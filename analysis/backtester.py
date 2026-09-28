"""
analysis/backtester.py — Backtester L2 event-driven dengan model antrean.

Kenapa modul ini ada
---------------------
Backtest yang "%s langsung terisi di mid price" tidak menguji apa pun kecuali
kebetulan. Ia mengasumsikan tiga hal yang tidak pernah terjadi di pasar nyata:

1. **Order kita berada di depan antrean.** Padahal di level harga yang sama,
   ada order lain yang antre lebih dulu di depan order kita. Order yang datang
   belakangan harus menunggu seluruh volume yang ada di depannya sebelum order
   kita bisa terisi.
2. **Ukuran market order kita tidak menggerakkan harga.** Padahal kalau kita
   mendorong book sebesar 5 BTC pada bid 60000, harga bergerak.
3. **Fill selalu tersedia.** Padahal kalau book kosong di harga yang kita
   tawar, order kita tidak pernah terisi.

Modul ini mensimulasikan semuanya dari replay data historis:

  * Snapshot L2 berurutan waktu (bisa JSON Lines, SQLite, atau Parquet)
  * Trade tape historis di timestamp yang sama
  * Order limit kita diantrekan di level tertentu, dan baru dianggap
    terisi setelah volume di depan order kita di level itu habis
  * Market impact dihitung dari kedalaman book pada saat event terjadi

Yang TIDAK dilakukan modul ini: prediksi. Ia tidak menebak ke mana harga akan
pergi. Ia hanya menjawab "kalau kamu kirim order jam 10:03:22 dengan parameter
ini, apa yang sebenarnya terjadi" — jawaban itu yang bisa diuji.
"""

import math
import sqlite3
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional, Sequence, Tuple

from core.config import get_config
from core.logger import get_logger

logger = get_logger("backtester")


# ===========================================================================
# Model data
# ===========================================================================


@dataclass
class L2Snapshot:
    """
    Satu snapshot orderbook penuh pada satu momen.

    `bids` dan `asks` adalah list of (price, size) terurut benar: bids
    menurun (best bid pertama), asks menaik (best ask pertama). Book yang
    tidak terurut tidak bisa dipakai untuk apa pun selain dekorasi.
    """
    timestamp: float
    symbol: str
    bids: List[Tuple[float, float]]
    asks: List[Tuple[float, float]]

    def best_bid(self) -> Optional[float]:
        return self.bids[0][0] if self.bids else None

    def best_ask(self) -> Optional[float]:
        return self.asks[0][0] if self.asks else None

    def mid(self) -> Optional[float]:
        b, a = self.best_bid(), self.best_ask()
        if b is None or a is None:
            return None
        return (b + a) / 2.0

    def bid_depth(self, n: int) -> float:
        return sum(sz for _, sz in self.bids[:n])

    def ask_depth(self, n: int) -> float:
        return sum(sz for _, sz in self.asks[:n])


@dataclass
class TapeTrade:
    """Satu trade yang benar-benar terjadi di pasar."""
    timestamp: float
    price: float
    size: float
    side: str = "BUY"   # BUY = agresor beli (menghantam bid), SELL = agresor jual


@dataclass
class PendingOrder:
    """
    Order limit yang menunggu giliran antrean.

    `queue_ahead` adalah volume yang harus terisi lebih dulu sebelum
    order kita bisa terisi. Di awal = volume yang sudah mengqueue di level
    harga yang sama saat order dikirim.
    """
    symbol: str
    side: str            # "BUY" atau "SELL"
    price: float
    quantity: float
    timestamp: float
    remaining: float
    queue_ahead: float
    filled_quantity: float = 0.0
    fill_price: Optional[float] = None
    reason: str = "SIGNAL"

    @property
    def is_filled(self) -> bool:
        return self.remaining <= 1e-12

    @property
    def is_expired(self) -> bool:
        return self.remaining > 1e-12


@dataclass
class Fill:
    """Hasil eksekusi satu order."""
    symbol: str
    side: str
    quantity: float
    price: float            # harga rata-rata yang benar-benar dibayar
    mid_at_fill: Optional[float]
    slippage: float          # harga - mid, dalam satuan absolut
    slippage_bps: float      # dalam basis point
    fees: float
    timestamp: float


