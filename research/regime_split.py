"""
Uji kandidat di rezim BULLISH dan BEARISH secara terpisah.

-Ini menggantikan mirror test. Mirror test membalik harga secara
sintetis; yang lebih kuat adalah memakai periode yang benar-benar
bullish dan periode yang benar-benar bearish dari riwayat bursa.

Alasannya penting: mirror test mengubah HARGSA tapi tidak mengubah
distribusi volume, dan mengubah arah tanpa mengubah volatilitas.
Data asli_registry tidak punya Problema itu.

Aturan:
  * Edge yang nyata harus POSITIF di kedua rezim.
  * Edge yang hanya positif di bullish adalah drift, apa pun
   signifikansi-nya.
  * Edge yang negatif di bearish bukan "tidakTzBaik" - itu actively
    berbahaya, karena itueras-bullish.

Sumber data: `data_store/historical_candles.db` (400 hari dari
Hyperliquid), bukan `trading_bot.db` yang hanya punya 25 jam dan
hanya periode bullish.
"""
import math
import sqlite3
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

TAKER = 0.00045
IMPACT = 0.0001

# Spread terukur dari order book live (research/spread_stability.py).
# Untuk simbol yang tidak ada di sana, pakai median global 0.0007.
FLOOR = {
    "BTC": 0.000012, "HYPE": 0.000012, "ETH": 0.000037,
    "XRP": 0.000066, "ZEC": 0.000070, "SOL": 0.000084,
    "NEAR": 0.000113, "LIT": 0.000128, "PUMP": 0.000175,
    "ENA": 0.000197,
}
DEFAULT_FLOOR = 0.00007

HIST = "data_store/historical_candles.db"


def load(interval="15m"):
    """symbol -> [(ts_ms, open, high, low, close)] kronologis."""
    p = Path(HIST)
    if not p.exists():
        return {}
    conn = sqlite3.connect(HIST)
    out = defaultdict(list)
    for sym, ts, o, h, l, c in conn.execute(
        "SELECT symbol, ts, open, high, low, close FROM hist_candles "
        "WHERE interval=? ORDER BY symbol, ts", (interval,)
    ):
        out[sym].append((ts, o, h, l, c))
    conn.close()
    return dict(out)


def split_regimes(data, window_days=30):
    """
    Temukan jendela bullish dan bearish yang paling ekstrem.

    Versi lama membagi di titik yang menyeimbangkan |return A| + |return B|,
    dan itu salah: hasilnya 198 hari versus 11 hari, yang bukan pemisahan
    rezim melainkan pemisahan panjang.

    Yang benar: hitung return rata-rata lintas simbol untuk setiap
    jendela bergeser, lalu ambil jendela dengan return tertinggi dan
    terendah. Kalau keduanya rezim yang berbeda, keduanya harus punya
    arah berlawanan DAN durasi yang sama - itu yang dibedakan oleh
    `window_days` yang tetap.
    """
    daily = defaultdict(list)
    for sym, rows in data.items():
        if len(rows) < 100:
            continue
        for i in range(1, len(rows)):
            a, b = rows[i - 1][4], rows[i][4]
            if a > 0:
                day = rows[i][0] // 86_400_000
                daily[day].append((b - a) / a)

    days = sorted(daily)
    if len(days) < window_days * 2:
        return None, None

    per_day = [statistics.mean(daily[d]) for d in days]

    best_bull = best_bear = None
    for start in range(0, len(days) - window_days + 1):
        score = sum(per_day[start:start + window_days]) / window_days
        if best_bull is None or score > best_bull[0]:
            best_bull = (score, start)
        if best_bear is None or score < best_bear[0]:
            best_bear = (score, start)

    bull = set(days[best_bull[1]:best_bull[1] + window_days])
    bear = set(days[best_bear[1]:best_bear[1] + window_days])

    print(f"  jendela : {window_days} hari")
    print(f"  bullish : {best_bull[0]:+.4f}%/hari  "
          f"({days[best_bull[1]]} s/d {days[best_bull[1] + window_days - 1]})")
    print(f"  bearish : {best_bear[0]:+.4f}%/hari  "
          f"({days[best_bear[1]]} s/d {days[best_bear[1] + window_days - 1]})")
    print(f"  selisih : {abs(best_bull[0] - best_bear[0]) * 100:.2f}%/hari")
    return bull, bear


