"""
tests/test_money_path_behaviour.py — jalur uang, diuji PERILAKU.

Empat fungsi yang menentukan apakah uang bergerak dengan benar:

  * `_open` — gerbang terakhir sebelum order dikirim
  * `_persist_open` — menulis fill bursa ke SQLite
  * `_close` — menutup posisi
  * `record_exchange_fills` — menutup baris dari fill bursa

Test sebelumnya memakai stub yang selalu mengembalikan dict sukses.
Stub seperti itu tidak bisa membedakan "kodenya benar" dari "kodenya
tidak pernah diuji": semua order terisi, semua harga sama, tidak pernah
slippage, tidak pernah partial fill.

Test di sini memakai `tests/stateful_exchange.py`, bursa tiruan yang
BERAKEH TIAN: order benar-benar mengubah posisi, partial fill sungguhan,
harga bergerak, dan ledger hanya berisi fill yang benar-benar terjadi.
"""

import asyncio
import unittest
from unittest import mock

from tests.stateful_exchange import StatefulExchange
from trading.live.executor import LiveExecutor
from trading.models import Order, TradeAction


def _order(**kw):
    base = dict(symbol="BTC/USDT:USDT", action=TradeAction.OPEN_LONG,
                quantity=0.1, price=100.0, leverage=5,
                stop_loss=90.0, take_profit=120.0, reasoning="uji")
    base.update(kw)
    return Order(**base)


def _executor(exchange, positions=None):
    """
    LiveExecutor dengan bursa tiruan dan repo dalam memori.

    Bursa tiruan duduk di `executor.engine.exchange` — itu tempat
    production, bukan tempat yang tidak nyata. Meletakkannya di
    `executor.exchange` membuat test hijau sementara kode produksi
    berjalan dengan AttributeError AttributeError.
    """
    ex = LiveExecutor.__new__(LiveExecutor)
    engine = mock.Mock()
    engine.exchange = exchange
    engine.positions = dict(positions or {})
    engine.gate = mock.Mock()
    ex.engine = engine
    inserted = []

    class _Repo:
        async def insert_position(self, pos):
            inserted.append(pos)
            return len(inserted)

        async def insert_trade(self, trade):
            inserted.append(trade)
            return len(inserted)

    ex._get_repo = lambda: asyncio.sleep(0, result=_Repo())
    ex._publish = lambda *a, **kw: asyncio.sleep(0)
    ex._log_agent = lambda *a, **kw: asyncio.sleep(0)
    ex._log_wallet = lambda *a, **kw: asyncio.sleep(0)
    ex._warn_shared_ledger = lambda: None

    class _Risk:
        @staticmethod
        def calculate_liquidation_price(entry, side, lev):
            return entry * 0.5 if side == "LONG" else entry * 1.5

        @staticmethod
        def calculate_fee(qty, price, kind):
            return qty * price * 0.00045

    ex.risk_manager = _Risk()
    ex.inserted = inserted
    return ex


