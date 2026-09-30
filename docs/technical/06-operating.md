# 06 — Operating and Extending

Everything in this document was re-derived from the working tree at commit time. Where the older
`docs/context/CONTEXT.md` disagrees, the corrections are stated inline with the evidence.

---

## 1. Running the bot

### 1.1 Prerequisites

| Requirement | Value | Source |
|---|---|---|
| Python | 3.10+ (this tree runs CPython 3.14, `cp314-win_amd64` ABI) | `README.md:201` |
| Dependencies | `pip install -r requirements.txt` | `README.md:207` |
| Working directory | **must be the repo root** | `core/config.py:557-620` |
| OS | Windows 11 (POSIX works; key decoding and stdout encoding differ) | `trading/live/tui.py:191,213` |

The working-directory rule is not a convention, it is enforced loudly. `load_config()` resolves
`Path("config.yaml")` relative to the CWD; when the file is missing it does **not** return silently.
It prints a block to stderr and logs a warning naming the six `RiskConfig` keys that will differ
from the ones in `config.yaml` (`core/config.py:547-554`):

```
max_risk_per_trade=0.02 (config.yaml: 0.005) — risiko per trade 4x lebih besar
max_leverage=20 (config.yaml: 50)
max_daily_loss=0.05 (config.yaml: 0.10)
max_drawdown=0.15 (config.yaml: 0.20)
max_open_positions=3 (config.yaml: 30)
default_leverage=5 (config.yaml: 10)
```

The dataclass default is *higher* on taker: `fees.taker` defaults to `0.0005` against `0.00045` in
`config.yaml` (`core/config.py:33`, `config.yaml:60`) — the fallback is 0.5 bp *more* expensive per
fill, not less, so the source's own parenthetical `biaya turun` ("cost goes down") at
`core/config.py:609` is backwards. The `decision_agent` delta is real: 60s instead of 3s
(`core/config.py:42`, `config.yaml:68`) means the bot that "safely" fell back decides **20x
slower**, which is a different strategy, not a safer one.

Boot-time config validation is a hard gate. `load_config` calls, in order
(`core/config.py:750-753`):

```
_validate_scalping_economics(config)
_validate_ensemble_config(config)
_validate_dynamic_tp_sl(config)
```

each of which raises `ValueError` with a bulleted problem list. Typos are caught too: `_apply_dict`
warns per unknown key and per type mismatch, and **still sets the value**
(`core/config.py:486-530`). Deliberate — a wrong-typed value must not prevent boot, but it must
never be silent either.

One config key is deliberately dead: `config.live.enabled` is force-written to `False` on every
load (`core/config.py:700`), so nothing in `config.yaml` can arm real money.

### 1.2 The four run modes

`_choose_mode()` (`run.py:186-233`) resolves the mode **before any subsystem is constructed**.

| Invocation | Mode | Notes |
|---|---|---|
| `python run.py --paper` | `paper` | skips the menu entirely; the only non-interactive-by-flag path |
| `python run.py --non-interactive` | `paper` | same branch, `run.py:202` |
| `python run.py --testnet` | `testnet` | prints a one-line confirmation |
| `python run.py --live` | `mainnet` | prints a five-line real-money warning first |
| `python run.py` | menu | interactive TUI when a TTY exists, numbered `input()` fallback otherwise |

Branch order is load-bearing: `--paper` is checked **before** the `--testnet`/`--live` loop, so
`python run.py --paper --live` is simulation (`run.py:202-209`).

A flag selects the mode, but does **not** enable live sending. That requires two environment
variables read by `SafetyGate.master_blockers` (`trading/live/safety.py:266-274`):

```
TRADEBOT_LIVE=1
TRADEBOT_LIVE_CONFIRMED=1
```

plus `HYPERLIQUID_PRIVATE_KEY` in the environment (`LiveConfig.private_key_env`,
`core/config.py:318`). Nothing in the repository sets `TRADEBOT_LIVE` outside tests — grep across
all `.py` returns only `config.py:321` (the field name), `safety.py`, `live_doctor.py:189-190`
(which deliberately *pops* them) and `tests/`. So on a fresh checkout the gate always refuses with
`LIVE_DISABLED` + `NOT_CONFIRMED`. That is fail-closed by design, and it also means no code path in
this repo has ever been observed to reach a live order.

### 1.3 What boots, in order

```
main()                                              run.py:934
 ├─ print_banner() / setup_logger()                 run.py:935-936
 ├─ _choose_mode()                                  run.py:941   ← BEFORE any subsystem
 ├─ TradingBotApp(decision)                         run.py:942
 └─ app.initialize()                                run.py:955
     ├─ init_db()                    database/db.py:248
     ├─ paper_engine.initialize()    trading/paper_engine.py  (init_account, 10000 USDT)
     ├─ price_feed.initialize()
     ├─ discover_top_volume_symbols(limit=top_n)   ← OVERWRITES config.symbols
     ├─ price_feed.start_streaming()  Hyperliquid WS opens here
     ├─ sentiment_analyzer.initialize()             VADER always, FinBERT best-effort
     ├─ 5 agents constructed, execution engine chosen
     ├─ DirectionEnsembleAgent.initialize()
     ├─ _prefetch_historical_candles()  1m/5m/1h, limit 240/120
     ├─ analysis_agent.update_macro()   bracketed by "Reading macro calendar"
     └─ print_startup_summary()
```

Five asyncio tasks are created in `run()` (`run.py:878-896`): price feed, execution loop (0.3 s),
candle refresh (30 s), telemetry (30 s), maintenance/prune loop. The dashboard runs on a sixth
execution context, a daemon `threading.Thread` named `DashBoardThread` (`run.py:846-855`).

Live mode adds a **seventh**: `engine.run_loop(interval=5.0)` at `run.py:321-322`. It is assigned
to `self._live_task` and **is never added to `self._background_tasks`** (`run.py:894-896`), so
`shutdown()` never cancels it — see §8.9.

### 1.4 Exit codes and failure messages

`__main__` catches `RuntimeError` from live setup and prints an operator-readable block, exiting
with code 2 (`run.py:981-995`):

```
  BOT TIDAK DIJALANKAN.
  Alasan: <message>
```

Two `RuntimeError`s are raised on purpose: missing private key (`run.py:294`) and failed health
check (`run.py:309`). Downgrading either to a generic exception throws away the message the block
exists to deliver.

---

## 2. The dashboard

```
python run.py                  # dashboard is one of the startup stages
python -m dashboard.app        # dashboard only (dashboard/app.py:147-148)
```

Default bind is `127.0.0.1:8050` (`DashboardConfig`, `core/config.py:71-75`, mirrored in
`config.yaml:119-123`). Two intervals drive it (`dashboard/app.py:51-65`):

| Interval id | Period | Bound callbacks |
|---|---|---|
| `dashboard-interval` | 500 ms | 10 HUD callbacks (plus 8 legacy/compat callbacks, 18 bound to the fast interval in total) |
| `dashboard-slow-interval` | 60 s | `update_trade_stream_and_marquee` |

Note `DashboardConfig.update_interval: int = 2000` (`core/config.py:75`) is **dead config**. A
repo-wide grep finds exactly one hit: its own declaration. Both periods are hardcoded literals.

To silence the Werkzeug access log (the 500 ms poll floods the terminal otherwise), both
`run.py:24-39` and `dashboard/app.py:12-15` set `logging.getLogger("werkzeug").setLevel(ERROR)` and
replace `WSGIRequestHandler.log_request` with a no-op. Set `TRADEBOT_BANNER=1` to keep the Flask
startup banner (`dashboard/app.py:112`).

