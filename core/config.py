"""
core/config.py — Pemuat & validasi konfigurasi dari config.yaml
"""

import logging
import os
import sys
import yaml
from dataclasses import dataclass, field, fields
from typing import Any, List, Optional, Tuple
from pathlib import Path


@dataclass
class AccountConfig:
    initial_balance: float = 10000.0
    currency: str = "USDT"


@dataclass
class RiskConfig:
    max_risk_per_trade: float = 0.02
    max_leverage: int = 20
    max_daily_loss: float = 0.05
    max_drawdown: float = 0.15
    max_open_positions: int = 3
    default_leverage: int = 5


@dataclass
class FeeConfig:
    maker: float = 0.0002
    taker: float = 0.0005


@dataclass
class AgentIntervals:
    news_agent: int = 300
    news_agent_us_open: int = 120
    analysis_agent: int = 300
    analysis_agent_us_open: int = 180
    decision_agent: int = 60
    decision_agent_us_open: int = 30
    finbert_batch: int = 900
    macro_data: int = 21600
    funding_rate: int = 28800
    balance_snapshot: int = 300


@dataclass
class USMarketConfig:
    timezone: str = "US/Eastern"
    open_hour: int = 9
    open_minute: int = 30
    close_hour: int = 16


@dataclass
class IndicatorConfig:
    rsi_period: int = 14
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    bollinger_period: int = 20
    bollinger_std: int = 2
    ema_short: int = 9
    ema_long: int = 21


@dataclass
class DashboardConfig:
    host: str = "127.0.0.1"
    port: int = 8050
    debug: bool = False
    update_interval: int = 2000


@dataclass
class LoggingConfig:
    level: str = "INFO"
    file: str = "data_store/logs/trading_bot.log"
    max_bytes: int = 10485760
    backup_count: int = 5


@dataclass
class ExchangeConfig:
    name: str = "binance"
    type: str = "future"
    sandbox: bool = True


@dataclass
class ScanningConfig:
    dynamic_top_volume: bool = True
    top_n: int = 10
    refresh_interval: int = 3600  # Refresh daftar top volume tiap 1 jam


@dataclass
class ScalpingConfig:
    enabled: bool = True
    # Ambang TP yang benar-benar dipakai `_scalp_take_profit`.
    # Harus JAUH di atas ambang breakeven, kalau tidak TP menyala duluan
    # dan proteksi breakeven jadi kode mati.
    min_profit_pct: float = 0.0060       # 0.60% minimum profit untuk TP
    max_hold_seconds: int = 300          # Auto-close posisi > 5 menit
    min_hold_seconds: int = 15           # Hindari close instan
    batch_size: int = 2                  # Posisi baru per siklus decision

    fast_tp_pct: float = 0.0060          # 0.60% take profit (+0.50% net setelah fee)
    tight_sl_pct: float = 0.0025         # 0.25% stop loss (-0.35% net setelah fee)
    min_confidence: float = 0.40         # 40% confidence cukup untuk entry

    # ── Ambang reversal (close paksa) ──
    # Confidence minimum untuk MEMBALIKKAN posisi yang sedang berjalan, bukan
    # membuka posisi baru. Sengaja lebih tinggi dari `min_confidence`: membuka
    # pada sinyal lemah wajar, tapi membalik posisi aktif membutuhkan bukti
    # jauh lebih kuat karena membakar dua fee dan sering bertemu whipsaw.
    reversal_close_threshold: float = 0.70

    # ── Guard kualitas tick (B2) ──
    # Stop scalp hanya 0.25% dari harga. Kalau harga fill adalah spike lokal,
    # stop-nya sudah berada di dalam spread dan posisi tersapu di pemeriksaan
    # berikutnya. Guard di `PaperTradingEngine._tick_quality_guard` menolak
    # fill yang menyimpang lebih dari `tight_sl_pct` dari median tick terakhir.
    #
    # `max_tick_age_seconds` menolak tick yang sudah terlalu tua untuk
    # menggambarkan pasar sekarang.
    #
    # `stale_tick_min_samples` sengaja ada: tanpa ambang jumlah sampel, median
    # dari 2-3 tick itu tidak lebih informatif daripada tidak menghitung apa
    # pun, dan guard akan menebak. Bot lebih baik tidak intervene daripada
    # intervene dengan statistik yang tidak bermakna — ini fail-open yang
    # disengaja, dan akibatnya dicatat di log debug.
    max_tick_age_seconds: float = 1.5        # 1.5 detik
    stale_tick_window_seconds: float = 3.0   # jendela median, dalam detik
    stale_tick_min_samples: int = 5

    # ── Filter microstructure & spread ──
    orderbook_imbalance_threshold: float = 0.60   # 60% imbalance nyata
    momentum_threshold: float = 0.0008            # 0.08% momentum 30 dtk
    max_spread_pct: float = 0.0006                # Max 0.06% spread

    # ── Rekorder order book historis ──
    #
    # Interval pencatatan fitur mikrostruktur. 1 detik dipilih karena
    # order flow yang menarik biasanya selesai dalam hitungan detik, dan
    # OFI yang di-average per menit menghapus persis informasi yang dicari.
    #
    # 0 mematikan recorder. Ini aman: data mikrostruktur adalah bahan
    # riset, bukan syarat bertransaksi, dan kegagalan di sini tidak boleh
    # menghentikan bot.
    order_book_interval_s: float = 1.0

    # ── Proteksi breakeven ──
    # `breakeven_trigger_pct` harus DI BAWAH `min_profit_pct`; kalau tidak,
    # `_scalp_take_profit` menutup lebih dulu dan seluruh logika ini mati.
    # `breakeven_offset_pct` harus menutup fee roundtrip (0.10%); kalau tidak,
    # SL "breakeven" masih merugi.
    breakeven_trigger_pct: float = 0.0020   # 0.20% profit -> geser SL
    breakeven_offset_pct: float = 0.0015    # 0.15% di atas/bawah entry

    # ── Cooldown antar-entry per simbol ──
    # Setelah loss berulang di simbol yang sama, cooldown dikalikan sampai 4x.
    # Tanpa ini bot langsung masuk lagi ke arah yang baru saja terbukti salah.
    cooldown_after_close_seconds: float = 20.0   # Setelah profit
    cooldown_after_loss_seconds: float = 90.0     # Dasar setelah SL (× streak)


