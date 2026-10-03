"""
Item (b) Fase 1: fill SL/TP di sisi bursa harus terdeteksi dan dicatat.

MASALAH
-------
Tidak ada kode di `trading/live/` yang membaca fill dari bursa. Ketika
SL atau TP benar-benar fires di bursa, Python tidak diberi tahu.
Siklus berikutnya `health_check` melihat koin hilang dari bursa tapi
masih ada di `self.positions` -> "posisi tercatat lokal tapi hilang di
bursa" -> `engage_kill_switch`.

Efeknya: strike pertama pada setiap posisi -- termasuk take profit yang
wajar -- menghentikan trading secara permanen.

Selain itu, `DAILY_LOSS_LIMIT` tidak punya sumber angka: `fill()` yang
menutup posisi di bursa tidak pernah memanggil `record_realized_pnl`,
karena tidak ada jalur yang membaca fill itu sama sekali.

SUMBER KEBENARAN: `userFills`
-----------------------------
Respons testnet yang direkam (hl_live_fixtures.py) menunjukkan:

    {"coin": "BTC", "px": "85171.0", "sz": "0.00069", "side": "B",
     "time": 1791048255070, "startPosition": "-0.24622",
     "dir": "Close Short", "closedPnl": "-0.0414",
     "oid": 61756806860, "crossed": false, "fee": "-0.001763",
     "tid": 789291717218145, "feeToken": "USDC"}

Tiga hal yang menentukan dan hanya ada di respons nyata:
  * `dir` membedakan Open/Close dan Long/Short
  * `closedPnl` sudah NET di bursa (fee_running dikurangi)
  * `fee` negatif; `abs(fee)` adalah biaya yang dibayar
  * `tid` pengenal unik per fill -- dipakai dedup
"""
import asyncio
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from hl_live_fixtures import (
    CLEARINGHOUSE_STATE_EMPTY,
    CLEARINGHOUSE_STATE_WITH_POSITION,
    FILL_CLOSE_SHORT_BTC_LOSS,
    FILL_CLOSE_SHORT_BTC_PROFIT,
    FILL_CLOSE_SHORT_BTC_SMALL_LOSS,
    FILL_OPEN_SHORT_BTC,
    FIXTURE_ADDRESS,
    make_info_double,
)
from repro_helpers import clean_gate

from core.config import LiveConfig
from trading.live.client import LiveExchange
from trading.live.engine import LiveEngine, LivePosition


def make_exchange(fills=None, state=None, open_orders=None):
    """LiveExchange dengan Info yang disuntik fixture rekaman."""
    ex = LiveExchange.__new__(LiveExchange)
    ex.testnet = True
    ex._account_address = None
    ex._rules = None
    ex.base_url = "https://api.hyperliquid-testnet.xyz/info"
    ex.info = make_info_double(
        state if state is not None else CLEARINGHOUSE_STATE_EMPTY
    )
    ex.info._fills = list(fills or [])
    ex.info._open_orders = list(open_orders or [])
    ex.exchange = None
    ex.wallet = None
    ex.address = FIXTURE_ADDRESS
    ex.query_address = FIXTURE_ADDRESS
    return ex


