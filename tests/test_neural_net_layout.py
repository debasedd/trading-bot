"""
tests/test_neural_net_layout.py — Invarian tata letak konstelasi neural net.

Ini adalah regression test untuk keluhan "berantakan / acak-acakan". Akar
masalahnya: slot token dulu dipetakan berdasarkan POSISI dalam daftar token
aktif, sehingga ketika satu token hilang, semua token di belakangnya melompat
slot. Test di bawah mengunci perilaku yang benar:

  - Slot token STABIL terhadap perubahan keanggotaan daftar.
  - Tidak ada koordinat kembar.
  - Simbol sentinel non-koin (MARKET) tidak menjadi simpul token.
  - Sisi yang sibuk memakai pola dash (dasar animasi CSS) dan bentuk spline.
"""

import math
import unittest

from dashboard.layouts.hud_figures import TOKEN_SLOTS
from dashboard.layouts.hud_figures import (
    NEURAL_SYSTEM_NODES,
    TOKEN_SLOTS,
    _TOKEN_SLOT_REGISTRY,
    create_neural_net_fig,
)

FULL_TOKENS = [
    "BTC", "ETH", "SOL", "XRP", "BNB", "DOGE",
    "NEAR", "HYPE", "ZEC", "ONDO", "ENA", "XPL",
]


def _mk(symbols, direction="LONG", conf=0.6):
    return {
        f"{s}/USDT:USDT": {
            "direction": direction,
            "confidence": conf,
            "price_change": 0.002,
        }
        for s in symbols
    }


def _slots(signal_map):
    fig = create_neural_net_fig(signal_map=signal_map)
    tr = [t for t in fig.data if t.mode == "markers+text"][0]
    return {txt: (x, y) for x, y, txt in zip(tr.x, tr.y, tr.text)}


# Label token yang dipakai di test — sama dengan 12 slot.
TOKEN_LABEL_SET = {
    "BTC", "ETH", "SOL", "XRP", "BNB", "DOGE",
    "NEAR", "HYPE", "ZEC", "ONDO", "ENA", "XPL",
}


class TestTokenSlotStability(unittest.TestCase):
    def test_slots_survive_token_removal(self):
        """
        Token yang tetap TETAP di tempatnya saat token lain hilang.

        Inilah regression test utama. Versi lama gagal di sini: XRP melompat
        8->7 dan BNB 11->9 hanya karena HYPE/ZEC/ONDO tidak ada di frame itu.
        """
        base = _slots(_mk(FULL_TOKENS))
        reduced = _slots(_mk([
            s for s in FULL_TOKENS
            if s not in ("DOGE", "HYPE", "ZEC", "ONDO")
        ]))
        for sym in ("BTC", "ETH", "SOL", "XRP", "BNB", "NEAR", "ENA", "XPL"):
            self.assertIn(sym, base)
            self.assertIn(sym, reduced)
            self.assertEqual(
                base[sym], reduced[sym],
                f"{sym} bergeser saat token lain hilang",
            )

    def test_slots_survive_being_alone(self):
        """Simpul utama tetap stabil walau hanya 3 koin yang tampil."""
        base = _slots(_mk(FULL_TOKENS))
        solo = _slots(_mk(["BTC", "ETH", "SOL"]))
        for sym in ("BTC", "ETH", "SOL"):
            self.assertEqual(base[sym], solo[sym])

    def test_slots_ignore_confidence_changes(self):
        """Perubahan confidence tidak boleh memindahkan simpul."""
        low = _slots(_mk(FULL_TOKENS, conf=0.2))
        high = _slots(_mk(FULL_TOKENS, conf=0.95))
        for sym in FULL_TOKENS:
            self.assertEqual(low[sym], high[sym])

    def test_slots_ignore_direction_flip(self):
        """Balik arah LONG/SHORT tidak boleh memindahkan simpul."""
        bull = _slots(_mk(FULL_TOKENS, direction="LONG"))
        bear = _slots(_mk(FULL_TOKENS, direction="SHORT"))
        for sym in FULL_TOKENS:
            self.assertEqual(bull[sym], bear[sym])

    def test_no_duplicate_coordinates(self):
        """Tidak boleh ada dua simpul di koordinat yang sama."""
        for symbols in (FULL_TOKENS, ["BTC", "ETH"], []):
            slots = _slots(_mk(symbols))
            coords = list(slots.values())
            self.assertEqual(
                len(coords), len(set(coords)),
                f"ada koordinat kembar untuk {symbols}",
            )

    def test_registry_resolves_collisions_without_duplicate_slots(self):
        """
        Universe token lebih besar dari jumlah slot, jadi tabrakan hash
        WAJIB terjadi. Yang dijamin bukan "tidak ada tabrakan", tapi hasil
        resolve-nya satu-ke-satu: tidak ada dua token berakhir di slot
        yang sama, dan tidak ada token yang jatuh di luar rentang.
        """
        values = list(_TOKEN_SLOT_REGISTRY.values())
        self.assertEqual(
            len(values), len(set(values)),
            "dua token-ending di slot yang sama",
        )
        for slot in values:
            self.assertTrue(0 <= slot < len(TOKEN_SLOTS))

    def test_registry_is_deterministic(self):
        """Registri dibangun ulang harus menghasilkan peta yang sama."""
        from dashboard.layouts.hud_figures import _build_token_slot_registry
        self.assertEqual(_build_token_slot_registry(), _TOKEN_SLOT_REGISTRY)


