"""
tests/test_probability_engine.py — Pengujian Unit untuk Quantitative Probability Engine.
"""

import unittest
import pandas as pd
import numpy as np
from analysis.probability_engine import (
    calculate_order_flow_imbalance,
    calculate_technical_zscore,
    calculate_realized_volatility,
    probability_engine,
    norm_cdf,
)


class TestProbabilityEngine(unittest.TestCase):
    """Test suite untuk probabilitas Bayesian & difusi kuantitatif."""

    def test_norm_cdf(self):
        """Uji fungsi distribusi kumulatif normal standar."""
        self.assertAlmostEqual(norm_cdf(0.0), 0.5, places=4)
        self.assertAlmostEqual(norm_cdf(1.96), 0.975, places=2)
        self.assertAlmostEqual(norm_cdf(-1.96), 0.025, places=2)

    def test_order_flow_imbalance(self):
        """Uji perhitungan Order Flow Imbalance dari L2 book."""
        # Dominasi Bid
        ob_bullish = {
            "bids": [[100.0, 10.0], [99.9, 8.0]],
            "asks": [[100.1, 2.0], [100.2, 1.0]],
        }
        ofi, spread = calculate_order_flow_imbalance(ob_bullish)
        self.assertGreater(ofi, 0.5)
        self.assertGreater(spread, 0.0)

        # Dominasi Ask
        ob_bearish = {
            "bids": [[100.0, 1.0]],
            "asks": [[100.1, 9.0]],
        }
        ofi_bear, _ = calculate_order_flow_imbalance(ob_bearish)
        self.assertLess(ofi_bear, -0.5)

    def test_technical_zscore(self):
        """Uji Z-score indikator teknikal."""
        indicators_bull = {
            "rsi": 65.0,
            "macd_hist": 2.5,
            "atr": 1.0,
            "ema_short": 105.0,
            "ema_long": 100.0,
            "bb_lower": 95.0,
            "bb_upper": 110.0,
        }
        z = calculate_technical_zscore(indicators_bull, current_price=106.0)
        self.assertGreater(z, 0.2)

    def test_composite_probability_bayesian(self):
        """Uji integrasi Bayesian multi-faktor."""
        res = probability_engine.compute_composite_probability(
            indicators={"rsi": 62.0, "macd_hist": 1.5, "atr": 1.0, "ema_short": 102.0, "ema_long": 100.0},
            current_price=101.5,
            sentiment_score=0.45,
            ml_prediction={"action": "BUY", "confidence": 0.82},
            order_book={"bids": [[101.4, 5.0]], "asks": [[101.6, 1.5]]},
            macro_bias="BULLISH",
        )
        self.assertIn("prob_bullish", res)
        self.assertIn("prob_bearish", res)
        self.assertIn("winner_odds", res)
        self.assertIn("loser_odds", res)
        # Sinyal bullish serentak harus menghasilkan prob_bullish > 0.60
        self.assertGreater(res["prob_bullish"], 0.60)
        self.assertAlmostEqual(res["prob_bullish"] + res["prob_bearish"], 1.0, places=3)
        self.assertAlmostEqual(res["winner_odds"] + res["loser_odds"], 1.0, places=3)

    def test_ml_action_label_variants(self):
        """Tabel signals menyimpan BULLISH/BEARISH; model ML memakai BUY/SELL/LONG/SHORT.
        Semua varian harus memetakan ke Z-score yang benar (regresi: BULLISH pernah diabaikan)."""
        bullish = ("BULLISH", "BUY", "LONG")
        bearish = ("BEARISH", "SELL", "SHORT")

        for act in bullish:
            res = probability_engine.compute_composite_probability(
                {}, 100.0, ml_prediction={"action": act, "confidence": 0.8}
            )
            self.assertGreater(res["factor_zscores"]["ml"], 0.0, f"{act} harus bullish")

        for act in bearish:
            res = probability_engine.compute_composite_probability(
                {}, 100.0, ml_prediction={"action": act, "confidence": 0.8}
            )
            self.assertLess(res["factor_zscores"]["ml"], 0.0, f"{act} harus bearish")

        neutral = probability_engine.compute_composite_probability(
            {}, 100.0, ml_prediction={"action": "HOLD", "confidence": 0.8}
        )
        self.assertEqual(neutral["factor_zscores"]["ml"], 0.0)

    def test_no_orderbook_returns_zero_spread(self):
        """Tanpa orderbook L2, engine harus melaporkan nol — bukan spread fabrikasi."""
        ofi, spread = calculate_order_flow_imbalance(None)
        self.assertEqual(ofi, 0.0)
        self.assertEqual(spread, 0.0)

    def test_diffusion_cone_monotonicity(self):
        """Uji kurva difusi Black-Scholes/CRR."""
        res = probability_engine.compute_diffusion_curve(
            prob_bullish=0.75,
            realized_vol_per_min=0.002,
            horizon_minutes=30,
            num_points=20,
        )
        self.assertEqual(len(res["time_horizons"]), 20)
        self.assertEqual(len(res["winner_probs"]), 20)
        self.assertEqual(len(res["loser_probs"]), 20)
        # Winner odds harus berada di range [0.5, 1.0] dan loser odds di [0.0, 0.5]
        for w in res["winner_probs"]:
            self.assertGreaterEqual(w, 0.50)
            self.assertLessEqual(w, 1.0)
        for l in res["loser_probs"]:
            self.assertGreaterEqual(l, 0.0)
            self.assertLessEqual(l, 0.50)

    def test_mathematical_edge_closed_trades(self):
        """Uji perhitungan edge dari daftar trades tertutup."""
        trades = [
            {"price": 100, "quantity": 1, "pnl": 10.0},
            {"price": 100, "quantity": 1, "pnl": 15.0},
            {"price": 100, "quantity": 1, "pnl": -5.0},
        ]
        edge = probability_engine.compute_mathematical_edge(trades)
        self.assertGreater(edge, 0.0)


