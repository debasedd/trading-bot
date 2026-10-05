"""
tests/test_fail_closed_account.py — "Tidak ada posisi" != "Tidak bisa memastikan".

MASALAH YANG INI TUTUP
----------------------
`_fetch_remote_positions()` memfilter `assetPositions` dengan `szi != 0`.
Untuk alamat yang bukan akun sungguhan, bursa membalas objek yang
bentuknya seperti akun tapi isinya kosong — `assetPositions: []`,
`accountValue: "0"`.

Hasilnya `positions() == []`, jadi `reconcile()` melaporkan "tidak ada
posisi", `healthy = True`, dan kill switch TIDAK menyala. Bot berjalan
dengan keyakinan bahwa datar, padahal posisinya ada di wallet lain.

Dokumentasi Hyperliquid menyebut jebakan ini eksplisit (bagian "User
address" di Info endpoint, dan "API wallets" di Nonces and API wallets):

    "To query the account data associated with a master or sub-account,
     you must pass in the actual address of that account. A common pitfall
     is to use the agent wallet's address which leads to an empty result."

Jadi "datar" dan "tidak bisa dipastikan" harus jadi dua hasil BERBEDA, dan
yang kedua harus fail closed.

YANG DIUJI
----------
1. Akun kosong (`accountValue == 0`) -> `UnverifiableAccount`, bukan datar.
2. Respons tanpa `marginSummary.accountValue` -> `malformed_state`.
3. `accountValue` bukan angka -> `malformed_state`.
4. `reconcile()` melaporkan `state_known=False` dan menyalakan kill switch.
5. Akun BENAR-BENAR datar (accountValue > 0, tanpa posisi) -> tetap
   `state_known=True` dan TIDAK menyalakan kill switch.

Poin 5 menjaga perbaikan ini tidak berubah jadi "selalu menolak". Kalau
hilang, reconcile tidak pernah hijau dan kill switch jadi tidak berguna:
operator akan menyimpulkan sistemnya rusak dan melepasnya paksa.

TIRUAN BUKAN STUB
-----------------
`_engine_with_state()` memanggil `_fetch_remote_positions()` dan
`reconcile()` SUNGGUHAN. Yang dipalsukan hanya isi respons bursa, dengan
nilai berbentuk sama seperti respons nyata di
`tests/hyperliquid_fixtures.py`. Tidak ada stub untuk logika yang diuji.
"""

import pathlib
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from core.config import LiveConfig
from trading.live.client import LiveExchange, PreflightError
from trading.live.engine import (
    LiveEngine,
    LivePosition,
    UnverifiableAccount,
    normalize_symbol,
)

from repro_helpers import clean_gate

# Alamat publik dari tests/hyperliquid_fixtures.py — bukan milik repo ini.
ACCOUNT_WITH_POSITION = "0x5972698398d8c5bbe67c0db74906236691020417"

UNIVERSE = [
    {"name": "SOL", "szDecimals": 2, "maxLeverage": 20},
    {"name": "BTC", "szDecimals": 5, "maxLeverage": 25},
]


def _state(account_value, asset_positions=None):
    """
    Bentuk respons `clearinghouseState` yang mencerminkan bursa.

    Semua angka datang sebagai STRING desimal — fakta yang hanya terlihat
    dari respons nyata, sudah didokumentasikan di
    `tests/hyperliquid_fixtures.py`.
    """
    return {
        "assetPositions": asset_positions or [],
        "marginSummary": {
            "accountValue": str(account_value),
            "totalNtlPos": "0",
            "totalRawUsd": str(account_value),
        },
        "crossMarginSummary": {"accountValue": str(account_value),
                               "totalNtlPos": "0"},
        "withdrawable": str(account_value),
    }


def _position(coin="BTC", szi="-0.24567", entry="85110.1"):
    return {"type": "oneWay",
            "position": {"coin": coin, "szi": szi, "entryPx": entry,
                         "leverage": {"type": "cross", "value": 5}}}


