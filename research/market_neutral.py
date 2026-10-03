"""
Market-neutral cross-sectional.

Semua 6 kandidat sebelumnya-directional: long kalau bullish, short
kalau bearish. Itu sebabnya semuanya mati di rezim bearish - bukan
karena sinyalnya salah, tapi karena strategi menanggung arah pasar
sementara sinyal hanya membaca arah itu.

MARKET-NEUTRAL mengubah itu secara KONSTRUKSI. Long N simbol dengan
momentum terkuat, short N terlemah, di saat yang sama. Net dollar
nol sebelum biaya, jadi tidak ada rezim yang bisa membunuhnya -
rezim bergerak semua simbol bersama, jadi long dan short saling
meniadakan.

Yang di-rank adalah return SUDAH TERJADI (trailing 20 jam), bukan
return ke depan. Kalau yang di-rank adalah return ke depan, ini
bukan strategi - itu menatap masa depan.

Biaya: spread terukur + impact + fee taker, per kaki.
"""
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

import regime_split as R

TAKER = 0.00045
IMPACT = 0.0001
DEFAULT_FLOOR = 0.00007
LEVERAGE = 10


def load(index_interval="1h"):
    """
    Dua bentuk dari data yang sama:

      rows[sym] = [(ts, open, high, low, close), ...]  - bentuk yang
        dipakai regime_split, jadi split_regimes bisa dipakai langsung
      cs[sym]    = {ts: close}                        - bentuk cepat untuk
        cross-sectional, supaya tidak bolak-balik list tiap simbol

    Kolom close ada di indeks 4 pada bentuk pertama; itu yang
    regime_split baca.
    """
    p = Path("data_store/historical_candles.db")
    if not p.exists():
        return {}, {}
    conn = sqlite3.connect(p)
    raw = defaultdict(list)
    for sym, ts, o, h, l, c in conn.execute(
        "SELECT symbol, ts, open, high, low, close FROM hist_candles "
        "WHERE interval=? ORDER BY symbol, ts", (index_interval,)
    ):
        raw[sym].append((ts, o, h, l, c))
    conn.close()
    rows = {s: v for s, v in raw.items()}
    cs = {s: {r[0]: r[4] for r in v} for s, v in raw.items()}
    return rows, cs


