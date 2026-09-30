# 02 — Data Flow and Runtime

Complete trace from a Hyperliquid WebSocket frame to a rendered HUD number. Every hop names the
exact function and `file:line`. Paper and live are traced side by side; divergence and rejoin
points are marked.

Line counts re-derived from the working tree with `[System.IO.File]::ReadAllLines`, not carried
over from `docs/context/CONTEXT.md`. Test count re-derived by running `python -m pytest
--collect-only -q` → **569 collected**. `CONTEXT.md` says 566; that is stale.

---

## 0. Corrections to the stale document

Five claims in `docs/context/CONTEXT.md` were checked against source and are wrong.

| Stale claim | Ground truth |
|---|---|
| `SafetyGate.record_realized_pnl` has zero production callers | It has exactly one: `trading/live/executor.py:847`, inside `_record_close`, called AFTER `update_account_stats` and BEFORE the `POSITION_UPDATE` publish. It is the only feed for `Blocker.DAILY_LOSS_LIMIT`, evaluated at `trading/live/safety.py:293`. The in-source comment at `executor.py:841-846` asserting the opposite is itself stale. | `trading/live/executor.py:841-847`; `trading/live/safety.py:293` |
| `LiveExecutor` implements 2 of the 6 members `ExecutionAgent` calls | It implements **6 of 6**. See the six-member table in §2.1. (`tests/test_live_executor.py:208-217` still only checks 2 — the test, not the class, is what the old number described.) |
| `Order.quantity` is `None` and becomes `size 0.0` | `DecisionAgent.act` always computes, validates and assigns it before publishing: `agents/decision_agent.py:397`, `:407-423`, `:432-436`. The `size=order.quantity or 0.0` fallback is gone. |
| `max_drawdown` / `sharpe_ratio` are never written | They are written on the paper path at `trading/position_manager.py:325-343`. Still unwritten on the live path — `trading/live/executor.py:831-839` omits both, so the HUD renders `N/A` in live. |
| 566 collected test cases | **569** (`python -m pytest --collect-only -q`). |

---

## 1. Hop 1 — market data in

Shared by paper and live. No divergence here.

```
data/hyperliquid_feed.py:517   websockets.connect(HL_WS_URL, open_timeout=10,
                                ping_interval=None, max_size=WS_MAX_SIZE)
      HL_WS_URL       = "wss://api.hyperliquid.xyz/ws"   (:47)
      WS_MAX_SIZE     = 2 ** 22                          (:50)
      WS_PING_INTERVAL= 30.0   (application-level {"method":"ping"})  (:53)
      WS_RECV_TIMEOUT = 60.0                             (:56)

data/hyperliquid_feed.py:513   self._ensure_native_kernel()   ← ONCE, before frame #1.
                                 guarded by self._native_kernel flag (:112)
                                 → core/microstructure.py:309 initialize_native_kernel

data/hyperliquid_feed.py:533   raw = await asyncio.wait_for(ws.recv(), timeout=WS_RECV_TIMEOUT)
data/hyperliquid_feed.py:534   await asyncio.to_thread(self._handle_message, raw)
                                ← JSON parsing OFF the event loop (docstring :497-505)

data/hyperliquid_feed.py:411   _handle_message(raw)
      :421  json.loads(raw)
      :432  channel == "allMids"       → :439 market_store.set_price(symbol, px)
      :444  channel == "l2Book"        → :451 coin_to_symbol
                                       → :452 _parse_book (:313)
                                       → :453 market_store.set_order_book(symbol, book)
                                       → :458 microstructure.ingest_l2(symbol, bids, asks)
                                            ← the streaming write path into the kernel; a SECOND production writer exists: `agents/direction_agents.py:125` calls `calculate_order_flow_imbalance(book, symbol=symbol)`, which re-ingests the same book via `analysis/probability_engine.py:89 microstructure.ingest_l2(symbol, bids, asks)`. (The third call site, `update_callbacks.py:1311`, omits `symbol` and uses a throwaway `PythonKernel`, so it does not touch the process kernel.)
      :461  channel == "candle"        → :467 market_store.set_live_candle
                                            (also set_price with close, market_store.py:196)
      :469  channel == "activeAssetCtx"→ :479 set_funding  :481 set_open_interest
      :485  channel == "trades"        → :490 market_store.set_recent_trades

Reconnect: :548  backoff = min(backoff * 2.0, 30.0)   (initial backoff = 1.0, :508)
```

`HyperliquidFeed.__init__` accepts an `event_bus` (`data/hyperliquid_feed.py:84`) and **never
publishes on it**. The WebSocket path writes to `market_store` and the kernel only.

### 1.1 REST safety net

```
run.py:879            asyncio.create_task(self.price_feed.price_update_loop())
data/price_feed.py:566 price_update_loop()
      :581  mids = await self.hyperliquid.fetch_all_mids()      (one request, whole market)
      :587  market_store.set_price(sym, fpx); self._last_prices[sym] = fpx
      :594  missing = [s for s in symbols
                       if market_store.get_price(s) is None
                       or (market_store.get_price_age(s) or 0) > 10.0]
      :600  await self.fetch_ticker(symbol);  :601 sleep 0.15
      :605  await asyncio.sleep(1.5)
data/price_feed.py:395   market_store.set_ticker(symbol, ticker)
data/price_feed.py:400   event_bus.publish(Channels.PRICE_UPDATE, {...}, source="price_feed")
```

### 1.2 Candle persistence

| Writer | Period | Limits | Writes |
|---|---|---|---|
| `run.py:440` `_prefetch_historical_candles` | once at boot | `1m` 240, `5m`/`1h` 120 | `candles` |
| `run.py:717` `_candle_refresh_loop` | `sleep(30)` at `:743` | `1m` 300 (`:735`), `5m` 120 (`:736`) | `candles` |
| `run.py:740` `refresh_volatility_cache` | same loop | `1m` limit 30 per symbol (`paper_engine.py:180`) | `_candle_cache` only |

Candle writes are UPSERT, never INSERT OR IGNORE: `database/repository.py:52-57`
`ON CONFLICT(symbol, timeframe, timestamp) DO UPDATE SET`. The OHLC invariant is double-enforced —
`data/price_feed.py` before building `Candle`, and `database/repository.py:_valid_candle:32-34`
for every caller.

---

## 2. Hop 2 — decision

Shared up to the engine boundary.

```
agents/direction_agents.py:437  run_cycle()
      :443  snapshot = await self._evaluate_symbol(symbol)
      :445  await repo.insert_direction_snapshot(snapshot)     ← ONE ROW PER SYMBOL PER CYCLE
agents/direction_agents.py:464  _evaluate_symbol
      :474  await spec.evaluate_async(symbol)      (TechnicalAgent → thread, :473)
      :476  spec.evaluate(symbol)                  (orderflow, momentum, microstructure)
      :478  result = aggregate(verdicts, self.ensemble)
      :485  diffusion = probability_engine.compute_directional_curve(...)

agents/analysis_agent.py:188    publish(Channels.MARKET_ANALYSIS, {...})   (every 15 s)

agents/decision_agent.py:273    act(analysis)              (every 3 s, config.yaml)
      :282  account = await repo.get_account()               ← read ONCE per cycle
      :376  sizing = risk_manager.calculate_scalp_position_size(balance, price, leverage)
      :397  quantity = float(sizing["quantity"])
      :407  problems = []
      :408  not math.isfinite(quantity) or quantity <= 0        → problem
      :410  not math.isfinite(stop_loss) or stop_loss <= 0      → problem
      :412  not math.isfinite(take_profit) or take_profit <= 0  → problem
      :414  LONG  and not (stop_loss < price < take_profit)     → problem
      :419  SHORT and not (take_profit < price < stop_loss)     → problem
      :425  if problems: continue        ← order NEVER published
      :432  order.quantity     = quantity
      :433  order.leverage     = leverage
      :434  order.stop_loss    = stop_loss
      :435  order.take_profit  = take_profit
      :436  order.price        = price
      :439  publish(Channels.TRADE_DECISION, {"order": order, "decision": decision,
                                                "stop_loss_pct": ..., "take_profit_pct": ...,
                                                "quantity": ..., "entry_price": ...})
```

