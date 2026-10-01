"""
Fade-the-imbalance: apakah ada edge di fade-the-imbalance?

ofi_test.py menemukan pola yang tidak diharapkan dan sangat kuat:
di SEMUA horizon, kuartil OFI RENDAH memberi return lebih besar
daripada kuartil OFI TINGGI, dan selisihnya tumbuh seiring horizon
(t-stat Q1: +3.41 di 1m sampai +13.84 di 15m).

Pertanyaan sekarang: apakah itu bisa dieksploitasi setelah biaya?

Kendala penting: `order_book_features` dan `candles` TIDAK overlay
sempurna.Order book terkumpul 24 jam, tapi candle 1m untuk simbol yang
sama hanya mencakup sebagian - karena reset database dan restart bot menggeser itu. Jadi fold-split per-simbol TIDAK bisa
dipakai di sini: fold2 candles berada di menit 29837362-29838971,
sementara order book di 29846168-29847615.

Karena itu test ini BUKAN walk-forward. Ini satu-satunya tes yang bisa dijalankan dengan data yang ada, dan hasilnya harus dibaca sebagai indikasi, bukan bukti. Mirror test (research/mirror_test_final.py)
adalah yang membuktikan alpha vs drift, dan itu butuh data yang
candles-nya benar-benar overlay.
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

cand = {}
for r in mn.execute(
    "SELECT symbol, timestamp, open, high, low, close FROM candles "
    "WHERE timeframe='1m' ORDER BY symbol, timestamp"
):
    cand.setdefault(r[0], []).append((r[1] // 60000, r[2]))

buckets = {}
for r in ob.execute(
    "SELECT symbol, timestamp, ofi FROM order_book_features WHERE ofi IS NOT NULL"
):
    k = (r[0], r[1] // 60000)
    b = buckets.setdefault(k, [0.0, 0])
    b[0] += r[1] or 0.0
    b[1] += 1

# Hanya yang benar-benar overlay di KEDUA tabel.
overlap_syms = [s for s in cand if s in {r[0] for r in
                                         ob.execute("SELECT DISTINCT symbol "
                                                    "FROM order_book_features")}]
usable = {}
for s in overlap_syms:
    idx = {c[0]: i for i, c in enumerate(cand[s])}
    mins = [k[1] for k in buckets if k[0] == s and k[1] in idx]
    if len(mins) >= 60:
        usable[s] = (idx, sorted(mins))

print("=" * 76)
print("DATA YANG BENAR-BENAR OVERLAY")
print("=" * 76)
for s, (idx, mins) in sorted(usable.items()):
    print(f"  {s.split('/')[0]:<8} {len(mins):>5} menit")
print(f"  total: {len(usable)} simbol, "
      f"{sum(len(v[1]) for v in usable.values())} bucket")
print()

if not usable:
    print("  TIDAK ADA simbol yang overlay cukup. Tidak bisa diuji.")
    raise SystemExit


def backtest(ofi_thresh, hold_min, long_low=True):
    eq = 10_000.0
    net = 0.0
    n = w = 0
    ws = ls = 0.0
    for sym, (idx, mins) in usable.items():
        cl = cand[sym]
        base = sym.split("/")[0]
        cost = FLOOR.get(base, 0.0003) + IMPACT + TAKER
        for m in mins:
            b = buckets[(sym, m)]
            ofi = b[0] / b[1]
            if long_low:
                if not (ofi < ofi_thresh):
                    continue
                d_ = 1
            else:
                if not (ofi > -ofi_thresh):
                    continue
                d_ = -1
            i0 = idx.get(m)
            i1 = idx.get(m + hold_min)
            if i0 is None or i1 is None or i1 <= i0:
                continue
            entry = cl[i0][1]
            if entry <= 0:
                continue
            qty = (0.005 * eq * 10) / entry
            ex = cl[i1][1]
            ef = ex * (1 - cost) if d_ > 0 else ex * (1 + cost)
            pnl = (ef - entry) * qty * d_ - (entry + ef) * qty * TAKER
            eq += pnl
            net += pnl
            n += 1
            if pnl > 0:
                w += 1
                ws += pnl
            else:
                ls += -pnl
    pf = ws / ls if ls > 0 else float("inf")
    return n, net, pf, (w / n if n else 0.0)


print("=" * 76)
print("FADE THE IMBALANCE — long saat OFI rendah")
print("=" * 76)
print(f"  {'cfg':<26} {'n':>5} {'net':>10} {'per_tr':>10} {'pf':>8} {'wr':>7}")
print("  " + "-" * 70)
for thresh in (-0.01, 0.0, 0.01, 0.02):
    for hold in (2, 3, 5, 10, 15):
        n, net, pf, wr = backtest(thresh, hold)
        if n < 20:
            continue
        print(f"  ofi<{thresh:<6} hold{hold:>2}m     {n:>5} {net:>+10.2f} "
              f"{net/n:>+10.4f} {pf:>8.3f} {wr*100:>6.1f}%")

print()
print("=" * 76)
print("KONTRA: long saat OFI TINGGI (doktrin order flow standar)")
print("=" * 76)
print(f"  {'cfg':<26} {'n':>5} {'net':>10} {'per_tr':>10} {'pf':>8} {'wr':>7}")
print("  " + "-" * 70)
for thresh in (0.0, 0.01):
    for hold in (2, 5, 10):
        n, net, pf, wr = backtest(thresh, hold, long_low=False)
        if n < 20:
            continue
        print(f"  ofi>{thresh:<6} short{hold:>2}m    {n:>5} {net:>+10.2f} "
              f"{net/n:>+10.4f} {pf:>8.3f} {wr*100:>6.1f}%")

print()
print("  kalau fade (long OFI rendah) menang sementara yang kontra kalah,")
print("  itu REVERSAL. kalau keduanya kalah, tidak ada edge yang bisa")
print("  diambil - dan OFI hanya berguna sebagai filter, bukan sinyal.")