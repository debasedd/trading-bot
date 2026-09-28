"""Browser-truth geometry harness for the neural-net constellation.

Renders the REAL create_neural_net_fig output in headless Chromium and
returns real DOM boxes: label rects, marker rects, and edge polyline
points (path d mapped through getScreenCTM).

Nothing here is estimated. Everything is read from the renderer.
"""
import itertools
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

CSS = os.path.join(ROOT, "dashboard", "assets", "style.css").replace("\\", "/")

PROBE = r"""
() => {
  const gd = document.getElementById('hud-neural-graph');
  if (!gd || !gd._fullLayout) return {error: 'plot not ready'};
  const L = gd._fullLayout;
  const gr = gd.getBoundingClientRect();
  const svg = gd.querySelector('svg.main-svg');
  const sr = svg.getBoundingClientRect();
  const m = L.margin;

  const edges = [];
  const full = gd._fullData || [];
  const traces = gd.querySelectorAll('g.scatterlayer g.trace');
  let ti = -1;
  for (const t of traces) {
    const p = t.querySelector('path.js-line');
    if (!p) continue;
    ti += 1;
    const cd = full[ti] && full[ti].customdata;
    let src = null, dst = null, flow = null;
    if (cd && cd.length) { src = cd[0][0]; dst = cd[0][1]; flow = cd[0][2]; }

    // Sample the REAL rendered geometry with getPointAtLength, then map
    // through getScreenCTM. Sampling is immune to the `d` grammar (no
    // number-pairing bug can shift a coordinate); the CTM puts the result
    // in the same space as getBoundingClientRect, so edges and marker/label
    // boxes can be compared directly.
    const len = p.getTotalLength();
    const ctm = p.getScreenCTM();
    const N = 64;
    const pts = [];
    for (let k = 0; k <= N; k++) {
      const sp = p.getPointAtLength(len * k / N);
      const q = new DOMPoint(sp.x, sp.y).matrixTransform(ctm);
      pts.push([q.x, q.y]);
    }
    edges.push({src: src, dst: dst, flow: flow, pts: pts,
                w: parseFloat(p.getAttribute('stroke-width') || '1')});
  }

  const scope = gd.querySelector('.scatterlayer');
  const pts = [...scope.querySelectorAll('path.point')];
  const txts = [...scope.querySelectorAll('text')];
  const nodes = [];
  const n = Math.min(pts.length, txts.length);
  for (let i = 0; i < n; i++) {
    const pb = pts[i].getBoundingClientRect();
    const tb = txts[i].getBoundingClientRect();
    const cs = getComputedStyle(txts[i]);
    nodes.push({
      label: (txts[i].textContent || '').trim(),
      m: [pb.x, pb.y, pb.x + pb.width, pb.y + pb.height],
      mc: [pb.x + pb.width / 2, pb.y + pb.height / 2],
      md: [pb.width, pb.height],
      l: [tb.x, tb.y, tb.x + tb.width, tb.y + tb.height],
      lw: tb.width, lh: tb.height,
      fs: cs.fontSize, family: cs.fontFamily
    });
  }
  return {
    graph: [gr.x, gr.y, gr.width, gr.height],
    svg: [sr.x, sr.y, sr.width, sr.height],
    margin: L.margin, xRange: L.xaxis.range, yRange: L.yaxis.range,
    plot: [sr.x + m.l, sr.y + m.t, sr.x + sr.width - m.r, sr.y + sr.height - m.b],
    edges: edges, nodes: nodes
  };
}
"""


def render_html(fig, config, out_path, height=230, body_pad=2):
    import plotly.io as pio
    div = pio.to_html(fig, full_html=False, include_plotlyjs="cdn",
                      config=config, div_id="hud-neural-graph")
    div = div.replace('<div id="hud-neural-graph"',
                      '<div id="hud-neural-graph" style="height:%dpx;width:100%%"' % height)
    html = (
        '<!doctype html><html><head><meta charset="utf-8">'
        '<link rel="stylesheet" href="file:///' + CSS + '"></head><body>'
        '<div class="hud-container"><div class="hud-grid">'
        '<div class="hud-panel span-3 zone3-cell">'
        '<div class="hud-panel-header"><span>SNIPE NEURAL NET</span>'
        '<span class="header-tag">CONSTELLATION ENGINE</span></div>'
        '<div class="hud-panel-body" style="padding:' + str(body_pad) + 'px">'
        + div +
        '</div></div></div></div>'
        '<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>'
        '</body></html>'
    )
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    return out_path


