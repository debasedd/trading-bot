"""
agents/analysis_agent.py — Agen 2: Analisis makro/mikro/teknikal.

Mengolah data teknikal, makro, dan sentimen menjadi MarketContext.
"""

import json
from typing import Dict, Optional

import pandas as pd

from agents.base_agent import BaseAgent
from core.event_bus import EventBus, Channels
from data.price_feed import PriceFeed
from data.macro_fetcher import MacroFetcher
from data.sentiment import SentimentAnalyzer
from analysis.technical import TechnicalAnalyzer
from analysis.fundamental import FundamentalAnalyzer
from analysis.ml_signals import MLSignalGenerator
from database.models import Signal
from core.config import get_config
from core.logger import get_logger

logger = get_logger("analysis_agent")


class AnalysisAgent(BaseAgent):
    """
    Agen analisis pasar menyeluruh.

    Siklus:
    1. Sense: ambil data harga (OHLCV), data makro, sentimen terbaru
    2. Think: hitung indikator teknikal, analisis fundamental, prediksi ML
    3. Act: broadcast MarketContext ke event bus
    """

    def __init__(
        self,
        event_bus: EventBus,
        price_feed: PriceFeed,
        macro_fetcher: MacroFetcher,
        sentiment_analyzer: SentimentAnalyzer,
    ):
        super().__init__("analysis_agent", event_bus)
        self.price_feed = price_feed
        self.macro_fetcher = macro_fetcher
        self.sentiment = sentiment_analyzer
        self.technical = TechnicalAnalyzer()
        self.fundamental = FundamentalAnalyzer()
        self.ml_signals = MLSignalGenerator()
        self.config = get_config()

        # Cache data antar siklus
        self._last_macro_context: Dict = {}
        self._last_sentiment_score: float = 0.0
        self._news_queue = None

    async def initialize(self):
        """Inisialisasi: subscribe ke event bus, muat model ML."""
        self._news_queue = await self.event_bus.subscribe(Channels.NEWS_SENTIMENT)
        await self.ml_signals.initialize()
        logger.info("AnalysisAgent diinisialisasi")

    async def sense(self) -> dict:
        """Kumpulkan data harga OHLCV dan sentimen terbaru."""
        result = {}

        # Ambil OHLCV untuk setiap simbol
        ohlcv_data = {}
        for symbol in self.config.symbols:
            candles = await self.price_feed.fetch_ohlcv(symbol, timeframe="5m", limit=200)
            if candles:
                ohlcv_data[symbol] = candles

        result["ohlcv"] = ohlcv_data

        # Baca sentimen terbaru dari event bus (non-blocking)
        while self._news_queue and not self._news_queue.empty():
            try:
                event = self._news_queue.get_nowait()
                sentiment_data = event.data
                self._last_sentiment_score = sentiment_data.get("aggregate", {}).get("avg_score", 0)
            except Exception:
                break

        result["sentiment_score"] = self._last_sentiment_score
        result["macro_context"] = self._last_macro_context

        return result

    async def think(self, data: dict) -> dict:
        """Analisis teknikal + fundamental + ML untuk setiap simbol."""
        analyses = {}

        for symbol, ohlcv in data.get("ohlcv", {}).items():
            # Konversi ke DataFrame
            df = TechnicalAnalyzer.ohlcv_to_dataframe(ohlcv)

            # Hitung indikator teknikal
            df = self.technical.calculate_indicators(df)

            # Hasilkan sinyal teknikal
            tech_signals = self.technical.generate_signals(df)

            # Analisis fundamental
            self.fundamental.update_macro(data.get("macro_context", {}))
            sentiment_agg = {"avg_score": data.get("sentiment_score", 0), "label": "NEUTRAL"}
            if data.get("sentiment_score", 0) > 0.05:
                sentiment_agg["label"] = "POSITIVE"
            elif data.get("sentiment_score", 0) < -0.05:
                sentiment_agg["label"] = "NEGATIVE"
            self.fundamental.update_sentiment(sentiment_agg)
            fund_analysis = self.fundamental.analyze()

            # Prediksi ML
            ml_prediction = self.ml_signals.predict(
                tech_signals,
                data.get("sentiment_score", 0),
                df,
            )

            # Gabungkan semua sinyal
            combined = self._combine_signals(tech_signals, fund_analysis, ml_prediction)

            # Ambil harga terakhir dan indikator
            last_row = df.iloc[-1]
            current_price = float(last_row["close"])

            analyses[symbol] = {
                "technical": tech_signals,
                "fundamental": fund_analysis,
                "ml_prediction": ml_prediction,
                "combined": combined,
                "current_price": current_price,
                "indicators": {
                    "rsi": float(last_row.get("rsi", 50)) if pd.notna(last_row.get("rsi")) else 50,
                    "macd_hist": float(last_row.get("macd_hist", 0)) if pd.notna(last_row.get("macd_hist")) else 0,
                    "atr": float(last_row.get("atr", 0)) if pd.notna(last_row.get("atr")) else 0,
                    "ema_short": float(last_row.get("ema_short", 0)) if pd.notna(last_row.get("ema_short")) else 0,
                    "ema_long": float(last_row.get("ema_long", 0)) if pd.notna(last_row.get("ema_long")) else 0,
                },
            }

        # Buat ringkasan
        reasoning_parts = []
        for symbol, a in analyses.items():
            c = a["combined"]
            reasoning_parts.append(
                f"{symbol}: {c['direction']} (confidence {c['confidence']:.0%}) "
                f"[Teknikal: {a['technical']['direction']}, "
                f"Fundamental: {a['fundamental']['direction']}, "
                f"ML: {a['ml_prediction']['action']}]"
            )

        reasoning = " | ".join(reasoning_parts) if reasoning_parts else "Tidak ada data"

        return {
            "analyses": analyses,
            "reasoning": reasoning,
            "summary": reasoning,
            "input_summary": {
                "symbols": list(data.get("ohlcv", {}).keys()),
                "sentiment_score": data.get("sentiment_score", 0),
            },
            "output_summary": {
                symbol: a["combined"]["direction"]
                for symbol, a in analyses.items()
            },
        }

    async def act(self, analysis: dict):
        """Broadcast hasil analisis ke event bus."""
        repo = await self._get_repo()

        for symbol, a in analysis.get("analyses", {}).items():
            # Simpan sinyal ke database
            signal = Signal(
                symbol=symbol,
                signal_type="TECHNICAL",
                signal_value=json.dumps(a["technical"]["signals"], default=str),
                direction=a["combined"]["direction"],
                confidence=a["combined"]["confidence"],
                source="analysis_agent",
            )
            await repo.insert_signal(signal)

            # Broadcast
            await self.publish(Channels.MARKET_ANALYSIS, {
                "symbol": symbol,
                "combined": a["combined"],
                "technical": a["technical"],
                "fundamental": a["fundamental"],
                "ml_prediction": a["ml_prediction"],
                "current_price": a["current_price"],
                "indicators": a["indicators"],
            })

        self.logger.info(analysis.get("reasoning", ""))

    async def update_macro(self):
        """
        Update data makroekonomi. Dipanggil oleh scheduler tiap 6 jam.
        """
        try:
            macro_data = await self.macro_fetcher.fetch_all()
            self._last_macro_context = self.macro_fetcher.interpret_macro_context(
                macro_data.get("fred", []),
                macro_data.get("calendar_events", []),
            )

            # Simpan ke database
            repo = await self._get_repo()
            for m in macro_data.get("fred", []):
                await repo.upsert_macro(m)

            self.fundamental.update_calendar(macro_data.get("calendar_events", []))
            self.logger.info(f"Data makro diperbarui: {self._last_macro_context.get('summary', '')}")

        except Exception as e:
            self.logger.error(f"Gagal update makro: {e}")

    async def run_finbert_batch(self):
        """
        Jalankan FinBERT batch pada berita terkumpul. Dipanggil tiap 15 menit.
        """
        try:
            repo = await self._get_repo()
            recent_news = await repo.get_recent_news(limit=20)

            if not recent_news:
                return

            # Ambil berita yang belum dianalisis FinBERT
            texts = []
            news_ids = []
            for n in recent_news:
                if n.get("sentiment_finbert") is None:
                    text = f"{n['title']}. {n.get('content_summary', '')}"
                    texts.append(text)
                    news_ids.append(n["id"])

            if not texts:
                return

            # Batch FinBERT
            results = await self.sentiment.analyze_finbert_batch(texts)

            # Update database
            db = await self._get_repo()
            for news_id, result in zip(news_ids, results):
                await db.db.execute(
                    "UPDATE news SET sentiment_finbert = ?, sentiment_label = ? WHERE id = ?",
                    (result["score"], result["label"], news_id),
                )
            await db.db.commit()

            self.logger.info(f"FinBERT batch: {len(texts)} berita dianalisis")

        except Exception as e:
            self.logger.error(f"FinBERT batch error: {e}")

    def _combine_signals(
        self, technical: Dict, fundamental: Dict, ml_prediction: Dict
    ) -> Dict:
        """
        Gabungkan sinyal dari 3 sumber dengan bobot:
        - Teknikal: 40%
        - Fundamental: 25%
        - ML: 35%
        """
        weights = {"technical": 0.40, "fundamental": 0.25, "ml": 0.35}

        # Konversi arah ke skor (-1, 0, +1)
        def direction_to_score(direction: str) -> float:
            mapping = {"BULLISH": 1, "LONG": 1, "BEARISH": -1, "SHORT": -1}
            return mapping.get(direction.upper(), 0)

        tech_score = direction_to_score(technical.get("direction", "NEUTRAL"))
        fund_score = direction_to_score(fundamental.get("direction", "NEUTRAL"))
        ml_score = direction_to_score(ml_prediction.get("action", "HOLD"))

        # Bobot berdasarkan confidence
        tech_conf = technical.get("confidence", 0)
        fund_conf = fundamental.get("confidence", 0)
        ml_conf = ml_prediction.get("confidence", 0)

        weighted_score = (
            tech_score * weights["technical"] * tech_conf
            + fund_score * weights["fundamental"] * fund_conf
            + ml_score * weights["ml"] * ml_conf
        )

        # Normalize confidence
        total_weight = (
            weights["technical"] * tech_conf
            + weights["fundamental"] * fund_conf
            + weights["ml"] * ml_conf
        )
        combined_confidence = total_weight if total_weight > 0 else 0

        # Tentukan arah
        if weighted_score > 0.15:
            direction = "BULLISH"
        elif weighted_score < -0.15:
            direction = "BEARISH"
        else:
            direction = "NEUTRAL"

        return {
            "direction": direction,
            "confidence": round(min(combined_confidence, 1.0), 3),
            "weighted_score": round(weighted_score, 4),
            "components": {
                "technical": {"direction": technical.get("direction"), "confidence": tech_conf},
                "fundamental": {"direction": fundamental.get("direction"), "confidence": fund_conf},
                "ml": {"action": ml_prediction.get("action"), "confidence": ml_conf},
            },
        }
