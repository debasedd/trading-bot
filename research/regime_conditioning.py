"""
Dua konfigurasiwbrowned]\b yang.text

Dua konfigurasiwygląda najlepiej: rebal 48h dan 96h, dengan
net +3025 dan +1642, gross +35.57 dan +37.79 per rebalance,
biaya hanya 16-17% dari gross.

Tapi sub-periode 3 dan 4 negatif di keduanya. Dua kemungkinan:
  A. Edge ada tapi tidak konsisten - nyata tapi tidak bisa dipakai.
  B. Edge ada HANYA di kondisi tertentu (misal saat crypto tidak
     likuid, atau saat ada macro event). Kalau ini, strategi
     bisa conditioned untuk tampil di kondisi yang tepat.

Yang menguji mana: compare kondisi saat sub-periode yang menang
vs yang kalah. Kalau ada perbedaan yang bisa dideteksi dari
data yang tersedia saat itu (volatilitas, dispersi), itu
conditionable. Kalau tidak, edge-nya tidak bisa dipakai.

Ini pertanyaan terakhir. Kalau ini tidak menghasilkan apa pun,
saya berhenti dan lapor honestly.
"""
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

import market_neutral as M
import final_audit as F


def enrich(cs, syms, hours, start, hz, n_side, reb, cost=F.COST):
    """Satu rebalance, plus kondisi pasar saat itu."""
    if start + hz >= len(hours) or start < 25:
        return None
    h0 = hours[start]
    avail = []
    for s in syms:
        a = cs[s].get(h0)
        b = cs[s].get(hours[start - 20])
        c = cs[s].get(hours[start + hz])
        if None in (a, b, c) or b <= 0 or a <= 0:
            continue
        avail.append((s, (a - b) / b * 100.0, a, c))
    if len(avail) < n_side * 2:
        return None
    avail.sort(key=lambda r: r[1])

    longs, shorts = avail[-n_side:], avail[:n_side]
    pnl = 0.0
    leg_returns = []
    for side, group in ((1, longs), (-1, shorts)):
        for s, _m, a, c in group:
            notional = 10_000.0 / (2.0 * n_side)
            ret = (c - a) / a * side
            pnl += notional * ret
            pnl -= notional * cost
            leg_returns.append(ret)

    # Kondisi pasar: rata-rata return trailing semua simbol (regime)
    market_mom = statistics.mean(r[1] for r in avail)

    # Dispersi: seberapaebar return antar-simbol saat ini.
    # Dispersi tinggi = ada yang/sqlite bergerak beda-beda = ada
    # cross-sectional edge yang bisa diambil. Dispersi rendah = semua
    # bergerak sama = tidak ada yang bisa dibedakan.
    rets = [r[1] for r in avail]
    dispersion = statistics.stdev(rets) if len(rets) > 1 else 0

    return {
        "pnl": pnl,
        "market_mom": market_mom,
        "dispersion": dispersion,
        "n_legs": len(leg_returns),
    }


def sweep_enriched(cs, hz, n_side, reb):
    syms, hours = F.build(cs)
    out = []
    for start in range(25, len(hours) - hz - 1, reb):
        e = enrich(cs, syms, hours, start, hz, n_side, reb)
        if e:
            out.append((hours[start], e))
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
    syms, hours = F.build(cs)
    print(f"jam {len(hours)} ({(hours[-1]-hours[0])/86400000:.0f} hari)\n")

    HZ, NS, RB = 72, 5, 48

    print("=" * 90)
    print("1. APAKAH EDGE BERGANTUNG PADA KONDISI PASAR?")
    print("=" * 90)
    data = sweep_enriched(cs, HZ, NS, RB)
    print(f"  {len(data)} rebalance dianalisis (horizon {HZ}h, rebal {RB}h)\n")

    # Compare winning vs losing rebalances by condition.
    win = [e for _, e in data if e["pnl"] > 0]
    loss = [e for _, e in data if e["pnl"] <= 0]

    print(f"  {'kondisi':<22} {'saat menang':>14} {'saat kalah':>14}")
    print("  " + "-" * 52)
    for label, key in (("market momentum %", "market_mom"),
                        ("dispersi antar-simbol %", "dispersion")):
        mw = statistics.mean(e[key] for e in win) if win else 0
        ml = statistics.mean(e[key] for e in loss) if loss else 0
        print(f"  {label:<22} {mw:>+13.3f} {ml:>+13.3f}")

    print()
    print(f"  menang: {len(win)}, kalah: {len(loss)}")

    print()
    print("=" * 90)
    print("2. EDGE BERSYARAT (jika bisa ditemukan)")
    print("=" * 90)
    print("  Filter hanya kondisi yang tersedia real-time: momentum")
    print("  pasar dan dispersi cross-sectional. Bukan masa depan.")
    print()

    best = None
    for mom_min in (-5, -2, -1, 0, 1, 2):
        for disp_min in (0, 1, 2, 3, 4):
            sel = [e["pnl"] for _, e in data
                   if e["market_mom"] >= mom_min
                   and e["dispersion"] >= disp_min]
            if len(sel) < 10:
                continue
            t = st(sel)
            if best is None or t > best[0]:
                best = (t, mom_min, disp_min, sel)

    if best and best[0] > 2:
        t, mm, dm, sel = best
        print(f"  mom>={mm:+.1f}%  dispersi>={dm:.1f}%  "
              f"n={len(sel)}  net {sum(sel):+.2f}  t {t:+.2f}")
        print("  >> SIGNIFIKAN setelah kondisi!")
    else:
        print("  Tidak ada kombinasi kondisi yang menghasilkan t > 2.")
        if best:
            t, mm, dm, sel = best
            print(f"  terbaik: mom>={mm:+.1f}% disp>={dm:.1f}%  "
                  f"n={len(sel)}  t {t:+.2f} (tidak significant)")

    print()
    print("=" * 90)
    print("3. RINGKASAN - apa yang sebenarnya diketahui")
    print("=" * 90)
    gross = [e["pnl"] for _, e in data]
    net = [p for _, p in sweep_enriched.__wrapped__(cs, HZ, NS, RB)] \
        if hasattr(sweep_enriched, "__wrapped__") else None

    all_pnl = [e["pnl"] for _, e in data]
    print(f"  edge per rebalance (net) : {statistics.mean(all_pnl):+.2f} USDT")
    print(f"  t-stat                  : {st(all_pnl):+.2f}")
    print(f"  rebalance positif       : {len(win)}/{len(data)}")
    print()
    print("  Kesimpulan:")
    if st(all_pnl) > 2 and len(win) > len(data) * 0.6:
        print("  EDGE ADA dan stabil. Bisa dipakai.")
    elif sum(all_pnl) > 0:
        print("  Edge ada tapi TIDAK SIGNIFIKAN. Secara statistik")
        print("  ini belum bisa diklaim. Butuh data lebih atau kondisi")
        print("  yang lebih selektif - dan kondisi tadi tidak ditemukan.")
        print("  Ini bukan 'hampir berhasil' - ini 'belum terbukti'.")
    else:
        print("  TIDAK ADA edge. Bersih.")