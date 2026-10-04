"""
tests/test_risk_manager_injection.py — Risiko harus lewat parameter, bukan singleton.

MASALAH
-------
`RiskManager.__init__` (trading/risk_manager.py:56-57) mengambil
`get_config().risk` dan `get_config().fees`. `get_config()` (core/config.py:1029)
adalah SINGLETON yang meng-cache `_config` di level modul.

Konsekuensinya nyata dan bukan teoretis: setiap `RiskManager` yang dibuat
setelah test lain mengubah config global mewarisi config tersebut. Test
akuntansi fee dan circuit breaker ikut menguji apa yang ditulis test lain —
persis kelas kegagalan yang biasanya sembunyikan pencemar urutan.

Dua hal yang sering tertukar dan sengaja dipisah di sini:

  * Batas yang diuji adalah `risk.max_daily_loss` dan `fees.taker`, bukan
    `max_leverage`. Jadi test ini tidak bisa lulus karena perubahan config
    yang sah dan tidak terkait.

  * Injeksi lewat parameter harus jadi jalur UTAMA. Constructor tanpa
    argumen tetap ada untuk pemanggil yang tidak punya alasan menyuntik,
    tapi jalur yang diuji di file ini adalah jalur injeksi.

Yang DIPERIKSA
--------------
  1. `RiskManager(config=...)` memakai config yang disuntik, bukan singleton.
  2. Dua RiskManager dengan config berbeda hidup berdampingan tanpa saling
     menimpa — tidak ada state bersama di level modul.
  3. Mengubah singleton SETELAH konstruksi tidak mengubah RiskManager yang
     sudah dibuat (sudah di-snapshot di __init__, bukan dibaca malas).
  4. Nilai default tetap sama dengan yang dibaca singleton, supaya
     perubahan ini tidak diam-diam mengubah perilaku produksi.
"""
import unittest

from core.config import AppConfig, get_config
from trading.risk_manager import RiskManager


class TestRiskManagerConfigInjection(unittest.TestCase):
    """Config masuk lewat parameter, bukan lewat singleton global."""

    def test_injected_config_is_the_one_used(self):
        """
        Bukti langsung: taker fee dari config yang disuntik yang dipakai.

        `calculate_fee` adalah jalur di mana tarif config benar-benar
        mengubah angka, jadi ini assertion yang benar-benar menguji
        percabangan mana yang dibaca — bukan cuma memeriksa atribut.
        """
        cfg = AppConfig()
        cfg.fees.taker = 0.007
        cfg.fees.maker = 0.0009

        rm = RiskManager(10_000.0, config=cfg)

        # 100 * 0.007 = 0.7
        self.assertAlmostEqual(rm.calculate_fee(100.0, 1.0), 0.7, places=9)
        self.assertAlmostEqual(rm.calculate_fee(100.0, 1.0, "limit"), 0.09, places=9)

    def test_injected_config_survives_singleton_change(self):
        """
        Config singleton diubah SESUDAH RiskManager dibuat.

        Kalau RiskManager membaca `get_config()` malas di setiap panggilan,
        test ini gagal dan breaker-nya berubah diam-diam saat test lain
        menulis `_config`. `max_daily_loss` yang jadi penyebut daily-loss
        breaker adalah contoh langsung: menyala atau tidak, angka di
        frontend ikut bergeser tanpa ada yang mengubah kode.
        """
        cfg = AppConfig()
        cfg.risk.max_daily_loss = 0.90  # longgar: -9999/10000 = 99.9% > 90% -> TOLAK

        rm = RiskManager(10_000.0, config=cfg)
        baseline = rm.validate_trade(
            balance=10000.0, margin_required=100.0,
            open_positions=0, daily_pnl=-5000.0,
        )
        self.assertTrue(baseline["allowed"], baseline["reasons"])

        # Sekarang singleton dibuat JAUH lebih ketat: 1e-9 = 0.0000001%.
        singleton = get_config()
        original = singleton.risk.max_daily_loss
        try:
            singleton.risk.max_daily_loss = 1e-9
            after = rm.validate_trade(
                balance=10000.0, margin_required=100.0,
                open_positions=0, daily_pnl=-5000.0,
            )
            # RiskManager yang menyuntik config TIDAK boleh ikut berubah.
            self.assertTrue(
                after["allowed"],
                "RiskManager membaca singleton, bukan config yang disuntik",
            )
            # Sanity: singleton yang disetel ketat MEMANG akan menolak,
            # jadi test ini tidak lulus karena batasnya tidak berlaku.
            strict = RiskManager(10_000.0)
            self.assertFalse(strict.validate_trade(
                balance=10000.0, margin_required=100.0,
                open_positions=0, daily_pnl=-5000.0,
            )["allowed"])
        finally:
            singleton.risk.max_daily_loss = original

    def test_two_managers_do_not_share_state(self):
        """
        Dua instance dengan config berbeda hidup berdampingan.

        Kalau ada cache atau state di level modul, manager kedua akan
        menimpa angka manager pertama dan test suite jadi punya hasil
        yang bergantung urutan — pencemaran dengan nama lain.
        """
        longge = AppConfig()
        longge.risk.max_daily_loss = 0.90
        longge.fees.taker = 0.001

        ketat = AppConfig()
        ketat.risk.max_daily_loss = 1e-9
        ketat.fees.taker = 0.05

        a = RiskManager(10_000.0, config=longge)
        b = RiskManager(10_000.0, config=ketat)

        # B yang ketat harus menolak; A yang longgar harus menerima.
        # daily_pnl = -5000 -> 50% dari modal awal.
        # Longgar (batas 90%) harus lolos, ketat (batas 1e-9) harus ditolak.
        kw = dict(balance=10000.0, margin_required=100.0,
                  open_positions=0, daily_pnl=-5000.0)
        self.assertTrue(a.validate_trade(**kw)["allowed"], a.validate_trade(**kw)["reasons"])
        self.assertFalse(b.validate_trade(**kw)["allowed"], "config ketat harus menolak")

        # Dan tarif fee keduanya tetap milik masing-masing.
        self.assertAlmostEqual(a.calculate_fee(100.0, 1.0), 0.1, places=9)
        self.assertAlmostEqual(b.calculate_fee(100.0, 1.0), 5.0, places=9)

    def test_default_matches_singleton(self):
        """
        Tanpa argumen, hasilnya HARUS sama dengan membaca singleton.

        Ini yang membuat perubahan injeksi tidak diam-diam mengubah
        perilaku produksi: jalur lama jadi jalur eksplisit yang angkanya
        sudah dibuktikan sama.
        """
        default = RiskManager(10_000.0)
        singleton = get_config()

        self.assertAlmostEqual(
            default.calculate_fee(100.0, 1.0),
            100.0 * singleton.fees.taker,
            places=12,
        )
        self.assertEqual(
            default.config.max_daily_loss, singleton.risk.max_daily_loss
        )
        self.assertEqual(
            default.config.max_open_positions, singleton.risk.max_open_positions
        )

    def test_no_reference_to_global_config_in_attributes(self):
        """
        Atribut RiskManager tidak boleh menyimpan rujukan ke singleton.

        Kalau `self.config` adalah objek yang sama dengan `get_config().risk`,
        test mana pun yang menulis ke sana lewat atribut managers ini akan
        diam-diam mengubah config global.
        """
        cfg = AppConfig()
        rm = RiskManager(10_000.0, config=cfg)

        self.assertIsNot(rm.config, get_config().risk)
        self.assertIsNot(rm.fees, get_config().fees)
        self.assertIs(rm.config, cfg.risk)


if __name__ == "__main__":
    unittest.main()