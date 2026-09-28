"""
data/hyperliquid_feed.py — Feed pasar Hyperliquid (perp DEX) real-time tanpa API key.

Hyperliquid menyediakan data publik lewat dua jalur yang tidak butuh kredensial:
1. REST  : POST https://api.hyperliquid.xyz/info  (body JSON berisi field "type")
2. WS    : wss://api.hyperliquid.xyz/ws           (JSON subscribe, tanpa auth)

Modul ini menjadi sumber data utama (Tier 0) karena:
- Orderbook L2 asli 20 level per sisi, dikirim sebagai snapshot penuh setiap update
  (bukan delta), sehingga tape HUD menampilkan depth 1:1 dengan website.
- Channel `candle` mendorong lilin yang sedang terbentuk setiap ada trade,
  sehingga chart bergerak real-time tanpa polling.
- Channel `allMids` mengirim seluruh mid price dalam satu pesan (~0.5s),
  jauh lebih hemat daripada polling ticker per simbol.

Pembagian tanggung jawab dengan `core.microstructure`
---------------------------------------------------
Modul ini TIDAK menghitung apa pun. Snapshot L2 diteruskan ke kernel
mikrostruktur, yang menjadi hot path dan dapat diganti implementasi C++/Rust
tanpa menyentuh satu baris pun di sini. Modul ini hanya mengurus transport
(WebSocket), parsing JSON, dan penulisan ke `market_store`.

Pemisahan itu disengaja: transport dan kalkulasi adalah dua worry yang berubah
dengan alasan yang sangat berbeda. Bursa ganti, transport berubah. Kernel perlu
dipercepat, kalkulasi berubah. Memcampurkan keduanya membuat keduanya mustahil
diuji dan mustahil diganti secara terpisah.

Format simbol internal proyek ini adalah "BTC/USDT:USDT"; Hyperliquid memakai "BTC".
Helper coin_to_symbol()/symbol_to_coin() menjembatani keduanya.
"""

import asyncio
import json
import time
import urllib.request
from typing import Dict, List, Optional, Set

import websockets

from core.logger import get_logger
from core.market_store import market_store
from core import microstructure

logger = get_logger("hyperliquid_feed")

HL_REST_URL = "https://api.hyperliquid.xyz/info"
HL_WS_URL = "wss://api.hyperliquid.xyz/ws"

# Batas ukuran pesan WS. allMids mengirim ~1000+ entri dalam satu frame.
WS_MAX_SIZE = 2 ** 22

# Interval ping aplikasi-level (Hyperliquid memakai ping JSON, bukan ping protokol WS)
WS_PING_INTERVAL = 30.0

# Timeout tunggu pesan sebelum koneksi dianggap mati
WS_RECV_TIMEOUT = 60.0


def coin_to_symbol(coin: str) -> str:
    """Konversi nama koin Hyperliquid ('BTC') ke format internal ('BTC/USDT:USDT')."""
    return f"{str(coin).upper()}/USDT:USDT"


def symbol_to_coin(symbol: str) -> str:
    """Konversi format internal ('BTC/USDT:USDT') ke nama koin Hyperliquid ('BTC')."""
    return (
        symbol.split("/")[0]
        .split(":")[0]
        .replace("USDT", "")
        .replace("USD", "")
        .strip()
        .upper()
    )


