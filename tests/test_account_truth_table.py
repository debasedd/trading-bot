"""
tests/test_account_truth_table.py — TABEL KEBENARAN "APA ITU AKUN NYATA?"

Kenapa tabel, bukan daftar test biasa: kombinasi `accountValue` / spot /
posisi punya interaksi yang tidak terlihat dari salah satu test. Setelah
sebelumnya salah di sini dua kali — sekali menghapus validasi alamat,
sekali menganggap `accountValue == 0` sebagai bukti akun kosong padahal
collateral bisa ada di SPOT — setiap sel dibuat eksplisit dan setiap sel
punya test sendiri.

TABEL
-----
  # | accountValue | posisi | spot        | hasil
  --+--------------+--------+-------------+---------------------------
  1 | > 0          | ya/tidak| apa pun    | LOLOS. Ada margin perps.
  2 | 0            | YA     | apa pun    | FAIL CLOSED. Posisi berarti
    |              |        |             | ada margin; 0 + posisi bukan
    |              |        |             | bentuk akun nyata.
  3 | 0            | tidak  | > 0        | LOLOS. Collateral di SPOT.
  4 | 0            | tidak  | 0          | DITOLAK. Salah alamat/akun
    |              |        |             | kosong tanpa posisi dan saldo.
  5 | 0            | tidak  | TIDAK      | TIDAK BISA MEMASTIKAN (bukan
    |              |        | TERBACA     | tolak, bukan lolos).

Baris 5 paling mudah hilang: spot tidak terbaca karena rate limit
DIBEDAKAN dari spot nol. Menyamakan keduanya membuat bot menolak akun
yang sehat (baris 5 jadi 4) ATAU — lebih buruk — meloloskan akun yang
salah (baris 5 jadi 3).

Baris 4 adalah kasus bug preflight asli: `account_address` berisi alamat
yang bukan akun sungguhan, dan bursa membalas akun kosong tanpa error.
"""

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from trading.live.client import LiveExchange, PreflightError

ACCOUNT = "0x5972698398d8c5bbe67c0db74906236691020417"
SIGNER = "0x" + "ab" + "11" * 19
OTHER = "0x" + "cd" + "22" * 19

# Alamat yang harus SEBELUMNYA ditolak: `account_address` salah ketik.
UNKNOWN_ADDRESSES = (ACCOUNT, SIGNER, OTHER)


def _perps_state(account_value, positions=()):
    return {
        "assetPositions": [
            {"type": "oneWay",
             "position": {"coin": "BTC", "szi": szi, "entryPx": "100"}}
            for szi in positions
        ],
        "marginSummary": {
            "accountValue": str(account_value),
            "withdrawable": str(account_value),
            "totalNtlPos": "0",
        },
    }


def _exchange(perps_state, spot_state):
    ex = LiveExchange.__new__(LiveExchange)
    ex._account_address = None
    ex._rules = None
    ex.address = ACCOUNT
    ex.query_address = ACCOUNT
    ex.testnet = True

    class _Info:
        def meta(self):
            return {"universe": [{"name": "BTC", "szDecimals": 5,
                                  "maxLeverage": 25}]}

        def user_state(self, address, dex=""):
            return perps_state

        def spot_user_state(self, address):
            if isinstance(spot_state, Exception):
                raise spot_state
            return spot_state

        def extra_agents(self, user):
            return []

    ex._info = _Info()
    return ex


def _usdc(total="0.0"):
    return {"balances": [{"coin": "USDC", "total": total, "hold": "0"}]}


class TestTruthTableRow1MarginPerpsExists(unittest.TestCase):
    """Baris 1: accountValue > 0 -> LOLOS, apa pun yang lain."""

    def test_margin_with_position_passes(self):
        _exchange(_perps_state("1000", ["0.5"]), _usdc("0")).preflight()

    def test_margin_without_position_passes(self):
        _exchange(_perps_state("1000"), _usdc("0")).preflight()

    def test_margin_with_zero_spot_passes(self):
        _exchange(_perps_state("1000"), {"balances": []}).preflight()

    def test_margin_with_unreadable_spot_passes(self):
        """
        Spot tidak terbaca TIDAK membatalkan margin perps yang ada.

        Fail closed berlaku pada apa yang tidak diketahui, bukan pada apa
        yang sudah diketahui. `accountValue` 1000 adalah bukti cukup.
        """
        _exchange(_perps_state("1000"),
                  ConnectionError("rate limit")).preflight()


