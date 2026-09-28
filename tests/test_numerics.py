"""
tests/test_numerics.py — Uji numerik yang hanya terlihat saat dijalankan.

Bug di sini bukan salah ketik; itu hasil aritmetika yang terlihat benar
tetapi menghasilkan kerugian. Contoh paling berbahaya: fee bernilai
negatif membuat saldo bertambah tanpa ada order yang menghasilkan uang,
dan itu tidak akan pernah terlihat di laporan PnL.
"""
import itertools
import unittest

from analysis.direction_ensemble import aggregate
from analysis.volatility import assess_volatility_gate, atr_1m_from_candles
from core.config import EnsembleConfig, get_config
from trading.risk_manager import RiskManager


class TestFeeNeverNegative(unittest.TestCase):
    """
    Fee harus non-negatif tanpa syarat.

    Fee negatif berarti "biaya" yang menambah saldo.
    """

    def setUp(self):
        self.rm = RiskManager(10_000.0)

    def test_negative_quantity_gives_positive_fee(self):
        self.assertGreaterEqual(self.rm.calculate_fee(-5, 100), 0.0)

    def test_negative_price_gives_positive_fee(self):
        self.assertGreaterEqual(self.rm.calculate_fee(1, -100), 0.0)

    def test_both_negative(self):
        self.assertGreaterEqual(self.rm.calculate_fee(-5, -100), 0.0)

    def test_zero_values_are_zero(self):
        self.assertEqual(self.rm.calculate_fee(0, 100), 0.0)
        self.assertEqual(self.rm.calculate_fee(1, 0), 0.0)

    def test_fee_is_symmetric(self):
        """Fee harus sama untuk nilai mutlak yang sama."""
        self.assertEqual(self.rm.calculate_fee(2, 100),
                         self.rm.calculate_fee(-2, 100))
        self.assertEqual(self.rm.calculate_fee(2, 100),
                         self.rm.calculate_fee(2, -100))


class TestSizingEdges(unittest.TestCase):
    def setUp(self):
        self.rm = RiskManager(10_000.0)

    def test_zero_leverage_raises(self):
        with self.assertRaises(ZeroDivisionError):
            self.rm.calculate_position_size(10_000, 100.0, 99.0, 0.005, 0)

    def test_zero_stop_distance_is_finite(self):
        r = self.rm.calculate_position_size(10_000, 100.0, 100.0, 0.005, 10)
        self.assertGreaterEqual(r["quantity"], 0)


class TestPnlSymmetry(unittest.TestCase):
    def setUp(self):
        self.rm = RiskManager(10_000.0)

    def test_long_and_short_move_opposite(self):
        up = self.rm.calculate_pnl("LONG", 100.0, 101.0, 2.0)
        down = self.rm.calculate_pnl("SHORT", 100.0, 101.0, 2.0)
        self.assertGreater(up["pnl"], 0)
        self.assertLess(down["pnl"], 0)
        self.assertAlmostEqual(up["pnl"] + down["pnl"], 0.0, places=9)

    def test_liquidation_on_correct_side(self):
        long_liq = self.rm.calculate_liquidation_price(100.0, "LONG", 10)
        short_liq = self.rm.calculate_liquidation_price(100.0, "SHORT", 10)
        self.assertLess(long_liq, 100.0)
        self.assertGreater(short_liq, 100.0)


class TestDailyBreaker(unittest.TestCase):
    """Penyebut daily-loss harus modal awal, bukan saldo berjalan."""

    def setUp(self):
        self.rm = RiskManager(10_000.0)

    def test_under_limit_allowed(self):
        r = self.rm.validate_trade(10_000, 100, 1, daily_pnl=-600,
                                   peak_balance=10_000, equity=9_400)
        self.assertTrue(r["allowed"], str(r["reasons"]))

    def test_over_limit_blocked(self):
        r = self.rm.validate_trade(10_000, 100, 1, daily_pnl=-1_500,
                                   peak_balance=10_000, equity=8_500)
        self.assertFalse(r["allowed"])


class TestAtrDataSufficiency(unittest.TestCase):
    def test_needs_more_candles_than_period(self):
        """ATR Wilder memakai `prev_close`, jadi N candle hanya
        menghasilkan N-1 true range."""
        candles = [{"timestamp": i, "high": 100 + i, "low": 99 + i,
                    "close": 99.5 + i} for i in range(20)]
        self.assertIsNotNone(atr_1m_from_candles(candles, period=14))
        self.assertIsNone(atr_1m_from_candles(candles[:14], period=14))


class TestEnsembleExtremes(unittest.TestCase):
    def setUp(self):
        self.ec = EnsembleConfig()

    def test_empty_is_neutral(self):
        self.assertEqual(aggregate([], self.ec)["prob_long"], 0.5)

    def test_all_abstained_is_neutral(self):
        out = aggregate([{"agent": "orderflow", "direction": "LONG",
                          "confidence": 0.9, "abstained": True}], self.ec)
        self.assertEqual(out["prob_long"], 0.5)
        self.assertEqual(out["n_abstained"], 1)

    def test_probabilities_always_clamped(self):
        """Tidak ada kombinasi input yang boleh menghasilkan 0.0 atau 1.0."""
        pairs = list(itertools.product(["LONG", "SHORT", "NEUTRAL"],
                                       [0.0, 0.5, 1.0]))
        for combo in itertools.product(pairs, repeat=3):
            vs = [{"agent": a, "direction": combo[i][0],
                   "confidence": combo[i][1]}
                  for i, a in enumerate(["orderflow", "momentum",
                                         "technical"])]
            p = aggregate(vs, self.ec)["prob_long"]
            self.assertGreaterEqual(p, 0.02, str(combo))
            self.assertLessEqual(p, 0.98, str(combo))


class TestVolatilityGate(unittest.TestCase):
    def setUp(self):
        self.cfg = get_config()

    def test_tp_below_roundtrip_fee_blocked(self):
        """Kalau TP <= fee, setiap trade pasti merugi apa pun arahnya."""
        rt = self.cfg.fees.taker * 2
        self.assertIsNotNone(
            assess_volatility_gate("BTC", 0.0025, rt - 0.0001, self.cfg))

    def test_reasonable_tp_passes(self):
        self.assertIsNone(
            assess_volatility_gate("BTC", 0.0025, 0.01, self.cfg))


class TestRuleValidation(unittest.TestCase):
    def test_non_finite_rejected(self):
        """
        NaN dan inf harus ditolak.

        Perbandingan dengan NaN selalu False, jadi batas yang berisi NaN
        terlihat ada tapi tidak pernah menyala.
        """
        from trading.live.console import validate_rule

        for bad in ("NaN", "inf", "-inf", "Infinity"):
            value, err = validate_rule("x", bad, "float", 0.0, 100.0)
            self.assertIsNone(value, bad)
            self.assertTrue(err, bad)

    def test_valid_value_has_no_error(self):
        from trading.live.console import validate_rule

        value, err = validate_rule("x", "50", "float", 0.0, 100.0)
        self.assertIsNone(err)
        self.assertEqual(value, 50.0)

