"""
Apakah edge cross-sectional marketasli ada, atau itu look-ahead?

leverage_audit menemukan: cross-sectional momentum (long N terkuat,
short N terlemah, simultaneous) menang di kedua rezim TANPA leverage.
net +281 USDT di 30 hari dari 10.000 - 2.8% per bulan, tanpa
leverage, tanpa prediksi arah pasar.

Tapi t-stat hanya 0.42. Dan ada satu cara strategy ini bisa
menjadi palsu yang belum saya periksa:

LOOK-AHEAD. Kalau ada bar yang dipakai untuk entry SEKALIGUS
dipakai untuk menghitung exit, hasilnya bukan strategi.

Diperiksa langsung:
  1. Apakah bar entry dan bar exit berbeda? Cetak timestamp keduanya.
  2. Kalau strategy-nya di-BLIND-kan ke return masa depan, apakah
     hasilnya tetap positif? (Harusnya tidak - kalau tetap, ada bug.)
  3. Replikasi sederhana yang bisaDiuji manual: ambil 20 bar
     teratas dari histogram return, lalu hitung return aktualnya.
"""
import sqlite3
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

import market_neutral as M
import regime_split as R

COST = 0.00007 + 0.0001 + 0.00045


def audit_lookahead(cs, days, horizon_h=6, n_side=5, rebalance_h=12,
                    trail_h=20):
    syms = sorted(cs)
    common = set(cs[syms[0]])
    for s in syms[1:]:
        common &= set(cs[s])
    hours = sorted(common)

    eq = 10_000.0
    rebal = []
    violations = 0
    checked = 0

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
                # Look-ahead: harga exit (c) harus dari bar SETELAH entry.
                if c <= 0 or a <= 0:
                    continue
                if hours[start + horizon_h] <= hours[start]:
                    violations += 1
                checked += 1
                notional = eq * 1.0 / (2 * n_side)   # leverage 1x gross
                pnl += notional * ((c - a) / a * side)
                pnl -= notional * COST

        eq += pnl
        rebal.append(pnl)

    return {
        "n": len(rebal),
        "net": sum(rebal),
        "legs_checked": checked,
        "violations": violations,
        "rebal": rebal,
    }


def blind_control(cs, days, horizon_h=6, n_side=5, rebalance_h=12,
                  trail_h=20):
    """
    Kontrol: statt entry berdasarkan return TRAILING, pakai ranking acak.

    Kalau random saja menghasilkan PF > 1 dengan magnitude yang
    sama, maka edge di atas bukan dari prediksi - tapi dari struktur
    portfolio yang somehow tidak_netral.
    """
    import random
    rnd = random.Random(42)

    syms = sorted(cs)
    common = set(cs[syms[0]])
    for s in syms[1:]:
        common &= set(cs[s])
    hours = sorted(common)

    eq = 10_000.0
    rebal = []
    for start in range(trail_h + 5, len(hours) - horizon_h - 1, rebalance_h):
        h0 = hours[start]
        if h0 // 86_400_000 not in days:
            continue
        avail = []
        for s in syms:
            a = cs[s].get(h0)
            c = cs[s].get(hours[start + horizon_h])
            if None in (a, c) or a <= 0:
                continue
            avail.append((s, a, c))
        if len(avail) < n_side * 2:
            continue
        rnd.shuffle(avail)
        pnl = 0.0
        for side, group in ((1, avail[:n_side]), (-1, avail[-n_side:])):
            for s, a, c in group:
                notional = eq / (2 * n_side)
                pnl += notional * ((c - a) / a * side)
                pnl -= notional * COST
        eq += pnl
        rebal.append(pnl)
    return {"n": len(rebal), "net": sum(rebal) if rebal else 0.0}


if __name__ == "__main__":
    import datetime as dt

    rows, cs = M.load("1h")
    data = {s: rows[s] for s in rows}
    bull, bear = R.split_regimes(data, window_days=30)
    print()

    print("=" * 88)
    print("1. LOOK-AHEAD CHECK")
    print("=" * 88)
    for label, days in (("BULLISH", bull), ("BEARISH", bear)):
        r = audit_lookahead(cs, days)
        print(f"  {label}: {r['legs_checked']} kaki dicek, "
              f"{r['violations']} VIOLATION (exit <= entry)")
        print(f"    net {r['net']:+.2f} USDT dari {r['n']} rebalance")
    print()

    # Print the actual timestamps for one rebalance, manual check.
    syms = sorted(cs)
    common = set(cs[syms[0]])
    for s in syms[1:]:
        common &= set(cs[s])
    hours = sorted(common)
    print("  Contoh nyata: bar yang dipakai")
    for start in (100, 200, 300):
        if start + 6 >= len(hours):
            continue
        h_entry = hours[start]
        h_exit = hours[start + 6]
        print(f"    entry {dt.datetime.fromtimestamp(h_entry/1000, dt.UTC)}"
              f"   exit {dt.datetime.fromtimestamp(h_exit/1000, dt.UTC)}"
              f"   delta {(h_exit-h_entry)/3600000:.0f} jam")
    print()

    print("=" * 88)
    print("2. KONTROL: RANKING ACAK memberi hasil yang sama?")
    print("=" * 88)
    print("  Kalau acak menghasilkan net yang sebanding, edge di atas")
    print("  bukan dari prediksi ranking - tapi dari struktur lain")
    print("  yang belum terisolasi.")
    print()
    for label, days in (("BULLISH", bull), ("BEARISH", bear)):
        real = audit_lookahead(cs, days)
        blind = blind_control(cs, days)
        print(f"  {label}:")
        print(f"    momentum ranking : net {real['net']:>+9.2f} "
              f"({real['n']} rebal)")
        print(f"    ranking ACAK    : net {blind['net']:>+9.2f} "
              f"({blind['n']} rebal)")
        ratio = (blind['net'] / real['net']
                 if real['net'] != 0 else float('inf'))
        print(f"    acak/momentum    : {ratio:>6.2f}x")
    print()

    print("=" * 88)
    print("3. SIGNIFIKAN per rezim, tanpa leverage")
    print("=" * 88)
    for label, days in (("BULLISH", bull), ("BEARISH", bear)):
        r = audit_lookahead(cs, days)
        rb = r["rebal"]
        if len(rb) < 3:
            continue
        t = (statistics.mean(rb) /
             (statistics.stdev(rb) / len(rb) ** 0.5)) if statistics.stdev(rb) else 0
        pos = sum(1 for x in rb if x > 0)
        print(f"  {label}: n={r['n']}  net {r['net']:>+8.2f}  "
              f"t {t:>+5.2f}  rebal positif {pos}/{r['n']}")