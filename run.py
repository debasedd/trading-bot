"""
run.py — Titik masuk utama (Entry Point) Sistem AI Trading Agent Kripto Futures.

Menjalankan:
1. Inisialisasi Database SQLite
2. Inisialisasi Event Bus & Data Feed (Binance Futures Publik)
3. Inisialisasi & Peluncuran 4 Agen Otonom:
   - NewsAgent (Pemindai Berita & Sentimen)
   - AnalysisAgent (Teknikal, Fundamental, ML)
   - DecisionAgent (Penalaran Keputusan & Risiko)
   - ExecutionAgent (Simulasi Eksekusi Paper Trading)
4. Scheduler Penyesuaian Jam Pasar AS
5. Dashboard Visual Real-time (Dash/Plotly di thread terpisah)
"""

import asyncio
import logging
import os
import signal
import sys
import pathlib
import threading
from typing import List, Optional

# Bungkam seluruh log akses HTTP polling Werkzeug & Flask di terminal
from werkzeug.serving import WSGIRequestHandler


def _silence_request_log(self, *args, **kwargs) -> None:
    """No-op pengganti `WSGIRequestHandler.log_request`.

    Polling dashboard setiap 500ms jadi membanjiri terminal kalau tidak
    dibungkam. Parameter sengaja dibiarkan apa adanya agar signature-nya
    tetap cocok dengan yang dipanggil Werkzeug.
    """
    del self, args, kwargs


logging.getLogger("werkzeug").setLevel(logging.ERROR)
logging.getLogger("flask").setLevel(logging.ERROR)
WSGIRequestHandler.log_request = _silence_request_log

from core.config import LiveConfig, get_config
from data.order_book_recorder import OrderBookRecorder
from core.logger import (
    end_quiet_mode,
    get_logger,
    setup_logger,
    stage,
    stage_done,
    step,
)
from core.event_bus import EventBus
from core.scheduler import AgentScheduler
from database.db import init_db, close_db, get_db
from database.repository import Repository
from data.price_feed import PriceFeed
from data.macro_fetcher import MacroFetcher
from data.sentiment import SentimentAnalyzer
from trading.paper_engine import PaperTradingEngine
from agents.news_agent import NewsAgent
from agents.analysis_agent import AnalysisAgent
from agents.decision_agent import DecisionAgent
from agents.execution_agent import ExecutionAgent
from agents.direction_agents import DirectionEnsembleAgent
from dashboard.app import run_dashboard

logger = get_logger("main")


# ─── Ritme & pelaporan error `_execution_loop` ───────────────────────
#
# `_execution_loop` adalah detak jantung bot: di mode live dia yang
# mengirim order dan yang mengawasi SL/TP tiap 0.3 detik. Karena itu
# capture error-nya harus dua-duanya: loop hidup TIDAK BOLEH mati, dan
# kegagalannya juga TIDAK BOLEH jadi tidak terlihat. Dua angka di bawah
# menjaga keduanya.
#
# Angka tetap, bukan config — sama seperti cadence 0.3 s yang sudah
# tertulis di docstring loop ini dan di banner startup. Dua sumber
# angka untuk satu hal lebih buruk daripada satu angka yang perlu
# restart untuk diubah; konsekuensinya (0.3 s x N kegagalan) ditulis
# sebagai perkiraan kasar di pesan CRITICAL, bukan sebagai janji.
_EXEC_TICK_SECONDS = 0.3
# Kegagalan-kegagalan pertama ditulis dengan traceback penuh. Di
# sinilah akar masalahnya masih ada (mis. LiveExecutor yang belum punya
# `_last_prices`, atau ImportError di dalam agen) — bug sebelumnya
# membuang info itu karena `except Exception` hanya menulis `str(e)`.
_EXEC_FAIL_TRACE_FIRST = 3
# Setelah itu, error yang sama cukup satu baris ringkas setiap kelipat
# 50 kegagalan (setiap ~15 detik): cukup untuk membuktikan loop masih
# hidup, tidak sampai ribuan baris identik per menit.
_EXEC_FAIL_LOG_EVERY = 50
# 100 kegagalan berturut-turut = ~30 detik tanpa satu pun siklus sukses.
# Di titik ini ini bukan lagi noise transien, jadi naik ke CRITICAL satu
# kali, lalu kembali ke baris periodik di atas.
_EXEC_FAIL_CRITICAL = 100

# Berapa lama `shutdown()` menunggu live poll loop benar-benar berhenti.
# Loop itu memanggil bursa lewat `asyncio.to_thread`, jadi satu putaran bisa
# tersedak beberapa detik menunggu panggilan bursa yang sedang berjalan
#(timeout jaringan). Menunggu terlalu lama membuat Ctrl+C terasa macet;
# terlalu singkat meninggalkan task yang masih hidup saat koneksi ditutup.
# Lima detik menutup kedua kasus tanpa menahan operator.
_LIVE_TASK_SHUTDOWN_TIMEOUT = 5.0

# Database terpisah untuk rekorder order book. Bot menulis ke
#  dari belasan task, dan SQLite hanya mengizinkan satu
# writer - rekorder di file yang sama kehilangan 82% tick-nya.
# Plus: data ini lebih besar dan tidak pernah dibaca saat runtime, jadi
# mencampurkannya dengan ledger transaksi hanya memperlambat order.
ORDER_BOOK_DB_PATH = "data_store/order_book.db"


def _print_safe(text: str):
    """
    Cetak teks yang mungkin berisi karakter blok/box-drawing.

    Console Windows default-nya cp1252 dan tidak punya glyph `╔` atau
    `█`. `print()` biasa akan melempar UnicodeEncodeError dan menggagalkan
    seluruh startup — termasuk saat stdout di-redirect ke file atau pipe.

    Kita ganti encoding stdout ke UTF-8 dengan errors="replace" lebih dulu,
    lalu tetap print seperti biasa. Kalau tidak bisa, terakhiran kita
    tulis ke sys.stderr, yang tidak pernah menggagalkan startup.
    """
    try:
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
        print(text)
    except UnicodeEncodeError:
        try:
            sys.stderr.write(text + "\n")
        except Exception:
            pass
    except Exception:
        pass


def print_banner():
    """Tampilkan banner ASCII futuristik nan berwarna di terminal."""
    banner = """
\033[38;5;51m╔═══════════════════════════════════════════════════════════════════════════════════════════════╗
║                                                                                               ║
║   \033[38;5;48m██████╗ ██╗  ██╗███████╗ ██████╗███████╗██████╗ ███████╗\033[38;5;51m                                    ║
║  \033[38;5;48m██╔═████╗╚██╗██╔╝██╔════╝██╔════╝██╔════╝╚════██╗██╔════╝\033[38;5;51m                                    ║
║  \033[38;5;48m██║██╔██║ ╚███╔╝ █████╗  ██║     █████╗   █████╔╝███████╗\033[38;5;51m    \033[1;37m0XF3CE25 // AUTONOMOUS AI\033[38;5;51m       ║
║  \033[38;5;48m████╔╝██║ ██╔██╗ ██╔══╝  ██║     ██╔══╝  ██╔═══╝ ╚════██║\033[38;5;51m    \033[38;5;220mCRYPTO FUTURES TERMINAL\033[38;5;51m         ║
║  \033[38;5;48m╚██████╔╝██╔╝ ██╗██║     ╚██████╗███████╗███████╗███████║\033[38;5;51m    \033[38;5;141mHIGH-FREQUENCY SCALPER\033[38;5;51m          ║
║   \033[38;5;48m╚═════╝ ╚═╝  ╚═╝╚═╝      ╚═════╝╚══════╝╚══════╝╚══════╝\033[38;5;51m                                    ║
║                                                                                               ║
╠═══════════════════════════════════════════════════════════════════════════════════════════════╣
║  \033[1;32m● ARCHITECTURE\033[38;5;51m : 4 SPECIALIST AGENTS (NEWS · ANALYSIS · DECISION · EXECUTION)               ║
║  \033[1;33m● ENGINE MODE \033[38;5;51m : ULTRA-FAST HIGH-FREQUENCY SCALPING (SUB-SECOND EXECUTION)                   ║
║  \033[1;34m● MARKET FEED \033[38;5;51m : HYPERLIQUID WEBSOCKET (178 PERPS) + BINANCE FUTURES DATA                     ║
║  \033[1;35m● AI MODELS   \033[38;5;51m : FINBERT SENTIMENT + RANDOMFOREST ML + TECHNICAL CONFLUENCE                    ║
║  \033[1;36m● BLOOMBERG UI\033[38;5;51m : RETRO DASHBOARD ACTIVE ON \033[4;37mhttp://127.0.0.1:8050\033[0;38;5;51m                              ║
╚═══════════════════════════════════════════════════════════════════════════════════════════════╝\033[0m
"""
    _print_safe(banner)


