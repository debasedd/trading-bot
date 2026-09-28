import json
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "data_store/_base.json"
d = json.load(open(path))
KEYS = ("panel", "plot_area", "px_per_unit", "node_count", "edge_count",
        "worst_label_gap", "worst_label_pair", "label_violations",
        "worst_marker_gap", "worst_marker_pair", "marker_violations",
        "max_marker_px", "edge_len_mean", "edge_len_max", "edges_over_150",
        "crossings", "edge_through_node", "edge_through_label", "BAD")
for w, r in d.items():
    print("WIDTH", w)
    for k in KEYS:
        print("   ", k, "=", r[k])
    print("    thruN:", r.get("edge_through_node_detail", [])[:8])
    print("    thruL:", r.get("edge_through_label_detail", [])[:8])
    print("    cross:", r.get("crossing_pairs", [])[:8])