class TestFillReading(unittest.TestCase):
    """
    Fungsi baca fill harus membaca respons apa adanya.

    Ini fakta tentang BURSA, bukan defect produksi -- jadi harus lulus
    dan mengunci bentuk data yang salah.
    """

    def test_fill_direction_classification(self):
        """
        `dir` dari bursa harus diklasifikasikan dengan benar.

        Salah baca di sini berarti salah klasifikasi Long/Short, yang
        berujung menghitung PnL dengan tanda terbalik.
        """
        from trading.live.engine import fill_direction_token

        self.assertEqual(
            fill_direction_token(FILL_OPEN_SHORT_BTC), "OPEN_SHORT")
        self.assertEqual(
            fill_direction_token(FILL_CLOSE_SHORT_BTC_LOSS), "CLOSE_SHORT")
        self.assertEqual(
            fill_direction_token(FILL_CLOSE_SHORT_BTC_PROFIT), "CLOSE_SHORT")

    def test_fill_direction_with_long_variant(self):
        """Varian Long harus dikenali juga, walau fixture kita Short."""
        from trading.live.engine import fill_direction_token

        long_open = dict(FILL_OPEN_SHORT_BTC, dir="Open Long")
        long_close = dict(FILL_CLOSE_SHORT_BTC_LOSS, dir="Close Long")
        self.assertEqual(fill_direction_token(long_open), "OPEN_LONG")
        self.assertEqual(fill_direction_token(long_close), "CLOSE_LONG")

    def test_non_trade_fill_is_ignored(self):
        """
        Settlement dan fill di coin non-perp harus diabaikan.

        `userFills` testnet memuat 2.000 baris; sebagian besar Settlement
        dan coin seperti "nxlb:SPLIT". Kalau ikut diproses, setiap
        settlement akan terlihat seperti penutupan posisi.
        """
        from trading.live.engine import fill_direction_token

        self.assertIsNone(
            fill_direction_token({"coin": "nxlb:SPLIT", "dir": "Settlement"}))
        self.assertIsNone(fill_direction_token({"coin": "BTC"}))
        self.assertIsNone(fill_direction_token({"coin": "BTC", "dir": ""}))

    def test_fee_is_read_as_positive_cost(self):
        """
        `fee` di respons bursa negatif. Biaya yang dicatat harus positif.

        Kalau fee negatif ikut masuk ke `realized_pnl` sebagai expenses,
        PnL terlihat lebih untung daripada kenyataan -- persis kelas bug
        yang_fee config dulu sebabkan.
        """
        from trading.live.engine import fill_fee_cost

        self.assertAlmostEqual(
            fill_fee_cost(FILL_CLOSE_SHORT_BTC_LOSS), 0.001763, places=9
        )
        self.assertGreater(fill_fee_cost(FILL_CLOSE_SHORT_BTC_LOSS), 0.0)


class TestPollExchangeFills(unittest.IsolatedAsyncioTestCase):
    """
    `poll_exchange_fills()` harus membaca fill dan mengembalikan yang
    benar-benar baru saja terjadi.
    """

    async def test_returns_closing_fills(self):
        """Fill 'Close' harus dikembalikan sebagai penutupan."""
        ex = make_exchange(fills=[FILL_CLOSE_SHORT_BTC_LOSS])
        eng = LiveEngine(gate=clean_gate(), exchange=ex, cfg=LiveConfig())

        found = await eng.poll_exchange_fills()
        closes = [f for f in found if f["kind"] == "CLOSE"]
        self.assertEqual(
            len(closes), 1,
            "fill Close dari bursa harus terdeteksi; yang ditemukan: %r" % found,
        )
        self.assertEqual(closes[0]["coin"], "BTC")
        self.assertAlmostEqual(closes[0]["closed_pnl"], -0.0414, places=9)

    async def test_open_fill_is_not_treated_as_close(self):
        """
        Fill 'Open' bukan penutupan.

        Kalau ini salah klasifikasi, bot akan menutup posisi yang baru
        dibuka -- dan itu persis yang terjadi saat order pertama masuk.
        """
        ex = make_exchange(fills=[FILL_OPEN_SHORT_BTC])
        eng = LiveEngine(gate=clean_gate(), exchange=ex, cfg=LiveConfig())

        found = await eng.poll_exchange_fills()
        closes = [f for f in found if f["kind"] == "CLOSE"]
        self.assertEqual(closes, [], "fill Open salah diklasifikasi sebagai Close")

    async def test_same_fill_not_returned_twice(self):
        """
        Fill yang sama tidak boleh diproses dua kali.

        Tanpa dedup, satu SL yang fire akan dicatat dua kali: PnL
        terhitung dua kali dan DAILY_LOSS_LIMIT terpakai dua kali.
        """
        ex = make_exchange(fills=[FILL_CLOSE_SHORT_BTC_LOSS])
        eng = LiveEngine(gate=clean_gate(), exchange=ex, cfg=LiveConfig())

        first = await eng.poll_exchange_fills()
        second = await eng.poll_exchange_fills()
        self.assertEqual(len(first), 1, "poll pertama harus melihat 1 fill")
        self.assertEqual(
            second, [],
            "fill yang sama muncul lagi di poll kedua -- tidak ada dedup",
        )

    async def test_partial_close_fill_is_reported_with_its_own_size(self):
        """
        Partial exit di bursa harus dilaporkan dengan ukuran ASLINYA.

        Fixture `FILL_CLOSE_SHORT_BTC_SMALL_LOSS` menutup 0.00013 dari
        posisi 0.24567. Kalau bot memakai ukuran baris SQLite (0.24567)
        untuk fill 0.00013, PnL-nya 1.890x lebih besar dari kenyataan.
        """
        ex = make_exchange(fills=[FILL_CLOSE_SHORT_BTC_SMALL_LOSS])
        eng = LiveEngine(gate=clean_gate(), exchange=ex, cfg=LiveConfig())

        found = await eng.poll_exchange_fills()
        closes = [f for f in found if f["kind"] == "CLOSE"]
        self.assertEqual(len(closes), 1)
        self.assertAlmostEqual(
            closes[0]["size"], 0.00013, places=9,
            msg="ukuran fill harus dari bursa, bukan dari baris posisi",
        )


