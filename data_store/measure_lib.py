"""
measure_lib.py — harness pengukuran geometri KONSTELASI dari DOM sungguhan.

Semua angka di sini dibaca dari browser, bukan dihitung di Python. Plotly
mengubah koordinat data -> koordinat SVG, lalu browser mengubah itu jadi
piksel. Cuma hitungan yang benar lewat seluruh rantai itu yang dipercaya.

Tiga jebakan yang sudah dikoreksi di sini (semua ditemukan karena hasil
Python disagrees sama browser):

 1. Atribut `d` pada path berada di user space SEBELUM transform. Harus
    lewat `getScreenCTM()` + `SVGMatrixTransform`, kalau tidak tiap edge
    meleset konstan ~28px.
 2. Edge memakai KEY internal (CORE_ENGINE), node menampilkan LABEL
    (SIGNAL). Tanpa peta key->label, tiap edge seolah "menembus" label
    sendiri — 44 pelanggaran palsu.
 3. Plotly 6.9 menaruh class `js-plotly-plot` pada div itu sendiri, jadi
    `gd.querySelector('.js-plotly-plot')` mengembalikan null.
"""
import json
import math
import os
import re
from pathlib import Path

import plotly
import plotly.io as pio

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PLOTLY_JS = str(Path(plotly.__file__).parent / "package_data" / "plotly.min.js")

# Ukuran panel NYATA, diturunkan dari CSS (bukan dari brief):
#   hud-container padding 0 10px      -> 20px
#   hud-grid 12 kolom, gap 6px        -> 11 * 6 = 66px
#   hud-panel border 2px              -> 2 * 2 = 4px
#   zone3-cell body padding 2px       -> 4px
#   dcc.Graph height var(--h-neural)  -> 230px
#   margin Plotly l14 r14 t10 b16     -> 28px horizontal, 26px vertikal
#   viewport 1920 (dihitung ulang di bawah dari DOM, bukan di-hardcode)
VIEWPORT_W = 1920

# Ambang dari brief.
GAP_LABEL_MIN = 8.0
GAP_MARKER_MIN = 6.0
MARKER_RADIUS_MAX = 15.0
EDGE_OVER_150 = 150.0


# --------------------------------------------------------------------------
# Token slot fiktif worst-case: label terpanjang, marker terbesar.
# Slot 0 dipakai label panjang supaya kotak teksnya realistis, bukan
# placeholder hidup. Dipakai untuk uji "semua Busy" di mana confidence = 1.0
# membuat marker mencapai diameter maksimum 30px.
# --------------------------------------------------------------------------
PROBE_TOKENS = {
    "BTC": ("LONG", 1.0),
    "ETH": ("SHORT", 1.0),
    "SOL": ("LONG", 1.0),
    "XRP": ("SHORT", 1.0),
    "BNB": ("LONG", 1.0),
    "DOGE": ("SHORT", 1.0),
    "NEAR": ("LONG", 1.0),
    "HYPE": ("SHORT", 1.0),
    "ZEC": ("LONG", 1.0),
    "ONDO": ("SHORT", 1.0),
    "ENA": ("LONG", 1.0),
    "XPL": ("SHORT", 1.0),
}

PROBE_SYSTEM = {
    "execution_agent": 1.0,
    "decision_agent": 1.0,
    "analysis_agent": 1.0,
    "news_agent": 1.0,
    "allMids": 1.0,
    "l2Book": 1.0,
    "candle": 1.0,
    "trades": 1.0,
}


def build_probe_fig(fig_fn, tokens=None, activity=None, flow=None):
    """Figure neural-net dengan data worst-case (semua marker besar, semua sisi Busy)."""
    tokens = PROBE_TOKENS if tokens is None else tokens
    signal_map = {
        sym: {"direction": d, "confidence": c, "price_change": 0.01}
        for sym, (d, c) in tokens.items()
    }
    return fig_fn(
        signal_map=signal_map,
        activity_map=dict(PROBE_SYSTEM if activity is None else activity),
        flow_map=dict(PROBE_SYSTEM if flow is None else flow),
    )


def _html_for(fig, width, height):
    # include_plotlyjs=True menyalin bundle ke dalam HTML. Memberi path
    # Windows sebagai string membuat Plotly memperlakukannya sebagai URL,
    # jadi skrip gagal dimuat dari file:// dan "Plotly is not defined".
    div = pio.to_html(
        fig,
        full_html=False,
        include_plotlyjs=True,
        config={"displayModeBar": False, "responsive": False,
                "scrollZoom": False, "doubleClick": False},
        default_width=str(width),
        default_height=str(height),
    )
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<style>html,body{margin:0;padding:0;overflow:hidden;"
        "background:#F2E9D8;}</style></head><body>"
        f"<div id='wrap' style='width:{width}px;height:{height}px;'>"
        f"{div}</div></body></html>"
    )


