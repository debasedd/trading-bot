"""
_smoke_console.py — Menjalankan jalur interaktif konsol tanpa manusia.

Nilai yang diketik disuntikkan ke `input`/`getpass`, jadi seluruh alur
menu—banner, konfirmasi, pengembalian mode—benar-benar dieksekusi.

Tujuannya membuktikan satu hal saja: **tidak ada urutan ketikan yang bisa
mencapai live tanpa sengaja**. Kalau ada skenario yang berhasil menembus,
`expect` di bawah akan gagal dan skrip ini berhenti di situ.
"""
import builtins
import getpass
import logging
import os

logging.disable(logging.CRITICAL)
os.environ.pop("HYPERLIQUID_PRIVATE_KEY", None)

from core.config import LiveConfig  # noqa: E402
from trading.live.console import (  # noqa: E402
    CONFIRM_PHRASE, ask_mode, derive_address,
)

KEY = "0x" + "ab" * 32
ADDRESS = derive_address(KEY)
if not ADDRESS:
    raise SystemExit("test key tidak bisa diturunkan ke alamat")

CFG = LiveConfig()


def run_typed(keys, expect):
    """
    Suntikkan `keys` ke input, jalankan menu, dan cek mode hasilnya.

    Alasan: `input` dipanggil bergantian dari `getpass` untuk key dan dari
    `input` biasa untuk sisanya, jadi cukup membaca antrean yang sama.
    """
    queue = list(keys)
    reads = []

    def fake_input(prompt=""):
        reads.append(prompt)
        return queue.pop(0) if queue else ""

    def fake_getpass(prompt=""):
        reads.append(prompt)
        return queue.pop(0) if queue else ""

    real_input, real_getpass = builtins.input, getpass.getpass
    builtins.input, getpass.getpass = fake_input, fake_getpass
    try:
        decision = ask_mode(CFG)
    finally:
        builtins.input = real_input
        getpass.getpass = real_getpass

    got = decision.mode
    if got != expect:
        raise SystemExit(
            "GAGAL: diketik {!r} -> mode {!r}, harusnya {!r}".format(
                keys, got, expect)
        )
    print("  ok  {!r:<58} -> {}".format(keys, got))
    return decision


print("Alamat turunan dari test key: {}\n".format(ADDRESS))

print("Skenario yang HARUS ditolak:")

# Langsung jawab "3" tanpa/key/konfirmasi.
run_typed(["x", "3"], "paper")

# Key ada, tapi kalimat konfirmasi salah.
os.environ["HYPERLIQUID_PRIVATE_KEY"] = KEY
run_typed(["x", "2", "ya"], "paper")

# Konfirmasi benar, tapi operator mengetik alamat yang keliru.
run_typed(["x", "2", CONFIRM_PHRASE, "0x" + "00" * 20], "paper")

# Konfirmasi benar, alamat diketik TIDAK ada.
run_typed(["x", "2", CONFIRM_PHRASE, ""], "paper")

# Pilihan ngawur.
run_typed(["x", "9"], "paper")

# Banner batal.
os.environ.pop("HYPERLIQUID_PRIVATE_KEY", None)
run_typed(["x", "2"], "paper")

print("\nSkenario yang BOLEH hidup (hanya dengan lengkap):")
os.environ["HYPERLIQUID_PRIVATE_KEY"] = KEY
d = run_typed(["s", KEY, "2", CONFIRM_PHRASE, ADDRESS], "testnet")
assert d.address == ADDRESS, "alamat hasil harus cocok"
d = run_typed(["s", KEY, "3", CONFIRM_PHRASE, ADDRESS], "mainnet")
assert d.address == ADDRESS, "alamat hasil harus cocok"

print("\nSemua skenario berperilaku seperti yang diharapkan.")
