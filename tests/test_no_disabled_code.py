"""
tests/test_no_disabled_code.py — Pagar untuk kode yang dimatikan.

MASALAH YANG INI TUTUP
----------------------
`trading/live/client.py` pernah punya baris:

    if False:  # MUTAN: validasi bentuk alamat dimatikan
        raise RuntimeError(...)

Guard validasi bentuk alamat mati. Helper `_is_address()` yang ditulis
untuk memanggilnya tidak pernah dipanggil di mana pun. Dan test yang
seharusnya menangkap ini tetap hijau, karena test itu salah bercabang dan
hanya memeriksa substring pesan.

Tiga kegagalan dalam satu file produksi, dan tidak ada satu pun yang
bersuara. `git status` bersih, `pytest` hijau.

Mutasi dipatok supaya TIDAK PERNAH terjadi diam-diam: dari sekarang
cabutan mutan dilakukan lewat patch yang diterapkan lalu dicabut, bukan
dengan menyunting file produksi di tempat. File ini yang memastikan sisa
edits seperti itu tidak ikut ter-commit.

YANG DIPINDAI
-------------
1. `if False:` / `if True:` sebagai kondisi — lewat AST, bukan grep.
2. Teks `MUTAN` di file produksi mana pun.

KENAPA AST, BUKAN GREP
---------------------
`grep "if False"` salah pada dua arah: ia mendeteksi `if False` di dalam
docstring dan komentar, dan ia TIDAK mendeteksi bentuk lain yang setara
fungsional seperti `if not False:`. AST melihat kondisi yang benar-benar
dieksekusi.

MENGAPA `trading/` DAN `run.py` SAJA
-----------------------------------
Keduanya menyentuh uang dan titik masuk proses. File test boleh penuh
marker mutasi — memang itu alat kerjanya.
"""

import ast
import pathlib
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SCANNED_DIRS = [REPO_ROOT / "trading"]
SCANNED_FILES = [REPO_ROOT / "run.py"]

# Marker yang menandai file produksi sebagai "sementara dimutasi". Hanya
# boleh hidup di `tests/`, karena di situ memang alat kerjanya.
MUTATION_MARKERS = ("MUTAN",)


def _iter_production_files():
    for directory in SCANNED_DIRS:
        for path in sorted(directory.rglob("*.py")):
            yield path
    for path in SCANNED_FILES:
        if path.exists():
            yield path


def _constant_conditions(tree):
    """
    Hasilkan `(lineno, kind)` untuk setiap `if` yang kondisinya konstan.

    `if False:` mematikan seluruh badan. `if True:` membodohi pembaca
    kode: ia terlihat seperti cabang yang bisa gagal, padahal tidak
    pernah. Keduanya layak menggagalkan commit.

    Angka dan `None` ikut dipindai karena secara fungsional sama:
    `if 1:` adalah `if True:`, dan `if 0:` adalah `if False:`. Tidak ada
    satu pun kondisi seperti itu di produksi saat pagar ini ditulis
    (dicek dengan AST ke seluruh `trading/` dan `run.py`), jadi tidak
    ada false positive yang harus dikecualikan.

    `while True:` TIDAK dipindai — itu pola loop yang sah.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if isinstance(test, ast.Constant):
            # `None` sebagai kondisi `if` juga selalu konstan.
            yield node.lineno, repr(test.value)
            continue
        # `if not False:` setara dengan `if True:`.
        if (isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not)
                and isinstance(test.operand, ast.Constant)):
            yield node.lineno, "not %s" % (test.operand.value,)


class TestNoConstantIfConditions(unittest.TestCase):
    """Tidak boleh ada cabang dengan kondisi konstan di jalur produksi."""

    def test_no_constant_if_conditions(self):
        offenders = []
        for path in _iter_production_files():
            tree = ast.parse(path.read_text(encoding="utf-8"),
                             filename=str(path))
            for lineno, kind in _constant_conditions(tree):
                rel = path.relative_to(REPO_ROOT).as_posix()
                offenders.append("%s:%d — `if %s:`" % (rel, lineno, kind))

        self.assertEqual(
            offenders, [],
            "Kondisi konstan di kode produksi. Cabang `if False:` mematikan "
            "perilaku tanpa suara, dan `if True:` membuat kode terlihat "
            "seperti punya jalur gagal padahal tidak:\n  "
            + "\n  ".join(offenders),
        )

    def test_scanner_actually_detects_the_pattern(self):
        """
        Kontrol negatif: pagar ini harus benar-benar bisa menangkap.

        Tanpa test ini, `test_no_constant_if_conditions` bisa hijau karena
        pemindainya rusak — dan pagar yang tidak pernah berbunyi sama
        nilainya dengan tidak ada pagar.
        """
        good = ast.parse("if x:\n    pass\n")
        self.assertEqual(list(_constant_conditions(good)), [])

        for snippet in ("if False:\n    pass\n", "if True:\n    pass\n",
                        "if not False:\n    pass\n", "if 0:\n    pass\n",
                        "if 1:\n    pass\n", "if None:\n    pass\n"):
            with self.subTest(snippet=snippet):
                found = list(_constant_conditions(ast.parse(snippet)))
                self.assertEqual(len(found), 1,
                                 "pemindai gagal menangkap %r" % snippet)

        # Konstanta di dalam perbandingan tetap sah: `if x > 1:` dan
        # `if n == 1:` bukan kondisi konstan.
        for snippet in ("if x > 1:\n    pass\n", "if n == 1:\n    pass\n",
                        "if flag:\n    pass\n"):
            with self.subTest(snippet=snippet):
                self.assertEqual(
                    list(_constant_conditions(ast.parse(snippet))), [],
                    "pemindai terlalu agresif pada %r" % snippet)

        # Loop `while True:` adalah pola sah dan tidak boleh kena.
        loop = ast.parse("while True:\n    pass\n")
        self.assertEqual(list(_constant_conditions(loop)), [])


class TestNoMutationMarkersInProduction(unittest.TestCase):
    """`MUTAN` hanya boleh ada di `tests/`."""

    def test_no_mutation_markers(self):
        offenders = []
        for path in _iter_production_files():
            text = path.read_text(encoding="utf-8")
            for lineno, line in enumerate(text.splitlines(), start=1):
                for marker in MUTATION_MARKERS:
                    if marker in line:
                        rel = path.relative_to(REPO_ROOT).as_posix()
                        offenders.append(
                            "%s:%d — %s" % (rel, lineno, line.strip()))

        self.assertEqual(
            offenders, [],
            "Marker mutasi tertinggal di kode produksi. Mutasi harus "
            "diberlakukan sebagai patch yang diterapkan lalu dicabut, "
            "bukan menyunting file ini:\n  " + "\n  ".join(offenders),
        )

    def test_repository_is_actually_scanned(self):
        """
        Kontrol negatif: daftar file yang dipindai tidak boleh kosong.

        Pagar yang memindai nol file akan hijau selamanya.
        """
        scanned = list(_iter_production_files())
        self.assertGreater(
            len(scanned), 10,
            "hanya %d file yang dipindai — cek SCANNED_DIRS/SCANNED_FILES"
            % len(scanned),
        )
        names = {p.name for p in scanned}
        self.assertIn("client.py", names,
                      "trading/live/client.py harus ikut dipindai")
        self.assertIn("run.py", names, "run.py harus ikut dipindai")


if __name__ == "__main__":
    unittest.main()