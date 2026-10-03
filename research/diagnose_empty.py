"""Kenapa 1m/5m/15m nol untuk simbol yang 1h jalan?"""
import json
import sqlite3
import time
import urllib.request

HL = "https://api.hyperliquid.xyz/info"
now = int(time.time() * 1000)
DAY = 86_400_000


def post(p):
    req = urllib.request.Request(HL, data=json.dumps(p).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


c = sqlite3.connect("data_store/historical_candles.db")
have = {(r[0], r[1]): r[2] for r in c.execute(
    "select symbol, interval, count(*) from hist_candles "
    "group by symbol, interval")}

print("Symbol yang punya 1h tapi TIDAK punya 1m:")
missing = [s for (s, iv), n in have.items()
           if iv == "1h" and (s, "1m") not in have]
print(" ", missing[:8], "..." if len(missing) > 8 else "")
print()

sym = missing[0] if missing else "BTC"
print(f"PROBE untuk {sym}:")
for iv in ("1h", "15m", "5m", "1m"):
    # Chunk yang sama seperti fetch_historical pakai
    step = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000}[iv]
    chunk = 4200 * step
    start = now - 400 * DAY
    try:
        rows = post({"type": "candleSnapshot",
                     "req": {"coin": sym, "interval": iv,
                             "startTime": start, "endTime": start + chunk}})
        if not rows:
            print(f"  {iv:<4} chunk={chunk/DAY:>7.1f} hari -> KOSONG")
        else:
            import datetime
            d0 = datetime.datetime.fromtimestamp(
                rows[0]["t"] / 1000, datetime.UTC).date()
            d1 = datetime.datetime.fromtimestamp(
                rows[-1]["t"] / 1000, datetime.UTC).date()
            print(f"  {iv:<4} chunk={chunk/DAY:>7.1f} hari -> "
                  f"n={len(rows):>5}  {d0} -> {d1}")
    except Exception as e:
        print(f"  {iv:<4} ERROR {type(e).__name__}: {e}")
    time.sleep(0.3)

print()
print("HIPOTESIS:")
print("  API menolak rentang yang terlalu DEEP untuk interval pendek.")
print("  Kalau 1m dengan startTime 400 hari lalu kosong, tapi dengan")
print("  startTime 5 hari lalu berisi, maka batasnya kedalaman, bukan")
print("  jumlah - dan kita harus mulai dari yang paling dekat ke sekarang")
print("  lalu majU mundur, bukan sebaliknya.")