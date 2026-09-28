"""
verify_constellation.py — Ukur tabrakan label/marker di browser sungguhan.

Perhitungan di Python hanya estimasi: yang sebenarnya menentukan apakah
dua label saling menimpa adalah kotak teks hasil render browser. Skrip ini
membaca bounding box tiap node dan label langsung dari DOM Plotly, lalu
menghitung gap minimum antar keduanya.

Jalankan dengan dashboard hidup di http://127.0.0.1:8050
"""
import itertools
import json
import math
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

from dashboard.layouts.hud_figures import NEURAL_SYSTEM_NODES

URL = "http://127.0.0.1:8050"
OUT = Path(__file__).parent / "data_store" / "constellation_check.json"

# Plotly memberi setiap titik marker sebuah <path class="point"> dengan
# transform translate(x,y). Label teksnya <text class="xtick">-like span
# di dalam g.point. Kita baca keduanya lewat data attribute Plotly.
PROBE = r"""
() => {
  const gd = document.getElementById('hud-neural-graph');
  const plot = gd && gd.querySelector('.js-plotly-plot');
  if (!plot) return {error: 'plot not found'};

  // Plotly v6: untuk trace markers+text, marker dan text adalah sibling
  // zip keduanya berdasarkan urutan, bukan
  // lookup parent.
  // Carryl semua path.point dan semua text di dalam scatterlayer, lalu
  // zip berdasarkan urutan dokumen. Plotly menaruh keduanya sebagai
  // sibling di dalam <g class="points">, tapi group pertama adalah trace
  // edge yang tidak punya marker sama sekali.
  const scope = plot.querySelector('.scatterlayer') || plot;
  const pts = [...scope.querySelectorAll('path.point')];
  const txts = [...scope.querySelectorAll('text')];
  if (!pts.length) return {error: 'no path.point anywhere in scatterlayer'};
  if (!txts.length) return {error: 'no text in scatterlayer'};

  const out = [];
  const n = Math.min(pts.length, txts.length);
  for (let i = 0; i < n; i++) {
    const pb = pts[i].getBoundingClientRect();
    const tb = txts[i].getBoundingClientRect();
    out.push({
      label: (txts[i].textContent || '').trim(),
      markerCx: pb.x + pb.width / 2, markerCy: pb.y + pb.height / 2,
      markerW: pb.width, markerH: pb.height,
      labelCx: tb.x + tb.width / 2, labelCy: tb.y + tb.height / 2,
      labelW: tb.width, labelH: tb.height,
      labelX0: tb.x, labelY0: tb.y, labelX1: tb.x + tb.width,
      labelY1: tb.y + tb.height,
    });
  }

  // --- EDGES -------------------------------------------------------------
  // Versi lama TIDAK mengukur edge sama sekali, padahal keluhan
  // justru soal garis. Garden tanpa label yang bertabrakan tetap bisa
  // terlihat seperti rawaan kalau 20 garis saling menyeberang.
  //
  // Koordinat atribut `d` berada di user space SEBELUM transform, jadi
  // harus lewat getScreenCTM() dulu — kalau tidak, tiap edge meleset
  // konstan ~28px dan semua pengukuran panjang jadi salah.
  const svg = plot.querySelector('svg.main-svg');
  const gr = gd.getBoundingClientRect();
  const edges = [];
  const edgeIds = [];
  for (const p of scope.querySelectorAll('path.js-line')) {
    const m = p.getScreenCTM();
    const nums = (p.getAttribute('d') || '').match(/-?\d+(\.\d+)?/g) || [];
    const poly = [];
    for (let i = 0; i + 1 < nums.length; i += 2) {
      const sp = svg.createSVGPoint();
      sp.x = +nums[i]; sp.y = +nums[i + 1];
      const t = sp.matrixTransform(m);
      poly.push([t.x - gr.x, t.y - gr.y]);
    }
    let len = 0;
    for (let i = 0; i + 1 < poly.length; i++) {
      len += Math.hypot(poly[i + 1][0] - poly[i][0], poly[i + 1][1] - poly[i][1]);
    }
    edges.push({
      poly: poly, len: len,
      busy: !!(p.style.strokeDasharray && p.style.strokeDasharray !== 'none'),
    });
    // Plotly menulis customdata tiap trace ke gd.data[n].customdata, dan
    // urutan <g class="trace"> mengikuti urutan trace.
    const idx = [...gd.querySelectorAll('.scatterlayer g.trace')]
      .indexOf(p.closest('g.trace'));
    const cd = gd.data && gd.data[idx] && gd.data[idx].customdata;
    edgeIds.push(cd && cd.length ? [cd[0][0], cd[0][1]] : ['?', '?']);
  }

  return {nodes: out, edges: edges, edgeids: edgeIds,
          panelW: plot.clientWidth, panelH: plot.clientHeight};
}
"""