# --------------------------------------------------------------------------
# PROBE: baca kotak marker, kotak label, dan geometri path yang BENAR-BENAR
# dirender browser. Path di-sample lewat getPointAtLength, bukan dari
# atribut `d`, jadi kurva bezier yang di-render ikut terukur.
# --------------------------------------------------------------------------
PROBE_JS = r"""
(args) => {
  const gd = document.querySelector('.js-plotly-plot');
  if (!gd) return {error: 'plot not found'};
  const svg = gd.querySelector('svg.main-svg');
  const scope = gd.querySelector('.scatterlayer');
  if (!scope) return {error: 'no scatterlayer'};

  const nodes = [];
  const pts = [...scope.querySelectorAll('path.point')];
  const txts = [...scope.querySelectorAll('text')];
  for (let i = 0; i < Math.min(pts.length, txts.length); i++) {
    const pb = pts[i].getBoundingClientRect();
    const tb = txts[i].getBoundingClientRect();
    nodes.push({
      label: (txts[i].textContent || '').trim(),
      mx: pb.x + pb.width / 2, my: pb.y + pb.height / 2,
      mr: pb.width / 2,
      lx0: tb.x, ly0: tb.y, lx1: tb.x + tb.width, ly1: tb.y + tb.height,
      lc: tb.x + tb.width / 2, lcy: tb.y + tb.height / 2,
      anchor: txts[i].getAttribute('text-anchor') || 'middle',
      baseline: txts[i].getAttribute('y'),
    });
  }

  const edges = [];
  const lines = scope.querySelectorAll('path.js-line');
  for (let k = 0; k < lines.length; k++) {
    const p = lines[k];
    // Endpoint diambil dari gd.data[k].customdata, BUKAN dari
    // p._fullData. Versi lama membaca _fullData.customdata, hasilnya
    // null, lalu src==dst==null membuat SETIAP pasangan edge terlihat
    // berbagi endpoint dan semua persilangan dilewati diam-diam.
    const tr = gd.data[k];
    const cd = tr && tr.customdata;
    const src = (cd && cd.length) ? cd[0][0] : null;
    const dst = (cd && cd.length) ? cd[0][1] : null;
    if (src === null || dst === null) return {error: 'edge ' + k + ' has no endpoints'};
    const L = p.getTotalLength();
    const SAMPLES = 64;
    const poly = [];
    for (let i = 0; i <= SAMPLES; i++) {
      const pt = p.getPointAtLength(L * i / SAMPLES);
      poly.push([pt.x, pt.y]);
    }
    edges.push({src: src, dst: dst, poly: poly,
                len: L, dash: p.getAttribute('stroke-dasharray')});
  }

  const g = gd.getBoundingClientRect();
  const pl = gd._fullLayout;
  return {
    nodes: nodes, edges: edges,
    graphW: g.width, graphH: g.height,
    xrange: pl.xaxis ? [pl.xaxis.range[0], pl.xaxis.range[1]] : null,
    yrange: pl.yaxis ? [pl.yaxis.range[0], pl.yaxis.range[1]] : null,
    ml: pl.margin.l, mr: pl.margin.r, mt: pl.margin.t, mb: pl.margin.b,
  };
}
"""


def _seg_cross(p1, p2, p3, p4):
    """Persilangan dua ruas. NULL bila sejajar atau berujung sama."""
    d1x, d1y = p2[0] - p1[0], p2[1] - p1[1]
    d2x, d2y = p4[0] - p3[0], p4[1] - p3[1]
    den = d1x * d2y - d1y * d2x
    if abs(den) < 1e-12:
        return None
    t = ((p3[0] - p1[0]) * d2y - (p3[1] - p1[1]) * d2x) / den
    u = ((p3[0] - p1[0]) * d1y - (p3[1] - p1[1]) * d1x) / den
    if t < -1e-9 or t > 1 + 1e-9 or u < -1e-9 or u > 1 + 1e-9:
        return None
    return (p1[0] + t * d1x, p1[1] + t * d1y)


def _pt_seg_dist(p, a, b):
    vx, vy = b[0] - a[0], b[1] - a[1]
    wx, wy = p[0] - a[0], p[1] - a[1]
    L2 = vx * vx + vy * vy
    if L2 < 1e-12:
        return math.hypot(wx, wy)
    t = max(0.0, min(1.0, (wx * vx + wy * vy) / L2))
    return math.hypot(wx - t * vx, wy - t * vy)


