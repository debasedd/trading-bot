"""
trading/paper_engine.py — Mesin eksekusi paper trading utama.

Menerima order, validasi risiko, eksekusi via PositionManager.
"""

import asyncio
import statistics
from typing import Dict, List, Optional
from datetime import datetime

from core.config import get_config
from core.event_bus import EventBus, Channels
from core.logger import get_logger
from core.market_store import market_store
from database.db import get_db
from database.repository import Repository
from database.models import AgentLog
from trading.models import Order, TradeAction, Side
from trading.risk_manager import RiskManager
from trading.position_manager import PositionManager

logger = get_logger("paper_engine")


class VolatilityGateError(Exception):
    """
    Order dibatalkan karena volatilitas membuat targetnya tidak layak.

    Ini memakai exception terpisah karena ini BUKAN kegagalan validasi risiko
    biasa: validation menolak berdasarkan state akun (margin, drawdown, daily
    loss), sedangkan ini menolak berdasarkan kondisi pasar (volatilitas).
    Keduanya dicatat dengan `reason` berbeda supaya audit bisa membedakan
    "kartu kita bermasalah" dari "pasar sedang tidak layak diperdagangkan".

    Atribut `meta` membawa detail perhitungan (SL/TP akhir, ATR, alasan) yang
    ditulis ke `agent_logs`.
    """

    def __init__(self, reason: str, meta: Optional[dict] = None):
        super().__init__(reason)
        self.reason = reason
        self.meta = meta or {}


