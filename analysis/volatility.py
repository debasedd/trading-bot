"""
analysis/volatility.py — Target dinamis berbasis volatilitas (ATR + realized vol).

Mengubah SL/TP dari persentase statis menjadi adaptif terhadap kondisi pasar.
Pada scalping, SL 0.25% berarti sesuatu yang sangat berbeda tergantung on
volatilitas:

* Gates calm (ATR 1m kecil): SL 0.25% adalah ruang gerak yang wajar, dan
  TP 0.60% tercapai relatif sering.
* Gates bergejolak (ATR 1m besar): SL 0.25% akan tersapu oleh noise — posisi
  menutup di SL bukan karena arah salah, tapi karena satu tickMqnormal.
  Di sini SL harus melebar, dan TP ikut menyesuaikan agar risk/reward tetap
  terjaga.

Filosofi: stop loss yang terlalu rapat bukan "hemat kerugian", itu cara
tercepat untuk membayar dua fee dan keluar di tengah noise.

Modul ini murni: tidak ada I/O selain membaca `market_store` (in-memory).
Fungsi-fungsinya bisa diuji tanpa database, tanpa jaringan, tanpa feed.
"""

import math
from typing import Dict, List, Optional, Tuple

from core.config import get_config
from core.logger import get_logger
from core.market_store import market_store

logger = get_logger("volatility")


# ===========================================================================
# 1. Realized Volatility (tick-based, 30 detik)
# ===========================================================================


def realized_volatility(symbol: str, window_seconds: float = 30.0) -> Optional[float]:
    """
    Volatilitas terealisasi dari tick (std deviasi log-return per tick).

    Mengembalikan None bila data tidak cukup.

    **Kenapa None dan bukan 0.0:** nol berarti "pasar benar-benar diam",
    yang berbeda dari "kita tidak tahu". Mengembalikan 0.0 untuk data kosong
    akan membuat guard menganggap pasar sedang tenang dan melepas SL rapat —
    justru pada saat data sedang tidak bisa dipercaya. `None` memaksa
    pemanggil turun ke default statis, yang perilakunya bisa diprakirakan.
    """
    history = market_store.get_price_history(symbol, seconds=window_seconds)
    prices = [px for _, px in history if px and px > 0]
    if len(prices) < 5:
        # 5 tick minimum: dengan 3-4 titik, std deviasi yang dihitung lebih
        # mencerminkan sampling noise daripada volatilitas pasar.
        return None

    returns = []
    for i in range(1, len(prices)):
        prev, curr = prices[i - 1], prices[i]
        if prev > 0 and curr > 0:
            returns.append(math.log(curr / prev))
    if len(returns) < 4:
        return None

    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    vol = math.sqrt(variance)
    return vol if vol > 0 else None


def realized_vol_pct(symbol: str, window_seconds: float = 30.0) -> Optional[float]:
    """
    Realized volatility sebagai fraksi harga, mis. 0.0008 = 0.08% per tick.

    Ini yang dipakai sebagai feature volatilitas, karena bisa langsung
    dibandingkan dengan `ATR_1m_pct` tanpa konversi tambahan.
    """
    vol = realized_volatility(symbol, window_seconds=window_seconds)
    if vol is None:
        return None
    price = market_store.get_price(symbol)
    if not price or price <= 0:
        return None
    return vol * price


# ===========================================================================
# 2. ATR 1 menit
# ===========================================================================


