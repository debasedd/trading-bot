"""
Apakah market-neutral benar-benar punya edge, atau cuma beberapa
rebalance besar yang dragging rata-rata?

Audit menemukan: t-stat +0.60 (tidak signifikan), max drawdown 42%,
rebalance terburuk -2383 USDT. PF 1.25 datang dari beberapa kemenangan
besar, bukan konsistensi.

Pertanyaan sebenarnya: kalau strategy ini di-deploy dengan uang
sungguhan, apakah hasil akhirnya realistis? Empat hal:

  1. WALK-FORWARD. Optimize di rezim A, test di rezim B. Kalau
     ini drift lagi, ia akan terlihat.
  2. THINNING. Kalau hanya 2-3 dari 60 rebalance yang bikin positif,
     hapus mereka dan lihat apakah sisanya masih positif.
  3. PER-SIMBOL. Apakah edge datang dari semua 21 simbol atau dari
     2 yang paling volatile (yang meniup return)?
  4. KURVA EQUITY. 42% drawdown dengan t-stat 0.6 itu kurva yang
     tidak bisa di-deploy - quelqu'un yang  mempercayai
 ini akan kehilangan setengah akun pada 첫ł jalan.
"""
import math
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


def detail_run(cs, days, horizon_h, n_side, rebalance_h, trail_h=20):
    """Kembalikan per-rebalance P&L DAN per-simbol kontribusi."""
    syms = sorted(cs)
    common = set(cs[syms[0]])
    for s in syms[1:]:
        common &= set(cs[s])
    hours = sorted(common)

    eq = 10_000.0
    rebal_pnl = []
    per_sym = defaultdict(float)
    gross_up = 0.0
    cost_total = 0.0

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

        pnl = 0.0
        for side, group in ((1, ranked[-n_side:]), (-1, ranked[:n_side])):
            for s, _m, a, c in group:
                notional = eq / (2.0 * n_side)
                g = notional * 10 * ((c - a) / a * side)
                cst = notional * COST
                pnl += g - cst
                per_sym[s] += g - cst
                gross_up += g
                cost_total += cst
        eq += pnl
        rebal_pnl.append(pnl)

    return {
        "n": len(rebal_pnl),
        "rebal": rebal_pnl,
        "net": sum(rebal_pnl),
        "final": eq,
        "per_sym": dict(per_sym),
        "gross": gross_up,
        "cost": cost_total,
    }


if __name__ == "__main__":
    rows, cs = M.load("1h")
    data = {s: rows[s] for s in rows}
    bull, bear = R.split_regimes(data, window_days=30)
    print()

    HZ, NS, RB = 6, 5, 12

    print("=" * 88)
    print("1. THINNING - apakah edge datang dari sedikit rebalance?")
    print("=" * 88)
    for label, days in (("BULLISH", bull), ("BEARISH", bear)):
        r = detail_run(cs, days, HZ, NS, RB)
        rb = sorted(r["rebal"], reverse=True)
        n = len(rb)
        print(f"\n  {label}  (n={n}, net {r['net']:+.2f})")
        for k in (1, 2, 3, 5):
            top = sum(rb[:k])
            without = r["net"] - top
            print(f"    buang {k} rebalance terbaik : net {without:>+9.2f} "
                  f"({'masih positif' if without > 0 else 'NEGATIF'})")
        print(f"    5 rebalance terburuk      : "
              f"{sum(rb[-5:]):>+9.2f}")

    print()
    print("=" * 88)
    print("2. PER-SIMBOL - dari mana edge datang?")
    print("=" * 88)
    for label, days in (("BULLISH", bull), ("BEARISH", bear)):
        r = detail_run(cs, days, HZ, NS, RB)
        ps = sorted(r["per_sym"].items(), key=lambda kv: -kv[1])
        total = sum(v for v in r["per_sym"].values()) or 1
        top3 = sum(v for _, v in ps[:3])
        print(f"\n  {label}:")
        print(f"    {'sym':<8} {'net':>10} {'kontribusi':>12}")
        for s, v in ps[:6]:
            print(f"    {s:<8} {v:>+10.2f} {v/total*100:>11.1f}%")
        print(f"    3 teratas menyumbang {top3/total*100:.1f}% dari total")

    print()
    print("=" * 88)
    print("3. GROSS vs COST - apakah edge MU sebelum biaya?")
    print("=" * 88)
    for label, days in (("BULLISH", bull), ("BEARISH", bear)):
        r = detail_run(cs, days, HZ, NS, RB)
        print(f"  {label:<8} gross {r['gross']:>+10.2f}  "
              f"cost {r['cost']:>9.2f}  net {r['net']:>+10.2f}")
        print(f"           biaya consume "
              f"{abs(r['cost']/r['gross']*100) if r['gross'] else 0:.1f}% dari gross")

    print()
    print("=" * 88)
    print("4. WALK-FORWARD - optimize di satu rezim, test di yang lain")
    print("=" * 88)
    print("  Parameter sudah tetap (tidak ada yang di-optimize per rezim),")
    print("  jadi ini lebih ketat dari walk-forward biasa.")
    for label, days in (("BULLISH", bull), ("BEARISH", bear)):
        r = detail_run(cs, days, HZ, NS, RB)
        rb = r["rebal"]
        if len(rb) < 3:
            continue
        t = (statistics.mean(rb) /
             (statistics.stdev(rb) / len(rb) ** 0.5)) if statistics.stdev(rb) else 0
        print(f"  {label:<8} n={r['n']:>3}  net {r['net']:>+9.2f}  "
              f"t {t:>+5.2f}  final {r['final']:>9.2f}")

    print()
    print("=" * 88)
    print("KESIMPULAN SEMENTARA")
    print("=" * 88)
    b = detail_run(cs, bull, HZ, NS, RB)
    s_ = detail_run(cs, bear, HZ, NS, RB)
    rb_b = sorted(b["rebal"], reverse=True)
    rb_s = sorted(s_["rebal"], reverse=True)
    w_b = b["net"] - sum(rb_b[:3])
    w_s = s_["net"] - sum(rb_s[:3])

    if w_b > 0 and w_s > 0:
        print("  Edge bertahan setelah membuang 3 rebalance terbaik")
        print("  di kedua rezim. Itu stabilitas yang nyata.")
    elif w_b > 0 or w_s > 0:
        print("  Edge bergantung pada beberapa rebalance besar di")
        print("  salah satu rezim. Fragile.")
    else:
        print("  Edge hilang setelah membuang 3 rebalance terbaik.")
        print("  PF > 1 berasal dari skew, bukan konsistensi..")