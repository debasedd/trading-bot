"""
constellation_search.py — Cari koordinat + daftar sisi yang满足 semua syarat.

Model analitik dikalibrasi dari DOM browser (data_store/calibrate_probe.py):
  plot area  434.5 x 204.0 px pada viewport 1920 (margin l14 r14 t10 b16)
  label font 10px Share Tech Mono -> 6.088 px/char, tinggi kotak 11px
  label 'top center'    : pusat teks = marker_y - 8.6 - r
  label 'bottom center' : pusat teks = marker_y + 9.0 + r
  marker  : diameter = size (12..30), jadi r maksimum 15px

Semua jarak dihitung dalam PX. Ambang: label >= 8px, marker >= 6px.
Sisi = Busur Bezier kuadrat yang sama persis dengan hud_figures._edge_curve,
disampel jadi polyline, lalu diuji persis seperti yang digambar browser.
"""
from __future__ import annotations

import itertools
import math

# ---- konstanta terukur (JANGAN di-hardcode ulang di tempat lain) ----------
PLOT_W = 434.5
PLOT_H = 204.0
PLOT_H_UNIT = 11.5     # yaxis.range[1] saat ini
CH_PX = 6.088          # px per karakter, rata-rata dari DOM
LABEL_H = 11.0         # tinggi kotak teks
STROKE = 1.5           # marker.line.width; ikut menambah bounding box
OFF_TOP = 8.6          # jarak tepi marker -> pusat teks, 'top center'
OFF_BOT = 9.0          # jarak tepi marker -> pusat teks, 'bottom center'
MAX_R = 15.0           # marker maksimum 30px diameter

LABEL_GAP_MIN = 8.0
MARKER_GAP_MIN = 6.0
NODE_CLEAR_MIN = 2.0   # jarak minimum sisi -> marker bukan-endpoint
LABEL_CLEAR_MIN = 2.0  # jarak minimum sisi -> kotak label


# ------------------------------------------------------------ geometry ----
def px_per_unit(xr, yr, panel_w=PLOT_W):
    """px per unit pada lebar panel tertentu."""
    pw = panel_w * (PLOT_W / (PLOT_W + 28.0))   # 434.5 dari 462.5 (margin 28)
    return pw / (xr[1] - xr[0]), PLOT_H / (yr[1] - yr[0])


def hw(label):
    return CH_PX * len(label) / 2.0


def boxes(label, x, y, label_above, ppx, ppy, r=MAX_R, ymax=PLOT_H_UNIT):
    """Kotak label + lingkaran marker dalam px.

    Jarak label dari marker bukan konstanta: Plotly menaruh teks di luar
    tepi marker, jadi offset = konstanta + radius. Dikalibrasi dari DOM:
    marker 18px -> 'top center' pusat teks -17.25px (= -8.25 - 9),
    marker 21px -> -19.12px (= -8.62 - 10.5), marker 24px -> +21.0px
    ('bottom center' = 9.0 + 12).
    """
    mx = x * ppx
    my = (ymax - y) * ppy
    off = -(OFF_TOP + r) if label_above else (OFF_BOT + r)
    lcy = my + off
    return {
        "label": label, "mx": mx, "my": my, "r": r,
        "lx0": mx - hw(label), "lx1": mx + hw(label),
        "ly0": lcy - LABEL_H / 2, "ly1": lcy + LABEL_H / 2,
    }


def rect_gap(a, b):
    dx = max(b["lx0"] - a["lx1"], a["lx0"] - b["lx1"], 0.0)
    dy = max(b["ly0"] - a["ly1"], a["ly0"] - b["ly1"], 0.0)
    return math.hypot(dx, dy)


def marker_gap(a, b):
    return math.hypot(a["mx"] - b["mx"], a["my"] - b["my"]) - a["r"] - b["r"]


def _cpt_dist(a, b, p):
    dx, dy = b[0] - a[0], b[1] - a[1]
    L2 = dx * dx + dy * dy
    if L2 <= 1e-12:
        return math.hypot(p[0] - a[0], p[1] - a[1])
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2))
    return math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy))


def _cross(o, p, q):
    return (p[0] - o[0]) * (q[1] - o[1]) - (p[1] - o[1]) * (q[0] - o[0])


def seg_dist(a1, a2, b1, b2):
    d1, d2 = _cross(b1, b2, a1), _cross(b1, b2, a2)
    d3, d4 = _cross(a1, a2, b1), _cross(a1, a2, b2)
    if ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)):
        return 0.0
    return min(_cpt_dist(a1, a2, b1), _cpt_dist(a1, a2, b2),
               _cpt_dist(b1, b2, a1), _cpt_dist(b1, b2, a2))


def seg_rect_dist(a, b, n):
    if (n["lx0"] <= a[0] <= n["lx1"] and n["ly0"] <= a[1] <= n["ly1"]) or \
       (n["lx0"] <= b[0] <= n["lx1"] and n["ly0"] <= b[1] <= n["ly1"]):
        return 0.0
    c = [(n["lx0"], n["ly0"]), (n["lx1"], n["ly0"]),
         (n["lx1"], n["ly1"]), (n["lx0"], n["ly1"])]
    best = min(_cpt_dist(a, b, k) for k in c)
    for i in range(4):
        best = min(best, seg_dist(a, b, c[i], c[(i + 1) % 4]))
        if best <= 0:
            return 0.0
    return best