class TestOpenGate(unittest.TestCase):
    """
    `_open` — gerbang terakhir sebelum uang bergerak.

    Setiap test membuktikan order TIDAK sampai ke bursa, bukan hanya
    bahwa dict hasilnya punya `success: False`.
    """

    def test_missing_sl_tp_never_reaches_exchange(self):
        ex = StatefulExchange()
        eng = _executor(ex)
        out = asyncio.run(eng._open(_order(stop_loss=None)))
        self.assertFalse(out["success"])
        self.assertEqual(out["details"]["blockers"], ["missing_tpsl"])
        self.assertEqual(ex.submitted, [], "order tanpa SL/TP sampai ke bursa")

    def test_missing_tp_never_reaches_exchange(self):
        ex = StatefulExchange()
        eng = _executor(ex)
        out = asyncio.run(eng._open(_order(take_profit=None)))
        self.assertFalse(out["success"])
        self.assertEqual(ex.submitted, [])

    def test_zero_quantity_never_reaches_exchange(self):
        """`quantity=0` adalah default `Order`."""
        ex = StatefulExchange()
        eng = _executor(ex)
        out = asyncio.run(eng._open(_order(quantity=0)))
        self.assertFalse(out["success"])
        self.assertEqual(out["details"]["blockers"], ["invalid_quantity"])
        self.assertEqual(ex.submitted, [], "order quantity=0 sampai ke bursa")

    def test_negative_quantity_never_reaches_exchange(self):
        ex = StatefulExchange()
        eng = _executor(ex)
        out = asyncio.run(eng._open(_order(quantity=-1.0)))
        self.assertFalse(out["success"])
        self.assertEqual(ex.submitted, [])

    def test_nan_quantity_never_reaches_exchange(self):
        ex = StatefulExchange()
        eng = _executor(ex)
        out = asyncio.run(eng._open(_order(quantity=float("nan"))))
        self.assertFalse(out["success"])
        self.assertEqual(ex.submitted, [])

    def test_leverage_none_never_reaches_exchange(self):
        """
        `leverage=None` dulu jadi `order.leverage or 5` — leverage 5 yang
        tidak pernah dinyatakan siapa pun.
        """
        ex = StatefulExchange()
        eng = _executor(ex)
        out = asyncio.run(eng._open(_order(leverage=None)))
        self.assertFalse(out["success"])
        self.assertEqual(out["details"]["blockers"], ["invalid_leverage"])
        self.assertEqual(ex.submitted, [], "order leverage=None jadi 5")

    def test_leverage_zero_never_reaches_exchange(self):
        ex = StatefulExchange()
        eng = _executor(ex)
        out = asyncio.run(eng._open(_order(leverage=0)))
        self.assertFalse(out["success"])
        self.assertEqual(ex.submitted, [])

    def test_leverage_bool_is_rejected(self):
        """`True` adalah int di Python — tapi bukan leverage masuk akal."""
        ex = StatefulExchange()
        eng = _executor(ex)
        out = asyncio.run(eng._open(_order(leverage=True)))
        self.assertFalse(out["success"])
        self.assertEqual(ex.submitted, [])

    def test_float_leverage_is_rejected(self):
        ex = StatefulExchange()
        eng = _executor(ex)
        out = asyncio.run(eng._open(_order(leverage=2.5)))
        self.assertFalse(out["success"])
        self.assertEqual(ex.submitted, [])

    def test_valid_order_submits_declared_leverage(self):
        ex = StatefulExchange(mid=100.0)
        eng = _executor(ex)
        seen = {}

        async def submit(**kw):
            seen.update(kw)
            return ex.submit(kw["coin"], kw["is_buy"], kw["size"], kw["price"])

        eng.engine = mock.Mock()
        eng.engine.submit_order = submit
        eng.engine.gate = mock.Mock()
        out = asyncio.run(eng._open(_order(leverage=7)))
        self.assertTrue(out["success"])
        self.assertEqual(seen.get("leverage"), 7,
                         "leverage yang dikirim bukan yang dinyatakan")