def run(cs, data, days, horizon_h, n_side, rebalance_h, trail_h=20):
    """
    Satu set Nested: return net, PF, winrate per rebalance.
    """
    # Jam yang ada di SEMUA simbol - tanpa itu, short bisa referencing
    # simbol yang datanya tidak ada dan net neutrality jadi bohong.
    syms = sorted(cs)
    if len(syms) < n_side * 2 + 2:
        return None
    common = set(cs[syms[0]])
    for s in syms[1:]:
        common &= set(cs[s])
    hours = sorted(common)
    if len(hours) < horizon_h + trail_h + 60:
        return None

    cost = DEFAULT_FLOOR + IMPACT + TAKER

    eq = 10_000.0
    net = 0.0
    rebal = 0
    legs = 0
    wins = 0
    win_sum = 0.0
    loss_sum = 0.0

    for start in range(trail_h + 5, len(hours) - horizon_h - 1,
                       rebalance_h):
        h0 = hours[start]
        if h0 // 86_400_000 not in days:
            continue

        # Rank momentum trailing
        ranked = []
        for s in syms:
            px_then = cs[s].get(h0)
            px_past = cs[s].get(hours[start - trail_h])
            if px_then is None or px_past is None or px_past <= 0:
                continue
            ranked.append((s, (px_then - px_past) / px_past * 100.0,
                           px_then))
        if len(ranked) < n_side * 2:
            continue
        ranked.sort(key=lambda r: r[1])

        groups = ((1, ranked[-n_side:]), (-1, ranked[:n_side]))
        h1 = hours[start + horizon_h]

        pnl = 0.0
        used = 0
        for side, group in groups:
            for s, _mom, px in group:
                px_exit = cs[s].get(h1)
                if px_exit is None or px <= 0:
                    continue
                notional = eq / (2.0 * n_side)
                ret = (px_exit - px) / px * side
                pnl += notional * LEVERAGE * ret
                pnl -= notional * cost
                used += 1

        if used < n_side * 2:
            continue

        eq += pnl
        net += pnl
        rebal += 1
        legs += used
        if pnl > 0:
            wins += 1
            win_sum += pnl
        else:
            loss_sum += -pnl

    if rebal == 0:
        return None
    return {
        "rebal": rebal,
        "legs": legs // 2,
        "net": net,
        "per_rebal": net / rebal,
        "per_leg": net / (legs // 2) if legs else 0.0,
        "pf": win_sum / loss_sum if loss_sum > 0 else float("inf"),
        "wr": wins / rebal,
        "final_equity": eq,
    }


if __name__ == "__main__":
    rows, cs = load("1h")
    data = {s: rows[s] for s in rows}
    print(f"simbol: {len(data)}, bar: {sum(len(v) for v in data.values())}")
    print()

    bull, bear = R.split_regimes(data, window_days=30)
    print()

    PLANS = [
        (1, 5, 12), (1, 8, 12), (1, 10, 12),
        (2, 5, 12), (2, 8, 12),
        (3, 5, 12), (3, 8, 12), (3, 10, 12),
        (6, 5, 12), (6, 8, 12),
        (12, 5, 24), (12, 8, 24),
        (3, 5, 6), (3, 5, 24), (6, 5, 24),
    ]

    print("=" * 92)
    print("MARKET-NEUTRAL CROSS-SECTIONAL")
    print("=" * 92)
    print("  long N momentum terkuat + short N terlemah, simultaneous")
    print()
    print(f"  {'hz':>3} {'N':>3} {'reb':>4} | "
          f"{'BULL/reb':>11} {'pf':>7} {'wr':>6} | "
          f"{'BEAR/reb':>11} {'pf':>7} {'wr':>6}  verdict")
    print("  " + "-" * 90)

    wins = []
    for hz, n_side, reb in PLANS:
        b = run(cs, data, bull, hz, n_side, reb)
        s_ = run(cs, data, bear, hz, n_side, reb)
        if not b or not s_:
            continue
        ok = b["per_rebal"] > 0 and s_["per_rebal"] > 0
        if ok:
            wins.append((hz, n_side, reb, b, s_))
        verdict = ("LOLOS" if ok else
                   ("drift" if b["per_rebal"] > 0 else "rugi"))
        print(f"  {hz:>3} {n_side:>3} {reb:>4} | "
              f"{b['per_rebal']:>+11.3f} {b['pf']:>7.3f} {b['wr']*100:>5.1f}% | "
              f"{s_['per_rebal']:>+11.3f} {s_['pf']:>7.3f} {s_['wr']*100:>5.1f}%"
              f"  {verdict}")

    print()
    if wins:
        print("=" * 92)
        print(f"{len(wins)} KONFIGURASI LOLOS DI KEDUA REZIM")
        print("=" * 92)
        for hz, n_side, reb, b, s_ in wins:
            print(f"\n  horizon {hz}h, {n_side} per sisi, rebal {reb}h")
            print(f"    bullish: net {b['net']:+.2f} USDT / {b['rebal']} rebal "
                  f"({b['legs']} kaki), pf {b['pf']:.3f}, wr {b['wr']*100:.1f}%")
            print(f"    bearish: net {s_['net']:+.2f} USDT / {s_['rebal']} rebal "
                  f"({s_['legs']} kaki), pf {s_['pf']:.3f}, wr {s_['wr']*100:.1f}%")
            print(f"    kompound over the window:")
            print(f"      bullish {(10000*(1+b['per_rebal']/10000)**b['rebal']):>10.2f} "
                  f"(from 10000)")
            print(f"      bearish {(10000*(1+s_['per_rebal']/10000)**s_['rebal']):>10.2f}")
    else:
        print("  Tidak ada konfigurasi yang lolos kedua rezim.")