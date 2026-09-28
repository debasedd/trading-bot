"""Self-test for the geometry harness.

A measurement tool that cannot be shown to be right is not a measurement.
Each case below is constructed so the answer is known by hand, and the
harness is only trusted if it returns that answer.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import harness as H


def mknode(label, cx, cy, d=12.0, lw=None, lh=12.0):
    lw = lw if lw is not None else 6.0 * len(label)
    return {
        "label": label,
        "m": [cx - d / 2, cy - d / 2, cx + d / 2, cy + d / 2],
        "mc": [cx, cy],
        "md": [d, d],
        "l": [cx - lw / 2, cy + d / 2, cx + lw / 2, cy + d / 2 + lh],
        "lw": lw, "lh": lh, "fs": "10px", "family": "mono",
    }


def mkprobe(nodes, edges, plot_w=440.0, plot_h=230.0):
    return {
        "graph": [0, 0, plot_w + 28, plot_h + 26],
        "svg": [0, 0, plot_w + 28, plot_h + 26],
        "margin": {"l": 14, "r": 14, "t": 10, "b": 16},
        "xRange": [0, 22], "yRange": [0, 11.5],
        "nodes": nodes, "edges": edges,
    }


def line(a, b, src, dst, n=32):
    pts = [[a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n]
           for i in range(n + 1)]
    return {"src": src, "dst": dst, "flow": 0.0, "pts": pts, "w": 1.0}


fails = []


def check(name, got, want):
    ok = got == want
    print(("  PASS " if ok else "  FAIL ") + name
          + "  got=%r want=%r" % (got, want))
    if not ok:
        fails.append(name)


print("case 1: single X crossing between two disjoint edges")
n = [mknode("A", 50, 100), mknode("B", 250, 100),
     mknode("C", 50, 200), mknode("D", 250, 200)]
e = [line((50, 100), (250, 200), "A", "D"), line((50, 200), (250, 100), "C", "B")]
r = H.analyse(mkprobe(n, e))
check("crossings", r["edge_crossings"], 1)

print("case 2: two disjoint parallel edges do NOT cross")
n = [mknode("A", 50, 100), mknode("B", 250, 100),
     mknode("C", 50, 150), mknode("D", 250, 150)]
e = [line((50, 100), (250, 100), "A", "B"), line((50, 150), (250, 150), "C", "D")]
r = H.analyse(mkprobe(n, e))
check("crossings", r["edge_crossings"], 0)

print("case 3: shared-endpoint V does NOT count as crossing")
n = [mknode("A", 50, 100), mknode("B", 250, 200), mknode("C", 250, 100)]
e = [line((50, 100), (250, 200), "A", "B"), line((50, 100), (250, 100), "A", "C")]
r = H.analyse(mkprobe(n, e))
check("crossings", r["edge_crossings"], 0)

print("case 4: edge through a marker it does not connect to")
n = [mknode("A", 50, 100), mknode("B", 300, 100), mknode("X", 175, 100, d=20)]
e = [line((50, 100), (300, 100), "A", "B")]
r = H.analyse(mkprobe(n, e))
check("thru_node", len(r["edge_through_node"]), 1)

print("case 5: edge through a LABEL box (and the near-invisible marker there)")
n = [mknode("A", 50, 100), mknode("B", 50, 300), mknode("MID", 50, 200, d=1, lw=40, lh=12)]
e = [line((50, 100), (50, 300), "A", "B")]
r = H.analyse(mkprobe(n, e))
check("thru_label", len(r["edge_through_label"]), 1)
# the 1px MID marker sits at y=200 on the segment, so a through-node hit is
# CORRECT here. The point of the case is that the label is caught too.
check("thru_node", len(r["edge_through_node"]), 1)

print("case 5b: label box hit WITHOUT a marker hit (label offset sideways)")
n = [mknode("A", 50, 100), mknode("B", 50, 300),
     mknode("MID", 30, 200, d=8, lw=50, lh=12)]
e = [line((50, 100), (50, 300), "A", "B")]
r = H.analyse(mkprobe(n, e))
check("thru_label", len(r["edge_through_label"]), 1)
check("thru_node", len(r["edge_through_node"]), 0)

print("case 6: label gap below threshold is flagged, above is not")
n = [mknode("AAAA", 100, 100, lw=30, lh=12), mknode("BBBB", 100, 118, lw=30, lh=12)]
r = H.analyse(mkprobe(n, []))
check("label_violations", r["label_violations"], 1)
n = [mknode("AAAA", 100, 100, lw=30, lh=12), mknode("BBBB", 100, 200, lw=30, lh=12)]
r = H.analyse(mkprobe(n, []))
check("label_violations(far)", r["label_violations"], 0)

print("case 7: marker gap uses EDGES of circles, not centres")
n = [mknode("AA", 100, 100, d=20), mknode("BB", 126, 100, d=20)]
r = H.analyse(mkprobe(n, []))
check("marker_gap_min", r["marker_gap_min"], 6.0)
check("marker_violations", r["marker_violations"], 0)
n = [mknode("AA", 100, 100, d=20), mknode("BB", 125, 100, d=20)]
r = H.analyse(mkprobe(n, []))
check("marker_violations(touching)", r["marker_violations"], 1)

print("case 8: edge length measured in px, not units")
n = [mknode("A", 0, 0), mknode("B", 300, 0)]
r = H.analyse(mkprobe(n, [line((0, 0), (300, 0), "A", "B")]))
check("edge_len_max", r["edge_len_max"], 300.0)
check("edges_over_150", r["edges_over_150"], 1)

print("case 9: key->label map resolves endpoints (no self-hit)")
n = [mknode("SIGNAL", 50, 100), mknode("RISK", 250, 100)]
e = [{"src": "CORE_ENGINE", "dst": "DECISION_GATE", "flow": 0.0,
      "pts": [[50 + 200 * i / 32, 100] for i in range(33)], "w": 1.0}]
r = H.analyse(mkprobe(n, e), key_to_label={"CORE_ENGINE": "SIGNAL",
                                           "DECISION_GATE": "RISK"})
check("thru_node", len(r["edge_through_node"]), 0)
check("endpoint_mismatch", len(r["endpoint_mismatch"]), 0)

print("case 10: without the map the same edge falsely self-hits (regression guard)")
r = H.analyse(mkprobe(n, e))
check("thru_node(no map)", len(r["edge_through_node"]), 2)

print()
if fails:
    print("HARNESS SELF-TEST FAILED: %s" % fails)
    sys.exit(1)
print("HARNESS SELF-TEST: all cases pass")
