import json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import data_store.constellation_measure as cm
import plotly.offline as po
from playwright.sync_api import sync_playwright
from dashboard.layouts.hud_figures import create_neural_net_fig, TOKEN_SLOTS, _TOKEN_SLOT_REGISTRY

toks = list(_TOKEN_SLOT_REGISTRY)[:len(TOKEN_SLOTS)]
sig = {t: {"direction":"LONG","confidence":1.0,"price_change":0.004} for t in toks}
fig = create_neural_net_fig(signal_map=sig,
    activity_map={k:1.0 for k in ("analysis_agent","decision_agent","execution_agent","news_agent")},
    flow_map={k:1.0 for k in ("l2Book","allMids","candle","trades")})
html = cm.HTML % {"css":cm.PANEL_CSS,"plotly":po.get_plotlyjs(),"fig":json.dumps(fig.to_plotly_json())}
tmp = cm.ROOT/"data_store"/"_dbg.html"; tmp.write_text(html, encoding="utf-8")
JS = """() => {
  const gd = document.getElementById('hud-neural-graph');
  return {
    fl: {
      xrange: gd._fullLayout.xaxis.range.slice(),
      yrange: gd._fullLayout.yaxis.range.slice(),
      margin: {l:gd._fullLayout.margin.l, r:gd._fullLayout.margin.r, t:gd._fullLayout.margin.t, b:gd._fullLayout.margin.b},
      size: gd._fullLayout._size,
      width: gd._fullLayout.width, height: gd._fullLayout.height,
      xdom: gd._fullLayout.xaxis.domain.slice(),
      ydom: gd._fullLayout.yaxis.domain.slice(),
    },
    rect: (()=>{const r=gd.getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height};})(),
  };
}"""
with sync_playwright() as p:
    b=p.chromium.launch(); pg=b.new_page(viewport={"width":1920,"height":1100})
    pg.goto(tmp.as_uri())
    pg.wait_for_function("()=>{const g=document.getElementById('hud-neural-graph');return g&&g.data&&g._fullLayout;}",timeout=20000)
    pg.wait_for_timeout(300)
    info=pg.evaluate(JS); dom=pg.evaluate(cm.PROBE); b.close()
print(json.dumps(info, indent=1))
tr=[t for t in fig.data if t.mode=="markers+text"][0]
# empirical px/unit from two far-apart tokens in the SAME row
by={n["label"]:n for n in dom["nodes"]}
row=[(n,l) for n,l in zip(tr.text,tr.y) if abs(l-4.0)<0.01]
a=by[row[0][0]]; b2=by[row[-1][0]]
x1=float(row[0][1] and tr.x[tr.text.index(row[0][0])])
x2=float(tr.x[tr.text.index(row[-1][0])])
print("empirical ppx:", (b2["mx"]-a["mx"])/(x2-x1), " dx", x1, x2, a["mx"], b2["mx"])
r1=[(n,l) for n,l in zip(tr.text,tr.y) if abs(l-1.2)<0.01]
c=by[r1[0][0]]; d=by[r1[-1][0]]
y1=float(tr.y[tr.text.index(r1[0][0])]); y2=float(tr.y[tr.text.index(r1[-1][0])])
print("empirical ppy:", (c["my"]-d["my"])/(y2-y1), " y1,y2", y1, y2, c["my"], d["my"])
