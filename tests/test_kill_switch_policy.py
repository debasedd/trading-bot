"""
tests/test_kill_switch_policy.py — Satu sumber untuk nilai "off".

DEFECT YANG INI TUTUP
--------------------
Nilai yang dianggap "off" untuk `TRADEBOT_LIVE_KILL_SWITCH` tercatat di
DUA tempat, dan keduanya tidak sama:

  * `SafetyGate.__init__`       -> `not in ("0", "false", "no", "off")`
  * `SafetyGate.master_blockers` -> `not in ("", "0", "false", "no")`

Operator yang menyetel `TRADEBOT_LIVE_KILL_SWITCH=off` menghasilkan:

  * `__init__` melepas switch (karana `"off"` ada di daftarnya)
  * `master_blockers` LANGSUNG menyalakan blocker KILL_SWITCH (karana
    `"off"` tidak ada di daftarnya)

Hasilnya: switch terlihat lepas di log, tapi setiap order ditolak — dan
tidak ada satu pesan pun yang bilang release-nya diabaikan. Persis kelas
bug yang paling mahal di sistem ini: UI semu yang terlihat benar.

Yang diuji:

1. Satu konstanta jadi sumber kebenaran; kedua pemakai mengacunya.
2. Semua ejaan "off" berlaku sama di `__init__` DAN `master_blockers`.
3. `""` (tidak di-set) TIDAK sama dengan `"0"`: env yang absen harus
   mengikuti disk, kalau tidak bot yang restart dengan konfigurasi bersih
   diam-diam melepas switch yang sengaja dinyalakan.
4. State file rusak/tak terbaca -> `engaged` (fail closed).
"""

import json
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from core.config import LiveConfig
from trading.live.engine import LiveEngine
from trading.live.safety import DayCounters, SafetyGate


def _env(**extra):
    base = {"TRADEBOT_LIVE": "1", "TRADEBOT_LIVE_CONFIRMED": "1",
            "HYPERLIQUID_PRIVATE_KEY": "0x" + "ab" * 32}
    base.update(extra)
    return base


def _gate(state_path, **env):
    return SafetyGate(LiveConfig(), env=_env(**env),
                      state_path=pathlib.Path(state_path))


def _fresh_path():
    return pathlib.Path(tempfile.mkdtemp()) / "counters.json"


def _engaged_state():
    """File state dengan kill switch aktif."""
    path = _fresh_path()
    counters = DayCounters()
    counters.engaged = True
    counters.save(path)
    return path