`agents/decision_agent.py:473` reads `repo.get_latest_direction_snapshot(symbol)` — the **same
row** `dashboard/callbacks/update_callbacks.py:799-805` reads. What the trader sees and what the
bot trades cannot diverge. Freshness gate at `:477` via `_snapshot_is_fresh` (`:509`).

### 2.1 Execution agent — the six engine members

`ExecutionAgent` touches exactly six members of `self.engine`. This is the whole adapter
contract; both engines satisfy all six.

| Member | Touched at | `PaperTradingEngine` | `LiveExecutor` |
|---|---|---|---|
| `update_price` | `execution_agent.py:64, 71, 100` | `paper_engine.py:186` | `executor.py:210` (writes `_last_prices`, `:234-235`) |
| `get_price` | `execution_agent.py:96` | `paper_engine.py:190` | `executor.py:237` |
| `execute_order` | `execution_agent.py:164` | `paper_engine.py:212` | `executor.py:397` |
| `check_positions` | `execution_agent.py:192` | `paper_engine.py:1049` | `executor.py:259` |
| `_last_prices` | `execution_agent.py:213, 266, 290` | `paper_engine.py:99` | `executor.py:110`, written `:235` |
| `position_manager` | `execution_agent.py:270, 311` | `paper_engine.py:92` (`PositionManager`) | `executor.py:115` (`_LivePositionManager`, class at `:912`) |

`ExecutionAgent` internals:

```
:46   initialize()    subscribe TRADE_DECISION (:48) and PRICE_UPDATE (:49)
:52   sense()         drain PRICE_UPDATE   → engine.update_price (:64)
                      drain market_store.get_all_prices() → engine.update_price (:69-71)
                      drain TRADE_DECISION → pending_orders (:74-79)
:83   think()         price = engine.get_price(order.symbol)              (:96)
                      fall back to market_store + update_price            (:98-100)
:117                    from analysis import volatility as vol_mod        (LAZY import)
:119                    thresholds = vol_mod.get_dynamic_tp_sl_thresholds(symbol, config)
                          ADVISORY ONLY — comment :105-116 says the engine overwrites these
:139                    order.stop_loss   = risk_manager.calculate_stop_loss(price, side, sl_pct)
:140                    order.take_profit = risk_manager.calculate_take_profit(price, side, tp_pct)
:158  act()            result = await self.engine.execute_order(order)    (:164)
:177  check_positions()  :183 _protect_breakeven
                          :186 _scalp_take_profit
                          :189 _auto_close_expired
                          :192 await self.engine.check_positions()      ← LAST, so drift repair wins
```

---

## 3. THE DIVERGENCE

One line region decides everything: `run.py:396-399`.

```
run.py:396   executor = self.paper_engine
run.py:397   if self.mode in ("testnet", "mainnet"):
run.py:398       executor = await self._build_live_executor()
run.py:399   self.executor = executor
run.py:401   self.execution_agent = ExecutionAgent(self.event_bus, executor)
```

Live construction order is fixed and documented at `run.py:270-281`:

```
run.py:291   live_cfg = LiveConfig()                  ← BARE dataclass, NOT config.yaml
run.py:292   key = os.environ.get(live_cfg.private_key_env)      → RuntimeError if empty (:294)
run.py:302   gate = SafetyGate(live_cfg)                            ← gate FIRST
run.py:303   exchange = LiveExchange(key, testnet=self.mode=="testnet", account_address=api_wallet)
run.py:305   engine = LiveEngine(gate=gate, exchange=exchange, cfg=live_cfg)
run.py:307   health = await engine.health_check()                  → RuntimeError if not ok (:309)
run.py:321   self._live_task = asyncio.create_task(engine.run_loop(interval=5.0, on_decision=_decide))
run.py:328   return LiveExecutor(engine, self.event_bus)
```

`self._live_task` is **not** appended to `self._background_tasks` (`run.py:894-896`), so
`shutdown()` (`:924-925`) never cancels `LiveEngine.run_loop`. It outlives shutdown.

### 3.1 Divergence table

| Concern | Paper | Live | Citation |
|---|---|---|---|
| Fill price | `fill_price_after_cost` (crossing cost) | exchange `avg_price`, no cost model | `paper_engine.py:562` / `executor.py:721-722` |
| SL/TP source | recomputed from the real fill | computed pre-fill by the agent, passed verbatim | `paper_engine.py:566-567` / `execution_agent.py:139-140` → `executor.py:475-476` |
| Volatility gate | `assess_volatility_gate` runs | never runs (zero callers in `trading/live/`) | `paper_engine.py:396` |
| Tick-quality guard | `_tick_quality_guard` runs | no counterpart | `paper_engine.py:280, :587` |
| `validate_trade` | runs (margin, count, daily-loss, drawdown) | no counterpart | `paper_engine.py:665` |
| `account.balance` | mutated by `apply_balance_delta` ×4 | **never** written | `repository.py:403`; call sites all in `position_manager.py:102,105,128,298` |
| `max_drawdown` / `sharpe_ratio` | written | omitted → HUD shows `N/A` | `position_manager.py:336-337` / `executor.py:831-839` |
| Mandatory admission gate | none (local risk only) | `SafetyGate.can_send`, 9 master + 8 per-order | `engine.py:236` → `safety.py:347` |
| Reconciliation | none | `health_check` every 5 s + `check_positions` throttled to 2 s | `engine.py:518,629` / `executor.py:57,277` |

---

## 4. Sequence diagram — one tick of the execution loop

```
run.py:_execution_loop            (async task created at run.py:881)
 │  _EXEC_TICK_SECONDS = 0.3                                run.py:81
 │
 └─ while self._running:                                     run.py:649
     ├─ try:                                                 run.py:650
     │   1. await self.execution_agent.run_cycle()           run.py:652
     │        ExecutionAgent.sense()      :52   drain PRICE_UPDATE, TRADE_DECISION
     │        ExecutionAgent.think()      :83   set order.stop_loss/take_profit (:139-140)
     │        ExecutionAgent.act()        :158  engine.execute_order(order)  (:164)
     │                                           │
     │                                           ├─ PAPER: paper_engine.py:212
     │                                           └─ LIVE : executor.py:397
     │        base_agent._log_cycle()   :119   INSERT agent_logs
     │
     ├─ 2. await self.execution_agent.check_positions()    run.py:654
     │        _protect_breakeven   :183  repo.update_position_sl_tp (:227 LONG / :237 SHORT)
     │        _scalp_take_profit  :186  position_manager.close_position(..., "SCALP_TP") (:311)
     │        _auto_close_expired :189  position_manager.close_position(..., "SCALP_EXPIRED") (:270)
     │        engine.check_positions()
     │             PAPER  paper_engine.py:1065  prices = market_store.get_all_prices()
     │                                          prices.update(self._last_prices)   (:1066)
     │                                          position_manager.update_positions(prices) (:1070)
     │             LIVE   executor.py:276  throttle: now - _last_refresh < 2.0 → return (:277)
     │                                            :285 all_mids()  (ONE to_thread read)
     │                                            :349 repo.update_position_pnl
     │                                            :362 repo.update_position_sl_tp  ← exchange → local
     │
     ├─ except asyncio.CancelledError:  log debug + re-raise     run.py:655-668
     ├─ except Exception as e:                                  run.py:669
     │      _exec_failures += 1                                          :670
     │      <= _EXEC_FAIL_TRACE_FIRST (3)  → logger.exception (full TB)  :671-679
     │      == _EXEC_FAIL_CRITICAL (100)   → logger.critical (ONCE)       :680-696
     │      % _EXEC_FAIL_LOG_EVERY (50)==0  → logger.error (one-line)     :697-703
     ├─ else: any clean iteration → _exec_failures = 0                     :704-709
     └─ await asyncio.sleep(_EXEC_TICK_SECONDS)   ← OUTSIDE the try, run.py:715
```

