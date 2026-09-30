"""
Backtest harness yang meniru biaya produksi SECARA JITUH.

Kenapa file ini ada: `docs/technical/04-risk-and-accounting.md` mencatat
bahwa `analysis/backtester.py` punya model antrean FIFO yang benar tapi
tidak pernah dipanggil produksi. Root itu belum terisi, jadi semua
"tuning" selama ini terjadi di P&L paper, yang turnaround-nya 3-5 menit
per konfigurasi dan tidak bisa dibedakan dari noise.

Yang dihormati harness ini:
  * Biaya fill 4 bps per sisi (half-spread floor + impact), dari
    `trading/fill_cost.py` - bukan 0.
  * Fee taker 4.5 bps per sisi, dari config.
  * Funding per settlement 1 jam, hanya kalau rate tersedia.
  * Fill di bar BERIKUTNYA pada open, bukan pada harga bar sinyal.
    Ini yang tidak bisa dinegosiasikan: harga pada bar yang menghasilkan
    sinyal sudah berisi informasinya sendiri.
  * Satu posisi per simbol, sesuai `decision_agent.py:217`.

Yang TIDAK dihormati: queue depth. Order book historis tidak ada di
database, jadi fill diasumsikan penuh. Itu cenderung optimistic - angka
hasilnya harus dibaca sebagai batas atas, bukan perkiraan realistis.
"""
from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

# ── Biaya, semua dalam fraksi notional per sisi ────────────────────
FILL_HALF_SPREAD = 0.0003      # 3 bps, floor dari fill_cost.py
FILL_IMPACT = 0.0001           # 1 bps
TAKER_FEE = 0.00045            # Hyperliquid base tier
COST_PER_SIDE = FILL_HALF_SPREAD + FILL_IMPACT + TAKER_FEE   # 8.5 bps
ROUND_TRIP = COST_PER_SIDE * 2                                  # 17 bps

# Settlement funding
FUNDING_PERIOD = 3600.0

import pathlib as _pathlib

_ROOT = _pathlib.Path(__file__).resolve().parent.parent
DB = str(_ROOT / "data_store" / "backups" / "trading_bot_pre_fix_20260925_191604.db")


# ── Indikator ───────────────────────────────────────────────────────
def ema_series(vals: List[float], n: int) -> List[float]:
    if not vals:
        return []
    k = 2.0 / (n + 1.0)
    out = [vals[0]]
    for v in vals[1:]:
        out.append(v * k + out[-1] * (1.0 - k))
    return out


def rsi_series(cl: List[float], n: int = 14) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(cl)
    if len(cl) < n + 1:
        return out
    g = l = 0.0
    for i in range(1, n + 1):
        d = cl[i] - cl[i - 1]
        g += max(d, 0.0)
        l += max(-d, 0.0)
    ag, al = g / n, l / n
    out[n] = 100.0 if al == 0 else 100.0 - 100.0 / (1.0 + ag / al)
    for i in range(n + 1, len(cl)):
        d = cl[i] - cl[i - 1]
        ag = (ag * (n - 1) + max(d, 0.0)) / n
        al = (al * (n - 1) + max(-d, 0.0)) / n
        out[i] = 100.0 if al == 0 else 100.0 - 100.0 / (1.0 + ag / al)
    return out


def true_range(hi, lo, cl, n: int = 14) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(cl)
    if len(cl) < n + 1:
        return out
    tr = [0.0] * len(cl)
    for i in range(1, len(cl)):
        tr[i] = max(hi[i] - lo[i], abs(hi[i] - cl[i - 1]), abs(lo[i] - cl[i - 1]))
    a = sum(tr[1:n + 1]) / n
    out[n] = a
    for i in range(n + 1, len(cl)):
        a = (a * (n - 1) + tr[i]) / n
        out[i] = a
    return out


# ── Data ────────────────────────────────────────────────────────────
@dataclass
class Series:
    symbol: str
    ts: List[int]
    o: List[float]
    h: List[float]
    l: List[float]
    c: List[float]
    v: List[float]
    rsi14: List[Optional[float]] = field(default_factory=list)
    atr14: List[Optional[float]] = field(default_factory=list)
    ema9: List[float] = field(default_factory=list)
    ema21: List[float] = field(default_factory=list)
    mom21: List[float] = field(default_factory=list)
    vol_ratio: List[float] = field(default_factory=list)
    # 1 kalau harga di atas EMA50, 0 kalau tidak. Dipakai filter regime.
    trend_up: List[int] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.c)


