"""
constellation_design.py — DESAIN + PENCARIAN koordinat untuk graf konstelasi.

Strategi: PRUNE RADIKAL. Tujuh sisi, semuanya benar, semuanyaalingkup ke satu
simpul analisis. Pita token tidak punya satu pun sinar radial; ia ditandai
dengan satu garis batas (containment), yang BUKAN sisi.

TOPOLOGI (7 sisi, semua dibuktikan dari kode yang berjalan):

  SENTIMENT      -> RF_MODEL        news_agent publish NEWS_SENTIMENT
  MACRO_FRED     -> RF_MODEL        analysis_agent.update_macro -> konteks
  TECH_TA        -> RF_MODEL        pandas-ta -> fitur
  CORE_ENGINE    -> RF_MODEL        l2Book -> analisis
  BINANCE_FEED   -> RF_MODEL        allMids -> analisis
  RF_MODEL       -> DECISION_GATE   publish MARKET_ANALYSIS
  DECISION_GATE  -> EXECUTION_AGENT publish TRADE_DECISION

YF_FALLBACK dan COINGECKO TIDAK punya sisi. Keduanya adalah TIER FALLBACK dari
sumber harga yang sama (data/price_feed.py:378-383, cascade `if not ticker`),
bukan tahap pipeline. Menggambar它们 berantai berarti mengarang relasi.

BENTUK: lima sumber di baris 1 mengembang (fan-in) ke satu hub di baris 2,
lalu rantai ke kanan di baris 3. Fan-in ke SATU simpul berarti tidak ada
dua sisi yang saling berpotongan di dalam koridor: sisi yang lebih dalam
selalu berada di dalam sisi yang lebih luar.
"""
from __future__ import annotations

import itertools
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import data_store.constellation_search as cs   # noqa: E402

XR = (0.0, 22.0)
YR = (0.0, 11.5)

# (key, label, is_token, ring) -> label mapping
SYSTEM_ROW1 = ["SENTIMENT", "MACRO_FRED", "TECH_TA", "CORE_ENGINE",
               "BINANCE_FEED"]
SYSTEM_ROW3 = ["YF_FALLBACK", "COINGECKO", "DECISION_GATE", "EXECUTION_AGENT"]

EDGES = [
    ("SENTIMENT", "RF_MODEL"),
    ("MACRO_FRED", "RF_MODEL"),
    ("TECH_TA", "RF_MODEL"),
    ("CORE_ENGINE", "RF_MODEL"),
    ("BINANCE_FEED", "RF_MODEL"),
    ("RF_MODEL", "DECISION_GATE"),
    ("DECISION_GATE", "EXECUTION_AGENT"),
]

# label yang dipakai di layar
LABEL = {
    "CORE_ENGINE": "SIGNAL", "DECISION_GATE": "RISK", "RF_MODEL": "RF ML",
    "SENTIMENT": "SENT", "BINANCE_FEED": "FLOW", "MACRO_FRED": "MACRO",
    "TECH_TA": "TECH", "EXECUTION_AGENT": "EXEC", "YF_FALLBACK": "YFI",
    "COINGECKO": "COIN",
}

TOKEN_LABELS = ["BTC", "ETH", "SOL", "XRP", "BNB", "DOGE",
                "NEAR", "HYPE", "ZEC", "ONDO", "ENA", "XPL"]

# semua label memakai arah yang sama: sistem di ATAS, token di BAWAH,
# KECUALI hub RF_MODEL yang menerima sisi dari atas dan melepas ke kanan,
# jadi labelnya HARUS di bawah supaya tidak menutup koridor fan-in.
TEXTPOS = {k: ("bottom center" if k == "RF_MODEL" else "top center")
           for k in LABEL}

# radius marker terburuk (30px) untuk semua simpul:设计上 search selalu
# worst case supaya layout aman untuk confidence 1.0 + pulse 1.0.
R_WORST = cs.MAX_R


