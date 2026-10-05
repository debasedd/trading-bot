"""
single-instance.py — satu proses bot per akun.

DUA INSTANCE, SATU AKUN
-----------------------
Dua bot yang mengirim order ke akun yang sama tidak sekadar "agak
berlebihan": keduanya membaca posisi dari bursa, menghitung, lalu
mengirim. Mereka tidak tahu tentang order satu sama lain, jadi dua order
menjadi dua eksposur. Kill switch hanya ada di satu proses — proses kedua
tidak punya state-nya.

Untuk akun paper bot itu hanya mengulang pekerjaan. Untuk akun live itu
uang sungguhan bergerak dua kali dari yang dihitung siapa pun.

LOCK FILE + PID
---------------
Lock adalah file yang namanya diturunkan dari alamat akun, isinya PID.
Alasan PID, bukan hanya keberadaan file: proses yang mati mendadak
(crash, kill -9, reboot) MELEWATKAN kesempatan membersihkan apa pun, jadi
file lock akan tertinggal. Lock yang tertinggal tanpa cek PID akan
menolak start selamanya — bot yang mati sekali tidak akan pernah bisa
jalan lagi tanpa intervensi manual.

Jadi: PID yang tercatat dibaca, dicek masih hidup atau tidak, dan hanya
itu yang menentukan lock dianggap sah. PID yang sudah mati = lock
basi, diambil alih dengan log yang menyebutnya.

Batas yang diketahui
-------------------
`os.kill(pid, 0)` memberi tahu proses hidup atau tidak, TIDAK untuk siapa.
Lock ini mencegah dua proses pada mesin yang sama. Ia TIDAK mencegah
dua mesin berbeda, dan TIDAK mencegah proses lain yang kebetulan memakai
akun itu.
"""

import errno
import os
from pathlib import Path
from typing import Optional


def _lock_dir() -> Path:
    d = Path("data_store")
    d.mkdir(parents=True, exist_ok=True)
    return d


def lock_path_for(account: str) -> Path:
    """
    Path lock untuk satu akun.

    Alamat dinormalisasi ke huruf kecil supaya `0xAB..` dan `0xab..`
    tidak mendapat dua lock berbeda untuk akun yang sama.
    """
    key = (account or "tanpa-alamat").strip().lower()
    return _lock_dir() / ("live_instance_%s.lock" % key)


def _pid_alive(pid: int) -> bool:
    """
    True kalau proses dengan PID itu masih hidup.

    LINTAS PLATFORM
    ---------------
    `os.kill(pid, 0)` adalah cara POSIX: ia tidak mengirim sinyal, hanya
    mengecek izin dan keberadaan. Di Windows `signal 0` TIDAK didukung
    dan melempar `WinError 87` — jadi memakainya membuat lock selalu
    dianggap basi, dan dua instance tetap bisa jalan bersamaan. Itu
    kegagalan yang justru membuka celah yang seharusnya ditutup.

    Di Windows dipakai `OpenProcess` + `GetExitCodeProcess` lewat
    ctypes: `STILL_ACTIVE` (259) berarti prosesnya hidup.

    `EPERM` di POSIX berarti proses ADA tapi milik user lain — itu
    tetap hidup, dan tetap harus dihitung sebagai lock yang sah.
    """
    if pid <= 0:
        return False

    if os.name == "nt":
        return _pid_alive_windows(pid)

    try:
        os.kill(pid, 0)
    except OSError as exc:
        if exc.errno == errno.ESRCH:
            return False
        if exc.errno == errno.EPERM:
            return True
        raise
    return True


def _pid_alive_windows(pid: int) -> bool:
    """Cek proses hidup di Windows lewat OpenProcess."""
    import ctypes
    from ctypes import wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION,
                                  False, pid)
    if not handle:
        # ERROR_INVALID_PARAMETER (87) = PID tidak ada.
        # ERROR_ACCESS_DENIED (5) = proses ADA tapi milik user lain.
        err = ctypes.get_last_error()
        if err == 5:
            return True
        return False
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def read_lock_pid(path: Path) -> Optional[int]:
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw:
        return None
    try:
        return int(raw.split()[0])
    except (ValueError, IndexError):
        return None


class SingleInstanceLock:
    """
    Kunci eksklusif untuk satu akun.

    `acquire()` mengembalikan True kalau kunci didapat. Kalau gagal, ia
    sudah menulis alasannya di `self.blocked_reason` — pesan itu harus
    sampai ke operator, karena "bot tidak jalan" tanpa alasan adalah
    kegagalan yang paling sulit didiagnosis.
    """

    def __init__(self, account: str):
        self.account = account
        self.path = lock_path_for(account)
        self.blocked_reason: Optional[str] = None
        self._held = False

    def acquire(self) -> bool:
        if self._held:
            return True

        # `O_CREAT | O_EXCL` bersifat atomik: dua proses yang berlomba
        # hanya SATU yang berhasil membuat file. Momentum "cek lalu buat"
        # membiarkan keduanya lullai yakin mereka memegang kunci.
        for _ in range(2):
            try:
                fd = os.open(str(self.path),
                             os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                pid = read_lock_pid(self.path)
                if pid is not None and _pid_alive(pid):
                    self.blocked_reason = (
                        "Instance bot lain sedang berjalan untuk akun ini "
                        "(PID %d, lock: %s). Dua proses yang mengirim order "
                        "ke akun yang sama berarti dua eksposur, dan kill "
                        "switch hanya ada di salah satunya. Stop instance "
                        "PID %d dulu." % (pid, self.path, pid))
                    return False
                # Lock BASI: PID tercatat sudah mati. Ambil alih, tapi
                # sebutkan supaya pola ini terlihat.
                try:
                    os.remove(str(self.path))
                except OSError:
                    pass
                continue
            else:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write("%d\n" % os.getpid())
                self._held = True
                return True

        self.blocked_reason = (
            "Tidak bisa mengambil lock akun di %s setelah mencoba "
            "mengambil alih lock basi. Periksa izin tulis folder itu."
            % self.path)
        return False

    def release(self) -> None:
        """
        Lepas kunci — HANYA kalau PID di dalamnya milik kita.

        Melepas lock milik proses lain akan membuat proses itu kehilangan
        penjaga akunnya. Pemeriksaan PID itu yang membuat `release()`
        aman dipanggil dari jalur error.
        """
        if not self._held:
            return
        pid = read_lock_pid(self.path)
        if pid is not None and pid != os.getpid():
            self._held = False
            return
        try:
            os.remove(str(self.path))
        except OSError:
            pass
        self._held = False

    def __enter__(self):
        if not self.acquire():
            raise RuntimeError(self.blocked_reason)
        return self

    def __exit__(self, *exc):
        self.release()
        return False