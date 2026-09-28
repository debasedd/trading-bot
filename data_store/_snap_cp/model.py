"""Analytic geometry model for the constellation, calibrated to the browser.

The browser is the oracle. This model reproduces its boxes so a candidate
space can be searched; every surviving candidate is then re-measured in
Chromium before it is reported.

Calibration source: data_store/_snap_cp/calib.json (browser, md5-pinned
hud_figures). Coordinate model reproduced marker centres to 0.000px.
"""
import itertools
import math

# --- text metrics, measured from the browser (Share Tech Mono, 10px) ------
CHAR_W = 5.203
LABEL_H = 11.0
LABEL_PAD = 0.41
GAP_ABOVE = 3.52      # marker top -> label bottom, "top center"
GAP_BELOW = 2.20      # marker bottom -> label top, "bottom center"

# Marker diameters are Plotly `marker.size` in PIXELS, independent of axis
# scale. Worst case per the brief is 30px.
MARKER_MAX = 30.0


def label_w(text):
    return CHAR_W * len(text) + LABEL_PAD


# --- the viewports that actually occur ---------------------------------
# .span-3 of a 12-col grid inside .hud-container (padding 10, gutter 6).
# Measured, not guessed: see measure_panel output.
VIEWPORTS = {
    1280: 274.5, 1366: 296.0, 1440: 314.5,
    1600: 354.5, 1920: 434.5, 2560: 594.5,
}
PLOT_H = 204.0
XRANGE = (0.0, 22.0)
YRANGE = (0.0, 11.5)
PXU_Y = PLOT_H / (YRANGE[1] - YRANGE[0])     # 17.7391, FIXED


def pxu_x(plot_w):
    return plot_w / (XRANGE[1] - XRANGE[0])


# --- node / label boxes in px, plot-area local coords (y DOWN) ----------
def boxes(x, y, text, textpos, marker_d, pxux):
    cx = (x - XRANGE[0]) * pxux
    cy = (YRANGE[1] - y) * PXU_Y
    r = marker_d / 2.0
    w = label_w(text)
    if textpos == "top center":
        ly0 = cy - r - GAP_ABOVE - LABEL_H
        ly1 = cy - r - GAP_ABOVE
    else:
        ly0 = cy + r + GAP_BELOW
        ly1 = ly0 + LABEL_H
    return dict(
        c=(cx, cy), r=r, md=marker_d,
        box=(cx - w / 2, ly0, cx + w / 2, ly1), lw=w,
    )


def rect_gap(a, b):
    dx = max(a[0] - b[2], b[0] - a[2], 0.0)
    dy = max(a[1] - b[3], b[1] - a[3], 0.0)
    return math.hypot(dx, dy)


def marker_gap(a, b):
    return math.hypot(a["c"][0] - b["c"][0], a["c"][1] - b["c"][1]) - a["r"] - b["r"]


def _cross(o, a, b):
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _inter(p1, p2, p3, p4):
    d1, d2 = _cross(p3, p4, p1), _cross(p3, p4, p2)
    d3, d4 = _cross(p1, p2, p3), _cross(p1, p2, p4)
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0))


def _seg_circle_hit(p1, p2, c, r):
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    L2 = dx * dx + dy * dy
    if L2 < 1e-12:
        return math.hypot(p1[0] - c[0], p1[1] - c[1]) <= r
    t = ((c[0] - p1[0]) * dx + (c[1] - p1[1]) * dy) / L2
    t = max(0.0, min(1.0, t))
    return math.hypot(p1[0] + dx * t - c[0], p1[1] + dy * t - c[1]) <= r


def _seg_rect_hit(p1, p2, rect):
    x0, y0, x1, y1 = rect
    if (x0 <= p1[0] <= x1 and y0 <= p1[1] <= y1) or \
       (x0 <= p2[0] <= x1 and y0 <= p2[1] <= y1):
        return True
    for a, b in (((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)),
                 ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))):
        if _inter(p1, p2, a, b):
            return True
    return False


def _seg_pts(p1, p2, step=1.5):
    d = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
    k = max(1, int(math.ceil(d / step)))
    return [(p1[0] + (p2[0] - p1[0]) * j / k, p1[1] + (p2[1] - p1[1]) * j / k)
            for j in range(k + 1)]


def bow_ctrl(a, b, bow_amt):
    """Quadratic control point for the same bow rule hud_figures uses."""
    mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
    dx, dy = b[0] - a[0], b[1] - a[1]
    L = math.hypot(dx, dy)
    if L < 1e-9:
        return None
    bow = 0.0 if abs(dx) < 1e-6 else min(L * 0.06, 0.9) * bow_amt
    return (mx - dy / L * bow, my + dx / L * bow)