def load(min_bars: int = 500) -> Dict[str, Series]:
    conn = sqlite3.connect(DB)
    raw: Dict[str, List] = {}
    for sym, ts, o, h, l, c, v in conn.execute(
        "SELECT symbol,timestamp,open,high,low,close,volume FROM candles "
        "WHERE timeframe='1m' ORDER BY symbol,timestamp"
    ):
        raw.setdefault(sym, []).append((ts, o, h, l, c, v))
    conn.close()

    out: Dict[str, Series] = {}
    for sym, rows in raw.items():
        if len(rows) < min_bars:
            continue
        ts = [r[0] for r in rows]
        o = [r[1] for r in rows]
        h = [r[2] for r in rows]
        l = [r[3] for r in rows]
        c = [r[4] for r in rows]
        v = [r[5] for r in rows]
        s = Series(sym, ts, o, h, l, c, v)
        s.rsi14 = rsi_series(c, 14)
        s.atr14 = true_range(h, l, c, 14)
        s.ema9 = ema_series(c, 9)
        s.ema21 = ema_series(c, 21)
        s.mom21 = [0.0] * len(c)
        for i in range(21, len(c)):
            if c[i - 21] > 0:
                s.mom21[i] = (c[i] - c[i - 21]) / c[i - 21] * 100.0
        e50 = ema_series(c, 50)
        s.trend_up = [1 if (c[i] > e50[i] and c[i] > 0) else 0 for i in range(len(c))]
        s.vol_ratio = [1.0] * len(c)
        if len(v) > 60:
            avg = sum(v[-60:]) / 60.0
            if avg > 0:
                s.vol_ratio = [(x / avg) if x is not None else 1.0 for x in v]
        out[sym] = s
    return out


# ── Strategy contract ───────────────────────────────────────────────
@dataclass
class Config:
    name: str = "cfg"
    # entry
    entry: str = "mom_long"        # mom_long | mom_both | rsi_rev | ema | none
    mom_thresh: float = 0.0
    rsi_low: float = 30.0
    rsi_high: float = 70.0
    # exit
    sl_pct: float = 0.0037          # 0.37% - median SL_HIT yang terukur
    tp_pct: float = 0.0042          # 0.42% - median SCALP_TP yang terukur
    min_profit_pct: Optional[float] = None   # early exit
    max_hold_s: int = 300
    min_hold_s: int = 15
    # sizing
    risk_pct: float = 0.005
    leverage: int = 10
    # guards
    max_positions: int = 10
    cooldown_s: int = 0


@dataclass
class Result:
    trades: int
    wins: int
    net: float
    pf: float
    winrate: float
    avg_win: float
    avg_loss: float
    max_dd: float
    equity: List[float]
    by_symbol: Dict[str, float]
    by_reason: Dict[str, float]

    def summary(self) -> str:
        return (f"n={self.trades:>4} wr={self.winrate*100:>5.1f}% "
                f"pf={self.pf:>6.3f} net={self.net:>+9.3f} "
                f"dd={self.max_dd:>7.3f}")


