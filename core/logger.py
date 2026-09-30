"""
core/logger.py — Logging terstruktur dengan warna ANSI dan format box untuk terminal + file rotasi bersih.
"""

import logging
import os
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from core.config import get_config

# Aktifkan dukungan virtual terminal ANSI di konsol Windows
if os.name == "nt":
    os.system("")

# Kode Warna ANSI & Gaya
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"

# Palet warna
CYAN = "\033[38;5;51m"
BRIGHT_BLUE = "\033[38;5;75m"
GREEN = "\033[38;5;48m"
AMBER = "\033[38;5;214m"
RED = "\033[38;5;196m"
PURPLE = "\033[38;5;141m"
GRAY = "\033[38;5;244m"
DARK_GRAY = "\033[38;5;238m"
WHITE = "\033[38;5;255m"

LEVEL_BADGES = {
    "DEBUG": f"{GRAY}[DEBUG]{RESET}",
    "INFO": f"{GREEN}[INFO ]{RESET}",
    "WARNING": f"{AMBER}[WARN ]{RESET}",
    "ERROR": f"{RED}{BOLD}[ERROR]{RESET}",
    "CRITICAL": f"{RED}{BOLD}[CRIT ]{RESET}",
}

MODULE_COLORS = {
    "main": CYAN,
    "decision_agent": PURPLE,
    "execution_agent": AMBER,
    "analysis_agent": BRIGHT_BLUE,
    "news_agent": "\033[38;5;220m",
    "paper_engine": GREEN,
    "position_manager": GREEN,
    "price_feed": CYAN,
    "hyperliquid_feed": CYAN,
    "risk_manager": RED,
    "scheduler": BRIGHT_BLUE,
}


class ColoredConsoleFormatter(logging.Formatter):
    """Formatter terminal berwarna dengan garis pemisah ASCII dan penyorotan kata kunci cerdas."""

    def format(self, record: logging.LogRecord) -> str:
        # Waktu (H:M:S) abu-abu redup
        time_str = f"{GRAY}{self.formatTime(record, '%H:%M:%S')}{RESET}"

        # Badge level
        level_badge = LEVEL_BADGES.get(record.levelname, f"[{record.levelname[:5]}]")

        # Nama modul
        mod = record.name.replace("trading_bot.", "")
        mod_color = MODULE_COLORS.get(mod, WHITE)
        mod_display = (mod[:14] + "…") if len(mod) > 15 else mod
        mod_str = f"{mod_color}{mod_display:<15}{RESET}"

        # Pesan dengan penyorotan kata kunci penting
        msg = record.getMessage()

        # Warna untuk aksi dan status scalping
        msg = re.sub(r"\b(LONG|BULLISH|BUY)\b", f"{GREEN}{BOLD}\\1{RESET}", msg)
        msg = re.sub(r"\b(SHORT|BEARISH|SELL)\b", f"{RED}{BOLD}\\1{RESET}", msg)
        msg = re.sub(r"\b(SCALP_TP|TP_HIT|PROFIT|OK)\b", f"{GREEN}{BOLD}\\1{RESET}", msg)
        msg = re.sub(r"\b(SL_HIT|LOSS|GAGAL|ERROR)\b", f"{RED}{BOLD}\\1{RESET}", msg)
        msg = re.sub(r"\b(OPEN|CLOSED)\b", f"{CYAN}{BOLD}\\1{RESET}", msg)
        msg = re.sub(r"(\+[0-9]+\.[0-9]+%?)", f"{GREEN}\\1{RESET}", msg)
        msg = re.sub(r"(-[0-9]+\.[0-9]+%?)", f"{RED}\\1{RESET}", msg)

        div = f"{DARK_GRAY}│{RESET}"
        line = f"{time_str} {div} {level_badge} {div} {mod_str} {div} {msg}"

        # Traceback HARUS ikut. Override `format()` ini tidak pernah
        # memanggil `formatException`, jadi `logger.exception()` dan
        # `exc_info=True` — yang keduanya mengirim `record.exc_info` —
        # dirender sebagai pesan satu baris tanpa jejak sama sekali.
        #
        # Untuk bot yang memakai loop 0.3 detik, itu bukan keterangan yang
        # hilang: tanpa traceback, satu error di iterasi ke-4.000 terlihat
        # identik dengan 4.000 error berturut, dan tidak ada yang bisa
        # ditelusuri dari mana asalnya. `run.py::_execution_loop` sempat
        # harus menyalin `traceback.format_exc()` ke teks pesan karena
        # formatter ini tidak menghasilkannya.
        #
        # Traceback sengaja TIDAK diberi warna: penyorotan di atas berlaku
        # untuk pesan, dan menerapkannya ke nama file dan nomor baris membuat
        # keduanya jadi tidak terbaca.
        if record.exc_info:
            exc_text = self.formatException(record.exc_info)
            if exc_text:
                line = line + "\n" + exc_text.rstrip()

        return line


