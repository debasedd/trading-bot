"""
tests/test_isolation_guards.py — Dua guard wajib harus benar-benar menahan.

Fixture `scrub_live_env` dan pemblokir socket di `conftest.py` meng
menawankan dua hal yang paling berbahaya di repo ini:

  1. Env `HYPERLIQUID_*` dan pengalih live tidak boleh bocor antar test.
     Kalau `TRADEBOT_LIVE=1` tertinggal, test berikutnya membangun
     `SafetyGate` dengan gerbang TERBUKA. Repo ini tidak pernah mengirim
     order ke mainnet, dan pytest yang membukanya adalah ide buruk.

  2. Tidak ada test yang boleh menyentuh jaringan. 190 test "live" hijau
     karena me-stub bursa, tapi tidak satu pun membuktikan kode tidak akan
     memanggil API publik sungguhan.

Guard yang tidak bisa dibuktikan menahan adalah tebakan. Test di bawah
menjalankan guard terhadap probe sintetis di subprocess, seperti
`test_pollution_guard_detects.py` melakukan untuk guard pencemar.
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
    Jalankan satu test sintetis di subprocess; kembalikan stdout+stderr.

    Nama file unik per pemanggilan — kalau dua probe memakai nama sama,
    keduanya saling menimpa dan suite jadi flaky untuk alasan yang tidak
    ada hubungannya dengan yang diuji.
    """
    probe_dir = os.path.join(ROOT, "tests", "_probe_tmp")
    os.makedirs(probe_dir, exist_ok=True)
    path = os.path.join(probe_dir, "test_iso_%s.py" % uuid.uuid4().hex[:12])
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


class TestNetworkIsBlocked(unittest.TestCase):
    """Test yang menyentuh jaringan harus GAGAL, bukan lolos diam-diam."""

    def test_outbound_tcp_is_denied(self):
        out = _run_probe("""
            import socket
            def test_tries_to_connect():
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                try:
                    s.connect(("api.hyperliquid-testnet.xyz", 443))
                finally:
                    pass
        """)
        self.assertIn("Akses jaringan diblokir", out, out[-900:])
        self.assertIn("FAILED", out, out[-900:])

    def test_create_connection_is_denied(self):
        out = _run_probe("""
            import socket
            def test_uses_create_connection():
                socket.create_connection(("api.hyperliquid-testnet.xyz", 443), 5)
        """)
        self.assertIn("Akses jaringan diblokir", out, out[-900:])

    def test_dns_resolution_is_denied(self):
        out = _run_probe("""
            import socket
            def test_resolves_dns():
                socket.getaddrinfo("api.hyperliquid-testnet.xyz", 443)
        """)
        self.assertIn("Akses jaringan diblokir", out, out[-900:])

    def test_live_exchange_constructor_makes_no_request(self):
        """
        `LiveExchange(...)` tidak boleh menembus bursa.

        Ini regresi untuk bug nyata: `__init__` pernah membangun
        `Info(base_url)` dan `Exchange(...)`, keduanya POST ke
        `api.hyperliquid-testnet.xyz` untuk `spotMeta` saat konstruksi.
        Test "offline" di `test_api_wallet_separation.py` rutin memanggil
        bursa sungguhan — 110 kegagalan berturut-turut membuktikan itu.
        """
        out = _run_probe("""
            from trading.live.client import LiveExchange
            KEY = "0x" + "cd" * 32
            def test_constructs_offline():
                ex = LiveExchange(KEY, testnet=True, account_address=None)
                assert ex.address
                assert ex.query_address == ex.address.lower()
        """)
        self.assertNotIn("Akses jaringan diblokir", out, out[-900:])
        self.assertIn("1 passed", out, out[-900:])

    def test_loopback_still_works(self):
        """
        Soket loopback TIDAK boleh diblokir.

        `asyncio` pada Windows membangun self-pipe event loop lewat
        `socket.socketpair()`, yang fallback-nya memanggil `connect()` ke
        `localhost`. Memblokir itu membuat setiap `IsolatedAsyncioTestCase`
        gagal — 185 test sekaligus — karena internal runtime, bukan karena
        test suite menyentuh jaringan.
        """
        out = _run_probe("""
            import asyncio
            class T(unittest.IsolatedAsyncioTestCase):
                async def test_async_still_works(self):
                    await asyncio.sleep(0)
            import unittest
        """.replace("class T(", "import unittest\nclass T("))
        self.assertNotIn("Akses jaringan diblokir", out, out[-900:])

    def test_isolated_asyncio_test_case_suite_runs(self):
        """Bukti nyata: loop asyncio harus bisa dibuat di bawah pemblokir."""
        out = _run_probe("""
            import asyncio
            import unittest

            class T(unittest.IsolatedAsyncioTestCase):
                async def test_loop_created(self):
                    await asyncio.sleep(0)
                    self.assertTrue(True)
        """)
        self.assertIn("1 passed", out, out[-900:])

    def test_optout_marker_allows_network(self):
        """
        Test yang ditandai `no_network` boleh memakai soket.

        Loopback dikecualikan secara implisit di atas; marker ini untuk
        kasus yang butuh listener sungguhan. Yang diuji di sini hanya
        bahwa marker TIDAK merusak apa pun — kalau opt-out rusak total,
        suite akan gagal dan itu terlihat di mana saja.
        """
        out = _run_probe("""
            import pytest

            @pytest.mark.no_network
            def test_marked_still_runs():
                assert True
        """)
        self.assertIn("1 passed", out, out[-900:])
        self.assertNotIn("ERROR", out, out[-900:])


class TestLiveEnvIsScrubbed(unittest.TestCase):
    """Env live tidak boleh diwarisi test berikutnya."""

    def test_live_env_is_removed_before_test(self):
        out = _run_probe("""
            import os

            # simulating shell yang menyalakan live
            os.environ["TRADEBOT_LIVE"] = "1"
            os.environ["HYPERLIQUID_PRIVATE_KEY"] = "0x" + "ab" * 32

            def test_env_is_clean():
                assert "TRADEBOT_LIVE" not in os.environ, \\
                    "TRADEBOT_LIVE bocor masuk test"
                assert not any(k.startswith("HYPERLIQUID_") for k in os.environ), \\
                    "HYPERLIQUID_* bocor masuk test"
        """)
        self.assertIn("1 passed", out, out[-900:])

    def test_env_written_by_test_still_reaches_the_pollution_guard(self):
        """
        Dua guard ini tidak boleh saling meniadakan.

        `scrub_live_env` hanya mengembalikan env yang ADA SEBELUM test.
        Kalau ia ikut membersihkan env yang ditulis test, `guard_global_state`
        tidak akan pernah melihatnya — dan pencemar paling berbahaya
        (env yang menyalakan live) jadi tidak terdeteksi.
        """
        out = _run_probe("""
            import os

            def test_writes_live_env():
                os.environ["TRADEBOT_LIVE"] = "1"
        """)
        self.assertIn("PENCEMAR STATE GLOBAL", out, out[-900:])
        self.assertIn("TRADEBOT_LIVE", out, out[-900:])

    def test_hyperliquid_prefix_is_scrubbed(self):
        out = _run_probe("""
            import os

            os.environ["HYPERLIQUID_ACCOUNT_ADDRESS"] = "0x" + "11" * 20

            def test_env_is_clean():
                assert "HYPERLIQUID_ACCOUNT_ADDRESS" not in os.environ
        """)
        self.assertIn("1 passed", out, out[-900:])


if __name__ == "__main__":
    unittest.main()