class PaperTradingEngine:
    """
    Mesin utama paper trading.

    Alur:
    1. Terima Order dari agen keputusan
    2. Validasi risiko (margin cukup, batas posisi, drawdown)
    3. Hitung ukuran posisi jika belum ditentukan
    4. Eksekusi via PositionManager
    5. Log hasil dan broadcast event
    """

    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus
        self.risk_manager = RiskManager()
        self.position_manager = PositionManager(event_bus, self.risk_manager)
        self.config = get_config()
        self._repo: Optional[Repository] = None
        # Cache candle 1m untuk perhitungan ATR. Diisi oleh
        # `_register_candle_source` dan `refresh_volatility_cache`.
        self._candle_cache: Dict[str, list] = {}
        self._candle_refresh_task = None
        self._last_prices: Dict[str, float] = {}

    async def _get_repo(self) -> Repository:
        if self._repo is None:
            db = await get_db()
            self._repo = Repository(db)
        return self._repo

    async def initialize(self):
        """Inisialisasi engine — buat akun jika belum ada."""
        repo = await self._get_repo()
        await repo.init_account(self.config.account.initial_balance)

        # Modal awal disuntikkan ke RiskManager dari database, bukan dari
        # config. Nilai di DB adalah yang benar setelah reset_paper_db.py,
        # sedangkan config hanya nilai default saat akun pertama dibuat.
        account = await repo.get_account()
        if account:
            self.risk_manager.set_initial_balance(
                account.get("initial_balance") or self.config.account.initial_balance
            )

        self._register_candle_source(repo)

        logger.info(
            "Paper Trading Engine diinisialisasi "
            f"(modal awal {self.risk_manager.initial_balance:.2f} "
            f"{self.config.account.currency})"
        )

    def _register_candle_source(self, repo: "Repository") -> None:
        """
        Daftarkan sumber candle sinkron ke `analysis.volatility`.

        `Repository.get_candles` asynchronous, sedangkan modul volatilitas
        sinkron dan murni supaya bisa diuji tanpa async. Jembatannya adalah
        cache: candle 1m yang baru di-refresh oleh `_candle_refresh_loop`
        disimpan di sini, dan modul volatilitas membacanya dari sana tanpa
        menyentuh database sama sekali.

        Ini bukan cache yang bisa basi: candle 1m baru selesai setiap menit,
        sementara ATR dihitung dari 14 candle terakhir — jadi data satu menit
        yang tertinggal tidak mengubah kesimpulan.
        """
        from analysis import volatility as vol_mod

        async def _refresh_cache():
            try:
                for symbol in list(self.config.symbols):
                    rows = await repo.get_candles(symbol, "1m", limit=30)
                    if rows:
                        self._candle_cache[symbol] = rows
            except Exception as exc:
                logger.debug(f"Gagal refresh cache candle volatilitas: {exc}")

        def _sync_source(symbol, timeframe, limit):
            rows = self._candle_cache.get(symbol)
            if not rows:
                return None
            if timeframe != "1m":
                return None
            return rows[:limit] if limit else rows

        vol_mod.set_candle_source(_sync_source)

        # Isi sekali supaya target dinamis langsung tersedia pada order
        # pertama, bukan menunggu satu putaran refresh.
        self._candle_refresh_task = asyncio.create_task(_refresh_cache())

    async def refresh_volatility_cache(self) -> None:
        """
        Muat ulang candle 1m untuk perhitungan ATR.

        Dipanggil dari `_candle_refresh_loop` supaya cache volatilitas tidak
        bergantung pada siklus refresh candle yang mungkin lambat atau gagal.
        Kegagalan di sini tidak boleh menjatuhkan candle refresh utama —
        target dinamis akan jatuh ke statis, yang perilakunya bisa dijelaskan.
        """
        repo = await self._get_repo()
        try:
            for symbol in list(self.config.symbols):
                rows = await repo.get_candles(symbol, "1m", limit=30)
                if rows:
                    self._candle_cache[symbol] = rows
        except Exception as exc:
            logger.debug(f"Gagal refresh cache candle volatilitas: {exc}")

    def update_price(self, symbol: str, price: float):
        """Update cache harga terbaru."""
        self._last_prices[symbol] = price

    def get_price(self, symbol: str) -> Optional[float]:
        """
        Ambil harga terbaru dari cache internal atau market_store.

        Cache internal menang kalau punya nilai — ia adalah harga yang terakhir
        dipakai engine untuk fill, jadi itu yang harus dipakai lagi supaya
        hasil `get_price()` konsisten dengan eksekusi sebelumnya.
        """
        cached = self._last_prices.get(symbol)
        if cached and cached > 0:
            return cached

        # market_store sudah diimpor di level modul. Import di dalam fungsi
        # pernah dipakai untuk menghindari circular import, tapi
        # `core.market_store` tidak mengimpor apa pun dari `trading`, jadi
        # tidak ada lagi alasan untuk menyembunyikannya.
        price = market_store.get_price(symbol)
        if price and price > 0:
            self._last_prices[symbol] = price
            return price
        return None

    async def execute_order(self, order: Order) -> Dict:
        """
        Eksekusi order trading.

        Returns:
            {"success": bool, "message": str, "position_id": int|None, "details": dict}
        """
        repo = await self._get_repo()

        # Ambil harga terbaru (dari internal cache atau market_store)
        price = self.get_price(order.symbol)
        if price is None:
            return {"success": False, "message": f"Harga {order.symbol} tidak tersedia", "position_id": None, "details": {}}

        # === OPEN LONG / OPEN SHORT ===
        if order.action in (TradeAction.OPEN_LONG, TradeAction.OPEN_SHORT):
            return await self._execute_open(order, price)

        # === CLOSE ===
        elif order.action == TradeAction.CLOSE:
            return await self._execute_close(order, price)

        # === HOLD ===
        elif order.action == TradeAction.HOLD:
            return {"success": True, "message": "HOLD — tidak ada aksi", "position_id": None, "details": {}}

        return {"success": False, "message": f"Aksi tidak dikenal: {order.action}", "position_id": None, "details": {}}

    def _tick_quality_guard(self, symbol: str, price: float, sl_pct: float):
        """
        Nilai tick yang akan dipakai sebagai harga fill.

        Mengembalikan string alasan penolakan, atau `None` bila tick layak
        eksekusi. Fungsi ini murni dan mudah diuji: seluruh state datang dari
        `market_store`, tidak ada I/O.

        Dua pemeriksaan, berurutan dari yang paling murah:

        1. **USIA TICK** — tick yang sudah tua tidak menggambarkan pasar
           sekarang. Batasnya `scalping.max_tick_age_seconds`.

        2. **OUTLIER** — bandingkan harga fill dengan median tick-tick
           terakhir, bukan dengan harga yang dipakai membangun order. Median
           dipilih bukan mean supaya satu spike tidak menggeser baseline.
           Tick yang menyimpang lebih dari `sl_pct` ditolak: pada scalp
           berjarak 0.25%, fill di puncak lokal berarti stop sudah berada di
           dalam spread, dan posisinya akan tersapu di pemeriksaan berikutnya.

        **Fail-open yang disengaja:** bila data belum cukup (feed baru hidup,
           simbol baru masuk daftar pantau), guard ini mengizinkan eksekusi.
           Menolak order karena tidak punya data sejarah akan membuat bot diam
           persis di detik-detik paling ramai. Ketiadaan data dicatat di log
           debug, bukan di layar pengguna.
        """
        scalp = getattr(self.config, "scalping", None)

        # --- 1. Usia tick ---
        max_age = getattr(scalp, "max_tick_age_seconds", None)
        if max_age is not None:
            age = market_store.get_price_age(symbol)
            if age is not None and age > float(max_age):
                return (
                    f"tick basi ({age:.2f}s, batas {float(max_age):.2f}s)"
                )

        # --- 2. Outlier terhadap median tick terakhir ---
        window = getattr(scalp, "stale_tick_window_seconds", None)
        min_samples = int(getattr(scalp, "stale_tick_min_samples", 5))
        if not window:
            return None

        history = market_store.get_price_history(symbol, seconds=float(window))
        samples = [px for _, px in history if px and px > 0]
        if len(samples) < min_samples:
            return None

        median_px = statistics.median(samples)
        if median_px <= 0 or price <= 0:
            return None

        deviation = abs(price - median_px) / median_px
        if deviation > sl_pct:
            return (
                f"harga {price:.8g} menyimpang {deviation * 100:.3f}% dari "
                f"median {median_px:.8g} (ambang {sl_pct * 100:.3f}%)"
            )

        return None

    def _resolve_tp_sl(self, symbol: str, order: "Order") -> tuple:
        """
        Tentukan SL/TP akhir untuk satu order, dengan gate volatilitas.

        Mengembalikan `(sl_pct, tp_pct, meta)`. Kalau gate menolak, method ini
        melempar `VolatilityGateError` — pemanggil yang translating menjadi
        penolakan order, supaya penolakan ini tercatat di jalur yang sama
        dengan guard tick dan validasi risiko lainnya (bukan diam-diam
        melewati validasi).

        Urutan pemeriksaan itu penting:
          1. Ambil target dinamis. Kalau ATR tidak tersedia, dapat fallback
             ke target statis — itu perilaku yang benar, bukan kegagalan.
          2. Jaga agar SL/TP tidak lebih rapat dari statis. Volatilitas mungkin
             membuat target melebar, tapi tidak boleh membuatnya menyempit di
             bawah konfigurasi statis: konfigurasi statis adalah batas bawah
             yang disepakati, bukan titik awal yang bisa ditembus ke bawah.
          3. Jalankan volatility gate terhadap hasil akhir.

        Catatan soal batas bawah: kalau SL berbasis ATR ternyata lebih rapat
        dari SL statis (mis. ATR anjlok kecil di menit yang tenang), memakai
        angka yang lebih rapat akan melanggar janji konfigurasi dan menaikkan
        frekuensi stop-out. SL statis karena itu diperlakukan sebagai batas
        bawah, bukan titik awal yang boleh ditembus ke bawah.
        """
        from analysis import volatility as vol_mod

        scalp_cfg = getattr(self.config, "scalping", None)
        static_sl = float(getattr(scalp_cfg, "tight_sl_pct", 0.0025))
        static_tp = float(getattr(scalp_cfg, "fast_tp_pct", 0.0060))

        thresholds = vol_mod.get_dynamic_tp_sl_thresholds(symbol, self.config)

        if thresholds.get("used_dynamic"):
            sl_pct = max(float(thresholds["sl_pct"]), static_sl)
            tp_pct = max(float(thresholds["tp_pct"]), static_tp)
        else:
            sl_pct = static_sl
            tp_pct = static_tp

        # R:R tidak boleh turun di bawah penguncian konfigurasi. `tp_pct` sudah
        # max() dengan min_profit_pct di volatility.py; di sini kita pastikan
        # juga terhadap SL yang baru saja dinaikkan.
        dyn_cfg = getattr(self.config, "dynamic_tp_sl", None)
        min_rr = float(getattr(dyn_cfg, "min_risk_reward", 1.5) or 1.5) if dyn_cfg else 1.5
        tp_pct = max(tp_pct, sl_pct * min_rr)

        meta = {
            "sl_pct": sl_pct,
            "tp_pct": tp_pct,
            "used_dynamic": bool(thresholds.get("used_dynamic")),
            "atr_pct": thresholds.get("atr_pct"),
            "reason": thresholds.get("reason", ""),
        }

        rejection = vol_mod.assess_volatility_gate(symbol, sl_pct, tp_pct, self.config)
        if rejection:
            meta["gate"] = rejection
            raise VolatilityGateError(rejection, meta)

        return sl_pct, tp_pct, meta

    async def _diagnose_open_failure(
        self,
        order: Order,
        quantity: float,
        price: float,
        margin: float,
        estimated_fee: float,
    ) -> str:
        """
        Bedakan penyebab kegagalan pembukaan agar pesan errornya jujur.

        `open_position` sengaja mengembalikan `None` untuk semua kegagalan agar
        pemanggil tidak perlu tahu detailnya. Tapi kalau pemanggilnya hanya
        menulis "saldo tidak cukup", kegagalan di lapisan database ikut
        tercatat sebagai masalah likuiditas — dan pembaca log akan menyalahkan
        sizing, padahal akar masalahnya di tempat lain.

        Urut pemeriksaan dari yang paling mungkin:
          1. Akun belum ada       → masalah konfigurasi / boot
          2. Saldo tidak cukup    → termasuk fee; inilah kasus yang sebenarnya
          3. Simpan posisi gagal  → masalah database, bukan masalah modal
        """
        repo = await self._get_repo()
        account = await repo.get_account()
        if not account:
            return "akun belum diinisialisasi (perlu init_account)"

        required_cash = margin + estimated_fee
        free = float(account["balance"])
        if free < required_cash:
            return (
                f"saldo tidak cukup: kas {free:.2f} < margin {margin:.2f} "
                f"+ fee {estimated_fee:.2f} = {required_cash:.2f}"
            )

        return (
            f"penyimpanan posisi gagal di database (modal cukup: kas "
            f"{free:.2f} >= {required_cash:.2f})"
        )

    async def _execute_open(self, order: Order, price: float) -> Dict:
        """Eksekusi pembukaan posisi."""
        repo = await self._get_repo()
        account = await repo.get_account()

        if not account:
            return {"success": False, "message": "Akun belum diinisialisasi", "position_id": None, "details": {}}

        side = "LONG" if order.action == TradeAction.OPEN_LONG else "SHORT"

        # ── Validasi input dasar ────────────────────────────────────────────
        # WAJIB sebelum apa pun yang memakai `quantity` atau `leverage`.
        #
        # Jalur `else` di bawah memakai `order.quantity` apa adanya dan
        # membagi dengan `order.leverage`. Tanpa penjaga ini:
        #   * `quantity` negatif -> `margin` negatif -> `required_cash`
        #     negatif -> `validate_trade` melihatnya "cukup" -> saldo
        #     bertambah, yaitu uang tercipta dari nol;
        #   * `quantity` nol -> posisi terbuka dengan ukuran nol;
        #   * `leverage` nol -> ZeroDivisionError yang mematikan loop.
        #
        # `quantity = None` TIDAK boleh ditolak: itu nilai yang disengaja di
        # `Order` — artinya "hitung sendiri dari risk manager", dan jalur
        # `if` di bawah memang memperoleh ukurannya dari sana. Yang haram
        # hanyalah nilai yang sudah terisi tapi tidak masuk akal.
        problems = []
        qty = order.quantity
        lev = order.leverage

        if qty is not None and qty <= 0:
            problems.append("quantity harus > 0 (dapat {})".format(qty))
        if lev is None:
            problems.append("leverage tidak terisi")
        elif lev <= 0:
            problems.append("leverage harus > 0 (dapat {})".format(lev))
        if price is None or price <= 0:
            problems.append("harga harus > 0 (dapat {})".format(price))

        if problems:
            message = "Order tidak valid: " + "; ".join(problems)
            logger.warning(f"{message} — {side} {order.symbol}")
            await repo.insert_agent_log(AgentLog(
                agent_name="paper_engine",
                action="TRADE_REJECTED",
                reasoning=f"Order {side} {order.symbol} ditolak: {message}",
                input_data=str({"order": str(order), "price": price}),
                output_data=str({"problems": problems}),
            ))
            return {
                "success": False,
                "message": message,
                "position_id": None,
                "details": {"invalid_input": problems},
            }

        # PENTING: SL/TP dihitung ulang dari harga fill yang BENAR-BENAR dipakai.
        #
        # `order.stop_loss` / `order.take_profit` sudah dihitung lebih awal oleh
        # ExecutionAgent dari harga yang dia lihat saat think(). Karena `think()`
        # dan `execute_order()` dipisah oleh await, harga bisa sudah bergerak di
        # antara keduanya. Kalau SL dihitung dari P1 tapi fill terjadi di P2,
        # jarak risiko sebenarnya bukan `tight_sl_pct` melainkan
        # `tight_sl_pct ± (P2 - P1)`. Pada altcoin tipis yang selisihnya bisa
        # lebih besar dari SL itu sendiri — posisi langsung kena stop di detik
        # pertama, persis seperti yang terlihat di log (durasi 0-6 detik).
        #
        # Persentase dari config dipakai ulang di sini agar jarak SL/TP
        # relatif terhadap fill selalu presisi.
        #
        # Target dinamis (ATR) dihitung DI SINI, bukan di ExecutionAgent,
        # karena hanya di titik ini harga fill-nya diketahui. Kalau ATR
        # dihitung lebih awal dari harga P1, enquanto fill terjadi di P2, maka
        # SL/TP ikut bergeser bersama P1-P2 — persis kelas bug yang SL/TP
        # hitung-ulang di sini diperbaiki.
        scalp_cfg = getattr(self.config, "scalping", None)
        if scalp_cfg and scalp_cfg.enabled:
            try:
                sl_pct, tp_pct, vol_meta = self._resolve_tp_sl(
                    order.symbol, order
                )
            except VolatilityGateError as gate_err:
                # Volatilitas membuat target tidak layak. Ini DITOLAK dengan
                # jejak audit, bukan dibiarkan lolos ke validasi risiko — di
                # titik itu reasons-nya akan menyebut margin, padahal masalah
                # sebenarnya ada di kondisi pasar.
                logger.warning(
                    f"Order {side} {order.symbol} dibatalkan: {gate_err.reason}"
                )
                await repo.insert_agent_log(AgentLog(
                    agent_name="paper_engine",
                    action="TRADE_REJECTED",
                    reasoning=(
                        f"Order {side} {order.symbol} dibatalkan volatility "
                        f"gate: {gate_err.reason}"
                    ),
                    input_data=str({"price": price, "order": str(order)}),
                    output_data=str(gate_err.meta),
                ))
                return {
                    "success": False,
                    "message": gate_err.reason,
                    "position_id": None,
                    "details": {"gate": "volatility", **gate_err.meta},
                }
        else:
            sl_pct = 0.02
            tp_pct = 0.04
            vol_meta = {}

        stop_loss = self.risk_manager.calculate_stop_loss(price, side, sl_pct)
        take_profit = self.risk_manager.calculate_take_profit(price, side, tp_pct)

        # ── Guard kualitas tick (B2) ──────────────────────────────────────
        # Guard lama membandingkan `price` dengan `stop_loss` yang SENDIRI
        # diturunkan dari `price`, jadi `price <= stop_loss` hanya mungkin true
        # kalau sl_pct <= 0. Itu dead code: tidak pernah menangkap apa pun.
        #
        # Secara ekonomi yang harus dicek adalah: apakah harga fill ini
        # representatif? Stop scalp hanya 0.25% dari harga, jadi fill di
        # puncak lokal berarti stop-nya secara efektif sudah menyentuh market
        # dalam hitungan milidetik — dan dua fee dibayar untuk trade yang tidak
        # pernah punya ruang gerak. Guard di bawah mengukur tepat itu: usia tick,
        # dan simpangan harga terhadap median tick-tick terakhir.
        rejection = self._tick_quality_guard(order.symbol, price, sl_pct)
        if rejection:
            logger.warning(
                f"Order {side} {order.symbol} dibatalkan: {rejection} "
                f"(harga {price:.8g})"
            )
            await repo.insert_agent_log(AgentLog(
                agent_name="paper_engine",
                action="TRADE_REJECTED",
                reasoning=(
                    f"Order {side} {order.symbol} dibatalkan guard tick: {rejection}"
                ),
                input_data=str({"price": price, "sl_pct": sl_pct}),
            ))
            return {
                "success": False,
                "message": f"Harga tidak layak eksekusi: {rejection}",
                "position_id": None,
                "details": {"guard": "tick_quality", "reason": rejection},
            }

        # Hitung ukuran posisi
        if order.quantity is None:
            if scalp_cfg and scalp_cfg.enabled:
                sizing = self.risk_manager.calculate_scalp_position_size(
                    balance=account["balance"],
                    entry_price=price,
                    leverage=order.leverage,
                )
            else:
                sizing = self.risk_manager.calculate_position_size(
                    balance=account["balance"],
                    entry_price=price,
                    stop_loss_price=stop_loss,
                    leverage=order.leverage,
                )
            quantity = sizing["quantity"]
            margin = sizing["margin"]
        else:
            quantity = order.quantity
            margin = (quantity * price) / order.leverage

        # Validasi risiko.
        #
        # `account["balance"]` adalah saldo KAS BEBAS — margin posisi terbuka
        # sudah dipotong dari sana. Memakainya untuk batas margin OK (modal
        # yang benar-benar bisa dikunci), TAPI untuk drawdown salah: setiap
        # posisi terbuka menurunkan saldo, jadi account yang equity-nya naik
        # pun terbaca drawdown. Pada ambang 20% itu menahan entry tepat saat
        # modal justru paling sehat.
        open_count = await self.position_manager.get_open_position_count()
        open_margin = await self.position_manager.get_total_open_margin()
        open_upnl = await self.position_manager.get_total_unrealized_pnl()
        free_balance = account["balance"]
        equity_now = free_balance + open_margin + open_upnl

        # B5 — yang benar-benar keluar dari dompet adalah `margin + fee`, bukan
        # margin saja: `open_position` memotong keduanya secara atomik. Validasi
        # dengan margin saja membuat order lolos gerbang lalu ditolak di
        # lapisan berikutnya, dan penolakannya dilaporkan sebagai "saldo tidak
        # cukup" — padahal validasinya sendiri sudah salah sejak awal.
        estimated_fee = self.risk_manager.calculate_fee(quantity, price, "TAKER")
        required_cash = margin + estimated_fee

        # B4 — circuit breaker kerugian harian. Tanpa ini `max_daily_loss`
        # hanya ada di config.yaml tanpa pernah dievaluasi, dan bot tetap
        # membuka posisi baru sepanjang hari meski portofolionya sudah jauh
        # melewati batas. Sumbernya adalah PnL terealisasi HARI INI, bukan PnL
        # kumulatif: pembatasannya harian, bukan sepanjang umur akun.
        daily_pnl = await repo.get_daily_realized_pnl()

        validation = self.risk_manager.validate_trade(
            balance=free_balance,
            margin_required=required_cash,
            open_positions=open_count,
            daily_pnl=daily_pnl,
            peak_balance=account.get("peak_balance"),
            equity=equity_now,
        )

        if not validation["allowed"]:
            reasons = "; ".join(validation["reasons"])
            logger.warning(f"Trade DITOLAK: {reasons}")

            # Log penolakan. `required_cash` ikut dicatat supaya audit bisa
            # merekonstruksi keputusan tanpa menebak fee berapa.
            await repo.insert_agent_log(AgentLog(
                agent_name="paper_engine",
                action="TRADE_REJECTED",
                reasoning=f"Order {side} {order.symbol} ditolak: {reasons}",
                input_data=str({
                    "order": str(order),
                    "price": price,
                    "margin": margin,
                    "estimated_fee": estimated_fee,
                    "required_cash": required_cash,
                    "daily_pnl": daily_pnl,
                }),
                output_data=str(validation),
            ))

            return {"success": False, "message": f"Ditolak: {reasons}", "position_id": None, "details": validation}

        # Eksekusi pembukaan
        position_id = await self.position_manager.open_position(
            symbol=order.symbol,
            side=side,
            entry_price=price,
            quantity=quantity,
            leverage=order.leverage,
            stop_loss=stop_loss,
            take_profit=take_profit,
            reasoning=order.reasoning,
        )

        if position_id is None:
            # `open_position` mengembalikan None untuk tiga alasan berbeda:
            # saldo tidak cukup, akun belum ada, atau INSERT posisi gagal.
            # Semuanya pernah dilaporkan sebagai "saldo tidak cukup", yang
            # membuat log audit menyesatkan — pembaca mengira sizing-nya yang
            # salah, padahal masalahnya bisa di lapisan database.
            # Diagnosis diulang di sini supaya pesan akhirnya jujur.
            diagnostics = await self._diagnose_open_failure(
                order=order,
                quantity=quantity,
                price=price,
                margin=margin,
                estimated_fee=estimated_fee,
            )
            logger.warning(
                f"Gagal membuka posisi {side} {order.symbol}: {diagnostics}"
            )
            await repo.insert_agent_log(AgentLog(
                agent_name="paper_engine",
                action="TRADE_REJECTED",
                reasoning=(
                    f"Order {side} {order.symbol} gagal dieksekusi: {diagnostics}"
                ),
                input_data=str({
                    "price": price,
                    "margin": margin,
                    "estimated_fee": estimated_fee,
                }),
            ))
            return {
                "success": False,
                "message": f"Gagal membuka posisi: {diagnostics}",
                "position_id": None,
                "details": {"margin": margin, "fee": estimated_fee},
            }

        # Log eksekusi
        await repo.insert_agent_log(AgentLog(
            agent_name="paper_engine",
            action="TRADE_EXECUTED",
            reasoning=f"Buka {side} {order.symbol} @ {price} qty={quantity:.5f} lev={order.leverage}x",
            input_data=str({"order": str(order)}),
            output_data=str({"position_id": position_id, "margin": margin}),
        ))

        # Broadcast
        await self.event_bus.publish(
            Channels.TRADE_EXECUTED,
            {
                "action": "OPEN",
                "position_id": position_id,
                "symbol": order.symbol,
                "side": side,
                "price": price,
                "quantity": quantity,
                "leverage": order.leverage,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "reasoning": order.reasoning,
            },
            source="paper_engine",
        )

        return {
            "success": True,
            "message": f"{side} {order.symbol} dibuka @ {price}",
            "position_id": position_id,
            "details": {
                "entry_price": price,
                "quantity": quantity,
                "leverage": order.leverage,
                "margin": margin,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
            },
        }

    async def _execute_close(self, order: Order, price: float) -> Dict:
        """
        Eksekusi penutupan posisi.

        Dua jalur: satu posisi spesifik (`order.position_id` diisi), atau semua
        posisi yang masih terbuka untuk simbol tersebut.

        Jalur "semua" memakai klaim-tunggal di `close_position` — hanya pemanggil
        yang benar-benar mengubah baris OPEN -> CLOSED yang mendapat pengembalian
        margin. Karena itu posisi yang sudah diklaim pemanggil lain (mis. SL/TP
        dari `update_positions` yang berjalan paralel) akan mengembalikan `None`,
        dan itu SAH — bukan kegagalan: posisi itu memang sudah tertutup, hanya
        bukan oleh pemanggil ini.
        """
        repo = await self._get_repo()

        if order.position_id:
            # Tutup posisi spesifik
            result = await self.position_manager.close_position(
                order.position_id, price, "SIGNAL"
            )
            if result:
                # B6 — jejak audit. Pembukaan punya TRADE_EXECUTED, penutupan
                # sebelumnya tidak punya apa pun, sehingga rekonstruksi "kenapa
                # posisi ini tidak ada lagi" mustahil dari DB.
                await repo.insert_agent_log(AgentLog(
                    agent_name="paper_engine",
                    action="TRADE_CLOSED",
                    reasoning=(
                        f"Tutup posisi #{order.position_id} {order.symbol} "
                        f"@ {price} PnL={result['realized_pnl']:+.2f} "
                        f"(fee {result['fee']:.2f}, ROE {result['roe_pct']:+.2f}%)"
                    ),
                    input_data=str({"price": price, "reason": "SIGNAL"}),
                    output_data=str(result),
                ))
                return {
                    "success": True,
                    "message": f"Posisi #{order.position_id} ditutup @ {price} PnL={result['realized_pnl']:+.2f}",
                    "position_id": order.position_id,
                    "details": result,
                }
            return {"success": False, "message": f"Posisi #{order.position_id} tidak ditemukan", "position_id": None, "details": {}}

        # Tutup semua posisi untuk simbol ini
        open_positions = await repo.get_open_positions(order.symbol)
        if not open_positions:
            return {"success": False, "message": f"Tidak ada posisi terbuka untuk {order.symbol}", "position_id": None, "details": {}}

        total_pnl = 0.0
        closed_ids: List[int] = []
        # B6 — kegagalan per-posisi dicatat, bukan diabaikan. Sebelumnya loop
        # ini membuang nilai falsy tanpa jejak dan tetap melaporkan success,
        # sehingga "3 posisi ditutup" bisa berarti sebenarnya hanya 1 yang
        # benar-benar menutup, dengan PnL separuh dari yang seharusnya.
        failed: List[dict] = []
        for pos in open_positions:
            result = await self.position_manager.close_position(pos["id"], price, "SIGNAL")
            if result:
                total_pnl += float(result["realized_pnl"])
                closed_ids.append(pos["id"])
            else:
                # None = klaim gagal, paling sering karena posisi sudah
                # ditutup jalur lain pada siklus yang sama.
                failed.append({
                    "position_id": pos["id"],
                    "side": pos.get("side"),
                    "entry_price": pos.get("entry_price"),
                })

        await repo.insert_agent_log(AgentLog(
            agent_name="paper_engine",
            action="TRADE_CLOSED",
            reasoning=(
                f"Tutup semua {order.symbol}: {len(closed_ids)}/{len(open_positions)} "
                f"berhasil, PnL={total_pnl:+.2f}, gagal={len(failed)}"
            ),
            input_data=str({"price": price, "reason": "SIGNAL"}),
            output_data=str({
                "closed_ids": closed_ids,
                "total_pnl": total_pnl,
                "failed": failed,
            }),
        ))

        # Sukses hanya bila SETIAP posisi benar-benar tertutup. Penutupan
        # parsial dilaporkan apa adanya — pemanggil wajib tahu masih ada
        # eksposur terbuka, bukan diberi angka optimistis.
        fully_closed = not failed
        if fully_closed:
            message = (
                f"{len(closed_ids)} posisi {order.symbol} ditutup, "
                f"total PnL={total_pnl:+.2f}"
            )
        elif closed_ids:
            message = (
                f"SEBAGIAN: {len(closed_ids)}/{len(open_positions)} posisi "
                f"{order.symbol} ditutup, PnL={total_pnl:+.2f}; "
                f"{len(failed)} posisi tetap terbuka ({self._describe_failures(failed)})"
            )
        else:
            message = (
                f"GAGAL: tidak ada dari {len(open_positions)} posisi "
                f"{order.symbol} yang tertutup ({self._describe_failures(failed)})"
            )

        if not fully_closed:
            logger.warning(f"Penutupan parsial {order.symbol}: {message}")

        return {
            "success": fully_closed,
            "message": message,
            "position_id": None,
            "details": {
                "closed_ids": closed_ids,
                "total_pnl": total_pnl,
                "requested": len(open_positions),
                "failed": failed,
                "fully_closed": fully_closed,
            },
        }

    @staticmethod
    def _describe_failures(failed: List[dict]) -> str:
        """Ringkas daftar posisi yang gagal ditutup, untuk pesan error."""
        if not failed:
            return "tidak ada"
        parts = [
            f"#{f['position_id']} {f.get('side', '?')}"
            for f in failed[:5]
        ]
        if len(failed) > 5:
            parts.append(f"… +{len(failed) - 5} lainnya")
        return ", ".join(parts)

    async def check_positions(self):
        """
        Cek semua posisi terbuka — update PnL, SL/TP/likuidasi.

        Dipanggil secara berkala oleh scheduler dan execution loop.
        """
        prices = market_store.get_all_prices()
        prices.update(self._last_prices)
        if not prices:
            return

        await self.position_manager.update_positions(prices)

    async def get_account_summary(self) -> Dict:
        """Ringkasan akun untuk dashboard."""
        repo = await self._get_repo()
        account = await repo.get_account()

        if not account:
            return {}

        unrealized = await self.position_manager.get_total_unrealized_pnl()
        open_count = await self.position_manager.get_open_position_count()
        open_margin = await self.position_manager.get_total_open_margin()

        # `account.balance` adalah saldo KAS BEBAS: margin posisi terbuka
        # sudah dipotong darinya saat order dieksekusi. Margin itu tetap
        # milik trader, jadi harus ditambahkan kembali sebelum menyebut
        # angka ini "saldo dompet" — kalau tidak, tiap posisi terbuka
        # terlihat seolah-olah mengurangi modal.
        free_balance = account["balance"]
        wallet_balance = free_balance + open_margin
        equity = wallet_balance + unrealized
        total_pnl = equity - account["initial_balance"]

        return {
            "free_balance": free_balance,
            "open_margin": open_margin,
            "balance": wallet_balance,
            "equity": equity,
            "unrealized_pnl": unrealized,
            "initial_balance": account["initial_balance"],
            "total_pnl": total_pnl,
            "total_trades": account.get("total_trades", 0),
            "winning_trades": account.get("winning_trades", 0),
            "losing_trades": account.get("losing_trades", 0),
            "win_rate": (
                account["winning_trades"] / account["total_trades"] * 100
                if account.get("total_trades", 0) > 0
                else 0
            ),
            "open_positions": open_count,
            "max_drawdown": account.get("max_drawdown", 0),
            "profit_factor": account.get("profit_factor", 0),
        }
