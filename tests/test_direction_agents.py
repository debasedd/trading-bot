"""
tests/test_direction_agents.py — Perilaku agen spesialis arah.

Fokus: setiap agen harus BENAR-BENAR abstain ketika datanya tidak ada.
Agen yang mengembalikan z=0/netral padahal buta data akan membuat ensemble
berpikir ada konsensus LONG/SHORT yang sebenarnya tidak ada.
"""

import unittest

from agents.direction_agents import (
    DirectionEnsembleAgent,
    MicrostructureAgent,
    MomentumAgent,
    OrderFlowAgent,
    TechnicalAgent,
    _signed_volume,
)
from core.event_bus import EventBus
from core.market_store import market_store


def _reset_store():
    """Bersihkan state market_store antar test."""
    market_store._order_books.clear()
    market_store._last_prices.clear()
    market_store._price_ts.clear()
    market_store._price_history.clear()
    market_store._recent_trades.clear()
    market_store._funding.clear()
    market_store._open_interest.clear()


class TestAbstention(unittest.TestCase):
    """Data kosong harus menghasilkan abstain, bukan verdict netral."""

    def setUp(self):
        _reset_store()
        self.bus = EventBus()

    def test_orderflow_abstains_without_book(self):
        agent = OrderFlowAgent(self.bus)
        v = agent.evaluate("BTC/USDT:USDT")
        self.assertTrue(v["abstained"])
        self.assertEqual(v["confidence"], 0.0)

    def test_momentum_abstains_without_history(self):
        agent = MomentumAgent(self.bus)
        v = agent.evaluate("BTC/USDT:USDT")
        self.assertTrue(v["abstained"])

    def test_technical_abstains_without_db(self):
        agent = TechnicalAgent(self.bus, "data_store/test_nonexistent.db")
        v = agent.evaluate("BTC/USDT:USDT")
        self.assertTrue(v["abstained"])

    def test_microstructure_abstains_without_data(self):
        agent = MicrostructureAgent(self.bus)
        v = agent.evaluate("BTC/USDT:USDT")
        self.assertTrue(v["abstained"])

    def test_all_agents_handle_unknown_symbol(self):
        """Simbol asing tidak boleh melempar exception."""
        for agent in (
            OrderFlowAgent(self.bus),
            MomentumAgent(self.bus),
            TechnicalAgent(self.bus, "data_store/test_nonexistent.db"),
            MicrostructureAgent(self.bus),
        ):
            v = agent.evaluate("TIDAK/ADA:ADA")
            self.assertIn("abstained", v)
            self.assertIn("direction", v)


class TestOrderFlowAgent(unittest.TestCase):
    def setUp(self):
        _reset_store()
        self.agent = OrderFlowAgent(EventBus())

    def test_bid_heavy_book_points_long(self):
        market_store.set_order_book("BTC/USDT:USDT", {
            "bids": [[100.0, 60.0], [99.9, 40.0]],
            "asks": [[100.1, 10.0], [100.2, 5.0]],
            "timestamp": 0,
        })
        v = self.agent.evaluate("BTC/USDT:USDT")
        self.assertFalse(v["abstained"])
        self.assertEqual(v["direction"], "LONG")
        self.assertGreater(v["factors"]["ofi"], 0)

    def test_ask_heavy_book_points_short(self):
        market_store.set_order_book("BTC/USDT:USDT", {
            "bids": [[100.0, 10.0], [99.9, 5.0]],
            "asks": [[100.1, 60.0], [100.2, 40.0]],
            "timestamp": 0,
        })
        v = self.agent.evaluate("BTC/USDT:USDT")
        self.assertEqual(v["direction"], "SHORT")
        self.assertLess(v["factors"]["ofi"], 0)

    def test_empty_sides_abstain(self):
        market_store._order_books["BTC/USDT:USDT"] = {
            "bids": [], "asks": [], "timestamp": 0,
        }
        v = self.agent.evaluate("BTC/USDT:USDT")
        self.assertTrue(v["abstained"])


class TestMomentumAgent(unittest.TestCase):
    def setUp(self):
        _reset_store()
        self.agent = MomentumAgent(EventBus())

    def test_rising_price_points_long(self):
        for price in (100.0, 100.1, 100.3):
            market_store.set_price("BTC/USDT:USDT", price)
        v = self.agent.evaluate("BTC/USDT:USDT")
        self.assertEqual(v["direction"], "LONG")
        self.assertGreater(v["factors"]["change"], 0)

    def test_falling_price_points_short(self):
        for price in (100.0, 99.9, 99.7):
            market_store.set_price("BTC/USDT:USDT", price)
        v = self.agent.evaluate("BTC/USDT:USDT")
        self.assertEqual(v["direction"], "SHORT")
        self.assertLess(v["factors"]["change"], 0)

    def test_flat_price_is_neutral_not_abstained(self):
        """Harga datar MEMILIKI data — jadi neutral, bukan abstain."""
        for _ in range(3):
            market_store.set_price("BTC/USDT:USDT", 100.0)
        v = self.agent.evaluate("BTC/USDT:USDT")
        self.assertFalse(v["abstained"])
        self.assertEqual(v["direction"], "NEUTRAL")


