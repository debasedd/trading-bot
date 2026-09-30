"""
tests/test_fill_price_sl.py — SL/TP harus dihitung dari harga fill, bukan
harga yang lihat lebih awal.

Bug yang diperbaiki: `ExecutionAgent.think()` menghitung SL/TP dari harga P1,
lalu `PaperTradingEngine._execute_open()` memakai `order.stop_loss` apa adanya
padahal fill terjadi di P2. Kalau P2 sudah melewati stop berbasis P1, posisi
langsung kena SL di detik pertama. Di log live ada posisi dengan durasi 0-6
detik yang seluruhnya berakhir SL_HIT.

CATATAN tentang harga fill: `FILL` di bawah adalah harga PASAR yang dilihat
engine, BUKAN harga yang dicatat di `positions.entry_price`. Sejak model
biaya menyeberang dipasang, fill LONG terjadi di `FILL * (1 + total_cost)`
dan fill SHORT di `FILL * (1 - total_cost)`. Semua assertion di file ini
dihitung relatif terhadap `p["entry_price"]` yang sebenarnya, bukan terhadap
`FILL` — kalau tidak, test ini akan mengunci kembali asumsi tanpa gesekan
yang sedang diperbaiki.
"""
import os
import unittest

import database.db as db_module
from core.config import get_config
from core.event_bus import EventBus
from database.db import Database, close_db
from database.repository import Repository
from trading.models import Order, TradeAction
from trading.paper_engine import (
    FILL_HALF_SPREAD_FLOOR,
    FILL_IMPACT_FLOOR,
    PaperTradingEngine,
)

FILL = 50000.0

# Tanpa order book di store, spread selalu jatuh ke lantai.
EXPECTED_COST = FILL_HALF_SPREAD_FLOOR + FILL_IMPACT_FLOOR


