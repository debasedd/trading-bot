"""Prove the analytic model matches the browser on the real current layout.

If these two disagree, every number this file's sibling search produces is
fiction, so this check gates the whole search.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from playwright.sync_api import sync_playwright
import harness as H
import model as M

import dashboard.layouts.hud_figures as HF
from dashboard.layouts.hud_figures import create_neural_net_fig
from dashboard.layouts.hud import create_neural_net_panel

TOKENS = ["BTC", "ETH", "SOL", "XRP", "BNB", "DOGE", "NEAR", "ONDO", "HYPE", "ZEC", "XPL", "ENA"]
SIGNALS = {k: {"direction": "LONG", "confidence": 0.8, "price_change": 0.01} for k in TOKENS}
ACT = {k: 0.7 for k in ("execution_agent", "decision_agent", "analysis_agent", "news_agent")}
FLOW = {k: 0.7 for k in ("allMids", "l2Book", "candle", "trades")}


def find_graph(c):
    if getattr(c, "id", None) == "hud-neural-graph":
        return c
    kids = getattr(c, "children", None) or []
    if not isinstance(kids, (list, tuple)):
        kids = [kids]
    for k in kids:
        if k is None:
            continue
        r = find_graph(k)
        if r is not None:
            return r
    return None


def main():
    panel = create_neural_net_panel()
    g = find_graph(panel)
    k2l = {k: v[2] for k, v in HF.NEURAL_SYSTEM_NODES.items()}

    fig = create_neural_net_fig(SIGNALS, ACT, FLOW)
    path = H.render_html(fig, g.config, os.path.join(HERE, "validate.html"))

    # Node positions and the edge list are read back from the RENDERED figure
    # (via the data coordinates the browser actually used), not re-derived
    # from the code tables. Re-deriving them means guessing at whatever
    # hub-selection rule is in force this minute, and the guess is what
    # would then be validated -- a model checked against itself.
    rows = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        for vw in (1280, 1366, 1920, 2560):
            pg = b.new_page(viewport={"width": vw, "height": 900})
            pg.goto("file:///" + path.replace("\\", "/"))
            pg.wait_for_timeout(2000)
            probe = pg.evaluate(H.PROBE)
            pg.close()
            real = H.analyse(probe, key_to_label=k2l)

            nodes = {}
            for n in probe["nodes"]:
                # recover data coords from the marker's own screen centre
                pxux = M.pxu_x(real["plot_w"])
                px0, py0, px1, py1 = probe["plot"]
                dx = (n["mc"][0] - px0) / pxux + M.XRANGE[0]
                dy = M.YRANGE[1] - (n["mc"][1] - py0) / M.PXU_Y
                pos = "top center"
                if n["l"][1] > n["mc"][1]:
                    pos = "bottom center"
                # invert the model to recover the diameter the browser used
                w = n["l"][2] - n["l"][0]
                nodes[n["label"]] = (round(dx, 4), round(dy, 4), n["label"],
                                     pos, n["md"][0])
            # Node dict is keyed by LABEL. Edge endpoints from the browser
            # carry internal keys, so translate them to labels here.
            edges = []
            for e in probe["edges"]:
                edges.append((e["sl"], e["dl"]))
            missing = {x for ed in edges for x in ed if x not in nodes}
            if missing:
                print("  !! edge endpoints not in node set:", missing)
            pred = M.evaluate(nodes, edges, real["plot_w"])

            rows.append((vw, real, pred, probe))

    hdr = ("%-6s %-20s %-9s %-9s | %-20s %-9s %-9s" %
           ("vw", "metric", "browser", "model", "metric2", "browser", "model"))
    print(hdr)
    print("-" * len(hdr))
    worst = 0.0
    for vw, real, pred, probe in rows:
        pairs = [
            ("edge_count", real["edge_count"], pred["edge_count"]),
            ("label_gap_min", real["label_gap_min"], pred["label_gap_min"]),
            ("marker_gap_min", real["marker_gap_min"], pred["marker_gap_min"]),
            ("edge_len_max", real["edge_len_max"], pred["edge_len_max"]),
            ("edges_over_150", real["edges_over_150"], pred["edges_over_150"]),
            ("edge_crossings", real["edge_crossings"], pred["crossings"]),
            ("thru_node", len(real["edge_through_node"]), len(pred["thru_node"])),
            ("thru_label", len(real["edge_through_label"]), len(pred["thru_label"])),
        ]
        for i in range(0, len(pairs), 2):
            a = pairs[i]
            c = pairs[i + 1] if i + 1 < len(pairs) else None
            print("%-6s %-20s %-9s %-9s | %-20s %-9s %-9s" %
                  (vw if i == 0 else "", a[0], a[1], a[2],
                   c[0] if c else "", c[1] if c else "", c[2] if c else ""))
            for mname, rv, pv in (a, c):
                if mname in ("edge_count", "edges_over_150", "edge_crossings",
                             "thru_node", "thru_label"):
                    if rv != pv:
                        worst = max(worst, 1.0)
                elif isinstance(rv, float):
                    worst = max(worst, abs(rv - pv))
        print("   thru_node browser:", sorted(real["edge_through_node"]))
        print("            model  :", sorted(pred["thru_node"]))
        print("   thru_lbl  browser:", sorted(real["edge_through_label"]))
        print("            model  :", sorted(pred["thru_label"]))
        print("   clip br   browser:", [round(v, 1) for v in probe["plot"]],
              " plot_h=%.1f" % real["plot_h"])
        print("   clipped   browser:", real.get("label_clipped"))
        print("            model  :", pred["clipped"])
        print()
    print("worst disagreement: %.2f   (mismatch: %s)"
          % (worst, "YES" if worst >= 1.0 else "no"))


if __name__ == "__main__":
    main()
