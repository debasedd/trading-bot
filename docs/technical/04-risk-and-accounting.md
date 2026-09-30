# Risk, Costs and Accounting

The money layer. This is the part of the codebase where a wrong number is a wrong
number about real money, and where the difference between the paper and live
regimes is widest.

All figures below were re-derived from the working tree, not carried over from
`docs/context/CONTEXT.md`. Where that document is now wrong, it is called out
explicitly.

---

## 1. Two regimes, one ledger

The bot has two execution engines and one shared SQLite file.

```
                    run.py:396-399
   executor = self.paper_engine
   if self.mode in ("testnet","mainnet"):
       executor = await self._build_live_executor()
   self.executor = executor        →  ExecutionAgent(..., executor)  run.py:401

   ┌────────────────────────────┐        ┌──────────────────────────────┐
   │ PAPER  paper_engine.py     │        │ LIVE  live/executor.py       │
   │ 1113 lines                 │        │ 1061 lines                   │
   │                            │        │                              │
   │ fill_cost entry slippage   │        │ NO fill_cost anywhere        │
   │ fill_cost exit  slippage   │        │ real exchange avg_price      │
   │ ATR volatility gate  ✔     │        │ ATR gate          ✘          │
   │ tick-quality guard    ✔    │        │ tick guard        ✘          │
   │ RiskManager.validate_trade ✔│       │ validate_trade    ✘          │
   │ account.balance  written   │        │ account.balance  NEVER written│
   │ max_drawdown + sharpe  ✔   │        │ mdd + sharpe      ✘          │
   │ SafetyGate                 │        │ SafetyGate.can_send (9+7)    │
   │ (no gate at all)           │        │  MANDATORY, unbypassable     │
   └───────────┬────────────────┘        └──────────────┬───────────────┘
               │  positions / trades / account / balance_history  ← no mode column
               ▼  ▼  ▼
        SQLite  database/db.py  (10 tables, SCHEMA_SQL)
```

The divergence is one line, `run.py:396-399`, and the two paths never rejoin in
accounting terms. They rejoin only in the shared tables and the shared
`Channels.POSITION_UPDATE` stream.

`database/db.py:29-48` defines the `positions` table with **no mode or venue
column**. The only live marker is a `"LIVE "` string prefix written into
`positions.reasoning` (`trading/live/executor.py:550`). No dashboard callback
reads it. The HUD's `PAPER TRADING` badge (`dashboard/layouts/hud.py:68`) is a
hardcoded literal, so a live session renders itself as paper.

**Consequence for the reader:** in live mode the HUD's balance, equity, P&L,
max-drawdown and Sharpe tiles are a hybrid of two incompatible ledgers, and the
`balance_snapshot` scheduler job (`run.py:614-618`) is wired *unconditionally* to
`self.paper_engine.position_manager.take_balance_snapshot` — so `balance_history`,
which is the sole input to max-drawdown and Sharpe, is built from the untouched
paper balance.

---

## 2. Risk limits — exact values and trigger expressions

### 2.1 Paper regime (`config.yaml`, loaded into `core/config.py` dataclasses)

| Limit | Value | Source | Trigger expression | Enforced at |
|---|---|---|---|---|
| `risk.max_risk_per_trade` | `0.005` (0.5%) | config.yaml:37 | sizing: `margin = balance * risk_pct` | `risk_manager.py:361` |
| `risk.max_leverage` | `50` | config.yaml:38 | `leverage = min(leverage, max_leverage)` | `risk_manager.py:97`, `:358` |
| `risk.max_daily_loss` | `0.10` (10%) | config.yaml:39 | `abs(daily_pnl)/reference >= 0.10` | `risk_manager.py:320-326` |
| `risk.max_drawdown` | `0.20` (20%) | config.yaml:40 | `(peak_balance - equity)/peak_balance >= 0.20` | `risk_manager.py:330-333` |
| `risk.max_open_positions` | `30` | config.yaml:41 | `open_positions >= 30` | `risk_manager.py:303-304` |
| `risk.default_leverage` | `10` | config.yaml:42 | leverage fallback | `risk_manager.py:94`, `:356` |
| Margin gate | `balance * 0.9` | hard-coded | `margin_required > balance * 0.9` | `risk_manager.py:299-300` |
| `fees.taker` | `0.00045` | config.yaml:60 | `fee = \|qty\|*\|px\|*0.00045` | `risk_manager.py:202-240` |
| `fees.maker` | `0.00015` | config.yaml:59 | never used — every site passes `"TAKER"` | `risk_manager.py:215` |
| `scalping.tight_sl_pct` | `0.0025` (0.25%) | config.yaml:140 | SL floor for the resolved pair | `paper_engine.py:375` |
| `scalping.fast_tp_pct` | `0.0060` (0.60%) | config.yaml:139 | TP floor | `paper_engine.py:376` |
| `dynamic_tp_sl.min_sl_pct` | `0.0025` | config.yaml:181 | `sl_pct = min(max(1.5*atr, 0.0025), 0.0150)` | `volatility.py:331` |
| `dynamic_tp_sl.max_sl_pct` | `0.0150` (1.50%) | config.yaml:182 | same clamp, upper bound | `volatility.py:331` |
| `dynamic_tp_sl.min_risk_reward` | `1.5` | config.yaml:183 | `tp_pct = max(sl_pct*1.5, 0.0060)` | `volatility.py:332`, `paper_engine.py:386` |
| `dynamic_tp_sl.max_breakeven_win_rate` | `0.65` | config.yaml:185 | reject if computed `breakeven_wr > 0.65` | `volatility.py:404` |
| `scalping.max_tick_age_seconds` | `1.5` | config.yaml:153 | reject tick older than this | `paper_engine.py:311-315` |
| `scalping.stale_tick_window_seconds` | `3.0` | config.yaml:154 | median window | `paper_engine.py:323` |
| `scalping.stale_tick_min_samples` | `5` | config.yaml:155 | fail-open below this | `paper_engine.py:325-326` |
| `scalping.breakeven_trigger_pct` | `0.0020` | config.yaml:159 | move SL to breakeven at +0.20% | `execution_agent.py:226` |
| `scalping.breakeven_offset_pct` | `0.0015` | config.yaml:160 | SL lands at entry ±0.15% | `execution_agent.py:224`, `:234` |
| `scalping.min_confidence` | `0.40` | config.yaml:141 | signal strength gate | `decision_agent.py:217` |
| `scalping.max_spread_pct` | `0.0006` | config.yaml:158 | skip symbol if spread wider | `decision_agent.py:228` |
| `scalping.max_hold_seconds` | `300` | config.yaml:136 | auto-close past 5 min | `execution_agent.py:262` |
| `scalping.min_hold_seconds` | `15` | config.yaml:137 | refuse instant close | `execution_agent.py:258` |
| `scalping.reversal_close_threshold` | `0.70` | config.yaml:146 | reversal gate | `decision_agent.py` |

