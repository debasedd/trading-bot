"""
Comprehensive cross-sectional factor sweep.

Sesi sebelumnya HANYA menguji satu faktor: trailing momentum 20h,
dan hanya satu arah (long winners, short losers). Itu "cross-sectional
momentum" - satu dari puluhan faktor yang dikenal di literatur.

Yang diuji di sini:

  FAKTOR:
  1. MOMENTUM: long winners short losers (sudah diuji, baseline)
  2. MEAN-REVERSION: long losers short winners (kebalikan - belum diuji)
  3. VOLATILITY-ADJUSTED MOMENTUM: rank by return/stdev, bukan raw return
  4. VOLUME-WEIGHTED: rank by return * relative_volume
  5. DISTANCE FROM HIGH: rank by jarak dari recent high (proxy oversold)

  TRAILING WINDOWS:
  6, 12, 20, 40, 72 jam (sebelumnya hanya 20)

  HOLDING PERIODS:
  6, 12, 24, 48, 72 jam

  REBALANCE:
  sama dengan hold (no overlap - clean measurement)

  COST MODEL:
  - TAKER: 4.5 bps fee + 1 bps impact + 0.7 bps spread = 6.2 bps/leg
  - MAKER: 1.5 bps fee + 0.7 bps spread = 2.2 bps/leg (bot supports limit orders)

  Total: 5 faktor x 5 trail x 5 hold x 2 cost = 250 configs
  Setiap config dijalankan di SEMUA 208 hari (bukan hanya 2 jendela 30 hari)

  KONTROL:
  - Random baseline per config
  - Sub-periode stability (4 quarters)
  - t-stat threshold: 2.0 minimum

  Goal: find ANY config with t > 2 after costs on full 208 days.
"""
import math
import random
import sqlite3
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

# Cost models (fraction per leg)
TAKER_COST = 0.00045 + 0.0001 + 0.00007   # 6.2 bps
MAKER_COST = 0.00015 + 0.00007             # 2.2 bps

LEVERAGE = 1  # test without leverage first


def load_1h():
    p = Path("data_store/historical_candles.db")
    if not p.exists():
        return {}, {}
    conn = sqlite3.connect(p)
    raw = defaultdict(list)
    for sym, ts, o, h, l, c, v in conn.execute(
        "SELECT symbol, ts, open, high, low, close, "
        "COALESCE(volume, 0) FROM hist_candles "
        "WHERE interval='1h' ORDER BY symbol, ts"
    ):
        raw[sym].append((ts, o, h, l, c, v))
    conn.close()
    # cs[sym] = {ts: (close, high, low, volume)}
    cs = {}
    for s, rows in raw.items():
        cs[s] = {r[0]: (r[5], r[3], r[4], r[1], r[2]) for r in rows}
        # cs[s][ts] = (close, high, low, open, volume_placeholder)
    # Actually we need: close for ranking, high/low for ATR
    # Simpler: cs[sym] = {ts: close}, and separate structures for extras
    # raw[sym] = [(ts, open, high, low, close, volume), ...]
    #              idx:  0     1     2    3     4       5
    cs_close = {s: {r[0]: r[4] for r in rows} for s, rows in raw.items()}
    cs_high = {s: {r[0]: r[2] for r in rows} for s, rows in raw.items()}
    cs_low = {s: {r[0]: r[3] for r in rows} for s, rows in raw.items()}
    return raw, cs_close, cs_high, cs_low


def build_common(cs_close):
    syms = sorted(cs_close)
    common = set(cs_close[syms[0]])
    for s in syms[1:]:
        common &= set(cs_close[s])
    return syms, sorted(common)


def rank_momentum(cs_close, syms, hours, idx, trail):
    """Return trailing momentum. Long winners short losers."""
    if idx < trail:
        return None
    h0 = hours[idx]
    hp = hours[idx - trail]
    ranked = []
    for s in syms:
        a = cs_close[s].get(hp)
        b = cs_close[s].get(h0)
        if a is None or b is None or a <= 0:
            continue
        ranked.append((s, (b - a) / a * 100.0, b))
    if len(ranked) < 12:
        return None
    ranked.sort(key=lambda r: r[1])
    return ranked  # low to high: shorts at start, longs at end


