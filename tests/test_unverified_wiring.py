"""
tests/test_unverified_wiring.py — Tracker HARUS terpakai oleh `run_loop`.

`UnverifiedTracker` sudah ada dan policy-nya sudah diuji, tapi kalau
tidak dipanggil dari jalur produksi maka seluruh policy itu tidak berarti.
`test_unverified_policy.py` menguji kelasnya; file ini menguji
SAMBUNGANNYA.

TIGA HAL YANG HARUS TERBUKTI
----------------------------
1. Satu kegagalan membaca -> order baru DIJEDA (kill switch belum).
2. Tiga kegagalan berturut-turut ATAU lewat 5 menit -> kill switch engaged.
3. Pemulihan (bursa terbaca lagi) -> jeda hilang.

CARANYA MENGUJINYA
------------------
Bursa tiruan dipasang di batas SDK (`LiveExchange` digantikan objek yang
mengontrol `reachable`), lalu helper yang dipanggil `run_loop`
dipanggil dengan urutan nyata dan hasilnya diperiksa di state publik:
`unverified` dan `gate.engaged`.

Stub TIDAK dipakai untuk tracker: kelas yang diuji adalah kelas produksi
yang sama persis dengan yang diimpor `run_loop`.

Test di `TestTrackerIsActuallyUsedByProduction` memeriksa SAMBUNGANNYA
di kode produksi, karena test lain memanggil helper secara langsung —
dan akan tetap hijau kalau `run_loop` berhenti memanggilnya.
"""

import pathlib
import sys
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from core.config import LiveConfig
from trading.live.safety import UnverifiedTracker

from repro_helpers import clean_gate


class _ExchangeDouble:
    """
    Bursa tiruan di batas SDK.

    `reachable` dikontrol supaya test bisa membuat bursa "tidak terbaca"
    tanpa jaringan. Yang diuji bukan bursa — yang diuji adalah apa yang
    dilakukan terhadap keadaan "tidak terbaca".
    """

    def __init__(self, reachable=True):
        self.reachable = reachable
        self.query_address = "0x5972698398d8c5bbe67c0db74906236691020417"
        self.address = self.query_address
        self.base_url = "https://api.hyperliquid-testnet.xyz/info"

    def get_account_state(self):
        if not self.reachable:
            raise ConnectionError("bursa tidak terjangkau (tiruan test)")
        return {"assetPositions": [],
                "marginSummary": {"accountValue": "1000",
                                  "withdrawable": "1000"}}

    def positions(self):
        return []

    def open_orders(self):
        return []

    def free_collateral(self):
        return 1000.0

    def total_notional(self):
        return 0.0

    def symbol_notional(self, coin):
        return 0.0

    def reverify_agent_if_due(self):
        """Verifikasi agent selalu sukses — test ini murni soal bursa."""
        return False

    @property
    def info(self):
        outer = self

        class _Info:
            def meta(self):
                if not outer.reachable:
                    raise ConnectionError("meta gagal")
                return {"universe": [{"name": "BTC", "szDecimals": 5,
                                      "maxLeverage": 25}]}

            def user_fills(self, *a, **kw):
                return []

            def frontend_open_orders(self, *a, **kw):
                return []

            def extra_agents(self, user):
                return []

            def spot_user_state(self, user):
                return {"balances": [{"coin": "USDC", "total": "1000",
                                      "hold": "0"}]}

        return _Info()


def _engine(reachable=False):
    from trading.live.engine import LiveEngine

    engine = LiveEngine.__new__(LiveEngine)
    engine.cfg = LiveConfig()
    engine.gate = clean_gate()
    engine.exchange = _ExchangeDouble(reachable=reachable)
    engine.positions = {}
    engine.uncertain_orders = {}
    engine.unverified = UnverifiedTracker(cfg=engine.cfg)
    return engine


class TestOneFailurePausesButDoesNotStop(unittest.TestCase):
    """Satu kegagalan: jeda order baru, kill switch BELUM menyala."""

    def test_single_failure_pauses(self):
        eng = _engine()
        eng._note_unverified("bursa tidak terbaca")
        self.assertTrue(eng.unverified.is_paused(),
                        "satu kegagalan tidak menjeda order baru")
        self.assertFalse(eng.gate.engaged,
                         "satu kegagalan menyalakan kill switch — itu "
                         "mematikan bot yang mungkin hanya experiencing "
                         "satu timeout")

    def test_two_failures_still_no_kill_switch(self):
        eng = _engine()
        eng._note_unverified("gagal 1")
        eng._note_unverified("gagal 2")
        self.assertEqual(eng.unverified.consecutive, 2)
        self.assertTrue(eng.unverified.is_paused())
        self.assertFalse(eng.gate.engaged,
                         "dua kegagalan menyalakan kill switch")

    def test_backoff_follows_config(self):
        eng = _engine()
        eng._note_unverified("gagal 1")
        self.assertEqual(eng._unverified_delay(5.0), 10.0)
        eng._note_unverified("gagal 2")
        self.assertEqual(eng._unverified_delay(5.0), 30.0)
        eng._note_unverified("gagal 3")
        self.assertEqual(eng._unverified_delay(5.0), 60.0)

    def test_backoff_never_returns_zero(self):
        """
        Backoff habis harus tetap menunggu, tidak retry tanpa jeda.

        Retry tanpa jeda membanjiri bursa tepat saat ia sedang menolak.
        """
        eng = _engine()
        for _ in range(5):
            eng._note_unverified("gagal")
        delay = eng._unverified_delay(5.0)
        self.assertGreaterEqual(delay, 1.0,
                                "delay %r — retry tanpa jeda" % delay)