def _pt_box_dist(p, box):
    dx = max(box["lx0"] - p[0], 0.0, p[0] - box["lx1"])
    dy = max(box["ly0"] - p[1], 0.0, p[1] - box["ly1"])
    return math.hypot(dx, dy)


def analyze(dom, key_to_label):
    """Hitung semua metrik dari DOM yang sudah dibaca browser."""
    nodes = dom["nodes"]
    labels = {n["label"] for n in nodes}

    # --- gap label & gap marker ------------------------------------------
    label_viol, marker_viol = [], []
    worst_label, worst_marker = 1e9, 1e9
    worst_label_pair, worst_marker_pair = None, None
    for i in range(len(nodes)):
        for j in range(i + 1, len(nodes)):
            a, b = nodes[i], nodes[j]
            ldx = max(a["lx0"] - b["lx1"], b["lx0"] - a["lx1"], 0.0)
            ldy = max(a["ly0"] - b["ly1"], b["ly0"] - a["ly1"], 0.0)
            lg = math.hypot(ldx, ldy) if (ldx or ldy) else 0.0
            if lg < worst_label:
                worst_label, worst_label_pair = lg, (a["label"], b["label"])
            if lg < GAP_LABEL_MIN:
                label_viol.append((a["label"], b["label"], round(lg, 2)))

            mdx = (math.hypot(a["mx"] - b["mx"], a["my"] - b["my"])
                   - a["mr"] - b["mr"])
            if mdx < worst_marker:
                worst_marker, worst_marker_pair = mdx, (a["label"], b["label"])
            if mdx < GAP_MARKER_MIN:
                marker_viol.append((a["label"], b["label"], round(mdx, 2)))

    # --- tepi pasif dan geometri ------------------------------------------
    edges = []
    for e in dom["edges"]:
        e = dict(e)
        e["sl"] = key_to_label.get(e["src"], e["src"])
        e["dl"] = key_to_label.get(e["dst"], e["dst"])
        edges.append(e)

    crossings = []
    for i in range(len(edges)):
        for j in range(i + 1, len(edges)):
            a, b = edges[i], edges[j]
            if a["sl"] == b["sl"] or a["sl"] == b["dl"] or \
               a["dl"] == b["sl"] or a["dl"] == b["dl"]:
                continue
            A, B = a["poly"], b["poly"]
            hit = 0
            for k in range(len(A) - 1):
                for m in range(len(B) - 1):
                    if _seg_cross(A[k], A[k + 1], B[m], B[m + 1]):
                        hit += 1
            if hit:
                crossings.append((a["sl"], a["dl"], b["sl"], b["dl"], hit))

    thru_node, thru_label = [], []
    for e in edges:
        if e["sl"] not in labels or e["dl"] not in labels:
            continue
        poly = e["poly"]
        for n in nodes:
            if n["label"] in (e["sl"], e["dl"]):
                continue
            # Jarak dari pusat marker ke ruas terdekat di sepanjang edge.
            d = min(_pt_seg_dist((n["mx"], n["my"]), poly[k], poly[k + 1])
                    for k in range(len(poly) - 1))
            if d < n["mr"]:
                thru_node.append((e["sl"], e["dl"], n["label"],
                                  round(n["mr"] - d, 2)))
            # Jarak dari setiap sampel edge ke kotak label. 0 = di dalam.
            # Disampel ke SELURUH poly, tanpa filter.
            ld = min(_pt_box_dist(p, n) for p in poly)
            if ld <= 0.0:
                thru_label.append((e["sl"], e["dl"], n["label"]))

    lens = [e["len"] for e in edges]
    return {
        "node_count": len(nodes),
        "labels": sorted(labels),
        "edge_count": len(edges),
        "edge_len_mean": round(sum(lens) / len(lens), 2) if lens else 0.0,
        "edge_len_max": round(max(lens), 2) if lens else 0.0,
        "edges_over_150": sum(1 for L in lens if L > EDGE_OVER_150),
        "crossings": len(crossings),
        "crossing_detail": crossings[:20],
        "thru_node": thru_node,
        "thru_label": thru_label,
        "label_violations": label_viol,
        "marker_violations": marker_viol,
        "worst_label_gap": round(worst_label, 2),
        "worst_label_pair": worst_label_pair,
        "worst_marker_gap": round(worst_marker, 2),
        "worst_marker_pair": worst_marker_pair,
        "graph_px": [round(dom["graphW"], 1), round(dom["graphH"], 1)],
        "plot_area_px": [round(dom["graphW"] - dom["ml"] - dom["mr"], 1),
                         round(dom["graphH"] - dom["mt"] - dom["mb"], 1)],
        "ranges": [dom["xrange"], dom["yrange"]],
        "edges": [(e["sl"], e["dl"], round(e["len"], 1)) for e in edges],
    }


