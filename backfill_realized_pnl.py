"""
_backfill_fix.py — Perbaiki `realized_pnl` historis.

`realized_pnl` pernah hanya mengurangi fee PENUTUPAN, padahal fee PEMBUKAAN
juga dipotong dari saldo saat posisi dibuka. Akibatnya:

  * win rate menghitung posisi yang rugi bersih sebagai "menang"
  * daily-loss breaker UNDER-estimasi kerugian sehingga menyala terlambat
  * laporan PnL_total lebih besar dari kenyataan

Skrip ini menghitung ulang `realized_pnl` dari data yang sama persis
dengan logika produksi saat ini, lalu menulisnya dalam SATU transaksi.

Keamanan:
  * Database dicadangkan penuh sebelum perubahan
  * Run pertama hanya MENAMPILKAN; penulisan perlu --apply
  * Semua dihitung dari `trades`, bukan dari harga yang bisa berubah
"""
import shutil
import sqlite3
import sys
import time

DB = "data_store/trading_bot.db"

# Logika yang sama dengan `PositionManager.close_position`.
# Catatan: nilai ini harus identik dengan kode produksi. Kalau produksi
# berubah, skrip ini harus ikut berubah.
Q_SELECT = """
SELECT p.id,
       p.realized_pnl,
       p.side,
       p.entry_price,
       p.close_price,
       p.quantity,
       COALESCE((SELECT SUM(fee) FROM trades
                 WHERE position_id = p.id AND trade_type = 'OPEN'), 0.0) AS open_fee,
       COALESCE((SELECT SUM(fee) FROM trades
                 WHERE position_id = p.id AND trade_type = 'CLOSE'), 0.0) AS close_fee
FROM positions p
WHERE p.status = 'CLOSED'
  AND p.close_price IS NOT NULL
  AND p.realized_pnl IS NOT NULL
"""


def net_pnl(side, entry, close, qty, open_fee, close_fee):
    """PnL bersih = bruto - fee buka - fee tutup."""
    gross = (close - entry) * qty if side == "LONG" else (entry - close) * qty
    return round(gross - open_fee - close_fee, 8)


def main() -> int:
    apply = "--apply" in sys.argv
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    rows = conn.execute(Q_SELECT).fetchall()
    changed = []
    for r in rows:
        correct = net_pnl(r["side"], r["entry_price"], r["close_price"],
                          r["quantity"], r["open_fee"], r["close_fee"])
        old = r["realized_pnl"]
        if abs(correct - old) > 1e-6:
            changed.append((r["id"], old, correct))

    delta = sum(c - o for _, o, c in changed)
    print("posisi diperiksa      :", len(rows))
    print("nilai yang perlu dikoreksi:", len(changed))
    print("selisih total         : {:+.2f} USDT".format(delta))
    print()
    print("contoh 10 perubahan pertama:")
    for pid, old, new in changed[:10]:
        print("  pos {:>4}  {:+9.4f} -> {:+9.4f}  ({:+.4f})".format(
            pid, old, new, new - old))

    if not apply:
        print()
        print("Mode laporan. Jalankan ulang dengan --apply untuk menulis.")
        conn.close()
        return 0

    if not changed:
        print()
        print("Tidak ada yang perlu diperbaiki.")
        conn.close()
        return 0

    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = "data_store/trading_bot.db.backup-" + stamp
    conn.close()
    shutil.copy2(DB, backup)
    print()
    print("cadangan dibuat:", backup)

    conn = sqlite3.connect(DB)
    try:
        with conn:  # SATU transaksi
            conn.executemany(
                "UPDATE positions SET realized_pnl = ? WHERE id = ?",
                [(new, pid) for pid, _old, new in changed],
            )
    finally:
        conn.close()

    print("baris diperbarui:", len(changed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