class TestOffValuesHaveOneSource(unittest.TestCase):
    """Tidak boleh ada dua daftar "off" yang berbeda."""

    def test_constant_exists(self):
        self.assertTrue(
            hasattr(SafetyGate, "KILL_SWITCH_OFF_VALUES"),
            "SafetyGate harus punya satu konstanta KILL_SWITCH_OFF_VALUES",
        )

    def test_every_off_spelling_never_releases(self):
        """
        Setiap ejaan "off" berlaku SAMA di `__init__` dan
        `master_blockers`, dan tidak satu pun melepas switch.

        Test lama di sini mengharapkan `assertFalse(gate.engaged)` —
        itumenguji perilaku yang sekarang DIHAPUS. Env tidak lagi
        bisa melepas; pelepasannya pindah ke `operator_release()`.
        """
        for value in ("0", "false", "no", "off"):
            with self.subTest(value=value):
                gate = _gate(_engaged_state(),
                             TRADEBOT_LIVE_KILL_SWITCH=value)
                self.assertTrue(
                    gate.engaged,
                    "env=%r melepas kill switch — env hanya boleh MENYALAKAN"
                    % value)

                blockers = [b.value for b in gate.master_blockers()]
                self.assertIn(
                    "kill switch aktif", blockers,
                    "env=%r: __init__ menahan switch tapi master_blockers "
                    "tidak memblokir — dua daftar 'off' yang berbeda"
                    % value)

    def test_on_values_engage_everywhere(self):
        """Setiap ejaan "on" menyalakan di kedua tempat."""
        for value in ("1", "true", "yes"):
            with self.subTest(value=value):
                gate = _gate(_fresh_path(), TRADEBOT_LIVE_KILL_SWITCH=value)
                self.assertTrue(gate.engaged,
                                "env=%r tidak menyalakan kill switch" % value)
                blockers = [b.value for b in gate.master_blockers()]
                self.assertIn("kill switch aktif", blockers,
                              "env=%r menyala di __init__ tapi "
                              "master_blockers tidak memblokir" % value)

    def test_unset_follows_disk_not_off(self):
        """
        Env yang TIDAK di-set harus mengikuti disk.

        Keadaan ketiga. Kalau tidak di-set diperlakukan sebagai "off",
        bot yang restart dengan konfigurasi bersih akan diam-diam melepas
        switch yang sengaja dinyalakan.
        """
        gate = _gate(_engaged_state())  # tanpa env kill switch sama sekali
        self.assertTrue(
            gate.engaged,
            "env tidak di-set harus mengikuti disk, bukan melepas switch")
        blockers = [b.value for b in gate.master_blockers()]
        self.assertIn("kill switch aktif", blockers)

    def test_master_blockers_consults_the_constant_not_a_literal(self):
        """
        `master_blockers()` harus membaca KONSTANTA, bukan daftar literal.

        Mutasi "kembalikan daftar `("", "0", "false", "no")` sendiri"
        survived setelah env tidak lagi bisa melepas switch: karena
        `engaged` selalu True dalam semua test di sini, blocker
        `KILL_SWITCH` muncul dari `self.engaged` APA pun isi daftar di
        `master_blockers`. Dua daftar identik secara perilaku — jadi test
        berbasis perilaku tidak bisa membedakannya.

        Test ini menutup celah itu dengan MENGUBAH konstantanya: kalau
        `master_blockers()` memakai literal, nilai tambahan itu tidak
        berpengaruh; kalau memakai konstanta,perbedaannya langsung
        kelihatan.
        """
        sentinel = "off-juga-untuk-tes"
        original = SafetyGate.KILL_SWITCH_OFF_VALUES
        try:
            SafetyGate.KILL_SWITCH_OFF_VALUES = frozenset(
                set(original) | {sentinel})
            # Gate yang TIDAK engaged, env = nilai sentinel. Kalau daftar
            # off dibaca, ini tidak menyalakan blocker.
            gate = _gate(_fresh_path(), TRADEBOT_LIVE_KILL_SWITCH=sentinel)
            blockers = [b.value for b in gate.master_blockers()]
            self.assertNotIn(
                "kill switch aktif", blockers,
                "master_blockers tidak membaca KILL_SWITCH_OFF_VALUES — "
                "ia memakai daftar literal sendiri")
        finally:
            SafetyGate.KILL_SWITCH_OFF_VALUES = original

    def test_constant_matches_observed_behaviour(self):
        """Kontrol: konstanta harus sama dengan yang diamati."""
        observed_off = []
        for value in ("0", "false", "no", "off"):
            gate = _gate(_engaged_state(), TRADEBOT_LIVE_KILL_SWITCH=value)
            if not gate.engaged:
                observed_off.append(value)

        declared = set(SafetyGate.KILL_SWITCH_OFF_VALUES)
        for value in observed_off:
            self.assertIn(value, declared,
                          "%r dilepas di __init__ tapi tidak ada di "
                          "konstanta" % value)


