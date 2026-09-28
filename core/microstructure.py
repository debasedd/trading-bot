"""
core/microstructure.py — Hot-path kernel mikro-struktur (L2 / OFI / depth).

Modul ini adalah SATU-SATUNYA tempat parsing orderbook L2, kalkulasi Order Flow
Imbalance, dan pelacakan market depth dilakukan. Kenapa dipisah:

* Ini jalur terpanas di sistem. Hyperliquid mengirim 20 level per sisi per
  update, dan setiap update dapat memicu perhitungan OFI.
* Hitungannya wajib bebas alokasi dan bebas I/O supaya bisa dipindah ke
  C++/Rust (pybind11 atau CFFI) tanpa mengubah satu baris pun di agent.
* Pemisahan membuat batas swap-paketnya eksplisit: agent hanya mengenal
  antarmuka di bawah, tidak pernah tahu implementasinya Python atau native.

Kontrak untuk implementasi native::

    class NativeKernel:
        def ingest_l2(self, symbol, bids, asks) -> None: ...
        def order_flow_imbalance(self, symbol, depth=5) -> tuple[float, float]: ...
        def depth_imbalance(self, symbol, depth=5) -> float: ...
        def reset(self, symbol=None) -> None: ...

Cara mengaktifkannya — sekali saat boot, sebelum WebSocket mulai menerima::

    from core.microstructure import register_kernel
    import my_native_ext
    register_kernel(NativeKernelAdapter(my_native_ext))

Setelah didaftarkan, seluruh agent, dashboard, dan test yang sudah ada memakai
implementasi native tanpa perubahan kode sama sekali. Kalau tidak ada yang
mendaftarkan, `PythonKernel` (murni) yang dipakai dan sistem tetap jalan persis
seperti sekarang.

Implementasi native bawaan
--------------------------
Modul C++20 `cpp_microstructure` (lihat `cpp/` dan `CMakeLists.txt`) sudah
disediakan di repo ini. Build-nya:

    cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
    cmake --build build --config Release

Kalau modul itu berhasil di-build, `initialize_native_kernel()` mendaftarkannya
secara otomatis. Kalau tidak — belum di-build, compiler tidak ada, ABI tidak
cocok — sistem **tidak gagal**: ia tetap memakai `PythonKernel` dengan hasil
yang identik. Perbedaannya hanya kecepatan, bukan kebenaran — dan itulah yang
dijaga oleh `tests/test_cpp_kernel.py`.
"""

import os
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Tuple

from core.logger import get_logger

logger = get_logger("microstructure")


class MicrostructureKernel(ABC):
    """
    Antarmuka untuk seluruh perhitungan mikro-struktur.

    Semua metode WAJIB bebas I/O dan bebas alokasi besar. Implementasi native
    boleh menyimpan state internal, tapi harus aman terhadap pergantian simbol
    dan harus di-reset lewat `reset()`.
    """

    @abstractmethod
    def ingest_l2(self, symbol: str, bids: list, asks: list) -> None:
        """Terima snapshot orderbook L2 penuh (bukan delta)."""

    @abstractmethod
    def order_flow_imbalance(
        self, symbol: str, depth: int = 5
    ) -> Tuple[float, float]:
        """Kembalikan `(ofi, relative_spread)`, ofi di [-1, 1]."""

    @abstractmethod
    def depth_imbalance(self, symbol: str, depth: int = 5) -> float:
        """Selisih volume bid vs ask pada N level teratas, di [-1, 1]."""