The counter is **consecutive**, not cumulative (`else` at `:704-709`). Exactly one of the three
tiers fires per failure — `==` at `:680`, not `<=`. The sleep at `:715` is deliberately outside the
`try`: inside, a failing iteration would skip the delay and hot-spin on the same exception.

---

## 5. One complete position lifecycle (open → SL close)

### 5.1 Paper

```
DecisionAgent.act                      decision_agent.py:432  quantity/leverage/sl/tp set
  └─ publish TRADE_DECISION                                :439
       └─ ExecutionAgent.sense drains queue               execution_agent.py:74-79
            └─ act → engine.execute_order(order)                       :164
                 PaperTradingEngine.execute_order         paper_engine.py:212
                 :222  price = self.get_price(order.symbol)
                 :227  action OPEN_LONG/OPEN_SHORT → _execute_open(order, price)
                      _execute_open                              :443
                 :446  account = await repo.get_account()
                 :451  side = "LONG" if OPEN_LONG else "SHORT"
                 ── input guard ────────────────────────────────────────
                 :472  qty is not None and qty <= 0        → problem
                 :474  lev is None / :476 lev <= 0         → problem
                 :478  price is None or price <= 0         → problem
                 :484  INSERT agent_logs (TRADE_REJECTED)      on any problem
                 ── ATR targets + volatility gate ─────────────────────
                 :520  sl_pct, tp_pct, vol_meta = self._resolve_tp_sl(symbol, order)
                         :372  vol_mod.get_dynamic_tp_sl_thresholds(symbol, config)
                         :375  sl_pct = max(dynamic, scalping.tight_sl_pct)   (0.0025)
                         :376  tp_pct = max(dynamic, scalping.fast_tp_pct)     (0.0060)
                         :386  tp_pct = max(tp_pct, sl_pct * min_risk_reward) (1.5)
                         :396  rejection = vol_mod.assess_volatility_gate(...)
                         :399  raise VolatilityGateError(rejection, meta)
                 :523  except VolatilityGateError → agent_log (:531) → return failure (:541)
                 ── crossing cost, applied to the price itself ──────────
                 :561  fill_side = "BUY" if side == "LONG" else "SELL"
                 :562  price, fill_meta = self._fill_price(order.symbol, fill_side, price,
                                                  reason="OPEN")
                          paper_engine.py:272 → fill_cost.fill_price_after_cost
                 :566  stop_loss   = risk_manager.calculate_stop_loss(price, side, sl_pct)
                 :567  take_profit = risk_manager.calculate_take_profit(price, side, tp_pct)
                        ← SL/TP re-anchored to the FILL, not the pre-fill reference (:498-516)
                 ── tick quality ──────────────────────────────────────────
                 :587  rejection = self._tick_quality_guard(symbol, fill_meta["ref_price"], sl_pct)
                          :311  age > scalping.max_tick_age_seconds (1.5 s)      → reject
                          :323  history = market_store.get_price_history(symbol, 3.0)
                          :325  fewer than stale_tick_min_samples (5) → fail-OPEN
                          :328  median = statistics.median(samples)
                          :332  deviation = abs(price - median)/median
                          :333  deviation > sl_pct                                 → reject
                 :595  agent_log TRADE_REJECTED on rejection
                 ── sizing + risk gate ────────────────────────────────────
                 :616  if order.quantity is None:   ← DEAD in production (DecisionAgent :432)
                 :655  estimated_fee = risk_manager.calculate_fee(quantity, price, "TAKER")
                 :656  required_cash = margin + estimated_fee
                 :663  daily_pnl = await repo.get_daily_realized_pnl()
                 :665  validation = risk_manager.validate_trade(balance=free, margin_required=,
                                    open_positions, daily_pnl, peak_balance, equity)
                 :680  agent_log TRADE_REJECTED with the full input snapshot
                 ── commit ─────────────────────────────────────────────────
                 :698  position_id = await self.position_manager.open_position(...)
                        PositionManager.open_position       position_manager.py:64
                        :84   margin = Decimal(qty)*Decimal(entry)/Decimal(lev)
                        :87   liq_price = risk_manager.calculate_liquidation_price(...)
                        :92   fee = risk_manager.calculate_fee(qty, entry, "TAKER")
                        :102  free_balance = await repo.apply_balance_delta(-(margin + fee))
                        :105  if free_balance < 0: apply_balance_delta(+(margin+fee)); return None
                        :124  row_id = await repo.insert_position(position)
                        :128  if position_id is None: apply_balance_delta(+(margin+fee))
                        :142  await repo.insert_trade(Trade(trade_type="OPEN", side=BUY/SELL))
                        :145  event_bus.publish(Channels.POSITION_UPDATE, {"action":"OPENED",...})
                 :754  agent_log TRADE_EXECUTED with the FULL cost breakdown
                       (ref_price, fill_price, half_spread_pct, impact_pct, total_cost_pct,
                        cost_source, book_age_s)
                 :779  event_bus.publish(Channels.TRADE_EXECUTED, {"action":"OPEN",...})

… 300 ms × N …  ExecutionAgent.check_positions → engine.check_positions

paper_engine.py:1049 check_positions
  :1065  prices = market_store.get_all_prices()
  :1066  prices.update(self._last_prices)      ← engine cache wins over the store
  :1070  await self.position_manager.update_positions(prices)

PositionManager.update_positions                       position_manager.py:402
  :419  pnl_info = risk_manager.calculate_pnl(side, entry_price, current_price, quantity)
  :422  await repo.update_position_pnl(pos["id"], pnl_info["pnl"])
  :425  if _should_liquidate(pos, current_price):  → _liquidate      :427   (see §6)
  :431  if pos["stop_loss"] and _sl_hit(pos, current_price):
  :433      await self.close_position(pos["id"], current_price, CloseReason.SL_HIT)
  :437  if pos["take_profit"] and _tp_hit(pos, current_price):
  :439      await self.close_position(pos["id"], current_price, CloseReason.TP_HIT)

PositionManager.close_position                         position_manager.py:167
  :202  positions = await repo.get_open_positions(); find pos by id
  :210  if not pos: return None
  :213  if price_is_final:  fill_price = close_price          ← ZERO CALLERS repo-wide
  :216  else: fill_price, fill_meta = close_fill_price(pos["symbol"], pos["side"],
                                     close_price, reason=reason, config=self.config)
              fill_cost.py:196  fill_side = "SELL" if position_side=="LONG" else "BUY"
  :227  pnl_info = risk_manager.calculate_pnl(pos["side"], pos["entry_price"], fill_price, qty)
  :235  fee = risk_manager.calculate_fee(pos["quantity"], fill_price, "TAKER")   ← FROM THE FILL
  :251  for t in await repo.get_trades_by_position(position_id):
  :252      if t["trade_type"].upper() == "OPEN": open_fee = t["fee"]; break   ← READ BACK, never recomputed
  :257  net_pnl = pnl_info["pnl"] - open_fee - fee
  :263  claimed = await repo.close_position(position_id, fill_price, net_pnl, reason)
          repository.py:160-169  UPDATE … WHERE id = ? AND status = 'OPEN'
          rowcount > 0 is the CLAIM.  :264  if not claimed: return None
  :278  await repo.insert_trade(Trade(side="SELL" if LONG else "BUY", trade_type="CLOSE"))
  :297  returned = pos["margin"] + net_pnl + open_fee       ← margin + net + opening-fee refund
  :298  new_balance = await repo.apply_balance_delta(returned)     ← ATOMIC
  :312  await repo.bump_peak_balance(new_balance + remaining_margin)
  :325  equity_series = await self._get_equity_series()      (limit=500, :513)
  :326  max_dd = risk_manager.calculate_max_drawdown(equity_series)     risk_manager.py:397
  :327  sharpe = risk_manager.calculate_sharpe_ratio(_equity_returns(series))  :414
  :331  await repo.update_account_stats(total_pnl, total_trades, winning_trades,
                  losing_trades, max_drawdown=max_dd, sharpe_ratio=sharpe, profit_factor=...)
  :346  event_bus.publish(Channels.POSITION_UPDATE, {"action":"CLOSED", "realized_pnl": net_pnl})
  :365  return {"realized_pnl", "fee", "gross_pnl", "roe_pct", "fill_price", "fill_meta"}

paper_engine.py:854  agent_log TRADE_CLOSED with the full cost breakdown
```

