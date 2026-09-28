import os
os.chdir(r"C:\Users\maman\Desktop\Project\trading-bot")

files = """agents/base_agent.py agents/news_agent.py agents/analysis_agent.py agents/decision_agent.py agents/execution_agent.py agents/direction_agents.py
analysis/technical.py analysis/fundamental.py analysis/ml_signals.py analysis/direction_ensemble.py analysis/probability_engine.py analysis/volatility.py analysis/vol_target.py analysis/backtester.py
trading/models.py trading/risk_manager.py trading/position_manager.py trading/paper_engine.py trading/live/safety.py trading/live/client.py trading/live/engine.py trading/live/executor.py trading/live/console.py trading/live/tui.py
data/price_feed.py data/hyperliquid_feed.py data/macro_fetcher.py data/news_fetcher.py data/sentiment.py data/__init__.py
database/db.py database/models.py database/repository.py database/__init__.py
dashboard/app.py dashboard/callbacks/update_callbacks.py dashboard/layouts/hud.py dashboard/layouts/hud_figures.py dashboard/layouts/palette.py dashboard/layouts/positions.py dashboard/layouts/performance.py dashboard/layouts/price_chart.py dashboard/layouts/news_feed.py dashboard/layouts/agent_logs.py dashboard/assets/style.css dashboard/assets/neural_flow.css
core/config.py core/event_bus.py core/logger.py core/market_store.py core/microstructure.py core/scheduler.py core/utils.py
ml/trainer.py ml/predictor.py""".split()

MAP = {"agents": "signal-agents", "analysis": "signal-agents", "trading": "execution-risk",
       "data": "data-persistence", "database": "data-persistence",
       "dashboard": "ui-infra", "core": "ui-infra", "ml": "ui-infra"}

tot = {}
for f in files:
    try:
        n = sum(1 for _ in open(f, encoding="utf-8", errors="replace"))
    except FileNotFoundError:
        n = 0
        print("MISSING", f)
    g = MAP[f.split("/")[0]]
    tot.setdefault(g, [0, 0])
    tot[g][0] += 1
    tot[g][1] += n
    print("%6d  %s" % (n, f))

print()
for g in ("signal-agents", "execution-risk", "data-persistence", "ui-infra"):
    c, l = tot[g]
    print(g, "files=", c, "lines=", l)
print("TOTAL files", sum(c for c, _ in tot.values()), "lines", sum(l for _, l in tot.values()))
