"""
dashboard/layouts/hud.py — Retro Bloomberg / CRT Terminal Command Center HUD Layout.
Menampilkan tampilan single-screen densitas tinggi dengan inisialisasi visual instan anti-whitebox.
"""

import pandas as pd
from dash import html, dcc

from dashboard.layouts.hud_figures import (
    create_mini_candlestick_fig,
    create_convergence_fig,
    create_neural_net_fig,
    create_equity_area_fig,
    create_mini_sparkline_fig,
    GREEN_VINTAGE,
    BLUE_VINTAGE,
)


def create_hud_layout() -> html.Div:
    """
    Susunan utama HUD.

    Satu grid 12 kolom. Setiap zone menyatakan lebar lewat `span-N`, jadi
    alignment datang dari grid dan bukan dari tebangan ukuran per baris.
    Urutan zone mengikuti urutan pertanyaan trader:

      Z1  metrics      — kondisi, saldo, dan eksposur
      Z2  price + risk — apa yang terjadi sekarang
      Z3  pipeline     — apakah sistemnya bekerja
      Z4  analytics    — apakah sistemnya bekerja atau tidak
    """
    return html.Div([
        create_top_bar(),
        create_ticker_tape(),

        html.Div([
            # Z1 · ACCOUNT RAIL — satu baris, semua angka yang selalu dilihat
            create_wallet_pnl_panel(),

            # Z2 · PRICE DOMINAN + STACK ANALITIK
            create_mini_price_panel(),
            html.Div([
                create_positions_panel(),
                create_scanner_panel(),
            ], className="zone2-stack"),

            # Z3 · PIPELINE — strip pipih, bukan baris penuh
            create_symbol_pnl_panel(),

            # Z4 · ANALYTICS SEKUNDER
            create_equity_growth_panel(),
            create_trade_logs_panel(),
            create_live_analytics_panel(),
            create_neural_net_panel(),
        ], className="hud-grid"),

        create_footer_ticker(),
    ], className="hud-container")


def create_top_bar() -> html.Div:
    """Header bar atas bergaya Bloomberg Terminal."""
    return html.Div([
        html.Div([
            html.Span("F3", className="badge-black", style={"padding": "2px 6px", "fontSize": "11px"}),
            html.Span("0XF3CE25 // AI CRYPTO FUTURES TERMINAL", className="bot-brand"),
            html.Span("PAPER TRADING", className="badge-outline-green"),
            # `pulse-live` adalah satu-satunya denyut di halaman: satu badge
            # ini menandakan sistem hidup, bukan 13 chip log yang berdenyut
            # bersamaan.
            html.Span("LIVE", className="badge-green pulse-live"),
        ], className="topbar-left"),

        html.Div([
            html.Div([
                html.Span("BTC", className="spot-sym"),
                html.Span("--", id="hud-spot-btc", style={"fontWeight": "bold"}),
            ], className="spot-pill"),
            html.Div([
                html.Span("ETH", className="spot-sym"),
                html.Span("--", id="hud-spot-eth", style={"fontWeight": "bold"}),
            ], className="spot-pill"),
            html.Div([
                html.Span("SOL", className="spot-sym"),
                html.Span("--", id="hud-spot-sol", style={"fontWeight": "bold"}),
            ], className="spot-pill"),

            html.Div([
                html.Span("EDGE", className="spot-edge"),
                html.Span("--", id="hud-avg-edge", className="text-green", style={"fontWeight": "bold"}),
            ], className="spot-pill"),

            html.Span("UTC --:--:--", id="hud-utc-clock", className="utc-clock"),
            html.Span("--ms", id="hud-latency-badge", className="badge-black"),
        ], className="topbar-right"),
    ], className="hud-topbar")


def create_ticker_tape() -> html.Div:
    """Running marquee live feed (120 FPS smooth continuous loop)."""
    text_sample = "INITIALIZING EVENT BUS · AWAITING LIVE AGENT TELEMETRY · "
    return html.Div([
        html.Span("●", className="ticker-dot"),
        html.Span("LIVE EVENT TAPE:", className="tape-label"),
        html.Div([
            html.Div(text_sample + text_sample, id="hud-live-ticker-text", className="ticker-marquee-content"),
        ], className="ticker-marquee-wrapper"),
    ], className="hud-live-ticker-tape")


