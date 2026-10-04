"""
tests/test_live_preflight.py — Bot tidak boleh start tanpa bukti bursa hidup.

MASALAH YANG INI TUTUP
----------------------
Sebelum `c224b58`, `LiveExchange.__init__` membangun `Info(base_url)`, dan
`Info.__init__` SDK melakukan POST ke bursa. Itu berarti
`LiveExchange(...)` GAGAL kalau bursa tidak terjangkau — kebetulan, bukan
desain.

Sekarang `Info` dibangun LAZY. Bufur detonation yang sama tidak ada lagi:
`LiveExchange(...)` berhasil di mesin yang tidak punya jaringan sama
sekali, dan kelihatannya sehat sampai order pertama dikirim.

Akibatnya ada dua jenis kegagalan yang berbeda bentuknya dan keduanya
berbahaya:

  * **Jaringan mati.** Order pertama ditolak bursa dengan timeout, atau
    — lebih buruk — terkirim tanpa knowing-the-state. Bot tidak tahu
    posisi sebenarnya.
  * **Kredensial salah.** `Account.from_key` menerima hampir semua hex 32
    byte dan menghasilkan wallet yang tidak pernah Signing ke bursa
    sungguhan. Key typo (`0x...ab` vs `0x...ba`) tidak pernah gagal
    sampai order pertama, dan order itu hilang.

Keduanya harus GAGAL DI START, bukan saat order pertama.

YANG DIUJI

  1. Bursa tidak terjangkau -> `exchange_unreachable`.
  2. `universe` kosong -> `universe_empty`.
  3. Alamat salah bentuk -> `bad_address`.
  4. Signer beda dengan query address -> `signer_mismatch`.
  5. Preflight HANYA membaca: tidak pernah mengirim order.
  6. Log tidak pernah memuat alamat penuh.
  7. `allow_api_wallet=True` tidak mematikan validasi bentuk.

Test perilaku boot ada di `tests/test_live_startup_gate.py`, bukan di
sini: file ini menguji `preflight()` sebagai unit, file itu menguji bahwa
`_build_live_executor()` benar-benar menjalankannya dan benar-benar berhenti.

DEFECT YANG DIPERBAIKI DI SINI (satu commit)
--------------------------------------------
Validasi bentuk alamat pernah ada dalam bentuk `if False:` — komentarnya
`# MUTAN: validasi bentuk alamat dimatikan` — jadi `_is_address()` tidak
dipanggil sama sekali di mana pun.

Test lama (`test_account_address_must_be_hex_address`) tetap HIJAU saat
mutan itu menyala, karena ia bercabang ke `signer_mismatch` lebih dulu dan
hanya meng-`assertIn("account_address", str(exc))` pada pesannya. Dua
cabang berbagi satu kata, jadi masing-masing menyelamatkan yang lain:
test hijau karena alasan yang salah, pola yang sama seperti tiga jebakan
di `fase-1-partial.md` §5.2.

Konsekuensinya di luar test: `preflight()` meloloskan
`account_address="bukan-alamat"` dan mencatat "Preflight OK". Bursa
tidak akan menolak string itu — dia membalas akun kosong — jadi bot akan
simpulkan tidak ada posisi padahal posisi ada.

Yang dipakai sekarang: `PreflightError.code`. Tiap cabang punya kode
khusus, dan tiap test mengunci KODE-nya. Tidak ada `assertIn` pada teks
pesan di mana pun di file ini.
"""
import unittest

from trading.live.client import LiveExchange, PreflightError

# Alamat publik dari `tests/hyperliquid_fixtures.py` — bukan milik repo ini.
MAIN_WALLET = "0x5972698398d8c5bbe67c0db74906236691020417"
OTHER_WALLET = "0x" + "11" * 20
# Panjang benar (0x + 40 hex) tapi BUKAN hex pada 4 karakter terakhir.
MALFORMED = "0x" + "cd" * 18 + "zzzz"
# Panjang 41 — satu hex kurang dari 42, jadi bukan alamat. (0x + 40 = 42.)
TRUNCATED = "0x" + "cd" * 19 + "c"
# Panjang 43 — satu hex lebih.
OVERLONG = "0x" + "cd" * 20 + "c"