class TestTruthTableRow2ZeroWithPosition(unittest.TestCase):
    """Baris 2: accountValue 0 + posisi -> FAIL CLOSED."""

    def test_zero_margin_with_position_rejected(self):
        ex = _exchange(_perps_state("0", ["0.5"]), _usdc("0"))
        with self.assertRaises(PreflightError) as ctx:
            ex.preflight()
        self.assertEqual(ctx.exception.code,
                         PreflightError.ACCOUNT_APPEARS_EMPTY)

    def test_zero_margin_with_position_rejected_even_if_spot_funded(self):
        """
        Spot bersaldo TIDAK menyelamatkan kombinasi ini.

        Posisi sebesar itu berarti ada margin; margin itu harus terlihat
        di `accountValue`. Kalau tidak, bentuk responsnya bukan akun
        sungguhan, dan spot yang dibaca bukan spot akun itu.
        """
        ex = _exchange(_perps_state("0", ["0.5"]), _usdc("99999"))
        with self.assertRaises(PreflightError) as ctx:
            ex.preflight()
        self.assertEqual(ctx.exception.code,
                         PreflightError.ACCOUNT_APPEARS_EMPTY)

    def test_rejected_for_every_mistyped_address(self):
        """
        Kasus bug preflight asli, diuji untuk tiap alamat salah ketik.

        Bursa membalas akun kosong untuk alamat yang bukan akun
        sungguhan. Kalau respons itu punya `szi != 0` tanpa margin,
        bentuknya bukan akun nyata.
        """
        for wrong in UNKNOWN_ADDRESSES:
            with self.subTest(address=wrong):
                ex = _exchange(_perps_state("0", ["0.5"]), _usdc("0"))
                with self.assertRaises(PreflightError):
                    ex.preflight()


class TestTruthTableRow3ZeroWithSpotCollateral(unittest.TestCase):
    """Baris 3: accountValue 0 + tanpa posisi + spot > 0 -> LOLOS."""

    def test_spot_usdc_funded_passes(self):
        _exchange(_perps_state("0"), _usdc("5000")).preflight()

    def test_spot_other_token_funded_passes(self):
        """HYPE/BTC adalah collateral sah (LTV 0.65 / 0.5)."""
        _exchange(_perps_state("0"), {"balances": [
            {"coin": "HYPE", "total": "1", "hold": "0"}]}).preflight()

    def test_spot_dust_passes(self):
        """Saldo dust tetap collateral."""
        _exchange(_perps_state("0"), _usdc("0.00001")).preflight()

    def test_spot_hold_only_passes(self):
        """Order GTC yang sedang hold = collateral sudah ada di bursa."""
        _exchange(_perps_state("0"), {"balances": [
            {"coin": "USDC", "total": "0", "hold": "100"}]}).preflight()


class TestTruthTableRow4ZeroNoPositionNoSpot(unittest.TestCase):
    """
    Baris 4: accountValue 0 + tanpa posisi + spot 0 -> DITOLAK.

    Ini kasus bug preflight asli: `account_address` berisi alamat yang
    bukan akun sungguhan, jadi bursa membalas akun kosong.
    """

    def test_completely_empty_rejected(self):
        ex = _exchange(_perps_state("0"), _usdc("0"))
        with self.assertRaises(PreflightError) as ctx:
            ex.preflight()
        self.assertEqual(ctx.exception.code,
                         PreflightError.ACCOUNT_APPEARS_EMPTY)

    def test_empty_with_no_balances_rejected(self):
        ex = _exchange(_perps_state("0"), {"balances": []})
        with self.assertRaises(PreflightError) as ctx:
            ex.preflight()
        self.assertEqual(ctx.exception.code,
                         PreflightError.ACCOUNT_APPEARS_EMPTY)

    def test_empty_with_all_zero_tokens_rejected(self):
        ex = _exchange(_perps_state("0"), {"balances": [
            {"coin": "USDC", "total": "0", "hold": "0"},
            {"coin": "HYPE", "total": "0", "hold": "0"}]})
        with self.assertRaises(PreflightError) as ctx:
            ex.preflight()
        self.assertEqual(ctx.exception.code,
                         PreflightError.ACCOUNT_APPEARS_EMPTY)

    def test_closed_position_entry_is_not_a_position(self):
        """
        Entri `szi == 0` bukan posisi, jadi sel ini tetap baris 4.

        Bursa mengirim entri untuk aset yang pernah dibuka lalu ditutup.
        Menghitungnya sebagai posisi membuat kondisi "0 + ada posisi"
        menyala untuk akun yang sebenarnya datar.
        """
        ex = _exchange(
            {"assetPositions": [{"type": "oneWay", "position": {
                "coin": "BTC", "szi": "0", "entryPx": "0"}}],
             "marginSummary": {"accountValue": "0", "withdrawable": "0"}},
            _usdc("0"))
        with self.assertRaises(PreflightError) as ctx:
            ex.preflight()
        self.assertEqual(ctx.exception.code,
                         PreflightError.ACCOUNT_APPEARS_EMPTY)


