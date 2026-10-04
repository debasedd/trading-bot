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
    """Akun kosong dari bursa TIDAK boleh dibaca sebagai "datar"."""

    def test_zero_account_value_is_unverifiable(self):
        engine = _engine_with_state(_state("0"))
        with self.assertRaises(UnverifiableAccount) as ctx:
            engine._fetch_remote_positions()
        self.assertEqual(ctx.exception.code,
                         UnverifiableAccount.EMPTY_ACCOUNT)

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


if __name__ == "__main__":
    unittest.main()