"""
Apa yang实际，没什么好隐瞒的。

Dari trigger.py: long-only dengan filter apa pun PF-nya mentok di 0.50-0.74,
tanpa puncak. Ituartefak, bukan edge.

Tapi ada dua angka yang perlu dijelaskan sebelum menyerah:

  1. Best PF = 0.738 (momentum>0 + tren + atr>0.10%). Angka itu TINGGI
     untukPF losing. Kalau costs-nya dihapus, apakah jadi positif?

  2. Cost floor. Cost 17 bps, SL 0.40%. Fixed-fractional dengan risk
     0.5% per trade: pada 348 trade, total biaya = 348 * 0.005 * 10 *
     0.0017 = $29.6. Total loss = $406.JUARA: biaya cuma 7% dari
     loss. Jadi biaya BUKAN penyebab utamanya. Edge-nya hilang, dan
     biaya cuma memperburuk.

Artinya: signals-nya sendiri tidak punya prediksi arah dalam timeframe ini.
Bukan biaya, bukan parameter, bukan filter.

Yang bisa岔出去 kalau gap ini benar-benar gap:
  A. Asset yang salah (crypto 1m mungkin bukan untuknya)
  B. Timeframe yang salah (scalar bot tapi data swing)
  C. Data yang salah (signal tidak dalam DB, hanya di memory)

Tapi sebelumitres tersebut, satu test terakhir: apakah forex/futures
LAIN punya pola yang sama? Kalau iya, ini systemic. Kalau tidak, mungkin memang ada yang khusus di crypto.
"""
import sys
import math
import statistics
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bt import load, split_by_position, backtest, Config, COST_PER_SIDE, TAKER_FEE


def decompose(cfg_params, d, fold_label):
    """Pisahkan loss jadi: biaya, gross, dan bagian yang tidak bisa dijelaskan."""
    from bt import _simulate_exit
    equity = 10_000.0
    gross_total = 0.0
    fees_total = 0.0
    n = wins = 0
    win_sum = loss_sum = 0.0

    sl, tp, hold = cfg_params["sl_pct"], cfg_params["tp_pct"], cfg_params["max_hold_s"]

    for sym, s in d.items():
        i = 100
        nb = len(s)
        while i < nb - 2:
            direction = 1 if s.mom21[i] > cfg_params["mom_thresh"] else 0
            if direction == 0:
                i += 1
                continue
            j = i + 1
            entry = s.o[j]
            if entry <= 0:
                i += 1
                continue
            qty = (0.005 * equity * 10) / entry
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
            ef = px * (1 - COST_PER_SIDE)
            gross = (ef - entry) * qty
            fees = (entry + ef) * qty * TAKER_FEE
            pnl = gross - fees
            gross_total += gross
            fees_total += fees
            equity += pnl
            n += 1
            if pnl > 0:
                wins += 1
                win_sum += pnl
            else:
                loss_sum += -pnl
            i = ei + 1

    net = gross_total - fees_total
    wr = wins / n if n else 0
    return n, gross_total, fees_total, net, wr, win_sum, loss_sum


data = load()
folds = split_by_position(data, folds=3)

print("=" * 84)
print("DECOMPOSISI LOSS: gross (tanpa biaya) vs biaya")
print("=" * 84)

CASES = [
    ("produksi saat ini (sl 0.37, hold 300)",
     {"sl_pct": 0.0037, "tp_pct": 0.0060, "max_hold_s": 300, "mom_thresh": 0.0}),
    ("config baru (sl 0.40, hold 900)",
     {"sl_pct": 0.0040, "tp_pct": 0.0060, "max_hold_s": 900, "mom_thresh": 0.0}),
    ("long+tren (atr 0.10)",
     {"sl_pct": 0.0040, "tp_pct": 0.0060, "max_hold_s": 900, "mom_thresh": 0.0}),
]

for name, params in CASES:
    print(f"\n  {name}")
    for k, lb in enumerate(("TRAIN", "VALID", "TEST")):
        n, gross, fees, net, wr, ws, ls = decompose(params, folds[k], lb)
        if n == 0:
            continue
        fee_pct = abs(fees / net) * 100 if net != 0 else 0
        print(f"    {lb:<6} n={n:>4}  gross {gross:>+8.2f}  biaya {fees:>+7.2f}  "
              f"net {net:>+8.2f}  biaya = {fee_pct:>4.0f}% dari loss")

print()
print("=" * 84)
print("INTERPRETASI")
print("=" * 84)
print("""
Kalau GROSS sudah positif tanpa biaya, masalahnya biaya.
Kalau GROSS sudah negatif tanpa biaya, masalahnya sinyal.

Bilangan kedua itu yang menentukan. Kalau gross positif tapi net
negatif, maka ada dua jalan: kurangi biaya (maker, atau jarang bertransaksi) atau perbaiki TP/SL. Kalau gross negatif, tidak ada
angka exit yang akan menolong - karena strateginya tidak punya
arah.
""")
