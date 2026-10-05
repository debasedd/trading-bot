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


def _current_commit_hash() -> str:
    """
    Hash commit git saat ini, atau `"unknown"`.

    Dicatat di audit log supaya pertanyaan "kode apa yang sedang
    berjalan waktu kill switch dilepas" bisa dijawab, bukan ditebak.
    Kegagalan `git` TIDAK boleh menggagalkan pelepasan — audit yang
    kosong lebih berguna daripada switch yang tidak bisa dilepas.
    """
    try:
        import subprocess
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
            cwd=str(Path(__file__).resolve().parent.parent),
        )
        if out.returncode == 0:
            return out.stdout.strip() or "unknown"
    except Exception:  # noqa: BLE001
        pass
    return "unknown"


def _env_is_live(env: dict) -> bool:
    return str(env.get("TRADEBOT_LIVE", "")).strip() in ("1", "true", "yes")


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
    # Kill switch yang sudah aktif harus bertahan melewati restart. Kalau
    # tidak, mematikan bot menjadi "perbaikan": bot yang restart otomatis
    # karena crash akan kembali dengan limit yang baru saja meledak. Ada
    # di sini, bukan di `SafetyGate`, supaya `persist()` — yang sudah
    # berjalan di setiap `record_success` dan `record_error` — menulisnya
    # tanpa perlu jalur tulis kedua yang bisa terlewat.
    engaged: bool = False
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
            "engaged": self.engaged,
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
            # `bool(...)` eksplisit: `bool("false")` adalah True, jadi
            # file yang berisi string JSON akan menyalakan
            # kill switch, bukan sebaliknya seperti yang dimaksud penulisnya.
            self.engaged = bool(data.get("engaged") or False)
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

    #: SATU-SATUNYA sumber daftar nilai yang berarti "off" untuk
    #: `TRADEBOT_LIVE_KILL_SWITCH`. Dulu daftar ini ada di DUA tempat dan
    #: keduanya berbeda: `__init__` memakai
    #: `("0", "false", "no", "off")` sementara `master_blockers` memakai
    #: `("", "0", "false", "no")`.
    #:
    #: Akibatnya operator yang menyetel `TRADEBOT_LIVE_KILL_SWITCH=off`
    #: melihat switch dilepas di log, sementara `master_blockers()` tetap
    #: mengembalikan `KILL_SWITCH` dan menolak setiap order — tanpa satu
    #: pesan pun yang bilang release-nya diabaikan.
    #:
    #: Dua daftar untuk satu konsep adalah tempat bug hidup. Sekarang
    #: hanya ada satu, dan `tests/test_kill_switch_policy.py` memverifikasi
    #: bahwa kedua pemakai benar-benar mengacunya.
    #:
    #: `""` SENGAJA TIDAK ada di sini. Env yang tidak di-set adalah keadaan
    #: ketiga: ia harus mengikuti apa yang tersimpan di disk. Kalau `""`
    #: diperlakukan sebagai "off", bot yang restart dengan konfigurasi
    #: bersih akan diam-diam melepas switch yang sengaja dinyalakan.
    KILL_SWITCH_OFF_VALUES = frozenset({"0", "false", "no", "off"})

    #: Frasa yang harus DIKETIK ULANG operator untuk melepas switch.
    #: Sengaja panjang dan spesifik supaya tidak bisa terjadi karena
        #: paste tidak sengaja, dan isinya menyebut konsekuensinya supaya
    #: operator membacanya sebelum mengetik.
    RELEASE_CONFIRMATION_PHRASE = (
        "SAYA SUDAH PERIKSA PENYEBABNYA DAN INGIN MELANGSUNGKAN TRADING"
    )

    def __init__(self, cfg: LiveConfig, env: Optional[dict] = None,
                 state_path=None):
        self.cfg = cfg
        self.env = env if env is not None else dict(os.environ)
        self.counters = DayCounters()
        # Set SEBELUM load, supaya file yang tersimpan untuk "hari ini" tidak
        # langsung di-rollover jadi nol. Tanpa ini, setiap panggilan
        # can_send() me-reset counter dan batas harian tidak pernah berlaku.
        self.counters.rollover_if_needed()
        self._private_key: Optional[str] = None
        self._key_loaded = False
        # Lokasi file state. Default ke folder data_store supaya tidak
        # menyentuh direktori kerja yang mungkin tidak bisa ditulis.
        self.state_path = state_path or Path(
            "data_store/live_counters.json")
        # Audit log pelepasan mengikuti `state_path`. Begitu keduanya berada
        # di tempat yang sama, test yang mengarahkan path ke tmp_path
        # otomatis mengarahkan audit juga — tanpa konfigurasi kedua yang
        # bisa lupa dan menulis audit ke file produksi.
        self.audit_path = self.state_path.with_name(
            self.state_path.stem + "_audit.jsonl")
        if state_path is not None or str(self.env.get("TRADEBOT_LIVE", "")) in (
                "1", "true", "yes"):
            # Hanya muat dari file kalau live memang diaktifkan, supaya
            # paper trading tidak pernah menyentuh file apa pun.
            self.counters.load(self.state_path)
            # File mungkin berisi state hari yang sudah lewat. Setelah
            # load, cek ulang supaya angka basi tidak dipakai.
            self.counters.rollover_if_needed()
            # FAIL CLOSED: file yang ada tapi tidak terbaca berarti angka
            # batas hari ini tidak diketahui, dan batas yang tidak diketahui
            # berarti tidak ada batas.
            #
            # Dulu kondisi ini hanya menambah blocker
            # `COUNTER_STATE_UNREADABLE`, jadi `engaged` tetap False dan
            # `health_check()` melaporkan `kill_switch: False` sementara
            # gerbang sebenarnya menolak setiap order — laporan yang
            # berlawanan dengan kenyataan.
            if not self.counters.readable:
                self.counters.engaged = True
        else:
            # Tanpa file, `engaged` harus mulai dari False dan TIDAK boleh
            # menyalin apa pun dari disk. Ini yang membuat test terisolasi:
            # test yang meng-inject `env` sendiri tidak pernah menyentuh
            # `data_store/live_counters.json`, jadi tidak bisa mewarisi
            # kill switch dari run sebelumnya — atau dari test lain.
            self.counters.engaged = False

        # `TRADEBOT_LIVE_KILL_SWITCH` HANYA BISA MENYALAKAN.
        #
        #   tidak di-set / "0"/"false"/"no"/"off" -> ikuti disk, jangan
        #                                             sentuh apa pun
        #   "1"/"true"/"yes"/...                     -> paksa AKTIF
        #
        # Versi sebelumnya memperlakukan nilai "off" sebagai "LEPAS". Itu
        # membuat pelepasan kill switch menjadi satu ketikan di shell —
        # bisa terjadi karena salah ketik, bisa karena restart cronjob yang
        # menyalin environment, dan tidak ada jejak siapa yang melepas.
        #
        # Env yang bisa MENYALAKAN tapi tidak bisa MELEPAS membuat kelas
        # kesalahan jauh lebih kecil: salah ketik, salah copy-paste, atau
        # proses restart yang bringa state switch dari disk. Melepas switch
        # adalah keputusan yang harus terlihat dan bisa dipertanggungjawabkan,
        # jadi ia pindah ke `operator_release()`, yang menuntut konfirmasi
        # ketik, alasan, rekonsiliasi bersih, dan audit log.
        raw_kill = str(self.env.get("TRADEBOT_LIVE_KILL_SWITCH", "") or "").strip().lower()
        if raw_kill and raw_kill not in self.KILL_SWITCH_OFF_VALUES:
            self.counters.engaged = True
            logger.error(
                "KILL SWITCH aktif karena TRADEBOT_LIVE_KILL_SWITCH diset. "
                "Tidak ada order yang dikirim sampai operator melepasnya."
            )
        elif raw_kill and self.counters.engaged:
            # Nilai "off" TIDAK melepas switch yang sudah aktif di disk.
            # Pesan ini wajib dan wajib menyebut jalan yang benar,
            # supaya operator tidak mengira dia sudah melepasnya.
            logger.error(
                "TRADEBOT_LIVE_KILL_SWITCH=%s DIABAIKAN: env tidak pernah "
                "melepas kill switch yang sudah aktif. Env hanya bisa "
                "MENYALAKAN. Untuk melepas, jalankan perintah operator "
                "resmi (lihat docs/STATE.md § Pelepasan kill switch) — "
                "perintah itu meminta konfirmasi ketik, alasan, dan "
                "rekonsiliasi bersih, serta mencatat audit log.", raw_kill
            )
        elif self.counters.engaged:
            logger.error(
                "KILL SWITCH masih aktif dari run sebelumnya. Tidak ada "
                "order yang dikirim sampai operator melepasnya lewat "
                "perintah operator resmi."
            )

    def operator_release(self, reason: str, typed_confirmation: str,
                          reconcile_clean: bool,
                          now: Optional[datetime] = None) -> bool:
        """
        SATU-SATUNYA jalan melepas kill switch. Env tidak bisa melakukannya.

        Empat syarat, semuanya wajib:

        1. **`typed_confirmation`** harus persis sama dengan
           `SafetyGate.RELEASE_CONFIRMATION_PHRASE`. Yang diketik ulang
           memaksa operator membaca Consequences-nya — melepas switch
           adalah tindakan yang tidak bisa dibatalkan.
        2. **`reason`** tidak boleh kosong. Tanpa alasan, audit log
           menyimpan "dilepas" tanpa jawaban "dilepas kenapa", dan tidak
           ada yang bisa belajar dari kejadiannya.
        3. **`reconcile_clean`** harus True. Melepas switch sementara posisi
           lokal dan bursa tidak cocok mengembalikan bot ke order dengan
           keyakinan salah — itu persis kondisi yang memicu switch di
           tempat pertama.
        4. Audit log ditulis SETELAH semua syarat terpenuhi, dan mencatat
           waktu, alasan, state sebelum/sesudah, dan hash commit.

        Mengembalikan True kalau switch benar-benar terlepas.

        `disengage_kill_switch()` tetap ada untuk pemakaian internal, tapi
        TIDAK menyentuh state `engaged` di disk dan tidak menulis audit log
        — itu bukan pelepasan, itu hanya perubahan lokal yang hilang saat
        restart.
        """
        if not self.engaged:
            logger.warning(
                "Permintaan lepas kill switch diabaikan: switch sudah "
                "tidak aktif."
            )
            self._write_release_audit(
                outcome="ignored_not_engaged", reason=reason,
                before=False, after=False, now=now)
            return False

        if typed_confirmation != self.RELEASE_CONFIRMATION_PHRASE:
            raise PermissionError(
                "Konfirmasi tidak cocok. Frasa yang harus diketik ulang: %r"
                % self.RELEASE_CONFIRMATION_PHRASE)

        if not reason or not str(reason).strip():
            raise ValueError("Alasan wajib diisi untuk melepas kill switch.")

        if not reconcile_clean:
            raise RuntimeError(
                "Rekonsiliasi belum bersih — kill switch TIDAK dilepas. "
                "Melepas switch sekarang mengembalikan bot ke order dengan "
                "keyakinan salah soal posisi. Perbaiki divergensi dulu.")

        before = True
        self.counters.engaged = False
        self.counters.consecutive_errors = 0
        self.persist()
        self._write_release_audit(
            outcome="released", reason=reason,
            before=before, after=False, now=now)
        logger.warning(
            "KILL SWITCH dilepas oleh operator: %s. Ini TIDAK memperbaiki "
            "apa pun — pastikan penyebabnya sudah ditangani.", reason)
        return True

    def _write_release_audit(self, outcome: str, reason: str,
                             before: bool, after: bool,
                             now: Optional[datetime] = None) -> None:
        """
        Tulis satu baris JSON ke audit log.

        Fields: waktu UTC, outcome, alasan, state sebelum/sesudah, dan hash
        commit — supaya jawaban "kode apa yang sedang berjalan saat itu"
        bisa dijawab kemudian, bukan ditebak.
        """
        now = now or datetime.now(timezone.utc)
        record = {
            "at_utc": now.isoformat(),
            "event": "kill_switch_release",
            "outcome": outcome,
            "reason": str(reason or ""),
            "engaged_before": bool(before),
            "engaged_after": bool(after),
            "commit": _current_commit_hash(),
            "state_path": str(self.state_path),
        }
        line = json.dumps(record, ensure_ascii=False, sort_keys=True)
        try:
            self.audit_path.parent.mkdir(parents=True, exist_ok=True)
            with self.audit_path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError as exc:
            # Kegagalan menulis audit TIDAK boleh membatalkan pelepasan
            # yang sudah terjadi — switch sudah tidak aktif di disk, dan
            # memaksa operator mengulanginya hanya menambahrecord ganda.
            # Tapi harus terlihat.
            logger.error("Gagal menulis audit log pelepasan (%s): %s",
                         self.audit_path, exc)

    @property
    def engaged(self) -> bool:
        """Kill switch aktif. Proxy ke counter supaya ikut ter-persist."""
        return self.counters.engaged

    @engaged.setter
    def engaged(self, value: bool) -> None:
        self.counters.engaged = bool(value)

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
        #
        # Mengakai `KILL_SWITCH_OFF_VALUES` — konstanta yang sama dengan
        # `__init__`. Dulu daftar di sini berbeda (`""` ikut masuk), dan
        # itu membuat `TRADEBOT_LIVE_KILL_SWITCH=off` melepas switch di
        # `__init__` tapi memblokir di sini.
        #
        # Env yang tidak di-set (`""`) TIDAK masuk daftar off, jadi
        # kondisi di bawah ini setara dengan `self.engaged`: ia mengikuti
        # disk, bukan memaksa apa pun.
        if self.engaged or (str(
                self.env.get("TRADEBOT_LIVE_KILL_SWITCH", "")).strip().lower()
                not in {""} | self.KILL_SWITCH_OFF_VALUES):
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
        # Persist di sini, bukan hanya di `record_error`. Jalur ini
        # dipanggil dari `LiveEngine` (SL gagal dipasang, reconcile
        # gagal, health check) yang tidak selalu lewat `record_error` —
        # jadi tanpa baris ini, kill switch yang dinyalakan karena
        # perlindungan hilang akan hilang lagi saat restart.
        self.persist()

    def disengage_kill_switch(self, reason: str) -> bool:
        """
        Lepas kill switch. Mengembalikan True kalau benar-benar terlepas.

        SENGaja tidak menghapus dirinya sendiri: satu-satunya pemanggil
        yang sah adalah operator, lewat `TRADEBOT_LIVE_KILL_SWITCH=0`
        atau perintah TUI. Tidak ada kode produksi yang memanggil ini.

        Melepas RESETJUMLAH error beruntun, karena itu yang menyalakan
        switch di `record_error`. Kalau tidak, `can_send` akan langsung
        menyalakannya lagi pada error berikutnya dan operator melihat
        switch "mati" selama satu order sebelum menyala lagi — lebih buruk
        daripada tidak melepasnya sama sekali, karena ia menyalakan
        keyakinan salah bahwa masalahnya beres.
        """
        if not self.engaged:
            logger.warning(
                "Permintaan lepas kill switch diabaikan: switch sudah "
                "tidak aktif."
            )
            return False

        logger.warning(
            "KILL SWITCH dilepas oleh operator: %s. Reset %d error "
            "beruntun. Pastikan penyebabnya sudah diperbaiki sebelum "
            "melanjutkan — melepas switch TIDAK memperbaiki apa pun.",
            reason, self.counters.consecutive_errors,
        )
        self.engaged = False
        self.counters.consecutive_errors = 0
        self.persist()
        return True

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