# ─── Startup progress ────────────────────────────────────────────────
#
# Saat start, terminal bukan tempat log verbose. Yang tampil cukup satu
# baris per tahap: "Connecting to exchange…", "Loading market data…".
# Detail tetap masuk ke file rotasi, tidak ada yang hilang.
#
# Quiet mode hanya untuk tahap STARTUP, dan bisa dimatikan lewat env:
#   TRADEBOT_VERBOSE_STARTUP=1  -> tampilkan juga detail
#
# Setelah startup selesai, `end_quiet_mode()` mengembalikan logger ke
# normal supaya peristiwa runtime (trade, error) tetap terlihat.

# Default VERBOSE: log detail tetap tampil seperti sebelumnya. Progress bar
#_stage_ dipindah ke depan dan tidak menimpa log, jadi keduanya bisa hidup
# bersamaan. Set TRADEBOT_QUIET_STARTUP=1 untuk suppress kalau perlu.
# `\r` ditulis sebagai konstanta supaya tidak salah escape saat
# refactor; dipakai progress bar untuk menimpa baris sendiri.
NEWLINE = "\r"
_QUIET = os.environ.get("TRADEBOT_QUIET_STARTUP", "").strip() in (
    "1", "true", "True", "yes",
)
_QUIET_LOGGER: logging.Logger = None

if _QUIET:
    # Progress bar `transformers`/HF menulis ke stderr saat model dimuat
    # dan tidak bisa diredam dari sini. Matikan lewat env SEBELUM pustaka
    # diimpor — makanya penempatannya di level modul ini.
    # `HF_HUB_DISABLE_PROGRESS_BARS` menerima "1"/"true"; string "false"
    # diartikan sebagai "tidak dimatikan" oleh huggingface_hub, jadi nilainya
    # harus benar-benar truthy.
    os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
    # Tokenizeriebner threadingwarning adalah noise di mode ini.
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")


def _make_progress_aware_emit(original_emit):
    """
    Bungkus `StreamHandler.emit` supaya baris progress dibersihkan dulu.

    Saat tahap berjalan, baris progress ditulis dengan carriage-return
    tanpa newline, jadi posisinya nempel di baris terakhir. Begitu
    sebuah record log keluar, terminal menambah baris baru di bawahnya,
    dan karakter CR dari update progress berikutnya menimpa baris yang
    baru saja ditulis.

    Hasilnya progress bar dan log detail saling tumpang tindih sampai
    tidak terbaca — persis yang ada di screenshot.

    Fungsi ini menutup baris progress dengan newline SEBELUM
    meneruskan emit, supaya tiap record log dapat baris utuh. Baris
    progress berikutnya menggambar ulang dirinya sendiri di baris baru.
    """
    def emit(record):
        st = _stage_active
        active = bool(st.get("label")) and sys.stdout.isatty()
        if active:
            sys.stdout.write(NEWLINE)
            sys.stdout.flush()
            st["_paused"] = True
        try:
            original_emit(record)
        finally:
            if active:
                st["_paused"] = False
    return emit


