"""
Long-only, tapi dengan filter yang benar-benar ketat.

Hasil sebelumnya: long+tren PF 0.68, masih rugi. Dua kemungkinan:

  A. Edge long tidak ada, dan 0.68 itu hanya sisa drift bullish.
  B. Edge long ADA tapi terlalu tipis untuk membayar 17 bps dengan
     entry yang terlalu sering.

Cara membedakan: kalau (B), maka filter yang lebih ketat harus
MENINGKATKAN PF per trade - sedikitnya tidak turun. Kalau (A),
PF akan terus turun seiring entry makin jarang, karena yang tersisa
cuma trade yang dipilih acak.

Jadi: sweep selectivity, dan lihat POLA. Kalau PF naik lalu
turun -> ada puncak, itu indikasi (B). Kalau PF monoton turun ->
cuma noise, itu (A).
"""
import sys
import math
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import bt
from bt import (load, backtest, split_by_position, Config, Series,
                COST_PER_SIDE, TAKER_FEE, _simulate_exit)


def run(d, mom_min, trend_required, atr_min, vol_min,
        sl=0.0040, tp=0.0060, hold=900, risk=0.005, lev=10):
    equity = 10_000.0
    net = 0.0
    n = wins = 0
    win_sum = loss_sum = 0.0

    for sym, s in d.items():
        i = 100
        nb = len(s)
        while i < nb - 2:
            mom = s.mom21[i]
            if mom <= mom_min:
                i += 1
                continue
            if trend_required and not s.trend_up[i]:
                i += 1
                continue
            a = s.atr14[i]
            if atr_min and (a is None or a / s.c[i] * 100 < atr_min):
                i += 1
                continue
            if vol_min and s.vol_ratio[i] < vol_min:
                i += 1
                continue

            j = i + 1
            entry = s.o[j]
            if entry <= 0:
                i += 1
                continue
            qty = (risk * equity * lev) / entry
            e_sl = entry * (1 - sl)
            e_tp = entry * (1 + tp)
            reason, ei = _simulate_exit(
                s, j, 1, e_sl, e_tp,
                Config(sl_pct=sl, tp_pct=tp, min_profit_pct=tp,
                       max_hold_s=hold, min_hold_s=15))
            if ei < 0:
                i += 1
                continue
            px = s.c[ei]
            ef = px * (1 - COST_PER_SIDE)
            gross = (ef - entry) * qty
            fees = (entry + ef) * qty * TAKER_FEE
            pnl = gross - fees
            equity += pnl
            net += pnl
            n += 1
            if pnl > 0:
                wins += 1
                win_sum += pnl
            else:
                loss_sum += -pnl
            i = ei + 1

    pf = (win_sum / loss_sum) if loss_sum > 0 else float("inf")
    return n, net, pf, (wins / n if n else 0.0)


data = load()
folds = split_by_position(data, folds=3)

print("=" * 86)
print("SWEEP SELECTIVITY (long-only) — apakah PF ada puncaknya?")
print("=" * 86)
print("  Kalau PF naik-lalu-turun, ada edge yang terlalu tipis.")
print("  Kalau monoton turun, yang tersisa cuma noise.\n")

print(f"  {'momentum>':>9} {'tren':>5} {'atr>':>6} {'vol>':>5} | "
      f"{'n_te':>5} {'pf_te':>7} {'per_tr':>9} {'wr':>6}")
print("  " + "-" * 66)

CASES = [
    (0.00, False, 0.00, 0.0),
    (0.10, False, 0.00, 0.0),
    (0.20, False, 0.00, 0.0),
    (0.30, False, 0.00, 0.0),
    (0.00, True,  0.00, 0.0),
    (0.10, True,  0.00, 0.0),
    (0.20, True,  0.00, 0.0),
    (0.30, True,  0.00, 0.0),
    (0.00, True,  0.10, 0.0),
    (0.10, True,  0.15, 0.0),
    (0.20, True,  0.20, 0.0),
    (0.00, True,  0.00, 1.2),
    (0.10, True,  0.00, 1.3),
    (0.20, True,  0.10, 1.3),
    (0.10, True,  0.15, 1.3),
    (0.20, True,  0.20, 1.5),
]

for mom_min, tr, atr_min, vol_min in CASES:
    n, net, pf, wr = run(folds[2], mom_min, tr, atr_min, vol_min)
    per = net / n if n else 0
    tag = ""
    if pf > 1.0 and n >= 25:
        tag = "  <== POSITIF"
    print(f"  {mom_min:>8.2f}% {'ya' if tr else '-':>5} {atr_min:>5.2f}% {vol_min:>5.1f} | "
          f"{n:>5} {pf:>7.3f} {per:>+9.4f} {wr*100:>5.1f}%{tag}")

print()
print("=" * 86)
print("CEK 3 KANDIDAT TERBAIK DI SEMUA FOLD")
print("=" * 86)
TOP = [(0.10, True, 0.00, 0.0),
       (0.20, True, 0.00, 0.0),
       (0.10, True, 0.15, 0.0),
       (0.20, True, 0.20, 1.3),
       (0.00, True, 0.00, 1.2)]

for mom_min, tr, atr_min, vol_min in TOP:
    rs = [run(folds[k], mom_min, tr, atr_min, vol_min) for k in range(3)]
    pers = [net / n if n else 0 for n, net, _, _ in rs]
    print(f"\n  mom>{mom_min:.2f}% tren={tr} atr>{atr_min} vol>{vol_min}")
    for k, lb in enumerate(("TRAIN", "VALID", "TEST")):
        n, net, pf, wr = rs[k]
        print(f"    {lb:<6} n={n:>4}  net {net:>+9.2f}  pf {pf:>6.3f}  wr {wr*100:>4.1f}%")
    ok = all(p > 0 for p in pers)
    print(f"    -> {'POSITIF DI KETIGA FOLD' if ok else 'tidak positif di semua fold'}")
