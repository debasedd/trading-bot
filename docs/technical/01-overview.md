# 01 — Overview and Architecture

Scope: `C:/Users/maman/Desktop/Project/trading-bot`. Python 3.14 (CPython 3.14 ABI), a single
asyncio event loop, a C++20 pybind11 hot-path kernel, APScheduler, Dash/Plotly, Windows 11.

Every number below was re-derived from source in this working tree. Where the previous
`docs/context/CONTEXT.md` is wrong, it is corrected explicitly in
§10 "What changed recently" and inline.

---

## 1. What this system is

An autonomous multi-agent crypto-derivatives scalping bot. It reads public Hyperliquid
market data over WebSocket, runs four cooperating agents plus a direction ensemble, and
turns their combined verdicts into leveraged perpetual-futures positions. It can run in
three modes, and only one of them touches an exchange:

| Mode | Exchange contact | Selected by |
|---|---|---|
| `paper` | none — simulated fills, SQLite ledger only | `python run.py` default, `--paper`, `--non-interactive`, or menu option 1 |
| `testnet` | real orders, Hyperliquid testnet funds | `--testnet`, or menu option 2 |
| `mainnet` | real orders, real USDC | `--live`, or menu option 3 (two deliberate human acts required) |

The mode decision happens **before any subsystem is constructed**: `run.py:941`
`decision = _choose_mode()` precedes `TradingBotApp(decision)` at `run.py:942` and every
`await app.initialize()` below it. Constructing the app first changes which engine gets
built and therefore which engine can be reached.

Configured trading profile (from `config.yaml`, which is the file actually loaded — the
dataclass defaults in `core/config.py:100-217` differ and are more conservative on risk but
20x slower on decision cadence; see §10.4):

| Parameter | Value | Source |
|---|---|---|
| Stop loss | `tight_sl_pct = 0.0025` (0.25%) | `config.yaml:140` |
| Take profit | `fast_tp_pct = 0.0060` (0.60%) | `config.yaml:139` |
| Risk per trade | `max_risk_per_trade = 0.005` (0.5%) | `config.yaml:37` |
| Default / max leverage | `10` / `50` | `config.yaml:42`, `config.yaml:38` |
| Daily loss circuit breaker | `max_daily_loss = 0.10` of **initial** balance | `config.yaml:39` |
| Max open positions | `30` | `config.yaml:41` |
| Taker fee | `0.00045` (Hyperliquid base tier) | `config.yaml:60` |
| Maker fee | `0.00015` | `config.yaml:59` |
| Decision tick | `0.3 s` | `run.py:81` `_EXEC_TICK_SECONDS` |
| Direction ensemble interval | `5 s` | `config.yaml:192` |
| US market open | 09:30–16:00 `US/Eastern`, Mon–Fri | `config.yaml:76-80` |

---

## 2. Layered architecture

Six layers plus two off-axis packages. The first-party import graph is a DAG — no cycles
among production modules. Arrows point "imports".

```
  L6  run.py                                        composition root; the ONLY place the
                                                    4 agents, both engines, the scheduler,
                                                    the 5 asyncio loops and the Dash
                                                    thread are wired together

  L5  dashboard/  (13 files, 3 781 lines)          read-only visual surface
        app.py (148)  callbacks/update_callbacks.py (1 429)  layouts/hud.py (440)
        layouts/hud_figures.py (972)  layouts/palette.py (139)  assets/style.css (1 253)
        layouts/performance.py (160)  layouts/price_chart.py (209)  layouts/news_feed.py (117)
        layouts/positions.py (95)  layouts/agent_logs.py (69)
        |
        +--> core.{config,logger,market_store}
        +--> analysis.{probability_engine,technical}          <-- L5 reaches DOWN to L2

  L4  trading/  (12 files, 6 479 lines)            the money
        paper_engine.py (1 113)  position_manager.py (577)  risk_manager.py (429)
        fill_cost.py (216)  models.py (86)
        live/executor.py (1 061)  live/engine.py (757)  live/client.py (493)
        live/console.py (751)  live/tui.py (572)  live/safety.py (423)
        |
        +--> core.{config,event_bus,logger,market_store}
        +--> database.{db,models,repository}
        +--> analysis.volatility                             <-- L4 reaches DOWN to L2
                                                             (2 lazy imports, paper_engine only)

  L3  agents/  (7 files, 2 007 lines)               decision makers
        decision_agent.py (549)  direction_agents.py (525)  analysis_agent.py (318)
        execution_agent.py (318)  base_agent.py (151)  news_agent.py (145)
        |
        +--> core.*  +--> database.*  +--> data.*   +--> analysis.*  +--> trading.{models,risk_manager}

  L2  analysis/  (9 files, 2 909 lines)             pure signal mathematics
        backtester.py (980)*  probability_engine.py (433)  volatility.py (412)
        technical.py (314)  ml_signals.py (257)  direction_ensemble.py (227)
        fundamental.py (153)  vol_target.py (132)*
        |
        +--> core.{config,logger,market_store,microstructure}
        (* backtester.py and vol_target.py have ZERO production importers)

  L1  data/ (6 files, 1 864 lines) + database/ (4 files, 993 lines)
        data:      price_feed.py (705)  hyperliquid_feed.py (573)  macro_fetcher.py (218)
                   sentiment.py (213)  news_fetcher.py (154)
        database:  repository.py (604)  db.py (258)  models.py (130)
        |   (same-layer peer edge: data.price_feed -> database.{db,repository,models})
        +--> core.*

  L0  core/  (8 files, 2 341 lines)                  foundation
        config.py (1 030)  logger.py (403)  microstructure.py (369)  market_store.py (265)
        scheduler.py (139)  event_bus.py (103)  utils.py (31)
        +--> core.config   (the true root: core/logger.py:11 imports it)

  OFF-AXIS
  L7  ml/  (3 files, 278 lines)   trainer.py (233, imports core.logger ONLY, offline,
                                   `python -m ml.trainer`)   predictor.py (44, DEAD — zero
                                   importers, its docstring says so)
  L8  cpp/ (1 326 C++ lines + CMakeLists.txt 152)
        include/microstructure_kernel.h (172)  microstructure_kernel.cpp (643)
        bindings.cpp (164)  include/simdjson.h (347)
        reached ONLY through core/microstructure.py:254, 277, 338, 348
```

Package facts a newcomer will trip on:

- **`trading/live/` has no `__init__.py`.** Verified: `Test-Path trading\live\__init__.py`
  → `False`. It is a PEP 420 namespace package (Python 3.3+). Every other top-level
  package has one. `cpp/` also has none.
- Every `__init__.py` in the repo is a 1-line docstring with **zero re-exports**, so all
  consumers import the submodule path directly (`from analysis.volatility import x` finds
  nothing, because the code uses `from analysis import volatility as vol_mod` at
  `trading/paper_engine.py:143,366` and `agents/execution_agent.py:117`).
- `run.py` is 995 lines. `database/repository.py` has 40 methods. `database/db.py`
  contains exactly **10** `CREATE TABLE IF NOT EXISTS` and 8 `CREATE INDEX IF NOT EXISTS`
  (README claims 11 tables and lists ten names — ten is correct).

---

## 3. Process topology

One OS process. One asyncio event loop. One non-asyncio thread. Everything blocking is
handed to `asyncio.to_thread` / `run_in_executor`.

### 3.1 asyncio tasks (created on the running loop)

