"""
TEMUAN: seluruh data ini bullish di ketiga fold.

  BTC   fold0 +5.40%   fold1 +4.27%   fold2 +0.26%
  ADA   fold0 +5.19%   fold1 +1.36%   fold2 -0.00%
  ARB   fold0 +3.15%   fold1 +4.43%   fold2 -3.64%

Artinya walk-forward yang selama ini gue jalankan TIDAK PERNAH
menguji apa yang terjadi saat pasar turun. Long-bias edge yang kita
temukan tumbuh seiring drift, dan di fold yang drift-nya paling
lemah (fold2) edge-nya paling tipis.

Kesimpulan yang menyakitkan tapi penting: long-only " profitable"
di sini berarti long-only di pasar naik. Kita belum tahu apakah
strategi ini rugi dua kali lebih besar di pasar turun, dan itu
persis yang akan menentukan apakah ia layak dipakai.

Yang bisa diuji tanpa data bear:
  1. Mirror test - balik semua candle (harga -> 1/harga) dan lihat
     apakah strateginya short. Kalau edge-nya asimetris ke bawah, itu konfirmasi.
  2. Short-only langsung - di data bullish, short harus rugi. Kalau
     ruginya PARUH dari long untungnya, simetri dan menarik.
  3. Robustness - apakah edge long hilang kalau dipotong 2x, atau
     hanya di DATANG dari drift?
"""
import sys
import math
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import bt
from bt import (load, split_by_position, Config, Series, _slice,
                _simulate_exit, ema_series, rsi_series)

data = load()
folds = split_by_position(data, folds=3)

SPREAD, IMPACT, TAKER = 0.0003, 0.0001, 0.00045
COST = SPREAD + IMPACT


def run(d, mode="long", mom_min=0.0, sl=0.0040, tp=0.0060, hold=900,
        risk=0.005, lev=10, fee=TAKER, cost=COST, trend_req=False):
    equity = 10_000.0
    gross_sum = net_sum = 0.0
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
                    i += 1; continue
                d_ = 1
            elif mode == "short":
                if mom >= -mom_min:
                    i += 1; continue
                d_ = -1
            else:
                d_ = 1 if mom > mom_min else (-1 if mom < -mom_min else 0)
                if d_ == 0:
                    i += 1; continue

            j = i + 1
            entry = s.o[j]
            if entry <= 0:
                i += 1; continue
            qty = (risk * equity * lev) / entry
            if d_ > 0:
                e_sl, e_tp = entry * (1 - sl), entry * (1 + tp)
            else:
                e_sl, e_tp = entry * (1 + sl), entry * (1 - tp)

            reason, ei = _simulate_exit(
                s, j, d_, e_sl, e_tp,
                Config(sl_pct=sl, tp_pct=tp, min_profit_pct=tp,
                       max_hold_s=hold, min_hold_s=15))
            if ei < 0:
                i += 1; continue
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
                wins += 1; win_sum += pnl
            else:
                loss_sum += -pnl
            i = ei + 1

    pf = (win_sum / loss_sum) if loss_sum > 0 else float("inf")
    return n, gross_sum, net_sum, pf, (wins / n if n else 0.0)


print("=" * 88)
print("1. SIMETRI: long vs short di data yang sama")
print("=" * 88)
print("  Kalau edge-nya benar-benar prediktif, long dan short harus")
print("  simetris. Kalau tidak, yang kita punya adalah bias arah.\n")
print(f"  {'fold':<7} {'mode':<7} {'n':>5} {'gross/tr':>10} {'net/tr':>9} {'pf':>7} {'wr':>6}")
print("  " + "-" * 62)
for k, lb in enumerate(("TRAIN", "VALID", "TEST")):
    for mode in ("long", "short"):
        n, g, net, pf, wr = run(folds[k], mode)
        print(f"  {lb:<7} {mode:<7} {n:>5} {g/n if n else 0:>+9.4f} "
              f"{net/n if n else 0:>+9.4f} {pf:>7.3f} {wr*100:>5.1f}%")
    print()

