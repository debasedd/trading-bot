"""
database/repository.py — Fungsi CRUD untuk semua tabel.
"""

import json
from typing import List, Optional
from database.db import Database, get_db
from database.models import (
    Candle, Position, Trade, Signal, NewsItem,
    AgentLog, Account, BalanceSnapshot, MacroData,
)
from core.logger import get_logger

logger = get_logger("repository")


def _valid_candle(c: Candle) -> bool:
    """
    Invarian OHLC: high harus menutupi open/close, low harus di bawah keduanya,
    dan tidak ada harga atau volume yang negatif.

    Bar yang melanggar ini tidak mungkin berasal dari bursa. Sekali tersimpan,
    satu bar seperti itu cukup untuk merusak chart — skala sumbu-y ikut bergeser
    dan lilinnya tampak patah. Validasi di sini menutup semua pemanggil, bukan
    hanya jalur PriceFeed.
    """
    try:
        o, h, l, cl, v = (float(c.open), float(c.high), float(c.low),
                          float(c.close), float(c.volume))
    except (TypeError, ValueError):
        return False
    if o <= 0 or h <= 0 or l <= 0 or cl <= 0 or v < 0:
        return False
    return not (h < l or h < o or h < cl or l > o or l > cl)


class Repository:
    """CRUD operations untuk semua tabel."""

    def __init__(self, db: Database):
        self.db = db

    # ─── Candles ─────────────────────────────────────────────

    async def insert_candle(self, c: Candle):
        if not _valid_candle(c):
            logger.debug(f"Lilin ditolak (invarian OHLC): {c.symbol} {c.timeframe} {c.timestamp}")
            return
        await self.db.execute(
            """INSERT INTO candles (symbol, timeframe, timestamp, open, high, low, close, volume)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(symbol, timeframe, timestamp) DO UPDATE SET
                   open = excluded.open,
                   high = excluded.high,
                   low = excluded.low,
                   close = excluded.close,
                   volume = excluded.volume""",
            (c.symbol, c.timeframe, c.timestamp, c.open, c.high, c.low, c.close, c.volume),
        )
        await self.db.commit()

    async def insert_candles_batch(self, candles: List[Candle]):
        """
        Tulis sekumpulan lilin secara idempoten.

        Memakai UPSERT, bukan INSERT OR IGNORE: lilin yang sudah ada DIPERBARUI
        dengan nilai terbaru dari bursa. Dengan INSERT OR IGNORE, satu baris yang
        pernah tersimpan salah (mis. lilin menit yang sempat terambil sebelum
        lengkap) akan bertahan selamanya — penyegaran berkala tidak akan pernah
        bisa memperbaikinya. UPSERT membuat setiap putaran penyegaran
        merekonsiliasi database terhadap bursa, bukan sekadar menambah baris baru.

        Bar yang gagal invarian OHLC dibuang di sini juga, sehingga tidak ada
        pemanggil yang bisa menyelundupkan bar rusak ke tabel.
        """
        candles = [c for c in candles if _valid_candle(c)]
        if not candles:
            return
        await self.db.executemany(
            """INSERT INTO candles (symbol, timeframe, timestamp, open, high, low, close, volume)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(symbol, timeframe, timestamp) DO UPDATE SET
                   open = excluded.open,
                   high = excluded.high,
                   low = excluded.low,
                   close = excluded.close,
                   volume = excluded.volume""",
            [(c.symbol, c.timeframe, c.timestamp, c.open, c.high, c.low, c.close, c.volume) for c in candles],
        )
        await self.db.commit()

    async def get_candles(self, symbol: str, timeframe: str, limit: int = 500) -> List[dict]:
        rows = await self.db.fetchall(
            """SELECT * FROM candles WHERE symbol = ? AND timeframe = ?
               ORDER BY timestamp DESC LIMIT ?""",
            (symbol, timeframe, limit),
        )
        return [dict(r) for r in rows]

    async def get_latest_candle(self, symbol: str, timeframe: str) -> Optional[dict]:
        row = await self.db.fetchone(
            """SELECT * FROM candles WHERE symbol = ? AND timeframe = ?
               ORDER BY timestamp DESC LIMIT 1""",
            (symbol, timeframe),
        )
        return dict(row) if row else None

    # ─── Positions ───────────────────────────────────────────

    async def insert_position(self, p: Position) -> int:
        cursor = await self.db.execute(
            """INSERT INTO positions
               (symbol, side, entry_price, quantity, leverage, margin,
                liquidation_price, stop_loss, take_profit, status, reasoning,
                mode)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (p.symbol, p.side, p.entry_price, p.quantity, p.leverage, p.margin,
             p.liquidation_price, p.stop_loss, p.take_profit, p.status,
             p.reasoning, getattr(p, "mode", "paper") or "paper"),
        )
        await self.db.commit()
        return cursor.lastrowid

    async def update_position_pnl(self, position_id: int, unrealized_pnl: float):
        await self.db.execute(
            "UPDATE positions SET unrealized_pnl = ? WHERE id = ?",
            (unrealized_pnl, position_id),
        )
        await self.db.commit()

    async def update_position_sl_tp(
        self, position_id: int, stop_loss: Optional[float] = None, take_profit: Optional[float] = None
    ):
        updates = []
        params = []
        if stop_loss is not None:
            updates.append("stop_loss = ?")
            params.append(stop_loss)
        if take_profit is not None:
            updates.append("take_profit = ?")
            params.append(take_profit)
        if not updates:
            return
        params.append(position_id)
        query = f"UPDATE positions SET {', '.join(updates)} WHERE id = ?"
        await self.db.execute(query, tuple(params))
        await self.db.commit()

    async def close_position(
        self, position_id: int, close_price: float,
        realized_pnl: float, close_reason: str
    ) -> bool:
        """
        Tutup posisi HANYA jika masih OPEN. Return True bila baris benar-benar
        diklaim oleh pemanggil ini.

        Syarat `status = 'OPEN'` di dalam WHERE membuat penutupan menjadi
        operasi klaim-tunggal: dua pemanggil yang berebut (mis. SL/TP dari
        `update_positions` dan `batch_close_positions` dari scheduler) tidak
        bisa dua-duanya mengembalikan margin yang sama.
        """
        cursor = await self.db.execute(
            """UPDATE positions
               SET status = 'CLOSED', closed_at = datetime('now'),
                   close_price = ?, realized_pnl = ?, close_reason = ?,
                   unrealized_pnl = 0
               WHERE id = ? AND status = 'OPEN'""",
            (close_price, realized_pnl, close_reason, position_id),
        )
        await self.db.commit()
        return cursor.rowcount > 0

    async def liquidate_position(self, position_id: int, liq_price: float, realized_pnl: float) -> bool:
        cursor = await self.db.execute(
            """UPDATE positions
               SET status = 'LIQUIDATED', closed_at = datetime('now'),
                   close_price = ?, realized_pnl = ?, close_reason = 'LIQUIDATED',
                   unrealized_pnl = 0
               WHERE id = ? AND status = 'OPEN'""",
            (liq_price, realized_pnl, position_id),
        )
        await self.db.commit()
        return cursor.rowcount > 0

    async def get_open_positions(self, symbol: str = None,
                                mode: str = None) -> List[dict]:
        """
        Posisi yang masih terbuka.

        `mode` memfilter 'paper' atau 'live'. None (default) berarti
        keduanya, yang selama ini satu-satunya perilaku dan tetap
        default supaya tidak ada pemanggil yang diam-diam berubah
        eredensinya. Melewati filter di run mode live berarti equity
        curve HUD menggabungkan simulasi dan uang sungguhan.
        """
        clauses = ["status = 'OPEN'"]
        params = []
        if symbol:
            clauses.append("symbol = ?")
            params.append(symbol)
        if mode:
            clauses.append("mode = ?")
            params.append(mode)
        sql = (
            "SELECT * FROM positions WHERE " + " AND ".join(clauses)
            + " ORDER BY opened_at DESC LIMIT 1000"
        )
        rows = await self.db.fetchall(sql, tuple(params))
        return [dict(r) for r in rows]

    async def get_all_positions(self, limit: int = 100,
                                mode: str = None) -> List[dict]:
        sql = "SELECT * FROM positions"
        params = []
        if mode:
            sql += " WHERE mode = ?"
            params.append(mode)
        sql += " ORDER BY opened_at DESC LIMIT ?"
        params.append(limit)
        rows = await self.db.fetchall(sql, tuple(params))
        return [dict(r) for r in rows]

    # ─── Trades ──────────────────────────────────────────────

    async def insert_trade(self, t: Trade) -> int:
        cursor = await self.db.execute(
            """INSERT INTO trades
               (position_id, symbol, side, price, quantity, fee, fee_type,
                trade_type, mode)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (t.position_id, t.symbol, t.side, t.price, t.quantity,
             t.fee, t.fee_type, t.trade_type,
             getattr(t, "mode", "paper") or "paper"),
        )
        await self.db.commit()
        return cursor.lastrowid

    async def prune_agent_logs(self, keep: int = 5000):
        """
        Buang `agent_logs` paling lama, sisakan `keep` terbaru.

        Tabel ini ditulis pada SETIAP siklus decision — termasuk saat tidak
        terjadi apa-apa ("Tidak ada order baru"). Pada interval 0,3 detik itu
        ~214 baris/menit, atau ~309.000 baris per hari, dan tidak ada satu pun
        `DELETE` yang menyentuhnya. `prune_direction_snapshots` tidak
        menyentuh tabel ini.

        Batas defaultnya 5.000 baris: cukup untuk menelusuri kejadian
        terbaru, dan tidak sekali-kali jadi bottleneck.
        """
        await self.db.execute(
            "DELETE FROM agent_logs WHERE id NOT IN "
            "(SELECT id FROM agent_logs ORDER BY id DESC LIMIT ?)",
            (int(keep),),
        )
        await self.db.commit()

    async def get_trades(self, limit: int = 100) -> List[dict]:
        rows = await self.db.fetchall(
            "SELECT * FROM trades ORDER BY executed_at DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in rows]

    async def get_trades_by_position(self, position_id: int) -> List[dict]:
        rows = await self.db.fetchall(
            "SELECT * FROM trades WHERE position_id = ? ORDER BY executed_at",
            (position_id,),
        )
        return [dict(r) for r in rows]

    # ─── Signals ─────────────────────────────────────────────

    async def insert_signal(self, s: Signal):
        await self.db.execute(
            """INSERT INTO signals
               (symbol, signal_type, signal_value, direction, confidence, source)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (s.symbol, s.signal_type, s.signal_value, s.direction, s.confidence, s.source),
        )
        await self.db.commit()

    async def get_recent_signals(self, symbol: str, limit: int = 50) -> List[dict]:
        rows = await self.db.fetchall(
            "SELECT * FROM signals WHERE symbol = ? ORDER BY timestamp DESC LIMIT ?",
            (symbol, limit),
        )
        return [dict(r) for r in rows]

    # ─── News ────────────────────────────────────────────────

    async def insert_news(self, n: NewsItem):
        await self.db.execute(
            """INSERT INTO news
               (title, source, url, published_at, sentiment_vader,
                sentiment_finbert, sentiment_label, impact_level, content_summary)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (n.title, n.source, n.url, n.published_at, n.sentiment_vader,
             n.sentiment_finbert, n.sentiment_label, n.impact_level, n.content_summary),
        )
        await self.db.commit()

    async def get_recent_news(self, limit: int = 50) -> List[dict]:
        rows = await self.db.fetchall(
            "SELECT * FROM news ORDER BY fetched_at DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in rows]

    async def news_exists(self, title: str, source: str) -> bool:
        row = await self.db.fetchone(
            "SELECT id FROM news WHERE title = ? AND source = ? LIMIT 1",
            (title, source),
        )
        return row is not None

    # ─── Agent Logs ──────────────────────────────────────────

    async def insert_agent_log(self, log: AgentLog):
        await self.db.execute(
            """INSERT INTO agent_logs
               (agent_name, action, reasoning, input_data, output_data)
               VALUES (?, ?, ?, ?, ?)""",
            (log.agent_name, log.action, log.reasoning, log.input_data, log.output_data),
        )
        await self.db.commit()

    async def get_agent_logs(self, agent_name: str = None, limit: int = 100) -> List[dict]:
        if agent_name:
            rows = await self.db.fetchall(
                "SELECT * FROM agent_logs WHERE agent_name = ? ORDER BY timestamp DESC LIMIT ?",
                (agent_name, limit),
            )
        else:
            rows = await self.db.fetchall(
                "SELECT * FROM agent_logs ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            )
        return [dict(r) for r in rows]

    # ─── Account ─────────────────────────────────────────────

    async def get_account(self) -> Optional[dict]:
        row = await self.db.fetchone("SELECT * FROM account ORDER BY id DESC LIMIT 1")
        return dict(row) if row else None

    async def init_account(self, initial_balance: float):
        existing = await self.get_account()
        if existing:
            return
        await self.db.execute(
            """INSERT INTO account
               (balance, initial_balance, peak_balance)
               VALUES (?, ?, ?)""",
            (initial_balance, initial_balance, initial_balance),
        )
        await self.db.commit()
        logger.info(f"Akun diinisialisasi — saldo awal: {initial_balance} USDT")

    async def update_account(
        self, balance: float, total_pnl: float,
        total_trades: int, winning_trades: int, losing_trades: int,
        max_drawdown: float, peak_balance: float,
        sharpe_ratio: float = None, profit_factor: float = None,
    ):
        await self.db.execute(
            """UPDATE account SET
               balance = ?, total_pnl = ?, total_trades = ?,
               winning_trades = ?, losing_trades = ?,
               max_drawdown = ?, peak_balance = ?,
               sharpe_ratio = ?, profit_factor = ?,
               updated_at = datetime('now')
               WHERE id = (SELECT MAX(id) FROM account)""",
            (balance, total_pnl, total_trades, winning_trades, losing_trades,
             max_drawdown, peak_balance, sharpe_ratio, profit_factor),
        )
        await self.db.commit()

    async def update_account_stats(
        self,
        total_pnl: float,
        total_trades: int,
        winning_trades: int,
        losing_trades: int,
        profit_factor: float = None,
        max_drawdown: float = None,
        sharpe_ratio: float = None,
    ):
        """
        Perbarui statistik performa TANPA menyentuh kolom `balance`.

        Dipisah dari `update_account` karena saldo harus berubah hanya lewat
        `apply_balance_delta` (mutasi atomik). Statistik boleh ditulis
        who've dari nilai hitungan ulang kapan saja.
        """
        sets = [
            "total_pnl = ?", "total_trades = ?",
            "winning_trades = ?", "losing_trades = ?",
            "updated_at = datetime('now')",
        ]
        params = [total_pnl, total_trades, winning_trades, losing_trades]
        if profit_factor is not None:
            sets.insert(0, "profit_factor = ?")
            params.insert(0, profit_factor)
        if sharpe_ratio is not None:
            sets.insert(0, "sharpe_ratio = ?")
            params.insert(0, sharpe_ratio)
        if max_drawdown is not None:
            sets.insert(0, "max_drawdown = ?")
            params.insert(0, max_drawdown)
        await self.db.execute(
            f"UPDATE account SET {', '.join(sets)} "
            "WHERE id = (SELECT MAX(id) FROM account)",
            tuple(params),
        )
        await self.db.commit()

    async def update_balance(self, new_balance: float):
        await self.db.execute(
            """UPDATE account SET balance = ?, updated_at = datetime('now')
               WHERE id = (SELECT MAX(id) FROM account)""",
            (new_balance,),
        )
        await self.db.commit()

    async def apply_balance_delta(self, delta: float) -> float:
        """
        Terapkan mutasi saldo secara ATOMIK dan kembalikan saldo baru.

        `update_balance` menulis nilai absolut hasil hitungan read-modify-write.
        Dua penulis yang konkuren (mis. scheduler decision_agent vs
        _execution_loop) bisa sama-sama membaca saldo yang sama lalu
        menimpa perubahan satu sama lain — margin dan fee hilang diam-diam.

        SQLite menSerialisasi `balance = balance + ?` di dalam satu
        pernyataan, jadi tidak ada celah baca-tulis di antaranya.
        """
        await self.db.execute(
            """UPDATE account
               SET balance = balance + ?, updated_at = datetime('now')
               WHERE id = (SELECT MAX(id) FROM account)""",
            (float(delta),),
        )
        await self.db.commit()
        row = await self.db.fetchone(
            "SELECT balance FROM account ORDER BY id DESC LIMIT 1"
        )
        return float(row["balance"]) if row else 0.0

    async def bump_peak_balance(self, candidate: float) -> float:
        """Naikkan peak_balance hanya bila kandidat lebih tinggi (atomik)."""
        await self.db.execute(
            """UPDATE account
               SET peak_balance = MAX(COALESCE(peak_balance, 0), ?)
               WHERE id = (SELECT MAX(id) FROM account)""",
            (float(candidate),),
        )
        await self.db.commit()
        row = await self.db.fetchone(
            "SELECT peak_balance FROM account ORDER BY id DESC LIMIT 1"
        )
        return float(row["peak_balance"]) if row and row["peak_balance"] else 0.0

    # ─── Balance History ─────────────────────────────────────

    async def insert_balance_snapshot(self, snap: BalanceSnapshot):
        await self.db.execute(
            """INSERT INTO balance_history (balance, unrealized_pnl, equity)
               VALUES (?, ?, ?)""",
            (snap.balance, snap.unrealized_pnl, snap.equity),
        )
        await self.db.commit()

    async def get_balance_history(self, limit: int = 1000) -> List[dict]:
        rows = await self.db.fetchall(
            "SELECT * FROM balance_history ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in rows]

    # ─── Direction Snapshots (ensemble LONG/SHORT) ──────────

    async def insert_direction_snapshot(self, snap: dict):
        """
        Simpan satu snapshot arah dari ensemble.

        `agent_breakdown` dan `diffusion` sudah berupa JSON string dari
        pemanggil — repository ini tidak melakukan serialisasi supaya
        pemanggil bebas memilih apa yang perlu disimpan.
        """
        await self.db.execute(
            """INSERT INTO direction_snapshots
               (symbol, prob_long, prob_short, direction, confidence,
                z_composite, agent_breakdown, diffusion)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                snap["symbol"],
                float(snap["prob_long"]),
                float(snap["prob_short"]),
                snap["direction"],
                float(snap["confidence"]),
                float(snap.get("z_composite") or 0.0),
                snap.get("agent_breakdown"),
                snap.get("diffusion"),
            ),
        )
        await self.db.commit()

    async def get_latest_direction_snapshot(self, symbol: str) -> Optional[dict]:
        """Snapshot arah terbaru untuk satu simbol, atau None bila belum ada."""
        row = await self.db.fetchone(
            "SELECT * FROM direction_snapshots WHERE symbol = ? "
            "ORDER BY id DESC LIMIT 1",
            (symbol,),
        )
        return dict(row) if row else None

    async def get_latest_direction_snapshots(self) -> List[dict]:
        """Snapshot arah terbaru untuk SEMUA simbol (satu baris per simbol)."""
        rows = await self.db.fetchall(
            "SELECT s.* FROM direction_snapshots s "
            "JOIN (SELECT symbol, MAX(id) AS mx FROM direction_snapshots "
            "      GROUP BY symbol) latest "
            "ON s.id = latest.mx"
        )
        return [dict(r) for r in rows]

    async def prune_direction_snapshots(self, keep_per_symbol: int = 120):
        """
        Buang snapshot lama, sisakan N terbaru per simbol.

        Tanpa prune, tabel tumbuh tanpa batas pada interval 5 detik × 10
        simbol — 172.800 baris per hari.
        """
        await self.db.execute(
            """DELETE FROM direction_snapshots
               WHERE id NOT IN (
                   SELECT id FROM (
                       SELECT id, ROW_NUMBER() OVER (
                           PARTITION BY symbol ORDER BY id DESC
                       ) AS rn
                       FROM direction_snapshots
                   ) WHERE rn <= ?
               )""",
            (int(keep_per_symbol),),
        )
        await self.db.commit()

    async def get_daily_realized_pnl(self) -> float:
        """
        Total PnL terealisasi HARI INI (UTC), lintas simbol.

        Dipakai circuit breaker `max_daily_loss`. Batasnya harian, jadi PnL
        kumulatif sepanjang umur akun tidak boleh dipakai di sini: akun yang
        sudah rugi besar sejak minggu lalu tidak boleh otomatis berhenti
        bertransaksi hanya karena totalrunning-nya jelek.

        `closed_at` disimpan sebagai `datetime('now')`, yaitu UTC — jadi
        `date('now')` di SQLite juga UTC dan kedua sisi konsisten tanpa
        konversi timezone. Return 0.0 (bukan None) bila belum ada posisi
        tertutup: nol kerugian harian adalah nilai yang valid, bukan data absen.
        """
        row = await self.db.fetchone(
            """SELECT COALESCE(SUM(realized_pnl), 0) AS pnl
               FROM positions
               WHERE status IN ('CLOSED', 'LIQUIDATED')
                 AND realized_pnl IS NOT NULL
                 AND date(closed_at) = date('now')"""
        )
        return float(row["pnl"]) if row and row["pnl"] is not None else 0.0

    # ─── Macro Data ──────────────────────────────────────────

    async def upsert_macro(self, m: MacroData):
        await self.db.execute(
            """INSERT INTO macro_data (indicator, value, period, source)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(indicator, period) DO UPDATE SET
               value = excluded.value, fetched_at = datetime('now')""",
            (m.indicator, m.value, m.period, m.source),
        )
        await self.db.commit()

    async def get_macro_latest(self, indicator: str) -> Optional[dict]:
        row = await self.db.fetchone(
            "SELECT * FROM macro_data WHERE indicator = ? ORDER BY fetched_at DESC LIMIT 1",
            (indicator,),
        )
        return dict(row) if row else None

    async def get_all_macro(self) -> List[dict]:
        rows = await self.db.fetchall(
            "SELECT * FROM macro_data ORDER BY fetched_at DESC"
        )
        return [dict(r) for r in rows]

    # ─── Statistik ───────────────────────────────────────────

    async def get_trade_stats(self, mode: str = None) -> dict:
        """
        Hitung statistik performa dari trade yang sudah ditutup.

        `mode` memfilter 'paper' atau 'live'. Tanpa filter, win rate dan
        profit factor menggabungkan simulasi dengan uang sungguhan, dan
        kedua angka itu jadi tidak berarti apa pun - profit simulasi
        menutupi loss bursa, atau sebaliknya.
        """
        sql = (
            "SELECT realized_pnl FROM positions "
            "WHERE status IN ('CLOSED', 'LIQUIDATED') "
            "AND realized_pnl IS NOT NULL"
        )
        params = ()
        if mode:
            sql += " AND mode = ?"
            params = (mode,)
        closed = await self.db.fetchall(sql, params)
        if not closed:
            return {
                "total_trades": 0, "winning_trades": 0, "losing_trades": 0,
                "win_rate": 0, "total_pnl": 0, "avg_pnl": 0,
                "gross_profit": 0, "gross_loss": 0, "profit_factor": 0,
            }

        pnls = [r["realized_pnl"] for r in closed]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))

        return {
            "total_trades": len(pnls),
            "winning_trades": len(wins),
            "losing_trades": len(losses),
            "win_rate": len(wins) / len(pnls) if pnls else 0,
            "total_pnl": sum(pnls),
            "avg_pnl": sum(pnls) / len(pnls) if pnls else 0,
            "gross_profit": gross_profit,
            "gross_loss": gross_loss,
            "profit_factor": gross_profit / gross_loss if gross_loss > 0 else float("inf"),
        }