def atr_1m_from_candles(candles: List[dict], period: int = 14) -> Optional[Dict[str, float]]:
    """
    Hitung ATR dari list candle 1 menit (dict dengan high/low/close).

    Mengembalikan dict {atr, atr_pct, close, samples}, atau None bila data
    kurang.

    **Metode Wilder smoothing**, sama seperti `ta.atr` pandas-ta — biar
    angkanya cocok dengan indikator yang sudah dipakai di
    `analysis/technical.py`. Memakai simple moving average di sini akan
    menghasilkan ATR yang berbeda dari yang tampil di chart, dan trader yang
    membandingkan keduanya akan melihat selisih yang sulit dijelaskan.

    True Range:
        TR = max(high - low, |high - prev_close|, |low - prev_close|)
    """
    if not candles or len(candles) < period + 1:
        return None

    # Urut dari lama ke baru. Caller sering memberi DESC dari SQL, jadi
    # urutannya dinormalkan di sini — bukan dipaksa setiap caller ingat.
    rows = sorted(candles, key=lambda c: c.get("timestamp", 0))
    if len(rows) < period + 1:
        return None

    trs = []
    for i in range(1, len(rows)):
        h = float(rows[i].get("high") or 0.0)
        low = float(rows[i].get("low") or 0.0)
        prev_close = float(rows[i - 1].get("close") or 0.0)
        if h <= 0 or low <= 0 or prev_close <= 0:
            continue
        trs.append(max(
            h - low,
            abs(h - prev_close),
            abs(low - prev_close),
        ))

    if len(trs) < period:
        return None

    # Wilder: rata-rata pertama dari `period` TR, lalu dismooth rekursif.
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period

    close = float(rows[-1].get("close") or 0.0)
    if close <= 0:
        return None

    return {
        "atr": atr,
        "atr_pct": atr / close,
        "close": close,
        "samples": len(trs),
    }


# Cache ATR supaya tidak query DB setiap order. Volume kunci:
# (symbol, bucket waktu). ATR berubah lambat — antar menit — jadi cache
# beberapa detik tidak membuat angka basi, hanya menghemat query.
_ATR_CACHE: Dict[Tuple[str, int], Dict[str, float]] = {}
_ATR_CACHE_BUCKET_SECONDS = 5
_ATR_CACHE_MAX_ENTRIES = 256