def rank_reversal(cs_close, syms, hours, idx, trail):
    """Mean reversion: long losers, short winners (reversed)."""
    r = rank_momentum(cs_close, syms, hours, idx, trail)
    if r is None:
        return None
    return list(reversed(r))  # flip: longs at end are now losers


def rank_vol_adjusted(cs_close, cs_high, cs_low, syms, hours, idx, trail):
    """Volatility-adjusted momentum: return / realized_vol."""
    if idx < trail:
        return None
    h0 = hours[idx]
    hp = hours[idx - trail]
    ranked = []
    for s in syms:
        a = cs_close[s].get(hp)
        b = cs_close[s].get(h0)
        if a is None or b is None or a <= 0:
            continue
        ret = (b - a) / a * 100.0
        # Realized vol from high-low range (Parkinson estimator)
        hl_sum = 0.0
        count = 0
        for k in range(max(0, idx - trail), idx):
            hk = hours[k]
            hi = cs_high[s].get(hk)
            lo = cs_low[s].get(hk)
            if hi and lo and lo > 0:
                hl_sum += math.log(hi / lo) ** 2
                count += 1
        if count < 5:
            continue
        vol = math.sqrt(hl_sum / (4.0 * count * math.log(2)))
        if vol < 1e-8:
            continue
        ranked.append((s, ret / vol, b))
    if len(ranked) < 12:
        return None
    ranked.sort(key=lambda r: r[1])
    return ranked


def rank_distance_from_high(cs_close, cs_high, syms, hours, idx, trail):
    """Rank by distance from recent high. Most oversold at end (long them)."""
    if idx < trail:
        return None
    h0 = hours[idx]
    ranked = []
    for s in syms:
        b = cs_close[s].get(h0)
        if b is None or b <= 0:
            continue
        recent_high = 0.0
        for k in range(max(0, idx - trail), idx + 1):
            hk = hours[k]
            hi = cs_high[s].get(hk)
            if hi is not None and hi > recent_high:
                recent_high = hi
        if recent_high <= 0:
            continue
        dist = (b - recent_high) / recent_high * 100.0  # negative = oversold
        ranked.append((s, dist, b))
    if len(ranked) < 12:
        return None
    ranked.sort(key=lambda r: r[1])
    # Most negative (most oversold) at start, least at end
    # For mean-reversion: long the most oversold (start), short the least (end)
    return list(reversed(ranked))


def rank_random(cs_close, syms, hours, idx, _trail, seed_offset=0):
    """Random baseline."""
    h0 = hours[idx]
    avail = []
    for s in syms:
        b = cs_close[s].get(h0)
        if b is not None and b > 0:
            avail.append((s, 0.0, b))
    if len(avail) < 12:
        return None
    rnd = random.Random(idx + seed_offset)
    rnd.shuffle(avail)
    return avail


def run_one(cs_close, syms, hours, rank_fn, trail, hold, n_side, cost_per_leg,
            rank_kwargs=None):
    """Run one config across all data. Return list of per-rebalance PnL."""
    if rank_kwargs is None:
        rank_kwargs = {}
    pnls = []
    for idx in range(trail + 5, len(hours) - hold - 1, hold):
        ranked = rank_fn(cs_close, syms, hours, idx, trail, **rank_kwargs)
        if ranked is None:
            continue

        longs = ranked[-n_side:]
        shorts = ranked[:n_side]

        pnl = 0.0
        used = 0
        h_exit = hours[idx + hold]
        for side, group in ((1, longs), (-1, shorts)):
            for s, _score, px_entry in group:
                px_exit = cs_close[s].get(h_exit)
                if px_exit is None or px_entry <= 0:
                    continue
                notional = 10_000.0 / (2.0 * n_side)
                ret = (px_exit - px_entry) / px_entry * side
                pnl += notional * LEVERAGE * ret
                pnl -= notional * cost_per_leg  # entry cost
                pnl -= notional * cost_per_leg  # exit cost
                used += 1

        if used < n_side * 2:
            continue
        pnls.append(pnl)

    return pnls