class _ExchangeDouble(LiveExchange):
    """
    `LiveExchange` tanpa jaringan, dengan `_info` disuntik.

    Dibangun lewat `__new__` supaya `Account.from_key` dan pembagian
    base_url tidak jalan — test ini tidak boleh menyentuh jaringan, dan
    `conftest.py` memblokirnya kalau terlupa.
    """

    def __init__(self, info_double, account_address=None, address=None):
        self._info = info_double
        self._exchange = None
        self._base_url = "https://api.hyperliquid-testnet.xyz/info"
        self._account_address = account_address
        self._timeout = 15.0
        self._rules = None
        self.testnet = True
        self.base_url = self._base_url
        self.wallet = None
        self.address = address or MAIN_WALLET
        self.query_address = (account_address or self.address).lower()


def _ok_info():
    """
    Info tiruan yang sehat: meta ada dan punya universe.

    `extra_agents` ikut disediakan karena `preflight()` memanggil
    `verify_agent_wallet()` setiap kali pemisahan wallet di-opt-in. Tanpa
    itu, test "opt-in lolos" akan gagal karena endpoint tidak ada — bukan
    karena alurnya salah.
    """
    class _Info:
        def meta(self):
            return {"universe": [{"name": "BTC", "szDecimals": 5,
                                  "maxLeverage": 25}]}

        def user_state(self, user):
            return {"assetPositions": [],
                    "marginSummary": {"accountValue": "1000"}}

        def extra_agents(self, user):
            # Signer pada test-test preflight adalah OTHER_WALLET, jadi
            # master harus melaporkannya sebagai agent yang sah.
            return [{"name": "bot", "address": OTHER_WALLET,
                     "validUntil": 0}]

    return _Info()


def _code_of(callable_, *args, **kwargs):
    """
    Jalankan `callable_`, kembalikan `PreflightError.code`, dan gagal keras
    kalau tidak ada exception atau tipenya salah.

    Dipakai supaya setiap test bisa menulis satu baris assert yang jelas
    cabang mana yang harus menyala — dan tidak bisa lolos diam-diam kalau
    preflight ternyata tidak melempar apa pun.
    """
    try:
        callable_(*args, **kwargs)
    except PreflightError as exc:
        return exc.code
    raise AssertionError(
        "preflight() tidak melempar PreflightError; cabang yang diuji "
        "tidak menyala"
    )


