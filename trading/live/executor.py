"""
trading/live/executor.py — Adapter supaya `ExecutionAgent` tidak perlu
tahu beda paper dan live.

`ExecutionAgent` sekarang hanya bisa bicara ke `PaperTradingEngine`.
Kalau live disambungkan langsung di sana, satu kelas jadi dua mesin
dengan dua cara failure berbeda. `LiveExecutor` memenuhi antarmuka
yang sama (`execute_order`, `update_price`) sehingga agent-nya tidak
perlu diubah.

Perbedaan safety hidup di DALAM adapter: setiap order live melewati
gerbang, dan kegagalan mengembalikan dict yang jelas -- bukan melempar
exception ke loop yang sedang berjalan.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any, Dict

from core.logger import get_logger
from core.market_store import market_store
from trading.live.engine import LiveEngine

logger = get_logger("live_executor")


def make_cloid(prefix: str = "tb") -> str:
    """
    Client order id yang unik.

    Wajib untuk order opening: tanpa cloid, timeout membuat retry
    menggandakan posisi karena bot tidak pernah bisa tahu apakah
    order pertamanya benar-benar masuk.
    """
    return "{}-{}".format(prefix, uuid.uuid4().hex[:16])


def coin_of(symbol: str) -> str:
    """
    Ambil ticker koin dari symbol ccxt.

    `BTC/USDT:USDT` -> `BTC`. Hyperliquid memakai ticker polos.
    """
    if not symbol:
        return symbol
    return symbol.split("/")[0].split(":")[0]


class LiveExecutor:
    """Adapter order untuk jalur live."""

    def __init__(self, engine: LiveEngine):
        self.engine = engine

    def update_price(self, symbol: str, price: float) -> None:
        """
        Live tidak butuh cache harga lokal.

        Sumber kebenaran adalah bursa, dan keputusan diambil dari
        snapshot arah. Menyimpan harga di sini hanya membuka
        kemungkinan dua sumber kebenaran yang berbeda.
        """
        return None

    async def execute_order(self, order) -> Dict[str, Any]:
        """Kirim satu order lewat live engine."""
        from trading.models import TradeAction

        if order.action in (TradeAction.OPEN_LONG, TradeAction.OPEN_SHORT):
            return await self._open(order)
        if order.action == TradeAction.CLOSE:
            return await self._close(order)
        return {"success": True, "message": "HOLD",
                "position_id": None, "details": {}}

    async def _open(self, order) -> Dict[str, Any]:
        from trading.models import TradeAction

        is_buy = order.action == TradeAction.OPEN_LONG
        coin = coin_of(order.symbol)
        cloid = make_cloid()

        if order.stop_loss is None or order.take_profit is None:
            return {
                "success": False,
                "message": "order live butuh SL dan TP; tanpa itu posisi "
                           "terbuka tanpa proteksi",
                "position_id": None,
                "details": {"blockers": ["missing_tpsl"]},
            }

        try:
            price = self._price_for(order)
        except ValueError as exc:
            return {"success": False, "message": str(exc),
                    "position_id": None, "details": {}}

        result = await self.engine.submit_order(
            coin=coin,
            symbol=order.symbol,
            is_buy=is_buy,
            size=order.quantity or 0.0,
            price=price,
            stop_loss=order.stop_loss,
            take_profit=order.take_profit,
            leverage=order.leverage or 5,
            cloid=cloid,
        )

        if not result.get("success"):
            return {"success": False,
                    "message": result.get("message", "ditolak"),
                    "position_id": None, "details": result}

        if not result.get("protected"):
            return {"success": True,
                    "message": "order resting (belum terisi), cloid={}"
                               .format(cloid),
                    "position_id": None, "details": result}

        return {"success": True,
                "message": result.get("message", "posisi dibuka"),
                "position_id": (result.get("position") or {}).get("coin"),
                "details": result}

    async def _close(self, order) -> Dict[str, Any]:
        """Tutup posisi yang tercatat di engine."""
        position = self.engine.positions.get(order.symbol)
        if position is None:
            return {
                "success": False,
                "message": "tidak ada posisi tercatat untuk {}".format(
                    order.symbol),
                "position_id": None,
                "details": {},
            }

        is_buy = position.side == "SHORT"
        price = await asyncio.to_thread(
            self.engine.exchange.mid_price, position.coin)
        if price <= 0:
            return {
                "success": False,
                "message": "harga pasar tidak terbaca; TIDAK bisa menutup",
                "position_id": None,
                "details": {},
            }

        # Slippage 0.1% supaya order langsung terisi di books mana pun.
        # Order yang tidak terisi tidak menutup apa pun -- jadi lebih
        # baik sedikit lebih mahal daripada tidak menutup sama sekali.
        limit = price * (1.001 if is_buy else 0.999)
        result = await self.engine.submit_order(
            coin=position.coin,
            symbol=order.symbol,
            is_buy=is_buy,
            size=position.size,
            price=limit,
            is_close=True,
            cloid=make_cloid("close"),
        )
        return {
            "success": bool(result.get("success")),
            "message": result.get("message", "closing dikirim"),
            "position_id": position.coin,
            "details": result,
        }

    @staticmethod
    def _price_for(order) -> float:
        """
        Harga acuan untuk order opening.

        Kalau order tidak membawa harga, pakai harga pasar terakhir yang
        diketahui. `submit_order` menolak harga <= 0, jadi harga yang
        tidak ada harus gagal di sini dengan pesan jelas -- bukan
        terkirim sebagai 0 lalu ditolak bursa tanpa alasan.
        """
        price = getattr(order, "price", None)
        if price:
            return float(price)

        px = market_store.get_price(coin_of(order.symbol))
        if px and px > 0:
            return float(px)
        raise ValueError(
            "tidak ada harga untuk {}; order live butuh harga pasar".format(
                order.symbol))

