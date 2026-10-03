"""
Market-neutral dengan leverage realistis.

Audit sebelumnya membunuh kandidat karena t-stat rendah (0.53-0.60) dan
42% drawdown. Tapi ada satu asumsi yang belum diuji dan bisa
menjelaskan kenapa t-stat segitu: LEVERAGE 10x.

Dengan 10 kaki long + 10 kaki short, tiap kaki 1/20 dari ekuitas
= 500 USDT di-leverage 10x = 5.000 USDT notional per kaki, jadi
10.000 total per sisi dari akun 10.000. Itu leverage BERSAMAAN 10x,
dan crypto bisa bergerak 10% dalam 6 jam tanpa masalah berarti.

Pertanyaan sebenarnya: apakah edge per-kaki ini cukup besar untuk membayar biaya, ATAU apakah ia hanya
terlihat karena leverage
menggandakan notional-nya?

Diuji:
  1. Edge dengan leverage 1, 2, 3, 5, 10
  2. Edge dengan batas eksposur riil (gross exposure cap)
  3. Liquidation: pada leverage berapa equity pernah hancur
"""
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

import market_neutral as M
import regime_split as R

TAKER = 0.00045
IMPACT = 0.0001
DEFAULT_FLOOR = 0.00007
COST = DEFAULT_FLOOR + IMPACT + TAKER


def run_leverage(cs, days, horizon_h, n_side, rebalance_h, leverage,
                 trail_h=20):
    """
    Gross exposure dibatasi: total notional = equity * leverage_total,
    dibagi rata ke semua kaki. Jadi leverage per kaki = leverage_total
    / (2 * n_side).

    Contoh: leverage_total 10 dengan 10 kaki = 1x per kaki (efektif
    tidak berleverage di atas 1x gross). leverage_total 50 dengan
    10 kaki = 5x per kaki.
    """
    syms = sorted(cs)
    common = set(cs[syms[0]])
    for s in syms[1:]:
        common &= set(cs[s])
    hours = sorted(common)

    eq = 10_000.0
    rebal = []
    max_dd = 0.0
    peak = eq

    for start in range(trail_h + 5, len(hours) - horizon_h - 1, rebalance_h):
        h0 = hours[start]
        if h0 // 86_400_000 not in days:
            continue
        ranked = []
        for s in syms:
            a = cs[s].get(h0)
            b = cs[s].get(hours[start - trail_h])
            c = cs[s].get(hours[start + horizon_h])
            if None in (a, b, c) or b <= 0 or a <= 0:
                continue
            ranked.append((s, (a - b) / b * 100.0, a, c))
        if len(ranked) < n_side * 2:
            continue
        ranked.sort(key=lambda r: r[1])

        legs = []
        for side, group in ((1, ranked[-n_side:]), (-1, ranked[:n_side])):
            for s, _m, a, c in group:
                legs.append((side, a, c))

        if not legs:
            continue

        # Gross exposure = equity * leverage, dibagi rata
        per_leg_notional = eq * leverage / len(legs)

        pnl = 0.0
        for side, a, c in legs:
            ret = (c - a) / a * side
            pnl += per_leg_notional * ret
            pnl -= per_leg_notional * COST

        eq += pnl
        peak = max(peak, eq)
        max_dd = max(max_dd, (peak - eq) / peak if peak > 0 else 0)
        rebal.append(pnl)

    if not rebal:
        return None
    t = (statistics.mean(rebal) /
         (statistics.stdev(rebal) / len(rebal) ** 0.5)) if len(rebal) > 1 and statistics.stdev(rebal) else 0
    return {
        "n": len(rebal),
        "net": sum(rebal),
        "final": eq,
        "t": t,
        "max_dd": max_dd,
        "mean": statistics.mean(rebal),
    }


if __name__ == "__main__":
    rows, cs = M.load("1h")
    data = {s: rows[s] for s in rows}
    bull, bear = R.split_regimes(data, window_days=30)
    print()

    HZ, NS, RB = 6, 5, 12

    print("=" * 94)
    print("LEVERAGE REALISTIS - edge per kaki vs gross exposure")
    print("=" * 94)
    print(f"  {NS} long + {NS} short = {2*NS} kaki")
    print()
    print(f"  {'gross':>6} {'per kaki':>9} | "
          f"{'BULL net':>10} {'t':>6} {'dd':>7} | "
          f"{'BEAR net':>10} {'t':>6} {'dd':>7}  verdict")
    print("  " + "-" * 92)

    for lev in (1, 2, 3, 5, 10, 20, 50, 100):
        per_leg = lev / (2 * NS)
        b = run_leverage(cs, bull, HZ, NS, RB, lev)
        s_ = run_leverage(cs, bear, HZ, NS, RB, lev)
        if not b or not s_:
            continue
        ok = b["net"] > 0 and s_["net"] > 0
        bt_ok = b["t"] > 2 and s_["t"] > 2
        verdict = "LOLOS" if ok else (
            "rugi di bearish" if b["net"] > 0 else "rugi")
        if ok and bt_ok:
            verdict = "LOLOS + SIGNIFIKAN"
        print(f"  {lev:>5}x {per_leg:>8.1f}x | "
              f"{b['net']:>+10.2f} {b['t']:>+6.2f} {b['max_dd']*100:>6.1f}% | "
              f"{s_['net']:>+10.2f} {s_['t']:>+6.2f} {s_['max_dd']*100:>6.1f}%"
              f"  {verdict}")

    print()
    print("=" * 94)
    print("BERAPA LEVERAGE YANG MEMBUTUHKAN EDGE POSITIF PER KAKI?")
    print("=" * 94)
    print()
    b = run_leverage(cs, bull, HZ, NS, RB, 1)
    s_ = run_leverage(cs, bear, HZ, NS, RB, 1)
    if b and s_:
        print(f"  Leverage 1x (netral, tanpa leverage):")
        print(f"    bullish  net {b['net']:>+8.2f}  mean/rebal {b['mean']:>+7.3f}")
        print(f"    bearish  net {s_['net']:>+8.2f}  mean/rebal {s_['mean']:>+7.3f}")
        print()
        if b["net"] > 0 and s_["net"] > 0:
            print("  >> EDGE ADA TANPA LEVERAGE. cross-sectional momentum")
            print("     benar-benar punya alpha di kedua rezim.")
            print("     Tapi t-stat perlu dicek - kalau rendah, hasilnya")
            print("     kecil tapi konsisten, bukan besar tapi fragile.")
        else:
            print("  >> Tidak ada edge tanpa leverage., ini berarti edge yang")
            print("     terlihat sebelumnya datang dari leverage, bukan")
            print("     dari prediksi.")