def setup_logger(name: str = "trading_bot") -> logging.Logger:
    """Buat logger dengan output terminal berwarna dan file rotasi teks bersih."""
    cfg = get_config().logging

    logger = logging.getLogger(name)

    # Jangan tambah handler jika sudah ada
    if logger.handlers:
        return logger

    logger.setLevel(getattr(logging, cfg.level.upper(), logging.INFO))

    # Formatter teks biasa untuk file log (tanpa kode ANSI)
    plain_formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler dengan warna & ASCII
    console = logging.StreamHandler()
    console.setFormatter(ColoredConsoleFormatter())
    if _QUIET:
        # Selama startup, terminal hanya menampilkan baris tahap.
        # Detail tetap masuk ke file rotasi di bawah.
        console.setLevel(logging.WARNING)
    else:
        # Tutup baris progress yang sedang berjalan sebelum log detail
        # menulis. Tanpa ini tiap baris log memecah baris progress, dan
        # `\r` barunya menimpa potongan baris sebelumnya — hasilnya
        # progress bar bertumpuk jadi Texture confuse.
        console.emit = _make_progress_aware_emit(console.emit)
    logger.addHandler(console)

    # File handler (rotasi teks bersih)
    log_path = Path(cfg.file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    file_handler = RotatingFileHandler(
        filename=str(log_path),
        maxBytes=cfg.max_bytes,
        backupCount=cfg.backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(plain_formatter)
    logger.addHandler(file_handler)

    return logger


# ─── Startup progress ────────────────────────────────────────────────
#
# Saat start, terminal bukan tempat log verbose. Yang tampil cukup satu baris
# per tahap: "Connecting to exchange…", "Loading market data…". Detail
# tetap masuk ke file rotasi, tidak ada yang hilang.
#
# Quiet mode hanya untuk tahap STARTUP, dan bisa dimatikan lewat env:
#   TRADEBOT_VERBOSE_STARTUP=1  -> tampilkan juga satu baris detail
#
# Setelah startup selesai, `end_quiet_mode()` mengembalikan logger ke
# normal supaya evento runtime (trade, error) tetap terlihat.



def begin_quiet_mode(name: str = "trading_bot"):
    """Tahan detail log di terminal; file tetap lengkap."""
    global _QUIET_LOGGER
    _QUIET_LOGGER = logging.getLogger(name)
    return _QUIET_LOGGER


def end_quiet_mode():
    """Kembalikan terminal ke mode normal setelah startup selesai."""
    global _QUIET, _QUIET_LOGGER
    _QUIET = False
    _QUIET_LOGGER = None


# -- Progress bar ------------------------------------------------------
#
# Lebar bar dalam karakter. Dipakai sebagai default-arg `width`, jadi
# harus didefinisikan SEBELUM fungsi yang memakainya.
_BAR_WIDTH = 28


# Warna per tahap: Connecting = cyan, Loading = amber, Starting = magenta.
_STAGE_COLORS = {
    "Preparing": "\033[38;5;244m",
    "Connecting": "\033[38;5;51m",
    "Scanning": "\033[38;5;51m",
    "Opening": "\033[38;5;51m",
    "Loading": "\033[38;5;220m",
    "Fetching": "\033[38;5;220m",
    "Reading": "\033[38;5;220m",
    "Starting": "\033[38;5;141m",
}

# Baris tahap yang sedang berjalan, dipakai `step()` untuk memperbaruinya
# di tempat yang sama.
# State progress bar global (satu bar untuk seluruh startup).
_stage_active = {"label": None, "done": 0, "total": 0,
                 "color": "[0m", "_paused": False, "_started": False}


def _stage_color(label: str) -> str:
    for key, code in _STAGE_COLORS.items():
        if label.startswith(key):
            return code
    return "\033[0m"


def _render_bar(label, done, total, color, width=_BAR_WIDTH):
    """Baris progress satu tahap, dengan label di kiri dan bar di kanan."""
    total = max(int(total), 0)
    done = max(0, min(int(done), total))
    ratio = (done / total) if total else 0.0
    filled = int(ratio * width)
    bar = (
        f"\033[38;5;48m{'█' * filled}"
        f"\033[38;5;239m{'░' * (width - filled)}\033[0m"
    )
    pct = f"\033[1;37m{ratio:>4.0%}\033[0m" if total else ""
    return f"  {color}{label}\033[0m  {bar}  {pct}"


def stage(label: str, total: int = 0):
    """
    Mulai atau ganti tahap startup, tanpa mereset progress global.

   Seluruh startup memakai SATU bar. `stage()` hanya menukar label dan,
    kalau `total` diberikan, menambahkannya ke total global — bukan
    memulai bar dari nol. Jadi yang terlihat di terminal hanya satu bar
    yang terus maju, bukan satu bar per tahap.

        stage("Scanning top 10 volume futures")
        step()      # 3/47
        stage_done("Top 10 symbols")
        stage("Fetching market data")
        step()      # 4/47   <- lanjut, tidak reset

    `stage_done()` menutup baris dengan hasil, dan bar menghilang
    sampai tahap berikutnya dimulai.
    """
    st = _stage_active
    color = _stage_color(label)

    if not st["_started"]:
        # Bar baru: belum ada unit kerja yang dihitung.
        st["_started"] = True
        st["done"] = 0
        st["total"] = max(int(total), 0)
    else:
        # Bar sudah berjalan. Total opsional menambah work unit baru;
        # counter `done` TIDAK direset supaya progress terlihat naik terus.
        st["total"] += max(int(total), 0)

    st["label"] = label
    st["color"] = color
    st["_paused"] = False
    _draw_stage()


def step(done: int = None, label: str = None):
    """
    Majukan progress bar global satu langkah.

    Tanpa argumen menambah satu; dengan angka, diset absolut relatif
    terhadap totalglobal. Label opsional menimpa label tahap.
    """
    st = _stage_active
    if not st["label"]:
        return
    if done is None:
        st["done"] += 1
    else:
        st["done"] = int(done)
    if label:
        st["label"] = label
        st["color"] = _stage_color(label)
    _draw_stage()


def _draw_stage():
    st = _stage_active
    if not st["label"]:
        return
    # Saat emit() sedang menulis record log, jangan gambar baris progress —
    # dia akan menimpa baris yang baru saja ditulis.
    if st.get("_paused"):
        return
    line = _render_bar(
        st["label"], st["done"], st["total"], st["color"]
    )
    if sys.stdout.isatty():
        sys.stdout.write(NEWLINE + "\033[K" + line)
        sys.stdout.flush()
    else:
        # Bukan TTY: print hanya saat state berubah supaya log file tetap
        # terbaca dan tidak dijejak baris identik berulang.
        key = (st["label"], st["done"], st["total"])
        if getattr(_draw_stage, "_last", None) != key:
            _draw_stage._last = key
            total = max(st["total"], 1)
            pct = int(st["done"] / total * 100)
            print(f"  {st['label']}  {pct}%")


def stage_done(message: str = None, label: str = None):
    """
    Tutup tahap yang sedang berjalan.

    Baris progress diganti baris final bertanda centang — progres bar
    yang animate hilang, dan yang tersisa hanya hasilnya.
    """
    st = _stage_active
    if not st["label"]:
        return
    text = message if message is not None else st["label"]
    if sys.stdout.isatty():
        # ASCII "[OK]", bukan "✓": console Windows default-nya cp1252
        # tidak punya glyph itu dan print() akan melempar UnicodeEncodeError.
        sys.stdout.write(
            "\r\033[K"
            f"  \033[38;5;48m[OK]\033[0m {text}"
            + "\n"
        )
        sys.stdout.flush()
    else:
        print(f"  [OK] {text}")

    # Tahap selesai, tapi counter global TIDAK direset: tahap berikutnya
    # melanjutkan dari angka yang sama, jadi bar terlihat terus naik
    # tanpa pernah lompat balik ke nol.
    _stage_active.update({"label": None, "color": "[0m", "_paused": False})



def is_quiet() -> bool:
    """True kalau detail log sedang ditahan (mode quiet startup)."""
    return _QUIET


def get_logger(module_name: str) -> logging.Logger:
    """Ambil child logger untuk modul tertentu."""
    parent = logging.getLogger("trading_bot")
    if not parent.handlers:
        setup_logger()
    return parent.getChild(module_name)


