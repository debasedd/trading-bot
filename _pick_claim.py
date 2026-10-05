"""
_pick_claim.py — memilih butir bukti cabutan secara ACAK lewat seed dari berkas.

Seed dibaca dari docs/reports/seed-bukti-cabutan.txt (dibuat oleh
_seed_pick.py), bukan diketik. Baris yang dicetak adalah perintah yang
benar-benar dipakai, supaya выбор acaknya bisa diulang.

Pakai:
    python _pick_claim.py
"""

import io
import sys

SEED_FILE = "docs/reports/seed-bukti-cabutan.txt"

CLAIMS = [
    "1. validUntil: pagar kewajaran (client.py)",
    "2. agent margin kedaluwarsa 3600 (client.py)",
    "3. collateral spot total ATAU hold (client.py)",
    "4. UnverifiedTracker: 1 gagal -> jeda order (engine.py)",
    "5. UnverifiedTracker: 3 gagal -> kill switch (engine.py)",
    "6. state kill switch selalu dibaca dari disk (safety.py)",
    "7. disengage_kill_switch tidak punya pemanggil (safety.py)",
    "8. single-instance lock: lock kedua ditolak (single_instance.py)",
    "9. release-kill-switch menolak saat bot hidup (run.py)",
    "10. partial fill dicatat sebagai qty order (executor.py)",
    "11. closing resting dilaporkan sukses (executor.py)",
    "12. fill CLOSE menutup baris lokal (executor.py)",
    "13. env tidak bisa melepas kill switch (safety.py)",
    "14. spot tidak terbaca -> None bukan False (client.py)",
]


def read_seeds(path):
    out = []
    for line in io.open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(chr(9))
        if len(parts) == 2 and parts[1]:
            out.append(parts[1])
    return out


def pick(seed, n=3):
    # Deterministik terhadap seed: seed yang sama -> butir yang sama.
    h = 0
    for ch in seed:
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    pool = list(CLAIMS)
    out = []
    for _ in range(min(n, len(pool))):
        h = (h * 1103515245 + 12345) & 0x7FFFFFFF
        out.append(pool[h % len(pool)])
        pool.remove(out[-1])
    return out


if __name__ == "__main__":
    seeds = read_seeds(SEED_FILE)
    if not seeds:
        print("tidak ada seed; jalankan _seed_pick.py dulu", file=sys.stderr)
        sys.exit(1)
    print("seed dari %s:" % SEED_FILE)
    for s in seeds:
        print("  %s -> %s" % (s, "; ".join(pick(s))))
