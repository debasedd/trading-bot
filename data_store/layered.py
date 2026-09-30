"""
layered.py — kandidat "layered flow" untuk graf konstelasi.

Bentuknya corong, bukan rantai:

    baris 1  enam SUMBER      SIGNAL FLOW YFI COIN MACRO SENT
    baris 2  tiga KONSUMEN    TECH  RF ML   RISK
    baris 3  satu HUB         EXEC
    baris 4  dua belas TOKEN, satu baris

Semua sisi di pita sistem TURUN ke kanan/kiri, tidak pernah kembali ke
atas. Sisi ke token berporos di EXEC dan shaped sebagai "bus": tiap sisi
menyimpang horizontal di ketinggian sendiri, lalu turun VERTIKAL di kolom
tokennya sendiri. Kenaikan `+ k*|dx|` pada ketinggian bus itulah yang
membuatkipas itu tidak pernah saling berpotongan — lihat `bus_y`.

Kenaikan monotonik terhadap |dx| itu penting, bukan gaya. Untuk dua token dengan
x_a < x_b < x_exec: tinggi bus_a > tinggi bus_b. Bus_a membentang di
[x_a, x_exec] pada y yang LEBIH TINGGI dari puncak bus_b, sedangkan
drop vertikal token b dimulai dari bus_b. Jadi bus_a selalu melewati di
ATAS seluruh drop yang-nya, dan drop b tidak pernah sampai setinggi
bus_a. Tidak ada persilangan, secara pembuktian, bukan harapan.

Pita token cukup tightened ke SATU baris karena tidak ada lagi baris di
bawahnya yang harus dilewati.
"""


import os
# --------------------------------------------------------------------------
# Pita sistem
# --------------------------------------------------------------------------
SYSTEM_NODES = {
    # Baris 1 — sumber. Empat harga (satu cascade dengan tiga tingkat) dan
    # dua fitur. Keduanya BENAR-BENAR jenis berbeda, jadi tidak dicampur.
    "CORE_ENGINE":    (1.60,  10.00, "SIGNAL", "#000000", "Hyperliquid L2 Book"),
    "BINANCE_FEED":   (5.00,  10.00, "FLOW",   "#c27800", "All Mids Stream (WS)"),
    "YF_FALLBACK":    (8.40,  10.00, "YFI",    "#555042", "Price Tier 1 · yfinance"),
    "COINGECKO":      (11.80, 10.00, "COIN",   "#555042", "Price Tier 2 · CoinGecko"),
    "MACRO_FRED":     (15.20, 10.00, "MACRO",  "#555042", "CPI / Yield / FFR"),
    "SENTIMENT":      (18.60, 10.00, "SENT",   "#c27800", "VADER + FinBERT"),
    # Baris 2 — konsumen. Masing-masing benar-benar ada di kodenya.
    "TECH_TA":        (5.00,   7.20, "TECH",   "#0077b6", "pandas-ta (RSI/MACD/BB)"),
    "RF_MODEL":      (10.00,   7.20, "RF ML",  "#7209b7", "RandomForest · 7 fitur"),
    "DECISION_GATE": (13.60,   7.20, "RISK",   "#0077b6", "Risk Sizing Gate"),
    # Baris 3 — satu-satunya hub, langsung di bawah RISK.
    "EXECUTION_AGENT": (13.60, 4.90, "EXEC",   "#009933", "Paper Engine Exec"),
}

# Tiap sisi di sini bisa ditunjuk ke baris kodenya sendiri.
SYSTEM_EDGES = [
    # Empat sumber harga bertemu di TECH: pandas-ta memang konsumen
    # harga (data/price_feed.py -> analyze(df)). Satu cascade, satu garis
    # per sumber, tidak ada turunan yang dikarang.
    ("CORE_ENGINE",   "TECH_TA"),
    ("BINANCE_FEED",  "TECH_TA"),
    ("YF_FALLBACK",   "TECH_TA"),
    ("COINGECKO",     "TECH_TA"),
    # Dua sumber fitur bertemu di model, bersama hasil TA.
    # SENT -> RF ML bukan tahap sesudah RF: sentiment_score adalah salah
    # satu dari 7 fitur (analysis/ml_signals.py:39).
    ("MACRO_FRED",    "RF_MODEL"),
    ("SENTIMENT",     "RF_MODEL"),
    ("TECH_TA",       "RF_MODEL"),
    # publish/subscribe yang nyata.
    ("RF_MODEL",      "DECISION_GATE"),   # MARKET_ANALYSIS
    ("DECISION_GATE", "EXECUTION_AGENT"),  # TRADE_DECISION
]

