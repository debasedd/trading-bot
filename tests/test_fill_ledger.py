"""
Item (b) bagian kedua: fill bursa harus benar-benar MENCATAT ke ledger.

Test di `test_fill_reconciliation.py` membuktikan fill terbaca. Test di
sini membuktikan fill itu sampai ke SQLite -- dan ke
`DAILY_LOSS_LIMIT`, yang sebelumnya tidak punya sumber angka sama sekali.

Kenapa perlu test DB
--------------------
`record_realized_pnl()` PANGGILANNYA ada di produksi
(`executor.py:853`) tapi hanya di `_record_close`, yang jalan dari
`_close` / `_LivePositionManager.close_position`. Kedua jalur itu
hanya terpakai kalau ORDER penutup berhasil dikirim dan terisi.

Yang tidak pernah terpakai adalah: SL/TP di bursa fires sendiri,
tanpa order penutup dari Python. Tidak ada kode yang membaca
`userFills`, jadi tidak ada yang menutup baris posisi itu, jadi tidak
ada yang memanggil `record_realized_pnl`.

Akibatnya `Blocker.DAILY_LOSS_LIMIT` (safety.py:361) tidak punya
sumber angka: `counters.realized_pnl` selalu 0.0, jadi batas rugi
harian 50 USDC tidak akan pernah menyala.

Database di sini benar-benar ditulis (SQLite di tempfile), bukan
mock. Yang di-fake hanya bursa-nya, lewat fixture rekaman testnet.
"""
import asyncio
import pathlib
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from hl_live_fixtures import (
    CLEARINGHOUSE_STATE_EMPTY,
    FILL_CLOSE_SHORT_BTC_LOSS,
    FILL_CLOSE_SHORT_BTC_PROFIT,
    FIXTURE_ADDRESS,
    make_info_double,
)
from repro_helpers import clean_gate

from core.config import LiveConfig
from database.db import Database
from database.models import Position
from database.repository import Repository
from trading.live.client import LiveExchange
from trading.live.engine import LiveEngine, LivePosition
from trading.live.executor import LiveExecutor


class _DbHarness(unittest.IsolatedAsyncioTestCase):
    """Database sementara per test; tidak menyentuh trading_bot.db."""

    async def asyncSetUp(self):
        self._dir = tempfile.mkdtemp()
        self._db_path = str(pathlib.Path(self._dir) / "test_fill.db")
        self.db = Database(self._db_path)
        await self.db.connect()
        self.repo = Repository(self.db)
        await self.repo.init_account(10000.0)

    def _executor(self, engine):
        """
        LiveExecutor yang memakai repo test ini.

        `_get_repo()` di produksi memanggil `get_db()` global. Tanpa
        injeksi, test akan menulis ke `data_store/trading_bot.db` --
        bukan ke database sementara. Ini injeksi dependency, bukan stub:
        Repository dan Database adalah kelas produksi yang sebenarnya,
        hanya sumbernya yang diarahkan ke file sementara.
        """
        ex = LiveExecutor(engine, None)
        ex._repo = self.repo
        return ex

    async def asyncTearDown(self):
        await self.db.close()

    def _exchange(self, fills):
        ex = LiveExchange.__new__(LiveExchange)
        ex.testnet = True
        ex._account_address = None
        ex._rules = None
        ex.base_url = "https://api.hyperliquid-testnet.xyz/info"
        ex.info = make_info_double(CLEARINGHOUSE_STATE_EMPTY)
        ex.info._fills = list(fills)
        ex.info._open_orders = []
        ex.exchange = None
        ex.wallet = None
        ex.address = FIXTURE_ADDRESS
        ex.query_address = FIXTURE_ADDRESS
        return ex

    async def _seed_open_position(self, symbol="BTC/USDT:USDT",
                                 side="SHORT", size=0.24567, entry=85110.1,
                                 sl=86000.0, tp=83000.0):
        return await self.repo.insert_position(Position(
            symbol=symbol, side=side, entry_price=entry, quantity=size,
            leverage=5, margin=4180.0, liquidation_price=488199.6,
            stop_loss=sl, take_profit=tp, status="OPEN", mode="live",
        ))

    async def _rows(self, sql, params=()):
        return [dict(r) for r in await self.db.fetchall(sql, params)]