### 5.2 Live

```
DecisionAgent.act   →  ExecutionAgent.act → executor.py:397 execute_order → :408 _open

LiveExecutor._open                                  executor.py:408
  :415  order.stop_loss is None or order.take_profit is None → refuse, blockers=["missing_tpsl"]
  :438  if not _finite_positive(order.quantity)             → refuse, ["invalid_quantity"]
  :451  if not isinstance(lev,int) or isinstance(lev,bool) or lev < 1
                                                            → refuse, ["invalid_leverage"]
  :464  price = self._price_for(order)   (:887, order.price else market_store, else ValueError)
  :469  result = await self.engine.submit_order(coin, symbol, is_buy, size, price,
                                                stop_loss, take_profit, leverage, cloid)

LiveEngine.submit_order                    trading/live/engine.py:204  — NOT REORDERABLE
  :235  context = await self._remote_context(coin)      (:188, 3 exchange reads in one to_thread)
  :236  allowed, blockers = self.gate.can_send(request, free_collateral=…, current_exposure=…,
                       symbol_exposure=…, now=self.now_fn(), cloid=cloid, leverage=leverage)
             safety.py:347  →  master_blockers  :247   (9 checks)
                    :261  COUNTER_STATE_UNREADABLE   :266  LIVE_DISABLED (env)
                    :271  NOT_CONFIRMED (env)         :277  KILL_SWITCH
                    :283  MISSING_KEY                 :287  OUTSIDE_WINDOW
                    :291  DAILY_ORDER_LIMIT           :293  DAILY_LOSS_LIMIT
                    :295  TOO_MANY_ERRORS
             →  order_blockers  :300   (8 checks)
                    :319 size<=0  :321 price<=0  :327 MISSING_CLOID (opening only)
                    :332 LEVERAGE_TOO_HIGH  :337 ORDER_TOO_LARGE  :339 POSITION_TOO_LARGE
                    :341 EXPOSURE_TOO_LARGE  :343 COLLATERAL_TOO_LOW
  :245  if not allowed: return {"success": False, "blockers": [...]}
  :255  if not is_close and (stop_loss is None or take_profit is None) → "missing_tpsl"
  :283  await asyncio.to_thread(self.exchange.set_leverage, coin, leverage, True)
        ← LEVERAGE BEFORE THE FIRST ORDER (:278-281).  Exception → record_error (:287), return.
  :296  outcome = await asyncio.to_thread(send)         # place_limit_order (client.py:372)
        exception → record_error (:298);  not outcome.ok → record_error (:305)
  :311  self.gate.record_success();  :312  self.gate.record_order_sent()
  :314  if is_close: self.positions.pop(symbol, None); return {"success":True, "outcome":…}
  :326  if outcome.filled_size <= 0: return {"success":True, "protected":False}
        ← RESTING: NO TRIGGER ORDERS ATTACHED (:322-325)
  :338  position = await self._attach_protection(coin, symbol, "LONG" if is_buy else "SHORT",
                                                outcome.filled_size, outcome.avg_price or price,
                                                stop_loss, take_profit, leverage)

LiveEngine._attach_protection                engine.py:351
  :378  is_close_buy = side == "SHORT"           ← trigger direction INVERTED from opening
  :382  place_trigger_order(is_buy=is_close_buy, size, trigger_price=stop_loss,  tpsl="sl",
                            reduce_only=True)
  :386  place_trigger_order(is_buy=is_close_buy, size, trigger_price=take_profit, tpsl="tp",
                            reduce_only=True)
  :397  if sl is None or not sl.ok:  logger.critical("… Tutup manual SEKARANG.")
  :404      self.gate.engage_kill_switch(f"SL gagal dipasang untuk {symbol}")
        (tp.ok is never tested — a working SL with a failed TP still reports protected=True)
  :413  self.positions[symbol] = position

LiveExecutor._persist_open                              executor.py:505
  :523  qty   = float(outcome.filled_size)      ← EXCHANGE number, never order.quantity
  :524  entry = float(outcome.avg_price) or float(price)
  :526  if not _finite_positive(qty) or not _finite_positive(entry):
  :530      logger.warning(...); return success-with-message, WRITE NOTHING TO SQL
  :541  self._warn_shared_ledger()                       (once, :162)
  :544  liq    = risk_manager.calculate_liquidation_price(entry, side, lev)
  :548  margin = qty * entry / lev                     ← same arithmetic as position_manager.py:84
  :549  fee    = risk_manager.calculate_fee(qty, entry, "TAKER")
  :550  reasoning = "LIVE " + (order.reasoning or "")
  :554  row_id = await repo.insert_position(Position(..., reasoning=reasoning))
  :567  except → logger.critical + engage_kill_switch                       (:573)
  :581  if row_id is None → logger.critical + engage_kill_switch             (:587)
  :596  await repo.insert_trade(Trade(trade_type="OPEN", side=BUY/SELL))
        failure here only warns (:602) — the position row already exists
  :609  publish Channels.POSITION_UPDATE {"action":"OPENED", …, "live": True}
  :622  publish Channels.TRADE_EXECUTED   {"action":"OPEN",  …, "cloid": cloid}
  :637  agent_log TRADE_EXECUTED
  :646  self._log_wallet("open")   ← real free_collateral / total_notional from the exchange

… the SL/TP triggers on the exchange fire; the bot is not involved …

Late protection path (only if the opening order was resting):
  engine.py:636 check_pending_fills → :484-486  stop_price defaults to price*0.99 / *1.01
                                           (hardcoded ±1%, 4x the configured 0.25%)
```

---

## 6. The seven close paths, side by side

