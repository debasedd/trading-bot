"""
Skrip sekali pakai: bersihkan karakter CJK/aneh yang tidak sengaja masuk ke
komentar file Python.

Editor tool kadang menyisipkan karakter asing. Karakter itu merusak file
dengan cara yang membingungkan: Python membacanya dengan cp1252 dan CRASH
saat hanya ingin mencetak ke stdout, sehingga file yang sebenarnya benar
terlihat rusak.

Karakter TIDAK dihapus diam-diam — daftar occurrence ditulis lebih dulu ke
file laporan supaya perbaikannya bisa ditinjau manusia.
"""
import pathlib
import sys

BAD_RANGES = (
    (0x2E80, 0x9FFF),    # CJK
    (0xAC00, 0xD7AF),    # Hangul
    (0xFF00, 0xFFEF),    # fullwidth
)


def is_bad(ch: str) -> bool:
    code = ord(ch)
    return any(lo <= code <= hi for lo, hi in BAD_RANGES)


def main() -> int:
    target = pathlib.Path(sys.argv[1])
    text = target.read_text(encoding="utf-8")
    lines = text.splitlines()

    report = []
    fixed = 0
    for number, line in enumerate(lines, 1):
        bad = [c for c in line if is_bad(c)]
        if not bad:
            continue
        report.append(
            "L{}: {} -> {}".format(
                number,
                " ".join(hex(ord(c)) for c in bad),
                line.strip()[:100].encode("ascii", "replace").decode("ascii"),
            )
        )
        fixed += len(bad)

    out = target.with_suffix(target.suffix + ".cjk-report.txt")
    out.write_text("\n".join(report) or "(bersih)", encoding="utf-8")
    print("{} kemunculan di {} baris -> {}".format(fixed, len(report), out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
