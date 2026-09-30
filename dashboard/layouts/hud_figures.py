"""
dashboard/layouts/hud_figures.py — Pembuat grafik Plotly khusus Retro Bloomberg HUD.
Menyediakan:
- create_mini_candlestick_fig: Lilin mini OHLCV dengan tema vintage parchment
- create_convergence_fig: Kurva difusi probabilitas Black-Scholes / CRR
- create_neural_net_fig: Graf konstelasi simpul agen 2D (ukuran simpul = confidence sinyal riil)
- create_equity_area_fig: Kurva area pertumbuhan equity bergaya retro
- create_mini_sparkline_fig: Sparkline ultra-kompak untuk PnL & Volume

Semua grafik memakai data riil. Bila data absen, ditampilkan empty-state eksplisit,
bukan deret sintetis atau angka hardcoded.
"""

#: Penanda bahwa ini modul geometri DASHBOARD PRODUKSI, bukan salinan
#: pengukuran. `data_store/layered.py::apply()` menolak mengubah modul yang
#: memakai marker ini kecuali env `0XF3CE25_ALLOW_PRODUCTION_LAYOUT_PATCH`
#: diset secara eksplisit.
#:
#: Alasannya bukan takhayul. `apply()` menulis ATRIBUT modul yang sudah
#: di-import, jadi hasilnya terlihat di mana saja yang memakainya - dan
#: tidak ada yang menghitung ulang figure yang sudah dibangun. Satu
#: `apply()` yang terpeleset di skrip pengukuran akan mengubah chart
#: produksi tanpa jejak.
LAYOUT_PRODUCTION = True

import math

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from dashboard.layouts import palette

def _hex_to_rgb(color: str) -> tuple:
    """Ubah '#rrggbb' ke (r, g, b) integer. Untuk token warna yang valid saja."""
    c = color.lstrip("#")
    return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16))


def with_alpha(color: str, alpha: float) -> str:
    """
    Turunkan token warna hex + alpha menjadi `rgba(...)`.

    Dipakai supaya intensitas garis dan isi tidak lagi ditulis sebagai literal.
    Kegunaannya nyata: kalau palet diubah, semua variannya ikut berubah.
    Token yang sudah berupa `var(...)` atau `rgba(...)` dikembalikan apa
    adanya — fungsi ini hanya menangani bentuk hex.
    """
    if not color.startswith("#"):
        return color
    r, g, b = _hex_to_rgb(color)
    return f"rgba({r}, {g}, {b}, {alpha:g})"


# Semua warna legacy ini berakar dari blok :root di style.css. Mode gelap
# menimpanya lewat --bg-app/--fg, jadi warna legacy HARUS berupa var() —
# kalau hex, dia membekukan tema dan membuat palet bercabang dua.
# Warna diambil dari `palette`, yang membaca `:root` di style.css.
#
# Nilai aslinya adalah "var(--bg-app)" dan friends. Plotly tidak bisa
# membaca variabel CSS -- kanvas menerima string, dan `var(--bg-app)`
# bukan warna. Plotly menerimanya tanpa error lalu menggambarnya kosong,
# jadi panelnya putih dan tidak nyambung dengan tema.
PARCHMENT_BG = palette.PAPER_BG
PARCHMENT_CARD = palette.CARD_BG
INK_BLACK = palette.INK
INK_MUTED = palette.INK_MUTED
GREEN_VINTAGE = palette.GREEN
RED_VINTAGE = palette.RED
AMBER_VINTAGE = palette.AMBER
BLUE_VINTAGE = palette.BLUE

# Varian transparan DITURUNKAN dari token di atas lewat `with_alpha()` — bukan
# ditulis sebagai literal rgba. Ini bukan soal gaya: kalau literal, mengubah
# GREEN_VINTAGE tidak akan mengubah garis yang memakainya, dan palet diam-diam
# bercabang dua.
GREEN_VINTAGE_WASH = with_alpha(GREEN_VINTAGE, 0.22)
BLUE_VINTAGE_DIM = with_alpha(BLUE_VINTAGE, 0.50)
INK_FAINT = with_alpha(INK_BLACK, 0.13)
INK_TRANSPARENT = with_alpha(INK_BLACK, 0.0)



