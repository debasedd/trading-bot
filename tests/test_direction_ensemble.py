"""
tests/test_direction_ensemble.py — Invarian matematika agregator arah.

Aggregator ini menentukan apakah bot LONG atau SHORT, jadi setiap
invari di sini adalah penjaga terhadap regression yang akan costing uang:
probabilitas yang tidak komplementer, shrinkage yang tidak monoton, atau
agen yang bisa "diam-diam" memilih arah.
"""

import unittest

from analysis.direction_ensemble import (
    DIRECTION_LONG,
    DIRECTION_NEUTRAL,
    DIRECTION_SHORT,
    aggregate,
    abstain,
    make_verdict,
)
from core.config import EnsembleConfig

AGENTS = ["orderflow", "momentum", "technical", "microstructure"]


class TestEnsembleAggregation(unittest.TestCase):
    def setUp(self):
        self.cfg = EnsembleConfig()

    def test_probabilities_are_complementary(self):
        """P(LONG) + P(SHORT) harus tepat 1.0 pada semua kombinasi."""
        combos = [
            [("LONG", 0.9), ("LONG", 0.5), ("SHORT", 0.2), ("SHORT", 0.8)],
            [("SHORT", 0.9), ("SHORT", 0.5), ("LONG", 0.2), ("LONG", 0.8)],
            [("NEUTRAL", 0.0), ("LONG", 0.1), ("NEUTRAL", 0.0), ("SHORT", 0.1)],
            [("LONG", 1.0), ("LONG", 1.0), ("LONG", 1.0), ("LONG", 1.0)],
            [("SHORT", 1.0), ("SHORT", 1.0), ("SHORT", 1.0), ("SHORT", 1.0)],
        ]
        for combo in combos:
            verdicts = [
                make_verdict(AGENTS[i], "BTC/USDT:USDT", d, c)
                for i, (d, c) in enumerate(combo)
            ]
            r = aggregate(verdicts, self.cfg)
            self.assertAlmostEqual(
                r["prob_long"] + r["prob_short"], 1.0, places=12,
                msg=f"tidak komplementer untuk {combo}",
            )

    def test_all_abstain_returns_neutral(self):
        """Tidak ada agen yang punya data -> netral, confidence 0."""
        verdicts = [abstain(a, "BTC/USDT:USDT") for a in AGENTS]
        r = aggregate(verdicts, self.cfg)
        self.assertEqual(r["direction"], DIRECTION_NEUTRAL)
        self.assertEqual(r["prob_long"], 0.5)
        self.assertEqual(r["prob_short"], 0.5)
        self.assertEqual(r["confidence"], 0.0)
        self.assertEqual(r["n_agents"], 0)
        self.assertEqual(r["n_abstained"], 4)

    def test_empty_input_returns_neutral(self):
        """Daftar verdict kosong tidak boleh meledakkan aggregator."""
        r = aggregate([], self.cfg)
        self.assertEqual(r["direction"], DIRECTION_NEUTRAL)
        self.assertEqual(r["prob_long"], 0.5)

    def test_single_long_agent_points_long(self):
        r = aggregate([make_verdict("orderflow", "BTC", DIRECTION_LONG, 0.7)], self.cfg)
        self.assertGreater(r["prob_long"], 0.5)
        self.assertEqual(r["direction"], DIRECTION_LONG)

    def test_single_short_agent_points_short(self):
        r = aggregate([make_verdict("orderflow", "BTC", DIRECTION_SHORT, 0.7)], self.cfg)
        self.assertLess(r["prob_long"], 0.5)
        self.assertEqual(r["direction"], DIRECTION_SHORT)

    def test_mirrored_inputs_give_mirrored_output(self):
        """
        Input cermin harus menghasilkan output cermin.

        Ini uji paling penting untuk kesetaraan arah: kalau LONG 0.8
        dan SHORT 0.8 tidak menghasilkan 1-x, ensemble punya bias
        tersembunyi yang akan mendorong bot ke satu sisi.
        """
        long_v = [
            make_verdict(AGENTS[i], "BTC", DIRECTION_LONG, c)
            for i, c in enumerate([0.8, 0.6, 0.45, 0.2])
        ]
        short_v = [
            make_verdict(AGENTS[i], "BTC", DIRECTION_SHORT, c)
            for i, c in enumerate([0.8, 0.6, 0.45, 0.2])
        ]
        r_long = aggregate(long_v, self.cfg)
        r_short = aggregate(short_v, self.cfg)
        self.assertAlmostEqual(
            r_long["prob_long"] + r_short["prob_long"], 1.0, places=9
        )
        self.assertEqual(r_long["direction"], DIRECTION_LONG)
        self.assertEqual(r_short["direction"], DIRECTION_SHORT)
        self.assertAlmostEqual(
            r_long["confidence"], r_short["confidence"], places=9
        )

    def test_shrinkage_pulls_toward_neutral(self):
        """
        Delta yang lebih kecil harus membawa probabilitas lebih dekat ke 0.5.

        Ini yang membuat ensemble tidak memancarkan confidence palsu.
        """
        verdicts = [
            make_verdict(AGENTS[i], "BTC", DIRECTION_LONG, c)
            for i, c in enumerate([0.9, 0.8, 0.7, 0.6])
        ]
        results = {}
        for delta in (0.4, 0.7, 1.0):
            cfg = EnsembleConfig(shrinkage_delta=delta)
            results[delta] = aggregate(verdicts, cfg)["prob_long"]

        self.assertLess(results[0.4], results[0.7])
        self.assertLess(results[0.7], results[1.0])
        for delta, p in results.items():
            self.assertGreater(p, 0.5, f"arah harus tetap LONG pada delta={delta}")

    def test_agreement_outweighs_equal_count_dissent(self):
        """
        3 agen searah harus mengalahkan 2-2, bahkan kalau ke-2 minoritasnya
        sangat yakin. Inilah alasan utama agreement weighting ada.
        """
        three_v = [
            make_verdict("orderflow", "BTC", DIRECTION_LONG, 0.6),
            make_verdict("momentum", "BTC", DIRECTION_LONG, 0.6),
            make_verdict("technical", "BTC", DIRECTION_LONG, 0.6),
            make_verdict("microstructure", "BTC", DIRECTION_SHORT, 0.1),
        ]
        split_v = [
            make_verdict("orderflow", "BTC", DIRECTION_LONG, 0.6),
            make_verdict("momentum", "BTC", DIRECTION_LONG, 0.6),
            make_verdict("technical", "BTC", DIRECTION_SHORT, 0.6),
            make_verdict("microstructure", "BTC", DIRECTION_SHORT, 0.6),
        ]
        r_three = aggregate(three_v, self.cfg)
        r_split = aggregate(split_v, self.cfg)
        self.assertGreater(
            r_three["prob_long"], r_split["prob_long"],
            "kecos jujuran majority harus menang atas split",
        )

    def test_clamp_prevents_extreme_probability(self):
        """Confidence 1.0 dari semua agen tidak boleh menghasilkan 0.9999."""
        verdicts = [
            make_verdict(a, "BTC", DIRECTION_LONG, 1.0) for a in AGENTS
        ]
        r = aggregate(verdicts, self.cfg)
        lo = self.cfg.min_prob
        self.assertGreaterEqual(r["prob_long"], lo)
        self.assertLessEqual(r["prob_long"], 1.0 - lo)
        self.assertGreaterEqual(r["prob_long"], 0.5)
        # Tidak mungkin menyentuh batas bawah absurd.
        self.assertGreater(r["prob_long"], 0.5)

    def test_abstain_is_not_neutral_vote(self):
        """
        Agen yang abstain TIDAK boleh dihitung sebagai agen netral.

        Kalau abstain dihitung sebagai z=0 biasa, menambah agen yang buta
        data akan "meredam" sinyal secara diam-diam. Yang benar: agen
        abstain dikeluarkan dari pembobotan.
        """
        one_loud = [make_verdict("orderflow", "BTC", DIRECTION_LONG, 0.9)]
        with_abstain = one_loud + [abstain("momentum", "BTC")]

        r1 = aggregate(one_loud, self.cfg)
        r2 = aggregate(with_abstain, self.cfg)
        self.assertAlmostEqual(r1["prob_long"], r2["prob_long"], places=9)

    def test_disabled_agent_is_excluded_from_pooling(self):
        """
        Agen yang dimatikan di config tidak boleh ikut memengaruhi hasil.

        `technical` di sini bersikap SHORT sementara dua agen lain LONG.
        Mematikannya harus menaikkan probabilitas LONG.
        """
        base = [
            make_verdict("orderflow", "BTC", DIRECTION_LONG, 0.9),
            make_verdict("momentum", "BTC", DIRECTION_LONG, 0.9),
        ]
        opposing = [make_verdict("technical", "BTC", DIRECTION_SHORT, 0.9)]

        r_on = aggregate(base + opposing, EnsembleConfig())
        cfg_off = EnsembleConfig()
        cfg_off.technical.enabled = False
        r_off = aggregate(base + opposing, cfg_off)

        self.assertGreater(
            r_off["prob_long"], r_on["prob_long"],
            "agen nonaktif masih ikut memengaruhi pooling",
        )
        # Tidak mungkin 1.0: shrinkage(delta) selalu menarik ke 0.5, dan itu
        # memang perilaku yang diinginkan.
        self.assertLess(r_off["prob_long"], 1.0)

    def test_neutral_verdict_produces_zero_z(self):
        """Agen NETRAL dengan confidence tinggi tetap z=0."""
        verdicts = [make_verdict("orderflow", "BTC", DIRECTION_NEUTRAL, 1.0)]
        r = aggregate(verdicts, self.cfg)
        self.assertAlmostEqual(r["prob_long"], 0.5, places=9)
        self.assertEqual(r["direction"], DIRECTION_NEUTRAL)

    def test_breakdown_is_reported_for_hud(self):
        """Strip per-agen di HUD membutuhkan breakdown yang utuh."""
        verdicts = [
            make_verdict("orderflow", "BTC", DIRECTION_LONG, 0.7, "ofi naik"),
            make_verdict("momentum", "BTC", DIRECTION_SHORT, 0.4, "turun"),
            abstain("technical", "BTC"),
        ]
        r = aggregate(verdicts, self.cfg)
        self.assertEqual(len(r["agent_breakdown"]), 2)
        names = {b["agent"] for b in r["agent_breakdown"]}
        self.assertEqual(names, {"orderflow", "momentum"})
        for b in r["agent_breakdown"]:
            self.assertIn("z", b)
            self.assertIn("reasoning", b)

    def test_mixed_abstain_and_active_counts_correctly(self):
        verdicts = [
            make_verdict("orderflow", "BTC", DIRECTION_LONG, 0.7),
            abstain("momentum", "BTC"),
            abstain("technical", "BTC"),
            make_verdict("microstructure", "BTC", DIRECTION_LONG, 0.5),
        ]
        r = aggregate(verdicts, self.cfg)
        self.assertEqual(r["n_agents"], 2)
        self.assertEqual(r["n_abstained"], 2)


if __name__ == "__main__":
    unittest.main()
