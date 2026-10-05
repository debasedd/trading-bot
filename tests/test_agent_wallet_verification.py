"""
tests/test_agent_wallet_verification.py — Signer harus agent wallet resmi.

MASALAH YANG INI TUTUP
----------------------
Pemisahan wallet (`HYPERLIQUID_ACCOUNT_ADDRESS`) sudah dipakai: bot men-sign
dengan satu alamat dan men-query akun di alamat lain. `preflight()` hanya
membandingkan bentuk alamat dan opt-in `allow_api_wallet`.

Yang BELUM diperiksa: apakah signer itu benar-benar agent wallet yang
DAFTAR di master account tersebut. Kalau tidak, dua kemungkinan yang
sama-sama buruk:

1. Alamat `account_address` salah ketik (`...ab` vs `...ba`). Bot membaca
   akun yang tidak memegang posisi apa pun dan menyimpulkan datar.
2. `account_address` benar, tapi signer-nya wallet asing yang tidak
   diberi wewenang. Order yang ditandatangani tidak pernah sampai ke
   akun itu — hilang tanpa jejak, dan itu baru ketahuan saat order hilang.

Keduanya menghasilkan HAL YANG SAMA secara observabel: bot melihat akun
yang benar-benar tidak ada isinya.

ENDPOINT YANG DIGUNAKAN
----------------------
`extraAgents` — `POST /info {"type": "extraAgents", "user": <master>}`.

Bentuk respons diambil dari SDK resmi
(`hyperliquid.info.Info.extra_agents`):

    [{"name": str, "address": str, "validUntil": int}, ...]

Dokumentasi resmi menyatakan API wallet (a.k.a. agent wallet) di-setujui
master account untuk men-sign atas namanya, dan bahwa query data akun
WAJIB memakai alamat master/sub-account — memakai alamat agent wallet
"leads to an empty result".

KESETRAAN ALAMAT
----------------
Perbandingan alamat harus tahan huruf besar/kecil. Alamat di JSON bursa
bisa berbeda kapitalisasi dari yang operator ketik di environment, dan
tidak ada alasan cryptoWarehouse untuk memperlakukannya sebagai akun
berbeda.
"""

import unittest
from unittest.mock import patch

from trading.live.client import (
    LiveExchange,
    PreflightError,
    mask_address,
)

MASTER = "0x5972698398d8c5bbe67c0db74906236691020417"
# WAJIB mengandung huruf: `SIGNER.upper()` harus benar-benar berbeda dari
# `SIGNER`, kalau tidak test case-sensitivity-nya kosong. Versi lama
# memakai `0x` + "11"*20 — semua heksadesimal, jadi `.upper()` tidak
# mengubah apa pun dan test-nya hijau tanpa menguji apa pun. Mutasi
# "perbandingan jadi case-sensitive" selamat karena itu.
SIGNER = "0xab" + "11" * 19
STRANGER = "0xcd" + "22" * 19


class _InfoDouble:
    """Info tiruan di batas SDK. Hanya `extra_agents` yang dipakai."""

    def __init__(self, agents):
        self._agents = agents

    def meta(self):
        return {"universe": [{"name": "BTC", "szDecimals": 5,
                              "maxLeverage": 25}]}

    def extra_agents(self, user):
        # Master yang benar harus selalu tercatat. Kalau tidak, respons
        # kosong adalah jawaban yang BENAR dan test harus membuktikannya.
        if str(user).lower() != MASTER.lower():
            return []
        return self._agents


class _ExchangeDouble(LiveExchange):
    def __init__(self, agents, account_address=None, address=None,
                 master=MASTER):
        self._info = _InfoDouble(agents)
        self._exchange = None
        self._base_url = "https://api.hyperliquid-testnet.xyz/info"
        self._account_address = account_address
        self._timeout = 15.0
        self._rules = None
        self.testnet = True
        self.base_url = self._base_url
        self.wallet = None
        self.address = address or MASTER
        self.query_address = (account_address or self.address).lower()
        self.master_address = master


