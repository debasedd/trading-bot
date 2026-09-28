"""
agents/news_agent.py — Agen 1: Pemindai berita & analisis sentimen.

Mengambil berita dari RSS + CryptoPanic, analisis sentimen via VADER,
dan broadcast hasilnya ke event bus.
"""

import json
from typing import Dict, List

from agents.base_agent import BaseAgent
from core.event_bus import EventBus, Channels
from data.news_fetcher import NewsFetcher
from data.sentiment import SentimentAnalyzer
from database.models import NewsItem, Signal
from core.logger import get_logger

logger = get_logger("news_agent")


class NewsAgent(BaseAgent):
    """
    Agen pemindai berita dan sentimen pasar.

    Siklus:
    1. Sense: ambil berita dari RSS + CryptoPanic
    2. Think: analisis sentimen setiap berita (VADER), hitung agregat
    3. Act: simpan ke DB, broadcast ke event bus
    """

    def __init__(self, event_bus: EventBus, sentiment_analyzer: SentimentAnalyzer):
        super().__init__("news_agent", event_bus)
        self.fetcher = NewsFetcher()
        self.sentiment = sentiment_analyzer

    async def sense(self) -> dict:
        """Ambil berita terbaru dari semua sumber."""
        news_items = await self.fetcher.fetch_all()
        self.fetcher.clear_cache()

        return {"news_items": news_items}

    async def think(self, data: dict) -> dict:
        """Analisis sentimen setiap berita, hitung agregat."""
        news_items: List[NewsItem] = data.get("news_items", [])

        if not news_items:
            return {
                "reasoning": "Tidak ada berita baru ditemukan",
                "news_with_sentiment": [],
                "aggregate": {"avg_score": 0, "label": "NEUTRAL", "count": 0},
                "summary": "Tidak ada berita baru",
                "input_summary": {"news_count": 0},
                "output_summary": {"sentiment": "NEUTRAL"},
            }

        # Analisis sentimen setiap berita
        sentiments = []
        news_with_sentiment = []

        for item in news_items:
            text = f"{item.title}. {item.content_summary or ''}"
            vader_result = await self.sentiment.analyze_vader_async(text)

            item.sentiment_vader = vader_result["compound"]
            item.sentiment_label = vader_result["label"]

            # Tentukan dampak berdasarkan skor sentimen
            abs_score = abs(vader_result["compound"])
            if abs_score > 0.5:
                item.impact_level = "HIGH"
            elif abs_score > 0.2:
                item.impact_level = "MEDIUM"
            else:
                item.impact_level = "LOW"

            sentiments.append(vader_result)
            news_with_sentiment.append(item)

        # Hitung agregat
        aggregate = self.sentiment.aggregate_sentiment(sentiments)

        # Identifikasi berita berdampak tinggi
        high_impact = [n for n in news_with_sentiment if n.impact_level == "HIGH"]

        reasoning = (
            f"Dianalisis {len(news_items)} berita. "
            f"Sentimen agregat: {aggregate['label']} (skor {aggregate['avg_score']:+.3f}). "
            f"{len(high_impact)} berita berdampak tinggi."
        )

        if high_impact:
            top_news = high_impact[0]
            reasoning += f" Berita teratas: '{top_news.title}' ({top_news.sentiment_label})"

        return {
            "reasoning": reasoning,
            "news_with_sentiment": news_with_sentiment,
            "aggregate": aggregate,
            "high_impact": high_impact,
            "summary": reasoning,
            "input_summary": {"news_count": len(news_items)},
            "output_summary": {
                "sentiment": aggregate["label"],
                "score": aggregate["avg_score"],
                "high_impact_count": len(high_impact),
            },
        }

    async def act(self, analysis: dict):
        """Simpan berita ke DB dan broadcast sentimen."""
        repo = await self._get_repo()

        # Simpan berita ke database
        for item in analysis.get("news_with_sentiment", []):
            try:
                exists = await repo.news_exists(item.title, item.source)
                if not exists:
                    await repo.insert_news(item)
            except Exception as e:
                self.logger.error(f"Gagal simpan berita: {e}")

        # Simpan sinyal sentimen
        aggregate = analysis.get("aggregate", {})
        if aggregate.get("count", 0) > 0:
            signal = Signal(
                symbol="MARKET",
                signal_type="SENTIMENT",
                signal_value=json.dumps(aggregate),
                direction=aggregate.get("label", "NEUTRAL"),
                confidence=min(abs(aggregate.get("avg_score", 0)) * 2, 1.0),
                source="news_agent",
            )
            await repo.insert_signal(signal)

        # Broadcast ke event bus
        await self.publish(Channels.NEWS_SENTIMENT, {
            "aggregate": aggregate,
            "high_impact_count": len(analysis.get("high_impact", [])),
            "high_impact_titles": [
                n.title for n in analysis.get("high_impact", [])[:5]
            ],
        })

        self.logger.info(analysis.get("reasoning", ""))
