"""
Unduh candle historis Hyperliquid untuk semua simbol yang pernah
dipakai bot.

Kenapa ini penting, dan kenapa sekarang:

Semua edge yang_so far_ terlihat hilang begitu pasar turun - dan itu
terbukti lewat mirror test, bukan tebakan. Data yang dipakai selama
ini hanya periode yang bullish: 19 dari 20 simbol naik, median +8%.

Yang dibutuhkan bukan lebih banyak data bullish. Yang dibutuhkan
adalah periode yang berlawanan arah - dan itu ada di riwayat,
7 dari 15 bulan terakhir bearish.

Candle historis diambil SEKARANG, bukan dikumpulkan mingguan.

Sumber: POST https://api.hyperliquid.xyz/info
  {"type": "candleSnapshot", "req": {"coin", "interval", "startTime", "endTime"}}

Batas API: ~5.000 candle per permintaan, jadi timeframe panjang harus
dipecah per bulan dan digabung.
"""
import json
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

HL = "https://api.hyperliquid.xyz/info"
TIMEOUT = 30
SLEEP = 0.25                       # jeda antar request, hormati rate limit

# API memotong hasil di ~5.000 candle TANPA memberi tahu. Jadi chunk harus
# dihitung dari interval, bukan dari tanggal - request 5 hari untuk 1m
# meminta 7.200 candle dan hanya 5.000 yang kembali, diam-diam. Itu sebabnya
# run pertama menghasilkan BTC 1m hanya 4 hari padahal meminta 400.
#
# Dikembalikan dari probe_api.py: `startTime` dihormati dengan benar,
# jadi yang dibutuhkan cuma chunk yang cukup kecil.
CANDLE_LIMIT = 4200                 # di bawah ~5.000 dengan margin aman
INTERVAL_MS = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}

DB = "data_store/historical_candles.db"

# Simbol yang pernah diproses bot, plus beberapa yang likuid.
SYMBOLS = [
    "BTC", "ETH", "SOL", "NEAR", "ENA", "PUMP", "HYPE", "XRP", "ZEC",
    "LIT", "DOGE", "AVAX", "ADA", "BNB", "ARB", "SUI", "LINK", "UNI",
    "XPL", "ONDO", "AAVE",
]

# Interval yang diambil. 1m untuk granularitas order-flow, 5m untuk
# horizon menengah yang dipakai backtest.
INTERVALS = ["1m", "5m", "1h", "15m"]

DAYS = 400


def post(payload, retries=3):
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                HL,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return json.loads(r.read())
        except (urllib.error.URLError, OSError) as e:
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise last


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA busy_timeout=15000;

CREATE TABLE IF NOT EXISTS hist_candles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    interval TEXT NOT NULL,
    ts INTEGER NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL,
    trades INTEGER,
    UNIQUE(symbol, interval, ts)
);
CREATE INDEX IF NOT EXISTS idx_hist ON hist_candles(symbol, interval, ts);
"""


def main():
    conn = sqlite3.connect(DB)
    conn.executescript(SCHEMA)
    conn.commit()

    now_ms = int(time.time() * 1000)
    start_ms = now_ms - DAYS * 86_400_000

    total_new = 0
    failed = []

    for sym in SYMBOLS:
        for iv in INTERVALS:
            # Pecah per chunk supaya tidak kena batas ~5.000 candle.
            cursor = start_ms
            got_for_iv = 0
            step_ms = INTERVAL_MS[iv]
            # Chunk dihitung dari interval supaya TIDAK PERNAH melebihi
            # batas ~5.000 candle API. 4.200 candle 1m = 2,9 hari;
            # 4.200 candle 1h = 175 hari, jadi 1h cuma butuh 3 request
            # untuk 400 hari.
            chunk_ms = CANDLE_LIMIT * step_ms
            empty_streak = 0
            while cursor < now_ms:
                end = min(cursor + chunk_ms, now_ms)
                try:
                    rows = post({
                        "type": "candleSnapshot",
                        "req": {
                            "coin": sym,
                            "interval": iv,
                            "startTime": cursor,
                            "endTime": end,
                        },
                    })
                except Exception as e:
                    failed.append((sym, iv, type(e).__name__))
                    break

                if not rows:
                    # Rentang ini tidak ada candle. Maju satu chunk, tapi
                    # hentikan kalau terlalu banyak kosong berturut - itu
                    # tandanya sym/interval ini tidak punya history jauh
                    # ke belakang (mis. perp baru listing).
                    empty_streak += 1
                    cursor = end + step_ms
                    if empty_streak >= 3:
                        break
                    time.sleep(SLEEP)
                    continue
                empty_streak = 0

                data = [
                    (sym, iv, r["t"], float(r["o"]), float(r["h"]),
                     float(r["l"]), float(r["c"]),
                     float(r.get("v") or 0.0), int(r.get("n") or 0))
                    for r in rows
                    if all(k in r for k in ("t", "o", "h", "l", "c"))
                ]
                if data:
                    conn.executemany(
                        "INSERT OR IGNORE INTO hist_candles "
                        "(symbol, interval, ts, open, high, low, close, "
                        " volume, trades) VALUES (?,?,?,?,?,?,?,?,?)",
                        data,
                    )
                    got_for_iv += len(data)

                nxt = rows[-1]["t"] + step_ms
                if nxt <= cursor:
                    break
                cursor = nxt
                time.sleep(SLEEP)

            conn.commit()
            total_new += got_for_iv
            print(f"  {sym:<6} {iv:<4} {got_for_iv:>6} candle", flush=True)

    conn.close()

    print()
    print(f"total: {total_new} candle baru")
    if failed:
        print(f"gagal: {len(failed)}")
        for f in failed[:10]:
            print(f"  {f[0]} {f[1]} {f[2]}")


if __name__ == "__main__":
    main()