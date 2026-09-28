"""
agents/direction_agents.py — Agen-agen spesialis untuk ensemble arah LONG/SHORT.

Empat agen spesialis menilai arah harga dari sumber data yang berbeda, lalu
`DirectionEnsembleAgent` menggabungkan verdict mereka menjadi satu
probabilitas LONG/SHORT.

Pola mengikuti `agents/base_agent.py`: sense() -> think() -> act(). Semua
agen adalah `BaseAgent`, tapi tidak satupun dijadwalkan sendiri — dipanggil
in-process oleh ensemble agent. Alasannya: semua agen membaca `market_store`
yang sama, jadi menjadwalkan masing-masing hanya menambah pembacaan identik,
sementara menulis ke koneksi `aiosqlite` yang sama dari beberapa task
sekaligus berisiko balapan.

Setiap agen mengembalikan verdict seragam lewat
`analysis.direction_ensemble.make_verdict` / `abstain`.
"""

import asyncio
import json
import math
import sqlite3
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from agents.base_agent import BaseAgent
from analysis.direction_ensemble import (
    DIRECTION_LONG,
    DIRECTION_NEUTRAL,
    DIRECTION_SHORT,
    aggregate,
    abstain,
    make_verdict,
)
from analysis.technical import TechnicalAnalyzer
from core.config import get_config
from core.market_store import market_store


class DirectionAgent(BaseAgent):
    """
    Basis untuk agen spesialis arah.

    Subclass mengimplementasikan `_evaluate(symbol)` yang mengembalikan
    `(direction, confidence, reasoning, factors)` atau `None` bila data
    tidak cukup. `evaluate()` membungkusnya menjadi verdict seragam.
    """

    agent_name = "direction"

    def __init__(self, event_bus):
        super().__init__(self.agent_name, event_bus)
        self.config = get_config()
        self.ensemble = self.config.ensemble

    async def initialize(self):
        """Agen spesialis tidak subscribe channel apa pun."""
        self.logger.info(f"{self.agent_name} siap")

    def evaluate(self, symbol: str) -> dict:
        """
        Hasilkan verdict untuk satu simbol. Selalu mengembalikan dict;
        `abstained=True` bila sumber data tidak tersedia atau terlalu sedikit.
        """
        try:
            outcome = self._evaluate(symbol)
        except Exception as exc:  # noqa: BLE001
            # Satu agen gagal tidak boleh menjatuhkan seluruh ensemble.
            self.logger.debug(f"{self.agent_name} gagal untuk {symbol}: {exc}")
            return abstain(self.agent_name, symbol, f"error: {exc}")

        if outcome is None:
            return abstain(self.agent_name, symbol, "data tidak cukup")

        direction, confidence, reasoning, factors = outcome
        return make_verdict(
            agent=self.agent_name,
            symbol=symbol,
            direction=direction,
            confidence=confidence,
            reasoning=reasoning,
            factors=factors,
        )

    def _evaluate(self, symbol: str):
        """Harus diimplementasikan subclass."""
        raise NotImplementedError

    # BaseAgent mensyaratkan tiga fase; agen spesialis tidak punya lifecycle
    # sendiri karena dipanggil langsung oleh ensemble agent.
    async def sense(self) -> dict:
        return {}

    async def think(self, data: dict) -> dict:
        return {}

    async def act(self, analysis: dict):
        return None


class OrderFlowAgent(DirectionAgent):
    """
    Arah dari tekanan order book L2.

    Memakai `calculate_order_flow_imbalance` sebagai fasad di atas
    `core.microstructure` — logikanya tidak ditulis ulang di sini, dan
    simpul ini yang menentukan implementasinya (Python atau native C++).
    """

    agent_name = "orderflow"

    def _evaluate(self, symbol: str):
        from analysis.probability_engine import calculate_order_flow_imbalance

        book = market_store.get_order_book(symbol)
        if not book:
            return None

        # Simbol diteruskan supaya orderbook ikut masuk state kernel. Dengan
        # begitu snapshot yang sama tidak perlu di-parse ulang oleh pemanggil
        # lain di siklus yang sama, dan implementasi native mendapat
        # kesempatan meng-cache struktur yang sudah diparse.
        ofi, rel_spread = calculate_order_flow_imbalance(book, symbol=symbol)
        if ofi == 0.0 and rel_spread == 0.0:
            return None

        if abs(ofi) < 1e-6:
            return (
                DIRECTION_NEUTRAL, 0.0,
                f"order book seimbang (spread {rel_spread:.4%})",
                {"ofi": ofi, "spread_pct": rel_spread},
            )

        # OFI +1 = tekanan bid (naik), -1 = tekanan ask (turun).
        confidence = abs(float(ofi))
        direction = DIRECTION_LONG if ofi > 0 else DIRECTION_SHORT
        side_label = "bid" if ofi > 0 else "ask"
        return (
            direction, confidence,
            f"orderbook {side_label} dominan {abs(ofi):.0%} "
            f"· spread {rel_spread:.4%}",
            {"ofi": round(ofi, 4), "spread_pct": round(rel_spread, 5)},
        )


