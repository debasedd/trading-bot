"""
data/order_book_recorder.py — Rekam order book L2 historis.

KENAPA INI ADA
-------------
Repositori ini tidak pernah menyimpan order book historis. `market_store`
menyimpan snapshot TERAKHIR saja, jadi begitu proses restart, semua
bukti struktur mikrostruktur hilang. Akibatnya:

  * `trading/fill_cost.py` tidak bisa di-backtest dengan spread asli.
  * Model microstructure tidak bisa dilatih sama sekali.
  * Spread dan depth yang dipakai saat ini adalah KOSTUM - konstanta
    3 bps yang dipilih karena terlihat wajar, bukan karena diukur.

Riset (research/, Sept 2026) menunjukkan edge strategi ada tapi
tipis: gross +0.18 per trade, dan biaya 8.5 bps per sisi
menghankannya. Gross itu berasal dari drift bullish, bukan alpha -
dibuktikan mirror test di research/bear.py. Jadi satu-satunya jalan
menuju edge nyata adalah sinyal yang BUKAN time-series: order flow.

Data ini adalah bahan bakunya.

APA YANG DICATAT
----------------
Snapshot L2 mentah pada interval tetap, plus turunan yang dibutuhkan
model: OFI (order flow imbalance), depth imbalance, spread relatif,
dan imbalance di beberapa kedalaman. Yang mentah disimpan supaya
fitur baru tidak memerlukan backfill.

SKEMA TABEL
-----------
Tabel terpisah, bukan kolom di `candles`, karena tiga alasan:

  1. Volume. L2 pada 20 level per sisi adalah ~40 baris per snapshot.
     Menyimpannya mentah per detik exploded PostgreSQL dalam hitungan
     jam. Yang disimpan adalah agregat per interval.
  2. Frekuensi berbeda. Candle 1m butuh 1 baris per menit. Book
     berubah puluhan kali per detik; menyimpan setiap perubahan
     berarti data unusable untuk query apa pun.
  3. Backward compatibility. Menambah kolom ke `candles` memaksa
     migrasi pada tabel yang sudah 78 MB.

Jadi: `order_book_snapshots` menyimpan agregat per interval (OFI,
depth, spread, size), dan `order_book_raw` opsional untuk debugging
dengan retention pendek.

INTERVAL
--------
Default 1 detik. Alasan: 1 menit terlalu kasar untuk order flow -
perubahan yang menarik biasanya selesai dalam hitungan detik,
dan OFI yang di-average per menit menghapus persis informasi yang dicari. 1 detik juga cukup kasar untuk tidak menyimpan book yang
sudah basi.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from pathlib import Path
from typing import Dict, List, Optional

from core.logger import get_logger

logger = get_logger("ob_recorder")


#: Skema tabel. `CREATE TABLE IF NOT EXISTS` jadi aman dipanggil ulang.
#: `PRAGMA` di bawah dieksekusi per-koneksi, bukan per-tabel, karena
#: WAL dan sinkronisasi adalah properti database.
#:
#: `busy_timeout` itu WAJIB, bukan opsional. File ini bukan database milik
#: rekorder sendiri - `database_path` yang sama dipakai `Database`
#: (aiosqlite) untuk candles, positions, trades. Dua koneksi menulis ke
#: file yang sama, jadi sqlite bisa mengunci. Tanpa `busy_timeout`,
#: `sqlite3.OperationalError: database is locked` langsung dilempar dan
#: TIK ITU HILANG - bukan hanya tertunda.
#:
#: Terukur: 69 dari ~1300 tick hilang dengan "database is locked" sebelum
#: baris ini ada. Itu 5% data hilang tanpa satu error pun terlihat di
#: tempat lain.
SCHEMA = """
PRAGMA busy_timeout=15000;
PRAGMA journal_mode=WAL;