---

## 3. The test suite

### 3.1 The authoritative numbers

Re-derived by execution, not carried over. **CONTEXT.md's `566` is stale.**

```
$ python -m pytest --collect-only -q
569 tests collected in 3.24s

$ python -m unittest discover tests
Ran 569 tests in 13.077s
OK (skipped=4)
```

Per-module counts via a `unittest.TestLoader` walk (sums to 569):

| Module | Cases | Module | Cases |
|---|---|---|---|
| test_advanced_modules | 59 | test_live_console | 27 |
| test_bugfixes | 62 | test_live_engine | 36 |
| test_config | 6 | test_live_executor | 21 |
| test_cpp_kernel | 22 | test_live_safety | 45 |
| test_dashboard_palette | 11 | test_live_tui | 65 |
| test_decision_agent_ensemble | 10 | test_neural_net_layout | 26 |
| test_direction_agents | 20 | test_numerics | 19 |
| test_direction_ensemble | 14 | test_paper_engine | 4 |
| test_fill_price_sl | 4 | test_position_manager | 6 |
| test_indicators | 3 | test_probability_engine | 16 |
| test_layout_budget | 10 | test_risk_manager | 11 |
| test_layout_contract | 18 | **total** | **569** |
| test_lifecycle_paths | 54 | | |

`tests/` holds 26 `.py` files: 24 test modules, `__init__.py`, and `t3.py` — which is **not** a
test. It is a 24-line manual WebSocket probe against `wss://api.hyperliquid.xyz/ws`
(`tests/t3.py:6`), named so loosely that `unittest discover` ignores it and collects zero cases.
Running it opens a live socket. Total lines across `tests/*.py`: **9 349**.

README.md is stale on three counts: "14 file test, 108+ test" (`:179`, `:221`) and "11 tabel"
(`:39`, `:111`, `:142`, `:245`). The schema has exactly **10** `CREATE TABLE IF NOT EXISTS` and 8
`CREATE INDEX IF NOT EXISTS` statements in `database/db.py:13-155`, and README's own list at
`:111` enumerates ten names.

### 3.2 Why `pytest` and `unittest` disagree on this tree

```
$ python -m pytest -q
2 failed, 563 passed, 4 skipped          # TestUnpatchedSmoke, stdin capture

$ python -m pytest -q -s
565 passed, 4 skipped                    # correct

$ python -m unittest discover tests
OK (skipped=4)
```

`tests/test_live_tui.py::TestUnpatchedSmoke` drives `console.ask_mode` through its real path with
only `is_tty` faked (`tests/test_live_tui.py:593-626`). `ask_mode` calls `show_banner()`
(`trading/live/console.py:419`), which calls `_read()` → `input()` (`console.py:275,167`). Under
pytest's default output capture, stdin is a `DontReadFromInput` object that raises `OSError`.
`console._read` is not patched in that class, so the failure is environmental, not a product bug.

**Use `python -m unittest discover tests` for the authoritative result.** Add `-s` (pytest) or
`-p no:cacheprovider` only if you are debugging that specific class.

### 3.3 What the skips are

All four live in `tests/test_live_tui.py::TestKeyParsing` (`:359`) and cover POSIX-only key
decoding. They are platform-stable on Windows, not flaky.

### 3.4 Coverage the suite does not have

| Gap | Size | Consequence |
|---|---|---|
| All of `data/` | 1 864 lines, 5 modules, zero test importers | every network call, timeout and fallback tier is untested |
| `dashboard/callbacks/update_callbacks.py` | 1 429 lines, 19 callbacks, no test importer | the whole render path is unguarded except by source-text greps |
| `agents/{analysis,news,base}_agent.py` | 614 lines, no test importer | |
| `analysis/ml_signals.py` + `ml/` | 535 lines, no test importer | reached on the live path, but no test executes the RandomForest branch |
| `core/logger.py`, `core/scheduler.py` | 542 lines, no test importer | |

`analysis/backtester.py` (980 lines) is the inverse: well tested, **zero** production importers.

### 3.5 Seven tests read source text instead of importing

These are architecture guards. They pass even if the guarded code is empty, and they are invisible
to import-graph tooling.

| File | Lines | Reads |
|---|---|---|
| `tests/test_advanced_modules.py` | 353, 793 | `agents/execution_agent.py`, `config.yaml` |
| `tests/test_bugfixes.py` | 1098, 1104, 1115, 1146 | `run.py` (x2), `data/hyperliquid_feed.py`, `.gitignore` |
| `tests/test_cpp_kernel.py` | 271, 305, 318, 337, 345 | `cpp/*`, `CMakeLists.txt` |
| `tests/test_dashboard_palette.py` | 49, 58, 74, 82 | layout `.py` + `style.css` |
| `tests/test_layout_budget.py` | 159 | `hud.py` |
| `tests/test_layout_contract.py` | 31 (`_read`) | `hud.py`, `app.py`, `update_callbacks.py`, `style.css` |
| `tests/test_neural_net_layout.py` | 236 | `neural_flow.css` |

The `run.py` greps assert the literal strings `'name="direction_snapshot_prune"'`,
`'def _maintenance_loop'` and `'self._maintenance_loop()'` — they would pass on an empty loop body.

---

## 4. Rebuilding the C++ extension

The native kernel is optional. Without it the bot runs `PythonKernel` — same numbers, roughly 13x
slower on reads.

```
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release
```

Verified working in this tree; `cmake --build build` reports `no work to do`, meaning the shipped
`.pyd` matches current source. Toolchain on this machine: `C:\msys64\ucrt64\bin\{cmake,ninja,g++}`,
pybind11 3.1.0 located via `python -m pybind11 --cmakedir` (`CMakeLists.txt:39-52`).

**Where the output lands.** `CMakeLists.txt:149-152` sets `LIBRARY_OUTPUT_DIRECTORY` and
`RUNTIME_OUTPUT_DIRECTORY` to `${CMAKE_CURRENT_SOURCE_DIR}` — the **repo root**, not `build/`. That
is why `.gitignore` carries `*.pyd` and `build/`. So a local build drops an untracked binary next
to `run.py`; that is intended, not litter.

**Two DLLs must sit beside the `.pyd`** (both present in the root, both gitignored):

```
libwinpthread-1.dll                 64 702 B
api-ms-win-crt-private-l1-1-0.dll   75 184 B
```

`CMakeLists.txt:118-137` links `-static-libgcc -static-libstdc++ -Wl,-Bstatic -lwinpthread
-Wl,-Bdynamic` and the comment there claims only Windows/UCRT DLLs, `python3xx.dll` and `KERNEL32`
should remain. That claim is **wrong**: `std::mutex` (`cpp/include/microstructure_kernel.h:107`)
pulls `libwinpthread-1.dll` back in through `libstdc++.a`, and it in turn needs the private UCRT
DLL. A PE import-directory dump of the shipped binary yields 12 entries, including both.

**Two build rules are enforced by tests, not by convention:**

- `-O2`, never `-Ofast` / `-ffast-math`. Fast-math permits FP reordering, and
  `tests/test_cpp_kernel.py:324-341` (`test_cmake_avoids_fast_math`) fails the build if it appears.
  The effective flag line in `build/build.ninja` is
  `FLAGS = -O3 -DNDEBUG -std=c++20 -fvisibility=hidden -O2 -Wall -Wextra -fvisibility=hidden` —
  last-wins gives `-O2`.
- Fixed-size storage only. `tests/test_cpp_kernel.py:293-314` greps the header (comments stripped)
  for `std::array` present and `std::vector` absent.

**Kernel selection, once, before the socket opens:**

