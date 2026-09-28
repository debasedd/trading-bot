"""
trading/live/console.py — Menu terminal untuk memilih mode dan mengatur aturan.

Prinsip yang dipegang di file ini:

1. **TIDAK ADA menu yang langsung menyalakan live.** Untuk mencapai live,
   operator harus mengetikkan dua hal: kalimat konfirmasi, DAN alamat
   wallet yang akan dipakai — string 42 karakter yang tidak mungkin tidak
   disengaja. Menu yang aktif dengan satu penekanan tombol adalah tombol
   yang akan ditekan saat salah.

2. **Alamat wallet ditampilkan SEBELUM apa pun.** Kalau layar tidak
   menunjukkan alamat 0x..., operator tidak tahu dia sedang memakai akun
   mana. Baginya, "akun yang salah" sama saja dengan "tidak tahu akun
   yang mana".

3. **Semua aturan ada di satu fungsi murni** (`decide_mode`) yang bisa
   diuji tanpa terminal. Logika menu yang hanya bisa diuji dengan menekan
   tombol adalah logika yang tidak pernah diuji.
"""
from __future__ import annotations

import getpass
import math
import os
import sys
from dataclasses import dataclass
from typing import List, Optional

from core.logger import get_logger

logger = get_logger("live_console")

Mode = str  # "paper" | "testnet" | "mainnet"

# Kalimat yang harus diketik untuk menyalakan mode apa pun yang menyentuh
# bursa. Dipilih supaya tidak mungkin "kebetulan" ketik benar.
CONFIRM_PHRASE = "SAYA MENGERTI"


@dataclass
class ModeDecision:
    """Keputusan mode beserta alasannya — selalu ada, tidak pernah `None`."""

    mode: Mode
    private_key: Optional[str] = None
    address: Optional[str] = None
    reason: str = ""
    refused: bool = False


def typed_addr_mismatch(shown: Optional[str], typed: Optional[str]) -> bool:
    """
    Alamat yang diketik harus sama dengan yang ditampilkan.

    Perbandingan case-insensitive: perbedaan huruf besar tidak mengubah
    akun yang dimaksud, sedangkan perbandingan yang ketat akan menolak
    operator yang benar.
    """
    if not shown or not typed:
        return True
    return shown.strip().lower() != typed.strip().lower()


def derive_address(private_key: str) -> Optional[str]:
    """Turunkan alamat publik dari private key. None bila key tidak valid."""
    try:
        from eth_account import Account
    except ImportError:
        return None
    try:
        return Account.from_key(private_key).address
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Private key tidak valid: {exc}")
        return None


def decide_mode(
    choice: str,
    typed_phrase: str,
    typed_address: str,
    shown_address: Optional[str],
    env: Optional[dict] = None,
) -> ModeDecision:
    """
    Keputusan mode, TANPA efek samping.

    Fungsi murni: input -> output. Tidak membaca terminal, tidak
    menyentuh environment sungguhan, tidak melakukan I/O. Semua aturan ada
    di sini supaya bisa diuji langsung.
    """
    env = env if env is not None else {}

    if choice == "1":
        return ModeDecision(mode="paper", reason="mode simulasi dipilih")

    if choice not in ("2", "3"):
        return ModeDecision(
            mode="paper", refused=True,
            reason="pilihan tidak dikenal; kembali ke mode simulasi",
        )

    mode: Mode = "testnet" if choice == "2" else "mainnet"

    # Aturan 1: private key harus ada di environment.
    key = env.get("HYPERLIQUID_PRIVATE_KEY")
    if not key:
        return ModeDecision(
            mode="paper", refused=True,
            reason=(
                "HYPERLIQUID_PRIVATE_KEY belum diisi di environment. "
                "Bot kembali ke mode simulasi."
            ),
        )

    # Aturan 2: kalimat konfirmasi harus persis, termasuk huruf besar.
    if typed_phrase.strip() != CONFIRM_PHRASE:
        return ModeDecision(
            mode="paper", refused=True,
            reason="kalimat konfirmasi tidak cocok; kembali ke mode simulasi",
        )

    # Aturan 3: alamat yang diketik harus sama dengan yang ditampilkan.
    if typed_addr_mismatch(shown_address, typed_address):
        return ModeDecision(
            mode="paper", refused=True,
            reason="alamat wallet yang diketik tidak cocok; kembali ke simulasi",
        )

    return ModeDecision(
        mode=mode, private_key=key, address=shown_address,
        reason="konfirmasi lengkap diterima",
    )


