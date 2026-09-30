"""
Apakah OFI (Order Flow Imbalance) memprediksi pergerakan harga?

Ini pertanyaan yang menjawab seluruh rencana. Kalau order flow tidak
memprediksi apa pun, maka:
  * Model microstructure tidak akan menyelamatkan edge yang hilang.
  * Edge itu benar-benar tidak ada, dan yang tersisa hanya biaya.
  * Waktu yang dihabiskan mengumpulkan data ini terbuang.

Kalau OFI memprediksi, ada sinyal yang BUKAN time-series, dan itu
satu-satunya jalan keluar dari temuan research/bear.py (yang membuktikan
edge time-series hanya drift).

Metodologi:
  1. Join fitur order book dengan candle 1m per simbol.
  2. Bucket snapshot per menit, supaya OFI dari beberapa detik dalam
     menit yang sama digabung. Tanpa itu, OFI di detik ke-3
     dibandingkan dengan harga yang sudah bergerak 3 detik --
     look-ahead palsu.
  3. Korelasikan OFI dengan return ke depan.
  4. Bucket kuartil: OFI tinggi -> return positif?
  5. Split kronologis di akhir, supaya kelihatan apakah ini hanya
     bekerja di separuh pertama data.
"""
import math
import sqlite3
import statistics

OB_DB = "data_store/order_book.db"
MAIN_DB = "data_store/trading_bot.db"

ob = sqlite3.connect(OB_DB)
ob.row_factory = sqlite3.Row
main = sqlite3.connect(MAIN_DB)
main.row_factory = sqlite3.Row

# ── Candle 1m: simbol -> [(menit, open, high, low, close)] ─────────
candles = {}
for r in main.execute(
    "SELECT symbol, timestamp, open, high, low, close FROM candles "
    "WHERE timeframe='1m' ORDER BY symbol, timestamp"
):
    candles.setdefault(r[0], []).append(
        (r[1] // 60000, r[2], r[3], r[4], r[5])
    )

ob_counts = {r[0]: r[1] for r in ob.execute(
    "SELECT symbol, COUNT(*) FROM order_book_features GROUP BY symbol"
)}

SYM = [s for s in candles
       if ob_counts.get(s, 0) > 500 and len(candles[s]) > 100]

print("=" * 78)
print("DATA")
print("=" * 78)
print(f"  simbol: {len(SYM)}")
for s in SYM:
    print(f"    {s.split('/')[0]:<8} ob={ob_counts[s]:>6}  "
          f"candle={len(candles[s]):>6}")
print()

# ── Bucket OFI per (simbol, menit) ────────────────────────────────
# bucket = [ofi_sum, top_sum, depth_sum, mid_sum, n]
buckets = {}
for r in ob.execute(
    "SELECT symbol, timestamp, ofi, top_imbalance, depth_imbalance, "
    "mid_price FROM order_book_features WHERE ofi IS NOT NULL"
):
    key = (r[0], r[1] // 60000)
    b = buckets.get(key)
    if b is None:
        buckets[key] = [r[2], r[3], r[4], r[5], 1]
    else:
        b[0] += r[2]
        b[1] += r[3]
        b[2] += r[4]
        b[3] += r[5]
        b[4] += 1

print(f"  {len(buckets)} (simbol, menit) bucket")
print()


def pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return 0.0
    mx, my = statistics.mean(xs), statistics.mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return num / (dx * dy) if dx and dy else 0.0


def series_for(sym, horizon_min):
    """Kembalikan (ofi, top, depth, ret) untuk satu simbol."""
    cl = candles[sym]
    idx = {c[0]: i for i, c in enumerate(cl)}
    ofi_l, top_l, dep_l, ret_l = [], [], [], []
    for (s, minute), b in buckets.items():
        if s != sym:
            continue
        i0 = idx.get(minute)
        if i0 is None:
            continue
        i1 = idx.get(minute + horizon_min)
        if i1 is None or i1 <= i0:
            continue
        n = b[4]
        ofi_l.append(b[0] / n)
        top_l.append(b[1] / n)
        dep_l.append(b[2] / n)
        ret_l.append((cl[i1][4] - cl[i0][4]) / cl[i0][4] * 100.0)
    return ofi_l, top_l, dep_l, ret_l


print("=" * 78)
print("1. KORELASI dengan return ke depan")
print("=" * 78)
for hz in (1, 2, 5):
    print(f"\n  --- horizon {hz} menit ---")
    print(f"    {'sym':<8} {'n':>6} {'corr(OFI)':>11} {'corr(top)':>11} "
          f"{'corr(depth)':>13}")
    print("    " + "-" * 54)
    for sym in SYM:
        ofi, top, dep, ret = series_for(sym, hz)
        if len(ret) < 15:
            continue
        print(f"    {sym.split('/')[0]:<8} {len(ret):>6} {pearson(ofi, ret):>11.4f} "
              f"{pearson(top, ret):>11.4f} {pearson(dep, ret):>13.4f}")

print()
print("=" * 78)
print("2. KUARTIL: OFI tinggi -> return positif?")
print("=" * 78)
for hz in (1, 2):
    print(f"\n  --- horizon {hz} menit ---")
    for sym in SYM[:5]:
        ofi, top, dep, ret = series_for(sym, hz)
        if len(ret) < 30:
            continue
        pts = sorted(zip(ofi, ret))
        q = len(pts) // 4
        print(f"    {sym.split('/')[0]}:")
        for label, sel in (("Q1 rendah ", pts[:q]),
                           ("Q2       ", pts[q:2 * q]),
                           ("Q3       ", pts[2 * q:3 * q]),
                           ("Q4 tinggi", pts[3 * q:])):
            if not sel:
                continue
            rets = [p[1] for p in sel]
            m = statistics.mean(rets)
            sd = statistics.stdev(rets) if len(rets) > 1 else 0
            t = m / (sd / math.sqrt(len(rets))) if sd else 0.0
            wr = sum(1 for r in rets if r > 0) / len(rets)
            print(f"      {label}  n={len(rets):>4}  ret {m:+.4f}%  "
                  f"wr {wr * 100:>5.1f}%  t {t:>+6.2f}")