```
TRADEBOT_KERNEL unset/python  -> PythonKernel
TRADEBOT_KERNEL=cpp           -> RuntimeError if the module will not import (hard fail)
import failure otherwise      -> INFO log + PythonKernel (silent fallback)
```

`data/hyperliquid_feed.py:513` calls `_ensure_native_kernel()` exactly once, immediately before
`websockets.connect`, guarded by `self._native_kernel` (`:112`), and re-raises (`:119-120`). The
reason is stated at `:104-106`: two implementations' OFI entering one pipeline within seconds.

Verify a build:

```
python -c "import cpp_microstructure as m; print(m.__version__, m.MAX_LEVELS)"
# 1.0.0 32
```

Exported surface is **3** module-level symbols (`MAX_LEVELS`, `MicrostructureKernel`, `level_weight`)
plus 6 methods on the class; only `ingest_l2` and `order_flow_imbalance` are production-reachable
(there is no `__init__` export — the kernel is constructed via `CppMicrostructureKernel`,
`core/microstructure.py:276-279`). `ingest_json` — and with it the entire 347-line hand-written JSON scanner
in `cpp/include/simdjson.h` — has **zero** production callers. Live traffic parses with Python
`json.loads` at `data/hyperliquid_feed.py:421` and enters the kernel at `:458` via `ingest_l2`.

Parity is protected by 23 executed tests at tolerance `1e-7`
(`tests/test_cpp_kernel.py:37`, plus `tests/test_lifecycle_paths.py:474-558`).

---

## 5. Retraining and loading the ML model

The live inference path is `analysis/ml_signals.py`, **not** `ml/`. Only the trainer writes the
artifact.

### 5.1 Retrain

```
python -m ml.trainer
```

`ml/trainer.py:232-233` is `if __name__ == "__main__": asyncio.run(train_from_exchange())` — there
is **no argparse**. The module docstring at `:5` advertises
`python -m ml.trainer --symbol BTCUSDT --timeframe 1h --days 90`; those flags are ignored. To change
the parameters, edit the defaults of `train_from_exchange(symbol="BTC/USDT:USDT", timeframe="1h",
limit=1000)` at `:171-175`.

Data path: ccxt Binance Futures `fetch_ohlcv` under `asyncio.wait_for(..., timeout=4.0)`
(`:187-190`) → on failure, `yf.Ticker("BTC-USD").history(period="1y", interval="1h")` in a thread
(`:198-216`).

Hard floor: fewer than 100 valid samples returns `{"accuracy": 0, "error": "Data terlalu sedikit"}`
and writes **no** artifact (`ml/trainer.py:125-127`).

Two properties of the model you inherit:

- The split is **chronological**: `train_test_split(..., test_size=0.2, random_state=42,
  shuffle=False)` (`:130-132`). Correct for time series, wrong for a defensible accuracy number —
  it trains on the past and scores the future with no gap. `random_state` is inert under
  `shuffle=False`.
- The model is effectively **six**-feature. The trainer hardcodes
  `df["sentiment_score"] = 0.0` when absent (`:72-73`), so that column is constant and no tree can
  split on it.

### 5.2 Load at inference

`MLSignalGenerator.initialize()` (`analysis/ml_signals.py:43-50`) offloads `joblib.load` to the
default executor. A missing file raises `FileNotFoundError('Model belum dilatih')` inside
`_load_model` (`:56`), which `initialize` catches **at INFO level** (`:48-49`). A missing model
therefore degrades silently to the rule-based scorer. To see which path is live, check
`predict()["method"]` — `"ML"` or `"RULE_BASED"` (`analysis/ml_signals.py:151`).

### 5.3 The CWD trap

`MODEL_DIR = Path("ml/models")` is declared **twice, independently, CWD-relative**:

```
ml/trainer.py:18            MODEL_DIR = Path("ml/models")
analysis/ml_signals.py:20   MODEL_DIR = Path("ml/models")
```

Train from the wrong directory and you write a fresh model into a different tree. Run the bot from
the wrong directory and it silently loads nothing, logs one INFO line, and falls back to rules.

**Feature contract — position, not name.** Slot 1 is `macd_hist_norm` in the trainer
(`ml/trainer.py:113`) and `macd_hist` at inference (`analysis/ml_signals.py:38`). Seven wide on both
sides. The names differ; the position binds.

---

## 6. Debug and operator tools at the repo root

### 6.1 First-line tools

| Command | Purpose |
|---|---|
| `python run.py --repair-candles` | delete OHLC-invalid `candles` rows, refetch 600× 1m and 5m for **every** symbol present in the table |
| `python reset_paper_db.py` | `DELETE` trades/positions/agent_logs/signals, rewrite newest `account` row to 10 000 |
| `python live_doctor.py --testnet [--expect 0xabc…]` | read-only live preflight, exits non-zero on any failure |
| `python backfill_realized_pnl.py [--apply]` | recompute `positions.realized_pnl` net of both fees |
| `python test_live_session.py [seconds]` | run the whole bot headless for N seconds, print PnL summary |

**`--repair-candles`** (`run.py:786-844`) is destructive and reachable from the CLI with no
confirmation prompt. Its SQL predicate (`run.py:802-806`) deletes any candle with
`volume < 0 OR open <= 0 OR … OR high < low OR …`. It refuses to run against a live instance by
design, not by check — it simply closes the DB and price feed on exit (`run.py:843-844`).

**`reset_paper_db.py`** does **not** reset `sharpe_ratio`. Its UPDATE list is `balance,
initial_balance, total_pnl, total_trades, winning_trades, losing_trades, max_drawdown,
peak_balance, profit_factor` (`reset_paper_db.py:17-29`) — Sharpe is absent, so a stale value from
the previous session survives a "full reset". It also never empties `balance_history`; there is no
`DELETE FROM balance_history` anywhere in the repo.

**`live_doctor.py`** is the safest live tool because it cannot move money: no code path in it can
change an exchange position, cancel an order, or place a trigger (stated in its own docstring,
`:4-9`). Its seven stages are credentials → connection → balance → positions/orders → **gate must
refuse** → asset rules → rate limit. Stage 5 deliberately **pops** `TRADEBOT_LIVE` and
`TRADEBOT_LIVE_CONFIRMED` from a copied env (`:188-190`) and asserts the gate refuses with
`Blocker.LIVE_DISABLED`. Its closing message is precise: passing is enough for read-only testnet,
**not** enough for real orders.

**`backfill_realized_pnl.py`** dry-runs by default and requires `--apply` to write. It backs up to
`data_store/trading_bot.db.backup-<stamp>` first and writes in one transaction (`:96-104`). Its
docstring at `:27-28` states the invariant that matters: its `net_pnl` must stay identical to
`PositionManager.close_position`, or the script corrects the DB into disagreement with production.

**`test_live_session.py`** imports `TradingBotApp` from `run` directly and disables the dashboard
thread with `app.start_dashboard = lambda: None` (`:20`). It reads `account` by
`ORDER BY id DESC LIMIT 1` (`:51`), never `WHERE id = 1` — a trap the comment at `:46-49` explains.

### 6.2 Dashboard verification tools

These require a **running** dashboard at `http://127.0.0.1:8050`, except the two marked otherwise.
Three of them import `playwright`, which is **not** in `requirements.txt`; the other three are pure
static parsers.

| Script | What it measures | Needs live server |
|---|---|---|
| `verify_constellation.py` | label/marker bounding-box gaps from the real Plotly DOM | yes |
| `verify_graph.py` | edge count, edge length, **crossings**, node-through-node | yes |
| `inspect_dropdown.py` | real DOM + computed style of the symbol dropdown | yes |
| `verify_css_resolution.py` | resolves every CSS token, flags cyclic / undefined | no |
| `verify_dashboard_palette.py` | no `var()` in figure colour values; every figure renders | no |
| `verify_dashboard_render.py` | theme colours actually present in emitted HTML | no |

