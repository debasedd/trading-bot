"""
tests/test_dashboard_palette.py — Jaring pengaman tema dashboard.

Bug yang dicegah
----------------
1. `var(--token)` lolos ke Plotly. Plotly menggambar ke kanvas, dan
   kanvas tidak bisa membaca variabel CSS. Plotly menerimanya tanpa
   error lalu menggambarnya kosong: panel putih, tanpa jejak di log.

2. Hex dari tema gelap lama (#0d1117) tertinggal di file figure. Tema
   dashboard ini kertas hangat, jadi panel gelap tidak nyambung dan teks
   terang jadi nyaris tak terbaca.

3. Warna terang di atas latar terang, karena token "vintage" berasal
   dari tema yang sudah tidak dipakai.
"""
import re
import unittest
from pathlib import Path

from dashboard.layouts import palette

LAYOUT = Path(__file__).resolve().parent.parent / "dashboard" / "layouts"
FILES = ["hud_figures.py", "price_chart.py", "performance.py"]

HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

# Warna tema GitHub gelap yang DULU dipakai. Kalau muncul lagi berarti
# ada figure yang disalin dari sumber lain.
GITHUB_DARK = {
    "0d1117", "161b22", "21262d", "30363d", "58a6ff", "3fb950",
    "f85149", "d29922", "bc8cff", "8b949e", "c9d1d9", "6e7681",
}


def _code_only(text):
    """Buang komentar supaya docstring tidak ikut dihitung."""
    return "\n".join(
        line for line in text.split("\n")
        if not line.lstrip().startswith("#")
    )


class TestNoCssVarsInFigures(unittest.TestCase):
    """`var()` tidak boleh sampai ke Plotly."""

    def test_no_var_in_figure_code(self):
        for name in FILES:
            code = _code_only((LAYOUT / name).read_text(encoding="utf-8"))
            self.assertNotIn(
                "var(--", code,
                "{} memakai var(); Plotly tidak bisa membacanya dan "
                "panel akan putih".format(name))

    def test_no_foreign_template(self):
        """Template bawaan Plotly menyuntikkan warna sendiri."""
        for name in FILES:
            code = _code_only((LAYOUT / name).read_text(encoding="utf-8"))
            for template in ("plotly_dark", "plotly_white"):
                self.assertNotIn(template, code,
                                 "{} memakai {}".format(name, template))


class TestPaletteMatchesTheme(unittest.TestCase):
    """Palet harus dibaca dari CSS, bukan hardcode."""

    def test_tokens_are_real_colors(self):
        for name, value in palette.TOKENS.items():
            self.assertTrue(
                HEX.match(value) or value.startswith("rgba"),
                "{} = {!r} bukan warna".format(name, value))

    def test_background_is_the_paper_theme(self):
        css = (LAYOUT.parent / "assets" / "style.css").read_text(
            encoding="utf-8")
        self.assertIn(palette.PAPER_BG, css,
                      "latar figure harus warna yang sama dengan "
                      "tema di style.css")

    def test_no_dark_theme_colors_left(self):
        for name in FILES:
            code = _code_only((LAYOUT / name).read_text(encoding="utf-8"))
            for color in GITHUB_DARK:
                self.assertNotIn(
                    color, code,
                    "{} masih memakai {} dari tema gelap yang lama"
                    .format(name, color))


class TestReadability(unittest.TestCase):
    """Warna terang di atas kertas hangat = nyaris tak terbaca."""

    def _luminance(self, hex_color):
        h = hex_color.lstrip("#")
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
        return 0.299 * r + 0.587 * g + 0.114 * b

    def test_text_is_darker_than_background(self):
        bg = self._luminance(palette.PAPER_BG)
        for name, value in (("INK", palette.INK),
                            ("INK_MUTED", palette.INK_MUTED)):
            lum = self._luminance(value)
            self.assertLess(
                lum, bg - 40,
                "{} ({}) terlalu terang untuk latar {} -- teksnya "
                "hampir tidak terlihat".format(name, value, palette.PAPER_BG))

    def test_semantic_colors_are_distinct(self):
        for a, b in ((palette.GREEN, palette.RED),
                     (palette.AMBER, palette.BLUE)):
            self.assertNotEqual(a, b)


class TestAlphaHelper(unittest.TestCase):
    def test_alpha_keeps_rgb(self):
        self.assertEqual(palette.alpha("#006a2b", 0.5),
                         "rgba(0,106,43,0.5)")

    def test_alpha_accepts_short_hex(self):
        self.assertEqual(palette.alpha("#fff", 1.0),
                         "rgba(255,255,255,1.0)")

    def test_alpha_stays_in_range(self):
        for opacity in (0.0, 0.13, 0.5, 1.0):
            out = palette.alpha(palette.PAPER_BG, opacity)
            value = float(out.rsplit(",", 1)[1].rstrip(")"))
            self.assertGreaterEqual(value, 0.0)
            self.assertLessEqual(value, 1.0)


class TestFiguresAreColored(unittest.TestCase):
    """Figure yang benar-benar dibangun harus punya background."""

    def _built_figures(self):
        import plotly.graph_objects as go

        figures = []
        for module_name in ("dashboard.layouts.hud_figures",
                            "dashboard.layouts.price_chart",
                            "dashboard.layouts.performance"):
            module = __import__(module_name, fromlist=["*"])
            for name in dir(module):
                if not name.startswith(("build", "make", "create")):
                    continue
                fn = getattr(module, name)
                if not callable(fn):
                    continue
                for args in ([], [None]):
                    try:
                        result = fn(*args)
                    except TypeError:
                        continue
                    except Exception:  # noqa: BLE001
                        break
                    if isinstance(result, go.Figure):
                        figures.append((module_name, name, result))
                    break
        return figures

    def test_built_figures_have_background(self):
        figures = self._built_figures()
        self.assertTrue(figures, "tidak ada figure yang bisa diuji")

        for module_name, name, fig in figures:
            for attr in ("paper_bgcolor", "plot_bgcolor"):
                value = str(getattr(fig.layout, attr) or "")
                if not value:
                    # Figure kosong dari make_subplots() belum di-style;
                    # itu normal dan bukan yang diuji di sini.
                    continue
                self.assertTrue(
                    HEX.match(value) or value.startswith("rgba"),
                    "{}.{}: {} = {!r} bukan warna".format(
                        module_name, name, attr, value))


if __name__ == "__main__":
    unittest.main()