class MomentumAgent(DirectionAgent):
    """
    Arah dari perubahan harga jangka pendek.

    `market_store.get_price_change` mengembalikan fraksi relatif
    (last - first) / first untuk jendela waktu tertentu.
    """

    agent_name = "momentum"

    def _evaluate(self, symbol: str):
        window = float(self.ensemble.momentum_window_seconds)
        change = market_store.get_price_change(symbol, seconds=window)
        if change is None:
            return None

        change = float(change)
        if abs(change) < 1e-9:
            return (
                DIRECTION_NEUTRAL, 0.0,
                f"harga flat {window:.0f}s",
                {"change": 0.0, "window_s": window},
            )

        # Confidence linier terhadap magnitude, dinormalisasi terhadap ambang
        # momentum terkonfigurasi. Perubahan 2x ambang = confidence 1.0.
        threshold = max(float(self.config.scalping.momentum_threshold), 1e-6)
        confidence = min(abs(change) / (threshold * 2.0), 1.0)
        direction = DIRECTION_LONG if change > 0 else DIRECTION_SHORT
        return (
            direction, confidence,
            f"momentum {change:+.2%} dalam {window:.0f}s",
            {"change": round(change, 6), "window_s": window},
        )


class TechnicalAgent(DirectionAgent):
    """
    Arah dari indikator teknikal pada lilin 1m.

    Memakai `calculate_technical_zscore` yang sudah ada, dengan data lilin
    dibaca lewat koneksi SQLite sinkron terpisah supaya tidak memakai
    koneksi aiosqlite yang sedang dipakai agen lain.
    """

    agent_name = "technical"

    def __init__(self, event_bus, db_path: str):
        super().__init__(event_bus)
        self.technical = TechnicalAnalyzer()
        self.db_path = db_path

    async def evaluate_async(self, symbol: str) -> dict:
        """
        Versi async dari `evaluate`.

        Kalkulasi indikator pandas/NumPy adalah CPU-bound. Dijalankan di
        thread executor supaya tidak memblokir event loop — kalau tidak, satu
        simbol dengan 100 lilin bisa menahan price update, eksekusi order,
        dan seluruh job scheduler sekaligus.
        """
        try:
            return await asyncio.to_thread(self.evaluate, symbol)
        except Exception as exc:  # noqa: BLE001
            self.logger.debug(f"technical gagal untuk {symbol}: {exc}")
            return abstain(self.agent_name, symbol, f"error: {exc}")

    def _evaluate(self, symbol: str):
        from analysis.probability_engine import calculate_technical_zscore

        closes = _load_closes(self.db_path, symbol, limit=100)
        if len(closes) < 30:
            return None

        df = pd.DataFrame(closes)
        df = self.technical.calculate_indicators(df)
        if df.empty or "close" not in df.columns:
            return None

        last = df.iloc[-1]
        price = float(last["close"])
        indicators = {}
        for key in ("rsi", "rsi_fast", "macd_hist", "atr",
                    "ema_short", "ema_long", "ema_3", "ema_5",
                    "bb_lower", "bb_upper"):
            indicators[key] = _safe_float(last.get(key))

        z = float(calculate_technical_zscore(indicators, price))
        if abs(z) < 1e-6:
            return (
                DIRECTION_NEUTRAL, 0.0,
                "indikator teknikal netral",
                {"z": round(z, 4)},
            )

        # z dari calculate_technical_zscore berada di skala sekitar ±2;
        # z=2 dipandang keyakin penuh.
        confidence = min(abs(z) / 2.0, 1.0)
        direction = DIRECTION_LONG if z > 0 else DIRECTION_SHORT
        rsi = indicators.get("rsi")
        rsi_txt = f"{rsi:.0f}" if rsi is not None else "n/a"
        return (
            direction, confidence,
            f"teknikal {direction.lower()} (z={z:+.2f}, RSI {rsi_txt})",
            {"z": round(z, 4)},
        )