def create_mini_candlestick_fig(df: pd.DataFrame, symbol: str) -> go.Figure:
    """Candlestick 1-minute TradingView-style: drag mouse untuk pan/geser mundur, tanpa kotak zoom abu-abu."""
    fig = go.Figure()

    def _empty_state(message="AWAITING 1M CANDLE INGEST"):
        fig.update_layout(
            template="none",
            paper_bgcolor=PARCHMENT_BG,
            plot_bgcolor=PARCHMENT_BG,
            margin=dict(l=4, r=42, t=4, b=16),
            showlegend=False,
            xaxis=dict(visible=False),
            yaxis=dict(visible=False),
            annotations=[dict(
                text=message,
                xref="paper", yref="paper", x=0.5, y=0.5,
                showarrow=False,
                font=dict(size=10, family="Courier New", color=INK_MUTED),
            )],
        )
        return fig

    if (df is None or df.empty or len(df) < 3
            or "open" not in df.columns or "timestamp" not in df.columns):
        # Empty-state jujur: tidak ada lilin = tidak ada chart. Tidak ada data sintetis.
        return _empty_state()

    sub_df = df.tail(120).copy() if len(df) > 120 else df.copy()

    # `timestamp` bisa arrive sebagai kolom string/epoch-ms dari beberapa pembaca
    # DB. Normalisasi ke datetime dulu supaya baris rusak terbuang, bukan meledak
    # dengan KeyError/AttributeError di tengah perakitan figure.
    if not pd.api.types.is_datetime64_any_dtype(sub_df["timestamp"]):
        try:
            sub_df["timestamp"] = pd.to_datetime(sub_df["timestamp"], unit="ms", errors="coerce")
        except (ValueError, TypeError):
            sub_df["timestamp"] = pd.to_datetime(sub_df["timestamp"], errors="coerce")
    sub_df = sub_df.dropna(subset=["timestamp", "open", "high", "low", "close"])
    if sub_df.empty or len(sub_df) < 3:
        return _empty_state("NO VALID CANDLES // CHECK TIMESTAMP UNIT")

    # Sumbu waktu dipetakan ke KATEGORI, bukan ke sumbu waktu kontinu.
    #
    # Alasannya ada dua, dan keduanya soal pan/zoom yang tiba-tiba balik ke
    # tampilan awal:
    #   1. Pada sumbu waktu kontinu, menit yang bolong meninggalkan celah dan
    #      satu-satunya cara menutupnya adalah `rangebreaks`. Isi `rangebreaks`
    #      berubah setiap kali ada menit baru yang kosong; setiap perubahan
    #      memaksa Plotly melakukan relayout dan itu membuang posisi yang
    #      sedang dipakai user.
    #     titik yang sama), jadi tidak pernah berpotongan.
    #      lilin selalu berdempetan tanpa perlu `rangebreaks` — dan tidak ada
    #      atribut yang berubah-ubah untuk memicu reset.
    #
    # OHLC-nya tetap murni dari pasar: yang berubah hanya cara sumbu-x
    # menempatkan titik, bukan nilainya.
    sub_df["_cat"] = sub_df["timestamp"].dt.strftime("%m-%d %H:%M")
    cat_order = sub_df["_cat"].tolist()

    # Trace 1: Candlestick 1M OHLC
    fig.add_trace(go.Candlestick(
        x=sub_df["_cat"],
        open=sub_df["open"],
        high=sub_df["high"],
        low=sub_df["low"],
        close=sub_df["close"],
        increasing_line_color=GREEN_VINTAGE,
        decreasing_line_color=RED_VINTAGE,
        increasing_fillcolor=GREEN_VINTAGE,
        decreasing_fillcolor=RED_VINTAGE,
        line_width=1.3,
        name="1M OHLC",
    ))

    # Trace 2: EMA 9 line
    if "ema_9" in sub_df.columns and not sub_df["ema_9"].isna().all():
        fig.add_trace(go.Scatter(
            x=sub_df["_cat"],
            y=sub_df["ema_9"],
            mode="lines",
            line=dict(color=AMBER_VINTAGE, width=1.2, dash="dot"),
            name="EMA 9",
            hoverinfo="skip",
        ))

    cur_p = float(sub_df["close"].iloc[-1]) if not sub_df.empty else 100.0

    # Fokus awal pada 30 lilin terakhir. Nilainya dipertahankan sama selama
    # jumlah lilin tidak berubah, sehingga uirevision mengenali figur sebagai
    # "tidak berubah" dan tidak menyentuh posisi pan/zoom user.
    x_range = None
    if len(sub_df) >= 30:
        x_range = [cat_order[-35], cat_order[-1]]

    fig.update_layout(
        template="none",
        dragmode="pan",
        paper_bgcolor=PARCHMENT_BG,
        plot_bgcolor=PARCHMENT_BG,
        margin=dict(l=4, r=42, t=4, b=16),
        showlegend=False,
        xaxis=dict(
            visible=True,
            showgrid=True,
            gridcolor="rgba(0, 0, 0, 0.08)",
            showline=True,
            linecolor=INK_BLACK,
            rangeslider=dict(visible=False),
            type="category",
            categoryorder="array",
            categoryarray=cat_order,
            range=x_range,
            tickfont=dict(size=8, family="Courier New", color=INK_MUTED),
            tickmode="auto",
            nticks=5,
            fixedrange=False,     # Aktifkan geser horizontal (pan)
        ),
        yaxis=dict(
            visible=True,
            side="right",
            showgrid=True,
            gridcolor="rgba(0, 0, 0, 0.08)",
            showline=True,
            linecolor=INK_BLACK,
            tickfont=dict(size=8.5, family="Courier New", color=INK_BLACK),
            tickmode="auto",
            nticks=4,
            tickformat=",.1f" if cur_p >= 10 else ",.4f",
            fixedrange=True,      # Kunci sumbu Y agar tidak terdistorsi saat pan horizontal
        ),
    )
    return fig


