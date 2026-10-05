"""
_seed_use.py — memakai seed yang SUDAH ditulis _seed_pick.py.

Seed tidak pernah diketik di sini dan tidak pernah diketik di laporan.
Skrip ini membacanya dari berkas, jadi pilihan acaknya bisa diulang:
orang lain menjalankan perintah yang sama dan mendapat item yang sama.
"""

import io
import sys


def read_seeds(path: str):
    out = []
    for line in io.open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) == 2 and parts[1]:
            out.append(parts[1])
    return out


if __name__ == "__main__":
    seeds = read_seeds(sys.argv[1])
    print("%d seed terbaca" % len(seeds))
    for s in seeds:
        print(s)
