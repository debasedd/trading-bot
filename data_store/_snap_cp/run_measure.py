"""Measure the CURRENT hud_figures neural-net layout in a real browser."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from playwright.sync_api import sync_playwright
import harness as H

from dashboard.layouts.hud import create_neural_net_panel
import plotly.io as pio

# production-realistic signal map: 12 tokens, mixed LONG/SHORT, mixed conf
TOKENS = ["BTC", "ETH", "SOL", "XRP", "BNB", "DOGE", "NEAR", "ONDO", "HYPE", "ZEC", "XPL", "ENA"]
DIRS = ["LONG", "SHORT", "LONG", "LONG", "SHORT", "SHORT", "LONG", "BEARISH", "BULLISH", "LONG", "SHORT", "BULLISH"]
CONFS = [0.9, 0.8, 0.7, 0.95, 0.6, 0.85, 0.75, 0.65, 1.0, 0.55, 0.88, 0.72]


def make_signal_map(n=12, conf_scale=1.0):
    sm = {}
    for i in range(min(n, len(TOKENS))):
        sm[TOKENS[i]] = {
            "direction": DIRS[i],
            "confidence": min(1.0, CONFS[i] * conf_scale),
            "price_change": 0.01 * (i + 1) / 12.0,
        }
    return sm


def full_signal_map():
    sm = make_signal_map(12)
    for i, t in enumerate(TOKENS[12:]):
        sm[t] = {"direction": "LONG", "confidence": 0.5, "price_change": 0.002}
    return sm


ACTIVITY = {k: 0.8 for k in ("execution_agent", "decision_agent", "analysis_agent", "news_agent")}
FLOW = {k: 0.7 for k in ("allMids", "l2Book", "candle", "trades")}


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


def main():
    panel = create_neural_net_panel()
    g = find_graph(panel)
    cfg = g.config

    import dashboard.layouts.hud_figures as HF
    from dashboard.layouts.hud_figures import create_neural_net_fig

    cases = [
        ("empty", create_neural_net_fig({}, {}, {})),
        ("12tok", create_neural_net_fig(full_signal_map(), ACTIVITY, FLOW)),
        ("12tok-maxmark", create_neural_net_fig(
            {k: {"direction": "LONG", "confidence": 1.0, "price_change": 0.05}
             for k in TOKENS}, {k: 1.0 for k in ACTIVITY}, {k: 1.0 for k in FLOW})),
    ]

    out = {}
    with sync_playwright() as p:
        b = p.chromium.launch()
        for vw in (1280, 1366, 1600, 1920, 2560):
            for name, fig in cases:
                path = H.render_html(fig, cfg, os.path.join(HERE, "probe_%s.html" % name))
                pg = b.new_page(viewport={"width": vw, "height": 900})
                pg.goto("file:///" + path.replace("\\", "/"))
                pg.wait_for_timeout(2000)
                probe = pg.evaluate(H.PROBE)
                pg.close()
                if "error" in probe:
                    print(name, vw, "PROBE ERROR", probe)
                    continue
                k2l = {k: v[2] for k, v in HF.NEURAL_SYSTEM_NODES.items()}
                res = H.analyse(probe, key_to_label=k2l)
                if res["endpoint_mismatch"]:
                    print("  !! COORDINATE SPACE BROKEN:", res["endpoint_mismatch"][:3])
                H.dump("%s vw=%d" % (name, vw), res)
                if res.get("label_clipped"):
                    print("     CLIPPED:", res["label_clipped"],
                          " plotW=%.1f widest=%.1f" % (res["plot_w_px"], res["label_widest"]))
                out["%s@%d" % (name, vw)] = res
        b.close()

    with open(os.path.join(HERE, "baseline.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, default=str)


if __name__ == "__main__":
    main()