Six of the seven paper paths converge on `PositionManager.close_position` (`position_manager.py:167`); only liquidation (row 3) does not — it goes through `PositionManager._liquidate` (`position_manager.py:460`), which carries its own P&L body.
Live's converge on `LiveExecutor._record_close` (`executor.py:758`). The two are **separate
classes**, not a subclass pair — `PositionManager.close_position` is never called in live mode.

| # | Trigger | Detection | Entry point | P&L write |
|---|---|---|---|---|
| 1 | **SL hit** | `position_manager.py:431` → `_sl_hit` `:448` | `close_position(id, price, CloseReason.SL_HIT)` `:433` | `repo.close_position` `repository.py:160-169` (claim-once) |
| 2 | **TP hit** | `position_manager.py:437` → `_tp_hit` `:454` | `close_position(id, price, CloseReason.TP_HIT)` `:439` | same |
| 3 | **Liquidation** | `position_manager.py:425` → `_should_liquidate` `:442` | `_liquidate(pos, price)` `:427`, body `:460` | `repo.liquidate_position` `repository.py:171-181` (claim-once) |
| 4 | **Scalp TP** | `execution_agent.py:310` `profit_pct >= scalping.min_profit_pct` (0.0060) | paper `position_manager.py:167` · live `executor.py:935` | paper `repository.py:160` · live `executor.py:813` |
| 5 | **Auto-close expired** | `execution_agent.py:262` `hold_seconds > max_hold_seconds` (300) | paper `position_manager.py:167` · live `executor.py:935` | same as #4 |
| 6 | **Paper order, single position** | `paper_engine.py:827` `order.position_id` set | `close_position(id, price, "SIGNAL")` `:841` | `repository.py:160` |
| 7 | **Paper order, batch** | `paper_engine.py:898` all OPEN rows for the symbol | loop `close_position` per row `:930` | per row; partial → `fully_closed` False `:978` |

Liquidation body — `position_manager.py:460-511`:

```
:472  fill_price, fill_meta = close_fill_price(symbol, side, price, reason="LIQUIDATION", config)
:479  fee = risk_manager.calculate_fee(pos["quantity"], fill_price, "TAKER")
:480  realized_pnl = -pos["margin"] - fee        ← margin already debited at open; opening fee NOT re-subtracted
:482  claimed = await repo.liquidate_position(id, fill_price, realized_pnl)
:483  if not claimed: return                     ← benign: another caller won
:496  await repo.insert_trade(Trade(trade_type="LIQUIDATION"))
:498  event_bus.publish(Channels.POSITION_UPDATE, {"action":"LIQUIDATED", …})
```

`_liquidate` writes **no** account statistics: no `bump_peak_balance`, no `update_account_stats`.
`total_trades`, `winning_trades`, `losing_trades`, `profit_factor`, `peak_balance`,
`max_drawdown` and `sharpe_ratio` all silently skip every liquidation.

Live close paths:

| Path | Entry | Refusals before the exchange |
|---|---|---|
| Order CLOSE | `executor.py:655` `_close` | `:663` unknown symbol in `engine.positions` → refuse, zero exchange contact · `:676` `mid_price <= 0` → refuse · `:707` `filled <= 0` → refuse, "posisi MASIH terbuka" |
| Agent scalp TP / expired | `executor.py:935` `_LivePositionManager.close_position` | `:960` no SQLite row → `None` · `:970` exchange read fails → `record_error` + `None` · `:991` `abs_size <= 0` → `None` · `:1002` `abs_size > recorded * 1.01` → `None` (do NOT hide an exposure difference) · `:1017` no mid price → `None` · `:1044` `filled <= 0` → `None` |
| `emergency_flat` | `engine.py:655` | cancels first (`:680`), closes at mid ± 0.1 % (`:700-705`), pops only what filled (`:722`). **Writes nothing to SQLite** and has **zero production callers** (only `tests/test_live_engine.py`). |

`_LivePositionManager.close_position` takes its close size from the exchange (`:984`), not from
`engine.positions` — `submit_order(is_close=True)` already popped that entry (`engine.py:315`), and
a late fill via `check_pending_fills` never entered it. The `price` argument is deliberately ignored
(`:946-951`).

### 6.1 The single P&L formula each side lands on

```
PAPER  close_position  (position_manager.py:257)
       net_pnl = pnl - open_fee - fee
       pnl      = (fill - entry) * qty   LONG   /   (entry - fill) * qty   SHORT
                   risk_manager.py:259-262
       fee      = |qty| * |fill| * 0.00045   quantized DOWN to 1e-10   risk_manager.py:238-239
       open_fee = SELECT fee FROM trades WHERE trade_type='OPEN'   (read back, :251-254)
       balance credit = margin + net_pnl + open_fee               (:297)

LIQUIDATION (position_manager.py:480)
       realized_pnl = -margin - fee          (opening fee not re-subtracted)

LIVE   _record_close (executor.py:806)
       net = gross - fee_open - fee_close
       gross     = risk_manager.calculate_pnl(side, entry, close, size)   :790
       fee_close = calculate_fee(size, close_price, "TAKER")               :792
       fee_open  = SELECT fee FROM trades WHERE trade_type='OPEN'          :800-804
       NO apply_balance_delta. NO bump_peak_balance. NO max_drawdown/sharpe.
```

---

## 7. Crossing-cost model

One function, `trading/fill_cost.py:105`, wrapped twice: `paper_engine._fill_price` (`:272`, entry)
and `PositionManager.close_position` (`:216`) / `_liquidate` (`:472`) (exit).

```
FILL_HALF_SPREAD_FLOOR            = 0.0003     # 3 bps     fill_cost.py:45
FILL_IMPACT_FLOOR                 = 0.0001     # 1 bps     fill_cost.py:46
FILL_BOOK_MALFORMED_SPREAD_PCT    = 0.05       # 5%        fill_cost.py:47
FILL_MAX_TOTAL_COST_PCT           = 0.0050     # 50 bps    fill_cost.py:48
```

```
_observed_half_spread(symbol, max_book_age_seconds)                       fill_cost.py:63
  :71  book = market_store.get_order_book(symbol)
  :72  age  = market_store.get_order_book_age(symbol)
  :76  if not book or age is None or age > max_book_age_seconds: return None
        ← an UNSTAMPED book is STALE, not fresh
  :92  if not (mid > 0 and best_bid > 0 and best_ask > 0): return None
  :95  observed = (best_ask - best_bid) / (2.0 * mid)
  :99  if not (0.0 < observed <= 0.05): return None

fill_price_after_cost(symbol, fill_side, ref_price, *, reason,
                      max_book_age_seconds=1.5, config=None)             fill_cost.py:105
  :133 if config is not None:
  :136   max_book_age_seconds = config.scalping.max_tick_age_seconds (1.5)
        ← same staleness budget as the tick guard, so the two cannot drift apart
  :152 half   = max(observed, FILL_HALF_SPREAD_FLOOR)      ← max, never min (:147-151)
  :157 impact = FILL_IMPACT_FLOOR                          ← constant, never reads the book
  :159 total  = min(half + impact, FILL_MAX_TOTAL_COST_PCT)
  :162 signed = +total if fill_side.upper()=="BUY" else -total
  :163 fill   = float(ref_price) * (1.0 + signed)

close_fill_price(symbol, position_side, ref_price, *, reason, config, max_book_age_seconds)
  :196 fill_side = "SELL" if position_side.upper()=="LONG" else "BUY"
```

At the shipped configuration (`config.yaml`: `taker: 0.00045`, `tight_sl_pct: 0.0025`,
`fast_tp_pct: 0.0060`), with no fresh book, one crossing costs `3 bps floor + 1 bps impact = 4 bps`
and a round trip costs `2×4 + 2×4.5 = 17 bps`.