def create_wallet_pnl_panel() -> html.Div:
    """
    Rail metrics — satu baris pipih, bukan kartu besar.

    Versi lama menaruh PnL 52px di kolom seempat dengan label, sub-teks,
    tiga statistik, dan baris risiko — total ~113px. Dipaksa ke rail 62px
    (setelah kontrak tinggi diperketat) angkanya terpotong separuh.

    Sekarang isinya satu GRID: PnL jadi kolom kiri yang lebar, angka
    pendukung flows ke kanan dalam baris yang sama. Tidak ada yang menumpuk
    vertikal, jadi tinggi rail yang dipakai benar-benar terpakai.
    """
    return html.Div([
        html.Div([
            html.Span("1. WALLET & SYSTEM PNL"),
            html.Span("PORTFOLIO", className="header-tag"),
        ], className="hud-panel-header"),
        html.Div([
            html.Div([
                html.Div("TOTAL ACCRUED PNL", className="rail-label"),
                html.Div("+$0.00", id="hud-giant-pnl", className="giant-pnl text-green"),
                html.Div("AWAITING SNAPSHOT", id="hud-wallet-sub", className="wallet-subtext"),
            ], className="rail-hero"),

            html.Div([
                html.Div([
                    html.Div("BALANCE", className="stat-label"),
                    html.Div("--", id="hud-stat-balance", className="stat-val"),
                ], className="rail-stat"),
                html.Div([
                    html.Div("EQUITY", className="stat-label"),
                    html.Div("--", id="hud-stat-equity", className="stat-val"),
                ], className="rail-stat"),
                html.Div([
                    html.Div("WIN RATE", className="stat-label"),
                    html.Div("0.0%", id="hud-stat-winrate", className="stat-val text-green"),
                ], className="rail-stat"),
                html.Div([
                    html.Div("DRAWDOWN", className="stat-label"),
                    html.Div("--/10", id="hud-stat-risk", className="stat-val text-green"),
                ], className="rail-stat"),
            ], className="rail-stats"),
        ], className="hud-panel-body rail-body"),
    ], className="hud-panel span-12 wallet-rail")


def create_mini_price_panel() -> html.Div:
    """Box 2: Candlestick Chart + Ladder Tape Mini Orderbook."""
    default_fig = create_mini_candlestick_fig(pd.DataFrame(), "BTC/USDT:USDT")

    return html.Div([
        html.Div([
            html.Div([
                html.Span("2. ASSET: "),
                dcc.Dropdown(
                    id="hud-chart-symbol-select",
                    className="symbol-select",
                    options=[
                        {"label": "BTC/USDT", "value": "BTC/USDT:USDT"},
                        {"label": "ETH/USDT", "value": "ETH/USDT:USDT"},
                        {"label": "SOL/USDT", "value": "SOL/USDT:USDT"},
                    ],
                    value="BTC/USDT:USDT",
                    clearable=False,
                ),
                html.Span("1M", className="tf-tag"),
            ]),
            html.Span("--", id="hud-chart-price-display", className="header-tag"),
        ], className="hud-panel-header"),
        html.Div([
            dcc.Graph(
                id="hud-candlestick-chart",
                figure=default_fig,
                style={"height": "var(--h-chart)", "width": "100%"},
                config={
                    "displayModeBar": False,
                    "responsive": True,
                    "scrollZoom": False,
                    "doubleClick": "reset",
                },
            ),
            html.Div(
                id="hud-orderbook-tape",
                className="orderbook-tape",
                children=[
                    html.Div("AWAITING L2 ORDERBOOK", className="empty-note"),
                ],
            ),
        ], className="hud-panel-body", style={"padding": "4px"}),
    ], className="hud-panel zone2-price")


def create_positions_panel() -> html.Div:
    """Box 3: Open Positions Grid."""
    return html.Div([
        html.Div([
            html.Span("3. ACTIVE POSITIONS"),
            html.Span("ACTIVE (0)", id="hud-positions-count-badge", className="header-tag"),
        ], className="hud-panel-header"),
        html.Div([
            html.Div(
                id="hud-positions-table",
                className="positions-grid-table",
                children=[
                    html.Div(
                        "NO OPEN POSITIONS",
                        className="empty-note",
                    ),
                    html.Div(
                        "scanner masih mencari entry di top 10 futures",
                        className="empty-note-sub",
                    ),
                ],
            ),
        ], className="hud-panel-body"),
    ], className="hud-panel p-stack")


