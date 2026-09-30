"""
database/db.py — Koneksi SQLite + pembuatan tabel (migrasi).
"""

import aiosqlite
from pathlib import Path
from core.config import get_config
from core.logger import get_logger

logger = get_logger("database")

# SQL untuk membuat semua tabel
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS candles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    timestamp INTEGER NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(symbol, timeframe, timestamp)
);
CREATE INDEX IF NOT EXISTS idx_candles_lookup ON candles(symbol, timeframe, timestamp);

CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    entry_price REAL NOT NULL,
    quantity REAL NOT NULL,
    leverage INTEGER NOT NULL DEFAULT 1,
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
    reasoning TEXT,
    mode TEXT NOT NULL DEFAULT 'paper'
);
CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status, symbol);

CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id INTEGER REFERENCES positions(id),
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    price REAL NOT NULL,
    quantity REAL NOT NULL,
    fee REAL NOT NULL DEFAULT 0,
    fee_type TEXT DEFAULT 'TAKER',
    trade_type TEXT NOT NULL,
    executed_at TEXT DEFAULT (datetime('now')),
    mode TEXT NOT NULL DEFAULT 'paper'
);
CREATE INDEX IF NOT EXISTS idx_trades_time ON trades(executed_at);

CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    timestamp TEXT DEFAULT (datetime('now')),
    signal_type TEXT NOT NULL,
    signal_value TEXT NOT NULL,
    direction TEXT,
    confidence REAL,
    source TEXT
);
CREATE INDEX IF NOT EXISTS idx_signals_time ON signals(symbol, timestamp);

CREATE TABLE IF NOT EXISTS news (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    source TEXT NOT NULL,
    url TEXT,
    published_at TEXT,
    fetched_at TEXT DEFAULT (datetime('now')),
    sentiment_vader REAL,
    sentiment_finbert REAL,
    sentiment_label TEXT,
    impact_level TEXT DEFAULT 'LOW',
    content_summary TEXT
);
CREATE INDEX IF NOT EXISTS idx_news_time ON news(fetched_at);

CREATE TABLE IF NOT EXISTS agent_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_name TEXT NOT NULL,
    action TEXT NOT NULL,
    reasoning TEXT NOT NULL,
    input_data TEXT,
    output_data TEXT,
    timestamp TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_agent_logs_time ON agent_logs(agent_name, timestamp);

