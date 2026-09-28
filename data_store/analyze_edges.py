"""
analyze_edges.py — Ukur geometri KONSTELASI dari figure yang BENAR-BENAR
dihasilkan `create_neural_net_fig`, pada kunci signal_map yang BENAR-BENAR
dipakai callback produksi (base symbol, mis. "BTC").

PENTING: identitas simpul dicocokkan lewat LABEL yang benar-benar dirender,
bukan lewat kunci internal. Kunci internal (CORE_ENGINE) tidak pernah
muncul sebagai teks; yang tampil adalah "SIGNAL".
"""
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dashboard.layouts.hud_figures import (  # noqa: E402
    NEURAL_SYSTEM_NODES,
    create_neural_net_fig,
)

PANEL_W, PANEL_H = 470.0, 260.0
MARGIN_L, MARGIN_R, MARGIN_T, MARGIN_B = 14.0, 14.0, 10.0, 16.0
PLOT_W = PANEL_W - MARGIN_L - MARGIN_R
PLOT_H = PANEL_H - MARGIN_T - MARGIN_B
CHAR_PX = 6.0
LABEL_H = 12.0
MAX_MARKER_R = 15.0   # marker max 30px diameter

TOKENS = ["BTC", "ETH", "SOL", "XRP", "BNB", "DOGE",
          "NEAR", "HYPE", "ZEC", "ONDO", "ENA", "XPL"]


def build(tokens=TOKENS, conf=0.7, busy=True):
    sm = {s: {"direction": "LONG", "confidence": conf,
              "price_change": 0.002} for s in tokens}
    return create_neural_net_fig(
        signal_map=sm,
        activity_map={"analysis_agent": 1.0, "decision_agent": 0.9,
                      "execution_agent": 0.8, "news_agent": 0.7} if busy else {},
        flow_map={"allMids": 1.0, "l2Book": 0.8, "candle": 0.5,
                  "trades": 0.6} if busy else {},
    )


def seg_intersect(p, q, r, s):
    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    d1, d2 = cross(r, s, p), cross(r, s, q)
    d3, d4 = cross(p, q, r), cross(p, q, s)
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0))


