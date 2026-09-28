"""
measure_panel.py — Ukur UKURAN NYATA panel konstelasi di browser.

Taskhil mengasumsikan plot area 440x230 dan px_per_unit 20.0. Asumsi itu
belum pernah diverifikasi. Skrip ini membangun halaman yang meniru struktur
CSS yang sebenarnya (12-kolom grid, .hud-container, .hud-panel, --h-neural)
lalu membacakan angka asli dari DOM.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dashboard.layouts.hud_figures import create_neural_net_fig  # noqa: E402

CSS = (ROOT / "dashboard" / "assets" / "style.css").read_text(encoding="utf-8")
OUT = ROOT / "data_store" / "_panel_probe.html"

TOKENS = ["BTC", "ETH", "SOL", "XRP", "BNB", "DOGE",
          "NEAR", "HYPE", "ZEC", "ONDO", "ENA", "XPL"]

fig = create_neural_net_fig(
    signal_map={s: {"direction": "LONG", "confidence": 0.7,
                    "price_change": 0.002} for s in TOKENS},
    activity_map={"analysis_agent": 1.0, "decision_agent": 0.9,
                  "execution_agent": 0.8, "news_agent": 0.7},
    flow_map={"allMids": 1.0, "l2Book": 0.8, "candle": 0.5, "trades": 0.6},
)

HTML = """<!doctype html><html><head><meta charset="utf-8">
<style>__CSS__</style>
<script src="__PLOTLY__"></script>
</head><body>
<div class="hud-container">
  <div class="hud-grid">
    <div class="hud-panel span-3 zone3-cell" id="p1"><div class="hud-panel-header"><span>SNIPE NEURAL NET</span></div>
      <div class="hud-panel-body" style="padding:2px"><div id="hud-neural-graph" style="height:var(--h-neural);width:100%"></div></div></div>
    <div class="hud-panel span-3 zone3-cell"><div class="hud-panel-header"><span>B</span></div><div class="hud-panel-body" style="padding:2px"></div></div>
    <div class="hud-panel span-3 zone3-cell"><div class="hud-panel-header"><span>C</span></div><div class="hud-panel-body" style="padding:2px"></div></div>
    <div class="hud-panel span-3 zone3-cell"><div class="hud-panel-header"><span>D</span></div><div class="hud-panel-body" style="padding:2px"></div></div>
  </div>
</div>
<script>
const fig = __FIG__;
Plotly.newPlot('hud-neural-graph', fig.data, fig.layout, {displayModeBar:false, responsive:true});
</script></body></html>"""

import plotly as _pl, os as _os
_PLOTLY = _os.path.join(_os.path.dirname(_pl.__file__), "package_data", "plotly.min.js")
OUT.write_text(HTML.replace("__PLOTLY__", "file:///" + _PLOTLY.replace("\\", "/"))
                  .replace("__CSS__", CSS).replace("__FIG__", json.dumps(
    {"data": json.loads(fig.to_json())["data"],
     "layout": json.loads(fig.to_json())["layout"]})), encoding="utf-8")

PROBE = r"""
() => {
  const gd = document.getElementById('hud-neural-graph');
  // Plotly >= 6 menaruh class .js-plotly-plot PADA div itu sendiri, bukan
  // pada anak. `gd.querySelector('.js-plotly-plot')` selalu null.
  const plot = gd.classList.contains('js-plotly-plot') ? gd
             : gd.querySelector('.js-plotly-plot');
  const panel = document.getElementById('p1');
  const body = panel.querySelector('.hud-panel-body');
  const head = panel.querySelector('.hud-panel-header');
  const xl = plot.querySelector('.xaxislayer-above, .xaxislayer-below, .xaxislayer');
  // Sub-pixel canvas utama = area gambar plotting sebenarnya
  const canvas = plot.querySelector('.main-svg');
  const bb = canvas ? canvas.getBoundingClientRect() : null;
  // kotak axes dari layer bg
  const bg = plot.querySelector('.bglayer rect.bg, .cartesianlayer .bg');
  const bgbb = bg ? bg.getBoundingClientRect() : null;
  return {
    container: getComputedStyle(document.querySelector('.hud-container')).padding,
    panelW: panel.getBoundingClientRect().width,
    panelH: panel.getBoundingClientRect().height,
    headerH: head.getBoundingClientRect().height,
    bodyH: body.getBoundingClientRect().height,
    graphW: gd.getBoundingClientRect().width,
    graphH: gd.getBoundingClientRect().height,
    plotW: plot.clientWidth, plotH: plot.clientHeight,
    svgW: bb ? bb.width : 0, svgH: bb ? bb.height : 0,
    bgW: bgbb ? bgbb.width : 0, bgH: bgbb ? bgbb.height : 0,
    xrange: [plot._fullLayout.xaxis.range[0], plot._fullLayout.xaxis.range[1]],
    yrange: [plot._fullLayout.yaxis.range[0], plot._fullLayout.yaxis.range[1]],
    margins: plot._fullLayout.margin,
  };
}
"""

from playwright.sync_api import sync_playwright  # noqa: E402

with sync_playwright() as pw:
    b = pw.chromium.launch()
    p = b.new_page(viewport={"width": 1920, "height": 1080},
                   device_scale_factor=1)
    p.goto(OUT.as_uri(), wait_until="load")
    p.wait_for_selector(".js-plotly-plot", timeout=30000)
    p.wait_for_timeout(1500)
    r = p.evaluate(PROBE)
    b.close()

ml, mr = r["margins"]["l"], r["margins"]["r"]
mt, mb = r["margins"]["t"], r["margins"]["b"]
plot_w = r["graphW"] - ml - mr
plot_h = r["graphH"] - mt - mb
dx = r["xrange"][1] - r["xrange"][0]
dy = r["yrange"][1] - r["yrange"][0]

print("=" * 70)
print("UKURAN NYATA (render browser, bukan asumsi)")
print("-" * 70)
for k in ("container", "panelW", "panelH", "headerH", "bodyH",
          "graphW", "graphH", "plotW", "plotH", "svgW", "svgH",
          "bgW", "bgH"):
    print("  %-10s = %s" % (k, r[k]))
print("  margins    = %s" % {k: r["margins"][k] for k in ("l", "r", "t", "b")})
print("  x range    = %s" % (r["xrange"],))
print("  y range    = %s" % (r["yrange"],))
print("-" * 70)
print("  plot area  = %.2f x %.2f px   (dihitung: graph - margin)" % (plot_w, plot_h))
print("  px/unit x  = %.4f" % (plot_w / dx))
print("  px/unit y  = %.4f" % (plot_h / dy))
print("  ASIMETRI   = %.2f%%  (x lebih %s dari y)" % (
    abs((plot_w / dx) / (plot_h / dy) - 1) * 100,
    "lebar" if plot_w / dx > plot_h / dy else "tinggi"))
print("=" * 70)
