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
import math
from typing import Any, Dict, Optional, Tuple

from core.market_store import market_store

logger = logging.getLogger("trading_bot.fill_cost")

# LANTAI GLOBAL, bukan target. Lihat docstring modul.
#
# Angka 3 bps ini berasal dari config, BUKAN dari pengukuran. Pengukuran
# order book historis (research/spread_stability.py, 21.000 snapshot)
# menunjukkan dua hal yang membatalkan asumsi ini:
#
#   * Spread BERSEDARIAN antar simbol. Median 0.12 bps (BTC) sampai
#     1.97 bps (ENA) - rasio 16x. 3 bps terlalu BESAR untuk BTC dan
#     sampai terlalu kecil untuk ENA pada p95-nya 6.95 bps.
#
#   * Spread BERKORELASI dengan volatilitas. Simbol bergejolak punya
#     median spread 1.72 bps vs 0.37 bps untuk yang tenang - rasio
#     4.64x. Jadi 3 bps konstan terlalu optimistic PADA SAAT spread
#     paling mahal, dan itulah saat kerugian paling besar.
#
# Floor global tetap ada sebagai jaring pengaman untuk book basi atau
# hilang, dan `FILL_HALF_SPREAD_FLOOR_BY_SYMBOL` lebih rendah untuk
# simbol yang spread-nya memang tipis. Yang menentukan adalah book
# live; floor hanya berlaku kalau book tidak bisa dipercaya.
FILL_HALF_SPREAD_FLOOR = 0.0003             # 3 bps - jaring pengaman global
FILL_IMPACT_FLOOR = 0.0001                  # 1 bps
FILL_BOOK_MALFORMED_SPREAD_PCT = 0.05       # 5% — di atas ini book rusak, bukan pasar
FILL_MAX_TOTAL_COST_PCT = 0.0050            # 50 bps, hanya jaring pengaman

#: Floor spread per simbol, dari median terukur (research/spread_stability.py).
#:
#: Dipakai SEBAGAI FLOOR, bukan sebagai pengganti pembacaan book live. Kalau
#: book live bisa dibaca, spread yang dipakai adalah yang terukur. Floor ini
#: hanya berlaku saat book tidak tersedia atau basi - dan di saat itu,
#: memakai median simbol yang lebih akurat daripada 3 bps untuk semua.
#:
#: Nilai ini akan meleset seiring likuiditas berubah, jadi diperbarui dari
#: data yang sedang terkumpul, bukan yang dibekukan selamanya.
FILL_HALF_SPREAD_FLOOR_BY_SYMBOL: Dict[str, float] = {
    "BTC": 0.000012,    # 0.12 bps
    "HYPE": 0.000012,   # 0.12 bps
    "ETH": 0.000037,    # 0.37 bps
    "XRP": 0.000066,    # 0.66 bps
    "ZEC": 0.000070,    # 0.70 bps
    "SOL": 0.000084,    # 0.84 bps
    "NEAR": 0.000113,   # 1.13 bps
    "LIT": 0.000128,    # 1.28 bps
    "PUMP": 0.000175,   # 1.75 bps
    "ENA": 0.000197,    # 1.97 bps
}

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

    # Floor per-simbol, bukan global. Lihat catatan di
    # `FILL_HALF_SPREAD_FLOOR_BY_SYMBOL`: median spread antar simbol beda
    # 16x, jadi memakai 3 bps untuk BTC yang spread-nya 0.12 bps
    # overcharge-nya 25x - dan overcharge itu menghapus edge yang secara empiris hanya 0.18 per trade.
    base = symbol.split("/")[0].split(":")[0].upper()
    floor = FILL_HALF_SPREAD_FLOOR_BY_SYMBOL.get(base, FILL_HALF_SPREAD_FLOOR)
    half = floor
    source = "floor"
    book_age_s = None

    observed, book_age = _observed_half_spread(symbol, float(max_book_age_seconds))
    if observed is not None:
        # `max`, BUKAN `min`: jalur live hanya boleh menambah biaya. Book
        # sempit yang meyakinkan justru yang paling rawan basi (jarak
        # timestamp jauh, dikirim saat book sempat renggang), jadi
        # mempercayainya tanpa lantai akan mengembalikan biaya mendekati
        # nol — persis bug yang model ini dibuat untuk hilangkan.
        half = max(observed, floor)
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


#: Periode funding Hyperliquid, dalam detik.
#:
#: config.yaml menulis `funding_rate: 28800` dengan komentar "8 jam".
#: Itu salah: Hyperliquid melakukan settlement funding setiap JAM, bukan
#: 8 jam seperti Binance. Salah periode membagi biaya funding dengan 8,
#: jadi bot melihat biaya delapan kali lebih murah dari kenyataan.
#:
#: Nilainya bukan di config karena `agent_intervals.funding_rate` mengatur
#: SEBERAPA SERING rate-nya di-refresh, bukan periode settlement-nya. Dua
#: hal berbeda yang kebetulan sama-sama disebut "funding rate".
FUNDING_PERIOD_SECONDS = 3600.0