class TestThreeFailuresEngageKillSwitch(unittest.TestCase):
    """Tiga kegagalan berturut-turut: kill switch menyala."""

    def test_three_consecutive_engages(self):
        eng = _engine()
        eng._note_unverified("gagal 1")
        eng._note_unverified("gagal 2")
        eng._note_unverified("gagal 3")
        self.assertTrue(eng.gate.engaged,
                        "tiga kegagalan berturut-turut tidak menyalakan "
                        "kill switch")

    def test_five_minutes_engages_without_three(self):
        """
        Batas waktu menangkap kegagalan yang JARANG.

        Dua kegagalan saja tidak cukup, tapi kalau jaraknya lebih dari 5
        menit, bot sudah tidak bisa memastikan selama itu lama — lebih
        buruk daripada tiga kegagalan beruntun.
        """
        eng = _engine()
        eng._note_unverified("gagal 1")
        # Paksa waktu meloncat: pura-pura yang pertama sudah lama lalu.
        eng.unverified.first_failure_at = (
            time.time() - eng.cfg.unverified_max_seconds - 1)
        eng._note_unverified("gagal 2")
        self.assertTrue(eng.gate.engaged,
                        "kegagalan jarang tapi bertahan lama tidak "
                        "menyalakan kill switch")

    def test_success_between_failures_resets_streak(self):
        """
        Gagal, pulih, gagal = streak 1, bukan 2.

        Tanpa reset, dua gangguan yang terpisah enam jam tetap dihitung
        beruntun dan mematikan bot yang sebenarnya sehat.
        """
        eng = _engine()
        eng._note_unverified("gagal 1")
        eng._note_verified()
        eng._note_unverified("gagal 2")
        self.assertEqual(eng.unverified.consecutive, 1,
                         "streak tidak direset setelah pemulihan")
        self.assertFalse(eng.gate.engaged)


class TestRecoveryClearsPause(unittest.TestCase):
    """Bursa pulih: jeda harus hilang."""

    def test_recovery_clears_pause(self):
        eng = _engine()
        eng._note_unverified("gagal")
        self.assertTrue(eng.unverified.is_paused())
        eng._note_verified()
        self.assertFalse(eng.unverified.is_paused(),
                         "jeda tidak hilang setelah bursa pulih — bot "
                         "tidak akan pernah mengirim order lagi")
        self.assertEqual(eng.unverified.consecutive, 0)
        self.assertIsNone(eng.unverified.first_failure_at)

    def test_recovery_while_engaged_does_not_clear_kill_switch(self):
        """
        Pulih dari jeda TIDAK melepas kill switch.

        Kill switch adalah keputusan operator. Bot yang "pulih sendiri"
        berarti bot yang restarting karena bug kembali tanpa benar-benar
        memperbaiki apa pun.
        """
        eng = _engine()
        for _ in range(3):
            eng._note_unverified("gagal")
        self.assertTrue(eng.gate.engaged)
        eng._note_verified()
        self.assertFalse(eng.unverified.is_paused())
        self.assertTrue(eng.gate.engaged,
                        "pemulihan melepas kill switch tanpa perintah "
                        "operator")


class TestTrackerIsActuallyUsedByProduction(unittest.TestCase):
    """
    Kontrol yang menutup gap: tracker dipakai jalur produksi.

    Kalau `run_loop` berhenti memanggil `_note_unverified`, semua test di
    atas masih hijau karena mereka memanggil helper-nya langsung. Test
    ini memeriksa SAMBUNGANNYA di kode produksi.
    """

    def test_run_loop_calls_the_helper(self):
        import inspect

        from trading.live.engine import LiveEngine

        src = inspect.getsource(LiveEngine.run_loop)
        self.assertIn("_note_unverified", src,
                      "run_loop tidak memanggil _note_unverified — policy "
                      "tidak terhubung ke jalur produksi")
        self.assertIn("_note_verified", src,
                      "run_loop tidak memanggil _note_verified — jeda "
                      "tidak pernah hilang")

    def test_run_loop_uses_backoff(self):
        import inspect

        from trading.live.engine import LiveEngine

        src = inspect.getsource(LiveEngine.run_loop)
        self.assertIn("_unverified_delay", src,
                      "run_loop tidak memakai backoff saat dijeda — retry "
                      "tanpa jeda membanjiri bursa")

    def test_engine_has_a_tracker_field(self):
        from trading.live.engine import LiveEngine

        self.assertIn("unverified", LiveEngine.__dataclass_fields__,
                      "LiveEngine tidak punya field unverified")


if __name__ == "__main__":
    unittest.main()