def cost_of(sym):
    return FLOOR.get(sym, DEFAULT_FLOOR) + IMPACT + TAKER


def backtest(data, day_filter, signal, hold_bars, sl, tp, risk=0.005, lev=10):
    """
    Backtest dengan entry dari `signal(sym, i, rows) -> 1 | -1 | 0`.
    Filter tanggal lewat `day_filter` (set hari atau None = semua).
    """
    eq = 10_000.0
    net = 0.0
    n = w = 0
    ws = ls = 0.0
    per_sym = defaultdict(float)

    for sym, rows in data.items():
        if len(rows) < 60:
            continue
        cost = cost_of(sym)
        closes = [r[4] for r in rows]
        i = 40
        last = len(rows) - 2
        while i < last:
            day = rows[i][0] // 86_400_000
            if day_filter is not None and day not in day_filter:
                i += 1
                continue
            d_ = signal(sym, i, rows)
            if d_ == 0:
                i += 1
                continue
            j = i + 1
            entry = rows[j][1]
            if entry <= 0:
                i += 1
                continue
            qty = (risk * eq * lev) / entry
            if d_ > 0:
                sl_p, tp_p = entry * (1 - sl), entry * (1 + tp)
            else:
                sl_p, tp_p = entry * (1 + sl), entry * (1 - tp)

            reason, ei = _simulate(rows, j, d_, sl_p, tp_p, hold_bars)
            if ei < 0:
                i += 1
                continue
            px = rows[ei][4]
            ef = px * (1 - cost) if d_ > 0 else px * (1 + cost)
            pnl = (ef - entry) * qty * d_ - (entry + ef) * qty * TAKER
            eq += pnl
            net += pnl
            n += 1
            per_sym[sym] += pnl
            if pnl > 0:
                w += 1
                ws += pnl
            else:
                ls += -pnl
            i = ei + 1

    pf = ws / ls if ls > 0 else float("inf")
    return {
        "n": n, "net": net, "pf": pf,
        "wr": (w / n if n else 0.0),
        "per_sym": dict(per_sym),
    }


def _simulate(rows, j, d_, sl_p, tp_p, hold_bars):
    for k in range(j + 1, min(len(rows), j + 1 + hold_bars)):
        h, l = rows[k][2], rows[k][3]
        if d_ > 0:
            sl_hit, tp_hit = l <= sl_p, h >= tp_p
        else:
            sl_hit, tp_hit = h >= sl_p, l <= tp_p
        # SL dicek lebih dulu: dalam satu bar, urutan high dan low tidak
        # diketahui, dan mengasumsikan yang menguntungkan membuat
        # backtest berbohong.
        if sl_hit:
            return "SL", k
        if tp_hit:
            return "TP", k
    if j + hold_bars < len(rows):
        return "EXPIRED", j + hold_bars
    return "", -1


def tstat(vals):
    if len(vals) < 3:
        return 0.0
    m = statistics.mean(vals)
    sd = statistics.stdev(vals)
    return m / (sd / math.sqrt(len(vals))) if sd else 0.0


if __name__ == "__main__":
    print("=" * 78)
    print("MEMBAGI DATA MENJADI DUA REZIM")
    print("=" * 78)
    # 1h, bukan 15m: 21 simbol x 208 hari vs 4 simbol x 52 hari.
    # Horizon yang diuji (hold 2 jam = 2 bar 1h) juga bekerja
    # pada 1h.
    data = load("1h")
    if not data:
        print("  historical_candles.db belum ada - jalankan "
              "research/fetch_historical.py dulu")
        raise SystemExit
    print(f"  simbol : {len(data)}")
    bars = sum(len(v) for v in data.values())
    print(f"  bar    : {bars}")
    A, B = split_regimes(data)
    if A is None:
        print("  data tidak cukup untuk membagi rezim")
        raise SystemExit
    print()
    print("=" * 78)
    print("STRUKTUR REZIM")
    print("=" * 78)
    for label, days in (("REZIM A", A), ("REZIM B", B)):
        syms = 0
        for s, rows in data.items():
            if any((r[0] // 86_400_000) in days for r in rows):
                syms += 1
        print(f"  {label}: {len(days)} hari, {syms} simbol")
    print()
    print("  >> Rezim dengan return positif = bullish, negatif = bearish.")
    print("     Edge yang nyata harus menang di KEDUA.")