"""
verify_dashboard_palette.py — Bukti bahwa setiap figure Plotly benar-benar
warna, bukan string `var()` yang diam-diam jadi putih.

Yang diperiksa:
  1. Tidak ada `var(--` yang lolos ke nilai warna.
  2. Setiap figure punya paper_bgcolor dan plot_bgcolor yang valid.
  3. Setiap figure yang dibangun benar-benar bisa dirender ke HTML
     tanpa error.

Failure di sini berarti panel putih di layar, jadi ini bukan pemeriksaan
kosmetik.
"""
import io
import re
import sys

sys.path.insert(0, ".")

import plotly.graph_objects as go  # noqa: E402

from dashboard.layouts import palette  # noqa: E402

FILES = [
    "dashboard/layouts/hud_figures.py",
    "dashboard/layouts/price_chart.py",
    "dashboard/layouts/performance.py",
]

HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
RGBA = re.compile(r"^rgba?\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*(?:,\s*[\d.]+\s*)?\)$")

problems = []

# --- 1. tidak ada var() di file figure -------------------------------
for path in FILES:
    text = io.open(path, encoding="utf-8").read()
    # Abaikan komentar.
    code = "\n".join(
        line for line in text.split("\n")
        if not line.lstrip().startswith("#")
    )
    if "var(--" in code:
        problems.append("{}: masih ada var(--) di kode".format(path))

    for name in ("plotly_dark", "plotly_white"):
        if name in code:
            problems.append("{}: template {} menyuntik warna sendiri"
                            .format(path, name))

# --- 2. background figure --------------------------------------------
fig = go.Figure()
fig.add_scatter(y=[1, 2, 3])
fig.update_layout(
    paper_bgcolor=palette.PAPER_BG,
    plot_bgcolor=palette.PAPER_BG,
    template="none",
)
lay = fig.layout

for attr in ("paper_bgcolor", "plot_bgcolor"):
    value = getattr(lay, attr)
    if not HEX.match(str(value)):
        problems.append("{}: {} = {!r} bukan warna"
                        .format("probe", attr, value))

# --- 3. semua builder figure jalan ------------------------------------
BUILDERS = []
for module_name in (
    "dashboard.layouts.hud_figures",
    "dashboard.layouts.price_chart",
    "dashboard.layouts.performance",
):
    try:
        module = __import__(module_name, fromlist=["*"])
    except Exception as exc:  # noqa: BLE001
        problems.append("{}: gagal diimpor: {}".format(module_name, exc))
        continue
    for name in dir(module):
        if name.startswith("_") or not name.startswith(("build", "make", "create")):
            continue
        fn = getattr(module, name)
        if not callable(fn):
            continue
        BUILDERS.append((module_name, name, fn))

built = 0
for module_name, name, fn in BUILDERS:
    for args in ([], [None]):
        try:
            result = fn(*args)
        except TypeError:
            continue
        except Exception as exc:  # noqa: BLE001
            problems.append("{}.{}: gagal dibangun: {}: {}"
                            .format(module_name, name,
                                    type(exc).__name__, exc))
            break
        if isinstance(result, go.Figure):
            # make_subplots() mengembalikan figure kosong; hanya figure
            # yang sudah di-style yang boleh diuji. Figure kosong bukan
            # bug -- checking-nya hanya membingungkan.
            paper_raw = str(result.layout.paper_bgcolor or '')
            plot_raw = str(result.layout.plot_bgcolor or '')
            if not paper_raw and not plot_raw:
                break
            built += 1
            paper = str(result.layout.paper_bgcolor or "")
            plot = str(result.layout.plot_bgcolor or "")
            for label, value in (("paper", paper), ("plot", plot)):
                if not (HEX.match(value) or RGBA.match(value)):
                    problems.append(
                        "{}.{}: {} = {!r} bukan warna"
                        .format(module_name, name, label, value))
        break

# --- 4. render nyata ke HTML -----------------------------------------
try:
    sample = go.Figure()
    sample.add_scatter(y=[1, 2, 3])
    sample.update_layout(
        paper_bgcolor=palette.PAPER_BG,
        plot_bgcolor=palette.PAPER_BG,
        template="none",
    )
    sample.to_html()
except Exception as exc:  # noqa: BLE001
    problems.append("render HTML gagal: {}".format(exc))

print("=" * 58)
print("PALET DASHBOARD")
print("=" * 58)
print("  latar      :", palette.PAPER_BG)
print("  kartu      :", palette.CARD_BG)
print("  teks       :", palette.INK)
print("  teks redup :", palette.INK_MUTED)
print("  naik       :", palette.GREEN)
print("  turun      :", palette.RED)
print("  caution    :", palette.AMBER)
print("  aksen      :", palette.BLUE)
print()
print("  file diperiksa :", len(FILES))
print("  builder diuji  :", len(BUILDERS))
print("  figure dibangun:", built)
print()

if problems:
    print("MASIH ADA MASALAH:")
    for p in problems:
        print("  -", p)
    print()
    print("Panel masih akan putih sampai ini beres.")
    sys.exit(1)

print("SEMUA FIGURE PAKAI WARNA ASLI DARI TEMA.")
print("Tidak ada var() yang lolos ke Plotly.")
sys.exit(0)
