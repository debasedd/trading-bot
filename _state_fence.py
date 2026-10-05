"""
_state_fence.py — pagar hash isi berkas data_store SEBELUM dan SESUDAH suite.

Untuk gerbang verifikasi. Yang di-hash ISI berkas .db dan .json, bukan
ukuran+mtime: dua berkas bisa sama besarnya dan berbeda isinya, dan
ukuran+mtime tidak menangkap itu.

Berkas -shm dan -wal SENGAJA DIABAIKAN: keduanya artefak SQLite yang
berubah karena proses lain yang membuka database, bukan karena test.
Termasukkannya membuat pagar selalu merah karena alasan yang tidak
berkaitan dengan pencemaran test.
"""

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path("data_store")
LOG = Path("logs") / "trading_bot.log"


def fence():
    out = {}
    if ROOT.exists():
        for p in sorted(ROOT.rglob("*")):
            if not p.is_file():
                continue
            if p.name.endswith("-shm") or p.name.endswith("-wal"):
                continue
            if p.suffix not in (".db", ".json"):
                continue
            try:
                out[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()
            except OSError as exc:
                out[str(p)] = "ERR:%s" % exc
    if LOG.exists():
        out["__log__"] = hashlib.sha256(LOG.read_bytes()).hexdigest()
    else:
        out["__log__"] = None
    return out


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "show"
    if mode == "before":
        Path(sys.argv[2]).write_text(
            json.dumps(fence(), indent=0, sort_keys=True), encoding="utf-8")
        print("fence SEBELAH ditulis: %d berkas" % len(fence()))
    elif mode == "after":
        before = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
        after = fence()
        baru = [k for k in after if k not in before]
        hilang = [k for k in before if k not in after]
        berubah = [k for k in after if k in before and after[k] != before[k]]
        print("fence SESUDAH: %d berkas" % len(after))
        print("BARU    : %d %s" % (len(baru), baru[:10]))
        print("HILANG : %d %s" % (len(hilang), hilang[:10]))
        print("BERUBAH: %d %s" % (len(berubah), berubah[:10]))
        sys.exit(1 if (baru or hilang or berubah) else 0)
    else:
        f = fence()
        print("%d berkas" % len(f))
        for k, v in sorted(f.items()):
            print("%s  %s" % ((v or "-")[:16], k))
