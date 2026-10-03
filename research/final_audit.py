"""
Verifikasi akhir edge market-neutral horizon panjang.

Yang深 di deep_neutral dan horizon_scan: cross-sectional momentum
(5 long terkuat + 5 short terlemah, simultaneous) memberi net positif
pada horizon 12-72 jam, di kedua rezim, tanpa leverage.

  hz    FULL 208d   bullish    bearish
  12h   +1758 t0.89   +81 t0.12   +2020 t1.98
  18h   +2774 t1.15   +364 t0.43   +2914 t2.22
  48h   +5111 t1.18  +1493 t0.94   +2476 t1.05
  72h   +6588 t1.22  +3863 t1.92   +2787 t0.96

Tiap klaim di sini masih bisa salah. Empat pemeriksaan:

  1. KONTROL ACAK. Kalau long/short acak menghasilkan angka yang
     sebanding, tidak ada edge - hanya struktur portfolio.
  2. REVERSE. Long TERLEMAH, short TERKUAT. Kalau edge-nya asli,
     arahnya tidak boleh bisa dibalik.
  3. SUB-PERIODE. Bagi 208 hari jadi 4 bagian sama. Kalau edge
     Consistent, keempatnya positif atau setidaknya tidak satu
     pun sangat negatif.
  4. TURN-ON. Tanpa biaya, hasilnya berapa? Kalau gross-nya besar sekali dan biaya yang hanya semua, edge-nya cuma soal spread.
"""
import math
import random
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


def fold(cs, syms, hours, start, hz, n_side, reverse=False,
         random_pick=False, cost=COST, trail=20):
    if start + hz >= len(hours) or start < trail + 5:
        return None
    h0 = hours[start]
    avail = []
    for s in syms:
        a = cs[s].get(h0)
        b = cs[s].get(hours[start - trail])
        c = cs[s].get(hours[start + hz])
        if None in (a, b, c) or b <= 0 or a <= 0:
            continue
        avail.append((s, (a - b) / b * 100.0, a, c))
    if len(avail) < n_side * 2:
        return None

    if random_pick:
        rnd = random.Random(start)
        rnd.shuffle(avail)
        longs, shorts = avail[:n_side], avail[-n_side:]
    else:
        avail.sort(key=lambda r: r[1])
        if reverse:
            longs, shorts = avail[:n_side], avail[-n_side:]
        else:
            longs, shorts = avail[-n_side:], avail[:n_side]

    pnl = 0.0
    for side, group in ((1, longs), (-1, shorts)):
        for s, _m, a, c in group:
            notional = 10_000.0 / (2.0 * n_side)
            pnl += notional * ((c - a) / a * side)
            pnl -= notional * cost
    return pnl


def sweep(cs, hz, n_side=5, reb=24, **kw):
    syms, hours = build(cs)
    out = []
    for start in range(25, len(hours) - hz - 1, reb):
        p = fold(cs, syms, hours, start, hz, n_side, **kw)
        if p is not None:
            out.append((hours[start], p))
    return out


def st(vals):
    n = len(vals)
    if n < 3:
        return 0.0
    m = statistics.mean(vals)
    sd = statistics.stdev(vals)
    return m / (sd / n ** 0.5) if sd else 0.0


def show(vals, label):
    cum = 10_000.0
    for v in vals:
        cum += v
    print(f"    {label:<26} n={len(vals):>4}  net {sum(vals):>+9.2f}  "
          f"t {st(vals):>+5.2f}  comp {cum:>8.0f}")


if __name__ == "__main__":
    rows, cs = M.load("1h")
    data = {s: rows[s] for s in rows}
    syms, hours = build(cs)
    print(f"simbol {len(syms)}, jam {len(hours)} "
          f"({(hours[-1]-hours[0])/86400000:.0f} hari)\n")

    HZ, NS, RB = 72, 5, 24

    print("=" * 88)
    print(f"1. KONTROL ACAK (horizon {HZ}h, rebal {RB}h)")
    print("=" * 88)
    normal = [p for _, p in sweep(cs, HZ, NS, RB)]
    random_ = [p for _, p in sweep(cs, HZ, NS, RB, random_pick=True)]
    show(normal, "momentum (asli)")
    show(random_, "long/short ACAK")
    print()

    print("=" * 88)
    print("2. ARAH DIBALIK")
    print("=" * 88)
    rev = [p for _, p in sweep(cs, HZ, NS, RB, reverse=True)]
    show(normal, "long TERKUAT (asli)")
    show(rev, "long TERLEMAH (dibalik)")
    print()
    if sum(rev) > 0 and sum(normal) > 0:
        print("  >> KEDUA arah positif. Edge bukan soal arah momentum.")
        print("     Kemungkinan besar ini mean-reversion, bukan momentum.")
    print()

    print("=" * 88)
    print("3. SUB-PERIODE - 208 hari dibagi 4")
    print("=" * 88)
    full = sweep(cs, HZ, NS, RB)
    n = len(full)
    quart = n // 4
    for i in range(4):
        chunk = full[i * quart:(i + 1) * quart if i < 3 else n]
        show([p for _, p in chunk], f"periode ke-{i+1}")
    print()

    print("=" * 88)
    print("4. TURN-ON - berapa edge sebelum biaya?")
    print("=" * 88)
    free = [p for _, p in sweep(cs, HZ, NS, RB, cost=0.0)]
    show(free, "tanpa biaya")
    show(normal, "dengan biaya 6.2bps")
    print()
    if free and sum(free):
        eaten = sum(free) - sum(normal)
        print(f"  biaya memakan {eaten:.2f} dari gross {sum(free):.2f} "
              f"({eaten/sum(free)*100:.1f}%)")
