"""
agents/decision_agent.py — Agen 3: Pengambil keputusan scalping agresif.

Mode scalping: buka belasan posisi per menit, SL/TP ketat,
sinyal dari orderbook imbalance + momentum + teknikal.
"""

import json
import math
import time
from typing import Dict, List, Optional

from agents.base_agent import BaseAgent
from core.event_bus import EventBus, Channels
from core.config import get_config
from core.scheduler import is_us_market_open
from core.market_store import market_store
from trading.models import Order, TradeAction, TradeDecision, Side
from trading.risk_manager import RiskManager
from database.models import Signal
from core.logger import get_logger
from core.utils import parse_db_timestamp

logger = get_logger("decision_agent")

# SL/TP untuk sizing saat scalping dimatikan.
#
# Fallback SL/TP untuk mode NON-scalping (analisis/volume, bukan momentum).
#
# Angka ini TIDAK boleh diganti dengan `tight_sl_pct` dari config: kalau
# sizing memakai SL berjarak 0.25% sementara SL yang benar-benar dipasang 2%,
# notionalnya meledak 8x dan risiko sebenarnya jauh lebih besar dari yang
# disetujui risk manager.
#
# Dulu angka ini DIHARDCODE di dua tempat - di sini dan di
# `PaperTradingEngine._execute_open` (trading/paper_engine.py) - dengan
# komentar yang saling menunjuk. Itu pola yang pasti gagal: satu hari
# seseorang mengubah satu sisi dan tidak sadar yang lain sudah stale.
#
# Sekarang `trading/risk_manager.py` yang memiliki keduanya, dan kedua file
# mengimpornya. Satu tempat kebenaran, dan mengubahnya mengubah kedua
# jalur sekaligus.
from trading.risk_manager import (
    NON_SCALP_SL_PCT as _NON_SCALP_SL_PCT,
    NON_SCALP_TP_PCT as _NON_SCALP_TP_PCT,
)