class TestPersistOpenUsesExchangeNumbers(unittest.TestCase):
    """
    `_persist_open` — angka yang ditulis harus angka BURSA.

    Order kita bukan kenyataan: partial fill berarti qty dan harga kita
    salah. Kalau yang ditulis angka order, database menampilkan posisi
    yang tidak pernah ada di bursa.
    """

    def _run(self, fill_ratio, order_qty=0.1, order_price=100.0):
        ex = StatefulExchange(mid=order_price)
        eng = _executor(ex)
        result = ex.submit("BTC", True, order_qty, order_price,
                           fill_ratio=fill_ratio)
        out = asyncio.run(eng._persist_open(
            _order(quantity=order_qty, price=order_price),
            result, order_price, "0x" + "a" * 32, True, 5))
        return out, eng, ex

    def _positions(self, eng):
        return [p for p in eng.inserted if getattr(p, "status", None)]

    def test_partial_fill_writes_exchange_size_not_order_size(self):
        out, eng, ex = self._run(0.4)
        self.assertTrue(out["success"])
        pos = self._positions(eng)[0]
        self.assertAlmostEqual(pos.quantity, 0.04, places=6,
                               msg="qty yang ditulis qty ORDER (0.1), "
                                   "bukan qty BURSA (0.04)")

    def test_zero_fill_is_not_written_as_a_position(self):
        ex = StatefulExchange(mid=100.0)
        eng = _executor(ex)
        result = ex.submit("BTC", True, 0.1, 100.0, fill_ratio=0.0)
        out = asyncio.run(eng._persist_open(
            _order(), result, 100.0, "0x" + "a" * 32, True, 5))
        self.assertEqual(self._positions(eng), [],
                         "fill nol ditulis sebagai posisi")

    def test_mode_is_live_so_it_never_mixes_with_paper(self):
        out, eng, ex = self._run(1.0)
        self.assertEqual(self._positions(eng)[0].mode, "live",
                         "baris live tidak ditandai 'live' — tidak bisa "
                         "dibedakan dari simulasi")

    def test_db_failure_engages_kill_switch(self):
        """
        Fill di bursa tapi gagal ditulis = uang terekspos tanpa jejak.
        Itu harus menyalakan kill switch, bukan hanya di-log.
        """
        ex = StatefulExchange(mid=100.0)
        eng = _executor(ex)

        class _Repo:
            async def insert_position(self, pos):
                raise RuntimeError("db mati")

        eng._get_repo = lambda: asyncio.sleep(0, result=_Repo())
        result = ex.submit("BTC", True, 0.1, 100.0)
        out = asyncio.run(eng._persist_open(
            _order(), result, 100.0, "0x" + "a" * 32, True, 5))
        self.assertTrue(eng.engine.gate.engage_kill_switch.called,
                        "gagal tulis fill live TIDAK menyalakan kill switch")
        self.assertIn("kill switch", out["message"].lower())


