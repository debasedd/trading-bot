"""
Cek sumber data historis yang bisa dipakai SEKARANG, tanpa menunggu.

Premis: masalah kita bukan "kurang data", tapi "data-nya bullish".
19 dari 20 simbol naik di periode sekarang. Semua kandidat edge
yang terlihat hilang begitu pasar turun - dan itu terbukti lewat
mirror test, bukan tebakan.

Yang kita butuhkan bukan lebih banyak data bullish. Kita butuh
data dari PERIODE YANG BEDA - terutama yang bearish.

Hyperliquid menyediakan candleCandle historis lewat REST tanpa
kredensial. Kalau itu jalan, kita bisa langsung menguji keenam
kandidat di periode yang datanya berlawanan arah.

Yang diuji di sini:
  1. Apakah candle historis bisa diambil, dan seberapa dalam.
  2. Apakah return periode itu bull atau bear.
  3. Kalau bisa, apakah itu cukup untuk menguji mirror test dengan
     data ASLI (bukan hasil pembalikan sintetis).
"""
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta

HL = "https://api.hyperliquid.xyz/info"
TIMEOUT = 20


def post(payload, url=HL):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read())


def probe():
    print("=" * 74)
    print("1. APAKAH HYPERLIQUID BISA DIMAKI?")
    print("=" * 74)
    try:
        meta = post({"type": "meta"})
        uni = meta["universe"]
        print(f"  universe        : {len(uni)} perps")
        print(f"  contoh          : {[u['name'] for u in uni[:6]]}")
    except (urllib.error.URLError, OSError) as e:
        print(f"  GAGAL: {type(e).__name__}: {e}")
        return None

    # Candle historis. Hyperliquid menerima startTime/endTime dalam ms.
    now_ms = int(time.time() * 1000)
    day_ms = 86_400_000
    for label, start, end in (
        ("24 jam", now_ms - day_ms, now_ms),
        ("7 hari", now_ms - 7 * day_ms, now_ms),
        ("30 hari", now_ms - 30 * day_ms, now_ms),
        ("90 hari", now_ms - 90 * day_ms, now_ms),
    ):
        try:
            c = post({
                "type": "candleSnapshot",
                "req": {
                    "coin": "BTC",
                    "interval": "1m",
                    "startTime": start,
                    "endTime": end,
                },
            })
            if not c:
                print(f"  {label:<9} : kosong")
                continue
            first = datetime.fromtimestamp(c[0]["t"] / 1000, timezone.utc)
            last = datetime.fromtimestamp(c[-1]["t"] / 1000, timezone.utc)
            print(f"  {label:<9} : {len(c):>6} candle  "
                  f"{first:%Y-%m-%d} .. {last:%Y-%m-%d}")
        except (urllib.error.URLError, OSError, KeyError) as e:
            print(f"  {label:<9} : GAGAL {type(e).__name__}: {e}")
    return uni


def returns_over_period(days=30):
    """
    Return aktual per simbol untuk periode yang diminta.

    Ini yang tidak boleh dilewatkan: kalau periode 90 hari kerally
    sementara 7 hari terakhir sideways, kita bisa memisahkan
    keduanya tanpa perlu menunggu.
    """
    print()
    print("=" * 74)
    print(f"2. RETURN AKTUAL {days} HARI TERAKHIR")
    print("=" * 74)
    now_ms = int(time.time() * 1000)
    start = now_ms - days * 86_400_000
    print(f"  {'sym':<8} {'candle':>8} {'first':>11} {'last':>11} {'ret%':>9}")
    print("  " + "-" * 52)
    out = {}
    for sym in ("BTC", "ETH", "SOL", "NEAR", "ENA", "PUMP", "HYPE",
                "XRP", "ZEC", "LIT"):
        try:
            c = post({
                "type": "candleSnapshot",
                "req": {"coin": sym, "interval": "1h",
                        "startTime": start, "endTime": now_ms},
            })
            if not c:
                print(f"  {sym:<8} kosong")
                continue
            first = float(c[0]["c"])
            last = float(c[-1]["c"])
            r = (last - first) / first * 100.0
            out[sym] = r
            print(f"  {sym:<8} {len(c):>8} {first:>11.2f} {last:>11.2f} "
                  f"{r:>+8.2f}%")
        except (urllib.error.URLError, OSError, KeyError) as e:
            print(f"  {sym:<8} GAGAL {type(e).__name__}")
    return out


def monthly_bars():
    """
    Return per bulan selama 12 bulan terakhir.

    Ini yang membuktikan ada (atau tidak ada) periode bearish di
    riwayat yang bisa diambil sekarang.
    """
    print()
    print("=" * 74)
    print("3. RETURN PER BULAN (apakah ada periode bearish?)")
    print("=" * 74)
    now_ms = int(time.time() * 1000)
    start = now_ms - 400 * 86_400_000
    try:
        c = post({
            "type": "candleSnapshot",
            "req": {"coin": "BTC", "interval": "4h",
                    "startTime": start, "endTime": now_ms},
        })
    except (urllib.error.URLError, OSError) as e:
        print(f"  GAGAL: {type(e).__name__}: {e}")
        return

    if not c:
        print("  kosong")
        return
    by_month = {}
    for row in c:
        m = datetime.fromtimestamp(row["t"] / 1000, timezone.utc)
        by_month.setdefault(m.strftime("%Y-%m"), []).append(float(row["c"]))
    print(f"  {'bulan':<9} {'ret%':>9}   {'lama':>6}")
    print("  " + "-" * 30)
    bears = 0
    for month in sorted(by_month):
        vals = by_month[month]
        r = (vals[-1] - vals[0]) / vals[0] * 100.0
        if r < 0:
            bears += 1
        flag = "  <-- BEARISH" if r < 0 else ""
        print(f"  {month:<9} {r:>+8.2f}%   {len(vals):>6}{flag}")
    print()
    print(f"  {bears} dari {len(by_month)} bulan bearish")
    if bears >= 3:
        print("  >> ADA periode bearish yang bisa diambil SEKARANG.")
        print("     Tidak perlu menunggu - cukupAmbil candle historis.")


if __name__ == "__main__":
    if probe() is None:
        sys.exit(1)
    returns_over_period(30)
    monthly_bars()