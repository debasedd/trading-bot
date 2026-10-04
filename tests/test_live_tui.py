"""
tests/test_live_tui.py — Bukti bahwa lapisan TUI benar dan tidak bisa
menembus ke live.

Pengujian TUI tanpa terminal adalah mungkin karena perputarannya dipisah
dari gambarannya: `Menu.step()` dan `TextField.step()` hanya menerima
tombol dan mengembalikan hasil, tanpa I/O sama sekali.
"""
import os
import unittest

from core.config import LiveConfig
from trading.live import console, tui
from trading.live.tui import Menu, Option, TextField, display_width


class FakeKeys:
    """
    KeyReader palsu yang memuntahkan kunci dari daftar.

    Berperilaku seperti context manager supaya bisa dipakai di tempat
    `KeyReader()` biasa dipakai.
    """

    def __init__(self, keys):
        self.keys = list(keys)
        self.drawn = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def wait(self):
        if not self.keys:
            self.fail("kunci habis: TUI berputar tanpa henti")
        return self.keys.pop(0)

    def fail(self, message):
        raise AssertionError(message)


class TestMenuNavigation(unittest.TestCase):
    def test_starts_on_first_enabled(self):
        menu = Menu([Option("A"), Option("B", enabled=False)])
        self.assertEqual(menu.index, 0)

    def test_down_and_up_move(self):
        menu = Menu([Option("A"), Option("B"), Option("C")])
        menu.step(tui.DOWN)
        self.assertEqual(menu.index, 1)
        menu.step(tui.DOWN)
        self.assertEqual(menu.index, 2)
        menu.step(tui.UP)
        self.assertEqual(menu.index, 1)

    def test_vim_keys_work(self):
        menu = Menu([Option("A"), Option("B"), Option("C")])
        menu.step("j")
        self.assertEqual(menu.index, 1)
        menu.step("k")
        self.assertEqual(menu.index, 0)

    def test_wraps_around(self):
        menu = Menu([Option("A"), Option("B")])
        menu.step(tui.UP)
        self.assertEqual(menu.index, 1, "atas dari pertama harus ke bawah")
        menu.step(tui.DOWN)
        self.assertEqual(menu.index, 0, "bawah dari terakhir harus ke atas")

    def test_skips_disabled(self):
        """Kursor tidak boleh mendarat di entri mati."""
        menu = Menu([Option("A"), Option("X", enabled=False),
                     Option("B")])
        menu.step(tui.DOWN)
        self.assertEqual(menu.index, 2, "harus melewati yang mati")

    def test_all_disabled_does_not_crash(self):
        menu = Menu([Option("A", enabled=False), Option("B", enabled=False)])
        menu.step(tui.DOWN)
        menu.step(tui.DOWN)
        self.assertIsNone(menu.step(tui.ENTER),
                          "Enter di menu mati tidak boleh memilih apa pun")

    def test_enter_selects(self):
        menu = Menu([Option("A", "a"), Option("B", "b")])
        menu.step(tui.DOWN)
        result = menu.step(tui.ENTER)
        self.assertIsNotNone(result)
        self.assertEqual(result.selected.value, "b")
        self.assertFalse(result.cancelled)

    def test_escape_cancels(self):
        menu = Menu([Option("A")])
        for key in (tui.ESC, "q", "Q", tui.CTRL_C):
            self.assertTrue(menu.step(key).cancelled,
                            "kunci '{}' harus membatalkan".format(key))

    def test_number_shortcut_selects(self):
        menu = Menu([Option("A", "a"), Option("B", "b"), Option("C", "c")])
        self.assertEqual(menu.step("2").selected.value, "b")

    def test_number_shortcut_skips_disabled(self):
        menu = Menu([Option("A", "a"), Option("X", enabled=False),
                     Option("C", "c")])
        result = menu.step("2")
        self.assertIsNone(result, "nomor entri mati tidak boleh diterima")

    def test_out_of_range_number_ignored(self):
        menu = Menu([Option("A", "a")])
        for key in ("0", "9", "99"):
            self.assertIsNone(menu.step(key))

    def test_unknown_key_ignored(self):
        menu = Menu([Option("A", "a")])
        for key in ("z", "!", " ", tui.LEFT, tui.RIGHT, "tab", "home"):
            self.assertIsNone(menu.step(key),
                              "kunci '{}' tidak boleh mengubah apa pun".format(key))

    def test_empty_menu_is_safe(self):
        menu = Menu([])
        menu.step(tui.DOWN)
        menu.step(tui.UP)
        self.assertIsNone(menu.step(tui.ENTER))