class TestSignerMustBeRegisteredAgent(unittest.TestCase):
    """
    Opt-in `allow_api_wallet` BUKAN bukti. Yang membuktikan adalah
    signer terdaftar sebagai agent di master account.
    """

    def test_registered_agent_passes(self):
        ex = _ExchangeDouble(
            [{"name": "bot", "address": SIGNER, "validUntil": 0}],
            account_address=MASTER, address=SIGNER)
        ex.verify_agent_wallet()

    def test_unregistered_signer_rejected(self):
        """
        Signer yang tidak terdaftar harus DITOLAK, bukan diasumsikan sah.

        Opt-in operator hanya menyatakan niat. Dia tidak membuktikan
        bahwa key-nya benar, dan key typo menghasilkan wallet yang tidak
        pernah men-sign ke bursa sungguhan.
        """
        ex = _ExchangeDouble(
            [{"name": "bot", "address": SIGNER, "validUntil": 0}],
            account_address=MASTER, address=STRANGER)
        with self.assertRaises(PreflightError) as ctx:
            ex.verify_agent_wallet()
        self.assertEqual(ctx.exception.code,
                         PreflightError.AGENT_NOT_REGISTERED)

    def test_empty_agent_list_rejected(self):
        """
        Daftar kosong = master tidak punya agent sama sekali.

        Ini yang terjadi kalau `HYPERLIQUID_ACCOUNT_ADDRESS` diisi dengan
        alamat yang bukan master: bursa membalas `[]`, bukan error.
        """
        ex = _ExchangeDouble([], account_address=MASTER, address=SIGNER)
        with self.assertRaises(PreflightError) as ctx:
            ex.verify_agent_wallet()
        self.assertEqual(ctx.exception.code,
                         PreflightError.AGENT_NOT_REGISTERED)

    def test_master_itself_needs_no_agent_check(self):
        """
        Kalau signer == master, tidak ada agent yang perlu dicek.

        Bot yang men-sign dengan master account sendiri adalah konfigurasi
        paling sederhana; memaksa operator mendaftarkan agent demi itu
        hanya menambah langkah tanpa menambah keamanan.
        """
        ex = _ExchangeDouble([], account_address=MASTER, address=MASTER)
        ex.verify_agent_wallet()

    def test_case_difference_still_matches(self):
        """Alamat huruf besar dari environment harus tetap dikenali."""
        ex = _ExchangeDouble(
            [{"name": "bot", "address": SIGNER.upper().replace("0X", "0x"),
              "validUntil": 0}],
            account_address=MASTER, address=SIGNER)
        ex.verify_agent_wallet()

    def test_signer_not_querying_master_is_a_config_error(self):
        """
        Agent dicek terhadap MASTER, bukan terhadap `query_address`.

        Kalau agent dicek terhadap alamat yang sedang di-query, setiap
        kombinasi akan lolos: master tidak punya agent yang namanya sama
        dengan master. Test ini membuktikannya lewat master yang salah.
        """
        wrong_master = "0x" + "99" * 20
        ex = _ExchangeDouble(
            [{"name": "bot", "address": SIGNER, "validUntil": 0}],
            account_address=wrong_master, address=SIGNER,
            master=wrong_master)
        with self.assertRaises(PreflightError):
            ex.verify_agent_wallet()


