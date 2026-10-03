"""
trading/live/executor.py — Adapter supaya `ExecutionAgent` tidak perlu
tahu beda paper dan live.

`ExecutionAgent` sekarang hanya bisa bicara ke `PaperTradingEngine`.
Kalau live disambungkan langsung di sana, satu kelas jadi dua mesin
dengan dua cara failure berbeda. `LiveExecutor` memenuhi antarmuka
yang sama (`execute_order`, `update_price`, `get_price`,
`check_positions`, `position_manager`) sehingga agent-nya tidak
perlu diubah.

Perbedaan safety hidup di DALAM adapter: setiap order live melewati
gerbang, dan kegagalan mengembalikan dict yang jelas -- bukan melempar
exception ke loop yang sedang berjalan.

Dua aturan yang tidak boleh dilanggar di file ini:

1. **Bursa adalah sumber kebenaran; SQLite hanya cermin.** Baris
   `positions` yang ditulis di sini tidak pernah dipakai untuk
   memutuskan apa yang dikirim ke bursa. Kalau angkanya tidak bisa
   dipercaya (harga tidak ada, size bursa tidak cocok, fill tanpa
   harga entry), hasilnya ditulis ke log -- bukan ke database. Angka
   karangan lebih berbahaya daripada tidak ada angka sama sekali,
   karena ia terlihat benar di dashboard.

2. **`account.balance` tidak pernah disentuh jalur live.** Saldo ada
   di bursa, dan ledger lokal tidak bisa melihat funding, deposit,
   order dari luar, maupun likuidasi sisi bursa. Tidak ada
   `apply_balance_delta`, tidak ada refund margin, tidak ada
   `update_balance`.
"""
from __future__ import annotations

import asyncio
import math
import time
import uuid
from typing import Any, Dict, Optional

from core.event_bus import Channels
from core.logger import get_logger
from core.market_store import market_store
from database.db import get_db
from database.models import AgentLog, Position, Trade
from database.repository import Repository
from trading.live.engine import LiveEngine
from trading.risk_manager import RiskManager

logger = get_logger("live_executor")

# Jarak minimum antara dua kali perbaikan drift. `ExecutionAgent`
# memanggil `check_positions` tiap ~0,3 detik; membaca mid map dari
# bursa pada frekuensi itu membakar kuota request Hyperliquid tanpa
# menambah kebenaran apa pun. 2 detik masih jauh lebih cepat daripada
# selisih yang perlu dibaca, dan biayanya kecil dibanding satu request
# ccxt yang gagal.
_REFRESH_SECONDS = 2.0


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


def _finite_positive(value: Any) -> bool:
    """
    True hanya untuk angka finit yang benar-benar positif.

    `bool(float("nan"))` bernilai True, jadi cek `if price:` pada kode
    lama membiarkan NaN lolos ke `submit_order` -- dan pesannya hanya
    menyebut "harga <= 0" tanpa menyebut NaN, jadi penyebabnya tidak
    pernah terbaca.
    """
    try:
        f = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(f) and f > 0.0


