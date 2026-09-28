"""
tests/test_layout_contract.py — Kontrak layout yang tidak boleh bocor.

Tiga guard yang tidak ada sebelumnya dan yang akan menangkap kelas bug
yang paling merusak:

1. SEMUA component id yang ditulis callback harus ada di layout. Missing
   id tidak melempar di Dash — panelnya diam-diam berhenti update,
   dan itu jauh lebih sulit DIDETEKSI daripada error 500. Guard ini
   membuat regresi itu gagal diam-diam.

2. Hanya tiga id Input/State yang WAJIB ada. Hanya tiga-tiganya yang
   benar-benar membuat Dash melempar 500 kalau hilang.

3. CSS tidak boleh punya warna hex di luar blok `:root`. Warna yang
   ditulis langsung di rule body adalah cara drift palet creeps
   kembali tanpa ada yang conscious.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent
HUD_PY = ROOT / "dashboard" / "layouts" / "hud.py"
APP_PY = ROOT / "dashboard" / "app.py"
CALLBACKS_PY = ROOT / "dashboard" / "callbacks" / "update_callbacks.py"
STYLE_CSS = ROOT / "dashboard" / "assets" / "style.css"


def _read(path):
    return path.read_text(encoding="utf-8")


def _mounted_ids_at_runtime():
    """
    Bangun layout sungguhan lalu kumpulkan id-nya.

    Regex di atas source tidak cukup: helper `_tree_node()` meneruskan id
    lewat argumen `id=`, jadi `id="hud-tree-node-feed"` tidak pernah muncul
    sebagai literal di hud.py — padahal enam id itu WAJIB hidup, karena
    callback menukar className mereka tiap 500 ms.
    """
    import sys
    sys.path.insert(0, str(ROOT))
    from dash import html

    from dashboard.layouts.hud import create_hud_layout

    found = set()

    def walk(node):
        # html component menyimpan props client-side di `_DashTreeLayout`
        cid = getattr(node, "id", None)
        if isinstance(cid, str):
            found.add(cid)
        children = getattr(node, "children", None)
        if children is None:
            return
        if isinstance(children, (list, tuple)):
            for c in children:
                walk(c)
        elif isinstance(children, (str, int, float)):
            return
        else:
            walk(children)

    walk(create_hud_layout())
    return found


def _dash_layout_ids():
    """Semua id di layout HUD + shim kompatibilitas di app.py."""
    ids = _mounted_ids_at_runtime()
    ids |= set(re.findall(r'id="([\w-]+)"', _read(APP_PY)))
    return ids


class TestComponentIdContract(unittest.TestCase):
    """ID yang ditulis callback harus punya tempat di DOM."""

    def test_every_callback_output_id_is_mounted(self):
        src = _read(CALLBACKS_PY)
        out_ids = set(re.findall(r'Output\(\s*"([\w-]+)"', src))
        mounted = _dash_layout_ids()

        missing = sorted(out_ids - mounted)
        self.assertFalse(
            missing,
            "callback menulis ke id yang tidak ada di layout "
            f"(panel akan diam-diam mati): {missing}",
        )

    def test_every_callback_input_id_is_mounted(self):
        """Input/State id hilang = Dash melempar 500. Ini kontrak keras."""
        src = _read(CALLBACKS_PY)
        in_ids = (
            set(re.findall(r'Input\(\s*"([\w-]+)"', src))
            | set(re.findall(r'State\(\s*"([\w-]+)"', src))
        )
        mounted = _dash_layout_ids()
        missing = sorted(in_ids - mounted)
        self.assertFalse(
            missing,
            f"Input/State id tidak ada di layout — Dash akan 500: {missing}",
        )

    def test_no_duplicate_component_ids(self):
        """
        id yang sama dua kali = Dash memakai yang terakhir, dan layout
        yang dirender berbeda dari yang dipikirkan.
        """
        seen, dupes = set(), set()
        for src in (_read(HUD_PY), _read(APP_PY)):
            for cid in re.findall(r'id="([\w-]+)"', src):
                if cid in seen:
                    dupes.add(cid)
                seen.add(cid)
        self.assertFalse(dupes, f"component id ganda: {sorted(dupes)}")


class TestStylesheetContract(unittest.TestCase):
    """CSS harus token-based dan motion-nya harus bisa dimatikan."""

    def _css_outside_root(self):
        """Bagian stylesheet di luar blok `:root`, tanpa komentar."""
        css = _read(STYLE_CSS)
        # Komentar dibuang dulu: nomor PR/issue sering disebut sebagai bukti
        # (`plotly/dash#3440`) dan itu bukan warna.
        css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
        m = re.search(r":root\s*\{", css)
        if not m:
            return css
        i = css.index("{", m.start())
        depth, j = 0, i
        while j < len(css):
            if css[j] == "{":
                depth += 1
            elif css[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        return css[:i] + css[j:]

    def test_no_hex_literals_outside_root(self):
        outside = self._css_outside_root()
        hexes = re.findall(r"#[0-9a-fA-F]{3,6}\b", outside)
        self.assertFalse(
            hexes,
            f"hex di luar :root — pakai var(--token): {sorted(set(hexes))}",
        )

    def test_no_permanent_will_change(self):
        """
        `will-change` adalah promosi layer GPU PERMANEN, bukan hint sekali
        pakai. Untuk properti non-compositable (box-shadow, text-shadow) atau
        layout (width) justru lebih buruk daripada tidak memberi hint.
        """
        self.assertNotIn("will-change", _read(STYLE_CSS))

    def test_has_global_reduced_motion_guard(self):
        """Setiap keyframe harus bisa dimatikan lewat preferensi sistem."""
        self.assertIn("prefers-reduced-motion", _read(STYLE_CSS))

    def test_animation_count_stays_small(self):
        """
        Guard jumlah animasi. Setiap tambahan keyframes harus punya
        alasan data, bukan karena terlihat hidup.
        """
        anims = re.findall(r"@keyframes\s+([\w-]+)", _read(STYLE_CSS))
        self.assertLessEqual(
            len(anims), 4,
            f"terlalu banyak keyframes ({len(anims)}): {anims}. "
            "Motion hanya untuk state yang benar-benar berubah.",
        )


if __name__ == "__main__":
    unittest.main()


class TestNoCyclicCssVariables(unittest.TestCase):
    """
    Guard yang mencegah kelas bug paling merusak di CSS ini.

    `var()` yang menunjuk dirinya sendiri (`--fg: var(--fg)`) tidak valid
    pada computed-value time. Browser membuang SELURUH deklarasi itu
    tanpa error, jadi property yang memakainya jatuh ke nilai awal —
    background jadi transparan, border jadi 0px. Panel tetap terlihat
    "ada" tapi kehilangan semua chrome-nya, dan tidak ada satu pun error
    di console untuk memberi tahu.

    Ini bukan hipotetis: tokenization yang menulis ulang `:root` pernah
    menghasilkan 19 token siklik, menetralkan ~80 deklarasi, dan
    membuat seluruh terminal tampil sebagai bidang krem tanpa pemisah.
    """

    def _tokens(self):
        # Tidak bisa split naif di '}' karena nilai seperti
        # rgba(0,0,0,0.13) mengandung kurung kurawal di dalamnya.
        # Ambil isi blok :root dengan pencocokan yang seimbang.
        css = _read(STYLE_CSS)
        m = re.search(r":root\s*\{", css)
        if not m:
            return {}
        i = css.index("{", m.start())
        depth, j = 0, i
        while j < len(css):
            if css[j] == "{":
                depth += 1
            elif css[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        # Komentar harus dibuang dulu: blok komentar di atas memuat
        # contoh literal seperti `--fg: var(--fg)`, dan regex akan
        # memakainya sebagai definisi asli sehingga token sebenarnya
        # tergeser satu slot.
        root = re.sub(r"/\*.*?\*/", "", css[i:j], flags=re.S)
        return dict(re.findall(
            r"(--[a-z0-9-]+)\s*:\s*([^;]+);", root
        ))

    def _resolve(self, name, defs, seen=frozenset()):
        if name in seen:
            return "CYCLE"
        m = re.fullmatch(
            r"var\((--[a-z0-9-]+)\)", defs.get(name, "").strip()
        )
        if m:
            return self._resolve(m.group(1), defs, seen | {name})
        return defs.get(name, "")

    def test_no_token_resolves_to_a_cycle(self):
        defs = self._tokens()
        cyclic = [n for n in defs if self._resolve(n, defs) == "CYCLE"]
        self.assertFalse(
            cyclic,
            f"token CSS siklik — browser akan membuang deklarasinya dan "
            f"property yang memakainya jadi transparan/0px: {cyclic}",
        )

    def test_no_token_references_itself_directly(self):
        defs = self._tokens()
        direct = [
            n for n, v in defs.items()
            if re.fullmatch(rf"var\(\s*{re.escape(n)}\s*\)", v.strip())
        ]
        self.assertFalse(
            direct,
            f"token mendefinisikan dirinya sendiri: {direct}",
        )

    def test_every_referenced_token_is_defined(self):
        """`var(--foo)` tanpa definisi = property itu hilang diam-diam."""
        css = _read(STYLE_CSS)
        defined = set(self._tokens())
        used = set(re.findall(r"var\((--[a-z0-9-]+)", css))
        missing = sorted(used - defined)
        self.assertFalse(
            missing,
            f"token dipakai tapi tidak didefinisikan: {missing}",
        )


class TestNoInlineHexInLayout(unittest.TestCase):
    """
    Layout tidak boleh menulis warna literal.

    Setiap warna harus datang dari token CSS. Warna inline adalah cara
    palet mulai menyimpang: `hud.py` pernah memegang 15 hex yang tidak
    cocok dengan token di style.css, sehingga panel yang sama tampil
    dalam dua warna berbeda tergantung elemen mana yang menggambarnya.
    """

    def test_layout_files_have_no_hex_literals(self):
        import re as _re

        for path in (HUD_PY, APP_PY):
            src = _read(path)
            # Buang komentar dulu: docstring boleh menyebut hex sebagai contoh.
            stripped = _re.sub(r'""".*?"""', "", src, flags=_re.S)
            stripped = _re.sub(r"#.*", "", stripped)
            hexes = _re.findall(r'#[0-9a-fA-F]{3,6}\b', stripped)
            self.assertFalse(
                hexes,
                f"{path.name} masih punya hex literal: {sorted(set(hexes))}. "
                "Pakai kelas CSS yang menarik dari token.",
            )

    def test_dropdown_has_css_class_not_inline_style(self):
        """
        `dcc.Dropdown` adalah React Select; `style=` hanya menjangkau
        elemen terluar, sedangkan kotak putih yang terlihat adalah
        `.Select-control` di dalam. Tanpa class CSS, dropdown selalu
        tampil sebagai kotak putih native.
        """
        src = _read(HUD_PY)
        i = src.index('id="hud-chart-symbol-select"')
        block = src[i:i + 700]
        self.assertIn(
            'className="symbol-select"', block,
            "dropdown perlu class CSS; style inline tidak menjangkau "
            ".Select-control",
        )

    def test_symbol_select_classes_exist_in_stylesheet(self):
        """Class yang dipakai layout harus benar-benar ada di CSS."""
        css = _read(STYLE_CSS)
        src = _read(HUD_PY)
        for cls in re.findall(r'className="([a-z0-9-]+)"', src):
            if cls in ("hud-panel", "hud-container"):
                continue
            self.assertIn(
                f".{cls}", css,
                f"className=\"{cls}\" dipakai layout tapi tidak ada di CSS",
            )


class TestDropdownUsesDash4Classes(unittest.TestCase):
    """
    Dash 4 menghapus react-select; kelas `.Select-*` sudah tidak ada.

    PR plotly/dash#3440 mengganti `dcc.Dropdown` ke komponen custom
    berbasis Radix. CSS yang menyasar `.Select-control` tidak match apa
    pun dan diam-diam jatuh ke tampilan bawaan — dropdown putih di
    dalam header hitam, persis yang terlihat di screenshot.
    """

    def test_stylesheet_has_no_select_class_rules(self):
        """Aturan_SELECT_* yang tidak match apa pun hanya menambah bobot."""
        css = _read(STYLE_CSS)
        stripped = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
        dead = re.findall(r"^\s*\.[Ss]elect-[\w-]+", stripped, flags=re.M)
        self.assertFalse(
            dead,
            f"selector react-select yang sudah mati di Dash 4: {sorted(set(dead))}. "
            "Pakai awalan `dash-dropdown-`.",
        )

    def test_stylesheet_targets_dash4_dropdown_classes(self):
        css = _read(STYLE_CSS)
        self.assertIn(
            ".symbol-select .dash-dropdown", css,
            "dropdown harus disasar lewat kelas `dash-dropdown-*` milik Dash 4",
        )

    def test_every_dash4_class_used_still_exists(self):
        """
        Setiap `dash-dropdown-*` yang dipakai CSS harus benar-benar ada
        di bundle Dash — supaya tidak mengulang kelas yang sudah mati.
        """
        import dash
        import os

        bundle = os.path.join(
            os.path.dirname(dash.__file__), "dcc", "async-dropdown.js"
        )
        if not os.path.exists(bundle):
            self.skipTest("bundle dropdown tidak ditemukan di instalasi ini")
        src = open(bundle, encoding="utf-8", errors="ignore").read()
        available = set(re.findall(r"dash-dropdown-[a-z-]+", src))

        used = set(re.findall(r"\.((?:dash-dropdown-[a-z-]+))", _read(STYLE_CSS)))
        unknown = sorted(used - available)
        self.assertFalse(
            unknown,
            f"selector dropdown tidak ada di Dash 4: {unknown}. "
            f"Yang tersedia: {sorted(available)}",
        )


class TestDropdownSelectorShape(unittest.TestCase):
    """
    Bentuk selector dropdown harus cocok dengan DOM sungguhan.

    Dua percobaan gagal sebelum ini, dan keduanya berakar sama: menulis
    `.symbol-select .dash-dropdown` seolah `symbol-select` adalah induk
    dari `dash-dropdown`. Padahal `className` Dash menempel pada elemen
    yang SAMA:

        <button class="dash-dropdown symbol-select">

    Descendant selector tidak akan pernah match satu elemen yang sama,
    sehingga seluruh aturan CSS diam-diam tidak berlaku dan dropdown
    jatuh ke gaya bawaan Dash.

    Test ini mengunci bentuknya supaya tidak diulang.
    """

    def test_no_selector_assumes_symbol_select_wraps_dash_dropdown(self):
        # Komentar dibuang dulu; hanya selector di body rules yang dihitung.
        # `.dash-dropdown-*` tetap sah karena itu benar-benar anak tombol.
        css = re.sub(r"/\*.*?\*/", "", _read(STYLE_CSS), flags=re.S)
        bad = re.findall(r"(?m)^\s*\.symbol-select\s+\.dash-dropdown\s*[,{]", css)
        self.assertFalse(
            bad,
            "`.symbol-select .dash-dropdown` tidak akan match: keduanya "
            "satu elemen (`<button class=\"dash-dropdown symbol-select\">`). "
            "Gunakan `.symbol-select` untuk elemennya sendiri.",
        )

    def test_bare_symbol_select_rule_exists(self):
        """Elemennya sendiri harus punya style, bukan cuma anak-anaknya."""
        css = _read(STYLE_CSS)
        m = re.search(r"^\.symbol-select\s*\{([^}]*)\}", css, re.M)
        self.assertIsNotNone(m, "tidak ada aturan untuk .symbol-select itu sendiri")
        body = m.group(1)
        for prop in ("background-color", "border", "font-size", "color"):
            self.assertIn(
                prop, body,
                f".symbol-select harus menentukan {prop} pada elemennya sendiri",
            )