def tstat(vals):
    n = len(vals)
    if n < 5:
        return 0.0
    m = statistics.mean(vals)
    sd = statistics.stdev(vals)
    return m / (sd / n ** 0.5) if sd > 0 else 0.0


def quarters(vals):
    """Return t-stats for 4 equal quarters."""
    n = len(vals)
    q = n // 4
    if q < 3:
        return []
    ts = []
    for i in range(4):
        chunk = vals[i * q:(i + 1) * q if i < 3 else n]
        ts.append(tstat(chunk))
    return ts


if __name__ == "__main__":
    raw, cs_close, cs_high, cs_low = load_1h()
    syms, hours = build_common(cs_close)
    print(f"symbols: {len(syms)}, hours: {len(hours)} "
          f"({(hours[-1]-hours[0])/86400000:.0f} days)")
    print()

    N_SIDE = 5
    TRAILS = [6, 12, 20, 40, 72]
    HOLDS = [6, 12, 24, 48, 72]

    # Factor definitions: (name, rank_fn, extra_kwargs_fn)
    def mk_mom():
        return lambda cs, sy, hr, idx, tr, **kw: rank_momentum(cs, sy, hr, idx, tr)
    def mk_rev():
        return lambda cs, sy, hr, idx, tr, **kw: rank_reversal(cs, sy, hr, idx, tr)
    def mk_voladj():
        return lambda cs, sy, hr, idx, tr, **kw: rank_vol_adjusted(
            cs, cs_high, cs_low, sy, hr, idx, tr)
    def mk_dist():
        return lambda cs, sy, hr, idx, tr, **kw: rank_distance_from_high(
            cs, cs_high, sy, hr, idx, tr)

    FACTORS = [
        ("momentum", mk_mom()),
        ("reversal", mk_rev()),
        ("vol_adj_mom", mk_voladj()),
        ("dist_from_high", mk_dist()),
    ]

    results = []

    print("=" * 100)
    print("COMPREHENSIVE FACTOR SWEEP - 208 days, all configs")
    print("=" * 100)
    print()

    for cost_name, cost_val in [("taker", TAKER_COST), ("maker", MAKER_COST)]:
        print(f"--- COST: {cost_name} ({cost_val*10000:.1f} bps/leg) ---")
        print(f"  {'factor':<16} {'trail':>5} {'hold':>5} {'n':>5} "
              f"{'net':>10} {'t':>7} {'wr':>6} {'q_pos':>6}  note")
        print("  " + "-" * 84)

        for fname, rank_fn in FACTORS:
            for trail in TRAILS:
                for hold in HOLDS:
                    pnls = run_one(cs_close, syms, hours, rank_fn, trail,
                                   hold, N_SIDE, cost_val)
                    if len(pnls) < 10:
                        continue
                    t = tstat(pnls)
                    net = sum(pnls)
                    wr = sum(1 for p in pnls if p > 0) / len(pnls)
                    qs = quarters(pnls)
                    q_pos = sum(1 for q in qs if q > 0)

                    note = ""
                    if t > 2.0:
                        note = "*** SIGNIFICANT ***"
                    elif t > 1.5:
                        note = "marginal"

                    if t > 1.0 or (t > 0 and q_pos >= 3):
                        print(f"  {fname:<16} {trail:>5} {hold:>5} {len(pnls):>5} "
                              f"{net:>+10.2f} {t:>+7.2f} {wr*100:>5.1f}% "
                              f"{q_pos:>5}/4  {note}")

                    results.append({
                        "factor": fname, "trail": trail, "hold": hold,
                        "cost": cost_name, "n": len(pnls), "net": net,
                        "t": t, "wr": wr, "q_pos": q_pos, "pnls": pnls,
                    })
        print()

    # Show best configs
    print("=" * 100)
    print("TOP 20 CONFIGS BY T-STAT")
    print("=" * 100)
    by_t = sorted(results, key=lambda r: r["t"], reverse=True)
    print(f"  {'#':>3} {'factor':<16} {'trail':>5} {'hold':>5} {'cost':<6} "
          f"{'n':>5} {'net':>10} {'t':>7} {'wr':>6} {'q':>4}")
    print("  " + "-" * 80)
    for i, r in enumerate(by_t[:20]):
        print(f"  {i+1:>3} {r['factor']:<16} {r['trail']:>5} {r['hold']:>5} "
              f"{r['cost']:<6} {r['n']:>5} {r['net']:>+10.2f} {r['t']:>+7.2f} "
              f"{r['wr']*100:>5.1f}% {r['q_pos']:>3}/4")

    # Random baseline for comparison
    print()
    print("=" * 100)
    print("RANDOM BASELINE (best config parameters)")
    print("=" * 100)
    if by_t:
        best = by_t[0]
        for seed in range(5):
            rnd_fn = lambda cs, sy, hr, idx, tr, **kw: rank_random(
                cs, sy, hr, idx, tr, seed_offset=seed*1000)
            pnls = run_one(cs_close, syms, hours, rnd_fn, best["trail"],
                           best["hold"], N_SIDE, MAKER_COST if best["cost"] == "maker" else TAKER_COST)
            if pnls:
                print(f"  seed={seed}  n={len(pnls)}  net={sum(pnls):+.2f}  "
                      f"t={tstat(pnls):+.2f}")

    # If any config has t > 2, deep-dive it
    significant = [r for r in results if r["t"] > 2.0]
    if significant:
        print()
        print("=" * 100)
        print(f"{len(significant)} CONFIGS WITH t > 2.0 - DEEP ANALYSIS")
        print("=" * 100)
        for r in sorted(significant, key=lambda x: -x["t"])[:5]:
            qs = quarters(r["pnls"])
            print(f"\n  {r['factor']} trail={r['trail']}h hold={r['hold']}h "
                  f"cost={r['cost']}")
            print(f"    n={r['n']}  net={r['net']:+.2f}  t={r['t']:+.2f}  "
                  f"wr={r['wr']*100:.1f}%")
            print(f"    quarters: {' / '.join(f't={q:+.2f}' for q in qs)}")
            print(f"    positive quarters: {r['q_pos']}/4")

            # Equity curve
            eq = 10_000.0
            peak = eq
            max_dd = 0.0
            for p in r["pnls"]:
                eq += p
                peak = max(peak, eq)
                max_dd = max(max_dd, (peak - eq) / peak if peak > 0 else 0)
            print(f"    final equity: {eq:.2f}  max drawdown: {max_dd*100:.1f}%")

            # Per month breakdown
            # (approximate: each rebalance is `hold` hours apart)
            month_size = max(1, 720 // r["hold"])  # ~30 days in hours / hold
            chunks = [r["pnls"][i:i+month_size]
                      for i in range(0, len(r["pnls"]), month_size)]
            print(f"    monthly: ", end="")
            for c in chunks:
                if c:
                    print(f"{sum(c):+.0f}", end="  ")
            print()
    else:
        print()
        print("  No config reached t > 2.0")
        print("  Checking if maker costs enable any near-misses...")
        near = [r for r in results if r["t"] > 1.5 and r["cost"] == "maker"]
        if near:
            print(f"  {len(near)} configs with t > 1.5 at maker cost:")
            for r in sorted(near, key=lambda x: -x["t"])[:5]:
                print(f"    {r['factor']} trail={r['trail']} hold={r['hold']} "
                      f"t={r['t']:+.2f} net={r['net']:+.2f}")
