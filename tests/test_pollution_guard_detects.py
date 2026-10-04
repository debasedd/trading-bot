"""
tests/test_pollution_guard_detects.py — Guard harus benar-benar MENANGGAL.

Fixture `guard_global_state` di `conftest.py` hanya berguna kalau ia
mendeteksi pencemar. Kalau guard-nya sendiri bocor, suite tetap hijau
dan pencemar berikutnya tidak akan pernah terlihat — persis kegagalan
yang sedang kita perbaiki.

Test di sini menjalankan guard di subprocess terhadap file test SINTETIS
yang sengaja bocor, lalu memeriksa pesan kegagalannya menyebut pembocornya.
Tidak ada yang menyentuh state proses pytest yang sedang berjalan.

Yang diuji satu per satu, karena tiap jenis pencemar punya jalur berbeda
di guard: `market_store`, `os.environ`, config singleton, dict modul,
dan `database.db._db`.
"""
import os
import subprocess
import sys
import textwrap
import unittest
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run_probe(body: str) -> str:
    """
    Jalankan satu test sintetis di subprocess, kembalikan stdout+stderr.

    `body` adalah badan test. Dirender ke file sementara di dalam repo
    supaya `conftest.py` ikut ter-collection — guard hanya aktif kalau
    pytest menemukan directory `tests/`.

    Nama file WAJIB unik per pemanggilan. Versi pertama memakai nama tetap
    untuk semua probe, jadi saat dua test berjalan bersamaan (atau satu
    menyisakan file saat yang lain menimpanya) subprocess kedua membaca
    file yang sudah dihapus dan melaporkan "file not found" — test suite
    saya sendiri jadi flaky, dan debug-nya memakan waktu lebih banyak
    daripada perbaikan yang asli.
    """
    probe_dir = os.path.join(ROOT, "tests", "_probe_tmp")
    os.makedirs(probe_dir, exist_ok=True)
    path = os.path.join(probe_dir, "test_probe_%s.py" % uuid.uuid4().hex[:12])
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(textwrap.dedent(body))
        r = subprocess.run(
            [sys.executable, "-m", "pytest", path, "-q",
             "-p", "no:cacheprovider", "-p", "no:randomly", "--tb=line"],
            capture_output=True, text=True, cwd=ROOT, timeout=120,
        )
        return (r.stdout + r.stderr)
    finally:
        if os.path.exists(path):
            os.remove(path)
        try:
            os.rmdir(probe_dir)
        except OSError:
            pass


class TestGuardDetectsPollution(unittest.TestCase):
    """Tiap jenis pencemar harus dibaca guard, bukan lolos diam-diam."""

    def assertDetected(self, out, needle):
        self.assertIn("PENCEMAR STATE GLOBAL", out, out[-800:])
        self.assertIn(needle, out, out[-800:])

    def test_detects_market_store_write(self):
        out = _run_probe("""
            def test_polluter():
                from core.market_store import market_store
                market_store.set_funding("BTC/USDT:USDT", 0.0002)
        """)
        self.assertDetected(out, "_funding")

    def test_detects_env_write(self):
        out = _run_probe("""
            import os
            def test_polluter():
                os.environ["TRADEBOT_LIVE_KILL_SWITCH"] = "1"
        """)
        self.assertDetected(out, "TRADEBOT_LIVE_KILL_SWITCH")

    def test_detects_config_content_change(self):
        """
        Config singleton diubah ISI-nya, bukan hanya `_config` di-replace.

        Ini kasus yang paling mudah lolos: `id(_config)` tetap sama,
        jadi guard yang hanya membandingkan identitas akan hijau.
        """
        out = _run_probe("""
            def test_polluter():
                from core.config import get_config
                get_config().fees.taker = 0.5
        """)
        self.assertDetected(out, "taker")

    def test_detects_module_level_dict_write(self):
        out = _run_probe("""
            from analysis import volatility
            def test_polluter():
                volatility._ATR_CACHE[("BTC", 14)] = {"atr": 1.0}
        """)
        self.assertDetected(out, "_ATR_CACHE")

    def test_detects_db_singleton_swap(self):
        out = _run_probe("""
            from database import db as db_mod
            class _Fake:
                path = "data_store/probe.db"
            def test_polluter():
                db_mod._db = _Fake()
        """)
        self.assertDetected(out, "db")

    def test_clean_test_passes(self):
        """
        Test yang TIDAK menulis state global harus tetap hijau.

        Guard yang menolak test bersih tidak bisa dipakai — eventually
        akan dimatikan, dan вот тогда pencemar masuk tanpa terlihat.
        """
        out = _run_probe("""
            def test_clean():
                from core.market_store import market_store
                assert market_store.get_funding("BTC/USDT:USDT") is None
        """)
        self.assertNotIn("PENCEMAR STATE GLOBAL", out, out[-800:])
        self.assertIn("1 passed", out, out[-800:])

    def test_restoring_makes_it_pass(self):
        """
        Test yang menulis lalu MEMULIHKAN harus hijau.

        Ini yang menguji bahwa guard membandingkan sebelum/sesudah, bukan
        sekadar melarang menulis. Tanpa test ini, "solusi" paling mudah
        adalah melarang semua akses, dan itu tidak menyelesaikan apa pun.
        """
        out = _run_probe("""
            import os
            def test_polluter_but_honest():
                os.environ["PROBE_KEY"] = "1"
                try:
                    assert os.environ["PROBE_KEY"] == "1"
                finally:
                    del os.environ["PROBE_KEY"]
        """)
        self.assertNotIn("PENCEMAR STATE GLOBAL", out, out[-800:])
        self.assertIn("1 passed", out, out[-800:])

    def test_polluter_is_named_in_the_message(self):
        """Pesan harus menyebut test mana yang bocor, bukan cuma apa yang bocor."""
        out = _run_probe("""
            def test_clearly_the_culprit():
                from core.market_store import market_store
                market_store.set_open_interest("BTC/USDT:USDT", 5.0)
        """)
        self.assertDetected(out, "test_clearly_the_culprit")


if __name__ == "__main__":
    unittest.main()