The gate is a **conjunction**: `paper_engine.py:665-672` passes all six inputs to
`validate_trade`, and a single failing reason rejects the whole order
(`paper_engine.py:674`). `required_cash = margin + estimated_fee`
(`paper_engine.py:655-656`) — the fee is reserved inside the gate, not discovered
at the next layer.

```
open order arrives
      │
      ▼
_get_price (cache → market_store)             paper_engine.py:222
      │  None → refuse "harga tidak tersedia"
      ▼
input guard: qty<=0, lev<=0, price<=0         paper_engine.py:472-479
      ▼
_resolve_tp_sl  ── VolatilityGateError ──► TRADE_REJECTED   paper_engine.py:523-546
      ▼
_fill_price  (entry crossing cost)            paper_engine.py:562
      ▼
SL / TP RECOMPUTED from the fill              paper_engine.py:566-567
      ▼
_tick_quality_guard ──► TRADE_REJECTED       paper_engine.py:587-613
      ▼
validate_trade (6 checks) ──► TRADE_REJECTED  paper_engine.py:665-695
      ▼
PositionManager.open_position
```

### 2.2 Live regime (`SafetyGate`, `trading/live/safety.py`)

`run.py:291` builds a **bare `LiveConfig()`**, not `get_config().live`. Every
number below is a dataclass default from `core/config.py:335-343`, not from
`config.yaml`, and not from anything `console.edit_rules` mutated in memory.

**`master_blockers` — 9 checks, always run** (`safety.py:247-298`):

| # | Blocker | Trigger | Line | Effective value |
|---|---|---|---|---|
| 0 | `COUNTER_STATE_UNREADABLE` | `not self.counters.readable` | :261-262 | corrupt `data_store/live_counters.json` |
| 1 | `LIVE_DISABLED` | `TRADEBOT_LIVE` ∉ `("1","true","yes")` | :266-267 | env only |
| 2 | `NOT_CONFIRMED` | `TRADEBOT_LIVE_CONFIRMED` ∉ same set | :271-274 | env only |
| 3 | `KILL_SWITCH` | `self.engaged` **or** `TRADEBOT_LIVE_KILL_SWITCH` ∉ `("","0","false","no")` | :277-280 | latched |
| 4 | `MISSING_KEY` | `not self.private_key` | :283-284 | env only |
| 5 | `OUTSIDE_WINDOW` | `not in_live_window(now)` | :287-288 | `live_window_utc = (13, 23)` UTC |
| 6 | `DAILY_ORDER_LIMIT` | `orders_sent >= 200` | :291-292 | 200 |
| 7 | `DAILY_LOSS_LIMIT` | `realized_pnl <= -50.0` | :293-294 | −50 USDC |
| 8 | `TOO_MANY_ERRORS` | `consecutive_errors >= 3` | :295-296 | 3 |

**`order_blockers` — 7 checks, only if master is clean** (`safety.py:300-346`):

| Blocker | Trigger | Line | Effective value |
|---|---|---|---|
| `INVALID_INPUT` | `size <= 0` or `price <= 0` | :319-322 | — |
| `MISSING_CLOID` | `not is_close and not cloid` | :327-328 | — |
| `LEVERAGE_TOO_HIGH` | `not (1 <= lev <= 10)` | :332-334 | `max_leverage = 10` |
| `ORDER_TOO_LARGE` | `notional > 100.0` | :337-338 | 100 USDC |
| `POSITION_TOO_LARGE` | `symbol_exposure + notional > 300.0` | :339-340 | 300 USDC |
| `EXPOSURE_TOO_LARGE` | `current_exposure + notional > 600.0` | :341-342 | 600 USDC |
| `COLLATERAL_TOO_LOW` | `free_collateral - notional < 100.0` | :343-344 | 100 USDC |

`can_send` returns a **tuple** `(allowed, blockers)` (`safety.py:347-382`), never a
bare bool, so the reason always travels with the refusal. It is called from
exactly one place: `engine.py:236` inside `submit_order`, which is the only route
to the exchange.

**The live window is half-open `[13, 23)` UTC** (`safety.py:242-245`), with a
wrap-around branch for overnight windows.

### 2.3 The paper/live asymmetry in risk coverage

Seven risk mechanisms present in the paper engine have **no live caller at all**
(verified by grep across `trading/live/`):

| Mechanism | Defined | Consequence in live |
|---|---|---|
| `_tick_quality_guard` | `paper_engine.py:280` | stale ticks and median outliers accepted |
| `assess_volatility_gate` | `volatility.py:347` | no fee/R:R kill; a 3.4× stop can pass (see §4.4) |
| `validate_trade` | `risk_manager.py:273` | **no `max_open_positions`, no drawdown cap, no %-of-initial daily breaker** |
| dynamic TP/SL floor re-imposition | `paper_engine.py:374-386` | SL/TP reach the exchange as the agent computed them, from the pre-fill price |
| `RiskManager.kelly_criterion` | `risk_manager.py:380` | zero callers repo-wide |
| `LiveExchange.cancel` | `client.py:462` | zero callers; no production path ever cancels a stale trigger |
| `LiveExchange.cancel_all` | `client.py:483` | only from the uncalled `emergency_flat` |

Live does have one thing paper does not: the nine master blockers plus seven
order blockers, which is the only unbypassable admission gate in the codebase.

The live daily-loss breaker exists but measures something different: an absolute
50 USDC against `counters.realized_pnl` (`safety.py:293`), whereas paper measures
`abs(daily_pnl)/initial_balance` against `0.10` (`risk_manager.py:320-322`). A
position bleeding while still open is counted by neither.

---

## 3. Kill switches

### 3.1 One-way latch

`SafetyGate.engaged` is written in exactly three places in production:

```
safety.py:177   self.engaged = False        # construction only
safety.py:388   self.engaged = True         # engage_kill_switch()
safety.py:408   self.engaged = True         # inlined inside record_error()
```

There is **no reset, no TTL, and no operator affordance** in production code.
Recovery is out-of-process: restart the bot, or set
`TRADEBOT_LIVE_KILL_SWITCH=0` — and because `self.env = dict(os.environ)` is a
**snapshot copy** taken at construction (`safety.py:171`), that variable must be
present *before* the process starts. Mutating `os.environ` at runtime does not
reach an already-built gate.

`TRADEBOT_LIVE_KILL_SWITCH` is a **trip** path, not only a release path:
`safety.py:277-280` treats anything outside `("", "0", "false", "no")` as
tripped. Unset yields `""`, which is *in* the tuple, so the switch does not trip
by default. The log message at `safety.py:406` names the release value, not the
trip value — a reader can invert the semantics.

### 3.2 Raisers

Seven `engage_kill_switch` call sites. Five are reachable in production:

| Site | Trigger | Reachable? |
|---|---|---|
| `engine.py:404` | SL attach failed | **yes** — `submit_order:338`, `check_pending_fills:489` |
| `engine.py:555` | `health_check` cannot read the exchange | **yes** — `run.py:307`, `engine.py:629` |
| `engine.py:599` | `health_check` returned `ok=False` | **yes** — same callers |
| `executor.py:573` | `_persist_open` raised | **yes** |
| `executor.py:587` | `_persist_open` got `row_id is None` | **yes** |
| `engine.py:128` | reconcile mismatch, and only when `not cfg.auto_reconcile` | **no** — `reconcile` has no production caller |
| `engine.py:737` | emergency-flat not clean | **no** — `emergency_flat` has no production caller |

Plus the inlined latch at `safety.py:408` inside `record_error`, reachable from
nine call sites (`engine.py:287, 298, 305, 446, 504, 650`;
`executor.py:290, 972`).

### 3.3 Three defects in the kill-switch machinery

**Tripping the switch permanently disables the late-fill protection installer.**
`health_check` short-circuits on `gate.engaged` and returns before contacting
the exchange (`engine.py:540-542`); `run_loop` then `continue`s before
`check_pending_fills` runs (`engine.py:629-636`). Since `engine.py:404` engages
the switch precisely when a stop fails to attach, the moment protection is most
needed is the moment its installer is switched off, and nothing re-enables it.

**A TP attach failure is silent.** `engine.py:394-404` tests only
`sl = results.get("sl")`; `tp` is never checked. `submit_order` returns
`"protected": position.sl_order_id is not None` (`engine.py:347`) — `True` — and
`_persist_open` stores the local TP (`executor.py:559`) even though no trigger
rests on the exchange.

**Late-filling GTC orders get a ±1% stop, four times the configured 0.25%.**
`engine.py:484-486` uses `order.get("stopPx")`, which a plain GTC limit never
sets, so the hardcoded `(price*0.99)` / `(price*1.01)` always runs. Leverage for
the late position is `gate.cfg.max_leverage` — the maximum, not the intended
value (`engine.py:501`).

---

## 4. The complete cost model

### 4.1 The one function

`trading/fill_cost.py:105` `fill_price_after_cost`, wrapped twice:
`PaperTradingEngine._fill_price` (`paper_engine.py:240`, entry) and
`close_fill_price` (`fill_cost.py:178`, exit and liquidation).

Constants (`fill_cost.py:45-48`), verbatim:

```python
FILL_HALF_SPREAD_FLOOR       = 0.0003   # 3 bps
FILL_IMPACT_FLOOR            = 0.0001   # 1 bps
FILL_BOOK_MALFORMED_SPREAD_PCT = 0.05   # 5% — above this the book is broken, not the market
FILL_MAX_TOTAL_COST_PCT      = 0.0050   # 50 bps, safety net only
```

Arithmetic (`fill_cost.py:141-163`):

```
half   = FILL_HALF_SPREAD_FLOOR                       # 0.0003
if book provably fresh:  half   = max(observed, 0.0003)   # :152  max(), never min()
                        source = "live_book"
else:                        source = "floor"
impact = FILL_IMPACT_FLOOR                            # :157  constant, never reads the book
total  = min(half + impact, FILL_MAX_TOTAL_COST_PCT) # :159
signed = +total if fill_side == "BUY" else -total     # :162
fill   = ref_price * (1.0 + signed)                   # :163
```

Observed half-spread is `(best_ask - best_bid) / (2 * mid)` (`fill_cost.py:95`),
admitted only when the book is provably fresh — `age is None` counts as
**stale, not fresh** (`fill_cost.py:76`), and the age budget is read from
`scalping.max_tick_age_seconds` (`fill_cost.py:133-139`) so the cost model and
the tick guard cannot drift apart.

The `max()` at line 152 is the load-bearing choice, and the module docstring
(`fill_cost.py:17-33`) states why: `market_store` never expires the order book,
so a narrow book is the most likely one to be stale. Trusting it without a floor
would return a cost near zero — the exact bug the model exists to remove.

**There is no funding cost anywhere.** `market_store.set_funding` /
`get_funding` exist (`core/market_store.py:211-221`) and `data/hyperliquid_feed.py:478-479`
populates them, and `OrderFlowAgent` reads funding as a contrarian *signal*
(`agents/direction_agents.py:288-294`). It is never added to any P&L. On
Hyperliquid a perpetual position held across a funding timestamp pays or receives
it; on a 5-minute scalp the exposure is small but nonzero, and it is unmodelled
in both regimes.

### 4.2 Fees

`risk_manager.py:202-240`:

```python
rate  = self.fees.taker if fee_type == "TAKER" else self.fees.maker   # :215
notional = abs(Decimal(str(quantity))) * abs(Decimal(str(price)))    # :218
fee   = (notional * Decimal(str(rate))).quantize(
            Decimal("0.0000000001"), rounding=ROUND_DOWN)            # :238-239
return abs(float(fee))
```

Two properties are deliberate and documented in-source:

- **Always non-negative.** `abs()` on both operands, because `abs(q)*abs(p)`
  from one negative and one positive would yield a *negative* fee, i.e. a cost
  that credits the balance. Adding negative fees to a balance is invisible in a
  P&L report (`risk_manager.py:206-213`).
- **Quantized to 1e-10, not to cents.** The prior cent rounding made a 100 USDT
  notional pay 0.09 instead of 0.08, breaking proportionality, and made a 0.10
  USDT order pay nothing at all — so a small-size scalping bot would look
  cost-free and profitable (`risk_manager.py:220-237`).

`maker = 0.00015` is configured (`config.yaml:59`) and **never used**: every call
site passes `"TAKER"`.

### 4.3 True round-trip cost against the configured stop

Worked at `ref = 100.00`, no book (floor path), `taker = 0.00045`,
`sl = 0.0025`, `tp = 0.0060`, quantity 1. Executed against the production
arithmetic chain including the `Decimal` `ROUND_DOWN` quantizations:

```
entry crossing   half 3.0 + impact 1.0   =  4.00 bps
exit  crossing                              =  4.00 bps
fees, 2 × taker                           =  9.00 bps
────────────────────────────────────────────────────
TRUE ROUND TRIP                           = 17.00 bps
```

That is **68.0% of the 25 bps configured stop** and **28.3% of the 60 bps
target**.

But the stop is **re-anchored to the fill**, not the reference price
(`paper_engine.py:566-567`), so the 4 bps entry crossing buys no extra stop
room. The market only has to travel 21 bps from the reference to reach the stop,
because the fill already moved 4 bps. Measured, per side:

| | LONG | SHORT |
|---|---|---|
| entry fill | 100.04000000 | 99.96000000 |
| SL level | 99.78989999 (25.0000 bps from fill) | 100.20990000 (25.0000 bps) |
| TP level | 100.64023999 (60.0000 bps from fill) | 99.36024000 (60.0000 bps) |
| SL exit fill | 99.74998403 | 100.24998396 |
| fee open / close | 0.04501800 / 0.04488749 | 0.04498200 / 0.04511249 |
| **net TP** | **+0.46971201** (+46.95 bps) | **+0.47028801** (+47.05 bps) |
| **net SL** | **−0.37990549** (−37.98 bps) | **−0.38009449** (−38.02 bps) |
| net R:R | 1:1.2364 | 1:1.2373 |
| breakeven WR | 44.71% | 44.70% |

```
announced            realized
stop   −25.00 bps  →  −37.98 bps   = 151.9% of what config.yaml promises
target +60.00 bps  →  +46.95 bps   =  78.3% of what config.yaml promises
R:R     1 : 2.40   →  1 : 1.24
breakeven WR  41.7% (naive)  →  44.71% (true)
```

What actually lands on a *stopped* trade is the exit crossing plus both fees =
**13.0 bps = 52.0% of the stop**. The 4 bps entry leg is already inside the fill
price, so it is not charged twice — but it is also not free, because the realized
stop distance is measured from the reference the operator reasoned about.

Direction convention verified correct for both sides: LONG entry fills above
mid, SHORT entry below; LONG exit receives less, SHORT exit pays more
(`fill_cost.py:196`). The two sides are mirror-symmetric to within 0.1 bps.

### 4.4 The volatility gate models 9 bps, not 17

`analysis/volatility.py:383-384`:

```python
taker = cfg.fees.taker          # 0.00045
roundtrip = taker * 2           # 0.0009  == 9.00 bps
```

The module never imports `trading.fill_cost`, so it is blind to the 8 bps of
crossing cost:

| sl | tp | true SL_net | true TP_net | true R:R | true beWR | gate beWR | gap |
|---|---|---|---|---|---|---|---|
| 25.0 bps | 60.0 bps | −37.98 | +46.95 | 1:1.236 | 44.71% | 40.00% | **+4.71** |
| 150.0 bps | 225.0 bps | −162.87 | +211.81 | 1:1.300 | 43.47% | 42.40% | +1.07 |

**The gate is also blind to the book.** At a 49 bps observed half-spread,
`total` caps at 50 bps per crossing (`fill_cost.py:159`), the round trip becomes
109 bps, a stopped trade loses 125 bps — **five times the announced stop** — and
a winning trade nets *negative*. Executed:

```
assess_volatility_gate("X", 0.0025, 0.0060, cfg)  →  None     (accepts)
true net TP   -43.00 bps
true net SL  -125.00 bps
true R:R      1 : -0.344          breakeven WR  > 100%
```

The cap truncates the sum, so `meta["half_spread_pct"]` can read 49 bps while
`meta["total_cost_pct"]` reads 50 bps — the log shows the observation, the
arithmetic used the cap.

### 4.5 The boot validator has the same blind spot

`core/config.py:959-974` also uses `roundtrip = config.fees.taker * 2`:

```python
roundtrip = config.fees.taker * 2
if scalp.breakeven_offset_pct < roundtrip:
    problems.append(...)
```

Executed: `breakeven_offset_pct = 0.0009` (9 bps) **passes the validator**, and
a breakeven exit at that offset nets **−4.0042 bps** — a "breakeven" stop that
loses money on every hit. The true break-even offset is **13.0 bps**; at 0.0013
the exit nets −0.0076 bps (exactly flat), and the shipped 0.0015 locks
+1.9907 bps.

### 4.6 Live: no cost model at all, and that is correct

`trading/live/` contains zero references to `fill_cost`. `_record_close`
computes P&L straight from the exchange's `avg_price`
(`executor.py:790-793`):

```python
gross     = self.risk_manager.calculate_pnl(side, entry_price, close_price, size)
fee_close = self.risk_manager.calculate_fee(size, close_price, "TAKER")
fee_open  = <read back from the trades table>       # :800-804
net       = gross["pnl"] - fee_open - fee_close     # :806
```

Adding a modelled crossing cost on top of a real fill would double-charge. The
`price_is_final` escape hatch in `PositionManager.close_position`
(`position_manager.py:213`) exists for exactly this caller and has **zero callers
repo-wide** — grep finds only the parameter, its docstring, and the branch.

**But live anchors SL/TP to the pre-fill price.** `ExecutionAgent.think` computes
`order.stop_loss` / `order.take_profit` from `engine.get_price(...)`
(`execution_agent.py:139-140`), `_open` passes them unchanged
(`executor.py:475-476`), and `_persist_open` stores them unchanged
(`executor.py:558-559`). There is no re-anchoring. So the live stop sits ~29 bps
from the real fill rather than 25, and the TP ~56 bps rather than 60. The paper
equity curve and a live fill are not comparable even at identical prices.

`emergency_flat` (`engine.py:655-756`) closes real positions and writes nothing
to SQLite at all — no trade row, no fee, no P&L. It has five tests and zero
production callers.

### 4.7 Dead cost surface

Three items:

- `fill_cost.describe_cost` (`fill_cost.py:207`) has zero callers — only the
  definition and the import at `position_manager.py:21`.
- `paper_engine.py:51-54` re-declares all four `FILL_*` constants locally,
  shadowing the imports it just took at `:21-26`. The values agree today, so no
  live bug — but the two copies can drift silently.
- `position_manager.batch_close_positions` (`position_manager.py:374`) has no
  production caller.

---

## 5. P&L and fee accounting

### 5.1 The double-counting that was fixed

`PositionManager.close_position` (`position_manager.py:167-372`):

```
fill_price, fill_meta = close_fill_price(symbol, pos["side"], close_price, ...)
                          # :216  exit crossing, direction from the POSITION side
pnl_info  = calculate_pnl(pos["side"], pos["entry_price"], fill_price, qty)   # :227
fee       = calculate_fee(qty, fill_price, "TAKER")                          # :235
open_fee  = <first OPEN row from get_trades_by_position>                     # :250-254
net_pnl   = pnl_info["pnl"] - open_fee - fee                                # :257
claimed   = repo.close_position(position_id, fill_price, net_pnl, reason)   # :263
returned  = pos["margin"] + net_pnl + open_fee                               # :297
```

Three invariants make this correct:

1. **The exit crossing is charged inside `close_position`**, never by the caller.
   `_execute_close` explicitly no longer pre-adjusts (`paper_engine.py:830-840`),
   because doing so charged the cost twice. The direction is derived from
   `pos["side"]`, not from a caller argument, so a wrong guess cannot flip the
   sign (`position_manager.py:190-193`).
2. **The opening fee is read back, never recomputed** (`position_manager.py:250-254`).
   The taker rate can change mid-run; a recomputation would disagree with what
   was actually debited.
3. **`open_fee` is added back to the balance credit, not left inside `net_pnl`**
   (`position_manager.py:284-296`). `net_pnl` is what lands in `realized_pnl`
   and in every statistic, so it must show the net figure; the balance credit is
   a separate transaction and must refund the fee exactly once.

