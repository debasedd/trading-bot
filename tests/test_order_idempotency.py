"""
DEFECT-5 / item (d): retry setelah timeout tidak boleh menggandakan posisi.

MASALAH
-------
`submit_order()` mengirim order lalu, kalau respons hilang, pemanggil
biasanya mencoba lagi. Order PERTAMA bisa saja sudah masuk dan terisi
di bursa sementara Python tidak pernah tahu -- karena responsnya hilang
karena timeout, bukan karena order ditolak.

Bentuk urutan yang harus dicegah:

    attempt 1  ->  TimeoutError  (order sebenarnya MASUK dan TERISI)
    attempt 2  ->  terkirim       (posisi sekarang 2x)

Ekorsnya lebih buruk: dua posisi dengan dua pasang SL/TP, sementara DB
hanya mencatat satu.

KENAPA TIDAK BISA DITANYAKAN KE BURSA
-------------------------------------
Saya sudah memeriksa endpoint /info testnet (publik, tanpa key):

    orderStatus + oid numerik  -> 200 {"status":"order", "order":{...}}
    orderStatus + oid = hex    -> 422 Failed to deserialize the JSON body
    orderStatus + cloid        -> 422 Failed to deserialize the JSON body
    queryOrderByCloid          -> 422 (tidak ada di bursa)

Jadi tidak ada "apakah order saya sudah masuk?" via cloid. Satu-satunya
caraIClaim adalah `oid` yang dikembalikan bursa -- dan `oid` justru
yang hilang saat timeout.

`cancel_by_cloid` ADA, tapi itu action L1 (membatalkan), bukan query.
Artinya: kalau order kita masih hidup, cloid bisa membatalkannya.
Kalau sudah terisi, tidak ada yang bisaIndonesia menanyakan.

KONSEKUENSI DESAIN
------------------
Karena bursa tidak bisa menjawab, identitas harus datang dari dalam bot
sendiri: cloid yang sama = order yang sama. Kalau bot sudah mengirim
dengan cloid tertentu, submission kedua dengan cloid itu tidak boleh
mengirim apa pun -- ia harus melaporkan apa yang diketahui soal order
pertama.

Ini bukan sekadar optimasi. Ini satu-satunya opsi yang tidak menebak.
"""
import asyncio
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from hl_live_fixtures import (
    CLEARINGHOUSE_STATE_EMPTY,
    FIXTURE_ADDRESS,
    make_info_double,
)
from repro_helpers import inside_trading_window

from core.config import LiveConfig
from trading.live.client import LiveExchange, OrderOutcome
from trading.live.engine import LiveEngine, LivePosition
from trading.live.executor import make_cloid


def make_exchange(order_outcomes=None, raises=None, held_positions=None,
                  open_orders=None):
    """
    Bursa yang perilakunya ditentukan test; datanya berdasar respons
    rekaman testnet.

    Yang di-fake hanya JAWABAN bursa. `submit_order`, gerbang
    `SafetyGate`, dan logika idempotensi produksi semuanya berjalan.
    """
    ex = LiveExchange.__new__(LiveExchange)
    ex.testnet = True
    ex._account_address = None
    ex._rules = None
    ex.base_url = "https://api.hyperliquid-testnet.xyz/info"
    ex.info = make_info_double(CLEARINGHOUSE_STATE_EMPTY)
    ex.info._fills = []
    ex.info._open_orders = list(open_orders or [])
    ex.wallet = None
    ex.address = FIXTURE_ADDRESS
    ex.query_address = FIXTURE_ADDRESS
    ex.exchange = None
    ex.canceled = []
    ex.submitted = []

    queue = list(order_outcomes or [])
    raiser = raises
    box = {"n": 0}

    def _place_limit_order(coin, is_buy, size, price,
                           reduce_only=False, cloid=None):
        box["n"] += 1
        ex.submitted.append({
            "coin": coin, "size": size, "price": price, "cloid": cloid,
            "attempt": box["n"],
        })
        if raiser is not None and box["n"] == 1:
            raise raiser
        if queue:
            return queue.pop(0)
        return OrderOutcome(ok=True, filled_size=size, avg_price=price,
                            order_id=1000 + box["n"])

    def _place_trigger_order(coin, is_buy, size, trigger_price, tpsl,
                             reduce_only=True):
        return OrderOutcome(ok=True, filled_size=size, order_id=2000)

    def _cancel(coin, oid):
        ex.canceled.append((coin, oid))
        return "cancelled"

    ex.place_limit_order = _place_limit_order
    ex.place_trigger_order = _place_trigger_order
    ex.cancel = _cancel
    ex.cancel_all = lambda coin: "cancelled"
    ex.positions = lambda: held_positions or []
    ex.open_orders = lambda: ex.info._open_orders
    ex.mid_price = lambda coin: 85134.0

    # Gerbang live menolak order yang collapsible lewat batas notional.
    # Tanpa ini test akan lulus karena GERBANG menolak -- jebakan yang
    # sudah menimpa tiga kali di Fase 0 dan Fase 1.
    ex.free_collateral = lambda: 10000.0
    ex.total_notional = lambda: 0.0
    ex.symbol_notional = lambda coin: 0.0
    ex.set_leverage = lambda coin, lev, is_cross=True: {"ok": True}
    return ex


