"""
tests/test_config.py — Validasi konfigurasi scalping.

Fokus pada invarian ekonomi yang sempat dilanggar dan membuat bot merah
berkepanjangan tanpa satu pun error yang terlihat di log.
"""
import unittest

from core.config import (
    ScalpingConfig,
    FeeConfig,
    AppConfig,
    _validate_scalping_economics,
)


def make_config(**scalp_kwargs) -> AppConfig:
    """Bangun AppConfig dengan bagian scalping yang bisa dikustomisasi."""
    cfg = AppConfig()
    cfg.scalping = ScalpingConfig(**scalp_kwargs)
    cfg.fees = FeeConfig(maker=0.0002, taker=0.0005)
    return cfg


class TestScalpingConfigValidation(unittest.TestCase):
    def test_current_config_is_valid(self):
        """Konfigurasi yang dipakai bot sekarang harus lolos validasi."""
        from core.config import get_config

        cfg = get_config()
        _validate_scalping_economics(cfg)  # tidak boleh melempar

    def test_breakeven_above_tp_is_rejected(self):
        """
        Breakeven di atas/di sama dengan TP membuat proteksi breakeven mati.

        `_scalp_take_profit` menutup posisi lebih dulu di siklus yang sama,
        jadi `_protect_breakeven` tidak pernah sempat menggeser stop loss.
        """
        cfg = make_config(
            min_profit_pct=0.0035,
            breakeven_trigger_pct=0.0035,
            breakeven_offset_pct=0.0015,
        )
        with self.assertRaises(ValueError) as ctx:
            _validate_scalping_economics(cfg)
        self.assertIn("breakeven_trigger_pct", str(ctx.exception))

    def test_reward_below_one_is_rejected(self):
        """
        TP yang lebih kecil dari SL setelah fee tidak mungkin impas.

        Dengan TP 0.35% dan SL 0.35%, fee roundtrip 0.10% membuat net
        1:0.56 — butuh win rate 64% yang tidak bisa dicapai sinyal teknikal.
        """
        cfg = make_config(
            min_profit_pct=0.0035,
            tight_sl_pct=0.0035,
            breakeven_trigger_pct=0.0020,
            breakeven_offset_pct=0.0015,
        )
        with self.assertRaises(ValueError) as ctx:
            _validate_scalping_economics(cfg)
        self.assertIn("risk/reward", str(ctx.exception))

    def test_breakeven_offset_below_fee_is_rejected(self):
        """
        Offset breakeven yang di bawah fee roundtrip menghasilkan SL yang
        masih merugi — fijar loss yang tidak pernah bisa impas.
        """
        cfg = make_config(
            min_profit_pct=0.0060,
            tight_sl_pct=0.0025,
            breakeven_trigger_pct=0.0020,
            breakeven_offset_pct=0.0005,   # di bawah fee roundtrip 0.10%
        )
        with self.assertRaises(ValueError) as ctx:
            _validate_scalping_economics(cfg)
        self.assertIn("breakeven_offset_pct", str(ctx.exception))

    def test_disabled_scalping_skips_economics(self):
        """Saat scalping dimatikan, validasi ekonomi tidak boleh menggagalkan boot."""
        cfg = make_config(enabled=False, min_profit_pct=0.0001, breakeven_trigger_pct=0.9)
        _validate_scalping_economics(cfg)  # tidak harus melempar

    def test_repaired_config_has_positive_expectancy(self):
        """Konfigurasi hasil perbaikan harus memberi win rate impas di bawah 50%."""
        cfg = make_config(
            min_profit_pct=0.0060,
            tight_sl_pct=0.0025,
            breakeven_trigger_pct=0.0020,
            breakeven_offset_pct=0.0015,
        )
        roundtrip = cfg.fees.taker * 2
        net_tp = cfg.scalping.min_profit_pct - roundtrip
        net_sl = cfg.scalping.tight_sl_pct + roundtrip
        self.assertGreater(net_tp, net_sl, "TP harus menutup lebih besar dari SL")
        breakeven_wr = net_sl / (net_tp + net_sl)
        self.assertLess(
            breakeven_wr, 0.50,
            f"Butuh win rate {breakeven_wr:.1%} untuk impas — jauh di atas acuan 50%",
        )


if __name__ == "__main__":
    unittest.main()
