import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from playwright.sync_api import sync_playwright
import harness as H
from dashboard.layouts.hud_figures import create_neural_net_fig
from dashboard.layouts.hud import create_neural_net_panel
import plotly.io as pio

panel = create_neural_net_panel()


def find_graph(c):
    if getattr(c, "id", None) == "hud-neural-graph":
        return c
    kids = getattr(c, "children", None) or []
    if not isinstance(kids, (list, tuple)):
        kids = [kids]
    for k in kids:
        if k is None:
            continue
        r = find_graph(k)
        if r is not None:
            return r
    return None


g = find_graph(panel)
fig = create_neural_net_fig({}, {}, {})
path = H.render_html(fig, g.config, os.path.join(HERE, "dbg.html"))

JS = r"""
() => {
  const gd = document.getElementById('hud-neural-graph');
  const out = [];
  const traces = gd.querySelectorAll('g.scatterlayer g.trace');
  for (const t of traces) {
    const p = t.querySelector('path.js-line');
    if (!p) continue;
    const len = p.getTotalLength();
    const d = p.getAttribute('d');
    out.push({
      hasData: !!t.__data__,
      dataKeys: t.__data__ ? Object.keys(t.__data__) : null,
      customdata: t.__data__ ? JSON.stringify(t.__data__.customdata && t.__data__.customdata[0]) : null,
      name: t.__data__ ? t.__data__.name : null,
      pathLen: len,
      dHead: d.slice(0, 160),
      pts: [p.getPointAtLength(0), p.getPointAtLength(len)]
    });
  }
  return out;
}
"""

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1920, "height": 900})
    pg.goto("file:///" + path.replace("\\", "/"))
    pg.wait_for_timeout(2200)
    for r in pg.evaluate(JS):
        print(r["name"], "| data:", r["hasData"], "| cd:", r["customdata"])
        print("   pathLen", round(r["pathLen"], 1), "svgpts", r["pts"])
        print("   d:", r["dHead"])
    b.close()
