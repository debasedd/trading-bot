"""
ml/predictor.py — Load model dan prediksi real-time.

Wrapper tipis di atas MLSignalGenerator (analysis/ml_signals.py).
File ini untuk penggunaan standalone jika perlu.
"""

from typing import Dict, Optional
from pathlib import Path

from analysis.ml_signals import MLSignalGenerator
from core.logger import get_logger

logger = get_logger("ml_predictor")


class Predictor:
    """Wrapper untuk prediksi ML real-time."""

    def __init__(self):
        self.generator = MLSignalGenerator()

    async def initialize(self):
        """Muat model."""
        await self.generator.initialize()
        logger.info(f"Predictor siap (model loaded: {self.generator._model_loaded})")

    def predict(
        self,
        technical_signals: Dict,
        sentiment_score: float = 0.0,
        df=None,
    ) -> Dict:
        """
        Prediksi sinyal trading.

        Returns:
            {"action": str, "confidence": float, "probabilities": dict, "method": str}
        """
        return self.generator.predict(technical_signals, sentiment_score, df)

    @property
    def model_available(self) -> bool:
        return self.generator._model_loaded
