"""
Cek apa yang BELUM pernah diuji.

Semua strategi sebelumnya-directional: long kalau bullish, short kalau
bearish. Itu sebabnya semuanya mati di rezim bearish - bukan karena
sinyalnya salah, tapi karena strategy-nya menanggung arah pasar
sementara sinyal hanya membaca arah itu.

Yang belum diuji sama sekali:
  1. MARKET-NEUTRAL CROSS-SECTIONAL. Long 5 simbol terbaik, short 5
     terburuk, di saat yang sama. Dollar netral KONSTRUKSI - tidak ada
     yang perlu ditebak arah pasarnya. Edge-nya datang dari DISPERSI
     antar-simbol, bukan dari timing pasar.
  2. FUNDING CARRY. Funding perp adalah satu-satunya edge yang
     benar-benar terbukti ada di crypto: long membayar / short menerima
     atau sebaliknya, terus-menerus, dan itu bukan prediksi.

Keduanya butuh data yang belum dipakai. Cek dulu.
"""
import json
import time
import urllib.request

HL = "https://api.hyperliquid.xyz/info"
now = int(time.time() * 1000)
DAY = 86_400_000


def post(p, retries=3):
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(
                HL, data=json.dumps(p).encode(),
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except Exception as e:
            last = e
            time.sleep(2.0 * (i + 1))
    raise last


print("=" * 74)
print("1. FUNDING RATE HISTORIS")
print("=" * 74)

for typ in ("fundingHistory", "predictedFundings"):
    try:
        rows = post({"type": typ, "coin": "BTC",
                     "startTime": now - 90 * DAY})
        if rows:
            import datetime
            d0 = datetime.datetime.fromtimestamp(
                rows[0]["time"] / 1000, datetime.UTC).date()
            d1 = datetime.datetime.fromtimestamp(
                rows[-1]["time"] / 1000, datetime.UTC).date()
            print(f"  {typ:<20} n={len(rows):>6}  {d0} -> {d1}")
            print(f"     sample: {rows[0]}")
        else:
            print(f"  {typ:<20} kosong")
    except Exception as e:
        print(f"  {typ:<20} GAGAL {type(e).__name__}: {e}")
    time.sleep(1.0)

print()
print("=" * 74)
print("2. SNAPSHOT SEMUA SIMBOL (untuk cross-sectional live check)")
print("=" * 74)
try:
    snap = post({"type": "metaAndAssetCtxs"})
    universe, ctxs = snap[0]["universe"], snap[1]
    rows = []
    for meta, ctx in zip(universe, ctxs):
        try:
            rows.append((meta["name"],
                         float(ctx["funding"]),
                         float(ctx["markPx"])))
        except (KeyError, TypeError, ValueError):
            pass
    rows.sort(key=lambda r: r[1])
    print(f"  {'sym':<8} {'funding/jam':>12}")
    for name, f, px in rows[:5]:
        print(f"  {name:<8} {f:>+11.6%}")
    print("  ...")
    for name, f, px in rows[-5:]:
        print(f"  {name:<8} {f:>+11.6%}")

    neg = sum(1 for _, f, _ in rows if f < 0)
    print()
    print(f"  funding negatif : {neg}/{len(rows)}  "
          f"({neg/len(rows)*100:.0f}%)")
    print("  funding negatif = short bayar long. Kalau mayoritas negatif,")
    print("  long membayar biaya sambil-short menerima carry - dan itu")
    print("  edge yang tidak butuh prediksi arah.")
except Exception as e:
    print(f"  GAGAL {type(e).__name__}: {e}")