class MicrostructureAgent(DirectionAgent):
    """
    Arah dari data mikrostruktur: trade tape, funding, dan open interest.

    Kemampuan baru — sebelumnya scanner tidak memakai data ini sama sekali
    meski sudah tersedia live dari feed Hyperliquid.
    """

    agent_name = "microstructure"

    def _evaluate(self, symbol: str):
        z_components: List[float] = []
        details: Dict = {}
        reasons: List[str] = []

        # 1. Signed volume imbalance dari trade tape.
        tape = market_store.get_recent_trades(symbol)
        min_tape = int(self.ensemble.min_trades_for_microstructure)
        if len(tape) >= min_tape:
            signed_vol, total_vol, buys, sells = _signed_volume(tape)
            if total_vol > 0:
                imbalance = signed_vol / total_vol  # -1..1
                z_components.append(imbalance * 1.5)
                details["tape_imbalance"] = round(imbalance, 4)
                details["tape_trades"] = len(tape)
                details["tape_buys"] = buys
                details["tape_sells"] = sells
                reasons.append(f"tape {imbalance:+.0%} ({buys}B/{sells}S)")

        # 2. Funding rate — kontra-sentimen. Long ramai membayar funding
        #    positif, jadi funding tinggi berarti posisi long sudah padat dan
        #    rawan balik. Skala 0.01% per jam = z 1.
        funding = market_store.get_funding(symbol)
        if funding is not None and abs(funding) > 0:
            z_funding = float(np.clip(-funding / 0.0001, -2.0, 2.0))
            if abs(z_funding) > 1e-6:
                z_components.append(z_funding)
                details["funding"] = round(float(funding), 6)
                reasons.append(f"funding {funding:+.4%}")

        # 3. Open interest dicatat sebagai konteks, bukan vote. OI hanya
        #    bermakna kalau ada arah; tanpa arah, OI tinggi hanya berarti
        #    "banyak yangamada Credentials" — bukan sinyal long atau short.
        oi = market_store.get_open_interest(symbol)
        if oi is not None:
            details["open_interest"] = round(float(oi), 2)

        if not z_components:
            return None

        z = float(np.mean(z_components))
        if abs(z) < 1e-6:
            return (DIRECTION_NEUTRAL, 0.0, "mikrostruktur netral", details)

        confidence = min(abs(z) / 2.0, 1.0)
        direction = DIRECTION_LONG if z > 0 else DIRECTION_SHORT
        detail_txt = " · ".join(reasons) if reasons else "tanpa detail"
        return (
            direction, confidence,
            f"mikrostruktur {direction.lower()} (z={z:+.2f}) · {detail_txt}",
            details,
        )


def _safe_float(value) -> Optional[float]:
    """Konversi ke float, kembalikan None untuk NaN/None/value tak valid."""
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(out) else out


def _load_closes(db_path: str, symbol: str, limit: int = 100) -> List[dict]:
    """
    Baca lilin 1m terbaru lewat koneksi SQLite sinkron terpisah.

    Koneksi sengaja terpisah dari `aiosqlite` global: agen ini berjalan di
    thread executor, dan memakai koneksi bersama dari thread lain akan
    menyatronkan `sqlite3.ProgrammingError`.
    """
    base = symbol.split("/")[0]
    try:
        conn = sqlite3.connect(db_path)
        try:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                "SELECT timestamp, open, high, low, close, volume FROM candles "
                "WHERE (symbol = ? OR symbol LIKE ?) AND timeframe = '1m' "
                "ORDER BY timestamp DESC LIMIT ?",
                (symbol, f"%{base}%", limit),
            )
            rows = [dict(r) for r in reversed(cur.fetchall())]
        finally:
            conn.close()
    except sqlite3.Error:
        return []

    if not rows:
        return []

    for r in rows:
        for k in ("open", "high", "low", "close", "volume"):
            v = _safe_float(r.get(k))
            if v is None:
                r[k] = 0.0
    return rows


def _signed_volume(tape: list):
    """
    Hitung volume bertanda dari daftar trade Hyperliquid.

    Payload trade punya `side` ('B'/'A') dan `sz`.

    Trade dengan `side` tidak dikenal DILEWATI sepenuhnya — tidak masuk
    pembilang maupun penyebut. Menghitungnya di penyebut tapi tidak di
    pembilang akan membuat imbalance paralyzed mendekati nol, yaitu
    artefak sinyal: agen terlihat punya data tapi selalu bilang netral.
    """
    signed = 0.0
    total = 0.0
    buys = 0
    sells = 0
    for t in tape:
        if not isinstance(t, dict):
            continue
        side = str(t.get("side", "")).upper()
        if not (side.startswith("B") or side.startswith("A")):
            continue
        size = _safe_float(t.get("sz")) or 0.0
        if size <= 0:
            continue
        total += size
        if side.startswith("B"):
            signed += size
            buys += 1
        else:
            signed -= size
            sells += 1
    return signed, total, buys, sells