`price_is_final` (`position_manager.py:172, 213`) exists so a caller that already paid its own cost
can opt out. It has **zero callers** in the repository — the live path books the exchange's real
`avg_price` and simply does not call `fill_cost` at all.

`trading/fill_cost.py` is imported by exactly two production modules: `paper_engine.py:21-27` and
`position_manager.py:21`. There is no importer anywhere under `trading/live/`.

---

## 8. Rejoin points

Paper and live never rejoin in accounting terms. They rejoin in four places, all of them either a
shared table or a shared channel.

| # | Rejoin | Paper | Live |
|---|---|---|---|
| R1 | Same `Order` objects from the same queue | `execution_agent.py:74-79` → `:164` | identical |
| R2 | Same SQLite tables `positions` and `trades` | `position_manager.py:124` / `:142`, `:278` | `executor.py:554` / `:596`, `:820` |
| R3 | Same `Channels.POSITION_UPDATE` with `action="CLOSED"` | `position_manager.py:346-358` | `executor.py:849-861` |
| R4 | Same rows the HUD reads | `update_callbacks.py:717`, `:1013`, `:1063` | identical |

R3 is load-bearing: `DecisionAgent.sense` keys its per-symbol cooldown and loss streak off that
exact string — `agents/decision_agent.py:83` `if event.data.get("action") == "CLOSED":`, then `:90`
`if pnl <= 0` escalates the cooldown by `cooldown_after_loss_seconds * min(streak, 4)`.

**There is no mode or venue column anywhere in the schema.** `database/db.py` defines 10 tables
and 8 indexes and no `mode`. The only live marker is a `"LIVE "` prefix written into
`positions.reasoning` (`executor.py:550`, `:565`), and **no dashboard callback reads that column**.
The `"PAPER TRADING"` badge at `dashboard/layouts/hud.py:68` is a hardcoded literal; nothing under
`dashboard/` reads `run.py`'s `self.mode` or any `LiveConfig` value.

---

## 9. `market_store` — in-memory state shape

Process-global singleton, `core/market_store.py:265`. Written from the WS thread and the asyncio
loop; read by agents, engines and every dashboard callback. **No locks, no persistence.**

| Attribute | Type | Written by | Read by | Empty-state contract |
|---|---|---|---|---|
| `_last_prices` | `Dict[str, float]` | `set_price` `:46` | `get_price` `:82-83`, `get_all_prices` `:103` | `None` |
| `_price_ts` | `Dict[str, float]` | `set_price` `:48` (`time.time()`) | `get_price_age` `:93-99` | `None` |
| `_price_history` | `Dict[str, deque]` | `set_price` `:49-53` | `get_price_history` `:105-126`, `get_price_change` `:128` | `[]` |
| `_tickers` | `Dict[str, dict]` | `set_ticker` `:59` | `get_ticker` `:64-72` | `None` |
| `_order_books` | `Dict[str, dict]` | `set_order_book` `:150` | `get_order_book` `:152-165`, `fill_cost.py:71` | `None` |
| `_live_candles` | `Dict[str, dict]` | `set_live_candle` `:195` | `get_live_candle` `:198-206` | `None` |
| `_funding` | `Dict[str, float]` | `set_funding` `:214` | `get_funding` `:218-226` | `None` |
| `_open_interest` | `Dict[str, float]` | `set_open_interest` `:231` | `get_open_interest` `:235-243` | `None` |
| `_recent_trades` | `Dict[str, list]` | `set_recent_trades` `:251` (capped `trades[:50]`) | `get_recent_trades` `:253-261` | `[]` |

```
PRICE_HISTORY_MAX = 240      core/market_store.py:19     (cadence is the WS `allMids` frame rate, not the HUD tick — the 2-minute figure assumes a feed that does not exist)
```

Order-book shape written by `HyperliquidFeed._parse_book` (`data/hyperliquid_feed.py:313`):

```python
{"bids": [[px, sz], ...],   # float() coerced, sz <= 0 levels DROPPED   (:320-330)
 "asks": [[px, sz], ...],
 "timestamp": int_ms}       # required — get_order_book_age returns None without it  (:176-178)
```

`set_price` accepts only `p > 0` after `float()` coercion (`:42-46`); `set_order_book` requires
non-empty `bids` AND `asks` (`:149`). Every getter falls back to a base-asset match
(`symbol.split("/")[0].split(":")[0]`) before returning `None`.

---

## 10. Persistence schema, table by table

`database/db.py:13-154` — `SCHEMA_SQL`, executed via `executescript` on **every** connect
(`db.py:191`). There is no migration versioning.

Connection: WAL (`:174`), `PRAGMA busy_timeout=15000` (`:187`), `row_factory = aiosqlite.Row` (`:188`).
Every `Repository` method commits individually, so a close is two transactions.

### 10.1 `candles` — `db.py:14`

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK AUTOINCREMENT | |
| `symbol` | TEXT NOT NULL | |
| `timeframe` | TEXT NOT NULL | `'1m'`, `'5m'`, `'1h'` |
| `timestamp` | INTEGER NOT NULL | **Unix milliseconds from the exchange** — the only non-TEXT timestamp |
| `open` `high` `low` `close` `volume` | REAL NOT NULL | OHLC invariant enforced twice |
| `created_at` | TEXT DEFAULT `datetime('now')` | UTC |
| — | `UNIQUE(symbol, timeframe, timestamp)` | `db.py:25` → UPSERT target |

Index: `idx_candles_lookup(symbol, timeframe, timestamp)` `db.py:27`.

### 10.2 `positions` — `db.py:29`

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | claim token for every close |
| `symbol` | TEXT NOT NULL | live rows carry the caller's symbol verbatim |
| `side` | TEXT NOT NULL | `'LONG'` / `'SHORT'` |
| `entry_price` `quantity` | REAL NOT NULL | live: the exchange's `filled_size` / `avg_price` |
| `leverage` | INTEGER NOT NULL DEFAULT 1 | |
| `margin` | REAL NOT NULL | `qty*price/leverage` |
| `liquidation_price` | REAL NOT NULL | |
| `stop_loss` `take_profit` | REAL | nullable |
| `unrealized_pnl` | REAL DEFAULT 0 | zeroed on close (`repository.py:164`) |
| `status` | TEXT NOT NULL DEFAULT `'OPEN'` | `'OPEN'` / `'CLOSED'` / `'LIQUIDATED'` |
| `opened_at` `closed_at` | TEXT `datetime('now')` | UTC; `closed_at` drives the daily-loss window |
| `close_price` `realized_pnl` `close_reason` | REAL / REAL / TEXT | `realized_pnl` is NET of both fees |
| `reasoning` | TEXT | live rows carry a `"LIVE "` prefix (`executor.py:550`) |

Index: `idx_positions_status(status, symbol)` `db.py:49`. **No mode column.**

### 10.3 `trades` — `db.py:51`

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `position_id` | INTEGER → `positions(id)` | |
| `symbol` | TEXT NOT NULL | |
| `side` | TEXT NOT NULL | `'BUY'` / `'SELL'` — the **fill** side, not the position side |
| `price` `quantity` | REAL NOT NULL | |
| `fee` | REAL NOT NULL DEFAULT 0 | TAKER, quantized to 1e-10 |
| `fee_type` | TEXT DEFAULT `'TAKER'` | |
| `trade_type` | TEXT NOT NULL | `'OPEN'` / `'CLOSE'` / `'LIQUIDATION'` |
| `executed_at` | TEXT `datetime('now')` | |

