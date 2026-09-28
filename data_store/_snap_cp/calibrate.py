"""Calibrate an analytic geometry model against the real browser.

The browser is the oracle. This script measures the constants an analytic
predictor needs (label widths per string, Plotly's text offset, marker
radii) and then checks the predictor's node boxes against the real DOM
boxes to sub-pixel accuracy. Only a predictor that reproduces the browser
may be used to search a candidate space.
"""
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from playwright.sync_api import sync_playwright
import harness as H

import dashboard.layouts.hud_figures as HF
from dashboard.layouts.hud_figures import create_neural_net_fig
from dashboard.layouts.hud import create_neural_net_panel

TEXTPOS_JS = r"""
() => {
  const gd = document.getElementById('hud-neural-graph');
  const scope = gd.querySelector('.scatterlayer');
  const pts = [...scope.querySelectorAll('path.point')];
  const txts = [...scope.querySelectorAll('text')];
  const out = [];
  const n = Math.min(pts.length, txts.length);
  for (let i = 0; i < n; i++) {
    const p = pts[i], t = txts[i];
    const tr = p.getAttribute('transform') || '';
    const m = tr.match(/translate\(([-\d.e]+),\s*([-\d.e]+)\)/);
    const pb = p.getBoundingClientRect();
    const tb = t.getBoundingClientRect();
    out.push({
      label: (t.textContent || '').trim(),
      attr: tr,
      ax: m ? parseFloat(m[1]) : null,
      ay: m ? parseFloat(m[2]) : null,
      mc: [pb.x + pb.width / 2, pb.y + pb.height / 2],
      md: [pb.width, pb.height],
      l: [tb.x, tb.y, tb.x + tb.width, tb.y + tb.height],
    });
  }
  return out;
}
"""


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


TOKENS = ["BTC", "ETH", "SOL", "XRP", "BNB", "DOGE", "NEAR", "ONDO", "HYPE", "ZEC", "XPL", "ENA"]
LABELS = ["SIGNAL", "RISK", "RF ML", "SENT", "FLOW", "MACRO", "TECH", "EXEC", "YFI", "COIN"] + TOKENS


def main():
    panel = create_neural_net_panel()
    g = find_graph(panel)
    fig = create_neural_net_fig(
        {k: {"direction": "LONG", "confidence": 0.8, "price_change": 0.01} for k in TOKENS},
        {k: 0.7 for k in ("execution_agent", "decision_agent", "analysis_agent", "news_agent")},
        {k: 0.7 for k in ("allMids", "l2Book", "candle", "trades")},
    )
    path = H.render_html(fig, g.config, os.path.join(HERE, "calib.html"))

    rows = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        for vw in (1280, 1366, 1920):
            pg = b.new_page(viewport={"width": vw, "height": 900})
            pg.goto("file:///" + path.replace("\\", "/"))
            pg.wait_for_timeout(2200)
            data = pg.evaluate(TEXTPOS_JS)
            probe = pg.evaluate(H.PROBE)
            m = probe["margin"]
            svg = probe["svg"]
            pw = svg[2] - m["l"] - m["r"]
            ph = svg[3] - m["t"] - m["b"]
            xr, yr = probe["xRange"], probe["yRange"]
            pxu = pw / (xr[1] - xr[0])
            pxuY = ph / (yr[1] - yr[0])
            # The marker's SVG `transform` is ALREADY in pixels measured from
            # the plot-area origin, so the origin must be solved for, not
            # guessed from the margin.
            ax0, ay0 = data[0]["ax"], data[0]["ay"]
            origin_x = data[0]["mc"][0] - (ax0 - 0.0)
            origin_y = data[0]["mc"][1] - ay0
            for d in data:
                pred_cx = origin_x + d["ax"]
                pred_cy = origin_y + d["ay"]
                lw = d["l"][2] - d["l"][0]
                lh = d["l"][3] - d["l"][1]
                rows.append(dict(
                    vw=vw, label=d["label"],
                    ax=d["ax"], ay=d["ay"],
                    err_x=round(pred_cx - d["mc"][0], 3),
                    err_y=round(pred_cy - d["mc"][1], 3),
                    marker_d=round(d["md"][0], 3),
                    label_w=round(lw, 3), label_h=round(lh, 3),
                    gap_to_label_above=round(d["mc"][1] - d["md"][1] / 2 - d["l"][3], 3),
                    gap_to_label_below=round(d["l"][1] - (d["mc"][1] + d["md"][1] / 2), 3),
                    label_dx=round((d["l"][0] + d["l"][2]) / 2 - d["mc"][0], 3),
                ))
            pg.close()
        b.close()

    with open(os.path.join(HERE, "calib.json"), "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=1)

    print("coord-model error: max|dx| = %.3f  max|dy| = %.3f"
          % (max(abs(r["err_x"]) for r in rows), max(abs(r["err_y"]) for r in rows)))
    print()
    seen = {}
    for r in rows:
        k = (r["label"], r["vw"])
        seen[k] = r
    print("%-8s %-6s %-8s %-8s %-9s %-9s" % ("label", "vw", "mdiam", "labW", "gapUp", "gapDn"))
    for lab in LABELS:
        for vw in (1280, 1920):
            r = seen.get((lab, vw))
            if r:
                print("%-8s %-6d %-8.2f %-8.2f %-9.2f %-9.2f"
                      % (lab, vw, r["marker_d"], r["label_w"],
                         r["gap_to_label_above"], r["gap_to_label_below"]))


if __name__ == "__main__":
    main()
