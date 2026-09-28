"""
core/event_bus.py — Event bus asyncio untuk komunikasi antar-agen.

Setiap agen subscribe ke channel tertentu. Publisher mengirim pesan
ke channel, semua subscriber di channel itu menerimanya.
"""

import asyncio
from typing import Any, Callable, Dict, List, Optional
from dataclasses import dataclass, field
from datetime import datetime, timezone
from core.logger import get_logger

logger = get_logger("event_bus")


@dataclass
class Event:
    """Satu event dalam sistem."""
    channel: str
    data: Any
    source: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class EventBus:
    """
    Bus pesan async berbasis asyncio.Queue.

    Penggunaan:
        bus = EventBus()
        queue = bus.subscribe("price_update")
        await bus.publish("price_update", {"symbol": "BTCUSDT", "price": 60000}, source="price_feed")
        event = await queue.get()
    """

    def __init__(self, maxsize: int = 1000):
        self._channels: Dict[str, List[asyncio.Queue]] = {}
        self._maxsize = maxsize
        self._lock = asyncio.Lock()

    async def subscribe(self, channel: str) -> asyncio.Queue:
        """Daftarkan subscriber baru ke channel. Return queue untuk menerima event."""
        async with self._lock:
            if channel not in self._channels:
                self._channels[channel] = []
            queue = asyncio.Queue(maxsize=self._maxsize)
            self._channels[channel].append(queue)
            logger.debug(f"Subscriber baru di channel '{channel}' (total: {len(self._channels[channel])})")
            return queue

    async def unsubscribe(self, channel: str, queue: asyncio.Queue):
        """Hapus subscriber dari channel."""
        async with self._lock:
            if channel in self._channels:
                try:
                    self._channels[channel].remove(queue)
                    logger.debug(f"Subscriber dihapus dari channel '{channel}'")
                except ValueError:
                    pass

    async def publish(self, channel: str, data: Any, source: str = ""):
        """Kirim event ke semua subscriber di channel."""
        event = Event(channel=channel, data=data, source=source)

        async with self._lock:
            subscribers = self._channels.get(channel, [])

        if not subscribers:
            return

        for queue in subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # Buang event terlama, masukkan yang baru
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    logger.warning(f"Queue penuh di channel '{channel}', event dibuang")

    def get_channel_stats(self) -> Dict[str, int]:
        """Statistik jumlah subscriber per channel."""
        return {ch: len(subs) for ch, subs in self._channels.items()}


# Channels yang digunakan dalam sistem
class Channels:
    """Konstanta nama channel."""
    PRICE_UPDATE = "price_update"           # Data harga terbaru
    NEWS_SENTIMENT = "news_sentiment"       # Hasil analisis berita
    MARKET_ANALYSIS = "market_analysis"     # Hasil analisis makro/teknikal
    TRADE_DECISION = "trade_decision"       # Keputusan trading
    TRADE_EXECUTED = "trade_executed"       # Konfirmasi eksekusi
    POSITION_UPDATE = "position_update"     # Update posisi
    BALANCE_UPDATE = "balance_update"       # Update saldo
    AGENT_LOG = "agent_log"                 # Log penalaran agen
    SYSTEM_EVENT = "system_event"           # Event sistem (start/stop/error)
    DIRECTION_ENSEMBLE = "direction_ensemble"  # Snapshot arah LONG/SHORT hasil ensemble
