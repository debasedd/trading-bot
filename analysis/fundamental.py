"""
analysis/fundamental.py — Pemrosesan data makro + sentimen menjadi bias pasar.
"""

from typing import Dict, List, Optional
from datetime import datetime
from core.logger import get_logger

logger = get_logger("fundamental")


class FundamentalAnalyzer:
    """
    Menggabungkan data makroekonomi, sentimen berita, dan konteks pasar
    menjadi penilaian fundamental keseluruhan.
    """

    def __init__(self):
        self._macro_context: Dict = {}
        self._sentiment_aggregate: Dict = {}
        self._calendar_events: List[dict] = []

    def update_macro(self, macro_context: Dict):
        """Update konteks makroekonomi dari MacroFetcher."""
        self._macro_context = macro_context

    def update_sentiment(self, sentiment_aggregate: Dict):
        """Update sentimen agregat dari SentimentAnalyzer."""
        self._sentiment_aggregate = sentiment_aggregate

    def update_calendar(self, events: List[dict]):
        """Update kalender ekonomi."""
        self._calendar_events = events

    def analyze(self) -> Dict:
        """
        Analisis fundamental lengkap.

        Returns:
            {
                "direction": "BULLISH" | "BEARISH" | "NEUTRAL",
                "confidence": 0.0 - 1.0,
                "macro_bias": str,
                "sentiment_bias": str,
                "risk_level": str,
                "summary": str,
                "factors": [str],
            }
        """
        factors = []
        bullish_score = 0
        bearish_score = 0

        # --- Faktor Makro ---
        macro = self._macro_context
        if macro:
            macro_bias = macro.get("bias", "NEUTRAL")
            if macro_bias == "BULLISH":
                bullish_score += 2
            elif macro_bias == "BEARISH":
                bearish_score += 2

            factors.extend(macro.get("factors", []))
            risk_level = macro.get("risk_level", "MEDIUM")
        else:
            macro_bias = "NEUTRAL"
            risk_level = "MEDIUM"
            factors.append("Data makro belum tersedia")

        # --- Faktor Sentimen ---
        sentiment = self._sentiment_aggregate
        if sentiment and sentiment.get("count", 0) > 0:
            avg_score = sentiment.get("avg_score", 0)
            label = sentiment.get("label", "NEUTRAL")

            if avg_score > 0.2:
                bullish_score += 2
                factors.append(f"Sentimen berita positif kuat ({avg_score:+.3f})")
            elif avg_score > 0.05:
                bullish_score += 1
                factors.append(f"Sentimen berita sedikit positif ({avg_score:+.3f})")
            elif avg_score < -0.2:
                bearish_score += 2
                factors.append(f"Sentimen berita negatif kuat ({avg_score:+.3f})")
            elif avg_score < -0.05:
                bearish_score += 1
                factors.append(f"Sentimen berita sedikit negatif ({avg_score:+.3f})")
            else:
                factors.append(f"Sentimen berita netral ({avg_score:+.3f})")

            sentiment_bias = label
        else:
            sentiment_bias = "NEUTRAL"
            factors.append("Data sentimen belum tersedia")

        # --- Faktor Kalender ---
        if self._calendar_events:
            high_impact = [e for e in self._calendar_events if e.get("impact") == "High"]
            if len(high_impact) >= 3:
                bearish_score += 1  # Banyak event = risiko tinggi
                risk_level = "HIGH"
                factors.append(f"{len(high_impact)} event ekonomi berdampak tinggi — volatilitas meningkat")

        # --- Tentukan arah ---
        net = bullish_score - bearish_score

        if net >= 2:
            direction = "BULLISH"
            confidence = min(net / 6, 1.0)
        elif net <= -2:
            direction = "BEARISH"
            confidence = min(abs(net) / 6, 1.0)
        else:
            direction = "NEUTRAL"
            confidence = 0.2

        summary = (
            f"Fundamental {direction} (confidence {confidence:.0%}) — "
            f"Makro: {macro_bias}, Sentimen: {sentiment_bias}, "
            f"Risiko: {risk_level}"
        )

        return {
            "direction": direction,
            "confidence": round(confidence, 3),
            "macro_bias": macro_bias,
            "sentiment_bias": sentiment_bias,
            "risk_level": risk_level,
            "summary": summary,
            "factors": factors,
            "bullish_score": bullish_score,
            "bearish_score": bearish_score,
        }

    def should_reduce_risk(self) -> bool:
        """Apakah kondisi fundamental menyarankan pengurangan risiko?"""
        analysis = self.analyze()
        return (
            analysis["risk_level"] == "HIGH"
            or analysis["direction"] == "BEARISH" and analysis["confidence"] > 0.5
        )

    def get_suggested_leverage(self, default: int = 5) -> int:
        """Saran leverage berdasarkan kondisi fundamental."""
        analysis = self.analyze()
        risk = analysis["risk_level"]

        if risk == "HIGH":
            return max(1, default // 3)
        elif risk == "MEDIUM":
            return max(1, default // 2)
        else:
            return default
