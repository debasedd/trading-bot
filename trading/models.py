"""
trading/models.py — Dataclass untuk order, keputusan trading, dan enumerasi.
"""

from dataclasses import dataclass, field
from typing import Optional
from enum import Enum


class Side(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class TradeAction(str, Enum):
    OPEN_LONG = "OPEN_LONG"
    OPEN_SHORT = "OPEN_SHORT"
    CLOSE = "CLOSE"
    HOLD = "HOLD"


class CloseReason(str, Enum):
    TP_HIT = "TP_HIT"
    SL_HIT = "SL_HIT"
    MANUAL = "MANUAL"
    LIQUIDATED = "LIQUIDATED"
    SIGNAL = "SIGNAL"


@dataclass
class Order:
    """Instruksi order dari agen keputusan."""
    symbol: str
    action: TradeAction
    side: Optional[Side] = None
    quantity: Optional[float] = None         # Jika None, dihitung oleh risk manager
    leverage: int = 5
    order_type: OrderType = OrderType.MARKET
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    position_id: Optional[int] = None        # Untuk close posisi tertentu
    reasoning: str = ""
    # Harga acuan untuk jalur live. Paper engine mengabaikan field ini
    # dan memakai harga pasar saat order dieksekusi; live engine
    # MEMBUTUHKANNYA, karena harga yang hilang di bursa akan ditolak
    # tanpa alasan yang bisa dibaca.
    price: Optional[float] = None


@dataclass
class TradeDecision:
    """Keputusan lengkap dari agen pengambil keputusan."""
    action: TradeAction
    symbol: str
    side: Optional[Side] = None
    confidence: float = 0.0                  # 0.0 - 1.0
    leverage: int = 5
    stop_loss_pct: Optional[float] = None    # % dari entry
    take_profit_pct: Optional[float] = None  # % dari entry
    risk_pct: float = 0.02                   # % saldo yang dipertaruhkan
    reasoning: str = ""
    signals: dict = field(default_factory=dict)  # Sinyal yang mendasari keputusan


@dataclass
class PositionInfo:
    """Informasi lengkap posisi aktif (untuk dashboard)."""
    id: int
    symbol: str
    side: str
    entry_price: float
    quantity: float
    leverage: int
    margin: float
    liquidation_price: float
    stop_loss: Optional[float]
    take_profit: Optional[float]
    unrealized_pnl: float
    roe_pct: float                           # Return on equity %
    mark_price: float
    duration: str                            # Berapa lama posisi terbuka
