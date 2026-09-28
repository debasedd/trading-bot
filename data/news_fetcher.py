"""
data/news_fetcher.py — RSS parser + CryptoPanic API untuk berita kripto.
"""

import asyncio
import time
from typing import List, Optional
from datetime import datetime, timedelta

import feedparser
import requests

from core.config import get_config
from core.logger import get_logger
from database.models import NewsItem

logger = get_logger("news_fetcher")


class NewsFetcher:
    """
    Pengambil berita kripto dari sumber gratis:
    - RSS Feeds (CoinDesk, CoinTelegraph)
    - CryptoPanic API (opsional, butuh token gratis)
    """

    def __init__(self):
        self.config = get_config()
        self._seen_titles = set()  # Cache untuk deduplikasi

    async def fetch_rss(self) -> List[NewsItem]:
        """Ambil berita dari semua RSS feed yang dikonfigurasi."""
        all_news = []

        for url in self.config.news_rss:
            try:
                items = await self._parse_rss_feed(url)
                all_news.extend(items)
            except Exception as e:
                logger.error(f"Gagal ambil RSS {url}: {e}")

        return all_news

    async def _parse_rss_feed(self, url: str) -> List[NewsItem]:
        """Parse satu RSS feed."""
        loop = asyncio.get_event_loop()

        # feedparser blocking — jalankan di thread pool
        feed = await loop.run_in_executor(None, feedparser.parse, url)

        items = []
        source = self._extract_source_name(url)

        for entry in feed.entries[:20]:  # Ambil 20 terbaru
            title = entry.get("title", "").strip()

            # Deduplikasi
            if not title or title in self._seen_titles:
                continue
            self._seen_titles.add(title)

            published = entry.get("published", "")
            link = entry.get("link", "")
            summary = entry.get("summary", "")

            # Bersihkan summary dari HTML
            if summary:
                import re
                summary = re.sub(r"<[^>]+>", "", summary)[:500]

            items.append(NewsItem(
                title=title,
                source=source,
                url=link,
                published_at=published,
                content_summary=summary,
            ))

        logger.info(f"RSS {source}: {len(items)} berita baru")
        return items

    async def fetch_cryptopanic(self) -> List[NewsItem]:
        """Ambil berita dari CryptoPanic (perlu token gratis)."""
        token = self.config.cryptopanic_token
        if not token:
            logger.debug("CryptoPanic token tidak dikonfigurasi, lewati")
            return []

        try:
            url = f"{self.config.cryptopanic_url}?auth_token={token}&public=true&filter=important"
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None, lambda: requests.get(url, timeout=10)
            )

            if response.status_code != 200:
                logger.warning(f"CryptoPanic API error: {response.status_code}")
                return []

            data = response.json()
            items = []

            for post in data.get("results", [])[:20]:
                title = post.get("title", "").strip()

                if not title or title in self._seen_titles:
                    continue
                self._seen_titles.add(title)

                items.append(NewsItem(
                    title=title,
                    source="cryptopanic",
                    url=post.get("url", ""),
                    published_at=post.get("published_at", ""),
                    content_summary=title,
                ))

            logger.info(f"CryptoPanic: {len(items)} berita baru")
            return items

        except Exception as e:
            logger.error(f"Gagal ambil CryptoPanic: {e}")
            return []

    async def fetch_all(self) -> List[NewsItem]:
        """Ambil berita dari semua sumber."""
        rss_task = self.fetch_rss()
        cp_task = self.fetch_cryptopanic()

        rss_news, cp_news = await asyncio.gather(rss_task, cp_task)
        all_news = rss_news + cp_news

        logger.info(f"Total berita baru: {len(all_news)}")
        return all_news

    def clear_cache(self):
        """Bersihkan cache deduplikasi (panggil secara berkala)."""
        # Simpan hanya 500 judul terakhir
        if len(self._seen_titles) > 500:
            recent = list(self._seen_titles)[-200:]
            self._seen_titles = set(recent)

    @staticmethod
    def _extract_source_name(url: str) -> str:
        """Ekstrak nama sumber dari URL."""
        if "coindesk" in url:
            return "coindesk"
        elif "cointelegraph" in url:
            return "cointelegraph"
        elif "decrypt" in url:
            return "decrypt"
        else:
            from urllib.parse import urlparse
            return urlparse(url).netloc.replace("www.", "")
