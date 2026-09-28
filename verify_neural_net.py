"""
verify_neural_net.py — Bukti perbaikan konstelasi neural net.

Memeriksa empat hal yang complained user:
  1. Slot token STABIL — tidak melompat saat token lain hilang.
  2. TIDAK ada koordinat kembar.
  3. `MARKET` (sentinel sentimen) tidak masuk sebagai koin.
  4. Sisi sibuk punya pola dash (untuk animasi CSS), sisi sepi solid.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dashboard.layouts.hud_figures import (
    create_neural_net_fig,
    NEURAL_SYSTEM_NODES,
    TOKEN_SLOTS,
)

OUT = Path(__file__).parent / "data_store" / "neural_check.txt"
_lines = []


def say(*a):
    _lines.append(" ".join(str(x) for x in a))


def slots_for(signal_map):
    fig = create_neural_net_fig(signal_map=signal_map)
    tr = [t for t in fig.data if t.mode == "markers+text"][0]
    return {txt: (x, y) for x, y, txt in zip(tr.x, tr.y, tr.text)}


def mk(symbols, direction="LONG", conf=0.6):
    return {f"{s}/USDT:USDT": {
        "direction": direction, "confidence": conf, "price_change": 0.002
    } for s in symbols}


def main() -> int:
    ok = True

    full = ["BTC", "ETH", "SOL", "XRP", "BNB", "DOGE",
            "NEAR", "HYPE", "ZEC", "ONDO", "ENA", "XPL"]

    # --- 1. Stabilitas slot -----------------------------------------
    say("=" * 66)
    say("1. STABILITAS SLOT TOKEN")
    say("=" * 66)
    base = slots_for(mk(full))
    say(f"  token aktif : {len(base)}  (termasuk 10 simpul sistem)")
    for s in full:
        if s in base:
            say(f"    {s:<6} -> ({base[s][0]:+.2f}, {base[s][1]:+.2f})")

    say("")
    say("  uji: hapus 4 token, apakah sisa token BERGERAK?")
    reduced = [s for s in full if s not in ("DOGE", "HYPE", "ZEC", "ONDO")]
    after = slots_for(mk(reduced))
    moved = [s for s in reduced if s in after and after[s] != base[s]]
    if moved:
        say(f"    PINDAH: {moved}")
        ok = False
    else:
        say("    TIDAK ADA token yang pindah — stabil")

    say("")
    say("  uji: hanya 3 koin mayor, apakah mayor tetap di tempat?")
    minor = slots_for(mk(["BTC", "ETH", "SOL"]))
    moved2 = [s for s in ("BTC", "ETH", "SOL") if minor[s] != base[s]]
    if moved2:
        say(f"    PINDAH: {moved2}")
        ok = False
    else:
        say("    TIDAK ADA — mayor juga stabil")

    # --- 2. Tidak ada koordinat kembar ------------------------------
    say("")
    say("=" * 66)
    say("2. KOORDINAT UNIK")
    say("=" * 66)
    coords = list(base.values())
    dupes = len(coords) - len(set(coords))
    sys_coords = {(v[0], v[1]) for v in NEURAL_SYSTEM_NODES.values()}
    clash = [c for c in coords if c in sys_coords and c in
             {(base.get(s, (None, None))[0], base.get(s, (None, None))[1])
              for s in full}]
    say(f"  node        : {len(base)}")
    say(f"  duplikat    : {dupes}")
    say(f"  tabrakan dgn simpul sistem : {len(clash)}")
    if dupes:
        ok = False
    else:
        say("  >>> tidak ada simpul bertumpuk")

    # --- 3. MARKET tidak jadi token ---------------------------------
    say("")
    say("=" * 66)
    say("3. SENTINEL MARKET DITOLAK")
    say("=" * 66)
    sm = mk(["BTC", "ETH"])
    sm["MARKET"] = {"direction": "POSITIVE", "confidence": 0.4,
                    "price_change": 0.0}
    s_with = slots_for(sm)
    say(f"  'MARKET' muncul sebagai simpul : {'MARKET' in s_with}")
    if "MARKET" in s_with:
        ok = False
    else:
        say("  >>> MARKET tidak dianggap koin")

    # --- 4. Sisi: dash untuk sibuk, solid untuk sepi ---------------
    say("")
    say("=" * 66)
    say("4. SISI GRAF (DASAR ANIMASI CSS)")
    say("=" * 66)
    fig = create_neural_net_fig(
        signal_map=mk(["BTC", "ETH", "SOL"]),
        activity_map={"analysis_agent": 1.0, "decision_agent": 0.9,
                      "execution_agent": 0.8, "news_agent": 0.7},
        flow_map={"allMids": 1.0, "l2Book": 0.8, "candle": 0.5,
                  "trades": 0.6},
    )
    lines = [t for t in fig.data if t.mode == "lines"]
    dashed = [t for t in lines if t.line.dash and "px" in str(t.line.dash)]
    solid = [t for t in lines if not t.line.dash or t.line.dash == "solid"]
    splined = [t for t in lines if t.line.shape == "spline"]
    say(f"  total trace sisi  : {len(lines)}")
    say(f"  putus-putus (dashes, dianimasikan) : {len(dashed)}")
    say(f"  solid (statis)                    : {len(solid)}")
    say(f"  memakai spline                    : {len(splined)}")
    say(f"  trace simpul                      : "
        f"{len([t for t in fig.data if t.mode == 'markers+text'])}")
    say(f"  tinggi figure   : {fig.layout.height}px")
    say(f"  uirevision      : {fig.layout.uirevision}")
    if len(splined) != len(lines):
        say("  >>> PERINGATAN: ada sisi tanpa spline")
        ok = False
    if not dashed:
        say("  >>> GAGAL: tidak ada sisi putus-putus untuk dianimasikan")
        ok = False
    if len(dashed) + len(solid) != len(lines):
        say("  >>> PERINGATAN: klasifikasi sisi tidak lengkap")
        ok = False

    say("")
    say("=" * 66)
    say("HASIL: " + ("SEMUA LULUS" if ok else "ADA MASALAH"))
    say("=" * 66)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(_lines) + "\n", encoding="utf-8")
    print(f"Laporan: {OUT}")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
