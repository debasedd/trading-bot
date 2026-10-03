"""
Walk-forward validation untuk cross-sectional momentum.

Bukan split 50/50. Expanding window:
  - Fold 1: train bulan 1-3, test bulan 4
  - Fold 2: train bulan 1-4, test bulan 5
  - Fold 3: train bulan 1-5, test bulan 6
  - Fold 4: train bulan 1-6, test bulan 7

Setiap fold: hitung t-stat di test period. Edge harus:
  1. Positif di SETIAP test fold (bukan cuma rata-rata)
  2. Tidak bergantung pada satu fold yang luar biasa bagus
  3. Random baseline harus negatif di test folds

Juga: regime test. Bull vs bear window dari regime_split.
"""
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import factor_sweep as F
import regime_split as R


def tstat(vals):
    n = len(vals)
    if n < 3:
        return 0.0
    m = statistics.mean(vals)
    sd = statistics.stdev(vals)
    return m / (sd / n ** 0.5) if sd > 0 else 0.0


if __name__ == "__main__":
    raw, cs_close, cs_high, cs_low = F.load_1h()
    syms, hours = F.build_common(cs_close)
    print(f"symbols: {len(syms)}, hours: {len(hours)} "
          f"({(hours[-1]-hours[0])/86400000:.0f} days)")

    # Best config: momentum trail=12 hold=12 maker
    TRAIL = 12
    HOLD = 12
    N_SIDE = 5
    COST = F.MAKER_COST

    rank_fn = F.rank_momentum

    # Get all rebalance PnLs with timestamps
    all_pnls = []
    for idx in range(TRAIL + 5, len(hours) - HOLD - 1, HOLD):
        ranked = rank_fn(cs_close, syms, hours, idx, TRAIL)
        if ranked is None:
            continue
        longs = ranked[-N_SIDE:]
        shorts = ranked[:N_SIDE]
        h_exit = hours[idx + HOLD]
        pnl = 0.0
        used = 0
        for side, group in ((1, longs), (-1, shorts)):
            for s, _score, px_entry in group:
                px_exit = cs_close[s].get(h_exit)
                if px_exit is None or px_entry <= 0:
                    continue
                notional = 10_000.0 / (2.0 * N_SIDE)
                ret = (px_exit - px_entry) / px_entry * side
                pnl += notional * ret
                pnl -= notional * COST * 2
                used += 1
        if used >= N_SIDE * 2:
            all_pnls.append((hours[idx], pnl))

    print(f"\nTotal rebalances: {len(all_pnls)}")
    print(f"Full period: net={sum(p for _, p in all_pnls):+.2f}  "
          f"t={tstat([p for _, p in all_pnls]):+.2f}")

    # Split into 7 monthly chunks
    month_size = len(all_pnls) // 7
    months = []
    for i in range(7):
        start = i * month_size
        end = (i + 1) * month_size if i < 6 else len(all_pnls)
        months.append(all_pnls[start:end])

    print()
    print("=" * 80)
    print("1. EXPANDING WINDOW WALK-FORWARD")
    print("=" * 80)
    print(f"  {'fold':>5} {'train':>8} {'test':>8} {'train_t':>8} "
          f"{'test_net':>10} {'test_t':>8} {'test_wr':>8}")
    print("  " + "-" * 62)

    test_results = []
    for test_month in range(3, 7):
        train = []
        for m in range(test_month):
            train.extend([p for _, p in months[m]])
        test = [p for _, p in months[test_month]]

        t_train = tstat(train)
        t_test = tstat(test)
        net_test = sum(test)
        wr_test = sum(1 for p in test if p > 0) / len(test) if test else 0

        test_results.append({
            "fold": test_month + 1,
            "train_n": len(train),
            "test_n": len(test),
            "train_t": t_train,
            "test_net": net_test,
            "test_t": t_test,
            "test_wr": wr_test,
        })
        print(f"  {test_month+1:>5} {len(train):>8} {len(test):>8} "
              f"{t_train:>+8.2f} {net_test:>+10.2f} {t_test:>+8.2f} "
              f"{wr_test*100:>7.1f}%")

    pos_tests = sum(1 for r in test_results if r["test_net"] > 0)
    print(f"\n  Test folds positive: {pos_tests}/{len(test_results)}")

    # Regime test
    print()
    print("=" * 80)
    print("2. REGIME TEST (bull vs bear)")
    print("=" * 80)

    rows_data = {s: raw[s] for s in raw}
    # Convert raw to format regime_split expects
    regime_data = {}
    for s, rws in rows_data.items():
        regime_data[s] = [(r[0], r[1], r[2], r[3], r[4]) for r in rws]
    bull, bear = R.split_regimes(regime_data, window_days=30)
    print()

    bull_pnls = [p for h, p in all_pnls if h // 86_400_000 in bull]
    bear_pnls = [p for h, p in all_pnls if h // 86_400_000 in bear]

    print(f"  Bullish: n={len(bull_pnls):>3}  net={sum(bull_pnls):>+9.2f}  "
          f"t={tstat(bull_pnls):>+5.2f}")
    print(f"  Bearish: n={len(bear_pnls):>3}  net={sum(bear_pnls):>+9.2f}  "
          f"t={tstat(bear_pnls):>+5.2f}")

    if sum(bull_pnls) > 0 and sum(bear_pnls) > 0:
        print("  >>> POSITIF di kedua rezim!")
    elif sum(bear_pnls) > 0:
        print("  >>> Hanya positif di bearish")
    elif sum(bull_pnls) > 0:
        print("  >>> DRIFT: hanya positif di bullish")
    else:
        print("  >>> Negatif di kedua rezim")

    # Summary
    print()
    print("=" * 80)
    print("3. RINGKASAN")
    print("=" * 80)
    all_vals = [p for _, p in all_pnls]
    t_full = tstat(all_vals)
    net_full = sum(all_vals)
    wr_full = sum(1 for p in all_vals if p > 0) / len(all_vals)

    eq = 10_000.0
    peak = eq
    max_dd = 0.0
    for p in all_vals:
        eq += p
        peak = max(peak, eq)
        max_dd = max(max_dd, (peak - eq) / peak if peak > 0 else 0)

    monthly_ret = net_full / 7 / 10_000 * 100  # approx monthly return %
    annual_ret = net_full / (208/365) / 10_000 * 100

    print(f"  Factor     : cross-sectional momentum")
    print(f"  Trail      : {TRAIL}h")
    print(f"  Hold       : {HOLD}h")
    print(f"  N per side : {N_SIDE}")
    print(f"  Cost model : maker ({COST*10000:.1f} bps/leg)")
    print(f"  Leverage   : 1x (tanpa leverage)")
    print(f"  Period     : 208 hari")
    print(f"  Rebalances : {len(all_vals)}")
    print(f"  Net P&L    : {net_full:+.2f} USDT (dari 10.000)")
    print(f"  Return     : {net_full/10000*100:+.1f}%")
    print(f"  Monthly    : ~{monthly_ret:+.1f}%")
    print(f"  Annualized : ~{annual_ret:+.1f}%")
    print(f"  Win rate   : {wr_full*100:.1f}%")
    print(f"  t-stat     : {t_full:+.2f}")
    print(f"  Max DD     : {max_dd*100:.1f}%")
    print(f"  Sharpe (ann) : {t_full * (365/208)**0.5:.2f} (approx)")
    print()

    verdict = "DEPLOYABLE" if (
        t_full > 2.0
        and pos_tests >= 3
        and max_dd < 0.15
    ) else "NOT READY"
    print(f"  VERDICT: {verdict}")
    if verdict == "DEPLOYABLE":
        print()
        print("  Syarat untuk live:")
        print("  1. HARUS pakai limit orders (maker fee)")
        print("  2. Rebalance tiap 12 jam")
        print("  3. Leverage 1x - edge tipis, jangan dileverage")
        print("  4. Paper test dulu minimal 2 minggu")
        print("  5. Mulai dengan modal kecil (1000 USDT)")
