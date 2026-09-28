"""
analysis/vol_target.py — Volatility targeting dan regime filter.

Dua komponen ini dipilih karena punya bukti empiris yang jauh lebih kuat
daripada indikator momentum jangka pendek:

1. **Volatility scaling.** Ukuran posisi = risiko / volatilitas. Ini
   satu-satunya bagian dari sistem ini yang langsung memindahkan
   ekspektasi ke rentang yang mendekati 1, dan komponen ini ada di setiap
   strategi trend-following yang terbukti di literatur
   (Moskowitz, Ooi & Pedersen 2012; Hurst, Ooi & Pedersen 2017).

2. **Regime filter.** Di pasar bergejolak, strategy yang biasa
   bekerja di pasar tenang Bursting Average. Menycaling position dengan
   kebalikan volatilitas GALAT di sini: yang perlu dikecilkan di volatilitas
   TINGGI adalah UKURAN POSISI, bukan frekuensi entry.

Yang TIDAK ada di modul ini dan sengaja tidak ditambahkan: indikator
   Semuanya
memiliki bukti yang jauh lebih tipis, dan pada horizon sub-detik efeknya
biasanya hilang di bawah biaya transaksi.
"""
from __future__ import annotations

import math
from typing import Dict, Optional, Tuple

from core.config import get_config
from core.logger import get_logger
from core.market_store import market_store

logger = get_logger("vol_target")

# Volatilitas tahunan yang dianggap "normal" untuk crypto perpetuals.
# Angka ini hanya dipakai sebagai titik tengah logaritmik, bukan sebagai
# prediksi.
REFERENCE_DAILY_VOL = 0.03  # 3% per hari


def daily_vol(symbol: str, window_seconds: float = 300.0) -> Optional[float]:
    """
    Volatilitas harian yang diestimasi dari tick.

    Mengembalikan None bila data tidak cukup — yang berarti "tidak tahu",
    bukan "tenang".
    """
    history = market_store.get_price_history(symbol, seconds=window_seconds)
    prices = [px for _, px in history if px and px > 0]
    if len(prices) < 20:
        return None

    returns = []
    for i in range(1, len(prices)):
        prev, curr = prices[i - 1], prices[i]
        if prev > 0 and curr > 0:
            returns.append(math.log(curr / prev))
    if len(returns) < 15:
        return None

    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    per_tick = math.sqrt(var)
    # Skala ke harian dengan asumsi tick ~0,3 detik.
    ticks_per_day = 86400.0 / 0.3
    return per_tick * math.sqrt(ticks_per_day)


def vol_ratio(symbol: str) -> Optional[float]:
    """
    Rasio volatilitas sekarang terhadap volatilitas normal.

    > 1 berarti pasar lebih liar dari biasa.
    """
    dv = daily_vol(symbol)
    if dv is None or dv <= 0:
        return None
    return dv / REFERENCE_DAILY_VOL


def target_risk_fraction(symbol: str,
                         base_risk: float = None,
                         max_ratio: float = 3.0) -> Optional[float]:
    """
    Posisi yang boleh diambil sebagai FRACTION dari risiko normal.

    Prinsipnya: risiko per trade harus tetap seragam, jadi ukurannya
    yang menyesuaikan volatilitas. Di pasar 3x lebih liar, size dikecilkan
    sampai ~3x lebih kecil.

    Return None bila volatilitas tidak bisa diestimasi — pemanggil harus
    memperlakukan ini sebagai "tidak boleh entry", bukan "pakai default".
    """
    cfg = get_config()
    if base_risk is None:
        base_risk = cfg.risk.max_risk_per_trade

    ratio = vol_ratio(symbol)
    if ratio is None:
        return None
    if ratio < 0.2:
        ratio = 0.2
    if ratio > max_ratio:
        # Di luar batas atas, perlakukan sebagai ratio maksimum. Mengurangi
        # dengan ratio tak beratas membuat size nol, yang sama dengan
        # bot berhenti.
        ratio = max_ratio
    return base_risk / ratio


def should_trade(symbol: str,
                 min_vol_ratio: float = 0.5,
                 max_vol_ratio: float = 3.0) -> Tuple[bool, str]:
    """
    Gate berbasis regime.

    Dua kondisi yang DITOLAK:
      * volatilitas terlalu rendah  -> pasar tidak bergerak, fee jadi
       -proporsi besar dari profit
      * volatilitas terlalu tinggi  -> noise mendominasi, stop akan
        tersapu

    Mengembalikan `(boleh, alasan)`. Alasan selalu diisi supaya log bisa
    menjelaskan kenapa entry dilewati.
    """
    ratio = vol_ratio(symbol)
    if ratio is None:
        return False, "volatilitas tidak bisa diestimasi"
    if ratio < min_vol_ratio:
        return False, "volatilitas terlalu rendah ({:.2f}x normal)".format(ratio)
    if ratio > max_vol_ratio:
        return False, "volatilitas terlalu tinggi ({:.2f}x normal)".format(ratio)
    return True, "regime oke ({:.2f}x normal)".format(ratio)