def engine_for(ex):
    """Engine dengan jam yang dikendalikan; lihat `inside_trading_window`."""
    eng = inside_trading_window()
    eng.exchange = ex
    return eng


async def _submit(eng, cloid, size=0.001, price=85000.0):
    """Ukuran 0.001 BTC = 85 USDC, di bawah max_order_notional (100)."""
    return await eng.submit_order(
        "BTC", "BTC/USDT:USDT", True, size, price,
        stop_loss=84000.0, take_profit=87000.0, leverage=5, cloid=cloid,
    )


async def _submit_that_passes_the_gate(eng, ex, cloid):
    """
    Kirim order dan buktikan order itu MELEWATI gerbang.

    Ini guard yang sama yang dipakai test partial fill. Tanpa assert ini,
    test idempotensi bisa lulus karena GERBANG menolak order (notional,
    collateral, kill switch, atau jendela waktu) -- bukan karena retry
    tidak mengirim apa pun. Pola itu sudah menimpa tiga kali di Fase 0
    dan Fase 1.
    """
    result = await _submit(eng, cloid)
    self_check = len(ex.submitted)
    assert self_check == 1, (
        "order pertama TIDAK melewati gerbang, jadi test ini tidak "
        "menguji apa pun. Ditolak dengan: %r"
        % result.get("message")
    )
    return result


class TestRetryAfterTimeoutDoesNotResend(unittest.IsolatedAsyncioTestCase):
    """
    Inti item (d):_timeout tidak boleh berubah jadi order kedua.
    """

    async def test_same_cloid_is_not_sent_twice_after_timeout(self):
        """
        Panggilan pertama timeout; panggilan kedua dengan cloid sama.

        Order pertama bisa saja sudah masuk. Mengirim ulang berarti
        posisi tergandakan, dan dua pasang SL/TP untuk satu order.
        """
        cloid = make_cloid()
        ex = make_exchange(raises=TimeoutError("respons hilang"))
        eng = engine_for(ex)

        await _submit_that_passes_the_gate(eng, ex, cloid)
        second = await _submit(eng, cloid)

        self.assertEqual(
            len(ex.submitted), 1,
            "cloid %s dikirim %d kali. Setelah timeout, retry tidak boleh "
            "mengirim order kedua -- order pertama mungkin sudah masuk."
            % (cloid, len(ex.submitted)),
        )

    async def test_second_attempt_reports_the_uncertainty(self):
        """
        Panggilan kedua harus MENJAWAB, bukan diam.

        Kalau ia melaporkan sukses, yang membaca log akan mengira order
        kedua terkirim. Kalau ia melaporkan gagal biasa, yang membaca log
        akan mencoba sekali lagi -- dan itulah penggandaan posisi.
        """
        cloid = make_cloid()
        ex = make_exchange(raises=TimeoutError("respons hilang"))
        eng = engine_for(ex)

        await _submit_that_passes_the_gate(eng, ex, cloid)
        second = await _submit(eng, cloid)

        self.assertIn(
            "uncertain", str(second.get("message", "")).lower(),
            "percobaan kedua harus melaporkan status yang tidak pasti; "
            "dapat: %r" % second.get("message"),
        )

    async def test_uncertain_order_does_not_engage_kill_switch(self):
        """
Timeout itu kejadian tunggal, bukan kegagalan sistem.

        `submit_order` jalur error memanggil `gate.record_error()`.
        Kalau percobaan kedua juga dihitung sebagai error, tiga timeout
        beruntun menyalakan kill switch pada sistem yang sebenarnya baik.
        """
        cloid = make_cloid()
        ex = make_exchange(raises=TimeoutError("respons hilang"))
        eng = engine_for(ex)
        gate = eng.gate

        await _submit_that_passes_the_gate(eng, ex, cloid)
        gate.counters.consecutive_errors = 0
        await _submit(eng, cloid)

        self.assertEqual(
            gate.counters.consecutive_errors, 0,
            "percobaan ulang yang tidak mengirim apa pun tidak boleh "
            "dihitung sebagai error; consecutive_errors=%r"
            % gate.counters.consecutive_errors,
        )
        self.assertFalse(gate.engaged,
                         "kill switch menyala pada sistem yang benar")

    async def test_a_different_cloid_is_still_allowed(self):
        """
        Idempotensi tidak boleh membekukan bot.

        Dua order BEDA (cloid berbeda, misalnya dua strategi atau dua
        percobaan yang memang disengaja) harus tetap bisa dikirim.
        """
        ex = make_exchange()
        eng = engine_for(ex)

        c1 = make_cloid()
        c2 = make_cloid()
        await _submit_that_passes_the_gate(eng, ex, c1)
        await _submit(eng, c2)

        self.assertEqual(
            len(ex.submitted), 2,
            "dua order dengan cloid berbeda adalah dua order; "
            "pengiriman: %r" % [s["cloid"] for s in ex.submitted],
        )

    async def test_a_fresh_cloid_after_a_definite_failure_is_allowed(self):
        """
        Kalau order pertama benar-benar DITOLAK bursa, itu bukan order
        yang sama -- retry dengan cloid baru harus boleh jalan.

        Yang dihalangi hanya pengulangan identitas yang sama saat status
        order pertama tidak diketahui.
        """
        ex = make_exchange(order_outcomes=[
            OrderOutcome(ok=False, error="insufficient margin"),
        ])
        eng = engine_for(ex)

        first = await _submit(eng, make_cloid())
        self.assertFalse(first.get("success"))
        self.assertEqual(len(ex.submitted), 1,
                         "order yang ditolak bursa harus tetap dikirim")

        second = await _submit(eng, make_cloid())
        self.assertEqual(
            len(ex.submitted), 2,
            "penolakan bursa yang jelas bukan order yang sama; harus boleh "
            "dikirim ulang dengan cloid baru",
        )
        self.assertTrue(second.get("success"))