The browser-based ones exist because Python-side arithmetic disagreed with the browser in three
specific, documented ways (`data_store/measure_lib.py:10-19`): path `d` attributes are in pre-transform
user space; edges are keyed by internal key (`CORE_ENGINE`) while nodes display a label (`SIGNAL`);
and Plotly 6.9 puts `js-plotly-plot` on the div itself.

`verify_dashboard_palette.py` covers a real class of bug that survives a green suite: passing the
**string** `"palette.alpha(palette.BLUE, 0.10)"` — missing parentheses — as a colour value. The
browser silently drops it. That exact defect is present today at
`dashboard/layouts/performance.py:93,109,147` and `dashboard/layouts/price_chart.py:130,139,142,198`.

### 6.3 Static analysis and economy probes in `data_store/`

`data_store/` is not a data directory; it is also a scratch **and** measurement workspace.

| Script | Lines | Purpose |
|---|---|---|
| `measure_lib.py` | 408 | shared Playwright geometry harness, calibrated constants |
| `constellation_measure.py` | 374 | measures panel/plot size, label gaps, marker gaps, edge lengths |
| `count_crossings.py` | 251 | samples each rendered spline via `getPointAtLength` and tests all pairs |
| `measure_truth.py` | 248 | reads marker transforms, label boxes and path `d` from the real DOM |
| `constellation_search.py` | 216 | analytic coordinate search against thresholds (label ≥ 8 px, marker ≥ 6 px) |
| `analyze_edges.py` | 201 | edge geometry against the *production* callback's real `signal_map` |
| `apply_candidate.py` | 182 | patches a **copy** of `hud_figures.py` into `_snap/hf_cand.py`; raises on any failed patch |
| `constellation_design.py` | 179 | topology design + provenance comments per edge |
| `layered.py` | 134 | a candidate layout, and `layered.apply(hf)` that patches a live module — see §8.7 |
| `measure_panel.py` | 128 | reads the real panel and plot-area pixel dimensions |
| `calibrate_probe.py` | 97 | calibrates Plotly label offset and per-character width |
| `validate_model.py` | 86 | proves the analytic model matches what the browser draws |
| `measure_candidate.py` | 44 | runs the harness against the patched candidate |
| `measure_baseline.py` | 29 | runs the harness against the shipped code |
| `show_report.py` | 17 | pretty-prints a measurement JSON |

### 6.4 Root-level throwaway scripts

13 underscore-prefixed `.py` files sit at the repo root (25 `.py` files in total there, the rest
being `run.py`, the operator tools and the six `verify_*` scripts). They are gitignored in effect
for their `.log`/`.txt` output but the `.py` files themselves match no ignore rule. Their docstrings
state what they were for:

| Script | Question it answered |
|---|---|
| `_audit_refs.py` (113) | static check that every called cross-module name is actually defined |
| `_econ.py` (83), `_econ3.py` (82) | does the actual win rate × R:R actually make money? loss percentiles |
| `_smoke_console.py` (99) | drives the live console with injected keystrokes; proves no key sequence reaches live by accident |
| `_ctx_imports.py`, `_imports2.py`, `_extract_sigs.py`, `_lines.py` | AST import-graph / signature / line-count dumps |
| `_fixcjk.py` (55) | one-shot CJK character scrub in comments |
| `_t.py` (37) | run tests and write the result to a file |
| `_f.py`, `_parse_wf.py` | arithmetic reproductions of the cost model and defect probes |

None are in `requirements.txt`, none are in the suite, several import production code. **Do not
delete them blindly** — `_audit_refs.py` and `_smoke_console.py` are real safety gates; they simply
are not wired into CI.

### 6.5 Repository hygiene, measured

`.gitignore` correctly excludes: `*.log`, `data_store/*.db` and `-wal`/`-shm`, `data_store/logs/`,
`data_store/test_*.db`, `data_store/_*.{md,html,json,txt}`, `__pycache__/`, `build/`, `*.pyd`,
`libwinpthread-1.dll`, `api-ms-win-crt-private-l1-1-0.dll`, `.env`, `secrets.yaml`,
`config.local.yaml`, `data_store/backups/`.

Not covered: the 13 root `_*.py` scripts, the 26 `data_store/*.py` scripts (15 measurement tools +
11 `_`-prefixed), and the 16 module snapshots under `data_store/_snap/`, `_snap_cp/` and
`_snapshot/`.

---

## 7. Extension recipes

Each recipe lists the exact files touched, in order. The order is the dependency order — a later
step imports or reads what an earlier one defined.

### 7.1 Add an agent

Two shapes exist. Pick by whether the agent needs its own scheduler slot.

**A. A scheduled agent** (own lifecycle, own DB writes)

1. **`agents/<name>_agent.py`** — subclass `BaseAgent` (`agents/base_agent.py:20`). Implement
   `sense()`, `think()`, `act()`. Never let an exception escape `run_cycle`: the base class catches
   `Exception`, logs at ERROR **without a traceback** (`base_agent.py:112-114`), and writes an
   `ERROR` row. That is the shape you inherit, so raise your own logging level if you need a stack.
2. **`agents/<name>_agent.py` `__init__`** — call `super().__init__("<name>", event_bus)`; subscribe
   to channels in `initialize()` (`base_agent.py:38-42` gives you a shared `Repository` via
   `_get_repo`).
3. **`core/config.py`** — add an `AgentIntervals` field (`:37-48`) plus the dispatch in
   `load_config` (`:703-705`), and a matching key in `config.yaml` under `agent_intervals:`.
4. **`run.py` `TradingBotApp.__init__`** (`:239-260`) — declare `self.<name>_agent = None`.
5. **`run.py` `TradingBotApp.initialize`** — construct it (after `price_feed.start_streaming()` if
   it reads `market_store`, `:370`) and `await` its `initialize()` alongside the others
   (`:418-420`).
6. **`run.py` `setup_scheduler`** (`:520-618`) — `scheduler.add_agent_job(name=..., func=self.<name>_agent.run_cycle,
   interval_normal=..., interval_us_open=...)`. This gives you the US-market interval switching for
   free (`core/scheduler.py:52-84`).
7. **`run.py` `run()`** — none. Scheduled agents need no task.
8. **`tests/test_<name>.py`** — cover `sense/think/act` and any rejection path. Remember the class
   rules in §7.5.

**B. An ensemble specialist** (no scheduler; called in-process by the coordinator)

This is much cheaper and is the right default. The existing four live in
`agents/direction_agents.py:103-317`.

1. **`agents/direction_agents.py`** — subclass `DirectionAgent` (`:42`). Set `agent_name`, implement
   `_evaluate(symbol)` returning `(direction, confidence, reasoning, factors)` **or `None`**.
   `evaluate()` (`:62-85`) wraps it: `None` becomes `abstained=True`, exceptions become an abstain
   verdict. `None` is not an error — it is how a data-blind agent says so, and `aggregate` treats
   abstain and NEUTRAL as different things on purpose (`analysis/direction_ensemble.py:99-103`).
   Use `analysis.direction_ensemble.make_verdict` / `DIRECTION_LONG|SHORT|NEUTRAL`.