Index: `idx_trades_time(executed_at)` `db.py:63`. **No PnL column** — the dashboard joins back to
`positions` (`update_callbacks.py:1066` `LEFT JOIN positions p ON p.id = t.position_id`).

### 10.4 `signals` — `db.py:65`

`id`, `symbol` NOT NULL, `timestamp` TEXT default, `signal_type` NOT NULL
(`'TECHNICAL'`/`'ML'`/`'SENTIMENT'`/`'MACRO'`), `signal_value` TEXT NOT NULL (JSON string),
`direction`, `confidence` REAL, `source`.
Index: `idx_signals_time(symbol, timestamp)` `db.py:75`.
Read by `update_neural_net` `update_callbacks.py:917-920` (newest row per symbol) and
`update_trade_stream_and_marquee` `:1076-1078` (last 5).

### 10.5 `news` — `db.py:77`

`id`, `title` NOT NULL, `source` NOT NULL, `url`, `published_at`, `fetched_at` TEXT default,
`sentiment_vader` REAL, `sentiment_finbert` REAL, `sentiment_label`, `impact_level` TEXT DEFAULT
`'LOW'`, `content_summary`.
Index: `idx_news_time(fetched_at)` `db.py:90`. **Never read by any dashboard callback.**

### 10.6 `agent_logs` — `db.py:92`

`id`, `agent_name` NOT NULL, `action` NOT NULL, `reasoning` NOT NULL, `input_data`, `output_data`,
`timestamp` TEXT default. Index `idx_agent_logs_time(agent_name, timestamp)` `db.py:101`.
Actions observed: `CYCLE`, `ERROR`, `TRADE_REJECTED`, `TRADE_EXECUTED`, `TRADE_CLOSED`.
Pruned to `agent_log_keep` = 5000 rows by `run.py:488` and the `agent_log_prune` job (`run.py:591`).

### 10.7 `account` — `db.py:103`

| Column | Type | Written by | Live writes it? |
|---|---|---|---|
| `id` | INTEGER PK | `init_account` `:332` | — |
| `balance` | REAL NOT NULL | **only** `apply_balance_delta` `repository.py:415-421` (4 call sites, all `position_manager.py:102,105,128,298`) | **NO** |
| `initial_balance` | REAL NOT NULL | `init_account` only | NO |
| `total_pnl` `total_trades` `winning_trades` `losing_trades` | | `update_account_stats` `:373-377` | YES `executor.py:831` |
| `max_drawdown` | REAL DEFAULT 0 | `position_manager.py:336` only | **NO** → `N/A` |
| `peak_balance` | REAL | `bump_peak_balance` `:429-435` (`MAX(...)`, monotonic) | NO |
| `sharpe_ratio` | REAL | `position_manager.py:337` only | **NO** → `N/A` |
| `profit_factor` | REAL | `position_manager.py:338` and `executor.py:836` | YES |
| `updated_at` | TEXT `datetime('now')` | every mutator except `bump_peak_balance` | — |

`update_balance` (`repository.py:395`) and `update_account` (`:337`) are absolute writers with **zero
production callers** — that deadness is the only reason `peak_balance` is monotonic.
Every mutation targets `WHERE id = (SELECT MAX(id) FROM account)`; every read is
`ORDER BY id DESC LIMIT 1`. Note the MAX(id) indirection: `reset_paper_db.py:10-13`
deletes `trades`, `positions`, `agent_logs` and `signals` but leaves `account` alone,
merely `UPDATE`ing the existing row (`:17-29`), and `Repository.init_account` returns
early when a row already exists (`database/repository.py:324-327`). So `id = 1`
survives a reset and a fresh account row is never minted — the stale comment at
`run.py:752-754` claiming otherwise is contradicted by `reset_paper_db.py:14-16`.

`total_trades` counts CLOSED POSITIONS, not fills (`repository.py:578-580, 595`). The HUD labels it
`"FILLS"` (`update_callbacks.py:685`), so the label and the number disagree.

### 10.8 `balance_history` — `db.py:118`

`id`, `balance` REAL NOT NULL, `unrealized_pnl` REAL DEFAULT 0, `equity` REAL NOT NULL,
`timestamp` TEXT default. Index `idx_balance_time(timestamp)` `db.py:125`.

`balance` here is the **wallet** balance (`account.balance + open_margin`,
`position_manager.py:569`), not free cash; `equity = wallet + Σ unrealized` (`:570`).
Single production writer: `take_balance_snapshot` (`position_manager.py:560-577`), registered
**unconditionally** to the PAPER position manager at `run.py:614-618`, every 60 s
(`config.yaml` `balance_snapshot: 60`). No `DELETE FROM balance_history` exists anywhere.
The binding is unconditional, but that does **not** make the table paper-only: the snapshot
body reads `account` plus `repo.get_open_positions()` from the **shared** `positions` table,
and live fills are written to that same table. In live mode this job therefore keeps firing
and writes live equity into `balance_history`. This matters because the table is the sole
source for `max_drawdown` and `sharpe_ratio` (`_get_equity_series`,
`position_manager.py:513`, limit 500) — in live mode those two stay `None` anyway, because
`LiveExecutor._record_close` calls `update_account_stats` without them and
`update_account_stats` skips every parameter that is `None`
(`database/repository.py:385-387`).

### 10.9 `direction_snapshots` — `db.py:131`

`id`, `symbol` NOT NULL, `prob_long` REAL NOT NULL, `prob_short` REAL NOT NULL, `direction` NOT
NULL, `confidence` REAL NOT NULL, `z_composite` REAL DEFAULT 0, `agent_breakdown` TEXT (JSON,
**already encoded by the caller** — `repository.py:460-467`), `diffusion` TEXT (JSON),
`created_at` TEXT default.
Index: `idx_dir_snap_symbol(symbol, id)` `db.py:143`.
Written `direction_agents.py:445`; read by `decision_agent.py:473` and
`update_callbacks.py:799-805`. Pruned to `snapshot_keep_per_symbol` = 120 by `run.py:464`.

### 10.10 `macro_data` — `db.py:145`

`id`, `indicator` NOT NULL, `value` REAL NOT NULL, `period`, `source` NOT NULL, `fetched_at` TEXT
default, `UNIQUE(indicator, period)` → UPSERT target (`repository.py:551-559`).
**No index** on this table. Read only by `derive_macro_bias` (`update_callbacks.py:72`), which has
zero callers.

---

## 11. Sequence diagram — one dashboard refresh