@dataclass
class BacktestStats:
    """Statistik performa. Field numerik selalu terisi; field `None` berarti
    metrik tidak bisa dihitung (bukan nol)."""
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    net_pnl: float = 0.0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    profit_factor: Optional[float] = None
    win_rate: Optional[float] = None
    sharpe: Optional[float] = None
    sortino: Optional[float] = None
    max_drawdown: Optional[float] = None
    max_drawdown_pct: Optional[float] = None
    avg_slippage_bps: Optional[float] = None
    total_fees: float = 0.0
    unfilled_orders: int = 0
    equity_curve: List[Tuple[float, float]] = field(default_factory=list)


# ===========================================================================
# Metrik
# ===========================================================================


def _returns(equity: List[float]) -> List[float]:
    """Return per periode dari kurva equity."""
    if len(equity) < 2:
        return []
    return [
        (equity[i] - equity[i - 1]) / equity[i - 1]
        for i in range(1, len(equity))
        if equity[i - 1] > 0
    ]


def sharpe_ratio(returns: Sequence[float], risk_free: float = 0.0) -> Optional[float]:
    """
    Sharpe ratio per-periode.

    None (bukan 0.0) bila return kurang dari 2 atau deviasi standarnya nol.
    Mengembalikan 0.0 untuk data kosong akan terbaca sebagai "performa netral",
    padahal yang sebenarnya adalah "tidak ada data untuk dinilai".
    """
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    excess = mean - risk_free
    var = sum((r - excess) ** 2 for r in returns) / (len(returns) - 1)
    std = math.sqrt(var)
    if std <= 0:
        return None
    return excess / std


def sortino_ratio(returns: Sequence[float], risk_free: float = 0.0) -> Optional[float]:
    """
    Sortino ratio: seperti Sharpe, tapi downside deviation sebagai penyebut.

    Rasio ini lebih jujur untuk strategi trading: return yang naik tidak
    boleh dihukum, sedangkan return yang turun harus dihukum proporsional
    besarnya. Memakai standard deviation sebagai penyebut akan menghukum
    keduanya dengan bobot yang sama.
    """
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    excess = mean - risk_free
    downside = [min(0.0, r - risk_free) for r in returns]
    dd_var = sum(d * d for d in downside) / len(downside)
    dd_std = math.sqrt(dd_var)
    if dd_std <= 0:
        # Tidak ada periode rugi sama sekali. Sortino tidak terdefinisi
        # secara matematis di sini — bukan "sangat baik".
        return None
    return excess / dd_std


def max_drawdown(equity: List[float]) -> Tuple[Optional[float], Optional[float]]:
    """
    Max drawdown absolut dan relatif dari kurva equity.

    Mengembalikan (absolut, persentase). Keduanya None bila kurva kosong.
    """
    if len(equity) < 2:
        return None, None
    peak = equity[0]
    worst_abs = 0.0
    worst_pct = 0.0
    for value in equity:
        if value > peak:
            peak = value
        if peak > 0:
            drawdown = peak - value
            if drawdown > worst_abs:
                worst_abs = drawdown
                worst_pct = drawdown / peak
    return worst_abs, worst_pct


# ===========================================================================
# Model antrean (queue position)
# ===========================================================================


