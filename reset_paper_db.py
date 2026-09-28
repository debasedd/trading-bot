"""
reset_paper_db.py — Reset database paper trading ke kondisi awal (saldo 10000 USDT).
"""
import asyncio
from database.db import get_db


async def reset():
    db = await get_db()
    await db.execute("DELETE FROM trades")
    await db.execute("DELETE FROM positions")
    await db.execute("DELETE FROM agent_logs")
    await db.execute("DELETE FROM signals")
    # Baris akun terbaru, bukan id = 1. Setelah reset sebelumnya, baris baru
    # tidak lagi memakai id 1 sehingga `WHERE id = 1` tidak mengubah apa pun
    # dan saldo tetap di angka hasil trading terakhir.
    await db.execute("""
        UPDATE account SET
            balance = 10000.0,
            initial_balance = 10000.0,
            total_pnl = 0.0,
            total_trades = 0,
            winning_trades = 0,
            losing_trades = 0,
            max_drawdown = 0.0,
            peak_balance = 10000.0,
            profit_factor = 0.0
        WHERE id = (SELECT MAX(id) FROM account)
    """)
    await db.commit()
    await db.close()
    print("Database paper trading berhasil direset ke saldo awal 10,000 USDT.")


if __name__ == "__main__":
    asyncio.run(reset())