class PythonKernel(MicrostructureKernel):
    """
    Implementasi murni Python. Dipakai sebagai default produksi sekaligus
    menjadi *reference* yang harus selalu menghasilkan nilai identik dengan
    implementasi native — itulah yang membuat test parity bermakna.

    Dua keputusan yang membuat hasilnya tidak naif:

    1. **Volume tertimbang per level, bukan jumlah mentah.** Jumlah mentah
       bias ke level terjauh: pada L2 20 level, level ke-20 biasanya memegang
       volume paling besar dan justru merepresentasikan order book yang
       tersusun, bukan intent pelaku pasar. Intent tercermin di dekat best
       bid/ask. Bobot linier menurun (1.0, 0.9, 0.8, …) memberi bobot lebih
       pada level dekat tanpa membuang informasi yang lebih jauh.

    2. **Spread relatif, bukan absolut.** OFI dalam satuan harga tidak bisa
       dibandingkan antar-simbol: spread 0.10 pada BTC jauh lebih kecil
       secara informasi daripada spread 0.10 pada DOGE.

    Pembagian dengan nol dijaga di setiap titik — orderbook yang memuat harga 0
    (sentinel batas bursa) harus menghasilkan 0.0, bukan `ZeroDivisionError`
    yang mematikan seluruh pipeline.
    """

    def __init__(self, max_symbols: int = 64):
        self._books: Dict[str, Tuple[list, list]] = {}
        self._max_symbols = max_symbols

    def ingest_l2(self, symbol: str, bids: list, asks: list) -> None:
        # Batasi jumlah simbol dilacak. Pada top-10 volume normal ini tidak
        # akan pernah penuh, tapi feed yang salah konfigurasi (mengirim semua
        # koin bursa) tidak boleh membuat state tumbuh tanpa batas.
        if len(self._books) >= self._max_symbols and symbol not in self._books:
            return
        # Normalisasi ke float sekali di sini supaya jalur hitung di bawah
        # tidak melakukan casting berulang.
        self._books[symbol] = (
            [(float(p), float(s)) for p, s in bids],
            [(float(p), float(s)) for p, s in asks],
        )

    @staticmethod
    def _weighted_volume(levels: list, depth: int) -> float:
        """Volume tertimbang dengan bobot linier menurun per level."""
        total = 0.0
        for i, (_, size) in enumerate(levels[:depth]):
            total += size * (1.0 - 0.1 * i)
        return total

    def order_flow_imbalance(
        self, symbol: str, depth: int = 5
    ) -> Tuple[float, float]:
        book = self._books.get(symbol)
        if not book:
            return 0.0, 0.0

        bids, asks = book
        if not bids or not asks:
            return 0.0, 0.0

        bid_vol = self._weighted_volume(bids, depth)
        ask_vol = self._weighted_volume(asks, depth)

        denom = bid_vol + ask_vol
        if denom <= 0.0:
            return 0.0, 0.0

        ofi = (bid_vol - ask_vol) / denom
        # Pagar terhadap drift float yang bisa lolos [-1, 1] sedikit tipis.
        ofi = max(-1.0, min(1.0, ofi))

        best_bid, best_ask = bids[0][0], asks[0][0]
        mid = (best_bid + best_ask) / 2.0
        if mid <= 0.0:
            return ofi, 0.0

        rel_spread = max(best_ask - best_bid, 0.0) / mid
        return ofi, rel_spread

    def depth_imbalance(self, symbol: str, depth: int = 5) -> float:
        book = self._books.get(symbol)
        if not book:
            return 0.0
        bids, asks = book
        bid_vol = self._weighted_volume(bids, depth)
        ask_vol = self._weighted_volume(asks, depth)
        denom = bid_vol + ask_vol
        if denom <= 0.0:
            return 0.0
        return max(-1.0, min(1.0, (bid_vol - ask_vol) / denom))

    def reset(self, symbol: Optional[str] = None) -> None:
        if symbol is None:
            self._books.clear()
        else:
            self._books.pop(symbol, None)


# Kernel aktif untuk seluruh proses. Satu instance dibagi antar thread; ini
# satu-satunya mutable state di hot path, jadi implementasi native WAJIB
# thread-safe atau memakai pengunci sendiri.
_KERNEL: MicrostructureKernel = PythonKernel()


def get_kernel() -> MicrostructureKernel:
    """Kembalikan kernel mikrostruktur yang aktif."""
    return _KERNEL


def register_kernel(kernel: MicrostructureKernel) -> None:
    """
    Ganti kernel aktif dengan implementasi lain (mis. ekstensi C++).

    Validasi dilakukan lebih dulu: kalau objek yang diberikan tidak memenuhi
    antarmuka, kernel lama tetap dipakai. Menolak di sini jauh lebih baik
    daripada gagal saat feed pertama kali datang — pada saat itu log akan
    menyalahkan koneksi bursa, padahal penyebabnya registrasi.
    """
    global _KERNEL

    required = (
        "ingest_l2",
        "order_flow_imbalance",
        "depth_imbalance",
        "reset",
    )
    missing = [m for m in required if not callable(getattr(kernel, m, None))]
    if missing:
        raise TypeError(f"Kernel mikrostruktur tidak lengkap, method hilang: {missing}")

    previous = _KERNEL.__class__.__name__
    _KERNEL = kernel
    logger.info(
        f"Kernel mikrostruktur diganti: {previous} -> {kernel.__class__.__name__}"
    )


def ingest_l2(symbol: str, bids: list, asks: list) -> None:
    """Kirim snapshot L2 ke kernel aktif."""
    _KERNEL.ingest_l2(symbol, bids, asks)


def order_flow_imbalance(
    symbol: str, depth: int = 5
) -> Tuple[float, float]:
    """Ambil `(ofi, relative_spread)` dari kernel aktif."""
    return _KERNEL.order_flow_imbalance(symbol, depth)


def depth_imbalance(symbol: str, depth: int = 5) -> float:
    """Ambil depth imbalance dari kernel aktif."""
    return _KERNEL.depth_imbalance(symbol, depth)


def reset_microstructure(symbol: Optional[str] = None) -> None:
    """Bersihkan kernel aktif."""
    _KERNEL.reset(symbol)


# ===========================================================================
# Integrasi C++20 bawaan
# ===========================================================================