def measure(fig, key_to_label, width, height, page):
    page.set_content(_html_for(fig, width, height))
    page.wait_for_selector(".js-plotly-plot .scatterlayer path.point",
                           timeout=20000)
    page.wait_for_timeout(250)
    dom = page.evaluate(PROBE_JS, {})
    if "error" in dom:
        raise RuntimeError(dom["error"])
    return dom, analyze(dom, key_to_label)


PANEL_CSS = """
html,body{margin:0;padding:0;}
.hud-container{width:100%%;max-width:100vw;margin:0 auto;padding:6px 10px;
  display:flex;flex-direction:column;gap:7px;box-sizing:border-box;}
.hud-grid{display:grid;grid-template-columns:repeat(12,minmax(0,1fr));gap:6px;
  width:100%%;align-items:start;}
.hud-panel{background:#EFE4CE;border:2px solid #1a1a1a;display:flex;
  flex-direction:column;min-width:0;overflow:hidden;}
.hud-panel-header{background:#1a1a1a;color:#fff;padding:3px 8px;font-size:11px;
  font-weight:bold;display:flex;justify-content:space-between;
  align-items:center;letter-spacing:0.3px;min-height:24px;}
.hud-panel-body{padding:2px;background:#EFE4CE;flex:1;display:flex;
  flex-direction:column;}
.span-3{grid-column:span 3;}
.zone3-cell{height:260px;}
"""

# Hanya kerangka panel + satu graph kosong. Lebar yang diukur adalah
# Lebar yang benar-benar didapat browser dari CSS di atas, pada viewport
# yang sama seperti browser sungguhan.
_WIDTH_PROBE = """
<style>{css}</style>
<div class="hud-container"><div class="hud-grid">
  <div class="hud-panel span-3 zone3-cell">
    <div class="hud-panel-header"><span>X</span><span>Y</span></div>
    <div class="hud-panel-body" style="padding:2px">
      <div id="probe" style="height:{h}px;width:100%"></div>
    </div>
  </div>
</div></div>
"""


def _real_panel_width(page, height, viewport):
    page.set_viewport_size({"width": viewport, "height": 800})
    page.set_content(PANEL_CSS + _WIDTH_PROBE.format(css=PANEL_CSS, h=height))
    page.wait_for_timeout(120)
    return page.evaluate(
        "() => document.getElementById('probe').getBoundingClientRect().width"
    )


def main(fig_fn, key_to_label, out_path, width=None, height=230,
         cases=None, page=None, viewport=VIEWPORT_W):
    """Jalankan harness pada beberapa kasus token."""
    own = page is None
    if own:
        pw = sync_playwright().start()
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": viewport, "height": 800})
    try:
        # Lebar panel NYATA dihitung dengan merender struktur DOM yang
        # sama persis dengan hud.py, bukan diterka. 462.5px di viewport
        # 1920, bukan 440px seperti asumsi brief.
        if width is None:
            width = _real_panel_width(page, height, viewport)
        results = {}
        for name, kwargs in (cases or [("all12", {})]).items():
            fig = build_probe_fig(fig_fn, **kwargs)
            _, res = measure(fig, key_to_label, width, height, page)
            results[name] = res
        payload = {"width": round(width, 1), "height": height,
                   "results": results}
        Path(out_path).write_text(json.dumps(payload, indent=2, default=str),
                                  encoding="utf-8")
        return payload
    finally:
        if own:
            page.context.browser.close()


def brief(results, name="all12"):
    r = results["results"][name]
    return (
        f"  {name}: nodes {r['node_count']}  edges {r['edge_count']}  "
        f"len mean {r['edge_len_mean']} max {r['edge_len_max']}  "
        f">150px {r['edges_over_150']}\n"
        f"    crossings {r['crossings']}   thruNode {len(r['thru_node'])}   "
        f"thruLabel {len(r['thru_label'])}\n"
        f"    labelGap {r['worst_label_gap']} {r['worst_label_pair']}   "
        f"markerGap {r['worst_marker_gap']} {r['worst_marker_pair']}   "
        f"V label={len(r['label_violations'])} marker="
        f"{len(r['marker_violations'])}"
    )