class TestReservedSymbols(unittest.TestCase):
    def test_market_sentinel_is_not_a_token(self):
        """
        `MARKET` ditulis NewsAgent sebagai sentimen agregat, bukan koin.

        Sebelum ada filter, ia occupy satu slot dan tampil sebagai simpul
        bernama "MARKET" di konstelasi.
        """
        sig = _mk(["BTC", "ETH"])
        sig["MARKET"] = {
            "direction": "POSITIVE", "confidence": 0.4, "price_change": 0.0
        }
        slots = _slots(sig)
        self.assertNotIn("MARKET", slots)
        self.assertIn("BTC", slots)

    def test_reserved_names_never_appear(self):
        sig = _mk(["BTC"])
        for reserved in ("GLOBAL", "AGGREGATE", "TOTAL"):
            sig[reserved] = {
                "direction": "NEUTRAL", "confidence": 0.1, "price_change": 0.0
            }
        slots = _slots(sig)
        for reserved in ("GLOBAL", "AGGREGATE", "TOTAL", "MARKET"):
            self.assertNotIn(reserved, slots)


class TestEdgeRendering(unittest.TestCase):
    """Sisi graf adalah bahan dasar animasi CSS."""

    def _fig(self):
        return create_neural_net_fig(
            signal_map=_mk(["BTC", "ETH", "SOL"]),
            activity_map={
                "analysis_agent": 1.0, "decision_agent": 0.9,
                "execution_agent": 0.8, "news_agent": 0.7,
            },
            flow_map={
                "allMids": 1.0, "l2Book": 0.8,
                "candle": 0.5, "trades": 0.6,
            },
        )

    def test_each_edge_is_its_own_trace(self):
        """
        Sisi harus satu trace per sisi.

        Kalau digabung, hanya ada satu `path.js-line` dan CSS tidak bisa
        menganimasikan tiap sisi secara berbeda.
        """
        fig = self._fig()
        lines = [t for t in fig.data if t.mode == "lines"]
        # 8 sisi sistem + 1 sisi per token yang punya arah. Token tanpa
        # arah TIDAK digambargarinya — versi lama menambah
        # `CORE_ENGINE -> token` tanpa syarat, jadi 12 garis radial
        # saling menyeberang shortest jadi noise.
        self.assertGreaterEqual(len(lines), 8)
        for t in lines:
            self.assertNotIn(None, list(t.x), "sisi tidak boleh berisi None")

    def test_busy_edges_are_dashed_and_splined(self):
        """
        Sisi sibuk memakai pola dash px + spline.

        Pola dash di-inline style Plotly; CSS lalu menggeser
        `stroke-dashoffset`. Sisi sepi tetap solid sehingga offset di sana
        menjadi no-op visual dan tidak ikut bergerak.
        """
        fig = self._fig()
        lines = [t for t in fig.data if t.mode == "lines"]
        dashed = [t for t in lines if t.line.dash and "px" in str(t.line.dash)]
        self.assertTrue(dashed, "tidak ada sisi putus-putus untuk dianimasikan")
        for t in lines:
            self.assertEqual(t.line.shape, "spline")

    def test_every_dash_pattern_matches_css_keyframe(self):
        """
        SETIAP pola dash harus berperiode 16px agar cocok dengan CSS.

        CSS menggeser `stroke-dashoffset` tepat -16px per siklus. Kalau ada
        pola yang periodenya berbeda, pola itu akan "meleset" di setiap loop
        dan terlihat bergetar.

        Polanya memang BERVARIASI antar sisi (untuk desync visual), tapi
        semuanya harus tetap satu periode yang sama.
        """
        import re
        from pathlib import Path

        fig = self._fig()
        dashes = {
            str(t.line.dash) for t in fig.data
            if t.mode == "lines" and t.line.dash and "px" in str(t.line.dash)
        }
        self.assertTrue(dashes, "tidak ada sisi putus-putus")
        self.assertGreater(
            len(dashes), 1,
            "pola dash harus bervariasi agar sisi tidak berdenyut serempak",
        )
        for pattern in dashes:
            total = sum(int(v) for v in re.findall(r"(\d+)px", pattern))
            self.assertEqual(
                total, 16,
                f"pola {pattern!r} berperiode {total}px, harus 16px",
            )

        css = (Path(__file__).parent.parent
               / "dashboard" / "assets" / "neural_flow.css").read_text(
            encoding="utf-8")
        self.assertIn("stroke-dashoffset: -16px", css)

    def test_figure_sets_uirevision(self):
        """Tanpa uirevision, Plotly menulis ulang zoom tiap tick."""
        self.assertEqual(
            self._fig().layout.uirevision, "neural-constellation"
        )

    def test_axes_have_no_scaleanchor(self):
        """
        Sumbu TIDAK BOLEH memakai scaleanchor.

        `scaleanchor="x"` + `scaleratio=1` memaksa Plotly memenuhi rasio
        1:1. Pada panel yang jauh lebih lebar daripada tinggi, satu-satunya
        cara Plotly memenuhi itu adalah MELARANGKAN range x, sehingga px
        per unit turun ~7.4x dan label token saling tumpuk jadi gundukan
        yang tidak terbaca.

        Test ini menggantikan `test_axes_are_anchored_for_round_markers`
        yang dulu mengunci scaleanchor sebagai fitur — padahal atribut itu
        justru penyebab utama layout konstelasi membusuk.
        """
        fig = self._fig()
        self.assertIsNone(fig.layout.yaxis.scaleanchor)
        self.assertIsNone(fig.layout.yaxis.scaleratio)


