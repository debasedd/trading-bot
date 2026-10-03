"""
Biaya memakan 83.7% dari gross. Itu kunci, bukan kebetulan.

Final audit menunjukkan: cross-sectional momentum punya edge SEBELUM
biaya (gross +1519 dari 205 rebalance) tapi hampir habis setelah
biaya (+248). Dan edge-nya tidak konsisten - 3 dari 4 sub-periode
negatif.

Tapi 83.7% itu terlalu tinggi untuk kondisi likuid. Setiap
rebalance membayar 6.2 bps per kaki, jadi 12 kaki = 74 bps per
rebalance. Kalau edge per rebalance hanya ~7 USDT di ekuitas 10000,
itu 7 bps - dan biaya 74 bps langsung menelan semuanya.

Yang belum diuji: MENGURANGI TURNOVER. Kalau rebalance tiap
72 jam (bukan 12), biaya per rebalance tetap 74 bps tapi edge
per rebalance 6x lebih besar karena posisi sempat winners
lebih lama.

Tapi turnover rendah berarti portofolio jadi lebih static, dan
itumemperlemah edge-nya kalau edge itu datang dari
re-ranking yang sering.

Diuji: sweep rebalance interval dari 12h sampai 168h, dengan
gross vs biaya, di seluruh 208 hari DAN per sub-periode.
"""
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

import market_neutral as M
import final_audit as F


def sweep(cs, hz, n_side, reb, cost=F.COST):
    syms, hours = F.build(cs)
    out = []
    for start in range(25, len(hours) - hz - 1, reb):
        p = F.fold(cs, syms, hours, start, hz, n_side, cost=cost)
        if p is not None:
            out.append((hours[start], p))
    return out


def st(vals):
    n = len(vals)
    if n < 3:
        return 0.0
    m = statistics.mean(vals)
    sd = statistics.stdev(vals)
    return m / (sd / n ** 0.5) if sd else 0.0


if __name__ == "__main__":
    rows, cs = M.load("1h")
    print(f"jam: {len(F.build(cs)[1])}\n")

    print("=" * 90)
    print("TURNOVER - sweep rebalance interval, horizon 72h, 5 per sisi")
    print("=" * 90)
    print()
    print(f"  {'rebal':>7} {'n':>5} {'gross':>10} {'net':>10} "
          f"{'t_net':>7} {'gross/reb':>11} {'biaya%':>8}")
    print("  " + "-" * 66)

    for reb in (24, 36, 48, 72, 96, 120, 168):
        gross = [p for _, p in sweep(cs, 72, 5, reb, cost=0.0)]
        net = [p for _, p in sweep(cs, 72, 5, reb)]
        if len(net) < 5:
            continue
        g_sum = sum(gross) / max(1, len(gross))
        n_sum = sum(net) / max(1, len(net))
        eaten = 1 - n_sum / g_sum if g_sum else 0
        print(f"  {reb:>5}h {len(net):>5} {sum(gross):>+10.2f} "
              f"{sum(net):>+10.2f} {st(net):>+7.2f} {g_sum:>+11.2f} "
              f"{eaten*100:>7.1f}%")

    print()
    print("=" * 90)
    print("SUB-PERIODE pada rebalance terbaik")
    print("=" * 90)

    # Find the rebalance interval with best t.
    best = None
    for reb in (24, 36, 48, 72, 96, 120, 168):
        net = [p for _, p in sweep(cs, 72, 5, reb)]
        if len(net) < 10:
            continue
        t = st(net)
        if best is None or t > best[0]:
            best = (t, reb, net)

    if best:
        t, reb, net = best
        print(f"\n  terbaik: rebal {reb}h, t {t:+.2f}, net {sum(net):+.2f}")
        print()
        full = sweep(cs, 72, 5, reb)
        n = len(full)
        q = n // 4
        for i in range(4):
            chunk = [p for _, p in full[i * q:(i + 1) * q if i < 3 else n]]
            if not chunk:
                continue
            cum = 10_000.0
            for v in chunk:
                cum += v
            print(f"    sub-periode {i+1}: n={len(chunk):>3}  "
                  f"net {sum(chunk):>+8.2f}  t {st(chunk):>+5.2f}  "
                  f"comp {cum:>8.0f}")

    print()
    print("=" * 90)
    print("TURN-ON: berapa edge yang benar-benar tersisa?")
    print("=" * 90)
    for reb in (48, 72, 96):
        gross = [p for _, p in sweep(cs, 72, 5, reb, cost=0.0)]
        net = [p for _, p in sweep(cs, 72, 5, reb)]
        if not net:
            continue
        print(f"  rebal {reb:>3}h  gross/reb {sum(gross)/len(gross):>+7.3f}  "
              f"net/reb {sum(net)/len(net):>+7.3f}  "
              f"biaya makan {(1-sum(net)/sum(gross))*100 if sum(gross) else 0:>5.1f}%")
    print()
    print("  Kalau edge per rebalance < 74 bps, strategy ini tidak")
    print("  bisa menang setelah biaya - dan biaya tidak bisa turun")
    print("  tanpa berubah dari taker ke maker.")