Verified by execution on a clean book: `(balance - initial_balance) -
SUM(realized_pnl) = -0.0000000000` — exact to ten decimal places.

The prior double-charge is documented in-source at `position_manager.py:294-296`:
"saldo akhir 2.70 USDT lebih kecil dari seharusnya untuk setiap posisi. Pada 330
posisi paper itu, sekitar 891 USDT tersembunyi di saldo."

### 5.2 Liquidation

`position_manager.py:460-511`:

```python
fill_price, fill_meta = close_fill_price(..., reason="LIQUIDATION")   # :472
fee         = calculate_fee(qty, fill_price, "TAKER")                 # :479
realized_pnl = -pos["margin"] - fee                                   # :480
```

The exit cost is now charged here too (it used to be `fee = 0`, which made the
liquidation path look ~100 bps better than reality), and the closing fee is now
recorded. Measured at `leverage = 10`, `qty = 1`, `entry = 100.04`:

```
margin       10.00400000     (already debited at open, :102)
fee_open      0.04501800     (already debited at open)
liq_fill      90.39998553
fee_close     0.04067999
recorded     -10.04467999
true loss    -10.08969799
understated by 0.04501800 = exactly fee_open
```

The understatement is `fee_open − fee_close`, because `realized_pnl` deducts the
closing fee but not the opening one, while `close_position` deducts both. That
same omission means `SUM(realized_pnl)` no longer reconciles with
`balance − initial_balance` after a liquidation, and `get_daily_realized_pnl`
(`repository.py:526-547`) — the daily-loss breaker's input — under-reads by the
opening fee on every liquidation.

**Liquidation also writes no account statistics.** `_liquidate` calls
`liquidate_position`, `insert_trade`, `publish` — and stops. No
`bump_peak_balance`, no `update_account_stats`. Compare `close_position:309-343`
which does both. Measured: after one liquidation, `account.total_trades` stayed
at 0 while `get_trade_stats()["total_trades"]` was 1. So `total_trades`,
`winning_trades`, `losing_trades`, `profit_factor`, `peak_balance`,
`max_drawdown` and `sharpe_ratio` all silently skip every liquidation — and the
high-water mark ignores a total loss, which makes the drawdown gate at
`risk_manager.py:330-333` read rosier than reality.

**In the shipped configuration liquidation cannot fire anyway.** The liquidation
price is `entry × (1 − 1/lev + 0.004)` (`risk_manager.py:150-151`):

| leverage | liq distance |
|---|---|
| 10 | 960 bps |
| 20 | 460 bps |
| 50 | 160 bps |

against stops of 25 bps (scalp), 150 bps (ATR cap) and 200 bps (non-scalp). The
crossover leverage is `1/(sl + 0.004)` — 153.8 for the scalp stop, 52.6 for the
ATR cap, 41.7 for the non-scalp stop. `risk.max_leverage = 50`, and live
`max_leverage = 10`. So the stop always fires first, and `_liquidate` is
effectively dead in the shipped config. It is still reachable if an operator
raises `max_leverage` above ~154 with scalping on.

### 5.3 `max_drawdown` and `sharpe_ratio`

These **are** now written, at `position_manager.py:325-343`:

```python
equity_series = await self._get_equity_series()          # :325, limit=500
max_dd = self.risk_manager.calculate_max_drawdown(equity_series)   # :326
sharpe = self.risk_manager.calculate_sharpe_ratio(
            _equity_returns(equity_series))                        # :327-329
await repo.update_account_stats(..., max_drawdown=max_dd,
                                sharpe_ratio=sharpe, ...)           # :331-343
```

`CONTEXT.md`'s claim that they are "never written" is **wrong for the paper
path**. It remains true for the live path: `_record_close` calls
`update_account_stats` **without** those two arguments (`executor.py:831-839`),
so the `is not None` guards at `repository.py:382-387` never fire and both
columns keep whatever paper last left — which in a live-only session is the
schema default `0`.

Three defects in the computation:

**An empty history destroys correct values.** Both functions return `0.0` on a
short series (`risk_manager.py:399-400`, `:418-419`). `0.0 is not None` is
`True`, so the guard at `repository.py:385` passes and the columns are
*overwritten with zero*. Executed: seeded `max_drawdown = 0.1234`,
`sharpe_ratio = 1.75`, closed one position with `balance_history` empty, and both
read back `0.0`. In the shipped repo `balance_history` is **empty** (0 rows), and
`take_balance_snapshot` runs on a 60 s job — so the first nine minutes of any
run, every close zeroes the two columns.

**Sharpe has no variance floor.** `risk_manager.py:424` computes
`np.std(arr, ddof=1)` with only an exact `== 0` guard at `:426`. Executed on five
near-identical snapshots: `sharpe_ratio = -773.4338`. On a genuinely flat equity
curve it diverges, and the HUD renders it verbatim
(`update_callbacks.py:1294-1295`). No annualization, no per-period scaling, no
minimum-variance threshold.

**It is a rolling 500-snapshot window, not an all-time maximum**
(`position_manager.py:513`). At 60 s snapshots that is ~8.3 hours, so a stored
value can *shrink*. The name says "max"; the behaviour says "worst drawdown
currently inside the window".

Two further consequences:

- `_liquidate` never calls this path at all (§5.2).
- The `balance_snapshot` job is bound to the **paper** manager unconditionally
  (`run.py:614-618`), and `_LivePositionManager` has no `take_balance_snapshot`
  by design (`executor.py:920-921`). In live, MDD and Sharpe are computed from
  paper equity — or, since the paper manager never sees a live close, they are
  simply not updated.

### 5.4 Three in-file comments now state the opposite of their code

Each of these sits directly above code that contradicts it. Any document
repeating them repeats a fixed bug:

1. `position_manager.py:314-318` — *"Drawdown dan Sharpe SELALU 0 karena
   `update_account_stats` dipanggil tanpa kedua argumen itu"* — directly above the
   call at `:331-337` that passes both.
2. `trading/live/executor.py:841-846` — *"`SafetyGate.record_realized_pnl`
   (safety.py:415) tidak punya satu pun pemanggil di produksi"* — directly above
   `self.engine.gate.record_realized_pnl(net)` on `:847`.
3. `dashboard/callbacks/update_callbacks.py:1287-1291` — *"`max_drawdown` dan
   `sharpe_ratio` di tabel `account` tidak pernah ditulis oleh jalur mana pun,
   nilainya permanen 0"* — directly above the read of exactly those columns.

### 5.5 Ledger identity and the accounting surface

