"""
data/sentiment.py — Analisis sentimen lokal: VADER (cepat) + FinBERT (batch).

Dua mode:
- VADER: rule-based, <1ms per teks, untuk analisis real-time
- FinBERT: transformer-based, ~500MB model, untuk analisis batch tiap 15 menit
"""

import asyncio
from typing import Dict, List, Optional, Tuple
from core.logger import get_logger

logger = get_logger("sentiment")


class SentimentAnalyzer:
    """
    Analisis sentimen dua-lapisan:
    1. VADER — cepat, berbasis aturan, untuk setiap berita masuk
    2. FinBERT — akurat, model transformer, batch tiap N menit
    """

    def __init__(self, use_finbert: bool = True):
        self._vader = None
        self._finbert_pipeline = None
        self._use_finbert = use_finbert
        self._finbert_loaded = False

    async def initialize(self):
        """Muat model-model sentimen."""
        loop = asyncio.get_event_loop()

        # VADER — selalu dimuat (ringan)
        try:
            await loop.run_in_executor(None, self._load_vader)
            logger.info("VADER sentiment analyzer dimuat")
        except Exception as e:
            logger.error(f"Gagal memuat VADER: {e}")

        # FinBERT — opsional (berat)
        if self._use_finbert:
            try:
                await loop.run_in_executor(None, self._load_finbert)
                self._finbert_loaded = True
                logger.info("FinBERT sentiment analyzer dimuat")
            except Exception as e:
                logger.warning(f"FinBERT tidak tersedia (CPU/RAM terbatas?): {e}")
                self._finbert_loaded = False

    def _load_vader(self):
        """Muat VADER analyzer."""
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
        self._vader = SentimentIntensityAnalyzer()

    def _load_finbert(self):
        """Muat FinBERT pipeline."""
        from transformers import pipeline
        self._finbert_pipeline = pipeline(
            "sentiment-analysis",
            model="ProsusAI/finbert",
            tokenizer="ProsusAI/finbert",
            device=-1,  # CPU only
        )

    def analyze_vader(self, text: str) -> Dict:
        """
        Analisis sentimen dengan VADER.

        Returns:
            {"compound": -1.0..1.0, "pos": 0..1, "neg": 0..1, "neu": 0..1, "label": str}
        """
        if not self._vader:
            return {"compound": 0.0, "pos": 0, "neg": 0, "neu": 1, "label": "NEUTRAL"}

        scores = self._vader.polarity_scores(text)
        compound = scores["compound"]

        if compound >= 0.05:
            label = "POSITIVE"
        elif compound <= -0.05:
            label = "NEGATIVE"
        else:
            label = "NEUTRAL"

        return {
            "compound": compound,
            "pos": scores["pos"],
            "neg": scores["neg"],
            "neu": scores["neu"],
            "label": label,
        }

    async def analyze_vader_async(self, text: str) -> Dict:
        """Versi async dari analyze_vader."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.analyze_vader, text)

    def analyze_finbert(self, text: str) -> Dict:
        """
        Analisis sentimen dengan FinBERT.

        Returns:
            {"score": -1.0..1.0, "label": str, "confidence": 0..1}
        """
        if not self._finbert_loaded or not self._finbert_pipeline:
            return {"score": 0.0, "label": "NEUTRAL", "confidence": 0.0}

        try:
            # Potong teks (FinBERT max 512 token)
            truncated = text[:512]
            result = self._finbert_pipeline(truncated)[0]

            label = result["label"]  # "positive", "negative", "neutral"
            confidence = result["score"]

            # Konversi ke skor -1 s/d +1
            if label == "positive":
                score = confidence
            elif label == "negative":
                score = -confidence
            else:
                score = 0.0

            return {
                "score": score,
                "label": label.upper(),
                "confidence": confidence,
            }
        except Exception as e:
            logger.error(f"FinBERT error: {e}")
            return {"score": 0.0, "label": "NEUTRAL", "confidence": 0.0}

    async def analyze_finbert_async(self, text: str) -> Dict:
        """Versi async dari analyze_finbert."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.analyze_finbert, text)

    async def analyze_finbert_batch(self, texts: List[str]) -> List[Dict]:
        """
        Analisis batch FinBERT — untuk efisiensi.
        Jalankan tiap 15 menit pada berita yang terkumpul.
        """
        if not self._finbert_loaded or not self._finbert_pipeline:
            return [{"score": 0.0, "label": "NEUTRAL", "confidence": 0.0} for _ in texts]

        loop = asyncio.get_event_loop()

        try:
            def _batch():
                truncated = [t[:512] for t in texts]
                results = self._finbert_pipeline(truncated)
                processed = []
                for r in results:
                    label = r["label"]
                    conf = r["score"]
                    if label == "positive":
                        score = conf
                    elif label == "negative":
                        score = -conf
                    else:
                        score = 0.0
                    processed.append({
                        "score": score,
                        "label": label.upper(),
                        "confidence": conf,
                    })
                return processed

            return await loop.run_in_executor(None, _batch)

        except Exception as e:
            logger.error(f"FinBERT batch error: {e}")
            return [{"score": 0.0, "label": "NEUTRAL", "confidence": 0.0} for _ in texts]

    def aggregate_sentiment(self, sentiments: List[Dict]) -> Dict:
        """
        Agregasi sentimen dari banyak berita menjadi satu skor.

        Returns:
            {"avg_score": float, "label": str, "count": int, "positive": int, "negative": int}
        """
        if not sentiments:
            return {"avg_score": 0.0, "label": "NEUTRAL", "count": 0, "positive": 0, "negative": 0}

        scores = []
        pos_count = 0
        neg_count = 0

        for s in sentiments:
            score = s.get("compound", s.get("score", 0.0))
            scores.append(score)
            label = s.get("label", "NEUTRAL")
            if label == "POSITIVE":
                pos_count += 1
            elif label == "NEGATIVE":
                neg_count += 1

        avg = sum(scores) / len(scores)

        if avg >= 0.1:
            label = "POSITIVE"
        elif avg <= -0.1:
            label = "NEGATIVE"
        else:
            label = "NEUTRAL"

        return {
            "avg_score": round(avg, 4),
            "label": label,
            "count": len(sentiments),
            "positive": pos_count,
            "negative": neg_count,
        }