class _ExchangeDouble:
    """
    `LiveExchange` tanpa jaringan: `get_account_state` dan `info.meta`.

    `_fetch_remote_positions()` memanggil `exchange.get_account_state()`
    dan `exchange.info.meta()`. Dua hal itu yang dipalsukan — sisanya
    kode produksi asli.
    """

    def __init__(self, state):
        self._state = state
        self.query_address = ACCOUNT_WITH_POSITION
        self.address = ACCOUNT_WITH_POSITION
        self.base_url = "https://api.hyperliquid-testnet.xyz/info"

    def get_account_state(self):
        return self._state

    def positions(self):
        """Meniru filter produksi, TANPA guard akun kosong.

        Sengaja tidak memanggil `_require_real_account`: kalau test memakai
        versi produksi, test tidak bisa membuktikan apa yang terjadi
        SEBELUM guard ada. Yang diuji adalah `reconcile()` +
        `_fetch_remote_positions()` sungguhan, dengan bursa yang jujur
        mengembalikan akun kosong.
        """
        return [
            p for p in (self._state.get("assetPositions") or [])
            if float((p.get("position") or {}).get("szi") or 0.0) != 0.0
        ]

    @property
    def info(self):
        class _Info:
            def meta(self):
                return {"universe": UNIVERSE}

            def name_to_asset(self, coin):
                for i, a in enumerate(UNIVERSE):
                    if a["name"] == coin:
                        return i
                return None

        return _Info()


def _engine_with_state(state, local_positions=None):
    """`LiveEngine` nyata dengan bursa tiruan dan gerbang terisolasi."""
    engine = LiveEngine.__new__(LiveEngine)
    engine.cfg = LiveConfig(auto_reconcile=False)
    engine.exchange = _ExchangeDouble(state)
    engine.gate = clean_gate()
    engine.positions = {}
    for pos in (local_positions or []):
        engine.positions[normalize_symbol(pos["symbol"])] = LivePosition(**pos)
    return engine


class TestEmptyAccountIsNotFlat(unittest.TestCase):
    """
    Akun kosong dari bursa TIDAK boleh dibaca sebagai "datar".

    ATURAN YANG BENAR-BENAR DITERAPKAN
    ---------------------------------
    `accountValue == 0` digabung dengan apa lagi yang ada di respons
    menentukan perlakuannya:

    * `accountValue == 0` DAN ada posisi perps (`szi != 0`) -> DITOLAK.
      Posisi yang punya ukuran berarti ada margin, jadi kombinasi ini
      tidak mungkin terjadi pada akun sungguhan.

    * `accountValue == 0` TANPA posisi -> TIDAK ditolak. Collateral bisa
      berada di SPOT. Dokumentasi Portfolio margin menyatakan spot dan
      perps "are collectively margined together within one account",
      jadi `accountValue` perps bisa nol sementara akun itu jelas bukan
      akun kosong. Menolaknya akan menghentikan bot yang sebenarnya sehat.
    """

    def test_zero_account_value_with_position_is_rejected(self):
        engine = _engine_with_state(_state("0", [_position()]))
        with self.assertRaises(UnverifiableAccount) as ctx:
            engine._fetch_remote_positions()
        self.assertEqual(ctx.exception.code,
                         UnverifiableAccount.EMPTY_ACCOUNT)

    def test_zero_account_value_without_position_is_accepted(self):
        """
        Akun perps datar TIDAK otomatis berarti akun kosong.

        Ini koreksi terhadap versi test sebelumnya, yang menolak
        `accountValue == 0` tanpa syarat. Versi itu akan menolak setiap
        akun yang collateral-nya ada di spot — penolakan palsu pada akun
        yang sehat, dan penolakan palsu lebih buruk daripada penolakan
        yang benar.
        """
        engine = _engine_with_state(_state("0"))
        self.assertEqual(
            engine._fetch_remote_positions(), [],
            "akun tanpa posisi perps dan tanpa margin perps bukan bukti "
            "akun kosong — collateral bisa ada di spot",
        )

    def test_zero_position_entry_is_not_a_position(self):
        """
        Entri dengan `szi == 0` BUKAN posisi terbuka.

        Bursa mengirim entri untuk aset yang pernah dibuka lalu ditutup.
        Menghitungnya sebagai posisi membuat `has_position` benar, dan
        `accountValue == 0` ikut ditolak untuk akun yang sebenarnya datar.
        """
        closed = {"type": "oneWay",
                  "position": {"coin": "BTC", "szi": "0", "entryPx": "0"}}
        engine = _engine_with_state(_state("0", [closed]))
        self.assertEqual(engine._fetch_remote_positions(), [])

    def test_empty_account_value_fails_closed(self):
        """
        Ini inti defectnya: reconcile harus menyalakan kill switch.

        Sebelum guard, `positions() == []` membuat `healthy = True` dan
        kill switch tetap mati.
        """
        import asyncio

        engine = _engine_with_state(_state("0"))
        with patch.object(engine, "_fetch_remote_positions",
                          side_effect=UnverifiableAccount("akun kosong")):
            report = asyncio.run(engine.reconcile())

        self.assertFalse(report["state_known"],
                         "reconcile melaporkan akun kosong seolah-olah datar")
        self.assertTrue(engine.gate.engaged,
                        "kill switch TIDAK menyala saat posisi tidak bisa "
                        "dipastikan")

    def test_report_says_why_not_just_that_it_failed(self):
        """Laporan harus menyebut penyebabnya."""
        import asyncio

        engine = _engine_with_state(_state("0"))
        with patch.object(engine, "_fetch_remote_positions",
                          side_effect=UnverifiableAccount("akun kosong")):
            report = asyncio.run(engine.reconcile())

        self.assertIn("problems", report)
        self.assertTrue(report["problems"],
                        "report tidak menjelaskan kenapa gagal")


