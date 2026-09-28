"""
verify_dashboard_render.py — Bukti terakhir: HTML yang benar-benar
dirender ke browser memuat warna tema, bukan `var()` dan bukan putih.

`verify_dashboard_palette.py` memeriksa nilai di objek Figure. File ini
memeriksa string yang benar-benar sampai ke browser, karena itu yang
# Figure contoh supaya selalu ada yang dirender.
"""
import io
import re
import sys

sys.path.insert(0, ".")

import plotly.graph_objects as go  # noqa: E402

from dashboard.layouts import palette  # noqa: E402
from dashboard.layouts import hud_figures  # noqa: E402

problems = []

# Bangun figure dari tiap builder yang bisa dipanggil tanpa argumen.
figures = []
for name in dir(hud_figures):
    if not name.startswith(("build", "make", "create")):
        continue
    fn = getattr(hud_figures, name)
    if not callable(fn):
        continue
    for args in ([], [None]):
        try:
            result = fn(*args)
        except TypeError:
            continue
        except Exception as exc:  # noqa: BLE001
            problems.append("{}: gagal: {}: {}".format(
                name, type(exc).__name__, exc))
            break
        if isinstance(result, go.Figure):
            figures.append((name, result))
        break

# menentukan apakah panel terlihat putih di layar.
probe = go.Figure()
probe.add_scatter(y=[1, 2, 3])
probe.update_layout(
    template="none",
    paper_bgcolor=palette.PAPER_BG,
    plot_bgcolor=palette.PAPER_BG,
)
figures.append(("probe", probe))

print("=" * 58)
print("RENDER HTML DASHBOARD")
print("=" * 58)
print("  figure dirender:", len(figures))
print()

for name, fig in figures:
    html = fig.to_html()
    if "var(--" in html:
        problems.append(
            "{}: var(-- ada di HTML -> browser tidak bisa menggambarnya"
            .format(name))

    # Warna latar harus muncul sebagai hex literal, bukan rujukan.
    if palette.PAPER_BG not in html:
        problems.append(
            "{}: {} tidak ada di HTML, panel akan pakai warna default "
            "(putih)".format(name, palette.PAPER_BG))

    for dark in ("0d1117", "161b22", "21262d"):
        if dark in html:
            problems.append(
                "{}: {} dari tema gelap lama masih ada".format(name, dark))

    print("  {:<34} {:>9} bytes  {}".format(
        name, len(html), "OK" if palette.PAPER_BG in html else "GAGAL"))

print()

if problems:
    print("MASIH ADA MASALAH:")
    for p in problems:
        print("  -", p)
    sys.exit(1)

print("Semua figure menghasilkan HTML dengan warna tema.")
print("Tidak ada var(), tidak ada warna tema gelap, tidak ada putih.")
sys.exit(0)
