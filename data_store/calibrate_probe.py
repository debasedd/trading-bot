"""Kalibrasi konstanta browser: offset label Plotly, lebar label per karakter."""
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import data_store.constellation_measure as cm  # noqa: E402
from dashboard.layouts.hud_figures import (  # noqa: E402
    NEURAL_SYSTEM_NODES, TOKEN_SLOTS, create_neural_net_fig, _TOKEN_SLOT_REGISTRY,
)

toks = list(_TOKEN_SLOT_REGISTRY)[:len(TOKEN_SLOTS)]
sig = {t: {"direction": "LONG", "confidence": 1.0, "price_change": 0.004}
       for t in toks}
fig = create_neural_net_fig(
    signal_map=sig,
    activity_map={k: 1.0 for k in ("analysis_agent", "decision_agent",
                                   "execution_agent", "news_agent")},
    flow_map={k: 1.0 for k in ("l2Book", "allMids", "candle", "trades")},
)


def get_dom(vw=1920):
    import plotly.offline as po
    from playwright.sync_api import sync_playwright
    html = cm.HTML % {"css": cm.PANEL_CSS, "plotly": po.get_plotlyjs(),
                      "fig": json.dumps(fig.to_plotly_json())}
    tmp = cm.ROOT / "data_store" / "_dbg.html"
    tmp.write_text(html, encoding="utf-8")
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": vw, "height": 1100})
        pg.goto(tmp.as_uri())
        pg.wait_for_function(
            "()=>{const g=document.getElementById('hud-neural-graph');"
            "return g&&g.data&&g._fullLayout;}", timeout=20000)
        pg.wait_for_timeout(300)
        dom = pg.evaluate(cm.PROBE)
        b.close()
    return dom


dom = get_dom()
plot_w = dom["plot"]["w"] - dom["margin"]["l"] - dom["margin"]["r"]
plot_h = dom["plot"]["h"] - dom["margin"]["t"] - dom["margin"]["b"]
rx = dom["xrange"][1] - dom["xrange"][0]
ry = dom["yrange"][1] - dom["yrange"][0]
ppx = plot_w / rx
ppy = plot_h / ry
print(f"plot_area {plot_w:.1f} x {plot_h:.1f}  ppx {ppx:.3f} ppy {ppy:.3f}")
print(f"origin  x0={dom['margin']['l']} y0(top)={dom['margin']['t']}")

# Plotly textposition offset: 'bottom center' menaruh teks di bawah marker
# dengan jarak = radius marker + beberapa px. Hitung dari DOM.
tr = [t for t in fig.data if t.mode == "markers+text"][0]
rows = []
for label, x, y, pos, size in zip(tr.text, tr.x, tr.y, tr.textposition,
                                  tr.marker.size):
    n = next(n for n in dom["nodes"] if n["label"] == label)
    exp_mx = dom["margin"]["l"] + x * ppx
    exp_my = dom["margin"]["t"] + (dom["yrange"][1] - y) * ppy
    rows.append((label, x, y, pos, size, n["mx"], n["my"],
                 abs(n["mx"] - exp_mx), abs(n["my"] - exp_my),
                 n["mr"] * 2, n["lw"], n["lh"],
                 n["ly0"], n["ly1"], exp_my, n["mx"] - n["lw"] / 2,
                 n["mx"] + n["lw"] / 2, n["lx0"], n["lx1"], exp_mx))
print(f"{'label':7} {'pos':14} {'msz':5} {'mrx2':5} {'dx':5} {'dy':5} "
      f"{'lw':5} {'lh':5} {'dxlab':6} {'dylab':7} {'vert_off':8}")
maxdx = maxdy = 0.0
for r in rows:
    lab, x, y, pos, size, mx, my, dx, dy, mr2, lw, lh, ly0, ly1, exp_my, lx, rx_, lx0, lx1, exp_x = r
    lcx = n_lcx = (lx0 + lx1) / 2
    dxlab = abs(lcx - exp_x)
    if pos == "bottom center":
        vert = ((ly0 + ly1) / 2) - my        # positif = teks di bawah marker
    else:
        vert = ((ly0 + ly1) / 2) - my        # negatif = teks di atas
    maxdx = max(maxdx, dxlab)
    maxdy = max(maxdy, dy)
    print(f"{lab:7} {pos:14} {size:5.1f} {mr2:5.1f} {dx:5.2f} {dy:5.2f} "
          f"{lw:5.1f} {lh:5.1f} {dxlab:6.2f} {vert:7.2f} {vert:8.2f}")
print(f"max label-center dx = {maxdx:.3f}px   max marker dy = {maxdy:.3f}px")

# lebar label per karakter, per font
wid = {}
for r in rows:
    lab, lw = r[0], r[10]
    wid.setdefault(lab, lw)
chars = {k: round(v / len(k), 3) for k, v in wid.items()}
print("px per char by label:", chars)
print("px per char mean:",
      round(sum(v / len(k) for k, v in wid.items()) / len(wid), 3))
print("label height (text bbox):", sorted({round(r[11], 1) for r in rows}))
print("marker px from size:", sorted({(round(r[4], 1), r[9]) for r in rows}))
