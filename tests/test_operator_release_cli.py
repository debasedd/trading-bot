"""
tests/test_operator_release_cli.py — perintah operator harus ADA, bukan
hanya API-nya.

`SafetyGate.operator_release()` menguji keempat syaratnya dengan benar,
tetapi tanpa perintah yang memanggilnya, fungsi itu tidak pernah jalan
dalam pemakaian nyata. Test di sini memanggil `_operator_release()`
persis seperti operator menjalankannya dari shell.

Yang diuji: perintah MENOLAK kalau salah satu syarat tidak terpenuhi, dan
melepas switch kalau semuanya terpenuhi.
"""

import io
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from core.config import LiveConfig
from trading.live.safety import SafetyGate

import run as run_module

PHRASE = SafetyGate.RELEASE_CONFIRMATION_PHRASE


def _gate(tmpdir, engaged=True):
    """Gate di file sementara supaya state dan audit terisolasi."""
    gate = SafetyGate(LiveConfig(), env={},
                      state_path=pathlib.Path(tmpdir) / "live_counters.json")
    if engaged:
        gate.engage_kill_switch("uji")
    return gate


def _run_release(gate, typed, reason, clean=True):
    """Jalankan perintah dengan gate, input, dan rekonsiliasi dipalsukan."""
    async def fake_reconcile():
        return clean, ["bursa terbaca: {}".format(clean), "cocok: 0"]

    with mock.patch.object(run_module, "LiveConfig", return_value=gate.cfg), \
         mock.patch.object(run_module, "setup_logger"), \
         mock.patch.object(run_module, "_fresh_reconcile", fake_reconcile), \
         mock.patch("trading.live.safety.SafetyGate", return_value=gate), \
         mock.patch("builtins.input", side_effect=[typed, reason]):
        return run_module._operator_release()


class TestReleaseCommandIsWired(unittest.TestCase):
    """Perintah harus ada dan terjangkau dari argv."""

    def test_cli_dispatches_to_operator_release(self):
        self.assertTrue(hasattr(run_module, "_operator_release"),
                        "run.py tidak punya _operator_release")

    def test_flag_is_dispatched(self):
        with mock.patch.object(run_module, "_operator_release",
                               return_value=0) as m:
            rc = run_module._cli(["run.py", "--release-kill-switch"])
        self.assertEqual(rc, 0)
        m.assert_called_once()

    def test_flag_appears_in_source(self):
        # Fixture autouse memindahkan cwd ke temp, jadi `run.py` harus
        # dicari lewat `__file__`, bukan lewat path relatif.
        root = pathlib.Path(__file__).resolve().parent.parent
        src = io.open(str(root / "run.py"), encoding="utf-8").read()
        self.assertIn("--release-kill-switch", src,
                      "flag tidak ada di run.py")


class TestReleaseRefusesWhenRequirementsMissing(unittest.TestCase):
    """Setiap syarat yang hilang harus mengembalikan kode 3."""

    def test_empty_reason_refuses(self):
        with tempfile.TemporaryDirectory() as d:
            gate = _gate(d)
            rc = _run_release(gate, PHRASE, "   ")
            self.assertEqual(rc, 3, "alasan kosong TIDAK ditolak")
            self.assertTrue(gate.engaged)

    def test_wrong_confirmation_refuses(self):
        with tempfile.TemporaryDirectory() as d:
            gate = _gate(d)
            rc = _run_release(gate, "ya", "sudah periksa")
            self.assertEqual(rc, 3, "konfirmasi salah TIDAK ditolak")
            self.assertTrue(gate.engaged)

    def test_dirty_reconcile_refuses(self):
        """
        Ini yang paling penting.

        Melepas switch dengan rekonsiliasi tidak bersih mengembalikan bot
        ke order dengan keyakinan salah soal posisi — persis kondisi yang
        menyalakan switch di tempat pertama.
        """
        with tempfile.TemporaryDirectory() as d:
            gate = _gate(d)
            rc = _run_release(gate, PHRASE, "sudah periksa", clean=False)
            self.assertEqual(rc, 3, "rekonsiliasi kotor TIDAK ditolak")
            self.assertTrue(gate.engaged, "switch terlepas padahal kotor")

    def test_unreachable_exchange_refuses(self):
        """Bursa tidak terbaca = tidak bisa memastikan = tidak boleh melepas."""
        with tempfile.TemporaryDirectory() as d:
            gate = _gate(d)

            async def boom():
                raise ConnectionError("bursa tidak terjangkau")

            with mock.patch.object(run_module, "LiveConfig",
                                   return_value=gate.cfg), \
                 mock.patch.object(run_module, "setup_logger"), \
                 mock.patch.object(run_module, "_fresh_reconcile", boom), \
                 mock.patch("trading.live.safety.SafetyGate", return_value=gate):
                rc = run_module._operator_release()
            self.assertEqual(rc, 3, "bursa mati TIDAK ditolak")
            self.assertTrue(gate.engaged)


class TestReleaseSucceedsAndAudits(unittest.TestCase):
    """Semua syarat terpenuhi: switch lepas, audit tertulis."""

    def test_releases_and_writes_audit(self):
        with tempfile.TemporaryDirectory() as d:
            gate = _gate(d)
            rc = _run_release(gate, PHRASE, "sudah periksa divergensi")

            self.assertEqual(rc, 0)
            self.assertFalse(gate.engaged, "switch tidak terlepas")

            audit = gate.audit_path
            self.assertTrue(audit.exists(),
                            "audit log tidak ditulis — pelepasan tanpa "
                            "jejak tidak bisa diaudit")
            lines = [json.loads(x) for x in
                     io.open(audit, encoding="utf-8").read().splitlines() if x]
            self.assertEqual(len(lines), 1)
            rec = lines[0]
            # Isi audit yang harus ada supaya pertanyaan "kapan, kenapa,
            # kode apa yang sedang jalan" bisa dijawab kemudian.
            for field in ("at_utc", "reason", "commit"):
                self.assertIn(field, rec, "audit kurang field %r" % field)
            self.assertEqual(rec["outcome"], "released")
            self.assertEqual(rec["reason"], "sudah periksa divergensi")
            self.assertTrue(rec["engaged_before"])
            self.assertFalse(rec["engaged_after"])


class TestReleaseWhenNotEngaged(unittest.TestCase):
    """Switch tidak aktif: tidak ada yang perlu dilepas."""

    def test_returns_zero_without_asking(self):
        with tempfile.TemporaryDirectory() as d:
            gate = _gate(d, engaged=False)
            with mock.patch.object(run_module, "LiveConfig",
                                   return_value=gate.cfg), \
                 mock.patch.object(run_module, "setup_logger"), \
                 mock.patch("trading.live.safety.SafetyGate", return_value=gate), \
                 mock.patch("builtins.input",
                            side_effect=AssertionError("tanya input")):
                rc = run_module._operator_release()
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()