def create_scanner_panel() -> html.Div:
    """Box 4: Probability Scanner LONG/SHORT dari ensemble multi-agen."""
    default_fig = create_convergence_fig()

    return html.Div([
        html.Div([
            html.Span("4. DIRECTION SCANNER"),
            html.Span("MULTI-AGENT ENSEMBLE", className="header-tag"),
        ], className="hud-panel-header"),
        html.Div([
            html.Div([
                html.Div([
                    html.Div("LONG PROB", className="odds-label"),
                    html.Div("--", id="hud-scanner-long-odds", className="odds-val-long"),
                ], className="odds-item"),
                html.Div([
                    html.Div("SIGNAL", className="odds-label"),
                    html.Div("AWAITING", id="hud-scanner-consensus", className="odds-consensus-badge"),
                ]),
                html.Div([
                    html.Div("SHORT PROB", className="odds-label"),
                    html.Div("--", id="hud-scanner-short-odds", className="odds-val-short"),
                ], className="odds-item"),
            ], className="scanner-odds-row"),

            # Ringkasan kesehatan ensemble: berapa agen yang bicara dan
            # berapa yang punya data. Ini yang membedakan "sinyal lemah
            # karena pasar sepi" dari "sinyal lemah karena data mati".
            html.Div([
                html.Span("ENSEMBLE", className="agent-strip-label"),
                html.Div(id="hud-scanner-agent-strip", className="agent-strip"),
            ], className="scanner-agent-strip"),

            dcc.Graph(
                id="hud-convergence-chart",
                figure=default_fig,
                style={"height": "var(--h-conv)", "width": "100%"},
                config={
                    "displayModeBar": False,
                    "responsive": True,
                    "scrollZoom": False,
                    "doubleClick": False,
                },
            ),
        ], className="hud-panel-body", style={"padding": "4px"}),
    ], className="hud-panel p-stack")


# Tahapan alur keputusan. Urutan ini dipakai bersama oleh layout dan callback
# penggerak animasi, sehingga penambahan tahap cukup diubah di satu tempat.
TREE_STAGES = ["feed", "scan", "sense", "gate", "exec", "outcome"]


def create_symbol_pnl_panel() -> html.Div:
    """
    PnL per simbol - siapa yang implementasi dan siapa yang bikin rugi.

    Panel ini menggantikan "Strategy Decision Tree" yang sebelumnya hanya
    menampilkan 6 kotak statis dengan badge "6/6 COMPLETE" - jawaban yang
    sama terus-menerus dan tidak pernah memberi informasi keputusan.

    Isinya sekarang pertanyaan yang belum terjawab di panel lain: dari
    simbol yang sedang dipantau, mana yangbringing PnL dan mana yang
    memakannya. Di sini terlihat jelas NEAR/ENA/XPL merugi sementara
    ETH/ZEC/HYPE mencetak profit - pola yang tidak akan pernah terlihat
    dari win rate global.
    """
    return html.Div([
        html.Div([
            html.Span("SYMBOL PNL - WHERE THE MONEY GOES"),
            html.Span("LIVE", id="hud-pnl-stage-tag", className="header-tag"),
        ], className="hud-panel-header"),
        html.Div([
            # Baris judul kolom - memberi tahu arti setiap angka.
            html.Div([
                html.Span("SYM", className="sympnl-head-sym"),
                html.Span("", className="sympnl-head-blank"),
                html.Span("PNL", className="sympnl-head-val"),
                html.Span("WR", className="sympnl-head-wr"),
                html.Span("N", className="sympnl-head-n"),
            ], className="sympnl-head"),
            # Kolom label - diisi lewat callback
            html.Div(id="hud-symbol-pnl-rail", className="sympnl-list"),
        ], className="hud-panel-body", style={"padding": "0"}),
    ], className="hud-panel span-12 symbol-pnl-panel")



def create_neural_net_panel() -> html.Div:
    """Graf Jaringan Konstelasi Transaksi & Sinyal Agen 2D."""
    default_fig = create_neural_net_fig(signal_map={})

    return html.Div([
        html.Div([
            html.Span("SNIPE NEURAL NET · LIVE TRANSACTION GRAPH"),
            html.Span("CONSTELLATION ENGINE", className="header-tag"),
        ], className="hud-panel-header"),
        html.Div([
            dcc.Graph(
                id="hud-neural-graph",
                figure=default_fig,
                # Tinggi dcc.Graph sengaja dibuat sedikit lebih besar dari
                # `fig.layout.height` (260px di hud_figures). Plotly mengisi
                # penuh div.graph, dan ruang ekstra mencegah label slot
                # terluar terpotong tepi.
                style={"height": "var(--h-neural)", "width": "100%"},
                config={
                    "displayModeBar": False,
                    "responsive": True,
                    "scrollZoom": False,
                    "doubleClick": False,
                },
            ),
        ], className="hud-panel-body", style={"padding": "2px"}),
    ], className="hud-panel span-3 zone3-cell")


