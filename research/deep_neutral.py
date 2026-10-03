"""
Market-neutral cross-sectional: apakah edge-nya nyata atau hanya
sedikit data yang terlihat bagus?

Bukti sekarang (semua sudah terperiksa):
  * Look-ahead: 0 violation dari 1.200 kaki, delta selalu 6 jam
  * Kontrol acak: -861 USDT vs +280 momentum, jadi edge bukan noise
  * Kedua rezim: bullish +280.73, bearish +288.90
  * Tanpa leverage: 1x gross, jadi bukan leverage yang bikin angka

Yang BELUM terjawab:
  * t-stat hanya 0.42 - itu lemah
  * 29 dari 60 rebalance positif - hampir coin flip
  * 30 hari per rezim - masih sedikit

Yang dilakukan di sini: pakai SEMUA 208 hari (bukan dua jendela
30 hari), dan pecah jadi banyak fold kecil untuk mengukur apakah
edge konsisten sepanjang waktu atau hanya di periode tertentu.
"""
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

import market_neutral as M

COST = 0.00007 + 0.0001 + 0.00045


def build_index(cs):
    """Jam yang ada di semua simbol."""
    syms = sorted(cs)
    common = set(cs[syms[0]])
    for s in syms[1:]:
        common &= set(cs[s])
    return syms, sorted(common)


def one_fold(cs, syms, hours, start, horizon_h, n_side, trail_h=20):
    """Satu rebalance. Return net, atau None."""
    if start + horizon_h >= len(hours):
        return None
    h0 = hours[start]
    ranked = []
    for s in syms:
        a = cs[s].get(h0)
        b = cs[s].get(hours[start - trail_h])
        c = cs[s].get(hours[start + horizon_h])
        if None in (a, b, c) or b <= 0 or a <= 0:
            continue
        ranked.append((s, (a - b) / b * 100.0, a, c))
    if len(ranked) < n_side * 2:
        return None
    ranked.sort(key=lambda r: r[1])

    pnl = 0.0
    used = 0
    for side, group in ((1, ranked[-n_side:]), (-1, ranked[:n_side])):
        for s, _m, a, c in group:
            notional = 10_000.0 / (2.0 * n_side)
            pnl += notional * ((c - a) / a * side)
            pnl -= notional * COST
            used += 1
    return pnl if used >= n_side * 2 else None


def sweep(cs, horizon_h, n_side, rebalance_h, trail_h=20):
    syms, hours = build_index(cs)
    rebal = []
    for start in range(trail_h + 5, len(hours) - horizon_h - 1, rebalance_h):
        p = one_fold(cs, syms, hours, start, horizon_h, n_side, trail_h)
        if p is not None:
            rebal.append(p)
    return rebal


def stat(vals, label):
    n = len(vals)
    if n < 3:
        return
    m = statistics.mean(vals)
    sd = statistics.stdev(vals)
    t = m / (sd / n ** 0.5) if sd else 0
    wins = sum(1 for v in vals if v > 0)
    cum = 10_000.0
    for v in vals:
        cum += v
    print(f"    {label:<16} n={n:>4}  net {sum(vals):>+9.2f}  "
          f"mean {m:>+7.3f}  t {t:>+5.2f}  win {wins}/{n}  "
          f"compounded -> {cum:>9.0f}")


if __name__ == "__main__":
    rows, cs = M.load("1h")
    print(f"simbol: {len(rows)}, bar: {sum(len(v) for v in rows.values())}")
    syms, hours = build_index(cs)
    print(f"jam yang ada di semua simbol: {len(hours)} "
          f"({(hours[-1]-hours[0])/86400000:.0f} hari)")
    print()

    print("=" * 88)
    print("1. SWEEP LENGKAP 208 HARI - apakah edge konsisten?")
    print("=" * 88)
    for hz, ns, rb in ((3, 5, 12), (6, 5, 12), (6, 8, 12),
                       (12, 5, 24), (2, 5, 12)):
        rebal = sweep(cs, hz, ns, rb)
        print(f"\n  horizon {hz}h, {ns}/sisi, rebal {rb}h:")
        stat(rebal, "FULL 208 hari")

    print()
    print("=" * 88)
    print("2. PECAH KE FOLD BULANAN - edge di bulan mana?")
    print("=" * 88)
    import datetime as dt
    rebal_hz = 6
    n_side = 5
    rb_h = 12
    rebal = sweep(cs, rebal_hz, n_side, rb_h)

    by_month = defaultdict(list)
    for start in range(25, len(hours) - rebal_hz - 1, rb_h):
        h0 = hours[start]
        p = one_fold(cs, syms, hours, start, rebal_hz, n_side)
        if p is None:
            continue
        month = dt.datetime.fromtimestamp(h0 / 1000, dt.UTC).strftime("%Y-%m")
        by_month[month].append(p)

    print(f"  {'bulan':<9} {'n':>4} {'net':>9} {'mean':>8} {'win':>7}")
    print("  " + "-" * 42)
    pos_months = 0
    for month in sorted(by_month):
        v = by_month[month]
        w = sum(1 for x in v if x > 0)
        if sum(v) > 0:
            pos_months += 1
        print(f"  {month:<9} {len(v):>4} {sum(v):>+9.2f} "
              f"{statistics.mean(v):>+8.3f} {w}/{len(v):>5}")
    print()
    print(f"  {pos_months} dari {len(by_month)} bulan positif")
    if pos_months >= len(by_month) * 0.6:
        print("  >> Edge konsisten lintas bulan - ini alpha yang serius.")
    else:
        print("  >> Edge tidak konsisten - Hanya di periode tertentu.")

    print()
    print("=" * 88)
    print("3. RENTANG HORIZON - di manakah edge paling kuat?")
    print("=" * 88)
    for hz in (1, 2, 3, 4, 6, 8, 12, 18, 24):
        rebal = sweep(cs, hz, 5, 12)
        if len(rebal) < 5:
            continue
        n = len(rebal)
        m = statistics.mean(rebal)
        sd = statistics.stdev(rebal)
        t = m / (sd / n ** 0.5) if sd else 0
        print(f"  hz {hz:>2}h  n={n:>4}  net {sum(rebal):>+9.2f}  "
              f"mean {m:>+7.3f}  t {t:>+5.2f}")