2. **`agents/direction_agents.py` `DirectionEnsembleAgent.initialize`** (`:418-433`) — add the
   `elif name == "<yours>"` branch. Read your inputs from `market_store`, and if the work is
   CPU-bound, subclass with an `evaluate_async` that does `await asyncio.to_thread(self.evaluate, symbol)`
   (pattern at `:200-213`) and add it to the `isinstance` dispatch in `_evaluate_symbol` (`:473-476`).
3. **`core/config.py`** — add `EnsembleAgentConfig` field to `EnsembleConfig` (`:264-275`), add the
   name to the tuple in `agent_configs()` (`:277-285`) and `base_weight()` (`:287-291`), and add a
   default weight in the constructor.
4. **`config.yaml`** — an `agents.<name>: {enabled, weight}` block. The loader applies it explicitly
   because `_apply_dict` would otherwise replace the dataclass with a raw dict
   (`core/config.py:659-679`).
5. **`tests/test_direction_agents.py`** — extend `TestAbstention` (`:34`) with your agent. Assert
   that a blind symbol yields `abstained=True`, not `NEUTRAL`.

**Constraints that will bite:**
- `MAX_AGENT_Z = 2.5` (`analysis/direction_ensemble.py:32`) caps every agent's z before pooling.
  Even a maximally confident single agent can only reach `prob_long = 0.8933` after
  `shrinkage_delta = 0.85`. Do not design a strategy that assumes your agent alone can be sure.
- An agent whose `base_weight()` is `0.0` is skipped in the pooling loop and flipped to
  `abstained=True` afterwards (`:136-146`), while `n_agents` still counts it — because `n_abstained`
  was computed before the loop at `:103`. That is deliberate but the two totals never disagree:
  `n_agents` is `len(breakdown)` built pre-loop at `:109-118`, so `n_agents + n_abstained` always
  equals `len(all_verdicts)` exactly. The zero-weight agent is silently counted as an ordinary
  *active* agent in `n_agents`, so it is invisible as an abstention. Verified by execution: one
  zero-weight verdict alone gives `n_agents=1, n_abstained=0`; two give `2, 0`.
- The all-abstain early return (`:121-131`) **omits the `z_shrunk` key entirely**. Any caller doing
  `result['z_shrunk']` unconditionally raises `KeyError` on a fully blind cycle.
- Never import `run.py` from a test. The only run.py assertions in the suite are string greps.

### 7.2 Add an indicator

1. **`core/config.py`** — add the field to `IndicatorConfig` (`:59-67`) if it should be tunable, plus
   the `load_config` dispatch for its block (`:711-713`). Only 8 of 14 technical indicator
   parameters are currently config-reachable; the six scalping lengths (`rsi_fast` 7, `ema_3`,
   `ema_5`, `vol_sma` 20, `atr` 14, `roc_5`) are hardcoded.
2. **`analysis/technical.py` `calculate_indicators`** (`:41-92`) — compute the column. Note the
   guard at `:51-53`: **below `macd_slow + macd_signal` (35 with the yaml values) the function
   returns the input frame unmodified** with no indicator columns at all, only a warning. If your
   indicator needs more data than that, the early-return will silently hide it.
   `calculate_indicators` **mutates the caller's DataFrame in place**.
3. **`analysis/technical.py` `generate_signals`** (`:94-299`) — score it. Increment
   `total_signals`, add to `bullish_count`/`bearish_count`, and record a
   `signals["<name>"] = {"value": ..., "signal": ...}` entry. Direction flips only when
   `abs(net) > 0.5` (`:277-285`) and `confidence = min(abs(net)/max(total_signals * 1.5, 1), 1.0)`.
   **You cannot reach confidence 1.0**: the seven scored blocks sum to at most 8.5 while
   `total_signals` reaches 8, so the ceiling is `8.5/12 = 0.7083`.
4. **`analysis/technical.py`** — if a downstream consumer needs the raw value, extend the dict
   literal `TechnicalAgent._evaluate` assembles (`agents/direction_agents.py:230-233`) — it
   enumerates exactly ten keys.
5. **`config.yaml`** — add the key under `indicators:` and document the unit. A wrong type triggers a
   warning and is still applied (`core/config.py:449-483`).
6. **`tests/test_indicators.py`** — extend `TestTechnicalIndicators` (`:12`); it already seeds
   `np.random(42)`. Add a case for the short-frame path so the `:51-53` early return stays covered.

**If it feeds the direction ensemble** rather than the technical analysis path, the work goes into
`analysis/probability_engine.py:calculate_technical_zscore` (`:93-136`) instead. That function
**clips every sub-z to ±2.0 and returns the UNWEIGHTED mean**, so a missing input degrades the score
silently rather than being excluded.

### 7.3 Add a dashboard panel

The order is enforced by `tests/test_layout_contract.py`, which mounts the real layout at runtime and
fails on any unmounted callback id (`:78-118`). A missing id does not raise in Dash — the panel
silently stops updating.

1. **`dashboard/layouts/hud_figures.py`** — add a factory if the panel has a chart. Signature
   convention: `create_<name>_fig(<data>, ...) -> go.Figure`. Every figure must set
   `template="none"` and `paper_bgcolor = plot_bgcolor = PARCHMENT_BG`, and must contain **zero**
   `var(--…)` strings. Plotly draws to canvas and cannot resolve a CSS reference; the string is
   accepted silently and paints nothing. Import colours from `dashboard/layouts/palette.py`, which
   exists solely to convert `:root` tokens to hex at import (`palette.py:29-105`).
   **Panel height is CSS-owned.** Five tokens cross into Python: `--h-chart` (179 px),
   `--h-conv` (92 px), `--h-neural` (230 px), `--h-spark` (34 px), `--h-equity` (230 px)
   (`style.css:86-94`). There are zero `height=` assignments in `hud_figures.py`.
2. **`dashboard/layouts/hud.py`** — add a `create_<name>_panel()` returning
   `html.Div([header, html.Div(id="hud-<name>-<slot>", className="empty-note")], className="hud-panel span-N zone3-cell")`.
   Give the output div a stable `id` — the runtime walker reads `node.id`, not source text
   (`tests/test_layout_contract.py:34-66`), so an id passed through a helper argument still counts.
   Place the call inside one of the four zones of `create_hud_layout` (`:20-58`).
3. **`dashboard/assets/style.css`** — add a height token inside `:root` only. The stylesheet has 59
   distinct `:root` custom properties (`style.css:14-97`) and exactly **one** `@keyframes`
   (`hud-pulse-live`, `:787-790`); `neural_flow.css` adds two more. `tests/test_layout_contract.py`
   enforces ≤ 4 keyframes, no hex outside `:root`, no cyclic `var()`, and no `will-change`. A `var()`
   that points at itself makes the browser drop the declaration with no console error.
4. **`dashboard/callbacks/update_callbacks.py`** — add a callback inside `register_callbacks`
   (`:114`). Open a connection with `_get_sync_db()` (`:64-69`), which creates a **fresh blocking
   `sqlite3.connect` per call** and you close it yourself in a `finally`. Bind to
   `Input("dashboard-interval", "n_intervals")` unless the panel genuinely needs 500 ms — a query on
   that tick runs twice a second forever.
5. **`dashboard/callbacks/update_callbacks.py`** — every `N/A`, never `0.00`, for anything you cannot
   prove. That is a documented convention of the HUD, and it is the honest behaviour.
6. **`tests/test_layout_contract.py`** — nothing to add if ids are mounted. If the panel needs its
   own source-text guard, add it.
7. **`tests/test_layout_budget.py`** — add the pixel arithmetic if the panel is in a height-constrained
   zone. `test_zone3_cells_fit_their_graphs` (`:59`) already checks the cells you would land in.

**Do not write to the database from a callback.** Every existing callback is read-only.

