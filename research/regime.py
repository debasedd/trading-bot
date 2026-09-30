"""
Tes filter regime — satu-satunya perubahan arsitektur yangDerived dari
temuan sendiri.

TEMUAN: long +0.15, short -0.37. Asimetri itu bukan noise, itu drift.
Crypto 1m merangkak ke atas. Bot yang mengambil short di pasar yang
merangkak continuously deterministic rugi.

Kalau benar begitu, perbaikannya BUKAN mengganti sinyal, tapi
membuat bot tidak bertransaksi melawan arus. Filter
regime: hanya long kalau tren naik, hanya short kalau tren turun,
di luar itu tidak masuk.

Yang diuji:
  A. Selalu long (baseline - hanya naik)
  B. Selalu long + filter tren
  C. Dua arah + filter tren  <- hipotesis utama
  D. Dua arah tanpa filter (produksi saat ini)

Kalau filter benar theories, C harus mengalahkan A DAN D di semua
fold. Kalau tidak, drift memang tidak bisa dieksploitasi dan kita
tahu itu sekarang, bukan nanti.
"""
import sys
import math
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import bt
from bt import (load, backtest, split_by_position, Config, Series, _slice,
                COST_PER_SIDE, TAKER_FEE, _simulate_exit, ema_series, rsi_series)

data = load()
folds = split_by_position(data, folds=3)
LBL = ("TRAIN", "VALID", "TEST")


# ── Tambahkan filter tren ke Series ────────────────────────────────
def add_trend(s: Series, fast: int = 9, slow: int = 50) -> Series:
    """Tambah trend_up ke Series: 1 kalau harga di atas EMA lambat."""
    n = len(s)
    e_slow = ema_series(s.c, slow)
    trend = [1 if (s.c[i] > e_slow[i] and s.c[i] > 0) else 0 for i in range(n)]
    s.trend_up = trend
    return s


for s in data.values():
    add_trend(s)


# ── Backtest dengan entry mode ────────────────────────────────────
def run(d, mode, sl=0.0040, tp=0.0060, min_profit=0.0060,
        hold=900, risk=0.005, lev=10, reg=50, allow_short=True):
    """
    mode: 'long' | 'both' | 'both_regime' | 'long_regime'
    """
    equity = 10_000.0
    net = 0.0
    n = wins = 0
    win_sum = loss_sum = 0.0
    skipped = 0

    for sym, s in d.items():
        i = 100
        nb = len(s)
        while i < nb - 2:
            mom = s.mom21[i]
            trend = s.trend_up[i]

            if mode == "long":
                d_ = 1 if mom > 0 else 0
            elif mode == "both":
                d_ = 1 if mom > 0 else (-1 if mom < -0.15 else 0)
            elif mode == "long_regime":
                d_ = 1 if (mom > 0 and trend) else 0
            elif mode == "both_regime":
                if trend:
                    d_ = 1 if mom > 0 else 0
                else:
                    d_ = -1 if mom < -0.15 else 0
            else:
                d_ = 0

            if d_ == 0:
                i += 1
                skipped += 1
                continue

            j = i + 1
            entry = s.o[j]
            if entry <= 0:
                i += 1
                continue
            qty = (risk * equity * lev) / entry
            if qty <= 0:
                i += 1
                continue

            if d_ > 0:
                e_sl = entry * (1 - sl)
                e_tp = entry * (1 + tp)
            else:
                e_sl = entry * (1 + sl)
                e_tp = entry * (1 - tp)

            reason, ei = _simulate_exit(
                s, j, d_, e_sl, e_tp,
                Config(sl_pct=sl, tp_pct=tp, min_profit_pct=min_profit,
                       max_hold_s=hold, min_hold_s=15))
            if ei < 0:
                i += 1
                continue

            px = s.c[ei]
            ef = px * (1 - COST_PER_SIDE) if d_ > 0 else px * (1 + COST_PER_SIDE)
            gross = (ef - entry) * qty * d_
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
    wr = wins / n if n else 0.0
    t = 0.0
    return n, net, pf, wr, t


print("=" * 82)
print("FILTER REGIME — apakah asimetri long/short bisa dihilangkan?")
print("=" * 82)
print("  sl 0.40%  tp 0.60%  hold 900s  risk 0.5% lev 10x")
print("  17 bps round trip\n")
print(f"  {'mode':<14} {'':>2} {'n':>5} {'net':>9} {'pf':>7} {'wr':>6} {'per_tr':>9}")
print("  " + "-" * 60)

for mode, label in (("both", "D. dua arah"),
                    ("long", "A. selalu long"),
                    ("long_regime", "B. long+tren"),
                    ("both_regime", "C. dua arah+tren")):
    for k, lb in enumerate(LBL):
        n, net, pf, wr, _ = run(folds[k], mode)
        per = net / n if n else 0
        flag = "  <==HIPOTESIS UTAMA" if (mode == "both_regime" and k == 0) else ""
        print(f"  {label if k == 0 else '':<14} {lb:<2} {n:>5} {net:>+9.2f} "
              f"{pf:>7.3f} {wr*100:>5.1f}% {per:>+9.4f}{flag}")
    print()

print("=" * 82)
print("RINGKASAN per fold")
print("=" * 82)
print(f"  {'mode':<16} {'TRAIN':>9} {'VALID':>9} {'TEST':>9}   {'pf_test':>8}")
print("  " + "-" * 60)
res = {}
for mode, label in (("both", "dua arah (produksi)"),
                    ("long", "selalu long"),
                    ("long_regime", "long + tren"),
                    ("both_regime", "dua arah + tren")):
    pers = []
    pft = 0
    for k in range(3):
        n, net, pf, wr, _ = run(folds[k], mode)
        pers.append(net / n if n else 0)
        if k == 2:
            pft = pf
    res[label] = (pers, pft)
    print(f"  {label:<16} {pers[0]:>+9.4f} {pers[1]:>+9.4f} {pers[2]:>+9.4f}   {pft:>8.3f}")
print()

best = max(res.items(), key=lambda kv: kv[1][0][2])
print(f"  terbaik di TEST: {best[0]}")
print()
print("  kalau dua-arah+tren menang di ketiga fold, itu filter")
print("  yang bekerja. kalau tidak, drift tidak bisa dieksploitasi")
print("  dan kita perlu tahu itu sekarang.")
