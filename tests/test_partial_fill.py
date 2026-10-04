"""
Item (c) Fase 1: partial fill harus ditangani.

MASALAH
-------
`place_limit_order` mengembalikan `OrderOutcome.filled_size`, yang bisa
LEBIH KECIL dari ukuran yang diminta. Tidak ada kode yang:

* membandingkan `filled_size` dengan ukuran yang diminta
* membatalkan sisa order yang masih resting
* memasang proteksi sesuai ukuran yang benar-benar terisi

Akibatnya proteksi dipasang untuk ukuran parsial sementara sisa order
GTC tetap hidup tanpa proteksi -- dan kalau bot mati, sisa itu adalah
posisi terbuka tanpa SL.

BENTUK RESPONSA NYATA
---------------------
`frontendOpenOrders` testnet (direkam di hl_live_fixtures.py):

    {"coin": "BTC", "side": "A", "limitPx": "85134.0", "sz": "0.12",
     "oid": 61757018228, "origSz": "0.3", "orderType": "Limit",
     "tif": "Alo", "cloid": "tb-repro-0001", ...}

`origSz` ada di respons itu. Order dengan `sz < origSz` = PARTIALLY
TERISI dan masih resting; selisihnya adalah sisa yang harus dibatalkan.
Tanpa membacanya, bot tidak punya cara mengetahui bahwa masih ada order
aktif untuk koin yang sama.
"""
import asyncio
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from hl_live_fixtures import (
    CLEARINGHOUSE_STATE_EMPTY,
    FILL_OPEN_SHORT_BTC,
    OPEN_ORDER_PARTIALLY_FILLED,
    OPEN_ORDER_RESTING_FULL,
    FIXTURE_ADDRESS,
    make_info_double,
)
from repro_helpers import inside_trading_window

from core.config import LiveConfig
from trading.live.client import LiveExchange, OrderOutcome
from trading.live.engine import LiveEngine, LivePosition


def make_exchange(order_outcome=None, open_orders=None, fills=None,
                  held_positions=None):
    """
    Bursa yang perilakunya ditentukan test, tapi datanya berdasar
    respons rekaman.

    Yang di-fake hanya JAWABAN bursa untuk skenario yang sedang diuji.
    Tidak ada yang dilewati: `submit_order` produksi yang berjalan,
    `place_trigger_order` produksi yang berjalan, dan pemeriksa
    partial fill yang baru diuji bekerja di atas jawaban itu.
    """
    ex = LiveExchange.__new__(LiveExchange)
    ex.testnet = True
    ex._account_address = None
    ex._rules = None
    ex.base_url = "https://api.hyperliquid-testnet.xyz/info"
    ex.info = make_info_double(CLEARINGHOUSE_STATE_EMPTY)
    ex.info._fills = list(fills or [])
    ex.info._open_orders = list(open_orders or [])
    ex.wallet = None
    ex.address = FIXTURE_ADDRESS
    ex.query_address = FIXTURE_ADDRESS
    ex.canceled = []
    ex.protected = []
    ex.submitted = []

    outcome_box = {"o": order_outcome}

    def _place_limit_order(coin, is_buy, size, price,
                           reduce_only=False, cloid=None):
        ex.submitted.append(
            {"coin": coin, "size": size, "price": price, "cloid": cloid})
        return outcome_box["o"]

    def _place_trigger_order(coin, is_buy, size, trigger_price, tpsl,
                             reduce_only=True):
        ex.protected.append(
            {"coin": coin, "size": size, "trigger": trigger_price,
             "tpsl": tpsl})
        return OrderOutcome(ok=True, filled_size=size, order_id=100)

    def _cancel(coin, oid):
        ex.canceled.append((coin, oid))
        return "cancelled"

    def _cancel_all(coin):
        ex.canceled.append((coin, None))
        return "cancelled"

    def _positions():
        return held_positions or []

    def _open_orders():
        return ex.info._open_orders

    def _mid_price(coin):
        return 85134.0

    ex.exchange = None
    ex.place_limit_order = _place_limit_order
    ex.place_trigger_order = _place_trigger_order
    ex.cancel = _cancel
    ex.cancel_all = _cancel_all
    ex.positions = _positions
    ex.open_orders = _open_orders
    ex.mid_price = _mid_price

    # Gate live menolak order yang collapsible lewat batas notional /
    # collateral. Tanpa ini test akan lulus karena GERBANG menolak, bukan
    # karena logika partial fill yang diuji benar -- jebakan yang sama
    # sudah menimpa dua kali di Fase 0 dan (b).
    ex.free_collateral = lambda: 10000.0
    ex.total_notional = lambda: 0.0
    ex.symbol_notional = lambda coin: 0.0
    ex.set_leverage = lambda coin, lev, is_cross=True: {"ok": True}
    return ex


