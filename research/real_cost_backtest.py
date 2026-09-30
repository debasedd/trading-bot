"""
Backtest ulang dengan spread TERUKUR, bukan asumsi 3 bps.

Seluruh riset sebelumnya memakai `FILL_HALF_SPREAD_FLOOR = 0.0003`
untuk semua simbol. Pengukuran order book historis (research/
spread_stability.py) menunjukkan spread median antar simbol beda 16x:
0.12 bps (BTC) sampai 1.97 bps (ENA).

Jadi backtest lama menganggap biaya 8.5 bps per sisi untuk BTC yang
sebenarnya 5.6 - dan 8.5 untuk ENA yang sebenarnya 7.5.

Pertanyaannya sederhana: apakah edge yang ada (gross +0.18 per trade
di test fold) cukup untuk impas dengan biaya yang sebenarnya?

Metodologi sama seperti search2.py: walk-forward, per-simbol split,
SL/TP diisi. Yang berubah HANYA angka biayanya - supaya selisihnya
men attributable ke spread, bukan ke segalanya.
"""
import math
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import bt
from bt import (load, split_by_position, Config, Series, _slice,
                _simulate_exit, ema_series)

# Biaya terukur per simbol (dari trading/fill_cost.py)
sys.path.insert(0, str(Path(__file__).parent.parent))
from trading.fill_cost import (
    FILL_HALF_SPREAD_FLOOR_BY_SYMBOL,
    FILL_IMPACT_FLOOR,
)

TAKER = 0.00045
OLD_HALF = 0.0003          # asumsi lama, semua simbol

data = load()
folds = split_by_position(data, folds=3)
LBL = ("TRAIN", "VALID", "TEST")


def cost_for(symbol, mode):
    """Biaya per sisi dalam fraksi."""
    base = symbol.split("/")[0].split(":")[0].upper()
    if mode == "old":
        half = OLD_HALF
    else:
        half = FILL_HALF_SPREAD_FLOOR_BY_SYMBOL.get(base, OLD_HALF)
    return half + FILL_IMPACT_FLOOR + TAKER


def run(d, mode, sl=0.0040, tp=0.0060, hold=900, risk=0.005, lev=10,
        mom_min=0.0, trend_req=False):
    equity = 10_000.0
    net = 0.0
    n = wins = 0
    win_sum = loss_sum = 0.0

    for sym, s in d.items():
        cost = cost_for(sym, mode)
        i = 100
        nb = len(s)
        while i < nb - 2:
            if trend_req and not s.trend_up[i]:
                i += 1
                continue
            if s.mom21[i] <= mom_min:
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
            ef = px * (1 - cost)
            gross = (ef - entry) * qty
            fees = (entry + ef) * qty * TAKER
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


print("=" * 78)
print("BACKTEST DENGAN BIAYA TERUKUR")
print("=" * 78)
print("  spread lama : 3.0 bps + 1.0 impact + 4.5 fee = 8.5 bps per sisi")
print("  spread baru : 0.12-1.97 bps + 1.0 impact + 4.5 fee")
print("               = 5.62 (BTC) s/d 7.47 (ENA) bps per sisi")
print()

CONFIGS = [
    ("sl0.40 tp0.60 h900", dict(sl=0.0040, tp=0.0060, hold=900)),
    ("sl0.40 tp0.60 h1800", dict(sl=0.0040, tp=0.0060, hold=1800)),
    ("sl0.30 tp0.45 h900", dict(sl=0.0030, tp=0.0045, hold=900)),
    ("sl0.30 tp0.45 h1800", dict(sl=0.0030, tp=0.0045, hold=1800)),
    ("sl0.50 tp0.80 h1800", dict(sl=0.0050, tp=0.0080, hold=1800)),
    ("sl0.40 tp0.60 h900 tren", dict(sl=0.0040, tp=0.0060, hold=900,
                                      trend_req=True)),
]

print(f"  {'konfigurasi':<26} {'biaya':<8} {'tr':>9} {'va':>9} {'te':>9}  {'pf_te':>7}")
print("  " + "-" * 76)
for name, cfgp in CONFIGS:
    for mode, label in (("old", "lama"), ("new", "TERUKUR")):
        rs = [run(folds[k], mode, **cfgp) for k in range(3)]
        pers = [net / n if n else 0.0 for n, net, _, _ in rs]
        print(f"  {name:<26} {label:<8} {pers[0]:>+8.4f} {pers[1]:>+8.4f} "
              f"{pers[2]:>+8.4f}  {rs[2][2]:>7.3f}")
    print()

print("=" * 78)
print("APA KALAU MAKER? (taker 1.5 bps + spread terukur)")
print("=" * 78)
print("  maker realistis hanya mengisi ~40% waktu; sisanya bot kehilangan")
print("  trade. Angka di bawah assumes 100% fill - itu BATAS ATAS, bukan")
print("  perkiraan. Jangan dipakai sebagai dasar keputusan.\n")
print(f"  {'konfigurasi':<26} {'pf_te taker':>12} {'pf_te maker':>12}")
print("  " + "-" * 52)
for name, cfgp in CONFIGS[:4]:
    rt = run(folds[2], "new", **cfgp)
    # maker: same but fee 1.5 bps and NO extra impact
    print(f"  {name:<26} {rt[2]:>12.3f} {'(tidak diuji)':>12}")
