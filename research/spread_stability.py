"""
Apakah spread yang terukur STABIL, atau itu hanya kebetulan sesaat?

Penting karena: kalau spread naik-turun mengikuti volatilitas, maka
konstanta 3 bps di `fill_cost` bukan sekadar konservatif - dia salah
secara sistematis, karena mengasumsikan spread tetap pada kondisi
paling likuid. Dan kalau spread ternyata MENGIKUTI volatilitas,
perhitungan biaya yang benar harus memakai spread real-time, bukan
konstanta.

Yang diukur:
  1. Distribusi spread per simbol (bukan rata-rata).
  2. Korelasi spread dengan realized volatility dari candle 1m.
  3. Spread di menit yang PALING LIKUID vs paling bergejolak.

Kalau (2) kuat, spread adalah variabel - dan `fill_cost` yang
memakai konstanta perlu diubah.
"""
import sqlite3
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

OB = "data_store/order_book.db"
MAIN = "data_store/trading_bot.db"

ob = sqlite3.connect(OB)
ob.row_factory = sqlite3.Row
main = sqlite3.connect(MAIN)
main.row_factory = sqlite3.Row

print("=" * 76)
print("1. DISTRIBUSI SPREAD per simbol (dalam bps)")
print("=" * 76)
print(f"  {'sym':<8} {'n':>6} {'p5':>7} {'p25':>7} {'median':>8} "
      f"{'p75':>7} {'p95':>7} {'max':>7}")
print("  " + "-" * 70)

syms = [r[0] for r in ob.execute(
    "SELECT DISTINCT symbol FROM order_book_features")]
summary = {}
for sym in syms:
    vals = [r[0] * 10000 for r in ob.execute(
        "SELECT spread_pct FROM order_book_features WHERE symbol=? "
        "ORDER BY spread_pct", (sym,))]
    if len(vals) < 100:
        continue
    n = len(vals)
    q = lambda p: vals[int(n * p)]
    print(f"  {sym.split('/')[0]:<8} {n:>6} {q(.05):>7.2f} {q(.25):>7.2f} "
          f"{q(.5):>8.2f} {q(.75):>7.2f} {q(.95):>7.2f} {vals[-1]:>7.2f}")
    summary[sym] = (q(.5), q(.95), q(.05))

print()
print("  fill_cost memakai 3.00 bps KONSTAN untuk semua simbol.")
print("  Bandingkan ke p95 - kalau p95 jauh di bawah 3, konstanta itu")
print("  konservatif; kalau p95 di atas 3, konstanta itu terlalu optimistic.")
print()
over = [(s.split('/')[0], v[1]) for s, v in summary.items() if v[1] > 3.0]
if over:
    print("  simbol yang p95NYA di atas 3 bps:")
    for s, v in over:
        print(f"    {s:<8} p95 {v:>6.2f} bps")
else:
    print("  TIDAK ADA simbol yang p95-nya di atas 3 bps.")
    print("  >> Konstanta 3 bps NEVER/sample ini konservatif di semua sisi.")
print()

# ── Korelasi dengan volatilitas ──────────────────────────────────────
print("=" * 76)
print("2. KORELASI spread dengan realized volatility")
print("=" * 76)
print("  Kalau spread mengikuti volatilitas, korelasinya positif dan")
print("  Artifact. Kalau tidak, spread didominasi struktur spread")
print("  (tick size, likuiditas statis), bukan kondisi pasar.")
print()

# Ambil ATR-like dari candle 1m
atr = {}
for r in main.execute("""
    SELECT symbol,
           AVG(ABS(high - low)) / AVG(NULLIF(close, 0)) * 100.0 AS rng
    FROM candles WHERE timeframe = '1m' GROUP BY symbol
"""):
    if r[1] is not None:
        atr[r[0]] = r[1]

def pearson(xs, ys):
    if len(xs) < 3:
        return 0.0
    mx, my = statistics.mean(xs), statistics.mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = sum((x - mx) ** 2 for x in xs) ** 0.5
    dy = sum((y - my) ** 2 for y in ys) ** 0.5
    return num / (dx * dy) if dx and dy else 0.0

print(f"  {'sym':<8} {'n':>6} {'corr(spread,range)':>20}")
print("  " + "-" * 40)
for sym in syms:
    a = atr.get(sym)
    if a is None:
        continue
    rows = ob.execute(
        "SELECT spread_pct FROM order_book_features WHERE symbol=?",
        (sym,)).fetchall()
    if len(rows) < 100:
        continue
    sp = [r[0] * 10000 for r in rows]
    # Bandingkan dengan range candle sebagai proxy volatilitas
    rng = [a] * len(sp)
    print(f"  {sym.split('/')[0]:<8} {len(sp):>6} {pearson(sp, rng):>20.4f}")
print()
print("  (candle range adalah rata-rata periode, jadi korelasi")
print("   di sini mengukur apakah spread di simbol yang bergejolak juga")
print("   lebih lebar - proxy kasar, tapi cukup untuk melihat arah.)")
print()

# ── Spread di bucket volatilitas ─────────────────────────────────────
print("=" * 76)
print("3. SPREAD di bucket volatilitas")
print("=" * 76)
print("  Kalau spread naik dengan volatilitas, Artifact: biaya bukan")
print("  konstanta, dan fill_cost perlu memakai spread real-time.")
print()
ranks = sorted(atr.items(), key=lambda kv: kv[1] or 0)
half = len(ranks) // 2
low_vol = [s for s, _ in ranks[:half]]
high_vol = [s for s, _ in ranks[half:]]
print(f"  {'bucket':<12} {'sym':<22} {'median bps':>11}")
print("  " + "-" * 48)
for label, group in (("rendah", low_vol), ("tinggi", high_vol)):
    for sym in group[:5]:
        vals = [r[0] * 10000 for r in ob.execute(
            "SELECT spread_pct FROM order_book_features WHERE symbol=?",
            (sym,))]
        if len(vals) < 50:
            continue
        print(f"  {label:<12} {sym.split('/')[0]:<22} "
              f"{statistics.median(vals):>11.2f}")
print()

lo_all, hi_all = [], []
for s in low_vol:
    lo_all += [r[0]*10000 for r in ob.execute(
        "SELECT spread_pct FROM order_book_features WHERE symbol=?", (s,))]
for s in high_vol:
    hi_all += [r[0]*10000 for r in ob.execute(
        "SELECT spread_pct FROM order_book_features WHERE symbol=?", (s,))]
if lo_all and hi_all:
    print(f"  median spread volatilitas RENDAH : {statistics.median(lo_all):.2f} bps")
    print(f"  median spread volatilitas TINGGI : {statistics.median(hi_all):.2f} bps")
    ratio = statistics.median(hi_all) / max(1e-9, statistics.median(lo_all))
    print(f"  rasio                          : {ratio:.2f}x")
    print()
    if ratio > 1.3:
        print("  >> Spread BERKORELASI dengan volatilitas. 3 bps konstan")
        print("     akan terlalu optimistic saat pasar bergejolak - dan")
        print("     justru saat spread paling mahal, itulah saat losses")
        print("     paling besar.")
    elif ratio > 1.1:
        print("  >> Ada korelasi lemah. 3 bps masuk akal tapi bukan optimal.")
    else:
        print("  >> Spread relatif DATAR terhadap volatilitas. Konstanta")
        print("     3 bps mungkin terlalu besar tapi tidak")
        print("     berbahaya.")
