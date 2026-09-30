"""
tests/test_live_engine.py — Urutan dan arah order di live engine.

Yang diuji di sini adalah hal yang tidak bisa dibaca dari kode: urutan
panggilan ke bursa dan arah order. Leverage yang dipasang setelah order
tetap terlihat "benar" di log, hanya leverage-nya yang salah.
"""
import os
import unittest
from pathlib import Path
from datetime import datetime, timezone

from core.config import LiveConfig
from trading.live.safety import SafetyGate

KEY = "0x" + "ab" * 32


class FakeOutcome:
    """Bentuk hasil order yang dipakai LiveEngine."""

    def __init__(self, ok=True, filled=1.0, price=100.0, oid=1, error=None):
        self.ok = ok
        self.filled_size = filled
        self.avg_price = price
        self.order_id = oid
        self.error = error

    def describe(self):
        if not self.ok:
            return "GAGAL: " + str(self.error)
        if self.filled_size <= 0:
            return "DITERIMA tapi tidak terisi"
        return "TERISI {} @ {}".format(self.filled_size, self.avg_price)


class FakeExchange:
    """
    Klien bursa palsu yang mencatat urutan panggilan.

    Urutan panggilan adalah hal yang paling penting untuk dibuktikan di
    sini. Leverage yang dipasang setelah order menghasilkan leverage
    default bursa, dan tidak ada error yang mengatakannya.
    """

    def __init__(self, fill=True, positions=None, mids=None):
        self.calls = []
        self._fill = fill
        self._positions = positions or []
        # `mids` yang kosong harus BERBEDA dari mids=None, supaya test bisa
        # memverifikasi jalur "harga pasar tidak terbaca".
        self._mids = {"BTC": 100.0} if mids is None else mids

    def _log(self, name, **kw):
        self.calls.append((name, kw))

    def free_collateral(self):
        return 10_000.0

    def total_notional(self):
        return 0.0

    def symbol_notional(self, coin):
        return 0.0

    def set_leverage(self, coin, leverage, is_cross=True):
        self._log("set_leverage", coin=coin, leverage=leverage)
        return {"ok": True}

    def place_limit_order(self, coin, is_buy, size, price,
                          reduce_only=False, cloid=None):
        self._log("place_limit_order", coin=coin, is_buy=is_buy,
                  reduce_only=reduce_only, cloid=cloid)
        if self._fill:
            return FakeOutcome(filled=size, price=price)
        return FakeOutcome(filled=0.0)

    def place_trigger_order(self, coin, is_buy, size, trigger_price, tpsl,
                           reduce_only=True):
        self._log("place_trigger_order", coin=coin, is_buy=is_buy,
                  trigger_price=trigger_price, tpsl=tpsl)
        return FakeOutcome(oid=100 + (1 if tpsl == "sl" else 2))

    def cancel_all(self, coin):
        self._log("cancel_all", coin=coin)
        return "cancelled"

    def mid_price(self, coin):
        self._log("mid_price", coin=coin)
        return self._mids.get(coin, 0.0)

    def positions(self):
        return self._positions

    class _Info:
        def meta(inner):
            return {"universe": [{"name": "BTC"}, {"name": "ETH"}]}

    info = _Info()


MIDDAY = datetime(2026, 1, 15, 15, 0, tzinfo=timezone.utc)


def _gate(kill=False):
    env = {
        "TRADEBOT_LIVE": "1",
        "TRADEBOT_LIVE_CONFIRMED": "1",
        "HYPERLIQUID_PRIVATE_KEY": KEY,
    }
    # State file di-scope ke PID DAN di-unlink dulu. unscoping saja tidak
    # cukup: dalam satu proses test semua test berbagi PID, jadi test kedua
    # akan membaca kill switch yang test pertama nyalakan. Gejalanya
    # persis seperti bug produksi - order ditolak dengan blocker
    # `KILL_SWITCH aktif` tanpa alasan yang bisa dibaca.
    state = Path("data_store/test_live_gate_%d.json" % os.getpid())
    for suffix in ("", ".tmp"):
        f = Path(str(state) + suffix)
        if f.exists():
            f.unlink()
    gate = SafetyGate(LiveConfig(), env=env, state_path=state)
    if kill:
        gate.engage_kill_switch("test")
    return gate