# ---------------------------------------------------------------------------
# Cabang 1 — bursa tidak terjangkau
# ---------------------------------------------------------------------------
class TestExchangeUnreachable(unittest.TestCase):
    """
    Bursa tidak terjangkau harus GAGAL, bukan lolos diam.

    Kode `exchange_unreachable` — bukan `NetworkAccessDenied` dari socket
    guard test. Di produksi tidak ada guard, jadi pesannya harus berdiri
    sendiri.
    """

    def test_unreachable_exchange_fails(self):
        class _Dead:
            def meta(self):
                raise ConnectionError("nama atau layanan tidak dikenal")

        ex = _ExchangeDouble(_Dead())
        self.assertEqual(_code_of(ex.preflight),
                         PreflightError.EXCHANGE_UNREACHABLE)

    def test_unreachable_exchange_is_runtime_error(self):
        """Subkelas RuntimeError supaya `run.py` bisa menampilkan pesannya."""
        class _Dead:
            def meta(self):
                raise ConnectionError("dead")

        with self.assertRaises(RuntimeError):
            _ExchangeDouble(_Dead()).preflight()

    def test_preflight_never_sends_an_order(self):
        """
        Preflight HANYA membaca, dan tidak menyentuh apa pun yang
        menulis.

        Preflight yang diam-diam mengirim order sama bobotnya dengan tidak
        ada preflight sama sekali — makanya `_Watchful` di sini: setiap
        atribut yang bukan operasi baca yang diizinkan dianggap percobaan
        kirim order.

        Daftar yang diizinkan itu eksplisit, bukan "apa pun yang bukan
        meta": `user_state` dan `spot_user_state` memang operasi baca,
        dan preflight membutuhkannya untuk memeriksa apakah akun benar-benar
        ada. Yang tidak boleh muncul: `exchange` (klien order),
        `place_limit_order`, `place_trigger_order`, `cancel_*`, dan apa pun
        lain di luar daftar.
        """
        sent = []
        allowed_reads = {
            "meta",                      # connectivity + universe
            "user_state",                # margin/posisi perps
            "spot_user_state",           # saldo spot
            "extra_agents",              # verifikasi agent wallet
        }

        class _Watchful:
            def meta(self):
                return {"universe": [{"name": "BTC"}]}

            def user_state(self, address, dex=""):
                return {"assetPositions": [],
                        "marginSummary": {"accountValue": "1000",
                                          "withdrawable": "1000"}}

            def spot_user_state(self, address):
                return {"balances": [{"coin": "USDC", "total": "1000",
                                      "hold": "0"}]}

            def extra_agents(self, user):
                return []

            def __getattr__(self, name):
                # `__getattr__` hanya dipanggil untuk atribut yang TIDAK
                # didefinisikan di atas, jadi yang diizinkan aman.
                def spy(*a, **kw):
                    sent.append(name)
                    raise AssertionError(
                        "preflight memanggil %s — di luar operasi baca "
                        "yang diizinkan" % name)
                return spy

        _ExchangeDouble(_Watchful()).preflight()
        self.assertEqual(
            sent, [],
            "preflight hanya boleh memakai operasi baca: %s; yang dipanggil: "
            "%r" % (sorted(allowed_reads), sent),
        )


class TestEmptyUniverse(unittest.TestCase):
    """
    `universe` kosong berarti aturan presisi aset tidak diketahui.

    Cabang sendiri, terpisah dari `exchange_unreachable`: bursa menjawab
    dengan benar. Kalau digabung, meta yang rusak diam-diam lolos dan size
    yang tidak sesuai aturan bursa ditolak tanpa pesan yang menyebut angkanya.
    """

    def test_empty_universe_rejected(self):
        class _EmptyMeta:
            def meta(self):
                return {"universe": []}

        ex = _ExchangeDouble(_EmptyMeta())
        self.assertEqual(_code_of(ex.preflight),
                         PreflightError.UNIVERSE_EMPTY)

    def test_missing_universe_key_rejected(self):
        """Bursa menjawab tanpa key `universe` sama sekali."""

        class _NoKey:
            def meta(self):
                return {}

        ex = _ExchangeDouble(_NoKey())
        self.assertEqual(_code_of(ex.preflight),
                         PreflightError.UNIVERSE_EMPTY)

    def test_null_meta_rejected(self):
        """`meta()` yang mengembalikan None, bukan dict."""

        class _Null:
            def meta(self):
                return None

        ex = _ExchangeDouble(_Null())
        self.assertEqual(_code_of(ex.preflight),
                         PreflightError.UNIVERSE_EMPTY)