| Task | Created at | Cadence | What it does |
|---|---|---|---|
| `price_feed.price_update_loop()` | `run.py:879` | `sleep 1.5 s` (`price_feed.py:605`), `0.15 s` between per-symbol ticker refills | `fetch_all_mids()` → `market_store.set_price`; refills only symbols with no price or age > 10 s via `fetch_ticker` |
| `hyperliquid.websocket_loop()` | `price_feed.py:154` (from `start_streaming`, `run.py:370`) | continuous, reconnect backoff `1→2→4→…→30 s` (`hyperliquid_feed.py:548`) | the only streaming market data path |
| `hyperliquid._ping_loop(ws)` | `hyperliquid_feed.py:529` | application-level `{"method":"ping"}` every 30.0 s | `WS_PING_INTERVAL = 30.0` (`:53`) |
| `TradingBotApp._execution_loop()` | `run.py:881` | `sleep 0.3 s` OUTSIDE the try block, `run.py:715` | **the heartbeat**: `execution_agent.run_cycle()` then `execution_agent.check_positions()` (`:652`, `:654`) |
| `TradingBotApp._candle_refresh_loop()` | `run.py:883` | `sleep 30 s` (`run.py:743`) | `fetch_ohlcv` 1m limit 300 + 5m limit 120 per symbol, then `paper_engine.refresh_volatility_cache()` |
| `TradingBotApp._telemetry_loop()` | `run.py:885` | `sleep 25 s` then every 30 s | prints the terminal telemetry card from raw SQL |
| `TradingBotApp._maintenance_loop()` | `run.py:892` | `max(60, snapshot_prune_interval)` = 3600 s | `_prune_direction_snapshots()` |
| `LiveEngine.run_loop(interval=5.0, on_decision=_decide)` | `run.py:321-322` — **live mode only** | 5 s | `health_check()` + `check_pending_fills()`; `on_decision` is a placeholder returning `None` (`run.py:317-319`), so this loop can never send an order |
| `_candle_refresh_task` = `_refresh_cache()` | `paper_engine.py:166` | one-shot at boot | fills the 1m candle cache `analysis.volatility` reads |
| `loop.add_signal_handler(..., create_task(app.shutdown()))` | `run.py:949` | on SIGINT/SIGTERM | `NotImplementedError` swallowed at `:950-952`; on Windows Ctrl+C arrives as `KeyboardInterrupt` instead, handled at `run.py:957` |

Total in live mode: **6 long-lived asyncio tasks** (5 in `_background_tasks` at
`run.py:894-896`, plus `self._live_task`). Known gap: `self._live_task` is **not** in
`_background_tasks`, so `shutdown()` (`run.py:924-925`) never cancels it — `run_loop`
outlives shutdown.

### 3.2 Threads

| Thread | Created at | What it does |
|---|---|---|
| `DashBoardThread` (daemon) | `run.py:849-854` | blocking Flask/Werkzeug server via `app.run(host=cfg.host, port=cfg.port)` (`dashboard/app.py:139-144`). The only non-asyncio thread; never joined or signalled. |
| default `ThreadPoolExecutor` | implicit | `asyncio.to_thread(self._handle_message, raw)` for every WS frame (`hyperliquid_feed.py:534`); `loop.run_in_executor(None, self._load_model)` (`analysis/ml_signals.py:47`); `asyncio.to_thread(self.evaluate, symbol)` for the CPU-bound `TechnicalAgent` (`direction_agents.py:210`) |
| C++ GIL release | `cpp/bindings.cpp:63,74,113,146,155` | `py::gil_scoped_release` on OFI/depth reads and `ingest_json`; `ingest_l2` holds the GIL by design (`bindings.cpp:12-13, :84`) |

### 3.3 Scheduler (APScheduler `AsyncIOScheduler`, `core/scheduler.py:36`)

10 jobs registered from `run.py:setup_scheduler` (`run.py:520-618`) plus 1 internal job
added in `AgentScheduler.start` (`scheduler.py:121-127`).

| Job | `run.py` | Interval (config.yaml) | US-open interval |
|---|---|---|---|
| `refresh_top_volume` (conditional) | `:533-537` | `scanning.refresh_interval` = 3600 s | — |
| `news_agent` | `:540-546` | 300 s | 120 s |
| `analysis_agent` | `:549-555` | 15 s | 10 s |
| `decision_agent` | `:558-564` | 3 s | 2 s |
| `direction_ensemble` (conditional) | `:570-574` | `ensemble.interval_seconds` = 5 s | — |
| `direction_snapshot_prune` (conditional) | `:579-585` | `max(60, snapshot_prune_interval)` = 3600 s | — |
| `agent_log_prune` (unconditional) | `:591-597` | `max(60, agent_log_prune_interval)` = 1800 s (dataclass default, `core/config.py:400`; the identical value at `config.yaml:115` is dropped at load) | — |
| `macro_update` | `:600-604` | 21600 s (6 h) | — |
| `finbert_batch` | `:607-611` | 900 s (15 min) | — |
| `balance_snapshot` | `:614-618` | 60 s | — |
| `_market_check` (internal) | `scheduler.py:121` | 60 s | — |

Interval switching is a real `reschedule_job` performed by `_adjust_intervals`
(`scheduler.py:100-116`), driven by `is_us_market_open()` (`scheduler.py:19-33`), which
uses `us_market.timezone = "US/Eastern"` and rejects weekends.

Note: `balance_snapshot` is bound unconditionally to
`self.paper_engine.position_manager.take_balance_snapshot` (`run.py:616`) with no mode
branch, so in live mode the equity series and therefore MDD/Sharpe are computed from the
untouched paper ledger.

### 3.4 Event bus (`core/event_bus.py:26`)

10 channels (`:92-103`): `price_update`, `news_sentiment`, `market_analysis`,
`trade_decision`, `trade_executed`, `position_update`, `balance_update`, `agent_log`,
`system_event`, `direction_ensemble`. Bounded queues (`maxsize=1000`, `:47`); on
`QueueFull` the oldest event is dropped and the newest inserted (`:75-84`).

Publishers and subscribers that matter:

| Channel | Publishers | Subscribers |
|---|---|---|
| `price_update` | `PriceFeed.fetch_ticker` (`source="price_feed"`) | `ExecutionAgent` (`execution_agent.py:49`) |
| `market_analysis` | `AnalysisAgent.act` | `DecisionAgent` (`:65`) |
| `news_sentiment` | `NewsAgent` | `AnalysisAgent` (`analysis_agent.py:60`) |
| `position_update` | `PositionManager` (`:349`), `LiveExecutor` (`executor.py:609`, `:853`) | `DecisionAgent` (`:66`) — keys its cooldown off the literal string `"CLOSED"` |
| `trade_decision` | `DecisionAgent` (`:439-448`) | `ExecutionAgent` (`:48`) |
| `trade_executed` | `LiveExecutor` (`executor.py:622`) | nobody in production |

The Hyperliquid WS path publishes nothing. `HyperliquidFeed.__init__(event_bus=None)`
stores the bus (`hyperliquid_feed.py:84-85`) and never uses it; frames go to
`market_store` and the kernel only.

---

## 4. Bot lifecycle

