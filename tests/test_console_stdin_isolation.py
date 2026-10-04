"""
Dua test `test_live_tui::TestUnpatchedSmoke` yang gagal, dan perbaikannya.

MASALAH
------
`test_ask_mode_escape_via_real_path` dan
`test_ask_mode_paper_via_real_path` memanggil `console.ask_mode()` dengan
`interactive()` dipalsukan ke True. `setUp` di kelas itu mengganti
`tui._write`, `console._pause`, `tui.is_tty`, dan `tui.KeyReader` — tapi
TIDAK mengganti `console._read`.

`ask_mode()` memanggil `show_banner()` (console.py:419), dan
`show_banner()` memanggil `_read()` (console.py:275), yang memanggil
`input()`. pytest menangkap stdin, jadi `input()` tidak pernah menerima
apa pun:

    OSError: pytest: reading from stdin while output is captured!
              Consider using `-s`.

Dua test ini tidak pernah hijau sejak ditulis. Penyebabnya bukan
regresi dari Fase 1 -- keduanya pre-existing, terbukti di Fase 0 dengan
`git stash`.

KENAPA TIDAK CUKUP DITAMBAH KE XFAIL
Gerbang Fase 1 butuh suite hijau, dan test yang dibiarkan gagal berarti
ada jalur setup operator yang tidak pernah benar-benar dieksekusi.
`ask_mode` adalah pintu masuk ARMING -- jalur yang menentukan apakah bot
boleh mengirim order ke bursa. Jalur itu wajib punya bukti hijau.

APA YANG DIUJI DI SINI
`_read` adalah satu-satunya jalan keluar dari stdin di modul console, dan
ia tidak ada di daftar yang di-swap `setUp`. Test di sini membuktikan
`_read` benar-benar dipanggil oleh `ask_mode`, supaya penambahan ke daftar
swap itu punya dasar -- bukan sekadar membuat test hijau.

Setelah `_read` masuk daftar swap, dua test `TestUnpatchedSmoke` itu
menguji jalur TUI sungguhan: `KeyReader` async, `Screen`, `render`,
`enable_vt_processing`, dan `decide_mode` semuanya dieksekusi.
"""
import io
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from trading.live import console


class _FakeStdin:
    """
    Objek yang berperilaku seperti stdin yang sudah ditutup.

    `input()` yang membaca dari sini harusLENGSUNG gagal dengan
    `OSError` -- persis yang terjadi di pytest. Objek ini dipakai untuk
    membuktikan bahwa `_read` TIDAK ditangani `except EOFError`, jadi
    kegagalan tidak bisa tidak terlihat.
    """

    def readline(self, *args, **kwargs):
        raise OSError(
            "pytest: reading from stdin while output is captured! "
            "Consider using `-s`.")

    def read(self, *args, **kwargs):
        raise OSError("reading from closed stdin")

    def isatty(self):
        return False

    def fileno(self):
        raise OSError("redirected stdin is pseudofile")


class TestReadIsNotSilentlySwallowed(unittest.TestCase):
    """
    `_read` hanya menangkap `EOFError`.

    Ini bukan detail: kalau `_read` juga menelan `OSError`, maka test
    yang memanggil `ask_mode` tanpa meng-swap `_read` akan mendapat
    string kosong dan BERHASIL tanpa menyentuh terminal sama sekali --
    persis hasil yang salah.
    """

    def test_oserror_propagates_from_read(self):
        """
        `OSError` dari stdin harus sampai ke pemanggil.

        Kalau tidak, kegagalan "tidak bisa baca input" berubah jadi
        "operator mengetik kosng" dan tidak ada yang tahu.
        """
        import builtins

        real = builtins.input
        builtins.input = lambda *a, **k: (_ for _ in ()).throw(
            OSError("reading from closed stdin"))
        try:
            with self.assertRaises(OSError):
                console._read("prompt")
        finally:
            builtins.input = real

    def test_eoferror_becomes_empty_string(self):
        """
        `EOFError` yang jadi string kosong ITU benar.

        Jalur output di-pipe memang tidak punya stdin; di sana
       `_ask_mode_plain` yang dipakai, dan string kosong membuat alur
        berhenti dengan rapi alih-alih crash.
        """
        import builtins

        real = builtins.input
        builtins.input = lambda *a, **k: (_ for _ in ()).throw(EOFError())
        try:
            self.assertEqual(console._read("prompt"), "")
        finally:
            builtins.input = real


class TestAskModeBannerReadsFromRead(unittest.TestCase):
    """
    `show_banner()` benar-benar memanggil `_read`.

    Inilah yang membuat kegagalan tadi terjadi, dan yang membuat
    perbaikannya (menambah `_read` ke daftar swap) punya alasan.
    """

    def test_show_banner_calls_read(self):
        """
        Banner{Banner} adalah pemanggil `_read` pertama di alur
        `ask_mode`.

        Kalau ini berubah, daftar swap di `setUp` jadi usang dan test
    akan gagal lagi dengan pesan yang sama tapi sebab berbeda.
        """
        calls = []

        real = console._read
        console._read = lambda prompt: (
            calls.append(prompt) or "x")
        try:
            console.show_banner()
        finally:
            console._read = real

        self.assertTrue(
            calls,
            "show_banner tidak memanggil _read; daftar swap di "
            "TestUnpatchedSmoke.setUp tidak perlu _read dan test "
            "ask_mode akan gagal lagi",
        )

    def test_ask_mode_reaches_banner_through_read(self):
        """
        Alur `ask_mode` -> `show_banner` -> `_read` harus utuh.

        Yang dipalsukan hanya `interactive` (harus True supaya jalur
        TUI dipilih) dan `tui.is_tty` (terminal sungguhan mustahil
        direplikasi). `_read` sendiri yang memanggil.
        """
        from trading.live import tui

        saved = (
            console._read, console._pause, console.interactive,
            tui.is_tty, tui._write, tui.KeyReader,
        )
        read_calls = []

        def fake_read(prompt):
            read_calls.append(prompt)
            return "x"   # batal

        console._read = fake_read
        console._pause = lambda *a, **k: None
        console.interactive = lambda: True
        tui.is_tty = lambda: True
        tui._write = lambda *a, **k: None
        tui.KeyReader = _keys_factory([tui.ESC])
        try:
            decision = console.ask_mode(_cfg())
        finally:
            (console._read, console._pause, console.interactive,
             tui.is_tty, tui._write, tui.KeyReader) = saved

        self.assertTrue(read_calls,
                        "ask_mode tidak menyentuh _read sama sekali")
        self.assertTrue(decision.refused,
                        "operator yang menekan ESC harus ditolak")


class _SharedKeys:
    """
    KeyReader dengan antrean yang dibagi, mengikuti bentuk yang dipakai
    `tests/test_live_tui.py::SharedKeys`.

    Bentuknya penting: `tui.run_menu` membuat `KeyReader()` sendiri lalu
    memakainya sebagai context manager sinkron (`with ... as keys`) dan
    mengambil kunci lewat `wait()`. Mengasync-kan helper di sini
    membuat test gagal karena alasan yang salah.
    """

    def __init__(self, shared):
        self._shared = list(shared)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def wait(self):
        if not self._shared:
            raise KeyboardInterrupt("kunci habis")
        return self._shared.pop(0)


def _keys_factory(keys):
    """Pabrik yang diminjan `tui.KeyReader` untuk alur satu giliran."""
    shared = list(keys)

    def factory():
        return _SharedKeys(shared)
    return factory


def _cfg():
    from core.config import LiveConfig
    return LiveConfig()


if __name__ == "__main__":
    unittest.main()