class HyperliquidFeed:
    """
    Klien Hyperliquid publik: REST untuk data historis/snapshot, WS untuk streaming.

    Tidak menyimpan kredensial apa pun dan tidak pernah mengirim order.
    Semua data ditulis ke `market_store` agar bisa dibaca langsung oleh callback Dash.
    """

    def __init__(self, event_bus=None):
        self.event_bus = event_bus
        self._running = False
        self._ws_connected = False
        self._known_coins: Set[str] = set()
        self._watched_coins: Set[str] = set()
        self._last_mid_time: float = 0.0
        self._last_book_time: float = 0.0
        self._msg_counts: Dict[str, int] = {}
        self._rest_failures = 0
        self._native_kernel = False

    # ------------------------------------------------------------------
    # Kernel C++
    # ------------------------------------------------------------------
    def _ensure_native_kernel(self) -> bool:
        """
        Aktifkan kernel C++20 kalau tersedia, sekali saja per feed.

        Dipanggil tepat sebelum WebSocket mulai menerima. Kernel tidak boleh
        diganti di tengah operasi: kalau agent sudah membaca OFI dari
        `PythonKernel` dan lalu kernel-nya ditukar, dua angka berbeda akan
        masuk ke pipeline yang sama dalam hitungan detik.

        Kegagalan TIDAK menjatuhkan sistem — `PythonKernel` menghasilkan angka
        yang identik, hanya lebih lambat. Yang wajib dijaga adalah bahwa
        kegagalan itu terlihat di log, bukan tersembunyi.
        """
        if self._native_kernel:
            return True
        try:
            self._native_kernel = microstructure.initialize_native_kernel()
        except Exception as exc:
            # TRADEBOT_KERNEL=cpp yang gagal adalah konfigurasi salah operator
            # — biarkan error-nya naik supaya ketahuan, bukan dipelan.
            logger.error(f"Gagal mengaktifkan kernel C++: {exc}")
            raise
        return self._native_kernel

    # ------------------------------------------------------------------
    # REST
    # ------------------------------------------------------------------
    def _post_info_sync(self, payload: dict, timeout: float = 8.0):
        """Panggilan REST Hyperliquid blocking. Dijalankan di thread terpisah."""
        req = urllib.request.Request(
            HL_REST_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return json.loads(res.read().decode("utf-8"))

    async def post_info(self, payload: dict, timeout: float = 8.0):
        """Panggilan REST Hyperliquid non-blocking."""
        return await asyncio.to_thread(self._post_info_sync, payload, timeout)

    async def fetch_universe(self) -> List[dict]:
        """
        Ambil daftar seluruh kontrak perpetual beserta metadata-nya.

        Respons `meta`: {"universe": [{"name","szDecimals","maxLeverage","isDelisted"?}, ...]}
        """
        try:
            meta = await self.post_info({"type": "meta"})
            universe = meta.get("universe", []) if isinstance(meta, dict) else []
            self._known_coins = {
                u["name"] for u in universe if u.get("name") and not u.get("isDelisted")
            }
            logger.info(f"Universe Hyperliquid: {len(self._known_coins)} perpetual aktif")
            return universe
        except Exception as e:
            logger.warning(f"Gagal mengambil universe Hyperliquid: {e}")
            return []

    async def discover_top_volume_symbols(self, limit: int = 10) -> List[str]:
        """
        Pindai kontrak perpetual dengan volume notional 24 jam tertinggi.

        Respons `metaAndAssetCtxs` adalah list 2 elemen:
          [0] = {"universe": [...]}  — metadata kontrak (urutan sejajar dengan ctxs)
          [1] = [{"dayNtlVlm": "...", "markPx": "...", ...}, ...] — konteks per kontrak

        Return format "XXX/USDT:USDT" agar kompatibel dengan PriceFeed.
        """
        try:
            data = await self.post_info({"type": "metaAndAssetCtxs"})
            if not isinstance(data, list) or len(data) < 2:
                return []

            universe = data[0].get("universe", [])
            ctxs = data[1]

            ranked = []
            for meta, ctx in zip(universe, ctxs):
                name = meta.get("name")
                if not name or meta.get("isDelisted"):
                    continue
                try:
                    vol = float(ctx.get("dayNtlVlm") or 0.0)
                except (TypeError, ValueError):
                    vol = 0.0
                if vol > 0:
                    ranked.append((name, vol))

            ranked.sort(key=lambda x: x[1], reverse=True)
            top = [coin_to_symbol(name) for name, _ in ranked[:limit]]
            if top:
                logger.info(
                    f"Top {len(top)} volume Hyperliquid: "
                    + ", ".join(f"{n}(${v/1e6:.0f}M)" for n, v in ranked[:limit])
                )
            return top
        except Exception as e:
            logger.warning(f"Discovery top volume Hyperliquid gagal: {e}")
            return []

    async def fetch_all_mids(self) -> Dict[str, float]:
        """
        Ambil seluruh mid price lewat REST.

        Bentuk respons berbeda antara REST dan WS:
          REST -> flat map langsung:  {"BTC": "78000.0", "ETH": "2500.0", ...}
          WS   -> dibungkus:          {"mids": {"BTC": "78000.0", ...}}
        Keduanya ditangani di sini agar pemanggil tidak perlu tahu asalnya.

        Dict REST juga memuat instrumen non-perp (spot dan token prediksi ber-ID
        seperti "#12090"), jadi hasilnya difilter terhadap `_known_coins` bila
        universe sudah dimuat.
        """
        try:
            data = await self.post_info({"type": "allMids"})
            if not isinstance(data, dict):
                return {}
            # REST mengirim flat map; WS membungkusnya di key "mids"
            mids = data.get("mids") if isinstance(data.get("mids"), dict) else data

            out = {}
            for coin, px in mids.items():
                if self._known_coins and coin not in self._known_coins:
                    continue
                try:
                    out[coin] = float(px)
                except (TypeError, ValueError):
                    continue
            return out
        except Exception as e:
            logger.debug(f"allMids Hyperliquid gagal: {e}")
            return {}

    async def fetch_l2_book(self, coin: str, depth: int = 20) -> Optional[dict]:
        """
        Ambil snapshot orderbook L2.

        Catatan: parameter `depth` diterima API tetapi diabaikan — Hyperliquid selalu
        mengembalikan 20 level per sisi. Nilai `n` adalah jumlah order di level itu.
        """
        try:
            data = await self.post_info({"type": "l2Book", "coin": coin, "depth": depth})
            levels = data.get("levels") if isinstance(data, dict) else None
            if not levels or len(levels) < 2:
                return None
            return self._parse_book(coin, levels, data.get("time"))
        except Exception as e:
            logger.debug(f"l2Book {coin} gagal: {e}")
            return None

    async def fetch_candles(
        self, coin: str, interval: str = "1m", limit: int = 120
    ) -> List[list]:
        """
        Ambil lilin historis. Respons tiap elemen:
          {"t","T","s","i","o","c","h","l","v","n"}
        t = open time (ms), T = close time (ms), n = jumlah trade.

        Return format OHLCV standar: [[ts_ms, o, h, l, c, v], ...] agar kompatibel
        dengan PriceFeed.fetch_ohlcv dan tabel `candles`.

        Hyperliquid mengembalikan SELURUH rentang yang diminta tanpa batas jumlah,
        jadi `limit` di sini adalah lebar jendela, bukan pemotongan hasil. Ini
        penting: memotong hasil ke `limit` terakhir akan mengubah "ambil 120 lilin
        terakhir" menjadi "ambil 120 lilin terakhir dari jendela yang lebih lebar",
        sehingga rentang permintaan yang besar tidak berguna.
        """
        try:
            now_ms = int(time.time() * 1000)
            # Interval -> durasi ms untuk menghitung lebar jendela permintaan
            unit_ms = {"m": 60_000, "h": 3_600_000, "d": 86_400_000}
            num = int(interval[:-1]) if interval[:-1].isdigit() else 1
            span = unit_ms.get(interval[-1], 60_000) * num
            start_ms = now_ms - span * (limit + 2)

            raw = await self.post_info({
                "type": "candleSnapshot",
                "req": {
                    "coin": coin,
                    "interval": interval,
                    "startTime": start_ms,
                    "endTime": now_ms,
                },
            })
            if not isinstance(raw, list):
                return []

            out = []
            for c in raw:
                try:
                    out.append([
                        int(c["t"]),
                        float(c["o"]),
                        float(c["h"]),
                        float(c["l"]),
                        float(c["c"]),
                        float(c["v"]),
                    ])
                except (KeyError, TypeError, ValueError):
                    continue
            # Tidak ada pemotongan: seluruh rentang yang diminta dikembalikan.
            return out
        except Exception as e:
            logger.debug(f"candleSnapshot {coin} {interval} gagal: {e}")
            return []

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------
    @staticmethod
    def _parse_book(coin: str, levels: list, ts: Optional[int]) -> dict:
        """
        Ubah struktur l2Book Hyperliquid ke format internal.

        Hyperliquid: levels = [[{"px","sz","n"}, ...bids], [{"px","sz","n"}, ...asks]]
        Internal   : {"bids": [[px, sz], ...], "asks": [[px, sz], ...], "spread", ...}
        """
        def to_pairs(side):
            pairs = []
            for lvl in side:
                try:
                    px = float(lvl["px"])
                    sz = float(lvl["sz"])
                except (KeyError, TypeError, ValueError):
                    continue
                if sz > 0:
                    pairs.append([px, sz])
            return pairs

        bids = to_pairs(levels[0]) if len(levels) > 0 else []
        asks = to_pairs(levels[1]) if len(levels) > 1 else []

        best_bid = bids[0][0] if bids else 0.0
        best_ask = asks[0][0] if asks else 0.0
        mid = (best_bid + best_ask) / 2.0 if (best_bid > 0 and best_ask > 0) else 0.0
        spread = max(best_ask - best_bid, 0.0) if (best_bid > 0 and best_ask > 0) else 0.0

        return {
            "symbol": coin_to_symbol(coin),
            "coin": coin,
            "bids": bids,
            "asks": asks,
            "spread": spread,
            "mid_price": mid,
            "source": "exchange",
            "exchange": "hyperliquid",
            "timestamp": int(ts) if ts else int(time.time() * 1000),
        }

    @staticmethod
    def _parse_candle(data: dict) -> Optional[dict]:
        """Ubah payload channel `candle` ke dict OHLCV internal."""
        try:
            return {
                "timestamp": int(data["t"]),
                "close_time": int(data["T"]),
                "open": float(data["o"]),
                "high": float(data["h"]),
                "low": float(data["l"]),
                "close": float(data["c"]),
                "volume": float(data["v"]),
                "trades": int(data.get("n") or 0),
                "interval": data.get("i", "1m"),
            }
        except (KeyError, TypeError, ValueError):
            return None

    # ------------------------------------------------------------------
    # WebSocket
    # ------------------------------------------------------------------
    def set_watched_coins(self, symbols: List[str]):
        """Perbarui daftar koin yang di-stream orderbook-nya."""
        self._watched_coins = {symbol_to_coin(s) for s in symbols if s}

    def _subscriptions(self) -> List[dict]:
        """
        Susun daftar langganan per koneksi.

        allMids bersifat global (seluruh pasar). Sisanya per koin terpantau:
        l2Book untuk depth, candle untuk lilin live, activeAssetCtx untuk funding
        & open interest, trades untuk ticker tape eksekusi.
        """
        subs = [{"type": "allMids"}]
        for coin in sorted(self._watched_coins):
            subs.append({"type": "l2Book", "coin": coin})
            subs.append({"type": "candle", "coin": coin, "interval": "1m"})
            subs.append({"type": "activeAssetCtx", "coin": coin})
            subs.append({"type": "trades", "coin": coin})
        return subs

    async def _subscribe(self, ws):
        subs = self._subscriptions()
        for sub in subs:
            await ws.send(json.dumps({"method": "subscribe", "subscription": sub}))
        logger.info(
            f"WS Hyperliquid: {len(subs)} langganan dikirim "
            f"({len(self._watched_coins)} koin terpantau + allMids)"
        )

    async def _ping_loop(self, ws):
        """Kirim ping JSON berkala agar koneksi tidak diputus server."""
        try:
            while True:
                await asyncio.sleep(WS_PING_INTERVAL)
                await ws.send(json.dumps({"method": "ping"}))
        except (asyncio.CancelledError, Exception):
            return

    def _handle_message(self, raw: str):
        """
        Tangani satu pesan WS.

        Dijalankan di thread executor, bukan inline di event loop — lihat
        catatan di `websocket_loop`. Fungsi ini tidak boleh menyentuh apa pun
        yang butuh I/O: satu-satunya interaksi yang diizinkan adalah menulis ke
        `market_store` dan kernel mikrostruktur, keduanya state in-memory.
        """
        try:
            msg = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return

        channel = msg.get("channel")
        if not channel:
            return

        self._msg_counts[channel] = self._msg_counts.get(channel, 0) + 1
        data = msg.get("data")

        if channel == "allMids":
            # Satu pesan berisi seluruh mid price pasar
            mids = (data or {}).get("mids") or {}
            for coin, px in mids.items():
                if self._known_coins and coin not in self._known_coins:
                    continue
                try:
                    market_store.set_price(coin_to_symbol(coin), float(px))
                except (TypeError, ValueError):
                    continue
            self._last_mid_time = time.time()

        elif channel == "l2Book":
            if not isinstance(data, dict):
                return
            coin = data.get("coin")
            levels = data.get("levels") or []
            if not coin or len(levels) < 2:
                return
            symbol = coin_to_symbol(coin)
            book = self._parse_book(coin, levels, data.get("time"))
            market_store.set_order_book(symbol, book)
            # Feed L2 juga menyerahkan salinannya ke kernel mikrostruktur.
            # Ini satu-satunya jalan masuk kernel di sistem: kernel dibangun dari
            # aliran bursa, bukan dari polling ulang. `_parse_book` sudah
            # menormalkan ke float, jadi di sini tidak ada casting ulang.
            microstructure.ingest_l2(symbol, book.get("bids", []), book.get("asks", []))
            self._last_book_time = time.time()

        elif channel == "candle":
            if not isinstance(data, dict):
                return
            coin = data.get("coin") or data.get("s")
            candle = self._parse_candle(data)
            if coin and candle:
                market_store.set_live_candle(coin_to_symbol(coin), candle)

        elif channel == "activeAssetCtx":
            if not isinstance(data, dict):
                return
            coin = data.get("coin")
            ctx = data.get("ctx") or {}
            if not coin:
                return
            sym = coin_to_symbol(coin)
            try:
                if ctx.get("funding") is not None:
                    market_store.set_funding(sym, float(ctx["funding"]))
                if ctx.get("openInterest") is not None:
                    market_store.set_open_interest(sym, float(ctx["openInterest"]))
            except (TypeError, ValueError):
                pass

        elif channel == "trades":
            # Sekumpulan trade terbaru — dipakai ticker tape HUD
            if isinstance(data, list) and data:
                coin = data[0].get("coin")
                if coin:
                    market_store.set_recent_trades(coin_to_symbol(coin), data)

    async def websocket_loop(self):
        """
        Loop streaming dengan reconnect otomatis dan exponential backoff.
        Berhenti hanya ketika self._running di-set False.

        Parsing pesan dilakukan di thread executor (`asyncio.to_thread`), bukan
        inline di event loop. Alasannya bukan tampilan: pada top-10 volume, frame
        L2 20-level bisa ratusan per detik, dan `json.loads` untuk masing-masing
        cukup menunda event loop hingga terasa pada detak jantung 0.3 dtk.
        Setiap penundaan itu adalah opportunity yang terlewat.

        Penulisan ke `market_store` tetap inline — keduanya struktur data
        in-memory yang hanya dibaca-tulis dari thread asyncio, jadi tidak ada
        contended lock di sana.
        """
        self._running = True
        backoff = 1.0

        # Kernel dipilih SEBELUM frame pertama diterima, tidak di tengah jalan.
        # Kalau kernel ditukar setelah agen mulai membaca OFI, dua angka dari
        # implementasi berbeda masuk ke pipeline yang sama.
        self._ensure_native_kernel()

        while self._running:
            try:
                async with websockets.connect(
                    HL_WS_URL,
                    open_timeout=10,
                    ping_interval=None,   # ping ditangani aplikasi (protokol Hyperliquid)
                    max_size=WS_MAX_SIZE,
                ) as ws:
                    self._ws_connected = True
                    self._rest_failures = 0
                    backoff = 1.0
                    logger.info("WebSocket Hyperliquid tersambung")

                    await self._subscribe(ws)
                    ping_task = asyncio.create_task(self._ping_loop(ws))

                    try:
                        while self._running:
                            raw = await asyncio.wait_for(ws.recv(), timeout=WS_RECV_TIMEOUT)
                            await asyncio.to_thread(self._handle_message, raw)
                    finally:
                        ping_task.cancel()

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._ws_connected = False
                if self._running:
                    logger.warning(
                        f"WS Hyperliquid terputus ({type(e).__name__}: {e}). "
                        f"Reconnect dalam {backoff:.1f}s"
                    )
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2.0, 30.0)

        self._ws_connected = False
        logger.info("WebSocket Hyperliquid dihentikan")

    def stop(self):
        """Hentikan loop WebSocket."""
        self._running = False

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------
    def is_connected(self) -> bool:
        return self._ws_connected

    def get_status(self) -> dict:
        """Ringkasan kesehatan feed untuk diagnostik HUD."""
        now = time.time()
        return {
            "connected": self._ws_connected,
            "watched_coins": len(self._watched_coins),
            "known_coins": len(self._known_coins),
            "mid_age_s": round(now - self._last_mid_time, 2) if self._last_mid_time else None,
            "book_age_s": round(now - self._last_book_time, 2) if self._last_book_time else None,
            "message_counts": dict(self._msg_counts),
        }
