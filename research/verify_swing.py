"""
Satu-satunya kandidat yang bertahan di ketiga fold: mom_long, SL 1.5%,
TP 3.0%, hold 4 jam, tanpa early-exit.

Sebelum ini dipakai, tiga pemeriksaan. Semuanya bisa membatalkan:

  1. SIGNIFIKANSI - t-stat di test fold. Kalau < 2, edge-nya mungkin
     noise dan n=93 terlalu kecil untuk membedakan.
  2. SHORT ASIMETRI - kalau edge-nya nyata (bukan drift), arah short
     harusnya punya sesuatu juga. Kalau short 100% rugi sementara long
     100% untung, itu drift.
  3. SUB-SAMPLING BERSIH - sampling dari SELURUH test fold, bukan
     dari trade yang sudah terpilih. Ini mengukur robustness tanpa
     conditioning on selection.
  4. SUB-PERIODE - bagi test fold jadi 3, apakah ketiganya positif?
     Edge yang cuma di satu periode adalah event, bukan strategi.
"""
import sys
import math
import random
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import bt
from bt import (load, backtest, split_by_position, Config, Series, _slice,
                COST_PER_SIDE, TAKER_FEE, _simulate_exit)

data = load()
folds = split_by_position(data, folds=3)
TRAIN, VAL, TEST = folds[0], folds[1], folds[2]

BEST = dict(entry="mom_long", mom_thresh=0.0, sl_pct=0.0150, tp_pct=0.0300,
            min_profit_pct=None, max_hold_s=14400, min_hold_s=60)


def collect(d, cfg, direction_mode="long"):
    """Kumpulkan (pnl, symbol) per trade."""
    out = []
    for sym, s in d.items():
        i = 30
        nb = len(s)
        while i < nb - 2:
            if direction_mode == "long":
                direction = 1 if s.mom21[i] > cfg.mom_thresh else 0
            else:
                direction = -1 if s.mom21[i] < cfg.mom_thresh else 0
            if direction == 0:
                i += 1
                continue
            j = i + 1
            entry = s.o[j]
            if entry <= 0:
                i += 1
                continue
            qty = 50.0 / entry
            if direction > 0:
                sl = entry * (1 - cfg.sl_pct)
                tp = entry * (1 + cfg.tp_pct)
            else:
                sl = entry * (1 + cfg.sl_pct)
                tp = entry * (1 - cfg.tp_pct)
            reason, ei = _simulate_exit(s, j, direction, sl, tp, cfg)
            if ei < 0:
                i += 1
                continue
            px = s.c[ei]
            ef = px * (1 - COST_PER_SIDE) if direction > 0 else px * (1 + COST_PER_SIDE)
            gross = (ef - entry) * qty * direction
            fees = (entry + ef) * qty * TAKER_FEE
            held = (s.ts[ei] - s.ts[j]) / 1000.0
            out.append((gross - fees, sym.split("/")[0], held, direction))
            i = ei + 1
    return out


def stats(pnls, label):
    n = len(pnls)
    if n < 2:
        print(f"  {label}: n={n} - tidak cukup")
        return
    m = sum(pnls) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in pnls) / (n - 1))
    t = m / (sd / math.sqrt(n)) if sd else 0.0
    pos = sum(x for x in pnls if x > 0)
    neg = -sum(x for x in pnls if x < 0)
    pf = pos / neg if neg > 0 else float("inf")
    print(f"  {label:<22} n={n:>4}  mean {m:>+8.4f}  sd {sd:>6.3f}  "
          f"t {t:>+6.2f}  pf {pf:>6.3f}  wr {sum(1 for x in pnls if x>0)/n*100:>5.1f}%"
          f"{'  <-- SIGNIFIKAN' if t > 2 else ''}")
    return n, m, t, pf


cfg = Config(name="best", **BEST)

print("=" * 84)
print("1. SIGNIFIKAN STATISTIK")
print("=" * 84)
long_tr = {}
for label, fold in (("TRAIN", TRAIN), ("VALIDASI", VAL), ("TEST", TEST)):
    tr = collect(fold, cfg)
    long_tr[label] = tr
    stats([p for p, _, _, _ in tr], f"LONG  {label}")
print()

print("=" * 84)
print("2. ASIMETRI ARAH — short harusnya bukan 100% rugi kalau ini alpha")
print("=" * 84)
print("  Kalau long untung dan short rugi total, itu drift, bukan prediksi.\n")
for label, fold in (("TRAIN", TRAIN), ("VALID", VAL), ("TEST", TEST)):
    tr = collect(fold, cfg, direction_mode="short")
    stats([p for p, _, _, _ in tr], f"SHORT {label}")
print()
print("  Pembacaan:")
l_test = [p for p, _, _, _ in long_tr["TEST"]]
s_test = [p for p, _, _, _ in collect(TEST, cfg, "short")]
l_mean = sum(l_test) / len(l_test)
s_mean = sum(s_test) / len(s_test)
print(f"    long mean  {l_mean:+.4f}   short mean {s_mean:+.4f}")
if l_mean > 0 and s_mean < 0:
    print("    -> ASIMETRI. Edge iniirectional, bukan prediktif.")
    print("       Bot hanya untung ketika pasar naik; rugi dua kali")
    print("       lebih besar ketika turun. Itu betting arah, bukan alpha.")
print()

print("=" * 84)
print("3. SUB-SAMPLING BERSIH (dari seluruh test fold)")
print("=" * 84)
random.seed(3)
pnls = [p for p, _, _, _ in long_tr["TEST"]]
if len(pnls) >= 40:
    for n_sub in (20, 30, 40):
        if len(pnls) < n_sub * 3:
            continue
        above = 0
        trials = 400
        for _ in range(trials):
            smp = random.sample(pnls, n_sub)
            pos = sum(x for x in smp if x > 0)
            neg = -sum(x for x in smp if x < 0)
            if neg > 0 and pos / neg > 1.0:
                above += 1
        print(f"  n={n_sub:>3}: PF>1 di {above}/{trials} ({above/trials*100:>4.0f}%)")
print()

print("=" * 84)
print("4. SUB-PERIODE dalam test fold")
print("=" * 84)
# Bagi test fold per simbol jadi 3 bagian kronologis
third = len(next(iter(TEST.values()))) // 3
subs = []
for k in range(3):
    d = {sym: _slice(s, k * third, (k + 1) * third) for sym, s in TEST.items()
         if len(s) >= (k + 1) * third + 30}
    if d:
        subs.append(d)
print(f"  test fold dibagi 3 sub-periode ({len(subs)} used)\n")
for k, d in enumerate(subs):
    tr = collect(d, cfg)
    stats([p for p, _, _, _ in tr], f"sub-periode {k}")
print()

print("=" * 84)
print("5. HOLD TIME AKTUAL")
print("=" * 84)
holds = sorted(h for _, _, h, _ in long_tr["TEST"])
if holds:
    n = len(holds)
    print(f"  median {holds[n//2]:>8.0f}s ({holds[n//2]/60:.0f} menit)")
    print(f"  p10    {holds[n//10]:>8.0f}s   p90 {holds[9*n//10]:>8.0f}s")
    print(f"  mean   {sum(holds)/n:>8.0f}s ({sum(holds)/n/60:.0f} menit)")
print()
print("  Bandingkan dengan max_hold_seconds produksi = 300s (5 menit).")
print("  Kalau median jauh di atas itu, horizon scalp 5 menit salah")
print("  untuk konfigurasi ini - bukan karena strategi buruk, tapi")
print("  karena tidak pernah cukup waktu untuk mencapai TP.")
