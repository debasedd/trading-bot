"""
test_live_session.py — Menjalankan bot paper trading secara terisolasi selama durasi tertentu
lalu menampilkan hasil eksekusi dan PnL.
"""

import asyncio
import sys
from run import TradingBotApp
from core.logger import setup_logger, get_logger
from database.db import get_db

logger = get_logger("test_live_session")


async def run_test(duration_seconds: int = 90):
    setup_logger()
    app = TradingBotApp()

    # Jangan jalankan thread dashboard untuk testing ini agar bersih
    app.start_dashboard = lambda: None

    print(f"\n==========================================")
    print(f" Memulai live paper testing selama {duration_seconds} detik...")
    print(f"==========================================\n")

    await app.initialize()

    # Jalankan background run
    bot_task = asyncio.create_task(app.run())

    # Tunggu sesuai durasi
    try:
        await asyncio.sleep(duration_seconds)
    except KeyboardInterrupt:
        print("\nDihentikan manual.")
    finally:
        print("\nMenghentikan bot...")
        await app.shutdown()
        bot_task.cancel()
        try:
            await bot_task
        except asyncio.CancelledError:
            pass

    # Ambil statistik hasil trade dari DB.
    # Akun diambil dari baris dengan id terbesar, BUKAN id = 1. Setelah reset,
    # `sqlite_sequence` dibersihkan sehingga baris baru tidak lagi memakai id 1
    # — query `WHERE id = 1` lalu mengembalikan None dan seluruh laporan
    # dilewati diam-diam.
    db = await get_db()
    account_row = await db.fetchone("SELECT * FROM account ORDER BY id DESC LIMIT 1")
    account = dict(account_row) if account_row else None
    trades = [dict(r) for r in await db.fetchall("SELECT * FROM trades ORDER BY id DESC LIMIT 20")]
    positions = [dict(r) for r in await db.fetchall("SELECT * FROM positions ORDER BY id DESC LIMIT 20")]

    print("\n================ HASIL TESTING ================")
    if account:
        open_positions = [p for p in positions if p.get('status') == 'OPEN']
        open_margin = sum(p.get('margin', 0) for p in open_positions)
        wallet_balance = account['balance'] + open_margin
        upnl = sum(p.get('unrealized_pnl', 0) for p in open_positions)
        equity = wallet_balance + upnl
        total_pnl = equity - account['initial_balance']

        print(f"Saldo Dompet     : {wallet_balance:.2f} USDT")
        print(f"Margin Terkunci  : {open_margin:.2f} USDT ({len(open_positions)} posisi terbuka)")
        print(f"Saldo Kas Bebas  : {account['balance']:.2f} USDT")
        print(f"Total Equity     : {equity:.2f} USDT")
        print(f"Total PnL        : {total_pnl:+.2f} USDT (Realized: {account['total_pnl']:+.2f} USDT, Floating: {upnl:+.2f} USDT)")
        print(f"Total Trade      : {account['total_trades']}")
        print(f"Winning Trades   : {account['winning_trades']}")
        print(f"Losing Trades    : {account['losing_trades']}")
        print(f"Profit Factor    : {account['profit_factor']:.2f}")

    print("\n--- 10 Posisi Terakhir ---")
    for p in positions[:10]:
        pnl_val = p.get('realized_pnl')
        pnl_str = f"{pnl_val:+.4f}" if pnl_val is not None else "OPEN"
        print(f"#{p['id']} {p['side']} {p['symbol']} | Entry: {p['entry_price']} | Close: {p.get('close_price')} | PnL: {pnl_str} | Status: {p['status']} | Alasan: {p.get('close_reason')}")

    print("===============================================\n")


if __name__ == "__main__":
    dur = int(sys.argv[1]) if len(sys.argv) > 1 else 90
    asyncio.run(run_test(dur))