@dataclass
class DynamicTpSlConfig:
    """
    Target TP/SL dinamis berbasis volatilitas (ATR).

    Alasan keberadaan: SL statis 0.25% berarti sesuatu yang sangat berbeda
    tergantung kondisi pasar. Di pasar tenang itu ruang gerak yang wajar; di
    pasar bergejolak itu jarak yang lebih kecil daripada noise, dan posisinya
    akan tersapu di stop — bukan karena arah salah, tapi karena satu tick
    normal. Di situ SL harus melebar, dan TP harus ikut menyesuaikan supaya
    risk/reward tidak anjlok di bawah yang dijanjikan.

    Rumus (lihat `analysis/volatility.py`):
        SL_pct = clamp(atr_multiple × ATR_1m, min_sl_pct, max_sl_pct)
        TP_pct = max(SL_pct × min_risk_reward, scalping.min_profit_pct)
    """
    enabled: bool = True

    # Pengali ATR. 1.5 berarti "beri ruang 1.5× range rata-rata menit".
    # Di bawah ~1.0 stop akan terlalu sering tersapu; di atas ~2.5 risiko
    # per trade membengkak dan equity curve jadi tidak bisa dibaca.
    atr_multiple: float = 1.5
    atr_period: int = 14                    # Wilder smoothing, sama seperti ta.atr

    # Pagar bawah & atas. Bawah mencegah SL jadi sempit sekali saat pasar
    # sangat tenang (biaya fee jadi tidak proporsional); atas mencegah SL
    # melebar sampai melebihi yang bisa ditanggung equity per trade.
    min_sl_pct: float = 0.0025              # 0.25% — sama dengan SL statis
    max_sl_pct: float = 0.0150              # 1.50% — batas atas keras

    # Penguncian risk/reward. TP tidak boleh jatuh di bawah SL × ini, apa pun
    # yang terjadi pada volatilitas.
    min_risk_reward: float = 1.5

    # Jendela realized volatility (tick-based) untuk feature tambahan.
    realized_window_seconds: float = 30.0

    # Batas win rate impas yang masih diterima. Di atas ini, volatilitas
    # dianggap terlalu tinggi untuk diperdagangkan dan order dibatalkan —
    # lebih baik tidak bertransaksi daripada mengambil R:R yang mustahil
    # dicapai.
    max_breakeven_win_rate: float = 0.65
    fast_tp_pct: float = 0.0060          # 0.60% take profit scalping
    tight_sl_pct: float = 0.0025         # 0.25% stop loss scalping
    min_confidence: float = 0.40         # 40% confidence cukup untuk entry
    orderbook_imbalance_threshold: float = 0.60
    momentum_threshold: float = 0.0008   # 0.08% momentum 30 detik
    max_spread_pct: float = 0.0006        # Max 0.06% spread
    # Proteksi breakeven: geser SL ke vicinity breakeven begitu profit menyentuh
    # ambang ini. Harus DI BAWAH min_profit_pct supaya sempat aktif.
    breakeven_trigger_pct: float = 0.0020   # 0.20% — aktif geser SL
    breakeven_offset_pct: float = 0.0015     # 0.15% di atas entry (tutup fee roundtrip 0.10%)
    # Cooldown antar-entry per simbol. Setelah loss berulang di simbol yang
    # sama, cooldown dikalikan sampai 4x. Tanpa ini bot langsung masuk lagi
    # ke arah yang baru saja kena SL dan membayar dua kali fee untuk move
    # yang sama.
    cooldown_after_close_seconds: float = 20.0   # Setelah penutupan untung
    cooldown_after_loss_seconds: float = 90.0    # Dasar setelah SL/rugi


@dataclass
class EnsembleAgentConfig:
    """Bobot & status satu agen spesialis dalam ensemble arah."""

    enabled: bool = True
    weight: float = 0.25


@dataclass
class EnsembleConfig:
    """
    Konfigurasi ensemble arah LONG/SHORT.

    Ensemble menggabungkan verdikt beberapa agen spesialis menjadi satu
    probabilitas arah memakai log-odds pooling, bukan rata-rata probabilitas
    biasa. Rata-rata dari beberapa probabilitas yang sudah terkalibrasi
    justru menghasilkan hasil yang tidak terkalibrasi; log-odds pooling
    shrunk over-confidence tanpa perlu kalibrasi ulang.
    """

    enabled: bool = True
    interval_seconds: int = 5         # Seberapa sering ensemble menulis snapshot
    # Faktor penyusutan z-score sebelum sigmoid. < 1.0 menarik probabilitas
    # kembali ke 0.5 sehingga ensemble yang terlalu yakin tidak memancarkan
    # confidence palsu seperti 99%.
    shrinkage_delta: float = 0.85
    # Bonus bobot untuk agen yang searah dengan mayoritas agen lain.
    # 0.0 = bobot rata (uniform trust), 1.0 = diskriminasi kuat.
    agreement_bonus: float = 0.5
    # Clamp probabilitas akhir agar satu agen liar tidak menghasilkan 99.9%.
    min_prob: float = 0.02
    # Snapshot lebih tua dari ini TIDAK boleh dipakai DecisionAgent.
    # Data arah yang basi lebih berbahaya daripada tidak ada data.
    max_snapshot_age_seconds: int = 20
    # Horizon (menit) untuk kurva difusi arah.
    diffusion_horizon_minutes: int = 30
    diffusion_points: int = 60
    # Jendela momentum untuk MomentumAgent (detik).
    momentum_window_seconds: float = 30.0
    # Minimum jumlah trade di tape sebelum MicrostructureAgent berani bicara.
    min_trades_for_microstructure: int = 8
    # Minimum jumlah return 1m sebelum sigma dianggap layak dipakai.
    min_returns_for_vol: int = 20
    orderflow: EnsembleAgentConfig = field(
        default_factory=lambda: EnsembleAgentConfig(weight=0.30)
    )
    momentum: EnsembleAgentConfig = field(
        default_factory=lambda: EnsembleAgentConfig(weight=0.25)
    )
    technical: EnsembleAgentConfig = field(
        default_factory=lambda: EnsembleAgentConfig(weight=0.25)
    )
    microstructure: EnsembleAgentConfig = field(
        default_factory=lambda: EnsembleAgentConfig(weight=0.20)
    )

    def agent_configs(self) -> dict:
        """Kembalikan {nama_agen: EnsembleAgentConfig} untuk agen yang aktif."""
        out = {}
        for name in ("orderflow", "momentum", "technical", "microstructure"):
            cfg = getattr(self, name, None)
            if cfg and cfg.enabled:
                out[name] = cfg
        return out

    def base_weight(self, agent_name: str) -> float:
        """Bobot dasar satu agen; 0.0 bila tidak dikenal atau nonaktif."""
        cfg = getattr(self, agent_name, None)
        if cfg is None or not cfg.enabled:
            return 0.0
        return float(cfg.weight)


