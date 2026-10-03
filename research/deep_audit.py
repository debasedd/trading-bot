"""
Deep audit of the two significant configs from factor_sweep.

Config 1: momentum trail=12h hold=12h maker — t=2.34
Config 2: dist_from_high trail=12h hold=72h maker — t=2.01

Masalah yang harus dijawab:
  1. Random baseline menghasilkan t=1.80 di satu seed. Berapa probabilitas
     t>2 dari random? (Monte Carlo: 1000 seeds)
  2. Quarter 1 config#1 negatif. Apakah edge datang dari satu bulan saja?
  3. Maker fee 1.5 bps — apakah bot ini bisa benar-benar mendapat maker?
     (Hyperliquid: limit order yang terisi sebagai maker membayar 0.015%)
  4. Apakah edge bertahan di taker cost? (6.2 bps)
  5. Kontrol reversed: apakah kebalikannya negatif?
"""
import random
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import factor_sweep as F


def run_random_monte_carlo(cs_close, syms, hours, trail, hold, n_side,
                            cost, n_seeds=1000):
    """Run n_seeds random baselines and return distribution of t-stats."""
    ts = []
    for seed in range(n_seeds):
        rnd_fn = lambda cs, sy, hr, idx, tr, **kw: F.rank_random(
            cs, sy, hr, idx, tr, seed_offset=seed * 7919)
        pnls = F.run_one(cs_close, syms, hours, rnd_fn, trail, hold,
                          n_side, cost)
        if len(pnls) >= 10:
            ts.append(F.tstat(pnls))
    return ts


if __name__ == "__main__":
    raw, cs_close, cs_high, cs_low = F.load_1h()
    syms, hours = F.build_common(cs_close)
    print(f"symbols: {len(syms)}, hours: {len(hours)}")
    print()

    configs = [
        ("momentum_12_12", F.rank_momentum, 12, 12, F.MAKER_COST),
        ("dist_high_12_72", lambda cs, sy, hr, idx, tr, **kw:
         F.rank_distance_from_high(cs, cs_high, sy, hr, idx, tr),
         12, 72, F.MAKER_COST),
    ]

    for name, rank_fn, trail, hold, cost in configs:
        print("=" * 80)
        print(f"AUDIT: {name}  (trail={trail}h hold={hold}h "
              f"cost={cost*10000:.1f}bps)")
        print("=" * 80)

        # Real result
        pnls = F.run_one(cs_close, syms, hours, rank_fn, trail, hold,
                          5, cost)
        t_real = F.tstat(pnls)
        print(f"\n  Real: n={len(pnls)}  net={sum(pnls):+.2f}  "
              f"t={t_real:+.2f}  wr={sum(1 for p in pnls if p>0)/len(pnls)*100:.1f}%")

        # 1. Monte Carlo random baseline
        print(f"\n  1. MONTE CARLO: 1000 random seeds at same params")
        rnd_ts = run_random_monte_carlo(cs_close, syms, hours, trail, hold,
                                         5, cost, n_seeds=500)
        if rnd_ts:
            above2 = sum(1 for t in rnd_ts if t > 2.0)
            above_real = sum(1 for t in rnd_ts if t > t_real)
            print(f"     random t: mean={statistics.mean(rnd_ts):+.2f}  "
                  f"stdev={statistics.stdev(rnd_ts):.2f}")
            print(f"     random t > 2.0: {above2}/{len(rnd_ts)} = "
                  f"{above2/len(rnd_ts)*100:.1f}%")
            print(f"     random t > {t_real:.2f}: {above_real}/{len(rnd_ts)} = "
                  f"{above_real/len(rnd_ts)*100:.1f}%")
            if above_real / len(rnd_ts) > 0.05:
                print(f"     >>> NOT SIGNIFICANT vs random (p={above_real/len(rnd_ts):.3f})")
            else:
                print(f"     >>> SIGNIFICANT vs random (p={above_real/len(rnd_ts):.3f})")

        # 2. Sub-periods
        print(f"\n  2. SUB-PERIODS (7 monthly chunks)")
        month_size = max(1, 720 // hold)
        for i in range(0, len(pnls), month_size):
            chunk = pnls[i:i + month_size]
            if len(chunk) >= 3:
                t_c = F.tstat(chunk)
                net_c = sum(chunk)
                print(f"     month {i//month_size+1}: n={len(chunk):>3}  "
                      f"net={net_c:+8.2f}  t={t_c:+5.2f}")

        # 3. At taker cost
        print(f"\n  3. AT TAKER COST (6.2 bps)")
        pnls_taker = F.run_one(cs_close, syms, hours, rank_fn, trail, hold,
                                5, F.TAKER_COST)
        if pnls_taker:
            t_taker = F.tstat(pnls_taker)
            print(f"     net={sum(pnls_taker):+.2f}  t={t_taker:+.2f}")
            if t_taker > 2.0:
                print(f"     >>> SURVIVES taker cost")
            else:
                print(f"     >>> DOES NOT survive taker cost (needs maker)")

        # 4. Reversed direction
        print(f"\n  4. REVERSED DIRECTION")
        rev_fn = lambda cs, sy, hr, idx, tr, **kw: list(
            reversed(rank_fn(cs, sy, hr, idx, tr) or []))
        pnls_rev = F.run_one(cs_close, syms, hours, rev_fn, trail, hold,
                              5, cost)
        if pnls_rev:
            t_rev = F.tstat(pnls_rev)
            print(f"     reversed: net={sum(pnls_rev):+.2f}  t={t_rev:+.2f}")
            if t_rev < 0:
                print(f"     >>> Good: reversed is negative")
            else:
                print(f"     >>> BAD: reversed is also positive (not directional)")

        # 5. Walk-forward: train on first half, test on second
        print(f"\n  5. WALK-FORWARD (train 1st half, test 2nd half)")
        mid = len(pnls) // 2
        train = pnls[:mid]
        test = pnls[mid:]
        t_train = F.tstat(train)
        t_test = F.tstat(test)
        print(f"     train: n={len(train)}  net={sum(train):+.2f}  t={t_train:+.2f}")
        print(f"     test:  n={len(test)}  net={sum(test):+.2f}  t={t_test:+.2f}")
        if sum(test) > 0 and t_test > 1.0:
            print(f"     >>> Out-of-sample positive")
        else:
            print(f"     >>> Out-of-sample weak or negative")

        print()
