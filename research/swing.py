"""
Temuan dari grid sebelumnya: 15 konfigurasi TERATAS di training
semuanya punya max_hold_seconds = 1800. Bukan 300, bukan 600 - 1800.

Dua bacaan yang mungkin:
  A) 30 menit memang horizon yang benar, dan scalp 5 menit memang
     salah untuk aset ini. Bot lu diberi parameter scalp ke data yang
     butuh swing.
  B) 1800 hanya menang karena Data_A punya tren naik panjang, jadi
    makin lama posisi terbuka, makin mungkin kena TP besar. Itu artefak
     drift, sama seperti yang ditemukan di momentum long.

Test yang membedakan: kalau (A), edge harus SURVIVE di validasi
dan test. Kalau (B), habis di fold berikutnya - persis seperti
semua kandidat lain.

Loop ini menguji (A) dengan benar: banyak konfigurasi horizon panjang,
semua di-validasi dengan cara yang sama.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bt import load, backtest, split_by_position, Config

data = load()
folds = split_by_position(data, folds=3)
TRAIN, VAL, TEST = folds[0], folds[1], folds[2]

ENTRIES = [
    ("mom_long", {"entry": "mom_long", "mom_thresh": 0.0}),
    ("ema",      {"entry": "ema"}),
    ("mom_both", {"entry": "mom_both", "mom_thresh": 0.15}),
]

# Horizon panjang, dengan SL/TP yang diskalakan proporsional.
# Untuk swing, SL 1% dan TP 2-3% adalah rasio normal.
SLS = [0.005, 0.007, 0.010, 0.015, 0.020]
TPS = [0.010, 0.015, 0.020, 0.030, 0.040, 0.060]
HOLDS = [900, 1800, 3600, 7200, 14400]

print("=" * 84)
print("HORIZON PANJANG — apakah 1800s bertahan di luar sampel?")
print("=" * 84)
print(f"  {len(ENTRIES)} x {len(SLS)} x {len(TPS)} x {len(HOLDS)} = "
      f"{len(ENTRIES)*len(SLS)*len(TPS)*len(HOLDS)} kandidat\n")

rows = []
for ename, ep in ENTRIES:
    for sl in SLS:
        for tp in TPS:
            if tp <= sl:
                continue
            for hold in HOLDS:
                cfg = Config(name="s", **ep, sl_pct=sl, tp_pct=tp,
                             min_profit_pct=None, max_hold_s=hold,
                             min_hold_s=min(60, hold // 20))
                r = backtest(TRAIN, cfg)
                if r.trades < 25:
                    continue
                rows.append((r.net / r.trades, r.pf, ename, ep, sl, tp, hold, r))

rows.sort(key=lambda x: -x[0])
print(f"{len(rows)} kandidat lolos\n")
print(f"  {'entry':<9} {'SL':>6} {'TP':>6} {'hold':>6} {'n':>5} {'per_tr':>9} {'pf':>6} {'wr':>6}")
for per, pf, ename, ep, sl, tp, hold, r in rows[:12]:
    print(f"  {ename:<9} {sl*100:>5.2f}% {tp*100:>5.2f}% {hold:>6} {r.trades:>5} "
          f"{per:>+8.4f} {pf:>6.3f} {r.winrate*100:>5.1f}%")
print()

print("=" * 84)
print("VALIDASI")
print("=" * 84)
print(f"  {'entry':<9} {'SL':>6} {'TP':>6} {'hold':>6} {'per_tr':>9} | "
      f"{'per_va':>9} {'pf_va':>6} | {'per_te':>9} {'pf_te':>6}  verdict")
print("  " + "-"*78)

survivors = []
for per, pf, ename, ep, sl, tp, hold, rtr in rows[:12]:
    cfg = Config(name="v", **ep, sl_pct=sl, tp_pct=tp,
                 min_profit_pct=None, max_hold_s=hold,
                 min_hold_s=min(60, hold // 20))
    rv = backtest(VAL, cfg)
    rt = backtest(TEST, cfg)
    per_va = rv.net/rv.trades if rv.trades else 0
    per_te = rt.net/rt.trades if rt.trades else 0
    ok = per_va > 0 and per_te > 0
    if ok:
        survivors.append((ename, ep, sl, tp, hold, per, pf, per_va, per_te, rv, rt))
    print(f"  {ename:<9} {sl*100:>5.2f}% {tp*100:>5.2f}% {hold:>6} {per:>+8.4f} | "
          f"{per_va:>+8.4f} {rv.pf:>6.3f} | {per_te:>+8.4f} {rt.pf:>6.3f}  "
          f"{'LOLOS' if ok else 'gagal'}")

print()
if survivors:
    print("  LOLOS DI KETIGA FOLD:")
    for s in survivors:
        print(f"    {s[0]}  SL {s[2]*100:.2f}%  TP {s[3]*100:.2f}%  hold {s[4]}s")
        print(f"      train {s[5]:+.4f}  valid {s[7]:+.4f}  test {s[8]:+.4f}")
        print(f"      test: n={s[10].trades} wr={s[10].winrate*100:.1f}% "
              f"maxDD={s[10].max_dd:.2f} net={s[10].net:+.2f}")
else:
    print("  12 teratas di train, nol yang positif di validasi DAN test.")
    print()
    print("  Kesimpulan: 1800s menang di train karena tren panjang, bukan")
    print("  karena horizon itu benar. Pola yang sama seperti momentum long.")
    print("  Bedanya hanya skalanya - dan skalanya berubah arah tiap")
    print("  pergantian rezim pasar, jadi tidak bisa dieksploitasi.")
