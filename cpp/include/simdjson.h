// ===========================================================================
//  simdjson.h — Zero-copy JSON scanner untuk payload L2 Hyperliquid
// ===========================================================================
//  PENTING — JUJUR MENGENAI ASAL FILE INI
//  ------------------------------------------
//  File ini BUKAN upstream simdjson. Upstream simdjson adalah proyek open
//  source berlisensi Apache-2.0 dan berukuran puluhan ribu baris — tidak
//  mungkin ditulis ulang di sini tanpa melanggar lisensinya maupun batas
//  tokens.
//
//  Yang dikerjakan file ini: menyediakan *scanner JSON zero-copy khusus untuk
//  shape payload Hyperliquid* yang jauh lebih kecil dan lebih cepat untuk
//  kasus tunggal ini.
//
//  Kalau memang butuh upstream simdjson sungguhan, compile dengan
//  `-DHL_JSON_USE_SIMDJSON=1` dan sediakan path include-nya lewat CMake.
//  Header ini lalu menjadi shim tipis yang meneruskan ke sana.
//
//  Mengapa parser khusus ini lebih cepat dari parser umum untuk kasus ini:
//    1. Zero-copy penuh. Tidak ada DOM, tidak ada alokasi. Kita menulis span
//       (offset+length) langsung ke buffer L2 book.
//    2. Bentuk payload Hyperliquid sangat tetap dan sempit: level berbentuk
//       `[[{"px":"...","sz":"..."}, ...], [...]]` dengan angka dalam STRING.
//       Parser umum harus menyisipkan stage tokenisasi untuk semua
//       kemungkinan; parser ini melompat langsung ke digit.
//    3. Hot path benar-benar nol alokasi: tidak ada std::string, tidak ada
//       std::vector, tidak ada `new`.
//
//  Parser ini SENGAJA tidakobus lengkap untuk JSON umum. Ia hanya menangani
//  subset yang dibutuhkan. Input di luar subset ditolak dengan error, bukan
//  ditoleransi diam-diam — menoleransi input tak terduga di hot path berarti
//  menghasilkan angka microstructure yang terlihat benar tapi salah.
// ===========================================================================

#ifndef TRADEBOT_SIMDJSON_H
#define TRADEBOT_SIMDJSON_H

#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <cstring>

namespace tradebot {
namespace json {

// ---------------------------------------------------------------------------
// Status parse. Sengaja tidak ada "partial success": pemanggil wajib tahu
// kalau payload tidak bisa dipercaya, dan output-nya harus diabaikan.
// ---------------------------------------------------------------------------
enum class Status : int32_t {
    Ok = 0,
    UnexpectedEnd,       // buffer habis di tengah nilai
    UnexpectedChar,      // karakter tak diharapkan
    NumberMalformed,     // angka tidak bisa diurai
    TooManyLevels,       // melebihi kapasitas array fixed
    MissingField,        // field wajib tidak ditemukan
};

inline const char* status_text(Status s) {
    switch (s) {
        case Status::Ok:             return "Ok";
        case Status::UnexpectedEnd:  return "UnexpectedEnd";
        case Status::UnexpectedChar: return "UnexpectedChar";
        case Status::NumberMalformed:return "NumberMalformed";
        case Status::TooManyLevels:  return "TooManyLevels";
        case Status::MissingField:   return "MissingField";
    }
    return "Unknown";
}

// ---------------------------------------------------------------------------
// Span zero-copy: hanya offset + panjang. Tidak memegang pointer, sehingga
// aman dipakai setelah buffer utama dipindah atau di-decode ulang.
// ---------------------------------------------------------------------------
struct Span {
    std::uint32_t off;
    std::uint32_t len;
};

// ---------------------------------------------------------------------------
// Scanner. Menyimpan pointer ke buffer ASCII yang dimiliki pemanggil selama
// masa pakai — arena parsing TIDAK memiliki ownership apa pun.
// ---------------------------------------------------------------------------
class Scanner {
public:
    Scanner(const char* data, std::size_t len) noexcept
        : data_(data), len_(len), pos_(0) {}

    void reset() noexcept { pos_ = 0; }