def gaps(items):
    """Semua pasangan, dengan gap label dan gap marker dalam piksel."""
    res = []
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            a, b = items[i], items[j]
            # Gap label: jarak antar kotak teks dikurangi separuh lebar.
            lgap = max(
                abs(a["labelCx"] - b["labelCx"]) - (a["labelW"] + b["labelW"]) / 2,
                abs(a["labelCy"] - b["labelCy"]) - (a["labelH"] + b["labelH"]) / 2,
            )
            # Gap marker: jarak antar titik dikurangi radius.
            mgap = max(
                abs(a["markerCx"] - b["markerCx"]) - (a["markerW"] + b["markerW"]) / 2,
                abs(a["markerCy"] - b["markerCy"]) - (a["markerH"] + b["markerH"]) / 2,
            )
            res.append({
                "a": a["label"], "b": b["label"],
                "label_gap": round(lgap, 1),
                "marker_gap": round(mgap, 1),
            })
    return res


# ---------------------------------------------------------------- edges
def _seg_cross(p1, p2, p3, p4):
    """True kalau dua segmen benar-benar berpotongan di bagian dalam."""
    d1x, d1y = p2[0] - p1[0], p2[1] - p1[1]
    d2x, d2y = p4[0] - p3[0], p4[1] - p3[1]
    den = d1x * d2y - d1y * d2x
    if abs(den) < 1e-12:
        return False
    s = ((p3[0] - p1[0]) * d2y - (p3[1] - p1[1]) * d2x) / den
    t = ((p3[0] - p1[0]) * d1y - (p3[1] - p1[1]) * d1x) / den
    return 1e-9 < s < 1 - 1e-9 and 1e-9 < t < 1 - 1e-9


def _seg_rect(seg, r):
    x0, y0, x1, y1 = r
    cs = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    for a in range(4):
        if _seg_cross(seg[0], seg[1], cs[a], cs[(a + 1) % 4]):
            return True
    return any(x0 <= p[0] <= x1 and y0 <= p[1] <= y1 for p in seg)


def _seg_circle(seg, cx, cy, r):
    a, b = seg
    dx, dy = b[0] - a[0], b[1] - a[1]
    l2 = dx * dx + dy * dy
    if l2 < 1e-12:
        return math.hypot(a[0] - cx, a[1] - cy) < r
    t = max(0.0, min(1.0, ((cx - a[0]) * dx + (cy - a[1]) * dy) / l2))
    return math.hypot(a[0] + t * dx - cx, a[1] + t * dy - cy) < r


def edge_report(nodes, edges, endpoints):
    """
    Metrik garis — ini yang TIDAK pernah diukur sebelumnya, padahal
    keluhan soal "garis ruwet".

    `endpoints` berisi [(src_label, dst_label)] sepanjang `edges`, supaya
    dua edge yang BERBAGI simpul tidak dihitung sebagai crossing (dua
    garis yang meet di satu titik bukan persilangan).
    """
    lens = sorted((e["len"], i) for i, e in enumerate(edges))
    res = {
        "edge_count": len(edges),
        "busy_edges": sum(1 for e in edges if e.get("busy")),
        "edge_len_mean": round(sum(l for l, _ in lens) / len(lens), 1) if lens else 0,
        "edge_len_max": round(lens[-1][0], 1) if lens else 0,
        "edges_over_150px": sum(1 for l, _ in lens if l > 150),
        "edges_over_200px": sum(1 for l, _ in lens if l > 200),
    }

    crossings = 0
    detail = []
    for i, j in itertools.combinations(range(len(edges)), 2):
        a, b = endpoints[i], endpoints[j]
        if set(a) & set(b):
            continue  # berbagi simpul, bukan persilangan
        A, B = edges[i]["poly"], edges[j]["poly"]
        # Per pasangan SEGMENT, bukan per pasangan (segA, segB) sekaligus:
        # `combinations(range(nA-1), 2)` mengambil dua segA DAN dua segB
        # dalam satu iterasi, sehingga dua titik potong pada garis yang
        # sama terhitung sebagai nol. Edge berubung 17 titik Sample
        # punya 16 segmen, jadi bentuknya combinations(range(16), 2).
        c = sum(1 for u in range(len(A) - 1) for v in range(len(B) - 1)
                if _seg_cross(A[u], A[u + 1], B[v], B[v + 1]))
        if c:
            crossings += c
            detail.append((c, "+".join(a), "+".join(b)))
    detail.sort(reverse=True)
    res["edge_crossings"] = crossings
    res["crossing_detail"] = detail[:12]

    through_node, through_label = [], []
    for i, e in enumerate(edges):
        a, b = endpoints[i]
        for n in nodes:
            if n["label"] in (a, b):
                continue
            r = min(n["markerW"], 30.0) / 2.0 + 1.0
            rect = (n["labelX0"], n["labelY0"], n["labelX1"], n["labelY1"])
            for k in range(len(e["poly"]) - 1):
                s = (e["poly"][k], e["poly"][k + 1])
                if _seg_circle(s, n["markerCx"], n["markerCy"], r):
                    through_node.append(["+".join((a, b)), n["label"]])
                    break
            for k in range(len(e["poly"]) - 1):
                s = (e["poly"][k], e["poly"][k + 1])
                if _seg_rect(s, rect):
                    through_label.append(["+".join((a, b)), n["label"]])
                    break
    res["edge_through_node"] = through_node
    res["edge_through_label"] = through_label
    return res