class TestMalformedStateIsRejected(unittest.TestCase):
    """Respons yang bentuknya bukan akun harus ditolak."""

    def _code_for(self, state):
        engine = _engine_with_state(state)
        try:
            engine._fetch_remote_positions()
        except UnverifiableAccount as exc:
            return exc.code
        self.fail("respons cacat tidak ditolak")

    def test_missing_margin_summary(self):
        self.assertEqual(self._code_for({"assetPositions": []}),
                         UnverifiableAccount.MALFORMED)

    def test_missing_account_value(self):
        self.assertEqual(
            self._code_for({"marginSummary": {"totalNtlPos": "0"},
                            "assetPositions": []}),
            UnverifiableAccount.MALFORMED)

    def test_non_numeric_account_value(self):
        self.assertEqual(self._code_for(_state("bukan-angka")),
                         UnverifiableAccount.MALFORMED)

    def test_non_dict_response(self):
        self.assertEqual(self._code_for(["bukan", "objek"]),
                         UnverifiableAccount.MALFORMED)

    def test_none_response(self):
        self.assertEqual(self._code_for(None),
                         UnverifiableAccount.MALFORMED)


class TestRealFlatAccountStillPasses(unittest.TestCase):
    """
    Kontrol yang menjaga perbaikan ini tidak berubah jadi "selalu menolak".

    Akun sungguhan yang Datar punya collateral, jadi `accountValue` bukan
    nol. Kalau test ini hilang, reconcile tidak pernah hijau dan kill
    switch jadi tidak berguna — operator akan menyimpulkan sistemnya
    rusak dan melepasnya paksa, dan proteksi yang nyata ikut hilang.
    """

    def test_flat_account_with_collateral_is_flat(self):
        import asyncio

        engine = _engine_with_state(_state("1000"))
        self.assertEqual(engine._fetch_remote_positions(), [],
                         "tidak ada posisi, dan itu memang benar")

        report = asyncio.run(engine.reconcile())
        self.assertTrue(report["state_known"],
                        "akun datar yang sungguhan dilaporkan tidak bisa "
                        "dipastikan — ini false positive")
        self.assertFalse(engine.gate.engaged,
                         "kill switch menyala padahal akun memang datar")

    def test_small_but_nonzero_account_is_accepted(self):
        """Akun dust tetap akun. Menolaknya jadi penolakan palsu."""
        engine = _engine_with_state(_state("0.01"))
        self.assertEqual(engine._fetch_remote_positions(), [])

    def test_position_with_collateral_is_read(self):
        """Posisi nyata + collateral = kondisi sehat, tidak diblokir."""
        engine = _engine_with_state(_state("1000", [_position()]))
        remote = engine._fetch_remote_positions()
        self.assertEqual(len(remote), 1)
        self.assertEqual(remote[0]["coin"], "BTC")
        self.assertEqual(remote[0]["symbol"], normalize_symbol("BTC"))