@dataclass
class LiveConfig:
    """
    Konfigurasi untuk eksekusi UANG SUNGGAHAN.

    Prinsip yang dipakai di sini: **semua default-nya menolak.** Mode live
    tidak mungkin tersalur hanya karena seseorang mengubah satu baris —
    mengaktifkannya butuh beberapa hal sekaligus, dan itu disengaja.

    Yang TIDAK ada di sini, dan memang tidak boleh ada: private key.
    Kredensial hanya dibaca dari environment saat runtime, tidak pernah
    dari file config, karena file config ikut ter-commit ke git.
    """

    # Yang benar-benar menembak ke bursa. Default False, dan tidak ada
    # kode yang bisa mengubahnya ke True selain variabel environment.
    enabled: bool = False

    # Testnet adalah mode yang DIHARAPKAN untuk setiap pengembangan dan dry
    # run. Mainnet harus diminta secara eksplisit lewat env var terpisah.
    testnet: bool = True

    # Nama environment yang memegang private key. Default-nya generik supaya
    # tidak menyiratkan key tertentu di repo.
    private_key_env: str = "HYPERLIQUID_PRIVATE_KEY"

    # Environment yang harus bernilai "1" sebelum order live boleh dikirim.
    # Ini lapis kedua: private key saja tidak cukup.
    live_confirm_env: str = "TRADEBOT_LIVE_CONFIRMED"

    # ── Jendela waktu ──────────────────────────────────────────────────
    # Bot hanya boleh bertransaksi di dalam jendela ini (UTC, 24 jam).
    # Di luar jendela, order DITOLAK di level gerbang — bukan sekadar
    # dijadwalkan ulang, supaya tidak ada race di batas menitnya.
    live_window_utc: Tuple[int, int] = (13, 23)   # [start, end) dalam jam

    # ── Batas keras ────────────────────────────────────────────────────
    # Semua ini adalah plafon ABSOLUT per hari/per order. Melewatinya
    # menghentikan trading, bukan hanya memperingatkan.
    # Leverage maksimum yang boleh diminta. Bursa menolak leverage di
    # luar rentangnya, tapi pesannya tidak selalu jelas - ditangkap di sini
    # menghasilkan log yang bisa dibaca.
    max_leverage: int = 10

    max_order_notional: float = 100.0      # USDC per order
    max_position_notional: float = 300.0   # USDC per posisi
    max_total_notional: float = 600.0      # USDC total eksposur
    max_daily_orders: int = 200            # order per hari
    max_daily_loss: float = 50.0           # USDC rugi sebelum dihentikan
    max_consecutive_errors: int = 3        # error beruntun sebelum stop
    min_free_collateral: float = 100.0     # USDC collateral yang dibiarkan

    # ── Proteksi di sisi bursa ─────────────────────────────────────────
    # WAJIB true. Stop loss yang hanya dipantau di Python berarti posisi
    # telanjang begitu proses mati atau jaringan putus — dan di live itu
    # berarti kehilangan uang sungguhan, bukan angka yang salah di layar.
    use_exchange_side_tpsl: bool = True

    # Bot boleh merekonsiliasi posisi sendiri kalau berbeda dengan bursa?
    # Tidak secara default: selisihnya dilaporkan, tapi tidak diubah diam-diam.
    # Perubahan otomatis tanpa persetujuan bisa menghapus posisi yang
    # sebenarnya milik Anda sendiri.
    auto_reconcile: bool = False

    # Jumlah hari tanpa kecocokan sebelum sistem menolak trading lagi.
    reconciliation_tolerance_days: int = 0


