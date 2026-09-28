"""
tests/test_live_console.py — Bukti bahwa menu TIDAK bisa mencapai live
dengan mudah.

Setiap test di sini mencoba menemukan jalan terpendek ke live, lalu
membuktikan jalan itu tertutup. Kalau ada satu test yang berhasil menembus,
seluruh sistem tidak layak dipakai untuk uang sungguhan.
"""
import unittest

from core.config import LiveConfig
from trading.live.console import (
    CONFIRM_PHRASE, check_consistency, decide_mode, derive_address,
    typed_addr_mismatch, validate_rule,
)

ADDRESS = "0x1234567890AbcdEF1234567890aBcdef12345678"
KEY = "0x" + "ab" * 32


def _env(**extra) -> dict:
    env = {"HYPERLIQUID_PRIVATE_KEY": KEY}
    env.update(extra)
    return env


class TestPaperIsDefault(unittest.TestCase):
    def test_choice_1_is_paper(self):
        d = decide_mode("1", "", "", None, {})
        self.assertEqual(d.mode, "paper")
        self.assertFalse(d.refused)

    def test_unknown_choice_falls_back_to_paper(self):
        """Tombol acak, atau Ctrl-D di tengah menu, tidak boleh live."""
        for choice in ("", "9", "x", "0", "-1", "paper", "3.0"):
            d = decide_mode(choice, CONFIRM_PHRASE, ADDRESS, ADDRESS, _env())
            self.assertEqual(
                d.mode, "paper",
                "pilihan '{}' tidak boleh menghasilkan live".format(choice),
            )

    def test_live_never_chosen_without_key(self):
        for choice in ("2", "3"):
            d = decide_mode(choice, CONFIRM_PHRASE, ADDRESS, ADDRESS, {})
            self.assertEqual(d.mode, "paper")
            self.assertTrue(d.refused)
            self.assertIn("KEY", d.reason.upper())


class TestConfirmationRequired(unittest.TestCase):
    """Konfirmasi harus benar-benar dicek, bukan sekadar dibaca."""

    def test_wrong_phrase_refused(self):
        for phrase in ("", "ya", "yes", "saya mengerti", "SAYA MENGERTI.",
                       " saya mengerti", "SAYA MENGERTI!"):
            d = decide_mode("2", phrase, ADDRESS, ADDRESS, _env())
            self.assertEqual(
                d.mode, "paper",
                "kalimat '{}' tidak boleh menyalakan live".format(phrase),
            )
            self.assertTrue(d.refused)

    def test_phrase_is_case_sensitive(self):
        d = decide_mode("2", CONFIRM_PHRASE.lower(), ADDRESS, ADDRESS, _env())
        self.assertTrue(d.refused, "huruf kecil tidak boleh dihitung")

    def test_phrase_with_padding_accepted(self):
        """Spasi di awal/akhir sah — itu kesalahan ketik, bukan niat lain."""
        d = decide_mode("2", "  " + CONFIRM_PHRASE + "  ", ADDRESS,
                        ADDRESS, _env())
        self.assertEqual(d.mode, "testnet")
        self.assertFalse(d.refused)

    def test_wrong_address_refused(self):
        """Alamat yang diketik harus sama persis dengan yang ditampilkan."""
        other = "0x" + "ff" * 20
        d = decide_mode("2", CONFIRM_PHRASE, other, ADDRESS, _env())
        self.assertTrue(d.refused, "alamat berbeda harus ditolak")
        self.assertEqual(d.mode, "paper")

    def test_address_matching_is_case_insensitive(self):
        """Beda huruf besar saja bukan akun berbeda."""
        d = decide_mode("2", CONFIRM_PHRASE, ADDRESS.upper(), ADDRESS, _env())
        self.assertEqual(d.mode, "testnet")

    def test_empty_address_refused(self):
        d = decide_mode("2", CONFIRM_PHRASE, "", ADDRESS, _env())
        self.assertTrue(d.refused)
        d = decide_mode("2", CONFIRM_PHRASE, ADDRESS, None, _env())
        self.assertTrue(d.refused)

    def test_full_confirmation_enables_testnet(self):
        d = decide_mode("2", CONFIRM_PHRASE, ADDRESS, ADDRESS, _env())
        self.assertEqual(d.mode, "testnet")
        self.assertEqual(d.address, ADDRESS)
        self.assertEqual(d.private_key, KEY)
        self.assertFalse(d.refused)

    def test_full_confirmation_enables_mainnet(self):
        d = decide_mode("3", CONFIRM_PHRASE, ADDRESS, ADDRESS, _env())
        self.assertEqual(d.mode, "mainnet")

    def test_all_three_conditions_needed_together(self):
        """Hanya kombinasi lengkap yang boleh; satu kurang selalu paper."""
        cases = (
            # (phrase, typed_addr, env_key)
            ("", "", _env()),
            (CONFIRM_PHRASE, "", _env()),
            ("", ADDRESS, _env()),
            (CONFIRM_PHRASE, ADDRESS, {}),
        )
        for phrase, addr, env in cases:
            d = decide_mode("3", phrase, addr, ADDRESS, env)
            self.assertEqual(d.mode, "paper",
                             "kombinasi {!r}/{!r} tidak boleh live".format(
                                 phrase, addr))