class TestMicrostructureAgent(unittest.TestCase):
    def setUp(self):
        _reset_store()
        self.agent = MicrostructureAgent(EventBus())

    def _tape(self, sides):
        return [
            {"coin": "BTC", "side": s, "px": "100", "sz": "10", "time": 1}
            for s in sides
        ]

    def test_buy_dominant_tape_points_long(self):
        # 7 buy vs 3 sell -> imbalance jelas positif.
        trades = self._tape(["B"] * 7 + ["A"] * 3)
        market_store.set_recent_trades("BTC/USDT:USDT", trades)
        v = self.agent.evaluate("BTC/USDT:USDT")
        self.assertFalse(v["abstained"])
        self.assertEqual(v["direction"], "LONG")
        self.assertGreater(v["factors"]["tape_imbalance"], 0)

    def test_sell_dominant_tape_points_short(self):
        trades = self._tape(["A", "A", "A", "A", "A", "A", "B", "B", "B", "B"])
        market_store.set_recent_trades("BTC/USDT:USDT", trades)
        v = self.agent.evaluate("BTC/USDT:USDT")
        self.assertEqual(v["direction"], "SHORT")

    def test_short_tape_below_threshold_abstains(self):
        """Tape terlalu sedikit = data tidak cukup, bukan sinyal lemah."""
        market_store.set_recent_trades("BTC/USDT:USDT", self._tape(["B", "B"]))
        v = self.agent.evaluate("BTC/USDT:USDT")
        self.assertTrue(v["abstained"])

    def test_positive_funding_is_contrarian_short(self):
        """
        Funding positif = long ramai membayar. Ini sinyal SHORT.

        Arahnya kontra-sentimen, jadi kalau ini terbalik, agen akan
        justru bergerak searah dengan long yang sudah padat.
        """
        market_store.set_funding("BTC/USDT:USDT", 0.0002)
        v = self.agent.evaluate("BTC/USDT:USDT")
        self.assertFalse(v["abstained"])
        self.assertEqual(v["direction"], "SHORT")

    def test_negative_funding_is_contrarian_long(self):
        market_store.set_funding("BTC/USDT:USDT", -0.0002)
        v = self.agent.evaluate("BTC/USDT:USDT")
        self.assertEqual(v["direction"], "LONG")

    def test_open_interest_is_context_only(self):
        """
        OI tanpa arah tidak boleh jadi vote.

        OI tinggi berarti "banyak posisi", bukan "long" atau "short".
        Menghitungnya sebagai z akan membuat agen berdiri di netral sambil
        terlihat punya sinyal.
        """
        market_store.set_open_interest("BTC/USDT:USDT", 1_000_000.0)
        v = self.agent.evaluate("BTC/USDT:USDT")
        self.assertTrue(v["abstained"])

    def test_signed_volume_ignores_malformed_entries(self):
        tape = [
            {"side": "B", "sz": "10"},
            {"side": "A", "sz": "4"},
            {"side": "???", "sz": "100"},   # tidak dikenal -> diabaikan
            {"side": "B", "sz": "abc"},     # size rusak -> diabaikan
            {"side": "B", "sz": "0"},       # nol -> diabaikan
            "bukan dict",
        ]
        signed, total, buys, sells = _signed_volume(tape)
        self.assertEqual(buys, 1)
        self.assertEqual(sells, 1)
        self.assertAlmostEqual(signed, 6.0)
        self.assertAlmostEqual(total, 14.0)


class TestEnsembleCoordinator(unittest.TestCase):
    def test_only_enabled_agents_are_built(self):
        _reset_store()
        import asyncio

        async def _build():
            from core.config import get_config
            cfg = get_config()
            agent = DirectionEnsembleAgent(
                EventBus(), ["BTC/USDT:USDT"], cfg.database_path
            )
            await agent.initialize()
            return agent

        agent = asyncio.run(_build())
        names = {s.agent_name for s in agent.specialists}
        self.assertEqual(
            names, {"orderflow", "momentum", "technical", "microstructure"}
        )

    def test_disabled_agent_not_constructed(self):
        """
        Agen yang dimatikan di config tidak boleh ikut dibangun.

        Config adalah singleton, jadi perubahan di sini HARUS dikembalikan
        atau akan bocor ke test lain.
        """
        _reset_store()
        import asyncio

        async def _build():
            from core.config import get_config
            cfg = get_config()
            agent = DirectionEnsembleAgent(
                EventBus(), ["BTC/USDT:USDT"], cfg.database_path
            )
            agent.ensemble.microstructure.enabled = False
            await agent.initialize()
            return agent

        try:
            agent = asyncio.run(_build())
            names = {s.agent_name for s in agent.specialists}
            self.assertNotIn("microstructure", names)
            self.assertEqual(
                names, {"orderflow", "momentum", "technical"}
            )
        finally:
            from core.config import get_config
            get_config().ensemble.microstructure.enabled = True


if __name__ == "__main__":
    unittest.main()
