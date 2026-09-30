"""
Edge ada (gross -26.93 tanpa biaya).Too tipis untuk 8.5 bps.

Tiga cara memperbesar edge:
  A. SELECTIVITAS - pilih trade yang lebih tajam. Kalau gross edge
    fels -26.93 di 628 trade, mungkin 200 trade terbaik punya
     gross edge 3x lebih besar.
  B. ASIMETRI KERUSAKAN - short selalu rugi. Long-only punya
     gross edge lebih besar dari dua-arah?
  C. EXIT LEBIH CERDAS - SL/TP asimetris. Dengan winrate 45% dan
     TP 0.60%, mungkin SL yang lebih besar membiarkan lebih banyak
     winner survive.

Semua diukur dengan GROSS (tanpa biaya) supaya kita lihat edge
murni, lalu di CECAK dengan biaya realistis.
"""
import sys
import math
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import bt
from bt import (load, split_by_position, Config, Series,
                _simulate_exit, ema_series)

data = load()
folds = split_by_position(data, folds=3)

SPREAD, IMPACT, TAKER = 0.0003, 0.0001, 0.00045
COST = SPREAD + IMPACT          # spread+impact, tanpa fee


def run(d, mode="long", mom_min=0.0, atr_min=0.0, vol_min=0.0,
        trend_req=False, sl=0.0040, tp=0.0060, hold=900,
        risk=0.005, lev=10, fee=TAKER, cost=COST, min_hold=15):
    """Return (n, gross, net, pf_net, wr) — gross TANPA fee."""
    equity = 10_000.0
    gross_sum = 0.0
    net_sum = 0.0
    n = wins = 0
    win_sum = loss_sum = 0.0

    for sym, s in d.items():
        i = 100
        nb = len(s)
        while i < nb - 2:
            if trend_req and not s.trend_up[i]:
                i += 1
                continue
            mom = s.mom21[i]
            if mode == "long":
                if mom <= mom_min:
                    i += 1
                    continue
                d_ = 1
            else:
                if mom > mom_min:
                    d_ = 1
                elif mom < -mom_min:
                    d_ = -1
                else:
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
            if d_ > 0:
                e_sl, e_tp = entry * (1 - sl), entry * (1 + tp)
            else:
                e_sl, e_tp = entry * (1 + sl), entry * (1 - tp)

            reason, ei = _simulate_exit(
                s, j, d_, e_sl, e_tp,
                Config(sl_pct=sl, tp_pct=tp, min_profit_pct=tp,
                       max_hold_s=hold, min_hold_s=min_hold))
            if ei < 0:
                i += 1
                continue

            px = s.c[ei]
            ef = px * (1 - cost) if d_ > 0 else px * (1 + cost)
            g = (ef - entry) * qty * d_
            fees = (entry + ef) * qty * fee
            pnl = g - fees
            equity += pnl
            gross_sum += g
            net_sum += pnl
            n += 1
            if pnl > 0:
                wins += 1
                win_sum += pnl
            else:
                loss_sum += -pnl
            i = ei + 1

    pf = (win_sum / loss_sum) if loss_sum > 0 else float("inf")
    wr = wins / n if n else 0.0
    return n, gross_sum, net_sum, pf, wr


print("=" * 90)
print("A. SELECTIVITAS — apakah edge per trade naik kalau lebih jarang?")
print("=" * 90)
print("  Test fold, long-only, taker 8.5bps/sisi\n")
print(f"  {'mom>':>7} {'atr>':>6} {'vol>':>5} {'tren':>5} | {'n':>5} "
      f"{'gross/tr':>10} {'net/tr':>9} {'pf':>7} {'wr':>6}")
print("  " + "-" * 76)

GRID = [
    (0.00, 0.00, 0.0, False),
    (0.10, 0.00, 0.0, False),
    (0.20, 0.00, 0.0, False),
    (0.30, 0.00, 0.0, False),
    (0.40, 0.00, 0.0, False),
    (0.50, 0.00, 0.0, False),
    (0.00, 0.00, 0.0, True),
    (0.10, 0.00, 0.0, True),
    (0.30, 0.00, 0.0, True),
    (0.00, 0.10, 0.0, True),
    (0.00, 0.15, 0.0, True),
    (0.00, 0.20, 0.0, True),
    (0.10, 0.10, 0.0, True),
    (0.10, 0.15, 0.0, True),
    (0.20, 0.10, 0.0, True),
    (0.20, 0.15, 0.0, True),
    (0.00, 0.10, 1.2, True),
    (0.10, 0.10, 1.2, True),
    (0.00, 0.15, 1.3, True),
    (0.10, 0.15, 1.3, True),
]

rows = []
for mom, atr, vol, tr in GRID:
    n, g, net, pf, wr = run(folds[2], "long", mom, atr, vol, tr)
    if n < 20:
        continue
    g_tr = g / n
    n_tr = net / n
    rows.append((g_tr, mom, atr, vol, tr, n, net, pf, wr))
    print(f"  {mom:>6.2f}% {atr:>5.2f}% {vol:>5.1f} {'ya' if tr else '-':>5} | "
          f"{n:>5} {g_tr:>+9.4f} {n_tr:>+9.4f} {pf:>7.3f} {wr*100:>5.1f}%")

print()
rows.sort(key=lambda x: -x[0])
print("  5 terbaik GROSS per trade:")
for g_tr, mom, atr, vol, tr, n, net, pf, wr in rows[:5]:
    print(f"    mom>{mom:.2f}% atr>{atr}% vol>{vol} tren={tr}  "
          f"gross {g_tr:+.4f}  n {n}  pf {pf:.3f}")
print()

print("=" * 90)
print("B. Kandidat terbaik di SEMUA FOLD (taker 8.5bps)")
print("=" * 90)
TOP = rows[:5]
for g_tr, mom, atr, vol, tr, _, _, _, _ in TOP:
    rs = [run(folds[k], "long", mom, atr, vol, tr) for k in range(3)]
    pers = [r[2] / r[0] if r[0] else 0 for r in rs]
    print(f"\n  mom>{mom:.2f}% atr>{atr}% vol>{vol} tren={tr}")
    for k, lb in enumerate(("TRAIN", "VALID", "TEST")):
        n, g, net, pf, wr = rs[k]
        print(f"    {lb:<6} n={n:>4}  gross {g:>+9.2f}  net {net:>+9.2f}  "
              f"pf {pf:>6.3f}  net/tr {pers[k]:>+8.4f}")
    ok = all(p > 0 for p in pers)
    print(f"    -> {'POSITIF DI KETIGA FOLD' if ok else 'tidak'}")