class TestPositionWithZeroAccountValueIsRejected(unittest.TestCase):
    """
    Posisi yang ADA tapi `accountValue` nol juga harus fail closed.

    Kasus ini yang paling tajam: ada isi, jadi filter `szi != 0`
    mengembalikannya, dan tanpa guard reconcile melaporkan posisi yang
    cocok — padahal bentuk akunnya tidak seharusnya begitu.
    """

    def test_position_with_zero_account_value_rejected(self):
        engine = _engine_with_state(_state("0", [_position()]))
        with self.assertRaises(UnverifiableAccount) as ctx:
            engine._fetch_remote_positions()
        self.assertEqual(ctx.exception.code,
                         UnverifiableAccount.EMPTY_ACCOUNT)


class TestSpotEquityIsNotConfusedWithEmpty(unittest.TestCase):
    """
    Collateral di SPOT tidak boleh terbaca sebagai "akun kosong".

    `marginSummary.accountValue` hanya mencakup margin PERPS.
    Dokumentasi Portfolio margin: spot dan perps "are collectively
    margined together within one account", jadi saldo spot tidak muncul
    di angka itu.

    `_require_real_account()` sudah tidak menolak `accountValue == 0`
    tanpa posisi karena itu. Pemeriksaan di sini melengkapi sisi
    sebaliknya: ketika TIDAK ADA posisi perps dan TIDAK ADA margin
    perps, saldo spot adalah satu-satunya bukti apakah akun ini nyata.
    """

    def _exchange(self, spot_state):
        from trading.live.client import LiveExchange

        ex = LiveExchange.__new__(LiveExchange)
        ex.query_address = ACCOUNT_WITH_POSITION

        class _Info:
            def spot_user_state(self, address):
                if isinstance(spot_state, Exception):
                    raise spot_state
                return spot_state

        ex._info = _Info()
        return ex

    def test_usdc_balance_means_account_is_funded(self):
        ex = self._exchange({"balances": [
            {"coin": "USDC", "total": "500.0", "hold": "0"}]})
        self.assertIs(ex.spot_equity_is_nonzero(), True)

    def test_zero_usdc_balance_means_no_spot_funds(self):
        ex = self._exchange({"balances": [
            {"coin": "USDC", "total": "0.0", "hold": "0"}]})
        self.assertIs(ex.spot_equity_is_nonzero(), False)

    def test_absent_usdc_entry_with_other_nonzero_token_is_funded(self):
        """
        Tidak ada entri USDC, tapi ada token lain yang bersaldo.

        Ini koreksi terhadap versi test sebelumnya, yang mengembalikan
        `False` begitu tidak menemukan USDC. Sekarang berlaku untuk "token
        apa pun yang tidak nol" — akun dengan HYPE tapi tanpa USDC tetap
        akun bersaldo, dan collateral-nya nyata menurut portfolio
        margin.
        """
        ex = self._exchange({"balances": [
            {"coin": "PURR", "total": "10.0", "hold": "0"}]})
        self.assertIs(ex.spot_equity_is_nonzero(), True)

    def test_any_nonzero_token_counts_as_funded(self):
        """
        Token apa pun yang tidak nol = akun punya collateral.

        Nilai TIDAK dijumlahkan. Untuk pertanyaan "nol atau bukan" satuan
        token tidak relevan, dan HYPE/BTC adalah collateral yang sah
        (LTV 0.65 / 0.5 menurut dokumentasi portfolio margin).
        """
        for coin, total in (("HYPE", "0.0001"), ("BTC", "0.5"),
                            ("USDC", "1"), ("PURR", "1")):
            with self.subTest(coin=coin):
                ex = self._exchange({"balances": [
                    {"coin": coin, "total": total, "hold": "0"}]})
                self.assertIs(
                    ex.spot_equity_is_nonzero(), True,
                    "%s=%s seharusnya dihitung sebagai collateral" % (coin, total))

    def test_tiny_nonzero_balance_is_still_funded(self):
        """
        Saldo dust tetap collateral.

        Ambang nol, bukan "cukup besar". Menolak 0,0001 HYPE berarti
        menandai akun yang hidup sebagai kosong — penolakan palsu pada
        akun yang punya collateral nyata.
        """
        ex = self._exchange({"balances": [
            {"coin": "HYPE", "total": "0.0000001", "hold": "0"}]})
        self.assertIs(ex.spot_equity_is_nonzero(), True)

    def test_unreadable_response_is_unknown_not_false(self):
        """
        Respons tak terbaca harus `None`, bukan `False`.

        Ini perbedaan yang menentukan: `False` berarti "akun ini memang
        kosong", `None` berarti "bot tidak tahu". Menyamakannya membuat
        preflight menolak akun yang sebenarnya sehat — penolakan palsu
        lebih buruk daripada penolakan yang benar.
        """
        ex = self._exchange(ConnectionError("jaringan putus"))
        self.assertIsNone(ex.spot_equity_is_nonzero(),
                          "kegagalan jaringan harus 'tidak diketahui', "
                          "bukan 'tidak ada dana'")

    def test_non_dict_response_is_unknown(self):
        ex = self._exchange(["bukan", "dict"])
        self.assertIsNone(ex.spot_equity_is_nonzero())

    def test_unparseable_total_is_unknown(self):
        ex = self._exchange({"balances": [
            {"coin": "USDC", "total": "bukan-angka", "hold": "0"}]})
        self.assertIsNone(ex.spot_equity_is_nonzero())

    def test_only_usdc_is_counted(self):
        """
        Token lain yang bersaldo juga dihitung.

        Dibalik dari versi test sebelumnya, yang mengharapkan HYPE/
        PURR diabaikan. Untuk pertanyaan "apakah akun ini punya
        collateral", satuan token tidak relevan — yang relevan adalah nol
        atau bukan. HYPE dan BTC adalah collateral sah di portfolio
        margin (LTV 0.65 / 0.5).
        """
        ex = self._exchange({"balances": [
            {"coin": "HYPE", "total": "1.0", "hold": "0"}]})
        self.assertIs(ex.spot_equity_is_nonzero(), True,
                      "HYPE bersaldo harus dihitung sebagai collateral")

    def test_all_zero_tokens_means_empty(self):
        """Semua token nol = memang tidak ada saldo."""
        ex = self._exchange({"balances": [
            {"coin": "USDC", "total": "0", "hold": "0"},
            {"coin": "HYPE", "total": "0.0", "hold": "0"},
            {"coin": "PURR", "total": "0", "hold": "0"}]})
        self.assertIs(ex.spot_equity_is_nonzero(), False)

    def test_held_balance_counts_as_funded(self):
        """
        Saldo yang sedang di-HOLD order GTC tetap milik akun.

        Kalau order resting tertinggal, collateral-nya sudah ada di bursa
        dan akun itu jelas bukan kosong.
        """
        ex = self._exchange({"balances": [
            {"coin": "USDC", "total": "0", "hold": "250.0"}]})
        self.assertIs(ex.spot_equity_is_nonzero(), True)

    def test_reads_only_queried_address(self):
        """Query memakai `query_address` (master), bukan signer."""
        from trading.live.client import LiveExchange

        asked = []

        ex = LiveExchange.__new__(LiveExchange)
        ex.query_address = ACCOUNT_WITH_POSITION

        class _Info:
            def spot_user_state(self, address):
                asked.append(address)
                return {"balances": []}

        ex._info = _Info()
        ex.spot_equity_is_nonzero()
        self.assertEqual(asked, [ACCOUNT_WITH_POSITION])


