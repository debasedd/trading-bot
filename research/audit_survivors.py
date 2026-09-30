"""
Empat kandidat bertahan. Sebelum dipakai, uji apakah mereka nyata.

Yang perlu diperiksa:
  1. Signifikan statistik. PF 1.3 dengan n=80 bisa_noise atau nyata.
  2. Apakah ini drift yang sama seperti momentum-long? Yaitu:
     long side (train) dan TEST sama-sama bullish, tapi VALIDASI tidak.
     Perhatikan: mom_long punya per_va +0.0289 (hampir nol) di
     14400/2.0/6.0, tapi per_te +2.4183. Itu TIDAK konsisten -
     pola yang biasanya berarti overfit, bukan edge.
     long side (train) dan TEST sama-sama bullish, tapi VALIDASI tidak.
     seharusnya juga punya sesuatu, atau setidaknya tidak jauh
     lebih buruk dari long.
  4. Sub-sampling. Kalau kita ambil 80 trade acak, berapa yang
     survive? Kalau banyak, edge-nya fragile.
"""
import sys
import math
import random
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bt import load, backtest, split_by_position, Config

data = load()
folds = split_by_position(data, folds=3)
TRAIN, VAL, TEST = folds[0], folds[1], folds[2]

SURVIVORS = [
    ("mom_long 2.0/6.0 14400", {"entry": "mom_long", "mom_thresh": 0.0}, 0.020, 0.060, 14400),
    ("mom_long 2.0/4.0 14400", {"entry": "mom_long", "mom_thresh": 0.0}, 0.020, 0.040, 14400),
    ("mom_long 2.0/6.0  7200", {"entry": "mom_long", "mom_thresh": 0.0}, 0.020, 0.060,  7200),
    ("mom_long 1.5/4.0 14400", {"entry": "mom_long", "mom_thresh": 0.0}, 0.015, 0.040, 14400),
]

print("=" * 86)
print("1. KONSISTENSI ANTAR FOLD")
print("=" * 86)
print("  Kalau edge nyata, per_tr harus stabil antar fold.")
print("  Kalau drift, satu fold akan jauh lebih baik dari yang lain.\n")
for name, ep, sl, tp, hold in SURVIVORS:
    cfg = Config(name="x", **ep, sl_pct=sl, tp_pct=tp,
                 min_profit_pct=None, max_hold_s=hold, min_hold_s=60)
    rs = [backtest(d, cfg) for d in (TRAIN, VAL, TEST)]
    pers = [r.net/r.trades if r.trades else 0 for r in rs]
    ns = [r.trades for r in rs]
    spread = max(pers) - min(pers)
    mean = sum(pers)/3
    unstable = spread > abs(mean) * 1.5
    print(f"  {name}")
    print(f"    train {pers[0]:+7.3f} (n={ns[0]:>3})   "
          f"valid {pers[1]:+7.3f} (n={ns[1]:>3})   "
          f"test {pers[2]:+7.3f} (n={ns[2]:>3})")
    print(f"    mean {mean:+7.3f}  spread {spread:7.3f}  "
          f"{'UNSTABLE - kemungkinan drift' if unstable else 'relatif stabil'}")
    print()

print("=" * 86)
print("2. SIGNIFIKAN STATISTIK (test fold)")
print("=" * 86)
print("  PF>1 dengan n kecil bisa noise. Butuh t-stat > 2.\n")


def t_stat(wins_n, losses_n, win_sum, loss_sum):
    """t-stat dari rasio win/loss mean."""
    if wins_n == 0 or losses_n == 0:
        return 0.0
    aw = win_sum / wins_n
    al = loss_sum / losses_n
    if al <= 0:
        return 0.0
    # Variance of per-trade P&L
    return 0.0  # placeholder, replaced below


from bt import COST_PER_SIDE, TAKER_FEE, _simulate_exit


def collect_trades(d, cfg):
    """Kumpulkan P&L per trade untuk signifikansi."""
    pnls = []
    for sym, s in d.items():
        i = 30
        nb = len(s)
        while i < nb - 2:
            direction = 1 if s.mom21[i] > cfg.mom_thresh else 0
            if direction == 0:
                i += 1; continue
            j = i + 1
            entry = s.o[j]
            if entry <= 0:
                i += 1; continue
            margin = 0.005 * 10_000.0
            qty = margin / entry
            sl = entry * (1 - cfg.sl_pct)
            tp = entry * (1 + cfg.tp_pct)
            reason, ei = _simulate_exit(s, j, direction, sl, tp, cfg)
            if ei < 0:
                i += 1; continue
            px = s.c[ei]
            ef = px * (1 - COST_PER_SIDE)
            gross = (ef - entry) * qty
            fees = (entry + ef) * qty * TAKER_FEE
            pnls.append(gross - fees)
            i = ei + 1
    return pnls


for name, ep, sl, tp, hold in SURVIVORS:
    cfg = Config(name="x", **ep, sl_pct=sl, tp_pct=tp,
                 min_profit_pct=None, max_hold_s=hold, min_hold_s=60)
    for fold_name, fold in (("TRAIN", TRAIN), ("VALID", VAL), ("TEST", TEST)):
        pnls = collect_trades(fold, cfg)
        n = len(pnls)
        if n < 2:
            continue
        m = sum(pnls)/n
        sd = math.sqrt(sum((x-m)**2 for x in pnls)/(n-1))
        t = m/(sd/math.sqrt(n)) if sd else 0
        marker = " <-- SIGNIFIKAN" if t > 2 else ""
        print(f"  {name:<26} {fold_name:<6} n={n:>3}  mean {m:>+7.4f}  "
              f"sd {sd:>6.4f}  t {t:>+6.2f}{marker}")
    print()

print("=" * 86)
print("3. SUB-SAMPLING — apakah edge fragile?")
print("=" * 86)
print("  Ambil 50 trade acak dari test fold, 500x. Berapa kali PF>1?\n")
random.seed(7)
for name, ep, sl, tp, hold in SURVIVORS[:2]:
    cfg = Config(name="x", **ep, sl_pct=sl, tp_pct=tp,
                 min_profit_pct=None, max_hold_s=hold, min_hold_s=60)
    pnls = collect_trades(TEST, cfg)
    if len(pnls) < 60:
        continue
    wins_above = 0
    trials = 500
    for _ in range(trials):
        sample = random.sample(pnls, 50)
        pos = sum(x for x in sample if x > 0)
        neg = -sum(x for x in sample if x < 0)
        if neg > 0 and pos / neg > 1.0:
            wins_above += 1
    print(f"  {name:<26} PF>1 di {wins_above}/{trials} ({wins_above/trials*100:.0f}%) "
          f"dari subsample n=50")
print()
print("  >90% = edge robust. 60-80% = fragile. <50% = noise.")
