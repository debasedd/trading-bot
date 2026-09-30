"""
tests/test_fill_cost_funding.py — Biaya funding perp.

Funding adalah biaya yang sejak awal tidak pernah ada di model ini, dan orang
yang membaca laporan tidak pernah mengetahuinya: tanpa funding, scalp pendek
terlihat hampir gratis. Test-test ini mengunci kontraknya supaya tidak
hilang lagi.
"""
import math
import unittest

from trading.fill_cost import (
    FUNDING_PERIOD_SECONDS,
    funding_cost,
)


class TestFundingPeriod(unittest.TestCase):
    def test_period_is_one_hour_not_eight(self):
        """
        Settlement Hyperliquid adalah 1 JAM, bukan 8 jam.

        config.yaml menulis `funding_rate: 28800` dengan komentar "8 jam",
        yang benar untuk Binance dan salah untuk Hyperliquid, bursa yang
       sebenarnya dipakai. Rate 8 jam membagi biaya dengan 8, jadi laporan
        P&L terlihat 8x lebih murah dari kenyataan.
        """
        self.assertEqual(
            FUNDING_PERIOD_SECONDS, 3600.0,
            "Hyperliquid settle funding tiap JAM, bukan 8 jam",
        )

    def test_one_held_hour_is_one_settled_period(self):
        _, meta = funding_cost("LONG", 1000.0, 3600.0, 0.0001)
        self.assertEqual(meta["periods"], 1.0)

    def test_zero_hold_settles_nothing(self):
        """Posisi yang ditutup seketika tidak mungkin sudah di-settle."""
        cost, meta = funding_cost("LONG", 1000.0, 0.0, 0.0001)
        self.assertEqual(cost, 0.0)
        self.assertEqual(meta["periods"], 0.0)


class TestFundingDirection(unittest.TestCase):
    """Funding positif: Long membayar, Short menerima."""

    def test_long_pays_when_rate_positive(self):
        cost, _ = funding_cost("LONG", 1000.0, 7200.0, 0.0001)
        self.assertGreater(cost, 0.0, "Long harus membayar saat rate positif")

    def test_short_receives_when_rate_positive(self):
        cost, _ = funding_cost("SHORT", 1000.0, 7200.0, 0.0001)
        self.assertLess(cost, 0.0, "Short harus menerima saat rate positif")

    def test_long_receives_when_rate_negative(self):
        cost, _ = funding_cost("LONG", 1000.0, 7200.0, -0.0001)
        self.assertLess(cost, 0.0, "Long harus menerima saat rate negatif")

    def test_short_pays_when_rate_negative(self):
        cost, _ = funding_cost("SHORT", 1000.0, 7200.0, -0.0001)
        self.assertGreater(cost, 0.0, "Short harus membayar saat rate negatif")

    def test_direction_is_symmetric(self):
        """Biaya LONG harus sebesar minus biaya SHORT, bukan berbeda."""
        long_cost, _ = funding_cost("LONG", 1000.0, 7200.0, 0.0001)
        short_cost, _ = funding_cost("SHORT", 1000.0, 7200.0, 0.0001)
        self.assertAlmostEqual(long_cost, -short_cost, places=12)


class TestFundingArithmetic(unittest.TestCase):
    def test_cost_is_notional_times_rate_times_periods(self):
        notional, rate, hours = 500.0, 0.0001, 1.0
        cost, _ = funding_cost(
            "LONG", notional, hours * FUNDING_PERIOD_SECONDS, rate,
        )
        self.assertAlmostEqual(cost, notional * rate * 1.0, places=12)

    def test_period_fraction_rounds_up_not_down(self):
        """
        Periode pecahan dibayar PENUH di periode yang memotongnya.

        Bursa tidak memb prorata. Posisi yang bertahan 90 menit sudah
        melewati satu settlement, jadi menagih dua periode adalah yang
        benar; membagi 1.5 akan undercharge setiap posisi yang ditutup
        di antara dua settlement — dan itu mayoritas posisi.
        """
        _, meta = funding_cost("LONG", 1000.0, 90 * 60, 0.0001)
        self.assertEqual(meta["periods"], 2.0)

    def test_two_exact_periods_is_not_three(self):
        """
        2.0 periode persis harus 2, bukan 3.

        `int(x) + 1` menghasilkan 3 untuk 2.0 dan menagih satu periode
        yang belum settlement. Ini regresi yang pernah terjadi, jadi
        dikunci di sini.
        """
        _, meta = funding_cost("LONG", 1000.0, 2 * 3600, 0.0001)
        self.assertEqual(meta["periods"], 2.0)

    def test_cost_scales_with_notional(self):
        small, _ = funding_cost("LONG", 1000.0, 3600.0, 0.0001)
        large, _ = funding_cost("LONG", 10000.0, 3600.0, 0.0001)
        self.assertAlmostEqual(large, small * 10.0, places=9)


class TestFundingDataQuality(unittest.TestCase):
    def test_missing_rate_costs_nothing_but_says_so(self):
        """
        Rate yang belum diterima = biaya 0, dan itu harus terlihat.

        Menebak rate dari default berarti membebankan angka yang tidak
        pernah bisa diverifikasi. Yang benar adalah membebankan nol dan
        melaporkan bahwa angkanya belum diketahui, supaya ketiadaan biaya
        terlihat sebagai ketiadaan, bukan sebagai nol yang sah.
        """
        cost, meta = funding_cost("LONG", 1000.0, 7200.0, None)
        self.assertEqual(cost, 0.0)
        self.assertIsNone(meta["funding_rate"])

    def test_absurdly_high_rate_is_rejected_not_charged(self):
        """
        Rate 99% per periode bukan pasar yang ekstrem, itu data salah.

        Membebankannya akan menghapus seluruh P&L posisi dalam satu
        settlement, dan angka itu akan terlihat sah di laporan.
        """
        cost, meta = funding_cost("LONG", 1000.0, 3600.0, 0.99)
        self.assertEqual(cost, 0.0)
        self.assertIsNone(meta["funding_rate"])

    def test_zero_rate_is_charged_zero_not_rejected(self):
        """Rate nol berarti pasar tidak menunda — itu data yang valid."""
        cost, meta = funding_cost("LONG", 1000.0, 3600.0, 0.0)
        self.assertEqual(cost, 0.0)
        self.assertEqual(meta["periods"], 1.0)

    def test_non_numeric_rate_is_rejected(self):
        cost, _ = funding_cost("LONG", 1000.0, 3600.0, "bukan-angka")
        self.assertEqual(cost, 0.0)

    def test_rate_exactly_at_floor_is_accepted(self):
        """
        Rate tepat di batas bawah harus diterima.

        Rate yang TEPAT di floor tiba sebagai hasil pembagian floating
        point, yang bisa sedikit di bawahnya. Perbandingan ketat
        membuangnya, dan impl itu berarti membebankan nol untuk rate
        yang sah — bias yang 항상 ke arah yang membuat hasil terlihat
        lebih baik.
        """
        from trading.fill_cost import FUNDING_RATE_FLOOR

        cost, _ = funding_cost("LONG", 1000.0, 2 * 3600, FUNDING_RATE_FLOOR)
        self.assertAlmostEqual(cost, 1000.0 * FUNDING_RATE_FLOOR * 2, places=12)
        self.assertGreater(cost, 0.0)


if __name__ == "__main__":
    unittest.main()