def create_convergence_fig(diffusion_data: dict = None) -> go.Figure:
    """
    Grafik konvergensi probabilitas arah LONG/SHORT.

    Memplot kurva probabilitas LONG dan SHORT sepanjang horizon waktu,
    diturunkan dari drift-diffusion. Kedua kurva komplementer dan boleh
    saling berpotongan di 0.5 — itulah yang membuat kasus bearish terlihat
    sebagai kurva yang menurun, bukan sekadar garis yang sama seperti bullish.

    `diffusion_data` mengekspektasi kunci `time_horizons`, `long_probs`,
    dan `short_probs` (dihasilkan `compute_directional_curve`). Kunci lama
    `winner_probs`/`loser_probs` masih diterima untuk call site yang belum
    dimigrasi.
    """
    fig = go.Figure()

    if not diffusion_data or "time_horizons" not in diffusion_data:
        # Render awal sebelum callback pertama: netral 50/50.
        from analysis.probability_engine import probability_engine
        diffusion_data = probability_engine.compute_directional_curve(
            prob_long=0.50,
            realized_vol_per_min=0.0018,
            horizon_minutes=30,
            num_points=60,
        )

    x = diffusion_data["time_horizons"]
    if "long_probs" in diffusion_data:
        y_long = diffusion_data["long_probs"]
        y_short = diffusion_data["short_probs"]
    else:
        # Jalur kompatibilitas untuk data bentuk lama.
        y_long = diffusion_data["winner_probs"]
        y_short = diffusion_data["loser_probs"]

    # LONG hijau solid, SHORT merah putus-putus. Pemetaan ini membalik
    # versi lama yang memakai merah untuk "winner" — warna sekarang mengikuti
    # arah, bukan kecondongan.
    fig.add_trace(go.Scatter(
        x=x, y=y_long,
        mode="lines",
        line=dict(color=GREEN_VINTAGE, width=2),
        name="LONG",
        hoverinfo="y",
    ))

    fig.add_trace(go.Scatter(
        x=x, y=y_short,
        mode="lines",
        line=dict(color=RED_VINTAGE, width=1.5, dash="dash"),
        name="SHORT",
        hoverinfo="y",
    ))

    # Garis acuan: 1.0 (keyakinan maksimum) dan 0.5 (netral).
    fig.add_hline(y=1.0, line=dict(color=INK_BLACK, width=1, dash="dot"))
    fig.add_hline(y=0.5, line=dict(color=INK_MUTED, width=1, dash="dash"))

    fig.update_layout(
        template="none",
        dragmode=False,
        paper_bgcolor=PARCHMENT_BG,
        plot_bgcolor=PARCHMENT_BG,
        margin=dict(l=28, r=8, t=6, b=18),
        showlegend=False,
        xaxis=dict(
            fixedrange=True,
            showgrid=True,
            gridcolor="rgba(0,0,0,0.08)",
            linecolor=INK_BLACK,
            tickfont=dict(size=8, family="Courier New", color=INK_MUTED),
            title=dict(text="EXPIRY TIME HORIZON (T-MIN)", font=dict(size=7.5, family="Courier New", color=INK_BLACK)),
        ),
        yaxis=dict(
            fixedrange=True,
            range=[-0.05, 1.1],
            showgrid=True,
            gridcolor="rgba(0,0,0,0.08)",
            linecolor=INK_BLACK,
            tickfont=dict(size=8, family="Courier New", color=INK_BLACK),
            tickvals=[0.0, 0.5, 1.0],
            ticktext=["$0.00", "$0.50", "$1.00"],
        ),
    )
    return fig


# Simpul sistem - PETA SISTEM, bukan aliran data.
#
# Struktur ini adalah POHON STRUKTUR: setiap simpul punya tepat SATU
# induk. Itu syarat matematis, bukan pilihan estetika:
#
#   - satu anak dengan DUA induk (merge) -> dua sisi pasti berpotongan
#   - satu induk dengan BANYAK anak (fan) -> sisi-sisinya tidak pernah
#     berpotongan asal keduanya menuju ke kanan
#
# Versi sebelumnya adalah aliran data, di mana FLOW/SENT/MACRO/TECH
# semuanya mencampur ke RF_MODEL lalu DECISION_GATE. Itu merge, dan
# merge tidak bisa dihilangkan hanya dengan menata koordinat. Peta sistem
# menghapus merge secara struktural: tiap agen punya induknya sendiri.
#
# Kolom: 0 akar | 1 kategori | 2 agen | 3 keluaran
NEURAL_SYSTEM_NODES = {
    # Kolom 0 - akar
    "CORE_ENGINE":      (1.0,  5.60, "CORE",  palette.INK, "Central AI Ingest"),

    # Kolom 1 - empat kategori, masing-masing dengan satu anak sendiri
    "CATEGORY_FEED":   (4.6, 11.10, "FEED",  palette.WARN, "Market data ingestion"),
    "CATEGORY_SENT":   (4.6,  7.20, "SENT",  palette.WARN, "News sentiment"),
    "CATEGORY_ML":     (4.6,  3.30, "ML",    palette.AMBER, "Machine learning"),
    "CATEGORY_RISK":   (4.6, -0.60, "RISK",  palette.BLUE, "Risk sizing gate"),

    # Kolom 2 - empat agen, satu per kategori, tidak ada yang berbagi induk
    "BINANCE_FEED":    (8.2, 12.60, "FLOW",  palette.WARN, "Hyperliquid WS + L2 book"),
    "SENTIMENT":       (8.2,  8.70, "SENT",  palette.WARN, "VADER + FinBERT"),
    "RF_MODEL":        (8.2,  4.80, "RF ML", palette.AMBER, "RandomForest (7-feat)"),
    "DECISION_GATE":   (8.2,  0.90, "RISK",  palette.BLUE, "Risk sizing gate"),

    # Kolom 3 - keluaran, masing-masing anak dari agennya sendiri
    "MACRO_FRED":      (11.8, 12.60, "MACRO", palette.INK_MUTED, "CPI / Yield / FFR"),
    "TECH_TA":         (11.8,  8.70, "TECH",  palette.BLUE, "pandas-ta (RSI/MACD/BB)"),
    "EXECUTION_AGENT": (11.8,  4.80, "EXEC",  palette.GREEN, "Paper engine exec"),
    "YF_FALLBACK":     (11.8,  0.90, "YFI",   palette.INK_MUTED, "Tier 1 fallback"),
}
# Tidak ada bus.
#
# Versi sebelumnya memakai simpul virtual ("bus") sebagai perantara
# EXEC -> token untuk menghindari crossing. Justru sebaliknya: bus
# menjadi sumber crossing BARU, karena EXEC punya dua anak (bus_left
# dan bus_right) dan jalur ke bus_right wajib melewati x=bus_left di
# mana drop vertikal ke token mulai. Persilangan pindah tempat,
# bukan hilang.
#
# Sekarang token connect LANGSUNG ke EXEC. Kipas dari satu titik
# adalah pola mindmap yang wajar; yang perlu dijaga hanya supaya
# edge dari sistem ke EXEC tidak ikut memotong kipas itu.



NEURAL_SYSTEM_EDGES = [
    # Pohon struktur. Setiap anak muncul tepat SEKALI dengan satu induk:
    # ROOT -> kategori -> agen -> keluaran. Tanpa merge, tanpa crossing.
    ("CORE_ENGINE", "CATEGORY_FEED"),
    ("CORE_ENGINE", "CATEGORY_SENT"),
    ("CORE_ENGINE", "CATEGORY_ML"),
    ("CORE_ENGINE", "CATEGORY_RISK"),
    ("CATEGORY_FEED", "BINANCE_FEED"),
    ("CATEGORY_SENT", "SENTIMENT"),
    ("CATEGORY_ML",   "RF_MODEL"),
    ("CATEGORY_RISK", "DECISION_GATE"),
    ("BINANCE_FEED",  "MACRO_FRED"),
    ("SENTIMENT",     "TECH_TA"),
    ("RF_MODEL",      "EXECUTION_AGENT"),
    ("DECISION_GATE", "YF_FALLBACK"),
]