def print_startup_summary(config, symbols, mode="paper", address=None):
    """
    Cetak ringkasan subsistem berbingkai ASCII rapi.

    `mode` dan `address` ditampilkan di sini, bukan hanya di menu. Layar
    yang menyebut wallet mana yang sedang dipakai adalah satu-satunya
    tempat operator bisa memverifikasi sekilas bahwa botnya tidak sedang
    memegang akun yang salah.
    """
    sym_list = ", ".join(s.split("/")[0] for s in symbols[:8])

    if mode == "paper":
        mode_line = (
            "│  \033[38;5;48m✔\033[0m "
            "\033[1;32mMODE: SIMULASI (PAPER)\033[0m"
            "\033[38;5;244m — nol order ke bursa\033[0;38;5;239m         │"
        )
    else:
        label = "TESTNET" if mode == "testnet" else "MAINNET"
        colour = "\033[1;33m" if mode == "testnet" else "\033[1;41;97m"
        mode_line = (
            "│  \033[38;5;48m✔\033[0m "
            "{colour}\033[1mMODE LIVE: {label}\033[0m"
            "\033[38;5;255m  {addr}\033[0;38;5;239m   │"
        ).format(colour=colour, label=label,
                 addr=(address or "tidak diketahui")[:42])

    card = f"""
\033[38;5;239m┌── \033[1;37mSUBSYSTEM STATUS MATRIX\033[0;38;5;239m ────────────────────────────────────────────────────────┐
│  \033[38;5;48m✔\033[0m Database Engine       : \033[38;5;255mSQLite WAL Mode (data_store/trading_bot.db)\033[0;38;5;239m     │
{mode_line}
│  \033[38;5;48m✔\033[0m Scalping Frequency    : \033[38;5;220m0.3s Loop\033[0m \033[38;5;239m│\033[0m Batch: \033[38;5;220m{config.scalping.batch_size}\033[0m \033[38;5;239m│\033[0m Max Pos: \033[38;5;220m{config.risk.max_open_positions}\033[0;38;5;239m     │
│  \033[38;5;48m✔\033[0m Targets & Risk Limits : TP: \033[32m+{config.scalping.fast_tp_pct:.2%}\033[0m \033[38;5;239m│\033[0m SL: \033[31m-{config.scalping.tight_sl_pct:.2%}\033[0m \033[38;5;239m│\033[0m Lev: \033[38;5;141m{config.risk.default_leverage}x\033[0;38;5;239m        │
│  \033[38;5;48m✔\033[0m Active Top Symbols    : \033[38;5;51m{sym_list}\033[0;38;5;239m             │
└───────────────────────────────────────────────────────────────────────────────────┘\033[0m
"""
    _print_safe(card)


def _choose_mode():
    """
    Tentukan mode jalan lewat menu terminal.

    Selalu mengembalikan keputusan yang valid. Kalau live tidak lolos
    konfirmasi, hasilnya `paper` — bukan error. Bot yang gagal start karena
    operator ragu-ragu lebih berbahaya daripada bot yang jalan dalam mode
    simulasi yang diketahui aman.

    `--paper` memaksa simulasi tanpa menampilkan menu; itu satu-satunya
    cara menjalankan bot tanpa interaksi.
    """
    from trading.live.console import ModeDecision

    cfg = get_config().live

    if "--paper" in sys.argv or "--non-interactive" in sys.argv:
        _print_safe("  Mode simulasi dipilih lewat argumen baris perintah.")
        return ModeDecision(mode="paper", reason="--paper")

    # Argumen mode eksplisit. Tanpa blok ini, `python run.py --testnet`
    # diam-diam jatuh ke simulasi karena tidak ada yang membaca
    # argumennya -- dan penutup yang diserahkan ke bursa mengira ini live.
    for flag, mode in (("--testnet", "testnet"), ("--live", "mainnet")):
        if flag in sys.argv:
            if mode == "mainnet":
                _print_safe("")
                _print_safe(
                    "  PERINGATAN: --live memakai uang sungguhan.")
                _print_safe(
                    "  Pastikan testnet sudah terbukti dulu, dan wallet")
                _print_safe(
                    "  hanya berisi jumlah yang sanggup hilang.")
                _print_safe("")
            else:
                _print_safe("  Mode testnet dipilih lewat argumen perintah.")
            return ModeDecision(mode=mode, reason=flag)

    try:
        from trading.live.console import ask_mode

        return ask_mode(cfg)
    except KeyboardInterrupt:
        _print_safe("\n  Dibatalkan oleh pengguna. Bot tidak berjalan.")
        return ModeDecision(mode="paper", refused=True, reason="dibatalkan")
    except ImportError as exc:
        _print_safe(f"  Menu live tidak tersedia ({exc}). Mode simulasi dipakai.")
        return ModeDecision(mode="paper", refused=True, reason="menu gagal")