class TestUnreadableStateEngagesKillSwitch(unittest.TestCase):
    """State rusak = tidak bisa dipercaya = fail closed."""

    def test_corrupt_json_engages(self):
        """
        File state rusak harus menyalakan switch.

        Sebelumnya hanya menambah blocker `COUNTER_STATE_UNREADABLE`:
        order diblokir TAPI `engaged` tetap False. Konsekuensinya
        `health_check()` menulis `kill_switch: False` sementara gerbang
        sebenarnya menolak — laporan yang berlawanan dengan kenyataan.
        """
        path = _fresh_path()
        path.write_text("{ini bukan json", encoding="utf-8")

        gate = _gate(path)
        self.assertTrue(
            gate.engaged,
            "file state rusak tidak menyalakan kill switch — fail closed "
            "membedakan 'tidak bisa dipercaya' dari 'sehat'")

    def test_wrong_types_engage(self):
        """Isi dengan tipe salah juga tidak bisa dipercaya."""
        path = _fresh_path()
        path.write_text(
            json.dumps({"day_utc": "2026-10-04",
                        "orders_sent": "bukan int",
                        "realized_pnl": [], "consecutive_errors": 0,
                        "engaged": False}),
            encoding="utf-8")

        gate = _gate(path)
        self.assertTrue(gate.engaged,
                        "tipe salah tidak menyalakan switch")

    def test_unreadable_state_still_blocks_orders(self):
        """Fail closed memblokir order, bukan hanya menaikkan flag."""
        path = _fresh_path()
        path.write_text("{rusak", encoding="utf-8")
        gate = _gate(path)
        blockers = [b.value for b in gate.master_blockers()]
        self.assertIn("kill switch aktif", blockers)

    def test_readable_flat_state_does_not_engage(self):
        """
        Kontrol: fail closed tidak boleh jadi "selalu menyala".

        State yang terbaca dan switch tidak aktif harus membiarkan bot
        jalan. Kalau test ini hilang, kill switch tidak akan pernah bisa
        dilepas dan seluruh sistem jadi tidak berguna.
        """
        path = _fresh_path()
        counters = DayCounters()
        counters.day_utc = "2026-10-04"
        counters.save(path)

        gate = _gate(path)
        self.assertFalse(gate.engaged,
                         "state yang sehat tidak boleh menyalakan switch")


class TestHealthCheckKeepsWatchingWhileEngaged(unittest.TestCase):
    """
    Kill switch menghentikan ORDER, bukan PENGLIHATAN.

    `health_check()` sebelumnya `return` begitu switch menyala. Reads-
    only reconciliation dan alert ikut berhenti — persis ketika posisi
    paling mungkin berubah, yaitu saat ada yang memasang trigger-nya atau
    operator melakukan order manual di luar bot.

    Yang harus tetap berlaku:
      * bursa tetap dibaca
      * divergensi tetap dilaporkan di `problems`
      * `ok` tetap False
      * switch TIDAK dinyalakan ulang (hanya menambah noise)
    """

    def _engine(self, remote_coins=(), local_coins=()):
        import asyncio as _asyncio

        from trading.live.engine import LiveEngine, LivePosition

        engine = LiveEngine.__new__(LiveEngine)
        from core.config import LiveConfig
        engine.cfg = LiveConfig()
        engine.positions = {
            c: LivePosition(symbol=c, coin=c, side="LONG", size=0.5,
                            entry_price=100.0, stop_loss=0.0,
                            take_profit=0.0, leverage=5)
            for c in local_coins
        }

        class _Exchange:
            query_address = "0x5972698398d8c5bbe67c0db74906236691020417"

            def get_account_state(self):
                return {"assetPositions": [
                    {"type": "oneWay",
                     "position": {"coin": c, "szi": "0.5",
                                  "entryPx": "100"}}
                    for c in remote_coins],
                    "marginSummary": {"accountValue": "1000",
                                      "withdrawable": "1000"}}

            def open_orders(self):
                return []

        engine.exchange = _Exchange()

        class _Info:
            def meta(self):
                return {"universe": [{"name": "BTC"}, {"name": "ETH"}]}

            def user_fills(self, *a, **kw):
                return []

            def frontend_open_orders(self, *a, **kw):
                return []

        engine.exchange.info = _Info()
        return engine

    def test_still_reads_exchange_while_engaged(self):
        """Bursa tetap dibaca meski switch menyala."""
        import asyncio as _asyncio

        eng = self._engine(remote_coins=("BTC",))
        eng.gate = _gate(_engaged_state())
        self.assertTrue(eng.gate.engaged, "switch harus menyala sebelum tes")

        with patch.object(LiveEngine, "poll_exchange_fills",
                          new=_async_empty_fills()):
            health = _asyncio.run(eng.health_check())

        self.assertTrue(health["reachable"],
                        "bursa tidak dibaca saat switch menyala — bot jadi "
                        "buta tepat saat posisi paling mungkin berubah")
        self.assertFalse(health["ok"])

    def test_still_reports_divergence_while_engaged(self):
        """
        Divergensi yang terjadi SETELAH switch menyala harus terlihat.

        Ini inti perubahan: switch sudah aktif, tapi posisi lokal dan bursa
        tetap dibandingkan, dan selisihnya muncul di `problems`.
        """
        import asyncio as _asyncio

        eng = self._engine(remote_coins=("ETH",), local_coins=("BTC",))
        eng.gate = _gate(_engaged_state())

        with patch.object(LiveEngine, "poll_exchange_fills",
                          new=_async_empty_fills()):
            health = _asyncio.run(eng.health_check())

        joined = " | ".join(health["problems"])
        self.assertIn("kill switch aktif", joined)
        self.assertTrue(
            "ETH" in joined or "BTC" in joined,
            "divergensi posisi tidak dilaporkan saat switch menyala: %r"
            % health["problems"],
        )

    def test_does_not_re_engage(self):
        """Switch yang sudah aktif tidak boleh dinyalakan ulang."""
        import asyncio as _asyncio

        eng = self._engine(remote_coins=("BTC",), local_coins=("BTC",))
        eng.gate = _gate(_engaged_state())
        engaged_calls = []
        eng.gate.engage_kill_switch = lambda r: engaged_calls.append(r)

        with patch.object(LiveEngine, "poll_exchange_fills",
                          new=_async_empty_fills()):
            _asyncio.run(eng.health_check())

        self.assertEqual(engaged_calls, [],
                         "switch yang sudah menyala dinyalakan ulang — "
                         "itu hanya menambah log, bukan informasi")


