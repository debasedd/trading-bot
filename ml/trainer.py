"""
ml/trainer.py — Latih model RandomForest dari data historis.

Jalankan terpisah:
    python -m ml.trainer --symbol BTCUSDT --timeframe 1h --days 90
"""

import asyncio
import numpy as np
import pandas as pd
from pathlib import Path
from typing import Optional

from core.logger import get_logger

logger = get_logger("ml_trainer")

MODEL_DIR = Path("ml/models")


class ModelTrainer:
    """
    Melatih RandomForest classifier untuk prediksi sinyal trading.

    Label:
    - LONG: harga naik > threshold dalam N candle ke depan
    - SHORT: harga turun > threshold
    - HOLD: pergerakan sideways

    Fitur: RSI, MACD hist, BB position, EMA trend, volume ratio, ATR %
    """

    def __init__(self, lookahead: int = 10, threshold_pct: float = 0.005):
        """
        Args:
            lookahead: jumlah candle ke depan untuk menentukan label
            threshold_pct: persentase pergerakan minimum untuk LONG/SHORT (0.5%)
        """
        self.lookahead = lookahead
        self.threshold_pct = threshold_pct

    def prepare_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """Hitung fitur dari DataFrame OHLCV."""
        import pandas_ta as ta

        # Indikator
        df["rsi"] = ta.rsi(df["close"], length=14) / 100.0
        macd = ta.macd(df["close"], fast=12, slow=26, signal=9)
        if macd is not None:
            df["macd_hist"] = macd.iloc[:, 1]
            # Normalize berdasarkan close price
            df["macd_hist_norm"] = df["macd_hist"] / df["close"]
            df["macd_hist_norm"] = df["macd_hist_norm"].clip(-0.01, 0.01) * 100

        bb = ta.bbands(df["close"], length=20, std=2)
        if bb is not None:
            bb_lower = bb.iloc[:, 0]
            bb_upper = bb.iloc[:, 2]
            bb_range = bb_upper - bb_lower
            df["bb_position"] = (df["close"] - bb_lower) / bb_range.replace(0, np.nan)
            df["bb_position"] = df["bb_position"].fillna(0.5)

        ema_short = ta.ema(df["close"], length=9)
        ema_long = ta.ema(df["close"], length=21)
        df["ema_trend"] = ((ema_short - ema_long) / df["close"]).clip(-0.01, 0.01) * 100

        vol_sma = ta.sma(df["volume"], length=20)
        df["volume_ratio"] = (df["volume"] / vol_sma.replace(0, np.nan)).clip(0, 3) / 3
        df["volume_ratio"] = df["volume_ratio"].fillna(0.5)

        # Sentiment score (historis default 0.0 jika tidak tersedia)
        if "sentiment_score" not in df.columns:
            df["sentiment_score"] = 0.0

        atr = ta.atr(df["high"], df["low"], df["close"], length=14)
        df["atr_pct"] = (atr / df["close"]).clip(0, 0.1) * 10

        return df

    def create_labels(self, df: pd.DataFrame) -> pd.Series:
        """
        Buat label berdasarkan pergerakan harga di masa depan.

        LONG: harga naik > threshold dalam lookahead candles
        SHORT: harga turun > threshold
        HOLD: sideways
        """
        future_returns = df["close"].shift(-self.lookahead) / df["close"] - 1

        labels = pd.Series("HOLD", index=df.index)
        labels[future_returns > self.threshold_pct] = "LONG"
        labels[future_returns < -self.threshold_pct] = "SHORT"

        return labels

    def train(self, df: pd.DataFrame) -> dict:
        """
        Latih model dari DataFrame OHLCV.

        Returns:
            {"accuracy": float, "feature_importance": dict, "model_path": str}
        """
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.model_selection import train_test_split
        from sklearn.metrics import classification_report, accuracy_score
        import joblib

        # Prepare
        df = self.prepare_features(df.copy())
        labels = self.create_labels(df)

        feature_cols = [
            "rsi", "macd_hist_norm", "bb_position",
            "ema_trend", "volume_ratio", "sentiment_score", "atr_pct",
        ]

        # Drop rows with NaN
        valid_mask = df[feature_cols].notna().all(axis=1) & labels.notna()
        # Juga hapus lookahead terakhir (tidak punya label)
        valid_mask = valid_mask & (labels.index < len(df) - self.lookahead)

        X = np.asarray(df.loc[valid_mask, feature_cols].values, dtype=np.float64)
        y = np.asarray(labels[valid_mask].tolist(), dtype=object)

        if len(X) < 100:
            logger.error(f"Data terlalu sedikit untuk training: {len(X)} samples")
            return {"accuracy": 0, "error": "Data terlalu sedikit"}

        # Split
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42, shuffle=False
        )

        # Train
        model = RandomForestClassifier(
            n_estimators=100,
            max_depth=10,
            min_samples_split=10,
            min_samples_leaf=5,
            random_state=42,
            n_jobs=-1,
        )
        model.fit(X_train, y_train)

        # Evaluate
        y_pred = model.predict(X_test)
        accuracy = accuracy_score(y_test, y_pred)
        report = classification_report(y_test, y_pred, output_dict=True)

        # Feature importance
        importance = dict(zip(feature_cols, model.feature_importances_))

        # Simpan model
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        model_path = MODEL_DIR / "signal_model.pkl"
        joblib.dump(model, str(model_path))

        logger.info(f"Model dilatih — accuracy: {accuracy:.2%}")
        logger.info(f"Feature importance: {importance}")

        return {
            "accuracy": round(accuracy, 4),
            "report": report,
            "feature_importance": {k: round(v, 4) for k, v in importance.items()},
            "model_path": str(model_path),
            "samples_train": len(X_train),
            "samples_test": len(X_test),
        }


async def train_from_exchange(
    symbol: str = "BTC/USDT:USDT",
    timeframe: str = "1h",
    limit: int = 1000,
):
    """Latih model dari data historis bursa atau yfinance fallback."""
    import ccxt.async_support as ccxt
    import yfinance as yf

    ohlcv = []
    exchange = None
    try:
        exchange = ccxt.binance({
            "timeout": 4000,
            "options": {"defaultType": "future"},
        })
        ohlcv = await asyncio.wait_for(
            exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit),
            timeout=4.0,
        )
    except Exception as e:
        logger.warning(f"Fetch ccxt untuk training gagal ({e}), menggunakan yfinance...")
    finally:
        if exchange:
            await exchange.close()

    if not ohlcv:
        def _get_yf():
            t = yf.Ticker("BTC-USD")
            df_yf = t.history(period="1y", interval="1h")
            if df_yf.empty:
                return []
            res = []
            for idx, row in df_yf.iterrows():
                ts = int(idx.timestamp() * 1000)
                res.append([
                    ts,
                    float(row["Open"]),
                    float(row["High"]),
                    float(row["Low"]),
                    float(row["Close"]),
                    float(row["Volume"]),
                ])
            return res

        ohlcv = await asyncio.to_thread(_get_yf)

    if not ohlcv:
        logger.error("Gagal mengunduh data OHLCV untuk pelatihan")
        return {"error": "No data"}

    df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")

    trainer = ModelTrainer()
    result = trainer.train(df)

    logger.info(f"Pelatihan selesai: {result}")
    return result


if __name__ == "__main__":
    asyncio.run(train_from_exchange())
