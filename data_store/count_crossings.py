"""
count_crossings.py — PERSILANGAN dari geometri SPLINE yang benar-benar
dirender browser.

Metode: untuk tiap <path class="js-line">, panggil `getPointAtLength(t)`
sepanjang path itu. Ini mengembalikan titik yang BENAR-BENAR ada di layar,
termasuk efek `shape="spline"` yang hanya diterapkan Plotly saat render —
daftar titik Python tidak mewakili apa yang dilihat user.

Lalu setiap pasangan sisi yang tidak berbagi endpoint diuji di sepanjang
polilini hasil sampling (bukan diagonal bounding box, yang selalu "berpotongan"
dan tidak berarti apa-apa).
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dashboard.layouts.hud_figures import create_neural_net_fig  # noqa: E402

CSS = (ROOT / "dashboard" / "assets" / "style.css").read_text(encoding="utf-8")
OUT = ROOT / "data_store" / "_cross_probe.html"
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
<style>__CSS__</style><script src="__PLOTLY__"></script></head><body>
<div class="hud-container"><div class="hud-grid">
  <div class="hud-panel span-3 zone3-cell"><div class="hud-panel-header"><span>NN</span></div>
   <div class="hud-panel-body" style="padding:2px"><div id="hud-neural-graph" style="height:var(--h-neural);width:100%"></div></div></div>
  <div class="hud-panel span-3 zone3-cell"><div class="hud-panel-header"><span>B</span></div><div class="hud-panel-body" style="padding:2px"></div></div>
  <div class="hud-panel span-3 zone3-cell"><div class="hud-panel-header"><span>C</span></div><div class="hud-panel-body" style="padding:2px"></div></div>
  <div class="hud-panel span-3 zone3-cell"><div class="hud-panel-header"><span>D</span></div><div class="hud-panel-body" style="padding:2px"></div></div>
</div></div>
<script>Plotly.newPlot('hud-neural-graph', __FIG__.data, __FIG__.layout,
  {displayModeBar:false, responsive:true});</script></body></html>"""

import plotly as _pl  # noqa: E402
_PLOTLY = os.path.join(os.path.dirname(_pl.__file__), "package_data", "plotly.min.js")
OUT.write_text(
    HTML.replace("__PLOTLY__", "file:///" + _PLOTLY.replace("\\", "/"))
         .replace("__CSS__", CSS)
         .replace("__FIG__", json.dumps(json.loads(fig.to_json()))),
    encoding="utf-8")