def _seg_pts(pts, step=2.5):
    out = []
    for a, b in zip(pts, pts[1:]):
        d = math.hypot(b[0] - a[0], b[1] - a[1])
        k = max(1, int(math.ceil(d / step)))
        for j in range(k + 1):
            t = j / k
            out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
    return out


def _cross(o, a, b):
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _inter(p1, p2, p3, p4):
    d1 = _cross(p3, p4, p1)
    d2 = _cross(p3, p4, p2)
    d3 = _cross(p1, p2, p3)
    d4 = _cross(p1, p2, p4)
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0))


def _seg_rect_hit(p1, p2, rect):
    x0, y0, x1, y1 = rect
    if x0 > x1 or y0 > y1:
        return False
    if (x0 <= p1[0] <= x1 and y0 <= p1[1] <= y1) or \
       (x0 <= p2[0] <= x1 and y0 <= p2[1] <= y1):
        return True
    edges = [((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)),
             ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))]
    for a, b in edges:
        if _inter(p1, p2, a, b):
            return True
    return False


def _seg_circle_hit(p1, p2, c, r):
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    L2 = dx * dx + dy * dy
    if L2 < 1e-12:
        return math.hypot(p1[0] - c[0], p1[1] - c[1]) <= r
    t = ((c[0] - p1[0]) * dx + (c[1] - p1[1]) * dy) / L2
    t = max(0.0, min(1.0, t))
    px, py = p1[0] + dx * t, p1[1] + dy * t
    return math.hypot(px - c[0], py - c[1]) <= r


def rect_gap(a, b):
    dx = max(a[0] - b[2], b[0] - a[2], 0.0)
    dy = max(a[1] - b[3], b[1] - a[3], 0.0)
    return math.hypot(dx, dy)


def circle_gap(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1]) - a[2] / 2 - b[2] / 2


LABEL_MIN = 8.0
MARKER_MIN = 6.0


def _mk(node):
    return (node["mc"][0], node["mc"][1], node["md"][0])


