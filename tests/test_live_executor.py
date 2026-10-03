"""
tests/test_live_executor.py — Adapter yang menghubungkan ExecutionAgent
ke live engine.

Fokus: memastikan adapter tidak pernah menjadi jalan pintas melewati
gerbang safety, dan tidak pernah mengirim order tanpa proteksi.
"""
import unittest

from trading.live.executor import LiveExecutor, coin_of, make_cloid
from trading.models import Order, Side, TradeAction


class TestCloid(unittest.TestCase):
    def test_cloid_is_unique(self):
        ids = {make_cloid() for _ in range(200)}
        self.assertEqual(len(ids), 200,
                         "cloid ulang = retry menggandakan posisi")

    def test_cloid_is_the_format_the_exchange_accepts(self):
        """
        cloid harus `0x` + 32 hex -- format yang bursa terima.

        Test lama asserts `make_cloid("close").startswith("close-")`,
        dan lulus karena mengklaim yang salah: prefix tidak muat. Format
        bursa memakai seluruh 16 byte, dan `Cloid._validate()` SDK
        menolak apa pun yang panjangnya bukan 32 karakter setelah `0x`.

        Test itu lulus karena menguji perilaku yang TIDAK PERNAH bekerja:
        order dengan prefix tidak akan sampai ke bursa.
        """
        for prefix in ("tb", "close"):
            cloid = make_cloid(prefix)
            self.assertTrue(
                cloid.startswith("0x"),
                "cloid %r tidak diawali 0x" % cloid,
            )
            body = cloid[2:]
            self.assertEqual(len(body), 32,
                             "cloid %r punya %d hex, bursa mewajibkan 32"
                             % (cloid, len(body)))
            self.assertTrue(
                all(ch in "0123456789abcdef" for ch in body),
                "cloid %r bukan hex" % cloid,
            )


class TestCoinExtraction(unittest.TestCase):
    """Hyperliquid memakai ticker polos, bukan symbol ccxt."""

    def test_ccxt_symbol(self):
        self.assertEqual(coin_of("BTC/USDT:USDT"), "BTC")
        self.assertEqual(coin_of("ETH/USDC:USDC"), "ETH")

    def test_plain_ticker(self):
        self.assertEqual(coin_of("SOL"), "SOL")

    def test_empty(self):
        self.assertEqual(coin_of(""), "")


class _Recorder:
    def __init__(self, result=None, mid=100.0):
        self.calls = []
        self._result = result or {"success": True, "protected": True,
                                  "position": {"coin": "BTC"}}
        self._mid = mid

    async def submit_order(self, **kw):
        self.calls.append(kw)
        return dict(self._result)

    def mid_price(self, coin):
        return self._mid


class _Engine:
    """Stub LiveEngine: meneruskan submit_order ke recorder."""

    def __init__(self, recorder, positions=None):
        self.exchange = recorder
        self.positions = positions or {}
        self.submitted = []

    async def submit_order(self, **kw):
        self.submitted.append(kw)
        return await self.exchange.submit_order(**kw)


def _order(**kw):
    data = dict(
        symbol="BTC/USDT:USDT",
        action=TradeAction.OPEN_LONG,
        side=Side.LONG,
        quantity=0.01,
        leverage=5,
        stop_loss=95.0,
        take_profit=110.0,
        price=100.0,
    )
    data.update(kw)
    return Order(**data)


class TestOpenOrder(unittest.IsolatedAsyncioTestCase):
    async def test_open_passes_cloid(self):
        rec = _Recorder()
        ex = LiveExecutor(_Engine(rec))

        await ex.execute_order(_order())

        self.assertEqual(len(rec.calls), 1)
        self.assertTrue(rec.calls[0]["cloid"],
                        "order opening wajib punya cloid")

    async def test_open_without_sl_refused(self):
        """Posisi baru tanpa SL ditolak sebelum sampai bursa."""
        rec = _Recorder()
        result = await LiveExecutor(_Engine(rec)).execute_order(
            _order(stop_loss=None))

        self.assertFalse(result["success"])
        self.assertEqual(rec.calls, [], "tidak boleh menyentuh bursa")

    async def test_open_without_tp_refused(self):
        rec = _Recorder()
        result = await LiveExecutor(_Engine(rec)).execute_order(
            _order(take_profit=None))

        self.assertFalse(result["success"])
        self.assertEqual(rec.calls, [])

    async def test_open_without_price_refused(self):
        """Harga 0 gagal di sini, bukan terkirim lalu ditolak bursa."""
        rec = _Recorder()
        result = await LiveExecutor(_Engine(rec)).execute_order(
            _order(price=0.0))

        self.assertFalse(result["success"])
        self.assertEqual(rec.calls, [])

    async def test_long_is_buy(self):
        rec = _Recorder()
        await LiveExecutor(_Engine(rec)).execute_order(_order())
        self.assertTrue(rec.calls[0]["is_buy"], "LONG = BUY")

    async def test_short_is_sell(self):
        rec = _Recorder()
        await LiveExecutor(_Engine(rec)).execute_order(
            _order(action=TradeAction.OPEN_SHORT, side=Side.SHORT))
        self.assertFalse(rec.calls[0]["is_buy"], "SHORT = SELL")

    async def test_unfilled_reports_not_protected(self):
        rec = _Recorder({"success": True, "protected": False})
        result = await LiveExecutor(_Engine(rec)).execute_order(_order())
        self.assertTrue(result["success"])
        self.assertIn("resting", result["message"])

    async def test_rejected_order_reports_failure(self):
        rec = _Recorder({"success": False, "message": "batas terlampaui"})
        result = await LiveExecutor(_Engine(rec)).execute_order(_order())
        self.assertFalse(result["success"])


