"""
Walk-forward SEJATI: fold sejajar waktu absolut, bukan per-simbol.

search2.py dan swing.py membagi POSISI dalam tiap simbol. Itu keliru
dengan cara yang penting: simbol-symbol punya tanggal mulai berbeda
(BTC mulai 17 Sep, ENA mulai 20 Sep), jadi "sepertiga pertama" BTC
adalah 17-19 Sep sementara "sepertiga pertama" ENA adalah 20-21 Sep.
Setiap fold berisi periode pasar yang BERBEDA untuk tiap simbol, dan
validasi kehilangan integrinya.

Di sini fold ditentukan oleh timestamp absolut, dan hanya simbol yang
memiliki data di semua fold yang dipakai. Itu lebih sedikit sampel,
tapi setiap fold benar-benar periode yang berbeda.
"""
import sys
import math
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bt import load, backtest, split_by_position, Config, Series, _slice

data = load()

# ── Cari simbol dengan cakupan penuh, pakai timestamp absolut ──────
all_ts = sorted({s.ts[i] for s in data.values() for i in range(len(s))})
LO, HI = all_ts[0], all_ts[-1]
SPAN = (HI - LO) / 3
BOUNDS = [(int(LO + SPAN * k), int(LO + SPAN * (k + 1))) for k in range(3)]

full = {}
for sym, s in data.items():
    lo, hi = min(s.ts), max(s.ts)
    if lo > BOUNDS[0][0] + 3600_000:
        continue                      # mulai terlalu lambat
    if hi < BOUNDS[2][1] - 3600_000:
        continue                      # berakhir terlalu cepat
    if len(s) < 900:
        continue
    full[sym] = s

print("=" * 84)
print("SYMBOLS DENGAN CAKUPAN PENUH (timestamp absolut)")
print("=" * 84)
print(f"  {len(full)} dari {len(data)} simbol lolos")
for sym, s in sorted(full.items()):
    ts = sorted(s.ts)
    cov = [sum(1 for t in ts if a <= t < b) for a, b in BOUNDS]
    print(f"    {sym.split('/')[0]:<8} total {len(s):>5}  "
          f"per fold: {cov[0]:>5} {cov[1]:>5} {cov[2]:>5}")
print()
for k, (a, b) in enumerate(BOUNDS):
    import datetime as dt
    print(f"  fold{k}: {dt.datetime.fromtimestamp(a/1000)} .. "
          f"{dt.datetime.fromtimestamp(b/1000)}")
print()

FULL_COVERAGE = len(full) >= 3

if not FULL_COVERAGE:
    print("  Walk-forward TEMPORAL tidak mungkin di data ini.")
    print("  Bukan karena metodanya: hanya 2 dari 20 simbol punya")
    print("  data di ketiga fold, dan fold1 kosong total karena jeda")
    print("  4 HARI di tengah (20 Sep -> 24 Sep).")
    print()
    print("  Konsekuensi: analisis di bawah memakai split per-simbol")
    print("  (search2.py), di mana setiap simbol mewarisi proporsi")
    print("  train/valid/test yang sama. Itu BUKAN waktu yang sama")
    print("  untuk tiap simbol - dan itu kelemahan nyata yang membuat")
    print("  semua angka di sini dibaca sebagai indikasi, bukan bukti.")
    print()
    import bt as _bt
    _F = _bt.split_by_position(load(), folds=3)
    F = [_F[0], _F[1], _F[2]]
    print("  fallback: split per-simbol")
    for k, label in ((0, "TRAIN"), (1, "VALIDASI"), (2, "TEST")):
        print(f"    {label:9} {len(F[k]):>2} simbol  "
              f"{sum(len(s) for s in F[k].values()):>6} bar")
    print()


if FULL_COVERAGE:
    def fold_data(k):
        a, b = BOUNDS[k]
        out = {}
        for sym, s in full.items():
            idx = [i for i, t in enumerate(s.ts) if a <= t < b]
            if len(idx) < 60:
                continue
            out[sym] = _slice(s, idx[0], idx[-1] + 1)
        return out

    F = [fold_data(k) for k in range(3)]
    for k, label in ((0, "TRAIN"), (1, "VALIDASI"), (2, "TEST")):
        print(f"  {label:9} {len(F[k]):>2} simbol  "
              f"{sum(len(s) for s in F[k].values()):>6} bar")
    print()


def per(r):
    return r.net / r.trades if r.trades else 0.0


def scan(name, ep, **xp):
    cfg = Config(name="q", **ep, **xp)
    rs = [backtest(f, cfg) for f in F]
    return name, rs, [per(r) for r in rs]


# ════════════════════════════════════════════════════════════════════
print("=" * 84)
print("A. KONFIGURASI SEKARANG vs YANG DIPERBAIKI")
print("=" * 84)
print("""
Config sekarang punya tiga cacat yang terukur, bukan tebakan:

  1. tight_sl_pct 0.25% TIDAK PERNAH dipakai. 118 dari 123 SL_HIT
     punya jarak 0.35-0.45% karena dynamic TP/SL (ATR) menimpanya.
  2. min_profit_pct = take_profit = 0.60%, tapi median hold 46 detik.
     88% winner ditutup di ~0.39%. Target tidak pernah tercapai.
  3. Akibatnya risk:reward efektif 1:1, bukan 1:2.4 seperti di config.

Diuji: apakah konfigurasi yang JUJUR (SL 0.40%, TP terpisah dari
min_profit) lebih baik dari yang sekarang.
""")