def build(sys_x1, sys_x3, xs_token, y, token_top_label_above=False):
    """y = (y_row1, y_row2, y_row3, y_tok1, y_tok2) dalam unit."""
    y1, y2, y3, t1, t2 = y
    pos = {}
    for k, x in zip(SYSTEM_ROW1, sys_x1):
        pos[k] = (x, y1)
    pos["RF_MODEL"] = (sys_x1[2], y2)
    for k, x in zip(SYSTEM_ROW3, sys_x3):
        pos[k] = (x, y3)
    # xs_token = 6 kolom; baris atas t1, baris bawah t2. Baris bawah
    # digeser +pitch/2 supaya TIDAK memakai kolom yang sama dengan baris
    # atas -- kolom kembar membuat label bawah menabrak label atas.
    off = (xs_token[1] - xs_token[0]) / 2.0
    for i, t in enumerate(TOKEN_LABELS):
        col, row = i % 6, i // 6
        x = xs_token[col] + (off if row == 1 else 0.0)
        if x > 20.4:
            x = xs_token[col] - off
        pos[t] = (x, t1 if row == 0 else t2)

    layout = {}
    for k in LABEL:
        layout[LABEL[k]] = (pos[k][0], pos[k][1], LABEL[k],
                            TEXTPOS[k] != 'top center', R_WORST)
    for t in TOKEN_LABELS:
        layout[t] = (pos[t][0], pos[t][1], t, True, R_WORST)
    # EDGES memakai KUNCI INTERNAL, layout memakai LABEL. Petakan sekali di
    # sini supaya evaluate() tidak pernah melihat dua kosmetik nama berbeda
    # untuk simpul yang sama.
    edges = [(LABEL[a], LABEL[b]) for a, b in EDGES]
    return layout, edges, pos


def search():
    """Cari y (dan x sistem) yang memenuhi SEMUA syarat pada semua lebar."""
    ppx_nom, ppy = cs.px_per_unit(XR, YR)
    # lebar panel yang harus dilayani: 1920 (utama) sampai 1280 (android)
    widths = [462.5, 432.5, 402.5, 372.5, 342.5, 312.5, 302.5]
    best = None
    # grid y
    for y1 in [10.9, 11.0, 11.1, 11.2]:
        for d12 in [2.0, 2.2, 2.4, 2.6]:
            y2 = y1 - d12
            for d23 in [2.0, 2.2, 2.4, 2.6, 2.8]:
                y3 = y2 - d23
                for d34 in [1.4, 1.6, 1.8, 2.0]:
                    t1 = y3 - d34
                    for d45 in [1.8, 2.0, 2.2, 2.4, 2.6]:
                        t2 = t1 - d45
                        if t2 < 1.9:
                            continue
                        # lebar label token: 6 kolom harus muat
                        for pitch in [3.4, 3.6, 3.8]:
                            xs_t = [2.0 + i * pitch for i in range(6)]
                            if xs_t[-1] > 20.4:
                                continue
                            sys_x1 = [2.2 + i * 4.3 for i in range(5)]
                            sys_x3 = [sys_x1[0], sys_x1[1], sys_x1[3],
                                      sys_x1[4]]
                            lay, eds, _ = build(sys_x1, sys_x3, xs_t,
                                                (y1, y2, y3, t1, t2))
                            worst = None
                            ok = True
                            for w in widths:
                                pw = w * (cs.PLOT_W / 462.5)
                                r = cs.evaluate(lay, eds, XR, YR, panel_w=pw)
                                if r["BAD"]:
                                    ok = False
                                    break
                                score = (r["worst_label_gap"]
                                         + r["worst_marker_gap"])
                                if worst is None or score < worst[0]:
                                    worst = (score, w, r)
                            if ok and worst:
                                cand = (worst[0], y1, d12, d23, d34, d45,
                                        pitch, worst[1], worst[2])
                                if best is None or cand[0] > best[0]:
                                    best = cand
    return best


if __name__ == "__main__":
    b = search()
    if not b:
        print("TIDAK ADA KANDIDAT yang memenuhi syarat")
        raise SystemExit(1)
    _, y1, d12, d23, d34, d45, pitch, w, r = b
    y2, y3 = y1 - d12, y1 - d12 - d23
    t1, t2 = y3 - d34, y3 - d34 - d45
    sys_x1 = [2.2 + i * 4.3 for i in range(5)]
    sys_x3 = [sys_x1[0], sys_x1[1], sys_x1[3], sys_x1[4]]
    xs_t = [2.0 + i * pitch for i in range(6)]
    print("y1=%.3f d12=%.3f d23=%.3f d34=%.3f d45=%.3f tokenpitch=%.3f"
          % (y1, d12, d23, d34, d45, pitch))
    print("sys_x1", sys_x1)
    print("sys_x3", sys_x3)
    print("xs_t", [round(v, 3) for v in xs_t])
    print("y =", [round(v, 3) for v in (y1, y2, y3, t1, t2)])
    print("worst-width report", w)
    print(json.dumps(r, indent=1))