class DecisionAgent(BaseAgent):
    """
    Agen scalping agresif.

    Tiap siklus (2-3 detik):
    1. Scan semua simbol untuk sinyal scalping
    2. Buka hingga batch_size posisi baru per siklus
    3. Close posisi yang sinyalnya berbalik
    """

    def __init__(self, event_bus: EventBus, risk_manager: RiskManager):
        super().__init__("decision_agent", event_bus)
        self.risk_manager = risk_manager
        self.config = get_config()
        self.scalp = self.config.scalping
        self._analysis_queue = None
        self._position_queue = None
        self._latest_analyses: Dict[str, Dict] = {}
        self._symbol_cooldowns: Dict[str, float] = {}
        self._symbol_loss_streak: Dict[str, int] = {}
        self._global_loss_streak: int = 0

    async def initialize(self):
        self._analysis_queue = await self.event_bus.subscribe(Channels.MARKET_ANALYSIS)
        self._position_queue = await self.event_bus.subscribe(Channels.POSITION_UPDATE)
        logger.info("DecisionAgent diinisialisasi")

    async def sense(self) -> dict:
        # Drain event bus
        while self._analysis_queue and not self._analysis_queue.empty():
            try:
                event = self._analysis_queue.get_nowait()
                symbol = event.data.get("symbol")
                if symbol:
                    self._latest_analyses[symbol] = event.data
            except Exception:
                break

        while self._position_queue and not self._position_queue.empty():
            try:
                event = self._position_queue.get_nowait()
                if event.data.get("action") == "CLOSED":
                    closed_sym = event.data.get("symbol")
                    pnl = float(event.data.get("realized_pnl") or 0.0)
                    if closed_sym:
                        # Cooldown dasar. Setelah SL beruntun di simbol yang sama,
                        # cooldown-nya dikalikan supaya bot tidak langsung
                        # masuk lagi ke arah yang baru saja terbukti salah.
                        if pnl <= 0:
                            streak = self._symbol_loss_streak.get(closed_sym, 0) + 1
                            self._symbol_loss_streak[closed_sym] = streak
                            cooldown = self.scalp.cooldown_after_loss_seconds * min(streak, 4)
                            self._global_loss_streak += 1
                        else:
                            self._symbol_loss_streak.pop(closed_sym, None)
                            cooldown = self.scalp.cooldown_after_close_seconds
                        self._symbol_cooldowns[closed_sym] = time.time() + cooldown
            except Exception:
                break

        return {
            "analyses": self._latest_analyses.copy(),
            "us_market_open": is_us_market_open(),
        }

    async def think(self, data: dict) -> dict:
        analyses = data.get("analyses", {})
        decisions = []
        reasoning_parts = []

        if not analyses:
            return {
                "decisions": [],
                "reasoning": "Menunggu data analisis",
                "summary": "HOLD — menunggu data",
                "input_summary": {},
                "output_summary": {"action": "HOLD"},
            }

        # Kumpulkan semua sinyal scalping dari snapshot ensemble arah
        scalp_signals = []
        for symbol, analysis in analyses.items():
            signals = await self._generate_scalp_signals(symbol, analysis)
            for sig in signals:
                scalp_signals.append((symbol, analysis, sig))

        # Sort by strength (strongest first)
        scalp_signals.sort(key=lambda x: x[2]["strength"], reverse=True)

        # Cek berapa posisi bisa dibuka
        repo = await self._get_repo()
        all_open = await repo.get_open_positions()
        open_count = len(all_open)
        max_new = max(0, self.config.risk.max_open_positions - open_count)
        batch_limit = min(self.scalp.batch_size, max_new)

        # Track simbol yang sudah punya posisi
        open_symbols = {}
        for p in all_open:
            sym = p["symbol"]
            if sym not in open_symbols:
                open_symbols[sym] = []
            open_symbols[sym].append(p)

        # CLOSE: tutup posisi yang berlawanan sinyal HANYA jika sinyal cukup
        # kuat (ambang di config, default 0.70).
        #
        # Ambang ini lebih tinggi dari `min_confidence` (0.40) dengan sengaja:
        # membuka posisi baru pada sinyal lemah adalah hal yang wajar, tapi
        # MEMBALIKKAN posisi yang sedang berjalan memerlukan bukti jauh lebih
        # kuat. Reversal salah arah bukan keputusan kecil — dia membakar dua
        # fee dan sering bertepatan dengan whipsaw yang justru mengenai stop.
        reversal_threshold = float(
            getattr(self.scalp, "reversal_close_threshold", 0.70)
        )
        now = time.time()
        closing_symbols = set()
        for symbol, analysis, sig in scalp_signals:
            if symbol in open_symbols:
                for pos in open_symbols[symbol]:
                    # Hormati min_hold_seconds sebelum cut reversal
                    open_ts = parse_db_timestamp(pos.get("opened_at"))
                    if open_ts > 0 and (now - open_ts) < self.scalp.min_hold_seconds:
                        continue

                    should_close = False
                    if pos["side"] == "LONG" and sig["direction"] == "BEARISH" and sig["strength"] >= reversal_threshold:
                        should_close = True
                    elif pos["side"] == "SHORT" and sig["direction"] == "BULLISH" and sig["strength"] >= reversal_threshold:
                        should_close = True

                    if should_close:
                        decisions.append(TradeDecision(
                            action=TradeAction.CLOSE,
                            symbol=symbol,
                            confidence=sig["strength"],
                            reasoning=f"Sinyal kuat berbalik {sig['direction']} ({sig['strength']:.0%}) — {sig['reason']}",
                            signals=sig,
                        ))
                        # Kosongkan `open_symbols` di SIKLUS YANG SAMA.
                        #
                        # Sebelumnya daftar ini tidak pernah berubah setelah
                        # dibangun, sehingga gate "maks 1 posisi per simbol"
                        # di bawah tetap melihat posisi yang baru saja
                        # recommends ditutup. Akibatnya reversal tidak bisa
                        # diikuti entry baru di simbol yang sama sampai
                        # siklus berikutnya — dan kalau cooldown masih aktif,
                        # bisa beberapa siklus lagi. Penutupan di bawah tidak
                        # dijamin terjadi: kalau klaim-nya kalah duel dengan
                        # SL/TP dari `update_positions`, posisi tetap terbuka dan
                        # entry di bawah harus tetap ditolak. Karena itu
                        # penghapusan dilakukan di sini, mengikuti rencana
                        # penutupan pada siklus ini, dan
                        # `self._symbol_cooldowns` tetap menjadi pagar kedua.
                        closing_symbols.add(symbol)

        for sym in closing_symbols:
            open_symbols.pop(sym, None)

        # OPEN: buka posisi baru (batch, max 1 posisi per simbol)
        opened = 0
        chosen_symbols = set()
        for symbol, analysis, sig in scalp_signals:
            if opened >= batch_limit:
                break

            # Hindari multiple entry simbol yang sama dalam 1 batch
            if symbol in chosen_symbols:
                continue

            # Cooldown per simbol setelah posisi ditutup
            if now < self._symbol_cooldowns.get(symbol, 0):
                continue

            # Cek confidence minimum
            if sig["strength"] < self.scalp.min_confidence:
                continue

            # Cek spread
            ob = market_store.get_order_book(symbol)
            if ob:
                bids = ob.get("bids", [])
                asks = ob.get("asks", [])
                if bids and asks:
                    mid = (bids[0][0] + asks[0][0]) / 2
                    spread_pct = (asks[0][0] - bids[0][0]) / mid if mid > 0 else 1
                    if spread_pct > self.scalp.max_spread_pct:
                        continue

            # Max 1 posisi per simbol agar tidak menumpuk risiko
            sym_count = len(open_symbols.get(symbol, []))
            if sym_count >= 1:
                continue

            side = Side.LONG if sig["direction"] == "BULLISH" else Side.SHORT
            action = TradeAction.OPEN_LONG if side == Side.LONG else TradeAction.OPEN_SHORT

            leverage = self._determine_leverage(
                analysis.get("fundamental", {}).get("risk_level", "LOW")
            )

            decisions.append(TradeDecision(
                action=action,
                symbol=symbol,
                side=side,
                confidence=sig["strength"],
                leverage=leverage,
                stop_loss_pct=self.scalp.tight_sl_pct,
                take_profit_pct=self.scalp.fast_tp_pct,
                reasoning=sig["reason"],
                signals=sig,
            ))
            opened += 1
            chosen_symbols.add(symbol)
            reasoning_parts.append(
                f"{action.value} {symbol} str={sig['strength']:.0%}"
            )

        reasoning = " | ".join(reasoning_parts) if reasoning_parts else f"Scanned {len(analyses)} simbol — no entry"

        return {
            "decisions": decisions,
            "reasoning": reasoning,
            "summary": reasoning,
            "input_summary": {"symbols": list(analyses.keys()), "open": open_count},
            "output_summary": {
                "new_positions": opened,
                "closes": sum(1 for d in decisions if d.action == TradeAction.CLOSE),
            },
        }

    async def act(self, analysis: dict):
        decisions: List[TradeDecision] = analysis.get("decisions", [])

        # Saldo dibaca SEKALI per siklus, bukan per order, karena `get_account`
        # adalah query SQLite dan `act()` bisa memproses seluruh batch order
        # dalam satu siklus. Pada jalur non-scalp, saldo ikut menentukan
        # quantity, jadi membacanya berulang hanya menambah beban tanpa
        # mengubah hasil.
        repo = await self._get_repo()
        account = await repo.get_account()
        balance = None
        if account is not None:
            try:
                balance = float(account["balance"])
            except (KeyError, TypeError, ValueError):
                balance = None

        cfg = self.config
        scalp_on = bool(getattr(cfg.scalping, "enabled", False))

        # Hitung dari yang benar-benar DIPUBLISKAS, bukan dari `decisions`.
        # Ringkasan di bawah tidak boleh mengklaim posisi yang dilewati
        # gerbang validitas — kalau tidak, log berbulan-bulan menyebut bot
        # membuka 3 posisi padahal tidak satu pun sampai ke bursa.
        opens = 0
        closes = 0

        for decision in decisions:
            if decision.action == TradeAction.HOLD:
                continue

            order = Order(
                symbol=decision.symbol,
                action=decision.action,
                side=decision.side,
                leverage=decision.leverage,
                stop_loss=None,
                take_profit=None,
                reasoning=decision.reasoning,
            )

            if decision.action in (TradeAction.OPEN_LONG, TradeAction.OPEN_SHORT):
                # ── Ukuran posisi dihitung DI SINI ────────────────────────
                #
                # `Order.quantity` yang None dulu berarti "biarkan engine yang
                # hitung". Jalur itu hanya hidup di paper engine
                # (paper_engine.py:517-535) karena ia baru tahu harga fill
                # saat eksekusi. LiveExecutor tidak punya jalur itu: ia
                # mengirim `size=order.quantity or 0.0` ke bursa, jadi None
                # menjadi size 0 — order yang benar-benar terkirim dengan
                # ukuran nol. Jadi `act()` adalah satu-satunya titik yang
                # dilewati SEMUA order sebelum sampai ke engine mana pun, dan
                # hanya titik ini yang juga memegang RiskManager yang sama
                # dengan kedua engine.
                #
                # Urutan langkah di bawah mengikuti paper engine persis.
                # Mengubah urutan atau menggunakan angka lain di sini membuat
                # paper dan live menghitung ukuran berbeda untuk sinyal yang
                # sama — selisih yang tidak terlihat sampai ukurannya meleset
                # jauh.
                # Harga & leverage dikonversi dalam try. Feed bisa menulis
                # apa saja ke market_store, dan exception yang lolos dari sini
                # akan mematikan seluruh siklus agen — bukan hanya order ini.
                try:
                    price = float(market_store.get_price(decision.symbol) or 0.0)
                except (TypeError, ValueError):
                    price = 0.0
                side_str = "LONG" if decision.action == TradeAction.OPEN_LONG else "SHORT"
                # Leverage dibaca dari config kalau kosong, lalu dipaksa jadi
                # int — nilai yang tidak bisa jadi int berakhir di gerbang
                # "leverage tidak valid" di bawah, bukan jadi exception.
                try:
                    leverage = int(decision.leverage or cfg.risk.default_leverage)
                except (TypeError, ValueError):
                    leverage = 0

                if not math.isfinite(price) or price <= 0:
                    self.logger.warning(
                        f"{decision.action.value} {decision.symbol} dilewati: "
                        f"tidak ada harga pasar (harga={price})"
                    )
                    continue

                if leverage < 1:
                    self.logger.warning(
                        f"{decision.action.value} {decision.symbol} dilewati: "
                        f"leverage tidak valid (leverage={leverage})"
                    )
                    continue

                if balance is None:
                    self.logger.warning(
                        f"{decision.action.value} {decision.symbol} dilewati: "
                        f"akun belum diinisialisasi"
                    )
                    continue

                sl_pct = float(decision.stop_loss_pct or cfg.scalping.tight_sl_pct)
                tp_pct = float(decision.take_profit_pct or cfg.scalping.fast_tp_pct)
                stop_loss = self.risk_manager.calculate_stop_loss(price, side_str, sl_pct)
                take_profit = self.risk_manager.calculate_take_profit(price, side_str, tp_pct)

                if scalp_on:
                    sizing = self.risk_manager.calculate_scalp_position_size(
                        balance=balance, entry_price=price, leverage=leverage
                    )
                else:
                    # SL/TP untuk sizing di sini sengaja memakai
                    # `_NON_SCALP_*`, bukan `sl_pct`/`tp_pct` di atas —
                    # sama seperti paper_engine.py:477-478. Pakai pct scalp di
                    # sini membuat kedua jalur tidak lagi menghasilkan
                    # angka yang sama.
                    stop_loss = self.risk_manager.calculate_stop_loss(
                        price, side_str, _NON_SCALP_SL_PCT
                    )
                    take_profit = self.risk_manager.calculate_take_profit(
                        price, side_str, _NON_SCALP_TP_PCT
                    )
                    sizing = self.risk_manager.calculate_position_size(
                        balance=balance,
                        entry_price=price,
                        stop_loss_price=stop_loss,
                        leverage=leverage,
                    )
                quantity = float(sizing["quantity"])

                # Gerbang validitas terakhir sebelum order menyentuh bursa.
                #
                # Kenapa wajib di sini dan bukan "biarkan engine yang menolak":
                # quantity NaN lolos ke Hyperliquid tanpa nama parameter yang
                # jelas, dan SL di atas entry pada LONG adalah stop instan yang
                # dieksekusi bursa dalam hitungan milidetik — di live itu
                # rugi nyata di detik pertama. Ukuran fiktif yang lolos hanya
                # akan berubah jadi order sungguhan.
                problems = []
                if not math.isfinite(quantity) or quantity <= 0:
                    problems.append(f"quantity tidak valid ({quantity})")
                if not math.isfinite(stop_loss) or stop_loss <= 0:
                    problems.append(f"stop loss tidak valid ({stop_loss})")
                if not math.isfinite(take_profit) or take_profit <= 0:
                    problems.append(f"take profit tidak valid ({take_profit})")
                if side_str == "LONG" and not (stop_loss < price < take_profit):
                    problems.append(
                        f"SL/TP terbalik untuk LONG (sl={stop_loss}, "
                        f"price={price}, tp={take_profit})"
                    )
                if side_str == "SHORT" and not (take_profit < price < stop_loss):
                    problems.append(
                        f"SL/TP terbalik untuk SHORT (sl={stop_loss}, "
                        f"price={price}, tp={take_profit})"
                    )

                if problems:
                    self.logger.warning(
                        f"{decision.action.value} {decision.symbol} dilewati: "
                        f"{'; '.join(problems)}"
                    )
                    continue

                order.quantity = quantity
                order.leverage = leverage
                order.stop_loss = stop_loss
                order.take_profit = take_profit
                order.price = price
                opens += 1

            await self.publish(Channels.TRADE_DECISION, {
                "order": order,
                "decision": decision,
                "stop_loss_pct": decision.stop_loss_pct,
                "take_profit_pct": decision.take_profit_pct,
                # Key baru: sizing harus bisa diaudit dari luar. Tanpa ini
                # tidak ada jejak angka yang benar-benar dikirim ke bursa.
                "quantity": order.quantity,
                "entry_price": order.price,
            })
            if decision.action == TradeAction.CLOSE:
                closes += 1

        # Log ringkas (tidak spam per posisi)
        if opens or closes:
            self.logger.info(
                f"Scalp: {opens} posisi baru, {closes} ditutup"
            )

    async def _generate_scalp_signals(
        self, symbol: str, analysis: Dict
    ) -> List[Dict]:
        """
        Ambil sinyal arah dari snapshot ensemble LONG/SHORT.

        Snapshot ini ditulis oleh `DirectionEnsembleAgent` dan dibaca juga
        oleh HUD, sehingga angka yang menentukan entry sama persis dengan
        angka yang tampil di layar — tidak ada lagi dua jalur sinyal yang
        bisa berbeda.

        Data arah yang basi lebih berbahaya daripada tidak ada data, jadi
        snapshot yang lebih tua dari `max_snapshot_age_seconds` ditolak.
        """
        repo = await self._get_repo()
        snap = await repo.get_latest_direction_snapshot(symbol)
        if not snap:
            return []

        age_ok = self._snapshot_is_fresh(snap.get("created_at"))
        if not age_ok:
            self.logger.debug(
                f"Snapshot arah {symbol} terlalu lama — entry dilewati"
            )
            return []

        direction = str(snap.get("direction") or "NEUTRAL").upper()
        if direction not in ("LONG", "SHORT"):
            return []

        strength = float(snap.get("confidence") or 0.0)
        if strength < float(self.scalp.min_confidence):
            return []

        prob_long = float(snap.get("prob_long") or 0.5)
        prob_short = float(snap.get("prob_short") or 0.5)
        agree = self._snapshot_agreement(snap)
        agree_txt = f" · {agree}" if agree else ""

        return [{
            "direction": "BULLISH" if direction == "LONG" else "BEARISH",
            "strength": strength,
            "source": "ensemble",
            "reason": (
                f"ensemble {direction} {strength:.0%} "
                f"(L {prob_long:.2f} / S {prob_short:.2f}){agree_txt}"
            ),
            "ensemble_direction": direction,
            "ensemble_confidence": strength,
        }]

    def _snapshot_is_fresh(self, created_at) -> bool:
        """True bila snapshot masih dalam batas umur yang dikonfigurasi."""
        ts = parse_db_timestamp(created_at)
        if ts <= 0:
            return False
        max_age = float(self.config.ensemble.max_snapshot_age_seconds)
        return (time.time() - ts) <= max_age

    @staticmethod
    def _snapshot_agreement(snap: Dict) -> str:
        """Ringkas berapa agen yang mendukung arah akhir, mis. '3/4 agree'."""
        raw = snap.get("agent_breakdown")
        if not raw:
            return ""
        try:
            entries = json.loads(raw)
        except (TypeError, ValueError):
            return ""
        if not entries:
            return ""

        final = str(snap.get("direction") or "").upper()
        active = [
            e for e in entries
            if str(e.get("direction", "")).upper() in ("LONG", "SHORT")
        ]
        if not active:
            return ""
        agree = sum(
            1 for e in active
            if str(e.get("direction")).upper() == final
        )
        return f"{agree}/{len(active)} agree"

    def _determine_leverage(self, risk_level: str) -> int:
        default = self.config.risk.default_leverage
        if risk_level == "HIGH":
            return max(2, default // 2)
        elif risk_level == "MEDIUM":
            return max(3, default * 3 // 4)
        return default