class QueueFillModel:
    """
    Model eksekusi limit order berbasis antrean.

    Ini adalah inti modul ini, dan alasan ia bukan sekadar "isi order di harga
    yang sama". Perbedaannya bukan pada harga — semuanya fill di harga limit
    yang sama — tapi pada KAPAN.

    Mekanismenya:

    1. Saat order dikirim, `queue_ahead` di-set ke volume yang SUDAH mengqueue
       di level harga tersebut. Kalau kita mengqueue di belakang 10 BTC yang
       sudah antre, order kita tidak mungkin terisi sebelum 10 BTC itu habis.

    2. Setiap trade yang terjadi di level harga yang sama mengurangi
       `queue_ahead`. Baru setelah `queue_ahead` habis, trade berikutnya
       mulai mengisi order kita.

    3. Order dengan `queue_ahead` awal nol (kita yang pertama) langsung bisa
       terisi oleh trade berikutnya.

    **Asumsi yang dinyatakan terbuka:** trade di level harga dianggap berurutan
    FIFO di dalam level itu. Bursa nyata memakai price-time priority, dan
    FIFO adalah approximasi yang wajar untuk level dengan banyak order. Yang
    TIDAK boleh dilakukan adalah mengasumsikan order kita selalu ada di depan
    — itulah yang membuat backtest terlalu optimis.

    **Conservative fill:** kalau level harga yang kita tawar tidak ada di
    book, order TIDAK diisi. Book kosong di harga itu berarti tidak ada yang
    mau bertransaksi di sana, dan mengisi order kita di harga itu berarti
    mengarang likuiditas.
    """

    def __init__(self, initial_queue_ahead: float = 0.0):
        self.queue_ahead = max(0.0, float(initial_queue_ahead))
        self.filled = 0.0
        self.total_traded_through = 0.0

    def on_trade(self, trade_size: float) -> float:
        """
        Proses satu trade di level harga yang sama.

        Mengembalikan kuantitas yang benar-benar terisi untuk order kita pada
        trade ini (bisa 0.0).
        """
        if trade_size <= 0:
            return 0.0

        self.total_traded_through += trade_size

        # Volume ini masih milik order yang antre di depan kita.
        if self.queue_ahead > 0:
            consumed = min(self.queue_ahead, trade_size)
            self.queue_ahead -= consumed
            leftover = trade_size - consumed
            if leftover <= 0:
                return 0.0
            return leftover

        return trade_size

    @property
    def has_priority(self) -> bool:
        """True kalau antrean di depan kita sudah habis."""
        return self.queue_ahead <= 1e-12


# ===========================================================================
# Data historis
# ===========================================================================


def load_l2_events(sqlite_path: str) -> Iterator[Tuple[str, float, object]]:
    """
    Baca event historis dari SQLite, urut waktu.

    Menghasilkan tuple (jenis, timestamp, payload) dengan jenis ∈
    {"snapshot", "trade"}.

    Skema yang diharapkan — sengaja minimal supaya mudah dibuat dari data
    mentah apa pun (JSON Lines dari WebSocket, arsip bursa, replay TradingView):

        CREATE TABLE l2_snapshots (
            ts REAL, symbol TEXT, bids TEXT, asks TEXT
        );   -- bids/asks berisi JSON: [[px, sz], ...]
        CREATE TABLE l2_trades (
            ts REAL, symbol TEXT, price REAL, size REAL, side TEXT
        );

    Dua tabel dipisah, dan itu disengaja: snapshot dan trade bukan event yang
    sama. Menggabungkannya dalam satu tabel memaksa kita menyimpan book penuh
    di setiap baris trade, yang boros dan lambat dibaca.
    """
    conn = sqlite3.connect(sqlite_path)
    try:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            """
            SELECT ts, symbol, bids, asks FROM l2_snapshots
            UNION ALL
            SELECT ts, symbol, price, size, side FROM l2_trades
            ORDER BY ts ASC
            """
        )
        for row in cursor:
            if row["bids"] is not None:
                yield ("snapshot", row["ts"], dict(row))
            else:
                yield ("trade", row["ts"], dict(row))
    finally:
        conn.close()


