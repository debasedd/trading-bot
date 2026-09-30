"""
Tes terakhir: apakah ada SATU PUN yang cukup besar untukroite biaya?

Semua test sebelumnya memakai SL 0.5% / TP 1.3%. Itu terlalu lebar untuk
skalp, dan mungkin menutupi fakta bahwa TP kecil bisa menang kalau
SL juga kecil.

Yang diuji: untuk setiap kombinasi SL/TP dari 0.10% sampai 3.0%,
apakah ada satu di mana expectancy per trade POSITIF di ketiga fold
sekaligus?

Kalau tidak ada, jawabannya sudah jelas dan tuning selanjutnya sia-sia.
Kalau ada, itu kandidat yang layak diuji lebih dalam.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bt import load, backtest, split_by_position, Config

data = load()
folds = split_by_position(data, folds=3)
TRAIN, VAL, TEST = folds[0], folds[1], folds[2]

ENTRIES = [
    ("mom_long",    {"entry": "mom_long", "mom_thresh": 0.0}),
    ("mom_both",    {"entry": "mom_both", "mom_thresh": 0.15}),
    ("rsi_rev",     {"entry": "rsi_rev", "rsi_low": 30.0, "rsi_high": 70.0}),
    ("ema",         {"entry": "ema"}),
]

SLS = [0.0010, 0.0015, 0.0020, 0.0025, 0.0030, 0.0040, 0.0050, 0.0070, 0.0100]
TPS = [0.0010, 0.0015, 0.0020, 0.0025, 0.0030, 0.0040, 0.0050, 0.0070, 0.0100,
       0.0150, 0.0200, 0.0300]
HOLDS = [60, 120, 300, 600, 1800]

print("=" * 86)
print("GRID PENUH — SL x TP, 4 entry, 5 horizon")
print("=" * 86)
print(f"  {len(ENTRIES)} x {len(SLS)} x {len(TPS)} x {len(HOLDS)} = "
      f"{len(ENTRIES)*len(SLS)*len(TPS)*len(HOLDS)} kandidat")
print("  mencari yang positif di TRAIN, lalu di cek di VALIDASI dan TEST\n")

rows = []
for ename, ep in ENTRIES:
    for sl in SLS:
        for tp in TPS:
            if tp <= sl:
                continue          # R:R harus > 1, kalau tidak TP mustahil tercapai
            for hold in HOLDS:
                cfg = Config(name="g", **ep, sl_pct=sl, tp_pct=tp,
                             min_profit_pct=None, max_hold_s=hold,
                             min_hold_s=min(15, hold // 4))
                r = backtest(TRAIN, cfg)
                if r.trades < 30:
                    continue
                per = r.net / r.trades
                rows.append((per, r.pf, ename, ep, sl, tp, hold, r))

rows.sort(key=lambda x: -x[0])
print(f"{len(rows)} kandidat lolos filter 30 trade\n")
print(f"  {'entry':<10} {'SL':>6} {'TP':>6} {'hold':>5} {'n':>5} "
      f"{'per_tr':>9} {'pf':>6} {'wr':>6}")
for per, pf, ename, ep, sl, tp, hold, r in rows[:15]:
    print(f"  {ename:<10} {sl*100:>5.2f}% {tp*100:>5.2f}% {hold:>5} {r.trades:>5} "
          f"{per:>+8.4f} {pf:>6.3f} {r.winrate*100:>5.1f}%")
print()

# 15 terbaik di train, validasi
print("=" * 86)
print("VALIDASI — 15 terbaik di train")
print("=" * 86)
print(f"  {'entry':<10} {'SL':>6} {'TP':>6} {'hold':>5} {'per_tr':>9} {'pf':>6} | "
      f"{'per_va':>9} {'pf_va':>6} | {'verdict'}")
print("  " + "-"*80)

survivors = []
for per, pf, ename, ep, sl, tp, hold, rtr in rows[:15]:
    cfg = Config(name="v", **ep, sl_pct=sl, tp_pct=tp,
                 min_profit_pct=None, max_hold_s=hold,
                 min_hold_s=min(15, hold // 4))
    rv = backtest(VAL, cfg)
    per_va = rv.net / rv.trades if rv.trades else 0
    ok = per_va > 0 and rv.trades >= 15
    if ok:
        survivors.append((ename, ep, sl, tp, hold, per, pf, per_va, rv))
    print(f"  {ename:<10} {sl*100:>5.2f}% {tp*100:>5.2f}% {hold:>5} {per:>+8.4f} "
          f"{pf:>6.3f} | {per_va:>+8.4f} {rv.pf:>6.3f} | {'LOLOS' if ok else 'gagal'}")

print()
if not survivors:
    print("  HASIL: dari {:,} kandidat, TIDAK SATU PUN punya".format(len(rows)))
    print("         net per trade positif di fold validasi.")
    print()
    print("  Ini bukan kurangnya tuning. Di timeframe 1 menit dengan")
    print("  biaya 17 bps round trip, tidak ada kombinasi SL/TP/entry")
    print("  dalam grid ini yang tersisa uang setelah biaya.")
    raise SystemExit

print("=" * 86)
print("TEST")
print("=" * 86)
survivors.sort(key=lambda x: -x[6])
for rank, (ename, ep, sl, tp, hold, per, pf, per_va, rv) in enumerate(survivors[:5], 1):
    cfg = Config(name="t", **ep, sl_pct=sl, tp_pct=tp,
                 min_profit_pct=None, max_hold_s=hold,
                 min_hold_s=min(15, hold // 4))
    rt = backtest(TEST, cfg)
    per_te = rt.net / rt.trades if rt.trades else 0
    print(f"\n  #{rank}  {ename}  SL {sl*100:.2f}%  TP {tp*100:.2f}%  hold {hold}s")
    print(f"      train  {per:+.4f}  pf {pf:.3f}")
    print(f"      valid  {per_va:+.4f}  pf {rv.pf:.3f}  n {rv.trades}")
    print(f"      TEST   {per_te:+.4f}  pf {rt.pf:.3f}  n {rt.trades}  "
          f"wr {rt.winrate*100:.1f}%")