# Slot koordinat koin satelit, disusun sebagai BUSUR RADIAL BERLAPIS.
#
# Alasan mengganti busur pinggir yang lama: simpul sistem occupy
# rentang x [-0.80, 0.82] dan y [-0.38, 0.65]. Slot token lama di y ~ -0.22
# sampai -0.74 berarti 5 dari 12 slot menumpuk area yang sama dengan
# DECISION_GATE / EXECUTION_AGENT / SENTIMENT. Akibatnya label bertabrakan
# dan garis saling menembus.
#
# Slot token sebagai DUA BARIS LURUS di bawah sumbu y=0, bukan cincin.
#
# Cincin konsentris terlihat rapi di atas kertas, tapi di panel 470px
# radius dalamnya cuma ~40px antar simpul. Akibatnya label 3-4
# karakter saling menimpa; Empat pasangan terdeteksi bertabrakan:
# BNB<->XRP, BNB<->ONDO, XRP<->DOGE, ONDO<->ETH.
#
# Baris lurus dengan jarak x tetap lebih hemat: jarak antar label jadi
# seragam dan bisa dipastikan lebih besar dari lebar teks, tanpa
# bergantung pada radius. Baris atas (token) dan baris bawah (sistem)
# dipisah celah y yang lega, dan label token selalu di BAWAH simpul —
# jadi tidak mungkin menabrak label sistem yang berada di atas.
# Dua kolom token, masing-masing dilayani satu bus vertikal di atasnya.
# Pitch baris 2.05 unit memberi setiap label ruang sendiri sehingga tidak
# pernah menyentuh baris di bawahnya.
TOKEN_SLOTS = [
    # Pitch baris 2.60 unit = 36px. Diameter marker maksimum 30px, jadi
    # pitch 2.05 unit (28.6px) membuat simpul bersentuhan - itu yang
    # membuat token pernah bertumpuk di kolom kanan atas.
    (18.5, 13.20), (18.5, 10.60), (18.5,  8.00),
    (18.5,  5.40), (18.5,  2.80), (18.5,  0.20),
    (22.0, 13.20), (22.0, 10.60), (22.0,  8.00),
    (22.0,  5.40), (22.0,  2.80), (22.0,  0.20),
]


# Registri slot token — dibangun SEKALI saat import, tidak pernah dihitung
# ulang saat render.
#
# Ini adalah akar "berantakan" yang dikeluhkan user. Versi lama memetakan
# `TOKEN_SLOTS[i]` berdasarkan POSISI dalam daftar token yang aktif. begitu
# satu token hilang, semua token di belakangnya melompat satu slot (XRP 8->7,
# BNB 11->9, dan seterusnya) — hanya karena daftar sinyal bergeser. Di layar
# itu terlihat persis acak: simpul melompat tiap 500 ms tanpa alasan.
#
# Di sini pemetaan token->slot diselesaikan SATU KALI dari daftar token
# yang tetap, lalu rendering jadi lookup murni. Konsekuensinya:
#   - Token yang sama SELALU di koordinat yang sama, selamanya.
#   - Tambah/hilang token lain tidak menyentuh posisi token ini.
#   - Nol pekerjaan pemetaan per frame.
#
# Tabrakan hash diselesaikan sekali di sini (bukan per render). Kalau
# diselesaikan per render, token yang "kalah" tabrakan akan loncat ke slot
# kosong hanya ketika 기간ya, lalu balik ke slot aslinya saat tabrakannya
# hilang — dua arah lompat, sama saja buruknya.
_TOKEN_UNIVERSE = [
    "BTC", "ETH", "SOL", "XRP", "BNB", "DOGE", "NEAR", "HYPE",
    "ZEC", "ONDO", "ENA", "XPL", "AVAX", "LINK", "ADA", "ARB", "SUI",
    "TIA", "APT", "OP", "INJ", "SEI", "PEPE", "WIF", "BONK",
]

# Prioritas tampil: koin mayor didahulukan saat harus memilih satu slot
# untuk dua token yang berebut. Murni kosmetik — tidak mengubah slot
# yang sudah ter-assign.
# Urutan tampilan token. 12 token PERTAMA yang disebut di sini
# yang dapat slot; sisanya menerima slot tersisa dalam urutan stabil.
# Kalau daftar ini hanya berisi 5 nama, sisanya diisi berdasarkan
# abjad (ADA, APT, ARB...) dan koin yang_urban seperti NEAR/HYPE
# justru tidak kebagian slot.
_TOKEN_DISPLAY_PRIORITY = [
    "BTC", "ETH", "SOL", "XRP", "BNB", "DOGE",
    "NEAR", "HYPE", "ZEC", "ONDO", "ENA", "XPL",
    "AVAX", "LINK", "ADA", "ARB", "SUI", "TIA",
    "APT", "OP", "INJ", "SEI", "PEPE", "WIF", "BONK",
]


def _fnv1a(text: str) -> int:
    """FNV-1a 32-bit.

    Deterministik lintas proses, berbeda dari `hash()` bawaan Python yang
    nilainya diacak tiap proses (PYTHONHASHSEED) — kalau yang dipakai,
    token bisa berganti tempat setiap kali aplikasi di-restart.
    """
    h = 0x811C9DC5
    for ch in text:
        h ^= ord(ch)
        h = (h * 0x01000193) & 0xFFFFFFFF
    return h


