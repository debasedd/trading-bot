"""
Tes mirror TERAKHIR untuk kandidat SL 2% / TP 4% / hold 2 jam.

Kandidat ini lolos semua pemeriksaan yang biasa:
  * t-stat +2.25 (signifikan)
  * region stabil - 25 konfigurasi tetangga semua PF > 1.45
  * 10 dari 13 simbol positif (tersebar, bukan 1-2 ganjil)
  * positif di ketiga fold

Tapi asimetri arahnya 0.56x - short rugi 1.8x lebih besar dari
long untung. Pola itu PERSIS sama dengan yang kill semua kandidat
sebelumnya (research/bear.py).

Yang membedakan alpha dari drift: MIRROR TEST. Balik semua candle
(harga jadi 1/harga) sehingga bullish jadi bearish, lalu jalankan
strategi yang sama.

  * Kalau MASIH untung di data yang dibalik -> ada alpha. Strategi
    memprediksi arah, dan itu bekerja dua arah.
  * Kalau RUGI di data yang dibalik -> itu drift. Edge-nya cuma
    "long di pasar naik", dan pasar naik tidak bisa dijamin.

Empat kandidat sebelumnya lolos t-test dan region check, lalu mati di
tes ini. Jadi tes ini DULUAN, bukan pelengkap.
"""
import math
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

import bt
from bt import (load, split_by_position, Config, _slice, _simulate_exit,
                ema_series)
from trading.fill_cost import (
    FILL_HALF_SPREAD_FLOOR_BY_SYMBOL, FILL_IMPACT_FLOOR,
)

TAKER = 0.00045
BEST = dict(sl=0.020, tp=0.040, hold=7200)

data = load()
folds = split_by_position(data, 3)


def mirror(d):
    """Balik candle: bullish jadi bearish."""
    out = {}
    for sym, s in d.items():
        ns = _slice(s, 0, len(s))
        ns.o = [1.0 / x if x > 0 else 0.0 for x in s.o]
        ns.h = [1.0 / x if x > 0 else 0.0 for x in s.l]
        ns.l = [1.0 / x if x > 0 else 0.0 for x in s.h]
        ns.c = [1.0 / x if x > 0 else 0.0 for x in s.c]
        ns.v = list(s.v)
        ns.rsi14 = [None if r is None else 100.0 - r for r in s.rsi14]
        ns.atr14 = list(s.atr14)
        e9, e21 = ema_series(ns.c, 9), ema_series(ns.c, 21)
        ns.ema9, ns.ema21 = e9, e21
        n = len(ns.c)
        ns.mom21 = [0.0] * n
        for i in range(21, n):
            if ns.c[i - 21] > 0:
                ns.mom21[i] = (ns.c[i] - ns.c[i - 21]) / ns.c[i - 21] * 100.0
        ns.trend_up = [1 if (ns.c[i] > e21[i] and ns.c[i] > 0) else 0
                       for i in range(n)]
        out[sym] = ns
    return out


def run(d, sl, tp, hold, direction=1):
    eq = 10_000.0
    net = 0.0
    n = w = 0
    ws = ls = 0.0
    for sym, s in d.items():
        base = sym.split('/')[0]
        cost = (FILL_HALF_SPREAD_FLOOR_BY_SYMBOL.get(base, 0.0003)
                + FILL_IMPACT_FLOOR + TAKER)
        i = 100
        nb = len(s)
        while i < nb - 2:
            m = s.mom21[i]
            if direction > 0:
                if m <= 0:
                    i += 1
                    continue
                d_ = 1
            else:
                if m >= 0:
                    i += 1
                    continue
                d_ = -1
            j = i + 1
            e = s.o[j]
            if e <= 0:
                i += 1
                continue
            q = (0.005 * eq * 10) / e
            if d_ > 0:
                slp, tpp = e * (1 - sl), e * (1 + tp)
            else:
                slp, tpp = e * (1 + sl), e * (1 - tp)
            r, ei = _simulate_exit(
                s, j, d_, slp, tpp,
                Config(sl_pct=sl, tp_pct=tp, min_profit_pct=tp,
                       max_hold_s=hold, min_hold_s=15))
            if ei < 0:
                i += 1
                continue
            px = s.c[ei]
            ef = px * (1 - cost) if d_ > 0 else px * (1 + cost)
            p = (ef - e) * q * d_ - (e + ef) * q * TAKER
            eq += p
            net += p
            n += 1
            if p > 0:
                w += 1
                ws += p
            else:
                ls += -p
            i = ei + 1
    pf = ws / ls if ls > 0 else float("inf")
    return n, net, pf, (w / n if n else 0.0)


mir = [mirror(folds[k]) for k in range(3)]

print("=" * 78)
print("MIRROR TEST - kandidat SL 2% / TP 4% / hold 2 jam")
print("=" * 78)
print()
print(f"  {'fold':<7} {'asli':>28} {'mirror':>28}")
print(f"  {'':<7} {'n':>4} {'per_tr':>11} {'pf':>7}   "
      f"{'n':>4} {'per_tr':>11} {'pf':>7}")
print("  " + "-" * 72)
for k, lb in enumerate(("TRAIN", "VALID", "TEST")):
    a = run(folds[k], direction=1, **BEST)
    m = run(mir[k], direction=1, **BEST)
    ap = a[1] / a[0] if a[0] else 0
    mp = m[1] / m[0] if m[0] else 0
    print(f"  {lb:<7} {a[0]:>4} {ap:>+11.4f} {a[2]:>7.3f}   "
          f"{m[0]:>4} {mp:>+11.4f} {m[2]:>7.3f}")
print()

print("=" * 78)
print("INTERPRETASI")
print("=" * 78)
mirrors = [run(mir[k], direction=1, **BEST) for k in range(3)]
mirrors_pos = sum(1 for r in mirrors if r[1] > 0)
originals_pos = sum(1 for k in range(3)
                     if run(folds[k], direction=1, **BEST)[1] > 0)

print(f"  asli   positif di {originals_pos}/3 fold")
print(f"  mirror positif di {mirrors_pos}/3 fold")
print()

if mirrors_pos == 3:
    print("  >> ALPHA. Strategi tetap untung di data yang dibalik, jadi")
    print("     ini memprediksi arah - bukan hanya long di pasar naik.")
    print("     Ini yang tidak pernah terjadi di empat kandidat sebelumnya.")
elif mirrors_pos >= 1:
    print("  >> SEBAGIAN. Profit di sebagian fold mirror, tapi tidak")
    print("     semuanya. Mungkin ada komponen alpha di atas drift, tapi")
    print("     besarnya belum bisa dipisahkan dengan data ini.")
else:
    print("  >> DRIFT. Tidak untung di data yang dibalik sama sekali.")
    print("     Edge yang terlihat adalah long di pasar naik, dan")
    print("     ini akan hilang begitu pasar turun.")
print()
print("  Data bullish: 19 dari 20 simbol naik di periode ini.")
print("  Mirror test adalah satu-satunya cara memisahkan alpha dari")
print("  artefak itu, dan satu-satunya alasan empat kandidat")
print("  sebelumnya bisa ditolak dengan bukti.")