def _engine(exchange, gate=None):
    from trading.live.engine import LiveEngine

    return LiveEngine(gate=gate or _gate(), exchange=exchange,
                      cfg=LiveConfig(), now_fn=lambda: MIDDAY)


def _names(exchange):
    return [c[0] for c in exchange.calls]


class TestOrderSequence(unittest.IsolatedAsyncioTestCase):
    """
    Urutan panggilan ke bursa.

    Ini yang tidak bisa dibaca dari kode saja: leverage yang dipasang
    setelah order akan tetap terlihat "benar" di log, hanya leverage-nya
    yang salah.
    """

    async def test_leverage_set_before_order(self):
        """
        Leverage WAJIB dipasang sebelum order dikirim.

        Kalau tidak, order pertama memakai leverage default bursa yang bisa
        jauh lebih tinggi dari yang bot kira.
        """
        ex = FakeExchange()
        eng = _engine(ex)

        await eng.submit_order(
            "BTC", "BTC / USDC:USDC", is_buy=True, size=0.01, price=100.0,
            stop_loss=95.0, take_profit=110.0, leverage=5, cloid="c1",
        )

        names = _names(ex)
        self.assertIn("set_leverage", names)
        self.assertLess(
            names.index("set_leverage"), names.index("place_limit_order"),
            "leverage harus SEBELUM order",
        )

    async def test_cloid_passed_through(self):
        """cloid harus diteruskan ke bursa, bukan diabaikan."""
        ex = FakeExchange()
        eng = _engine(ex)

        await eng.submit_order(
            "BTC", "BTC / USDC:USDC", is_buy=True, size=0.01, price=100.0,
            stop_loss=95.0, take_profit=110.0, leverage=5, cloid="unik-123",
        )

        orders = [c for c in ex.calls if c[0] == "place_limit_order"]
        self.assertEqual(orders[0][1]["cloid"], "unik-123")

    async def test_no_protection_attached_when_unfilled(self):
        """
        Order yang tidak terisi TIDAK boleh memasang SL.

        SL untuk posisi yang tidak pernah ada ditolak reduceOnly, dan
        sisanya order yatim yang menggantung.
        """
        ex = FakeExchange(fill=False)
        eng = _engine(ex)

        result = await eng.submit_order(
            "BTC", "BTC / USDC:USDC", is_buy=True, size=0.01, price=100.0,
            stop_loss=95.0, take_profit=110.0, leverage=5, cloid="c1",
        )

        self.assertFalse(result["protected"])
        self.assertNotIn("place_trigger_order", _names(ex))

    async def test_gate_blocks_before_any_exchange_call(self):
        """Leverage tidak boleh disentuh kalau gerbang menolak."""
        ex = FakeExchange()
        eng = _engine(ex, gate=_gate(kill=True))

        result = await eng.submit_order(
            "BTC", "BTC / USDC:USDC", is_buy=True, size=0.01, price=100.0,
            stop_loss=95.0, take_profit=110.0, leverage=5, cloid="c1",
        )

        self.assertFalse(result["success"])
        self.assertEqual(_names(ex), [], "tidak ada panggilan ke bursa")

    async def test_missing_protection_blocks_order(self):
        """Posisi baru tanpa SL atau TP ditolak sebelum dikirim."""
        for sl, tp in ((None, 110.0), (95.0, None)):
            with self.subTest(sl=sl, tp=tp):
                ex = FakeExchange()
                eng = _engine(ex)
                result = await eng.submit_order(
                    "BTC", "BTC / USDC:USDC", is_buy=True, size=0.01,
                    price=100.0, stop_loss=sl, take_profit=tp,
                    leverage=5, cloid="c1",
                )
                self.assertFalse(result["success"])
                self.assertEqual(_names(ex), [])