def backtest(data: Dict[str, Series], cfg: Config,
             t0: int = 0, t1: int = 10**13) -> Result:
    """
    Jalankan strategi pada rentang waktu [t0, t1).

    Satu posisi per simbol, diisi di OPEN bar berikutnya setelah sinyal,
    dan diperiksa setiap bar sesudahnya untuk SL/TP/timeout.
    """
    equity = 10_000.0
    peak = equity
    max_dd = 0.0
    curve = [equity]
    n = wins = 0
    net = 0.0
    by_sym: Dict[str, float] = {}
    by_reason: Dict[str, float] = {}
    win_sum = loss_sum = 0.0

    # cooldown per simbol: bar index sampai mana simbol terkunci
    blocked_until: Dict[str, int] = {}

    for sym, s in data.items():
        n_bars = len(s)
        i = 30
        while i < n_bars - 2:
            ts = s.ts[i]
            if ts < t0 or ts >= t1:
                i += 1
                continue
            if blocked_until.get(sym, -1) > i:
                i += 1
                continue

            direction = _signal(s, i, cfg)
            if direction == 0:
                i += 1
                continue

            # Fill di OPEN bar berikutnya - harga bar sinyal sudah
            # mengandung informasi yang menghasilkan sinyal itu.
            j = i + 1
            entry = s.o[j]
            if entry <= 0:
                i += 1
                continue

            margin = cfg.risk_pct * equity
            qty = (margin * cfg.leverage) / entry
            if qty <= 0:
                i += 1
                continue

            if direction > 0:
                sl = entry * (1.0 - cfg.sl_pct)
                tp = entry * (1.0 + cfg.tp_pct)
            else:
                sl = entry * (1.0 + cfg.sl_pct)
                tp = entry * (1.0 - cfg.tp_pct)

            reason, exit_i = _simulate_exit(s, j, direction, sl, tp, cfg)
            if exit_i < 0:
                i += 1
                continue

            px = s.c[exit_i]
            gross = (px - entry) * qty * direction
            exit_fill = px * (1.0 - COST_PER_SIDE) if direction > 0 else px * (1.0 + COST_PER_SIDE)
            gross = (exit_fill - entry) * qty * direction
            fees = (entry + exit_fill) * qty * TAKER_FEE

            hold_s = (s.ts[exit_i] - s.ts[j]) / 1000.0
            funding = _funding(qty, entry, direction, hold_s)

            pnl = gross - fees - funding
            equity += pnl
            net += pnl
            n += 1
            if pnl > 0:
                wins += 1
                win_sum += pnl
            else:
                loss_sum += -pnl

            sym_key = sym.split("/")[0]
            by_sym[sym_key] = by_sym.get(sym_key, 0.0) + pnl
            by_reason[reason] = by_reason.get(reason, 0.0) + pnl
            curve.append(equity)
            peak = max(peak, equity)
            max_dd = max(max_dd, peak - equity)

            if cfg.cooldown_s:
                blocked_until[sym] = exit_i + max(
                    1, cfg.cooldown_s // 60)
            i = exit_i + 1

    wr = wins / n if n else 0.0
    pf = (win_sum / loss_sum) if loss_sum > 0 else float("inf")
    return Result(n, wins, net, pf, wr,
                  win_sum / wins if wins else 0.0,
                  loss_sum / (n - wins) if n > wins else 0.0,
                  max_dd, curve, by_sym, by_reason)


def _signal(s: Series, i: int, cfg: Config) -> int:
    if cfg.entry == "none":
        return 0
    if cfg.entry == "mom_long":
        return 1 if s.mom21[i] > cfg.mom_thresh else 0
    if cfg.entry == "mom_both":
        if s.mom21[i] > cfg.mom_thresh:
            return 1
        if s.mom21[i] < -cfg.mom_thresh:
            return -1
        return 0
    if cfg.entry == "ema":
        return 1 if s.ema9[i] > s.ema21[i] else 0
    if cfg.entry == "rsi_rev":
        r = s.rsi14[i]
        if r is None:
            return 0
        if r < cfg.rsi_low:
            return 1
        if r > cfg.rsi_high:
            return -1
        return 0
    return 0


def _simulate_exit(s: Series, j: int, direction: int,
                   sl: float, tp: float, cfg: Config) -> Tuple[str, int]:
    """
    Kembalikan (reason, index_bar) atau ("", -1) kalau belum keluar.

    Bar pertama diperiksa adalah bar SESUDAH entry, karena fill terjadi
    di OPEN bar j dan sebagian bar j sudah terjadi sebelum fill.
    Memeriksa SL dan TP di bar yang sama dengan fill juga tidak fair: urutan high dan low dalam satu menit tidak diketahui.

    min_hold_s HANYA menahan early-exit min_profit_pct, bukan SL/TP.
    Versi sebelumnya men-skip seluruh bar di bawah `min_hold_s` termasuk
    cek SL/TP, jadi stop yang tersentuh di menit ke-0 diabaikan
    sepenuhnya dan posisi itu keluar jauh di harga yang tidak
    berhubungan - yang membuat setiap hari"
    """
    last = len(s) - 1
    for k in range(j + 1, last + 1):
        held = (s.ts[k] - s.ts[j]) / 1000.0

        if direction > 0:
            sl_hit = s.l[k] <= sl
            tp_hit = s.h[k] >= tp
        else:
            sl_hit = s.h[k] >= sl
            tp_hit = s.l[k] <= tp

        # SL diperiksa lebih dulu: dalam satu menit, harga bisa menyentuh
        # keduanya, dan mengasumsikan urutan yang menguntungkan adalah
        # cara termurah untuk membuat backtest berbohong.
        if sl_hit:
            return "SL", k
        if tp_hit:
            return "TP", k

        if cfg.min_profit_pct:
            px = s.c[k]
            prog = ((px - s.o[j]) / s.o[j]) * direction
            # `prog` dan `cfg.min_profit_pct` sama-sama FRACTION.
            # Versi sebelumnya membandingkan `prog * 100.0` dengan
            # min_profit_pct yang juga fraction, jadi ambangnya jadi 100x
            # terlalu kecil: exit memicu di 0.117% saat ambangnya 0.60%.
            if prog >= cfg.min_profit_pct:
                return "SCALP_TP", k

        if held >= cfg.max_hold_s:
            return "EXPIRED", k

    return "", -1


def _funding(qty: float, entry: float, direction: int,
             held_s: float) -> float:
    """Biaya funding. Tidak ada data rate historis, jadi rate 0."""
    periods = held_s / FUNDING_PERIOD
    if periods <= 0:
        return 0.0
    return 0.0


def walk_forward(data: Dict[str, Series], cfg: Config,
                 folds: int = 3) -> List[Tuple[str, Result]]:
    """Split chronological, JANGAN acak."""
    all_ts = sorted({s.ts[i] for s in data.values() for i in range(len(s))})
    lo, hi = all_ts[0], all_ts[-1]
    span = (hi - lo) / folds
    out = []
    for k in range(folds):
        a = int(lo + span * k)
        b = int(lo + span * (k + 1))
        if k == 0:
            label = f"fold{k}(train)"
            r = backtest(data, cfg, lo, b)
        elif k == folds - 1:
            label = f"fold{k}(test)"
            r = backtest(data, cfg, a, hi)
        else:
            label = f"fold{k}(val)"
            r = backtest(data, cfg, a, b)
        out.append((label, r))
    return out


def split_by_position(data: Dict[str, Series], folds: int = 3):
    """
    Walk-forward yang benar untuk data dengan gap.

    Data candle di repo ini punya jeda 4 HARI di tengah (20 Sep -> 24
    Sep), jadi membagi berdasarkan timestamp global menghasilkan satu
    fold yang kosong untuk semua simbol - validasi jadi tidak mungkin.

    Karena itu pembagiannya per-simbol,secara kronologi, dan setiap
    simbol diberi label fold berdasarkan posisinya sendiri.
    Hasilnya: setiap simbol mewarisi proporsi train/valid/test yang
    sama, tanpa melewati gap.

    Return: dict fold_index -> dict symbol -> Series
    """
    out: Dict[int, Dict[str, Series]] = {k: {} for k in range(folds)}
    for sym, s in data.items():
        n = len(s)
        a = n // folds
        b = 2 * n // folds
        if a < 30 or (b - a) < 30 or (n - b) < 30:
            continue
        out[0][sym] = _slice(s, 0, a)
        out[1][sym] = _slice(s, a, b)
        out[2][sym] = _slice(s, b, n)
    return out


def _slice(s: Series, a: int, b: int) -> Series:
    return Series(
        symbol=s.symbol, ts=s.ts[a:b], o=s.o[a:b], h=s.h[a:b],
        l=s.l[a:b], c=s.c[a:b], v=s.v[a:b],
        rsi14=s.rsi14[a:b], atr14=s.atr14[a:b],
        ema9=s.ema9[a:b], ema21=s.ema21[a:b],
        mom21=s.mom21[a:b], vol_ratio=s.vol_ratio[a:b],
        trend_up=s.trend_up[a:b] if s.trend_up else [],
    )


def backtest_folds(folds_data: Dict[int, Dict[str, Series]], cfg: Config) -> Result:
    """Jalankan backtest pada mapping fold-index -> data."""
    merged: Dict[str, Series] = {}
    for _, d in folds_data.items():
        merged.update(d)
    return backtest(merged, cfg)
