"""
database/models.py — Dataclass Python untuk representasi tabel database.
"""

from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime


@dataclass
class Candle:
    symbol: str
    timeframe: str
    timestamp: int  # Unix ms
    open: float
    high: float
    low: float
    close: float
    volume: float
    id: Optional[int] = None
    created_at: Optional[str] = None


@dataclass
class Position:
    symbol: str
    side: str  # 'LONG' atau 'SHORT'
    entry_price: float
    quantity: float
    leverage: int
    margin: float
    liquidation_price: float
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    unrealized_pnl: float = 0.0
    status: str = "OPEN"  # 'OPEN', 'CLOSED', 'LIQUIDATED'
    opened_at: Optional[str] = None
    closed_at: Optional[str] = None
    close_price: Optional[float] = None
    realized_pnl: Optional[float] = None
    close_reason: Optional[str] = None
    reasoning: Optional[str] = None
    id: Optional[int] = None


@dataclass
class Trade:
    symbol: str
    side: str  # 'BUY' atau 'SELL'
    price: float
    quantity: float
    trade_type: str  # 'OPEN', 'CLOSE', 'LIQUIDATION'
    position_id: Optional[int] = None
    fee: float = 0.0
    fee_type: str = "TAKER"
    executed_at: Optional[str] = None
    id: Optional[int] = None


@dataclass
class Signal:
    symbol: str
    signal_type: str  # 'TECHNICAL', 'ML', 'SENTIMENT', 'MACRO'
    signal_value: str  # JSON string
    direction: Optional[str] = None  # 'BULLISH', 'BEARISH', 'NEUTRAL'
    confidence: Optional[float] = None
    source: Optional[str] = None
    timestamp: Optional[str] = None
    id: Optional[int] = None


@dataclass
class NewsItem:
    title: str
    source: str
    url: Optional[str] = None
    published_at: Optional[str] = None
    fetched_at: Optional[str] = None
    sentiment_vader: Optional[float] = None
    sentiment_finbert: Optional[float] = None
    sentiment_label: Optional[str] = None
    impact_level: str = "LOW"
    content_summary: Optional[str] = None
    id: Optional[int] = None


@dataclass
class AgentLog:
    agent_name: str
    action: str
    reasoning: str
    input_data: Optional[str] = None
    output_data: Optional[str] = None
    timestamp: Optional[str] = None
    id: Optional[int] = None


@dataclass
class Account:
    balance: float
    initial_balance: float
    total_pnl: float = 0.0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    max_drawdown: float = 0.0
    peak_balance: Optional[float] = None
    sharpe_ratio: Optional[float] = None
    profit_factor: Optional[float] = None
    updated_at: Optional[str] = None
    id: Optional[int] = None


@dataclass
class BalanceSnapshot:
    balance: float
    equity: float
    unrealized_pnl: float = 0.0
    timestamp: Optional[str] = None
    id: Optional[int] = None


@dataclass
class MacroData:
    indicator: str
    value: float
    source: str
    period: Optional[str] = None
    fetched_at: Optional[str] = None
    id: Optional[int] = None