class TestTriggerDirection(unittest.IsolatedAsyncioTestCase):
    """
    Arah order trigger.

    Menutup LONG = SELL. Menutup SHORT = BUY. Salah arah tidak error:
    order-nya diterima lalu menggantung selamanya, dan posisinya terlihat
    terlindungi padahal tidak.
    """

    async def _triggers_for(self, is_buy):
        ex = FakeExchange()
        eng = _engine(ex)
        await eng.submit_order(
            "BTC", "BTC / USDC:USDC", is_buy=is_buy, size=0.01, price=100.0,
            stop_loss=95.0, take_profit=110.0, leverage=5, cloid="c1",
        )
        return [c for c in ex.calls if c[0] == "place_trigger_order"]

    async def test_long_uses_sell_triggers(self):
        """Posisi LONG ditutup dengan SELL."""
        triggers = await self._triggers_for(is_buy=True)
        self.assertTrue(triggers, "SL dan TP harus dipasang")
        for call in triggers:
            self.assertFalse(
                call[1]["is_buy"],
                "trigger untuk LONG harus SELL, bukan BUY",
            )

    async def test_short_uses_buy_triggers(self):
        """Posisi SHORT ditutup dengan BUY."""
        triggers = await self._triggers_for(is_buy=False)
        self.assertTrue(triggers, "SL dan TP harus dipasang")
        for call in triggers:
            self.assertTrue(
                call[1]["is_buy"],
                "trigger untuk SHORT harus BUY, bukan SELL",
            )

    async def test_both_sl_and_tp_installed(self):
        """SL dan TP keduanya harus ada."""
        triggers = await self._triggers_for(is_buy=True)
        kinds = {c[1]["tpsl"] for c in triggers}
        self.assertEqual(kinds, {"sl", "tp"})


class TestEmergencyFlat(unittest.IsolatedAsyncioTestCase):
    """
    Penutupan darurat harus benar-benar menutup posisi.

    Versi sebelumnya hanya menghapus state lokal dan mengembalikan
    `flattened: True`. Itu kebohongan: database bersih sementara bursa
    masih memegang eksposur milik pengguna.
    """

    def _position(self, side="LONG", size=0.01):
        from trading.live.engine import LivePosition

        return LivePosition(
            symbol="BTC / USDC:USDC", coin="BTC", side=side, size=size,
            entry_price=100.0, stop_loss=95.0, take_profit=110.0, leverage=5,
        )

    def _with_position(self, side="LONG"):
        ex = FakeExchange()
        eng = _engine(ex)
        eng.positions["BTC / USDC:USDC"] = self._position(side)
        return ex, eng

    async def test_long_closed_with_buy(self):
        """Menutup LONG = BUY."""
        ex, eng = self._with_position("LONG")
        await eng.emergency_flat("test")

        orders = [c for c in ex.calls if c[0] == "place_limit_order"]
        self.assertTrue(orders, "harus ada order menutup")
        self.assertTrue(orders[0][1]["is_buy"], "menutup LONG harus BUY")
        self.assertTrue(orders[0][1]["reduce_only"])

    async def test_short_closed_with_sell(self):
        """Menutup SHORT = SELL."""
        ex, eng = self._with_position("SHORT")
        await eng.emergency_flat("test")

        orders = [c for c in ex.calls if c[0] == "place_limit_order"]
        self.assertTrue(orders)
        self.assertFalse(orders[0][1]["is_buy"], "menutup SHORT harus SELL")

    async def test_cancel_happens_before_close(self):
        """
        Order resting dibatalkan DULU.

        Kalau dibalik, order GTC bisa mengeksekusi di tengah proses
        penutupan dan membuka posisi baru tepat saat kita sedang menutup
        semuanya.
        """
        ex, eng = self._with_position("LONG")
        await eng.emergency_flat("test")

        names = _names(ex)
        self.assertIn("cancel_all", names)
        self.assertLess(
            names.index("cancel_all"), names.index("place_limit_order"),
            "batal harus SEBELUM menutup",
        )

    async def test_flattened_false_when_close_fails(self):
        """
        `flattened` hanya True kalau benar-benar bersih.

        Nilai yang optimistis membuat operator berpikir posisi sudah
        tertutup padahal masih terbuka.
        """
        class FailingExchange(FakeExchange):
            def place_limit_order(self, coin, is_buy, size, price,
                                  reduce_only=False, cloid=None):
                self._log("place_limit_order", coin=coin, is_buy=is_buy)
                return FakeOutcome(ok=False, error="bursa menolak")

        ex = FailingExchange()
        eng = _engine(ex)
        eng.positions["BTC / USDC:USDC"] = self._position("LONG")
        result = await eng.emergency_flat("test")

        self.assertFalse(result["flattened"],
                         "gagal menutup = flattened harus False")
        self.assertTrue(result["failed"])
        self.assertTrue(eng.gate.engaged, "kill switch harus menyala")

    async def test_no_price_means_no_close(self):
        """Tanpa harga pasar, bot tidak boleh mengklaim berhasil."""
        ex = FakeExchange(mids={})
        eng = _engine(ex)
        eng.positions["BTC / USDC:USDC"] = self._position("LONG")
        result = await eng.emergency_flat("test")

        self.assertFalse(result["flattened"])
        self.assertNotIn("place_limit_order", _names(ex))


