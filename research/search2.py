"""
Walk-forward dengan split per-simbol (menghormati gap di data).

v1 search.py gagal karena membagi berdasarkan timestamp global: ada
jeda 4 HARI di tengah data, jadi fold validasi kosong untuk 20/20
simbol dan semua kandidat "gagal" tanpa benar-benar diuji.

Split di sini per-simbol, kronologis, sehingga setiap simbol mewarisi
proporsi train/valid/test yang sama dan gap dilewati tanpa merusak
fold.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bt import load, backtest, split_by_position, Config, Series

data = load()
folds = split_by_position(data, folds=3)

for k, label in ((0, "TRAIN"), (1, "VALIDASI"), (2, "TEST")):
    d = folds[k]
    bars = sum(len(s) for s in d.values())
    print(f"  {label:9} {len(d):>2} simbol  {bars:>6} bar  "
          f"{min(s.ts[0] for s in d.values())} .. {max(s.ts[-1] for s in d.values())}")
print()
print(f"data: {len(data)} simbol, {sum(len(s) for s in data.values())} bar 1m")
print(f"cost: 17 bps round trip (8.5 bps per side)\n")

TRAIN, VAL, TEST = folds[0], folds[1], folds[2]

ENTRIES = [
    ("mom_long",      {"entry": "mom_long", "mom_thresh": 0.0}),
    ("mom_long_t",    {"entry": "mom_long", "mom_thresh": 0.10}),
    ("mom_long_st",   {"entry": "mom_long", "mom_thresh": 0.20}),
    ("mom_both",      {"entry": "mom_both", "mom_thresh": 0.15}),
    ("rsi_rev",       {"entry": "rsi_rev", "rsi_low": 30.0, "rsi_high": 70.0}),
    ("rsi_rev_tight", {"entry": "rsi_rev", "rsi_low": 35.0, "rsi_high": 65.0}),
    ("ema",           {"entry": "ema"}),
]

EXITS = []
for sl in (0.0030, 0.0037, 0.0050):
    for tp in (0.0060, 0.0090, 0.0130, 0.0180):
        for mp in (None, 0.0042, 0.0060, 0.0090):
            for hold in (180, 300, 600):
                EXITS.append({"sl_pct": sl, "tp_pct": tp,
                              "min_profit_pct": mp, "max_hold_s": hold,
                              "min_hold_s": 15})

print("=" * 82)
print("FASE 1 — SEARCH DI TRAIN")
print("=" * 82)
print(f"  {len(ENTRIES)} entry x {len(EXITS)} exit = {len(ENTRIES)*len(EXITS)} kandidat\n")

scored = []
for ename, ep in ENTRIES:
    for xp in EXITS:
        cfg = Config(name="c", **ep, **xp)
        r = backtest(TRAIN, cfg)
        if r.trades < 40:
            continue
        scored.append((r.pf, r.net, ename, ep, xp, r))

scored.sort(key=lambda x: -x[0])
print(f"{len(scored)} kandidat lolos filter 40 trade\n")
print(f"  {'#':>3} {'entry':<15} {'SL':>6} {'TP':>6} {'MP':>6} {'hold':>5} "
      f"{'pf':>6} {'net':>9} {'wr':>6} {'n':>5}")
for i, (pf, net, ename, ep, xp, r) in enumerate(scored[:15], 1):
    print(f"  {i:>3} {ename:<15} {xp['sl_pct']*100:>5.2f}% {xp['tp_pct']*100:>5.2f}% "
          f"{(xp['min_profit_pct']*100 if xp['min_profit_pct'] else 0):>5.2f}% "
          f"{xp['max_hold_s']:>5} {pf:>6.3f} {net:>+9.2f} {r.winrate*100:>5.1f}% {r.trades:>5}")
print()

print("=" * 82)
print("FASE 2 — VALIDASI")
print("=" * 82)
print(f"  {'#':>3} {'entry':<15} {'pf_tr':>6} {'pf_va':>6} {'net_va':>9} "
      f"{'wr_va':>6} {'n_va':>5}  verdict")
print("  " + "-"*70)

survivors = []
for i, (pf, net, ename, ep, xp, rtr) in enumerate(scored[:15], 1):
    cfg = Config(name="v", **ep, **xp)
    rv = backtest(VAL, cfg)
    ok = rv.pf > 1.0 and rv.trades >= 15
    if ok:
        survivors.append((ename, ep, xp, pf, rv))
    print(f"  {i:>3} {ename:<15} {pf:>6.3f} {rv.pf:>6.3f} {rv.net:>+9.2f} "
          f"{rv.winrate*100:>5.1f}% {rv.trades:>5}  {'LOLOS' if ok else 'gagal'}")

print()
if not survivors:
    print("  TIDAK ADA yang bertahan.")
    print()
    print("  1008 kandidat, 15 terbaik di train, nol yang punya PF>1 di")
    print("  fold berikutnya. Ini bukan tuning yang belum cukup - ini")
    print("  'tidak ada edge' pada data ini.")
    raise SystemExit

print("=" * 82)
print("FASE 3 — TEST (fold yang belum pernah dilihat)")
print("=" * 82)
survivors.sort(key=lambda x: -x[4].pf)

for rank, (ename, ep, xp, pft, rv) in enumerate(survivors[:5], 1):
    cfg = Config(name="t", **ep, **xp)
    rt = backtest(TEST, cfg)
    print(f"\n  #{rank}  {ename}  SL {xp['sl_pct']*100:.2f}%  TP {xp['tp_pct']*100:.2f}%  "
          f"hold {xp['max_hold_s']}s")
    print(f"      train  pf {pft:>6.3f}  n {backtest(TRAIN, Config(name='t',**ep,**xp)).trades:>4}")
    print(f"      valid  pf {rv.pf:>6.3f}  n {rv.trades:>4}  net {rv.net:>+8.2f}")
    print(f"      TEST   pf {rt.pf:>6.3f}  n {rt.trades:>4}  net {rt.net:>+8.2f}  "
          f"wr {rt.winrate*100:.1f}%  maxDD {rt.max_dd:.2f}")
    print(f"      -> {'BERTahan' if rt.pf > 1.0 else 'GAGAL — overfit'}")