```
python run.py
  │
  ├─ print_banner()                                    run.py:124   (UTF-8-safe via _print_safe :97)
  ├─ setup_logger()                                    run.py:937   (rotating file + ANSI console)
  │                                                    core/logger.py:155
  ├─ decision = _choose_mode()                         run.py:941   <-- BEFORE anything else
  │      --paper / --non-interactive → paper           run.py:202-204
  │      --testnet / --live           → that mode      run.py:209-222
  │      else ask_mode(cfg)           → TUI menu       run.py:227 → console.ask_mode:405
  │           → decide_mode (pure, no I/O)            console.py:78
  │             requires ALL of: key in env, exact phrase "SAYA MENGERTI"
  │             (CONFIRM_PHRASE, console.py:38), retyped wallet address
  │      KeyboardInterrupt / ImportError → paper       run.py:228-233
  │
  ├─ app = TradingBotApp(decision)                     run.py:942 → __init__ :239
  │      constructs: EventBus, AgentScheduler, PriceFeed, SentimentAnalyzer,
  │                   MacroFetcher, PaperTradingEngine
  │
  └─ await app.initialize()                            run.py:330
         1. init_db()                          :336    aiosqlite singleton, WAL, 10-table DDL
         2. paper_engine.initialize()          :341    init_account(10000), inject initial_balance
                                                   into RiskManager, register candle source
         3. price_feed.initialize()             :346    Hyperliquid universe + ccxt binance
         3b. discover_top_volume_symbols(10)   :357    OVERWRITES config.symbols if it succeeds
         3c. price_feed.start_streaming()      :370    opens the WS
         4. sentiment_analyzer.initialize()    :375    VADER always; FinBERT best-effort
         5. NewsAgent / AnalysisAgent / DecisionAgent   :380 / :382 / :389
         5b executor = paper_engine             :396
             if mode in (testnet, mainnet):      :397
                 executor = _build_live_executor()      :398 → :267
                     SafetyGate(live_cfg)              :302   (live_cfg = bare LiveConfig(), :291)
                     LiveExchange(key, testnet)        :303
                     LiveEngine(gate, exchange, cfg)   :305
                     await engine.health_check()       :307   RuntimeError if not ok, loop never starts
                     create_task(run_loop(5.0, _decide))  :321
                     return LiveExecutor(engine, event_bus) :328
             self.executor = executor            :399
             ExecutionAgent(event_bus, executor) :401
         5c DirectionEnsembleAgent (if ensemble.enabled)  :407-413
         5d analysis/decision/execution .initialize()      :418-420
         6  _prefetch_historical_candles()       :426    1m×240, 5m×120, 1h×120 per symbol
         7  analysis_agent.update_macro()       :430
         └─ print_startup_summary(...)          :434

  └─ await app.run()                                  run.py:857
         setup_scheduler() + scheduler.start()   :863-865
         start_dashboard()  → DashBoardThread   :871
         5 asyncio tasks created                 :879-892
         end_quiet_mode()                        :901   (restores full console logging)
         while self._running: await sleep(1)     :908-909

  Ctrl+C
     ├─ signal handler (POSIX)  → create_task(app.shutdown())   run.py:949
     └─ Windows: KeyboardInterrupt propagates out of main()     run.py:957
           → finally: await app.shutdown()                     run.py:960
                _running = False, scheduler.shutdown(wait=False),
                price_feed.stop(), cancel the 5 background tasks,
                await price_feed.close(), await close_db()
```

Startup order is enforced, not incidental: `setup_scheduler` dereferences
`self.news_agent.run_cycle`, `self.analysis_agent.run_finbert_batch` and
`self.paper_engine.position_manager.take_balance_snapshot`, all created in `initialize`.

---

## 5. Running it

All commands verified against `run.py`'s actual argument handling. `sys.argv` is scanned
for `--paper` / `--non-interactive` (`run.py:202`), `--testnet` / `--live` (`run.py:209`),
and `--repair-candles` (`run.py:967`). There is no argparse; unknown arguments are
silently ignored.

```bash
# From the repo root. Working directory matters — see §10.4.
pip install -r requirements.txt

# Paper (simulation) — no exchange orders
python run.py --paper
python run.py --non-interactive        # identical, run.py:202

# Interactive mode picker (default when no flag is given)
python run.py

# Testnet / mainnet, bypassing the menu
python run.py --testnet
python run.py --live                   # prints a real-money warning, run.py:212-219

# Live preflight — READ ONLY, cannot place/cancel/attach anything
python live_doctor.py --testnet
python live_doctor.py --testnet --expect 0xabc...

# Candle repair: deletes OHLC-invalid rows, refetches, exits. No agents, no dashboard,
# no mode menu, never transacts. Destructive and unprompted.
python run.py --repair-candles         # run.py:967-975

# Paper DB reset (does NOT reset sharpe_ratio — reset_paper_db.py:17-29)
python reset_paper_db.py

# Retrain the ML artifact (offline; overwrites ml/models/signal_model.pkl)
python -m ml.trainer

# Tests — 569 collected: 547 pass, 4 skip, 18 fail (16 in test_lifecycle_paths.py)
python -m unittest discover tests     # same 16, plus a shared-DB flake; NOT green
python -m pytest -q                    # see §11

# Build the native kernel (output .pyd lands in the repo ROOT, not build/)
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release
```

Dashboard: `http://127.0.0.1:8050` (`DashboardConfig`, `core/config.py:71-75`). The
`PAPER TRADING` badge at `dashboard/layouts/hud.py:68` is a **hardcoded literal** and
lies in live mode; nothing under `dashboard/` reads `run.py`'s `self.mode`.

Environment variables that matter:

| Variable | Effect | Source |
|---|---|---|
| `HYPERLIQUID_PRIVATE_KEY` | the only credential source; read once and memoized, never written anywhere | `safety.py:200-211`, `run.py:292` |
| `HYPERLIQUID_ACCOUNT_ADDRESS` | API wallet — the address that actually holds positions | `run.py:298-299`, `client.py:128-133` |
| `TRADEBOT_LIVE` | must be `1`/`true`/`yes` for `Blocker.LIVE_DISABLED` to clear | `safety.py:266` |
| `TRADEBOT_LIVE_CONFIRMED` | second, independent lock; a private key alone is never sufficient | `safety.py:271-274` |
| `TRADEBOT_LIVE_KILL_SWITCH` | **inverted**: any value outside `("", "0", "false", "no")` TRIPS the switch; unset does not | `safety.py:277-280` |
| `TRADEBOT_KERNEL` | `python` forces `PythonKernel`; `cpp` makes import failure a hard `RuntimeError` | `core/microstructure.py:329-345` |
| `TRADEBOT_BANNER` | `1`/`true`/`yes` re-enables the Flask banner | `dashboard/app.py:112` |

Nothing in the repository sets `TRADEBOT_LIVE` or `TRADEBOT_LIVE_CONFIRMED` except tests
and `live_doctor.py` (which deliberately pops them). There is no `.bat`/`.ps1`/`.sh`
wrapper. The live stack is therefore fail-closed as shipped: `master_blockers` always
returns `LIVE_DISABLED` + `NOT_CONFIRMED` on a fresh checkout.

`run.py:291` builds a **bare** `LiveConfig()`, so the limits the gate enforces are the
dataclass defaults at `core/config.py:335-343` — not `config.yaml`, and not anything
`console.edit_rules` mutated in the menu.

---

## 6. The two execution paths, and where they diverge