class TestQuantization(unittest.TestCase):
    """
    Ukuran dan harga harus mengikuti lot size bursa.

    Size yang tidak sesuai ditolak bursa dengan pesan yang sering tidak
    menyebut angkanya, jadi bot akan gagal berulang dengan alasan yang
    sama tanpa pernah tahu penyebabnya.
    """

    def _exchange(self, universe):
        from trading.live.client import LiveExchange

        ex = LiveExchange.__new__(LiveExchange)
        ex._rules = None
        ref = universe

        class _Info:
            def meta(self_inner):
                return {"universe": ref}

        ex.info = _Info()
        return ex

    def setUp(self):
        self.ex = self._exchange([
            {"name": "BTC", "szMax": 1e6, "szMin": 1e-5, "maxLeverage": 50},
            {"name": "PEPE", "szMax": 1e12, "szMin": 1.0, "maxLeverage": 5},
        ])

    def test_decimals_from_sz_min_not_sz_max(self):
        """SzMax cuma batas atas; presisi ada di szMin.

        BTC punya szMax 1e6 dan szMin 1e-5. Kalau szMax yang dipakai,
        desimalnya jadi 0 dan semua order BTC dibulatkan ke lot 1 --
        order 0.01 BTC berubah jadi 0 dan ditolak.
        """
        self.assertEqual(self.ex.asset_rules()["BTC"]["sz_decimals"], 5)
        self.assertEqual(self.ex.asset_rules()["PEPE"]["sz_decimals"], 0)

    def test_small_order_survives(self):
        """Order kecil harus tetap kecil, bukan jadi nol."""
        self.assertAlmostEqual(self.ex.quantize_size("BTC", 0.01), 0.01)

    def test_never_exceeds_requested(self):
        """Hasil tidak boleh LEBIH dari yang diminta."""
        for size in (0.0123456789, 123.456789, 7.7777777):
            self.assertLessEqual(self.ex.quantize_size("BTC", size), size)

    def test_below_min_raises_to_min(self):
        """Di bawah szMin harus naik, bukan turun ke nol."""
        self.assertAlmostEqual(self.ex.quantize_size("BTC", 1e-9), 1e-5)

    def test_above_max_clamped(self):
        self.assertLessEqual(self.ex.quantize_size("BTC", 1e9), 1e6)

    def test_zero_stays_zero(self):
        """Nol tidak boleh dinaikkan ke szMin."""
        self.assertEqual(self.ex.quantize_size("BTC", 0.0), 0.0)
        self.assertEqual(self.ex.quantize_size("PEPE", 0.0), 0.0)

    def test_exact_multiple_is_stable(self):
        """Kelipatan float tidak boleh memangkas satu step."""
        for size in (0.5, 0.3, 0.7, 1.5, 0.1):
            self.assertAlmostEqual(self.ex.quantize_size("BTC", size), size)

    def test_negative_keeps_sign(self):
        self.assertLess(self.ex.quantize_size("BTC", -0.0123456789), 0)

    def test_integer_lot_coin(self):
        """Koin dengan lot 1 tidak boleh pecah."""
        self.assertEqual(self.ex.quantize_size("PEPE", 5.7), 5.0)

    def test_unknown_coin_passes_through(self):
        self.assertEqual(self.ex.quantize_size("TIDAKADA", 1.23456), 1.23456)

    def test_price_quantized_sensibly(self):
        self.assertEqual(self.ex.quantize_price("BTC", 2500.5555), 2500.56)
        self.assertEqual(self.ex.quantize_price("BTC", 0.0), 0.0)

    def test_order_applies_quantization(self):
        """Size yang dikirim harus yang sudah dibulatkan."""
        sent = {}

        class _Exch:
            def order(self_inner, coin, is_buy, size, price, ot,
                      reduce_only=False, cloid=None):
                sent["size"] = size
                sent["price"] = price
                return {"status": "ok",
                        "filled": {"totalSz": size, "avgPx": price},
                        "oid": 1}

        self.ex.exchange = _Exch()
        self.ex.quantize_size = lambda coin, s: 0.5
        self.ex.quantize_price = lambda coin, p: 100.0
        outcome = self.ex.place_limit_order("BTC", True, 0.123456789, 100.5,
                                            cloid="c")
        self.assertTrue(outcome.ok)
        self.assertEqual(sent["size"], 0.5)


