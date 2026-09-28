"""
analysis/probability_engine.py — Quantitative Probability Calculation Engine.

Theoretical Foundations:
1. Bayesian Multi-Factor Logit Formulation (Thomas Bayes & Grinold-Kahn Fundamental Law):
   Integrates heterogeneous predictive alpha signals (Technical indicators, FinBERT/VADER sentiment,
   RandomForest ML class probabilities, L2 Order Flow Imbalance, and Macro FRED signals)
   into standardized log-odds Z-scores:
     Z_composite = sum(w_i * Z_i)
     P(Bullish) = 1 / (1 + exp(-Z_composite))
     P(Bearish) = 1 - P(Bullish)

2. Microstructure Order Flow Imbalance (Cartea, Jaimungal & Penalva, 2015):
   Calculates directional queue pressure from real L2 orderbook bids and asks:
     OFI = (sum(BidSize) - sum(AskSize)) / (sum(BidSize) + sum(AskSize))

3. Term-Structure Drift-Diffusion Cone (Black-Scholes & Cox-Ross-Rubinstein):
   Computes terminal profit probability curve across horizons T in [1m, 60m] under Geometric Brownian Motion:
     P(S_t > S_0) = Phi( ((mu - 0.5 * sigma^2) * t) / (sigma * sqrt(t)) )
   eliminating all synthetic sine waves and placeholder functions.

4. Mathematical Expectancy & Trading Edge (Ed Thorp & Ralph Vince):
   Calculates mathematical edge:
     Edge = (P_win * AvgWin) - (P_loss * AvgLoss)
"""

import math
import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Tuple

from core import microstructure


