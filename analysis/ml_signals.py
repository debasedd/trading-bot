"""
analysis/ml_signals.py — RandomForest classifier untuk prediksi sinyal trading.

Input: fitur teknikal + sentimen
Output: probabilitas arah (LONG/SHORT/HOLD)
"""

import asyncio
import json
import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Tuple
from pathlib import Path

from core.config import get_config
from core.logger import get_logger

logger = get_logger("ml_signals")

MODEL_DIR = Path("ml/models")


class MLSignalGenerator:
    """
    Model ML untuk konfirmasi sinyal trading.

    Menggunakan RandomForest classifier:
    - Input: RSI, MACD histogram, BB position, EMA cross, volume ratio, sentiment
    - Output: probabilitas LONG, SHORT, atau HOLD

    Model dilatih dari data historis secara offline (trainer.py).
    Jika model belum dilatih, fallback ke rule-based scoring.
    """

    def __init__(self):
        self._model = None
        self._feature_names: List[str] = [
            "rsi", "macd_hist", "bb_position", "ema_trend",
            "volume_ratio", "sentiment_score", "atr_pct",
        ]
        self._model_loaded = False

    async def initialize(self):
        """Muat model jika tersedia."""
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, self._load_model)
        except Exception as e:
            logger.info(f"Model ML belum tersedia, pakai rule-based: {e}")
            self._model_loaded = False

    def _load_model(self):
        """Muat model dari file .pkl."""
        model_path = MODEL_DIR / "signal_model.pkl"
        if not model_path.exists():
            raise FileNotFoundError("Model belum dilatih")

        import joblib
        self._model = joblib.load(str(model_path))
        self._model_loaded = True
        logger.info("Model ML dimuat dari signal_model.pkl")

    def extract_features(
        self,
        technical_signals: Dict,
        sentiment_score: float = 0.0,
        df: pd.DataFrame = None,
    ) -> Optional[np.ndarray]:
        """
        Ekstrak fitur dari sinyal teknikal + sentimen.

        Returns:
            numpy array fitur, atau None jika data tidak cukup.
        """
        signals = technical_signals.get("signals", {})

        # RSI (0-100, normalized ke 0-1)
        rsi_data = signals.get("rsi", {})
        rsi = rsi_data.get("value", 50) / 100.0 if isinstance(rsi_data.get("value"), (int, float)) else 0.5

        # MACD histogram (normalized)
        macd_data = signals.get("macd", {})
        macd_val = macd_data.get("value", 0)
        macd_hist = float(macd_val) if isinstance(macd_val, (int, float)) else 0.0
        # Normalize: clip ke [-1, 1] berdasarkan proporsi harga
        macd_hist_norm = np.clip(macd_hist / 100, -1, 1) if abs(macd_hist) > 0 else 0

        # Bollinger Band position (0-1)
        bb_data = signals.get("bollinger", {})
        bb_val = bb_data.get("value", "0.5")
        try:
            bb_position = float(bb_val)
        except (ValueError, TypeError):
            if bb_val == "LOWER_BAND":
                bb_position = 0.0
            elif bb_val == "UPPER_BAND":
                bb_position = 1.0
            else:
                bb_position = 0.5

        # EMA trend (-1 bearish, 0 neutral, 1 bullish)
        ema_data = signals.get("ema_cross", {})
        ema_signal = ema_data.get("signal", "NEUTRAL")
        if "BULLISH" in ema_signal:
            ema_trend = 1.0 if "CROSSOVER" in ema_signal else 0.5
        elif "BEARISH" in ema_signal:
            ema_trend = -1.0 if "CROSSOVER" in ema_signal else -0.5
        else:
            ema_trend = 0.0

        # Volume ratio
        vol_data = signals.get("volume", {})
        vol_val = vol_data.get("value", "1.0x")
        try:
            volume_ratio = float(str(vol_val).replace("x", ""))
        except (ValueError, TypeError):
            volume_ratio = 1.0
        volume_ratio = min(volume_ratio / 3.0, 1.0)  # Normalize

        # Sentiment score (-1 to 1)
        sent_norm = np.clip(sentiment_score, -1, 1)

        # ATR percentage (volatilitas)
        atr_pct = 0.5
        if df is not None and "atr" in df.columns and "close" in df.columns:
            last = df.iloc[-1]
            if pd.notna(last.get("atr")) and last["close"] > 0:
                atr_pct = min(float(last["atr"] / last["close"]), 0.1) * 10  # 0-1

        features = np.array([
            rsi, macd_hist_norm, bb_position, ema_trend,
            volume_ratio, sent_norm, atr_pct,
        ]).reshape(1, -1)

        return features

    def predict(
        self,
        technical_signals: Dict,
        sentiment_score: float = 0.0,
        df: pd.DataFrame = None,
    ) -> Dict:
        """
        Prediksi sinyal trading.

        Returns:
            {
                "action": "LONG" | "SHORT" | "HOLD",
                "confidence": 0.0 - 1.0,
                "probabilities": {"LONG": float, "SHORT": float, "HOLD": float},
                "method": "ML" | "RULE_BASED",
            }
        """
        features = self.extract_features(technical_signals, sentiment_score, df)

        if features is None:
            return {
                "action": "HOLD",
                "confidence": 0.0,
                "probabilities": {"LONG": 0.33, "SHORT": 0.33, "HOLD": 0.34},
                "method": "NONE",
            }

        if self._model_loaded and self._model is not None:
            return self._predict_ml(features)
        else:
            return self._predict_rule_based(features)

    def _predict_ml(self, features: np.ndarray) -> Dict:
        """Prediksi dengan model ML."""
        try:
            proba = self._model.predict_proba(features)[0]
            classes = self._model.classes_

            prob_dict = {str(c): round(float(p), 4) for c, p in zip(classes, proba)}
            action = str(classes[np.argmax(proba)])
            confidence = float(np.max(proba))

            return {
                "action": action,
                "confidence": confidence,
                "probabilities": prob_dict,
                "method": "ML",
            }
        except Exception as e:
            logger.error(f"Prediksi ML gagal: {e}")
            return self._predict_rule_based(features)

    def _predict_rule_based(self, features: np.ndarray) -> Dict:
        """
        Fallback rule-based scoring ketika model ML belum tersedia.

        Fitur: [rsi, macd_hist, bb_pos, ema_trend, vol_ratio, sentiment, atr_pct]
        """
        rsi, macd, bb, ema, vol, sent, atr = features[0]

        score = 0.0

        # RSI
        if rsi < 0.3:
            score += 1.5  # Oversold → bullish
        elif rsi > 0.7:
            score -= 1.5  # Overbought → bearish
        elif rsi < 0.45:
            score += 0.5
        elif rsi > 0.55:
            score -= 0.5

        # MACD
        score += macd * 2.0

        # Bollinger
        if bb < 0.2:
            score += 1.0
        elif bb > 0.8:
            score -= 1.0

        # EMA trend
        score += ema * 1.5

        # Sentiment
        score += sent * 1.0

        # Normalize ke probabilitas
        sigmoid = lambda x: 1 / (1 + np.exp(-x))
        p_long = sigmoid(score)
        p_short = sigmoid(-score)
        p_hold = 1 - abs(p_long - p_short)

        # Normalize
        total = p_long + p_short + p_hold
        p_long /= total
        p_short /= total
        p_hold /= total

        # Tentukan aksi
        threshold = 0.45
        if p_long > threshold and p_long > p_short:
            action = "LONG"
            confidence = p_long
        elif p_short > threshold and p_short > p_long:
            action = "SHORT"
            confidence = p_short
        else:
            action = "HOLD"
            confidence = p_hold

        return {
            "action": action,
            "confidence": round(float(confidence), 4),
            "probabilities": {
                "LONG": round(float(p_long), 4),
                "SHORT": round(float(p_short), 4),
                "HOLD": round(float(p_hold), 4),
            },
            "method": "RULE_BASED",
        }
