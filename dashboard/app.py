"""
dashboard/app.py — Dash server utama dengan tema Retro Bloomberg Command Center HUD.
Dilengkapi penangan request basi untuk mencegah KeyError saat browser reload.
"""

import logging
from werkzeug.serving import WSGIRequestHandler
import dash
from dash import html, dcc
from flask import jsonify

# Bungkam seluruh log polling HTTP akses Werkzeug dan Flask di console terminal
logging.getLogger("werkzeug").setLevel(logging.ERROR)
logging.getLogger("flask").setLevel(logging.ERROR)
WSGIRequestHandler.log_request = lambda self, *args, **kwargs: None

from core.config import get_config
from core.logger import get_logger
from dashboard.layouts.hud import create_hud_layout
from dashboard.callbacks.update_callbacks import register_callbacks

logger = get_logger("dashboard")


def create_dash_app() -> dash.Dash:
    """Buat dan konfigurasi Dash app dengan layout Retro Bloomberg HUD."""
    cfg = get_config().dashboard

    app = dash.Dash(
        __name__,
        assets_folder="assets",
        title="0XF3CE25 // AI CRYPTO FUTURES TERMINAL",
        update_title="SYNCING...",
        suppress_callback_exceptions=True,
    )

    # Interceptor Flask untuk menangkap permintaan callback basi dari browser cache
    @app.server.errorhandler(KeyError)
    def handle_stale_callback_key_error(e):
        err_msg = str(e)
        if "Callback function not found for output" in err_msg:
            # Kembalikan respons kosong aman tanpa mencetak Traceback error di terminal
            return jsonify({"response": {}, "multi": True}), 200
        raise e

    # Layout utama: Retro Bloomberg Terminal HUD + Elemen Kompatibilitas Tersembunyi
    app.layout = html.Div([
        # Interval update real-time. 500ms karena data sudah didorong WebSocket
        # Hyperliquid (harga & orderbook sub-detik), jadi callback hanya membaca
        # dari memori tanpa menunggu jaringan.
        dcc.Interval(
            id="dashboard-interval",
            interval=500,
            n_intervals=0,
        ),

        # Interval lambat (60 detik) khusus panel yang isinya memang jarang
        # berubah: TRADE LOGS stream, ticker tape, dan footer. Memisahkannya dari
        # interval 500 ms membuat query berat (JOIN trades+positions+agent_logs)
        # tidak dijalankan 2x per detik.
        dcc.Interval(
            id="dashboard-slow-interval",
            interval=60000,
            n_intervals=0,
        ),

        # Kontainer HUD Utama
        html.Div(id="hud-main-content", children=create_hud_layout()),

        # Elemen kompatibilitas tersembunyi untuk seluruh tab lama
        html.Div([
            html.Div(id="header-balance"),
            html.Div(id="header-equity"),
            html.Div(id="header-pnl"),
            html.Div(id="header-positions"),
            dcc.Dropdown(id="chart-symbol", options=[]),
            dcc.Dropdown(id="chart-timeframe", options=[]),
            dcc.Graph(id="candlestick-chart"),
            html.Div(id="indicator-values"),
            html.Div(id="active-positions-table"),
            html.Div(id="trade-history-table"),
            html.Div(id="agent-logs-container"),
            dcc.Dropdown(id="agent-log-filter", options=[]),
            html.Div(id="sentiment-summary"),
            html.Div(id="news-feed-container"),
            html.Div(id="performance-stats"),
            dcc.Graph(id="equity-chart"),
            dcc.Graph(id="drawdown-chart"),
            html.Div(id="tab-content"),
            dcc.Tabs(id="main-tabs", children=[]),
        ], style={"display": "none"}),
    ], style={"backgroundColor": "#dfd5b8", "minHeight": "100vh"})

    # Daftarkan semua callback real-time (HUD + Legacy Fallback)
    register_callbacks(app)

    return app


def _silence_flask_banner():
    """
    Matikan banner " * Serving Flask app ..." dari Werkzeug.

    Dash 4 tidak punya `flask_banner_hidden`, dan banner itu dicetak lewat
    `click.echo` saat server start. Kita sudah mencetak "Running — dashboard
    http://..." sendiri lewat `stage_done()`, jadi mengulangnya cuma jadi
    dua baris untuk satu fakta yang sama.

    Set `TRADEBOT_BANNER=1` kalau banner wanted.
    """
    import os
    if os.environ.get("TRADEBOT_BANNER", "").strip() in ("1", "true", "True", "yes"):
        return
    # Banner Flask (" * Serving Flask app ...") lewat click.echo.
    try:
        import click
        click.echo = lambda *a, **k: None
    except Exception:
        pass
    # "Dash is running on ..." lewat logger internal Dash, bukan click.
    try:
        import logging as _logging
        _logging.getLogger("dash").setLevel(_logging.WARNING)
    except Exception:
        pass


def run_dashboard():
    """Jalankan dashboard server."""
    cfg = get_config().dashboard
    app = create_dash_app()

    # Pastikan log request Werkzeug tetap sunyi
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    WSGIRequestHandler.log_request = lambda self, *args, **kwargs: None

    logger.info(f"Retro Bloomberg Command Center HUD aktif di http://{cfg.host}:{cfg.port}")
    _silence_flask_banner()
    app.run(
        host=cfg.host,
        port=cfg.port,
        debug=cfg.debug,
        dev_tools_silence_routes_logging=True,
    )


if __name__ == "__main__":
    run_dashboard()