`run.py:396-399` is the only divergence point. Both engines satisfy the same interface, so
`ExecutionAgent` is mode-agnostic. They rejoin only in the shared SQLite tables and the
shared `position_update` bus channel — **there is no `mode` or `venue` column anywhere in
`SCHEMA_SQL`**, and the only live marker is a `"LIVE "` prefix written into
`positions.reasoning` (`executor.py:550`), which no dashboard callback reads.

```
             DecisionAgent ── publish(TRADE_DECISION, order) ──┐
                                                                 ▼
                                                    ExecutionAgent.run_cycle()
                                                                 │
                                                       engine.execute_order(order)
                                                                 │
                        run.py:396-399  executor = paper_engine
                        run.py:397      if mode in (testnet, mainnet): executor = LiveExecutor
                                                                 │
        ┌────────────────────────────────────────────────────────────────────────┤
        │ PAPER                                                                  │ LIVE
        ▼                                                                         ▼
  paper_engine.execute_order :212                                    LiveExecutor.execute_order :397
    _execute_open :443                                                 _open :408
      input guard qty>0 lev>0 px>0          :468-496                    missing SL/TP refuse :415-422
      _resolve_tp_sl :520                                               _finite_positive(qty) refuse :438
        vol.get_dynamic_tp_sl_thresholds :372                           leverage int>=1 refuse :451
        vol.assess_volatility_gate :396                                 LiveEngine.submit_order :469
          → VolatilityGateError :399, caught :523
      _fill_price :562 → fill_cost.fill_price_after_cost                 gate.can_send :236   ◀══ THE GATE ══
        half  = max(observed, 3 bps)   fill_cost.py:152                  missing_tpsl refuse :255
        impact = 1 bps (constant)      :157                              set_leverage :283  (BEFORE the order)
        total  = min(half+imp, 50 bps) :159                              place_limit_order :296
        fill   = ref*(1+total) BUY / ref*(1-total) SELL :162-163           filled_size<=0 → no protection :326
      SL/TP re-derived from the FILL  :566-567  (paper-only)             _attach_protection :338
      _tick_quality_guard :587                                           is_close_buy = side=="SHORT" :378
      validate_trade(...) :665                                           SL attach fail → kill switch :404
      position_manager.open_position :698                                back in executor:
        fee debited :102, insert_position :124,                           _persist_open :505
        insert_trade(OPEN) :142                                            uses EXCHANGE qty/avg_price :523
                                                                             untrusted fill → write nothing :526
        ── 5 close paths, all converge on position_manager.py:167 ──        DB failure → kill switch :573/:587
        (1) SL hit      :431-433                                           publish POSITION_UPDATE :609
        (2) TP hit      :437-439                                           publish TRADE_EXECUTED  :622
        (3) liquidation :425-427 → _liquidate :460
              close_fill_price :472, fee :479, realized = -margin - fee :480
        (4) scalp TP     execution_agent.py:310-312
        (5) expired      execution_agent.py:262-271
        (6) order CLOSE single  paper_engine.py:841-843
        (7) order CLOSE batch   paper_engine.py:929-944
                                                                 ── live close paths, converge on _record_close:758 ──
                                                                 (A) order CLOSE  _close :655
                                                                 (B) agent scalp-TP / expired
                                                                     _LivePositionManager.close_position :935
                                                                     size from the EXCHANGE :970-989
                                                                     exchange > ledger×1.01 → DO NOT close :1002
                                                                 (C) emergency_flat  engine.py:655
                                                                     cancel_all first :685, then reduce_only closes :702
                                                                     writes NOTHING to SQLite
```

Divergences that matter when reading numbers:

| Behaviour | Paper | Live |
|---|---|---|
| `trading/fill_cost.py` crossing cost | charged on every open AND every close | **not called anywhere in `trading/live/`** — live books the exchange's real `avg_price` (correct, but means paper and live P&L columns are not comparable) |
| `assess_volatility_gate` | one caller, `paper_engine.py:396` | no caller |
| `_tick_quality_guard` | `paper_engine.py:280`, called `:587` | no caller |
| `RiskManager.validate_trade` | called `paper_engine.py:665` | no caller → no `max_open_positions` cap, no drawdown cap |
| SL/TP anchor | the **fill** price (`paper_engine.py:566-567`) | the **pre-fill** reference (`execution_agent.py:139-140` → `executor.py:475-476` verbatim) |
| `account.balance` | mutated by `apply_balance_delta` at 4 sites | **never written**; the executor states this at `executor.py:26-30` |
| `max_drawdown` / `sharpe_ratio` | written (`position_manager.py:336-337`) | not written (`executor.py:831-839` passes neither) |

---

## 7. Data flow, end to end

### 7.1 WebSocket frame → memory

```
hyperliquid_feed.py:517  websockets.connect(HL_WS_URL, open_timeout=10, ping_interval=None, max_size=2**22)
hyperliquid_feed.py:513  _ensure_native_kernel()          kernel chosen ONCE, before the first frame
                            → core/microstructure.py:309 initialize_native_kernel
hyperliquid_feed.py:534  await asyncio.to_thread(self._handle_message, raw)
hyperliquid_feed.py:411  _handle_message(raw)
    "allMids"        :439  market_store.set_price
    "l2Book"         :452  _parse_book → :453 market_store.set_order_book
                                   → :458 microstructure.ingest_l2(...)   ← ONLY kernel write
    "candle"         :467  market_store.set_live_candle (also set_price with the close)
    "activeAssetCtx" :479/:481  set_funding / set_open_interest
    "trades"         :490  market_store.set_recent_trades
```

`market_store` (`core/market_store.py:22`) returns `None` for anything never actually
received — no synthetic depth, no default price, no fabricated candle. The HUD is
required to render an honest empty state.

Kernel: `TRADEBOT_KERNEL` → `initialize_native_kernel` (`core/microstructure.py:309`) →
`register_kernel` (`:190`, which validates 4 required callables and raises `TypeError`
without swapping). `HyperliquidFeed._ensure_native_kernel` (`:99`) is idempotent via a
flag and re-raises, so a bad `TRADEBOT_KERNEL=cpp` is an operator error, not a silent
degrade. A missing `.pyd` is not an error: `PythonKernel` gives identical numbers, slower.
The prebuilt `cpp_microstructure.cp314-win_amd64.pyd` (408 576 B) is present in this tree.

### 7.2 Decision

```
agents/direction_agents.py:437  run_cycle
      :474/:476  specialist evaluate (TechnicalAgent via asyncio.to_thread at :210)
      :478       analysis.direction_ensemble.aggregate(verdicts, cfg)
      :485       probability_engine.compute_directional_curve(...)
      :445       repo.insert_direction_snapshot(snapshot)          ← ONLY writer of that table
agents/decision_agent.py:473    repo.get_latest_direction_snapshot(symbol)   ← same newest row
      :477/:509  freshness gate, max_snapshot_age_seconds = 20
      :376-397   sizing (calculate_scalp_position_size) → quantity
      :407-423   validity gate (finite-positive qty/SL/TP + per-side ordering)
      :432-436   order.quantity / leverage / stop_loss / take_profit / price assigned
      :439-448   publish(TRADE_DECISION, order)
agents/execution_agent.py:74    drain the decision queue
      :96/:100   engine.get_price
      :117-121   analysis.volatility.get_dynamic_tp_sl_thresholds  (ADVISORY — see comment :105-116)
      :139-140   order.stop_loss / order.take_profit from the pre-fill reference
      :164       engine.execute_order(order)
```