def poly_len(P):
    return sum(math.dist(P[i], P[i + 1]) for i in range(len(P) - 1))


# ------------------------------------------------- busur edge (sama dgn app)
def edge_curve(a, b, ppx, ppy, samples=18, bow_cap=0.9, bow_k=0.06):
    """Salinan _edge_curve dari hud_figures, langsung dalam px."""
    ax, ay = a[0] * ppx, a[1] * ppy
    bx, by = b[0] * ppx, b[1] * ppy
    # y Plotly terbalik: makin besar y ke atas
    ax, ay = ax, PLOT_H - ay
    bx, by = bx, PLOT_H - by
    mx, my = (ax + bx) / 2, (ay + by) / 2
    dx, dy = bx - ax, by - ay
    L = math.hypot(dx, dy)
    if L < 1e-9:
        return [(ax, ay), (bx, by)]
    bow = 0.0 if abs(dx) < 1e-6 else min(L * bow_k, bow_cap)
    cx, cy = mx + (-dy / L) * bow, my + (dx / L) * bow
    P = []
    for i in range(samples):
        t = i / (samples - 1)
        iv = 1 - t
        P.append((iv * iv * ax + 2 * iv * t * cx + t * t * bx,
                  iv * iv * ay + 2 * iv * t * cy + t * t * by))
    return P


# ------------------------------------------------------------ evaluate ----
def evaluate(layout, edges, xr, yr, panel_w=PLOT_W):
    """layout: {key: (x, y, label, is_token)}; edges: [(src, dst)]."""
    ppx, ppy = px_per_unit(xr, yr, panel_w)
    nb = {k: boxes(v[2], v[0], v[1], not v[3], ppx, ppy,
                   (v[4] if len(v) > 4 else MAX_R) + STROKE / 2.0,
                   ymax=yr[1])
          for k, v in layout.items()}
    rep = {"panel_w": panel_w, "ppx": round(ppx, 2), "ppy": round(ppy, 2),
           "node_count": len(nb), "edge_count": len(edges)}

    lab = sorted(((rect_gap(a, b), a["label"], b["label"])
                  for a, b in itertools.combinations(nb.values(), 2)))
    mk = sorted(((marker_gap(a, b), a["label"], b["label"])
                 for a, b in itertools.combinations(nb.values(), 2)))
    rep["worst_label_gap"] = round(lab[0][0], 1)
    rep["worst_label_pair"] = [lab[0][1], lab[0][2]]
    rep["label_violations"] = sum(1 for t in lab if t[0] < LABEL_GAP_MIN)
    rep["worst_marker_gap"] = round(mk[0][0], 1)
    rep["worst_marker_pair"] = [mk[0][1], mk[0][2]]
    rep["marker_violations"] = sum(1 for t in mk if t[0] < MARKER_GAP_MIN)

    polys = {}
    for (s, d) in edges:
        polys[(s, d)] = edge_curve((layout[s][0], layout[s][1]),
                                   (layout[d][0], layout[d][1]), ppx, ppy)

    lens = [poly_len(P) for P in polys.values()]
    rep["edge_len_mean"] = round(sum(lens) / len(lens), 1) if lens else None
    rep["edge_len_max"] = round(max(lens), 1) if lens else None
    rep["edges_over_150"] = sum(1 for L in lens if L > 150)

    cross = 0
    keys = list(polys)
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            ka, kb = keys[i], keys[j]
            if set(ka) & set(kb):
                continue
            hit = any(seg_dist(polys[ka][a], polys[ka][a + 1],
                               polys[kb][b], polys[kb][b + 1]) <= 0.0
                      for a in range(len(polys[ka]) - 1)
                      for b in range(len(polys[kb]) - 1))
            cross += hit
    rep["crossings"] = cross

    thru_n, thru_l = [], []
    for k, P in polys.items():
        ends = set(k)
        for key, n in nb.items():
            if key in ends:
                continue
            d = min(_cpt_dist(P[i], P[i + 1], (n["mx"], n["my"]))
                    for i in range(len(P) - 1)) - n["r"]
            if d < NODE_CLEAR_MIN:
                thru_n.append((f"{k[0]}->{k[1]}", n["label"], round(d, 1)))
            d = min(seg_rect_dist(P[i], P[i + 1], n)
                    for i in range(len(P) - 1))
            if d < LABEL_CLEAR_MIN:
                thru_l.append((f"{k[0]}->{k[1]}", n["label"], round(d, 1)))
    rep["edge_through_node"] = len(thru_n)
    rep["edge_through_node_detail"] = thru_n
    rep["edge_through_label"] = len(thru_l)
    rep["edge_through_label_detail"] = thru_l

    # semua kotak harus di dalam area gambar
    out = [n["label"] for n in nb.values()
           if n["lx0"] < 0 or n["lx1"] > panel_w or n["ly0"] < 0
           or n["ly1"] > PLOT_H]
    rep["labels_outside_canvas"] = out

    rep["BAD"] = (rep["label_violations"] + rep["marker_violations"]
                  + rep["edge_through_node"] + rep["edge_through_label"]
                  + cross * 3 + len(out))
    return rep