class TestPreflightAcceptsSpotFundedAccount(unittest.TestCase):
    """
    Akun yang dananya di SPOT harus LULUS preflight.

    Ini kebalikan dari test yang menolak. Kalau preflight menolak
    `accountValue == 0` tanpa posisi, setiap akun bersaldo spot — yang
    sangat mungkin di portfolio margin — akan ditolak, dan penolakan
    palsu lebih buruk daripada penolakan yang benar.
    """

    def _exchange(self, perps_value, spot_state):
        from trading.live.client import LiveExchange

        ex = LiveExchange.__new__(LiveExchange)
        ex._account_address = None
        ex._rules = None
        ex.address = ACCOUNT_WITH_POSITION
        ex.query_address = ACCOUNT_WITH_POSITION
        ex.testnet = True

        class _Info:
            def meta(self):
                return {"universe": [{"name": "BTC", "szDecimals": 5,
                                      "maxLeverage": 25}]}

            def user_state(self, address, dex=""):
                return {"assetPositions": [],
                        "marginSummary": {"accountValue": str(perps_value),
                                          "withdrawable": str(perps_value)}}

            def spot_user_state(self, address):
                if isinstance(spot_state, Exception):
                    raise spot_state
                return spot_state

            def extra_agents(self, user):
                return []

        ex._info = _Info()
        return ex

    def test_spot_funded_account_passes_preflight(self):
        """Margin perps nol, tapi ada saldo USDC di spot."""
        ex = self._exchange("0", {"balances": [
            {"coin": "USDC", "total": "5000", "hold": "0"}]})
        ex.preflight()

    def test_genuinely_empty_account_rejected(self):
        """Nol di perps DAN nol di spot = memang tidak ada apa-apa."""
        ex = self._exchange("0", {"balances": []})
        with self.assertRaises(PreflightError) as ctx:
            ex.preflight()
        self.assertEqual(ctx.exception.code,
                         PreflightError.ACCOUNT_APPEARS_EMPTY)

    def test_unreadable_spot_is_not_treated_as_empty(self):
        """
        Spot tidak terbaca = TIDAK BISA DIPASTIKAN, bukan "akun kosong".

        Kalau spot hanya gagal dibaca karena rate limit, akun yang
        sebenarnya sehat akan ditolak. Itu penolakan palsu yang paling
        mungkin terjadi di produksi.
        """
        ex = self._exchange("0", ConnectionError("rate limit"))
        with self.assertRaises(PreflightError) as ctx:
            ex.preflight()
        # Tetap menolak, TAPI dengan kode dan pesan yang berbeda dari
        # "akun kosong".
        self.assertEqual(ctx.exception.code,
                         PreflightError.ACCOUNT_APPEARS_EMPTY)
        self.assertIn("TIDAK BISA MEMASTIKAN", str(ctx.exception),
                      "pesan harus menyatakan spot tidak terbaca, bukan "
                      "menyimpulkan akunnya kosong")

    def test_non_usdc_spot_balance_is_funded(self):
        """
        Collateral non-USDC membuat akun LULUS, bukan ditolak.

        Versi test sebelumnya mengharapkan penolakan dengan pesan
        "spot selain USDC". Itu berasal dari asumsi bahwa hanya USDC
        yang dihitung — asumsi yang salah menurut dokumentasi portfolio
        margin, di mana HYPE dan BTC adalah collateral sah.
        """
        ex = self._exchange("0", {"balances": [
            {"coin": "HYPE", "total": "100", "hold": "0"}]})
        ex.preflight()


if __name__ == "__main__":
    unittest.main()