// ===========================================================================
//  microstructure_kernel.h — C++20 hot-path kernel untuk L2 / OFI / depth
// ===========================================================================
//  Deklarasi header-only untuk Book, BookStore, dan MicrostructureKernel.
//
//  Semua struktur memakai fixed-size array. Tidak ada `std::vector`, tidak ada
//  `std::string` di jalur hitung. Satu-satunya alokasi yang mungkin terjadi
//  adalah saat `std::unordered_map` tumbuh untuk simbol BARU — yang terjadi
//  beberapa kali seumur proses, bukan per frame.
//
//  Kontrak paritas dengan `PythonKernel` dijaga ketat oleh
//  `tests/test_cpp_kernel.py`.
// ===========================================================================

#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <mutex>
#include <string>
#include <unordered_map>
#include <utility>

namespace tradebot {

// Kapasitas level per sisi. Hyperliquid mengirim 20; kita sisakan 32 supaya
// ada ruang untuk bursa yang menambah level tanpa mengubah kode.
inline constexpr std::uint32_t kMaxLevels = 32;

// ---------------------------------------------------------------------------
// Book — satu orderbook L2 dalam bentuk fixed-size.
// ---------------------------------------------------------------------------
class Book {
public:
    Book() noexcept;

    void clear() noexcept;

    void ingest_bid(double px, double sz) noexcept;
    void ingest_ask(double px, double sz) noexcept;

    std::uint32_t n_bid() const noexcept { return n_bid_; }
    std::uint32_t n_ask() const noexcept { return n_ask_; }

    double bid_price(std::uint32_t i) const noexcept { return bid_px_[i]; }
    double bid_size(std::uint32_t i) const noexcept { return bid_sz_[i]; }
    double ask_price(std::uint32_t i) const noexcept { return ask_px_[i]; }
    double ask_size(std::uint32_t i) const noexcept { return ask_sz_[i]; }

    bool overflow_bid() const noexcept { return overflow_bid_; }
    bool overflow_ask() const noexcept { return overflow_ask_; }

private:
    std::array<double, kMaxLevels> bid_px_;
    std::array<double, kMaxLevels> bid_sz_;
    std::array<double, kMaxLevels> ask_px_;
    std::array<double, kMaxLevels> ask_sz_;
    std::uint32_t n_bid_;
    std::uint32_t n_ask_;
    bool overflow_bid_;
    bool overflow_ask_;
};

// ---------------------------------------------------------------------------
// BookStore — peta simbol ke Book.
//
//  Mutex hanya melindungi registrasi & lookup pointer. Penulisan isi book
//  sendiri dilakukan pemanggil pada struct yang sudah ditemukan, tanpa lock
//  tambahan — lapisan Python menjamin satu book hanya disentuh satu thread
//  pada satu waktu (GIL), jadi lock di sini cukup untuk menjaga integritas
//  map itu sendiri.
//
//  CATATAN DEPENDENSI: `std::mutex` menarik `libwinpthread-1.dll` lewat
//  libstdc++, dan DLL itu butuh `api-ms-win-crt-private-l1-1-0.dll` yang hanya
//  ada di `C:\Windows\System32\downlevel\`. Keduanya harus diletakkan di
//  folder yang sama dengan `.pyd` — lihat catatan di `CMakeLists.txt`.
//  Sudah dicoba replaces `std::mutex` dengan spinlock header-only supaya
//  dependensi hilang, tapi `libstdc++.a` tetap menarik `libwinpthread` sendiri,
//  jadi percobaan itu tidak menyelesaikan masalah dan hanya menambah
//  kompleksitas. Spinlock baru sepadan kalau toolchain-nya MSVC atau MinGW
//  varian msvcrt.
// ---------------------------------------------------------------------------
class BookStore {
public:
    explicit BookStore(std::size_t max_symbols) noexcept;
    ~BookStore();

    BookStore(const BookStore&) = delete;
    BookStore& operator=(const BookStore&) = delete;

    // Tambah simbol bila belum ada. False bila kapasitas penuh.
    bool reserve_symbol(const char* symbol, std::size_t len) noexcept;

    // Pointer ke book, atau nullptr bila simbol tidak dikenal.
    const Book* find(const char* symbol) const noexcept;

    void set_book(const char* symbol, std::size_t len, const Book& book) noexcept;

    void reset(const char* symbol) noexcept;
    void reset_all() noexcept;

    std::size_t size() const noexcept;
    std::size_t max_symbols() const noexcept { return max_symbols_; }

private:
    mutable std::mutex mutex_;
    std::unordered_map<std::string, Book> books_;
    std::size_t max_symbols_;
};

// ---------------------------------------------------------------------------
// MicrostructureKernel — antarmuka hitung, padanan persis PythonKernel.
//
//  Seluruh perhitungan TIDAK melakukan alokasi. Argumen `symbol` adalah
//  pointer + panjang, bukan `std::string`, supaya pemanggil di Python tidak
//  perlu menyalin nama simbol untuk setiap panggilan.
// ---------------------------------------------------------------------------
class MicrostructureKernel {
public:
    explicit MicrostructureKernel(std::size_t max_symbols = 64) noexcept;
    ~MicrostructureKernel();

    MicrostructureKernel(const MicrostructureKernel&) = delete;
    MicrostructureKernel& operator=(const MicrostructureKernel&) = delete;

    // OFI + relative spread. `depth` = berapa level teratas yang dipakai.
    std::pair<double, double>
    order_flow_imbalance(const char* symbol, std::size_t len,
                         std::uint32_t depth = 5) const noexcept;

    double depth_imbalance(const char* symbol, std::size_t len,
                           std::uint32_t depth = 5) const noexcept;

    // Parsing payload l2Book Hyperliquid langsung ke dalam store.
    // Mengembalikan true bila parsing sukses DAN book tersimpan.
    //
    // `json_error` (bila tidak null) diisi dengan teks status saat gagal —
    // pemanggil DIWAJIBKAN memeriksa nilai balik, bukan mengabaikannya,
    // karena payload yang gagal parse TIDAK boleh menghasilkan angka OFI
    // basi dari book lama.
    bool ingest_json(const char* payload, std::size_t len,
                     char* symbol_out, std::size_t symbol_out_cap,
                     char* json_error, std::size_t json_error_cap) noexcept;

    bool reserve_symbol(const char* symbol, std::size_t len) noexcept;

    // Timpa book yang sudah terisi. Pemanggil WAJIB memanggil
    // `reserve_symbol` lebih dulu; kalau simbol belum dikenal, store penuh,
    // atau apa pun, operasi ini diam-diam tidak mengubah apa pun.
    void ingest_book(const char* symbol, std::size_t len,
                     const Book& book) noexcept;

    void reset(const char* symbol) noexcept;   // nullptr = reset semua

    std::size_t symbol_count() const noexcept;

    // Helper statis untuk testing dan reuse: hitung OFI dari Book yang sudah
    // terisi, tanpa menyentuh store.
    static std::pair<double, double> book_ofi(const Book& book,
                                              std::uint32_t depth) noexcept;
    static double book_depth(const Book& book, std::uint32_t depth) noexcept;

private:
    BookStore store_;
};

// Helper bobot level — diekspos supaya test C++ bisa memverifikasi bahwa
// bobotnya benar-benar 1.0, 0.9, 0.8, 0.7, 0.6 (dan tidak diam-diam berubah).
double level_weight(std::uint32_t index) noexcept;

}  // namespace tradebot