-- Agregat per interval. Baris ini yang dipakai untuk latihan model.
CREATE TABLE IF NOT EXISTS order_book_features (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Waktu dalam milidetik epoch, sama seperti tabel candles, supaya
    -- join ke price tidak perlu konversi. Mengganti timezone di sini
    -- akan menggeser semua data 7 jam dan menutupi kecacatannya.
    timestamp INTEGER NOT NULL,

    symbol TEXT NOT NULL,

    -- Harga
    best_bid REAL NOT NULL,
    best_ask REAL NOT NULL,
    mid_price REAL NOT NULL,
    spread REAL NOT NULL,
    -- Spread relatif terhadap mid: berapa persen dari harga.ini yang
    -- yang comparable antar simbol; spread absolut tidak.
    spread_pct REAL NOT NULL,

    -- Volume di level terbaik
    bid_size REAL NOT NULL,
    ask_size REAL NOT NULL,
    -- Imbalance level terbaik, dalam [-1, 1]. Positif = sisi bid lebih
    -- besar = tekanan naik.
    top_imbalance REAL NOT NULL,

    -- Volume agregat di N level teratas per sisi, dan imbalance-nya.
    -- Dihitung dengan bobot linier menurun (level 0 bobot 1.0, level 1
    -- bobot 0.9, ...), sama dengan formula yang dipakai kernel
    -- `core/microstructure.py:122-128`. Kalau bobotnya berbeda, fitur
    -- yang dilatih tidak akan cocok dengan yang dipakai produksi.
    bid_depth REAL NOT NULL,
    ask_depth REAL NOT NULL,
    depth_imbalance REAL NOT NULL,

    -- Order Flow Imbalance: perubahan netto antara snapshot ini dan
    -- sebelumnya, dalam fraksi. Ini fitur paling penting untuk
    -- memprediksi arah gerak harga berikutnya.
    ofi REAL,

    -- Jumlah level yang benar-benar diterima. Book yang hanya punya 3
    -- level bukan book yang sama dengan yang punya 20, dan
    -- Fitur depth-nya tidak comparable. Disimpan supaya filter bisa
    -- membuang snapshot yang tidak layak.
    levels_bid INTEGER NOT NULL,
    levels_ask INTEGER NOT NULL
);

-- Index untuk query rentang waktu per simbol, yang merupakan pola
-- akses utama saat dilatih.
CREATE INDEX IF NOT EXISTS idx_obf_symbol_time
    ON order_book_features(symbol, timestamp);

CREATE INDEX IF NOT EXISTS idx_obf_time
    ON order_book_features(timestamp);

