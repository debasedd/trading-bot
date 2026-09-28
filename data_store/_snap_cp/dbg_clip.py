import json
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

TOKENS = ["BTC", "ETH", "SOL", "XRP", "BNB", "DOGE", "NEAR", "ONDO", "HYPE", "ZEC", "XPL", "ENA"]
SIGNALS = {k: {"direction": "LONG", "confidence": 0.8, "price_change": 0.01} for k in TOKENS}
ACT = {k: 0.7 for k in ("execution_agent", "decision_agent", "analysis_agent", "news_agent")}
FLOW = {k: 0.7 for k in ("allMids", "l2Book", "candle", "trades")}


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


panel = create_neural_net_panel()
g = find_graph(panel)
fig = create_neural_net_fig(SIGNALS, ACT, FLOW)
path = H.render_html(fig, g.config, os.path.join(HERE, "dbgclip.html"))

JS = r"""
() => {
  const gd = document.getElementById('hud-neural-graph');
  const svg = gd.querySelector('svg.main-svg');
  const sr = svg.getBoundingClientRect();
  const L = gd._fullLayout;
  const m = L.margin;
  const scope = gd.querySelector('.scatterlayer');
  const pts = [...scope.querySelectorAll('path.point')];
  const txts = [...scope.querySelectorAll('text')];
  const rows = [];
  const n = Math.min(pts.length, txts.length);
  for (let i = 0; i < n; i++) {
    const pb = pts[i].getBoundingClientRect();
    const tb = txts[i].getBoundingClientRect();
    const at = txts[i].getAttribute('transform') || '';
    rows.push({label: (txts[i].textContent||'').trim(),
      attr: at, xform: pts[i].getAttribute('transform'),
      mcy: +(pb.y + pb.height/2).toFixed(2), md: +pb.width.toFixed(2),
      ly0: +tb.y.toFixed(2), ly1: +(tb.y+tb.height).toFixed(2),
      lcx: +(tb.x+tb.width/2).toFixed(2)});
  }
  return {svg:[sr.x,sr.y,sr.width,sr.height], m:m, yr:L.yaxis.range, xr:L.xaxis.range,
          plot:[sr.x+m.l, sr.y+m.t, sr.x+sr.width-m.r, sr.y+sr.height-m.b], rows: rows};
}
"""

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1280, "height": 900})
    pg.goto("file:///" + path.replace("\\", "/"))
    pg.wait_for_timeout(2000)
    d = pg.evaluate(JS)
    b.close()

print("svg ", d["svg"])
print("marg", d["m"])
print("plot", [round(v, 2) for v in d["plot"]], " h=", round(d["plot"][3]-d["plot"][1], 2))
print("xrange", d["xr"], "yrange", d["yr"])
print()
print("%-8s %-26s %-9s %-7s %-9s %-9s" % ("label", "marker transform", "mcy", "md", "ly0", "ly1"))
for r in d["rows"]:
    print("%-8s %-26s %-9.2f %-7.2f %-9.2f %-9.2f"
          % (r["label"], r["xform"], r["mcy"], r["md"], r["ly0"], r["ly1"]))
