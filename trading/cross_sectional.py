"""
trading/cross_sectional.py - Strategi cross-sectional momentum.

EDGE YANG TERVERIFIKASI:
  - Momentum trailing 12h, hold 12h, 5 long + 5 short
  - t=2.34, p=0.000 vs 500 random baselines
  - Out-of-sample (2nd half): t=2.11
  - Reversed: t=-4.25 (konfirmasi searah)
  - 5/7 bulan positif, max drawdown 8.3%
  - Net +4.460 USDT dari 10.000 dalam 208 hari (tanpa leverage)

SYARAT KRITIS:
  - HARUS mendapat maker fill (1.5 bps fee). Taker fee (4.5 bps)
    menghancurkan edge (t turun dari 2.34 ke 0.60).
  - Oleh karena itu strategi ini memposting LIMIT ORDERS, bukan market.
  - Rebalance tiap 12 jam memberikan cukup waktu untuk limit fill.

ARSITEKTUR:
  - Modul ini TIDAK menggantikan DecisionAgent. Ia menyediakan sinyal
    ke DecisionAgent lewat event bus, sama seperti DirectionEnsemble.
  - DecisionAgent mengubah sinyal jadi Order, RiskManager menghitung
    sizing, engine mengeksekusi.
  - Yang berubah: sinyal sekarang berisi DAFTAR posisi yang harus
    dipegang (portfolio target), bukan sinyal per-simbol.
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from core.market_store import market_store

logger = logging.getLogger("trading_bot.cross_sectional")

# Strategi parameters - dari riset, JANGAN diubah tanpa re-validasi.
TRAIL_HOURS = 12
HOLD_HOURS = 12
N_SIDE = 5           # 5 long + 5 short
REBAL_INTERVAL_S = HOLD_HOURS * 3600

# Cost constants
MAKER_FEE = 0.00015  # 1.5 bps - Hyperliquid base tier maker
SPREAD_FLOOR = 0.00007  # 0.7 bps


@dataclass
class PortfolioTarget:
    """Target portofolio dari cross-sectional ranking."""
    longs: List[str]      # simbol yang harus di-long
    shorts: List[str]     # simbol yang harus di-short
    scores: Dict[str, float]  # momentum score per simbol
    timestamp: float      # kapan ranking dihitung
    trail_hours: int = TRAIL_HOURS
    hold_hours: int = HOLD_HOURS


@dataclass
class PriceHistory:
    """Simpan harga per simbol untuk trailing momentum."""
    prices: Dict[str, List[Tuple[float, float]]] = field(
        default_factory=lambda: defaultdict(list)
    )  # symbol -> [(timestamp_s, price), ...]
    max_history_hours: int = 80  # simpan lebih dari trail

    def record(self, symbol: str, price: float, ts: Optional[float] = None):
        """Catat harga baru."""
        if ts is None:
            ts = time.time()
        self.prices[symbol].append((ts, price))
        # Prune old
        cutoff = ts - self.max_history_hours * 3600
        self.prices[symbol] = [
            (t, p) for t, p in self.prices[symbol] if t > cutoff
        ]

    def get_trailing_return(self, symbol: str, trail_hours: int,
                            now: Optional[float] = None) -> Optional[float]:
        """Return trailing dalam persen, atau None kalau data kurang."""
        if now is None:
            now = time.time()
        pts = self.prices.get(symbol, [])
        if not pts:
            return None

        # Harga sekarang: titik terakhir
        current = pts[-1][1]
        if current <= 0:
            return None

        # Harga trail_hours lalu: cari titik terdekat
        target_ts = now - trail_hours * 3600
        best = None
        for t, p in pts:
            if best is None or abs(t - target_ts) < abs(best[0] - target_ts):
                best = (t, p)

        if best is None or best[1] <= 0:
            return None

        # Harus cukup dekat (dalam 2 jam dari target)
        if abs(best[0] - target_ts) > 2 * 3600:
            return None

        return (current - best[1]) / best[1] * 100.0


class CrossSectionalStrategy:
    """
    Strategi cross-sectional momentum.

    Setiap REBAL_INTERVAL_S:
    1. Hitung trailing return semua simbol
    2. Rank dari terendah ke tertinggi
    3. Long N_SIDE teratas, Short N_SIDE terbawah
    4. Kirim PortfolioTarget ke DecisionAgent
    """

    def __init__(self, symbols: List[str]):
        self.symbols = symbols
        self.history = PriceHistory()
        self.last_rebalance: float = 0
        self.current_target: Optional[PortfolioTarget] = None
        self._initialized = False

    def record_price(self, symbol: str, price: float,
                     ts: Optional[float] = None):
        """Dipanggil setiap kali harga baru masuk."""
        self.history.record(symbol, price, ts)

    def needs_rebalance(self, now: Optional[float] = None) -> bool:
        """Apakah sudah waktunya rebalance?"""
        if now is None:
            now = time.time()
        return (now - self.last_rebalance) >= REBAL_INTERVAL_S

    def compute_target(self, now: Optional[float] = None) -> Optional[PortfolioTarget]:
        """
        Hitung portfolio target baru.

        Return None kalau data kurang (belum 12 jam sejak start).
        """
        if now is None:
            now = time.time()

        # Hitung trailing return semua simbol
        ranked = []
        for sym in self.symbols:
            ret = self.history.get_trailing_return(sym, TRAIL_HOURS, now)
            if ret is None:
                continue
            # Ambil harga terkini untuk referensi
            pts = self.history.prices.get(sym, [])
            if not pts:
                continue
            ranked.append((sym, ret))

        if len(ranked) < N_SIDE * 2 + 2:
            logger.warning(
                f"Cross-sectional: hanya {len(ranked)} simbol dengan data "
                f"cukup, butuh minimal {N_SIDE * 2 + 2}"
            )
            return None

        # Sort: terendah ke tertinggi
        ranked.sort(key=lambda r: r[1])

        # Long top momentum, Short bottom momentum
        longs = [s for s, _ in ranked[-N_SIDE:]]
        shorts = [s for s, _ in ranked[:N_SIDE]]
        scores = {s: sc for s, sc in ranked}

        target = PortfolioTarget(
            longs=longs,
            shorts=shorts,
            scores=scores,
            timestamp=now,
        )

        self.current_target = target
        self.last_rebalance = now

        logger.info(
            f"Cross-sectional rebalance: "
            f"LONG {longs} (top momentum), "
            f"SHORT {shorts} (bottom momentum), "
            f"from {len(ranked)} symbols"
        )

        return target

    def get_desired_positions(self) -> Dict[str, int]:
        """
        Return dict {symbol: direction} untuk semua posisi yang seharusnya
        ada.  direction: +1 = long, -1 = short, 0 = flat.

        DecisionAgent bisa membandingkan ini dengan posisi aktif dan
        mengeluarkan order yang diperlukan.
        """
        if self.current_target is None:
            return {}

        desired = {}
        for sym in self.current_target.longs:
            desired[sym] = +1
        for sym in self.current_target.shorts:
            desired[sym] = -1

        # Simbol yang tidak ada di target harus di-flat-kan
        for sym in self.symbols:
            if sym not in desired:
                desired[sym] = 0

        return desired

    def get_position_sizing_info(self) -> Dict:
        """Info untuk sizing: berapa banyak kaki, berapa alokasi per kaki."""
        return {
            "n_long": N_SIDE,
            "n_short": N_SIDE,
            "total_legs": N_SIDE * 2,
            "allocation_per_leg_fraction": 1.0 / (N_SIDE * 2),
            "leverage": 1,  # tanpa leverage - edge sudah cukup
            "order_type": "LIMIT",  # HARUS maker
            "maker_fee_bps": MAKER_FEE * 10000,
        }
