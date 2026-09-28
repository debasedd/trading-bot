"""
agents/execution_agent.py — Agen 4: Mesin eksekusi paper trading + scalping.

Scalping additions:
- Auto-close posisi expired (> max_hold_seconds)
- Fast take profit (close saat profit >= min_profit_pct)
- Batch processing untuk throughput tinggi
"""

import json
import time
from typing import Dict, Optional

from agents.base_agent import BaseAgent
from core.event_bus import EventBus, Channels
from core.config import get_config
from core.market_store import market_store
from core.logger import get_logger
from core.utils import parse_db_timestamp
from trading.models import Order, TradeAction
from trading.risk_manager import RiskManager

logger = get_logger("execution_agent")


class ExecutionAgent(BaseAgent):
    """
    Agen eksekusi paper trading + scalping.

    Tiap siklus:
    1. Proses order baru dari DecisionAgent
    2. Auto-close posisi expired (> max_hold_seconds)
    3. Fast take profit (close saat profit >= min_profit_pct)
    4. SL/TP/likuidasi standar via engine
    """

    def __init__(self, event_bus: EventBus, engine):
        super().__init__("execution_agent", event_bus)
        self.engine = engine
        self.risk_manager = RiskManager()
        self.config = get_config()
        self.scalp = self.config.scalping
        self._decision_queue = None
        self._price_queue = None

    async def initialize(self):
        """Subscribe ke channel keputusan dan harga."""
        self._decision_queue = await self.event_bus.subscribe(Channels.TRADE_DECISION)
        self._price_queue = await self.event_bus.subscribe(Channels.PRICE_UPDATE)
        logger.info("ExecutionAgent diinisialisasi (scalping mode)")

    async def sense(self) -> dict:
        """Baca order dari DecisionAgent dan update harga."""
        pending_orders = []

        # Baca update harga dari event bus
        while self._price_queue and not self._price_queue.empty():
            try:
                event = self._price_queue.get_nowait()
                price_data = event.data
                symbol = price_data.get("symbol")
                price = price_data.get("price")
                if symbol and price:
                    self.engine.update_price(symbol, price)
            except Exception:
                break

        # Sinkronkan harga dari market_store ke engine
        for sym, px in market_store.get_all_prices().items():
            if px and px > 0:
                self.engine.update_price(sym, px)

        # Baca keputusan trading
        while self._decision_queue and not self._decision_queue.empty():
            try:
                event = self._decision_queue.get_nowait()
                pending_orders.append(event.data)
            except Exception:
                break

        return {"pending_orders": pending_orders}

    async def think(self, data: dict) -> dict:
        """Validasi dan persiapkan order untuk eksekusi."""
        pending = data.get("pending_orders", [])
        validated_orders = []

        for order_data in pending:
            order: Order = order_data.get("order")
            decision = order_data.get("decision")

            if order is None:
                continue

            # Hitung SL/TP absolut dari persentase
            price = self.engine.get_price(order.symbol)
            if not price:
                price = market_store.get_price(order.symbol)
                if price:
                    self.engine.update_price(order.symbol, price)

            if price and order.action in (TradeAction.OPEN_LONG, TradeAction.OPEN_SHORT):
                side = "LONG" if order.action == TradeAction.OPEN_LONG else "SHORT"

                # Target dinamis (ATR) yang otoritatifnya dihitung di
                # `PaperTradingEngine._execute_open` dari harga fill. Nilai di
                # sini advisory: `order.stop_loss` dan `order.take_profit`
                # akan ditimpa engine sebelum disimpan.
                #
                # Kita tetap menghitungnya di sini karena dua alasan:
                #   1. DecisionAgent membaca `order.stop_loss_pct` untuk
                #      reasoning-nya, jadi angkanya harus masuk akal.
                #   2. Kalau hitungan engine ditolak volatility gate, nilai di
                #      sini menjadi jejak apa yang sebenarnya dihitung.
                # Melewatkan begitu saja membuat reasoning agent mengutip
                # angka yang sudah basi.
                from analysis import volatility as vol_mod

                thresholds = vol_mod.get_dynamic_tp_sl_thresholds(
                    order.symbol, self.config
                )
                if thresholds.get("used_dynamic"):
                    sl_pct = max(
                        float(thresholds["sl_pct"]),
                        float(self.scalp.tight_sl_pct),
                    )
                    tp_pct = max(
                        float(thresholds["tp_pct"]),
                        float(self.scalp.fast_tp_pct),
                    )
                else:
                    sl_pct = order_data.get(
                        "stop_loss_pct", self.scalp.tight_sl_pct
                    )
                    tp_pct = order_data.get(
                        "take_profit_pct", self.scalp.fast_tp_pct
                    )

                order.stop_loss = self.risk_manager.calculate_stop_loss(price, side, sl_pct)
                order.take_profit = self.risk_manager.calculate_take_profit(price, side, tp_pct)

            validated_orders.append(order)

        reasoning = (
            f"{len(validated_orders)} order siap dieksekusi"
            if validated_orders
            else "Tidak ada order baru"
        )

        return {
            "validated_orders": validated_orders,
            "reasoning": reasoning,
            "summary": reasoning,
            "input_summary": {"pending_count": len(pending)},
            "output_summary": {"validated_count": len(validated_orders)},
        }

    async def act(self, analysis: dict):
        """Eksekusi order yang sudah divalidasi."""
        orders = analysis.get("validated_orders", [])

        for order in orders:
            try:
                result = await self.engine.execute_order(order)

                if result["success"]:
                    self.logger.debug(f"Eksekusi OK: {order.symbol} {order.action.value}")
                else:
                    self.logger.warning(f"Eksekusi gagal: {result['message']}")

            except Exception as e:
                self.logger.error(f"Error eksekusi order {order.symbol}: {e}")

        if orders:
            self.logger.info(f"Batch eksekusi: {len(orders)} order diproses")

    async def check_positions(self):
        """
        Cek posisi terbuka — scalping auto-close + SL/TP/likuidasi.
        Dipanggil dari execution loop (tiap ~0.3 detik).
        """
        # 1. Kunci breakeven stop loss untuk posisi yang sudah profit melampaui fee
        await self._protect_breakeven()

        # 2. Fast take profit (close saat profit >= min_profit_pct)
        await self._scalp_take_profit()

        # 3. Auto-close posisi expired (> max_hold_seconds)
        await self._auto_close_expired()

        # 4. SL/TP/likuidasi standar
        await self.engine.check_positions()

    async def _protect_breakeven(self):
        """
        Kunci stop loss ke level breakeven (+fee) begitu profit menyentuh
        `breakeven_trigger_pct`. Mencegah posisi hijau berbalik menjadi kerugian.

        Jalankan SEBELUM `_scalp_take_profit` dan ambangnya DI BAWAH
        `min_profit_pct`. Kalau keduanya sama, TP menyala lebih dulu di siklus
        yang sama dan seluruh logika ini tidak pernah memberi efek.
        """
        repo = await self._get_repo()
        open_positions = await repo.get_open_positions()

        trigger = self.scalp.breakeven_trigger_pct
        offset = self.scalp.breakeven_offset_pct

        for pos in open_positions:
            symbol = pos["symbol"]
            price = market_store.get_price(symbol)
            if not price:
                price = self.engine._last_prices.get(symbol)
            if not price:
                continue

            entry = pos["entry_price"]
            if entry <= 0:
                continue

            # Hitung persentase pergerakan harga
            if pos["side"] == "LONG":
                pnl_pct = (price - entry) / entry
                be_sl = entry * (1 + offset)
                current_sl = pos.get("stop_loss")
                if pnl_pct >= trigger and (current_sl is None or current_sl < be_sl):
                    await repo.update_position_sl_tp(pos["id"], stop_loss=be_sl)
                    pos["stop_loss"] = be_sl
                    self.logger.debug(
                        f"Breakeven locked LONG #{pos['id']} {symbol} SL -> {be_sl:.4f}"
                    )
            else:
                pnl_pct = (entry - price) / entry
                be_sl = entry * (1 - offset)
                current_sl = pos.get("stop_loss")
                if pnl_pct >= trigger and (current_sl is None or current_sl > be_sl):
                    await repo.update_position_sl_tp(pos["id"], stop_loss=be_sl)
                    pos["stop_loss"] = be_sl
                    self.logger.debug(
                        f"Breakeven locked SHORT #{pos['id']} {symbol} SL -> {be_sl:.4f}"
                    )

    async def _auto_close_expired(self):
        """Tutup posisi yang sudah melewati max_hold_seconds."""
        repo = await self._get_repo()
        open_positions = await repo.get_open_positions()
        now = time.time()
        closed_count = 0

        for pos in open_positions:
            open_ts = parse_db_timestamp(pos.get("opened_at"))
            if open_ts <= 0:
                continue

            hold_seconds = now - open_ts

            # Skip posisi yang masih terlalu muda
            if hold_seconds < self.scalp.min_hold_seconds:
                continue

            # Close posisi expired
            if hold_seconds > self.scalp.max_hold_seconds:
                symbol = pos["symbol"]
                price = market_store.get_price(symbol)
                if not price:
                    price = self.engine._last_prices.get(symbol)
                if not price:
                    continue

                result = await self.engine.position_manager.close_position(
                    pos["id"], price, "SCALP_EXPIRED"
                )
                if result:
                    closed_count += 1

        if closed_count:
            self.logger.info(f"Auto-close: {closed_count} posisi expired ditutup")

    async def _scalp_take_profit(self):
        """Tutup posisi yang sudah profit >= min_profit_pct."""
        repo = await self._get_repo()
        open_positions = await repo.get_open_positions()
        now = time.time()
        closed_count = 0

        for pos in open_positions:
            symbol = pos["symbol"]
            price = market_store.get_price(symbol)
            if not price:
                price = self.engine._last_prices.get(symbol)
            if not price:
                continue

            # Cek hold time minimum
            open_ts = parse_db_timestamp(pos.get("opened_at"))
            if open_ts > 0 and (now - open_ts) < self.scalp.min_hold_seconds:
                continue

            # Hitung profit pct
            entry = pos["entry_price"]
            if entry <= 0:
                continue

            if pos["side"] == "LONG":
                profit_pct = (price - entry) / entry
            else:
                profit_pct = (entry - price) / entry

            # Fast take profit: close jika profit >= min_profit_pct
            if profit_pct >= self.scalp.min_profit_pct:
                result = await self.engine.position_manager.close_position(
                    pos["id"], price, "SCALP_TP"
                )
                if result:
                    closed_count += 1

        if closed_count:
            self.logger.info(f"Scalp TP: {closed_count} posisi profitable ditutup")