CASES = [
    ("SEKARANG (replika produksi)",
     {"entry": "mom_long", "mom_thresh": 0.0},
     {"sl_pct": 0.0037, "tp_pct": 0.0060, "min_profit_pct": 0.0060,
      "max_hold_s": 300, "min_hold_s": 15}),

    ("SL realistis 0.40%, early-exit 0.30%",
     {"entry": "mom_long", "mom_thresh": 0.0},
     {"sl_pct": 0.0040, "tp_pct": 0.0060, "min_profit_pct": 0.0030,
      "max_hold_s": 300, "min_hold_s": 15}),

    ("SL 0.40%, TP 0.60%, TANPA early-exit",
     {"entry": "mom_long", "mom_thresh": 0.0},
     {"sl_pct": 0.0040, "tp_pct": 0.0060, "min_profit_pct": None,
      "max_hold_s": 300, "min_hold_s": 15}),

    ("SL 0.50%, TP 0.50%, tanpa early-exit",
     {"entry": "mom_long", "mom_thresh": 0.0},
     {"sl_pct": 0.0050, "tp_pct": 0.0050, "min_profit_pct": None,
      "max_hold_s": 600, "min_hold_s": 15}),

    ("SL 1.0%, TP 1.5%, swing 2 jam",
     {"entry": "mom_long", "mom_thresh": 0.0},
     {"sl_pct": 0.0100, "tp_pct": 0.0150, "min_profit_pct": None,
      "max_hold_s": 7200, "min_hold_s": 60}),

    ("SL 1.5%, TP 3.0%, swing 4 jam",
     {"entry": "mom_long", "mom_thresh": 0.0},
     {"sl_pct": 0.0150, "tp_pct": 0.0300, "min_profit_pct": None,
      "max_hold_s": 14400, "min_hold_s": 60}),
]

print(f"  {'konfigurasi':<34} {'tr':>9} {'va':>9} {'te':>9}   {'pf_te':>6} {'n_te':>5}")
print("  " + "-" * 80)
for name, ep, xp in CASES:
    nm, rs, pers = scan(name, ep, **xp)
    rt = rs[2]
    print(f"  {name:<34} {pers[0]:>+8.3f} {pers[1]:>+8.3f} {pers[2]:>+8.3f}   "
          f"{rt.pf:>6.3f} {rt.trades:>5}")

# ════════════════════════════════════════════════════════════════════
print()
print("=" * 84)
print("B. MAKER FEE — satu-satunya perubahan yang bisa mengubah matematika")
print("=" * 84)
print("""
Taker round trip = 17 bps. Maker = 3 bps (0.015% x 2). Selisihnya
14 bps per trade, dan itu 2.4x jarak stop produksi.

TAPI maker tidak gratis: limit pasif hanya terisi saat harga bergerak
MENUJU kamu, yangmostly berarti movement yang akan melawannya. Itu
adverse selection, dan mengabaikannya membuat maker tampak 2x lebih
baik daripada kenyataan.

Diuji dengan model fills berbeda - 100% selalu terisi (tidak
realistis, jadi batas atas), lalu turun ke 60% dan 40%.
""")

import bt


def maker_backtest(d, fill_rate, seed=11):
    """Backtest dengan asumsi order pasif terisi hanya 'fill_rate' kali."""
    import random
    rnd = random.Random(seed)
    equity = 10_000.0
    net = 0.0
    n = wins = 0
    win_sum = loss_sum = 0.0
    SPREAD_ONLY = 0.0003 * 2      # 3 bps half-spread, di kedua sisi
    MAKER_FEE = 0.00015

    for sym, s in d.items():
        i = 30
        nb = len(s)
        while i < nb - 2:
            direction = 1 if s.mom21[i] > 0 else 0
            if direction == 0:
                i += 1
                continue
            # Adverse selection: order pasif sering TIDAK terisi.
            if rnd.random() > fill_rate:
                i += 1
                continue
            j = i + 1
            entry = s.o[j]
            if entry <= 0:
                i += 1
                continue
            margin = 0.005 * 10_000.0
            qty = margin / entry
            sl = entry * (1 - 0.0040)
            tp = entry * (1 + 0.0060)
            reason, ei = bt._simulate_exit(
                s, j, direction, sl, tp,
                Config(sl_pct=0.004, tp_pct=0.006, max_hold_s=300, min_hold_s=15))
            if ei < 0:
                i += 1
                continue
            px = s.c[ei]
            # maker: hanya membayar spread, tidak membayar taker fee
            ef = px * (1 - SPREAD_ONLY)
            gross = (ef - entry) * qty
            fees = (entry + ef) * qty * MAKER_FEE
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
    return n, net, pf, wins / n if n else 0.0


print(f"  {'fill rate':<12} {'n_tr':>5} {'per_tr':>9} {'pf':>7} {'wr':>6}  catatan")
print("  " + "-" * 68)
for fr in (1.00, 0.80, 0.60, 0.40, 0.25):
    n, net, pf, wr = maker_backtest(F[2], fr)
    note = "tidak realistis" if fr == 1.00 else (
        "sangat rewardtif" if fr <= 0.5 else "")
    print(f"  {fr*100:>10.0f}%  {n:>5} {net/n if n else 0:>+8.3f} {pf:>7.3f} "
          f"{wr*100:>5.1f}%  {note}")
