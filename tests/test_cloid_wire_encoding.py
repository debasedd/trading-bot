"""
DEFECT-6: cloid yang bot kirim tidak pernah sampai ke bursa.

MASALAH
-------
`make_cloid()` (executor.py:60) mengembalikan string biasa:

    'tb-b50f716c29034fde'

SDK Hyperliquid mewajibkan `Cloid` -- kelas yang isinya hex `0x` diikuti
32 karakter hex, karena nilainya ikut ditandatangani bersama order:

    order_request_to_order_wire() (signing.py:514)
        order_wire["c"] = order["cloid"].to_raw()

String biasa tidak punya `.to_raw()`, jadiorder GAGAL di lapisan itu --
sebelum signing, sebelum ada yang dikirim ke bursa.

REPRODUKSI (tanpa jaringan, tanpa key, tanpa order)
---------------------------------------------------
    >>> order_request_to_order_wire({..., "cloid": "tb-b50f7..."}, 3)
    AttributeError: 'str' object has no attribute 'to_raw'

BENTUK RESPONSA NYATA
---------------------
Tidak ada respons bursa karena order tidak pernah dikirim. Yang
sebenarnya terjadi adalah exception di dalam proses lokal. Reproduksi
di test ini memakai `Cloid` dari SDK yang SULLITAN -- bukan stub.

DAMPAK
------
`SafetyGate.order_blockers` menolak order opening tanpa cloid
(`Blocker.MISSING_CLOID`, safety.py:395). Jadi pada jalur live:

    cloid wajib  ->  cloid selalu diisi  ->  wire encoding gagal
                                     ->  OrderOutcome(ok=False)

Artinya SETIAP order opening dan SETIAP order closing ditolak. Bot live
belum pernah bisa membuka posisi sama sekali, dan tidak ada satu pun
error yang menyinggung penyebabnya: `place_limit_order` menangkap
dan mengubahnya jadi `OrderOutcome(ok=False, error=...)` yang terlihat
seperti penolakan bursa biasa.

Ini lebih mendahulukan daripada penggandaan posisi yang item (d) takut,
tetapi related: tanpa cloid yang benar, tidak ada client order id yang
bisa dipakai untuk idempotensi maupun pembatalan.
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from hyperliquid.utils.signing import order_request_to_order_wire
from hyperliquid.utils.types import Cloid

from trading.live.client import LiveExchange
from trading.live.executor import make_cloid


class TestMakeCloidIsAcceptedByTheSdk(unittest.TestCase):
    """Yang dihasilkan `make_cloid()` harus bisa dipakai SDK."""

    def test_generated_cloid_passes_sdk_validation(self):
        """
        `Cloid.from_str()` memanggil `_validate()` yang menolak apa pun
        yang bukan `0x` + 32 hex. Kalau ini gagal, order tidak akan
        pernah sampai ke bursa.
        """
        raw = make_cloid()
        try:
            Cloid.from_str(raw)
        except TypeError as exc:
            self.fail(
                "make_cloid() menghasilkan %r yang ditolak SDK: %s. "
                "Order live akan gagal di wire encoding, sebelum dikirim."
                % (raw, exc)
            )

    def test_generated_cloid_is_unique_as_a_cloid(self):
        """Dua order tidak boleh punya client order id yang sama."""
        raw_ids = {make_cloid() for _ in range(200)}
        self.assertEqual(
            len(raw_ids), 200,
            "cloid ulang = retry tidak bisa dibedakan dari order baru",
        )

    def test_generated_cloid_actually_encodes_in_the_wire(self):
        """
        UJUNG: seluruh jalur sampai ke wire harus jalan.

        Ini yang diuji reproduction-nya: `order_request_to_order_wire`
        adalah fungsi SDK sungguhan yang membentuk payload yang ditandatangan.
        """
        from trading.live.client import to_cloid

        order = {
            "coin": "BTC", "is_buy": True, "sz": 0.001,
            "limit_px": 85000.0, "order_type": {"limit": {"tif": "Gtc"}},
            "reduce_only": False, "cloid": to_cloid(make_cloid()),
        }
        try:
            wire = order_request_to_order_wire(order, 3)
        except AttributeError as exc:
            self.fail("wire encoding gagal: %s" % exc)
        self.assertIn(
            "c", wire,
            "order wire harus membawa client order id; dapat: %r" % wire,
        )
        self.assertRegex(wire["c"], r"^0x[0-9a-f]{32}$")


class TestToCloid(unittest.TestCase):
    """Konversi di batas SDK: satu tempat, tidak ditebak."""

    def test_plain_string_becomes_cloid(self):
        from trading.live.client import to_cloid
        self.assertIsInstance(to_cloid("0x" + "ab" * 16), Cloid)

    def test_cloid_passes_through_unchanged(self):
        """Sudah benar bentuknya, tidak dibungkus dua kali."""
        from trading.live.client import to_cloid
        original = Cloid.from_str("0x" + "cd" * 16)
        self.assertIs(to_cloid(original), original)

    def test_none_stays_none(self):
        """
        Order closing dan `emergency_flat` mengirim tanpa cloid. Itu
        sah dan harus tetap bisa.
        """
        from trading.live.client import to_cloid
        self.assertIsNone(to_cloid(None))

    def test_legacy_prefix_style_cloid_is_rejected_loudly(self):
        """
        Bentuk lama (`tb-<hex>`) HARUS ditolak dengan pesan yang menyebut
        cloid -- bukan diteruskan dan gagal diam-diam jauh di dalam SDK.

        `make_cloid()` masih menghasilkan bentuk ini sampai DEFECT-6
        diperbaiki, jadi test ini ikut menjaga keduanya.
        """
        from trading.live.client import to_cloid
        with self.assertRaises(ValueError) as ctx:
            to_cloid("tb-b50f716c29034fde")
        self.assertIn(
            "cloid", str(ctx.exception).lower(),
            "pesan harus menyebut cloid; dapat: %r" % str(ctx.exception),
        )

    def test_short_hex_is_rejected(self):
        from trading.live.client import to_cloid
        with self.assertRaises(ValueError):
            to_cloid("0xabcd")

    def test_non_hex_is_rejected(self):
        from trading.live.client import to_cloid
        with self.assertRaises(ValueError):
            to_cloid("0x" + "zz" * 16)


class TestPlaceLimitOrderPassesAValidCloid(unittest.TestCase):
    """
    `place_limit_order` adalah FUNGSI PRODUKSI yang berjalan di sini.

    Yang digantikan hanya `ex.exchange` --Klien jaringan SDK-- dengan
    permeateks yang mencatat apa yang diterimanya. Pola yang sama dipakai
    test lain di repo ini; tidak ada logika yang diuji yang dilewati.
    """

    def _exchange(self):
        ex = LiveExchange.__new__(LiveExchange)
        ex.testnet = True
        ex._account_address = None
        ex._rules = {"BTC": {"sz_decimals": 5, "sz_min": 0.00001,
                             "sz_max": 1e6, "max_leverage": 25}}
        ex.base_url = "https://api.hyperliquid-testnet.xyz/info"
        ex.address = "0x" + "11" * 20
        ex.query_address = ex.address
        ex.wallet = None

        received = []

        class _ExchangeDouble:
            def order(self, coin, is_buy, size, price, order_type,
                      reduce_only=False, cloid=None, builder=None):
                received.append({
                    "coin": coin, "cloid": cloid, "size": size,
                    "limit_px": price, "order_type": order_type,
                    "reduce_only": reduce_only,
                })
                # Bentuk respons sukses yang BURSA kirim (lihat
                # parse_order_response). Tidak ada order yang dikirim:
                # objek ini tidak punya jaringan sama sekali.
                return {"status": "ok", "oid": 61756985785,
                        "filled": {"totalSz": 0.0, "avgPx": "0.0"}}

        ex.exchange = _ExchangeDouble()
        return ex, received

    def test_cloid_reaching_the_sdk_is_a_cloid_object(self):
        """
        Nilai yang sampai ke `exchange.order()` harus `Cloid`.

        Kalau string, SDK gagal di `order_request_to_order_wire` dan order
        tidak pernah dikirim -- persis reproduction DEFECT-6.
        """
        ex, received = self._exchange()
        ex.place_limit_order(
            "BTC", True, 0.001, 85000.0, cloid="0x" + "ef" * 16)

        self.assertEqual(len(received), 1,
                         "order harus diteruskan ke SDK")
        self.assertIsInstance(
            received[0]["cloid"], Cloid,
            "SDK menerima %r (%s), bukan Cloid -- order akan gagal di wire "
            "encoding" % (received[0]["cloid"],
                          type(received[0]["cloid"]).__name__),
        )

    def test_order_without_cloid_is_still_allowed(self):
        """
        Order closing `reduce_only` dan `emergency_flat` tidak memakai
        cloid. Jalur itu harus tetap jalan.
        """
        ex, received = self._exchange()
        out = ex.place_limit_order(
            "BTC", False, 0.001, 85000.0, reduce_only=True, cloid=None)

        self.assertTrue(out.ok, "order tanpa cloid harus tetap bisa dikirim")
        self.assertIsNone(received[0]["cloid"])

    def test_invalid_cloid_is_reported_as_a_failure_not_an_exception(self):
        """
        Bentuk cloid yang salah harus jadi `OrderOutcome(ok=False)` dengan
        pesan yang menyebut cloid.

        Kalau exceptionlemenular keluar dari `place_limit_order`, pemanggil
        (`engine.submit_order`) menangkapnya dan mencatat `record_error()`.
        Empat penolakan seperti itu menyalakan kill switch pada sistem yang
        sebenarnya benar.
        """
        ex, received = self._exchange()
        out = ex.place_limit_order(
            "BTC", True, 0.001, 85000.0, cloid="tb-b50f716c29034fde")

        self.assertFalse(out.ok, "cloid salah harus ditolak")
        self.assertEqual(received, [],
                         "order dengan cloid salah tidak boleh dikirim")
        self.assertIn(
            "cloid", str(out.error).lower(),
            "pesan harus menyebut cloid; dapat: %r" % out.error,
        )


if __name__ == "__main__":
    unittest.main()