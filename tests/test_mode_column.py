"""
tests/test_mode_column.py — Provenance kolom `mode` di positions/trades.

Live dan paper menulis ke tabel yang sama (`database_path` satu untuk
keduanya). Tanpa kolom penanda, equity curve HUD bisa menggabungkan
profit simulasi dengan loss bursa, dan tidak ada yang bisa dibedakan.

Migrasi kolom ini yang paling rawan: `CREATE TABLE IF NOT EXISTS` tidak
menambah kolom ke tabel yang sudah ada, jadi database lama akan kehilangan
kolom selamanya dan `INSERT` yang menyebutnya gagal dengan "no such column".
Test-test di sini memverifikasi jalur itu langsung, terhadap database
yang sengaja dibangun TANPA kolom mode.
"""
import asyncio
import os
import sqlite3
import unittest
from pathlib import Path

from database.db import Database
from database.models import Position, Trade
from database.repository import Repository

# Skema PERSIS seperti sebelum kolom mode ada. Disalin, bukan
# di-import, supaya test ini gagal kalau skema lama berubah tanpa
# sengaja - yang justru akan membuat test ini tidak menguji apa pun.
PRE_MIGRATION_SCHEMA = """
    CREATE TABLE positions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        side TEXT NOT NULL,
        entry_price REAL NOT NULL,
        quantity REAL NOT NULL,
        leverage INTEGER DEFAULT 1,
        margin REAL NOT NULL,
        liquidation_price REAL NOT NULL,
        stop_loss REAL,
        take_profit REAL,
        unrealized_pnl REAL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'OPEN',
        opened_at TEXT DEFAULT (datetime('now')),
        closed_at TEXT,
        close_price REAL,
        realized_pnl REAL,
        close_reason TEXT,
        reasoning TEXT
    );
    CREATE TABLE trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        position_id INTEGER,
        symbol TEXT NOT NULL,
        side TEXT NOT NULL,
        price REAL NOT NULL,
        quantity REAL NOT NULL,
        fee REAL DEFAULT 0,
        fee_type TEXT DEFAULT 'TAKER',
        trade_type TEXT NOT NULL,
        executed_at TEXT DEFAULT (datetime('now'))
    );
"""