class TestHealthCheckAcceptsExchangeTriggeredClose(unittest.IsolatedAsyncioTestCase):
    """
    Fill SL/TP di bursa TIDAK boleh dianggap anomali.
    """

    def _engine_with_local_position_and_fill(self, fill, exchange_state):
        ex = make_exchange(fills=[fill], state=exchange_state)
        eng = LiveEngine(gate=clean_gate(), exchange=ex, cfg=LiveConfig())
        eng.positions["BTC/USDT:USDT"] = LivePosition(
            symbol="BTC/USDT:USDT", coin="BTC", side="SHORT", size=0.24567,
            entry_price=85110.1, stop_loss=86000.0, take_profit=83000.0,
            leverage=5, sl_order_id=1, tp_order_id=2,
        )
        return eng

    async def test_stop_loss_fill_does_not_engage_kill_switch(self):
        """
        SL yang fires = posisi tertutup sesuai rencana. Itu BERHASIL.

        Sebelum item (b): health_check melaporkan "posisi tercatat lokal
        tapi hilang di bursa" dan menyalakan kill switch.
        """
        eng = self._engine_with_local_position_and_fill(
            FILL_CLOSE_SHORT_BTC_LOSS, CLEARINGHOUSE_STATE_EMPTY)
        health = await eng.health_check()
        self.assertFalse(
            eng.gate.engaged,
            "kill switch menyala setelah stop-loss normal: %s" % health["problems"],
        )

    async def test_take_profit_fill_does_not_engage_kill_switch(self):
        """TP yang fires juga keberhasilan, bukan kegagalan."""
        eng = self._engine_with_local_position_and_fill(
            FILL_CLOSE_SHORT_BTC_PROFIT, CLEARINGHOUSE_STATE_EMPTY)
        health = await eng.health_check()
        self.assertFalse(
            eng.gate.engaged,
            "kill switch menyala setelah take-profit normal: %s" % health["problems"],
        )

    async def test_real_mismatch_still_engages_kill_switch(self):
        """
        Normalisasi TIDAK boleh membuat health_check buta.

        Posisi lokal yang hilang dari bursa TANPA fill yang menjelaskan
        tetap divergensi nyata -- itu yang harus menyalakan kill switch.
        """
        ex = make_exchange(fills=[], state=CLEARINGHOUSE_STATE_EMPTY)
        eng = LiveEngine(gate=clean_gate(), exchange=ex, cfg=LiveConfig())
        eng.positions["BTC/USDT:USDT"] = LivePosition(
            symbol="BTC/USDT:USDT", coin="BTC", side="SHORT", size=0.1,
            entry_price=85110.1, stop_loss=86000.0, take_profit=83000.0,
            leverage=5, sl_order_id=1, tp_order_id=2,
        )
        health = await eng.health_check()
        self.assertTrue(
            eng.gate.engaged,
            "divergensi nyata tanpa fill penjelasan harus tetap menyalakan "
            "kill switch; problems=%s" % health["problems"],
        )


if __name__ == "__main__":
    unittest.main()
