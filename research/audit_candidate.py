"""
Audit kandidat terbaik: SL 2% / TP 4% / hold 2 jam.

Di backtest terakhir, konfigurasi ini positif di ketiga fold dengan
PF test 2.04. Itu terlihat sangat bagus, dan itulah yang membuatnya
PERLU diperiksa, bukan dipercaya.

Yang diperiksa:
  1. Signifikan statistik - t-stat di test fold. PF 2 dengan n=82
     mungkin noise.
  2. Stabilitas - berapa spread di sekitar konfigurasi ini? Kalau
     profit hanya ada di titik SL=0.02/TP=0.04 dan lenyap di
     SL=0.019 atau 0.021, itu spike, bukan edge.
  3. Asimetri arah - long saja? Kalau long menang dan short rugi
     2x lebih besar, itu drift lagi (lihat research/bear.py).
  4. Simbol - apakahprofitnya berasal dari 1-2 simbol ganjil, atau
     tersebar?
  5. CATATAN: horizon 2 jam, bukan scalp. Apakah ini masih
    _encode_ dari config yang bisa dieksekusi? `max_hold_seconds`
     ada, tapi apakah fee 2 jam di-deploy realistis?

Yang@-WAIT: kalau gagal salah satu, jangan dipakai.
"""
import math
import sqlite3
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

import bt
from bt import (load, split_by_position, Config, _simulate_exit)
from trading.fill_cost import (
    FILL_HALF_SPREAD_FLOOR_BY_SYMBOL, FILL_IMPACT_FLOOR,
)

TAKER = 0.00045
data = load()
folds = split_by_position(data, 3)


def run(d, sl, tp, hold, trend=False, mom=0.0, direction=1,
        symbols=None, collect=False):
    eq = 10_000.0
    net = 0.0
    n = w = 0
    ws = ls = 0.0
    per_sym = {}
    pnls = []
    for sym, s in d.items():
        if symbols and sym.split('/')[0] not in symbols:
            continue
        base = sym.split('/')[0]
        cost = (FILL_HALF_SPREAD_FLOOR_BY_SYMBOL.get(base, 0.0003)
                + FILL_IMPACT_FLOOR + TAKER)
        i = 100
        nb = len(s)
        while i < nb - 2:
            if trend and not s.trend_up[i]:
                i += 1
                continue
            m = s.mom21[i]
            if direction > 0:
                if m <= mom:
                    i += 1
                    continue
                d_ = 1
            else:
                if m >= -mom:
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
            if collect:
                pnls.append(p)
            per_sym[base] = per_sym.get(base, 0.0) + p
            if p > 0:
                w += 1
                ws += p
            else:
                ls += -p
            i = ei + 1
    pf = ws / ls if ls > 0 else float("inf")
    return n, net, pf, (w / n if n else 0.0), per_sym, pnls


BEST = dict(sl=0.020, tp=0.040, hold=7200)

print("=" * 78)
print("1. SIGNIFIKAN STATISTIK (test fold)")
print("=" * 78)
n, net, pf, wr, per_sym, pnls = run(folds[2], collect=True, **BEST)
m = statistics.mean(pnls)
sd = statistics.stdev(pnls) if len(pnls) > 1 else 0
t = m / (sd / math.sqrt(len(pnls))) if sd else 0
print(f"  n={n}  mean {m:+.4f}  sd {sd:.4f}  PF {pf:.3f}  wr {wr*100:.1f}%")
print(f"  t-stat = {t:+.2f}   {'SIGNIFIKAN' if t > 2 else 'TIDAK SIGNIFIKAN'}")
print(f"  95% CI mean: [{m - 1.96*sd/math.sqrt(n):+.4f}, "
      f"{m + 1.96*sd/math.sqrt(n):+.4f}]")
print()

print("=" * 78)
print("2. STABILITAS - apakah ini spike atau region?")
print("=" * 78)
print(f"  {'cfg':<28} {'pf_te':>8} {'per_tr':>10} {'n':>5}")
print("  " + "-" * 56)
for sl in (0.014, 0.017, 0.020, 0.023, 0.026):
    for tp in (0.028, 0.034, 0.040, 0.046, 0.052):
        if tp <= sl:
            continue
        r = run(folds[2], sl, tp, 7200)
        mark = ""
        if abs(sl - 0.020) < 1e-9 and abs(tp - 0.040) < 1e-9:
            mark = "  <-- kandidat"
        print(f"  sl{sl:.3f} tp{tp:.3f}{'':<10} {r[2]:>8.3f} "
              f"{r[1]/r[0] if r[0] else 0:>+10.4f} {r[0]:>5}{mark}")
print()

print("=" * 78)
print("3. ASIMETRI ARAH")
print("=" * 78)
rl = run(folds[2], direction=1, **BEST)
rs = run(folds[2], direction=-1, **BEST)
print(f"  LONG  n={rl[0]:>4}  net {rl[1]:>+9.2f}  PF {rl[2]:>6.3f}")
print(f"  SHORT n={rs[0]:>4}  net {rs[1]:>+9.2f}  PF {rs[2]:>6.3f}")
if rl[0] and rs[0]:
    ratio = (rl[1] / rl[0]) / (-rs[1] / rs[0]) if rs[1] else 0
    print(f"  long/short = {ratio:+.2f}x")
    if abs(ratio) > 1.5:
        print("  >> ASIMETRI. Ini drift lagi, bukan alpha.")
print()

print("=" * 78)
print("4. DISTRIBUSI PER SIMBOL (test fold)")
print("=" * 78)
print(f"  {'sym':<8} {'net':>10}")
for s, v in sorted(per_sym.items(), key=lambda kv: -kv[1]):
    print(f"  {s:<8} {v:>+10.2f}")
pos = sum(1 for v in per_sym.values() if v > 0)
print(f"\n  {pos}/{len(per_sym)} simbol positif")
if pos <= 2:
    print("  >> Terlalu sedikit. Edge kemungkinan berasal dari 1-2 simbol")
    print("     yang tidak akan berulang.")
print()

print("=" * 78)
print("5. KONSISTENSI KANONIK - apakah survives tanpa drift?")
print("=" * 78)
print("  Kalau edge ini drift, long-only akan menang di SEMUA fold karena")
print("  data bullish. Kalau alpha, performanya akan varied - dan itu")
print("  lebih sulit dijual daripada yang terlihat bagus.")
print()
for k, lb in enumerate(("TRAIN", "VALID", "TEST")):
    r = run(folds[k], **BEST)
    print(f"  {lb:<6} n={r[0]:>4}  net {r[1]:>+9.2f}  PF {r[2]:>6.3f}  "
          f"per_tr {r[1]/r[0] if r[0] else 0:>+8.4f}")
print()
print("  nb: hold 7200s = 2 JAM. Ini bukan scalp. config.yaml sekarang")
print("  max_hold 900. Pakai kandidat ini berarti mengubah horizon strategi")
print("  dari scalp ke swing - perubahan yang jauh lebih besar dari")
print("  sekadar mengatur parameter.")
