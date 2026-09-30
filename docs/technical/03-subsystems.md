# Subsystem Reference

This is the per-subsystem reference for the trading bot: one subsection per source file, grouped into the nine subsystems below, ordered so that each group only depends on groups that appear earlier. Every group is a slice of the runtime, not a layer of the architecture — the live and paper engines sit side by side rather than one above the other, and `data/ui` is deliberately last because the dashboard reads from every other group.

Each file subsection has the same shape: a **Role** line stating what the module is responsible for, a **Symbols** table, prose covering the behaviour that is load-bearing or surprising, then **State**, **Side effects** and **Consumers** so the blast radius of a change is visible without opening the file. Every signature in every Symbols table is copied verbatim from source and carries a `file:line` reference, so a reader can jump straight to the definition and confirm the signature has not drifted. Prose that names a line number means the same file and line.

The two tables at the end were computed by parsing the real `import` statements out of source with `ast` — they are not transcribed from anywhere. The first lists every first-party import edge that crosses a group boundary, including function-local imports. The second summarises each group's size and its neighbours; `Files` counts documented files, and the per-package `__init__.py` markers are counted in `Lines` but not in `Files`, since they hold no subsystem. Neither table includes `run.py`, the tests, or the root-level helper scripts, which are outside these nine groups — `run.py` has its own section above the tables because it is the composition root and belongs to no single group.

## Configuration

### 1. `core/config.py`

**Role.** Loads `config.yaml` into a tree of frozen-in-shape dataclasses, applies the tree to the process-wide singleton, and refuses to boot on three classes of economically impossible configuration. It is the root of the first-party import graph: nothing in the repo imports it *before* it, and 25 first-party modules import from it.

**Symbols.**

| Symbol | Kind | Signature | Loc |
| --- | --- | --- | --- |
| `AccountConfig` | dataclass | `class AccountConfig:` — `initial_balance: float = 10000.0`, `currency: str = "USDT"` | `core/config.py:15` |
| `RiskConfig` | dataclass | `class RiskConfig:` — `max_risk_per_trade: float = 0.02`, `max_leverage: int = 20`, `max_daily_loss: float = 0.05`, `max_drawdown: float = 0.15`, `max_open_positions: int = 3`, `default_leverage: int = 5` | `core/config.py:21` |
| `FeeConfig` | dataclass | `class FeeConfig:` — `maker: float = 0.0002`, `taker: float = 0.0005` | `core/config.py:31` |
| `AgentIntervals` | dataclass | `class AgentIntervals:` — `news_agent: int = 300`, `news_agent_us_open: int = 120`, `analysis_agent: int = 300`, `analysis_agent_us_open: int = 180`, `decision_agent: int = 60`, `decision_agent_us_open: int = 30`, `finbert_batch: int = 900`, `macro_data: int = 21600`, `funding_rate: int = 28800`, `balance_snapshot: int = 300` | `core/config.py:37` |
| `USMarketConfig` | dataclass | `class USMarketConfig:` — `timezone: str = "US/Eastern"`, `open_hour: int = 9`, `open_minute: int = 30`, `close_hour: int = 16` | `core/config.py:51` |
| `IndicatorConfig` | dataclass | `class IndicatorConfig:` — `rsi_period: int = 14`, `macd_fast: int = 12`, `macd_slow: int = 26`, `macd_signal: int = 9`, `bollinger_period: int = 20`, `bollinger_std: int = 2`, `ema_short: int = 9`, `ema_long: int = 21` | `core/config.py:59` |
| `DashboardConfig` | dataclass | `class DashboardConfig:` — `host: str = "127.0.0.1"`, `port: int = 8050`, `debug: bool = False`, `update_interval: int = 2000` | `core/config.py:71` |
| `LoggingConfig` | dataclass | `class LoggingConfig:` — `level: str = "INFO"`, `file: str = "data_store/logs/trading_bot.log"`, `max_bytes: int = 10485760`, `backup_count: int = 5` | `core/config.py:79` |
| `ExchangeConfig` | dataclass | `class ExchangeConfig:` — `name: str = "binance"`, `type: str = "future"`, `sandbox: bool = True` | `core/config.py:87` |
| `ScanningConfig` | dataclass | `class ScanningConfig:` — `dynamic_top_volume: bool = True`, `top_n: int = 10`, `refresh_interval: int = 3600` | `core/config.py:94` |
| `ScalpingConfig` | dataclass | `class ScalpingConfig:` — `enabled: bool = True`, `min_profit_pct: float = 0.0060`, `max_hold_seconds: int = 300`, `min_hold_seconds: int = 15`, `batch_size: int = 2`, `fast_tp_pct: float = 0.0060`, `tight_sl_pct: float = 0.0025`, `min_confidence: float = 0.40`, `reversal_close_threshold: float = 0.70`, `max_tick_age_seconds: float = 1.5`, `stale_tick_window_seconds: float = 3.0`, `stale_tick_min_samples: int = 5`, `orderbook_imbalance_threshold: float = 0.60`, `momentum_threshold: float = 0.0008`, `max_spread_pct: float = 0.0006`, `breakeven_trigger_pct: float = 0.0020`, `breakeven_offset_pct: float = 0.0015`, `cooldown_after_close_seconds: float = 20.0`, `cooldown_after_loss_seconds: float = 90.0` | `core/config.py:101` |
| `DynamicTpSlConfig` | dataclass | `class DynamicTpSlConfig:` — `enabled: bool = True`, `atr_multiple: float = 1.5`, `atr_period: int = 14`, `min_sl_pct: float = 0.0025`, `max_sl_pct: float = 0.0150`, `min_risk_reward: float = 1.5`, `realized_window_seconds: float = 30.0`, `max_breakeven_win_rate: float = 0.65`, then a scalping-economics mirror block: `fast_tp_pct: float = 0.0060`, `tight_sl_pct: float = 0.0025`, `min_confidence: float = 0.40`, `orderbook_imbalance_threshold: float = 0.60`, `momentum_threshold: float = 0.0008`, `max_spread_pct: float = 0.0006`, `breakeven_trigger_pct: float = 0.0020`, `breakeven_offset_pct: float = 0.0015`, `cooldown_after_close_seconds: float = 20.0`, `cooldown_after_loss_seconds: float = 90.0` | `core/config.py:161` |
| `EnsembleAgentConfig` | dataclass | `class EnsembleAgentConfig:` — `enabled: bool = True`, `weight: float = 0.25` | `core/config.py:221` |
| `EnsembleConfig` | dataclass | `class EnsembleConfig:` — `enabled: bool = True`, `interval_seconds: int = 5`, `shrinkage_delta: float = 0.85`, `agreement_bonus: float = 0.5`, `min_prob: float = 0.02`, `max_snapshot_age_seconds: int = 20`, `diffusion_horizon_minutes: int = 30`, `diffusion_points: int = 60`, `momentum_window_seconds: float = 30.0`, `min_trades_for_microstructure: int = 8`, `min_returns_for_vol: int = 20`, and four `field(default_factory=...)` sub-dataclasses: `orderflow` (weight 0.30), `momentum` (0.25), `technical` (0.25), `microstructure` (0.20) | `core/config.py:229` |
| `EnsembleConfig.agent_configs` | method | `def agent_configs(self) -> dict:` | `core/config.py:276` |
| `EnsembleConfig.base_weight` | method | `def base_weight(self, agent_name: str) -> float:` | `core/config.py:285` |
| `LiveConfig` | dataclass | `class LiveConfig:` — `enabled: bool = False`, `testnet: bool = True`, `private_key_env: str = "HYPERLIQUID_PRIVATE_KEY"`, `live_confirm_env: str = "TRADEBOT_LIVE_CONFIRMED"`, `live_window_utc: Tuple[int, int] = (13, 23)`, `max_leverage: int = 10`, `max_order_notional: float = 100.0`, `max_position_notional: float = 300.0`, `max_total_notional: float = 600.0`, `max_daily_orders: int = 200`, `max_daily_loss: float = 50.0`, `max_consecutive_errors: int = 3`, `min_free_collateral: float = 100.0`, `use_exchange_side_tpsl: bool = True`, `auto_reconcile: bool = False`, `reconciliation_tolerance_days: int = 0` | `core/config.py:294` |
| `AppConfig` | dataclass | `class AppConfig:` — `account`, `symbols` (10 defaults: BTC/ETH/SOL/XRP/BNB/DOGE/ADA/AVAX/LINK/NEAR USDT perps), `exchange`, `scanning`, `scalping`, `ensemble`, `risk`, `fees`, `live`, `agent_intervals`, `us_market`, `indicators`, `dashboard`, `logging` (all `field(default_factory=...)`), then scalars `database_path: str = "data_store/trading_bot.db"`, `snapshot_prune_interval: int = 3600`, `snapshot_keep_per_symbol: int = 120`, `agent_log_prune_interval: int = 1800`, `agent_log_keep: int = 5000`, `news_rss: List[str]` (CoinDesk + Cointelegraph), `cryptopanic_url: str = "https://cryptopanic.com/api/free/v1/posts/"`, `cryptopanic_token: Optional[str] = None`, and `dynamic_tp_sl` | `core/config.py:362` |
| `_config_logger` | function | `def _config_logger():` | `core/config.py:410` |
| `_KNOWN_FIELDS` | global | `_KNOWN_FIELDS: dict = {}` | `core/config.py:437` |
| `_field_names` | function | `def _field_names(cls) -> frozenset:` | `core/config.py:440` |
| `_type_matches` | function | `def _type_matches(value: Any, expected) -> bool:` | `core/config.py:449` |
| `_apply_dict` | function | `def _apply_dict(obj, data: dict, section: str = "") -> None:` | `core/config.py:486` |
| `_MISSING_CONFIG_RISK_DEFAULTS` | constant | `_MISSING_CONFIG_RISK_DEFAULTS = (...)` — 6-tuple of `"key=dataclass_default (config.yaml: yaml_value) — <delta>"` strings | `core/config.py:547` |
| `_warn_missing_config_file` | function | `def _warn_missing_config_file(config: AppConfig, config_path: Path) -> None:` | `core/config.py:557` |
| `load_config` | function | `def load_config(path: str = "config.yaml") -> AppConfig:` | `core/config.py:623` |
| `_validate_dynamic_tp_sl` | function | `def _validate_dynamic_tp_sl(config: AppConfig) -> None:` | `core/config.py:756` |
| `_validate_ensemble_config` | function | `def _validate_ensemble_config(config: AppConfig) -> None:` | `core/config.py:855` |
| `_validate_scalping_economics` | function | `def _validate_scalping_economics(config: AppConfig) -> None:` | `core/config.py:932` |
| `_config` | global | `_config: Optional[AppConfig] = None` | `core/config.py:1015` |
| `get_config` | function | `def get_config() -> AppConfig:` | `core/config.py:1018` |
| `reload_config` | function | `def reload_config(path: str = "config.yaml") -> AppConfig:` | `core/config.py:1026` |

#### `load_config` contract

`path` is a **CWD-relative** string; `get_config()` takes no path at all and calls `load_config()` with the `"config.yaml"` default (`:1022`). The function always returns an `AppConfig` — it never returns `None` — and it builds the object by `AppConfig()` first, then overlays the file. Four things about the dispatch are load-bearing:

1. **Missing file returns early, before the validators.** `:628-630` — `if not config_path.exists(): _warn_missing_config_file(...); return config`. This returns *before* `yaml.safe_load` and before all three `_validate_*` calls at `:750-752`. A missing `config.yaml` therefore skips every boot validator, not just the file parsing.
2. **`config.live.enabled` is unconditionally forced to `False` at `:700`**, after any `live:` block is applied at `:699`. This is deliberate (`config.yaml` is committed to git); the only path to live trading is `trading/live/safety.py`, which reads env vars, not config. `config.yaml` has no `live:` block at all today, so `:698-700` is dead but retained as a guard.
3. **`symbols` and `news_sources` bypass `_apply_dict` entirely** (`:641`, `:739-748`) — plain assignment, no typo detection, no type warning.
4. **The `database:` block is filtered.** `:734-736` passes only keys starting with `snapshot_` into `_apply_dict(config, ...)`. `agent_log_prune_interval` and `agent_log_keep` *are* written in `config.yaml:115-116` and are therefore **never read** — the values `run.py:496` and `run.py:595` use are the dataclass defaults. The comment at `:727-733` says so explicitly and classifies it as a separate behavioural bug, not a visibility one. It is invisible today because `config.yaml` happens to write 1800/5000, exactly the dataclass defaults.

The `ensemble:` block is the only one needing two-level handling (`:657-675`): `agents_raw = ens_raw.pop("agents", None)` so the plain `_apply_dict` does not replace an agent dataclass with a raw dict, then each agent sub-dict gets its own `_apply_dict` under section `ensemble.agents.<name>`.

#### The three boot validators

All three share a shape: early `return` on a disabled `enabled` flag, accumulate **every** problem into a list, then raise one `ValueError` with messages joined by `"\n  - "` — never fail on the first problem. Called in this order from `load_config:750-752`.

- **`_validate_scalping_economics`** (`:932`) — six checks, all in `scalping`/`fees`: `breakeven_trigger_pct < min_profit_pct` (`:952`); net R:R after `fees.taker * 2` must not be sub-1 (`:959-968`); `breakeven_offset_pct >= roundtrip` (`:970`); `min_confidence <= reversal_close_threshold < 1.0` (`:982`); `stale_tick_window_seconds > max_tick_age_seconds` (`:993-1000`); `stale_tick_min_samples >= 2` (`:1002`).
- **`_validate_ensemble_config`** (`:855`) — seven checks: at least one enabled agent (`:881`); total active weight `> 0` (`:884`); no negative weight (`:888`); `0 < shrinkage_delta <= 1` (`:892`); `0 < min_prob < 0.5` (`:897`); `agreement_bonus >= 0` (`:902`); `max_snapshot_age_seconds > 0` (`:905`); `max_snapshot_age_seconds >= interval_seconds * 2` (`:916`); `diffusion_points >= 2` (`:923`).
- **`_validate_dynamic_tp_sl`** (`:756`) — `min_sl_pct > 0 and max_sl_pct > 0` (`:787`); `min_sl_pct < max_sl_pct` (`:791`); `atr_multiple > 0` (`:798`); `min_risk_reward > 1.0` (`:805`); `0.5 < max_breakeven_win_rate < 1.0` (`:812`); `atr_period >= 2` (`:819`); and the economics gate at `:826-846`, which runs only when `not problems` — minimum TP at minimum SL must exceed the fee roundtrip, and the implied breakeven win rate must not exceed `max_breakeven_win_rate`.

#### The warning paths

`_apply_dict` (`:486`) is the single choke point for every dataclass block, called ~19 times per load (15 static dispatches plus up to 4 agent sub-dicts). Three distinct diagnostics originate there:

- **Non-dict section** (`:504-509`) — the whole section is skipped with a warning naming the offending type.
- **Unknown key** (`:513-520`) — `hasattr` miss means typo or stale key. The value is *discarded* and the warning lists every known field in that section, sorted, so the operator can see the intended spelling.
- **Type mismatch** (`:521-528`) — `_type_matches` fails, warning fires, and the value is **still set** (`setattr` at `:529`). Deliberate: rejecting the entire config over one typo would stop the bot from starting, and the operator is better served by seeing the warning and restarting. The prevention target is silent failure, not imperfection.

`_type_matches` (`:449`) is deliberately loose. `bool` is checked before `int` because `bool` is an `int` subclass in Python; `float` fields accept `int` (YAML writes `2` where a float is meant); `int` fields reject `float` (`max_open_positions: 2.5` would be used as a count); compound types (dataclass, `Optional`, `Union`) fall through to `return True` at `:483` and are left to the validators. Note it is called as `_type_matches(value, type(getattr(obj, key, None)))` (`:521`) — the expected type is derived from the **current default value's** runtime type, not from the annotation.

`_field_names` (`:440`) wraps `fields(cls)` in a `frozenset` cached per class in `_KNOWN_FIELDS` (`:437`), because `load_config` would otherwise re-derive the same set ~19 times per boot.

#### `_warn_missing_config_file`

Fires when `config.yaml` is absent, which in practice means the wrong working directory rather than a deleted file — so there is no crash to notice, only a different risk profile. It emits two layers on purpose (`:558-582`): a `logging` warning (goes to the rotation file once `setup_logger()` has run) **and** a `print(..., file=sys.stderr, flush=True)` banner, because `load_config` can run before any handler exists and an unhandled `logger.warning` falls to `logging.lastResort` — bare, unformatted, easy to lose in surrounding output. The `print` is wrapped in `try/except Exception` (`:617-620`) so a closed stderr cannot take the boot down; the loaded config is still returned.

The banner enumerates `_MISSING_CONFIG_RISK_DEFAULTS` (`:547-554`, six lines, all of `RiskConfig`) plus two further behavioural deltas stated inline: `fees.taker 0.0005 vs 0.00045` and `decision_agent 60s vs 3s` — a 20× slower decision loop.

**Note:** the `config: AppConfig` parameter at `:557` is never read inside the function body; the banner text is fully hardcoded. The signature is the contract; the argument is inert.

#### Why `logging.getLogger` and not `core.logger.get_logger`

`_config_logger` (`:410-431`) returns `logging.getLogger("trading_bot.config")` and the module-level `from core.logger import get_logger` import is deliberately absent. Two distinct failures follow from adding it.

**Circular import.** `core/logger.py:11` does `from core.config import get_config` at module level. If `core/config.py` imported `core.logger` back at module level, then whichever is imported first needs the other while the second is only half-evaluated. The brief's claim is about the second failure, which is the more dangerous one.

**Re-entrancy, up to `RecursionError`.** `get_logger()` (`core/logger.py:396`) calls `setup_logger()` when the parent logger has no handlers, and `setup_logger()` (`core/logger.py:157`) calls `get_config().logging`, which re-enters `load_config()`. But `get_config()` only assigns the `_config` global *after* `load_config()` returns (`:1021-1023`). So at the exact point where `load_config` is handling a missing file and calls `_warn_missing_config_file` → `_config_logger` → `get_logger`, `_config` is still `None`, `load_config()` runs again, hits the same missing file, warns again — forever. Using `logging.getLogger` directly takes the plain logger with **no** `setup_logger` bootstrap, so nothing inside `load_config` can call `get_config` back.

#### The 13 defaults that disagree with `config.yaml`

Derived by comparing every key `load_config` actually applies against its dataclass default:

| Section | Field | Dataclass default | `config.yaml` |
| --- | --- | --- | --- |
| `risk` | `max_risk_per_trade` | `0.02` | `0.005` |
| `risk` | `max_leverage` | `20` | `50` |
| `risk` | `max_daily_loss` | `0.05` | `0.10` |
| `risk` | `max_drawdown` | `0.15` | `0.20` |
| `risk` | `max_open_positions` | `3` | `30` |
| `risk` | `default_leverage` | `5` | `10` |
| `fees` | `maker` | `0.0002` | `0.00015` |
| `fees` | `taker` | `0.0005` | `0.00045` |
| `agent_intervals` | `analysis_agent` | `300` | `15` |
| `agent_intervals` | `analysis_agent_us_open` | `180` | `10` |
| `agent_intervals` | `decision_agent` | `60` | `3` |
| `agent_intervals` | `decision_agent_us_open` | `30` | `2` |
| `agent_intervals` | `balance_snapshot` | `300` | `60` |

Six are the whole of `RiskConfig`; two are fees; five are agent intervals. The comment at `:534-546` states this count and is correct. Note the direction is not uniformly "safer by default": dataclass defaults are *tighter* on risk (0.5%→2% per trade is 4× larger exposure, 3 positions vs 30, 5% vs 10% daily loss) but `max_leverage` 20 < 50, and the decision loop runs at 60 s instead of 3 s — 20× slower, so a delayed decision is a decision that already expired. Neither profile is the intended one; the intended one is what `config.yaml` says.

Every `config.yaml` top-level key is read by `load_config`; the two `agent_log_*` keys are read *syntactically* (they are inside `database:`) and then discarded by the `snapshot_` prefix filter.

**State.** Owns the process-wide `_config: Optional[AppConfig]` singleton (`:1015`), the `_KNOWN_FIELDS` class→field-name cache (`:437`), and the `_MISSING_CONFIG_RISK_DEFAULTS` constant (`:547`). Because `_config` is a module global that `get_config()` hands out by reference, every consumer shares one mutable tree — `tests/test_direction_agents.py:262-263` mutates `get_config().ensemble.microstructure.enabled` and has to restore it in a `finally`. `EnsembleConfig.agent_configs` and `base_weight` are the only methods on any dataclass here; everything else is data.

**Side effects.** Reads `config.yaml` (and nothing else) via `open(..., encoding="utf-8")` + `yaml.safe_load` (`:632-633`). Writes no files, opens no network connection, touches no database. Global mutation: sets `_config` (`get_config`, `reload_config`) and writes into `_KNOWN_FIELDS`. Emits log records on the `"trading_bot.config"` logger and writes a banner to `sys.stderr`. `import os` at `:6` is dead — `os` appears nowhere else in the file.

**Consumers.** 25 first-party modules, all via `from core.config import ...`: `core/logger.py:11` and `core/scheduler.py:13`; `run.py:41` (`get_config`) and `run.py:285` (`LiveConfig`, deferred); `agents/analysis_agent.py:21`, `agents/decision_agent.py:15`, `agents/direction_agents.py:38`, `agents/execution_agent.py:16`; `analysis/backtester.py:36`, `analysis/ml_signals.py:15`, `analysis/technical.py:11`, `analysis/vol_target.py:28`, `analysis/volatility.py:25`; `data/macro_fetcher.py:14`, `data/news_fetcher.py:13`, `data/price_feed.py:24`; `database/db.py:7`; `trading/paper_engine.py:12`, `trading/position_manager.py:12`, `trading/risk_manager.py:7`; `dashboard/app.py:17`, `dashboard/callbacks/update_callbacks.py:17`, `dashboard/layouts/price_chart.py:9`; `trading/live/engine.py:29`, `trading/live/safety.py:24` (both `LiveConfig`); `live_doctor.py:185` (`LiveConfig`, deferred). Tests additionally import the private surface directly — `tests/test_config.py:9-14` pulls `_validate_scalping_economics`, and `tests/test_advanced_modules.py:803` pulls `_validate_dynamic_tp_sl` — so these validators are de facto public API.

## Core infrastructure

### 2. `core/event_bus.py`

**Role.** Single asyncio fan-out point between the four agents and the two trading engines. Each subscriber gets its own `asyncio.Queue`; `publish` drops the oldest queued event when a queue is full rather than blocking the publisher.

**Symbols.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `logger` | module global | `logger = get_logger("event_bus")` | `core/event_bus.py:14` |
| `Event` | dataclass | `@dataclass` | `core/event_bus.py:17` |
| `Event.channel` | field | `channel: str` | `core/event_bus.py:20` |
| `Event.data` | field | `data: Any` | `core/event_bus.py:21` |
| `Event.source` | field | `source: str = ""` | `core/event_bus.py:22` |
| `Event.timestamp` | field | `timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())` | `core/event_bus.py:23` |
| `EventBus` | class | `class EventBus:` | `core/event_bus.py:26` |
| `EventBus.__init__` | method | `def __init__(self, maxsize: int = 1000):` | `core/event_bus.py:37` |
| `EventBus.subscribe` | coroutine | `async def subscribe(self, channel: str) -> asyncio.Queue:` | `core/event_bus.py:42` |
| `EventBus.unsubscribe` | coroutine | `async def unsubscribe(self, channel: str, queue: asyncio.Queue):` | `core/event_bus.py:52` |
| `EventBus.publish` | coroutine | `async def publish(self, channel: str, data: Any, source: str = ""):` | `core/event_bus.py:62` |
| `EventBus.get_channel_stats` | method | `def get_channel_stats(self) -> Dict[str, int]:` | `core/event_bus.py:86` |
| `Channels` | class | `class Channels:` | `core/event_bus.py:92` |
| `Channels.PRICE_UPDATE` | constant | `PRICE_UPDATE = "price_update"` | `core/event_bus.py:94` |
| `Channels.NEWS_SENTIMENT` | constant | `NEWS_SENTIMENT = "news_sentiment"` | `core/event_bus.py:95` |
| `Channels.MARKET_ANALYSIS` | constant | `MARKET_ANALYSIS = "market_analysis"` | `core/event_bus.py:96` |
| `Channels.TRADE_DECISION` | constant | `TRADE_DECISION = "trade_decision"` | `core/event_bus.py:97` |
| `Channels.TRADE_EXECUTED` | constant | `TRADE_EXECUTED = "trade_executed"` | `core/event_bus.py:98` |
| `Channels.POSITION_UPDATE` | constant | `POSITION_UPDATE = "position_update"` | `core/event_bus.py:99` |
| `Channels.BALANCE_UPDATE` | constant | `BALANCE_UPDATE = "balance_update"` | `core/event_bus.py:100` |
| `Channels.AGENT_LOG` | constant | `AGENT_LOG = "agent_log"` | `core/event_bus.py:101` |
| `Channels.SYSTEM_EVENT` | constant | `SYSTEM_EVENT = "system_event"` | `core/event_bus.py:102` |
| `Channels.DIRECTION_ENSEMBLE` | constant | `DIRECTION_ENSEMBLE = "direction_ensemble"` | `core/event_bus.py:103` |

**Channel traffic, as it actually runs.** Five of the ten constants carry traffic.

| Channel | Published at | Subscribed at |
|---|---|---|
| `PRICE_UPDATE` | `data/price_feed.py:401` | `agents/execution_agent.py:49` |
| `NEWS_SENTIMENT` | `agents/news_agent.py:137` | `agents/analysis_agent.py:60` |
| `MARKET_ANALYSIS` | `agents/analysis_agent.py:188` | `agents/decision_agent.py:65` |
| `TRADE_DECISION` | `agents/decision_agent.py:439` | `agents/execution_agent.py:48` |
| `POSITION_UPDATE` | `trading/position_manager.py:146`, `:347`, `:499`; `trading/live/executor.py:609`, `:849` | `agents/decision_agent.py:66` |

`TRADE_EXECUTED` is published twice (`trading/paper_engine.py:780`, `trading/live/executor.py:622`) and **never subscribed by anyone**. The other two production occurrences of the string — `trading/paper_engine.py:756` and `trading/live/executor.py:639` — are `action="TRADE_EXECUTED"` on `AgentLog` rows, not publishes. `BALANCE_UPDATE`, `AGENT_LOG`, `SYSTEM_EVENT` and `DIRECTION_ENSEMBLE` are declared and never referenced outside their own definition line — the ensemble result goes to the `direction_snapshots` table, the balance snapshot to a table via `take_balance_snapshot`, agent reasoning to `agent_logs`.

`EventBus.unsubscribe` and `EventBus.get_channel_stats` have **no first-party caller**. Subscribers drain with `get_nowait()` in a `while … not queue.empty()` loop (`agents/execution_agent.py:57`, `:74`; `agents/analysis_agent.py:80`), so nothing ever unregisters.

**State.** Per-instance `self._channels: Dict[str, List[asyncio.Queue]]`, `self._maxsize` (default `1000`), `self._lock = asyncio.Lock()`. No module-level mutable state. `publish` snapshots the subscriber list under the lock (`:66-67`) then puts *outside* it (`:72-84`).

**Side effects.** `logger.debug` per subscribe/unsubscribe (`:49`, `:58`), `logger.warning` on a doubly-full queue (`:84`). Nothing else.

**Consumers.** `agents/base_agent.py:13`, `agents/analysis_agent.py:13`, `agents/decision_agent.py:14`, `agents/execution_agent.py:15`, `agents/news_agent.py:12`, `data/price_feed.py:25`, `trading/paper_engine.py:13`, `trading/position_manager.py:13`, `trading/live/executor.py:40` (imports `Channels` only — takes the bus as a constructor arg and tolerates `None` at `trading/live/executor.py:135-143`). `Event` is constructed only at `core/event_bus.py:64`; no module imports the dataclass.

---

### 3. `core/logger.py`

**Role.** Builds the single `trading_bot` logger tree (coloured console + rotating plain-text file) and owns the startup progress-bar subsystem that `run.py` drives through `stage()` / `step()` / `stage_done()`.

**Symbols.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `RESET`, `BOLD`, `DIM` | constants | `RESET = "\033[0m"` / `BOLD = "\033[1m"` / `DIM = "\033[2m"` | `core/logger.py:18-20` |
| `CYAN` … `WHITE` | constants | `CYAN = "\033[38;5;51m"` … `WHITE = "\033[38;5;255m"` | `core/logger.py:23-31` |
| `LEVEL_BADGES` | dict | `LEVEL_BADGES = {` | `core/logger.py:33` |
| `MODULE_COLORS` | dict | `MODULE_COLORS = {` | `core/logger.py:41` |
| `ColoredConsoleFormatter` | class | `class ColoredConsoleFormatter(logging.Formatter):` | `core/logger.py:56` |
| `ColoredConsoleFormatter.format` | method | `def format(self, record: logging.LogRecord) -> str:` | `core/logger.py:59` |
| `NEWLINE` | constant | `NEWLINE = "\r"` | `core/logger.py:105` |
| `_QUIET` | module global | `_QUIET = os.environ.get("TRADEBOT_QUIET_STARTUP", "").strip() in (` | `core/logger.py:106` |
| `_QUIET_LOGGER` | module global | `_QUIET_LOGGER: logging.Logger = None` | `core/logger.py:109` |
| `_make_progress_aware_emit` | function | `def _make_progress_aware_emit(original_emit):` | `core/logger.py:123` |
| `_make_progress_aware_emit.<locals>.emit` | closure | `def emit(record):` | `core/logger.py:140` |
| `setup_logger` | function | `def setup_logger(name: str = "trading_bot") -> logging.Logger:` | `core/logger.py:155` |
| `begin_quiet_mode` | function | `def begin_quiet_mode(name: str = "trading_bot"):` | `core/logger.py:218` |
| `end_quiet_mode` | function | `def end_quiet_mode():` | `core/logger.py:225` |
| `_BAR_WIDTH` | constant | `_BAR_WIDTH = 28` | `core/logger.py:236` |
| `_STAGE_COLORS` | dict | `_STAGE_COLORS = {` | `core/logger.py:240` |
| `_stage_active` | module global dict | `_stage_active = {"label": None, "done": 0, "total": 0,` | `core/logger.py:254` |
| `_stage_color` | function | `def _stage_color(label: str) -> str:` | `core/logger.py:258` |
| `_render_bar` | function | `def _render_bar(label, done, total, color, width=_BAR_WIDTH):` | `core/logger.py:265` |
| `stage` | function | `def stage(label: str, total: int = 0):` | `core/logger.py:279` |
| `step` | function | `def step(done: int = None, label: str = None):` | `core/logger.py:316` |
| `_draw_stage` | function | `def _draw_stage():` | `core/logger.py:336` |
| `stage_done` | function | `def stage_done(message: str = None, label: str = None):` | `core/logger.py:361` |
| `is_quiet` | function | `def is_quiet() -> bool:` | `core/logger.py:391` |
| `get_logger` | function | `def get_logger(module_name: str) -> logging.Logger:` | `core/logger.py:396` |

**Defect: `ColoredConsoleFormatter.format` drops every traceback.** The whole method builds one line by hand and returns it. The message is fetched at `core/logger.py:73` and the return is `core/logger.py:85`:

```python
        # Pesan dengan penyorotan kata kunci penting
        msg = record.getMessage()
```

```python
        div = f"{DARK_GRAY}│{RESET}"
        return f"{time_str} {div} {level_badge} {div} {mod_str} {div} {msg}"
```

There is no call to `self.formatException(record)` and no `self.formatStack(record.stack_info)` anywhere in the repo — grep for `formatException|exc_text|formatStack` returns nothing. `logging.Formatter.format` normally does three things: `record.getMessage()`, `self.formatException(record.exc_info)` when `exc_info` is set, and `self.formatStack` when `stack_info` is set. This override implements the first and drops the other two, so `logger.exception(...)` and any `logger.error(..., exc_info=True)` write **no traceback at all** to the console.

The two hottest error paths in the system depend on it: `run.py:677` (`logger.exception("Error pada execution loop (kegagalan %d berturut-turut): %s", ...)`, whose comment at `:672-676` explicitly justifies itself as *"Traceback penuh di sini, bukan cuma `str(e)`"*), `run.py:687-696` (`logger.critical(..., exc_info=True)`), and `trading/live/engine.py:651` (`logger.exception("Error di live loop: %s", exc)`). Each yields the message line and nothing else.

The rotating **file** handler is unaffected: it uses the plain `logging.Formatter` built at `core/logger.py:168-171`, which does call `formatException`. The traceback survives at `data_store/logs/trading_bot.log` and is missing from the terminal. Minimal fix: append the exception text before the `return`, e.g. `if record.exc_info: msg += "\n" + self.formatException(record.exc_info)`.

Two smaller things in the same method. `MODULE_COLORS` is keyed by bare module name (`:41-53`), while `mod = record.name.replace("trading_bot.", "")` (`:67`) leaves agent loggers as `agent.execution_agent` — a key that does not exist, so all four agents fall through to `WHITE`. And `mod_display = (mod[:14] + "…") if len(mod) > 15 else mod` (`:69`) truncates any name over 15 chars; `agent.execution_agent` is 21, while every `MODULE_COLORS` key passes through whole.

**Startup progress subsystem.** A single global bar, not one bar per stage. `stage(label, total=0)` (`:279`) sets the label; on the *first* call it resets `done`/`total`, on every later call it **adds** to `st["total"]` and never resets `done` (`:300-308`) — which is why the docstring example shows `step()  # 3/47` then `step()  # 4/47 <- lanjut, tidak reset`. `step(done=None, label=None)` (`:316`) returns immediately when no stage is active. `stage_done(message=None, label=None)` (`:361`) rewrites the line as a green `[OK] <text>` and clears `label`/`color` while deliberately leaving `done`/`total` intact (`:387`). ASCII `[OK]` rather than `✓` because the Windows console default cp1252 has no such glyph (`:373-374`). `_draw_stage` (`:336`) branches on TTY: on a TTY it writes `NEWLINE + "\033[K" + line` and flushes (`:347-349`); off a TTY it `print`s a deduped `label  pct%` line, deduped via a **function attribute** `_draw_stage._last` holding the `(label, done, total)` key (`:353-355`) — hidden global state not present in `_stage_active`.

**The env var that gates it.** `_QUIET` at `core/logger.py:106-108` reads **`TRADEBOT_QUIET_STARTUP`**, accepting `"1"`, `"true"`, `"True"`, `"yes"`. The module's own comment blocks are wrong about the name: they appear twice (`:95`, `:211`) and both say `TRADEBOT_VERBOSE_STARTUP=1 -> tampilkan juga detail`, a variable no code anywhere reads — grep for both names across the repo hits only `core/logger.py`. When `_QUIET` is set, three things happen: the console handler is pinned to `logging.WARNING` (`:176-179`), `HF_HUB_DISABLE_PROGRESS_BARS=1` and `TOKENIZERS_PARALLELISM=false` are injected into `os.environ` **at import time** (`:111-120`, deliberately before `transformers` is imported anywhere), and `setup_logger` skips the `_make_progress_aware_emit` wrapper entirely (`:185`) — quiet mode and progress-aware emit are mutually exclusive.

`end_quiet_mode()` (`:225`) sets `_QUIET = False` but **does not restore `console.setLevel`**; the console handler stays at WARNING for the life of the process. `run.py:901` is its only caller. `begin_quiet_mode` (`:218`) and `is_quiet()` (`:391`) have no caller at all, so `_QUIET_LOGGER` is written only by `end_quiet_mode` and read by nothing.

**State.** Module globals: the ANSI palette, `LEVEL_BADGES`, `MODULE_COLORS`, `NEWLINE`, `_QUIET`, `_QUIET_LOGGER`, `_BAR_WIDTH`, `_STAGE_COLORS`, `_stage_active`, plus the implicit `_draw_stage._last`. Handlers live on the `"trading_bot"` logger.

**Side effects.** Creates `Path(cfg.file).parent` (`data_store/logs/`) at `:190` and opens a `RotatingFileHandler` (10 MiB × 5 by default, `core/config.py:80-83`). Writes ANSI to `sys.stdout` from `stage`/`step`/`stage_done`/`_draw_stage` and from the patched `emit`. Mutates `os.environ` at `:118` and `:120`. At `:14-15`, `if os.name == "nt": os.system("")` spawns a `cmd.exe` child process purely to enable VT processing — a module-import-time side effect on every Windows run.

**Consumers.** `run.py:42-49` imports `end_quiet_mode`, `get_logger`, `setup_logger`, `stage`, `stage_done`, `step`; calls `setup_logger()` at `:937` and `:969`, `end_quiet_mode()` at `:901`, and drives 20+ `stage`/`step`/`stage_done` calls across `initialize()` and `run()`. `get_logger` is imported by 33 further modules. The claim that this covers "every agent, and all of `analysis/`, `data/`, `database/`, `ml/`, `trading/`, `trading/live/` and the dashboard" is false: `agents/direction_agents.py` and `trading/live/tui.py` contain no `core.logger` import at all, and neither does any file in `dashboard/layouts/`, `database/models.py`, `core/market_store.py`, `core/utils.py` or `trading/fill_cost.py`.

---

### 4. `core/market_store.py`

**Role.** Process-wide in-memory mirror of everything the feeds receive, so the background asyncio loops and the 500 ms Dash callbacks read the same object without touching the DB. Explicit rule at the top of the module: a field never filled returns `None`, never a plausible-looking default.

**Symbols.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `PRICE_HISTORY_MAX` | constant | `PRICE_HISTORY_MAX = 240` | `core/market_store.py:19` |
| `MarketStore` | class | `class MarketStore:` | `core/market_store.py:22` |
| `MarketStore.__init__` | method | `def __init__(self):` | `core/market_store.py:25` |
| `MarketStore.set_price` | method | `def set_price(self, symbol: str, price: float):` | `core/market_store.py:39` |
| `MarketStore.set_ticker` | method | `def set_ticker(self, symbol: str, ticker: dict):` | `core/market_store.py:55` |
| `MarketStore.get_ticker` | method | `def get_ticker(self, symbol: str) -> Optional[dict]:` | `core/market_store.py:64` |
| `MarketStore.get_price` | method | `def get_price(self, symbol: str) -> Optional[float]:` | `core/market_store.py:74` |
| `MarketStore.get_price_age` | method | `def get_price_age(self, symbol: str) -> Optional[float]:` | `core/market_store.py:91` |
| `MarketStore.get_all_prices` | method | `def get_all_prices(self) -> Dict[str, float]:` | `core/market_store.py:101` |
| `MarketStore.get_price_history` | method | `def get_price_history(self, symbol: str, seconds: Optional[float] = None) -> List[tuple]:` | `core/market_store.py:105` |
| `MarketStore.get_price_change` | method | `def get_price_change(self, symbol: str, seconds: float = 60.0) -> Optional[float]:` | `core/market_store.py:128` |
| `MarketStore.set_order_book` | method | `def set_order_book(self, symbol: str, order_book: dict):` | `core/market_store.py:147` |
| `MarketStore.get_order_book` | method | `def get_order_book(self, symbol: str) -> Optional[dict]:` | `core/market_store.py:152` |
| `MarketStore.has_order_book` | method | `def has_order_book(self, symbol: str) -> bool:` | `core/market_store.py:167` |
| `MarketStore.get_order_book_age` | method | `def get_order_book_age(self, symbol: str) -> Optional[float]:` | `core/market_store.py:171` |
| `MarketStore.set_live_candle` | method | `def set_live_candle(self, symbol: str, candle: dict):` | `core/market_store.py:187` |
| `MarketStore.get_live_candle` | method | `def get_live_candle(self, symbol: str) -> Optional[dict]:` | `core/market_store.py:198` |
| `MarketStore.set_funding` | method | `def set_funding(self, symbol: str, funding_rate: float):` | `core/market_store.py:211` |
| `MarketStore.get_funding` | method | `def get_funding(self, symbol: str) -> Optional[float]:` | `core/market_store.py:218` |
| `MarketStore.set_open_interest` | method | `def set_open_interest(self, symbol: str, open_interest: float):` | `core/market_store.py:228` |
| `MarketStore.get_open_interest` | method | `def get_open_interest(self, symbol: str) -> Optional[float]:` | `core/market_store.py:235` |
| `MarketStore.set_recent_trades` | method | `def set_recent_trades(self, symbol: str, trades: list):` | `core/market_store.py:248` |
| `MarketStore.get_recent_trades` | method | `def get_recent_trades(self, symbol: str) -> list:` | `core/market_store.py:253` |
| `market_store` | singleton | `market_store = MarketStore()` | `core/market_store.py:265` |

**What every accessor returns when the field was never filled.**

| Accessor | Never-filled result | Notes |
|---|---|---|
| `get_price` | `None` | exact key, then base-asset segment match (`symbol.split("/")[0].split(":")[0].upper()`), then `None` |
| `get_price_age` | `None` | `time.time() - self._price_ts[...]`; no ts entry → `None` |
| `get_price_history` | `[]` | never raises; filters by `ts >= time.time() - seconds` |
| `get_price_change` | `None` | also `None` when `len(window) < 2` or `first <= 0` |
| `get_ticker` | `None` | matches by `base in k.upper()` — free substring, unlike the other getters |
| `get_all_prices` | `{}` | returns a `.copy()`, so the caller cannot mutate the store |
| `get_order_book` | `None` | no synthetic depth is ever fabricated |
| `has_order_book` | `False` | thin wrapper over `get_order_book(...) is not None` |
| `get_order_book_age` | `None` | `None` if no book, `None` if no `"timestamp"` key, `None` on a non-numeric timestamp; divides the ms timestamp by `1000.0` before subtracting |
| `get_live_candle` | `None` | |
| `get_funding` | `None` | |
| `get_open_interest` | `None` | |
| `get_recent_trades` | `[]` | the only list-valued accessor that returns an empty list rather than `None` |

Only two accessors are dead in-tree: `get_ticker` (`:64`) and `has_order_book` (`:167`). `get_ticker`'s `base in k.upper()` substring test is also the one place the documented "NEAR must not match another symbol" rule at `:78-80` is not enforced — every other getter compares the base segment with `==`.

Writers refuse silently rather than storing junk: `set_price` drops non-float and `p <= 0` (`:41-45`), `set_ticker` drops falsy dicts (`:57-58`), `set_order_book` requires both `bids` and `asks` truthy (`:149`), `set_live_candle` requires a truthy `"close"` (`:194`), `set_funding`/`set_open_interest` swallow `TypeError`/`ValueError` (`:215`, `:232`), `set_recent_trades` accepts only a real `list` and truncates to `trades[:50]` (`:250-251`). `set_ticker` and `set_live_candle` both feed through to `set_price`, so a price can arrive without either feed calling `set_price` directly.

**State.** Nine per-symbol dicts on the singleton: `_tickers`, `_order_books`, `_last_prices`, `_price_ts`, `_price_history` (`deque(maxlen=240)`), `_live_candles`, `_funding`, `_open_interest`, `_recent_trades`. The docstring calls this "thread-safe" (`:23`) but there is **no lock** — safety rests entirely on CPython dict/deque atomicity plus the single event loop.

**Side effects.** Writes nothing. `time.time()` reads only. Bounded growth: 240 samples/symbol of history, 50 trades/symbol.

**Consumers.** `data/hyperliquid_feed.py:41` (six setters), `data/price_feed.py:394`, `:424`, `:523`, `:582`, `:593`, `trading/fill_cost.py:40` (via `get_order_book` + `get_order_book_age`), `trading/paper_engine.py:15`, `trading/position_manager.py:15`, `trading/live/executor.py:42`, `agents/decision_agent.py:17`, `agents/direction_agents.py:39`, `agents/execution_agent.py:17`, `analysis/volatility.py:27`, `analysis/vol_target.py:30`, `dashboard/callbacks/update_callbacks.py:19`.

---

### 5. `core/microstructure.py`

**Role.** The one and only place L2 orderbooks are parsed and Order Flow Imbalance is computed. Its whole reason to exist is to be swappable: the agent-facing surface is a module-level function set over one process-global `_KERNEL`, so a C++ implementation can replace the Python one without a single line changing in any agent.

**Symbols.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `MicrostructureKernel` | class (ABC) | `class MicrostructureKernel(ABC):` | `core/microstructure.py:57` |
| `MicrostructureKernel.ingest_l2` | abstract | `def ingest_l2(self, symbol: str, bids: list, asks: list) -> None:` | `core/microstructure.py:67` |
| `MicrostructureKernel.order_flow_imbalance` | abstract | `def order_flow_imbalance(` (→ `self, symbol: str, depth: int = 5`) `) -> Tuple[float, float]:` | `core/microstructure.py:71` |
| `MicrostructureKernel.depth_imbalance` | abstract | `def depth_imbalance(self, symbol: str, depth: int = 5) -> float:` | `core/microstructure.py:77` |
| `PythonKernel` | class | `class PythonKernel(MicrostructureKernel):` | `core/microstructure.py:81` |
| `PythonKernel.__init__` | method | `def __init__(self, max_symbols: int = 64):` | `core/microstructure.py:105` |
| `PythonKernel.ingest_l2` | method | `def ingest_l2(self, symbol: str, bids: list, asks: list) -> None:` | `core/microstructure.py:109` |
| `PythonKernel._weighted_volume` | staticmethod | `def _weighted_volume(levels: list, depth: int) -> float:` | `core/microstructure.py:123` |
| `PythonKernel.order_flow_imbalance` | method | `def order_flow_imbalance(` (→ `self, symbol: str, depth: int = 5`) `) -> Tuple[float, float]:` | `core/microstructure.py:130` |
| `PythonKernel.depth_imbalance` | method | `def depth_imbalance(self, symbol: str, depth: int = 5) -> float:` | `core/microstructure.py:160` |
| `PythonKernel.reset` | method | `def reset(self, symbol: Optional[str] = None) -> None:` | `core/microstructure.py:172` |
| `_KERNEL` | module global | `_KERNEL: MicrostructureKernel = PythonKernel()` | `core/microstructure.py:182` |
| `get_kernel` | function | `def get_kernel() -> MicrostructureKernel:` | `core/microstructure.py:185` |
| `register_kernel` | function | `def register_kernel(kernel: MicrostructureKernel) -> None:` | `core/microstructure.py:190` |
| `ingest_l2` | function | `def ingest_l2(symbol: str, bids: list, asks: list) -> None:` | `core/microstructure.py:218` |
| `order_flow_imbalance` | function | `def order_flow_imbalance(` (→ `symbol: str, depth: int = 5`) `) -> Tuple[float, float]:` | `core/microstructure.py:223` |
| `depth_imbalance` | function | `def depth_imbalance(symbol: str, depth: int = 5) -> float:` | `core/microstructure.py:230` |
| `reset_microstructure` | function | `def reset_microstructure(symbol: Optional[str] = None) -> None:` | `core/microstructure.py:235` |
| `cpp_kernel_available` | function | `def cpp_kernel_available() -> bool:` | `core/microstructure.py:245` |
| `CppMicrostructureKernel` | class | `class CppMicrostructureKernel(MicrostructureKernel):` | `core/microstructure.py:260` |
| `CppMicrostructureKernel.__init__` | method | `def __init__(self, max_symbols: int = 64, native_module=None):` | `core/microstructure.py:276` |
| `CppMicrostructureKernel.ingest_l2` | method | `def ingest_l2(self, symbol: str, bids: list, asks: list) -> None:` | `core/microstructure.py:281` |
| `CppMicrostructureKernel.order_flow_imbalance` | method | `def order_flow_imbalance(` (→ `self, symbol: str, depth: int = 5`) `) -> Tuple[float, float]:` | `core/microstructure.py:284` |
| `CppMicrostructureKernel.depth_imbalance` | method | `def depth_imbalance(self, symbol: str, depth: int = 5) -> float:` | `core/microstructure.py:290` |
| `CppMicrostructureKernel.reset` | method | `def reset(self, symbol: Optional[str] = None) -> None:` | `core/microstructure.py:293` |
| `CppMicrostructureKernel.ingest_json` | method | `def ingest_json(self, payload: bytes):` | `core/microstructure.py:296` |
| `CppMicrostructureKernel.symbol_count` | method | `def symbol_count(self) -> int:` | `core/microstructure.py:305` |
| `initialize_native_kernel` | function | `def initialize_native_kernel(force: bool = False) -> bool:` | `core/microstructure.py:309` |

**The ABC.** Three `@abstractmethod`s only. Notably `reset` is **not** part of the ABC even though `register_kernel` validates for it (`:201-206`) and both concrete classes implement it; and neither `ingest_json` nor `symbol_count` are on the ABC, so those two are C++-only extras invisible to the interface.

**PythonKernel.** Stores `_books: Dict[str, Tuple[list, list]]` of already-float-normalised `(price, size)` pairs, capped at `_max_symbols = 64` — `ingest_l2` silently drops a new symbol once 64 are tracked (`:113-114`). `_weighted_volume` applies a linearly decaying weight `1.0 - 0.1 * i`, so level 5 carries half the weight of the best level (`:127`). `order_flow_imbalance` returns `(0.0, 0.0)` for an unknown symbol or an empty side, guards `denom <= 0.0`, clamps `ofi` to `[-1, 1]`, and returns `(ofi, 0.0)` when `mid <= 0` (a price-0 exchange sentinel) (`:145-155`). `depth_imbalance` has the same guards but does not require both sides non-empty.

**How the two are swapped.** `_KERNEL` is a plain module-global rebind, never a registry. `register_kernel` validates first with `callable(getattr(...))` over the four names, raises `TypeError` listing what is missing if incomplete (`:207-209`), and only then rebinds and logs `"Kernel mikrostruktur diganti: PythonKernel -> CppMicrostructureKernel"` (`:211-215`). A broken adapter is refused at boot rather than silently producing zero OFI on the first frame. The five module-level functions are thin forwarders onto `_KERNEL`, so every call site follows the swap automatically.

`initialize_native_kernel` (`:309`) is the only path that performs the swap in production. It reads **`TRADEBOT_KERNEL`** (`:329`): `"python"` returns `False` unless `force=True`; `"cpp"` without `force` raises `RuntimeError` carrying the exact cmake build commands if the module cannot import (`:336-345`); unset, or any other value, attempts the import and on any exception logs at INFO and returns `False`, so the system keeps running on `PythonKernel`. On success it logs the native `__version__` and `MAX_LEVELS` (`:351-355`). It does a redundant local `import os` at `:327` even though `os` is already imported at module level (`:48`).

`CppMicrostructureKernel.__init__` does `import cpp_microstructure as native` **unconditionally at `:277`** even when a `native_module` was passed, so the injection point still hard-requires the compiled module to be importable. Every return value is cast through `float(...)` (`:288`, `:291`, `:306`).

**State.** `_KERNEL` (:182). `PythonKernel._books` / `._max_symbols`. `CppMicrostructureKernel._native` / `._impl`. `logger = get_logger("microstructure")` at `:54`.

**Side effects.** `logger.info` on every successful swap (`:213`) and on every silent fallback (`:364`). `register_kernel` raises `TypeError`; `initialize_native_kernel` may raise `RuntimeError` when `TRADEBOT_KERNEL=cpp` was explicitly requested. No files, no network, no DB. No lock — the module comment at `:179-181` states the single `_KERNEL` is shared across threads and therefore requires any native implementation to be thread-safe.

**Consumers.** `data/hyperliquid_feed.py:42` — calls `microstructure.initialize_native_kernel()` from `_ensure_native_kernel()` (`:115`), invoked once at `:513` *before the first WebSocket frame*, and calls `microstructure.ingest_l2(...)` on every L2 book (`:458`). `analysis/probability_engine.py:32` — `calculate_order_flow_imbalance` (`:40`) is the agent-facing façade; with a `symbol` it forwards to `microstructure.ingest_l2` + `.order_flow_imbalance` (`:89-90`), and **without** a symbol it constructs a throwaway `PythonKernel()` and calls it directly (`:85-87`), bypassing the global entirely. Tests: `tests/test_cpp_kernel.py` (parity, `-O2`-not-`-Ofast` in CMakeLists, `initialize_native_kernel`/`_ensure_native_kernel` wiring at `:343-353`, `register_kernel` rejecting a broken adapter at `:372-388`), `tests/test_bugfixes.py` (`get_kernel()` identity at `:923`/`:940`). Module-level `depth_imbalance()` and `reset_microstructure()` have **no first-party caller**; `cpp_kernel_available()` is referenced only by `tests/test_cpp_kernel.py:362`.

---

### 6. `core/scheduler.py`

**Role.** Thin wrapper over `AsyncIOScheduler` that lets agent jobs run at one cadence when the US equity market is shut and another when it is open, without the agents knowing which mode they are in.

**Symbols.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `logger` | module global | `logger = get_logger("scheduler")` | `core/scheduler.py:16` |
| `is_us_market_open` | function | `def is_us_market_open() -> bool:` | `core/scheduler.py:19` |
| `AgentScheduler` | class | `class AgentScheduler:` | `core/scheduler.py:36` |
| `AgentScheduler.__init__` | method | `def __init__(self):` | `core/scheduler.py:46` |
| `AgentScheduler.add_agent_job` | method | `def add_agent_job(` (→ `self, name: str, func: Callable, interval_normal: int, interval_us_open: int, start_immediately: bool = True`) `:` | `core/scheduler.py:51` |
| `AgentScheduler.add_fixed_job` | method | `def add_fixed_job(self, name: str, func: Callable, interval_seconds: int):` | `core/scheduler.py:88` |
| `AgentScheduler._adjust_intervals` | coroutine | `async def _adjust_intervals(self):` | `core/scheduler.py:100` |
| `AgentScheduler.start` | method | `def start(self):` | `core/scheduler.py:118` |
| `AgentScheduler.shutdown` | method | `def shutdown(self):` | `core/scheduler.py:132` |
| `AgentScheduler.running` | property | `def running(self) -> bool:` | `core/scheduler.py:138` |

**US-equity session test.** `is_us_market_open` (`:19`) builds minutes-since-midnight on both sides and does one half-open comparison, `open_minutes <= current_minutes < close_minutes` (`:33`), after rejecting `now.weekday() >= 5`. It reads `get_config().us_market` (`timezone="US/Eastern"`, `open_hour=9`, `open_minute=30`, `close_hour=16`; `core/config.py:52-55`), so the window is 570 ≤ t < 960 US/Eastern. Note `close_minutes = cfg.close_hour * 60` (`:31`) silently drops any close minute — there is no `close_minute` field, so a 16:30 close cannot be expressed.

**The job registry.** `self._jobs: dict` (`:48`) maps job name → `{"func", "interval_normal", "interval_us_open", "current_interval"}` (`:75-80`) and holds **only** jobs registered through `add_agent_job`. `add_fixed_job` calls `add_job` and logs but writes **no registry entry** (`:88-98`), so fixed jobs are invisible to `_adjust_intervals` and never rescheduled — correct by construction, since their cadence is constant.

**The interval swap.** `add_agent_job` picks `interval_us_open if is_us_market_open() else interval_normal` once at registration (`:64`) and installs an `IntervalTrigger(seconds=current_interval)` with `max_instances=1` and `id=name`. `start()` adds a second job, `id="_market_check"`, firing `_adjust_intervals` every `self._check_interval = 60` seconds (`:49`, `:121-127`). `_adjust_intervals` recomputes `is_us_market_open()` and, per registry entry where `target != info["current_interval"]`, calls `self._scheduler.reschedule_job(name, trigger=IntervalTrigger(seconds=target))` and writes the new value back (`:105-112`). The swap is therefore off a 60 s poll, not a real session calendar: worst-case latency between the 09:30 open and a job actually shortening its interval is one minute. `add_agent_job` with `start_immediately=True` additionally registers a second, trigger-less job `id=f"{name}_init"` (`:86`) that fires once at start.

**State.** `self._scheduler`, `self._jobs`, `self._check_interval = 60`. No module-level mutable state.

**Side effects.** Registers jobs on the shared `AsyncIOScheduler`; `logger.info` at `:82`, `:98`, `:114-116`, `:130`, `:135`. No files, no network, no DB.

**Consumers.** `run.py:51` / `:244` construct `AgentScheduler()`; ten registrations follow at `run.py:533`, `:540`, `:549`, `:558`, `:570`, `:579`, `:591`, `:600`, `:607`, `:614`. Only three go through `add_agent_job` and are therefore session-sensitive: `news_agent` (300 s → 120 s), `analysis_agent` (300 s → 180 s), `decision_agent` (60 s → 30 s; the only one passed `start_immediately=False`), all read from `AgentIntervals` at `core/config.py:38-43`. The other seven — `refresh_top_volume`, `direction_ensemble`, `direction_snapshot_prune`, `agent_log_prune`, `macro_update`, `finbert_batch`, `balance_snapshot` — are `add_fixed_job`. `run.py:917-918` guards `shutdown()` behind the `running` property. `is_us_market_open` is also imported directly by `agents/decision_agent.py:16`, which stamps `"us_market_open": is_us_market_open()` into its cycle record at `agents/decision_agent.py:104` — the only consumer of session state outside the scheduler itself.

---

### 7. `core/utils.py`

**Role.** One function: convert SQLite UTC timestamp values into `time.time()`-compatible epoch seconds without the OS local timezone shifting them.

**Symbols.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `parse_db_timestamp` | function | `def parse_db_timestamp(ts_val: Any) -> float:` | `core/utils.py:9` |

**Behaviour, in order.** Falsy input (`None`, `0`, `""`) → `0.0` (`:17-18`). `int`/`float` → `float(ts_val)` unchanged (`:19-20`). Otherwise the value is `str()`-ified and stripped, `"Z"` is replaced with `"+00:00"`, and `datetime.fromisoformat` parses it (`:22-25`); a naive result is stamped `tzinfo=timezone.utc` (`:27-28`) — the whole point of the function, since SQLite's `datetime('now')` is a naive UTC string and a naive `.timestamp()` on a UTC+7 machine is off by seven hours. **Any** exception in that block returns `0.0` (`:30-31`), so an unparseable string is indistinguishable from "never set". Callers therefore treat `0.0` as unknown: `agents/decision_agent.py:511-513` returns `False` from `_snapshot_is_fresh` when `ts <= 0`; `:163-164` skips a reversal check when `open_ts > 0` is false. `Union` (`:6`) is imported and unused.

**State.** None. Pure function.

**Side effects.** None.

**Consumers.** `agents/decision_agent.py:22` (`:163`, `:511`), `agents/execution_agent.py:19` (`:251`, `:295`, for `min_hold_seconds` enforcement on open positions).
## Sensing agents

### 8. `agents/direction_agents.py`

**Role.** Five classes: one abstract base (`DirectionAgent`), four specialist "voters" that each read one data source and emit a single `(direction, confidence, reasoning, factors)` verdict, and one coordinator (`DirectionEnsembleAgent`) that calls them all and writes one row per symbol per cycle to the `direction_snapshots` table. This module is the only LONG/SHORT direction path in the system; `AnalysisAgent._combine_signals` (below) is a separate, independent one that never sees these verdicts.

**Symbols.**

| Symbol | Kind | Signature | Loc |
| --- | --- | --- | --- |
| `DirectionAgent` | class | `class DirectionAgent(BaseAgent):` | `agents/direction_agents.py:42` |
| `DirectionAgent.agent_name` | attr | `agent_name = "direction"` | `agents/direction_agents.py:51` |
| `DirectionAgent.__init__` | method | `def __init__(self, event_bus):` | `agents/direction_agents.py:53` |
| `DirectionAgent.initialize` | method | `async def initialize(self):` | `agents/direction_agents.py:58` |
| `DirectionAgent.evaluate` | method | `def evaluate(self, symbol: str) -> dict:` | `agents/direction_agents.py:62` |
| `DirectionAgent._evaluate` | method | `def _evaluate(self, symbol: str):` | `agents/direction_agents.py:87` |
| `DirectionAgent.sense` | method | `async def sense(self) -> dict:` | `agents/direction_agents.py:93` |
| `DirectionAgent.think` | method | `async def think(self, data: dict) -> dict:` | `agents/direction_agents.py:96` |
| `DirectionAgent.act` | method | `async def act(self, analysis: dict):` | `agents/direction_agents.py:99` |
| `OrderFlowAgent` | class | `class OrderFlowAgent(DirectionAgent):` | `agents/direction_agents.py:103` |
| `OrderFlowAgent.agent_name` | attr | `agent_name = "orderflow"` | `agents/direction_agents.py:112` |
| `OrderFlowAgent._evaluate` | method | `def _evaluate(self, symbol: str):` | `agents/direction_agents.py:114` |
| `MomentumAgent` | class | `class MomentumAgent(DirectionAgent):` | `agents/direction_agents.py:148` |
| `MomentumAgent.agent_name` | attr | `agent_name = "momentum"` | `agents/direction_agents.py:156` |
| `MomentumAgent._evaluate` | method | `def _evaluate(self, symbol: str):` | `agents/direction_agents.py:158` |
| `TechnicalAgent` | class | `class TechnicalAgent(DirectionAgent):` | `agents/direction_agents.py:184` |
| `TechnicalAgent.agent_name` | attr | `agent_name = "technical"` | `agents/direction_agents.py:193` |
| `TechnicalAgent.__init__` | method | `def __init__(self, event_bus, db_path: str):` | `agents/direction_agents.py:195` |
| `TechnicalAgent.evaluate_async` | method | `async def evaluate_async(self, symbol: str) -> dict:` | `agents/direction_agents.py:200` |
| `TechnicalAgent._evaluate` | method | `def _evaluate(self, symbol: str):` | `agents/direction_agents.py:215` |
| `MicrostructureAgent` | class | `class MicrostructureAgent(DirectionAgent):` | `agents/direction_agents.py:256` |
| `MicrostructureAgent.agent_name` | attr | `agent_name = "microstructure"` | `agents/direction_agents.py:264` |
| `MicrostructureAgent._evaluate` | method | `def _evaluate(self, symbol: str):` | `agents/direction_agents.py:266` |
| `_safe_float` | function | `def _safe_float(value) -> Optional[float]:` | `agents/direction_agents.py:320` |
| `_load_closes` | function | `def _load_closes(db_path: str, symbol: str, limit: int = 100) -> List[dict]:` | `agents/direction_agents.py:331` |
| `_signed_volume` | function | `def _signed_volume(tape: list):` | `agents/direction_agents.py:367` |
| `DirectionEnsembleAgent` | class | `class DirectionEnsembleAgent(BaseAgent):` | `agents/direction_agents.py:401` |
| `DirectionEnsembleAgent.__init__` | method | `def __init__(self, event_bus, symbols: List[str], db_path: str):` | `agents/direction_agents.py:410` |
| `DirectionEnsembleAgent.initialize` | method | `async def initialize(self):` | `agents/direction_agents.py:418` |
| `DirectionEnsembleAgent.run_cycle` | method | `async def run_cycle(self):` | `agents/direction_agents.py:437` |
| `DirectionEnsembleAgent.sense` | method | `async def sense(self) -> dict:` | `agents/direction_agents.py:455` |
| `DirectionEnsembleAgent.think` | method | `async def think(self, data: dict) -> dict:` | `agents/direction_agents.py:458` |
| `DirectionEnsembleAgent.act` | method | `async def act(self, analysis: dict):` | `agents/direction_agents.py:461` |
| `DirectionEnsembleAgent._evaluate_symbol` | method | `async def _evaluate_symbol(self, symbol: str) -> Optional[dict]:` | `agents/direction_agents.py:464` |
| `DirectionEnsembleAgent._realized_vol` | method | `async def _realized_vol(self, symbol: str) -> Optional[float]:` | `agents/direction_agents.py:505` |

**Verdict rules, class by class.**

`DirectionAgent.evaluate` is the single funnel. It calls the subclass `_evaluate`, and converts the 4-tuple into a uniform dict via `analysis.direction_ensemble.make_verdict`. Any exception becomes `abstain(agent, symbol, f"error: {exc}")` — one blind agent must never take the ensemble down. `_evaluate` returning `None` becomes `abstain(..., "data tidak cukup")`. This distinction matters: `abstained=True` is stripped before pooling (`direction_ensemble.py:102`), so a blind agent is invisible rather than being counted as a calm NEUTRAL vote.

- **`OrderFlowAgent`** — reads `market_store.get_order_book(symbol)`; `None` book → abstain. Calls `calculate_order_flow_imbalance(book, symbol=symbol)`, passing `symbol` so the same book is already resident in the kernel this cycle (`agents/direction_agents.py:125`). If both OFI and spread are exactly `0.0` → abstain. If `abs(ofi) < 1e-6` → NEUTRAL with confidence `0.0`. Otherwise direction is LONG when `ofi > 0`, SHORT when `ofi < 0`, and **confidence is the raw `abs(ofi)`** — no normalization.
- **`MomentumAgent`** — `market_store.get_price_change(symbol, seconds=self.ensemble.momentum_window_seconds)`, a relative fraction. `None` → abstain. `abs(change) < 1e-9` → NEUTRAL, confidence `0.0`. Otherwise confidence is `min(abs(change) / (threshold * 2.0), 1.0)` where `threshold = max(config.scalping.momentum_threshold, 1e-6)` — i.e. two times the configured 0.0008 momentum threshold is full confidence.
- **`TechnicalAgent`** — loads the last 100 1m candles through `_load_closes` on a *separate synchronous* `sqlite3` connection; fewer than 30 closes → abstain. Builds a DataFrame, runs `self.technical.calculate_indicators(df)`, extracts ten indicator keys (`rsi`, `rsi_fast`, `macd_hist`, `atr`, `ema_short`, `ema_long`, `ema_3`, `ema_5`, `bb_lower`, `bb_upper`) through `_safe_float`, then hands them to `calculate_technical_zscore`. `abs(z) < 1e-6` → NEUTRAL, confidence `0.0`. Otherwise confidence is `min(abs(z) / 2.0, 1.0)`, direction from the sign of `z`. Its `evaluate_async` (`agents/direction_agents.py:200`) wraps `evaluate` in `asyncio.to_thread` — the pandas work is CPU-bound and would otherwise stall price updates, order execution and every scheduler job on the single event loop.
- **`MicrostructureAgent`** — builds a list of z-components. (1) Signed tape volume: at least `min_trades_for_microstructure` (default 8) trades, `imbalance = signed_vol / total_vol` in −1..1, contributed as `imbalance * 1.5`. (2) Funding, deliberately **contrarian**: `z_funding = clip(-funding / 0.0001, -2.0, 2.0)` — crowding longs pay positive funding, so high funding is a reversal risk. (3) Open interest is recorded in `details` as context but casts **no vote**. With no z-components at all → abstain; `abs(mean(z)) < 1e-6` → NEUTRAL; else confidence `min(abs(z)/2.0, 1.0)`.
- **`_signed_volume`** — trades whose `side` is neither `B` nor `A` are dropped from *both* numerator and denominator. Counting them in the denominator only would drag imbalance toward zero, producing a fake NEUTRAL that looks like real evidence.

**Ensemble weights.** `initialize` (`agents/direction_agents.py:418`) builds only those four specialists whose `self.ensemble.base_weight(name)` is non-zero. Default `EnsembleConfig` weights are **orderflow 0.30, momentum 0.25, technical 0.25, microstructure 0.20** (`core/config.py:263-274`, mirrored in `config.yaml:202-214`). Pooling happens in `analysis/direction_ensemble.py:aggregate`, which does not average probabilities: each verdict's confidence maps linearly to a z-score capped at `MAX_AGENT_Z = 2.5`, weight is multiplied by an agreement bonus `1 + 0.5 * agreement_score`, the weighted mean is shrunk by `shrinkage_delta` (0.85), pushed through a numerically-stable sigmoid, and clamped into `[min_prob, 1 - min_prob]` = `[0.02, 0.98]`. `prob_short = 1 - prob_long` and `confidence = abs(prob_long - 0.5) * 2`.

**How the snapshot is written.** `_evaluate_symbol` (`agents/direction_agents.py:464`) collects one verdict per specialist — dispatching `TechnicalAgent` through `evaluate_async`, the rest synchronously since they only read the in-memory `market_store` — then calls `aggregate(verdicts, self.ensemble)`. It then calls `_realized_vol`, which loads 40 1m candles and computes `std(diff(log(closes)), ddof=1)`, refusing to return a sigma unless it has at least `min_returns_for_vol + 1` closes, and flooring the result at `0.0002`. When sigma exists, `probability_engine.compute_directional_curve` produces the diffusion payload over `diffusion_horizon_minutes` (30) across `diffusion_points` (60). `run_cycle` loops `self.symbols` and calls `repo.insert_direction_snapshot(snapshot)` — one `INSERT` plus one `commit` per symbol (`database/repository.py:460`), so this class is the sole writer of `direction_snapshots` and readers never see a half-written row. `agent_breakdown` and `diffusion` are serialized with `json.dumps(..., default=str)` here, not by the repository.

**State.** `DirectionAgent`: `config`, `ensemble`. `TechnicalAgent`: `technical` (a `TechnicalAnalyzer`), `db_path`. `DirectionEnsembleAgent`: `config`, `ensemble`, `symbols` (copied list), `db_path`, `specialists`. The base supplies `self._repo` (lazily built, shared) via `BaseAgent._get_repo`. Module-level singletons read but not written: `market_store`.

**Side effects.** Reads `market_store` (`get_order_book`, `get_price_change`, `get_recent_trades`, `get_funding`, `get_open_interest`); reads the `candles` table via its own short-lived synchronous `sqlite3` connection, deliberately separate from the global `aiosqlite` connection because it runs in a worker thread; `INSERT`s into `direction_snapshots` and commits; logs through `self.logger`. It publishes **no** event-bus channel — its only outward interface is the table. The ensemble is not itself a `BaseAgent` lifecycle user in practice: `sense`/`think`/`act` are empty formality, because `run_cycle` overrides the sense→think→act loop.

**Consumers.** `run.py:62` imports `DirectionEnsembleAgent` and constructs it at `run.py:408` only when `config.ensemble.enabled`, scheduling `run_cycle` every `ensemble.interval_seconds` (5 s default, `run.py:571-573`). `DecisionAgent` reads it back via `repo.get_latest_direction_snapshot(symbol)` at `agents/decision_agent.py:473`, rejecting snapshots older than `ensemble.max_snapshot_age_seconds` (20 s, `agents/decision_agent.py:514`). The dashboard reads the latest row directly at `dashboard/callbacks/update_callbacks.py:799-803`. `tests/test_direction_agents.py:11-18` imports the five classes plus `_signed_volume`.

---

### 9. `agents/analysis_agent.py`

**Role.** The second, independent direction-scoring path. It runs the classical sense→think→act cycle over 5m OHLCV, macro context and news sentiment, combines technical / fundamental / ML opinions into a `BULLISH` / `BEARISH` / `NEUTRAL` verdict, persists a `Signal` row per symbol, and broadcasts on `Channels.MARKET_ANALYSIS`.

**Symbols.**

| Symbol | Kind | Signature | Loc |
| --- | --- | --- | --- |
| `logger` | module global | `logger = get_logger("analysis_agent")` | `agents/analysis_agent.py:24` |
| `AnalysisAgent` | class | `class AnalysisAgent(BaseAgent):` | `agents/analysis_agent.py:27` |
| `AnalysisAgent.__init__` | method | `def __init__(self, event_bus: EventBus, price_feed: PriceFeed, macro_fetcher: MacroFetcher, sentiment_analyzer: SentimentAnalyzer):` | `agents/analysis_agent.py:37` |
| `AnalysisAgent.initialize` | method | `async def initialize(self):` | `agents/analysis_agent.py:58` |
| `AnalysisAgent.sense` | method | `async def sense(self) -> dict:` | `agents/analysis_agent.py:64` |
| `AnalysisAgent.think` | method | `async def think(self, data: dict) -> dict:` | `agents/analysis_agent.py:91` |
| `AnalysisAgent.act` | method | `async def act(self, analysis: dict):` | `agents/analysis_agent.py:171` |
| `AnalysisAgent.update_macro` | method | `async def update_macro(self):` | `agents/analysis_agent.py:200` |
| `AnalysisAgent.run_finbert_batch` | method | `async def run_finbert_batch(self):` | `agents/analysis_agent.py:222` |
| `AnalysisAgent._combine_signals` | method | `def _combine_signals(self, technical: Dict, fundamental: Dict, ml_prediction: Dict) -> Dict:` | `agents/analysis_agent.py:262` |
| `direction_to_score` | nested function | `def direction_to_score(direction: str) -> float:` | `agents/analysis_agent.py:274` |

**`_combine_signals` — the second scoring path.** Weights are fixed literals in the function body, not config: `{"technical": 0.40, "fundamental": 0.25, "ml": 0.35}` (`agents/analysis_agent.py:271`). Directions are collapsed to a signed score through a local mapping — `{"BULLISH": 1, "LONG": 1, "BEARISH": -1, "SHORT": -1}`, anything else `0`, so an unknown string is silently neutral rather than an error. Each score is multiplied by *both* its weight and that source's own confidence, then summed; the confidence denominator is the same weighted sum of confidences without the sign. The **deadband** is a ±0.15 threshold on that weighted score: `> 0.15` → BULLISH, `< -0.15` → BEARISH, otherwise NEUTRAL (`agents/analysis_agent.py:302-307`). Because the score is confidence-scaled, three sources that all agree at confidence 0.5 sum to at most 0.5 × (0.40 + 0.25 + 0.35) = 0.5 — clearing the band — while one source at confidence 0.5 alone yields at most 0.20, also clearing it. A single weak dissenting source cannot flip the verdict. Returned `confidence` is `round(min(total_weight, 1.0), 3)`, i.e. it saturates once the three confidences sum to 1 under their weights; `weighted_score` is rounded to 4 places, and `components` echoes each source's raw direction and confidence for the HUD. This path shares no code and no weights with the `direction_agents.py` ensemble, and its output never reaches `direction_snapshots`.

**Cycle.** `sense` fetches `limit=200` 5m candles for every `config.symbols` entry via `price_feed.fetch_ohlcv`, then drains its `Channels.NEWS_SENTIMENT` subscription queue without blocking, keeping only the newest `aggregate.avg_score` into `_last_sentiment_score`; `initialize` is what performs that subscription and also loads the ML model. `think` converts each candle list with `TechnicalAnalyzer.ohlcv_to_dataframe`, calculates indicators, generates technical signals, re-seeds the shared `FundamentalAnalyzer` with the cached macro context and a POSITIVE/NEGATIVE/NEUTRAL label derived from the sentiment score at a ±0.05 cut, runs `ml_signals.predict`, calls `_combine_signals`, and extracts five indicator values (`rsi`, `macd_hist`, `atr`, `ema_short`, `ema_long`) with NaN fallbacks of 50 / 0 / 0 / 0 / 0. `act` writes one `Signal` row per symbol and publishes the combined verdict.

**State.** `price_feed`, `macro_fetcher`, `sentiment`, `technical`, `fundamental`, `ml_signals`, `config`, plus the cycle caches `_last_macro_context: Dict`, `_last_sentiment_score: float`, `_news_queue`. `fundamental` and `ml_signals` are **long-lived mutable collaborators**, not per-call objects: `update_macro` mutates the analyzer's calendar state and `think` mutates its macro/sentiment inputs on every cycle, which is why its own outputs carry between cycles.

**Side effects.** `INSERT` into `signals` (one row per symbol per cycle, `signal_type="TECHNICAL"`, `source="analysis_agent"`) plus a commit each; `publish` on `Channels.MARKET_ANALYSIS` with combined / technical / fundamental / ml / price / indicators; a `BaseAgent` `agent_logs` row per cycle. `update_macro` performs the network fetch (`macro_fetcher.fetch_all()`), upserts every FRED series via `repo.upsert_macro`, and refreshes the fundamental analyzer's calendar. `run_finbert_batch` reads the 20 most recent news rows, selects only those with `sentiment_finbert IS NULL`, and issues a **raw** `UPDATE news SET sentiment_finbert = ?, sentiment_label = ? WHERE id = ?` through `db.db.execute` followed by one `db.db.commit()` — it bypasses `Repository` entirely. Both auxiliary methods swallow all exceptions and log an error.

**Consumers.** `run.py:59` imports `AnalysisAgent`; it is constructed at `run.py:382`, initialized at `run.py:418`, and scheduled three ways: `run_cycle` at `analysis_agent` / `analysis_agent_us_open` intervals (`run.py:549-555`), `update_macro` every `macro_data` seconds, default 21600 (6 h, `run.py:600-604`), and `run_finbert_batch` every `finbert_batch` seconds, default 900 (15 min, `run.py:607-611`). Its `MARKET_ANALYSIS` events are consumed by `DecisionAgent`, which subscribes at `agents/decision_agent.py:65`.

---

### 10. `agents/news_agent.py`

**Role.** Fetches headlines, scores each one for sentiment, computes one market-level aggregate, persists the result, and broadcasts it for `AnalysisAgent` to consume.

**Symbols.**

| Symbol | Kind | Signature | Loc |
| --- | --- | --- | --- |
| `logger` | module global | `logger = get_logger("news_agent")` | `agents/news_agent.py:18` |
| `NewsAgent` | class | `class NewsAgent(BaseAgent):` | `agents/news_agent.py:21` |
| `NewsAgent.__init__` | method | `def __init__(self, event_bus: EventBus, sentiment_analyzer: SentimentAnalyzer):` | `agents/news_agent.py:31` |
| `NewsAgent.sense` | method | `async def sense(self) -> dict:` | `agents/news_agent.py:36` |
| `NewsAgent.think` | method | `async def think(self, data: dict) -> dict:` | `agents/news_agent.py:43` |
| `NewsAgent.act` | method | `async def act(self, analysis: dict):` | `agents/news_agent.py:110` |

**How headlines are scored.** `sense` calls `NewsFetcher.fetch_all()`, which fans RSS (CoinDesk / CoinTelegraph) and the CryptoPanic HTTP request out concurrently with `asyncio.gather` and concatenates the results (`data/news_fetcher.py:125-134`), then immediately calls `fetcher.clear_cache()` — that trims the dedup set to the last 200 titles once it exceeds 500. `think` builds the text `f"{item.title}. {item.content_summary or ''}"` and awaits `SentimentAnalyzer.analyze_vader_async` per item; that helper is just `loop.run_in_executor(None, self.analyze_vader, text)` (`data/sentiment.py:93-96`), so the CPU-bound VADER work leaves the event loop alone. The `compound` score is written to `item.sentiment_vader` and the VADER label to `item.sentiment_label` — note these are mutations of the in-memory `NewsItem`, not DB writes.

**Impact tiers** are derived from `abs(compound)`: `> 0.5` → HIGH, `> 0.2` → MEDIUM, otherwise LOW (`agents/news_agent.py:69-75`).

**Aggregate.** `self.sentiment.aggregate_sentiment(sentiments)` (`data/sentiment.py:175`) reads `compound`, falling back to `score`, averages over *all* items regardless of tier, rounds to 4 places, and labels `avg >= 0.1` POSITIVE, `avg <= -0.1` NEGATIVE, else NEUTRAL, while counting positives and negatives separately. When `news_items` is empty, `think` short-circuits with `{"avg_score": 0, "label": "NEUTRAL", "count": 0}`.

**What it publishes and writes.** `act` inserts each item only if `repo.news_exists(item.title, item.source)` is false, so duplicates are skipped per row and a failure on one item does not abort the rest. It then writes a single `Signal` row under the sentinel symbol **`"MARKET"`** (not a coin — the HUD filters it out, `dashboard/layouts/hud_figures.py:535-536`) with `signal_type="SENTIMENT"`, the JSON aggregate as `signal_value`, the label as `direction`, and confidence `min(abs(avg_score) * 2, 1.0)`, so the 0.1 NEUTRAL band edge saturates at only 0.2 confidence. Finally it publishes on `Channels.NEWS_SENTIMENT` with the aggregate, the high-impact count, and at most the first five high-impact titles.

**State.** `fetcher` (a `NewsFetcher` it constructs itself), `sentiment` (injected and shared with `AnalysisAgent`, so the FinBERT pipeline loaded at boot is reused). The module-level `logger` is bound but unused inside the class — the class logs through `self.logger` inherited from `BaseAgent`.

**Side effects.** Outbound HTTP to RSS feeds and CryptoPanic; `INSERT` into `news` and into `signals` (each with its own commit); `publish` on `Channels.NEWS_SENTIMENT`; a `BaseAgent` `agent_logs` row per cycle. It does not implement `initialize`, so the base's no-op applies and no channel is subscribed.

**Consumers.** `run.py:58` imports `NewsAgent`; constructed at `run.py:380` and scheduled at `run.py:541-546` with `start_immediately=True`, at `news_agent` / `news_agent_us_open` intervals. Its `NEWS_SENTIMENT` channel has exactly one subscriber, `AnalysisAgent` (`agents/analysis_agent.py:60`). The `news` table it fills is later read by `AnalysisAgent.run_finbert_batch` at `agents/analysis_agent.py:228`.

## Decision and execution agents

### 11. `agents/base_agent.py`

**Role.** Abstract base for every autonomous agent. It fixes the sense → think → act cycle, wraps each cycle in error handling and cancellation handling, and persists the reasoning of every cycle to `agent_logs`.

**Symbols.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `BaseAgent` | class (ABC) | `class BaseAgent(ABC):` | `agents/base_agent.py:20` |
| `BaseAgent.__init__` | method | `def __init__(self, name: str, event_bus: EventBus):` | `agents/base_agent.py:30` |
| `BaseAgent._get_repo` | method | `async def _get_repo(self) -> Repository:` | `agents/base_agent.py:38` |
| `BaseAgent.sense` | abstract | `async def sense(self) -> dict:` | `agents/base_agent.py:45` |
| `BaseAgent.think` | abstract | `async def think(self, data: dict) -> dict:` | `agents/base_agent.py:55` |
| `BaseAgent.act` | abstract | `async def act(self, analysis: dict):` | `agents/base_agent.py:68` |
| `BaseAgent.run_cycle` | method | `async def run_cycle(self):` | `agents/base_agent.py:77` |
| `BaseAgent._log_cycle` | method | `async def _log_cycle(self, analysis: dict):` | `agents/base_agent.py:119` |
| `BaseAgent._log_error` | method | `async def _log_error(self, error_msg: str):` | `agents/base_agent.py:136` |
| `BaseAgent.publish` | method | `async def publish(self, channel: str, data: dict):` | `agents/base_agent.py:149` |

**State.** `self.name` (the logger stem, e.g. `"execution_agent"` → logger `agent.execution_agent`), `self.event_bus`, `self.logger`, `self._repo` (lazy `Repository`, `None` until first use), `self._running` (declared at `:35`, never read or written anywhere else in the class), `self._cycle_count` (monotonic integer incremented at the top of every `run_cycle`, formatted into `cycle_id = f"{self.name}#{self._cycle_count}"` at `:80`). The class owns no module-level globals.

**The sense/think/act contract.** `sense()` collects raw data, `think(data)` reasons about it, `act(analysis)` acts. `run_cycle` is the only driver: it increments the counter, then runs the three phases strictly in sequence (`:86`, `:89`, `:92`), and on success writes a `CYCLE` row via `_log_cycle` (`:95`). It returns `None`; the cycle result is carried entirely in the dicts. Subclasses in this repo add an `initialize()` that is *not* part of the contract — the base class never calls it; `run.py` calls each `initialize()` explicitly at `:418-420`.

**The `CancelledError` comment at `agents/base_agent.py:99-110` is load-bearing.** `run_cycle` has two sibling handlers, and the ordering plus the bare `raise` are the whole point. The comment, translated and quoted:

> This is NOT an error. `shutdown()` cancels the task that is waiting on I/O, and APScheduler passes it through as-is.
>
> In Python 3.8+ `CancelledError` descends from `BaseException`, not `Exception` — so the `except Exception` below does NOT catch it, and the whole traceback gets printed to the terminal every single time you Ctrl+C.
>
> We absorb it and let the cancellation keep propagating upward.

Consequences that follow directly from that reasoning and are visible in the code: the `except asyncio.CancelledError` clause logs at DEBUG (not ERROR) with `f"Siklus {cycle_id} dibatalkan (shutdown)"` and re-raises, so cancellation is never counted as a failure; `_log_cycle` and `_log_error` are skipped on cancellation because the handler at `:99` precedes them. The `except Exception as e` clause at `:112` logs ERROR, dumps `traceback.format_exc()` at DEBUG, and calls `_log_error`. `run.py:655-668` repeats the same pattern in the execution loop and explicitly cross-references `base_agent.py:99-110` as the canonical instance.

**Side effects.** DB writes only: `Repository.insert_agent_log` in `_log_cycle` (`:132`) and `_log_error` (`:145`), both writing an `AgentLog` row. `input_data` / `output_data` are `json.dumps(..., default=str)[:2000]` of `analysis["input_summary"]` / `analysis["output_summary"]`; `reasoning` falls back `analysis["reasoning"]` → `analysis["summary"]` → the literal `"Tidak ada penalaran"` (`:128`). No network. `publish()` (`:151`) forwards to `EventBus.publish(channel, data, source=self.name)` — the only way a subclass reaches the bus. No global mutation.

**Consumers.** `agents/analysis_agent.py:12`, `agents/decision_agent.py:13`, `agents/direction_agents.py:28`, `agents/execution_agent.py:14`, `agents/news_agent.py:11`. All five subclasses are instantiated in `run.py`.

### 12. `agents/execution_agent.py`

**Role.** Turns validated `TradeDecision`s into engine calls, and owns three scalp exit policies (breakeven SL lock, fast take profit, expired-position auto-close) that run on a separate ~0.3 s tick from `check_positions()`.

**Symbols.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `ExecutionAgent` | class | `class ExecutionAgent(BaseAgent):` | `agents/execution_agent.py:26` |
| `ExecutionAgent.__init__` | method | `def __init__(self, event_bus: EventBus, engine):` | `agents/execution_agent.py:37` |
| `ExecutionAgent.initialize` | method | `async def initialize(self):` | `agents/execution_agent.py:46` |
| `ExecutionAgent.sense` | method | `async def sense(self) -> dict:` | `agents/execution_agent.py:52` |
| `ExecutionAgent.think` | method | `async def think(self, data: dict) -> dict:` | `agents/execution_agent.py:83` |
| `ExecutionAgent.act` | method | `async def act(self, analysis: dict):` | `agents/execution_agent.py:158` |
| `ExecutionAgent.check_positions` | method | `async def check_positions(self):` | `agents/execution_agent.py:177` |
| `ExecutionAgent._protect_breakeven` | method | `async def _protect_breakeven(self):` | `agents/execution_agent.py:194` |
| `ExecutionAgent._auto_close_expired` | method | `async def _auto_close_expired(self):` | `agents/execution_agent.py:243` |
| `ExecutionAgent._scalp_take_profit` | method | `async def _scalp_take_profit(self):` | `agents/execution_agent.py:279` |
| `logger` | module global | `logger = get_logger("execution_agent")` | `agents/execution_agent.py:23` |

**The six-member engine interface, and every call site.** `self.engine` is duck-typed — never annotated, never imported. `run.py:396-401` passes either `self.paper_engine` or the `LiveExecutor` built by `_build_live_executor()`. The six members required are `update_price`, `get_price`, `execute_order`, `check_positions`, `position_manager`, and the private `_last_prices` dict.

| Member | Call site(s) in this file |
|---|---|
| `self.engine.update_price(...)` | `:64` (per drained `PRICE_UPDATE` event), `:71` (per `market_store` snapshot), `:100` (backfill after `market_store.get_price` fallback) |
| `self.engine.get_price(...)` | `:96` — `price = self.engine.get_price(order.symbol)` |
| `self.engine.execute_order(...)` | `:164` — `result = await self.engine.execute_order(order)` |
| `self.engine.check_positions()` | `:192` — last step of `check_positions()` |
| `self.engine._last_prices.get(symbol)` | `:213` (`_protect_breakeven`), `:266` (`_auto_close_expired`), `:290` (`_scalp_take_profit`) — the three places the live adapter's `update_price` docstring cites |
| `self.engine.position_manager.close_position(...)` | `:270-272` (`_auto_close_expired`, reason `"SCALP_EXPIRED"`), `:311-313` (`_scalp_take_profit`, reason `"SCALP_TP"`) |

Both implementations now satisfy all six. `PaperTradingEngine`: `update_price` `trading/paper_engine.py:186`, `get_price` `:190`, `execute_order` `:212`, `check_positions` `:1049`, `_last_prices` initialised at `:99`, `position_manager` bound at `:92`. `LiveExecutor`: `update_price` `trading/live/executor.py:210`, `get_price` `:237`, `execute_order` `:397`, `check_positions` `:259`, `_last_prices` at `:110`, `position_manager = _LivePositionManager(self)` at `:115`. The module docstring at `trading/live/executor.py:1-10` states the intent directly: the agent is not changed between paper and live, and the safety differences live inside the adapter. `executor.py:214-221` records why `update_price` is non-trivial on live — returning `None` there would make `check_positions()` raise `AttributeError` on the three `_last_prices` reads and silently disable all three scalp policies.

**Where a market price is handed to `close_position`.** Both scalp exits pass a *market* mid price, not a fill: `:271` `await self.engine.position_manager.close_position(pos["id"], price, "SCALP_EXPIRED")` and `:312` `await self.engine.position_manager.close_position(pos["id"], price, "SCALP_TP")`, where `price` comes from `market_store.get_price(symbol)` (`:264`, `:288`) falling back to `engine._last_prices` (`:266`, `:290`). Neither call passes the fourth parameter. That is the load-bearing dependency: `PositionManager.close_position` defaults `price_is_final=False` (`trading/position_manager.py:167-173`) and, on that default, charges the exit cost itself by routing the market price through `close_fill_price` (`:216-222`, defined `trading/fill_cost.py:178`, which derives the fill side from the *position* side, `:196`), then charges the taker fee on that fill and subtracts the opening fee already paid (`:235-257`). Its docstring states the rule at `position_manager.py:182-188`: cost is no longer the caller's responsibility, because every exit path — SL, TP, liquidation, scalp TP, expired auto-close, CLOSE order — used to pass the market price through untouched, so the opening cost charged in `_execute_open` never had a partner and paper P&L counted only half a round trip. The live adapter diverges deliberately: `_LivePositionManager.close_position` (`trading/live/executor.py:935`) accepts `price` and **ignores it**, because the exchange mid at submission time is the only real fill (`:948-951`).

**SL/TP computed in `think()` is advisory.** `think()` fetches price (`:96-100`), then for `OPEN_LONG`/`OPEN_SHORT` calls `analysis.volatility.get_dynamic_tp_sl_thresholds` (`:119-121`) and writes `order.stop_loss` / `order.take_profit` via `RiskManager.calculate_stop_loss` / `calculate_take_profit` (`:139-140`). The comment at `:105-116` is explicit that the engine is authoritative — the values computed here are overwritten by `PaperTradingEngine._execute_open` before persisting — and gives two reasons for computing them anyway: `DecisionAgent` reads `order.stop_loss_pct` for its reasoning, and if the engine's calculation is rejected by the volatility gate this value is the trace of what was actually computed. The non-dynamic branch (`:132-137`) falls back to the payload's `stop_loss_pct` / `take_profit_pct`, then to `self.scalp.tight_sl_pct` / `self.scalp.fast_tp_pct`.

**Ordering in `check_positions()` is deliberate.** `:183` `_protect_breakeven()` → `:186` `_scalp_take_profit()` → `:189` `_auto_close_expired()` → `:192` `engine.check_positions()`. The docstring at `:199-201` requires `breakeven_trigger_pct` (0.0020, `core/config.py:150`) to sit below `min_profit_pct` (0.0060, `:106`); if they were equal, TP would fire first in the same cycle and the whole breakeven branch would be dead code.

**State.** `self.engine`, `self.risk_manager = RiskManager()` (its own instance, not the one the decision agent holds), `self.config = get_config()`, `self.scalp = self.config.scalping`, `self._decision_queue` and `self._price_queue` (both `None` until `initialize()` subscribes to `Channels.TRADE_DECISION` and `Channels.PRICE_UPDATE` at `:48-49`). The three queue-drain loops at `:57`, `:74`, plus `:77`/`:79` use `not ... .empty()` then `get_nowait()` and `break` out of the loop on any exception — drain never propagates.

**Side effects.** DB reads: `repo.get_open_positions()` at `:204`, `:246`, `:282`. DB write: `repo.update_position_sl_tp(pos["id"], stop_loss=be_sl)` at `:227` (LONG) and `:237` (SHORT) — the only write the agent makes itself. Engine calls as tabled above; `execute_order` results are read for `result["success"]` (`:166`) and `result["message"]` (`:169`). `market_store` reads (`:69`, `:98`, `:211`, `:264`, `:288`). No direct network. Note the unused imports at the top: `json` (`:10`), `Dict`, `Optional` (`:12`), and `Channels` is used only for the two `subscribe` calls.

**Consumers.** `run.py:61` (import), `run.py:401` (construction with the mode-dependent executor), `run.py:420` (`initialize()`), `run.py:652` (`run_cycle()`), `run.py:654` (`check_positions()`). Tests: `tests/test_live_executor.py:219-222` asserts the agent accepts a generic engine; `tests/test_advanced_modules.py:351-356` asserts the source uses dynamic thresholds.

### 13. `agents/decision_agent.py`

**Role.** The scalping decision maker: reads the ensemble direction snapshot per symbol, emits `OPEN_LONG` / `OPEN_SHORT` / `CLOSE` decisions under batch, cooldown, confidence, spread and one-position-per-symbol limits, and — the load-bearing part — performs position sizing so that every order arrives at any engine with a concrete quantity.

**Symbols.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `_NON_SCALP_SL_PCT` | module constant | `_NON_SCALP_SL_PCT = 0.02` | `agents/decision_agent.py:38` |
| `_NON_SCALP_TP_PCT` | module constant | `_NON_SCALP_TP_PCT = 0.04` | `agents/decision_agent.py:39` |
| `DecisionAgent` | class | `class DecisionAgent(BaseAgent):` | `agents/decision_agent.py:42` |
| `DecisionAgent.__init__` | method | `def __init__(self, event_bus: EventBus, risk_manager: RiskManager):` | `agents/decision_agent.py:52` |
| `DecisionAgent.initialize` | method | `async def initialize(self):` | `agents/decision_agent.py:64` |
| `DecisionAgent.sense` | method | `async def sense(self) -> dict:` | `agents/decision_agent.py:69` |
| `DecisionAgent.think` | method | `async def think(self, data: dict) -> dict:` | `agents/decision_agent.py:107` |
| `DecisionAgent.act` | method | `async def act(self, analysis: dict):` | `agents/decision_agent.py:273` |
| `DecisionAgent._generate_scalp_signals` | method | `async def _generate_scalp_signals(\n        self, symbol: str, analysis: Dict\n    ) -> List[Dict]:` | `agents/decision_agent.py:458` |
| `DecisionAgent._snapshot_is_fresh` | method | `def _snapshot_is_fresh(self, created_at) -> bool:` | `agents/decision_agent.py:509` |
| `DecisionAgent._snapshot_agreement` | static | `@staticmethod\n    def _snapshot_agreement(snap: Dict) -> str:` | `agents/decision_agent.py:517-518` |
| `DecisionAgent._determine_leverage` | method | `def _determine_leverage(self, risk_level: str) -> int:` | `agents/decision_agent.py:543` |
| `logger` | module global | `logger = get_logger("decision_agent")` | `agents/decision_agent.py:24` |

**The sizing call.** It lives in `act()`, not `think()`, at `agents/decision_agent.py:375-397`, and it is the only place every order passes through:

- scalp on (`scalp_on` from `cfg.scalping.enabled`, `:291`): `sizing = self.risk_manager.calculate_scalp_position_size(balance=balance, entry_price=price, leverage=leverage)` (`:376-378`).
- scalp off: SL and TP are recomputed from `_NON_SCALP_SL_PCT` / `_NON_SCALP_TP_PCT` (`:385-390`), then `sizing = self.risk_manager.calculate_position_size(balance=balance, entry_price=price, stop_loss_price=stop_loss, leverage=leverage)` (`:391-396`).

The comment at `:315-335` is the reason. `Order.quantity` being `None` used to mean "let the engine compute it", and that path lives only in the paper engine (`trading/paper_engine.py:616-631`, which branches on `order.quantity is None` at `:616`). `LiveExecutor` has no such path: it sends `size=float(order.quantity)` (`trading/live/executor.py:473`) and hard-rejects a non-positive quantity at `:438-449` — the comment there records that the old `size=order.quantity or 0.0` turned `None` into a real zero-size order. So `act()` is the single point every order crosses before reaching either engine, and it is the only point that also holds the same `RiskManager` both engines use. The step order — price, then leverage, then SL/TP, then sizing, then the validity gate — is required to be identical to the paper engine's; changing it makes paper and live size the same signal differently. Price and leverage are each read in their own `try` (`:336-339`, `:344-347`) because the feed can put anything in `market_store` and an escaping exception would kill the whole cycle, not just this order.

**`Order` fields now set.** The `Order` is constructed at `:304-312` with `symbol`, `action`, `side`, `leverage`, `stop_loss=None`, `take_profit=None`, `reasoning` — and no `quantity`, no `price`. For `OPEN_LONG`/`OPEN_SHORT` only, after the validity gate passes, five fields are assigned at `:432-436`: `order.quantity = quantity`, `order.leverage = leverage`, `order.stop_loss = stop_loss`, `order.take_profit = take_profit`, `order.price = price`. The `opens` counter increments at `:437`.

**The four skip gates, with their exact log messages.** All four are `self.logger.warning`, all emit the prefix `f"{decision.action.value} {decision.symbol} dilewati: "`, all `continue` without publishing. Three are per-order; the fourth is the aggregate validity gate.

1. **No market price** (`:349-354`) — `f"{decision.action.value} {decision.symbol} dilewati: tidak ada harga pasar (harga={price})"`, triggered by `not math.isfinite(price) or price <= 0`.
2. **Invalid leverage** (`:356-361`) — `f"{decision.action.value} {decision.symbol} dilewati: leverage tidak valid (leverage={leverage})"`, triggered by `leverage < 1` (the `except (TypeError, ValueError): leverage = 0` at `:346-347` is what routes a non-castable value here instead of raising).
3. **Account not initialised** (`:363-368`) — `f"{decision.action.value} {decision.symbol} dilewati: akun belum diinisialisasi"`, triggered by `balance is None` (set at `:283-288` when `repo.get_account()` returns `None` or `balance` is not castable).
4. **Validity problems** (`:399-430`) — `f"{decision.action.value} {decision.symbol} dilewati: {'; '.join(problems)}"`, built by accumulating into `problems` (`:407-423`): `f"quantity tidak valid ({quantity})"`, `f"stop loss tidak valid ({stop_loss})"`, `f"take profit tidak valid ({take_profit})"`, `f"SL/TP terbalik untuk LONG (sl={stop_loss}, price={price}, tp={take_profit})"`, `f"SL/TP terbalik untuk SHORT (sl={stop_loss}, price={price}, tp={take_profit})"`. The comment at `:399-406` argues this gate is mandatory here rather than left to the engine: a NaN quantity reaches Hyperliquid with no clear parameter name attached, and an SL above entry on a LONG is a stop the exchange executes in milliseconds — real loss in the first second.

**New payload keys.** The publish at `:439-448` is `await self.publish(Channels.TRADE_DECISION, {...})` and carries `order`, `decision`, `stop_loss_pct`, `take_profit_pct`, plus the two new keys `quantity` and `entry_price` (`:446-447`), read back as `order.quantity` and `order.price`. The comment at `:444-445` gives the reason: sizing must be auditable from outside — without these there is no trace of the number actually sent to the exchange. `ExecutionAgent.think` consumes the same payload (it reads `order`, `decision`, `stop_loss_pct`, `take_profit_pct` at `agents/execution_agent.py:89-90`, `:132-137`).

**Why `CLOSE` orders are untouched.** The whole `if decision.action in (TradeAction.OPEN_LONG, TradeAction.OPEN_SHORT):` block at `:314-437` is skipped for `TradeAction.CLOSE`, so a CLOSE order leaves the constructor at `:304-312` with `quantity=None`, `price=None`, `stop_loss=None`, `take_profit=None` — exactly the shape the engines need, because a close is routed by `order.position_id` / symbol, not by a size. `TradeAction.HOLD` is skipped earlier at `:301-302` and never becomes an `Order` at all. The `opens` / `closes` counters (`:437`, `:450`) are only used in the summary log `f"Scalp: {opens} posisi baru, {closes} ditutup"` (`:454-456`), and the comment at `:293-296` explains that they count what was actually **published**, not what `decisions` contained, so the log cannot claim positions that a validity gate rejected.

**The `_NON_SCALP_SL_PCT` / `_NON_SCALP_TP_PCT` mirror, current line numbers on both sides.**

| Side | What | Loc |
|---|---|---|
| This file, constants | `_NON_SCALP_SL_PCT = 0.02`, `_NON_SCALP_TP_PCT = 0.04` | `agents/decision_agent.py:38-39` |
| This file, header comment naming the other side | "Salinan literal dari `PaperTradingEngine._execute_open` (`trading/paper_engine.py:477-478`)" | `agents/decision_agent.py:28-29` |
| Paper engine, the literals themselves | `sl_pct = 0.02` / `tp_pct = 0.04` in the non-scalp `else` branch | `trading/paper_engine.py:548-549` |
| This file, the non-scalp use site | `calculate_stop_loss(price, side_str, _NON_SCALP_SL_PCT)` / `calculate_take_profit(price, side_str, _NON_SCALP_TP_PCT)` | `agents/decision_agent.py:385-390` |
| This file, in-code restatement of the other side's line numbers | "sama seperti `paper_engine.py:477-478`" | `agents/decision_agent.py:383` |

The values agree (`0.02` / `0.04` on both sides) but the cited line numbers do not: the header comment at `:28-29` and the in-code note at `:383` both say `paper_engine.py:477-478`, while the literals actually live at `paper_engine.py:548-549`. `paper_engine.py:477` today reads `problems.append("leverage harus > 0 (dapat {})".format(lev))` inside `_execute_open`'s input validation. The rationale in the header comment (`:30-37`) is unchanged and still correct: those numbers are hardcoded on the engine side and must not be replaced with `stop_loss_pct` from config, because sizing against a 0.25% stop while the engine installs a 2% stop inflates notional 8× and the real risk then exceeds what risk management approved. The stated consequence — both sides must change together, and the engine side is out of scope so changes there must be hand-matched — is exactly what the stale line numbers will defeat.

**Other gates in `think()`.** Not the "four skip gates" but worth naming: batch limit `min(self.scalp.batch_size, max_new)` at `:136` where `max_new = max(0, self.config.risk.max_open_positions - open_count)`; one position per symbol at `:232-234`; per-symbol cooldown at `:213-214`, set in `sense()` at `:98` as `cooldown_after_loss_seconds * min(streak, 4)` on a loss or `cooldown_after_close_seconds` on a win (`:90-98`); reversal close requires `sig["strength"] >= reversal_threshold` (0.70, `core/config.py:120`) at `:168`/`:170`, deliberately above `min_confidence` (0.40) per the comment at `:146-153`; spread gate at `:221-229` against `max_spread_pct`. Signals come exclusively from `repo.get_latest_direction_snapshot` (`:473`), rejected if older than `config.ensemble.max_snapshot_age_seconds` (`:477-482`, `:509-515`). `us_market_open` is returned by `sense()` (`:104`) but not read by `think()`.

**State.** `self.risk_manager` (injected, shared with the paper engine — `run.py:389`), `self.config`, `self.scalp`, `self._analysis_queue` / `self._position_queue` (`None` until `initialize()` subscribes to `Channels.MARKET_ANALYSIS` and `Channels.POSITION_UPDATE` at `:65-66`), `self._latest_analyses: Dict[str, Dict]` (latest snapshot per symbol, overwritten in `sense()` at `:76`, shallow-copied into the return value at `:103`), `self._symbol_cooldowns: Dict[str, float]` (absolute `time.time()` deadlines), `self._symbol_loss_streak: Dict[str, int]`, `self._global_loss_streak: int` (incremented at `:94`, never read). Unused imports: `Optional` (`:11`), `Signal` (`:20`).

**Side effects.** DB reads only: `repo.get_account()` (`:282`), `repo.get_open_positions()` (`:133`), `repo.get_latest_direction_snapshot` (`:473`). DB writes: none directly — but the indirect write is significant, because this agent's `order` objects drive `PositionManager.open_position` and `close_position`. `market_store` reads: `get_price` (`:337`), `get_order_book` (`:221`). One event-bus publish per non-skipped decision (`:439`). No network. `BaseAgent._log_cycle` then writes a `CYCLE` row per cycle with `input_summary` `{"symbols": [...], "open": open_count}` and `output_summary` `{"new_positions": opened, "closes": ...}` (`:266-270`).

**Consumers.** `run.py:60` (import), `run.py:389` (constructed with `self.paper_engine.risk_manager`), `run.py:419` (`initialize()`). The decision loop itself is driven by `run.py`, not by `check_positions`; tests: `tests/test_decision_agent_ensemble.py:24` (ensemble-driven trading) and `tests/test_bugfixes.py:30` (source-level assertions against `agents/decision_agent.py`).

## Signal layer

Seven modules under `analysis/`, re-derived from source. Imports are only `core.{config,logger,market_store}` plus `core.microstructure` for `probability_engine`.

### 14. `analysis/technical.py`

**Role.** pandas-ta wrapper that stamps every indicator column onto the OHLCV DataFrame in one pass and then scores seven indicator groups into a single `direction` + `confidence`.

**Symbols.**

| Symbol | Kind | Signature | Loc |
| --- | --- | --- | --- |
| `logger` | module global | `logger = get_logger("technical")` | `analysis/technical.py:14` |
| `TechnicalAnalyzer` | class | `class TechnicalAnalyzer:` | `analysis/technical.py:17` |
| `.` | method | `def __init__(self):` | `analysis/technical.py:30` |
| `.calculate_indicators` | method | `def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:` | `analysis/technical.py:41` |
| `.generate_signals` | method | `def generate_signals(self, df: pd.DataFrame) -> Dict:` | `analysis/technical.py:94` |
| `.ohlcv_to_dataframe` | staticmethod | `def ohlcv_to_dataframe(ohlcv_data: List[list]) -> pd.DataFrame:` | `analysis/technical.py:302` (decorator `:301`) |

**Indicator periods.** `__init__` reads `get_config().indicators` (`:31-39`): RSI 14, MACD 12/26/9, Bollinger 20 × std 2, EMA 9/21 (`core/config.py:60-67`, `config.yaml:84-91`). Scalping constants are **hardcoded, not configurable** — `rsi_fast length=7` (`:59`), `ema_3 length=3` (`:80`), `ema_5 length=5` (`:81`), `roc_5 length=5` (`:84`), `vol_sma length=20` (`:87`), `atr length=14` (`:90`); the 7/3/5 pairs appear nowhere in config.

**Guard.** Returns the frame unchanged when `len(df) < self.macd_slow + self.macd_signal` (`:51-53`) — **35 bars** at defaults. Columns are positional: `macd.iloc[:, 0..2]` → `macd`, `macd_hist`, `macd_signal` (`:64-66`); `bb.iloc[:, 0..2]` → `bb_lower`, `bb_mid`, `bb_upper` (`:71-73`).

**Scoring weights** in `generate_signals`, verbatim: RSI `<30` +1 bull / `>70` +1 bear / `<45` +0.5 bull / `>55` +0.5 bear (`:125-138`); `rsi_fast` `<25` ±1.5 / `>75` ±1.5 / `<40` +0.3 / `>60` -0.3 (`:144-157`); MACD histogram sign cross ±1.5, same-sign-only ±0.5 (`:167-178`); Bollinger band ±1, inside-band 0 (`:188-196`); EMA 9/21 cross ±1.5, above/below ±0.5 (`:206-217`); EMA 3/5 cross ±1.0, above/below ±0.3 (`:227-238`); ROC `>0.5` +1.0 / `>0.1` +0.3 / `<-0.5` -1.0 / `<-0.1` -0.3 (`:244-257`); volume above 1.5× or below 0.5× `vol_sma` labels only, adding nothing to either counter (`:266-271`).

**Net → direction.** `net = bullish_count - bearish_count`; `max_score = max(total_signals * 1.5, 1)`; `confidence = min(net / max_score, 1.0)` for `net > 0.5`, `min(abs(net) / max_score, 1.0)` for `net < -0.5`, else `"NEUTRAL"` at `0.0` (`:274-285`). Volume counts toward `total_signals` (`:264`) but can never move `net`, inflating the denominator with a zero-weight input. Also returns `bullish_score` / `bearish_score` (`:297-298`).

**State.** The eight period attributes. `calculate_indicators` **mutates and returns the caller's DataFrame in place**.

**Side effects.** None beyond that; one `logger.warning` on the short-frame path (`:52`).

**Consumers.** `agents/analysis_agent.py:17` (`ohlcv_to_dataframe` `:97`, `calculate_indicators` `:100`, `generate_signals` `:103`); `agents/direction_agents.py:37` (`TechnicalAgent`, `:197`, `calculate_indicators` `:223`); `dashboard/callbacks/update_callbacks.py:26` (import only); `tests/test_indicators.py:9`.

### 15. `analysis/fundamental.py`

**Role.** Pure scorecard that folds macro context, aggregate news sentiment and the economic calendar into a `direction` / `confidence` / `risk_level` triple.

**Symbols.**

| Symbol | Kind | Signature | Loc |
| --- | --- | --- | --- |
| `logger` | module global | `logger = get_logger("fundamental")` | `analysis/fundamental.py:9` |
| `FundamentalAnalyzer` | class | `class FundamentalAnalyzer:` | `analysis/fundamental.py:12` |
| `.` | method | `def __init__(self):` | `analysis/fundamental.py:18` |
| `.update_macro` | method | `def update_macro(self, macro_context: Dict):` | `analysis/fundamental.py:23` |
| `.update_sentiment` | method | `def update_sentiment(self, sentiment_aggregate: Dict):` | `analysis/fundamental.py:27` |
| `.update_calendar` | method | `def update_calendar(self, events: List[dict]):` | `analysis/fundamental.py:31` |
| `.analyze` | method | `def analyze(self) -> Dict:` | `analysis/fundamental.py:35` |
| `.should_reduce_risk` | method | `def should_reduce_risk(self) -> bool:` | `analysis/fundamental.py:135` |
| `.get_suggested_leverage` | method | `def get_suggested_leverage(self, default: int = 5) -> int:` | `analysis/fundamental.py:143` |

**Weights and thresholds.** Macro `bias == "BULLISH"` → `bullish_score += 2`, `"BEARISH"` → `bearish_score += 2` (`:58-61`). Sentiment on `avg_score`: `> 0.2` +2 bull, `> 0.05` +1 bull, `< -0.2` +2 bear, `< -0.05` +1 bear, else a neutral factor only (`:76-89`); requires `count > 0` (`:72`). Calendar: **three or more** `impact == "High"` events → `bearish_score += 1`, `risk_level = "HIGH"` (`:97-102`). `risk_level` otherwise comes from `macro.get("risk_level", "MEDIUM")` (`:64`), defaulting `"MEDIUM"` on empty macro (`:67`).

**Net → direction.** `net >= 2` BULLISH, `net <= -2` BEARISH, else NEUTRAL; confidence `min(net / 6, 1.0)`, `min(abs(net) / 6, 1.0)`, and a **hardcoded `0.2`** for NEUTRAL (`:107-115`). `should_reduce_risk` = `risk_level == "HIGH"` **or** (`BEARISH` and confidence > 0.5) (`:138-141`). `get_suggested_leverage`: HIGH → `max(1, default // 3)`, MEDIUM → `max(1, default // 2)`, LOW → `default` (`:148-153`).

**State.** `_macro_context`, `_sentiment_aggregate`, `_calendar_events`, replaced wholesale by the `update_*` setters. `should_reduce_risk` and `get_suggested_leverage` each re-run all of `analyze`.

**Side effects.** None. `logger` is bound and never called.

**Consumers.** `agents/analysis_agent.py:18` only (`update_macro` `:106`, `update_sentiment` `:112`, `update_calendar` `:216`, `analyze` `:113`). `should_reduce_risk` and `get_suggested_leverage` have **zero callers**, tests included.

### 16. `analysis/ml_signals.py`

**Role.** Loads a pickled RandomForest and converts the technical signal dict into a 7-feature vector; when no model is loaded, falls back to a hand-weighted sigmoid score.

**Symbols.**

| Symbol | Kind | Signature | Loc |
| --- | --- | --- | --- |
| `MODEL_DIR` | module const | `MODEL_DIR = Path("ml/models")` | `analysis/ml_signals.py:20` |
| `logger` | module global | `logger = get_logger("ml_signals")` | `analysis/ml_signals.py:18` |
| `MLSignalGenerator` | class | `class MLSignalGenerator:` | `analysis/ml_signals.py:23` |
| `.` | method | `def __init__(self):` | `analysis/ml_signals.py:35` |
| `.initialize` | method | `async def initialize(self):` | `analysis/ml_signals.py:43` |
| `._load_model` | method | `def _load_model(self):` | `analysis/ml_signals.py:52` |
| `.extract_features` | method | `def extract_features(` | `analysis/ml_signals.py:63` — `self, technical_signals: Dict, sentiment_score: float = 0.0, df: pd.DataFrame = None,` → `) -> Optional[np.ndarray]:` (`:68`) |
| `.predict` | method | `def predict(` | `analysis/ml_signals.py:137` — same three params, `:137-142`, `) -> Dict:` (`:142`) |
| `._predict_ml` | method | `def _predict_ml(self, features: np.ndarray) -> Dict:` | `analysis/ml_signals.py:169` |
| `._predict_rule_based` | method | `def _predict_rule_based(self, features: np.ndarray) -> Dict:` | `analysis/ml_signals.py:189` |

**Feature vector** (`_feature_names`, `:37-40`), order mirrored in `_predict_rule_based`'s unpacking at `:195`: `[rsi, macd_hist, bb_position, ema_trend, volume_ratio, sentiment_score, atr_pct]`.

**Feature formulas, verbatim.** RSI `/100.0`, fallback `0.5` (`:79`). MACD `np.clip(macd_hist / 100, -1, 1)`, `0` when `abs(macd_hist) == 0` (`:86`) — a literal 100 divisor with no price or ATR normalization, so any instrument above 100 pins the histogram near zero. BB position parsed as float, `LOWER_BAND`→`0.0`, `UPPER_BAND`→`1.0`, else `0.5` (`:91-99`). EMA trend `±1.0` for a cross, `±0.5` otherwise, `0.0` for NEUTRAL (`:104-109`). Volume `float(str(v).replace("x", ""))` then `min(volume_ratio / 3.0, 1.0)` (`:115-118`). Sentiment `np.clip(sentiment_score, -1, 1)` (`:121`). ATR `min(float(last["atr"] / last["close"]), 0.1) * 10`, default `0.5` (`:124-128`). Output `reshape(1, -1)` (`:133`).

**Rule-based scorer** (`:195-257`): RSI `<0.3` +1.5 / `>0.7` -1.5 / `<0.45` +0.5 / `>0.55` -0.5; `score += macd * 2.0` (`:210`); BB `<0.2` +1.0 / `>0.8` -1.0; `score += ema * 1.5` (`:219`); `score += sent * 1.0` (`:222`). Then `p_long = sigmoid(score)`, `p_short = sigmoid(-score)`, `p_hold = 1 - abs(p_long - p_short)` (`:226-228`), renormalized by their sum (`:231-234`), decision `threshold = 0.45` (`:237`) — an **independent** threshold, unrelated to the 0.45 in `probability_engine`; neither has a derivation.

**State.** `_model`, `_feature_names`, `_model_loaded`. `initialize` offloads `_load_model` to `loop.run_in_executor(None, ...)` (`:47`) and swallows any exception into `logger.info` (`:48-50`).

**Side effects.** Reads `ml/models/signal_model.pkl` via `joblib.load` (`:54-59`); `MODEL_DIR` is **relative**, so resolution depends on process CWD.

**Consumers.** `agents/analysis_agent.py:19` (`initialize()` `:61`, `predict(...)` `:116-120`); `ml/predictor.py:11` (thin wrapper, itself unimported by production code).

### 17. `analysis/probability_engine.py`

**Role.** Multi-factor logit fusing technical, sentiment, ML, L2 order-flow and macro into one bullish probability, plus the drift-diffusion curve and expectancy figure the dashboard renders.

**Symbols.**

| Symbol | Kind | Signature | Loc |
| --- | --- | --- | --- |
| `norm_cdf` | function | `def norm_cdf(x: float) -> float:` | `analysis/probability_engine.py:35` |
| `calculate_order_flow_imbalance` | function | `def calculate_order_flow_imbalance(` | `analysis/probability_engine.py:40` — `order_book: Optional[Dict], symbol: str = None,` → `) -> Tuple[float, float]:` (`:43`) |
| `calculate_technical_zscore` | function | `def calculate_technical_zscore(indicators: Dict, current_price: float) -> float:` | `analysis/probability_engine.py:93` |
| `calculate_realized_volatility` | function | `def calculate_realized_volatility(df: pd.DataFrame, window: int = 30) -> float:` | `analysis/probability_engine.py:139` |
| `QuantitativeProbabilityEngine` | class | `class QuantitativeProbabilityEngine:` | `analysis/probability_engine.py:155` |
| `.` | method | `def __init__(self):` | `analysis/probability_engine.py:161` |
| `.compute_composite_probability` | method | `def compute_composite_probability(` | `analysis/probability_engine.py:171` — `self, indicators: Dict, current_price: float, sentiment_score: float = 0.0, ml_prediction: Optional[Dict] = None, order_book: Optional[Dict] = None, macro_bias: str = "NEUTRAL",` → `) -> Dict:` (`:179`) |
| `.compute_directional_curve` | method | `def compute_directional_curve(` | `analysis/probability_engine.py:270` — `self, prob_long: float, realized_vol_per_min: float, horizon_minutes: int = 30, num_points: int = 60,` → `) -> Dict:` (`:276`) |
| `.compute_diffusion_curve` | method | `def compute_diffusion_curve(` | `analysis/probability_engine.py:326` — `self, prob_bullish: float, realized_vol_per_min: float, horizon_minutes: int = 60, num_points: int = 80,` → `) -> Dict:` (`:332`) |
| `.compute_mathematical_edge` | method | `def compute_mathematical_edge(` | `analysis/probability_engine.py:386` — `self, trades: List[Dict], current_winner_odds: float = 0.55, target_rr_ratio: float = 1.5,` → `) -> float:` (`:391`) |
| `probability_engine` | module singleton | `probability_engine = QuantitativeProbabilityEngine()` | `analysis/probability_engine.py:433` |

**Docstring formula vs implemented formula.** The header at `:17-20` claims a Cox-Ross-Rubinstein "term-structure" computation across `T in [1m, 60m]`:

```
P(S_t > S_0) = Phi( ((mu - 0.5 * sigma^2) * t) / (sigma * sqrt(t)) )
```

What both curve functions actually compute (identical `d2` in both, `:307` and `:366`):

```
d2 = ((mu - 0.5 * sigma**2) * t) / (sigma * sqrt(t))
p_terminal = norm_cdf(d2)                     # 0.5 * (1 + erf(x / sqrt(2)))   :37
weight_t = 1 - exp(-t * 0.08)                  # :311 / :372
p = (1 - weight_t) * p_clamped + weight_t * p_terminal
```

Three differences matter. The grid is `np.linspace(0.2, horizon_minutes, num_points)` — **starts at 0.2 minutes, not 1** (`:301`, `:355`). `mu` is not a market drift estimate at all but `z_score * 0.45 * sigma`, Bayesian log-odds rescaled by realized vol. And there is no `S_0`/`S_t` anywhere — the output is a probability, not a price path; `-0.5 * sigma**2` is the risk-neutral Brownian drift adjustment, not a CRR lattice.

**`drift_per_min` — two occurrences, two behaviours.** `:299` in `compute_directional_curve`, `:353` in the deprecated `compute_diffusion_curve`, both reading `drift_per_min = z_score * 0.45 * sigma`. Byte-identical; the divergence is downstream. `:360` takes `effective_mu = abs(drift_per_min)` and `:374` clips `p_win` to `[0.50, 0.999]`, so the deprecated path cannot express a bearish curve at all (`p_bullish` 0.35 and 0.65 give identical output). `:316` clips the live path to `[0.001, 0.999]` and returns `short_probs = 1 - p_long` (`:318`), so a negative `z_score` genuinely descends.

**The `0.45` has no derivation anywhere.** It appears twice here (`:299`, `:353`) and once more as an unrelated decision threshold in `ml_signals.py:237`. Repo-wide grep for `0.45` in Python returns only those three sites plus test fixtures (`tests/test_direction_ensemble.py:86`, `:90`; `tests/test_probability_engine.py:64`). It is in neither `core/config.py` nor `config.yaml`; no docstring mentions it, no comment explains it. It sits between `z_score` (unitless log-odds, unbounded) and `sigma` (per-minute vol ~1e-3), so it sets the absolute slope of every diffusion curve: at most `0.45 * 2.5 * 0.0018 ≈ 0.002` drift per minute. Unexplained tunable, not a fitted parameter.

**Factor Z-scores.** Weights, `__init__` `:163-169`: technical `0.30`, sentiment `0.20`, ml `0.25`, orderbook `0.15`, macro `0.10` — sum 1.00, **hardcoded, absent from `EnsembleConfig`**, so this path and the log-odds ensemble use unrelated weight sets.

- RSI `(rsi - 50.0) / 20.0`, `np.clip(..., -2.0, 2.0)` (`:107-108`).
- MACD `denom = atr if valid else max(current_price * 0.002, 1e-4)`; `(macd_hist / denom) * 1.5`, clipped ±2.0 (`:114-116`).
- EMA `diff_pct = (ema_s - ema_l) / ema_l`; `z_ema = diff_pct * 100.0`, clipped ±2.0 (`:122-124`).
- Bollinger `z_bb = (pct_b - 0.5) * 2.5`, clipped ±2.0 (`:132-134`).
- `z_tech = float(np.mean(sub_z))` — plain mean over whichever sub-z's were present (`:136`), so a frame with only RSI scores the same as one with all four.
- Sentiment `np.clip(sentiment_score * 2.2, -2.5, 2.5)` (`:200`).
- ML `log(p_long / p_short)` when both probs exist (each floored at `1e-4`); else match the action string against `("BULL","BUY","LONG")` / `("BEAR","SELL","SHORT")` → `±conf * 2.0`; clipped ±2.5 (`:208-220`).
- Orderbook `np.clip(ofi * 2.5, -2.5, 2.5)` (`:224`); macro substring test `"BULLISH"` → `+0.8`, `"BEARISH"` → `-0.8`, else `0.0` (`:227-233`).

Composite `z_comp = Σ w_i · z_i` (`:236-242`), `prob_bullish = 1.0 / (1.0 + math.exp(-z_comp))` (`:245`), `prob_bearish = 1 - prob_bullish` (`:246`), `direction = "BULLISH" if prob_bullish >= 0.5 else "BEARISH"` (`:250`).

**OFI is a facade, not a computation.** `calculate_order_flow_imbalance` (`:40`) holds no arithmetic; missing/empty book → `(0.0, 0.0)` (`:72-79`). Without a symbol it builds a throwaway `PythonKernel()`, ingests under the sentinel `"__ad_hoc__"`, reads back at `depth=5` (`:84-87`); with one it calls module-level `ingest_l2` then `order_flow_imbalance` (`:89-90`). The math lives in `core/microstructure.py:130`: level-weighted volume `size * (1.0 - 0.1 * i)` over the top `depth` levels (`:123-128`), `ofi = (bid_vol - ask_vol) / denom` clamped to `[-1, 1]` (`:148-150`), `rel_spread = max(best_ask - best_bid, 0.0) / mid` (`:157`).

**`calculate_realized_volatility`** (`:139`): `std(ddof=1)` of `log(close/close.shift(1))` over the last `window` closes; returns `0.0015` on short input or `<3` returns (`:143-149`), else `max(vol, 0.0002)` (`:152`). Unlike `analysis/volatility.py`, it cannot signal "unknown".

**`compute_mathematical_edge`** (`:386`): with `>= 2` closed trades, `avg_notional = max(mean(price*quantity), 1.0)` (per-trade defaults 100/1, `:415`), `edge = win_rate*avg_win - loss_rate*avg_loss`, returns `clip(edge / avg_notional * 100.0, -10.0, 30.0)` (`:420-422`). Else `p_win = clip(current_winner_odds, 0.50, 0.95)`, `edge = p_win*target_rr_ratio - (1 - p_win)`, returns `clip(edge * 1.25, 0.10, 5.0)` (`:425-429`).

**State.** Only `self.weights` per instance; the `probability_engine` singleton at `:433` is process-wide.

**Side effects.** `calculate_order_flow_imbalance` **mutates the global `core.microstructure` kernel cache** whenever `symbol` is not None (`:89`). `compute_composite_probability` calls it *without* a symbol (`:223`), allocating a throwaway kernel per call.

**Consumers.** `agents/direction_agents.py:115` (`calculate_order_flow_imbalance` with `symbol=`, `:125`), `:216` (`calculate_technical_zscore`, `:235`), `:483` (`compute_directional_curve`, `:485`); `dashboard/callbacks/update_callbacks.py:20-25` (`compute_mathematical_edge` `:373`, `:1181`; `calculate_order_flow_imbalance` `:1311`); `dashboard/layouts/hud_figures.py:223` (`compute_directional_curve` `:224`, hardcoded `realized_vol_per_min=0.0018`); `tests/test_probability_engine.py:8`, `tests/test_bugfixes.py:954`. **`compute_composite_probability` (`:171`) and `compute_diffusion_curve` (`:326`) have no production caller** — the Bayesian five-factor path is dead in the running bot, which uses `direction_ensemble`; the diffusion variant is deprecated in its own docstring (`:334`) but retained.

### 18. `analysis/direction_ensemble.py`

**Role.** I/O-free arbiter for the four direction specialists: converts each verdict to a bounded signed z, agreement-weights them, pools in log-odds, shrinks, sigmoids once.

**Symbols.**

| Symbol | Kind | Signature | Loc |
| --- | --- | --- | --- |
| `MAX_AGENT_Z` | module const | `MAX_AGENT_Z = 2.5` | `analysis/direction_ensemble.py:32` |
| `DIRECTION_LONG` | module const | `DIRECTION_LONG = "LONG"` | `analysis/direction_ensemble.py:34` |
| `DIRECTION_SHORT` | module const | `DIRECTION_SHORT = "SHORT"` | `analysis/direction_ensemble.py:35` |
| `DIRECTION_NEUTRAL` | module const | `DIRECTION_NEUTRAL = "NEUTRAL"` | `analysis/direction_ensemble.py:36` |
| `_sigmoid` | function | `def _sigmoid(z: float) -> float:` | `analysis/direction_ensemble.py:39` |
| `_normalize_z` | function | `def _normalize_z(direction: str, confidence: float) -> float:` | `analysis/direction_ensemble.py:47` |
| `agreement_score` | function | `def agreement_score(verdict: dict, peers: List[dict]) -> float:` | `analysis/direction_ensemble.py:63` |
| `aggregate` | function | `def aggregate(verdicts: List[dict], cfg) -> dict:` | `analysis/direction_ensemble.py:83` |
| `make_verdict` | function | `def make_verdict(` | `analysis/direction_ensemble.py:194` — `agent: str, symbol: str, direction: str, confidence: float, reasoning: str = "", factors: Optional[dict] = None, abstained: bool = False,` → `) -> dict:` (`:202`) |
| `abstain` | function | `def abstain(agent: str, symbol: str, reason: str = "tidak ada data") -> dict:` | `analysis/direction_ensemble.py:218` |

**Math.** `_normalize_z`: `conf = clip(confidence, 0, 1)`, `magnitude = MAX_AGENT_Z * conf`, sign from direction, NEUTRAL or unrecognised → `0.0` (`:54-60`). `_sigmoid` is branch-stable for large `|z|` (`:41-44`).

`aggregate` pipeline: (1) split `abstained=True` from active, `n_abstained = len(all) - len(active)` (`:102-103`); (2) stamp `v["_z"]` **in place on the caller's dicts** (`:107`); (3) empty `active` → hard 0.5/0.5, `confidence 0.0`, `n_agents 0` (`:121-131`); (4) `base = float(cfg.base_weight(agent))`, any weight `<= 0.0` force-marked `v["abstained"] = True` and skipped — unknown/disabled agents leave the pool but still count as abstentions (`:136-142`); (5) `bonus = 1.0 + cfg.agreement_bonus * agreement_score(v, active)`, `weight = base * bonus`, `z = Σ(w·z_v) / Σw` (`:143-146`); (6) `z_shrunk = z * cfg.shrinkage_delta`, `prob_long = _sigmoid(z_shrunk)` (`:163-165`); (7) clamp to `[cfg.min_prob, 1 - cfg.min_prob]` (`:168-169`), `confidence = abs(prob_long - 0.5) * 2.0` (`:172`). Denominator `<= 0` → neutral payload with `agent_breakdown` populated (`:148-159`).

`agreement_score` excludes peers whose z is exactly `0` from **both** numerator and denominator (`:72-73`), so an all-neutral panel scores `0.0` for everyone, not `0.5`. It reads `verdict["_z"]` (`:78`), so calling it on a raw verdict raises `KeyError`.

**Shipped constants.** `EnsembleConfig` (`core/config.py:229-290`), `config.yaml:190-214`: `shrinkage_delta = 0.85`, `agreement_bonus = 0.5`, `min_prob = 0.02` → clamped to `[0.02, 0.98]`, `diffusion_horizon_minutes = 30`, `diffusion_points = 60`, `max_snapshot_age_seconds = 20`, `min_trades_for_microstructure = 8`, `min_returns_for_vol = 20`; weights orderflow `0.30`, momentum `0.25`, technical `0.25`, microstructure `0.20`.

**State.** Module constants only; `aggregate` keeps none but **mutates each input verdict** by adding `_z` (and possibly `abstained`).

**Side effects.** None — no I/O, no globals, no config read. Documented pure at `analysis/direction_ensemble.py:5`.

**Consumers.** `agents/direction_agents.py:29-36` — the three direction constants, `aggregate` (`:478`), `abstain` (`:72`, `:75`, `:213`), `make_verdict` (`:78`); `tests/test_direction_ensemble.py:12`, `tests/test_numerics.py:12`.

### 19. `analysis/volatility.py`

**Role.** Converts static percentage SL/TP into ATR-scaled targets with a hard risk/reward lock, plus the circuit breaker that cancels an order when fees or R:R make it unexecutable. `PaperTradingEngine` and `ExecutionAgent` both depend on this math.

**Symbols.**

| Symbol | Kind | Signature | Loc |
| --- | --- | --- | --- |
| `logger` | module global | `logger = get_logger("volatility")` | `analysis/volatility.py:29` |
| `realized_volatility` | function | `def realized_volatility(symbol: str, window_seconds: float = 30.0) -> Optional[float]:` | `analysis/volatility.py:37` |
| `realized_vol_pct` | function | `def realized_vol_pct(symbol: str, window_seconds: float = 30.0) -> Optional[float]:` | `analysis/volatility.py:70` |
| `atr_1m_from_candles` | function | `def atr_1m_from_candles(candles: List[dict], period: int = 14) -> Optional[Dict[str, float]]:` | `analysis/volatility.py:91` |
| `_ATR_CACHE` | module global | `_ATR_CACHE: Dict[Tuple[str, int], Dict[str, float]] = {}` | `analysis/volatility.py:152` |
| `_ATR_CACHE_BUCKET_SECONDS` | module const | `_ATR_CACHE_BUCKET_SECONDS = 5` | `analysis/volatility.py:153` |
| `_ATR_CACHE_MAX_ENTRIES` | module const | `_ATR_CACHE_MAX_ENTRIES = 256` | `analysis/volatility.py:154` |
| `_cache_key` | function | `def _cache_key(symbol: str) -> Tuple[str, int]:` | `analysis/volatility.py:157` |
| `atr_1m_pct` | function | `def atr_1m_pct(symbol: str, candles: Optional[List[dict]] = None,` / `               period: int = 14) -> Optional[float]:` | `analysis/volatility.py:163-164` |
| `clear_volatility_cache` | function | `def clear_volatility_cache() -> None:` | `analysis/volatility.py:203` |
| `_CANDLE_SOURCE` | module global | `_CANDLE_SOURCE = None  # callable(symbol, timeframe, limit) -> list[dict]` | `analysis/volatility.py:222` |
| `set_candle_source` | function | `def set_candle_source(fn) -> None:` | `analysis/volatility.py:225` |
| `clear_candle_source` | function | `def clear_candle_source() -> None:` | `analysis/volatility.py:243` |
| `_load_candles` | function | `def _load_candles(symbol: str, limit: int) -> Optional[List[dict]]:` | `analysis/volatility.py:249` |
| `get_dynamic_tp_sl_thresholds` | function | `def get_dynamic_tp_sl_thresholds(symbol: str, cfg=None) -> Dict[str, Optional[float]]:` | `analysis/volatility.py:266` |
| `assess_volatility_gate` | function | `def assess_volatility_gate(` | `analysis/volatility.py:347` — `symbol: str, sl_pct: float, tp_pct: float, cfg=None,` → `) -> Optional[str]:` (`:352`) |

**The ATR-based dynamic TP/SL math** (`:266-344`), step by step as implemented:

```
static_sl = scalping.tight_sl_pct        # 0.0025   :295
static_tp = scalping.fast_tp_pct          # 0.0060   :296
if dyn.enabled is False: return static    #          :310-312
atr = atr_1m_pct(symbol, period=dyn.atr_period)   # :317
if atr is None or atr <= 0: return static         # :321-322

raw_sl = dyn.atr_multiple * atr                  # 1.5 * atr          :330
sl_pct = min(max(raw_sl, dyn.min_sl_pct), dyn.max_sl_pct)             # :331
tp_pct = max(sl_pct * dyn.min_risk_reward, scalping.min_profit_pct)   # :332
```

Shipped values (`core/config.py:181-195`, `config.yaml:179-185`): `atr_multiple = 1.5`, `atr_period = 14`, `min_sl_pct = 0.0025`, `max_sl_pct = 0.0150`, `min_risk_reward = 1.5`, `realized_window_seconds = 30.0`, `max_breakeven_win_rate = 0.65`; scalping side `min_profit_pct = fast_tp_pct = 0.0060`, `tight_sl_pct = 0.0025` (`core/config.py:106-112`, `config.yaml:135-140`). Every `getattr` carries the same literal as its default, so the function works with an empty config.

Worked range: `ATR_1m = 0.0015` → `raw_sl 0.00225` → clamped up to `0.0025` → `tp 0.0060`, R:R 2.40. `ATR_1m = 0.0040` → `raw_sl 0.0060` → `tp 0.0090`, R:R 1.50. `ATR_1m = 0.012` → `raw_sl 0.018` → clamped to `0.0150` → `tp 0.0225`, R:R 1.50.

Returned dict (`:298-343`): `sl_pct`, `tp_pct`, `static_sl_pct`, `static_tp_pct`, `atr_pct`, `realized_vol`, `used_dynamic`, `reason`, plus `raw_sl_pct` and `clamped = sl_pct != raw_sl` on the dynamic path. `reason` carries the derivation, e.g. `ATR_1m 0.4000% x1.5 -> SL 0.6000%, TP 0.9000% (R:R 1.50)`.

**ATR computation.** `atr_1m_from_candles` requires `len(candles) >= period + 1` (`:107`), sorts ascending by `timestamp` so DESC-from-SQL callers still work (`:112`), then `TR = max(h - low, |h - prev_close|, |low - prev_close|)` (`:123-127`), skipping rows with any non-positive `h`/`low`/`prev_close` (`:121-122`). Wilder smoothing: seed with the mean of the first `period` TRs, then recurse `atr = (atr*(period-1) + tr)/period` (`:133-135`) — matching `pandas_ta`'s `atr` so the number agrees with `analysis/technical.py:90`. Returns `{atr, atr_pct = atr/close, close, samples}` (`:141-146`).

`atr_1m_pct` requests `period + 1` candles, not `period`, because Wilder needs one prior bar for `prev_close` — with exactly `period` candles `len(trs) == period - 1`, which always fails the `len(trs) < period` gate and silently disables every dynamic target in production (`:176-183`, same warning in `core/config.py:182`). Cache key `(symbol, int(time.time() // 5))` (`:157-160`), 256-entry clear-on-overflow (`:197-199`); it **excludes `period`**, so a non-default period can be served a default-period entry.

**Realized vol.** `realized_volatility` needs `>= 5` prices (`:51`) and `>= 4` log returns (`:61`), sample std with `n-1` denominator, returns `None` rather than `0.0` when the result is `0` (`:67`) — per the docstring at `:44-47`, `0.0` would make guards believe the market is calm. `realized_vol_pct` returns `vol * price` (`:83`) — a **price-unit** quantity, not a fraction, despite the docstring's "0.0008 = 0.08%" phrasing; it surfaces only as the informational `realized_vol` field.

**The gate** (`:347-412`). `taker` from `cfg.fees.taker` — `0.0005` default literal, but `config.yaml:60` ships **`taker: 0.00045`** (Hyperliquid base tier) → `roundtrip = 0.0009` in production. Rejection 1: `tp_pct <= roundtrip` → cancel, "setiap trade pasti merugi" (`:386-390`). Rejection 2: `net_tp = tp_pct - roundtrip`, `net_sl = sl_pct + roundtrip`; if `net_tp < net_sl`, `breakeven_wr = net_sl / (net_tp + net_sl)`, cancel when `breakeven_wr > 0.65` (`:397-410`). At the locked floor `sl 0.0025`/`tp 0.0060` → `0.40` — passes; at the ATR ceiling `sl 0.0150`/`tp 0.0225` → `0.424` — passes. The gate bites when `tp` is dragged toward the fee floor while `sl` stays wide.

**State.** `_ATR_CACHE` (keyed `(symbol, 5s-bucket)`) and `_CANDLE_SOURCE`. `set_candle_source` rebinds the global **and unconditionally clears the ATR cache** (`:238-240`); `_ATR_CACHE.clear()` fires wholesale past 256 entries (`:197-198`).

**Side effects.** No files, no network. Reads in-memory `core.market_store` (`:49`, `:80`) and whatever synchronous callback was registered via `set_candle_source`; those setters mutate module globals. `_load_candles` swallows any exception into `logger.debug`, returning `None` (`:254-258`). Never touches the event loop (`:212-220`).

**Consumers.** `trading/paper_engine.py:143` registers `_sync_source` via `set_candle_source` (`:162`), fed from `_candle_cache` refreshed in `refresh_volatility_cache` (`:168-184`); `trading/paper_engine.py:366` calls `get_dynamic_tp_sl_thresholds` (`:372`), applies `max(sl, static_sl)` / `max(tp, static_tp)` (`:375-376`) plus a second RR lock `tp_pct = max(tp_pct, sl_pct * min_rr)` (`:386`), then `assess_volatility_gate` (`:396`) raising `VolatilityGateError` (`:397-399`). `agents/execution_agent.py:117-140` recomputes thresholds so `Order.stop_loss_pct` quotes live numbers, applying the same floors before `risk_manager.calculate_stop_loss/take_profit` (`:139-140`). Tests: `tests/test_advanced_modules.py:53`, `tests/test_numerics.py:13`.



### 20. `analysis/vol_target.py`

**Role.** Volatility-targeting position sizing plus a regime filter. Imports only `core.{config,logger,market_store}`.

**Symbols.**

| Symbol | Kind | Signature | Loc |
| --- | --- | --- | --- |
| `logger` | module global | `logger = get_logger("vol_target")` | `analysis/vol_target.py:32` |
| `REFERENCE_DAILY_VOL` | module const | `REFERENCE_DAILY_VOL = 0.03  # 3% per hari` | `analysis/vol_target.py:37` |
| `daily_vol` | function | `def daily_vol(symbol: str, window_seconds: float = 300.0) -> Optional[float]:` | `analysis/vol_target.py:40` |
| `vol_ratio` | function | `def vol_ratio(symbol: str) -> Optional[float]:` | `analysis/vol_target.py:68` |
| `target_risk_fraction` | function | `def target_risk_fraction(symbol: str,` / `                         base_risk: float = None,` / `                         max_ratio: float = 3.0) -> Optional[float]:` | `analysis/vol_target.py:80-82` |
| `should_trade` | function | `def should_trade(symbol: str,` / `                 min_vol_ratio: float = 0.5,` / `                 max_vol_ratio: float = 3.0) -> Tuple[bool, str]:` | `analysis/vol_target.py:110-112` |

**Scaling math.** `daily_vol` samples 300 s of ticks, requires `>= 20` prices (`:49`) and `>= 15` log returns (`:57`), takes sample std, then scales `per_tick * math.sqrt(ticks_per_day)` with the hardcoded **`ticks_per_day = 86400.0 / 0.3`** (`:64-65`) — a 0.3-second tick, matching no configured feed interval. `vol_ratio = dv / 0.03` (`:77`). `target_risk_fraction` = `base_risk / clip(ratio, 0.2, max_ratio)` (`:100-107`), defaulting `base_risk` to `cfg.risk.max_risk_per_trade`. `should_trade` rejects `ratio < 0.5` and `ratio > 3.0` (`:128-131`); both bounds are hardcoded defaults, not config keys.

**State.** None — pure functions over `market_store` plus one module constant.

**Side effects.** Reads `core.market_store` only (`:47`). No writes, no globals, no network. `logger` is bound and never used.

**Consumers.** **None.** Grep across `*.py` finds no importer of `analysis.vol_target`, no call to any of its four functions, and no test file touching it. `risk.max_risk_per_trade` is consumed directly by `trading/risk_manager.py` and, outside risk management, only by `vol_target.py:95` — itself unreachable. The module is live-looking, well-reasoned, and entirely inert; position sizing is done by `trading/risk_manager.py`.

## Fill cost, position management and risk

### 21. `trading/fill_cost.py`

**Role.** The single place where simulated execution cost is computed: a fill priced *after* crossing cost (half spread + impact), in the direction of the side being filled, plus a metadata dict so an auditor can answer "where did the money go" from the log row alone. It exists because the cost used to live only inside `PaperTradingEngine._fill_price`, so only *opening* orders paid anything — SL hits, TP hits, liquidation, scalp TP and expired auto-close all closed at the raw `current_price`, i.e. free, and paper P&L counted half the round trip.

**Symbols.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `FILL_HALF_SPREAD_FLOOR` | constant | `FILL_HALF_SPREAD_FLOOR = 0.0003             # 3 bps` | `trading/fill_cost.py:45` |
| `FILL_IMPACT_FLOOR` | constant | `FILL_IMPACT_FLOOR = 0.0001                  # 1 bps` | `trading/fill_cost.py:46` |
| `FILL_BOOK_MALFORMED_SPREAD_PCT` | constant | `FILL_BOOK_MALFORMED_SPREAD_PCT = 0.05       # 5% — di atas ini book rusak, bukan pasar` | `trading/fill_cost.py:47` |
| `FILL_MAX_TOTAL_COST_PCT` | constant | `FILL_MAX_TOTAL_COST_PCT = 0.0050            # 50 bps, hanya jaring pengaman` | `trading/fill_cost.py:48` |
| `__all__` | export list | lists the 4 constants + `fill_price_after_cost` + `close_fill_price`; `describe_cost` is public but *not* exported | `trading/fill_cost.py:53` |
| `_observed_half_spread` | private fn | `def _observed_half_spread(symbol: str, max_book_age_seconds: float) -> Tuple[Optional[float], Optional[float]]:` | `trading/fill_cost.py:63` |
| `fill_price_after_cost` | public fn | `def fill_price_after_cost(\n    symbol: str,\n    fill_side: str,\n    ref_price: float,\n    *,\n    reason: str,\n    max_book_age_seconds: float = 1.5,\n    config: Any = None,\n) -> Tuple[float, Dict[str, Any]]:` | `trading/fill_cost.py:105` |
| `close_fill_price` | public fn | `def close_fill_price(\n    symbol: str,\n    position_side: str,\n    ref_price: float,\n    *,\n    reason: str,\n    config: Any = None,\n    max_book_age_seconds: float = 1.5,\n) -> Tuple[float, Dict[str, Any]]:` | `trading/fill_cost.py:178` |
| `describe_cost` | public fn | `def describe_cost(meta: Optional[Dict[str, Any]]) -> str:` | `trading/fill_cost.py:207` |
| `logger` | module global | `logger = logging.getLogger("trading_bot.fill_cost")` | `trading/fill_cost.py:42` |

**The four constants are floors, not targets.** `market_store` never expires an order book and `get_order_book_age` returns `None` for an unstamped book (`core/market_store.py:171`), so a book from an hour ago still reads as "present". Everything untrusted is pushed *down* to a floor:

- **Stale or unstamped book** → `_observed_half_spread` returns `(None, age)`, caller keeps `half = FILL_HALF_SPREAD_FLOOR` (`:141-145`). The `age is None` branch at `:76` matters: `None` means *not provably fresh*, not "fresh with no stamp".
- **Sentinel zero.** `0` is the exchange boundary sentinel (`core/microstructure.py:100-102`); `if not (mid > 0 and best_bid > 0 and best_ask > 0)` (`:92`) treats a non-positive level or mid as a *broken book*, not a wide spread — floor, not zero.
- **Empty sides or unparseable levels** → `(None, age)` at `:80`, `:85-86`.
- **Absurd spread.** The only ceiling is `FILL_BOOK_MALFORMED_SPREAD_PCT` (5%), catching broken books rather than capping markets: a 2% spread is real and must be paid in full (`:99`).

**Max, never min.** A surviving observation may only *add* cost — `half = max(observed, FILL_HALF_SPREAD_FLOOR)` (`:152`, `source = "live_book"`). `min()` would let a suspiciously tight book push cost toward zero, and a tight book is the one most likely to be stale: it can sit unchanged for a long time and be resent during a moment when the book is wide.

**Impact is a constant and never reads depth.** `impact = FILL_IMPACT_FLOOR` (`:157`) is assigned literally; no impact path touches the book. A depth-proportional term must divide by book depth, and the zero case is a crash in progress. `calculate_scalp_position_size` sizes from *balance*, not depth, so scalp participation is routinely large against a thin book. Known limitation, not a bug: this model **under-charges very large orders**. The sum is then clamped by the only `min()` in the module — `total = min(half + impact, FILL_MAX_TOTAL_COST_PCT)` (`:159`) — a 50 bps safety net, not a target.

**Direction convention, and why guessing it flips a SHORT.** `fill_side` is the **fill** side, not the position side: `"BUY"` to open a LONG and to close a SHORT, `"SELL"` to open a SHORT and to close a LONG. The charge, at `:162`:

```python
signed = +total if str(fill_side).upper() == "BUY" else -total
fill = float(ref_price) * (1.0 + signed)
```

BUY pays more, SELL receives less — both work against the trader. `close_fill_price` does the inversion once at `:196` (`fill_side = "SELL" if str(position_side).upper() == "LONG" else "BUY"`). Guessing wrong is not small: charging a SHORT's exit as SELL flips the sign, so the closing cost books as a gain.

**Both public functions.** `fill_price_after_cost` is the primitive; it takes the caller's `max_book_age_seconds` (default `1.5`, matching `scalping.max_tick_age_seconds`) and, given a `config`, overrides it from `config.scalping.max_tick_age_seconds` (`:133-139`) so the book's staleness budget is the one the tick guard uses. `close_fill_price` is the position-side wrapper, forwarding `reason`, `config`, `max_book_age_seconds`. Both return `(fill_price, meta)` carrying `reason`, `ref_price`, `fill_price`, `half_spread_pct`, `impact_pct`, `total_cost_pct`, `cost_source` (`"floor"` / `"live_book"`) and `book_age_s` (`:165-174`). `describe_cost` renders `total_cost_pct` in bps with its source.

**State.** Module-level constants only. No mutable or cached state. Reads `market_store` (`get_order_book` `:71`, `get_order_book_age` `:72`).

**Side effects.** None. Pure read of in-memory market state; no files, network, DB, or global mutation.

**Consumers.** `trading/paper_engine.py:21` (all four constants plus `fill_price_after_cost`; `_fill_price` at `:272` delegates to it) and `trading/position_manager.py:21` (`close_fill_price` at `:216` and `:472`, plus `describe_cost` — imported at `:21` but never called there). Tests: `tests/test_lifecycle_paths.py:106`, `:239`, `:334`. Note `trading/paper_engine.py:51-54` re-declares the same four constants as aliases, so the engine's literals and its import agree by convention, not by single source.

---

### 22. `trading/models.py`

**Role.** Dataclasses and string enums shared by the agents and both engines: the order a decision agent emits, the decision, the dashboard-facing position view, and the four enums keeping side, action, order type and close reason as one closed vocabulary.

**Symbols.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `Side` | enum | `class Side(str, Enum):` — `LONG = "LONG"`, `SHORT = "SHORT"` | `trading/models.py:10` |
| `OrderType` | enum | `class OrderType(str, Enum):` — `MARKET = "MARKET"`, `LIMIT = "LIMIT"` | `trading/models.py:15` |
| `TradeAction` | enum | `class TradeAction(str, Enum):` — `OPEN_LONG`, `OPEN_SHORT`, `CLOSE`, `HOLD` | `trading/models.py:20` |
| `CloseReason` | enum | `class CloseReason(str, Enum):` — `TP_HIT`, `SL_HIT`, `MANUAL`, `LIQUIDATED`, `SIGNAL` | `trading/models.py:27` |
| `Order` | dataclass | `@dataclass\nclass Order:` — fields `symbol: str`, `action: TradeAction`, `side: Optional[Side] = None`, `quantity: Optional[float] = None`, `leverage: int = 5`, `order_type: OrderType = OrderType.MARKET`, `stop_loss: Optional[float] = None`, `take_profit: Optional[float] = None`, `position_id: Optional[int] = None`, `reasoning: str = ""`, `price: Optional[float] = None` | `trading/models.py:36` |
| `TradeDecision` | dataclass | `@dataclass\nclass TradeDecision:` — fields `action: TradeAction`, `symbol: str`, `side: Optional[Side] = None`, `confidence: float = 0.0`, `leverage: int = 5`, `stop_loss_pct: Optional[float] = None`, `take_profit_pct: Optional[float] = None`, `risk_pct: float = 0.02`, `reasoning: str = ""`, `signals: dict = field(default_factory=dict)` | `trading/models.py:56` |
| `PositionInfo` | dataclass | `@dataclass\nclass PositionInfo:` — fields `id`, `symbol`, `side: str`, `entry_price`, `quantity`, `leverage`, `margin`, `liquidation_price`, `stop_loss: Optional[float]`, `take_profit: Optional[float]`, `unrealized_pnl`, `roe_pct`, `mark_price`, `duration: str` | `trading/models.py:71` |

Two fields carry weight. `Order.quantity` (`:41`) is `Optional`, commented "Jika None, dihitung oleh risk manager" — but `DecisionAgent.act()` now assigns it explicitly (`agents/decision_agent.py:432`, with `order.leverage`, `order.stop_loss`, `order.take_profit`, `order.price` at `:433-436`), because sizing happens in the agent against the *same* `RiskManager` both engines hold, and live sends `size=order.quantity or 0.0` to the exchange: a `None` reaching it becomes a real order of size zero. `Order.price` (`:52`) is the reference price for the live path — paper ignores it and uses the market price at execution, live requires it, since a missing price is rejected by the exchange with no readable reason.

**State.** None. No module globals, no mutable state beyond the enum value tables.

**Side effects.** None. Pure data declarations.

**Consumers.** `trading/paper_engine.py:19`; `trading/risk_manager.py:9` (`Order`, `TradeDecision`, `Side` — all imported but unused there); `trading/position_manager.py:20` (`Side` unused; `CloseReason` at `:433`, `:439`); `trading/live/executor.py:399`, `:409` (function-local imports); `agents/decision_agent.py:18`; `agents/execution_agent.py:20`. Tests: `tests/test_bugfixes.py`, `tests/test_fill_price_sl.py:27`, `tests/test_paper_engine.py:19`, `tests/test_live_executor.py:11`, `tests/test_lifecycle_paths.py:651`. `analysis/backtester.py` does **not** use these dataclasses — it has its own `PendingOrder` (`:622`).

---

### 23. `trading/risk_manager.py`

**Role.** Arithmetic and the pre-trade gate: position sizing (fixed-fractional and scalp), leverage clamping, SL/TP/liquidation price derivation, fee and PnL, and `validate_trade`. Everything numeric goes through `Decimal` so rounding direction is explicit rather than inherited from `Decimal.quantize`'s `ROUND_HALF_EVEN` default.

**Symbols.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `PRICE_QUANT` | constant | `PRICE_QUANT = Decimal("0.00000001")` | `trading/risk_manager.py:16` |
| `MONEY_QUANT` | constant | `MONEY_QUANT = Decimal("0.01")` | `trading/risk_manager.py:17` |
| `QTY_QUANT` | constant | `QTY_QUANT = Decimal("0.00001")` | `trading/risk_manager.py:18` |
| `RiskManager` | class | `class RiskManager:` | `trading/risk_manager.py:21` |
| `.__init__` | method | `def __init__(self, initial_balance: float = 0.0):` | `trading/risk_manager.py:31` |
| `.initial_balance` | property | `@property\ndef initial_balance(self) -> float:` | `trading/risk_manager.py:46` |
| `.set_initial_balance` | method | `def set_initial_balance(self, value: float) -> None:` | `trading/risk_manager.py:51` |
| `.calculate_position_size` | method | `def calculate_position_size(\n        self,\n        balance: float,\n        entry_price: float,\n        stop_loss_price: float,\n        risk_pct: float = None,\n        leverage: int = None,\n    ) -> Dict:` | `trading/risk_manager.py:64` |
| `.calculate_liquidation_price` | method | `def calculate_liquidation_price(\n        self,\n        entry_price: float,\n        side: str,\n        leverage: int,\n        mmr: float = 0.004,  # 0.4% default BTC\n    ) -> float:` | `trading/risk_manager.py:133` |
| `.calculate_stop_loss` | method | `def calculate_stop_loss(\n        self, entry_price: float, side: str, sl_pct: float = 0.02\n    ) -> float:` | `trading/risk_manager.py:157` |
| `.calculate_take_profit` | method | `def calculate_take_profit(\n        self, entry_price: float, side: str, tp_pct: float = 0.04\n    ) -> float:` | `trading/risk_manager.py:181` |
| `.calculate_fee` | method | `def calculate_fee(self, quantity: float, price: float, fee_type: str = "TAKER") -> float:` | `trading/risk_manager.py:202` |
| `.calculate_pnl` | method | `def calculate_pnl(\n        self,\n        side: str,\n        entry_price: float,\n        current_price: float,\n        quantity: float,\n    ) -> Dict:` | `trading/risk_manager.py:242` |
| `.validate_trade` | method | `def validate_trade(\n        self,\n        balance: float,\n        margin_required: float,\n        open_positions: int,\n        daily_pnl: float = 0,\n        peak_balance: float = None,\n        equity: float = None,\n    ) -> Dict:` | `trading/risk_manager.py:273` |
| `.calculate_scalp_position_size` | method | `def calculate_scalp_position_size(\n        self,\n        balance: float,\n        entry_price: float,\n        leverage: int = None,\n        risk_pct: float = None,\n    ) -> Dict:` | `trading/risk_manager.py:340` |
| `.kelly_criterion` | method | `def kelly_criterion(self, win_rate: float, avg_win: float, avg_loss: float) -> float:` | `trading/risk_manager.py:380` |
| `.calculate_max_drawdown` | method | `def calculate_max_drawdown(self, equity_history: List[float]) -> float:` | `trading/risk_manager.py:397` |
| `.calculate_sharpe_ratio` | method | `def calculate_sharpe_ratio(\n        self, returns: List[float], risk_free_rate: float = 0.0\n    ) -> float:` | `trading/risk_manager.py:414` |

**Every limit trigger in `validate_trade`** — all four are independent, each appends a reason, and `allowed` is `len(reasons) == 0` (`:335-338`).

1. **Margin vs free cash.** `if margin_required > balance * 0.9:` (`:299`) → `"Margin (…) melebihi 90% saldo (…)"`, inline float arithmetic. `paper_engine.py:655-656` passes `margin + estimated_fee`, not bare margin, because `open_position` debits both atomically — validating on margin alone lets an order clear the gate and be rejected a layer down as "insufficient balance" when the validation was already wrong.
2. **Open position count.** `if open_positions >= self.config.max_open_positions:` (`:303`). `>=`, so the configured maximum is the first *rejected* count: with `max_open_positions: 30` (`config.yaml:41`) the 30th concurrent position is refused.
3. **Daily loss circuit breaker.** Guarded by `if daily_pnl < 0 and reference > 0:` (`:320`), then `loss_fraction = abs(daily_pnl) / reference`, `if loss_fraction >= self.config.max_daily_loss:` (`:322`). The denominator is `reference = self._initial_balance if self._initial_balance > 0 else balance` (`:319`) — initial balance, not current cash. Cash looks more intuitive but is wrong: once margin locks, cash shrinks, the denominator shrinks with it, and the limit relaxes precisely when losses are worst. The `balance` fallback keeps the breaker alive if that metadata is missing. `initial_balance` is injected once from the DB by `PaperTradingEngine.initialize()` (`trading/paper_engine.py:117`).
4. **Drawdown.** `mark = equity if equity is not None else balance` (`:329`), then `if peak_balance and peak_balance > 0:` (`:330`), `drawdown = (peak_balance - mark) / peak_balance`, `if drawdown >= self.config.max_drawdown:` (`:332`). `equity` is wallet + unrealized because drawdown measures portfolio value; cash-only input makes locked margin read as a loss.

**Lines carrying the `0.9` literal.** Three, all here: `:119` in `calculate_position_size` (`max_margin = Decimal(str(balance)) * Decimal("0.9")  # Max 90% saldo`, margin/value/quantity recomputed when it binds, `:120-123`); `:299` in `validate_trade` (bare float); `:364` in `calculate_scalp_position_size` (`max_per_pos = Decimal(str(balance)) * Decimal("0.9") / Decimal(str(self.config.max_open_positions))`, applied at `:365`).

**Rounding and fee numerics.** SL, TP and liquidation all pass `rounding=ROUND_DOWN` explicitly (`:179`, `:200`, `:155`); without it `Decimal.quantize` defaults to `ROUND_HALF_EVEN` and an SL moves one tick the wrong way (for a LONG a *lower* stop), which on a 0.25%-wide scalp eats the room that made it worth taking. `calculate_fee` (`:202`) is always non-negative — rate `abs`-ed if negative (`:216-217`), `notional = abs(quantity) * abs(price)` (`:218`), result `abs`-ed again (`:240`) — since a negative fee would be *added* to the balance. Fees quantize to **10 decimals** (`Decimal("0.0000000001")`, `:238-239`), not cents: cent-rounding made fees non-linear in notional (100 USDT paid 0.09, not 0.08) and erased small orders (0.001 coins at $100 → $0.10 notional, fee $0.000045, paid $0.00, so a small-size scalpper looks cost-free and profitable). `calculate_max_drawdown` (`:397`) walks a running peak, `0.0` under 2 points. `calculate_sharpe_ratio` (`:414`) takes **returns, not prices**, uses `ddof=1`, `0.0` under 2 samples or on zero std, and imports numpy inside the body (`:421`). `kelly_criterion` (`:380`) is half-Kelly clamped to `[0, 0.10]` with no first-party caller — sizing used everywhere is fixed-fractional.

**State.** `self.config = get_config().risk`, `self.fees = get_config().fees` (`:40-41`), snapshotted at construction, so a later config reload is not picked up. Mutable state: `_daily_pnl: float = 0.0` (`:42`) and `_daily_reset_date: str = ""` (`:43`), both written nowhere in this file, plus `_initial_balance` (`:44`), writable only via `set_initial_balance` (`:51`), which coerces and falls back to `0.0` on `TypeError`/`ValueError`.

**Side effects.** None of its own. The only log line is the SL-distance-zero warning (`:108`) on `logger = get_logger("risk_manager")` (`:11`); config reads happen in `__init__` via `get_config()`.

**Consumers.** `trading/paper_engine.py:20` (`:117` `set_initial_balance`, `:566-567` SL/TP, `:618` scalp sizing, `:624` fixed-fractional sizing, `:655` fee, `:665` `validate_trade`); `trading/position_manager.py:19` (`:87` liq price, `:92`/`:235`/`:479` fee, `:227`/`:419` PnL, `:326` max drawdown, `:327` Sharpe); `agents/decision_agent.py:19` (`:372-373` SL/TP, `:376`/`:391` sizing); `agents/execution_agent.py:21` (`:139-140` SL/TP); `trading/live/executor.py:47` (`:347`/`:790` PnL, `:544` liq price, `:549`/`:792` fee). Tests: `tests/test_risk_manager.py:6`, `tests/test_numerics.py:15`, `tests/test_bugfixes.py:297`/`:318`, `tests/test_decision_agent_ensemble.py:23`, `tests/test_position_manager.py:13`, `tests/test_lifecycle_paths.py:30`. `run.py:389` wires `paper_engine.risk_manager` into `DecisionAgent`.

---

### 24. `trading/position_manager.py`

**Role.** Owns the paper-trading position lifecycle: opening (margin + fee debit, position row, OPEN trade), per-cycle unrealized PnL and SL/TP/liquidation checks, closing (exit cost, realized PnL, CLOSE trade, account stats, event), and the aggregate getters the engine and scheduler read. It is the **single source of exit cost** — no close path prices its own exit.

**Symbols.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `_equity_returns` | module fn | `def _equity_returns(equity_series: List[float]) -> List[float]:` | `trading/position_manager.py:26` |
| `PositionManager` | class | `class PositionManager:` | `trading/position_manager.py:42` |
| `.__init__` | method | `def __init__(self, event_bus: EventBus, risk_manager: RiskManager):` | `trading/position_manager.py:52` |
| `._get_repo` | method | `async def _get_repo(self) -> Repository:` | `trading/position_manager.py:58` |
| `.open_position` | method | `async def open_position(\n        self,\n        symbol: str,\n        side: str,\n        entry_price: float,\n        quantity: float,\n        leverage: int,\n        stop_loss: float = None,\n        take_profit: float = None,\n        reasoning: str = "",\n    ) -> Optional[int]:` | `trading/position_manager.py:64` |
| `.close_position` | method | `async def close_position(\n        self,\n        position_id: int,\n        close_price: float,\n        reason: str = "MANUAL",\n        price_is_final: bool = False,\n    ) -> Optional[Dict]:` | `trading/position_manager.py:167` |
| `.batch_close_positions` | method | `async def batch_close_positions(\n        self, position_ids: List[int], prices: Dict[str, float], reason: str\n    ) -> int:` | `trading/position_manager.py:374` |
| `.update_positions` | method | `async def update_positions(self, prices: Dict[str, float]):` | `trading/position_manager.py:402` |
| `._should_liquidate` | method | `def _should_liquidate(self, pos: dict, current_price: float) -> bool:` | `trading/position_manager.py:442` |
| `._sl_hit` | method | `def _sl_hit(self, pos: dict, price: float) -> bool:` | `trading/position_manager.py:448` |
| `._tp_hit` | method | `def _tp_hit(self, pos: dict, price: float) -> bool:` | `trading/position_manager.py:454` |
| `._liquidate` | method | `async def _liquidate(self, pos: dict, price: float):` | `trading/position_manager.py:460` |
| `._get_equity_series` | method | `async def _get_equity_series(self, limit: int = 500) -> List[float]:` | `trading/position_manager.py:513` |
| `.get_open_position_count` | method | `async def get_open_position_count(self) -> int:` | `trading/position_manager.py:544` |
| `.get_total_unrealized_pnl` | method | `async def get_total_unrealized_pnl(self) -> float:` | `trading/position_manager.py:549` |
| `.get_total_open_margin` | method | `async def get_total_open_margin(self) -> float:` | `trading/position_manager.py:554` |
| `.take_balance_snapshot` | method | `async def take_balance_snapshot(self):` | `trading/position_manager.py:560` |

**`close_position` — the single source of exit cost.** `close_price` is a *market* price (mid), not a fill price. `price_is_final: bool = False` (`:172`) is the documented opt-out: `True` means "the caller already charged its own cost", so the price is used as-is (`:213-214`) with no `fill_meta`. It exists for the live path, which books the exchange's real fill — but it has **no caller anywhere in the repo**, so every close in practice goes through the model. When false (`:215-222`), cost comes from `close_fill_price(pos["symbol"], pos["side"], close_price, reason=str(reason), config=self.config)` — direction derived from `pos["side"]`, the position's own row, not from an argument a caller can get wrong. Cost moved out of the callers because every close previously passed the raw market price through, leaving the opening cost charged in `_execute_open` without a partner and paper P&L counting half a round trip.

**One filled price feeds four consumers.** `fill_price` is the DB row's close price (`:263`), the fee's price basis (`:235`), the realized-PnL price basis (`:227-229`), the CLOSE trade row's `price=fill_price` (`:270`) and the event's `"close_price"` (`:353`). A fee from the market price under-charges every exit, since the fee is a percentage of the notional actually paid; PnL from the market price books the cost in the log but not in the numbers, leaving the HUD's P&L still lying.

**Opening fee is deducted from the credited return.** `open_fee` is read back from the `trades` table (first row with `trade_type == "OPEN"`, `:250-254`) rather than recomputed from `entry_price`, since the taker rate can change mid-run and recomputing yields a number different from what was debited. `net_pnl = pnl_info["pnl"] - open_fee - fee` (`:257`), but the credited amount is `pos["margin"] + net_pnl + open_fee` (`:297`) — `open_fee` is added back in the credit, not folded into `net_pnl`, since it was debited at open. Crediting `net_pnl` alone charges it twice; that double-count measured ~891 USDT hidden across 330 paper positions, 2.70 USDT low each.

**The close paths.** Six first-party call sites reach `close_position`, plus a wrapper with no first-party caller. Liquidation is the one path that does *not* converge here.

| Path | Call site | Reason argument |
|---|---|---|
| SL hit | `trading/position_manager.py:433` | `CloseReason.SL_HIT` |
| TP hit | `trading/position_manager.py:439` | `CloseReason.TP_HIT` |
| Paper order, one position (`order.position_id` set) | `trading/paper_engine.py:841` | `"SIGNAL"` |
| Paper order, batch — loop per open row for the symbol | `trading/paper_engine.py:930` | `"SIGNAL"` |
| Scalp TP (`profit_pct >= scalping.min_profit_pct`) | `agents/execution_agent.py:311` | `"SCALP_TP"` |
| Auto-close expired (`hold_seconds > scalping.max_hold_seconds`) | `agents/execution_agent.py:270` | `"SCALP_EXPIRED"` |
| `batch_close_positions` wrapper | `trading/position_manager.py:394` | caller-supplied; **no first-party caller** |

`_execute_close` deliberately charges nothing itself (`trading/paper_engine.py:830-840`) — since `close_position` charges from `pos["side"]`, charging in the engine double-charged. Side benefit: a mixed LONG+SHORT batch for one symbol now charges each position in its own direction instead of one direction for the batch. A `None` return is not an error — another caller already moved the row OPEN→CLOSED, so this one gets no margin back.

**`_liquidate` now charges a fee** (`:460`). It does not debit balance (margin was debited at open) and records only the margin loss: `realized_pnl = -pos["margin"] - fee` (`:480`). The breakout cost is charged anyway — the exchange still crosses the spread and still takes a fee; with `fee=0` the path looked free and liquidation P&L looked ~100 bps better than reality. It calls `close_fill_price(..., reason="LIQUIDATION", config=self.config)` (`:472`), computes the fee from the filled price (`:479`), writes a `trade_type="LIQUIDATION"` row (`:486-496`) and publishes a `LIQUIDATED` payload with `liquidation_price`, `margin_lost`, `fee` and the full `fill_meta` (`:498-511`). Claim-once too (`:482-484`).

**Drawdown and Sharpe from a reversed equity series.** `_get_equity_series(limit=500)` (`:513`) reads `repo.get_balance_history(limit=limit)` and iterates `for r in reversed(rows)` (`:534`) because `get_balance_history` sorts **DESC** while both metrics need chronological order; DESC input makes "drawdown" measure valley-to-peak — always small, meaningless. It prefers the `equity` column, falling back to `balance` when `equity` is `None` (`:535-537`): equity includes margin locked in open positions, so a fall is a real fall in portfolio value rather than money moved into a position. `limit=500` keeps `update_account_stats` from growing more expensive — enough for the drawdown currently in progress, the only recoverable one. Both feed `repo.update_account_stats(…, max_drawdown=max_dd, sharpe_ratio=sharpe, profit_factor=…)` at `:331-343`, `profit_factor` mapped from `inf` to `0`. Sharpe gets `_equity_returns(series)` (`:327-329`), simple returns `(curr - prev) / abs(prev)` skipping zero priors — on raw prices the ratio's skew is dominated by price scale. `bump_peak_balance` is fed `equity_now = new_balance + remaining_margin` (`:310-312`), not free cash, which would let every open position lower the peak on its own. The `balance` column is never rewritten here: `apply_balance_delta` is the atomic mutation, and an absolute write would clobber a concurrent open in another coroutine.

**State.** `self.event_bus`, `self.risk_manager`, `self.config = get_config()` (`:53-55`), and a lazily built `self._repo` (`:56`, built in `_get_repo` `:58-62`) — the only cache, never invalidated.

**Side effects.** DB writes via `Repository`: `apply_balance_delta` (debit `:102`, refunds `:105`/`:128`, credit `:298`), `insert_position`, `insert_trade` (OPEN `:142`, CLOSE `:278`, LIQUIDATION `:496`), claim-once `close_position`/`liquidate_position`, `update_position_pnl`, `bump_peak_balance`, `update_account_stats`, `insert_balance_snapshot` (`:577`), plus reads `get_account`, `get_open_positions`, `get_trades_by_position`, `get_trade_stats`, `get_balance_history`. EventBus publishes `OPENED` (`:145`), `CLOSED` (`:346`), `LIQUIDATED` (`:498`) on `Channels.POSITION_UPDATE`, all `source="position_manager"`. Reads `market_store.get_price` as fallback (`:413`). Logs via `get_logger("position_manager")` (`:23`).

**Consumers.** `trading/paper_engine.py:28` — the only first-party constructor; it drives the three aggregate getters (`:644-646`), `close_position` (`:841`, `:930`) and `update_positions` (`:1070`). `run.py:616` schedules `paper_engine.position_manager.take_balance_snapshot`. `agents/execution_agent.py:270` and `:311` reach through `self.engine.position_manager.close_position(...)` for scalp TP and expiry. **Live never uses this class** — `trading/live/executor.py:115` assigns a distinct `_LivePositionManager`, so `close_position` is on no live path, which is why `price_is_final` has no caller. Tests: `tests/test_position_manager.py:14`, `tests/test_lifecycle_paths.py:29`.

## Paper trading engine

### 25. `trading/paper_engine.py`

**Role.** The paper-mode order router: it takes an `Order` from the decision agent, turns a market price into a *paid* fill price, rebuilds SL/TP at that fill, validates risk, and hands the result to `PositionManager`. It is also the only module that owns the tick-quality guard and the volatility gate, and the only writer of the `TRADE_EXECUTED` / `TRADE_REJECTED` / `TRADE_CLOSED` audit rows for the paper path.

**Symbols.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `logger` | module global | `logger = get_logger("paper_engine")` | `trading/paper_engine.py:30` |
| `FILL_HALF_SPREAD_FLOOR` | module global (alias) | `FILL_HALF_SPREAD_FLOOR = 0.0003             # 3 bps` | `trading/paper_engine.py:51` |
| `FILL_IMPACT_FLOOR` | module global (alias) | `FILL_IMPACT_FLOOR = 0.0001                  # 1 bps` | `trading/paper_engine.py:52` |
| `FILL_BOOK_MALFORMED_SPREAD_PCT` | module global (alias) | `FILL_BOOK_MALFORMED_SPREAD_PCT = 0.05       # 5% — di atas ini book rusak, bukan pasar` | `trading/paper_engine.py:53` |
| `FILL_MAX_TOTAL_COST_PCT` | module global (alias) | `FILL_MAX_TOTAL_COST_PCT = 0.0050            # 50 bps, hanya jaring pengaman` | `trading/paper_engine.py:54` |
| `VolatilityGateError` | exception | `class VolatilityGateError(Exception):` | `trading/paper_engine.py:57` |
| `VolatilityGateError.__init__` | method | `def __init__(self, reason: str, meta: Optional[dict] = None):` | `trading/paper_engine.py:71` |
| `PaperTradingEngine` | class | `class PaperTradingEngine:` | `trading/paper_engine.py:77` |
| `PaperTradingEngine.__init__` | method | `def __init__(self, event_bus: EventBus):` | `trading/paper_engine.py:89` |
| `PaperTradingEngine._get_repo` | method | `async def _get_repo(self) -> Repository:` | `trading/paper_engine.py:101` |
| `PaperTradingEngine.initialize` | method | `async def initialize(self):` | `trading/paper_engine.py:107` |
| `PaperTradingEngine._register_candle_source` | method | `def _register_candle_source(self, repo: "Repository") -> None:` | `trading/paper_engine.py:129` |
| `PaperTradingEngine._register_candle_source.<locals>._refresh_cache` | nested coroutine | `async def _refresh_cache():` | `trading/paper_engine.py:145` |
| `PaperTradingEngine._register_candle_source.<locals>._sync_source` | nested callback | `def _sync_source(symbol, timeframe, limit):` | `trading/paper_engine.py:154` |
| `PaperTradingEngine.refresh_volatility_cache` | method | `async def refresh_volatility_cache(self) -> None:` | `trading/paper_engine.py:168` |
| `PaperTradingEngine.update_price` | method | `def update_price(self, symbol: str, price: float):` | `trading/paper_engine.py:186` |
| `PaperTradingEngine.get_price` | method | `def get_price(self, symbol: str) -> Optional[float]:` | `trading/paper_engine.py:190` |
| `PaperTradingEngine.execute_order` | method | `async def execute_order(self, order: Order) -> Dict:` | `trading/paper_engine.py:212` |
| `PaperTradingEngine._fill_price` | method | `def _fill_price(self, symbol: str, side: str, ref_price: float, *, reason: str) -> tuple:` | `trading/paper_engine.py:240` |
| `PaperTradingEngine._tick_quality_guard` | method | `def _tick_quality_guard(self, symbol: str, price: float, sl_pct: float):` | `trading/paper_engine.py:280` |
| `PaperTradingEngine._resolve_tp_sl` | method | `def _resolve_tp_sl(self, symbol: str, order: "Order") -> tuple:` | `trading/paper_engine.py:341` |
| `PaperTradingEngine._diagnose_open_failure` | method | `async def _diagnose_open_failure(self, order: Order, quantity: float, price: float, margin: float, estimated_fee: float) -> str:` | `trading/paper_engine.py:403` |
| `PaperTradingEngine._execute_open` | method | `async def _execute_open(self, order: Order, price: float) -> Dict:` | `trading/paper_engine.py:443` |
| `PaperTradingEngine._execute_close` | method | `async def _execute_close(self, order: Order, price: float) -> Dict:` | `trading/paper_engine.py:811` |
| `PaperTradingEngine._describe_fill_meta` | static method | `def _describe_fill_meta(meta: Optional[dict]) -> str:` | `trading/paper_engine.py:1013` (decorator `:1012`) |
| `PaperTradingEngine._describe_failures` | static method | `def _describe_failures(failed: List[dict]) -> str:` | `trading/paper_engine.py:1037` (decorator `:1036`) |
| `PaperTradingEngine.check_positions` | method | `async def check_positions(self):` | `trading/paper_engine.py:1049` |
| `PaperTradingEngine.get_account_summary` | method | `async def get_account_summary(self) -> Dict:` | `trading/paper_engine.py:1072` |

**State.** Module level: `logger`, plus the four `FILL_*` names at `:51-54`. Those four are **redefinitions that shadow the identical import at `:21-26`** — the import block pulls `FILL_BOOK_MALFORMED_SPREAD_PCT`, `FILL_HALF_SPREAD_FLOOR`, `FILL_IMPACT_FLOOR`, `FILL_MAX_TOTAL_COST_PCT` and `fill_price_after_cost` from `trading.fill_cost`, and the assignments at `:51-54` then overwrite four of those names in this module's namespace. Grepping the file body confirms the shadowed names are never read again after `:54`; only `fill_price_after_cost` survives the shadow, because it is not redefined. The constants are re-exported deliberately — `trading/fill_cost.py:50-52` says the definitions stay here as aliases so existing importers do not break, and `tests/test_fill_price_sl.py:28-32` and `tests/test_paper_engine.py:14-18` both import `FILL_HALF_SPREAD_FLOOR` and `FILL_IMPACT_FLOOR` from `paper_engine`, not from `fill_cost`.

Instance level, all set in `__init__`: `event_bus`, `risk_manager` (`:91`), `position_manager` (`:92`), `config` (`:93`), `_repo` (`:94`, lazy), `_candle_cache: Dict[str, list]` (`:97`), `_candle_refresh_task` (`:98`, assigned once at `:166` and never awaited or cancelled), `_last_prices: Dict[str, float]` (`:99`). `_last_prices` is private but part of the engine contract — `agents/execution_agent.py:266` and `:290` read `self.engine._last_prices.get(symbol)` on the fallback path when `market_store` has no price.

One dead local: `vol_meta` is bound from `_resolve_tp_sl` at `:520` and from the literal `{}` at `:550`, and read nowhere afterwards — the `TRADE_EXECUTED` `output_data` at `:765-775` carries only `fill_meta` fields. On the gate-rejection path the volatility numbers do reach the log, but through `gate_err.meta` at `:539`, not through `vol_meta`.

**The module docstring's WHY, `:32-50`.** The comment block above the constants is the design rationale and should not be paraphrased away. Its argument, in order:

1. Slippage is no longer subtracted as a number *after* the fill. The price handed to `open_position` / `close_position` is the already-shifted price, because every consumer derives from it: `close_position` derives PnL from `entry_price` vs `close_price` (`position_manager.py:176-178` per the comment), `open_position` derives margin, liquidation price and fee from `entry_price` (`:67-75`), and SL/TP come off the same fill. Subtracting later makes the DB's `close_price` fictional, arms SL/TP at a level that never existed, and forces `position_manager` to be edited too. "Shift the price" is the only shape that fixes all of it without touching other files.
2. The numbers are **floors, not targets**. `market_store` never arbitrages the order book, and `get_order_book_age` returns `None` for an unstamped book, so a one-hour-old book still reads as "valid". The live path may only *add* cost via `max()`, never `min()`. Stale books, unstamped books, zero-price sentinels and narrow spreads all collapse onto the floor; that single `max` property is what makes untrusted input safe to use.

**`execute_order` (`:212-238`).** Router only. Resolves the price once via `self.get_price(order.symbol)` (`:222`); if it is `None` the order is rejected before any other work (`:224`). `OPEN_LONG` / `OPEN_SHORT` → `_execute_open` (`:228`), `CLOSE` → `_execute_close` (`:232`), `HOLD` → a success dict with no action (`:236`), anything else → a failure dict naming the unknown action (`:238`). Every branch returns the same four-key shape: `{"success", "message", "position_id", "details"}`. `HOLD` never touches the DB or the bus.

**`_fill_price` (`:240-278`) is now a delegation, not the model.** Its whole body is one call:

```python
return fill_price_after_cost(
    symbol,
    side,
    ref_price,
    reason=reason,
    config=self.config,
)
```

The arithmetic moved to `trading/fill_cost.py` because `PositionManager` needs it too when *closing*. Previously the cost lived only here, so every close — SL hit, TP hit, liquidation, scalp TP, auto-close expired — closed at the raw `current_price`, i.e. for free, and paper P&L charged only half the round trip. `side` here is the **fill** side, not the position side: `"BUY"` for opening a LONG and closing a SHORT, `"SELL"` for opening a SHORT and closing a LONG. Guessing it inverts the sign for SHORT. The return is `(fill_price, meta)`, and `meta` exists so the audit log alone can answer where the money went.

The docstring's last paragraph is a placement rule, not trivia: **tick validation is deliberately absent from `_fill_price`**, because the guard already runs on the OPEN path with the post-gate `sl_pct`. Putting it here would make `_fill_price` raise `VolatilityGateError` on the CLOSE path, which does not catch it — a close order would become an exception instead of a tidy rejection.

**`_tick_quality_guard` (`:280-339`).** Pure and I/O-free; all state comes from `market_store`. Returns a rejection string, or `None` when the tick is tradeable. Two checks, cheapest first. (1) **Tick age** against `scalping.max_tick_age_seconds` (`:309-315`), via `market_store.get_price_age`. (2) **Outlier** against the median of recent ticks (`:318-337`), not against the price the order was built from — median, not mean, so one spike cannot drag the baseline. Ticks deviating more than `sl_pct` are rejected; on a 0.25 % scalp a fill at a local top puts the stop already inside the spread. Fail-open is intentional: below `stale_tick_min_samples` samples (or with `stale_tick_window_seconds` unset) the guard returns `None` and logs nothing user-visible, because rejecting orders for lack of history would silence the bot exactly when it is busiest.

**`_resolve_tp_sl` (`:341-401`).** Returns `(sl_pct, tp_pct, meta)` or raises. Static floors come from `scalping.tight_sl_pct` (default `0.0025`) and `scalping.fast_tp_pct` (default `0.0060`) via `getattr` (`:369-370`). Order of operations is load-bearing: (1) fetch dynamic ATR thresholds; if ATR is unavailable, fall back to static — correct behaviour, not a failure (`:374-379`); (2) clamp with `max()` against the static values, because volatility may widen the target but must never narrow it below the agreed static floor (`:375-376`); (3) re-clamp `tp_pct` against `sl_pct * min_risk_reward` (default `1.5`) so the raised SL cannot drop the risk/reward lock (`:384-386`); (4) run the gate (`:396-399`). `meta` carries `sl_pct`, `tp_pct`, `used_dynamic`, `atr_pct`, `reason`, and `gate` when it rejects.

**`VolatilityGateError` (`:57-74`).** Its docstring states why it is a separate type: this is *not* ordinary risk-validation failure. `RiskManager` rejects on account state (margin, drawdown, daily loss); this rejects on market state. The two are logged under different `reason` values so an auditor can tell "our ticket is bad" from "the market is untradeable". `__init__` takes `(reason, meta=None)`, calls `super().__init__(reason)`, and exposes `.reason` and `.meta` (`meta or {}`).

**`_execute_open` (`:443-809`)** — the ordering is the whole design:

1. Account check (`:448`), `side` derivation from the action (`:451`).
2. **Basic input validation before anything reads `quantity` or `leverage`** (`:468-496`). The `else` branch at `:632-634` uses `order.quantity` verbatim and divides by `order.leverage`: negative `quantity` → negative margin → negative `required_cash` → `validate_trade` sees it as affordable → *money created from nothing*; zero quantity → a zero-size position; zero leverage → `ZeroDivisionError` that kills the loop. `quantity=None` is explicitly allowed — that is the deliberate "size it from the risk manager" signal consumed by the `if` branch. Any collected problem writes a `TRADE_REJECTED` `AgentLog` and returns `{"success": False, "details": {"invalid_input": problems}}`.
3. **Volatility gate / SL-TP resolution** (`:517-546`), only when `scalping.enabled`. `_resolve_tp_sl` is called inside `try`; `VolatilityGateError` is caught at `:523`, logged, written as a `TRADE_REJECTED` row whose `output_data` is `gate_err.meta` (`:539`), and returned as `{"success": False, "details": {"gate": "volatility", **meta}}`. **The gate result is not passed through to risk validation on purpose** — at that point the reasons would name margin, when the real problem is market conditions. The non-scalp branch hardcodes `sl_pct = 0.02`, `tp_pct = 0.04`, `vol_meta = {}` (`:547-550`); `agents/decision_agent.py:26-39` documents that `_NON_SCALP_SL_PCT` / `_NON_SCALP_TP_PCT` are a deliberate literal duplicate of these two numbers and must be changed together.
4. **Cost is shifted into the price, once** (`:561-564`): `fill_side = "BUY" if side == "LONG" else "SELL"`, then `price, fill_meta = self._fill_price(order.symbol, fill_side, price, reason="OPEN")`. From this line `price` is what is actually paid. The comment at `:552-560` states the invariant: SL/TP, sizing, margin, fee and `open_position` all consume the same value; any consumer still holding the reference price makes the equity curve charge a cost that was never paid.
5. **SL/TP rebuilt at the fill price** (`:566-567`) — `calculate_stop_loss(price, side, sl_pct)` and `calculate_take_profit(price, side, tp_pct)`. `order.stop_loss` / `order.take_profit` were computed earlier by the agent from the price it saw during `think()`, and `think()` and `execute_order()` are separated by `await`; if SL came from P1 and the fill happened at P2, the true risk distance is `tight_sl_pct ± (P2 - P1)`, which on a thin altcoin can exceed the stop itself. `tests/test_fill_price_sl.py:1-17` documents the observed symptom — positions lasting 0-6 seconds, all ending `SL_HIT`. Dynamic ATR targets are computed *here* rather than in the agent for the same reason.
6. **Tick-quality guard** (`:587-613`). Note it is fed `fill_meta["ref_price"]`, **not** the shifted `price`. The comment at `:569-586` is explicit: the old guard compared `price` against a `stop_loss` derived from `price`, so `price <= stop_loss` could only be true if `sl_pct <= 0` — dead code that never caught anything. The replacement measures tick age and deviation from the median of recent ticks. Slippage guards validate; they do not adjust — validating the shifted price would reject an order for a deviation the guard itself created. Rejection writes a `TRADE_REJECTED` row carrying `sl_pct` and the full `fill_meta`, and returns `{"success": False, "message": f"Harga tidak layak eksekusi: {rejection}", "details": {"guard": "tick_quality", "reason": rejection}}`.
7. **Sizing** (`:616-634`). `order.quantity is None` → `calculate_scalp_position_size(balance, entry_price=price, leverage)` when scalping is on, else `calculate_position_size(balance, entry_price=price, stop_loss_price=stop_loss, leverage)`; `quantity`/`margin` come from the result. Otherwise `quantity = order.quantity` and `margin = (quantity * price) / order.leverage`.
8. **Risk validation** (`:644-695`). Open count, open margin and unrealized PnL are gathered, and `equity_now = free_balance + open_margin + open_upl` — because `account["balance"]` is *free cash* with open margin already deducted, valid for the margin limit but wrong for drawdown. `estimated_fee = calculate_fee(quantity, price, "TAKER")` and `required_cash = margin + estimated_fee`: `open_position` deducts both atomically, so validating margin alone lets an order through this gate and get rejected one layer down under a misleading "insufficient balance". `daily_pnl = await repo.get_daily_realized_pnl()` feeds the daily-loss breaker (today's realised PnL, not cumulative). `validate_trade(balance=free_balance, margin_required=required_cash, open_positions=open_count, daily_pnl=daily_pnl, peak_balance=account.get("peak_balance"), equity=equity_now)`. A rejection joins `validation["reasons"]` with `"; "` and writes a `TRADE_REJECTED` row that includes `required_cash` so an auditor can reconstruct the decision without guessing the fee.
9. **Open** (`:698-707`) via `position_manager.open_position(symbol, side, entry_price=price, quantity, leverage, stop_loss, take_profit, reasoning)`. `None` means one of three different failures, so `_diagnose_open_failure` (`:403-441`) re-derives the honest message: no account → init problem; `free < margin + fee` → balance; otherwise a DB insert failure with funds confirmed sufficient.
10. **`TRADE_EXECUTED` audit log** (`:754-776`). `reasoning` prints the fill price, the reference price, the cost in bps and its `cost_source`, quantity and leverage. `output_data` deliberately carries the *whole* cost breakdown — `ref_price`, `fill_price`, `half_spread_pct`, `impact_pct`, `total_cost_pct`, `cost_source`, `book_age_s` — plus `position_id` and `margin`. The stated reason (`:747-753`): a losing run must be diagnosable from the log alone; without `cost_source` and `book_age_s`, a 4 bps charge from a live book is indistinguishable from a 4 bps charge off a stale one that happened to read as valid.
11. **Broadcast** (`:779-794`) `Channels.TRADE_EXECUTED` with `source="paper_engine"`, then the success dict whose `details` include `entry_price`, `quantity`, `leverage`, `margin`, `stop_loss`, `take_profit`, `fill_meta`.

**`_execute_close` (`:811-1010`)** has two branches, and the branch point is `order.position_id`.

*Single position* (`:827-895`): calls `close_position(order.position_id, price, "SIGNAL")` **with the raw market price and no adjustment**. The comment at `:829-840` is the single most important line for anyone editing this file — cost is no longer charged here. `close_position` charges it itself now, from `pos["side"]` read straight off the position row, the same source the SL/TP/liquidation paths use. Adjusting here charged it **twice**: once in `_execute_close`, once in `close_position`. One source of truth means no path can forget the cost and no path can double-charge it because it cannot tell which paths already adjusted. On success it writes a `TRADE_CLOSED` row: `reasoning` embeds fill price, realised PnL, fee, ROE and `self._describe_fill_meta(fill_meta)`; `output_data` is `{**result, ...}` with the cost fields sourced defensively from `fill_meta` (`0.0` / `"none"` / `None` when it is `None`). On `None` it returns "position not found".

*All positions for the symbol* (`:898-1010`): `repo.get_open_positions(order.symbol)`; empty → failure. The loop calls `close_position(pos["id"], price, "SIGNAL")` per position, passing the same unmodified `price` — **no pre-loop adjustment at all**. The comment at `:902-914` records the old bug: cost used to be computed once outside the loop from the *first* position's side and then charged again inside `close_position`, so every cost was charged twice. Per-position closure also means a mixed LONG+SHORT batch now charges each position by its own direction instead of uniformly by the first one's; since `max_open_positions` plus one-position-per-symbol make mixed batches unreachable on this path, it is mainly an arithmetic correction rather than a behaviour change. The comment cites `decision_agent.py:217` for that one-per-symbol rule, but that line is now the `min_confidence` check — the rule actually lives at `agents/decision_agent.py:203-210` (`chosen_symbols` + the batch loop) with the "max 1 posisi per simbol" note at `:201`, so the cross-reference in the comment has drifted.

`fill_meta` for the batch log comes from the **first position that actually closed** (`if fill_meta is None:` at `:934-936`); `failed` entries carry only `position_id`, `side`, `entry_price` so no numbers are invented for rows that were not claimed. `None` from `close_position` means the claim was lost — most often a concurrent SL/TP path already closed the row — and that is legitimate, not an error. The log records `closed_ids`, `total_pnl`, `failed` and the cost breakdown. Success requires `not failed` (`:978`): partial closes return `success: False` with a message that names how many stayed open plus `self._describe_failures(failed)`, and log a warning (`:997`), because the caller must know exposure remains rather than receive an optimistic number.

**`_describe_fill_meta` (`:1012-1034`)** renders `meta` as one line of bps: `"12.34bps dari live_book (half 10.00bps + impact 1.00bps, book 0.42s)"`, with the `book_age_s` clause appended only when it is not `None`. `meta is None` means *no fill happened* — the position was gone before it could be closed — and the function returns `"tidak ada (posisi tidak ditemukan)"`. That distinction is load-bearing: reporting it as zero cost would hide the very failure that needs seeing.

**`check_positions` (`:1049-1070`)** merges `market_store.get_all_prices()` with `self._last_prices` (internal cache wins) and hands the map to `position_manager.update_positions(prices)`; empty map → return. Its docstring is an explicit prohibition: **slippage must NOT be applied here**, even though this looks like the natural place for it. `update_positions` uses one price for two things — marking unrealised PnL and testing SL/TP. Shifting it would arm stops at levels the market never quoted, so stops would "hit" on phantoms the code invented. Mark-to-market is at the reference price; only crossings pay cost.

**`get_account_summary` (`:1072-1113`)** returns `{}` when there is no account, otherwise reads unrealised PnL, open count and open margin from the position manager. `account["balance"]` is free cash with open margin already deducted, so it is added back before being called "balance": `wallet_balance = free_balance + open_margin`, `equity = wallet_balance + unrealized`, `total_pnl = equity - initial_balance`. `win_rate` guards division on `total_trades > 0`. The dict includes `max_drawdown` and `profit_factor` from the account row, both now actually written — `position_manager.close_position` computes max drawdown and Sharpe from the equity series in `balance_history` and passes both to `update_account_stats` (`trading/position_manager.py:325-343`).

**Side effects.**

- **DB writes.** All through the lazily-built `Repository` (`_get_repo`, `:101-105`, backed by the `database.db` singleton). `init_account` (`:110`) and `get_account` (`:115`, `:446`, `:427`, `:1075`) in the read paths; `get_candles` (`:148`, `:180`); `get_daily_realized_pnl` (`:663`); `get_open_positions` (`:898`); and seven `insert_agent_log` sites writing `AgentLog` rows under `agent_name="paper_engine"` — `TRADE_REJECTED` at `:484` (invalid input), `:531` (volatility gate), `:595` (tick guard), `:680` (risk validation), `:726` (open failure); `TRADE_EXECUTED` at `:754`; `TRADE_CLOSED` at `:854` and `:946`. Both close paths are `:854` (single) and `:946` (batch).
- **Bus.** One publish: `Channels.TRADE_EXECUTED` at `:779-794` with `source="paper_engine"`. The close paths publish nothing — `PositionManager` broadcasts `Channels.POSITION_UPDATE` itself (`trading/position_manager.py:346-358`).
- **Global mutation.** `analysis.volatility.set_candle_source(_sync_source)` at `:162` replaces the module-level candle provider of a *different* module. `asyncio.create_task(_refresh_cache())` at `:166` starts an unreferenced background task whose handle is stored in `_candle_refresh_task` (`:98`) and never awaited, cancelled, or re-read — `run.py:740` drives the ongoing refresh instead, via `refresh_volatility_cache()`. Both candle-cache writers swallow every exception to `logger.debug` (`:151-152`, `:183-184`).
- **Files / network.** None. The engine writes nothing to disk itself; all persistence is SQL through the `Repository`.

**Consumers.** `run.py:57` (`from trading.paper_engine import PaperTradingEngine`), instantiated at `run.py:248`, initialised at `:341`, handed to `ExecutionAgent` as `executor = self.paper_engine` at `:396-401`, and driven by `await self.paper_engine.refresh_volatility_cache()` at `:740`. `run.py:389` reads `self.paper_engine.risk_manager` to build the `DecisionAgent`, and `run.py:616` schedules `self.paper_engine.position_manager.take_balance_snapshot` as an APScheduler job. No other first-party module imports it: `analysis/volatility.py:213`, `agents/execution_agent.py:106`, `agents/decision_agent.py:28` and `core/config.py:125` reference `PaperTradingEngine` only in comments. `VolatilityGateError` is imported by tests only — `tests/test_advanced_modules.py:334` and `:346`, with `:349` asserting `VolatilityGateError.__module__ == "trading.paper_engine"`.

**Single source of the exit cost.** `PositionManager.close_position`, via `close_fill_price` → `fill_price_after_cost` in `trading/fill_cost.py` (`trading/position_manager.py:216-222`). It charges on every path — `SL_HIT`, `TP_HIT`, `MANUAL`, `SCALP_TP`, `SCALP_EXPIRED`, `SIGNAL` from this engine, and liquidation via the same helper at `:472` — using the position's own `side`, with PnL and the closing fee both computed from the *fill* price (`position_manager.py:227-235`).

**Call sites that must NOT adjust the price before calling `close_position`:** both closes in this file — `trading/paper_engine.py:841` (single-position branch) and `trading/paper_engine.py:930` (batch branch, inside the loop, not before it) — plus `agents/execution_agent.py:270` (`_auto_close_expired`) and `agents/execution_agent.py:311` (`_scalp_take_profit`). Passing a pre-adjusted price charges the cost twice. The single sanctioned exception is `price_is_final=True` (`trading/position_manager.py:172`, `:213-214`), reserved for callers that genuinely received a price from the exchange.

## Live trading path

Six modules in `trading/live/`, a PEP 420 namespace package (no `__init__.py`, nothing re-exported). Layering is one-directional: `tui.py` is standalone, `console.py` depends on `tui.py`, `safety.py` and `client.py` are independent, `engine.py` depends on both of those, `executor.py` on `engine.py`. Only `executor.py` touches the database. All six import without the Hyperliquid SDK — that import sits in `LiveExchange.__init__` (`client.py:115-124`).
---

### 26. `trading/live/safety.py`

**Role.** The one place deciding whether an order may touch real money. Network-free, small enough to audit alone. Rule: failure means don't send.

**Symbols.**

| Symbol | Kind | Signature | Loc |
| --- | --- | --- | --- |
| `Blocker` | `str, Enum` (17 members) | `class Blocker(str, Enum):` | `trading/live/safety.py:30` |
| `OrderRequest` | dataclass | `class OrderRequest:` | `trading/live/safety.py:53` |
| `OrderRequest.notional` | property | `def notional(self) -> float:` | `trading/live/safety.py:64` |
| `DayCounters` | dataclass | `class DayCounters:` | `trading/live/safety.py:69` |
| `DayCounters.readable` | property | `def readable(self) -> bool:` | `trading/live/safety.py:91` |
| `DayCounters.to_dict` | method | `def to_dict(self) -> Dict[str, Any]:` | `trading/live/safety.py:95` |
| `DayCounters.load` | method | `def load(self, path) -> None:` | `trading/live/safety.py:103` |
| `DayCounters.save` | method | `def save(self, path) -> bool:` | `trading/live/safety.py:127` |
| `DayCounters.rollover_if_needed` | method | `def rollover_if_needed(self, now: Optional[datetime] = None) -> None:` | `trading/live/safety.py:145` |
| `SafetyGate` | class | `class SafetyGate:` | `trading/live/safety.py:159` |
| `SafetyGate.__init__` | ctor | `def __init__(self, cfg: LiveConfig, env: Optional[dict] = None, state_path=None):` | `trading/live/safety.py:168` |
| `SafetyGate.persist` | method | `def persist(self) -> bool:` | `trading/live/safety.py:193` |
| `SafetyGate.private_key` | property | `def private_key(self) -> Optional[str]:` | `trading/live/safety.py:200` |
| `SafetyGate.redact` | method | `def redact(self, text: str) -> str:` | `trading/live/safety.py:213` |
| `SafetyGate.in_live_window` | method | `def in_live_window(self, now: Optional[datetime] = None) -> bool:` | `trading/live/safety.py:231` |
| `SafetyGate.master_blockers` | method | `def master_blockers(self, now: Optional[datetime] = None) -> List[Blocker]:` | `trading/live/safety.py:247` |
| `SafetyGate.order_blockers` | method | `def order_blockers(self, request: OrderRequest, free_collateral: float = 0.0, current_exposure: float = 0.0, symbol_exposure: float = 0.0, cloid: Optional[Any] = None, leverage: Optional[int] = None) -> List[Blocker]:` | `trading/live/safety.py:300` |
| `SafetyGate.can_send` | method | `def can_send(self, request: OrderRequest, free_collateral: float = 0.0, current_exposure: float = 0.0, symbol_exposure: float = 0.0, now: Optional[datetime] = None, cloid: Optional[Any] = None, leverage: Optional[int] = None) -> tuple:` | `trading/live/safety.py:347` |
| `SafetyGate.engage_kill_switch` | method | `def engage_kill_switch(self, reason: str) -> None:` | `trading/live/safety.py:384` |
| `SafetyGate.record_error` | method | `def record_error(self) -> None:` | `trading/live/safety.py:397` |
| `SafetyGate.record_success` | method | `def record_success(self) -> None:` | `trading/live/safety.py:410` |
| `SafetyGate.record_realized_pnl` | method | `def record_realized_pnl(self, pnl: float) -> None:` | `trading/live/safety.py:415` |
| `SafetyGate.record_order_sent` | method | `def record_order_sent(self) -> None:` | `trading/live/safety.py:419` |
| `logger` | module global | `logger = get_logger("live_safety")` | `trading/live/safety.py:27` |

**State.** `SafetyGate`: `cfg`, `env` (copy of `os.environ` taken at construction, or the injected dict), `counters`, `engaged`, `_private_key`, `_key_loaded`, `state_path` (default `data_store/live_counters.json`). `DayCounters`: `day_utc`, `orders_sent`, `realized_pnl`, `consecutive_errors`, `_unreadable`.

**Guards, in order.** `can_send` runs `master_blockers` first and enters `order_blockers` only if that list is empty, so a system fault short-circuits before per-order arithmetic.

`master_blockers` (`:247`): 0 `COUNTER_STATE_UNREADABLE` (`:261`) · 1 `LIVE_DISABLED` — `TRADEBOT_LIVE` not in `("1","true","yes")` (`:266`) · 2 `NOT_CONFIRMED` — `cfg.live_confirm_env` not truthy (`:271`) · 3 `KILL_SWITCH` — `self.engaged` **or** `TRADEBOT_LIVE_KILL_SWITCH` not in `("","0","false","no")` (`:277`) · 4 `MISSING_KEY` (`:283`) · 5 `OUTSIDE_WINDOW` — UTC half-open `[start, end)` from `cfg.live_window_utc` (`:287`, `:231`) · 6a `DAILY_ORDER_LIMIT` (`:291`) · 6b `DAILY_LOSS_LIMIT` — `realized_pnl <= -abs(cfg.max_daily_loss)` (`:293`) · 6c `TOO_MANY_ERRORS` (`:295`).

`order_blockers` (`:300`): `INVALID_INPUT` for `size <= 0` (`:319`) and again for `price <= 0` (`:321`) · `MISSING_CLOID` when not a close and no cloid (`:327`) · `LEVERAGE_TOO_HIGH` when `leverage is not None` and outside `1 <= int(leverage) <= cfg.max_leverage` (`:332`) · then `ORDER_TOO_LARGE` (`:337`), `POSITION_TOO_LARGE` on `symbol_exposure + notional` (`:339`), `EXPOSURE_TOO_LARGE` on `current_exposure + notional` (`:341`), `COLLATERAL_TOO_LOW` on `free_collateral - notional` (`:343`). `Blocker.RECONCILIATION_FAILED` (`:45`) is the one member never raised in first-party code.

**`engaged` is a one-way latch.** Written in exactly three places repo-wide: `False` at construction (`:177`), `True` in `engage_kill_switch` (`:388`), `True` inlined in `record_error` at the error limit (`:408`). No releaser exists. `record_success` zeroes `consecutive_errors` but never clears `engaged`. The env escape named in the log text (`:406`) works only on the next process start, and only against the second half of guard 3 — `self.env` was snapshotted at construction, so later `os.environ` mutation is invisible here.

**`engage_kill_switch` call sites — seven.** `engine.py:128` (reconcile mismatch, gated on `not cfg.auto_reconcile`), `:404` (SL install failed), `:555` (exchange unreadable), `:599` (`health_check` not-ok), `:737` (emergency flat not clean); `executor.py:573` and `:587` (SQLite insert raised or returned `None` for a real fill). `:737` and `:128` are unreachable in production — both sit behind `emergency_flat`. Live: `engine.py:404, 555, 599`, `executor.py:573, 587`.

**`record_realized_pnl` has a production caller.** Exactly one: `executor.py:847`, in `_record_close`, after `update_account_stats` (`:831`) and before the `POSITION_UPDATE` publish (`:849`). It is the only feed for `Blocker.DAILY_LOSS_LIMIT` (`:293`), so live does have a daily-loss breaker. The comment at `executor.py:841-846` claiming otherwise is stale — it contradicts the line beneath it. Other feeders: `record_error` at `engine.py:287, 298, 305, 446, 504, 650` and `executor.py:290, 972`; `record_success` at `engine.py:311`; `record_order_sent` at `engine.py:312`.

**Side effects.** Writes `data_store/live_counters.json` (or the injected path) via temp file then `Path.replace`, so a mid-write restart cannot leave a half file (`:136-139`). Logs each rejection with its reason list (`:376-380`). The private key is never written anywhere; `redact` (`:213`) scrubs it before logging. Loads from disk only when `state_path` was injected or `TRADEBOT_LIVE` is truthy, so paper mode never touches the filesystem (`:184-191`).

**Consumers.** `engine.py:32`; `run.py:289`; `live_doctor.py:186`; `tests/test_live_safety.py:19`.

**Defect.** `DayCounters.to_dict` is annotated `-> Dict[str, Any]` (`:95`) but `:22` imports only `Any, List, Optional`. `from __future__ import annotations` (`:14`) keeps the hint a string, so import and runtime are fine; anything resolving hints raises `NameError`.

---

### 27. `trading/live/client.py`

**Role.** The only module touching the Hyperliquid SDK. Fixes testnet/mainnet in one place, keeps the private key inside its boundary, pins order shape, normalizes errors into one type.

**Symbols.**

| Symbol | Kind | Signature | Loc |
| --- | --- | --- | --- |
| `OrderOutcome` | dataclass | `class OrderOutcome:` | `trading/live/client.py:34` |
| `OrderOutcome.describe` | method | `def describe(self) -> str:` | `trading/live/client.py:51` |
| `parse_order_response` | function | `def parse_order_response(raw: Any) -> OrderOutcome:` | `trading/live/client.py:59` |
| `LiveExchange` | class | `class LiveExchange:` | `trading/live/client.py:89` |
| `LiveExchange.__init__` | ctor | `def __init__(self, private_key: str, testnet: bool = True, account_address: Optional[str] = None, timeout: float = 15.0):` | `trading/live/client.py:97` |
| `LiveExchange.get_account_state` | method | `def get_account_state(self) -> Dict[str, Any]:` | `trading/live/client.py:160` |
| `LiveExchange.free_collateral` | method | `def free_collateral(self) -> float:` | `trading/live/client.py:164` |
| `LiveExchange.positions` | method | `def positions(self) -> List[Dict[str, Any]]:` | `trading/live/client.py:186` |
| `LiveExchange.total_notional` | method | `def total_notional(self) -> float:` | `trading/live/client.py:194` |
| `LiveExchange.symbol_notional` | method | `def symbol_notional(self, coin: str) -> float:` | `trading/live/client.py:211` |
| `LiveExchange.rate_limit` | method | `def rate_limit(self) -> Dict[str, Any]:` | `trading/live/client.py:226` |
| `LiveExchange.open_orders` | method | `def open_orders(self) -> List[Dict[str, Any]]:` | `trading/live/client.py:237` |
| `LiveExchange.asset_rules` | method | `def asset_rules(self) -> Dict[str, Dict[str, Any]]:` | `trading/live/client.py:242` |
| `LiveExchange.quantize_size` | method | `def quantize_size(self, coin: str, size: float) -> float:` | `trading/live/client.py:299` |
| `LiveExchange.quantize_price` | method | `def quantize_price(self, coin: str, price: float) -> float:` | `trading/live/client.py:341` |
| `LiveExchange.set_leverage` | method | `def set_leverage(self, coin: str, leverage: int, is_cross: bool = True) -> Any:` | `trading/live/client.py:362` |
| `LiveExchange.place_limit_order` | method | `def place_limit_order(self, coin: str, is_buy: bool, size: float, price: float, reduce_only: bool = False, cloid: Optional[Any] = None) -> OrderOutcome:` | `trading/live/client.py:372` |
| `LiveExchange.place_trigger_order` | method | `def place_trigger_order(self, coin: str, is_buy: bool, size: float, trigger_price: float, tpsl: str, reduce_only: bool = True) -> OrderOutcome:` | `trading/live/client.py:418` |
| `LiveExchange.cancel` | method | `def cancel(self, coin: str, oid: int) -> Any:` | `trading/live/client.py:462` |
| `LiveExchange.mid_price` | method | `def mid_price(self, coin: str) -> float:` | `trading/live/client.py:465` |
| `LiveExchange.cancel_all` | method | `def cancel_all(self, coin: str) -> Any:` | `trading/live/client.py:483` |
| `logger` | module global | `logger = get_logger("live_client")` | `trading/live/client.py:30` |

**State.** `testnet`, `_account_address`, `_rules` (per-asset precision cache, filled once), `wallet`, `address` (signer), `query_address` (lower-cased; the address actually holding positions — set to `account_address` when an API wallet is used), `base_url`, `info`, `exchange`.

**Points that propagate downstream.** `free_collateral` prefers `marginSummary.withdrawable`, falling back to `accountValue - total_notional`, because `accountValue` includes locked margin and would leave the collateral guard always seeing funds as available (`:174-184`). `total_notional` sums `|szi| * entryPx`, not `accountValue`, which is whole-account equity (`:196-209`). `asset_rules` derives lot precision from `szMin`, never `szMax` (`:269-273`), then increments `sz_decimals` until the step divides `szMin` exactly (`:281-287`). `quantize_size` uses `Decimal` + `ROUND_DOWN`: `0.5 / 1e-5` in float is `49999.99999999999`, and a naive `floor` drops a full step (`:331-336`). Everything below `client.py:355` is blocking; async callers wrap with `asyncio.to_thread`.

**Side effects.** Network only: `user_state`, `frontend_open_orders`, `meta`, `user_rate_limit`, `name_to_asset`, `order`, `cancel`, `update_leverage`, `all_mids`. No file or DB writes. Raises `ValueError` on empty private key (`:105`) and on `tpsl` outside `("tp","sl")` (`:439`); SDK calls are wrapped so transport failures return `OrderOutcome(ok=False)`.

**Consumers.** `engine.py:31`; `run.py:286`; `live_doctor.py:116`; `tests/test_live_engine.py:365`. `rate_limit` only from `live_doctor.py:229`; `cancel` by `oid` has no caller; `cancel_all` only from `LiveEngine.emergency_flat`, itself uncalled in production.

---

### 28. `trading/live/engine.py`

**Role.** Execution of real money. The exchange is the source of truth, not the local DB; TP/SL sit on the exchange so protection survives process death; no order bypasses `submit_order`; exchange rejection is a normal state, not an exception.

**Symbols.**

| Symbol | Kind | Signature | Loc |
| --- | --- | --- | --- |
| `LivePosition` | dataclass | `class LivePosition:` | `trading/live/engine.py:38` |
| `LiveEngine` | dataclass | `class LiveEngine:` | `trading/live/engine.py:54` |
| `LiveEngine.reconcile` | async method | `async def reconcile(self) -> Dict[str, Any]:` | `trading/live/engine.py:74` |
| `LiveEngine._fetch_remote_positions` | method | `def _fetch_remote_positions(self) -> List[Dict[str, Any]]:` | `trading/live/engine.py:134` |
| `LiveEngine._sizes_match` | staticmethod | `def _sizes_match(local: LivePosition, remote: Dict[str, Any]) -> bool:` | `trading/live/engine.py:172` |
| `LiveEngine._remote_context` | async method | `async def _remote_context(self, coin: str) -> Dict[str, float]:` | `trading/live/engine.py:188` |
| `LiveEngine.submit_order` | async method | `async def submit_order(self, coin: str, symbol: str, is_buy: bool, size: float, price: float, stop_loss: Optional[float] = None, take_profit: Optional[float] = None, leverage: int = 5, is_close: bool = False, cloid: Optional[Any] = None) -> Dict[str, Any]:` | `trading/live/engine.py:204` |
| `LiveEngine._attach_protection` | async method | `async def _attach_protection(self, coin: str, symbol: str, side: str, size: float, entry_price: float, stop_loss: float, take_profit: float, leverage: int) -> LivePosition:` | `trading/live/engine.py:351` |
| `LiveEngine.check_pending_fills` | async method | `async def check_pending_fills(self) -> List[Dict[str, Any]]:` | `trading/live/engine.py:421` |
| `LiveEngine.health_check` | async method | `async def health_check(self) -> Dict[str, Any]:` | `trading/live/engine.py:518` |
| `LiveEngine.run_loop` | async method | `async def run_loop(self, interval: float = 5.0, on_decision=None) -> None:` | `trading/live/engine.py:603` |
| `LiveEngine.emergency_flat` | async method | `async def emergency_flat(self, reason: str = "manual") -> Dict[str, Any]:` | `trading/live/engine.py:655` |
| `logger` | module global | `logger = get_logger("live_engine")` | `trading/live/engine.py:34` |

**State.** `gate`, `exchange`, `cfg`, `positions: Dict[str, LivePosition]` keyed by the caller-supplied `symbol`, and `now_fn` — an injectable clock so window behaviour is testable without waiting.

**`submit_order` ordering is load-bearing.** `_remote_context` reads free collateral, total notional and symbol notional from the exchange in one `to_thread` (`:188-202`); `gate.can_send` (`:236`); SL/TP presence check for non-close orders (`:255`); `set_leverage` (`:283`); `place_limit_order` (`:296`). Reordering any two leaves an orphan SL with no position, or a first order at the exchange's default leverage. A close pops `self.positions[symbol]` (`:315`). An opening fill of `filled_size <= 0` deliberately does **not** attach protection (`:326`) — a `reduce_only` SL for a nonexistent position is rejected and leaves an orphan order. The result dict carries `protected: bool` and the `LivePosition`.

**`_attach_protection` trigger direction.** `is_close_buy = side == "SHORT"` (`:378`): closing a LONG is a SELL, closing a SHORT a BUY. The comment at `:375-377` records that `is_buy=is_long` was a live bug — a LONG would get a BUY trigger that opens more size when the stop is touched. A failed SL install logs `critical` and engages the kill switch (`:400-404`) but still records the position.

**`check_pending_fills` (`:421`).** Reads open orders, mids and positions in one blocking call (`:437-441`); builds a per-coin `held` size map from exchange positions (`:453-458`), because a resting order may be partially filled; skips coins already in `self.positions`, matched on `.coin` not the symbol key (`:468`). Size is `abs(...)` (`:472`) so a late SHORT fill is protected rather than skipped. Defaults ±1% of `limitPx` or mid, on the correct side of entry for the direction (`:484-500`); leverage from `gate.cfg.max_leverage` (`:501`). Failure records an error and logs `critical` (`:503-508`).

**`health_check` (`:518`).** Short-circuits to `ok=False` if `gate.engaged` (`:540`). Otherwise it makes the exchange readable, engaging the kill switch immediately and returning on failure — an unreadable exchange means the bot does not know its positions, and that must stop now (`:555`). It then flags exchange positions with no local record, local records missing on the exchange, size mismatches, and resting orders with no local position. `ok = reachable and reconciled`; any failure engages the kill switch (`:599`).

**`run_loop` (`:603`).** Health check first; on failure log, sleep, `continue`. Then `check_pending_fills`. Then `on_decision()` only when a callback was supplied *and* `gate.counters.readable` (`:643`). `CancelledError` re-raises; anything else records an error and logs with traceback. Default `interval=5.0`.

**`emergency_flat` (`:655`) has no production caller.** Its only callers are `tests/test_live_engine.py:287, 297, 312, 337, 349` — no console command, no signal handler, no `run.py` path. The engine is parked at `run.py:323` as `_live_engine_obj` and never read again. Because `reconcile` is called from exactly one place (`emergency_flat` at `:728`), **`LiveEngine.reconcile` is also unreachable in production**; it is not called at startup — `run.py:307` calls `health_check` instead. `emergency_flat` cancels all resting orders first (`:676-685`), because a live GTC could fill mid-shutdown and open a fresh position; closes each at mid ± 0.1% (`:700-705`), recording a failure when the mid is unreadable; pops local state only for fills returning `filled_size > 0` (`:722`); reports `flattened: False` if any failure, any exchange-side position remains, **or** any position is still tracked locally (`:736-749`). Writes nothing to SQLite.

**Symbol-key mismatch — still present.** `_fetch_remote_positions` sets `"symbol": "{} / USDC:USDC".format(name)` (`:162`) and `check_pending_fills` keys new positions the same way (`:491`). But `submit_order` stores under whatever `symbol` the caller passed, and `LiveExecutor._open` passes `order.symbol` verbatim (`executor.py:471`) — a ccxt symbol such as `BTC/USDT:USDT` (`config.yaml:12`). `reconcile` compares the strings directly (`:88-99`), so the two sets are disjoint for every position: `common` is empty and everything falls into `only_local` and `only_remote`. Real and unresolved; invisible only because `reconcile` is itself unreachable. `health_check` is immune — both sides keyed by coin (`:568-569`) — and `LiveExecutor.check_positions` works around it with `lp_by_coin` (`executor.py:302`).

**Side effects.** Exchange calls only, via `asyncio.to_thread`: `user_state`, `frontend_open_orders`, `meta`, `all_mids`, `update_leverage`, `order`, `cancel`. Mutates `self.positions`, and through the gate `gate.engaged` plus the persisted day counters. No file or DB writes.

**Consumers.** `executor.py:46`; `run.py:287` (`health_check` at `run.py:307`, `run_loop` at `run.py:322`); `tests/test_live_engine.py:116, 271, 502, 569`.

---

### 29. `trading/live/executor.py`

**Role.** Adapter that lets `ExecutionAgent` speak live without knowing live exists. Implements the same surface as `PaperTradingEngine`, and is the only module here that writes to the database.

**Symbols.**

| Symbol | Kind | Signature | Loc |
| --- | --- | --- | --- |
| `_REFRESH_SECONDS` | module constant | `_REFRESH_SECONDS = 2.0` | `trading/live/executor.py:57` |
| `make_cloid` | function | `def make_cloid(prefix: str = "tb") -> str:` | `trading/live/executor.py:60` |
| `coin_of` | function | `def coin_of(symbol: str) -> str:` | `trading/live/executor.py:71` |
| `_finite_positive` | function | `def _finite_positive(value: Any) -> bool:` | `trading/live/executor.py:82` |
| `LiveExecutor` | class | `class LiveExecutor:` | `trading/live/executor.py:98` |
| `LiveExecutor.__init__` | ctor | `def __init__(self, engine: LiveEngine, event_bus=None):` | `trading/live/executor.py:101` |
| `LiveExecutor._get_repo` | async method | `async def _get_repo(self) -> Repository:` | `trading/live/executor.py:119` |
| `LiveExecutor._publish` | async method | `async def _publish(self, channel: str, data: Dict[str, Any], source: str) -> None:` | `trading/live/executor.py:125` |
| `LiveExecutor._log_agent` | async method | `async def _log_agent(self, log: AgentLog) -> None:` | `trading/live/executor.py:149` |
| `LiveExecutor._warn_shared_ledger` | method | `def _warn_shared_ledger(self) -> None:` | `trading/live/executor.py:162` |
| `LiveExecutor._log_wallet` | async method | `async def _log_wallet(self, label: str) -> None:` | `trading/live/executor.py:185` |
| `LiveExecutor.update_price` | method | `def update_price(self, symbol: str, price: float) -> None:` | `trading/live/executor.py:210` |
| `LiveExecutor.get_price` | method | `def get_price(self, symbol: str) -> Optional[float]:` | `trading/live/executor.py:237` |
| `LiveExecutor.check_positions` | async method | `async def check_positions(self) -> None:` | `trading/live/executor.py:259` |
| `LiveExecutor._engine_all_mids` | method | `def _engine_all_mids(self) -> Dict[str, Any]:` | `trading/live/executor.py:387` |
| `LiveExecutor.execute_order` | async method | `async def execute_order(self, order) -> Dict[str, Any]:` | `trading/live/executor.py:397` |
| `LiveExecutor._open` | async method | `async def _open(self, order) -> Dict[str, Any]:` | `trading/live/executor.py:408` |
| `LiveExecutor._persist_open` | async method | `async def _persist_open(self, order, result: Dict[str, Any], price: float, cloid: str, is_buy: bool, lev: int) -> Dict[str, Any]:` | `trading/live/executor.py:505` |
| `LiveExecutor._close` | async method | `async def _close(self, order) -> Dict[str, Any]:` | `trading/live/executor.py:655` |
| `LiveExecutor._find_open_row` | async method | `async def _find_open_row(self, symbol: str) -> Optional[Dict[str, Any]]:` | `trading/live/executor.py:747` |
| `LiveExecutor._record_close` | async method | `async def _record_close(self, *, row_id: int, symbol: str, side: str, size: float, entry_price: float, close_price: float, reason: str, cloid: Optional[str] = None) -> Optional[Dict[str, Any]]:` | `trading/live/executor.py:758` |
| `LiveExecutor._price_for` | staticmethod | `def _price_for(order) -> float:` | `trading/live/executor.py:887` |
| `_LivePositionManager` | class | `class _LivePositionManager:` | `trading/live/executor.py:912` |
| `_LivePositionManager.__init__` | ctor | `def __init__(self, executor: LiveExecutor):` | `trading/live/executor.py:928` |
| `_LivePositionManager.engine` | property | `def engine(self) -> LiveEngine:` | `trading/live/executor.py:932` |
| `_LivePositionManager.close_position` | async method | `async def close_position(self, position_id: int, price: float, reason: str = "MANUAL") -> Optional[Dict]:` | `trading/live/executor.py:935` |
| `logger` | module global | `logger = get_logger("live_executor")` | `trading/live/executor.py:49` |

**The six engine members.** `ExecutionAgent` touches exactly six things on its engine, and `LiveExecutor` implements exactly those:

| Member | Kind | Read by `agents/execution_agent.py` |
| --- | --- | --- |
| `update_price` | method | `:64`, `:71`, `:100` |
| `get_price` | method | `:96` |
| `execute_order` | method | `:164` |
| `check_positions` | method | `:192` |
| `position_manager` | attribute → `_LivePositionManager` | `:270`, `:311` |
| `_last_prices` | dict attribute | `:213`, `:266`, `:290` |

`_last_prices` is the one that is not a method and the one that is easy to miss: three policy routines read `self.engine._last_prices` directly, so an unpopulated cache breaks those plus `check_positions`. `get_price` is a line-for-line copy of `PaperTradingEngine.get_price` (`paper_engine.py:190`) — cache first, then `market_store` — and returns `None` rather than `0.0` when nothing is known, because `0.0` looks like a price. `get_account_summary` is not implemented and no first-party path calls it through the executor.

**State.** `engine`, `event_bus` (optional; `None` means publish nothing), `risk_manager = RiskManager()`, `_last_prices`, `_repo` (lazily from `get_db()`), `_last_refresh` (monotonic), `_publish_warned`, `_ledger_warned`, `position_manager`.

**`check_positions` (`:259`) repairs drift; it never closes.** Throttled to one exchange read per `_REFRESH_SECONDS = 2.0` (`:57`, `:277`) because the agent calls this about every 0.3 s. It reads the whole mid map in one call (`:285`), and on failure records an error and returns **without touching the database** (`:288-292`) — repairing from partial data would overwrite numbers that are still correct. It indexes engine positions **by coin, not symbol** (`:302`), the same workaround as above. A SQLite row with no matching engine position is left `OPEN` and logged (`:312-321`); a row with unusable `entry_price`/`quantity`, or no usable mid after a `market_store` fallback, is skipped. PnL is recomputed from the mark (`:349`). Local SL/TP are then **overwritten with the exchange's values** (`:361-365`), never the reverse — the trigger that will actually execute is the one on the exchange, and re-arming from a 0.3 s policy loop means sending real orders. The sync log is `debug`, since `_protect_breakeven` rewrites the SL column every 0.3 s. Exchange positions with no local row are logged `error` but **no row is inserted** (`:381-385`) — the position may be the operator's. Ordering matters: `ExecutionAgent.check_positions` runs `_protect_breakeven` *first* and `engine.check_positions()` only after (`execution_agent.py:192`), so the repair runs last and wins.

**`execute_order` (`:397`).** `OPEN_LONG`/`OPEN_SHORT` → `_open`, `CLOSE` → `_close`, anything else → a `HOLD` success dict.

**The quantity and leverage gate (`_open`, `:408-479`).** SL and TP must both be present, else `missing_tpsl` (`:415-422`). Then two hard rejects with no defaults: `quantity` must pass `_finite_positive`, else `invalid_quantity` (`:438-449`); `leverage` must be a real `int >= 1` and not a `bool`, else `invalid_leverage` (`:450-461`). Both replace `size=order.quantity or 0.0` and `leverage=order.leverage or 5`. `Order.quantity` defaults to `None` (`trading/models.py:41`) and `DecisionAgent` now always sets it (`agents/decision_agent.py:314-324`), so these branches should be unreachable in production — they exist so an upstream regression cannot put `size=0.0` on the wire. `math.isfinite` is what makes the check bite: `bool(float("nan"))` is `True`, so the old `if price:` form let NaN through to a message mentioning only `price <= 0`. Price comes from `_price_for` (`:464`), which raises `ValueError` when neither `order.price` nor `market_store` has a usable number. A gate rejection is logged as a warning and **not** counted as an error — `submit_order` already records its own, and triple-counting three safe rejections would latch the kill switch on a correct system (`:481-491`). If `result["protected"]` is false the order is resting and nothing is written (`:493-500`).

**`_persist_open` (`:505`) writes exchange truth, not the order.** `qty` comes from `outcome.filled_size`, `entry` from `outcome.avg_price` (falling back to the requested price only when the exchange returned no average) (`:523-524`) — never `order.quantity` / `order.price`, because an order can be partially filled and the order is not the truth. If either value fails `_finite_positive` the fill is **not** written and manual reconciliation is demanded (`:526-539`); an invented number is worse than none because it looks right on the dashboard. Otherwise: liquidation price from `risk_manager.calculate_liquidation_price`, margin as `qty * entry / lev` — the same arithmetic as `PositionManager.open_position` (`position_manager.py:67`), so live and paper rows compare like for like — a TAKER fee (`:544-549`), a `Position` insert with `reasoning` prefixed `"LIVE "` as the marker separating real money from simulation (`:565`), and a `Trade` insert. If the position insert raises **or** returns `None`, it logs `critical` and engages the kill switch (`:567-593`): the fill happened on the exchange and there is now no local record. A failed `Trade` insert is only a warning (`:602-607`). Publishes `Channels.POSITION_UPDATE` (`action="OPENED"`) and `Channels.TRADE_EXECUTED`, writes one `AgentLog`; publish and audit-log failures are swallowed, so neither a dashboard problem nor a DB hiccup loses a real fill (`:125-160`).

**`_close` (`:655`).** `self.engine.positions` is the only source of positions here, no exchange fallback (`:663`). `is_buy = position.side == "SHORT"`, mid price, limit at mid ± 0.1%, cloid prefixed `"close"`. Success requires `filled_size > 0`: `success` here means **confirmed filled**, not "sent". A resting `reduce_only` close closes nothing, and reporting a close while the position lives is the worst lie available in this path (`:707-719`). `_find_open_row` (`:747`) then locates the SQLite row by scanning `get_open_positions()` and matching on **coin**, not the symbol string, so it survives the ccxt-vs-`BTC / USDC:USDC` key mismatch.

**`_record_close` (`:758`).** One helper for both close paths — `_close` and `_LivePositionManager.close_position` — so no path can close on the exchange and leave an `OPEN` row forever. Returns `None` rather than writing a PnL of `0.0` when the entry or close price is unusable (`:777-786`). The body is inside `try/except` (`:788-884`): a bookkeeping failure must not erase a fill that already happened. Gross PnL from `risk_manager.calculate_pnl`; the **opening fee is read back from the `trades` table** rather than recomputed, since taker rates can change mid-session (`:800-804`); net = gross − fee_open − fee_close. `repo.close_position` is treated as a claim — not-claimed means another caller already closed the row, and double-counting is worse than skipping (`:813-818`). It then inserts the `CLOSE` trade, recomputes `update_account_stats` with no `balance` argument, since the live balance is the exchange's (`:830-839`), calls `gate.record_realized_pnl(net)` at `:847`, publishes `Channels.POSITION_UPDATE` with `action="CLOSED"` — load-bearing, since `DecisionAgent.sense()` keys cooldown and loss streak off that string — and writes an `AgentLog` with the fee/ROE breakdown.

**`_LivePositionManager` (`:912`).** Only `close_position` is implemented; the other `PositionManager` methods are deliberately absent, because an uncalled method is a second surface that can be wrong and `take_balance_snapshot` cannot be filled honestly live. `__init__` touches the exchange not at all, so tests can build `LiveExecutor(_Engine(rec))` against a stub with only `submit_order` and `mid_price`. `close_position` (`:935`) finds the row by `position_id`, then takes **size and entry from the exchange**, not from `self.engine.positions` — `submit_order(is_close=True)` already popped that entry (`engine.py:315`), and late fills never entered it. The `price` parameter is ignored on purpose: the close is priced off the exchange mid at send time, since a caller's price is milliseconds stale and an unfilled close closes nothing. Each of these returns `None` without writing: row not found; the exchange positions call raised (records an error, `:971`); the coin is absent or zero-size on the exchange, leaving the local row `OPEN` (`:991-1000`); the exchange holds **more** than the recorded quantity by over 1%, because closing the exchange size there would hide a real exposure difference exactly when it most needs to be visible (`:1002-1013`); or the mid is unusable. Otherwise it closes at mid ± 0.1% with a `"close"` cloid, requires `filled_size > 0`, and hands off to `_executor._record_close`. A gate rejection is deliberately not counted as an error, same reason as `_open` (`:1033-1040`).

**Side effects.** SQLite: `insert_position`, `insert_trade`, `get_open_positions`, `get_trades_by_position`, `close_position`, `update_position_pnl`, `update_position_sl_tp`, `get_trade_stats`, `update_account_stats`, `insert_agent_log`. Event-bus publishes on `Channels.POSITION_UPDATE` and `Channels.TRADE_EXECUTED`. Exchange reads via `asyncio.to_thread`: `all_mids`, `positions`, `mid_price`, `free_collateral`, `total_notional`. Mutates `_last_prices`, `_repo`, `_last_refresh`, both warn-once flags, `gate.engaged`, and the persisted daily counters. **`account.balance` is never touched** — no `apply_balance_delta`, no margin refund, no `update_balance`; `_warn_shared_ledger` (`:162`) warns once that live positions land in the same `positions` table as paper while `account.balance` stays the paper ledger, making the dashboard's `wallet_balance = balance + SUM(margin)` formula wrong for live rows, and declines to paper over that with a guess.

**Consumers.** `run.py:288` (`LiveExecutor(engine, self.event_bus)` at `run.py:328`; passing the bus is mandatory or the dashboard never sees a live position); `tests/test_live_executor.py:10, 143`.

---

### 30. `trading/live/tui.py`

**Role.** Terminal UI primitives: arrow-key reading, in-place full-screen redraw, and a one-line text editor. Split out of `console.py` for the same reason `decide_mode` is split out of `ask_mode` — what lives here is testable without a human, and `console.py` is what calls it. **No first-party imports at all**, so it loads anywhere.

**Symbols.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `UP`…`CTRL_C` | constants | `UP = "up"` … `CTRL_C = "ctrl_c"` | `trading/live/tui.py:33-40` |
| `WIDTH` | constant | `WIDTH = 68` | `trading/live/tui.py:42` |
| `_WINDOWS_KEYS` | dict | `{"H": UP, "P": DOWN, "M": RIGHT, "K": LEFT, …}` | `trading/live/tui.py:45` |
| `NotATerminal` | class | `class NotATerminal(RuntimeError):` | `trading/live/tui.py:52` |
| `enable_vt_processing` | function | `def enable_vt_processing() -> bool:` | `trading/live/tui.py:61` |
| `ANSI_CLEAR`…`ANSI_SHOW_CURSOR` | constants | `ANSI_CLEAR = "\x1b[2J\x1b[H"` … | `trading/live/tui.py:89-94` |
| `Screen` | class | `class Screen:` | `trading/live/tui.py:97` |
| `Screen.start` / `.stop` | methods | `def start(self) -> None:` / `def stop(self) -> None:` | `:116` / `:121` |
| `Screen.draw` | method | `def draw(self, lines: Sequence[str]) -> None:` | `trading/live/tui.py:132` |
| `display_width` | function | `def display_width(text: str) -> int:` | `trading/live/tui.py:156` |
| `is_tty` | function | `def is_tty() -> bool:` | `trading/live/tui.py:184` |
| `_read_windows` | function | `def _read_windows() -> Optional[str]:` | `trading/live/tui.py:191` |
| `_read_posix` | function | `def _read_posix() -> Optional[str]:` | `trading/live/tui.py:213` |
| `KeyReader` | class | `class KeyReader:` | `trading/live/tui.py:239` |
| `KeyReader.get` / `.wait` | methods | `def get(self) -> Optional[str]:` / `def wait(self) -> str:` | `:278` / `:284` |
| `Option` | dataclass | `class Option:` — `label: str`, `value: object = None`, `detail: str = ""`, `enabled: bool = True`, `danger: bool = False` | `trading/live/tui.py:301` |
| `MenuResult` | dataclass | `class MenuResult:` — `selected: Optional[Option] = None`, `cancelled: bool = False` | `trading/live/tui.py:312` |
| `Menu` | class | `class Menu:` | `trading/live/tui.py:328` |
| `TextField` | class | `class TextField:` | `trading/live/tui.py:427` |
| `_CANCELLED` | sentinel | `_CANCELLED = _Cancelled()` | `trading/live/tui.py:502` |
| `run_menu` / `run_text` | functions | `def run_menu(title: str, options: Sequence[Option], footer: str = "", …)` / `def run_text(prompt: str, secret: bool = False, max_length: int = 200, …)` | `:506` / `:526` |
| `is_cancelled` / `fallback_prompt` | functions | `def is_cancelled(value: object) -> bool:` / `def fallback_prompt(prompt: str, secret: bool = False) -> str:` | `:545` / `:550` |

**`display_width` is what keeps the box borders straight (`:156`).** `len()` is wrong twice over here: the menus are coloured, so `len()` counts SGR escape bytes as visible characters, and the Indonesian strings carry CJK. It strips `\x1b[...letter` sequences (`:166`), skips combining marks, and counts East Asian `W`/`F` width as two cells (`:171`).

**`Screen.draw` deliberately does not use a full clear (`:132`).** It emits `ANSI_HOME`, then each line padded to `self.width` and followed by `ANSI_ERASE_LINE`, then one `ANSI_ERASE_DOWN`. A `2J` per frame flashes on every keypress. `stop()` writes `ANSI_SHOW_CURSOR` unconditionally-if-active — leaving the cursor hidden makes the operator think the bot hung, at which point they press Ctrl+C.

**Windows and POSIX keys are read by two separate functions.** `_read_windows` (`:191`) uses `msvcrt.getwch()`, where an arrow is a `\x00`/`\xe0` prefix followed by a code mapped through `_WINDOWS_KEYS`; `_read_posix` (`:213`) parses escape sequences. `KeyReader` (`:239`) picks by `os.name != "nt"` and enables `termios` raw mode **only** on POSIX (`:249`, `:252`) — forcing it on Windows corrupts the console.

**`Option.enabled` and the TUI contract.** `Menu._first_enabled` / `.step` (`:341`, `:363`) skip disabled rows; a menu where nothing is selectable would otherwise trap the operator. `fallback_prompt` (`:550`) is the escape hatch for non-tty stdout — it is what keeps CI and piped runs from drawing half a screen and hanging.

**State.** None at module level beyond the constants. `Screen` holds `width`/`active`; `Menu` and `TextField` hold the cursor and the option list; `KeyReader` holds the saved `termios` state.

**Side effects.** Writes ANSI to `sys.stdout` and reads `sys.stdin`. `enable_vt_processing` (`:61`) calls `ctypes` on the Windows console handle — the same trick `core/logger.py:14-15` uses. `import msvcrt` (`:192`) and `import termios` (`:259`, `:269`) are function-local so the module still imports on the other platform.

**Consumers.** `trading/live/console.py:180`, `:196`, `:417`, `:464`, `:596`, `:624` (`from trading.live import tui`) and `:240`, `:567`, `:664` (`from trading.live.tui import Option`). No test and no root script imports it directly.

---

### 31. `trading/live/console.py`

**Role.** The operator-facing mode menu and rules editor. Its stated purpose is to make live trading impossible to reach by accident: three independent confirmations, and a refusal always resolves to `paper` rather than to an error.

**Symbols.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `CONFIRM_PHRASE` | constant | `CONFIRM_PHRASE = "SAYA MENGERTI"` | `trading/live/console.py:38` |
| `ModeDecision` | dataclass | `class ModeDecision:` — `mode: Mode`, `private_key: Optional[str] = None`, `address: Optional[str] = None`, `reason: str = ""`, `refused: bool = False` | `trading/live/console.py:42` |
| `typed_addr_mismatch` | function | `def typed_addr_mismatch(shown: Optional[str], typed: Optional[str]) -> bool:` | `trading/live/console.py:52` |
| `derive_address` | function | `def derive_address(private_key: str) -> Optional[str]:` | `trading/live/console.py:65` |
| `decide_mode` | function | `def decide_mode(choice: str, typed_phrase: str, typed_address: str, shown_address: Optional[str], env: Optional[dict] = None) -> ModeDecision:` | `trading/live/console.py:78` |
| `interactive` | function | `def interactive() -> bool:` | `trading/live/console.py:172` |
| `limits_summary` | function | `def limits_summary(cfg: LiveConfig) -> List[str]:` | `trading/live/console.py:225` |
| `mode_options` | function | `def mode_options(cfg: LiveConfig, has_key: bool):` | `trading/live/console.py:238` |
| `show_banner` | function | `def show_banner() -> None:` | `trading/live/console.py:261` |
| `ask_mode` | function | `def ask_mode(cfg: LiveConfig) -> ModeDecision:` | `trading/live/console.py:405` |
| `_do_confirmations` | function | `def _do_confirmations(choice: str, info: dict) -> Optional[ModeDecision]:` | `trading/live/console.py:462` |
| `EDITABLE` | constant | 7-tuple of `(field, label, kind, lo, hi)` | `trading/live/console.py:491` |
| `validate_rule` | function | `def validate_rule(field: str, value, kind: str, lo: float, hi: float):` | `trading/live/console.py:505` |
| `check_consistency` | function | `def check_consistency(cfg: LiveConfig) -> List[str]:` | `trading/live/console.py:532` |
| `rule_options` | function | `def rule_options(cfg: LiveConfig):` | `trading/live/console.py:565` |
| `edit_rules` | function | `def edit_rules(cfg: LiveConfig) -> LiveConfig:` | `trading/live/console.py:587` |
| `edit_rules_plain` | function | `def edit_rules_plain(cfg: LiveConfig) -> LiveConfig:` | `trading/live/console.py:654` |

**All three gates live in one pure function (`:78`).** `decide_mode` takes its `env` as a parameter, reads no terminal, and touches no real environment, so the safety rules are unit-testable. Choice `"1"` is paper and returns immediately. Any choice other than `"1"`/`"2"`/`"3"` is a refusal, not an error (`:97-101`). For testnet/mainnet, three checks run in order and **each one returns `paper` with `refused=True`**: `HYPERLIQUID_PRIVATE_KEY` must be present (`:106`), the typed phrase must equal `CONFIRM_PHRASE` exactly (`:117`), and the typed address must match the shown one (`:124`). There is no path that raises. `typed_addr_mismatch` (`:52`) compares case-insensitively and treats either side empty as a mismatch, so an all-blank entry cannot pass.

**`validate_rule` rejects `NaN` explicitly, and that is the point (`:505`).** `float("NaN")` is a valid Python float, but every comparison against NaN is False — so a NaN bound would make the guard "never fire": protection that looks present while protecting nothing (`:513-517`, `:523-524`). The second return slot is `None` rather than `""` so a caller can distinguish "no problem" from "an error message that happens to be empty" (`:509-511`).

**Edited rules are never written to disk.** `edit_rules` (`:587`) mutates the `LiveConfig` object it returns and leaves `config.yaml` alone, because that file is committed to git and a limit changed inside git leaves no trace of who changed it (`:591-594`). This is the same reasoning as `config.live.enabled` being forced to `False` in `core/config.py:700`. `check_consistency` (`:532`) is advisory, not blocking: it returns warnings for `max_order_notional > max_position_notional`, `max_total_notional < max_position_notional`, and `max_daily_loss >= max_total_notional` (`:541-555`) — the last of which means the bot could lose its whole exposure in one day.

**Two front ends, one decision.** `ask_mode` (`:405`) uses the arrow-key TUI when `interactive()` holds, and otherwise falls back to `_ask_mode_plain`; both call the same `decide_mode`. A cancelled menu returns `mode="paper", refused=True` (`:429-430`), never `None`, so `_choose_mode` in `run.py` always has a decision object to read `mode` off.

**State.** None. `ModeDecision` is a plain dataclass; the module holds only the `CONFIRM_PHRASE`/`EDITABLE` constants.

**Side effects.** Reads `os.environ["HYPERLIQUID_PRIVATE_KEY"]` (`:423`) and the `LiveConfig` handed in by the caller. Writes ANSI and prompts to `sys.stdout`/`sys.stdin`; `_read_secret` (`:208`) uses `getpass`. `derive_address` (`:65`) imports `eth_account` **function-locally** and returns `None` on `ImportError` or on a bad key, logging the failure — so a machine without the SDK degrades to "cannot show the address", never to "show a wrong address". No network, no file, no database.

**Consumers.** `run.py:198` (`from trading.live.console import ModeDecision`) and `run.py:225` (`from trading.live.console import ask_mode`), both inside `_choose_mode` and both function-local, so `run.py` still imports when the console is unavailable — `_choose_mode` catches `ImportError` and returns `paper` (`run.py:231-233`). Also the root throwaway script `_smoke_console.py:20`.

## Data, persistence, UI and native code

### 32. `data/price_feed.py`

**Role.** Multi-tier OHLCV/ticker/orderbook front door: Hyperliquid (Tier 0) → ccxt Binance Futures (Tier 1) → yfinance → CoinGecko.

**Symbols.**

| Symbol | Signature | Loc |
| --- | --- | --- |
| `COINGECKO_MAP` | 20 ids, `BTC`→`bitcoin` … `TRX`→`tron` | `:31` |
| `SPECIAL_YF_MAP` | `SUI`→`SUI20947-USD`, `PEPE`→`PEPE24478-USD`, `APT`→`APT21794-USD`, `UNI`→`UNI7083-USD`, `SHIB`→`SHIB-USD` | `:54` |
| `VERIFIED_FUTURES` | 20 symbols incl. `ATOM`, `INJ` | `:63` |
| `_symbol_to_yf(symbol: str) -> str` | func | `:69` |
| `_symbol_to_base(symbol: str) -> str` | func | `:77` |
| `PriceFeed.__init__(self, event_bus: EventBus)` | ctor | `:93` |
| `.initialize(self)` | async | `:109` |
| `.close(self)` | async | `:136` |
| `.start_streaming(self)` | async, returns the `create_task` handle | `:146` |
| `._fetch_ohlcv_yf(self, symbol: str, timeframe: str = "1m", limit: int = 100) -> List[list]` | async | `:160` |
| `.fetch_ohlcv(self, symbol: str, timeframe: str = "1m", limit: int = 100, save_to_db: bool = True) -> List[list]` | async | `:198` |
| `._fetch_ticker_yf(self, symbol: str) -> Optional[dict]` | async | `:273` |
| `._fetch_ticker_coingecko(self, symbol: str) -> Optional[dict]` | async | `:305` |
| `.fetch_ticker(self, symbol: str) -> Optional[dict]` | async | `:340` |
| `.fetch_funding_rate(self, symbol: str) -> Optional[dict]` | async | `:417` |
| `.fetch_mark_price(self, symbol: str) -> Optional[float]` | async | `:481` |
| `.fetch_order_book(self, symbol: str, limit: int = 5) -> Optional[dict]` | async | `:515` |
| `.fetch_all_tickers(self) -> Dict[str, dict]` | async | `:557` |
| `.price_update_loop(self)` | async | `:566` |
| `.stop(self)` | sync | `:607` |
| `.update_symbols(self, new_symbols: List[str])` | sync, mutates `config.symbols` | `:611` |
| `.discover_top_volume_symbols(self, limit: int = 10) -> List[str]` | async | `:616` |
| `.get_last_price(self, symbol: str) -> Optional[float]` | sync | `:689` |
| `.get_all_last_prices(self) -> Dict[str, float]` | sync | `:693` |
| `_active_price_feed: Optional["PriceFeed"]` | module global, rebound on every ctor | `:700` |
| `get_price_feed() -> Optional["PriceFeed"]` | func | `:703` |

**State.** `event_bus`, `config`, `_exchange`, `_running`, `_last_prices`, `_ccxt_available`, `hyperliquid`, `_hl_coins`.

**Side effects.** Network: `api.coingecko.com/api/v3/simple/price?ids=…&vs_currencies=usd&include_24hr_vol=true&include_24hr_change=true` (4 s) and `…/coins/markets?vs_currency=usd&order=volume_desc&per_page=50&page=1` (5 s); ccxt `timeout: 4000`, `enableRateLimit`, `defaultType: "future"`, `wait_for` 4.0 s OHLCV / 3.0 s ticker+funding / 2.0 s book / 5.0 s tickers. **No retry, no cache TTL** — a ccxt failure flips `_ccxt_available = False` permanently. DB write: only Hyperliquid/Binance candles via lazy `from database.db import get_db` + `Repository.insert_candles_batch` (`:240-242`); `persist` is set only when a real exchange returned rows, so yfinance output is never stored. OHLC invariants re-checked at `:256-258`. Writes `market_store` (`set_ticker/set_price/set_order_book/set_funding`), publishes `Channels.PRICE_UPDATE`. **Loop sleeps 1.5 s** (`:605`), 0.15 s between per-symbol refetches; a symbol is refetched when missing or `get_price_age(s) > 10.0`.

**Consumers.** `run.py:54`; `agents/analysis_agent.py:14`; `dashboard/callbacks/update_callbacks.py:958`.

### 33. `data/hyperliquid_feed.py`

**Role.** Hyperliquid public-DEX transport (REST `/info` + unauthenticated `wss://…/ws`). Computes nothing; forwards to `market_store` and `core.microstructure`.

**Symbols.** `HL_REST_URL = "https://api.hyperliquid.xyz/info"` `:46`; `HL_WS_URL = "wss://api.hyperliquid.xyz/ws"` `:47`; `WS_MAX_SIZE = 2 ** 22` `:50`; `WS_PING_INTERVAL = 30.0` `:53`; `WS_RECV_TIMEOUT = 60.0` `:56`; `coin_to_symbol(coin: str) -> str` `:59`; `symbol_to_coin(symbol: str) -> str` `:64`; `_ensure_native_kernel(self) -> bool` `:99`; `_post_info_sync(self, payload: dict, timeout: float = 8.0)` `:126`; `post_info(self, payload: dict, timeout: float = 8.0)` `:139`; `fetch_universe(self) -> List[dict]` `:143`; `discover_top_volume_symbols(self, limit: int = 10) -> List[str]` `:161`; `fetch_all_mids(self) -> Dict[str, float]` `:203`; `fetch_l2_book(self, coin: str, depth: int = 20) -> Optional[dict]` `:236`; `fetch_candles(self, coin: str, interval: str = "1m", limit: int = 120) -> List[list]` `:253`; `_parse_book(coin: str, levels: list, ts: Optional[int]) -> dict` static `:313`; `_parse_candle(data: dict) -> Optional[dict]` static `:353`; `set_watched_coins(self, symbols: List[str])` `:373`; `_subscriptions(self) -> List[dict]` `:377`; `_subscribe(self, ws)` `:393`; `_ping_loop(self, ws)` `:402`; `_handle_message(self, raw: str)` `:411`; `websocket_loop(self)` `:492`; `stop(self)` `:553`; `is_connected(self) -> bool` `:560`; `get_status(self) -> dict` `:563`.

**State.** `_running`, `_ws_connected`, `_known_coins`, `_watched_coins`, `_last_mid_time`, `_last_book_time`, `_msg_counts`, `_rest_failures`, `_native_kernel`.

**Side effects.** REST `{"type": …}`: `meta`, `metaAndAssetCtxs`, `allMids`, `l2Book{coin,depth}`, `candleSnapshot{req:{coin,interval,startTime,endTime}}` — `urllib` in a thread, 8 s timeout, `User-Agent: Mozilla/5.0`. WS: `open_timeout=10`, `ping_interval=None` (app-level JSON ping every 30 s), `wait_for(ws.recv(), 60.0)`, reconnect backoff 1.0 s doubling to a 30.0 s cap — the only retry in `data/`. **Five subscribed message types**: `allMids` (global) plus, per watched coin, `l2Book`, `candle` interval `"1m"`, `activeAssetCtx`, `trades`. `candleSnapshot`'s `limit` is a *window* (`start_ms = now − span*(limit+2)`), not a truncation. Writes `market_store.set_price/set_order_book/set_live_candle/set_funding/set_open_interest/set_recent_trades` and `microstructure.ingest_l2(symbol, bids, asks)`. No TTL; freshness surfaces only via `get_status()["mid_age_s"]/["book_age_s"]`. `_rest_failures` is reset to 0 on connect and never incremented.

**Consumers.** `data/price_feed.py:27`; `dashboard/callbacks/update_callbacks.py:961` (`feed.hyperliquid.get_status()["message_counts"]`, differenced into per-channel rates).

### 34. `data/macro_fetcher.py`

**Role.** FRED series + Forex Factory weekly calendar, and a hand-rolled bull/bear score of the two.

**Symbols.** `FRED_SERIES` `:21` = `DFF`→`FED_FUNDS_RATE`, `CPIAUCSL`→`CPI`, `DTWEXBGS`→`DXY`, `UNRATE`→`UNEMPLOYMENT`, `T10Y2Y`→`YIELD_CURVE`, `VIXCLS`→`VIX`. `MacroFetcher.__init__(self, fred_api_key: str = None)` `:38`; `fetch_fred_data(self) -> List[MacroData]` `:42`; `_fetch_fred_series(self, series_id: str) -> Optional[dict]` `:73`; `fetch_forex_factory_calendar(self) -> List[dict]` `:104`; `fetch_all(self) -> dict` `:137`; `interpret_macro_context(self, fred_data: List[MacroData], calendar_events: List[dict]) -> dict` `:149`.

**State.** `fred_api_key`, `config` (stored, never read here).

**Side effects.** `https://api.stlouisfed.org/fred/series/observations` (`file_type=json, sort_order=desc, limit=1`, 15 s) — six sequential requests, no retry, no `User-Agent`; `https://nfs.faireconomy.media/ff_calendar_thisweek.json` (15 s). FRED `"."` counts as missing. `run.py:247` builds `MacroFetcher()` **with no key**, so FRED is always skipped in the shipped run.

**Consumers.** `agents/analysis_agent.py:205-214` → `repo.upsert_macro`; job `macro_update` every 21600 s (`run.py:600-604`).

### 35. `data/news_fetcher.py`

**Role.** RSS + optional CryptoPanic collection with in-process title dedup.

**Symbols.** `NewsFetcher.__init__(self)` `:27`; `fetch_rss(self) -> List[NewsItem]` `:31`; `_parse_rss_feed(self, url: str) -> List[NewsItem]` `:44`; `fetch_cryptopanic(self) -> List[NewsItem]` `:82`; `fetch_all(self) -> List[NewsItem]` `:125`; `clear_cache(self)` `:136`; `_extract_source_name(url: str) -> str` static `:144`.

**State.** `config`, `_seen_titles: set`.

**Side effects.** `config.news_rss` = CoinDesk + Cointelegraph (`core/config.py:402-405`); `feedparser.parse` in executor, top 20 entries/feed. CryptoPanic `{cryptopanic_url}?auth_token=…&public=true&filter=important`, 10 s, top 20 — token is `None` in the shipped config so this returns `[]`. Summaries HTML-stripped to 500 chars. No retry, no TTL; `clear_cache` keeps the last 200 of >500 titles.

**Consumers.** `agents/news_agent.py:13` (`fetch_all` then `clear_cache` `:38-39`, `insert_news` `:119`).

### 36. `data/sentiment.py`

**Role.** VADER per headline, FinBERT on a 15-minute batch.

**Symbols.** `SentimentAnalyzer.__init__(self, use_finbert: bool = True)` `:23`; `initialize(self)` `:29`; `_load_vader(self)` `:50`; `_load_finbert(self)` `:55`; `analyze_vader(self, text: str) -> Dict` `:65`; `analyze_vader_async(self, text: str) -> Dict` `:93`; `analyze_finbert(self, text: str) -> Dict` `:98`; `analyze_finbert_async(self, text: str) -> Dict` `:133`; `analyze_finbert_batch(self, texts: List[str]) -> List[Dict]` `:138`; `aggregate_sentiment(self, sentiments: List[Dict]) -> Dict` `:175`.

**State.** `_vader`, `_finbert_pipeline`, `_use_finbert`, `_finbert_loaded`.

**Side effects.** Loads `vaderSentiment` and, if enabled, `transformers.pipeline("sentiment-analysis", model="ProsusAI/finbert", …, device=-1)` — a ~500 MB CPU download on first run. No network after load, no DB. VADER ±0.05, aggregate ±0.1, FinBERT input truncated to 512 chars.

**Consumers.** `run.py:246,375`; `agents/analysis_agent.py:16`; `agents/news_agent.py:14`. Batch job `finbert_batch` every 900 s (`run.py:607-611`).

### 37. `database/db.py`

**Role.** aiosqlite singleton plus the one `SCHEMA_SQL` migration — **11 tables**.

**Symbols.** `SCHEMA_SQL` `:13` = 11 `CREATE TABLE IF NOT EXISTS` + 7 `CREATE INDEX IF NOT EXISTS`. `Database.__init__(self, db_path: str = None)` `:160`; `connect(self)` `:166`; `close(self)` `:196`; `conn` property `:204`; `execute(self, sql: str, params: tuple = None)` `:210`; `executemany(self, sql: str, params_list: list)` `:216`; `fetchone(self, sql: str, params: tuple = None)` `:220`; `fetchall(self, sql: str, params: tuple = None)` `:225`; `commit(self)` `:230`; `_db` global `:236`; `get_db() -> Database` `:239`; `init_db() -> Database` `:248`; `close_db()` `:253`.

**State.** `db_path` (default `data_store/trading_bot.db`, `core/config.py:388`), `_connection`, global `_db`.

**Side effects.** mkdir parents, connect, `PRAGMA journal_mode=WAL`, **`PRAGMA busy_timeout=15000`** (`:187`, raised from 5000 after 1150 recorded lock failures crashed the execution loop), `row_factory = aiosqlite.Row`, `executescript(SCHEMA_SQL)` + commit.

**Schema.** `candles` `id, symbol, timeframe, timestamp, open, high, low, close, volume, created_at` — `UNIQUE(symbol,timeframe,timestamp)`, `idx_candles_lookup`. `positions` `id, symbol, side, entry_price, quantity, leverage DEFAULT 1, margin, liquidation_price, stop_loss, take_profit, unrealized_pnl DEFAULT 0, status DEFAULT 'OPEN', opened_at, closed_at, close_price, realized_pnl, close_reason, reasoning` — `idx_positions_status(status,symbol)`. `trades` `id, position_id REFERENCES positions(id), symbol, side, price, quantity, fee DEFAULT 0, fee_type DEFAULT 'TAKER', trade_type, executed_at` — `idx_trades_time(executed_at)`. `signals` `id, symbol, timestamp, signal_type, signal_value, direction, confidence, source` — `idx_signals_time(symbol,timestamp)`. `news` `id, title, source, url, published_at, fetched_at, sentiment_vader, sentiment_finbert, sentiment_label, impact_level DEFAULT 'LOW', content_summary` — `idx_news_time(fetched_at)`. `agent_logs` `id, agent_name, action, reasoning, input_data, output_data, timestamp` — `idx_agent_logs_time(agent_name,timestamp)`. `account` `id, balance, initial_balance, total_pnl, total_trades, winning_trades, losing_trades, max_drawdown, peak_balance, sharpe_ratio, profit_factor, updated_at`. `balance_history` `id, balance, unrealized_pnl DEFAULT 0, equity, timestamp` — `idx_balance_time(timestamp)`. `direction_snapshots` `id, symbol, prob_long, prob_short, direction, confidence, z_composite, agent_breakdown, diffusion, created_at` — `idx_dir_snap_symbol(symbol,id)`. `macro_data` `id, indicator, value, period, source, fetched_at` — `UNIQUE(indicator,period)`.

**No table has a mode column.** `positions`, `trades`, `account` and `balance_history` are one shared set for paper and live; `run.py` swaps only the *executor*, never the storage.

**Consumers.** `run.py:52,750,799`; `trading/{paper_engine:16,position_manager:16,live/executor:43}`; `agents/base_agent.py:15`; `data/price_feed.py:240`; `reset_paper_db.py:5`.

### 38. `database/models.py`

**Role.** Nine `@dataclass` row types, no ORM.

**Symbols.** `Candle` `:11`; `Position` `:25`; `Trade` `:47`; `Signal` `:61`; `NewsItem` `:73`; `AgentLog` `:88`; `Account` `:99`; `BalanceSnapshot` `:115`; `MacroData` `:124`. Each ends in `id: Optional[int] = None`; `Candle`/`Position`/`Trade` also carry `created_at`/`opened_at`/`executed_at`. `Account` has `max_drawdown: float = 0.0`, `peak_balance`, `sharpe_ratio`, `profit_factor`. **No `DirectionSnapshot`** — `insert_direction_snapshot` takes a raw `dict`.

**State / side effects.** None; pure data. Imported by `agents/*`, `trading/*`, `data/macro_fetcher.py:16`, `data/news_fetcher.py:15`, `database/repository.py:8`.

### 39. `database/repository.py`

**Role.** Every SQL statement the bot issues, in one class.

**Symbols.** `_valid_candle(c: Candle) -> bool` `:17`; `__init__(self, db: Database)` `:40`; `insert_candle(self, c: Candle)` `:45`; `insert_candles_batch(self, candles: List[Candle])` `:62`; `get_candles(self, symbol: str, timeframe: str, limit: int = 500) -> List[dict]` `:92`; `get_latest_candle(self, symbol: str, timeframe: str) -> Optional[dict]` `:100`; `insert_position(self, p: Position) -> int` `:110`; `update_position_pnl(self, position_id: int, unrealized_pnl: float)` `:122`; `update_position_sl_tp(self, position_id: int, stop_loss: Optional[float] = None, take_profit: Optional[float] = None)` `:129`; `close_position(self, position_id: int, close_price: float, realized_pnl: float, close_reason: str) -> bool` `:147`; `liquidate_position(self, position_id: int, liq_price: float, realized_pnl: float) -> bool` `:171`; `get_open_positions(self, symbol: str = None) -> List[dict]` `:183`; `get_all_positions(self, limit: int = 100) -> List[dict]` `:195`; `insert_trade(self, t: Trade) -> int` `:204`; `prune_agent_logs(self, keep: int = 5000)` `:215`; `get_trades(self, limit: int = 100) -> List[dict]` `:235`; `get_trades_by_position(self, position_id: int) -> List[dict]` `:242`; `insert_signal(self, s: Signal)` `:251`; `get_recent_signals(self, symbol: str, limit: int = 50) -> List[dict]` `:260`; `insert_news(self, n: NewsItem)` `:269`; `get_recent_news(self, limit: int = 50) -> List[dict]` `:280`; `news_exists(self, title: str, source: str) -> bool` `:287`; `insert_agent_log(self, log: AgentLog)` `:296`; `get_agent_logs(self, agent_name: str = None, limit: int = 100) -> List[dict]` `:305`; `get_account(self) -> Optional[dict]` `:320`; `init_account(self, initial_balance: float)` `:324`; `update_account(self, balance: float, total_pnl: float, total_trades: int, winning_trades: int, losing_trades: int, max_drawdown: float, peak_balance: float, sharpe_ratio: float = None, profit_factor: float = None)` `:337`; `update_account_stats(self, total_pnl: float, total_trades: int, winning_trades: int, losing_trades: int, profit_factor: float = None, max_drawdown: float = None, sharpe_ratio: float = None)` `:356`; `update_balance(self, new_balance: float)` `:395`; `apply_balance_delta(self, delta: float) -> float` `:403`; `bump_peak_balance(self, candidate: float) -> float` `:427`; `insert_balance_snapshot(self, snap: BalanceSnapshot)` `:443`; `get_balance_history(self, limit: int = 1000) -> List[dict]` `:451`; `insert_direction_snapshot(self, snap: dict)` `:460`; `get_latest_direction_snapshot(self, symbol: str) -> Optional[dict]` `:486`; `get_latest_direction_snapshots(self) -> List[dict]` `:495`; `prune_direction_snapshots(self, keep_per_symbol: int = 120)` `:505`; `get_daily_realized_pnl(self) -> float` `:526`; `upsert_macro(self, m: MacroData)` `:551`; `get_macro_latest(self, indicator: str) -> Optional[dict]` `:561`; `get_all_macro(self) -> List[dict]` `:568`; `get_trade_stats(self) -> dict` `:576`. All `async` except `_valid_candle` and `__init__`.

**State.** `db` only. No caching.

**Side effects.** One `commit()` per method. Candle writes are `ON CONFLICT … DO UPDATE` (UPSERT), never `INSERT OR IGNORE`. `close_position`/`liquidate_position` are single-claim — `WHERE id = ? AND status = 'OPEN'`, returning `cursor.rowcount > 0` — so SL/TP and scheduler batch closes cannot both claim the margin. `apply_balance_delta` does `balance = balance + ?` in one statement; `bump_peak_balance` uses `MAX(COALESCE(peak_balance,0), ?)`. `update_account_stats` omits `balance` and includes `profit_factor`/`sharpe_ratio`/`max_drawdown` only when not `None` — that is how `max_drawdown` and `sharpe_ratio` now get written, from `PositionManager.close_position` (`trading/position_manager.py:331`). `prune_direction_snapshots` uses `ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY id DESC)`. `get_daily_realized_pnl` filters `date(closed_at) = date('now')`, both UTC.

**Who writes `balance_history`.** Exactly one caller of `insert_balance_snapshot`: `PositionManager.take_balance_snapshot` (`trading/position_manager.py:577`), registered as APScheduler job `balance_snapshot` at `run.py:614-618` with `interval_seconds = agent_intervals.balance_snapshot` = **60 s**. **The dashboard only ever `SELECT`s it.** There is **no `DELETE FROM balance_history` anywhere in the repo** and `reset_paper_db.py` does not truncate it. In live mode the job still targets `self.paper_engine.position_manager` (`run.py:616`).

**Consumers.** `trading/{position_manager:17,paper_engine:17,live/executor:45}`; `agents/base_agent.py:16`; `data/price_feed.py:241`; tests.

### 40. `dashboard/app.py`

**Role.** Builds and serves the single `dash.Dash` app.

**Symbols.** Module side-effects `:13-15`: `logging.getLogger("werkzeug")`/`("flask")` at `ERROR`, `WSGIRequestHandler.log_request = lambda self, *args, **kwargs: None`. `create_dash_app() -> dash.Dash` `:25`; `handle_stale_callback_key_error(e)` nested on `@app.server.errorhandler(KeyError)` `:39`; `_silence_flask_banner()` `:100`; `run_dashboard()` `:128`.

**State.** None of its own; host/port/debug from `get_config().dashboard` (`127.0.0.1:8050`, `debug: false`). `dashboard.update_interval: 2000` (`config.yaml:123`) is **never read** — the real intervals are hardcoded.

**Side effects.** Flask `KeyError` handler returns `{"response": {}, "multi": True}, 200` for stale-callback messages. Two `dcc.Interval`s: **`dashboard-interval` = 500 ms**, **`dashboard-slow-interval` = 60000 ms**. `app.run(..., dev_tools_silence_routes_logging=True)`. `_silence_flask_banner` monkeypatches `click.echo` to a no-op unless `TRADEBOT_BANNER` is truthy.

**Consumers.** `run.py:63` imports `run_dashboard`.

### 41. `dashboard/callbacks/update_callbacks.py`

**Role.** Every Dash callback, opening its **own synchronous `sqlite3` connection** rather than using `Database`/`Repository`.

**Symbols.** `_neural_msg_prev: dict` `:49`; `_neural_last_fig = None` `:54`; `_set_neural_last_fig(fig)` `:57`; `_get_sync_db()` `:64`; `derive_macro_bias(macro_rows) -> str` `:72`; `register_callbacks(app)` `:114`. Callbacks, all nested: `update_legacy_header` `:128`; `update_legacy_performance` `:155`; `update_legacy_positions` `:241`; **`update_legacy_news` `:250`**; `update_legacy_logs` `:258`; `update_legacy_indicators` `:266`; `update_legacy_candlestick` `:274`; `update_legacy_symbol_options` `:303`; `update_top_bar` `:319`; `update_symbol_dropdown` `:391`; `update_mini_candlestick_and_tape` `:414`; `update_wallet_overview` `:629`; `update_positions_grid` `:714`; `update_probability_scanner` `:794`; `update_neural_net` `:913`; `update_equity_area` `:1002`; `update_trade_stream_and_marquee` `:1057`; `update_analytics_and_sparklines` `:1209`; `update_symbol_pnl` `:1347`.

**Stubs.** `update_legacy_news` is registered for `sentiment-summary` and `news-feed-container` and its whole body is `return html.Div(), html.Div()` (`:250-251`) — no connection, no query, nothing rendered. Three siblings are equally inert: `update_legacy_positions` `:242`, `update_legacy_logs` `:259`, `update_legacy_indicators` `:267`. Their targets sit inside a `display:none` compatibility block (`dashboard/app.py:71-91`).

**Direct SQLite queries** — 40 `conn.execute` calls, all via `sqlite3.connect(cfg.database_path)` with `row_factory = sqlite3.Row` and **no busy timeout**: `account` (full row, or `balance, initial_balance`, or `total_pnl, total_trades, winning_trades`) `:131,158,632,1022,1080,1240,1248,1266`; `COALESCE(SUM(margin),0)` / `COALESCE(SUM(unrealized_pnl),0)` / `COUNT(*)` / `SELECT *` over `positions WHERE status = 'OPEN'` `:134,136,138,161,166,635,717,1028`; `realized_pnl` for closed/liquidated positions `:171,334,638,1016,1233,1274`; `balance_history` `LIMIT 300` equity `:180`, `SELECT * … LIMIT 300` `:1012`, `LIMIT 15` `:1226`; 1m `candles` — `LIMIT 120` `:278`, `LIMIT 400` `:431`, `volume LIMIT 15` `:1255`, last close for `%BTC%`/`%ETH%`/`%SOL%` `:344,351,358`; latest `direction_snapshots` per symbol `:799`; latest `signals` per symbol `:917` and 5 newest `:1076`; `agent_logs` 60-s `COUNT(*) GROUP BY agent_name` `:923` and 15 newest `:1071`; `trades LEFT JOIN positions` on `position_id` selecting `p.realized_pnl AS pnl`, `LIMIT 20` `:1062`; `COALESCE(SUM(fee),0) FROM trades` `:1269`; `strftime('%s', closed_at) - strftime('%s', opened_at)` `:1298`; per-symbol `GROUP BY symbol HAVING COUNT(*)>0 ORDER BY pnl DESC` `:1363`; `SELECT DISTINCT symbol FROM positions WHERE status = 'OPEN'` `:1379`.

**Side effects.** **Read-only — no writes at all.** Global mutation of `_neural_msg_prev` and `_neural_last_fig`. Prices/books come from `market_store` in memory, never the network. `update_probability_scanner` recomputes nothing: it renders the newest `direction_snapshots` row so HUD and `DecisionAgent` read identical numbers (`:776-783`). `update_analytics_and_sparklines` prints `N/A`, not `0.00`, for `max_drawdown`/`sharpe_ratio`/slippage when data is missing (`:1287-1318`). `derive_macro_bias` has **no caller** anywhere outside docs/tests.

**Consumers.** `dashboard/app.py:20`.

### 42. `dashboard/layouts/palette.py`

**Role.** Parses `:root` out of `dashboard/assets/style.css` once at import and re-exports the tokens as hex (Plotly canvas cannot read CSS `var()`).

**Symbols.** `_CSS` path `:29`; `_FALLBACK: Dict[str, str]` `:33` = `bg_app #dfd5b8`, `bg_card #e5dbc0`, `bg_inset #ede4cc`, `fg #000000`, `fg_dim #3a352c`, `fg_muted #555042`, `line #000000`, `rule #ccc2a5`, `rule_soft #b5ab8d`, `pos #006a2b`, `neg #9e1b16`, `warn #8a5200`, `accent #005a8c`; `_VAR_RE` `:49`; `_load() -> Dict[str, str]` `:52`; `TOKENS` `:84`; `PAPER_BG` `:88`, `CARD_BG` `:89`, `INSET_BG` `:90`, `INK` `:91`, `INK_DIM` `:92`, `INK_MUTED` `:93`, `RULE` `:94`, `RULE_SOFT` `:95`, `GREEN` `:97`, `RED` `:98`, `AMBER` `:99`, `WARN` `:101` (alias of `AMBER`), `BLUE` `:102`; `alpha(hex_color: str, opacity: float) -> str` `:105`; `apply_dark_figure(fig, height: int = None)` `:120`.

**State.** Module-level `TOKENS`, brace-matched against the first `:root` block. Only keys already in `_FALLBACK` are overwritten, so a missing CSS file leaves the fallback palette intact.

**Side effects.** One `read_text` at import. `apply_dark_figure` pins `template="none"` (so Plotly's white default cannot re-inject itself), `paper_bgcolor`/`plot_bgcolor` = `PAPER_BG`, font `'Share Tech Mono', ui-monospace, monospace`. `alpha` expands 3-digit hex, returns `"rgba(r,g,b,a)"`.

**Consumers.** `dashboard/layouts/hud_figures.py:19`, `dashboard/layouts/performance.py:8`, `dashboard/layouts/price_chart.py:10`; `tests/test_dashboard_palette.py`, `verify_dashboard_palette.py`, `verify_dashboard_render.py`.

---

### 43. `dashboard/layouts/hud.py`

**Role.** The single-screen HUD layout: one 12-column grid of panels, assembled in the order a trader asks questions (state → what is happening now → is the pipeline working → is the analytics working). Every panel is a pure function of nothing — it returns markup with empty placeholders that callbacks fill in.

**Symbols.** All return `html.Div`; all take no arguments. `create_hud_layout` `:20`; `create_top_bar` `:62`; `create_ticker_tape` `:100`; `create_wallet_pnl_panel` `:112`; `create_mini_price_panel` `:158`; `create_positions_panel` `:204`; `create_scanner_panel` `:230`; `TREE_STAGES` `:280`; `create_symbol_pnl_panel` `:283`; `create_neural_net_panel` `:318`; `create_equity_growth_panel` `:347`; `create_trade_logs_panel` `:372`; `create_live_analytics_panel` `:391`; `create_footer_ticker` `:435`.

**Zone structure, in call order (`:33-59`).** Top bar and ticker tape, then one `hud-grid` holding Z1 the wallet/PnL account rail, Z2 the dominant price panel beside a `zone2-stack` of the positions and scanner panels, Z3 the symbol-PnL strip, and Z4 the four `zone3-cell` analytics panels (equity, trade log, KPIs, neural net), then the footer. Panel width comes from a `span-N` class, not from hand-tuned pixel sizes, so alignment is the grid's job (`:24-25`).

**Empty states are written into the layout, not faked by data.** `create_positions_panel` ships `NO OPEN POSITIONS` plus a sub-line explaining the scanner is still looking (`:216-223`); `create_trade_logs_panel` ships `NO EXECUTIONS YET` (`:384`). Three panels build their figure eagerly at layout time so the first paint is themed rather than a white Plotly frame: `create_scanner_panel` calls `create_convergence_fig()` (`:232`), `create_neural_net_panel` calls `create_neural_net_fig(signal_map={})` (`:320`), `create_equity_growth_panel` calls `create_equity_area_fig([])` (`:349`), and `create_live_analytics_panel` calls `create_mini_sparkline_fig` twice (`:393-394`).

**`TREE_STAGES` (`:280`) is a shared constant, not a layout detail.** The comment states the ordering is used by both the layout and the callback that drives the animation, so adding a stage is a one-place change (`:278-279`).

**`dcc.Graph` heights are CSS variables, deliberately oversized.** `:331-334` explains that the `dcc.Graph` is set slightly taller than `fig.layout.height` (260 px in `hud_figures`) so Plotly's slot labels are not clipped at the edge. The mode bar is disabled on every HUD graph (`displayModeBar: False`, `responsive: True`, `doubleClick: False`), and the sparklines additionally set `staticPlot: True` (`:409`, `:418`).

**State.** None. No module globals, no I/O, no database. It is imported once at `dashboard/app.py:19` and called once at `dashboard/app.py:68`.

**Side effects.** None. `import pandas as pd` at `:6` is unused.

**Consumers.** `dashboard/app.py:19` and `:68`. Also the layout tests (`tests/test_layout_contract.py:47`, `tests/test_layout_budget.py:147, 158`) and the calibration scripts under `data_store/`. No production module imports it.

---

### 44. `dashboard/layouts/hud_figures.py`

**Role.** Every HUD Plotly figure. The largest file in the dashboard at 972 lines, and the only one that reaches outside `dashboard/` — it imports `analysis.probability_engine` for the convergence fallback.

**Symbols.** `_hex_to_rgb` `:21`; `with_alpha` `:27`; `PARCHMENT_BG`…`BLUE_VINTAGE` `:51-58`; `GREEN_VINTAGE_WASH`…`INK_TRANSPARENT` `:64-67`; `create_mini_candlestick_fig` `:71`; `create_convergence_fig` `:205`; `NEURAL_SYSTEM_NODES` `:307`; `NEURAL_SYSTEM_EDGES` `:344`; `TOKEN_SLOTS` `:385`; `_TOKEN_UNIVERSE` `:415`; `_TOKEN_DISPLAY_PRIORITY` `:429`; `_fnv1a` `:437`; `_build_token_slot_registry` `:451`; `_TOKEN_SLOT_REGISTRY` `:493`; `AGENT_NODE_METRIC` `:502`; `FEED_NODE_CHANNEL` `:512`; `create_neural_net_fig` `:520`; `create_equity_area_fig` `:859`; `create_mini_sparkline_fig` `:931`.

**Colours are re-derived from `palette`, never hardcoded (`:42-58`).** The comment is explicit about why: Plotly accepts `var(--bg-app)` without error and then draws nothing, leaving a white panel disconnected from the theme (`:46-50`). `PARCHMENT_BG` and friends are aliases of `palette.PAPER_BG` etc., and the translucent variants are computed with `with_alpha` rather than written as `rgba(...)` literals — otherwise editing `GREEN_VINTAGE` would not change the fills that depend on it and the palette would fork (`:60-63`).

**Token slot assignment is decided once at import, and the reason is a visible bug (`:396-409`).** The previous version indexed `TOKEN_SLOTS[i]` by the *position in the active token list*, so when one token disappeared every token after it shifted down a slot — on screen nodes jumped every 500 ms with no apparent reason. Now `_build_token_slot_registry` (`:451`) resolves token → slot once, and rendering is a pure lookup. Hash collisions are resolved once, here, rather than per render: resolving them per frame makes a losing token jump to an empty slot only while the collision lasts, then jump back — two directions of movement, equally bad (`:410-414`).

**`_fnv1a` is kept only as a deterministic-hash primitive (`:437`).** The docstring notes `hash()` is salted by `PYTHONHASHSEED`, so token placement would change on every restart. The registry builder no longer uses it — it assigns by display priority, majors first, then remaining tokens alphabetically (`_TOKEN_UNIVERSE` ∖ `_TOKEN_DISPLAY_PRIORITY`), filling the left six slots before the right six (`:471-489`), so no token is dropped for being unrecognised (`:460-462`).

**`create_equity_area_fig` uses `tonexty` against a baseline trace, not `tozeroy` (`:874-881`).** `tozeroy` forces the y-axis to include zero; on a 10 000 USDT account a 10 USDT equity move is 0.1% of the axis, so the curve looks flat while the bot is in profit. Filling to a separate baseline trace lets the axis follow the data.

**Missing data renders as an explicit empty state (`:863`, `:935`).** With fewer than two snapshots the equity chart draws a flat line at `initial_balance` — labelled as a baseline, not as data. `create_mini_sparkline_fig` with fewer than two points returns a "NO DATA" annotation (`:946-951`) rather than a flat line that reads like a real signal.

**`create_convergence_fig` imports the engine function-locally (`:223`).** Before the first callback there is no diffusion payload, so it computes a neutral 50/50 curve at `prob_long=0.50` (`:224-229`). Legacy `winner_probs`/`loser_probs` keys are still accepted alongside `long_probs`/`short_probs` (`:214-217`, `:231-234`).

**State.** `_TOKEN_SLOT_REGISTRY` (`:493`) is the only module-level mutable object, built once at import. `NEURAL_NODE_LAYOUT`, `TOKEN_NODE_KEYS` and `TOKEN_ANCHORS` (`:497-499`) are compatibility aliases for earlier imports.

**Side effects.** None. No network, no database, no file writes; it renders only from arguments and `palette`.

**Consumers.** `dashboard/layouts/hud.py:9` (`create_mini_candlestick_fig`, `create_convergence_fig`, `create_neural_net_fig`, `create_equity_area_fig`, `create_mini_sparkline_fig`, `GREEN_VINTAGE`, `BLUE_VINTAGE`); `dashboard/callbacks/update_callbacks.py:27`; `tests/test_neural_net_layout.py`; `tests/test_dashboard_palette.py:140`; the `verify_*.py` root scripts; and the calibration scripts under `data_store/`.

---

### 45. `dashboard/layouts/performance.py`

**Role.** Equity-curve and drawdown tab: the stat cards, the two charts, and the drawdown arithmetic.

**Symbols.** `create_performance_layout()` `:11`; `create_performance_stats(account_summary)` `:33`; `create_equity_chart(balance_history)` `:70`; `create_drawdown_chart(balance_history)` `:117`.

**Drawdown is computed here, from the equity series (`:133-139`).** A running peak is tracked over the (reversed) history and each point is `(peak - eq) / peak * 100`, **negated** so the curve plots below zero. The value is a percentage of peak, not of initial balance, and a rising peak resets the baseline.

**Eight stat cards (`:50-59`).** Balance, Equity, Total PnL, Return, Total Trade, Win Rate, Profit Factor, Max Drawdown. Return is `(balance - initial) / initial`, guarded against a zero initial balance (`:48`). Win Rate picks its colour on a three-way threshold (>50 positive, <40 negative, else neutral) rather than a simple sign check (`:56`). Profit Factor renders `—` when falsy, so an absent ratio is not shown as a real `0.00` (`:57`).

**`create_performance_layout` has no caller.** `create_performance_stats`, `create_equity_chart` and `create_drawdown_chart` are all called from `update_callbacks.py` (`:227-229`, `:233`), so the three renderers are live; only the tab shell — like the other four in this group — is dead.

**A defect worth naming here too: three colours are the literal string `"palette.alpha(palette.X, n)"`, not a call.** At `:93`, `:109` and `:147` the equity fill, the legend background and the drawdown fill are all passed to Plotly as unparseable text, which it accepts silently and renders as nothing. Same bug as in `price_chart.py`; `hud_figures.with_alpha` is the correct form.

**State.** None.

**Side effects.** None. Reads only its arguments; palette access is import-time.

**Consumers.** `dashboard/callbacks/update_callbacks.py:39-41`. `import make_subplots` at `:7` is unused.

---

### 46. `dashboard/layouts/positions.py`

**Role.** Active-positions and trade-history tables for the positions tab.

**Symbols.** `create_positions_layout()` `:8`; `create_positions_table(positions)` `:27`; `create_trade_history_table(trades)` `:69`.

**Both tables are hand-built `html.Table`, not `dash_table.DataTable` (`:5`, `:63-66`).** `dash_table` is imported at `:5` and never used. The positions table has 12 columns including a derived ROE column: `roe = pnl / margin * 100`, guarded with `if margin > 0 else 0` (`:41`). Both PnL and ROE share one colour variable (`:43`), so a profitable row cannot show a red PnL. A null `stop_loss` or `take_profit` renders `—` rather than `$0.00` (`:56-57`) — zero is a real price and absence is not.

**One unused local.** `side_color` is computed at `:44` and at `:78` but never read; the positions table colours the side through a `side-badge long/short` class instead (`:50`).

**`create_trade_history_table` truncates to 50 (`:77`)**, the newest 50, with no indicator that more exist.

**State.** None.

**Side effects.** None.

**Consumers.** `dashboard/callbacks/update_callbacks.py:36`. `create_positions_layout` itself has no caller.

---

### 47. `dashboard/layouts/price_chart.py`

**Role.** Candlestick chart with volume and RSI subplots and EMA/Bollinger overlays.

**Symbols.** `create_price_chart_layout()` `:13`; `create_candlestick_figure(df, symbol="BTC/USDT", trades=None)` `:72`.

**Symbol options are built from `get_config().symbols` at call time (`:15-22`).** The short label is `s.split("/")[0] + "/USDT"`, so a configured `BTC/USDT:USDT` displays as `BTC/USDT` while the value keeps the full ccxt key. If the list is empty the dropdown falls back to a single hardcoded BTC option (`:20-21`) — a chart still has to render on a misconfigured config.

**Three stacked subplots, fixed row heights (`:81-87`).** `rows=3`, `shared_xaxes=True`, `vertical_spacing=0.03`, `row_heights=[0.6, 0.2, 0.2]`: price 60%, volume 20%, RSI 20%. Subplot titles are `[symbol, "Volume", "RSI"]`.

**Every overlay is conditional on the column being present (`:105`, `:115`, `:126`, `:177`).** EMA short/long, Bollinger upper/lower and RSI are each guarded by `if "<col>" in df.columns`, so the function degrades rather than raising when the DataFrame carries only raw OHLCV. Volume bars are coloured per-candle by close-vs-open (`:165`).

**A defect worth naming: four `rgba(...)` strings are built as the literal text `"palette.alpha(palette.X, n)"`.** At `:130`, `:139`, `:142` and `:198` the argument to `fillcolor`/`line.color`/`bgcolor` is the *string* `"palette.alpha(palette.X, n)"`, not a call to it. Plotly accepts an unparseable colour without raising and renders nothing, so both Bollinger bands, the BB fill and the legend background are silently absent. The same mistake appears three times in `performance.py` (`:93`, `:109`, `:147`). Compare `hud_figures.with_alpha`, which does the same job correctly.

**EMA period labels are hardcoded (`:110`, `:120`).** The legends read `EMA 9` and `EMA 21` as f-strings with no interpolation, so a config change to `ema_short`/`ema_long` leaves the chart lying about which periods it drew.

**State.** None.

**Side effects.** `get_config()` reads `config.yaml` via the cached singleton. No network, no database.

**Consumers.** `dashboard/callbacks/update_callbacks.py:42`; `core.config` at `dashboard/layouts/price_chart.py:9`. `tests/test_dashboard_palette.py:141`; `verify_dashboard_palette.py:71`. `create_price_chart_layout` itself has no caller.

---

### 48. `dashboard/layouts/news_feed.py`

**Role.** News tab: the sentiment summary card and the headline list.

**Symbols.** `create_news_feed_layout()` `:8`; `create_sentiment_summary(aggregate)` `:27`; `create_news_items(news_list)` `:79`.

**Sentiment is read as `sentiment_vader`, not the FinBERT score (`:86`).** The badge renders `f"{sentiment:+.2f}"` from `n.get("sentiment_vader") or 0` — a headline with no VADER score displays `+0.00` rather than a blank, which reads as a measured neutral.

**Two identical three-way branches (`:38-46`, `:89-94`).** `create_sentiment_summary` picks `badge_class` and an icon (`+`/`-`/`~`) from the label; `create_news_items` picks only `badge_class`. Impact is coloured from a dict lookup with a `"LOW"` default (`:97`).

**Both functions return a placeholder paragraph on empty input (`:30`, `:82`)**, so the tab has a defined look before the first cycle.

**State.** None.

**Side effects.** None. `import dcc` at `:5` is unused — this file renders no Dash component that needs it.

**Consumers.** `dashboard/callbacks/update_callbacks.py:38`. `create_news_feed_layout` itself has no caller; its ids `sentiment-summary` and `news-feed-container` are the targets of the inert `update_legacy_news` stub instead (`dashboard/callbacks/update_callbacks.py:250-251`).

---

### 49. `dashboard/layouts/agent_logs.py`

**Role.** Agent reasoning-log tab: a filter dropdown and the log stream.

**Symbols.** `create_agent_logs_layout()` `:8`; `create_log_entries(logs)` `:39`.

**Row colour is inferred by substring search over the text (`:52-58`).** The cascade is `"BULLISH" in reasoning.upper() or "LONG" in action.upper()` → bullish, then the bearish pair, then `"HOLD" in action.upper() or "NEUTRAL" in reasoning.upper()` → neutral, defaulting to `info`. Because it matches on `action`, any close whose action contains `LONG` renders green regardless of the PnL it reports.

**The filter dropdown is decorative (`:14-27`).** `id="agent-log-filter"` with six options, and `clearable=False`. Nothing in `dashboard/callbacks/update_callbacks.py` reads `agent-log-filter`; `update_legacy_logs` (`:258`), the callback for the sibling container, is one of the inert stubs returning `html.Div()`. So the dropdown sets a value no code consumes.

**`create_agent_logs_layout` has no caller**, and neither does anything render `agent-logs-container` in the live HUD.

**State.** None.

**Side effects.** None. `import dcc` at `:5` is used here (the dropdown).

**Consumers.** `create_log_entries` — `dashboard/callbacks/update_callbacks.py:37`. No other production consumer.

### 50. `ml/trainer.py`

**Role.** Offline `RandomForestClassifier` trainer. Not on any runtime path.

**Symbols.** `MODEL_DIR = Path("ml/models")` `:18`; `ModelTrainer.__init__(self, lookahead: int = 10, threshold_pct: float = 0.005)` `:33`; `prepare_features(self, df: pd.DataFrame) -> pd.DataFrame` `:42`; `create_labels(self, df: pd.DataFrame) -> pd.Series` `:80`; `train(self, df: pd.DataFrame) -> dict` `:96`; `train_from_exchange(symbol: str = "BTC/USDT:USDT", timeframe: str = "1h", limit: int = 1000)` `:171`.

**State.** `lookahead`, `threshold_pct`. Features `rsi, macd_hist_norm, bb_position, ema_trend, volume_ratio, sentiment_score, atr_pct` (`:112-115`); `n_estimators=100, max_depth=10, min_samples_split=10, min_samples_leaf=5, random_state=42, n_jobs=-1`; split `test_size=0.2, shuffle=False`; <100 valid samples → `{"accuracy": 0, "error": "Data terlalu sedikit"}`.

**Side effects.** `MODEL_DIR.mkdir(parents=True, exist_ok=True)` then `joblib.dump` to `ml/models/signal_model.pkl` — the exact path `analysis/ml_signals.py:54` loads. Network: ccxt `fetch_ohlcv` timeout 4.0 s, yfinance `BTC-USD` 1y/1h fallback. `__main__` runs `asyncio.run(train_from_exchange())`.

**Consumers.** **None.** Repo-wide grep for `from ml.` / `import ml.` returns only this module's own docstring.

### 51. `ml/predictor.py`

**Role.** 45-line wrapper over `analysis.ml_signals.MLSignalGenerator`.

**Symbols.** `Predictor.__init__(self)` `:20`; `async def initialize(self)` `:23`; `def predict(self, technical_signals: Dict, sentiment_score: float = 0.0, df=None) -> Dict` `:28`; `@property def model_available(self) -> bool` `:42`.

**State / side effects.** Holds `self.generator`; reads its private `_model_loaded`. No network, no DB, no computation of its own.

**Consumers.** **None in production.** `agents/analysis_agent.py:50` builds `MLSignalGenerator` directly and bypasses this wrapper.

### 52. `cpp/include/microstructure_kernel.h`

**Role.** Header-only C++20 declarations for `Book`, `BookStore`, `MicrostructureKernel` — the exact numeric twin of `core.microstructure.PythonKernel`.

**Symbols.** `inline constexpr std::uint32_t kMaxLevels = 32;` `:29`. `Book`: `Book() noexcept` `:36`; `void clear() noexcept` `:38`; `void ingest_bid(double px, double sz) noexcept` `:40`; `void ingest_ask(double px, double sz) noexcept` `:41`; `std::uint32_t n_bid() const noexcept` `:43`; `n_ask()` `:44`; `double bid_price/bid_size/ask_price/ask_size(std::uint32_t i) const noexcept` `:46-49`; `overflow_bid()/overflow_ask()` `:51-52`; private `std::array<double, kMaxLevels> bid_px_, bid_sz_, ask_px_, ask_sz_`. `BookStore`: `explicit BookStore(std::size_t max_symbols) noexcept` `:86`; `bool reserve_symbol(const char* symbol, std::size_t len) noexcept` `:93`; `const Book* find(const char* symbol) const noexcept` `:96`; `void set_book(const char* symbol, std::size_t len, const Book& book) noexcept` `:98`; `void reset(const char* symbol) noexcept` `:100`; `void reset_all() noexcept` `:101`; `std::size_t size() const noexcept` `:103`; `max_symbols()` `:104`; copy ctor/assign deleted; `mutable std::mutex mutex_`, `std::unordered_map<std::string, Book> books_`. `MicrostructureKernel`: `explicit MicrostructureKernel(std::size_t max_symbols = 64) noexcept` `:121`; `std::pair<double, double> order_flow_imbalance(const char* symbol, std::size_t len, std::uint32_t depth = 5) const noexcept` `:128`; `double depth_imbalance(const char* symbol, std::size_t len, std::uint32_t depth = 5) const noexcept` `:132`; `bool ingest_json(const char* payload, std::size_t len, char* symbol_out, std::size_t symbol_out_cap, char* json_error, std::size_t json_error_cap) noexcept` `:142`; `bool reserve_symbol(const char* symbol, std::size_t len) noexcept` `:146`; `void ingest_book(const char* symbol, std::size_t len, const Book& book) noexcept` `:151`; `void reset(const char* symbol) noexcept` `:154`; `std::size_t symbol_count() const noexcept` `:156`; `static std::pair<double, double> book_ofi(const Book& book, std::uint32_t depth) noexcept` `:160`; `static double book_depth(const Book& book, std::uint32_t depth) noexcept` `:162`. `double level_weight(std::uint32_t index) noexcept` `:170`.

**State.** Fixed-size arrays only — no `std::vector`, no `std::string` on the compute path. The sole allocation is `unordered_map` growth for a **new** symbol.

**Side effects.** None. `find()`-then-write is documented as GIL-protected from Python; the mutex only guards map integrity. `ingest_book` silently no-ops for an unregistered symbol.

### 53. `cpp/microstructure_kernel.cpp`

**Role.** Implementation of the above plus the `l2Book` payload parser.

**Symbols.** `constexpr double kLevelWeight0 = 1.0;` `:57`; `kLevelWeightStep = 0.1;` `:58`; `double level_weight(std::uint32_t index) noexcept` `:62`; `Book::Book/clear/ingest_bid/ingest_ask` `:70/80/87/101`; `BookStore` members `:116-178`; `weighted_volume(const double* sizes, std::uint32_t n) noexcept` `:192`; `MicrostructureKernel` members `:202-329`; anon-namespace `json::Status parse_px_sz_object(json::Scanner& sc, double& px, double& sz) noexcept` `:352`; `json::Status parse_side(json::Scanner& sc, Book& book, bool is_bid) noexcept` `:417`; `void set_error(char* out, std::size_t cap, const char* msg) noexcept` `:451`; `static json::Status parse_data_block(json::Scanner& sc, char* coin, std::size_t coin_cap, bool& have_coin, Book& book, bool& have_levels) noexcept` `:460`; `MicrostructureKernel::ingest_json(...)` `:528`.

**State.** `store_` in the kernel, `mutable std::mutex` in `BookStore`.

**Side effects.** None. Parity-critical: weights are `size * (1.0 - 0.1*i)` summed level 0→n-1 **in that order** (float addition is not associative, so order is contractual); `book_ofi` returns `(0.0, 0.0)` when either side is empty, while `book_depth` deliberately does **not** replicate that check — it matches Python `depth_imbalance` exactly. `ingest_json` returns `false` with an **empty** `json_error` when the payload is not an `l2Book` channel, versus a filled one when it is malformed; the caller must distinguish the two, and ignoring the return value would leave a stale book feeding bogus OFI. `coin` buffer is 64 bytes; `symbol_out`/`json_error` are 64/128 bytes at the binding.

### 54. `cpp/bindings.cpp`

**Role.** pybind11 module `cpp_microstructure`.

**Symbols.**

| Exported | Binding / signature | Loc |
| --- | --- | --- |
| `book_from_python` | `void book_from_python(const py::object& bids, const py::object& asks, tradebot::Book& book) noexcept` (anon-namespace helper) | `:32` |
| `MicrostructureKernel` | `py::class_<tradebot::MicrostructureKernel>(m, "MicrostructureKernel")`, `py::init<std::size_t>(), py::arg("max_symbols") = 64` | `:53-57` |
| `.order_flow_imbalance` | `py::arg("symbol"), py::arg("depth") = 5` → `(ofi, relative_spread)` | `:59-68` |
| `.depth_imbalance` | `py::arg("symbol"), py::arg("depth") = 5` | `:70-78` |
| `.ingest_l2` | `py::arg("symbol"), py::arg("bids"), py::arg("asks")` | `:80-94` |
| `.ingest_json` | `py::arg("payload")`; returns `py::str` or `py::none()` | `:96-134` |
| `.reset` | `py::arg("symbol") = py::none()`; `None` clears all | `:136-150` |
| `.symbol_count` | no args | `:152-158` |
| `level_weight` | `m.def("level_weight", &tradebot::level_weight, py::arg("index"), …)` | `:160` |
| `MAX_LEVELS` | `m.attr("MAX_LEVELS") = static_cast<int>(tradebot::kMaxLevels);` | `:162` |
| `__version__` | `m.attr("__version__") = "1.0.0";` | `:163` |

**Array shape / dtype / contiguity contract.** **There is none — no numpy is involved anywhere in the binding.** No buffer protocol, no `py::array_t`, no dtype, no contiguity requirement, because no array is ever accepted. `ingest_l2` takes two plain Python iterables of `(px, sz)` pairs: `for (const auto& item : bids) { const auto pair = item.cast<std::pair<double, double>>(); book.ingest_bid(pair.first, pair.second); }`. Book capacity is 32 levels per side; levels beyond that are dropped and flagged via `overflow_bid`/`overflow_ask`, and only 20 ever arrive from Hyperliquid. `ingest_json` takes `py::bytes` and is zero-copy (`Py_ssize_t` into the C++ `const char*`), returning `py::str` — not `std::string` — because the lambda's return type is already `py::object`.

**`book_from_python` is `noexcept` but can throw.** It is marked `noexcept` while iterating a Python list and calling `item.cast<std::pair<double,double>>()` per element. Both the iteration protocol and `cast` allocate and can raise `py::error_already_set` / `py::cast_error`. A `noexcept` function that throws calls `std::terminate`, so a malformed element (a dict, a string, a 3-tuple) **aborts the interpreter** rather than raising a catchable Python exception.

**GIL discipline.** `order_flow_imbalance`, `depth_imbalance`, `ingest_json` and `reset` take `py::gil_scoped_release` around the C++ call; `reset` first copies the symbol into a local `std::string` so the `char*` outlives the release. `ingest_l2` deliberately **keeps** the GIL, because `book_from_python` touches Python objects.

**Side effects.** Kernel state only. `ingest_l2` throws `std::runtime_error` when `reserve_symbol` fails (store full); `ingest_json` throws `std::runtime_error(error_out)` for a malformed l2Book and returns `None` for any other channel.

**Consumers.** `core/microstructure.py:277`; `CppMicrostructureKernel` (`:260-306`) adapts it and `initialize_native_kernel(force: bool = False) -> bool` (`:309`) registers it honouring `TRADEBOT_KERNEL=python|cpp`. Invoked once per feed from `HyperliquidFeed._ensure_native_kernel`.

### 55. `cpp/include/simdjson.h`

**Role.** Hand-written zero-copy JSON scanner `tradebot::json`. **Not** upstream simdjson despite the filename.

**Symbols.** `enum class Status : int32_t { Ok, UnexpectedEnd, UnexpectedChar, NumberMalformed, TooManyLevels, MissingField }` `:50`; `inline const char* status_text(Status s)` `:59`; `struct Span { std::uint32_t off; std::uint32_t len; }` `:75`; `class Scanner` `:84`: `Scanner(const char* data, std::size_t len) noexcept` `:86`, `void reset() noexcept` `:89`, `bool eof() const noexcept` `:91`, `std::size_t position() const noexcept` `:92`, `const char* data() const noexcept` `:93`, `std::size_t size() const noexcept` `:94`, `Status skip_ws() noexcept` `:100`, `Status expect(char c) noexcept` `:112`, `Status peek(char& out) noexcept` `:120`, `Status skip_string() noexcept` `:128`, `Status scan_string_span(Span& out) noexcept` `:148`, `Status find_key(const char* key, std::size_t key_len, bool& found) noexcept` `:169`, `Status scan_number_double(double& out) noexcept` `:217`, `Status skip_value() noexcept` `:274`, private `skip_literal/skip_array/skip_object`; members `data_`, `len_`, `pos_`.

**State.** A cursor over a buffer the caller owns. Never allocates, never copies.

**Side effects.** None. `scan_string_span` **rejects any string containing a backslash** (`UnexpectedChar`) — safe for `coin`/`levels`/`px`/`sz`, not a general JSON reader. `scan_number_double` accepts quoted and unquoted numbers, copies ≤63 bytes into a stack buffer for `strtod`, returns `NumberMalformed` beyond that. `TooManyLevels` is declared but never returned. Out-of-subset input is rejected, never tolerated.

**Consumers.** `cpp/microstructure_kernel.cpp:33`. Under `HL_JSON_USE_SIMDJSON=1` the upstream singleheader goes on the include path and this header becomes a shim.

### 56. `CMakeLists.txt`

**Role.** Builds the `cpp_microstructure` extension.

**Symbols.** `cmake_minimum_required(VERSION 3.18)` `:21`; `project(cpp_microstructure LANGUAGES CXX)` `:22`; `CMAKE_CXX_STANDARD 20` / `..._REQUIRED ON` / `..._EXTENSIONS OFF` `:24-26`; `CMAKE_POSITION_INDEPENDENT_CODE ON` `:27`; default `CMAKE_BUILD_TYPE Release` `:29-31`; `find_package(Python3 COMPONENTS Interpreter Development.Module REQUIRED)` `:36`; pybind11 located by asking Python (`${Python3_EXECUTABLE} -m pybind11 --cmakedir`, `:40-46`) with `find_package(pybind11 CONFIG REQUIRED)` fallback `:55`; `pybind11_add_module(cpp_microstructure cpp/microstructure_kernel.cpp cpp/bindings.cpp)` `:67-70`; `target_include_directories` adding both `cpp` and `cpp/include` `:74-77`; `option(HL_JSON_USE_SIMDJSON … OFF)` `:86`; `set(SIMDJSON_ROOT "" CACHE PATH …)` `:87`; MSVC `/O2 /W4` `:107`, else `-O2 -Wall -Wextra -fvisibility=hidden` `:109-114`; MinGW static link `-static-libgcc -static-libstdc++ -Wl,-Bstatic -lwinpthread -Wl,-Bdynamic` `:138-144`; `LIBRARY_OUTPUT_DIRECTORY` / `RUNTIME_OUTPUT_DIRECTORY` = `${CMAKE_CURRENT_SOURCE_DIR}` `:149-152` so `import cpp_microstructure` works without `PYTHONPATH`.

**Side effects.** Writes `cpp_microstructure.{pyd,so}` into the project root. **No `simdjson.h` in the source list** — header-only, reached via the include path. `-Ofast`/`-ffast-math` is deliberately rejected (`:14-18`) because it permits FP reordering and would break the ≤1e-7 parity contract with `PythonKernel`. The target must be created *before* `target_include_directories` names it (`:61-65`) — CMake resolves those at configure time, so a forward reference is a hard error, not a warning.

**Consumers.** Operator build step; `core/microstructure.py:342-344` prints these exact two `cmake` commands in its error message.

### 57. `analysis/backtester.py`

**Role.** Event-driven L2 backtester with a real queue-position fill model and book-derived market impact. No predictions, no strategy decisions.

**Symbols.** `L2Snapshot` `:48` (`timestamp, symbol, bids, asks`; `.best_bid() :61`, `.best_ask() :64`, `.mid() :67`, `.bid_depth(n) :73`, `.ask_depth(n) :76`); `TapeTrade` `:81`; `PendingOrder` `:90` (`.is_filled :110`, `.is_expired :114`); `Fill` `:119`; `BacktestStats` `:133` (15 fields incl. `profit_factor`, `sharpe`, `sortino`, `max_drawdown`, `avg_slippage_bps`, `unfilled_orders`); `_returns(equity: List[float]) -> List[float]` `:159`; `sharpe_ratio(returns: Sequence[float], risk_free: float = 0.0) -> Optional[float]` `:170`; `sortino_ratio(returns: Sequence[float], risk_free: float = 0.0) -> Optional[float]` `:189`; `max_drawdown(equity: List[float]) -> Tuple[Optional[float], Optional[float]]` `:212`; `QueueFillModel` `:239` — `__init__(self, initial_queue_ahead: float = 0.0)` `:272`, `on_trade(self, trade_size: float) -> float` `:277`, `@property has_priority(self) -> bool` `:301`; `load_l2_events(sqlite_path: str) -> Iterator[Tuple[str, float, object]]` `:311`; `synthesize_walk(base_price: float = 100.0, steps: int = 600, step_seconds: float = 1.0, spread_bps: float = 2.0, depth_levels: int = 5, base_size: float = 10.0, trade_rate: float = 4.0, seed: int = 7) -> Tuple[List[L2Snapshot], List[TapeTrade]]` `:352`; `CompletedTrade` `:436`; `L2Backtester` `:453` — `__init__(self, initial_balance: float = 10000.0, taker_fee: float = 0.0005, max_book_levels: int = 5, impact_coefficient: float = 0.5)` `:469`, `_worst_fill_price(self, symbol: str, side: str, quantity: float) -> Optional[Tuple[float, float]]` `:497`, `_estimate_impact_bps(self, symbol: str, side: str, quantity: float) -> Optional[float]` `:542`, `_queue_ahead_at_price(self, symbol: str, side: str, price: float) -> float` `:573`, `submit_limit(self, symbol: str, side: str, price: float, quantity: float, reason: str = "SIGNAL") -> Optional[PendingOrder]` `:601`, `on_snapshot(self, snap: L2Snapshot) -> None` `:639`, `on_trade_event(self, trade: TapeTrade) -> None` `:645`, `open_position(self, symbol: str, side: str, quantity: float, reason: str = "SIGNAL", use_market_impact: bool = True) -> Optional[CompletedTrade]` `:696`, `close_position(self, reason: str = "SIGNAL", use_market_impact: bool = True)` `:763`, `_mark_equity(self, timestamp: float) -> None` `:832`, `replay(self, snapshots: List[L2Snapshot], trades: List[TapeTrade], strategy=None) -> "L2Backtester"` `:856`, `stats(self) -> BacktestStats` `:901`, `report(self) -> str` `:950`.

**`QueueFillModel`.** `on_trade` accumulates `total_traded_through`; while `queue_ahead > 0` it consumes `min(queue_ahead, trade_size)` against the queue and returns `0.0` if nothing is left over, else the leftover; once the queue is empty the whole size fills. `has_priority` is `queue_ahead <= 1e-12`. The declared open assumption is FIFO within a level (real exchanges use price-time priority), and it **never** assumes our order is at the front. Conservative fill: if our price level is absent from the book the order is *not* filled — inventing liquidity is refused. `on_trade_event` rebuilds a `QueueFillModel(order.queue_ahead)` per matching trade and writes the decremented value back, so queue state survives across events. **Deliberate asymmetry:** `_worst_fill_price` reads `asks` for a BUY while `_queue_ahead_at_price` reads `bids` — a market order lifts offers, a resting maker order joins the bid queue. The `⚠️` block at `:580-590` exists so nobody "fixes" it into an impact bug.

**State.** `initial_balance`, `cash`, `taker_fee`, `max_book_levels`, `impact_coefficient`, `book: Dict[str, L2Snapshot]`, `pending`, `fills`, `completed`, `open_trade`, `equity_curve` seeded `[(0.0, cash)]`, `current_ts`.

**Side effects.** Read-only `sqlite3.connect` in `load_l2_events` — one `SELECT … UNION ALL … ORDER BY ts ASC` over `l2_snapshots(ts, symbol, bids, asks)` and `l2_trades(ts, symbol, price, size, side)`. `synthesize_walk` uses a locally seeded `random.Random(seed)`. `get_config` is imported at `:36` and **never used**. `taker_fee` defaults to `0.0005`, not matching `config.yaml`'s Hyperliquid base-tier taker `0.00045`. Uncomputable metrics return `None`, never `0.0` (`stats()` sets `profit_factor = None` rather than `inf` when there are no losses). `replay` sorts once on `(timestamp, kind)` with snapshots first, so the book exists before any trade consumes queue volume.

**Does any production code call into the backtester? No.** A repo-wide grep for `backtester|L2Backtester|QueueFillModel|sharpe_ratio|sortino_ratio` outside `analysis/backtester.py` returns only `tests/test_advanced_modules.py` (371, 434, 447, 654, 667, 692, 714, 728, 739, 758); the `sharpe_ratio` **column** in `database/db.py:113` and `database/models.py:108`; the `sharpe_ratio=` **argument** in `database/repository.py:341-384`; and a stale `.snapshot/` copy of `trading/position_manager.py`. Nothing in `run.py`, `trading/`, `agents/`, `dashboard/` or `data/` imports it. `trading/live/executor.py` computes live Sharpe through `RiskManager.calculate_sharpe_ratio` over `balance_history` — a separate implementation. The module is test-only and research-only.

---

## Entry point

`run.py` is the composition root — it owns no logic of its own and is deliberately outside all nine groups, so it is numbered last and excluded from both tables below.

### 58. `run.py`

**Role.** Wires the nine subsystems together, chooses paper vs live, and owns the process lifecycle: construct, initialise, run, shut down. 995 lines, and the only module that knows about all of them.

**Symbols.**

| Symbol | Kind | Signature | Loc |
|---|---|---|---|
| `_silence_request_log` | function | `def _silence_request_log(self, *args, **kwargs) -> None:` | `run.py:27` |
| `logger` | module global | `logger = get_logger("main")` | `run.py:65` |
| `_EXEC_TICK_SECONDS` | constant | `0.3` | `run.py:81` |
| `_EXEC_FAIL_TRACE_FIRST` | constant | `3` | `run.py:86` |
| `_EXEC_FAIL_LOG_EVERY` | constant | `50` | `run.py:90` |
| `_EXEC_FAIL_CRITICAL` | constant | `100` | `run.py:94` |
| `_print_safe` | function | `def _print_safe(text: str):` | `run.py:97` |
| `print_banner` | function | `def print_banner():` | `run.py:124` |
| `print_startup_summary` | function | `def print_startup_summary(config, symbols, mode="paper", address=None):` | `run.py:147` |
| `_choose_mode` | function | `def _choose_mode():` | `run.py:186` |
| `TradingBotApp` | class | `class TradingBotApp:` | `run.py:236` |
| `TradingBotApp.__init__` | method | `def __init__(self, decision=None):` | `run.py:239` |
| `TradingBotApp._get_repo` | coroutine | `async def _get_repo(self) -> Repository:` | `run.py:261` |
| `TradingBotApp._build_live_executor` | coroutine | `async def _build_live_executor(self):` | `run.py:267` |
| `TradingBotApp.initialize` | coroutine | `async def initialize(self):` | `run.py:330` |
| `TradingBotApp._prefetch_historical_candles` | coroutine | `async def _prefetch_historical_candles(self):` | `run.py:440` |
| `TradingBotApp._prune_direction_snapshots` | coroutine | `async def _prune_direction_snapshots(self):` | `run.py:464` |
| `TradingBotApp._prune_agent_logs` | coroutine | `async def _prune_agent_logs(self):` | `run.py:488` |
| `TradingBotApp._maintenance_loop` | coroutine | `async def _maintenance_loop(self):` | `run.py:504` |
| `TradingBotApp.setup_scheduler` | method | `def setup_scheduler(self):` | `run.py:520` |
| `TradingBotApp._execution_loop` | coroutine | `async def _execution_loop(self):` | `run.py:620` |
| `TradingBotApp._candle_refresh_loop` | coroutine | `async def _candle_refresh_loop(self):` | `run.py:717` |
| `TradingBotApp._telemetry_loop` | coroutine | `async def _telemetry_loop(self):` | `run.py:745` |
| `TradingBotApp.repair_candles` | coroutine | `async def repair_candles(self, window_minutes: int = 600):` | `run.py:786` |
| `TradingBotApp.start_dashboard` | method | `def start_dashboard(self):` | `run.py:846` |
| `TradingBotApp.run` | coroutine | `async def run(self):` | `run.py:857` |
| `TradingBotApp.shutdown` | coroutine | `async def shutdown(self):` | `run.py:911` |
| `main` | coroutine | `async def main():` | `run.py:934` |

**Import-time side effects, before anything else runs (`:24-39`).** `werkzeug` and `flask` loggers are pinned to `ERROR` and `WSGIRequestHandler.log_request` is replaced with a no-op, because the dashboard polls every 500 ms and would otherwise flood the terminal. This happens at module import, before `setup_logger()` runs, so it cannot be reordered into `main()` without changing behaviour.

**Mode selection happens before any subsystem starts (`:939-942`).** `main` calls `_choose_mode()` and only then constructs `TradingBotApp`; the decision determines which engine is built, so it cannot be deferred. `_choose_mode` (`:186`) returns a `ModeDecision` and never raises: `--paper`/`--non-interactive` force simulation (`:202`), `--testnet`/`--live` set the mode directly with a printed warning for `--live` (`:209-222`), and everything else falls through to the interactive menu. Both `trading.live.console` imports are function-local (`:198`, `:225`) and `ImportError` is caught to fall back to `paper` (`:231-233`).

**`_build_live_executor` fixes an order that is safety-critical (`:271-281`).** `SafetyGate` is constructed **before** the exchange connection, so a gate that is only ready after connecting can be bypassed by an early order; the health check runs **before** the loop starts, and a failed health check raises rather than starting the loop, because a bot running against divergent local and exchange positions will guess — and guessing a position means doubling the exposure.

**`_execution_loop` may not die and may not fail silently (`:620-639`).** In live mode this loop is the only thing sending orders and watching SL/TP. The four `_EXEC_*` constants encode the previous failure mode: the old handler wrote `logger.error(f"...: {e}")` with no traceback, so a totally broken live interface produced thousands of identical healthy-looking lines per minute. The loop counts *consecutive* failures (`:647`), resets on any success, logs a full traceback for the first three, one compact line every 50 thereafter, and escalates once to `CRITICAL` at 100 (~30 s with no successful cycle). `asyncio.CancelledError` is re-raised explicitly (`:655-668`) — it descends from `BaseException` on Python 3.8+, so `except Exception` would not catch it, and a refactor to `except BaseException` would swallow shutdown as a failure and keep the loop spinning instead of stopping.

**The dashboard runs on its own thread (`:846-855`).** `threading.Thread(target=run_dashboard, daemon=True)`. Everything else — price feed, execution, candle refresh, telemetry, maintenance — is an `asyncio.Task` on the single event loop (`:879-895`); only Dash is off-loop, because its WSGI server is blocking.

**`_maintenance_loop` closes a first-hour gap (`:887-892`).** The hourly APScheduler prune jobs do nothing during the first hour of boot, which is exactly when the tables grow fastest. The loop is idempotent, so the two callers do not collide.

**Signal handling is best-effort on Windows (`:947-952`).** `loop.add_signal_handler` raises `NotImplementedError` on Windows and is caught, so `Ctrl+C` falls through to the `KeyboardInterrupt` handler instead.

**State.** `TradingBotApp` holds `config`, `decision`, `mode`, `event_bus`, `scheduler`, `price_feed`, `sentiment_analyzer`, `macro_fetcher`, `paper_engine`, the five agent slots (all `None` until `initialize`), `_running`, `_background_tasks`, and a lazily-built `_repo` (`:240-259`).

**Side effects.** Owns the process: `init_db`/`close_db` (`get_db`), `PriceFeed` construction and its websocket, the scheduler, all five background tasks, the Dash thread, and `setup_logger()` — called twice, at `:937` and `:969`. `shutdown` (`:911`) sets `_running = False`, shuts the scheduler down, stops the feed, cancels every tracked task, then closes the feed and the database. `repair_candles` (`:786`) is reachable only via the `--repair-candles` argument, which returns before the menu and never transacts (`:967`).

**Consumers.** Nothing imports it; it is the root executable. It is, however, a **consumer** of every group — `core.config` `:41`, `core.logger` `:42-49`, `core.event_bus` `:50`, `core.scheduler` `:51`, `database.db` `:52`, `database.repository` `:53`, all four data modules, `trading.paper_engine` `:57`, the four agents `:58-62`, and `dashboard.app` `:63`. Because it imports the dashboard at module level (`:63`), `run.py` cannot be imported on a machine without Dash installed.

---

## Package markers

### 59. The ten `__init__.py` files

**Role.** Nothing but a docstring — one line each, no imports, no re-exports, no `__all__`.

| File | Content | Group |
|---|---|---|
| `core/__init__.py` | `"""Core package — konfigurasi, event bus, scheduler, logger."""` | Core infrastructure |
| `agents/__init__.py` | `"""Agents package — agen otonom: berita, analisis, keputusan, eksekusi."""` | Decision and execution agents |
| `analysis/__init__.py` | `"""Analysis package — indikator teknikal, fundamental, sinyal ML."""` | Signal layer |
| `trading/__init__.py` | `"""Trading package — mesin paper trading, posisi, risiko."""` | Fill cost, position management and risk |
| `data/__init__.py` | `"""Data package — pengambilan harga, berita, makro, sentimen."""` | Data, persistence, UI and native code |
| `database/__init__.py` | `"""Database package — koneksi, skema, repository."""` | Data, persistence, UI and native code |
| `dashboard/__init__.py` | `"""Dashboard package — antarmuka visual Dash/Plotly."""` | Data, persistence, UI and native code |
| `dashboard/callbacks/__init__.py` | `"""Dashboard callbacks package."""` | Data, persistence, UI and native code |
| `dashboard/layouts/__init__.py` | `"""Dashboard layouts package."""` | Data, persistence, UI and native code |
| `ml/__init__.py` | `"""ML package — pelatihan dan prediksi model."""` | Data, persistence, UI and native code |

**None of them re-exports anything**, so every import in the repo is fully qualified — `from core.config import get_config`, never `from core import get_config`. That is what makes the import table below well-defined: the first-party edge is always visible in the statement text.

**`trading/live/` deliberately has no `__init__.py`.** It is a PEP 420 namespace package, which is why `trading/live/console.py` imports its sibling as `from trading.live import tui` (a relative import would also work, but the absolute form is what the file uses throughout).

**State / Side effects / Consumers.** None / none / no production module imports any of them as a module object — they execute only as a side effect of importing a sibling.

## Import table

Every first-party import edge that crosses a group boundary, in group order. 127 edges. `Where` is the import statement itself; `Why` is the reason this module, rather than reaching around it.

| Importer | Imports | Where | Why |
|---|---|---|---|
| `core/logger.py` | `core.config` (`get_config`) | `core/logger.py:11` | process-wide `AppConfig` singleton; live trading is forced off here and can only be enabled via env vars |
| `core/scheduler.py` | `core.config` (`get_config`) | `core/scheduler.py:13` | process-wide `AppConfig` singleton; live trading is forced off here and can only be enabled via env vars |
| `agents/analysis_agent.py` | `agents.base_agent` (`BaseAgent`) | `agents/analysis_agent.py:12` | the sense/think/act lifecycle every agent is driven through |
| `agents/analysis_agent.py` | `core.event_bus` | `agents/analysis_agent.py:13` | async fan-out point between agents and engines; the bus is injected, never constructed here |
| `agents/analysis_agent.py` | `data.price_feed` (`PriceFeed`) | `agents/analysis_agent.py:14` | the only OHLCV/ticker source injected into the analysis agent |
| `agents/analysis_agent.py` | `data.macro_fetcher` (`MacroFetcher`) | `agents/analysis_agent.py:15` | FRED series + economic calendar, re-seeding the fundamental analyzer |
| `agents/analysis_agent.py` | `data.sentiment` (`SentimentAnalyzer`) | `agents/analysis_agent.py:16` | VADER per headline, FinBERT on a batch; the loaded model is shared with the analysis agent |
| `agents/analysis_agent.py` | `analysis.technical` (`TechnicalAnalyzer`) | `agents/analysis_agent.py:17` | indicator stamping and the seven-group score |
| `agents/analysis_agent.py` | `analysis.fundamental` (`FundamentalAnalyzer`) | `agents/analysis_agent.py:18` | macro + sentiment + calendar scorecard |
| `agents/analysis_agent.py` | `analysis.ml_signals` (`MLSignalGenerator`) | `agents/analysis_agent.py:19` | 7-feature vector into the pickled RandomForest, with a rule-based fallback |
| `agents/analysis_agent.py` | `database.models` (`Signal`) | `agents/analysis_agent.py:20` | row dataclass inserted per cycle |
| `agents/analysis_agent.py` | `core.config` (`get_config`) | `agents/analysis_agent.py:21` | process-wide `AppConfig` singleton; live trading is forced off here and can only be enabled via env vars |
| `agents/analysis_agent.py` | `core.logger` (`get_logger`) | `agents/analysis_agent.py:22` | the one `trading_bot` logger tree every module writes through |
| `agents/direction_agents.py` | `agents.base_agent` (`BaseAgent`) | `agents/direction_agents.py:28` | the sense/think/act lifecycle every agent is driven through |
| `agents/direction_agents.py` | `analysis.direction_ensemble` | `agents/direction_agents.py:29` | pure verdict arbiter — no I/O, so the whole ensemble math is testable in isolation |
| `agents/direction_agents.py` | `analysis.technical` (`TechnicalAnalyzer`) | `agents/direction_agents.py:37` | indicator stamping and the seven-group score |
| `agents/direction_agents.py` | `core.config` (`get_config`) | `agents/direction_agents.py:38` | process-wide `AppConfig` singleton; live trading is forced off here and can only be enabled via env vars |
| `agents/direction_agents.py` | `core.market_store` | `agents/direction_agents.py:39` | in-memory price/book/funding mirror; avoids a DB read on every hot path |
| `agents/direction_agents.py` | `analysis.probability_engine` (`calculate_order_flow_imbalance`) | `agents/direction_agents.py:115` | agent-facing façade over the swappable microstructure kernel |
| `agents/direction_agents.py` | `analysis.probability_engine` (`calculate_technical_zscore`) | `agents/direction_agents.py:216` | collapses the indicator set into one signed z |
| `agents/direction_agents.py` | `analysis.probability_engine` | `agents/direction_agents.py:483` | the process-wide engine singleton; drives the diffusion payload |
| `agents/news_agent.py` | `agents.base_agent` (`BaseAgent`) | `agents/news_agent.py:11` | the sense/think/act lifecycle every agent is driven through |
| `agents/news_agent.py` | `core.event_bus` | `agents/news_agent.py:12` | async fan-out point between agents and engines; the bus is injected, never constructed here |
| `agents/news_agent.py` | `data.news_fetcher` (`NewsFetcher`) | `agents/news_agent.py:13` | RSS + CryptoPanic collection with in-process dedup |
| `agents/news_agent.py` | `data.sentiment` (`SentimentAnalyzer`) | `agents/news_agent.py:14` | VADER per headline, FinBERT on a batch; the loaded model is shared with the analysis agent |
| `agents/news_agent.py` | `database.models` | `agents/news_agent.py:15` | row dataclass inserted per headline |
| `agents/news_agent.py` | `core.logger` (`get_logger`) | `agents/news_agent.py:16` | the one `trading_bot` logger tree every module writes through |
| `agents/base_agent.py` | `core.event_bus` | `agents/base_agent.py:13` | async fan-out point between agents and engines; the bus is injected, never constructed here |
| `agents/base_agent.py` | `core.logger` (`get_logger`) | `agents/base_agent.py:14` | the one `trading_bot` logger tree every module writes through |
| `agents/base_agent.py` | `database.db` (`get_db`) | `agents/base_agent.py:15` | the process-wide aiosqlite connection |
| `agents/base_agent.py` | `database.repository` (`Repository`) | `agents/base_agent.py:16` | every SQL statement the bot issues, in one class |
| `agents/base_agent.py` | `database.models` (`AgentLog`) | `agents/base_agent.py:17` | row dataclass for the cycle/error audit trail |
| `agents/decision_agent.py` | `core.event_bus` | `agents/decision_agent.py:14` | async fan-out point between agents and engines; the bus is injected, never constructed here |
| `agents/decision_agent.py` | `core.config` (`get_config`) | `agents/decision_agent.py:15` | process-wide `AppConfig` singleton; live trading is forced off here and can only be enabled via env vars |
| `agents/decision_agent.py` | `core.scheduler` (`is_us_market_open`) | `agents/decision_agent.py:16` | session state stamped into the cycle record |
| `agents/decision_agent.py` | `core.market_store` | `agents/decision_agent.py:17` | in-memory price/book/funding mirror; avoids a DB read on every hot path |
| `agents/decision_agent.py` | `trading.models` | `agents/decision_agent.py:18` | the closed order vocabulary; `Order.quantity` is what the agent sizes |
| `agents/decision_agent.py` | `trading.risk_manager` (`RiskManager`) | `agents/decision_agent.py:19` | sizing, SL/TP, fees and the pre-trade gate; the agent holds the same instance the engine does |
| `agents/decision_agent.py` | `database.models` (`Signal`) | `agents/decision_agent.py:20` | row dataclass inserted per cycle |
| `agents/decision_agent.py` | `core.logger` (`get_logger`) | `agents/decision_agent.py:21` | the one `trading_bot` logger tree every module writes through |
| `agents/decision_agent.py` | `core.utils` (`parse_db_timestamp`) | `agents/decision_agent.py:22` | SQLite stores naive UTC strings; this is what makes them comparable to `time.time()` |
| `agents/execution_agent.py` | `core.event_bus` | `agents/execution_agent.py:15` | async fan-out point between agents and engines; the bus is injected, never constructed here |
| `agents/execution_agent.py` | `core.config` (`get_config`) | `agents/execution_agent.py:16` | process-wide `AppConfig` singleton; live trading is forced off here and can only be enabled via env vars |
| `agents/execution_agent.py` | `core.market_store` | `agents/execution_agent.py:17` | in-memory price/book/funding mirror; avoids a DB read on every hot path |
| `agents/execution_agent.py` | `core.logger` (`get_logger`) | `agents/execution_agent.py:18` | the one `trading_bot` logger tree every module writes through |
| `agents/execution_agent.py` | `core.utils` (`parse_db_timestamp`) | `agents/execution_agent.py:19` | SQLite stores naive UTC strings; this is what makes them comparable to `time.time()` |
| `agents/execution_agent.py` | `trading.models` | `agents/execution_agent.py:20` | the closed order vocabulary |
| `agents/execution_agent.py` | `trading.risk_manager` (`RiskManager`) | `agents/execution_agent.py:21` | sizing, SL/TP, fees and the pre-trade gate; the agent holds the same instance the engine does |
| `analysis/fundamental.py` | `core.logger` (`get_logger`) | `analysis/fundamental.py:7` | the one `trading_bot` logger tree every module writes through |
| `analysis/ml_signals.py` | `core.config` (`get_config`) | `analysis/ml_signals.py:15` | process-wide `AppConfig` singleton; live trading is forced off here and can only be enabled via env vars |
| `analysis/ml_signals.py` | `core.logger` (`get_logger`) | `analysis/ml_signals.py:16` | the one `trading_bot` logger tree every module writes through |
| `analysis/technical.py` | `core.config` (`get_config`) | `analysis/technical.py:11` | process-wide `AppConfig` singleton; live trading is forced off here and can only be enabled via env vars |
| `analysis/technical.py` | `core.logger` (`get_logger`) | `analysis/technical.py:12` | the one `trading_bot` logger tree every module writes through |
| `analysis/vol_target.py` | `core.config` (`get_config`) | `analysis/vol_target.py:28` | process-wide `AppConfig` singleton; live trading is forced off here and can only be enabled via env vars |
| `analysis/vol_target.py` | `core.logger` (`get_logger`) | `analysis/vol_target.py:29` | the one `trading_bot` logger tree every module writes through |
| `analysis/vol_target.py` | `core.market_store` | `analysis/vol_target.py:30` | in-memory price/book/funding mirror; avoids a DB read on every hot path |
| `analysis/volatility.py` | `core.config` (`get_config`) | `analysis/volatility.py:25` | process-wide `AppConfig` singleton; live trading is forced off here and can only be enabled via env vars |
| `analysis/volatility.py` | `core.logger` (`get_logger`) | `analysis/volatility.py:26` | the one `trading_bot` logger tree every module writes through |
| `analysis/volatility.py` | `core.market_store` | `analysis/volatility.py:27` | in-memory price/book/funding mirror; avoids a DB read on every hot path |
| `trading/fill_cost.py` | `core.market_store` | `trading/fill_cost.py:40` | in-memory price/book/funding mirror; avoids a DB read on every hot path |
| `trading/position_manager.py` | `core.config` (`get_config`) | `trading/position_manager.py:12` | process-wide `AppConfig` singleton; live trading is forced off here and can only be enabled via env vars |
| `trading/position_manager.py` | `core.event_bus` | `trading/position_manager.py:13` | async fan-out point between agents and engines; the bus is injected, never constructed here |
| `trading/position_manager.py` | `core.logger` (`get_logger`) | `trading/position_manager.py:14` | the one `trading_bot` logger tree every module writes through |
| `trading/position_manager.py` | `core.market_store` | `trading/position_manager.py:15` | in-memory price/book/funding mirror; avoids a DB read on every hot path |
| `trading/position_manager.py` | `database.db` (`get_db`) | `trading/position_manager.py:16` | the process-wide aiosqlite connection |
| `trading/position_manager.py` | `database.repository` (`Repository`) | `trading/position_manager.py:17` | every SQL statement the bot issues, in one class |
| `trading/position_manager.py` | `database.models` | `trading/position_manager.py:18` | row dataclass for an open or closed position |
| `trading/risk_manager.py` | `core.config` (`get_config`) | `trading/risk_manager.py:7` | process-wide `AppConfig` singleton; live trading is forced off here and can only be enabled via env vars |
| `trading/risk_manager.py` | `core.logger` (`get_logger`) | `trading/risk_manager.py:8` | the one `trading_bot` logger tree every module writes through |
| `trading/paper_engine.py` | `core.config` (`get_config`) | `trading/paper_engine.py:12` | process-wide `AppConfig` singleton; live trading is forced off here and can only be enabled via env vars |
| `trading/paper_engine.py` | `core.event_bus` | `trading/paper_engine.py:13` | async fan-out point between agents and engines; the bus is injected, never constructed here |
| `trading/paper_engine.py` | `core.logger` (`get_logger`) | `trading/paper_engine.py:14` | the one `trading_bot` logger tree every module writes through |
| `trading/paper_engine.py` | `core.market_store` | `trading/paper_engine.py:15` | in-memory price/book/funding mirror; avoids a DB read on every hot path |
| `trading/paper_engine.py` | `database.db` (`get_db`) | `trading/paper_engine.py:16` | the process-wide aiosqlite connection |
| `trading/paper_engine.py` | `database.repository` (`Repository`) | `trading/paper_engine.py:17` | every SQL statement the bot issues, in one class |
| `trading/paper_engine.py` | `database.models` (`AgentLog`) | `trading/paper_engine.py:18` | row dataclass for the cycle/error audit trail |
| `trading/paper_engine.py` | `trading.models` | `trading/paper_engine.py:19` | dispatch key for open/close routing |
| `trading/paper_engine.py` | `trading.risk_manager` (`RiskManager`) | `trading/paper_engine.py:20` | sizing, SL/TP, fees and the pre-trade gate; the agent holds the same instance the engine does |
| `trading/paper_engine.py` | `trading.fill_cost` | `trading/paper_engine.py:21` | the single cost model; `_fill_price` delegates to it rather than re-deriving the arithmetic |
| `trading/paper_engine.py` | `trading.position_manager` (`PositionManager`) | `trading/paper_engine.py:28` | the only first-party constructor of the paper position lifecycle |
| `trading/live/client.py` | `core.logger` (`get_logger`) | `trading/live/client.py:28` | the one `trading_bot` logger tree every module writes through |
| `trading/live/console.py` | `core.logger` (`get_logger`) | `trading/live/console.py:30` | the one `trading_bot` logger tree every module writes through |
| `trading/live/engine.py` | `core.config` (`LiveConfig`) | `trading/live/engine.py:29` | `LiveConfig` limits block, passed straight through to the safety gate |
| `trading/live/engine.py` | `core.logger` (`get_logger`) | `trading/live/engine.py:30` | the one `trading_bot` logger tree every module writes through |
| `trading/live/executor.py` | `core.event_bus` (`Channels`) | `trading/live/executor.py:40` | channel-name constants for subscribe/publish |
| `trading/live/executor.py` | `core.logger` (`get_logger`) | `trading/live/executor.py:41` | the one `trading_bot` logger tree every module writes through |
| `trading/live/executor.py` | `core.market_store` | `trading/live/executor.py:42` | in-memory price/book/funding mirror; avoids a DB read on every hot path |
| `trading/live/executor.py` | `database.db` (`get_db`) | `trading/live/executor.py:43` | the process-wide aiosqlite connection |
| `trading/live/executor.py` | `database.models` | `trading/live/executor.py:44` | row dataclass for the cycle/error audit trail |
| `trading/live/executor.py` | `database.repository` (`Repository`) | `trading/live/executor.py:45` | every SQL statement the bot issues, in one class |
| `trading/live/executor.py` | `trading.risk_manager` (`RiskManager`) | `trading/live/executor.py:47` | sizing, SL/TP, fees and the pre-trade gate; the agent holds the same instance the engine does |
| `trading/live/executor.py` | `trading.models` (`TradeAction`) | `trading/live/executor.py:399` | dispatch key for open/close routing |
| `trading/live/executor.py` | `trading.models` (`TradeAction`) | `trading/live/executor.py:409` | dispatch key for open/close routing |
| `trading/live/safety.py` | `core.config` (`LiveConfig`) | `trading/live/safety.py:24` | `LiveConfig` limits block, passed straight through to the safety gate |
| `trading/live/safety.py` | `core.logger` (`get_logger`) | `trading/live/safety.py:25` | the one `trading_bot` logger tree every module writes through |
| `analysis/backtester.py` | `core.config` (`get_config`) | `analysis/backtester.py:36` | process-wide `AppConfig` singleton; live trading is forced off here and can only be enabled via env vars |
| `analysis/backtester.py` | `core.logger` (`get_logger`) | `analysis/backtester.py:37` | the one `trading_bot` logger tree every module writes through |
| `dashboard/app.py` | `core.config` (`get_config`) | `dashboard/app.py:17` | process-wide `AppConfig` singleton; live trading is forced off here and can only be enabled via env vars |
| `dashboard/app.py` | `core.logger` (`get_logger`) | `dashboard/app.py:18` | the one `trading_bot` logger tree every module writes through |
| `dashboard/callbacks/update_callbacks.py` | `core.config` (`get_config`) | `dashboard/callbacks/update_callbacks.py:17` | process-wide `AppConfig` singleton; live trading is forced off here and can only be enabled via env vars |
| `dashboard/callbacks/update_callbacks.py` | `core.logger` (`get_logger`) | `dashboard/callbacks/update_callbacks.py:18` | the one `trading_bot` logger tree every module writes through |
| `dashboard/callbacks/update_callbacks.py` | `core.market_store` | `dashboard/callbacks/update_callbacks.py:19` | in-memory price/book/funding mirror; avoids a DB read on every hot path |
| `dashboard/callbacks/update_callbacks.py` | `analysis.probability_engine` | `dashboard/callbacks/update_callbacks.py:20` | rendered directly by the scanner, so the HUD shows the same numbers the agents read |
| `dashboard/callbacks/update_callbacks.py` | `analysis.technical` (`TechnicalAnalyzer`) | `dashboard/callbacks/update_callbacks.py:26` | indicator stamping and the seven-group score |
| `dashboard/layouts/hud_figures.py` | `analysis.probability_engine` | `dashboard/layouts/hud_figures.py:223` | function-local fallback: a neutral 50/50 diffusion curve before the first callback fires |
| `dashboard/layouts/price_chart.py` | `core.config` (`get_config`) | `dashboard/layouts/price_chart.py:9` | supplies the symbol dropdown options and the default selection |
| `data/hyperliquid_feed.py` | `core.logger` (`get_logger`) | `data/hyperliquid_feed.py:40` | the one `trading_bot` logger tree every module writes through |
| `data/hyperliquid_feed.py` | `core.market_store` | `data/hyperliquid_feed.py:41` | in-memory price/book/funding mirror; avoids a DB read on every hot path |
| `data/macro_fetcher.py` | `core.config` (`get_config`) | `data/macro_fetcher.py:14` | process-wide `AppConfig` singleton; live trading is forced off here and can only be enabled via env vars |
| `data/macro_fetcher.py` | `core.logger` (`get_logger`) | `data/macro_fetcher.py:15` | the one `trading_bot` logger tree every module writes through |
| `data/news_fetcher.py` | `core.config` (`get_config`) | `data/news_fetcher.py:13` | process-wide `AppConfig` singleton; live trading is forced off here and can only be enabled via env vars |
| `data/news_fetcher.py` | `core.logger` (`get_logger`) | `data/news_fetcher.py:14` | the one `trading_bot` logger tree every module writes through |
| `data/price_feed.py` | `core.config` (`get_config`) | `data/price_feed.py:24` | process-wide `AppConfig` singleton; live trading is forced off here and can only be enabled via env vars |
| `data/price_feed.py` | `core.event_bus` | `data/price_feed.py:25` | async fan-out point between agents and engines; the bus is injected, never constructed here |
| `data/price_feed.py` | `core.logger` (`get_logger`) | `data/price_feed.py:26` | the one `trading_bot` logger tree every module writes through |
| `data/price_feed.py` | `core.market_store` | `data/price_feed.py:394` | in-memory price/book/funding mirror; avoids a DB read on every hot path |
| `data/price_feed.py` | `core.market_store` | `data/price_feed.py:424` | in-memory price/book/funding mirror; avoids a DB read on every hot path |
| `data/price_feed.py` | `core.market_store` | `data/price_feed.py:523` | in-memory price/book/funding mirror; avoids a DB read on every hot path |
| `data/price_feed.py` | `core.market_store` | `data/price_feed.py:582` | in-memory price/book/funding mirror; avoids a DB read on every hot path |
| `data/price_feed.py` | `core.market_store` | `data/price_feed.py:593` | in-memory price/book/funding mirror; avoids a DB read on every hot path |
| `data/sentiment.py` | `core.logger` (`get_logger`) | `data/sentiment.py:11` | the one `trading_bot` logger tree every module writes through |
| `database/db.py` | `core.config` (`get_config`) | `database/db.py:7` | process-wide `AppConfig` singleton; live trading is forced off here and can only be enabled via env vars |
| `database/db.py` | `core.logger` (`get_logger`) | `database/db.py:8` | the one `trading_bot` logger tree every module writes through |
| `database/repository.py` | `core.logger` (`get_logger`) | `database/repository.py:12` | the one `trading_bot` logger tree every module writes through |
| `ml/predictor.py` | `analysis.ml_signals` (`MLSignalGenerator`) | `ml/predictor.py:11` | 7-feature vector into the pickled RandomForest, with a rule-based fallback |
| `ml/predictor.py` | `core.logger` (`get_logger`) | `ml/predictor.py:12` | the one `trading_bot` logger tree every module writes through |
| `ml/trainer.py` | `core.logger` (`get_logger`) | `ml/trainer.py:14` | the one `trading_bot` logger tree every module writes through |

## Group summary

| Group | Files | Lines | May import | Imported by |
|---|---|---|---|---|
| Configuration | 1 | 1030 | — | data/ui, decision, infra, live, paper, sense, signal, trading |
| Core infrastructure | 6 | 1311 | config | data/ui, decision, live, paper, sense, signal, trading |
| Sensing agents | 3 | 988 | config, data/ui, decision, infra, signal | — |
| Decision and execution agents | 3 | 1019 | config, data/ui, infra, trading | sense |
| Signal layer | 7 | 1929 | config, infra | data/ui, sense |
| Fill cost, position management and risk | 4 | 1309 | config, data/ui, infra | decision, live, paper |
| Paper trading engine | 1 | 1113 | config, data/ui, infra, trading | — |
| Live trading path | 6 | 4057 | config, data/ui, infra, trading | — |
| Data, persistence, UI and native code | 26 | 9375 | config, infra, signal | decision, live, paper, sense, trading |
