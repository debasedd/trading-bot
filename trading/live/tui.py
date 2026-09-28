"""
trading/live/tui.py — Primitif TUI terminal: baca tombol panah, gambar layar
penuh, dan editor teks satu baris.

Dipisah dari `console.py` karena alasan yang sama seperti `decide_mode`
dipisah dari `ask_mode`: yang di sini bisa diuji tanpa manusia, sementara
`console.py` yang memanggilnya.

Tiga hal yang membuat TUI terminal mudah rusak, dan cara mengatasinya:

1. **Konsol Windows tidak punya ANSI.** Perintah erase/rumah tidak ada
   sebelum VT diaktifkan lewat ctypes. Tanpa itu, `cls` berulang tidak
   menghasilkan apa-apa dan layar jadi bertumpuk.

2. **Windows membaca tombol berbeda.** `msvcrt.getwch()` mengembalikan
   dua karakter untuk panah (prefix lalu kode), sedangkan Unix
   mengembalikan satu escape sequence. Dua jalur harus ditangani
   terpisah, bukan diasumsikan sama.

3. **Kursor nyasar kalau stdout bukan terminal** — misal saat output
   di-pipe ke file atau dijalankan di CI. Di sana mode panah tidak bisa
   dipakai sama sekali; semua fungsi otomatis turun ke mode input
   bernomor. Menolak jalan lebih baik daripada menggambar halfway.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Sequence

# ---------------------------------------------------------------- key codes
UP = "up"
DOWN = "down"
LEFT = "left"
RIGHT = "right"
ENTER = "enter"
ESC = "esc"
BACKSPACE = "backspace"
CTRL_C = "ctrl_c"

WIDTH = 68

# Kode yang folgt prefix byte di Windows.
_WINDOWS_KEYS = {
    "H": UP, "P": DOWN, "M": RIGHT, "K": LEFT,
    "G": "home", "O": "end", "I": "pageup", "Q": "pagedown",
    "S": "delete", "R": "insert",
}


class NotATerminal(RuntimeError):
    """DimLempar saat TUI diminta di stdout yang bukan terminal sungguhan."""


# ------------------------------------------------------------------- colour
def _vt_enabled() -> bool:
    return hasattr(sys.stdout, "reconfigure")


def enable_vt_processing() -> bool:
    """
    Aktifkan pemrosesan virtual terminal di Windows.

    Tanpa ini semua escape sequence dicetak apa adanya sebagai teks, bukan
    dieksekusi — jadi `cls`, gerakan kursor, dan warna semuanya tidak
    terjadi dan layar akan menumpuk. Mengembalikan True kalau ANSI bisa
    dipakai di terminal ini.
    """
    if os.name != "nt":
        return _vt_enabled()
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        ENABLE_VT = 0x0004
        if not (mode.value & ENABLE_VT):
            if not kernel32.SetConsoleMode(handle, mode.value | ENABLE_VT):
                return False
        return True
    except Exception:  # noqa: BLE001
        return False


ANSI_CLEAR = "\x1b[2J\x1b[H"
ANSI_HOME = "\x1b[H"
ANSI_ERASE_DOWN = "\x1b[J"
ANSI_ERASE_LINE = "\x1b[K"
ANSI_HIDE_CURSOR = "\x1b[?25l"
ANSI_SHOW_CURSOR = "\x1b[?25h"


class Screen:
    """
    Layar penuh yang digambar ulang di tempat, bukan ditumpuk.

    Menggambar ulang memakai "ke atas, tulis, hapus sisa baris" alih-alih
    `2J` penuh di setiap frame. Kebersihan total memunculkan kedipan di
    setiap penekanan tombol, dan pada menu dengan ~
    """
    def __init__(self, width: int = WIDTH):
        self.width = width
        self.active = False

    def __enter__(self) -> "Screen":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    def start(self) -> None:
        if not self.active:
            _write(ANSI_HIDE_CURSOR + ANSI_CLEAR)
            self.active = True

    def stop(self) -> None:
        """Kembalikan terminal ke keadaan semula.

        Cursor wajib dikembalikan. Kalau tidak, prompt shell berikutnya
        tidak terlihat dan operator akan mengira botnya hang — saat itu
        dia mungkin menekan Ctrl+C.
        """
        if self.active:
            _write(ANSI_SHOW_CURSOR)
            self.active = False

    def draw(self, lines: Sequence[str]) -> None:
        """
        Gambar ulang di tempat: ke atas, tulis tiap baris, hapus sisanya.

        Sengaja TIDAK memakai `2J` penuh di setiap frame. Kebersihan total
        memunculkan kedipan di setiap penekanan tombol, dan pada menu yang
        sering digambar ulang kelipannya lebih lambat daripada kursor.
        """
        self.start()
        out = [ANSI_HOME]
        for line in lines:
            out.append(_pad(line, self.width) + ANSI_ERASE_LINE + "\n")
        out.append(ANSI_ERASE_DOWN)
        _write("".join(out))

    def clear(self) -> None:
        _write(ANSI_CLEAR)


def _pad(text: str, width: int) -> str:
    """Pad ke lebar tetap, dengan memperhitungkan lebar tampilan."""
    return text + " " * max(0, width - display_width(text))


def display_width(text: str) -> int:
    """
    Lebar tampilan yang benar untuk teks ber-ANSI dan karakter lebar.

    Menu memakai warna, jadi `len()` menghitung kode warna sebagai karakter
    dan membuat bingkai jadi salah. Karakter CJK dihitung dua petak.
    """
    import re
    import unicodedata

    plain = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", text)
    total = 0
    for ch in plain:
        if unicodedata.combining(ch):
            continue
        total += 2 if unicodedata.east_asian_width(ch) in "WF" else 1
    return total


def _write(text: str) -> None:
    try:
        sys.stdout.write(text)
        sys.stdout.flush()
    except (BrokenPipeError, ValueError):
        pass


# --------------------------------------------------------------- key input
def is_tty() -> bool:
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


def _read_windows() -> Optional[str]:
    import msvcrt

    if not msvcrt.kbhit():
        return None
    ch = msvcrt.getwch()
    if ch in ("\x00", "\xe0"):
        # Prefix byte: byte kedua adalah kode tombol.
        return _WINDOWS_KEYS.get(msvcrt.getwch(), None)
    if ch == "\x03":
        return CTRL_C
    if ch in ("\r", "\n"):
        return ENTER
    if ch == "\x1b":
        return ESC
    if ch in ("\x08", "\x7f"):
        return BACKSPACE
    if ch == "\t":
        return "tab"
    return ch


def _read_posix() -> Optional[str]:
    import select

    if not select.select([sys.stdin], [], [], 0)[0]:
        return None
    seq = os.read(sys.stdin.fileno(), 8)
    if not seq:
        return ENTER  # EOF diperlakukan sebagai Enter agar tidak menggantung
    text = seq.decode("utf-8", "replace")
    if text.startswith("\x1b["):
        return {"A": UP, "B": DOWN, "C": RIGHT, "D": LEFT}.get(
            text[2:3], None)
    if text.startswith("\x1bO"):
        return _WINDOWS_KEYS.get(text[2:3], None)
    first = text[0]
    if first == "\x03":
        return CTRL_C
    if first in ("\r", "\n"):
        return ENTER
    if first == "\x1b":
        return ESC
    if first in ("\x08", "\x7f"):
        return BACKSPACE
    return first


class KeyReader:
    """
    Pembaca tombol yang aman dipakai sebagai context manager.

    `raw_mode` hanya diaktifkan di POSIX. Di Windows tidak diperlukan dan
    kalau tetap dipaksa akan merusak konsol.
    """

    def __init__(self):
        self._raw = None
        self._posix = os.name != "nt"

    def __enter__(self) -> "KeyReader":
        if self._posix:
            self._enter_raw()
        return self

    def __exit__(self, *exc) -> None:
        if self._raw is not None:
            try:
                import termios

                termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN,
                                  self._raw)
            except Exception:  # noqa: BLE001
                pass
            self._raw = None

    def _enter_raw(self) -> None:
        try:
            import termios
            import tty

            fd = sys.stdin.fileno()
            self._raw = termios.tcgetattr(fd)
            tty.setcbreak(fd)
        except Exception:  # noqa: BLE001
            self._raw = None

    def get(self) -> Optional[str]:
        """Kunci berikutnya, atau None kalau tidak ada input yang menunggu."""
        if self._posix:
            return _read_posix()
        return _read_windows()

    def wait(self) -> str:
        """Blokir sampai ada kunci. Dijamin mengembalikan kunci valid."""
        while True:
            key = self.get()
            if key is not None:
                return key
            _sleep()


def _sleep() -> None:
    import time

    time.sleep(0.01)


# -------------------------------------------------------------------- menu
@dataclass
class Option:
    """Satu baris menu."""

    label: str
    value: object = None
    detail: str = ""
    enabled: bool = True
    danger: bool = False


@dataclass
class MenuResult:
    selected: Optional[Option] = None
    cancelled: bool = False


def _box_top(title: str, width: int = WIDTH) -> str:
    inner = width - 2
    label = " {} ".format(title.upper())
    pad = max(0, inner - display_width(label) - 2)
    return "\u250c" + "\u2500" * pad + label + "\u2500" * (inner - pad - display_width(label)) + "\u2510"


def _box_bottom(width: int = WIDTH) -> str:
    return "\u2514" + "\u2500" * (width - 2) + "\u2518"


class Menu:
    """
    Menu daftar yang dinavigasi panah.

    Putarannya dipisah dari gambarannya lewat `step()`, supaya logika
    navigasi bisa diuji dengan.Does tidak ada tombol yang perlu ditekan.
    """

    def __init__(self, options: Sequence[Option]):
        self.options = list(options)
        self.index = self._first_enabled()
        self._last_drawn = ""

    def _first_enabled(self) -> int:
        for i, opt in enumerate(self.options):
            if opt.enabled:
                return i
        return 0

    def _move(self, delta: int) -> None:
        """Geser kursor, melewati entri yang mati.

        Melewati entri mati itu penting: entri mati adalah opsi yang
        sengaja dikunci. Kursor yang mendarat di situ membuat operator
        menekan Enter tanpa terjadi apa-apa, dan itu terbaca sebagai
        botnya rusak.
        """
        count = len(self.options)
        if count == 0:
            return
        for _ in range(count):
            self.index = (self.index + delta) % count
            if self.options[self.index].enabled:
                return

    def step(self, key: str) -> Optional[MenuResult]:
        """Majukan menu satu langkah. Kembalikan hasil kalau selesai."""
        if key in ("q", "Q", ESC, CTRL_C):
            return MenuResult(cancelled=True)
        if not self.options:
            # Menu kosong: tidak ada yang bisa dipilih, tidak ada yang
            # bisa diklik. Mengakses options[0] di sini akan melempar
            # IndexError tepat saat operator menekan Enter.
            return None
        if key in (UP, "k"):
            self._move(-1)
        elif key in (DOWN, "j"):
            self._move(1)
        elif key == ENTER:
            opt = self.options[self.index]
            if opt.enabled:
                return MenuResult(selected=opt)
        elif len(key) == 1 and key.isdigit():
            # Nomor sebagai jalan pintas, 1-based.
            pos = int(key) - 1
            if 0 <= pos < len(self.options) and self.options[pos].enabled:
                return MenuResult(selected=self.options[pos])
        return None

    def render(self, title: str, footer: str = "",
               width: int = WIDTH) -> List[str]:
        """
        Bingkai menu dengan lebar seragam.

        Baris dipad di sini, bukan oleh `Screen.draw`. Padding di luar
        bingkai membuat setiap baris punya lebar berbeda saat diuji, dan
        yang lebih buruk: operator yang menghitung posisi kursor secara
        visual akan salah saat jumlah baris dan lebarnya tidak cocok.
        """
        lines = ["", _pad(_box_top(title, width), width)]
        for i, opt in enumerate(self.options):
            active = i == self.index
            pointer = "\u25b6" if active else " "
            if not opt.enabled:
                body = "  \u2500\u2500 {}  (tidak tersedia)".format(opt.label)
                lines.append(_pad(" \u2502 {} \u001b[38;5;240m{}\u001b[0m".format(
                    pointer, body), width))
                continue
            if active:
                colour = "\u001b[1;41;97m" if opt.danger else "\u001b[48;5;24m"
                cell = "\u001b[38;5;48m{}\u001b[0m".format(pointer)
                lines.append(_pad(
                    " \u2502 {}{}{}\u001b[0m ".format(
                        cell, colour, opt.label), width))
            else:
                lines.append(_pad(
                    " \u2502 {} \u001b[0m{}\u001b[0m ".format(pointer, opt.label),
                    width))
            if opt.detail:
                lines.append(_pad(
                    " \u2502   \u001b[38;5;244m{}\u001b[0m".format(opt.detail),
                    width))
        lines.append(_pad(_box_bottom(width), width))
        if footer:
            lines.append(_pad(" " + footer, width))
        return lines


# --------------------------------------------------------------- text input
class TextField:
    """
    Input satu baris dengan tombol panah dan backspace.

    Divalidasi per ketikan, bukan per tekan Enter. Menu yang memvalidasi
    baru saat Enter memaksa operator menelusuri ulang bolak-balik untuk
    menemukan kesalahan yang bisa saja dikatakan sejak awal.
    """

    def __init__(self, prompt: str, secret: bool = False,
                 max_length: int = 200):
        self.prompt = prompt
        self.secret = secret
        self.max_length = max_length
        self.value = ""
        self.message = ""
        self.error = ""

    def _validate(self, text: str) -> None:
        """Hook subclass: override di sini. Default: selalu valid."""

    def step(self, key: str) -> Optional[object]:
        """
        Terapkan satu tombol. Kembalikan nilai kalau Enter ditekan.

        Nilai dikembalikan hanya kalau valid — Enter pada teks yang salah
        tidak bisa "dilewati" dengan tidak sengaja.
        """
        if key == CTRL_C:
            return _CANCELLED
        if key == ESC:
            return _CANCELLED
        if key == BACKSPACE:
            self.value = self.value[:-1]
            self.error = ""
            return None
        if key == ENTER:
            if not self.value.strip():
                self.error = "kosong"
                return None
            # Error lama harus DIBERSIHKAN dulu sebelum validasi ulang.
            # Kalau tidak, error dari percobaan sebelumnya menempel dan
            # teks yang sekarang sudah benar tetap tertolak.
            self.error = ""
            self._validate(self.value)
            if self.error:
                return None
            return self.value
        if len(key) == 1 and key.isprintable():
            if len(self.value) < self.max_length:
                self.value += key
                self.error = ""
        return None

    def display(self) -> str:
        """Teks yang tampil. Untuk input rahasia, hanya panjangnya."""
        if self.secret:
            shown = "\u25cf" * len(self.value)
        else:
            shown = self.value
        line = self.prompt + shown
        if self.error:
            line += "  \u001b[1;31m< " + self.error + "\u001b[0m"
        else:
            line += "  \u001b[38;5;238m_\u001b[0m"
        return line


class _Cancelled:
    """Penanda hasil: input dibatalkan operator."""

    def __repr__(self) -> str:  # pragma: no cover
        return "<dibatalkan>"


_CANCELLED = _Cancelled()


# ------------------------------------------------------------------ runners
def run_menu(title: str, options: Sequence[Option], footer: str = "",
             screen: Optional[Screen] = None,
             reader: Optional[KeyReader] = None) -> MenuResult:
    """
    Jalankan menu panah sampai operator memilih atau membatalkan.

    Menu yang dibatalkan dianggap "tidak ada pilihan", dan pemanggil yang
    menentukan apa artinya. Menu yang mengembalikan paper karena dibatalkan
    tanpa penjelasan membuat operator mengira bot sudah berjalan.
    """
    screen = screen or Screen()
    menu = Menu(options)
    with (reader or KeyReader()) as keys:
        while True:
            screen.draw(menu.render(title, footer))
            result = menu.step(keys.wait())
            if result is not None:
                return result


def run_text(prompt: str, secret: bool = False, max_length: int = 200,
             screen: Optional[Screen] = None,
             field: Optional[TextField] = None,
             reader: Optional[KeyReader] = None) -> object:
    """
    Jalankan input teks satu baris.

    Kembalikan string, atau `_CANCELLED` kalau operator membatalkan.
    """
    screen = screen or Screen()
    field = field or TextField(prompt, secret, max_length)
    with (reader or KeyReader()) as keys:
        while True:
            screen.draw(["", "  " + field.display(), ""])
            result = field.step(keys.wait())
            if result is not None:
                return result


def is_cancelled(value: object) -> bool:
    """Benarkah hasil input berarti operator membatalkan?"""
    return isinstance(value, _Cancelled)


def fallback_prompt(prompt: str, secret: bool = False) -> str:
    """
    Bawaan untuk stdout yang bukan terminal.

    Dipakai saat output di-pipe atau dijalankan di CI. TUI penuh tidak bisa
    di sana — kursor nyasar, tidak ada yang bisa dibalas — jadi input biasa
    lebih baik daripada menggambar separuh layar lalu menggantung.
    """
    import getpass

    if secret:
        try:
            return getpass.getpass(prompt).strip()
        except Exception:  # noqa: BLE001
            return input(prompt).strip()
    return input(prompt).strip()


def render_keymap() -> str:
    return "\u001b[38;5;244m\u2191\u2193 pindah  \u2022  Enter pilih  \u2022  Esc/ Q kembali  \u2022  Ctrl+C keluar\u001b[0m"