class TestAgentCheckIsReadOnly(unittest.TestCase):
    """Pemeriksaan agent tidak boleh mengirim apa pun."""

    def test_fixture_addresses_actually_exercise_case_folding(self):
        """
        Kontrol untuk test case-sensitivity-nya sendiri.

        `SIGNER` versi lama adalah `0x` + "11"*20 — semua heksadesimal, jadi
        `.upper()` menghasilkan string yang IDENTIK. Test case-insensitivity
        yang memakainya hijau tanpa menguji apa pun, dan mutasi
        "perbandingan jadi case-sensitive" selamat karena itu.

        Test ini gagal begitu ada konstanta yang kembali ke bentuk itu.
        """
        self.assertNotEqual(
            SIGNER, SIGNER.upper(),
            "SIGNER tidak punya huruf — test case-insensitivity jadi kosong",
        )
        self.assertEqual(len(SIGNER), 42, "bukan alamat Ethereum")
        self.assertEqual(len(STRANGER), 42, "bukan alamat Ethereum")

    def test_only_calls_extra_agents(self):
        touched = []

        class _Watchful(_InfoDouble):
            def extra_agents(self, user):
                touched.append("extra_agents")
                return self._agents

            def __getattr__(self, name):
                def spy(*a, **kw):
                    touched.append(name)
                    raise AssertionError(
                        "verifikasi agent memanggil %s — di luar operasi "
                        "baca yang diizinkan" % name)
                return spy

        ex = _ExchangeDouble(
            [{"name": "bot", "address": SIGNER, "validUntil": 0}],
            account_address=MASTER, address=SIGNER)
        ex._info = _Watchful(
            [{"name": "bot", "address": SIGNER, "validUntil": 0}])
        ex.verify_agent_wallet()

        self.assertEqual(
            touched, ["extra_agents"],
            "verifikasi agent hanya boleh memakai extra_agents(); "
            "panggilan lain: %r" % (touched,),
        )

    def test_error_message_is_masked(self):
        """Pesan error tidak boleh membocorkan alamat penuh."""
        ex = _ExchangeDouble([], account_address=MASTER, address=SIGNER)
        with self.assertRaises(PreflightError) as ctx:
            ex.verify_agent_wallet()
        blob = str(ctx.exception)
        self.assertNotIn(SIGNER, blob, "alamat signer penuh di pesan error")
        self.assertNotIn(MASTER, blob, "alamat master penuh di pesan error")
        self.assertIn(mask_address(SIGNER), blob,
                      "pesan harus tetap menyebut alamat yang disamarkan")


