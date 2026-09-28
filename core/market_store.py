"""
core/market_store.py — In-memory real-time market data store.

Menyimpan harga terakhir, ticker, orderbook level 2, lilin live, funding rate, dan
trade terakhir untuk sinkronisasi sub-milidetik antara loop background (Hyperliquid
WebSocket / PriceFeed) dan callback Dash di proses yang sama.

Prinsip: store ini HANYA menyimpan data yang benar-benar diterima dari sumber.
Bila sebuah field belum pernah diisi, getter mengembalikan None — bukan angka
default yang menyerupai data nyata.
"""

import time
from collections import deque
from typing import Dict, List, Optional

# Panjang riwayat harga per simbol. Callback HUD berjalan 500 ms, jadi 240
# sampel ≈ 2 menit jendela pengamatan untuk mengukur perubahan harga riil.
PRICE_HISTORY_MAX = 240


class MarketStore:
    """Store thread-safe sederhana untuk data pasar real-time."""

    def __init__(self):
        self._tickers: Dict[str, dict] = {}
        self._order_books: Dict[str, dict] = {}
        self._last_prices: Dict[str, float] = {}
        self._price_ts: Dict[str, float] = {}
        self._price_history: Dict[str, deque] = {}
        self._live_candles: Dict[str, dict] = {}
        self._funding: Dict[str, float] = {}
        self._open_interest: Dict[str, float] = {}
        self._recent_trades: Dict[str, list] = {}

    # ------------------------------------------------------------------
    # Harga
    # ------------------------------------------------------------------
    def set_price(self, symbol: str, price: float):
        """Simpan harga terakhir dari feed mana pun (WS mid, REST ticker, candle)."""
        try:
            p = float(price)
        except (TypeError, ValueError):
            return
        if p > 0:
            self._last_prices[symbol] = p
            now = time.time()
            self._price_ts[symbol] = now
            hist = self._price_history.get(symbol)
            if hist is None:
                hist = deque(maxlen=PRICE_HISTORY_MAX)
                self._price_history[symbol] = hist
            hist.append((now, p))

    def set_ticker(self, symbol: str, ticker: dict):
        """Simpan data ticker terbaru."""
        if not ticker:
            return
        self._tickers[symbol] = ticker
        price = ticker.get("last") or ticker.get("close") or ticker.get("mid")
        if price:
            self.set_price(symbol, price)

    def get_ticker(self, symbol: str) -> Optional[dict]:
        """Ambil data ticker terakhir."""
        if symbol in self._tickers:
            return self._tickers[symbol]
        base = symbol.split("/")[0].split(":")[0].upper()
        for k, v in self._tickers.items():
            if base in k.upper():
                return v
        return None

    def get_price(self, symbol: str) -> Optional[float]:
        """
        Ambil harga terakhir suatu simbol.

        Pencocokan longgar (base asset) hanya dipakai bila tidak ada exact match.
        Pencocokan dilakukan pada segmen base simbol, bukan substring bebas, agar
        'NEAR' tidak salah cocok dengan simbol lain.
        """
        if symbol in self._last_prices:
            return self._last_prices[symbol]

        base = symbol.split("/")[0].split(":")[0].upper()
        for k, v in self._last_prices.items():
            if k.split("/")[0].split(":")[0].upper() == base:
                return v
        return None

    def get_price_age(self, symbol: str) -> Optional[float]:
        """Usia data harga dalam detik, atau None bila belum pernah diisi."""
        if symbol in self._price_ts:
            return time.time() - self._price_ts[symbol]
        base = symbol.split("/")[0].split(":")[0].upper()
        for k, ts in self._price_ts.items():
            if k.split("/")[0].split(":")[0].upper() == base:
                return time.time() - ts
        return None

    def get_all_prices(self) -> Dict[str, float]:
        """Ambil semua harga terakhir."""
        return self._last_prices.copy()

    def get_price_history(self, symbol: str, seconds: Optional[float] = None) -> List[tuple]:
        """
        Riwayat (timestamp, harga) untuk simbol, urut lama -> baru.

        `seconds` membatasi jendela; None berarti seluruh riwayat yang tersimpan.
        Dipakai HUD untuk mengukur perubahan harga riil, bukan osilasi sintetis.
        """
        hist = None
        if symbol in self._price_history:
            hist = self._price_history[symbol]
        else:
            base = symbol.split("/")[0].split(":")[0].upper()
            for k, v in self._price_history.items():
                if k.split("/")[0].split(":")[0].upper() == base:
                    hist = v
                    break
        if not hist:
            return []
        if seconds is None:
            return list(hist)
        cutoff = time.time() - float(seconds)
        return [(ts, px) for ts, px in hist if ts >= cutoff]

    def get_price_change(self, symbol: str, seconds: float = 60.0) -> Optional[float]:
        """
        Perubahan harga relatif (fraksi) selama `seconds` terakhir.

        Membandingkan harga tertua di dalam jendela dengan harga terkini.
        Mengembalikan None bila riwayat belum cukup untuk mengukur apa pun.
        """
        window = self.get_price_history(symbol, seconds=seconds)
        if len(window) < 2:
            return None
        first = window[0][1]
        last = window[-1][1]
        if first <= 0:
            return None
        return (last - first) / first

    # ------------------------------------------------------------------
    # Orderbook L2
    # ------------------------------------------------------------------
    def set_order_book(self, symbol: str, order_book: dict):
        """Simpan snapshot depth orderbook L2 dari exchange."""
        if order_book and order_book.get("bids") and order_book.get("asks"):
            self._order_books[symbol] = order_book

    def get_order_book(self, symbol: str) -> Optional[dict]:
        """
        Ambil orderbook L2 terakhir yang benar-benar diterima dari exchange.

        Mengembalikan None bila belum ada snapshot asli. Tidak ada depth sintetis:
        pemanggil harus menangani ketiadaan data secara eksplisit.
        """
        if symbol in self._order_books:
            return self._order_books[symbol]
        base = symbol.split("/")[0].split(":")[0].upper()
        for k, v in self._order_books.items():
            if k.split("/")[0].split(":")[0].upper() == base:
                return v
        return None

    def has_order_book(self, symbol: str) -> bool:
        """True bila ada snapshot L2 asli yang tersimpan."""
        return self.get_order_book(symbol) is not None

    def get_order_book_age(self, symbol: str) -> Optional[float]:
        """Usia snapshot orderbook dalam detik, atau None bila belum ada."""
        ob = self.get_order_book(symbol)
        if not ob:
            return None
        ts = ob.get("timestamp")
        if not ts:
            return None
        try:
            return time.time() - (float(ts) / 1000.0)
        except (TypeError, ValueError):
            return None

    # ------------------------------------------------------------------
    # Lilin live (candle yang sedang terbentuk)
    # ------------------------------------------------------------------
    def set_live_candle(self, symbol: str, candle: dict):
        """
        Simpan lilin interval terkini yang sedang berjalan.

        Channel `candle` Hyperliquid mendorong pembaruan setiap ada trade pada
        lilin yang belum tertutup, sehingga chart bisa bergerak real-time.
        """
        if candle and candle.get("close"):
            self._live_candles[symbol] = candle
            self.set_price(symbol, candle["close"])

    def get_live_candle(self, symbol: str) -> Optional[dict]:
        """Ambil lilin live terakhir, atau None bila belum ada."""
        if symbol in self._live_candles:
            return self._live_candles[symbol]
        base = symbol.split("/")[0].split(":")[0].upper()
        for k, v in self._live_candles.items():
            if k.split("/")[0].split(":")[0].upper() == base:
                return v
        return None

    # ------------------------------------------------------------------
    # Funding rate & open interest (dari channel activeAssetCtx)
    # ------------------------------------------------------------------
    def set_funding(self, symbol: str, funding_rate: float):
        """Simpan funding rate terkini (fraksi, mis. 0.0000125 = 0.00125%)."""
        try:
            self._funding[symbol] = float(funding_rate)
        except (TypeError, ValueError):
            pass

    def get_funding(self, symbol: str) -> Optional[float]:
        """Ambil funding rate terkini, atau None bila belum diterima."""
        if symbol in self._funding:
            return self._funding[symbol]
        base = symbol.split("/")[0].split(":")[0].upper()
        for k, v in self._funding.items():
            if k.split("/")[0].split(":")[0].upper() == base:
                return v
        return None

    def set_open_interest(self, symbol: str, open_interest: float):
        """Simpan open interest terkini (dalam unit base asset)."""
        try:
            self._open_interest[symbol] = float(open_interest)
        except (TypeError, ValueError):
            pass

    def get_open_interest(self, symbol: str) -> Optional[float]:
        """Ambil open interest terkini, atau None bila belum diterima."""
        if symbol in self._open_interest:
            return self._open_interest[symbol]
        base = symbol.split("/")[0].split(":")[0].upper()
        for k, v in self._open_interest.items():
            if k.split("/")[0].split(":")[0].upper() == base:
                return v
        return None

    # ------------------------------------------------------------------
    # Trade terakhir (ticker tape)
    # ------------------------------------------------------------------
    def set_recent_trades(self, symbol: str, trades: list):
        """Simpan daftar trade terakhir dari channel `trades`."""
        if isinstance(trades, list):
            self._recent_trades[symbol] = trades[:50]

    def get_recent_trades(self, symbol: str) -> list:
        """Ambil trade terakhir, atau list kosong bila belum ada."""
        if symbol in self._recent_trades:
            return self._recent_trades[symbol]
        base = symbol.split("/")[0].split(":")[0].upper()
        for k, v in self._recent_trades.items():
            if k.split("/")[0].split(":")[0].upper() == base:
                return v
        return []


# Singleton instance yang dibagi antar thread dalam proses yang sama
market_store = MarketStore()