-- Book mentah, retention pendek. Hanya untuk debugging: "kenapa OFI
-- melonjak di 14:32:07?". Hapus setelah tidak ada yang menanyakannya.
CREATE TABLE IF NOT EXISTS order_book_raw (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    bids TEXT NOT NULL,     -- JSON [[px, sz], ...]
    asks TEXT NOT NULL,
    spread REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_obr_symbol_time
    ON order_book_raw(symbol, timestamp);
"""


#: Berapa kali percobaan ulang sebelum menyerah pada write yang terkunci.
#: 3 percobaan dengan backoff 8/16/32ms menutup hampir semua konflik
#: tanpa menunda loop lebih dari ~60ms.
_WRITE_RETRIES = 3


class OrderBookRecorder:
    """
    Rekam order book dari `market_store` ke SQLite pada interval tetap.

    Cara pakai: instantiate sekali, panggil `start()`, dan biarkan
    ia berjalan. Ia membaca `market_store.get_order_book(symbol)`
    langsung, jadi tidak perlu menyentuh jalur websocket sama sekali
    - yang penting `market_store` sudah diisi oleh feed yang ada.
    """

    def __init__(self, db_path: str, symbols: List[str],
                 interval_s: float = 1.0, keep_raw: bool = False,
                 raw_retention_s: float = 300.0):
        self.db_path = str(db_path)
        self.symbols = list(symbols)
        self.interval_s = float(interval_s)
        self.keep_raw = bool(keep_raw)
        self.raw_retention_s = float(raw_retention_s)

        self._db: Optional[sqlite3.Connection] = None
        self._task: Optional[asyncio.Task] = None
        self._running = False

        # Snapshot sebelumnya per simbol, untuk menghitung OFI.
        # Tanpa ini, OFI tidak bisa dihitung: OFI adalah PERUBAHAN,
        # bukan level absolut.
        self._prev: Dict[str, dict] = {}

        self._stats = {
            "snapshots": 0,
            "skipped_no_book": 0,
            "skipped_thin": 0,
            "errors": 0,
            # Berapa kali retry lock benar-benar habis. Kalau ini naik,
            # berarti data yang hilang bukan karena lock sesaat tapi
            # karena ada proses lain yang menahan file terlalu lama.
            "lock_retries_exhausted": 0,
        }

    # ── Lifecycle ────────────────────────────────────────────────
    def open(self) -> None:
        """Buka koneksi, buat skema. Sinkron - pemanggil saat boot."""
        if self._db is not None:
            return
        p = Path(self.db_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.db_path, timeout=15.0)
        self._db.executescript(SCHEMA)
        self._db.commit()
        logger.info(
            "Order book recorder siap: %s (%d simbol, interval %.1fs)",
            self.db_path, len(self.symbols), self.interval_s,
        )

    def close(self) -> None:
        if self._db is not None:
            try:
                self._db.commit()
            except sqlite3.Error:
                pass
            self._db.close()
            self._db = None
        logger.info("Order book recorder ditutup: %s", self._stats)

    async def start(self) -> None:
        if self._running:
            return
        self.open()
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("Order book recorder berjalan")

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self.close()

    async def _loop(self) -> None:
        while self._running:
            try:
                self._tick()
            except Exception as exc:
                self._stats["errors"] += 1
                # Error per tick harus terlihat tapi tidak boleh
                # menghentikan loop: data partially collected
                # lebih berguna dari tidak sama sekali.
                logger.warning("Order book recorder tick gagal: %s", exc)
            await asyncio.sleep(self.interval_s)

    # ── Pengumpulan ──────────────────────────────────────────────
    def _tick(self) -> None:
        if self._db is None:
            self.open()
        from core.market_store import market_store

        now_ms = int(time.time() * 1000)
        rows = []
        raw_rows = []
        cutoff = now_ms - int(self.raw_retention_s * 1000)

        for symbol in self.symbols:
            book = market_store.get_order_book(symbol)
            if not book:
                self._stats["skipped_no_book"] += 1
                continue

            feats = self._features(symbol, book, now_ms)
            if feats is None:
                self._stats["skipped_thin"] += 1
                continue
            rows.append(feats)

            if self.keep_raw:
                raw_rows.append((
                    now_ms, symbol,
                    json.dumps(book.get("bids") or []),
                    json.dumps(book.get("asks") or []),
                    float(book.get("spread") or 0.0),
                ))

        # `order_book_features` DAN `order_book_raw` ditulis dalam satu
        # transaksi, lalu di-commit sekali. Dua commit terpisah berarti dua
        # write lock berurutan pada file yang juga dipakai Database
        # aiosqlite, dan lock kedua kadang tertahan sampai timeout.
        self._write(rows, raw_rows, cutoff)

    def _write(self, rows, raw_rows, cutoff) -> None:
        """
        Tulis features + raw dalam satu transaksi, dengan retry.

        Retry itu perlu karena `busy_timeout` menambah cara SQLite
        MEMBAWAKAN lock, tapi tidak menjamin: pada konflik tinggi,
        `SQLITE_BUSY` masih bisa muncul. Tanpa retry, satu tick hilang
        - dan tick hilang berarti satu baris data hilang, tanpa jejak.
        """
        if not rows and not raw_rows:
            return

        for attempt in range(_WRITE_RETRIES):
            try:
                if rows:
                    self._db.executemany(
                        """INSERT INTO order_book_features
                           (timestamp, symbol, best_bid, best_ask, mid_price,
                            spread, spread_pct, bid_size, ask_size,
                            top_imbalance, bid_depth, ask_depth,
                            depth_imbalance, ofi, levels_bid, levels_ask)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        rows,
                    )
                if raw_rows:
                    self._db.executemany(
                        "INSERT INTO order_book_raw "
                        "(timestamp, symbol, bids, asks, spread)"
                        " VALUES (?,?,?,?,?)",
                        raw_rows,
                    )
                    # Prune raw dalam transaksi yang sama, supaya tidak
                    # perlu write lock kedua.
                    self._db.execute(
                        "DELETE FROM order_book_raw WHERE timestamp < ?",
                        (cutoff,),
                    )
                self._db.commit()
                if rows:
                    self._stats["snapshots"] += len(rows)
                return
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc) and "busy" not in str(exc):
                    raise
                # Rollback dulu supaya transaksi setengah jadi tidak
                # menahan lock untuk penulisan berikutnya.
                try:
                    self._db.rollback()
                except sqlite3.Error:
                    pass
                if attempt == _WRITE_RETRIES - 1:
                    self._stats["lock_retries_exhausted"] += 1
                    raise
                # Backoff eksponensial, kecil. 8ms, 16ms, 32ms - cukup
                # untuk lock yang sudah hampir selesai, dan tidak menunda
                # loop lebih dari yang perlu.
                time.sleep(0.008 * (2 ** attempt))

    def _features(self, symbol: str, book: dict,
                  now_ms: int) -> Optional[tuple]:
        """Hitung fitur dari satu snapshot. None bila book tidak layak."""
        bids = book.get("bids") or []
        asks = book.get("asks") or []
        if len(bids) < 2 or len(asks) < 2:
            # Book dengan satu level tidak punya spread, dan spread
            # adalah half dari biaya. Merekam snapshot seperti itu
            # hanya menambah baris yang tidak bisa dipakai.
            return None

        best_bid = float(bids[0][0])
        best_ask = float(asks[0][0])
        bid_size = float(bids[0][1])
        ask_size = float(asks[0][1])

        if best_bid <= 0 or best_ask <= 0 or best_ask < best_bid:
            # Book terbalik atau rusak. Menyelamatkan dengan menukar
            # sides akan menyembunyikan masalah di feed.
            return None

        mid = (best_bid + best_ask) / 2.0
        spread = best_ask - best_bid
        spread_pct = spread / mid if mid > 0 else 0.0

        # Bobot linier menurun, identik dengan `core/microstructure.py:122-128`.
        #
        # PENTING: depth dibatasi 10 level, bukan 20. Bobotnya
        # `1.0 - 0.1*i` yang jadi NEGATIF mulai level ke-11
        # (`1.0 - 0.1*11 = -0.1`), jadi memakai 20 level membuat
        # ask_depth bisa negatif dan `depth_imbalance` melonjak di luar
        # rentang [-1, 1] - di سعر yang benar-benar diam.
        #
        # Produksi aman karena `depth_imbalance` default-nya 5, jadi bobot
        # tidak pernah negatif di sana. Yang rusak di sini adalah rekorder
        # yang memakai [:20] sendiri.
        _DEPTH_LEVELS = 10
        bid_depth = 0.0
        for i, (px, sz) in enumerate(bids[:_DEPTH_LEVELS]):
            bid_depth += float(sz) * (1.0 - 0.1 * i)
        ask_depth = 0.0
        for i, (px, sz) in enumerate(asks[:_DEPTH_LEVELS]):
            ask_depth += float(sz) * (1.0 - 0.1 * i)

        total_depth = bid_depth + ask_depth
        depth_imb = ((bid_depth - ask_depth) / total_depth
                     if total_depth > 0 else 0.0)
        # Clamp defensif. Feature yang keluar dari [-1, 1] berarti ada
        # bug di perhitungan ATAU data book yang rusak, dan kedua hal itu
        # harus terlihat sebagai anomali yang bisa dibuang - bukan
        # diam-diam terkirim ke model yang dilatih.
        if not (-1.0 <= depth_imb <= 1.0):
            depth_imb = 0.0

        top_total = bid_size + ask_size
        top_imb = (bid_size - ask_size) / top_total if top_total > 0 else 0.0
        if not (-1.0 <= top_imb <= 1.0):
            top_imb = 0.0

        # OFI: perubahan volume di level terbaik, bukan level statis.
        prev = self._prev.get(symbol)
        ofi = None
        if prev is not None:
            d_bid = bid_size - prev["bid_size"]
            d_ask = ask_size - prev["ask_size"]
            denom = bid_size + ask_size + prev["bid_size"] + prev["ask_size"]
            ofi = (d_bid - d_ask) / denom if denom > 0 else 0.0

        self._prev[symbol] = {"bid_size": bid_size, "ask_size": ask_size}

        return (
            now_ms, symbol,
            best_bid, best_ask, mid, spread, spread_pct,
            bid_size, ask_size, top_imb,
            bid_depth, ask_depth, depth_imb, ofi,
            len(bids), len(asks),
        )

    # ── Diagnostik ───────────────────────────────────────────────
    def stats(self) -> dict:
        return dict(self._stats)


def count_rows(db_path: str) -> dict:
    """Hitung baris per tabel, untuk memastikan rekaman berjalan."""
    p = Path(db_path)
    if not p.exists():
        return {}
    conn = sqlite3.connect(str(p), timeout=10.0)
    try:
        out = {}
        for t in ("order_book_features", "order_book_raw"):
            try:
                out[t] = conn.execute(
                    f"SELECT COUNT(*) FROM {t}"
                ).fetchone()[0]
            except sqlite3.Error:
                out[t] = 0
        try:
            r = conn.execute(
                "SELECT MIN(timestamp), MAX(timestamp) FROM order_book_features"
            ).fetchone()
            if r and r[0]:
                out["window_s"] = (r[1] - r[0]) / 1000.0
        except sqlite3.Error:
            pass
        return out
    finally:
        conn.close()