class TestRestingOrderRemainingSize(unittest.TestCase):
    """
    Sisa order resting harus bisa dibaca dari `origSz`.

    Tanpa ini, bot tidak bisa tahu ada order aktif yang belumnibus
    terlindungi.
    """

    def test_remaining_size_reads_origsz(self):
        """`sz < origSz` berarti partially filled; selisihnya sisa."""
        from trading.live.engine import resting_order_remaining

        self.assertAlmostEqual(
            resting_order_remaining(OPEN_ORDER_PARTIALLY_FILLED),
            0.18, places=9,
            msg="sisa = origSz 0.3 - sz 0.12 = 0.18",
        )

    def test_fully_filled_order_has_no_remaining(self):
        """
        Order yang BELUM terisi (`sz == origSz`) tidak punya "sisa".

        Sisa di sini berarti "bagian yang sudah dibatalkan". Order GTC
        yang belum terisi sama sekali memang normal dan harus dibiarkan
        -- membatalkannya justru menghentikan strategi.
        """
        from trading.live.engine import resting_order_remaining

        self.assertAlmostEqual(
            resting_order_remaining(OPEN_ORDER_RESTING_FULL), 0.0, places=9)

    def test_missing_fields_do_not_raise(self):
        """
        Bursa kadang mengirim order tanpa `origSz` (mis. trigger order).

        Fungsi harus mengembalikan 0.0, bukan melempar -- pemanggilnya
        berjalan di loop 0.3 detik.
        """
        from trading.live.engine import resting_order_remaining

        self.assertEqual(resting_order_remaining({}), 0.0)
        self.assertEqual(resting_order_remaining({"coin": "BTC"}), 0.0)
        self.assertEqual(resting_order_remaining(None), 0.0)

    def test_trigger_order_is_not_treated_as_resting(self):
        """
        Trigger order (SL/TP) ada di `frontendOpenOrders` juga, tapi itu
        BUKAN order yang perlu dibatalkan -- justru itu proteksinya.

        `isTrigger` membedakan keduanya.
        """
        from trading.live.engine import resting_order_remaining

        trigger = dict(OPEN_ORDER_RESTING_FULL, isTrigger=True,
                       origSz="5.0", sz="5.0")
        self.assertEqual(
            resting_order_remaining(trigger), 0.0,
            "trigger order harusAlways 0 -- itu SL/TP, bukan sisa order",
        )