@dataclass
class AppConfig:
    account: AccountConfig = field(default_factory=AccountConfig)
    symbols: List[str] = field(default_factory=lambda: [
        "BTC/USDT:USDT",
        "ETH/USDT:USDT",
        "SOL/USDT:USDT",
        "XRP/USDT:USDT",
        "BNB/USDT:USDT",
        "DOGE/USDT:USDT",
        "ADA/USDT:USDT",
        "AVAX/USDT:USDT",
        "LINK/USDT:USDT",
        "NEAR/USDT:USDT",
    ])
    exchange: ExchangeConfig = field(default_factory=ExchangeConfig)
    scanning: ScanningConfig = field(default_factory=ScanningConfig)
    scalping: ScalpingConfig = field(default_factory=ScalpingConfig)
    ensemble: EnsembleConfig = field(default_factory=EnsembleConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    fees: FeeConfig = field(default_factory=FeeConfig)
    live: LiveConfig = field(default_factory=LiveConfig)
    agent_intervals: AgentIntervals = field(default_factory=AgentIntervals)
    us_market: USMarketConfig = field(default_factory=USMarketConfig)
    indicators: IndicatorConfig = field(default_factory=IndicatorConfig)
    dashboard: DashboardConfig = field(default_factory=DashboardConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    database_path: str = "data_store/trading_bot.db"
    dynamic_tp_sl: DynamicTpSlConfig = field(default_factory=DynamicTpSlConfig)

    # Pathway pemangkasan snapshot arah. Ditulis oleh
    # `run.py::_prune_direction_snapshots` yang dijadwalkan tiap jam, dan dibaca
    # test untuk memverifikasi prune benar-benar dijalankan.
    snapshot_prune_interval: int = 3600        # 1 jam
    snapshot_keep_per_symbol: int = 120        # ~10 menit pada interval 5 dtk

    # Pathway pemangkasan `agent_logs`. Tabel ini ditulis tiap siklus
    # decision (0,3 dtk) termasuk saat idle, jadi tanpa prune ~309.000
    # baris/hari. Ditulis oleh `run.py::_prune_agent_logs`.
    agent_log_prune_interval: int = 1800       # 30 menit
    agent_log_keep: int = 5000                 # ~20 menit jejak decision
    news_rss: List[str] = field(default_factory=lambda: [
        "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "https://cointelegraph.com/rss",
    ])
    cryptopanic_url: str = "https://cryptopanic.com/api/free/v1/posts/"
    cryptopanic_token: Optional[str] = None


def _config_logger():
    """
    Ambil logger untuk pesan diagnostik konfigurasi.

    Import-nya SENGAJA di dalam fungsi, bukan di atas modul. `core/logger.py`
    meng-import `get_config` dari file ini, jadi `from core.logger import
    get_logger` di level modul akan jadi lingkaran import: saat
    `core.logger` diimpor pertama kali, ia butuh `core.config`, dan
    `core.config` sedang belum selesai dievaluasi.

    Yang lebih berbahaya dari lingkaran import adalah RE-ENTRANSINYA.
    `get_logger()` memanggil `setup_logger()` kalau logger induk belum punya
    handler, dan `setup_logger()` memanggil `get_config().logging` — yang
    memanggil `load_config()` lagi. Karena `get_config()` baru menulis
    `_config`-global SESUDAH `load_config()` selesai, posisi di mana
    `load_config` sedang memproses file yang hilang akan melihat `_config`
    masih `None`, memanggil `load_config()` lagi, dan seterusnya sampai
    RecursionError. Jadi logger di sini hanya boleh diambil lewat
    `logging.getLogger` langsung — tanpa `setup_logger` — supaya tidak ada
    yang memanggil `get_config` dari dalam `load_config`.
    """
    return logging.getLogger("trading_bot.config")


# Nama field setiap dataclass config, dipakai untuk mendeteksi key yang diketik
# salah di config.yaml. Dibangun sekali per kelas dan di-cache, bukan dihitung
# per `_apply_dict` call, karena `load_config` memanggilnya ~20 kali.
_KNOWN_FIELDS: dict = {}


def _field_names(cls) -> frozenset:
    """Nama field dataclass `cls`, di-cache per kelas."""
    names = _KNOWN_FIELDS.get(cls)
    if names is None:
        names = frozenset(f.name for f in fields(cls))
        _KNOWN_FIELDS[cls] = names
    return names


def _type_matches(value: Any, expected) -> bool:
    """
    Apakah `value` masuk akal untuk field bertipe `expected`.

    Ini pemeriksaan LONGGAR dan sengaja hanya menyaring yang jelas-jelas
    salah, bukan memaksa konversi. Tujuannya bukan menjamin tipe, tapi
    mencegah satu kelas kegagalan yang sebelumnya tidak terlihat sama sekali:
    `max_open_positions: "tiga puluh"` di config.yaml akan lolos `hasattr`
    lalu di-set apa adanya, dan baru meledak jauh di dalam RiskManager saat
    order sedang berjalan — jauh dari file config yang salah menyebabkannya.

    `bool` ditangani terpisah karena di Python `bool` adalah subclass `int`,
    jadi tanpa pemeriksaan eksplisit `enabled: "true"` lolos sebagai int.
    """
    if expected is bool:
        return isinstance(value, bool)
    if expected is int:
        # `float` tidak diterima untuk field int. `max_open_positions: 2.5`
        # akan dipakai apa adanya sebagai batas jumlah posisi, dan
        # membiarkan 2.5 posisi terbuka bukan hal yang bisa dibatalkan.
        return isinstance(value, int) and not isinstance(value, bool)
    if expected is float:
        # `int` aman(float(x) valid), dan YAML sering menulis `2` untuk
        # field float, jadi menolak int di sini hanya menghasilkan
        # warning palsu tanpa mencegah satu pun bug.
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected is str:
        return isinstance(value, str)
    if expected is list:
        return isinstance(value, list)
    if expected is tuple:
        return isinstance(value, (list, tuple))
    # Tipe compound (dataclass, Optional, Union) tidak dicoba ditelusuri;
    # nilainya sudah divalidasi oleh validator khusus di bawah.
    return True


def _apply_dict(obj, data: dict, section: str = "") -> None:
    """
    Terapkan dict ke dataclass. Key yang tidak dikenal DIBUANG dengan warning.

    Kenapa tidak dibuang diam-diam seperti sebelumnya: `hasattr(obj, key)`
    yang tanpa `else` membuat typo di config.yaml (`max_risk_per_tradee`)
    atau key yang dihapus dari dataclass hilang begitu saja, dan bot berjalan
    dengan nilai default tanpa satu baris pun yang mengatakannya. Di file
    konfigurasi yang SEGURANYA menentukan profil risiko, konfigurasi yang
    gagal dimuat sebagian jauh lebih berbahaya daripada konfigurasi yang gagal
    dimuat sama sekali.

    Nilai yang tipenya salah juga diperingatkan, tapi TETAP di-set. Menolak
    seluruh config hanya karena satu salah ketik akan membuat bot tidak bisa
    start, dan operator lebih tepat memperbaikinya setelah melihat warning lalu
    restart daripada kehilangan seluruh konfigurasi. Yang dicegah di sini adalah
    kegagalan senyap, bukan ketidaksempurnaan.
    """
    if not isinstance(data, dict):
        _config_logger().warning(
            f"Bagian config '{section or '?'}' bukan dict (bertipe "
            f"{type(data).__name__}); seluruh isinya dilewati"
        )
        return

    known = _field_names(type(obj))
    for key, value in data.items():
        if not hasattr(obj, key):
            # Key typo, atau key milik versi lama file ini.
            _config_logger().warning(
                f"Key config '{section}.{key}' di config.yaml tidak dikenal "
                f"dan DIABAIKAN — nilai operator tidak pernah dipakai. "
                f"Key yang dikenal di bagian ini: {', '.join(sorted(known)) or '(kosong)'}"
            )
            continue
        if not _type_matches(value, type(getattr(obj, key, None))):
            _config_logger().warning(
                f"Nilai config '{section}.{key}' bertipe "
                f"{type(value).__name__}, bertentangan dengan field "
                f"bertipe {type(getattr(obj, key, None)).__name__}; nilai "
                f"dipakai apa adanya ({value!r}). Periksa config.yaml — "
                f"tipe yang salah di sini bisa gagal jauh dari sumbernya."
            )
        setattr(obj, key, value)


# ── Pemuatan konfigurasi gagal: apa akibatnya, bukan hanya apa yang terjadi ──
#
# Nilai di bawah adalah profil risiko yang BENAR-BENAR dipakai saat
# config.yaml gagal dimuat — bukan nilai generik. Tiga belas kunci di
# config.yaml berbeda dari default dataclass, dan enam di antaranya adalah
# seluruh `RiskConfig`. Kalau file tidak ada, bot tidak berjalan dengan
# "setting backup": ia berjalan dengan profil risiko lain yang tidak pernah
# diuji siapa pun, dan bedanya cukup besar untuk mengubah hasil.
#
# Arah bedanya penting dan harus jujur di pesan. Default dataclass lebih KETAT
# pada risiko (2%/trade vs 0.5%, 5% daily loss vs 10%, 3 posisi vs 30) tapi
# `max_leverage` 20 < 50 dan, lebih penting, loop decision 60 dtk vs 3 dtk —
# jadi bot yang "aman" ini justru 20x lebih lambat, dan keputusan yang
# tertunda adalah kesempatan yang sudah lewat. Tidak satu pun dari dua profil
# ini yang dimaksud; yang dimaksud hanya yang tertulis di config.yaml.
_MISSING_CONFIG_RISK_DEFAULTS = (
    "max_risk_per_trade=0.02 (config.yaml: 0.005) — risiko per trade 4x lebih besar",
    "max_leverage=20 (config.yaml: 50) — leverage maksimum lebih rendah",
    "max_daily_loss=0.05 (config.yaml: 0.10) — batas rugi harian lebih rendah",
    "max_drawdown=0.15 (config.yaml: 0.20) — batas drawdown lebih rendah",
    "max_open_positions=3 (config.yaml: 30) — 10x lebih sedikit posisi boleh terbuka",
    "default_leverage=5 (config.yaml: 10) — leverage default lebih rendah",
)


def _warn_missing_config_file(config: AppConfig, config_path: Path) -> None:
    """
    Laporkan dengan keras bahwa profil risiko aktif BUKAN profil yang ditulis.

    Bug sebelumnya: `load_config` mengembalikan `AppConfig()` telanjang saat
    file tidak ada, tanpa satu baris pun. Pemanggilan `load_config()` ini
    umumnya terjadi karena working directory salah (bot dijalankan dari folder
    lain), bukan karena file benar-benar dihapus, jadi gejalanya bukan crash —
    gejalanya bot yang "jalan normal" dengan setting berbeda total dan nol
    error di log. Bot yang mengambil risiko 4x lebih besar per trade, tapi
    mengambil keputusan 20x lebih lambat, tidak akan pernah mencurigai dirinya
    sendiri hanya dari price chart.

    Dua lapis output, karena "tidak terlihat di terminal" adalah setengah dari
    bug ini:
      1. `logger.warning` -> masuk file rotasi, dan ke console begitu
         `core.logger.setup_logger()` sudah terpasang.
      2. `print` langsung ke stderr. Alasannya spesifik: `load_config` bisa
         dipanggil SEBELUM `core.logger.setup_logger()` pernah berjalan, dan
         `get_logger()` tidak boleh dipanggil dari sini (re-entran
         `get_config` -> `load_config` -> `get_config`, lihat `_config_logger`).
         Tanpa handler, `logger.warning` jatuh ke `logging.lastResort` yang
         hanya menulis ke stderr di level WARNING tanpa format, jadi pesannya
         mudah tenggelam di antara output lain. `print` memastikan operator
         benar-benar melihat perubahan profil risiko ini.
    """
    risk_lines = "\n".join(
        f"      {i}. {line}" for i, line in enumerate(_MISSING_CONFIG_RISK_DEFAULTS, 1)
    )

    _config_logger().warning(
        f"config.yaml tidak ditemukan di {config_path.absolute()} — memakai "
        f"default AppConfig(): max_risk_per_trade=0.02, max_daily_loss=0.05, "
        f"max_drawdown=0.15, max_open_positions=3, max_leverage=20, "
        f"default_leverage=5. Profil risiko aktif BUKAN yang di config.yaml."
    )

    # `end_quiet_mode()` mengembalikan logger ke normal supaya
    # "peristiwa runtime (trade, warning, error) tetap terlihat." Cetakan
    # banner ini ke stderr supaya operator tidak perlu membuka file log untuk
    # tahu bahwa profil risiko yang aktif bukan yang ada di config.yaml.
    try:
        print(
            "\n"
            "  ============================================================\n"
            "   PERINGATAN RISIKO: config.yaml tidak ditemukan\n"
            "  ============================================================\n"
            f"  Dicoba: {config_path.absolute()}\n"
            "  Bot JALAN dengan default dataclass AppConfig().\n"
            "  Profil risiko aktif BUKAN yang ditulis di config.yaml:\n"
            f"{risk_lines}\n"
            "  Perilaku lain juga berubah:\n"
            "    - fees.taker 0.0005 vs 0.00045 (biaya turun, hasil PnL bergeser)\n"
            "    - decision_agent 60s vs 3s (keputusan 20x lebih lambat)\n"
            "  Penyebab paling umum: working directory salah.\n"
            "  Jalankan dari root repo agar config.yaml terbaca.\n"
            "  ============================================================\n",
            file=sys.stderr,
            flush=True,
        )
    except Exception:  # noqa: BLE001
        # Menampilkan peringatan tidak boleh menjatuhkan boot. Kalau misal
        # stderr ditutup, konfigurasi yang dimuat tetap harus dikembalikan.
        pass


def load_config(path: str = "config.yaml") -> AppConfig:
    """Muat konfigurasi dari file YAML. Pakai default jika file tidak ada."""
    config = AppConfig()

    config_path = Path(path)
    if not config_path.exists():
        _warn_missing_config_file(config, config_path)
        return config

    with open(config_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    # Account
    if "account" in raw:
        _apply_dict(config.account, raw["account"], "account")

    # Symbols
    if "symbols" in raw:
        config.symbols = raw["symbols"]

    # Scanning
    if "scanning" in raw:
        _apply_dict(config.scanning, raw["scanning"], "scanning")

    # Scalping
    if "scalping" in raw:
        _apply_dict(config.scalping, raw["scalping"], "scalping")

    # Target TP/SL dinamis berbasis volatilitas
    if "dynamic_tp_sl" in raw:
        _apply_dict(config.dynamic_tp_sl, raw["dynamic_tp_sl"], "dynamic_tp_sl")

    # Ensemble arah LONG/SHORT
    if "ensemble" in raw:
        ens_raw = dict(raw["ensemble"])
        # Blok `agents:` berisi subdik per agen. `_apply_dict` biasa akan
        # menimpa dataclass agen dengan dict mentah, jadi harus diterapkan
        # satu level lebih dalam secara eksplisit.
        agents_raw = ens_raw.pop("agents", None)
        _apply_dict(config.ensemble, ens_raw, "ensemble")
        if isinstance(agents_raw, dict):
            for name, sub in agents_raw.items():
                if hasattr(config.ensemble, name) and isinstance(sub, dict):
                    _apply_dict(
                        getattr(config.ensemble, name), sub, f"ensemble.agents.{name}"
                    )
                else:
                    _config_logger().warning(
                        f"Key config 'ensemble.agents.{name}' diabaikan: bukan "
                        f"agen ensemble yang dikenal atau bukan dict. "
                        f"Agen yang dikenal: orderflow, momentum, technical, "
                        f"microstructure."
                    )

    # Exchange
    if "exchange" in raw:
        _apply_dict(config.exchange, raw["exchange"], "exchange")

    # Risk
    if "risk" in raw:
        _apply_dict(config.risk, raw["risk"], "risk")

    # Fees
    if "fees" in raw:
        _apply_dict(config.fees, raw["fees"], "fees")

    # Live trading (UANG SUNGGAHAN).
    #
    # `enabled` SENGAJA dikunci ulang ke False di sini apa pun yang tertulis
    # di config.yaml. Alasannya: file config ikut ter-commit ke git, jadi ia
    # bukan tempat yang tepat untuk menyalakan apa pun yang desenvolvido uang
    # sungguhan. Satu-satunya cara menyalakan mode live adalah variabel
    # environment yang dibaca `trading/live/safety.py` — di luar file ini.
    # Kalau config.yaml suatu saat punya `live.enabled: true`, itu diabaikan
    # dengan sengaja, bukan karena bug.
    if "live" in raw:
        _apply_dict(config.live, raw["live"], "live")
    config.live.enabled = False

    # Agent intervals
    if "agent_intervals" in raw:
        _apply_dict(config.agent_intervals, raw["agent_intervals"],
                    "agent_intervals")

    # US Market
    if "us_market" in raw:
        _apply_dict(config.us_market, raw["us_market"], "us_market")

    # Indicators
    if "indicators" in raw:
        _apply_dict(config.indicators, raw["indicators"], "indicators")

    # Dashboard
    if "dashboard" in raw:
        _apply_dict(config.dashboard, raw["dashboard"], "dashboard")

    # Logging
    if "logging" in raw:
        _apply_dict(config.logging, raw["logging"], "logging")

    # Database
    if "database" in raw and "path" in raw["database"]:
        config.database_path = raw["database"]["path"]

    # Snapshot pruning — dibaca dari blok `database` agar zusammen dengan path.
    #
    # CATATAN: `agent_log_prune_interval` / `agent_log_keep` juga ditulis di
    # blok `database` config.yaml tapi TIDAK difilter di sini, jadi keduanya
    # tidak pernah terbaca dan nilai `run.py::_prune_agent_logs` yang dipakai
    # tetap default dataclass. Itu bug terpisah (perilaku, bukan visibilitas)
    # dan sengaja tidak ikut diubah di sini.
    if "database" in raw:
        _apply_dict(config, {k: v for k, v in raw["database"].items()
                             if k.startswith("snapshot_")}, "database")

    # News sources
    if "news_sources" in raw:
        ns = raw["news_sources"]
        if "rss" in ns:
            config.news_rss = ns["rss"]
        if "cryptopanic" in ns:
            cp = ns["cryptopanic"]
            if "base_url" in cp:
                config.cryptopanic_url = cp["base_url"]
            if "token" in cp and cp["token"]:
                config.cryptopanic_token = cp["token"]

    _validate_scalping_economics(config)
    _validate_ensemble_config(config)
    _validate_dynamic_tp_sl(config)
    return config


def _validate_dynamic_tp_sl(config: AppConfig) -> None:
    """
    Tolak konfigurasi TP/SL dinamis yang tidak mungkin bekerja.

    Daeun dinamis adalah perangkat pengaman, bukan strategi. Kalau pagar-pagar
    nya salah set, yang terjadi bukan "sedikit tidak optimal" — SL bisa
    melebar melebihi batas yang bisa ditanggung equity, atau TP bisa turun di
    bawah fee sehingga setiap trade pasti merugi tanpa bot menyadarinya.

    Syarat yang diperiksa:
      1. min_sl_pct < max_sl_pct, dan keduanya > 0.
         Pagar bawah >= pagar atas membuat clamp selalu mengembalikan nilai
         yang sama, jadi ATR tidak lagi berpengaruh sama sekali.
      2. atr_multiple > 0. Multiplier nol membuat SL selalu nol.
      3. min_risk_reward > 1. R:R = 1 persis berarti impas sebelum fee;
         setelah fee, impas mustahil dicapai. Penguncian R:R yang tidak
         tinggi hanya formalitas.
      4. max_breakeven_win_rate > 0.5 dan < 1.0. Di bawah 0.5 hampir semua
         kondisi ditolak (bot diam); 1.0 membuat gerbangnya tidak pernah
         menolak, jadi tidak berfungsi sebagai circuit breaker.
      5. TP minimum pada SL terkecil harus tetap impas setelah fee.
    """
    dyn = config.dynamic_tp_sl
    scalp = config.scalping
    if not getattr(dyn, "enabled", False):
        return

    problems: List[str] = []

    min_sl = float(dyn.min_sl_pct)
    max_sl = float(dyn.max_sl_pct)
    if min_sl <= 0 or max_sl <= 0:
        problems.append(
            f"min_sl_pct={min_sl:.4%} dan max_sl_pct={max_sl:.4%} harus > 0"
        )
    if min_sl >= max_sl:
        problems.append(
            f"min_sl_pct ({min_sl:.4%}) harus lebih kecil dari max_sl_pct "
            f"({max_sl:.4%}); kalau tidak, clamp tidak pernah membuka ruang "
            f"dan ATR tidak berpengaruh"
        )

    if float(dyn.atr_multiple) <= 0:
        problems.append(
            f"atr_multiple = {dyn.atr_multiple} harus > 0; multiplier nol "
            f"membuat SL selalu nol"
        )

    rr = float(dyn.min_risk_reward)
    if rr <= 1.0:
        problems.append(
            f"min_risk_reward = {rr} harus > 1.0; R:R = 1 persis impas "
            f"sebelum fee, dan setelah fee mustahil dicapai"
        )

    wb = float(dyn.max_breakeven_win_rate)
    if not (0.5 < wb < 1.0):
        problems.append(
            f"max_breakeven_win_rate = {wb:.2f} harus di rentang (0.5, 1.0); "
            f"di bawah 0.5 hampir semua kondisi ditolak, 1.0 membuat "
            f"circuit breaker tidak pernah menolak"
        )

    if int(dyn.atr_period) < 2:
        problems.append(
            f"atr_period = {dyn.atr_period} minimal 2; Wilder smoothing "
            f"dengan 1 periode hanya mengembalikan True Range terakhir"
        )

    # TP minimum pada SL terkecil harus tetap menutup setelah fee.
    if not problems and min_sl > 0:
        roundtrip = config.fees.taker * 2
        tp_at_min_sl = max(min_sl * rr, float(scalp.min_profit_pct))
        if tp_at_min_sl <= roundtrip:
            problems.append(
                f"TP minimum pada SL terkecil ({tp_at_min_sl:.4%}) tidak "
                f"melebihi fee roundtrip ({roundtrip:.4%}); setiap trade "
                f"pasti merugi apa pun arahnya"
            )
        else:
            net_tp = tp_at_min_sl - roundtrip
            net_sl = min_sl + roundtrip
            if net_tp < net_sl:
                breakeven = net_sl / (net_tp + net_sl)
                problems.append(
                    f"Pada SL terkecil ({min_sl:.4%}), TP minimum "
                    f"({tp_at_min_sl:.4%}) memberi R:R bersih hanya "
                    f"1:{net_tp / net_sl:.2f} sehingga butuh win rate "
                    f"{breakeven:.1%} untuk impas — lebih tinggi dari "
                    f"batas {wb:.0%}"
                )

    if problems:
        raise ValueError(
            "Konfigurasi dynamic_tp_sl tidak valid:\n  - " + "\n  - ".join(problems)
        )


# Single validator consensus, dipanggil dari load_config().
def _validate_ensemble_config(config: AppConfig) -> None:
    """
    Tolak konfigurasi ensemble yang tidak mungkin menghasilkan sinyal berguna.

    Polem yang dicek:

    1. Bobot agen aktif tidak boleh nol total — kalau iya, agregator tidak
       punya basis dan setiap simbol jadi NEUTRAL permanen.
    2. Bobot tidak boleh negatif; bobot negatif berarti agen bisa "memilih"
       arah yang berlawanan dengan verdictnya sendiri.
    3. `shrinkage_delta` harus di (0, 1]. 0 membuat ensemble selalu netral;
       > 1 justru menaikkan confidence, kebalikan dari tujuannya.
    4. `min_prob` harus di (0, 0.5) supaya LONG dan SHORT tidak pernah
      Meeting di 0 maupun 1.
    5. `max_snapshot_age_seconds` harus >= 2x `interval_seconds`. Kalau
       DecisionAgent bisa saja membaca snapshot yang lebih tua dari satu
       siklus ensemble, snapshot basi ikut terbaca sebagai sinyal — dan itu
       membatalkan alasan freshness check di DecisionAgent.
    """
    ens = config.ensemble
    if not ens.enabled:
        return

    problems = []

    active = ens.agent_configs()
    if not active:
        problems.append("tidak ada agen yang enabled; ensemble tidak punya sumber sinyal")

    total_weight = sum(float(c.weight) for c in active.values())
    if active and total_weight <= 0:
        problems.append(f"total bobot agen aktif = {total_weight:.4f}, harus > 0")

    for name, c in active.items():
        if float(c.weight) < 0:
            problems.append(f"bobot agen '{name}' = {c.weight} negatif")

    if not (0.0 < float(ens.shrinkage_delta) <= 1.0):
        problems.append(
            f"shrinkage_delta = {ens.shrinkage_delta}, harus di rentang (0, 1]"
        )

    if not (0.0 < float(ens.min_prob) < 0.5):
        problems.append(
            f"min_prob = {ens.min_prob}, harus di rentang (0, 0.5)"
        )

    if float(ens.agreement_bonus) < 0:
        problems.append(f"agreement_bonus = {ens.agreement_bonus} negatif")

    if int(ens.max_snapshot_age_seconds) <= 0:
        problems.append(
            f"max_snapshot_age_seconds = {ens.max_snapshot_age_seconds}, harus > 0"
        )

    # Freshness check di DecisionAgent hanya berguna kalau snapshot masih
    # dalam jangkauan. Kalau umur maksimum lebih pendek dari satu siklus
    # ensemble, ada celah di mana tidak ada snapshot yang cukup segar —
    # bot akan kehilangan entry tanpa alasan yang bisa dijelaskan.
    interval = int(ens.interval_seconds)
    max_age = int(ens.max_snapshot_age_seconds)
    if interval > 0 and max_age < interval * 2:
        problems.append(
            f"max_snapshot_age_seconds = {max_age} terlalu pendek untuk "
            f"interval_seconds = {interval}; harus >= {interval * 2} supaya "
            f"selalu ada snapshot yang cukup segar saat DecisionAgent membaca"
        )

    if int(ens.diffusion_points) < 2:
        problems.append(f"diffusion_points = {ens.diffusion_points}, minimal 2")

    if problems:
        raise ValueError(
            "Konfigurasi ensemble tidak valid:\n  - " + "\n  - ".join(problems)
        )


def _validate_scalping_economics(config: AppConfig) -> None:
    """
    Tolak konfigurasi scalping yang secara struktur tidak mungkin untung.

    Dua invarian yang sempat dilanggar dan membuat bot terus merugi:

    1. `breakeven_trigger_pct` harus DI BAWAH `min_profit_pct`. Kalau sama
       atau lebih tinggi, `_scalp_take_profit` menutup posisi lebih dulu di
       siklus yang sama dan proteksi breakeven tidak pernah sempat aktif.

    2. Risiko/reward bersih harus >= 1. Setelah fee roundtrip, TP harus
       menutup lebih dari SL. Kalau tidak, strategi butuh win rate lebih
       tinggi dari yang bisa dicapai sinyal teknis mana pun.
    """
    scalp = config.scalping
    if not scalp.enabled:
        return

    problems = []

    if scalp.breakeven_trigger_pct >= scalp.min_profit_pct:
        problems.append(
            f"breakeven_trigger_pct ({scalp.breakeven_trigger_pct:.4%}) harus lebih kecil "
            f"daripada min_profit_pct ({scalp.min_profit_pct:.4%}); kalau tidak, "
            f"proteksi breakeven tidak pernah aktif"
        )

    roundtrip = config.fees.taker * 2
    net_tp = scalp.min_profit_pct - roundtrip
    net_sl = scalp.tight_sl_pct + roundtrip
    if net_tp > 0 and net_tp < net_sl:
        breakeven_wr = net_sl / (net_tp + net_sl)
        problems.append(
            f"risk/reward bersih hanya 1:{net_tp / net_sl:.2f} "
            f"(TP {net_tp:+.2%} vs SL {-net_sl:+.2%} setelah fee {roundtrip:.2%}); "
            f"butuh win rate {breakeven_wr:.1%} untuk impas"
        )

    if scalp.breakeven_offset_pct < roundtrip:
        problems.append(
            f"breakeven_offset_pct ({scalp.breakeven_offset_pct:.4%}) lebih kecil dari "
            f"fee roundtrip ({roundtrip:.4%}); SL breakeven masih merugi"
        )

    # 3. Ambang reversal harus >= `min_confidence`, dan tetap di bawah 1.
    #
    # Kalau di bawah `min_confidence`, posisi bisa dibuka pada sinyal yang
    # terlalu lemah untuk membalikannya — entry dibuat lalu tidak pernah
    # ditutup oleh logika reversal, hanya oleh SL/TP. Kalau di atas 1,
    # gate-nya tidak pernah terpenuhi dan logika reversal jadi kode mati.
    if not (float(scalp.min_confidence) <= float(scalp.reversal_close_threshold) < 1.0):
        problems.append(
            f"reversal_close_threshold ({scalp.reversal_close_threshold:.4%}) harus "
            f"di rentang [min_confidence ({scalp.min_confidence:.4%}), 1.0); di bawah "
            f"min_confidence gate reversal tidak akan pernah menyala, di atas 1.0 "
            f"logikanya jadi kode mati"
        )

    # 4. Jendela guard tick harus lebih lebar daripada `max_tick_age_seconds`.
    #    Kalau jendela lebih pendek dari umur tick yang masih diterima, median
    #    dihitung dari sampel yang sudah dianggap basi oleh pemeriksaan pertama.
    window = float(getattr(scalp, "stale_tick_window_seconds", 0.0) or 0.0)
    max_age = float(getattr(scalp, "max_tick_age_seconds", 0.0) or 0.0)
    if window > 0 and max_age > 0 and window <= max_age:
        problems.append(
            f"stale_tick_window_seconds ({window:.4f}) harus lebih besar dari "
            f"max_tick_age_seconds ({max_age:.4f}); kalau tidak, median dihitung "
            f"dari sampel yang sudah ditolak karena basi"
        )

    if int(getattr(scalp, "stale_tick_min_samples", 0) or 0) < 2:
        problems.append(
            f"stale_tick_min_samples = {getattr(scalp, 'stale_tick_min_samples', 0)}, "
            f"minimal 2; median dari satu sampel bukan median"
        )

    if problems:
        raise ValueError(
            "Konfigurasi scalping tidak valid:\n  - " + "\n  - ".join(problems)
        )


# Singleton global config
_config: Optional[AppConfig] = None


def get_config() -> AppConfig:
    """Ambil konfigurasi global. Muat dari file jika belum dimuat."""
    global _config
    if _config is None:
        _config = load_config()
    return _config


def reload_config(path: str = "config.yaml") -> AppConfig:
    """Muat ulang konfigurasi dari file."""
    global _config
    _config = load_config(path)
    return _config
