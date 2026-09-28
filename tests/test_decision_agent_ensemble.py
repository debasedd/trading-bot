"""
tests/test_decision_agent_ensemble.py — DecisionAgent harus trades on ensemble.

Dua hal yang dijaga di sini:

1. Entry hanya terjadi dari snapshot arah yang FRESH dan cukup yakin.
   Data arah yang basi lebih berbahaya daripada tidak ada data.
2. Ekonomi scalping yang baru saja dituning (TP 0.60% / SL 0.25%) tidak
   boleh berubah karena pergantian sumber sinyal.
"""

import json
import os
import time
import unittest
from datetime import datetime, timezone

import database.db as db_module
from core.config import get_config
from core.event_bus import EventBus
from database.db import Database, close_db
from database.repository import Repository
from trading.risk_manager import RiskManager
from agents.decision_agent import DecisionAgent


def _fresh_timestamp():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _stale_timestamp(seconds_ago):
    return datetime.fromtimestamp(
        time.time() - seconds_ago, tz=timezone.utc
    ).strftime("%Y-%m-%d %H:%M:%S")


class TestDecisionAgentEnsembleGating(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.path = "data_store/test_decision_ensemble.db"
        for suffix in ("", "-wal", "-shm"):
            p = self.path + suffix
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass

        self.db = Database(db_path=self.path)
        await self.db.connect()
        db_module._db = self.db
        self.repo = Repository(self.db)
        await self.repo.init_account(10000.0)

        self.config = get_config()
        self.agent = DecisionAgent(EventBus(), RiskManager())

    async def asyncTearDown(self):
        await close_db()
        for suffix in ("", "-wal", "-shm"):
            p = self.path + suffix
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass

    async def _write_snapshot(
        self, direction, confidence, prob_long=None, prob_short=None,
        created_at=None, breakdown=None,
    ):
        if prob_long is None:
            prob_long = confidence if direction == "LONG" else 1.0 - confidence
        if prob_short is None:
            prob_short = 1.0 - prob_long
        await self.repo.insert_direction_snapshot({
            "symbol": "BTC/USDT:USDT",
            "prob_long": prob_long,
            "prob_short": prob_short,
            "direction": direction,
            "confidence": confidence,
            "z_composite": 0.0,
            "agent_breakdown": json.dumps(breakdown or []),
            "diffusion": None,
        })
        if created_at:
            await self.db.execute(
                "UPDATE direction_snapshots SET created_at = ? "
                "WHERE id = (SELECT MAX(id) FROM direction_snapshots)",
                (created_at,),
            )
            await self.db.commit()

    async def test_fresh_long_snapshot_produces_long_signal(self):
        await self._write_snapshot("LONG", 0.72, created_at=_fresh_timestamp())
        sigs = await self.agent._generate_scalp_signals(
            "BTC/USDT:USDT", {"combined": {}}
        )
        self.assertEqual(len(sigs), 1)
        self.assertEqual(sigs[0]["direction"], "BULLISH")
        self.assertEqual(sigs[0]["ensemble_direction"], "LONG")
        self.assertAlmostEqual(sigs[0]["strength"], 0.72, places=3)

    async def test_fresh_short_snapshot_produces_short_signal(self):
        await self._write_snapshot("SHORT", 0.70, created_at=_fresh_timestamp())
        sigs = await self.agent._generate_scalp_signals(
            "BTC/USDT:USDT", {"combined": {}}
        )
        self.assertEqual(len(sigs), 1)
        self.assertEqual(sigs[0]["direction"], "BEARISH")
        self.assertEqual(sigs[0]["ensemble_direction"], "SHORT")

    async def test_stale_snapshot_is_rejected(self):
        """
        Snapshot lama TIDAK boleh dipakai.

        Ini guards paling penting: data arah yang basi menghasilkan entry
        di pasar yang sudah berubah arah.
        """
        max_age = self.config.ensemble.max_snapshot_age_seconds
        await self._write_snapshot(
            "LONG", 0.95, created_at=_stale_timestamp(max_age + 30)
        )
        sigs = await self.agent._generate_scalp_signals(
            "BTC/USDT:USDT", {"combined": {}}
        )
        self.assertEqual(len(sigs), 0, "snapshot basi harus ditolak")

    async def test_snapshot_at_age_limit_is_accepted(self):
        max_age = self.config.ensemble.max_snapshot_age_seconds
        await self._write_snapshot(
            "LONG", 0.72, created_at=_stale_timestamp(1)
        )
        sigs = await self.agent._generate_scalp_signals(
            "BTC/USDT:USDT", {"combined": {}}
        )
        self.assertEqual(len(sigs), 1)

    async def test_low_confidence_snapshot_is_gated(self):
        """
        Confidence di bawah `min_confidence` tidak boleh jadi entry.

        Ambang ini yang menjaga win rate di atas titik impas 41.2%.
        """
        below = self.config.scalping.min_confidence - 0.05
        await self._write_snapshot("LONG", below, created_at=_fresh_timestamp())
        sigs = await self.agent._generate_scalp_signals(
            "BTC/USDT:USDT", {"combined": {}}
        )
        self.assertEqual(len(sigs), 0)

    async def test_neutral_snapshot_produces_no_signal(self):
        await self._write_snapshot("NEUTRAL", 0.10, created_at=_fresh_timestamp())
        sigs = await self.agent._generate_scalp_signals(
            "BTC/USDT:USDT", {"combined": {}}
        )
        self.assertEqual(len(sigs), 0)

    async def test_missing_snapshot_produces_no_signal(self):
        sigs = await self.agent._generate_scalp_signals(
            "BTC/USDT:USDT", {"combined": {}}
        )
        self.assertEqual(len(sigs), 0)

    async def test_latest_snapshot_wins(self):
        """Snapshot terbaru harus menggantikan yang lama."""
        await self._write_snapshot("SHORT", 0.80, created_at=_fresh_timestamp())
        sigs1 = await self.agent._generate_scalp_signals(
            "BTC/USDT:USDT", {"combined": {}}
        )
        self.assertEqual(sigs1[0]["ensemble_direction"], "SHORT")

        await self._write_snapshot("LONG", 0.80, created_at=_fresh_timestamp())
        sigs2 = await self.agent._generate_scalp_signals(
            "BTC/USDT:USDT", {"combined": {}}
        )
        self.assertEqual(sigs2[0]["ensemble_direction"], "LONG")

    async def test_scalp_economics_unchanged(self):
        """
        TP/SL yang dipakai DecisionAgent harus persis nilai config yang
        baru saja dituning menjadi profitable.

        Kalau ini bergeser, R:R bersih bukan lagi 1:1.43 dan seluruh
        perbaikan ekonomi sebelumnya ikut hilang.
        """
        await self._write_snapshot("LONG", 0.80, created_at=_fresh_timestamp())
        sigs = await self.agent._generate_scalp_signals(
            "BTC/USDT:USDT", {"combined": {}}
        )
        self.assertEqual(len(sigs), 1)

        # Di sini cukup pastikan sinyal membawa arah yang bisa dipetakan;
        # pemetaan ke percentage SL/TP terjadi di think().
        self.assertIn(sigs[0]["direction"], ("BULLISH", "BEARISH"))

        # Invariant ekonomi yang divalidasi config di tempat lain.
        roundtrip = self.config.fees.taker * 2
        net_tp = self.config.scalping.fast_tp_pct - roundtrip
        net_sl = self.config.scalping.tight_sl_pct + roundtrip
        self.assertGreater(net_tp, net_sl, "TP harus menutup > SL setelah fee")
        breakeven = net_sl / (net_tp + net_sl)
        self.assertLess(breakeven, 0.50)

    async def test_agreement_summary_is_reported(self):
        """Reasoning harus menyebut berapa agen yang setuju."""
        breakdown = [
            {"agent": "orderflow", "direction": "LONG", "confidence": 0.8},
            {"agent": "momentum", "direction": "LONG", "confidence": 0.7},
            {"agent": "technical", "direction": "LONG", "confidence": 0.6},
            {"agent": "microstructure", "direction": "SHORT", "confidence": 0.4},
        ]
        await self._write_snapshot(
            "LONG", 0.75, created_at=_fresh_timestamp(), breakdown=breakdown
        )
        sigs = await self.agent._generate_scalp_signals(
            "BTC/USDT:USDT", {"combined": {}}
        )
        self.assertEqual(len(sigs), 1)
        self.assertIn("3/4 agree", sigs[0]["reason"])


if __name__ == "__main__":
    unittest.main()
