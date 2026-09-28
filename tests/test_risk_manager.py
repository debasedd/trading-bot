"""
tests/test_risk_manager.py — Pengujian logika manajemen risiko dan rumus matematika futures.
"""

import unittest
from trading.risk_manager import RiskManager


class TestRiskManager(unittest.TestCase):
    """Pengujian kalkulasi risiko, margin, PnL, dan likuidasi."""

    def setUp(self):
        self.rm = RiskManager()

    def test_position_sizing(self):
        """Uji perhitungan ukuran posisi (Fixed Fractional)."""
        balance = 10000.0
        entry = 50000.0
        stop_loss = 49000.0  # SL distance = 1000
        risk_pct = 0.02      # Risk = 200 USDT
        leverage = 10

        res = self.rm.calculate_position_size(
            balance=balance,
            entry_price=entry,
            stop_loss_price=stop_loss,
            risk_pct=risk_pct,
            leverage=leverage,
        )

        # Quantity = Risk / SL_distance = 200 / 1000 = 0.2 BTC
        self.assertAlmostEqual(res["quantity"], 0.2, places=4)
        # Position Value = 0.2 * 50000 = 10000 USDT
        self.assertAlmostEqual(res["position_value"], 10000.0, places=2)
        # Initial Margin = 10000 / 10 = 1000 USDT
        self.assertAlmostEqual(res["margin"], 1000.0, places=2)
        self.assertAlmostEqual(res["risk_amount"], 200.0, places=2)
        self.assertEqual(res["leverage"], 10)

    def test_liquidation_price_long(self):
        """Uji rumus harga likuidasi untuk posisi LONG."""
        entry = 60000.0
        leverage = 10
        mmr = 0.004  # 0.4%

        # Liq = Entry * (1 - 1/Lev + MMR)
        # Liq = 60000 * (1 - 0.1 + 0.004) = 60000 * 0.904 = 54240.0
        liq = self.rm.calculate_liquidation_price(entry, "LONG", leverage, mmr)
        self.assertAlmostEqual(liq, 54240.0, places=2)

    def test_liquidation_price_short(self):
        """Uji rumus harga likuidasi untuk posisi SHORT."""
        entry = 60000.0
        leverage = 10
        mmr = 0.004

        # Liq = Entry * (1 + 1/Lev - MMR)
        # Liq = 60000 * (1 + 0.1 - 0.004) = 60000 * 1.096 = 65760.0
        liq = self.rm.calculate_liquidation_price(entry, "SHORT", leverage, mmr)
        self.assertAlmostEqual(liq, 65760.0, places=2)

    def test_pnl_long_profit(self):
        """Uji perhitungan untung posisi LONG."""
        entry = 50000.0
        current = 52000.0
        qty = 0.5

        # PnL = (52000 - 50000) * 0.5 = 1000 USDT
        res = self.rm.calculate_pnl("LONG", entry, current, qty)
        self.assertAlmostEqual(res["pnl"], 1000.0, places=2)
        self.assertGreater(res["roe_pct"], 0)

    def test_pnl_short_profit(self):
        """Uji perhitungan untung posisi SHORT."""
        entry = 50000.0
        current = 48000.0
        qty = 0.5

        # PnL = (50000 - 48000) * 0.5 = 1000 USDT
        res = self.rm.calculate_pnl("SHORT", entry, current, qty)
        self.assertAlmostEqual(res["pnl"], 1000.0, places=2)
        self.assertGreater(res["roe_pct"], 0)

    def test_pnl_long_loss(self):
        """Uji perhitungan rugi posisi LONG."""
        entry = 50000.0
        current = 49000.0
        qty = 0.5

        # PnL = (49000 - 50000) * 0.5 = -500 USDT
        res = self.rm.calculate_pnl("LONG", entry, current, qty)
        self.assertAlmostEqual(res["pnl"], -500.0, places=2)
        self.assertLess(res["roe_pct"], 0)

    def test_calculate_fee(self):
        """
        Uji perhitungan fee taker dan maker.

        Angka takers diambil dari config, bukan ditulis mati di sini.
        Angka tarif diambil dari config, bukan ditulis mati di sini.
        Tarif Hyperliquid pernah berubah (0.05% -> 0.045%) dan test yang
        """
        from core.config import get_config

        cfg = get_config()
        qty = 1.0
        price = 60000.0

        fee_taker = self.rm.calculate_fee(qty, price, "TAKER")
        self.assertAlmostEqual(
            fee_taker, qty * price * cfg.fees.taker, places=8,
            msg="fee taker harus = notional x tarif taker di config")

        fee_maker = self.rm.calculate_fee(qty, price, "MAKER")
        self.assertAlmostEqual(
            fee_maker, qty * price * cfg.fees.maker, places=8,
            msg="fee maker harus = notional x tarif maker di config")

    def test_fee_rates_match_hyperliquid_base_tier(self):
        """
        Tarif di config harus sama dengan tier dasar Hyperliquid.

        Config ini dulu berisi 0.05%/0.02% -- angka Binance, bukan
        Hyperliquid. Selisihnya kecil tapi cukup untuk membuat simulasi
        terlihat lebih untung daripada kenyataannya.
        """
        from core.config import get_config

        cfg = get_config()
        self.assertAlmostEqual(cfg.fees.taker, 0.00045, places=6)
        self.assertAlmostEqual(cfg.fees.maker, 0.00015, places=6)

    def test_validate_trade_constraints(self):
        """Uji validasi batasan trading (saldo tidak cukup, posisi max, daily loss)."""
        balance = 1000.0

        # Margin melebihi 90% saldo
        v1 = self.rm.validate_trade(balance=balance, margin_required=950.0, open_positions=0)
        self.assertFalse(v1["allowed"])

        # Posisi terbuka mencapai batas maksimum
        max_pos = self.rm.config.max_open_positions
        v2 = self.rm.validate_trade(balance=balance, margin_required=100.0, open_positions=max_pos)
        self.assertFalse(v2["allowed"])

        # Kerugian harian melebihi batas saldo
        daily_loss_limit = balance * self.rm.config.max_daily_loss
        v3 = self.rm.validate_trade(balance=balance, margin_required=100.0, open_positions=1, daily_pnl=-(daily_loss_limit + 10.0))
        self.assertFalse(v3["allowed"])

        # Trade valid
        v4 = self.rm.validate_trade(balance=balance, margin_required=200.0, open_positions=1, daily_pnl=10.0)
        self.assertTrue(v4["allowed"])

    def test_max_drawdown(self):
        """Uji perhitungan maximum drawdown."""
        equity_series = [10000.0, 10500.0, 9500.0, 9000.0, 10200.0]
        # Peak = 10500, Trough = 9000 -> Drawdown = (10500 - 9000) / 10500 = 1500 / 10500 = 14.29%
        mdd = self.rm.calculate_max_drawdown(equity_series)
        self.assertAlmostEqual(mdd, 0.1429, places=3)

    def test_sharpe_ratio(self):
        """Uji perhitungan Sharpe Ratio."""
        returns = [0.02, 0.01, -0.005, 0.03, 0.015]
        sr = self.rm.calculate_sharpe_ratio(returns)
        self.assertIsInstance(sr, float)
        self.assertGreater(sr, 0)


if __name__ == "__main__":
    unittest.main()