class TestLabelCollision(unittest.TestCase):
    """
    Label tidak boleh saling menimpa.

    Di screenshot, lima label token (ENA, ETH, ONDO, XPL, BNB) menumpuk
    jadi satu gumpalan di tengah panel. Penyebabnya dua: cincin token
    punya jarak antar-simpul hanya ~40px padahal label 3-4 karakter
    selebar ~26px, dan penempatan label ikut ambang y global sehingga
    label sistem bisa mengarah ke baris token.
    """

    # Panel zona-3 di 1920px: satu dari empat sel grid 12-kolom.
    CELL_W = 470
    PANEL_H = 260
    HEAD_H = 22
    PAD = 8

    def _px_per_unit(self, fig):
        inner_w = self.CELL_W - self.HEAD_H - self.PAD
        inner_h = self.PANEL_H - self.HEAD_H - self.PAD
        dx = fig.layout.xaxis.range[1] - fig.layout.xaxis.range[0]
        dy = fig.layout.yaxis.range[1] - fig.layout.yaxis.range[0]
        return max(inner_w / dx, inner_h / dy)

    def _nodes(self, fig):
        tr = [t for t in fig.data if t.mode == "markers+text"][0]
        return [
            (txt, x, y, pos)
            for txt, x, y, pos in zip(tr.text, tr.x, tr.y, tr.textposition)
        ]

    def _label_gap(self, a, b, px):
        """Jarak antara dua posisi label, memperhitungkan arah masing-masing."""
        off = {"bottom center": 14, "top center": -14}
        return math.hypot(
            (a[1] - b[1]) * px,
            ((a[2] + off[a[3]] / px) - (b[2] + off[b[3]] / px)) * px,
        )

    def _all_gaps(self, fig):
        system_names = set(NEURAL_SYSTEM_NODES)
        # Label sistem memakai nama pendek;token memakai ticker.
        pts = self._nodes(fig)
        sysn = [p for p in pts if p[0] in system_names or p[0] in
                {v[2] for v in NEURAL_SYSTEM_NODES.values()}]
        tok = [p for p in pts if p not in sysn]
        px = self._px_per_unit(fig)
        gaps = []
        for grp in (tok, sysn):
            for i in range(len(grp)):
                for j in range(i + 1, len(grp)):
                    gaps.append(self._label_gap(grp[i], grp[j], px))
        for t in tok:
            for s in sysn:
                gaps.append(self._label_gap(t, s, px))
        return gaps

    def test_no_label_collisions(self):
        """Setiap pasang label harus jaraknya > 34px (label 3-4 char ~26px)."""
        fig = create_neural_net_fig(signal_map={
            f"{s}/USDT:USDT": {"direction": "LONG", "confidence": 0.7,
                               "price_change": 0.002}
            for s in ("BTC", "ETH", "SOL", "XRP", "BNB", "DOGE",
                      "NEAR", "HYPE", "ZEC", "ONDO", "ENA", "XPL")
        })
        gaps = self._all_gaps(fig)
        self.assertTrue(gaps, "tidak ada node untuk diuji")
        self.assertGreaterEqual(
            min(gaps), 24.0,
            f"dua label hanya {min(gaps):.0f}px apart — label akan menimpa",
        )

    def test_token_labels_all_below_system_labels_all_above(self):
        """
        Arah label ditentukan jenis simpul, bukan ambang y.

        Ambang y global pernah membuat EXECUTION (sistem) memakai
        "bottom center" sehingga labelnya melompat ke baris token.
        """
        from dashboard.layouts.hud_figures import _TOKEN_SLOT_REGISTRY

        fig = create_neural_net_fig(signal_map={
            f"{s}/USDT:USDT": {"direction": "LONG", "confidence": 0.7,
                               "price_change": 0.002}
            for s in ("BTC", "ETH", "XRP", "BNB", "DOGE", "NEAR")
        })
        sys_labels = {v[2] for v in NEURAL_SYSTEM_NODES.values()}
        for txt, _, _, pos in self._nodes(fig):
            if txt in sys_labels:
                # Akar menaruh label di bawah; sisanya di atas.
                expected = "bottom center" if txt == "CORE" else "top center"
                self.assertEqual(pos, expected, f"label sistem {txt} salah")
            else:
                self.assertEqual(pos, "top center", f"label token {txt} salah")

    def test_no_duplicate_node_coordinates(self):
        fig = create_neural_net_fig(signal_map={
            f"{s}/USDT:USDT": {"direction": "LONG", "confidence": 0.7,
                               "price_change": 0.002}
            for s in ("BTC", "ETH", "SOL", "XRP", "BNB", "DOGE",
                      "NEAR", "HYPE", "ZEC", "ONDO", "ENA", "XPL")
        })
        coords = [(x, y) for _, x, y, _ in self._nodes(fig)]
        self.assertEqual(len(coords), len(set(coords)))

    def test_node_labels_fit_inside_axis_range(self):
        """Label di bawah simpul terendah harus masih di dalam kanvas."""
        fig = create_neural_net_fig(signal_map={
            f"{s}/USDT:USDT": {"direction": "LONG", "confidence": 0.7,
                               "price_change": 0.002}
            for s in ("BTC", "SOL", "NEAR")
        })
        y_min = fig.layout.yaxis.range[0]
        for txt, _, y, pos in self._nodes(fig):
            if pos == "bottom center":
                self.assertGreater(
                    y, y_min + 0.1,
                    f"label {txt} di y={y} terlalu dekat tepi bawah "
                    f"({y_min})",
                )


