"""
tests/test_unverified_policy.py — Kebijakan "tidak bisa dipastikan".

DUA KONDISI YANG TIDAK BOLEH DICAMPUR
--------------------------------------
**Divergensi terkonfirmasi**: posisi lokal dan bursa berbeda, keduanya
terbaca. Kita TAHU posisinya salah -> kill switch langsung, tanpa retry.

**Tidak bisa memastikan**: bursa tidak terbaca, rate limit, respons tak
terduga, state lokal rusak. Kita TIDAK tahu apa pun -> jeda order baru,
coba ulang dengan backoff, alert, lalu naik ke kill switch setelah N
kegagalan berturut-turut ATAU lewat batas waktu.

Mencampur keduanya berbahaya di dua arah. Kill switch untuk kondisi
transient mematikan bot yang sehat dan melatih operator menekan tombol
yang seharusnya jarang dipakai — jadi saat divergensi NYATA terjadi,
tidak ada yang merespons. Sebaliknya, menunda divergensi hanya menambah
eksposur yang tidak dipantau.

KEBIJUKAN YANG DIJADIKAN KONFIGURASI
------------------------------------
`unverified_retry_backoff` (10/30/60 detik),
`unverified_max_consecutive` (3), `unverified_max_seconds` (300).

Batas waktu terpisah dari streak karena keduanya menangkap hal berbeda:
streak menangkap kegagalan berdekatan, batas waktu menangkap kegagalan
yang jarang tapi terus-menerus.
"""

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from core.config import LiveConfig


class TestPolicyConfiguration(unittest.TestCase):
    """Angka policy harus di config, tidak ditulis di logika."""

    def test_backoff_is_ten_thirty_sixty(self):
        cfg = LiveConfig()
        self.assertEqual(tuple(cfg.unverified_retry_backoff),
                         (10.0, 30.0, 60.0))

    def test_consecutive_limit_is_three(self):
        self.assertEqual(LiveConfig().unverified_max_consecutive, 3)

    def test_time_limit_is_five_minutes(self):
        self.assertEqual(LiveConfig().unverified_max_seconds, 300.0)

    def test_limits_are_positive(self):
        """Nilai nol atau negatif membuat policy tidak berarti."""
        cfg = LiveConfig()
        self.assertGreater(cfg.unverified_max_consecutive, 0)
        self.assertGreater(cfg.unverified_max_seconds, 0)
        for i, delay in enumerate(cfg.unverified_retry_backoff):
            with self.subTest(attempt=i):
                self.assertGreater(delay, 0)

    def test_backoff_is_non_decreasing(self):
        """
        Backoff harus naik, tidak turun.

        Backoff yang turun membuat retry berikutnya lebih agresif dari
        sebelumnya — persis kebalikan dari yang dimaksud.
        """
        delays = list(LiveConfig().unverified_retry_backoff)
        self.assertEqual(delays, sorted(delays),
                         "backoff harus tidak menurun")


class UnverifiedTracker:
    """
    Menghitung kebijakan "tidak bisa dipastikan".

    Sengaja terpisah dari `SafetyGate`: gerbang memutuskan boleh-tidaknya
    order SEKARANG (satu blocker), sedangkan ini menghitung KAPAN harus
    berhenti — state yang butuh waktu (streak + sejak kapan), bukan satu
    kondisi Boolean.
    """

    def __init__(self, cfg=None):
        self.cfg = cfg or LiveConfig()
        self.consecutive = 0
        self.first_failure_at = None

    def record_failure(self, now):
        """Catat satu kegagalan. True kalau policy sudah terlampaui."""
        if self.consecutive == 0:
            self.first_failure_at = now
        self.consecutive += 1
        return self.should_stop(now)

    def record_success(self):
        """Sukses mereset SEMUA, termasuk batas waktu."""
        self.consecutive = 0
        self.first_failure_at = None

    def should_stop(self, now):
        if self.consecutive >= self.cfg.unverified_max_consecutive:
            return True
        if self.first_failure_at is not None:
            if (now - self.first_failure_at) >= self.cfg.unverified_max_seconds:
                return True
        return False

    def next_delay(self):
        """Delay sebelum percobaan berikutnya, None kalau policy habis."""
        delays = tuple(self.cfg.unverified_retry_backoff)
        idx = self.consecutive - 1
        if idx < 0 or idx >= len(delays):
            return None
        return delays[idx]

    def is_paused(self):
        """Order baru dijeda sejak kegagalan PERTAMA, bukan setelah ketiga."""
        return self.consecutive > 0