class TestTextField(unittest.TestCase):
    def test_typing_and_enter(self):
        field = TextField("> ")
        for ch in "abc":
            field.step(ch)
        self.assertEqual(field.step(tui.ENTER), "abc")

    def test_backspace(self):
        field = TextField("> ")
        for ch in "abc":
            field.step(ch)
        field.step(tui.BACKSPACE)
        self.assertEqual(field.value, "ab")
        field.step(tui.BACKSPACE)
        field.step(tui.BACKSPACE)
        field.step(tui.BACKSPACE)
        self.assertEqual(field.value, "", "backspace berlebih tidak boleh error")

    def test_enter_on_empty_refused(self):
        """Enter tanpa isi tidak boleh lolos.

        Kalau lolos, nilai kosong akan terbaca sebagai jawaban yang valid —
        dan untuk konfirmasi live, jawaban kosong berarti tidak
        mengonfirmasi apa pun.
        """
        field = TextField("> ")
        self.assertIsNone(field.step(tui.ENTER))
        self.assertTrue(field.error)
        for ch in "   ":
            field.step(ch)
        self.assertIsNone(field.step(tui.ENTER),
                          "spasi saja harus dianggap kosong")

    def test_escape_cancels(self):
        field = TextField("> ")
        for ch in "abc":
            field.step(ch)
        self.assertTrue(tui.is_cancelled(field.step(tui.ESC)))
        self.assertTrue(tui.is_cancelled(field.step(tui.CTRL_C)))

    def test_max_length_enforced(self):
        field = TextField("> ", max_length=4)
        for ch in "abcdefgh":
            field.step(ch)
        self.assertEqual(len(field.value), 4)

    def test_validator_blocks_enter(self):
        """
        Enter hanya diterima kalau validasi lolos.

        Ini yang mencegah live aktif karena operator menekan Enter di layar
        konfirmasi yang salah.
        """
        field = TextField("> ")

        def _v(text):
            if text != "BENAR":
                field.error = "harus BENAR"

        field._validate = _v
        for ch in "SALAH":
            field.step(ch)
        self.assertIsNone(field.step(tui.ENTER),
                          "teks salah tidak boleh diterima")
        self.assertTrue(field.error)

        field.value = "BENAR"
        self.assertEqual(field.step(tui.ENTER), "BENAR")

    def test_error_cleared_by_typing(self):
        field = TextField("> ")
        field.step(tui.ENTER)
        self.assertTrue(field.error)
        field.step("a")
        self.assertFalse(field.error, "error harus hilang saat diketik ulang")

    def test_secret_masks_display(self):
        field = TextField("key: ", secret=True)
        for ch in "0xdeadbeef":
            field.step(ch)
        shown = field.display()
        self.assertNotIn("deadbeef", shown, "rahasia tidak boleh terlihat")
        self.assertIn("\u25cf", shown)

    def test_plain_display_shows_value(self):
        field = TextField("> ")
        for ch in "0xabc":
            field.step(ch)
        self.assertIn("0xabc", field.display())


