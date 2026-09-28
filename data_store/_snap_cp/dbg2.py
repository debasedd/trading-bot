import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from playwright.sync_api import sync_playwright
import harness as H
from dashboard.layouts.hud_figures import create_neural_net_fig
from dashboard.layouts.hud import create_neural_net_panel


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


panel = create_neural_net_panel()
g = find_graph(panel)
fig = create_neural_net_fig({}, {}, {})
path = H.render_html(fig, g.config, os.path.join(HERE, "dbg.html"))

JS = r"""
() => {
  const gd = document.getElementById('hud-neural-graph');
  const p0 = gd.querySelector('g.scatterlayer g.trace path.js-line');
  const ctm = p0.getScreenCTM();
  const svg = gd.querySelector('svg.main-svg');
  const sctm = svg.getScreenCTM();
  // parent chain transforms
  let el = p0, chain = [];
  while (el && el !== document.body) {
    const t = el.getAttribute && el.getAttribute('transform');
    if (t) chain.push(el.tagName + ' :: ' + t);
    el = el.parentNode;
  }
  const sp0 = p0.getPointAtLength(0);
  const mapped = new DOMPoint(sp0.x, sp0.y).matrixTransform(ctm);
  const rect = p0.getBoundingClientRect();

  // node markers
  const scope = gd.querySelector('.scatterlayer');
  const pts = [...scope.querySelectorAll('path.point')];
  const nodes = pts.slice(0, 4).map(e => {
    const r = e.getBoundingClientRect();
    return [e.getAttribute('transform'), r.x, r.y, r.width, r.height];
  });
  return {
    ctm: [ctm.a, ctm.b, ctm.c, ctm.d, ctm.e, ctm.f],
    sctm: [sctm.a, sctm.b, sctm.c, sctm.d, sctm.e, sctm.f],
    chain: chain,
    sp0: [sp0.x, sp0.y],
    mapped: [mapped.x, mapped.y],
    rect: [rect.x, rect.y, rect.width, rect.height],
    totalLength: p0.getTotalLength(),
    nodes: nodes,
    gplot: (gd.querySelector('g.plot') || {}).getAttribute
        ? gd.querySelector('g.plot').getAttribute('transform') : 'NOGPLOT',
    gcart: gd.querySelectorAll('g').length
  };
}
"""

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1920, "height": 900})
    pg.goto("file:///" + path.replace("\\", "/"))
    pg.wait_for_timeout(2200)
    import json
    d = pg.evaluate(JS)
    print(json.dumps(d, indent=1))
    b.close()