# YF_FALLBACK dan COINGECKO TIDAK dapat sisi ke hilir. Keduanya adalah
# TINGKAT fallback dari satu sumber harga (data/price_feed.py:378-383,
# cascade `if not ticker`), bukan tahap pipeline. Memberi sisi dari
# mereka ke simpul lain berarti mengarang relasi yang tidak ada.

HUB = "EXECUTION_AGENT"

# --------------------------------------------------------------------------
# Pita token — SATU baris, pitch 1.70 unit (33.6px).
#
# Pitch ini hasil membagi lebar plot, bukan tebakan. Empat huruf ("HYPE",
# "ONDO", "XPL", "NEAR") = 24px di font 10px mono. Butuh gap 8px, jadi
# pitch minimum 32px. 1.70 unit = 33.6px memberi 9.6px untuk yang
# terlebar dan lebih untuk yang tiga huruf.
# --------------------------------------------------------------------------
TOKEN_Y = 1.40
TOKEN_PITCH = 1.70
TOKEN_COUNT = 12
TOKEN_X0 = 1.65            # 11 * 1.70 = 18.7 unit, sisa 3.3 dibagi dua


def token_slots(pitch=TOKEN_PITCH, x0=TOKEN_X0, y=TOKEN_Y, n=TOKEN_COUNT):
    return [(x0 + pitch * k, y) for k in range(n)]


# Kenaikan bus per unit jarak horizontal. NIlainya Establishes urutan:
# token yang jauh punya bus lebih tinggi.
BUS_RISE = 0.060


def build():
    return (dict(SYSTEM_NODES), list(SYSTEM_EDGES), token_slots(),
            [0.0, 22.0], [0.0, 11.5])


def apply(hf):
    """
    Pasang kandidat ke modul hud_figures supaya bisa diukur.

    Mengubah ATRIBUT modul yang sudah di-import, jadi hasilnya terlihat di
    mana saja yang memakainya. Karena itu ada guard di bawah: tanpa itu,
    satu `apply(hud_figures)` yang terpeleset di skrip pengukuran akan
    mengubah geometri dashboard produksi tanpa jejak, dan tidak ada yang
    menghitung ulang sampai chart-nya terlihat salah.

    Guardian: modul target harus punya marker `LAYOUT_PRODUCTION = True`,
    yang hanya ada di `dashboard/layouts/hud_figures.py`. Salinan di
    `_snap/` dan modul hasil perluasan apa pun tidak punya marker itu,
    jadi pengukuran ke kandidat tetap bisa jalan tanpa membuka pintu ke
    produksi.

    Catatan: import modul INI sendiri tidak menyentuh apa pun - semua
    mutasi terjadi di dalam `apply()`. Itu terverifikasi lewat md5 file
    produksi sebelum dan sesudah import.
    """
    if getattr(hf, "LAYOUT_PRODUCTION", False) and not os.environ.get(
        "0XF3CE25_ALLOW_PRODUCTION_LAYOUT_PATCH"
    ):
        raise RuntimeError(
            "Menolak mengubah geometri dashboard produksi. Kandidat harus "
            "diukur terhadap salinan di data_store/_snap/, bukan modul "
            "produksi. Kalau ini memang yang kamu mau, set "
            "0XF3CE25_ALLOW_PRODUCTION_LAYOUT_PATCH=1 - dan rfap textures "
            "semua figure yang sudah dibangun akan memakai geometri baru."
        )
    nodes, edges, slots, xr, yr = build()
    hf.NEURAL_SYSTEM_NODES = dict(nodes)
    hf.NEURAL_SYSTEM_EDGES = list(edges)
    hf.TOKEN_SLOTS = list(slots)
    hf.TOKEN_ANCHORS = hf.TOKEN_SLOTS
    hf.HUB = HUB
    hf.BUS_RISE = BUS_RISE
    hf._TOKEN_SLOT_REGISTRY = _sequential_registry(hf)
    hf._X_RANGE, hf._Y_RANGE = xr, yr
    return hf


def _sequential_registry(hf):
    """Slot berurutan, bukan hasil hash, supaya yang diukur geometri yang
    benar-benar tampil."""
    n = len(hf.TOKEN_SLOTS)
    reg = {}
    for i, sym in enumerate(hf._TOKEN_UNIVERSE[:n]):
        reg[sym] = i
    return reg


if __name__ == "__main__":
    nodes, edges, slots, xr, yr = build()
    hub = nodes[HUB]
    print(f"nodes {len(nodes)}  system edges {len(edges)}  slots {len(slots)}")
    print(f"hub {hub[2]} at ({hub[0]}, {hub[1]})")
    print("token x:", [round(s[0], 2) for s in slots])
    print("bus y :", [round(hub[1] + BUS_RISE * abs(s[0] - hub[0]), 2)
                      for s in slots])