#: Lantai untuk rate funding, sebagai fraksi per periode.
#:
#: Rate yang mendekati nol berarti pasar belum menunda dan membebankan
#: funding yang benar-benar nol adalah benar. Tapi rate absurd kecil juga
#: bisa jadi data rusak, jadi ada lantai: di bawahnya, ukuran posisi
#: yang menjelaskan pergerakan, bukan rate-nya.
FUNDING_RATE_FLOOR = 1e-7

#: Batas atas rate funding per periode. Rate di atas ini tidak pernah
#: terjadi di pasar nyata; kalau terbaca, itu data salah, dan membebankan
#: angka itu akan menghapus seluruh P&L posisi dalam satu-detik.
FUNDING_RATE_CEILING = 0.01


def funding_cost(
    position_side: str,
    notional: float,
    held_seconds: float,
    funding_rate: Optional[float] = None,
) -> Tuple[float, Dict[str, Any]]:
    """
    Biaya funding untuk posisi yang ditutup, dalam mata uang.

    Perpetual futures tidak punya expiry; biayanya dibayar dari posisi yang
    masih terbuka. Tidak menghitungnya berarti biaya yang dilaporkan
    SELALU terlalu kecil - dan untuk strategi yang hold-nya jauh lebih
    lama dari satu periode, itu bukan selisih kecil.

    Arahnya: funding positif berarti Long membayar dan Short menerima.
    Jadi biaya bertanda positif untuk LONG dan negatif untuk SHORT.

    Dihitung di waktu TUTUP, bukan akrual per periode. Akrual periodik
    butuh state yang bertahan di setiap titik kegagalan - crash di tengah
    periode berarti biaya yang hilang atau dibayar dua kali. Membebankan
    seluruh akrual saat tutup membuat biaya jadi bagian terikat dari
    P&L: kalau proses mati, biaya yang belum dibayar hilang bersama
    posisinya, bukan terkirim dua kali.

    `funding_rate` boleh None (data belum diterima) dan hasilnya 0.0.
    Menebak rate dari default berarti membebankan angka yang tidak pernah
    bisa diverifikasi; tidak membebankan apa pun berarti dilaporkan
    jujur bahwa angka itu belum diketahui.
    """
    meta: Dict[str, Any] = {
        "held_seconds": float(held_seconds),
        "periods": 0.0,
        "funding_rate": None,
        "gross_cost": 0.0,
        "note": "tidak ada (tidak ada periode settlement yang selesai)",
    }

    if funding_rate is None:
        return 0.0, meta

    try:
        rate = float(funding_rate)
    except (TypeError, ValueError):
        return 0.0, meta

    # Data rusak dibuang, bukan dipakai. Rate di luar batas bukan pasar
    # yang ekstrem, itu feed yang salah.
    #
    # Rate NOL adalah rate yang valid dan diketahui: pasar tidak menunda.
    # Lantai hanya berlaku untuk rate NONZERO yang terlalu kecil, karena di
    # situ kita tidak bisa membedakan "funding sangat tipis" dari "feed
    # belum mengisi". Menolak nol secara tidak sengaja membuat meta
    # melaporkan periode 0 untuk posisi yang jelas sudah melewati
    # settlement, dan laporan itu terlihat seperti "belum ada funding"
    # alih-alih "funding-nya memang nol".
    #
    # Batas bawah dibuat longgar sedikit dari `FUNDING_RATE_FLOOR`: rate
    # yang TEPAT di floor tiba di sini sebagai hasil pembagian floating
    # point, yang bisa sedikit di bawahnya, dan perbandingan `<=` yang
    # ketat akan membuangnya. Safer untuk membebankan rate yang sangat
    # kecil daripada membuang rate yang sah.
    if abs(rate) > 0.0 and abs(rate) < FUNDING_RATE_FLOOR * 0.99:
        return 0.0, meta
    if abs(rate) > FUNDING_RATE_CEILING:
        return 0.0, meta

    periods = max(0.0, float(held_seconds)) / FUNDING_PERIOD_SECONDS

    # Periode pecahan dibayar penuh di periode yang memotongnya. Bursa
    # tidak membagi prorata; memprosesnya secara proporsional akan
    # undercharge setiap posisi yang ditutup di antara dua settlement, dan
    # itu mayoritas posisi.
    #
    # `ceil` dan bukan `int(x) + 1`: untuk 2.0 periode persis, `int+1`
    # menghasilkan 3 dan membebankan satu periode yang belum settlement.
    # `ceil` memberi 2 untuk 2.0 dan 3 untuk 2.5 — benar keduanya, dan
    # 0.0 detik tetap 0, bukan 1.
    settled = float(math.ceil(periods)) if periods > 0 else 0.0

    notional = abs(float(notional))
    # Long MEMBAYAR saat rate positif; Short membayar saat rate negatif.
    # Tanda di sini adalah tanda biaya, bukan tanda arus dana: untuk
    # LONG, rate positif menghasilkan biaya positif, persis seperti fee.
    signed = rate if str(position_side).upper() == "LONG" else -rate
    gross = notional * rate * settled
    cost = notional * signed * settled

    meta.update({
        "periods": settled,
        "funding_rate": rate,
        "gross_cost": gross,
        "signed_cost": cost,
        "note": "{:.2f} periode @ {:+.4%}".format(settled, rate),
    })
    return cost, meta


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