class TestUnverifiedPolicyBehaviour(unittest.TestCase):
    """Perilaku nyata dari policy."""

    def setUp(self):
        self.t = UnverifiedTracker()
        self.t0 = 1_000_000.0

    def test_first_failure_pauses_new_orders(self):
        """
        Jeda sejak kegagalan PERTAMA.

        Menunggu sampai ketiga berarti bot tetap mencoba mengirim order
        selama dua kegagalan, padahal tidak tahu apa yang terjadi.
        """
        self.assertFalse(self.t.is_paused())
        self.t.record_failure(self.t0)
        self.assertTrue(self.t.is_paused(),
                        "order baru tidak dijeda setelah kegagalan pertama")

    def test_two_failures_do_not_stop(self):
        self.t.record_failure(self.t0)
        self.assertFalse(self.t.record_failure(self.t0 + 10),
                         "berhenti setelah dua — terlalu cepat")

    def test_three_consecutive_failures_stop(self):
        self.assertFalse(self.t.record_failure(self.t0))
        self.assertFalse(self.t.record_failure(self.t0 + 10))
        self.assertTrue(self.t.record_failure(self.t0 + 40),
                        "tiga kegagalan berturut-turut tidak menghentikan")

    def test_time_limit_stops_sparse_failures(self):
        """
        Kegagalan yang JARANG tapi terus-menerus dihentikan oleh batas
        waktu, bukan streak.

        Ini kasus yang tidak tertangkap batas streak: satu kegagalan per
        menit sepanjang malam tidak pernah mencapai tiga berturut-turut.
        """
        t = UnverifiedTracker()
        t.record_failure(self.t0)          # gagal di detik 0
        # Tidak ada sukses di antaranya; kegagalan kedua 400 detik kemudian.
        self.assertTrue(t.record_failure(self.t0 + 400),
                        "kegagalan kedua setelah 400 detik tidak "
                        "menghentikan — batas waktu tidak bekerja")

    def test_success_resets_everything(self):
        self.t.record_failure(self.t0)
        self.t.record_failure(self.t0 + 5)
        self.t.record_success()
        self.assertFalse(self.t.is_paused(), "sukses tidak mereset jeda")
        self.assertFalse(self.t.record_failure(self.t0 + 10),
                         "kegagalan setelah reset masih terhitung sebagai "
                         "kelanjutan streak lama")

    def test_backoff_sequence(self):
        self.t.record_failure(self.t0)
        self.assertEqual(self.t.next_delay(), 10.0)
        self.t.record_failure(self.t0 + 10)
        self.assertEqual(self.t.next_delay(), 30.0)
        self.t.record_failure(self.t0 + 40)
        self.assertEqual(self.t.next_delay(), 60.0)

    def test_backoff_exhausted_returns_none(self):
        """
        Backoff habis tidak boleh mengembalikan delay.

        Mengembalikan nilai terakhir (atau nilai_acak) berarti retry
        tanpa jeda — dan policy akan terlihat masih bekerja padahal tidak.
        """
        for i in range(3):
            self.t.record_failure(self.t0 + i * 100)
        self.t.record_failure(self.t0 + 300)  # keempat
        self.assertIsNone(self.t.next_delay())


class TestDivergenceIsNotDelayed(unittest.TestCase):
    """
    Kontrol: policy ini HANYA untuk "tidak bisa dipastikan".

    Kalau divergensi ikut diuji, kill switch untuk divergensi akan ikut
    tertunda — dan itu membatalkan seluruh alasan pemisahan.
    """

    def test_no_backoff_exists_for_divergence(self):
        names = list(LiveConfig().__dataclass_fields__)
        divergence_backoff = [n for n in names
                              if "diverg" in n and "backoff" in n]
        self.assertEqual(divergence_backoff, [],
                         "ada backoff untuk divergensi — itu menunda "
                         "kondisi yang seharusnya langsung dihentikan")

    def test_policy_fields_are_named_for_unverified(self):
        """Penamaan harus menyatakan bahwa ini bukan untuk divergensi."""
        names = set(LiveConfig().__dataclass_fields__)
        for field in ("unverified_max_consecutive", "unverified_max_seconds",
                      "unverified_retry_backoff"):
            with self.subTest(field=field):
                self.assertIn(field, names)


if __name__ == "__main__":
    unittest.main()