class TradingBotApp:
    """Aplikasi terpadu AI Trading Bot."""

    def __init__(self, decision=None):
        self.config = get_config()
        self.decision = decision
        self.mode = getattr(decision, "mode", "paper") if decision else "paper"
        self.event_bus = EventBus()
        self.scheduler = AgentScheduler()
        self.price_feed = PriceFeed(self.event_bus)
        self.sentiment_analyzer = SentimentAnalyzer(use_finbert=True)
        self.macro_fetcher = MacroFetcher()
        self.paper_engine = PaperTradingEngine(self.event_bus)

        # Agen-agen
        self.news_agent: NewsAgent = None
        self.analysis_agent: AnalysisAgent = None
        self.decision_agent: DecisionAgent = None
        self.execution_agent: ExecutionAgent = None
        self.ensemble_agent: DirectionEnsembleAgent = None

        self._running = False
        self._background_tasks: List[asyncio.Task] = []
        self._repo: Optional[Repository] = None
        # Rekorder order book. None kalau gagal start atau dimatikan lewat
        # `scalping.order_book_interval_s: 0`.
        self._ob_recorder = None

    async def _get_repo(self) -> Repository:
        """Repository tunggal untuk seluruh operasi DB di level aplikasi."""
        if self._repo is None:
            self._repo = Repository(await get_db())
        return self._repo

    async def _build_live_executor(self):
        """
        Bangun adapter live lengkap dengan gerbang dan loop.

        Urutannya penting dan tidak boleh diacak:

        1. `SafetyGate` dibuat DULU. Gate yang hanya siap setelah
           koneksi bursa bisa terlewati kalau ada order yang datang
           duluan.
        2. Health check dijalankan SEBELUM loop dimulai. Kalau posisi
           lokal dan bursa sudah berbeda saat start, itu harus
           ketahuan sebelum order pertama.
        3. Kalau health check gagal, JANGAN mulai loop. Bot yang
           menjalankan loop dengan state tidak sinkron akan menebak
           posisi -- dan menebak posisi berarti menggandakan eksposur.
        """
        import os

        from core.config import LiveConfig
        from trading.live.client import LiveExchange, mask_address
        from trading.live.engine import LiveEngine
        from trading.live.executor import LiveExecutor
        from trading.live.safety import SafetyGate

        live_cfg = LiveConfig()
        key = (os.environ.get(live_cfg.private_key_env) or "").strip()
        if not key:
            raise RuntimeError(
                "{} belum diisi; mode live tidak bisa dijalankan".format(
                    live_cfg.private_key_env))

        api_wallet = (os.environ.get("HYPERLIQUID_ACCOUNT_ADDRESS")
                      or "").strip() or None
        testnet = self.mode == "testnet"

        gate = SafetyGate(live_cfg)
        exchange = LiveExchange(key, testnet=testnet,
                                account_address=api_wallet)
        # Preflight SEBELUM LiveEngine dibuat. `Info` dibangun lazy sejak
        # `c224b58`, jadi constructing `LiveExchange` tidak lagi menyentuh
        # jaringan — tanpa cek ini, bot bisa start di mesin yang tidak punya
        # koneksi dan baru gagal (atau lebih buruk, salah baca posisi) saat
        # order pertama.
        #
        # `allow_api_wallet` hanya menyala kalau operator memang memakai
        # pemisahan wallet. Tanpa itu, signer yang berbeda dari query
        # address dianggap salah ketik — dan memang harus.
        exchange.preflight(allow_api_wallet=bool(api_wallet))
        engine = LiveEngine(gate=gate, exchange=exchange, cfg=live_cfg)

        health = await engine.health_check()
        if not health["ok"]:
            raise RuntimeError(
                "Health check gagal saat start, loop TIDAK dijalankan: "
                + "; ".join(health["problems"][:3]))

        logger.info("Live siap: %s @ %s (%d aset)",
                    mask_address(exchange.query_address), exchange.base_url,
                    len(exchange.asset_rules()))

        async def _decide():
            """Placeholder: agent sudah pushes order lewat event bus."""
            return None

        # Executor dibuat DULU, lalu disambungkan ke engine.
        #
        # Urutan ini bukan pilihan gaya: `poll_exchange_fills()` membaca
        # fill dari bursa, dan tanpa executor yang mencatatnya, fill itu
        # hanya dibaca lalu dibuang -- baris posisi tetap OPEN,
        # `realized_pnl` tidak pernah terisi, dan `DAILY_LOSS_LIMIT`
        # tidak punya sumber angka. `engine.poll_exchange_fills` memanggil
        # `on_exchange_fill` setiap kali menemukan fill CLOSE.
        executor = LiveExecutor(engine, self.event_bus)
        engine.on_exchange_fill = executor.record_exchange_fills

        self._live_task = asyncio.create_task(
            engine.run_loop(interval=5.0, on_decision=_decide))
        self._live_engine_obj = engine
        # `event_bus` wajib diteruskan: tanpa itu `LiveExecutor._publish`
        # jadi no-op dan live fill tidak pernah sampai ke HUD. `ExecutionAgent`
        # dan `PaperTradingEngine` dibangun dengan bus yang sama di bawah,
        # jadi tiga jalur ini berpublikasi ke tempat yang satu.
        return executor

    async def initialize(self):
        """Inisialisasi semua subsistem."""
        logger.info("=== MEMULAI SISTEM AI TRADING AGENT (PAPER TRADING) ===")

        # 1. Database
        stage("Preparing database")
        await init_db()
        stage_done("Database siap (SQLite WAL)")
        logger.info("Database lokal SQLite siap (WAL mode)")

        # 2. Paper Engine & Saldo Virtual
        await self.paper_engine.initialize()
        logger.info(f"Paper Engine aktif — saldo awal: {self.config.account.initial_balance} USDT")

        # 3. Price Feed
        stage("Connecting to exchanges")
        await self.price_feed.initialize()
        stage_done("Exchange terhubung")

        # 3b. Deteksi & Perbarui Top Volume Futures jika diaktifkan
        scanning_cfg = getattr(self.config, "scanning", None)
        if scanning_cfg and scanning_cfg.dynamic_top_volume:
            stage(
                f"Scanning top {scanning_cfg.top_n} volume futures",
                total=scanning_cfg.top_n,
            )
            logger.info(f"Memindai Top {scanning_cfg.top_n} Volume Futures...")
            top_syms = await self.price_feed.discover_top_volume_symbols(limit=scanning_cfg.top_n)
            step()
            if top_syms:
                self.config.symbols = top_syms
                logger.info(f"Simbol aktif diperbarui ke Top {len(top_syms)} Volume: {top_syms}")
                stage_done(f"Top {len(top_syms)} symbols: "
                           + ", ".join(s.split('/')[0] for s in top_syms[:5])
                           + "…")
            else:
                stage_done("Scan selesai — memakai daftar config")

        # 3c. Mulai streaming WebSocket Hyperliquid untuk orderbook & lilin live
        stage("Opening market data stream")
        await self.price_feed.start_streaming()
        stage_done("WebSocket tersambung")

        # 4. Sentiment Analyzer
        stage("Loading sentiment models")
        await self.sentiment_analyzer.initialize()
        stage_done("Model sentiment siap")

        # 5. Inisialisasi Agen
        stage("Starting agents", total=5)
        self.news_agent = NewsAgent(self.event_bus, self.sentiment_analyzer)
        step(label="Starting agents · news")
        self.analysis_agent = AnalysisAgent(
            self.event_bus,
            self.price_feed,
            self.macro_fetcher,
            self.sentiment_analyzer,
        )
        step(label="Starting agents · analysis")
        self.decision_agent = DecisionAgent(self.event_bus, self.paper_engine.risk_manager)
        step(label="Starting agents · decision")

        # Engine eksekusi: paper atau live, tergantung mode yang dipilih.
        # Keduanya memenuhi antarmuka yang sama, jadi ExecutionAgent tidak
        # perlu tahu mana yang aktif.
        self.live_engine = None
        executor = self.paper_engine
        if self.mode in ("testnet", "mainnet"):
            executor = await self._build_live_executor()
        self.executor = executor

        self.execution_agent = ExecutionAgent(self.event_bus, executor)
        step(label="Starting agents · execution")

        # 5b. Ensemble arah LONG/SHORT (multi-agen).
        # Dibangun setelah price feed streaming aktif supaya market_store
        # sudah berisi order book, harga, dan tape trade.
        if self.config.ensemble.enabled:
            self.ensemble_agent = DirectionEnsembleAgent(
                self.event_bus,
                self.config.symbols,
                self.config.database_path,
            )
            await self.ensemble_agent.initialize()
        else:
            self.ensemble_agent = None
            logger.info("Ensemble arah LONG/SHORT dinonaktifkan via config")

        await self.analysis_agent.initialize()
        await self.decision_agent.initialize()
        await self.execution_agent.initialize()
        step(label="Starting agents · ready")
        stage_done("5 agents + direction ensemble siap")
        logger.info("Semua agen otonom siap")

        # 6. Prefetch data lilin historis untuk semua simbol
        await self._prefetch_historical_candles()

        # 7. Update awal makroekonomi
        stage("Reading macro calendar")
        await self.analysis_agent.update_macro()
        stage_done("Kalender makro dimuat")

        # Cetak ringkasan status sistem berbingkai ASCII
        print_startup_summary(
            self.config, self.config.symbols,
            mode=self.mode,
            address=getattr(self.decision, "address", None),
        )

    async def _prefetch_historical_candles(self):
        """Ambil data candle historis awal agar chart dan indikator langsung berisi."""
        symbols = self.config.symbols
        timeframes = ["1m", "5m", "1h"]
        total = len(symbols) * len(timeframes)
        stage(f"Fetching market data · {len(symbols)} symbols", total=total)
        logger.info(
            f"Mengunduh data candle historis awal untuk {len(symbols)} simbol..."
        )

        done = 0
        for symbol in symbols:
            for tf in timeframes:
                try:
                    limit_count = 240 if tf == "1m" else 120
                    candles = await self.price_feed.fetch_ohlcv(symbol, timeframe=tf, limit=limit_count, save_to_db=True)
                    logger.info(f"Lilin awal {symbol} ({tf}): {len(candles)} candle tersimpan")
                except Exception as e:
                    # Prefetch gagal tidak menghentikan bot - harga tetap
                    # mengalir lewat websocket dan candle akan dibangun
                    # dari situ. Tapi penyebabnya tidak selalu jelas, jadi
                    # traceback ikut: "gagal prefetch" tanpa jejak tidak
                    # bisa dibedakan dari outage feed.
                    logger.warning(
                        "Gagal prefetch %s %s: %s", symbol, tf, e,
                        exc_info=True,
                    )
                done += 1
                step(label=f"Fetching {symbol.split('/')[0]} {tf}")

        stage_done(f"{done} candle sets dimuat")

    async def _prune_direction_snapshots(self):
        """
        Buang snapshot arah lama agar tabel tidak tumbuh tanpa batas.

        Tabel ini ditulis ensemble setiap `ensemble.interval_seconds` untuk
        setiap simbol: pada 5 detik x 10 simbol, itu 172.800 baris per hari.
        Tanpa prune, `get_latest_direction_snapshots` — yang dipakai HUD untuk
        menggambar konstelasi neural net — makin lama harus menyapu tabel yang
        terus membesar, dan itu langsung terasa sebagai jitter di dashboard.

        Nilai interval dan batas simpan diambil dari config, bukan ditulis
        langsung di sini, supaya batas retensi bisa diubah tanpa menyentuh
        kode.
        """
        keep = int(getattr(self.config, "snapshot_keep_per_symbol", 120))
        try:
            repo = await self._get_repo()
            await repo.prune_direction_snapshots(keep_per_symbol=keep)
            logger.debug(f"Prune snapshot arah selesai (keep {keep} per simbol)")
        except Exception as e:
            # Kegagalan prune tidak boleh menjatuhkan sistem: penuhnya disk
            # adalah masalah operasional, bukan alasan berhenti bertransaksi.
            logger.warning("Prune snapshot arah gagal: %s", e, exc_info=True)

    async def _prune_agent_logs(self):
        """
        Pangkas `agent_logs` supaya tabelnya tidak tumbuh tanpa batas.

        Berbeda dari `direction_snapshots` yang prune per simbol, ini cukup
        memotong yang paling lama secara global — tidak ada dimensi yang perlu
        dijaga per simbol seperti pada snapshot arah.
        """
        keep = int(getattr(self.config, "agent_log_keep", 5000))
        try:
            repo = await self._get_repo()
            await repo.prune_agent_logs(keep=keep)
            logger.debug(f"Prune agent_logs selesai (keep {keep})")
        except Exception as e:
            # Sama seperti prune snapshot: kegagalan di sini adalah
            # masalah operasional, bukan alasan berhenti bertransaksi.
            # Traceback ikut karena "gagal prune" tanpa jejak akan
            # terlihat seperti tabel yang tumbuh sendiri - dan gejala itu
            # berbeda dari penyebabnya.
            logger.warning("Prune agent_logs gagal: %s", e, exc_info=True)

    async def _maintenance_loop(self):
        """
        Task latar untuk perawatan database yang berjalan terus-menerus.

        Dipisah dari `direction_snapshot_prune` yang dijadwalkan APScheduler
        supaya prune pertama benar-benar terjadi di menit pertama, bukan satu
        jam setelah boot — pada jam pertama itulah tabel tumbuh paling cepat
        dan belum ada satu pun baris lama untuk dipangkas.
        """
        interval = max(60, int(getattr(self.config, "snapshot_prune_interval", 3600)))
        while self._running:
            await asyncio.sleep(interval)
            if not self._running:
                break
            await self._prune_direction_snapshots()

    def setup_scheduler(self):
        """Daftarkan jadwal tugas setiap agen dengan penyesuaian jam pasar AS."""
        intervals = self.config.agent_intervals

        # Penjadwalan Pembaruan Berkala Top Volume Futures
        scanning_cfg = getattr(self.config, "scanning", None)
        if scanning_cfg and scanning_cfg.dynamic_top_volume:
            async def _refresh_top_volume():
                logger.info("Memperbarui ranking Top Volume Futures berkala...")
                new_top = await self.price_feed.discover_top_volume_symbols(limit=scanning_cfg.top_n)
                if new_top:
                    self.price_feed.update_symbols(new_top)

            self.scheduler.add_fixed_job(
                name="refresh_top_volume",
                func=_refresh_top_volume,
                interval_seconds=scanning_cfg.refresh_interval,
            )

        # Agen Berita (NewsAgent)
        self.scheduler.add_agent_job(
            name="news_agent",
            func=self.news_agent.run_cycle,
            interval_normal=intervals.news_agent,
            interval_us_open=intervals.news_agent_us_open,
            start_immediately=True,
        )

        # Agen Analisis (AnalysisAgent)
        self.scheduler.add_agent_job(
            name="analysis_agent",
            func=self.analysis_agent.run_cycle,
            interval_normal=intervals.analysis_agent,
            interval_us_open=intervals.analysis_agent_us_open,
            start_immediately=True,
        )

        # Agen Pengambil Keputusan (DecisionAgent)
        self.scheduler.add_agent_job(
            name="decision_agent",
            func=self.decision_agent.run_cycle,
            interval_normal=intervals.decision_agent,
            interval_us_open=intervals.decision_agent_us_open,
            start_immediately=False,
        )

        # Ensemble arah LONG/SHORT — sumber tunggal untuk HUD scanner DAN
        # DecisionAgent. Dafar di sini (bukan di initialize) supaya ticker
        # pertama-tama jelas setelah price feed & scheduler hidup.
        if self.ensemble_agent is not None:
            self.scheduler.add_fixed_job(
                name="direction_ensemble",
                func=self.ensemble_agent.run_cycle,
                interval_seconds=self.config.ensemble.interval_seconds,
            )

            # Buang snapshot lama secara berkala; tanpa prune tabel tumbuh
            # ~172.800 baris/hari pada interval 5 detik x 10 simbol. Interval
            # dan batas retensi berasal dari config, bukan angka tetap di sini.
            self.scheduler.add_fixed_job(
                name="direction_snapshot_prune",
                func=self._prune_direction_snapshots,
                interval_seconds=max(
                    60, int(getattr(self.config, "snapshot_prune_interval", 3600))
                ),
            )

        # `agent_logs` juga tumbuh tanpa batas: decision loop menulis satu
        # baris per siklus (0,3 detik) termasuk saat tidak ada yang terjadi,
        # jadi ~214 baris/menit atau ~309.000 per hari. Tabel ini TIDAK
        # disentuh `prune_direction_snapshots`, jadi butuh jalurnya sendiri.
        self.scheduler.add_fixed_job(
            name="agent_log_prune",
            func=self._prune_agent_logs,
            interval_seconds=max(
                60, int(getattr(self.config, "agent_log_prune_interval", 1800))
            ),
        )

        # Update Makroekonomi (6 jam)
        self.scheduler.add_fixed_job(
            name="macro_update",
            func=self.analysis_agent.update_macro,
            interval_seconds=intervals.macro_data,
        )

        # Analisis Batch FinBERT (15 menit)
        self.scheduler.add_fixed_job(
            name="finbert_batch",
            func=self.analysis_agent.run_finbert_batch,
            interval_seconds=intervals.finbert_batch,
        )

        # Snapshot Saldo & Equity (5 menit)
        self.scheduler.add_fixed_job(
            name="balance_snapshot",
            func=self.paper_engine.position_manager.take_balance_snapshot,
            interval_seconds=intervals.balance_snapshot,
        )

    async def _execution_loop(self):
        """
        Loop responsif eksekusi order & pemantauan posisi (tiap 0.3 detik untuk scalping).

        Dua aturan yang tidak boleh dilanggar di sini:

        1. Loop TIDAK BOLEH mati. Di mode live, posisi yang terbuka hanya
           dikelola oleh siklus `check_positions()` di bawah; kalau loop
           ini mati karena satu exception, tidak ada yang lagi memantau
           SL/TP, tidak ada yang mengunci breakeven, dan tidak ada yang
           menutup posisi expired. Posisi jadi terlantar, bukan berhenti.
        2. Loop tidak boleh diam-diam gagal. Bug sebelumnya menulis
           `logger.error(f"...: {e}")` — hanya pesan exception, tanpa
           traceback. Antarmuka live yang rusak total (mis. executor yang
           tidak punya `get_price`) lalu menghasilkan log yang terlihat
           sehat: ribuan baris IDENTIK per menit, nol order, nol eskalasi.
           Traceback penuh sekarang ditulis, dan kegagalan berulang
           dihitung supaya yang penting tidak tenggelam di antara baris
           yang sama.
        """
        logger.info("Execution loop dimulai (scalping mode: 0.3s interval)")

        # Berapa banyak siklus gagal BERTURUT-TURUT, bukan total. Satu
        # iterasi sukses di tengah-tengah me-reset-nya ke nol, jadi error
        # yang hanya kadang muncul (retry/network blip) tidak pernah
        # dinaikkan jadi CRITICAL, sedangkan error yang benar-benar
        # permanen akan terus menumpuk dan akhirnya memicunya.
        _exec_failures = 0

        while self._running:
            try:
                # 1. Jalankan siklus eksekusi untuk proses order tertunda
                await self.execution_agent.run_cycle()
                # 2. Pantau SL/TP, likuidasi, auto-close expired, scalp TP
                await self.execution_agent.check_positions()
            except asyncio.CancelledError:
                # INI BUKAN ERROR. `shutdown()` membatalkan task ini, dan
                # pada Python 3.8+ CancelledError turun dari BaseException
                # — bukan Exception — sehingga `except Exception` di bawah
                # memang TIDAK menangkapnya dan pembatalan merambat apa
                # adanya (pola yang sama dengan run_cycle di
                # base_agent.py:99-110).
                #
                # Ditulis eksplisit hanya sebagai jangkar: kalau suatu saat
                # handler ini di-refactor menjadi `except BaseException`,
                # pembatalan shutdown akan tertangkap dan dihitung sebagai
                # kegagalan — dan loop justru berputar terus alih-alih mati.
                logger.debug("Execution loop dibatalkan (shutdown)")
                raise
            except Exception as e:
                _exec_failures += 1
                if _exec_failures <= _EXEC_FAIL_TRACE_FIRST:
                    # Traceback penuh di sini, bukan cuma `str(e)`:
                    # inilah jendela di mana akar masalahnya masih bisa
                    # dibaca. Setelah `_EXEC_FAIL_TRACE_FIRST` baris, akar
                    # masalahnya sudah tercetak, jadi pengulangan tidak
                    # menambah informasi apa pun.
                    logger.exception(
                        "Error pada execution loop (kegagalan %d berturut-turut): %s",
                        _exec_failures, e)
                elif _exec_failures == _EXEC_FAIL_CRITICAL:
                    # Satu kali, bukan tiap 0.3 detik selamanya. ~30 detik
                    # tanpa satu pun siklus bersih di mode live berarti tidak
                    # ada order, tidak ada pemantauan posisi, dan tidak ada
                    # yang menutup posisi menua. Logger tetap hidup, jadi
                    # eskalasi harus berupa SUARA operator, bukan baris log
                    # ke-6000 yang tidak dibaca siapa pun.
                    logger.critical(
                        "Execution loop gagal %d kali berturut-turut "
                        "(~%.0f detik tanpa satu siklus sukses). Bot masih "
                        "jalan tapi tidak bekerja: tidak ada order baru dan "
                        "posisi terbuka tidak dipantau. Perlu pemeriksaan "
                        "manual — hentikan bot bila ragu. "
                        "Kesalahan terakhir: %s",
                        _exec_failures, _exec_failures * _EXEC_TICK_SECONDS, e,
                        exc_info=True,
                    )
                elif _exec_failures % _EXEC_FAIL_LOG_EVERY == 0:
                    # denyut nadi: membuktikan loop masih hidup tanpa
                    # membanjiri log dengan baris yang identik.
                    logger.error(
                        "Error pada execution loop masih terjadi "
                        "(%d kegagalan berturut-turut): %s",
                        _exec_failures, e)
            else:
                if _exec_failures:
                    logger.info(
                        "Execution loop pulih setelah %d kegagalan berturut-turut",
                        _exec_failures)
                _exec_failures = 0

            # `sleep` SENGAJA di luar try/except. Iterasi yang gagal
            # harus tetap membayar satu tick penuh: kalau sleep-nya ikut
            # masuk blok error lalu dilewati, loop akan berputar panas
            # pada exception yang sama dan menaburkan error tanpa jeda.
            await asyncio.sleep(_EXEC_TICK_SECONDS)

    async def _candle_refresh_loop(self):
        """
        Loop penyegaran candle berkala untuk charting real-time.

        Jendela 1m harus cukup lebar untuk MEMPERBAIKI lubang, bukan sekadar
        menambah lilin terbaru. Dengan limit kecil, menit yang sempat terlewat
        (mis. saat proses restart atau jaringan putus) tidak akan pernah kembali
        dan chart menyisakan celah permanen.

        Lebarnya juga menentukan seberapa tua baris yang bisa DIPERBAIKI. UPSERT
        hanya menyentuh menit yang ikut diminta, jadi jendela 120 menit berarti
        baris rusak yang lebih tua dari itu tidak akan pernah direkonsiliasi.
        Jendela 300 menit memberi ruang perbaikan lima jam sekaligus, sementara
        jumlah permintaannya tetap satu per simbol per putaran.
        """
        while self._running:
            try:
                for symbol in self.config.symbols:
                    await self.price_feed.fetch_ohlcv(symbol, timeframe="1m", limit=300, save_to_db=True)
                    await self.price_feed.fetch_ohlcv(symbol, timeframe="5m", limit=120, save_to_db=True)
                # Segarkan juga cache volatilitas. Target TP/SL dinamis
                # membaca candle 1m dari cache ini, jadi tanpa refresh di
                # sini ATR akan membeku pada menit pertama boot.
                await self.paper_engine.refresh_volatility_cache()
            except Exception as e:
                logger.debug(f"Penyegaran candle berkala gagal: {e}")
            await asyncio.sleep(30)

    async def _telemetry_loop(self):
        """Cetak live status bar ringkas di terminal setiap 30 detik."""
        await asyncio.sleep(25)
        while self._running:
            try:
                from database.db import get_db
                db = await get_db()
                # Baris akun terbaru, bukan id = 1. Setelah reset, baris baru
                # tidak lagi memakai id 1 sehingga query id = 1 mengembalikan
                # None dan kartu telemetry diam-diam tidak pernah tampil.
                acc_row = await db.fetchone(
                    "SELECT * FROM account ORDER BY id DESC LIMIT 1"
                )
                if acc_row:
                    acc = dict(acc_row)
                    pos_rows = [dict(r) for r in await db.fetchall("SELECT * FROM positions WHERE status = 'OPEN'")]
                    open_margin = sum(float(p.get("margin") or 0.0) for p in pos_rows)
                    upnl = sum(float(p.get("unrealized_pnl") or 0.0) for p in pos_rows)
                    wallet_bal = acc["balance"] + open_margin
                    equity = wallet_bal + upnl
                    total_pnl = equity - acc["initial_balance"]
                    pnl_color = "\033[1;92m" if total_pnl >= 0 else "\033[1;91m"
                    pnl_sign = "+" if total_pnl >= 0 else ""
                    trades = acc.get("total_trades", 0)
                    wins = acc.get("winning_trades", 0)
                    wr = (wins / trades * 100) if trades > 0 else 0.0

                    card = (
                        f"\n\033[38;5;239m┌─ \033[1;36m[LIVE TELEMETRY]\033[0;38;5;239m "
                        f"────────────────────────────────────────────────────────────────────────┐\n"
                        f"│ \033[1;37mWALLET\033[0m: ${wallet_bal:,.2f} │ \033[1;37mEQUITY\033[0m: ${equity:,.2f} │ "
                        f"\033[1;37mTOTAL PNL\033[0m: {pnl_color}{pnl_sign}${total_pnl:,.2f}\033[0;38;5;239m │ "
                        f"\033[1;37mWIN RATE\033[0m: {wr:.1f}% ({wins}/{trades}) │\n"
                        f"│ \033[1;37mACTIVE POSITIONS\033[0m: {len(pos_rows)} OPEN (Margin: ${open_margin:,.2f} │ Free Margin: ${acc['balance']:,.2f})        │\n"
                        f"└───────────────────────────────────────────────────────────────────────────────────────────┘\033[0m\n"
                    )
                    _print_safe(card)
            except Exception:
                pass
            await asyncio.sleep(30)

    async def repair_candles(self, window_minutes: int = 600):
        """
        Bersihkan dan rekonsiliasi tabel `candles` terhadap bursa.

        Dipakai saat chart menampilkan lilin yang tidak masuk akal. Penyebabnya
        biasanya baris warisan dari build lama — tersimpan pada presisi float32
        dengan volume nol — yang tidak pernah bisa diperbaiki oleh UPSERT karena
        jendela permintaan candleSnapshot hanya selebar beberapa jam.

        Langkah:
          1. Hapus baris yang melanggar invarian OHLC (tidak mungkin dari bursa).
          2. Tarik ulang jendela lebar dari Hyperliquid dan tulis dengan UPSERT.
        """
        from database.db import get_db

        db = await get_db()
        where = (
            "volume < 0 OR open <= 0 OR high <= 0 OR low <= 0 OR close <= 0 "
            "OR high < low OR high < open OR high < close "
            "OR low > open OR low > close"
        )

        before = (await db.fetchone("SELECT COUNT(*) AS n FROM candles"))["n"]
        bad = (await db.fetchone(f"SELECT COUNT(*) AS n FROM candles WHERE {where}"))["n"]
        await db.execute(f"DELETE FROM candles WHERE {where}")
        await db.commit()
        after = (await db.fetchone("SELECT COUNT(*) AS n FROM candles"))["n"]
        logger.info(f"Reparasi candle: {bad} baris gagal validasi dihapus ({before} -> {after})")

        await self.price_feed.initialize()

        # Simbol yang diperbaiki harus mencakup SEMUA simbol yang punya baris di
        # tabel, bukan hanya Top-10 saat ini. Simbol yang tergeser keluar dari
        # peringkat volume tetap meninggalkan baris rusaknya di database, dan
        # baris itu akan muncul lagi begitu simbol tersebut dipilih di chart.
        top = await self.price_feed.discover_top_volume_symbols(limit=10)
        db_symbols = [
            r["symbol"] for r in await db.fetchall("SELECT DISTINCT symbol FROM candles")
        ]
        symbols = sorted(set(top or []) | set(db_symbols) | set(self.config.symbols))
        logger.info(f"Reparasi mencakup {len(symbols)} simbol: {symbols}")

        for symbol in symbols:
            for tf, limit_count in (("1m", window_minutes), ("5m", window_minutes)):
                try:
                    candles = await self.price_feed.fetch_ohlcv(
                        symbol, timeframe=tf, limit=limit_count, save_to_db=True
                    )
                    logger.info(f"Reparasi {symbol} {tf}: {len(candles)} lilin bursa ditulis ulang")
                except Exception as e:
                    logger.warning(f"Reparasi {symbol} {tf} gagal: {e}")
            await asyncio.sleep(0.2)

        remaining = (await db.fetchone(f"SELECT COUNT(*) AS n FROM candles WHERE {where}"))["n"]
        total = (await db.fetchone("SELECT COUNT(*) AS n FROM candles"))["n"]
        logger.info(f"Reparasi selesai: {total} baris total, {remaining} masih gagal validasi")

        await self.price_feed.close()
        await close_db()

    def start_dashboard(self):
        """Jalankan dashboard Dash di thread terpisah."""
        cfg = self.config.dashboard
        dash_thread = threading.Thread(
            target=run_dashboard,
            name="DashBoardThread",
            daemon=True,
        )
        dash_thread.start()
        logger.info(f"Dashboard visual aktif di http://{cfg.host}:{cfg.port}")

    async def run(self):
        """Mulai semua loop dan scheduler."""
        self._running = True

        # Mulai scheduler
        stage("Starting scheduler", total=2)
        self.setup_scheduler()
        step(label="Starting scheduler · jobs")
        self.scheduler.start()
        step(label="Starting scheduler · running")
        stage_done("Scheduler aktif")

        # Jalankan dashboard
        stage("Starting dashboard")
        self.start_dashboard()
        stage_done(
            f"Dashboard http://{self.config.dashboard.host}:"
            f"{self.config.dashboard.port}"
        )

        # Mulai background task
        stage("Starting trading engine", total=5)
        price_task = asyncio.create_task(self.price_feed.price_update_loop())
        step(label="Starting trading engine · price feed")
        exec_task = asyncio.create_task(self._execution_loop())
        step(label="Starting trading engine · execution")
        candle_task = asyncio.create_task(self._candle_refresh_loop())
        step(label="Starting trading engine · candle refresh")
        telem_task = asyncio.create_task(self._telemetry_loop())
        step(label="Starting trading engine · telemetry")
        # Loop pemangkasan snapshot. Job APScheduler dengan interval satu jam
        # saja belum menyentuh apa pun selama jam pertama boot — padahal
        # itulah jendela saat tabel tumbuh paling cepat. Loop ini menutup
        # celah itu, dan `_prune_direction_snapshots` bersifat idempoten
        # sehingga dua pemanggil tidak saling mengganggu.
        maintenance_task = asyncio.create_task(self._maintenance_loop())
        step(label="Starting trading engine · db maintenance")

        # Rekorder order book. Menyimpan fitur mikrostruktur historis
        # (OFI, depth imbalance, spread) yang TIDAK ada di database
        # sebelumnya - `market_store` hanya menyimpan snapshot terakhir,
        # yang hilang begitu restart.
        #
        # Ini bahan untuk melatih model microstructure, dan satu-satunya
        # arah edge yang bukan time-series. Riset di research/bear.py
        # menunjukkan edge time-series yang ada berasal dari drift
        # bullish, bukan prediksi. Data ini yang dibutuhkan untuk
        # menguji hipotesis itu.
        #
        # Kegagalan start recorder TIDAK boleh menghentikan bot:
        # order book adalah bahan riset, bukan syarat bertransaksi.
        #
        # FILE TERPISAH, bukan `self.config.database_path`. Bot ini punya
        # belasan task yang menulis ke DB yang sama (candles, positions,
        # trades, agent_logs, snapshots) dan SQLite hanya mengizinkan satu
        # writer. Terukur: file bersamasolidar menolak 82% tick rekorder
        # dengan "database is locked" - 1000 baris per 100 detik, yang
        # sampai 180.
        #
        # Data order book juga punya siklus hidup berbeda: ukurannya jauh
        # lebih besar, dan tidak pernah dibaca runtime bot. Mencampurkannya
        # dengan ledger transaksi berarti rekorder ikut memperlambat
        # setiap order - untuk sesuatu yang tidak dibutuhkan saat runtime.
        self._ob_recorder = None
        _ob_interval = float(
            getattr(getattr(self.config, "scalping", None),
                    "order_book_interval_s", 1.0) or 0.0)
        if _ob_interval > 0:
            try:
                self._ob_recorder = OrderBookRecorder(
                    db_path=ORDER_BOOK_DB_PATH,
                    symbols=list(getattr(self.config, "symbols", None) or []),
                    interval_s=_ob_interval,
                )
                await self._ob_recorder.start()
                step(label="Starting order book recorder")
            except Exception as exc:  # noqa: BLE001
                self._ob_recorder = None
                logger.warning(
                    "Order book recorder gagal start (%s). Bot tetap "
                    "jalan - data mikrostruktur tidak akan terkumpul, tapi "
                    "tidak ada order yang terganggu.", exc,
                )
        stage_done("Trading engine berjalan")

        # Tahap startup selesai: kembalikan terminal ke mode normal supaya
        # peristiwa runtime (trade, warning, error) tetap terlihat.
        end_quiet_mode()
        print()
        print(f"  [38;5;48m* RUNNING[0m  dashboard "
              f"http://{self.config.dashboard.host}:{self.config.dashboard.port}"
              f"  -  Ctrl+C untuk berhenti\n")

        # Tunggu sampai dihentikan
        while self._running:
            await asyncio.sleep(1)

    async def shutdown(self):
        """Hentikan sistem secara aman (Graceful Shutdown)."""
        logger.info("Menghentikan sistem trading bot...")
        self._running = False

        # Hentikan scheduler
        if self.scheduler.running:
            self.scheduler.shutdown()

        # Hentikan price feed
        self.price_feed.stop()

        # Batalkan background tasks
        for task in self._background_tasks:
            task.cancel()

        # Live poll loop HARI INI juga. Loop ini sengaja tidak masuk
        # `_background_tasks` (dibuat sebelum list itu diisi), jadi tanpa
        # baris di bawah ia tetap berjalan setelah operator menekan
        # Ctrl+C — memanggil bursa di koneksi yang sudah ditutup, atau
        # bersaing dengan `close_db` di bawah.
        #
        # Urutan penting: batalkan DAN tunggu dulu, baru tutup koneksi.
        # Membatalkan tanpa menunggu hanya menandai task; ia masih punya
        # satu giliran eksekusi yang bisa memanggil `self.exchange`.
        if getattr(self, "_live_task", None) is not None:
            self._live_task.cancel()
            try:
                await asyncio.wait_for(
                    self._live_task, timeout=_LIVE_TASK_SHUTDOWN_TIMEOUT
                )
            except asyncio.TimeoutError:
                logger.error(
                    "Live poll loop tidak berhenti dalam %.0f detik. "
                    "Tidak menunggu lagi; proses sedang keluar.",
                    _LIVE_TASK_SHUTDOWN_TIMEOUT,
                )
            except asyncio.CancelledError:
                pass
            except Exception as exc:  # noqa: BLE001
                # Task yang sudah selesai dengan exception harus tetap
                # dilaporkan: di sinilah alasan live mode mati biasanya
                # tercatat, dan shutdown yang menelan exception-nya
                # membuat penyebabnya hilang tanpa jejak.
                logger.error("Live poll loop berakhir dengan error: %s", exc)
            finally:
                self._live_task = None

        # Rekorder order book berhenti sebelum database ditutup. Urutannya penting:
        # recorder punya koneksi SQLite sendiri yang menunjuk file yang sama,
        # dan menutup DB aplikasi lebih dulu akan meninggalkan half-written
        # transaction di file WAL.
        if self._ob_recorder is not None:
            try:
                await self._ob_recorder.stop()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Order book recorder gagal stop: %s", exc)
            self._ob_recorder = None

        # Tutup koneksi exchange & database
        await self.price_feed.close()
        await close_db()

        logger.info("Sistem trading bot berhasil dinonaktifkan.")


