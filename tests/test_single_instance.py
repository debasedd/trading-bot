"""
tests/test_single_instance.py — satu proses bot per akun.

Dua bot yang mengirim order ke akun yang sama menghasilkan DUA
eksposur: keduanya membaca posisi dari bursa, menghitung, lalu mengirim,
dan mereka tidak tahu tentang order satu sama lain. Kill switch hanya
ada di satu proses.

Yang diuji, bukan yang diasumsikan:
  * lock kedua benar-benar MENOLAK (bukan hanya "ada filenya");
  * lock yang tertinggal dari proses yang sudah MATI diambil alih;
  * `--release-kill-switch` menolak jalan saat bot hidup.
"""

import asyncio
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from trading.live.single_instance import (SingleInstanceLock, _pid_alive,
                                          lock_path_for, read_lock_pid)

ACCOUNT = "0x" + "ab" * 32


class _Chdir:
    """Jalankan test di direktori sementara supaya lock tidak bocor."""

    def __init__(self, path):
        self.path = path

    def __enter__(self):
        self.old = os.getcwd()
        os.chdir(self.path)
        return self

    def __exit__(self, *exc):
        os.chdir(self.old)
        return False


class _App:
    """App tiruan yang tidak menjalankan apa pun."""

    def __init__(self, decision):
        self.decision = decision

    async def initialize(self):
        pass

    async def run(self):
        pass

    async def shutdown(self):
        pass


class _LiveDecision:
    mode = "testnet"


class TestLockIsExclusive(unittest.TestCase):
    """Kunci kedua harus ditolak, bukan hanya 'file-nya ada'."""

    def test_second_lock_is_refused(self):
        with tempfile.TemporaryDirectory() as d, _Chdir(d):
            first = SingleInstanceLock(ACCOUNT)
            self.assertTrue(first.acquire())

            second = SingleInstanceLock(ACCOUNT)
            self.assertFalse(second.acquire(),
                             "dua lock untuk akun yang sama keduanya "
                             "berhasil — dua proses akan mengirim order "
                             "ke akun yang sama")
            self.assertIsNotNone(second.blocked_reason)

    def test_blocked_reason_names_the_pid(self):
        with tempfile.TemporaryDirectory() as d, _Chdir(d):
            first = SingleInstanceLock(ACCOUNT)
            first.acquire()
            second = SingleInstanceLock(ACCOUNT)
            second.acquire()
            self.assertIn(str(os.getpid()), second.blocked_reason,
                          "pesan tolak tidak menyebut PID yang memegang "
                          "lock — operator tidak tahu harus dihentikan proses mana")

    def test_release_allows_reacquire(self):
        with tempfile.TemporaryDirectory() as d, _Chdir(d):
            first = SingleInstanceLock(ACCOUNT)
            first.acquire()
            first.release()
            second = SingleInstanceLock(ACCOUNT)
            self.assertTrue(second.acquire(),
                            "lock tidak bisa diambil setelah dilepas")

    def test_different_accounts_do_not_block_each_other(self):
        """Dua akun berbeda = dua proses sah."""
        with tempfile.TemporaryDirectory() as d, _Chdir(d):
            a = SingleInstanceLock(ACCOUNT)
            b = SingleInstanceLock("0x" + "cd" * 32)
            self.assertTrue(a.acquire())
            self.assertTrue(b.acquire())

    def test_address_case_does_not_create_two_locks(self):
        """`0xAB..` dan `0xab..` akun yang sama, jadi lock-nya satu."""
        with tempfile.TemporaryDirectory() as d, _Chdir(d):
            upper = SingleInstanceLock("0x" + "AB" * 32)
            upper.acquire()
            lower = SingleInstanceLock("0x" + "ab" * 32)
            self.assertFalse(lower.acquire(),
                             "huruf besar vs kecil mendapat lock berbeda "
                             "untuk akun yang sama")


class TestStaleLockIsTakenOver(unittest.TestCase):
    """
    Lock dari proses yang sudah MATI harus diambil alih.

    Proses yang mati mendadak (crash, kill -9, reboot) tidak pernah
    membersihkan apa pun. Kalau lock basi itu tidak ditangani, bot mati
    sekali tidak akan pernah bisa start lagi tanpa intervensi manual.
    """

    def test_stale_lock_from_dead_pid_is_taken_over(self):
        with tempfile.TemporaryDirectory() as d, _Chdir(d):
            path = lock_path_for(ACCOUNT)
            path.write_text("999999\n", encoding="utf-8")
            self.assertFalse(_pid_alive(999999))

            lock = SingleInstanceLock(ACCOUNT)
            self.assertTrue(lock.acquire(),
                            "lock basi dari proses mati tidak diambil "
                            "alihkan — bot tidak akan pernah bisa start "
                            "lagi tanpa intervensi manual")
            self.assertEqual(read_lock_pid(path), os.getpid())

    def test_empty_lock_file_is_taken_over(self):
        """File lock kosong = tidak ada yang bisa dipastikan hidup."""
        with tempfile.TemporaryDirectory() as d, _Chdir(d):
            lock_path_for(ACCOUNT).write_text("", encoding="utf-8")
            self.assertTrue(SingleInstanceLock(ACCOUNT).acquire())

    def test_garbage_lock_file_is_taken_over(self):
        with tempfile.TemporaryDirectory() as d, _Chdir(d):
            lock_path_for(ACCOUNT).write_text("bukan angka",
                                              encoding="utf-8")
            self.assertTrue(SingleInstanceLock(ACCOUNT).acquire())

    def test_live_pid_is_not_taken_over(self):
        """PID yang hidup = lock sah, meski filenya dibuat manual."""
        with tempfile.TemporaryDirectory() as d, _Chdir(d):
            lock_path_for(ACCOUNT).write_text("%d\n" % os.getpid(),
                                              encoding="utf-8")
            self.assertFalse(SingleInstanceLock(ACCOUNT).acquire(),
                             "lock dengan PID hidup diambil alih")