```
run.py:846  start_dashboard()
run.py:849      threading.Thread(target=run_dashboard, name="DashBoardThread", daemon=True)

dashboard/app.py:51   dcc.Interval(id="dashboard-interval",     interval=500)
dashboard/app.py:61   dcc.Interval(id="dashboard-slow-interval", interval=60000)

── every 500 ms ───────────────────────────────────────────────────────────────────────
 _get_sync_db()                        update_callbacks.py:64-69
     conn = sqlite3.connect(cfg.database_path)      ← FRESH BLOCKING CONNECTION PER CALL
     conn.row_factory = sqlite3.Row                ← the SAME WAL file the engine writes

 update_top_bar                :319   market_store.get_price ×3 (:324-326)
                                       + SELECT entry_price, quantity, realized_pnl
                                         FROM positions WHERE status IN ('CLOSED','LIQUIDATED')  (:334-337)
                                       + probability_engine.compute_mathematical_edge(trades_list) (:373)
 update_symbol_dropdown         :391   State-bound
 update_mini_candlestick_and_tape :414  SELECT … FROM candles WHERE timeframe='1m'
                                            ORDER BY timestamp DESC LIMIT 400  (:431-435)
                                       then reversed in Python (:469) — ASC LIMIT would freeze the chart
                                       live candle overlays market_store.get_live_candle (:483)
                                       empty → "AWAITING 1M CANDLE INGEST // NO SYNTHETIC DATA" (:458)
 update_wallet_overview         :629   SELECT * FROM account ORDER BY id DESC LIMIT 1   (:632)
                                       SELECT * FROM positions WHERE status='OPEN'        (:635)
                                       unrealized recomputed live from market_store      (:657-668)
                                       wallet_balance = balance + total_margin           (:672)
                                       equity         = wallet_balance + total_upnl      (:673)
                                       total_pnl      = equity - initial_balance          (:674)
 update_positions_grid          :714   SELECT * FROM positions WHERE status='OPEN'
                                            ORDER BY id DESC                              (:717)
                                       unrealized recomputed from market_store            (:744)
 update_probability_scanner     :794   newest direction_snapshots row for the symbol     (:799-805)
                                       none → ("--","--","AWAITING", …)                   (:812-817)
 update_neural_net              :913   newest signal per symbol                          (:917-920)
                                       agent_logs rows in the last 60 s, per agent        (:923-927)
                                       WS message-rate delta per channel via
                                         get_price_feed().hyperliquid.get_status()        (:958-973)
 update_equity_area             :1002  SELECT * FROM balance_history
                                            ORDER BY timestamp DESC LIMIT 300             (:1011-1015)
                                       then [::-1] in Python
                                       empty → one synthetic point from balance+margin (:1027-1033)
                                       → hud_figures.py:859 create_equity_area_fig
 update_analytics_and_sparklines :1209 realized from positions; profit factor recomputed (:1274-1285)
                                       mdd/sharpe read from `account` and shown only when
                                         non-zero, else "N/A"                             (:1292-1295, :1328)
                                       slippage "N/A" until a real L2 book exists          (:1310-1318)
 update_symbol_pnl              :1347 GROUP BY symbol over CLOSED/LIQUIDATED, ORDER BY pnl DESC

── every 60 000 ms ────────────────────────────────────────────────────────────────────
 update_trade_stream_and_marquee :1057 trades LEFT JOIN positions for pnl                 (:1061-1068)
                                        ORDER BY executed_at DESC LIMIT 20
                                       newest 15 agent_logs                               (:1071-1073)
                                       newest 5 signals                                    (:1075-1078)

conn.close()  in the same function.  EVERY CALLBACK IS READ-ONLY.  There is no INSERT,
UPDATE or DELETE anywhere under dashboard/.
```

Every callback is a `@app.callback` on one of the two intervals; the eight `update_legacy_*`
callbacks (`:128, :155, :241, :250, :258, :266, :274, :303`) exist only so a stale browser tab does
not raise `KeyError: Callback function not found for output`, handled at
`dashboard/app.py:38-44`.

**The dashboard never subscribes to the EventBus.** `grep -r 'event_bus\|EventBus\|Channels\.\|
subscribe' dashboard/` returns zero matches. Live fills reach the HUD purely through the SQLite
mirror written by `executor._persist_open` and `executor._record_close`.

### 11.1 Dashboard honesty rules

| Metric | Empty/unproven value | Citation |
|---|---|---|
| Slippage | `"N/A"` | `update_callbacks.py:1318` |
| Max drawdown | `"N/A"` (`f"{mdd:.2f}%" if mdd else "N/A"`) | `:1328` |
| Sharpe | `"N/A"` when `== 0` or NULL | `:1294-1295` |
| Candles | `"AWAITING 1M CANDLE INGEST // NO SYNTHETIC DATA"` | `:458` |
| Price with no feed data | `"--"` | `:368-370`, `:453` |
| Direction scanner | `("--","--","AWAITING")` | `:812-817` |
| Per-symbol PnL | `"NO CLOSED TRADES YET"` / `"IDLE"` | `:1389-1390` |

There is no resample and no forward-fill anywhere (`:473-477`). Synthetic OHLC *is* built, but
only for the current minute: `dashboard/callbacks/update_callbacks.py:527-536` constructs a
doji from a live tick (`open = high = low = close = live_price`, `volume = 0.0`) whenever the
last stored candle is older than now, so the chart's newest bar is a placeholder rather than
a traded candle.

---

## 12. Startup, shutdown and cadence

| Item | Value | Citation |
|---|---|---|
| Mode decided before any subsystem | `decision = _choose_mode()` then `TradingBotApp(decision)` | `run.py:941-942` |
| Execution tick | 0.3 s | `run.py:81`, `:715` |
| Price loop | 1.5 s, +0.15 s per symbol | `data/price_feed.py:605`, `:601` |
| Candle loop | 30 s | `run.py:743` |
| Telemetry | 25 s warm-up, then 30 s | `run.py:747`, `:784` |
| Maintenance loop | `max(60, snapshot_prune_interval)`, default 3600 | `run.py:513` |
| Live poll loop | 5.0 s | `run.py:322` |
| Live drift throttle | 2.0 s (`_REFRESH_SECONDS`), keyed on `time.monotonic()` | `executor.py:57`, `:276-278` |
| `news_agent` | 300 s / 120 s US-open | `run.py:540-546`, `config.yaml` |
| `analysis_agent` | 15 s / 10 s US-open | `run.py:549-555` |
| `decision_agent` | 3 s / 2 s US-open | `run.py:558-564` |
| `direction_ensemble` | 5 s | `run.py:570-574`, `ensemble.interval_seconds` |
| `balance_snapshot` | 60 s, **bound to the paper manager unconditionally** | `run.py:614-618` |
| `macro_update` | 21600 s | `run.py:600-604` |
| `finbert_batch` | 900 s | `run.py:607-611` |
| HUD interval | 500 ms / 60 s | `dashboard/app.py:53`, `:63` |

Asyncio tasks created in `run()`: 5 (`run.py:894-896`) plus `self._live_task` in live mode
(`run.py:321`). `shutdown()` (`:911-931`) cancels only the five, never awaits them, then closes
the DB at `:929`.

`core/config.py:698-700` force-writes `config.live.enabled = False` on every load. The
`SafetyGate` never reads that field — `TRADEBOT_LIVE` and `TRADEBOT_LIVE_CONFIRMED` are the only
route, and nothing in the repository sets them outside tests.

---

## Not verified

- **Exact sqlite3 version, aiosqlite version and Python patch level.** Stated as CPython 3.14 ABI
  from the task brief and consistent with the `.pyd` suffix
  (`cpp_microstructure.cp314-win_amd64.pyd`), but not re-checked by execution.
- **Runtime behaviour of the dashboard under a real browser.** All callback SQL and figure
  construction was read from source, not executed against a live Dash server.
- **Whether `max_drawdown` / `sharpe_ratio` behave as documented under a real `balance_history`.**
  The writer chain (`position_manager.py:325-343` → `repository.py:382-387`) was read from source;
  the note that `0.0 is not None` passes the guard and can overwrite a correct value with zero is
  derived from reading both sides, not from executing a close against a seeded database.
- **`safety.py:415-417` `record_realized_pnl` body.** Its only production caller
  (`executor.py:847`) is confirmed by grep; the accumulation into `counters.realized_pnl` and the
  persist were read from source and the call chain, not executed.
- **Wall-clock timing claims** (0.3 s heartbeat, 500 ms HUD tick) are configuration values, not
  measured latencies.
