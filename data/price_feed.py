"""
data/price_feed.py — WebSocket + REST harga futures dengan Multi-Tier Fallback.

Menyediakan data harga real-time dan candle OHLCV dari:
0. Hyperliquid perp DEX (REST publik + WebSocket, tanpa API key) — sumber utama
1. Binance Futures (ccxt publik, tanpa API key)
2. Fallback Tier 1: Yahoo Finance (yfinance) jika Binance diblokir DNS ISP
3. Fallback Tier 2: CoinGecko Public API

Hyperliquid dipakai lebih dulu karena menyediakan orderbook L2 asli 20 level dan
streaming lilin live, yang tidak tersedia lewat REST polling biasa.
"""

import asyncio
import time
import urllib.request
import json
from typing import Callable, Dict, List, Optional
from datetime import datetime, timezone

import ccxt.async_support as ccxt
import yfinance as yf

from core.config import get_config
from core.event_bus import EventBus, Channels
from core.logger import get_logger
from data.hyperliquid_feed import HyperliquidFeed, symbol_to_coin, coin_to_symbol

logger = get_logger("price_feed")

COINGECKO_MAP = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "XRP": "ripple",
    "BNB": "binancecoin",
    "DOGE": "dogecoin",
    "ADA": "cardano",
    "AVAX": "avalanche-2",
    "LINK": "chainlink",
    "NEAR": "near",
    "SUI": "sui",
    "DOT": "polkadot",
    "LTC": "litecoin",
    "UNI": "uniswap",
    "PEPE": "pepe",
    "SHIB": "shiba-inu",
    "APT": "aptos",
    "ZEC": "zcash",
    "HYPE": "hyperliquid",
    "TRX": "tron",
}

SPECIAL_YF_MAP = {
    "SUI": "SUI20947-USD",
    "PEPE": "PEPE24478-USD",
    "APT": "APT21794-USD",
    "UNI": "UNI7083-USD",
    "SHIB": "SHIB-USD",
}

# Aset kripto futures dengan likuiditas dan dukungan feed terverifikasi
VERIFIED_FUTURES = {
    "BTC", "ETH", "SOL", "XRP", "BNB", "DOGE", "ADA", "AVAX", "LINK", "NEAR",
    "SUI", "DOT", "LTC", "UNI", "PEPE", "SHIB", "APT", "TRX", "ATOM", "INJ"
}


def _symbol_to_yf(symbol: str) -> str:
    """Konversi format simbol bursa (misal BTC/USDT:USDT) ke format yfinance (BTC-USD)."""
    base = symbol.split('/')[0].split(':')[0].replace('USDT', '').replace('USD', '').strip().upper()
    if base in SPECIAL_YF_MAP:
        return SPECIAL_YF_MAP[base]
    return f"{base}-USD"


def _symbol_to_base(symbol: str) -> str:
    """Ambil aset dasar (misal BTC dari BTC/USDT:USDT)."""
    return symbol.split('/')[0].split(':')[0].replace('USDT', '').replace('USD', '').strip()