class TestModeMigration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.path = Path("data_store/test_mode_%d.db" % os.getpid())
        self._purge()
        self._build_pre_migration_db()

    async def asyncTearDown(self):
        self._purge()

    def _purge(self):
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(self.path) + suffix)
            if p.exists():
                p.unlink()

    def _build_pre_migration_db(self):
        """Buat database tanpa kolom mode, dengan satu baris posisi lama."""
        c = sqlite3.connect(self.path)
        c.executescript(PRE_MIGRATION_SCHEMA)
        c.execute(
            "INSERT INTO positions (symbol, side, entry_price, quantity, "
            "leverage, margin, liquidation_price, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("BTC/USDT:USDT", "LONG", 50000.0, 0.2, 10, 1000.0, 45000.0,
             "CLOSED"),
        )
        c.commit()
        c.close()

    def _columns(self, table):
        c = sqlite3.connect(self.path)
        cols = {r[1] for r in c.execute("PRAGMA table_info(%s)" % table)}
        c.close()
        return cols

    async def test_migration_adds_mode_to_both_tables(self):
        db = Database(db_path=str(self.path))
        await db.connect()
        await db.close()

        self.assertIn("mode", self._columns("positions"))
        self.assertIn("mode", self._columns("trades"))

    async def test_migration_preserves_existing_rows(self):
        """Baris yang sudah ada TIDAK boleh hilang atau berubah."""
        db = Database(db_path=str(self.path))
        await db.connect()
        await db.close()

        c = sqlite3.connect(self.path)
        rows = c.execute(
            "SELECT symbol, side, entry_price, status, mode FROM positions"
        ).fetchall()
        c.close()

        self.assertEqual(len(rows), 1, "baris lama harus utuh")
        self.assertEqual(rows[0][0], "BTC/USDT:USDT")
        self.assertEqual(rows[0][3], "CLOSED")
        self.assertEqual(rows[0][1], "LONG")
        self.assertAlmostEqual(rows[0][2], 50000.0)

    async def test_existing_rows_default_to_paper(self):
        """
        Baris lama diberi 'paper', bukan 'live'.

        Default 'live' akan melabeli ulang seluruh sejarah secara keliru -
        dandefault itu yang akan dipakai kalau developerr lupa mengedit
        migrasi setelah menambah kolom baru.
        """
        db = Database(db_path=str(self.path))
        await db.connect()
        await db.close()

        c = sqlite3.connect(self.path)
        modes = {r[0] for r in c.execute("SELECT mode FROM positions")}
        c.close()

        self.assertEqual(modes, {"paper"})

    async def test_migration_is_idempotent(self):
        """
        Boot kedua tidak boleh gagal.

        Migrasi dipanggil setiap boot, jadi harus aman dijalankan berulang.
        Tanpa guard "sudah ada", `ALTER TABLE ADD COLUMN` kedua akan
        meledak dengan "duplicate column name" dan bot tidak bisa start.
        """
        db = Database(db_path=str(self.path))
        await db.connect()
        await db.close()

        # Boot kedua - inilah yang diuji.
        db2 = Database(db_path=str(self.path))
        await db2.connect()
        await db2.close()

        c = sqlite3.connect(self.path)
        count = c.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
        c.close()
        self.assertEqual(count, 1, "boot kedua tidak boleh menambah baris")

    async def test_new_rows_default_to_paper(self):
        db = Database(db_path=str(self.path))
        await db.connect()
        repo = Repository(db)

        pid = await repo.insert_position(Position(
            symbol="ETH/USDT:USDT", side="SHORT", entry_price=3000.0,
            quantity=0.5, leverage=5, margin=300.0,
            liquidation_price=3400.0, status="OPEN",
        ))
        tid = await repo.insert_trade(Trade(
            symbol="ETH/USDT:USDT", side="SELL", price=3000.0, quantity=0.5,
            trade_type="OPEN", position_id=pid,
        ))
        await db.close()

        c = sqlite3.connect(self.path)
        pos_mode = c.execute(
            "SELECT mode FROM positions WHERE id=?", (pid,)
        ).fetchone()[0]
        trade_mode = c.execute(
            "SELECT mode FROM trades WHERE id=?", (tid,)
        ).fetchone()[0]
        c.close()

        self.assertEqual(pos_mode, "paper")
        self.assertEqual(trade_mode, "paper")

    async def test_mode_filter_separates_live_from_paper(self):
        """
        Filter mode harus benar-benar memisahkan.

        Kalau filter tidak membedakan, equity curve HUD tetap
        menggabungkan profit simulasi dengan loss bursa, dan seluruh
        tujuan kolom ini hilang.
        """
        db = Database(db_path=str(self.path))
        await db.connect()
        repo = Repository(db)

        await repo.insert_position(Position(
            symbol="PAPER1/USDT:USDT", side="LONG", entry_price=100.0,
            quantity=1.0, leverage=2, margin=50.0,
            liquidation_price=50.0, status="OPEN", mode="paper",
        ))
        await repo.insert_position(Position(
            symbol="LIVE1/USDT:USDT", side="LONG", entry_price=100.0,
            quantity=1.0, leverage=2, margin=50.0,
            liquidation_price=50.0, status="OPEN", mode="live",
        ))
        await db.close()

        db2 = Database(db_path=str(self.path))
        await db2.connect()
        repo2 = Repository(db2)

        paper = await repo2.get_open_positions(mode="paper")
        live = await repo2.get_open_positions(mode="live")
        everything = await repo2.get_open_positions()
        await db2.close()

        self.assertEqual([p["symbol"] for p in paper], ["PAPER1/USDT:USDT"])
        self.assertEqual([p["symbol"] for p in live], ["LIVE1/USDT:USDT"])
        self.assertEqual(len(everything), 2,
                         "tanpa filter harus mengembalikan keduanya")

    async def test_mode_filter_on_get_all_positions(self):
        db = Database(db_path=str(self.path))
        await db.connect()
        repo = Repository(db)

        for sym, mode in (("A/USDT:USDT", "paper"), ("B/USDT:USDT", "live")):
            await repo.insert_position(Position(
                symbol=sym, side="LONG", entry_price=100.0, quantity=1.0,
                leverage=2, margin=50.0, liquidation_price=50.0,
                status="CLOSED", realized_pnl=10.0, mode=mode,
            ))
        await db.close()

        db2 = Database(db_path=str(self.path))
        await db2.connect()
        repo2 = Repository(db2)

        paper = await repo2.get_all_positions(mode="paper")
        live = await repo2.get_all_positions(mode="live")
        await db2.close()

        # Baris lama dari `_build_pre_migration_db` juga `paper`, jadi
        # filter paper harus mengembalikan keduanya - BUKAN hanya A.
        # Kalau migrasi salah melabeli baris lama sebagai live, test ini
        # akan menangkapnya; kalau filter diabaikan, juga tertangkap.
        self.assertEqual(
            sorted(p["symbol"] for p in paper),
            ["A/USDT:USDT", "BTC/USDT:USDT"],
            "baris lama harus ikut ter-'paper'-kan, bukan hilang",
        )
        self.assertEqual([p["symbol"] for p in live], ["B/USDT:USDT"])


if __name__ == "__main__":
    unittest.main()
