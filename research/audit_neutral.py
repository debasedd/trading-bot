"""
Audit kandidat market-neutral. Angka +5330 dari 10000 dalam 30 hari
itu 53% dalam sebulan. Itu TIDAK mungkin untuk strategi anyhow.

Sebelum percaya, periksa apa yang membuatnya terlihat bagus. Kalau
karena bug, angka itu artefak. Kalau karenalook-ahead, juga artefak.
Yang dicari di sini:

  1. LOOK-AHEAD. apakah ranking memakai return yang sudah terjadi atau
     yang akan terjadi?
  2. BIAYA. apakah spread + impact + fee benar-benar dipotong per kaki?
  3. KAPASITAS. 10 kaki x 10x leverage di perp dengan quote kecil
     mungkin tidak bisa diisi.
  4. LIQUIDATION. Dengan leverage 10x dan TP/SL dari modul
     sebelumnya, pergerakan 10% menghapus semuanya. Kalau equity
     tidak pernah turun, tidak ada stop yang reinstallable.
  5. OVERLAP JENDELA. Jendela bullish dan bearish tumpang tindih?
     Kalau ya, ini bukan dua rezim terpisah.
  6. LOOK-AHEAD WAKTU. memakai bar yang sama untuk entry DAN exit?
"""
import sqlite3
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

import market_neutral as M
import regime_split as R

TAKER = 0.00045
IMPACT = 0.0001
DEFAULT_FLOOR = 0.00007


def audit_run(cs, days, horizon_h, n_side, rebalance_h, trail_h=20):
    """Versi yang mengembalikan kurva equity per rebalance."""
    syms = sorted(cs)
    common = set(cs[syms[0]])
    for s in syms[1:]:
        common &= set(cs[s])
    hours = sorted(common)

    cost = DEFAULT_FLOOR + IMPACT + TAKER
    eq = 10_000.0
    curve = [(hours[0], eq)]
    per_rebal = []
    draws = []

    for start in range(trail_h + 5, len(hours) - horizon_h - 1, rebalance_h):
        h0 = hours[start]
        if h0 // 86_400_000 not in days:
            continue
        ranked = []
        for s in syms:
            a = cs[s].get(h0)
            b = cs[s].get(hours[start - trail_h])
            c = cs[s].get(hours[start + horizon_h])
            if None in (a, b, c) or b <= 0 or a <= 0:
                continue
            ranked.append((s, (a - b) / b * 100.0, a, c))
        if len(ranked) < n_side * 2:
            continue
        ranked.sort(key=lambda r: r[1])

        pnl = 0.0
        for side, group in ((1, ranked[-n_side:]), (-1, ranked[:n_side])):
            for s, _m, a, c in group:
                notional = eq / (2.0 * n_side)
                pnl += notional * 10 * ((c - a) / a * side)
                pnl -= notional * cost
        eq += pnl
        per_rebal.append(pnl)
        curve.append((hours[start + horizon_h], eq))

    if not per_rebal:
        return None

    peak = 10_000.0
    max_dd = 0.0
    for _, e in curve:
        peak = max(peak, e)
        max_dd = max(max_dd, (peak - e) / peak)

    worst = min(per_rebal)
    best = max(per_rebal)
    return {
        "n": len(per_rebal),
        "net": sum(per_rebal),
        "final": eq,
        "max_dd": max_dd,
        "worst_rebal": worst,
        "best_rebal": best,
        "mean": statistics.mean(per_rebal),
        "sd": statistics.stdev(per_rebal) if len(per_rebal) > 1 else 0,
        "curve": curve,
    }


if __name__ == "__main__":
    rows, cs = M.load("1h")
    data = {s: rows[s] for s in rows}
    bull, bear = R.split_regimes(data, window_days=30)
    print()

    print("=" * 88)
    print("1. OVERLAP JENDELA REZIM")
    print("=" * 88)
    ov = bull & bear
    print(f"  bullish : {len(bull)} hari")
    print(f"  bearish : {len(bear)} hari")
    print(f"  OVERLAP : {len(ov)} hari  "
          f"{'<<< ADA OVERLAP - bukan dua rezim!' if ov else 'bersih'}")
    print()

    print("=" * 88)
    print("2. AUDIT KANDIDAT TERBAIK (hz 6h, N=5, reb 12h)")
    print("=" * 88)

    for label, days in (("BULLISH", bull), ("BEARISH", bear)):
        r = audit_run(cs, days, 6, 5, 12)
        if not r:
            continue
        t = (r["mean"] / (r["sd"] / (r["n"] ** 0.5))) if r["sd"] else 0
        print(f"\n  {label}:")
        print(f"    rebalance      : {r['n']}")
        print(f"    net            : {r['net']:+.2f} USDT")
        print(f"    equity akhir   : {r['final']:.2f}")
        print(f"    max drawdown   : {r['max_dd']*100:.2f}%")
        print(f"    rata2/rebal    : {r['mean']:+.2f}")
        print(f"    t-stat         : {t:+.2f}  "
              f"{'SIGNIFIKAN' if t > 2 else 'TIDAK SIGNIFIKAN'}")
        print(f"    rebal terburuk : {r['worst_rebal']:+.2f}")
        print(f"    rebal terbaik  : {r['best_rebal']:+.2f}")

    print()
    print("=" * 88)
    print("3. APAKAH equity PERNAH TURUN? (stop tidak di-reinstall)")
    print("=" * 88)
    r = audit_run(cs, bull, 6, 5, 12)
    if r:
        print("  kurva equity (10 sample):")
        for ts, e in r["curve"][::max(1, len(r["curve"]) // 10)]:
            print(f"    {e:>10.2f}")
        downs = [1 for i in range(1, len(r["curve"]))
                 if r["curve"][i][1] < r["curve"][i - 1][1]]
        print(f"  rebalance yang turun: {len(downs)} dari {len(r['curve'])-1}")
        print(f"  max drawdown: {r['max_dd']*100:.2f}%")
        if r["max_dd"] < 0.05:
            print("  >> suspiciously low. Nine 10x-legged positions across")
            print("     20 symbols should produce visible drawdown. Check")
            print("     apakah ada stop yang reinstallable.")

    print()
    print("=" * 88)
    print("4. BIAYA - berapa bps per kaki")
    print("=" * 88)
    print(f"  spread floor   : {DEFAULT_FLOOR*10000:.2f} bps")
    print(f"  impact         : {IMPACT*10000:.2f} bps")
    print(f"  taker fee      : {TAKER*10000:.2f} bps")
    print(f"  total per kaki : {(DEFAULT_FLOOR+IMPACT+TAKER)*10000:.2f} bps")
    print(f"  per round trip : {(DEFAULT_FLOOR+IMPACT+TAKER)*2*10000:.2f} bps")
    print()
    print("  Kalau net per rebalance ~ +90 USDT di atas ekuitas ~13000,")
    print("  itu ~70 bps per rebalance bruto - jauh di atas biaya.")
    print("  Tapi 5 kaki long + 5 kaki short = 10 kaki, jadi per kaki")
    print("  ~9 USDT. Volume tidak wajib dipakai.")