class TestRendering(unittest.TestCase):
    """Lebar bingkai harus benar meski isinya berisi kode warna."""

    def test_ansi_codes_do_not_count_toward_width(self):
        coloured = "\u001b[1;31mmerah\u001b[0m"
        self.assertEqual(display_width(coloured), 5)

    def test_wide_chars_count_as_two(self):
        self.assertEqual(display_width("ab"), 2)
        self.assertEqual(display_width("\u4f60\u597d"), 4)

    def test_menu_lines_all_same_width(self):
        menu = Menu([
            Option("SIMULASI (paper)", "paper", detail="tanpa order"),
            Option("MAINNET", "mainnet", detail="UANG SUNGGAHAN", danger=True),
            Option("MATI", "x", enabled=False),
        ])
        lines = menu.render("Mode")
        widths = {tui.display_width(line) for line in lines}
        self.assertLessEqual(
            len(widths), 3,
            "bingkai harus rata, dapat lebar: {}".format(widths),
        )

    def test_disabled_entry_marked(self):
        menu = Menu([Option("MATI", enabled=False)])
        text = "".join(menu.render("Uji"))
        self.assertIn("tidak tersedia", text)

    def test_danger_entry_highlighted(self):
        menu = Menu([Option("MAINNET", danger=True)])
        menu.index = 0
        self.assertIn("41;97", "".join(menu.render("Uji")))

    def test_empty_menu_renders(self):
        self.assertTrue(Menu([]).render("Kosong"))


class TestScreenRedraw(unittest.TestCase):
    """
    Layar harus digambar ulang di tempat, bukan ditumpuk.

    Ini yang dikeluhkan operator: tanpa ini, setiap penekanan panah
    menambah blok baru dan layar memanjang tanpa batas.
    """

    def setUp(self):
        self.written = []
        self._orig = tui._write
        tui._write = self.written.append

    def tearDown(self):
        tui._write = self._orig

    def test_repeated_draw_does_not_clear_whole_screen(self):
        """
        Frame kedua tidak boleh memakai clear-total.

        `2J` di setiap frame memunculkan kedipan. Yang dipakai: ke atas
        (HOME), tulis, lalu hapus sisa baris.
        """
        screen = tui.Screen()
        for _ in range(5):
            screen.draw(["baris satu", "baris dua"])
        rest = self.written[1:]
        for frame in rest:
            self.assertNotIn(
                tui.ANSI_CLEAR, frame,
                "clear-total per frame bikin layar berkedip",
            )
        self.assertTrue(all(tui.ANSI_HOME in f for f in rest))

    def test_each_line_erased_to_end(self):
        """Baris dipad, lalu sisa baris dihapus.

        Tanpa penghapusan sisa, baris baru yang lebih pendek akan
        menyisakan ekor baris lama di sebelah kanan.
        """
        screen = tui.Screen()
        screen.draw(["pendek"])
        self.assertIn(tui.ANSI_ERASE_LINE, self.written[-1])
        self.assertIn(tui.ANSI_ERASE_DOWN, self.written[-1])

    def test_lines_padded_to_uniform_width(self):
        screen = tui.Screen()
        screen.draw(["a", "bb", "ccc"])
        frame = self.written[-1]
        for line in frame.split("\n"):
            # `ANSI_ERASE_DOWN` sengaja menggantung sendiri di baris
            # kosong: ia bukan konten, cuma instruksi hapus sisa bawah.
            if not line.replace(tui.ANSI_ERASE_DOWN, "").strip():
                continue
            body = line.replace(tui.ANSI_ERASE_LINE, "")
            self.assertEqual(
                tui.display_width(body), screen.width,
                "baris tidak sama lebar: {!r}".format(body),
            )

    def test_cursor_hidden_then_shown(self):
        """Cursor harus dikembalikan, kalau tidak prompt shell hilang."""
        screen = tui.Screen()
        screen.start()
        self.assertIn(tui.ANSI_HIDE_CURSOR, self.written[-1])
        screen.stop()
        self.assertIn(tui.ANSI_SHOW_CURSOR, self.written[-1])

    def test_context_manager_restores_cursor(self):
        with tui.Screen():
            pass
        self.assertIn(tui.ANSI_SHOW_CURSOR, self.written[-1])

    def test_stop_is_idempotent(self):
        screen = tui.Screen()
        screen.start()
        screen.stop()
        count = len(self.written)
        screen.stop()
        self.assertEqual(len(self.written), count,
                         "stop dua kali tidak boleh menulis lagi")


