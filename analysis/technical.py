"""
analysis/technical.py — Indikator teknikal via pandas-ta.

Menghitung RSI, MACD, Bollinger Bands, EMA, dan menghasilkan sinyal.
Ditambah indikator scalping: RSI 7, EMA 3/5, Momentum ROC.
"""

import pandas as pd
import pandas_ta as ta
from typing import Dict, List, Optional
from core.config import get_config
from core.logger import get_logger

logger = get_logger("technical")


class TechnicalAnalyzer:
    """
    Analisis teknikal dari data OHLCV.

    Menghitung indikator dan menghasilkan sinyal:
    - RSI (14 + 7 untuk scalping)
    - MACD (Moving Average Convergence Divergence)
    - Bollinger Bands
    - EMA crossover (9/21 + 3/5 untuk scalping)
    - Volume analysis
    - Momentum ROC (scalping)
    """

    def __init__(self):
        cfg = get_config().indicators
        self.rsi_period = cfg.rsi_period
        self.macd_fast = cfg.macd_fast
        self.macd_slow = cfg.macd_slow
        self.macd_signal = cfg.macd_signal
        self.bb_period = cfg.bollinger_period
        self.bb_std = cfg.bollinger_std
        self.ema_short = cfg.ema_short
        self.ema_long = cfg.ema_long

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Hitung semua indikator teknikal pada DataFrame OHLCV.

        Args:
            df: DataFrame dengan kolom [timestamp, open, high, low, close, volume]

        Returns:
            DataFrame dengan kolom indikator tambahan.
        """
        if len(df) < self.macd_slow + self.macd_signal:
            logger.warning(f"Data terlalu sedikit ({len(df)} candles), butuh minimal {self.macd_slow + self.macd_signal}")
            return df

        # RSI standard (14)
        df["rsi"] = ta.rsi(df["close"], length=self.rsi_period)

        # RSI cepat (7) untuk scalping
        df["rsi_fast"] = ta.rsi(df["close"], length=7)

        # MACD
        macd = ta.macd(df["close"], fast=self.macd_fast, slow=self.macd_slow, signal=self.macd_signal)
        if macd is not None:
            df["macd"] = macd.iloc[:, 0]        # MACD line
            df["macd_hist"] = macd.iloc[:, 1]   # Histogram
            df["macd_signal"] = macd.iloc[:, 2] # Signal line

        # Bollinger Bands
        bb = ta.bbands(df["close"], length=self.bb_period, std=self.bb_std)
        if bb is not None:
            df["bb_lower"] = bb.iloc[:, 0]
            df["bb_mid"] = bb.iloc[:, 1]
            df["bb_upper"] = bb.iloc[:, 2]

        # EMA standard
        df["ema_short"] = ta.ema(df["close"], length=self.ema_short)
        df["ema_long"] = ta.ema(df["close"], length=self.ema_long)

        # EMA ultra-pendek untuk scalping (3, 5)
        df["ema_3"] = ta.ema(df["close"], length=3)
        df["ema_5"] = ta.ema(df["close"], length=5)

        # Momentum ROC (Rate of Change) 5 bar
        df["roc_5"] = ta.roc(df["close"], length=5)

        # Volume SMA (20 periode)
        df["vol_sma"] = ta.sma(df["volume"], length=20)

        # ATR (Average True Range) untuk volatilitas
        df["atr"] = ta.atr(df["high"], df["low"], df["close"], length=14)

        return df

    def generate_signals(self, df: pd.DataFrame) -> Dict:
        """
        Hasilkan sinyal trading dari indikator.

        Returns:
            {
                "direction": "BULLISH" | "BEARISH" | "NEUTRAL",
                "confidence": 0.0 - 1.0,
                "signals": {...},
                "summary": str,
            }
        """
        if len(df) < 2 or "rsi" not in df.columns:
            return {
                "direction": "NEUTRAL",
                "confidence": 0.0,
                "signals": {},
                "summary": "Data indikator belum cukup",
            }

        last = df.iloc[-1]
        prev = df.iloc[-2]
        signals = {}
        bullish_count = 0
        bearish_count = 0
        total_signals = 0

        # --- RSI Standard ---
        rsi_val = last.get("rsi", 50)
        if pd.notna(rsi_val):
            total_signals += 1
            if rsi_val < 30:
                signals["rsi"] = {"value": round(rsi_val, 2), "signal": "OVERSOLD (Bullish)"}
                bullish_count += 1
            elif rsi_val > 70:
                signals["rsi"] = {"value": round(rsi_val, 2), "signal": "OVERBOUGHT (Bearish)"}
                bearish_count += 1
            elif rsi_val < 45:
                signals["rsi"] = {"value": round(rsi_val, 2), "signal": "WEAK_BULLISH"}
                bullish_count += 0.5
            elif rsi_val > 55:
                signals["rsi"] = {"value": round(rsi_val, 2), "signal": "WEAK_BEARISH"}
                bearish_count += 0.5
            else:
                signals["rsi"] = {"value": round(rsi_val, 2), "signal": "NEUTRAL"}

        # --- RSI Fast (7) untuk scalping ---
        rsi_fast = last.get("rsi_fast", 50)
        if pd.notna(rsi_fast):
            total_signals += 1
            if rsi_fast < 25:
                signals["rsi_fast"] = {"value": round(rsi_fast, 2), "signal": "EXTREME_OVERSOLD"}
                bullish_count += 1.5
            elif rsi_fast > 75:
                signals["rsi_fast"] = {"value": round(rsi_fast, 2), "signal": "EXTREME_OVERBOUGHT"}
                bearish_count += 1.5
            elif rsi_fast < 40:
                signals["rsi_fast"] = {"value": round(rsi_fast, 2), "signal": "WEAK_BULLISH"}
                bullish_count += 0.3
            elif rsi_fast > 60:
                signals["rsi_fast"] = {"value": round(rsi_fast, 2), "signal": "WEAK_BEARISH"}
                bearish_count += 0.3
            else:
                signals["rsi_fast"] = {"value": round(rsi_fast, 2), "signal": "NEUTRAL"}

        # --- MACD ---
        macd_val = last.get("macd", 0)
        macd_sig = last.get("macd_signal", 0)
        macd_hist = last.get("macd_hist", 0)
        prev_macd_hist = prev.get("macd_hist", 0)

        if pd.notna(macd_val) and pd.notna(macd_sig):
            total_signals += 1
            if macd_hist > 0 and prev_macd_hist <= 0:
                signals["macd"] = {"value": round(float(macd_hist), 4), "signal": "BULLISH_CROSSOVER"}
                bullish_count += 1.5
            elif macd_hist < 0 and prev_macd_hist >= 0:
                signals["macd"] = {"value": round(float(macd_hist), 4), "signal": "BEARISH_CROSSOVER"}
                bearish_count += 1.5
            elif macd_hist > 0:
                signals["macd"] = {"value": round(float(macd_hist), 4), "signal": "BULLISH"}
                bullish_count += 0.5
            else:
                signals["macd"] = {"value": round(float(macd_hist), 4), "signal": "BEARISH"}
                bearish_count += 0.5

        # --- Bollinger Bands ---
        close = last.get("close", 0)
        bb_lower = last.get("bb_lower", 0)
        bb_upper = last.get("bb_upper", 0)
        bb_mid = last.get("bb_mid", 0)

        if pd.notna(bb_lower) and pd.notna(bb_upper) and close > 0:
            total_signals += 1
            if close <= bb_lower:
                signals["bollinger"] = {"value": "LOWER_BAND", "signal": "OVERSOLD (Bullish)"}
                bullish_count += 1
            elif close >= bb_upper:
                signals["bollinger"] = {"value": "UPPER_BAND", "signal": "OVERBOUGHT (Bearish)"}
                bearish_count += 1
            else:
                bb_pos = (close - bb_lower) / (bb_upper - bb_lower) if (bb_upper - bb_lower) > 0 else 0.5
                signals["bollinger"] = {"value": f"{bb_pos:.2f}", "signal": "WITHIN_BANDS"}

        # --- EMA Crossover Standard ---
        ema_s = last.get("ema_short", 0)
        ema_l = last.get("ema_long", 0)
        prev_ema_s = prev.get("ema_short", 0)
        prev_ema_l = prev.get("ema_long", 0)

        if pd.notna(ema_s) and pd.notna(ema_l):
            total_signals += 1
            if ema_s > ema_l and prev_ema_s <= prev_ema_l:
                signals["ema_cross"] = {"value": "GOLDEN_CROSS", "signal": "BULLISH_CROSSOVER"}
                bullish_count += 1.5
            elif ema_s < ema_l and prev_ema_s >= prev_ema_l:
                signals["ema_cross"] = {"value": "DEATH_CROSS", "signal": "BEARISH_CROSSOVER"}
                bearish_count += 1.5
            elif ema_s > ema_l:
                signals["ema_cross"] = {"value": "ABOVE", "signal": "BULLISH"}
                bullish_count += 0.5
            else:
                signals["ema_cross"] = {"value": "BELOW", "signal": "BEARISH"}
                bearish_count += 0.5

        # --- EMA Fast Crossover (3/5) untuk scalping ---
        ema3 = last.get("ema_3", 0)
        ema5 = last.get("ema_5", 0)
        prev_ema3 = prev.get("ema_3", 0)
        prev_ema5 = prev.get("ema_5", 0)

        if pd.notna(ema3) and pd.notna(ema5):
            total_signals += 1
            if ema3 > ema5 and prev_ema3 <= prev_ema5:
                signals["ema_fast_cross"] = {"value": "FAST_GOLDEN", "signal": "BULLISH_CROSSOVER"}
                bullish_count += 1.0
            elif ema3 < ema5 and prev_ema3 >= prev_ema5:
                signals["ema_fast_cross"] = {"value": "FAST_DEATH", "signal": "BEARISH_CROSSOVER"}
                bearish_count += 1.0
            elif ema3 > ema5:
                signals["ema_fast_cross"] = {"value": "FAST_ABOVE", "signal": "BULLISH"}
                bullish_count += 0.3
            else:
                signals["ema_fast_cross"] = {"value": "FAST_BELOW", "signal": "BEARISH"}
                bearish_count += 0.3

        # --- Momentum ROC (5 bar) ---
        roc = last.get("roc_5", 0)
        if pd.notna(roc):
            total_signals += 1
            if roc > 0.5:
                signals["momentum"] = {"value": round(float(roc), 3), "signal": "STRONG_BULLISH"}
                bullish_count += 1.0
            elif roc > 0.1:
                signals["momentum"] = {"value": round(float(roc), 3), "signal": "BULLISH"}
                bullish_count += 0.3
            elif roc < -0.5:
                signals["momentum"] = {"value": round(float(roc), 3), "signal": "STRONG_BEARISH"}
                bearish_count += 1.0
            elif roc < -0.1:
                signals["momentum"] = {"value": round(float(roc), 3), "signal": "BEARISH"}
                bearish_count += 0.3
            else:
                signals["momentum"] = {"value": round(float(roc), 3), "signal": "NEUTRAL"}

        # --- Volume ---
        vol = last.get("volume", 0)
        vol_sma = last.get("vol_sma", 0)

        if pd.notna(vol) and pd.notna(vol_sma) and vol_sma > 0:
            total_signals += 1
            vol_ratio = vol / vol_sma
            if vol_ratio > 1.5:
                signals["volume"] = {"value": f"{vol_ratio:.2f}x", "signal": "HIGH_VOLUME"}
            elif vol_ratio < 0.5:
                signals["volume"] = {"value": f"{vol_ratio:.2f}x", "signal": "LOW_VOLUME"}
            else:
                signals["volume"] = {"value": f"{vol_ratio:.2f}x", "signal": "NORMAL"}

        # --- Tentukan arah & confidence ---
        net = bullish_count - bearish_count
        max_score = max(total_signals * 1.5, 1)

        if net > 0.5:
            direction = "BULLISH"
            confidence = min(net / max_score, 1.0)
        elif net < -0.5:
            direction = "BEARISH"
            confidence = min(abs(net) / max_score, 1.0)
        else:
            direction = "NEUTRAL"
            confidence = 0.0

        summary = (
            f"Teknikal {direction} (confidence {confidence:.0%}) — "
            f"Bullish: {bullish_count:.1f}, Bearish: {bearish_count:.1f}"
        )

        return {
            "direction": direction,
            "confidence": round(confidence, 3),
            "signals": signals,
            "summary": summary,
            "bullish_score": bullish_count,
            "bearish_score": bearish_count,
        }

    @staticmethod
    def ohlcv_to_dataframe(ohlcv_data: List[list]) -> pd.DataFrame:
        """
        Konversi data OHLCV dari ccxt ke DataFrame.

        Args:
            ohlcv_data: List of [timestamp_ms, open, high, low, close, volume]
        """
        df = pd.DataFrame(
            ohlcv_data,
            columns=["timestamp", "open", "high", "low", "close", "volume"],
        )
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        return df