class TestFillPriceSl(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.test_db_path = "data_store/test_fill_sl_%d.db" % os.getpid()
        for suffix in ("", "-wal", "-shm"):
            p = self.test_db_path + suffix
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass

        self.db = Database(db_path=self.test_db_path)
        await self.db.connect()
        db_module._db = self.db

        self.repo = Repository(self.db)
        await self.repo.init_account(10000.0)

        self.engine = PaperTradingEngine(EventBus())
        await self.engine.initialize()
        self.config = get_config()
        self.scalp = self.config.scalping

    async def asyncTearDown(self):
        await close_db()
        for suffix in ("", "-wal", "-shm"):
            p = self.test_db_path + suffix
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass

    def _order(self, action=TradeAction.OPEN_LONG, side="LONG"):
        return Order(
            symbol="BTC/USDT:USDT",
            action=action,
            side=side,
            leverage=self.config.risk.default_leverage,
        )

    async def test_sl_and_tp_derived_from_fill_price(self):
        """SL/TP persis `tight_sl_pct` / `fast_tp_pct` dari harga fill."""
        self.engine.update_price("BTC/USDT:USDT", FILL)

        result = await self.engine.execute_order(self._order())
        self.assertTrue(result["success"], result["message"])

        p = (await self.repo.get_open_positions())[0]

        # Fill LONG di atas harga pasar: pembeli taker membayar ask, bukan
        # mid. Ini bukan pembulatan — assertion lama justru mengunci harga
        # fill == harga pasar, yaitu asumsi tanpa gesekan yang sedang
        # dibongkar.
        self.assertAlmostEqual(
            p["entry_price"], FILL * (1 + EXPECTED_COST), places=6,
        )
        self.assertGreater(p["entry_price"], FILL)

        # SL/TP relatif terhadap fill SEBENARNYA, bukan terhadap `FILL`.
        self.assertAlmostEqual(
            p["stop_loss"], p["entry_price"] * (1 - self.scalp.tight_sl_pct),
            places=2,
        )
        self.assertAlmostEqual(
            p["take_profit"], p["entry_price"] * (1 + self.scalp.fast_tp_pct),
            places=2,
        )

    async def test_long_precomputed_stops_are_ignored(self):
        """
        SL/TP yang sudah dihitung agen dari harga lain harus TIDAK dipakai.

        Agen menaruh SL di ATAS harga fill LONG — kalau dipakai apa adanya,
        posisi baru langsung "di bawah stop" sejak detik pertama.
        """
        self.engine.update_price("BTC/USDT:USDT", FILL)

        order = self._order()
        order.stop_loss = FILL * 1.02
        order.take_profit = FILL * 1.05

        result = await self.engine.execute_order(order)
        self.assertTrue(result["success"], result["message"])

        p = (await self.repo.get_open_positions())[0]
        self.assertLess(p["stop_loss"], p["entry_price"], "SL LONG harus di bawah entry")
        self.assertGreater(p["take_profit"], p["entry_price"], "TP LONG harus di atas entry")
        self.assertAlmostEqual(
            p["stop_loss"], p["entry_price"] * (1 - self.scalp.tight_sl_pct),
            places=2,
        )
        self.assertAlmostEqual(
            p["take_profit"], p["entry_price"] * (1 + self.scalp.fast_tp_pct),
            places=2,
        )

    async def test_short_precomputed_stops_are_ignored(self):
        """Symetris untuk SHORT: SL di atas entry, TP di bawah entry."""
        self.engine.update_price("BTC/USDT:USDT", FILL)

        order = self._order(TradeAction.OPEN_SHORT, "SHORT")
        order.stop_loss = FILL * 0.98
        order.take_profit = FILL * 0.95

        result = await self.engine.execute_order(order)
        self.assertTrue(result["success"], result["message"])

        p = (await self.repo.get_open_positions())[0]
        self.assertGreater(p["stop_loss"], p["entry_price"], "SL SHORT harus di atas entry")
        self.assertLess(p["take_profit"], p["entry_price"], "TP SHORT harus di bawah entry")

        # Fill SHORT di BAWAH harga pasar: penjual taker menerima bid.
        self.assertAlmostEqual(
            p["entry_price"], FILL * (1 - EXPECTED_COST), places=6,
        )
        self.assertLess(p["entry_price"], FILL)
        self.assertAlmostEqual(
            p["stop_loss"], p["entry_price"] * (1 + self.scalp.tight_sl_pct),
            places=2,
        )
        self.assertAlmostEqual(
            p["take_profit"], p["entry_price"] * (1 - self.scalp.fast_tp_pct),
            places=2,
        )

    async def test_risk_distance_matches_announced_pct(self):
        """
        Jarak risiko harus sama dengan yang diumumkan di config, untuk kedua
        arah. Ini yang menentukan apakah kurva equity hasil simulasi berarti
        atau hanya ilustrasi.
        """
        for action, side in (
            (TradeAction.OPEN_LONG, "LONG"),
            (TradeAction.OPEN_SHORT, "SHORT"),
        ):
            await close_db()
            self.db = Database(db_path=self.test_db_path)
            await self.db.connect()
            db_module._db = self.db
            self.repo = Repository(self.db)
            await self.repo.init_account(10000.0)
            self.engine = PaperTradingEngine(EventBus())
            await self.engine.initialize()

            self.engine.update_price("BTC/USDT:USDT", FILL)
            result = await self.engine.execute_order(self._order(action, side))
            self.assertTrue(result["success"], result["message"])

            p = (await self.repo.get_open_positions())[0]
            risk = abs(p["entry_price"] - p["stop_loss"]) / p["entry_price"]
            reward = abs(p["take_profit"] - p["entry_price"]) / p["entry_price"]

            self.assertAlmostEqual(risk, self.scalp.tight_sl_pct, places=6)
            self.assertAlmostEqual(reward, self.scalp.fast_tp_pct, places=6)


if __name__ == "__main__":
    unittest.main()