class TestDirectionalDiffusionCurve(unittest.TestCase):
    """
    Regression test untuk bug `abs(drift)` + clip [0.50, 0.999].

    `compute_diffusion_curve` lama memakai `effective_mu = abs(drift)` lalu
    meng-clip ke >= 0.50, sehingga probabilitas bearish (0.35) menghasilkan
    kurva yang IDENTIK dengan kasus bullish (0.65). Grafik secara struktural
    tidak pernah bisa menampilkan kasus SHORT.

    Test di bawah gagal pada kode lama dan harus lulus pada
    `compute_directional_curve`.
    """

    def _curve(self, prob_long):
        return probability_engine.compute_directional_curve(
            prob_long=prob_long,
            realized_vol_per_min=0.0018,
            horizon_minutes=30,
            num_points=60,
        )

    def test_bearish_curve_declines(self):
        """
        P(LONG)=0.35 harus menghasilkan kurva LONG yang MENURUN.

        Ini guards utama dari arah LONG/SHORT.
        """
        res = self._curve(0.35)
        longs = res["long_probs"]
        self.assertLess(
            longs[-1], longs[0],
            "kurva LONG untuk pembacaan bearish harus menurun, bukan naik",
        )
        self.assertLess(longs[-1], 0.5)

    def test_bullish_curve_rises(self):
        res = self._curve(0.65)
        longs = res["long_probs"]
        self.assertGreater(longs[-1], longs[0])
        self.assertGreater(longs[-1], 0.5)

    def test_curves_are_complementary(self):
        """P(LONG) + P(SHORT) == 1 di setiap titik horizon."""
        for p in (0.15, 0.35, 0.50, 0.65, 0.85):
            res = self._curve(p)
            for lo, sh in zip(res["long_probs"], res["short_probs"]):
                self.assertAlmostEqual(lo + sh, 1.0, places=6)

    def test_bullish_and_bearish_are_mirrors(self):
        """
        P(LONG)=0.35 dan P(LONG)=0.65 harus hampir saling mencerminkan.

        Tidak bisa persis 1-x: suku `-0.5 * sigma^2` pada d2 menggeser kedua
        kasus ke arah yang sama (drag geometris), jadi sisi bearish benar
        sedikit lebih kuat daripada bullish pada sigma yang sama. Yang penting
        arahnya berlawanan dan selisihnya kecil — kalau selisihnya besar,
        ada bias tersembunyi yang akan mendorong bot ke satu sisi.
        """
        bull = self._curve(0.65)["long_probs"]
        bear = self._curve(0.35)["long_probs"]
        for i, (b, s) in enumerate(zip(bull, bear)):
            self.assertAlmostEqual(
                b + s, 1.0, places=2,
                msg=f"penyimpangan cermin terlalu besar di titik {i}",
            )

    def test_bearish_is_weaker_than_bullish_by_drag(self):
        """
        Konsekuensi fisikal `-0.5*sigma^2`: untuk sigma sama, sisi bearish
        berakhir sedikit di bawah cermin bullish. Dokumen ini mengunci
        perilaku itu supaya perubahan tak disengaja ketahuan.
        """
        bull = self._curve(0.65)["long_probs"]
        bear = self._curve(0.35)["long_probs"]
        self.assertLess(bull[-1] + bear[-1], 1.0)
        self.assertGreater(bull[-1] + bear[-1], 0.99)

    def test_bearish_short_curve_rises(self):
        """Pada kasus bearish, kurva SHORT harus naik."""
        res = self._curve(0.35)
        shorts = res["short_probs"]
        self.assertGreater(shorts[-1], shorts[0])
        self.assertGreater(shorts[-1], 0.5)

    def test_curve_stays_in_valid_range(self):
        for p in (0.01, 0.10, 0.50, 0.90, 0.99):
            res = self._curve(p)
            for lo, sh in zip(res["long_probs"], res["short_probs"]):
                self.assertGreater(lo, 0.0)
                self.assertLess(lo, 1.0)
                self.assertGreater(sh, 0.0)
                self.assertLess(sh, 1.0)

    def test_legacy_function_still_callable(self):
        """
        Shim lama harus tetap bisa dipanggil selama transisi.

        Yang diperiksa hanya availability — perilaku buggy-nya sengaja
        dipertahankan supaya call site lama tidak ikut berubah diam-diam.
        """
        res = probability_engine.compute_diffusion_curve(
            prob_bullish=0.65,
            realized_vol_per_min=0.0018,
            horizon_minutes=30,
            num_points=20,
        )
        self.assertIn("winner_probs", res)
        self.assertIn("loser_probs", res)


if __name__ == "__main__":
    unittest.main()