### 7.4 Add a risk rule

Decide first which layer owns it, because the two layers are not interchangeable.

| Layer | Applies to | Enforced by |
|---|---|---|
| `trading/risk_manager.py` | **paper only** | `validate_trade` (`:273-338`), called from `paper_engine.py:665` |
| `trading/live/safety.py` | **live only** | `SafetyGate.order_blockers` (`:300-346`), reached from `engine.py:236` through `SafetyGate.can_send` (which calls it at `safety.py:366`) |

Neither covers the other. Live has **no** `validate_trade` caller, so `max_open_positions`,
`max_drawdown` and the percentage-of-initial-balance daily breaker are paper-only today. Paper has
no `SafetyGate`, so the absolute notional caps and the UTC window are live-only. A "risk rule" added
to one layer does not exist in the other mode.

**Paper rule**

1. **`core/config.py`** — field on `RiskConfig` (`:20-27).
2. **`trading/risk_manager.py` `validate_trade`** — append to `reasons`. Use the existing style:
   append a formatted string, return `{"allowed": len(reasons) == 0, "reasons": reasons}`.
   **Collect every reason, never return early.** `tests/test_lifecycle_paths.py:828`
   (`test_all_reasons_collected_not_just_first`, `assertGreaterEqual(..., 3)` at `:835`) requires ≥ 3
   reasons so a future "only the first reason" optimisation fails rather than quietly degrading the
   operator-facing text.
3. **`tests/test_risk_manager.py`** — pure-formula test. Read the threshold from config, never from a
   frozen literal: hardcoding `0.05` produces a test that stays green while the limit changes.
4. **If it changes a quantity** — `agents/decision_agent.py` now owns the sizing maths
   (`calculate_position_size` at `:391`, `calculate_scalp_position_size` at `:376`, assigned to
   `order.quantity` at `:432`). `paper_engine.py:616-634` still recomputes it, but only in its
   `order.quantity is None` branch, which no production caller reaches. See §7.5.

**Live rule**

1. **`core/config.py`** — field on `LiveConfig` (`:291-360). Remember `run.py:291` constructs a bare
   `LiveConfig()`, so the gate runs on **dataclass defaults**, not on `config.yaml` and not on
   anything `console.edit_rules` mutated in memory.
2. **`trading/live/safety.py`** — append to `order_blockers` (per-order) or `master_blockers`
   (per-session). Add a `Blocker` enum member with a human-readable Indonesian string
   (`:30-49`); `can_send` returns `(allowed, blockers)`, never a bare bool, so the reason always
   travels with the refusal.
3. **`trading/live/console.py` `EDITABLE`** (`:491-502`) — if the operator should be able to change
   it at runtime, add `(field, label, kind, lo, hi)`. `validate_rule` (`:505-529`) rejects NaN and
   infinity **explicitly**, because every comparison against NaN is False and a NaN limit looks like
   protection while never firing. The editor mutates the in-memory `LiveConfig`; it never writes
   `config.yaml` — deliberate, so who changed a limit is always traceable in git.
4. **`trading/live/safety.py` `_persist`** — remember every `record_*` method writes
   `data_store/live_counters.json` through a temp-file rename. A rule that needs cross-restart state
   belongs in `DayCounters`, and adding one changes the on-disk shape of a file that a corrupt read
   currently turns into a permanent block (`safety.py:103-125, 261-262`).
5. **`tests/test_live_safety.py`** — 45 cases exist, all proving the gate **refuses**. Add yours the
   same way. `tests/test_live_console.py` covers the editor path.

### 7.5 Add a test

Four rules the suite follows, none enforced by tooling:

1. **No duplicate class names within a module.** `unittest discover` silently binds the later class
   and drops the earlier one. The former `TestFeeAccounting` duplicate in `test_lifecycle_paths.py`
   was found and renamed; nothing prevents a recurrence.
2. **No duplicate method names within a class.** `tests/test_neural_net_layout.py:240` and `:259`
   both define `test_axes_hhave_no_scaleanchor` — the first is dead code. 27 defs, 26 collected.
3. **Compute fees from the model, not from a frozen literal.** `tests/test_lifecycle_paths.py:310-325`
   documents this after a `-9.0` expectation had to be removed. Same discipline at
   `test_position_manager.py:145-165` for liquidation.
4. **Restore any module-level singleton you mutate** in `tearDown` or `finally` — `tui._write`,
   `console._read`, `console.interactive`, `tui.KeyReader`, `tui.is_tty`, `get_config()`. Pattern at
   `tests/test_live_tui.py:608-626` and `tests/test_direction_agents.py:254-263`.

Do not loosen your own assertion floor. `test_all_reasons_collected_not_just_first`
(`test_lifecycle_paths.py:828`, with the `assertGreaterEqual(..., 3)` at `:835`) is the pattern: assert
a minimum count so a future optimisation that truncates the reason list fails the build.

---

## 8. Sharp edges

The things that will actually bite, ordered by cost.

### 8.1 Fill cost is single-source — do not re-derive it

`trading/fill_cost.py` (216 lines) is the **only** implementation of crossing cost. Two
production importers: `trading/paper_engine.py:21` and `trading/position_manager.py:21`.

Constants (`fill_cost.py:45-48`), verbatim:

```python
FILL_HALF_SPREAD_FLOOR          = 0.0003   # 3 bps
FILL_IMPACT_FLOOR               = 0.0001   # 1 bps
FILL_BOOK_MALFORMED_SPREAD_PCT  = 0.05     # 5%
FILL_MAX_TOTAL_COST_PCT         = 0.0050   # 50 bps, jaring pengaman
```

The formula (`fill_cost.py:141-163`):

```python
half   = max(observed_half_spread, FILL_HALF_SPREAD_FLOOR)   # max, NEVER min
impact = FILL_IMPACT_FLOOR                                     # constant, never reads the book
total  = min(half + impact, FILL_MAX_TOTAL_COST_PCT)
signed = +total if fill_side == "BUY" else -total
fill   = ref_price * (1.0 + signed)
```

Three things about it are non-obvious and all three are deliberate:

- The numbers are **floors, not estimates**. `market_store` never expires the order book and
  `get_order_book_age` returns `None` for an unstamped book, so yesterday's book still reads
  "valid". `max()` is the only safe operator; `min()` would return a near-zero cost from exactly
  the narrow, stale, rengang-time books that are most untrustworthy.
- An **unstamped book is stale, not fresh**: `age is None` → `return None` (`fill_cost.py:76`).
  An absent L2 snapshot falls to the floor, never to zero.
- **Impact is a hard constant.** Documented at `:28-33`: a depth-proportional rate needs a division
  whose zero case is a live crash, and `calculate_scalp_position_size` sizes from balance, not
  depth. The consequence — very large orders are under-charged — is a known limitation, stated, not
  hidden.

**`trading/paper_engine.py` re-declares all four constants locally at `:51-54`, shadowing the
identical imports it just took at `:21-26`.** An AST load/store analysis shows the four names are
STORE-only in the body — never loaded. The values agree today, so there is no live bug, but the two
copies can drift silently. If you change a floor in `fill_cost.py`, change it there too, or delete
the shadowing block.

Also dead: `fill_cost.describe_cost` (`fill_cost.py:207`) has zero callers — imported at
`position_manager.py:21` and never called. (The `:207` shorthand in this section resolves to
`position_manager.py:207`, which is the `pos["id"]` loop, not the function.)

### 8.2 Never pre-adjust a price before `close_position`