def _cache_key(symbol: str) -> Tuple[str, int]:
    import time
    bucket = int(time.time() // _ATR_CACHE_BUCKET_SECONDS)
    return (symbol, bucket)


def atr_1m_pct(symbol: str, candles: Optional[List[dict]] = None,
               period: int = 14) -> Optional[float]:
    """
    ATR 1m sebagai fraksi harga, mis. 0.0035 = 0.35%.

    `candles` opsional: kalau diberikan, dipakai langsung. Kalau tidak,
    fungsi mencoba memuatnya lewat repo yang di-cache di modul ini
    (`set_candle_source`).

    Mengembalikan None bila tidak ada cukup candle — pemanggil wajib
    memperlakukannya sebagai "tidak tahu", bukan sebagai 0.
    """
    if candles is None:
        # PERLU `period + 1` candle, bukan `period`. ATR Wilder menghitung
        # true range memakai satu candle SEBELUMNYA sebagai pembanding
        # (`prev_close`), jadi N candle hanya menghasilkan N-1 true range.
        # Meminta tepat `period` candle membuat `len(trs) = period - 1`, yang
        # selalu gagal ambang `len(trs) < period` — dan akibatnya ATR selalu
        # None, sehingga seluruh target dinamis diam-diam mati di produksi
        # meski test yang menyuntik `candles=` secara langsung tetap hijau.
        candles = _load_candles(symbol, period + 1)

    if not candles:
        return None

    key = _cache_key(symbol)
    cached = _ATR_CACHE.get(key)
    if cached is not None:
        return cached["atr_pct"]

    result = atr_1m_from_candles(candles, period=period)
    if result is None:
        return None

    if len(_ATR_CACHE) >= _ATR_CACHE_MAX_ENTRIES:
        _ATR_CACHE.clear()
    _ATR_CACHE[key] = result
    return result["atr_pct"]


def clear_volatility_cache() -> None:
    """Kosongkan cache ATR. Dipanggil test."""
    _ATR_CACHE.clear()


# ===========================================================================
# 3. Sumber data candle
# ===========================================================================
#
# `Repository` asynchronous, sementara modul volatilitas ini sinkron dan murni.
# Jembatannya: pemanggil (PaperTradingEngine) mendaftarkan sebuah callback
# sinkron yang sudah memegang cache candle sendiri. Modul ini tidak pernah
# menyentuh event loop — itu justru yang membuat modul ini bisa diuji
# tanpa async.
#
# Kalau tidak ada sumber yang didaftarkan, `atr_1m_pct` mengembalikan None
# dan target jatuh ke default statis. Itu perilaku yang benar: lebih baik
# memakai angka statis yang bisa dijelaskan daripada mengarang ATR.

_CANDLE_SOURCE = None  # callable(symbol, timeframe, limit) -> list[dict]


def set_candle_source(fn) -> None:
    """
    Daftarkan sumber candle sinkron untuk perhitungan ATR.

    `fn` menerima (symbol, timeframe, limit) dan mengembalikan list of dict
    candle, atau None/list kosong bila tidak ada. Dipanggil di thread
    executor oleh pemanggil, bukan di event loop.

    ⚠️ Cache ATR SELALU dikosongkan di sini. Nilai yang di-cache dihitung dari
    sumber yang LAMA; mempertahankan cache saat sumber diganti berarti
    menghitung ATR dari data yang sudah tidak berlaku — hasil yang terlihat
    masuk akal tapi salah, dan jauh lebih sulit DIDETEKSI daripada None.
    """
    global _CANDLE_SOURCE
    _CANDLE_SOURCE = fn
    _ATR_CACHE.clear()


def clear_candle_source() -> None:
    """Lepas sumber candle. Dipanggil test."""
    global _CANDLE_SOURCE
    _CANDLE_SOURCE = None


def _load_candles(symbol: str, limit: int) -> Optional[List[dict]]:
    if _CANDLE_SOURCE is None:
        return None
    try:
        return _CANDLE_SOURCE(symbol, "1m", limit)
    except Exception as exc:
        # Sumber candle yang gagal adalah masalah operasional, bukan alasan
        # menjatuhkan sistem. None memaksa fallback statis.
        logger.debug(f"Gagal memuat candle 1m untuk {symbol}: {exc}")
        return None


# ===========================================================================
# 4. Target dinamis dengan penguncian Risk-to-Reward
# ===========================================================================


def get_dynamic_tp_sl_thresholds(symbol: str, cfg=None) -> Dict[str, Optional[float]]:
    """
    Hitung SL/TP dinamis untuk satu simbol.

    Formula:
        SL_pct = clamp(ATR_mult × ATR_1m, min_sl_pct, max_sl_pct)
        TP_pct = max(SL_pct × min_rr, min_profit_pct)

    Mengembalikan dict berisi sl_pct, tp_pct, static_sl_pct, static_tp_pct,
    atr_pct, realized_vol, used_dynamic, reason, dan (bila dinamis aktif)
    raw_sl_pct serta clamped.

    **Kapan jatuh ke statis:** ATR tidak tersedia (candle kurang, feed baru
    hidup, atau sumber candle tidak terdaftar). Itu bukan kegagalan — default
    statis adalah perilaku yang bisa dijelaskan, dan sistem tidak boleh
    berhenti bertransaksi hanya karena satu indikator belum siap.

    **Kenapa TP memakai max() dan bukan langsung SL × RR:** kalau SL melebar
    karena volatilitas, TP harus ikut naik — kalau tidak, risk/reward-nya
    anjlok di bawah yang dijanjikan, dan itu persis yang tidak boleh terjadi.
    Sebaliknya, di pasar tenang SL tetap di `min_sl_pct` dan TP mengikuti
    `min_profit_pct` supaya target tidak ikut menyusut ke bawah biaya.
    """
    if cfg is None:
        cfg = get_config()

    scalp = getattr(cfg, "scalping", None)
    dyn = getattr(cfg, "dynamic_tp_sl", None)

    static_sl = float(getattr(scalp, "tight_sl_pct", 0.0025) or 0.0025) if scalp else 0.0025
    static_tp = float(getattr(scalp, "fast_tp_pct", 0.0060) or 0.0060) if scalp else 0.0060

    result = {
        "sl_pct": static_sl,
        "tp_pct": static_tp,
        "static_sl_pct": static_sl,
        "static_tp_pct": static_tp,
        "atr_pct": None,
        "realized_vol": None,
        "used_dynamic": False,
        "reason": "statis (ATR belum tersedia)",
    }

    # Dinamis dimatikan secara eksplisit?
    if dyn is not None and not getattr(dyn, "enabled", False):
        result["reason"] = "dinamis dimatikan di config"
        return result

    atr_period = int(getattr(dyn, "atr_period", 14) or 14) if dyn else 14
    window = float(getattr(dyn, "realized_window_seconds", 30.0) or 30.0) if dyn else 30.0

    atr = atr_1m_pct(symbol, period=atr_period)
    result["atr_pct"] = atr
    result["realized_vol"] = realized_vol_pct(symbol, window_seconds=window)

    if atr is None or atr <= 0:
        return result

    atr_mult = float(getattr(dyn, "atr_multiple", 1.5) or 1.5) if dyn else 1.5
    min_sl = float(getattr(dyn, "min_sl_pct", 0.0025) or 0.0025) if dyn else 0.0025
    max_sl = float(getattr(dyn, "max_sl_pct", 0.015) or 0.015) if dyn else 0.015
    min_rr = float(getattr(dyn, "min_risk_reward", 1.5) or 1.5) if dyn else 1.5
    min_profit = float(getattr(scalp, "min_profit_pct", 0.0060) or 0.0060) if scalp else 0.0060

    raw_sl = atr_mult * atr
    sl_pct = min(max(raw_sl, min_sl), max_sl)
    tp_pct = max(sl_pct * min_rr, min_profit)

    result["sl_pct"] = sl_pct
    result["tp_pct"] = tp_pct
    result["used_dynamic"] = True
    result["raw_sl_pct"] = raw_sl
    result["clamped"] = sl_pct != raw_sl
    result["reason"] = (
        f"ATR_1m {atr:.4%} x{atr_mult:g} -> SL {sl_pct:.4%}"
        f"{' (clamped)' if result['clamped'] else ''}, "
        f"TP {tp_pct:.4%} (R:R {tp_pct / sl_pct:.2f})"
    )
    return result


def assess_volatility_gate(
    symbol: str,
    sl_pct: float,
    tp_pct: float,
    cfg=None,
) -> Optional[str]:
    """
    Volatility circuit breaker: apakah target yang dihitung masih layak dieksekusi.

    Mengembalikan string alasan penolakan, atau None bila target lolos.

    Fungsi ini adalah pasangan dari `get_dynamic_tp_sl_thresholds`: modul itu
    menghitung angkanya, modul ini memutuskan apakah angka itu masih masuk
    akal secara ekonomi setelah fee.

    Dua kondisi yang membatalkan order:

    1. **Fee memakan seluruh target.** Kalau `tp_pct <= roundtrip`, setiap
       trade pasti merugi apa pun arahnya. Ini bukan skenario sulit — ini
       kepastian, dan lonjakan volatilitas adalah kasus yang paling mungkin
       membuatnya terjadi.

    2. **Risk/reward tidak lagi impas.** Kalau setelah fee, TP tidak menutup
       lebih besar dari SL, win rate yang dibutuhkan impas melebihi yang bisa
       dicapai sinyal mana pun. Dievaluasi terhadap batas terkonfigurasi,
       bukan angka tetap.

    Kenapa pemeriksaan ini berada di sini, bukan di validator boot: validator
    boot menilai *konfigurasi*, sedangkan ini menilai *kondisi pasar saat
    ini*. Volatilitas tidak pernah statis, jadi tidak bisa dinilai sekali
    lalu dianggap sudah selesai.
    """
    if cfg is None:
        cfg = get_config()

    fees = getattr(cfg, "fees", None)
    taker = float(getattr(fees, "taker", 0.0005) or 0.0005) if fees else 0.0005
    roundtrip = taker * 2

    if tp_pct <= roundtrip:
        return (
            f"volatilitas gate: TP {tp_pct:.4%} <= fee roundtrip "
            f"{roundtrip:.4%} — setiap trade pasti merugi"
        )

    dyn = getattr(cfg, "dynamic_tp_sl", None)
    max_breakeven = (
        float(getattr(dyn, "max_breakeven_win_rate", 0.65) or 0.65) if dyn else 0.65
    )

    net_tp = tp_pct - roundtrip
    net_sl = sl_pct + roundtrip
    if net_sl <= 0:
        return "volatilitas gate: SL bersih tidak positif"

    if net_tp < net_sl:
        breakeven_wr = net_sl / (net_tp + net_sl)
        if breakeven_wr > max_breakeven:
            return (
                f"volatilitas gate: R:R bersih 1:{net_tp / net_sl:.2f} "
                f"(TP {net_tp:+.2%} vs SL -{net_sl:.2%}) butuh win rate "
                f"{breakeven_wr:.1%} untuk impas, melebihi batas "
                f"{max_breakeven:.0%}"
            )

    return None