class TestEnvCannotReleaseKillSwitch(unittest.TestCase):
    """
    Env HANYA bisa menyalakan. Tidak pernah melepas.

    Pelepasan lewat env adalah pelepasan yang tidak terlihat: bisa terjadi
    karena salah ketik, karena cronjob menyalin environment, atau karena
    restart proses. Tidak ada jejak siapa yang melepas dan kenapa.
    """

    def test_off_value_does_not_release_persisted_state(self):
        """
        `TRADEBOT_LIVE_KILL_SWITCH=0` tidak boleh melepas switch disk.

        Inilah cacat aslinya: nilai "off" diperlakukan sebagai LEPAS, jadi
        satu ketikan di shell mematikan proteksi yang sengaja dinyalakan.
        """
        gate = _gate(_engaged_state(), TRADEBOT_LIVE_KILL_SWITCH="0")
        self.assertTrue(
            gate.engaged,
            "env=0 melepas kill switch yang tersimpan di disk — proteksi "
            "yang sengaja dinyalakan hilang karena satu ketikan")

    def test_no_off_value_releases_persisted_state(self):
        for value in ("0", "false", "no", "off"):
            with self.subTest(value=value):
                gate = _gate(_engaged_state(),
                             TRADEBOT_LIVE_KILL_SWITCH=value)
                self.assertTrue(gate.engaged, "env=%r melepas switch" % value)

    def test_on_value_still_engages(self):
        """Env harus tetap bisa MENYALAKAN — itu satu-satunya fungsinya."""
        gate = _gate(_fresh_path(), TRADEBOT_LIVE_KILL_SWITCH="1")
        self.assertTrue(gate.engaged)

    def test_disk_state_survives_restart(self):
        """
        State switch harus bertahan melewati restart.

        Kalau switch tidak bertahan, mematikan bot menjadi "perbaikan":
        bot yang restart otomatis karena crash kembali dengan limit yang
        baru saja meledak.
        """
        path = _engaged_state()
        first = _gate(path, TRADEBOT_LIVE_KILL_SWITCH="0")
        self.assertTrue(first.engaged)
        second = _gate(path)  # proses baru, env bersih
        self.assertTrue(second.engaged,
                        "kill switch tidak bertahan melewati restart")


