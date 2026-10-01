"""
Mirror test untuk OFI - apakah edge Q1 itu alpha atau drift?

Temuan sebelumnya (research/reversal_test.py): dengan 24 jam data,
long di kuartil OFI RENDAH menghasilkan +0.0121% per trade setelah
biaya terukur (t = +3.72), sementara Q2/Q3/Q4 semuanya negatif.

Yang belum dijawab: apakah itu mean reversion (alpha) atau hanya
"long di pasar naik" (drift).

Semua kandidat time-series sebelumnya gagal di sini, dan sekarang
`candles` dan `order_book_features` sudah overlay 1.484 menit, jadi
tes ini BISA dijalankan untuk pertama kalinya pada data order book.

Cara kerja: balik harga menjadi 1/harga, jadi bullish jadi bearish,
lalu jalankan strategi yang sama di data yang dibalik. Kalau Q1
masih positif di sana, itu alpha.
"""
import math
import sqlite3
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

TAKER = 0.00045
IMPACT = 0.0001
FLOOR = {
    "BTC": 0.000012, "HYPE": 0.000012, "ETH": 0.000037,
    "XRP": 0.000066, "ZEC": 0.000070, "SOL": 0.000084,
    "NEAR": 0.000113, "LIT": 0.000128, "PUMP": 0.000175,
    "ENA": 0.000197,
}

ob = sqlite3.connect("data_store/order_book.db")
ob.row_factory = sqlite3.Row
mn = sqlite3.connect("data_store/trading_bot.db")
mn.row_factory = sqlite3.Row


def load_candles():
    d = {}
    for r in mn.execute(
        "SELECT symbol, timestamp, close FROM candles "
        "WHERE timeframe='1m' ORDER BY symbol, timestamp"
    ):
        d.setdefault(r[0], []).append((r[1] // 60000, r[2]))
    return d


def load_buckets():
    b = {}
    for r in ob.execute(
        "SELECT symbol, timestamp, ofi FROM order_book_features "
        "WHERE ofi IS NOT NULL"
    ):
        k = (r[0], r[1] // 60000)
        v = b.setdefault(k, [0.0, 0])
        v[0] += r[1] or 0.0
        v[1] += 1
    return b


def mirror(cand):
    """Balik harga: bullish -> bearish."""
    out = {}
    for sym, rows in cand.items():
        out[sym] = [(m, (1.0 / c if c > 0 else 0.0)) for m, c in rows]
    return out


def quartile_test(cand, buckets, label):
    """Long di Q1 (OFI terendah), dengan biaya terukur."""
    pts = []
    for sym, rows in cand.items():
        idx = {m: i for i, (m, _) in enumerate(rows)}
        mins = [k[1] for k in buckets if k[0] == sym and k[1] in idx]
        if len(mins) < 60:
            continue
        for m in mins:
            b = buckets[(sym, m)]
            ofi = b[0] / b[1]
            i0 = idx.get(m)
            i1 = idx.get(m + 5)
            if i0 is None or i1 is None or i1 <= i0:
                continue
            e = rows[i0][1]
            x = rows[i1][1]
            if e <= 0 or x <= 0:
                continue
            pts.append((sym, ofi, e, x))
    if len(pts) < 200:
        print(f"  {label}: hanya {len(pts)} titik, tidak cukup")
        return None

    pts.sort(key=lambda p: p[1])
    Q = len(pts) // 4
    out = []
    for i in range(4):
        sel = pts[i * Q:(i + 1) * Q]
        rets = []
        for sym, ofi, e, x in sel:
            cost = FLOOR.get(sym.split("/")[0], 0.0003) + IMPACT + TAKER
            q = 50.0 / e
            p = (x * (1 - cost) - e) * q - (e + x * (1 - cost)) * q * TAKER
            rets.append(p / 50.0 * 100.0)
        m_ = statistics.mean(rets)
        sd = statistics.stdev(rets) if len(rets) > 1 else 0
        t = m_ / (sd / math.sqrt(len(rets))) if sd else 0
        out.append((m_, t, sum(1 for r in rets if r > 0) / len(rets)))
    return out


cand = load_candles()
buckets = load_buckets()
mir = mirror(cand)

print("=" * 76)
print("MIRROR TEST - OFI quartile, long, hold 5 menit")
print("=" * 76)
print()
orig = quartile_test(cand, buckets, "asli")
mirr = quartile_test(mir, buckets, "mirror")

if orig is None or mirr is None:
    raise SystemExit("data tidak cukup")

print(f"  {'quartile':<10} {'ASLI ret%':>11} {'t':>7} | "
      f"{'MIRROR ret%':>12} {'t':>7}")
print("  " + "-" * 56)
for i, label in enumerate(["Q1 (terendah)", "Q2", "Q3", "Q4 (tertinggi)"]):
    o_m, o_t, _ = orig[i]
    m_m, m_t, _ = mirr[i]
    print(f"  {label:<10} {o_m:>+11.4f} {o_t:>+7.2f} | "
          f"{m_m:>+12.4f} {m_t:>+7.2f}")
print()

q1_o, q1_ot, _ = orig[0]
q1_m, q1_mt, _ = mirr[0]
print(f"  Q1 asli   : {q1_o:+.4f}%  t {q1_ot:+.2f}  "
      f"{'POSITIF' if q1_o > 0 else 'negatif'}")
print(f"  Q1 mirror : {q1_m:+.4f}%  t {q1_mt:+.2f}  "
      f"{'POSITIF' if q1_m > 0 else 'negatif'}")
print()

if q1_o > 0 and q1_m > 0 and abs(q1_mt) > 2:
    print("  >> ALPHA. Q1 tetap untung di data yang dibalik.")
    print("    _mean reversion)_ yang bekerja dua arah, bukan bias arah.")
elif q1_o > 0 and q1_m < 0:
    print("  >> DRIFT. Q1 untung hanya di data bullish.")
    print("     Hilang begitu pasar turun - pola yang sama dengan")
    print("     lima kandidat time-series sebelumnya.")
else:
    print("  >> AMBIGU. Q1 tidak jelas di salah satu sisi.")
    print("     Butuh lebih banyak data sebelum keputusan apa pun.")