"""
trading/position_manager.py — Kelola posisi Long/Short, cek SL/TP/likuidasi.

Scalping mode: batch close, reduced logging, throughput tinggi.
"""

import asyncio
from decimal import Decimal
from typing import Dict, List, Optional
from datetime import datetime

from core.config import get_config
from core.event_bus import EventBus, Channels
from core.logger import get_logger
from core.market_store import market_store
from database.db import get_db
from database.repository import Repository
from database.models import Position, Trade, BalanceSnapshot
from trading.risk_manager import RiskManager
from trading.models import Side, CloseReason

logger = get_logger("position_manager")


class PositionManager:
    """
    Mengelola lifecycle posisi:
    - Buka posisi baru
    - Update unrealized PnL
    - Cek stop loss / take profit
    - Cek likuidasi
    - Tutup posisi (single + batch)
    """

    def __init__(self, event_bus: EventBus, risk_manager: RiskManager):
        self.event_bus = event_bus
        self.risk_manager = risk_manager
        self.config = get_config()
        self._repo: Optional[Repository] = None

    async def _get_repo(self) -> Repository:
        if self._repo is None:
            db = await get_db()
            self._repo = Repository(db)
        return self._repo

    async def open_position(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        quantity: float,
        leverage: int,
        stop_loss: float = None,
        take_profit: float = None,
        reasoning: str = "",
    ) -> Optional[int]:
        """
        Buka posisi baru.

        Returns:
            position_id jika berhasil, None jika gagal.
        """
        repo = await self._get_repo()

        # Hitung margin dan harga likuidasi
        margin = float(
            Decimal(str(quantity)) * Decimal(str(entry_price)) / Decimal(str(leverage))
        )
        liq_price = self.risk_manager.calculate_liquidation_price(
            entry_price, side, leverage
        )

        # Hitung fee pembukaan
        fee = self.risk_manager.calculate_fee(quantity, entry_price, "TAKER")

        # Fee pembukaan langsung dipotong dari saldo (margin dikunci terpisah)
        account = await repo.get_account()
        if not account:
            logger.error("Akun belum diinisialisasi")
            return None

        # Validasi kecukupan modal, lalu potong lewat SQL delta atomik supaya
        # dua pembukaan yang jalan bersamaan tidak saling menimpa saldo.
        free_balance = await repo.apply_balance_delta(-(margin + fee))
        if free_balance < 0:
            # Batalkan: kembalikan yang sempat terpotong.
            await repo.apply_balance_delta(margin + fee)
            logger.debug(
                f"Saldo tidak cukup: {account['balance']:.2f} < margin {margin:.2f} + fee {fee:.2f}"
            )
            return None

        # Simpan posisi
        position = Position(
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            quantity=quantity,
            leverage=leverage,
            margin=margin,
            liquidation_price=liq_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            reasoning=reasoning,
        )
        position_id = await repo.insert_position(position)

        if position_id is None:
            # Gagal menyimpan -> kembalikan modal yang sudah terpotong.
            await repo.apply_balance_delta(margin + fee)
            return None

        # Simpan trade pembukaan
        trade = Trade(
            symbol=symbol,
            side="BUY" if side == "LONG" else "SELL",
            price=entry_price,
            quantity=quantity,
            fee=fee,
            fee_type="TAKER",
            trade_type="OPEN",
            position_id=position_id,
        )
        await repo.insert_trade(trade)

        # Broadcast event
        await self.event_bus.publish(
            Channels.POSITION_UPDATE,
            {
                "action": "OPENED",
                "position_id": position_id,
                "symbol": symbol,
                "side": side,
                "entry_price": entry_price,
                "quantity": quantity,
                "leverage": leverage,
                "margin": margin,
                "fee": fee,
            },
            source="position_manager",
        )

        logger.debug(
            f"Posisi dibuka: #{position_id} {side} {symbol} @ {entry_price} "
            f"qty={quantity} lev={leverage}x margin={margin:.2f}"
        )
        return position_id

    async def close_position(
        self,
        position_id: int,
        close_price: float,
        reason: str = "MANUAL",
    ) -> Optional[Dict]:
        """
        Tutup posisi.

        Returns:
            {"realized_pnl": float, "fee": float, "net_pnl": float}
        """
        repo = await self._get_repo()

        # Ambil data posisi
        positions = await repo.get_open_positions()
        pos = None
        for p in positions:
            if p["id"] == position_id:
                pos = p
                break

        if not pos:
            return None

        # Hitung PnL
        pnl_info = self.risk_manager.calculate_pnl(
            pos["side"], pos["entry_price"], close_price, pos["quantity"]
        )

        # Fee penutupan
        fee = self.risk_manager.calculate_fee(pos["quantity"], close_price, "TAKER")

        # Fee PEMBUKAAN harus ikut dihitung. Fee itu sudah dipotong dari saldo
        # saat posisi dibuka (lihat `open_position`), jadi kalau `realized_pnl`
        # tidak ikut memotongnya, maka:
        #   * `get_trade_stats` melaporkan PnL lebih besar dari kenyataan;
        #   * win rate menghitung posisi yang rugi bersih sebagai "menang";
        #   * `get_daily_realized_pnl` — sumber angka circuit breaker
        #     daily-loss — UNDER-estimasi kerugian, sehingga breaker menyala
        #     terlambat.
        #
        # Nilainya dibaca dari tabel `trades`, bukan dihitung ulang dari
        # `entry_price`: tarif taker bisa berubah di tengah jalan, dan
        # menghitung ulang akan menghasilkan angka yang berbeda dari yang
        # benar-benar terpotong dari saldo.
        open_fee = 0.0
        for t in await repo.get_trades_by_position(position_id):
            if (t.get("trade_type") or "").upper() == "OPEN":
                open_fee = float(t.get("fee") or 0.0)
                break

        # PnL bersih = PnL - fee PEMBUKAAN - fee PENUTUPAN
        net_pnl = pnl_info["pnl"] - open_fee - fee

        # Klaim-tunggal: hanya imbalik modal kalau baris ini benar-benar
        # bertransisi OPEN -> CLOSED oleh pemanggil ini. Pemanggil lain yang
        # berebut posisi yang sama menerima False dan tidak membayar margin
        # untuk kedua kalinya.
        claimed = await repo.close_position(position_id, close_price, net_pnl, reason)
        if not claimed:
            return None

        # Simpan trade penutupan
        trade = Trade(
            symbol=pos["symbol"],
            side="SELL" if pos["side"] == "LONG" else "BUY",
            price=close_price,
            quantity=pos["quantity"],
            fee=fee,
            fee_type="TAKER",
            trade_type="CLOSE",
            position_id=position_id,
        )
        await repo.insert_trade(trade)

        # Kembalikan margin + PnL bersih ke saldo lewat delta atomik.
        # Nilai absolut hasil read-modify-write akan saling menimpa ketika
        # pembukaan dan penutupan berjalan bersamaan.
        #
        # `open_fee` ditambah balik di sini, bukan di `net_pnl` yang
        # dikreditkan:
        #
        #   * fee opening SUDAH dipotong dari saldo saat `open_position`;
        #   * `net_pnl` (dipakai untuk `realized_pnl` dan statistik)
        #     juga memotong \open_fee\, supaya win rate dan circuit
        #     breaker daily-loss melihat profit bersih sebenarnya;
        #   * kalau `net_pnl` saja yang dikreditkan, `open_fee` terpotong
        #     DUA KALI -- sekali saat opening, sekali lagi di sini.
        #
        # Dampak double-hitung yang dulu ada: saldo akhir 2.70 USDT
        # lebih kecil dari seharusnya untuk setiap posisi. Pada 330
        # posisi paper itu, sekitar 891 USDT tersembunyi di saldo.
        returned = pos["margin"] + net_pnl + open_fee
        new_balance = await repo.apply_balance_delta(returned)

        # Update statistik akun. Kolom `balance` SENGAJA tidak disentuh di sini:
        # `apply_balance_delta` sudah menjadikannya mutasi atomik di atas, dan
        # menulis ulang nilai absolut di sini akan menimpa pembukaan yang
        # mungkin selesai bersamaan di coroutine lain.
        #
        # `peak_balance` harus mengikuti EQUITY (kas + margin + unrealized),
        # bukan kas bebas. Kalau mengikuti kas bebas, setiap posisi terbuka
        # menurunkan peak sendiri sehingga batas drawdown bergeser tanpa
        # mencerminkan penurunan nilai portofolio yang sebenarnya.
        remaining_margin = await self.get_total_open_margin()
        equity_now = new_balance + remaining_margin
        stats = await repo.get_trade_stats()
        await repo.bump_peak_balance(equity_now)
        await repo.update_account_stats(
            total_pnl=stats["total_pnl"],
            total_trades=stats["total_trades"],
            winning_trades=stats["winning_trades"],
            losing_trades=stats["losing_trades"],
            profit_factor=(
                stats["profit_factor"]
                if stats["profit_factor"] != float("inf")
                else 0
            ),
        )

        # Broadcast
        await self.event_bus.publish(
            Channels.POSITION_UPDATE,
            {
                "action": "CLOSED",
                "position_id": position_id,
                "symbol": pos["symbol"],
                "side": pos["side"],
                "close_price": close_price,
                "realized_pnl": net_pnl,
                "reason": reason,
            },
            source="position_manager",
        )

        logger.debug(
            f"Posisi ditutup: #{position_id} {pos['side']} {pos['symbol']} "
            f"@ {close_price} PnL={net_pnl:+.2f} alasan={reason}"
        )

        return {
            "realized_pnl": net_pnl,
            "fee": fee,
            "gross_pnl": pnl_info["pnl"],
            "roe_pct": pnl_info["roe_pct"],
        }

    async def batch_close_positions(
        self, position_ids: List[int], prices: Dict[str, float], reason: str
    ) -> int:
        """
        Tutup beberapa posisi sekaligus. Return jumlah yang berhasil ditutup.
        """
        closed = 0
        repo = await self._get_repo()
        open_positions = await repo.get_open_positions()

        # Map id -> pos
        pos_map = {p["id"]: p for p in open_positions}

        for pid in position_ids:
            pos = pos_map.get(pid)
            if not pos:
                continue
            price = prices.get(pos["symbol"])
            if not price:
                continue
            result = await self.close_position(pid, price, reason)
            if result:
                closed += 1

        if closed:
            logger.info(f"Batch close: {closed}/{len(position_ids)} posisi ditutup ({reason})")
        return closed

    async def update_positions(self, prices: Dict[str, float]):
        """
        Update semua posisi terbuka: hitung unrealized PnL, cek SL/TP/liquidasi.
        """
        repo = await self._get_repo()
        open_positions = await repo.get_open_positions()

        for pos in open_positions:
            symbol = pos["symbol"]
            current_price = prices.get(symbol)
            if current_price is None:
                current_price = market_store.get_price(symbol)

            if current_price is None:
                continue

            # Hitung unrealized PnL
            pnl_info = self.risk_manager.calculate_pnl(
                pos["side"], pos["entry_price"], current_price, pos["quantity"]
            )
            await repo.update_position_pnl(pos["id"], pnl_info["pnl"])

            # Cek likuidasi
            if self._should_liquidate(pos, current_price):
                logger.warning(f"LIKUIDASI posisi #{pos['id']} {pos['side']} {symbol}")
                await self._liquidate(pos, current_price)
                continue

            # Cek stop loss
            if pos["stop_loss"] and self._sl_hit(pos, current_price):
                logger.debug(f"SL hit: #{pos['id']}")
                await self.close_position(pos["id"], current_price, CloseReason.SL_HIT)
                continue

            # Cek take profit
            if pos["take_profit"] and self._tp_hit(pos, current_price):
                logger.debug(f"TP hit: #{pos['id']}")
                await self.close_position(pos["id"], current_price, CloseReason.TP_HIT)
                continue

    def _should_liquidate(self, pos: dict, current_price: float) -> bool:
        if pos["side"] == "LONG":
            return current_price <= pos["liquidation_price"]
        else:
            return current_price >= pos["liquidation_price"]

    def _sl_hit(self, pos: dict, price: float) -> bool:
        if pos["side"] == "LONG":
            return price <= pos["stop_loss"]
        else:
            return price >= pos["stop_loss"]

    def _tp_hit(self, pos: dict, price: float) -> bool:
        if pos["side"] == "LONG":
            return price >= pos["take_profit"]
        else:
            return price <= pos["take_profit"]

    async def _liquidate(self, pos: dict, price: float):
        repo = await self._get_repo()

        # Margin sudah terpotong saat posisi dibuka, jadi likuidasi tidak
        # mengubah saldo lagi — yang dicatat hanya seluruh margin yang hilang
        # sebagai realized PnL. Klaim-tunggal mencegah dua jalur (mis.
        # `update_positions` dan scheduler) sama-sama mencatat likuidasi.
        realized_pnl = -pos["margin"]
        claimed = await repo.liquidate_position(pos["id"], price, realized_pnl)
        if not claimed:
            return

        trade = Trade(
            symbol=pos["symbol"],
            side="SELL" if pos["side"] == "LONG" else "BUY",
            price=price,
            quantity=pos["quantity"],
            fee=0,
            trade_type="LIQUIDATION",
            position_id=pos["id"],
        )
        await repo.insert_trade(trade)

        await self.event_bus.publish(
            Channels.POSITION_UPDATE,
            {
                "action": "LIQUIDATED",
                "position_id": pos["id"],
                "symbol": pos["symbol"],
                "side": pos["side"],
                "liquidation_price": price,
                "margin_lost": pos["margin"],
            },
            source="position_manager",
        )

    async def get_open_position_count(self) -> int:
        repo = await self._get_repo()
        positions = await repo.get_open_positions()
        return len(positions)

    async def get_total_unrealized_pnl(self) -> float:
        repo = await self._get_repo()
        positions = await repo.get_open_positions()
        return sum(p.get("unrealized_pnl", 0) for p in positions)

    async def get_total_open_margin(self) -> float:
        """Total margin yang sedang terkunci oleh posisi terbuka."""
        repo = await self._get_repo()
        positions = await repo.get_open_positions()
        return sum(p.get("margin", 0) for p in positions)

    async def take_balance_snapshot(self):
        repo = await self._get_repo()
        account = await repo.get_account()
        if not account:
            return

        positions = await repo.get_open_positions()
        open_margin = sum(p.get("margin", 0) for p in positions)
        unrealized = sum(p.get("unrealized_pnl", 0) for p in positions)
        wallet_balance = account["balance"] + open_margin
        equity = wallet_balance + unrealized

        snap = BalanceSnapshot(
            balance=wallet_balance,
            unrealized_pnl=unrealized,
            equity=equity,
        )
        await repo.insert_balance_snapshot(snap)