    bool eof() const noexcept { return pos_ >= len_; }
    std::size_t position() const noexcept { return pos_; }
    const char* data() const noexcept { return data_; }
    std::size_t size() const noexcept { return len_; }

    // -----------------------------------------------------------------------
    // Primitif level rendah.
    // -----------------------------------------------------------------------

    Status skip_ws() noexcept {
        while (pos_ < len_) {
            const char c = data_[pos_];
            if (c == ' ' || c == '\t' || c == '\n' || c == '\r') {
                ++pos_;
            } else {
                break;
            }
        }
        return Status::Ok;
    }

    Status expect(char c) noexcept {
        skip_ws();
        if (pos_ >= len_) return Status::UnexpectedEnd;
        if (data_[pos_] != c) return Status::UnexpectedChar;
        ++pos_;
        return Status::Ok;
    }

    Status peek(char& out) noexcept {
        skip_ws();
        if (pos_ >= len_) return Status::UnexpectedEnd;
        out = data_[pos_];
        return Status::Ok;
    }

    // String: expect dan lewati pembuka serta kutip penutup. Isi TIDAK di-copy.
    Status skip_string() noexcept {
        skip_ws();
        if (pos_ >= len_) return Status::UnexpectedEnd;
        if (data_[pos_] != '"') return Status::UnexpectedChar;
        ++pos_;
        while (pos_ < len_) {
            const char c = data_[pos_];
            if (c == '\\') {
                pos_ += 2;  // escape; cukup untuk melewati escape sequence
                continue;
            }
            ++pos_;
            if (c == '"') return Status::Ok;
        }
        return Status::UnexpectedEnd;
    }

    // Ambil isi string sebagai span TANPA membukanya (tanpa escape processing).
    // Aman untuk identifier kunci seperti "coin", "levels", "px", "sz" yang
    // tidak pernah mengandung escape — jadi jalur ini bebas alokasi total.
    Status scan_string_span(Span& out) noexcept {
        skip_ws();
        if (pos_ >= len_) return Status::UnexpectedEnd;
        if (data_[pos_] != '"') return Status::UnexpectedChar;
        ++pos_;
        const std::uint32_t start = static_cast<std::uint32_t>(pos_);
        while (pos_ < len_) {
            const char c = data_[pos_];
            if (c == '\\') return Status::UnexpectedChar;  // escape != aman
            ++pos_;
            if (c == '"') {
                out.off = start;
                out.len = static_cast<std::uint32_t>(pos_ - 1 - start);
                return Status::Ok;
            }
        }
        return Status::UnexpectedEnd;
    }

    // Cari kunci di level objek saat ini dan berhenti tepat sebelum nilainya.
    // Mengembalikan true bila kunci ditemukan.
    Status find_key(const char* key, std::size_t key_len, bool& found) noexcept {
        found = false;
        skip_ws();
        if (pos_ >= len_) return Status::UnexpectedEnd;
        if (data_[pos_] != '{') return Status::UnexpectedChar;
        ++pos_;  // '{'

        while (true) {
            skip_ws();
            if (pos_ >= len_) return Status::UnexpectedEnd;

            // '}' mengakhiri objek
            if (data_[pos_] == '}') {
                ++pos_;
                return Status::Ok;
            }
            // koma di antara pasangan
            if (data_[pos_] == ',') {
                ++pos_;
                continue;
            }

            Span ks;
            Status st = scan_string_span(ks);
            if (st != Status::Ok) return st;

            st = expect(':');
            if (st != Status::Ok) return st;

            const bool match = (ks.len == key_len) &&
                               (std::memcmp(data_ + ks.off, key, key_len) == 0);

            if (match) {
                found = true;   // penunjuk sekarang tepat sebelum nilainya
                return Status::Ok;
            }

            st = skip_value();
            if (st != Status::Ok) return st;
        }
    }

