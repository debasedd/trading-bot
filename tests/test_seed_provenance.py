"""
tests/test_seed_provenance.py — seed acak harus bisa ditelusuri.

Seed yang diketik manual di laporan verifikasi tidak bisa diaudit: tidak
ada yang bisa membuktikanchoices itu benar-benar acak, dan tidak ada yang
bisa mengulangnya.

`_seed_pick.py` menulis seed ke berkas; `_seed_use.py` membacanya dari
berkas. Skrip yang dipakai di laporan harus lewat kedua jalur itu.
"""

import io
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
PICK = ROOT / "_seed_pick.py"
USE = ROOT / "_seed_use.py"


class TestSeedProvenance(unittest.TestCase):

    def test_both_scripts_exist(self):
        self.assertTrue(PICK.exists(), "_seed_pick.py tidak ada")
        self.assertTrue(USE.exists(), "_seed_use.py tidak ada")

    def test_pick_writes_readable_seeds_to_file(self):
        with tempfile.TemporaryDirectory() as d:
            target = pathlib.Path(d) / "seed.txt"
            r = subprocess.run([sys.executable, str(PICK), "3", str(target)],
                               capture_output=True, text=True, cwd=str(ROOT))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(target.exists(),
                            "seed tidak ditulis ke berkas")

            u = subprocess.run([sys.executable, str(USE), str(target)],
                               capture_output=True, text=True, cwd=str(ROOT))
            self.assertEqual(u.returncode, 0, u.stderr)
            self.assertIn("3 seed terbaca", u.stdout)

    def test_seed_file_records_its_own_provenance(self):
        """
        Berkas seed harus menyebut kapan dan perintah apa yang
        menghasilkannya.

        Tanpa itu, isi berkas bisa saja diketik manual seperti isi
        laporan — dan file itu sendiri jadi klaim tanpa bukti.
        """
        with tempfile.TemporaryDirectory() as d:
            target = pathlib.Path(d) / "seed.txt"
            subprocess.run([sys.executable, str(PICK), "2", str(target)],
                           capture_output=True, text=True, cwd=str(ROOT))
            body = target.read_text(encoding="utf-8")
            self.assertIn("dibuat:", body,
                          "berkas seed tidak mencatat waktu dibuat")
            self.assertIn("python", body,
                          "berkas seed tidak mencatat perintah penghasilnya")

    def test_two_runs_produce_different_seeds(self):
        """
        Dua Calls harus memberi seed berbeda.

        Kalau tidak, seed-nya konstan dan "acak" itu hanya lengthwise.
        """
        with tempfile.TemporaryDirectory() as d:
            a = pathlib.Path(d) / "a.txt"
            b = pathlib.Path(d) / "b.txt"
            for target in (a, b):
                subprocess.run([sys.executable, str(PICK), "3", str(target)],
                               capture_output=True, text=True, cwd=str(ROOT))
            self.assertNotEqual(a.read_text(encoding="utf-8").split("\n\n")[1],
                                b.read_text(encoding="utf-8").split("\n\n")[1],
                                "dua panggilan menghasilkan seed yang sama "
                                "— seed-nya tidak acak")

    def test_committed_seed_file_exists_and_is_readable(self):
        """Berkas seed yang dipakai laporan harus ikut ter-commit."""
        target = ROOT / "docs" / "reports" / "seed-bukti-cabutan.txt"
        self.assertTrue(target.exists(),
                        "docs/reports/seed-bukti-cabutan.txt tidak ada")
        sys.path.insert(0, str(ROOT))
        from _seed_use import read_seeds
        self.assertGreaterEqual(len(read_seeds(str(target))), 1)


if __name__ == "__main__":
    unittest.main()