def synthesize_walk(
    base_price: float = 100.0,
    steps: int = 600,
    step_seconds: float = 1.0,
    spread_bps: float = 2.0,
    depth_levels: int = 5,
    base_size: float = 10.0,
    trade_rate: float = 4.0,
    seed: int = 7,
) -> Tuple[List[L2Snapshot], List[TapeTrade]]:
    """
    Bangun data L2 sintetis yang deterministik untuk pengujian.

    DETERMINISTIK adalah syarat, bukan pilihan: test yang memakai RNG tanpa
    seed menghasilkan angka berbeda setiap kali, dan kegagalan test jadi tidak
    bisa direproduksi.

    Yang disimulasikan:
      * Random walk pada mid price.
      * Book dua sisi dengan spread tetap dan kedalaman yang menurun per level.
      * Trade tape acak pada kedua sisi, dengan probabilitas menyalin
        kedalaman book di level tersebut.

    Parameter seeded dari `seed` supaya test bisa mengatur skenario tertentu
    (mis. volatilitas rendah vs tinggi) hanya lewat `step_bps`.
    """
    import random

    rng = random.Random(seed)
    snapshots: List[L2Snapshot] = []
    trades: List[TapeTrade] = []

    mid = base_price
    ts = 1_700_000_000.0
    half_spread = mid * spread_bps / 2.0 / 10_000.0

    for i in range(steps):
        # Random walk. Volatilitas per langkah sengaja kecil supaya hasilnya
        # realistis untuk simulasi scalp, bukan untuk swing.
        mid += mid * rng.gauss(0.0, 0.00015)

        bids = []
        asks = []
        for level in range(depth_levels):
            decay = (1.0 - 0.15 * level)
            bids.append((round(mid - half_spread - level * half_spread * 0.5, 8),
                         round(base_size * decay + rng.uniform(0, base_size * 0.1), 8)))
            asks.append((round(mid + half_spread + level * half_spread * 0.5, 8),
                         round(base_size * decay + rng.uniform(0, base_size * 0.1), 8)))

        snapshots.append(L2Snapshot(
            timestamp=ts, symbol="TEST/USDT:USDT", bids=bids, asks=asks
        ))

        # Trade tape pada level atas, acak sisi.
        n_trades = int(rng.random() * trade_rate)
        for _ in range(n_trades):
            buy = rng.random() < 0.5
            if buy and bids:
                level = rng.randrange(len(bids))
                price, size = bids[level]
            elif asks:
                level = rng.randrange(len(asks))
                price, size = asks[level]
            else:
                continue
            # Volume trade tidak melebihi apa yang ada di book.
            trades.append(TapeTrade(
                timestamp=ts, price=price,
                size=round(size * rng.uniform(0.1, 0.6), 8),
                side="BUY" if buy else "SELL",
            ))

        ts += step_seconds

    return snapshots, trades


# ===========================================================================
# Engine
# ===========================================================================


@dataclass
class CompletedTrade:
    """Satu siklus trade lengkap: masuk dan keluar."""
    symbol: str
    side: str
    quantity: float
    entry_price: float
    exit_price: float
    entry_fill: Optional[Fill]
    exit_fill: Optional[Fill]
    pnl: float
    fees: float
    net_pnl: float
    reason: str
    entry_ts: float
    exit_ts: float


