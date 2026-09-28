"""
validate_model.py — Buktikan model analitik constellation_search.py cocok dengan
yang benar-benar digambar browser.

Kalau model ini meleset, pencarian koordinat yang/CBD dengan model ini tidak
berarti. Jadi: render figure yang SEDANG BERJALAN, ukur di browser, lalu hitung
ulang dengan model analitik, lalu bandingkan.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import data_store.constellation_measure as cm      # noqa: E402
import data_store.constellation_search as cs      # noqa: E402
from dashboard.layouts.hud_figures import (         # noqa: E402
    NEURAL_SYSTEM_NODES, NEURAL_SYSTEM_EDGES, TOKEN_SLOTS, _TOKEN_SLOT_REGISTRY,
    create_neural_net_fig,
)

toks = list(_TOKEN_SLOT_REGISTRY)[:len(TOKEN_SLOTS)]
sig = {t: {"direction": "LONG", "confidence": 1.0, "price_change": 0.004}
       for t in toks}
fig = create_neural_net_fig(
    signal_map=sig,
    activity_map={k: 1.0 for k in ("analysis_agent", "decision_agent",
                                   "execution_agent", "news_agent")},
    flow_map={k: 1.0 for k in ("l2Book", "allMids", "candle", "trades")},
)
l2k = {v[2]: k for k, v in NEURAL_SYSTEM_NODES.items()}

VW = 1920
real = cm.measure(fig, VW, l2k)

tr = [t for t in fig.data if t.mode == "markers+text"][0]
# Kunci SELALU label. Versi pertama meng-key sistem dengan key internal
# (CORE_ENGINE) lalu menambahkan ulang dengan label (SIGNAL), jadi setiap
# simpul sistem terhitung dua kali -> 32 "node", termasuk FLOW kembar.
# radius marker ikut dihitung: Plotly menaruh label di luar tepi marker,
# jadi radius salah = kotak label salah.
layout = {lab: (x, y, lab, lab not in l2k, r / 2.0)
          for x, y, lab, r in zip(tr.x, tr.y, tr.text, tr.marker.size)}
assert len(layout) == len(tr.text), (len(layout), len(tr.text))

# customdata memakai KUNCI INTERNAL (CORE_ENGINE), layout memakai LABEL
# (SIGNAL). Tanpa pemetaan ini tiap sisi diuji terhadap entri yang salah.
k2l = {v: k for k, v in l2k.items()}
edges = [(k2l.get(t.customdata[0][0], t.customdata[0][0]),
          k2l.get(t.customdata[0][1], t.customdata[0][1]))
         for t in fig.data if t.mode == "lines"]
assert all(a in layout and d in layout for a, d in edges), "edge tanpa node"
model = cs.evaluate(layout, edges, fig.layout.xaxis.range,
                    fig.layout.yaxis.range,
                    panel_w=real["plot_area"][0])

KEYS = ("node_count", "edge_count", "worst_label_gap", "worst_label_pair",
        "label_violations", "worst_marker_gap", "worst_marker_pair",
        "marker_violations", "edge_len_mean", "edge_len_max", "edges_over_150",
        "crossings", "edge_through_node", "edge_through_label")

print(f"{'key':22} {'BROWSER':>22}   {'MODEL':>22}   delta")
bad = 0
for k in KEYS:
    a, b = real[k], model[k]
    da = "" if not isinstance(a, (int, float)) or not isinstance(b, (int, float)) \
        else f"{b - a:+.2f}"
    flag = ""
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if abs(b - a) > max(2.5, abs(a) * 0.06):
            flag = "  <-- BEDA"
            bad += 1
    print(f"{k:22} {str(a):>22}   {str(b):>22}   {da}{flag}")

print()
print("browser thruN:", [f"{a} x {b} ({d})" for a, b, d in
                          real["edge_through_node_detail"][:12]])
print("model   thruN:", [f"{a} x {b} ({d})" for a, b, d in
                          model["edge_through_node_detail"][:12]])
print("browser thruL:", [f"{a} x {b}" for a, b in
                         real["edge_through_label_detail"][:12]])
print("model   thruL:", [f"{a} x {b}" for a, b, d in
                         model["edge_through_label_detail"][:12]])
print()
print("MISMATCHES:", bad, "of", len(KEYS))
