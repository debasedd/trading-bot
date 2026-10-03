"""
Audit reversal: kenapa net +9.2M dari 10K?

Kecurigaan: reversal = rank losers first, longs at END.
Tapi di run_one: longs = ranked[-n_side:], shorts = ranked[:n_side].
Kalau reversal membalik list, maka "longs" sekarang adalah top-momentum,
dan "shorts" adalah bottom-momentum. Itu SAMA dengan momentum biasa.
Atau kebalik?

Cek: apakah reversal benar-benar long losers, atau ada bug di arah?
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import factor_sweep as F

raw, cs_close, cs_high, cs_low = F.load_1h()
syms, hours = F.build_common(cs_close)

# Test at one point
idx = 100
trail = 20

mom = F.rank_momentum(cs_close, syms, hours, idx, trail)
rev = F.rank_reversal(cs_close, syms, hours, idx, trail)

print("MOMENTUM ranking (low to high):")
if mom:
    print(f"  shorts ([:5]) = {[(s, f'{sc:+.2f}') for s, sc, _ in mom[:5]]}")
    print(f"  longs  ([-5:]) = {[(s, f'{sc:+.2f}') for s, sc, _ in mom[-5:]]}")
    print(f"  longs have HIGHER return -> momentum long winners")

print()
print("REVERSAL ranking (reversed):")
if rev:
    print(f"  shorts ([:5]) = {[(s, f'{sc:+.2f}') for s, sc, _ in rev[:5]]}")
    print(f"  longs  ([-5:]) = {[(s, f'{sc:+.2f}') for s, sc, _ in rev[-5:]]}")
    print(f"  longs have LOWER return -> reversal long losers")

print()
print("=" * 70)
print("Sekarang cek satu rebalance reversal secara detail")
print("=" * 70)

# Run one rebalance manually
trail = 20
hold = 6
n_side = 5
cost = F.TAKER_COST

idx = trail + 5
h0 = hours[idx]
h_exit = hours[idx + hold]

rev_ranked = F.rank_reversal(cs_close, syms, hours, idx, trail)
if rev_ranked:
    longs = rev_ranked[-n_side:]
    shorts = rev_ranked[:n_side]

    print(f"\nEntry hour idx={idx}, exit idx={idx+hold}")
    print(f"  ts_entry={h0}, ts_exit={h_exit}")
    print()

    total_pnl = 0
    for tag, side, group in [("LONG", 1, longs), ("SHORT", -1, shorts)]:
        print(f"  {tag}:")
        for s, score, px_entry in group:
            px_exit = cs_close[s].get(h_exit)
            if px_exit is None:
                continue
            notional = 10_000.0 / (2.0 * n_side)
            ret = (px_exit - px_entry) / px_entry * side
            pnl = notional * 1 * ret  # leverage 1
            cost_pnl = notional * cost * 2  # entry + exit
            net_pnl = pnl - cost_pnl
            total_pnl += net_pnl
            print(f"    {s:<8} trailing={score:+.2f}%  "
                  f"entry={px_entry:.4f}  exit={px_exit:.4f}  "
                  f"ret={ret*100:+.4f}%  pnl={net_pnl:+.2f}")

    print(f"\n  total pnl this rebalance: {total_pnl:+.2f}")
    print(f"  That's {total_pnl/10000*100:+.4f}% of initial equity")

print()
print("=" * 70)
print("BUG CHECK: is equity compounding in run_one?")
print("=" * 70)
print("  run_one uses: notional = 10_000.0 / (2.0 * n_side)")
print("  This is FIXED at 1000 USDT per leg regardless of equity.")
print("  So net +9.2M means avg PnL per rebalance = 9216685/828 = ", end="")
print(f"{9216685/828:+.2f} USDT")
print(f"  That's {9216685/828/1000*100:+.2f}% per leg per rebalance")
print()
print("  If this is ACTUALLY returning 1113% per rebalance per leg,")
print("  something is deeply wrong. Checking actual return magnitudes...")

# Check distribution of per-rebalance returns
pnls = F.run_one(cs_close, syms, hours,
                 lambda cs, sy, hr, idx, tr, **kw: F.rank_reversal(cs, sy, hr, idx, tr),
                 20, 6, 5, F.TAKER_COST)
import statistics
print(f"\n  n={len(pnls)}")
print(f"  mean={statistics.mean(pnls):+.2f}")
print(f"  median={statistics.median(pnls):+.2f}")
print(f"  stdev={statistics.stdev(pnls):.2f}")
print(f"  min={min(pnls):+.2f}  max={max(pnls):+.2f}")
print(f"  p5={sorted(pnls)[len(pnls)//20]:+.2f}  "
      f"p95={sorted(pnls)[len(pnls)*19//20]:+.2f}")
