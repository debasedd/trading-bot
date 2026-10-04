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

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from core.config import LiveConfig
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

    def test_every_off_spelling_is_accepted_everywhere(self):
        """
        Setiap ejaan "off" harus berlaku sama di kedua tempat.

        Ini test yang menangkap defect aslinya: `"off"` ada di `__init__`
        tapi tidak di `master_blockers`.
        """
        for value in ("0", "false", "no", "off"):
            with self.subTest(value=value):
                gate = _gate(_engaged_state(),
                             TRADEBOT_LIVE_KILL_SWITCH=value)
                self.assertFalse(
                    gate.engaged,
                    "env=%r tidak melepas kill switch di __init__" % value)

                blockers = [b.value for b in gate.master_blockers()]
                self.assertNotIn(
                    "kill switch aktif", blockers,
                    "env=%r melepas switch di __init__ tapi "
                    "master_blockers tetap memblokir — dua daftar 'off' "
                    "yang berbeda" % value)

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


if __name__ == "__main__":
    unittest.main()