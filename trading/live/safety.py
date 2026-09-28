"""
trading/live/safety.py — Gerbang yang harus lolos SEBELUM ada order yang menyentuh
uang sungguhan.

Modul ini sengaja dibuat **kecil, tanpa_side-effect, dan tanpa jaringan**.
Semua keputusan "boleh / tidak boleh" terkumpul di satu tempat supaya bisa
diaudit tanpa membaca seluruh sistem.

Aturan besarnya: **gagal berarti jangan kirim.** Setiap kondisi yang tidak
pasti aman menghasilkan penolakan, bukan percobaan. Untuk uang sungguhan,
kehati-hatian yang berlebihan jauh lebih murah daripada satu order yang
tidak seharusnya keluar.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, List, Optional

from core.config import LiveConfig
from core.logger import get_logger

logger = get_logger("live_safety")


class Blocker(str, Enum):
    """Penyebab penolakan. String supaya mudah dibaca di log."""

    LIVE_DISABLED = "live trading tidak dinyalakan"
    NOT_CONFIRMED = "variabel konfirmasi live tidak bernilai 1"
    MISSING_KEY = "private key tidak ada di environment"
    OUTSIDE_WINDOW = "di luar jendela waktu trading (UTC)"
    KILL_SWITCH = "kill switch aktif"
    ORDER_TOO_LARGE = "nilai order melebihi batas"
    POSITION_TOO_LARGE = "nilai posisi melebihi batas"
    EXPOSURE_TOO_LARGE = "total eksposur melebihi batas"
    DAILY_ORDER_LIMIT = "batas order harian tercapai"
    DAILY_LOSS_LIMIT = "batas kerugian harian tercapai"
    TOO_MANY_ERRORS = "terlalu banyak error beruntun"
    COLLATERAL_TOO_LOW = "collateral bebas di bawah minimum"
    RECONCILIATION_FAILED = "rekonsiliasi posisi dengan bursa gagal"
    INVALID_INPUT = "input order tidak valid"
    MISSING_CLOID = "order opening tanpa client order id"
    LEVERAGE_TOO_HIGH = "leverage melebihi batas bursa"
    COUNTER_STATE_UNREADABLE = "state penghitung harian tidak bisa dibaca"


@dataclass
class OrderRequest:
    """Satu niat order, sudah tervalidasi bentuknya tapi belum dikirim."""

    symbol: str
    is_buy: bool
    size: float
    price: float
    is_close: bool = False
    reduce_only: bool = False

    @property
    def notional(self) -> float:
        return abs(self.size) * self.price


@dataclass
class DayCounters:
    """
    Penghitung harian, dengan pemulihan state dari file.

    Dulu penghitung ini murni in-memory, dan komentarnya mengklaim itu
    "lebih konservatif". Justru sebaliknya: bot yang crash lalu start
    ulang akan mulai dari nol, dan batas harian jadi tidak ada sama
    sekali selama hari itu.

    Prinsipnya sekarang: **gagal memuat berarti paling konservatif.**
    File yang tidak ada = hari pertama, counter kosong. File yang ada
    tapi rusak = order DITOLAK sampai file diperbaiki, bukan diabaikan
    dengan angka yang mungkin salah.
    """

    day_utc: str = ""
    orders_sent: int = 0
    realized_pnl: float = 0.0
    consecutive_errors: int = 0
    _unreadable: bool = field(default=False, repr=False)

    @property
    def readable(self) -> bool:
        """False berarti state tidak bisa dipercaya; order harus ditolak."""
        return not self._unreadable

    def to_dict(self) -> Dict[str, Any]:
        return {
            "day_utc": self.day_utc,
            "orders_sent": self.orders_sent,
            "realized_pnl": self.realized_pnl,
            "consecutive_errors": self.consecutive_errors,
        }

    def load(self, path) -> None:
        """Muat state dari file JSON."""
        self._unreadable = False
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.error(
                "Counter harian tidak bisa dibaca (%s): %s. Order akan "
                "DITOLAK sampai file diperbaiki." % (path, exc))
            self._unreadable = True
            return
        try:
            self.day_utc = str(data.get("day_utc") or "")
            self.orders_sent = int(data.get("orders_sent") or 0)
            self.realized_pnl = float(data.get("realized_pnl") or 0.0)
            self.consecutive_errors = int(data.get("consecutive_errors") or 0)
        except (TypeError, ValueError) as exc:
            logger.error(
                "Isi counter harian tidak valid (%s): %s. Order akan "
                "DITOLAK sampai file diperbaiki." % (path, exc))
            self._unreadable = True

    def save(self, path) -> bool:
        """
        Simpan state. Kembalikan False kalau gagal.

        Penulisan lewat file sementara lalu rename: rename pada filesystem
        yang sama bersifat atomik, jadi restart di tengah penulisan tidak
        menghasilkan file setengah jadi.
        """
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(json.dumps(self.to_dict()), encoding="utf-8")
            tmp.replace(path)
            return True
        except OSError as exc:
            logger.error("Counter harian gagal disimpan (%s): %s" % (path, exc))
            return False

    def rollover_if_needed(self, now: Optional[datetime] = None) -> None:
        now = now or datetime.now(timezone.utc)
        today = now.strftime("%Y-%m-%d")
        if self.day_utc != today:
            logger.info(
                "Penghitung harian di-rollover: %s -> %s"
                % (self.day_utc or "(kosong)", today))
            self.day_utc = today
            self.orders_sent = 0
            self.realized_pnl = 0.0
            # consecutive_errors sengaja TIDAK direset: error beruntun
            # lintas tengah malam harus hilang sebelum bot boleh mengirim.


class SafetyGate:
    """
    Satu-satunya tempat yang memutuskan boleh-tidaknya sebuah order dikirim.

    Setiap method mengembalikan daftar `Blocker` yang MENJELASKAN penolakan,
    bukan sekadar boolean. Saat uang yang hilang, "kenapa tidak dikirim"
    jauh lebih berharga daripada "tidak dikirim".
    """

    def __init__(self, cfg: LiveConfig, env: Optional[dict] = None,
                 state_path=None):
        self.cfg = cfg
        self.env = env if env is not None else dict(os.environ)
        self.counters = DayCounters()
        # Set SEBELUM load, supaya file yang tersimpan untuk "hari ini" tidak
        # langsung di-rollover jadi nol. Tanpa ini, setiap panggilan
        # can_send() me-reset counter dan batas harian tidak pernah berlaku.
        self.counters.rollover_if_needed()
        self.engaged = False
        self._private_key: Optional[str] = None
        self._key_loaded = False
        # Lokasi file state. Default ke folder data_store supaya tidak
        # menyentuh direktori kerja yang mungkin tidak bisa ditulis.
        self.state_path = state_path or Path(
            "data_store/live_counters.json")
        if state_path is not None or str(self.env.get("TRADEBOT_LIVE", "")) in (
                "1", "true", "yes"):
            # Hanya muat dari file kalau live memang diaktifkan, supaya
            # paper trading tidak pernah menyentuh file apa pun.
            self.counters.load(self.state_path)
            # File mungkin berisi state hari yang sudah lewat. Setelah
            # load, cek ulang supaya angka basi tidak dipakai.
            self.counters.rollover_if_needed()

    def persist(self) -> bool:
        """Simpan counter ke disk. Dipanggil setelah setiap perubahan."""
        return self.counters.save(self.state_path)

    # ── Kredensial ─────────────────────────────────────────────────────

    @property
    def private_key(self) -> Optional[str]:
        """
        Private key dibaca SEKALI dari environment, lalu disimpan di memori.

        `env` bisa di-inject untuk test, jadi tidak ada test yang perlu
        menyentuh environment asli. Properti ini tidak pernah menulis
        key ke mana pun — tidak ke log, tidak ke DB, tidak ke file.
        """
        if not self._key_loaded:
            self._private_key = self.env.get(self.cfg.private_key_env) or None
            self._key_loaded = True
        return self._private_key

    def redact(self, text: str) -> str:
        """
        Buang private key dari teks apa pun sebelum masuk log.

        Dipakai pada setiap pesan error yang mungkin memuat payload
        yang dibuang SDK. Satu kebocoran key di log berarti akun Anda
        dikompromikan, jadi ini bukan hiasan.
        """
        if not text:
            return text
        out = text
        key = self._private_key or self.env.get(self.cfg.private_key_env)
        if key and len(key) >= 8:
            out = out.replace(key, "<REDACTED>")
        return out

    # ── Pemeriksaan master ─────────────────────────────────────────────

    def in_live_window(self, now: Optional[datetime] = None) -> bool:
        """
        Apakah sekarang berada di dalam jendela trading?

        Perbandingan dilakukan dalam UTC, sama seperti seluruh basis data
        dan jadwal bot — mencampur zona waktu di sini akan membuka jendela
        pada jam yang salah.
        """
        now = now or datetime.now(timezone.utc)
        hour = now.hour
        start, end = self.cfg.live_window_utc
        if start <= end:
            return start <= hour < end
        # Jendela yang melewati tengah malam, mis. (22, 4).
        return hour >= start or hour < end

    def master_blockers(self, now: Optional[datetime] = None) -> List[Blocker]:
        """
        Pemeriksaan yang berlaku untuk SEMUA order, bukan order tertentu.

        Dipanggil sekali sebelum order apa pun, dan lagi setelah ethereum
        address siap — karena reconcile hanya mungkin dilakukan kalau kita
        sudah tahu kita benar-benar di akun yang benar.
        """
        blockers: List[Blocker] = []
        self.counters.rollover_if_needed(now)

        # 0. State harian harus bisa dibaca. Kalau file counter rusak,
        #    angka batas hari ini tidak diketahui, dan batas yang tidak
        #    diketahui berarti tidak ada batas. Menolak lebih aman.
        if not self.counters.readable:
            blockers.append(Blocker.COUNTER_STATE_UNREADABLE)

        # 1. Mode live harus dinyalakan. Di `load_config` nilai ini sengaja
        #    dikunci ke False, jadi satu-satunya jalan ke sini adalah env.
        if str(self.env.get("TRADEBOT_LIVE", "")).strip() not in ("1", "true", "yes"):
            blockers.append(Blocker.LIVE_DISABLED)

        # 2. Konfirmasi eksplisit kedua. Private key saja tidak cukup —
        #    ada langkah yang harus disengaja.
        if str(self.env.get(self.cfg.live_confirm_env, "")).strip() not in (
            "1", "true", "yes"
        ):
            blockers.append(Blocker.NOT_CONFIRMED)

        # 3. Kill switch.
        if self.engaged or str(self.env.get("TRADEBOT_LIVE_KILL_SWITCH", "")) not in (
            "", "0", "false", "no"
        ):
            blockers.append(Blocker.KILL_SWITCH)

        # 4. Private key harus ada.
        if not self.private_key:
            blockers.append(Blocker.MISSING_KEY)

        # 5. Jendela waktu.
        if not self.in_live_window(now):
            blockers.append(Blocker.OUTSIDE_WINDOW)

        # 6. Penghitung harian.
        if self.counters.orders_sent >= self.cfg.max_daily_orders:
            blockers.append(Blocker.DAILY_ORDER_LIMIT)
        if self.counters.realized_pnl <= -abs(self.cfg.max_daily_loss):
            blockers.append(Blocker.DAILY_LOSS_LIMIT)
        if self.counters.consecutive_errors >= self.cfg.max_consecutive_errors:
            blockers.append(Blocker.TOO_MANY_ERRORS)

        return blockers

    def order_blockers(
        self,
        request: OrderRequest,
        free_collateral: float = 0.0,
        current_exposure: float = 0.0,
        symbol_exposure: float = 0.0,
        cloid: Optional[Any] = None,
        leverage: Optional[int] = None,
    ) -> List[Blocker]:
        """
        Pemeriksaan khusus untuk SATU order.

        `free_collateral`, `current_exposure`, dan `symbol_exposure` berasal
        dari state BURSA, bukan dari database lokal. Database lokal bisa
        menyimpang, dan kalau book lokal dianggap benar, limit jadi tidak
        melindungi apa pun.
        """
        blockers: List[Blocker] = []

        if request.size <= 0:
            blockers.append(Blocker.INVALID_INPUT)
        if request.price <= 0:
            blockers.append(Blocker.INVALID_INPUT)

        # Order yang MEMBUKA posisi wajib punya client order id. Tanpa itu,
        # timeout membuat retry menggandakan posisi karena bot tidak pernah
        # bisa tahu apakah order pertamanya sebenarnya masuk.
        if not request.is_close and not cloid:
            blockers.append(Blocker.MISSING_CLOID)

        # Leverage di luar batas bursa ditolak bursa dengan pesan yang tidak
        # selalu jelas. Menangkapnya di sini menghasilkan log yang terbaca.
        if leverage is not None:
            if not (1 <= int(leverage) <= self.cfg.max_leverage):
                blockers.append(Blocker.LEVERAGE_TOO_HIGH)

        notional = request.notional
        if notional > self.cfg.max_order_notional:
            blockers.append(Blocker.ORDER_TOO_LARGE)
        if symbol_exposure + notional > self.cfg.max_position_notional:
            blockers.append(Blocker.POSITION_TOO_LARGE)
        if current_exposure + notional > self.cfg.max_total_notional:
            blockers.append(Blocker.EXPOSURE_TOO_LARGE)
        if free_collateral - notional < self.cfg.min_free_collateral:
            blockers.append(Blocker.COLLATERAL_TOO_LOW)

        return blockers
    def can_send(
        self,
        request: OrderRequest,
        free_collateral: float = 0.0,
        current_exposure: float = 0.0,
        symbol_exposure: float = 0.0,
        now: Optional[datetime] = None,
        cloid: Optional[Any] = None,
        leverage: Optional[int] = None,
    ) -> tuple:
        """
        Satu-satunya jalan menuju `exchange.order(...)`.

        Mengembalikan `(allowed, blockers)` — dan pemanggil WAJIB memeriksa
        `allowed` sebelum mengirim. Mengembalikan tuple, bukan boolean,
        supaya alasan penolakan selalu ikut terbawa.
        """
        blockers = self.master_blockers(now)
        if not blockers:
            blockers = self.order_blockers(
                request,
                free_collateral=free_collateral,
                current_exposure=current_exposure,
                symbol_exposure=symbol_exposure,
                cloid=cloid,
                leverage=leverage,
            )

        if blockers:
            logger.warning(
                f"ORDER DITOLAK {request.symbol} "
                f"{'BUY' if request.is_buy else 'SELL'} {request.size} @ {request.price}: "
                + "; ".join(b.value for b in blockers)
            )
            return False, blockers
        return True, []

    def engage_kill_switch(self, reason: str) -> None:
        """Matikan trading sampai operator melepasnya secara manual."""
        if not self.engaged:
            logger.error(f"KILL SWITCH diaktifkan oleh sistem: {reason}")
        self.engaged = True

    # ── Pencatatan hasil ───────────────────────────────────────────────
    #
    # Method-method ini di SINI, bukan di `DayCounters`: yang SISTEM yang
    # memutuskan kapan kill switch menyala, bukan penghitung angka. Sempat
    # terbalik, dan gejalanya `AttributeError` saat bot mencoba mencatat —
    # yang persis di jalur yang seharusnya menyelamatkan uang.

    def record_error(self) -> None:
        """Catat satu kegagalan."""
        self.counters.consecutive_errors += 1
        self.persist()
        if self.counters.consecutive_errors >= self.cfg.max_consecutive_errors:
            if not self.engaged:
                logger.error(
                    f"KILL SWITCH aktif: {self.counters.consecutive_errors} "
                    f"error beruntun (batas {self.cfg.max_consecutive_errors}). "
                    "Set TRADEBOT_LIVE_KILL_SWITCH=0 untuk melepas."
                )
            self.engaged = True

    def record_success(self) -> None:
        """Satu keberhasilan memutus rentetan error."""
        self.counters.consecutive_errors = 0
        self.persist()

    def record_realized_pnl(self, pnl: float) -> None:
        self.counters.realized_pnl += pnl
        self.persist()

    def record_order_sent(self) -> None:
        self.counters.orders_sent += 1
        self.persist()