class TestAgentExpiry(unittest.TestCase):
    """
    `extraAgents` mengembalikan `validUntil` per agent.

    Agent kedaluwarsa tidak bisa men-sign. Order yang ditandatanganinya
    hilang tanpa jejak — pola yang sama dengan agent yang tidak
    terdaftar, tapi muncul JAUH setelah start.
    """

    def _now(self):
        import time
        return time.time()

    def _agent(self, valid_until):
        return {"name": "bot", "address": SIGNER, "validUntil": valid_until}

    def test_expired_agent_rejected(self):
        ex = _ExchangeDouble([self._agent(1000)],  # 1000 detik epoch = 1970
                              account_address=MASTER, address=SIGNER)
        with self.assertRaises(PreflightError) as ctx:
            ex.verify_agent_wallet()
        self.assertEqual(ctx.exception.code, PreflightError.AGENT_EXPIRED)

    def test_expiring_within_margin_rejected(self):
        """
        Agent yang valid 10 menit lagi harus DITOLAK.

        Order yang sedang dikirim bisa melewati batas itu. Margin
        `AGENT_EXPIRY_MARGIN_SECONDS` ada persis untuk ini: kedaluwarsa
        dalam 5 menit bukan "masih aman".
        """
        ex = _ExchangeDouble([self._agent(self._now() + 600)],
                              account_address=MASTER, address=SIGNER)
        with self.assertRaises(PreflightError) as ctx:
            ex.verify_agent_wallet()
        self.assertEqual(ctx.exception.code, PreflightError.AGENT_EXPIRED)

    def test_valid_far_in_future_passes(self):
        ex = _ExchangeDouble([self._agent(self._now() + 30 * 86400)],
                              account_address=MASTER, address=SIGNER)
        ex.verify_agent_wallet()

    def test_milliseconds_are_normalised(self):
        """
        Satuan milidetik harus dibaca sebagai milidetik.

        Kalau salah baca, `1700000000000` (ms) dianggap 1700000000000
        detik = tahun 55927, sehingga agent yang sudah kedaluwarsa terbaca
        aman. Tidak ada yang mengetahuinya karena pemeriksaan selalu
        "lulus".
        """
        now_ms = int(self._now() * 1000)
        ex = _ExchangeDouble([self._agent(now_ms - 60_000)],  # 60 detik lalu
                              account_address=MASTER, address=SIGNER)
        with self.assertRaises(PreflightError) as ctx:
            ex.verify_agent_wallet()
        self.assertEqual(ctx.exception.code, PreflightError.AGENT_EXPIRED)

    def test_missing_validuntil_is_not_treated_as_expired(self):
        """
        `validUntil` yang tidak ada = TIDAK bisa dinilai, bukan kedaluwarsa.

        Menolak agent yang jelas masih beres hanya karena bursa tidak
        mengirim field-nya adalah penolakan palsu — dan itu lebih buruk
        daripada memeriksa kedaluwarsa yang dilewati.
        """
        ex = _ExchangeDouble(
            [{"name": "bot", "address": SIGNER}],
            account_address=MASTER, address=SIGNER)
        ex.verify_agent_wallet()

    def test_unparseable_validuntil_is_not_treated_as_expired(self):
        ex = _ExchangeDouble(
            [{"name": "bot", "address": SIGNER, "validUntil": "bukan-angka"}],
            account_address=MASTER, address=SIGNER)
        ex.verify_agent_wallet()

    def test_zero_validuntil_is_not_treated_as_expired(self):
        ex = _ExchangeDouble([self._agent(0)],
                              account_address=MASTER, address=SIGNER)
        ex.verify_agent_wallet()

    def test_absurdly_far_future_is_not_assumed_valid(self):
        """
        Angka di tahun 55927 = hasil salah satuan, dan itu TIDAK boleh
        dianggap "beres".

        Ini arah kegagalan yang paling berbahaya dari pagar kewajaran:
        angka gila yang dianggap aman membuat pemeriksaan kedaluwarsa
        selalu lulus, dan itu tidak pernah bersuara.
        """
        ex = _ExchangeDouble([self._agent(1_700_000_000_000_000)],
                              account_address=MASTER, address=SIGNER)
        ex.verify_agent_wallet()   # tidak melempar: dianggap "tidak bisa nilai"

    def test_far_future_conversion_returns_none(self):
        seconds = LiveExchange._agent_valid_until_seconds(
            1_700_000_000_000_000)
        self.assertIsNone(seconds,
                          "nilai tahun 55927 diterima sebagai validUntil")

    def test_past_value_is_expired_not_unverifiable(self):
        """
        Nilai di masa lalu yang MASUK AKAL = kedaluwarsa sungguhan.

        Pagar kewajaran hanya menolak yang tidak masuk akal; yang masuk
        akal tapi sudah lewat harus tetap jadi `agent_expired`.
        """
        import time
        ex = _ExchangeDouble(
            [{"name": "bot", "address": SIGNER,
              "validUntil": time.time() - 86400}],   # kemarin
            account_address=MASTER, address=SIGNER)
        with self.assertRaises(PreflightError) as ctx:
            ex.verify_agent_wallet()
        self.assertEqual(ctx.exception.code, PreflightError.AGENT_EXPIRED)

    def test_plausible_window_boundaries(self):
        """
        Batas jendela: 30 detik ke depan sah, 2 tahun ke depan tidak.

        Angka 2 tahun ke depan hampir pasti salah satuan, dan menerimanya
        berarti pemeriksaan kedaluwarsa jadi tidak FUNCIONAL.
        """
        import time
        now = time.time()
        self.assertIsNotNone(
            LiveExchange._agent_valid_until_seconds(now + 30),
            "30 detik ke depan ditolak sebagai tidak masuk akal")
        self.assertIsNone(
            LiveExchange._agent_valid_until_seconds(now + 2 * 366 * 86400),
            "2 tahun ke depan diterima — pagar kewajaran tidak bekerja")

    def test_milliseconds_in_past_are_expired(self):
        """
        Satuan ms di masa lalu harus kedaluwarsa SAHIH, bukan "tidak bisa nilai".

        Versi test sebelumnya mengharapkan `None` untuk kasus ini — itu
        menguji KE SALAH pagarnya: menolak nilai masa lalu sebagai "tidak
        bisa menilai" membuat agent yang benar-benar kedaluwarsa LOLOS.
        Pagar kewajaran hanya boleh menolak nilai yang tidak masuk akal
        (terlalu jauh ke depan), bukan yang sudah lewat.
        """
        import time
        now_ms = int(time.time() * 1000)
        seconds = LiveExchange._agent_valid_until_seconds(now_ms - 86400_000)
        self.assertIsNotNone(seconds,
                            "nilai ms kemarin jadi 'tidak bisa nilai'")
        self.assertLess(seconds, time.time(),
                        "nilai ms kemarin ditafsirkan sebagai masa depan")

        # Dan efeknya: `verify_agent_wallet` harus MENOLAKNYA.
        ex = _ExchangeDouble(
            [{"name": "bot", "address": SIGNER,
              "validUntil": now_ms - 86400_000}],
            account_address=MASTER, address=SIGNER)
        with self.assertRaises(PreflightError) as ctx:
            ex.verify_agent_wallet()
        self.assertEqual(ctx.exception.code, PreflightError.AGENT_EXPIRED)


