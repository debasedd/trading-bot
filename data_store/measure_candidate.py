"""Ukur kandidat di browser sungguhan, lalu bandingkan dengan baseline."""
import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "data_store" / "_snap"))

from data_store.apply_candidate import build_module
from data_store.measure_lib import main, brief, PROBE_TOKENS

CASES = {
    "all12": {},
    "first6": {"tokens": dict(list(PROBE_TOKENS.items())[:6])},
    "last6": {"tokens": dict(list(PROBE_TOKENS.items())[6:])},
    "one": {"tokens": {"BTC": ("LONG", 1.0)}},
    "wide4": {"tokens": {"BTC": ("LONG", 1.0), "XRP": ("SHORT", 1.0),
                         "ZEC": ("LONG", 1.0), "XPL": ("SHORT", 1.0)}},
    "empty": {"tokens": {}},
}


def run(mod_path, tag, **kw):
    sys.path.insert(0, str(mod_path.parent))
    mod = importlib.import_module(mod_path.stem)
    importlib.reload(mod)
    k2l = {k: v[2] for k, v in mod.NEURAL_SYSTEM_NODES.items()}
    res = main(mod.create_neural_net_fig, k2l,
               f"data_store/report_{tag}.json", cases=CASES, **kw)
    print(f"=== {tag} (md5-relevant) graph {res['width']}x{res['height']} ===")
    for name in CASES:
        print(brief(res, name))
    return res


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "cand"
    if which == "base":
        sys.path.insert(0, str(ROOT / "dashboard" / "layouts"))
        run(Path(ROOT / "dashboard" / "layouts" / "hud_figures.py"), "base")
    else:
        out = build_module(dst=Path("data_store/_snap/hf_cand.py").resolve())
        run(out, "cand")