| Column | Written by | Never written by |
|---|---|---|
| `balance` | `apply_balance_delta` ×4, all in `position_manager.py` (`:102, :105, :128, :298`) | the entire live path (`executor.py:26-30`) |
| `initial_balance` | `init_account` (`repository.py:328-334`), sole caller `paper_engine.py:110` | live |
| `peak_balance` | `bump_peak_balance` (`repository.py:429-435`), sole caller `position_manager.py:312` | live, `_liquidate` |
| `total_pnl`, `total_trades`, `winning_trades`, `losing_trades` | `update_account_stats` (`:373-393`) — callers `position_manager.py:331`, `executor.py:831` | `_liquidate` |
| `profit_factor` | same, `is not None` guarded; both callers convert `inf` → `0` | `_liquidate` |
| `max_drawdown`, `sharpe_ratio` | same, paper only | live, `_liquidate` |

`update_balance` (`repository.py:395`) and `update_account` (`:337`) have **zero
production callers**. That deadness is the only reason `peak_balance` is
monotonic — `bump_peak_balance` uses `MAX(COALESCE(peak_balance,0), ?)`, which has
no lowering branch.

`total_trades` counts **closed positions, not fills**
(`repository.py:578-580` selects `realized_pnl` from `positions`, then `:595`
returns `len(pnls)`). Verified: 3 closes with 6 `trades` rows wrote
`total_trades = 3`. The HUD labels the derived count "FILLS"
(`update_callbacks.py:685`).

`winning_trades` / `losing_trades` split on `p > 0` vs `p <= 0`
(`repository.py:589-590`), so a break-even position books as a loss.

`reset_paper_db.py:17-29` resets nine of ten performance columns — **`sharpe_ratio`
is absent**, so a stale Sharpe survives a "full reset". It also never empties
`balance_history` (no `DELETE FROM balance_history` exists anywhere), so that
table grows 1 440 rows/day without bound.

`profit_factor` is stored as `0.0` when `gross_loss == 0` (both callers convert
`inf`), and the two dashboards render that same `0.0` differently:
`performance.py:57` shows "—", `update_callbacks.py:1327` shows "0.00".

---

## 6. Position state machine

```
                       DecisionAgent.act            decision_agent.py:375-437
                     (quantity ALWAYS set, validated)
                                  │
                                  ▼
                       ExecutionAgent.think          execution_agent.py:96-140
                     (SL/TP from PRE-FILL price — advisory in live)
                                  │
                                  ▼
                       ExecutionAgent.act            execution_agent.py:164
                                  │
              ┌───────────────────┴───────────────────┐
              ▼                                       ▼
        PAPER execute_order                    LIVE execute_order
   paper_engine.py:212                      executor.py:397
              │                                       │
              ▼                                       ▼
        _execute_open :443                        _open :408
   resolve SL/TP from FILL :566-567      3 guards :415,:438,:451
   fee = qty*fill*0.00045 :655                        │
              │                                       ▼
              ▼                              engine.submit_order :204
   validate_trade :665                            ├ gate.can_send :236
              │                                   ├ set_leverage :283   BEFORE the order
              │                                   ├ place_limit_order :296
              ▼                                   └ _attach_protection :338
   PositionManager.open_position :64                     │
     margin = qty*entry/lev :84                         ▼
     liq    = entry*(1-1/lev+0.004) :87          _persist_open :505
     fee    (TAKER) :92                         entry = outcome.avg_price :524
     apply_balance_delta(-(margin+fee)) :102    fill  = outcome.filled_size :523
     insert_position :124 / insert_trade :142   insert_position :554
              │                                 insert_trade :596
              │                                 publish OPENED :609
              ▼
        OPEN  ──────────────────────────────────────┘
              │
   ┌──────────┼───────────────────────────────────────────────┐
   │          │                                               │
   ▼          ▼                                               ▼
 update_positions   _scalp_take_profit      _auto_close_expired   _protect_breakeven
  :402 (0.3 s)      execution_agent:310     execution_agent:262  execution_agent:194
   │                  SCALP_TP                SCALP_EXPIRED        SL → entry ± 0.15%
   ├─ liq?  → _liquidate :460  LIQUIDATED    │
   ├─ SL?   → close_position :433  SL_HIT    │
   └─ TP?   → close_position :439  TP_HIT    │
              │              │              │              │
              └──────────────┴──────┬───────┴──────────────┘
                                    ▼
                     PositionManager.close_position :167
                       claim-once :263  (repository.py:147-169)
                       balance credit :298
                       update_account_stats :331  (mdd + sharpe)
                       publish CLOSED :346
                                    ▼
                                 CLOSED
```

Three properties of the machine that matter for money:

**Claim-once is what makes double-P&L impossible.** `repository.close_position`
is `UPDATE ... WHERE id = ? AND status = 'OPEN'` returning `rowcount > 0`
(`repository.py:160-169`). Four paper close paths and two live close paths
contend for the same row; the losers get `None`/`False` and every caller treats
that as benign rather than as an error (`position_manager.py:263-265`,
`executor.py:815-818`).

**Mark-to-market never carries slippage.** `update_positions` is fed raw prices
and the docstring at `paper_engine.py:1055-1063` is explicit: shifting that price
would make stops "fire" at levels that never existed in the market. Only
execution pays crossing cost.

**The breakeven lock is a silent no-op in live.** `ExecutionAgent.check_positions`
runs `_protect_breakeven` *first* (`execution_agent.py:183`), writing the
breakeven SL to SQLite, and only then calls `engine.check_positions()`
(`:192`). `LiveExecutor.check_positions` then copies the *exchange's* SL/TP back
into the local row (`executor.py:361-365`), reverting the write in the same
cycle, every 0.3 s. No exchange trigger is ever moved by `_protect_breakeven` in
either regime.

---

## 7. Defects found in the money layer

Reproduced by execution against the working tree, not read off a comment.

| # | Defect | Evidence |
|---|---|---|
| D1 | `max_drawdown` / `sharpe_ratio` overwritten with `0.0` when `balance_history` is short or empty | seeded 0.1234 / 1.75 → read back 0.0 / 0.0 after one close with an empty history |
| D2 | Sharpe has no variance floor | 5 near-identical snapshots → `sharpe_ratio = -773.4338` |
| D3 | `_liquidate` writes no account statistics | `account.total_trades` 0 vs `get_trade_stats()` 1 after one liquidation |
| D4 | Liquidation `realized_pnl` omits `fee_open` | `(balance−initial) − SUM(realized) = −10.04500000 − (−10.04066373)`; and `get_daily_realized_pnl` under-reads |
| D5 | Volatility gate prices a 9 bps round trip, true cost is 17 bps | gate beWR 40.00% vs true 44.71% (+4.71) |
| D6 | Gate blind to the order book | at 49 bps half-spread `assess_volatility_gate(0.0025, 0.0060)` returns `None` while the true R:R is negative |
| D7 | Boot validator accepts a `breakeven_offset_pct` of 0.0009 that nets −4.0042 bps | executed: passes validator, exit nets negative |
| D8 | Live SL/TP anchored to the pre-fill price, never re-anchored | `execution_agent.py:139-140` → `executor.py:475-476` → `executor.py:558-559`; no `fill_cost` in `trading/live/` |
| D9 | Batch-close audit log raises `TypeError` when every close loses its claim race | `paper_engine.py:927` sets `fill_meta = None`, `:965` dereferences it; reproduced: `TypeError: 'NoneType' object is not subscriptable` |
| D10 | Tripping the kill switch disables the late-fill protection installer | `engine.py:540-542` + `:634` |
| D11 | TP attach failure is silent; `protected` reports `True` | `engine.py:394-404`, `:347` |
| D12 | Late GTC fills get a ±1% stop and maximum leverage | `engine.py:484-486`, `:501` |
| D13 | Breakeven lock reverted by the live drift repair every cycle | `execution_agent.py:183` then `executor.py:361-365` |
| D14 | Four `FILL_*` constants duplicated in `paper_engine.py:51-54`, shadowing the imports at `:21-26` | grep; values agree today |
| D15 | `reset_paper_db.py` does not reset `sharpe_ratio`; never empties `balance_history` | `reset_paper_db.py:17-29` |

