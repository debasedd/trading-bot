"""
agents/base_agent.py — Kelas dasar untuk semua agen otonom.

Setiap agen mengikuti siklus: sense → think → act
"""

import asyncio
import traceback
from abc import ABC, abstractmethod
from typing import Optional
from datetime import datetime

from core.event_bus import EventBus, Channels
from core.logger import get_logger
from database.db import get_db
from database.repository import Repository
from database.models import AgentLog


class BaseAgent(ABC):
    """
    Abstract base class untuk semua agen.

    Subclass harus mengimplementasikan:
    - sense(): kumpulkan data
    - think(): analisis & penalaran
    - act(): ambil tindakan berdasarkan hasil penalaran
    """

    def __init__(self, name: str, event_bus: EventBus):
        self.name = name
        self.event_bus = event_bus
        self.logger = get_logger(f"agent.{name}")
        self._repo: Optional[Repository] = None
        self._running = False
        self._cycle_count = 0

    async def _get_repo(self) -> Repository:
        if self._repo is None:
            db = await get_db()
            self._repo = Repository(db)
        return self._repo

    @abstractmethod
    async def sense(self) -> dict:
        """
        Fase 1: Kumpulkan data dari sumber eksternal dan event bus.

        Returns:
            Dict berisi data mentah yang dikumpulkan.
        """
        pass

    @abstractmethod
    async def think(self, data: dict) -> dict:
        """
        Fase 2: Analisis data, hasilkan penalaran dan keputusan.

        Args:
            data: Output dari sense()

        Returns:
            Dict berisi hasil analisis dan keputusan.
        """
        pass

    @abstractmethod
    async def act(self, analysis: dict):
        """
        Fase 3: Ambil tindakan berdasarkan hasil penalaran.

        Args:
            analysis: Output dari think()
        """
        pass

    async def run_cycle(self):
        """Jalankan satu siklus sense → think → act."""
        self._cycle_count += 1
        cycle_id = f"{self.name}#{self._cycle_count}"

        try:
            self.logger.debug(f"Siklus {cycle_id} dimulai")

            # Sense
            data = await self.sense()

            # Think
            analysis = await self.think(data)

            # Act
            await self.act(analysis)

            # Log ke database
            await self._log_cycle(analysis)

            self.logger.debug(f"Siklus {cycle_id} selesai")

        except asyncio.CancelledError:
            # Ini BUKAN error. `shutdown()` membatalkan task yang sedang
            # menunggu I/O, dan APScheduler meneruskannya apa adanya.
            #
            # Di Python 3.8+ CancelledError turun dari BaseException,
            # bukan Exception — jadi `except Exception` di bawah TIDAK
            # menangkapnya, dan seluruh traceback terprinted ke terminal
            # setiap kali lo Ctrl+C.
            #
            # Kita resorpsi dan biarkan pembatalan itu merambat ke atas.
            self.logger.debug(f"Siklus {cycle_id} dibatalkan (shutdown)")
            raise

        except Exception as e:
            self.logger.error(f"Error di siklus {cycle_id}: {e}")
            self.logger.debug(traceback.format_exc())

            # Log error
            await self._log_error(str(e))

    async def _log_cycle(self, analysis: dict):
        """Simpan log penalaran ke database."""
        try:
            repo = await self._get_repo()
            import json

            log = AgentLog(
                agent_name=self.name,
                action="CYCLE",
                reasoning=analysis.get("reasoning", analysis.get("summary", "Tidak ada penalaran")),
                input_data=json.dumps(analysis.get("input_summary", {}), default=str)[:2000],
                output_data=json.dumps(analysis.get("output_summary", {}), default=str)[:2000],
            )
            await repo.insert_agent_log(log)
        except Exception as e:
            self.logger.error(f"Gagal log siklus: {e}")

    async def _log_error(self, error_msg: str):
        """Log error ke database."""
        try:
            repo = await self._get_repo()
            log = AgentLog(
                agent_name=self.name,
                action="ERROR",
                reasoning=error_msg[:2000],
            )
            await repo.insert_agent_log(log)
        except Exception:
            pass

    async def publish(self, channel: str, data: dict):
        """Shortcut untuk publish event."""
        await self.event_bus.publish(channel, data, source=self.name)
