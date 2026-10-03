"""
Uji kandidat terbaik di rezim BULLISH dan BEARISH yang benar-benar
terjadi di bursa.

Kandidat: SL 2% / TP 4% / long-only saat momentum > 0, hold 2 jam.

Di data lama (hanya periode bullish) kandidat ini terlihat sangat bagus:
PF test 2.04, t +2.25, positif di ketiga fold. Mirror test membalikkan
harga dan membunuhnya - tapi mirror test mengubah harga tanpa mengubah
distribusi volume, jadi masih ada pertanyaan: apakah candi asli di
periode bearish memberi jawaban yang sama?

Sekarang jawabannya bisa diuji langsung. Data: 21 simbol, 208 hari
dari Hyperliquid, dua jendela 30 hari yang dipilih karena return-nya
paling ekstrem - satu bullish (+0.06%/hari), satu bearish (-0.04%/hari).

Aturan: edge yang nyata harus positif di KEDUA. Edge yang hanya positif
di bullish adalah drift. Edge yang negatif di bearish bukan "netral" -
itu actively berbahaya, karena bisa山大 caldo di rezim yang salah.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

import regime_split as R


def momentum_signal(threshold=0.0):
    """Long saat momentum 21-batang naik di atas ambang."""
    def sig(sym, i, rows):
        n = len(rows)
        if i < 21 or n <= i:
            return 0
        a, b = rows[i - 21][4], rows[i][4]
        if a <= 0:
            return 0
        mom = (b - a) / a * 100.0
        return 1 if mom > threshold else 0
    return sig


def both_sides(threshold=0.15):
    """Long saat naik, short saat turun."""
    def sig(sym, i, rows):
        n = len(rows)
        if i < 21 or n <= i:
            return 0
        a, b = rows[i - 21][4], rows[i][4]
        if a <= 0:
            return 0
        mom = (b - a) / a * 100.0
        if mom > threshold:
            return 1
        if mom < -threshold:
            return -1
        return 0
    return sig


def trend_signal(sym, i, rows):
    """EMA cepat di atas EMA lambat."""
    n = len(rows)
    if i < 50 or n <= i:
        return 0
    window = [r[4] for r in rows[max(0, i - 49):i + 1]]
    k = 2.0 / 10.0
    e9, e21 = window[0], window[0]
    for v in window[1:]:
        e9 = v * k + e9 * (1 - k)
        k21 = 2.0 / 22.0
        e21 = v * k21 + e21 * (1 - k21)
    return 1 if e9 > e21 else 0


if __name__ == "__main__":
    data = R.load("1h")
    print(f"simbol: {len(data)}, bar: {sum(len(v) for v in data.values())}")
    print()
    bull, bear = R.split_regimes(data, window_days=30)
    print()

    STRATS = [
        ("momentum long", momentum_signal(0.0), 0.020, 0.040, 2),
        ("momentum long t.15", momentum_signal(0.15), 0.020, 0.040, 2),
        ("momentum dua arah", both_sides(0.15), 0.020, 0.040, 2),
        ("momentum dua arah t.3", both_sides(0.30), 0.020, 0.040, 2),
        ("EMA trend long", trend_signal, 0.020, 0.040, 2),
        ("EMA trend long", trend_signal, 0.015, 0.030, 4),
        ("EMA trend long", trend_signal, 0.010, 0.020, 8),
    ]

    print("=" * 86)
    print("KANDIDAT DI DUA REZIM NYATA")
    print("=" * 86)
    print(f"  {'strategi':<22} {'sl/tp/hold':<16} "
          f"{'BULL n':>7} {'BULL pf':>8} {'BULL/tr':>9} | "
          f"{'BEAR n':>7} {'BEAR pf':>8} {'BEAR/tr':>9}  verdict")
    print("  " + "-" * 104)

    for name, sig, sl, tp, hold in STRATS:
        tag = f"{sl*100:.0f}/{tp*100:.0f}%/{hold}h"
        b = R.backtest(data, bull, sig, hold, sl, tp)
        s = R.backtest(data, bear, sig, hold, sl, tp)

        bp = b["net"] / b["n"] if b["n"] else 0.0
        sp = s["net"] / s["n"] if s["n"] else 0.0

        if bp > 0 and sp > 0:
            verdict = "LOLOS di kedua"
        elif bp > 0 and sp < 0:
            verdict = "DRIFT (rugi di bearish)"
        elif bp < 0 and sp > 0:
            verdict = "menarik hanya di bearish"
        else:
            verdict = "rugi di kedua"

        print(f"  {name:<22} {tag:<16} "
              f"{b['n']:>7} {b['pf']:>8.3f} {bp:>+9.4f} | "
              f"{s['n']:>7} {s['pf']:>8.3f} {sp:>+9.4f}  {verdict}")

    print()
    print("=" * 86)
    print("CARA MEMBACA")
    print("=" * 86)
    print("  LOLOS di kedua        edge memprediksi arah, bukan cuma运气")
    print("  DRIFT                 hanya menang di pasar naik")
    print("  rugi di kedua         tidak ada edge sama sekali")
    print()
    print("  Perhatikan: PF di bearish bisa >1 dua arah. Yang penting")
    print("  adalah net per trade, dan apakah keduanya konsisten.")