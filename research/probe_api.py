"""Apakah Hyperliquid menghormati startTime, atau selalu kembalikan terbaru?"""
import json
import time
import urllib.request

HL = "https://api.hyperliquid.xyz/info"
now_ms = int(time.time() * 1000)
day = 86_400_000


def post(payload):
    req = urllib.request.Request(
        HL, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read()), time.time() - t0


print("TEST: apakah startTime dihormati?")
print()
for iv in ("1h", "15m"):
    for label, days_back in (("hari ini", 0), ("30 hari lalu", 30), ("120 hari lalu", 120)):
        start = now_ms - days_back * day - day
        rows, el = post({
            "type": "candleSnapshot",
            "req": {"coin": "BTC", "interval": iv,
                    "startTime": start, "endTime": now_ms},
        })
        if not rows:
            print(f"  {iv:<4} {label:<12} kosong  ({el:.1f}s)")
            continue
        import datetime
        d0 = datetime.datetime.fromtimestamp(rows[0]["t"] / 1000, datetime.UTC).date()
        d1 = datetime.datetime.fromtimestamp(rows[-1]["t"] / 1000, datetime.UTC).date()
        print(f"  {iv:<4} {label:<12} n={len(rows):>5}  "
              f"{d0} -> {d1}  ({el:.1f}s)")
    print()

print("KESIMPULAN:")
print("  Kalau startTime 30/120 hari lalu menghasilkan tanggal yang BERBEDA,")
print("  API menghormati rentang -> tinggal perbaiki pagination.")
print("  Kalau semuanya mengembalikan tanggal yang SAMA (terbaru), API")
print("  mengabaikan rentang -> interval 1h adalah satu-satunya cara")
print("  mendapatkan history panjang, dan itu harus cukup.")