def norm_cdf(x: float) -> float:
    """Standard normal cumulative distribution function Phi(x) via erf."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def calculate_order_flow_imbalance(
    order_book: Optional[Dict],
    symbol: str = None,
) -> Tuple[float, float]:
    """
    Hitung Order Flow Imbalance (OFI) dan Spread dari data L2 Orderbook.
    Teori: Cartea & Jaimungal (2015) Market Microstructure.

    Fungsi ini adalah *fasad* di atas `core.microstructure`. Seluruh
    perhitungan antara volume dan spread terjadi di kernel, bukan di sini —
    itulah yang membuat OFI bisa dipindah ke C++/Rust tanpa menyentuh satu
    baris pun di modul ini maupun di agent mana pun.

    Args:
        order_book: dict berisi `bids` dan `asks` (snapshot penuh L2), atau
            None bila tidak ada data.
        symbol: untuk state kernel. Kalau diisi, orderbook ini juga di-cache
            ke kernel sehingga panggilan berikutnya yang hanya butuh OFI tidak
            perlu membawa orderbooknya lagi.

    Returns:
        (ofi, relative_spread):
        ofi: [-1.0 (tekanan ask berat) sampai +1.0 (tekanan bid berat)]
        relative_spread: spread relatif terhadap mid-price

    **Doktrin data absen:** tanpa orderbook yang valid, hasilnya (0.0, 0.0) —
    bukan spread tebakan. Nilai 0.0 di sini berarti "tidak ada informasi",
    dan pemanggil wajib memperlakukannya begitu: `OrderFlowAgent` memakai
    `abstain` untuk kasus itu, bukan melaporkan NEUTRAL.
    """
    # Tanpa orderbook L2 tidak ada informasi mikro-struktur: kembalikan nol,
    # bukan spread fabrikasi, agar pemanggil tahu data memang absen.
    if not order_book or "bids" not in order_book or "asks" not in order_book:
        return 0.0, 0.0

    bids = order_book.get("bids", [])
    asks = order_book.get("asks", [])

    if not bids or not asks:
        return 0.0, 0.0

    # Cache-kan ke kernel bila simbol diketahui, lalu minta jawabannya dari
    # kernel. Tanpa simbol, dict ini tidak bisa di-cache, jadi kernel dipakai
    # sebagai fungsi murni pada data yang diberikan.
    if symbol is None:
        kernel = microstructure.PythonKernel()
        kernel.ingest_l2("__ad_hoc__", bids, asks)
        return kernel.order_flow_imbalance("__ad_hoc__", depth=5)

    microstructure.ingest_l2(symbol, bids, asks)
    return microstructure.order_flow_imbalance(symbol, depth=5)


def calculate_technical_zscore(indicators: Dict, current_price: float) -> float:
    """
    Hitung skor Z teknikal gabungan dari RSI, MACD Histogram, EMA, dan Bollinger Bands.
    Nilai Z positif = dorongan bullish, negatif = dorongan bearish.
    """
    if not indicators:
        return 0.0

    sub_z = []

    # 1. RSI (50 neutral, 70+ overbought/bullish momentum, 30- oversold)
    rsi = indicators.get("rsi")
    if rsi is not None and not math.isnan(rsi):
        # Center at 50, scale so 70 is +1.0, 30 is -1.0
        z_rsi = (float(rsi) - 50.0) / 20.0
        sub_z.append(np.clip(z_rsi, -2.0, 2.0))

    # 2. MACD Histogram normalized by ATR
    macd_hist = indicators.get("macd_hist")
    atr = indicators.get("atr")
    if macd_hist is not None and not math.isnan(macd_hist):
        denom = float(atr) if (atr and not math.isnan(atr) and atr > 0) else max(current_price * 0.002, 1e-4)
        z_macd = (float(macd_hist) / denom) * 1.5
        sub_z.append(np.clip(z_macd, -2.0, 2.0))

    # 3. EMA Short vs EMA Long
    ema_s = indicators.get("ema_short")
    ema_l = indicators.get("ema_long")
    if ema_s and ema_l and not math.isnan(ema_s) and not math.isnan(ema_l) and ema_l > 0:
        diff_pct = (float(ema_s) - float(ema_l)) / float(ema_l)
        z_ema = diff_pct * 100.0  # e.g. +0.5% diff -> +0.50
        sub_z.append(np.clip(z_ema, -2.0, 2.0))

    # 4. Bollinger Band position
    bb_lower = indicators.get("bb_lower")
    bb_upper = indicators.get("bb_upper")
    if bb_lower and bb_upper and not math.isnan(bb_lower) and not math.isnan(bb_upper):
        band_w = float(bb_upper) - float(bb_lower)
        if band_w > 0:
            pct_b = (current_price - float(bb_lower)) / band_w
            z_bb = (pct_b - 0.5) * 2.5
            sub_z.append(np.clip(z_bb, -2.0, 2.0))

    return float(np.mean(sub_z)) if sub_z else 0.0


def calculate_realized_volatility(df: pd.DataFrame, window: int = 30) -> float:
    """
    Hitung per-minute realized volatility dari close-to-close log returns.
    """
    if df.empty or len(df) < 5 or "close" not in df.columns:
        return 0.0015  # 0.15% per-minute default volatility

    closes = df["close"].tail(window).astype(float)
    log_ret = np.log(closes / closes.shift(1)).dropna()
    if len(log_ret) < 3:
        return 0.0015

    vol = float(log_ret.std(ddof=1))
    return max(vol, 0.0002)


class QuantitativeProbabilityEngine:
    """
    Engine perhitungan probabilitas murni kuantitatif berbasis Teori Bayesian Logit
    dan Difusi Black-Scholes/Cox-Ross-Rubinstein.
    """

    def __init__(self):
        # Bobot Grinold-Kahn Information Coefficient (IC)
        self.weights = {
            "technical": 0.30,
            "sentiment": 0.20,
            "ml": 0.25,
            "orderbook": 0.15,
            "macro": 0.10,
        }

    def compute_composite_probability(
        self,
        indicators: Dict,
        current_price: float,
        sentiment_score: float = 0.0,
        ml_prediction: Optional[Dict] = None,
        order_book: Optional[Dict] = None,
        macro_bias: str = "NEUTRAL",
    ) -> Dict:
        """
        Hitung probabilitas terpadu P(Bullish) dan P(Bearish) dengan Bayesian Multi-Factor Logit.

        Returns:
            {
                "prob_bullish": float,      # 0.0 to 1.0
                "prob_bearish": float,      # 0.0 to 1.0
                "winner_odds": float,       # max(prob_bullish, prob_bearish)
                "loser_odds": float,        # min(prob_bullish, prob_bearish)
                "direction": "BULLISH" | "BEARISH",
                "z_composite": float,
                "factor_zscores": dict,
                "order_flow_imbalance": float,
                "relative_spread": float,
            }
        """
        # 1. Z-Score Teknikal
        z_tech = calculate_technical_zscore(indicators, current_price)

        # 2. Z-Score Sentimen (FinBERT / VADER [-1, 1] scaled to log-odds)
        z_sent = np.clip(sentiment_score * 2.2, -2.5, 2.5)

        # 3. Z-Score Machine Learning (Random Forest)
        z_ml = 0.0
        if ml_prediction:
            action = ml_prediction.get("action", "HOLD")
            conf = ml_prediction.get("confidence", 0.5)
            probs = ml_prediction.get("probabilities", {})
            if probs and "LONG" in probs and "SHORT" in probs:
                p_long = max(float(probs["LONG"]), 1e-4)
                p_short = max(float(probs["SHORT"]), 1e-4)
                z_ml = math.log(p_long / p_short)
            else:
                # Tabel signals menyimpan arah sebagai BULLISH/BEARISH; model ML bisa
                # memakai BUY/SELL atau LONG/SHORT. Terima semua varian.
                action_upper = str(action).upper()
                if any(tok in action_upper for tok in ("BULL", "BUY", "LONG")):
                    z_ml = conf * 2.0
                elif any(tok in action_upper for tok in ("BEAR", "SELL", "SHORT")):
                    z_ml = -conf * 2.0
        z_ml = float(np.clip(z_ml, -2.5, 2.5))

        # 4. Z-Score Order Flow Imbalance (L2 Orderbook Depth)
        ofi, rel_spread = calculate_order_flow_imbalance(order_book)
        z_ob = float(np.clip(ofi * 2.5, -2.5, 2.5))

        # 5. Z-Score Makroekonomi FRED
        macro_bias_upper = str(macro_bias).upper()
        if "BULLISH" in macro_bias_upper:
            z_fund = 0.8
        elif "BEARISH" in macro_bias_upper:
            z_fund = -0.8
        else:
            z_fund = 0.0

        # Gabungan Multi-Factor Bayesian Logit
        z_comp = (
            self.weights["technical"] * z_tech
            + self.weights["sentiment"] * z_sent
            + self.weights["ml"] * z_ml
            + self.weights["orderbook"] * z_ob
            + self.weights["macro"] * z_fund
        )

        # Logit Sigmoid: P(Bullish) = 1 / (1 + exp(-z_comp))
        prob_bullish = 1.0 / (1.0 + math.exp(-z_comp))
        prob_bearish = 1.0 - prob_bullish

        winner_odds = max(prob_bullish, prob_bearish)
        loser_odds = min(prob_bullish, prob_bearish)
        direction = "BULLISH" if prob_bullish >= 0.5 else "BEARISH"

        return {
            "prob_bullish": round(prob_bullish, 4),
            "prob_bearish": round(prob_bearish, 4),
            "winner_odds": round(winner_odds, 4),
            "loser_odds": round(loser_odds, 4),
            "direction": direction,
            "z_composite": round(z_comp, 4),
            "factor_zscores": {
                "technical": round(z_tech, 3),
                "sentiment": round(z_sent, 3),
                "ml": round(z_ml, 3),
                "orderbook": round(z_ob, 3),
                "macro": round(z_fund, 3),
            },
            "order_flow_imbalance": round(ofi, 3),
            "relative_spread": round(rel_spread, 5),
        }

    def compute_directional_curve(
        self,
        prob_long: float,
        realized_vol_per_min: float,
        horizon_minutes: int = 30,
        num_points: int = 60,
    ) -> Dict:
        """
        Hitung kurva difusi arah LONG/SHORT sepanjang horizon waktu.

        Berbeda dari `compute_diffusion_curve` (lama), fungsi ini TIDAK memakai
        nilai mutlak drift dan TIDAK meng-clip ke [0.50, 1.0]. Drift dipakai
        apa adanya, sehingga pembacaan bearish menghasilkan kurva yang
        menurun — dan probabilitas LONG bisa jatuh di bawah 0.5.

        Return:
            {
                "time_horizons": list of float (menit),
                "long_probs": list of float,
                "short_probs": list of float,
            }
        """
        sigma = max(float(realized_vol_per_min), 0.0003)

        p_clamped = float(np.clip(prob_long, 0.001, 0.999))
        # Log-odds dari probabilitas arah = "drift per satuan volatilitas".
        z_score = math.log(p_clamped / (1.0 - p_clamped))

        # Drift TIDAK abs: tanda z menentukan arah kurva.
        drift_per_min = z_score * 0.45 * sigma

        t_steps = np.linspace(0.2, float(horizon_minutes), num_points)
        long_probs: List[float] = []
        short_probs: List[float] = []

        for t in t_steps:
            sqrt_t = math.sqrt(t)
            d2 = ((drift_per_min - 0.5 * (sigma ** 2)) * t) / (sigma * sqrt_t)
            p_terminal = norm_cdf(d2)

            # Konvergensi halus dari probabilitas saat ini ke terminal.
            weight_t = 1.0 - math.exp(-t * 0.08)
            p_long = (1.0 - weight_t) * p_clamped + weight_t * p_terminal

            # Clamp hanya ke rentang valid, bukan ke >= 0.5 — inilah yang
            # membuat kasus bearish benar-benar terlihat sebagai SHORT.
            p_long = float(np.clip(p_long, 0.001, 0.999))
            long_probs.append(round(p_long, 4))
            short_probs.append(round(1.0 - p_long, 4))

        return {
            "time_horizons": [round(float(t), 2) for t in t_steps],
            "long_probs": long_probs,
            "short_probs": short_probs,
        }

    def compute_diffusion_curve(
        self,
        prob_bullish: float,
        realized_vol_per_min: float,
        horizon_minutes: int = 60,
        num_points: int = 80,
    ) -> Dict:
        """
        DEPRECATED — gunakan `compute_directional_curve`.

        Versi lama memakai `abs(drift)` dan meng-clip ke [0.50, 0.999], sehingga
        probabilitas bearish (0.35) menghasilkan kurva yang identik dengan
        kasus bullish (0.65). Kurva itu tidak pernah bisa menampilkan SHORT.

        Shim ini dipertahankan sementara agar call site lama tidak langsung
        rusak; logikanya sengaja dibiarkan apa adanya.
        """
        sigma = max(realized_vol_per_min, 0.0003)

        # Ekstrak drift per menit dari probabilitas Bayesian
        # Jika prob_bullish = 0.5 -> drift = 0
        # Jika prob_bullish = 0.85 -> drift kuat positif
        # Menggunakan invers logit untuk drift proporsional
        p_clamped = np.clip(prob_bullish, 0.001, 0.999)
        z_score = math.log(p_clamped / (1.0 - p_clamped))

        # Drift proporsional terhadap volatilitas aset
        drift_per_min = z_score * 0.45 * sigma

        t_steps = np.linspace(0.2, float(horizon_minutes), num_points)
        winner_probs = []
        loser_probs = []

        # Magnitudo drift dipakai karena kurva selalu memplot sisi pemenang (p_win >= 0.5)
        effective_mu = abs(drift_per_min)

        for t in t_steps:
            # Formula Black-Scholes drift-diffusion:
            # d2 = (mu - 0.5 * sigma^2) * t / (sigma * sqrt(t))
            sqrt_t = math.sqrt(t)
            d2 = ((effective_mu - 0.5 * (sigma ** 2)) * t) / (sigma * sqrt_t)

            # Baseline probability pada t=0 adalah winner_odds awal
            p_terminal = norm_cdf(d2)

            # Konvergensi halus dari prior odds ke terminal odds
            weight_t = 1.0 - math.exp(-t * 0.08)
            p_win = (1.0 - weight_t) * max(prob_bullish, 1.0 - prob_bullish) + weight_t * p_terminal
            p_win = float(np.clip(p_win, 0.50, 0.999))
            p_lose = 1.0 - p_win

            winner_probs.append(round(p_win, 4))
            loser_probs.append(round(p_lose, 4))

        return {
            "time_horizons": [round(float(t), 2) for t in t_steps],
            "winner_probs": winner_probs,
            "loser_probs": loser_probs,
        }

    def compute_mathematical_edge(
        self,
        trades: List[Dict],
        current_winner_odds: float = 0.55,
        target_rr_ratio: float = 1.5,
    ) -> float:
        """
        Hitung Ekspektasi Matematis (Mathematical Trading Edge):
          Edge = (WinRate * AvgWin) - (LossRate * AvgLoss)

        Bila belum ada trade tertutup di database:
        Gunakan ekspektasi matematis teoritis berdasarkan odds saat ini dan target R:R ratio:
          Edge_theor = (P_win * R) - (1 - P_win)
        """
        closed_trades = [t for t in trades if t.get("pnl") is not None]

        if len(closed_trades) >= 2:
            wins = [float(t["pnl"]) for t in closed_trades if float(t["pnl"]) > 0]
            losses = [abs(float(t["pnl"])) for t in closed_trades if float(t["pnl"]) < 0]

            n_total = len(closed_trades)
            win_rate = len(wins) / n_total
            loss_rate = len(losses) / n_total

            avg_win = (sum(wins) / len(wins)) if wins else 0.0
            avg_loss = (sum(losses) / len(losses)) if losses else 0.0

            # Nilai nominal trade rata-rata
            avg_notional = np.mean([
                float(t.get("price", 100)) * float(t.get("quantity", 1))
                for t in closed_trades
            ]) if closed_trades else 100.0
            avg_notional = max(avg_notional, 1.0)

            edge_nominal = (win_rate * avg_win) - (loss_rate * avg_loss)
            edge_pct = (edge_nominal / avg_notional) * 100.0
            return float(np.clip(edge_pct, -10.0, 30.0))

        # Teori ekspektasi Thorp/Vince bila trade masih 0 atau 1
        p_win = np.clip(current_winner_odds, 0.50, 0.95)
        edge_theoretical = (p_win * target_rr_ratio) - (1.0 - p_win)
        # Normalisasi ke persentase edge per trade
        edge_pct = edge_theoretical * 1.25
        return float(np.clip(edge_pct, 0.10, 5.0))


# Singleton instance
probability_engine = QuantitativeProbabilityEngine()