`PositionManager.close_position(position_id, close_price, reason, price_is_final=False)`
(`position_manager.py:167-173`) charges the exit crossing **itself** at `:216` unless
`price_is_final=True`. Every close path — SL hit, TP hit, liquidation, scalp TP, expired, order
`CLOSE` — funnels into this one function, so none of them can forget the cost.

`price_is_final=True` exists so a caller that already paid its own cost can opt out. It has
**zero production callers** (grep across the tree: only the definition at `:172`, its docstring at
`:178`, and the branch at `:213`). The live path would be the natural user — it books the
exchange's real `avg_price` — but `LiveExecutor._record_close` does not use it either, because live
never calls `PositionManager.close_position` at all: `_LivePositionManager` (`executor.py:912`) is a
separate class, not a subclass.

Pre-adjusting a price before calling `close_position` charges the crossing twice. The removed
double-charge is documented at `paper_engine.py:830-840`.

### 8.3 The duplicated SL/TP literals

Two files carry a literal copy of the non-scalping stop/target pair:

```
trading/paper_engine.py:548-549    sl_pct = 0.02
                                 tp_pct = 0.04

agents/decision_agent.py:37-38    _NON_SCALP_SL_PCT = 0.02
                                 _NON_SCALP_TP_PCT = 0.04
```

`decision_agent.py:26-36` states the rule: these must **not** be swapped for the config
`stop_loss_pct`. Sizing from a 0.25% scalp stop while the engine installs a 2% stop inflates notional
8x and makes the real risk far larger than the risk manager approved. Both sides must be edited
together.

The scalping path does not have this problem: both sides call
`RiskManager.calculate_scalp_position_size` (`risk_manager.py:340-378`), so the numbers agree in the
default configuration.

A second, less obvious duplication: `trading/paper_engine.py:374-386` re-imposes floors **on top of**
`analysis/volatility.py:330-332` — `sl_pct = max(dynamic_sl, tight_sl_pct)`, then
`tp_pct = max(tp_pct, sl_pct * min_risk_reward)` a second time. The volatility module's `min_sl_pct`
and `min_profit_pct` are belt-and-braces.

### 8.4 The undocumented 0.45 drift coefficient

`analysis/probability_engine.py:299`, in the live `compute_directional_curve`:

```python
drift_per_min = z_score * 0.45 * sigma
```

`0.45` appears in exactly two places — `:299` (live) and `:353` (the deprecated
`compute_diffusion_curve` shim) — and is **documented nowhere**: not in the module docstring, not in
`config.yaml`, not in any dataclass. It is a bare magic number that converts a log-odds direction
score into a per-minute drift.

Its effect is small and that is the trap. Sigma does not enter only through `-0.5 * sigma**2` at
`:307` — it is already a factor of `drift_per_min` at `:299`, and `:307` divides the whole numerator
by `sigma * sqrt(t)`, so the `-0.5 * sigma**2` piece contributes `-0.5 * sigma * sqrt(t)` to `d2`:
it grows **linearly** with sigma, from -0.00082 at the 0.0003 floor to -0.0493 at 0.018, against a
drift term of +1.53 (`0.45 * z * sqrt(t)` at `prob_long=0.65`). The 60x sweep is still small: the
30-minute endpoint moves 0.9104 -> 0.9047, about 0.006. **The curve is directionally sensitive and essentially
volatility-insensitive.** If you are looking for volatility to visibly reshape this curve, it will
not.

The deprecated sibling uses `abs(drift)` and clips to `[0.50, 0.999]` (`:360`), which is exactly why
it can never display a SHORT. `tests/test_probability_engine.py:139` is the regression test for
that.

### 8.5 The logger that swallows tracebacks

`config.yaml:127` sets `logging.level: "INFO"`. `core/logger.py:164` applies it to the
`trading_bot` parent logger, and **both handlers sit at `NOTSET`**, so the parent level is the only
gate. Verified at runtime:

```
after setup_logger(): level=INFO, isEnabledFor(DEBUG)=False
```

Now follow what that costs. The pattern `logger.error(f"...: {e}")` followed by
`logger.debug(traceback.format_exc())` appears in the highest-traffic error paths:

| Site | What you lose |
|---|---|
| `agents/base_agent.py:112-114` | every agent cycle exception — `str(e)` only, no frames |
| `agents/direction_agents.py:69-72, 211-213` | every specialist failure becomes an `abstained` verdict |
| `agents/analysis_agent.py:219-220, 259-260` | macro update and FinBERT batch failures |
| `run.py:782-783` | the entire telemetry loop body is `except Exception: pass` |
| `dashboard/callbacks/update_callbacks.py:1427-1429` | `logger.debug(...)` then `raise PreventUpdate` |

An exception with an empty message — `KeyError`, `StopIteration`, bare `RuntimeError()` — produces a
log line containing nothing but the agent name and cycle id.

`trading/live/executor.py` shows the fix for the formatting half of the problem: it uses
`logger.warning("...: %s", exc)` / `logger.error("...: %s", exc)` lazily throughout. But it is **not**
a counter-example for the traceback half — it contains no `logger.exception` and no `exc_info=True`
anywhere. `_record_close`'s handler (`executor.py:882`) is a plain
`logger.error("Gagal mencatat penutupan live posisi #%s %s: %s", row_id, symbol, exc)`, so the frames
are still lost there. Repo-wide, only `run.py:677`, `run.py:695` and `trading/live/engine.py:651` ever
pass a traceback.

To debug any of the above, run the process with the level raised. `core/logger.py` reads
`cfg.level` only at `setup_logger()`, so either edit `config.yaml` and restart, or set the level
programmatically before `setup_logger()` runs.

### 8.6 Live and paper share the same tables

There is **no mode or venue column anywhere in the schema**. `database/db.py` defines 10 tables and
8 indexes and contains no `mode` column. Paper and live both write `positions` and `trades` through
the same `Repository`.

Consequences, all verified from source:

- `account.balance` is **paper-only**. Its single writer is
  `Repository.apply_balance_delta` (`repository.py:403-425`), reached from exactly four call sites,
  all in `trading/position_manager.py` (`:102, :105, :128, :298`). No live path calls it, and there is
  a comment at `trading/live/executor.py:26-30` saying exactly that.
- The dashboard computes `wallet_balance = account.balance + SUM(margin)` over **all** open rows
  (`update_callbacks.py:672`, identically at `:140` and `:188`). In live mode that is a stale paper
  number plus live margins.
- `LiveExecutor._warn_shared_ledger` (`executor.py:162-183`) prints this warning **once**, at the
  first live fill. If you miss it, nothing else says it again.
- `max_drawdown` and `sharpe_ratio` are written only on the paper path
  (`position_manager.py:325-343`). `LiveExecutor._record_close` calls `update_account_stats`
  **without** those two arguments (`executor.py:831-839`), so in live mode both KPI tiles render
  `N/A` forever. **This corrects CONTEXT.md**, which claims both are never written at all — they are
  now written, but only for paper.
- The one live marker in the data is a `"LIVE "` prefix written into `positions.reasoning`
  (`executor.py:550`). **No dashboard callback reads that column.** The top-bar badge
  `PAPER TRADING` is a hardcoded literal (`dashboard/layouts/hud.py:68`) and lies in live mode.
- The `balance_snapshot` scheduler job is bound unconditionally to the **paper** position manager
  (`run.py:614-618`) — no mode branch. So `balance_history`, and therefore MDD and Sharpe, are
  computed from paper equity even in a live session.

### 8.7 `data_store/layered.py` mutates the live dashboard module

`layered.apply(hf)` (`data_store/layered.py:103-114`) takes a module and rebinds module-level
attributes on it in place:

```python
hf.NEURAL_SYSTEM_NODES    = dict(nodes)
hf.NEURAL_SYSTEM_EDGES    = list(edges)
hf.TOKEN_SLOTS            = list(slots)
hf.TOKEN_ANCHORS          = hf.TOKEN_SLOTS
hf.HUB                    = HUB
hf.BUS_RISE               = BUS_RISE
hf._TOKEN_SLOT_REGISTRY   = _sequential_registry(hf)
hf._X_RANGE, hf._Y_RANGE  = xr, yr
```

Those are the exact globals `dashboard/layouts/hud_figures.py` reads at render time (`:531`,
`:557-575`, `:601`). Verified by execution:

```
import data_store.layered            → live module unchanged (13 nodes, 12 edges, 12 slots)
data_store.layered.apply(hf)        → (10 nodes, 9 edges, 12 slots, HUB='EXECUTION_AGENT', BUS_RISE=0.06)
```

Import alone is inert; **calling `apply()` is not**. There is no caller of `layered.apply` anywhere
in the repo today — grep finds only the definition — so this is latent. But the sibling
`data_store/apply_candidate.py` is the **safe** pattern and the reason to prefer it: it reads
`hud_figures.py` as text, applies regex patches that **raise `AssertionError` if a patch does not
stick** (`apply_candidate.py:122-126`), and writes the result to `data_store/_snap/hf_cand.py`. The
production file is never touched. Its own docstring at `:8-10` records the failure mode: "that's how a
previous *fix* shipped without changing a single line."

**Never pass the live `hud_figures` module to a scratch script.** Use `apply_candidate.build_module()`.

### 8.8 The batch-close audit log raises when it matters most

`trading/paper_engine.py:927` sets `fill_meta = None` and only assigns it inside `if result:` at
`:934-936`. At `:965` the audit log dereferences `fill_meta["ref_price"]` unconditionally.

Reproduced: force every `close_position` in the batch loop to return `None` (which happens whenever
another path wins the claim race — the exact race documented at `:938-944`) and you get
`TypeError: 'NoneType' object is not subscriptable`. The log whose entire purpose is to record the
cost breakdown of a partially-failed batch close instead throws, and the `'GAGAL:'` report built at
`:990-994` is never returned.

### 8.9 The live poll loop outlives shutdown

`run.py:321-322` assigns `self._live_task`. `run.py:894-896` extends
`self._background_tasks` with exactly five tasks — price, execution, candle, telemetry,
maintenance. The live task is not among them, and `shutdown()` iterates only that list
(`run.py:924-925`). After Ctrl+C, `LiveEngine.run_loop` keeps polling `health_check` and
`check_pending_fills` until the process exits. Bounded severity, because `run_loop` cannot send
orders (`on_decision` is a placeholder returning `None`, `run.py:317-319`) — but it can still attach
exchange trigger orders for late fills.

### 8.10 Dead code that looks alive

Grep-verified, zero production callers (row `RiskManager.validate_trade` needs its note corrected —
`paper_engine.py:665` is a production call site, so the note holds only for live mode;
`paper_engine.py:459` mentions it in a comment, and `tests/*.py` account for the other 30 hits):

| Symbol | Defined | Note |
|---|---|---|
| `LiveEngine.reconcile` | `engine.py:74` | only caller is `emergency_flat` |
| `LiveEngine.emergency_flat` | `engine.py:655` | only callers are 5 tests — no console command, no signal handler |
| `LiveExchange.cancel` | `client.py:462` | zero callers repo-wide |
| `LiveExchange.cancel_all` | `client.py:483` | only `emergency_flat` |
| `RiskManager.validate_trade` | `risk_manager.py:273` | no live caller → no live max-positions / drawdown cap |
| `analysis.vol_target` | whole module (132 lines) | zero importers, including tests |
| `ml/predictor.py` | whole module (44 lines) | zero importers |
| `analysis/backtester.py` | whole module (980 lines) | tests only |
| `derive_macro_bias` | `update_callbacks.py:72` | zero callers |
| `TREE_STAGES` | `hud.py:280` | zero readers |
| `DashboardConfig.update_interval` | `config.py:75` | never read |
| `LiveConfig.testnet` / `.use_exchange_side_tpsl` / `.reconciliation_tolerance_days` | `config.py:313,349,358` | never read |
| `Blocker.RECONCILIATION_FAILED` | `safety.py:45` | never appended — a reconcile mismatch surfaces as `KILL_SWITCH` and the real cause is hidden |

Two kill-switch raisers are consequently unreachable: `engine.py:128` (inside `reconcile`) and
`engine.py:737` (inside `emergency_flat`). Five of seven are live: `engine.py:404, 555, 599`,
`executor.py:573, 587`.

### 8.11 Two more live-path gaps worth knowing before you touch anything

- **A TP attach failure is silent.** `engine.py:394-404` reads `sl = results.get("sl")` and guards
  only on `sl`; `tp` is never tested. The `LivePosition` is built with
  `tp_order_id=None` and `submit_order` returns `"protected": position.sl_order_id is not None`
  (`:347`) — i.e. `True`. A position with a working stop and **no target** reports itself protected.
- **A late GTC fill gets a hardcoded ±1% stop**, four times wider than the configured 0.25%
  (`engine.py:484-486`), at `max_leverage` rather than the intended leverage (`:501`).
- **`TRADEBOT_LIVE_KILL_SWITCH` is inverted from intuition** (`safety.py:277-280`). Unset → `""` →
  does **not** trip. Only a value outside `("", "0", "false", "no")` trips it. The release message at
  `safety.py:406` names `TRADEBOT_LIVE_KILL_SWITCH=0`, which is the *release* value, not the trip
  value — a reader can invert the semantics from that line alone. Also, `SafetyGate.__init__` takes a
  **snapshot copy** of `os.environ` (`safety.py:171`), so setting the variable at runtime does not
  reach an already-built gate.
- **Tripping the kill switch disables the late-fill protection installer.** `health_check`
  short-circuits on `gate.engaged` and returns `ok=False` (`engine.py:540-542`), and `run_loop`
  `continue`s before `check_pending_fills` (`:629-636`). Since `engine.py:404` engages the switch
  precisely when an SL fails to attach, the moment protection is most needed is the moment its
  installer is switched off.

---

## Not verified

- **Wall-clock duration of the bot run itself.** Only the test suite and the CMake build were
  executed in this pass. No `python run.py` session was started, so no claim here rests on
  observing a live boot.
- **Native rebuild from a clean `build/`.** `cmake -S . -B build` was re-run and `cmake --build`
  reported `no work to do`; the shipped `.pyd` was **not** recompiled from scratch. The claim that it
  matches current source rests on that no-op plus the successful `import cpp_microstructure`, not on
  a full rebuild.
- **The `C++` parity numbers at `1e-7`.** The parity tests ran and passed as part of the 569, but the
  independent 300-book fuzz sweep behind that tolerance was not re-run here.
- **`playwright` browser measurements.** All six dashboard `verify_*` scripts were read but none
  was executed; they require a running dashboard on `127.0.0.1:8050` and a Chromium download.
- **Runtime behaviour on POSIX.** Everything here was observed on Windows 11 / CPython 3.14. The
  four `TestKeyParsing` skips and the `msvcrt`-vs-`select` key split
  (`trading/live/tui.py:191,213`) mean the TUI paths differ by platform and were only verified on one.
- **The economic figures attributed to §8.1** (17.0 bps round trip, realized SL 152% of the announced
  stop, R:R collapsing to ~1:1.24) appear nowhere in §8.1 (lines 715-760), which quotes only the
  four constants and the formula. Any such figures must be re-derived before they are cited.
