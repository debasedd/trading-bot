"""Baseline: ukur kode yang sedang berjalan, apa adanya."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dashboard.layouts import hud_figures
from data_store.measure_lib import main, brief, PROBE_TOKENS

K2L = {k: v[2] for k, v in hud_figures.NEURAL_SYSTEM_NODES.items()}

CASES = {
    "all12": {},
    "one": {"tokens": {"BTC": ("LONG", 1.0)}},
    "six": {"tokens": dict(list(PROBE_TOKENS.items())[:6])},
    "empty": {"tokens": {}},
}

if __name__ == "__main__":
    res = main(hud_figures.create_neural_net_fig, K2L,
               "data_store/baseline_report.json", cases=CASES)
    print(f"graph width {res['width']}  height {res['height']}")
    for name in CASES:
        print(brief(res, name))
    r = res["results"]["all12"]
    print("  plot area px:", r["plot_area_px"], " ranges:", r["ranges"])
    print("  thruNode:", r["thru_node"])
    print("  thruLabel:", r["thru_label"])
    print("  edges:", r["edges"])
