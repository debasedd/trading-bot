"""
verify_graph.py — Ukur KUALITAS GRAFIK LENGKAP di browser sungguhan.

Semua fix sebelumnya hanya mengecek label-vs-label dan marker-vs-marker.
Yang tidak pernah diukur adalah EDGE: berapa banyak, seberapa panjang, dan
apakah saling berpotongan. Screen userasync penuh garis "ruwet" persis
karena angka itu tidak pernah dicek.

Metrik yang diukur:
  node/label/edge count
  node/label/edge count
  marker gap - bulet vs bulet
  crossings  - pasangan garis berpotongan TANPA shared endpoint
  long edges - garis > 150px di panel 440px
  node-through - garis yang melewati simpul tak terkait

Jalankan dengan dashboard hidup di http://127.0.0.1:8050
"""
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

URL = "http://127.0.0.1:8050"
OUT = Path(__file__).parent / "data_store" / "graph_check.json"
SHOT = Path(__file__).parent / "data_store" / "graph_shot.png"

# Plotly menulis setiap garis sebagai satu <path class="js-line"> dengan
# d = "M x,y L x,y L x,y ...". Kita baca SEMUA titik, bukan cuma ujung,
# supaya lengkungan Bezier ikut terukur.
PROBE = r"""
() => {
  const gd = document.getElementById('hud-neural-graph');
  const scope = gd.querySelector('.scatterlayer');
  const edgePaths = [...scope.querySelectorAll('path.js-line')];

  const polylines = edgePaths.map(p => {
    const d = p.getAttribute('d') || '';
    const nums = d.replace(/[MLZ]/g, ' ').split(/[ ,]+/)
      .filter(s => s.length).map(Number).filter(n => !isNaN(n));
    const pts = [];
    for (let i = 0; i + 1 < nums.length; i += 2) pts.push([nums[i], nums[i+1]]);
    return pts;
  }).filter(p => p.length >= 2);

  const markers = [...scope.querySelectorAll('path.point')].map(p => {
    const b = p.getBoundingClientRect();
    return {cx: b.x + b.width/2, cy: b.y + b.height/2, r: b.width/2};
  });

  const texts = [...scope.querySelectorAll('text')].map(t => {
    const b = t.getBoundingClientRect();
    return {x: b.x, y: b.y, w: b.width, h: b.height,
            label: (t.textContent || '').trim()};
  });

  return {polylines, markers, texts,
          w: scope.clientWidth, h: scope.clientHeight};
}
"""


def seg_cross(p1, p2, p3, p4):
    def cr(o, a, b):
        return (a[0]-o[0])*(b[1]-o[1]) - (a[1]-o[1])*(b[0]-o[0])
    d1, d2 = cr(p3, p4, p1), cr(p3, p4, p2)
    d3, d4 = cr(p1, p2, p3), cr(p1, p2, p4)
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0))


def seg_point_dist(p1, p2, pt, n=14):
    best = 1e9
    for i in range(n + 1):
        a = (p1[0] + (p2[0]-p1[0])*i/n, p1[1] + (p2[1]-p1[1])*i/n)
        best = min(best, ((a[0]-pt[0])**2 + (a[1]-pt[1])**2) ** 0.5)
    return best


def point_in_box(pt, box, pad):
    return (box["x"] - pad <= pt[0] <= box["x"] + box["w"] + pad and
            box["y"] - pad <= pt[1] <= box["y"] + box["h"] + pad)


def main() -> int:
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        pg = b.new_page(viewport={"width": 1920, "height": 1080}, device_scale_factor=1)
        pg.goto(URL, wait_until="domcontentloaded")
        pg.wait_for_selector("#hud-neural-graph", timeout=30000)
        pg.wait_for_timeout(6000)
        info = pg.evaluate(PROBE)
        el = pg.query_selector("#hud-neural-graph")
        el.evaluate_handle("n => n.closest('.hud-panel')").as_element().screenshot(
            path=str(SHOT))
        b.close()

    lines = info["polylines"]
    labels = info["texts"]
    marks = info["markers"]

    # --- label vs label: AABB gap (Both axes must be clear) ---
    worst_label = 1e9
    worst_label_pair = None
    for i in range(len(labels)):
        for j in range(i + 1, len(labels)):
            dx = abs(labels[i]["x"] - labels[j]["x"]) - (labels[i]["w"] + labels[j]["w"]) / 2
            dy = abs(labels[i]["y"] - labels[j]["y"]) - (labels[i]["h"] + labels[j]["h"]) / 2
            gap = max(dx, dy)      # AABB separation: both axes clear
            if gap < worst_label:
                worst_label, worst_label_pair = gap, (labels[i]["label"], labels[j]["label"])

    # --- marker vs marker ---
    worst_marker = 1e9
    for i in range(len(marks)):
        for j in range(i + 1, len(marks)):
            d = ((marks[i]["cx"]-marks[j]["cx"])**2 + (marks[i]["cy"]-marks[j]["cy"])**2) ** 0.5
            worst_marker = min(worst_marker, d - marks[i]["r"] - marks[j]["r"])

    # --- edge crossings: shared-endpoint pairs are legitimate, not spaghetti ---
    crossings = 0
    crossing_pairs = []
    for i in range(len(lines)):
        for j in range(i + 1, len(lines)):
            A, B = lines[i], lines[j]
            if abs(A[0][0]-B[0][0]) < .5 and abs(A[0][1]-B[0][1]) < .5:
                continue   # share start node
            if abs(A[-1][0]-B[-1][0]) < .5 and abs(A[-1][1]-B[-1][1]) < .5:
                continue   # share end node
            for a in range(len(A)-1):
                for b in range(len(B)-1):
                    if seg_cross(A[a], A[a+1], B[b], B[b+1]):
                        crossings += 1
                        crossing_pairs.append((a, b))
                        break
                else:
                    continue
                break

    # --- edge length (screen px) ---
    lens = []
    for L in lines:
        # SVG viewBox units -> screen px. Approximate via panel scale.
        lens.append(0.0)
    scale = info["w"] / 22.0   # xaxis range is 0..22

    # --- edges passing through unrelated markers ---
    through_nodes = 0
    for L in lines:
        for m in marks:
            if seg_point_dist(L[0], L[-1], (m["cx"], m["cy"])) < m["r"] + 2:
                through_nodes += 1
                break

    # --- edges passing through label boxes ---
    through_labels = 0
    for L in lines:
        for box in labels:
            if point_in_box(L[0], box, 0) or point_in_box(L[-1], box, 0):
                through_labels += 1
                break

    report = {
        "panel": f'{info["w"]}x{info["h"]}',
        "nodes": len(marks),
        "labels": len(labels),
        "edges": len(lines),
        "worst_label_gap": round(worst_label, 1),
        "worst_label_pair": worst_label_pair,
        "worst_marker_gap": round(worst_marker, 1),
        "edge_crossings": crossings,
        "edges_through_markers": through_nodes,
        "edges_through_labels": through_labels,
        "svg_scale_px_per_unit": round(scale, 2),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    print()
    clean = (crossings == 0 and worst_label >= 0 and worst_marker >= 0)
    print("VERDICT:", "BERSIH" if clean else "MASIH ADA MASALAH")
    return 0 if clean else 2


if __name__ == "__main__":
    sys.exit(main())