class TestAddressDerivation(unittest.TestCase):
    def test_valid_key_gives_address(self):
        addr = derive_address(KEY)
        self.assertIsNotNone(addr)
        self.assertTrue(addr.startswith("0x"))
        self.assertEqual(len(addr), 42)

    def test_garbage_key_returns_none(self):
        """Key rusak harus jadi None, tidak boleh melempar exception."""
        for bad in ("", "bukan-key", "0x", "0xzz", KEY[:-2]):
            self.assertIsNone(derive_address(bad),
                              "key '{}' seharusnya ditolak".format(bad))

    def test_typed_addr_mismatch_helper(self):
        self.assertFalse(typed_addr_mismatch(ADDRESS, ADDRESS))
        self.assertFalse(typed_addr_mismatch(ADDRESS, ADDRESS.upper()))
        self.assertTrue(typed_addr_mismatch(ADDRESS, "0xdead"))
        self.assertTrue(typed_addr_mismatch(None, ADDRESS))
        self.assertTrue(typed_addr_mismatch(ADDRESS, None))


class TestRuleValidation(unittest.TestCase):
    """Editor aturan harus menolak nilai yang tidak masuk akal."""

    def test_valid_float_accepted(self):
        value, err = validate_rule("x", "250.5", "float", 10.0, 1000.0)
        self.assertIsNone(err)
        self.assertAlmostEqual(value, 250.5)

    def test_valid_int_accepted(self):
        value, err = validate_rule("x", "50", "int", 1, 1000)
        self.assertIsNone(err)
        self.assertEqual(value, 50)
        self.assertIsInstance(value, int)

    def test_non_numeric_rejected(self):
        for raw in ("abc", "", "1.2.3", "1,5"):
            value, err = validate_rule("x", raw, "float", 0.0, 100.0)
            self.assertIsNone(value, "nilai '{}' harus ditolak".format(raw))
            self.assertTrue(err)

    def test_nan_and_infinity_rejected(self):
        """
        NaN dan inf harus DITOLAK.

        `float("NaN")` berhasil di Python, dan perbandingan dengan NaN
        selalu False. Batas yang berisi NaN akan terlihat ada tapi TIDAK
        PERNAH menyala — proteksi yang tidak melindungi apa pun.
        """
        for raw in ("NaN", "nan", "inf", "-inf", "Infinity", "1e400"):
            value, err = validate_rule("x", raw, "float", 0.0, 100.0)
            self.assertIsNone(value, "'{}' harus ditolak".format(raw))
            self.assertTrue(err, "'{}' harus punya pesan error".format(raw))

    def test_valid_value_returns_none_error(self):
        """Nilai yang valid mengembalikan `None`, bukan string kosong."""
        value, err = validate_rule("x", "50", "float", 0.0, 100.0)
        self.assertIsNone(err, "error harus None saat valid")
        self.assertEqual(value, 50.0)

    def test_out_of_range_rejected(self):
        for raw in ("0", "-5", "1e9", "1e-9"):
            value, err = validate_rule("x", raw, "float", 10.0, 1000.0)
            self.assertIsNone(value, "nilai '{}' di luar rentang".format(raw))
            self.assertTrue(err)

    def test_boundaries_accepted(self):
        for raw in ("10", "1000"):
            value, err = validate_rule("x", raw, "float", 10.0, 1000.0)
            self.assertIsNone(err, "batas harus boleh: " + raw)
            self.assertIsNotNone(value)

    def test_int_rejects_float_text(self):
        value, err = validate_rule("x", "3.7", "int", 1, 100)
        self.assertIsNone(value)
        self.assertTrue(err, "field int tidak boleh menerima '3.7'")


class TestConsistencyWarnings(unittest.TestCase):
    """
    Hubungan antar batas.

    Ini peringatan, bukan error yang memblokir: angka yang "aneh" kadang
    memang disengaja. Yang penting operator TAHU.
    """

    def test_default_config_is_consistent(self):
        self.assertEqual(
            check_consistency(LiveConfig()), [],
            "default harus bebas peringatan",
        )

    def test_order_bigger_than_position_warns(self):
        cfg = LiveConfig()
        cfg.max_order_notional = 500.0
        cfg.max_position_notional = 100.0
        warnings = check_consistency(cfg)
        self.assertTrue(any("order maks" in w for w in warnings))

    def test_daily_loss_bigger_than_exposure_warns(self):
        cfg = LiveConfig()
        cfg.max_total_notional = 100.0
        cfg.max_daily_loss = 500.0
        warnings = check_consistency(cfg)
        self.assertTrue(
            any("rugi harian" in w for w in warnings),
            "batas rugi harian > total eksposur harus diperingatkan: "
            + str(warnings),
        )

    def test_exposure_below_position_warns(self):
        cfg = LiveConfig()
        cfg.max_position_notional = 900.0
        cfg.max_total_notional = 200.0
        self.assertTrue(check_consistency(cfg))