class TestUncertainOrderIsTracked(unittest.IsolatedAsyncioTestCase):
    """
    Order yang statusnya tidak diketahui harus TERLACAK.

    Kalau tidak dicatat, restart akan hilang dari begitu saja dan order
    yang mungkin masih hidup di bursa tidak akan pernah diketahui.
    """

    async def test_uncertain_cloid_is_exposed_for_inspection(self):
        cloid = make_cloid()
        ex = make_exchange(raises=TimeoutError("respons hilang"))
        eng = engine_for(ex)

        await _submit_that_passes_the_gate(eng, ex, cloid)
        await _submit(eng, cloid)

        uncertain = getattr(eng, "uncertain_orders", None)
        self.assertIsNotNone(
            uncertain,
            "LiveEngine tidak punya catatan order yang statusnya tidak "
            "diketahui; order seperti itu hilang begitu saja saat restart",
        )
        self.assertIn(
            cloid, uncertain,
            "cloid yang timeout harus tercatat sebagai order yang "
            "statusnya tidak diketahui; tercatat: %r" % list(uncertain),
        )


class TestCancelByCloidIsAvailable(unittest.IsolatedAsyncioTestCase):
    """
    Satu-satunya '=' untuk order yang belum tentu terisi.

    Kalau order kita ternyata masih RESTING di bursa, cloid bisa
    membatalkannya -- SDK punya `cancel_by_cloid`. Kalau sudah terisi,
    tidak ada yang bisaWG menanyakan; itu yang membuat pelacakan di atas
    wajib ada.
    """

    def test_live_exchange_can_cancel_by_cloid(self):
        """
        Harus ada jalan membatalkan order yang cloid-nya diketahui.

        Tanpa itu, order yang timeout dan ternyata masih hidup hanya
        bisa dibatalkan per-oid -- dan oid-nya justru yang hilang.
        """
        ex = LiveExchange.__new__(LiveExchange)
        self.assertTrue(
            hasattr(ex, "cancel_by_cloid"),
            "LiveExchange tidak punya jalur pembatalan lewat cloid; "
            "order yang timeout dan ternyata masih resting tidak bisa "
            "dibatalkan tanpa oid, dan oid justru yang hilang saat timeout",
        )

    def test_cancel_is_reachable_from_production_code(self):
        """
        Pembatalan per-order harus benar-benar dipanggil di produksi.

        Dipakai lewat `asyncio.to_thread(self.exchange.cancel, coin, oid)`,
        jadi yang dicari adalah penyebutan `.cancel`, bukan hanya
        `.cancel(`.
        """
        root = pathlib.Path(__file__).resolve().parent.parent
        callers = []
        for py in root.rglob("*.py"):
            rel = py.relative_to(root)
            if rel.parts[0] in ("tests", "build", "research", "data_store"):
                continue
            if rel.name == "client.py":
                continue  # definisinya sendiri
            text = py.read_text(encoding="utf-8", errors="replace")
            for i, line in enumerate(text.splitlines(), 1):
                s = line.strip()
                if "cancel_all" in s or ".cancel()" in s:
                    continue
                if "task.cancel" in s or "asyncio" not in s and ".cancel" not in s:
                    continue
                if ".cancel" in s and "(" in s.split(".cancel")[-1][:30] \
                        or ".cancel," in s or ".cancel)" in s:
                    callers.append("%s:%d  %s" % (rel, i, s[:70]))
        self.assertTrue(
            callers,
            "cancel(coin, oid) tidak dipanggil dari mana pun di produksi; "
            "tidak ada jalur pembatalan per-order",
        )


if __name__ == "__main__":
    unittest.main()