def qcurve(a, b, ctrl, samples=16):
    if ctrl is None:
        return [a, b]
    out = []
    for i in range(samples):
        t = i / (samples - 1)
        inv = 1 - t
        out.append((inv * inv * a[0] + 2 * inv * t * ctrl[0] + t * t * b[0],
                    inv * inv * a[1] + 2 * inv * t * ctrl[1] + t * t * b[1]))
    return out


def evaluate(nodes, edges, plot_w, bow_amt=1.0, label_min=8.0, marker_min=6.0):
    """nodes: {key: (x, y, label, textpos, marker_d)}
       edges: [(src_key, dst_key)]
    Returns a metric dict. Everything is px in plot-local coords.
    """
    pxux = pxu_x(plot_w)
    B = {k: boxes(v[0], v[1], v[2], v[3], v[4], pxux) for k, v in nodes.items()}

    lab_min, lab_worst = 1e9, None
    mkr_min, mkr_worst = 1e9, None
    lab_v, mkr_v = 0, 0
    keys = list(nodes)
    for i, j in itertools.combinations(keys, 2):
        g = rect_gap(B[i]["box"], B[j]["box"])
        if g < lab_min:
            lab_min, lab_worst = g, (nodes[i][2], nodes[j][2])
        if g < label_min:
            lab_v += 1
        mg = marker_gap(B[i], B[j])
        if mg < mkr_min:
            mkr_min, mkr_worst = mg, (nodes[i][2], nodes[j][2])
        if mg < marker_min:
            mkr_v += 1

    # edges, in px, as densified polylines
    polys, lens = [], []
    for s, d in edges:
        a, b = B[s]["c"], B[d]["c"]
        c = bow_ctrl(a, b, bow_amt)
        pts = qcurve(a, b, c)
        L = sum(math.hypot(q[0] - p[0], q[1] - p[1]) for p, q in zip(pts, pts[1:]))
        lens.append(L)
        dense = []
        for p, q in zip(pts, pts[1:]):
            dense.extend(_seg_pts(p, q))
        polys.append((s, d, dense))

    crossings = []
    for (s1, d1, A), (s2, d2, Bp) in itertools.combinations(polys, 2):
        if s1 in (s2, d2) or d1 in (s2, d2):
            continue
        hit = False
        for a1, a2 in zip(A, A[1:]):
            for b1, b2 in zip(Bp, Bp[1:]):
                if _inter(a1, a2, b1, b2):
                    hit = True
                    break
            if hit:
                break
        if hit:
            crossings.append("%s>%s X %s>%s" % (nodes[s1][2], nodes[d1][2],
                                                nodes[s2][2], nodes[d2][2]))

    thru_node, thru_lbl = [], []
    for s, d, D in polys:
        for k in keys:
            if k == s or k == d:
                continue
            if any(_seg_circle_hit(a, b, B[k]["c"], B[k]["r"]) for a, b in zip(D, D[1:])):
                thru_node.append("%s>%s hits %s" % (nodes[s][2], nodes[d][2], nodes[k][2]))
            if any(_seg_rect_hit(a, b, B[k]["box"]) for a, b in zip(D, D[1:])):
                thru_lbl.append("%s>%s hits %s" % (nodes[s][2], nodes[d][2], nodes[k][2]))

    clipped = []
    for k in keys:
        x0, y0, x1, y1 = B[k]["box"]
        over = max(0.0, -x0, -y0, x1 - plot_w, y1 - PLOT_H)
        if over > 0.5:
            clipped.append((nodes[k][2], round(over, 2)))

    return dict(
        plot_w=plot_w, pxux=round(pxux, 4),
        label_gap_min=round(lab_min, 2), label_gap_worst=lab_worst,
        label_violations=lab_v,
        marker_gap_min=round(mkr_min, 2), marker_gap_worst=mkr_worst,
        marker_violations=mkr_v,
        edge_count=len(edges),
        edge_len_mean=round(sum(lens) / len(lens), 2) if lens else 0.0,
        edge_len_max=round(max(lens), 2) if lens else 0.0,
        edges_over_150=sum(1 for L in lens if L > 150),
        crossings=len(crossings), crossing_list=crossings,
        thru_node=thru_node, thru_label=thru_lbl,
        clipped=clipped,
    )


def score(m):
    """Lower is better. Hard constraints dominate."""
    pen = 0.0
    pen += 20000 * m["label_violations"]
    pen += 20000 * m["marker_violations"]
    pen += 6000 * m["crossings"]
    pen += 2500 * len(m["thru_node"])
    pen += 2500 * len(m["thru_label"])
    pen += 900 * len(m["clipped"])
    pen += 200 * m["edges_over_150"]
    pen += 3.0 * m["edge_len_max"]
    return pen