async def main():
    """Fungsi utama."""
    print_banner()
    setup_logger()

    # Menu mode. WAJIB lebih dulu, sebelum satu pun subsystem dinyalakan,
    # karena keputusan ini menentukan engine mana yang dipakai.
    decision = _choose_mode()
    app = TradingBotApp(decision)

    loop = asyncio.get_running_loop()

    # Tangani sinyal penghentian di platform yang mendukung
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda: asyncio.create_task(app.shutdown()))
        except NotImplementedError:
            # Windows tidak mendukung add_signal_handler penuh di asyncio
            pass

    try:
        await app.initialize()
        await app.run()
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Sinyal henti diterima dari pengguna.")
    finally:
        await app.shutdown()


async def _fresh_reconcile():
    """
    Rekonsiliasi SEGAR terhadap bursa, untuk perintah pelepasan.

    Mengembalikan `(clean, baris_detail)`.

    `clean` hanya True kalau bursa TERBACA dan posisi lokal cocok dengan
    posisi bursa. Bursa yang tidak terbaca menghasilkan `clean=False`,
    bukan True: ketidaktahuan bukan persetujuan.

    Pakai `LiveEngine.reconcile()` sungguhan, bukan implementasi ulang di
    sini. Melacak ulang perbandingan posisi di tempat lain berarti
    "rekonsiliasi bersih" yang diperiksa di layar operator belum tentu
    sama dengan yang dimaksud bot.
    """
    from core.config import LiveConfig as _LC
    from trading.live.client import LiveExchange
    from trading.live.engine import LiveEngine
    from trading.live.safety import SafetyGate, UnverifiedTracker

    ex = LiveExchange(
        os.environ.get("HYPERLIQUID_PRIVATE_KEY", ""),
        testnet=os.environ.get("TRADEBOT_TESTNET", "1") not in ("0", "false", ""),
        account_address=os.environ.get("HYPERLIQUID_ACCOUNT_ADDRESS"),
    )
    ex.verify_agent_wallet()

    engine = LiveEngine.__new__(LiveEngine)
    engine.cfg = _LC()
    engine.exchange = ex
    engine.gate = SafetyGate(_LC(), env=dict(os.environ))
    engine.positions = {}
    engine.uncertain_orders = {}
    engine.unverified = UnverifiedTracker(cfg=engine.cfg)

    report = await engine.reconcile()
    detail = [
        "bursa terbaca: {}".format(report.get("state_known")),
        "cocok: {}".format(report.get("matched")),
        "hanya lokal: {}".format(report.get("only_local") or "tidak ada"),
        "hanya bursa: {}".format(report.get("only_remote") or "tidak ada"),
        "ukuran beda: {}".format(report.get("size_mismatch") or "tidak ada"),
    ]
    clean = bool(report.get("state_known")) and not any(
        report.get(k) for k in ("only_local", "only_remote", "size_mismatch"))
    return clean, detail


