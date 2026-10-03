"""ADI test rentang dekat vs jauh, untuk symbol yang 1h-nya berhasil."""
import json
import time
import urllib.request
import datetime

HL = "https://api.hyperliquid.xyz/info"
now = int(time.time() * 1000)
DAY = 86_400_000


def post(p):
    req = urllib.request.Request(HL, data=json.dumps(p).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def fmt(rows):
    if not rows:
        return "KOSONG"
    d0 = datetime.datetime.fromtimestamp(rows[0]["t"] / 1000, datetime.UTC).date()
    d1 = datetime.datetime.fromtimestamp(rows[-1]["t"] / 1000, datetime.UTC).date()
    return f"n={len(rows):>5} {d0} -> {d1}"


# BTC punya 1h 5.002 baris di DB - jadi request-nya HARUS berhasil.
# Bandingkan dengan chunk 175 hari yang di-fetch_historical pakai.
print("BTC, 1h, request Various window")
print("-" * 62)
for days in (7, 30, 90, 175, 200, 400):
    start = now - days * DAY
    rows = post({"type": "candleSnapshot",
                 "req": {"coin": "BTC", "interval": "1h",
                         "startTime": start, "endTime": now}})
    print(f"  {days:>4} hari ke belakang : {fmt(rows)}")
    time.sleep(0.3)

print()
print("Now test the SAME thing on a symbol that's missing from DB.")
print("-" * 62)
for sym in ("ADA", "DOGE", "LIT"):
    for days in (7, 30, 90):
        start = now - days * DAY
        rows = post({"type": "candleSnapshot",
                     "req": {"coin": sym, "interval": "1h",
                             "startTime": start, "endTime": now}})
        print(f"  {sym:<5} {days:>4} hari : {fmt(rows)}")
        time.sleep(0.3)
    print()

print("KESIMPULAN:")
print("  Kalau BTC 1h dengan 400 hari KOSONG tapi 7 hari berisi,")
print("  berarti API membatasi KEDALAMAN history per interval -")
print("  1m hanya beberapa hari, 1h bisa setahun.")
print("  Itu mengubah strategi: untuk horizon panjang kita pakai 1h,")
print("  dan kita tidak butuh 1m sama sekali.")