# ---------------------------------------------------------------------------
# Cabang 3 — bentuk alamat. INI CABANG YANG PERNAH MATI.
# ---------------------------------------------------------------------------
class TestAddressShape(unittest.TestCase):
    """
    Alamat salah bentuk tidak menghasilkan error dari bursa.

    `user_state("bukan-alamat")` membalas akun kosong tanpa error, jadi bot
    menyimpulkan "tidak ada posisi" padahal posisinya ada di wallet utama.
    Kesalahan ini TIDAK AKAN muncul sendiri di kemudian hari.
    """

    def _with_mismatch_branch_disabled(self, malformed):
        """
        Exchange yang cabang `signer_mismatch`-nya secara BUKTI tidak bisa
        menyala: signer == query_address == nilai yang sama.

        Ini yang membuat test di kelas ini jujur. Kalau signer dan query
        berbeda, cabang 4 menyala duluan dan test jadi hijau karena alasan
        salah — persis cacat di versi test sebelumnya.
        """
        ex = _ExchangeDouble(_ok_info(),
                             account_address=malformed, address=malformed)
        ex.query_address = malformed
        # Jaring pengaman: kalau konfigurasi test ini berubah dan keempat
        # nilai tidak lagi sama, cabang 4 bisa menyala lagi dan test
        # kembali berbohong. Gagal di sini, bukan diam.
        self.assertEqual(ex._account_address, ex.address)
        self.assertEqual(ex.query_address, ex.address)
        return ex

    def test_signer_and_query_both_malformed_still_rejected(self):
        """
        Kasus yang membuktikan guard bentuk alamat benar-benar hidup.

        Signer == query_address == string salah bentuk, jadi cabang
        `signer_mismatch` TIDAK bisa menyala. Inilah test yang merah saat
        `if False:` menyala.
        """
        ex = self._with_mismatch_branch_disabled(MALFORMED)
        self.assertEqual(_code_of(ex.preflight),
                         PreflightError.BAD_ADDRESS)

    def test_non_address_string_rejected(self):
        ex = self._with_mismatch_branch_disabled("bukan-alamat")
        self.assertEqual(_code_of(ex.preflight),
                         PreflightError.BAD_ADDRESS)

    def test_truncated_address_rejected(self):
        """Panjang 41 — satu hex kurang, jadi bukan alamat."""
        ex = self._with_mismatch_branch_disabled(TRUNCATED)
        self.assertEqual(_code_of(ex.preflight),
                         PreflightError.BAD_ADDRESS)

    def test_too_long_address_rejected(self):
        ex = self._with_mismatch_branch_disabled(OVERLONG)
        self.assertEqual(_code_of(ex.preflight),
                         PreflightError.BAD_ADDRESS)

    def test_missing_0x_prefix_rejected(self):
        ex = self._with_mismatch_branch_disabled("cd" * 20)
        self.assertEqual(_code_of(ex.preflight),
                         PreflightError.BAD_ADDRESS)

    def test_empty_account_address_rejected(self):
        """
        String kosong harus ditolak, bukan diperlakukan sebagai "tidak diisi".

        `_ExchangeDouble` memakai `or` untuk fallback, jadi `""` akan
        berubah jadi alamat utama. address dikunci ulang di sini supaya
        yang diuji memang string kosong.
        """
        ex = _ExchangeDouble(_ok_info(), account_address="",
                             address=MAIN_WALLET)
        ex._account_address = ""
        ex.address = ""
        ex.query_address = ""
        self.assertEqual(ex._account_address, ex.address)
        self.assertEqual(ex.query_address, ex.address)
        self.assertEqual(_code_of(ex.preflight),
                         PreflightError.BAD_ADDRESS)

    def test_malformed_account_address_with_valid_signer(self):
        """
        Akun ditanyakan ke alamat salah bentuk sementara signer sah.

        Cabang bentuk alamat diperiksa lebih dulu, jadi kodenya
        `bad_address`, bukan `signer_mismatch`.
        """
        ex = _ExchangeDouble(_ok_info(), account_address=MALFORMED,
                             address=MAIN_WALLET)
        self.assertEqual(_code_of(ex.preflight),
                         PreflightError.BAD_ADDRESS)

    def test_opt_in_does_not_disable_shape_check(self):
        """
        `allow_api_wallet=True` mematikan cabang mismatch — dan TIDAK boleh
        mematikan validasi bentuk. Kalau guard bentuk ikut mati, preflight
        lolos pada alamat garbage persis di mode yang paling dipakai.
        """
        ex = self._with_mismatch_branch_disabled(MALFORMED)
        self.assertEqual(_code_of(ex.preflight, allow_api_wallet=True),
                         PreflightError.BAD_ADDRESS)

    def test_is_address_helper_agrees_with_guard(self):
        """
        `_is_address()` harus benar sendiri, bukan cuma dipanggil.

        Helper yang ada tapi tidak pernah dipakai adalah symptom yang sama
        dengan mutan yang belum dicabut: terlihat benar di review, tidak
        pernah dieksekusi.
        """
        cases = [
            (MAIN_WALLET, True),
            (OTHER_WALLET, True),
            (MALFORMED, False),
            (TRUNCATED, False),
            (OVERLONG, False),
            ("bukan-alamat", False),
            ("", False),
            (None, False),
            (12345, False),
        ]
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertIs(LiveExchange._is_address(value), expected)