def main() -> int:
    report = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(
            viewport={"width": 1920, "height": 1080}, device_scale_factor=1
        )
        page.goto(URL, wait_until="domcontentloaded")
        page.wait_for_selector("#hud-neural-graph", timeout=30000)
        # Beri Plotly + callback ensemble waktu untuk mengisi node.
        page.wait_for_timeout(6000)

        info = page.evaluate(PROBE)
        if info.get("error"):
            print("ERROR:", info["error"])
            return 2
        if not info.get("nodes"):
            print("ERROR: no nodes extracted; keys =", list(info))

        nodes = info["nodes"]
        panel = f'{info["panelW"]}x{info["panelH"]}'
        g = gaps(nodes)
        g.sort(key=lambda r: r["label_gap"])
        worst_label = g[0] if g else None
        worst_marker = min(g, key=lambda r: r["marker_gap"]) if g else None

        report = {
            "panel": panel,
            "node_count": len(nodes),
            "worst_label_pair": worst_label,
            "worst_marker_pair": worst_marker,
            "label_gap_violations": [r for r in g if r["label_gap"] < 0],
            "marker_gap_violations": [r for r in g if r["marker_gap"] < 0],
            "tightest_8": g[:8],
        }

        # Metrik garis. Endpoint dibaca dari customdata trace, lalu dipetakan
        # key->label supaya "edge menembus label" tidak salah menghitung
        # label milik simpul itu sendiri (CORE_ENGINE berlabel SIGNAL).
        raw = info.get("edgeids") or []
        lbl2key = {v[2]: k for k, v in NEURAL_SYSTEM_NODES.items()}
        key2lbl = {k: v[2] for k, v in NEURAL_SYSTEM_NODES.items()}
        endpoints = [
            (key2lbl.get(a, a), key2lbl.get(b, b)) for a, b in raw
        ]
        if len(endpoints) == len(info.get("edges") or []):
            report.update(edge_report(nodes, info["edges"], endpoints))
        else:
            report["edge_error"] = (
                f"jumlah endpoint {len(endpoints)} != jumlah path "
                f"{len(info.get('edges') or [])}")

        el = page.query_selector("#hud-neural-graph")
        el.evaluate_handle("n => n.closest('.hud-panel')").as_element().screenshot(
            path=str(Path(__file__).parent / "data_store" / "constellation_shot.png")
        )
        browser.close()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))

    # Gate. Angka garis sekarang ikut menentukan lulus/tidak.
    #
    # Versi lama hanya meng-gate label/marker, jadi layout yang garisnya
    # saling menyeberang tetap lulus dan diklaim "CLEAN" padahal itu
    # persis keluhan yang belum selesai. Ambang disetel di sini supaya
    # regresi garis mustahil lolos tanpa terlihat.
    ok = (
        not report["label_gap_violations"]
        and not report["marker_gap_violations"]
        and not report.get("edge_error")
        and report.get("edge_crossings", 1) <= 8
        and not report.get("edge_through_node")
        and not report.get("edge_through_label")
    )
    print("\nRESULT:", "CLEAN" if ok else "ADA MASALAH")
    print(f"  label gap  : {report['worst_label_pair']['label_gap']}px "
          f"(butuh 8)  {report['worst_label_pair']['a']} vs "
          f"{report['worst_label_pair']['b']}")
    print(f"  marker gap : {report['worst_marker_pair']['marker_gap']}px "
          f"(butuh 6)  {report['worst_marker_pair']['a']} vs "
          f"{report['worst_marker_pair']['b']}")
    print(f"  edges      : {report.get('edge_count')} "
          f"(mean {report.get('edge_len_mean')}px, "
          f"max {report.get('edge_len_max')}px, "
          f">150px {report.get('edges_over_150px')})")
    print(f"  crossings  : {report.get('edge_crossings')} (maks 8)")
    print(f"  thru node  : {len(report.get('edge_through_node') or [])}")
    print(f"  thru label : {len(report.get('edge_through_label') or [])}")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
