"""
verify_css_resolution.py — Buktikan semua token CSS benar-benar resolve.

Browser membuang deklarasi yang|teamnya invalid (mis. `var()` siklik)
dengan diam-diam: property-nya jatuh ke nilai awal, jadi panel kehilangan
background dan border tanpa satu pun error di console.

Skrip ini meniru aturan itu: resolve setiap token, tandai yang siklik
atau tak terdefinisi, lalu hitung berapa deklarasi yang benar-benar
berdampak. Guard yang sama ada di tests/test_layout_contract.py; skrip
ini hanya menampilkan angkanya.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent
CSS = ROOT / "dashboard" / "assets" / "style.css"
OUT = ROOT / "data_store" / "css_resolution.txt"

_lines = []


def say(*a):
    _lines.append(" ".join(str(x) for x in a))


def strip_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def root_block(css: str) -> str:
    m = re.search(r":root\s*\{", css)
    if not m:
        return ""
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
    return css[i:j]


def main() -> int:
    raw = CSS.read_text(encoding="utf-8")
    css = strip_comments(raw)
    root = root_block(css)

    defs = dict(re.findall(r"(--[a-z0-9-]+)\s*:\s*([^;]+);", root))

    def resolve(name, seen=frozenset()):
        if name in seen:
            return "CYCLE"
        m = re.fullmatch(r"var\((--[a-z0-9-]+)\)", defs.get(name, "").strip())
        if m:
            return resolve(m.group(1), seen | {name})
        return defs.get(name, "")

    say("=" * 64)
    say("RESOLUSI TOKEN CSS")
    say("=" * 64)
    say(f"  token didefinisikan : {len(defs)}")

    cyclic = sorted(n for n in defs if resolve(n) == "CYCLE")
    say(f"  token SIKLIK        : {len(cyclic)}  {cyclic if cyclic else ''}")

    used = set(re.findall(r"var\((--[a-z0-9-]+)", css))
    undefined = sorted(used - set(defs))
    say(f"  token tak terdefinisi: {len(undefined)}  {undefined if undefined else ''}")

    # How many declarations are actually affected?
    impacted = 0
    for prop in ("background", "background-color", "color", "border",
                 "border-top", "border-bottom", "border-color", "fill",
                 "text-shadow"):
        for m in re.finditer(rf"{prop}\s*:\s*([^;}}]+)", css):
            if re.search(r"var\((--[a-z0-9-]+)\)", m.group(1)):
                impacted += 1
    say("")
    say(f"  deklarasi memakai var() : {impacted}")
    if cyclic or undefined:
        say("  >>> ADA TOKEN RUSAK — panel akan kehilangan warna/garis")

    # Spot-check the tokens that carry the entire visual system.
    say("")
    say("  spot check:")
    for name in ("--bg-app", "--bg-card", "--fg", "--line", "--pos", "--neg"):
        v = resolve(name)
        ok = v not in ("CYCLE", None, "")
        say(f"    {name:<12} = {v!r}  {'OK' if ok else 'BROKEN'}")

    say("")
    say("=" * 64)
    say("HASIL: " + ("SEMUA RESOLVE" if not cyclic and not undefined
                      else "ADA YANG RUSAK"))
    say("=" * 64)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(_lines) + "\n", encoding="utf-8")
    print(f"Laporan: {OUT}")
    return 0 if not cyclic and not undefined else 2


if __name__ == "__main__":
    sys.exit(main())
