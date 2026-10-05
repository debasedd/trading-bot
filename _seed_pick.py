"""
_seed_pick.py — MEMILIH seed acak dan menulisnya ke berkas, bukan diketik.

Semua angka acak di laporan verifikasi HARUS berasal dari berkas ini.
Seed yang diketik manual bisa "benar" tanpa pernah dijalankan, jadi
klaimnya tidak bisa diaudit.

Pakai:
    python _seed_pick.py <jumlah> <nama_berkas>

Contoh:
    python _seed_pick.py 3 docs/reports/seed-bukti-cabutan.txt

Isi berkas: seed yang dipakai + waktu + perintah yang memakainya,
supaya angka di laporan bisa ditelusuri ke sumbernya.
"""

import io
import os
import sys
import time
from datetime import datetime, timezone


def main() -> int:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    path = sys.argv[2] if len(sys.argv) > 2 else "seed-pick.txt"
    seeds = [os.urandom(4).hex().upper() for _ in range(n)]
    stamp = datetime.now(timezone.utc).isoformat()

    lines = [
        "# seed acak — dibuat oleh _seed_pick.py, BUKAN diketik manual",
        "# dibuat: %s" % stamp,
        "# jumlah: %d" % n,
        "",
    ]
    for i, s in enumerate(seeds, 1):
        lines.append("%d\t%s" % (i, s))
    lines += ["", "# perintah yang mengproduksinya:",
              "#   python %s %d %s" % (sys.argv[0], n, path)]

    io.open(path, "w", encoding="utf-8", newline="\n").write(
        "\n".join(lines) + "\n")
    print("ditulis ke %s" % path)
    for i, s in enumerate(seeds, 1):
        print("%d\t%s" % (i, s))
    return 0


if __name__ == "__main__":
    sys.exit(main())
