"""
data/macro_fetcher.py — Pengambil data makroekonomi dari FRED + Forex Factory.

FRED memerlukan API key gratis (daftar di https://fred.stlouisfed.org/docs/api/).
Forex Factory calendar tidak perlu key.
"""

import asyncio
from typing import Dict, List, Optional
from datetime import datetime

import requests

from core.config import get_config
from core.logger import get_logger
from database.models import MacroData

logger = get_logger("macro_fetcher")

# Indikator FRED yang relevan untuk kripto
FRED_SERIES = {
    "DFF": "FED_FUNDS_RATE",       # Federal Funds Rate
    "CPIAUCSL": "CPI",              # Consumer Price Index
    "DTWEXBGS": "DXY",              # Dollar Index (broad)
    "UNRATE": "UNEMPLOYMENT",       # Unemployment Rate
    "T10Y2Y": "YIELD_CURVE",        # 10Y-2Y Treasury Spread
    "VIXCLS": "VIX",                # Volatility Index
}


class MacroFetcher:
    """
    Pengambil data makroekonomi:
    - FRED (Federal Reserve Economic Data) — gratis dengan API key
    - Forex Factory — kalender ekonomi mingguan, tanpa key
    """

    def __init__(self, fred_api_key: str = None):
        self.fred_api_key = fred_api_key
        self.config = get_config()

    async def fetch_fred_data(self) -> List[MacroData]:
        """Ambil semua indikator dari FRED."""
        if not self.fred_api_key:
            # FRED bersifat opsional. Melewati satu sumber macro bukan
            # kondisi yang perlu diperbaiki; di mode verbose tetap terlihat,
            # di terminal startup yang ringkas tidak.
            logger.info("FRED API key tidak diset — melewati sumber macro tersebut")
            return []

        results = []
        loop = asyncio.get_event_loop()

        for series_id, indicator_name in FRED_SERIES.items():
            try:
                data = await loop.run_in_executor(
                    None, self._fetch_fred_series, series_id
                )
                if data:
                    results.append(MacroData(
                        indicator=indicator_name,
                        value=data["value"],
                        period=data["date"],
                        source="FRED",
                    ))
                    logger.debug(f"FRED {indicator_name}: {data['value']} ({data['date']})")
            except Exception as e:
                logger.error(f"Gagal ambil FRED {series_id}: {e}")

        logger.info(f"FRED: {len(results)} indikator diambil")
        return results

    def _fetch_fred_series(self, series_id: str) -> Optional[dict]:
        """Ambil nilai terbaru dari satu seri FRED."""
        url = "https://api.stlouisfed.org/fred/series/observations"
        params = {
            "series_id": series_id,
            "api_key": self.fred_api_key,
            "file_type": "json",
            "sort_order": "desc",
            "limit": 1,
        }

        resp = requests.get(url, params=params, timeout=15)
        if resp.status_code != 200:
            return None

        observations = resp.json().get("observations", [])
        if not observations:
            return None

        obs = observations[0]
        value_str = obs.get("value", ".")

        # FRED kadang pakai "." untuk data belum tersedia
        if value_str == ".":
            return None

        return {
            "value": float(value_str),
            "date": obs.get("date", ""),
        }

    async def fetch_forex_factory_calendar(self) -> List[dict]:
        """
        Ambil kalender ekonomi mingguan dari Forex Factory.
        Sumber: nfs.faireconomy.media (mirror gratis FF calendar).
        """
        url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
        loop = asyncio.get_event_loop()

        try:
            resp = await loop.run_in_executor(
                None, lambda: requests.get(url, timeout=15)
            )

            if resp.status_code != 200:
                logger.warning(f"Forex Factory calendar error: {resp.status_code}")
                return []

            events = resp.json()

            # Filter event berdampak tinggi
            high_impact = [
                e for e in events
                if e.get("impact", "").lower() in ("high", "medium")
                and e.get("country", "").upper() == "USD"
            ]

            logger.info(f"Forex Factory: {len(high_impact)} event USD berdampak tinggi")
            return high_impact

        except Exception as e:
            logger.error(f"Gagal ambil Forex Factory: {e}")
            return []

    async def fetch_all(self) -> dict:
        """Ambil semua data makroekonomi."""
        fred_task = self.fetch_fred_data()
        ff_task = self.fetch_forex_factory_calendar()

        fred_data, ff_events = await asyncio.gather(fred_task, ff_task)

        return {
            "fred": fred_data,
            "calendar_events": ff_events,
        }

    def interpret_macro_context(self, fred_data: List[MacroData], calendar_events: List[dict]) -> dict:
        """
        Interpretasi kondisi makro menjadi konteks pasar.

        Returns:
            {
                "bias": "BULLISH" | "BEARISH" | "NEUTRAL",
                "risk_level": "LOW" | "MEDIUM" | "HIGH",
                "summary": "...",
                "factors": [...]
            }
        """
        factors = []
        bullish_score = 0
        bearish_score = 0

        for m in fred_data:
            if m.indicator == "FED_FUNDS_RATE":
                if m.value > 5.0:
                    bearish_score += 2
                    factors.append(f"Suku bunga tinggi ({m.value}%) — bearish untuk risk assets")
                elif m.value < 3.0:
                    bullish_score += 2
                    factors.append(f"Suku bunga rendah ({m.value}%) — bullish untuk risk assets")

            elif m.indicator == "DXY":
                if m.value > 105:
                    bearish_score += 1
                    factors.append(f"Dollar kuat (DXY {m.value}) — bearish kripto")
                elif m.value < 100:
                    bullish_score += 1
                    factors.append(f"Dollar lemah (DXY {m.value}) — bullish kripto")

            elif m.indicator == "VIX":
                if m.value > 25:
                    bearish_score += 1
                    factors.append(f"VIX tinggi ({m.value}) — pasar takut")
                elif m.value < 15:
                    bullish_score += 1
                    factors.append(f"VIX rendah ({m.value}) — pasar tenang")

        # Event kalender berdampak tinggi = risiko tinggi
        upcoming_high_impact = len([e for e in calendar_events if e.get("impact") == "High"])
        if upcoming_high_impact >= 3:
            factors.append(f"{upcoming_high_impact} event berdampak tinggi minggu ini — hati-hati")

        # Tentukan bias
        net = bullish_score - bearish_score
        if net >= 2:
            bias = "BULLISH"
        elif net <= -2:
            bias = "BEARISH"
        else:
            bias = "NEUTRAL"

        # Tentukan risk level
        risk_level = "LOW"
        if upcoming_high_impact >= 3 or abs(net) == 0:
            risk_level = "HIGH"
        elif upcoming_high_impact >= 1:
            risk_level = "MEDIUM"

        return {
            "bias": bias,
            "risk_level": risk_level,
            "summary": f"Makro {bias} (skor {net:+d}), risiko {risk_level}",
            "factors": factors,
            "bullish_score": bullish_score,
            "bearish_score": bearish_score,
        }
