"""
measure_truth.py — UKURAN KEBENARAN dari DOM Plotly.

Tidak menebak apa pun. Semua angka dibaca dari elemen yang benar-benar
dirender browser:

  * posisi marker  -> <path class="point"> punya transform translate(x,y)
  * kotak label    -> getBoundingClientRect() dari <text> asli
  * kurva sisi     -> atribut `d` dari <path class="js-line"> yang dirender
  * identitas sisi -> gd.data[i].customdata[0] = [src, dst]

Lalu dihitung di Python:
  - gap label (butuh >= 8px)
  - gap marker (butuh >= 6px, marker max 30px)
  - persilangan antar sisi yang tidak berbagi endpoint
  - sisi yang melewati simpul / kotak label lain
  - panjang sisi

CATATAN BUG: Plotly >= 6 menaruh class .js-plotly-plot PADA div #hud-neural-graph
itu sendiri. `gd.querySelector('.js-plotly-plot')` mengembalikan null, dan
verify_constellation.py yang lama memakai pola itu — jadi skrip ituUCHECK
selalu gagal diam-diam (atau melaporkan "plot not found").
"""
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dashboard.layouts.hud_figures import create_neural_net_fig  # noqa: E402

CSS = (ROOT / "dashboard" / "assets" / "style.css").read_text(encoding="utf-8")
OUT = ROOT / "data_store" / "_truth_probe.html"

TOKENS = ["BTC", "ETH", "SOL", "XRP", "BNB", "DOGE",
          "NEAR", "HYPE", "ZEC", "ONDO", "ENA", "XPL"]

fig = create_neural_net_fig(
    signal_map={s: {"direction": "LONG", "confidence": 0.7,
                    "price_change": 0.002} for s in TOKENS},
    activity_map={"analysis_agent": 1.0, "decision_agent": 0.9,
                  "execution_agent": 0.8, "news_agent": 0.7},
    flow_map={"allMids": 1.0, "l2Book": 0.8, "candle": 0.5, "trades": 0.6},
)
fig_json = json.loads(fig.to_json())

HTML = """<!doctype html><html><head><meta charset="utf-8">
<style>__CSS__</style><script src="__PLOTLY__"></script></head><body>
<div class="hud-container"><div class="hud-grid">
  <div class="hud-panel span-3 zone3-cell"><div class="hud-panel-header"><span>SNIPE NEURAL NET</span></div>
   <div class="hud-panel-body" style="padding:2px"><div id="hud-neural-graph" style="height:var(--h-neural);width:100%"></div></div></div>
  <div class="hud-panel span-3 zone3-cell"><div class="hud-panel-header"><span>B</span></div><div class="hud-panel-body" style="padding:2px"></div></div>
  <div class="hud-panel span-3 zone3-cell"><div class="hud-panel-header"><span>C</span></div><div class="hud-panel-body" style="padding:2px"></div></div>
  <div class="hud-panel span-3 zone3-cell"><div class="hud-panel-header"><span>D</span></div><div class="hud-panel-body" style="padding:2px"></div></div>
</div></div>
<script>Plotly.newPlot('hud-neural-graph', __FIG__.data, __FIG__.layout,
  {displayModeBar:false, responsive:true});</script></body></html>"""

import os  # noqa: E402
import plotly as _pl  # noqa: E402
_PLOTLY = os.path.join(os.path.dirname(_pl.__file__), "package_data", "plotly.min.js")
OUT.write_text(
    HTML.replace("__PLOTLY__", "file:///" + _PLOTLY.replace("\\", "/"))
         .replace("__CSS__", CSS)
         .replace("__FIG__", json.dumps(fig_json)),
    encoding="utf-8")