# ===========================================================================
# Tampilan terminal
# ===========================================================================

LINE = "=" * 68
THIN = "-" * 68

GREY = "\x1b[38;5;244m"
DIM = "\x1b[38;5;240m"
RESET = "\x1b[0m"
RED = "\x1b[1;31m"
GREEN = "\x1b[1;32m"


def _out(text: str = "") -> None:
    """
    Cetak dengan stdout dipaksa ke UTF-8.

    Console Windows default-nya cp1252 dan tidak punya glyph kotak. Tanpa
    ini, baris pertama saja sudah gagal dan tidak ada yang bisa terlihat —
    termasuk pesan penolakan yang justru paling penting untuk dilihat.
    """
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    print(text)


def _read(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


def interactive() -> bool:
    """
    Apakah TUI penuh bisa dipakai?

    Kalau stdout bukan terminal — output di-pipe, atau dijalankan di
    CI/service — panah tidak punya artinya dan menggambar bingkai cuma
    mengotori log. Di sana kita turun ke mode input biasa.
    """
    from trading.live import tui

    if not tui.is_tty():
        return False
    return tui.enable_vt_processing()


def _pause(enter_continues: bool = True) -> None:
    """
    Tunggu Enter sebelum menggambar ulang layar.

    Tanpa jeda, layar yang baru langsung menimpa yang lama dan operator
    tidak sempat membaca pesan kesalahannya. Pola ini penting: sebagian
    besar alur di sini berakhir dengan "ditolak, kembali ke menu".
    """
    if enter_continues:
        from trading.live import tui

        if tui.is_tty():
            tui._write(
                "\n  " + GREY + "Enter untuk kembali..." + RESET + "\n")
            try:
                input()
            except EOFError:
                pass



def _read_secret(prompt: str) -> str:
    """
    Baca tanpa menggema.

    `getpass` dipakai supaya private key tidak ikut tersimpan di scrollback
    terminal dan tidak masuk ke riwayat shell. Kalau tidak didukung, input
    biasa tetap dipakai, tapi operator diberi tahu risikonya.
    """
    try:
        return getpass.getpass(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        return ""
    except Exception:  # noqa: BLE001
        _out("  (peringatan: input akan terlihat di layar)")
        return _read(prompt)


def limits_summary(cfg: LiveConfig) -> List[str]:
    """Batas aktif, diformat untuk ditampilkan di dalam menu."""
    start, end = cfg.live_window_utc
    return [
        "order maks       ${:,.2f}".format(cfg.max_order_notional),
        "posisi maks      ${:,.2f}".format(cfg.max_position_notional),
        "total eksposur   ${:,.2f}".format(cfg.max_total_notional),
        "order per hari   {:,}".format(cfg.max_daily_orders),
        "stop rugi harian ${:,.2f}".format(cfg.max_daily_loss),
        "jendela UTC      {:02d}:00 - {:02d}:00".format(start, end),
    ]


def mode_options(cfg: LiveConfig, has_key: bool):
    """Opsi menu mode."""
    from trading.live.tui import Option

    return [
        Option(
            "SIMULASI (paper)", "paper",
            detail="tidak ada order ke bursa sama sekali",
        ),
        Option(
            "LIVE TESTNET", "testnet",
            detail="uang mock di bursa testnet",
        ),
        Option(
            "LIVE MAINNET", "mainnet",
            detail="UANG SUNGGAHAN - tidak bisa dibatalkan",
            danger=True,
        ),
        Option("Aturan / batas nominal", "rules",
                detail="ubah batas order, posisi, dan stop rugi"),
    ]


def show_banner() -> None:
    _out()
    _out(LINE)
    _out("  HYPERLIQUID LIVE SETUP")
    _out(LINE)
    _out("  Bot TIDAK dapat mengirim order apa pun tanpa langkah manual")
    _out("  di bawah ini. Tidak ada jalur otomatis ke mode live.")
    _out()
    _out("  Private key dibaca dari environment variable, TIDAK pernah dari")
    _out("  file config, karena file config ikut ter-commit ke git.")
    _out()
    _out("  Pilih 's' untuk menempelkan private key ke environment sesi")
    _out("  ini, atau 'x' untuk membatalkan dan kembali ke mode simulasi.")
    _out(LINE)
    choice = _read("  Pilih [s] sensitive / [x] cancel: ").lower()

    if choice == "s":
        key = _read_secret("  Tempel HYPERLIQUID_PRIVATE_KEY: ")
        if key:
            os.environ["HYPERLIQUID_PRIVATE_KEY"] = key
            _out("  Key disimpan di environment proses ini saja.")
            _out("  (tidak ditulis ke disk, hilang saat jendela ditutup)")
        else:
            _out("  Key kosong — tidak ada yang diubah.")


def _ask_mode_plain(cfg: LiveConfig) -> ModeDecision:
    """
    Jalur cadangan untuk stdout yang bukan terminal.

    Dipakai saat output di-pipe atau bot dijalankan sebagai service. Tidak
    ada panah, tidak ada bingkai — operator mengetik nomor seperti biasa.

    Sengaja BUKAN melempar error: `ask_mode` tidak boleh pernah gagal
    total, karena pemanggilnya adalah `run.py` dan kegagalan di sini berarti
    bot tidak jalan sama sekali.
    """
    show_banner()

    while True:
        _out()
        _out(LINE)
        _out("  MODE JALAN")
        _out(THIN)
        _out("  [1] SIMULASI (paper)")
        _out("  [2] LIVE TESTNET")
        _out("  [3] LIVE MAINNET  (UANG SUNGGAHAN)")
        _out("  [4] Aturan / batas nominal")
        _out(THIN)
        choice = _read("  Pilihan: ")

        if choice == "1":
            return ModeDecision(mode="paper", reason="mode simulasi dipilih")
        if choice == "4":
            cfg = edit_rules_plain(cfg)
            continue
        if choice not in ("2", "3"):
            return ModeDecision(mode="paper", refused=True,
                                reason="pilihan tidak dikenal")

        info = _show_wallet_plain(cfg, choice)
        if info is None:
            _out("  HYPERLIQUID_PRIVATE_KEY belum ada atau tidak valid.")
            continue

        phrase = _read("  Ketik '" + CONFIRM_PHRASE + "': ")
        if phrase.strip() != CONFIRM_PHRASE:
            _out("  Konfirmasi tidak cocok.")
            continue
        typed = _read("  Ketik ulang alamat wallet: ")

        decision = decide_mode(choice, phrase, typed, info["address"],
                               os.environ)
        if decision.refused:
            _out("  " + decision.reason)
            continue
        return decision


def _show_wallet_plain(cfg: LiveConfig, mode: str) -> Optional[dict]:
    """Tampilan wallet tanpa TUI."""
    key = os.environ.get("HYPERLIQUID_PRIVATE_KEY")
    if not key:
        return None
    address = derive_address(key)
    if not address:
        return None
    if mode == "mainnet":
        _out("  PERINGATAN: MAINNET memakai UANG SUNGGAHAN.")
    _out("  Wallet: " + address)
    _out("  Batas berlaku:")
    for line in limits_summary(cfg):
        _out("    " + line)
    return {"address": address, "key": key}


def _make_phrase_validator(field):
    def _validate(text: str) -> None:
        if text.strip() != CONFIRM_PHRASE:
            field.error = "harus persis: " + CONFIRM_PHRASE
    return _validate


def _make_address_validator(field, address: str):
    def _validate(text: str) -> None:
        if typed_addr_mismatch(address, text):
            field.error = "alamat tidak cocok dengan yang di atas"
    return _validate



def _show_wallet(cfg: LiveConfig, mode: str) -> Optional[dict]:
    """
    Tampilkan wallet + batas, lalu tunggu operator menekan Enter.

    Kembalikan `None` kalau tidak ada private key yang bisa dipakai — ini
    bukan error, cuma berarti operator belum menempelkan key.
    """
    key = os.environ.get("HYPERLIQUID_PRIVATE_KEY")
    if not key:
        return None

    address = derive_address(key)
    if not address:
        return None

    if mode == "mainnet":
        _out()
        _out("  " + "!" * 66)
        _out("  PERINGATAN: MODE MAINNET. Order yang dikirim TIDAK bisa")
        _out("  dibatalkan, dan menggunakan UANG SUNGGAHAN.")
        _out("  " + "!" * 66)

    _out()
    _out("  Wallet yang akan dipakai:")
    _out("    " + GREEN + address + RESET)
    _out()
    _out("  Batas yang berlaku:")
    for line in limits_summary(cfg):
        _out("    " + line)
    _pause()
    return {"address": address, "key": key}


def ask_mode(cfg: LiveConfig) -> ModeDecision:
    """
    Alur interaktif pemilihan mode.

    Menu penuh dipakai kalau TUI tersedia. Kalau stdout bukan terminal —
    output di-pipe, atau dijalankan sebagai service — turun ke input
    bernomor. Kedua jalur menghasilkan keputusan yang sama; yang menentukan
    safety ada di `decide_mode`, bukan di sini.
    """
    if not interactive():
        return _ask_mode_plain(cfg)

    from trading.live import tui

    show_banner()
    screen = tui.Screen()
    try:
        while True:
            has_key = bool(os.environ.get("HYPERLIQUID_PRIVATE_KEY"))
            result = tui.run_menu(
                "Pilih Mode", mode_options(cfg, has_key),
                footer=tui.render_keymap(), screen=screen,
            )
            if result.cancelled:
                return ModeDecision(mode="paper", refused=True,
                                    reason="dibatalkan operator")
            choice = result.selected.value
            if choice == "paper":
                screen.clear()
                return ModeDecision(mode="paper",
                                    reason="mode simulasi dipilih")
            if choice == "rules":
                cfg = edit_rules(cfg)
                continue

            info = _show_wallet(cfg, choice)
            if info is None:
                _out("  HYPERLIQUID_PRIVATE_KEY belum ada di environment.")
                _pause()
                continue

            decision = _do_confirmations(choice, info)
            screen.clear()
            if decision is None or decision.refused:
                if decision is not None and decision.reason:
                    _out("  " + decision.reason)
                _pause()
                continue

            _out("  " + GREEN + "Mode disetujui: "
                 + decision.mode.upper() + RESET + " untuk "
                 + decision.address)
            return decision
    finally:
        screen.stop()


def _do_confirmations(choice: str, info: dict) -> Optional[ModeDecision]:
    """Konfirmasi bertingkat. None berarti batal di tengah jalan."""
    from trading.live import tui

    address = info["address"]

    field = tui.TextField("  Ketik: ")
    field._validate = _make_phrase_validator(field)
    phrase = tui.run_text("  Ketik untuk konfirmasi: ", screen=tui.Screen(),
                          field=field)
    if tui.is_cancelled(phrase):
        return None

    addr_field = tui.TextField("  Alamat: ", max_length=64)
    addr_field._validate = _make_address_validator(addr_field, address)
    typed = tui.run_text("  Salin ulang alamat di atas: ", screen=tui.Screen(),
                        field=addr_field)
    if tui.is_cancelled(typed):
        return None

    return decide_mode(choice, phrase, typed, address, os.environ)



# ===========================================================================
# Editor aturan
# ===========================================================================

# Aturan yang boleh diubah: (nama field, label, jenis, batas bawah, batas atas)
EDITABLE = (
    ("max_order_notional", "Order maks (USDC)", "float", 10.0, 100_000.0),
    ("max_position_notional", "Posisi maks per simbol (USDC)", "float",
     10.0, 1_000_000.0),
    ("max_total_notional", "Total eksposur maks (USDC)", "float",
     10.0, 10_000_000.0),
    ("max_daily_orders", "Order per hari", "int", 1, 100_000),
    ("max_daily_loss", "Stop rugi harian (USDC)", "float", 1.0, 1_000_000.0),
    ("min_free_collateral", "Collateral minimum (USDC)", "float",
     0.0, 1_000_000.0),
    ("max_consecutive_errors", "Error beruntun sebelum stop", "int", 1, 100),
)


def validate_rule(field: str, value, kind: str, lo: float, hi: float):
    """
    Validasi satu aturan. Mengembalikan `(nilai_akhir, pesan_error)`.

    `None` pada posisi kedua berarti valid. Forma kembaliannya sengaja
    `None` (bukan string kosong) supaya pemanggil bisa membedakan "tidak ada
    masalah" dari "pesan yang kebetulan kosong".

    **`NaN` dan `inf` ditolak eksplisit.** Ini bukan detail remeh —
    `float("NaN")` valid di Python, tapi perbandingan dengan NaN selalu
    False. Akibatnya batas yang berisi NaN akan menyala TIDAK PERNAH:
    proteksi yang terlihat ada padahal tidak melindungi apa pun.
    """
    try:
        parsed = int(value) if kind == "int" else float(value)
    except (TypeError, ValueError):
        return None, "bukan angka"

    if kind == "float" and not math.isfinite(parsed):
        return None, "nilai tidak boleh NaN atau infinity"

    if parsed < lo or parsed > hi:
        return None, "harus antara {} dan {}".format(lo, hi)

    return parsed, None


def check_consistency(cfg: LiveConfig) -> List[str]:
    """
    Periksa hubungan antar batas. Mengembalikan daftar peringatan.

    Ini bukan error yang memblokir: angka yang "aneh" mungkin memang
    disengaja. Yang penting operator TAHU, dan tidak bisa menerima begitu
    saja bahwa ada yang tidak masuk akal.
    """
    warnings: List[str] = []
    if cfg.max_order_notional > cfg.max_position_notional:
        warnings.append(
            "order maks lebih besar dari posisi maks — order besar akan "
            "selalu ditolak oleh batas posisi"
        )
    if cfg.max_total_notional < cfg.max_position_notional:
        warnings.append(
            "total eksposur lebih kecil dari posisi per simbol — hanya satu "
            "posisi yang bisa terbuka pada satu waktu"
        )
    if cfg.max_daily_loss >= cfg.max_total_notional:
        warnings.append(
            "batas rugi harian lebih besar dari total eksposur — bot bisa "
            "kehilangan seluruh dana dalam satu hari"
        )
    return warnings


def _format_value(value) -> str:
    if isinstance(value, float):
        return "${:,.2f}".format(value)
    return "{:,}".format(value)


def rule_options(cfg: LiveConfig):
    """Opsi menu aturan, lengkap dengan nilai sekarang."""
    from trading.live.tui import Option

    options = []
    for field, label, _kind, lo, hi in EDITABLE:
        value = getattr(cfg, field)
        options.append(Option(
            "{} = {}".format(label, _format_value(value)),
            ("field", field),
            detail="rentang {} sampai {}".format(lo, hi),
        ))
    start, end = cfg.live_window_utc
    options.append(Option(
        "Jendela waktu UTC = {:02d}:00 - {:02d}:00".format(start, end),
        ("window", None),
        detail="bot hanya bertransaksi di dalam rentang ini",
    ))
    options.append(Option("Kembali ke menu mode", ("back", None)))
    return options


def edit_rules(cfg: LiveConfig) -> LiveConfig:
    """
    Menu ubah aturan memakai tombol panah.

    Nilai disimpan di objek config yang dikembalikan, TIDAK ditulis ke
    `config.yaml`. Alasannya: file itu ikut ter-commit, dan batas nominal
    yang ada di dalam git akan ikut berubah tanpa jejak siapa yang
    mengubahnya.
    """
    from trading.live import tui

    if not interactive():
        return edit_rules_plain(cfg)

    screen = tui.Screen()
    try:
        while True:
            result = tui.run_menu(
                "Aturan Live", rule_options(cfg),
                footer=tui.render_keymap(), screen=screen,
            )
            if result.cancelled:
                return cfg

            kind, field = result.selected.value
            if kind == "back":
                return cfg
            if kind == "window":
                _edit_window(cfg, screen)
                continue
            _edit_field_tui(cfg, field, screen)
    finally:
        screen.stop()


def _edit_field_tui(cfg: LiveConfig, field: str, screen) -> None:
    """Ubah satu aturan lewat input TUI, divalidasi tiap ketikan."""
    from trading.live import tui

    entry = next((e for e in EDITABLE if e[0] == field), None)
    if entry is None:
        return
    _name, label, kind, lo, hi = entry

    widget = tui.TextField("  Nilai baru [{} sampai {}]: ".format(lo, hi))

    def _validate(text: str) -> None:
        parsed, error = validate_rule(field, text, kind, lo, hi)
        if error:
            widget.error = error
        else:
            setattr(cfg, field, parsed)
            widget.message = "{} -> {}".format(label, _format_value(parsed))

    widget._validate = _validate
    result = tui.run_text(widget.prompt, screen=screen, field=widget)
    if tui.is_cancelled(result):
        return

    screen.clear()
    if widget.message:
        _out("  " + GREEN + widget.message + RESET)
    for warning in check_consistency(cfg):
        _out("  " + RED + "PERINGATAN: " + warning + RESET)
    _pause()


def edit_rules_plain(cfg: LiveConfig) -> LiveConfig:
    """
    Menu aturan untuk stdout non-terminal: ketik nomor.

    Penomoran dibangun dari `rule_options`, bukan dihitung ulang di sini.
    Versi sebelumnya menghitung sendiri dan membuat nomor bertabrakan:
    dengan 7 aturan, jendela dicetak sebagai `[7]` — sama dengan entri
    ketujuh — sehingga mengetik `7` mengubah aturan yang salah dan
    membuka jendela yang tidak pernah tercapai.
    """
    from trading.live.tui import Option

    options = rule_options(cfg)
    _out()
    _out(LINE)
    _out("  ATURAN LIVE")
    _out(THIN)
    for i, opt in enumerate(options, 1):
        _out("  [{}] {}".format(i, opt.label))
    _out(THIN)

    raw = _read("  Ubah nomor [0=batal]: ")
    if raw in ("", "0"):
        return cfg

    try:
        index = int(raw)
    except ValueError:
        _out("  Nomor tidak valid.")
        return cfg

    if not (1 <= index <= len(options)):
        _out("  Nomor di luar daftar.")
        return cfg

    kind, field = options[index - 1].value
    if kind == "back":
        return cfg
    if kind == "window":
        _edit_window(cfg)
        return cfg

    entry = next((e for e in EDITABLE if e[0] == field), None)
    if entry is None:
        return cfg
    _name, label, rule_kind, lo, hi = entry

    _out("  {}".format(label))
    _out("  Nilai sekarang: {}".format(getattr(cfg, field)))
    _out("  Batas yang boleh: {} sampai {}".format(lo, hi))
    raw_value = _read("  Nilai baru (kosong = batal): ")
    if not raw_value:
        _out("  Dibatalkan.")
        return cfg

    parsed, error = validate_rule(field, raw_value, rule_kind, lo, hi)
    if error:
        _out("  DITOLAK: {}. Nilai lama dipertahankan.".format(error))
        return cfg

    setattr(cfg, field, parsed)
    _out("  {} -> {}".format(label, _format_value(parsed)))

    for warning in check_consistency(cfg):
        _out("  PERINGATAN: " + warning)
    return cfg


def _edit_window(cfg: LiveConfig, screen=None) -> None:
    """
    Ubah jendela waktu trading, dalam jam UTC.

    Rentang dibatasi [start, end). Contoh 13,23 berarti 13:00 sampai
    22:59 UTC. Start == end sengaja ditolak: jendela kosong berarti bot
    tidak boleh bertransaksi sama sekali, dan itu lebih baik ditulis
    eksplisit daripada tersesat ke angka yang terlihat tidak valid.
    """
    _out("  Jendela waktu trading, dalam jam UTC. Bot hanya bertransaksi di")
    _out("  dalam rentang [start, end). Contoh 13,23 = 13:00-22:59 UTC.")
    raw = _read("  Format 'start,end' (kosong = batal): ")
    if not raw:
        _out("  Dibatalkan.")
        return
    parts = [p.strip() for p in raw.split(",")]
    if len(parts) != 2 or not all(p.lstrip("-").isdigit() for p in parts):
        _out("  Format harus 'start,end' dengan dua angka.")
        return
    start, end = int(parts[0]), int(parts[1])
    if not (0 <= start <= 23 and 0 <= end <= 23):
        _out("  Jam harus antara 0 dan 23.")
        return
    if start == end:
        _out("  start dan end sama berarti jendela kosong - ditolak.")
        return
    cfg.live_window_utc = (start, end)
    _out("  Jendela -> {:02d}:00 - {:02d}:00 UTC".format(start, end))