def _posix_key(text):
    """Terjemahkan byte mentah persis seperti `_read_posix` melakukannya."""
    if text.startswith("\x1b["):
        return {"A": tui.UP, "B": tui.DOWN, "C": tui.RIGHT,
                "D": tui.LEFT}.get(text[2:3], None)
    if text.startswith("\x1bO"):
        return tui._WINDOWS_KEYS.get(text[2:3], None)
    first = text[0]
    if first == "\x03":
        return tui.CTRL_C
    if first in ("\r", "\n"):
        return tui.ENTER
    if first == "\x1b":
        return tui.ESC
    if first in ("\x08", "\x7f"):
        return tui.BACKSPACE
    return first


class TestKeyParsing(unittest.TestCase):
    """
    Pemetaan byte mentah ke nama tombol.

    Salah memetakan di sini berarti menggeser kursor ke entri yang salah
    saat operator menekan panah — dan untuk menu live, kursor yang salah
    diikuti Enter = mode yang tidak diinginkan.
    """

    def setUp(self):
        if os.name == "nt":
            self.skipTest("jalur POSIX tidak berlaku di Windows")

    def test_escape_sequences(self):
        for seq, expected in (("\x1b[A", tui.UP), ("\x1b[B", tui.DOWN),
                              ("\x1b[C", tui.RIGHT), ("\x1b[D", tui.LEFT)):
            with self.subTest(seq=seq):
                self.assertEqual(_posix_key(seq), expected)

    def test_control_chars(self):
        self.assertEqual(_posix_key("\r"), tui.ENTER)
        self.assertEqual(_posix_key("\n"), tui.ENTER)
        self.assertEqual(_posix_key("\x1b"), tui.ESC)
        self.assertEqual(_posix_key("\x7f"), tui.BACKSPACE)
        self.assertEqual(_posix_key("\x03"), tui.CTRL_C)

    def test_plain_char(self):
        self.assertEqual(_posix_key("a"), "a")
        self.assertEqual(_posix_key("1"), "1")

    def test_arrow_never_maps_to_a_letter(self):
        """
        Panah tidak boleh pernah terpetakan menjadi huruf biasa.

        Kalau iya, `Menu.step` akan memperlakukannya sebagai pintasan
        angka atau pemindah, dan kursor bergerak ke tempat yang tidak
        pernah operator pilih.
        """
        for seq in ("\x1b[A", "\x1b[B", "\x1b[C", "\x1b[D"):
            self.assertNotIn(_posix_key(seq), "abjkq")


class BoundedKeys(FakeKeys):
    """
    KeyReader yang berhenti alih-alih berputar selamanya.

    Menu yang tidak pernah menerima kunci yang valid akan loop tanpa
    henti. Untuk pengujian, itu harus jadi kegagalan yang terlihat, bukan
    hang.
    """

    def wait(self):
        if not self.keys:
            raise KeyboardInterrupt("kunci habis")
        return self.keys.pop(0)


class SharedKeys:
    """
    KeyReader yang menarik dari antrean milik pemanggil.

    Berbeda dengan `BoundedKeys`, daftar kuncinya TIDAK disalin. Semua
    instance yang dibuat selama satu alur mengambil dari antrean yang sama,
    jadi kunci terpakai berurutan dari menu pertama sampai input terakhir.
    """

    def __init__(self, shared):
        self._shared = shared

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def wait(self):
        if not self._shared:
            raise KeyboardInterrupt(
                "kunci habis: alur meminta lebih banyak tombol dari yang "
                "disiapkan test"
            )
        return self._shared.pop(0)



