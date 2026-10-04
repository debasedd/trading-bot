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
SIGNER = "0x" + "11" * 20
STRANGER = "0x" + "22" * 20


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