# ── Mirror: balik harga, jadi trend up jadi trend down ──────────────
print("=" * 88)
print("2. MIRROR TEST — balik candle, jadi 'bullish' jadi 'bearish'")
print("=" * 88)
print("  Kalau strategies benar-benar punya edge prediktif, edge-nya")
print("  harus ikut berbalik. Kalau TIDAK, edge itu cuma bias arah.\n")


def mirror(d):
    out = {}
    for sym, s in d.items():
        ns = _slice(s, 0, len(s))
        n = len(ns.c)
        ns.o = [1.0 / x if x > 0 else 0.0 for x in s.o]
        ns.h = [1.0 / x if x > 0 else 0.0 for x in s.l]   # high <- low
        ns.l = [1.0 / x if x > 0 else 0.0 for x in s.h]   # low  <- high
        ns.c = [1.0 / x if x > 0 else 0.0 for x in s.c]
        ns.v = list(s.v)
        ns.rsi14 = [None if r is None else 100.0 - r for r in s.rsi14]
        ns.atr14 = list(s.atr14)
        # EMA dan momentum harus dihitung ulang pada harga baru
        e9, e21 = ema_series(ns.c, 9), ema_series(ns.c, 21)
        ns.ema9, ns.ema21 = e9, e21
        ns.mom21 = [0.0] * n
        for i in range(21, n):
            if ns.c[i - 21] > 0:
                ns.mom21[i] = (ns.c[i] - ns.c[i - 21]) / ns.c[i - 21] * 100.0
        ns.trend_up = [1 if (ns.c[i] > e21[i] and ns.c[i] > 0) else 0
                       for i in range(n)]
        out[sym] = ns
    return out


mir = [mirror(folds[k]) for k in range(3)]
print(f"  {'fold':<7} {'asli':<16} {'mirror':<16} {'ratio':>7}")
print("  " + "-" * 50)
for k, lb in enumerate(("TRAIN", "VALID", "TEST")):
    n1, g1, net1, pf1, _ = run(folds[k], "long")
    n2, g2, net2, pf2, _ = run(mir[k], "long")
    g1t = g1 / n1 if n1 else 0
    g2t = g2 / n2 if n2 else 0
    ratio = (g2t / g1t) if g1t != 0 else 0
    print(f"  {lb:<7} gross {g1t:>+8.4f}   gross {g2t:>+8.4f}   {ratio:>6.2f}x")
print()
print("  ratio ~ -1.0  = edge benar-benar simetris (prediktif)")
print("  ratio ~  0.0  = edge hilang di mirror (cuma bias arah, drift)")
print("  ratio >  0    = edge hanya di satu arah")
print()

# ── Robustness: short-only di data bullish ──────────────────────────
print("=" * 88)
print("3. ROBUSTNESS — short-only di data bullish harus rugi, tapi")
print("   SEBERAPA RUGI? Kalau ruginya PARUH dari long untungnya,")
print("   berarti simetris dan alpha-nya nyata.")
print("=" * 88)
print()
for k, lb in enumerate(("TRAIN", "VALID", "TEST")):
    nl, gl, netl, pfl, wrl = run(folds[k], "long")
    ns_, gs, nets, pfs, wrs = run(folds[k], "short")
    lt = gl / nl if nl else 0
    st = gs / ns_ if ns_ else 0
    print(f"  {lb:<7} long {lt:>+8.4f}   short {st:>+8.4f}   "
          f"rasio short/long = {st/lt if lt else 0:>6.2f}")
print()
print("  Rasio -1.0 = long untung dan short rugi sama besarnya")
print("  Rasio  0.0 = dua-duanya rugi (tidak ada alpha sama sekali)")
print("  Rasio >-1.0 = long untung LEBIH BESAR dari short rugi")
print("              (bias arah, dan itu drift)")
