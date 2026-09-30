"""
tests/test_paper_engine.py — Pengujian eksekusi simulasi Paper Trading Engine.
"""

import os
import unittest
from pathlib import Path

from core.event_bus import EventBus
from core.config import get_config
from database.db import Database, close_db
import database.db as db_module
from database.repository import Repository
from trading.paper_engine import (
    FILL_HALF_SPREAD_FLOOR,
    FILL_IMPACT_FLOOR,
    PaperTradingEngine,
)
from trading.models import Order, TradeAction, Side

# Fill LONG dibebankan di atas harga pasar: half-spread + impact.
# Nilai ini bukan tebakan test — ia konstanta yang dipakai engine, supaya
# test ini menguji Round trip UTUH (biaya masuk + biaya keluar), bukan
# saldo yang kebetulan cocok.
FILL_COST = FILL_HALF_SPREAD_FLOOR + FILL_IMPACT_FLOOR


class TestPaperTradingEngine(unittest.IsolatedAsyncioTestCase):
    """Pengujian alur eksekusi order paper trading."""

    async def asyncSetUp(self):
        # Gunakan database terpisah untuk pengujian
        self.test_db_path = "data_store/test_trading_engine_%d.db" % os.getpid()
        if os.path.exists(self.test_db_path):
            try:
                os.remove(self.test_db_path)
            except OSError:
                pass

        # Ganti singleton database
        self.db = Database(db_path=self.test_db_path)
        await self.db.connect()
        db_module._db = self.db

        self.event_bus = EventBus()
        self.engine = PaperTradingEngine(self.event_bus)
        await self.engine.initialize()

        # Update cache harga
        self.engine.update_price("BTC/USDT:USDT", 60000.0)
        self.engine.update_price("ETH/USDT:USDT", 3000.0)

    async def asyncTearDown(self):
        await close_db()
        if os.path.exists(self.test_db_path):
            try:
                os.remove(self.test_db_path)
            except OSError:
                pass

    async def test_initial_account(self):
        """Uji saldo awal akun virtual."""
        summary = await self.engine.get_account_summary()
        self.assertEqual(summary["balance"], 10000.0)
        self.assertEqual(summary["equity"], 10000.0)
        self.assertEqual(summary["open_positions"], 0)

    async def test_execute_open_long(self):
        """Uji eksekusi pembukaan posisi LONG."""
        order = Order(
            symbol="BTC/USDT:USDT",
            action=TradeAction.OPEN_LONG,
            quantity=0.1,
            leverage=10,
            stop_loss=58000.0,
            take_profit=64000.0,
            reasoning="Sinyal konfirmasi teknikal bullish",
        )

        res = await self.engine.execute_order(order)
        self.assertTrue(res["success"])
        self.assertIsNotNone(res["position_id"])

        pos_id = res["position_id"]
        repo = await self.engine._get_repo()
        positions = await repo.get_open_positions()

        self.assertEqual(len(positions), 1)
        p = positions[0]
        self.assertEqual(p["id"], pos_id)
        self.assertEqual(p["symbol"], "BTC/USDT:USDT")
        self.assertEqual(p["side"], "LONG")

        # Harga fill BUKAN harga feed. Taker LONG membayar di atas mid, jadi
        # `entry_price` harus di atas 60000 — assertion lama yang membandingkan
        # dengan 60000.0 mengunci asumsi tanpa gesekan.
        entry = 60000.0 * (1 + FILL_COST)
        self.assertAlmostEqual(p["entry_price"], entry, places=6)

        # Margin = (0.1 * entry) / 10, dihitung dari harga fill.
        margin = 0.1 * entry / 10
        self.assertAlmostEqual(p["margin"], margin, places=2)

        # Cek saldo akun berkurang oleh margin + fee, KEDUA-duanya dihitung
        # dari harga fill — kalau fee dihitung dari harga pasar, gerbang validasi
        # dan `open_position` akan memakai dua angka berbeda.
        account = await repo.get_account()
        fee = self.engine.risk_manager.calculate_fee(0.1, entry, 'TAKER')
        expected_balance = 10000.0 - margin - fee
        self.assertAlmostEqual(account["balance"], expected_balance, places=2)

    async def test_execute_open_and_close(self):
        """Uji alur buka dan tutup posisi dengan keuntungan."""
        # 1. Buka LONG @ 60000
        order_open = Order(
            symbol="BTC/USDT:USDT",
            action=TradeAction.OPEN_LONG,
            quantity=0.1,
            leverage=10,
            stop_loss=58000.0,
            take_profit=64000.0,
        )
        res_open = await self.engine.execute_order(order_open)
        pos_id = res_open["position_id"]

        # 2. Harga naik ke 62000
        self.engine.update_price("BTC/USDT:USDT", 62000.0)

        # 3. Tutup posisi
        order_close = Order(
            symbol="BTC/USDT:USDT",
            action=TradeAction.CLOSE,
            position_id=pos_id,
        )
        res_close = await self.engine.execute_order(order_close)
        self.assertTrue(res_close["success"])

        # PnL harus NET dari KEDUA sisi, DAN dari biaya menyeberang:
        #   fill open LONG  = 60000 * (1 + c)   -> lebih mahal dari mid
        #   fill close LONG = 62000 * (1 - c)   -> lebih murah dari mid
        #   gross           = (close_fill - open_fill) * 0.1
        #   fee buka        = 0.1 * open_fill  * fee_rate
        #   fee tutup       = 0.1 * close_fill * fee_rate
        #
        # Ini inti dari verifikasi round trip: harga bergerak naik, tapi PnL
        # tetap harus lebih kecil daripada gerakan harga murni, karena taker
        # membayar dua kali. Assertion lama memakai harga feed di kedua
        # tempat, jadi ia hanya menguji fee — dan secara tidak sengaja
        # melegitimasi simulasi tanpa gesekan.
        details = res_close["details"]
        open_fill = 60000.0 * (1 + FILL_COST)
        close_fill = 62000.0 * (1 - FILL_COST)
        gross = (close_fill - open_fill) * 0.1
        fee_open = self.engine.risk_manager.calculate_fee(0.1, open_fill, 'TAKER')
        fee_close = self.engine.risk_manager.calculate_fee(0.1, close_fill, 'TAKER')
        expected_pnl = gross - fee_open - fee_close
        self.assertAlmostEqual(details["realized_pnl"], expected_pnl,
                               places=6, msg="PnL harus NET dua sisi + slippage")
        self.assertAlmostEqual(details["fee"], fee_close, places=8,
                               msg="fee yang dikembalikan adalah fee TUTUP")

        # Bukti langsung bahwa gesekan membebani hasil: perhitungan naif (tanpa
        # slippage) menghasilkan PnL lebih besar pada harga yang sama.
        frictionless = 200.0 - self.engine.risk_manager.calculate_fee(
            0.1, 60000.0, 'TAKER'
        ) - self.engine.risk_manager.calculate_fee(0.1, 62000.0, 'TAKER')
        self.assertLess(expected_pnl, frictionless,
                        "biaya menyeberang harus mengurangi PnL")

        # Saldo akhir = modal awal + PnL bersih dua sisi (margin kembali utuh)
        #             = 10000 + expected_pnl
        # Perhatikan: margin kembali utuh, PnL sudah bersih fee DAN slippage.
        repo = await self.engine._get_repo()
        account = await repo.get_account()
        self.assertAlmostEqual(account["balance"],
                               10000.0 + expected_pnl, places=6)

    async def test_hold_action(self):
        """Uji aksi HOLD tidak mengubah posisi atau saldo."""
        order_hold = Order(
            symbol="BTC/USDT:USDT",
            action=TradeAction.HOLD,
            reasoning="Pasar sideways",
        )
        res = await self.engine.execute_order(order_hold)
        self.assertTrue(res["success"])
        self.assertIn("HOLD", res["message"])


if __name__ == "__main__":
    unittest.main()