def _build_token_slot_registry() -> dict:
    """
    Petakan token ke slot secara DETERMINISTIS dan TERBACA.

    Versi lama memakai hash FNV-1a, jadi token jatuh ke kolom mana saja
    secara acak -lements reader. Untuk mindmap, pemisahan dua kolom token
    harus stabil: 6 token pertama di kolom kiri, 6 berikutnya di kanan,
    mengikuti urutan prioritas di bawah (mayor lebih dulu).

    Token di luar daftar tetap dapat slot, diisi dari slot yang tersisa
    dalam urutan stabil (alphabet), sehingga tidak ada token yang hilang
    hanya karena namanya tidak dikenal.
    """
    n = len(TOKEN_SLOTS)
    left_x = [s[0] for s in TOKEN_SLOTS[:n // 2]]
    right_x = [s[0] for s in TOKEN_SLOTS[n // 2:]]

    registry: dict = {}
    used: set = set()

    # Prioritas: koin mayor dulu, sisanya alphabet - deterministik.
    order = list(_TOKEN_DISPLAY_PRIORITY) + [
        t for t in sorted(_TOKEN_UNIVERSE)
        if t not in _TOKEN_DISPLAY_PRIORITY
    ]

    half = n // 2
    left_slots = list(range(half))            # slot 0..5  -> kolom kiri
    right_slots = list(range(half, n))        # slot 6..11 -> kolom kanan

    for idx, sym in enumerate(order):
        if len(used) >= n:
            break
        pool = left_slots if idx < half else right_slots
        for si in pool:
            if si not in used:
                used.add(si)
                registry[sym] = si
                break
    return registry


_TOKEN_SLOT_REGISTRY: dict = _build_token_slot_registry()


# Kompatibilitas dengan impor terdahulu
NEURAL_NODE_LAYOUT = dict(NEURAL_SYSTEM_NODES)
TOKEN_NODE_KEYS = ["BTC", "ETH", "SOL", "XRP", "BNB", "DOGE", "NEAR"]
TOKEN_ANCHORS = TOKEN_SLOTS

# Pemetaan simpul agen -> kunci denyut di `activity_map` (baris log per menit).
AGENT_NODE_METRIC = {
    "EXECUTION_AGENT": "execution_agent",
    "DECISION_GATE": "decision_agent",
    "RF_MODEL": "analysis_agent",
    "SENTIMENT": "news_agent",
    "TECH_TA": "analysis_agent",
    "MACRO_FRED": "analysis_agent",
}

# Pemetaan simpul feed -> channel WebSocket Hyperliquid.
FEED_NODE_CHANNEL = {
    "BINANCE_FEED": "allMids",
    "CORE_ENGINE": "l2Book",
    "YF_FALLBACK": "candle",
    "COINGECKO": "trades",
}


def create_neural_net_fig(signal_map: dict = None, activity_map: dict = None,
                         flow_map: dict = None) -> go.Figure:
    """
    Grafik Konstelasi Jaringan 2D (Snipe Neural Net).
    Semua koin ditempatkan pada slot orbital eksklusif tanpa pernah bertumpuk.
    """
    fig = go.Figure()
    signal_map = signal_map or {}
    activity_map = activity_map or {}
    flow_map = flow_map or {}

    layout = dict(NEURAL_SYSTEM_NODES)

    # 1. Kumpulkan token unik yang aktif.
    #
    # `MARKET` adalah simbol sentinel yang ditulis NewsAgent untuk sentimen
    # agregat pasar (agents/news_agent.py), BUKAN koin. Tanpa filter ini ia
    # menempati satu slot satelit dan tampil sebagai simpul bernama "MARKET".
    RESERVED_SYMBOLS = {"MARKET", "GLOBAL", "AGGREGATE", "TOTAL"}
    active_tokens = []
    for s in signal_map:
        base = str(s or "").split("/")[0].split(":")[0].upper()
        if not base or base in RESERVED_SYMBOLS:
            continue
        if base not in active_tokens and base not in NEURAL_SYSTEM_NODES:
            active_tokens.append(base)

    # 2. Hanya token yang BENAR-BENAR punya sinyal yang digambar.
    #
    # Versi lama mengisi sisanya dari `_TOKEN_UNIVERSE` sampai 12 token, lalu
    # membuat edge `CORE_ENGINE -> token` untuk semuanya. Akibatnya dengan
    # `signal_map={}` graf tetap menampilkan 22 marker dan 24 edge — 12 di
    # antaranya untuk koin yang tidak punya data. Itu bukan placeholder
    # yang jujur, itu dekorasi yang menyamar sebagai data, dan membuat panel
    # terlihat sibuk padahal tidak ada yang terjadi.
    #
    # Kalau memang tidak ada sinyal, graf harus benar-benar kosong.
    display_tokens = [t for t in active_tokens][:len(TOKEN_SLOTS)]

    # 3. Setiap token menempati satu slot koordinat unik (anti-tumpang tindih).
    token_layout_keys = []
    for sym in display_tokens:
        slot_index = _TOKEN_SLOT_REGISTRY.get(sym)
        if slot_index is None:
            # Token di luar universe registry: jatuh ke slot sisa terakhir
            # yang belum dipakai. Ini kasus langka (koin baru yang belum
            # masuk daftar), tapi tidak boleh membuat figure meledak.
            free = [i for i in range(len(TOKEN_SLOTS))
                    if all(
                        TOKEN_SLOTS[i] != tuple(layout[k][:2])
                        for k in token_layout_keys
                    )]
            if not free:
                continue
            slot_index = free[0]
        ax, ay = TOKEN_SLOTS[slot_index]
        layout[sym] = (ax, ay, sym, INK_MUTED, "Perpetual Satellite")
        token_layout_keys.append(sym)
    # --- Sisi graf -------------------------------------------------------
    def edge_flow(src, dst):
        """Bobot aliran riil untuk sebuah sisi."""
        if dst in signal_map:
            ch = signal_map[dst].get("price_change")
            if ch is not None:
                return min(abs(float(ch)) * 400.0, 1.0)
        agent_key = AGENT_NODE_METRIC.get(src) or AGENT_NODE_METRIC.get(dst)
        if agent_key:
            return min(float(activity_map.get(agent_key, 0.0)), 1.0)
        for node in (src, dst):
            ch = FEED_NODE_CHANNEL.get(node)
            if ch:
                return min(float(flow_map.get(ch, 0.0)), 1.0)
        return 0.0

    # Kumpulkan sisi sistem + sisi ke koin satelit.
    #
    # Edge ke token HANYA digambar kalau token itu punya arah yang jelas
    # (LONG/SHORT). Versi lama menambahkan `CORE_ENGINE -> token` tanpa
    # syarat apa pun, jadi setiap token - termasuk yang datanya sepi -
    # tetap menarik garis ke pusat. Hasilnya 12 garis radial yang saling
    # menyeberang menjadi pola seperti sitemap, bukan peta aliran.
    all_edges = list(NEURAL_SYSTEM_EDGES)
    # EXEC -> puncak bus. Edge ini satu-satunya yang menghubungkan
    # zona keluaran ke zona token; sisanya drop paralel dari bus.
    # HANYA simpul baris bawah yang jadi tempat(token).
    #
    # Sebelumnya CORE_ENGINE ikut jadi kandidat, dan karena posisinya di
    # KIRI ATAS while semua token di KANAN BAWAH, setiap edge token
    # menyeberang hampir seluruh panel (rata-rata 242px dari 440px) dan
    # saling berpotongan. Baris bawah punya kolom yang sejajar dengan
    # kolom token, jadi setiap token bisa drop tegak lurus.
    # Token menempel ke KELUARAN paling kanan, karena mindmap mengalir ke
    # kanan: CORE -> sumber -> analisis -> keluaran -> token.
    # Edge datar dari Exec (x=13) ke kolom token (x=17) tidak pernah
    # memotong simpul lain karena tidak ada node di antaranya.
    token_hubs = ("EXECUTION_AGENT",)

    # Arah per token: menentukan solid vs dashed, bukan ada/tidaknya edge.
    token_edge_direction = {}

    for sym in token_layout_keys:
        # SETIAP token dapat satu edge, bukan hanya yang punya arah.
        #
        # Versi lama `continue` kalau arahnya kosong, jadi 11 dari 12 token
        # sama sekali tidak punya garis - itu yang bikin sebagian koin
        # terlihat "nempel" dan sebagian menggantung. Arah sinyal hanya
        # menentukan GAYA garis (solid vs dashed), bukan apakah garis ada.
        sig = signal_map.get(sym)
        direction = str((sig or {}).get("direction") or "").upper()
        directional = direction in ("LONG", "SHORT", "BULLISH", "BEARISH")

        # Token connect LANGSUNG ke EXEC - tanpa perantara. Kipas dari satu
        # titik adalah pola mindmap yang wajar; yang perlu dijaga hanya
        # supaya edge sistem lain tidak ikut memotong kipas ini.
        all_edges.append(("EXECUTION_AGENT", sym))
        token_edge_direction[sym] = directional


    # Sisi digambar SATU PER TRACE, bukan digabung dalam satu trace.
    #
    # Alasannya dua, keduanya penting:
    #   1. CSS `stroke-dashoffset` hanya bisa diarahkan ke path tertentu.
    #      Kalau 20 sisi digabung dalam satu `path.js-line`, animasi yang
    #      sama berlaku untuk semuanya dan animasi staggering mustahil.
    #   2. Plotly memotong tiap trace dengan clip path sendiri. Dengan satu
    #      trace per sisi, tidak ada garis yang ikut terpotong tetangga.
    #
    # Titik kontrol Bezier digeser tegak lurus dari titik tengah sehingga
    # sisi melengkung dan tidak pernah menumpuk persis di atas sisi lain.
    def _edge_curve(src, dst, samples=18):
        """Titik-titik Bezier kuadrat dari src ke dst, melengkung ke samping."""
        ax, ay = layout[src][0], layout[src][1]
        bx, by = layout[dst][0], layout[dst][1]
        mx, my = (ax + bx) / 2.0, (ay + by) / 2.0
        dx, dy = bx - ax, by - ay
        length = math.hypot(dx, dy)
        if length < 1e-9:
            return [ax, bx], [ay, by]
        # Offset tegak lurus.ubyek DIBATAS tetap, bukan proporsional
        # panjang: busur yang ikut membesar membuat edge panjang melengkung
        # jauh dan berdempet jadi pita - sumber "ruwet" yang lain.
        # Untuk edge yang_drop tegak lurus (token ke hub di atasnya) busur
        # kecilpun membuat garis terlihat miring, jadi busur hanya
        # dipakai kalau edge-nya benar-benar miring.
        bow = 0.0 if abs(dx) < 1e-6 else min(length * 0.06, 0.9)
        ox = -dy / length * bow
        oy = dx / length * bow
        cx, cy = mx + ox, my + oy

        xs, ys = [], []
        for i in range(samples):
            t = i / (samples - 1)
            inv = 1.0 - t
            xs.append(inv * inv * ax + 2 * inv * t * cx + t * t * bx)
            ys.append(inv * inv * ay + 2 * inv * t * cy + t * t * by)
        return xs, ys

    for edge_idx, (src, dst) in enumerate(all_edges):
        if src not in layout or dst not in layout:
            continue
        xs, ys = _edge_curve(src, dst)
        flow = edge_flow(src, dst)
        busy = flow >= 0.34

        if busy:
            # Sisi sibuk. Pola dash ditetapkan DI SINI (bukan di CSS) supaya
            # Plotly menulisnya sebagai inline style yang ikut ter-render
            # ulang setiap redraw. CSS lalu hanya menggeser
            # `stroke-dashoffset` pada pola yang sudah ada itu.
            #
            # Pola DIBERDAIKAN lewat hash NAMA sisi, bukan lewat urutan,
            # supaya stabil: sisi yang sama selalu memakai pola yang sama,
            # tidak berubah ketika tetangga di atasnya hilang.
            #
            # Kenapa polanya dibeda-bedakan, dan bukan satu untuk semua:
            # CSS murni tidak bisa memberi `animation-delay` per sisi. Cubaan
            # sebelumnya memakai `:nth-child(6n+1..6)`, tapi itu tidak pernah
            # aktif — Plotly membuat satu `<g class="trace">` per trace dan
            # tiap `<g>` memuat tepat satu `<path class="js-line">`, jadi
            # setiap path selalu `:nth-child(1)`. Side-effect-nya semua sisi
            # berdenyut serentak persis dalam ritme yang paling mekanis.
            # Meng varied dash period accomplishes the same visual desync
            # without depending on a selector that cannot exist.
            #
            # SEMUA pola di bawah berperiode 16px, jadi CSS tetap bisa
            # menggeser tepat -16px per siklus dan tidak ada "meleset" di
            # sambungan. Variasinya hanya di panjang dash dan gap.
            dash_phase = _fnv1a(f"{src}->{dst}") % 4
            dash_patterns = ("9px,7px", "11px,5px", "6px,10px", "12px,4px")
            fig.add_trace(go.Scatter(
                x=xs, y=ys,
                mode="lines",
                line=dict(
                    color=with_alpha(BLUE_VINTAGE, 0.50),
                    width=1.1 + 1.5 * flow,
                    dash=dash_patterns[dash_phase],
                    shape="spline",
                    smoothing=0.7,
                ),
                name=f"busy-{edge_idx}",
                customdata=[[src, dst, round(flow, 3)] for _ in xs],
                hovertemplate=(
                    f"{src} &#8594; {dst}<br>flow {flow:.0%}<extra></extra>"
                ),
                hoverinfo="text",
                showlegend=False,
            ))
        else:
            # Sisi sepi: tipis, redup, dan STATIS. Arus data tidak mengalir
            # di jalur tanpa aktivitas; menganimasikan semuanya hanya
            # menambah noise tanpa informasi.
            fig.add_trace(go.Scatter(
                x=xs, y=ys,
                mode="lines",
                line=dict(
                    color=INK_FAINT,
                    width=0.9,
                    shape="spline",
                    smoothing=0.7,
                ),
                name=f"idle-{edge_idx}",
                customdata=[[src, dst, 0.0] for _ in xs],
                hovertemplate=f"{src} &#8594; {dst}<extra></extra>",
                hoverinfo="text",
                showlegend=False,
            ))

    # --- Simpul ----------------------------------------------------------
    node_x, node_y, node_text, node_color, node_size, node_hover, node_textpos = [], [], [], [], [], [], []

    for key, (nx, ny, label, base_color, desc) in layout.items():
        sig = signal_map.get(key) if key in signal_map else None
        pulse = None
        if key in AGENT_NODE_METRIC:
            pulse = activity_map.get(AGENT_NODE_METRIC[key])
        elif key in FEED_NODE_CHANNEL:
            pulse = flow_map.get(FEED_NODE_CHANNEL[key])

        if sig:
            direction = str(sig.get("direction") or "").upper()
            confidence = float(sig.get("confidence") or 0.0)
            if "BULL" in direction or "LONG" in direction or "BUY" in direction:
                color = GREEN_VINTAGE
            elif "BEAR" in direction or "SHORT" in direction or "SELL" in direction:
                color = RED_VINTAGE
            else:
                color = INK_MUTED
            size = 12.0 + float(np.clip(confidence, 0.0, 1.0)) * 12.0
            hover = f"<b>{label}</b><br>{desc}<br>Signal: {direction or 'N/A'} · Conf {confidence:.0%}"
            ch = sig.get("price_change")
            if ch is not None:
                hover += f"<br>Δ60s: {float(ch) * 100:+.3f}%"
        else:
            color = base_color
            size = 15.0 if key in ("CORE_ENGINE",) else 12.0
            hover = f"<b>{label}</b><br>{desc}"

        if pulse is not None:
            size += float(np.clip(float(pulse), 0.0, 1.0)) * 6.0
            hover += f"<br>Activity: {float(pulse) * 100:.0f}%"

        # Arah label mengikuti POSISI dalam mindmap.
        #
        # Kolom paling kiri (akar) menaruh label di BAWAH supaya tidak
        # menabrak cabang yang keluar ke kanan. Kolom paling kanan
        # (token) menaruh label di ATAS supaya tidak menabrak token
        # yang ada di bawahnya.
        is_token = key in token_layout_keys
        if is_token:
            textpos = "top center"
        elif nx <= 1.01:
            textpos = "bottom center"
        else:
            textpos = "top center"

        node_x.append(nx)
        node_y.append(ny)
        node_text.append(label)
        node_color.append(color)
        node_size.append(size)
        node_hover.append(hover)
        node_textpos.append(textpos)

    fig.add_trace(go.Scatter(
        x=node_x, y=node_y,
        mode="markers+text",
        marker=dict(
            size=node_size,
            color=node_color,
            line=dict(color=INK_BLACK, width=1.5),
            symbol="circle",
        ),
        text=node_text,
        textposition=node_textpos,
        # 10px, bukan 8.5. Di sel 470px ada 78px antar-simpul — label
        # 8.5px cuma-baca, 10px terbaca tanpa menabrak tetangga.
        textfont=dict(
            size=10,
            family="'Share Tech Mono', ui-monospace, Courier New, monospace",
            color=INK_BLACK,
        ),
        hoverinfo="text",
        hovertext=node_hover,
        showlegend=False,
    ))

    fig.update_layout(
        template="none",
        dragmode=False,
        paper_bgcolor=PARCHMENT_BG,
        plot_bgcolor=PARCHMENT_BG,
        margin=dict(l=14, r=14, t=10, b=16),
        showlegend=False,
        # Sumbu diperlebar sedikit supaya label token di slot terluar tidak
        # pernah terpotong tepi kanvas.
        # Dipilih supaya px_per_unit_x == px_per_unit_y (lingkaran tetap
        # bulat) DAN label node paling bawah tidak jatuh keluar kanvas.
        xaxis=dict(visible=False, fixedrange=True, range=[0.0, 31.5]),
        yaxis=dict(visible=False, fixedrange=True, range=[-1.8, 14.67]),
        # JANGAN memakai scaleanchor/scaleratio di sini.
        #
        # scaleanchor="x" + scaleratio=1 memaksa Plotly memenuhi rasio 1:1.
        # Ketika panel jauh lebih lebar daripada tingginya (1900 x 246 px),
        # satu-satunya cara Plotly memenuhi itu adalah MELARANGKAN RANGE x
        # (bukan domain), sehingga range efektif x meledak dari 2.04 menjadi
        # sekitar 15 unit. Akibatnya px-per-unit turun ~7.4x:
        # cincin token yang mestnya berjarak 27px hanya jadi ~14px, dan
        # label ticker 3-4 karakter (16-21px) saling tumpuk jadi
        # gundukan yang tidak terbaca. Persis keluhan "berantakan".
        #
        # Kalau marker terlihat lonjong saat panel Very wide, itu konsekuensi
        # yang JAUH lebih baik daripada label yang tidak bisa dibaca.
        # Sumbu tetap antar refresh: tanpa ini Plotly menulis ulang zoom tiap
        # tick dan graf terasa berkedip.
        uirevision="neural-constellation",
    )
    return fig


def create_equity_area_fig(balance_history: list, initial_balance: float = 10000.0) -> go.Figure:
    """Grafik kurva pertumbuhan Equity & Realized PnL bergaya vintage retro."""
    fig = go.Figure()

    if not balance_history or len(balance_history) < 2:
        # Belum ada snapshot equity: garis dasar datar pada saldo awal akun riil
        x = pd.date_range(end=pd.Timestamp.now(), periods=5, freq="10min")
        y = [float(initial_balance)] * 5
    else:
        df = pd.DataFrame(balance_history)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.sort_values("timestamp")
        x = df["timestamp"]
        y = df["equity"] if "equity" in df.columns else df["balance"]

    # Baseline = modal awal, digambar sebagai trace sendiri.
    #
    # Dulu fill="tozeroy" dipakai agar area diwarnai sampai bawah. Tapi
    # `tozeroy` memaksa sumbu-y memuat NOL, dan pada akun 10.000 USDT dengan
    # pergerakan equity 10 USDT, angka 10 itu cuma 0.1% tinggi grafik —
    # kurvanya tampak datar padahal bot sedang untung. `tonexty` mengisi
    # ke trace sebelumnya (baseline), jadi sumbu-y bebas mengikuti data dan
    # pergerakan sekecil apa pun tetap terlihat.
    fig.add_trace(go.Scatter(
        x=x, y=[float(initial_balance)] * len(x),
        mode="lines",
        line=dict(color=INK_TRANSPARENT, width=0),
        hoverinfo="skip",
        showlegend=False,
        name="baseline",
    ))

    fig.add_trace(go.Scatter(
        x=x, y=y,
        mode="lines",
        line=dict(color=GREEN_VINTAGE, width=2),
        fill="tonexty",
        fillcolor=with_alpha(GREEN_VINTAGE, 0.22),
        name="EQUITY (USDT)",
        hoverinfo="x+y",
    ))

    fig.add_hline(y=float(initial_balance), line=dict(color=INK_MUTED, width=1, dash="dot"))

    fig.update_layout(
        template="none",
        dragmode=False,
        paper_bgcolor=PARCHMENT_BG,
        plot_bgcolor=PARCHMENT_BG,
        margin=dict(l=48, r=12, t=8, b=24),
        showlegend=False,
        xaxis=dict(
            fixedrange=True,
            showgrid=True,
            gridcolor="rgba(0,0,0,0.07)",
            linecolor=INK_BLACK,
            tickfont=dict(size=8.5, family="Courier New", color=INK_BLACK),
            tickformat="%H:%M",
        ),
        yaxis=dict(
            fixedrange=True,
            showgrid=True,
            gridcolor="rgba(0,0,0,0.07)",
            linecolor=INK_BLACK,
            tickfont=dict(size=8.5, family="Courier New", color=INK_BLACK),
            tickprefix="$",
            tickformat=",.0f",
        ),
    )
    return fig


def create_mini_sparkline_fig(data_series: list, line_color: str = GREEN_VINTAGE) -> go.Figure:
    """Sparkline mini tanpa axis untuk velocitas PnL atau pulse volume."""
    fig = go.Figure()

    if not data_series or len(data_series) < 2:
        # Tidak ada data = tidak ada garis. Empty-state jujur, bukan deret hardcoded.
        fig.update_layout(
            template="none",
            dragmode=False,
            paper_bgcolor=PARCHMENT_BG,
            plot_bgcolor=PARCHMENT_BG,
            margin=dict(l=0, r=0, t=0, b=0),
            showlegend=False,
            xaxis=dict(visible=False, fixedrange=True),
            yaxis=dict(visible=False, fixedrange=True),
            annotations=[dict(
                text="NO DATA",
                xref="paper", yref="paper", x=0.5, y=0.5,
                showarrow=False,
                font=dict(size=8, family="Courier New", color=INK_MUTED),
            )],
        )
        return fig

    fig.add_trace(go.Scatter(
        y=data_series,
        mode="lines",
        line=dict(color=line_color, width=1.6),
        hoverinfo="none",
    ))

    fig.update_layout(
        template="none",
        dragmode=False,
        paper_bgcolor=PARCHMENT_BG,
        plot_bgcolor=PARCHMENT_BG,
        margin=dict(l=0, r=0, t=0, b=0),
        showlegend=False,
        xaxis=dict(visible=False, fixedrange=True),
        yaxis=dict(visible=False, fixedrange=True),
    )
    return fig
