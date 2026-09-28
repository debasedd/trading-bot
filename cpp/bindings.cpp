// ===========================================================================
//  bindings.cpp — Jembatan pybind11 untuk kernel mikrostruktur
// ===========================================================================
//  Prinsip yang dipegang di sini:
//
//  1. Lepas GIL (`py::gil_scoped_release`) di setiap perhitungan dan parsing.
//     Tanpa itu, thread Python lain membeku selama feed L2 mem-parsing ribuan
//     pesan per detik — dan urutan acquiring GIL di asyncio justru bertolak
//     belakang dengan urutan kernel dieksekusi, sehingga hasil bisa BERBEDA
//     run-to-run. Ini bukan hanya soal kecepatan.
//
//  2. Iterasi list Python WAJIB dilakukan dengan GIL dipegang. Menyentuh
//     objek Python tanpa GIL adalah crash, bukan sekadar bug.
//
//  3. Modul ini tidak pernah menyentuh database, config, atau logger. Kernel
//     harus bisa diuji berdiri sendiri.
// ===========================================================================

#include "microstructure_kernel.h"

#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <cstring>
#include <stdexcept>

namespace py = pybind11;

namespace {

// Isi `Book` dari list of (px, sz) milik Python. GIL WAJIB dipegang.
void book_from_python(const py::object& bids, const py::object& asks,
                      tradebot::Book& book) noexcept {
    book.clear();

    for (const auto& item : bids) {
        const auto pair = item.cast<std::pair<double, double>>();
        book.ingest_bid(pair.first, pair.second);
    }
    for (const auto& item : asks) {
        const auto pair = item.cast<std::pair<double, double>>();
        book.ingest_ask(pair.first, pair.second);
    }
}

}  // namespace

PYBIND11_MODULE(cpp_microstructure, m) {
    m.doc() =
        "Kernel mikrostruktur L2 native (C++20). Padanan persis PythonKernel "
        "di core/microstructure.py; dijaga oleh tests/test_cpp_kernel.py.";

    py::class_<tradebot::MicrostructureKernel>(m, "MicrostructureKernel")
        .def(py::init<std::size_t>(), py::arg("max_symbols") = 64,
             "Kernel mikrostruktur. `max_symbols` membatasi jumlah simbol yang "
             "dilacak; feed yang salah konfigurasi tidak boleh membuat state "
             "tumbuh tanpa batas.")

        .def(
            "order_flow_imbalance",
            [](const tradebot::MicrostructureKernel& self, const std::string& symbol,
               std::uint32_t depth) {
                py::gil_scoped_release release;
                return self.order_flow_imbalance(symbol.c_str(), symbol.size(),
                                                 depth);
            },
            py::arg("symbol"), py::arg("depth") = 5,
            "Kembalikan (ofi, relative_spread). Lepas GIL selama kalkulasi.")

        .def(
            "depth_imbalance",
            [](const tradebot::MicrostructureKernel& self, const std::string& symbol,
               std::uint32_t depth) {
                py::gil_scoped_release release;
                return self.depth_imbalance(symbol.c_str(), symbol.size(), depth);
            },
            py::arg("symbol"), py::arg("depth") = 5,
            "Depth imbalance pada N level teratas. Lepas GIL.")

        .def(
            "ingest_l2",
            [](tradebot::MicrostructureKernel& self, const std::string& symbol,
               const py::object& bids, const py::object& asks) {
                // GIL dipegang: book_from_python menyentuh list Python.
                tradebot::Book book;
                book_from_python(bids, asks, book);
                if (!self.reserve_symbol(symbol.c_str(), symbol.size())) {
                    throw std::runtime_error(
                        "store mikrostruktur penuh: jumlah simbol melebihi batas");
                }
                self.ingest_book(symbol.c_str(), symbol.size(), book);
            },
            py::arg("symbol"), py::arg("bids"), py::arg("asks"),
            "Isi book dari list of (px, sz).")

        .def(
            "ingest_json",
            [](tradebot::MicrostructureKernel& self, py::bytes payload) -> py::object {
                char* data = nullptr;
                Py_ssize_t len = 0;
                if (PyBytes_AsStringAndSize(payload.ptr(), &data, &len) != 0) {
                    throw py::error_already_set();
                }

                char symbol_out[64];
                char error_out[128];
                std::memset(symbol_out, 0, sizeof(symbol_out));
                std::memset(error_out, 0, sizeof(error_out));

                bool ok = false;
                {
                    // Nol salinan: `data` menunjuk langsung ke buffer py::bytes.
                    py::gil_scoped_release release;
                    ok = self.ingest_json(data, static_cast<std::size_t>(len),
                                          symbol_out, sizeof(symbol_out),
                                          error_out, sizeof(error_out));
                }

                if (!ok) {
                    // error_out kosong = payload dari kanal lain, bukan error.
                    if (error_out[0] == '\0') {
                        return py::none();
                    }
                    throw std::runtime_error(error_out);
                }
                // `py::str` (bukan `std::string`) karena nilai balik lambda
                // sudah `py::object`; konversi dari `std::string` ke
                // `py::object` tidak eksplisit dan tidak bisa dideduksi.
                return py::str(symbol_out);
            },
            py::arg("payload"),
            "Parse payload l2Book Hyperliquid. Zero-copy, lepas GIL.\n"
            "Mengembalikan nama coin, atau None bila payload bukan kanal\n"
            "l2Book. Melempar exception bila payload l2Book rusak.")

        .def(
            "reset",
            [](tradebot::MicrostructureKernel& self, const py::object& symbol) {
                const bool is_none = symbol.is_none();
                std::string holder;
                const char* ptr = nullptr;
                if (!is_none) {
                    holder = symbol.cast<std::string>();
                    ptr = holder.c_str();
                }
                py::gil_scoped_release release;
                self.reset(ptr);   // ptr == nullptr berarti reset semua
            },
            py::arg("symbol") = py::none(),
            "Kosongkan kernel. symbol=None menghapus semua.")

        .def(
            "symbol_count",
            [](const tradebot::MicrostructureKernel& self) {
                py::gil_scoped_release release;
                return self.symbol_count();
            },
            "Jumlah simbol yang dilacak.");

    m.def("level_weight", &tradebot::level_weight, py::arg("index"),
          "Bobot level ke-i: 1.0, 0.9, 0.8, 0.7, 0.6, ...");
    m.attr("MAX_LEVELS") = static_cast<int>(tradebot::kMaxLevels);
    m.attr("__version__") = "1.0.0";
}