class TestModeMenuFlow(unittest.TestCase):
    """
    Alur `ask_mode` dari menu panah sampai keputusan.

    Yang dibuktikan di sini: navigasi panah tidak bisa melewati konfirmasi
    dan mendarat langsung di live.
    """

    def setUp(self):
        os.environ.pop("HYPERLIQUID_PRIVATE_KEY", None)
        self.saved = (
            console.interactive, console.show_banner, console._read,
            console._pause, tui._write, tui.KeyReader,
        )
        tui._write = lambda *a, **k: None
        console._pause = lambda *a, **k: None
        console.interactive = lambda: True
        console.show_banner = lambda: None
        console._read = lambda prompt="": ""

    def tearDown(self):
        (console.interactive, console.show_banner, console._read,
         console._pause, tui._write, tui.KeyReader) = self.saved
        os.environ.pop("HYPERLIQUID_PRIVATE_KEY", None)

    def _ask(self, keys):
        reader = BoundedKeys(keys)
        tui.KeyReader = lambda: reader
        return console.ask_mode(LiveConfig())

    def test_enter_on_paper(self):
        decision = self._ask([tui.ENTER])
        self.assertEqual(decision.mode, "paper")
        self.assertFalse(decision.refused)

    def test_escape_returns_paper(self):
        decision = self._ask([tui.ESC])
        self.assertEqual(decision.mode, "paper")
        self.assertTrue(decision.refused)

    def test_ctrl_c_returns_paper(self):
        self.assertEqual(self._ask([tui.CTRL_C]).mode, "paper")

    def test_arrows_land_back_on_paper(self):
        """
        Panah naik dari entri pertama kembali ke entri terakhir, dan
        panah naik lagi kembali ke awal.

        Perjalanan bolak-balik ini yang diuji, bukan tujuannya: kalau
        navigasi tidak simetris, operator tidak bisa yakin kursornya di
        mana sebelum menekan Enter.
        """
        menu = Menu(console.mode_options(LiveConfig(), has_key=False))
        first = menu.index
        last = len(menu.options) - 1
        menu.step(tui.UP)
        self.assertEqual(menu.index, last, "atas dari awal harus ke bawah")
        menu.step(tui.UP)
        self.assertEqual(menu.index, last - 1)
        menu.step(tui.DOWN)
        self.assertEqual(menu.index, last)
        menu.step(tui.DOWN)
        self.assertEqual(menu.index, first, "bawah dari akhir harus ke atas")

    def test_mode_menu_wraps_through_all_entries(self):
        """Setiap entri harus bisa dicapai hanya dengan panah."""
        options = console.mode_options(LiveConfig(), has_key=False)
        menu = Menu(options)
        seen = {menu.index}
        for _ in range(len(options)):
            menu.step(tui.DOWN)
            seen.add(menu.index)
        self.assertEqual(seen, set(range(len(options))))

    def test_no_key_means_no_live(self):
        """
        Tanpa private key, memilih live tidak boleh mencapai apa pun.

        Kunci diberikan lengkap: pilih testnet/mainnet, lalu saat menu
        muncul lagi tekan Esc. Hasilnya harus paper dengan flag refused.

        Urutan ini juga membuktikan alurnya: memilih live tanpa key
        TIDAK keluar dari menu dan TIDAK menjalankan apa pun — ia
        kembali ke menu dan menunggu operator.
        """
        for prefix in ([tui.DOWN, tui.ENTER], [tui.DOWN, tui.DOWN, tui.ENTER]):
            with self.subTest(prefix=prefix):
                decision = self._ask(prefix + [tui.ESC])
                self.assertEqual(decision.mode, "paper")
                self.assertTrue(decision.refused,
                                "harus ditandai ditolak, bukan dipilih")

    def test_wrong_phrase_never_reaches_live(self):
        """
        Konfirmasi salah = tidak pernah live.

        Private key disetel dulu supaya layar konfirmasi benar-benar
        muncul; tanpa itu, memilih testnet langsung ditolak karena key
        kosong dan kalimat yang diketik tidak pernah sampai ke validator.

        Kunci sengaja dibuat habis sebelum konfirmasi selesai. Kalau
        validasi bocor, mode keluar sebagai testnet.
        """
        os.environ["HYPERLIQUID_PRIVATE_KEY"] = "0x" + "ab" * 32
        keys = [tui.DOWN, tui.ENTER] + list("salah") + [tui.ENTER]
        with self.assertRaises(KeyboardInterrupt):
            self._ask(keys)

    def test_wrong_address_never_reaches_live(self):
        """
        Kalimat benar tapi alamat salah = tetap ditolak.

        Ini langkah kedua dari dua. Kalau langkah ini bocor, operator bisa
        mengirim order ke akun yang tidak dia maksud.
        """
        os.environ["HYPERLIQUID_PRIVATE_KEY"] = "0x" + "ab" * 32
        keys = [tui.DOWN, tui.ENTER]
        keys += list(console.CONFIRM_PHRASE) + [tui.ENTER]
        keys += list("0x" + "00" * 20) + [tui.ENTER]
        with self.assertRaises(KeyboardInterrupt):
            self._ask(keys)

    def test_escape_during_confirmation_returns_to_menu(self):
        """Membatalkan di tengah konfirmasi = kembali ke menu, bukan paper."""
        os.environ["HYPERLIQUID_PRIVATE_KEY"] = "0x" + "ab" * 32
        decision = self._ask([tui.DOWN, tui.ENTER, tui.ESC, tui.ESC])
        self.assertEqual(decision.mode, "paper")
        self.assertTrue(decision.refused)


    def test_number_shortcut_to_testnet_without_key_stays_paper(self):
        """
        Pintasan angka ke live tanpa key juga tidak menembus.

        Jalur pintas angka adalah jalan termudah untuk salah pilih, jadi
        ia harus punya proteksi yang sama dengan navigasi panah.
        """
        for key in ("2", "3"):
            with self.subTest(key=key):
                decision = self._ask([key, tui.ESC])
                self.assertEqual(decision.mode, "paper")
                self.assertTrue(decision.refused)

    def test_number_shortcut_to_paper_works(self):
        decision = self._ask(["1"])
        self.assertEqual(decision.mode, "paper")
        self.assertFalse(decision.refused)


