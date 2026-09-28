"""
core/scheduler.py — Wrapper APScheduler + deteksi jam pasar AS.
"""

import asyncio
from datetime import datetime
from typing import Callable, Optional

import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from core.config import get_config
from core.logger import get_logger

logger = get_logger("scheduler")


def is_us_market_open() -> bool:
    """Cek apakah pasar saham AS sedang buka (jam reguler, Senin-Jumat)."""
    cfg = get_config().us_market
    tz = pytz.timezone(cfg.timezone)
    now = datetime.now(tz)

    # Sabtu (5) dan Minggu (6) tutup
    if now.weekday() >= 5:
        return False

    current_minutes = now.hour * 60 + now.minute
    open_minutes = cfg.open_hour * 60 + cfg.open_minute
    close_minutes = cfg.close_hour * 60

    return open_minutes <= current_minutes < close_minutes


class AgentScheduler:
    """
    Penjadwal agen yang menyesuaikan interval saat jam buka pasar AS.

    Penggunaan:
        scheduler = AgentScheduler()
        scheduler.add_agent_job("news_agent", news_agent.run_cycle, 300, 120)
        scheduler.start()
    """

    def __init__(self):
        self._scheduler = AsyncIOScheduler()
        self._jobs: dict = {}
        self._check_interval = 60  # Cek perubahan jam pasar tiap 60 detik

    def add_agent_job(
        self,
        name: str,
        func: Callable,
        interval_normal: int,
        interval_us_open: int,
        start_immediately: bool = True,
    ):
        """
        Tambahkan job agen dengan dua interval:
        - interval_normal: interval saat pasar AS tutup (detik)
        - interval_us_open: interval saat pasar AS buka (detik)
        """
        current_interval = interval_us_open if is_us_market_open() else interval_normal

        job = self._scheduler.add_job(
            func,
            trigger=IntervalTrigger(seconds=current_interval),
            id=name,
            name=name,
            replace_existing=True,
            max_instances=1,
        )

        self._jobs[name] = {
            "func": func,
            "interval_normal": interval_normal,
            "interval_us_open": interval_us_open,
            "current_interval": current_interval,
        }

        logger.info(f"Job '{name}' ditambahkan — interval: {current_interval}s")

        if start_immediately:
            # Jalankan sekali saat startup
            self._scheduler.add_job(func, id=f"{name}_init", replace_existing=True)

    def add_fixed_job(self, name: str, func: Callable, interval_seconds: int):
        """Tambahkan job dengan interval tetap (tidak berubah saat pasar AS)."""
        self._scheduler.add_job(
            func,
            trigger=IntervalTrigger(seconds=interval_seconds),
            id=name,
            name=name,
            replace_existing=True,
            max_instances=1,
        )
        logger.info(f"Job tetap '{name}' ditambahkan — interval: {interval_seconds}s")

    async def _adjust_intervals(self):
        """Sesuaikan interval berdasarkan jam pasar AS."""
        market_open = is_us_market_open()

        for name, info in self._jobs.items():
            target = info["interval_us_open"] if market_open else info["interval_normal"]

            if target != info["current_interval"]:
                self._scheduler.reschedule_job(
                    name,
                    trigger=IntervalTrigger(seconds=target),
                )
                info["current_interval"] = target
                status = "BUKA" if market_open else "TUTUP"
                logger.info(
                    f"Interval '{name}' diubah ke {target}s (pasar AS {status})"
                )

    def start(self):
        """Mulai scheduler."""
        # Job untuk memeriksa perubahan jam pasar
        self._scheduler.add_job(
            self._adjust_intervals,
            trigger=IntervalTrigger(seconds=self._check_interval),
            id="_market_check",
            name="market_hour_check",
            replace_existing=True,
        )

        self._scheduler.start()
        logger.info("Scheduler dimulai")

    def shutdown(self):
        """Hentikan scheduler."""
        self._scheduler.shutdown(wait=False)
        logger.info("Scheduler dihentikan")

    @property
    def running(self) -> bool:
        return self._scheduler.running
