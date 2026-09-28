# `0XF3CE25` — Crypto Perpetual Futures Scalping Bot

## What this is

A single-process, single-writer **crypto-perpetual-futures scalping bot** in Python, with a C++20/pybind11 hot-path kernel, an in-process SQLite ledger, and a Dash/Plotly operator HUD. One asyncio event loop in one OS thread does the trading; one daemon thread serves the web UI. It trades a rotating top-10 universe of Hyperliquid perpetuals on a 0.3-second decision tick. The paper path is complete, well-tested and actually trades; the live path is **structurally unreachable end-to-end** for two independent reasons documented in §5.12.1. The repository is not under version control in this working tree.

This document merges seven parallel analysis passes and re-verifies every disputed number against source. Section `02-*` was never written to disk, so §2 covers the architecture and process topology the missing section would have owned.

---

## Table of contents

- [Start here — the five things to know first](#start-here--the-five-things-to-know-first)
- [1. What this system is](#1-what-this-system-is)
- [2. Architecture and process topology](#2-architecture-and-process-topology)
- [3. Data flow and state](#3-data-flow-and-state)
- [4. Configuration and constants](#4-configuration-and-constants)
- [5. Risk, safety and correctness](#5-risk-safety-and-correctness)
- [6. Native extension and numerics](#6-native-extension-and-numerics)
- [7. Tests and contracts](#7-tests-and-contracts)
- [8. Working with this codebase](#8-working-with-this-codebase)
- [9. Subsystem reference](#9-subsystem-reference)
- [Appendix A — System map](#appendix-a--system-map)
- [Appendix B — Defect register](#appendix-b--defect-register)
- [Appendix C — Verification status](#appendix-c--verification-status)

---

## Start here — the five things to know first

> **1. `direction_snapshots` is the single source of truth for direction.**
> `DirectionEnsembleAgent` is the only writer (`agents/direction_agents.py:445`). `DecisionAgent._generate_scalp_signals` re-reads the newest row from SQLite rather than trusting the in-payload analysis (`agents/decision_agent.py:304`). The HUD probability scanner reads the *same* newest row (`dashboard/callbacks/update_callbacks.py:799-805`). The schema comment states the intent directly (`database/db.py:127-130`): *"HUD dan DecisionAgent membaca baris TERBARU yang sama, sehingga tampilan dan keputusan trade tidak mungkin berbeda sumber."* The numbers on screen are byte-identical to the numbers that gate an entry. **Verified:** `insert_direction_snapshot` has exactly one production call site.

> **2. The live path is inert, twice over.**
> `ExecutionAgent` calls six members of `self.engine`. `LiveExecutor` implements two. `get_price`, `check_positions`, `_last_prices` and `position_manager` are all **missing**, so every live cycle raises `AttributeError` — swallowed by the blanket `except Exception` at `run.py:596`, which makes a broken executor indistinguishable from a healthy one. Even with that fixed, `DecisionAgent` never sets `Order.quantity`, so `LiveExecutor._open` sends `size = order.quantity or 0.0` = `0.0` and `SafetyGate` rejects it as `INVALID_INPUT`. Two independent blockers on the same path.

> **3. `config.yaml` overrides dataclass defaults on almost every economic knob, and a missing file is not an error.**
> `load_config` returns pure defaults when the file is absent (`core/config.py:420-421`), and `get_config()` takes no path argument, so the path is CWD-relative. Launching from the wrong directory silently yields a **3-position, 20-leverage, 0.0005-taker** bot instead of the configured **30-position, 50-leverage, 0.00045-taker** one. No diagnostic, no warning.

> **4. SL/TP are rebuilt from the actual fill price, and the agent's stops are discarded.**
> `_execute_open` throws away `order.stop_loss` / `order.take_profit` and recomputes both from the price it actually filled at (`trading/paper_engine.py:481-482`). This is deliberate: `think()` and `execute_order()` are separated by an `await`, so a stop computed at P1 and filled at P2 misprices the actual risk distance. The recorded symptom was 0-6 second positions, 100 % `SL_HIT`. Locked by `tests/test_fill_price_sl.py:82`.

> **5. The paper fill model has no slippage, no spread, no depth and no impact.**
> `PaperTradingEngine.get_price` returns the cached last price verbatim (`trading/paper_engine.py:159-179, 191`). The only round-trip cost modelled is the double taker fee, `0.0009` (0.09 %). A correct FIFO-queue book-walk model exists in `analysis/backtester.py:239-300` and is never called in production. Paper results are therefore an upper bound on achievable performance and a lower bound on achievable risk.

---

## 1. What this system is

### 1.1 Identity and surface

The product name is stamped into the UI and the startup banner: `0XF3CE25 // AI CRYPTO FUTURES TERMINAL` (`run.py:102-104`, `dashboard/app.py:30`). The footer claims `0XF3CE25 CORE ENGINE v2.4 · ZERO LEAKAGE` (`dashboard/layouts/hud.py:439`); "v2.4" is a hardcoded literal in the footer markup, not a version read from anywhere. `0XF3CE25` is branding — not a build id, a hash, or a version string.

Shipped surface, counted rather than claimed. **All figures in this table were re-measured during integration:**

| Surface | Count | How it was measured |
|---|---|---|
| Python packages (first-party) | 9 (`core`, `agents`, `analysis`, `trading`, `trading/live`, `data`, `database`, `dashboard`, `ml`) | directory listing |
| `run.py` | **878** lines | `wc -l run.py` |
| `config.yaml` | **214** lines, **16** top-level blocks | `wc -l`; `grep -cE "^[a-z_]+:"` |
| SQLite tables | **10** | `grep -c "CREATE TABLE" database/db.py` |
| SQLite indexes | **8** | `grep -c "CREATE INDEX" database/db.py` |
| Test files matching `tests/test_*.py` | **24** | `ls tests/test_*.py \| wc -l` |
| `def test_` occurrences (regex) | **570** | `grep -rho "def test_" tests/test_*.py \| wc -l` |
| **Collected test cases (authoritative)** | **566**, `OK (skipped=4)`, 8.284 s | `python -m unittest discover tests`, exit 0 |
| Interpreter on this machine | CPython 3.14.6 | `python --version` |
| `requirements.txt` entries | **23** (plus comment blocks) | `grep -vE "^\s*#\|^\s*$"` |
| Native extension | `cpp_microstructure.cp314-win_amd64.pyd` at repo root, 408,576 bytes | directory listing |

`README.md` disagrees with the code on four counts, and the code is right. It claims `run.py` is 629 lines (actually 878, `README.md:189`); "14 file test, 108+ test" (actually 24 files / 566 collected cases, `README.md:179,221`); and "11 tabel" in three places while listing ten table names (`README.md:39,111,142,245`; `database/db.py` has exactly 10 `CREATE TABLE` statements). The README's architecture diagram is otherwise accurate about the one thing that matters most, reproduced in §1.2.

### 1.2 What it does

```
   Hyperliquid WebSocket  ──►  core/market_store (in-memory, per symbol)
        │                            │
        │                            ├──►  core/microstructure kernel (L2 → OFI)
        │                            └──►  EventBus (asyncio fan-out)
        │                                     │
   SQLite candles ◄── data/price_feed          │
        │                                     ▼
        │              4 specialists  ──►  DirectionEnsembleAgent  (log-odds pooling)
        │              orderflow 0.30                               │
        │              momentum  0.25                               │ writes every 5 s
        │              technical 0.25                               ▼
        │              microstructure 0.20                 direction_snapshots
        │                                                          │
        │                                              ┌───────────┴───────────┐
        │                                       DecisionAgent           HUD
        │                                       (reads the same row)   12-column grid
        ▼                                                              ▲
   PaperTradingEngine / LiveExecutor  ◄── Order(stop_loss=None)        │
        │                                                              │
        └─► PositionManager ─► SQLite WAL ─► Repository ─► callbacks ┘
```

Verbatim from `README.md:30-42`, which matches the code:

```
│  orderflow(0.30) momentum(0.25)       │
│  technical(0.25) microstructure(0.20) │
└───────────────────────────────────────┘
        │  log-odds pooling + shrinkage 0.85 + agreement bonus
        ▼
   P(LONG) / P(SHORT)  ──►  tabel `direction_snapshots` (tiap 5 dtk)
        │                                    │              ▲
        │ DecisionAgent baca snapshot        │              │ sumber
        ▼ (fresh < 20 dtk, conf ≥ 0.40)      └── HUD 12 kolom┘
   Order → PaperTradingEngine → PositionManager → SQLite WAL (11 tabel)
        │  guard tick · SL/TP ROUND_DOWN · circuit breaker rugi harian
        ▼
   Dashboard Dash/Plotly @ http://127.0.0.1:8050 (interval 500 ms)
```

The single most important structural property is the one in Start-here item 1: `direction_snapshots` is the one source of truth for direction.

### 1.3 One-paragraph summary

`run.py` boots a SQLite ledger in WAL mode, connects to Hyperliquid's public WebSocket for 20-level L2 order books and live candles, starts four specialist direction agents (order flow, momentum, technical, microstructure) that pool their verdicts by weighted log-odds into one `prob_long`/`prob_short` pair every 5 seconds, hands that snapshot to a `DecisionAgent` that opens or closes leveraged positions against it, and lets an `ExecutionAgent` fill those orders through a paper engine that recomputes stop-loss and take-profit from the actual fill price, guards against stale or spiking ticks, and enforces four hard risk limits before every open. Positions are held in SQLite with atomic balance mutation and claim-once closes; P&L is realized net of both taker fees. A Dash dashboard on `127.0.0.1:8050` repaints at 2 Hz from the same rows. A parallel `trading/live/` stack gates any real-money order behind a pure `SafetyGate` and the Hyperliquid SDK — but the live path is currently unreachable end-to-end (§5.12.1).

### 1.4 Layered architecture

Imports reconstructed from source. Arrows point from importer to imported. Zero first-party import cycles exist; the graph is a 7-level DAG.

```
L7  tests/                                sink; imports every layer

L6  dashboard/
      app.py                -> layouts.hud, callbacks.update_callbacks
      callbacks/update_callbacks.py
                             -> core.{config,logger,market_store}
                             -> analysis.{probability_engine,technical}
                             -> layouts.{hud_figures,positions,agent_logs,
                                        news_feed,performance,price_chart}
                             -> (deferred) data.price_feed
      layouts/hud.py        -> layouts.hud_figures
      layouts/hud_figures.py-> layouts.palette
                             -> (deferred, in-function) analysis.probability_engine

L5  agents/
      base_agent.py         -> core.{event_bus,logger}, database.{db,models,repository}
      news_agent.py         -> base_agent, data.{news_fetcher,sentiment}
      analysis_agent.py     -> base_agent, data.{price_feed,macro_fetcher,sentiment},
                               analysis.{technical,fundamental,ml_signals}
      decision_agent.py     -> base_agent, core.{scheduler,market_store,utils},
                               trading.{models,risk_manager}
      execution_agent.py    -> base_agent, trading.{models,risk_manager}
                             -> (deferred) analysis.volatility
      direction_agents.py   -> base_agent, analysis.{direction_ensemble,technical}, core.market_store
                             -> (deferred) analysis.probability_engine

L4  trading/
      models.py             -> (nothing first-party)
      risk_manager.py       -> core.{config,logger}, trading.models
      position_manager.py   -> core.{config,event_bus,logger,market_store},
                               database.{db,models,repository}, trading.risk_manager
      paper_engine.py       -> core.{config,event_bus,logger,market_store},
                               database.{db,models,repository},
                               trading.{models,risk_manager,position_manager}
                               -> (deferred) analysis.volatility
      live/safety.py        -> core.{config,logger}
      live/client.py        -> core.logger
      live/console.py       -> core.logger -> (9 deferred) live.tui
      live/engine.py        -> core.{config,logger}, live.{client,safety}
      live/executor.py      -> core.{logger,market_store}, live.engine
    ml/
      trainer.py            -> core.logger
      predictor.py          -> analysis.ml_signals, core.logger   <-- only upward edge

L3  data/
      hyperliquid_feed.py   -> core.{logger,market_store,__microstructure}
      price_feed.py         -> core.{config,event_bus,logger}, data.hyperliquid_feed
                               -> (deferred) core.market_store x5, database.{db,models,repository}
      macro_fetcher.py      -> core.{config,logger}, database.models
      news_fetcher.py       -> core.{config,logger}, database.models
      sentiment.py          -> core.logger

L2  analysis/
      direction_ensemble.py -> (nothing first-party; pure functions, no I/O)
      technical.py          -> core.{config,logger}
      volatility.py         -> core.{config,logger,market_store}
      vol_target.py         -> core.{config,logger,market_store}      [DEAD MODULE]
      probability_engine.py -> core.__microstructure
      fundamental.py        -> core.logger
      ml_signals.py         -> core.{config (unused),logger}
      backtester.py         -> core.{config,logger}                     [TESTS-ONLY]

L1  database/
      db.py                 -> core.{config,logger}
      models.py             -> (nothing first-party)
      repository.py         -> database.{db,models}, core.logger
    trading/models.py        -> (nothing first-party)

L0  core/
      config.py             -> (stdlib only: os, yaml, dataclasses, pathlib)
      logger.py             -> core.config
      event_bus.py          -> core.logger
      microstructure.py     -> core.logger
      scheduler.py          -> core.{config,logger}
      market_store.py       -> (stdlib only: time, collections.deque, typing)
      utils.py              -> (stdlib only: datetime, typing)
```

Two composition roots, both outside the DAG's lower layers:

| Root | Imports | Role |
|---|---|---|
| `run.py` | 16 module-level first-party + 6 deferred, touching all 7 layers | sole runtime entry point; nothing in the repo imports it except `test_live_session.py:8` |
| `dashboard/app.py` | 4 first-party | second root: builds the Dash app, mounts the layout, binds callbacks |

Three notable structural facts:

- **L0 is a true leaf.** `core/` imports no other first-party package except itself. `core/logger.py:11 → core/config.py` is the only internal edge.
- **The only upward edge in the whole graph** is `ml/predictor.py:11 → analysis.ml_signals` (L4 → L2). It is also dead: `ml/predictor.py` has zero importers anywhere, including tests. The real inference engine is `analysis/ml_signals.py`.
- **Three modules are entirely unreferenced:** `analysis/vol_target.py`, `ml/predictor.py`, `ml/trainer.py` (the last is an offline CLI). `analysis/backtester.py` (980 lines, a full L2 queue-position fill model) is imported only by `tests/test_advanced_modules.py`. **`trading/live/` has no `__init__.py`** — it works as a PEP 420 implicit namespace package, confirmed by `ls trading/live/` returning only `client.py console.py engine.py executor.py safety.py tui.py __pycache__`. **Verified.**

Fan-in is extremely concentrated: `core.logger` has 33 production importers and `core.config` has 23, against a median near 1.

### 1.5 One-paragraph glossary of local jargon

These are the terms that will mislead a reader who takes them at face value.

| Term | Meaning in this codebase | Where |
|---|---|---|
| **microstructure / kernel** | Two things. (a) The discipline: L2 order-book shape metrics. (b) `MicrostructureKernel`, a swappable order-flow-imbalance calculator behind an ABC, with a process-global `_KERNEL` instance swapped at boot between a pure-Python reference and a C++20 pybind11 module. Not a GPU or low-latency-runtime claim. | `core/microstructure.py:57-78,182` |
| **L2 / `l2Book`** | The full order book, 20 levels per side. Hyperliquid sends a **complete snapshot every update, never a delta**. | `data/hyperliquid_feed.py:9,313-350` |
| **OFI (order flow imbalance)** | `(weighted_bid_vol − weighted_ask_vol) / (weighted_bid_vol + weighted_ask_vol)`, clamped to `[-1, 1]`, returned alongside a *relative* spread (fraction of mid, not absolute price). Weights decay linearly: level *i* gets `1.0 − 0.1·i`, so raw summed volume cannot dominate from the deepest resting level. | `core/microstructure.py:130-158`; C++ `cpp/microstructure_kernel.cpp:192-248` |
| **constellation** | The dashboard's node-link diagram of the agent pipeline, drawn by `create_neural_net_fig`. **Not** an ensemble, not a signal-optimizer, not a parameter search. Its geometry was tuned offline by browser-measured layout scripts in `data_store/`. | `dashboard/layouts/hud_figures.py:307-393,520`; panel label `SNIPE NEURAL NET · LIVE TRANSACTION GRAPH` at `hud.py:324` |
| **HUD** | The single-screen 12-column CSS grid dashboard ("Bloomberg"-style), as opposed to the five older tab layouts that still exist only as hidden compatibility shims. | `dashboard/layouts/hud.py:20`; `dashboard/app.py:70-89` |
| **snapshot** | One row in `direction_snapshots`: a per-symbol LONG/SHORT verdict with `prob_long`, `prob_short`, `confidence`, `z_composite`, an agent-breakdown JSON, and a diffusion-curve JSON. Written every 5 s per symbol, pruned to the newest 120 per symbol. | `database/db.py:131-140`; `agents/direction_agents.py:445` |
| **verdict** | The uniform dict every specialist returns: `{agent, symbol, direction, confidence, reasoning, factors, abstained}`. The aggregator never needs to know which agent produced it. | `analysis/direction_ensemble.py:194-216` |
| **abstain vs NEUTRAL** | Strictly separated, and the distinction is the primary test focus of `tests/test_direction_agents.py`. `abstain()` sets `abstained=True` so a data-blind agent is excluded from **both** numerator and denominator of the pool. Returning `NEUTRAL`/0.0 instead would let a blind agent look like a deliberately-neutral voter and drag the average. | `analysis/direction_ensemble.py:218`; `agents/direction_agents.py:126-127` |
| **log-odds pooling** | The ensemble arbitration method, explicitly **not** voting. Each active verdict becomes `z = sign(direction) × MAX_AGENT_Z(2.5) × confidence`, weighted by `base_weight × (1 + agreement_bonus × agreement)`, pooled, shrunk by `shrinkage_delta` (0.85), sigmoided once, clamped to `[0.02, 0.98]`. Chosen over averaging because averaging already-calibrated probabilities yields an *un*calibrated result. | `analysis/direction_ensemble.py:83-191`; `MAX_AGENT_Z = 2.5` at `:32` |
| **calibration / calibrated** | A statistical property of a probability (P(event) matches the observed rate), never a tuning step. The only "calibration" tooling in the repo is `data_store/calibrate_probe.py` and `data_store/_snap_cp/calibrate.py`, which measure the dashboard graph's pixel-per-character constant. | `analysis/direction_ensemble.py:9-10`; `core/config.py:233-234` |
| **snap / snapshot** | Two unrelated meanings: (a) the ensemble row above; (b) the WebSocket/candles "snapshot" payload. In `direction_agents.py` the word always means (a). | `agents/decision_agent.py:293-311`; `data/hyperliquid_feed.py:311-350` |
| **fill_price_sl** | Not a symbol in the codebase — it names a *contract*: SL and TP must be derived from the actual fill price, never from the price the agent saw at `think()` time. The bug it locks produced live positions lasting 0-6 seconds, 100 % `SL_HIT`. | `tests/test_fill_price_sl.py:1-10`; `trading/paper_engine.py:427-443` |
| **edges** | Three unrelated things: (a) the neural-graph node connections (`NEURAL_SYSTEM_EDGES`); (b) `compute_mathematical_edge` — a post-hoc realised-expectancy percentage, either `(win_rate × avg_win) − (loss_rate × avg_loss)` scaled by notional, or the Thorp form `(p·R) − (1−p)` with under 2 closed trades; (c) the `path.js-line` CSS targets in the neural graph. None of them means "order book edge". | `hud_figures.py:344`; `analysis/probability_engine.py:386`; `dashboard/callbacks/update_callbacks.py:373,1181` |
| **tape** | The recent-trades stream, truncated to `trades[:50]` at ingest. `MicrostructureAgent` converts it to signed volume; trades with unknown side are **skipped entirely** rather than counted in the denominator. | `core/market_store.py:248-251`; `agents/direction_agents.py:367-386` |
| **diffusion curve** | A time-indexed probability path for LONG and SHORT over `diffusion_horizon_minutes` (30) at `diffusion_points` (60) samples, from `compute_directional_curve`. It is a **display** curve, not a forecast with independently validated parameters — its steepness is set entirely by the undocumented scalar `0.45` in `drift_per_min = z_score * 0.45 * sigma`. | `agents/direction_agents.py:485-489`; `analysis/probability_engine.py:270-299` |
| **tier** | Data-source priority level. Tier 0 = Hyperliquid (real 20-level L2 + live candles), Tier 1 = Binance Futures via `ccxt`, then yfinance, then CoinGecko. "Tier" never refers to a model tier or an SLA tier. | `data/price_feed.py:2-12,110-122` |
| **volatility gate** | A runtime circuit breaker on the computed TP/SL: rejects when `tp_pct <= roundtrip_fee` ("every trade loses") or when the post-fee breakeven win rate exceeds `max_breakeven_win_rate` (0.65). It raises a dedicated `VolatilityGateError` rather than returning a rejection string, so the audit trail distinguishes "our ticket is bad" from "the market is untradeable". | `analysis/volatility.py:347-412`; `trading/paper_engine.py:26,325-328` |
| **breakeven** | Moving the stop loss to just past entry once the position is up more than `breakeven_trigger_pct` (0.20 %), at `breakeven_offset_pct` (0.15 %) above/below entry — chosen to cover the 0.10 % round-trip fee. The move is **monotonic**: it only fires if the current SL is `None` or on the wrong side. | `agents/execution_agent.py:194-241`; `config.yaml:159-160` |
| **free balance / wallet / equity** | Three distinct numbers, named explicitly because an earlier version conflated them. `free_balance` = cash after margin locks; `wallet_balance` = free + open margin; `equity` = wallet + unrealized PnL. | `trading/paper_engine.py:846-855`; duplicated at `run.py:646-648` and `update_callbacks.py:670-674` |
| **quiet mode** | Startup-only mode: raises the console log handler to WARNING while a single global progress bar draws, then `end_quiet_mode()` restores normal output so runtime trade/error events stay visible. Decided once at import from `TRADEBOT_QUIET_STARTUP`. The file handler always keeps everything. | `core/logger.py:106-108,218-229`; `run.py:784` |
| **claim-once close** | `UPDATE ... WHERE id = ? AND status = 'OPEN'` returning `rowcount > 0`. Four concurrent close paths race (SL/TP scan, scalp TP, expiry, reversal); only one can win, and the losers get `None`, which is treated as benign. | `database/repository.py:147,171`; `trading/position_manager.py:209-211,392-394` |
| **round-down money** | All monetary arithmetic is `Decimal` quantized with explicit `ROUND_DOWN`: price `1e-8`, money `0.01`, qty `1e-5`. Never `ROUND_HALF_EVEN`, because on a 0.25 % scalp a one-tick round-half-even move in the wrong direction eats exactly the room the scalp was chosen for. | `trading/risk_manager.py:16-18` — **verified** |
| **0XF3CE25** | Product branding in the banner and footer. Not a build id, a hash, or a version string. | `run.py:102`; `dashboard/layouts/hud.py:439` |

---

## 2. Architecture and process topology

### 2.1 Threads and tasks

One OS thread does everything except serving HTTP. All other concurrency is asyncio tasks on one loop.

| Kind | Name | Created at | Cadence | Tracked in `_background_tasks`? |
|---|---|---|---|---|
| OS thread | `DashBoardThread` (daemon) | `run.py:732-737`, target `run_dashboard` | serves Dash forever | n/a |
| task | `HyperliquidFeed.websocket_loop` | `data/price_feed.py:154` via `start_streaming` | continuous, backoff 1 s → ×2 → 30 s cap | **no** — `run.py:337` discards the handle |
| task | `_ping_loop(ws)` | `data/hyperliquid_feed.py:402` | app-level JSON ping every 30 s | child of the WS task, cancelled in its `finally` |
| task | `_refresh_cache` (volatility) | `trading/paper_engine.py:135` | fire-and-forget at engine init | **no** — never awaited, never cancelled |
| task | `price_update_loop` | `run.py:762` | 1.5 s cycle, re-fetches only symbols whose price is `None` or >10.0 s old, 0.15 s stagger | yes |
| task | **`_execution_loop`** | `run.py:764` | **0.3 s — the tick** | yes |
| task | `_candle_refresh_loop` | `run.py:766` | 30 s; 1m/limit 300 + 5m/limit 120 per symbol, then `refresh_volatility_cache()` | yes |
| task | `_telemetry_loop` | `run.py:768` | 25 s warmup, then every 30 s | yes |
| task | `_maintenance_loop` | `run.py:775` | `max(60, snapshot_prune_interval)` = 3600 s | yes |
| task | `LiveEngine.run_loop` | `run.py:292` (`create_task`) | 5.0 s, live modes only | **no** — `self._live_task` is never cancelled in `shutdown()` |

`self._background_tasks` holds exactly the five tracked tasks (`run.py:777-779`).

Blocking work is pushed to the default `ThreadPoolExecutor` via `asyncio.to_thread` / `run_in_executor`: WebSocket frame parsing (`data/hyperliquid_feed.py:534`), `TechnicalAgent.evaluate` (`agents/direction_agents.py:210`), the technical agent's private `sqlite3` connection (`agents/direction_agents.py:341,514`), the FinBERT model load (`analysis/ml_signals.py:45`), the six FRED series (`data/macro_fetcher.py:54`), and every blocking `LiveExchange` REST call.

### 2.2 The tick

```python
# run.py:587-598 — verified verbatim
async def _execution_loop(self):
    """Loop responsif eksekusi order & pemantauan posisi (tiap 0.3 detik untuk scalping)."""
    logger.info("Execution loop dimulai (scalping mode: 0.3s interval)")
    while self._running:
        try:
            # 1. Jalankan siklus eksekusi untuk proses order tertunda
            await self.execution_agent.run_cycle()
            # 2. Pantau SL/TP, likuidasi, auto-close expired, scalp TP
            await self.execution_agent.check_positions()
        except Exception as e:
            logger.error(f"Error pada execution loop: {e}")
        await asyncio.sleep(0.3)
```

`check_positions` order is load-bearing and documented as such (`agents/execution_agent.py:177-192`):

1. `_protect_breakeven` (`:194`) — **must** run before step 2
2. `_scalp_take_profit` (`:279`)
3. `_auto_close_expired` (`:243`)
4. `engine.check_positions` → `PositionManager.update_positions` → per-tick SL/TP/liquidation scan

The reason for the 1-before-2 ordering: `breakeven_trigger_pct` is 0.0020 and `min_profit_pct` is 0.0060 (`config.yaml:159,135`). If they were equal, TP would fire in the same cycle and the entire breakeven-protection path would be dead code. The boot validator `_validate_scalping_economics` (`core/config.py:707-786`) rejects that configuration, and `tests/test_config.py:33` locks it.

The bare `except Exception` at `run.py:596` means the loop cannot die — and also means a permanently broken executor is indistinguishable from a healthy one in the logs.

### 2.3 Scheduler jobs

`AgentScheduler` wraps `AsyncIOScheduler`. `run.py:487-585` is the only job registry. Every job is registered with `max_instances=1`, so a slow cycle **skips** the next tick rather than queueing it.

| Job name | Function | Interval | Source |
|---|---|---|---|
| `news_agent` | `NewsAgent.run_cycle` | 300 s / 120 s US-open + one-shot at startup | `config.yaml:64-65` |
| `analysis_agent` | `AnalysisAgent.run_cycle` | 15 s / 10 s US-open + one-shot at startup | `config.yaml:66-67` |
| `decision_agent` | `DecisionAgent.run_cycle` | 3 s / 2 s US-open, **no** one-shot | `config.yaml:68-69` |
| `direction_ensemble` | `DirectionEnsembleAgent.run_cycle` | 5 s fixed | `config.yaml:192` |
| `refresh_top_volume` | `discover_top_volume_symbols` | 3600 s | `config.yaml:27` |
| `direction_snapshot_prune` | `_prune_direction_snapshots` | `max(60, 3600)` | `config.yaml:108` |
| `agent_log_prune` | `_prune_agent_logs` | `max(60, 1800)` | `config.yaml:115` |
| `macro_update` | `AnalysisAgent.update_macro` | 21600 s | `config.yaml:71` |
| `finbert_batch` | `AnalysisAgent.run_finbert_batch` | 900 s | `config.yaml:70` |
| `balance_snapshot` | `PositionManager.take_balance_snapshot` | 60 s | `config.yaml:73` |
| `_market_check` | `_adjust_intervals` | 60 s self-rescheduling | `core/scheduler.py:48,122` |

`agent_intervals.funding_rate: 28800` is declared in both `core/config.py:44` and `config.yaml:72` and is **never registered as a job**. Funding data is instead pulled opportunistically by `PriceFeed.fetch_funding_rate`.

`decision_agent` is registered with `start_immediately=False` (`run.py:530`) because the 0.3 s execution loop already drives it.

Note: the paper engine's accounting runs in **both** modes. `run.py:583` schedules `self.paper_engine.position_manager.take_balance_snapshot` and `run.py:623` calls `self.paper_engine.refresh_volatility_cache()` unconditionally, outside any mode branch. In live mode this is why the HUD shows a live-looking equity curve that no live order ever touched.

### 2.4 Agent lifecycle

All five agents inherit `BaseAgent.run_cycle` (`agents/base_agent.py:77-117`) except the ensemble:

```
run_cycle:  sense() -> think() -> act() -> _log_cycle()
            └─ any Exception  -> AgentLog row, never re-raised
            └─ CancelledError -> deliberately re-raised (BaseException, not Exception)
```

`DirectionEnsembleAgent.run_cycle` **overrides** the base entirely (`agents/direction_agents.py:437-451`). Consequence: ensemble cycles write **no `AgentLog` audit row** and get no `cycle_id`. The single writer of the table both the decision layer and the HUD read has the weakest error surface in the system. The sense/think/act stubs at `agents/direction_agents.py:455-462` are pure formality and are never called.

The `DirectionAgent` specialists deliberately make `sense`/`think`/`act` no-ops returning `{}`/`{}`/`None` (`agents/direction_agents.py:93-100`); they have no independent lifecycle, and the no-ops exist only to keep the classes concrete under `BaseAgent`'s `@abstractmethod` contract.

### 2.5 Two dashboard clocks

`dashboard/app.py:52-67` mounts two `dcc.Interval` components, with the reason stated inline. **Verified verbatim:**

- `dashboard-interval` at **500 ms** — 16 callbacks. Data is already in memory from the WebSocket, so these callbacks only read memory.
- `dashboard-slow-interval` at **60000 ms** — 1 callback (`update_trade_stream_and_marquee`). It runs a heavy `trades ⋈ positions ⋈ agent_logs` JOIN that must not execute twice a second.

`register_callbacks` binds **19** callbacks (8 legacy compat stubs, 11 live) — **verified** by counting `@app.callback` in `dashboard/callbacks/update_callbacks.py`.

`DashboardConfig.update_interval = 2000` (`core/config.py:73`, `config.yaml:123`) has **zero readers** anywhere in the repo.

### 2.6 Running it

#### Prerequisites

- Python 3.10+ per `README.md:198`; this machine runs CPython 3.14.6, and the native kernel `.pyd` is CPython-3.14-specific (`cp314`), not limited-API.
- `pip install -r requirements.txt`.
- Internet access. Hyperliquid REST/WS requires no API key; the FRED key is optional but **never wired** — `data/macro_fetcher.py` uses raw `requests`, and `fredapi` is declared in `requirements.txt` but imported nowhere.

**Run everything from the repo root.** `config.yaml`, `ml/models/` and `data_store/` are all CWD-relative.

#### CLI

`run.py` has **no argparse**. All flags are substring tests against `sys.argv`:

| Flag | Effect | Site |
|---|---|---|
| `--paper`, `--non-interactive` | skip the mode menu, force simulation | `run.py:173` |
| `--testnet` | live testnet, skip the menu | `run.py:180` |
| `--live` | **real money**, skip the menu, prints a warning first | `run.py:180-190` |
| `--repair-candles` | one-shot DB repair, `sys.exit(0)`, no agents, no dashboard | `run.py:850-858` |

Because these are `if "--flag" in sys.argv` substring tests, `--tes` matches `--testnet`, and any unknown flag is silently ignored.

```bash
python run.py                      # interactive: mode menu, then boot
python run.py --paper              # non-interactive simulation
python run.py --testnet            # live on Hyperliquid testnet
python run.py --live               # real money
python run.py --repair-candles     # maintenance only, exits
```

Exit codes: `0` from the repair path (`run.py:858`); `2` when live config or the health check fails, printed as `BOT TIDAK DIJALANKAN.` with the reason (`run.py:864-878`); `0` on Ctrl+C. The only real `argparse` parser in the repo is `live_doctor.py:62-71` (`--testnet`, `--mainnet`, `--expect`).

#### Boot sequence, in order

`main()` (`run.py:817-843`) → `TradingBotApp.initialize()` (`run.py:297`) → `TradingBotApp.run()` (`run.py:740`):

```
print_banner                        run.py:819
setup_logger                        run.py:820   (quiet mode from TRADEBOT_QUIET_STARTUP)
_choose_mode                        run.py:824   BEFORE any subsystem
  --paper/--non-interactive -> paper
  --testnet/--live          -> live, menu skipped
  otherwise ask_mode(cfg)     -> paper | testnet | mainnet
  every failure path               -> paper, never raises
TradingBotApp.__init__              run.py:825   EventBus, AgentScheduler, PriceFeed,
                                                SentimentAnalyzer(use_finbert=True),
                                                MacroFetcher, PaperTradingEngine
add_signal_handler SIGINT/SIGTERM   run.py:830   NotImplementedError swallowed on Windows
initialize():
  1  init_db()                      run.py:303   SQLite WAL, 10 CREATE TABLE IF NOT EXISTS
  2  paper_engine.initialize()      run.py:308   init_account + set_initial_balance
                                               + volatility.set_candle_source
  3  price_feed.initialize()        run.py:313   Hyperliquid universe + ccxt binance
  3b discover_top_volume_symbols    run.py:324   REPLACES config.symbols (run.py:327)
  3c start_streaming()              run.py:337   WS task handle discarded
  4  sentiment_analyzer.initialize  run.py:342   VADER + ProsusAI/finbert
  5  4 agents constructed           run.py:347-369
     LIVE BRANCH                    run.py:364-365
       SafetyGate(273) -> LiveExchange(274) -> LiveEngine(276) -> health_check(278)
       health not ok -> RuntimeError -> exit 2, loop NEVER created
  5b DirectionEnsembleAgent         run.py:374-380
  6  _prefetch_historical_candles   run.py:393   240 x 1m, 120 x 5m, 120 x 1h per symbol
  7  update_macro()                 run.py:397   FRED + Forex Factory
run():
  _running = True                   run.py:742
  setup_scheduler()  -> 11 jobs     run.py:746
  scheduler.start()                 run.py:748
  start_dashboard()                 run.py:754   spawns DashBoardThread
  5 asyncio tasks                   run.py:762-779
  end_quiet_mode()                  run.py:784
  while self._running: sleep(1)     run.py:791
```

The progress bar drawn during stages is one global bar, not one per stage; `stage()` accumulates into `_stage_active["total"]` without resetting `done` once started, and `stage_done()` clears only the label (`core/logger.py:300-312, 387`). `stage_done` prints ASCII `[OK]` rather than `✓` because the default Windows cp1252 console has no glyph for it and `print()` would raise `UnicodeEncodeError` (`core/logger.py:372-378`).

#### Environment variables

| Variable | Effect | Read at |
|---|---|---|
| `HYPERLIQUID_PRIVATE_KEY` | EVM signing key | `run.py:263`, `trading/live/safety.py:200`, `console.py:280` |
| `HYPERLIQUID_ACCOUNT_ADDRESS` | holding address (distinct from signer) | `run.py:269` |
| `TRADEBOT_LIVE` | must be truthy before any live order | `trading/live/safety.py:266` |
| `TRADEBOT_LIVE_CONFIRMED` | second authorization layer, must be `"1"` | `trading/live/safety.py:270` |
| `TRADEBOT_LIVE_KILL_SWITCH` | **inverted**: unset = NOT killed; trips on any value outside `("", "0", "false", "no")` | `trading/live/safety.py:277` |
| `TRADEBOT_KERNEL` | `python` \| `cpp`; `cpp` raises `RuntimeError` if the `.pyd` is missing | `core/microstructure.py:329-345` |
| `TRADEBOT_QUIET_STARTUP` | raises console handler to WARNING; decided once at import | `core/logger.py:106` |
| `TRADEBOT_BANNER` | re-enables the silenced Flask banner | `dashboard/app.py:112` |
| `TRADEBOT_VERBOSE_STARTUP` | **documented at `core/logger.py:95,211` and read by no code** | — |

`TRADEBOT_LIVE` and `TRADEBOT_LIVE_CONFIRMED` are set only by tests. The console sets only `HYPERLIQUID_PRIVATE_KEY`. On a fresh checkout, `SafetyGate.master_blockers` therefore always reports `LIVE_DISABLED` + `NOT_CONFIRMED` and `can_send` returns `False` for every order — fail-closed by design, but it also means the live stack has never been observed running.

#### Companion commands

```bash
python -m ml.trainer                      # retrain RandomForest -> ml/models/signal_model.pkl
python -m unittest discover tests         # 566 collected cases, OK (skipped=4)
python run.py --repair-candles            # delete OHLC-invalid rows, re-pull 600 bars
python reset_paper_db.py                  # wipe trades/positions/logs, reset to 10 000 USDT
python live_doctor.py --testnet           # read-only live preflight
python check_behavior.py                  # 11 runtime-behaviour assertions
python check_dashboard_data.py            # post-hoc proof the callbacks actually ran
python backfill_realized_pnl.py --apply   # recompute positions.realized_pnl net of both fees
```

`ml/trainer.py:5` advertises `--symbol/--timeframe/--days` in its docstring; there is no argument parsing anywhere in the file (`ml/trainer.py:232-233`). The flags are silently ignored.

---

## 3. Data flow and state

### 3.1 The one-sentence shape

There is no pipeline. There are **two independent fan-outs from a single in-memory store**, joined by exactly one SQLite row table (`direction_snapshots`), and the paper/live split is a *single-line object substitution* that never rejoins.

```
Hyperliquid WS ──► market_store (in-memory)  ──┬─► DirectionEnsembleAgent ──► direction_snapshots (SQLite)
                                               │                                      │
                                               │                    ┌─────────────────┴─────────────────┐
                                               │                    ▼                                   ▼
                                               │            DecisionAgent                              HUD
                                               │                    │ TRADE_DECISION (EventBus)
                                               │                    ▼
                                               └──► ExecutionAgent ──► PaperTradingEngine ──► SQLite
                                                                    └──► LiveExecutor ──► Hyperliquid REST
                                                                          (writes NOTHING back)
```

Two facts a new engineer must internalise before reading any code:

1. **The dashboard never subscribes to the EventBus.** It opens a fresh `sqlite3.connect` per callback (`dashboard/callbacks/update_callbacks.py:64`) and reads the same rows the engine wrote. The EventBus is an in-process agent-to-agent channel only (`core/event_bus.py:62`).
2. **Live mode does not write to SQLite and does not publish to the EventBus.** `trading/live/executor.py` and `trading/live/engine.py` contain zero references to `Repository` or `publish`. In live mode the HUD renders a paper database that no live order ever touched.

### 3.2 End-to-end hop table: market data → filled position → P&L → UI

| # | Hop | Function (verbatim) | File:line | Output shape |
|---|---|---|---|---|
| 1 | Transport opens | `HyperliquidFeed.websocket_loop` | `data/hyperliquid_feed.py:492` | WS task; `HL_WS_URL` at `:47` |
| 1a | Kernel chosen once, pre-first-frame | `self._ensure_native_kernel()` | `data/hyperliquid_feed.py:513` → `core/microstructure.py:309` | rebinds module-global `_KERNEL` (`core/microstructure.py:182`) |
| 2 | Frame received | `raw = await asyncio.wait_for(ws.recv(), timeout=WS_RECV_TIMEOUT)` | `data/hyperliquid_feed.py:533` | str; `WS_RECV_TIMEOUT = 60.0` (`:56`) |
| 3 | Parse off-loop | `await asyncio.to_thread(self._handle_message, raw)` | `data/hyperliquid_feed.py:534` | thread-pool hop |
| 4 | **Single normalization point** | `HyperliquidFeed._handle_message` | `data/hyperliquid_feed.py:411` | `json.loads` at `:421` |
| 4a | allMids | `market_store.set_price(coin_to_symbol(coin), float(px))` | `data/hyperliquid_feed.py:439` | float |
| 4b | l2Book | `_parse_book` → `market_store.set_order_book` → `microstructure.ingest_l2` | `:452` → `:453` → `:458` | dict, then kernel write |
| 4c | candle | `market_store.set_live_candle(coin_to_symbol(coin), candle)` | `data/hyperliquid_feed.py:467` | dict; also calls `set_price` (`core/market_store.py:196`) |
| 4d | activeAssetCtx | `set_funding` / `set_open_interest` | `data/hyperliquid_feed.py:479` / `:481` | float |
| 4e | trades | `market_store.set_recent_trades(coin_to_symbol(coin), data)` | `data/hyperliquid_feed.py:490` | list, hard-truncated `trades[:50]` (`core/market_store.py:251`) |
| 5 | Kernel ingest (sole ingress) | `microstructure.ingest_l2(symbol, bids, asks)` | `core/microstructure.py:218` | mutates `_KERNEL` (`:182`) |
| 6 | In-memory read (hot path) | `market_store.get_price` / `get_order_book` / `get_price_history` / `get_price_age` / `get_live_candle` / `get_funding` / `get_open_interest` / `get_recent_trades` | `core/market_store.py:74 / 152 / 105 / 91 / 198 / 218 / 235 / 253` | see §3.4 |
| 7 | On-disk candle write (only path) | `PriceFeed.fetch_ohlcv` → `Repository.insert_candles_batch` | `data/price_feed.py:198` → `:267` → `database/repository.py:62` | `Candle` rows |
| 8 | Specialist verdict | `DirectionAgent.evaluate` (4 concrete `_evaluate`) | `agents/direction_agents.py:62`; OrderFlow `:114`, Momentum `:158`, Technical `:215`, Microstructure `:266` | verdict dict or `abstain` (`:218`) |
| 9 | Pool | `analysis.direction_ensemble.aggregate(verdicts, cfg)` | `analysis/direction_ensemble.py:83` | `{prob_long, prob_short, direction, confidence, z_composite, agent_breakdown}` |
| 9a | Curve | `probability_engine.compute_directional_curve` | `analysis/probability_engine.py:270` | `{time_horizons, long_probs, short_probs}` |
| 10 | **The join row** | `repo.insert_direction_snapshot(snapshot)` | `agents/direction_agents.py:445` → `database/repository.py:460` | one row per symbol per 5 s |
| 11 | Decision reads the join row | `repo.get_latest_direction_snapshot(symbol)` | `agents/decision_agent.py:304` → `database/repository.py:486` | dict |
| 11a | Freshness gate (hard) | `DecisionAgent._snapshot_is_fresh` | `agents/decision_agent.py:340` | `ts <= 0` → `False` |
| 12 | Order published | `await self.publish(Channels.TRADE_DECISION, {...})` | `agents/decision_agent.py:274` → `core/event_bus.py:62` | `Event` into each subscriber `asyncio.Queue` |
| 13 | Agent consumes | `ExecutionAgent.sense` drains `self._decision_queue` | `agents/execution_agent.py:73` | list of order payloads |
| 13a | Advisory SL/TP | `risk_manager.calculate_stop_loss` / `calculate_take_profit` | `agents/execution_agent.py:139-140` → `trading/risk_manager.py:157` / `:181` | floats, **overwritten downstream** |
| 14 | Router (paper) | `PaperTradingEngine.execute_order(order) -> Dict` | `trading/paper_engine.py:181` | `{"success","message","position_id","details"}` |
| 14a | Fill price | `price = self.get_price(order.symbol)` | `trading/paper_engine.py:191` → `:159` | cache-first, then `market_store` |
| 14b | SL/TP rebuild at fill | `calculate_stop_loss(price, side, sl_pct)` / `calculate_take_profit(...)` | `trading/paper_engine.py:481-482` | `order.stop_loss` / `order.take_profit` are **never read** here |
| 15 | Open path | `_execute_open(order, price)` | `trading/paper_engine.py:372` | 5-gate ladder, see §3.6 |
| 16 | Position insert | `PositionManager.open_position` | `trading/position_manager.py:47` | `position_id` or `None` |
| 17 | Atomic debit | `repo.apply_balance_delta(-(margin + fee))` | `trading/position_manager.py:85` → `database/repository.py:403` | free balance |
| 18 | Trade row | `repo.insert_trade(trade)` (`trade_type='OPEN'`) | `trading/position_manager.py:125` → `database/repository.py:204` | fee recorded here |
| 19 | Mark-to-market (per tick) | `PositionManager.update_positions(prices)` | `trading/position_manager.py:326` | writes `positions.unrealized_pnl` every tick at `:346` |
| 19a | Exit scan | LIQ → SL → TP, each `continue` | `trading/position_manager.py:349` / `:356` / `:362` | |
| 20 | Realize | `PositionManager.close_position` | `trading/position_manager.py:150` | dict or `None` (lost claim) |
| 20a | `open_fee` read back from `trades` | loop over `repo.get_trades_by_position` | `trading/position_manager.py:196-200` | not recomputed from `entry_price` |
| 20b | `net_pnl = pnl_info["pnl"] - open_fee - fee` | | `trading/position_manager.py:203` | net of **both** fees |
| 20c | Claim-once | `repo.close_position(...)` → `WHERE id=? AND status='OPEN'` | `trading/position_manager.py:209` → `database/repository.py:147` | `rowcount > 0` |
| 20d | Credit | `returned = margin + net_pnl + open_fee` | `trading/position_manager.py:243` | open fee added back, charged once |
| 20e | High-water mark | `repo.bump_peak_balance(equity_now)` | `trading/position_manager.py:258` → `database/repository.py:427` | `peak_balance = MAX(COALESCE(peak_balance,0), ?)` |
| 21 | Equity curve row (60 s job) | `PositionManager.take_balance_snapshot` | `trading/position_manager.py:436`, registered `run.py:583` | `balance_history` row (`database/repository.py:443`) |
| 22 | UI read (money) | `_get_sync_db()` per callback | `dashboard/callbacks/update_callbacks.py:64` | raw `sqlite3.Connection` |
| 22a | UI read (prices) | `market_store.get_price` in-memory | `dashboard/callbacks/update_callbacks.py:657`, `:744` | live uPnL is **recomputed**, not read from `positions` |
| 23 | UI read (direction) | `SELECT ... FROM direction_snapshots WHERE symbol = ? ORDER BY id DESC LIMIT 1` | `dashboard/callbacks/update_callbacks.py:799-805` | the same row as hop 11 |

### 3.3 Paper path vs live path, side by side

Everything from the WebSocket through `DecisionAgent` and the `TRADE_DECISION` event is identical. Divergence is a single object substitution.

#### Divergence point

```python
# run.py:359-368
self.live_engine = None
executor = self.paper_engine
if self.mode in ("testnet", "mainnet"):
    executor = await self._build_live_executor()
self.executor = executor

self.execution_agent = ExecutionAgent(self.event_bus, executor)
```

`_build_live_executor` (`run.py:238-295`) runs in this exact order, and the order is documented as non-reorderable in the docstring. **Verified verbatim from source:**

| Order | Call | Line | Why it must be here |
|---|---|---|---|
| 1 | `live_cfg = LiveConfig()` | `run.py:262` | bare dataclass defaults; **ignores `get_config().live`** |
| 2 | key from env `live_cfg.private_key_env` | `run.py:263` | `RuntimeError` if unset — bot refuses to start |
| 3 | `gate = SafetyGate(live_cfg)` | `run.py:273` | constructed **before** any connection |
| 4 | `exchange = LiveExchange(key, testnet=..., account_address=api_wallet)` | `run.py:274` | |
| 5 | `engine = LiveEngine(gate=gate, exchange=exchange, cfg=live_cfg)` | `run.py:276` | |
| 6 | `health = await engine.health_check()` | `run.py:278` | `if not health["ok"]: raise RuntimeError` (`:279-282`) — **before** the loop task exists; caught in `__main__` at `run.py:864` → exit code 2 |
| 7 | `create_task(engine.run_loop(interval=5.0, on_decision=_decide))` | `run.py:292-293` | `_decide` at `:288` always returns `None` |
| 8 | `return LiveExecutor(engine)` | `run.py:295` | |

#### Hop-by-hop comparison

| Hop | Paper | Live |
|---|---|---|
| Order entry | `PaperTradingEngine.execute_order` `trading/paper_engine.py:181` | `LiveExecutor.execute_order` `trading/live/executor.py:66` |
| Dispatch | `_execute_open` `:372` / `_execute_close` `:687` | `_open` `:77` / `_close` `:127` |
| Fill price | `self.get_price` — cache-first, zero slippage, no book walk | `_price_for` `executor.py:171` → `order.price` → `market_store.get_price`; **raises** on none |
| Price cache | `update_price` `paper_engine.py:155` writes `_last_prices` | `update_price` `executor.py:56` is a **deliberate no-op** (`return None`); docstring: the exchange is the source of truth |
| SL/TP source | **rebuilt at the fill price** `paper_engine.py:481-482` | taken from `order.stop_loss` / `order.take_profit`; refused if either is `None` (`executor.py:84-91`, again `engine.py:255`) |
| Volatility gate | `VolatilityGateError` `paper_engine.py:26`, raised at `:328` | **none** |
| Tick-quality guard | `_tick_quality_guard` `paper_engine.py:209`, called `:495` | **none** |
| Sizing | `calculate_scalp_position_size` `risk_manager.py:340` via `:519` | caller must supply `size`; `executor.py:103` sends `size=order.quantity or 0.0` |
| Fee | `calculate_fee(..., "TAKER")` `risk_manager.py:202` | exchange-charged; not modelled locally |
| Risk gate | `RiskManager.validate_trade` `risk_manager.py:273` via `paper_engine.py:566` — 4 limits | `SafetyGate.can_send` `trading/live/safety.py:347` — `master_blockers` `:247` (short-circuit) then `order_blockers` `:300` |
| Position record | SQLite `positions` + `trades` rows | exchange position, mirrored in `LiveEngine.positions` in-memory only (`engine.py:56-65`) |
| Exit mechanism | bot-side per 0.3 s tick | **exchange-side trigger orders** attached at submit time (`engine.py:351 _attach_protection`) — survives process death |
| Exit state machine | 4 edges: none→OPEN, OPEN→CLOSED, OPEN→LIQUIDATED | 2 edges: fill→protected, no close path in-process except `emergency_flat` `engine.py:655` |
| P&L accounting | `position_manager.py:150-270` | none; `SafetyGate.record_realized_pnl` `safety.py:415` has **zero production callers** |
| Publishes to EventBus | `Channels.TRADE_EXECUTED` `paper_engine.py:657`; `Channels.POSITION_UPDATE` `position_manager.py:129/273/408` | **none** |
| Writes to SQLite | every hop 17-21 | **none** |
| Visible to HUD | yes | **no** |

#### Rejoin: there is none

```
                 ┌── paper ──► positions/trades/account/balance_history (SQLite) ──► HUD reads it
ExecutionAgent ──┤
                 └── live  ──► Hyperliquid exchange ──► nowhere. No SQLite row, no EventBus event.
```

#### The interface mismatch — verified, not inferred

`ExecutionAgent` calls six members on `self.engine`. **Re-verified during integration by grepping both files.** `LiveExecutor` (`trading/live/executor.py:53-189`, 190 lines) implements exactly two of the six:

| Attribute used | Call site | `PaperTradingEngine` | `LiveExecutor` |
|---|---|---|---|
| `update_price` | `execution_agent.py:64, 71, 100` | `:155` yes | `:56` yes (no-op) |
| `get_price` | `execution_agent.py:96` | `:159` yes | **MISSING** |
| `execute_order` | `execution_agent.py:164` | `:181` yes | `:66` yes |
| `check_positions` | `execution_agent.py:192` | `:822` yes | **MISSING** |
| `_last_prices` | `execution_agent.py:213, 266, 290` | `:68` yes | **MISSING** |
| `position_manager` | `execution_agent.py:270, 311` | `:61` yes | **MISSING** |

`LiveExecutor`'s full method set is `__init__` (`:53`), `update_price` (`:56`), `execute_order` (`:66`), `_open` (`:77`), `_close` (`:127`), `_price_for` (`:171`), plus two module-level helpers `make_cloid` (`:28`) and `coin_of` (`:39`).

Consequence: in live mode `ExecutionAgent.check_positions()` raises `AttributeError` at `execution_agent.py:192` on **every** 0.3 s tick, and `run.py:596-597` swallows it, so the process looks healthy while nothing is checked. Live positions can only be exited by the exchange-side triggers attached at `engine.py:351`.

A second, independent blocker on the same path: `DecisionAgent.act` constructs `Order(...)` with `stop_loss=None, take_profit=None` (`agents/decision_agent.py:269-270`) and never sets `quantity`. `LiveExecutor._open` refuses the missing TP/SL first (`executor.py:84-91`); if that were bypassed, `size=order.quantity or 0.0` (`executor.py:103`) would be `0.0` and `SafetyGate` would append `INVALID_INPUT` (`safety.py:319-320`).

### 3.4 In-memory state shape

#### `core.market_store.MarketStore` — the process-wide singleton

Created at import: `market_store = MarketStore()` (`core/market_store.py:265`). No lock anywhere in the file, despite the docstring saying "thread-safe" (`:23`); safety relies on the GIL plus whole-entry replacement.

```python
# core/market_store.py:25-33
self._tickers:        Dict[str, dict]   = {}   # set_ticker  :55
self._order_books:    Dict[str, dict]   = {}   # set_order_book :147  (bids AND asks non-empty only)
self._last_prices:    Dict[str, float]  = {}   # set_price   :39
self._price_ts:       Dict[str, float]  = {}   # set_price   :47
self._price_history:  Dict[str, deque]  = {}   # set_price   :49-53  deque(maxlen=PRICE_HISTORY_MAX)
self._live_candles:   Dict[str, dict]   = {}   # set_live_candle :187
self._funding:        Dict[str, float]  = {}   # set_funding  :211
self._open_interest:  Dict[str, float]  = {}   # set_open_interest :228
self._recent_trades:  Dict[str, list]   = {}   # set_recent_trades :248  (trades[:50])
```

`PRICE_HISTORY_MAX = 240` (`core/market_store.py:19`) — deque of `(timestamp, price)` tuples, ~2 minutes at the 500 ms HUD cadence.

| Field | Value shape | Written by |
|---|---|---|
| `_last_prices[symbol]` | `float > 0`; anything else silently dropped (`set_price` `:41-46`) | WS allMids `:439`, WS candle `:467` (via `set_live_candle` → `core/market_store.py:196`), REST `price_update_loop` |
| `_order_books[symbol]` | `{"bids": [[px, sz], …], "asks": [[px, sz], …], "coin", "timestamp"}` from `_parse_book` `data/hyperliquid_feed.py:313` | `set_order_book` `:147`; **only stored when both sides are non-empty** |
| `_price_history[symbol]` | `deque[(float ts, float px)]`, `maxlen=240` | `set_price` `:49-53` |
| `_live_candles[symbol]` | `_parse_candle` dict `data/hyperliquid_feed.py:353` | `set_live_candle` `:187` |
| `_recent_trades[symbol]` | list, hard-capped 50 | `set_recent_trades` `:248` |

**Symbol matching is inconsistent by design.** Every getter except `get_ticker` matches on the base segment `symbol.split("/")[0].split(":")[0].upper()` (exact equality, e.g. `:83-87`); `get_ticker` uses substring `base in k.upper()` (`:70`) — the one place `'NEAR'` could false-match.

**Never fabricates.** Unset fields return `None` or `[]`. `get_order_book` returns `None` with no synthetic depth (`:152-165` docstring). `get_price_change` returns `None` when fewer than 2 samples in the window (`:133-134`).

#### `core.microstructure._KERNEL` — the process-global kernel

```python
_KERNEL: MicrostructureKernel = PythonKernel()   # core/microstructure.py:182
```

Facade functions every caller uses: `ingest_l2` `:218`, `order_flow_imbalance` `:223`, `depth_imbalance` `:230`, `reset_microstructure` `:235`, `get_kernel` `:185`, `register_kernel` `:190`, `initialize_native_kernel` `:309`.

Identity is a **boot-time-only invariant** — `data/hyperliquid_feed.py:513` calls `_ensure_native_kernel()` before the `while self._running:` loop, with the comment that swapping mid-stream would put two different OFI number streams into one pipeline.

The only production writer is `data/hyperliquid_feed.py:458`. Readers:
- `analysis/probability_engine.py:89-90` with a symbol → uses the active kernel;
- `analysis/probability_engine.py:85-87` **without** a symbol → builds a throwaway `PythonKernel()` keyed `"__ad_hoc__"`, bypassing the native kernel entirely;
- `agents/direction_agents.py:125` `OrderFlowAgent._evaluate` — passes `symbol=` so the book also lands in kernel state, abstains on the `(0.0, 0.0)` sentinel (`:126-127`);
- `dashboard/callbacks/update_callbacks.py:1311` — consumes only `rel_spread`, never `ofi`, to display half-spread slippage.

#### `EventBus` — `core/event_bus.py`

```python
self._channels: Dict[str, List[asyncio.Queue]] = {}   # per-channel subscriber queues
self._lock = asyncio.Lock()                           # publish :65
```

`Event` (`:17-23`): `channel: str`, `data: Any`, `source: str = ""`, `timestamp: str` (default `datetime.now(...)`).

`publish` (`:62-84`): builds the `Event`, copies the subscriber list under the lock (released *before* the put loop, so it is a snapshot), then `put_nowait` into each queue. On `asyncio.QueueFull` it **discards the oldest** event and retries — a slow subscriber loses the OLDEST, not the newest. `maxsize=1000` (`:37`).

Channel usage — only 6 of 10 declared channels have any traffic. **Channel names verified verbatim from `core/event_bus.py:92-104`:**

| Channel | Const | Publisher | Subscriber |
|---|---|---|---|
| `price_update` | `:95` | `data/price_feed.py:401` | `ExecutionAgent` (`execution_agent.py:48`) |
| `news_sentiment` | `:96` | `agents/news_agent.py:137` | — |
| `market_analysis` | `:97` | `agents/analysis_agent.py:188` | `DecisionAgent` (`decision_agent.py:49-50`) |
| `trade_decision` | `:98` | `agents/decision_agent.py:274` | `ExecutionAgent` (`execution_agent.py:74`) |
| `trade_executed` | `:99` | `trading/paper_engine.py:657` | — |
| `position_update` | `:100` | `trading/position_manager.py:129, 273, 408` | `DecisionAgent` (`decision_agent.py:50`) |
| `balance_update` | `:101` | **none** | **none** |
| `agent_log` | `:102` | **none** | **none** |
| `system_event` | `:103` | **none** | **none** |
| `direction_ensemble` | `:104` | **none** | **none** — the snapshot reaches the HUD via SQLite, not the bus |

`unsubscribe` (`:52`) and `get_channel_stats` (`:86`) have zero callers; queues live for the process lifetime.

#### Other in-memory state that participates in the flow

| Store | Shape | Line |
|---|---|---|
| `PaperTradingEngine._last_prices` | `Dict[str, float]`, **no timestamp, never evicted** | `trading/paper_engine.py:68` |
| `PaperTradingEngine._candle_cache` | `Dict[str, list]` of 1m candles, read by the sync volatility bridge | `trading/paper_engine.py:66`, filled `:119` / `:151` |
| `PaperTradingEngine._candle_refresh_task` | fire-and-forget `asyncio.Task`, never awaited or cancelled | `trading/paper_engine.py:67`, `:135` |
| `RiskManager._initial_balance` | injected once from the DB at engine init | `trading/paper_engine.py:86-88` |
| `DecisionAgent._symbol_cooldowns` / `_symbol_loss_streak` / `_global_loss_streak` | `Dict[str, float]` absolute expiry / `int` per symbol / one unused global `int` | `agents/decision_agent.py:46`, mutated `:77-82` |
| `BaseAgent._cycle_count` | `int`, in-memory only → cycle ids reset on restart | `agents/base_agent.py:30`, `:79` |
| `analysis.volatility._ATR_CACHE` | `Dict[(symbol, int(time.time()//5)), dict]`, `.clear()`ed wholesale at 256 entries | `analysis/volatility.py:152-154` |
| `analysis.volatility._CANDLE_SOURCE` | process-global sync callback, installed by the paper engine | `analysis/volatility.py:222`, set `trading/paper_engine.py:131` |
| `LiveEngine.positions` | `Dict[str, LivePosition]`, in-memory only, lost on restart | `trading/live/engine.py:56-65` |
| `SafetyGate.engaged` | sticky in-process kill-switch latch, **no releaser in the repo** | `trading/live/safety.py:177` |
| `SafetyGate.counters` (`DayCounters`) | `day_utc, orders_sent, realized_pnl, consecutive_errors, _unreadable` | `trading/live/safety.py:69-101` |
| `_neural_msg_prev` / `_neural_last_fig` | module globals in the callbacks module, per-tick deltas and last-good figure | `dashboard/callbacks/update_callbacks.py:49, 54` |

### 3.5 Persistence schema

Single file, single driver. `config.database_path` → `"data_store/trading_bot.db"` (`core/config.py:386`, overridden `config.yaml:104`). Driver is `aiosqlite` only. `SCHEMA_SQL` is a module-level string executed unconditionally on **every** `connect()` (`database/db.py:191`) — every statement is `CREATE TABLE IF NOT EXISTS`, so there is no migration system: adding a column to `SCHEMA_SQL` is a no-op on an existing file. WAL + `busy_timeout = 15000` (`:174`, `:187`).

Ten tables, verbatim from `database/db.py:13-156`:

```sql
CREATE TABLE IF NOT EXISTS candles (
    id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, timeframe TEXT NOT NULL,
    timestamp INTEGER NOT NULL, open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL,
    close REAL NOT NULL, volume REAL NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(symbol, timeframe, timestamp));                        -- :13-25
CREATE INDEX IF NOT EXISTS idx_candles_lookup ON candles(symbol, timeframe, timestamp);

CREATE TABLE IF NOT EXISTS positions (                             -- :26-42
    id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, side TEXT NOT NULL,
    entry_price REAL NOT NULL, quantity REAL NOT NULL, leverage INTEGER NOT NULL DEFAULT 1,
    margin REAL NOT NULL, liquidation_price REAL NOT NULL,
    stop_loss REAL, take_profit REAL, unrealized_pnl REAL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'OPEN',
    opened_at TEXT DEFAULT (datetime('now')), closed_at TEXT, close_price REAL,
    realized_pnl REAL, close_reason TEXT, reasoning TEXT);
CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status, symbol);

CREATE TABLE IF NOT EXISTS trades (                               -- :43-56
    id INTEGER PRIMARY KEY AUTOINCREMENT, position_id INTEGER REFERENCES positions(id),
    symbol TEXT NOT NULL, side TEXT NOT NULL, price REAL NOT NULL, quantity REAL NOT NULL,
    fee REAL NOT NULL DEFAULT 0, fee_type TEXT DEFAULT 'TAKER',
    trade_type TEXT NOT NULL, executed_at TEXT DEFAULT (datetime('now')));
-- NB: `trades` has NO pnl column. All P&L lives on positions.

CREATE TABLE IF NOT EXISTS signals (...)                          -- :57-71
CREATE TABLE IF NOT EXISTS news (...)                             -- :72-86
CREATE TABLE IF NOT EXISTS agent_logs (...)                       -- :87-97
CREATE TABLE IF NOT EXISTS account (                              -- :98-113
    id INTEGER PRIMARY KEY AUTOINCREMENT, balance REAL NOT NULL, initial_balance REAL NOT NULL,
    total_pnl REAL DEFAULT 0, total_trades INTEGER DEFAULT 0,
    winning_trades INTEGER DEFAULT 0, losing_trades INTEGER DEFAULT 0,
    max_drawdown REAL DEFAULT 0, peak_balance REAL, sharpe_ratio REAL, profit_factor REAL,
    updated_at TEXT DEFAULT (datetime('now')));

CREATE TABLE IF NOT EXISTS balance_history (                      -- :114-123
    id INTEGER PRIMARY KEY AUTOINCREMENT, balance REAL NOT NULL,
    unrealized_pnl REAL DEFAULT 0, equity REAL NOT NULL,
    timestamp TEXT DEFAULT (datetime('now')));
CREATE INDEX IF NOT EXISTS idx_balance_time ON balance_history(timestamp);

-- Snapshot arah LONG/SHORT hasil ensemble multi-agen.                 -- :125-130 (comment)
-- HUD dan DecisionAgent membaca baris TERBARU yang sama, sehingga tampilan
-- dan keputusan trade tidak mungkin berbeda sumber.
CREATE TABLE IF NOT EXISTS direction_snapshots (                  -- :131-145
    id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL,
    prob_long REAL NOT NULL, prob_short REAL NOT NULL,
    direction TEXT NOT NULL, confidence REAL NOT NULL, z_composite REAL DEFAULT 0,
    agent_breakdown TEXT,      -- JSON: verdict per agen
    diffusion TEXT,            -- JSON: time_horizons + long_probs + short_probs
    created_at TEXT DEFAULT (datetime('now')));
CREATE INDEX IF NOT EXISTS idx_dir_snap_symbol ON direction_snapshots(symbol, id);

CREATE TABLE IF NOT EXISTS macro_data (...)                       -- :146-155  UNIQUE(indicator, period)
```

Table → producer → consumer map for the flow traced here:

| Table | Sole/primary writer | Flow readers |
|---|---|---|
| `candles` | `Repository.insert_candles_batch` `:62` (UPSERT), via `data/price_feed.py:267` | `TechnicalAgent._load_closes` `agents/direction_agents.py:331` (own sync connection); `paper_engine._register_candle_source` `:98`; `update_mini_candlestick_and_tape` `update_callbacks.py:414` |
| `positions` | `insert_position` `:110`, `update_position_pnl` `:122`, `close_position` `:147`, `liquidate_position` `:171` | `update_positions` `position_manager.py:326`; every HUD callback; `get_daily_realized_pnl` `:526` |
| `trades` | `insert_trade` `:204` | `close_position` re-reads the OPEN fee from it (`position_manager.py:196-200`); `update_trade_stream_and_marquee` `update_callbacks.py:1057` |
| `account` | `apply_balance_delta` `:403` (atomic `balance = balance + ?`), `bump_peak_balance` `:427` (`MAX(peak, ?)`), `update_account_stats` `:356` | `validate_trade` inputs (`paper_engine.py:566-573`); all HUD panels |
| `balance_history` | `insert_balance_snapshot` `:443` from `take_balance_snapshot` `position_manager.py:436` (60 s job, `run.py:583`) | `update_equity_area` `update_callbacks.py:1002` |
| `direction_snapshots` | `insert_direction_snapshot` `:460` — called from exactly one site, `agents/direction_agents.py:445` | `DecisionAgent._generate_scalp_signals` `decision_agent.py:304`; `update_probability_scanner` `update_callbacks.py:799-805` |
| `agent_logs` | `insert_agent_log` `:296` — 5 `TRADE_REJECTED` sites in `paper_engine.py` (`:413, 460, 501, 581, 627`), `TRADE_EXECUTED` `:647`, `TRADE_CLOSED` `:712/757`; every `BaseAgent` cycle `:132` | `update_trade_stream_and_marquee` |
| `signals`, `news`, `macro_data` | `AnalysisAgent.act` `analysis_agent.py:185`; `NewsAgent.act` `news_agent.py:119`; `upsert_macro` `:551` | HUD legacy panels; `FundamentalAnalyzer` |

Three properties of this layer that shape the flow:

- **One commit per Repository method.** A close is therefore two transactions: `UPDATE positions` (`:168`) then `INSERT trades` (`:204`). A crash between them leaves a CLOSED position with no trade row.
- **Claim-once is in SQL, not in Python.** `close_position` and `liquidate_position` both carry `WHERE id = ? AND status = 'OPEN'` and return `cursor.rowcount > 0`. Four concurrent close paths race on the same row (`update_positions` SL/TP `position_manager.py:357/:363`, `_scalp_take_profit` `execution_agent.py:311`, `_auto_close_expired` `:270`, DecisionAgent CLOSE `decision_agent.py:157-164`); the loser gets `None`, treated as benign, not failure.
- **`get_candles` returns `ORDER BY timestamp DESC`** (`repository.py:92`) — newest first. Every consumer reverses in Python. The same trap applies to `ORDER BY … ASC LIMIT n`, which returns the **oldest** n rows.

**Three out-of-band SQLite readers exist outside the `Database` abstraction**, none of which uses `Repository`:

| Reader | Line | Why |
|---|---|---|
| `agents/direction_agents._load_closes` | `agents/direction_agents.py:341` | `sqlite3.ProgrammingError` when the aiosqlite connection crosses a thread; the TechnicalAgent path runs in `asyncio.to_thread` (`:210`) |
| `dashboard/callbacks/update_callbacks._get_sync_db` | `update_callbacks.py:64` | Dash dispatches callbacks on its own threads; opens a fresh `sqlite3.connect` per invocation and closes only on the success path |
| `run.py::_telemetry_loop` | `run.py:633-641` | raw `SELECT * FROM account` / `positions` via `get_db().fetchone/fetchall` |

Plus one non-SQLite persistent file: `data_store/live_counters.json` — `DayCounters.save` `safety.py:127-140`, atomic temp+`replace()`, only written when `state_path` was passed or `TRADEBOT_LIVE` is truthy (`:184`). `run.py:273` passes no `state_path`.

### 3.6 One tick of the execution loop

"Live loop" here means the always-running asyncio loop of the bot, not live-money trading. The engine driven is whichever object `run.py:362-368` bound.

```
run.py::_execution_loop                 run.py:587      every 0.3 s (sleep at :598)
   │
   ├─ await execution_agent.run_cycle()                     :593
   │     BaseAgent.run_cycle                base_agent.py:77
   │       ├─ sense()                                       execution_agent.py:52
   │       │    drain _price_queue  → engine.update_price  :64
   │       │    market_store.get_all_prices → update_price  :71
   │       │    drain _decision_queue → pending_orders      :73-78
   │       ├─ think()                                       :83
   │       │    engine.get_price(symbol)                    :96   ← AttributeError in live mode
   │       │    volatility.get_dynamic_tp_sl_thresholds     :119
   │       │    risk_manager.calculate_stop_loss / _take_profit  :139-140  (ADVISORY)
   │       └─ act()                                         :158
   │            engine.execute_order(order)                 :164
   │              ├─ PAPER  paper_engine.py:181
   │              │           price = get_price()           :191
   │              │           _execute_open / _execute_close  :197 / :200
   │              └─ LIVE   executor.py:66
   │                        _open :77 / _close :127
   │                        engine.submit_order  engine.py:204
   │                          gate.can_send      safety.py:347
   │                          set_leverage      engine.py:282   BEFORE the order
   │                          place_limit_order engine.py:296
   │                          _attach_protection engine.py:351  (exchange-side SL/TP)
   │            _log_cycle → INSERT agent_logs             base_agent.py:132
   │            any Exception → AgentLog row, NEVER re-raised   :112-117
   │            CancelledError → re-raised deliberately          :99-110
   │
   ├─ await execution_agent.check_positions()                 :595
   │     1 _protect_breakeven   :194  → repo.update_position_sl_tp  repository.py:129
   │          MUST precede 2; breakeven_trigger_pct(0.0020) MUST be < min_profit_pct(0.0060)
   │     2 _scalp_take_profit   :279  → close_position   :311   reason "SCALP_TP"
   │     3 _auto_close_expired  :243  → close_position   :270   reason "SCALP_EXPIRED"
   │     4 engine.check_positions()  :192   ← AttributeError in live mode
   │          paper_engine.py:822
   │            prices = market_store.get_all_prices(); prices.update(self._last_prices)
   │            position_manager.update_positions(prices)   position_manager.py:326
   │              per open position:
   │                price = prices[s] or market_store.get_price(s)      :334-338
   │                calculate_pnl → update_position_pnl EVERY TICK     :342-346
   │                LIQ?  → _liquidate,        continue                :350-354
   │                SL?   → close_position,    continue                :356-360
   │                TP?   → close_position,    continue                :362-364
   │                (precedence LIQ > SL > TP; all inclusive comparisons)
   │
   ├─ except Exception → logger.error("Error pada execution loop")   :596-597
   │     (loop cannot die; it also cannot report that it is degraded)
   └─ await asyncio.sleep(0.3)                                    :598
```

Concurrent writers inside the same tick window, all sharing one `aiosqlite` connection: APScheduler's 11 jobs (`run.py:487-585`, all `max_instances=1`), the 30 s candle refresh (`run.py:600-626`), the 1.5 s price safety net (`data/price_feed.py:566`), the 60 s balance snapshot, and the WebSocket receive loop. `busy_timeout = 15000` (`database/db.py:187`) exists because of this contention.

### 3.7 One paper fill

```
DecisionAgent.act                         agents/decision_agent.py:257
   Order(symbol, action, side, leverage,
         stop_loss=None, take_profit=None)          :264-272   ← no quantity, no stops
   publish(Channels.TRADE_DECISION, {...})         :274 → event_bus.py:62 → put_nowait
        │
ExecutionAgent.sense                        execution_agent.py:52
   self._decision_queue.get_nowait() → pending_orders            :73-78
ExecutionAgent.think                        execution_agent.py:83
   price = engine.get_price(symbol)                               :96
   sl_pct/tp_pct = volatility.get_dynamic_tp_sl_thresholds(...)  :119
   order.stop_loss    = risk_manager.calculate_stop_loss(price, side, sl_pct)   :139
   order.take_profit  = risk_manager.calculate_take_profit(price, side, tp_pct) :140
        │   (advisory — these are recomputed below and never read by the engine)
        ▼
ExecutionAgent.act → engine.execute_order      execution_agent.py:164
        ▼
PaperTradingEngine.execute_order              paper_engine.py:181
   price = self.get_price(order.symbol)                          :191
     └─ get_price :159 → self._last_prices[symbol] if >0
                    else market_store.get_price(symbol) memoized into the same cache
        │   NO slippage, NO spread, NO book walk, NO impact, NO partial fill
        ▼
_execute_open(order, price)                  paper_engine.py:372
   account = repo.get_account(); None → reject                   :374-378
   ── GATE 1 input validation: qty>0, lev>0, px>0                :397-425
        │      (exists specifically to block negative-qty money creation)
        │      reject → agent_log TRADE_REJECTED                  :413
   ── GATE 2 _resolve_tp_sl(symbol, order)                       :270
        │      thresholds = volatility.get_dynamic_tp_sl_thresholds  volatility.py:266
        │        sl_pct = max(dynamic, scalping.tight_sl_pct 0.0025)
        │        tp_pct = max(dynamic, scalping.fast_tp_pct  0.0060)
        │      tp_pct = max(tp_pct, sl_pct * min_risk_reward 1.5)
        │      rejection = volatility.assess_volatility_gate        volatility.py:347
        │        roundtrip = taker*2 = 0.0009; reject if tp<=roundtrip,
        │        or net_tp<net_sl and breakeven_wr>0.65
        │      reject → raise VolatilityGateError  :328
        │           caught :452 → agent_log TRADE_REJECTED  :460
   stop_loss   = calculate_stop_loss(price, side, sl_pct)          :481  ← REBUILT AT FILL
   take_profit = calculate_take_profit(price, side, tp_pct)        :482      (order.* discarded)
   ── GATE 3 _tick_quality_guard(symbol, price, sl_pct)           :495 → :209
        │      (a) market_store.get_price_age  >  max_tick_age_seconds 1.5   :238
        │      (b) statistics.median(get_price_history(3.0s))
        │          |price-median|/median > sl_pct                          :257-266
        │      FAILS OPEN below stale_tick_min_samples = 5                  :252-255
        │      reject → agent_log TRADE_REJECTED                  :501
   ── GATE 4 sizing
        │      calculate_scalp_position_size(balance, entry, leverage)   :519 → risk_manager.py:340
        │        margin = risk_pct*balance = 0.005*balance              :361
        │        capped at balance*0.9/max_open_positions(30) = 3%      :364
        │      estimated_fee = calculate_fee(qty, price, "TAKER")        :556
        │      required_cash = margin + estimated_fee                    :557  (B5)
        │      daily_pnl = repo.get_daily_realized_pnl()                :564 → repository.py:526
        │      equity_now = free_balance + open_margin + open_upnl      :549
        │      validate_trade(balance=free, margin_required, open_positions,
        │                     daily_pnl, peak_balance, equity)         :566 → risk_manager.py:273
        │          margin_required > balance*0.9                        :299
        │          open_positions >= max_open_positions (30)            :303
        │          |daily_pnl| / FROZEN initial_balance >= 0.10          :319-322
        │          (peak_balance - equity)/peak_balance >= 0.20         :330-333
        │      reject → agent_log TRADE_REJECTED                  :581
   ── GATE 5 open_position
        │      margin   = qty*price/leverage                 position_manager.py:67-69
        │      liq_price = calculate_liquidation_price(price, side, leverage)  :70-72
        │      fee       = calculate_fee(qty, price, "TAKER")            :75
        │      apply_balance_delta(-(margin + fee))  → free_balance      :85 → repository.py:403
        │        free_balance < 0 → refund, reject                        :86-92
        │      insert_position(Position(...))                             :107 → repository.py:110
        │        None → refund, reject                                    :109-112
        │      insert_trade(Trade(trade_type='OPEN', fee, fee_type='TAKER'))  :125 → repository.py:204
        │      publish POSITION_UPDATE action='OPENED'                    :128-129
   agent_log TRADE_EXECUTED                                          :647
   publish Channels.TRADE_EXECUTED action='OPEN'                      :656-657
   return {"success": True, "position_id": <id>, ...}
```

Both legs are hardcoded `fee_type="TAKER"` (`position_manager.py:75`, `:181`, `paper_engine.py:556`). `order.order_type` is never consulted, so LIMIT and MARKET fill identically and maker rebates are unreachable in paper. Round-trip cost is `2 × 0.00045 = 0.09%` against a `0.25%` scalp stop.

### 3.8 One dashboard refresh

Two clocks, deliberately split (`dashboard/app.py:52-67`):

```
dcc.Interval id="dashboard-interval"       interval=500    app.py:53   → 16 callbacks
dcc.Interval id="dashboard-slow-interval"  interval=60000  app.py:63   → update_trade_stream_and_marquee only
```

`register_callbacks(app)` binds 19 callbacks (8 legacy compat stubs, 11 live), declared at `update_callbacks.py:114` and called once from `create_dash_app` (`dashboard/app.py:95`).

```
browser 500 ms tick
     │
     ├─ 8× legacy compat callbacks  → all return html.Div() / raise PreventUpdate   (silent absorb)
     │
     └─ 11× live callbacks, each independent, each thread-dispatched by Dash:
          │
          ├─ update_top_bar                     :319   account + positions
          ├─ update_symbol_dropdown             :391   config.symbols (raises PreventUpdate when stable)
          ├─ update_mini_candlestick_and_tape   :414   ─── THE MERGE ───
          │    candles = SELECT … FROM candles WHERE symbol=? AND timeframe='1m'
          │               ORDER BY timestamp DESC LIMIT 400   (DESC then reversed in Python)
          │    live = market_store.get_live_candle(symbol)      market_store.py:198
          │      if live.timestamp already a row  → df.loc[idx, [o,h,l,c,v]] = live  (update in place)
          │      else                            → concat onto the tail
          │    px = market_store.get_price(symbol)              market_store.py:74
          │      if last row IS the current minute → set close only; widen high=max(), low=min()
          │    ema9 = df['close'].ewm(span=9, adjust=False).mean()   (recomputed every tick, never stored)
          │    NO resample, NO ffill — gaps are removed by Plotly rangebreaks, not invented OHLC
          │    create_mini_candlestick_fig(df, symbol)  → go.Figure (height-less; CSS owns height)
          │
          ├─ update_wallet_overview             :629   ─── MONEY FROM SQL, PRICE FROM MEMORY ───
          │    conn = _get_sync_db()                                 :631  (new connect per call)
          │    account  = SELECT * FROM account ORDER BY id DESC LIMIT 1
          │    positions= SELECT * FROM positions WHERE status='OPEN'
          │    closed  = SELECT realized_pnl FROM positions
          │               WHERE status IN ('CLOSED','LIQUIDATED') AND realized_pnl IS NOT NULL
          │    conn.close()                                          :641
          │    for each open position:                               :652-668
          │      cur_p = market_store.get_price(sym) or entry
          │      uPnL   = (cur_p - entry)*qty   [LONG]  |  (entry - cur_p)*qty  [SHORT]
          │                ↑ RECOMPUTED, positions.unrealized_pnl is only the fallback
          │    free_balance   = account['balance']          (cash after margin locks)
          │    wallet_balance = free_balance + total_margin
          │    equity         = wallet_balance + total_upnl
          │    total_pnl      = equity - initial_balance
          │
          ├─ update_positions_grid              :714   same recompute, per-position rows   :744-754
          ├─ update_probability_scanner        :794   ─── THE JOIN ROW, SAME BYTES AS THE AGENT ───
          │    SELECT prob_long, prob_short, direction, confidence,
          │           agent_breakdown, diffusion, created_at
          │      FROM direction_snapshots WHERE symbol = ? ORDER BY id DESC LIMIT 1   :799-805
          │    no row → ("--", "--", "AWAITING", …, create_convergence_fig())
          │                                                 (explicit empty state, never synthetic)
          ├─ update_neural_net                  :913
          │    feed = get_price_feed() (lazy import)   :958
          │    counts = feed.hyperliquid.get_status()['message_counts']
          │    flow_map = PER-TICK DELTA vs _neural_msg_prev, normalised to the busiest channel
          │    activity_map from agent_logs, only if peak_agent >= 5.0          :931-938
          │    create_neural_net_fig(signal_map, activity_map, flow_map)
          │    on ANY exception → return _neural_last_fig, NOT an empty figure  :984-992
          ├─ update_equity_area                 :1002
          │    SELECT * FROM balance_history ORDER BY timestamp DESC LIMIT 300  then [::-1]
          │    (ASC + LIMIT would freeze the curve at the 300 OLDEST rows — :1005-1015)
          │    create_equity_area_fig(balance_history, initial_balance)
          ├─ update_symbol_pnl                  :1347   per-symbol realised PnL from positions
          ├─ update_analytics_and_sparklines    :1209
          │    slippage = (rel_spread/2.0)*100.0 from calculate_order_flow_imbalance(ob)  :1311
          │    rel_spread == 0 → "N/A", never 0.000%                              :1318
          │    account.max_drawdown / sharpe_ratio → "N/A" (never written by any path)
          └─ 60 s clock: update_trade_stream_and_marquee  :1057
               trades ⋈ positions ⋈ agent_logs JOIN — deliberately NOT on the 500 ms clock
```

Steady state: 11 live callbacks × 2 Hz ≈ 22 `sqlite3.connect` per second, each closed only on the success path (`update_callbacks.py:64`, `conn.close()` at `:641`, `:1304`).

What the HUD refuses to do: no synthetic candles, no synthetic order book, no synthetic probabilities. Every absent input has a distinct literal string — `'AWAITING 1M CANDLE INGEST // NO SYNTHETIC DATA'` (`:441-467`), `'AWAITING L2 ORDERBOOK SNAPSHOT // HYPERLIQUID WS'` (`:553-562`), `'BELUM ADA SNAPSHOT'` (`:817`), `'N/A'` for unmeasurable metrics, and `_neural_last_fig` rather than a blank figure.

### 3.9 Load-bearing invariants (each has a test that fails if broken)

| Invariant | Where enforced | Test |
|---|---|---|
| `breakeven_trigger_pct` (0.0020) < `min_profit_pct` (0.0060), else `_scalp_take_profit` fires first and `_protect_breakeven` is dead code | ordering `execution_agent.py:177-192`; boot validator `core/config.py:707-786` | `tests/test_config.py:33` |
| SL/TP derived from the FILL price, never from the agent's precomputed stops | `paper_engine.py:481-482` (the P1→P2 fill drift bug) | `tests/test_fill_price_sl.py:82` |
| `realized_pnl` net of BOTH fees; open fee added back to the credit so it is charged once | `position_manager.py:203`, `:243` (891 USDT lost over 330 positions) | `tests/test_lifecycle_paths.py:209` |
| Margin limit reserves `margin + fee`, not margin alone | `paper_engine.py:556-557` | `tests/test_bugfixes.py:507` |
| Daily-loss denominator is frozen `initial_balance`, not live cash | `risk_manager.py:319` (the breaker must not relax as margin locks) | `tests/test_lifecycle_paths.py:621` |
| Tick guard uses a **median** and **fails open** below 5 samples | `paper_engine.py:252-255` | `tests/test_bugfixes.py:42` |
| Close is claim-once; a lost claim is benign, not failure | `repository.py:147`, `:171` | `tests/test_position_manager.py:183` |
| Direction snapshot must be fresh, and an unparseable timestamp is treated as expired | `decision_agent.py:340-346` | `tests/test_decision_agent_ensemble.py:112` |
| `abstain` ≠ `NEUTRAL` — a data-blind agent is excluded from numerator AND denominator | `direction_ensemble.py:218`; `direction_agents.py:126-127` | `tests/test_direction_ensemble.py:160` |
| Live order sequence is gate → leverage → order → protection (unfilled ⇒ no protection) | `engine.py:219-229`, `:322-336` | `tests/test_live_engine.py:135` |
| C++ and Python kernels are bit-identical on OFI | `kLevelWeight0=1.0` / `kLevelWeightStep=0.1` declared identically on both sides; no `-Ofast` | `tests/test_cpp_kernel.py:37, 324` |
| Every mounted Dash `Output` id exists in the layout (a missing id does not raise — the panel just stops updating) | `dashboard/app.py:71-91` compat subtree + `:38-44` KeyError handler | `tests/test_layout_contract.py:78` |

### 3.10 Failure surfaces in the flow

| Failure | Where caught | Effect on the flow |
|---|---|---|
| WS frame malformed | `_handle_message` `hyperliquid_feed.py:421-424` `except (JSONDecodeError, TypeError): return` | frame dropped silently |
| WS disconnect | `websocket_loop` `:540-548` | reconnect, backoff 1.0 s ×2 capped 30.0 s, reset on connect. The **only** retry in the data layer |
| WS message parse stalls the loop | offloaded `asyncio.to_thread` `:534` | prevents a missed 0.3 s tick |
| Agent cycle raises | `BaseAgent.run_cycle` `base_agent.py:112-117` | AgentLog row, never propagates. `CancelledError` re-raised at `:99-110` |
| Ensemble cycle raises | `direction_agents.py:449-450` (overrides `run_cycle`, so no AgentLog row at all) | snapshot not written → DecisionAgent sees a stale one and skips entry |
| Live Executor interface gap | `run.py:596` | swallowed, logged, loop continues; nothing is ever checked |
| SQLite lock | `busy_timeout=15000` `db.py:187`; comment records 1150 observed failures at 5000 ms | the 0.3 s loop would otherwise die on a decision cycle |
| Order not filled (paper) | `open_position` returns `None` for 3 different reasons; `_diagnose_open_failure` `paper_engine.py:332` re-derives the cause | refund `:88`/`:111` |
| Fill crash between delta and insert | separate commits (`position_manager.py:85` then `:107`) | margin debited with no position row |
| Live order accepted but unfilled | `engine.py:326-336` | protection deliberately skipped (`reduceOnly` would reject it → orphan order) |
| Live fill with no SL attached | `engine.py:397-404` | `logger.critical` + `engage_kill_switch` |
| Live process dies after fill, before protection | nothing | reconcile logs only (`engine.py:114-119`), health_check flags only (`:573-576`), `run.py:279` refuses to start. **The position stays naked** |
| Dashboard callback error | `update_mini_candlestick_and_tape` logs ERROR + `PreventUpdate`; others log DEBUG | last good render stays on screen |

---

## 4. Configuration and constants

### 4.1 The four configuration layers

```
┌─ LAYER 1: config.yaml (214 lines, 16 top-level blocks) ───────────────────┐
│  read ONLY by core/config.py:415 load_config()                          │
│  allow-list of `if "section" in raw` blocks — NOT a recursive walk       │
└──────────────────────────────┬──────────────────────────────────────────┘
                               │ _apply_dict = hasattr() + setattr(), NO TYPE CHECK
                               ▼
┌─ LAYER 2: AppConfig singleton (16 dataclasses) ────────────────────────┐
│  core/config.py:790 _config, lazily built by get_config():793          │
│  mutated in place by tests/test_direction_agents.py:263, no lock        │
└──────────────────────────────┬──────────────────────────────────────────┘
        ┌─────────────────────┼──────────────────────┐
        ▼                     ▼                      ▼
┌─ LAYER 3: ENV VARS (9 read) ─┐ ┌─ CLI FLAGS (7) ─┐ ┌─ LAYER 4: HARDCODED
│  the ONLY real-money gates    │ │  substring match │ │  TUNABLES (no key at
│  safety.py:266,270,277        │ │  no argparse     │ │  all — see 4.6)
└───────────────────────────────┘ └──────────────────┘ └──────────────────────┘
```

**Loader contract, verbatim** (`core/config.py:407-412`) — **re-verified during integration**:

```python
def _apply_dict(obj, data: dict):
    """Terapkan dict ke dataclass, abaikan key yang tidak ada."""
    for key, value in data.items():
        if hasattr(obj, key):
            setattr(obj, key, value)
```

Three consequences:

| Property | Behaviour | Evidence |
|---|---|---|
| Unknown YAML key | Silently discarded, dataclass default stays in force. `risk: {max_dailly_loss: 0.9}` → `risk.max_daily_loss == 0.1` with no warning. | `core/config.py:411` (no `else` branch) |
| Wrong YAML type | Silently accepted. `scanning.top_n: "TEN"` → `scanning.top_n == 'TEN'` (str); the first `top_n + 1` raises `TypeError: can only concatenate str (not "int") to str` at `run.py:324`. | `_apply_dict` has no `isinstance` check |
| Missing `config.yaml` | **Not an error.** `load_config` returns pure dataclass defaults. `get_config()` takes no path argument; the `'config.yaml'` default is CWD-relative. | `core/config.py:419-421` |

Nested `ensemble.agents` needs a two-level apply (`core/config.py:448-457`) because plain `_apply_dict` would replace the `EnsembleAgentConfig` dataclass field with a raw dict. `agents` is `pop`ped off a copy of the block so it never reaches the scalar apply.

### 4.2 Complete `config.yaml` key table

Every row: dataclass default (`core/config.py`), YAML value, the first-party reader with `file:line`, and what changing it actually does. "Dead" rows are cross-referenced in §4.7. The 16 top-level blocks in declaration order are: `account`, `symbols`, `scanning`, `exchange`, `risk`, `fees`, `agent_intervals`, `us_market`, `indicators`, `news_sources`, `database`, `dashboard`, `logging`, `scalping`, `dynamic_tp_sl`, `ensemble`. **There is no `live:` block.**

#### `account:` — config.yaml:6-8

| Key | dataclass default | YAML | Type | Read at | What it changes |
|---|---|---|---|---|---|
| `account.initial_balance` | `10000.0` (config.py:14) | `10000.0` (yaml:7) | float | `trading/paper_engine.py:79` (`repo.init_account`), `:87` (fallback for `risk_manager.set_initial_balance`) | Seeds the paper account row. Also read from SQLite afterwards — this YAML key only matters on a fresh DB. |
| `account.currency` | `"USDT"` (config.py:15) | `"USDT"` (yaml:8) | str | `trading/paper_engine.py:95` | **Display only** — interpolated into one startup log line. No arithmetic reads it. |

#### `symbols:` — config.yaml:11-21

| Key | dataclass default | YAML | Type | Read at | What it changes |
|---|---|---|---|---|---|
| `symbols` | 10 entries (config.py:362-373) | 10 entries (yaml:12-21) | List[str] | `run.py:327` (**overwritten**), `:377`, `:402`, `:409`, `:617`, `:708`; `data/price_feed.py:153`, `:156`, `:560`, `:595` | The traded universe. **Replaced in place at boot** by the top-volume scan: `run.py:327` `self.config.symbols = top_syms`. Every later consumer — WS subscription, candle loops, agent symbol lists — reads the post-scan list. |

#### `scanning:` — config.yaml:24-27

| Key | dataclass default | YAML | Type | Read at | What it changes |
|---|---|---|---|---|---|
| `scanning.dynamic_top_volume` | `True` (config.py:93) | `true` (yaml:25) | bool | `run.py:318`, `run.py:493` | Gates the whole scan. When true, the YAML `symbols` list is discarded and replaced hourly. |
| `scanning.top_n` | `10` (config.py:94) | `10` (yaml:26) | int | `run.py:320`, `:321`, `:324` (startup), `:496` (hourly) | Number of futures pairs retained. Consumed as `discover_top_volume_symbols(limit=…)` — a string here raises `TypeError` downstream. |
| `scanning.refresh_interval` | `3600` (config.py:95) | `3600` (yaml:27) | int | `run.py:503` | Seconds between the `refresh_top_volume` fixed job. |

#### `exchange:` — config.yaml:30-33 — **ENTIRE SECTION IS DEAD**

| Key | dataclass default | YAML | Type | Read at | What it changes |
|---|---|---|---|---|---|
| `exchange.name` | `"binance"` (config.py:86) | `"binance"` (yaml:31) | str | **none** | Nothing. The loader is the only reference (`config.py:461`). Actively misleading: the bot trades Hyperliquid. |
| `exchange.type` | `"future"` (config.py:87) | `"future"` (yaml:32) | str | **none** | Nothing. |
| `exchange.sandbox` | `True` (config.py:88) | `true` (yaml:33) | bool | **none** | Nothing. |

The exchange identity is hardcoded: `data/hyperliquid_feed.py:46-47` pins `HL_REST_URL = "https://api.hyperliquid.xyz/info"` and `HL_WS_URL = "wss://api.hyperliquid.xyz/ws"`; `data/price_feed.py:109-130` constructs the Hyperliquid Tier-0 client and a ccxt binance-future client unconditionally. The `fees:` block comment (config.yaml:58-60) explicitly says the rates are Hyperliquid base tier, **not** Binance — so the `exchange:` and `fees:` blocks contradict each other on the same page.

#### `risk:` — config.yaml:36-42

| Key | dataclass default | YAML | Type | Read at | What it changes |
|---|---|---|---|---|---|
| `risk.max_risk_per_trade` | `0.02` (config.py:20) | `0.005` (yaml:37) | float | `trading/risk_manager.py:92` (fixed-fractional sizing), `:354` (scalp sizing); `analysis/vol_target.py:95` (dead module) | Fraction of balance risked per trade → position size. |
| `risk.max_leverage` | `20` (config.py:21) | `50` (yaml:38) | int | `trading/risk_manager.py:97`, `:358` | Clamps leverage **inside the two sizing functions only**. `trading/paper_engine.py:604` still forwards the raw `order.leverage` to `position_manager.open_position`, so the stored margin and liquidation price are computed from an unclamped value. |
| `risk.max_daily_loss` | `0.05` (config.py:22) | `0.10` (yaml:39) | float | `trading/risk_manager.py:322` | Daily-loss circuit breaker. Fed real data: `trading/paper_engine.py:566` `daily_pnl = await repo.get_daily_realized_pnl()` → `validate_trade` at `:569`. Denominator is the **frozen** `_initial_balance` (`risk_manager.py:319`), not live cash. |
| `risk.max_drawdown` | `0.15` (config.py:23) | `0.20` (yaml:40) | float | `trading/risk_manager.py:332` | Drawdown breaker, measured on `equity` (`paper_engine.py:549` = free + margin + unrealized) against `peak_balance` (`position_manager.py:256` bumps from cash + margin only — a different measure). |
| `risk.max_open_positions` | `3` (config.py:24) | `30` (yaml:41) | int | `trading/risk_manager.py:303` (breaker), `:364` (per-position margin divisor); `agents/decision_agent.py:119` | Simultaneous position ceiling. Also divides the per-position margin cap: `balance * 0.9 / 30` = 3% of cash per trade. |
| `risk.default_leverage` | `5` (config.py:25) | `10` (yaml:42) | int | `agents/decision_agent.py:375` (`_determine_leverage`); printed at `run.py:150` | Baseline leverage. `_determine_leverage` returns `max(2, default//2)` for HIGH risk, `max(3, default*3//4)` for MEDIUM, else `default` — so 10 / 5 / 7 with current config. The `2`, `3` floors are bare literals. |

#### `fees:` — config.yaml:58-60

| Key | dataclass default | YAML | Type | Read at | What it changes |
|---|---|---|---|---|---|
| `fees.maker` | `0.0002` (config.py:30) | `0.00015` (yaml:59) | float | `trading/risk_manager.py:215` | Maker rate. **Unreachable in paper**: both position legs hardcode `fee_type="TAKER"` (`position_manager.py:75`, `:181`; `paper_engine.py:556`), and `Order.order_type` is never consulted. |
| `fees.taker` | `0.0005` (config.py:31) | `0.00045` (yaml:60) | float | `trading/risk_manager.py:215`; `core/config.py:602`, `:734` (boot validators); `analysis/volatility.py:383`; `_econ.py:18`; `_econ3.py:15`; `check_behavior.py:149` | Round-trip cost `= taker * 2` = **0.09%**. Feeds every economic validator, so raising it can turn a previously-valid config into a boot crash. |

#### `agent_intervals:` — config.yaml:63-73

| Key | dataclass default | YAML | Type | Read at | What it changes |
|---|---|---|---|---|---|
| `news_agent` | `300` (config.py:36) | `300` (yaml:64) | int | `run.py:510` (`add_agent_job` `interval_normal`) | NewsAgent cycle, normal hours. |
| `news_agent_us_open` | `120` (config.py:37) | `120` (yaml:65) | int | `run.py:511` | Retimed while `is_us_market_open()`. |
| `analysis_agent` | `300` (config.py:38) | `15` (yaml:66) | int | `run.py:519` | 20× faster than the dataclass default. |
| `analysis_agent_us_open` | `180` (config.py:39) | `10` (yaml:67) | int | `run.py:520` | |
| `decision_agent` | `60` (config.py:40) | `3` (yaml:68) | int | `run.py:528` | 20× faster than the dataclass default. Registered with `start_immediately=False` (`run.py:530`) because the 0.3 s `_execution_loop` already drives it. |
| `decision_agent_us_open` | `30` (config.py:41) | `2` (yaml:69) | int | `run.py:529` | |
| `finbert_batch` | `900` (config.py:42) | `900` (yaml:70) | int | `run.py:574` | FinBERT batch job period. |
| `macro_data` | `21600` (config.py:43) | `21600` (yaml:71) | int | `run.py:570` | FRED/ForexFactory refresh period. |
| `balance_snapshot` | `300` (config.py:45) | `60` (yaml:73) | int | `run.py:583` | `take_balance_snapshot` job period. |
| `funding_rate` | `28800` (config.py:44) | `28800` (yaml:72) | int | **none — DEAD** | Never passed to `add_fixed_job`. `run.py:487-585` registers 3 `add_agent_job` + 7 `add_fixed_job`; `funding_rate` is in none of them. Funding data is pulled opportunistically by `data/price_feed.py:417 fetch_funding_rate`, not on a schedule. |

#### `us_market:` — config.yaml:76-80

| Key | dataclass default | YAML | Type | Read at | What it changes |
|---|---|---|---|---|---|
| `us_market.timezone` | `"US/Eastern"` (config.py:50) | `"US/Eastern"` (yaml:77) | str | `core/scheduler.py:21` → `pytz.timezone(cfg.timezone)` | Session clock. |
| `us_market.open_hour` | `9` (config.py:51) | `9` (yaml:78) | int | `core/scheduler.py:30` | |
| `us_market.open_minute` | `30` (config.py:52) | `30` (yaml:79) | int | `core/scheduler.py:30` | |
| `us_market.close_hour` | `16` (config.py:53) | `16` (yaml:80) | int | `core/scheduler.py:31` | **There is no `close_minute` field.** `core/scheduler.py:31` computes `close_minutes = cfg.close_hour * 60`, so the boundary is 16:00 sharp. Session window is `[09:30, 16:00)`, weekdays only (`weekday() >= 5` → closed, `scheduler.py:27`). |

#### `indicators:` — config.yaml:83-91

| Key | dataclass default | YAML | Type | Read at | What it changes |
|---|---|---|---|---|---|
| `indicators.rsi_period` | `14` (config.py:58) | `14` (yaml:84) | int | `analysis/technical.py:32`, `:56` | |
| `indicators.macd_fast` | `12` (config.py:59) | `12` (yaml:85) | int | `analysis/technical.py:33`, `:62` | |
| `indicators.macd_slow` | `26` (config.py:60) | `26` (yaml:86) | int | `technical.py:52` bails out returning the input df unchanged if `len(df) < macd_slow + macd_signal` (= 35). | |
| `indicators.macd_signal` | `9` (config.py:61) | `9` (yaml:87) | int | `analysis/technical.py:35`, `:62` | |
| `indicators.bollinger_period` | `20` (config.py:62) | `20` (yaml:88) | int | `analysis/technical.py:36` | |
| `indicators.bollinger_std` | `2` (config.py:63) | `2` (yaml:89) | int | `analysis/technical.py:37` | |
| `indicators.ema_short` | `9` (config.py:64) | `9` (yaml:90) | int | `analysis/technical.py:38`, `:76` | |
| `indicators.ema_long` | `21` (config.py:65) | `21` (yaml:91) | int | `analysis/technical.py:39`, `:77` | |

`ml/trainer.py:48-58` **hardcodes** the same indicator lengths (rsi 14, macd 12/26/9, bbands 20/2, ema 9/21) rather than reading `IndicatorConfig`. Changing a MACD key desynchronises the trainer from the live feature distribution with no error anywhere.

#### `news_sources:` — config.yaml:94-100

| Key | dataclass default | YAML | Type | Read at | What it changes |
|---|---|---|---|---|---|
| `news_sources.rss` | 2 URLs (config.py:400-403) | 2 URLs (yaml:96-97) | List[str] | `data/news_fetcher.py:35` | RSS feeds polled by `fetch_all`. |
| `news_sources.cryptopanic.base_url` | `https://cryptopanic.com/api/free/v1/posts/` (config.py:404) | same (yaml:99) | str | `data/news_fetcher.py:90` | Query base; token appended. |
| `news_sources.cryptopanic.token` | `None` (config.py:405) | **commented out** (yaml:100) | Optional[str] | `data/news_fetcher.py:84` | With no token, `fetch_cryptopanic` is skipped entirely. `core/config.py:522` also requires the value be truthy, so an empty string is treated as absent. |

#### `database:` — config.yaml:103-116

| Key | dataclass default | YAML | Type | Read at | What it changes |
|---|---|---|---|---|---|
| `database.path` | `"data_store/trading_bot.db"` (config.py:386, top-level `AppConfig.database_path`) | same (yaml:104) | str | `core/config.py:505-506` → `database/db.py:162` | The single SQLite file. `db.py:169` `mkdir(parents=True, exist_ok=True)` on the parent. This is the only key that bypasses the dataclass tree entirely. |
| `database.snapshot_prune_interval` | `3600` (config.py:392, top-level) | `3600` (yaml:108) | int | `run.py:546` (`max(60, ...)`), `run.py:478` | `direction_snapshot_prune` job period. Reaches AppConfig via the `startswith("snapshot_")` filter at `config.py:509-511`. |
| `database.snapshot_keep_per_symbol` | `120` (config.py:393, top-level) | `120` (yaml:109) | int | `run.py:445` | Newest-N-per-symbol retention (N≈10 min at the 5 s ensemble interval). Same filter. |
| `database.agent_log_prune_interval` | `1800` (config.py:398, top-level) | `1800` (yaml:115) | int | `run.py:466` via `getattr(..., 1800)` | **The YAML value never reaches AppConfig** — see §4.7.1. The `getattr` fallback supplies the number. |
| `database.agent_log_keep` | `5000` (config.py:399, top-level) | `5000` (yaml:116) | int | `run.py:464` via `getattr(..., 5000)` | Same defect. |

#### `dashboard:` — config.yaml:119-123

| Key | dataclass default | YAML | Type | Read at | What it changes |
|---|---|---|---|---|---|
| `dashboard.host` | `"127.0.0.1"` (config.py:70) | `"127.0.0.1"` (yaml:120) | str | `dashboard/app.py:130` → `app.run(host=…)`, `:139` log line | Dash bind address. |
| `dashboard.port` | `8050` (config.py:71) | `8050` (yaml:121) | int | `dashboard/app.py:130` → `app.run(port=…)`, `:139` | Dash port. |
| `dashboard.debug` | `False` (config.py:72) | `false` (yaml:122) | bool | `dashboard/app.py:142` | Flask debug mode. True exposes the interactive debugger. |
| `dashboard.update_interval` | `2000` (config.py:73) | `2000` (yaml:123) | int | **none — DEAD** | `dashboard/app.py:53` hardcodes `interval=500` and `:63` hardcodes `interval=60000`. README:118 documents the real hardcoded values correctly, so this key is pure noise. |

#### `logging:` — config.yaml:126-130

| Key | dataclass default | YAML | Type | Read at | What it changes |
|---|---|---|---|---|---|
| `logging.level` | `"INFO"` (config.py:78) | `"INFO"` (yaml:127) | str | `core/logger.py:157`, `:164` | `logger.setLevel(getattr(logging, cfg.level.upper(), logging.INFO))` — an unrecognised name falls back to INFO rather than raising. |
| `logging.file` | `"data_store/logs/trading_bot.log"` (config.py:79) | same (yaml:128) | str | `core/logger.py:190` | `mkdir(parents=True, exist_ok=True)` then `RotatingFileHandler`. |
| `logging.max_bytes` | `10485760` (config.py:80) | `10485760` (yaml:129) | int | `core/logger.py:193` | Rotation threshold, 10 MB. |
| `logging.backup_count` | `5` (config.py:81) | `5` (yaml:130) | int | `core/logger.py:194` | Retained rotated files. |

#### `scalping:` — config.yaml:133-162

| Key | dataclass default | YAML | Type | Read at | What it changes |
|---|---|---|---|---|---|
| `scalping.enabled` | `True` (config.py:100) | `true` (yaml:134) | bool | `trading/paper_engine.py:517`; `core/config.py:722` (validator short-circuit) | When false, `paper_engine.py:476-479` hardcodes `sl_pct=0.02 / tp_pct=0.04`, bypassing every other scalping key, the volatility gate's ATR context, and the tick guard's 0.25 % deviation threshold. |
| `scalping.min_profit_pct` | `0.0060` (config.py:104) | `0.0060` (yaml:135) | float | `agents/execution_agent.py:309`; `analysis/volatility.py:328` | `_scalp_take_profit` close threshold; also the floor for the dynamic TP. |
| `scalping.max_hold_seconds` | `300` (config.py:105) | `300` (yaml:136) | int | `agents/execution_agent.py:262` | Auto-close (`SCALP_EXPIRED`) age limit. |
| `scalping.min_hold_seconds` | `15` (config.py:106) | `15` (yaml:137) | int | `agents/decision_agent.py:148`; `agents/execution_agent.py:258`, `:296` | Blocks both reversal-close and expiry-close inside the first 15 s. |
| `scalping.batch_size` | `2` (config.py:107) | `2` (yaml:138) | int | `agents/decision_agent.py:120` | Max new positions per decision cycle. |
| `scalping.fast_tp_pct` | `0.0060` (config.py:109) | `0.0060` (yaml:139) | float | `agents/decision_agent.py:234`; `agents/execution_agent.py:129`, `:136`; `analysis/volatility.py:296`; `trading/paper_engine.py:298` | Static TP floor. |
| `scalping.tight_sl_pct` | `0.0025` (config.py:110) | `0.0025` (yaml:140) | float | `agents/decision_agent.py:233`; `agents/execution_agent.py:125`, `:133`; `analysis/volatility.py:295`; `trading/paper_engine.py:297` | Static SL floor. Also the tick-quality guard's deviation threshold (`paper_engine.py:239`) — a fill deviating more than the SL distance from the median is rejected as already-stopped. |
| `scalping.min_confidence` | `0.40` (config.py:111) | `0.40` (yaml:141) | float | `agents/decision_agent.py:201` (legacy AnalysisAgent path), `:320` (ensemble snapshot path); `core/config.py:757` (validator) | Gates **OPEN**. |
| `scalping.reversal_close_threshold` | `0.70` (config.py:118) | `0.70` (yaml:146) | float | `agents/decision_agent.py:139` `getattr(self.scalp, "reversal_close_threshold", 0.70)` | Gates **CLOSE/reversal**. The inline `0.70` literal duplicates the dataclass — a future edit to config.py:118 alone leaves the `getattr` fallback stale. Validator requires `min_confidence <= this < 1.0` (`config.py:757-763`). |
| `scalping.max_tick_age_seconds` | `1.5` (config.py:134) | `1.5` (yaml:153) | float | `trading/paper_engine.py:238` `getattr(scalp, "max_tick_age_seconds", None)` | Tick age ceiling. `None` default disables the check. |
| `scalping.stale_tick_window_seconds` | `3.0` (config.py:135) | `3.0` (yaml:154) | float | `trading/paper_engine.py:247` `getattr(..., None)` | Median window. `None` disables the deviation check. |
| `scalping.stale_tick_min_samples` | `5` (config.py:136) | `5` (yaml:155) | int | `trading/paper_engine.py:248` `getattr(..., 5)` | Below this the guard **fails open** (`paper_engine.py:254-255`) — deliberate, so a newly-connected feed does not silence the bot. |
| `scalping.orderbook_imbalance_threshold` | `0.60` (config.py:139) | `0.60` (yaml:156) | float | **none — DEAD** | Zero readers anywhere, including tests. |
| `scalping.momentum_threshold` | `0.0008` (config.py:140) | `0.0008` (yaml:157) | float | `agents/direction_agents.py:174` `max(float(self.config.scalping.momentum_threshold), 1e-6)` | MomentumAgent 30 s return threshold. |
| `scalping.max_spread_pct` | `0.0006` (config.py:141) | `0.0006` (yaml:158) | float | `agents/decision_agent.py:212` | Entry spread guard; the real spread is read from `market_store.get_order_book`, with a `1.0` fallback when the book is missing (`decision_agent.py:211`). |
| `scalping.breakeven_trigger_pct` | `0.0020` (config.py:148) | `0.0020` (yaml:159) | float | `agents/execution_agent.py:206`; `core/config.py:727` (validator) | Profit at which the SL slides to breakeven. **Must stay below `min_profit_pct`** or `_scalp_take_profit` closes first and the whole breakeven path is dead code. |
| `scalping.breakeven_offset_pct` | `0.0015` (config.py:149) | `0.0015` (yaml:160) | float | `agents/execution_agent.py:207`; `core/config.py:745` (validator) | SL placed at `entry*(1±offset)`. Must exceed the round-trip fee (`0.0009`) or "breakeven" still loses. |
| `scalping.cooldown_after_close_seconds` | `20.0` (config.py:154) | `20.0` (yaml:161) | float | `agents/decision_agent.py:81` | Flat cooldown after a winning close. |
| `scalping.cooldown_after_loss_seconds` | `90.0` (config.py:155) | `90.0` (yaml:162) | float | `agents/decision_agent.py:77` `self.scalp.cooldown_after_loss_seconds * min(streak, 4)` | Loss cooldown, multiplied by the loss streak. **The `4` is a bare literal** — max 360 s. |

#### `dynamic_tp_sl:` — config.yaml:177-185

Every field is read through `getattr(..., <inline default>)` in `analysis/volatility.py`, so a missing key degrades silently rather than raising. Only 7 of the 18 declared fields have any reader at all.

| Key | dataclass default | YAML | Type | Read at | What it changes |
|---|---|---|---|---|---|
| `dynamic_tp_sl.enabled` | `True` (config.py:174) | `true` (yaml:178) | bool | `analysis/volatility.py:310`; `core/config.py:555` (validator short-circuit) | When false, `get_dynamic_tp_sl_thresholds` returns static targets with `reason="dinamis dimatikan di config"`. |
| `dynamic_tp_sl.atr_multiple` | `1.5` (config.py:179) | `1.5` (yaml:179) | float | `analysis/volatility.py:324` `getattr(dyn, "atr_multiple", 1.5)` | `raw_sl = atr_multiple * atr_pct` (`:330`). |
| `dynamic_tp_sl.atr_period` | `14` (config.py:180) | `14` (yaml:180) | int | `analysis/volatility.py:314` `getattr(dyn, "atr_period", 14)` | Wilder ATR period. Validator requires `>= 2` (`config.py:594`). |
| `dynamic_tp_sl.min_sl_pct` | `0.0025` (config.py:185) | `0.0025` (yaml:181) | float | `analysis/volatility.py:325` `getattr(dyn, "min_sl_pct", 0.0025)` | Lower clamp on the ATR-derived SL. |
| `dynamic_tp_sl.max_sl_pct` | `0.0150` (config.py:186) | `0.0150` (yaml:182) | float | `analysis/volatility.py:326` `getattr(dyn, "max_sl_pct", 0.015)` | Upper clamp, hard. |
| `dynamic_tp_sl.min_risk_reward` | `1.5` (config.py:190) | `1.5` (yaml:183) | float | `analysis/volatility.py:327`; `trading/paper_engine.py:314` `getattr(dyn_cfg, "min_risk_reward", 1.5)` | R:R lock: `tp_pct = max(sl_pct * min_risk_reward, scalping.min_profit_pct)`. `paper_engine.py:315` re-applies `tp_pct = max(tp_pct, sl_pct * min_rr)` after the dynamic/static merge. Validator requires `> 1.0` (`config.py:580`). |
| `dynamic_tp_sl.realized_window_seconds` | `30.0` (config.py:193) | `30.0` (yaml:184) | float | `analysis/volatility.py:315` `getattr(dyn, "realized_window_seconds", 30.0)` | Window for the tick-based realized-vol feature (`volatility.py:318`). |
| `dynamic_tp_sl.max_breakeven_win_rate` | `0.65` (config.py:199) | `0.65` (yaml:185) | float | `analysis/volatility.py:394` `getattr(dyn, "max_breakeven_win_rate", 0.65)` | Runtime circuit breaker in `assess_volatility_gate`. Rejects when post-fee R:R demands a win rate above this. Validator requires `(0.5, 1.0)` (`config.py:587`). |
| `dynamic_tp_sl.fast_tp_pct` | `0.0060` (config.py:200) | *(not in YAML)* | float | **none — DEAD** | Shadow duplicate of `scalping.fast_tp_pct`. |
| `dynamic_tp_sl.tight_sl_pct` | `0.0025` (config.py:201) | *(not in YAML)* | float | **none — DEAD** | Shadow duplicate of `scalping.tight_sl_pct`. |
| `dynamic_tp_sl.min_confidence` | `0.40` (config.py:202) | *(not in YAML)* | float | **none — DEAD** | Shadow duplicate. |
| `dynamic_tp_sl.orderbook_imbalance_threshold` | `0.60` (config.py:203) | *(not in YAML)* | float | **none — DEAD** | Shadow duplicate of an already-dead key. |
| `dynamic_tp_sl.momentum_threshold` | `0.0008` (config.py:204) | *(not in YAML)* | float | **none — DEAD** | Shadow duplicate. |
| `dynamic_tp_sl.max_spread_pct` | `0.0006` (config.py:205) | *(not in YAML)* | float | **none — DEAD** | Shadow duplicate. |
| `dynamic_tp_sl.breakeven_trigger_pct` | `0.0020` (config.py:208) | *(not in YAML)* | float | **none — DEAD** | Shadow duplicate. |
| `dynamic_tp_sl.breakeven_offset_pct` | `0.0015` (config.py:209) | *(not in YAML)* | float | **none — DEAD** | Shadow duplicate. |
| `dynamic_tp_sl.cooldown_after_close_seconds` | `20.0` (config.py:214) | *(not in YAML)* | float | **none — DEAD** | Shadow duplicate. |
| `dynamic_tp_sl.cooldown_after_loss_seconds` | `90.0` (config.py:215) | *(not in YAML)* | float | **none — DEAD** | Shadow duplicate. |

#### `ensemble:` — config.yaml:190-214

| Key | dataclass default | YAML | Type | Read at | What it changes |
|---|---|---|---|---|---|
| `ensemble.enabled` | `True` (config.py:238) | `true` (yaml:191) | bool | `run.py:374`; `core/config.py:650` (validator short-circuit) | When false, `DirectionEnsembleAgent` is never constructed and `run.py:536-541` registers neither the ensemble job nor the snapshot-prune job. |
| `ensemble.interval_seconds` | `5` (config.py:239) | `5` (yaml:192) | int | `run.py:540` | `add_fixed_job` period for the ensemble snapshot. Registered as a **fixed** job, so it does not participate in US-market-hours retiming. |
| `ensemble.shrinkage_delta` | `0.85` (config.py:243) | `0.85` (yaml:193) | float | `analysis/direction_ensemble.py:163` | `z_shrunk = z * shrinkage_delta` before the sigmoid. Validator requires `(0, 1]` (`config.py:667`). |
| `ensemble.agreement_bonus` | `0.5` (config.py:246) | `0.5` (yaml:194) | float | `analysis/direction_ensemble.py:143` | `weight *= 1 + agreement_bonus * agreement_score(...)`. `0` = uniform trust, `1` = strong discrimination. Must be `>= 0` (`config.py:677`). |
| `ensemble.min_prob` | `0.02` (config.py:248) | `0.02` (yaml:195) | float | `analysis/direction_ensemble.py:168` | Clamp on final `prob_long` to `[min_prob, 1 - min_prob]`. Validator requires `(0, 0.5)` (`config.py:672`). |
| `ensemble.max_snapshot_age_seconds` | `20` (config.py:251) | `20` (yaml:196) | int | `agents/decision_agent.py:345` `_snapshot_is_fresh` | Hard staleness gate: older snapshots are rejected as no-data (`decision_agent.py:340-346`, also rejects unparseable timestamps where `ts <= 0`). Validator requires `>= 2 * interval_seconds` (`config.py:691`). |
| `ensemble.diffusion_horizon_minutes` | `30` (config.py:253) | `30` (yaml:197) | int | `agents/direction_agents.py:488` | `compute_directional_curve(horizon_minutes=…)`. |
| `ensemble.diffusion_points` | `60` (config.py:254) | `60` (yaml:198) | int | `agents/direction_agents.py:489` | `num_points` in the same call. Validator requires `>= 2` (`config.py:698`). |
| `ensemble.momentum_window_seconds` | `30.0` (config.py:256) | `30.0` (yaml:199) | float | `agents/direction_agents.py:159` | `MomentumAgent` price-change window. |
| `ensemble.min_trades_for_microstructure` | `8` (config.py:258) | `8` (yaml:200) | int | `agents/direction_agents.py:273` | Minimum tape trades before `MicrostructureAgent` will speak; below it the agent **abstains** rather than returning NEUTRAL. |
| `ensemble.min_returns_for_vol` | `20` (config.py:260) | `20` (yaml:201) | int | `agents/direction_agents.py:513` | Minimum 1m returns before sigma is usable in the diffusion exponent. |
| `ensemble.agents.orderflow.{enabled,weight}` | `True, 0.30` (config.py:261-263) | `true, 0.30` (yaml:203-205) | bool/float | `core/config.py:288` `base_weight()` → `analysis/direction_ensemble.py:137`; construction gate at `agents/direction_agents.py:422` | **Live, via indirection.** A naive `\.weight` grep reports these as dead; the real path is `cfg.base_weight(agent_name)` which returns `float(cfg.weight)` for enabled agents and `0.0` otherwise. A `0.0` weight forces `v["abstained"] = True` at `direction_ensemble.py:141`. |
| `ensemble.agents.momentum.{enabled,weight}` | `True, 0.25` (config.py:264-266) | `true, 0.25` (yaml:206-208) | bool/float | same | same |
| `ensemble.agents.technical.{enabled,weight}` | `True, 0.25` (config.py:267-269) | `true, 0.25` (yaml:209-211) | bool/float | same | same |
| `ensemble.agents.microstructure.{enabled,weight}` | `True, 0.20` (config.py:270-272) | `true, 0.20` (yaml:212-214) | bool/float | same | same |

`EnsembleAgentConfig` has no default `.enabled` beyond `True` (config.py:222) and `weight = 0.25` (config.py:223); the four per-agent instances override weight via `default_factory`.

#### `live:` — **config.yaml has no `live:` block at all**

`core/config.py:480-482` applies the block if present, then **unconditionally** forces `config.live.enabled = False`. **Verified verbatim:**

```python
    if "live" in raw:
        _apply_dict(config.live, raw["live"])
    config.live.enabled = False
```

The docstring at `config.py:471-479` states the reason: config.yaml is committed to git, so the only way to enable real money is an environment variable read by `trading/live/safety.py`.

| Field | Default (core/config.py) | Read at | Status |
|---|---|---|---|
| `enabled` | `False` (:307) | never by `SafetyGate`; forced `False` at `config.py:482` | Consumed only by `EnsembleConfig`-style helpers, not by the live gate |
| `testnet` | `True` (:311) | `run.py:271` derives testnet from `self.mode` (the CLI decision), **not** from this field | effectively unused |
| `private_key_env` | `"HYPERLIQUID_PRIVATE_KEY"` (:315) | `run.py:263`, `:267`; `trading/live/safety.py:209` | LIVE — names the env var, never holds the key |
| `live_confirm_env` | `"TRADEBOT_LIVE_CONFIRMED"` (:319) | `trading/live/safety.py:270` | LIVE |
| `live_window_utc` | `(13, 23)` (:325) | `trading/live/safety.py:231` `in_live_window`; displayed/edited at `trading/live/console.py:227`, `:577`, `:748` | LIVE — half-open `[start, end)` |
| `max_leverage` | `10` (:333) | `trading/live/safety.py:333`; `trading/live/engine.py:501` | LIVE — also used as the leverage for a late-filled position |
| `max_order_notional` | `100.0` (:335) | `trading/live/safety.py:337` | LIVE |
| `max_position_notional` | `300.0` (:336) | `trading/live/safety.py:339` | LIVE |
| `max_total_notional` | `600.0` (:337) | `trading/live/safety.py:341` | LIVE |
| `max_daily_orders` | `200` (:338) | `trading/live/safety.py:291` | LIVE |
| `max_daily_loss` | `50.0` (:339) | `trading/live/safety.py:293` | LIVE in the check, but `SafetyGate.record_realized_pnl` (safety.py:415) has **zero production callers** — **verified by grep during integration** — so `counters.realized_pnl` stays `0.0` and this blocker is structurally unreachable. |
| `max_consecutive_errors` | `3` (:340) | `trading/live/safety.py:295` | LIVE |
| `min_free_collateral` | `100.0` (:341) | `trading/live/safety.py:343` | LIVE |
| `use_exchange_side_tpsl` | `True` (:347) | **none — DEAD** | `LiveEngine._attach_protection` (engine.py:351) always attaches. |
| `auto_reconcile` | `False` (:353) | `trading/live/engine.py:127` | LIVE — false means a reconcile mismatch engages the kill switch |
| `reconciliation_tolerance_days` | `0` (:356) | **none — DEAD** | |

**`run.py` discards this singleton entirely.** `run.py:171` reads `get_config().live` only to feed the interactive menu (where `console.edit_rules` mutates it in memory, never writing config.yaml), and `run.py:262` then builds a **bare `LiveConfig()`** for `SafetyGate` (`:273`) and `LiveEngine` (`:276`). Every limit an operator edited is thrown away at live startup.

### 4.3 CLI flags

Exactly 7 flags exist across the whole repo. Only `live_doctor.py` uses `argparse`.

| Flag | Parsed at | Mechanism | Effect |
|---|---|---|---|
| `--paper` | `run.py:172` | `if "--paper" in sys.argv` | Force simulation, skip the menu. |
| `--non-interactive` | `run.py:172` | `if "--non-interactive" in sys.argv` | Same as `--paper`. |
| `--testnet` | `run.py:180` | `for flag, mode in (("--testnet", "testnet"), ("--live", "mainnet")): if flag in sys.argv` | Short-circuits the menu into testnet mode. |
| `--live` | `run.py:180` | same loop | Short-circuits into mainnet; prints a real-money warning first (`run.py:182-190`). |
| `--repair-candles` | `run.py:851` | `if "--repair-candles" in sys.argv` in `__main__` | One-shot destructive candle repair; no agents, no dashboard, `sys.exit(0)`. |
| `--testnet` / `--mainnet` / `--expect <addr>` | `live_doctor.py:64-70` | `argparse.ArgumentParser` (`--testnet` is `store_true` and effectively a no-op since `testnet = not args.mainnet` at `:73`) | Preflight only; sends no orders. |
| `--apply` | `backfill_realized_pnl.py:54` | `apply = "--apply" in sys.argv` | Report-only unless present. |

Two properties of the `run.py` flags:

- **All are substring tests against `sys.argv`, not argparse.** `--tes` matches `--testnet`; `--l` matches `--live`; `--paperish` matches `--paper`. There is no parser on `run.py` at all, so an unknown flag is silently ignored.
- `ml/trainer.py:5` advertises `--symbol` / `--timeframe` / `--days` in its module docstring, but `ml/trainer.py:232-233` has **no argument parsing whatsoever** — the `__main__` block calls `train_from_exchange()` with bare defaults. The documented flags are silently ignored.

### 4.4 Dataclass default vs `config.yaml` — every divergence

Loading `config.yaml` versus falling back to pure defaults (missing file, or wrong CWD) produces materially different economics. **All rows re-verified against `core/config.py:18-31, 36-45, 69-74` and `config.yaml:36-73` during integration:**

| Key | `core/config.py` default | `config.yaml` | Ratio |
|---|---|---|---|
| `risk.max_risk_per_trade` | `0.02` (:20) | `0.005` (yaml:37) | **4× tighter** |
| `risk.max_leverage` | `20` (:21) | `50` (yaml:38) | 2.5× looser |
| `risk.max_daily_loss` | `0.05` (:22) | `0.10` (yaml:39) | 2× looser |
| `risk.max_drawdown` | `0.15` (:23) | `0.20` (yaml:40) | |
| `risk.max_open_positions` | `3` (:24) | `30` (yaml:41) | **10×** |
| `risk.default_leverage` | `5` (:25) | `10` (yaml:42) | 2× |
| `fees.maker` | `0.0002` (:30) | `0.00015` (yaml:59) | Hyperliquid base tier, not the Binance-ish default |
| `fees.taker` | `0.0005` (:31) | `0.00045` (yaml:60) | |
| `agent_intervals.analysis_agent` | `300` (:38) | `15` (yaml:66) | **20× faster** |
| `agent_intervals.analysis_agent_us_open` | `180` (:39) | `10` (yaml:67) | 18× faster |
| `agent_intervals.decision_agent` | `60` (:40) | `3` (yaml:68) | **20× faster** |
| `agent_intervals.decision_agent_us_open` | `30` (:41) | `2` (yaml:69) | 15× faster |
| `agent_intervals.balance_snapshot` | `300` (:45) | `60` (yaml:73) | 5× faster |

Everything else (`scalping`, `dynamic_tp_sl`, `ensemble`, `indicators`, `us_market`, `logging`, `dashboard`, `scanning`, `symbols`, `database`) is identical between the two sources.

Because `load_config` returns pure defaults on a missing file and `get_config()` takes no path argument, **running from the wrong working directory silently yields the 3-position, 20-leverage, 0.0005-taker configuration** — a materially different risk profile with no diagnostic.

### 4.5 Hardcoded tunables with no config key

These behave exactly like tunables and are not reachable from any file.

#### Timing

| Value | Where | What it controls |
|---|---|---|
| `0.3` s | `run.py:598` | `_execution_loop` — the actual decision tick. **Verified verbatim.** |
| `30` s | `run.py:626` | `_candle_refresh_loop` period, with `timeframe="1m", limit=300` and `timeframe="5m", limit=120` hardcoded at `run.py:618-619`. The 300-minute window is chosen so UPSERT can repair restart gaps, not just append. |
| `25` s warmup, then `30` s | `run.py:630`, `run.py:667` | `_telemetry_loop`. |
| `1.5` s cycle, `10.0` s staleness, `0.15` s stagger | `data/price_feed.py:606`, `:597`, `:601` | `price_update_loop` safety net. |
| `5.0` s | `run.py:292` | `LiveEngine.run_loop(interval=5.0, …)`. **Verified.** |
| `500` ms / `60000` ms | `dashboard/app.py:53`, `:63` | The two `dcc.Interval` periods. **Verified verbatim.** `dashboard.update_interval` is dead. |
| `60` s | `core/scheduler.py:48`, `:122` | `_market_check` — the self-rescheduling US-session re-check that calls `reschedule_job`. |
| `600` | `run.py:669` | `repair_candles(window_minutes=600)`. |
| `0.2` s | `run.py:720` | Sleep between symbol refetches during repair. |

#### Risk and economics

| Value | Where | What it controls |
|---|---|---|
| `0.9` | `trading/risk_manager.py:299` (`validate_trade`), `:119` (`calculate_position_size`), `:364` (`calculate_scalp_position_size`) | Free-cash margin cap, written **three separate times**. The real exposure ceiling for a leveraged book; unchangeable without editing source. **Verified at all three by grep.** |
| `0.02` / `0.04` | `trading/paper_engine.py:476-479` | Scalping-disabled fallback SL/TP. Bypasses every scalping key, the volatility gate, and the tick guard's ATR context. |
| `1` | `agents/decision_agent.py:217` `if sym_count >= 1: continue` | Max open positions per symbol — a bare literal, unlike `batch_size` and `max_open_positions` which are configurable. |
| `4` | `agents/decision_agent.py:77` `min(streak, 4)` | Loss-streak cooldown multiplier cap (max 360 s with the current 90 s config). |
| `2` / `3` | `agents/decision_agent.py:377`, `:379` | `_determine_leverage` floors: `max(2, default//2)` and `max(3, default*3//4)`. |
| `0.004` | `trading/risk_manager.py:133` | `mmr` default for the liquidation price (0.4 % BTC maintenance margin). |
| `1e-8` / `0.01` / `1e-5` | `trading/risk_manager.py:16-18` | `PRICE_QUANT` / `MONEY_QUANT` / `QTY_QUANT`, always `ROUND_DOWN`. **Verified verbatim.** |

#### Signal weights — none of these exist in `config.yaml`

| Value | Where | What it controls |
|---|---|---|
| `0.40` / `0.25` / `0.35` | `agents/analysis_agent.py:271` | AnalysisAgent's technical/fundamental/ML blend. Band `±0.15` at `:302`. |
| `0.30` / `0.20` / `0.25` / `0.15` / `0.10` | `analysis/probability_engine.py:163-168` | The 5-factor Bayesian composite: technical / sentiment / ml / orderbook / macro. |
| `2.5` | `analysis/direction_ensemble.py:32` `MAX_AGENT_Z` | Every agent verdict normalises to `±2.5 × confidence`, capping any single agent's share. |
| `0.45` | `analysis/probability_engine.py:299`, `:353` | `drift_per_min = z_score * 0.45 * sigma`. **No derivation anywhere** in the file or in config; it alone sets the diffusion curve's steepness. The module docstring at `probability_engine.py:19-20` describes a different formula than the one implemented. |
| `0.0002` | `agents/direction_agents.py:525` | Realized-vol floor before it enters the diffusion exponent. |
| `0.0001` (funding→z), `1.5` (tape weight), `2.0` (z→confidence saturation) | `agents/direction_agents.py:290`, `:278`, `:175`/`:245`/`:310` | MicrostructureAgent scaling; the `2.0` divisor encodes "a z of ±2 counts as full conviction". |
| `0.45` | `analysis/ml_signals.py:237` | ML action threshold in the rule-based fallback. |
| `0.5` / `0.2` | `agents/news_agent.py:70`, `:72` | News impact buckets (HIGH / MEDIUM), on **absolute** VADER score. |
| `512` chars | `data/sentiment.py:110`, `:150` | FinBERT truncation. The adjacent comment says "512 token" — a ~4× under-use of the window on English text. |
| `5` | `core/market_store.py:251` | `set_recent_trades` hard-truncates to `trades[:50]`. |
| `1.0e-6` … `0.05` | `core/microstructure.py:130-176` | Microstructure z-score epsilons. |

#### Data-layer and storage constants

| Value | Where | What it controls |
|---|---|---|
| `1000` | `core/event_bus.py:37` | `EventBus(maxsize=1000)`. On overflow `publish` discards the **oldest** event per subscriber (`event_bus.py:74-84`). |
| `240` | `core/market_store.py:19` | `PRICE_HISTORY_MAX` — ~2 minutes of history at the 500 ms HUD cadence. |
| `30` / `40` | `agents/direction_agents.py:219`, `:514` | Minimum candles for TechnicalAgent z; candle count for realized vol. |
| `0.0003` / `0.001` / `0.0002` | `analysis/probability_engine.py:270`, `:139` | Diffusion-curve sigma floor; realized-vol defaults. |
| `2 ** 22` (4 MB) | `data/hyperliquid_feed.py:50` | `WS_MAX_SIZE` — allMids ships 1000+ entries per frame. |
| `30.0` / `60.0` | `data/hyperliquid_feed.py:53`, `:56` | WS ping interval; receive timeout. |
| `1.0` → `30.0` s | `data/hyperliquid_feed.py:515-548` | WS reconnect backoff, doubling, capped. The only retry logic in the data layer; there are no retries on any REST path. |
| `15000` | `database/db.py:187` | SQLite `busy_timeout` in ms (raised from 5000 after 1150 observed lock failures — see the comment at `db.py:180-186`). |

### 4.6 Dead configuration

Every entry here is a maintenance hazard: an operator can edit it, see no error, and observe no behaviour change.

#### 4.6.1 Values read but never defined by `config.yaml` — silently dropped at load

**`database.agent_log_prune_interval` and `database.agent_log_keep`.** The loader routes prune keys to top-level `AppConfig` attributes through a prefix filter. **Re-verified verbatim during integration at `core/config.py:508-511`:**

```python
    if "database" in raw:
        _apply_dict(config, {k: v for k, v in raw["database"].items()
                             if k.startswith("snapshot_")})
```

`agent_log_prune_interval` and `agent_log_keep` do not start with `snapshot_`, so they are filtered out before `_apply_dict` ever sees them. The values the bot actually uses come from the `getattr` fallbacks in `run.py`:

- `run.py:464` — `agent_log_keep`, default `5000`
- `run.py:466` — `max(60, int(getattr(self.config, "agent_log_prune_interval", 1800)))`

Because the YAML values coincidentally equal the dataclass defaults, nothing is wrong today. The moment an operator edits `config.yaml:115-116`, the edit is silently discarded. Fix is one line: widen the filter to `k.startswith("snapshot_") or k.startswith("agent_log_")`.

#### 4.6.2 Keys defined in `config.yaml` and in a dataclass, read nowhere

| Key | Defined at | Read at | Consequence |
|---|---|---|---|
| `scalping.orderbook_imbalance_threshold` | `config.yaml:156`; `core/config.py:139` | **nowhere** | Zero readers repo-wide, including tests. |
| `dynamic_tp_sl.orderbook_imbalance_threshold` | `core/config.py:203` (not in YAML) | **nowhere** | Second copy of an already-dead key. |
| `agent_intervals.funding_rate` | `config.yaml:72`; `core/config.py:44` | **nowhere** | Never registered as a scheduler job. Funding is fetched opportunistically by `data/price_feed.py:417`, not on an 8-hour timer. |
| `exchange.name` | `config.yaml:31`; `core/config.py:86` | **nowhere** | Value `"binance"` is actively misleading; the bot trades Hyperliquid (`data/hyperliquid_feed.py:46-47`). Also the only `config.exchange` reference outside the loader is `config.py:461`. |
| `exchange.type` | `config.yaml:32`; `core/config.py:87` | **nowhere** | |
| `exchange.sandbox` | `config.yaml:33`; `core/config.py:88` | **nowhere** | |
| `dashboard.update_interval` | `config.yaml:123`; `core/config.py:73` | **nowhere** | `dashboard/app.py:53` hardcodes `500`, `:63` hardcodes `60000`. README:118 documents the real values, so the key is pure noise. |
| `live.use_exchange_side_tpsl` | `core/config.py:347` (no YAML block exists) | **nowhere** | `LiveEngine._attach_protection` (engine.py:351) always attaches. The docstring says "WAJIB true", but nothing reads the field. |
| `live.reconciliation_tolerance_days` | `core/config.py:356` | **nowhere** | |

#### 4.6.3 Shadow duplicates: `DynamicTpSlConfig` fields that mirror `ScalpingConfig`

`DynamicTpSlConfig` (config.py:158-215) carries ten fields with **identical defaults** to `ScalpingConfig`, and all ten are unread. Every real reader takes the `scalping` copy:

| Field | `ScalpingConfig` | `DynamicTpSlConfig` | Real reader |
|---|---|---|---|
| `fast_tp_pct` `0.0060` | config.py:109 | config.py:200 | `analysis/volatility.py:296` |
| `tight_sl_pct` `0.0025` | config.py:110 | config.py:201 | `analysis/volatility.py:295` |
| `min_confidence` `0.40` | config.py:111 | config.py:202 | `agents/decision_agent.py:201`, `:320` |
| `orderbook_imbalance_threshold` `0.60` | config.py:139 | config.py:203 | none (both dead) |
| `momentum_threshold` `0.0008` | config.py:140 | config.py:204 | `agents/direction_agents.py:174` |
| `max_spread_pct` `0.0006` | config.py:141 | config.py:205 | `agents/decision_agent.py:212` |
| `breakeven_trigger_pct` `0.0020` | config.py:148 | config.py:208 | `agents/execution_agent.py:206` |
| `breakeven_offset_pct` `0.0015` | config.py:149 | config.py:209 | `agents/execution_agent.py:207` |
| `cooldown_after_close_seconds` `20.0` | config.py:154 | config.py:214 | `agents/decision_agent.py:81` |
| `cooldown_after_loss_seconds` `90.0` | config.py:155 | config.py:215 | `agents/decision_agent.py:77` |

None of the ten appear in `config.yaml`'s `dynamic_tp_sl:` block, so they are reachable only by editing `core/config.py` — where an edit would have no effect. This is the highest-value deletion in the config surface.

#### 4.6.4 Dead documentation and dead reads

| Item | Location | Note |
|---|---|---|
| `TRADEBOT_VERBOSE_STARTUP` | documented `core/logger.py:95` and `core/logger.py:211` | Repo-wide grep returns only these two comment lines. No code reads it. Either dead doc or an unimplemented inverse of `TRADEBOT_QUIET_STARTUP`. |
| `ml/trainer.py --symbol/--timeframe/--days` | `ml/trainer.py:5` (docstring) | No `argparse` exists anywhere in the file; `ml/trainer.py:232-233` calls `train_from_exchange()` with bare defaults. |

#### 4.6.5 False positives — things that look dead but are live

Listed so a future reader does not "fix" them:

- **`ensemble.agents.*.weight`** — read through `EnsembleConfig.base_weight()` (`core/config.py:283-288`) via `analysis/direction_ensemble.py:137`. A `.weight` attribute-chain grep reports all four as unused; they are the live pooling weights.
- **`fees.maker`** — read at `trading/risk_manager.py:215`, but unreachable in paper because both position legs hardcode `fee_type="TAKER"` (`trading/position_manager.py:75`, `:181`).
- **`risk.max_daily_loss`** — genuinely enforced: `trading/paper_engine.py:566` supplies real data via `repo.get_daily_realized_pnl()`.
- **`live.max_daily_loss`** — the *check* exists at `trading/live/safety.py:293` but `SafetyGate.record_realized_pnl` (safety.py:415) has zero production callers, so the blocker is structurally unreachable. This is dead-by-omission, not dead-by-definition.

#### 4.6.6 Summary count

```
Defined in config.yaml .................... 110 leaf keys (121 keys total, 16 blocks)
Read by first-party code .................. 86
Dead (defined, never read) ................ 10   (4.6.2)
Silently dropped at load ..................  2   (4.6.1)
Shadow duplicates in DynamicTpSlConfig .... 10   (4.6.3)
Dataclass fields with zero readers ........  5   (orderbook_imbalance_threshold ×2,
                                                 sandbox, update_interval,
                                                 use_exchange_side_tpsl,
                                                 reconciliation_tolerance_days)
```

### 4.7 Validation — boot-time and fatal

Three validators run in `load_config`, in this order (`core/config.py:525-527`) — **verified at `:523-526`**:

1. `_validate_scalping_economics(config)` — `config.py:707-786`
2. `_validate_ensemble_config(config)` — `config.py:630-704`
3. `_validate_dynamic_tp_sl(config)` — `config.py:531-626`

Each returns immediately if its `enabled` flag is false. Each accumulates **every** problem into a list and raises one `ValueError` with the messages joined by `"\n  - "`. A `ValueError` from `load_config` propagates out of `get_config()` and out of the first `get_logger()` call that bootstraps it (`core/logger.py:157`).

`_validate_scalping_economics` checks:

| Rule | Line | Message |
|---|---|---|
| `breakeven_trigger_pct < min_profit_pct` | `:727` | else `_scalp_take_profit` closes first and the breakeven path is dead code |
| `net_tp >= net_sl` after `roundtrip = fees.taker * 2` | `:735-743` | else the strategy needs an unachievable win rate; `breakeven_wr = net_sl / (net_tp + net_sl)` is reported |
| `breakeven_offset_pct >= roundtrip` | `:745` | else "breakeven" SL still loses money |
| `min_confidence <= reversal_close_threshold < 1.0` | `:757` | else the reversal gate never fires or fires on weak positions |
| `stale_tick_window_seconds > max_tick_age_seconds` | `:770` | else the median is computed from samples the first check already rejected |
| `stale_tick_min_samples >= 2` | `:777` | a median of one sample is not a median |

`_validate_ensemble_config` checks: at least one enabled agent; total active weight `> 0`; no negative weight; `shrinkage_delta ∈ (0, 1]`; `min_prob ∈ (0, 0.5)`; `agreement_bonus >= 0`; `max_snapshot_age_seconds > 0`; **`max_snapshot_age_seconds >= 2 × interval_seconds`** (`:691`); `diffusion_points >= 2`.

`_validate_dynamic_tp_sl` checks: `min_sl_pct > 0`; `max_sl_pct > 0`; `min_sl_pct < max_sl_pct`; `atr_multiple > 0`; `min_risk_reward > 1.0`; `max_breakeven_win_rate ∈ (0.5, 1.0)`; `atr_period >= 2`; and that `max(min_sl × min_rr, scalping.min_profit_pct)` still clears the round-trip fee and its post-fee R:R needs a win rate within `max_breakeven_win_rate` (`:601-621`).

All three read the **live** `config.fees.taker` rather than a constant, so raising `fees.taker` toward the dataclass default `0.0005` tightens every breakeven constraint and can turn a previously-valid configuration into a boot crash. The contracts are locked by `tests/test_config.py:25-101` and `tests/test_bugfixes.py:967-1020`.

---

## 5. Risk, safety and correctness

### 5.1 Two disjoint risk regimes

The repo contains two entirely separate risk systems. They share no code, no state, and no database.

| | PAPER | LIVE |
|---|---|---|
| Entry point | `PaperTradingEngine.execute_order` — `trading/paper_engine.py:181` | `SafetyGate.can_send` — `trading/live/safety.py:347` |
| Limit owner | `RiskManager.validate_trade` — `trading/risk_manager.py:273` | `SafetyGate.master_blockers` / `.order_blockers` — `safety.py:247,300` |
| State source | local SQLite (`account`, `positions`) | the exchange (`info.user_state`) |
| Persistence | SQLite, survives restart | `data_store/live_counters.json` (only when live env is set) |
| Kills trading | rejects the order | returns `(False, blockers)` |

`DecisionAgent` receives the **paper** `RiskManager` in both modes: `run.py:356` passes `self.paper_engine.risk_manager` unconditionally, before the live branch at `run.py:364`. The live gate is therefore never consulted by the decision layer; it is only consulted inside `LiveEngine.submit_order` (`engine.py:236`).

```
Order path (paper)                      Order path (live)
  DecisionAgent.act  :264                  DecisionAgent.act  :264
    Order(quantity=None)                     Order(quantity=None)
    stop_loss=None take_profit=None           stop_loss=None take_profit=None
  ExecutionAgent.think :96/:139/:140        ExecutionAgent.think :96/:139/:140
    engine.get_price        <-- AttributeError on LiveExecutor
  engine.execute_order                       engine.execute_order
    PaperTradingEngine:181                   LiveExecutor:66
      _tick_quality_guard                       _open:77 refuses missing SL/TP
      _resolve_tp_sl + vol gate                   size = order.quantity or 0.0  <-- 0.0
      sizing (scalp/fixed-fractional)            SafetyGate.can_send -> INVALID_INPUT
      validate_trade (4 limits)
    position_manager.open_position:47        LiveEngine.submit_order:204
      atomic balance delta                       SafetyGate.can_send
      claim-once close                           set_leverage BEFORE order
      net of both fees                           _attach_protection (SL/TP on venue)
```

### 5.2 Paper-path risk limits, exact values and triggers

All four are in `RiskManager.validate_trade` (`trading/risk_manager.py:273-338`), verified verbatim during integration. They accumulate into a `reasons` list; `allowed = len(reasons) == 0` (`:335-337`).

| # | Limit | Config key | Value in `config.yaml` | Dataclass default | Trigger expression | file:line |
|---|---|---|---|---|---|---|
| 1 | Free-cash margin cap | — (hardcoded `0.9`) | — | — | `margin_required > balance * 0.9` | `risk_manager.py:299` |
| 2 | Max open positions | `risk.max_open_positions` | `30` | `3` | `open_positions >= max_open_positions` | `risk_manager.py:303` |
| 3 | Daily loss breaker | `risk.max_daily_loss` | `0.10` | `0.05` | `daily_pnl < 0 and abs(daily_pnl)/reference >= 0.10` | `risk_manager.py:319-326` |
| 4 | Drawdown breaker | `risk.max_drawdown` | `0.20` | `0.15` | `(peak_balance - mark)/peak_balance >= 0.20` | `risk_manager.py:330-333` |

**Denominator freezing**, verbatim at `risk_manager.py:319`:

```python
        reference = self._initial_balance if self._initial_balance > 0 else balance
```

The comment above it records the reason: using live `balance` shrinks the denominator as margin locks, so the breaker *relaxes* at the worst moment. `_initial_balance` is injected once from the DB in `PaperTradingEngine.initialize` (`paper_engine.py:86-88`).

**What happens when a limit trips.** `_execute_open` logs `TRADE_REJECTED` to `agent_logs` (`paper_engine.py:581-594`) with `required_cash`, `estimated_fee`, `margin` and `daily_pnl` recorded separately so the decision is reconstructible, then returns `{"success": False, "message": "Ditolak: " + "; ".join(reasons)}` (`:596`). No order is sent, no event is published, no position is touched. Every limit still in the `reasons` list is reported, not just the first.

**Inputs to `validate_trade`** (`paper_engine.py:566-573`):

| Param | Source | Note |
|---|---|---|
| `balance` | `account["balance"]` = **free cash** (open margin already deducted) | `paper_engine.py:548` |
| `margin_required` | `required_cash = margin + estimated_fee` (`:556-557`) | fee is included deliberately; `open_position` deducts both atomically |
| `open_positions` | `PositionManager.get_open_position_count()` | `:545` |
| `daily_pnl` | `Repository.get_daily_realized_pnl()` | `:564`; sums `realized_pnl` for `status IN ('CLOSED','LIQUIDATED')` and `date(closed_at) = date('now')` (UTC) — `repository.py:526-547` |
| `peak_balance` | `account.get("peak_balance")` | only ever raised by `bump_peak_balance` in `close_position` (`position_manager.py:258`) |
| `equity` | `free_balance + open_margin + open_upnl` (`:549`) | marks include unrealized P&L, as required by the docstring at `risk_manager.py:286-291` |

### 5.3 Live-path risk limits, exact values and triggers

`SafetyGate` has **two** passes. `can_send` (`safety.py:347-382`) runs `master_blockers` first and short-circuits: if any master blocker exists, `order_blockers` is never evaluated (`:364-373`).

#### Master blockers (all orders) — `safety.py:247-298`

**Re-verified verbatim during integration at `safety.py:258-298`:**

| # | Blocker | Condition | file:line |
|---|---|---|---|
| 0 | `COUNTER_STATE_UNREADABLE` | `not self.counters.readable` (present-but-corrupt counter file) | `safety.py:261-262` |
| 1 | `LIVE_DISABLED` | `env["TRADEBOT_LIVE"] not in ("1","true","yes")` | `safety.py:266-267` |
| 2 | `NOT_CONFIRMED` | `env[cfg.live_confirm_env]` not in `("1","true","yes")` | `safety.py:270-274` |
| 3 | `KILL_SWITCH` | `self.engaged` **or** `env["TRADEBOT_LIVE_KILL_SWITCH"] not in ("","0","false","no")` | `safety.py:277-280` |
| 4 | `MISSING_KEY` | `not self.private_key` | `safety.py:283-284` |
| 5 | `OUTSIDE_WINDOW` | `not in_live_window()` — half-open `[start, end)` UTC, default `(13, 23)` | `safety.py:287-288`, `:231-245` |
| 6 | `DAILY_ORDER_LIMIT` | `counters.orders_sent >= cfg.max_daily_orders` (`200`) | `safety.py:291-292` |
| 7 | `DAILY_LOSS_LIMIT` | `counters.realized_pnl <= -abs(cfg.max_daily_loss)` (`-50.0`) | `safety.py:293-294` |
| 8 | `TOO_MANY_ERRORS` | `counters.consecutive_errors >= cfg.max_consecutive_errors` (`3`) | `safety.py:295-296` |

**`TRADEBOT_LIVE_KILL_SWITCH` is inverted from intuition.** Unset yields `""`, which *is* in the tuple, so the condition is False and the kill switch does **not** trip. Only a value outside `("", "0", "false", "no")` — in practice the literal `"1"` — trips it. The release message at `safety.py:406` (`Set TRADEBOT_LIVE_KILL_SWITCH=0 untuk melepas`) describes the release value, not the trip value.

#### Per-order blockers — `safety.py:300-346`

This pass is **not** short-circuited: a single bad order stacks every applicable blocker.

| # | Blocker | Condition | Default | file:line |
|---|---|---|---|---|
| 1 | `INVALID_INPUT` | `request.size <= 0` | — | `safety.py:319-320` |
| 2 | `INVALID_INPUT` | `request.price <= 0` (appended twice when both bad) | — | `safety.py:321-322` |
| 3 | `MISSING_CLOID` | `not request.is_close and not cloid` | — | `safety.py:327-328` |
| 4 | `LEVERAGE_TOO_HIGH` | `leverage is not None and not (1 <= int(leverage) <= cfg.max_leverage)` | `10` | `safety.py:332-334` |
| 5 | `ORDER_TOO_LARGE` | `notional > cfg.max_order_notional` | `100.0` USDC | `safety.py:337-338` |
| 6 | `POSITION_TOO_LARGE` | `symbol_exposure + notional > cfg.max_position_notional` | `300.0` USDC | `safety.py:339-340` |
| 7 | `EXPOSURE_TOO_LARGE` | `current_exposure + notional > cfg.max_total_notional` | `600.0` USDC | `safety.py:341-342` |
| 8 | `COLLATERAL_TOO_LOW` | `free_collateral - notional < cfg.min_free_collateral` | `100.0` USDC | `safety.py:343-344` |

Exposure numbers come from the exchange, not the local DB: `marginSummary.withdrawable` (explicitly *not* `accountValue`) at `client.py:178-180`, `sum(|szi| * entryPx)` at `client.py:194-209`, per-coin notional at `client.py:211-224`.

Rejection returns `(False, blockers)` and `submit_order` returns `{"success": False, "blockers": [...], "message": "..."}` without touching the exchange (`engine.py:245-250`).

### 5.4 Kill switches

| Switch | Kind | Raised by | Released by |
|---|---|---|---|
| `SafetyGate.engaged` | sticky in-process bool, `safety.py:177` | 5 raisers below + env | **nothing in the repo**; restart the process, or set `TRADEBOT_LIVE_KILL_SWITCH=0` |
| `TRADEBOT_LIVE_KILL_SWITCH` env | external latch | operator | operator |
| `VolatilityGateError` | per-order exception, `paper_engine.py:26-43` | `assess_volatility_gate` returns a reason (`volatility.py:347-412`) | n/a, per order |
| tick-quality guard | per-order return string, `paper_engine.py:209-268` | stale tick or median outlier | n/a, per order |
| daily-loss breaker | paper, `risk_manager.py:320-326` | DB read of today's realized P&L | next UTC day |
| drawdown breaker | paper, `risk_manager.py:330-333` | `peak_balance` vs `equity` | new high-water mark |

**Kill-switch raisers (`SafetyGate.engaged`)** — **verified by grep during integration: exactly 5 `engage_kill_switch` call sites outside the definition at `safety.py:384`, plus 1 inlined latch, for 6 distinct paths that set `engaged = True`:**

| Trigger | file:line |
|---|---|
| `record_error()` while `consecutive_errors >= 3` (inlined at `safety.py:402-408`, not via `engage_kill_switch`) | `safety.py:401-408` |
| `reconcile()` mismatch while `not cfg.auto_reconcile` | `engine.py:128` |
| SL attach failed after a fill | `engine.py:404` |
| exchange unreadable in `health_check` | `engine.py:555` |
| any `health_check` problem | `engine.py:599` |
| `emergency_flat` not clean | `engine.py:737` |

`engage_kill_switch` never clears anything (`safety.py:384-388` — it guards with `if not self.engaged:` and only ever sets `True`). **Verified:** grep finds no call to `emergency_flat` outside `trading/live/engine.py` (definition at `:655`, a docstring mention in `client.py:469`) and `tests/test_live_engine.py`. The flatten routine is defined but never wired into `run.py`. `run.py:294` stores `self._live_engine_obj` and `run.py:292` stores `self._live_task`, but `shutdown()` (`:794-814`) only cancels `self._background_tasks` (`:807-808`), so the live poll loop outlives shutdown.

### 5.5 P&L and fee math

#### Fee formula — `RiskManager.calculate_fee` (`risk_manager.py:202-240`)

```
rate     = fees.taker if fee_type == "TAKER" else fees.maker
notional = abs(Decimal(quantity)) * abs(Decimal(price))
fee      = (notional * Decimal(rate)).quantize(Decimal("0.0000000001"), ROUND_DOWN)
return   = abs(fee)
```

Three deliberate defences:
- `abs()` on both operands — a negative quantity times a negative price is positive, and negative × positive yields a **negative fee**, i.e. a cost that *adds* to the balance with no order behind it (`:206-213`).
- rate forced non-negative (`:216-217`).
- quantized to **10 decimals**, not to cents. The docstring records the two bugs this replaced: fee stopped being linear in notional, and small scalping orders paid `$0.00` — the bot looked free to trade and looked profitable because of it (`:220-237`).

Both legs are hardcoded `"TAKER"` at `position_manager.py:75`, `:181` and `paper_engine.py:556`. `Order.order_type` is never consulted, so LIMIT and MARKET fill identically and maker rebates (`0.00015`) are unreachable in paper.

#### Round-trip economics

```
roundtrip = fees.taker * 2 = 0.00045 * 2 = 0.0009     (0.09% of notional)
net_tp    = min_profit_pct - roundtrip = 0.0060 - 0.0009 = 0.0051
net_sl    = tight_sl_pct  + roundtrip = 0.0025 + 0.0009 = 0.0034
breakeven_wr = net_sl / (net_tp + net_sl) = 0.0034 / 0.0085 = 0.40
```

This arithmetic is computed identically in three places: boot validator `core/config.py:734-743`, dynamic-TP validator `core/config.py:601-621`, and runtime gate `analysis/volatility.py:383-403`.

#### P&L on a normal close — `position_manager.py:150-296`

```
gross    = (close - entry) * qty            LONG   (risk_manager.py:260)
           (entry - close) * qty            SHORT
net_pnl  = gross - open_fee - close_fee     position_manager.py:203
credit   = pos.margin + net_pnl + open_fee  position_manager.py:243
```

`open_fee` is **read back from the `trades` table** (`:196-200`) rather than recomputed from `entry_price`, because the taker rate can change mid-run and recomputing would credit a different number from the one actually deducted. `open_fee` is added back to the *credit* (not excluded from `net_pnl`) so it is charged exactly once: once at open from the balance, once inside `realized_pnl` for statistics. The comment at `:230-242` records the double-charge this replaced: 2.70 USDT per position, ~891 USDT across 330 paper positions.

`realized_pnl` written to `positions.realized_pnl` is therefore **net of both fees**, which is what `get_trade_stats` (`repository.py:576-604`) and `get_daily_realized_pnl` (`:526-547`) both read. The breakeven shift at `execution_agent.py:226-237` is sized for that: `breakeven_offset_pct = 0.0015 > roundtrip 0.0009`, validated at boot (`core/config.py:745-749`).

#### P&L on a liquidation — `position_manager.py:384-418`

```
realized_pnl = -pos.margin        # full margin, no gross computation
fee          = 0                  # Trade row written with fee=0
balance      = unchanged           # margin was already deducted at open
```

Claim-once via `Repository.liquidate_position` (`repository.py:171-181`, `WHERE id = ? AND status = 'OPEN'`, returns `rowcount > 0`).

#### Fee / rounding policy elsewhere

| Quantity | Quantum | Rounding | file:line |
|---|---|---|---|
| `PRICE_QUANT` | `Decimal("0.00000001")` | `ROUND_DOWN` | `risk_manager.py:16` — **verified verbatim** |
| `MONEY_QUANT` | `Decimal("0.01")` | `ROUND_DOWN` | `risk_manager.py:17` — **verified verbatim** |
| `QTY_QUANT` | `Decimal("0.00001")` | `ROUND_DOWN` | `risk_manager.py:18` — **verified verbatim** |
| fee | `Decimal("0.0000000001")` | `ROUND_DOWN` | `risk_manager.py:238-239` |

`ROUND_DOWN` is explicit everywhere because `Decimal.quantize` defaults to `ROUND_HALF_EVEN`, and on a 0.25 % scalp a one-tick half-even move in the wrong direction eats into the exact room the scalp was chosen for (`risk_manager.py:163-169`, `:186-190`).

**No funding P&L exists.** `grep -rn funding trading/` returns zero hits. Hyperliquid funding is fetched (`data/price_feed.py:417-479`) and traded on as a contrarian signal (`agents/direction_agents.py:290`), but a position held across a funding timestamp accrues no funding cost anywhere in the accounting. On a perp with hourly funding this is a systematic over-statement of carry.

### 5.6 Position sizing

#### Scalp sizing (the production path) — `risk_manager.py:340-378`

```
margin = risk_pct * balance                        risk_pct default = max_risk_per_trade = 0.005
max_per_pos = balance * 0.9 / max_open_positions   = balance * 0.03
margin = min(margin, max_per_pos)
position_value = margin * leverage                 leverage = min(leverage, max_leverage)
quantity       = position_value / entry_price
```

Called from `paper_engine.py:519-523` with **no** `risk_pct`, so it defaults to `max_risk_per_trade`. Consequences:

- **Stop distance is not an input.** The function signature has no `stop_loss_price`. A 0.25 %-wide stop and a 1.5 %-wide ATR stop both produce the same `margin`.
- At `max_open_positions = 30` the portfolio cap is 3 % of free cash per position. With 30 positions locked, 90 % of cash is committed, and limit #1 then rejects every new order.
- Verified arithmetic: `balance = 10000` → `margin = 50.0`, notional `500.0` at `default_leverage = 10`, cap never binds (`300.0`).
- Real risk per trade at that size: `500 × 0.0025 = 1.25` stop loss + `500 × 0.0009 = 0.45` fees = `1.70`, i.e. **0.017 % of balance per stopped-out trade**, not 0.5 %.

#### Fixed-fractional sizing (fallback when `scalping.enabled` is false) — `risk_manager.py:64-131`

```
quantity        = (balance * risk_pct) / abs(entry - sl)
margin          = quantity * entry / leverage
if margin > balance * 0.9: clamp margin, back-solve position_value and quantity
```

Degenerate-SL fallback: if `sl_distance == 0`, substitute `entry * 0.01` and log a warning (`:107-109`) — otherwise quantity is infinite. The 0.9 clamp shrinks the trade rather than failing it (`:119-123`).

#### Leverage

`max_leverage` is applied **only inside the sizing functions** (`risk_manager.py:97`, `:358`). The raw `order.leverage` is then forwarded to `position_manager.open_position` (`paper_engine.py:604`) and used *un-clamped* for the stored `margin` and the liquidation price (`position_manager.py:67-72`). The only input guard is `lev <= 0` (`paper_engine.py:403-406`). Not exploitable today because `DecisionAgent._determine_leverage` (`decision_agent.py:374-380`) can only return `10`, `7`, or `5`, but the cap does not exist on the path that matters.

`DecisionAgent` therefore never requests the configured `max_leverage = 50`, nor `max_risk_per_trade`-derived sizing — leverage is chosen purely from `fundamental.risk_level`.

### 5.7 Fill model

**Zero slippage, zero spread, zero depth, zero impact, zero latency.**

```
PaperTradingEngine.get_price(symbol)   paper_engine.py:159-179
  cached = self._last_prices.get(symbol);  if cached and cached > 0: return cached
  price  = market_store.get_price(symbol);  if price and price > 0: cache and return
  return None

execute_order: price = self.get_price(order.symbol)   paper_engine.py:191
```

That value **is** the fill price. There is no slippage constant anywhere in `trading/` (grep for `slippage` in `trading/` matches only a docstring at `engine.py:665` and a comment at `executor.py:150`). A correct FIFO-queue book-walk fill model exists at `analysis/backtester.py:239-300` (`QueueFillModel`) and `:497-540` (`_worst_fill_price`) and is never called in production.

`check_positions` merges `market_store.get_all_prices()` with `self._last_prices` (`:828-829`), and the engine cache silently overrides `market_store` for any symbol in both, so the engine can keep filling at a price the feed has already moved past.

### 5.8 Order rejection ladder (open path only)

`_execute_open` (`paper_engine.py:372-685`) runs five sequential gates. Each writes a distinct `agent_logs` row with `action="TRADE_REJECTED"`.

| Order | Gate | file:line | On trip |
|---|---|---|---|
| 0 | price available | `:191-193` | returns "Harga {symbol} tidak tersedia" |
| 1 | input validation: `quantity > 0` if set, `leverage > 0`, `price > 0` | `:397-425` | `"Order tidak valid: …"`, `details={"invalid_input": problems}` |
| 2 | `_resolve_tp_sl` → `assess_volatility_gate` | `:449-475` | `VolatilityGateError` caught, `TRADE_REJECTED` with gate meta, `details={"guard": "volatility", ...}` |
| 3 | `_tick_quality_guard` | `:495-514` | `TRADE_REJECTED` with stale/outlier reason |
| 4 | `validate_trade` | `:566-596` | `TRADE_REJECTED` with all `reasons` |
| 5 | `open_position` returns `None` | `:610-644` | `_diagnose_open_failure` distinguishes no-account / insufficient-cash / DB-insert failure |

**Every close path bypasses gates 1-4.** `_tick_quality_guard` has exactly one call site (`:495`, inside `_execute_open`). `validate_trade` is only reached from the open path. The close paths call `position_manager.close_position` directly:

| Close path | file:line |
|---|---|
| `SCALP_EXPIRED` (> `max_hold_seconds` = 300) | `execution_agent.py:270` |
| `SCALP_TP` (profit ≥ `min_profit_pct` = 0.0060) | `execution_agent.py:311` |
| all-symbol `SIGNAL` close | `paper_engine.py:744` |
| SL / TP hit scan | `position_manager.py:357, 363` |

Those paths resolve their price as `market_store.get_price(symbol)` then fall back to `self.engine._last_prices.get(symbol)` (`execution_agent.py:211-215`, `:264-268`, `:288-292`) — a plain dict with no timestamp and no eviction (`paper_engine.py:155-157`). An exit can therefore be filled at an arbitrarily old price, while the open path enforces a 1.5-second age limit.

### 5.9 SL/TP resolution and the volatility gate

#### Formula — `analysis/volatility.py:266-344`

```
raw_sl = atr_multiple(1.5) * ATR_1m
sl_pct = clamp(raw_sl, min_sl_pct(0.0025), max_sl_pct(0.0150))
tp_pct = max(sl_pct * min_risk_reward(1.5), min_profit_pct(0.0060))
```

`used_dynamic` is False whenever ATR is unavailable; the caller then falls back to the static floors (`paper_engine.py:303-308`). The `max()` against static is one-directional by design: volatility may widen the targets, never narrow them below the agreed static config (`:283-293`).

An additional R:R lock runs in `_resolve_tp_sl`: `tp_pct = max(tp_pct, sl_pct * min_rr)` (`paper_engine.py:315`).

#### Runtime gate — `assess_volatility_gate` (`volatility.py:347-412`)

```
roundtrip = taker * 2 = 0.0009
if tp_pct <= roundtrip:            -> reject "setiap trade pasti merugi"
net_tp = tp_pct - roundtrip
net_sl = sl_pct + roundtrip
if net_tp < net_sl and net_sl/(net_tp+net_sl) > 0.65:  -> reject "R:R bersih … butuh win rate …"
```

`max_breakeven_win_rate` is read from `dynamic_tp_sl` (`:392-395`). Under the shipped config the static case gives `breakeven_wr = 0.40 < 0.65`, so the gate does not fire at the floor; it only fires when ATR widens SL enough to push `sl_pct * 1.5` against a `0.65` demand.

#### SL/TP are recomputed at the fill price

`_execute_open` **discards** `order.stop_loss` and `order.take_profit` and rebuilds both from `price` (`:481-482`). Rationale at `:427-445`: `ExecutionAgent.think()` and `execute_order()` are separated by an `await`, so a stop computed from P1 while the fill happens at P2 misprices the actual risk distance — the recorded log symptom was 0-6 second positions, 100 % `SL_HIT`. Locked by `tests/test_fill_price_sl.py:82`.

#### Tick-quality guard — `paper_engine.py:209-268`

Two checks, cheapest first:

| Check | Rule | file:line |
|---|---|---|
| tick age | `market_store.get_price_age(symbol) > max_tick_age_seconds` (1.5) | `:238-244` |
| median outlier | `abs(price - median)/median > sl_pct` over `stale_tick_window_seconds` (3.0), requires `>= stale_tick_min_samples` (5) | `:252-266` |

Deliberate **fail-open** below 5 samples (`:254-255`): refusing on missing history would silence the bot exactly when the feed first connects. This guard replaced provably dead code — the previous check compared `price <= stop_loss` where `stop_loss` was itself derived from `price`, so it could only fire if `sl_pct <= 0` (`:485-494`).

The deviation threshold is `sl_pct` itself, so the static floor that `_resolve_tp_sl` enforces is also what keeps this guard meaningful.

#### Breakeven lock — `execution_agent.py:194-241`

Runs **before** `_scalp_take_profit` (`:182-186`), and `breakeven_trigger_pct` (0.0020) must stay strictly below `min_profit_pct` (0.0060) or TP fires first in the same cycle and the whole path is dead — enforced at boot by `_validate_scalping_economics` (`core/config.py:727-732`) and at `tests/test_config.py:33`.

```
LONG : trigger pnl_pct >= 0.0020  and (sl is None or sl < entry*1.0015) -> sl = entry*1.0015
SHORT: trigger pnl_pct >= 0.0020  and (sl is None or sl > entry*0.9985) -> sl = entry*0.9985
```

Monotonic: it only ever moves the stop inward (`:226`, `:236`). The offset `0.0015` covers the `0.0009` round-trip with `0.0006` to spare.

### 5.10 Position state machine and reconciliation

#### Exactly four edges

```
                    open_position  position_manager.py:47
                   apply_balance_delta(-(margin+fee))   :85
                   insert_position (status='OPEN')     :107
                   insert_trade(trade_type='OPEN')     :125
      (none) ────────────────────────────────────────▶  OPEN
                                                      │
     ┌────────────────────────────────────────────────┼──────────────────────────────┐
     │ update_positions  :349  LIQ > SL > TP           │ _scalp_take_profit :311     │
     │   each branch `continue`s                       │ _auto_close_expired :270    │
     │   _liquidate      :384                          │ DecisionAgent CLOSE order   │
     ▼                                                ▼
 _should_liquidate :366   close_position :150
 LONG: price <= liq          calculate_pnl          SHORT: price >= liq
        SHORT: price >= liq  net_pnl = gross - open_fee - close_fee
                             repo.close_position (claim-once)
                             insert_trade(CLOSE)
                             apply_balance_delta(margin + net_pnl + open_fee)
                             bump_peak_balance(equity_now)
                             update_account_stats
     ▼                                                ▼
 LIQUIDATED                                          CLOSED          (both terminal;
 realized_pnl = -margin, fee=0, no balance delta)                    no reopen, no amend,
                                                                  no partial reduce)
```

#### Trigger precedence — `position_manager.py:349-364`

`LIQUIDATION > STOP_LOSS > TAKE_PROFIT`, each with a `continue`. All comparisons are inclusive:

| Check | LONG | SHORT | file:line |
|---|---|---|---|
| liquidate | `price <= liquidation_price` | `price >= liquidation_price` | `:366-370` |
| SL | `price <= stop_loss` | `price >= stop_loss` | `:372-376` |
| TP | `price >= take_profit` | `price <= take_profit` | `:378-382` |

Liquidation is checked first because a position at its liquidation price is dead regardless of where its stops sit. Under the shipped config the SL is always nearer than the liq price (computed, `mmr = 0.004`):

| leverage | liq price (LONG) | static SL 0.25 % | max SL 1.50 % |
|---|---|---|---|
| 5 | 0.8040 | 0.9975 | 0.9850 |
| 10 | 0.9040 | 0.9975 | 0.9850 |
| 20 | 0.9540 | 0.9975 | 0.9850 |
| 50 | 0.9840 | 0.9975 | 0.9850 |

so the SL always fires first and liquidation is only reachable if the SL is missing or wider than the remaining runway.

#### Claim-once

Both terminal edges are single-claim at the SQL level:

```sql
-- repository.py:160-169 (close) and :172-181 (liquidate)
UPDATE positions SET status = … WHERE id = ? AND status = 'OPEN'
return cursor.rowcount > 0
```

Four close paths can race on the same position (`position_manager.py:357`, `:363`; `execution_agent.py:270`, `:311`; `paper_engine.py:744`, `:705`). The losing caller gets `None` and returns (`position_manager.py:209-211`, `:392-394`), which is treated as legitimate "already closed by someone else", not as an error.

#### Balance accounting

| Event | SQL | file:line |
|---|---|---|
| open | `balance = balance - (margin + fee)` | `position_manager.py:85` → `repository.py:403-425` |
| close | `balance = balance + (margin + net_pnl + open_fee)` | `position_manager.py:243-244` |
| liquidate | no balance mutation | `position_manager.py:391` |
| peak | `peak_balance = MAX(COALESCE(peak_balance,0), ?)` | `repository.py:427-439` |

`apply_balance_delta` exists to replace read-modify-write; its docstring names the lost-update race between the decision scheduler and the execution loop where margin and fees vanished silently (49.30 USDT across the paper session that motivated the test).

Three distinct balance concepts, named explicitly at `paper_engine.py:846-855`. **Verified verbatim:**

```python
        free_balance = account["balance"]
        wallet_balance = free_balance + open_margin
        equity = wallet_balance + unrealized
        total_pnl = equity - account["initial_balance"]
```

The same identity is recomputed independently at `position_manager.py:445-446` (balance snapshot) and in three dashboard callbacks.

#### Not transactional

`apply_balance_delta` commits at `repository.py:421`; `insert_position` commits at `:119`. They are separate transactions. A crash between `position_manager.py:85` and `:107` destroys margin with no position row to explain it. The refund at `:88`/`:111` only runs if the process is alive.

### 5.11 Live reconciliation

| Property | Value |
|---|---|
| Trigger | `LiveEngine.reconcile()` — `engine.py:74-132`; also from `run_loop` every 5 s via `health_check` |
| Source of truth | exchange `info.user_state` (`client.py:160-186`) |
| Size tolerance | 1 % relative — `abs(local-abs(remote))/max(...) < 0.01` (`engine.py:184`) |
| Symbol key | `"{} / USDC:USDC".format(name)` (`engine.py:162`, `:491`) |
| `szi == 0.0` skipped | `engine.py:148-154` (negative zero is indistinguishable from zero) |
| `info.meta()` calls | one, outside the loop (`engine.py:144`) |
| On mismatch | **report only** by default; `auto_reconcile = False` (`core/config.py:353`) |
| On mismatch + no auto-reconcile | `engage_kill_switch` (`engine.py:127-130`) |
| `run.py` startup | `health_check()` runs *before* the loop is created; failure raises `RuntimeError` and the process exits 2 (`run.py:278-282`, `:864-878`) |

**Asymmetry is deliberate** and documented at `engine.py:74-86`: differences are reported and change nothing by default, because auto-closing "because it differs" can delete a position that is actually the operator's own. Only an *unknown remote* position is logged loudly (`engine.py:114-119`), because an unprotected position is the greatest danger.

`health_check` flags four distinct problems, any of which engages the kill switch: exchange-side position with no local record (`:573-576`), local record missing on the exchange (`:580-583`), size mismatch (`:584-586`), resting order with no recorded position (`:590-594`).

**No recovery path exists.** When `health_check` fails at startup, `run.py:280` raises. Nothing ever attaches protection to an orphan. `LiveEngine.check_pending_fills` (`:421-514`) closes the GTC late-fill gap for orders the bot still has a handle on, using `abs(held.get(coin, 0.0))` (`:472`) so a negative (SHORT) size is not skipped, and defaults the stop to ±1 % of the order's `limitPx` (`:484-486`) with the **config leverage cap** rather than the order's actual leverage (`:501`).

### 5.12 Complete catalogue of ways the bot loses money

#### 5.12.1 Live path (real money) — the path is inert

| # | Loss mode | file:line | What happens |
|---|---|---|---|
| L1 | **Every live cycle raises `AttributeError`** | `execution_agent.py:96, 192, 213, 266, 270, 290, 311` vs `executor.py:53-189` | `ExecutionAgent` calls `engine.get_price`, `engine.check_positions`, `engine._last_prices`, `engine.position_manager`. `LiveExecutor` defines only `__init__`, `update_price`, `execute_order`, `_open`, `_close`, `_price_for`. **Re-verified by grep during integration.** Caught by `run.py:596` `except Exception`, which only logs — the loop keeps spinning and the dashboard looks healthy. |
| L2 | **Every live open rejected even if L1 is fixed** | `decision_agent.py:264-272`, `executor.py:103`, `safety.py:319-320` | `Order(...)` is constructed with no `quantity` anywhere in the repo. `LiveExecutor._open` sends `size=order.quantity or 0.0` = `0.0`. `SafetyGate` appends `INVALID_INPUT`. The reported reason names input shape, never the real cause. **Verified verbatim in `_open`.** |
| L3 | **Naked position on crash between fill and protection** | `engine.py:295-349`, `:397-404` | `place_limit_order` and `_attach_protection` are two separate calls. A crash in between leaves the exchange holding an unprotected position. On restart, `reconcile` only logs (`engine.py:114-119`) and `health_check` only flags (`:573-576`); `run.py:280` then refuses to start. The position stays open and unprotected, permanently. |
| L4 | **Gate bypass via size quantization** | `client.py:322-325` called from `client.py:398`, after `engine.py:236` | `quantize_size` rounds a sub-`szMin` size **up** to `szMin`. The gate already approved the smaller notional. A 1e-6 BTC order approved at ~$0.06 can reach the venue at `szMin` (1e-5, ~$0.60) — 10× the approved exposure. |
| L5 | **Failed TP is silently tolerated** | `engine.py:397-404` | Only a failed SL engages the kill switch. `results["tp"]` is captured at `:386-389` and written to `LivePosition.tp_order_id` at `:411`, but no branch escalates a rejected TP. The position then rides to full stop-loss loss. |
| L6 | **Daily-loss breaker is structurally unreachable** | `safety.py:415-417`, `:293-294` | `SafetyGate.record_realized_pnl` has **zero** production callers — **re-verified by grep during integration: the only other hits are `tests/test_live_safety.py:361, 390`**, and that test sets `counters.realized_pnl` *directly*, so it passes while the limit can never fire. `counters.realized_pnl` is permanently `0.0`, so `DAILY_LOSS_LIMIT` can never fire despite `LiveConfig.max_daily_loss = 50.0`. |
| L7 | **Late-filled position gets 1 % stop and cap leverage** | `engine.py:484-501` | `check_pending_fills` synthesises `stop = price*0.99/1.01` and `TP = price*1.01/0.99` — 1 % vs the engine's 0.25 % and `dynamic_tp_sl.max_sl_pct` 0.0150. No volatility gate, no R:R lock. Leverage is `getattr(cfg, "max_leverage", 10) or 10` — the ceiling, not what was traded. |
| L8 | **Operator-edited limits are discarded** | `console.py:587-595`, `run.py:171, 199, 262` | `edit_rules` mutates a `LiveConfig` in memory; `ask_mode` returns a `ModeDecision` and the mutated config is dropped; `_build_live_executor` builds a fresh bare `LiveConfig()`. The editor can set `max_daily_loss` to 1,000,000 (bounds at `console.py:496`); the applied value is always `50.0`. |
| L9 | **Kill switch never released, live task never cancelled** | `safety.py:384-388`, `run.py:292, 794-814` | No code path clears `SafetyGate.engaged`. `shutdown()` iterates only `self._background_tasks` (`:807-808`); `self._live_task` (`:292`) is never cancelled, so the exchange poll loop survives shutdown. `shutdown()` is also reachable from both the signal handler (`:832`) and the `finally` (`:843`) and is not idempotent. |
| L10 | **No retry, no backoff, no rate-limit enforcement** | `client.py:226-236` documents the limit; nothing consumes it | Every `LiveExchange` call is a bare `try/except` returning failure. Hyperliquid allows 1 request per 1 USDC traded; a 0.3 s scalping loop exhausts the quota long before P&L realises, and transport errors, auth errors and genuine bugs are indistinguishable to the caller. |

#### 5.12.2 Paper path — the only path that actually trades

| # | Loss mode | file:line | What happens |
|---|---|---|---|
| P1 | **Fill model has no slippage, spread, depth, or impact** | `paper_engine.py:159-179`, `:191` | The fill price is the last cached price verbatim. Round-trip cost is only the double fee (`0.0009`). A 0.25 % stop with a realistic 0.05-0.1 % spread means the stop is inside the spread on real venues. |
| P2 | **Exit fills use an unbounded-stalency price** | `execution_agent.py:211-215, 264-268, 288-292`; `paper_engine.py:155-157` | Exit paths read `market_store.get_price` then fall back to `self.engine._last_prices`, a plain dict with no timestamp and no eviction. A position can be closed at a price from minutes ago. |
| P3 | **All close paths bypass tick guard, volatility gate and `validate_trade`** | single `_tick_quality_guard` call at `paper_engine.py:495`; `validate_trade` only at `:566` | Only `_execute_open` is guarded. Expiry close, scalp-TP close and the all-symbol close call `position_manager.close_position` directly. |
| P4 | **Liquidation under-reports realized loss** | `position_manager.py:391` vs `:203` | Normal close deducts both fees; liquidation records `realized_pnl = -margin` and `fee = 0`, omitting the open fee already deducted at open. `get_daily_realized_pnl` (`repository.py:526-547`) sums exactly this column, so **the daily-loss breaker reads a smaller loss than the balance actually took** and fires later than it should. |
| P5 | **Liquidation leaves account aggregates stale** | `position_manager.py:384-418` | `_liquidate` skips `bump_peak_balance` and `update_account_stats` (compare `:255-269` in `close_position`). `account.total_trades`, `winning_trades`, `losing_trades`, `profit_factor` and `max_drawdown` stay stale until the next *normal* close, and the drawdown high-water mark is not raised. |
| P6 | **Drawdown breaker compares two different quantities** | `paper_engine.py:549` vs `position_manager.py:255-256` | The mark includes unrealized P&L (`free + margin + upnl`); the peak is bumped from `new_balance + remaining_margin` only. Numerator and denominator are different measures, so the reported ratio is systematically overstated and the high-water mark never captures intra-trade equity gains. |
| P7 | **Position open is not transactional** | `position_manager.py:85` then `:107` | Two separate commits. A crash between them destroys margin with no position row. |
| P8 | **Third, un-injected `RiskManager` degrades the daily-loss denominator** | `execution_agent.py:40` | Three `RiskManager` instances exist: `paper_engine.py:60` (injected from DB at `:86-88`), `execution_agent.py:40` (bare, `initial_balance = 0.0`), and the one DecisionAgent receives. On the un-injected one, `risk_manager.py:319` falls back to `balance` as the denominator — the exact shrinking-denominator failure the comment at `:306-318` warns against. |
| P9 | **Non-transactional close: CLOSED row without a trade row** | `repository.py:168` then `:212` | `close_position` and `insert_trade` are separate transactions. A crash between them leaves a closed position with no recorded trade — the fee and the gross are unrecoverable from the ledger. |
| P10 | **No funding cost anywhere** | grep `funding` in `trading/` → 0 hits | Perpetual funding is never accrued against an open position. A position held across funding timestamps over-states its net result. |
| P11 | **Scaler sizing ignores stop distance** | `risk_manager.py:340-378` | `calculate_scalp_position_size` has no `stop_loss_price` parameter. Real per-trade risk at the shipped config is `0.017 %` of balance (computed: `notional 500 × (0.0025 + 0.0009) = 1.70` on 10,000), not the `0.5 %` the key name implies — while the daily-loss breaker is calibrated against `initial_balance`, not against actual per-trade exposure. |
| P12 | **Scalping disabled → hardcoded 2 %/4 % bypasses every guard** | `paper_engine.py:476-479` | With `scalping.enabled = False`, `sl_pct = 0.02` / `tp_pct = 0.04` are literals. No `assess_volatility_gate` call (contrast `:325`), and the tick guard's deviation threshold becomes 2 % instead of 0.25 %. |
| P13 | **Sharpe / max_drawdown columns are never written** | `repository.py:356-393` | `update_account_stats` is called with `total_pnl`, `total_trades`, `winning_trades`, `losing_trades`, `profit_factor` only. `max_drawdown` and `sharpe_ratio` are never set; the dashboard reads them as `N/A` rather than `0` for that reason. |
| P14 | **`peak_balance` is never reset or recomputed** | `position_manager.py:258` | Monotonic by SQL design (`repository.py:427-435`). After a session, drawdown is measured against a high-water mark accumulated across all sessions. |
| P15 | **Backtest P&L formula is a hand-maintained duplicate** | `backfill_realized_pnl.py:47-50` vs `position_manager.py:203` | `net_pnl(side, entry, close, qty, open_fee, close_fee)` must be kept byte-identical to production by hand. It uses SQL `SUM(fee)` per side where production reads the single OPEN fee and the computed close fee. The two will diverge silently. |

#### 5.12.3 Correct paths (do not regress)

| Guarantee | file:line |
|---|---|
| Fee is never negative and is symmetric | `risk_manager.py:216-240`; `tests/test_numerics.py:18` |
| `ROUND_DOWN` everywhere, never half-even | `risk_manager.py:126-128, 155, 179, 200, 238-239, 373-377` |
| Atomic balance mutation, no read-modify-write | `repository.py:403-425`; `tests/test_position_manager.py:147` |
| Claim-once close and liquidate | `repository.py:147-181`; `tests/test_position_manager.py:183` |
| Negative quantity cannot create money | `paper_engine.py:397-425` |
| Margin validation includes the fee | `paper_engine.py:556-557`; `tests/test_lifecycle_paths.py:507` |
| `realized_pnl` net of both fees, open fee charged once | `position_manager.py:196-243`; `tests/test_lifecycle_paths.py:219, 258` |
| Daily-loss denominator frozen to `initial_balance` | `risk_manager.py:306-322`; `tests/test_bugfixes.py:199` |
| Margin cap uses cash, drawdown uses equity | `risk_manager.py:286-291, 329-333` |
| Boot-time economic validators are fatal and complete | `core/config.py:525-527, 601-626, 707-786` |
| `check_positions` ordering is load-bearing | `execution_agent.py:182-192`; `tests/test_config.py:33` |
| Partial close reports failure, not optimism | `paper_engine.py:742-807`; `tests/test_bugfixes.py:599` |
| Live gate order: gate → leverage → order → protect | `engine.py:217-229, 282-285, 295, 338`; `tests/test_live_engine.py:135` |
| Live trigger direction is the CLOSING direction | `engine.py:372-378`; `tests/test_live_engine.py:216` |
| Corrupt counter file blocks all orders | `safety.py:103-125, 261-262`; `tests/test_live_safety.py:400` |
| NaN/infinity explicitly rejected in live rule editor | `console.py:523-524` |
| Snapshot staleness is a hard gate | `decision_agent.py:340-346`; `tests/test_decision_agent_ensemble.py:112` |

### 5.13 Known gaps

Limits and guards that are **defined but not enforced on the path that matters**, plus unguarded exception paths.

#### 5.13.1 Defined but not enforced

| # | Item | Defined at | Gap |
|---|---|---|---|
| G1 | `SafetyGate.record_realized_pnl` | `safety.py:415-417` | Zero production callers → `DAILY_LOSS_LIMIT` (`:293-294`) can never fire. Live has no daily-loss breaker at all. **Re-verified.** |
| G2 | `Blocker.RECONCILIATION_FAILED` | `safety.py:45` | Never appended anywhere. A reconcile mismatch routes to `engage_kill_switch` (`engine.py:128`) and surfaces as `KILL_SWITCH` instead. |
| G3 | `LiveConfig.use_exchange_side_tpsl` | `core/config.py:347` | Zero readers. `_attach_protection` (`engine.py:351`) always attaches. |
| G4 | `LiveConfig.reconciliation_tolerance_days` | `core/config.py:356` | Zero readers. |
| G5 | `risk.max_leverage` | `config.yaml:38` = `50` | Applied only inside sizing (`risk_manager.py:97, 358`). The raw `order.leverage` reaches `open_position` (`paper_engine.py:604`) and computes the stored margin and liquidation price un-clamped (`position_manager.py:67-72`). |
| G6 | `scalping.max_spread_pct` = `0.0006` | `config.yaml:158` | Read only in `DecisionAgent.think` (`decision_agent.py:205-213`) and only when an order book exists. `ob` is falsy → the spread check is skipped entirely and the entry proceeds. |
| G7 | `scalping.orderbook_imbalance_threshold` = `0.60` | `config.yaml:156`, `core/config.py:139` | Zero readers in first-party code. |
| G8 | `DynamicTpSlConfig.{fast_tp_pct, tight_sl_pct, min_confidence, orderbook_imbalance_threshold, momentum_threshold, max_spread_pct, breakeven_trigger_pct, breakeven_offset_pct, cooldown_after_close_seconds, cooldown_after_loss_seconds}` | `core/config.py:200-215` | Ten shadow duplicates of `ScalpingConfig` with identical defaults. Every real read goes through `scalping`. The dynamic block is only read for `atr_multiple`, `atr_period`, `min_sl_pct`, `max_sl_pct`, `min_risk_reward`, `realized_window_seconds`, `max_breakeven_win_rate`. |
| G9 | `LiveConfig.enabled` | `core/config.py:307` | Force-written to `False` at `core/config.py:482`; consumed only by `base_weight()`/`agent_configs()`. `SafetyGate` uses the env var instead, so the field gates nothing. |
| G10 | `Order.order_type` | `trading/models.py:43` | Never read. Both fee legs hardcode `"TAKER"` (`position_manager.py:75, 181`), so LIMIT and MARKET fill identically. |
| G11 | `RiskManager.kelly_criterion`, `calculate_max_drawdown`, `calculate_sharpe_ratio` | `risk_manager.py:380, 397, 414` | No first-party consumer; tests only. `account.sharpe_ratio` / `account.max_drawdown` are consequently never populated. |
| G12 | `PositionManager.batch_close_positions` | `position_manager.py:298` | No runtime caller; tests only. |
| G13 | `dashboard.update_interval` = `2000` | `config.yaml:123`, `core/config.py:73` | Zero readers. `dashboard/app.py:53, 63` hardcode 500 ms and 60,000 ms. |
| G14 | `agent_intervals.funding_rate` = `28800` | `config.yaml:72`, `core/config.py:44` | Never registered as a scheduler job in `run.py:487-585`. |
| G15 | `0.9` free-cash margin cap | `risk_manager.py:299, 119, 364` | A genuine exposure tunable with **no config key**, duplicated in three places. |
| G16 | max-1-position-per-symbol | `decision_agent.py:217` (`sym_count >= 1`) | Hardcoded literal, unlike `batch_size` and `max_open_positions`. |
| G17 | loss-streak cooldown multiplier cap of 4 | `decision_agent.py:77` (`min(streak, 4)`) | Hardcoded literal; caps the cooldown at `90 × 4 = 360 s`. |
| G18 | `DecisionAgent._global_loss_streak` | `decision_agent.py:46, 78` | Incremented on every losing close and never read. Almost certainly an unwired global daily-loss breaker. |
| G19 | `LiveEngine.emergency_flat` | `engine.py:655-756` | Never called from `run.py`. The only flatten routine in the system is not wired to any trigger, including the kill switch. **Re-verified by grep.** |
| G20 | `agent_log_prune_interval` / `agent_log_keep` | `config.yaml:115-116` | The loader's prune filter is `k.startswith("snapshot_")` (`core/config.py:509-511`), so these two YAML keys never reach `AppConfig`. `run.py:464, 466` reads them via `getattr` with defaults 5000 / 1800 — the dataclass values, not YAML. Today they coincide; a future YAML edit is silently discarded. |
| G21 | `process_wide` `_last_prices` cache | `paper_engine.py:68, 157` | Never expired and never evicted, yet is the fallback price for every exit path and the preferred price for every fill. |

#### 5.13.2 Unguarded exception paths

| # | Path | file:line | Why unguarded |
|---|---|---|---|
| U1 | `_execution_loop` blanket catch | `run.py:591-597` | `except Exception` logs and continues. A permanently broken live interface produces a healthy-looking log full of identical errors and zero orders. Nothing escalates. |
| U2 | `BaseAgent.run_cycle` | `agents/base_agent.py:99-117` | Swallows every `Exception` into an `AgentLog` row. A systematically failing agent is indistinguishable from an idle one except by reading row counts. |
| U3 | `DirectionEnsembleAgent.run_cycle` | `agents/direction_agents.py:437-451` | Overrides `run_cycle` entirely: **no `AgentLog` row, no `CancelledError` branch, no error telemetry** beyond a single `logger.error`. The single writer of `direction_snapshots` — the table both the decision layer and the HUD read — has the weakest error surface in the system. |
| U4 | `ExecutionAgent.act` | `execution_agent.py:163-172` | `except Exception` per order, `logger.error`, continue. A live rejection is logged and the next order is attempted. |
| U5 | `analysis/volatility.py::_load_candles` | `:249-258` | `except Exception` → `logger.debug` → `None`. Every candle-source failure silently degrades to static targets, including the failure of the candle refresh loop itself. |
| U6 | `PaperTradingEngine._refresh_cache` / `refresh_volatility_cache` | `:114-121, 147-153` | Same shape: any DB error becomes a debug line, and dynamic TP/SL is permanently off with no operator-visible signal. |
| U7 | `PaperTradingEngine._candle_refresh_task` | `:67, 135` | `asyncio.create_task` handle stored, never awaited, never cancelled, never inspected. If the engine is re-initialised the previous task is orphaned. |
| U8 | `LiveEngine` — every `LiveExchange` call | `client.py:355-360` and each method | Bare `except Exception # noqa: BLE001` returning `OrderOutcome` or `0.0`. Transport errors, auth errors and genuine bugs are indistinguishable. `run_loop` additionally catches everything and records it as a single `consecutive_errors` tick (`engine.py:649-651`). |
| U9 | `LiveExecutor.execute_order` | `executor.py:66-75` | Every failure path returns a dict; nothing propagates. A broken order path in live mode is invisible to the caller. |
| U10 | `trading/live/safety.py:95` | `:95` | `def to_dict(self) -> Dict[str, Any]` annotates `Dict`, which is **not imported** (`from typing import Any, List, Optional` at `:22`). It survives only because `from __future__ import annotations` (`:14`) makes annotations lazy strings. Any runtime introspection touching this method raises `NameError`. |
| U11 | `trading/live/engine.py:32` | `:32` | `Blocker` imported, never used — dead import on the safety path. |
| U12 | `data/price_feed.py` Hyperliquid branches | `:356-363` and the funding/mark branches | Bare `except Exception: pass` — an HL-tier failure drops silently to ccxt with **no log line**, unlike the yfinance/CoinGecko tiers which log at debug. |
| U13 | `market_store` — no lock | `core/market_store.py:23` | Docstring says "thread-safe"; the class has none. The Hyperliquid feed thread and the Dash callback thread both write. Safety rests on the GIL plus whole-entry replacement. |
| U14 | `get_db()` singleton | `database/db.py:239-246` | Not concurrency-safe. Two concurrent first-callers both construct a `Database`; the second overwrites the first and leaks its connection. |
| U15 | `init_account` | `database/repository.py:324-335` | Check-then-insert with no transaction and no `UNIQUE` on `account`. Two concurrent callers can insert two rows; every later mutation targets `WHERE id = (SELECT MAX(id) FROM account)`, so both are mutated, but the older is orphaned. |
| U16 | `direction_agents._load_closes` | `agents/direction_agents.py:331-364` | A separate synchronous `sqlite3` connection opened per call against the same WAL file. Justified (cross-thread aiosqlite is unsafe) but it is a fourth SQL surface outside `Repository`. |
| U17 | `dashboard/callbacks/update_callbacks.py::_get_sync_db` | `:64-69` | A raw `sqlite3.connect` per callback invocation (14 call sites, ~22 connects/sec at the 500 ms cadence), closed only on the success path. |
| U18 | `run.py:434-469` prune jobs | `:450, 468` | `except Exception` with a log line only. A permanently failing prune grows `agent_logs` and `direction_snapshots` without bound and nothing escalates. |
| U19 | `tests/test_lifecycle_paths.py` | `:75` and `:209` | `class TestFeeAccounting` is defined **twice** with the same name. The first definition — including `test_realized_pnl_is_net_of_both_fees` — is shadowed and never runs. The fee invariant it guarded is currently protected only by the second class's two tests. |
| U20 | `analysis/probability_engine.py:223` | `:223` | `calculate_order_flow_imbalance(order_book)` with no symbol allocates a throwaway `PythonKernel` and keys it `"__ad_hoc__"`, bypassing the process-wide kernel entirely. Results are identical by construction, but the ad-hoc path silently never exercises the C++ kernel in production. |

---

## 6. Native extension and numerics

### 6.1 The boundary in one picture

```
  Python (agents, dashboard, analysis)                  C++ (cpp_microstructure)
  ───────────────────────────────────────────            ─────────────────────────
  data/hyperliquid_feed.py:458
    microstructure.ingest_l2(symbol, bids, asks)
        │
        ▼
  core/microstructure.py  ── process-global _KERNEL (core/microstructure.py:182)
        │  register_kernel() rebound once at boot
        │
        ├── PythonKernel          (pure, reference, always identical)
        └── CppMicrostructureKernel  (core/microstructure.py:260-306)
                │  import cpp_microstructure
                ▼
        pybind11 → cpp/bindings.cpp
                    MicrostructureKernel{ingest_l2, order_flow_imbalance,
                                         depth_imbalance, ingest_json, reset,
                                         symbol_count}
                    level_weight(i) -> float
                    MAX_LEVELS = 32, __version__ = "1.0.0"
```

Everything the C++ code computes is also computed, identically, by `PythonKernel`. The C++ path exists only for speed. The only fallback in the whole design is at the Python level.

### 6.2 Exported surface — exactly four names

Verified by importing the shipped `.pyd` (`dir()` on the live module).

| Symbol | Kind | Signature | Source |
|---|---|---|---|
| `MicrostructureKernel` | class | `__init__(max_symbols: int = 64)` | `cpp/bindings.cpp:53-57` |
| `.ingest_l2` | method | `(symbol: str, bids, asks) -> None` | `cpp/bindings.cpp:80-94` |
| `.order_flow_imbalance` | method | `(symbol: str, depth: int = 5) -> tuple[float, float]` | `cpp/bindings.cpp:59-68` |
| `.depth_imbalance` | method | `(symbol: str, depth: int = 5) -> float` | `cpp/bindings.cpp:70-78` |
| `.ingest_json` | method | `(payload: bytes) -> str \| None` | `cpp/bindings.cpp:96-134` |
| `.reset` | method | `(symbol: str \| None = None) -> None` | `cpp/bindings.cpp:136-150` |
| `.symbol_count` | method | `() -> int` | `cpp/bindings.cpp:152-158` |
| `level_weight` | function | `(index: int) -> float` → `1.0, 0.9, 0.8, 0.7, 0.6, 0.5` | `cpp/bindings.cpp:160` |
| `MAX_LEVELS` | attr | `int` = `32` | `cpp/bindings.cpp:162`, `cpp/include/microstructure_kernel.h:29` |
| `__version__` | attr | `str` = `"1.0.0"` | `cpp/bindings.cpp:163` |

Nothing else is exported. There is no binding for `Book::overflow_bid()` / `overflow_ask()` even though both accessors exist in the header (`cpp/include/microstructure_kernel.h:51-52`).

### 6.3 Data contract, argument by argument

#### `ingest_l2(symbol, bids, asks)`

| Aspect | Contract | Source |
|---|---|---|
| `symbol` | Python `str` → pybind converts to `std::string`, then `.c_str()` + `.size()`. The length is passed but **unused** on the compute path. | `cpp/bindings.cpp:82`, `cpp/microstructure_kernel.cpp:290-295` |
| `bids`/`asks` | Arbitrary Python iterable of 2-element sequences. Each item is `item.cast<std::pair<double,double>>()`. | `cpp/bindings.cpp:36-43` |
| list vs tuple | Both work. Empirically verified: `[100.0, 1.0]` and `(100.0, 1.0)` yield identical OFI `0.7478991596638656`. | `cpp/bindings.cpp:37` |
| numpy arrays | **Not supported** and not present anywhere. The stack trace names the failing type as `list`, not `ndarray`; no caller passes one. | — |
| Overflow | Levels past 32/side are silently dropped and an internal flag is set. | `cpp/microstructure_kernel.cpp:96-98, 107-109` |
| Store full | Raises `RuntimeError: store mikrostruktur penuh: jumlah simbol melebihi batas`. Verified: `max_symbols=2`, third distinct symbol → that message. | `cpp/bindings.cpp:87-90`, `cpp/microstructure_kernel.cpp:130-132` |

**DEFECT (high): `book_from_python` is `noexcept` but calls `item.cast<>()`, which throws.**

`cpp/bindings.cpp:32-33` declares the helper `noexcept`. A `py::cast_error` thrown inside therefore cannot propagate; it hits `std::terminate`. Verified — each of these aborts the interpreter with `what(): Unable to cast Python instance of type <class 'list'> to C++ type` and process exit code `3221226505`:

| Input | Result |
|---|---|
| `[[1.0, 2.0, 3.0]]` (3-tuple) | process abort |
| `[[1.0, None]]` | process abort |
| `[{'px':1.0,'sz':1.0}]` (dict) | process abort |
| `[['100.0','1.0']]` (numeric strings) | process abort |

No Python traceback, nothing catchable by the caller. Production is safe today only because the single write caller normalises first: `data/hyperliquid_feed.py:313-350` (`_parse_book`) coerces `px`/`sz` to `float` inside `try/except (KeyError, TypeError, ValueError): continue` and drops any level with `sz <= 0`. The safe fix is to drop `noexcept` from `book_from_python` so pybind11 translates the `cast_error` into an ordinary Python exception.

#### `order_flow_imbalance(symbol, depth) -> (ofi, rel_spread)`

`depth` is `std::uint32_t`. Verified: `depth=-1` → `TypeError`, `depth=2**32` → `TypeError`, `depth=0` → `(0.0, 0.0)`, `depth=2**32-1` accepted (clamped to available levels).

#### `ingest_json(payload) -> str | None`

`payload` must be `bytes`; the binding takes the raw `PyBytes_AsStringAndSize` pointer and releases the GIL for the whole parse (`cpp/bindings.cpp:101-117`). Return contract, measured:

| Payload | Returns |
|---|---|
| valid `l2Book` | `'BTC'` (the coin name) |
| `channel != "l2Book"` | `None` |
| truncated mid-number | `RuntimeError: NumberMalformed` |
| level missing `sz` | `RuntimeError: MissingField` |
| exponent notation `"1e5"` | `RuntimeError: NumberMalformed` (no exponent support) |
| 80-char coin name | `RuntimeError: MissingField` (`symbol_out[64]` bound) |
| key order swapped (`data` before `channel`) | `'ETH'` — key order irrelevant |
| trailing bytes after closing `}` | `'BTC'` — **accepted**, no EOF check |
| unquoted numbers `{"px":100,"sz":1}` | `'SOL'` — both paths supported |
| empty payload | `RuntimeError: payload kosong` |

Tri-state is encoded in the **error buffer**, not the exception type: `error_out[0] == '\0'` means "not an l2Book payload, ignore it"; non-empty means genuinely corrupt (`cpp/bindings.cpp:119-125`). `char symbol_out[64]` and `char error_out[128]` are C++ stack buffers, `memset` to zero at `cpp/bindings.cpp:107-108`.

Ordering subtlety: `if (!is_l2book)` at `cpp/microstructure_kernel.cpp:606` sits **before** the corruption check at `:613`, so a payload that is both malformed and not-l2Book returns `None` instead of raising. Rationale is at `:343-347` — the feed multiplexes many channels and a `trades` message must not stop the pipeline.

#### GIL policy

| Method | GIL | Source |
|---|---|---|
| `order_flow_imbalance` | released | `cpp/bindings.cpp:63` |
| `depth_imbalance` | released | `cpp/bindings.cpp:74` |
| `ingest_json` | released | `cpp/bindings.cpp:113` |
| `reset` | released | `cpp/bindings.cpp:146` |
| `symbol_count` | released | `cpp/bindings.cpp:155` |
| `ingest_l2` | **held** — iterates Python lists | `cpp/bindings.cpp:84-86` |

Stated reason for releasing beyond speed (`cpp/bindings.cpp:5-10`): asyncio reacquires the GIL in the inverse order to the kernel's execution order, so holding it would make results differ run-to-run. Locked by `tests/test_cpp_kernel.py:316-322` (source-text assertion).

### 6.4 Build system

```
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release
```

Output is written to the **project root**, not `build/`, so `import cpp_microstructure` works with no `PYTHONPATH` (`CMakeLists.txt:149-152`).

| Setting | Value | Source |
|---|---|---|
| Standard | `CMAKE_CXX_STANDARD 20`, `STANDARD_REQUIRED ON`, `EXTENSIONS OFF` | `CMakeLists.txt:24-26` |
| pybind11 discovery | `python -m pybind11 --cmakedir` first, `find_package(pybind11 CONFIG REQUIRED)` fallback | `CMakeLists.txt:40-56` |
| Actual compile line | `-O3 -DNDEBUG -std=c++20 -fvisibility=hidden -O2 -Wall -Wextra -fvisibility=hidden` | `build/build.ninja:56, 68` |
| Effective optimisation | **O2**, not O3 — the target's `-O2` comes after `CMAKE_CXX_FLAGS_RELEASE`'s `-O3` and wins | `CMakeLists.txt:109-114` |
| Link line | `-shared -static-libgcc -static-libstdc++ -Wl,-Bstatic -lwinpthread -Wl,-Bdynamic` | `build/build.ninja:87` |
| POST_BUILD | `C:\msys64\ucrt64\bin\strip.exe <pyd>` | `build/build.ninja:90` |
| Python linked | `.../pythoncore-3.14-64/libs/python314.lib` | `build/build.ninja:88` |
| Generator | Ninja, single-config Release | `build/CMakeCache.txt:302` |
| Compiler | `C:/msys64/ucrt64/bin/c++.exe` | `build/CMakeCache.txt:28` |

#### Is the prebuilt binary in sync with source? — YES, verified three ways

| Check | Result |
|---|---|
| `ninja -C build -n` | `ninja: no work to do.` |
| `cpp_microstructure.cp314-win_amd64.pyd` mtime | `2026-09-27 23:01:23.542` |
| Newest source | `CMakeLists.txt` `2026-09-27 22:33:06.571`; `microstructure_kernel.cpp` `22:31:57.573`; `bindings.cpp` `22:22:19.831`; `microstructure_kernel.h` `22:33:06.571`; `simdjson.h` `20:12:02.091` |
| `build/.ninja_log` last entry | `7592 7792 … cpp_microstructure.cp314-win_amd64.pyd` |
| Size | 408,576 bytes (listed as 399.0K by `ls -l`) |

The `.pyd` is **CPython-3.14-specific and not limited-API** (ABI tag `cp314`, links `python314.lib`, interpreter is 3.14.6 per `build/CMakeCache.txt:388`). On any other minor version both parity suites skip silently rather than fail (`tests/test_cpp_kernel.py:73`, `tests/test_lifecycle_paths.py:399-404`).

#### Runtime DLL dependencies of the shipped binary

`objdump -p` on the shipped `.pyd` lists: `KERNEL32.dll`, nine `api-ms-win-crt-*-l1-1-0.dll` (including `api-ms-win-crt-private-l1-1-0.dll`), `libwinpthread-1.dll`, `python314.dll`.

`libwinpthread-1.dll` survives despite `-static-libgcc -static-libstdc++ -Wl,-Bstatic -lwinpthread` because `std::mutex` pulls it in through `libstdc++` regardless (`cpp/include/microstructure_kernel.h:74-83` documents the chain and records that a header-only spinlock replacement was tried and abandoned). Two sidecar DLLs sit at the project root to satisfy it; both are `.gitignore`d (`.gitignore:72-73`), and the `.pyd` itself is gitignored (`.gitignore:62`) despite being present on disk.

### 6.5 SIMD and runtime feature detection — THERE ARE NONE

The natural presumption of SIMD/AVX paths with runtime CPU detection and fallback **does not hold for this codebase**.

- `cpp/include/simdjson.h` is a **misnomer**. Its own header comment (lines 4-33) states it is NOT upstream simdjson; it is a hand-written, single-pass, zero-copy, scalar byte-scanning JSON-subset parser specialised for the Hyperliquid l2Book shape.
- A grep for `immintrin|__m128|__m256|_mm_|cpuid|__builtin_cpu|avx|sse` across `cpp/` returns **exactly one hit**: a comment at `cpp/include/simdjson.h:16` mentioning `-DHL_JSON_USE_SIMDJSON=1`. There is no intrinsic anywhere.
- The compile line has no `-march` / `-mtune`, so codegen is baseline x86-64.

**Consequence: there is no CPU-dependent code path, therefore no runtime detection and no dispatch/fallback logic.** The only fallback in the design is the Python/kernel swap at `core/microstructure.py:309-369`.

#### The upstream-simdjson switch is inert

`HL_JSON_USE_SIMDJSON` is `OFF` in the shipped build (`build/CMakeCache.txt:223-226`, `SIMDJSON_ROOT:PATH=` empty). Even when `ON`, `CMakeLists.txt:89-99` only adds an include directory and `-DHL_JSON_USE_SIMDJSON=1` — no source file `#ifdef`s that macro, so the documented upstream path would not actually compile. The only reference to the macro in `cpp/` is the comment at `simdjson.h:16`.

#### `simdjson.h` dead members

`Scanner::find_key` (`:169`), `Scanner::reset/position/size` (`:89-94`) and `json::Status::TooManyLevels` (`:55`, with its `status_text` branch at `:65`) are defined and never referenced. `TooManyLevels` is unreachable because overflow is handled by `Book`'s flag, not by the parser.

### 6.6 The Python kernel, and why parity is bit-exact

`PythonKernel` (`core/microstructure.py:81-176`) is both the production default and the reference the C++ must match.

```python
@staticmethod
def _weighted_volume(levels: list, depth: int) -> float:   # core/microstructure.py:122-128
    total = 0.0
    for i, (_, size) in enumerate(levels[:depth]):
        total += size * (1.0 - 0.1 * i)
    return total
```

The C++ mirror is `weighted_volume` at `cpp/microstructure_kernel.cpp:192-198`, which **re-hardcodes the literal** `sizes[i] * (1.0 - 0.1 * static_cast<double>(i))` rather than calling the exported `level_weight(i)`.

**Consequence: `kLevelWeight0` / `kLevelWeightStep` (`cpp/microstructure_kernel.cpp:57-58`) are dead code on the hot path.** They feed only `level_weight()`. Changing `kLevelWeight0` to 2.0 would make the exported function report 2.0 while the actual OFI math stayed at 1.0 — the exported helper would lie. The constants exist so `tests/test_cpp_kernel.py:264-281` can grep the source text on machines with no compiler; that is a stated rationale, not an accident.

**Parity measured, not assumed.** Across 300 randomized books (0-25 levels per side, depths drawn from 1/3/5/10/20/32/64) plus a 20-level production-shaped book, the maximum `|Python − C++|` over `ofi`, `rel_spread` and `depth_imbalance` was **0.0** — bit-identical, not merely inside the `TOLERANCE = 1e-7` declared at `tests/test_cpp_kernel.py:37`.

That exactness is not luck. FP addition is not associative, so both sides must sum in the same level order. The C++ comment states reversing it "bisa mengubah digit terakhir" (`cpp/microstructure_kernel.cpp:186-191`). `CMakeLists.txt:14-18` refuses `-Ofast` / `-ffast-math` for the same reason, locked by `tests/test_cpp_kernel.py:324-341`.

#### Deliberate asymmetry between the two OFI/depth functions

| Case | `order_flow_imbalance` | `depth_imbalance` |
|---|---|---|
| unknown symbol | `(0.0, 0.0)` | `0.0` |
| either side empty | `(0.0, 0.0)` (`:213`) | **not short-circuited** — bid-only gives `+1.0`, ask-only `-1.0` (`:256-278`) |
| `denom <= 0.0` | `(0.0, 0.0)` | `0.0` |

All verified empirically. The asymmetry is intentional and commented as parity-critical (`cpp/microstructure_kernel.cpp:258-260`): `PythonKernel.depth_imbalance` (`core/microstructure.py:160-170`) likewise does not check for an empty side, only `denom <= 0.0`.

#### `reset` semantics diverge between the two implementations

| | Python | C++ |
|---|---|---|
| `reset(symbol)` | `self._books.pop(symbol, None)` — **removes** the registration (`core/microstructure.py:176`) | `it->second.clear()` — **keeps** the registration (`cpp/microstructure_kernel.cpp:158-168`) |
| `reset()` | `_books.clear()` | `books_.clear()` — equivalent |

Observable: after `k.reset('A')` on a 2-symbol store, `symbol_count()` is still `2` in C++ (`ofi` becomes `(0.0, 0.0)` because the book is empty), while `PythonKernel._books` is empty. Only visible via `symbol_count()` or re-ingest, but it is a real contract divergence.

#### Silent truncation past 32 levels, with no signal

Ingesting 40 levels per side returns a plausible-looking OFI with 12 levels discarded per side and no error. `overflow_bid_` / `overflow_ask_` are set (`cpp/microstructure_kernel.cpp:96-98, 107-109`) but never bound to Python. This directly contradicts the module's own stated principle that "book yang utuh tapi salah jauh lebih berbahaya daripada book kosong" (`cpp/microstructure_kernel.cpp:93-95`). Never fires today (Hyperliquid sends 20 levels).

#### Thread-safety argument is incomplete

`cpp/include/microstructure_kernel.h:68-72` justifies the `BookStore` mutex by claiming the GIL guarantees one book is touched by one thread at a time. But `order_flow_imbalance` releases the GIL (`cpp/bindings.cpp:63`) **before** calling `store_.find()`, and `find()` returns a raw `const Book*` into the map after dropping the mutex (`cpp/microstructure_kernel.cpp:137-145`, `return &it->second;` outside the `lock_guard` scope). `book_ofi` then dereferences that pointer with no lock held. Pointer stability rests solely on `books_.reserve(max_symbols_)` at `cpp/microstructure_kernel.cpp:118`.

### 6.7 Activation, fallback, and the kernel-identity invariant

`initialize_native_kernel(force=False) -> bool` (`core/microstructure.py:309-369`) is the only production entry into the native kernel, called from `data/hyperliquid_feed.py:115` inside `_ensure_native_kernel()` (defined `:99`, called `:513`).

| `TRADEBOT_KERNEL` | Behaviour | Source |
|---|---|---|
| `python` | returns `False` immediately, `PythonKernel` stays active. `force=True` still overrides so tests can compare. | `core/microstructure.py:331-335` |
| `cpp` | `import cpp_microstructure` failure raises `RuntimeError` with the two cmake lines. A later activation failure also raises. Silent downgrade is treated as hiding an operator misconfiguration. | `core/microstructure.py:336-345, 357-363` |
| absent / anything else | tries native; on any exception logs at INFO and returns `False` | `core/microstructure.py:347-369` |

`register_kernel` (`core/microstructure.py:190-215`) validates `ingest_l2`, `order_flow_imbalance`, `depth_imbalance`, `reset` are all callable **before** swapping (`:201-209`), so an incomplete kernel leaves the previous one active rather than failing on the first feed. Note the ABC at `core/microstructure.py:57-78` declares only three `@abstractmethod` — `reset` is required by `register_kernel` but not by the ABC, so a subclass missing `reset` fails at registration, not at instantiation.

**Kernel identity is a boot-time-only invariant.** `_ensure_native_kernel` runs exactly once before the WebSocket loop begins, and the docstring (`data/hyperliquid_feed.py:100-107`) states why: swapping mid-flight would put two different OFI number streams into one pipeline within seconds. `TRADEBOT_KERNEL=cpp` re-raises the exception instead of downgrading (`data/hyperliquid_feed.py:117-122`).

`CppMicrostructureKernel.__init__` (`core/microstructure.py:276-279`) imports the real module unconditionally even when `native_module` is passed, so constructing the adapter without a compiled `.pyd` raises `ImportError` rather than using the injected module.

### 6.8 Every Python caller of every native symbol

| # | Caller | Symbol used | What it does with the result |
|---|---|---|---|
| 1 | `data/hyperliquid_feed.py:458` | `microstructure.ingest_l2` (→ `ingest_l2`) | **The only write path.** Every l2Book WS frame. Book source is `_parse_book` (`:313-350`), which already coerced to `float` and dropped `sz <= 0`. |
| 2 | `analysis/probability_engine.py:89-90` | `ingest_l2` + `order_flow_imbalance(symbol, depth=5)` | Result becomes `z_ob = clip(ofi * 2.5, -2.5, 2.5)` in the 5-factor Bayesian composite; `orderbook` weight `0.15` (`analysis/probability_engine.py:163, 223-224`). |
| 3 | `analysis/probability_engine.py:84-87` | **bypasses native entirely** | When called without a symbol, instantiates a throwaway `PythonKernel()` keyed `"__ad_hoc__"`. The composite path at `:223` calls it with one argument, so the native speedup does not cover it. |
| 4 | `agents/direction_agents.py:125` | `calculate_order_flow_imbalance(book, symbol=symbol)` → `ingest_l2` + `order_flow_imbalance` | `ofi == 0.0 and rel_spread == 0.0` → return `None` (**abstain**, not NEUTRAL). `abs(ofi) < 1e-6` → NEUTRAL conf 0.0. Else `confidence = abs(float(ofi))` — **unbounded**, later clipped by `MAX_AGENT_Z = 2.5` in the ensemble. |
| 5 | `dashboard/callbacks/update_callbacks.py:1311` | `calculate_order_flow_imbalance(ob)` — uses `rel_spread` **only**, ignores `ofi` | Displayed slippage = `(rel_spread / 2.0) * 100.0`, half the relative spread, citing Cartea & Jaimungal 2015. `rel_spread == 0` → `"N/A"`, never `0.000%`. |
| 6 | `tests/test_cpp_kernel.py` | all of the above | Parity suite (`:74`), JSON parser tests (`:177-233`), weight test (`:167`) |
| 7 | `tests/test_lifecycle_paths.py:401-404` | `MicrostructureKernel` | Second parity suite on unequal level counts and zero-size books |

`ingest_json` has **zero production callers**. A repo-wide grep for `ingest_json` in `*.py` returns 2 hits in the adapter (`core/microstructure.py:296, 303`) and 6 in tests. The live WS path uses the Python `ingest_l2` route, so the zero-copy parser that exists to be the fast path is dead code in production.

`depth_imbalance` has no first-party caller at all. The facade exists (`core/microstructure.py:230-232`) but nothing outside the kernel contract and tests calls it.

### 6.9 Price and quantity units

| Quantity | Unit | Where converted |
|---|---|---|
| Price | Quote currency per base unit (USDT). Symbol form `BASE/USDT:USDT` for perps; `coin_to_symbol` at `data/hyperliquid_feed.py:59`. | — |
| Quantity | Base-asset units (BTC, ETH, …), never lots | `trading/risk_manager.py:112, 370` |
| Notional | `quantity * entry_price` | `trading/risk_manager.py:115, 368` |
| Margin | `notional / leverage` | `trading/risk_manager.py:116, 368`; `trading/position_manager.py:68` |
| Funding rate | Fraction per hour. `0.0001` = 0.01 %/hr = `z 1` in `MicrostructureAgent` | `agents/direction_agents.py:290` |
| Fee rate | Fraction of notional. `taker 0.00045` = 0.045 %, `maker 0.00015` = 0.015 % (Hyperliquid base tier) | `config.yaml:59-60` |
| `*_pct` config keys | **Fractions, not percents.** `tight_sl_pct: 0.0025` is 0.25 % | `config.yaml:140` |
| `*_pct` report keys | True percents. `pnl_pct` in `calculate_pnl` multiplies by 100 | `trading/risk_manager.py:265-270` |
| `atr_pct`, `realized_vol` | Fraction of price | `analysis/volatility.py:141-143, 70-83` |
| `depth` | Number of book levels, not currency | `core/microstructure.py:71` |

The mixed `*_pct` convention is a genuine trap: `scalping.tight_sl_pct = 0.0025` and `FundamentalAnalyzer`'s returned `pnl_pct` (a percent) appear in the same codebase with different scales.

### 6.10 float vs Decimal

`Decimal` is used in exactly two places, both for money-adjacent quantisation. Everywhere else is IEEE-754 float64.

| Constant | Value | Purpose | Source |
|---|---|---|---|
| `PRICE_QUANT` | `Decimal("0.00000001")` | 8 dp — SL, TP, liquidation price | `trading/risk_manager.py:16` |
| `MONEY_QUANT` | `Decimal("0.01")` | 2 dp — margin, position value, risk amount | `trading/risk_manager.py:17` |
| `QTY_QUANT` | `Decimal("0.00001")` | 5 dp — quantity | `trading/risk_manager.py:18` |
| fee quantum | `Decimal("0.0000000001")` | 10 dp — fee only | `trading/risk_manager.py:239` |

`trading/live/client.py:334-337` uses `Decimal` for exchange lot-size quantisation with `to_integral_value(rounding=ROUND_DOWN)`, because `0.5 / 1e-5` in binary float is `49999.99999999999` and `floor` would chop a full step.

`trading/position_manager.py:68` uses `Decimal(str(quantity)) * Decimal(str(entry_price)) / Decimal(str(leverage))` for the margin.

`backfill_realized_pnl.py` (root) and `_econ*.py` are dev tools that read the DB with plain `sqlite3`; not on the runtime path.

**Rounding direction — the single most load-bearing convention in the money layer.** All sizing, SL, TP, liquidation price and fees use `ROUND_DOWN`. The stated reason appears three times: `Decimal.quantize` defaults to `ROUND_HALF_EVEN`, and on a 0.25 % scalp a one-tick round-half-even move in the wrong direction is not neutral — it eats into the exact room the scalp was chosen for (`trading/risk_manager.py:163-169, 186-190, 220-240`).

**One exception, and it is a bug surface:** `calculate_pnl` calls `.quantize(Decimal("0.01"))` with **no `rounding=` argument** (`trading/risk_manager.py:268-270`), so it silently uses `ROUND_HALF_EVEN`. `Decimal('2.675').quantize(Decimal('0.01'))` → `2.68`; with `ROUND_DOWN` it would be `2.67`. Every PnL figure in the DB goes through this one call.

### 6.11 NaN / inf handling

The codebase uses a **three-way separation** between "no data", "zero", and "not derivable", and the choice is deliberate at every site.

| Layer | Convention | Evidence |
|---|---|---|
| Kernel, unknown symbol | `(0.0, 0.0)` / `0.0`, never raise | `core/microstructure.py:134-135, 162-163`; `cpp/microstructure_kernel.cpp:213, 296-299` |
| Kernel, NaN price | **Not guarded.** `ingest_l2('N', [[nan,1.0]], [[101.0,1.0]])` → `(0.0, nan)`. `inf` size → `(nan, 0.00995...)`. | measured; no `isfinite` anywhere in `cpp/` |
| Volatility | `None` for insufficient data, `0.0` never returned | `analysis/volatility.py:41-67, 51-54` |
| Volatility, zero realised vol | `None` (`return vol if vol > 0 else None`) | `analysis/volatility.py:67` |
| `calculate_realized_volatility` | floor `max(vol, 0.0002)`; default `0.0015` for < 5 rows | `analysis/probability_engine.py:143-152` |
| `compute_directional_curve` | `sigma = max(realized_vol_per_min, 0.0003)` | `analysis/probability_engine.py:292` |
| `_realized_vol` (ensemble) | floor `max(std, 0.0002)`, needs `min_returns_for_vol + 1` closes | `agents/direction_agents.py:525, 513` |
| `_safe_float` | NaN and unconvertible → `None` | `agents/direction_agents.py:320-328` |
| Backtester metrics | uncomputable → `None`, never `0.0` | `analysis/backtester.py:170-235` |
| `probability_factor` | `None` when `gross_loss == 0` (not `inf`) | `analysis/backtester.py:931-937` |
| `calculate_technical_zscore` | every factor guarded with `math.isnan` | `analysis/probability_engine.py:105, 113-114, 121, 129` |
| Live config rule editor | `NaN`/`inf` explicitly rejected, because every comparison against NaN is False so the limit would never fire | `trading/live/console.py:523-524`; `tests/test_numerics.py:149-166` |
| DB trade stats | `profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")` | `database/repository.py:605` |
| Position manager | converts `inf` back to `0` before writing account stats | `trading/position_manager.py:265-268` |

The one place the guard is missing on the money path is the C++ kernel: NaN/inf input propagates into a tuple the agents treat as valid data. `OrderFlowAgent` would then compute `confidence = abs(nan)`, and `analysis/direction_agents.py:328` (`_safe_float`) is never on that path.

The live-path kill of `float("inf")` is done by rewriting it to `0` rather than `None` (`trading/position_manager.py:265-268`), which is a *different* claim from the backtester's `None`. Two modules, two conventions, for the same un-derivable quantity.

### 6.12 Rounding on output, and where display numbers are cut

Reporting rounds at several different precisions, chosen per field rather than centrally.

| Site | Rounding | Source |
|---|---|---|
| `prob_bullish` / `prob_bearish` / `winner_odds` / `loser_odds` | `round(x, 4)` | `analysis/probability_engine.py:253-256` |
| `z_composite` | `round(x, 4)` | `analysis/probability_engine.py:258` |
| `factor_zscores` | `round(x, 3)` | `analysis/probability_engine.py:260-264` |
| `order_flow_imbalance` | `round(x, 3)` | `analysis/probability_engine.py:266` |
| `relative_spread` | `round(x, 5)` | `analysis/probability_engine.py:267` |
| curve points | `round(p_long, 4)`, `time_horizons` `round(t, 2)` | `analysis/probability_engine.py:317-321` |
| `agent_breakdown` confidence / z | `round(x, 4)` | `analysis/direction_ensemble.py:113-114` |
| technical signal values | `round(x, 2)` for RSI, `round(x, 3)` for ROC | `analysis/technical.py:126-258` |
| ML probabilities / confidence | `round(x, 4)` | `analysis/ml_signals.py:175, 250-254` |
| combined confidence / weighted score | `round(x, 3)` / `round(x, 4)` | `agents/analysis_agent.py:311-312` |
| `calculate_pnl` | `quantize(Decimal("0.01"))`, `ROUND_HALF_EVEN` implied | `trading/risk_manager.py:268-270` |
| `calculate_max_drawdown`, `calculate_sharpe_ratio`, `kelly_criterion` | `round(x, 4)` | `trading/risk_manager.py:395, 412, 429` |

Note the display-then-reuse hazard: `probability_engine` rounds `order_flow_imbalance` to 3 decimals **in the returned dict**, so any consumer reading the dict rather than the raw tuple loses precision. The ensemble path does not go through that dict — it pools `confidence = abs(ofi)` at full float64 (`agents/direction_agents.py:135`).

### 6.13 Precision-sensitive spots

These are the places where a well-meaning "cleanup" changes money.

#### 1. Sums must run level 0 → n-1 in both implementations

`core/microstructure.py:126-127` and `cpp/microstructure_kernel.cpp:194-196` both accumulate ascending. Reversing either changes the last digit and breaks parity at a scale that looks like noise. `CMakeLists.txt:14-18` and `tests/test_cpp_kernel.py:324-341` exist to keep `-ffast-math` out.

#### 2. `atr_1m_pct` requests `period + 1` candles, and that is load-bearing

N candles yield only N-1 true ranges (each TR needs the previous close). Asking for exactly `period` makes `len(trs) = period - 1 < period` always true, so ATR is always `None` and every dynamic target silently disables in production while unit tests that inject `candles=` directly stay green (`analysis/volatility.py:176-183`).

#### 3. `atr_1m_from_candles` mirrors pandas-ta's Wilder smoothing exactly

First ATR = mean of the first `period` TRs, then `atr = (atr * (period - 1) + tr) / period` (`analysis/volatility.py:132-135`). Using a simple moving average produces a different number from the one on the chart. The function also re-sorts candles ascending by `timestamp` itself (`:112`) because callers pass SQL `DESC`.

#### 4. `_ATR_CACHE` is time-bucketed, not a TTL, and is cleared by source swap

Key is `(symbol, int(time.time() // 5))` with `_ATR_CACHE_BUCKET_SECONDS = 5`, `_ATR_CACHE_MAX_ENTRIES = 256` (`analysis/volatility.py:152-159`). The whole dict is `.clear()`ed at 256 entries, and `set_candle_source` **unconditionally clears it** (`:238-240`) because a retained value would be ATR computed from the previous data source — a number that looks reasonable but is from the wrong data. `atr_1m_pct` also checks the cache *before* considering explicitly-passed `candles` (`:188-191`), which is cheap in production and a trap in tests.

#### 5. Dynamic TP/SL uses `max()` for TP and clamps for SL — deliberately

```
raw_sl = atr_mult * atr
sl_pct = min(max(raw_sl, min_sl), max_sl)      # clamp
tp_pct = max(sl_pct * min_rr, min_profit)      # R:R lock
```

`analysis/volatility.py:330-332`. Volatility may **widen** the stop but must never narrow it below the agreed static floor — that floor is what keeps the tick-quality guard's deviation threshold meaningful. `trading/paper_engine.py:302-315` applies the same `max()` a second time against `tight_sl_pct` / `fast_tp_pct`, plus `tp_pct = max(tp_pct, sl_pct * min_rr)` at `:317`.

#### 6. Fee-aware economics are re-derived in three places

| Site | Formula |
|---|---|
| `core/config.py:602, 734` | `roundtrip = fees.taker * 2`; `net_tp = min_profit_pct - roundtrip`; `net_sl = tight_sl_pct + roundtrip`; `breakeven_wr = net_sl / (net_tp + net_sl)` |
| `analysis/volatility.py:384-404` | same, evaluated at runtime against live `sl_pct` / `tp_pct`, plus `breakeven_wr > max_breakeven_win_rate (0.65)` |
| `agents/decision_agent.py` + `core/config.py:743-763` | `breakeven_offset_pct (0.0015) >= roundtrip (0.0009)`; `reversal_close_threshold` in `[min_confidence, 1.0)` |

At the shipped config: `roundtrip = 0.0009`, `net_tp = 0.0060 − 0.0009 = 0.0051`, `net_sl = 0.0025 + 0.0009 = 0.0034`, `breakeven_wr = 0.0034 / 0.0085 = 0.400`. The invariant `breakeven_trigger_pct (0.0020) < min_profit_pct (0.0060)` is what keeps `_protect_breakeven` alive; equality would make `_scalp_take_profit` fire first in the same cycle and the whole breakeven path dead. Rejected at boot by `_validate_scalping_economics` (`core/config.py:727-732`), locked by `tests/test_config.py:33`.

#### 7. The tick guard's threshold IS the stop distance

`deviation > sl_pct` where `sl_pct` is the same value used to place the stop (`trading/paper_engine.py:262-265`). At a 0.25 % scalp stop, a fill at a local peak means the stop already sits inside the spread. The guard is a **median over ≥ 5 samples in a 3.0 s window**, and it **fails open** below 5 samples (`:250-255`) because refusing on missing history would silence the bot exactly when the feed first connects.

#### 8. `scalp_position_size` spreads risk by `max_open_positions`, and ignores stop distance

`margin = risk_pct * balance`, then `margin = min(margin, balance * 0.9 / max_open_positions)` (`trading/risk_manager.py:361-365`). With `max_open_positions = 30` that caps each position at 3 % of free cash regardless of its stop. `0.9` is a bare literal here and at `trading/risk_manager.py:119, 299` — three copies, no config key.

#### 9. The daily-loss denominator is frozen `initial_balance`, not live cash

`reference = self._initial_balance if self._initial_balance > 0 else balance` (`trading/risk_manager.py:319`). Using live `balance` means the threshold drops exactly when margin locks, i.e. the breaker relaxes at the worst moment. Note `agents/execution_agent.py:40` constructs a second bare `RiskManager()` with `initial_balance = 0.0`, which takes the `balance` fallback — the exact failure the comment at `:306-318` warns about.

#### 10. Drawdown marks on equity but the peak is bumped on cash + margin

`equity_now = free_balance + open_margin + open_upnl` (`trading/paper_engine.py:549`) versus `equity_now = new_balance + remaining_margin` (`trading/position_manager.py:255-256`). Numerator and denominator of `(peak − mark) / peak` measure different quantities.

#### 11. Fee quantisation is 10 dp, not cents

An earlier version quantised fees to `0.01`, which made a `$0.10` notional pay `$0.00` — a small-size scalping bot looked free and therefore profitable. Fixed to `Decimal("0.0000000001")` with `ROUND_DOWN` (`trading/risk_manager.py:220-240`).

#### 12. PnL is net of **both** fees, and the open fee is read back from the DB

`net_pnl = pnl_info["pnl"] - open_fee - fee` (`trading/position_manager.py:203`), where `open_fee` is read from the `trades` table rather than recomputed, because the taker rate can change mid-run (`:196-201`). The balance credit then adds the open fee back: `returned = pos["margin"] + net_pnl + open_fee` (`:243`). The historical bug this fixed is recorded at `:240-242` — 891 USDT across 330 paper positions.

#### 13. ML feature order and the train/serve skew

The feature vector is 7 wide and order-significant (`ml/trainer.py:112-115` vs `analysis/ml_signals.py:37-40, 130-133`). The shipped artifact `ml/models/signal_model.pkl` (3,903,681 bytes) was verified by loading:
`RandomForestClassifier(n_estimators=100, max_depth=10, random_state=42)`, `feature_importances_ = [0.1538, 0.1798, 0.1442, 0.1721, 0.0901, 0.0, 0.2600]`. Index 5 (`sentiment_score`) is **exactly 0.0** — the trainer hardcoded `0.0` into that column because OHLCV has no sentiment, so the model is effectively 6-feature while receiving a 7-vector. Independently, three slots are computed differently on each side (`macd_hist_norm`, `ema_trend`, `bb_position`, `volume_ratio`).

#### 14. Ensemble z pooling, not probability averaging

`MAX_AGENT_Z = 2.5` (`analysis/direction_ensemble.py:32`); each verdict normalises to `z = ±2.5 × clip(confidence, 0, 1)` (`:47-60`); weight is `base_weight(agent) × (1 + 0.5 × agreement_score)`; pooled `z = Σ(w·z)/Σw`; then `z_shrunk = z × 0.85`; one sigmoid; clamp to `[0.02, 0.98]`. Shipped weights (`config.yaml:202-214`): orderflow 0.30, momentum 0.25, technical 0.25, microstructure 0.20.

`agreement_score` (`:63-80`) excludes peers whose z is exactly 0 from both numerator and denominator, so an all-neutral panel scores `0.0` for everyone rather than `0.5`. It reads `verdict["_z"]`, so calling it on a raw verdict that has not been through `aggregate()` raises `KeyError`.

#### 15. Realized vol feeds a diffusion exponent

`_realized_vol` floors at `0.0002` (`agents/direction_agents.py:525`) because sigma enters `d2 = ((drift − 0.5σ²)·t) / (σ√t)` in `compute_directional_curve` (`analysis/probability_engine.py:307`); an under-estimated sigma distorts the whole curve. `drift_per_min = z_score * 0.45 * sigma` (`:299`) — the `0.45` has no derivation in the file or config and is the only thing setting terminal-curve steepness.

#### 16. `technical.generate_signals` confidence scales with indicator count

`max_score = max(total_signals * 1.5, 1)` (`analysis/technical.py:274`), so a sparser DataFrame yields a **higher** confidence for the same net score. `calculate_indicators` returns the input DataFrame unchanged if `len(df) < macd_slow + macd_signal` (= 35) (`:51-53`).

#### 17. `SQLite` timestamps are naive UTC; the parser force-attaches UTC

`parse_db_timestamp` (`core/utils.py:9-31`) returns `0.0` — never raises — for falsy input, unparseable strings, or `ts <= 0`. `DecisionAgent._snapshot_is_fresh` treats `ts <= 0` as **stale**, not as "no data" (`agents/decision_agent.py:340-346`), so a malformed row can never produce an entry. This is the specific WIB/UTC+7 seven-hour-offset bug the function exists to prevent.

#### 18. `repository.get_candles` returns newest-first

`ORDER BY timestamp DESC` (`database/repository.py:92-99`); every consumer that needs chronological order reverses in Python. `_load_closes` does `reversed(cur.fetchall())` (`agents/direction_agents.py:341-364`). The same DESC-then-reverse pattern is applied three times in the dashboard (`update_callbacks.py:179-183, 1011-1015, 1226-1230`).

---

## 7. Tests and contracts

Suite under `C:/Users/maman/Desktop/Project/trading-bot/tests/`. **26 Python files: 24 `test_*.py` plus `t3.py` (not a test) and `__init__.py`.**

**Re-measured during integration:**

```
$ python -m unittest discover tests
Ran 566 tests in 8.284s
OK (skipped=4)
$ echo $?
0
```

The **566 collected cases** figure is authoritative over the **570 `def test_` regex count** — the difference is 4 shadowed definitions (§7.5.5). The 4 skips are all the same skip: `TestKeyParsing` in `tests/test_live_tui.py:359`, `skipTest("jalur POSIX tidak berlaku di Windows")`. The C++ parity suites **did run**, because `cpp_microstructure.cp314-win_amd64.pyd` is present at the repo root and the interpreter is CPython 3.14.

Test bodies carry Indonesian prose explaining *which real bug each test exists for*. Several name the money lost: `891 USDT` from double-charged opening fees across 330 paper positions (`tests/test_lifecycle_paths.py:210-217`), `49.30 USDT` from concurrent read-modify-write balance races (`tests/test_position_manager.py:154`), and 0-6 second positions all ending `SL_HIT` from stops priced off P1 while fills happened at P2 (`tests/test_fill_price_sl.py:5-10`).

### 7.1 Inventory — size vs. coverage

`line count` is the raw file line count; `Cases` is what `unittest.TestLoader().discover('tests')` collected. **Line counts re-measured during integration.**

| File | Lines | Cases | Primary contract defended | Exercises real code? |
|---|---:|---:|---|---|
| `test_bugfixes.py` | 1157 | 62 | B1-B9 regression set + microstructure + config fields | Mixed — 17 of 62 assert on source text |
| `test_advanced_modules.py` | 881 | 59 | Wilder ATR, dynamic TP/SL, volatility gate, L2 backtester | Yes (real `analysis.volatility`, real `backtester`) |
| `test_live_tui.py` | 822 | 65 | Menu/TextField rotation, keymap, screen redraw, `ask_mode` flow | Real rotation + real `render`; `KeyReader` stubbed |
| `test_lifecycle_paths.py` | 804 | 51 | Fee attribution, double-claim race, batch close, prune, risk-gate branches | Yes (real SQLite + real `PositionManager`) |
| `test_live_engine.py` | 644 | 36 | Order sequencing, trigger direction, quantization, health, emergency flat | Real `LiveEngine` + `FakeExchange` |
| `test_neural_net_layout.py` | 560 | 26 | Constellation slot stability, edge trace shape, label geometry | Real `create_neural_net_fig` |
| `test_live_safety.py` | 546 | 45 | `SafetyGate` must **refuse**; counter persistence; window; cloid; leverage | Real `SafetyGate`, env injected, no network |
| `test_layout_contract.py` | 414 | 18 | Mounted component ids, CSS token cycles, Dash 4 dropdown classes | Runtime layout build + CSS parse |
| `test_cpp_kernel.py` | 395 | 22 | C++/Python kernel parity + C++ source integrity | Real native `.pyd` when importable |
| `test_direction_agents.py` | 267 | 20 | Abstain-vs-NEUTRAL doctrine for 4 specialists | Real agents against real `market_store` |
| `test_probability_engine.py` | 248 | 16 | Norm CDF, OFI sign, composite sums to 1, diffusion monotonicity | Real engine |
| `test_direction_ensemble.py` | 236 | 14 | `prob_long + prob_short == 1` to 12dp; mirrored input → mirrored output | Real `aggregate` |
| `test_live_console.py` | 234 | 27 | `decide_mode` cannot reach live by accident | Real pure functions, no I/O |
| `test_live_executor.py` | 226 | 21 | Adapter never bypasses the gate; never sends unprotected | Real `LiveExecutor`, stub engine |
| `test_decision_agent_ensemble.py` | 223 | 10 | Snapshot freshness + confidence gate on real private method | Real `DecisionAgent`, real SQLite |
| `test_layout_budget.py` | 222 | 10 | Pixel arithmetic: zone cells must contain their graphs | CSS token parse + real `create_symbol_pnl_panel` |
| `test_position_manager.py` | 217 | 6 | SL/TP/liq transitions + two concurrency invariants | Real, real SQLite |
| `test_dashboard_palette.py` | 180 | 11 | No `var(--x)` reaches Plotly; palette is CSS-derived | Real `palette` + real figure builders |
| `test_risk_manager.py` | 171 | 11 | Fixed-fractional sizing, liquidation both sides, fee rates from config | Real `RiskManager`, pure |
| `test_numerics.py` | 170 | 19 | Fee never negative, PnL symmetry, ensemble clamping, ATR sufficiency | Real `RiskManager`, real `aggregate` |
| `test_paper_engine.py` | 161 | 4 | Open/close flow; PnL net of both fees; HOLD is a no-op | Real engine |
| `test_fill_price_sl.py` | 161 | 4 | SL/TP derived from **fill** price, agent stops discarded | Real `PaperTradingEngine` |
| `test_config.py` | 106 | 6 | Scalping economics after roundtrip fee | Real validators |
| `test_indicators.py` | 85 | 3 | Indicator columns exist and last row is not NaN | Real `TechnicalAnalyzer` |
| `tests/t3.py` | 24 | 0 | **Not a test** — live WebSocket probe, see §7.6 | n/a |
| `tests/__init__.py` | 1 | 0 | Package marker, docstring only | n/a |

#### Coverage shape

```
live-safety surface (trading/live/*)  194 cases   ########################
paper trading state machine           61 cases   ####
microstructure / C++ parity           32 cases   ###
config validators                     22 cases   ##
backtester                            29 cases   ###
dashboard layout + CSS                65 cases   #####
direction ensemble + specialists       34 cases   ###
probability engine                     16 cases   #
analysis technical indicators           3 cases   #
data/ ingestion (4 modules)            0 cases
dashboard/callbacks/update_callbacks   0 cases
agents/{base,news,analysis}_agent      0 cases
ml/ (trainer, predictor, ml_signals)   0 cases
run.py                                 0 cases
core/{logger,event_bus,scheduler,
      market_store,utils}              0 cases of their own logic
```

Verified gap by AST-import scan of every `tests/*.py` against every first-party module: **20 of 62 production modules have no test import at all**, and among those are `data.price_feed`, `data.hyperliquid_feed`, `data.news_fetcher`, `data.macro_fetcher`, `data.sentiment`, `dashboard.app`, `dashboard.callbacks.update_callbacks`, `agents.analysis_agent`, `agents.news_agent`, `agents.base_agent`, `ml.trainer`, `ml.predictor`, `run`.

**Nothing runs the tests automatically.** There is no `.github/`, no `pytest.ini`, `tox.ini`, `setup.cfg`, `pyproject.toml`, `conftest.py`, `Makefile`, or `.pre-commit-config.yaml` in the repo root. `python -m unittest discover tests` is a manual act. Each file except `test_cpp_kernel.py` carries a `unittest.main()` `__main__` block, so `python tests/test_x.py` works per file.

### 7.2 What each test file defends

Grouped by contract, not by file, because several files defend the same one.

#### Contract A — Fee attribution is net of BOTH legs, charged exactly once

The single most valuable contract in the suite, and the one with the most unusual history. `realized_pnl` is deliberately net of two fees while the balance credit adds the opening fee back, so the opening fee is charged once to the balance and once to the trade-quality metric.

| Assertion | Site | Guards |
|---|---|---|
| `realized_pnl == -(open_fee + close_fee)` on a flat trade | `tests/test_lifecycle_paths.py:271-280` | The stored PnL is genuinely net |
| `balance_final - balance_at_open == margin + realized + open_fee` | `tests/test_lifecycle_paths.py:252-256` | The **double-charge** bug (891 USDT) |
| Flat trade loses exactly `-9.0` (an absolute literal) | `tests/test_lifecycle_paths.py:267-269` | An absolute number, not a derived one — see §7.5.4 |
| `asyncio.gather` two closes → exactly one dict returned | `tests/test_lifecycle_paths.py:167-176` | Claim-once at SQL level |
| Six concurrent closers → one winner, exactly one `CLOSE` trade row | `tests/test_position_manager.py:201-213` | Same claim, wider race |
| `get_daily_realized_pnl() == realized_pnl` | `tests/test_lifecycle_paths.py:133-139` (in the **shadowed** class) | Breaker reads the same number |
| `details["realized_pnl"] == 200.0 - fee_open - fee_close` | `tests/test_paper_engine.py:134-136` | Engine return value matches DB |
| `balance == 10000.0 + expected_pnl` after close | `tests/test_paper_engine.py:145-146` | Margin returns whole, PnL already net |

The `test_paper_engine.py:120-130` comment documents the trap: the *previous* expectation was `196.9` (= 200 − 3.1, close fee only), which **encoded the bug**. Rewriting the assertion to the buggy value would have kept the suite green.

#### Contract B — Nothing reaches the exchange that should not

`tests/test_live_safety.py` is written as adversarial: its module docstring says the focus is "code that **refuses**", because for real money the worst failure is a gate that does not block.

| Sub-contract | Cases | Site |
|---|---:|---|
| Default config refuses everything; `LiveConfig.enabled=True` does **not** bypass env | 5 | `test_live_safety.py:52-94` |
| Kill switch: env, internal latch, consecutive-error streak, reset on success | 4 | `:97-121` |
| Live window half-open `[start, end)`, incl. overnight | 5 | `:124-166` |
| Notional limits accumulate against existing exposure | 5 | `:176-234` |
| Invalid input (0/negative size, 0/negative price) never sends | 4 | `:237-267` |
| Daily counters + rollover keeps the error streak | 5 | `:270-327` |
| Corrupt counter file blocks orders; missing file is a new day | 6 | `:330-431` |
| Key redaction, incl. short keys left alone | 3 | `:434-459` |
| cloid required for opens, not for closes | 4 | `:462-511` |
| Leverage bounds | 4 | `:514-543` |

Two of these deserve the reader's attention because the *reason* is not visible in the code:

- `test_corrupt_file_blocks_orders` (`:400-416`) — a present-but-unparseable counter file sets `_unreadable`, and every order is refused. Missing file is fail-open (fresh day); corrupt file is fail-closed. The assertion is on `Blocker.COUNTER_STATE_UNREADABLE`, not on a boolean.
- `test_wrong_truthy_value_does_not_enable` (`:87-94`) — `"on"`, `"y"`, `"2"`, `"yes please"` are all still *not* live. Only the exact allowed set counts.

#### Contract C — The live order sequence is not reorderable

`tests/test_live_engine.py` runs the real `LiveEngine` against a `FakeExchange` that records call order in `self.calls` (`:44-97`).

| Invariant | Case | Site |
|---|---|---|
| `set_leverage` precedes `place_limit_order` | `test_leverage_set_before_order` | `:135-155` |
| cloid passes through unchanged | `test_cloid_passed_through` | `:157-168` |
| Unfilled order attaches **no** trigger | `test_no_protection_attached_when_unfilled` | `:170-186` |
| Blocked gate → **zero** exchange calls | `test_gate_blocks_before_any_exchange_call` | `:188-199` |
| Missing SL or TP → zero exchange calls | `test_missing_protection_blocks_order` | `:201-213` |
| LONG → SELL triggers; SHORT → BUY triggers; both `sl`+`tp` installed | 3 | `:234-258` |
| `emergency_flat` cancels **before** closing | `test_cancel_happens_before_close` | `:303-319` |
| `flattened: False` when a close fails, and kill switch engages | `test_flattened_false_when_close_fails` | `:321-342` |
| No market price → no close order, `flattened` stays False | `test_no_price_means_no_close` | `:344-352` |
| Unreachable exchange → `ok: False`, `reachable: False`, kill switch on | `test_unreachable_exchange_blocks` | `:585-602` |
| Unknown remote position, missing local position, resting order w/o position, kill switch | 4 | `:604-645` |
| Late GTC fill gets both triggers; already-protected skipped; SHORT SL above entry | 5 | `:464-545` |

`test_emergency_flat`'s docstring (`:266-268`) states the old defect plainly: a previous version only cleared local state and returned `flattened: True` — "the database is clean while the exchange still holds the user's exposure".

#### Contract D — The menu cannot reach live by accident

Two files. `test_live_console.py` tests the **pure** `decide_mode` (no terminal, no env mutation). `test_live_tui.py` tests rotation separately from rendering, which is why it can test a TUI at all.

| Invariant | Site |
|---|---|
| Any unknown/malformed choice (`""`, `"9"`, `"x"`, `"0"`, `"-1"`, `"paper"`, `"3.0"`) → paper | `test_live_console.py:33-40` |
| Phrase is case-sensitive, whitespace-padded, and exact; 7 near-miss phrases all refused | `:50-72` |
| Typed address must match the displayed one, case-insensitively; empty/None on either side refuses | `:74-101` |
| All three conditions together, and each one missing alone | `:103-116` |
| `validate_rule` rejects NaN/inf/`1e400` — every comparison against NaN is False, so a NaN limit looks present and never fires | `:160-171` |
| `check_consistency` warns (does not block) on the three impossible combinations | `:197-233` |
| Menu wraps both directions, skips disabled, number shortcuts, Esc/q/Q/Ctrl-C cancel | `test_live_tui.py:44-125` |
| TextField: empty refused, backspace over-run safe, `max_length`, validator blocks Enter, error clears on retype, secret masks | `:128-216` |
| `display_width` strips ANSI, counts CJK as 2 | `:222-228` |
| Redraw uses HOME + erase, never full `2J`; cursor always restored; `stop()` idempotent | `:257-337` |
| `ask_mode` full flow: wrong phrase, wrong address, Esc mid-confirmation, number-shortcut to live | `:474-590` |
| **Unpatched smoke** — 16 cases that call the real `run_menu`/`run_text`/`ask_mode`/`edit_rules`, mocking only `KeyReader` | `:593-816` |

`TestUnpatchedSmoke` exists for a documented reason (`:594-606`): patching `interactive()` hid a bug where `enable_vt_processing` was called under a name that does not exist, so the process died on that line only when a human ran the bot. `test_interactive_does_not_raise` (`:648-660`) is the case that catches it.

#### Contract E — Ensemble arbitration is symmetric and abstain is not a vote

| Invariant | Site |
|---|---|
| `prob_long + prob_short == 1.0` to **12 decimal places** across 5 mixed combos | `test_direction_ensemble.py:29-47` |
| Mirrored input → mirrored output, confidences equal to 9dp | `:76-101` |
| `abstain` changes nothing (adding a blind agent does not damp the signal) | `:160-173` |
| Disabled agent excluded from pooling | `:175-199` |
| Shrinkage is monotone: delta 0.4 < 0.7 < 1.0 all pull toward 0.5 | `:103-122` |
| 3-against-1 beats 2-2 even when the minority is confident | `:123-145` |
| NEUTRAL at confidence 1.0 still yields z=0 | `:201-206` |
| Every specialist abstains on empty data, and `direction`+`abstained` always present | `test_direction_agents.py:41-72` |
| Flat-but-present price is NEUTRAL, **not** abstain | `:128-134` |
| Open interest is recorded but contributes **zero** to the vote | `:186-196` |
| `_signed_volume` skips unknown side, non-numeric size, zero size, and non-dict rows | `:198-211` |
| Positive funding is contrarian SHORT; negative is LONG | `:169-184` |
| Probabilities clamped to [0.02, 0.98] across all 27 direction×confidence triples | `test_numerics.py:120-131` |

#### Contract F — Direction snapshots gate entries, and staleness is a hard reject

`tests/test_decision_agent_ensemble.py` writes real rows into a real SQLite and calls the real private `DecisionAgent._generate_scalp_signals` — the single-source-of-truth design (DecisionAgent and the HUD read the same `direction_snapshots` row) is only meaningful if the read path is exercised.

| Case | Line |
|---|---:|
| Fresh LONG snapshot → one signal, `ensemble_direction == "LONG"`, `strength == 0.72` | 93 |
| Fresh SHORT → `BEARISH` | 103 |
| Snapshot older than `ensemble.max_snapshot_age_seconds` → **zero** signals | 112 |
| Snapshot 1 second old → accepted | 128 |
| Below `min_confidence` → zero signals | 138 |
| NEUTRAL → zero; missing row → zero; latest row wins | 151, 158, 164 |
| Reasoning contains `"3/4 agree"` from the agent breakdown | 204 |

Every threshold is read from `get_config()` at runtime (`:54`, `:119`, `:144`) rather than hardcoded, and the test file's stated reason at `tests/test_lifecycle_paths.py:676-679` is worth quoting as a suite-wide convention: "a test that invents its own threshold stays green when config changes, and that is the most expensive weakness for money protection."

#### Contract G — C++/Python kernel parity

Two independent guards, in `tests/test_cpp_kernel.py`, with `TOLERANCE = 1e-7` at `:37`.

**Numeric parity** (`TestCppPythonParity`, 13 cases, skipped entirely if `cpp_microstructure` is not importable): balanced / bid-heavy / ask-heavy books, a 20-level book swept at `depth in (1, 3, 5, 10, 20)`, zero volume, asymmetric depth, fractional prices, unknown symbol → `(0.0, 0.0)`, `reset`, and `level_weight(i)` matching `[1.0, 0.9, 0.8, 0.7, 0.6]` at `1e-12`.

`test_ingest_json_parity` (`:177-217`) has a fixture-shape comment that is itself a defect record (`:185-193`): the previous fixture had `[[[` where the parser needs `[[`, and because the class was skipped whenever the native module was absent, **the malformed payload was never seen**. The parser's `UnexpectedChar` rejection was correct all along.

A second parity class, `TestNativeKernelParity` in `tests/test_lifecycle_paths.py:385-469`, deliberately targets *hostile* books: unequal level counts, empty bid side, empty ask side, zero-sized levels, all-zero volume, 50 levels truncated at 32, `depth=0`, `depth > book`, a **crossed** book (best bid > best ask), and prices at `1e-8` and `1e9`.

**Source integrity** (`TestKernelSourceIntegrity`, 9 cases, never skipped) exists because the docstring says the parity failure message is too technical (`:236-248`). It asserts `kLevelWeight0 = 1.0` and `kLevelWeightStep = 0.1` appear in the C++ source, that `std::array` is present and `std::vector` is absent, that `py::gil_scoped_release` is bound, that `-O2` is present while `-Ofast`/`ffast-math` are absent, and that the feed calls `initialize_native_kernel` / `_ensure_native_kernel`. Two of these strip comments first (`re.sub(r"//.*", "", header)` at `:308`; `re.sub(r"#.*", "", cmake)` at `:338`) — the file's own comment (`:302-303`) says the CMakeLists deliberately **names** `-Ofast` to explain why it is not used, so a raw grep would flag the explanation as the violation. The suite explicitly calls that irony out (`:332-335`).

#### Contract H — Money gates refuse at the boundary

| Invariant | Site |
|---|---|
| Margin boundary is strictly `>`, so exactly 90 % passes | `test_lifecycle_paths.py:636-652` |
| Daily-loss denominator is `_initial_balance`, threshold read from config | `:669-703` |
| Drawdown measured on **equity**, not free cash | `:709-721` |
| `peak_balance = 0` does not divide by zero | `:723-726` |
| All rejection reasons accumulate (≥3 at once) | `:732-741` |
| Fee never negative for any sign combination, and symmetric in both operands | `test_numerics.py:28-46` |
| `leverage = 0` raises `ZeroDivisionError` rather than returning junk | `:53-55` |
| LONG/SHORT PnL sum to exactly 0 at `places=9` | `:66-71` |
| Liquidation price is on the correct side for each direction | `:73-77` |
| Config fee rates **are** the Hyperliquid base tier (0.00045/0.00015) | `test_risk_manager.py:119-131` |

`test_lifecycle_paths.py:669-679` and `test_numerics.py:80-94` test the *same* breaker from two angles with different mechanisms — the lifecycle version mutates `rm._initial_balance` directly, the numerics version uses the constructor. Duplication, not divergence, since both read the threshold from config.

#### Contract I — The tick-quality guard actually fires

`tests/test_bugfixes.py:42-192` exists because the old guard compared `price` against a `stop_loss` that was itself derived from `price`, so `price <= stop_loss` could only fire when `sl_pct <= 0` — it was provably dead (`:44-47`).

| Case | Line |
|---|---:|
| Stale tick (age 99 s) rejected, reason contains "basi" | 111 |
| Outlier tick (>1 % from the 5-tick median) rejected | 118 |
| Representative tick accepted — the guard must not block everything | 132 |
| **Insufficient samples fails OPEN** (2 samples, 65000 price → allowed) | 137 |
| Integration: `execute_order` with a stale tick is rejected and no position opens | 147 |
| The rejection path does not raise `NameError` | 173 |

The fail-open case is the load-bearing one and its docstring (`:138-143`) gives the reason: "refusing an order for lack of history will silence the bot precisely in the busiest seconds, namely when the feed first connects."

#### Contract J — Config validators are fatal and self-consistent

`tests/test_config.py` (6 cases) and `tests/test_bugfixes.py:967-1153` (16 cases) between them cover all three validators.

| Rejected configuration | Site |
|---|---|
| `breakeven_trigger_pct >= min_profit_pct` (would make breakeven protection dead code) | `test_config.py:33-47` |
| Net TP ≤ net SL after roundtrip fee | `:49-64` |
| `breakeven_offset_pct` below the roundtrip fee | `:66-79` |
| `reversal_close_threshold < min_confidence` or `>= 1.0` | `test_bugfixes.py:1005-1022` |
| `stale_tick_window_seconds <= max_tick_age_seconds` | `:1024-1037` |
| `stale_tick_min_samples < 2` | `:1039-1048` |
| `max_snapshot_age_seconds < 2 × interval_seconds` | `:1050-1064` |
| `min_sl_pct >= max_sl_pct`; `min_risk_reward <= 1.0`; `max_breakeven_win_rate` outside (0.5, 1.0) | `test_advanced_modules.py:806-853` |
| Disabled `scalping` or `dynamic_tp_sl` skips its validator | `test_config.py:81`, `test_advanced_modules.py:855` |
| Shipped `config.yaml` must declare the new keys | `test_bugfixes.py:1066-1084` |

`test_repaired_config_has_positive_expectancy` (`test_config.py:86-102`) is the only place the whole economic chain is asserted end to end: `roundtrip = taker*2`, `net_tp = min_profit_pct - roundtrip`, `net_sl = tight_sl_pct + roundtrip`, `breakeven_wr = net_sl/(net_tp+net_sl) < 0.50`.

#### Contract K — Volatility-adaptive targets

`tests/test_advanced_modules.py:129-256`, using candles engineered so the answer is provable: `_make_candles` (`:23-46`) makes every true range exactly `R`, so Wilder smoothing must return exactly `R`. Its docstring (`:27-33`) says the earlier claim that ATR comes out "slightly below average" is what made the test demand `< 1.0` and fail — the test was wrong, not the code.

- ATR exactly `1.0` on uniform candles, `places=9` (`:56-67`)
- ATR is order-independent — feeds forward and reversed and requires equality to 12 dp (`:87-100`), because `Repository.get_candles` returns DESC
- Missing/zero-price candles → `None`, not `0.0` (`:76-107`)
- SL scales **proportionally** with ATR: the test asserts the **ratio** is 2.0, not "4x", and explains why 4x is untestable (clamping) at `:169-203`
- SL clamped to `max_sl_pct` with the `clamped` flag set; never below `min_sl_pct` (`:205-222`)
- `tp_pct >= sl_pct * 1.5` across five volatility levels (`:224-241`)
- Gate blocks when TP ≤ roundtrip fee, blocks poor R:R (`breakeven = 0.667 > 0.65`), and — importantly — **passes** a moderate R:R, because "a gate that rejects everything is as pathological as a gate that rejects nothing" (`:309-320`)
- `VolatilityGateError` exists as a distinct exception type so the audit trail can tell "our ticket is bad" from "the market is untradeable" (`:327-349`)

#### Contract L — Backtester fill honesty

`tests/test_advanced_modules.py:360-786`. The `QueueFillModel` cases (8) exist because "if the queue model is wrong, every statistic above it becomes meaningless" (`:363-367`).

- No queue ahead → first trade fills fully; 10 units ahead → the first 4-unit trade fills **nothing** (`:380-391`)
- A single large trade clears the queue *and* fills the remainder (`:402-405`)
- Negative queue clamped to 0; negative trade size ignored (`:412-427`)
- Market order fills **worse** than mid, and fills no more than the book depth (`:466-494`)
- Net PnL = gross − fees exactly, no rounding help (`:511-535`)
- `replay` orders events by time — input is deliberately reversed `[snap2, snap1]` and the assertion is that the book ends at 100.09 (`:678-710`)
- `synthesize_walk` is deterministic for a fixed seed and differs across seeds (`:712-735`)
- End-to-end replay over 300 synthetic steps produces completed trades where `total == wins + losses` (`:753-785`)

`test_metrics_return_none_when_undefined` (`:665-676`) carries a comment correcting itself: `max_drawdown` returns a **tuple**, so `assertIsNone(max_drawdown([]))` "will always fail" — the assertion is `assertEqual(max_drawdown([]), (None, None))`.

### 7.3 Real code vs. reimplementation vs. stub

This is where a newcomer should be most careful. Roughly **three quarters** of the suite drives real production code; the rest is source-text, stubs, or — worst — duplicated logic.

| Category | Cases | Examples |
|---|---:|---|
| **Real production code, real persistence** | ~300 | All of `test_lifecycle_paths`, `test_position_manager`, `test_paper_engine`, `test_fill_price_sl`, `test_decision_agent_ensemble`. Real `Database`, real `Repository`, real `PaperTradingEngine`, real `PositionManager`. |
| **Real code, injected environment** | ~140 | `test_live_safety` (real `SafetyGate`, injected `env` + `state_path`), `test_live_console` (real pure functions), `test_live_tui` (real rotation, stubbed `KeyReader`), `test_direction_agents` (real agents on real `market_store`). |
| **Real code, fake boundary** | ~60 | `test_live_engine` (real `LiveEngine` + `FakeExchange` recording call order), `test_live_executor` (real `LiveExecutor` + `_Recorder`/`_Engine` stubs). `TestQuantization` bypasses `__init__` entirely via `LiveExchange.__new__` + injected `_Info` (`:364-376`). |
| **Real math, no I/O** | ~60 | `test_direction_ensemble`, `test_numerics`, `test_risk_manager`, `test_probability_engine`, `test_config`, `test_advanced_modules` (volatility + backtester halves). |
| **Real code, real CSS/HTML parse** | ~65 | `test_layout_contract` builds the actual Dash layout and walks it for ids (`:34-68`); `test_layout_budget` parses `:root` tokens with `re` and compares arithmetic. |
| **Source-text assertions** | **36** | See §7.4. |
| **Duplicated logic in the test** | **~4** | See §7.5. |
| **Live-network, not a test** | 0 | `tests/t3.py` — see §7.6. |

Notably, the suite **avoids** mocking in almost every case. `tests/test_bugfixes.py:620-634` is the one place a live method is replaced on a live object — `pm.close_position = flaky_close`, restored in a `finally` — and it is used deliberately to simulate the mid-loop close race that cannot be produced any other way (the docstring at `:609-614` explains that closing a position manually *first* would not trigger it, because the row would already be gone from `get_open_positions`).

Two global-state mutations leak between tests and are **not** restored:

- `db_module._db = self.db` in every async `setUp` (e.g. `test_lifecycle_paths.py:46`, `test_paper_engine.py:33`, `test_bugfixes.py:67`). There is no matching restore in any `tearDown`; `close_db()` nulls the module global, so a test running after these inherits whatever the next `setUp` sets.
- `core.config` singleton: `test_direction_agents.py:263` restores `get_config().ensemble.microstructure.enabled = True` in a `finally`; `test_live_tui.py:453,467` pops `HYPERLIQUID_PRIVATE_KEY` in `setUp`/`tearDown`; `test_bugfixes.py:936` restores `ms._KERNEL = original` by reaching into a private module global.

Test databases are real files under `data_store/`: `test_lifecycle.db`, `test_pos_mgr.db`, `test_trading_engine.db`, `test_fill_sl.db`, `test_decision_ensemble.db`, `test_bugfix_tickguard.db`, `test_bugfix_dailyloss.db`, `test_bugfix_marginfee.db`, `test_bugfix_partialclose.db`. Each is deleted (plus `-wal`/`-shm`) in `setUp` and again in `tearDown`. `tests/data_store/test_nonexistent.db` is referenced as a deliberately-absent DB path by `test_direction_agents.py:53,67` to force `TechnicalAgent` to abstain.

### 7.4 FALSE CONFIDENCE — the source-text list

**36 of the 570 test methods found by AST (6.3 %) assert on the text of a file rather than on behaviour.** They grep for a string, so they pass if the string is present and fail if it is renamed — and they are completely blind to whether the surrounding code is correct.

Distribution, by AST analysis of every test body (a test is classified source-text when it reads a file *and* makes a string assertion):

| File | Count |
|---|---:|
| `test_bugfixes.py` | 17 |
| `test_layout_contract.py` | 6 |
| `test_cpp_kernel.py` | 5 |
| `test_dashboard_palette.py` | 4 |
| `test_advanced_modules.py` | 2 |
| `test_layout_budget.py` | 1 |
| `test_neural_net_layout.py` | 1 |

The dangerous ones — those asserting on a *behavioural* guarantee by reading source:

| Test | Asserts | What it misses | Site |
|---|---|---|---|
| `test_validation_reserves_fee_not_just_margin` | the literal `"required_cash = margin + estimated_fee"` and `"margin_required=required_cash"` appear in `paper_engine.py` | Renaming the variable fails the test while behaviour is unchanged; and if someone reintroduced the bug *differently* (e.g. adds the fee inside `RiskManager`), the string is still present and the suite stays green. The test's own docstring (`:508-513`) admits it is locked this way "because it is hard to test directly" | `test_bugfixes.py:507-523` |
| `test_stop_loss_uses_round_down` | `"rounding=ROUND_DOWN"` inside the `calculate_stop_loss` source block | Slicing the source by `src.index("def calculate_stop_loss")` → `src.index("def calculate_take_profit")` breaks silently if the function is renamed or moved. Same for the TP twin at `:772-778` | `test_bugfixes.py:763-778` |
| `test_daily_loss_uses_initial_balance_not_free_cash` | the string `"modal awal"` appears in the joined reasons | This is in a class named `TestDailyLossCircuitBreaker` and reads like a source check; it actually calls `validate_trade` and asserts on message text. Message wording is not the contract — the denominator is. It passes if the message is reworded *and* the logic breaks only when the reason string is also changed | `test_bugfixes.py:290-314` |
| `test_websocket_parsing_runs_off_event_loop` | `"asyncio.to_thread(self._handle_message"` in `hyperliquid_feed.py` | Renaming the method or adding a `.args` breaks the test with no behaviour change; conversely, an offloaded call that is then `await`ed in-line passes | `test_bugfixes.py:1098-1109` |
| `test_prune_job_is_scheduled` / `test_maintenance_loop_is_started` | `'name="direction_snapshot_prune"'` and `"def _maintenance_loop"` appear in `run.py` | Proves a string exists. Does **not** prove the job is registered with the scheduler, that the interval is right, or that the loop is ever scheduled | `test_bugfixes.py:1086-1096` |
| `test_reversal_clears_open_symbols_in_same_cycle` | the strings `"closing_symbols"` and `"open_symbols.pop"` appear in `decision_agent.py` | A variable name is not the ordering guarantee. The pop could happen *after* the OPEN pass and the test would still pass | `test_bugfixes.py:1120-1130` |
| `test_direction_agent_uses_configured_reversal_threshold` | `"reversal_close_threshold"` is present and `'sig["strength"] >= 0.70'` is absent | A different hardcoded threshold (0.65, 0.75) passes cleanly | `test_bugfixes.py:1111-1118` |
| `test_execution_agent_uses_dynamic_thresholds` | `"get_dynamic_tp_sl_thresholds"` in `execution_agent.py` | The import could be there and the call unused | `test_advanced_modules.py:351-357` |
| `test_cpp_releases_gil` | `"py::gil_scoped_release"` in `bindings.cpp` | Any one occurrence satisfies it — including one on a method that is not the hot path | `test_cpp_kernel.py:316-322` |
| `test_cmake_avoids_fast_math` | `-O2` present, `-Ofast`/`ffast-math` absent, after stripping `#` comments | Strips comments, so a genuine `-Ofast` in a **CMake function argument on a commented line** is missed. Acceptable trade-off, stated in the docstring | `test_cpp_kernel.py:324-341` |
| `test_hardcoded_rgba_replaced_with_token` | four specific `rgba(...)` strings absent from `hud_figures.py` | Enumerates exactly four literals. Any *other* hardcoded rgba sails through — and four rgba literals outside `:root` do survive in `style.css`, simply because this test only reads `hud_figures.py` | `test_bugfixes.py:711-730` |
| `test_gitignore_exists_and_covers_stray_logs` | `.gitignore` exists and contains `_*.log`, `data_store/*.db`, `__pycache__` | Tests the ignore file, not any bot behaviour. Belongs in CI, not the unit suite | `test_bugfixes.py:1132-1139` |
| `test_decision_tree_markup_is_gone` | `"tree-step-node"` and `"hud-tree-node-"` absent from `hud.py` | Dead-markup removal, not a contract | `test_layout_budget.py:156-163` |
| `test_every_dash_pattern_matches_css_keyframe` | real figure is built, **then** `neural_flow.css` is read for `"stroke-dashoffset: -16px"` | Hybrid: the Python side is behavioural, the CSS side is a string match. The two can disagree about which pixel value they agree on | `test_neural_net_layout.py:204-238` |

The remaining source-text tests are more defensible because they defend a *prose* rule that is otherwise unenforceable: no `var(--x)` in a figure file, no `plotly_dark`/`plotly_white` template, no GitHub-dark hex residue, `will-change` absent, `prefers-reduced-motion` present, `@keyframes` count ≤ 4, no hex outside `:root`, every `className` in `hud.py` has a matching CSS rule, no `std::vector` in the C++ header. These are the "silent failure" class the dashboard notes describe: a cyclic CSS token, a `var()` reaching canvas, or a dead `.Select-*` rule produces a broken panel with **no error anywhere**, so a grep is genuinely the cheapest available detector.

`TestNoCyclicCssVariables` is the most sophisticated member of this class: it strips comments before parsing, brace-counts the `:root` block (because `rgba(0,0,0,0.13)` contains braces and a naive split fails), and resolves transitively with a `seen` set to detect cycles at any depth (`test_layout_contract.py:198-242`). Its docstring (`:192-197`) records a real incident: a tokenization pass once produced 19 cyclic tokens neutralising ~80 declarations and rendering the whole terminal as one cream field.

### 7.5 FALSE CONFIDENCE — logic duplicated inside a test

The places where a test re-implements the production rule instead of calling it. These are the highest-risk items in the suite, because a green test here proves the *test's* arithmetic, not the bot's.

#### 7.5.1 `test_repaired_config_has_positive_expectancy` — reimplements the validator

`tests/test_config.py:86-102`:

```python
roundtrip = cfg.fees.taker * 2
net_tp = cfg.scalping.min_profit_pct - roundtrip
net_sl = cfg.scalping.tight_sl_pct + roundtrip
breakeven_wr = net_sl / (net_tp + net_sl)
self.assertGreater(net_tp, net_sl, ...)
self.assertLess(breakeven_wr, 0.50, ...)
```

This is a **hand-copied** version of the formula in `core/config.py`'s `_validate_scalping_economics`. It is the only test that asserts the *end* of the chain, and it asserts a copy. If the production formula drifts — say the roundtrip stops double-counting the taker fee, or `net_sl` stops adding the fee — this test stays green and the boot validator's own `ValueError` message is never checked. Worse, the same copy appears a third time in `tests/test_decision_agent_ensemble.py:197-202` (using `fast_tp_pct` instead of `min_profit_pct`) and a fourth in the docstring of `tests/test_bugfixes.py:1002` (`FeeConfig(maker=0.0002, taker=0.0005)`).

**Fix direction:** assert that `_validate_scalping_economics(cfg)` does not raise, then read the *production* breakeven figure back out of a single shared helper. The suite already has the better pattern in `tests/test_lifecycle_paths.py:669-703`, which reads `limit = rm.config.max_daily_loss` instead of writing its own.

#### 7.5.2 `_posix_key` in `test_live_tui.py` — a copy of the production key decoder

`tests/test_live_tui.py:340-356` reimplements `trading/live/tui.py::_read_posix`'s byte→key mapping, then `TestKeyParsing` (`:359-398`) tests **the copy**:

```python
def _posix_key(text):
    if text.startswith("\x1b["):
        return {"A": tui.UP, "B": tui.DOWN, ...}.get(text[2:3], None)
    ...
```

`TestKeyParsing`'s own docstring (`:361-366`) says a mis-mapping here "moves the cursor to the wrong menu entry" — and for a live-trading menu, wrong cursor + Enter is the wrong mode. But the test validates a re-implementation, so `_read_posix` can break and the suite stays green.

**Compounding this: all four cases skip on Windows** (`if os.name == "nt": self.skipTest(...)` at `:368-370`), and the developer machine is Windows 11. These are the 4 skips in the 566-case run. So on this platform, POSIX key parsing is asserted by a copy that also never executes. Only `test_live_engine.py` covers the Windows `msvcrt` path, and only indirectly.

#### 7.5.3 `TestReadability._luminance` — measures with the wrong formula

`tests/test_dashboard_palette.py:93-98`:

```python
    return 0.299 * r + 0.587 * g + 0.114 * b
```

Those are **YIQ luma** coefficients. WCAG relative luminance uses `(0.2126, 0.7152, 0.0722)`. The assertions at `:100-108` (`lum < bg - 40`) therefore pass or fail on a metric the product does not use, and the two orderings can disagree for saturated colours — which is exactly the palette in question (`--pos: #006a2b` green, `--neg: #9e1b16` red on `#dfd5b8` parchment).

#### 7.5.4 `test_flat_trade_loses_exactly_two_fees` — a magic absolute

`tests/test_lifecycle_paths.py:267-269`:

```python
self.assertAlmostEqual(float(pos["realized_pnl"]), -9.0, places=4, ...)
```

`-9.0` is `2 × 0.2 × 50000 × 0.00045`, i.e. it bakes in `quantity=0.2`, `price=50000.0`, and **taker 0.00045**. The same file's neighbouring tests avoid exactly this by reading the fee back from the `trades` table (`:87-90`, `:192-196`, `:246-250`). If the fee rate changes, this one case fails with a bare `AssertionError: -9.0 != -10.0` and no indication that the assertion, not the code, is stale. It is a violation of the convention the suite states for itself at `test_risk_manager.py:99-101` and `test_lifecycle_paths.py:676-679`.

#### 7.5.5 The silently shadowed tests

**Three test methods are defined but never run.** `tests/test_lifecycle_paths.py` defines `class TestFeeAccounting` **twice** — at line 75 and line 209. Python binds the second name over the first at module scope, so the first class object is unreachable, and with it:

- `test_realized_pnl_is_net_of_both_fees` @ 78 — shadowed by a same-named method at 271, which does cover the ground
- **`test_win_rate_not_inflated_by_round_trip_fee` @ 97** — never executed
- **`test_daily_pnl_reflects_opening_fee` @ 125** — never executed

The two lost tests are not redundant. `test_daily_pnl_reflects_opening_fee` is the **only** assertion in the entire suite that `get_daily_realized_pnl()` equals the stored `realized_pnl` — i.e. that the number the daily-loss circuit breaker reads (`trading/risk_manager.py:322`) is the same number the trade-quality metric holds. It is silently gone. `test_win_rate_not_inflated_by_round_trip_fee` is the only test that checks `get_trade_stats()` counts a fee-net loser as a **loser**, via `stats["winning_trades"] == 0`. Also silently gone. Both are the kind of end-to-end claim the suite otherwise prides itself on; a `def class` twice is invisible to `unittest discover` and to any coverage report.

This is the concrete explanation for the 570-vs-566 gap: the regex/AST counts find 570 `def test_` occurrences, but 4 of them are shadowed duplicates that are collected zero times or once instead of twice.

There is a second, milder instance: `TestEdgeRendering.test_axes_have_no_scaleanchor` is defined **twice** in `tests/test_neural_net_layout.py`, at lines 240 and 259, with the second overwriting the first. Here it is harmless — both bodies assert the same thing — but the second docstring (`:261-271`) records that the earlier test *locked `scaleanchor` as a feature* when it was in fact the cause of the layout collapse, so the duplicate reads like an incomplete edit rather than a deliberate one.

### 7.6 Other structural facts worth knowing before you run the suite

**`tests/t3.py` is not a test.** It is a 24-line live probe that opens a real WebSocket to `wss://api.hyperliquid.xyz/ws` and sends seven hand-written subscription payloads (`tests/t3.py:17-23`). It contains **no assertions whatsoever** — it prints. It is skipped by discovery only because the filename does not match `test*.py`. It has no `__main__` guard either; running `python tests/t3.py` executes it immediately against mainnet. Given that §7.4 lists several source-text checks that are arguably CI work, a directory of scripts that look like tests but are not is a trap for the next reader.

**The 4 skips are all the same skip.** `TestKeyParsing` (`test_live_tui.py:359`), all four cases, `skipTest("jalur POSIX tidak berlaku di Windows")`. Nothing else skips on this machine — the C++ kernel parity suites **did run**, because `cpp_microstructure.cp314-win_amd64.pyd` is present at the repo root and the interpreter is CPython 3.14. On any other CPython minor version, `TestCppPythonParity` (13) and `TestNativeKernelParity` (10) would skip too — 23 of the suite's strongest money/numerics contracts, gone silently, with `SKIP_REASON` (`test_cpp_kernel.py:50-54`) only reachable if you read the file.

**Several tests deliberately lock in behaviour that looks wrong**, and the docstrings say so:

| Test | What it locks | Why | Site |
|---|---|---|---|
| `test_legacy_function_still_callable` | `compute_diffusion_curve` still returns `winner_probs`/`loser_probs` and keeps its broken `abs(drift)` + clip-to-0.50 behaviour | Transitional shim; a behavioural change would silently alter old call sites | `test_probability_engine.py:230-244` |
| `test_bearish_is_weaker_than_bullish_by_drag` | For equal sigma, the bearish terminal sits strictly below the bullish mirror (`sum < 1.0`, `sum > 0.99`) | The `-0.5*sigma^2` term in `d2` shifts both cases the same way; the docstring calls it a physical consequence, not a defect | `test_probability_engine.py:203-212` |
| `test_insufficient_samples_fails_open` | Fewer than `stale_tick_min_samples` samples → order **allowed** | Refusing on missing history silences the bot at feed-connect time | `test_bugfixes.py:137-145` |
| `test_metrics_return_none_when_undefined` | `max_drawdown([]) == (None, None)` — a tuple, not `None` | The comment at `:673-674` records that an earlier `assertIsNone` here "will always fail" | `test_advanced_modules.py:665-676` |
| `test_quantization_precision_is_8_decimals` | Asserts the result is **on** the 1e-8 grid, not that it has exactly 8 decimals | `60000.0 * (1 - 0.0025)` is exactly `59850.0`, so `Decimal` reports exponent −1. A test demanding "always 8 decimals" would reject a valid round price | `test_bugfixes.py:794-814` |

**The suite mutates production module globals and does not fully restore them.** `core.config` is mutated in `test_direction_agents.py:250` (restored in a `finally` at `:261-263`); `core.microstructure._KERNEL` is poked directly at `test_bugfixes.py:936`; `analysis.volatility` module state (`_CANDLE_SOURCE`, `_ATR_CACHE`) is installed and cleared in `test_advanced_modules.py:138-140`; `trading.live.tui` module attributes (`_write`, `KeyReader`, `is_tty`, `enable_vt_processing`) are swapped in `setUp` and restored in `tearDown` at `test_live_tui.py:265-271, 607-626`. Running a single file in isolation gives different results from running it after others; there is no isolation layer.

**`os.environ` mutation is confined but real.** `test_live_tui.py:453,467,547,559,568,626` and `test_live_safety.py`'s `_env()` helper build a fake env dict rather than touching the process env for the gate, but `test_live_tui` genuinely sets and pops `HYPERLIQUID_PRIVATE_KEY` in the real `os.environ` because `ask_mode` reads it there.

**Runtime.** The full suite runs in ~8.3 s on this machine. Individual files are runnable: `python tests/test_live_safety.py`, or `python -m unittest tests.test_lifecycle_paths`.

### 7.7 COVERAGE GAPS — important behaviour with no test at all

Ordered by consequence, not by file.

#### 7.7.1 The live order path is never exercised end to end

`ExecutionAgent` and `LiveExecutor` are wired together at `run.py:363-368`, and the adapter is only ever tested against a stub engine. Nothing in the suite tests the interface between them. Three specific gaps:

| Gap | Why it matters | Evidence |
|---|---|---|
| **`ExecutionAgent` is never run against `LiveExecutor`.** `test_live_executor.py:219-226` asserts only that the constructor parameter is named `engine` and that `PaperTradingEngine`/`LiveExecutor` both expose `execute_order`/`update_price`. It does **not** check the four *other* members the agent actually uses: `get_price`, `check_positions`, `position_manager`, `_last_prices`. | A real-interface mismatch is invisible to the whole suite. The compatibility test checks the two methods that happen to be implemented, and the two engines are symmetric in exactly those two. | `test_live_executor.py:200-226` vs. the call sites in `agents/execution_agent.py` |
| **No test drives `DecisionAgent.act` → `LiveExecutor.execute_order`.** Every `test_decision_agent_ensemble.py` case stops at `_generate_scalp_signals`. | The `Order` that `DecisionAgent` constructs carries `stop_loss=None, take_profit=None`; the values are filled in later by `ExecutionAgent.think()`. The chain from signal to a live `Order` is unbroken only by inspection. | `test_decision_agent_ensemble.py:93-219` |
| **`LiveEngine.run_loop`, `emergency_flat`'s caller, and `LiveEngine.reconcile` are untested at the run.py boundary.** `emergency_flat` itself has 5 cases (`test_live_engine.py:284-352`), but nothing in `run.py` ever calls it. | A kill switch that engages and nothing calls `emergency_flat` is an untested liveness path. | `test_live_engine.py:261-352` |

#### 7.7.2 Money paths with no test

| Behaviour | Where | Why it matters |
|---|---|---|
| **The `0.9` free-cash margin cap is asserted only at its boundary.** `test_lifecycle_paths.py:636-652` pins `>` vs `>=` in `validate_trade`. The *other two* copies — `risk_manager.py:119` in `calculate_position_size` and `risk_manager.py:364` in `calculate_scalp_position_size` — are never asserted at all. | The scalp-sizing clamp is the one that actually binds in production (margin is `risk_pct * balance`, capped at `balance * 0.9 / max_open_positions`). No test pins that divisor. |
| **`max_leverage` is never tested on the order path.** `test_live_safety.py:514-543` covers the *live* gate. Nothing covers `risk.max_leverage` in `calculate_position_size` / `calculate_scalp_position_size`, and nothing covers the fact that `paper_engine.py:604` forwards the **raw** `order.leverage` to `open_position` (the clamp at `risk_manager.py:97`/`:358` only affects the sizing arithmetic, and `position_manager.py:70` then computes the liquidation price from the unclamped value). | A position can be recorded above the configured leverage cap with a liquidation price derived from unbounded leverage. No test can catch this today. |
| **The daily-loss circuit breaker's data source is only half-tested.** `test_bugfixes.py:249-260` tests `get_daily_realized_pnl` reads today's closes; `test_lifecycle_paths.py:669-703` tests the threshold. But the *integration* — `paper_engine.py:566` fetching it and passing it to `validate_trade` — is only exercised by `test_bugfixes.py:262-276`, which injects a raw SQL `INSERT INTO positions`. No test creates a real losing trade and observes the breaker stop accepting orders. |
| **`calculate_scalp_position_size` is barely tested.** `test_lifecycle_paths.py:800-804` asserts one relationship conditionally (`if s.get("quantity")`). Nothing tests the `max_open_positions` divisor, the interaction with `max_risk_per_trade = 0.005`, or what happens at `max_open_positions = 30`. |
| **Liquidation's accounting divergence is untested.** `test_position_manager.py:120-145` asserts `realized_pnl == -margin` on a liquidation — which is exactly the value that **omits the opening fee** that `close_position` deducts, and therefore **under-reports** the loss into `get_daily_realized_pnl`. The test locks the current number as correct. Liquidation also skips `bump_peak_balance` and `update_account_stats`; nothing asserts that either way. |
| **No funding-fee line item exists and none is tested.** `grep -rn funding trading/` returns zero matches. Positions hold for hours at 1.6 %/day carry on perp funding and nothing accrues. |

#### 7.7.3 The breakeven / scalp-TP / expiry exits are entirely untested as code

`grep -n "_protect_breakeven\|_scalp_take_profit\|_auto_close_expired\|check_positions" tests/*.py` returns **only docstring prose** in `tests/test_config.py:37-38` and one `breakeven_offset_pct=0.0015` config literal. No test calls any of them.

That means these are untested:

- `_protect_breakeven` moving the stop to breakeven, and its monotonic-improvement guard (only moves if SL is `None` or on the wrong side)
- `_scalp_take_profit` closing at `fast_tp_pct`
- `_auto_close_expired` closing at `max_hold_seconds`
- the **ordering constraint** that `_protect_breakeven` must precede `_scalp_take_profit`, and that `breakeven_trigger_pct` (0.0020) must stay below `min_profit_pct` (0.0060)

The config validator *does* enforce the last two relationships at boot (`tests/test_config.py:33-47`), which is a real defence — but the *behaviour* those thresholds gate is unexercised. Three of the bot's four exit paths are only reachable through a running agent, and no test runs an agent.

#### 7.7.4 Untouched modules, by name

Verified by AST-import scan across every `tests/*.py`:

| Module | Lines of production code | What goes untested |
|---|---|---|
| `data/price_feed.py` | 705 | The entire 4-tier fallback chain (Hyperliquid → Binance ccxt → yfinance → CoinGecko), symbol mapping, the `persist` flag that keeps yfinance rows out of `candles`, the OHLC invariant filter, `PRICE_UPDATE` publication, `discover_top_volume_symbols`. The README claims the yfinance-persistence rule; nothing locks it. |
| `data/hyperliquid_feed.py` | 573 | WebSocket connect/reconnect/backoff, `_parse_book`, `_parse_candle`, `_handle_message` dispatch, `_ensure_native_kernel`. Only touched by two source-text greps (`test_bugfixes.py:1098`, `test_cpp_kernel.py:343`). |
| `data/sentiment.py` | 213 | VADER thresholds, FinBERT batch, `aggregate_sentiment`. Zero tests. |
| `data/news_fetcher.py` | 154 | RSS parsing, CryptoPanic, the title-dedupe set. Zero tests. |
| `data/macro_fetcher.py` | 218 | FRED, Forex Factory, `interpret_macro_context` — including its `risk_level = HIGH when abs(net) == 0` behaviour. Zero tests. |
| `dashboard/callbacks/update_callbacks.py` | 1429 | All 19 callbacks: the three-layer price/candle merge, live-uPnL recomputation, the ASC-LIMIT-then-reverse equity reads, the `'N/A'`-instead-of-zero contract for `max_drawdown`/`sharpe`/`slippage`, the neural figure's "return the last good figure" fallback, the two 500 ms/60 s clock split. The `test_bugfixes.py:336-407` class reads the file as **text** to check `Output()` list-wrapping — it never calls a callback. |
| `dashboard/app.py` | 148 | `create_dash_app`, the stale-callback `KeyError` handler, `_silence_flask_banner`, `run_dashboard`. Zero tests. |
| `agents/base_agent.py` | 151 | The whole `run_cycle` error boundary, the `CancelledError` re-raise, `_log_cycle` / `_log_error`. Zero tests — the single most important control-flow guarantee in the agent layer is unexercised. |
| `agents/analysis_agent.py` | 318 | `_combine_signals` and its `0.40/0.25/0.35` blend, the ±0.15 band, the sentiment drain, `run_finbert_batch`'s raw SQL update. Zero tests. |
| `agents/news_agent.py` | 145 | VADER scoring, impact bucketing, `news_exists` dedupe, the `MARKET` sentinel Signal. Zero tests — yet `test_neural_net_layout.py:129-153` tests that the dashboard *filters out* the `MARKET` symbol the agent writes. |
| `analysis/ml_signals.py` | 257 | `extract_features`, `_predict_ml`, `_predict_rule_based`. Zero tests. The only `ml_prediction` fixtures in the suite (`test_probability_engine.py:65,86,92,97`) are hand-built dicts with **no `probabilities` key**, so they exercise only the string-token fallback branch — the trained RandomForest path and the rule-based scorer are never called. |
| `ml/trainer.py`, `ml/predictor.py` | 233 + 44 | Feature engineering, labelling, training split, artifact write. Zero tests (and both modules have no production caller). |
| `analysis/fundamental.py` | 153 | The macro/sentiment/calendar scorecard and `get_suggested_leverage`. Zero tests. |
| `analysis/vol_target.py` | 132 | Entirely dead module with no importer; zero tests, correctly. |
| `run.py` | 878 | Startup ordering, `_choose_mode`, the 0.3 s execution loop, `setup_scheduler`'s 11 jobs, `_prune_*`, `repair_candles`, `shutdown`. Zero tests. `test_live_session.py` at the repo root boots a real `TradingBotApp` for N seconds but has **no assertions**. |
| `core/scheduler.py` | 139 | `is_us_market_open`'s US/Eastern window, `add_agent_job`'s `max_instances=1`, `_adjust_intervals`' 60 s retiming, `start_immediately`' `_init` one-shot. Zero tests. |
| `core/logger.py` | 403 | `stage`/`step`/`stage_done` progress accounting, `begin_quiet_mode`/`end_quiet_mode`, the progress-aware `emit` wrapper. Zero tests. |
| `core/market_store.py` | 265 | Only the *setters* are used as test fixtures (`test_direction_agents.py`, `test_bugfixes.py`). No test asserts `get_price_age`, `get_price_history`'s windowing, `get_price_change`, the `trades[:50]` truncation, the `sz <= 0` filter in `set_order_book`, or the substring-vs-exact symbol-matching inconsistency between `get_ticker` and the other getters. |
| `core/event_bus.py` | 103 | `subscribe`/`publish` are used as plumbing in 7 test files, but no test asserts the contract: `put_nowait` drops the **oldest** event on `QueueFull` at `maxsize=1000`, or that `unsubscribe` / `get_channel_stats` exist at all (both have zero callers). |
| `core/utils.py` | 31 | `parse_db_timestamp` is used by `agents/decision_agent.py` and `agents/execution_agent.py` to gate entries on snapshot freshness — and the test for that gate (`test_decision_agent_ensemble.py:112`) injects **pre-formatted** timestamps, never a numeric, a `Z`-suffixed string, or an unparseable one. The UTC-normalisation function itself is never called in any test. |

#### 7.7.5 Behaviour that exists but has no assertion even in files that touch it

| Gap | Site |
|---|---|
| `BaseAgent.run_cycle`'s `CancelledError` re-raise — the one documented reason the error boundary is safe | `agents/base_agent.py:99-110` |
| `DirectionEnsembleAgent.run_cycle` overrides the base entirely, so **ensemble cycles write no `AgentLog` row at all**. No test asserts either the presence or the absence of that audit trail. | `agents/direction_agents.py:437-451` |
| `parse_db_timestamp` returning `0.0` for unparseable input, and `_snapshot_is_fresh` treating `ts <= 0` as stale | `core/utils.py:9-31`, `agents/decision_agent.py:340-346` |
| `SafetyGate.record_realized_pnl` — it has **zero production callers**, so `counters.realized_pnl` is permanently `0.0` and `Blocker.DAILY_LOSS_LIMIT` at `safety.py:293` is structurally unreachable. `test_live_safety.py:286-290` sets `counters.realized_pnl` **directly** to test the limit, so the test passes while the limit can never fire. | `trading/live/safety.py:415` |
| `PositionManager.batch_close_positions` returning a **count** rather than a list, and skipping unknown ids / symbols without prices | tested in 4 cases at `test_lifecycle_paths.py:343-382`, so this one *is* covered — noted for contrast |
| `prune_direction_snapshots` (per-symbol `ROW_NUMBER()` retention) — only `prune_agent_logs` is tested (4 cases) | `test_lifecycle_paths.py:472-532` covers only agent_logs |
| Dashboard: `'N/A'` instead of a fabricated `0` for `max_drawdown`/`sharpe`/`slippage`; the "return last good figure" fallback | `update_callbacks.py:1287-1320`, `:984-992` |
| `test_layout_contract.py` verifies every callback's `Output`/`Input`/`State` id is **mounted** — but no test verifies the reverse (mounted ids that nothing drives), and the layout notes record five such inert ids. | `test_layout_contract.py:81-105` |

---

## 8. Working with this codebase

### 8.1 Prerequisites and the one hard environment fact

| Requirement | Value | Source |
|---|---|---|
| Interpreter | CPython 3.14.6 | `python -V` on this machine |
| Native ABI | `cpp_microstructure.cp314-win_amd64.pyd` — CPython **3.14 only**, not limited-API | `CMakeLists.txt:36` (`find_package(Python3 COMPONENTS Interpreter Development.Module REQUIRED)`), file name |
| pybind11 | 3.1.0, found via `python -m pybind11 --cmakedir` | `CMakeLists.txt:40-55` |
| Toolchain | MinGW-w64 GCC at `C:/msys64/ucrt64/bin/c++.exe`; `ninja` and `cmake` in the same dir | `build/build.ninja`, `build/CMakeCache.txt` |
| CWD sensitivity | `config.yaml`, `ml/models/`, `data_store/` are all **CWD-relative** | `core/config.py:415` default `path="config.yaml"`; `ml/trainer.py:18` and `analysis/ml_signals.py:20` both `MODEL_DIR = Path("ml/models")` |

```bash
pip install -r requirements.txt          # 23 entries (verified); see §8.10 for the ones nothing imports
```

`requirements.txt` does not list `eth_account`, `flask`, `werkzeug`, `click` or `playwright`, all of which are imported by first-party or dev code (`trading/live/client.py:116`, `dashboard/app.py:7-10,116`, `verify_constellation.py:19`). `hyperliquid-python-sdk>=0.20.0` pulls `eth_account` and `msgpack` transitively, which covers the live path but not Flask/Werkzeug (they arrive with `dash`).

**Run everything from the repo root.** A different CWD silently gives you pure dataclass defaults instead of `config.yaml` — `load_config` returns early with no error when the file is missing (`core/config.py:419-421`), and those defaults differ on almost every economic knob.

### 8.2 Running the bot

#### Paper mode

```bash
python run.py --paper          # skips the mode menu entirely (run.py:172)
python run.py --non-interactive   # same path (run.py:172)
```

Without a flag, `python run.py` shows the terminal menu (`run.py:195-198` → `trading/live/console.py:405 ask_mode`). Every failure path in `_choose_mode` returns `paper`, never an exception — `KeyboardInterrupt`, `ImportError` on the console, and any refusal from the live confirmations all degrade to simulation with a `refused=True` flag (`run.py:199-204`).

CLI parsing is a substring test against `sys.argv`, not argparse. `--tes` matches `--testnet`; unknown flags are ignored.

Read the `Active Top Symbols` line of the startup banner, not `config.yaml`, to know what is actually being traded — `scanning.dynamic_top_volume: true` (`config.yaml:25`) means the top-10 scan overwrites the configured list at boot and again every `refresh_interval` seconds (`run.py:493-506`).

#### Live mode (testnet / mainnet)

```bash
HYPERLIQUID_PRIVATE_KEY=0x... TRADEBOT_LIVE=1 TRADEBOT_LIVE_CONFIRMED=1 python run.py --testnet
```

`--testnet` / `--live` short-circuit the menu (`run.py:180-193`); `--live` also prints a real-money warning. Otherwise choose mode in the TUI, where reaching live requires typing the exact phrase `SAYA MENGERTI` (`trading/live/console.py:38`) **and** retyping the derived wallet address (`console.py:78 decide_mode`, called from `console.py:405 ask_mode` → `console.py:462 _do_confirmations`). Cancellation returns `ModeDecision(mode="paper", refused=True)` — never an exception (`console.py:405-411`).

The three env vars above are the whole authorization surface, and **nothing in the repo sets the first two outside tests** (`tests/test_live_safety.py:39-40`, `tests/test_live_engine.py:105-106`).

Two things to know before trusting a live run:

- `run.py:262` builds a **bare** `LiveConfig()`, not `get_config().live`. Every limit the operator edited in the console's rule editor (`console.py:587 edit_rules`, which mutates the object in memory and never writes `config.yaml`) is discarded. Independently, `core/config.py:482` force-writes `config.live.enabled = False` on every load, and `config.yaml` has no `live:` block at all.
- The live order path is **structurally unreachable from the agent** — see §3.3 and §5.12.1.

Preflight without sending anything:

```bash
python live_doctor.py --testnet            # argparse; also --mainnet, --expect 0x…
```

Read-only by construction: it POPS `TRADEBOT_LIVE` and `TRADEBOT_LIVE_CONFIRMED` before building its own gate (`live_doctor.py:189-190`) and uses a temp counter file (`live_doctor.py:192`). Exit 0/1.

### 8.3 Running the dashboard

Standalone (no bot):

```bash
python dashboard/app.py         # __main__ block at dashboard/app.py:147
```

`run_dashboard()` (`dashboard/app.py:128-145`) reads `config.dashboard` and calls `app.run(host=cfg.host, port=cfg.port, debug=cfg.debug)`. Defaults: `127.0.0.1:8050`, `debug=False` (`core/config.py:69-73`, `config.yaml:119-123`).

Inside the bot, `start_dashboard` (`run.py:729-738`) spawns one daemon thread named `DashBoardThread` targeting `run_dashboard`. `test_live_session.py:19` neutralizes it with `app.start_dashboard = lambda: None`.

The hidden compatibility block (`dashboard/app.py:70-89`) mounts `display:none` elements with the old ids so a cached browser tab gets HTTP 200 instead of a `KeyError` traceback (handler at `app.py:38-44`).

### 8.4 Running the tests

```bash
python -m unittest discover tests            # 566 cases, OK (skipped=4), ~8.3 s  (re-measured)
python -m unittest tests.test_cpp_kernel     # one module
python tests/test_bugfixes.py                # every file except test_cpp_kernel.py has
                                             # its own `if __name__ == "__main__": unittest.main()`
```

There is no pytest, no `conftest.py`, no `tox.ini`, and no `.github/` workflow — nothing enforces that this runs.

Two side effects to be aware of before running against a working tree:

- `tests/t3.py` is a **live WebSocket probe**, not a test. Discovery skips it only because the filename does not match `test*.py`.
- Async DB tests overwrite the `database.db` module singleton (`db_module._db = self.db`) with no restore in `tearDown`, and clear the `core.market_store` singleton between tests (`tests/test_direction_agents.py:23 _reset_store`).

A practical script that also leaves a log:

```bash
python _t.py                 # runs everything, writes _t.txt
python _t.py tests.test_numerics
```

**`_t.py` never calls `sys.exit` with the result** (`_t.py:11-34`); it always exits 0. A failing suite cannot fail a shell pipeline that uses it. The summary line is in `_t.txt`: `run=… errors=… failures=… skipped=…`.

### 8.5 Rebuilding the C++ extension

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release
```

Verbatim from the `CMakeLists.txt:6-8` header. Output lands in the **project root**, not `build/` (`CMakeLists.txt:148-152`), so `import cpp_microstructure` works with no `PYTHONPATH`. A POST_BUILD step runs msys64 `strip.exe` on the artifact.

Current state, verified:

```
$ /c/msys64/ucrt64/bin/ninja -C build -n
ninja: no work to do.
cpp_microstructure.cp314-win_amd64.pyd   2026-09-27 23:01:23   408,576 bytes
cpp/microstructure_kernel.cpp            2026-09-27 22:31:57
cpp/bindings.cpp                         2026-09-27 22:22:19
```

The binary is newer than every source file, so it is in sync. Parity is bit-exact on the shipped build — verified directly:

```python
>>> import cpp_microstructure as m; m.__version__, m.MAX_LEVELS
('1.0.0', 32)
>>> # 20-level book through both kernels
cpp (0.08, 0.004987531172069825)
py  (0.08, 0.004987531172069825)
```

Two deployment facts: the `.pyd` still dynamically imports `libwinpthread-1.dll`, which in turn needs `api-ms-win-crt-private-l1-1-0.dll`; both are present at the repo root and must sit next to the `.pyd`. And `HL_JSON_USE_SIMDJSON` (`CMakeLists.txt:86`) is inert — it adds an include path and a `-D` define that no source file `#ifdef`s, and the option is `OFF` in `build/CMakeCache.txt`.

### 8.6 Retraining and loading the ML model

Retrain (offline, no bot running):

```bash
python -m ml.trainer
```

That is the whole interface. `ml/trainer.py:232-233` is `asyncio.run(train_from_exchange())` with **no argparse**, despite the docstring at `ml/trainer.py:5` advertising `--symbol BTCUSDT --timeframe 1h --days 90`. The flags are silently ignored.

`train_from_exchange` (`ml/trainer.py:171-216`) pulls `symbol="BTC/USDT:USDT"`, `timeframe="1h"`, `limit=1000` from ccxt Binance with a 4-second `asyncio.wait_for` (`:189`), falling back to yfinance on failure or empty result (`:198-216`). The yfinance path hardcodes `Ticker("BTC-USD")` and ignores the `symbol` argument.

The trainer writes `ml/models/signal_model.pkl` with `joblib.dump` after `MODEL_DIR.mkdir(parents=True, exist_ok=True)` (`ml/trainer.py:154-156`) — unconditional overwrite, no backup, no atomic temp-then-rename, no version, no checksum. The shipped artifact is 3.7 MB and tracked by git (`.gitignore` has no `*.pkl` entry).

Loading (runtime, by the bot, not by you):

```
AnalysisAgent.initialize()                     agents/analysis_agent.py:61
  └─ MLSignalGenerator.initialize()            analysis/ml_signals.py:43
       └─ loop.run_in_executor(None, _load_model)   ml_signals.py:45
            └─ joblib.load("ml/models/signal_model.pkl")   ml_signals.py:58
```

A missing or corrupt model is **not** fatal: the broad `try/except` at `ml_signals.py:46-50` downgrades any error to `logger.info`, sets `_model_loaded=False`, and the bot runs the whole session on `_predict_rule_based` (`ml_signals.py:189`) with nothing but an INFO line. No consumer ever checks `ml_prediction["method"]`.

Feature-vector contract — the two sides do not agree, and this matters before you retrain:

| Slot | Trainer trains on (`ml/trainer.py:48-77`) | Runtime feeds (`analysis/ml_signals.py:37-40, 63-135`) |
|---|---|---|
| 0 | `rsi = ta.rsi(...) / 100.0` continuous | `rsi / 100.0` — same |
| 1 | `macd_hist_norm = clip(macd/close, ±0.01) * 100` | `clip(macd/100, -1, 1)` — **different formula, different scale** |
| 2 | `bb_position = (close-lower)/(upper-lower)` continuous | categorical 0.0/0.5/1.0 from the `LOWER_BAND`/`UPPER_BAND` string |
| 3 | `ema_trend = clip((ema9-ema21)/close, ±0.01) * 100` | discrete ±1.0 / ±0.5 / 0.0 from `"CROSSOVER" in signal` |
| 4 | `volume_ratio = clip(vol/sma, 0, 3)/3` | from the `signals` dict strings |
| 5 | `sentiment_score` — **always 0.0** (`trainer.py:71-73`) | live sentiment score |
| 6 | `atr_pct = clip(atr/close, 0, 0.1) * 10` | same formula |

Slot 5 has `feature_importances_ == 0.0` in the shipped artifact because the tree can never split on a constant column, yet the runtime feeds a live value there. The trainer's `feature_cols` names the column `macd_hist_norm`; `ml_signals._feature_names` calls slot 1 `macd_hist`. Position is the contract, not the name.

`MODEL_DIR = Path("ml/models")` is declared **twice**, independently, in `ml/trainer.py:18` and `analysis/ml_signals.py:20`, neither anchored to the repo root. Train from the wrong directory and you write a fresh model somewhere else; run from the wrong directory and the bot silently loads nothing.

`ml/predictor.py` is a working but **unreferenced** wrapper — zero importers repo-wide. Editing the model interface means editing `ml/predictor.py`, `analysis/ml_signals.py`, and `agents/analysis_agent.py`, and only two of the three are live.

### 8.7 Extension recipes

#### Add a new direction agent (ensemble specialist)

| # | File | What to add |
|---|---|---|
| 1 | `core/config.py` (in `EnsembleConfig`, ~L226-272) | new `EnsembleAgentConfig` field with a `default_factory=lambda: EnsembleAgentConfig(weight=...)` |
| 2 | `config.yaml` (in `ensemble.agents.`, ~L202-214) | matching `enabled:` / `weight:` |
| 3 | `agents/direction_agents.py` | `class MyAgent(DirectionAgent)` with `agent_name`, `__init__`, `_evaluate(symbol)` returning a 4-tuple `(direction, confidence, reasoning, factors)`; construct it in `DirectionEnsembleAgent.initialize` (`:421-433`) |
| 4 | `core/config.py` (the tuples at `:276`, `:283`) | add the name to `EnsembleConfig.agent_configs` and `EnsembleConfig.base_weight` |
| 5 | `analysis/direction_ensemble.py` | nothing — `aggregate()` is name-agnostic; it reads `cfg.base_weight(v["agent"])` at `:137` |
| 6 | `tests/test_direction_ensemble.py` / `test_direction_agents.py` | extend the `AGENTS` list (`test_direction_ensemble.py:22`) and the coordinator test |

Read `DirectionAgent._evaluate` and the base-class no-ops at `direction_agents.py:87-100` first. Return `DIRECTION_NEUTRAL` with confidence `0.0` only when you *have* data and it says nothing; the base class cannot return `None`, so the abstain path is implemented per-agent (see `OrderFlowAgent._evaluate:114-126`, which returns `None` on the `(0.0, 0.0)` empty-book sentinel, and `TechnicalAgent._evaluate:215-219`, which returns `None` below 30 closes). `analysis/direction_ensemble.py:218 abstain` exists for that purpose, and `abstained=True` is excluded from **both** numerator and denominator of the pool (`:102`) — a blind agent must not dilute the average.

Confidence convention, already in use: `min(abs(z) / 2.0, 1.0)` for z-scored agents (`direction_agents.py:245`, `:310`), `min(abs(change) / (threshold * 2.0), 1.0)` for momentum (`:175`). `OrderFlowAgent` is the exception — raw `abs(ofi)`, unbounded, later clipped by `MAX_AGENT_Z = 2.5` (`analysis/direction_ensemble.py:32`).

Do **not** forget `run.py`: `DirectionEnsembleAgent` is constructed at `run.py:375-380` with `(event_bus, config.symbols, config.database_path)`, and the specialist list is a literal tuple at `direction_agents.py:422`.

#### Add a new indicator

| # | File | What to add |
|---|---|---|
| 1 | `analysis/technical.py` | column in `calculate_indicators` (`:41-92`); add a vote in `generate_signals` (`:94-299`) if it should produce a signal |
| 2 | `core/config.py` `IndicatorConfig` (~L56-65) | a field **only if** the period must be tunable — note the scalping periods are already hardcoded: `rsi_fast=7`, `ema_3`, `ema_5`, `roc_5`, `vol_sma=20`, `atr=14` (`technical.py:57-90`) |
| 3 | `config.yaml` `indicators:` (L83-91) | the matching value |
| 4 | `agents/direction_agents.py:230-233` | add the key name to `TechnicalAgent`'s `INDICATOR_KEYS` tuple, or the z-score cannot see it |
| 5 | `analysis/probability_engine.py:93-136` | extend `calculate_technical_zscore` if it should feed the composite |
| 6 | `analysis/ml_signals.py:37-40` | extend `_feature_names` **and** `extract_features` if it should reach the model — and expect to retrain |

`calculate_indicators` bails out returning the input frame unchanged when `len(df) < macd_slow + macd_signal` (= 35) (`technical.py:52-55`), so any new indicator silently does not exist on short frames. DirectionAgent needs ≥30 closes (`direction_agents.py:219`).

If the indicator must also reach the SL/TP path, it goes through the ATR channel, not `technical.py`: `analysis/volatility.py:91 atr_1m_from_candles` → `:163 atr_1m_pct` → `:266 get_dynamic_tp_sl_thresholds` → `trading/paper_engine.py:270 _resolve_tp_sl`.

#### Add a new dashboard panel

Five files, in this order:

| # | File | What to add |
|---|---|---|
| 1 | `dashboard/assets/style.css` | a `--h-<name>` height token in the `:root` block (L78-94). **Div owns the height; a Plotly figure never declares one** — a 1 px disagreement leaves a dead strip on every 500 ms repaint |
| 2 | `dashboard/layouts/hud.py` | a `create_<name>_panel()` returning `html.Div(className="hud-panel …")`, mounted inside the one `hud-grid` in `create_hud_layout` (`:37-56`) with a `span-N` className |
| 3 | `dashboard/layouts/hud_figures.py` | a `create_<name>_fig(...)` if there is a chart. Colors must come from `dashboard/layouts/palette.py`, **never** `var(--token)` — Plotly draws to canvas and silently paints nothing |
| 4 | `dashboard/callbacks/update_callbacks.py` | an `@app.callback` inside `register_callbacks` (`:114`), Inputs `dashboard-interval` or `dashboard-slow-interval`, `id=` matching the layout |
| 5 | `tests/test_layout_contract.py` / `test_layout_budget.py` | id-mount contract and the height/figure assertions |

Reference implementation to copy: `create_scanner_panel` (`hud.py:230-278`) plus `update_probability_scanner` (`update_callbacks.py:794`).

Non-obvious but load-bearing:

- Every callback id referenced in an `Output` must be **mounted** in the layout. A missing id does not raise — the panel just stops updating, with no error anywhere. `tests/test_layout_contract.py:78 TestComponentIdContract` is the gate.
- `dashboard/layouts/palette.py:52 _load()` parses `style.css` `:root` at import and re-exports hex. A self-referential `var()` in `:root` voids the whole declaration in the browser; `verify_css_resolution.py` and `test_layout_contract.py:182` both check for it.
- Read the DB with `_get_sync_db()` (`update_callbacks.py:64-69`) — it opens a fresh `sqlite3.connect` per call and closes it only on the success path. At 500 ms × ~11 callbacks that is ~22 connects/sec.
- Missing data must render an explicit empty state, never a synthetic number. `update_probability_scanner` returns `("--", "--", "AWAITING", …)` (`:809-817`).
- `style.css` has exactly one `@keyframes` (`hud-pulse-live`) applied to exactly one element; `tests/test_layout_budget.py` caps it at 4. `neural_flow.css` is separate and allowed its own two.

#### Add a new risk rule

Decide first which layer it belongs to, because the two rule sets are completely separate:

| Layer | Entry point | Applies to | Failure signal |
|---|---|---|---|
| Paper pre-trade | `trading/risk_manager.py:273 validate_trade` | the **open** path only | appends to `reasons`; `allowed = len(reasons) == 0` |
| Paper execution guard | `trading/paper_engine.py` `_execute_open` ladder | open only | writes an `AgentLog` `TRADE_REJECTED` row |
| Live master | `trading/live/safety.py:247 master_blockers` | all live orders, short-circuits | new `Blocker` enum member |
| Live per-order | `trading/live/safety.py:300 order_blockers` | one order, all evaluated and stacked | new `Blocker` enum member |

For a new **paper** limit:

1. Add the config field to `core/config.py` `RiskConfig` (`:18-25`) with a default, and to `config.yaml` `risk:` (L36-42).
2. Add the check in `validate_trade` (`risk_manager.py:296-338`). Note the two conventions already there: the daily-loss denominator is **frozen** `_initial_balance`, not live cash (`:319`, with the reasoning in the comment at `:306-318` — using live `balance` relaxes the breaker exactly when you are losing), and drawdown is measured on `equity` (free + margin + unrealized, supplied by `paper_engine.py:549`) not on free cash.
3. If it can be violated, the pre-trade gate is not enough — `validate_trade` is called from exactly one site (`paper_engine.py:566`) and every close path bypasses it. Guard the close path too or state explicitly that the rule is open-path-only.
4. Add a test that reads the threshold from config rather than hardcoding it. That convention is deliberate and repeated (`tests/test_risk_manager.py:119`, `tests/test_lifecycle_paths.py:684`): "a test that invents its own threshold stays green when config changes".

For a new **live** limit: add the member to `Blocker` (`safety.py:30-50`), add the check to `master_blockers` or `order_blockers`, then wire a `record_*` method (`safety.py:397-423`) that feeds it. A blocker with no data behind it is decoration — `record_realized_pnl` (`safety.py:415`) has zero production callers, which makes the `DAILY_LOSS_LIMIT` check at `:293` structurally unreachable despite `LiveConfig.max_daily_loss = 50.0`.

`edit_rules` in the console must be updated too if the field is operator-tunable: `EDITABLE` (`console.py:491-502`), `rule_options` (`:565`), and both `edit_rules` (`:587`) and `edit_rules_plain` (`:654`).

### 8.8 Debug and measurement tools in the repo root

| Script | Lines | Invocation | Purpose | Exit code | Output |
|---|---:|---|---|---|---|
| `live_doctor.py` | 254 | `python live_doctor.py --testnet` | read-only live preflight: key, reachability, balance, positions, open orders, `SafetyGate.can_send`, asset rules, quantization, rate limit | 0/1 | stdout |
| `test_live_session.py` | 86 | `python test_live_session.py 90` | boots a real paper session for N seconds (default 90) and prints PnL. No assertions — a manual harness. Only file in the repo that imports `run.py` (`:8`) | — | stdout |
| `check_behavior.py` | 175 | `python check_behavior.py` | 11 runtime-behaviour assertions on RiskManager math, ATR, ensemble aggregation, console rule validation. Exits non-zero on failure — usable as a gate | 0/1 | stdout |
| `check_dashboard_data.py` | 168 | `python check_dashboard_data.py` | post-hoc proof the callbacks ran (looks for `balance_history` rows) and cross-checks HUD arithmetic against the DB | 0/1 | `data_store/dashboard_check.txt` |
| `backfill_realized_pnl.py` | 111 | `python backfill_realized_pnl.py` → report; `--apply` to write | recomputes `positions.realized_pnl` net of **both** fees. `shutil.copy2` timestamped backup first, then one transaction | 0/1 | stdout |
| `reset_paper_db.py` | 36 | `python reset_paper_db.py` | wipes trades/positions/agent_logs/signals, resets the newest account row to 10000 USDT. The only root script using the app's own aiosqlite path | — | stdout |
| `_audit_refs.py` | 113 | `python _audit_refs.py` | pure-AST check that every `tui.*` / `console.*` / `c.*` cross-module reference actually exists. Explicitly a build gate | 0/1 | stdout |
| `_t.py` | 37 | `python _t.py [module]` | runs the suite, writes `_t.txt` | **always 0** | `_t.txt` |
| `_econ.py` / `_econ3.py` | 83 / 82 | `python _econ.py` | theoretical breakeven win rate vs actual, PnL broken down by `close_reason`; `_econ3` adds loss percentiles as % of margin per reason | — | `_econ.txt` / `_econ3.txt` |
| `_smoke_console.py` | 99 | `python _smoke_console.py` | headless smoke test of the live-mode terminal menu: monkeypatches `input` and `getpass.getpass` with a scripted queue, asserts 6 rejection + 2 acceptance scenarios | raises `SystemExit` on mismatch | stdout |
| `_fixcjk.py` | 55 | `python _fixcjk.py <file.py>` | scans for stray CJK/Hangul/fullwidth chars that crash Python on a cp1252 console. **Report-only — it never removes anything** | 0/1 | `<file>.cjk-report.txt` |
| `verify_neural_net.py` | 158 | `python verify_neural_net.py` | 4 pure-Python constellation invariants: slot stability when tokens disappear, no duplicate coordinates, `MARKET` sentinel never becomes a coin node, edge rules | 0/2 | `data_store/neural_check.txt` |
| `verify_css_resolution.py` | 110 | `python verify_css_resolution.py` | resolves every `--token` in `:root` and flags cyclic or undefined ones (browsers silently drop those declarations) | 0/2 | `data_store/css_resolution.txt` |
| `verify_dashboard_palette.py` | 157 | `python verify_dashboard_palette.py` | asserts no literal `var(--` or dark-template string reaches a figure, then calls every builder | 0/1 | stdout |
| `verify_dashboard_render.py` | 90 | `python verify_dashboard_render.py` | one level downstream: renders each figure to HTML and checks the parchment hex is present | 0/1 | stdout |
| `verify_constellation.py` | 319 | `python verify_constellation.py` | Playwright/Chromium measurement of the real `#hud-neural-graph` at 1920×1080 against a **running** dashboard on `:8050` | 0/1/2 | `data_store/constellation_check.json` |
| `verify_graph.py` | 186 | `python verify_graph.py` | superseded by `verify_constellation.py`. Its edge-length metric is a stub that appends a literal `0.0`, so "long edges > 150px" is always 0 in its own report | — | `data_store/graph_check.json`, `graph_shot.png` |
| `inspect_dropdown.py` | 155 | `python inspect_dropdown.py` | one-off probe for the Dash 4 dropdown class-name rewrite. Not in the README | — | stdout |
| `_ctx_imports.py`, `_imports2.py`, `_extract_sigs.py` | 75 / 42 / 54 | — | one-off AST import/signature extraction helpers used to build the analysis in §2 and §1.4 | — | stdout |

`verify_constellation.py` and `verify_graph.py` need `playwright` (`verify_constellation.py:19`) which is **not** in `requirements.txt`, and a dashboard already running. They cannot run in a clean install.

The 18 `_*.log` files at the repo root (~600 KB) are pure shell-redirect artifacts; `.gitignore:6-10` states outright that no code reads them.

### 8.9 Sharp edges

#### Stale snapshots under `data_store/_snap*`

Three directories hold frozen full copies of `dashboard/layouts/hud_figures.py`, and **none is imported by the live bot**:

```
data_store/_snapshot/hud_figures_snap.py      893 lines   oldest, 5-hub nearest-match
data_store/_snap/hf_28291.py                  918 lines   6+4 two-row, 6-tuple token_hubs
data_store/_snap/hud_figures_1309.py          920 lines
data_store/_snap_cp/hud_figures_snapshot.py  919 lines   calibration baseline
data_store/_snap/hf_cand.py                   937 lines   generated candidate
```

Verified divergence:

```
dashboard/layouts/hud_figures.py            md5 339ca510c27776e2cf31d78e40305350   (972 lines, LIVE)
data_store/_snap_cp/hud_figures_snapshot.py md5 3b408c2ab61a58aa91c8eabdb12f58fe   (919 lines)
```

Production now uses a 4-column tree with intermediate `CATEGORY_*` nodes and two-column token slots; the snapshots use the older fan topologies. `_snap_cp/model.py` pins its own constants to the `_snap_cp` snapshot, so that calibration model no longer describes production.

**Editing the wrong file is the actual failure mode.** `data_store/apply_candidate.py:119 build_module` regex-patches a *copy* of production into `_snap/hf_cand.py` and raises `AssertionError` if a pattern no longer matches — it fails loudly, but its patterns are anchored to file text that no longer exists, so it is inert. `data_store/layered.py:103 apply(hf)` mutates the **live** `hud_figures` module object in place; importing it is enough to change production geometry. The `__pycache__` directories next to all three snapshot folders mean an editor's "go to definition" from a stale reference lands in a dead copy.

Only two files reference the live figure: `dashboard/layouts/hud.py:320` (default figure at layout-construction time) and `dashboard/callbacks/update_callbacks.py:977` (per-tick rerender).

#### Duplicate logic between live and paper

The two execution paths share everything from the WebSocket through `DecisionAgent` and the `TRADE_DECISION` event, and diverge at exactly one line — `run.py:362-368`, where `self.executor` is bound to either `paper_engine` or `LiveExecutor`. **They never rejoin.** See §3.3 and §5.1 for the full table.

`trading/live/` has **no `__init__.py`** — it is a PEP 420 implicit namespace package. `trading/__init__.py` is a docstring only, matching every other package in the repo. So `import trading.live.client` works but there is no export surface to lean on.

One more trap: `RiskManager` is instantiated three times. `paper_engine.py:60` (correctly injected with the DB's `initial_balance` at `:86-88`), `agents/execution_agent.py:40` (bare, `initial_balance=0.0`), and `agents/decision_agent.py:36` (receives the paper engine's). On any un-injected instance the daily-loss denominator silently degrades to live `balance` (`risk_manager.py:319`) — the exact shrinking-denominator failure the comment two lines above warns about.

#### The prebuilt `.pyd`

Five things will bite:

1. **CPython 3.14 only, and not limited-API.** On any other interpreter both parity suites (`TestCppPythonParity`, `TestNativeKernelParity`) **skip silently** rather than fail — the skip is unconditional on `cpp_microstructure is None` (`tests/test_cpp_kernel.py:40-73`, decorator at `:73`). A green suite on Python 3.12 proves nothing about the kernel. `tests/test_cpp_kernel.py:186-193` records the honest consequence: a fixture with a malformed `[[[` payload survived undetected precisely because the class never ran.
2. **Two sidecar DLLs must sit next to it.** `libwinpthread-1.dll` and `api-ms-win-crt-private-l1-1-0.dll`, both present at the repo root, both required. `cpp/include/microstructure_kernel.h:74-83` documents the chain: `std::mutex` drags in libwinpthread, which needs the private UCRT DLL.
3. **A missing `.pyd` is not an error.** `initialize_native_kernel` falls back to `PythonKernel` with a single `logger.info` (`core/microstructure.py:355-366`). The numbers are identical — `PythonKernel` is the reference implementation the C++ is parity-tested against — so the only symptom is slowness, and nothing surfaces it at WARNING. Use `TRADEBOT_KERNEL=cpp` to convert a silent fallback into a hard `RuntimeError`.
4. **`.gitignore:71` says `*.pyd`, yet the file is present in the working tree** with mtime 2026-09-27 23:01:23. This working tree is not a git repository, so the ignore rule is inert here. Do not assume the file is version-controlled on another machine.
5. **`CppMicrostructureKernel.__init__` imports the real module unconditionally**, even when `native_module` is passed (`core/microstructure.py:276-279`). Constructing the adapter without a compiled `.pyd` raises `ImportError` instead of using the injected module — which is exactly what the parity tests do at `tests/test_cpp_kernel.py:67`.

Two more native-boundary facts worth knowing before you feed it your own data:

- `book_from_python` is declared `noexcept` (`cpp/bindings.cpp:32-33`) but calls `item.cast<std::pair<double,double>>()` inside (`:37,:41`), which throws `pybind11::cast_error`. `noexcept` routes it to `std::terminate` — a 3-tuple level, a `None` size, or even numeric **strings** kills the interpreter with no catchable exception. Production is safe today only because the sole writer, `_parse_book` (`data/hyperliquid_feed.py:313-350`), normalizes to float and `continue`s past bad levels. Drop the `noexcept` if you write a third-party adapter.
- Ingesting more than 32 levels per side silently truncates and returns a plausible-looking OFI. `overflow_bid_` / `overflow_ask_` are set (`cpp/microstructure_kernel.cpp:96-97`) but **never bound to Python** — `dir()` on the kernel shows only the 6 methods. Hyperliquid sends 20 levels, so this never fires today; a venue that adds levels would corrupt OFI invisibly.

#### Other traps the tests themselves name

- **Config default vs YAML divergence is the norm, not the exception.** See the table in §4.4.
- **`get_candles` returns newest-first** (`database/repository.py:92` `ORDER BY timestamp DESC`). Three readers reverse it in Python; any new reader that forgets silently plots time backwards. The same trap with `ORDER BY … ASC LIMIT n` returns the **oldest** n rows.
- **PnL lives in `positions.realized_pnl`, not `trades`.** `trades` has no pnl column. Eight dashboard callbacks say so in comments. Any new metric must follow `t.pid = p.id`.
- **Every close path bypasses the tick guard, the volatility gate, and `validate_trade`.** See §5.8.
- **`check_positions` ordering is load-bearing.** `_protect_breakeven` must run before `_scalp_take_profit`, and `breakeven_trigger_pct` (0.0020) must stay **below** `min_profit_pct` (0.0060). Equal values make the entire breakeven path dead code. The boot validator rejects it (`core/config.py` `_validate_scalping_economics`) and `tests/test_config.py:33` locks it.
- **`Order.stop_loss` / `take_profit` from the agent are advisory and discarded.** `_execute_open` recomputes both from the fill price (`paper_engine.py:481-482`). The agent still computes them so `DecisionAgent`'s reasoning quotes a live number and rejections leave an audit trail.
- **`shutdown()` is not idempotent and is reachable twice** — from the signal handler (`run.py:832`) and from the `finally` in `main()` (`run.py:843`). It cancels the five tracked tasks without awaiting them, and does **not** cancel `self._live_task` (`run.py:292`), so on testnet/mainnet the exchange-poll loop outlives `shutdown()`. On Windows `loop.add_signal_handler` raises `NotImplementedError` and is swallowed (`run.py:830-835`), so Ctrl+C is handled only by the `KeyboardInterrupt` path.
- **`on_decision` in the live loop is a stub** returning `None` (`run.py:288-290`). `LiveEngine.run_loop` is monitor-only by default — the real order path is `ExecutionAgent` → `LiveExecutor.execute_order`, not the loop.
- **Nothing ever clears the kill switch.** `SafetyGate.engaged` is a sticky in-process latch with six raisers (`safety.py:401`, `engine.py:128`, `:404`, `:555`, `:599`, `:737`) and zero releasers. Restart the process, or set `TRADEBOT_LIVE_KILL_SWITCH=0`.
- **The `__main__`-block exception handler matters.** `run.py:864-878` turns a live-config `RuntimeError` into `BOT TIDAK DIJALANKAN.` + exit 2. Without it the operator sees a raw traceback that does not say whether the cause is configuration or a bug.
- **The `t3.py` trap.** A live WebSocket probe against the Hyperliquid mainnet endpoint sitting inside `tests/`. Discovery skips it by filename, but nothing stops someone running it.

### 8.10 Dependency reality check

`requirements.txt` declares **23 entries** (verified: `grep -vE "^\s*#|^\s*$" | wc -l`). AST scan across all first-party `.py` shows four are never imported: `praw` (line 5), `fredapi` (line 7 — `data/macro_fetcher.py` uses raw `requests`), `dash-bootstrap-components` (Dashboard section), and `torch` (pulled transitively by `transformers`). Conversely, `playwright` is **absent from `requirements.txt` entirely** yet genuinely imported by 18 dev/measurement files — 3 at the root (`verify_constellation.py`, `verify_graph.py`, `inspect_dropdown.py`), 7 at the `data_store/` root, and 8 in `data_store/_snap_cp/`. None is on a production import path, so the suite only runs on a machine where playwright happens to be installed.

Three modules are entirely unreferenced by any importer, tests included: `analysis/vol_target.py`, `ml/predictor.py`, `ml/trainer.py` (the last is an offline CLI). `analysis/backtester.py` — 980 lines with a correct FIFO-queue book-walk fill model — is imported by tests only, never by `run.py` or any production package. Production fills are zero-slippage last-price; the good fill model is never called.

---

The test suite was executed once during integration (`Ran 566 tests, OK (skipped=4), exit 0`). No other code in the repository was run.

## 9. Subsystem reference

Every source file in the repository, grouped by what it does rather than by when it loads. Each file gets one subsection in the same shape: **Role** in one line, a **Key symbols** table of classes, functions and constants with their signatures and `file:line` locations, then **State it owns**, **Side effects** and **Consumers**. Where a decision depends on exact numbers — scoring weights, pre-trade limits, guard orders, rounding modes — the numbers are inlined as their own table rather than paraphrased.

The four groups are ordered the way the process runs: signals are produced, orders are executed, feeds fill the database, and infrastructure serves all three. Subsection order inside a group follows the part files, not the import graph. Every figure quoted below is the value actually in force, including where a file's own prose disagrees with its body — those disagreements are called out in **Note** rather than smoothed over.

Two derived tables close the section. The **import table** (9.5) lists every first-party import edge whose two endpoints sit in different groups, taken from an AST walk of the repository rather than from the per-file **Consumers** lines, which additionally record symbol-level call sites, function-local imports and test consumers. The **group summary** (9.6) folds those same edges into a dependency rule per group. `run.py` is the one module the four parts do not cover: it is the composition root, so it appears in 9.5 and gets its own row in 9.6, but it has no subsection here.

---

### 9.1 Signal and agent layers (`agents/`, `analysis/`)

#### 9.1.1 `agents/base_agent.py`

**Role.** Abstract base for every agent; fixes the `sense() -> think() -> act()` lifecycle and writes one `AgentLog` row per cycle or error.

**Key symbols.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `BaseAgent` | class | `class BaseAgent(ABC):` | `agents/base_agent.py:20` |
| `.__init__` | method | `def __init__(self, name: str, event_bus: EventBus):` | `agents/base_agent.py:30` |
| `._get_repo` | method | `async def _get_repo(self) -> Repository:` | `agents/base_agent.py:38` |
| `.sense` | abstract method | `async def sense(self) -> dict:` | `agents/base_agent.py:45` |
| `.think` | abstract method | `async def think(self, data: dict) -> dict:` | `agents/base_agent.py:55` |
| `.act` | abstract method | `async def act(self, analysis: dict):` | `agents/base_agent.py:68` |
| `.run_cycle` | method | `async def run_cycle(self):` | `agents/base_agent.py:77` |
| `._log_cycle` | method | `async def _log_cycle(self, analysis: dict):` | `agents/base_agent.py:119` |
| `._log_error` | method | `async def _log_error(self, error_msg: str):` | `agents/base_agent.py:136` |
| `.publish` | method | `async def publish(self, channel: str, data: dict):` | `agents/base_agent.py:149` |

**State it owns.** `name: str`, `event_bus: EventBus`, `logger` (`get_logger(f"agent.{name}")`), `_repo: Optional[Repository]` (built once, cached), `_running: bool = False`, `_cycle_count: int = 0`; per-cycle `cycle_id = f"{self.name}#{self._cycle_count}"`.

**Side effects.** DB `repo.insert_agent_log` with `action="CYCLE"` (reasoning from `analysis["reasoning"]` or `["summary"]`, `input_data`/`output_data` JSON-truncated to 2000 chars) or `action="ERROR"`; `publish` writes to the in-process `EventBus`. No network.

**Consumers.** `agents/analysis_agent.py:12`, `agents/decision_agent.py:12`, `agents/execution_agent.py:14`, `agents/news_agent.py:11`, `agents/direction_agents.py:28`.

**Note.** `run_cycle` re-raises `asyncio.CancelledError` (`:99-110`); `except Exception` (`:112`) cannot catch it — it descends from `BaseException`.

#### 9.1.2 `agents/news_agent.py`

**Role.** Agent 1. RSS + CryptoPanic news → VADER score per item → aggregate broadcast on `Channels.NEWS_SENTIMENT`.

**Key symbols.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `NewsAgent` | class | `class NewsAgent(BaseAgent):` | `agents/news_agent.py:21` |
| `.__init__` | method | `def __init__(self, event_bus: EventBus, sentiment_analyzer: SentimentAnalyzer):` | `agents/news_agent.py:31` |
| `.sense` | method | `async def sense(self) -> dict:` | `agents/news_agent.py:36` |
| `.think` | method | `async def think(self, data: dict) -> dict:` | `agents/news_agent.py:43` |
| `.act` | method | `async def act(self, analysis: dict):` | `agents/news_agent.py:110` |

**Decision procedure (`think`).** Per item, text is `f"{item.title}. {item.content_summary or ''}"`; VADER `compound` sets `sentiment_vader`/`sentiment_label`. Impact on `abs(compound)`: `> 0.5` → `HIGH`, `> 0.2` → `MEDIUM`, else `LOW`. Aggregate via `SentimentAnalyzer.aggregate_sentiment`. No news → `{"avg_score": 0, "label": "NEUTRAL", "count": 0}`.

**State it owns.** `fetcher: NewsFetcher` (built internally), `sentiment: SentimentAnalyzer` (injected).

**Side effects.** Network `NewsFetcher.fetch_all()`, `analyze_vader_async`. DB `repo.news_exists(title, source)` → `repo.insert_news(item)`; `repo.insert_signal(Signal(symbol="MARKET", signal_type="SENTIMENT", direction=aggregate["label"], confidence=min(abs(avg_score) * 2, 1.0), source="news_agent"))`. Publishes aggregate + top-5 high-impact titles.

**Consumers.** `run.py:347`; interval `news_agent` = 300 s (120 s when US open).

#### 9.1.3 `agents/analysis_agent.py`

**Role.** Agent 2. 5m OHLCV per symbol → technical + fundamental + ML → one weighted direction → `Signal` row + `Channels.MARKET_ANALYSIS`.

**Key symbols.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `AnalysisAgent` | class | `class AnalysisAgent(BaseAgent):` | `agents/analysis_agent.py:27` |
| `.__init__` | method | `def __init__(self, event_bus: EventBus, price_feed: PriceFeed, macro_fetcher: MacroFetcher, sentiment_analyzer: SentimentAnalyzer):` | `agents/analysis_agent.py:37` |
| `.initialize` | method | `async def initialize(self):` | `agents/analysis_agent.py:58` |
| `.sense` | method | `async def sense(self) -> dict:` | `agents/analysis_agent.py:64` |
| `.think` | method | `async def think(self, data: dict) -> dict:` | `agents/analysis_agent.py:91` |
| `.act` | method | `async def act(self, analysis: dict):` | `agents/analysis_agent.py:171` |
| `.update_macro` | method | `async def update_macro(self):` | `agents/analysis_agent.py:200` |
| `.run_finbert_batch` | method | `async def run_finbert_batch(self):` | `agents/analysis_agent.py:222` |
| `._combine_signals` | method | `def _combine_signals(self, technical: Dict, fundamental: Dict, ml_prediction: Dict) -> Dict:` | `agents/analysis_agent.py:262` |

**Decision procedure (`_combine_signals`).** 1. `weights = {"technical": 0.40, "fundamental": 0.25, "ml": 0.35}` (`:271`). 2. `direction_to_score` maps `{"BULLISH": 1, "LONG": 1, "BEARISH": -1, "SHORT": -1}`, default `0`. 3. `weighted_score = tech_score * 0.40 * tech_conf + fund_score * 0.25 * fund_conf + ml_score * 0.35 * ml_conf`. 4. `combined_confidence = 0.40*tech_conf + 0.25*fund_conf + 0.35*ml_conf`, or `0` when that sum is `<= 0`. 5. `> 0.15` → `BULLISH`, `< -0.15` → `BEARISH`, else `NEUTRAL`. 6. Returns `confidence = round(min(combined_confidence, 1.0), 3)`, `weighted_score`, per-component detail. Sentiment label (`:107-112`): `> 0.05` → `POSITIVE`, `< -0.05` → `NEGATIVE`, else `NEUTRAL`.

**State it owns.** `price_feed`, `macro_fetcher`, `sentiment`, `technical: TechnicalAnalyzer`, `fundamental: FundamentalAnalyzer`, `ml_signals: MLSignalGenerator`, `config`; caches `_last_macro_context: Dict = {}`, `_last_sentiment_score: float = 0.0`, `_news_queue`.

**Side effects.** `PriceFeed.fetch_ohlcv(symbol, timeframe="5m", limit=200)`, `MacroFetcher.fetch_all`, `SentimentAnalyzer.analyze_finbert_batch`, `Repository.get_recent_news(limit=20)`. DB: `repo.insert_signal(Signal(signal_type="TECHNICAL", source="analysis_agent"))`, `repo.upsert_macro(m)` per FRED row, raw `db.execute("UPDATE news SET sentiment_finbert = ?, sentiment_label = ? WHERE id = ?")` + `db.commit()`.

**Consumers.** `run.py:349`; jobs `macro_update` (21600 s, `run.py:569`), `finbert_batch` (900 s, `run.py:576`); `update_macro()` at boot (`run.py:397`).

#### 9.1.4 `agents/decision_agent.py`

**Role.** Agent 3, the scalping entry/exit engine. Computes no signals: reads the `direction_snapshots` row written by the ensemble, applies cooldown / spread / confidence gates, emits `TradeDecision` on `Channels.TRADE_DECISION`.

**Key symbols.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `DecisionAgent` | class | `class DecisionAgent(BaseAgent):` | `agents/decision_agent.py:26` |
| `.__init__` | method | `def __init__(self, event_bus: EventBus, risk_manager: RiskManager):` | `agents/decision_agent.py:36` |
| `.initialize` | method | `async def initialize(self):` | `agents/decision_agent.py:48` |
| `.sense` | method | `async def sense(self) -> dict:` | `agents/decision_agent.py:53` |
| `.think` | method | `async def think(self, data: dict) -> dict:` | `agents/decision_agent.py:91` |
| `.act` | method | `async def act(self, analysis: dict):` | `agents/decision_agent.py:257` |
| `._generate_scalp_signals` | method | `async def _generate_scalp_signals(self, symbol: str, analysis: Dict) -> List[Dict]:` | `agents/decision_agent.py:289` |
| `._snapshot_is_fresh` | method | `def _snapshot_is_fresh(self, created_at) -> bool:` | `agents/decision_agent.py:340` |
| `._snapshot_agreement` | staticmethod | `def _snapshot_agreement(snap: Dict) -> str:` | `agents/decision_agent.py:349` |
| `._determine_leverage` | method | `def _determine_leverage(self, risk_level: str) -> int:` | `agents/decision_agent.py:374` |

**`sense` (`:53`).** Drain `_analysis_queue` (`MARKET_ANALYSIS`) into `_latest_analyses[symbol]`. Drain `_position_queue` (`POSITION_UPDATE`); on `action == "CLOSED"`: `realized_pnl <= 0` → `streak = _symbol_loss_streak[symbol] + 1`, `cooldown = scalp.cooldown_after_loss_seconds (90.0) * min(streak, 4)`, `_global_loss_streak += 1`; `> 0` → pop streak, `cooldown = scalp.cooldown_after_close_seconds (20.0)`. Either way `_symbol_cooldowns[symbol] = time.time() + cooldown`. Also returns `us_market_open = is_us_market_open()` — computed, not used as a gate.

**`think` (`:91`), step by step.** 1. No analyses → hold-all, `reasoning="Menunggu data analisis"`. 2. `_generate_scalp_signals` per symbol, then `scalp_signals.sort(key=lambda x: x[2]["strength"], reverse=True)`. 3. `open_count = len(await repo.get_open_positions())`; `max_new = max(0, config.risk.max_open_positions (3) - open_count)`; `batch_limit = min(scalp.batch_size (2), max_new)`. 4. **CLOSE pass** — `reversal_threshold = float(getattr(self.scalp, "reversal_close_threshold", 0.70))`; skip positions younger than `scalp.min_hold_seconds (15 s)`; emit `TradeAction.CLOSE` when a LONG faces `BEARISH` or a SHORT faces `BULLISH`, both needing `strength >= reversal_threshold`. 0.70 sits above `min_confidence` (0.40) deliberately: opening on a weak signal is normal, reversing a live position burns two fees and usually lands on the whipsaw that hits the stop. Symbols in `closing_symbols` are popped from `open_symbols` in the same cycle so a reversal can be followed by an entry, with `_symbol_cooldowns` as the second fence. 5. **OPEN pass** — `break` at `opened >= batch_limit`; reject when symbol in `chosen_symbols`, or `now < _symbol_cooldowns.get(symbol, 0)`, or `sig["strength"] < scalp.min_confidence (0.40)`, or live spread from `market_store.get_order_book` with `mid = (bids[0][0] + asks[0][0]) / 2` and `spread_pct = (asks[0][0] - bids[0][0]) / mid` exceeds `scalp.max_spread_pct (0.0006)`, or `len(open_symbols.get(symbol, [])) >= 1`. 6. `side = Side.LONG if sig["direction"] == "BULLISH" else Side.SHORT`; `action = TradeAction.OPEN_LONG | OPEN_SHORT`; `leverage = _determine_leverage(analysis["fundamental"]["risk_level"])`; `stop_loss_pct = scalp.tight_sl_pct (0.0025)`; `take_profit_pct = scalp.fast_tp_pct (0.0060)`; `confidence = sig["strength"]`. 7. `_determine_leverage` (`:374`): `HIGH` → `max(2, default // 2)`, `MEDIUM` → `max(3, default * 3 // 4)`, else `config.risk.default_leverage`.

**`_generate_scalp_signals` (`:289`).** `repo.get_latest_direction_snapshot(symbol)`; `[]` when absent, stale, direction outside `("LONG","SHORT")`, or `confidence < scalp.min_confidence`. `_snapshot_is_fresh` rejects `parse_db_timestamp(created_at) <= 0` or `time.time() - ts > config.ensemble.max_snapshot_age_seconds (20)`. Emits `{"direction": "BULLISH" if direction=="LONG" else "BEARISH", "strength": confidence, "source": "ensemble", "reason", "ensemble_direction", "ensemble_confidence"}`. `_snapshot_agreement` parses `agent_breakdown` JSON, returns e.g. `"3/4 agree"` counting only LONG/SHORT agents.

**`act` (`:257`).** Skips `HOLD`; builds `Order(symbol, action, side, leverage, stop_loss=None, take_profit=None, reasoning)`, publishes `{"order", "decision", "stop_loss_pct", "take_profit_pct"}`. SL/TP travel as percentages; absolute levels computed downstream.

**State it owns.** `risk_manager`, `config`, `scalp`, `_analysis_queue`, `_position_queue`, `_latest_analyses: Dict[str, Dict]`, `_symbol_cooldowns: Dict[str, float]` (epoch seconds), `_symbol_loss_streak: Dict[str, int]`, `_global_loss_streak: int`.

**Side effects.** DB reads only (`get_open_positions`, `get_latest_direction_snapshot`); `market_store` reads; one event-bus publish per decision. No DB writes.

**Consumers.** `run.py:356`; `tests/test_decision_agent_ensemble.py:24`.

#### 9.1.5 `agents/execution_agent.py`

**Role.** Agent 4. Turns `TradeDecision` events into orders; runs the scalping position lifecycle — breakeven lock, fast TP, expiry close, engine SL/TP/liquidation.

**Key symbols.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `ExecutionAgent` | class | `class ExecutionAgent(BaseAgent):` | `agents/execution_agent.py:26` |
| `.__init__` | method | `def __init__(self, event_bus: EventBus, engine):` | `agents/execution_agent.py:37` |
| `.initialize` | method | `async def initialize(self):` | `agents/execution_agent.py:46` |
| `.sense` | method | `async def sense(self) -> dict:` | `agents/execution_agent.py:52` |
| `.think` | method | `async def think(self, data: dict) -> dict:` | `agents/execution_agent.py:83` |
| `.act` | method | `async def act(self, analysis: dict):` | `agents/execution_agent.py:158` |
| `.check_positions` | method | `async def check_positions(self):` | `agents/execution_agent.py:177` |
| `._protect_breakeven` | method | `async def _protect_breakeven(self):` | `agents/execution_agent.py:194` |
| `._auto_close_expired` | method | `async def _auto_close_expired(self):` | `agents/execution_agent.py:243` |
| `._scalp_take_profit` | method | `async def _scalp_take_profit(self):` | `agents/execution_agent.py:279` |

**Lifecycle order (`check_positions`, `:177`) is load-bearing:** `_protect_breakeven` → `_scalp_take_profit` → `_auto_close_expired` → `engine.check_positions()`. `breakeven_trigger_pct (0.0020)` is deliberately below `min_profit_pct (0.0060)`; if TP fired first, breakeven would be dead code.

**Thresholds.** `_protect_breakeven`: LONG `pnl_pct = (price - entry) / entry`, `be_sl = entry * (1 + offset)`; SHORT `pnl_pct = (entry - price) / entry`, `be_sl = entry * (1 - offset)`; writes `repo.update_position_sl_tp(pos["id"], stop_loss=be_sl)` when `pnl_pct >= trigger (0.0020)` and the stored SL is looser; offset 0.0015. `_scalp_take_profit`: skips positions younger than `min_hold_seconds (15)`, closes via `engine.position_manager.close_position(pos["id"], price, "SCALP_TP")` at `profit_pct >= min_profit_pct (0.0060)`. `_auto_close_expired`: skips positions younger than `min_hold_seconds`, closes with reason `"SCALP_EXPIRED"` past `max_hold_seconds (300)`.

**SL/TP in `think` (`:117-140`).** Lazy `from analysis import volatility as vol_mod`, then `get_dynamic_tp_sl_thresholds(order.symbol, self.config)`; when `used_dynamic`, `sl_pct = max(thresholds["sl_pct"], scalp.tight_sl_pct)` and `tp_pct = max(thresholds["tp_pct"], scalp.fast_tp_pct)`, else the payload's percentages. Absolute levels from `risk_manager.calculate_stop_loss(price, side, sl_pct)` / `calculate_take_profit(price, side, tp_pct)` — advisory only; `PaperTradingEngine._execute_open` recomputes from the fill price and overwrites.

**State it owns.** `engine` (paper or live, duck-typed), `risk_manager: RiskManager` (built internally), `config`, `scalp`, `_decision_queue` (`TRADE_DECISION`), `_price_queue` (`PRICE_UPDATE`).

**Side effects.** `engine.update_price(symbol, price)` per `market_store` price each cycle; DB `get_open_positions`, `update_position_sl_tp`; engine `execute_order`, `check_positions`, `position_manager.close_position`. No direct network calls.

**Consumers.** `run.py:368`; driven from `run.py:_execution_loop` at 0.3 s calling `run_cycle()` then `check_positions()` (`run.py:595`); `tests/test_live_executor.py:222`.

#### 9.1.6 `agents/direction_agents.py`

**Role.** A `DirectionAgent` base, four read-only specialist voters, and `DirectionEnsembleAgent` — sole writer of `direction_snapshots`. Specialists are never scheduled; the ensemble calls them in-process.

**Base class and helpers.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `DirectionAgent` | class | `class DirectionAgent(BaseAgent):` | `agents/direction_agents.py:42` |
| `agent_name` | class attr | `agent_name = "direction"` | `agents/direction_agents.py:51` |
| `.__init__` | method | `def __init__(self, event_bus):` | `agents/direction_agents.py:53` |
| `.initialize` | method | `async def initialize(self):` | `agents/direction_agents.py:58` |
| `.evaluate` | method | `def evaluate(self, symbol: str) -> dict:` | `agents/direction_agents.py:62` |
| `._evaluate` | method | `def _evaluate(self, symbol: str):` | `agents/direction_agents.py:87` |
| `.sense` | method | `async def sense(self) -> dict:` | `agents/direction_agents.py:93` |
| `.think` | method | `async def think(self, data: dict) -> dict:` | `agents/direction_agents.py:96` |
| `.act` | method | `async def act(self, analysis: dict):` | `agents/direction_agents.py:99` |
| `_safe_float` | function | `def _safe_float(value) -> Optional[float]:` | `agents/direction_agents.py:320` |
| `_load_closes` | function | `def _load_closes(db_path: str, symbol: str, limit: int = 100) -> List[dict]:` | `agents/direction_agents.py:331` |
| `_signed_volume` | function | `def _signed_volume(tape: list):` | `agents/direction_agents.py:367` |

`evaluate` wraps `_evaluate` in try/except: exception → `abstain(agent_name, symbol, f"error: {exc}")`; `None` → `abstain(..., "data tidak cukup")`; 4-tuple → `make_verdict(...)`. One failing agent never sinks the ensemble. The three BaseAgent phases return `{}`/`{}`/`None` — formality only.

**Specialists.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `OrderFlowAgent` | class | `class OrderFlowAgent(DirectionAgent):` — `agent_name = "orderflow"` | `agents/direction_agents.py:103` (`:112`) |
| `OrderFlowAgent._evaluate` | method | `def _evaluate(self, symbol: str):` | `agents/direction_agents.py:114` |
| `MomentumAgent` | class | `class MomentumAgent(DirectionAgent):` — `agent_name = "momentum"` | `agents/direction_agents.py:148` (`:156`) |
| `MomentumAgent._evaluate` | method | `def _evaluate(self, symbol: str):` | `agents/direction_agents.py:158` |
| `TechnicalAgent` | class | `class TechnicalAgent(DirectionAgent):` — `agent_name = "technical"` | `agents/direction_agents.py:184` (`:193`) |
| `TechnicalAgent.__init__` | method | `def __init__(self, event_bus, db_path: str):` | `agents/direction_agents.py:195` |
| `TechnicalAgent.evaluate_async` | method | `async def evaluate_async(self, symbol: str) -> dict:` | `agents/direction_agents.py:200` |
| `TechnicalAgent._evaluate` | method | `def _evaluate(self, symbol: str):` | `agents/direction_agents.py:215` |
| `MicrostructureAgent` | class | `class MicrostructureAgent(DirectionAgent):` — `agent_name = "microstructure"` | `agents/direction_agents.py:256` (`:264`) |
| `MicrostructureAgent._evaluate` | method | `def _evaluate(self, symbol: str):` | `agents/direction_agents.py:266` |

**Voter procedures, verbatim.**

*OrderFlow (`:114`)* — no book → `None`. `ofi, rel_spread = calculate_order_flow_imbalance(book, symbol=symbol)`; both `0.0` → `None` (abstain, not NEUTRAL). `abs(ofi) < 1e-6` → `(NEUTRAL, 0.0, ...)`. Else `confidence = abs(float(ofi))`, `DIRECTION_LONG if ofi > 0 else DIRECTION_SHORT`, label `"bid"`/`"ask"`.

*Momentum (`:158`)* — `window = ensemble.momentum_window_seconds (30.0)`; `market_store.get_price_change(symbol, seconds=window)`; `None` → abstain; `abs(change) < 1e-9` → `(NEUTRAL, 0.0, ...)`. `threshold = max(config.scalping.momentum_threshold (0.0008), 1e-6)`; `confidence = min(abs(change) / (threshold * 2.0), 1.0)` — 2× the threshold maps to confidence 1.0.

*Technical (`:215`)* — `_load_closes(db_path, symbol, limit=100)`; `< 30` closes → abstain. Runs `TechnicalAnalyzer.calculate_indicators`, extracts `("rsi", "rsi_fast", "macd_hist", "atr", "ema_short", "ema_long", "ema_3", "ema_5", "bb_lower", "bb_upper")`, then `z = float(calculate_technical_zscore(indicators, price))`. `abs(z) < 1e-6` → NEUTRAL; else `confidence = min(abs(z) / 2.0, 1.0)`, direction from sign of `z`. `evaluate_async` (`:200`) runs `self.evaluate` through `asyncio.to_thread` so pandas/NumPy cannot block the event loop.

*Microstructure (`:266`)* — inputs accumulate into `z_components`: (1) signed-volume imbalance from `market_store.get_recent_trades`, gated on `len(tape) >= ensemble.min_trades_for_microstructure (8)`, `imbalance = signed_vol / total_vol` in `-1..1`, contributed as `imbalance * 1.5`; (2) funding, contrarian, `z_funding = float(np.clip(-funding / 0.0001, -2.0, 2.0))` — 0.01%/hour maps to |z| = 1, and crowded longs pay positive funding, so high funding pushes z negative; (3) open interest recorded as context, never a vote. `z = float(np.mean(z_components))`; `abs(z) < 1e-6` → NEUTRAL; `confidence = min(abs(z) / 2.0, 1.0)`. `_signed_volume` skips trades whose `side` is neither `B`- nor `A`-prefixed entirely, not just in the numerator — otherwise a blind agent looks like a confidently neutral one.

**Ensemble coordinator.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `DirectionEnsembleAgent` | class | `class DirectionEnsembleAgent(BaseAgent):` | `agents/direction_agents.py:401` |
| `.__init__` | method | `def __init__(self, event_bus, symbols: List[str], db_path: str):` | `agents/direction_agents.py:410` |
| `.initialize` | method | `async def initialize(self):` | `agents/direction_agents.py:418` |
| `.run_cycle` | method | `async def run_cycle(self):` | `agents/direction_agents.py:437` |
| `.sense` | method | `async def sense(self) -> dict:` | `agents/direction_agents.py:455` |
| `.think` | method | `async def think(self, data: dict) -> dict:` | `agents/direction_agents.py:458` |
| `.act` | method | `async def act(self, analysis: dict):` | `agents/direction_agents.py:461` |
| `._evaluate_symbol` | method | `async def _evaluate_symbol(self, symbol: str) -> Optional[dict]:` | `agents/direction_agents.py:464` |
| `._realized_vol` | method | `async def _realized_vol(self, symbol: str) -> Optional[float]:` | `agents/direction_agents.py:505` |

`initialize` builds specialists only for names whose `ensemble.base_weight(name) > 0`, iterating `("orderflow", "momentum", "technical", "microstructure")`. `run_cycle` **overrides** the base sense/think/act loop: `_evaluate_symbol` per symbol, each non-`None` snapshot written via `repo.insert_direction_snapshot(...)` inside one try/except.

`_evaluate_symbol` (`:464`) — dispatch on `isinstance(spec, TechnicalAgent)`: that agent → `await spec.evaluate_async(symbol)`, all others `spec.evaluate(symbol)` synchronously (they only read in-memory `market_store`). Then `result = aggregate(verdicts, self.ensemble)`. Then optional diffusion: `_realized_vol(symbol)` (1m log-return sigma, needs `ensemble.min_returns_for_vol (20) + 1` closes from `asyncio.to_thread(_load_closes, db_path, symbol, 40)`, floored at `max(..., 0.0002)`) → `probability_engine.compute_directional_curve(prob_long, realized_vol_per_min=vol, horizon_minutes=ensemble.diffusion_horizon_minutes (30), num_points=ensemble.diffusion_points (60))`. Snapshot: `symbol, prob_long, prob_short, direction, confidence, z_composite, agent_breakdown` (JSON), `diffusion` (JSON or `None`).

**State it owns.** `config`, `ensemble`, `symbols: List[str]`, `db_path: str`, `specialists: List[DirectionAgent]`. `TechnicalAgent` holds `technical: TechnicalAnalyzer` and `db_path`, opening its own sync `sqlite3` connection per call — separate from the shared `aiosqlite` connection, since it runs in a thread executor.

**Side effects.** DB `repo.insert_direction_snapshot(snapshot)` — the only writer of that table. Reads 1m candles over the private sync connection; `_load_closes` swallows `sqlite3.Error` and returns `[]`.

**Consumers.** `run.py:375` (only when `config.ensemble.enabled`), scheduled as `direction_ensemble` at `ensemble.interval_seconds (5)` — `run.py:536-539`. Its table is the sole input to `DecisionAgent._generate_scalp_signals` and to the HUD. Tests: `tests/test_direction_agents.py:11`, `tests/test_decision_agent_ensemble.py:24`.

#### 9.1.7 `analysis/technical.py`

**Role.** pandas-ta indicator computation and rule-based signal scoring on an OHLCV DataFrame.

**Key symbols.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `TechnicalAnalyzer` | class | `class TechnicalAnalyzer:` | `analysis/technical.py:17` |
| `.__init__` | method | `def __init__(self):` | `analysis/technical.py:30` |
| `.calculate_indicators` | method | `def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:` | `analysis/technical.py:41` |
| `.generate_signals` | method | `def generate_signals(self, df: pd.DataFrame) -> Dict:` | `analysis/technical.py:94` |
| `.ohlcv_to_dataframe` | staticmethod | `def ohlcv_to_dataframe(ohlcv_data: List[list]) -> pd.DataFrame:` | `analysis/technical.py:302` |

**State it owns.** Read-only snapshot of `get_config().indicators`: `rsi_period` (14), `macd_fast` (12), `macd_slow` (26), `macd_signal` (9), `bb_period` (20), `bb_std` (2), `ema_short` (9), `ema_long` (21).

**Indicators (`:41`).** Returns unchanged, with a warning, when `len(df) < macd_slow + macd_signal` (35). Adds `rsi`, `rsi_fast` (7), `macd`/`macd_hist`/`macd_signal`, `bb_lower`/`bb_mid`/`bb_upper`, `ema_short`/`ema_long`, `ema_3`/`ema_5`, `roc_5`, `vol_sma` (20), `atr` (14).

**Scoring weights (`:94`).** Early return `{"direction": "NEUTRAL", "confidence": 0.0, "signals": {}}` when `len(df) < 2` or `"rsi" not in df.columns`. Each rule increments `total_signals`:

| Rule | Condition | Weight | Loc |
|---|---|---|---|
| RSI(14) | `< 30` bull, `> 70` bear, `< 45` weak bull, `> 55` weak bear | ±1.0 / ±0.5 | `:125-138` |
| RSI(7) | `< 25` bull, `> 75` bear, `< 40` weak bull, `> 60` weak bear | ±1.5 / ±0.3 | `:144-157` |
| MACD | hist `> 0` & prev `<= 0` bull crossover; `< 0` & prev `>= 0` bear crossover; else by sign | ±1.5 / ±0.5 | `:167-178` |
| Bollinger | `close <= bb_lower` bull, `close >= bb_upper` bear, else `WITHIN_BANDS` (position recorded, 0 weight) | ±1.0 | `:188-196` |
| EMA 9/21 | golden cross bull, death cross bear, else above/below | ±1.5 / ±0.5 | `:206-217` |
| EMA 3/5 | fast golden bull, fast death bear, else above/below | ±1.0 / ±0.3 | `:227-238` |
| ROC(5) | `> 0.5`/`> 0.1` bull, `< -0.5`/`< -0.1` bear | ±1.0 / ±0.3 | `:244-257` |
| Volume | ratio `> 1.5` HIGH, `< 0.5` LOW, else NORMAL | 0 (informational) | `:263-271` |

`net = bullish_count - bearish_count`; `max_score = max(total_signals * 1.5, 1)`; `net > 0.5` → BULLISH, `net < -0.5` → BEARISH, else NEUTRAL with `confidence = 0.0`; `confidence = min(abs(net) / max_score, 1.0)`.

**Side effects.** None — pure CPU; `calculate_indicators` adds columns to the DataFrame handed to it.

**Consumers.** `agents/analysis_agent.py:17,100,103`; `agents/direction_agents.py:37,223`; `dashboard/callbacks/update_callbacks.py:26`; `tests/test_indicators.py:9`.

#### 9.1.8 `analysis/fundamental.py`

**Role.** Collapses macro context, news sentiment and the economic calendar into one directional verdict plus a risk level.

**Key symbols.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `FundamentalAnalyzer` | class | `class FundamentalAnalyzer:` | `analysis/fundamental.py:12` |
| `.__init__` | method | `def __init__(self):` | `analysis/fundamental.py:18` |
| `.update_macro` | method | `def update_macro(self, macro_context: Dict):` | `analysis/fundamental.py:23` |
| `.update_sentiment` | method | `def update_sentiment(self, sentiment_aggregate: Dict):` | `analysis/fundamental.py:27` |
| `.update_calendar` | method | `def update_calendar(self, events: List[dict]):` | `analysis/fundamental.py:31` |
| `.analyze` | method | `def analyze(self) -> Dict:` | `analysis/fundamental.py:35` |
| `.should_reduce_risk` | method | `def should_reduce_risk(self) -> bool:` | `analysis/fundamental.py:135` |
| `.get_suggested_leverage` | method | `def get_suggested_leverage(self, default: int = 5) -> int:` | `analysis/fundamental.py:143` |

**State it owns.** `_macro_context: Dict = {}`, `_sentiment_aggregate: Dict = {}`, `_calendar_events: List[dict] = []`, each replaced wholesale by its setter.

**Decision procedure (`analyze`, `:35`).**
- Macro: `bias == "BULLISH"` → bull +2; `"BEARISH"` → bear +2; `risk_level = macro.get("risk_level", "MEDIUM")`, or `"MEDIUM"` with no context.
- Sentiment (only when `count > 0`): `avg_score > 0.2` → bull +2; `> 0.05` → bull +1; `< -0.2` → bear +2; `< -0.05` → bear +1; else a neutral factor only.
- Calendar: `>= 3` events with `impact == "High"` → bear +1, `risk_level = "HIGH"`.
- `net = bullish_score - bearish_score`: `>= 2` → BULLISH, `confidence = min(net / 6, 1.0)`; `<= -2` → BEARISH, `confidence = min(abs(net) / 6, 1.0)`; else NEUTRAL with hardcoded `confidence = 0.2`.

`should_reduce_risk` (`:135`) re-runs `analyze()`: `risk_level == "HIGH"` or (`direction == "BEARISH"` and `confidence > 0.5`). `get_suggested_leverage(default=5)`: HIGH → `max(1, default // 3)`; MEDIUM → `max(1, default // 2)`; else `default`.

**Side effects.** None — pure, no I/O.

**Consumers.** `agents/analysis_agent.py:18,49` only. `DecisionAgent._determine_leverage` reads the `risk_level` it emits (`agents/decision_agent.py:224`).

#### 9.1.9 `analysis/ml_signals.py`

**Role.** RandomForest direction classifier over seven technical/sentiment features, rule-based fallback when no trained `.pkl` exists.

**Key symbols.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `MLSignalGenerator` | class | `class MLSignalGenerator:` | `analysis/ml_signals.py:23` |
| `MODEL_DIR` | module const | `MODEL_DIR = Path("ml/models")` | `analysis/ml_signals.py:20` |
| `.__init__` | method | `def __init__(self):` | `analysis/ml_signals.py:35` |
| `.initialize` | method | `async def initialize(self):` | `analysis/ml_signals.py:43` |
| `._load_model` | method | `def _load_model(self):` | `analysis/ml_signals.py:52` |
| `.extract_features` | method | `def extract_features(self, technical_signals: Dict, sentiment_score: float = 0.0, df: pd.DataFrame = None) -> Optional[np.ndarray]:` | `analysis/ml_signals.py:63` |
| `.predict` | method | `def predict(self, technical_signals: Dict, sentiment_score: float = 0.0, df: pd.DataFrame = None) -> Dict:` | `analysis/ml_signals.py:137` |
| `._predict_ml` | method | `def _predict_ml(self, features: np.ndarray) -> Dict:` | `analysis/ml_signals.py:169` |
| `._predict_rule_based` | method | `def _predict_rule_based(self, features: np.ndarray) -> Dict:` | `analysis/ml_signals.py:189` |

**State it owns.** `_model = None`, `_feature_names = ["rsi", "macd_hist", "bb_position", "ema_trend", "volume_ratio", "sentiment_score", "atr_pct"]`, `_model_loaded: bool = False`.

**Feature extraction (`:63`).** `rsi = value / 100.0` (default 0.5); `macd_hist_norm = np.clip(macd_hist / 100, -1, 1)` when `abs(macd_hist) > 0` else 0; `bb_position` parsed from the string, falling back to `0.0` (`LOWER_BAND`), `1.0` (`UPPER_BAND`), else `0.5`; `ema_trend` = `1.0` bull-crossover / `0.5` bull / `-1.0` bear-crossover / `-0.5` bear / `0.0` neutral; `volume_ratio = min(float(vol_val) / 3.0, 1.0)`; `sent_norm = np.clip(sentiment_score, -1, 1)`; `atr_pct = min(atr / close, 0.1) * 10`, else `0.5`.

**Rule-based fallback (`:189`).** `score`: RSI `< 0.3` +1.5, `> 0.7` −1.5, `< 0.45` +0.5, `> 0.55` −0.5; `score += macd * 2.0`; BB `< 0.2` +1.0, `> 0.8` −1.0; `score += ema * 1.5`; `score += sent * 1.0` (vol and ATR unused). `sigmoid(x) = 1/(1+exp(-x))`; `p_long = sigmoid(score)`, `p_short = sigmoid(-score)`, `p_hold = 1 - abs(p_long - p_short)`, renormalised to sum 1. At `threshold = 0.45`: `LONG` if `p_long > 0.45 and p_long > p_short`, `SHORT` if `p_short > 0.45 and p_short > p_long`, else `HOLD` with `confidence = p_hold`. Returns `method: "RULE_BASED"`; `_predict_ml` returns `method: "ML"`, `confidence = float(np.max(proba))`.

**Side effects.** Filesystem read of `ml/models/signal_model.pkl` via `joblib.load`, on the default executor. No writes, no network, no DB.

**Consumers.** `agents/analysis_agent.py:19,50`; `ml/predictor.py:11`.

#### 9.1.10 `analysis/direction_ensemble.py`

**Role.** Pure aggregation library (no I/O) merging specialist verdicts into one LONG/SHORT probability via log-odds pooling with shrinkage and an agreement bonus.

**Key symbols.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `MAX_AGENT_Z` | module const | `MAX_AGENT_Z = 2.5` | `analysis/direction_ensemble.py:32` |
| direction constants | module const | `DIRECTION_LONG = "LONG"` / `DIRECTION_SHORT = "SHORT"` / `DIRECTION_NEUTRAL = "NEUTRAL"` | `:34` / `:35` / `:36` |
| `_sigmoid` | function | `def _sigmoid(z: float) -> float:` | `analysis/direction_ensemble.py:39` |
| `_normalize_z` | function | `def _normalize_z(direction: str, confidence: float) -> float:` | `analysis/direction_ensemble.py:47` |
| `agreement_score` | function | `def agreement_score(verdict: dict, peers: List[dict]) -> float:` | `analysis/direction_ensemble.py:63` |
| `aggregate` | function | `def aggregate(verdicts: List[dict], cfg) -> dict:` | `analysis/direction_ensemble.py:83` |
| `make_verdict` | function | `def make_verdict(agent: str, symbol: str, direction: str, confidence: float, reasoning: str = "", factors: Optional[dict] = None, abstained: bool = False) -> dict:` | `analysis/direction_ensemble.py:194` |
| `abstain` | function | `def abstain(agent: str, symbol: str, reason: str = "tidak ada data") -> dict:` | `analysis/direction_ensemble.py:218` |

**Decision procedure (`aggregate`, `:83`).** 1. Split `abstained=True` verdicts from active ones — abstain means "no data", never "neutral data". 2. Stamp each active verdict with `_z = _normalize_z(direction, confidence)`, `magnitude = MAX_AGENT_Z * clamp(confidence, 0, 1)`, NEUTRAL → `0.0`. 3. No active verdicts → `prob_long = prob_short = 0.5`, NEUTRAL, `confidence = 0.0`, empty breakdown. 4. Weighting loop: `base = float(cfg.base_weight(agent))`; `base <= 0` marks the verdict `abstained = True` and skips it; else `bonus = 1.0 + cfg.agreement_bonus (0.5) * agreement_score(v, active)`, `weight = base * bonus`, `numerator += weight * v["_z"]`, `denominator += weight`. `agreement_score` counts only peers with nonzero `z`, matching `(z_p > 0) == (verdict["_z"] > 0)`, returning `same / len(others)`. 5. `denominator <= 0` → neutral with the breakdown populated. 6. `z = numerator / denominator`; `z_shrunk = z * cfg.shrinkage_delta (0.85)`; `prob_long = _sigmoid(z_shrunk)` clamped to `[cfg.min_prob, 1 - cfg.min_prob]` (0.02 / 0.98); `prob_short = 1 - prob_long`; `confidence = abs(prob_long - 0.5) * 2.0`; direction from sign of `prob_long - 0.5`. 7. Returns `prob_long, prob_short, direction, confidence, z_composite, z_shrunk, agent_breakdown, n_agents, n_abstained`.

Default `EnsembleConfig` weights: orderflow 0.30, momentum 0.25, technical 0.25, microstructure 0.20.

**Side effects.** None — pure functions.

**Consumers.** `agents/direction_agents.py:29-36`; `check_behavior.py:116`; `tests/test_direction_ensemble.py:12`; `tests/test_numerics.py:12`.

#### 9.1.11 `analysis/probability_engine.py`

**Role.** Bayesian multi-factor logit: five standardized z-scores weighted into one composite, sigmoid-squashed into a directional probability, then projected forward into a drift-diffusion curve and an expectancy edge.

**Key symbols.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `norm_cdf` | function | `def norm_cdf(x: float) -> float:` | `analysis/probability_engine.py:35` |
| `calculate_order_flow_imbalance` | function | `def calculate_order_flow_imbalance(order_book: Optional[Dict], symbol: str = None) -> Tuple[float, float]:` | `analysis/probability_engine.py:40` |
| `calculate_technical_zscore` | function | `def calculate_technical_zscore(indicators: Dict, current_price: float) -> float:` | `analysis/probability_engine.py:93` |
| `calculate_realized_volatility` | function | `def calculate_realized_volatility(df: pd.DataFrame, window: int = 30) -> float:` | `analysis/probability_engine.py:139` |
| `QuantitativeProbabilityEngine` | class | `class QuantitativeProbabilityEngine:` | `analysis/probability_engine.py:155` |
| `.weights` | instance attr | `{"technical": 0.30, "sentiment": 0.20, "ml": 0.25, "orderbook": 0.15, "macro": 0.10}` | `analysis/probability_engine.py:163` |
| `.compute_composite_probability` | method | `def compute_composite_probability(self, indicators: Dict, current_price: float, sentiment_score: float = 0.0, ml_prediction: Optional[Dict] = None, order_book: Optional[Dict] = None, macro_bias: str = "NEUTRAL") -> Dict:` | `analysis/probability_engine.py:171` |
| `.compute_directional_curve` | method | `def compute_directional_curve(self, prob_long: float, realized_vol_per_min: float, horizon_minutes: int = 30, num_points: int = 60) -> Dict:` | `analysis/probability_engine.py:270` |
| `.compute_diffusion_curve` | method | `def compute_diffusion_curve(self, prob_bullish: float, realized_vol_per_min: float, horizon_minutes: int = 60, num_points: int = 80) -> Dict:` | `analysis/probability_engine.py:326` |
| `.compute_mathematical_edge` | method | `def compute_mathematical_edge(self, trades: List[Dict], current_winner_odds: float = 0.55, target_rr_ratio: float = 1.5) -> float:` | `analysis/probability_engine.py:386` |
| `probability_engine` | singleton | `probability_engine = QuantitativeProbabilityEngine()` | `analysis/probability_engine.py:433` |

**`calculate_technical_zscore` (`:93`).** Four sub-scores, each clipped to `[-2.0, 2.0]`, averaged; empty `indicators` → `0.0`. 1. `z_rsi = (rsi - 50.0) / 20.0` (70 → +1.0, 30 → −1.0). 2. `denom = atr if atr and not isnan(atr) and atr > 0 else max(current_price * 0.002, 1e-4)`; `z_macd = (macd_hist / denom) * 1.5`. 3. `z_ema = ((ema_short - ema_long) / ema_long) * 100.0`. 4. `z_bb = (((current_price - bb_lower) / (bb_upper - bb_lower)) - 0.5) * 2.5`.

**`calculate_order_flow_imbalance` (`:40`) — facade, not implementation.** Returns `(0.0, 0.0)` when the book lacks `bids`/`asks` or a side is empty — absent data, never a fabricated spread. `symbol is None`: throwaway `microstructure.PythonKernel()`, `ingest_l2("__ad_hoc__", bids, asks)`, `order_flow_imbalance("__ad_hoc__", depth=5)`. Symbol supplied: module-level `microstructure.ingest_l2(symbol, bids, asks)` / `order_flow_imbalance(symbol, depth=5)`, caching the parsed book in the kernel.

**`calculate_realized_volatility` (`:139`).** `df.empty or len(df) < 5 or "close" not in df.columns` → `0.0015`; fewer than 3 log returns → `0.0015`; else `max(float(np.log(closes/closes.shift(1)).dropna().std(ddof=1)), 0.0002)` over the last `window` (30) closes.

**`compute_composite_probability` (`:171`).** 1. `z_tech = calculate_technical_zscore(...)`. 2. `z_sent = np.clip(sentiment_score * 2.2, -2.5, 2.5)`. 3. `z_ml`: with `probabilities` carrying LONG and SHORT, `math.log(max(p_long, 1e-4) / max(p_short, 1e-4))`; else token-match the action (`BULL`/`BUY`/`LONG` → `conf * 2.0`, `BEAR`/`SELL`/`SHORT` → `-conf * 2.0`); clipped to `[-2.5, 2.5]`. 4. `z_ob = float(np.clip(ofi * 2.5, -2.5, 2.5))`. 5. `z_fund`: BULLISH `0.8`, BEARISH `-0.8`, else `0.0`. 6. `z_comp = 0.30*z_tech + 0.20*z_sent + 0.25*z_ml + 0.15*z_ob + 0.10*z_fund`. 7. `prob_bullish = 1.0 / (1.0 + math.exp(-z_comp))`, `prob_bearish = 1 - prob_bullish`, `direction = "BULLISH" if prob_bullish >= 0.5 else "BEARISH"`. Returns `prob_bullish, prob_bearish, winner_odds, loser_odds, direction, z_composite, factor_zscores, order_flow_imbalance, relative_spread`.

**`compute_directional_curve` (`:270`) — the live path.** `sigma = max(realized_vol_per_min, 0.0003)`; `p_clamped = clip(prob_long, 0.001, 0.999)`; `z_score = log(p/(1-p))`; **`drift_per_min = z_score * 0.45 * sigma`, signed not absolute**, so a bearish read yields a falling curve and `prob_long` may fall below 0.5. `t_steps = np.linspace(0.2, horizon_minutes, num_points)`; per step `d2 = ((drift_per_min - 0.5 * sigma**2) * t) / (sigma * sqrt(t))`, `p_terminal = norm_cdf(d2)`, `weight_t = 1.0 - math.exp(-t * 0.08)`, `p_long = (1 - weight_t) * p_clamped + weight_t * p_terminal` clamped to `[0.001, 0.999]` — never to `>= 0.5`, which is what lets SHORT be visible.

**`compute_diffusion_curve` (`:326`) — DEPRECATED.** Uses `effective_mu = abs(drift_per_min)` and clamps to `[0.50, 0.999]`, so a bearish input renders identically to a bullish one and SHORT can never appear. Kept as a shim for old call sites.

**`compute_mathematical_edge` (`:386`).** With `>= 2` trades carrying non-`None` `pnl`: `win_rate = len(wins)/n`, `loss_rate = len(losses)/n`, `avg_win`, `avg_loss`, `avg_notional = max(mean(price * quantity), 1.0)`; returns `clip(((win_rate*avg_win - loss_rate*avg_loss) / avg_notional) * 100.0, -10.0, 30.0)`. Otherwise `p_win = clip(current_winner_odds, 0.50, 0.95)`, `edge = p_win * target_rr_ratio - (1 - p_win)`, returns `clip(edge * 1.25, 0.10, 5.0)`.

**State it owns.** Only `weights`. The `probability_engine` singleton is shared process-wide.

**Side effects.** None, except `calculate_order_flow_imbalance` mutating the `core.microstructure` kernel cache when `symbol` is supplied; `compute_composite_probability` calls it without a symbol (`:223`), so that path is non-caching.

**Consumers.** `agents/direction_agents.py:115,216,483`; `dashboard/callbacks/update_callbacks.py:20-24`; `dashboard/layouts/hud_figures.py:223`; `tests/test_probability_engine.py:8`, `tests/test_bugfixes.py:944`.

#### 9.1.12 `analysis/volatility.py`

**Role.** Converts static percentage SL/TP into ATR-adaptive targets and adds a post-hoc economic gate. Pure, synchronous; only state is a module-level ATR cache and a registered synchronous candle source.

**Key symbols.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `realized_volatility` | function | `def realized_volatility(symbol: str, window_seconds: float = 30.0) -> Optional[float]:` | `analysis/volatility.py:37` |
| `realized_vol_pct` | function | `def realized_vol_pct(symbol: str, window_seconds: float = 30.0) -> Optional[float]:` | `analysis/volatility.py:70` |
| `atr_1m_from_candles` | function | `def atr_1m_from_candles(candles: List[dict], period: int = 14) -> Optional[Dict[str, float]]:` | `analysis/volatility.py:91` |
| `_ATR_CACHE` | module global | `_ATR_CACHE: Dict[Tuple[str, int], Dict[str, float]] = {}` | `analysis/volatility.py:152` |
| `_ATR_CACHE_BUCKET_SECONDS` | module global | `_ATR_CACHE_BUCKET_SECONDS = 5` | `analysis/volatility.py:153` |
| `_ATR_CACHE_MAX_ENTRIES` | module global | `_ATR_CACHE_MAX_ENTRIES = 256` | `analysis/volatility.py:154` |
| `_cache_key` | function | `def _cache_key(symbol: str) -> Tuple[str, int]:` | `analysis/volatility.py:157` |
| `atr_1m_pct` | function | `def atr_1m_pct(symbol: str, candles: Optional[List[dict]] = None, period: int = 14) -> Optional[float]:` | `analysis/volatility.py:163` |
| `clear_volatility_cache` | function | `def clear_volatility_cache() -> None:` | `analysis/volatility.py:203` |
| `_CANDLE_SOURCE` | module global | `_CANDLE_SOURCE = None` | `analysis/volatility.py:222` |
| `set_candle_source` | function | `def set_candle_source(fn) -> None:` | `analysis/volatility.py:225` |
| `clear_candle_source` | function | `def clear_candle_source() -> None:` | `analysis/volatility.py:243` |
| `_load_candles` | function | `def _load_candles(symbol: str, limit: int) -> Optional[List[dict]]:` | `analysis/volatility.py:249` |
| `get_dynamic_tp_sl_thresholds` | function | `def get_dynamic_tp_sl_thresholds(symbol: str, cfg=None) -> Dict[str, Optional[float]]:` | `analysis/volatility.py:266` |
| `assess_volatility_gate` | function | `def assess_volatility_gate(symbol: str, sl_pct: float, tp_pct: float, cfg=None) -> Optional[str]:` | `analysis/volatility.py:347` |

**ATR (`:91`).** Needs `len(candles) >= period + 1`; sorts ascending by `timestamp` (callers pass DESC from SQL). `TR = max(high - low, abs(high - prev_close), abs(low - prev_close))`, skipping rows where any of `high`/`low`/`prev_close <= 0`; needs `len(trs) >= period`. **Wilder smoothing** (`atr = sum(trs[:period]) / period`, then `atr = (atr * (period - 1) + tr) / period`) so it matches `ta.atr` at `analysis/technical.py:90`. Returns `{"atr", "atr_pct": atr / close, "close", "samples"}`. `atr_1m_pct` loads `period + 1` candles (`:183`) — asking for exactly `period` yields `len(trs) = period - 1`, silently disabling dynamic targets in production while tests injecting `candles=` still pass. Cache key `(symbol, int(time.time() // 5))`; clears at 256 entries.

**`get_dynamic_tp_sl_thresholds` (`:266`).** 1. Static defaults `tight_sl_pct (0.0025)` / `fast_tp_pct (0.0060)`, returned with `used_dynamic: False`, `reason: "statis (ATR belum tersedia)"`. 2. `dynamic_tp_sl.enabled` false → `reason = "dinamis dimatikan di config"`. 3. `atr = atr_1m_pct(symbol, period=dyn.atr_period (14))`, `realized_vol = realized_vol_pct(symbol, window_seconds=dyn.realized_window_seconds (30.0))`; `atr is None or atr <= 0` → static. 4. `raw_sl = dyn.atr_multiple (1.5) * atr`; `sl_pct = min(max(raw_sl, dyn.min_sl_pct (0.0025)), dyn.max_sl_pct (0.0150))`; `tp_pct = max(sl_pct * dyn.min_risk_reward (1.5), scalp.min_profit_pct (0.0060))`. 5. Adds `used_dynamic: True`, `raw_sl_pct`, `clamped` (`sl_pct != raw_sl`), and a reason string.

**`assess_volatility_gate` (`:347`).** Rejection string or `None`. 1. `roundtrip = cfg.fees.taker (0.0005) * 2 (0.0010)`; `tp_pct <= roundtrip` → reject (TP cannot cover fees, so every trade loses regardless of direction). 2. `net_tp = tp_pct - roundtrip`, `net_sl = sl_pct + roundtrip`; `net_sl <= 0` → reject `"volatilitas gate: SL bersih tidak positif"`. 3. When `net_tp < net_sl`, `breakeven_wr = net_sl / (net_tp + net_sl)`; reject when above `dyn.max_breakeven_win_rate (0.65)`. 4. Else `None`.

**State it owns.** `_ATR_CACHE` and `_CANDLE_SOURCE`, both module globals. `set_candle_source` **always** clears the cache — cached values came from the previous source.

**Side effects.** Reads `market_store.get_price_history` / `get_price`; mutates the two globals. Never touches the event loop; no file or DB writes.

**Consumers.** `trading/paper_engine.py:112,131,295,301,325`; `agents/execution_agent.py:117,119`; `check_behavior.py:104,147`; `tests/test_advanced_modules.py:53`, `tests/test_numerics.py:13`.

#### 9.1.13 `analysis/vol_target.py`

**Role.** Volatility-targeting position sizing and a regime gate, scaling daily tick volatility against a fixed 3%/day reference.

**Key symbols.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `REFERENCE_DAILY_VOL` | module const | `REFERENCE_DAILY_VOL = 0.03  # 3% per hari` | `analysis/vol_target.py:37` |
| `daily_vol` | function | `def daily_vol(symbol: str, window_seconds: float = 300.0) -> Optional[float]:` | `analysis/vol_target.py:40` |
| `vol_ratio` | function | `def vol_ratio(symbol: str) -> Optional[float]:` | `analysis/vol_target.py:68` |
| `target_risk_fraction` | function | `def target_risk_fraction(symbol: str, base_risk: float = None, max_ratio: float = 3.0) -> Optional[float]:` | `analysis/vol_target.py:80` |
| `should_trade` | function | `def should_trade(symbol: str, min_vol_ratio: float = 0.5, max_vol_ratio: float = 3.0) -> Tuple[bool, str]:` | `analysis/vol_target.py:110` |

**Procedures.** `daily_vol` (`:40`) needs `>= 20` prices in the 300 s window and `>= 15` log returns, else `None` (unknown, not calm); sample variance uses `n - 1`; annualises with hardcoded `ticks_per_day = 86400.0 / 0.3`. `vol_ratio` = `daily_vol / 0.03`. `target_risk_fraction` (`:80`) defaults `base_risk` to `cfg.risk.max_risk_per_trade (0.02)`, clamps the ratio into `[0.2, max_ratio]`, returns `base_risk / ratio` — an unbounded ratio would drive size to zero, i.e. a stopped bot. `should_trade` (`:110`) returns `(False, reason)` when the ratio is `None`, `< 0.5` (too low — fees become disproportionate), or `> 3.0` (too high — noise dominates, stops get swept); else `(True, "regime oke ({:.2f}x normal)")`.

**State it owns.** None — module constant only; `get_config()` read inside `target_risk_fraction`.

**Side effects.** None — reads `market_store.get_price_history` only.

**Consumers.** **None.** No importer anywhere in the repository; defined but not wired into any agent, engine or dashboard callback.

#### 9.1.14 `analysis/backtester.py`

**Role.** L2 event-driven backtester with an explicit queue-position fill model and book-depth market impact. **Production never calls it** — `run.py` does not import it; the only first-party importers are `tests/test_advanced_modules.py:371,434,447,654`.

**Data model.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `L2Snapshot` | dataclass | `@dataclass class L2Snapshot:` — `timestamp: float`, `symbol: str`, `bids: List[Tuple[float, float]]`, `asks: List[Tuple[float, float]]` | `analysis/backtester.py:48` |
| `.best_bid` | method | `def best_bid(self) -> Optional[float]:` | `analysis/backtester.py:61` |
| `.best_ask` | method | `def best_ask(self) -> Optional[float]:` | `analysis/backtester.py:64` |
| `.mid` | method | `def mid(self) -> Optional[float]:` | `analysis/backtester.py:67` |
| `.bid_depth` | method | `def bid_depth(self, n: int) -> float:` | `analysis/backtester.py:73` |
| `.ask_depth` | method | `def ask_depth(self, n: int) -> float:` | `analysis/backtester.py:76` |
| `TapeTrade` | dataclass | `@dataclass class TapeTrade:` — `timestamp: float`, `price: float`, `size: float`, `side: str = "BUY"` | `analysis/backtester.py:81` |
| `PendingOrder` | dataclass | `@dataclass class PendingOrder:` — `symbol`, `side`, `price`, `quantity`, `timestamp`, `remaining`, `queue_ahead`, `filled_quantity: float = 0.0`, `fill_price: Optional[float] = None`, `reason: str = "SIGNAL"` | `analysis/backtester.py:90` |
| `.is_filled` | property | `def is_filled(self) -> bool:` | `analysis/backtester.py:110` |
| `.is_expired` | property | `def is_expired(self) -> bool:` | `analysis/backtester.py:114` |
| `Fill` | dataclass | `@dataclass class Fill:` — `symbol`, `side`, `quantity`, `price`, `mid_at_fill`, `slippage`, `slippage_bps`, `fees`, `timestamp` | `analysis/backtester.py:119` |
| `BacktestStats` | dataclass | `@dataclass class BacktestStats:` — `total_trades`, `wins`, `losses`, `net_pnl`, `gross_profit`, `gross_loss`, `profit_factor`, `win_rate`, `sharpe`, `sortino`, `max_drawdown`, `max_drawdown_pct`, `avg_slippage_bps`, `total_fees`, `unfilled_orders`, `equity_curve` | `analysis/backtester.py:133` |
| `CompletedTrade` | dataclass | `@dataclass class CompletedTrade:` — `symbol`, `side`, `quantity`, `entry_price`, `exit_price`, `entry_fill`, `exit_fill`, `pnl`, `fees`, `net_pnl`, `reason`, `entry_ts`, `exit_ts` | `analysis/backtester.py:436` |

**Event model.** Two event kinds, strictly timestamp-ordered: `L2Snapshot` (replace that symbol's book) and `TapeTrade` (consume queue, possibly fill pending orders). `replay` (`:856`) merges both into `(timestamp, kind, payload)`, snapshots at `kind = 0`, trades at `kind = 1`, then `events.sort(key=lambda e: (e[0], e[1]))`. Snapshots must precede trades at the same timestamp or queue position is computed from a book that never existed; the sort key must stay type-consistent or Python compares tuple to float and raises `TypeError` mid-replay. Optional `strategy(tester, snapshot)` runs after each snapshot — the backtester never decides when to trade. `on_snapshot` (`:639`) sets `self.book[snap.symbol] = snap`, advances `current_ts = max(current_ts, snap.timestamp)`, marks equity. `on_trade_event` (`:645`) advances the clock, walks `self.pending`, skips orders with `abs(order.price - trade.price) > 1e-9`, feeds matches through a `QueueFillModel`, records a `Fill` (`fees = take * order.price * taker_fee`, slippage against the current mid in absolute and bps), removes filled orders.

**FIFO queue-position fill (`QueueFillModel`, `:239`).** `__init__(initial_queue_ahead: float = 0.0)` sets `queue_ahead = max(0.0, float(initial_queue_ahead))`, `filled = 0.0`, `total_traded_through = 0.0`. `on_trade(trade_size)` (`:277`): `trade_size <= 0` → `0.0`; `total_traded_through += trade_size`; if `queue_ahead > 0`, `consumed = min(queue_ahead, trade_size)`, `queue_ahead -= consumed`, `leftover = trade_size - consumed`, return `0.0` when `leftover <= 0` else `leftover`; otherwise return the whole `trade_size`. `has_priority` (`:301`) is `queue_ahead <= 1e-12`. `PendingOrder.is_filled` uses `remaining <= 1e-12`; `is_expired` is literally `remaining > 1e-12` — the same predicate, opposite name. Initial `queue_ahead` comes from `_queue_ahead_at_price` (`:573`), which reads **bids** for BUY and **asks** for SELL — the deliberate mirror of `_worst_fill_price`, since a resting maker queues behind bids while a taker BUY lifts asks; both directions documented inline at `:579-591` as a bug source. **Conservative fill:** a traded price level absent from the book does not fill the order.

**Market impact.** `_worst_fill_price` (`:497`) walks `snap.asks` for BUY / `snap.bids` for SELL across `levels[:max_book_levels]`, accumulating `price * take`, returning `(notional / filled, filled)`; a side thinner than the order leaves the remainder **unfilled**. `_estimate_impact_bps` (`:542`) is a linear approximation — `participation = quantity / side_depth`, `impact_bps = impact_coefficient * participation * 10_000.0` — defined and tested but not called from `open_position`/`close_position`.

**Engine and metrics.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `_returns` | function | `def _returns(equity: List[float]) -> List[float]:` | `analysis/backtester.py:159` |
| `sharpe_ratio` | function | `def sharpe_ratio(returns: Sequence[float], risk_free: float = 0.0) -> Optional[float]:` | `analysis/backtester.py:170` |
| `sortino_ratio` | function | `def sortino_ratio(returns: Sequence[float], risk_free: float = 0.0) -> Optional[float]:` | `analysis/backtester.py:189` |
| `max_drawdown` | function | `def max_drawdown(equity: List[float]) -> Tuple[Optional[float], Optional[float]]:` | `analysis/backtester.py:212` |
| `QueueFillModel` | class | `class QueueFillModel:` | `analysis/backtester.py:239` |
| `.on_trade` | method | `def on_trade(self, trade_size: float) -> float:` | `analysis/backtester.py:277` |
| `.has_priority` | property | `def has_priority(self) -> bool:` | `analysis/backtester.py:301` |
| `load_l2_events` | function | `def load_l2_events(sqlite_path: str) -> Iterator[Tuple[str, float, object]]:` | `analysis/backtester.py:311` |
| `synthesize_walk` | function | `def synthesize_walk(base_price: float = 100.0, steps: int = 600, step_seconds: float = 1.0, spread_bps: float = 2.0, depth_levels: int = 5, base_size: float = 10.0, trade_rate: float = 4.0, seed: int = 7) -> Tuple[List[L2Snapshot], List[TapeTrade]]:` | `analysis/backtester.py:352` |
| `L2Backtester` | class | `class L2Backtester:` | `analysis/backtester.py:453` |
| `.__init__` | method | `def __init__(self, initial_balance: float = 10000.0, taker_fee: float = 0.0005, max_book_levels: int = 5, impact_coefficient: float = 0.5):` | `analysis/backtester.py:469` |
| `._worst_fill_price` | method | `def _worst_fill_price(self, symbol: str, side: str, quantity: float) -> Optional[Tuple[float, float]]:` | `analysis/backtester.py:497` |
| `._estimate_impact_bps` | method | `def _estimate_impact_bps(self, symbol: str, side: str, quantity: float) -> Optional[float]:` | `analysis/backtester.py:542` |
| `._queue_ahead_at_price` | method | `def _queue_ahead_at_price(self, symbol: str, side: str, price: float) -> float:` | `analysis/backtester.py:573` |
| `.submit_limit` | method | `def submit_limit(self, symbol: str, side: str, price: float, quantity: float, reason: str = "SIGNAL") -> Optional[PendingOrder]:` | `analysis/backtester.py:601` |
| `.on_snapshot` | method | `def on_snapshot(self, snap: L2Snapshot) -> None:` | `analysis/backtester.py:639` |
| `.on_trade_event` | method | `def on_trade_event(self, trade: TapeTrade) -> None:` | `analysis/backtester.py:645` |
| `.open_position` | method | `def open_position(self, symbol: str, side: str, quantity: float, reason: str = "SIGNAL", use_market_impact: bool = True) -> Optional[CompletedTrade]:` | `analysis/backtester.py:696` |
| `.close_position` | method | `def close_position(self, reason: str = "SIGNAL", use_market_impact: bool = True):` | `analysis/backtester.py:763` |
| `._mark_equity` | method | `def _mark_equity(self, timestamp: float) -> None:` | `analysis/backtester.py:832` |
| `.replay` | method | `def replay(self, snapshots: List[L2Snapshot], trades: List[TapeTrade], strategy=None) -> "L2Backtester":` | `analysis/backtester.py:856` |
| `.stats` | method | `def stats(self) -> BacktestStats:` | `analysis/backtester.py:901` |
| `.report` | method | `def report(self) -> str:` | `analysis/backtester.py:950` |

**Metric definitions (`stats`, `:901`).** `total_trades = len(self.completed)`; `unfilled_orders = len(self.pending)`; `net_pnl += trade.net_pnl` and `total_fees += trade.fees` per trade; a trade is a win when `net_pnl > 0` (net, after fees) feeding `gross_profit`, else a loss feeding `abs(net_pnl)` into `gross_loss`. `win_rate = wins / total_trades`, else `None`. `profit_factor = gross_profit / gross_loss` when `gross_loss > 0`; `None` with no losses (mathematically infinite — `None` beats `float("inf")`); `None` with no trades. `sharpe`/`sortino` from `_returns(equity_values)` at `risk_free = 0.0`. `max_drawdown`, `max_drawdown_pct` from `max_drawdown(equity_values)`. `avg_slippage_bps = sum(abs(f.slippage_bps) for f in self.fills) / len(self.fills)`. Anything undefined stays `None`, never `0.0` — `profit_factor = 0.0` and "cannot be computed" are different claims.

`sharpe_ratio` (`:170`): `None` when `len(returns) < 2` or `std <= 0`; else `(mean - risk_free) / sqrt(sample variance of excess returns)`. `sortino_ratio` (`:189`): downside `min(0.0, r - risk_free)`, `dd_var = sum(d*d)/len(downside)`, `None` when `dd_std <= 0` — no losing period at all is undefined, not "excellent". `max_drawdown` (`:212`): `(None, None)` for `len(equity) < 2`; else running peak with worst absolute and relative drop. `report` (`:950`) prints a 62-column ASCII block, rendering `None` as `"N/A"`.

**Data sources.** `load_l2_events` (`:311`) expects `l2_snapshots(ts, symbol, bids, asks)` (bids/asks JSON `[[px, sz], ...]`) UNION ALL'd with `l2_trades(ts, symbol, price, size, side)`, `ORDER BY ts ASC`; rows with non-`NULL bids` tag `"snapshot"`, else `"trade"`. `synthesize_walk` (`:352`) builds a seeded deterministic book: `rng.gauss(0.0, 0.00015)` mid steps, per-level decay `(1.0 - 0.15 * level)` plus `rng.uniform(0, base_size * 0.1)`, `int(rng.random() * trade_rate)` trades per step sized `size * rng.uniform(0.1, 0.6)`.

**State it owns.** `initial_balance`, `cash`, `taker_fee`, `max_book_levels`, `impact_coefficient`, `book: Dict[str, L2Snapshot]`, `pending`, `fills`, `completed`, `open_trade: Optional[CompletedTrade]`, `equity_curve` seeded `(0.0, cash)`, `current_ts: float = 0.0`. `_mark_equity` (`:832`) marks cash plus unrealized PnL from the last book mid. PnL: `open_position` moves cash `-= filled*price + fees` on BUY, `+= filled*price - fees` on SELL; `close_position` mirrors it on `exit_side`, computes `gross = (price - entry_price) * filled_qty` (BUY) or `(entry_price - price) * filled_qty` (SELL), `net_pnl = gross - (entry + exit fees)`; returns `None` leaving the position open when the exit-side book is exhausted.

**Side effects.** Reads a SQLite file via `load_l2_events` (own connection, closed in `finally`); no file, DB, or network writes.

**Consumers.** Tests only: `tests/test_advanced_modules.py:371,434,447,654,667`. **No production module imports this file.**

#### 9.1.15 `agents/__init__.py`

**Role.** Package marker only — `"""Agents package — agen otonom: berita, analisis, keputusan, eksekusi."""`, no exports; all imports are fully qualified (`agents/__init__.py:1`).

#### 9.1.16 `analysis/__init__.py`

**Role.** Package marker only — `"""Analysis package — indikator teknikal, fundamental, sinyal ML."""`, no exports (`analysis/__init__.py:1`).

---

### 9.2 Execution, position and risk layers (`trading/`)

#### 9.2.1 `trading/models.py`

**Role.** Pure dataclass/enum vocabulary shared by agents, the paper engine and the live adapter. No behaviour, no I/O — the wire format between `DecisionAgent` and whichever executor is bound.

**Key symbols.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `Side` | enum | `class Side(str, Enum)` — `LONG`, `SHORT` | `trading/models.py:10` |
| `OrderType` | enum | `class OrderType(str, Enum)` — `MARKET`, `LIMIT` | `trading/models.py:15` |
| `TradeAction` | enum | `class TradeAction(str, Enum)` — `OPEN_LONG`, `OPEN_SHORT`, `CLOSE`, `HOLD` | `trading/models.py:20` |
| `CloseReason` | enum | `class CloseReason(str, Enum)` — `TP_HIT`, `SL_HIT`, `MANUAL`, `LIQUIDATED`, `SIGNAL` | `trading/models.py:27` |
| `Order` | dataclass | `class Order:` fields `symbol, action, side=None, quantity=None, leverage=5, order_type=OrderType.MARKET, stop_loss=None, take_profit=None, position_id=None, reasoning=""`, `price: Optional[float] = None` | `trading/models.py:36` |
| `TradeDecision` | dataclass | `class TradeDecision:` fields `action, symbol, side=None, confidence=0.0, leverage=5, stop_loss_pct=None, take_profit_pct=None, risk_pct=0.02, reasoning=""`, `signals: dict = field(default_factory=dict)` | `trading/models.py:56` |
| `PositionInfo` | dataclass | `class PositionInfo:` fields `id, symbol, side, entry_price, quantity, leverage, margin, liquidation_price, stop_loss, take_profit, unrealized_pnl, roe_pct, mark_price, duration` | `trading/models.py:71` |

**State it owns.** None. Field notes that are load-bearing: `Order.quantity is None` is the *deliberate* sentinel meaning "size me from the risk manager" (`models.py:41`); `Order.price` is ignored by the paper engine and **required** by the live engine (`models.py:48-52`).

**Side effects.** None.

**Consumers.** `agents/decision_agent.py:17`, `agents/execution_agent.py:20`, `trading/risk_manager.py:9`, `trading/position_manager.py:20`, `trading/paper_engine.py:19`, `trading/live/executor.py:68` (function-local import).

#### 9.2.2 `trading/risk_manager.py`

**Role.** All position sizing, SL/TP/liq price derivation, fee and PnL arithmetic, and the four pre-trade limits. Stateless apart from the injected initial balance; every function is pure Decimal arithmetic.

**Key symbols — module constants.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `PRICE_QUANT` | constant | `PRICE_QUANT = Decimal("0.00000001")` | `trading/risk_manager.py:16` |
| `MONEY_QUANT` | constant | `MONEY_QUANT = Decimal("0.01")` | `trading/risk_manager.py:17` |
| `QTY_QUANT` | constant | `QTY_QUANT = Decimal("0.00001")` | `trading/risk_manager.py:18` |

**Key symbols — `RiskManager`.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `RiskManager` | class | `class RiskManager:` | `trading/risk_manager.py:21` |
| `__init__` | method | `def __init__(self, initial_balance: float = 0.0):` | `trading/risk_manager.py:31` |
| `initial_balance` | property | `def initial_balance(self) -> float:` | `trading/risk_manager.py:47` |
| `set_initial_balance` | method | `def set_initial_balance(self, value: float) -> None:` | `trading/risk_manager.py:51` |
| `calculate_position_size` | method | `def calculate_position_size(self, balance: float, entry_price: float, stop_loss_price: float, risk_pct: float = None, leverage: int = None) -> Dict:` | `trading/risk_manager.py:64` |
| `calculate_liquidation_price` | method | `def calculate_liquidation_price(self, entry_price: float, side: str, leverage: int, mmr: float = 0.004) -> float:` | `trading/risk_manager.py:133` |
| `calculate_stop_loss` | method | `def calculate_stop_loss(self, entry_price: float, side: str, sl_pct: float = 0.02) -> float:` | `trading/risk_manager.py:157` |
| `calculate_take_profit` | method | `def calculate_take_profit(self, entry_price: float, side: str, tp_pct: float = 0.04) -> float:` | `trading/risk_manager.py:181` |
| `calculate_fee` | method | `def calculate_fee(self, quantity: float, price: float, fee_type: str = "TAKER") -> float:` | `trading/risk_manager.py:202` |
| `calculate_pnl` | method | `def calculate_pnl(self, side: str, entry_price: float, current_price: float, quantity: float) -> Dict:` | `trading/risk_manager.py:242` |
| `validate_trade` | method | `def validate_trade(self, balance: float, margin_required: float, open_positions: int, daily_pnl: float = 0, peak_balance: float = None, equity: float = None) -> Dict:` | `trading/risk_manager.py:273` |
| `calculate_scalp_position_size` | method | `def calculate_scalp_position_size(self, balance: float, entry_price: float, leverage: int = None, risk_pct: float = None) -> Dict:` | `trading/risk_manager.py:340` |
| `kelly_criterion` | method | `def kelly_criterion(self, win_rate: float, avg_win: float, avg_loss: float) -> float:` | `trading/risk_manager.py:380` |
| `calculate_max_drawdown` | method | `def calculate_max_drawdown(self, equity_history: List[float]) -> float:` | `trading/risk_manager.py:397` |
| `calculate_sharpe_ratio` | method | `def calculate_sharpe_ratio(self, returns: List[float], risk_free_rate: float = 0.0) -> float:` | `trading/risk_manager.py:414` |

**Every limit, with its trigger expression.** All four live in `validate_trade`; each appends to `reasons`, and `allowed = len(reasons) == 0` (`risk_manager.py:335-338`).

| # | Limit | Trigger | Config field (default) | Loc |
|---|---|---|---|---|
| 1 | Margin ceiling | `margin_required > balance * 0.9` | — (hard-coded 90%) | `trading/risk_manager.py:299` |
| 2 | Max open positions | `open_positions >= self.config.max_open_positions` | `risk.max_open_positions = 3` | `trading/risk_manager.py:303` |
| 3 | Daily-loss circuit breaker | `daily_pnl < 0 and reference > 0` **and** `abs(daily_pnl) / reference >= self.config.max_daily_loss`, where `reference = self._initial_balance if self._initial_balance > 0 else balance` | `risk.max_daily_loss = 0.05` | `trading/risk_manager.py:319-322` |
| 4 | Max drawdown | `peak_balance and peak_balance > 0` **and** `(peak_balance - mark) / peak_balance >= self.config.max_drawdown`, where `mark = equity if equity is not None else balance` | `risk.max_drawdown = 0.15` | `trading/risk_manager.py:329-332` |

Two structural facts about these limits: the daily-loss denominator is the **fixed initial balance**, not live cash — using cash would shrink the threshold exactly when losses mount. And the drawdown numerator uses **equity** (free cash + open margin + unrealized), because free cash drops every time a position opens; `paper_engine.py:549` computes `equity_now = free_balance + open_margin + open_upnl` and passes it as `equity=`.

**Sizing math.** `calculate_position_size` (fixed-fractional): `risk_amount = balance * risk_pct`; `sl_distance = abs(entry - stop_loss)`, falling back to `entry * 0.01` when zero (`:107-109`); `quantity = risk_amount / sl_distance`; `position_value = quantity * entry`; `margin = position_value / leverage`. Leverage clamped `min(leverage, config.max_leverage)` (default 20). If `margin > balance * 0.9`, everything rescales down off the 90% cap (`:119-123`). `calculate_scalp_position_size` skips SL distance: `margin = balance * risk_pct` clamped to `balance * 0.9 / max_open_positions` (`:364-365`), `position_value = margin * leverage`, `quantity = position_value / entry`. That is the branch scalping actually takes.

**Rounding.** Every output quantizes with `ROUND_DOWN` — quantity to `1e-5`, money to `0.01` (`:126-130`, `:373-377`). Prices to `1e-8`. The docstrings at `:163-169` and `:186-190` state why explicitly: default `ROUND_HALF_EVEN` can move a 0.25% scalp stop one tick the wrong way, which is a material fraction of the intended room.

**State it owns.** `self.config` (`RiskConfig`), `self.fees` (`FeeConfig`: `maker=0.0002`, `taker=0.0005`), `self._daily_pnl: float = 0.0` and `self._daily_reset_date: str = ""` (both written nowhere in this module — vestigial), `self._initial_balance: float`.

**Side effects.** None — no file, network or DB writes. `calculate_sharpe_ratio` does a function-local `import numpy as np` (`:421`). `set_initial_balance` mutates only its own field.

**Consumers.** `agents/decision_agent.py:18`, `agents/execution_agent.py:21`, `trading/position_manager.py:19`, `trading/paper_engine.py:20` (instantiated in `PaperTradingEngine.__init__`, `paper_engine.py:60`), plus tests `tests/test_risk_manager.py:6`, `tests/test_bugfixes.py`, `tests/test_numerics.py:15`, `tests/test_lifecycle_paths.py:30`, `tests/test_position_manager.py:13`, `tests/test_decision_agent_ensemble.py:23`, `check_behavior.py:30`.

#### 9.2.3 `trading/position_manager.py`

**Role.** The only writer of position/trade/balance rows. Owns the full position lifecycle — open, unrealized update, SL/TP/liquidation checks, close, batch close, balance snapshots.

**Key symbols — `PositionManager`.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `PositionManager` | class | `class PositionManager:` | `trading/position_manager.py:25` |
| `__init__` | method | `def __init__(self, event_bus: EventBus, risk_manager: RiskManager):` | `trading/position_manager.py:35` |
| `_get_repo` | method | `async def _get_repo(self) -> Repository:` | `trading/position_manager.py:41` |
| `open_position` | method | `async def open_position(self, symbol: str, side: str, entry_price: float, quantity: float, leverage: int, stop_loss: float = None, take_profit: float = None, reasoning: str = "") -> Optional[int]:` | `trading/position_manager.py:47` |
| `close_position` | method | `async def close_position(self, position_id: int, close_price: float, reason: str = "MANUAL") -> Optional[Dict]:` | `trading/position_manager.py:150` |
| `batch_close_positions` | method | `async def batch_close_positions(self, position_ids: List[int], prices: Dict[str, float], reason: str) -> int:` | `trading/position_manager.py:298` |
| `update_positions` | method | `async def update_positions(self, prices: Dict[str, float]):` | `trading/position_manager.py:326` |
| `_should_liquidate` | method | `def _should_liquidate(self, pos: dict, current_price: float) -> bool:` | `trading/position_manager.py:366` |
| `_sl_hit` | method | `def _sl_hit(self, pos: dict, price: float) -> bool:` | `trading/position_manager.py:372` |
| `_tp_hit` | method | `def _tp_hit(self, pos: dict, price: float) -> bool:` | `trading/position_manager.py:378` |
| `_liquidate` | method | `async def _liquidate(self, pos: dict, price: float):` | `trading/position_manager.py:384` |
| `get_open_position_count` | method | `async def get_open_position_count(self) -> int:` | `trading/position_manager.py:420` |
| `get_total_unrealized_pnl` | method | `async def get_total_unrealized_pnl(self) -> float:` | `trading/position_manager.py:425` |
| `get_total_open_margin` | method | `async def get_total_open_margin(self) -> float:` | `trading/position_manager.py:430` |
| `take_balance_snapshot` | method | `async def take_balance_snapshot(self):` | `trading/position_manager.py:436` |

**Exit trigger expressions** (evaluated in `update_positions` in this order — liquidation first, then SL, then TP; each `continue`s so only one fires per cycle):

| Check | LONG | SHORT | Loc |
|---|---|---|---|
| Liquidation | `current_price <= pos["liquidation_price"]` | `current_price >= pos["liquidation_price"]` | `trading/position_manager.py:367-370` |
| Stop loss | `price <= pos["stop_loss"]` | `price >= pos["stop_loss"]` | `trading/position_manager.py:373-376` |
| Take profit | `price >= pos["take_profit"]` | `price <= pos["take_profit"]` | `trading/position_manager.py:379-382` |

**Balance accounting.** `account.balance` in the DB is **free cash**, not wallet balance. `open_position` debits `-(margin + fee)` via the atomic `repo.apply_balance_delta` (`:85-86`); on insufficient funds or a failed INSERT it credits the same amount back (`:88`, `:111`). `close_position` credits `pos["margin"] + net_pnl + open_fee` (`:243-244`) — margin plus net, plus the open fee added *back* because it was already debited at open and is also subtracted inside `net_pnl`. Liquidation is the exception: margin was already locked, so `_liquidate` writes `realized_pnl = -pos["margin"]` and touches the balance not at all (`:391`).

**Claim-once semantics.** Both `close_position` and `_liquidate` rely on the repository's conditional UPDATE as a claim token: `repo.close_position(...)` / `repo.liquidate_position(...)` return falsy if the row was not `OPEN`, and the caller returns `None` (`position_manager.py:209-211`, `:392-394`). This is what lets `update_positions` and a scheduler close the same position concurrently without double-paying margin. `peak_balance` is bumped from **equity**, not free cash (`:255-258`).

**State it owns.** `self.event_bus`, `self.risk_manager`, `self.config = get_config()`, `self._repo: Optional[Repository]` (lazily built, then cached).

**Side effects.** DB writes: `insert_position`, `insert_trade` (x2 per round trip), `apply_balance_delta`, `close_position`/`liquidate_position` (conditional), `update_position_pnl`, `update_account_stats`, `bump_peak_balance`, `insert_balance_snapshot`. DB reads: `get_account`, `get_open_positions`, `get_trades_by_position`, `get_trade_stats`. Event bus publishes on `Channels.POSITION_UPDATE` with `action` in `{OPENED, CLOSED, LIQUIDATED}` and `source="position_manager"` (`:128-142`, `:272-284`, `:407-418`). No network.

**Consumers.** `trading/paper_engine.py:21` (constructed at `:61`); `agents/execution_agent.py:270` and `:311` reach through `self.engine.position_manager.close_position(...)` directly for scalp-TP and expiry closes; `tests/test_position_manager.py:14`, `tests/test_lifecycle_paths.py:29`.

#### 9.2.4 `trading/paper_engine.py`

**Role.** The paper execution engine: takes an `Order`, prices it, gates it through the risk manager, and hands it to `PositionManager`. Also owns the ATR candle cache that feeds `analysis.volatility`.

**Key symbols.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `VolatilityGateError` | exception | `class VolatilityGateError(Exception):` — `__init__(self, reason: str, meta: Optional[dict] = None)` | `trading/paper_engine.py:26`, `:40` |
| `PaperTradingEngine` | class | `class PaperTradingEngine:` | `trading/paper_engine.py:46` |
| `__init__` | method | `def __init__(self, event_bus: EventBus):` | `trading/paper_engine.py:58` |
| `_get_repo` | method | `async def _get_repo(self) -> Repository:` | `trading/paper_engine.py:70` |
| `initialize` | method | `async def initialize(self):` | `trading/paper_engine.py:76` |
| `_register_candle_source` | method | `def _register_candle_source(self, repo: "Repository") -> None:` | `trading/paper_engine.py:98` |
| `refresh_volatility_cache` | method | `async def refresh_volatility_cache(self) -> None:` | `trading/paper_engine.py:137` |
| `update_price` | method | `def update_price(self, symbol: str, price: float):` | `trading/paper_engine.py:155` |
| `get_price` | method | `def get_price(self, symbol: str) -> Optional[float]:` | `trading/paper_engine.py:159` |
| `execute_order` | method | `async def execute_order(self, order: Order) -> Dict:` | `trading/paper_engine.py:181` |
| `_tick_quality_guard` | method | `def _tick_quality_guard(self, symbol: str, price: float, sl_pct: float):` | `trading/paper_engine.py:209` |
| `_resolve_tp_sl` | method | `def _resolve_tp_sl(self, symbol: str, order: "Order") -> tuple:` | `trading/paper_engine.py:270` |
| `_diagnose_open_failure` | method | `async def _diagnose_open_failure(self, order: Order, quantity: float, price: float, margin: float, estimated_fee: float) -> str:` | `trading/paper_engine.py:332` |
| `_execute_open` | method | `async def _execute_open(self, order: Order, price: float) -> Dict:` | `trading/paper_engine.py:372` |
| `_execute_close` | method | `async def _execute_close(self, order: Order, price: float) -> Dict:` | `trading/paper_engine.py:687` |
| `_describe_failures` | static | `def _describe_failures(failed: List[dict]) -> str:` | `trading/paper_engine.py:810` |
| `check_positions` | method | `async def check_positions(self):` | `trading/paper_engine.py:822` |
| `get_account_summary` | method | `async def get_account_summary(self) -> Dict:` | `trading/paper_engine.py:835` |

**The fill / fee / slippage model, precisely.**

- **Fill price** — exactly `self.get_price(order.symbol)` (`:191`): the `_last_prices` cache if it holds a positive value, else `market_store.get_price(symbol)`, written back into the cache (`:159-179`). **No bid/ask midpoint, no order book, no partial fill, no queue position.** An accepted order fills 100% of quantity at that one tick.
- **Slippage** — there is **no slippage model at all**: no adjustment, no spread crossing, no partial rejection. The nearest thing is `_tick_quality_guard` (`:209-268`), which *rejects* rather than reprices. Two ordered checks: (1) tick age — `age > scalping.max_tick_age_seconds` (1.5 s) ⇒ reject; (2) outlier — over `market_store.get_price_history(symbol, seconds=stale_tick_window_seconds)` (3.0 s), if samples `< stale_tick_min_samples` (5) it fails **open** and allows, else `abs(price - median_px) / median_px > sl_pct` ⇒ reject. Median, not mean, so one spike cannot move the baseline. Fail-open on thin data is deliberate (`:229-233`).
- **Fee** — always **TAKER**, `rate = fees.taker = 0.0005`, never maker. `fee = abs(|qty| * |price|) * rate` quantized to `Decimal("0.0000000001")`, `ROUND_DOWN`, then `abs()` (`risk_manager.py:202-240` — the 10-decimal quantum is deliberate; quantizing to cents made sub-dollar scalps pay zero fee). Charged **twice** per round trip: at open alongside margin (`:85`), and at close (`position_manager.py:181`). Both subtracted from `net_pnl` (`:203`).
- **Margin** — `quantity * price / leverage`, locked from free cash at open, released at close.
- **SL/TP** — recomputed at fill from percentages, never taken from the order. `_resolve_tp_sl` (`:270-330`): `sl_pct = max(dynamic_sl, tight_sl_pct)`, `tp_pct = max(dynamic_tp, fast_tp_pct)`, then `tp_pct = max(tp_pct, sl_pct * dynamic_tp_sl.min_risk_reward)` (1.5). Static values are a **floor, not a starting point** — ATR may widen targets, never narrow below config. Only with `scalping.enabled == False` do fixed `0.02 / 0.04` apply (`:477-478`). `assess_volatility_gate` runs last, raising `VolatilityGateError`, caught at `:452` into a `TRADE_REJECTED` row so market rejections stay distinguishable from account ones.
- **PnL rounding** — `pnl`, `roe_pct`, `pnl_pct` all quantized to `0.01`; the last two are the *same number* (`risk_manager.py:267-271`).

**Rejection ladder in `_execute_open`:** (1) account missing (`:377`); (2) input sanity — `quantity <= 0` when non-`None`, `leverage` `None` or `<= 0`, `price <= 0` (`:397-425`); (3) volatility gate (`:452`); (4) tick guard (`:495`); (5) `validate_trade` (`:566`); (6) `open_position` returning `None`, re-diagnosed into one of three honest messages (`:332-370`) — account uninitialised / insufficient cash / DB insert failed. Steps 2 and 4 exist because without them a negative `quantity` makes `required_cash` negative, `validate_trade` calls it affordable, and money is created from nothing (`:385-396`).

**Sizing branch** (`:517-535`): `quantity is None` + scalping on → `calculate_scalp_position_size`; `quantity is None` + scalping off → `calculate_position_size` with the fresh `stop_loss`; `quantity` set → `margin = (quantity * price) / order.leverage`, **no cap**. `DecisionAgent` never sets `quantity` (`agents/decision_agent.py:264-271`), so the first branch always runs.

**State it owns.** `self.event_bus`, `self.risk_manager`, `self.position_manager`, `self.config`, `self._repo: Optional[Repository]`, `self._candle_cache: Dict[str, list]` (30 × 1m candles/symbol), `self._candle_refresh_task`, `self._last_prices: Dict[str, float]`.

**Side effects.** DB: `init_account`, `get_account`, `get_candles`, `get_daily_realized_pnl`, `get_open_positions`, plus `insert_agent_log` on every rejection and on `TRADE_EXECUTED` / `TRADE_CLOSED`. Event bus: `Channels.TRADE_EXECUTED` on open (`:656-671`). Global mutation: `analysis.volatility.set_candle_source(_sync_source)` — **process-wide module global** (`:131`) — and an `asyncio.Task` at `:135`. No network.

**Consumers.** `run.py:57` (bound as default executor at `run.py:363`), `run.py:356` (its `risk_manager` → `DecisionAgent`), `agents/execution_agent.py:64,71,96,100,164,192,213,266,270,290,311`, `tests/test_paper_engine.py:14`, `tests/test_advanced_modules.py:334,346`, `tests/test_bugfixes.py:217,433`, `tests/test_fill_price_sl.py:20`, `tests/test_lifecycle_paths.py:546`, `tests/test_live_executor.py:209`.

#### 9.2.5 `trading/live/safety.py`

**Role.** The single yes/no gate for anything touching real money. Small, side-effect-light, network-free by design. Returns explanatory `Blocker` values, never a bare boolean.

**Key symbols.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `Blocker` | enum | `class Blocker(str, Enum)` — 17 members | `trading/live/safety.py:30` |
| `OrderRequest` | dataclass | `class OrderRequest:` — `symbol, is_buy, size, price, is_close=False, reduce_only=False`; `notional` property `abs(self.size) * self.price` | `trading/live/safety.py:53`, `:64` |
| `DayCounters` | dataclass | `class DayCounters:` — `day_utc="", orders_sent=0, realized_pnl=0.0, consecutive_errors=0, _unreadable=False` | `trading/live/safety.py:69` |
| `DayCounters.readable` | property | `def readable(self) -> bool:` | `trading/live/safety.py:91` |
| `DayCounters.to_dict` | method | `def to_dict(self) -> Dict[str, Any]:` | `trading/live/safety.py:95` |
| `DayCounters.load` | method | `def load(self, path) -> None:` | `trading/live/safety.py:103` |
| `DayCounters.save` | method | `def save(self, path) -> bool:` | `trading/live/safety.py:127` |
| `DayCounters.rollover_if_needed` | method | `def rollover_if_needed(self, now: Optional[datetime] = None) -> None:` | `trading/live/safety.py:145` |
| `SafetyGate` | class | `class SafetyGate:` | `trading/live/safety.py:159` |
| `__init__` | method | `def __init__(self, cfg: LiveConfig, env: Optional[dict] = None, state_path=None):` | `trading/live/safety.py:168` |
| `persist` | method | `def persist(self) -> bool:` | `trading/live/safety.py:193` |
| `private_key` | property | `def private_key(self) -> Optional[str]:` | `trading/live/safety.py:200` |
| `redact` | method | `def redact(self, text: str) -> str:` | `trading/live/safety.py:213` |
| `in_live_window` | method | `def in_live_window(self, now: Optional[datetime] = None) -> bool:` | `trading/live/safety.py:231` |
| `master_blockers` | method | `def master_blockers(self, now: Optional[datetime] = None) -> List[Blocker]:` | `trading/live/safety.py:247` |
| `order_blockers` | method | `def order_blockers(self, request: OrderRequest, free_collateral: float = 0.0, current_exposure: float = 0.0, symbol_exposure: float = 0.0, cloid: Optional[Any] = None, leverage: Optional[int] = None) -> List[Blocker]:` | `trading/live/safety.py:300` |
| `can_send` | method | `def can_send(self, request: OrderRequest, free_collateral: float = 0.0, current_exposure: float = 0.0, symbol_exposure: float = 0.0, now: Optional[datetime] = None, cloid: Optional[Any] = None, leverage: Optional[int] = None) -> tuple:` | `trading/live/safety.py:347` |
| `engage_kill_switch` | method | `def engage_kill_switch(self, reason: str) -> None:` | `trading/live/safety.py:384` |
| `record_error` | method | `def record_error(self) -> None:` | `trading/live/safety.py:397` |
| `record_success` | method | `def record_success(self) -> None:` | `trading/live/safety.py:410` |
| `record_realized_pnl` | method | `def record_realized_pnl(self, pnl: float) -> None:` | `trading/live/safety.py:415` |
| `record_order_sent` | method | `def record_order_sent(self) -> None:` | `trading/live/safety.py:419` |

**Every guard, in order.**

*Master guards — `master_blockers`, all evaluated, none short-circuit (`safety.py:255-298`).*

| # | Guard | Trigger | Blocker | Loc |
|---|---|---|---|---|
| M0 | Counter file readable | `not self.counters.readable` | `COUNTER_STATE_UNREADABLE` | `trading/live/safety.py:261` |
| M1 | Live mode on | `str(env["TRADEBOT_LIVE"]).strip() not in ("1","true","yes")` | `LIVE_DISABLED` | `trading/live/safety.py:266` |
| M2 | Explicit confirm env | `str(env[cfg.live_confirm_env]).strip() not in ("1","true","yes")` (default name `TRADEBOT_LIVE_CONFIRMED`) | `NOT_CONFIRMED` | `trading/live/safety.py:271` |
| M3 | Kill switch | `self.engaged` **or** `env["TRADEBOT_LIVE_KILL_SWITCH"] not in ("", "0", "false", "no")` | `KILL_SWITCH` | `trading/live/safety.py:277` |
| M4 | Private key present | `not self.private_key` | `MISSING_KEY` | `trading/live/safety.py:283` |
| M5 | Trading window | `not self.in_live_window(now)`; window is `start <= hour < end`, or `hour >= start or hour < end` when it wraps midnight | `OUTSIDE_WINDOW` | `trading/live/safety.py:287`, `:242-245` |
| M6 | Daily order count | `self.counters.orders_sent >= self.cfg.max_daily_orders` (200) | `DAILY_ORDER_LIMIT` | `trading/live/safety.py:291` |
| M7 | Daily loss | `self.counters.realized_pnl <= -abs(self.cfg.max_daily_loss)` (50 USDC) | `DAILY_LOSS_LIMIT` | `trading/live/safety.py:293` |
| M8 | Consecutive errors | `self.counters.consecutive_errors >= self.cfg.max_consecutive_errors` (3) | `TOO_MANY_ERRORS` | `trading/live/safety.py:295` |

*Order guards — `order_blockers`, only reached when the master list is empty (`safety.py:364-373`).*

| # | Guard | Trigger | Blocker | Loc |
|---|---|---|---|---|
| O1 | Size positive | `request.size <= 0` | `INVALID_INPUT` | `trading/live/safety.py:319` |
| O2 | Price positive | `request.price <= 0` | `INVALID_INPUT` | `trading/live/safety.py:321` |
| O3 | Cloid on opening order | `not request.is_close and not cloid` | `MISSING_CLOID` | `trading/live/safety.py:327` |
| O4 | Leverage in range | `leverage is not None and not (1 <= int(leverage) <= self.cfg.max_leverage)` (10) | `LEVERAGE_TOO_HIGH` | `trading/live/safety.py:332` |
| O5 | Order notional | `request.notional > self.cfg.max_order_notional` (100) | `ORDER_TOO_LARGE` | `trading/live/safety.py:337` |
| O6 | Position notional | `symbol_exposure + notional > self.cfg.max_position_notional` (300) | `POSITION_TOO_LARGE` | `trading/live/safety.py:339` |
| O7 | Total exposure | `current_exposure + notional > self.cfg.max_total_notional` (600) | `EXPOSURE_TOO_LARGE` | `trading/live/safety.py:341` |
| O8 | Free collateral floor | `free_collateral - notional < self.cfg.min_free_collateral` (100) | `COLLATERAL_TOO_LOW` | `trading/live/safety.py:343` |

`can_send` returns `(False, blockers)` and logs every blocker value, or `(True, [])` (`:375-382`). The exposure inputs are deliberately sourced from the exchange, not the local DB (`:312-315`).

**State it owns.** `self.cfg: LiveConfig`, `self.env: dict` (defaults to `dict(os.environ)`, injectable for tests), `self.counters: DayCounters`, `self.engaged: bool = False` (the in-process kill switch), `self._private_key: Optional[str]`, `self._key_loaded: bool`, `self.state_path: Path` (default `data_store/live_counters.json`). The private key is read once and never written to log, DB or file (`:200-211`); `redact()` strips any ≥8-char occurrence from outbound text (`:213-227`).

**Side effects.** Writes `data_store/live_counters.json` — via temp-file + `Path.replace`, i.e. atomic rename (`:135-143`) — but **only when live is enabled**: the load is gated on `state_path is not None or TRADEBOT_LIVE in ("1","true","yes")` (`:184-188`), so paper mode never touches the file. Every `record_*` method calls `persist()`. `record_error` can set `self.engaged = True`, latching the kill switch (`:408`). Reads env vars. No network.

**Consumers.** `trading/live/engine.py:32` (`SafetyGate`, `OrderRequest`; `Blocker` is imported at `:32` but unused), `run.py:260,273`, `live_doctor.py:186`, `tests/test_live_safety.py:19`, `tests/test_live_engine.py:12`.

#### 9.2.6 `trading/live/client.py`

**Role.** The only module that touches the Hyperliquid SDK. Pins testnet-vs-mainnet, holds the private key, enforces tick/lot precision, and normalizes every exchange response into `OrderOutcome`.

**Key symbols.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `OrderOutcome` | dataclass | `class OrderOutcome:` — `ok, filled_size=0.0, avg_price=0.0, order_id=None, error=None, raw=None` | `trading/live/client.py:34` |
| `OrderOutcome.describe` | method | `def describe(self) -> str:` | `trading/live/client.py:51` |
| `parse_order_response` | function | `def parse_order_response(raw: Any) -> OrderOutcome:` | `trading/live/client.py:59` |
| `LiveExchange` | class | `class LiveExchange:` | `trading/live/client.py:89` |
| `__init__` | method | `def __init__(self, private_key: str, testnet: bool = True, account_address: Optional[str] = None, timeout: float = 15.0):` | `trading/live/client.py:97` |
| `get_account_state` | method | `def get_account_state(self) -> Dict[str, Any]:` | `trading/live/client.py:160` |
| `free_collateral` | method | `def free_collateral(self) -> float:` | `trading/live/client.py:164` |
| `positions` | method | `def positions(self) -> List[Dict[str, Any]]:` | `trading/live/client.py:186` |
| `total_notional` | method | `def total_notional(self) -> float:` | `trading/live/client.py:194` |
| `symbol_notional` | method | `def symbol_notional(self, coin: str) -> float:` | `trading/live/client.py:211` |
| `rate_limit` | method | `def rate_limit(self) -> Dict[str, Any]:` | `trading/live/client.py:226` |
| `open_orders` | method | `def open_orders(self) -> List[Dict[str, Any]]:` | `trading/live/client.py:237` |
| `asset_rules` | method | `def asset_rules(self) -> Dict[str, Dict[str, Any]]:` | `trading/live/client.py:242` |
| `quantize_size` | method | `def quantize_size(self, coin: str, size: float) -> float:` | `trading/live/client.py:299` |
| `quantize_price` | method | `def quantize_price(self, coin: str, price: float) -> float:` | `trading/live/client.py:341` |
| `set_leverage` | method | `def set_leverage(self, coin: str, leverage: int, is_cross: bool = True) -> Any:` | `trading/live/client.py:362` |
| `place_limit_order` | method | `def place_limit_order(self, coin: str, is_buy: bool, size: float, price: float, reduce_only: bool = False, cloid: Optional[Any] = None) -> OrderOutcome:` | `trading/live/client.py:372` |
| `place_trigger_order` | method | `def place_trigger_order(self, coin: str, is_buy: bool, size: float, trigger_price: float, tpsl: str, reduce_only: bool = True) -> OrderOutcome:` | `trading/live/client.py:418` |
| `cancel` | method | `def cancel(self, coin: str, oid: int) -> Any:` | `trading/live/client.py:462` |
| `mid_price` | method | `def mid_price(self, coin: str) -> float:` | `trading/live/client.py:465` |
| `cancel_all` | method | `def cancel_all(self, coin: str) -> Any:` | `trading/live/client.py:483` |

**Precision model.** `sz_decimals` derives from `szMin` (not `szMax`) as `round(-log10(sz_min))`, then loosens up to 9 times until the step divides `szMin` exactly (`:269-287`) — `szMax` for BTC is ~1e6 and encodes no precision at all. `quantize_size` divides via `Decimal` with `ROUND_DOWN`, not `float` floor (which turns `0.5/1e-5` into `49999.99999999999` and drops a full step, `:331-337`); zero stays zero, sub-`szMin` raises to `szMin`, above-`szMax` floors to the largest fitting step. `quantize_price` is a magnitude rule, not an exchange rule: 5 decimals under $1, 4 under $100, 2 above (`:352`).

**Response semantics.** `parse_order_response` unwraps the batch list first element, treats a bare `str` or non-dict as an error, and trusts only `status == "ok"`; `filled.totalSz` to `filled_size`, `filled.avgPx` to `avg_price` (`:59-86`). `ok` means *accepted*, not *filled* — load-bearing in `engine.submit_order`.

**State it owns.** `self.testnet`, `self._account_address`, `self._rules: Optional[Dict[str, Dict[str, Any]]]` (lazy asset-rule cache), `self.wallet`, `self.address` (signer), `self.query_address` (address actually holding positions — API-wallet aware, `:128-133`), `self.base_url`, `self.info` (`Info`, `skip_ws=True`), `self.exchange` (`Exchange`).

**Side effects.** Network on every method — `info.user_state`, `info.frontend_open_orders`, `info.user_rate_limit`, `info.meta()`, `info.name_to_asset`, `exchange.order`, `exchange.cancel`, `exchange.update_leverage`, `exchange.all_mids`. All **synchronous and blocking by design** (`:355-360`); async callers must wrap in `asyncio.to_thread`. SDK imported inside `__init__` so the module imports cleanly in CI without it (`:115-124`); missing SDK raises `RuntimeError` with the install command. No file or DB writes; the key is never logged, only the derived address (`:148-156`).

**Consumers.** `trading/live/engine.py:31` (`LiveExchange`, `OrderOutcome`), `run.py:257,274`, `live_doctor.py:116`, `tests/test_live_engine.py:365`.

#### 9.2.7 `trading/live/engine.py`

**Role.** The live brain. The exchange — not the local DB — is the source of truth. Every order goes through `submit_order`, protection lives on the exchange, and failure is a normal return value rather than an exception.

**Key symbols.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `LivePosition` | dataclass | `class LivePosition:` — `symbol, coin, side, size, entry_price, stop_loss, take_profit, leverage`, `sl_order_id=None`, `tp_order_id=None` | `trading/live/engine.py:38` |
| `LiveEngine` | dataclass | `class LiveEngine:` — `gate: SafetyGate`, `exchange: LiveExchange`, `cfg: LiveConfig` (all three required — no default), `positions: Dict[str, LivePosition] = field(default_factory=dict)`, `now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc)` | `trading/live/engine.py:54` |
| `reconcile` | method | `async def reconcile(self) -> Dict[str, Any]:` | `trading/live/engine.py:74` |
| `_fetch_remote_positions` | method | `def _fetch_remote_positions(self) -> List[Dict[str, Any]]:` | `trading/live/engine.py:134` |
| `_sizes_match` | static | `def _sizes_match(local: LivePosition, remote: Dict[str, Any]) -> bool:` | `trading/live/engine.py:172` |
| `_remote_context` | method | `async def _remote_context(self, coin: str) -> Dict[str, float]:` | `trading/live/engine.py:188` |
| `submit_order` | method | `async def submit_order(self, coin: str, symbol: str, is_buy: bool, size: float, price: float, stop_loss: Optional[float] = None, take_profit: Optional[float] = None, leverage: int = 5, is_close: bool = False, cloid: Optional[Any] = None) -> Dict[str, Any]:` | `trading/live/engine.py:204` |
| `_attach_protection` | method | `async def _attach_protection(self, coin: str, symbol: str, side: str, size: float, entry_price: float, stop_loss: float, take_profit: float, leverage: int) -> LivePosition:` | `trading/live/engine.py:351` |
| `check_pending_fills` | method | `async def check_pending_fills(self) -> List[Dict[str, Any]]:` | `trading/live/engine.py:421` |
| `health_check` | method | `async def health_check(self) -> Dict[str, Any]:` | `trading/live/engine.py:518` |
| `run_loop` | method | `async def run_loop(self, interval: float = 5.0, on_decision=None) -> None:` | `trading/live/engine.py:603` |
| `emergency_flat` | method | `async def emergency_flat(self, reason: str = "manual") -> Dict[str, Any]:` | `trading/live/engine.py:655` |

**`submit_order` order of operations** — load-bearing; reversed order is unsafe (`:217-229`). (1) Build `OrderRequest` with `reduce_only = is_close`; (2) fetch `free_collateral` / `total_notional` / `symbol_notional` from the exchange in one `to_thread`; (3) `gate.can_send(...)` — refusal returns immediately; (4) reject a new position lacking SL or TP; (5) `set_leverage` — **before** the order, or the first order uses the exchange default leverage; (6) `place_limit_order`; (7) on failure `gate.record_error()`, on success `record_success()` + `record_order_sent()`; (8) if `is_close`, drop the local record and return; (9) if `outcome.filled_size <= 0`, return `protected: False` and **skip protection** — SL on an unfilled order is an orphan `reduceOnly` will reject anyway; (10) else `_attach_protection`. The returned `protected` key is what `LiveExecutor._open` branches on.

**Trigger direction.** `_attach_protection` sets `is_close_buy = side == "SHORT"` (`:378`) — the trigger side is the *closing* side. The comment at `:373-377` records that the earlier `is_buy=is_long` was a live bug that would open a bigger position on stop. If SL fails: `logger.critical` + kill switch (`:397-404`).

**`check_pending_fills`** closes the GTC-resting gap: reads `open_orders()`, `all_mids()`, `positions()` in one thread; takes authoritative size from exchange positions (`abs(szi)`); skips coins already tracked (matched by `.coin`, not the full symbol key, `:465-468`); installs `SL = 0.99*price` for LONG / `1.01*price` for SHORT and the mirrored TP (`:484-501`). Leverage for late fills comes from `gate.cfg.max_leverage` (`:501`).

**`health_check`** engages the kill switch immediately if the exchange is unreadable (`:555`), then flags four divergence classes: exchange position with no local record, local record missing on exchange, size mismatch (1% tolerance, `:184`), resting order with no tracked position. `ok = reachable and reconciled`.

**`emergency_flat`** cancels **first**, then closes — reversing the order lets a live GTC open a new position mid-shutdown (`:658-662`). Closes at `mid_price * (1.001 if is_buy else 0.999)`, clears `self.positions` only for confirmed fills, then reconciles and reports `flattened = not (failures or open_remote or open_local)`, engaging the kill switch on anything left (`:736-746`).

**State it owns.** `self.gate`, `self.exchange`, `self.cfg`, `self.positions: Dict[str, LivePosition]`, `self.now_fn`.

**Side effects.** Network via `asyncio.to_thread` throughout. Mutates `self.positions` and `self.gate.engaged`; `gate.persist()` at `check_pending_fills:513`. No local DB, no file writes.

**Consumers.** `trading/live/executor.py:23,53`, `run.py:258,276,292-294`, `tests/test_live_engine.py:116,271,502,569`.

**Where the live path diverges from what the agents actually call.** `ExecutionAgent` touches **seven** executor members; `LiveExecutor` implements two:

| Agent call site | `PaperTradingEngine` | `LiveExecutor` | Consequence in live |
|---|---|---|---|
| `agents/execution_agent.py:64,71,100` `engine.update_price(symbol, price)` | caches into `_last_prices` | no-op `return None` (`executor.py:56`) | live keeps no local price cache by design |
| `agents/execution_agent.py:96` `engine.get_price(order.symbol)` | resolves cache → `market_store` | **not defined** | `AttributeError` before an order is even built |
| `agents/execution_agent.py:164` `engine.execute_order(order)` | full paper path | `_open`/`_close` | the one path that actually works |
| `agents/execution_agent.py:192` `engine.check_positions()` | SL/TP/liq sweep | **not defined** | `AttributeError` on every 0.3 s loop |
| `agents/execution_agent.py:213,266,290` `engine._last_prices.get(symbol)` | dict exists | **not defined** | `AttributeError` in breakeven / scalp-TP / expiry |
| `agents/execution_agent.py:270,311` `engine.position_manager.close_position(...)` | `PositionManager` | **not defined** | `AttributeError`; and the calls read the **local SQLite** positions, which bypass `SafetyGate` entirely |

The last row is the sharpest divergence: the agent's own close paths (`SCALP_TP`, `SCALP_EXPIRED`) would close *exchange* positions from a *local SQLite* read, with no gate check, no cloid, no reconcile. Live exits are supposed to come from exchange-side trigger orders plus `emergency_flat`, never that route. Separately, `ExecutionAgent.think()` computes `order.stop_loss`/`order.take_profit` at `agents/execution_agent.py:139-140` and calls them "advisory" — true on paper, where `_execute_open` overwrites them, but on live these are the *only* values reaching the exchange, since `LiveExecutor._open` passes `order.stop_loss` straight through (`executor.py:105-106`). Since `think()` calls `self.engine.get_price()` first, live never reaches that line anyway.

#### 9.2.8 `trading/live/executor.py`

**Role.** Adapter that gives `LiveExecutor` the same `execute_order` / `update_price` shape as `PaperTradingEngine`, so `ExecutionAgent` needs no mode branch. All safety lives inside the adapter, as return values — never exceptions into the running loop.

**Key symbols.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `make_cloid` | function | `def make_cloid(prefix: str = "tb") -> str:` → `"{}-{}".format(prefix, uuid.uuid4().hex[:16])` | `trading/live/executor.py:28` |
| `coin_of` | function | `def coin_of(symbol: str) -> str:` — `BTC/USDT:USDT` → `BTC` | `trading/live/executor.py:39` |
| `LiveExecutor` | class | `class LiveExecutor:` | `trading/live/executor.py:50` |
| `__init__` | method | `def __init__(self, engine: LiveEngine):` | `trading/live/executor.py:53` |
| `update_price` | method | `def update_price(self, symbol: str, price: float) -> None:` | `trading/live/executor.py:56` |
| `execute_order` | method | `async def execute_order(self, order) -> Dict[str, Any]:` | `trading/live/executor.py:66` |
| `_open` | method | `async def _open(self, order) -> Dict[str, Any]:` | `trading/live/executor.py:77` |
| `_close` | method | `async def _close(self, order) -> Dict[str, Any]:` | `trading/live/executor.py:127` |
| `_price_for` | static | `def _price_for(order) -> float:` | `trading/live/executor.py:171` |

**Key symbols — `LiveEngine` methods.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `submit_order` | method | `async def submit_order(self, coin: str, symbol: str, is_buy: bool, size: float, price: float, stop_loss: Optional[float] = None, take_profit: Optional[float] = None, leverage: int = 5, is_close: bool = False, cloid: Optional[Any] = None) -> Dict[str, Any]:` | `trading/live/engine.py:204` |
| `health_check` | method | `async def health_check(self) -> Dict[str, Any]:` | `trading/live/engine.py:518` |
| `check_pending_fills` | method | `async def check_pending_fills(self) -> List[Dict[str, Any]]:` | `trading/live/engine.py:421` |
| `emergency_flat` | method | `async def emergency_flat(self, reason: str = "manual") -> Dict[str, Any]:` | `trading/live/engine.py:655` |
| `reconcile` | method | `async def reconcile(self) -> Dict[str, Any]:` | `trading/live/engine.py:74` |

**Path behaviour.** `_open` refuses without SL/TP before anything else (`:84-91`), resolves price via `_price_for` — `order.price`, else `market_store.get_price(coin)`, else a clear `ValueError` rather than sending price `0` (`:170-189`) — then calls `submit_order` with `size=order.quantity or 0.0`, `leverage=order.leverage or 5`. Three outcomes are distinguished: rejected; accepted-but-resting (`protected` falsy ⇒ `position_id: None`); protected-and-filled (`position_id` is the **coin name**, not a numeric id, `:124`). `_close` looks the position up in `engine.positions` by full symbol, prices off `exchange.mid_price` in a thread, refuses hard if that reads `<= 0`, and submits at `price * (1.001 if is_buy else 0.999)` — deliberate 0.1% adverse slippage so the close actually crosses (`:150-153`).

**Divergence from the paper path.** Beyond the interface gaps under `engine.py`, `_open` never calls the risk manager: no `RiskManager` sizing, no `validate_trade`, no ATR gate, no tick guard, no `calculate_fee` anywhere live. Safety is entirely `SafetyGate` + `LiveConfig` ceilings. The consequence is concrete — `DecisionAgent` builds every `Order` without `quantity` (`agents/decision_agent.py:264-271`), so `order.quantity or 0.0` is `0.0` and `SafetyGate` guard **O1** (`request.size <= 0`, `safety.py:319`) rejects every live opening. Paper sizes inside `_execute_open`; live has no equivalent step and no path that fills one in.

**State it owns.** `self.engine: LiveEngine` only.

**Side effects.** Network via `engine.submit_order` / `engine.exchange.mid_price`. Mutates `engine.positions` and `engine.gate` counters. Reads `core.market_store`. No file or DB writes; `trading.models` imported function-locally at `:68`, `:78`.

**Consumers.** `run.py:259,295` — returned from `_build_live_executor()`, assigned as `self.executor` at `run.py:365-368`; `tests/test_live_executor.py:10`.

#### 9.2.9 `trading/live/console.py`

**Role.** Terminal front-end for mode selection and live-limit editing. No menu item turns live on by itself: reaching testnet/mainnet requires typing a confirmation phrase *and* re-typing the wallet address, and the wallet address is always shown first.

**Key symbols.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `CONFIRM_PHRASE` | constant | `CONFIRM_PHRASE = "SAYA MENGERTI"` | `trading/live/console.py:38` |
| `Mode` | alias | `Mode = str` — `"paper"` \| `"testnet"` \| `"mainnet"` | `trading/live/console.py:34` |
| `ModeDecision` | dataclass | `class ModeDecision:` — `mode: Mode`, `private_key=None`, `address=None`, `reason=""`, `refused=False` | `trading/live/console.py:42` |
| `typed_addr_mismatch` | function | `def typed_addr_mismatch(shown: Optional[str], typed: Optional[str]) -> bool:` | `trading/live/console.py:52` |
| `derive_address` | function | `def derive_address(private_key: str) -> Optional[str]:` | `trading/live/console.py:65` |
| `decide_mode` | function | `def decide_mode(choice: str, typed_phrase: str, typed_address: str, shown_address: Optional[str], env: Optional[dict] = None) -> ModeDecision:` | `trading/live/console.py:78` |
| `_out` | function | `def _out(text: str = "") -> None:` | `trading/live/console.py:150` |
| `_read` | function | `def _read(prompt: str) -> str:` | `trading/live/console.py:165` |
| `interactive` | function | `def interactive() -> bool:` | `trading/live/console.py:172` |
| `_pause` | function | `def _pause(enter_continues: bool = True) -> None:` | `trading/live/console.py:187` |
| `_read_secret` | function | `def _read_secret(prompt: str) -> str:` | `trading/live/console.py:208` |
| `limits_summary` | function | `def limits_summary(cfg: LiveConfig) -> List[str]:` | `trading/live/console.py:225` |
| `mode_options` | function | `def mode_options(cfg: LiveConfig, has_key: bool):` | `trading/live/console.py:238` |
| `show_banner` | function | `def show_banner() -> None:` | `trading/live/console.py:261` |
| `_ask_mode_plain` | function | `def _ask_mode_plain(cfg: LiveConfig) -> ModeDecision:` | `trading/live/console.py:287` |
| `_show_wallet_plain` | function | `def _show_wallet_plain(cfg: LiveConfig, mode: str) -> Optional[dict]:` | `trading/live/console.py:340` |
| `_make_phrase_validator` | function | `def _make_phrase_validator(field):` | `trading/live/console.py:357` |
| `_make_address_validator` | function | `def _make_address_validator(field, address: str):` | `trading/live/console.py:364` |
| `_show_wallet` | function | `def _show_wallet(cfg: LiveConfig, mode: str) -> Optional[dict]:` | `trading/live/console.py:372` |
| `ask_mode` | function | `def ask_mode(cfg: LiveConfig) -> ModeDecision:` | `trading/live/console.py:405` |
| `_do_confirmations` | function | `def _do_confirmations(choice: str, info: dict) -> Optional[ModeDecision]:` | `trading/live/console.py:462` |
| `EDITABLE` | constant | `EDITABLE = (...)` — 7 tuples `(field, label, kind, lo, hi)` | `trading/live/console.py:491` |
| `validate_rule` | function | `def validate_rule(field: str, value, kind: str, lo: float, hi: float):` | `trading/live/console.py:505` |
| `check_consistency` | function | `def check_consistency(cfg: LiveConfig) -> List[str]:` | `trading/live/console.py:532` |
| `_format_value` | function | `def _format_value(value) -> str:` | `trading/live/console.py:559` |
| `rule_options` | function | `def rule_options(cfg: LiveConfig):` | `trading/live/console.py:565` |
| `edit_rules` | function | `def edit_rules(cfg: LiveConfig) -> LiveConfig:` | `trading/live/console.py:587` |
| `_edit_field_tui` | function | `def _edit_field_tui(cfg: LiveConfig, field: str, screen) -> None:` | `trading/live/console.py:622` |
| `edit_rules_plain` | function | `def edit_rules_plain(cfg: LiveConfig) -> LiveConfig:` | `trading/live/console.py:654` |
| `_edit_window` | function | `def _edit_window(cfg: LiveConfig, screen=None) -> None:` | `trading/live/console.py:722` |

**`decide_mode` rule order** (pure function, the only thing that actually decides safety — `console.py:94-133`): choice `"1"` ⇒ paper; choice not in `("2","3")` ⇒ paper/refused; `HYPERLIQUID_PRIVATE_KEY` absent from `env` ⇒ paper/refused; `typed_phrase.strip() != "SAYA MENGERTI"` ⇒ paper/refused; `typed_addr_mismatch(shown, typed)` ⇒ paper/refused; otherwise the requested mode with `private_key` and `address`. Every failure returns a populated `ModeDecision`, never `None`.

**Editable limits** — `EDITABLE` (`:491-502`) with the validation range each is held to:

| Field | Label | Kind | Lo | Hi |
|---|---|---|---|---|
| `max_order_notional` | Order maks (USDC) | float | 10.0 | 100_000.0 |
| `max_position_notional` | Posisi maks per simbol (USDC) | float | 10.0 | 1_000_000.0 |
| `max_total_notional` | Total eksposur maks (USDC) | float | 10.0 | 10_000_000.0 |
| `max_daily_orders` | Order per hari | int | 1 | 100_000 |
| `max_daily_loss` | Stop rugi harian (USDC) | float | 1.0 | 1_000_000.0 |
| `min_free_collateral` | Collateral minimum (USDC) | float | 0.0 | 1_000_000.0 |
| `max_consecutive_errors` | Error beruntun sebelum stop | int | 1 | 100 |

`validate_rule` rejects non-numerics, then explicitly rejects `NaN`/`inf` for floats (`:523-524` — a NaN bound compares False forever, so the protection would look present and never fire), then range-checks. `check_consistency` emits three non-blocking warnings: order > position, total < position, daily loss >= total (`:541-555`). `_edit_window` requires `[0,23]` for both hours and rejects `start == end` as an explicit empty window (`:742-747`). `EDITABLE` is a subset of `LiveConfig`: `max_leverage` and `live_window_utc` are shown but only the window is editable.

**State it owns.** None of its own beyond the returned `ModeDecision`; it mutates the passed `LiveConfig` in place via `setattr(cfg, field, parsed)` (`:638`, `:714`). `show_banner` sets `os.environ["HYPERLIQUID_PRIVATE_KEY"]` for the current process only (`:280`) — explicitly not written to `config.yaml`, because that file is committed (`:269-270`, `:592-594`).

**Side effects.** Terminal I/O; ANSI colour constants `GREY/DIM/RESET/RED/GREEN` (`:143-147`); `sys.stdout.reconfigure(encoding="utf-8")` on every `_out` (needed because Windows cp1252 has no box glyphs, `:157-160`); `os.environ` mutation in `show_banner`; `eth_account` import inside `derive_address`. `trading.live.tui` is imported function-locally throughout so the module stays importable headless. No network, no DB.

**Consumers.** `run.py:169,196`; `tests/test_live_console.py:12`, `tests/test_numerics.py:157,165`, `check_behavior.py:157`, `_smoke_console.py:20`.

#### 9.2.10 `trading/live/tui.py`

**Role.** Terminal primitives — arrow-key reading, in-place full-screen redraw, and single-line validated text input. Split out of `console.py` for the same reason `decide_mode` is split out of `ask_mode`: this part is testable without a human.

**Key symbols — key codes and terminal setup.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `UP`/`DOWN`/`LEFT`/`RIGHT`/`ENTER`/`ESC`/`BACKSPACE`/`CTRL_C` | constants | `UP = "up"` … `CTRL_C = "ctrl_c"` | `trading/live/tui.py:33-40` |
| `WIDTH` | constant | `WIDTH = 68` | `trading/live/tui.py:42` |
| `_WINDOWS_KEYS` | constant | dict mapping `msvcrt` prefix bytes → key names | `trading/live/tui.py:45` |
| `NotATerminal` | exception | `class NotATerminal(RuntimeError):` — defined, never raised | `trading/live/tui.py:52` |
| `_vt_enabled` | function | `def _vt_enabled() -> bool:` | `trading/live/tui.py:57` |
| `enable_vt_processing` | function | `def enable_vt_processing() -> bool:` | `trading/live/tui.py:61` |
| `ANSI_CLEAR` … `ANSI_SHOW_CURSOR` | constants | `"\x1b[2J\x1b[H"`, `"\x1b[H"`, `"\x1b[J"`, `"\x1b[K"`, `"\x1b[?25l"`, `"\x1b[?25h"` | `trading/live/tui.py:89-94` |

**Key symbols — classes and runners.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `Screen` | class | `class Screen:` | `trading/live/tui.py:97` |
| `Screen.__init__` | method | `def __init__(self, width: int = WIDTH):` | `trading/live/tui.py:105` |
| `Screen.__enter__` | method | `def __enter__(self) -> "Screen":` | `trading/live/tui.py:109` |
| `Screen.__exit__` | method | `def __exit__(self, *exc) -> None:` | `trading/live/tui.py:113` |
| `Screen.start` | method | `def start(self) -> None:` | `trading/live/tui.py:116` |
| `Screen.stop` | method | `def stop(self) -> None:` | `trading/live/tui.py:121` |
| `Screen.draw` | method | `def draw(self, lines: Sequence[str]) -> None:` | `trading/live/tui.py:132` |
| `Screen.clear` | method | `def clear(self) -> None:` | `trading/live/tui.py:147` |
| `_pad` | function | `def _pad(text: str, width: int) -> str:` | `trading/live/tui.py:151` |
| `display_width` | function | `def display_width(text: str) -> int:` | `trading/live/tui.py:156` |
| `_write` | function | `def _write(text: str) -> None:` | `trading/live/tui.py:175` |
| `is_tty` | function | `def is_tty() -> bool:` | `trading/live/tui.py:184` |
| `_read_windows` | function | `def _read_windows() -> Optional[str]:` | `trading/live/tui.py:191` |
| `_read_posix` | function | `def _read_posix() -> Optional[str]:` | `trading/live/tui.py:213` |
| `KeyReader` | class | `class KeyReader:` | `trading/live/tui.py:239` |
| `KeyReader.__init__` | method | `def __init__(self):` | `trading/live/tui.py:247` |
| `KeyReader.__enter__` | method | `def __enter__(self) -> "KeyReader":` | `trading/live/tui.py:251` |
| `KeyReader.__exit__` | method | `def __exit__(self, *exc) -> None:` | `trading/live/tui.py:256` |
| `KeyReader._enter_raw` | method | `def _enter_raw(self) -> None:` | `trading/live/tui.py:267` |
| `KeyReader.get` | method | `def get(self) -> Optional[str]:` | `trading/live/tui.py:278` |
| `KeyReader.wait` | method | `def wait(self) -> str:` | `trading/live/tui.py:284` |
| `_sleep` | function | `def _sleep() -> None:` | `trading/live/tui.py:293` |
| `Option` | dataclass | `class Option:` — `label, value=None, detail="", enabled=True, danger=False` | `trading/live/tui.py:301` |
| `MenuResult` | dataclass | `class MenuResult:` — `selected: Optional[Option] = None`, `cancelled: bool = False` | `trading/live/tui.py:312` |
| `_box_top` | function | `def _box_top(title: str, width: int = WIDTH) -> str:` | `trading/live/tui.py:317` |
| `_box_bottom` | function | `def _box_bottom(width: int = WIDTH) -> str:` | `trading/live/tui.py:324` |
| `Menu` | class | `class Menu:` | `trading/live/tui.py:328` |
| `Menu.__init__` | method | `def __init__(self, options: Sequence[Option]):` | `trading/live/tui.py:336` |
| `Menu._first_enabled` | method | `def _first_enabled(self) -> int:` | `trading/live/tui.py:341` |
| `Menu._move` | method | `def _move(self, delta: int) -> None:` | `trading/live/tui.py:347` |
| `Menu.step` | method | `def step(self, key: str) -> Optional[MenuResult]:` | `trading/live/tui.py:363` |
| `Menu.render` | method | `def render(self, title: str, footer: str = "", width: int = WIDTH) -> List[str]:` | `trading/live/tui.py:387` |
| `TextField` | class | `class TextField:` | `trading/live/tui.py:427` |
| `TextField.__init__` | method | `def __init__(self, prompt: str, secret: bool = False, max_length: int = 200):` | `trading/live/tui.py:436` |
| `TextField._validate` | method | `def _validate(self, text: str) -> None:` | `trading/live/tui.py:445` |
| `TextField.step` | method | `def step(self, key: str) -> Optional[object]:` | `trading/live/tui.py:448` |
| `TextField.display` | method | `def display(self) -> str:` | `trading/live/tui.py:481` |
| `_Cancelled` | class | `class _Cancelled:` | `trading/live/tui.py:495` |
| `_CANCELLED` | instance | `_CANCELLED = _Cancelled()` | `trading/live/tui.py:502` |
| `run_menu` | function | `def run_menu(title: str, options: Sequence[Option], footer: str = "", screen: Optional[Screen] = None, reader: Optional[KeyReader] = None) -> MenuResult:` | `trading/live/tui.py:506` |
| `run_text` | function | `def run_text(prompt: str, secret: bool = False, max_length: int = 200, screen: Optional[Screen] = None, field: Optional[TextField] = None, reader: Optional[KeyReader] = None) -> object:` | `trading/live/tui.py:526` |
| `is_cancelled` | function | `def is_cancelled(value: object) -> bool:` | `trading/live/tui.py:545` |
| `fallback_prompt` | function | `def fallback_prompt(prompt: str, secret: bool = False) -> str:` | `trading/live/tui.py:550` |
| `render_keymap` | function | `def render_keymap() -> str:` | `trading/live/tui.py:568` |

**Behaviour worth knowing.** `Menu.step` (`:363-385`) splits navigation from rendering; `_move` skips disabled entries so the cursor never lands on a locked row. Accepts `q`/`Q`/`ESC`/`CTRL_C` to cancel, arrows and `j`/`k`, Enter to select, 1-based digit shortcuts. `Menu.render` pads every row itself, so frame widths stay uniform. `TextField` validates **per keystroke** and clears the previous error before revalidating, so a corrected entry is accepted (`:467-474`); Enter on empty sets `error = "kosong"` and does not return. `Screen.draw` does home → padded lines with `ANSI_ERASE_LINE` → `ANSI_ERASE_DOWN`, never a full `2J` per frame, to avoid flicker (`:132-145`); `Screen.stop` always restores the cursor. `enable_vt_processing` uses `ctypes.windll.kernel32`, `GetStdHandle(-11)`, `ENABLE_VT = 0x0004` on `nt`, degrading to a `reconfigure`-probe elsewhere. `KeyReader` enters cbreak via `termios`/`tty` **only on POSIX** — forcing it on Windows breaks the console. `_read_posix` treats EOF as Enter (`:220`). `display_width` strips ANSI first, then counts East-Asian `W`/`F` as two cells.

**State it owns.** `Screen.width`/`Screen.active`; `KeyReader._raw` saved termios and `KeyReader._posix`; `Menu.options`/`Menu.index`/`Menu._last_drawn`; `TextField.prompt`/`secret`/`max_length`/`value`/`message`/`error`.

**Side effects.** Terminal only: `sys.stdout.write` + `flush` (swallowing `BrokenPipeError`/`ValueError`), `msvcrt`/`termios`/`tty`/`select`/`os.read` on stdin, and `SetConsoleMode` on the Windows console handle. No network, no files, no DB. `console.py` reaches into two privates by design: `tui._write` at `console.py:199` and `field._validate = ...` assignment at `console.py:469,476,641`.

**Consumers.** `trading/live/console.py` (nine function-local imports: `:180,196,240,417,464,567,596,624,664`; direct use of `tui.is_tty`, `tui.enable_vt_processing`, `tui.Screen`, `tui.run_menu`, `tui.run_text`, `tui.TextField`, `tui.is_cancelled`, `tui.Option`, `tui.render_keymap`); `tests/test_live_tui.py:13,14`, `tests/test_live_console.py`.

#### 9.2.11 `trading/__init__.py`

**Role.** Package marker only — `"""Trading package — mesin paper trading, posisi, risiko."""`, no exports (`trading/__init__.py:1`).

---

### 9.3 Data ingestion and persistence (`data/`, `database/`)

#### 9.3.1 `data/price_feed.py`

**Role.** Unified futures price façade: four-tier cascade (Hyperliquid → Binance Futures via ccxt → yfinance → CoinGecko) for OHLCV, tickers, funding, mark price and L2 depth, plus a background `price_update_loop` that back-fills symbols the WebSocket is not covering. Only exchange-sourced candles (Tier 0/1) are persisted; yfinance output is display-only.

**Key symbols — module-level.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `COINGECKO_MAP` | constant | `Dict[str, str]` — 20 base→CoinGecko-id entries | `data/price_feed.py:31` |
| `SPECIAL_YF_MAP` | constant | `Dict[str, str]` — `SUI/PEPE/APT/UNI/SHIB` → yfinance exchange-specific tickers | `data/price_feed.py:54` |
| `VERIFIED_FUTURES` | constant | `set[str]` — 20 symbols with verified feed/liquidity (incl. `ATOM`, `INJ`, `HYPE` absent from other maps) | `data/price_feed.py:63` |
| `_symbol_to_yf` | function | `def _symbol_to_yf(symbol: str) -> str` | `data/price_feed.py:69` |
| `_symbol_to_base` | function | `def _symbol_to_base(symbol: str) -> str` | `data/price_feed.py:77` |
| `_active_price_feed` | global | `Optional["PriceFeed"] = None` — module singleton set by `__init__` | `data/price_feed.py:700` |
| `get_price_feed` | function | `def get_price_feed() -> Optional["PriceFeed"]` | `data/price_feed.py:703` |
| `logger` | global | `Logger` bound to channel `"price_feed"` (CYAN) | `data/price_feed.py:29` |

**Key symbols — `class PriceFeed` public methods.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `PriceFeed.__init__` | method | `def __init__(self, event_bus: EventBus)` | `data/price_feed.py:93` |
| `initialize` | coroutine | `async def initialize(self)` | `data/price_feed.py:109` |
| `close` | coroutine | `async def close(self)` | `data/price_feed.py:136` |
| `start_streaming` | coroutine | `async def start_streaming(self)` | `data/price_feed.py:146` |
| `fetch_ohlcv` | coroutine | `async def fetch_ohlcv(self, symbol: str, timeframe: str = "1m", limit: int = 100, save_to_db: bool = True) -> List[list]` | `data/price_feed.py:198` |
| `fetch_ticker` | coroutine | `async def fetch_ticker(self, symbol: str) -> Optional[dict]` | `data/price_feed.py:340` |
| `fetch_funding_rate` | coroutine | `async def fetch_funding_rate(self, symbol: str) -> Optional[dict]` | `data/price_feed.py:417` |
| `fetch_mark_price` | coroutine | `async def fetch_mark_price(self, symbol: str) -> Optional[float]` | `data/price_feed.py:481` |
| `fetch_order_book` | coroutine | `async def fetch_order_book(self, symbol: str, limit: int = 5) -> Optional[dict]` | `data/price_feed.py:515` |
| `fetch_all_tickers` | coroutine | `async def fetch_all_tickers(self) -> Dict[str, dict]` | `data/price_feed.py:557` |
| `price_update_loop` | coroutine | `async def price_update_loop(self)` | `data/price_feed.py:566` |
| `stop` | method | `def stop(self)` | `data/price_feed.py:607` |
| `update_symbols` | method | `def update_symbols(self, new_symbols: List[str])` | `data/price_feed.py:611` |
| `discover_top_volume_symbols` | coroutine | `async def discover_top_volume_symbols(self, limit: int = 10) -> List[str]` | `data/price_feed.py:616` |
| `get_last_price` | method | `def get_last_price(self, symbol: str) -> Optional[float]` | `data/price_feed.py:689` |
| `get_all_last_prices` | method | `def get_all_last_prices(self) -> Dict[str, float]` | `data/price_feed.py:693` |

**Key symbols — `class PriceFeed` private methods.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `_fetch_ohlcv_yf` | coroutine | `async def _fetch_ohlcv_yf(self, symbol: str, timeframe: str = "1m", limit: int = 100) -> List[list]` | `data/price_feed.py:160` |
| `_fetch_ticker_yf` | coroutine | `async def _fetch_ticker_yf(self, symbol: str) -> Optional[dict]` | `data/price_feed.py:273` |
| `_fetch_ticker_coingecko` | coroutine | `async def _fetch_ticker_coingecko(self, symbol: str) -> Optional[dict]` | `data/price_feed.py:305` |

**State it owns.**

| Field | Type | Meaning | Loc |
|---|---|---|---|
| `self.event_bus` | `EventBus` | injected bus used for `PRICE_UPDATE` broadcast | `data/price_feed.py:94` |
| `self.config` | `Config` | `get_config()`; `config.symbols` is mutated in place by `update_symbols` | `data/price_feed.py:95` |
| `self._exchange` | `Optional[ccxt.binance]` | Tier 1 client; `None` until `initialize`, again after `close` | `data/price_feed.py:96` |
| `self._running` | `bool` | loop flag for `price_update_loop` | `data/price_feed.py:97` |
| `self._last_prices` | `Dict[str, float]` | last price per symbol, also read by `fetch_mark_price` fallback | `data/price_feed.py:98` |
| `self._ccxt_available` | `bool` | sticky kill-switch; set `False` on first ccxt exception, disables Tier 1 permanently | `data/price_feed.py:99` |
| `self.hyperliquid` | `HyperliquidFeed` | Tier 0 transport, constructed with the same bus | `data/price_feed.py:101` |
| `self._hl_coins` | `set` | non-delisted perp names, gates every Tier 0 branch | `data/price_feed.py:102` |
| `_active_price_feed` (module) | `Optional[PriceFeed]` | mutated inside `__init__` at line 107 | `data/price_feed.py:700` |

**Side effects.** Network: `ccxt.binance` with `enableRateLimit=True`, `timeout=4000` ms, `defaultType="future"` (`:124`); `asyncio.wait_for` caps of 4.0 s on `fetch_ohlcv`, 3.0 s on `fetch_ticker`/`fetch_funding_rate`/`fetch_mark_price`, 2.0 s on `fetch_order_book`, 5.0 s on discovery `fetch_tickers`. CoinGecko `…/simple/price?ids=…&vs_currencies=usd&include_24hr_vol=true&include_24hr_change=true` (`:312`, 4 s) and `…/coins/markets?vs_currency=usd&order=volume_desc&per_page=50&page=1` (`:652`, 5 s), both `User-Agent: Mozilla/5.0` via `urllib.request`. yfinance runs in `asyncio.to_thread`, interval→period map `1m→("1m","1d")`, `5m→("5m","5d")`, `15m→("15m","5d")`, `1h→("1h","1mo")`, `1d→("1d","1y")`, default `("5m","5d")` (`:166`). DB: lazy `get_db()` + `Repository.insert_candles_batch` (`:240-267`), only when `persist`; each bar re-checked against OHLC invariants before insert. Global mutation: `market_store.set_ticker` (`:395`), `set_funding` (`:447`), `set_order_book` (`:530`, `:549`), `set_price` (`:587`); `event_bus.publish(Channels.PRICE_UPDATE, …, source="price_feed")` (`:400`); module global `_active_price_feed` (`:107`). Cadence: loop sleeps 1.5 s per iteration, 0.15 s between per-symbol refetches, refetches any symbol with `market_store.get_price_age(s) > 10.0` s (`:597`). No cache TTL of its own — freshness delegated to `market_store`.

**Consumers.** `run.py:54` (construct, `initialize`, `discover_top_volume_symbols`, `start_streaming`, `fetch_ohlcv(..., save_to_db=True)`, `update_symbols` at `run.py:498`); `agents/analysis_agent.py:14` (`fetch_ohlcv(symbol, timeframe="5m", limit=200)` at `:71`); `dashboard/callbacks/update_callbacks.py:958` (`get_price_feed()` for WS channel counters).

#### 9.3.2 `data/hyperliquid_feed.py`

**Role.** Tier 0 market transport for the Hyperliquid perp DEX — REST `/info` for snapshots and history, `wss://…/ws` for streaming orderbook/mid/candle/funding/trade channels. No credentials, no orders, no computation: the only kernel handoff is `microstructure.ingest_l2` per L2 frame.

**Key symbols — module-level.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `HL_REST_URL` | constant | `str = "https://api.hyperliquid.xyz/info"` | `data/hyperliquid_feed.py:46` |
| `HL_WS_URL` | constant | `str = "wss://api.hyperliquid.xyz/ws"` | `data/hyperliquid_feed.py:47` |
| `WS_MAX_SIZE` | constant | `int = 2 ** 22` (4 MiB frame cap; `allMids` carries 1000+ entries) | `data/hyperliquid_feed.py:50` |
| `WS_PING_INTERVAL` | constant | `float = 30.0` — application-level JSON ping, not protocol ping | `data/hyperliquid_feed.py:53` |
| `WS_RECV_TIMEOUT` | constant | `float = 60.0` — no message in 60 s ⇒ reconnect | `data/hyperliquid_feed.py:56` |
| `coin_to_symbol` | function | `def coin_to_symbol(coin: str) -> str` → `"BTC/USDT:USDT"` | `data/hyperliquid_feed.py:59` |
| `symbol_to_coin` | function | `def symbol_to_coin(symbol: str) -> str` → `"BTC"` | `data/hyperliquid_feed.py:64` |

**Key symbols — `class HyperliquidFeed` methods (all of them).**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `__init__` | method | `def __init__(self, event_bus=None)` | `data/hyperliquid_feed.py:84` |
| `_ensure_native_kernel` | method | `def _ensure_native_kernel(self) -> bool` | `data/hyperliquid_feed.py:99` |
| `_post_info_sync` | method | `def _post_info_sync(self, payload: dict, timeout: float = 8.0)` | `data/hyperliquid_feed.py:126` |
| `post_info` | coroutine | `async def post_info(self, payload: dict, timeout: float = 8.0)` | `data/hyperliquid_feed.py:139` |
| `fetch_universe` | coroutine | `async def fetch_universe(self) -> List[dict]` | `data/hyperliquid_feed.py:143` |
| `discover_top_volume_symbols` | coroutine | `async def discover_top_volume_symbols(self, limit: int = 10) -> List[str]` | `data/hyperliquid_feed.py:161` |
| `fetch_all_mids` | coroutine | `async def fetch_all_mids(self) -> Dict[str, float]` | `data/hyperliquid_feed.py:203` |
| `fetch_l2_book` | coroutine | `async def fetch_l2_book(self, coin: str, depth: int = 20) -> Optional[dict]` | `data/hyperliquid_feed.py:236` |
| `fetch_candles` | coroutine | `async def fetch_candles(self, coin: str, interval: str = "1m", limit: int = 120) -> List[list]` | `data/hyperliquid_feed.py:253` |
| `_parse_book` | staticmethod | `def _parse_book(coin: str, levels: list, ts: Optional[int]) -> dict` | `data/hyperliquid_feed.py:313` |
| `_parse_candle` | staticmethod | `def _parse_candle(data: dict) -> Optional[dict]` | `data/hyperliquid_feed.py:353` |
| `set_watched_coins` | method | `def set_watched_coins(self, symbols: List[str])` | `data/hyperliquid_feed.py:373` |
| `_subscriptions` | method | `def _subscriptions(self) -> List[dict]` | `data/hyperliquid_feed.py:377` |
| `_subscribe` | coroutine | `async def _subscribe(self, ws)` | `data/hyperliquid_feed.py:393` |
| `_ping_loop` | coroutine | `async def _ping_loop(self, ws)` | `data/hyperliquid_feed.py:402` |
| `_handle_message` | method | `def _handle_message(self, raw: str)` | `data/hyperliquid_feed.py:411` |
| `websocket_loop` | coroutine | `async def websocket_loop(self)` | `data/hyperliquid_feed.py:492` |
| `stop` | method | `def stop(self)` | `data/hyperliquid_feed.py:553` |
| `is_connected` | method | `def is_connected(self) -> bool` | `data/hyperliquid_feed.py:560` |
| `get_status` | method | `def get_status(self) -> dict` | `data/hyperliquid_feed.py:563` |

**State it owns.**

| Field | Type | Meaning | Loc |
|---|---|---|---|
| `self.event_bus` | `Optional[EventBus]` | stored but never published to; all fan-out goes through `market_store` | `data/hyperliquid_feed.py:85` |
| `self._running` | `bool` | WS loop stop flag | `data/hyperliquid_feed.py:86` |
| `self._ws_connected` | `bool` | live socket flag | `data/hyperliquid_feed.py:87` |
| `self._known_coins` | `Set[str]` | perps from `{"type":"meta"}`, excludes `isDelisted`; filters REST `allMids` noise (spot, `#12090` prediction tokens) | `data/hyperliquid_feed.py:88` |
| `self._watched_coins` | `Set[str]` | per-coin subscriptions; 4 channels each | `data/hyperliquid_feed.py:89` |
| `self._last_mid_time` | `float` | unix ts of last `allMids` frame, surfaced as `mid_age_s` | `data/hyperliquid_feed.py:90` |
| `self._last_book_time` | `float` | unix ts of last `l2Book` frame, surfaced as `book_age_s` | `data/hyperliquid_feed.py:91` |
| `self._msg_counts` | `Dict[str, int]` | per-channel message counters for HUD diagnostics | `data/hyperliquid_feed.py:92` |
| `self._rest_failures` | `int` | reset to 0 on every successful WS connect; not read for control flow | `data/hyperliquid_feed.py:93` |
| `self._native_kernel` | `bool` | latched once; kernel is never swapped mid-session | `data/hyperliquid_feed.py:94` |

**Side effects.** REST `POST https://api.hyperliquid.xyz/info` via `urllib.request` on `asyncio.to_thread`, default `timeout=8.0` s, headers `Content-Type: application/json` + `User-Agent: Mozilla/5.0` (`:126-137`). Payload types: `meta`, `metaAndAssetCtxs`, `allMids`, `l2Book`, `candleSnapshot`. `fetch_candles` derives a window of `span * (limit + 2)`, `span = {m:60_000, h:3_600_000, d:86_400_000}[interval[-1]] * int(interval[:-1])`, and deliberately does **not** truncate the response (`:273-276`). WS connect: `open_timeout=10`, `ping_interval=None` (app-level ping every 30 s), `max_size=4 MiB`, recv timeout 60 s (`:517-533`). Reconnect backoff 1.0 s, doubling, cap 30.0 s, reset on connect (`:525`, `:547-548`). Subscriptions per connection = 1 global `allMids` + 4 per watched coin (`l2Book`, `candle` with hardcoded `interval:"1m"`, `activeAssetCtx`, `trades`) (`:385-391`). `market_store` writes: `set_price` (`:439`), `set_order_book` (`:453`), `set_live_candle` (`:467`), `set_funding` (`:479`), `set_open_interest` (`:481`), `set_recent_trades` (`:490`). Kernel: `microstructure.initialize_native_kernel()` once before the first frame (`:115`; raises if `TRADEBOT_KERNEL=cpp` was requested) and `microstructure.ingest_l2(symbol, bids, asks)` per L2 frame (`:458`). `json.loads` runs in `asyncio.to_thread` so the 0.3 s decision tick is not stalled (`:534`).

**Consumers.** `data/price_feed.py:27` — the only production importer (`HyperliquidFeed`, `symbol_to_coin`, `coin_to_symbol`). Reaches `core/market_store.py` and `core/microstructure.py`. Read as text by `tests/test_bugfixes.py:1105`, `tests/test_cpp_kernel.py:345`.

#### 9.3.3 `data/macro_fetcher.py`

**Role.** Pulls macro context from FRED (key required) and the Forex Factory weekly calendar mirror (no key), then scores it into a BULLISH/BEARISH/NEUTRAL market bias with a risk level.

**Key symbols.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `FRED_SERIES` | constant | `Dict[str, str]` — 6 series→indicator names: `DFF→FED_FUNDS_RATE`, `CPIAUCSL→CPI`, `DTWEXBGS→DXY`, `UNRATE→UNEMPLOYMENT`, `T10Y2Y→YIELD_CURVE`, `VIXCLS→VIX` | `data/macro_fetcher.py:21` |
| `MacroFetcher.__init__` | method | `def __init__(self, fred_api_key: str = None)` | `data/macro_fetcher.py:38` |
| `fetch_fred_data` | coroutine | `async def fetch_fred_data(self) -> List[MacroData]` | `data/macro_fetcher.py:42` |
| `_fetch_fred_series` | method | `def _fetch_fred_series(self, series_id: str) -> Optional[dict]` | `data/macro_fetcher.py:73` |
| `fetch_forex_factory_calendar` | coroutine | `async def fetch_forex_factory_calendar(self) -> List[dict]` | `data/macro_fetcher.py:104` |
| `fetch_all` | coroutine | `async def fetch_all(self) -> dict` | `data/macro_fetcher.py:137` |
| `interpret_macro_context` | method | `def interpret_macro_context(self, fred_data: List[MacroData], calendar_events: List[dict]) -> dict` | `data/macro_fetcher.py:149` |

**State it owns.** `self.fred_api_key: Optional[str]` (`data/macro_fetcher.py:39`) — empty key makes `fetch_fred_data` return `[]` immediately; `self.config: Config` (`:40`) — held but never read. No cache, no TTL, no retry.

**Side effects.** `GET https://api.stlouisfed.org/fred/series/observations` with `series_id`, `api_key`, `file_type=json`, `sort_order=desc`, `limit=1`, `requests.get(..., timeout=15)` — one request per series, 6 total, serially through `loop.run_in_executor` (`:56`, `:84`). Non-200 or empty observations return `None`; FRED's `"."` placeholder for unreleased data counts as missing (`:96`). `GET https://nfs.faireconomy.media/ff_calendar_thisweek.json`, `timeout=15` (`:109`, `:114`), filtered to `impact in ("high","medium")` **and** `country == "USD"` (`:124-128`). `fetch_all` gathers both sources concurrently (`:142`). No DB writes. Scoring: fed funds >5.0 bearish +2 / <3.0 bullish +2; DXY >105 bearish +1 / <100 bullish +1; VIX >25 bearish +1 / <15 bullish +1; ≥3 `impact=="High"` events ⇒ HIGH risk (or MEDIUM at ≥1); bias BULLISH at net ≥2, BEARISH at net ≤−2, else NEUTRAL (`:165-209`).

**Consumers.** `agents/analysis_agent.py:15` — `fetch_all()` at `:205`, `interpret_macro_context(...)` at `:206`; `run.py:55` constructs it.

#### 9.3.4 `data/news_fetcher.py`

**Role.** Aggregates crypto headlines from configured RSS feeds plus the optional CryptoPanic API, producing de-duplicated `NewsItem` records for the news agent to score and persist.

**Key symbols.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `NewsFetcher.__init__` | method | `def __init__(self)` | `data/news_fetcher.py:27` |
| `fetch_rss` | coroutine | `async def fetch_rss(self) -> List[NewsItem]` | `data/news_fetcher.py:31` |
| `_parse_rss_feed` | coroutine | `async def _parse_rss_feed(self, url: str) -> List[NewsItem]` | `data/news_fetcher.py:44` |
| `fetch_cryptopanic` | coroutine | `async def fetch_cryptopanic(self) -> List[NewsItem]` | `data/news_fetcher.py:82` |
| `fetch_all` | coroutine | `async def fetch_all(self) -> List[NewsItem]` | `data/news_fetcher.py:125` |
| `clear_cache` | method | `def clear_cache(self)` | `data/news_fetcher.py:136` |
| `_extract_source_name` | staticmethod | `def _extract_source_name(url: str) -> str` | `data/news_fetcher.py:144` |

**State it owns.** `self.config: Config` (`:28`) — reads `config.news_rss`, `config.cryptopanic_url`, `config.cryptopanic_token`. `self._seen_titles: set` (`:29`) — in-process de-dup cache, trimmed by `clear_cache` to the last 200 titles once it exceeds 500 (`:139-141`); `NewsAgent` calls it after every `fetch_all`. No TTL: unbounded between calls and process-local, so a restart re-admits every headline. Durable de-dup is `Repository.news_exists(title, source)`.

**Side effects.** RSS: `feedparser.parse(url)` in a thread-pool executor per `config.news_rss` entry — defaults `https://www.coindesk.com/arc/outboundfeeds/rss/` and `https://cointelegraph.com/rss` (`core/config.py:400-403`); max 20 entries per feed (`:54`); `summary` stripped of HTML via `re.sub(r"<[^>]+>", "", …)` and cut to 500 chars (`:69`). CryptoPanic: `GET {config.cryptopanic_url}?auth_token={token}&public=true&filter=important`, `requests.get(..., timeout=10)`, first 20 results (`:90-103`); a missing token is a debug log plus empty list, not an error (`:85-87`). `_extract_source_name` maps `coindesk`/`cointelegraph`/`decrypt` substrings to fixed labels, else netloc minus `www.` (`:146-154`). `fetch_all` gathers both sources concurrently (`:130`); per-source failure is logged and the other source still returns (`:40`, `:122`). No DB writes, no event-bus publish.

**Consumers.** `agents/news_agent.py:13` — constructed at `:33`, `fetch_all()` at `:38`, `clear_cache()` at `:39`.

#### 9.3.5 `data/sentiment.py`

**Role.** Two-tier local sentiment: VADER for per-headline real-time scoring and FinBERT (`ProsusAI/finbert`, CPU) for batch rescoring, plus a cross-item aggregator.

**Key symbols.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `SentimentAnalyzer.__init__` | method | `def __init__(self, use_finbert: bool = True)` | `data/sentiment.py:23` |
| `initialize` | coroutine | `async def initialize(self)` | `data/sentiment.py:29` |
| `_load_vader` | method | `def _load_vader(self)` | `data/sentiment.py:50` |
| `_load_finbert` | method | `def _load_finbert(self)` | `data/sentiment.py:55` |
| `analyze_vader` | method | `def analyze_vader(self, text: str) -> Dict` | `data/sentiment.py:65` |
| `analyze_vader_async` | coroutine | `async def analyze_vader_async(self, text: str) -> Dict` | `data/sentiment.py:93` |
| `analyze_finbert` | method | `def analyze_finbert(self, text: str) -> Dict` | `data/sentiment.py:98` |
| `analyze_finbert_async` | coroutine | `async def analyze_finbert_async(self, text: str) -> Dict` | `data/sentiment.py:133` |
| `analyze_finbert_batch` | coroutine | `async def analyze_finbert_batch(self, texts: List[str]) -> List[Dict]` | `data/sentiment.py:138` |
| `aggregate_sentiment` | method | `def aggregate_sentiment(self, sentiments: List[Dict]) -> Dict` | `data/sentiment.py:175` |

**State it owns.** `self._vader = None` (`:24`) — `SentimentIntensityAnalyzer`, or `None` before `initialize`. `self._finbert_pipeline = None` (`:25`) — HuggingFace pipeline. `self._use_finbert: bool` (`:26`). `self._finbert_loaded: bool` (`:27`) — gate for the FinBERT methods; a failed load leaves it `False` and every call degrades to `{"score": 0.0, "label": "NEUTRAL", "confidence": 0.0}`.

**Side effects.** Model loads at `initialize` on the default executor: `vaderSentiment.vaderSentiment.SentimentIntensityAnalyzer` (`:52-53`) and `transformers.pipeline("sentiment-analysis", model="ProsusAI/finbert", tokenizer="ProsusAI/finbert", device=-1)` (`:57-63`, CPU only, ~500 MB). VADER labels POSITIVE at `compound >= 0.05`, NEGATIVE at `<= -0.05`, else NEUTRAL; unloaded state returns an all-zero dict with `neu=1` (`:73-83`). FinBERT truncates each text to 512 characters (`:110`, `:150`) and maps `positive→+conf`, `negative→-conf`, else `0.0`. `aggregate_sentiment` prefers `compound` then falls back to `score`, rounds the mean to 4 dp, labels POSITIVE at `avg >= 0.1` / NEGATIVE at `<= -0.1` (`:190-206`). No network at inference, no file writes beyond the HF cache, no event-bus or DB access.

**Consumers.** `agents/news_agent.py:14` (`analyze_vader_async` at `:63`, `aggregate_sentiment` at `:81`); `agents/analysis_agent.py:16` (`analyze_finbert_batch` at `:246`, run on the ~15-minute batch cadence); `run.py:56` constructs it with `use_finbert=True` (`:217`) and calls `initialize()` (`:342`).

#### 9.3.6 `database/db.py`

**Role.** Owns the single async SQLite connection: WAL pragmas, idempotent schema creation, and a thin `execute`/`fetch*` surface that `Repository` builds on. `Database` never auto-commits — every caller commits explicitly.

**Key symbols — module-level.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `SCHEMA_SQL` | constant | `str` — `CREATE TABLE IF NOT EXISTS` for 10 tables + 8 `CREATE INDEX IF NOT EXISTS` | `database/db.py:13` |
| `_db` | global | `_db: Database = None` — module singleton | `database/db.py:236` |
| `get_db` | coroutine | `async def get_db() -> Database` | `database/db.py:239` |
| `init_db` | coroutine | `async def init_db() -> Database` | `database/db.py:248` |
| `close_db` | coroutine | `async def close_db()` | `database/db.py:253` |
| `logger` | global | `Logger` bound to channel `"database"` | `database/db.py:10` |

**Key symbols — `class Database` (all members).**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `__init__` | method | `def __init__(self, db_path: str = None)` | `database/db.py:160` |
| `connect` | coroutine | `async def connect(self)` | `database/db.py:166` |
| `close` | coroutine | `async def close(self)` | `database/db.py:196` |
| `conn` | property | `def conn(self) -> aiosqlite.Connection` — raises `RuntimeError("Database belum terhubung. Panggil connect() dulu.")` when `None` | `database/db.py:204` |
| `execute` | coroutine | `async def execute(self, sql: str, params: tuple = None)` | `database/db.py:210` |
| `executemany` | coroutine | `async def executemany(self, sql: str, params_list: list)` | `database/db.py:216` |
| `fetchone` | coroutine | `async def fetchone(self, sql: str, params: tuple = None)` | `database/db.py:220` |
| `fetchall` | coroutine | `async def fetchall(self, sql: str, params: tuple = None)` | `database/db.py:225` |
| `commit` | coroutine | `async def commit(self)` | `database/db.py:230` |

**State it owns.** `self.db_path: str` (`database/db.py:163`), defaulting to `get_config().database_path` = `data_store/trading_bot.db` (`core/config.py:386`). `self._connection: aiosqlite.Connection = None` (`:164`). Module global `_db` (`:236`) is the one connection shared process-wide; `get_db` lazily constructs and connects on first call, `close_db` nulls it so a later `get_db` reconnects.

**Side effects.** `connect()` creates the parent directory via `Path(...).parent.mkdir(parents=True, exist_ok=True)`, opens the file, runs `PRAGMA journal_mode=WAL` and `PRAGMA busy_timeout=15000` (15 s, raised from 5000 ms after 1150 recorded lock failures that crashed the execution loop, `database/db.py:176-187`), sets `row_factory = aiosqlite.Row`, then `executescript(SCHEMA_SQL)` + `commit()` (`:188-192`). Everything is `IF NOT EXISTS`, so `connect()` doubles as the migration — a new column needs a manual `ALTER TABLE` alongside the `SCHEMA_SQL` edit.

**Schema, table by table.**

| Table | Columns (beyond `id INTEGER PRIMARY KEY AUTOINCREMENT`) | Constraints / indexes | Loc |
|---|---|---|---|
| `candles` | `symbol TEXT NOT NULL`, `timeframe TEXT NOT NULL`, `timestamp INTEGER NOT NULL` (unix ms), `open/high/low/close REAL NOT NULL`, `volume REAL NOT NULL`, `created_at TEXT DEFAULT (datetime('now'))` | `UNIQUE(symbol, timeframe, timestamp)`; `idx_candles_lookup(symbol, timeframe, timestamp)` | `database/db.py:14-27` |
| `positions` | `symbol TEXT NOT NULL`, `side TEXT NOT NULL`, `entry_price REAL NOT NULL`, `quantity REAL NOT NULL`, `leverage INTEGER NOT NULL DEFAULT 1`, `margin REAL NOT NULL`, `liquidation_price REAL NOT NULL`, `stop_loss REAL`, `take_profit REAL`, `unrealized_pnl REAL DEFAULT 0`, `status TEXT NOT NULL DEFAULT 'OPEN'`, `opened_at TEXT DEFAULT (datetime('now'))`, `closed_at TEXT`, `close_price REAL`, `realized_pnl REAL`, `close_reason TEXT`, `reasoning TEXT` | `idx_positions_status(status, symbol)` | `database/db.py:29-49` |
| `trades` | `position_id INTEGER REFERENCES positions(id)`, `symbol TEXT NOT NULL`, `side TEXT NOT NULL`, `price REAL NOT NULL`, `quantity REAL NOT NULL`, `fee REAL NOT NULL DEFAULT 0`, `fee_type TEXT DEFAULT 'TAKER'`, `trade_type TEXT NOT NULL`, `executed_at TEXT DEFAULT (datetime('now'))` | `idx_trades_time(executed_at)` | `database/db.py:51-63` |
| `signals` | `symbol TEXT NOT NULL`, `timestamp TEXT DEFAULT (datetime('now'))`, `signal_type TEXT NOT NULL`, `signal_value TEXT NOT NULL` (JSON string), `direction TEXT`, `confidence REAL`, `source TEXT` | `idx_signals_time(symbol, timestamp)` | `database/db.py:65-75` |
| `news` | `title TEXT NOT NULL`, `source TEXT NOT NULL`, `url TEXT`, `published_at TEXT`, `fetched_at TEXT DEFAULT (datetime('now'))`, `sentiment_vader REAL`, `sentiment_finbert REAL`, `sentiment_label TEXT`, `impact_level TEXT DEFAULT 'LOW'`, `content_summary TEXT` | `idx_news_time(fetched_at)` | `database/db.py:77-90` |
| `agent_logs` | `agent_name TEXT NOT NULL`, `action TEXT NOT NULL`, `reasoning TEXT NOT NULL`, `input_data TEXT`, `output_data TEXT`, `timestamp TEXT DEFAULT (datetime('now'))` | `idx_agent_logs_time(agent_name, timestamp)` | `database/db.py:92-101` |
| `account` | `balance REAL NOT NULL`, `initial_balance REAL NOT NULL`, `total_pnl REAL DEFAULT 0`, `total_trades INTEGER DEFAULT 0`, `winning_trades INTEGER DEFAULT 0`, `losing_trades INTEGER DEFAULT 0`, `max_drawdown REAL DEFAULT 0`, `peak_balance REAL`, `sharpe_ratio REAL`, `profit_factor REAL`, `updated_at TEXT DEFAULT (datetime('now'))` | none — single-row-by-convention; every write targets `MAX(id)` | `database/db.py:103-116` |
| `balance_history` | `balance REAL NOT NULL`, `unrealized_pnl REAL DEFAULT 0`, `equity REAL NOT NULL`, `timestamp TEXT DEFAULT (datetime('now'))` | `idx_balance_time(timestamp)` | `database/db.py:118-125` |
| `direction_snapshots` | `symbol TEXT NOT NULL`, `prob_long REAL NOT NULL`, `prob_short REAL NOT NULL`, `direction TEXT NOT NULL`, `confidence REAL NOT NULL`, `z_composite REAL DEFAULT 0`, `agent_breakdown TEXT` (JSON verdicts), `diffusion TEXT` (JSON `time_horizons`/`long_probs`/`short_probs`), `created_at TEXT DEFAULT (datetime('now'))` | `idx_dir_snap_symbol(symbol, id)` | `database/db.py:131-143` |
| `macro_data` | `indicator TEXT NOT NULL`, `value REAL NOT NULL`, `period TEXT`, `source TEXT NOT NULL`, `fetched_at TEXT DEFAULT (datetime('now'))` | `UNIQUE(indicator, period)` — the UPSERT target | `database/db.py:145-153` |

All `datetime('now')` defaults are UTC, so `get_daily_realized_pnl`'s `date('now')` and `date(closed_at)` agree without conversion (`database/repository.py:536-538`).

**Consumers.** `run.py:52` (`init_db`, `close_db`, `get_db`); `agents/base_agent.py:15`; `trading/paper_engine.py:16`; `trading/position_manager.py:16`; `reset_paper_db.py:5`; `test_live_session.py:10`; `data/price_feed.py:240` (lazy `get_db`); `database/repository.py:7`.

#### 9.3.7 `database/models.py`

**Role.** Plain dataclasses mirroring the SQL tables, used as typed carriers between fetcher, agents and `Repository`. No behaviour, no validation.

**Key symbols.** All are `@dataclass`; field order below is the declared order and matches the `INSERT` column lists in `repository.py`.

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `Candle` | class | `symbol: str`, `timeframe: str`, `timestamp: int  # Unix ms`, `open: float`, `high: float`, `low: float`, `close: float`, `volume: float`, `id: Optional[int] = None`, `created_at: Optional[str] = None` | `database/models.py:11` |
| `Position` | class | `symbol: str`, `side: str  # 'LONG' atau 'SHORT'`, `entry_price: float`, `quantity: float`, `leverage: int`, `margin: float`, `liquidation_price: float`, `stop_loss: Optional[float] = None`, `take_profit: Optional[float] = None`, `unrealized_pnl: float = 0.0`, `status: str = "OPEN"`, `opened_at: Optional[str] = None`, `closed_at: Optional[str] = None`, `close_price: Optional[float] = None`, `realized_pnl: Optional[float] = None`, `close_reason: Optional[str] = None`, `reasoning: Optional[str] = None`, `id: Optional[int] = None` | `database/models.py:25` |
| `Trade` | class | `symbol: str`, `side: str  # 'BUY' atau 'SELL'`, `price: float`, `quantity: float`, `trade_type: str  # 'OPEN', 'CLOSE', 'LIQUIDATION'`, `position_id: Optional[int] = None`, `fee: float = 0.0`, `fee_type: str = "TAKER"`, `executed_at: Optional[str] = None`, `id: Optional[int] = None` | `database/models.py:47` |
| `Signal` | class | `symbol: str`, `signal_type: str  # 'TECHNICAL', 'ML', 'SENTIMENT', 'MACRO'`, `signal_value: str  # JSON string`, `direction: Optional[str] = None`, `confidence: Optional[float] = None`, `source: Optional[str] = None`, `timestamp: Optional[str] = None`, `id: Optional[int] = None` | `database/models.py:61` |
| `NewsItem` | class | `title: str`, `source: str`, `url: Optional[str] = None`, `published_at: Optional[str] = None`, `fetched_at: Optional[str] = None`, `sentiment_vader: Optional[float] = None`, `sentiment_finbert: Optional[float] = None`, `sentiment_label: Optional[str] = None`, `impact_level: str = "LOW"`, `content_summary: Optional[str] = None`, `id: Optional[int] = None` | `database/models.py:73` |
| `AgentLog` | class | `agent_name: str`, `action: str`, `reasoning: str`, `input_data: Optional[str] = None`, `output_data: Optional[str] = None`, `timestamp: Optional[str] = None`, `id: Optional[int] = None` | `database/models.py:88` |
| `Account` | class | `balance: float`, `initial_balance: float`, `total_pnl: float = 0.0`, `total_trades: int = 0`, `winning_trades: int = 0`, `losing_trades: int = 0`, `max_drawdown: float = 0.0`, `peak_balance: Optional[float] = None`, `sharpe_ratio: Optional[float] = None`, `profit_factor: Optional[float] = None`, `updated_at: Optional[str] = None`, `id: Optional[int] = None` | `database/models.py:99` |
| `BalanceSnapshot` | class | `balance: float`, `equity: float`, `unrealized_pnl: float = 0.0`, `timestamp: Optional[str] = None`, `id: Optional[int] = None` | `database/models.py:115` |
| `MacroData` | class | `indicator: str`, `value: float`, `source: str`, `period: Optional[str] = None`, `fetched_at: Optional[str] = None`, `id: Optional[int] = None` | `database/models.py:124` |

**State it owns.** None — instances are value carriers. `Account` is imported by `database/repository.py:10` and then never referenced there; `Repository` takes raw floats for account writes and a `BalanceSnapshot` only for `insert_balance_snapshot`.

**Side effects.** None.

**Consumers.** `database/repository.py:8`; `data/price_feed.py:242` (`Candle`); `data/macro_fetcher.py:16` (`MacroData`); `data/news_fetcher.py:15` (`NewsItem`); `agents/base_agent.py:17` (`AgentLog`); `agents/decision_agent.py:19`, `agents/analysis_agent.py:20`, `agents/news_agent.py:15` (`Signal`, `NewsItem`); `trading/paper_engine.py:18` (`AgentLog`); `trading/position_manager.py:18` (`Position`, `Trade`, `BalanceSnapshot`).

#### 9.3.8 `database/repository.py`

**Role.** Every SQL statement in the codebase, one method per intent, each followed by an explicit `commit()`. It owns the OHLC write guard, the balance-mutation discipline, and the two retention prunes.

**Key symbols — module-level.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `_valid_candle` | function | `def _valid_candle(c: Candle) -> bool` — rejects non-coercible fields, any of o/h/l/c ≤ 0, volume < 0, and `h < l or h < o or h < cl or l > o or l > cl` | `database/repository.py:17` |
| `Repository.__init__` | method | `def __init__(self, db: Database)` | `database/repository.py:40` |
| `logger` | global | `Logger` bound to channel `"repository"` | `database/repository.py:14` |

**Key symbols — `class Repository` methods, with the table each touches.**

| Method | Signature | Table(s) | Loc |
|---|---|---|---|
| `insert_candle` | `async def insert_candle(self, c: Candle)` | `candles` (UPSERT on `(symbol, timeframe, timestamp)`) | `database/repository.py:45` |
| `insert_candles_batch` | `async def insert_candles_batch(self, candles: List[Candle])` | `candles` (bulk UPSERT) | `database/repository.py:62` |
| `get_candles` | `async def get_candles(self, symbol: str, timeframe: str, limit: int = 500) -> List[dict]` | `candles` (SELECT, `ORDER BY timestamp DESC`) | `database/repository.py:92` |
| `get_latest_candle` | `async def get_latest_candle(self, symbol: str, timeframe: str) -> Optional[dict]` | `candles` | `database/repository.py:100` |
| `insert_position` | `async def insert_position(self, p: Position) -> int` | `positions` (returns `cursor.lastrowid`) | `database/repository.py:110` |
| `update_position_pnl` | `async def update_position_pnl(self, position_id: int, unrealized_pnl: float)` | `positions` | `database/repository.py:122` |
| `update_position_sl_tp` | `async def update_position_sl_tp(self, position_id: int, stop_loss: Optional[float] = None, take_profit: Optional[float] = None)` | `positions` (dynamic SET, skips `None` args) | `database/repository.py:129` |
| `close_position` | `async def close_position(self, position_id: int, close_price: float, realized_pnl: float, close_reason: str) -> bool` | `positions` (single-claim: `WHERE id = ? AND status = 'OPEN'`, returns `rowcount > 0`) | `database/repository.py:147` |
| `liquidate_position` | `async def liquidate_position(self, position_id: int, liq_price: float, realized_pnl: float) -> bool` | `positions` (same claim guard, `status='LIQUIDATED'`) | `database/repository.py:171` |
| `get_open_positions` | `async def get_open_positions(self, symbol: str = None) -> List[dict]` | `positions` | `database/repository.py:183` |
| `get_all_positions` | `async def get_all_positions(self, limit: int = 100) -> List[dict]` | `positions` (`ORDER BY opened_at DESC`) | `database/repository.py:195` |
| `insert_trade` | `async def insert_trade(self, t: Trade) -> int` | `trades` | `database/repository.py:204` |
| `prune_agent_logs` | `async def prune_agent_logs(self, keep: int = 5000)` | `agent_logs` (`DELETE … WHERE id NOT IN (SELECT id … ORDER BY id DESC LIMIT ?)`) | `database/repository.py:215` |
| `get_trades` | `async def get_trades(self, limit: int = 100) -> List[dict]` | `trades` | `database/repository.py:235` |
| `get_trades_by_position` | `async def get_trades_by_position(self, position_id: int) -> List[dict]` | `trades` | `database/repository.py:242` |
| `insert_signal` | `async def insert_signal(self, s: Signal)` | `signals` | `database/repository.py:251` |
| `get_recent_signals` | `async def get_recent_signals(self, symbol: str, limit: int = 50) -> List[dict]` | `signals` | `database/repository.py:260` |
| `insert_news` | `async def insert_news(self, n: NewsItem)` | `news` | `database/repository.py:269` |
| `get_recent_news` | `async def get_recent_news(self, limit: int = 50) -> List[dict]` | `news` (`ORDER BY fetched_at DESC`) | `database/repository.py:280` |
| `news_exists` | `async def news_exists(self, title: str, source: str) -> bool` | `news` (`SELECT id … LIMIT 1`) | `database/repository.py:287` |
| `insert_agent_log` | `async def insert_agent_log(self, log: AgentLog)` | `agent_logs` | `database/repository.py:296` |
| `get_agent_logs` | `async def get_agent_logs(self, agent_name: str = None, limit: int = 100) -> List[dict]` | `agent_logs` | `database/repository.py:305` |
| `get_account` | `async def get_account(self) -> Optional[dict]` | `account` (`ORDER BY id DESC LIMIT 1`) | `database/repository.py:320` |
| `init_account` | `async def init_account(self, initial_balance: float)` | `account` (no-op when a row exists) | `database/repository.py:324` |
| `update_account` | `async def update_account(self, balance: float, total_pnl: float, total_trades: int, winning_trades: int, losing_trades: int, max_drawdown: float, peak_balance: float, sharpe_ratio: float = None, profit_factor: float = None)` | `account` (absolute `balance` write) | `database/repository.py:337` |
| `update_account_stats` | `async def update_account_stats(self, total_pnl: float, total_trades: int, winning_trades: int, losing_trades: int, profit_factor: float = None, max_drawdown: float = None, sharpe_ratio: float = None)` | `account` (never touches `balance`) | `database/repository.py:356` |
| `update_balance` | `async def update_balance(self, new_balance: float)` | `account` (absolute write) | `database/repository.py:395` |
| `apply_balance_delta` | `async def apply_balance_delta(self, delta: float) -> float` | `account` (atomic `balance = balance + ?`, then re-SELECT) | `database/repository.py:403` |
| `bump_peak_balance` | `async def bump_peak_balance(self, candidate: float) -> float` | `account` (`peak_balance = MAX(COALESCE(peak_balance,0), ?)`) | `database/repository.py:427` |
| `insert_balance_snapshot` | `async def insert_balance_snapshot(self, snap: BalanceSnapshot)` | `balance_history` | `database/repository.py:443` |
| `get_balance_history` | `async def get_balance_history(self, limit: int = 1000) -> List[dict]` | `balance_history` | `database/repository.py:451` |
| `insert_direction_snapshot` | `async def insert_direction_snapshot(self, snap: dict)` | `direction_snapshots` (dict input, not a dataclass) | `database/repository.py:460` |
| `get_latest_direction_snapshot` | `async def get_latest_direction_snapshot(self, symbol: str) -> Optional[dict]` | `direction_snapshots` | `database/repository.py:486` |
| `get_latest_direction_snapshots` | `async def get_latest_direction_snapshots(self) -> List[dict]` | `direction_snapshots` (self-join on `MAX(id) GROUP BY symbol`) | `database/repository.py:495` |
| `prune_direction_snapshots` | `async def prune_direction_snapshots(self, keep_per_symbol: int = 120)` | `direction_snapshots` (`ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY id DESC)`) | `database/repository.py:505` |
| `get_daily_realized_pnl` | `async def get_daily_realized_pnl(self) -> float` | `positions` (`SUM(realized_pnl)` for `CLOSED`/`LIQUIDATED` with `date(closed_at) = date('now')`) | `database/repository.py:526` |
| `upsert_macro` | `async def upsert_macro(self, m: MacroData)` | `macro_data` (UPSERT on `(indicator, period)`) | `database/repository.py:551` |
| `get_macro_latest` | `async def get_macro_latest(self, indicator: str) -> Optional[dict]` | `macro_data` | `database/repository.py:561` |
| `get_all_macro` | `async def get_all_macro(self) -> List[dict]` | `macro_data` | `database/repository.py:568` |
| `get_trade_stats` | `async def get_trade_stats(self) -> dict` | `positions` (aggregated in Python, not SQL) | `database/repository.py:576` |

**State it owns.** `self.db: Database` (`database/repository.py:41`) — the only field, the injected shared connection. No caching, no lifecycle: closing is `Database`/`close_db`'s job. `insert_direction_snapshot` reads `snap["symbol"]` with `[]` (KeyError if absent) but `.get(...)` for `z_composite`, `agent_breakdown`, `diffusion`; JSON blobs arrive pre-serialized and this layer does not serialize (`:460-467`).

**Side effects.** Every method is a write-then-`commit()`; reads issue one `SELECT`. Two concurrency disciplines live here and must not be re-implemented elsewhere: `close_position`/`liquidate_position` are claim-single operations keyed on `status = 'OPEN'` in the `WHERE` clause, so the SL/TP path and the scheduler's batch close cannot both release the same margin (`:151-158`); `apply_balance_delta` mutates as `balance = balance + ?` in one statement rather than read-modify-write, closing the lost-update window between the decision agent and `_execution_loop` (`:403-414`). `update_account_stats` exists so recomputed statistics can be written without disturbing `balance` (`:366-371`). Retention: `prune_agent_logs(keep=5000)` guards a table written every 0.3 s decision cycle (~309k rows/day), driven by `run.py` on `agent_log_prune_interval = 1800` s / `agent_log_keep = 5000` (`core/config.py:395-398`); `prune_direction_snapshots(keep_per_symbol=120)` guards the 5 s × 10-symbol ensemble write (~172.8k rows/day). `get_trade_stats` returns a zeroed dict when nothing is closed and `profit_factor = float("inf")` when `gross_loss == 0` (`:582-603`).

**Consumers.** `run.py:53`; `agents/base_agent.py:16` (lazy `Repository(get_db())` in `_get_repo`, `:38-41`); `trading/paper_engine.py:17` (`:70-73`); `trading/position_manager.py:17` (`:41-44`); `data/price_feed.py:241` (constructs its own short-lived `Repository` inside `fetch_ohlcv`); tests `tests/test_decision_agent_ensemble.py:22`, `tests/test_fill_price_sl.py:18`, `tests/test_lifecycle_paths.py:28`, `tests/test_paper_engine.py:13`, `tests/test_position_manager.py:12`, `tests/test_bugfixes.py` (all use `close_db` to isolate the singleton).

#### 9.3.9 `data/__init__.py`

**Role.** Package marker only — `"""Data package — pengambilan harga, berita, makro, sentimen."""`, no exports; all imports are fully qualified (`data/__init__.py:1`).

#### 9.3.10 `database/__init__.py`

**Role.** Package marker only — `"""Database package — koneksi, skema, repository."""`, no exports (`database/__init__.py:1`).

---

### 9.4 UI and infrastructure layers (`core/`, `dashboard/`, `ml/`)

Covers `dashboard/` (Dash/Plotly HUD server), `core/` (config, event bus, logger, market store, microstructure kernel, scheduler, utils) and `ml/` (offline trainer + thin predictor wrapper).

#### 9.4.1 `dashboard/__init__.py`

**Role.** Package marker only — `"""Dashboard package — antarmuka visual Dash/Plotly."""`, no exports (`dashboard/__init__.py:1`).

#### 9.4.2 `dashboard/app.py`

**Role.** Dash application factory and server entrypoint for the Retro Bloomberg HUD; installs a `KeyError` interceptor for stale browser-cached callbacks and silences Werkzeug access logging.

**Key symbols.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `create_dash_app` | function | `def create_dash_app() -> dash.Dash:` | `dashboard/app.py:25` |
| `handle_stale_callback_key_error` | nested function | `def handle_stale_callback_key_error(e):` | `dashboard/app.py:39` |
| `_silence_flask_banner` | function | `def _silence_flask_banner():` | `dashboard/app.py:100` |
| `run_dashboard` | function | `def run_dashboard():` | `dashboard/app.py:128` |
| `logger` | global | `get_logger("dashboard")` | `dashboard/app.py:22` |

**State it owns.** None — reads `get_config().dashboard` (`host`, `port`, `debug`).

**Side effects.** At import (`app.py:13-15`): `werkzeug` and `flask` loggers set to `ERROR`, `WSGIRequestHandler.log_request` replaced with a no-op lambda. `_silence_flask_banner` monkeypatches `click.echo` to a no-op and lowers the `dash` logger unless `TRADEBOT_BANNER` is truthy. `handle_stale_callback_key_error` is a Flask `errorhandler(KeyError)` returning `jsonify({"response": {}, "multi": True}), 200` for messages containing `"Callback function not found for output"`. `run_dashboard` binds via `app.run(host=cfg.host, port=cfg.port, debug=cfg.debug, dev_tools_silence_routes_logging=True)`.

**Component ids declared here.** `dashboard-interval` (`dcc.Interval`, 500 ms), `dashboard-slow-interval` (`dcc.Interval`, 60000 ms), `hud-main-content`, plus hidden legacy-compat ids `header-balance/equity/pnl/positions`, `chart-symbol`, `chart-timeframe`, `candlestick-chart`, `indicator-values`, `active-positions-table`, `trade-history-table`, `agent-logs-container`, `agent-log-filter`, `sentiment-summary`, `news-feed-container`, `performance-stats`, `equity-chart`, `drawdown-chart`, `tab-content`, `main-tabs`.

**Consumers.** `run.py:63` imports `run_dashboard`; `run.py:729-737` starts it in a `threading.Thread`.

#### 9.4.3 `dashboard/callbacks/__init__.py`

**Role.** Package marker only — `"""Dashboard callbacks package."""`, no exports (`dashboard/callbacks/__init__.py:1`).

#### 9.4.4 `dashboard/callbacks/update_callbacks.py`

**Role.** Registers all Dash callbacks: live HUD callbacks on the 500 ms interval, plus eight legacy-compat callbacks that only exist so an old browser tab does not `KeyError` after a redeploy.

**Key symbols (module level).**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `_neural_msg_prev` | global | `_neural_msg_prev: dict = {}` | `dashboard/callbacks/update_callbacks.py:49` |
| `_neural_last_fig` | global | `_neural_last_fig = None` | `dashboard/callbacks/update_callbacks.py:54` |
| `_set_neural_last_fig` | function | `def _set_neural_last_fig(fig):` | `dashboard/callbacks/update_callbacks.py:57` |
| `_get_sync_db` | function | `def _get_sync_db():` | `dashboard/callbacks/update_callbacks.py:64` |
| `derive_macro_bias` | function | `def derive_macro_bias(macro_rows) -> str:` | `dashboard/callbacks/update_callbacks.py:72` |
| `register_callbacks` | function | `def register_callbacks(app):` | `dashboard/callbacks/update_callbacks.py:114` |

**Callbacks registered inside `register_callbacks`.**

| Callback fn | Input | State | Output | Loc |
|---|---|---|---|---|
| `update_legacy_header` | `dashboard-interval` | — | `header-balance/equity/pnl/positions .children` | `dashboard/callbacks/update_callbacks.py:128` |
| `update_legacy_performance` | `dashboard-interval` | — | `performance-stats.children`, `equity-chart.figure`, `drawdown-chart.figure` | `dashboard/callbacks/update_callbacks.py:155` |
| `update_legacy_positions` | `dashboard-interval` | — | `active-positions-table.children`, `trade-history-table.children` | `dashboard/callbacks/update_callbacks.py:241` |
| `update_legacy_news` | `dashboard-interval` | — | `sentiment-summary.children`, `news-feed-container.children` | `dashboard/callbacks/update_callbacks.py:250` |
| `update_legacy_logs` | `dashboard-interval` | — | `agent-logs-container.children` | `dashboard/callbacks/update_callbacks.py:258` |
| `update_legacy_indicators` | `dashboard-interval` | — | `indicator-values.children` | `dashboard/callbacks/update_callbacks.py:266` |
| `update_legacy_candlestick` | `dashboard-interval` | — | `candlestick-chart.figure` | `dashboard/callbacks/update_callbacks.py:274` |
| `update_legacy_symbol_options` | `dashboard-interval` | — | `chart-symbol.options` | `dashboard/callbacks/update_callbacks.py:303` |
| `update_top_bar` | `dashboard-interval` | — | `hud-spot-btc/eth/sol.children`, `hud-avg-edge.children`, `hud-utc-clock.children`, `hud-latency-badge.children` | `dashboard/callbacks/update_callbacks.py:319` |
| `update_symbol_dropdown` | `dashboard-interval` | `hud-chart-symbol-select.options` | `hud-chart-symbol-select.options` | `dashboard/callbacks/update_callbacks.py:391` |
| `update_mini_candlestick_and_tape` | `dashboard-interval`, `hud-chart-symbol-select.value` | — | `hud-candlestick-chart.figure`, `hud-chart-price-display.children`, `hud-orderbook-tape.children` | `dashboard/callbacks/update_callbacks.py:414` |
| `update_wallet_overview` | `dashboard-interval` | — | `hud-giant-pnl.children`, `hud-giant-pnl.className`, `hud-wallet-sub.children`, `hud-stat-balance/equity/winrate/risk.children` | `dashboard/callbacks/update_callbacks.py:629` |
| `update_positions_grid` | `dashboard-interval` | — | `hud-positions-table.children`, `hud-positions-count-badge.children` | `dashboard/callbacks/update_callbacks.py:714` |
| `update_probability_scanner` | `dashboard-interval`, `hud-chart-symbol-select.value` | — | `hud-scanner-long-odds.children`, `hud-scanner-short-odds.children`, `hud-scanner-consensus.children`, `hud-scanner-consensus.className`, `hud-scanner-agent-strip.children`, `hud-convergence-chart.figure` | `dashboard/callbacks/update_callbacks.py:794` |
| `update_neural_net` | `dashboard-interval` | — | `hud-neural-graph.figure` | `dashboard/callbacks/update_callbacks.py:913` |
| `update_equity_area` | `dashboard-interval` | — | `hud-realized-tag.children`, `hud-equity-area-chart.figure` | `dashboard/callbacks/update_callbacks.py:1002` |
| `update_trade_stream_and_marquee` | `dashboard-slow-interval` | — | `hud-trade-logs-stream.children`, `hud-live-ticker-text.children`, `hud-footer-ticker-text.children` | `dashboard/callbacks/update_callbacks.py:1057` |
| `update_analytics_and_sparklines` | `dashboard-interval`, `hud-chart-symbol-select.value` | — | `hud-sparkline-pnl.figure`, `hud-sparkline-volume.figure`, `hud-kpi-sharpe/pf/mdd/duration/slippage/fees.children` | `dashboard/callbacks/update_callbacks.py:1209` |
| `update_symbol_pnl` | `dashboard-interval` | — | `hud-symbol-pnl-rail.children`, `hud-pnl-stage-tag.children` | `dashboard/callbacks/update_callbacks.py:1347` |

Intervals above are `n_intervals` props. All eight legacy callbacks set `prevent_initial_call=False`; the HUD callbacks leave it at the Dash default.

**State it owns.** `_neural_msg_prev: dict` — last-seen cumulative `message_counts` per WS channel; only the *delta* is used so the constellation shows rate, not a saturating total. `_neural_last_fig` — last good constellation figure, returned on error so a transient failure does not blank every token.

**Side effects.** Fresh synchronous `sqlite3.connect(cfg.database_path)` (`row_factory = sqlite3.Row`) per call, reading `account`, `positions`, `candles`, `balance_history`, `trades`, `agent_logs`, `signals`, `direction_snapshots`. Mutates `_neural_msg_prev`. Lazily imports `data.price_feed.get_price_feed()` and reads `feed.hyperliquid.get_status()["message_counts"]`. Never writes.

**Data-honesty rules.** Realized PnL comes from `positions.realized_pnl` (`trades` has no PnL column). History is always `ORDER BY timestamp DESC LIMIT 300` then reversed in Python — `ASC LIMIT 300` freezes the curve past 300 rows. Unwritten `account.max_drawdown` / `sharpe_ratio` render `N/A`, never `0.00`. Missing candles or L2 snapshots give explicit waiting-state text, never synthetic GBM/depth. Slippage is `N/A` until a real L2 snapshot exists.

**Consumers.** `dashboard/app.py:20`. Imports `core.{config,logger,market_store}`, `analysis.{probability_engine,technical}`, and every `dashboard.layouts.*` factory.

#### 9.4.5 `dashboard/layouts/__init__.py`

**Role.** Package marker only — `"""Dashboard layouts package."""`, no exports (`dashboard/layouts/__init__.py:1`).

#### 9.4.6 `dashboard/layouts/hud.py`

**Role.** Builds the whole single-screen HUD as a 12-column CSS grid; owns all `hud-*` component ids.

**Key symbols.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `create_hud_layout` | function | `def create_hud_layout() -> html.Div:` | `dashboard/layouts/hud.py:20` |
| `create_top_bar` | function | `def create_top_bar() -> html.Div:` | `dashboard/layouts/hud.py:62` |
| `create_ticker_tape` | function | `def create_ticker_tape() -> html.Div:` | `dashboard/layouts/hud.py:100` |
| `create_wallet_pnl_panel` | function | `def create_wallet_pnl_panel() -> html.Div:` | `dashboard/layouts/hud.py:112` |
| `create_mini_price_panel` | function | `def create_mini_price_panel() -> html.Div:` | `dashboard/layouts/hud.py:158` |
| `create_positions_panel` | function | `def create_positions_panel() -> html.Div:` | `dashboard/layouts/hud.py:204` |
| `create_scanner_panel` | function | `def create_scanner_panel() -> html.Div:` | `dashboard/layouts/hud.py:230` |
| `TREE_STAGES` | constant | `["feed", "scan", "sense", "gate", "exec", "outcome"]` | `dashboard/layouts/hud.py:280` |
| `create_symbol_pnl_panel` | function | `def create_symbol_pnl_panel() -> html.Div:` | `dashboard/layouts/hud.py:283` |
| `create_neural_net_panel` | function | `def create_neural_net_panel() -> html.Div:` | `dashboard/layouts/hud.py:318` |
| `create_equity_growth_panel` | function | `def create_equity_growth_panel() -> html.Div:` | `dashboard/layouts/hud.py:347` |
| `create_trade_logs_panel` | function | `def create_trade_logs_panel() -> html.Div:` | `dashboard/layouts/hud.py:372` |
| `create_live_analytics_panel` | function | `def create_live_analytics_panel() -> html.Div:` | `dashboard/layouts/hud.py:391` |
| `create_footer_ticker` | function | `def create_footer_ticker() -> html.Div:` | `dashboard/layouts/hud.py:435` |

**Component ids owned.** `hud-spot-btc/-eth/-sol`, `hud-avg-edge`, `hud-utc-clock`, `hud-latency-badge`, `hud-live-ticker-text`, `hud-giant-pnl`, `hud-wallet-sub`, `hud-stat-balance/-equity/-winrate/-risk`, `hud-chart-symbol-select` (`dcc.Dropdown`, default `"BTC/USDT:USDT"`, `clearable=False`), `hud-chart-price-display`, `hud-candlestick-chart`, `hud-orderbook-tape`, `hud-positions-count-badge`, `hud-positions-table`, `hud-scanner-long-odds`, `hud-scanner-consensus`, `hud-scanner-short-odds`, `hud-scanner-agent-strip`, `hud-convergence-chart`, `hud-pnl-stage-tag`, `hud-symbol-pnl-rail`, `hud-neural-graph`, `hud-realized-tag`, `hud-equity-area-chart`, `hud-trade-logs-stream`, `hud-sparkline-pnl`, `hud-sparkline-volume`, `hud-kpi-sharpe/-pf/-mdd/-duration/-slippage/-fees`, `hud-footer-ticker-text`.

**State it owns.** `TREE_STAGES` — stage order for the now-replaced decision-tree animation, kept so stage additions need one edit.

**Side effects.** None. Panel heights come from CSS custom properties (`--h-chart`, `--h-conv`, `--h-neural`, `--h-spark`, `--h-equity`), never inline px, so figures and divs cannot disagree.

**Consumers.** `dashboard/app.py:19`.

#### 9.4.7 `dashboard/layouts/hud_figures.py`

**Role.** Every Plotly figure factory for the HUD, plus the deterministic layout tables behind the neural-net constellation.

**Key symbols.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `_hex_to_rgb` | function | `def _hex_to_rgb(color: str) -> tuple:` | `dashboard/layouts/hud_figures.py:21` |
| `with_alpha` | function | `def with_alpha(color: str, alpha: float) -> str:` | `dashboard/layouts/hud_figures.py:27` |
| `PARCHMENT_BG`, `PARCHMENT_CARD`, `INK_BLACK`, `INK_MUTED`, `GREEN_VINTAGE`, `RED_VINTAGE`, `AMBER_VINTAGE`, `BLUE_VINTAGE` | constants | re-exports of `palette.PAPER_BG`, `CARD_BG`, `INK`, `INK_MUTED`, `GREEN`, `RED`, `AMBER`, `BLUE` | `dashboard/layouts/hud_figures.py:51-58` |
| `GREEN_VINTAGE_WASH`, `BLUE_VINTAGE_DIM`, `INK_FAINT`, `INK_TRANSPARENT` | constants | derived via `with_alpha` | `dashboard/layouts/hud_figures.py:64-67` |
| `create_mini_candlestick_fig` | function | `def create_mini_candlestick_fig(df: pd.DataFrame, symbol: str) -> go.Figure:` | `dashboard/layouts/hud_figures.py:71` |
| `create_convergence_fig` | function | `def create_convergence_fig(diffusion_data: dict = None) -> go.Figure:` | `dashboard/layouts/hud_figures.py:205` |
| `NEURAL_SYSTEM_NODES` | constant | `dict[str, (x, y, label, color, tooltip)]` — 4-column tree: root / category / agent / output | `dashboard/layouts/hud_figures.py:307` |
| `NEURAL_SYSTEM_EDGES` | constant | `list[tuple[str, str]]` — parent/child pairs, one parent per child, no merges | `dashboard/layouts/hud_figures.py:344` |
| `TOKEN_SLOTS` | constant | `list[tuple[float, float]]` — 12 orbital slots, 2 columns of 6, 2.60-unit row pitch | `dashboard/layouts/hud_figures.py:385` |
| `_TOKEN_UNIVERSE` | constant | `list[str]` — 25 candidate tokens | `dashboard/layouts/hud_figures.py:415` |
| `_TOKEN_DISPLAY_PRIORITY` | constant | `list[str]` — majors first, then alphabetical | `dashboard/layouts/hud_figures.py:429` |
| `_fnv1a` | function | `def _fnv1a(text: str) -> int:` | `dashboard/layouts/hud_figures.py:437` |
| `_build_token_slot_registry` | function | `def _build_token_slot_registry() -> dict:` | `dashboard/layouts/hud_figures.py:451` |
| `_TOKEN_SLOT_REGISTRY` | constant | `dict[str, int]` — token → slot index, built once at import | `dashboard/layouts/hud_figures.py:493` |
| `NEURAL_NODE_LAYOUT`, `TOKEN_NODE_KEYS`, `TOKEN_ANCHORS` | constants | back-compat aliases of the tables above | `dashboard/layouts/hud_figures.py:497-499` |
| `AGENT_NODE_METRIC` | constant | `dict[str, str]` — node → `agent_logs.agent_name` key driving its pulse | `dashboard/layouts/hud_figures.py:502` |
| `FEED_NODE_CHANNEL` | constant | `dict[str, str]` — feed node → Hyperliquid WS channel | `dashboard/layouts/hud_figures.py:512` |
| `create_neural_net_fig` | function | `def create_neural_net_fig(signal_map: dict = None, activity_map: dict = None, flow_map: dict = None) -> go.Figure:` | `dashboard/layouts/hud_figures.py:520` |
| `create_equity_area_fig` | function | `def create_equity_area_fig(balance_history: list, initial_balance: float = 10000.0) -> go.Figure:` | `dashboard/layouts/hud_figures.py:859` |
| `create_mini_sparkline_fig` | function | `def create_mini_sparkline_fig(data_series: list, line_color: str = GREEN_VINTAGE) -> go.Figure:` | `dashboard/layouts/hud_figures.py:931` |

**State it owns.** `_TOKEN_SLOT_REGISTRY`, computed once at import. Assignment is display-priority ordered (majors to left slots 0-5, next six right), never hash-based, so a token never jumps slots when another token's activity comes and goes. `MARKET`/`GLOBAL`/`AGGREGATE`/`TOTAL` sentinels are filtered — `MARKET` is written by `NewsAgent` as a market-sentiment aggregate, not a coin.

**Side effects.** Pure. `create_equity_area_fig` uses `tonexty` against an explicit baseline trace, not `fill="tozeroy"` — the latter forces a zero into the y-axis and flattens a 10 USDT move on a 10 000 USDT account.

**Consumers.** `dashboard/layouts/hud.py`, `dashboard/callbacks/update_callbacks.py`, and offline measurement scripts under `data_store/` (`analyze_edges`, `calibrate_probe`, `constellation_measure`, `measure_truth`, `validate_model`, …) plus `check_dashboard_data.py`.

#### 9.4.8 `dashboard/layouts/palette.py`

**Role.** Single source of truth for Plotly colors: Plotly renders to canvas and cannot resolve `var(--token)`, so this parses the `:root` block of `style.css` once at import and re-exports tokens as literal hex.

**Key symbols.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `_CSS` | constant | `Path(__file__).resolve().parent.parent / "assets" / "style.css"` | `dashboard/layouts/palette.py:30` |
| `_FALLBACK` | constant | `Dict[str, str]` — 14 tokens matching current `:root` | `dashboard/layouts/palette.py:34` |
| `_VAR_RE` | constant | `re.compile(r"(--[a-z0-9-]+)\s*:\s*([^;]+);", re.IGNORECASE)` | `dashboard/layouts/palette.py:50` |
| `_load` | function | `def _load() -> Dict[str, str]:` | `dashboard/layouts/palette.py:53` |
| `TOKENS` | constant | `Dict[str, str]` — parsed once at import | `dashboard/layouts/palette.py:85` |
| `PAPER_BG`, `CARD_BG`, `INSET_BG`, `INK`, `INK_DIM`, `INK_MUTED`, `RULE`, `RULE_SOFT` | constants | aliases of `TOKENS[...]` | `dashboard/layouts/palette.py:89-96` |
| `GREEN`, `RED`, `AMBER`, `WARN`, `BLUE` | constants | semantic aliases (`WARN` aliases `AMBER`) | `dashboard/layouts/palette.py:98-103` |
| `alpha` | function | `def alpha(hex_color: str, opacity: float) -> str:` | `dashboard/layouts/palette.py:106` |
| `apply_dark_figure` | function | `def apply_dark_figure(fig, height: int = None):` | `dashboard/layouts/palette.py:121` |

**Loader contract.** `_load()` locates `:root`, brace-matches to that block's closing brace so tokens from other blocks cannot leak in, and accepts only keys already present in `_FALLBACK` — a CSS typo is ignored, not invented. Returns `_FALLBACK` unchanged on `OSError` or missing `:root`, so an unreadable stylesheet degrades to the current theme rather than a white panel.

**State it owns.** `TOKENS`, evaluated at first import and never refreshed — a CSS edit needs a process restart.

**Side effects.** One filesystem read of `style.css` at import.

**Consumers.** `dashboard/layouts/{hud_figures,performance,price_chart}.py`; verified by `tests/test_dashboard_palette.py`, `verify_dashboard_palette.py`, `verify_dashboard_render.py`.

#### 9.4.9 `dashboard/layouts/positions.py`

**Role.** Legacy positions tab plus HTML-table builders for open positions and trade history, retained for the compat callbacks.

**Key symbols.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `create_positions_layout` | function | `def create_positions_layout():` | `dashboard/layouts/positions.py:8` |
| `create_positions_table` | function | `def create_positions_table(positions):` | `dashboard/layouts/positions.py:27` |
| `create_trade_history_table` | function | `def create_trade_history_table(trades):` | `dashboard/layouts/positions.py:69` |

**Component ids owned.** `active-positions-table`, `trade-history-table`.

**State it owns.** None.

**Side effects.** None. `create_trade_history_table` truncates to `trades[:50]`; `create_positions_table` hardcodes dark-theme hexes (`#3fb950` / `#f85149`) that no longer match the parchment palette.

**Consumers.** `dashboard/callbacks/update_callbacks.py:36` (imported, not invoked — the live HUD renders its own compact rows).

#### 9.4.10 `dashboard/layouts/performance.py`

**Role.** Legacy performance tab: stat-card grid, equity curve, drawdown chart.

**Key symbols.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `create_performance_layout` | function | `def create_performance_layout():` | `dashboard/layouts/performance.py:11` |
| `create_performance_stats` | function | `def create_performance_stats(account_summary):` | `dashboard/layouts/performance.py:33` |
| `create_equity_chart` | function | `def create_equity_chart(balance_history):` | `dashboard/layouts/performance.py:70` |
| `create_drawdown_chart` | function | `def create_drawdown_chart(balance_history):` | `dashboard/layouts/performance.py:117` |

**Component ids owned.** `performance-stats`, `equity-chart`, `drawdown-chart`.

**State it owns.** None. `create_performance_stats` reads keys `balance`, `initial_balance`, `total_pnl`, `total_trades`, `win_rate`, `profit_factor`, `max_drawdown`, `equity`.

**Side effects.** None. **Consumers.** `dashboard/callbacks/update_callbacks.py:39-41`; palette checked by `tests/test_dashboard_palette.py`.

#### 9.4.11 `dashboard/layouts/price_chart.py`

**Role.** Legacy full-page price chart: symbol/timeframe selectors, Plotly candlestick + volume + RSI subplots, indicator readout.

**Key symbols.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `create_price_chart_layout` | function | `def create_price_chart_layout():` | `dashboard/layouts/price_chart.py:13` |
| `create_candlestick_figure` | function | `def create_candlestick_figure(df, symbol="BTC/USDT", trades=None):` | `dashboard/layouts/price_chart.py:72` |

**Component ids owned.** `chart-symbol`, `chart-timeframe`, `candlestick-chart`, `indicator-panel`, `indicator-values`. The first three are duplicated as hidden compat nodes in `dashboard/app.py:76-78`.

**State it owns.** None. Imports `get_config` from `core.config` at module load.

**Side effects.** None. **Consumers.** `dashboard/callbacks/update_callbacks.py:42`; `tests/test_dashboard_palette.py`, `verify_dashboard_palette.py`.

#### 9.4.12 `dashboard/layouts/news_feed.py`

**Role.** Legacy news tab: sentiment summary card and news item list.

**Key symbols.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `create_news_feed_layout` | function | `def create_news_feed_layout():` | `dashboard/layouts/news_feed.py:8` |
| `create_sentiment_summary` | function | `def create_sentiment_summary(aggregate):` | `dashboard/layouts/news_feed.py:27` |
| `create_news_items` | function | `def create_news_items(news_list):` | `dashboard/layouts/news_feed.py:79` |

**Component ids owned.** `sentiment-summary`, `news-feed-container`.

**State it owns.** None. `create_sentiment_summary` expects `avg_score`, `label`, `count`, `positive`, `negative`.

**Side effects.** None. **Consumers.** `dashboard/callbacks/update_callbacks.py:38` (the live compat callback returns `html.Div()`).

#### 9.4.13 `dashboard/layouts/agent_logs.py`

**Role.** Legacy agent-reasoning log tab with an agent filter dropdown.

**Key symbols.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `create_agent_logs_layout` | function | `def create_agent_logs_layout():` | `dashboard/layouts/agent_logs.py:8` |
| `create_log_entries` | function | `def create_log_entries(logs):` | `dashboard/layouts/agent_logs.py:39` |

**Component ids owned.** `agent-log-filter` (options `ALL`, `news_agent`, `analysis_agent`, `decision_agent`, `execution_agent`, `paper_engine`), `agent-logs-container`.

**State it owns.** None.

**Side effects.** None. **Consumers.** `dashboard/callbacks/update_callbacks.py:37`. The live log stream is rendered inline by `update_trade_stream_and_marquee` and reads the real `agent_logs.reasoning` column, not `message`.

#### 9.4.14 `dashboard/assets/style.css`

**Role.** The entire HUD visual contract: design tokens in `:root`, grid geometry, panel chrome, and every class the callbacks emit.

**Key token groups (`:root`, lines 14-117).**

| Group | Tokens | Loc |
|---|---|---|
| Type | `--font-mono` (`'Share Tech Mono'`), `--fs-micro` 9px … `--fs-hero` 34px | `dashboard/assets/style.css:16-22` |
| Spacing | `--sp-1` 2px … `--sp-6` 16px (4px rhythm) | `dashboard/assets/style.css:25-26` |
| Surface / line | `--bg-app` `#dfd5b8`, `--bg-card` `#e5dbc0`, `--bg-inset` `#ede4cc`, `--fg` `#000000`, `--line`, `--line-soft` | `dashboard/assets/style.css:34-39` |
| Text | `--fg-dim` `#3a352c`, `--fg-muted` `#555042` | `dashboard/assets/style.css:42-43` |
| Semantic | `--pos` `#006a2b`, `--neg` `#9e1b16`, `--warn` `#8a5200`, `--accent` `#005a8c` | `dashboard/assets/style.css:46-49` |
| Derived tints | `--pos-ink/-text/-wash`, `--neg-ink/-text/-wash/-wash-2`, `--warn-wash/-wash-2`, `--rule`, `--rule-soft`, `--rule-strong`, `--track`, `--white` | `dashboard/assets/style.css:52-65` |
| Height contract | `--h-topbar` 28, `--h-rail` 66, `--h-tape` 20, `--h-zone2` 356, `--h-zone3` 260, `--h-pipe` 56, `--h-head` 22, `--h-scroll` 230, `--h-chart` 179, `--h-tape-ob` 143, `--h-ob-row` 13, `--h-conv` 92, `--h-neural` 230, `--h-spark` 34, `--h-equity` 230, `--h-log` 230, `--h-kpi` 118 | `dashboard/assets/style.css:78-94` |

**Class families.** Grid `.hud-grid`, `.span-12/-8/-4/-3/-2`, `.zone2-price`, `.zone2-stack`, `.zone3-cell`, `.p-stack`; chrome `.hud-container`, `.hud-panel*`, `.header-tag`; top bar `.hud-topbar`, `.topbar-*`, `.bot-brand`, `.badge-*`, `.pulse-live`, `.spot-*`, `.utc-clock`; tape `.hud-live-ticker-tape`, `.ticker-*`, `.tape-label`; orderbook `.orderbook-*`, `.ob-bar*`, `.ob-price/-size/-total`; positions `.positions-grid-table`, `.position-*`; scanner `.odds-*`, `.agent-strip`, `.agent-chip*`; logs `.log-chip*`, `.log-time`, `.trade-log-*`; symbol PnL `.sympnl-*`; dropdown `.symbol-select` + `.dash-dropdown-*`; utility `.tf-tag`, `.spark-*`, `.empty-note*`, `.text-*`.

**Notable rules.** Every `:root` token must hold a literal value — a self-referential `var()` is invalid at computed-value time and the browser silently drops the declaration, losing background and border (guarded by `test_no_cyclic_css_variables`). The old green-on-inset palette measured 2.72:1 contrast and was replaced. The height contract exists because seven chart heights were previously hardcoded in two files, leaving a dead strip on every 500 ms refresh.

**State / side effects.** None; static stylesheet. `@import` pulls `Share Tech Mono` from Google Fonts (`style.css:12`). **Consumers.** Served by Dash; parsed at import by `dashboard/layouts/palette.py`.

#### 9.4.15 `dashboard/assets/neural_flow.css`

**Role.** Declarative animation for the `hud-neural-graph` constellation — edge dash-offset flow and node breathing — with zero server cost and zero JavaScript.

**Key selectors.**

| Selector / at-rule | Purpose | Loc |
|---|---|---|
| `#hud-neural-graph .scatterlayer .trace path.js-line` | `animation: neural-edge-flow 1.2s linear infinite` (`:31`) — shifts `stroke-dashoffset` only | `dashboard/assets/neural_flow.css:24` |
| `@keyframes neural-edge-flow` | `stroke-dashoffset: 0 → -16px` | `dashboard/assets/neural_flow.css:50` |
| `#hud-neural-graph .scatterlayer .trace path.point` | `animation: neural-node-breathe 3.4s ease-in-out infinite` (`:68`) | `dashboard/assets/neural_flow.css:67` |
| `@keyframes neural-node-breathe` | `opacity` 1→0.78→1, `stroke-opacity` 1→0.7→1 | `dashboard/assets/neural_flow.css:71` |
| `@media (prefers-reduced-motion: reduce)` | `animation: none !important` (`:87`) on both selectors | `dashboard/assets/neural_flow.css:84` |

**Division of labour with Python.** Python sets the dash *pattern* (`dash="9px,7px"`) as an inline Plotly style; this stylesheet only shifts the *offset*, so idle solid edges need no separate class — a dash-offset shift on a solid line is a visual no-op and one selector covers both.

**Hard constraints.** Never animate `transform` on `path.point` — Plotly positions markers via the SVG attribute `transform="translate(x,y)"` and a CSS `transform` overrides it, collapsing every marker onto the origin. Per-edge `:nth-child(6n+k)` stagger never worked (each edge is its own trace, so the selector always resolved to `:nth-child(1)`); per-edge phase is randomized from Python instead.

**State / side effects.** None; static stylesheet. **Consumers.** Served by Dash alongside `style.css`.

#### 9.4.16 `core/__init__.py`

**Role.** Package marker only — `"""Core package — konfigurasi, event bus, scheduler, logger."""`, no exports (`core/__init__.py:1`).

#### 9.4.17 `core/config.py`

**Role.** Typed configuration tree loaded from `config.yaml`, a validator suite that refuses economically impossible settings, and a process-wide singleton.

**Key symbols — dataclasses.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `AccountConfig` | dataclass | `initial_balance: float = 10000.0`, `currency: str = "USDT"` | `core/config.py:13` |
| `RiskConfig` | dataclass | `max_risk_per_trade=0.02`, `max_leverage=20`, `max_daily_loss=0.05`, `max_drawdown=0.15`, `max_open_positions=3`, `default_leverage=5` | `core/config.py:19` |
| `FeeConfig` | dataclass | `maker: float = 0.0002`, `taker: float = 0.0005` | `core/config.py:29` |
| `AgentIntervals` | dataclass | 10 int intervals: `news_agent=300`, `news_agent_us_open=120`, `analysis_agent=300`, `analysis_agent_us_open=180`, `decision_agent=60`, `decision_agent_us_open=30`, `finbert_batch=900`, `macro_data=21600`, `funding_rate=28800`, `balance_snapshot=300` | `core/config.py:35` |
| `USMarketConfig` | dataclass | `timezone="US/Eastern"`, `open_hour=9`, `open_minute=30`, `close_hour=16` | `core/config.py:49` |
| `IndicatorConfig` | dataclass | `rsi_period=14`, `macd_fast/slow/signal=12/26/9`, `bollinger_period=20`, `bollinger_std=2`, `ema_short=9`, `ema_long=21` | `core/config.py:57` |
| `DashboardConfig` | dataclass | `host="127.0.0.1"`, `port=8050`, `debug=False`, `update_interval=2000` | `core/config.py:69` |
| `LoggingConfig` | dataclass | `level="INFO"`, `file="data_store/logs/trading_bot.log"`, `max_bytes=10485760`, `backup_count=5` | `core/config.py:77` |
| `ExchangeConfig` | dataclass | `name="binance"`, `type="future"`, `sandbox=True` | `core/config.py:85` |
| `ScanningConfig` | dataclass | `dynamic_top_volume=True`, `top_n=10`, `refresh_interval=3600` | `core/config.py:92` |
| `ScalpingConfig` | dataclass | 19 fields: TP/SL `min_profit_pct=0.0060`, `fast_tp_pct=0.0060`, `tight_sl_pct=0.0025`; gating `min_confidence=0.40`, `reversal_close_threshold=0.70`; timing `max/min_hold_seconds=300/15`, `batch_size=2`; tick guard `max_tick_age_seconds=1.5`, `stale_tick_window_seconds=3.0`, `stale_tick_min_samples=5`; microstructure `orderbook_imbalance_threshold=0.60`, `momentum_threshold=0.0008`, `max_spread_pct=0.0006`; breakeven `breakeven_trigger_pct=0.0020`, `breakeven_offset_pct=0.0015`; cooldown `20.0`/`90.0` | `core/config.py:99` |
| `DynamicTpSlConfig` | dataclass | ATR-driven: `atr_multiple=1.5`, `atr_period=14`, `min_sl_pct=0.0025`, `max_sl_pct=0.0150`, `min_risk_reward=1.5`, `max_breakeven_win_rate=0.65`, `realized_window_seconds=30.0` | `core/config.py:159` |
| `EnsembleAgentConfig` | dataclass | `enabled: bool = True`, `weight: float = 0.25` | `core/config.py:219` |
| `EnsembleConfig` | dataclass | `interval_seconds=5`, `shrinkage_delta=0.85`, `agreement_bonus=0.5`, `min_prob=0.02`, `max_snapshot_age_seconds=20`, `diffusion_horizon_minutes=30`, `diffusion_points=60`, `momentum_window_seconds=30.0`, `min_trades_for_microstructure=8`, `min_returns_for_vol=20`; four `EnsembleAgentConfig` fields `orderflow=0.30`, `momentum=0.25`, `technical=0.25`, `microstructure=0.20` | `core/config.py:227` |
| `LiveConfig` | dataclass | real-money guardrails, all defaults refuse; see below | `core/config.py:292` |
| `AppConfig` | dataclass | root: 16 sub-configs + `symbols: List[str]` (10 defaults), `database_path="data_store/trading_bot.db"`, `snapshot_prune_interval=3600`, `snapshot_keep_per_symbol=120`, `agent_log_prune_interval=1800`, `agent_log_keep=5000`, `news_rss`, `cryptopanic_url`, `cryptopanic_token` | `core/config.py:360` |

**`EnsembleConfig` methods.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `agent_configs` | method | `def agent_configs(self) -> dict:` | `core/config.py:274` |
| `base_weight` | method | `def base_weight(self, agent_name: str) -> float:` | `core/config.py:283` |

**`LiveConfig` key fields.** `enabled: bool = False`, `testnet: bool = True`, `private_key_env: str = "HYPERLIQUID_PRIVATE_KEY"`, `live_confirm_env: str = "TRADEBOT_LIVE_CONFIRMED"`, `live_window_utc: Tuple[int, int] = (13, 23)`, `max_leverage=10`, `max_order_notional=100.0`, `max_position_notional=300.0`, `max_total_notional=600.0`, `max_daily_orders=200`, `max_daily_loss=50.0`, `max_consecutive_errors=3`, `min_free_collateral=100.0`, `use_exchange_side_tpsl=True`, `auto_reconcile=False`, `reconciliation_tolerance_days=0`. There is deliberately no private-key field: credentials come from the environment at runtime, never from the committed config.

**Loader contract.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `_apply_dict` | function | `def _apply_dict(obj, data: dict):` | `core/config.py:408` |
| `load_config` | function | `def load_config(path: str = "config.yaml") -> AppConfig:` | `core/config.py:415` |
| `_validate_dynamic_tp_sl` | function | `def _validate_dynamic_tp_sl(config: AppConfig) -> None:` | `core/config.py:531` |
| `_validate_ensemble_config` | function | `def _validate_ensemble_config(config: AppConfig) -> None:` | `core/config.py:630` |
| `_validate_scalping_economics` | function | `def _validate_scalping_economics(config: AppConfig) -> None:` | `core/config.py:707` |
| `_config` | global | `_config: Optional[AppConfig] = None` | `core/config.py:790` |
| `get_config` | function | `def get_config() -> AppConfig:` | `core/config.py:793` |
| `reload_config` | function | `def reload_config(path: str = "config.yaml") -> AppConfig:` | `core/config.py:801` |

`load_config` runs in fixed order: start from a fully-defaulted `AppConfig`; return it unchanged if the path is missing; `yaml.safe_load`; apply each of the 14 top-level sections via `_apply_dict`, which **ignores any key the dataclass does not already have** (unknown YAML keys are dropped, never injected). `ensemble` is special-cased — the nested `agents:` mapping is popped and applied one level deeper, because plain `_apply_dict` would overwrite an `EnsembleAgentConfig` with a raw dict. `database.path` sets `database_path`; `database.snapshot_*` keys apply to `AppConfig` itself. `news_sources.rss` / `.cryptopanic.base_url` / `.cryptopanic.token` map to `news_rss` / `cryptopanic_url` / `cryptopanic_token` (empty token not assigned). Then, unconditionally, **`config.live.enabled = False`** is forced after the `live` block — `live.enabled: true` in YAML is ignored by design, because the file is committed to git. The three validators then run and may `raise ValueError`, so a bad config fails at load, not mid-trade.

**Validator invariants.** `_validate_scalping_economics`: `breakeven_trigger_pct < min_profit_pct`, net R:R ≥ 1 after `taker*2`, `breakeven_offset_pct >= roundtrip`, `min_confidence <= reversal_close_threshold < 1.0`, `stale_tick_window_seconds > max_tick_age_seconds`, `stale_tick_min_samples >= 2`. `_validate_ensemble_config`: ≥1 enabled agent, non-negative weights summing > 0, `shrinkage_delta ∈ (0,1]`, `min_prob ∈ (0,0.5)`, `max_snapshot_age_seconds >= 2*interval_seconds`, `diffusion_points >= 2`. `_validate_dynamic_tp_sl`: `0 < min_sl_pct < max_sl_pct`, `atr_multiple > 0`, `min_risk_reward > 1.0`, `max_breakeven_win_rate ∈ (0.5,1.0)`, `atr_period >= 2`, and minimum TP at smallest SL must beat the roundtrip fee. Each collects all problems and raises one bulleted `ValueError`.

**State it owns.** Module-global `_config`, populated lazily on first `get_config()`, replaced by `reload_config(path)`. **Side effects.** One file read and one YAML parse per call; no writes.

**Consumers.** `core/{logger,scheduler}.py`, `dashboard/{app.py,callbacks/update_callbacks.py,layouts/price_chart.py}`, `agents/{analysis,decision,direction,execution}_agent.py`, `analysis/{backtester,direction_ensemble,ml_signals,technical,volatility,vol_target}.py`, `data/{macro_fetcher,news_fetcher,price_feed}.py`, `database/db.py`, `trading/live/safety.py`.

#### 9.4.18 `core/event_bus.py`

**Role.** Asyncio pub/sub for inter-agent messaging. Each subscriber gets its own bounded `asyncio.Queue`; publishing fans out to every queue on a channel.

**Key symbols.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `Event` | dataclass | `channel: str`, `data: Any`, `source: str = ""`, `timestamp: str` (ISO-8601 UTC, default factory) | `core/event_bus.py:18` |
| `EventBus.__init__` | method | `def __init__(self, maxsize: int = 1000):` | `core/event_bus.py:37` |
| `EventBus.subscribe` | async method | `async def subscribe(self, channel: str) -> asyncio.Queue:` | `core/event_bus.py:42` |
| `EventBus.unsubscribe` | async method | `async def unsubscribe(self, channel: str, queue: asyncio.Queue):` | `core/event_bus.py:52` |
| `EventBus.publish` | async method | `async def publish(self, channel: str, data: Any, source: str = ""):` | `core/event_bus.py:62` |
| `EventBus.get_channel_stats` | method | `def get_channel_stats(self) -> Dict[str, int]:` | `core/event_bus.py:86` |
| `Channels` | class (constants) | `PRICE_UPDATE`, `NEWS_SENTIMENT`, `MARKET_ANALYSIS`, `TRADE_DECISION`, `TRADE_EXECUTED`, `POSITION_UPDATE`, `BALANCE_UPDATE`, `AGENT_LOG`, `SYSTEM_EVENT`, `DIRECTION_ENSEMBLE` | `core/event_bus.py:92-103` |

**State it owns.** `EventBus._channels: Dict[str, List[asyncio.Queue]]`, `._maxsize: int`, `._lock: asyncio.Lock`.

**Side effects.** `publish` drops the oldest event when a subscriber queue is full (`QueueFull` → `get_nowait` → retry, warn if it still fails), so a slow consumer loses stale events rather than stalling the publisher. No I/O, no DB, no global mutation.

**Consumers.** `agents/{base,analysis,decision,execution,news}_agent.py`, `data/price_feed.py`, `run.py`, `trading/{paper_engine,position_manager}.py`, `data_store/{apply_candidate,_snap/hf_cand}.py`, and seven test modules.

#### 9.4.19 `core/logger.py`

**Role.** Structured logging: ANSI console formatter with keyword highlighting, plain rotating file handler, and a single global startup progress bar.

**Key symbols.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `RESET`, `BOLD`, `DIM` | constants | ANSI style escapes | `core/logger.py:18-20` |
| `CYAN`, `BRIGHT_BLUE`, `GREEN`, `AMBER`, `RED`, `PURPLE`, `GRAY`, `DARK_GRAY`, `WHITE` | constants | 256-color ANSI palette | `core/logger.py:23-31` |
| `LEVEL_BADGES` | constant | `Dict[str, str]` — level name → colored badge | `core/logger.py:33` |
| `MODULE_COLORS` | constant | `Dict[str, str]` — 11 known module names → color | `core/logger.py:41` |
| `ColoredConsoleFormatter.format` | method | `def format(self, record: logging.LogRecord) -> str:` | `core/logger.py:59` |
| `NEWLINE` | constant | `"\r"` | `core/logger.py:105` |
| `_QUIET` | global | `bool` from `TRADEBOT_QUIET_STARTUP` | `core/logger.py:106` |
| `_QUIET_LOGGER` | global | `logging.Logger` or `None` | `core/logger.py:109` |
| `_make_progress_aware_emit` | function | `def _make_progress_aware_emit(original_emit):` | `core/logger.py:123` |
| `setup_logger` | function | `def setup_logger(name: str = "trading_bot") -> logging.Logger:` | `core/logger.py:155` |
| `begin_quiet_mode` | function | `def begin_quiet_mode(name: str = "trading_bot"):` | `core/logger.py:218` |
| `end_quiet_mode` | function | `def end_quiet_mode():` | `core/logger.py:225` |
| `_BAR_WIDTH` | constant | `28` | `core/logger.py:236` |
| `_STAGE_COLORS` | constant | `Dict[str, str]` — 7 stage prefixes → color | `core/logger.py:240` |
| `_stage_active` | global | `{"label", "done", "total", "color", "_paused", "_started"}` | `core/logger.py:254` |
| `_stage_color` | function | `def _stage_color(label: str) -> str:` | `core/logger.py:258` |
| `_render_bar` | function | `def _render_bar(label, done, total, color, width=_BAR_WIDTH):` | `core/logger.py:265` |
| `stage` | function | `def stage(label: str, total: int = 0):` | `core/logger.py:279` |
| `step` | function | `def step(done: int = None, label: str = None):` | `core/logger.py:316` |
| `_draw_stage` | function | `def _draw_stage():` | `core/logger.py:336` |
| `stage_done` | function | `def stage_done(message: str = None, label: str = None):` | `core/logger.py:361` |
| `is_quiet` | function | `def is_quiet() -> bool:` | `core/logger.py:391` |
| `get_logger` | function | `def get_logger(module_name: str) -> logging.Logger:` | `core/logger.py:396` |

**State it owns.** `_QUIET` (module `bool`), `_QUIET_LOGGER`, and the shared bar dict `_stage_active`. All startup stages share one bar: `stage()` swaps the label and *adds* to the global total, never resetting `done`; `stage_done()` closes it with an ASCII `[OK]` line (a `✓` glyph throws `UnicodeEncodeError` on the default Windows cp1252 console). `end_quiet_mode()` flips `_QUIET` back to `False` after boot so runtime events stay visible.

**Side effects.** Creates `data_store/logs/` and writes `trading_bot.log` via `RotatingFileHandler(maxBytes=10 MiB, backupCount=5)`. When `_QUIET` is set at import it also sets `HF_HUB_DISABLE_PROGRESS_BARS=1` and `TOKENIZER_PARALLELISM=false` in `os.environ` *before* those libraries import. `_make_progress_aware_emit` wraps the console handler's `emit` to write a newline first while a bar is active, so log lines are not overwritten by the next `\r` update. `_draw_stage` writes to `sys.stdout`; on a non-TTY it prints a deduplicated `label pct%` line instead.

**Consumers.** `core/{event_bus,microstructure,scheduler}.py`, `dashboard/{app.py,callbacks/update_callbacks.py}`, `ml/{trainer,predictor}.py`, `agents/*`, `analysis/*`, `data/*`.

#### 9.4.20 `core/market_store.py`

**Role.** In-process real-time market store shared between the background WebSocket/REST loops and the 500 ms Dash callbacks. Contract: an unfilled field returns `None`, never a default that looks like real data.

**Key symbols.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `PRICE_HISTORY_MAX` | constant | `240` (~2 min at 2 Hz; HUD ticks at 500 ms) | `core/market_store.py:19` |
| `market_store` | singleton | `market_store = MarketStore()` | `core/market_store.py:265` |
| `MarketStore.__init__` | method | `def __init__(self):` | `core/market_store.py:25` |
| `set_price` | method | `def set_price(self, symbol: str, price: float):` | `core/market_store.py:39` |
| `set_ticker` | method | `def set_ticker(self, symbol: str, ticker: dict):` | `core/market_store.py:55` |
| `get_ticker` | method | `def get_ticker(self, symbol: str) -> Optional[dict]:` | `core/market_store.py:64` |
| `get_price` | method | `def get_price(self, symbol: str) -> Optional[float]:` | `core/market_store.py:74` |
| `get_price_age` | method | `def get_price_age(self, symbol: str) -> Optional[float]:` | `core/market_store.py:91` |
| `get_all_prices` | method | `def get_all_prices(self) -> Dict[str, float]:` | `core/market_store.py:101` |
| `get_price_history` | method | `def get_price_history(self, symbol: str, seconds: Optional[float] = None) -> List[tuple]:` | `core/market_store.py:105` |
| `get_price_change` | method | `def get_price_change(self, symbol: str, seconds: float = 60.0) -> Optional[float]:` | `core/market_store.py:128` |
| `set_order_book` | method | `def set_order_book(self, symbol: str, order_book: dict):` | `core/market_store.py:147` |
| `get_order_book` | method | `def get_order_book(self, symbol: str) -> Optional[dict]:` | `core/market_store.py:152` |
| `has_order_book` | method | `def has_order_book(self, symbol: str) -> bool:` | `core/market_store.py:167` |
| `get_order_book_age` | method | `def get_order_book_age(self, symbol: str) -> Optional[float]:` | `core/market_store.py:171` |
| `set_live_candle` | method | `def set_live_candle(self, symbol: str, candle: dict):` | `core/market_store.py:187` |
| `get_live_candle` | method | `def get_live_candle(self, symbol: str) -> Optional[dict]:` | `core/market_store.py:198` |
| `set_funding` / `get_funding` | methods | `def set_funding(self, symbol: str, funding_rate: float):` / `def get_funding(self, symbol: str) -> Optional[float]:` | `core/market_store.py:211` / `:218` |
| `set_open_interest` / `get_open_interest` | methods | `def set_open_interest(self, symbol: str, open_interest: float):` / `def get_open_interest(self, symbol: str) -> Optional[float]:` | `core/market_store.py:228` / `:235` |
| `set_recent_trades` / `get_recent_trades` | methods | `def set_recent_trades(self, symbol: str, trades: list):` / `def get_recent_trades(self, symbol: str) -> list:` | `core/market_store.py:248` / `:253` |

**State it owns.** `._tickers: Dict[str, dict]`, `._order_books: Dict[str, dict]`, `._last_prices: Dict[str, float]`, `._price_ts: Dict[str, float]`, `._price_history: Dict[str, deque]` (maxlen 240), `._live_candles: Dict[str, dict]`, `._funding: Dict[str, float]`, `._open_interest: Dict[str, float]`, `._recent_trades: Dict[str, list]` (capped at 50).

**Side effects.** Pure in-memory mutation; no locks, no I/O. `set_price` rejects non-numeric and non-positive prices. Every getter falls back to base-asset matching on the segment before `/` and `:` (so `BTC/USDT:USDT` matches `BTC/USDT`), never free substring matching — otherwise `NEAR` would match unrelated symbols. `set_live_candle` also feeds the candle close into `set_price`. `get_order_book_age` converts the book's epoch-ms `timestamp` to seconds.

**Consumers.** Writers: `data/{price_feed,hyperliquid_feed}.py`. Readers: `dashboard/callbacks/update_callbacks.py`, `agents/{decision,direction,execution}_agent.py`, `analysis/{volatility,vol_target}.py`, `trading/{paper_engine,position_manager,live/executor}.py`; tests `test_bugfixes`, `test_direction_agents`.

#### 9.4.21 `core/microstructure.py`

**Role.** Sole owner of L2 orderbook parsing, Order Flow Imbalance and depth imbalance, behind an interface that a C++20 extension can back with no change to any caller.

**The Python/C++ swap contract.**

| Piece | Detail | Loc |
|---|---|---|
| Abstract interface | `class MicrostructureKernel(ABC)`; abstract `def ingest_l2(self, symbol: str, bids: list, asks: list) -> None:`, `def order_flow_imbalance(self, symbol: str, depth: int = 5) -> Tuple[float, float]:`, `def depth_imbalance(self, symbol: str, depth: int = 5) -> float:` | `core/microstructure.py:57`, `:67`, `:71`, `:76` |
| Module façade | `def ingest_l2(symbol: str, bids: list, asks: list) -> None:`, `def order_flow_imbalance(symbol: str, depth: int = 5) -> Tuple[float, float]:`, `def depth_imbalance(symbol: str, depth: int = 5) -> float:`, `def reset_microstructure(symbol: Optional[str] = None) -> None:` — all delegate to the global `_KERNEL` | `core/microstructure.py:218-237` |
| Active kernel | `_KERNEL: MicrostructureKernel = PythonKernel()` | `core/microstructure.py:182` |
| `get_kernel` | function | `def get_kernel() -> MicrostructureKernel:` | `core/microstructure.py:185` |
| `register_kernel` | function | `def register_kernel(kernel: MicrostructureKernel) -> None:` | `core/microstructure.py:190` |
| Native adapter | `CppMicrostructureKernel(MicrostructureKernel)`; `def __init__(self, max_symbols: int = 64, native_module=None):`, `def ingest_json(self, payload: bytes):` (parses an `l2Book` frame entirely in C++, returns coin name or `None` for a non-l2Book channel, raises on malformed payloads), `def symbol_count(self) -> int:` | `core/microstructure.py:260`, `:276`, `:296`, `:305` |
| Availability probe | `def cpp_kernel_available() -> bool:` — a function, not a module constant, because tests probe it after a mid-session build | `core/microstructure.py:245` |
| Boot hook | `def initialize_native_kernel(force: bool = False) -> bool:` | `core/microstructure.py:309` |

`register_kernel` validates that `ingest_l2`, `order_flow_imbalance`, `depth_imbalance`, `reset` are all callable, raising `TypeError` listing missing methods, then rebinds `_KERNEL` and logs old → new class name.

`initialize_native_kernel` env contract: `TRADEBOT_KERNEL=python` forces `PythonKernel`, returns `False` (unless `force=True`, which lets tests compare both). `TRADEBOT_KERNEL=cpp` requires the extension; if it cannot be imported it raises `RuntimeError` carrying the exact `cmake -S . -B build -DCMAKE_BUILD_TYPE=Release && cmake --build build --config Release` commands rather than degrading silently. Unset: it attempts the C++ import, registers the adapter and returns `True` on success, else logs the reason and returns `False` — a missing compiler, unbuilt module or ABI mismatch never breaks the system, because parity (not speed) is guaranteed and `tests/test_cpp_kernel.py` holds parity honest. Call site: `data/hyperliquid_feed.py:115`, result kept on `self._native_kernel`.

**`PythonKernel` behaviour** (`core/microstructure.py:81`). `__init__(self, max_symbols: int = 64)` bounds tracked symbols so a misconfigured feed cannot grow state without limit. `_weighted_volume(levels, depth)` (staticmethod) applies linearly decreasing per-level weights (1.0, 0.9, 0.8, …) — raw sums bias toward the far level, which usually holds the most volume and represents a resting book, not intent. `order_flow_imbalance` returns `(ofi, relative_spread)` with `ofi = (bid_vol - ask_vol) / (bid_vol + ask_vol)` clamped to `[-1, 1]` and spread `(best_ask - best_bid) / mid` clamped at 0 — relative, because a 0.10 absolute spread means something very different on BTC than on DOGE. Every division is guarded: a 0 price (exchange boundary sentinel) yields `0.0`, not `ZeroDivisionError`. `reset(symbol=None)` clears all or one symbol.

**State it owns.** `PythonKernel._books: Dict[str, Tuple[list, list]]`, `._max_symbols: int`, and the module-global `_KERNEL` — the only mutable state in the hot path, so a native implementation must be thread-safe or carry its own lock.

**Side effects.** None beyond in-memory state. All methods are documented I/O-free and allocation-light so they can move to C++/Rust via pybind11 or CFFI unchanged.

**Consumers.** `data/hyperliquid_feed.py` (ingests L2, owns native init), `agents/direction_agents.py`, `analysis/probability_engine.py`; tests `test_cpp_kernel.py`, `test_lifecycle_paths.py`.

#### 9.4.22 `core/scheduler.py`

**Role.** APScheduler `AsyncIOScheduler` wrapper that swaps agent intervals when the US equity market opens or closes, plus the market-hours predicate.

**Key symbols.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `is_us_market_open` | function | `def is_us_market_open() -> bool:` | `core/scheduler.py:19` |
| `AgentScheduler.__init__` | method | `def __init__(self):` | `core/scheduler.py:46` |
| `add_agent_job` | method | `def add_agent_job(self, name: str, func: Callable, interval_normal: int, interval_us_open: int, start_immediately: bool = True):` | `core/scheduler.py:51` |
| `add_fixed_job` | method | `def add_fixed_job(self, name: str, func: Callable, interval_seconds: int):` | `core/scheduler.py:88` |
| `_adjust_intervals` | async method | `async def _adjust_intervals(self):` | `core/scheduler.py:100` |
| `start` | method | `def start(self):` | `core/scheduler.py:118` |
| `shutdown` | method | `def shutdown(self):` | `core/scheduler.py:132` |
| `running` | property | `def running(self) -> bool:` | `core/scheduler.py:138` |

**State it owns.** `._scheduler: AsyncIOScheduler`, `._jobs: dict` mapping name → `{"func", "interval_normal", "interval_us_open", "current_interval"}`, `._check_interval: int = 60`.

**Side effects.** Registers jobs on APScheduler and logs interval changes. `is_us_market_open` returns `False` for Saturday/Sunday and otherwise compares `hour*60 + minute` against the configured open/close in `timezone` (default `US/Eastern`). `add_agent_job` picks the right interval at registration, sets `max_instances=1` / `replace_existing=True`, and with `start_immediately` also schedules a one-shot `<name>_init` job. `start()` adds a `_market_check` job calling `_adjust_intervals` every 60 s, which reschedules only jobs whose target interval actually changed. All jobs are `max_instances=1`, so a slow run is never stacked.

**Consumers.** `run.py`, `agents/decision_agent.py`.

#### 9.4.23 `core/utils.py`

**Role.** Converts SQLite UTC timestamp values to Unix seconds, avoiding the "local OS assumed the UTC string was local" off-by-hours bug.

**Key symbols.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `parse_db_timestamp` | function | `def parse_db_timestamp(ts_val: Any) -> float:` | `core/utils.py:9` |

**State / side effects.** None. Falsy input → `0.0`; `int`/`float` passes through; strings are stripped, a trailing `Z` becomes `+00:00`, parsed with `datetime.fromisoformat`, and a naive result (SQLite `datetime('now')` has no tzinfo but is UTC) is stamped `timezone.utc`. Any parse failure returns `0.0`.

**Consumers.** `agents/{decision,execution}_agent.py`.

#### 9.4.24 `ml/__init__.py`

**Role.** Package marker only — `"""ML package — pelatihan dan prediksi model."""`, no exports (`ml/__init__.py:1`).

#### 9.4.25 `ml/trainer.py`

**Role.** Offline trainer for the RandomForest direction classifier. Run as a script (`python -m ml.trainer`), never imported by the live path. It is the only writer of the model artifact.

**Key symbols.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `MODEL_DIR` | constant | `Path("ml/models")` | `ml/trainer.py:18` |
| `ModelTrainer.__init__` | method | `def __init__(self, lookahead: int = 10, threshold_pct: float = 0.005):` | `ml/trainer.py:33` |
| `prepare_features` | method | `def prepare_features(self, df: pd.DataFrame) -> pd.DataFrame:` | `ml/trainer.py:42` |
| `create_labels` | method | `def create_labels(self, df: pd.DataFrame) -> pd.Series:` | `ml/trainer.py:80` |
| `train` | method | `def train(self, df: pd.DataFrame) -> dict:` | `ml/trainer.py:96` |
| `train_from_exchange` | async function | `async def train_from_exchange(symbol: str = "BTC/USDT:USDT", timeframe: str = "1h", limit: int = 1000):` | `ml/trainer.py:171` |

**Feature contract.** `prepare_features` writes seven columns in fitted order: `rsi` (÷100), `macd_hist_norm` (MACD histogram ÷ close, clipped ±0.01, ×100), `bb_position` ((close − lower) ÷ band range, NaN→0.5), `ema_trend` ((EMA9 − EMA21) ÷ close, clipped, ×100), `volume_ratio` (volume ÷ SMA20, clipped [0,3], ÷3, NaN→0.5), `sentiment_score` (defaults `0.0` when absent), `atr_pct` (ATR ÷ close, clipped [0,0.1], ×10). `create_labels` computes the `lookahead`-bar forward return: above `+threshold_pct` → `"LONG"`, below `-threshold_pct` → `"SHORT"`, else `"HOLD"`.

**Model artifact lifecycle — write side.** `train` drops the last `lookahead` rows and returns `{"accuracy": 0, "error": "Data terlalu sedikit"}` under 100 valid samples. Otherwise: `RandomForestClassifier(n_estimators=100, max_depth=10, min_samples_split=10, min_samples_leaf=5, random_state=42, n_jobs=-1)`, split `test_size=0.2, random_state=42, shuffle=False` (chronological), then `MODEL_DIR.mkdir(parents=True, exist_ok=True)` and `joblib.dump(model, "ml/models/signal_model.pkl")` (`ml/trainer.py:154-156`). Returns `accuracy`, `report`, `feature_importance`, `model_path`, `samples_train`, `samples_test`. The artifact is **overwritten in place** every run — no versioning, no backup.

**Side effects.** Network (ccxt / yfinance); creates `ml/models/`; writes the pickle. `train_from_exchange` tries `ccxt.async_support.binance` with `defaultType: future` under a 4-second timeout, always closing the exchange in `finally`; on failure it falls back to `yf.Ticker("BTC-USD").history(period="1y", interval="1h")` via `asyncio.to_thread`. If both yield nothing it returns `{"error": "No data"}` and writes nothing.

**Consumers.** No first-party importer. Invoked only as `python -m ml.trainer` (`ml/trainer.py:232-233`).

#### 9.4.26 `ml/predictor.py`

**Role.** Thin wrapper over `analysis.ml_signals.MLSignalGenerator` for standalone use; owns no model logic.

**Key symbols.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `Predictor.__init__` | method | `def __init__(self):` | `ml/predictor.py:20` |
| `initialize` | async method | `async def initialize(self):` | `ml/predictor.py:23` |
| `predict` | method | `def predict(self, technical_signals: Dict, sentiment_score: float = 0.0, df=None) -> Dict:` | `ml/predictor.py:28` |
| `model_available` | property | `def model_available(self) -> bool:` | `ml/predictor.py:43` |

**State it owns.** `self.generator: MLSignalGenerator`.

**Return contract.** `predict` forwards to the generator and yields `{"action": "LONG"|"SHORT"|"HOLD", "confidence": float, "probabilities": {LONG, SHORT, HOLD}, "method": "ML"|"RULE_BASED"|"NONE"}`.

**Model artifact lifecycle — read side.** The generator (`analysis/ml_signals.py:23`) holds `_model`, `_model_loaded`, and a seven-name `_feature_names` list. `initialize()` offloads `_load_model` to the default executor; that raises `FileNotFoundError` when `ml/models/signal_model.pkl` is absent, which `initialize` catches, logs, and converts into `_model_loaded = False` (`analysis/ml_signals.py:46-61`). **There is no hot reload** — the pickle is read once at startup, so retraining needs a process restart. With no model loaded, `predict` falls through to `_predict_rule_based`; the HUD can therefore show a live bot while the ML path is silently degraded, and `Predictor.model_available` is the flag to check.

**Side effects.** One `joblib.load`. No writes.

**Consumers.** None — `ml/predictor.py` is the only file referencing `Predictor`. The live path uses `agents/analysis_agent.py:19,50,61,116`, which constructs `MLSignalGenerator` directly, awaits `initialize()`, and calls `predict`.

---

### 9.5 Import table

Every first-party import edge that crosses a group boundary, including imports inside a function body (marked with the line where they occur rather than at module scope). `Why` states what the edge is actually for. Rows are sorted by importer group, then importer file, then imported file.

| Importer | Imports | Where | Why |
|---|---|---|---|
| `agents/analysis_agent.py` | `data/macro_fetcher.py` | `agents/analysis_agent.py:15` | `fetch_all()` + `interpret_macro_context()` produce the macro factor and risk level |
| `agents/analysis_agent.py` | `data/price_feed.py` | `agents/analysis_agent.py:14` | 200 × 5m OHLCV bars per symbol, the input to the technical and ML passes |
| `agents/analysis_agent.py` | `data/sentiment.py` | `agents/analysis_agent.py:16` | FinBERT batch rescore of already-stored news, on the 15-minute `finbert_batch` job |
| `agents/analysis_agent.py` | `database/models.py` | `agents/analysis_agent.py:20` | `Signal` row written once per analysis cycle |
| `agents/analysis_agent.py` | `core/config.py` | `agents/analysis_agent.py:21` | `get_config()` supplies indicator periods, scalping thresholds and the risk budget |
| `agents/analysis_agent.py` | `core/event_bus.py` | `agents/analysis_agent.py:13` | injected bus; publishes `MARKET_ANALYSIS`, drains the queue the same channel feeds |
| `agents/analysis_agent.py` | `core/logger.py` | `agents/analysis_agent.py:22` | module logger channel |
| `agents/base_agent.py` | `database/db.py` | `agents/base_agent.py:15` | `get_db()` builds the lazily-cached `Repository` in `_get_repo` |
| `agents/base_agent.py` | `database/models.py` | `agents/base_agent.py:17` | `AgentLog` — the single row written per cycle or per error |
| `agents/base_agent.py` | `database/repository.py` | `agents/base_agent.py:16` | `Repository(get_db())`; the only DB access the base class performs |
| `agents/base_agent.py` | `core/event_bus.py` | `agents/base_agent.py:13` | injected `EventBus`; every agent constructor takes one and `publish()` is the base class's only fan-out |
| `agents/base_agent.py` | `core/logger.py` | `agents/base_agent.py:14` | `get_logger(f"agent.{name}")` gives each agent its own coloured channel |
| `agents/decision_agent.py` | `trading/models.py` | `agents/decision_agent.py:17` | `Order`, `TradeAction`, `Side`, `TradeDecision` — the wire format handed to whichever executor is bound |
| `agents/decision_agent.py` | `trading/risk_manager.py` | `agents/decision_agent.py:18` | injected; its `risk_manager` is passed on to `ExecutionAgent`, so this is a wiring edge as much as a call edge |
| `agents/decision_agent.py` | `database/models.py` | `agents/decision_agent.py:19` | `Signal` dataclass for the analysis payload drained from the bus |
| `agents/decision_agent.py` | `core/config.py` | `agents/decision_agent.py:14` | cooldown, batch size, spread ceiling, min confidence and snapshot age all read from config, not literals |
| `agents/decision_agent.py` | `core/event_bus.py` | `agents/decision_agent.py:13` | injected bus; publishes `TRADE_DECISION`, drains `MARKET_ANALYSIS` and `POSITION_UPDATE` |
| `agents/decision_agent.py` | `core/logger.py` | `agents/decision_agent.py:20` | module logger channel |
| `agents/decision_agent.py` | `core/market_store.py` | `agents/decision_agent.py:16` | live `get_order_book` for the spread gate — the one real-time input to the open/close decision |
| `agents/decision_agent.py` | `core/scheduler.py` | `agents/decision_agent.py:15` | `is_us_market_open()`, returned by `sense` as context; deliberately not used as a gate |
| `agents/decision_agent.py` | `core/utils.py` | `agents/decision_agent.py:21` | `parse_db_timestamp` decides whether a `direction_snapshots` row is fresh enough to trade on |
| `agents/direction_agents.py` | `core/config.py` | `agents/direction_agents.py:38` | ensemble weights, momentum window, diffusion horizon, min-trades and snapshot age |
| `agents/direction_agents.py` | `core/market_store.py` | `agents/direction_agents.py:39` | in-memory reads only — order book, price change, recent trades, funding |
| `agents/execution_agent.py` | `trading/models.py` | `agents/execution_agent.py:20` | `Order` built from the decision payload in `act` |
| `agents/execution_agent.py` | `trading/risk_manager.py` | `agents/execution_agent.py:21` | built internally; `calculate_stop_loss` / `calculate_take_profit` convert percentages into absolute levels |
| `agents/execution_agent.py` | `core/config.py` | `agents/execution_agent.py:16` | scalping thresholds and the dynamic TP/SL bounds the agent clamps against |
| `agents/execution_agent.py` | `core/event_bus.py` | `agents/execution_agent.py:15` | injected bus; `TRADE_DECISION` and `PRICE_UPDATE` subscriptions |
| `agents/execution_agent.py` | `core/logger.py` | `agents/execution_agent.py:18` | module logger channel |
| `agents/execution_agent.py` | `core/market_store.py` | `agents/execution_agent.py:17` | per-cycle prices pushed into `engine.update_price` and used for the SL/TP percentage maths |
| `agents/execution_agent.py` | `core/utils.py` | `agents/execution_agent.py:19` | `parse_db_timestamp` for position age and the min-hold window |
| `agents/news_agent.py` | `data/news_fetcher.py` | `agents/news_agent.py:13` | RSS + CryptoPanic headlines, de-duplicated in-process and again via `news_exists` |
| `agents/news_agent.py` | `data/sentiment.py` | `agents/news_agent.py:14` | VADER scoring per item, `aggregate_sentiment` across the batch |
| `agents/news_agent.py` | `database/models.py` | `agents/news_agent.py:15` | `NewsItem` and `Signal` dataclasses for the two inserts in `think`/`act` |
| `agents/news_agent.py` | `core/event_bus.py` | `agents/news_agent.py:12` | injected bus; publishes the `NEWS_SENTIMENT` aggregate and the top-5 high-impact titles |
| `agents/news_agent.py` | `core/logger.py` | `agents/news_agent.py:16` | module logger channel |
| `analysis/backtester.py` | `core/config.py` | `analysis/backtester.py:36` | default database path handed to `load_l2_events` |
| `analysis/backtester.py` | `core/logger.py` | `analysis/backtester.py:37` | replay and strategy-callback logging |
| `analysis/fundamental.py` | `core/logger.py` | `analysis/fundamental.py:7` | module logger channel |
| `analysis/ml_signals.py` | `core/config.py` | `analysis/ml_signals.py:15` | `get_config()` in the feature and rule-based-fallback path |
| `analysis/ml_signals.py` | `core/logger.py` | `analysis/ml_signals.py:16` | logs once when the model artifact is absent and predictions degrade to rule-based |
| `analysis/probability_engine.py` | `core/microstructure.py` | `analysis/probability_engine.py:32` | the one impure call in the module: `calculate_order_flow_imbalance` delegates to the L2 kernel |
| `analysis/technical.py` | `core/config.py` | `analysis/technical.py:11` | indicator periods snapshotted once at construction rather than read per call |
| `analysis/technical.py` | `core/logger.py` | `analysis/technical.py:12` | warns and returns the frame unchanged when it is shorter than the MACD window |
| `analysis/vol_target.py` | `core/config.py` | `analysis/vol_target.py:28` | `risk.max_risk_per_trade` is the default base risk fraction |
| `analysis/vol_target.py` | `core/logger.py` | `analysis/vol_target.py:29` | module logger channel |
| `analysis/vol_target.py` | `core/market_store.py` | `analysis/vol_target.py:30` | 300 s price history for the daily-vol estimate; the module has no other state |
| `analysis/volatility.py` | `core/config.py` | `analysis/volatility.py:25` | dynamic TP/SL bounds and the taker-fee roundtrip used by the volatility gate |
| `analysis/volatility.py` | `core/logger.py` | `analysis/volatility.py:26` | ATR cache diagnostics |
| `analysis/volatility.py` | `core/market_store.py` | `analysis/volatility.py:27` | 1m candles and last price behind ATR and realized volatility |
| `trading/live/client.py` | `core/logger.py` | `trading/live/client.py:28` | address derivation and quantisation logging — the key itself is never passed to it |
| `trading/live/console.py` | `core/logger.py` | `trading/live/console.py:30` | mode banner and live-limit edit logging |
| `trading/live/engine.py` | `core/config.py` | `trading/live/engine.py:29` | `LiveConfig` for window, leverage and exposure limits |
| `trading/live/engine.py` | `core/logger.py` | `trading/live/engine.py:30` | `critical` when SL placement fails, the log line that accompanies kill-switch engagement |
| `trading/live/executor.py` | `core/logger.py` | `trading/live/executor.py:21` | module logger channel |
| `trading/live/executor.py` | `core/market_store.py` | `trading/live/executor.py:22` | last-resort price in `_price_for` when the `Order` carries none |
| `trading/live/safety.py` | `core/config.py` | `trading/live/safety.py:24` | `LiveConfig` supplies all 17 blocker ceilings; no limit is hard-coded in the gate |
| `trading/live/safety.py` | `core/logger.py` | `trading/live/safety.py:25` | `can_send` logs every blocker value, so a refusal is always explained |
| `trading/paper_engine.py` | `analysis/volatility.py` | `trading/paper_engine.py:112,295` | registers the synchronous candle source (a process-wide module global) and raises `VolatilityGateError` |
| `trading/paper_engine.py` | `database/db.py` | `trading/paper_engine.py:16` | `get_db()` inside `_get_repo` |
| `trading/paper_engine.py` | `database/models.py` | `trading/paper_engine.py:18` | `AgentLog` rows for every rejection and every execution |
| `trading/paper_engine.py` | `database/repository.py` | `trading/paper_engine.py:17` | account init, candle reads, daily realized PnL and open positions |
| `trading/paper_engine.py` | `core/config.py` | `trading/paper_engine.py:12` | scalping, dynamic TP/SL and risk config behind the six-step rejection ladder |
| `trading/paper_engine.py` | `core/event_bus.py` | `trading/paper_engine.py:13` | `TRADE_EXECUTED` publish on a filled open |
| `trading/paper_engine.py` | `core/logger.py` | `trading/paper_engine.py:14` | module logger channel |
| `trading/paper_engine.py` | `core/market_store.py` | `trading/paper_engine.py:15` | fill-price resolution and the 3 s outlier window in `_tick_quality_guard` |
| `trading/position_manager.py` | `database/db.py` | `trading/position_manager.py:16` | `get_db()` inside the lazily built `_get_repo` |
| `trading/position_manager.py` | `database/models.py` | `trading/position_manager.py:18` | `Position`, `Trade`, `BalanceSnapshot` |
| `trading/position_manager.py` | `database/repository.py` | `trading/position_manager.py:17` | the only writer of position, trade and balance rows in the codebase |
| `trading/position_manager.py` | `core/config.py` | `trading/position_manager.py:12` | `get_config()` for position, risk and scalping settings |
| `trading/position_manager.py` | `core/event_bus.py` | `trading/position_manager.py:13` | publishes `POSITION_UPDATE` on open, close and liquidation |
| `trading/position_manager.py` | `core/logger.py` | `trading/position_manager.py:14` | module logger channel |
| `trading/position_manager.py` | `core/market_store.py` | `trading/position_manager.py:15` | last prices driving the liquidation → SL → TP sweep in `update_positions` |
| `trading/risk_manager.py` | `core/config.py` | `trading/risk_manager.py:7` | `RiskConfig` and `FeeConfig` supply all four pre-trade limits and the maker/taker rates |
| `trading/risk_manager.py` | `core/logger.py` | `trading/risk_manager.py:8` | module logger channel |
| `data/hyperliquid_feed.py` | `core/logger.py` | `data/hyperliquid_feed.py:40` | per-channel counters, reconnect backoff and the native-kernel outcome |
| `data/hyperliquid_feed.py` | `core/market_store.py` | `data/hyperliquid_feed.py:41` | the only fan-out path for every HL frame — the injected bus is stored and never published to |
| `data/hyperliquid_feed.py` | `core/microstructure.py` | `data/hyperliquid_feed.py:42` | `initialize_native_kernel` once at boot, then `ingest_l2` per book frame |
| `data/macro_fetcher.py` | `core/config.py` | `data/macro_fetcher.py:14` | held on the instance; the FRED key and calendar endpoint come from construction, not config |
| `data/macro_fetcher.py` | `core/logger.py` | `data/macro_fetcher.py:15` | per-series fetch logging and the FRED `"."` missing-value path |
| `data/news_fetcher.py` | `core/config.py` | `data/news_fetcher.py:13` | RSS list, CryptoPanic base URL and auth token |
| `data/news_fetcher.py` | `core/logger.py` | `data/news_fetcher.py:14` | per-feed parse logging and the missing-token debug line |
| `data/price_feed.py` | `core/config.py` | `data/price_feed.py:24` | `config.symbols` (mutated in place by `update_symbols`), database path, scan settings |
| `data/price_feed.py` | `core/event_bus.py` | `data/price_feed.py:25` | `PRICE_UPDATE` broadcast on the back-fill loop |
| `data/price_feed.py` | `core/logger.py` | `data/price_feed.py:26` | tier fallback and the sticky ccxt kill-switch |
| `data/price_feed.py` | `core/market_store.py` | `data/price_feed.py:394,424,523,582,593` | `set_ticker` / `set_funding` / `set_order_book` / `set_price` from the REST tiers that the WS does not cover |
| `data/sentiment.py` | `core/logger.py` | `data/sentiment.py:11` | model-load logging and the silent-degradation path when FinBERT fails to load |
| `database/db.py` | `core/config.py` | `database/db.py:7` | `get_config().database_path` is the default connection target |
| `database/db.py` | `core/logger.py` | `database/db.py:8` | module logger channel |
| `database/repository.py` | `core/logger.py` | `database/repository.py:12` | prune and claim-conflict logging |
| `dashboard/callbacks/update_callbacks.py` | `analysis/probability_engine.py` | `dashboard/callbacks/update_callbacks.py:20` | recomputes technical z-scores and the composite probability for the indicator panel |
| `dashboard/callbacks/update_callbacks.py` | `analysis/technical.py` | `dashboard/callbacks/update_callbacks.py:26` | indicator readout in the legacy compat callback |
| `dashboard/callbacks/update_callbacks.py` | `data/price_feed.py` | `dashboard/callbacks/update_callbacks.py:958` | function-local `get_price_feed()` to read Hyperliquid WS per-channel message counters for the constellation |
| `dashboard/layouts/hud_figures.py` | `analysis/probability_engine.py` | `dashboard/layouts/hud_figures.py:223` | renders the drift-diffusion convergence curve from `compute_directional_curve` output |
| `ml/predictor.py` | `analysis/ml_signals.py` | `ml/predictor.py:11` | wraps `MLSignalGenerator`; owns no model logic of its own |
| `run.py` | `agents/analysis_agent.py` | `run.py:59` | Agent 2, plus the `macro_update` and `finbert_batch` jobs |
| `run.py` | `agents/decision_agent.py` | `run.py:60` | Agent 3, constructed with the engine's risk manager |
| `run.py` | `agents/direction_agents.py` | `run.py:62` | ensemble agent, registered only when `config.ensemble.enabled` |
| `run.py` | `agents/execution_agent.py` | `run.py:61` | Agent 4, driven by the 0.3 s execution loop |
| `run.py` | `agents/news_agent.py` | `run.py:58` | Agent 1, scheduled on the news interval (300 s, 120 s when the US market is open) |
| `run.py` | `trading/live/client.py` | `run.py:257` | function-local `LiveExchange` construction inside the live path |
| `run.py` | `trading/live/console.py` | `run.py:169,196` | function-local mode selection and the two-factor confirmation prompts |
| `run.py` | `trading/live/engine.py` | `run.py:258` | function-local `LiveEngine` construction inside the live path |
| `run.py` | `trading/live/executor.py` | `run.py:259` | function-local `LiveExecutor`, assigned as `self.executor` at line 365 |
| `run.py` | `trading/live/safety.py` | `run.py:260` | function-local `SafetyGate` construction inside the live path |
| `run.py` | `trading/paper_engine.py` | `run.py:57` | bound as the default executor whenever live mode is off |
| `run.py` | `data/macro_fetcher.py` | `run.py:55` | constructed and wired into `AnalysisAgent` |
| `run.py` | `data/price_feed.py` | `run.py:54` | constructs, initialises, discovers top-volume symbols and starts streaming |
| `run.py` | `data/sentiment.py` | `run.py:56` | constructed with `use_finbert=True` and initialised at boot |
| `run.py` | `database/db.py` | `run.py:52,633,682` | `init_db` / `get_db` / `close_db` bracket the process lifetime |
| `run.py` | `database/repository.py` | `run.py:53` | one shared `Repository` handed to the engine and the agents |
| `run.py` | `core/config.py` | `run.py:41,256` | composition root; `get_config()` at boot, `LiveConfig` function-locally inside `_build_live_executor` |
| `run.py` | `core/event_bus.py` | `run.py:50` | constructs the single `EventBus` shared by every agent and both engines |
| `run.py` | `core/logger.py` | `run.py:42` | boot progress bar and the per-module channel loggers |
| `run.py` | `core/scheduler.py` | `run.py:51` | registers every agent job and drives the US-market interval swap |
| `run.py` | `dashboard/app.py` | `run.py:63` | `run_dashboard` started on a background `threading.Thread` |

### 9.6 Group summary

Aggregates the edges in 9.5. **May import** lists the first-party files a group can reach across a group boundary; every group also imports freely inside its own packages, named in the first column. **Imported by** is the reverse fan-in — the practical way to answer "what breaks if I change this file". `run.py` is listed separately as the composition root because it imports all four groups and is imported by none of them; it is not documented as a subsection in this section.

Two shapes are worth naming. No two peer groups import each other, so the graph is acyclic between groups; the only reverse edge anywhere is `core/config.py` reading `core/logger.py`, which is a same-group cycle. And only the infrastructure group is imported by all three of the others, which is what makes `core/` the only safe place for a shared singleton.

| Group | Files | Lines | May import | Imported by |
|---|---|---|---|---|
| **Signal and agent layers** — `agents/`, `analysis/` | 16 | 4,747 | `core/config.py`, `core/event_bus.py`, `core/logger.py`, `core/market_store.py`, `core/microstructure.py`, `core/scheduler.py`, `core/utils.py`, `data/macro_fetcher.py`, `data/news_fetcher.py`, `data/price_feed.py`, `data/sentiment.py`, `database/db.py`, `database/models.py`, `database/repository.py`, `trading/models.py`, `trading/risk_manager.py` | `dashboard/callbacks/update_callbacks.py`, `dashboard/layouts/hud_figures.py`, `ml/predictor.py`, `trading/paper_engine.py` |
| **Execution, position and risk layers** — `trading/` | 11 | 5,031 | `analysis/volatility.py`, `core/config.py`, `core/event_bus.py`, `core/logger.py`, `core/market_store.py`, `database/db.py`, `database/models.py`, `database/repository.py` | `agents/decision_agent.py`, `agents/execution_agent.py` |
| **Data ingestion and persistence** — `data/`, `database/` | 10 | 2,857 | `core/config.py`, `core/event_bus.py`, `core/logger.py`, `core/market_store.py`, `core/microstructure.py` | `agents/analysis_agent.py`, `agents/base_agent.py`, `agents/decision_agent.py`, `agents/news_agent.py`, `dashboard/callbacks/update_callbacks.py`, `trading/paper_engine.py`, `trading/position_manager.py` |
| **UI and infrastructure layers** — `core/`, `dashboard/`, `ml/` | 26 | 7,517 | `analysis/ml_signals.py`, `analysis/probability_engine.py`, `analysis/technical.py`, `data/price_feed.py` | `agents/analysis_agent.py`, `agents/base_agent.py`, `agents/decision_agent.py`, `agents/direction_agents.py`, `agents/execution_agent.py`, `agents/news_agent.py`, `analysis/backtester.py`, `analysis/fundamental.py`, `analysis/ml_signals.py`, `analysis/probability_engine.py`, `analysis/technical.py`, `analysis/vol_target.py`, `analysis/volatility.py`, `data/hyperliquid_feed.py`, `data/macro_fetcher.py`, `data/news_fetcher.py`, `data/price_feed.py`, `data/sentiment.py`, `database/db.py`, `database/repository.py`, `trading/live/client.py`, `trading/live/console.py`, `trading/live/engine.py`, `trading/live/executor.py`, `trading/live/safety.py`, `trading/paper_engine.py`, `trading/position_manager.py`, `trading/risk_manager.py` |
| **Composition root** — `run.py` | 1 | 878 | `agents/analysis_agent.py`, `agents/decision_agent.py`, `agents/direction_agents.py`, `agents/execution_agent.py`, `agents/news_agent.py`, `core/config.py`, `core/event_bus.py`, `core/logger.py`, `core/scheduler.py`, `dashboard/app.py`, `data/macro_fetcher.py`, `data/price_feed.py`, `data/sentiment.py`, `database/db.py`, `database/repository.py`, `trading/live/client.py`, `trading/live/console.py`, `trading/live/engine.py`, `trading/live/executor.py`, `trading/live/safety.py`, `trading/paper_engine.py` | — |
| **Total (documented)** | **63** | **20,152** | | |


---

## Appendix A — System map

Every tracked source file in the repository, with its line count, one-line role, and owning subsystem. Line counts re-measured on 2026-09-28. **Total first-party Python: 19,688 lines across 62 modules (61 package modules at 18,810 lines, plus `run.py` at 878; 10 of the 62 are `__init__.py` docstring-only package markers).**

### A.1 `core/` — foundation, L0, no first-party imports except itself

| File | Lines | Role | Subsystem |
|---|---:|---|---|
| `core/config.py` | 805 | 16 dataclasses, `_apply_dict` loader, 3 boot validators, `LiveConfig` with forced-false `enabled` | config |
| `core/logger.py` | 403 | Rotating file + console handlers, quiet mode, one global ASCII progress bar | config |
| `core/microstructure.py` | 369 | `MicrostructureKernel` ABC, `PythonKernel`, `CppMicrostructureKernel`, module-global `_KERNEL` | native kernel |
| `core/market_store.py` | 265 | Unsynchronized process-wide in-memory store: prices, books, candles, tape, funding, OI | state |
| `core/scheduler.py` | 139 | `AgentScheduler` wrapper, US-session open check, interval retiming | scheduling |
| `core/event_bus.py` | 103 | 10-channel `asyncio.Queue` fan-out, drops OLDEST on overflow | messaging |
| `core/utils.py` | 31 | `parse_db_timestamp` — naive-UTC parser returning `0.0` on failure | state |
| `core/__init__.py` | 1 | package marker, docstring only | — |

### A.2 `agents/` — L5, the decision layer

| File | Lines | Role | Subsystem |
|---|---:|---|---|
| `agents/direction_agents.py` | 525 | 4 `DirectionAgent` specialists + `DirectionEnsembleAgent`, the sole `direction_snapshots` writer | direction ensemble |
| `agents/decision_agent.py` | 380 | Reads the newest snapshot row, opens/closes positions, cooldowns, leverage selection | direction ensemble |
| `agents/execution_agent.py` | 318 | Drains the decision queue, advisory SL/TP, breakeven → scalp-TP → expiry → engine scan | execution |
| `agents/analysis_agent.py` | 318 | 0.40/0.25/0.35 technical/fundamental/ML blend, macro, FinBERT batch | analysis |
| `agents/base_agent.py` | 151 | `run_cycle` = sense→think→act→log, `CancelledError` re-raise, `AgentLog` audit | agents (base) |
| `agents/news_agent.py` | 145 | VADER/FinBERT scoring, impact bucketing, title dedupe | news |
| `agents/__init__.py` | 1 | package marker, docstring only | — |

### A.3 `analysis/` — L2, pure computation

| File | Lines | Role | Subsystem |
|---|---:|---|---|
| `analysis/backtester.py` | 980 | L2 queue-position FIFO fill model + metrics. **Tests-only; never called in production** | backtest |
| `analysis/probability_engine.py` | 433 | 5-factor Bayesian composite, diffusion curve, `compute_mathematical_edge` | analysis |
| `analysis/volatility.py` | 412 | Wilder ATR, dynamic TP/SL, `assess_volatility_gate`, time-bucketed `_ATR_CACHE` | risk |
| `analysis/technical.py` | 314 | RSI/MACD/BBands/EMA/ROC/ATR/OBV, `generate_signals` voting | analysis |
| `analysis/ml_signals.py` | 257 | 7-slot feature vector, RandomForest inference, rule-based fallback | ML |
| `analysis/direction_ensemble.py` | 227 | Log-odds pooling, `abstain`, agreement scoring, `MAX_AGENT_Z = 2.5` | direction ensemble |
| `analysis/fundamental.py` | 153 | Macro/sentiment/calendar scorecard, `get_suggested_leverage` | analysis |
| `analysis/vol_target.py` | 132 | **DEAD** — zero importers anywhere | — |
| `analysis/__init__.py` | 1 | package marker, docstring only | — |

### A.4 `trading/` — L4, the money layer

| File | Lines | Role | Subsystem |
|---|---:|---|---|
| `trading/paper_engine.py` | 876 | Zero-slippage fill, 5-gate open ladder, tick guard, SL/TP rebuild at fill, balance identity | execution (paper) |
| `trading/position_manager.py` | 453 | Open/close/liquidate, claim-once, net-of-both-fees P&L, balance snapshot | risk |
| `trading/risk_manager.py` | 429 | `validate_trade` 4 limits, sizing (2 variants), fees, `ROUND_DOWN` quanta, liquidation price | risk |
| `trading/live/engine.py` | 757 | Live order sequencing, `_attach_protection`, `reconcile`, `health_check`, `emergency_flat` | live |
| `trading/live/console.py` | 751 | TTY menu, `ask_mode`, `edit_rules` operator rule editor | live |
| `trading/live/tui.py` | 572 | Terminal rendering, key decoding, TextField, VT processing | live |
| `trading/live/client.py` | 493 | Hyperliquid SDK wrapper, `quantize_size` rounding **up** to `szMin`, rate-limit docs | live |
| `trading/live/safety.py` | 423 | `SafetyGate`: 9 master + 8 per-order blockers, `DayCounters` persistence | live |
| `trading/live/executor.py` | 190 | `LiveExecutor` adapter. **Implements 2 of the 6 members `ExecutionAgent` calls** | live |
| `trading/models.py` | 86 | `Order`, `Position`, `Trade`, `BalanceSnapshot` dataclasses | models |
| `trading/__init__.py` | 1 | package marker, docstring only | — |
| `trading/live/__init__.py` | — | **DOES NOT EXIST** — PEP 420 implicit namespace package (verified) | — |

### A.5 `data/` — L3, ingestion

| File | Lines | Role | Subsystem |
|---|---:|---|---|
| `data/price_feed.py` | 705 | 4-tier fallback (Hyperliquid → ccxt Binance → yfinance → CoinGecko), top-volume scan, 1.5 s price safety net | data |
| `data/hyperliquid_feed.py` | 573 | WS transport, backoff, `_parse_book`/`_parse_candle`, `_ensure_native_kernel` | data |
| `data/macro_fetcher.py` | 218 | FRED via raw `requests` + Forex Factory, `interpret_macro_context` | macro |
| `data/sentiment.py` | 213 | VADER + ProsusAI/FinBERT (512-char truncation), `aggregate_sentiment` | news |
| `data/news_fetcher.py` | 154 | RSS via feedparser, CryptoPanic, title dedupe | news |
| `data/__init__.py` | 1 | package marker, docstring only | — |

### A.6 `database/` — L1, persistence

| File | Lines | Role | Subsystem |
|---|---:|---|---|
| `database/repository.py` | 604 | All SQL. Atomic `apply_balance_delta`, claim-once close/liquidate, `get_daily_realized_pnl` | persistence |
| `database/db.py` | 258 | `SCHEMA_SQL` (10 tables, 8 indexes), WAL, `busy_timeout=15000`, aiosqlite driver | persistence |
| `database/models.py` | 130 | Row dataclasses, no first-party imports | persistence |
| `database/__init__.py` | 1 | package marker, docstring only | — |

### A.7 `dashboard/` — L6, the operator HUD

| File | Lines | Role | Subsystem |
|---|---:|---|---|
| `dashboard/callbacks/update_callbacks.py` | 1429 | 19 callbacks (11 live at 2 Hz, 8 compat stubs). No EventBus subscription; own `sqlite3` per call | dashboard |
| `dashboard/layouts/hud_figures.py` | 972 | Every Plotly figure incl. the constellation node-link graph. **5 stale copies live under `data_store/_snap*`** | dashboard |
| `dashboard/layouts/hud.py` | 440 | The single-screen 12-column grid; 5 legacy tab layouts survive as hidden shims | dashboard |
| `dashboard/layouts/price_chart.py` | 209 | Candlestick + tape figures | dashboard |
| `dashboard/layouts/performance.py` | 160 | Equity/performance figures | dashboard |
| `dashboard/layouts/palette.py` | 139 | Parses `style.css` `:root` at import, re-exports hex. Plotly must never see `var()` | dashboard |
| `dashboard/layouts/news_feed.py` | 117 | News panel | dashboard |
| `dashboard/layouts/positions.py` | 95 | Positions panel | dashboard |
| `dashboard/layouts/agent_logs.py` | 69 | Agent-log panel | dashboard |
| `dashboard/app.py` | 148 | `create_dash_app`, two `dcc.Interval` (500 ms / 60 s), `_silence_flask_banner` | dashboard |
| `dashboard/assets/style.css` | 1253 | `:root` design tokens, the HUD grid, one `@keyframes` | dashboard |
| `dashboard/assets/neural_flow.css` | 89 | Animated edge dashes for the constellation, own 2 keyframes | dashboard |
| `dashboard/__init__.py`, `dashboard/callbacks/__init__.py`, `dashboard/layouts/__init__.py` | 3 | package markers, docstring only | — |

### A.8 `ml/` — offline only

| File | Lines | Role | Subsystem |
|---|---:|---|---|
| `ml/trainer.py` | 233 | RandomForest training from ccxt/yfinance, writes `signal_model.pkl`. No argparse. **No production caller** | ML |
| `ml/predictor.py` | 44 | Inference wrapper. **Zero importers anywhere, including tests** | ML |
| `ml/models/signal_model.pkl` | 3,903,681 B | Shipped artifact, 100 trees, `feature_importances_[5] == 0.0` exactly | ML |
| `ml/__init__.py` | 1 | package marker, docstring only | — |

### A.9 Root

| File | Lines | Role | Subsystem |
|---|---:|---|---|
| `run.py` | 878 | Sole runtime entry point. 16 module-level first-party imports, boot sequence, 11 scheduler jobs, 5 asyncio tasks, 0.3 s tick | runtime |
| `config.yaml` | 214 | 16 top-level blocks, 110 leaf keys (121 keys total). Overwrites the dataclass defaults on 13 economic knobs | config |
| `requirements.txt` | 45 | 23 declared packages; 4 unimported, `playwright` undeclared | config |
| `CMakeLists.txt` | 152 | C++20 build, pybind11 discovery, rejects `-Ofast`, POST_BUILD strip, output to project root | native kernel |
| `README.md` | 305 | Operator doc. **Four counts wrong vs. code** (§1.1) | docs |
| `.gitignore` | 102 | Ignores `*.pyd`, the sidecar DLLs, `_*.log`, `data_store/*.db` | — |
| `cpp/microstructure_kernel.cpp` | 643 | C++20 kernel: `Book`, `BookStore`, `weighted_volume`, `book_ofi`, `book_depth_imbalance`, `parse_l2book_json` | native kernel |
| `cpp/bindings.cpp` | 164 | pybind11 module. **`book_from_python` is `noexcept` but throws (§6.3)** | native kernel |
| `cpp/include/microstructure_kernel.h` | 172 | `Book`/`BookStore` declarations, mutex rationale, DLL chain note | native kernel |
| `cpp/include/simdjson.h` | 347 | **Misnomer** — hand-written scalar l2Book JSON-subset parser, not upstream simdjson | native kernel |
| `cpp_microstructure.cp314-win_amd64.pyd` | 408,576 B | Prebuilt extension, in sync with source, CPython-3.14-only | native kernel |
| `libwinpthread-1.dll`, `api-ms-win-crt-private-l1-1-0.dll` | 63 KB / 73 KB | Required runtime sidecars; both gitignored yet present | native kernel |
| `bands_rows.png`, `column_split_preview.png` | — | Screenshot artifacts of past dashboard debugging | docs |

Root debug/measurement scripts are listed with line counts in §8.8 (20 files, 2,900 lines). Root `_*.log` (18 files, 724 KB) and `_*.txt` analysis dumps are shell-redirect artifacts; `.gitignore:6-10` states outright that no code reads them.

### A.10 `tests/` — 24 test modules, 9,130 lines, 566 collected cases

Line counts in §7.1. Per-file case counts sum to 566. **20 of 62 production modules have no test import at all** (§7.1).

### A.11 `data_store/` — measurement tooling, not shipped code

**Nothing in `data_store/` is imported by the running bot.** It contains 32 Python files (8,759 lines: 16 at the `data_store/` root, 3 in `_snap/`, 12 in `_snap_cp/`, 1 in `_snapshot/`) of dashboard-geometry measurement and calibration tooling, plus five frozen copies of `hud_figures.py` (§8.9), plus the live database `trading_bot.db` (+ `-wal`, `-shm`, an 80 MB `trading_bot.db.backup-20260928-142831`), `live_counters.json`, `critiques.txt` (66 KB), `backups/` (4 `.db` files, ~195 MB, pre-dating the 2026-09-25 DB reset series), and `logs/` (`trading_bot.log` 32,082 lines, plus `run_debug.log` 495 lines with no configured writer in the repo).

> **DEFECT — the highest-consequence file in this directory is `data_store/layered.py`.** At `:103` it calls `apply(hf)` on the **live** `hud_figures` module object at import time, so merely importing anything from `data_store/` silently changes production dashboard geometry. See D67 in Appendix B.

| Directory | Contents |
|---|---|
| `data_store/*.py` (16 files) | `constellation_design.py`, `constellation_measure.py`, `constellation_search.py`, `measure_lib.py` (408), `measure_baseline.py`, `measure_candidate.py`, `measure_truth.py`, `measure_panel.py`, `analyze_edges.py`, `count_crossings.py`, `apply_candidate.py`, `layered.py`, `calibrate_probe.py`, `_cal2.py`, `validate_model.py`, `show_report.py` |
| `data_store/_snap/`, `_snap_cp/`, `_snapshot/` | 16 files: frozen copies of `hud_figures.py` and the calibration harness (`calib.py`, `calibrate.py`, `calib_offsets.py`, `model.py`, `harness.py`, `test_harness.py`, `validate_model.py`, `run_measure.py`, `measure_panel.py`, `dbg*.py`, …). None are on any import path. |
| `data_store/*.json` | `_base.json` (baseline calibration state), `baseline_report.json`, `truth_report.json`, `report_cand.json`, `crossings_report.json`, `live_counters.json` — measurement outputs of the scripts above; all orphaned from the running bot |
| `data_store/*.txt` | `critiques.txt` (66 KB), `_edges.txt`, `_nodes.txt`, `_val.txt`, `_md5_now.txt`, `dashboard_check.txt`, `_val.txt` — analysis dumps feeding the constellation tooling |
| `data_store/_snap*` (3 dirs, 5 files) | Frozen `hud_figures` copies, 893-937 lines each, all stale |
| `data_store/_snap_cp/` (17 files) | Calibration baseline: `harness.py`, `model.py`, `calibrate.py`, `validate_model.py`, debug scripts |
| `data_store/*.json`, `critiques.txt` | Measurement reports and a 66 KB critique log |
| `data_store/*.db`, `backups/`, `logs/` | Runtime state and backups |

**Editing the wrong file here is the actual failure mode** — `data_store/layered.py:103` mutates the live `hud_figures` module object in place at import.

### A.12 `build/` — CMake scratch, 8+ files

Ninja generator state (`build.ninja`, `.ninja_log`, `.ninja_deps`, `CMakeCache.txt`) and compiled objects (`cpp_microstructure.dir/cpp/*.obj`). Not source. Regenerated by `cmake -S . -B build`.

---

## Appendix B — Defect register

Consolidated across all sections, ordered by consequence. Each was filed once; the module section does not repeat it.

### B.1 Live path — the two hard blockers

| # | Defect | Evidence | Consequence |
|---|---|---|---|
| D1 | `LiveExecutor` does not implement the `ExecutionAgent` interface | `trading/live/executor.py:53-189` (methods: `__init__`, `update_price`, `execute_order`, `_open`, `_close`, `_price_for`) vs `agents/execution_agent.py:96, 192, 213, 266, 270, 290, 311` | `AttributeError` on **every** 0.3 s tick, swallowed by `run.py:596`. **Re-verified by grep during integration.** |
| D2 | `LiveExecutor._open` sends `size=0.0` because `DecisionAgent` never sets `Order.quantity` | `executor.py:103`; `decision_agent.py:264-272`; `safety.py:319-320` | Second, independent blocker on the same path. **Re-verified verbatim in `_open`.** |
| D3 | `SafetyGate.record_realized_pnl` has zero production callers | `safety.py:293, 415`; only other hits `tests/test_live_safety.py:361, 390` | `DAILY_LOSS_LIMIT` can never fire. Live has **no daily-loss breaker**. **Re-verified.** |
| D4 | `cpp/bindings.cpp:32` marks `book_from_python` `noexcept` but it calls `item.cast<std::pair<double,double>>()`, which throws `py::cast_error` | `cpp/bindings.cpp:32, 37, 41` | `std::terminate`, exit 127, no catchable Python exception. Production safe today only because `data/hyperliquid_feed.py:313-350` normalizes first. |
| D5 | `LiveEngine.emergency_flat` has zero callers; `SafetyGate.engaged` has zero releasers | `engine.py:655`; `safety.py:384-388`; `run.py` | The only flatten routine is unwired; a tripped kill switch has no recovery path. **Re-verified by grep.** |
| D6 | Naked position on crash between fill and `_attach_protection` | `engine.py:295-349, 397-404`; `run.py:279-282` | On restart, `reconcile` logs only, `health_check` flags only, and the bot **refuses to start** with the position still unprotected. |
| D7 | `LiveEngine.run_loop` task is never cancelled by `shutdown()` | `run.py:292` vs `run.py:794-814` | The exchange poll loop outlives shutdown. `shutdown()` is also reachable twice (signal handler + `finally`) without being idempotent. |
| D8 | `quantize_size` rounds a sub-`szMin` size **up**, after the gate approved the smaller notional | `client.py:322-325` called from `client.py:398`, after `engine.py:236` | A gate-approved 1e-6 BTC order can reach the venue at `szMin` — 10× the approved exposure. |
| D9 | Failed TP is silently tolerated; only a failed SL escalates | `engine.py:397-404` | A position can ride to full stop-loss loss with no kill switch engaged. |
| D10 | `_build_live_executor` builds a bare `LiveConfig()`, discarding every operator-edited limit | `run.py:262` vs `run.py:171` | The console's entire rule editor is unreachable in normal operation. |
| D11 | `safety.py:95` annotates `-> Dict[str, Any]` but `Dict` is not imported | `safety.py:14, 22, 95` | Survives only because `from __future__ import annotations` makes annotations lazy strings. Any runtime introspection raises `NameError`. |
| D12 | `engine.py:32` imports `Blocker` and never uses it | `trading/live/engine.py:32` | Dead import on the safety path. |
| D13 | `README.md` claims a 100 % simulation posture, but `trading/live/` and `run.py:238-295` implement a real-money path reachable via `--testnet` / `--live` | `README.md:1-20`; `run.py:180, 238` | The doc understates the blast radius of the live stack. |

### B.2 Paper path — money-affecting

| # | Defect | Evidence | Consequence |
|---|---|---|---|
| D14 | Zero-slippage, zero-spread, zero-depth fill model | `paper_engine.py:159-179, 191` | Paper results are an upper bound on performance, a lower bound on risk. Correct model exists at `backtester.py:239-300`, never called. |
| D15 | Exit fills use an unbounded-stalency price | `execution_agent.py:211-215, 264-268, 288-292`; `paper_engine.py:155-157` | A position can be closed at a price from minutes ago, while the open path enforces a 1.5 s age limit. |
| D16 | Every close path bypasses the tick guard, the volatility gate and `validate_trade` | single `_tick_quality_guard` call at `paper_engine.py:495`; `validate_trade` only at `:566` | Three of four exit paths are unguarded. |
| D17 | Liquidation records `realized_pnl = -margin`, `fee = 0`, omitting the opening fee | `position_manager.py:391` vs `:203` | The daily-loss breaker reads a smaller loss than the balance actually took, and fires later than it should. |
| D18 | Liquidation skips `bump_peak_balance` and `update_account_stats` | `position_manager.py:384-418` vs `:255-269` | Drawdown high-water mark and all account aggregates go stale. |
| D19 | Drawdown breaker mixes measures: mark includes unrealized, peak is cash + margin only | `paper_engine.py:549` vs `position_manager.py:255-256` | The ratio is systematically overstated; intra-trade equity gains are never captured. |
| D20 | Position open is two separate commits | `position_manager.py:85` then `:107` | A crash between them destroys margin with no position row. |
| D21 | Close is two separate commits: `UPDATE positions` then `INSERT trades` | `repository.py:168` then `:212` | A crash leaves a CLOSED position with no trade row; fee and gross unrecoverable. |
| D22 | A third, un-injected `RiskManager` degrades the daily-loss denominator | `execution_agent.py:40`; `risk_manager.py:319` | The exact shrinking-denominator failure the comment at `:306-318` warns against. |
| D23 | No funding P&L anywhere | `grep -rn funding trading/` → 0 hits | Systematic over-statement of carry on a perp. |
| D24 | `max_leverage` clamps sizing but not the stored margin or liquidation price | `paper_engine.py:604`; `position_manager.py:67-72`; `risk_manager.py:97, 358` | Not exploitable today (leverage can only be 5/7/10) but the cap does not exist on the path that matters. |
| D25 | `calculate_scalp_position_size` has no `stop_distance` input | `risk_manager.py:340-378` | Real per-trade risk is `0.017 %` of balance, not the `0.5 %` `max_risk_per_trade` implies. |
| D26 | `account.max_drawdown` and `account.sharpe_ratio` are never written | `repository.py:356-393` | The dashboard shows `N/A`; nothing in the product can ever display a drawdown number from the DB. |
| D27 | `backfill_realized_pnl.py`'s P&L formula is a hand-maintained duplicate of production | `backfill_realized_pnl.py:47-50` vs `position_manager.py:203` | The two will diverge silently. |

### B.3 Configuration and numerics

| # | Defect | Evidence | Consequence |
|---|---|---|---|
| D28 | `agent_log_prune_interval` and `agent_log_keep` are silently dropped at load | `core/config.py:509-511` filter is `k.startswith("snapshot_")` | Editing `config.yaml:115-116` does nothing, with no diagnostic. Coincidentally harmless today. |
| D29 | Ten `DynamicTpSlConfig` fields shadow `ScalpingConfig` with identical defaults, all unread | `core/config.py:200-215` vs `:98-155` | Editing the wrong block silently has no effect. |
| D30 | `exchange.{name,type,sandbox}` are read nowhere; `name: "binance"` is actively misleading | `config.yaml:30-33`; venue is hardcoded at `hyperliquid_feed.py:46-47` | The `exchange:` and `fees:` blocks contradict each other on the same page. |
| D31 | `agent_intervals.funding_rate: 28800` is never registered as a job | `config.yaml:72`; `core/config.py:44` | Declared, never scheduled. |
| D32 | `dashboard.update_interval` has zero readers | `config.yaml:123`; `app.py:53, 63` hardcode 500/60000 | Pure noise. |
| D33 | `live.use_exchange_side_tpsl` and `live.reconciliation_tolerance_days` have zero readers | `core/config.py:347, 356` | Dead-by-definition config. |
| D34 | `scalping.orderbook_imbalance_threshold` and its `dynamic_tp_sl` copy have zero readers | `config.yaml:156`; `core/config.py:139, 203` | Dead-by-definition config. |
| D35 | `Blocker.RECONCILIATION_FAILED` is defined and never appended | `safety.py:45` | A reconcile mismatch surfaces as `KILL_SWITCH`, hiding the real cause. |
| D36 | `probability_engine.py:299` `drift_per_min = z_score * 0.45 * sigma` — the `0.45` has no derivation anywhere | `probability_engine.py:19-20, 299` | It alone sets the diffusion curve's steepness, and the module docstring describes a different formula than the code implements. |
| D37 | `calculate_pnl` calls `.quantize(Decimal("0.01"))` with **no `rounding=`** | `risk_manager.py:268-270` | Silently uses `ROUND_HALF_EVEN` in the one place the rest of the money layer is `ROUND_DOWN`. Every PnL figure in the DB goes through it. |
| D38 | `atr_1m_pct` must request `period + 1` candles or ATR is always `None` | `volatility.py:176-183` | Getting this wrong silently disables every dynamic target in production while unit tests that inject `candles=` stay green. |
| D39 | The C++ kernel does not guard NaN/inf | no `isfinite` anywhere in `cpp/` | `ingest_l2('N', [[nan,1.0]], [[101.0,1.0]])` → `(0.0, nan)`, which `OrderFlowAgent` treats as valid data. |
| D40 | C++ silently truncates past 32 levels per side, never binding the overflow flags to Python | `microstructure_kernel.cpp:96-98, 107-109` | A venue adding levels would corrupt OFI invisibly. |
| D41 | `kLevelWeight0`/`kLevelWeightStep` are dead on the hot path — C++ re-hardcodes the literal | `microstructure_kernel.cpp:57-58` vs `:192-198` | Changing them makes the exported `level_weight()` lie while OFI stays unchanged. |
| D42 | `kLevelWeight` thread-safety argument is incomplete: `find()` returns a raw pointer after dropping the mutex, and OFI is called with the GIL released | `microstructure_kernel.h:68-72`; `microstructure_kernel.cpp:137-145`; `bindings.cpp:63` | Pointer stability rests solely on `books_.reserve()`. |
| D43 | ML train/serve skew: slot 1, 2, 3, 4 use different formulas on each side; slot 5 is a constant at train time and live at serve time | `ml/trainer.py:48-77` vs `ml_signals.py:37-40, 63-135` | `feature_importances_[5] == 0.0` exactly. The model is effectively 6-feature receiving a 7-vector. |
| D44 | `price_chart.py` and `performance.py` pass the literal string `"palette.alpha(palette.INK_MUTED, 0.30)"` as a color at 7 sites | `price_chart.py:130,139,142,198`; `performance.py:93,109,147` | Not valid CSS, so the canvas paints nothing. |

### B.4 Process, concurrency and observability

| # | Defect | Evidence | Consequence |
|---|---|---|---|
| D45 | `market_store` docstring says "thread-safe"; there is no lock in the file | `core/market_store.py:23` | Three threads (feed, asyncio loop, Dash callback) write. Safety rests on the GIL. |
| D46 | `DirectionEnsembleAgent.run_cycle` overrides the base entirely | `agents/direction_agents.py:437-451` | The sole writer of `direction_snapshots` writes **no `AgentLog` audit row** and has no `CancelledError` branch. |
| D47 | Blanket `except Exception` in the 0.3 s loop | `run.py:591-597` | A permanently broken executor produces a healthy-looking log full of identical errors and zero orders. |
| D48 | The WebSocket task handle is discarded | `run.py:337` | `start_streaming()`'s task cannot be cancelled by `shutdown()`. |
| D49 | `PaperTradingEngine._candle_refresh_task` is created and never awaited, cancelled or inspected | `paper_engine.py:67, 135` | Re-initialising the engine orphans the previous task. |
| D50 | `EventBus` is not concurrency-safe as a singleton; `get_db()` is not either | `database/db.py:239-246` | Two concurrent first-callers can both construct; the second overwrites and leaks. |
| D51 | `init_account` is check-then-insert with no transaction and no `UNIQUE` | `repository.py:324-335` | Two concurrent callers insert two `account` rows; the older is orphaned. |
| D52 | `_get_sync_db` opens a fresh `sqlite3.connect` per callback, closed only on the success path | `update_callbacks.py:64` | ~22 connects/sec at the 500 ms cadence, and a mid-callback raise leaks the handle. |
| D53 | `data/price_feed.py` Hyperliquid branches use bare `except Exception: pass` | `price_feed.py:356-363` and funding/mark branches | An HL-tier failure drops silently to ccxt with **no log line**, unlike the other tiers. |
| D54 | Four asyncio tasks are untracked by `_background_tasks` | `run.py:777-779` holds exactly 5; WS, `_refresh_cache`, `_live_task` are outside | `shutdown()` cannot cancel them. |
| D55 | There is no watchdog, heartbeat or liveness assertion in the paper path | absence | A hung agent cycle is detected only indirectly, because `max_instances=1` silently skips the next tick. |

### B.5 Test-suite defects

| # | Defect | Evidence | Consequence |
|---|---|---|---|
| D56 | `class TestFeeAccounting` is defined twice in the same module | `tests/test_lifecycle_paths.py:75` and `:209` | Three tests never run, including `test_daily_pnl_reflects_opening_fee` — the **only** assertion that the daily-loss breaker and the trade-quality metric read the same number. Invisible to `unittest discover` and to coverage. |
| D57 | `TestEdgeRendering.test_axes_have_no_scaleanchor` defined twice | `tests/test_neural_net_layout.py:240` and `:259` | Harmless (identical bodies) but reads like an incomplete edit. |
| D58 | 36 of 570 test methods (6.3 %) assert on source text, not behaviour | §7.4 | Pass if a string is present; blind to whether the surrounding code is correct. |
| D59 | `test_repaired_config_has_positive_expectancy` re-implements the production validator's arithmetic | `tests/test_config.py:86-102`; copy also at `test_decision_agent_ensemble.py:197-202` and `test_bugfixes.py:1002` | The only end-of-chain economic assertion asserts a **copy**. Stays green if production drifts. |
| D60 | `_posix_key` in `test_live_tui.py` re-implements `_read_posix` and tests the copy | `tests/test_live_tui.py:340-356` | And all four cases skip on Windows, so the copy is never executed on this platform either. |
| D61 | `TestReadability._luminance` uses YIQ coefficients, not WCAG relative luminance | `tests/test_dashboard_palette.py:93-98` | Contrast assertions run on a metric the product does not use. |
| D62 | `test_flat_trade_loses_exactly_two_fees` asserts the magic absolute `-9.0` | `tests/test_lifecycle_paths.py:267-269` | Bakes in `quantity=0.2`, `price=50000.0` and taker `0.00045`. Violates the suite's own stated convention. |
| D63 | `test_live_safety.py` sets `counters.realized_pnl` directly to test `DAILY_LOSS_LIMIT` | `tests/test_live_safety.py:286-290` | The test passes while the limit can never fire in production (see D3). |
| D64 | The `ExecutionAgent`↔`LiveExecutor` interface is never tested | `tests/test_live_executor.py:200-226` | The suite is structurally blind to D1. |
| D65 | `db_module._db` and `core.config` are mutated and not fully restored | §7.3 | Running one file in isolation differs from running it after others. No isolation layer. |
| D66 | `tests/t3.py` is a live mainnet WebSocket probe living inside `tests/` | `tests/t3.py:17-23` | No assertions, no `__main__` guard, skipped by discovery only on filename. |

### B.6 Tooling and repository hygiene

| # | Defect | Evidence | Consequence |
|---|---|---|---|
| D67 | `data_store/layered.py` mutates the **live** `hud_figures` module object at import time | `data_store/layered.py:103` | Importing anything from `data_store/` silently changes production dashboard geometry. Nothing in the running bot imports it, so this is a trap for the next person who does. |
| D68 | `docs/context/CONTEXT.md` §C.5 originally claimed its own input artifact `_digest.md` was absent | corrected in this revision | A reader was told a 355 KB file sitting in the same directory did not exist. Self-referential provenance errors are the hardest kind to catch downstream. |
| D69 | Root `_*.txt` and `_*.log` dumps (18 logs ≈ 724 KB plus signature/import extractions) are committed shell-redirect artifacts | `.gitignore:6-10` states no code reads them | Noise in the tree; the import-graph DAG in §1.4 cannot be re-derived from them because they are untracked leftovers of a past analysis run. |
| D70 | ~195 MB of `.db` backups and an 80 MB `trading_bot.db.backup-20260928-142831` sit inside the project | `data_store/backups/` (4 files, 20260925 timestamps) | The repository's largest artifacts are unreviewable binary state; anyone reasoning about what can be shipped or committed needs these excluded. |

---

## Appendix C — Verification status

### C.1 Verified against source during this integration pass

Every claim below was re-measured by opening the file, not carried over from a prior section.

| Claim | How verified | Result |
|---|---|---|
| `run.py` is 878 lines | `wc -l run.py` | **878** confirmed |
| `config.yaml` is 214 lines with 16 top-level blocks | `wc -l`; `grep -cE "^[a-z_]+:"` | **214, 16** confirmed |
| `database/db.py` has 10 tables, 8 indexes | `grep -c "CREATE TABLE"` / `"CREATE INDEX"` | **10, 8** confirmed |
| 24 `tests/test_*.py` files, 570 `def test_` occurrences | `ls` + `grep -rho` | **24, 570** confirmed |
| The suite passes | `python -m unittest discover tests` | **Ran 566 tests in 8.284s, OK (skipped=4), exit 0** |
| The 570-vs-566 gap is 4 shadowed test methods | §7.5.5 analysis of the double-defined `TestFeeAccounting` and `TestEdgeRendering.test_axes_have_no_scaleanchor` | **Explained** |
| Interpreter is CPython 3.14.6 | `python --version` | confirmed |
| `requirements.txt` declares 23 packages | `grep -vE "^\s*#\|^\s*$" \| wc -l` | **23** confirmed (one prior section said 25) |
| `insert_direction_snapshot` has exactly one production call site | repo-wide grep | **1** — `agents/direction_agents.py:445` |
| `record_realized_pnl` has zero production callers | repo-wide grep | **0** — only `safety.py:415` (def) and `tests/test_live_safety.py:361, 390` |
| `emergency_flat` has no caller in `run.py` | repo-wide grep | confirmed — only `engine.py:655` (def) and a `client.py:469` docstring |
| `engage_kill_switch` has 5 call sites outside its definition | repo-wide grep | **5** — `engine.py:128, 404, 555, 599, 737` (`safety.py:401-408` inlines the latch instead of calling the helper, giving 6 distinct raiser paths) |
| `LiveExecutor` implements 2 of the 6 members `ExecutionAgent` calls | AST/grep of `executor.py` vs `execution_agent.py` | **2 of 6** confirmed: `__init__:53`, `update_price:56`, `execute_order:66`, `_open:77`, `_close:127`, `_price_for:171` |
| `trading/live/` has no `__init__.py` | `ls trading/live/` | confirmed — PEP 420 namespace package |
| `_apply_dict` is `hasattr`+`setattr` with no `else` and no type check | read `core/config.py:407-412` | confirmed verbatim |
| `config.live.enabled = False` is forced unconditionally | read `core/config.py:480-482` | confirmed verbatim |
| The `database` prune filter drops `agent_log_*` keys | read `core/config.py:508-511` | confirmed verbatim: `if k.startswith("snapshot_")` |
| Risk/fee/interval dataclass defaults vs `config.yaml` values | read `core/config.py:18-31, 36-45, 69-74` and `config.yaml:36-73` | **all 13 divergences confirmed** |
| `PRICE_QUANT`/`MONEY_QUANT`/`QTY_QUANT` and `ROUND_DOWN` | read `risk_manager.py:16-18` | confirmed verbatim |
| `validate_trade` has 4 limits, frozen daily-loss denominator | read `risk_manager.py:273-338` | confirmed verbatim, `reference = self._initial_balance if ... > 0 else balance` at `:319` |
| The 0.3 s execution loop body | read `run.py:587-598` | confirmed verbatim, including the blanket `except` |
| `_build_live_executor` ordering and the bare `LiveConfig()` | read `run.py:238-295` | confirmed verbatim |
| `LiveExecutor._open` refuses missing SL/TP and sends `size=order.quantity or 0.0` | read `executor.py:77-110` | confirmed verbatim |
| The 9 master blockers in order | read `safety.py:258-298` | confirmed verbatim, including the inverted kill switch |
| `engage_kill_switch` never clears | read `safety.py:384-388` | confirmed — `if not self.engaged: … self.engaged = True` |
| Both `dcc.Interval` values (500 / 60000) | read `dashboard/app.py:52-67` | confirmed verbatim |
| `register_callbacks` binds 19 callbacks | `grep -c "@app.callback"` | **19** confirmed |
| All 10 `Channels` constants and their names | read `core/event_bus.py:92-104` | confirmed; 4 of 10 have no traffic |
| The free/wallet/equity identity | read `paper_engine.py:844-860` and `position_manager.py:440-455` | confirmed verbatim at both sites |
| `trading_bot.db` + `-wal` + `-shm` + a 20260928 backup exist | `ls data_store/` | confirmed |
| `ml/models/signal_model.pkl` is 3,903,681 bytes | `ls -la ml/models/` | confirmed |
| 18 `_*.log` files, 724 KB total | `ls _*.log \| wc -l`; `du -ch` | confirmed |
| `data_store/` contains 32 Python files (8,759 lines) and 5 frozen `hud_figures` copies | `find` + `wc -l` | confirmed |
| The repo is not a git repository | environment flag `Is a git repository: false`; no `.git` directory | confirmed |

### C.2 Conflicts between the section files, and how each was resolved

The seven sections were written in parallel and disagreed on eight points. Every one was resolved by opening the source, not by averaging.

| # | Conflict | Sections in conflict | Resolution |
|---|---|---|---|
| C1 | **`config.yaml` block count: 8 vs 12 vs 16** | §1 said 8, §4 said 12, §4 body said 16 | **16** is correct. `grep -cE "^[a-z_]+:" config.yaml` → 16, and the sixteen block names are enumerated in §4.2. §1's "8 top-level blocks" and §4's diagram label "12" were both wrong. |
| C2 | **`config.yaml` line count: 214 vs 215** | §1 said 214, §4 diagram said 215 | **214**, per `wc -l`. |
| C3 | **Test count: 570 vs 566, and 24 vs 26 files** | §1 said 570 test functions / 24 files; §7 and §8 said 566 cases / 26 files | **Both are right about different things.** 24 files match `test_*.py`; the `tests/` directory holds 26 `.py` files including `t3.py` (not a test) and `__init__.py`. **566 is the authoritative case count** — measured by running the suite. The 570 `def test_` regex count includes 4 shadowed methods that never execute (§7.5.5). §1's "570 collected test cases" phrasing was wrong; §7's "570 in §7.4" is an AST method count used for the source-text percentage, which is legitimate in that context. |
| C4 | **`analysis/backtester.py` size: 901 vs 980 lines** | §1 said "901+ lines", §4 said 901, §7 said 881 (that was `test_advanced_modules.py`) | **980** per `wc -l`. §1's "901" was stale. |
| C5 | **`requirements.txt`: 25 entries vs 23** | §8 said 25; the file itself says otherwise | **23** per `grep -vE "^\s*#\|^\s*$" \| wc -l`. §8's "25" counted comment/blank lines or was stale. |
| C6 | **`_apply_dict` / `load_config` line numbers: 408-412 vs 407-412; 415 vs 419-421** | §4 gave both ranges in different places | **407-412** for `_apply_dict`, **414** for `def load_config`, **419-421** for the missing-file early return. Confirmed by reading `core/config.py`. |
| C7 | **`LiveConfig` force-false location: `config.py:480-482` vs `:482`** | §3 said `safety.py:266,270,277` for the env gates; §4 said `config.py:482` | Both correct in context: the force-write is the statement at `core/config.py:482`; the guard clause that precedes it is the `if "live" in raw:` block at `:480-481`. No conflict, just different granularity. |
| C8 | **`SafetyGate.engaged` definition line: 159 vs 177** | §3 said `safety.py:159`, §4/§5 said `safety.py:177` | **177** is correct: `self.engaged = False`. §3's 159 pointed at the surrounding class/decl block. |
| C9 | **Live kill-switch raiser count: 5 vs 6** | §4 said 5, §5/§8 said 6 | **6** is the honest count: `safety.py:401-408` inlines the latch inside `record_error()` rather than calling `engage_kill_switch`, plus `engine.py:128, 404, 555, 599, 737`. The discrepancy is "callers of `engage_kill_switch`" (4) vs "distinct paths that set `engaged = True`" (6). §5.4 now states both numbers explicitly. |
| C10 | **`_build_live_executor` order: gate before exchange before engine** | §3, §5, §8 all agreed; §4 said `LiveConfig()` "ignores `get_config().live`" | No conflict. Verified verbatim at `run.py:238-295`. |

### C.3 What could NOT be verified

Stated explicitly rather than guessed.

- **Runtime behaviour of the bot was not observed.** `python run.py` was never started in any mode. No order was placed, no log was read to confirm a claim. The 0.3 s tick, the 1.5 s price loop, the 5 s ensemble interval, the 25 scheduler jobs' actual firing — all are **code constants, not measurements**.
- **The live path has never been observed running**, and could not be: `TRADEBOT_LIVE` and `TRADEBOT_LIVE_CONFIRMED` are set only by tests, and even with them set the path raises `AttributeError` every tick. The unit tests use `FakeExchange` and injected env.
- **No network call was made.** No Hyperliquid REST or WS request, no FRED fetch, no CryptoPanic call, no yfinance/ccxt request. Feed behaviour, symbol discovery, the 24 h volume scan and the reconnect backoff are read from source only.
- **The C++/Python parity was not re-measured in this pass.** The prior section's measurement (max |Python − C++| = 0.0 over 300 randomized books plus a production-shaped book) is carried forward and is consistent with the suite passing its 23 parity cases on this machine, but the 300-book sweep itself was not repeated here.
- **`cpp_microstructure`'s destructive behaviours** — `noexcept` process aborts, 32-level truncation, NaN propagation, `ingest_json` error taxonomy, GIL-release ordering — were not re-executed here. The `noexcept`/abort claim rests on a prior run plus the source at `cpp/bindings.cpp:32-43`.
- **The `-O3` vs `-O2` precedence claim** is read off `build/build.ninja:56` and `CMakeLists.txt:109-114`. No disassembly was performed.
- **The prebuilt `.pyd` is newer than every source file** (mtime comparison only). `ninja -n` reporting "no work to do" was not re-run here, though the mtimes in §6.4 are consistent with it.
- **The `HL_JSON_USE_SIMDJSON=ON` build was not attempted.** As written, the option only adds an include path and a `-D` define that no source file `#ifdef`s, so the upstream path is *expected* not to compile — unconfirmed.
- **Linux/macOS build untested.** `CMakeLists.txt` has an `if(MSVC)` branch that has never been exercised; the static-link block is inside the `else` (MinGW) branch only.
- **Performance of the native kernel is unmeasured.** No benchmark of C++ vs Python throughput exists anywhere in the repo. The entire native path is justified on speed grounds and nothing in-tree substantiates it.
- **`_t.py`'s summary line format** is read from the source, not from a run of that script. (The suite itself *was* run directly, so its result is first-hand.)
- **The 20-of-62 "no test import" figure** is carried forward from a prior AST-import scan; it was not re-run here.
- **The 36-of-570 source-text-test figure** comes from a heuristic (a test body both reads a file and makes a string assertion). It was not re-derived here.
- **Dashboard geometry claims** — the md5 divergence between `hud_figures.py` and its snapshots, the four surviving `rgba()` literals outside `:root`, the `--h-*` token values in `style.css:78-94` — are carried forward from the prior dashboard and test passes and were not re-measured.
- **`_t.py`, `_econ.py`, `_econ3.py`, `_smoke_console.py`, `_fixcjk.py`, `_audit_refs.py`** were not executed. Their exit codes and output paths are read from source.
- **`data_store/` was not surveyed for currency.** 32 Python files of measurement tooling exist; whether any of it is still meaningful is not established. §A.11 reports sizes and names only.
- **`PROJECT_CONTEXT.md` and `PLANNING.md`, referenced three times in `README.md`, are absent** from the working tree. Whether they were deleted, never committed, or live on another branch is not determinable from what is here.
- **No environment variables are set on this machine for this bot.** In particular `TRADEBOT_LIVE` and `TRADEBOT_LIVE_CONFIRMED` are unset.
- **Windows signal handling** is read from the `except NotImplementedError` at `run.py:830-835` and the comment above it, not from an observed Ctrl+C.

### C.4 Open questions raised by the analysis

Unresolved questions, each with what would settle it.

| # | Question | Why it is open | What would settle it |
|---|---|---|---|
| Q1 | **Is the paper strategy actually profitable?** | The fill model has no slippage, spread, depth or impact, and no funding cost is accrued. Paper P&L is therefore structurally optimistic in a way no amount of post-hoc analysis can correct. | Re-run the paper session with a realistic fill model and a funding line, or evaluate `_econ.py` output against a slippage-adjusted model. |
| Q2 | **What is the real drawdown, given that `account.max_drawdown` is never written and `peak_balance` is monotonic across sessions?** | The DB cannot answer it. P13/D26. | Query `balance_history` and recompute the equity curve externally. |
| Q3 | **Why does `RiskManager.set_initial_balance` exist on an un-injected instance (`execution_agent.py:40`) that nothing uses?** | G18/D22. It looks like an unwired global daily-loss breaker (`_global_loss_streak` is the other candidate), but neither is wired. | Ask the author, or grep for the intended caller. |
| Q4 | **Was `live.max_daily_loss = 50.0` ever intended to be enforced?** | D3/L6. The check exists, the config field exists, the counter is persisted — only the `record_*` call is missing. It reads like a one-line omission rather than a deliberate removal. | Ask the author. If unintended, the fix is one call site. |
| Q5 | **Is the `0.45` drift coefficient a placeholder or a derived value?** | D36. It has no derivation in the file, no config key, no comment, and the module docstring describes a different formula. It alone sets the diffusion curve's steepness, which is displayed on the HUD as a forecast-looking path. | Ask the author, or find the notebook that produced it. |
| Q6 | **Is the 32-level C++ truncation reachable against real Hyperliquid data?** | D40. The overflow flags exist but are never bound to Python, so there is no way to tell from Python whether it ever fired. | Bind the flags, or check the venue's documented max depth (currently 20). |
| Q7 | **Does the ABI-tagged `.pyd` block deployment on any other interpreter, and is that intended?** | §6.4, §8.9. Both parity suites skip silently on a version mismatch, so a green suite on 3.12 would prove nothing. | Rebuild against a stable ABI (`Development.Module` + `Py_LIMITED_API`) or add a CI job that fails when the native module is unimportable. |
| Q8 | **What is the intended source of truth for the risk profile: `config.yaml` or the dataclass defaults?** | §4.4. The two disagree on 13 keys including every risk limit. A wrong CWD silently selects the looser half of `max_open_positions` and the stricter half of `taker`, and nothing reports it. | Ask the author. If YAML is canonical, `load_config` should fail loudly when the file is missing. |
| Q9 | **Are the five `data_store/_snap*` copies of `hud_figures.py` dead, or is one of them the intended reference?** | §8.9. `_snap_cp/model.py` still calibrates against a snapshot that no longer matches production, and `data_store/layered.py:103` mutates the live module at import. | Ask the author. If dead, delete the directories and the calibration model together. |
| Q10 | **Was `PROJECT_CONTEXT.md` / `PLANNING.md` deleted, never committed, or on another branch?** | Referenced three times in `README.md`; absent from the tree. | Check the remote / other branches. |
| Q11 | **Should the dashboard read `direction_snapshots` in live mode at all?** | §3.3.3. In live mode it renders a paper database that no live order ever touched, which is actively misleading. | Either write live fills to SQLite or gate the HUD panels on mode. |
| Q12 | **What was the intended purpose of the two shadowed `TestFeeAccounting` classes?** | D56. `test_daily_pnl_reflects_opening_fee` is the only assertion that the daily-loss breaker and the trade-quality metric read the same number, and it silently does not run. | Ask the author; the fix is renaming one class. |
| Q13 | **Why is `praw` declared and never imported?** | §8.10. `data/news_fetcher.py` uses RSS and CryptoPanic only. | Remove the dependency, or find the intended Reddit source. |
| Q14 | **Is the paper engine's accounting meant to run in live mode?** | §2.3. `run.py:583` and `:623` schedule the paper engine's balance snapshot and volatility refresh unconditionally, outside any mode branch. | Gate on mode, or document that the equity curve is paper-only in both modes. |

### C.5 Provenance of this document

Seven section files were merged: `01-overview-overview.md`, `03-dataflow-dataflow.md`, `04-config-config.md`, `05-risk-risk.md`, `06-native-numerics-native.md`, `07-tests-tests.md`, `08-recipes-recipes.md`. **`02-*.md` was never written to disk** — the single writer for it stalled through all six attempts — so the integrator reconstructed the architecture and process-topology material as §2 from source rather than merging it.

**§9 Subsystem reference was written in a second pass**, after §2 was already numbered and cross-referenced by the rest of this document, so it was numbered 9 rather than 2 to keep every existing `§n.m` citation valid. It was produced by four per-group writers (signal/agents, execution/risk, data/persistence, UI/infra), stitched into one section, then checked by three adversarial auditors sampling the symbol tables against source: **5 wrong signatures, 27 wrong line locations and 8 missing files** were found and corrected, with 3 auditor findings rejected on verification. The `__init__.py` package markers were added by that correction pass. Its two closing tables are derived from an AST walk of the repository, not from the per-file prose.

The raw analysis backing this document is preserved alongside it as **`docs/context/_digest.md`** (3,876 lines / 355 KB) — the seven raw section files, consolidated verbatim. A third artifact, `_connections.md`, was planned for the cross-cutting traces but was never persisted; those traces were folded into the sections themselves. The intermediate section files have been deleted, leaving `CONTEXT.md` and `_digest.md` in `docs/context/`.

**Verification passes that corrected this document.** After the first merge, a completeness critic audited it and its corrections were applied after re-verifying each figure against source: the first-party line total (15,566 → **19,688**), the test-suite line total (7,132 → **9,130**), the `data_store/` inventory (34 files → **32 files / 8,759 lines**), the dataclass count (17 → **16**), the kill-switch call-site count (4 → **5**), one `risk_manager.py` line number (`:118` → **`:119`**), the `config.yaml` leaf count (96 → **110** of 121 keys), and the provenance paragraph above, which had described its own 355 KB input artifact as absent. Four further defects (D67–D70) were added to Appendix B as a result.

The test suite was executed once during integration (`Ran 566 tests, OK (skipped=4), exit 0`). No other code in the repository was run.