class L2Backtester:
    """
    Backtester event-driven dengan model antrean dan market impact.

    Alur pemakaian::

        tester = L2Backtester(initial_balance=10000.0)
        tester.replay(snapshots, trades)
        tester.submit_limit("TEST/USDT:USDT", "BUY", price, qty)
        stats = tester.stats()

    Setiap event (snapshot atau trade) diproses **sekuensial** dalam urutan
    waktu. Urutan ini bukan detail teknis: kalau snapshot dan trade diproses
    dalam urutan yang salah, antrean dihitung dari book yang belum pernah ada.
    """

    def __init__(
        self,
        initial_balance: float = 10000.0,
        taker_fee: float = 0.0005,
        max_book_levels: int = 5,
        impact_coefficient: float = 0.5,
    ):
        self.initial_balance = float(initial_balance)
        self.cash = float(initial_balance)
        self.taker_fee = float(taker_fee)
        self.max_book_levels = int(max_book_levels)
        self.impact_coefficient = float(impact_coefficient)

        self.book: Dict[str, L2Snapshot] = {}
        self.pending: List[PendingOrder] = []
        self.fills: List[Fill] = []
        self.completed: List[CompletedTrade] = []

        # Posisi yang sedang dibuka di simulasi.
        self.open_trade: Optional[CompletedTrade] = None
        self.equity_curve: List[Tuple[float, float]] = [(0.0, self.cash)]

        self.current_ts: float = 0.0

    # ------------------------------------------------------------------
    # Market impact
    # ------------------------------------------------------------------

    def _worst_fill_price(
        self, symbol: str, side: str, quantity: float
    ) -> Optional[Tuple[float, float]]:
        """
        Harga rata-rata kalau order langsung memakan book (market impact).

        Ini TIDAK menganggap fill di mid. Kita menelusuri book level demi level
        dari sisi yang benar, mengumpulkan volume sampai quantity kita habis,
        lalu mengambil rata-rata berbobot dari harga-harga itu.

        Kalau book di sisi itu lebih tipis dari order kita, sisa quantity
        TIDAK diisi pada harga apa pun: book habis adalah informasi, dan
        mengarang harga untuk sisanya berarti mengarang likuiditas.

        Mengembalikan (harga_rata_rata, kuantitas_terisi) atau None kalau
        tidak ada book sama sekali.
        """
        snap = self.book.get(symbol)
        if snap is None:
            return None

        # BUY memakan ASK (kita mengangkat offer), SELL memakan BID (kita
        # menekan bid). Membalik ini membuat market order terlihat lebih murah
        # dari seharusnya — bias yang persis ingin kita hindari.
        levels = snap.asks if side == "BUY" else snap.bids
        if not levels:
            return None

        remaining = float(quantity)
        notional = 0.0
        filled = 0.0
        for price, size in levels[:self.max_book_levels]:
            if remaining <= 1e-12:
                break
            take = min(size, remaining)
            if take <= 0:
                continue
            notional += price * take
            filled += take
            remaining -= take

        if filled <= 1e-12:
            return None
        return notional / filled, filled

    def _estimate_impact_bps(
        self, symbol: str, side: str, quantity: float
    ) -> Optional[float]:
        """
        Estimasi market impact dalam basis point, dari rasio ukuran order
        terhadap kedalaman book.

        Pendekatan linier yang disengaja: model impact yang akurat butuh data
        eksekusi yang mahal, dan untuk keputusan "apakah order ini terlalu
        besar" yang dibutuhkan backtest, linier sudah cukup untuk
        membedakan "seimbang" dari "akan menggerakkan harga".
        """
        snap = self.book.get(symbol)
        if snap is None:
            return None
        side_depth = (
            snap.ask_depth(self.max_book_levels) if side == "BUY"
            else snap.bid_depth(self.max_book_levels)
        )
        if side_depth <= 0:
            return None
        mid = snap.mid()
        if not mid or mid <= 0:
            return None
        participation = quantity / side_depth
        return self.impact_coefficient * participation * 10_000.0

    # ------------------------------------------------------------------
    # Antrean & order
    # ------------------------------------------------------------------

    def _queue_ahead_at_price(self, symbol: str, side: str, price: float) -> float:
        """
        Volume yang sudah mengqueue di level harga tersebut saat order dikirim.

        Untuk order BUY, kita antre di belakang volume yang sudah ada di
        level bid itu. Volume yang ada itu ADALAH antrean di depan kita.

        ⚠️ HATI-HATI: baris di bawah sengaja memakai `bids if BUY`, yang
        TERBALIK dari `_worst_fill_price` di atas (yang memakai `asks if BUY`).

        Bedanya nyata, bukan salah ketik:
          * `_worst_fill_price` = order yang langsung memakan book (market
            order). BUY mengangkat offer, jadi consumes asks.
          * `_queue_ahead_at_price` = order yang bertahan di book sebagai
            maker. BUY duduk di antrean bid, jadi reads bids.

        Menyalin satu ke yang lain tanpa komentar ini adalah sumber bug market
        impact yang membuat fill terlihat "terlalu murah".
        """
        snap = self.book.get(symbol)
        if snap is None:
            return 0.0
        levels = snap.bids if side == "BUY" else snap.asks
        for level_price, level_size in levels:
            if abs(level_price - price) < 1e-9:
                return level_size
        return 0.0

    def submit_limit(
        self,
        symbol: str,
        side: str,
        price: float,
        quantity: float,
        reason: str = "SIGNAL",
    ) -> Optional[PendingOrder]:
        """
        Daftarkan limit order ke antrean.

        Order TIDAK langsung terisi. Ia menunggu trade di level harga yang
        sama, dan baru terisi setelah volume di depannya habis.

        Mengembalikan objek order, atau None kalau price/quantity tidak valid.
        """
        if side not in ("BUY", "SELL"):
            raise ValueError(f"side tidak dikenal: {side!r}")
        if price <= 0 or quantity <= 0:
            return None

        order = PendingOrder(
            symbol=symbol,
            side=side,
            price=float(price),
            quantity=float(quantity),
            timestamp=self.current_ts,
            remaining=float(quantity),
            queue_ahead=self._queue_ahead_at_price(symbol, side, price),
            reason=reason,
        )
        self.pending.append(order)
        return order

    # ------------------------------------------------------------------
    # Pemrosesan event
    # ------------------------------------------------------------------

    def on_snapshot(self, snap: L2Snapshot) -> None:
        """Proses satu snapshot L2."""
        self.book[snap.symbol] = snap
        self.current_ts = max(self.current_ts, snap.timestamp)
        self._mark_equity(snap.timestamp)

    def on_trade_event(self, trade: TapeTrade) -> None:
        """
        Proses satu trade dari tape.

        Di sinilah antrian berkurang. Order pending pada level harga yang sama
        dengan trade inilah yang bisa menerima fill — dan hanya setelah
        volume di depannya habis.
        """
        self.current_ts = max(self.current_ts, trade.timestamp)

        for order in list(self.pending):
            if order.is_filled:
                self.pending.remove(order)
                continue
            if abs(order.price - trade.price) > 1e-9:
                continue

            model = QueueFillModel(order.queue_ahead)
            filled_now = model.on_trade(trade.size)
            order.queue_ahead = model.queue_ahead

            if filled_now <= 0:
                continue

            take = min(filled_now, order.remaining)
            order.remaining -= take
            order.filled_quantity += take

            snap = self.book.get(order.symbol)
            mid = snap.mid() if snap else None
            fees = take * order.price * self.taker_fee
            slippage = 0.0
            slippage_bps = 0.0
            if mid and mid > 0:
                slippage = (
                    (order.price - mid) if order.side == "BUY"
                    else (mid - order.price)
                )
                slippage_bps = slippage / mid * 10_000.0

            self.fills.append(Fill(
                symbol=order.symbol, side=order.side, quantity=take,
                price=order.price, mid_at_fill=mid, slippage=slippage,
                slippage_bps=slippage_bps, fees=fees, timestamp=trade.timestamp,
            ))

            if order.is_filled:
                self.pending.remove(order)

        self._mark_equity(trade.timestamp)

    def open_position(
        self,
        symbol: str,
        side: str,
        quantity: float,
        reason: str = "SIGNAL",
        use_market_impact: bool = True,
    ) -> Optional[CompletedTrade]:
        """
        Buka posisi di market, dengan market impact dari kedalaman book.

        Berbeda dengan `submit_limit`, ini order yang langsung mengeksekusi
        book — dan karena itu ia memakai `_worst_fill_price`, bukan mid.

        Mengembalikan `CompletedTrade` yang masih terbuka, atau None kalau
        tidak ada likuiditas cukup.
        """
        if quantity <= 0:
            return None

        if use_market_impact:
            worst = self._worst_fill_price(symbol, side, quantity)
            if worst is None:
                logger.debug(f"Tidak ada likuiditas untuk {side} {symbol}")
                return None
            price, filled_qty = worst
        else:
            snap = self.book.get(symbol)
            mid = snap.mid() if snap else None
            if mid is None or mid <= 0:
                return None
            price, filled_qty = mid, float(quantity)

        fees = filled_qty * price * self.taker_fee
        if side == "BUY":
            self.cash -= filled_qty * price + fees
        else:
            self.cash += filled_qty * price - fees

        snap = self.book.get(symbol)
        mid_at_fill = snap.mid() if snap else None
        slippage = 0.0
        slippage_bps = 0.0
        if mid_at_fill and mid_at_fill > 0:
            slippage = (
                (price - mid_at_fill) if side == "BUY"
                else (mid_at_fill - price)
            )
            slippage_bps = slippage / mid_at_fill * 10_000.0

        fill = Fill(
            symbol=symbol, side=side, quantity=filled_qty, price=price,
            mid_at_fill=mid_at_fill, slippage=slippage,
            slippage_bps=slippage_bps, fees=fees, timestamp=self.current_ts,
        )
        self.fills.append(fill)

        trade = CompletedTrade(
            symbol=symbol, side=side, quantity=filled_qty,
            entry_price=price, exit_price=0.0,
            entry_fill=fill, exit_fill=None,
            pnl=0.0, fees=fees, net_pnl=0.0,
            reason=reason, entry_ts=self.current_ts, exit_ts=0.0,
        )
        self.open_trade = trade
        return trade

    def close_position(self, reason: str = "SIGNAL", use_market_impact: bool = True):
        """
        Tutup posisi yang sedang terbuka, lalu hitung PnL bersih.

        Mengembalikan `CompletedTrade` yang sudah selesai, atau None kalau
        tidak ada posisi terbuka atau book sudah habis.
        """
        trade = self.open_trade
        if trade is None:
            return None

        exit_side = "SELL" if trade.side == "BUY" else "BUY"
        if use_market_impact:
            worst = self._worst_fill_price(trade.symbol, exit_side, trade.quantity)
            if worst is None:
                logger.debug("Book habis saat menutup — posisi tetap terbuka")
                return None
            price, filled_qty = worst
        else:
            snap = self.book.get(trade.symbol)
            mid = snap.mid() if snap else None
            if mid is None or mid <= 0:
                return None
            price, filled_qty = mid, trade.quantity

        exit_fees = filled_qty * price * self.taker_fee
        if trade.side == "BUY":
            self.cash += filled_qty * price - exit_fees
        else:
            self.cash -= filled_qty * price + exit_fees

        snap = self.book.get(trade.symbol)
        mid_at_exit = snap.mid() if snap else None
        exit_slippage = 0.0
        exit_slippage_bps = 0.0
        if mid_at_exit and mid_at_exit > 0:
            exit_slippage = (
                (mid_at_exit - price) if trade.side == "BUY"
                else (price - mid_at_exit)
            )
            exit_slippage_bps = exit_slippage / mid_at_exit * 10_000.0

        exit_fill = Fill(
            symbol=trade.symbol, side=exit_side, quantity=filled_qty,
            price=price, mid_at_fill=mid_at_exit, slippage=exit_slippage,
            slippage_bps=exit_slippage_bps, fees=exit_fees,
            timestamp=self.current_ts,
        )
        self.fills.append(exit_fill)

        if trade.side == "BUY":
            gross = (price - trade.entry_price) * filled_qty
        else:
            gross = (trade.entry_price - price) * filled_qty

        total_fees = trade.fees + exit_fees
        trade.exit_price = price
        trade.exit_fill = exit_fill
        trade.pnl = gross
        trade.fees = total_fees
        trade.net_pnl = gross - total_fees
        trade.reason = reason
        trade.exit_ts = self.current_ts

        self.completed.append(trade)
        self.open_trade = None
        self._mark_equity(self.current_ts)
        return trade

    def _mark_equity(self, timestamp: float) -> None:
        """
        Catat titik equity.

        Unrealized PnL ikut dihitung dari harga book terakhir, supaya kurva
        equity mencerminkan nilai portofolio yang sebenarnya — bukan hanya
        kas setelah trade yang sudah tertutup.
        """
        equity = self.cash
        trade = self.open_trade
        if trade is not None:
            snap = self.book.get(trade.symbol)
            mid = snap.mid() if snap else None
            if mid and mid > 0:
                if trade.side == "BUY":
                    equity += (mid - trade.entry_price) * trade.quantity
                else:
                    equity += (trade.entry_price - mid) * trade.quantity
        self.equity_curve.append((timestamp, equity))

    # ------------------------------------------------------------------
    # Replay
    # ------------------------------------------------------------------

    def replay(
        self,
        snapshots: List[L2Snapshot],
        trades: List[TapeTrade],
        strategy=None,
    ) -> "L2Backtester":
        """
        Jalankan ulang event historis secara berurutan waktu.

        `strategy` opsional: callable(tester, snapshot) -> None, dipanggil
        setiap snapshot. Di situlah logika entry bot disuntikkan — backtester
        sendiri tidak memutuskan kapan harus bertransaksi, karena itu
        keputusan strategi, bukan keputusan simulator.

        Event digabung lalu diurutkan satu kali. Mengurutkan ulang per event
        akan membuat hasilnya berbeda tergantung urutan input, dan itu bug
        yang sangat sulit dikenali.
        """
        events: List[Tuple[float, int, object]] = []
        for snap in snapshots:
            # Snapshot didahulukan pada timestamp yang sama: book harus ada
            # SEBELUM trade diproses, kalau tidak antrean dihitung dari book
            # yang belum pernah ada.
            events.append((snap.timestamp, 0, snap))
        for trade in trades:
            events.append((trade.timestamp, 1, trade))

        # Kunci urut HARUS konsisten tipe: (timestamp, kind). Mixed float/tuple
        # akan membuat Python membandingkan tuple dengan float dan melempar
        # TypeError di tengah replay.
        events.sort(key=lambda e: (e[0], e[1]))

        for _, kind, payload in events:
            if kind == 0:
                self.on_snapshot(payload)
                if strategy is not None:
                    strategy(self, payload)
            else:
                self.on_trade_event(payload)
        return self

    # ------------------------------------------------------------------
    # Statistik
    # ------------------------------------------------------------------

    def stats(self) -> BacktestStats:
        """
        Hitung statistik performa lengkap.

        Metrik yang tidak bisa dihitung dikembalikan `None`, bukan 0.0.
        `profit_factor = 0.0` dan `profit_factor tidak bisa dihitung` adalah
        dua klaim yang sangat berbeda, dan yang kedua jauh lebih jujur.
        """
        completed = self.completed
        st = BacktestStats()
        st.total_trades = len(completed)
        st.unfilled_orders = len(self.pending)
        st.equity_curve = list(self.equity_curve)

        for trade in completed:
            st.net_pnl += trade.net_pnl
            st.total_fees += trade.fees
            if trade.net_pnl > 0:
                st.wins += 1
                st.gross_profit += trade.net_pnl
            else:
                st.losses += 1
                st.gross_loss += abs(trade.net_pnl)

        if st.total_trades > 0:
            st.win_rate = st.wins / st.total_trades

        if st.gross_loss > 0:
            st.profit_factor = st.gross_profit / st.gross_loss
        elif st.gross_profit > 0:
            # Tidak ada kerugian sama sekali. PF secara matematis tak
            # hingga, dan HUD sudah punya konvensi "N/A" untuk hal seperti
            # ini. None lebih jujur daripada float("inf") yang bisa merusak
            # perhitungan di hilir.
            st.profit_factor = None

        equity_values = [eq for _, eq in self.equity_curve]
        rets = _returns(equity_values)
        st.sharpe = sharpe_ratio(rets)
        st.sortino = sortino_ratio(rets)
        st.max_drawdown, st.max_drawdown_pct = max_drawdown(equity_values)

        if self.fills:
            st.avg_slippage_bps = sum(
                abs(f.slippage_bps) for f in self.fills
            ) / len(self.fills)

        return st

    def report(self) -> str:
        """Ringkasan statistik dalam bentuk teks, siap dicetak ke terminal."""
        st = self.stats()

        def fmt(value, spec="{:.2f}", none="N/A"):
            if value is None:
                return none
            return spec.format(value)

        lines = [
            "=" * 62,
            " L2 EVENT-DRIVEN BACKTEST — LAPORAN",
            "=" * 62,
            f" Trade selesai      : {st.total_trades}",
            f" Order tidak terisi : {st.unfilled_orders}",
            f" Win / Loss         : {st.wins} / {st.losses}",
            f" Win rate           : {fmt(st.win_rate, '{:.1%}')}",
            f" PnL kotor          : {fmt(st.net_pnl + st.total_fees, '{:+,.2f}')}",
            f" Total fee          : {fmt(st.total_fees, '{:,.4f}')}",
            f" PnL bersih         : {fmt(st.net_pnl, '{:+,.2f}')}",
            "-" * 62,
            f" Profit factor      : {fmt(st.profit_factor)}",
            f" Sharpe ratio       : {fmt(st.sharpe)}",
            f" Sortino ratio      : {fmt(st.sortino)}",
            f" Max drawdown       : {fmt(st.max_drawdown, '{:,.2f}')} "
            f"({fmt(st.max_drawdown_pct, '{:.2%}')})",
            f" Avg slippage       : {fmt(st.avg_slippage_bps, '{:.2f}')} bps",
            "=" * 62,
        ]
        return "\n".join(lines)