class TestSignerVersusQueryAddress(unittest.TestCase):
    """
    Wallet penanda tangan != wallet yang ditanyakan harus dinyatakan.

    Pola ini BENAR untuk API wallet, dan salah untuk salah ketik. Kalau
    tidak dinyatakan, bot berjalan membaca akun yang tidak memegang posisi.
    """

    def test_mismatch_without_opt_in_rejected(self):
        ex = _ExchangeDouble(_ok_info(), account_address=MAIN_WALLET,
                             address=OTHER_WALLET)
        self.assertEqual(_code_of(ex.preflight),
                         PreflightError.SIGNER_MISMATCH)

    def test_mismatch_with_opt_in_passes(self):
        """Kalau operator menyatakannya, preflight harus lolos."""
        ex = _ExchangeDouble(_ok_info(), account_address=MAIN_WALLET,
                             address=OTHER_WALLET)
        ex.preflight(allow_api_wallet=True)

    def test_same_signer_and_query_passes(self):
        _ExchangeDouble(_ok_info()).preflight()

    def test_case_difference_alone_is_not_a_mismatch(self):
        """
        Alamat Ethereum tidak case-sensitive untuk perbandingan.

        Tanpa ini, operator dengan `HYPERLIQUID_ACCOUNT_ADDRESS` dalam huruf
        besar ditolak padahal wallet-nya sama persis.
        """
        upper = MAIN_WALLET.upper().replace("0X", "0x")
        ex = _ExchangeDouble(_ok_info(), account_address=upper,
                             address=MAIN_WALLET)
        ex.preflight()

    def test_no_account_address_passes(self):
        """`account_address=None` kasus valid — fallback ke signer."""
        _ExchangeDouble(_ok_info(), account_address=None).preflight()


# ---------------------------------------------------------------------------
# Log tidak boleh membocorkan alamat penuh
# ---------------------------------------------------------------------------
class TestLogsDoNotLeakFullAddress(unittest.TestCase):
    """Alamat penuh tidak dibutuhkan untuk diagnosis."""

    def test_success_log_is_masked(self):
        ex = _ExchangeDouble(_ok_info())
        with self.assertLogs("trading_bot.live_client", level="INFO") as cap:
            ex.preflight()
        blob = "\n".join(cap.output)
        self.assertNotIn(MAIN_WALLET, blob, "alamat penuh masuk ke log")
        self.assertIn("…", blob, "alamat seharusnya disamarkan")

    def test_failure_messages_are_masked(self):
        ex = _ExchangeDouble(_ok_info(), account_address=MAIN_WALLET,
                             address=OTHER_WALLET)
        try:
            ex.preflight()
        except PreflightError as exc:
            blob = str(exc)
        else:
            self.fail("preflight seharusnya menolak signer yang berbeda")
        self.assertNotIn(MAIN_WALLET, blob)
        self.assertNotIn(OTHER_WALLET, blob)

    def test_malformed_address_message_is_masked(self):
        ex = _ExchangeDouble(_ok_info(), account_address=MALFORMED,
                             address=MALFORMED)
        ex.query_address = MALFORMED
        try:
            ex.preflight()
        except PreflightError as exc:
            blob = str(exc)
        else:
            self.fail("preflight seharusnya menolak alamat salah bentuk")
        self.assertNotIn(MALFORMED, blob)


if __name__ == "__main__":
    unittest.main()
