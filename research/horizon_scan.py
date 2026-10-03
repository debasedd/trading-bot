"""
Kenapa horizon pendek rugi, dan apakah horizon panjang benar-benar edge?

Pola di deep_neutral sangat monoton:

  hz   1h   t -3.83
  hz   2h   t -3.32
  hz   3h   t -2.36
  hz   4h   t -1.28
  hz   6h   t -0.61
  hz   8h   t +0.04
  hz  12h   t +0.89
  hz  18h   t +1.15
  hz  24h   t +1.29

Horizon pendek rugi significant. Ini bukan noise - itu karakter
dari cross-sectional momentum pada crypto 1 jam, dan ada penjelasan
yang masuk akal: pada horizon pendek, biaya 6.2 bps per kaki adalah
porsi besar dari edge, dan spread crypto 1 jam pada altcoin besar
sudah memakan hampir seluruh pergerakan 1-3 jam.

Yang harus diuji sekarang:

  1. Apakah ini benar-benar edge, atau hanya "biaya terlalu besar
     untuk horizon pendek"? Kalau ya, Horizon panjang seharusnya
     punya t yang TINGGI dan positif.
  2. Di horizon panjang, apakah edge ada di kedua rezim?
  3. Apakah rebalance terlalu sering? Mungkin 24h hold + 24h
     rebalance = turnover yang tidak perlu.
"""
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

import market_neutral as M
import regime_split as R

COST = 0.00007 + 0.0001 + 0.00045


def build(cs):
    syms = sorted(cs)
    common = set(cs[syms[0]])
    for s in syms[1:]:
        common &= set(cs[s])
    return syms, sorted(common)


def fold_pnl(cs, syms, hours, start, hz, n_side, trail=20):
    if start + hz >= len(hours):
        return None
    h0 = hours[start]
    ranked = []
    for s in syms:
        a = cs[s].get(h0)
        b = cs[s].get(hours[start - trail])
        c = cs[s].get(hours[start + hz])
        if None in (a, b, c) or b <= 0 or a <= 0:
            continue
        ranked.append((s, (a - b) / b * 100.0, a, c))
    if len(ranked) < n_side * 2:
        return None
    ranked.sort(key=lambda r: r[1])
    pnl = 0.0
    for side, group in ((1, ranked[-n_side:]), (-1, ranked[:n_side])):
        for s, _m, a, c in group:
            notional = 10_000.0 / (2.0 * n_side)
            pnl += notional * ((c - a) / a * side)
            pnl -= notional * COST
    return pnl


def sweep(cs, hz, n_side, reb, trail=20):
    syms, hours = build(cs)
    out = []
    for start in range(trail + 5, len(hours) - hz - 1, reb):
        p = fold_pnl(cs, syms, hours, start, hz, n_side, trail)
        if p is not None:
            out.append((hours[start], p))
    return out


def report(pairs, label):
    vals = [p if isinstance(p, float) else p[1] for p in pairs]
    if len(vals) < 3:
        return 0.0, 10_000.0
    n = len(vals)
    m = statistics.mean(vals)
    sd = statistics.stdev(vals)
    t = m / (sd / n ** 0.5) if sd else 0
    cum = 10_000.0
    for v in vals:
        cum += v
    print(f"    {label:<22} n={n:>4}  net {sum(vals):>+9.2f}  "
          f"t {t:>+5.2f}  comp {cum:>8.0f}")
    return t, cum


if __name__ == "__main__":
    rows, cs = M.load("1h")
    data = {s: rows[s] for s in rows}
    bull, bear = R.split_regimes(data, window_days=30)
    syms, hours = build(cs)
    print()

    print("=" * 90)
    print("1. HORIZON PANJANG DI KEDUA REZIM")
    print("=" * 90)
    print("  rebal 12h, 5 per sisi, leverage 1x")
    print()
    for hz in (12, 18, 24, 36, 48, 72):
        full = sweep(cs, hz, 5, 12)
        b = [p for h, p in full if h // 86_400_000 in bull]
        s_ = [p for h, p in full if h // 86_400_000 in bear]
        print(f"  horizon {hz:>2}h")
        report([p for _, p in full], "FULL 208 hari")
        report(b, "rezim bullish")
        report(s_, "rezim bearish")
        print()

    print("=" * 90)
    print("2. REBALANCE FREKUENSI - mungkin terlalu sering")
    print("=" * 90)
    print("  Pada horizon panjang, rebal cepat berarti turnover sia-sia.")
    print("  Biaya 6.2 bps per kaki jadi mahal kalau sering rebalance.")
    print()
    for hz, reb in ((24, 24), (24, 48), (48, 48), (48, 72), (72, 72), (72, 96)):
        full = sweep(cs, hz, 5, reb)
        t, cum = report([p for _, p in full], f"hz{hz} reb{reb}")
    print()

    print("=" * 90)
    print("3. UKURAN SISI")
    print("=" * 90)
    for ns in (3, 5, 8, 10):
        full = sweep(cs, 24, ns, 24)
        report([p for _, p in full], f"{ns} per sisi, hz24 reb24")