class LiveExecutor:
    """Adapter order untuk jalur live."""

    def __init__(self, engine: LiveEngine, event_bus=None):
        self.engine = engine
        # `event_bus` opsional supaya `LiveExecutor(_Engine(rec))` di test
        # tetap jalan tanpa bus. `None` berarti "jangan publikasikan
        # apa pun" -- itulah sebabnya `run.py` WAJIB mengoper
        # `self.event_bus` ke sini, kalau tidak dashboard tidak pernah
        # melihat satu pun posisi live.
        self.event_bus = event_bus
        self.risk_manager = RiskManager()
        self._last_prices: Dict[str, float] = {}
        self._repo: Optional[Repository] = None
        self._last_refresh = 0.0
        self._publish_warned = False
        self._ledger_warned = False
        self.position_manager = _LivePositionManager(self)

    # ── Plumbing ───────────────────────────────────────────────────────

    async def _get_repo(self) -> Repository:
        if self._repo is None:
            db = await get_db()
            self._repo = Repository(db)
        return self._repo

    async def _publish(self, channel: str, data: Dict[str, Any],
                       source: str) -> None:
        """
        Publikasikan event kalau bus ada.

        Kegagalan publish tidak boleh menjatuhkan fill yang sudah
        terjadi di bursa: yang hilang cuma tampilan dashboard, dan itu
        jauh lebih murah daripada exception yang menggagalkan pencatatan
        posisi.
        """
        if self.event_bus is None:
            if not self._publish_warned:
                self._publish_warned = True
                logger.debug(
                    "LiveExecutor dibuat tanpa event bus; event tidak "
                    "dipublikasikan. run.py harus mengoper self.event_bus "
                    "supaya dashboard melihat posisi live."
                )
            return
        try:
            await self.event_bus.publish(channel, data, source=source)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Gagal publish ke %s: %s", channel, exc)

    async def _log_agent(self, log: AgentLog) -> None:
        """
        Tulis jejak audit.

        Kegagalan menulis TIDAK boleh menghapus pencatatan fill yang
        sudah terjadi, jadi exception-nya ditelan di sini.
        """
        try:
            repo = await self._get_repo()
            await repo.insert_agent_log(log)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Gagal menulis agent_log: %s", exc)

    def _warn_shared_ledger(self) -> None:
        """
        Peringatkan SATU KALI bahwa ada dua buku besar yang berbeda.

        `account.balance` adalah ledger paper. Posisi live masuk ke
        tabel `positions` yang sama, jadi rumus dashboard
        `wallet_balance = balance + SUM(margin)` tidak lagi cocok untuk
        baris live. Yang dilakukan di sini adalah menempelkan kenyataan
        itu di terminal; menulis angka hasil tebakan ke `balance` akan
        merusak pembukuan paper tanpa memperbaiki apa pun.
        """
        if self._ledger_warned:
            return
        self._ledger_warned = True
        logger.warning(
            "Live: posisi dicatat di tabel `positions` yang sama dengan "
            "paper, TETAPI `account.balance` tetap ledger paper dan tidak "
            "pernah disentuh jalur live. Rumus dashboard "
            "`wallet_balance = balance + SUM(margin)` karena itu tidak "
            "lagi cocok untuk baris live. Angka wallet sebenarnya "
            "dicetak di setiap fill/close dari `free_collateral` bursa."
        )

    async def _log_wallet(self, label: str) -> None:
        """
        Cetak angka wallet yang dibaca dari bursa.

        Dipakai supaya nominal sungguhan ada di terminal, karena SQLite
        tidak menyimpannya. Keduanya diambil dalam satu `to_thread` agar
        tidak memblokir loop event.
        """
        def fetch():
            return (self.engine.exchange.free_collateral(),
                    self.engine.exchange.total_notional())

        try:
            free, notional = await asyncio.to_thread(fetch)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Gagal membaca angka wallet bursa: %s", exc)
            return
        logger.info(
            "Wallet bursa [%s]: free_collateral=%.2f total_notional=%.2f "
            "(SQLite tidak menyimpan angka ini)",
            label, free, notional,
        )

    # ── Antarmuka yang dipakai ExecutionAgent ──────────────────────────

    def update_price(self, symbol: str, price: float) -> None:
        """
        Simpan harga terakhir ke cache lokal.

        Dulu fungsi ini `return None` dengan alasan "live tidak butuh
        cache harga". Itu tidak lagi benar: `ExecutionAgent` membaca
        `engine._last_prices` sebagai sumber harga di tiga tempat --
        `_protect_breakeven`, `_scalp_take_profit`, dan
        `_auto_close_expired` (execution_agent.py:213/266/290). Tanpa
        cache, `check_positions()` pada live akan menabrak
        `AttributeError` dan ketiga policy itu diam-diam tidak pernah
        jalan sama sekali.

        Ini bukan sumber kebenaran kedua: nilainya SELALU berasal dari
        feed bursa (`market_store` diisi dari `all_mids` Hyperliquid),
        dan hanya dipakai sebagai fallback saat store belum punya
        harga. Yang tersimpan di sini tidak pernah menolak order --
        pemeriksaannya sengaja lebih ketat dari paper karena
        avanzadasnya hanya bisa MENOLAK.
        """
        try:
            p = float(price)
        except (TypeError, ValueError):
            return
        if math.isfinite(p) and p > 0:
            self._last_prices[symbol] = p

    def get_price(self, symbol: str) -> Optional[float]:
        """
        Harga terbaru: cache dulu, baru `market_store`.

        Urutan ini SALINAN PERSIS dari `PaperTradingEngine.get_price`
        (paper_engine.py:159-179) dan itu disengaja. Dua mesin yang
        menghitung sizing dan stop dari sumber harga berbeda akan
        menghasilkan posisi berbeda untuk keputusan yang sama.

        Kalau tidak ada harga, hasilnya `None` -- bukan 0.0. `0.0` itu
        bentuk "tidak bisa dipakai" yang terlihat seperti harga.
        """
        cached = self._last_prices.get(symbol)
        if cached and cached > 0:
            return cached

        price = market_store.get_price(symbol)
        if price and price > 0:
            self._last_prices[symbol] = price
            return price
        return None

    async def check_positions(self) -> None:
        """
        Perbaiki drift antara baris SQLite dan yang sebenarnya di bursa.

        Ini BUKAN no-op, dan posisinya dalam urutan panggilan itu sendiri
        yang menentukan: `ExecutionAgent.check_positions()` menjalankan
        `_protect_breakeven` DULUAN -- yang menulis SL breakeven ke setiap
        baris OPEN termasuk baris live -- dan baru sesudahnya memanggil
        `self.engine.check_positions()`. Jadi perbaikan yang diletakkan di
        sini berjalan SESUDAH tulisan lokal itu, dan karena itu menang.

        Yang diperbaiki hanya PnL unrealized dan SL/TP lokal yang
        menyimpang dari trigger yang benar-benar duduk di bursa. Tidak
        ada yang DITUTUP di sini: menutup posisi karena "berbeda"
        berarti menebak, dan menebak posisi adalah cara paling umum untuk
        menutup exposure orang lain lalu menyebutnya merapikan.
        """
        now = time.monotonic()
        if now - self._last_refresh < _REFRESH_SECONDS:
            return
        self._last_refresh = now

        # Satu kali baca blocking ke bursa, seluruh mid map sekaligus.
        # Membaca per koin akan mengulang request yang sama puluhan kali
        # pada portofolio multi-simbol.
        try:
            mids = await asyncio.to_thread(self._engine_all_mids)
        except Exception as exc:  # noqa: BLE001
            # Pembacaan bursa yang gagal adalah error bursa. Dan TIDAK
            # boleh menyentuh database: repairing dengan data setengah
            # jadi berarti menimpa angka yang masih benar.
            self.engine.gate.record_error()
            logger.error("Gagal membaca mid price bursa: %s", exc)
            return

        repo = await self._get_repo()
        rows = await repo.get_open_positions()

        # Index berdasarkan KOIN, bukan symbol.
        #
        # `normalize_symbol()` (engine.py) sekarang menjamin SEMUA kunci
        # `self.positions` berformat sama, jadi pencocokan per symbol
        # sudah bisa dipakai. Index per koin tetap dipakai di sini
        # sebagai lapisan kedua: nama koin dari bursa ("BTC") adalah
        # bentuk yang paling murah untuk dicocokkan, dan ia tahan kalau
        # suatu saat format internal berubah lagi.
        lp_by_coin = {p.coin.upper(): p for p in self.engine.positions.values()}
        seen_coins = set()

        for row in rows:
            coin = coin_of(row.get("symbol") or "").upper()
            seen_coins.add(coin)
            lp = lp_by_coin.get(coin)
            if lp is None:
                # Tidak ada di bursa ATAU tidak pernah ada. Dua hal itu
                # dibedakan oleh `LiveEngine.reconcile`, dan keduanya
                # milik operator: baris dibiarkan OPEN supaya tetap
                # terlihat, dan TIDAK ditutup dengan harga tebakan.
                logger.warning(
                    "Posisi #%s %s %s qty=%s entry=%s tercatat OPEN di "
                    "SQLite tapi TIDAK ada di engine live: baris dibiarkan "
                    "OPEN, resolution-nya milik LiveEngine.reconcile / "
                    "operator. Menutupnya di sini berarti menebak harga.",
                    row.get("id"), row.get("symbol"), row.get("side"),
                    row.get("quantity"), row.get("entry_price"),
                )
                continue

            entry = float(row.get("entry_price") or 0.0)
            qty = float(row.get("quantity") or 0.0)
            if not _finite_positive(entry) or not _finite_positive(qty):
                logger.warning(
                    "Posisi #%s %s dilewati: entry_price=%s quantity=%s "
                    "tidak bisa dipakai menghitung PnL",
                    row.get("id"), row.get("symbol"),
                    row.get("entry_price"), row.get("quantity"),
                )
                continue

            mark = float((mids or {}).get(coin) or 0.0)
            if not _finite_positive(mark):
                # Fallback ke feed lokal dengan aturan yang sama: tanpa
                # harga yang jujur, baris ini dilewati saja.
                fallback = market_store.get_price(row.get("symbol") or "")
                if not fallback or not _finite_positive(fallback):
                    logger.debug(
                        "Posisi #%s %s dilewati: tidak ada harga mid "
                        "untuk menandai PnL", row.get("id"), coin)
                    continue
                mark = float(fallback)

            pnl = self.risk_manager.calculate_pnl(
                row.get("side"), entry, mark, qty)["pnl"]
            await repo.update_position_pnl(row["id"], pnl)

            # SL/TP lokal disamakan dengan yang ADA DI BURSA, bukan
            # sebaliknya. Trigger yang benar-benar akan mengeksekusi
            # adalah yang duduk di bursa. Menaruh ulang trigger dari loop
            # 0,3 detik berarti mengirim order NYATA dari policy loop,
            # sedangkan menyalin angka bursa ke lokal hanya membuang
            # kebohongan dari dashboard.
            local_sl = round(float(row.get("stop_loss") or 0.0), 8)
            local_tp = round(float(row.get("take_profit") or 0.0), 8)
            live_sl = round(float(lp.stop_loss or 0.0), 8)
            live_tp = round(float(lp.take_profit or 0.0), 8)
            if local_sl != live_sl or local_tp != live_tp:
                await repo.update_position_sl_tp(
                    row["id"],
                    stop_loss=lp.stop_loss, take_profit=lp.take_profit,
                )
                # DEBUG, bukan INFO: `_protect_breakeven` mengotori ulang
                # kolom SL ini tiap 0,3 detik, jadi di INFO setiap
                # perbaikan jadi derau yang menutupi yang penting.
                logger.debug(
                    "SL/TP diselaraskan ke bursa untuk #%s %s: "
                    "SL %s -> %s, TP %s -> %s",
                    row.get("id"), coin, local_sl, live_sl,
                    local_tp, live_tp,
                )

        for coin in sorted(set(lp_by_coin) - seen_coins):
            # Posisi di bursa tanpa catatan lokal: tidak ada yang memasang
            # atau memantau proteksinya. `health_check` (engine.py:575)
            # melaporkannya dengan cara yang sama. Baris TIDAK disisipkan
            # di sini -- posisi itu mungkin milik operator, bukan bot.
            logger.error(
                "Posisi di bursa tanpa catatan lokal: %s "
                "(proteksinya tidak dipantau oleh bot)",
                coin,
            )

    def _engine_all_mids(self) -> Dict[str, Any]:
        """
        Akses internal yang sama dengan `check_pending_fills`
        (engine.py:439), supaya ada hanya satu cara membaca mid map di
        jalur live.
        """
        return self.engine.exchange.exchange.all_mids() or {}

    # ── Order ──────────────────────────────────────────────────────────

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

        # GERBANG TERAKHIR SEBELUM UANG ASLI BERGERAK.
        #
        # Sebelumnya `size=order.quantity or 0.0` dan
        # `leverage=order.leverage or 5`. `or 0.0` meneruskan quantity
        # `None` -- itu default `Order` -- apa adanya ke bursa, dan
        # default leverage 5 diam-diam mengarang angka untuk order yang
        # tidak pernah menyatakannya.
        #
        # `DecisionAgent` sekarang selalu mengirim quantity dan leverage
        # yang valid, jadi cabang ini SEHARUSNYA tidak terjangkau di
        # produksi. Ia tetap ada supaya pemanggil lain, dan regresi apa
        # pun di hulu, tidak bisa mengirim `size=0.0` ke Hyperliquid.
        # Tidak ada ukuran default yang aman, jadi tidak ada yang ditebak
        # di sini.
        if not _finite_positive(order.quantity):
            logger.warning(
                "%s %s ditolak: quantity=%r tidak valid",
                order.symbol, order.action, order.quantity,
            )
            return {
                "success": False,
                "message": "order live butuh quantity yang valid; tidak "
                           "ada ukuran default yang aman",
                "position_id": None,
                "details": {"blockers": ["invalid_quantity"]},
            }
        lev = order.leverage
        if not isinstance(lev, int) or isinstance(lev, bool) or lev < 1:
            logger.warning(
                "%s %s ditolak: leverage=%r bukan int >= 1",
                order.symbol, order.action, lev,
            )
            return {
                "success": False,
                "message": "order live butuh leverage int >= 1",
                "position_id": None,
                "details": {"blockers": ["invalid_leverage"]},
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
            size=float(order.quantity),
            price=price,
            stop_loss=order.stop_loss,
            take_profit=order.take_profit,
            leverage=lev,
            cloid=cloid,
        )

        if not result.get("success"):
            # Penolakan gerbang BUKAN error bursa. `submit_order` sudah
            # memanggil `record_error()` di jalur error-nya sendiri
            # (engine.py:288/299/307); menambah satu di sini akan
            # menghitung tiga penolakan aman sebagai tiga error beruntun
            # dan menyalakan kill switch pada sistem yang justru benar.
            logger.warning("Order live %s %s ditolak: %s", order.symbol,
                           order.action, result.get("message", "ditolak"))
            return {"success": False,
                    "message": result.get("message", "ditolak"),
                    "position_id": None, "details": result}

        if not result.get("protected"):
            # Resting. Bursa belum mengisi, jadi tidak ada yang BENAR
            # untuk ditulis: quantity, harga, dan fee semuanya masih
            # bara. Baris yang dicatat di sini akan nol.
            return {"success": True,
                    "message": "order resting (belum terisi), cloid={}"
                               .format(cloid),
                    "position_id": None, "details": result}

        return await self._persist_open(
            order, result, price, cloid, is_buy, lev)

    async def _persist_open(self, order, result: Dict[str, Any],
                            price: float, cloid: str, is_buy: bool,
                            lev: int) -> Dict[str, Any]:
        """
        Tulis fill live yang sudah terisi ke SQLite, lalu brook event.

        Kalau tidak, baris di database tidak pernah ada: dashboard
        menampilkan account kosong sementara ada uang sungguhan yang
        sedang terekspos, dan `ExecutionAgent` -- yang hanya melihat baris
        OPEN -- tidak akan pernah menjalankan scalping TP, lock breakeven,
        maupun auto-close untuk posisi itu.

        Yang ditulis adalah angka BURSA: `outcome.filled_size` dan
        `outcome.avg_price`, bukan `order.quantity` dan `order.price`.
        Order bisa terisi sebagian, dan order kita bukan kenyataan.
        """
        pos = result.get("position")
        outcome = result.get("outcome")
        qty = float(getattr(outcome, "filled_size", 0.0) or 0.0)
        entry = float(getattr(outcome, "avg_price", 0.0) or 0.0) or float(price)

        if not _finite_positive(qty) or not _finite_positive(entry):
            # Fill tanpa qty/entry yang bisa dipercaya TIDAK ditulis.
            # Menyimpan tebakan berarti menampilkan posisi di angka yang
            # tidak pernah ada di bursa.
            logger.warning(
                "%s terisi tapi qty/entry tidak bisa dipercaya "
                "(qty=%s entry=%s): tidak ditulis ke SQLite, rekonsiliasi "
                "manual diperlukan.", order.symbol, qty, entry,
            )
            return {"success": True,
                    "message": "order terisi tapi fill tidak bisa dicatat "
                               "(qty/entry tidak valid), cloid={}".format(
                                   cloid),
                    "position_id": None, "details": result}

        self._warn_shared_ledger()
        side = getattr(pos, "side", None) or ("LONG" if is_buy else "SHORT")
        lev = int(getattr(pos, "leverage", 0) or lev)
        liq = self.risk_manager.calculate_liquidation_price(entry, side, lev)
        # Aritmetika margin sama dengan `PositionManager.open_position`
        # (position_manager.py:67) supaya baris live dibandingkan
        # apples-to-apples dengan baris paper.
        margin = qty * entry / lev
        fee = self.risk_manager.calculate_fee(qty, entry, "TAKER")
        reasoning = "LIVE " + (order.reasoning or "")

        try:
            repo = await self._get_repo()
            row_id = await repo.insert_position(Position(
                symbol=order.symbol, side=side, entry_price=entry,
                quantity=qty, leverage=lev, margin=margin,
                liquidation_price=liq,
                stop_loss=getattr(pos, "stop_loss", None),
                take_profit=getattr(pos, "take_profit", None),
                status="OPEN",
                reasoning=reasoning,
                # Prefix "LIVE " adalah penanda yang membuat baris live
                # tidak pernah tertukar dengan simulasi: satu query
                # "posisi mana yang uang sungguhan" harus menemukan
                # semuanya.
                mode="live",
            ))
        except Exception as exc:  # noqa: BLE001
            logger.critical(
                "Fill live %s terisi tapi GAGAL dicatat ke SQLite: %s. "
                "Dashboard buta terhadap uang sungguhan -- tutup manual.",
                order.symbol, exc,
            )
            self.engine.gate.engage_kill_switch(
                "gagal mencatat fill live ke SQLite untuk {}".format(
                    order.symbol))
            return {"success": True,
                    "message": "posisi TERISI di bursa tapi gagal dicatat "
                               "lokal; kill switch aktif",
                    "position_id": None, "details": result}

        if row_id is None:
            logger.critical(
                "Fill live %s terisi tapi insert_position mengembalikan "
                "None: tidak ada catatan lokal untuk uang sungguhan.",
                order.symbol,
            )
            self.engine.gate.engage_kill_switch(
                "gagal mencatat fill live ke SQLite untuk {}".format(
                    order.symbol))
            return {"success": True,
                    "message": "posisi TERISI di bursa tapi gagal dicatat "
                               "lokal; kill switch aktif",
                    "position_id": None, "details": result}

        try:
            await repo.insert_trade(Trade(
                symbol=order.symbol,
                side="BUY" if side == "LONG" else "SELL",
                price=entry, quantity=qty, fee=fee, fee_type="TAKER",
                trade_type="OPEN", position_id=row_id,
                mode="live",
            ))
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Trade OPEN live %s gagal dicatat: %s (posisi #%s tetap "
                "ada; hanya fee yang hilang dari jejak)",
                order.symbol, exc, row_id,
            )

        await self._publish(Channels.POSITION_UPDATE, {
            "action": "OPENED",
            "position_id": row_id,
            "symbol": order.symbol,
            "side": side,
            "entry_price": entry,
            "quantity": qty,
            "leverage": lev,
            "margin": margin,
            "fee": fee,
            "live": True,
        }, source="live_executor")

        await self._publish(Channels.TRADE_EXECUTED, {
            "action": "OPEN",
            "position_id": row_id,
            "symbol": order.symbol,
            "side": side,
            "price": entry,
            "quantity": qty,
            "leverage": lev,
            "stop_loss": getattr(pos, "stop_loss", None),
            "take_profit": getattr(pos, "take_profit", None),
            "reasoning": reasoning,
            "live": True,
            "cloid": cloid,
        }, source="live_executor")

        await self._log_agent(AgentLog(
            agent_name="live_executor",
            action="TRADE_EXECUTED",
            reasoning=("LIVE buka {} {} @ {} qty={} lev={}x cloid={}".format(
                side, order.symbol, entry, qty, lev, cloid)),
            input_data=str({"order": str(order)}),
            output_data=str({"position_id": row_id, "margin": margin,
                             "fee": fee, "cloid": cloid}),
        ))
        await self._log_wallet("open")

        details = dict(result)
        details["coin"] = coin_of(order.symbol)
        return {"success": True,
                "message": result.get("message", "posisi dibuka"),
                "position_id": row_id,
                "details": details}

    async def _close(self, order) -> Dict[str, Any]:
        """Tutup posisi yang tercatat di engine."""
        # `self.engine.positions` adalah SATU-SATUNYA sumber posisi di
        # sini. Tidak ada fallback ke bursa: `test_close_unknown_
        # position_refused` mengirim `positions={}` dan memastikan bursa
        # tidak pernah disentuh. Kalau entri lokal hilang, posisi itu
        # tidak dikenal bot, dan `LiveEngine.reconcile` yang punya
        # jawabannya.
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
        cloid = make_cloid("close")
        result = await self.engine.submit_order(
            coin=position.coin,
            symbol=order.symbol,
            is_buy=is_buy,
            size=position.size,
            price=limit,
            is_close=True,
            cloid=cloid,
        )
        if not result.get("success"):
            logger.warning("Penutupan live %s gagal: %s", order.symbol,
                           result.get("message", "ditolak"))
            return {"success": False,
                    "message": result.get("message", "ditolak"),
                    "position_id": None, "details": result}

        outcome = result.get("outcome")
        filled = float(getattr(outcome, "filled_size", 0.0) or 0.0)
        if filled <= 0:
            # `success` di sini berarti CONFIRMED FILLED, bukan "order
            # sudah dikirim". Order closing yang resting TIDAK menutup
            # apa pun, dan melaporkan_CLOSE lalu menutup baris sementara
            # posisi masih hidup adalah kebohongan yang paling berbahaya
            # di jalur ini. Order closing `reduce_only`, jadi percobaan
            # ulang tidak akan pernah membalik posisi.
            return {"success": False,
                    "message": "order closing RESTING (belum terisi), "
                               "cloid={}; posisi MASIH terbuka".format(cloid),
                    "position_id": None,
                    "details": dict(result, resting=True,
                                    coin=position.coin)}

        close_price = (float(getattr(outcome, "avg_price", 0.0) or 0.0)
                       or float(limit))
        row = await self._find_open_row(order.symbol)
        if row is None:
            logger.warning(
                "Posisi %s TERISI di bursa tapi tidak ada baris OPEN di "
                "SQLite: tidak ada yang bisa diperbarui. "
                "LiveEngine.reconcile / operator yang menyelesaikannya.",
                order.symbol,
            )
        else:
            await self._record_close(
                row_id=row["id"], symbol=row["symbol"],
                side=row.get("side") or position.side, size=filled,
                entry_price=float(row.get("entry_price") or 0.0),
                close_price=close_price, reason="SIGNAL", cloid=cloid,
            )
        await self._log_wallet("close")

        details = dict(result)
        details["coin"] = position.coin
        return {"success": True,
                "message": result.get("message", "closing dikirim"),
                "position_id": row["id"] if row else None,
                "details": details}

    async def _find_open_row(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Baris OPEN di SQLite untuk symbol ini, dicocokkan per KOIN."""
        repo = await self._get_repo()
        coin = coin_of(symbol).upper()
        for row in await repo.get_open_positions():
            if coin_of(row.get("symbol") or "").upper() == coin:
                return row
        return None

    # ── Penutupan yang tercatat ────────────────────────────────────────

    async def _record_close(
        self, *, row_id: int, symbol: str, side: str, size: float,
        entry_price: float, close_price: float, reason: str,
        cloid: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Tutup baris SQLite setelah bursa mengisi.

        Satu helper untuk kedua jalur penutupan -- `_close` dari order
        DecisionAgent dan `_LivePositionManager.close_position` dari
        policy scalping -- supaya tidak ada satu jalur pun yang menutup di
        bursa lalu meninggalkan baris OPEN selamanya.

        SELURUH body dibungkus try/except: kegagalan pencatatan tidak
        boleh menghilangkan fill yang sudah benar-benar terjadi.
        """
        # `realized_pnl: 0.0` untuk fill tanpa harga entry adalah angka
        # karangan yang terlihat sangat rapi di laporan PnL. Tidak ada
        # yang lebih baik daripada tidak menulis apa pun.
        if not _finite_positive(entry_price):
            logger.warning(
                "Fill live tercatat tanpa harga entry (posisi #%s %s): "
                "PnL tidak bisa dihitung, TIDAK ditulis.", row_id, symbol)
            return None
        if not _finite_positive(close_price):
            logger.warning(
                "Fill live tanpa harga close yang bisa dipakai (posisi "
                "#%s %s): TIDAK ditulis.", row_id, symbol)
            return None

        try:
            repo = await self._get_repo()
            gross = self.risk_manager.calculate_pnl(
                side, entry_price, close_price, size)
            fee_close = self.risk_manager.calculate_fee(
                size, close_price, "TAKER")

            # Fee PEMBUKAAN dibaca dari tabel `trades`, bukan dihitung
            # ulang: tarif taker bisa berubah di tengah sesi, dan
            # menghitung ulang menghasilkan angka yang berbeda dari yang
            # benar-benar dibayar (alasan yang sama seperti
            # position_manager.py:190-201).
            fee_open = 0.0
            for t in await repo.get_trades_by_position(row_id):
                if (t.get("trade_type") or "").upper() == "OPEN":
                    fee_open = float(t.get("fee") or 0.0)
                    break

            net = gross["pnl"] - fee_open - fee_close

            # Klaim-tunggal: `repo.close_position` hanya mengubah baris
            # yang masih OPEN (repository.py:147-169). `not claimed`
            # berarti pemanggil lain sudah menutupnya -- itu bukan
            # kegagalan, dan menutup dua kali akan menghitung PnL
            # dua kali.
            claimed = await repo.close_position(
                row_id, close_price, net, reason)
            if not claimed:
                logger.debug("Posisi #%s sudah diklaim pemanggil lain; "
                             "penutupan live tidak dicatat ulang.", row_id)
                return None

            await repo.insert_trade(Trade(
                symbol=symbol,
                side="SELL" if side == "LONG" else "BUY",
                price=close_price, quantity=size, fee=fee_close,
                fee_type="TAKER", trade_type="CLOSE", position_id=row_id,
                mode="live",
            ))

            # Statistik tanpa `balance`. `update_account_stats` memang
            # tidak menyentuh saldo, dan itu justru yang kita mau:
            # saldo live milik bursa.
            stats = await repo.get_trade_stats()
            await repo.update_account_stats(
                total_pnl=stats["total_pnl"],
                total_trades=stats["total_trades"],
                winning_trades=stats["winning_trades"],
                losing_trades=stats["losing_trades"],
                profit_factor=(stats["profit_factor"]
                               if stats["profit_factor"] != float("inf")
                               else 0),
            )

            # Pakan PERTAMA untuk `Blocker.DAILY_LOSS_LIMIT`.
            # `SafetyGate.record_realized_pnl` (safety.py:415) tidak
            # punya satu pun pemanggil di produksi, jadi batas rugi
            # harian itu tidak pernah bisa menyala dan live tidak punya
            # daily-loss breaker sama sekali. Panggilan inilah yang
            # memberinya sumber angka.
            self.engine.gate.record_realized_pnl(net)

            await self._publish(Channels.POSITION_UPDATE, {
                # String "CLOSED" bukan hiasan: `DecisionAgent.sense()`
                # (decision_agent.py:67) mengunci cooldown dan loss streak
                # persis dari string ini.
                "action": "CLOSED",
                "position_id": row_id,
                "symbol": symbol,
                "side": side,
                "close_price": close_price,
                "realized_pnl": net,
                "reason": reason,
                "live": True,
            }, source="live_executor")

            await self._log_agent(AgentLog(
                agent_name="live_executor",
                action="TRADE_CLOSED",
                reasoning=("Tutup LIVE {} {} @ {} PnL={:+.2f} "
                           "(fee buka {:.6f}, fee tutup {:.6f}, ROE {:+.2f}%) "
                           "alasan={}").format(
                               side, symbol, close_price, net, fee_open,
                               fee_close, gross["roe_pct"], reason),
                input_data=str({"cloid": cloid, "size": size,
                                "entry_price": entry_price}),
                output_data=str({"position_id": row_id,
                                 "gross_pnl": gross["pnl"],
                                 "net_pnl": net}),
            ))
            return {"realized_pnl": net,
                    "fee": fee_close,
                    "gross_pnl": gross["pnl"],
                    "roe_pct": gross["roe_pct"]}
        except Exception as exc:  # noqa: BLE001
            logger.error("Gagal mencatat penutupan live posisi #%s %s: %s",
                         row_id, symbol, exc)
            return None

    @staticmethod
    def _price_for(order) -> float:
        """
        Harga acuan untuk order opening.

        Kalau order tidak membawa harga, pakai harga pasar terakhir yang
        diketahui. `submit_order` menolak harga <= 0, jadi harga yang
        tidak ada harus gagal di sini dengan pesan jelas -- bukan
        terkirim sebagai 0 lalu ditolak bursa tanpa alasan.
        """
        # Uji finit DAN positif di kedua cabang. `if price:` dulu
        # membiarkan NaN lolos (`bool(nan)` True) dan hanya tertangkap
        # gerbang `price <= 0` dengan pesan yang tidak menyebut NaN sama
        # sekali, jadi penyebabnya tidak pernah terbaca.
        price = getattr(order, "price", None)
        if _finite_positive(price):
            return float(price)

        px = market_store.get_price(coin_of(order.symbol))
        if px and math.isfinite(float(px)) and float(px) > 0:
            return float(px)
        raise ValueError(
            "tidak ada harga untuk {}; order live butuh harga pasar".format(
                order.symbol))


class _LivePositionManager:
    """
    `position_manager` versi live.

    Hanya `close_position` yang dibutuhkan pemanggil
    (`_scalp_take_profit` dan `_auto_close_expired` di
    execution_agent.py:270/311). Stub lain sengaja TIDAK dibuat: method
    yang tidak dipanggil adalah permukaan kedua yang bisa salah, dan
    `take_balance_snapshot` versi live tidak bisa diisi dengan jujur --
    saldonya ada di bursa, bukan di SQLite.

    Tidak menyentuh bursa sama sekali di `__init__`: test membangun
    `LiveExecutor(_Engine(rec))` dengan stub exchange yang hanya punya
    `submit_order` dan `mid_price`.
    """

    def __init__(self, executor: LiveExecutor):
        self._executor = executor

    @property
    def engine(self) -> LiveEngine:
        return self._executor.engine

    async def close_position(
        self, position_id: int, price: float, reason: str = "MANUAL",
    ) -> Optional[Dict]:
        """
        Tutup posisi menurut baris SQLite, dengan BURSA sebagai otoritas
        ukuran.

        `self.engine.positions` TIDAK boleh jadi sumber size di sini:
        `submit_order(is_close=True)` sudah `pop` entri itu
        (engine.py:315), dan posisi yang terisi telat lewat
        `check_pending_fills` tidak pernah masuk sana. Yang benar adalah
        apa yang bursa pegang sekarang.

        Parameter `price` diabaikan dengan sengaja. Harga yang
       Dipakai menutup adalah mid bursa saat order dikirim -- harga dari
        pemanggil bisa beberapa milidetik basi, dan order closing yang
        tidak terisi tidak menutup apa pun.
        """
        ex = self._executor
        repo = await ex._get_repo()
        row = None
        for candidate in await repo.get_open_positions():
            if candidate["id"] == position_id:
                row = candidate
                break
        if row is None:
            # Sama seperti `PositionManager.close_position`: None di sini
            # bukan kegagalan, pemanggil hanya menguji kebenaran nilai.
            return None

        coin = coin_of(row.get("symbol") or "")
        try:
            # `positions()` adalah panggilan blocking ccxt yang menarik
            # `get_account_state()`. HARUS di `to_thread`, sama seperti
            # engine.py:441.
            assets = await asyncio.to_thread(self.engine.exchange.positions)
        except Exception as exc:  # noqa: BLE001
            self.engine.gate.record_error()
            logger.error("Gagal membaca posisi bursa untuk %s: %s",
                         coin, exc)
            return None

        abs_size = 0.0
        entry_px = 0.0
        is_short = False
        for asset in assets or []:
            pos = (asset or {}).get("position") or {}
            if (pos.get("coin") or "").upper() != coin.upper():
                continue
            signed = float(pos.get("szi") or 0.0)
            abs_size = abs(signed)
            entry_px = float(pos.get("entryPx") or 0.0)
            # Size negatif = posisi SHORT.
            is_short = signed < 0
            break

        if abs_size <= 0:
            # Posisi benar-benar sudah tidak ada. Baris lokal TIDAK
            # ditutup di sini: menutupnya dengan harga tebakan menulis
            # PnL yang tidak pernah terjadi.
            logger.warning(
                "Posisi #%s %s tidak ada di bursa; baris lokal dibiarkan "
                "OPEN. Rekonsiliasi atau operator yang menyelesaikannya.",
                position_id, row.get("symbol"),
            )
            return None

        recorded = float(row.get("quantity") or 0.0)
        if recorded > 0 and abs_size > recorded * 1.01:
            # Bursa memegang lebih besar dari yang tercatat. Menutup
            # ukuran bursa di sini akan MENYEMBUNYIKAN perbedaan
            # eksposur yang nyata, justru di saat paling perlu terlihat.
            logger.warning(
                "Ukuran di bursa lebih besar dari yang tercatat untuk #%s "
                "%s: bursa=%.8f catatan=%.8f. Tidak menutup; rekonsiliasi "
                "manual diperlukan.",
                position_id, row.get("symbol"), abs_size, recorded,
            )
            return None

        mark = await asyncio.to_thread(
            self.engine.exchange.mid_price, coin)
        if not _finite_positive(mark):
            # Sama seperti `_close`: penutupan tanpa harga bukan
            # penutupan.
            logger.warning("Harga pasar %s tidak terbaca; posisi #%s TIDAK "
                           "ditutup.", coin, position_id)
            return None

        # Menutup LONG = BUY, menutup SHORT = SELL.
        is_buy = is_short
        limit = mark * (1.001 if is_buy else 0.999)

        cloid = make_cloid("close")
        result = await self.engine.submit_order(
            coin=coin, symbol=row["symbol"], is_buy=is_buy, size=abs_size,
            price=limit, is_close=True, cloid=cloid,
        )
        if not result.get("success"):
            # TIDAK `record_error()` di sini: penolakan gerbang bukan
            # error bursa, dan `submit_order` sudah menghitungnya sendiri
            # di jalur error-nya. Tiga penolakan aman yang dihitung tiga
            # kali akan menyalakan kill switch pada sistem yang benar.
            logger.warning("Penutupan #%s %s ditolak: %s", position_id,
                           coin, result.get("message", "ditolak"))
            return None

        outcome = result.get("outcome")
        filled = float(getattr(outcome, "filled_size", 0.0) or 0.0)
        if filled <= 0:
            # Order closing `reduce_only` (engine.py:232-233), jadi
            # menutup lagi tidak akan pernah membalik posisi. None di
            # sini aman untuk dicoba ulang siklus berikutnya.
            logger.warning("Order closing #%s %s RESTING (belum terisi); "
                           "posisi BELUM tertutup.", position_id, coin)
            return None

        close_price = (float(getattr(outcome, "avg_price", 0.0) or 0.0)
                       or float(limit))
        recorded = await self._executor._record_close(
            row_id=position_id, symbol=row["symbol"],
            side="SHORT" if is_short else "LONG", size=filled,
            entry_price=entry_px, close_price=close_price, reason=reason,
            cloid=cloid,
        )
        await self._executor._log_wallet("close_position")
        return recorded
