"""
tests/test_layout_budget.py — Guard overflow tata letak.

Screenshot terakhir menunjukkan empat regresi, semua dari satu penyebab:
kontrak tinggi diperketat tapi ISI panel tidak ikut dirapikan, sehingga
angkanya terpotong.

  1. `.giant-pnl` masih 52px di rail 62px  -> PnL kepotong separuh
  2. Grafik zone-3 memakai token yang salah -> equity 188px kosong
  3. Tabel KPI butuh 120px, tersedia 106px -> baris terakhir terpotong
  4. Enam node pipeline butuh 700px di strip 44px -> baris patah

Guard di sini mengunci ARITMETIKANYA, bukan cuma keberadaan token. CSS
tidak bisa diuji pakai unittest, jadi tinggi yang dihitung di sini
dibandingkan terhadap token yang benar-benar dipakai layout.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent
CSS = (ROOT / "dashboard" / "assets" / "style.css").read_text(encoding="utf-8")
HUD = (ROOT / "dashboard" / "layouts" / "hud.py").read_text(encoding="utf-8")
FIGS = (ROOT / "dashboard" / "layouts" / "hud_figures.py").read_text(encoding="utf-8")


def token(name):
    """Nilai px sebuah token CSS, atau None kalau tidak ada."""
    m = re.search(rf"{name}\s*:\s*(\d+)px", CSS)
    return int(m.group(1)) if m else None


def _walk_ids(node):
    """Kumpulkan semua component id di pohon Dash."""
    out = []
    cid = getattr(node, "id", None)
    if isinstance(cid, str):
        out.append(node)
    ch = getattr(node, "children", None)
    if isinstance(ch, (list, tuple)):
        for c in ch:
            out.extend(_walk_ids(c))
    elif ch is not None and not isinstance(ch, str):
        out.extend(_walk_ids(ch))
    return out


class TestZoneBudget(unittest.TestCase):
    """Setiap zona harus punya tinggi yang cukup untuk isinya."""

    def test_header_token_is_single_source(self):
        head = token("--h-head")
        self.assertIsNotNone(head, "token --h-head hilang")
        self.assertEqual(
            head, 22,
            "tinggi header harus seragam di semua panel; kalau berubah, "
            "semua perhitungan budget di bawah ikut bergeser",
        )

    def test_zone3_cells_fit_their_graphs(self):
        """
        Sel zone-3 setinggi --h-zone3, dikurangi header dan padding.
        Setiap grafik di dalamnya harus muat, dengan sisa yang wajar —
        bukan nol, yang berarti desain meleset dan graf jadi terpotong.
        """
        zone3 = token("--h-zone3")
        head = token("--h-head")
        inner = zone3 - head - 8  # --sp-2 = 4px per sisi

        for name in ("--h-equity", "--h-log", "--h-neural"):
            h = token(name)
            self.assertIsNotNone(h, f"token {name} hilang")
            self.assertLessEqual(
                h, inner,
                f"{name}={h}px melebihi {inner}px yang tersedia di sel zone-3",
            )

    def test_analytics_panel_fits_its_kpi_table(self):
        """
        Panel analytics: dua blok sparkline + tabel 6 baris.

        Dihitung ulang karena screenshot menunjukkan baris terakhir
        terpotong — tabel butuh 120px tapi hanya dapat 106px.
        """
        zone3 = token("--h-zone3")
        head = token("--h-head")
        spark_graph = token("--h-spark")
        spark_label = 12
        inner = zone3 - head - 8

        blocks = 2 * (spark_label + spark_graph)
        table_avail = inner - blocks - 8

        # 6 baris: font 10px * line-height 1.4 + padding 1px*2 + border 1px
        row_h = 10 * 1.4 + 2 * 1 + 1
        need = 6 * row_h

        self.assertLessEqual(
            need, table_avail,
            f"tabel KPI butuh {need:.0f}px, hanya {table_avail}px tersedia",
        )

    def test_wallet_rail_fits_its_hero(self):
        """
        Rail metrics: PnL besar + angka pendukung SEJAJAR horizontal.

        Kalau hero nanti dikembalikan ke susunan vertikal, tinggi rail
        ini tidak akan cukup dan angkanya akan terpotong.
        """
        rail = token("--h-rail")
        head = token("--h-head")
        inner = rail - head - 4  # rail body padding --sp-1

        hero_px = int(re.search(
            r"\.rail-hero \.giant-pnl\s*\{[^}]*font-size:\s*(\d+)px", CSS
        ).group(1))
        self.assertLessEqual(
            hero_px, inner,
            f"hero PnL {hero_px}px tidak muat di rail {inner}px",
        )

    def test_giant_pnl_uses_the_type_scale(self):
        """
        Regression: `.giant-pnl` pernah 52px sementara rail-nya 62px.

        Nilai itu dulu ditulis langsung dan tidak pernah ikut dikunci,
        jadi overhaul layout tidak menyentuhnya dan angkanya terpotong
        tanpa error apa pun.
        """
        m = re.search(r"\.giant-pnl\s*\{[^}]*font-size:\s*([\w()\-]+)", CSS)
        self.assertIsNotNone(m, "aturan .giant-pnl tidak ditemukan")
        value = m.group(1)
        self.assertTrue(
            value.startswith("var(") or value.startswith("calc("),
            f"font-size .giant-pnl harus pakai token, bukan nilai lepas: {value}",
        )

    def test_symbol_pnl_panel_replaces_decision_tree(self):
        """
        Panel PnL per simbol menggantikan decision tree.

        Decision tree isinya 6 kotak statis dengan badge yang selalu sama
        ("6/6 COMPLETE"), jadi tidak pernah mengubah keputusan apa pun.
        Panel baru menjawab pertanyaan yang belum terjawab: simbol mana
        yang membawa PnL dan mana yang memakannya.
        """
        from dashboard.layouts.hud import create_symbol_pnl_panel

        panel = create_symbol_pnl_panel()
        ids = {c.id for c in _walk_ids(panel)}
        self.assertIn(
            "hud-symbol-pnl-rail", ids,
            "panel PnL harus punya rail yang diisi callback",
        )

    def test_decision_tree_markup_is_gone(self):
        """Markup decision tree tidak boleh tersisa di layout."""
        from dashboard.layouts import hud
        src = (ROOT / "dashboard" / "layouts" / "hud.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("tree-step-node", src, "helper _tree_node belum dihapus")
        self.assertNotIn("hud-tree-node-", src, "id tree masih dipakai")

    def test_scanner_convergence_fits_the_stack(self):
        """
        Scanner duduk di stack zone-2 bersama panel posisi.

        Kurvanya harus muat di sisa tinggi stack setelah baris odds dan
        strip agen. Token --h-conv pernah 113px saat hanya 95px tersedia.
        """
        zone2 = token("--h-zone2")
        head = token("--h-head")
        stack_inner = zone2 - head - 8
        gutter = 6

        # Stack berisi dua panel yang membagi tinggi SETENGAH (flex 1 1 0)
        # dikurangi satu gutter di antaranya.
        gscan = stack_inner / 2 - gutter / 2

        conv = token("--h-conv")
        odds = int(re.search(
            r"\.scanner-odds-row\s*\{[^}]*min-height:\s*(\d+)px", CSS
        ).group(1)) if re.search(
            r"\.scanner-odds-row\s*\{[^}]*min-height:\s*(\d+)px", CSS) else 44
        strip, pad = 16, 8
        need = odds + conv + strip + pad
        self.assertLessEqual(
            need, gscan,
            f"scanner butuh {need}px, stack hanya menyediakan {gscan:.0f}px",
        )

    def test_no_figure_declares_its_own_height(self):
        """
        Tinggi hanya boleh dimiliki `div`.

        Figure yang ikut mengklaim tinggi menghasilkan selisih px yang
        meninggalkan strip mati di setiap refresh — persis bug yang
        terjadi saat neural graph 260px di dalam div 272px.
        """
        self.assertNotRegex(
            FIGS, r"height=\d+",
            "figure tidak boleh punya height=; div pemiliknya yang menentukan",
        )

    def test_inline_graph_heights_use_tokens(self):
        """
        Tinggi chart inline harus token, bukan px lepas.

        Dulu ada 7 tinggi (140/115/210/272/45/45/22) yang tersebar di dua
        file dan tidak pernah bisa salingavic agreement.
        """
        for m in re.finditer(r'style=\{"height":\s*"([^"]+)"', HUD):
            value = m.group(1)
            self.assertTrue(
                value.startswith("var(--"),
                f"tinggi inline harus token, bukan `{value}`",
            )


if __name__ == "__main__":
    unittest.main()