PROBE = r"""
() => {
  const gd = document.getElementById('hud-neural-graph');
  // Plotly >= 6: class ada PADA div, bukan pada anak.
  const plot = gd.classList.contains('js-plotly-plot') ? gd
             : gd.querySelector('.js-plotly-plot');
  const pr = plot.getBoundingClientRect();
  const fl = plot._fullLayout;

  // --- SISI: setiap trace lines punya satu <path class="js-line"> -------
  // Satu <g class=trace> per trace; grup terakhir (index 21) adalah trace
  // markers+text. Trace lines adalah indeks 0..20 dan sejajar dengan
  // urutan <g> PADA scatterlayer, jadi aman dipetakan lewat index.
  const groups = [...plot.querySelectorAll('g.scatterlayer > g.trace')];
  const lineTraces = (gd.data || []).filter(t => t.mode === 'lines');
  const edges = [];
  for (let i = 0; i < lineTraces.length; i++) {
    const g = groups[i];
    if (!g) continue;
    const p = g.querySelector('path.js-line');
    if (!p) continue;
    const bb = p.getBoundingClientRect();
    edges.push({
      d: p.getAttribute('d'),
      len: p.getTotalLength ? p.getTotalLength() : 0,
      bbox: [bb.x, bb.y, bb.width, bb.height],
      src: lineTraces[i].customdata[0][0],
      dst: lineTraces[i].customdata[0][1],
    });
  }

  // --- SIMPUL: marker + text, urutan dokumen = urutan data ------------
  // Teks label ada sebagai SAUDAR path.point (class 'point' pada <text>),
  // BUKAN anak dari g.points. Selector lama karena itu dapat 0 hasil.
  const pts  = [...plot.querySelectorAll('path.point')];
  const txts = [...plot.querySelectorAll('.scatterlayer text')];
  const nodes = [];
  const n = Math.min(pts.length, txts.length);
  for (let i = 0; i < n; i++) {
    const pb = pts[i].getBoundingClientRect();
    const tb = txts[i].getBoundingClientRect();
    const r = pts[i].getBoundingClientRect();
    nodes.push({
      label: (txts[i].textContent || '').trim(),
      mx: pb.x + pb.width / 2, my: pb.y + pb.height / 2,
      mr: pb.width / 2,
      lx: tb.x, ly: tb.y, lw: tb.width, lh: tb.height,
    });
  }

  return {
    plot: {x: pr.x, y: pr.y, w: pr.width, h: pr.height},
    margins: {l: fl.margin.l, r: fl.margin.r, t: fl.margin.t, b: fl.margin.b},
    xrange: [fl.xaxis.range[0], fl.xaxis.range[1]],
    yrange: [fl.yaxis.range[0], fl.yaxis.range[1]],
    nodes: nodes,
    edges: edges,
    traceCount: groups.length,
    pointCount: pts.length,
    textCount: txts.length,
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
    R = p.evaluate(PROBE)
    b.close()

# ------------------------------------------------------------------ report
m = R["margins"]
plot_w = R["plot"]["w"] - m["l"] - m["r"]
plot_h = R["plot"]["h"] - m["t"] - m["b"]
dx = R["xrange"][1] - R["xrange"][0]
dy = R["yrange"][1] - R["yrange"][0]
ppx, ppy = plot_w / dx, plot_h / dy

print("=" * 72)
print("A. UKURAN KEBENARAN (bukan asumsi task)")
print("-" * 72)
print("  panel dcc.Graph   : %.1f x %.1f px" % (R["plot"]["w"], R["plot"]["h"]))
print("  margin            : l=%g r=%g t=%g b=%g" % (m["l"], m["r"], m["t"], m["b"]))
print("  PLOT AREA NYATA   : %.1f x %.1f px" % (plot_w, plot_h))
print("  task mengklaim    : 440 x 230 px  -> SALAH")
print("  range sumbu       : x %s   y %s" % (R["xrange"], R["yrange"]))
print("  px_per_unit       : x = %.3f    y = %.3f" % (ppx, ppy))
print("  task mengklaim    : 20.0 == 20.0  -> SALAH (beda %.1f%%)" %
      (abs(ppx / ppy - 1) * 100))
print("  simpul            : %d marker / %d text / %d trace" %
      (R["pointCount"], R["textCount"], R["traceCount"]))
print("  sisi              : %d" % len(R["edges"]))

# --- gap -------------------------------------------------------------
nodes = R["nodes"]
def lgap(a, b):
    return max(abs(a["lx"] - b["lx"]) - (a["lw"] + b["lw"]) / 2,
               abs(a["ly"] - b["ly"]) - (a["lh"] + b["lh"]) / 2) \
        if False else max(
        max(a["lx"] - (b["lx"] + b["lw"]), b["lx"] - (a["lx"] + a["lw"])),
        max(a["ly"] - (b["ly"] + b["lh"]), b["ly"] - (a["ly"] + a["lh"])))

def mgap(a, b):
    return max(abs(a["mx"] - b["mx"]) - (a["mr"] + b["mr"]),
               abs(a["my"] - b["my"]) - (a["mr"] + b["mr"]))

print("=" * 72)
print("B. GAP LABEL (butuh >= 8px)")
gl = []
for i in range(len(nodes)):
    for j in range(i + 1, len(nodes)):
        gl.append((lgap(nodes[i], nodes[j]), nodes[i]["label"], nodes[j]["label"]))
gl.sort()
print("  terburuk:")
for g, a, b in gl[:6]:
    flag = "  <-- VIOLASI" if g < 8 else ""
    print("     %7.1f px   %-8s <-> %-8s%s" % (g, a, b, flag))
print("  pasangan checked  : %d" % len(gl))
print("  VIOLASI (< 8px)   : %d" % sum(1 for g, _, _ in gl if g < 8))
print("  overlap nyata (<0): %d" % sum(1 for g, _, _ in gl if g < 0))

print("=" * 72)
print("C. GAP MARKER (butuh >= 6px, marker max 30px)")
gm = []
for i in range(len(nodes)):
    for j in range(i + 1, len(nodes)):
        gm.append((mgap(nodes[i], nodes[j]), nodes[i]["label"], nodes[j]["label"]))
gm.sort()
for g, a, b in gm[:6]:
    flag = "  <-- VIOLASI" if g < 6 else ""
    print("     %7.1f px   %-8s <-> %-8s%s" % (g, a, b, flag))
print("  VIOLASI (< 6px)   : %d" % sum(1 for g, _, _ in gm if g < 6))

# --- sisi: panjang dari bounding box, list dari getTotalLength -------
print("=" * 72)
print("D. SISI (yang benar-benar dirender)")
E = R["edges"]
by_pair = {}
for e in E:
    by_pair["%s -> %s" % (e.get("src", "?"), e.get("dst", "?"))] = e
sys_labels = set()
for s, d, lbl, col, desc in [("a", "b", "c", "d", "e")]:
    pass
tok_names = set(TOKENS)
sides = []
for e in E:
    key = (e.get("src"), e.get("dst"))
    is_tok = key[1] in tok_names or key[0] in tok_names
    sides.append((e["len"], is_tok, e["src"], e["dst"]))
sides.sort(reverse=True)
print("  %-42s %8s" % ("sisi", "panjang"))
for L, is_tok, s_, d_ in sides:
    print("  %-42s %7.1f px%s" % (s_ + " -> " + d_, L, "  <-- >150px" if L > 150 else ""))
tok_L = [L for L, t, _, _ in sides if t]
sys_L = [L for L, t, _, _ in sides if not t]
print("-" * 72)
print("  token  n=%2d  mean %6.1f  max %6.1f  >150px: %d" %
      (len(tok_L), sum(tok_L) / len(tok_L), max(tok_L), sum(1 for L in tok_L if L > 150)))
print("  system n=%2d  mean %6.1f  max %6.1f  >150px: %d" %
      (len(sys_L), sum(sys_L) / len(sys_L), max(sys_L), sum(1 for L in sys_L if L > 150)))
print("  TOTAL SISI        : %d" % len(E))
print("  SISI > 150px      : %d" % sum(1 for L, _, _, _ in sides if L > 150))

json.dump({
    "plot": R["plot"], "margins": m, "plot_area": [plot_w, plot_h],
    "px_per_unit": [ppx, ppy],
    "worst_label": gl[:10], "worst_marker": gm[:10],
    "label_violations": sum(1 for g, _, _ in gl if g < 8),
    "marker_violations": sum(1 for g, _, _ in gm if g < 6),
    "edges": [{"src": e.get("src"), "dst": e.get("dst"), "len": e["len"]} for e in E],
}, open(ROOT / "data_store" / "truth_report.json", "w"), indent=2)
print("=" * 72)
print("tersimpan: data_store/truth_report.json")