def _operator_release() -> int:
    """
    `python run.py --release-kill-switch` — jalan melepas switch yang nyata.

    `SafetyGate.operator_release()` sudah ada dan menguji keempat
    syaratnya, tapi TIDAK ADA perintah yang memanggilnya. Fungsi yang
    tidak punya jalur produksi adalah fungsi yang tidak ada.

    Urutan di sini penting dan tidak boleh di-shortcut:

      1. Baca state dari disk (state rusak tetap dianggap engaged).
      2. Rekonsiliasi SEGAR terhadap bursa — bukan state lama.
      3. Tampilkan hasil, lalu minta konfirmasi ketik.
      4. Minta alasan. Alasan kosong ditolak.
      5. Panggil `operator_release()`, yang menulis audit log.

    Rekonsiliasi harus SEGAR. Melepas switch berdasarkan rekonsiliasi
    yang berumur sejam lalu berarti melepas switch berdasarkan informasi
    yang sudah bisa basi — persis kesalahan yang menyalakan switch.

    Kode keluar:
      0 — switch terlepas (atau memang sudah tidak aktif).
      3 — ditolak: rekonsiliasi tidak bersih, alasan kosong, konfirmasi
          salah, atau bursa tidak terbaca.
    """
    from trading.live.safety import SafetyGate

    setup_logger()

    # `state_path` diteruskan eksplisit supaya state persisted SELALU
    # dibaca, apa pun isi TRADEBOT_LIVE.
    #
    # Tanpa ini, operator yang menjalankan perintah ini tanpa
    # `TRADEBOT_LIVE=1` akan diberi tahu "tidak ada yang perlu dilepas"
    # padahal kill switch-nya NYALA di disk. Itu jawaban yang salah di
    # situasi yang paling penting: operator mengira sudah bebas padahal
    # switch masih memblokirnya.
    #
    # Perintah ini memang tentang state persisted, jadi membaca state
    # persisted selalu benar di sini.
    gate = SafetyGate(LiveConfig(), env=dict(os.environ),
                      state_path=pathlib.Path("data_store/live_counters.json"))
    if not gate.engaged:
        _print_safe("")
        _print_safe("  Kill switch TIDAK aktif. Tidak ada yang perlu dilepas.")
        return 0

    _print_safe("")
    _print_safe("  KILL SWITCH AKTIF. Melepasnya mengizinkan trading lagi.")
    _print_safe("")
    _print_safe("  Melepas switch TIDAK memperbaiki apa pun. Kalau penyebabnya")
    _print_safe("  belum ditangani, switch menyala lagi — dan setiap kali bot")
    _print_safe("  restart, posisi mungkin sudah berbeda.")
    _print_safe("")
    _print_safe("  Menjalankan rekonsiliasi SEGAR terhadap bursa...")

    try:
        clean, detail = asyncio.run(_fresh_reconcile())
    except Exception as exc:  # noqa: BLE001
        _print_safe("")
        _print_safe("  Gagal menghubungi bursa: {}".format(exc))
        _print_safe("  Kill switch TIDAK dilepas. Tidak bisa memastikan apa pun")
        _print_safe("  berarti tidak boleh melepas switch.")
        return 3

    _print_safe("")
    _print_safe("  Hasil rekonsiliasi:")
    for line in detail:
        _print_safe("    - {}".format(line))

    if not clean:
        _print_safe("")
        _print_safe("  REKONSILIASI TIDAK BERSIH. Kill switch TIDAK dilepas.")
        _print_safe("  Melepas switch sekarang mengembalikan bot ke order dengan")
        _print_safe("  keyakinan salah soal posisi — kondisi yang memicu switch")
        _print_safe("  di tempat pertama.")
        return 3

    _print_safe("")
    _print_safe("  Rekonsiliasi BERSIH.")
    _print_safe("")
    _print_safe("  Untuk melanjutkan, ketik frasa ini persis:")
    _print_safe("")
    _print_safe("      {}".format(SafetyGate.RELEASE_CONFIRMATION_PHRASE))
    _print_safe("")
    try:
        typed = input("  > ")
    except EOFError:
        typed = ""
    _print_safe("")
    _print_safe("  Alasan (wajib — masuk audit log):")
    try:
        reason = input("  > ").strip()
    except EOFError:
        reason = ""

    if not reason:
        _print_safe("")
        _print_safe("  Alasan kosong. Kill switch TIDAK dilepas.")
        return 3

    try:
        released = gate.operator_release(
            reason=reason, typed_confirmation=typed, reconcile_clean=True)
    except (PermissionError, ValueError, RuntimeError) as exc:
        _print_safe("")
        _print_safe("  Ditolak: {}".format(exc))
        return 3

    if released:
        _print_safe("")
        _print_safe("  KILL SWITCH SUDAH DILEPAS.")
        _print_safe("  Audit: {}".format(gate.audit_path))
    else:
        _print_safe("")
        _print_safe("  Kill switch tidak terlepas (tidak engaged?).")
    return 0