class TestOperatorReleaseRequiresAllFour(unittest.TestCase):
    """Empat syarat, semuanya wajib."""

    def _engaged_gate(self):
        return _gate(_engaged_state())

    def test_all_conditions_met_releases(self):
        gate = self._engaged_gate()
        ok = gate.operator_release(
            reason="divergensi sudah dikonfirmasi hilang",
            typed_confirmation=SafetyGate.RELEASE_CONFIRMATION_PHRASE,
            reconcile_clean=True)
        self.assertTrue(ok)
        self.assertFalse(gate.engaged)

    def test_wrong_confirmation_raises(self):
        gate = self._engaged_gate()
        for bad in ("", "ya", "SAYA SUDAH PERIKSA PENYEBABNYA", "lantai"):
            with self.subTest(typed=bad):
                with self.assertRaises(PermissionError):
                    gate.operator_release(reason="alasan",
                                          typed_confirmation=bad,
                                          reconcile_clean=True)
                self.assertTrue(gate.engaged,
                                "switch terlepas meski konfirmasi salah")

    def test_empty_reason_raises(self):
        gate = self._engaged_gate()
        for bad in ("", "   "):
            with self.subTest(reason=bad):
                with self.assertRaises(ValueError):
                    gate.operator_release(
                        reason=bad,
                        typed_confirmation=SafetyGate.RELEASE_CONFIRMATION_PHRASE,
                        reconcile_clean=True)
                self.assertTrue(gate.engaged)

    def test_dirty_reconcile_raises(self):
        """
        Rekonsiliasi kotor = JANGAN lepas.

        Melepas switch dengan posisi lokal dan bursa tidak cocok mengembalikan
        bot ke order dengan keyakinan salah — persis kondisi yang memicu
        switch di tempat pertama.
        """
        gate = self._engaged_gate()
        with self.assertRaises(RuntimeError):
            gate.operator_release(
                reason="alasan",
                typed_confirmation=SafetyGate.RELEASE_CONFIRMATION_PHRASE,
                reconcile_clean=False)
        self.assertTrue(gate.engaged, "switch terlepas padahal reconcile kotor")

    def test_release_when_not_engaged_returns_false(self):
        gate = _gate(_fresh_path())
        self.assertFalse(gate.operator_release(
            reason="alasan",
            typed_confirmation=SafetyGate.RELEASE_CONFIRMATION_PHRASE,
            reconcile_clean=True))


class TestReleaseAuditLog(unittest.TestCase):
    """Audit log mencatat waktu, alasan, state sebelum/sesudah, commit."""

    def _audit_lines(self, gate):
        if not gate.audit_path.exists():
            return []
        return [json.loads(line) for line in
                gate.audit_path.read_text(encoding="utf-8").splitlines()
                if line.strip()]

    def test_successful_release_is_recorded(self):
        gate = _gate(_engaged_state())
        gate.operator_release(
            reason="divergensi sudah hilang",
            typed_confirmation=SafetyGate.RELEASE_CONFIRMATION_PHRASE,
            reconcile_clean=True)
        records = self._audit_lines(gate)
        self.assertEqual(len(records), 1, "audit log harus punya satu record")
        rec = records[0]
        self.assertEqual(rec["event"], "kill_switch_release")
        self.assertEqual(rec["outcome"], "released")
        self.assertEqual(rec["reason"], "divergensi sudah hilang")
        self.assertTrue(rec["engaged_before"])
        self.assertFalse(rec["engaged_after"])
        self.assertIn("at_utc", rec)
        self.assertIn("commit", rec)

    def test_not_engaged_request_is_recorded(self):
        gate = _gate(_fresh_path())
        gate.operator_release(
            reason="coba-coba",
            typed_confirmation=SafetyGate.RELEASE_CONFIRMATION_PHRASE,
            reconcile_clean=True)
        records = self._audit_lines(gate)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["outcome"], "ignored_not_engaged")
        self.assertFalse(records[0]["engaged_before"])

    def test_audit_path_follows_state_path(self):
        """
        Audit log mengikuti `state_path`.

        Kalau tidak, test yang mengarahkan state ke tmp_path akan menulis
        audit ke file produksi.
        """
        state = pathlib.Path(tempfile.mkdtemp()) / "c.json"
        gate = _gate(state)
        self.assertEqual(gate.audit_path.parent, state.parent,
                         "audit log tidak mengikuti state_path")
        self.assertEqual(gate.audit_path.suffix, ".jsonl")

    def test_audit_records_do_not_contain_private_key(self):
        """
        Audit log tidak boleh memuat private key.

        File audit dibaca manusia dan sering ditempel di issue.
        """
        gate = _gate(_engaged_state())
        gate.operator_release(
            reason="alasan uji",
            typed_confirmation=SafetyGate.RELEASE_CONFIRMATION_PHRASE,
            reconcile_clean=True)
        blob = gate.audit_path.read_text(encoding="utf-8")
        self.assertNotIn("0x" + "ab" * 32, blob,
                         "private key bocor ke audit log")


def _async_empty_fills():
    """`poll_exchange_fills` async yang mengembalikan daftar kosong."""
    async def _stub(self):
        return []
    return _stub


if __name__ == "__main__":
    unittest.main()