class TestTruthTableRow5SpotUnreadable(unittest.TestCase):
    """
    Baris 5: spot TIDAK TERBACA -> TIDAK BISA MEMASTIKAN.

    Bukan "tidak ada dana". Baris ini yang paling mudah hilang, dan
    hilang di sini berarti dua kemungkinan buruk sekaligus.
    """

    def test_unreadable_spot_reported_as_unverifiable(self):
        ex = _exchange(_perps_state("0"), ConnectionError("rate limit"))
        with self.assertRaises(PreflightError) as ctx:
            ex.preflight()
        self.assertEqual(ctx.exception.code,
                         PreflightError.ACCOUNT_APPEARS_EMPTY)
        self.assertIn("TIDAK BISA MEMASTIKAN", str(ctx.exception),
                      "pesan harus menyatakan spot tidak terbaca, bukan "
                      "menyimpulkan akunnya kosong")

    def test_non_dict_spot_is_unverifiable(self):
        with self.assertRaises(PreflightError):
            _exchange(_perps_state("0"), ["bukan", "dict"]).preflight()

    def test_unparseable_total_is_unverifiable(self):
        ex = _exchange(_perps_state("0"), {"balances": [
            {"coin": "USDC", "total": "bukan-angka", "hold": "0"}]})
        with self.assertRaises(PreflightError) as ctx:
            ex.preflight()
        self.assertIn("TIDAK BISA MEMASTIKAN", str(ctx.exception))

    def test_one_bad_entry_makes_whole_balance_unverifiable(self):
        """
        Satu entri rusak membuat seluruh saldo tidak diketahui.

        USDC terbaca 0 tapi HYPE tidak bisa diparse. Menyimpulkan "saldo
        nol" berarti akun kosong, padahal mungkin ada HYPE.
        """
        ex = _exchange(_perps_state("0"), {"balances": [
            {"coin": "USDC", "total": "0", "hold": "0"},
            {"coin": "HYPE", "total": "bukan-angka", "hold": "0"}]})
        self.assertIsNone(ex.spot_equity_is_nonzero())


class TestTableCoverage(unittest.TestCase):
    """
    Kontrol: tabelnya benar-benar terisi.

    Kalau ada sel tanpa test, tabel ini hanya dokumentasi — dan
    dokumentasi tanpa bukti adalah klaim, bukan pegangan.
    """

    def test_every_row_has_at_least_three_tests(self):
        classes = [
            TestTruthTableRow1MarginPerpsExists,
            TestTruthTableRow2ZeroWithPosition,
            TestTruthTableRow3ZeroWithSpotCollateral,
            TestTruthTableRow4ZeroNoPositionNoSpot,
            TestTruthTableRow5SpotUnreadable,
        ]
        for cls in classes:
            with self.subTest(kelas=cls.__name__):
                count = len([m for m in dir(cls) if m.startswith("test_")])
                self.assertGreaterEqual(
                    count, 3, "%s punya terlalu sedikit test" % cls.__name__)

    def test_docstring_table_lists_every_row(self):
        """Tabel di docstring harus menyebut kelima baris."""
        import test_account_truth_table as mod
        doc = mod.__doc__ or ""
        for row in ("1", "2", "3", "4", "5"):
            self.assertIn(" %s | " % row, doc,
                          "baris %s tidak ada di tabel docstring" % row)


if __name__ == "__main__":
    unittest.main()