def create_equity_growth_panel() -> html.Div:
    """Box Bawah 1: Equity & Cumulative PnL Growth Area Chart."""
    default_fig = create_equity_area_fig([])

    return html.Div([
        html.Div([
            html.Span("EQUITY & PNL GROWTH"),
            html.Span("+$0.00 REALIZED", id="hud-realized-tag", className="header-tag"),
        ], className="hud-panel-header"),
        html.Div([
            dcc.Graph(
                id="hud-equity-area-chart",
                figure=default_fig,
                style={"height": "var(--h-equity)", "width": "100%"},
                config={
                    "displayModeBar": False,
                    "responsive": True,
                    "scrollZoom": False,
                    "doubleClick": False,
                },
            ),
        ], className="hud-panel-body", style={"padding": "4px"}),
    ], className="hud-panel span-3 zone3-cell")


def create_trade_logs_panel() -> html.Div:
    """Box Bawah 2: Monospace Live Trade Log Stream."""
    return html.Div([
        html.Div([
            html.Span("TRADE LOGS · LIVE STREAM"),
            html.Span("RAW EXECUTION", className="header-tag"),
        ], className="hud-panel-header"),
        html.Div([
            html.Div(
                id="hud-trade-logs-stream",
                className="trade-log-stream",
                children=[
                    html.Div("NO EXECUTIONS YET", className="empty-note"),
                ],
            ),
        ], className="hud-panel-body"),
    ], className="hud-panel span-3 zone3-cell")


def create_live_analytics_panel() -> html.Div:
    """Box Bawah 3: Dual Sparklines & Strategy KPIs Table."""
    pnl_fig = create_mini_sparkline_fig([], GREEN_VINTAGE)
    vol_fig = create_mini_sparkline_fig([], BLUE_VINTAGE)

    return html.Div([
        html.Div([
            html.Span("LIVE ANALYTICS & STRATEGY KPIS"),
            html.Span("METRIC AUDIT", className="header-tag"),
        ], className="hud-panel-header"),
        html.Div([
            html.Div([
                html.Div([
                    html.Div("PNL VELOCITY", className="spark-label"),
                    dcc.Graph(
                        id="hud-sparkline-pnl",
                        figure=pnl_fig,
                        style={"height": "var(--h-spark)", "width": "100%"},
                        config={"displayModeBar": False, "responsive": True, "staticPlot": True},
                    ),
                ], style={"flex": 1}),
                html.Div([
                    html.Div("VOLUME PULSE", className="spark-label"),
                    dcc.Graph(
                        id="hud-sparkline-volume",
                        figure=vol_fig,
                        style={"height": "var(--h-spark)", "width": "100%"},
                        config={"displayModeBar": False, "responsive": True, "staticPlot": True},
                    ),
                ], style={"flex": 1}),
            ], className="spark-row"),

            html.Table([
                html.Tr([html.Td("SHARPE RATIO"), html.Td("0.00", className="text-green", id="hud-kpi-sharpe")]),
                html.Tr([html.Td("PROFIT FACTOR"), html.Td("0.00", className="text-green", id="hud-kpi-pf")]),
                html.Tr([html.Td("MAX DRAWDOWN"), html.Td("0.00%", className="text-green", id="hud-kpi-mdd")]),
                html.Tr([html.Td("AVG TRADE DURATION"), html.Td("0m 00s", id="hud-kpi-duration")]),
                html.Tr([html.Td("TOTAL SLIPPAGE"), html.Td("0.000%", id="hud-kpi-slippage")]),
                html.Tr([html.Td("TAKER FEES SAVED"), html.Td("$0.00", className="text-green", id="hud-kpi-fees")]),
            ], className="analytics-metric-table"),
        ], className="hud-panel-body"),
    ], className="hud-panel span-3 zone3-cell")


def create_footer_ticker() -> html.Div:
    """Footer running status ticker."""
    return html.Div([
        html.Span("INITIALIZING METRIC FEED · SCANNING TOP 10 VOLUME FUTURES...", id="hud-footer-ticker-text"),
        html.Span("0XF3CE25 CORE ENGINE v2.4 · ZERO LEAKAGE · SQLITE WAL LOCALHOST · ALL SYSTEMS NOMINAL"),
    ], className="hud-footer")