class TestPartialFillProtection(unittest.IsolatedAsyncioTestCase):
    """
    Proteksi harus dipasang untuk ukuran yang BENAR-BENAR terisi.
    """

    async def _submit(self, outcome, open_orders, held_positions):
        ex = make_exchange(
            order_outcome=outcome, open_orders=open_orders,
            held_positions=held_positions)
        eng = inside_trading_window()
        eng.exchange = ex
        return ex, await eng.submit_order(
            "BTC", "BTC/USDT:USDT", True, 0.001, 85134.0,
            stop_loss=84000.0, take_profit=87000.0, leverage=5,
            cloid="tb-partial-0001")

    async def test_protection_uses_filled_size_not_requested(self):
        """
        Order 0.004 terisi 0.004 dari 5 level book... tapi untuk partial
        fill, proteksi WAJIB mengikuti `filled_size`.
        """
        outcome = OrderOutcome(ok=True, filled_size=0.0003,
                               avg_price=85134.0, order_id=4242)
        ex, result = await self._submit(outcome, [], [])
        self.assertEqual(
            len(ex.protected), 2,
            "SL dan TP harus dipasang untuk ukuran yang terisi",
        )
        for p in ex.protected:
            self.assertAlmostEqual(
                p["size"], 0.0003, places=9,
                msg="proteksi untuk %s memakai ukuran %.6g, harus %.6g "
                    "(ukuran yang BENAR-BENAR terisi)"
                    % (p["tpsl"], p["size"], 0.0003),
            )

    async def test_remainder_is_canceled(self):
        """
        Sisa order HARUS dibatalkan.

        Kalau tidak, ada posisi terbuka tanpa SL yang tidak akan pernah
        hilang -- bot sudah attaching proteksi ke ukuran yang terisi dan
        menganggap order selesai.
        """
        outcome = OrderOutcome(ok=True, filled_size=0.0003,
                               avg_price=85134.0, order_id=4242)
        resting = dict(OPEN_ORDER_PARTIALLY_FILLED,
                       coin="BTC", sz="0.0007", origSz="0.001")
        ex, result = await self._submit(outcome, [resting], [])

        self.assertTrue(
            ex.canceled,
            "sisa order partial harus dibatalkan; yang dibatalkan: %r "
            "(fill 0.0012 dari 0.004 -> sisa 0.0028 masih resting)"
            % ex.canceled,
        )

    async def test_partial_fill_is_reported_in_message(self):
        """
        Hasil order harus menyebutkan bahwa ini partial fill.

        `executor._persist_open` membaca `result["message"]` untuk
        menentukan apa yang dicatat; partial fill yang tidak
        discriminated akan terlihat sama dengan fill penuh.
        """
        outcome = OrderOutcome(ok=True, filled_size=0.0003,
                               avg_price=85134.0, order_id=4242)
        resting = dict(OPEN_ORDER_PARTIALLY_FILLED,
                       coin="BTC", sz="0.0007", origSz="0.001")
        _ex, result = await self._submit(outcome, [resting], [])

        msg = str(result.get("message", "")).lower()
        self.assertIn(
            "partial", msg,
            "pesan harus menyebutkan partial fill; dapat: %r" % result.get("message"),
        )
        self.assertIn(
            "0.0003", msg,
            "pesan harus menyebut berapa yang benar-benar terisi; dapat: %r"
            % result.get("message"),
        )

    async def test_full_fill_is_not_reported_as_partial(self):
        """Fill penuh harus tetap dilaporkan sebagai fill penuh."""
        outcome = OrderOutcome(ok=True, filled_size=0.004,
                               avg_price=85134.0, order_id=4242)
        _ex, result = await self._submit(outcome, [], [])

        msg = str(result.get("message", "")).lower()
        self.assertNotIn(
            "partial", msg,
            "fill penuh tidak boleh dilaporkan partial; dapat: %r"
            % result.get("message"),
        )

    async def test_unfilled_order_attaches_no_protection(self):
        """
        Order yang tidak terisi sama sekali tidak boleh mendapat
        proteksi.

        Memasang SL/TP untuk posisi yang belum ada menghasilkan order
        yatim yang menggantung selamanya.
        """
        outcome = OrderOutcome(ok=True, filled_size=0.0,
                               avg_price=0.0, order_id=4242)
        ex, result = await self._submit(outcome, [OPEN_ORDER_RESTING_FULL], [])

        self.assertEqual(
            ex.protected, [],
            "proteksi dipasang untuk order yang tidak terisi",
        )
        self.assertFalse(result.get("protected"),
                         "result harus melaporkan tidak terlindungi")


if __name__ == "__main__":
    unittest.main()