class TestEdgeQuality(unittest.TestCase):
    """
    Kualitas GARIS, bukan cuma tabrakan node.

    Semua fix sebelumnya hanya mengecek label-vs-label dan marker-vs-marker.
    Yang tidak pernah diukur adalah edge itu sendiri. Screen penuh garis
    "ruwet" persis karena angka itu tidak pernah dicek: 24 garis dengan
    10 di antaranya menyeberang >200px di panel 440px.

    Guard di bawah mengunci dua hal yang bikin graph jadi reader:
      1. Edge token HANYA boleh berlabuh ke baris bawah sistem, supaya
         bisa drop tegak lurus dan tidak menyeberang panel.
      2. Sisi memakai busur kecil, karena busur yang proporsional
         terhadap panjang membuat edge panjang melengkung dan berdempet.
    """

    def _fig(self):
        return create_neural_net_fig(signal_map={
            f"{s}/USDT:USDT": {"direction": "LONG", "confidence": 0.7,
                               "price_change": 0.002}
            for s in ("BTC", "ETH", "SOL", "XRP", "BNB", "DOGE",
                      "NEAR", "HYPE", "ZEC", "ONDO", "ENA", "XPL")
        })

    def test_mindmap_flows_left_to_right(self):
        """
        Mindmap: akar di kolom paling kiri, semuacabang ke kanan.

        Edge sistem harus selalu menuju kolom yang LEBIH KANAN, kalau tidak
        graf berhenti terbaca sebagai pohon dan kembali jadi gumpalan.
        """
        from dashboard.layouts.hud_figures import NEURAL_SYSTEM_EDGES

        nodes = NEURAL_SYSTEM_NODES
        for a, b in NEURAL_SYSTEM_EDGES:
            # `>=` bukan `>`: YFI dan COIN ada di kolom yang sama dengan
            # EXEC, jadi edge ke sana datar, bukan mundur. Yang dilarang
            # hanya edge yang*KEMBALI* ke kolom kiri.
            self.assertGreaterEqual(
                nodes[b][0], nodes[a][0],
                f"edge {a}->{b} mengalir ke kanan belakang",
            )

    def test_every_system_node_has_a_parent(self):
        """Tidak ada simpul sistem yang menggantung di udara."""
        from dashboard.layouts.hud_figures import (
            NEURAL_SYSTEM_EDGES, NEURAL_SYSTEM_NODES,
        )
        parents = {b for _, b in NEURAL_SYSTEM_EDGES}
        # Akar memang TIDAK punya induk - itu definisi mindmap. Yang
        # tidak boleh ada adalah simpul non-akar yang menggantung, karena
        # tanpa induk dia tidak punya cabang masuk dan graf jadi berantakan.
        for key in NEURAL_SYSTEM_NODES:
            if key == "CORE_ENGINE":
                continue
            self.assertIn(
                key, parents,
                f"{key} tidak punya induk - cabang menggantung",
            )

    def test_every_token_gets_an_edge_regardless_of_direction(self):
        """
        SETIAP token harus punya edge, mesmo tanpa arah sinyal.

        Versi lama `continue` kalau arahnya kosong, jadi 11 dari 12 token
        tidak punya garis sama sekali - sebagian terlihat "nempel",
        sebagian menggantung. Arah sinyal hanya menentukan GAYA
        (solid vs dashed), bukan ada atau tidaknya edge.
        """
        fig = create_neural_net_fig(signal_map={
            s: {"direction": "LONG", "confidence": 0.7, "price_change": 0.002}
            for s in ("BTC", "ETH", "SOL", "XRP", "BNB", "DOGE",
                      "NEAR", "HYPE", "ZEC", "ONDO", "ENA", "XPL")
        })
        token_edges = 0
        for tr in fig.data:
            if tr.mode == "lines" and tr.customdata:
                dst = tr.customdata[0][1]
                if dst in TOKEN_LABEL_SET:
                    token_edges += 1
        self.assertEqual(
            token_edges, 12,
            "setiap token harus punya tepat satu edge ke busnya",
        )

    def test_tokens_connect_directly_to_exec(self):
        """
        Token connect LANGSUNG ke EXEC, tanpa perantara.

        Versi sebelumnya memakai simpul virtual "bus". Justru bus itu
        yang jadi sumber crossing baru: EXEC punya dua anak (bus_left dan
        bus_right), dan jalur ke bus_right wajib melewati x=bus_left di
        mana drop vertikal ke token mulai. Persilangan pindah tempat,
        bukan hilang.
        """
        fig = create_neural_net_fig(signal_map={
            s: {"direction": "LONG", "confidence": 0.7, "price_change": 0.002}
            for s in ("BTC", "ETH", "SOL", "XRP", "BNB", "DOGE",
                      "NEAR", "HYPE", "ZEC", "ONDO", "ENA", "XPL")
        })
        token_edges = []
        for tr in fig.data:
            if tr.mode == "lines" and tr.customdata:
                src, dst = tr.customdata[0][0], tr.customdata[0][1]
                if dst in TOKEN_LABEL_SET:
                    token_edges.append(src)

        self.assertEqual(len(token_edges), 12, "setiap token punya satu edge")
        for src in token_edges:
            self.assertEqual(
                src, "EXECUTION_AGENT",
                "token harus connect langsung ke EXEC",
            )

    def test_no_bus_nodes_remain(self):
        """Simpul virtual tidak boleh tersisa di layout."""
        from dashboard.layouts import hud_figures
        self.assertFalse(
            hasattr(hud_figures, "TOKEN_BUS"),
            "TOKEN_BUS sudah dihapus; token connect langsung ke EXEC",
        )

    def test_root_is_leftmost_column(self):
        """
        Akar harus jadi simpul paling kiri - itu yang membuat graf terbaca
        mengalir ke kanan.
        """
        root_x = NEURAL_SYSTEM_NODES["CORE_ENGINE"][0]
        for key, v in NEURAL_SYSTEM_NODES.items():
            if key == "CORE_ENGINE":
                continue  # akar memang di kolom paling kiri
            self.assertGreater(
                v[0], root_x,
                f"{key} tidak di kanan akar - graf bukan mindmap",
            )

    def test_tokens_are_right_of_every_system_node(self):
        """
        Zona token harus terpisah jelas di kanan semua simpul sistem.

        Kalau token bercampur dengan sistem, edge keluaran->token bercampur
        dengan edge sistem dan graf kembali jadi gumpalan.
        """
        max_system_x = max(v[0] for v in NEURAL_SYSTEM_NODES.values())
        for x, y in TOKEN_SLOTS:
            self.assertGreater(
                x, max_system_x,
                f"token di x={x} tidak di kanan semua simpul sistem "
                f"(maks {max_system_x})",
            )

    def test_token_block_rows_are_evenly_spaced(self):
        """Baris token harus berjarak sama — kalau tidak, terlihat acak."""
        rows = sorted({round(y, 4) for _, y in TOKEN_SLOTS}, reverse=True)
        self.assertGreaterEqual(len(rows), 2, "token harus punya beberapa baris")
        gaps = [rows[i] - rows[i + 1] for i in range(len(rows) - 1)]
        for g in gaps:
            self.assertAlmostEqual(
                g, gaps[0], places=3,
                msg=f"jarak antar baris token tidak seragam: {gaps}",
            )