class TestPeriodicReverification(unittest.TestCase):
    """
    Verifikasi agent harus BERJALAN, bukan sekali saat start.

    Agent bisa dicabut dari master kapan saja. Verifikasi satu kali hanya
    membuktikan keadaan saat itu.
    """

    def _exchange(self):
        import time
        return _ExchangeDouble(
            [{"name": "bot", "address": SIGNER,
              "validUntil": time.time() + 30 * 86400}],
            account_address=MASTER, address=SIGNER)

    def test_first_check_is_always_due(self):
        ex = self._exchange()
        self.assertTrue(ex.agent_verification_due())

    def test_not_due_immediately_after_success(self):
        ex = self._exchange()
        ex.reverify_agent_if_due()
        self.assertFalse(ex.agent_verification_due(),
                         "verifikasi kedua langsung due")
        self.assertFalse(ex.reverify_agent_if_due(),
                         "reverify_agent_if_due() jalan padahal belum due")

    def test_due_again_after_interval(self):
        import time
        ex = self._exchange()
        ex.reverify_agent_if_due()
        later = time.time() + ex.AGENT_REVERIFY_SECONDS + 1
        self.assertTrue(ex.agent_verification_due(now=later))

    def test_reverify_raises_when_agent_deregistered(self):
        """
        Agent yang dicabut di tengah jalan harus terdeteksi.

        Ini yang tidak tertangkap oleh verifikasi sekali saat start: agent
        dicabut setelah bot berjalan sejam, dan tanpa pengecekan ulang
        order berikutnya hilang tanpa jejak.
        """
        import time
        ex = _ExchangeDouble(
            [{"name": "bot", "address": SIGNER,
              "validUntil": time.time() + 30 * 86400}],
            account_address=MASTER, address=SIGNER)
        ex.reverify_agent_if_due()  # verifikasi pertama, agent masih ada

        # Agent dicabut dari master.
        ex._info._agents = []

        # Paksa waktu sudah lewat interval, supaya verifikasi due.
        ex._agent_verified_at = time.time() - ex.AGENT_REVERIFY_SECONDS - 1

        with self.assertRaises(PreflightError) as ctx:
            ex.reverify_agent_if_due()
        self.assertEqual(ctx.exception.code,
                         PreflightError.AGENT_NOT_REGISTERED)

    def test_verification_interval_is_configurable_value(self):
        """Interval dan margin adalah konstanta yang bisa dibaca."""
        ex = self._exchange()
        self.assertGreater(LiveExchange.AGENT_REVERIFY_SECONDS, 0)
        self.assertGreater(LiveExchange.AGENT_EXPIRY_MARGIN_SECONDS, 0)


class TestPreflightCoversAgentMismatch(unittest.TestCase):
    """`preflight()` harus menjalankan cek agent, bukan hanya bentuk alamat."""

    def test_preflight_rejects_unregistered_agent(self):
        ex = _ExchangeDouble(
            [{"name": "bot", "address": SIGNER, "validUntil": 0}],
            account_address=MASTER, address=STRANGER)
        with self.assertRaises(PreflightError) as ctx:
            ex.preflight(allow_api_wallet=True)
        self.assertEqual(ctx.exception.code,
                         PreflightError.AGENT_NOT_REGISTERED)


if __name__ == "__main__":
    unittest.main()