Ensemble arithmetic (`analysis/direction_ensemble.py:83-191`): per-agent z is
`MAX_AGENT_Z * confidence` signed by direction, capped at `MAX_AGENT_Z = 2.5` (`:32`);
weight is `cfg.base_weight(agent) * (1 + agreement_bonus * agreement_score)`; the pooled
`z` is multiplied by `shrinkage_delta = 0.85` (`:163`) and pushed through a numerically
stable sigmoid (`:39-44`), then clamped to `[min_prob, 1-min_prob] = [0.02, 0.98]`;
`confidence = abs(prob_long - 0.5) * 2`. Weights: orderflow 0.30, momentum 0.25,
technical 0.25, microstructure 0.20 (`core/config.py:263-274`). A single maximally
confident agent therefore cannot exceed `prob_long = 0.8933` — verified by the arithmetic
`2.5 × 0.85 = 2.125 → sigmoid(2.125)`.

`abstained` is not `NEUTRAL`: an agent with no data is dropped from both numerator and
denominator (`direction_ensemble.py:102-107`). A zero-weight agent is skipped in the
pooling loop and flipped to `abstained=True` at `:141`, but `n_agents` still counts it.

### 7.3 Fill → realized P&L

Paper: `position_manager.close_position` (`position_manager.py:167`) charges
`close_fill_price` at `:216` (position side → fill side at `fill_cost.py:196`), computes
PnL from the FILL (`:227`), the closing fee from the FILL (`:235`), reads the **actual
opening fee back out of the `trades` table** (`:250-254`) instead of recomputing it, and
writes `net_pnl = pnl - open_fee - fee` (`:257`). The claim-once `repo.close_position`
(`database/repository.py:147-169`, `WHERE id = ? AND status = 'OPEN'`) makes double P&L
impossible; losers get `None`, which every caller treats as benign. Balance is credited
`margin + net_pnl + open_fee` (`:297`) through atomic `apply_balance_delta`.

Live: `LiveExecutor._record_close` (`executor.py:758`) is the single writer for both live
close paths. It refuses to write anything if entry or close price is not finite-positive
(`:777-786`), books `net = gross - fee_open - fee_close` where the exchange fill is the
`close_price` (`:790-806`), then `update_account_stats` (`:831-839`, no MDD/Sharpe) and
`gate.record_realized_pnl(net)` (`:847`). The whole body is try/except (`:881-884`): a DB
failure can never erase a fill that already happened.

### 7.4 Render

`run.py:849-854` starts `DashBoardThread`. `dashboard/app.py:51-65` defines two intervals:
500 ms and 60 s. `register_callbacks` (`update_callbacks.py:114`) attaches **19**
`@app.callback` decorators at lines 120, 148, 235, 244, 253, 261, 269, 298, 310, 386, 407,
619, 709, 784, 909, 997, 1051, 1197, 1342 — 8 `update_legacy_*` shims bound to hidden divs
(`:128`, `:155`, `:241`, `:250`, `:258`, `:266`, `:274`, `:303`) plus 11 real HUD callbacks.
The 60 s `update_trade_stream_and_marquee` (`:1051-1057`) is the only one on the slow
interval.

Every callback opens a fresh blocking `sqlite3.connect(cfg.database_path)` via
`_get_sync_db()` (`update_callbacks.py:64-69`) and closes it in the same function. No file
under `dashboard/` imports or references `EventBus`, `Channels` or `subscribe` — grep
returns zero matches. Live fills reach the HUD **only** through the SQLite mirror.

Honest-empty-state doctrine, all verified: slippage `N/A` (`:1310-1318`), MDD/Sharpe
`N/A` (`:1292-1295`, `:1328`), candles `AWAITING 1M CANDLE INGEST // NO SYNTHETIC DATA`
(`:441-467`), direction scanner `AWAITING` (`:809-817`).

### 7.5 The cost model, in one place

`trading/fill_cost.py` is the shared exit-side model, created because the cost used to
live only in `_fill_price` so only opens were charged. Constants (`fill_cost.py:45-48`):

```python
FILL_HALF_SPREAD_FLOOR = 0.0003             # 3 bps
FILL_IMPACT_FLOOR = 0.0001                  # 1 bps
FILL_BOOK_MALFORMED_SPREAD_PCT = 0.05       # 5% — above this the book is broken, not the market
FILL_MAX_TOTAL_COST_PCT = 0.0050            # 50 bps, a backstop only
```

`fill_price_after_cost` (`:105`): observed half-spread is admitted only when the book is
provably fresh — `if not book or age is None or age > max_book_age_seconds: return None`
(`:76`), an unstamped book is stale, not fresh. Then
`half = max(observed, FILL_HALF_SPREAD_FLOOR)` (`:152` — `max`, never `min`, because
`market_store` never expires books), `impact = FILL_IMPACT_FLOOR` constant (`:157`),
`total = min(half + impact, FILL_MAX_TOTAL_COST_PCT)` (`:159`),
`signed = +total if fill_side == "BUY" else -total` and
`fill = ref_price * (1.0 + signed)` (`:162-163`). The age budget is taken from
`config.scalping.max_tick_age_seconds` (1.5 s, `config.yaml:153`) at `:133-139` so the
cost model and the tick guard cannot drift apart.

At the configured scalp with no book: entry crossing 4.0 bps, exit crossing 4.0 bps,
taker fees 2 × 4.5 bps → **round trip 17.0 bps**. Paper re-anchors SL/TP to the fill
(`paper_engine.py:566-567`), so a stopped trade actually loses 13.0 bps of cost, not 17.
`assess_volatility_gate` prices the round trip as `fees.taker * 2` = 9.0 bps only
(`analysis/volatility.py:383-384`) — it never imports `fill_cost`, so it understates the
required breakeven win rate. The boot validator uses the same fees-only figure
(`core/config.py:959-974`).

---

## 8. Persistence

`database/db.py:13` `SCHEMA_SQL`, executed on **every** connect (`:191`
`executescript`), idempotent via `IF NOT EXISTS`, no migration versioning.
`PRAGMA journal_mode=WAL` then `PRAGMA busy_timeout=15000` (`db.py:166-195`) — the
timeout is load-bearing: 1150 recorded lock failures at the old 5000 ms actually crashed
the execution loop.

Ten tables: `candles`, `positions`, `trades`, `signals`, `news`, `agent_logs`, `account`,
`balance_history`, `direction_snapshots`, `macro_data`.

Load-bearing rules:

- OHLC invariant enforced twice, identically — `PriceFeed.fetch_ohlcv:256-259` and
  `repository._valid_candle:17`. A single bad row permanently rescales the chart's y-axis.
- Candle write is UPSERT, not INSERT OR IGNORE.
- yfinance OHLCV is **never persisted** — different instrument, different volume scale.
- `account.balance` moves only through `apply_balance_delta` (SQL `balance = balance + ?`).
  `update_balance` and `update_account` are absolute writers with **zero production
  callers** — which is the only reason `peak_balance` (SQL `MAX(COALESCE(peak_balance,0),?)`)
  is monotonic.
- Every `account` mutation targets `WHERE id = (SELECT MAX(id) FROM account)`; querying
  `id = 1` is a known trap after `reset_paper_db.py`.
