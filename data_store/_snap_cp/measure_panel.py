import json, os, sys
sys.path.insert(0, os.path.abspath("."))
from playwright.sync_api import sync_playwright
from dashboard.layouts.hud import create_neural_net_panel
import plotly.io as pio

panel = create_neural_net_panel()
def find_graph(c):
    if getattr(c, "id", None) == "hud-neural-graph":
        return c
    kids = getattr(c, "children", None) or []
    if not isinstance(kids, (list, tuple)):
        kids = [kids]
    for k in kids:
        if k is None: continue
        r = find_graph(k)
        if r is not None: return r
    return None
g = find_graph(panel)
cfg = g.config

# Mirror real DOM: .hud-container > .hud-grid > .hud-panel.span-3.zone3-cell > body(pad 2) > graph(h 230px)
plot_div = pio.to_html(g.figure, full_html=False, include_plotlyjs="cdn", config=cfg, div_id="hud-neural-graph")
plot_div = plot_div.replace('<div id="hud-neural-graph"', '<div id="hud-neural-graph" style="height:230px;width:100%"')
html = f"""<!doctype html><html><head><meta charset="utf-8">
<link rel="stylesheet" href="file:///{os.path.abspath('dashboard/assets/style.css').replace(chr(92),'/')}">
</head><body>
<div class="hud-container"><div class="hud-grid">
<div class="hud-panel span-3 zone3-cell">
  <div class="hud-panel-header"><span>SNIPE NEURAL NET</span><span class="header-tag">CONSTELLATION ENGINE</span></div>
  <div class="hud-panel-body" style="padding:2px">{plot_div}</div>
</div></div></div>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
</body></html>"""

here = os.path.dirname(os.path.abspath(__file__))
out = os.path.join(here, "panel_probe.html")
open(out, "w", encoding="utf-8").write(html)

JS = """() => {
  const gd = document.getElementById('hud-neural-graph');
  const r = gd.getBoundingClientRect();
  const svg = gd.querySelector('svg.main-svg');
  const ir = svg.getBoundingClientRect();
  const L = gd._fullLayout;
  return {w:r.width,h:r.height,sw:ir.width,sh:ir.height,margin:L.margin,
          xr:L.xaxis.range, yr:L.yaxis.range,
          ntext: gd.querySelectorAll('.scatterlayer text').length,
          npath: gd.querySelectorAll('path.js-line').length};
}"""
rows=[]
with sync_playwright() as p:
    b=p.chromium.launch()
    for vw in (1280,1366,1440,1600,1920,2560):
        pg=b.new_page(viewport={"width":vw,"height":900})
        pg.goto("file:///"+out.replace("\\","/"))
        pg.wait_for_timeout(2200)
        d=pg.evaluate(JS); m=d["margin"]
        pw=d["sw"]-m["l"]-m["r"]; ph=d["sh"]-m["t"]-m["b"]
        rows.append(dict(vw=vw,graphW=round(d["w"],2),graphH=round(d["h"],2),
            svgW=round(d["sw"],2),svgH=round(d["sh"],2),
            plotW=round(pw,2),plotH=round(ph,2),
            pxuX=round(pw/(d["xr"][1]-d["xr"][0]),4),pxuY=round(ph/(d["yr"][1]-d["yr"][0]),4),
            ntext=d["ntext"],npath=d["npath"]))
        pg.close()
    b.close()
print(json.dumps(rows,indent=1))
