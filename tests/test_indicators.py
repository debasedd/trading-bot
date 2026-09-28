"""
tests/test_indicators.py — Pengujian kalkulasi indikator teknikal (pandas-ta) dan sinyal.
"""

import unittest
import numpy as np
import pandas as pd

from analysis.technical import TechnicalAnalyzer


class TestTechnicalIndicators(unittest.TestCase):
    """Pengujian indikator teknikal dan pembuatan sinyal."""

    def setUp(self):
        self.analyzer = TechnicalAnalyzer()

        # Buat data sintetis 100 candle
        np.random.seed(42)
        n = 100
        timestamps = [1600000000000 + i * 300000 for i in range(n)]
        close_prices = 50000.0 + np.cumsum(np.random.randn(n) * 200.0)
        open_prices = close_prices + np.random.randn(n) * 50.0
        high_prices = np.maximum(open_prices, close_prices) + np.random.rand(n) * 100.0
        low_prices = np.minimum(open_prices, close_prices) - np.random.rand(n) * 100.0
        volumes = np.random.rand(n) * 50.0 + 10.0

        ohlcv = [
            [ts, o, h, l, c, v]
            for ts, o, h, l, c, v in zip(
                timestamps, open_prices, high_prices, low_prices, close_prices, volumes
            )
        ]
        self.df = TechnicalAnalyzer.ohlcv_to_dataframe(ohlcv)

    def test_ohlcv_dataframe_conversion(self):
        """Uji konversi list OHLCV ke DataFrame pandas."""
        self.assertEqual(len(self.df), 100)
        self.assertIn("timestamp", self.df.columns)
        self.assertIn("open", self.df.columns)
        self.assertIn("high", self.df.columns)
        self.assertIn("low", self.df.columns)
        self.assertIn("close", self.df.columns)
        self.assertIn("volume", self.df.columns)

    def test_calculate_indicators(self):
        """Uji penambahan kolom indikator: RSI, MACD, Bollinger, EMA, ATR."""
        df_ind = self.analyzer.calculate_indicators(self.df.copy())

        self.assertIn("rsi", df_ind.columns)
        self.assertIn("macd", df_ind.columns)
        self.assertIn("macd_hist", df_ind.columns)
        self.assertIn("macd_signal", df_ind.columns)
        self.assertIn("bb_lower", df_ind.columns)
        self.assertIn("bb_mid", df_ind.columns)
        self.assertIn("bb_upper", df_ind.columns)
        self.assertIn("ema_short", df_ind.columns)
        self.assertIn("ema_long", df_ind.columns)
        self.assertIn("atr", df_ind.columns)
        self.assertIn("vol_sma", df_ind.columns)

        # Nilai baris terakhir harus bukan NaN
        last = df_ind.iloc[-1]
        self.assertFalse(pd.isna(last["rsi"]))
        self.assertFalse(pd.isna(last["macd_hist"]))
        self.assertFalse(pd.isna(last["bb_upper"]))
        self.assertFalse(pd.isna(last["ema_short"]))

    def test_generate_signals(self):
        """Uji output sinyal teknikal (direction, confidence, signals dict)."""
        df_ind = self.analyzer.calculate_indicators(self.df.copy())
        signals = self.analyzer.generate_signals(df_ind)

        self.assertIn("direction", signals)
        self.assertIn(signals["direction"], ["BULLISH", "BEARISH", "NEUTRAL"])
        self.assertIn("confidence", signals)
        self.assertGreaterEqual(signals["confidence"], 0.0)
        self.assertLessEqual(signals["confidence"], 1.0)
        self.assertIn("signals", signals)
        self.assertIn("rsi", signals["signals"])
        self.assertIn("macd", signals["signals"])


if __name__ == "__main__":
    unittest.main()