class TestExchangeFillIsRecorded(_DbHarness):
    """Fill CLOSE dari bursa harus menutup baris posisi."""

    async def test_sl_fill_closes_the_position_row(self):
        """
        SL yang fires di bursa harus menutup baris OPEN di SQLite.

        Sebelum item (b): baris tetap OPEN dengan realized_pnl NULL
        selamanya.
        """
        row_id = await self._seed_open_position()
        ex = self._exchange([FILL_CLOSE_SHORT_BTC_LOSS])
        eng = LiveEngine(gate=clean_gate(), exchange=ex, cfg=LiveConfig())
        eng.positions["BTC/USDT:USDT"] = LivePosition(
            symbol="BTC/USDT:USDT", coin="BTC", side="SHORT", size=0.24567,
            entry_price=85110.1, stop_loss=86000.0, take_profit=83000.0,
            leverage=5, sl_order_id=1, tp_order_id=2,
        )
        ex_exec = self._executor(eng)

        recorded = await ex_exec.record_exchange_fills()

        self.assertEqual(
            len(recorded), 1,
            "fill CLOSE harus tercatat; tercatat: %r" % recorded,
        )
        rows = await self._rows(
            "SELECT status, realized_pnl, close_reason, close_price "
            "FROM positions WHERE id = ?", (row_id,))
        self.assertEqual(rows[0]["status"], "CLOSED",
                         "baris posisi harus CLOSED setelah fill bursa")
        self.assertIsNotNone(rows[0]["realized_pnl"])

    async def test_realized_pnl_matches_exchange_closed_pnl(self):
        """
        PnL yang dicatat harus ANGKA BURSA, bukan hasil hitung ulang.

        `closedPnl` di respons bursa sudah net dari fee. Fixture ini
        bernilai "-0.0414". Kalau produksi menghitung ulang dari harga
        entry/close, angkanya akan berbeda -- dan kita tidak akan pernah
        tahufee sebenarnya terpotong atau tidak.
        """
        await self._seed_open_position()
        ex = self._exchange([FILL_CLOSE_SHORT_BTC_LOSS])
        eng = LiveEngine(gate=clean_gate(), exchange=ex, cfg=LiveConfig())
        eng.positions["BTC/USDT:USDT"] = LivePosition(
            symbol="BTC/USDT:USDT", coin="BTC", side="SHORT", size=0.24567,
            entry_price=85110.1, stop_loss=86000.0, take_profit=83000.0,
            leverage=5, sl_order_id=1, tp_order_id=2,
        )
        ex_exec = self._executor(eng)

        await ex_exec.record_exchange_fills()

        rows = await self._rows(
            "SELECT realized_pnl FROM positions WHERE symbol = ?",
            ("BTC/USDT:USDT",))
        self.assertAlmostEqual(
            float(rows[0]["realized_pnl"]), -0.0414, places=9,
            msg="realized_pnl harus sama dengan closedPnl bursa",
        )

    async def test_trade_row_uses_exchange_fee(self):
        """
        Fee di baris `trades` harus dari bursa, bukan `config.fees.taker`.

        Fixture ini punya fee = -0.001763. Dengan tarif taker config
        (0.00045) dan notional fill, fee akan beda -- dan fee yang
        dipakai daily-loss breaker harus yang benar-benar dibayar.
        """
        await self._seed_open_position()
        ex = self._exchange([FILL_CLOSE_SHORT_BTC_LOSS])
        eng = LiveEngine(gate=clean_gate(), exchange=ex, cfg=LiveConfig())
        eng.positions["BTC/USDT:USDT"] = LivePosition(
            symbol="BTC/USDT:USDT", coin="BTC", side="SHORT", size=0.24567,
            entry_price=85110.1, stop_loss=86000.0, take_profit=83000.0,
            leverage=5, sl_order_id=1, tp_order_id=2,
        )
        ex_exec = self._executor(eng)

        await ex_exec.record_exchange_fills()

        trades = await self._rows(
            "SELECT fee, fee_type, trade_type, mode FROM trades "
            "WHERE trade_type = 'CLOSE'")
        self.assertEqual(len(trades), 1, "harus ada satu baris trade CLOSE")
        self.assertAlmostEqual(
            float(trades[0]["fee"]), 0.001763, places=9,
            msg="fee harus dari respons bursa (abs fee), bukan tarif config",
        )
        self.assertEqual(trades[0]["mode"], "live")

    async def test_daily_loss_breaker_receives_the_number(self):
        """
        INI yang paling penting dari item (b).

        `record_realized_pnl()` tidak pernah terpanggil dari fill bursa.
        Akibatnya `Blocker.DAILY_LOSS_LIMIT` tidak punya sumber angka:
        `counters.realized_pnl` selalu 0.0 dan batas 50 USDC tidak akan
        pernah menyala.
        """
        await self._seed_open_position()
        ex = self._exchange([FILL_CLOSE_SHORT_BTC_LOSS])
        gate = clean_gate()
        eng = LiveEngine(gate=gate, exchange=ex, cfg=LiveConfig())
        eng.positions["BTC/USDT:USDT"] = LivePosition(
            symbol="BTC/USDT:USDT", coin="BTC", side="SHORT", size=0.24567,
            entry_price=85110.1, stop_loss=86000.0, take_profit=83000.0,
            leverage=5, sl_order_id=1, tp_order_id=2,
        )
        ex_exec = self._executor(eng)

        self.assertEqual(
            gate.counters.realized_pnl, 0.0,
            "sebelum fill diproses, counter harus 0",
        )
        await ex_exec.record_exchange_fills()
        self.assertNotEqual(
            gate.counters.realized_pnl, 0.0,
            "fill bursa tidak mengisi daily-loss breaker; "
            "Blocker.DAILY_LOSS_LIMIT tidak akan pernah menyala",
        )
        self.assertAlmostEqual(
            gate.counters.realized_pnl, -0.0414, places=9,
        )

    async def test_daily_loss_breaker_can_actually_fire(self):
        """
        Uji ujung: breaker harus benar-benar menyala setelah banyaknya
        fill rugi.

        Loop ini memanggil `record_realized_pnl()` langsung -- sama
        persis dengan yang dilakukan `record_exchange_fills()` setelah
        fill tercatat. Yang diuji di sini adalah APAKAH breaker itu
        menyala, bukan bagaimana PnL sampai ke counter (itu test lain).

        Order terakhir yang dikirim memuat `cloid` dan angka yang sah,
        supaya satu-satunya alasan penolakan adalah DAILY_LOSS_LIMIT.
        Tanpa itu, test lulus karena alasan yang salah -- persis jebakan
        yang sudah hampir menimpa saya di Fase 0.
        """
        from datetime import datetime, timezone

        from trading.live.safety import OrderRequest

        gate = clean_gate()

        # 40 fill rugi, masing-masing -2 USDC -> total -80 USDC
        for _ in range(40):
            gate.record_realized_pnl(-2.0)

        # `now` harus HARI INI UTC, sama dengan `day_utc` yang diisi
        # `clean_gate()`. Kalau tidak, `rollover_if_needed(now)` menganggap
        # ini pergantian hari dan meng-nolkan PnL sebelum blokir dievaluasi
        # -- lalu order lolos dan test ini lulus karena alasan yang salah.
        now = datetime.now(timezone.utc).replace(
            hour=15, minute=0, second=0, microsecond=0)
        self.assertEqual(
            gate.counters.day_utc, now.strftime("%Y-%m-%d"),
            "day_utc dan `now` harus tanggal yang sama",
        )

        self.assertLess(
            gate.counters.realized_pnl, -50.0,
            "total rugi harus melewati batas harian 50 USDC",
        )
        allowed, blockers = gate.can_send(
            OrderRequest(symbol="ETH/USDT:USDT", is_buy=True,
                         size=0.0005, price=2000.0),
            free_collateral=1000.0,
            now=now,
            cloid="tb-test-daily-loss-0001",
            leverage=5,
        )
        self.assertFalse(allowed, "order harus DITOLAK setelah rugi 80 USDC")
        self.assertTrue(
            any("batas kerugian harian" in b.value for b in blockers),
            "blocker yang benar seharusnya muncul; dapat: %r"
            % [b.value for b in blockers],
        )

    async def test_partial_close_uses_exchange_size(self):
        """
        Partial exit di bursa harus dicatat dengan ukuran fill ASLI.

        Fixture `FILL_CLOSE_SHORT_BTC_SMALL_LOSS` menutup 0.00013 dari
        posisi 0.24567. Baris `trades.quantity` harus 0.00013, bukan
        0.24567 -- kalau yang lama, PnL tercatat 1.890x lebih besar.
        """
        from hl_live_fixtures import FILL_CLOSE_SHORT_BTC_SMALL_LOSS

        await self._seed_open_position()
        ex = self._exchange([FILL_CLOSE_SHORT_BTC_SMALL_LOSS])
        eng = LiveEngine(gate=clean_gate(), exchange=ex, cfg=LiveConfig())
        eng.positions["BTC/USDT:USDT"] = LivePosition(
            symbol="BTC/USDT:USDT", coin="BTC", side="SHORT", size=0.24567,
            entry_price=85110.1, stop_loss=86000.0, take_profit=83000.0,
            leverage=5, sl_order_id=1, tp_order_id=2,
        )
        ex_exec = self._executor(eng)

        await ex_exec.record_exchange_fills()

        trades = await self._rows(
            "SELECT quantity FROM trades WHERE trade_type = 'CLOSE'")
        self.assertAlmostEqual(
            float(trades[0]["quantity"]), 0.00013, places=9,
            msg="quantity harus ukuran fill bursa, bukan ukuran posisi",
        )

    async def test_engine_position_removed_after_fill(self):
        """
        `engine.positions` harus mencerminkan kenyataan bursa.

        Kalau tidak, health_check akan tetap melihat posisi yang sudah
        tertutup dan laporan divergensi palsu -- persis yang item (b)
        perbaiki.
        """
        await self._seed_open_position()
        ex = self._exchange([FILL_CLOSE_SHORT_BTC_LOSS])
        eng = LiveEngine(gate=clean_gate(), exchange=ex, cfg=LiveConfig())
        eng.positions["BTC/USDT:USDT"] = LivePosition(
            symbol="BTC/USDT:USDT", coin="BTC", side="SHORT", size=0.24567,
            entry_price=85110.1, stop_loss=86000.0, take_profit=83000.0,
            leverage=5, sl_order_id=1, tp_order_id=2,
        )
        ex_exec = self._executor(eng)

        self.assertIn("BTC/USDT:USDT", eng.positions)
        await ex_exec.record_exchange_fills()
        self.assertNotIn(
            "BTC/USDT:USDT", eng.positions,
            "posisi sudah tertutup di bursa tapi masih tercatat di engine",
        )

    async def test_same_fill_not_recorded_twice(self):
        """
        Dedup: satu fill tidak boleh menutup posisi dua kali.

        Tanpa dedup, PnL terhitung dua kali dan daily-loss breaker
        terpakai dua kali untuk satu kejadian.
        """
        row_id = await self._seed_open_position()
        ex = self._exchange([FILL_CLOSE_SHORT_BTC_PROFIT])
        eng = LiveEngine(gate=clean_gate(), exchange=ex, cfg=LiveConfig())
        eng.positions["BTC/USDT:USDT"] = LivePosition(
            symbol="BTC/USDT:USDT", coin="BTC", side="SHORT", size=0.24567,
            entry_price=85110.1, stop_loss=86000.0, take_profit=83000.0,
            leverage=5, sl_order_id=1, tp_order_id=2,
        )
        ex_exec = self._executor(eng)

        first = await ex_exec.record_exchange_fills()
        second = await ex_exec.record_exchange_fills()

        self.assertEqual(len(first), 1)
        self.assertEqual(
            second, [],
            "fill yang sama diproses lagi -- PnL akan terhitung dua kali",
        )
        trades = await self._rows(
            "SELECT COUNT(*) AS n FROM trades WHERE trade_type = 'CLOSE'")
        self.assertEqual(trades[0]["n"], 1,
                         "harus ada tepat satu baris trade CLOSE")

    async def test_wiring_via_on_exchange_fill_callback(self):
        """
        Jalur yang BENAR-BENAR dipakai produksi: `on_exchange_fill`.

        `run.py::_build_live_executor` menyetel
        `engine.on_exchange_fill = executor.record_exchange_fills`, dan
        `poll_exchange_fills()` memanggilnya dengan DAFTAR FILL sebagai
        argumen.

        Test lain di file ini memanggil `record_exchange_fills()` langsung
        tanpa argumen -- jadi mereka tidak akan menangkap tanda tangan
        yang salah. Bug ini sudah nyata terjadi: fill terbaca, dicatat
        gagal, dan log hanya berbunyi "gagal dicatat" tanpa jejak DB.
        """
        row_id = await self._seed_open_position()
        ex = self._exchange([FILL_CLOSE_SHORT_BTC_LOSS])
        gate = clean_gate()
        eng = LiveEngine(gate=gate, exchange=ex, cfg=LiveConfig())
        eng.positions["BTC/USDT:USDT"] = LivePosition(
            symbol="BTC/USDT:USDT", coin="BTC", side="SHORT", size=0.24567,
            entry_price=85110.1, stop_loss=86000.0, take_profit=83000.0,
            leverage=5, sl_order_id=1, tp_order_id=2,
        )
        ex_exec = self._executor(eng)
        # Persis seperti run.py
        eng.on_exchange_fill = ex_exec.record_exchange_fills

        health = await eng.health_check()

        self.assertTrue(
            health["closed_by_exchange"],
            "health_check harus melaporkan fill yang ditutup bursa",
        )
        rows = await self._rows(
            "SELECT status, realized_pnl FROM positions WHERE id = ?",
            (row_id,))
        self.assertEqual(
            rows[0]["status"], "CLOSED",
            "fill yang lewat on_exchange_fill tidak sampai ke DB",
        )
        self.assertAlmostEqual(
            float(rows[0]["realized_pnl"]), -0.0414, places=9)


if __name__ == "__main__":
    unittest.main()