class _Mids:
    def __init__(self, data):
        self._data = data

    def all_mids(self):
        return self._data


class TestPendingFillMonitoring(unittest.IsolatedAsyncioTestCase):
    """
    Order GTC yang telat terisi tetap harus dilindungi.

    Order limit tidak selalu terisi saat dikirim. Kalau bot berhenti
    setelah melihat `filled_size == 0`, posisi itu terbuka di bursa tanpa
    SL dan tanpa TP, dan tidak ada apa pun yang akan memasangnya.
    """

    def _ready(self, exchange, coin="BTC", size=0.05, is_buy=True):
        exchange.open_orders = lambda: [{"coin": coin, "isBuy": is_buy,
                                         "limitPx": 100.0}]
        exchange.positions = lambda: [
            {"position": {"coin": coin, "szi": size, "entryPx": 100.0}}]
        exchange.exchange = _Mids({coin: 100.0})
        return _engine(exchange)

    async def test_late_fill_gets_protection(self):
        ex = FakeExchange()
        eng = self._ready(ex)

        filled = await eng.check_pending_fills()

        self.assertEqual(len(filled), 1)
        self.assertIn("BTC / USDC:USDC", eng.positions)
        triggers = [c for c in ex.calls if c[0] == "place_trigger_order"]
        self.assertEqual(len(triggers), 2, "SL dan TP harus dipasang")

    async def test_no_position_means_no_action(self):
        ex = FakeExchange()
        eng = self._ready(ex)
        ex.positions = lambda: []

        self.assertEqual(await eng.check_pending_fills(), [])
        self.assertNotIn("place_trigger_order", _names(ex))

    async def test_already_protected_is_skipped(self):
        """Posisi yang sudah dilindungi tidak dilindungi dua kali."""
        from trading.live.engine import LivePosition

        ex = FakeExchange()
        eng = self._ready(ex)
        eng.positions["BTC / USDC:USDC"] = LivePosition(
            symbol="BTC / USDC:USDC", coin="BTC", side="LONG", size=0.05,
            entry_price=100.0, stop_loss=95.0, take_profit=110.0, leverage=5,
            sl_order_id=1, tp_order_id=2,
        )

        self.assertEqual(await eng.check_pending_fills(), [])
        self.assertNotIn("place_trigger_order", _names(ex))

    async def test_short_fill_uses_buy_trigger(self):
        """Posisi SHORT yang telat terisi harus dapat trigger BUY."""
        ex = FakeExchange()
        eng = self._ready(ex, size=-0.05, is_buy=False)

        await eng.check_pending_fills()

        triggers = [c for c in ex.calls if c[0] == "place_trigger_order"]
        self.assertTrue(triggers)
        for call in triggers:
            self.assertTrue(call[1]["is_buy"],
                            "trigger untuk SHORT harus BUY")

    async def test_short_stop_is_above_entry(self):
        """
        SL untuk SHORT harus DI ATAS entry.

        SL di bawah entry untuk posisi short berarti order langsung
        terisi: posisi tertutup instan dengan kerugian.
        """
        ex = FakeExchange()
        eng = self._ready(ex, size=-0.05, is_buy=False)

        await eng.check_pending_fills()

        stops = [c for c in ex.calls if c[0] == "place_trigger_order"
                 and c[1]["tpsl"] == "sl"]
        self.assertTrue(stops, "SL harus dipasang")
        for call in stops:
            self.assertGreater(call[1]["trigger_price"], 100.0,
                               "SL untuk SHORT harus di atas harga entry")