CREATE TABLE IF NOT EXISTS account (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    balance REAL NOT NULL,
    initial_balance REAL NOT NULL,
    total_pnl REAL DEFAULT 0,
    total_trades INTEGER DEFAULT 0,
    winning_trades INTEGER DEFAULT 0,
    losing_trades INTEGER DEFAULT 0,
    max_drawdown REAL DEFAULT 0,
    peak_balance REAL,
    sharpe_ratio REAL,
    profit_factor REAL,
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS balance_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    balance REAL NOT NULL,
    unrealized_pnl REAL DEFAULT 0,
    equity REAL NOT NULL,
    timestamp TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_balance_time ON balance_history(timestamp);

-- Snapshot arah LONG/SHORT hasil ensemble multi-agen.
-- Satu baris per simbol per siklus ensemble. HUD dan DecisionAgent membaca
-- baris TERBARU yang sama, sehingga tampilan dan keputusan trade tidak
-- mungkin berbeda sumber.
CREATE TABLE IF NOT EXISTS direction_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    prob_long REAL NOT NULL,
    prob_short REAL NOT NULL,
    direction TEXT NOT NULL,
    confidence REAL NOT NULL,
    z_composite REAL DEFAULT 0,
    agent_breakdown TEXT,      -- JSON: verdict per agen
    diffusion TEXT,            -- JSON: time_horizons + long_probs + short_probs
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_dir_snap_symbol ON direction_snapshots(symbol, id);

CREATE TABLE IF NOT EXISTS macro_data (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    indicator TEXT NOT NULL,
    value REAL NOT NULL,
    period TEXT,
    source TEXT NOT NULL,
    fetched_at TEXT DEFAULT (datetime('now')),
    UNIQUE(indicator, period)
);
"""


class Database:
    """Wrapper koneksi SQLite async dengan WAL mode."""

    def __init__(self, db_path: str = None):
        if db_path is None:
            db_path = get_config().database_path
        self.db_path = db_path
        self._connection: aiosqlite.Connection = None

    async def connect(self):
        """Buka koneksi dan buat tabel jika belum ada."""
        # Pastikan folder ada
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

        self._connection = await aiosqlite.connect(self.db_path)

        # WAL mode untuk baca/tulis bersamaan.
        await self._connection.execute("PRAGMA journal_mode=WAL")

        # `busy_timeout` menentukan berapa lama SQLite BERGOBAH menunggu lock
        # sebelum melempar "database is locked". Nilai lama 5000 ms ternyata
        # tidak cukup: log pernah merekam 1150 kegagalan lock, dan akibatnya
        # execution loop ikut crash ("Error pada execution loop") — jadi bukan
        # sekadar log yang hilang, tapi siklus keputusan yang berhenti.
        #
        # Bot ini punya banyak penulis bersamaan pada satu koneksi: loop
        # keputusan 0,3 detik, ensemble arah 5 detik, candle, agent log,
        # dan prune. Di bawah beban itu, satu penulis bisa memegang lock lebih
        # dari 5 detik. 15 detik memberi ruang yang wajar tanpa membuat
        # kegagalan yang sah menggantung terlalu lama.
        await self._connection.execute("PRAGMA busy_timeout=15000")
        self._connection.row_factory = aiosqlite.Row

        # Buat tabel
        await self._connection.executescript(SCHEMA_SQL)
        await self._connection.commit()

        # Migrasi kolom yang ditambahkan SETELAH database pertama dibuat.
        #
        # `CREATE TABLE IF NOT EXISTS` di SCHEMA_SQL tidak menambah kolom
        # ke tabel yang sudah ada, jadi database lama akan kehilangan setiap kolom
        # baru selamanya - dan `INSERT` yang menyebut kolom itu akan gagal
        # dengan "no such column" yang tidak menyuruh siapa-siapa melihat migrasinya.
        #
        # `_add_column_if_missing` di-scope per-kolom dan idempotent, jadi
        # dipanggil setiap boot tanpa efek samping setelah pertama.
        await self._migrate_columns()

        logger.info(f"Database terhubung: {self.db_path}")

    async def _migrate_columns(self) -> None:
        """
        Tambah kolom yang belum ada, tanpa menghapus atau mengubah data.

        Pola `PRAGMA table_info` + `ALTER TABLE ADD COLUMN` dipakai, bukan
        `CREATE TABLE ... AS SELECT`, karena yang kedua men-drop index dan
        constraint yang sudah ada.

        Kolom baru SELALU punya default atau NULL-able, supaya baris lama
        yang sudah ada tidak melanggar constraint saat kolom ditambahkan.
        """
        # (tabel, kolom, definisi SQL kolom)
        migrations = [
            # Mode penvenance. Live dan paper menulis ke tabel yang sama
            # (`database_path` satu untuk keduanya), jadi tanpa kolom ini
            # HUD menampilkan equity curve gabungan yang tidak pernah
            # terjadi di dunia nyata: profit dari simulasi dicampur dengan
            # loss dari bursa, dan tidak ada yang bisa dibedakan.
            #
            # Default 'paper' karena SEMUA baris yang ada sudah ditulis
            # oleh paper engine - mengubah default ke live akan melabeli ulang sejarah secara keliru.
            ("positions", "mode", "TEXT NOT NULL DEFAULT 'paper'"),
            ("trades", "mode", "TEXT NOT NULL DEFAULT 'paper'"),
        ]

        for table, column, definition in migrations:
            await self._add_column_if_missing(table, column, definition)

        # Index untuk memfilter per-mode. Tanpa ini setiap query HUD
        # menyapu seluruh tabel, dan `positions` tumbuh tanpa batas
        # selama bot berjalan.
        for table in ("positions", "trades"):
            await self._connection.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{table}_mode "
                f"ON {table}(mode)"
            )
        await self._connection.commit()

    async def _add_column_if_missing(
        self, table: str, column: str, definition: str
    ) -> None:
        cursor = await self._connection.execute(
            f"PRAGMA table_info({table})"
        )
        existing = {row[1] for row in await cursor.fetchall()}
        if column in existing:
            return
        await self._connection.execute(
            f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
        )
        logger.info(
            f"Migrasi: kolom '{column}' ditambahkan ke tabel '{table}'"
        )

    async def close(self):
        """Tutup koneksi."""
        if self._connection:
            await self._connection.close()
            self._connection = None
            logger.info("Database ditutup")

    @property
    def conn(self) -> aiosqlite.Connection:
        """Akses koneksi langsung."""
        if self._connection is None:
            raise RuntimeError("Database belum terhubung. Panggil connect() dulu.")
        return self._connection

    async def execute(self, sql: str, params: tuple = None):
        """Eksekusi SQL tunggal."""
        if params:
            return await self.conn.execute(sql, params)
        return await self.conn.execute(sql)

    async def executemany(self, sql: str, params_list: list):
        """Eksekusi SQL untuk banyak baris."""
        return await self.conn.executemany(sql, params_list)

    async def fetchone(self, sql: str, params: tuple = None):
        """Ambil satu baris."""
        cursor = await self.execute(sql, params)
        return await cursor.fetchone()

    async def fetchall(self, sql: str, params: tuple = None):
        """Ambil semua baris."""
        cursor = await self.execute(sql, params)
        return await cursor.fetchall()

    async def commit(self):
        """Commit transaksi."""
        await self.conn.commit()


# Singleton
_db: Database = None


async def get_db() -> Database:
    """Ambil instance database global. Buat koneksi jika belum ada."""
    global _db
    if _db is None:
        _db = Database()
        await _db.connect()
    return _db


async def init_db() -> Database:
    """Inisialisasi database (alias untuk get_db)."""
    return await get_db()


async def close_db():
    """Tutup koneksi database global."""
    global _db
    if _db is not None:
        await _db.close()
        _db = None