class TestCloseRefusesWithoutPosition(unittest.TestCase):
    """
    `_close` — posisi tidak dikenal berarti TIDAK menyentuh bursa.

    Bursa yang tidak disentuh adalah satu-satunya bukti aman. `success:
    False` saja tidak membuktikan apa pun.
    """

    def test_unknown_position_does_not_touch_exchange(self):
        ex = StatefulExchange()
        eng = _executor(ex, positions={})
        out = asyncio.run(eng._close(_order(action=TradeAction.CLOSE)))
        self.assertFalse(out["success"])
        self.assertEqual(ex.submitted, [],
                         "posisi tidak dikenal tapi order tetap dikirim")

    def test_zero_market_price_refuses_without_order(self):
        """Harga 0 = bursa tidak terbaca. Order limit 0 tidak menutup apa pun."""
        ex = StatefulExchange(mid=0.0)
        pos = mock.Mock(coin="BTC", side="LONG", size=0.1)
        eng = _executor(ex, positions={"BTC/USDT:USDT": pos})
        out = asyncio.run(eng._close(_order(action=TradeAction.CLOSE)))
        self.assertFalse(out["success"])
        self.assertEqual(ex.submitted, [], "order dikirim dengan harga 0")

    def test_resting_close_reports_position_still_open(self):
        """
        Order closing yang RESTING tidak menutup apa pun.

        Melaporkan CLOSE dan menutup baris sementara posisi masih hidup
        adalah kebohongan yang paling berbahaya di jalur ini.
        """
        ex = StatefulExchange(mid=100.0)
        pos = mock.Mock(coin="BTC", side="LONG", size=0.1)
        eng = _executor(ex, positions={"BTC/USDT:USDT": pos})

        async def submit(**kw):
            return {"success": True, "protected": False,
                    "message": "resting", "outcome": None}

        eng.engine.submit_order = submit
        eng._find_open_row = lambda s: asyncio.sleep(0, result=None)
        out = asyncio.run(eng._close(_order(action=TradeAction.CLOSE)))
        self.assertFalse(out["success"],
                         "order closing resting dilaporkan sukses")
        self.assertIn("MASIH terbuka", out["message"])

    def test_filled_close_records_against_open_row(self):
        ex = StatefulExchange(mid=100.0)
        pos = mock.Mock(coin="BTC", side="LONG", size=0.1)
        eng = _executor(ex, positions={"BTC/USDT:USDT": pos})

        async def submit(**kw):
            return ex.submit(kw["coin"], kw["is_buy"], kw["size"],
                             kw["price"], is_close=True)

        eng.engine.submit_order = submit
        seen = {}

        async def record_close(**kw):
            seen.update(kw)

        eng._find_open_row = lambda s: asyncio.sleep(
            0, result={"id": 42, "symbol": s, "side": "LONG",
                       "entry_price": 100.0})
        eng._record_close = record_close
        out = asyncio.run(eng._close(_order(action=TradeAction.CLOSE)))
        self.assertTrue(out["success"])
        self.assertEqual(out["position_id"], 42)
        self.assertEqual(seen.get("row_id"), 42,
                         "penutupan dicatat ke baris yang salah")

    def test_partial_close_records_actual_filled_size(self):
        """Penutupan sebagian tidak boleh dicatat sebagai penutupan penuh."""
        ex = StatefulExchange(mid=100.0)
        pos = mock.Mock(coin="BTC", side="LONG", size=0.1)
        eng = _executor(ex, positions={"BTC/USDT:USDT": pos})
        seen = {}

        async def submit(**kw):
            return ex.submit(kw["coin"], kw["is_buy"], kw["size"],
                             kw["price"], is_close=True, fill_ratio=0.3)

        eng.engine.submit_order = submit

        async def record_close(**kw):
            seen.update(kw)

        eng._find_open_row = lambda s: asyncio.sleep(
            0, result={"id": 7, "symbol": s, "side": "LONG",
                       "entry_price": 100.0})
        eng._record_close = record_close
        asyncio.run(eng._close(_order(action=TradeAction.CLOSE)))
        self.assertAlmostEqual(seen.get("size"), 0.03, places=6,
                               msg="qty yang dicatat bukan qty yang "
                                   "benar-benar terisi di bursa")