class TestReleaseDoesNotStealOtherProcessLock(unittest.TestCase):
    """`release()` hanya boleh menghapus lock milik sendiri."""

    def test_release_keeps_foreign_lock(self):
        with tempfile.TemporaryDirectory() as d, _Chdir(d):
            path = lock_path_for(ACCOUNT)
            path.write_text("999998\n", encoding="utf-8")

            lock = SingleInstanceLock(ACCOUNT)
            lock._held = True          # simulasikan "saya pegang"
            lock.release()
            self.assertTrue(path.exists(),
                            "release() menghapus lock yang PID-nya bukan "
                            "milik kita — proses lain kehilangan penjaga "
                            "akunnya")


class TestReleaseCommandRefusesWhileBotRuns(unittest.TestCase):
    """`--release-kill-switch` harus menolak saat bot hidup."""

    def _cli(self):
        import run as run_module

        env = {"HYPERLIQUID_ACCOUNT_ADDRESS": ACCOUNT}
        with mock.patch.dict(os.environ, env, clear=False):
            return run_module._cli(["run.py", "--release-kill-switch"])

    def test_refuses_with_exit_code_4_when_bot_holds_lock(self):
        with tempfile.TemporaryDirectory() as d, _Chdir(d):
            lock = SingleInstanceLock(ACCOUNT)
            self.assertTrue(lock.acquire())
            rc = self._cli()
            self.assertEqual(rc, 4,
                             "perintah pelepasan jalan padahal bot aktif")

    def test_allows_when_lock_is_stale(self):
        """Lock basi bukan alasan menolak — itu justru harus diabaikan."""
        with tempfile.TemporaryDirectory() as d, _Chdir(d):
            lock_path_for(ACCOUNT).write_text("999997\n", encoding="utf-8")
            rc = self._cli()
            self.assertNotEqual(rc, 4,
                                "lock basi dianggap bot yang sedang jalan")


class TestBotStartTakesAndReleasesLock(unittest.TestCase):
    """
    Start bot harus benar-benar memegang dan melepas kunci.

    Diuji lewat `main()` supaya yang diperiksa adalah jalur produksi,
    bukan fungsi lock-nya dipanggil manual.
    """

    def _run_main(self, decision=None):
        import run as run_module

        dec = decision or _LiveDecision()
        with mock.patch.dict(
                os.environ, {"HYPERLIQUID_ACCOUNT_ADDRESS": ACCOUNT},
                clear=False), \
             mock.patch.object(run_module, "_choose_mode",
                               return_value=dec), \
             mock.patch.object(run_module, "TradingBotApp",
                               return_value=_App(dec)), \
             mock.patch.object(run_module, "print_banner"), \
             mock.patch.object(run_module, "setup_logger"), \
             mock.patch.object(run_module, "_print_safe"):
            return asyncio.run(run_module.main())

    def test_lock_released_after_bot_stops(self):
        with tempfile.TemporaryDirectory() as d, _Chdir(d):
            self._run_main()
            self.assertFalse(lock_path_for(ACCOUNT).exists(),
                             "lock tidak dilepas setelah bot berhenti — "
                             "instance berikutnya akan mengira masih ada bot "
                             "yang jalan")

    def test_second_start_refuses_when_first_is_running(self):
        with tempfile.TemporaryDirectory() as d, _Chdir(d):
            first = SingleInstanceLock(ACCOUNT)
            self.assertTrue(first.acquire())
            rc = self._run_main()
            self.assertEqual(rc, 4,
                             "instance kedua TIDAK ditolak — dua proses "
                             "akan mengirim order ke akun yang sama")

    def test_paper_mode_does_not_take_lock(self):
        """Dua instance paper hanya mengulang pekerjaan."""

        class _PaperDecision:
            mode = "paper"

        with tempfile.TemporaryDirectory() as d, _Chdir(d):
            self._run_main(_PaperDecision())
            self.assertFalse(lock_path_for(ACCOUNT).exists())