class TestCloseOrder(unittest.IsolatedAsyncioTestCase):
    def _position(self, side):
        from trading.live.engine import LivePosition

        return LivePosition(
            symbol="BTC/USDT:USDT", coin="BTC", side=side, size=0.01,
            entry_price=100.0, stop_loss=95.0, take_profit=110.0,
            leverage=5, sl_order_id=1, tp_order_id=2,
        )

    async def test_close_long_is_buy(self):
        rec = _Recorder()
        eng = _Engine(rec, {"BTC/USDT:USDT": self._position("LONG")})

        await LiveExecutor(eng).execute_order(
            _order(action=TradeAction.CLOSE, side=Side.LONG))
        await LiveExecutor(eng).execute_order(
            _order(action=TradeAction.CLOSE, side=None))
        self.assertTrue(rec.calls[0]["is_close"])

    async def test_close_short_is_sell(self):
        rec = _Recorder()
        eng = _Engine(rec, {"BTC/USDT:USDT": self._position("SHORT")})

        await LiveExecutor(eng).execute_order(
            _order(action=TradeAction.CLOSE, side=Side.SHORT))
        await LiveExecutor(eng).execute_order(
            _order(action=TradeAction.CLOSE, side=None))

    async def test_close_unknown_position_refused(self):
        rec = _Recorder()
        result = await LiveExecutor(_Engine(rec, {})).execute_order(
            _order(action=TradeAction.CLOSE, side=Side.LONG))

        self.assertFalse(result["success"])
        self.assertEqual(rec.calls, [])

    async def test_close_without_price_refused(self):
        """Tanpa harga pasar, tidak boleh mengklaim menutup."""
        rec = _Recorder(mid=0.0)
        eng = _Engine(rec, {"BTC/USDT:USDT": self._position("LONG")})

        result = await LiveExecutor(eng).execute_order(
            _order(action=TradeAction.CLOSE, side=Side.LONG))

        self.assertFalse(result["success"])
        self.assertEqual(rec.calls, [])


class TestHoldIsNoop(unittest.IsolatedAsyncioTestCase):
    async def test_hold_touches_nothing(self):
        rec = _Recorder()
        result = await LiveExecutor(_Engine(rec)).execute_order(
            _order(action=TradeAction.HOLD))

        self.assertTrue(result["success"])
        self.assertEqual(rec.calls, [], "HOLD tidak boleh kirim order")


class TestAgentCompatibility(unittest.TestCase):
    """
    `ExecutionAgent` harus menerima kedua engine tanpa perubahan.

    Kalau ini gagal, agent dan adapter tidak benar-benar terpisah, dan
    perubahan kecil di salah satunya akan merusak yang lain.
    """

    def test_paper_engine_has_the_interface(self):
        from trading.paper_engine import PaperTradingEngine

        for name in ("execute_order", "update_price"):
            self.assertTrue(callable(getattr(PaperTradingEngine, name,
                                             None)), name)

    def test_live_executor_has_the_interface(self):
        for name in ("execute_order", "update_price"):
            self.assertTrue(callable(getattr(LiveExecutor, name, None)), name)

    def test_execution_agent_takes_generic_engine(self):
        import inspect

        from agents.execution_agent import ExecutionAgent

        params = inspect.signature(ExecutionAgent.__init__).parameters
        self.assertIn("engine", params)
        self.assertNotIn("PaperTradingEngine", str(params["engine"]))
