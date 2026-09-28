"""
tests/test_position_manager.py — Pengujian manajemen posisi, stop loss, take profit, dan likuidasi.
"""

import asyncio
import os
import unittest

from core.event_bus import EventBus
from database.db import Database, close_db
import database.db as db_module
from database.repository import Repository
from trading.risk_manager import RiskManager
from trading.position_manager import PositionManager


class TestPositionManager(unittest.IsolatedAsyncioTestCase):
    """Pengujian lifecycle posisi dan pemicu otomatis SL/TP/Likuidasi."""

    async def asyncSetUp(self):
        self.test_db_path = "data_store/test_pos_mgr.db"
        if os.path.exists(self.test_db_path):
            try:
                os.remove(self.test_db_path)
            except OSError:
                pass

        self.db = Database(db_path=self.test_db_path)
        await self.db.connect()
        db_module._db = self.db

        self.repo = Repository(self.db)
        await self.repo.init_account(10000.0)

        self.event_bus = EventBus()
        self.risk_manager = RiskManager()
        self.pm = PositionManager(self.event_bus, self.risk_manager)

    async def asyncTearDown(self):
        await close_db()
        if os.path.exists(self.test_db_path):
            try:
                os.remove(self.test_db_path)
            except OSError:
                pass

    async def test_open_position(self):
        """Uji pembukaan posisi Long dan validasi field tersimpan."""
        pos_id = await self.pm.open_position(
            symbol="BTC/USDT:USDT",
            side="LONG",
            entry_price=50000.0,
            quantity=0.2,
            leverage=10,
            stop_loss=48000.0,
            take_profit=54000.0,
            reasoning="Tes buka posisi",
        )
        self.assertIsNotNone(pos_id)

        positions = await self.repo.get_open_positions()
        self.assertEqual(len(positions), 1)
        p = positions[0]
        self.assertEqual(p["side"], "LONG")
        self.assertEqual(p["status"], "OPEN")
        # Liq price = 50000 * (1 - 0.1 + 0.004) = 45200.0
        self.assertAlmostEqual(p["liquidation_price"], 45200.0, places=1)

    async def test_stop_loss_trigger(self):
        """Uji eksekusi otomatis penutupan posisi saat Stop Loss tersentuh."""
        pos_id = await self.pm.open_position(
            symbol="BTC/USDT:USDT",
            side="LONG",
            entry_price=50000.0,
            quantity=0.1,
            leverage=10,
            stop_loss=48000.0,
            take_profit=54000.0,
        )

        # Harga turun ke 47500 (di bawah SL 48000)
        prices = {"BTC/USDT:USDT": 47500.0}
        await self.pm.update_positions(prices)

        # Posisi seharusnya ditutup dengan alasan SL_HIT
        open_pos = await self.repo.get_open_positions()
        self.assertEqual(len(open_pos), 0)

        all_pos = await self.repo.get_all_positions()
        closed_p = all_pos[0]
        self.assertEqual(closed_p["status"], "CLOSED")
        self.assertIn("SL_HIT", closed_p["close_reason"])
        self.assertLess(closed_p["realized_pnl"], 0)

    async def test_take_profit_trigger(self):
        """Uji eksekusi otomatis penutupan posisi saat Take Profit tersentuh."""
        pos_id = await self.pm.open_position(
            symbol="BTC/USDT:USDT",
            side="LONG",
            entry_price=50000.0,
            quantity=0.1,
            leverage=10,
            stop_loss=48000.0,
            take_profit=54000.0,
        )

        # Harga naik ke 54500 (di atas TP 54000)
        prices = {"BTC/USDT:USDT": 54500.0}
        await self.pm.update_positions(prices)

        open_pos = await self.repo.get_open_positions()
        self.assertEqual(len(open_pos), 0)

        all_pos = await self.repo.get_all_positions()
        closed_p = all_pos[0]
        self.assertEqual(closed_p["status"], "CLOSED")
        self.assertIn("TP_HIT", closed_p["close_reason"])
        self.assertGreater(closed_p["realized_pnl"], 0)

    async def test_liquidation_trigger(self):
        """Uji likuidasi otomatis saat harga menembus harga likuidasi."""
        # Entry 50000, lev 10x -> Liq = 45200.0
        pos_id = await self.pm.open_position(
            symbol="BTC/USDT:USDT",
            side="LONG",
            entry_price=50000.0,
            quantity=0.1,
            leverage=10,
            stop_loss=None,
            take_profit=None,
        )

        # Harga jatuh drastis ke 45000 (di bawah liq 45200)
        prices = {"BTC/USDT:USDT": 45000.0}
        await self.pm.update_positions(prices)

        open_pos = await self.repo.get_open_positions()
        self.assertEqual(len(open_pos), 0)

        all_pos = await self.repo.get_all_positions()
        liq_p = all_pos[0]
        self.assertEqual(liq_p["status"], "LIQUIDATED")
        self.assertEqual(liq_p["close_reason"], "LIQUIDATED")
        # Margin yang hilang sama dengan initial margin
        self.assertAlmostEqual(liq_p["realized_pnl"], -liq_p["margin"])

    async def test_concurrent_open_keeps_balance_consistent(self):
        """
        Regression: mutasi saldo yang konkuren tidak boleh saling menimpa.

        `open_position` dulu menulis saldo absolut hasil read-modify-write.
        Beberapa pembukaan yang jalan bersamaan membaca saldo yang sama lalu
        menimpa perubahan masing-masing, sehingga margin + fee hilang diam-diam
        (terukur 49.30 USDT hilang pada satu sesi). Saldo harus bisa
        direkonstruksi persis dari mutasi yang tercatat.
        """
        await asyncio.gather(*[
            self.pm.open_position(
                symbol="BTC/USDT:USDT",
                side="LONG",
                entry_price=100.0,
                quantity=0.5,
                leverage=10,
                stop_loss=99.0,
                take_profit=102.0,
            )
            for _ in range(10)
        ])

        account = await self.repo.get_account()
        positions = await self.repo.get_all_positions()
        open_fee = (await self.db.fetchone(
            "SELECT COALESCE(SUM(fee), 0) AS f FROM trades WHERE trade_type = 'OPEN'"
        ))["f"]
        total_margin = sum(p["margin"] for p in positions)

        expected = 10000.0 - open_fee - total_margin
        self.assertAlmostEqual(
            account["balance"], expected, places=4,
            msg="Saldo tidak cocok dengan margin - fee yang tercatat",
        )

    async def test_concurrent_close_only_pays_margin_once(self):
        """
        Regression: satu posisi hanya boleh membayar margin satu kali.

        Enam pemanggil penutup berebut posisi yang sama. Tanpa klaim
        `status = 'OPEN'` di dalam WHERE, semuanya bisa melihat posisi OPEN dan
        mengembalikan margin berulang.
        """
        pos_id = await self.pm.open_position(
            symbol="BTC/USDT:USDT",
            side="LONG",
            entry_price=100.0,
            quantity=0.5,
            leverage=10,
            stop_loss=99.0,
            take_profit=102.0,
        )

        results = await asyncio.gather(*[
            self.pm.close_position(pos_id, 101.0, f"RACER_{i}")
            for i in range(6)
        ])
        winners = [r for r in results if r]
        self.assertEqual(len(winners), 1, "Lebih dari satu penutup membayarkan margin")

        close_trades = (await self.db.fetchone(
            "SELECT COUNT(*) AS n FROM trades "
            "WHERE position_id = ? AND trade_type = 'CLOSE'",
            (pos_id,),
        ))["n"]
        self.assertEqual(close_trades, 1, "Trade CLOSE tercatat lebih dari sekali")


if __name__ == "__main__":
    unittest.main()