class TestRecordExchangeFills(unittest.TestCase):
    """
    `record_exchange_fills` — baris lokal harus ditutup dari fill bursa.

    Kalau ini tidak jalan, tiga hal sekaligus rusak: baris posisi tetap
    `OPEN` selamanya, daily-loss breaker tidak punya sumber angka, dan
    setiap SL/TP yang bekerja terlihat sebagai divergensi.
    """

    def _engine_with_fills(self, ex, row=None):
        eng = _executor(ex)
        closed = []
        eng._find_open_row = lambda s: asyncio.sleep(0, result=row)

        class _Repo:
            async def close_position(self, row_id, price, pnl, reason):
                closed.append({"row_id": row_id, "price": price,
                               "pnl": pnl, "reason": reason})
                return True

            async def insert_trade(self, trade):
                return 1

            async def get_trade_stats(self, mode=None):
                return {"total_pnl": 0.0, "total_trades": 1,
                        "winning_trades": 1, "losing_trades": 0,
                        "profit_factor": float("inf")}

            async def update_account_stats(self, **kw):
                return True

        eng._get_repo = lambda: asyncio.sleep(0, result=_Repo())
        return eng, closed

    def _close_fill(self, **kw):
        base = {"kind": "CLOSE", "coin": "BTC",
                "symbol": "BTC/USDT:USDT", "size": 0.1,
                "price": 110.0, "fee": -0.001, "closed_pnl": 1.0}
        base.update(kw)
        return base

    def _open_row(self):
        return {"id": 42, "symbol": "BTC/USDT:USDT", "side": "LONG",
                "stop_loss": 90.0, "take_profit": 120.0,
                "entry_price": 100.0}

    def test_close_fill_closes_the_local_row(self):
        """
        Fill CLOSE dari bursa harus menutup baris lokal.

        Tanpa ini: baris posisi tetap `OPEN` selamanya, daily-loss
        breaker tidak punya sumber angka, dan setiap SL/TP yang bekerja
        terlihat sebagai divergensi.
        """
        ex = StatefulExchange(mid=100.0)
        eng, closed = self._engine_with_fills(ex, row=self._open_row())
        out = asyncio.run(eng.record_exchange_fills([self._close_fill()]))
        self.assertEqual(len(out), 1, "fill CLOSE tidak dicatat sama sekali")
        self.assertEqual(closed[0]["row_id"], 42)

    def test_close_fill_feeds_daily_loss_breaker(self):
        """
        `record_realized_pnl` adalah SATU-SATUNYA pemakan
        `Blocker.DAILY_LOSS_LIMIT` di live.
        """
        ex = StatefulExchange(mid=100.0)
        eng, closed = self._engine_with_fills(ex, row=self._open_row())
        asyncio.run(eng.record_exchange_fills(
            [self._close_fill(closed_pnl=-5.0)]))
        self.assertTrue(eng.engine.gate.record_realized_pnl.called,
                        "PnL realized tidak masuk gate — daily-loss "
                        "breaker live tidak punya sumber angka")

    def test_close_fill_drops_position_from_engine(self):
        """
        Posisi di `engine.positions` harus hilang setelah fill CLOSE.

        Kalau tidak, `reconcile` masih punya posisi yang sudah tutup dan
        kill switch menyala pada SL/TP yang bekerja.
        """
        ex = StatefulExchange(mid=100.0)
        eng, closed = self._engine_with_fills(ex, row=self._open_row())
        eng.engine.positions["BTC/USDT:USDT"] = mock.Mock()
        asyncio.run(eng.record_exchange_fills([self._close_fill()]))
        self.assertNotIn("BTC/USDT:USDT", eng.engine.positions,
                         "posisi tetap ada di engine setelah fill CLOSE")

    def test_open_fill_is_not_treated_as_a_close(self):
        """
        Fill OPEN bukan penutupan.

        Mutan yang mematikan filter `kind != "CLOSE"` harus membuat test
        ini merah: tanpa filter, baris yang baru dibuat ikut tertutup.
        """
        ex = StatefulExchange(mid=100.0)
        eng, closed = self._engine_with_fills(ex, row=self._open_row())
        asyncio.run(eng.record_exchange_fills(
            [self._close_fill(kind="OPEN", price=100.0, closed_pnl=0.0)]))
        self.assertEqual(closed, [], "fill OPEN diperlakukan sebagai CLOSE")

    def test_zero_price_fill_is_not_recorded(self):
        """Harga 0 = respons tidak bisa dipercaya. Tidak boleh dicatat."""
        ex = StatefulExchange(mid=100.0)
        eng, closed = self._engine_with_fills(ex, row=self._open_row())
        asyncio.run(eng.record_exchange_fills([self._close_fill(price=0.0)]))
        self.assertEqual(closed, [],
                         "fill dengan harga 0 dicatat sebagai penutupan")

    def test_empty_fill_list_sends_nothing(self):
        ex = StatefulExchange(mid=100.0)
        eng, closed = self._engine_with_fills(ex)
        asyncio.run(eng.record_exchange_fills([]))
        self.assertEqual(closed, [])
        self.assertEqual(ex.submitted, [],
                         "tidak ada fill tapi order tetap dikirim")