def analyse(probe, label_min=LABEL_MIN, marker_min=MARKER_MIN,
            use_label_boxes=True, key_to_label=None):
    """Full metric bundle from a raw browser probe.

    `key_to_label` maps internal node keys (e.g. CORE_ENGINE) to the label
    the DOM prints (e.g. SIGNAL). Edges are emitted in key form, nodes are
    rendered in label form; without this map every edge appears to strike its
    own endpoints and every crossing is counted against phantom pairs.
    """
    nodes = probe["nodes"]
    edges = probe["edges"]
    k2l = key_to_label or {}
    for e in edges:
        e["sl"] = k2l.get(e["src"], e["src"])
        e["dl"] = k2l.get(e["dst"], e["dst"])
    by_label = {n["label"]: n for n in nodes}
    res = {}

    # ---- hard self-check: every edge must actually terminate on its own
    # endpoints' markers. If this fails, the coordinate space or the
    # key->label map is wrong and every number below is garbage.
    bad_endpoints = []
    for e in edges:
        for ep, other in ((e["pts"][0], e["sl"]), (e["pts"][-1], e["dl"])):
            n = by_label.get(other)
            if n is None:
                bad_endpoints.append("%s/%s no such node" % (e["src"], e["dst"]))
                break
            d = math.hypot(ep[0] - n["mc"][0], ep[1] - n["mc"][1])
            if d > max(n["md"]) / 2 + 6.0:
                bad_endpoints.append("%s>%s endpoint %.1fpx from %s centre"
                                     % (e["src"], e["dst"], d, other))
    res["endpoint_mismatch"] = bad_endpoints

    lab_min, lab_worst = 1e9, None
    mkr_min, mkr_worst = 1e9, None
    lab_v, mkr_v = 0, 0
    for i, j in itertools.combinations(range(len(nodes)), 2):
        g = rect_gap(nodes[i]["l"], nodes[j]["l"])
        if g < lab_min:
            lab_min, lab_worst = g, (nodes[i]["label"], nodes[j]["label"])
        if g < label_min:
            lab_v += 1
        mg = circle_gap(_mk(nodes[i]), _mk(nodes[j]))
        if mg < mkr_min:
            mkr_min, mkr_worst = mg, (nodes[i]["label"], nodes[j]["label"])
        if mg < marker_min:
            mkr_v += 1
    res["node_count"] = len(nodes)
    res["label_gap_min"] = round(lab_min, 2)
    res["label_gap_worst_pair"] = list(lab_worst) if lab_worst else None
    res["marker_gap_min"] = round(mkr_min, 2)
    res["marker_gap_worst_pair"] = list(mkr_worst) if mkr_worst else None
    res["label_violations"] = lab_v
    res["marker_violations"] = mkr_v

    lens = []
    for e in edges:
        p = e["pts"]
        lens.append(sum(math.hypot(b[0] - a[0], b[1] - a[1])
                        for a, b in zip(p, p[1:])))
    res["edge_count"] = len(edges)
    res["edge_len_mean"] = round(sum(lens) / len(lens), 2) if lens else 0.0
    res["edge_len_max"] = round(max(lens), 2) if lens else 0.0
    res["edges_over_150"] = sum(1 for L in lens if L > 150)
    res["edges_over_200"] = sum(1 for L in lens if L > 200)

    dense = [_seg_pts(e["pts"]) for e in edges]

    cross = []
    for i, j in itertools.combinations(range(len(edges)), 2):
        ei, ej = edges[i], edges[j]
        if ei["src"] in (ej["src"], ej["dst"]) or \
           ei["dst"] in (ej["src"], ej["dst"]):
            continue
        hit = False
        A, B = dense[i], dense[j]
        for a1, a2 in zip(A, A[1:]):
            for b1, b2 in zip(B, B[1:]):
                if _inter(a1, a2, b1, b2):
                    hit = True
                    break
            if hit:
                break
        if hit:
            cross.append("%s>%s X %s>%s"
                         % (ei["sl"], ei["dl"], ej["sl"], ej["dl"]))
    res["edge_crossings"] = len(cross)
    res["crossing_pairs"] = cross

    thru_node, thru_label = [], []
    for e, D in zip(edges, dense):
        for n in nodes:
            nm = n["label"]
            if nm == e["sl"] or nm == e["dl"]:
                continue
            r = n["md"][0] / 2 + 0.5
            for a, b in zip(D, D[1:]):
                if _seg_circle_hit(a, b, n["mc"], r):
                    thru_node.append("%s>%s hits %s" % (e["sl"], e["dl"], nm))
                    break
            if not use_label_boxes:
                continue
            for a, b in zip(D, D[1:]):
                if _seg_rect_hit(a, b, n["l"]):
                    thru_label.append("%s>%s hits %s" % (e["sl"], e["dl"], nm))
                    break
    res["edge_through_node"] = thru_node
    res["edge_through_label"] = thru_label

    # ---- clipping: a label wider than the plot area cannot be drawn in
    # full, and a label that leaves the plot rect is visually truncated.
    # This is invisible to a pairwise-gap test, which is why it survived.
    if "plot" in probe:
        px0, py0, px1, py1 = probe["plot"]
        clipped = []
        for n in nodes:
            lx0, ly0, lx1, ly1 = n["l"]
            over = round(max(0.0, px0 - lx0, ly0 - py0, lx1 - px1, ly1 - py1), 2)
            if over > 0.5:
                clipped.append((n["label"], over))
        res["label_clipped"] = clipped
        res["label_widest"] = max((n["lw"] for n in nodes), default=0.0)
        res["plot_w_px"] = round(px1 - px0, 2)

    m = probe["margin"]
    pw = probe["svg"][2] - m["l"] - m["r"]
    ph = probe["svg"][3] - m["t"] - m["b"]
    res["graph_w"] = round(probe["graph"][2], 2)
    res["plot_w"] = round(pw, 2)
    res["plot_h"] = round(ph, 2)
    res["pxu_x"] = round(pw / (probe["xRange"][1] - probe["xRange"][0]), 4)
    res["pxu_y"] = round(ph / (probe["yRange"][1] - probe["yRange"][0]), 4)
    res["font"] = nodes[0]["fs"] if nodes else None
    res["family"] = nodes[0]["family"] if nodes else None
    return res


def dump(tag, res):
    keys = ["graph_w", "plot_w", "plot_h", "pxu_x", "pxu_y", "font", "node_count",
            "label_gap_min", "label_gap_worst_pair", "label_violations",
            "marker_gap_min", "marker_gap_worst_pair", "marker_violations",
            "edge_count", "edge_len_mean", "edge_len_max", "edges_over_150",
            "edge_crossings", "edge_through_node", "edge_through_label"]
    parts = []
    for k in keys:
        v = res.get(k)
        parts.append("%s=%s" % (k, v))
    print("[%s] " % tag + "  ".join(parts))