def pt_seg_dist(pt, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    L2 = dx * dx + dy * dy
    if L2 < 1e-12:
        return math.dist(pt, a)
    t = max(0.0, min(1.0, ((pt[0] - a[0]) * dx + (pt[1] - a[1]) * dy) / L2))
    return math.hypot(pt[0] - (a[0] + t * dx), pt[1] - (a[1] + t * dy))


def analyze(fig, title="", verbose=True):
    dx = fig.layout.xaxis.range[1] - fig.layout.xaxis.range[0]
    dy = fig.layout.yaxis.range[1] - fig.layout.yaxis.range[0]
    sx, sy = PLOT_W / dx, PLOT_H / dy
    ymax = fig.layout.yaxis.range[1]

    def px(x, y):
        return (MARGIN_L + x * sx, MARGIN_T + (ymax - y) * sy)

    tr = [t for t in fig.data if t.mode == "markers+text"][0]
    key2label, node_px, node_r, label_pos, label_box = {}, {}, {}, {}, {}
    for key, x, y, pos, sz in zip(tr.customdata if tr.customdata is not None
                                   else [None] * len(tr.text),
                                   tr.x, tr.y, tr.textposition, tr.marker.size):
        pass
    # labels in order; keys not exposed on marker trace -> recover via layout
    labels = list(tr.text)
    for lbl, x, y, pos, sz in zip(labels, tr.x, tr.y, tr.textposition,
                                  tr.marker.size):
        node_px[lbl] = px(x, y)
        node_r[lbl] = float(sz) / 2.0
        label_pos[lbl] = pos
        w = len(lbl) * CHAR_PX
        cx, cy = node_px[lbl]
        off = MAX_MARKER_R + 3 + LABEL_H / 2
        ly = cy - off if pos == "top center" else cy + off
        label_box[lbl] = (cx - w / 2, ly - LABEL_H / 2,
                          cx + w / 2, ly + LABEL_H / 2)

    edges = []
    for t in fig.data:
        if t.mode != "lines":
            continue
        src, dst = t.customdata[0][0], t.customdata[0][1]
        poly = [px(x, y) for x, y in zip(t.x, t.y)]
        L = sum(math.dist(poly[i], poly[i + 1]) for i in range(len(poly) - 1))
        edges.append({"src": src, "dst": dst, "poly": poly, "len": L})

    sys_labels = {v[2] for v in NEURAL_SYSTEM_NODES.values()}

    def label_of(key):
        if key in key2label:
            return key2label[key]
        for k, v in NEURAL_SYSTEM_NODES.items():
            if k == key:
                return v[2]
        return key

    # crossings
    s_cross, c_cross = [], []
    for i in range(len(edges)):
        for j in range(i + 1, len(edges)):
            e1, e2 = edges[i], edges[j]
            if {e1["src"], e1["dst"]} & {e2["src"], e2["dst"]}:
                continue
            if seg_intersect(e1["poly"][0], e1["poly"][-1],
                             e2["poly"][0], e2["poly"][-1]):
                s_cross.append((e1, e2))
            hit = False
            for a in range(len(e1["poly"]) - 1):
                for b in range(len(e2["poly"]) - 1):
                    if seg_intersect(e1["poly"][a], e1["poly"][a + 1],
                                     e2["poly"][b], e2["poly"][b + 1]):
                        hit = True
                        break
                if hit:
                    break
            if hit:
                c_cross.append((e1, e2))

    through_node, through_label = [], []
    for e in edges:
        for lbl, c in node_px.items():
            if lbl in (e["src"], e["dst"]):
                continue
            r = node_r[lbl]
            for a in range(len(e["poly"]) - 1):
                if (seg_intersect((c[0] - r, c[1]), (c[0] + r, c[1]),
                                  e["poly"][a], e["poly"][a + 1])
                        or seg_intersect((c[0], c[1] - r), (c[0], c[1] + r),
                                         e["poly"][a], e["poly"][a + 1])
                        or pt_seg_dist(c, e["poly"][a], e["poly"][a + 1]) <= r):
                    through_node.append((e, lbl))
                    break
        for lbl, bx in label_box.items():
            if lbl in (e["src"], e["dst"]):
                continue
            x0, y0, x1, y1 = bx
            corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
            for a in range(len(e["poly"]) - 1):
                p, q = e["poly"][a], e["poly"][a + 1]
                inside = (x0 <= p[0] <= x1 and y0 <= p[1] <= y1) or \
                         (x0 <= q[0] <= x1 and y0 <= q[1] <= y1)
                if inside or any(seg_intersect(p, q, corners[k],
                                              corners[(k + 1) % 4])
                                 for k in range(4)):
                    through_label.append((e, lbl))
                    break

    tok_edges = [e for e in edges if e["dst"] in TOKENS or e["src"] in TOKENS]
    sys_edges = [e for e in edges if e not in tok_edges]
    over150 = [e for e in edges if e["len"] > 150]

    if verbose:
        print("=" * 74)
        print(title or "KONSTELASI SAAT INI")
        print("  panel %gx%g  plot %gx%g  px/unit x=%.2f y=%.2f  nodes=%d"
              % (PANEL_W, PANEL_H, PLOT_W, PLOT_H, sx, sy, len(node_px)))
        print("-" * 74)
        for tag, grp in (("token ", tok_edges), ("system", sys_edges)):
            if grp:
                v = [e["len"] for e in grp]
                print("  %s n=%2d mean %6.1f max %6.1f  >150px: %d"
                      % (tag, len(v), sum(v) / len(v), max(v),
                         sum(1 for z in v if z > 150)))
        print("  JUMLAH SISI            : %d" % len(edges))
        print("  SISI > 150px           : %d" % len(over150))
        print("  PERSILANGAN garis ruang: %d" % len(s_cross))
        print("  PERSILANGAN kurva nyata: %d" % len(c_cross))
        print("  SISI lewat simpul lain : %d" % len(through_node))
        print("  SISI lewat kotak label : %d" % len(through_label))
        if c_cross:
            print("  -- pasangan kurva yang berpotongan --")
            for e1, e2 in c_cross:
                print("     %-26s x %-26s" % (e1["src"] + ">" + e1["dst"],
                                              e2["src"] + ">" + e2["dst"]))

    return {"edges": len(edges), "over150": len(over150),
            "cross_straight": len(s_cross), "cross_curve": len(c_cross),
            "through_node": len(through_node), "through_label": len(through_label),
            "edges_obj": edges, "nodes": node_px, "label_box": label_box,
            "node_r": node_r, "sys_labels": sys_labels}


if __name__ == "__main__":
    r = analyze(build(), "KONSTELASI SAAT INI (kunci base-symbol, 12 token LONG)")
    print("=" * 74)
    print("RINGKASAN:", {k: v for k, v in r.items()
                         if not isinstance(v, (list, dict, set))})