def _cli(argv=None) -> int:
    """
    Titik masuk proses. Mengembalikan KODE KELUAR.

    Dipisah dari blok `if __name__ == "__main__"` supaya kontraknya bisa
    diuji: "gagal konfigurasi live berhenti dengan kode 2" adalah
    Pernyataan "gagal konfigurasi live berhenti dengan kode 2" adalah
    pernyataan tentang PERILAKU, dan membuktikannya lewat
    `inspect.getsource` hanya membuktikan bahwa teks tertentu ada di file.
    Test yang begitu tetap hijau kalau pemanggilnya dibungkus `except`
    yang menelan exception, atau kalau exception-nya dilempar di jalur
    kode yang tidak pernah dieksekusi.

    Kode keluar:
      0 — bot selesai normal, atau KeyboardInterrupt.
      2 — konfigurasi/jalur live tidak memenuhi syarat. Pesan dicetak
          lewat `_print_safe` supaya tidak ikut crash di Windows cp1252.
    """
    argv = sys.argv if argv is None else argv

    # Perintah sekali: `python run.py --release-kill-switch`. Jalur ini
    # menjalankan rekonsiliasi dan menulis audit log, tapi tidak pernah
    # mengirim order dan tidak menjalankan agen maupun dashboard.
    if "--release-kill-switch" in argv:
        return _operator_release()

    # Mode sekali: `python run.py --repair-candles` membersihkan tabel
    # candles lalu keluar, tanpa menjalankan agen atau dashboard. Tidak ada
    # menu mode di sini — perintah ini tidak pernah bertransaksi sama sekali.
    if "--repair-candles" in argv:
        async def _repair():
            setup_logger()
            app = TradingBotApp()
            await init_db()
            await app.repair_candles()

        asyncio.run(_repair())
        return 0

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        return 0
    except RuntimeError as exc:
        # Kegagalan konfigurasi live (kunci privat kosong, preflight
        # gagal, health check gagal saat start) dilempar sebagai
        # RuntimeError dengan pesan yang bisa dibaca operator.
        #
        # Tanpa blok ini, operator hanya melihat traceback Python --
        # yang tidak menjelaskan apakah ini masalah konfigurasi atau
        # bug, dan pesan aslinya hilang. Itu sebabnya blok ini wajib:
        # keamanan bukan soal exception, tapi soal pesan yang sampai
        # ke orang yang menjalankan.
        _print_safe("")
        _print_safe("  BOT TIDAK DIJALANKAN.")
        _print_safe("  Alasan: {}".format(exc))
        _print_safe("")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