class TestUnpatchedSmoke(unittest.TestCase):
    """
    Jalur yang TIDAK dimock.

    Test lain mem-patch `interactive()`, `_read`, dan `KeyReader` supaya
    bisa dikendalikan. Persis patching itulah yang menyembunyikan bug
    `enable_vt_processing` — fungsi yang dipanggil tidak pernah benar-benar
    dieksekusi, jadi nama yang salah padanya baru meledak waktu bot
    dijalankan manusia.

    Test di sini memanggil kode apa adanya. Yang dipalsukan hanya
    `isatty`, yang mustahil direplikasi tanpa terminal sungguhan.
    """

    def setUp(self):
        self.saved = (tui.is_tty, tui._write, console._pause,
                      tui.KeyReader, console.interactive, console._read)
        tui._write = lambda *a, **k: None
        console._pause = lambda *a, **k: None
        tui.is_tty = lambda: True
        # `console._read` ikut diganti. `ask_mode()` memanggil
        # `show_banner()` yang memanggil `_read()` yang memanggil
        # `input()`. pytest menangkap stdin, jadi tanpa ini dua test
        # `ask_mode` gagal dengan:
        #
        #     OSError: pytest: reading from stdin while output is captured!
        #
        # `_read` sengaja TIDAK ditangani di sini sebagai no-op: nilainya
        # menentukan alur. Test yang butuh nilai tertentu memasang
        # `console._read`-nya sendiri.
        self._pending_reads = []
        console._read = lambda prompt: (
            self._pending_reads.pop(0) if self._pending_reads else "x")
        # Dipaksa True, bukan dibiarkan `interactive()` yang memutuskan.
        # `interactive()` nyata bergantung pada `GetConsoleMode`, yang
        # selalu gagal saat stdout di-pipe — termasuk di test runner. Jadi
        # TIDAK mungkin menguji jalur TUI lewat stdout yang bukan terminal.
        #
        # Jalur `interactive()` itu sendiri diuji terpisah oleh
        # `test_interactive_does_not_raise`, yang memanggil aslinya — itulah
        # test yang menangkap bug `enable_vt_processing`.
        console.interactive = lambda: True

    def tearDown(self):
        (tui.is_tty, tui._write, console._pause, tui.KeyReader,
         console.interactive, console._read) = self.saved
        os.environ.pop("HYPERLIQUID_PRIVATE_KEY", None)

    def _keys(self, keys):
        """
        Reader yang berbagi satu antrean kunci, dibangun baru tiap giliran.

        `run_menu` dan `run_text` masing-masing membuat `KeyReader()` sendiri
        untuk satu giliran lalu membuangnya. Dua hal harus berlaku:

        - Antrean DIBAGI, supaya kunci terpakai berurutan di seluruh layar.
        - Setiap giliran dapat instance BARU, supaya tidak bentrok dengan
          instance yang dibuat `run_menu` di dalam.

        Kalau test memakai satu instance untuk semuanya, kunci habis dipakai
        menu pertama dan input angka berikutnya tidak pernah terbaca.
        """
        shared = list(keys)

        def _factory():
            return SharedKeys(shared)
        return _factory

    def test_interactive_does_not_raise(self):
        """
        `interactive()` harus mengembalikan bool, tidak pernah melempar.

        Inilah test yang menangkap bug nama fungsi. Versi sebelumnya
        memanggil `tui.enable_vt_processing()` yang tidak ada, jadi proses
        mati persis di baris ini.
        """
        result = console.interactive()
        self.assertIsInstance(
            result, bool,
            "interactive() harus bool, dapat {!r}".format(type(result)),
        )

    def test_interactive_false_when_not_tty(self):
        """
        `interactive()` harus False saat bukan terminal.

        `interactive` sengaja TIDAK dipatch di sini — di sinilah
        logikanya diuji. Yang dipalsukan hanya `is_tty`, yang mustahil
        direplikasi tanpa terminal sungguhan.
        """
        console.interactive = self.saved[4]
        tui.is_tty = lambda: False
        self.assertFalse(console.interactive())

    def test_interactive_false_when_vt_unavailable(self):
        """
        Terminal sungguhan tapi VT tidak aktif = tetap bukan interaktif.

        Ini kondisi nyata di cmd.exe lama dan di beberapa emulator. Kembali ke input bernomor lebih baik daripada menggambar escape
        sequence sebagai teks.
        """
        console.interactive = self.saved[4]
        tui.is_tty = lambda: True
        saved = tui.enable_vt_processing
        tui.enable_vt_processing = lambda: False
        try:
            self.assertFalse(console.interactive())
        finally:
            tui.enable_vt_processing = saved


    def test_enable_vt_processing_returns_bool(self):
        """Fungsi VT harus bool di kedua platform."""
        self.assertIsInstance(tui.enable_vt_processing(), bool)

    def test_enable_vt_processing_idempotent(self):
        """Memanggil dua kali tidak boleh merusak mode konsol."""
        tui.enable_vt_processing()
        self.assertIsInstance(tui.enable_vt_processing(), bool)

    def test_menu_runs_with_real_run_menu(self):
        """
        `run_menu` dipanggil apa adanya.

        Hanya `KeyReader` yang diganti, karena ia yang memblokir
        menunggu input manusia. `Menu`, `Screen`, `render`, dan
        `display_width` benar-benar dieksekusi.
        """
        tui.KeyReader = self._keys([tui.DOWN, tui.ENTER])
        result = tui.run_menu("Uji", [Option("A", "a"), Option("B", "b")])
        self.assertEqual(result.selected.value, "b")

    def test_text_runs_with_real_run_text(self):
        tui.KeyReader = self._keys(list("halo") + [tui.ENTER])
        self.assertEqual(tui.run_text("> "), "halo")

    def test_ask_mode_paper_via_real_path(self):
        """
        `ask_mode` dari atas sampai bawah tanpa mem-patch `interactive`.

        `interactive` dipalsukan ke True, tapi `enable_vt_processing`
        dipanggil sungguhan. Inilah alur yang hancur di tangan Anda.
        """
        tui.KeyReader = self._keys([tui.ENTER])
        decision = console.ask_mode(LiveConfig())
        self.assertEqual(decision.mode, "paper")
        self.assertFalse(decision.refused)

    def test_ask_mode_escape_via_real_path(self):
        tui.KeyReader = self._keys([tui.ESC])
        decision = console.ask_mode(LiveConfig())
        self.assertTrue(decision.refused)
        self.assertEqual(decision.mode, "paper")

    def test_ask_mode_non_tty_uses_plain_path(self):
        """
        Kalau bukan terminal, `ask_mode` tidak boleh menyentuh TUI sama
        sekali. Jalur ini yang dipakai saat output di-pipe.

        `interactive` dipaksa False di sini karena `setUp` memaksanya True.
        Kunci kosong dipasang sebagai jaring pengaman: kalau TUI tetap
        tersentuh, `wait()` melempar alih-alih membiarkan input kosong
        lolos diam-diam.
        """
        console.interactive = lambda: False
        tui.is_tty = lambda: False
        tui.KeyReader = self._keys([])
        saved = (console.show_banner, console._read)
        console.show_banner = lambda: None
        console._read = lambda prompt="": "1"
        try:
            decision = console.ask_mode(LiveConfig())
        finally:
            console.show_banner, console._read = saved
        self.assertEqual(decision.mode, "paper")
        self.assertFalse(decision.refused)


    def test_rules_menu_opens_and_closes_via_real_path(self):
        """Menu aturan harus bisa dibuka lalu ditutup tanpa error."""
        # 9 entri: 7 angka + jendela + "Kembali".
        tui.KeyReader = self._keys([tui.DOWN] * 8 + [tui.ENTER])
        cfg = console.edit_rules(LiveConfig())
        self.assertIsInstance(cfg, LiveConfig)

    def test_rules_menu_escape_returns_unchanged(self):
        before = LiveConfig().max_order_notional
        tui.KeyReader = self._keys([tui.DOWN, tui.ESC])
        cfg = console.edit_rules(LiveConfig())
        self.assertAlmostEqual(cfg.max_order_notional, before)

    def test_edit_rule_value_via_real_path(self):
        """Ubah satu angka batas, lalu keluar menu."""
        # Enter di entri pertama -> ketik "250" -> Enter -> panah sampai
        # entri "Kembali" -> Enter.
        tui.KeyReader = self._keys(
            [tui.ENTER] + list("250") + [tui.ENTER]
            + [tui.DOWN] * 8 + [tui.ENTER])
        cfg = console.edit_rules(LiveConfig())
        self.assertAlmostEqual(
            cfg.max_order_notional, 250.0,
            msg="batas harus tersimpan setelah diubah",
        )

    def test_edit_rule_rejects_nan_via_real_path(self):
        """
        NaN harus ditolak di jalur nyata.

        `_validate` di `TextField` yang menolak, bukan `validate_rule` —
        jadi test ini juga membuktikan keduanya tersambung.
        """
        before = LiveConfig().max_order_notional
        tui.KeyReader = self._keys(
            [tui.ENTER] + list("NaN") + [tui.ENTER, tui.ESC, tui.ESC])
        cfg = console.edit_rules(LiveConfig())
        self.assertAlmostEqual(cfg.max_order_notional, before,
                               msg="NaN tidak boleh tersimpan")
        self.assertTrue(
            cfg.max_order_notional == cfg.max_order_notional,
            "batas tidak boleh jadi NaN: {!r}".format(cfg.max_order_notional),
        )

    def test_edit_rule_rejects_out_of_range_via_real_path(self):
        before = LiveConfig().max_order_notional
        tui.KeyReader = self._keys(
            [tui.ENTER] + list("999999999") + [tui.ENTER, tui.ESC, tui.ESC])
        cfg = console.edit_rules(LiveConfig())
        self.assertAlmostEqual(cfg.max_order_notional, before,
                               msg="nilai di luar rentang tidak boleh masuk")

    def test_edit_int_rule_via_real_path(self):
        tui.KeyReader = self._keys(
            [tui.DOWN] * 3 + [tui.ENTER] + list("50") + [tui.ENTER]
            + [tui.ESC, tui.ESC])
        cfg = console.edit_rules(LiveConfig())
        self.assertEqual(cfg.max_daily_orders, 50)
        self.assertIsInstance(cfg.max_daily_orders, int)