def cpp_kernel_available() -> bool:
    """
    True bila modul `cpp_microstructure` bisa di-import.

    Sengaja berupa fungsi, bukan konstanta module-level: keputusan ini
    diambil saat boot, dan test sering memeriksa ketersediaan setelah
    mencoba build di tengah sesi.
    """
    try:
        import cpp_microstructure  # noqa: F401
    except Exception:
        return False
    return True


class CppMicrostructureKernel(MicrostructureKernel):
    """
    Adapter untuk modul C++20 `cpp_microstructure`.

    Adapter ini tidak melakukan kalkulasi apa pun — dia hanya menerjemahkan
    kontrak Python (`ingest_l2(symbol, bids, asks)`) ke API native. Seluruh
    perhitungan terjadi di C++; kelas ini hanya memanggilnya.

    Yang dijanjikan:
      * NOL alokasi string di lapisan ini. Nama simbol diteruskan apa adanya;
        modul C++ menerima `const char*` + panjang.
      * OFI dipanggil dengan GIL yang sudah dilepas oleh binding pybind11,
        jadi loop asyncio tidak terblokir selama kalkulasi.
      * Paritas dengan `PythonKernel` dijaga oleh `tests/test_cpp_kernel.py`.
    """

    def __init__(self, max_symbols: int = 64, native_module=None):
        import cpp_microstructure as native
        self._native = native_module or native
        self._impl = self._native.MicrostructureKernel(max_symbols)

    def ingest_l2(self, symbol: str, bids: list, asks: list) -> None:
        self._impl.ingest_l2(symbol, bids, asks)

    def order_flow_imbalance(
        self, symbol: str, depth: int = 5
    ) -> Tuple[float, float]:
        ofi, spread = self._impl.order_flow_imbalance(symbol, depth)
        return float(ofi), float(spread)

    def depth_imbalance(self, symbol: str, depth: int = 5) -> float:
        return float(self._impl.depth_imbalance(symbol, depth))

    def reset(self, symbol: Optional[str] = None) -> None:
        self._impl.reset(symbol)

    def ingest_json(self, payload: bytes):
        """
        Parse payload l2Book langsung di C++, tanpa membuat dict Python.

        Mengembalikan nama coin, atau None bila payload bukan kanal l2Book.
        Melempar exception bila payload l2Book rusak.
        """
        return self._impl.ingest_json(payload)

    def symbol_count(self) -> int:
        return int(self._impl.symbol_count())


def initialize_native_kernel(force: bool = False) -> bool:
    """
    Coba aktifkan kernel C++ bawaan bila tersedia.

    Args:
        force: True untuk mendaftarkan kernel C++ walau user sebelumnya
            memilih Python (digunakan oleh test).

    Returns:
        True bila kernel C++ sekarang aktif, False bila PythonKernel yang
        dipakai (karena modul belum di-build, atau karena user memilih
        Python lewat env var).

    Env var:
        TRADEBOT_KERNEL=python  → paksa Python, jangan sentuh native.
        TRADEBOT_KERNEL=cpp    → paksa native; kalau tidak tersedia, error
                                eksplisit (jauh lebih baik daripada diam).
    """
    import os

    choice = os.environ.get("TRADEBOT_KERNEL", "").strip().lower()

    if choice == "python":
        if not force:
            logger.info("TRADEBOT_KERNEL=python — memakai PythonKernel")
            return False
        # force=True tetap mengabaikan env, supaya test bisa membandingkan.
    elif choice == "cpp" and not force:
        try:
            import cpp_microstructure  # noqa: F401
        except Exception as exc:
            raise RuntimeError(
                "TRADEBOT_KERNEL=cpp diminta, tapi modul cpp_microstructure "
                f"tidak bisa di-import: {exc}. Build dulu dengan:\n"
                "  cmake -S . -B build -DCMAKE_BUILD_TYPE=Release\n"
                "  cmake --build build --config Release"
            ) from exc

    try:
        import cpp_microstructure as native
        kernel = CppMicrostructureKernel(native_module=native)
        register_kernel(kernel)
        logger.info(
            "Kernel mikrostruktur C++20 aktif "
            f"(versi {getattr(native, '__version__', '?')}, "
            f"MAX_LEVELS={getattr(native, 'MAX_LEVELS', '?')})"
        )
        return True
    except Exception as exc:
        if choice == "cpp" and not force:
            # Diminta eksplisit tapi gagal: diam-diam jatuh ke Python akan
            # menyembunyikan masalah build dari operator.
            raise RuntimeError(
                f"Gagal mengaktifkan kernel C++: {exc}"
            ) from exc
        logger.info(
            f"Kernel C++ tidak aktif ({type(exc).__name__}: {exc}) — "
            "memakai PythonKernel. Hasilnya identik, hanya lebih lambat. "
            "Build dengan: cmake -S . -B build && cmake --build build"
        )
        return False