D9 is the sharpest: the one code path whose entire purpose is to record the cost
breakdown of a batch close becomes an exception precisely in the race it exists
to document.

---

## 8. Every way this bot can lose money

### 8.1 Structural (present in every regime)

1. **17 bps of unavoidable round-trip cost against a 25 bps stop.** A naive 1:2.4
   R:R becomes 1:1.24, and the required win rate rises from 41.7% to 44.7% before
   any modelling error.
2. **No funding cost is modelled.** Perpetual funding is a real cost paid on
   positions held across a funding timestamp; it appears nowhere in any P&L.
3. **Impact is a constant 1 bps regardless of size.** Documented as a known
   limitation, not a bug (`fill_cost.py:28-33`): a depth-proportional rate needs
   a division whose zero case is a live crash, and `calculate_scalp_position_size`
   sizes from balance, not depth. Consequence: very large orders are
   *under*-charged.
4. **The 50 bps cap truncates the observed spread, not just the model.** Past
   that point the true cost is unbounded and the log under-reports it.
5. **Sharpe and max-drawdown are computed from a 60 s snapshot series**, so they
   miss intra-window losses entirely and, on an empty table, silently read zero.
6. **Liquidations are invisible to every statistic** and their ledger omits the
   opening fee.
7. **A break-even trade books as a loss** (`p <= 0` at `repository.py:590`),
   depressing win rate and profit factor.

### 8.2 Paper-regime specific

8. **The volatility gate approves economics the true cost model cannot sustain**
   (D5, D6).
9. **The tick-quality guard fails open below 5 samples** — deliberate
   (`paper_engine.py:325-326`), and correct, but it means a fresh symbol trades
   at the floor cost with no median check.
10. **`max_open_positions = 30` is unreachable.** Sizing binds on
    `max_risk_per_trade` first: `margin = min(balance*0.005, balance*0.9/30)` =
    `min(50, 300)` = **50 USDC** at a 10 000 balance. Thirty positions would need
    1 500 USDC of margin, which the 0.9× margin gate forbids. The concentration
    gate that would normally catch a bad symbol never fires.
11. **`account.balance` conflates free cash with equity for the dashboard.**
    `wallet_balance = account["balance"] + SUM(open margin)`
    (`update_callbacks.py:672`, identically at `:140` and `:188`). In live mode
    that adds *live* margin to a *paper* cash balance.
12. **The batch-close audit path crashes** (D9) instead of reporting.

### 8.3 Live-regime specific

13. **Seven paper-side risk mechanisms have no live implementation** (§2.3),
    including `max_open_positions`, the drawdown cap and the %-of-initial daily
    breaker.
14. **Live SL/TP sit ~16% wider than configured** (D8) — and the entry crossing
    is not modelled anywhere on that path.
15. **The kill switch is one-way and disabling the protection installer** (D10).
16. **A missing TP trigger reports `protected: True`** (D11).
17. **A late GTC fill opens at 4× the planned risk at maximum leverage** (D12).
18. **`account.balance` is never written**, so the HUD's wallet/equity/P&L tiles
    in live mode are a hybrid of two ledgers and MDD/Sharpe render `N/A` forever.
19. **`emergency_flat` writes no audit trail** — a flattened position leaves a
    row `OPEN` and no trade record.
20. **`LiveEngine.reconcile` and `emergency_flat` have zero production callers**,
    so two of the seven kill-switch raisers are dead. Drift detection rests
    entirely on `health_check`, which does run.
21. **The breakeven protection is a no-op** (D13) — the operator believes a
    green position is protected when no trigger has moved.
22. **Gate rejections are counted as exchange errors nowhere**, which is correct,
    but it also means `TOO_MANY_ERRORS` only ever counts genuine exchange
    failures — and three genuine-but-safe refusals would not trip it, so a
    chronically refusing system never stops.

### 8.4 Config and operational

23. **A missing `config.yaml` changes the entire risk profile silently-ish.**
    Now loud (`core/config.py:557-620` warns *and* prints a stderr block), but the
    dataclass defaults are 4× the per-trade risk (0.02 vs 0.005), half the daily
    loss budget, half the drawdown budget, 10× fewer positions, and a 20×
    slower decision loop — and `fees.taker = 0.0005` instead of `0.00045`, so the
    P&L shifts too.
24. **`run.py:291` discards every live limit the operator set in the TUI.**
    `console.edit_rules` mutates `get_config().live` in memory; `_build_live_executor`
    builds a bare `LiveConfig()`. Effective live limits are dataclass defaults.
25. **The cost constants are duplicated** (D14) and can drift.
26. **`price_is_final` and `describe_cost` are dead surface** — untested escape
    hatches that a future caller might use without knowing they are unused.

---

## 9. Limits that are defined but cannot fire

