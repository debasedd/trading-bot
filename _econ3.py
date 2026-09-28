"""
_econ3.py — Distribusi kerugian dengan persentil yang BENAR.

Versi sebelumnya salah melabeli: daftar diurutkan menaik, jadi indeks
terakhir adalah yang PALING sedikit ruginya, bukan terdalam. Angka yang
cetak lalu dihitung ulang di sini dengan definisi eksplisit.
"""
import os
import sqlite3

from core.config import get_config

cfg = get_config()
sc = cfg.scalping
rt = cfg.fees.taker * 2
sl_net_pct = (sc.tight_sl_pct + rt) * 100.0
lev = cfg.risk.default_leverage
sl_net_margin = sl_net_pct * lev

lines = []
lines.append("=== KONVERSI KE MARGIN ===")
lines.append("leverage default       {}x".format(lev))
lines.append("SL net (harga)         {:+.2f}%".format(-sl_net_pct))
lines.append("SL net (dari margin)   {:+.2f}%".format(-sl_net_margin))
lines.append("")

path = "data_store/trading_bot.db"
conn = sqlite3.connect("file:{}?mode=ro".format(path), uri=True)
rows = conn.execute(
    "SELECT realized_pnl, close_reason, margin, leverage "
    "FROM positions WHERE status='CLOSED' AND realized_pnl IS NOT NULL"
).fetchall()
conn.close()


def pct(sorted_vals, q):
    """Persentil q (0..1) dari daftar yang sudah terurut menaik."""
    if not sorted_vals:
        return None
    i = int(round(q * (len(sorted_vals) - 1)))
    return sorted_vals[i]


for reason in ("SL_HIT", "SCALP_EXPIRED", "SCALP_TP"):
    sel = [r for r in rows if (r[1] or "") == reason]
    if not sel:
        continue
    lines.append("=== {} ({} posisi) ===".format(reason, len(sel)))

    loss_pct = sorted(-r[0] / r[2] * 100.0 for r in sel
                      if r[0] < 0 and r[2])   # positif = besar ruginya
    if loss_pct:
        lines.append("  distribusi kerugian, sebagai % dari margin:")
        lines.append("    p10 (paling kecil)  {:6.2f}%".format(
            pct(loss_pct, 0.10)))
        lines.append("    median             {:6.2f}%".format(
            pct(loss_pct, 0.50)))
        lines.append("    p90                {:6.2f}%".format(
            pct(loss_pct, 0.90)))
        lines.append("    TERDALAM           {:6.2f}%".format(loss_pct[-1]))
        lines.append("    SL net direncanakan {:6.2f}%".format(
            sl_net_margin * (lev / float(lev))))
        deeper = [x for x in loss_pct if x > sl_net_margin]
        lines.append("    posisi yang MELAMPAUI SL: {} dari {} ({:.0%})"
                     .format(len(deeper), len(loss_pct),
                             len(deeper) / len(loss_pct)))
    lines.append("")

lines.append("=== RINGKASAN ===")
all_loss = sorted(-r[0] / r[2] * 100.0 for r in rows
                  if r[0] < 0 and r[2])
all_win = sorted(r[0] / r[2] * 100.0 for r in rows
                 if r[0] > 0 and r[2])
lines.append("median rugi  {:+.2f}% dari margin".format(
    pct(all_loss, 0.50)))
lines.append("median menang {:+.2f}% dari margin".format(
    pct(all_win, 0.50)))
lines.append("rasio median  {:.2f}:1".format(
    pct(all_win, 0.50) / pct(all_loss, 0.50)))

with open("_econ3.txt", "w", encoding="utf-8") as fh:
    fh.write("\n".join(lines) + "\n")
