"""
_econ.py — Apakah win rate dan R:R yang
sebenarnya menghasilkan profit?

Config menyelesaikan win rate impas 41,2% sementara win rate aktual 49,7%.
Kalau begitu seharusnya untung. Kalau tidak, ada sesuatu yang tidak
sesuai asumsi dan itu harus diketahui, bukan diasumsikan.
"""
import glob
import os
import sqlite3

from core.config import get_config

cfg = get_config()
sc = cfg.scalping
fees = cfg.fees
rt = fees.taker * 2

lines = []
lines.append("=== KONFIGURASI EKONOMIS (asumsi teoretis) ===")
lines.append("TP mentah        {:+.2%}".format(sc.min_profit_pct))
lines.append("SL mentah        {:+.2%}".format(-sc.tight_sl_pct))
lines.append("fee roundtrip    {:+.2%}".format(-rt))
net_tp = sc.min_profit_pct - rt
net_sl = sc.tight_sl_pct + rt
lines.append("net TP           {:+.2%}".format(net_tp))
lines.append("net SL           {:+.2%}".format(-net_sl))
lines.append("R:R bersih       1:{:.2f}".format(net_tp / net_sl))
lines.append("win rate impas   {:.1%}".format(net_sl / (net_tp + net_sl)))
lines.append("")

path = "data_store/trading_bot.db"
if not os.path.exists(path):
    raise SystemExit("DB tidak ada")

conn = sqlite3.connect("file:{}?mode=ro".format(path), uri=True)
rows = conn.execute(
    "SELECT realized_pnl, close_reason FROM positions "
    "WHERE status='CLOSED' AND realized_pnl IS NOT NULL"
).fetchall()
conn.close()

wins = [p for p, _ in rows if p > 0]
losses = [p for p, _ in rows if p <= 0]

lines.append("=== DATA AKTUAL ({} posisi tertutup) ===".format(len(rows)))
lines.append("win rate         {:.1%}".format(len(wins) / len(rows)))
if wins:
    lines.append("rata-rata menang  {:+.4f} USDT".format(sum(wins) / len(wins)))
if losses:
    lines.append("rata-rata rugi   {:+.4f} USDT".format(sum(losses) / len(losses)))
lines.append("total PnL        {:+.2f} USDT".format(sum(p for p, _ in rows)))
lines.append("")

lines.append("=== KENAPA R:R TEORETIS TIDAK CUKUP ===")
if wins and losses:
    aw = sum(wins) / len(wins)
    al = -sum(losses) / len(losses)
    lines.append(" expectancy/trade {:+.4f} USDT".format(
        (len(wins) / len(rows)) * aw - (len(losses) / len(rows)) * al))
    lines.append(" rasio win:loss   {:.2f}:1".format(aw / al))
    lines.append(" R:R yang dibutuhkan {:.2f}".format(al / aw))
    lines.append("")
    lines.append(" -> Konfigurasi rusak kalau: TP jadi 0.60% tapi")
    lines.append("    rata-rata kerugian NYATA lebih besar dari rata-rata")
    lines.append("    keuntungan. Slippage dan exit-at-SL membuat")
    lines.append("    kerugian efektif lebih dalam dari SL yang direncanakan.")
    lines.append("    Loss tail = spread yang harus dilewati dua kali.")

lines.append("")
lines.append("=== SEBAB KELUAR ===")
from collections import Counter  # noqa: E402

reasons = Counter(r for _, r in rows)
for reason, n in reasons.most_common():
    sel = [p for p, r in rows if r == reason]
    total = sum(sel)
    lines.append("  {:<12} {:>4} posisi  win {:>5.1%}  PnL {:>9.2f}".format(
        str(reason or "-"), n, sum(1 for x in sel if x > 0) / max(1, n), total))

with open("_econ.txt", "w", encoding="utf-8") as fh:
    fh.write("\n".join(lines) + "\n")
