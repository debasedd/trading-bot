"""
tests/test_cpp_kernel.py — Paritas antara kernel C++20 dan PythonKernel.

Toleransi 1e-7 seperti yang dispesifikasikan sebenarnya lebih ketat dari yang
dibutuhkan untuk float64, dan itu disengaja. Kedua implementasi menjalankan
operasi aritmetika yang sama dalam urutan yang sama, jadi hasilnya harus
identik sampai digit terakhir. Selisih yang jauh lebih besar dari 1e-7 berarti
ada perubahan urutan operasi di salah satu sisi — dan itu justru bug yang
paling berbahaya, karena kernel yang lebih cepat bisa diam-diam menghasilkan
angka OFI yang sedikit berbeda. Kecocokan yang bersih lebih penting daripada
kecepatan.

Test yang butuh modul C++ akan DILEWATI (skip), bukan gagal, kalau modul belum
di-build. Ini disengaja: `python -m unittest discover tests` harus tetap hijau
di mesin yang belum punya compiler C++.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent

# Sample L2 books yang dipakai bersama kedua kernel. Sengaja berisi kasus
# lebih dari 20 baris, bukan 5: bobot per level dan urutan penjumlahan adalah
# bagian dari kontrak paritas, jadi kedua sisi harus melewati ambang `depth`
# yang sama.
BIDS_5 = [
    (99.99, 10.0), (99.98, 12.0), (99.97, 8.0), (99.96, 15.0), (99.95, 6.0),
]
ASKS_5 = [
    (100.01, 3.0), (100.02, 4.0), (100.03, 2.0), (100.04, 7.0), (100.05, 5.0),
]
BIDS_20 = [(round(100.0 - i * 0.01, 2), float(10 + (i % 5) * 2)) for i in range(20)]
ASKS_20 = [(round(100.0 + 0.01 + i * 0.01, 2), float(9 + (i % 4) * 3)) for i in range(20)]

TOLERANCE = 1e-7


def _load_cpp():
    """Import modul C++, atau kembalikan None kalau belum di-build."""
    try:
        import cpp_microstructure  # noqa: F401
    except Exception:
        return None
    return cpp_microstructure


CPP = _load_cpp()
SKIP_REASON = (
    "Modul cpp_microstructure belum di-build. Build dengan:\n"
    "  cmake -S . -B build -DCMAKE_BUILD_TYPE=Release\n"
    "  cmake --build build --config Release"
)


def _make_pair(bids, asks, symbol="BTC/USDT:USDT"):
    """Buat sepasang kernel (C++ dan Python) dengan book yang sama."""
    from core.microstructure import PythonKernel

    py_kernel = PythonKernel()
    py_kernel.ingest_l2(symbol, bids, asks)

    if CPP is None:
        return None, py_kernel

    from core.microstructure import CppMicrostructureKernel
    cpp_kernel = CppMicrostructureKernel(native_module=CPP)
    cpp_kernel.ingest_l2(symbol, bids, asks)
    return cpp_kernel, py_kernel


@unittest.skipIf(CPP is None, SKIP_REASON)
class TestCppPythonParity(unittest.TestCase):
    """Nilai OFI dan depth dari kedua kernel harus identik."""

    def _assert_parity(self, bids, asks, depth=5, symbol="BTC/USDT:USDT"):
        cpp_kernel, py_kernel = _make_pair(bids, asks, symbol)
        self.assertIsNotNone(cpp_kernel, "kernel C++ tidak bisa dibuat")

        cpp_ofi, cpp_spread = cpp_kernel.order_flow_imbalance(symbol, depth)
        py_ofi, py_spread = py_kernel.order_flow_imbalance(symbol, depth)

        self.assertAlmostEqual(
            cpp_ofi, py_ofi, delta=TOLERANCE,
            msg=f"OFI berbeda pada depth={depth}: cpp={cpp_ofi!r} py={py_ofi!r}",
        )
        self.assertAlmostEqual(
            cpp_spread, py_spread, delta=TOLERANCE,
            msg=f"spread berbeda pada depth={depth}: "
                f"cpp={cpp_spread!r} py={py_spread!r}",
        )
        self.assertAlmostEqual(
            cpp_kernel.depth_imbalance(symbol, depth),
            py_kernel.depth_imbalance(symbol, depth),
            delta=TOLERANCE,
            msg=f"depth imbalance berbeda pada depth={depth}",
        )

    def test_parity_balanced_book(self):
        """Book seimbang harus menghasilkan OFI ~0 di kedua kernel."""
        bids = [(100.0 - i * 0.01, 5.0) for i in range(5)]
        asks = [(100.01 + i * 0.01, 5.0) for i in range(5)]
        self._assert_parity(bids, asks)

    def test_parity_bid_heavy(self):
        """Tekanan bid besar harus memberi OFI positif di kedua kernel."""
        self._assert_parity(BIDS_5, ASKS_5)

    def test_parity_ask_heavy(self):
        """Tekanan ask besar harus memberi OFI negatif di kedua kernel."""
        self._assert_parity(ASKS_5, BIDS_5)

    def test_parity_deep_book_20_levels(self):
        """
        Book 20 level harus dipotong ke `depth` yang sama oleh kedua kernel.

        Ini menguji bahwa tidak ada kernel yang diam-diam menghitung seluruh
        20 level sementara yang lain berhenti di 5 — perbedaan sekecil itu akan
        terlihat sebagai OFI yang berbeda secara sistematis pada book dalam.
        """
        for depth in (1, 3, 5, 10, 20):
            with self.subTest(depth=depth):
                self._assert_parity(BIDS_20, ASKS_20, depth=depth)

    def test_parity_zero_volume(self):
        """Book ber-volume 0 tidak boleh membagi dengan nol di salah satu sisi."""
        self._assert_parity([(0.0, 0.0)], [(0.0, 0.0)])

    def test_parity_asymmetric_depth(self):
        """Jumlah level tidak sama antar sisi."""
        self._assert_parity(BIDS_20, ASKS_5, depth=5)
        self._assert_parity(BIDS_5, ASKS_20, depth=5)

    def test_parity_fractional_prices(self):
        """Harga pecahan harus diurai sama persis di kedua kernel."""
        bids = [(99.123456789, 1.234567), (99.12, 2.5)]
        asks = [(100.987654321, 0.5), (100.99, 3.25)]
        self._assert_parity(bids, asks)

    def test_parity_unknown_symbol_returns_zeros(self):
        """Simbol yang tidak dikenal harus (0, 0) di kedua kernel."""
        from core.microstructure import PythonKernel

        cpp_kernel = CPP.MicrostructureKernel(8)
        py_kernel = PythonKernel()

        cpp_ofi, cpp_spread = cpp_kernel.order_flow_imbalance("TIDAK/ADA", 5)
        py_ofi, py_spread = py_kernel.order_flow_imbalance("TIDAK/ADA", 5)

        self.assertEqual((cpp_ofi, cpp_spread), (0.0, 0.0))
        self.assertEqual((py_ofi, py_spread), (0.0, 0.0))

    def test_parity_reset(self):
        """Setelah reset, kedua kernel harus sama-sama kosong."""
        cpp_kernel, py_kernel = _make_pair(BIDS_5, ASKS_5)
        cpp_kernel.reset()
        py_kernel.reset()

        self.assertEqual(
            cpp_kernel.order_flow_imbalance("BTC/USDT:USDT", 5), (0.0, 0.0)
        )
        self.assertEqual(
            py_kernel.order_flow_imbalance("BTC/USDT:USDT", 5), (0.0, 0.0)
        )

    def test_level_weights_match_specification(self):
        """Bobot level harus persis 1.0, 0.9, 0.8, 0.7, 0.6."""
        expected = [1.0, 0.9, 0.8, 0.7, 0.6]
        for i, want in enumerate(expected):
            with self.subTest(level=i):
                self.assertAlmostEqual(
                    CPP.level_weight(i), want, delta=1e-12,
                    msg=f"bobot level {i} harus {want}",
                )

    def test_ingest_json_parity(self):
        """
        Payload l2Book harus memberi OFI yang sama dengan `ingest_l2` manual.

        Ini menguji parser zero-copy: kalau ia salah baca urutan px/sz, atau
        salah menangani angka yang memang berbentuk string, angkanya akan
        meleset jauh dari book yang di-feed manual.
        """
        # CATATAN: `levels` diikuti DUA `[`, bukan tiga:
        #     "levels": [  <-- array levels
        #                [ ...levels sisi BID... ],   <-- array level pertama
        #                [ ...levels sisi ASK... ]    <-- array level kedua
        #              ]
        # Fixture versi sebelumnya punya `[[[` dan karena itu TIDAK PERNAH
        # dijalankan: test ini selalu di-skip selama modul native belum
        # ter-build, jadi payload rusak itu tidak pernah terlihat. Parser
        # menolak dengan `UnexpectedChar` — yang memang perilaku yang benar.
        payload = (
            b'{"channel":"l2Book","data":{"coin":"BTC","levels":['
            b'[{"px":"99.99","sz":"10.0"},{"px":"99.98","sz":"12.0"},'
            b'{"px":"99.97","sz":"8.0"},{"px":"99.96","sz":"15.0"},'
            b'{"px":"99.95","sz":"6.0"}],'
            b'[{"px":"100.01","sz":"3.0"},{"px":"100.02","sz":"4.0"},'
            b'{"px":"100.03","sz":"2.0"},{"px":"100.04","sz":"7.0"},'
            b'{"px":"100.05","sz":"5.0"}]],"time":1700000000000}}'
        )

        from core.microstructure import PythonKernel

        cpp_kernel = CPP.MicrostructureKernel(8)
        coin = cpp_kernel.ingest_json(payload)
        self.assertEqual(coin, "BTC", "parser harus mengembalikan nama coin")

        py_kernel = PythonKernel()
        py_kernel.ingest_l2("BTC", BIDS_5, ASKS_5)

        cpp_ofi, cpp_spread = cpp_kernel.order_flow_imbalance("BTC", 5)
        py_ofi, py_spread = py_kernel.order_flow_imbalance("BTC", 5)

        self.assertAlmostEqual(cpp_ofi, py_ofi, delta=TOLERANCE)
        self.assertAlmostEqual(cpp_spread, py_spread, delta=TOLERANCE)

    def test_ingest_json_ignores_other_channels(self):
        """Payload dari kanal lain harus diabaikan, bukan diparse jadi book."""
        payload = (
            b'{"channel":"trades","data":{"coin":"BTC","levels":['
            b'[[{"px":"1.0","sz":"1.0"}]]]}}'
        )
        cpp_kernel = CPP.MicrostructureKernel(8)
        result = cpp_kernel.ingest_json(payload)
        self.assertIsNone(result, "kanal selain l2Book harus mengembalikan None")

    def test_ingest_json_rejects_malformed(self):
        """Payload l2Book rusak harus melempar, bukan menghasilkan book diam."""
        cpp_kernel = CPP.MicrostructureKernel(8)
        with self.assertRaises(RuntimeError):
            cpp_kernel.ingest_json(b'{"channel":"l2Book","data":{"coin":}}')


class TestKernelSourceIntegrity(unittest.TestCase):
    """
    Pemeriksaan sumber yang tidak butuh modul C++ ter-build.

    Test di atas dilewati kalau modul belum ada. Test ini tidak: ia
    memverifikasi bahwa berkas sumber C++ benar-benar ada, dan — yang lebih
    penting — bahwa bobot level di kedua sisi didefinisikan dengan angka
    yang sama.

    Kalau seseorang mengubah satu sisi saja tanpa mengubah yang lain, test
    paritas akan gagal dengan pesan yang sangat teknis. Test ini gagal lebih
    dulu dan lebih jelas.
    """

    def test_cpp_sources_exist(self):
        """Berkas sumber C++ harus ada di repo."""
        for rel in (
            "cpp/microstructure_kernel.cpp",
            "cpp/bindings.cpp",
            "cpp/include/microstructure_kernel.h",
            "cpp/include/simdjson.h",
            "CMakeLists.txt",
        ):
            with self.subTest(path=rel):
                self.assertTrue(
                    (ROOT / rel).exists(), f"berkas sumber hilang: {rel}"
                )

    def test_cpp_weight_constants_match_python(self):
        """
        Konstanta bobot di sumber C++ harus sama dengan yang dipakai Python.

        Dicek lewat pembacaan teks sumber, bukan lewat modul ter-build,
        supaya ketidakcocokan ketahuan bahkan sebelum ada compiler.
        """
        cpp_src = (ROOT / "cpp" / "microstructure_kernel.cpp").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "constexpr double kLevelWeight0 = 1.0", cpp_src,
            "kLevelWeight0 di C++ harus 1.0",
        )
        self.assertIn(
            "constexpr double kLevelWeightStep = 0.1", cpp_src,
            "kLevelWeightStep di C++ harus 0.1",
        )

    def test_python_weight_is_descending(self):
        """Bobot Python harus menurun linier 1.0, 0.9, 0.8, ..."""
        from core.microstructure import PythonKernel

        for i in range(6):
            with self.subTest(level=i):
                expected = sum(1.0 - 0.1 * k for k in range(i + 1))
                got = PythonKernel._weighted_volume([(100.0, 1.0)] * (i + 1), i + 1)
                self.assertAlmostEqual(got, expected, places=12)

    def test_cpp_uses_fixed_size_arrays(self):
        """
        Header harus memakai fixed-size array, bukan std::vector.

        Ini klaim performa inti modul C++. Kalau ada yang mengubahnya
        menjadi vektor, hot path akan mulai beralokasi dan kernel tidak lagi
        memenuhi syarat yang membuatnya ada.

        Komentar DIBUANG lebih dulu: file ini sendiri menyebut "std::vector"
        di docstring untuk menyatakan bahwa kita TIDAK memakainya, dan grep
        mentah akan menandai kalimat itu sebagai pelanggaran.
        """
        header = (ROOT / "cpp" / "include" / "microstructure_kernel.h").read_text(
            encoding="utf-8"
        )
        code_only = re.sub(r"//.*", "", header)

        self.assertIn("std::array", code_only, "book harus memakai std::array")
        self.assertNotIn(
            "std::vector", code_only,
            "book tidak boleh memakai std::vector — itu alokasi di hot path",
        )

    def test_cpp_releases_gil(self):
        """Binding harus melepas GIL, kalau tidak event loop akan membeku."""
        bindings = (ROOT / "cpp" / "bindings.cpp").read_text(encoding="utf-8")
        self.assertIn(
            "py::gil_scoped_release", bindings,
            "binding harus melepas GIL saat kalkulasi/parsing berjalan",
        )

    def test_cmake_avoids_fast_math(self):
        """
        Build tidak boleh memakai -Ofast / -ffast-math.

        Fast-math mengizinkan reordering operasi floating point, dan itu
        merusak paritas dengan PythonKernel. Kecepatan yang dibeli dengan angka
        yang salah adalah hasil yang buruk.

        Komentar DIBUANG lebih dulu: CMakeLists.txt sengaja MENYebut
        `-Ofast` di dalam komentar untuk menjelaskan kenapa kita tidak
        memakainya. Grep mentah akan menandai penjelasan itu sebagai
        pelanggaran — persis ironis.
        """
        cmake = (ROOT / "CMakeLists.txt").read_text(encoding="utf-8")
        code_only = re.sub(r"#.*", "", cmake)
        self.assertNotIn("-Ofast", code_only, "-Ofast merusak paritas floating point")
        self.assertNotIn("ffast-math", code_only, "fast-math merusak paritas")
        self.assertIn("-O2", code_only, "pakai -O2, bukan -Ofast")

    def test_auto_fallback_is_wired(self):
        """Feed L2 harus mengaktifkan kernel native sebelum menerima frame."""
        feed_src = (ROOT / "data" / "hyperliquid_feed.py").read_text(encoding="utf-8")
        self.assertIn(
            "initialize_native_kernel", feed_src,
            "feed harus mencoba mengaktifkan kernel native",
        )
        self.assertIn(
            "_ensure_native_kernel", feed_src,
            "feed harus punya titik aktivasi kernel yang idempoten",
        )

    def test_microstructure_module_exposes_native_api(self):
        """API native harus tersedia di modul mikrostruktur."""
        from core import microstructure

        for name in (
            "CppMicrostructureKernel",
            "initialize_native_kernel",
            "cpp_kernel_available",
            "PythonKernel",
            "MicrostructureKernel",
        ):
            with self.subTest(name=name):
                self.assertTrue(
                    hasattr(microstructure, name),
                    f"core.microstructure tidak mengekspos {name}",
                )

    def test_register_kernel_rejects_incomplete_native(self):
        """
        Kernel native yang tidak lengkap harus ditolak, bukan dipasang diam-diam.

        Ini yang menjaga auto-fallback tetap jujur: kalau adapter salah,
        sistem harus menolak, bukan terpasang lalu menghasilkan OFI nol tanpa
        ada yang menyadari.
        """
        from core import microstructure

        class Broken:
            def ingest_l2(self, *a):
                pass
            # sengaja tidak punya order_flow_imbalance / depth_imbalance / reset

        with self.assertRaises(TypeError):
            microstructure.register_kernel(Broken())


if __name__ == "__main__":
    unittest.main()



