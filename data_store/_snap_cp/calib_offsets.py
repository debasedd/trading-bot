"""Calibrate Plotly's marker->text offset as a function of marker diameter.

The offset is not a constant: it depends on marker size, and that
dependency is what makes eyeballed "it's fine" wrong. This measures the
real offset for a sweep of diameters in both text positions and fits it.
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
import plotly.graph_objects as go

SIZES = [12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30]
TEXTS = ["SIGNAL", "BTC", "NEAR", "DOGE", "HYPE", "RF ML", "MACRO", "COIN", "YFI", "XRP"]


def build():
    xs, ys, ss, tt, tp = [], [], [], [], []
    n = 0
    for d in SIZES:
        for pos in ("top center", "bottom center", "middle center"):
            for t in TEXTS:
                xs.append(1.0 + (n % 5) * 4)
                ys.append(1.0 + (n // 5) * 2)
                ss.append(d)
                tt.append(t)
                tp.append(pos)
                n += 1
    f = go.Figure()
    f.add_trace(go.Scatter(x=xs, y=ys, mode="markers+text", marker=dict(
        size=ss, color="#000", line=dict(color="#000", width=1.5)),
        text=tt, textposition=tp,
        textfont=dict(size=10, family="'Share Tech Mono', monospace"),
        hoverinfo="skip", showlegend=False))
    f.update_layout(template="none", width=1000, height=900,
                    margin=dict(l=14, r=14, t=10, b=16),
                    xaxis=dict(visible=False, range=[0, 30]),
                    yaxis=dict(visible=False, range=[0, 30]))
    return f


JS = r"""
() => {
  const gd = document.getElementById('hud-neural-graph');
  if (!gd || !gd._fullLayout) return {error:'nope'};
  const scope = gd.querySelector('.scatterlayer');
  const pts = [...scope.querySelectorAll('path.point')];
  const txts = [...scope.querySelectorAll('text')];
  const out = [];
  const n = Math.min(pts.length, txts.length);
  for (let i = 0; i < n; i++) {
    const pb = pts[i].getBoundingClientRect();
    const tb = txts[i].getBoundingClientRect();
    out.push({
      d: pb.width, md: pb.height,
      cy: pb.y + pb.height/2, r: pb.width/2,
      ty0: tb.y, ty1: tb.y + tb.height, tcy: tb.y + tb.height/2,
      tw: tb.width, th: tb.height,
      label: (txts[i].textContent||'').trim()
    });
  }
  return out;
}
"""

def main():
    fig = build()
    out_path = os.path.join(HERE, "offsets.html")
    import plotly.io as pio
    div = pio.to_html(fig, full_html=False, include_plotlyjs="cdn",
                      config={"displayModeBar": False}, div_id="hud-neural-graph")
    html = ('<!doctype html><html><head><meta charset="utf-8">'
            '<link rel="stylesheet" href="file:///'
            + os.path.join(ROOT, "dashboard", "assets", "style.css").replace("\\", "/")
            + '"></head><body><div style="width:1000px">' + div
            + '</div><script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>'
              '</body></html>')
    open(out_path, "w", encoding="utf-8").write(html)

    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1100, "height": 1000})
        pg.goto("file:///" + out_path.replace("\\", "/"))
        pg.wait_for_timeout(2500)
        d = pg.evaluate(JS)
        b.close()

    if isinstance(d, dict):
        print("PROBE ERROR", d)
        return

    rows = []
    i = 0
    for size in SIZES:
        for pos in ("top center", "bottom center", "middle center"):
            for t in TEXTS:
                r = d[i]
                i += 1
                rows.append(dict(size=size, pos=pos, text=t,
                                 got_d=round(r["d"], 2), got_h=round(r["md"], 2),
                                 tw=round(r["tw"], 2), th=round(r["th"], 2),
                                 gap=round(r["tcy"] - r["cy"], 2),
                                 near_edge=round(
                                     min(r["ty0"], r["ty1"]) if False else 0, 2)))
    json.dump(rows, open(os.path.join(HERE, "offsets.json"), "w"), indent=1)

    print("%-5s %-16s %-8s %-8s %-8s" % ("size", "pos", "gap(t-cy)", "textW", "textH"))
    seen = set()
    for r in rows:
        k = (r["size"], r["pos"])
        if k in seen:
            continue
        seen.add(k)
        print("%-5d %-16s %-8.2f %-8.2f %-8.2f" %
              (r["size"], r["pos"], r["gap"], r["tw"], r["th"]))

    # text width per string
    print()
    w = {}
    for r in rows:
        w.setdefault(r["text"], r["tw"])
    for t, ww in w.items():
        print("width %-8s %.3f  per-char %.4f" % (t, ww, ww / len(t)))


if __name__ == "__main__":
    main()
