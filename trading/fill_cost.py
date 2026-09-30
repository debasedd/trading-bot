"""
trading/fill_cost.py — Model biaya eksekusi yang DIPAKAI BERKAS.

Dulu biaya ini hanya hidup di `PaperTradingEngine._fill_price`, jadi hanya
order PEMBUKAAN yang membebankan spread dan impact. Setiap penutupan —
SL hit, TP hit, likuidasi, scalp TP, auto-close expired — menutup pada
harga `current_price` mentah, yaitu gratis. Akibatnya P&L paper menghitung
separuh biaya round trip saja.

Modul ini memindahkan perhitungan itu ke satu tempat supaya POSITION MANAGER
(yang menutup posisi) memakai model yang sama persis dengan mesin pembuka.
Arah biaya ikut dihitung di sini, dan itu bukan detail kecil: membebankan
harga ke sisi yang salah untuk SHORT membalik tandanya menjadi keuntungan.

Aturan yang berlaku di sini, dan tidak boleh dilanggar di tempat lain:

  * Angka di bawah adalah LANTAI, bukan target. `market_store` tidak pernah
    mengkedaluwarsakan order book, dan `get_order_book_age` mengembalikan
    `None` untuk book tanpa stempel — book dari sejam lalu tetap terbaca
    "valid". Karena itu jalur live hanya boleh MENAMBAH biaya lewat `max()`,
    tidak pernah `min()`. Book basi, book tanpa stempel, sentinel harga 0,
    dan book spread sempit semuanya jatuh ke lantai.

  * `side` adalah sisi FILL-nya, BUKAN sisi posisinya: "BUY" untuk buka LONG
    dan tutup SHORT, "SELL" untuk buka SHORT dan tutup LONG. Menebak di sini
    berarti membebankan biaya ke sisi yang salah.

  * Impact SELALU konstanta dan tidak pernah membaca book. Suku impact yang
    bergantung pada depth butuh pembagian dengan kedalaman book, dan kasus
    nolnya adalah crash yang sedang terjadi. `calculate_scalp_position_size`
    menakar dari saldo, bukan dari depth, jadi partisipasinya rutin besar
    terhadap book tipis. Konsekuensinya: model ini KURANG membebani order
    yang sangat besar. Itu keterbatasan yang diketahui, bukan bug.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

from core.market_store import market_store

logger = logging.getLogger("trading_bot.fill_cost")

# LANTAI, bukan target. Lihat docstring modul.
FILL_HALF_SPREAD_FLOOR = 0.0003             # 3 bps
FILL_IMPACT_FLOOR = 0.0001                  # 1 bps
FILL_BOOK_MALFORMED_SPREAD_PCT = 0.05       # 5% — di atas ini book rusak, bukan pasar
FILL_MAX_TOTAL_COST_PCT = 0.0050            # 50 bps, hanya jaring pengaman

#: Nama konstanta lama, dipertahankan supaya modul yang sudah mengimpornya
#: dari `trading.paper_engine` tidak ikut pecah. Definisi aslinya tinggal di
#: situ sebagai alias; kebenaran ada di sini.
__all__ = [
    "FILL_HALF_SPREAD_FLOOR",
    "FILL_IMPACT_FLOOR",
    "FILL_BOOK_MALFORMED_SPREAD_PCT",
    "FILL_MAX_TOTAL_COST_PCT",
    "fill_price_after_cost",
    "close_fill_price",
]


def _observed_half_spread(symbol: str, max_book_age_seconds: float) -> Tuple[Optional[float], Optional[float]]:
    """
    Setengah spread yang terukur dari book live, atau `None` kalau tidak bisa
    dibuktikan segar.

    Mengembalikan `(half_spread, book_age_seconds)`. `half_spread is None`
    berarti "tidak ada data yang bisa dipercaya" — bukan "spread-nya nol".
    """
    book = market_store.get_order_book(symbol)
    age = market_store.get_order_book_age(symbol)

    # `age is None` berarti TIDAK SEGAR, bukan "segar tanpa stempel" — usia
    # yang tidak diketahui bukan bukti kesegaran.
    if not book or age is None or age > max_book_age_seconds:
        return None, (float(age) if age is not None else None)

    bids, asks = book.get("bids") or [], book.get("asks") or []
    if not bids or not asks:
        return None, float(age)

    try:
        best_bid, best_ask = float(bids[0][0]), float(asks[0][0])
    except (TypeError, ValueError, IndexError):
        return None, float(age)

    # 0 adalah sentinel batas bursa (core/microstructure.py:99-101).
    # `mid <= 0` atau harga level <= 0 berarti book RUSAK, bukan spread
    # lebar — teorinya harus jatuh ke lantai, bukan ke 0.
    mid = (best_bid + best_ask) / 2.0
    if not (mid > 0 and best_bid > 0 and best_ask > 0):
        return None, float(age)

    observed = (best_ask - best_bid) / (2.0 * mid)

    # Tidak ada plafon atas yang "wajar": spread 2% itu nyata dan harus
    # dibayar penuh. Plafon hanya menangkap book rusak.
    if not (0.0 < observed <= FILL_BOOK_MALFORMED_SPREAD_PCT):
        return None, float(age)

    return observed, float(age)


def fill_price_after_cost(
    symbol: str,
    fill_side: str,
    ref_price: float,
    *,
    reason: str,
    max_book_age_seconds: float = 1.5,
    config: Any = None,
) -> Tuple[float, Dict[str, Any]]:
    """
    Harga fill setelah biaya menyeberang dibebankan, plus metadata biaya.

    Args:
        symbol: simbol untuk lookup book.
        fill_side: "BUY" atau "SELL" — sisi FILL, bukan sisi posisi.
        ref_price: harga acuan (mid / harga pasar terakhir).
        reason: label untuk jejak audit, mis. "OPEN_LONG" atau "SL_HIT".
        max_book_age_seconds: umur maksimum book yang masih dipercaya.
        config: opsional, dipakai untuk mengambil `max_tick_age_seconds`
            dari `ScalpingConfig` supaya UMUR observasi untuk book sama
            dengan yang dipakai tick guard. Dua anggaran basi yang
            terpisah pasti akan melenceng satu dari keduanya, dan yang
            melenceng itu akan diam-diam mempercayai data basi.

    Returns:
        `(fill_price, meta)`. `meta` ditulis ke `agent_logs` supaya auditor
        bisa menjawab "kemana uangnya pergi" dari log saja.
    """
    if config is not None:
        scalp_cfg = getattr(config, "scalping", None)
        if scalp_cfg is not None:
            max_book_age_seconds = float(
                getattr(scalp_cfg, "max_tick_age_seconds", max_book_age_seconds)
                or max_book_age_seconds
            )

    half = FILL_HALF_SPREAD_FLOOR
    source = "floor"
    book_age_s = None

    observed, book_age = _observed_half_spread(symbol, float(max_book_age_seconds))
    if observed is not None:
        # `max`, BUKAN `min`: jalur live hanya boleh menambah biaya. Book
        # sempit yang meyakinkan justru yang paling rawan basi (jarak
        # timestamp jauh, dikirim saat book sempat renggang), jadi
        # mempercayainya tanpa lantai akan mengembalikan biaya mendekati
        # nol — persis bug yang model ini dibuat untuk hilangkan.
        half = max(observed, FILL_HALF_SPREAD_FLOOR)
        source = "live_book"
        book_age_s = book_age

    # Impact SELALU konstanta, tidak pernah membaca book. Lihat docstring modul.
    impact = FILL_IMPACT_FLOOR

    total = min(half + impact, FILL_MAX_TOTAL_COST_PCT)

    # Arah biaya: BUY Paying more, SELL receiving less. Both work against you.
    signed = +total if str(fill_side).upper() == "BUY" else -total
    fill = float(ref_price) * (1.0 + signed)

    meta = {
        "reason": reason,
        "ref_price": float(ref_price),
        "fill_price": fill,
        "half_spread_pct": half,
        "impact_pct": impact,
        "total_cost_pct": total,
        "cost_source": source,
        "book_age_s": book_age_s,
    }
    return fill, meta


def close_fill_price(
    symbol: str,
    position_side: str,
    ref_price: float,
    *,
    reason: str,
    config: Any = None,
    max_book_age_seconds: float = 1.5,
) -> Tuple[float, Dict[str, Any]]:
    """
    Harga fill untuk MENUTUP posisi, dengan arah biaya yang benar.

    `position_side` adalah sisi POSISI ("LONG" / "SHORT"), bukan sisi fill.
    Menutup LONG = SELL, menutup SHORT = BUY. Konversinya di sini supaya
    pemanggil tidak pernah perlu mengingatinya — dan salah ingat di sini
    persis membalik tanda biaya, membuat penutupan SHORT terlihat untung.

    """
    fill_side = "SELL" if str(position_side).upper() == "LONG" else "BUY"
    return fill_price_after_cost(
        symbol,
        fill_side,
        ref_price,
        reason=reason,
        config=config,
        max_book_age_seconds=max_book_age_seconds,
    )


def describe_cost(meta: Optional[Dict[str, Any]]) -> str:
    """Ringkasan satu baris biaya dalam bps, untuk kolom `reasoning`."""
    if not meta:
        return "tidak ada (posisi tidak ditemukan)"
    try:
        bps = float(meta["total_cost_pct"]) * 10000.0
    except (KeyError, TypeError, ValueError):
        return "tidak ada"
    src = meta.get("cost_source", "?")
    return "{:.1f} bps (sumber: {})".format(bps, src)