class PriceFeed:
    """
    Pengambil data harga futures terpadu dengan multi-tier fallback otomatis.

    Fitur:
    - Ambil OHLCV via REST (Binance Futures -> yfinance fallback)
    - Ambil harga terakhir (ticker) dengan broadcast ke EventBus
    - Ambil funding rate terkini
    - Simpan candle ke SQLite secara otomatis
    """

    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus
        self.config = get_config()
        self._exchange: Optional[ccxt.binance] = None
        self._running = False
        self._last_prices: Dict[str, float] = {}
        self._ccxt_available = True  # Berpindah ke False jika koneksi awal ccxt gagal
        # Sumber utama: Hyperliquid (REST + WS publik, tanpa kredensial)
        self.hyperliquid = HyperliquidFeed(event_bus)
        self._hl_coins: set = set()
        # Daftarkan instance aktif agar modul lain (mis. callback HUD) bisa membaca
        # status feed — jumlah pesan per channel WebSocket — tanpa perlu
        # menembuskan referensi lewat rantai konstruktor.
        global _active_price_feed
        _active_price_feed = self

    async def initialize(self):
        """Inisialisasi feed Hyperliquid (Tier 0) dan Binance Futures (Tier 1)."""
        # --- Tier 0: Hyperliquid ---
        try:
            universe = await self.hyperliquid.fetch_universe()
            self._hl_coins = {
                u["name"] for u in universe if u.get("name") and not u.get("isDelisted")
            }
            if self._hl_coins:
                logger.info(f"Hyperliquid siap — {len(self._hl_coins)} perpetual publik")
        except Exception as e:
            logger.warning(f"Inisialisasi Hyperliquid gagal: {e}. Lanjut ke Binance.")

        # --- Tier 1: Binance Futures ---
        try:
            self._exchange = ccxt.binance({
                "enableRateLimit": True,
                "timeout": 4000,  # 4 detik batas waktu agar fallback cepat aktif
                "options": {
                    "defaultType": "future",
                },
            })
            logger.info("PriceFeed terhubung ke Binance Futures (publik)")
        except Exception as e:
            logger.warning(f"Koneksi awal ccxt gagal: {e}. Mengaktifkan mode fallback yfinance.")
            self._ccxt_available = False

    async def close(self):
        """Tutup koneksi exchange dan WebSocket."""
        self.hyperliquid.stop()
        if self._exchange:
            try:
                await self._exchange.close()
            except Exception:
                pass
            self._exchange = None

    async def start_streaming(self):
        """
        Jalankan WebSocket Hyperliquid sebagai background task.

        Dipanggil setelah simbol aktif ditentukan, agar langganan orderbook
        mencakup seluruh koin yang dipantau.
        """
        self.hyperliquid.set_watched_coins(self.config.symbols)
        task = asyncio.create_task(self.hyperliquid.websocket_loop())
        logger.info(
            f"Streaming Hyperliquid dimulai untuk {len(self.config.symbols)} simbol"
        )
        return task

    async def _fetch_ohlcv_yf(
        self, symbol: str, timeframe: str = "1m", limit: int = 100
    ) -> List[list]:
        """Ambil data OHLCV menggunakan yfinance di thread terpisah."""
        def _get():
            yf_sym = _symbol_to_yf(symbol)
            tf_map = {
                "1m": ("1m", "1d"),
                "5m": ("5m", "5d"),
                "15m": ("15m", "5d"),
                "1h": ("1h", "1mo"),
                "1d": ("1d", "1y"),
            }
            interval, period = tf_map.get(timeframe, ("5m", "5d"))
            ticker = yf.Ticker(yf_sym)
            df = ticker.history(period=period, interval=interval)
            if df.empty:
                return []
            df = df.tail(limit)
            result = []
            for idx, row in df.iterrows():
                ts = int(idx.timestamp() * 1000)
                result.append([
                    ts,
                    float(row["Open"]),
                    float(row["High"]),
                    float(row["Low"]),
                    float(row["Close"]),
                    float(row["Volume"]),
                ])
            return result

        try:
            return await asyncio.to_thread(_get)
        except Exception as e:
            logger.error(f"Fallback yfinance OHLCV {symbol} {timeframe} gagal: {e}")
            return []

    async def fetch_ohlcv(
        self, symbol: str, timeframe: str = "1m", limit: int = 100, save_to_db: bool = True
    ) -> List[list]:
        """
        Ambil data OHLCV dari Binance Futures atau fallback yfinance.

        Returns:
            List of [timestamp_ms, open, high, low, close, volume]
        """
        ohlcv = []
        # Hanya lilin dari bursa (Hyperliquid / Binance Futures) yang boleh
        # dipersistensikan. yfinance dipakai untuk tampilan darurat saja:
        # instrumennya berbeda (saham/ETF proxy vs perpetual), sehingga harga
        # dan satuan volumenya tidak sebanding. Menyimpannya ke tabel `candles`
        # akan mencampur dua skala berbeda dalam satu simbol.
        persist = False

        # 0. Hyperliquid (sumber utama: lilin real-time, tanpa kredensial)
        coin = symbol_to_coin(symbol)
        if coin in self._hl_coins:
            ohlcv = await self.hyperliquid.fetch_candles(coin, interval=timeframe, limit=limit)
            persist = bool(ohlcv)

        # 1. Coba ccxt Binance Futures jika aktif
        if not ohlcv and self._ccxt_available and self._exchange:
            try:
                ohlcv = await asyncio.wait_for(
                    self._exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit),
                    timeout=4.0,
                )
                persist = bool(ohlcv)
            except Exception as e:
                logger.debug(f"ccxt OHLCV {symbol} gagal: {e}. Mengalihkan ke yfinance.")
                self._ccxt_available = False

        # 2. Fallback ke yfinance jika ccxt gagal atau kosong
        if not ohlcv:
            ohlcv = await self._fetch_ohlcv_yf(symbol, timeframe=timeframe, limit=limit)

        # 3. Simpan candle ke database lokal
        if save_to_db and ohlcv and persist:
            try:
                from database.db import get_db
                from database.repository import Repository
                from database.models import Candle
                db = await get_db()
                repo = Repository(db)
                candles = []
                for c in ohlcv:
                    try:
                        o, h, l, cl = float(c[1]), float(c[2]), float(c[3]), float(c[4])
                        v = float(c[5])
                    except (TypeError, ValueError):
                        continue
                    # Tolak bar yang melanggar invarian OHLC. Bar seperti ini tidak
                    # mungkin berasal dari bursa, dan sekali tersimpan ia akan
                    # merusak chart: lilin dengan high di bawah open, atau harga
                    # nol, akan mengubah skala sumbu-y dan menyisipkan lilin palsu.
                    if o <= 0 or h <= 0 or l <= 0 or cl <= 0 or v < 0:
                        continue
                    if h < l or h < o or h < cl or l > o or l > cl:
                        continue
                    candles.append(Candle(
                        symbol=symbol,
                        timeframe=timeframe,
                        timestamp=c[0],
                        open=o, high=h, low=l, close=cl, volume=v,
                    ))
                if candles:
                    await repo.insert_candles_batch(candles)
            except Exception as save_err:
                logger.debug(f"Gagal simpan candle ke DB: {save_err}")

        return ohlcv

    async def _fetch_ticker_yf(self, symbol: str) -> Optional[dict]:
        """Ambil data ticker real-time via yfinance."""
        def _get():
            yf_sym = _symbol_to_yf(symbol)
            ticker = yf.Ticker(yf_sym)
            info = ticker.fast_info
            price = float(info.last_price or 0)
            if price <= 0:
                return None
            prev = float(info.previous_close or price)
            high = float(info.day_high or price)
            low = float(info.day_low or price)
            pct = ((price - prev) / prev * 100) if prev else 0.0
            vol = float(getattr(info, "last_volume", 0) or 0)
            return {
                "symbol": symbol,
                "last": price,
                "bid": price * 0.9999,
                "ask": price * 1.0001,
                "quoteVolume": vol,
                "percentage": pct,
                "high": high,
                "low": low,
                "timestamp": int(time.time() * 1000),
            }

        try:
            return await asyncio.to_thread(_get)
        except Exception as e:
            logger.debug(f"yfinance ticker {symbol} gagal: {e}")
            return None

    async def _fetch_ticker_coingecko(self, symbol: str) -> Optional[dict]:
        """Fallback sekunder via CoinGecko REST."""
        def _get():
            base = _symbol_to_base(symbol)
            cg_id = COINGECKO_MAP.get(base)
            if not cg_id:
                return None
            url = f"https://api.coingecko.com/api/v3/simple/price?ids={cg_id}&vs_currencies=usd&include_24hr_vol=true&include_24hr_change=true"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=4) as res:
                data = json.loads(res.read().decode())
                item = data.get(cg_id, {})
                price = float(item.get("usd", 0))
                if price <= 0:
                    return None
                pct = float(item.get("usd_24h_change", 0.0))
                vol = float(item.get("usd_24h_vol", 0.0))
                return {
                    "symbol": symbol,
                    "last": price,
                    "bid": price * 0.9999,
                    "ask": price * 1.0001,
                    "quoteVolume": vol,
                    "percentage": pct,
                    "high": price * 1.02,
                    "low": price * 0.98,
                    "timestamp": int(time.time() * 1000),
                }

        try:
            return await asyncio.to_thread(_get)
        except Exception as e:
            logger.debug(f"CoinGecko ticker {symbol} gagal: {e}")
            return None

    async def fetch_ticker(self, symbol: str) -> Optional[dict]:
        """
        Ambil harga terakhir (ticker) dengan cascading fallback:
        Binance Futures -> yfinance -> CoinGecko.
        """
        ticker = None

        # 0. Hyperliquid (REST snapshot; WebSocket sudah mengisi market_store lebih cepat)
        coin = symbol_to_coin(symbol)
        if coin in self._hl_coins:
            try:
                data = await self.hyperliquid.post_info({"type": "allMids"})
                # REST mengirim flat map; WS membungkusnya di key "mids"
                mids = data.get("mids") if isinstance(data.get("mids"), dict) else data
                mid = mids.get(coin) if isinstance(mids, dict) else None
                if mid:
                    price = float(mid)
                    ticker = {
                        "symbol": symbol,
                        "last": price,
                        "mid": price,
                        "timestamp": int(time.time() * 1000),
                        "exchange": "hyperliquid",
                    }
            except Exception:
                pass

        # 1. Coba ccxt jika tersedia
        if not ticker and self._ccxt_available and self._exchange:
            try:
                ticker = await asyncio.wait_for(
                    self._exchange.fetch_ticker(symbol),
                    timeout=3.0,
                )
            except Exception:
                self._ccxt_available = False

        # 2. Fallback ke yfinance
        if not ticker:
            ticker = await self._fetch_ticker_yf(symbol)

        # 3. Fallback ke CoinGecko
        if not ticker:
            ticker = await self._fetch_ticker_coingecko(symbol)

        if not ticker:
            logger.warning(f"Gagal mengambil harga untuk {symbol} dari semua sumber")
            return None

        price = ticker.get("last", 0)
        self._last_prices[symbol] = price

        # Update real-time market store
        try:
            from core.market_store import market_store
            market_store.set_ticker(symbol, ticker)
        except Exception:
            pass

        # Broadcast ke event bus
        await self.event_bus.publish(
            Channels.PRICE_UPDATE,
            {
                "symbol": symbol,
                "price": price,
                "bid": ticker.get("bid", price * 0.9999),
                "ask": ticker.get("ask", price * 1.0001),
                "volume_24h": ticker.get("quoteVolume", 0),
                "change_24h": ticker.get("percentage", 0),
                "high_24h": ticker.get("high", price),
                "low_24h": ticker.get("low", price),
                "timestamp": int(time.time() * 1000),
            },
            source="price_feed",
        )
        return ticker

    async def fetch_funding_rate(self, symbol: str) -> Optional[dict]:
        """
        Ambil funding rate terkini.

        Prioritas: cache WebSocket Hyperliquid (channel activeAssetCtx, real-time)
        -> REST Hyperliquid -> ccxt Binance -> nilai standar kontrak perpetual.
        """
        from core.market_store import market_store

        # 0a. Cache dari WebSocket (paling segar, tanpa request tambahan)
        cached = market_store.get_funding(symbol)
        if cached is not None:
            return {
                "symbol": symbol,
                "funding_rate": cached,
                "funding_timestamp": datetime.now(timezone.utc).isoformat(),
                "next_funding": "Setiap 1 Jam (Hyperliquid)",
                "source": "hyperliquid_ws",
            }

        # 0b. REST Hyperliquid
        coin = symbol_to_coin(symbol)
        if coin in self._hl_coins:
            try:
                data = await self.hyperliquid.post_info({"type": "metaAndAssetCtxs"})
                if isinstance(data, list) and len(data) >= 2:
                    universe, ctxs = data[0].get("universe", []), data[1]
                    for meta, ctx in zip(universe, ctxs):
                        if meta.get("name") == coin and ctx.get("funding") is not None:
                            rate = float(ctx["funding"])
                            market_store.set_funding(symbol, rate)
                            return {
                                "symbol": symbol,
                                "funding_rate": rate,
                                "funding_timestamp": datetime.now(timezone.utc).isoformat(),
                                "next_funding": "Setiap 1 Jam (Hyperliquid)",
                                "source": "hyperliquid_rest",
                            }
            except Exception:
                pass

        if self._ccxt_available and self._exchange:
            try:
                funding = await asyncio.wait_for(
                    self._exchange.fetch_funding_rate(symbol),
                    timeout=3.0,
                )
                return {
                    "symbol": symbol,
                    "funding_rate": funding.get("fundingRate", 0.0001),
                    "funding_timestamp": funding.get("fundingDatetime"),
                    "next_funding": funding.get("nextFundingDatetime"),
                }
            except Exception:
                pass

        # Nilai funding rate standar kontrak perpetual (0.01% / 8 jam)
        return {
            "symbol": symbol,
            "funding_rate": 0.0001,
            "funding_timestamp": datetime.now(timezone.utc).isoformat(),
            "next_funding": "Setiap 8 Jam",
        }

    async def fetch_mark_price(self, symbol: str) -> Optional[float]:
        """Ambil mark price untuk perhitungan margin/likuidasi."""
        # 0. Hyperliquid mark price (dipakai untuk perhitungan margin di bursa asal)
        coin = symbol_to_coin(symbol)
        if coin in self._hl_coins:
            try:
                data = await self.hyperliquid.post_info({"type": "metaAndAssetCtxs"})
                if isinstance(data, list) and len(data) >= 2:
                    universe, ctxs = data[0].get("universe", []), data[1]
                    for meta, ctx in zip(universe, ctxs):
                        if meta.get("name") == coin and ctx.get("markPx"):
                            return float(ctx["markPx"])
            except Exception:
                pass

        if self._ccxt_available and self._exchange:
            try:
                ticker = await asyncio.wait_for(
                    self._exchange.fetch_ticker(symbol),
                    timeout=3.0,
                )
                info = ticker.get("info", {})
                return float(info.get("markPrice", ticker.get("last", 0)))
            except Exception:
                pass

        # Gunakan harga terakhir dari cache atau ambil ticker baru
        cached = self._last_prices.get(symbol)
        if cached:
            return cached

        t = await self.fetch_ticker(symbol)
        return t.get("last", 0.0) if t else None

    async def fetch_order_book(self, symbol: str, limit: int = 5) -> Optional[dict]:
        """
        Ambil orderbook L2 real-time.

        Hyperliquid selalu mengembalikan 20 level per sisi. Bila tidak tersedia,
        mengembalikan None — tidak ada depth sintetis, karena tape HUD harus
        menampilkan depth exchange yang sebenarnya.
        """
        from core.market_store import market_store

        # 0. Hyperliquid (20 level asli per sisi, tanpa kredensial)
        coin = symbol_to_coin(symbol)
        if coin in self._hl_coins:
            ob = await self.hyperliquid.fetch_l2_book(coin, depth=limit)
            if ob:
                market_store.set_order_book(symbol, ob)
                return ob

        # 1. Binance Futures via ccxt
        if self._ccxt_available and self._exchange:
            try:
                ob = await asyncio.wait_for(
                    self._exchange.fetch_order_book(symbol, limit=limit),
                    timeout=2.0,
                )
                if ob and ob.get("bids") and ob.get("asks"):
                    normalized = {
                        "symbol": symbol,
                        "bids": [[float(b[0]), float(b[1])] for b in ob["bids"]],
                        "asks": [[float(a[0]), float(a[1])] for a in ob["asks"]],
                        "timestamp": ob.get("timestamp") or int(time.time() * 1000),
                        "source": "exchange",
                        "exchange": "binance",
                    }
                    market_store.set_order_book(symbol, normalized)
                    return normalized
            except Exception:
                pass

        # Tidak ada orderbook asli: kembalikan cache terakhir bila ada, selain itu None
        return market_store.get_order_book(symbol)

    async def fetch_all_tickers(self) -> Dict[str, dict]:
        """Ambil ticker untuk semua simbol yang dikonfigurasi."""
        results = {}
        for symbol in self.config.symbols:
            ticker = await self.fetch_ticker(symbol)
            if ticker:
                results[symbol] = ticker
        return results

    async def price_update_loop(self):
        """
        Loop utama: update harga seluruh simbol secara berkala.

        Bila WebSocket Hyperliquid aktif, harga sudah mengalir jauh lebih cepat
        (sub-detik) sehingga loop ini hanya berperan sebagai jaring pengaman
        untuk simbol yang tidak tercakup WS.
        """
        self._running = True
        logger.info("Price update loop dimulai")

        while self._running:
            try:
                # Hyperliquid allMids: satu permintaan untuk seluruh pasar
                if self._hl_coins:
                    mids = await self.hyperliquid.fetch_all_mids()
                    from core.market_store import market_store
                    for coin, px in mids.items():
                        sym = coin_to_symbol(coin)
                        try:
                            fpx = float(px)
                            market_store.set_price(sym, fpx)
                            self._last_prices[sym] = fpx
                        except (TypeError, ValueError):
                            pass

                # Simbol yang belum punya harga (atau WS mati) diambil per simbol
                from core.market_store import market_store
                missing = [
                    s for s in self.config.symbols
                    if market_store.get_price(s) is None
                    or (market_store.get_price_age(s) or 0) > 10.0
                ]
                for symbol in missing:
                    await self.fetch_ticker(symbol)
                    await asyncio.sleep(0.15)
            except Exception as e:
                logger.error(f"Error dalam price loop: {e}")

            await asyncio.sleep(1.5)

    def stop(self):
        """Hentikan loop update harga."""
        self._running = False

    def update_symbols(self, new_symbols: List[str]):
        """Perbarui daftar simbol yang aktif dipantau."""
        self.config.symbols = new_symbols
        logger.info(f"Daftar simbol trading diperbarui ({len(new_symbols)} simbol): {new_symbols}")

    async def discover_top_volume_symbols(self, limit: int = 10) -> List[str]:
        """
        Pindai simbol futures dengan volume perdagangan 24 jam tertinggi.
        Tingkat 0: Hyperliquid metaAndAssetCtxs (dayNtlVlm)
        Tingkat 1: Binance Futures via ccxt
        Tingkat 2: CoinGecko Public Markets (volume_desc)
        Tingkat 3: Fallback ke konfigurasi default top 10
        """
        # 0. Hyperliquid (volume notional 24 jam per kontrak perpetual)
        try:
            hl_top = await self.hyperliquid.discover_top_volume_symbols(limit=limit)
            if hl_top and len(hl_top) >= limit:
                return hl_top
        except Exception as e:
            logger.debug(f"Discovery top volume Hyperliquid gagal: {e}")

        # 1. Coba ccxt Binance Futures
        if self._ccxt_available and self._exchange:
            try:
                tickers = await asyncio.wait_for(self._exchange.fetch_tickers(), timeout=5.0)
                stables = ("USDC", "FDUSD", "BUSD", "DAI", "TUSD", "USDP", "USD1", "USDG")
                usdt_tickers = []
                for s, t in tickers.items():
                    if "/USDT" in s and not any(st in s for st in stables):
                        vol = float(t.get("quoteVolume") or t.get("baseVolume") or 0)
                        usdt_tickers.append((s, vol))
                usdt_tickers.sort(key=lambda x: x[1], reverse=True)
                top = [x[0] for x in usdt_tickers[:limit]]
                if len(top) >= limit:
                    logger.info(f"Top {limit} volume futures ditemukan via Binance: {top}")
                    return top
            except Exception as e:
                logger.debug(f"Discovery top volume via ccxt gagal: {e}")

        # 2. Coba CoinGecko Public Markets API
        def _get_cg():
            url = "https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=volume_desc&per_page=50&page=1"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=5) as res:
                data = json.loads(res.read().decode())
                top_syms = []
                for c in data:
                    sym = c.get("symbol", "").upper()
                    if sym in VERIFIED_FUTURES:
                        top_syms.append(f"{sym}/USDT:USDT")
                    if len(top_syms) == limit:
                        break
                return top_syms

        try:
            cg_symbols = await asyncio.to_thread(_get_cg)
            if cg_symbols and len(cg_symbols) >= limit:
                logger.info(f"Top {limit} volume futures ditemukan via CoinGecko: {cg_symbols}")
                return cg_symbols
        except Exception as e:
            logger.debug(f"Discovery top volume via CoinGecko gagal: {e}")

        # 3. Default fallback top 10
        default_top = [
            "BTC/USDT:USDT",
            "ETH/USDT:USDT",
            "SOL/USDT:USDT",
            "XRP/USDT:USDT",
            "BNB/USDT:USDT",
            "DOGE/USDT:USDT",
            "ADA/USDT:USDT",
            "AVAX/USDT:USDT",
            "LINK/USDT:USDT",
            "NEAR/USDT:USDT",
        ][:limit]
        logger.info(f"Menggunakan fallback default top {limit} volume futures: {default_top}")
        return default_top

    def get_last_price(self, symbol: str) -> Optional[float]:
        """Ambil harga terakhir yang di-cache."""
        return self._last_prices.get(symbol)

    def get_all_last_prices(self) -> Dict[str, float]:
        """Ambil semua harga terakhir yang di-cache."""
        return self._last_prices.copy()


# Instance PriceFeed yang sedang berjalan. Dipakai callback HUD untuk membaca
# status feed (denyut channel WebSocket). None selama bot belum diinisialisasi.
_active_price_feed: Optional["PriceFeed"] = None


def get_price_feed() -> Optional["PriceFeed"]:
    """Ambil instance PriceFeed aktif, atau None bila bot belum berjalan."""
    return _active_price_feed