class DirectionEnsembleAgent(BaseAgent):
    """
    Koordinator ensemble: memanggil tiap spesialis, menggabungkan verdictnya,
    lalu menulis satu snapshot per simbol.

    Ini SATU-SATUNYA penulis tabel `direction_snapshots`, jadi pembaca tidak
    pernah melihat snapshot setengah tertulis.
    """

    def __init__(self, event_bus, symbols: List[str], db_path: str):
        super().__init__("direction_ensemble", event_bus)
        self.config = get_config()
        self.ensemble = self.config.ensemble
        self.symbols = list(symbols)
        self.db_path = db_path
        self.specialists: List[DirectionAgent] = []

    async def initialize(self):
        """Bangun agen spesialis dari config (hanya yang enabled)."""
        self.specialists = []
        for name in ("orderflow", "momentum", "technical", "microstructure"):
            if not self.ensemble.base_weight(name):
                continue
            if name == "orderflow":
                self.specialists.append(OrderFlowAgent(self.event_bus))
            elif name == "momentum":
                self.specialists.append(MomentumAgent(self.event_bus))
            elif name == "technical":
                self.specialists.append(
                    TechnicalAgent(self.event_bus, self.db_path)
                )
            elif name == "microstructure":
                self.specialists.append(MicrostructureAgent(self.event_bus))
        names = [s.agent_name for s in self.specialists]
        self.logger.info(f"Ensemble arah siap — spesialis aktif: {names}")

    async def run_cycle(self):
        """Satu siklus ensemble untuk semua simbol aktif."""
        try:
            repo = await self._get_repo()
            written = 0
            for symbol in self.symbols:
                snapshot = await self._evaluate_symbol(symbol)
                if snapshot is not None:
                    await repo.insert_direction_snapshot(snapshot)
                    written += 1
            if written:
                self.logger.debug(f"Ensemble menulis {written} snapshot arah")
        except Exception as exc:  # noqa: BLE001
            self.logger.error(f"Error siklus ensemble: {exc}")

    # `run_cycle` di atas menggantikan siklus sense/think/act bawaan, jadi
    # ketiga fase itu tidak berlaku untuk agen ini. Implementasi kosong
    # hanya formality agar kelas tetap konkret.
    async def sense(self) -> dict:
        return {}

    async def think(self, data: dict) -> dict:
        return {}

    async def act(self, analysis: dict):
        return None

    async def _evaluate_symbol(self, symbol: str) -> Optional[dict]:
        """
        Kumpulkan verdict semua spesialis untuk satu simbol lalu agregasi.

        TechnicalAgent CPU-bound jadi dikirim ke thread executor; spesialis
        lain hanya membaca `market_store` in-memory dan sangat murah.
        """
        verdicts: List[dict] = []
        for spec in self.specialists:
            if isinstance(spec, TechnicalAgent):
                verdicts.append(await spec.evaluate_async(symbol))
            else:
                verdicts.append(spec.evaluate(symbol))

        result = aggregate(verdicts, self.ensemble)

        diffusion = None
        vol = await self._realized_vol(symbol)
        if vol is not None:
            from analysis.probability_engine import probability_engine

            diffusion = probability_engine.compute_directional_curve(
                prob_long=result["prob_long"],
                realized_vol_per_min=vol,
                horizon_minutes=int(self.ensemble.diffusion_horizon_minutes),
                num_points=int(self.ensemble.diffusion_points),
            )

        return {
            "symbol": symbol,
            "prob_long": result["prob_long"],
            "prob_short": result["prob_short"],
            "direction": result["direction"],
            "confidence": result["confidence"],
            "z_composite": result.get("z_composite", 0.0),
            "agent_breakdown": json.dumps(
                result.get("agent_breakdown", []), default=str
            ),
            "diffusion": json.dumps(diffusion, default=str) if diffusion else None,
        }

    async def _realized_vol(self, symbol: str) -> Optional[float]:
        """
        Sigma per menit dari lilin 1m, atau None bila data tidak cukup.

        Ambang `min_returns_for_vol` penting: sigma dari 3 return sangat
        tidak stabil, dan sigma ini masuk ke eksponen difusi sehingga error
        diretamente merusak bentuk kurva.
        """
        min_returns = int(self.ensemble.min_returns_for_vol)
        closes = await asyncio.to_thread(_load_closes, self.db_path, symbol, 40)
        if len(closes) < min_returns + 1:
            return None

        series = [float(r["close"]) for r in closes if float(r["close"]) > 0]
        if len(series) < min_returns + 1:
            return None

        log_ret = np.diff(np.log(np.array(series, dtype=float)))
        if len(log_ret) < min_returns:
            return None
        return max(float(np.std(log_ret, ddof=1)), 0.0002)
