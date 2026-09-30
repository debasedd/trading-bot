"""
Temuan yang membalikkan kesimpulan sebelumnya.

Di test fold, config baru memberi:
    gross (tanpa biaya)  -26.93
    biaya                -278.08
    net                  -305.01

Gross nyaris impas. Biaya 91% dari seluruh loss. Jadi edge-nya ADA -
tipis, tapi ada. Yang membunuh bukan sinyalnya, tapi BIAYANYA.

Config lama: gross -200.66, biaya -434.87. Gross-nya 7x lebih buruk.
Jadi perubahan config tadi bukan cuma mengurangi loss - dia
MEMBEBASKAN edge yang selama ini tersembunyi di balik biaya.

Pertanyaan sekarang: berapa biaya yang bisa ditanggung? Dan
bagaimana mendapatkannya secara NYATA (bukan dengan mengorbankan
fill rate)?

Diuji:
  A. Turunkan fee (maker 0.015% vs taker 0.045%)
  B. Turunkan spread floor (3 bps -> 1 bps) - hanya realistis untuk
     pair yang sangat liquid
  C. Kurangi frekuensi trade (PF per trade harus naik)
  D. Kombinasi
"""
import sys
import math
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import bt
from bt import (load, backtest, split_by_position, Config, Series,
                _simulate_exit, ema_series)

data = load()
folds = split_by_position(data, folds=3)


def run_cost(d, half_spread, impact, taker_fee, maker=False,
             mom_thresh=0.0, sl=0.0040, tp=0.0060, hold=900,
             risk=0.005, lev=10):
    """Backtest dengan parameter biaya yang bisa diubah."""
    COST = half_spread + impact
    equity = 10_000.0
    net = 0.0
    n = wins = 0
    win_sum = loss_sum = 0.0

    for sym, s in d.items():
        i = 100
        nb = len(s)
        while i < nb - 2:
            direction = 1 if s.mom21[i] > mom_thresh else 0
            if direction == 0:
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
                s, j, direction, e_sl, e_tp,
                Config(sl_pct=sl, tp_pct=tp, min_profit_pct=tp,
                       max_hold_s=hold, min_hold_s=15))
            if ei < 0:
                i += 1
                continue
            px = s.c[ei]
            ef = px * (1 - COST)
            gross = (ef - entry) * qty
            fees = (entry + ef) * qty * taker_fee
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


print("=" * 88)
print("BERAPA BIAYA YANG BISA DITANGGUNG?")
print("=" * 88)
print("  gross di test fold = -26.93 (hampir impas tanpa biaya)")
print("  biaya sekarang     = 278.08 per fold test")
print()
print("  Karena itu PERSIS ada titik impas. Cari di mana.\n")

print(f"  {'spread':>7} {'impact':>7} {'fee':>7} {'per sisi':>9} | "
      f"{'n':>5} {'net_te':>9} {'pf_te':>7} {'per_tr':>9} {'wr':>6}")
print("  " + "-" * 70)

CASES = [
    ("produksi lama",  0.0003, 0.0001, 0.00045),
    ("config baru",    0.0003, 0.0001, 0.00045),
    ("maker fee",      0.0003, 0.0001, 0.00015),
    ("maker + spread 1bp", 0.0001, 0.0001, 0.00015),
    ("maker + spread 0.5bp", 0.00005, 0.0001, 0.00015),
    ("taker, spread 1bp", 0.0001, 0.0001, 0.00045),
    ("maker + no impact", 0.0003, 0.0, 0.00015),
    ("maker + spread 1bp + no impact", 0.0001, 0.0, 0.00015),
    ("NOL biaya (batas atas)", 0.0, 0.0, 0.0),
]

for name, hs, im, fee in CASES:
    n, net, pf, wr = run_cost(folds[2], hs, im, fee)
    per = net / n if n else 0
    per_side = (hs + im + fee) * 10000
    tag = "  <== PROFITABLE" if per > 0 and n >= 30 else ""
    print(f"  {hs*10000:>6.1f}bp {im*10000:>6.1f}bp {fee*10000:>6.1f}bp "
          f"{per_side:>7.1f}bp | {n:>5} {net:>+9.2f} {pf:>7.3f} "
          f"{per:>+9.4f} {wr*100:>5.1f}%{tag}")

print()
print("=" * 88)
print("KASUS NYATA: maker fill rate")
print("=" * 88)
print("""
Maker 0.015% vs taker 0.045% itu 3x lebih murah, TAPI order pasif
hanya terisi saat harga bergerak MENUJU order. Market maker yang
menempatkan limit di bawah harga hanya terisi saat harga turun.

Jadi maker bukan murah - itu murah tapi hanya benar 50% waktu.
Sisa 50% waktu order tidak terisi, dan bot kehilangan trade.
""")
print("  tradeoff nyata: turunkan biaya per trade, tapi turun volume")
print("  dengan threshold lebih tinggi (selectivity).")
print()

# Kombinasi: maker + selectivity
print(f"  {'mom>':>7} | {'taker n':>8} {'taker pf':>9} | "
      f"{'maker n':>8} {'maker pf':>9} {'maker net':>10}")
print("  " + "-" * 62)
for mom in (0.0, 0.10, 0.20, 0.30, 0.40):
    nt, nett, pft, wrt = run_cost(folds[2], 0.0003, 0.0001, 0.00045, mom_thresh=mom)
    nm, netm, pfm, wrm = run_cost(folds[2], 0.0003, 0.0001, 0.00015, mom_thresh=mom)
    print(f"  {mom:>6.2f}% | {nt:>8} {pft:>9.3f} | {nm:>8} {pfm:>9.3f} {netm:>+10.2f}")

print()
print("=" * 88)
print("SEMUA FOLD dengan konfigurasi terbaik")
print("=" * 88)

# Cari kombinasi yang positif di test dulu
best = None
for mom in (0.0, 0.1, 0.2, 0.3, 0.4, 0.5):
    for hs, im, fee in ((0.0003, 0.0001, 0.00045),
                        (0.0001, 0.0001, 0.00015),
                        (0.0001, 0.0, 0.00015)):
        n, net, pf, wr = run_cost(folds[2], hs, im, fee, mom_thresh=mom)
        per = net / n if n else -999
        if n >= 30 and (best is None or per > best[0]):
            best = (per, mom, hs, im, fee)

if best:
    per, mom, hs, im, fee = best
    print(f"  terbaik di TEST: mom>{mom:.2f}%  spread {hs*10000:.1f}bp  "
          f"impact {im*10000:.1f}bp  fee {fee*10000:.1f}bp  per_tr {per:+.4f}")
    print()
    for k, lb in enumerate(("TRAIN", "VALID", "TEST")):
        n, net, pf, wr = run_cost(folds[k], hs, im, fee, mom_thresh=mom)
        pt = net / n if n else 0
        print(f"    {lb:<6} n={n:>4}  net {net:>+9.2f}  pf {pf:>6.3f}  "
              f"per_tr {pt:>+9.4f}  wr {wr*100:>4.1f}%")
    print()
    print(f"   biaya yang dibutuhkan: <={per*10000:.1f} bps per sisi untuk impas di TEST")
