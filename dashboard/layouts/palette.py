"""
dashboard/layouts/palette.py — Warna Plotly yang sejati dengan tema.

Masalah yang diselesaikan modul ini
------------------------------------
Plotly menggambar ke dalam kanvas. Canvas tidak bisa membaca variabel
CSS: `var(--bg-app)` bukan warna, itu rujukan. Plotly menerimanya tanpa
error, lalu menggambarnya sebagai tidak berwarna -- hasilnya panel putih
yang tidak nyambung dengan tema.

Dulu file figure memakai `var(--...)` karena terlihat rapi, dan sekarang
pakai hex gelap `#0d1117` karena hex itu "pasti warna". Dua-duanya
menempel pada tema yang salah: tema dashboard ini kertas hangat
    # dua-duanya menempel pada tema yang salah: tema dashboard ini kertas
    # hangat (#dfd5b8), bukan gelap. Hex gelap di atas kertas memberi
    # panel gelap, dan \ar()\ memberi panel putih.
`var()` membuat panel putih.

Modul ini membaca `:root` dari `style.css` SEKALI di import, lalu
mengekspos warna sebagai hex. Jadi tema berubah di satu tempat dan
semua figure ikut berubah, tanpa ada `var()` yang salah Begriff.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict

_CSS = Path(__file__).resolve().parent.parent / "assets" / "style.css"

# Dicadangkan kalau file CSS tidak terbaca. Nilainya sama dengan
# `:root` saat ini, jadi_failure tidak mengubah tampilan mendadak.
_FALLBACK: Dict[str, str] = {
    "bg_app": "#dfd5b8",
    "bg_card": "#e5dbc0",
    "bg_inset": "#ede4cc",
    "fg": "#000000",
    "fg_dim": "#3a352c",
    "fg_muted": "#555042",
    "line": "#000000",
    "rule": "#ccc2a5",
    "rule_soft": "#b5ab8d",
    "pos": "#006a2b",
    "neg": "#9e1b16",
    "warn": "#8a5200",
    "accent": "#005a8c",
}

_VAR_RE = re.compile(r"(--[a-z0-9-]+)\s*:\s*([^;]+);", re.IGNORECASE)


def _load() -> Dict[str, str]:
    """Baca token warna dari `:root` di style.css."""
    try:
        text = _CSS.read_text(encoding="utf-8")
    except OSError:
        return dict(_FALLBACK)

    start = text.find(":root")
    if start < 0:
        return dict(_FALLBACK)

    # Batasi ke blok :root supaya token dari blok lain tidak ikut.
    depth = 0
    end = len(text)
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                end = i
                break

    out = dict(_FALLBACK)
    for name, value in _VAR_RE.findall(text[start:end]):
        key = name[2:].replace("-", "_")
        value = value.strip()
        if key in out and value:
            out[key] = value
    return out


TOKENS = _load()

# Nama yang dipakai file figure. Sengaja memakai nama lama supaya
# diff-nya kecil dan tidak menyentuh semua call site.
PAPER_BG = TOKENS["bg_app"]
CARD_BG = TOKENS["bg_card"]
INSET_BG = TOKENS["bg_inset"]
INK = TOKENS["fg"]
INK_DIM = TOKENS["fg_dim"]
INK_MUTED = TOKENS["fg_muted"]
RULE = TOKENS["line"]
RULE_SOFT = TOKENS["rule_soft"]

GREEN = TOKENS["pos"]
RED = TOKENS["neg"]
AMBER = TOKENS["warn"]
# Alias dipakai di peta warna node neural net.
WARN = AMBER
BLUE = TOKENS["accent"]


def alpha(hex_color: str, opacity: float) -> str:
    """Ubah hex jadi rgba dengan alfa tetap.

    Dipakai untuk garis bantu dan area isi. Tanpa ini, tiap figure
    menulis literal rgba sendiri dan mulai menyimpang dari palet.
    """
    h = hex_color.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r = int(h[0:2], 16)
    g = int(h[2:4], 16)
    b = int(h[4:6], 16)
    return "rgba({},{},{},{})".format(r, g, b, round(float(opacity), 3))


def apply_dark_figure(fig, height: int = None):
    """
    Set style figure agar menyatu dengan tema.

    Dipakai semua figure Plotly di dashboard. `template` dikunci ke
    `none` supaya template bawaan Plotly tidak menyuntikkan warna putih
    di atas palet tema.
    """
    fig.update_layout(
        template="none",
        paper_bgcolor=PAPER_BG,
        plot_bgcolor=PAPER_BG,
        font=dict(
            color=INK,
            family="'Share Tech Mono', ui-monospace, monospace",
        ),
    )
    if height is not None:
        fig.update_layout(height=height)
    return fig