class HealthMids:
    def all_mids(self):
        return {"BTC": 100.0}


class TestHealthCheck(unittest.IsolatedAsyncioTestCase):
    """
    Health check menentukan boleh-tidaknya bot mengirim order.

    Lapisan terakhir sebelum uang benar-benar bergerak: kalau bursa tidak
    bisa dibaca atau posisi tidak cocok, bot yang tetap mengirim order akan
    menebak, dan menebak posisi berarti menggandakan eksposur.
    """

    def _wire(self, exchange):
        exchange.exchange = HealthMids()
        if not hasattr(exchange, "open_orders"):
            exchange.open_orders = lambda: []
        return _engine(exchange)

    def _position(self, side="LONG", size=0.01):
        from trading.live.engine import LivePosition

        return LivePosition(
            symbol="BTC / USDC:USDC", coin="BTC", side=side, size=size,
            entry_price=100.0, stop_loss=95.0, take_profit=110.0, leverage=5,
            sl_order_id=1, tp_order_id=2,
        )

    async def test_healthy_when_empty(self):
        ex = FakeExchange()
        ex.positions = lambda: []
        eng = self._wire(ex)

        h = await eng.health_check()
        self.assertTrue(h["ok"], str(h["problems"]))

    async def test_unreachable_exchange_blocks(self):
        """Bursa tidak terbaca = berhenti, bukan menebak."""
        ex = FakeExchange()
        ex.positions = lambda: []
        eng = self._wire(ex)

        # Di-set setelah _wire, karena _wire menulis .exchange tapi tidak
        # menyentuh .info -- dan `info` yang jadi sumber `universe`.
        class Broken:
            def meta(self_inner):
                raise RuntimeError("jaringan putus")

        ex.info = Broken()

        h = await eng.health_check()
        self.assertFalse(h["ok"])
        self.assertFalse(h["reachable"])
        self.assertTrue(eng.gate.engaged, "kill switch harus menyala")

    async def test_unknown_remote_position_blocks(self):
        """Posisi di bursa tanpa catatan lokal = bahaya terbesar."""
        ex = FakeExchange()
        ex.positions = lambda: [
            {"position": {"coin": "BTC", "szi": 0.5, "entryPx": 100.0}}]
        eng = self._wire(ex)

        h = await eng.health_check()
        self.assertFalse(h["ok"])
        self.assertTrue(any("tanpa catatan lokal" in p
                            for p in h["problems"]), str(h["problems"]))

    async def test_missing_local_position_blocks(self):
        ex = FakeExchange()
        ex.positions = lambda: []
        eng = self._wire(ex)
        eng.positions["BTC / USDC:USDC"] = self._position("LONG")

        h = await eng.health_check()
        self.assertFalse(h["ok"])
        self.assertTrue(any("hilang di bursa" in p
                            for p in h["problems"]), str(h["problems"]))

    async def test_kill_switch_short_circuits(self):
        ex = FakeExchange()
        ex.positions = lambda: []
        eng = _engine(ex, _gate(kill=True))

        h = await eng.health_check()
        self.assertFalse(h["ok"])
        self.assertIn("kill switch aktif", h["problems"])

    async def test_resting_order_without_position_flags(self):
        ex = FakeExchange()
        ex.positions = lambda: []
        ex.open_orders = lambda: [{"coin": "BTC"}]
        eng = self._wire(ex)

        h = await eng.health_check()
        self.assertFalse(h["ok"])
        self.assertTrue(any("order resting" in p for p in h["problems"]),
                        str(h["problems"]))