| Limit | Defined at | Why it cannot fire |
|---|---|---|
| `max_open_positions = 30` (paper) | `config.yaml:41`, `risk_manager.py:303` | sizing binds at 50 USDC/trade; 30 positions need 1 500 USDC, blocked by the 0.9× margin gate |
| `kelly_criterion` | `risk_manager.py:380` | zero callers repo-wide |
| `LiveEngine.reconcile` | `engine.py:74` | only caller is `emergency_flat` (`engine.py:728`), which itself has only test callers |
| `emergency_flat` kill switch | `engine.py:737` | inside the uncalled `emergency_flat` |
| reconcile kill switch | `engine.py:128` | inside `reconcile`, also needs `not cfg.auto_reconcile` |
| `Blocker.RECONCILIATION_FAILED` | `safety.py:45` | never appended to any blockers list; a mismatch surfaces as `KILL_SWITCH` with the real cause hidden |
| `LiveConfig.use_exchange_side_tpsl` | `config.py:349` | zero readers; `_attach_protection` always attaches |
| `LiveConfig.reconciliation_tolerance_days` | `config.py:358` | zero readers |
| `LiveConfig.testnet` | `config.py:313` | zero readers; endpoint selection is correct anyway (`run.py:300` passes the flag directly) |
| `LiveConfig.enabled` | `config.yaml` → force-written `False` at `config.py:700` | never read by the gate at all; env vars are the only route |
| `LiveExchange.cancel` | `client.py:462` | zero callers; no production path ever cancels a stale trigger |
| `LiveExchange.cancel_all` | `client.py:483` | only from the uncalled `emergency_flat` |
| tick-quality guard (live) | `paper_engine.py:280` | no live caller |
| `assess_volatility_gate` (live) | `volatility.py:347` | no live caller |
| `validate_trade` (live) | `risk_manager.py:273` | no live caller — so no `max_open_positions`, no drawdown cap, no %-daily-loss in live |
| dynamic TP/SL floor re-imposition (live) | `paper_engine.py:374-386` | no live caller |
| `_liquidate` | `position_manager.py:460` | liq distance 960/460/160 bps at 10×/20×/50× vs stops of 25/150/200 bps; crossover needs lev > 154 |
| `price_is_final=True` branch | `position_manager.py:213` | zero callers |
| `describe_cost` | `fill_cost.py:207` | zero callers |
| `batch_close_positions` | `position_manager.py:374` | no production caller |
| `max_drawdown` / `sharpe_ratio` (live) | `executor.py:831-839` | not passed; stay at the schema default `0` |
| MDD/Sharpe on a liquidation | `position_manager.py:460-511` | `_liquidate` never calls `update_account_stats` |
| console `edit_rules` / `check_consistency` output | `console.py:587`, `:532` | discarded at `run.py:291` |

---

## 10. Measured profit factor

Read from the working tree's own paper ledger, `data_store/trading_bot.db`,
using exactly the query `Repository.get_trade_stats` uses
(`repository.py:576-604`):

```
closed positions (status IN 'CLOSED','LIQUIDATED')   3
wins (realized_pnl > 0)                               2
losses (realized_pnl <= 0)                            1
gross_profit                                          0.073753 USDT
gross_loss                                            0.120436 USDT
profit_factor                                         0.6123820969277232
win_rate                                             0.666667
total_pnl                                            -0.046683 USDT
avg_pnl                                              -0.015561 USDT
```

`account.profit_factor` in the same file agrees exactly (0.6123820969277232).

**A profit factor below 1.0 means the strategy loses money per unit of risk, at
its current cost model.** A 66.7% win rate is *above* the 44.7% break-even
threshold from §4.3 — and still loses. The reason is visible in the two numbers:
gross profit 0.0738 across two winners is 0.0369 each, while the single loss is
0.1204, more than three times a typical win. That is exactly the signature the
cost arithmetic predicts: the target pays 47 bps net, the stop costs 38 bps net,
and realized R:R is 1:1.24, so a 66.7% win rate nets positive on *count* but the
single loss dominates.

**Caveat, stated plainly: n = 3.** Three closed positions cannot establish an
edge, and the 66.7% win rate is not statistically distinguishable from a coin
flip. The number is reported because it is what the repository's own ledger
holds, not because it is meaningful. What *is* meaningful is the arithmetic in
§4.3, which is derived from the source and does not depend on the sample: at 17
bps of round-trip cost against a 25 bps stop, the strategy must win 44.7% of the
time just to break even, and the observed loss size relative to win size is
consistent with a genuine R:R shortfall rather than with variance.

Also note the sample is drawn from a session where `balance_history` is empty
(0 rows), which is precisely the condition that zeroes `max_drawdown` and
`sharpe_ratio` on every close (D1) — the account row confirms both are `0.0`.

---

## 11. Not verified

- **Live economics.** No live order has ever been placed from this tree
  (`TRADEBOT_LIVE` and `TRADEBOT_LIVE_CONFIRMED` are set by nothing outside
  tests). Every live figure above is arithmetic from the source, not an
  observation.
- **Real fill quality.** All cost figures use the floor path (no order book in
  `market_store` at measurement time). The book-dependent branch
  (`fill_cost.py:145-154`) was exercised only by synthetic construction, not
  against a live Hyperliquid book.
- **Whether the ATR path ever produces `used_dynamic=True` in production.**
  `analysis/volatility.py:183` requests `period+1` candles; whether the registered
  candle source returns that many was not exercised end to end here. When it
  does not, SL/TP silently fall back to the static 0.25%/0.60% pair and the
  volatility gate sees the same pair.
- **The 17 bps figure assumes a mid-market reference.** If the reference price is
  itself already bid- or ask-side, the realized crossing differs by one half of
  the observed spread.
- **Sharpe and max-drawdown values over a populated `balance_history`.** The
  empty-history case is verified; the populated case was checked only on
  synthetic snapshots.
- **Test count**: 569 collected, re-derived by running
  `python -m pytest --collect-only -q` (569 tests collected), `OK (skipped=4)`.
  The stale `566` in `docs/context/CONTEXT.md` is wrong.

---

## 12. Corrections to `docs/context/CONTEXT.md`

Stated explicitly, as required.

| CONTEXT.md claim | Status | Ground truth |
|---|---|---|
| "SafetyGate.record_realized_pnl has zero production callers" | **WRONG** | one caller: `trading/live/executor.py:847`, inside `_record_close`, immediately after the `update_account_stats` write. It is the only feed for `Blocker.DAILY_LOSS_LIMIT`. The in-source comment at `executor.py:841-846` that repeats the stale claim is itself wrong. |
| "max_drawdown and sharpe_ratio are never written" | **WRONG for paper, true for live** | paper: `trading/position_manager.py:326-343`. live: omitted at `executor.py:831-839`. |
| "Liquidation under-reports realized loss / records realized_pnl = -margin and fee = 0" | **WRONG** | `trading/position_manager.py:479-480` charges the closing fee and the exit crossing. A *different*, smaller understatement remains: `fee_open` is not deducted (§5.2). |
| "LiveExecutor writes nothing to the Repository and publishes nothing to the EventBus" | **WRONG** | `_persist_open` writes `positions`/`trades` (`executor.py:554`, `:596`); `_record_close` writes both (`executor.py:813`, `:820`); publishes `POSITION_UPDATE` and `TRADE_EXECUTED` (`executor.py:609`, `:622`, `:849`). |
| "566 collected test cases" | **WRONG** | 569. |
| Any file inventory / line count in this slice | **STALE** | re-measured: `trading/risk_manager.py` 429, `trading/position_manager.py` 577, `trading/fill_cost.py` 216, `trading/paper_engine.py` 1113, `trading/live/safety.py` 423, `trading/live/executor.py` 1061, `trading/live/engine.py` 757, `core/config.py` 1030, `config.yaml` 214. |
| "the one-time SL/TP is anchored to the fill" applied to both regimes | **WRONG for live** | paper re-anchors (`paper_engine.py:566-567`); live does not (§4.6). |
