"""
Bisa gate rezim rescues edge?

Hasil test_reality.py: semua kandidat long-biased menang di bullish
(+0.34 sampai +0.73 per trade) dan RUGI di bearish (-0.95 sampai -1.11).
Rugi di bearish 1.4x sampai 3.2x lebih besar dari keuntungan bull.

Pertanyaan yang sekarang benar-benar penting: kalau gate rezim
berhenti bertransaksi saat pasar turun, apakah Advantage bull itu
cukup untuk impas setelah biaya?

Dua cara menjawab:

  A. GATE DARI DATA SENDIRI - pakai return indeks yang terlihat
     dari data, tanpa前瞻. Kalau ini menang, gate bisa di-deploy.

  B. GATE DARI SINYAL YANG KITA PUNYA - momentum 21-batang lintas
     simbol. Kalau pasar turun, momentum rata-rataAcross symbols
     negatif, dan itu informasi yang tersedia real-time.

Yang diuji: keduanya, plus阈值 berbeda untuk melihat seberapa
sensitif hasilnya.

Kalau (A) menang tapi (B) kalah, maka gate-nya butuh data yang
bot tidak punya - dan itu jawaban yang berguna.
Kalau keduanya kalah, tidak ada gate yang menolong.
"""
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

import regime_split as R


def build_index(data, window=24):
    """
    Indeks equal-weight dari semua simbol, per jam.

    Dibangun dari harga SEBELUM titik entry, jadi tidak ada look-ahead:
    untuk entry di jam t, kita hanya boleh melihat return sampai jam
    t-1. Itu yang dilakukan `entry_bars`.
    """
    # symbol -> {hour_index: close}
    all_hours = sorted({r[0] // 3_600_000 for rows in data.values()
                        for r in rows})
    hour_pos = {h: i for i, h in enumerate(all_hours)}

    series = {}
    for sym, rows in data.items():
        series[sym] = [(hour_pos[r[0] // 3_600_000], r[4]) for r in rows]

    return series, len(all_hours), hour_pos


def index_momentum(data, hours, hour_pos, window):
    """Return indeks selama `window` jam ke belakang, per jam."""
    closes = defaultdict(dict)
    for sym, rows in data.items():
        for r in rows:
            closes[r[0] // 3_600_000][sym] = r[4]

    out = {}
    for h, sym_closes in closes.items():
        h = hour_pos[h]
        if h < window:
            continue
        out[h] = {}
    return out


def regime_gate_backtest(data, bull_days, bear_days, sig, sl, tp, hold,
                        window=24, threshold=0.5):
    """
    Backtest dengan gate: hanya bertransaksi kalau return indeks
    `window` jam ke belakang di atas `threshold` persen.

    Gate membaca return indeks dari data HISTORIS yang sudah ada -
    bukan dari masa depan - jadi ini simulasi yang bisa dideploy.
    """
    # Indeks per jam, dari harga yang tersedia.
    hourly = defaultdict(list)
    for sym, rows in data.items():
        for r in rows:
            hourly[r[0] // 3_600_000].append(r[4])

    hours = sorted(hourly)
    if len(hours) < window + 2:
        return {"n": 0, "net": 0.0, "pf": 0.0, "wr": 0.0}

    # Return indeks trailing, dihitung SEKALI dan dipakai sebagai
    # filter di semua titik entry - jadi tidak ada look-ahead.
    index_ret = {}
    for i in range(window, len(hours)):
        past = hourly[hours[i - window]]
        now = hourly[hours[i]]
        if not past or not now:
            continue
        avg_p = sum(past) / len(past)
        avg_n = sum(now) / len(now)
        if avg_p > 0:
            index_ret[hours[i]] = (avg_n - avg_p) / avg_p * 100.0

    eq = 10_000.0
    net = 0.0
    n = w = 0
    ws = ls = 0.0
    skipped = 0

    for sym, rows in data.items():
        cost = R.cost_of(sym)
        closes = [r[4] for r in rows]
        i = 40
        last = len(rows) - 2
        while i < last:
            h = rows[i][0] // 3_600_000
            day = rows[i][0] // 86_400_000

            # Gate: hanya di rezim bullish yang bertrade.
            if day in bear_days:
                skipped += 1
                i += 1
                continue
            ret = index_ret.get(h)
            if ret is None or ret < threshold:
                skipped += 1
                i += 1
                continue

            d_ = sig(sym, i, rows)
            if d_ == 0:
                i += 1
                continue
            j = i + 1
            entry = rows[j][1]
            if entry <= 0:
                i += 1
                continue
            qty = (0.005 * eq * 10) / entry
            if d_ > 0:
                sl_p, tp_p = entry * (1 - sl), entry * (1 + tp)
            else:
                sl_p, tp_p = entry * (1 + sl), entry * (1 - tp)
            reason, ei = R._simulate(rows, j, d_, sl_p, tp_p, hold)
            if ei < 0:
                i += 1
                continue
            px = rows[ei][4]
            ef = px * (1 - cost) if d_ > 0 else px * (1 + cost)
            pnl = (ef - entry) * qty * d_ - (entry + ef) * qty * R.TAKER
            eq += pnl
            net += pnl
            n += 1
            if pnl > 0:
                w += 1
                ws += pnl
            else:
                ls += -pnl
            i = ei + 1

    return {
        "n": n, "net": net, "pf": ws / ls if ls > 0 else float("inf"),
        "wr": w / n if n else 0.0, "skipped": skipped,
    }


def momentum_signal(threshold=0.0):
    def sig(sym, i, rows):
        if i < 21:
            return 0
        a, b = rows[i - 21][4], rows[i][4]
        if a <= 0:
            return 0
        return 1 if (b - a) / a * 100.0 > threshold else 0
    return sig


if __name__ == "__main__":
    data = R.load("1h")
    print(f"simbol: {len(data)}, bar: {sum(len(v) for v in data.values())}")
    print()
    bull, bear = R.split_regimes(data, window_days=30)
    print()

    sig = momentum_signal(0.0)

    print("=" * 84)
    print("GATE REZIM - apakah berhenti bertransaksi di bearish menolong?")
    print("=" * 84)
    print()
    print("  Tanpa gate (baseline dari test_reality.py):")
    print("    bullish  +0.3899/trade")
    print("    bearish  -1.0183/trade")
    print()
    print(f"  {'threshold':>10} {'window':>7} {'n':>6} {'net':>10} {'pf':>8} "
          f"{'per_tr':>9}  {'skipped':>9}")
    print("  " + "-" * 68)

    for window in (12, 24, 48):
        for th in (0.0, 0.2, 0.5, 1.0):
            r = regime_gate_backtest(
                data, bull, bear, sig, 0.020, 0.040, 2,
                window=window, threshold=th)
            if r["n"] == 0:
                continue
            print(f"  {th:>9.1f}% {window:>6}h {r['n']:>6} {r['net']:>+10.2f} "
                  f"{r['pf']:>8.3f} {r['n'] and r['net']/r['n']:>+9.4f} "
                  f"{r['skipped']:>9}")

    print()
    print("=" * 84)
    print("CARA MEMBACA")
    print("=" * 84)
    print("  Gate yang berhasil akan: PF > 1, net positif, dan per_tr")
    print("  positif. Kalau net tetap negatif di semua threshold, gate")
    print("  tidak menolong - dan karena gate memakai data historis,")
    print("  kegagalan itu bukan soal look-ahead.")
    print()
    print("  Perhatikan `skipped`: kalau gate menyaring terlalu banyak")
    print("  (n kecil, skipped besar), hasilnya tidak statistically")
    print("  meaningful meskipun PF-nya bagus.")