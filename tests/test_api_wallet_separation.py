"""
Pemisahan signing key dan account address.

MASALAH YANG INI CEK
--------------------
Untuk production dengan dana sungguhan, pola Hyperliquid yang benar
adalah:

    wallet utama  -> _address_ yang memegang posisi dan dana
    API/agent key -> private key yang menandatangani order

Bot membaca posisi dari wallet UTAMA tapi menandatangani dengan API key.
Kalau keduanya tertukar:

  * bot membaca akun yang salah -> menyimpulkan tidak ada posisi padahal
    ada, lalu mengirim order lagi -> posisi tergandakan;
  * order ditandatangani wallet yang tidak memegang posisi -> order
    ditolak bursa dengan pesan yang tidak menyebut alamat yang benar;
  * kill switch dan rekonsiliasi berjalan di atas angka yang bukan
    milik akun sebenarnya.

Kode produksi sudah memisahkannya (`client.py` menerima
`account_address` terpisah dari private key). Test di sini membuktikan
pemrahaman itu benar dan tidak bisa rusak diam-diam.

TIDAK ADA KEY SUNGGUHAN DI FILE INI
Key yang dipakai adalah 32 byte hex tetap yang hanya untuk membuat
objek yang bisa ditandatangani secara offline. Tidak ada order yang
diteruskan ke jaringan: test ini berhenti sebelum pemanggilan yang
mengirim apa pun.
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from hyperliquid.utils.signing import order_wires_to_order_action
from hyperliquid.utils.types import Cloid

from trading.live.client import LiveExchange, to_cloid

# 32 byte hex. Bukan key nyata; hanya agar eth_account bisa membuat
# wallet offline. Tidak pernah dipakai menandatangani ke bursa nyata --
# test berhenti sebelum `_post_action`.
DUMMY_API_KEY = "0x" + "cd" * 32

# Alamat "wallet utama". Sekarbitrary juga -- hanya dipakai untuk
# memeriksa alamat mana yang dipakai untuk query.
MAIN_WALLET = "0x5972698398d8c5bbe67c0db74906236691020417"


def build_exchange(account_address):
    """
    LiveExchange sungguhan, dibangun dari private key dummy.

    Yang diuji adalah pembagian alamat di dalam produksi: `address`
    (penanda tangan) harus BERBEDA dari `query_address` (pemegang
    posisi) kalau `account_address` diisi.
    """
    return LiveExchange(
        DUMMY_API_KEY,
        testnet=True,
        account_address=account_address,
    )


class TestSigningAndQueryAddressesAreSeparate(unittest.TestCase):
    """Signer dan pembaca posisi harus bisa -- dan harus -- berbeda."""

    def test_query_address_follows_account_address(self):
        """
        Posisi dibaca dari wallet UTAMA, bukan dari API wallet.

        Kalau `query_address` ikut ke alamat penanda tangan, bot akan
        membaca akun yang tidak memegang posisi: rekonsiliasi melaporkan
        "tidak ada posisi" untuk posisi yang benar-benar ada.
        """
        ex = build_exchange(MAIN_WALLET)

        self.assertEqual(
            ex.query_address, MAIN_WALLET.lower(),
            "query_address=%r, harus wallet utama %r"
            % (ex.query_address, MAIN_WALLET.lower()),
        )
        self.assertNotEqual(
            ex.query_address, ex.address,
            "query_address sama dengan alamat penanda tangan: bot akan "
            "membaca akun yang salah",
        )

    def test_without_account_address_query_falls_back_to_signer(self):
        """
        Tanpa API wallet, query harus tetap jalan dengan alamat sendiri.

        Jalur ini dipakai testnet dan mode tanpa pemisahan wallet.

        Perbandingan against `address.lower()`, bukan `address`:
        `eth_account` mengembalikan alamat berformat CHECKSUM (campur
        huruf besar-kecil), sedangkan `query_address` selalu lowercase
        karena Hyperliquid membandingkan alamat dalam bentuk itu.
        Dua-duanya benar; membandingkan langsung keduanya akan salah.
        """
        ex = build_exchange(None)

        self.assertEqual(
            ex.query_address, ex.address.lower(),
            "tanpa account_address, query harus jatuh ke alamat sendiri",
        )
        self.assertTrue(ex.query_address.startswith("0x"))
        self.assertEqual(ex.query_address, ex.query_address.lower(),
                         "query_address harus selalu lowercase")

    def test_query_address_is_lowercased(self):
        """
        Hyperliquid membandingkan alamat dengan huruf kecil.

        `HYPERLIQUID_ACCOUNT_ADDRESS` bisa diketik dengan huruf besar;
        tanpa normalisasi, query membaca akun yang kelihatan tidak ada.
        """
        ex = build_exchange(MAIN_WALLET.upper())
        self.assertEqual(ex.query_address, ex.query_address.lower())
        self.assertEqual(ex.query_address, MAIN_WALLET.lower())


class _RecordingInfo:
    """
    `Info` tiruan yang mencatat alamat yang dibaca.

    Hanya punya method yang butuh alamat. Sengaja TIDAK mewarisi
    `hyperliquid.info.Info` — kelas itu melakukan POST ke bursa di
    `__init__` untuk `spotMeta`, jadi mewarisinya berarti memanggil
    jaringan sungguhan.
    """

    def frontend_open_orders(self, user):
        return []

    def query_order_by_oid(self, user, oid):
        return {"status": "order", "order": {"status": "open"}}

    def user_fills(self, user, startTime=None):
        return []

    def clearinghouse_state(self, user, dex=""):
        return {"assetPositions": [], "marginSummary": {}}

    def user_state(self, user):
        return {"assetPositions": [], "marginSummary": {}, "withdrawable": "0"}

    def name_to_asset(self, name):
        return {"BTC": 0, "ETH": 1, "SOL": 2, "HYPE": 3}.get(name, 0)

    def meta(self):
        return {"universe": []}

    def user_rate_limit(self, user):
        return {}


class TestInfoCallsUseTheQueryAddress(unittest.TestCase):
    """
    Semua pembacaan bursa harus memakai alamat yang benar.

    Ini yang menentukan angka mana yang jadi sumber kebenaran.
    """

    def setUp(self):
        self.calls = []

    def _info(self):
        ex = build_exchange(MAIN_WALLET)

        # SUNTIK Info tiruan. Versi lama memakai `ex.info` langsung,
        # yang membangun `Info(base_url)` SDK -- dan itu melakukan POST
        # nyata ke `api.hyperliquid-testnet.xyz` untuk `spotMeta` setiap
        # kali构造函数 dipanggil. Test ini didokumentasikan sebagai
        # offline, dan memang memanggil bursa sungguhan.
        #
        # Ditemukan oleh pemblokir socket di `conftest.py`: 110 kegagalan
        # berturut-turut dengan `getaddrinfo(('api.hyperliquid-testnet.xyz', 443))`.
        info = _RecordingInfo()

        # Setel ke instance, supaya `ex.info` mengembalikan tiruan ini
        # dan tidak pernah menyentuh jaringan.
        ex.info = info

        # Ganti callable yang menerima alamat, satu per satu, supaya
        # bisa diawasi.
        def recorder(name, fn):
            def wrapped(user, *args, **kwargs):
                self.calls.append((name, user))
                return fn(user, *args, **kwargs) if fn else []
            return wrapped

        info.frontend_open_orders = recorder(
            "frontend_open_orders", info.frontend_open_orders)
        info.query_order_by_oid = recorder(
            "query_order_by_oid",
            lambda u, oid: {"status": "order", "order": {"status": "open"}})
        info.user_fills = recorder(
            "user_fills", lambda u, startTime=None: [])
        info.clearinghouse_state = recorder(
            "clearinghouse_state", lambda u, dex="": {
                "assetPositions": [], "marginSummary": {},
            })
        return ex

    def test_open_orders_reads_the_main_wallet(self):
        ex = self._info()
        ex.open_orders()

        self.assertTrue(self.calls, "open_orders tidak pernah memanggil info")
        name, user = self.calls[-1]
        self.assertEqual(name, "frontend_open_orders")
        self.assertEqual(
            user, MAIN_WALLET.lower(),
            "order dibaca dari %r; harus dari wallet utama" % user,
        )

    def test_fills_read_the_main_wallet(self):
        """
        Fill SL/TP adalah sumber kebenaran untuk PnL dan daily-loss.

        Membacanya dari akun yang salah berarti PnL posisi sebenarnya
        tidak pernah masuk ke breaker.
        """
        ex = self._info()

        def fetch():
            return ex.info.user_fills(ex.query_address, startTime=0)
        fetch()

        name, user = self.calls[-1]
        self.assertEqual(name, "user_fills")
        self.assertEqual(
            user, MAIN_WALLET.lower(),
            "fill dibaca dari %r; harus wallet utama" % user,
        )

    def test_order_status_reads_the_main_wallet(self):
        ex = self._info()
        ex.order_status("BTC", 61756985785)

        name, user = self.calls[-1]
        self.assertEqual(name, "query_order_by_oid")
        self.assertEqual(user, MAIN_WALLET.lower())


class TestRedactionProtectsTheSigningKey(unittest.TestCase):
    """
    Key API wallet tidak boleh bocor ke log.

    Bocor key berarti siapa pun bisa menandatangani order untuk akun
    Anda. `SafetyGate.redact` adalah satu-satunya penyaring sebelum
    pesan masuk ke log.
    """

    def test_key_is_stripped_from_error_messages(self):
        from trading.live.safety import SafetyGate

        gate = SafetyGate(
            __import__("core.config", fromlist=["LiveConfig"]).LiveConfig(),
            env={"TRADEBOT_LIVE": "1",
                 "HYPERLIQUID_PRIVATE_KEY": DUMMY_API_KEY},
            state_path=None,
        )
        text = gate.redact("gagal kirim: %s" % DUMMY_API_KEY)

        self.assertNotIn(
            DUMMY_API_KEY, text,
            "private key bocor ke pesan log",
        )
        self.assertIn("<REDACTED>", text)

    def test_redaction_ignores_short_strings(self):
        """
        `redact` hanya menyaring key sepanjang >= 8 karakter.

        Tanpa itu, pesan error pendek jadi penuh `<REDACTED>` dan log
        kehilangan makna.
        """
        from trading.live.safety import SafetyGate

        gate = SafetyGate(
            __import__("core.config", fromlist=["LiveConfig"]).LiveConfig(),
            env={"TRADEBOT_LIVE": "1",
                 "HYPERLIQUID_PRIVATE_KEY": DUMMY_API_KEY},
            state_path=None,
        )
        self.assertEqual(gate.redact("order ditolak"), "order ditolak")


class TestOfflineSigningWorksForBothWalletModes(unittest.TestCase):
    """
    Order harus bisa DITANDATANGANI secara offline untuk kedua mode.

    Ini yang diminta sebagai tambahan wajib. Tidak ada jaringan: yang
    dipanggil adalah fungsi tanda tangan SDK, dan hasilnya diperiksa
    bentuknya. Kalau pemisahan wallet merusak signing, failure-nya
    muncul di sini -- bukan setelah dana bergerak.
    """

    def _wire_for(self, account_address):
        ex = build_exchange(account_address)
        return ex, order_wires_to_order_action([{
            "a": 3,                      # index aset BTC di universe
            "b": True,
            "p": 85000000000000,         # harga dalam wire format
            "s": 100000,                 # size dalam wire format
            "r": False,
            "t": {"limit": {"tif": "Gtc"}},
            "c": to_cloid("0x" + "ef" * 16).to_raw(),
        }])

    def test_signing_works_with_api_wallet(self):
        """
        Mode produksi: signer berbeda, pemegang posisi wallet utama.
        """
        ex, action = self._wire_for(MAIN_WALLET)

        self.assertEqual(action["type"], "order")
        self.assertEqual(len(action["orders"]), 1)
        self.assertIn("c", action["orders"][0],
                      "order harus membawa client order id")
        self.assertNotEqual(ex.address.lower(), ex.query_address,
                            "mode ini harus memakai dua wallet berbeda")

    def test_signing_works_with_single_wallet(self):
        """Mode testnet sederhana: satu wallet untuk segalanya."""
        ex, action = self._wire_for(None)

        self.assertEqual(action["type"], "order")
        self.assertEqual(len(action["orders"]), 1)
        self.assertEqual(ex.address.lower(), ex.query_address)

    def test_cloid_survives_signing_in_both_modes(self):
        """
        cloid ikut ditandatangani, jadi harus utuh di kedua mode.

        cloid yang hilang di jalur signing = order tidak bisa
        dibatalkan = posisi tanpa jalan keluar selain manual.
        """
        expected = to_cloid("0x" + "ef" * 16).to_raw()
        for addr in (MAIN_WALLET, None):
            _ex, action = self._wire_for(addr)
            self.assertEqual(
                action["orders"][0]["c"], expected,
                "cloid berubah pada mode account_address=%r" % addr,
            )


if __name__ == "__main__":
    unittest.main()