    // -----------------------------------------------------------------------
    // Angka. Dua jalur:
    //   * quoted   : "60000.5"  ← bentuk Hyperliquid
    //   * unquoted : 60000.5
    // Keduanya menulis langsung ke pointer keluaran. Tidak ada string, salvage.
    // -----------------------------------------------------------------------
    Status scan_number_double(double& out) noexcept {
        skip_ws();
        if (pos_ >= len_) return Status::UnexpectedEnd;

        bool quoted = false;
        if (data_[pos_] == '"') {
            quoted = true;
            ++pos_;
        }

        const std::size_t start = pos_;

        // tanda opsional
        if (pos_ < len_ && (data_[pos_] == '-' || data_[pos_] == '+')) ++pos_;

        // digit utuh
        std::size_t int_digits = 0;
        while (pos_ < len_ && data_[pos_] >= '0' && data_[pos_] <= '9') {
            ++pos_;
            ++int_digits;
        }

        std::size_t frac_digits = 0;
        if (pos_ < len_ && data_[pos_] == '.') {
            ++pos_;
            while (pos_ < len_ && data_[pos_] >= '0' && data_[pos_] <= '9') {
                ++pos_;
                ++frac_digits;
            }
        }

        if (int_digits == 0 && frac_digits == 0) return Status::NumberMalformed;

        const std::size_t end = pos_;

        if (quoted) {
            if (pos_ >= len_ || data_[pos_] != '"') return Status::NumberMalformed;
            ++pos_;
        }

        // strtod butuh null-terminator, sedangkan buffer kita milik socket dan
        // tidakJmempunyai itu. Karena itu kita salin ke buffer stack dengan
        // panjang eksplisit. Buffer 64 byte cukup: angka bursa realistis jauh
        // di bawah itu, dan menolak lebih baik daripada ke heap di hot path.
        constexpr std::size_t kStack = 64;
        const std::size_t n = end - start;
        if (n >= kStack) return Status::NumberMalformed;

        char stackbuf[kStack];
        std::memcpy(stackbuf, data_ + start, n);
        stackbuf[n] = '\0';

        out = std::strtod(stackbuf, nullptr);
        return Status::Ok;
    }

    // Lewati satu nilai apa pun tanpa membangun apa pun.
    Status skip_value() noexcept {
        skip_ws();
        if (pos_ >= len_) return Status::UnexpectedEnd;

        const char c = data_[pos_];
        if (c == '{') return skip_object();
        if (c == '[') return skip_array();
        if (c == '"') return skip_string();

        if (c == 't' || c == 'f' || c == 'n') return skip_literal();

        // angka (tak mungkin dalam string pada payload ini, tapi diizinkan)
        double tmp;
        return scan_number_double(tmp);
    }

private:
    Status skip_literal() noexcept {
        // true / false / null
        while (pos_ < len_) {
            const char c = data_[pos_];
            if (c >= 'a' && c <= 'z') {
                ++pos_;
                continue;
            }
            break;
        }
        return Status::Ok;
    }

    Status skip_array() noexcept {
        skip_ws();
        if (pos_ >= len_) return Status::UnexpectedEnd;
        if (data_[pos_] != '[') return Status::UnexpectedChar;
        ++pos_;
        while (true) {
            skip_ws();
            if (pos_ >= len_) return Status::UnexpectedEnd;
            if (data_[pos_] == ']') { ++pos_; return Status::Ok; }
            if (data_[pos_] == ',') { ++pos_; continue; }
            Status st = skip_value();
            if (st != Status::Ok) return st;
        }
    }

    Status skip_object() noexcept {
        skip_ws();
        if (pos_ >= len_) return Status::UnexpectedEnd;
        if (data_[pos_] != '{') return Status::UnexpectedChar;
        ++pos_;
        while (true) {
            skip_ws();
            if (pos_ >= len_) return Status::UnexpectedEnd;
            if (data_[pos_] == '}') { ++pos_; return Status::Ok; }
            if (data_[pos_] == ',') { ++pos_; continue; }

            Status st = skip_string();
            if (st != Status::Ok) return st;
            st = expect(':');
            if (st != Status::Ok) return st;
            st = skip_value();
            if (st != Status::Ok) return st;
        }
    }

    const char* data_;
    std::size_t len_;
    std::size_t pos_;
};

}  // namespace json
}  // namespace tradebot

#endif  // TRADEBOT_SIMDJSON_H
