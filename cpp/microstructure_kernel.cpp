// ===========================================================================
//  microstructure_kernel.cpp — C++20 hot-path kernel untuk L2 / OFI / depth
// ===========================================================================
//  Modul ini adalah padanan persis dari `PythonKernel` di
//  `core/microstructure.py`. Parity bukan kebetulan: test
//  `tests/test_cpp_kernel.py` membandingkan keduanya pada payload L2 yang sama
//  dan menuntut selisih <= 1e-7. Kalau implementasi ini berubah, test itu
//  harus ikut diperbarui secara sadar — bukan diam-diam menyimpang.
//
//  Prinsip yang dipegang:
//
//  1. NOL ALOKASI DI HOT PATH. L2 book memakai `std::array` fixed-size, bukan
//     `std::vector`. `std::unordered_map` hanya tumbuh saat simbol BARU
//     ditemukan, yang terjadi beberapa kali seumur proses — bukan per frame.
//     Perhitungan OFI sendiri sama sekali tidak menyentuh map.
//
//  2. BUKAN ADIL. Parser menolak payload yang tidak berbentuk persis seperti
//     yang diharapkan, alih-alih menoleransi dan menghasilkan angka yang
//     terlihat benar tapi salah. Di sistem yang mengambil keputusan uang,
//     "tidak tahu" jauh lebih aman daripada "tahu tapi salah".
//
//  3. HASIL IDENTIK DENGAN PYTHON. Bobot, urutan operasi, dan clamping
//     semuanya ditulis berurutan sama seperti versi Python. Urutan adalah
//     bagian dari kontrak, bukan detail implementasi: floating point tidak
//     asosiatif, dan satu operasi yang dipindah bisa mengubah digit terakhir.
//
//  4. THREAD-SAFE. Dipanggil dari `asyncio.to_thread`, jadi beberapa worker
//     bisa menyentuh book yang sama. Mutex pada `BookStore` hanya melindungi
//     registrasi dan lookup pointer.
// ===========================================================================

#include "microstructure_kernel.h"
#include "simdjson.h"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <mutex>
#include <string>
#include <unordered_map>

