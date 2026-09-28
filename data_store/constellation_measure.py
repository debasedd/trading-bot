"""
constellation_measure.py — UKUR geometri konstelasi dari DOM Plotly nyata.

Semua angka di sini diukur, bukan dihitung dari asumsi. Yang menentukan
apakah dua label saling menimpa atau sebuah garis menembus marker adalah
kotak teks dan path SVG hasil render browser — bukan aritmetika unit.

Yang diukur:
  - ukuran panel & plot area sebenarnya (px_per_unitx/y)
  - gap label-ke-label  (butuh >= 8px)
  - gap marker-ke-marker (butuh >= 6px, marker max 30px)
  - jumlah edge, panjang edge, edge > 150px
  - PERSILANGAN antar edge yang TIDAK berbagi endpoint
  - edge yang menembus marker simpul yang bukan endpoint-nya
  - edge yang menembus kotak label

Jalankan:
    python data_store/constellation_measure.py            # ukur kode saat ini
    python data_store/constellation_measure.py --widths 1920 1600 1280
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MAX_MARKER_PX = 30.0
LABEL_GAP_MIN = 8.0
MARKER_GAP_MIN = 6.0

# CSS yang benar-benar dipakai dashboard (dashboard/assets/style.css).
# Disalin persis supaya panel hasil render di sini = panel di aplikasi.
PANEL_CSS = """
html,body{margin:0;padding:0;background:#e8e2d0;}
.wrap{padding:6px 10px;}
.hud-grid{display:grid;grid-template-columns:repeat(12,minmax(0,1fr));gap:6px;width:100%;align-items:start;}
.span-3{grid-column:span 3;}
.hud-panel{background:#efe9d8;border:2px solid #b5ab8d;display:flex;flex-direction:column;}
.hud-panel-header{min-height:22px;}
.hud-panel-body{padding:2px;background:#efe9d8;flex:1;display:flex;}
.zone3-cell{height:260px;}
"""

# PROBE: baca kotak marker, kotak label, dan tiap path garis dalam koordinat
# LAYAR (client). Path di-map lewat getScreenCTM() karena atribut 'd' berada
# di user space pra-transform.
PROBE = r"""
() => {
  const gd = document.getElementById('hud-neural-graph');
  if (!gd) return {error: 'no graph div'};
  const plot = gd.classList.contains('js-plotly-plot') ? gd
                                                  : gd.querySelector('.js-plotly-plot');
  if (!plot) return {error: 'plot not found'};
  const gr = gd.getBoundingClientRect();
  const pr = plot.getBoundingClientRect();
  const mt = plot._fullLayout ? plot._fullLayout.margin : {l:0,r:0,t:0,b:0};
  const scatter = plot.querySelector('.scatterlayer');
  if (!scatter) return {error: 'no scatterlayer'};

  // Node: trace terakhir (markers+text). Plotly menulis path.point dan
  // text.point sebagai SIBLING di dalam g.points, zip berdasarkan urutan.
  const traces = [...scatter.querySelectorAll('g.trace')];
  const nodeTrace = traces.find(t => t.querySelectorAll('text').length &&
                                       t.querySelectorAll('path.point').length);
  if (!nodeTrace) return {error: 'no marker trace'};
  const pts  = [...nodeTrace.querySelectorAll('path.point')];
  const txts = [...nodeTrace.querySelectorAll('text')];
  const n = Math.min(pts.length, txts.length);
  const nodes = [];
  for (let i = 0; i < n; i++) {
    const pb = pts[i].getBoundingClientRect();
    const tb = txts[i].getBoundingClientRect();
    nodes.push({
      label: (txts[i].textContent || '').trim(),
      mx: pb.x + pb.width / 2, my: pb.y + pb.height / 2,
      mr: Math.max(pb.width, pb.height) / 2,
      lx0: tb.x, ly0: tb.y, lx1: tb.x + tb.width, ly1: tb.y + tb.height,
      lw: tb.width, lh: tb.height,
    });
  }

  // Edge: satu <g class="trace"> per sisi, masing-masing satu path.js-line.
  //
  // PENTING: customdata TIDAK ada di elemen DOM. Plotly menyimpan datanya di
  // plot.data[], terpisah dari SVG. Versi pertama probe ini membaca
  // t.__cd__ dan dapat null untuk SEMUA sisi, sehingga setiap edge dilaporkan
  // sebagai "None->None" dan setiap simpul terbaca sebagai bukan-endpoint —
  // itu menghasilkan 60 pelanggaran palsu. Pasangkan path.js-line (urutan DOM)
  // trace lines (urutan plot.data); keduanya 1:1 karena satu trace per sisi.
  const lineTraces = plot.data.filter(d => d && d.mode === 'lines');
  const linePaths = [...scatter.querySelectorAll('path.js-line')];
  const edges = [];
  for (let i = 0; i < linePaths.length; i++) {
    const path = linePaths[i];
    const d = lineTraces[i] || {};
    const cd = d.customdata || null;
    let src = null, dst = null, flow = null;
    if (cd && cd.length && cd[0]) { src = cd[0][0]; dst = cd[0][1]; flow = cd[0][2]; }
    const ctm = path.getScreenCTM();
    const len = path.getTotalLength();
    const NS = 64;
    const pts2 = [];
    for (let i = 0; i <= NS; i++) {
      const p = path.getPointAtLength(len * i / NS);
      const q = new DOMPoint(p.x, p.y).matrixTransform(ctm);
      pts2.push([q.x, q.y]);
    }
    const r = path.getBoundingClientRect();
    edges.push({src: src, dst: dst, flow: flow,
                stroke: (path.getAttribute('stroke') || ''),
                width: r.width, height: r.height,
                pts: pts2});
  }
  return {
    panel: {w: gr.width, h: gr.height},
    plot:  {w: pr.width, h: pr.height},
    margin: {l: mt.l, r: mt.r, t: mt.t, b: mt.b},
    xrange: plot._fullLayout.xaxis.range.slice(),
    yrange: plot._fullLayout.yaxis.range.slice(),
    nodes: nodes, edges: edges,
  };
}
"""

HTML = """<!doctype html><html><head><meta charset="utf-8">
<style>%(css)s</style></head><body>
<div class="wrap"><div class="hud-grid">
  <div class="hud-panel span-3 zone3-cell">
    <div class="hud-panel-header"><span>CONSTELLATION ENGINE</span></div>
    <div class="hud-panel-body" style="padding:2px">
      <div id="hud-neural-graph" style="height:230px;width:100%%"></div>
    </div>
  </div>
</div></div>
<script>%(plotly)s</script>
<script>window.__FIG__ = %(fig)s;</script>
<script>
  const gd = document.getElementById('hud-neural-graph');
  Plotly.newPlot(gd, window.__FIG__.data, window.__FIG__.layout,
                 {displayModeBar: false, responsive: true});
</script>
</body></html>"""


# ---------------------------------------------------------------- math ----
def _seg_pt_dist(a, b, p):
    ax, ay = a; bx, by = b; px, py = p
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    if L2 <= 1e-12:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _seg_seg_dist(a1, a2, b1, b2):
    """Jarak minimum antara dua segmen (0 kalau berpotongan)."""
    def cross(o, p, q):
        return (p[0] - o[0]) * (q[1] - o[1]) - (p[1] - o[1]) * (q[0] - o[0])
    d1, d2 = cross(b1, b2, a1), cross(b1, b2, a2)
    d3, d4 = cross(a1, a2, b1), cross(a1, a2, b2)
    if ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)):
        return 0.0
    return min(_seg_pt_dist(a1, a2, b1), _seg_pt_dist(a1, a2, b2),
               _seg_pt_dist(b1, b2, a1), _seg_pt_dist(b1, b2, a2))


def _seg_rect_dist(a, b, x0, y0, x1, y1):
    """Jarak minimum segmen ke kotak; 0 kalau masuk/touches."""
    if (x0 <= a[0] <= x1 and y0 <= a[1] <= y1) or \
       (x0 <= b[0] <= x1 and y0 <= b[1] <= y1):
        return 0.0
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    best = min(_seg_pt_dist(a, b, c) for c in corners)
    for i in range(4):
        c1, c2 = corners[i], corners[(i + 1) % 4]
        best = min(best, _seg_seg_dist(a, b, c1, c2))
        if best <= 0.0:
            return 0.0
    return best


def _rect_rect_gap(a, b):
    dx = max(b["lx0"] - a["lx1"], a["lx0"] - b["lx1"], 0.0)
    dy = max(b["ly0"] - a["ly1"], a["ly0"] - b["ly1"], 0.0)
    return math.hypot(dx, dy)


def _marker_gap(a, b):
    return math.hypot(a["mx"] - b["mx"], a["my"] - b["my"]) - a["mr"] - b["mr"]


def _poly_len(pts):
    return sum(math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
               for i in range(len(pts) - 1))


def analyze(dom, label2key=None):
    """dom = hasil PROBE. label2key = {label: key} untuk tahu endpoint."""
    label2key = label2key or {}
    # Edge memakai KUNCI internal (CORE_ENGINE), simpul menampilkan LABEL
    # (SIGNAL). Tanpa peta ini setiap edge terlihat menembus label sendiri.
    # Token: label == key, jadi fallback ke label itu sendiri.
    def key_of(label):
        return label2key.get(label, label)

    nodes = dom["nodes"]
    edges = dom["edges"]
    rep = {}

    plot_w = dom["plot"]["w"] - dom["margin"]["l"] - dom["margin"]["r"]
    plot_h = dom["plot"]["h"] - dom["margin"]["t"] - dom["margin"]["b"]
    rx = dom["xrange"][1] - dom["xrange"][0]
    ry = dom["yrange"][1] - dom["yrange"][0]
    rep["panel"] = [round(dom["panel"]["w"], 1), round(dom["panel"]["h"], 1)]
    rep["plot_area"] = [round(plot_w, 1), round(plot_h, 1)]
    rep["px_per_unit"] = [round(plot_w / rx, 3), round(plot_h / ry, 3)]
    rep["node_count"] = len(nodes)
    rep["edge_count"] = len(edges)

    # --- gap label & marker -------------------------------------------
    lab = [(a["label"], b["label"], _rect_rect_gap(a, b))
           for a, b in itertools.combinations(nodes, 2)]
    mk = [(a["label"], b["label"], _marker_gap(a, b))
          for a, b in itertools.combinations(nodes, 2)]
    lab.sort(key=lambda t: t[2])
    mk.sort(key=lambda t: t[2])
    rep["worst_label_gap"] = round(lab[0][2], 1) if lab else None
    rep["worst_label_pair"] = [lab[0][0], lab[0][1]] if lab else None
    rep["label_violations"] = sum(1 for t in lab if t[2] < LABEL_GAP_MIN)
    rep["worst_marker_gap"] = round(mk[0][2], 1) if mk else None
    rep["worst_marker_pair"] = [mk[0][0], mk[0][1]] if mk else None
    rep["marker_violations"] = sum(1 for t in mk if t[2] < MARKER_GAP_MIN)
    rep["max_marker_px"] = round(max((n["mr"] for n in nodes), default=0) * 2, 1)

    # --- panjang edge ---------------------------------------------------
    lens = []
    for e in edges:
        e["len"] = _poly_len(e["pts"])
        e["keys"] = {e["src"], e["dst"]}
        e["ends"] = {e["src"], e["dst"]} - {None}
        e["lbl"] = f"{e['src']}->{e['dst']}"
        lens.append(e["len"])
    rep["edge_len_mean"] = round(statistics.fmean(lens), 1) if lens else None
    rep["edge_len_max"] = round(max(lens), 1) if lens else None
    rep["edges_over_150"] = sum(1 for L in lens if L > 150.0)

    # --- persilangan: hanya pasangan edge TIDAK berbagi endpoint -------
    cross = []
    for a, b in itertools.combinations(edges, 2):
        if a["ends"] & b["ends"]:
            continue
        if a["keys"] & b["keys"]:
            continue
        for i in range(len(a["pts"]) - 1):
            for j in range(len(b["pts"]) - 1):
                if _seg_seg_dist(a["pts"][i], a["pts"][i + 1],
                                 b["pts"][j], b["pts"][j + 1]) <= 0.0:
                    cross.append([a["lbl"], b["lbl"]])
                    break
            else:
                continue
            break
    rep["crossings"] = len(cross)
    rep["crossing_pairs"] = cross[:20]

    # --- edge menembus marker bukan-endpoint ---------------------------
    thru_n = []
    for e in edges:
        for n in nodes:
            key = key_of(n["label"])
            if key in e["ends"]:
                continue
            d = min(_seg_pt_dist(e["pts"][i], e["pts"][i + 1], (n["mx"], n["my"]))
                    for i in range(len(e["pts"]) - 1))
            d -= n["mr"]
            if d <= 0.0:
                thru_n.append([e["lbl"], n["label"], round(d, 1)])
    rep["edge_through_node"] = len(thru_n)
    rep["edge_through_node_detail"] = thru_n[:20]

    # --- edge menembus kotak label ------------------------------------
    thru_l = []
    for e in edges:
        for n in nodes:
            if key_of(n["label"]) in e["ends"]:
                continue
            d = min(_seg_rect_dist(e["pts"][i], e["pts"][i + 1],
                                   n["lx0"], n["ly0"], n["lx1"], n["ly1"])
                    for i in range(len(e["pts"]) - 1))
            if d <= 0.0:
                thru_l.append([e["lbl"], n["label"]])
    rep["edge_through_label"] = len(thru_l)
    rep["edge_through_label_detail"] = thru_l[:20]

    rep["BAD"] = (rep["label_violations"] + rep["marker_violations"]
                  + rep["edge_through_node"] + rep["edge_through_label"])
    return rep


# ------------------------------------------------------------- browser ----
def _plotly_js() -> str:
    import plotly.offline as po
    return po.get_plotlyjs()


def measure(fig, viewport_w=1920, label2key=None, shot=None):
    from playwright.sync_api import sync_playwright
    import json as _json
    html = HTML % {
        "css": PANEL_CSS, "plotly": _plotly_js(),
        "fig": _json.dumps(fig.to_plotly_json()),
    }
    tmp = ROOT / "data_store" / "_measure.html"
    tmp.write_text(html, encoding="utf-8")
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": viewport_w, "height": 1100})
        pg.goto(tmp.as_uri())
        pg.wait_for_function("() => {const g=document.getElementById('hud-neural-graph');"
                             "return g && g.data && g._fullLayout;}", timeout=20000)
        pg.wait_for_timeout(350)
        dom = pg.evaluate(PROBE)
        if shot:
            pg.screenshot(path=shot, full_page=True)
        b.close()
    if "error" in dom:
        raise RuntimeError(dom["error"])
    return analyze(dom, label2key)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--widths", type=int, nargs="*", default=[1920])
    ap.add_argument("--tokens", default=None,
                    help="comma list; default = the 12-slot production set")
    ap.add_argument("--conf", type=float, default=1.0)
    ap.add_argument("--shot", default=None)
    args = ap.parse_args()

    from dashboard.layouts.hud_figures import (
        NEURAL_SYSTEM_NODES, TOKEN_SLOTS, create_neural_net_fig,
        _TOKEN_SLOT_REGISTRY,
    )
    toks = (args.tokens.split(",") if args.tokens else
            list(_TOKEN_SLOT_REGISTRY)[:len(TOKEN_SLOTS)])
    sig = {t: {"direction": "LONG", "confidence": args.conf,
               "price_change": 0.004} for t in toks}
    fig = create_neural_net_fig(
        signal_map=sig,
        activity_map={k: 1.0 for k in ("analysis_agent", "decision_agent",
                                       "execution_agent", "news_agent")},
        flow_map={k: 1.0 for k in ("l2Book", "allMids", "candle", "trades")},
    )
    label2key = {v[2]: k for k, v in NEURAL_SYSTEM_NODES.items()}
    out = {}
    for w in args.widths:
        out[str(w)] = measure(fig, w, label2key,
                              shot=(args.shot.replace("W", str(w))
                                    if args.shot else None))
    print(json.dumps(out, indent=1))
    return out


if __name__ == "__main__":
    main()
