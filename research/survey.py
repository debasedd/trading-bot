"""Survey singkat: berapa yang benar-benar terunduh per simbol/interval."""
import datetime
import sqlite3

c = sqlite3.connect("data_store/historical_candles.db")
rows = c.execute(
    "select symbol, interval, count(*), min(ts), max(ts) "
    "from hist_candles group by symbol, interval "
    "order by symbol, interval").fetchall()

print("total rows:", c.execute(
    "select count(*) from hist_candles").fetchone()[0])
print()

by_iv = {}
for sym, iv, n, t0, t1 in rows:
    days = (t1 - t0) / 86_400_000
    by_iv.setdefault(iv, []).append((sym, n, days))

for iv in sorted(by_iv):
    lst = by_iv[iv]
    syms = len(lst)
    total = sum(x[1] for x in lst)
    best = max(lst, key=lambda x: x[2])
    print(f"{iv:<4} {syms:>2} simbol  {total:>8} baris  "
          f"terpanjang {best[2]:.0f} hari ({best[0]})")

print()
print("=" * 74)
print("KANEEDAAN PER SIMBOL (1h = horizon panjang, 15m = menengah)")
print("=" * 74)
syms = sorted({r[0] for r in rows})
print(f"{'sym':<8} {'1h':>8} {'hari':>6} | {'15m':>8} {'hari':>6} | "
      f"{'5m':>8} {'hari':>6}")
print("-" * 66)
m1h = {(s, i): (n, (t1 - t0) / 86400000) for s, i, n, t0, t1 in rows}
for s in syms:
    cells = []
    for iv in ("1h", "15m", "5m"):
        v = m1h.get((s, iv))
        cells.append(f"{v[0]:>8} {v[1]:>6.0f}" if v else f"{'-':>8} {'-':>6}")
    print(f"{s:<8} " + " | ".join(cells))

print()
print("=" * 74)
print("APA YANG CUKUP UNTUK EVALUASI STRATEGI")
print("=" * 74)
h1 = [(s, v[1]) for (s, i), v in m1h.items() if i == "1h" and v[1] > 300]
print(f"  simbol dengan >300 hari di 1h : {len(h1)}")
print(f"  total baris 1h                 : "
      f"{sum(v[0] for (s,i),v in m1h.items() if i=='1h')}")
print()
if h1:
    print("  1h dengan 300+ hari CUKUP untuk:")
    print("   - membagi rezim bullish vs bearish")
    print("   - menguji kandidat SL 2% / TP 4% (hold 2 jam = 2 bar 1h)")
    print("   - menguji horizon pendek di 15m bila tersedia")
else:
    print("  BELUM CUKUP. Perlu tunggu rate limit longgar dan ulangi.")