- All stored timestamps are TEXT from SQLite `datetime('now')` = UTC. `candles.timestamp`
  is the exception: INTEGER Unix **milliseconds** straight from the exchange.
- `trades` has no PnL column. Every realized-PnL figure joins back to
  `positions.realized_pnl`.
- `direction_snapshots.agent_breakdown` and `.diffusion` arrive already JSON-encoded;
  `insert_direction_snapshot` does not serialize.
- Non-`Repository` SQL in production: one `UPDATE news` (`analysis_agent.py:251-255`),
  one `DELETE FROM candles` (`run.py:810`), and out-of-band `sqlite3` readers in
  `dashboard/callbacks/update_callbacks.py:67`, `agents/direction_agents.py:341`
  (`_load_closes`), and `analysis/backtester.py:332-349` (tests-only).

---

## 9. Glossary of local jargon

| Term | Meaning in this repo |
|---|---|
| **microstructure** | L2 order-book shape: per-level weighted volume, Order Flow Imbalance, relative spread. Isolated in `core/microstructure.py` behind the `MicrostructureKernel` ABC so the compute can be swapped for native code without touching an agent. |
| **OFI (Order Flow Imbalance)** | `(ΣBidSize − ΣAskSize) / (ΣBidSize + ΣAskSize)` over the top N levels with linearly decreasing per-level weights, `1.0 - 0.1 * i`, spelled differently on each side. `PythonKernel._weighted_volume` hardcodes them inline — `total += size * (1.0 - 0.1 * i)` (`core/microstructure.py:127`, loop body `:126-128`) — with no named constant in that file at all; this is the reference implementation and the kernel that runs whenever the `.pyd` is missing. The C++ side declares the named constants `kLevelWeight0 = 1.0` and `kLevelWeightStep = 0.1` (`cpp/microstructure_kernel.cpp:57-58`) but its own OFI hot path does **not** consume them: `weighted_volume` re-hardcodes the literal `sizes[i] * (1.0 - 0.1 * static_cast<double>(i))` (`cpp/microstructure_kernel.cpp:195`, called from `book_ofi` at `:231-232`), which leaves the constants reachable only through the exported `level_weight()` helper (`cpp/microstructure_kernel.cpp:62-64`, bound at `cpp/bindings.cpp:160`). Nothing in the source links the two spellings — no shared constant, no generated header — but the divergence is **not** silent: `TestCppPythonParity` compares both kernels at runtime against `PythonKernel` at 1e-7 tolerance across 9 test definitions, so a Python-side edit is caught immediately. The C++ side is conditional: `test_cpp_weight_constants_match_python` only greps the C++ *text* for the two `constexpr` declarations and never inspects the Python literal, and the whole parity class sits behind `@unittest.skipIf(CPP is None)`. On a tree without the built `.pyd`, an edit to the C++ hot path is caught by nothing. Any weight change must be applied to `core/microstructure.py:127` and `cpp/microstructure_kernel.cpp:195` together — matching summation order as well, since float addition is not associative (`cpp/microstructure_kernel.cpp:188-191`) — to keep the kernels bit-exact. Range `[-1, 1]`. Returned as `(ofi, relative_spread)`. Absent L2 data returns `(0.0, 0.0)` by explicit doctrine (`probability_engine.py:72-79`), and callers abstain rather than guess. |
| **constellation** | The dashboard's "Snipe Neural Net" graph — a 13-node structural TREE (1 root → 4 categories → 4 agents → 4 outputs) plus 12 token satellites, in `dashboard/layouts/hud_figures.py:307-359`. `NEURAL_SYSTEM_EDGES` guarantees every child has exactly one parent, so edges provably never cross. It is a structural map, not a data flow; only tokens that genuinely have a signal are drawn. |
| **HUD** | The single-screen Dash layout (`dashboard/layouts/hud.py:20` `create_hud_layout`), 12-column CSS grid, warm-parchment theme `#dfd5b8`, two tickers (500 ms / 60 s). |
| **scalp** | The whole trading style: 0.25% stop, 0.60% target, `max_hold_seconds = 300`, `min_hold_seconds = 15`, `batch_size = 2`, decisions every 3 s on a 0.3 s execution loop. |
| **fill cost** | `trading/fill_cost.py` — the shared crossing-cost model (half-spread floor + constant impact, capped at 50 bps, sign-flipped by fill side). Floors, not estimates. |
| **cloid** | Client order id, `make_cloid()` → `"tb-<16 hex>"` (`executor.py:60-68`). **Required** for any opening order: `Blocker.MISSING_CLOID` (`safety.py:327-328`). Without it, a timeout makes retry double the position. |
| **SafetyGate** | `trading/live/safety.py:159` — the single admission gate for real money. Returns `(allowed, blockers)`, a TUPLE, so the reason always travels with the refusal. 9 master checks + up to 7 per-order checks. Mandatory: the only route to the exchange is `LiveEngine.submit_order` → `gate.can_send`. |
| **direction_snapshots** | SQLite table, one row per symbol per ensemble cycle (5 s). The **single shared row** that `DecisionAgent` and the HUD both read, so what is traded and what is displayed cannot diverge. |
| **abstain** | An agent's verdict meaning "I have no data", distinct from NEUTRAL. Abstaining agents are removed from both the numerator and the denominator of the pool (`direction_ensemble.py:102-107`). |
| **claim-once** | `UPDATE ... WHERE id = ? AND status = 'OPEN'` returning `rowcount > 0`. Four racing close paths contend; losers get `False`/`None`, which every caller treats as benign. This is what makes double-charged P&L impossible. |
| **"ROE" (repo's own name for a return on position notional)** | P&L as a percentage of POSITION NOTIONAL, not of the account's equity: `trading/risk_manager.py:264-265` sets `position_value = entry * qty` then `pnl_pct = (pnl / position_value * 100) if position_value > 0 else Decimal("0")`. `calculate_pnl` (`:242`) returns that single value under both keys `roe_pct` and `pnl_pct` (`:269-270`), and `trading/live/executor.py:867-870` prints it in a live `TRADE_CLOSED` AgentLog as `ROE {:+.2f}%`. The `roe_pct` field name and the `ROE` log label are misnomers; the denominator is position notional. |
| **T1–T4 network tiers** | Price-source fallback ladder: T0 Hyperliquid (WS+REST, primary), T1 ccxt Binance Futures, T2 yfinance, T3 CoinGecko. Failure means fall to the next tier; ccxt failure latches `_ccxt_available = False` for the process lifetime. No retries anywhere in that slice except the WS reconnect. |
| **VADER / FinBERT** | The two sentiment tiers: VADER is rule-based and synchronous per headline; FinBERT (`ProsusAI/finbert`, CPU) runs in a 900 s batch job. Missing model degrades silently to rule-based at INFO (`ml_signals`-style pattern; `sentiment.py`). |
| **paper ledger** | The `account` row. Mutated only in paper mode. In live, `account.balance` is a stale paper number and the real wallet is printed from `free_collateral` at every fill. |
| **quiet mode** | `core/logger` suppresses INFO/WARNING on the console during startup so only stage/step lines show; `end_quiet_mode()` (called at `run.py:901`) restores full logging once boot completes. |
| **monitor-only** | `LiveEngine.run_loop` with `on_decision=None`. `run.py:317-319` passes a placeholder that returns `None`, so the 5 s loop can only run `health_check` and `check_pending_fills` — it is physically incapable of sending an order. |

---

## 10. What changed recently

The previous `docs/context/CONTEXT.md` was written before the following landed. Each claim
below was re-derived from source in this tree.

### 10.1 The six live-trading fixes

| # | Change | Where | What the old doc said |
|---|---|---|---|
| 1 | `LiveExecutor` mirrors live fills into SQLite and publishes to the EventBus | `_persist_open` `executor.py:505`, `_record_close` `:758`, publish sites `:609` / `:622` / `:853` | "LiveExecutor writes nothing to the Repository and publishes nothing to the EventBus" — **false**, and the largest single correction |
| 2 | `LiveExecutor` implements **all six** members `ExecutionAgent` touches | `update_price` `:210`, `get_price` `:237`, `check_positions` `:259`, `execute_order` `:397`, `_last_prices` `:110`, `position_manager` `:115` | "implements 2 of the 6 members" — **false**. `ExecutionAgent` touches those six at `execution_agent.py:64, 71, 96, 100, 164, 192, 213, 266, 270, 290, 311`. Note `tests/test_live_executor.py` still only checks two, so the old number described the *test*, not the class. |
| 3 | `DecisionAgent` always computes and validates `Order.quantity` before publishing | `decision_agent.py:397` (sizing), `:407-423` (validity gate), `:432-436` (assignment), `:439` (publish) | "Order.quantity is None and becomes size 0.0" — **false on both halves**. The `or 0.0` fallback is gone from the live path; `_open` now hard-refuses a non-finite-positive quantity (`executor.py:438-449`) and a non-`int`/`< 1` leverage (`:450-461`). Paper's `if order.quantity is None` branch (`paper_engine.py:616-631`) is now dead in production. |
| 4 | `SafetyGate.record_realized_pnl` has a production caller | `executor.py:847`, inside `_record_close`, right after `update_account_stats` at `:831-839` and right before the `POSITION_UPDATE` publish at `:849` | "has zero production callers" — **false**. It is the only feed for `Blocker.DAILY_LOSS_LIMIT` (`safety.py:293`). The comment at `executor.py:841-846` that says otherwise is itself stale. |
| 5 | Live position index is by **coin**, not symbol | `check_positions` `executor.py:302`, `_find_open_row` `:750-753` | per-symbol matching — **false**; `submit_order` stores the caller's symbol verbatim while `check_pending_fills` stores `"BTC / USDC:USDC"` (`engine.py:491`), so per-symbol matching orphans half of live positions |
| 6 | `update_price` is a real cache write | `executor.py:210-235` | "a deliberate no-op returning None" — **false**; it is why `ExecutionAgent`'s three `self.engine._last_prices` reads do not raise `AttributeError` in live mode |

### 10.2 Phase B accounting work

- **Entry-side slippage model** — `PaperTradingEngine._fill_price` (`:240-278`) now calls the
  shared `fill_cost.fill_price_after_cost`, and SL/TP are re-derived from the **fill**, not
  the pre-fill reference (`:562-567`).
- **`trading/fill_cost.py` is a new module** (216 lines) providing the shared exit-side
  model. `PositionManager.close_position` (`:216`) and `_liquidate` (`:472`) now charge it,
  so every close path pays exit cost. Previously every close used the raw market price and
  P&L booked half the round trip.
- **Liquidation charges a fee** — `position_manager.py:479-480`:
  `fee = calculate_fee(qty, fill, "TAKER")`, `realized_pnl = -pos["margin"] - fee`.
- **`max_drawdown` and `sharpe_ratio` are now written** — `position_manager.py:325-337`
  computes them from the 500-row `balance_history` equity series and passes both to
  `update_account_stats` at `:331-343`. The old doc's "never written" is **false for the
  paper path**; it remains **true for the live path**, which passes neither
  (`executor.py:831-839`), so in live mode the HUD's MDD and Sharpe tiles stay `N/A`.
- **The duplicate `TestFeeAccounting` class was renamed** — `tests/test_lifecycle_paths.py`
  now has `TestFeeAccounting` at `:75` and `TestOpeningFeeNotDoubleCharged` at `:253`, 11
  distinct class names, no collision.

### 10.3 Numbers the old document got wrong

| Claim in CONTEXT.md | Actual (re-derived) |
|---|---|
| 566 collected tests | **569** collected, but not all passing: `python -m pytest tests -q` → `18 failed, 547 passed, 4 skipped`. Per-file **def** counts: advanced_modules 59, bugfixes 62, config 6, cpp_kernel 22, dashboard_palette 11, decision_agent_ensemble 10, direction_agents 20, direction_ensemble 14, fill_price_sl 4, indicators 3, layout_budget 10, layout_contract 18, lifecycle_paths 54, live_console 27, live_engine 36, live_executor 21, live_safety 45, live_tui 65, neural_net_layout 27, numerics 19, paper_engine 4, position_manager 6, probability_engine 16, risk_manager 11 — 570 `def test` lines in total, of which 569 are collected (the two identically-named `test_axes_have_no_scaleanchor` defs in `test_neural_net_layout.py` collapse to one). 194 of the 569 are in the five live modules. `tests/` = 26 files (24 test modules + `__init__.py` + `t3.py`, a 24-line non-test WS probe) / 9 342 lines. |
| `run.py` is 878 lines | **995** |
| 11 tables | **10** |
| 20 Dash callbacks | **19** `@app.callback` decorators |
| 16 test files / 108+ tests (README) | 24 test modules / 569 collected cases (18 of them failing — §11) |
| `run.py:487-585` for the scheduler | `run.py:520-618` |
| APScheduler "11 jobs" | 10 registered from `run.py` + 1 internal `_market_check` (`scheduler.py:121`) = 11 total. The 11-job figure is right; the line range was not. |

### 10.4 Loud config failure

`load_config` warns loudly on a missing `config.yaml`: `_warn_missing_config_file`
(`core/config.py:557-620`), dispatched at `:628-630`. Two output channels — a `logger.warning`
into the rotating file, and a `print` block to **stderr** listing the six `RiskConfig` keys
that differ from `config.yaml` (`_MISSING_CONFIG_RISK_DEFAULTS`, `:547-554`) — because
`load_config` can run before `setup_logger` and `get_logger` must not be called from here
(re-entrancy into `load_config`, explained at `:410-431`). The stated most-common cause is
**the wrong working directory**.

`_apply_dict` (`:486-529`) now warns on unknown keys and on type mismatches
(`_type_matches`, `:449-483`) while still applying the value. Note: `agent_log_prune_interval`
and `agent_log_keep` are written under `database:` in `config.yaml:115-116` but the loader
only filters keys starting with `snapshot_` (`core/config.py:734-736`), so both keys are
dropped at load and the dataclass defaults at `:400-401` are what `run.py` reads
(`:496`, `:595`). Those defaults are 1800 and 5000 — the same values `config.yaml:115-116`
already carries, so the drop is currently invisible. The source comment at `:729-733` calls
it a latent behavior bug rather than a live divergence: editing either key in `config.yaml`
would have no effect until the filter is widened.

Three boot-time validators raise `ValueError`: `_validate_scalping_economics` (`:932`),
`_validate_ensemble_config` (`:~858`), `_validate_dynamic_tp_sl` (`:756`).

---

## 11. Test suite and known gaps

**The suite is not green.** `python -m pytest tests -q` →
`18 failed, 547 passed, 4 skipped` in ~13 s, reproducible across runs. `python -m unittest
discover tests` is not green either: it reports the same 16 `test_lifecycle_paths.py`
failures (plus, on a second run, `15 failures, 1 error` — `test_lifecycle_paths.py` shares
one on-disk SQLite file, `data_store/test_lifecycle.db`, across all its `IsolatedAsyncio`
cases, so its own results are order-dependent).

The 4 skips are all `tests/test_live_tui.py::TestKeyParsing` (`setUp` at
`test_live_tui.py:368-370` calls `self.skipTest` when `os.name == "nt"`).

Two independent failure clusters:

**1. Sixteen real failures, all in `tests/test_lifecycle_paths.py`** (54 test defs in the
file, 38 of them pass). They are not harness artefacts — they reproduce under both runners
and after deleting `data_store/test_lifecycle.db*`. Representative tracebacks read:

- `TestFeeAccounting` (3 errors) — `close_position` returns `None` and the position's
  `realized_pnl` is left `None`, so `TypeError: unsupported operand type(s) for -:
  'float' and 'NoneType'` at `test_lifecycle_paths.py:151` and `:106`.
- `TestBatchPaths`, `TestRaceAndDoubleClaim`, `TestTriggerPaths`,
  `TestAgentLogPruning`, `TestEngineInputValidation` (12 failures) — e.g.
  `AssertionError: 1 != 0` at `:459`, `AssertionError: 3 != 4` at `:442`.

**2. Two pytest-only failures** in
`TestUnpatchedSmoke::test_ask_mode_escape_via_real_path` and
`::test_ask_mode_paper_via_real_path`. These *are* harness artefacts: pytest's capture makes
`input()` inside `console.show_banner` → `console._read` (`console.py:167`) raise
`OSError: pytest: reading from stdin while output is captured!`. They pass under `unittest`
— but that does not make `unittest` the clean signal, because cluster 1 fails under both.

Coverage gaps, re-derived:

- **`data/` has zero test coverage.** No test file imports `data.price_feed`,
  `data.hyperliquid_feed`, `data.macro_fetcher`, `data.news_fetcher` or `data.sentiment` —
  1 864 lines covering every network call, timeout, fallback tier and cache TTL in the bot.
- `dashboard/callbacks/update_callbacks.py` (1 429 lines) and `dashboard/app.py` (148) have
  no test importer. Only `hud`, `hud_figures` and `palette` are imported by tests.
- `agents/analysis_agent.py` (318), `agents/base_agent.py` (151), `agents/news_agent.py` (145)
  have no test importer. `core/logger.py` (403, the most-imported module in the repo) and
  `core/scheduler.py` (139) likewise.
- No test imports `ml.trainer`, `ml.predictor` or `analysis.ml_signals` — the entire ML path
  is unexercised.
- 51 of the 569 test methods read production **source text** instead of importing it
  (architecture guards); 48 of them are pure text guards that would pass if the
  guarded code were empty, and 3 parse the file with `yaml.safe_load` and assert
  key presence. Counted by walking each `test_*` function's AST for a `read_text` /
  `read_bytes` / `open()` call on a production path **and resolving helper functions
  transitively** — a direct-call-only count gives 18, because helpers like `_read`
  (`test_bugfixes.py:34`), `_read` (`test_layout_contract.py:30`), `_code_only`
  (`test_dashboard_palette.py:36`) and `_dash_layout_ids` (two levels deep via
  `_mounted_ids_at_runtime`) are invisible to it. Per file: `test_bugfixes.py` 20,
  `test_layout_contract.py` 18, `test_cpp_kernel.py` 5, `test_dashboard_palette.py` 4,
  `test_advanced_modules.py` 2, `test_layout_budget.py` 1, `test_neural_net_layout.py` 1.
  a production path. Per file: layout_budget 6, cpp_kernel 5, bugfixes 4, dashboard_palette 1,
  advanced_modules 1, neural_net_layout 1.
- Two production modules are fully orphaned — no production importer, no test importer:
  `analysis/vol_target.py` (132 lines) and `ml/predictor.py` (44 lines).
- One test def is shadowed: `test_neural_net_layout.py:240` and `:259` both define
  `test_axes_have_no_scaleanchor`; 27 defs, 26 collected.

---

## 12. Not verified

- **Run time behaviour.** Every claim here is from reading source and from test/collection
  runs. The bot itself was not started, no exchange socket was opened, and no live or
  testnet order path was executed.
- **Line counts** were measured with `[System.IO.File]::ReadAllLines(...).Count`, not
  `Measure-Object -Line`, which undercounts by one per file. Package totals include
  `__init__.py`.
- **`requirements.txt` declares 23 packages.** Four are never imported anywhere in
  first-party code: `praw`, `fredapi`, `dash-bootstrap-components`, `torch`. Five packages
  are imported but undeclared: `eth_account`, `flask`, `werkzeug`, `click`, and
  `playwright` — and `playwright` only in scratch scripts (`verify_*.py`,
  `data_store/*.py`), never in the shipped packages. A sixth, `msgpack`, is listed in this
  bullet as undeclared in earlier revisions; it is **not** imported by any `.py` file in the
  tree — `requirements.txt:15` and `trading/live/client.py:18` only mention it in prose —
  so it drops to five. `requirements.txt:19` states `eth_account` and `msgpack` are pulled
  in transitively on purpose.
- **The C++ kernel's parity, performance, and exception behaviour** were not re-measured
  here. `cpp/` is 1 326 C++ lines plus a 152-line `CMakeLists.txt`; the prebuilt `.pyd`
  (408 576 B) is present and `cpp_microstructure.__version__` is `1.0.0`. `TRADEBOT_KERNEL`
  selection, the `register_kernel` validation tuple (`ingest_l2`, `order_flow_imbalance`,
  `depth_imbalance`, `reset`) and the `PythonKernel` reference implementation are cited from
  source only.
- **`_execution_loop`'s escalation ladder** was verified by reading
  `run.py:669-709` and the constants at `:81-94`, not by inducing failures at runtime.
  The tiers are mutually exclusive per failure: `<= 3` → full traceback (`:671-679`),
  `== 100` → one `CRITICAL` (`:680-696`), `% 50 == 0` → one-line pulse (`:697-703`). The
  counter is **consecutive**, reset to 0 by the `else` branch at `:704-709`, and
  `await asyncio.sleep(_EXEC_TICK_SECONDS)` sits deliberately **outside** the try (`:715`)
  so a failing iteration still pays a full tick instead of hot-spinning.
- **`shutdown()` never awaits the cancelled tasks** (`run.py:924-925` cancels, then `:929`
  closes the DB). Whether a cancelled task can hit a closed connection was not reproduced.
- **The 18 test failures** were observed by running both runners and reading the
  tracebacks. Cluster 1 (`test_lifecycle_paths.py`) is characterised from its symptoms —
  `close_position` yielding `None`/null `realized_pnl`, and stale rows surviving
  batch-close and prune — but the underlying cause was **not** diagnosed. The
  order-dependence of that file's own results across two `unittest` runs was observed, not
  explained.


---

*Dokumen ini mengoreksi klaim dari `docs/context/ARCHIVED-2026-09-28.md`
(selama ini `docs/context/CONTEXT.md`). Berkas itu sudah diarsipkan dan
ditandai usang; nama lamanya tidak lagi dipakai supaya tidak ada dua
sumber kebenaran untuk hal yang sama.*