PROBE = r"""
() => {
  const gd = document.getElementById('hud-neural-graph');
  const plot = gd.classList.contains('js-plotly-plot') ? gd
             : gd.querySelector('.js-plotly-plot');
  const groups = [...plot.querySelectorAll('g.scatterlayer > g.trace')];
  const lineTraces = (gd.data || []).filter(t => t.mode === 'lines');
  const N = 64;
  const edges = [];
  for (let i = 0; i < lineTraces.length; i++) {
    const g = groups[i];
    if (!g) continue;
    const p = g.querySelector('path.js-line');
    if (!p) continue;
    const L = p.getTotalLength();
    const pts = [];
    const svg = p.ownerSVGElement;
    const ctm = p.getScreenCTM();
    for (let k = 0; k <= N; k++) {
      const pt = p.getPointAtLength(L * k / N);
      // transform ke koordinat viewport absolut
      const m = svg.createSVGPoint();
      m.x = pt.x; m.y = pt.y;
      const abs = m.matrixTransform(ctm);
      pts.push([abs.x, abs.y]);
    }
    edges.push({
      src: lineTraces[i].customdata[0][0],
      dst: lineTraces[i].customdata[0][1],
      len: L, pts: pts,
      stroke: p.getAttribute('stroke'),
      width: p.getAttribute('stroke-width'),
    });
  }
  const pts = [...plot.querySelectorAll('path.point')];
  const tr = (gd.data||[]).find(t => t.mode === 'markers+text');
  const nodes = pts.map((p, k) => {
    const r = p.getBoundingClientRect();
    return {x: r.x + r.width/2, y: r.y + r.height/2, r: r.width/2,
            label: tr && tr.text ? tr.text[k] : String(k)};
  });
  const txts = [...plot.querySelectorAll('.scatterlayer text')];
  const labels = txts.map(t => {
    const r = t.getBoundingClientRect();
    return {text:(t.textContent||'').trim(), x:r.x, y:r.y, w:r.width, h:r.height};
  });
  return {edges: edges, nodes: nodes, labels: labels};
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

E = R["edges"]
N = R["nodes"]
L = R["labels"]


def cross(o, a, c):
    return (a[0]-o[0])*(c[1]-o[1]) - (a[1]-o[1])*(c[0]-o[0])


def seg_int(p1, p2, p3, p4):
    d1, d2 = cross(p3, p4, p1), cross(p3, p4, p2)
    d3, d4 = cross(p1, p2, p3), cross(p1, p2, p4)
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0))


def poly_hit(A, B, shrink=0.10):
    """Berpotongan di tengah polilini, abaikan 10% ter端的 di tiap ujung
    supaya segmen yang menyentuh simpul bersama tidak dihitung."""
    a0 = int(len(A) * shrink); a1 = len(A) - a0
    b0 = int(len(B) * shrink); b1 = len(B) - b0
    for i in range(a0, a1):
        for j in range(b0, b1):
            if seg_int(A[i], A[i+1], B[j], B[j+1]):
                return True
    return False


print("=" * 76)
print("PERSILANGAN — geometri spline hasil RENDER (getPointAtLength)")
print("=" * 76)
print("  sampling per sisi : 64 titik di sepanjang path")
print("  total sisi        : %d" % len(E))
print("  total simpul      : %d   label: %d" % (len(N), len(L)))

pairs = 0
hits = []
for i in range(len(E)):
    for j in range(i + 1, len(E)):
        a, c = E[i], E[j]
        if {a["src"], a["dst"]} & {c["src"], c["dst"]}:
            continue
        pairs += 1
        if poly_hit(a["pts"], c["pts"]):
            hits.append((a, c))

print("-" * 76)
print("  pasangan endpoint-disjoint  : %d" % pairs)
print("  PERSILANGAN                : %d" % len(hits))
for a, c in hits:
    print("     %-28s x %-28s" % (a["src"] + ">" + a["dst"],
                                  c["src"] + ">" + c["dst"]))

print("-" * 76)
tok = {"BTC","ETH","SOL","XRP","BNB","DOGE","NEAR","HYPE","ZEC","ONDO","ENA","XPL"}
tl = [e["len"] for e in E if e["dst"] in tok or e["src"] in tok]
sl = [e["len"] for e in E if not (e["dst"] in tok or e["src"] in tok)]
print("  panjang token  n=%2d mean %6.1f max %6.1f  >150px: %d"
      % (len(tl), sum(tl)/len(tl), max(tl), sum(1 for x in tl if x > 150)))
print("  panjang system n=%2d mean %6.1f max %6.1f  >150px: %d"
      % (len(sl), sum(sl)/len(sl), max(sl), sum(1 for x in sl if x > 150)))
print("  TOTAL sisi > 150px : %d dari %d" % (sum(1 for e in E if e["len"]>150), len(E)))

# --- sisi melewati simpul / label lain -----------------------------
def seg_dist(pt, a, b):
    dx, dy = b[0]-a[0], b[1]-a[1]
    L2 = dx*dx+dy*dy
    if L2 < 1e-12:
        return ((pt[0]-a[0])**2 + (pt[1]-a[1])**2) ** 0.5
    t = max(0.0, min(1.0, ((pt[0]-a[0])*dx + (pt[1]-a[1])*dy)/L2))
    return ((pt[0]-(a[0]+t*dx))**2 + (pt[1]-(a[1]+t*dy))**2) ** 0.5

from dashboard.layouts.hud_figures import NEURAL_SYSTEM_NODES as _NSN
# Peta kunci internal -> label yang TAMPIL. Sisi memakai kunci internal
# ("CORE_ENGINE"), simpul/label memakai teks ("SIGNAL"). Tanpa konversi ini
# setiap sisi dianggap "melewati" simpulnya sendiri.
label_of = {k: v[2] for k, v in _NSN.items()}
rev_label = {v[2]: k for k, v in _NSN.items()}

tn, tl_hits = [], []
for e in E:
    ep_lbl = {label_of.get(e["src"], e["src"]), label_of.get(e["dst"], e["dst"])}
    for nd in N:
        nm = nd["label"]
        if nm in ep_lbl:
            continue   # simpul ujungnya sendiri, dilewati secara wajar

        for i in range(len(e["pts"]) - 1):
            if seg_dist((nd["x"], nd["y"]), e["pts"][i], e["pts"][i+1]) <= nd["r"]:
                tn.append((e, nm)); break
    for lb in L:
        if lb["text"] in ep_lbl:
            continue   # label simpul ujungnya sendiri

        bx0, by0 = lb["x"], lb["y"]
        bx1, by1 = lb["x"]+lb["w"], lb["y"]+lb["h"]
        for i in range(len(e["pts"]) - 1):
            p1, p2 = e["pts"][i], e["pts"][i+1]
            ins = (bx0<=p1[0]<=bx1 and by0<=p1[1]<=by1) or (bx0<=p2[0]<=bx1 and by0<=p2[1]<=by1)
            if ins:
                tl_hits.append((e, lb["text"])); break

print("-" * 76)
print("  SISI MELEWATI SIMPUL LAIN : %d" % len(tn))
for e, nm in tn:
    print("     %-28s melewati %s" % (e["src"]+">"+e["dst"], nm))
print("  SISI MELEWATI KOTAK LABEL : %d" % len(tl_hits))
for e, nm in tl_hits:
    print("     %-28s melewati label %s" % (e["src"]+">"+e["dst"], nm))

# --- gap ------------------------------------------------------------
def rect_gap(a, b):
    return max(a["x"]-(b["x"]+b["w"]), b["x"]-(a["x"]+a["w"]),
               a["y"]-(b["y"]+b["h"]), b["y"]-(a["y"]+a["h"]))
gl = sorted((rect_gap(L[i], L[j]), L[i]["text"], L[j]["text"])
            for i in range(len(L)) for j in range(i+1, len(L)))
gm = sorted((max(abs(N[i]["x"]-N[j]["x"])-(N[i]["r"]+N[j]["r"]),
                 abs(N[i]["y"]-N[j]["y"])-(N[i]["r"]+N[j]["r"])),
             N[i]["label"], N[j]["label"])
            for i in range(len(N)) for j in range(i+1, len(N)))
print("-" * 76)
print("  gap label terburuk   : %.1f px  (%s <-> %s)" % gl[0])
print("  gap marker terburuk  : %.1f px  (%s <-> %s)" % gm[0])
print("  label < 8px          : %d dari %d" % (sum(1 for g,_,_ in gl if g<8), len(gl)))
print("  marker < 6px         : %d dari %d" % (sum(1 for g,_,_ in gm if g<6), len(gm)))
print("=" * 76)

json.dump({"edges": len(E), "crossings": len(hits),
           "crossing_pairs": [[a["src"]+">"+a["dst"], c["src"]+">"+c["dst"]]
                              for a, c in hits],
           "over150": sum(1 for e in E if e["len"]>150),
           "through_node": len(tn), "through_label": len(tl_hits),
           "worst_label_gap": gl[0][0], "worst_marker_gap": gm[0][0]},
          open(ROOT/"data_store"/"crossings_report.json", "w"), indent=2)