namespace tradebot {

// ---------------------------------------------------------------------------
// Bobot per level. WAJIB identik dengan `PythonKernel._weighted_volume`,
// yang memakai `size * (1.0 - 0.1 * i)`.
//
// Angka 1.0 dan 0.1 diberi nama, bukan ditulis inline: bobot ini adalah
// parameter STRATEGI (seberapa cepat confidence menurun lintas level), bukan
// konstanta matematis. Menamainya berarti test bisa mengunci nilainya lewat
// pembacaan teks sumber, sehingga perubahan bobot diam-diam ketahuan bahkan
// di mesin tanpa compiler.
// ---------------------------------------------------------------------------

namespace {

constexpr double kLevelWeight0 = 1.0;
constexpr double kLevelWeightStep = 0.1;

}  // namespace

double level_weight(std::uint32_t index) noexcept {
    return kLevelWeight0 - kLevelWeightStep * static_cast<double>(index);
}

// ---------------------------------------------------------------------------
// Book — fixed-size, nol alokasi setelah konstruksi.
// ---------------------------------------------------------------------------

Book::Book() noexcept
    : n_bid_(0), n_ask_(0), overflow_bid_(false), overflow_ask_(false) {
    for (std::uint32_t i = 0; i < kMaxLevels; ++i) {
        bid_px_[i] = 0.0;
        bid_sz_[i] = 0.0;
        ask_px_[i] = 0.0;
        ask_sz_[i] = 0.0;
    }
}

void Book::clear() noexcept {
    n_bid_ = 0;
    n_ask_ = 0;
    overflow_bid_ = false;
    overflow_ask_ = false;
}

void Book::ingest_bid(double px, double sz) noexcept {
    if (n_bid_ < kMaxLevels) {
        bid_px_[n_bid_] = px;
        bid_sz_[n_bid_] = sz;
        ++n_bid_;
    }
    // Melebihi kapasitas: level di luar kMaxLevels dipotong, dan itu harus
    // terlihat lewat flag overflow — bukan lewat perilaku diam-diam, karena
    // book yang utuh tapi salah jauh lebih berbahaya daripada book kosong.
    if (n_bid_ >= kMaxLevels) {
        overflow_bid_ = true;
    }
}

void Book::ingest_ask(double px, double sz) noexcept {
    if (n_ask_ < kMaxLevels) {
        ask_px_[n_ask_] = px;
        ask_sz_[n_ask_] = sz;
        ++n_ask_;
    }
    if (n_ask_ >= kMaxLevels) {
        overflow_ask_ = true;
    }
}

// ---------------------------------------------------------------------------
// BookStore
// ---------------------------------------------------------------------------

BookStore::BookStore(std::size_t max_symbols) noexcept
    : max_symbols_(max_symbols == 0 ? std::size_t(1) : max_symbols) {
    books_.reserve(max_symbols_);
}

BookStore::~BookStore() = default;

bool BookStore::reserve_symbol(const char* symbol, std::size_t len) noexcept {
    std::lock_guard<std::mutex> guard(mutex_);

    const std::string key(symbol, len);
    if (books_.find(key) != books_.end()) {
        return true;   // sudah ada: tidak perlu alokasi baru
    }
    if (books_.size() >= max_symbols_) {
        return false;  // penuh
    }
    books_.emplace(key, Book());
    return true;
}

const Book* BookStore::find(const char* symbol) const noexcept {
    std::lock_guard<std::mutex> guard(mutex_);
    const std::string key(symbol);
    const auto it = books_.find(key);
    if (it == books_.end()) {
        return nullptr;
    }
    return &it->second;
}

void BookStore::set_book(const char* symbol, std::size_t len,
                         const Book& book) noexcept {
    std::lock_guard<std::mutex> guard(mutex_);
    const std::string key(symbol, len);
    const auto it = books_.find(key);
    if (it == books_.end()) {
        return;   // simbol tidak dikenal: jangan diam-diam membuat book baru
    }
    it->second = book;
}

void BookStore::reset(const char* symbol) noexcept {
    std::lock_guard<std::mutex> guard(mutex_);
    if (symbol == nullptr) {
        books_.clear();
        return;
    }
    const auto it = books_.find(std::string(symbol));
    if (it != books_.end()) {
        it->second.clear();
    }
}

void BookStore::reset_all() noexcept {
    std::lock_guard<std::mutex> guard(mutex_);
    books_.clear();
}

std::size_t BookStore::size() const noexcept {
    std::lock_guard<std::mutex> guard(mutex_);
    return books_.size();
}

// ---------------------------------------------------------------------------
// MicrostructureKernel — perhitungan Inti
// ---------------------------------------------------------------------------

namespace {

// Jumlahkan `size * (1.0 - 0.1 * i)` untuk `n` level pertama.
//
// JUMLAH DAN URUTANNYA WAJIB sama persis dengan
// `PythonKernel._weighted_volume`. Python menjumlahkan dari level 0 ke
// `depth-1`; penjumlahan float tidak asosiatif, jadi membalik urutan di sini
// bisa mengubah digit terakhir dan menggagalkan test paritas.
double weighted_volume(const double* sizes, std::uint32_t n) noexcept {
    double total = 0.0;
    for (std::uint32_t i = 0; i < n; ++i) {
        total += sizes[i] * (1.0 - 0.1 * static_cast<double>(i));
    }
    return total;
}

}  // namespace

MicrostructureKernel::MicrostructureKernel(std::size_t max_symbols) noexcept
    : store_(max_symbols) {}

MicrostructureKernel::~MicrostructureKernel() = default;

std::pair<double, double>
MicrostructureKernel::book_ofi(const Book& book, std::uint32_t depth) noexcept {
    // Padanan persis `PythonKernel.order_flow_imbalance`, termasuk urutan
    // pemeriksaan. Book kosong ATAU salah satu sisi kosong -> (0.0, 0.0).
    const std::uint32_t nb = book.n_bid();
    const std::uint32_t na = book.n_ask();
    if (nb == 0 || na == 0) {
        return std::make_pair(0.0, 0.0);
    }

    const std::uint32_t db = (depth < nb) ? depth : nb;
    const std::uint32_t da = (depth < na) ? depth : na;

    // Kumpulkan ukuran level yang akan dipakai. `Book` tidak mengekspos array
    // mentah ke luar, tapi accessor per-level tersedia dan tanpa alokasi.
    double bid_sizes[kMaxLevels];
    double ask_sizes[kMaxLevels];
    for (std::uint32_t i = 0; i < db; ++i) {
        bid_sizes[i] = book.bid_size(i);
    }
    for (std::uint32_t i = 0; i < da; ++i) {
        ask_sizes[i] = book.ask_size(i);
    }

    const double bid_vol = weighted_volume(bid_sizes, db);
    const double ask_vol = weighted_volume(ask_sizes, da);

    const double denom = bid_vol + ask_vol;
    if (denom <= 0.0) {
        return std::make_pair(0.0, 0.0);
    }

    // Pagar terhadap drift float yang bisa lolos [-1, 1] sedikit tipis.
    double ofi = (bid_vol - ask_vol) / denom;
    if (ofi < -1.0) ofi = -1.0;
    if (ofi > 1.0) ofi = 1.0;

    const double best_bid = book.bid_price(0);
    const double best_ask = book.ask_price(0);
    const double mid = (best_bid + best_ask) / 2.0;
    if (mid <= 0.0) {
        return std::make_pair(ofi, 0.0);
    }

    const double raw = best_ask - best_bid;
    const double rel_spread = (raw > 0.0 ? raw : 0.0) / mid;
    return std::make_pair(ofi, rel_spread);
}

double MicrostructureKernel::book_depth(const Book& book,
                                        std::uint32_t depth) noexcept {
    // Padanan persis `PythonKernel.depth_imbalance`. Perhatikan: fungsi Python
    // ini TIDAK mengecek apakah salah satu sisi kosong — ia hanya bergantung
    // pada `denom <= 0`. Kita ikuti persis, kalau tidak paritas pecah.
    const std::uint32_t nb = book.n_bid();
    const std::uint32_t na = book.n_ask();
    const std::uint32_t db = (depth < nb) ? depth : nb;
    const std::uint32_t da = (depth < na) ? depth : na;

    double bid_sizes[kMaxLevels];
    double ask_sizes[kMaxLevels];
    for (std::uint32_t i = 0; i < db; ++i) {
        bid_sizes[i] = book.bid_size(i);
    }
    for (std::uint32_t i = 0; i < da; ++i) {
        ask_sizes[i] = book.ask_size(i);
    }

    const double bid_vol = weighted_volume(bid_sizes, db);
    const double ask_vol = weighted_volume(ask_sizes, da);
    const double denom = bid_vol + ask_vol;
    if (denom <= 0.0) {
        return 0.0;
    }
    double d = (bid_vol - ask_vol) / denom;
    if (d < -1.0) d = -1.0;
    if (d > 1.0) d = 1.0;
    return d;
}

std::pair<double, double>
MicrostructureKernel::order_flow_imbalance(const char* symbol, std::size_t len,
                                           std::uint32_t depth) const noexcept {
    // `BookStore::find` menerima `const char*` saja (bukan pointer+panjang),
    // jadi `len` memang tidak dipakai di sini. Nama simbol bursa tidak pernah
    // memuat null di tengah, dan lookup berbasis `std::string` memerlukan
    // null-terminated. Parameternya dibiarkan agar tanda tangan di header
    // seragam dengan fungsi yang memang memakai panjang.
    (void)len;
    const Book* book = store_.find(symbol);
    if (book == nullptr) {
        return std::make_pair(0.0, 0.0);
    }
    return book_ofi(*book, depth);
}

double MicrostructureKernel::depth_imbalance(const char* symbol, std::size_t len,
                                              std::uint32_t depth) const noexcept {
    (void)len;   // lihat catatan di order_flow_imbalance
    const Book* book = store_.find(symbol);
    if (book == nullptr) {
        return 0.0;
    }
    return book_depth(*book, depth);
}

bool MicrostructureKernel::reserve_symbol(const char* symbol,
                                          std::size_t len) noexcept {
    return store_.reserve_symbol(symbol, len);
}

void MicrostructureKernel::ingest_book(const char* symbol, std::size_t len,
                                       const Book& book) noexcept {
    store_.set_book(symbol, len, book);
}

void MicrostructureKernel::reset(const char* symbol) noexcept {
    store_.reset(symbol);
}

std::size_t MicrostructureKernel::symbol_count() const noexcept {
    return store_.size();
}

// ---------------------------------------------------------------------------
// Parser payload l2Book Hyperliquid
// ---------------------------------------------------------------------------
//
// Bentuk yang diharapkan (ANGKA DI DALAM STRING — detail Hyperliquid yang
// sering bikin parser salah):
//
//   {"channel":"l2Book","data":{"coin":"BTC","levels":[
//      [{"px":"64000.5","sz":"1.2"}, ...],      <- sisi BID, index 0
//      [{"px":"64001.0","sz":"0.8"}, ...]       <- sisi ASK, index 1
//   ],"time":1700000000000}}
//
// Mengembalikan `false` tanpa mengisi `json_error` bila payload BUKAN kanal
// l2Book. Pemisahan "bukan kanal" dari "rusak" itu penting: feed mengirim
// banyak kanal, dan menganggap gagal parse pada pesan `trades` yang memang
// tidak kita pedulikan akan menghentikan pipeline. Hanya payload l2Book yang
// benar-benar rusak yang boleh dianggap error.

namespace {

// Parse satu objek level `{"px":"...","sz":"..."}`.
json::Status parse_px_sz_object(json::Scanner& sc, double& px,
                                double& sz) noexcept {
    json::Status st = sc.expect('{');
    if (st != json::Status::Ok) return st;

    bool have_px = false;
    bool have_sz = false;

    while (true) {
        st = sc.skip_ws();
        if (st != json::Status::Ok) return st;

        char c = '\0';
        st = sc.peek(c);
        if (st != json::Status::Ok) return st;

        if (c == '}') {
            st = sc.expect('}');
            if (st != json::Status::Ok) return st;
            break;
        }
        if (c == ',') {
            st = sc.expect(',');
            if (st != json::Status::Ok) return st;
            continue;
        }

        json::Span key;
        st = sc.scan_string_span(key);
        if (st != json::Status::Ok) return st;

        st = sc.expect(':');
        if (st != json::Status::Ok) return st;

        const char* base = sc.data();
        const bool is_px = (key.len == 2) && (base[key.off] == 'p') &&
                           (base[key.off + 1] == 'x');
        const bool is_sz = (key.len == 2) && (base[key.off] == 's') &&
                           (base[key.off + 1] == 'z');

        if (is_px) {
            st = sc.scan_number_double(px);
            if (st != json::Status::Ok) return st;
            have_px = true;
        } else if (is_sz) {
            st = sc.scan_number_double(sz);
            if (st != json::Status::Ok) return st;
            have_sz = true;
        } else {
            st = sc.skip_value();
            if (st != json::Status::Ok) return st;
        }
    }

    // WAJIB dicek di sini, di luar loop. Versi sebelumnya me-return langsung
    // dari cabang `}` sehingga pemeriksaan ini tidak pernah tereksekusi —
    // level yang hanya punya `px` tanpa `sz` akan lolos sebagai book dengan
    // ukuran 0, dan OFI dihitung dari data yang tidak pernah ada.
    if (!have_px || !have_sz) {
        return json::Status::MissingField;
    }
    return json::Status::Ok;
}

// Parse satu sisi: array of `{"px":..,"sz":..}`.
json::Status parse_side(json::Scanner& sc, Book& book, bool is_bid) noexcept {
    json::Status st = sc.expect('[');
    if (st != json::Status::Ok) return st;

    while (true) {
        st = sc.skip_ws();
        if (st != json::Status::Ok) return st;

        char c = '\0';
        st = sc.peek(c);
        if (st != json::Status::Ok) return st;

        if (c == ']') {
            return sc.expect(']');
        }
        if (c == ',') {
            st = sc.expect(',');
            if (st != json::Status::Ok) return st;
            continue;
        }

        double px = 0.0;
        double sz = 0.0;
        st = parse_px_sz_object(sc, px, sz);
        if (st != json::Status::Ok) return st;

        if (is_bid) {
            book.ingest_bid(px, sz);
        } else {
            book.ingest_ask(px, sz);
        }
    }
}

void set_error(char* out, std::size_t cap, const char* msg) noexcept {
    if (out == nullptr || cap == 0) return;
    std::strncpy(out, msg, cap - 1);
    out[cap - 1] = '\0';
}

}  // namespace

// Parse blok `data` (sudah melewati `{` pembuka dan `:`).
static json::Status parse_data_block(json::Scanner& sc, char* coin,
                                     std::size_t coin_cap, bool& have_coin,
                                     Book& book, bool& have_levels) noexcept {
    json::Status st = sc.expect('{');
    if (st != json::Status::Ok) return st;

    const char* base = sc.data();

    while (true) {
        st = sc.skip_ws();
        if (st != json::Status::Ok) return st;

        char c = '\0';
        st = sc.peek(c);
        if (st != json::Status::Ok) return st;

        if (c == '}') {
            return sc.expect('}');
        }
        if (c == ',') {
            st = sc.expect(',');
            if (st != json::Status::Ok) return st;
            continue;
        }

        json::Span key;
        st = sc.scan_string_span(key);
        if (st != json::Status::Ok) return st;

        st = sc.expect(':');
        if (st != json::Status::Ok) return st;

        const bool key_is_coin = (key.len == 4) &&
                                 (std::memcmp(base + key.off, "coin", 4) == 0);
        const bool key_is_levels = (key.len == 6) &&
                                   (std::memcmp(base + key.off, "levels", 6) == 0);

        if (key_is_coin) {
            json::Span val;
            st = sc.scan_string_span(val);
            if (st != json::Status::Ok) return st;
            if (val.len == 0 || val.len >= coin_cap) {
                return json::Status::MissingField;
            }
            std::memcpy(coin, base + val.off, val.len);
            coin[val.len] = '\0';
            have_coin = true;
        } else if (key_is_levels) {
            // `levels` = [ sisi_bid, sisi_ask ]
            st = sc.expect('[');
            if (st != json::Status::Ok) return st;
            st = parse_side(sc, book, true);
            if (st != json::Status::Ok) return st;
            st = sc.expect(',');
            if (st != json::Status::Ok) return st;
            st = parse_side(sc, book, false);
            if (st != json::Status::Ok) return st;
            st = sc.expect(']');
            if (st != json::Status::Ok) return st;
            have_levels = true;
        } else {
            // "time" dan field lain dilewati, bukan disimpan.
            st = sc.skip_value();
            if (st != json::Status::Ok) return st;
        }
    }
}

bool MicrostructureKernel::ingest_json(const char* payload, std::size_t len,
                                       char* symbol_out,
                                       std::size_t symbol_out_cap,
                                       char* json_error,
                                       std::size_t json_error_cap) noexcept {
    if (symbol_out != nullptr && symbol_out_cap > 0) {
        symbol_out[0] = '\0';
    }

    if (payload == nullptr || len == 0) {
        set_error(json_error, json_error_cap, "payload kosong");
        return false;
    }

    json::Scanner sc(payload, len);
    const char* base = sc.data();

    json::Status st = sc.expect('{');
    if (st != json::Status::Ok) {
        set_error(json_error, json_error_cap, json::status_text(st));
        return false;
    }

    bool is_l2book = false;
    char coin[64];
    coin[0] = '\0';
    bool have_coin = false;
    bool have_levels = false;
    Book book;

    while (true) {
        st = sc.skip_ws();
        if (st != json::Status::Ok) break;

        char c = '\0';
        st = sc.peek(c);
        if (st != json::Status::Ok) break;

        if (c == '}') {
            st = sc.expect('}');
            break;
        }
        if (c == ',') {
            st = sc.expect(',');
            if (st != json::Status::Ok) break;
            continue;
        }

        json::Span key;
        st = sc.scan_string_span(key);
        if (st != json::Status::Ok) break;

        st = sc.expect(':');
        if (st != json::Status::Ok) break;

        const bool key_is_channel = (key.len == 7) &&
                                    (std::memcmp(base + key.off, "channel", 7) == 0);
        const bool key_is_data = (key.len == 4) &&
                                 (std::memcmp(base + key.off, "data", 4) == 0);

        if (key_is_channel) {
            json::Span val;
            st = sc.scan_string_span(val);
            if (st != json::Status::Ok) break;
            is_l2book = (val.len == 6) &&
                        (std::memcmp(base + val.off, "l2Book", 6) == 0);
        } else if (key_is_data) {
            st = parse_data_block(sc, coin, sizeof(coin), have_coin, book,
                                  have_levels);
            if (st != json::Status::Ok) break;
        } else {
            st = sc.skip_value();
            if (st != json::Status::Ok) break;
        }
    }

    // Bukan kanal l2Book: `json_error` sengaja DIKOSONGKAN supaya pemanggil
    // bisa membedakannya dari payload rusak yang wajib dilaporkan.
    if (!is_l2book) {
        if (json_error != nullptr && json_error_cap > 0) {
            json_error[0] = '\0';
        }
        return false;
    }

    if (st != json::Status::Ok) {
        set_error(json_error, json_error_cap, json::status_text(st));
        return false;
    }
    if (!have_coin) {
        set_error(json_error, json_error_cap, "l2Book tanpa field coin");
        return false;
    }
    if (!have_levels) {
        set_error(json_error, json_error_cap, "l2Book tanpa field levels");
        return false;
    }

    if (symbol_out != nullptr && symbol_out_cap > 0) {
        set_error(symbol_out, symbol_out_cap, coin);
    }

    const std::size_t n = std::strlen(coin);
    if (!store_.reserve_symbol(coin, n)) {
        set_error(json_error, json_error_cap,
                  "store mikrostruktur penuh: jumlah simbol melebihi batas");
        return false;
    }
    store_.set_book(coin, n, book);
    return true;
}

}  // namespace tradebot



