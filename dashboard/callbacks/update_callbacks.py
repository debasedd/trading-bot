"""
dashboard/callbacks/update_callbacks.py — Callbacks real-time untuk Retro Bloomberg Command Center HUD.
Menghubungkan seluruh komponen HUD ke database lokal SQLite dan feed harga real-time.
Memuat fallback callback lengkap untuk mencegah KeyError pada browser tab lama.
"""

import math
import time
import json
import sqlite3
import numpy as np
import pandas as pd
from datetime import datetime, timezone
from dash import Input, Output, State, html
from dash.exceptions import PreventUpdate

from core.config import get_config
from core.logger import get_logger
from core.market_store import market_store
from analysis.probability_engine import (
    probability_engine,
    calculate_realized_volatility,
    calculate_order_flow_imbalance,
    calculate_technical_zscore,
)
from analysis.technical import TechnicalAnalyzer
from dashboard.layouts.hud_figures import (
    create_mini_candlestick_fig,
    create_convergence_fig,
    create_neural_net_fig,
    create_equity_area_fig,
    create_mini_sparkline_fig,
    GREEN_VINTAGE,
    BLUE_VINTAGE,
)
from dashboard.layouts.positions import create_positions_table, create_trade_history_table
from dashboard.layouts.agent_logs import create_log_entries
from dashboard.layouts.news_feed import create_sentiment_summary, create_news_items
from dashboard.layouts.performance import (
    create_performance_stats, create_equity_chart, create_drawdown_chart,
)
from dashboard.layouts.price_chart import create_candlestick_figure

logger = get_logger("dashboard.callbacks")

# Jumlah pesan WebSocket per channel pada pembacaan sebelumnya. Dipakai untuk
# menghitung LAJU pesan (selisih antar pembacaan), bukan total kumulatif yang
# akan terus naik dan mentok. Nilainya hanya hidup selama proses berjalan.
_neural_msg_prev: dict = {}

# Figure konstelasi terakhir yang berhasil dibangun. Dipakai sebagai
# fallback saat callback gagal, supaya error sesaat tidak menghapus
# seluruh token dari layar lalu memunculkannya kembali.
_neural_last_fig = None


def _set_neural_last_fig(fig):
    """Simpan figure terakhir untuk fallback."""
    global _neural_last_fig
    _neural_last_fig = fig
    return fig


def _get_sync_db():
    """Koneksi SQLite sinkron untuk Dash callbacks."""
    cfg = get_config()
    conn = sqlite3.connect(cfg.database_path)
    conn.row_factory = sqlite3.Row
    return conn


def derive_macro_bias(macro_rows) -> str:
    """
    Turunkan bias makro dari baris macro_data FRED yang tersimpan di database.
    Logika ambang identik dengan MacroFetcher.interpret_macro_context:
      FED_FUNDS_RATE > 5.0 bearish (+2) / < 3.0 bullish (+2)
      DXY            > 105 bearish (+1) / < 100 bullish (+1)
      VIX            > 25  bearish (+1) / < 15  bullish (+1)
    Net >= +2 -> BULLISH, <= -2 -> BEARISH, selain itu NEUTRAL.
    """
    bullish_score = 0
    bearish_score = 0
    for row in macro_rows:
        try:
            indicator = str(row["indicator"]).upper()
            value = float(row["value"])
        except (KeyError, TypeError, ValueError):
            continue

        if indicator == "FED_FUNDS_RATE":
            if value > 5.0:
                bearish_score += 2
            elif value < 3.0:
                bullish_score += 2
        elif indicator == "DXY":
            if value > 105:
                bearish_score += 1
            elif value < 100:
                bullish_score += 1
        elif indicator == "VIX":
            if value > 25:
                bearish_score += 1
            elif value < 15:
                bullish_score += 1

    net = bullish_score - bearish_score
    if net >= 2:
        return "BULLISH"
    if net <= -2:
        return "BEARISH"
    return "NEUTRAL"


def register_callbacks(app):
    """Daftarkan semua callback Retro Bloomberg HUD dan legacy fallback ke Dash app."""

    # =====================================================================
    # 0. CALLBACK KOMPATIBILITAS LENGKAP (ANTI-KEYERROR BROWSER CACHE)
    # =====================================================================
    @app.callback(
        [Output("header-balance", "children"),
         Output("header-equity", "children"),
         Output("header-pnl", "children"),
         Output("header-positions", "children")],
        [Input("dashboard-interval", "n_intervals")],
        prevent_initial_call=False,
    )
    def update_legacy_header(n):
        try:
            conn = _get_sync_db()
            acc = conn.execute("SELECT balance, initial_balance FROM account ORDER BY id DESC LIMIT 1").fetchone()
            bal = float(acc[0]) if acc else 10000.0
            init_b = float(acc[1]) if acc else 10000.0
            margin_row = conn.execute("SELECT COALESCE(SUM(margin), 0) FROM positions WHERE status = 'OPEN'").fetchone()
            open_margin = float(margin_row[0]) if margin_row else 0.0
            upnl_row = conn.execute("SELECT COALESCE(SUM(unrealized_pnl), 0) FROM positions WHERE status = 'OPEN'").fetchone()
            upnl = float(upnl_row[0]) if upnl_row else 0.0
            pos_cnt = conn.execute("SELECT COUNT(*) FROM positions WHERE status = 'OPEN'").fetchone()[0]
            conn.close()
            wallet_balance = bal + open_margin
            eq = wallet_balance + upnl
            pnl = eq - init_b
            pnl_sign = "+" if pnl >= 0 else ""
            return f"${wallet_balance:,.2f}", f"${eq:,.2f}", f"{pnl_sign}${pnl:,.2f}", str(pos_cnt)
        except Exception:
            return "$10,000.00", "$10,000.00", "+$0.00", "0"

    @app.callback(
        [Output("performance-stats", "children"),
         Output("equity-chart", "figure"),
         Output("drawdown-chart", "figure")],
        [Input("dashboard-interval", "n_intervals")],
        prevent_initial_call=False,
    )
    def update_legacy_performance(n):
        try:
            conn = _get_sync_db()
            acc = conn.execute("SELECT * FROM account ORDER BY id DESC LIMIT 1").fetchone()
            account = dict(acc) if acc else {}

            upnl_row = conn.execute(
                "SELECT COALESCE(SUM(unrealized_pnl), 0) FROM positions WHERE status = 'OPEN'"
            ).fetchone()
            upnl = float(upnl_row[0]) if upnl_row and upnl_row[0] is not None else 0.0

            margin_row = conn.execute(
                "SELECT COALESCE(SUM(margin), 0) FROM positions WHERE status = 'OPEN'"
            ).fetchone()
            open_margin = float(margin_row[0]) if margin_row and margin_row[0] is not None else 0.0

            pnl_rows = conn.execute(
                "SELECT realized_pnl FROM positions "
                "WHERE status IN ('CLOSED', 'LIQUIDATED') AND realized_pnl IS NOT NULL"
            ).fetchall()
            pnls = [float(r[0]) for r in pnl_rows]

            # DESC lalu dibalik: `ASC LIMIT 300` mengambil 300 baris
            # TERLAMA, sehingga kurvanya beku begitu history melewati 300.
            history = [
                dict(r) for r in conn.execute(
                    "SELECT equity FROM balance_history ORDER BY timestamp DESC LIMIT 300"
                ).fetchall()
            ][::-1]
            conn.close()

            balance = float(account.get("balance") or 10000.0)
            initial_balance = float(account.get("initial_balance") or 10000.0)
            wallet_balance = balance + open_margin
            equity = wallet_balance + upnl

            total_trades = len(pnls)
            wins = [p for p in pnls if p > 0]
            losses = [abs(p) for p in pnls if p < 0]
            win_rate = (len(wins) / total_trades * 100.0) if total_trades else 0.0

            gross_profit = sum(wins)
            gross_loss = sum(losses)
            if gross_loss > 0:
                profit_factor = gross_profit / gross_loss
            elif gross_profit > 0:
                profit_factor = gross_profit
            else:
                profit_factor = 0.0

            # Max drawdown dari riwayat equity riil; fallback ke nilai tersimpan di account
            max_drawdown = 0.0
            equity_series = [float(h["equity"]) for h in history if h.get("equity") is not None]
            if len(equity_series) >= 2:
                peak = equity_series[0]
                for eq in equity_series:
                    peak = max(peak, eq)
                    if peak > 0:
                        max_drawdown = max(max_drawdown, (peak - eq) / peak)
            elif account.get("max_drawdown"):
                max_drawdown = float(account["max_drawdown"])

            summary = {
                "balance": wallet_balance,
                "equity": equity,
                "initial_balance": initial_balance,
                "total_pnl": equity - initial_balance,
                "total_trades": total_trades,
                "win_rate": win_rate,
                "profit_factor": profit_factor,
                "max_drawdown": max_drawdown,
            }
            stats = create_performance_stats(summary)
            equity_fig = create_equity_chart(history)
            dd_fig = create_drawdown_chart(history)
            return stats, equity_fig, dd_fig
        except Exception as e:
            logger.debug(f"Error legacy performance: {e}")
            return html.Div(), create_equity_chart([]), create_drawdown_chart([])

    @app.callback(
        [Output("active-positions-table", "children"),
         Output("trade-history-table", "children")],
        [Input("dashboard-interval", "n_intervals")],
        prevent_initial_call=False,
    )
    def update_legacy_positions(n):
        return html.Div(), html.Div()

    @app.callback(
        [Output("sentiment-summary", "children"),
         Output("news-feed-container", "children")],
        [Input("dashboard-interval", "n_intervals")],
        prevent_initial_call=False,
    )
    def update_legacy_news(n):
        return html.Div(), html.Div()

    @app.callback(
        Output("agent-logs-container", "children"),
        [Input("dashboard-interval", "n_intervals")],
        prevent_initial_call=False,
    )
    def update_legacy_logs(n):
        return html.Div()

    @app.callback(
        Output("indicator-values", "children"),
        [Input("dashboard-interval", "n_intervals")],
        prevent_initial_call=False,
    )
    def update_legacy_indicators(n):
        return html.Div()

    @app.callback(
        Output("candlestick-chart", "figure"),
        [Input("dashboard-interval", "n_intervals")],
        prevent_initial_call=False,
    )
    def update_legacy_candlestick(n):
        """Chart legacy memakai lilin 1m riil dari database, bukan deret GBM sintetis."""
        try:
            conn = _get_sync_db()
            rows = conn.execute(
                "SELECT timestamp, open, high, low, close, volume FROM candles "
                "WHERE (symbol = ? OR symbol LIKE ?) AND timeframe = '1m' "
                "ORDER BY timestamp DESC LIMIT 120",
                ("BTC/USDT:USDT", "%BTC%"),
            ).fetchall()
            conn.close()

            if not rows:
                return create_mini_candlestick_fig(pd.DataFrame(), "BTC/USDT:USDT")

            df = pd.DataFrame([dict(r) for r in reversed(rows)])
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", errors="coerce")
            df = df.dropna(subset=["timestamp"]).sort_values("timestamp")
            df["ema_9"] = df["close"].ewm(span=9, adjust=False).mean()
            return create_mini_candlestick_fig(df, "BTC/USDT:USDT")
        except Exception as e:
            logger.debug(f"Error legacy candlestick: {e}")
            return create_mini_candlestick_fig(pd.DataFrame(), "BTC/USDT:USDT")

    @app.callback(
        Output("chart-symbol", "options"),
        [Input("dashboard-interval", "n_intervals")],
        prevent_initial_call=False,
    )
    def update_legacy_symbol_options(n):
        cfg = get_config()
        return [{"label": s.split("/")[0] + "/USDT", "value": s} for s in cfg.symbols]

    # =====================================================================
    # 1. TOP BAR: SPOT TICKERS, UTC CLOCK, LATENCY
    # =====================================================================
    @app.callback(
        [Output("hud-spot-btc", "children"),
         Output("hud-spot-eth", "children"),
         Output("hud-spot-sol", "children"),
         Output("hud-avg-edge", "children"),
         Output("hud-utc-clock", "children"),
         Output("hud-latency-badge", "children")],
        [Input("dashboard-interval", "n_intervals")],
    )
    def update_top_bar(n):
        t_start = time.perf_counter()
        utc_now = datetime.now(timezone.utc).strftime("UTC %H:%M:%S")

        # Coba ambil harga real-time sub-detik langsung dari MarketStore
        p_btc = market_store.get_price("BTC/USDT")
        p_eth = market_store.get_price("ETH/USDT")
        p_sol = market_store.get_price("SOL/USDT")

        trades_list = []

        try:
            conn = _get_sync_db()
            # Ambil posisi tertutup untuk menghitung Mathematical Trading Edge murni.
            # PnL terealisasi hidup di positions.realized_pnl — tabel trades tidak menyimpan PnL.
            closed_rows = conn.execute(
                "SELECT entry_price, quantity, realized_pnl FROM positions "
                "WHERE status IN ('CLOSED', 'LIQUIDATED') AND realized_pnl IS NOT NULL"
            ).fetchall()
            trades_list = [
                {"price": float(r[0] or 0.0), "quantity": float(r[1] or 0.0), "pnl": float(r[2])}
                for r in closed_rows
            ]

            if not p_btc:
                c_btc = conn.execute(
                    "SELECT close FROM candles WHERE symbol LIKE '%BTC%' ORDER BY timestamp DESC LIMIT 1"
                ).fetchone()
                if c_btc and c_btc[0]:
                    p_btc = float(c_btc[0])

            if not p_eth:
                c_eth = conn.execute(
                    "SELECT close FROM candles WHERE symbol LIKE '%ETH%' ORDER BY timestamp DESC LIMIT 1"
                ).fetchone()
                if c_eth and c_eth[0]:
                    p_eth = float(c_eth[0])

            if not p_sol:
                c_sol = conn.execute(
                    "SELECT close FROM candles WHERE symbol LIKE '%SOL%' ORDER BY timestamp DESC LIMIT 1"
                ).fetchone()
                if c_sol and c_sol[0]:
                    p_sol = float(c_sol[0])

            conn.close()
        except Exception as e:
            logger.debug(f"Error update topbar prices: {e}")

        btc_price = f"${p_btc:,.0f}" if p_btc else "--"
        eth_price = f"${p_eth:,.1f}" if p_eth else "--"
        sol_price = f"${p_sol:,.2f}" if p_sol else "--"

        # Ekspektasi Matematis (Ed Thorp Expectancy) murni tanpa sine wave
        real_edge = probability_engine.compute_mathematical_edge(trades_list)
        edge_sign = "+" if real_edge >= 0 else ""
        edge_val = f"{edge_sign}{real_edge:.2f}%"

        # Latensi riil polling proses
        elapsed_ms = max(int((time.perf_counter() - t_start) * 1000), 2)
        latency = f"{elapsed_ms}ms"

        return btc_price, eth_price, sol_price, edge_val, utc_now, latency

    # =====================================================================
    # 2. DROPDOWN PILIHAN SIMBOL TOP VOLUME
    # =====================================================================
    @app.callback(
        Output("hud-chart-symbol-select", "options"),
        [Input("dashboard-interval", "n_intervals")],
        [State("hud-chart-symbol-select", "options")],
    )
    def update_symbol_dropdown(n, current_options):
        try:
            cfg = get_config()
            options = [
                {"label": s.split("/")[0] + "/USDT", "value": s}
                for s in cfg.symbols
            ]
            if current_options and len(current_options) == len(options):
                raise PreventUpdate
            return options
        except Exception:
            raise PreventUpdate

    # =====================================================================
    # 3. BOX 2: MINI CANDLESTICK & ORDERBOOK LADDER TAPE (ANTI-WHITEBOX & REAL-TIME)
    # =====================================================================
    @app.callback(
        [Output("hud-candlestick-chart", "figure"),
         Output("hud-chart-price-display", "children"),
         Output("hud-orderbook-tape", "children")],
        [Input("dashboard-interval", "n_intervals"),
         Input("hud-chart-symbol-select", "value")],
    )
    def update_mini_candlestick_and_tape(n, symbol):
        try:
            import pandas as pd
            conn = _get_sync_db()

            base_sym = symbol.split("/")[0].split(":")[0].upper() if symbol else "BTC"
            # HANYA lilin 1m. Tidak ada fallback yang menjatuhkan filter timeframe:
            # mencampur 1m/5m/1h pada satu sumbu waktu menghasilkan lilin yang
            # tumpang tindih dan itulah yang membuat chart tampak rusak.
            # Jendela 400 menit, bukan 120. Ada dua alasan:
            #   1. Pan mundur ala TradingView butuh riwayat. Dengan 120 lilin
            #      hanya ada 2 jam ke belakang dan menggeser mouse langsung
            #      mentok.
            #   2. Pada sumbu kategori, daftar kategori yang bergeser setiap
            #      menit memaksa Plotly membuang posisi pan/zoom user. Dengan
            #      jendela yang jauh lebih lebar dari yang ditampilkan, kepala
            #      daftar praktis tidak bergerak, jadi tidak ada yang di-reset.
            cursor = conn.execute(
                "SELECT timestamp, open, high, low, close, volume "
                "FROM candles WHERE (symbol = ? OR symbol LIKE ?) AND timeframe = '1m' "
                "ORDER BY timestamp DESC LIMIT 400",
                (symbol, f"%{base_sym}%"),
            )
            rows = cursor.fetchall()

            conn.close()

            if not rows:
                # Tidak ada lilin di DB: tampilkan empty-state jujur, bukan deret
                # GBM sintetis. Harga juga harus jujur — kalau market_store tidak
                # punya harga, kita TIDAK mengarang angka dari tabel statis.
                # Angka karangan di layar terlihat benar tapi salah, dan itu
                # lebih berbahaya daripada menampilkan "--".
                cur_price = market_store.get_price(symbol)
                if cur_price and cur_price >= 1000:
                    price_str = f"${cur_price:,.2f}"
                elif cur_price:
                    price_str = f"${cur_price:.4f}"
                else:
                    price_str = "--"
                return (
                    create_mini_candlestick_fig(pd.DataFrame(), symbol),
                    price_str,
                    [html.Div(
                        "AWAITING 1M CANDLE INGEST // NO SYNTHETIC DATA",
                        style={
                            "textAlign": "center",
                            "fontSize": "10px",
                            "fontFamily": "'Courier New', monospace",
                            "color": "#665e4e",
                            "padding": "16px 0",
                        },
                    )],
                )
            else:
                df = pd.DataFrame([dict(r) for r in reversed(rows)])
                df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", errors="coerce")
                df = df.dropna(subset=["timestamp"]).drop_duplicates(subset=["timestamp"]).sort_values("timestamp")

                # TIDAK ada resample/ffill. Menambal menit yang tidak punya data
                # akan menciptakan lilin doji datar yang tidak pernah ada di pasar —
                # itu data karangan. Bila ada menit bolong, lilinnya memang bolong;
                # gap dihilangkan di sisi Plotly lewat rangebreaks, bukan dengan
                # mengarang OHLC.
                cur_price = float(df["close"].iloc[-1])

            # Lilin live dari WebSocket Hyperliquid: OHLC penuh untuk lilin yang sedang
            # terbentuk, didorong setiap ada trade. Lebih akurat daripada menambal
            # close harga tick ke lilin DB yang sudah tertutup.
            live_candle = market_store.get_live_candle(symbol)
            if live_candle and not df.empty:
                ts = pd.to_datetime(live_candle["timestamp"], unit="ms", errors="coerce")
                if pd.notna(ts):
                    cur_price = float(live_candle["close"])
                    if ts in df["timestamp"].values:
                        # Lilin yang sama: perbarui OHLC-nya di tempat
                        idx = df.index[df["timestamp"] == ts][-1]
                        df.loc[idx, ["open", "high", "low", "close", "volume"]] = [
                            float(live_candle["open"]),
                            float(live_candle["high"]),
                            float(live_candle["low"]),
                            float(live_candle["close"]),
                            float(live_candle["volume"]),
                        ]
                    else:
                        # Lilin baru belum ada di DB: sisipkan di ujung
                        df = pd.concat([df, pd.DataFrame([{
                            "timestamp": ts,
                            "open": float(live_candle["open"]),
                            "high": float(live_candle["high"]),
                            "low": float(live_candle["low"]),
                            "close": float(live_candle["close"]),
                            "volume": float(live_candle["volume"]),
                        }])], ignore_index=True)

            # Lilin menit yang SEDANG berjalan.
            #
            # Bila channel `candle` WS belum mengirim, lilin berjalan tetap ada di
            # pasar — dibentuk dari tick harga riil yang masuk. Yang TIDAK boleh
            # dilakukan adalah menambal lilin yang sudah tutup dengan harga sekarang:
            # itu mengubah sejarah dan membuat lilin terakhir tampak melonjak.
            live_price = market_store.get_price(symbol)
            if live_price and live_price > 0:
                cur_price = live_price
                if not df.empty:
                    now_minute = pd.Timestamp.now(tz="UTC").floor("1min").tz_localize(None)
                    last_ts = df["timestamp"].iloc[-1]
                    if last_ts == now_minute:
                        # Lilin menit ini sudah ada di chart (dari WS atau DB): perluas saja
                        last_idx = df.index[-1]
                        df.loc[last_idx, "close"] = live_price
                        df.loc[last_idx, "high"] = max(float(df.loc[last_idx, "high"]), live_price)
                        df.loc[last_idx, "low"] = min(float(df.loc[last_idx, "low"]), live_price)
                    elif last_ts < now_minute:
                        # Menit baru belum tercatat: mulai lilin dari tick pertama yang riil
                        df = pd.concat([df, pd.DataFrame([{
                            "timestamp": now_minute,
                            "open": live_price,
                            "high": live_price,
                            "low": live_price,
                            "close": live_price,
                            "volume": 0.0,
                        }])], ignore_index=True)

            # Hitung EMA 9 secara dinamis di pandas (kolom ema_9 tidak disimpan di SQLite)
            df["ema_9"] = df["close"].ewm(span=9, adjust=False).mean()

            fig = create_mini_candlestick_fig(df, symbol)

            if cur_price >= 1000:
                price_str = f"${cur_price:,.2f}"
            else:
                price_str = f"${cur_price:.4f}"

            # Orderbook L2 asli dari exchange (Hyperliquid: 20 level per sisi).
            # Bila belum ada snapshot, tampilkan status menunggu — bukan depth sintetis.
            ob = market_store.get_order_book(symbol)

            if not ob or not ob.get("bids") or not ob.get("asks"):
                waiting = [html.Div(
                    "AWAITING L2 ORDERBOOK SNAPSHOT // HYPERLIQUID WS",
                    style={
                        "textAlign": "center",
                        "fontSize": "10px",
                        "fontFamily": "'Courier New', monospace",
                        "color": "#665e4e",
                        "padding": "16px 0",
                    },
                )]
                return fig, price_str, waiting

            asks = ob["asks"]
            bids = ob["bids"]
            spread = float(ob.get("spread") or 0.0)
            exchange = str(ob.get("exchange", "exchange")).upper()

            def fmt_px(p):
                return f"${p:,.2f}" if p >= 10 else f"${p:.4f}"

            # Agregat 5 level teratas per sisi untuk bar depth proporsional
            ask_depth5 = sum(float(a[1]) for a in asks[:5])
            bid_depth5 = sum(float(b[1]) for b in bids[:5])
            max_depth = max(ask_depth5, bid_depth5, 1e-9)

            tape_rows = []

            # 5 baris ASK, harga tertinggi di atas lalu mendekati spread di bawah
            for ask_item in reversed(asks[:5]):
                ask_p, ask_s = float(ask_item[0]), float(ask_item[1])
                bar_pct = (ask_s / max_depth) * 100.0
                tape_rows.append(html.Div([
                    html.Span(f"ASK {fmt_px(ask_p)}", className="ob-price"),
                    html.Span(f"{ask_s:,.4f}", className="ob-size"),
                    html.Div(className="ob-bar ob-bar-ask", style={"width": f"{bar_pct:.1f}%"}),
                    html.Span(f"${ask_p * ask_s:,.0f}", className="ob-total"),
                ], className="orderbook-row ask"))

            # Baris spread: nilai + sumber bursa asli
            spread_pct = (spread / cur_price * 100) if cur_price > 0 else 0.0
            s_fmt = f"${spread:,.2f}" if spread >= 0.01 else f"${spread:.4f}"
            tape_rows.append(html.Div(
                f"SPREAD {s_fmt} ({spread_pct:.3f}%) // {exchange} L2",
                className="orderbook-spread",
            ))

            # 5 baris BID, harga tertinggi (dekat spread) di atas
            for bid_item in bids[:5]:
                bid_p, bid_s = float(bid_item[0]), float(bid_item[1])
                bar_pct = (bid_s / max_depth) * 100.0
                tape_rows.append(html.Div([
                    html.Span(f"BID {fmt_px(bid_p)}", className="ob-price"),
                    html.Span(f"{bid_s:,.4f}", className="ob-size"),
                    html.Div(className="ob-bar ob-bar-bid", style={"width": f"{bar_pct:.1f}%"}),
                    html.Span(f"${bid_p * bid_s:,.0f}", className="ob-total"),
                ], className="orderbook-row bid"))

            return fig, price_str, tape_rows

        except Exception as e:
            logger.error(f"Error update mini chart & tape: {e}")
            raise PreventUpdate

    # =====================================================================
    # 4. BOX 1: WALLET & REAL-TIME PNL OVERVIEW
    # =====================================================================
    @app.callback(
        [Output("hud-giant-pnl", "children"),
         Output("hud-giant-pnl", "className"),
         Output("hud-wallet-sub", "children"),
         Output("hud-stat-balance", "children"),
         Output("hud-stat-equity", "children"),
         Output("hud-stat-winrate", "children"),
         Output("hud-stat-risk", "children")],
        [Input("dashboard-interval", "n_intervals")],
    )
    def update_wallet_overview(n):
        try:
            conn = _get_sync_db()
            acc_row = conn.execute("SELECT * FROM account ORDER BY id DESC LIMIT 1").fetchone()
            account = dict(acc_row) if acc_row else {"balance": 10000.0, "initial_balance": 10000.0}

            pos_rows = conn.execute("SELECT * FROM positions WHERE status = 'OPEN'").fetchall()
            positions = [dict(r) for r in pos_rows]

            closed_rows = conn.execute(
                "SELECT realized_pnl FROM positions "
                "WHERE status IN ('CLOSED', 'LIQUIDATED') AND realized_pnl IS NOT NULL"
            ).fetchall()
            conn.close()

            balance = float(account.get("balance", 10000.0))
            initial_balance = float(account.get("initial_balance", 10000.0))

            # Hitung live unrealized PnL dari posisi terbuka dengan live market store
            total_upnl = 0.0
            total_margin = 0.0
            for p in positions:
                sym = p.get("symbol", "")
                entry = float(p.get("entry_price") or 0.0)
                amount = float(p.get("quantity") or 0.0)
                side = p.get("side", "LONG").upper()
                total_margin += float(p.get("margin") or 0.0)

                cur_p = market_store.get_price(sym)
                if not cur_p or cur_p <= 0:
                    cur_p = entry

                if cur_p > 0 and entry > 0 and amount > 0:
                    if side == "LONG":
                        pos_upnl = (cur_p - entry) * amount
                    else:
                        pos_upnl = (entry - cur_p) * amount
                else:
                    pos_upnl = float(p.get("unrealized_pnl") or 0.0)
                total_upnl += pos_upnl

            # Perhitungan akuntansi futures nyata:
            # Saldo modal dompet (Wallet Balance) = Saldo kas bebas (Available) + Margin terkunci
            wallet_balance = balance + total_margin
            equity = wallet_balance + total_upnl
            total_pnl = equity - initial_balance

            pnl_sign = "+" if total_pnl >= 0 else ""
            giant_pnl_text = f"{pnl_sign}${total_pnl:,.2f}"
            pnl_class = "giant-pnl text-green" if total_pnl >= 0 else "giant-pnl text-red"

            # Hitung win rate dan jumlah eksekusi murni dari riwayat transaksi riil
            trades_count = len(closed_rows)
            wins = sum(1 for r in closed_rows if r[0] is not None and float(r[0]) > 0)
            win_rate = (wins / trades_count * 100) if trades_count > 0 else 0.0

            sub_text = f"{trades_count:,} FILLS // {len(positions)} OPEN POSITIONS"

            # Rasio margin untuk penilaian risiko drawdown riil
            margin_ratio = (total_margin / equity) if equity > 0 else 0.0
            risk_score = min(margin_ratio * 10, 10.0)
            risk_status = "SAFE" if risk_score < 3.0 else ("MODERATE" if risk_score < 6.0 else "HIGH")
            risk_text = f"DRAWDOWN RISK: {risk_score:.1f}/10 {risk_status}"

            return (
                giant_pnl_text,
                pnl_class,
                sub_text,
                f"${wallet_balance:,.2f}",
                f"${equity:,.2f}",
                f"{win_rate:.1f}%",
                risk_text,
            )
        except Exception as e:
            logger.debug(f"Error wallet overview: {e}")
            raise PreventUpdate

    # =====================================================================
    # 5. BOX 3: POSISI AKTIF (COMPACT GRID REAL-TIME)
    # =====================================================================
    @app.callback(
        [Output("hud-positions-table", "children"),
         Output("hud-positions-count-badge", "children")],
        [Input("dashboard-interval", "n_intervals")],
    )
    def update_positions_grid(n):
        try:
            conn = _get_sync_db()
            cursor = conn.execute("SELECT * FROM positions WHERE status = 'OPEN' ORDER BY id DESC")
            positions = [dict(r) for r in cursor.fetchall()]
            conn.close()

            if not positions:
                empty_msg = html.Div(
                    "NO ACTIVE POSITIONS // SCANNING TOP 10 FUTURES...",
                    style={
                        "color": "#665e4e",
                        "fontSize": "10px",
                        "fontFamily": "'Share Tech Mono', 'Courier New', monospace",
                        "padding": "16px 8px",
                        "textAlign": "center",
                    },
                )
                return [empty_msg], "ACTIVE (0)"

            items = []
            for p in positions:
                sym = p.get("symbol", "")
                sym_short = sym.split("/")[0] + "/USDT"
                side = p.get("side", "LONG").upper()
                side_color = "text-green" if side == "LONG" else "text-red"
                entry = float(p.get("entry_price") or 0.0)
                amount = float(p.get("quantity") or 0.0)

                # Update live unrealized PnL dari harga pasar real-time
                cur_p = market_store.get_price(sym)
                if not cur_p or cur_p <= 0:
                    cur_p = entry

                if cur_p > 0 and entry > 0 and amount > 0:
                    if side == "LONG":
                        upnl = (cur_p - entry) * amount
                    else:
                        upnl = (entry - cur_p) * amount
                else:
                    upnl = float(p.get("unrealized_pnl") or 0.0)

                upnl_sign = "+" if upnl >= 0 else ""
                upnl_color = "text-green" if upnl >= 0 else "text-red"
                liq = float(p.get("liquidation_price") or 0.0)
                liq_str = f"LIQ ${liq:,.1f}" if liq > 0 else "LIQ SAFE"

                items.append(html.Div([
                    html.Span(sym_short, className="position-symbol-chip"),
                    html.Span(f"{side} {p.get('leverage', 5)}X", className=side_color),
                    html.Span(f"{upnl_sign}${upnl:,.2f}", className=upnl_color),
                    html.Span(liq_str, style={"color": "#666"}),
                ], className="position-compact-row"))

            badge_text = f"ACTIVE ({len(positions)})"
            return items, badge_text

        except Exception as e:
            logger.debug(f"Error positions grid: {e}")
            raise PreventUpdate

    # =====================================================================
    # 6. BOX 4: DIRECTION SCANNER (ENSEMBLE LONG/SHORT MULTI-AGEN)
    #
    # Callback ini HANYA membaca snapshot yang sudah dihitung ensemble agent.
    # Sebelumnya callback ini menghitung ulang indikator teknikal, order flow,
    # sentimen, ML, dan makro pada SETIAP tick 500ms — kerja mahal yang
    # sekaligus membuat angka HUD tidak pernah bisa sama dengan angka yang
    # dipakai DecisionAgent. Sekarang keduanya membaca baris yang sama.
    # =====================================================================
    @app.callback(
        [Output("hud-scanner-long-odds", "children"),
         Output("hud-scanner-short-odds", "children"),
         Output("hud-scanner-consensus", "children"),
         Output("hud-scanner-consensus", "className"),
         Output("hud-scanner-agent-strip", "children"),
         Output("hud-convergence-chart", "figure")],
        [Input("dashboard-interval", "n_intervals"),
         Input("hud-chart-symbol-select", "value")],
    )
    def update_probability_scanner(n, symbol):
        try:
            symbol = symbol or "BTC/USDT:USDT"
            conn = _get_sync_db()
            try:
                row = conn.execute(
                    "SELECT prob_long, prob_short, direction, confidence, "
                    "       agent_breakdown, diffusion, created_at "
                    "FROM direction_snapshots WHERE symbol = ? "
                    "ORDER BY id DESC LIMIT 1",
                    (symbol,),
                ).fetchone()
            finally:
                conn.close()

            if not row:
                # Belum ada siklus ensemble untuk simbol ini: tampilkan
                # empty-state jujur, bukan angka karangan.
                return (
                    "--", "--", "AWAITING",
                    "odds-consensus-badge stance-neutral",
                    [html.Span("BELUM ADA SNAPSHOT", className="agent-chip abstained")],
                    create_convergence_fig(),
                )

            prob_long = float(row["prob_long"])
            prob_short = float(row["prob_short"])
            direction = str(row["direction"] or "NEUTRAL").upper()
            confidence = float(row["confidence"])

            # Verdict per agen untuk strip ringkasan.
            breakdown = []
            if row["agent_breakdown"]:
                try:
                    breakdown = json.loads(row["agent_breakdown"]) or []
                except (TypeError, ValueError):
                    breakdown = []

            chips = []
            n_agree = 0
            n_active = 0
            for v in breakdown:
                v_dir = str(v.get("direction", "NEUTRAL")).upper()
                v_conf = float(v.get("confidence", 0.0))
                name = str(v.get("agent", "?"))[:6].upper()
                if v_dir == "NEUTRAL":
                    chips.append(html.Span(
                        f"{name}:--", className="agent-chip abstained",
                        title=f"{name}: tidak ada data",
                    ))
                    continue
                n_active += 1
                if v_dir == direction:
                    n_agree += 1
                stance = "long" if v_dir == "LONG" else "short"
                chips.append(html.Span(
                    f"{name}:{v_dir[0]}{v_conf * 100:.0f}",
                    className=f"agent-chip stance-{stance}",
                    title=f"{name} -> {v_dir} {v_conf:.0%}",
                ))

            if not chips:
                chips = [html.Span(
                    "NO AGENT DATA", className="agent-chip abstained",
                )]

            if direction == "LONG":
                consensus_txt = f"LONG {confidence:.0%} · {n_agree}/{n_active}"
                consensus_cls = "odds-consensus-badge stance-long"
            elif direction == "SHORT":
                consensus_txt = f"SHORT {confidence:.0%} · {n_agree}/{n_active}"
                consensus_cls = "odds-consensus-badge stance-short"
            else:
                consensus_txt = f"NEUTRAL {confidence:.0%}"
                consensus_cls = "odds-consensus-badge stance-neutral"

            diffusion = None
            if row["diffusion"]:
                try:
                    diffusion = json.loads(row["diffusion"])
                except (TypeError, ValueError):
                    diffusion = None

            fig = create_convergence_fig(diffusion_data=diffusion)

            return (
                f"{prob_long:.2f}",
                f"{prob_short:.2f}",
                consensus_txt,
                consensus_cls,
                chips,
                fig,
            )

        except Exception as e:
            logger.debug(f"Error direction scanner: {e}")
            raise PreventUpdate

    # =====================================================================
    # 7. ROW 3: SNIPE NEURAL NET — KONSTELASI BERBASIS DATA NYATA
    #
    # Tiga sumber riil yang sebelumnya tidak tersambung:
    #   - tabel signals          -> warna & ukuran simpul token
    #   - tabel agent_logs       -> denyut simpul agen (baris log per menit)
    #   - market_store + WS feed -> perubahan harga token & laju pesan channel
    #
    # Dua hal yang harus benar agar animasinya jujur:
    #   1. Yang dipakai adalah LAJU, bukan total kumulatif. message_counts
    #      Hyperliquid terus bertambah seumur koneksi, jadi memakai angkanya
    #      langsung akan mentok di nilai maksimum setelah beberapa menit dan
    #      grafik berhenti bergerak.
    #   2. Normalisasi dilakukan RELATIF terhadap kanal tersibuk, bukan terhadap
    #      ambang tetap. Tanpa ini, kanal yang jauh lebih ramai membuat semua
    #      kanal lain tampak nol.
    # =====================================================================
    @app.callback(
        Output("hud-neural-graph", "figure"),
        [Input("dashboard-interval", "n_intervals")],
    )
    def update_neural_net(n):
        """Seluruh simpul dan sisi dimodulasi metrik riil, bukan animasi sintetis."""
        try:
            conn = _get_sync_db()
            sig_rows = conn.execute(
                "SELECT symbol, direction, confidence FROM signals "
                "WHERE id IN (SELECT MAX(id) FROM signals GROUP BY symbol)"
            ).fetchall()

            # Denyut agen: jumlah baris log dalam 60 detik terakhir per agen.
            agent_rows = conn.execute(
                "SELECT agent_name, COUNT(*) AS n FROM agent_logs "
                "WHERE timestamp >= datetime('now', '-60 seconds') "
                "GROUP BY agent_name"
            ).fetchall()
            conn.close()

            # Normalisasi relatif: agen teraktif = 1.0, sisanya proporsional.
            # Threshold 5 baris/menit mencegah satu baris log sesekali terlihat
            # seperti aktivitas penuh.
            raw_agent = {str(r["agent_name"]): float(r["n"]) for r in agent_rows}
            peak_agent = max(raw_agent.values()) if raw_agent else 0.0
            activity_map = {
                name: min(cnt / peak_agent, 1.0)
                for name, cnt in raw_agent.items()
            } if peak_agent >= 5.0 else {}

            signal_map = {}
            for r in sig_rows:
                base_sym = str(r["symbol"] or "").split("/")[0].split(":")[0].upper()
                if not base_sym:
                    continue
                # Perubahan harga 60 detik terakhir dari riwayat tick riil
                signal_map[base_sym] = {
                    "direction": r["direction"],
                    "confidence": r["confidence"],
                    "price_change": market_store.get_price_change(
                        f"{base_sym}/USDT:USDT", seconds=60.0
                    ),
                }

            # Laju pesan WebSocket per channel: selisih jumlah pesan sejak
            # pembacaan sebelumnya, dinormalisasi relatif terhadap kanal tersibuk.
            flow_map = {}
            try:
                from data.price_feed import get_price_feed
                feed = get_price_feed()
                if feed is not None:
                    counts = feed.hyperliquid.get_status().get("message_counts") or {}
                    rates = {}
                    for ch, total in counts.items():
                        prev = _neural_msg_prev.get(ch)
                        if prev is not None and total >= prev:
                            rates[ch] = float(total - prev)
                        else:
                            # Pembacaan pertama, atau koneksi WS baru saja di-reset
                            rates[ch] = float(total) if prev is None else 0.0
                        _neural_msg_prev[ch] = total
                    peak = max(rates.values()) if rates else 0.0
                    if peak > 0:
                        flow_map = {ch: v / peak for ch, v in rates.items()}
            except Exception as e:
                logger.debug(f"Status feed tidak tersedia untuk neural net: {e}")

            fig = create_neural_net_fig(
                signal_map=signal_map,
                activity_map=activity_map,
                flow_map=flow_map,
            )
            _set_neural_last_fig(fig)
            return fig
        except Exception as e:
            logger.debug(f"Error neural net: {e}")
            # Fallback harus memakai figure terakhir yang diketahui, bukan
            # `signal_map={}` kosong. Figure kosong membuat semua token
            # menghilang seketika lalu muncul lagi — kedipan yang jauh lebih
            # mengganggu daripada data yang sedikit tertunda.
            if _neural_last_fig is not None:
                return _neural_last_fig
            return create_neural_net_fig(signal_map={})

    # =====================================================================
    # 8. ROW 4: EQUITY GROWTH AREA CHART (ANTI-WHITEBOX)
    # =====================================================================
    @app.callback(
        [Output("hud-realized-tag", "children"),
         Output("hud-equity-area-chart", "figure")],
        [Input("dashboard-interval", "n_intervals")],
    )
    def update_equity_area(n):
        try:
            conn = _get_sync_db()
            # DESC dulu, baru dibalik di Python.
            #
            # `ORDER BY timestamp ASC LIMIT 300` mengambil 300 snapshot
            # TERLAMA — begitu history melewati 300 baris, kurvanya berhenti
            # bergerak sama sekali dan terlihat datar padahal bot sedang
            # untung/rugian. Grafik equity yang beku adalah kebohongan.
            history = [
                dict(r) for r in conn.execute(
                    "SELECT * FROM balance_history ORDER BY timestamp DESC LIMIT 300"
                ).fetchall()
            ][::-1]
            realized_row = conn.execute(
                "SELECT COALESCE(SUM(realized_pnl), 0) FROM positions "
                "WHERE status IN ('CLOSED', 'LIQUIDATED')"
            ).fetchone()
            realized_val = float(realized_row[0]) if (realized_row and realized_row[0] is not None) else 0.0

            acc_row = conn.execute(
                "SELECT balance, initial_balance FROM account ORDER BY id DESC LIMIT 1"
            ).fetchone()
            initial_balance = float(acc_row[1]) if acc_row and acc_row[1] else 10000.0

            if not history:
                margin_row = conn.execute("SELECT COALESCE(SUM(margin), 0) FROM positions WHERE status = 'OPEN'").fetchone()
                open_margin = float(margin_row[0]) if margin_row and margin_row[0] else 0.0
                current_bal = (float(acc_row[0]) if acc_row and acc_row[0] else initial_balance) + open_margin
                history = [
                    {"timestamp": datetime.now(timezone.utc).isoformat(), "balance": current_bal, "equity": current_bal}
                ]
            conn.close()

            fig = create_equity_area_fig(history, initial_balance=initial_balance)
            sign = "+" if realized_val >= 0 else ""
            tag = f"{sign}${realized_val:,.2f} REALIZED"
            return tag, fig
        except Exception as e:
            logger.debug(f"Error equity area: {e}")
            raise PreventUpdate

    # =====================================================================
    # 9. ROW 4: TRADE LOGS LIVE STREAM & RUNNING TICKER
    # =====================================================================
    # Dipisah dari interval cepat: panel log dan footer cukup diperbarui sekali
    # per menit. Dash menjalankan tiap callback mengikuti Input-nya sendiri, jadi
    # output lambat TIDAK boleh tetap berbagi Input dengan interval 500 ms —
    # kalau ikut, ia akan tetap di-query 2x per detik.
    @app.callback(
        [Output("hud-trade-logs-stream", "children"),
         Output("hud-live-ticker-text", "children"),
         Output("hud-footer-ticker-text", "children")],
        [Input("dashboard-slow-interval", "n_intervals")],
    )
    def update_trade_stream_and_marquee(n):
        try:
            conn = _get_sync_db()
            # PnL tidak ada di tabel trades — di-JOIN dari positions.realized_pnl via position_id
            trades = [
                dict(r) for r in conn.execute(
                    "SELECT t.id, t.symbol, t.side, t.price, t.quantity, t.fee, "
                    "t.fee_type, t.trade_type, t.executed_at, "
                    "p.realized_pnl AS pnl, p.status AS position_status "
                    "FROM trades t LEFT JOIN positions p ON p.id = t.position_id "
                    "ORDER BY t.executed_at DESC LIMIT 20"
                ).fetchall()
            ]
            agent_logs = [
                dict(r) for r in conn.execute(
                    "SELECT * FROM agent_logs ORDER BY timestamp DESC LIMIT 15"
                ).fetchall()
            ]
            recent_signals = [
                dict(r) for r in conn.execute(
                    "SELECT symbol, direction, confidence FROM signals ORDER BY id DESC LIMIT 5"
                ).fetchall()
            ]
            acc_row = conn.execute("SELECT balance, total_pnl, total_trades, winning_trades FROM account ORDER BY id DESC LIMIT 1").fetchone()
            conn.close()

            rows = []
            marquee_snippets = []

            for t in trades[:12]:
                # executed_at tersimpan sebagai TEXT datetime('now') UTC, bukan epoch-ms
                raw_ts = str(t.get("executed_at") or "")
                try:
                    dt = pd.to_datetime(raw_ts, utc=True).strftime("%H:%M:%S")
                except Exception:
                    dt = raw_ts[-8:] or "--:--:--"

                side = str(t.get("side", "BUY")).upper()
                # `badge-green` / `badge-red` hanya untuk badge "LIVE" di
                # topbar — kelas itu punya denyut 1.8s. Memakainya di sini
                # membuat setiap chip BUY historis ikut berdenyut, termasuk
                # fill yang sudah 3 jam lalu. Chip log memakai kelas sendiri
                # yang datar dan warnanya berasal dari token semantik.
                badge_class = "log-chip log-chip-buy" if side == "BUY" else "log-chip log-chip-sell"
                sym = str(t.get("symbol", "?")).split("/")[0] + "/USDT"

                # trade_type & fee_type adalah kolom riil di tabel trades
                trade_type = str(t.get("trade_type") or "").upper()
                fee_type = str(t.get("fee_type") or "TAKER").upper()
                label = trade_type if trade_type else fee_type

                # PnL hanya bermakna setelah posisi tertutup; posisi terbuka ditandai OPEN
                pos_status = str(t.get("position_status") or "").upper()
                if t.get("pnl") is not None and pos_status in ("CLOSED", "LIQUIDATED"):
                    pnl = float(t["pnl"])
                    pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
                    pnl_color = "text-green" if pnl >= 0 else "text-red"
                else:
                    pnl_str = "OPEN"
                    pnl_color = "text-muted"

                rows.append(html.Div([
                    html.Span(dt, className="log-time"),
                    html.Span(f"{side} {label}", className=badge_class),
                    html.Span(sym, style={"fontWeight": "bold"}),
                    html.Span(f"{float(t.get('quantity') or 0):.3f} @ ${float(t.get('price') or 0):,.2f}"),
                    html.Span(pnl_str, className=pnl_color, style={"fontWeight": "bold"}),
                ], className="trade-log-row"))

                marquee_snippets.append(f"{sym} {side} FILL @ ${float(t.get('price') or 0):,.2f}")

            for al in agent_logs[:8]:
                dt = str(al.get("timestamp") or "")[-8:]
                agent = al.get("agent_name", "AGENT")
                # Kolom riil adalah `reasoning`, bukan `message`
                thought = str(al.get("reasoning") or "")[:45]
                rows.append(html.Div([
                    html.Span(dt, className="log-time"),
                    html.Span(agent[:8].upper(), className="log-chip badge-black"),
                    html.Span(thought, style={"color": "#333"}),
                ], className="trade-log-row"))

            if not rows:
                now_str = datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3]
                rows = [
                    html.Div([
                        html.Span(now_str, className="log-time"),
                        html.Span("LISTENING", className="log-chip badge-black"),
                        html.Span("AUTONOMOUS AGENTS", style={"fontWeight": "bold"}),
                        html.Span("Event bus active · Scanning Top 10 Futures orderbooks"),
                    ], className="trade-log-row"),
                ]

            for s in recent_signals:
                c_pct = f"{float(s.get('confidence', 0)):.0%}"
                marquee_snippets.append(f"{s['symbol'].split('/')[0]} {s['direction']} (CONF {c_pct})")

            # Tambahkan harga live ticker
            all_prices = market_store.get_all_prices()
            for sym, p in list(all_prices.items())[:5]:
                b_sym = sym.split("/")[0].split(":")[0]
                marquee_snippets.append(f"{b_sym} ${p:,.2f}")

            base_marquee = (
                " · ".join(marquee_snippets)
                if marquee_snippets else
                "AI AGENTS SYNCHRONIZED · TOP 10 VOLUME FUTURES SCANNER ACTIVE · "
                "ZERO KEY REST API INGEST · REALTIME L2 ORDERBOOK DEPTH MONITORED · "
                "BAYESIAN MULTI-FACTOR LOGIT PROBABILITY MODEL LOADED"
            )
            # Dulu digandakan (`base + " · " + base`) supaya animasi
            # 35 detik tidak terlihat mentok saat teks habis. Animasi itu
            # sudah dihapus, jadi penggandaan ini hanya menampilkan isi
            # dua kali.
            marquee_text = base_marquee

            # Footer status ticker teks dinamis
            total_trades = acc_row[2] if acc_row else len(trades)
            win_count = acc_row[3] if acc_row else sum(
                1 for t in trades
                if t.get("pnl") is not None and float(t["pnl"]) > 0
                and str(t.get("position_status") or "").upper() in ("CLOSED", "LIQUIDATED")
            )
            win_rate = (win_count / total_trades * 100.0) if total_trades > 0 else 0.0
            real_edge = probability_engine.compute_mathematical_edge(trades)
            footer_text = (
                f"SYSTEM EDGE: {real_edge:+.2f}% · TOTAL FILLS: {total_trades} · "
                f"WIN RATE: {win_rate:.1f}% · VOL SCANNER: TOP 10 ACTIVE · "
                f"BAYESIAN ODDS: ACTIVE · SQLITE WAL ENGINE ONLINE"
            )

            return rows, marquee_text, footer_text

        except Exception as e:
            logger.debug(f"Error trade stream: {e}")
            raise PreventUpdate

    # =====================================================================
    # 10. ROW 4: DUAL SPARKLINES & STRATEGY KPIS (GENUINE METRICS)
    # =====================================================================
    @app.callback(
        [Output("hud-sparkline-pnl", "figure"),
         Output("hud-sparkline-volume", "figure"),
         Output("hud-kpi-sharpe", "children"),
         Output("hud-kpi-pf", "children"),
         Output("hud-kpi-mdd", "children"),
         Output("hud-kpi-duration", "children"),
         Output("hud-kpi-slippage", "children"),
         Output("hud-kpi-fees", "children")],
        [Input("dashboard-interval", "n_intervals"),
         Input("hud-chart-symbol-select", "value")],
    )
    def update_analytics_and_sparklines(n, symbol):
        fees_val = "$0.00"
        pf = 0.0
        mdd = 0.0
        sharpe_val = "N/A"
        duration_val = "0m 00s"
        slippage_val = "0.000%"

        pnl_series = []
        vol_series = []

        try:
            conn = _get_sync_db()
            symbol = symbol or "BTC/USDT:USDT"
            base_sym = symbol.split("/")[0].split(":")[0].upper()

            # 1. PnL Series dari balance_history atau trades tertutup murni
            bal_rows = conn.execute(
                "SELECT equity FROM balance_history ORDER BY timestamp DESC LIMIT 15"
            ).fetchall()
            if bal_rows and len(bal_rows) >= 2:
                pnl_series = [float(r[0]) for r in reversed(bal_rows)]
            else:
                # PnL terealisasi kronologis dari posisi tertutup (tabel trades tidak punya kolom pnl)
                trade_pnl_rows = conn.execute(
                    "SELECT realized_pnl FROM positions "
                    "WHERE status IN ('CLOSED', 'LIQUIDATED') AND realized_pnl IS NOT NULL "
                    "ORDER BY closed_at ASC LIMIT 15"
                ).fetchall()
                if trade_pnl_rows:
                    init_row = conn.execute(
                        "SELECT initial_balance FROM account ORDER BY id DESC LIMIT 1"
                    ).fetchone()
                    cum = float(init_row[0]) if init_row and init_row[0] else 10000.0
                    pnl_series = [cum]
                    for r in trade_pnl_rows:
                        cum += float(r[0])
                        pnl_series.append(cum)
                else:
                    acc_row = conn.execute(
                        "SELECT balance FROM account ORDER BY id DESC LIMIT 1"
                    ).fetchone()
                    cur_bal = float(acc_row[0]) if acc_row and acc_row[0] else 10000.0
                    pnl_series = [cur_bal, cur_bal]

            # 2. Volume Series 1-menit dari simbol aktif
            vol_rows = conn.execute(
                "SELECT volume FROM candles WHERE (symbol = ? OR symbol LIKE ?) AND timeframe = '1m' "
                "ORDER BY timestamp DESC LIMIT 15",
                (symbol, f"%{base_sym}%"),
            ).fetchall()
            if vol_rows and len(vol_rows) >= 2:
                vol_series = [float(r[0]) for r in reversed(vol_rows)]
            else:
                vol_series = [10.0, 15.0, 12.0, 18.0, 14.0]

            # 3. Metrik Akun & Performa
            acc = conn.execute("SELECT * FROM account ORDER BY id DESC LIMIT 1").fetchone()

            # Total fee taker riil dari seluruh eksekusi (trades.fee)
            fee_row = conn.execute("SELECT COALESCE(SUM(fee), 0) FROM trades").fetchone()
            total_fees = float(fee_row[0]) if fee_row and fee_row[0] is not None else 0.0
            fees_val = f"${total_fees:,.2f}"

            # Profit Factor dari PnL terealisasi posisi tertutup (bukan tabel trades)
            pnl_rows = conn.execute(
                "SELECT realized_pnl FROM positions "
                "WHERE status IN ('CLOSED', 'LIQUIDATED') AND realized_pnl IS NOT NULL"
            ).fetchall()
            gross_profit = sum(float(r[0]) for r in pnl_rows if float(r[0]) > 0)
            gross_loss = abs(sum(float(r[0]) for r in pnl_rows if float(r[0]) < 0))
            if gross_loss > 0:
                pf = gross_profit / gross_loss
            elif gross_profit > 0:
                pf = gross_profit
            elif acc and acc["profit_factor"]:
                pf = float(acc["profit_factor"])

            # `max_drawdown` dan `sharpe_ratio` di tabel `account` tidak
            # pernah ditulis oleh jalur mana pun, nilainya permanen 0.
            # Menampilkan "0.00" membuat trader membaca "tidak pernah ada
            # drawdown" atau "Sharpe nol", dan keduanya klaim yang salah
            # tentang sistemnya. `N/A` jujur; angka nolnya tidak.
            if acc and acc["max_drawdown"] is not None and float(acc["max_drawdown"]) > 0:
                mdd = float(acc["max_drawdown"])
            if acc and acc["sharpe_ratio"] is not None and float(acc["sharpe_ratio"]) != 0:
                sharpe_val = f"{float(acc['sharpe_ratio']):.2f}"

            # 4. Durasi Rata-rata Posisi Terbuka / Tertutup Murni
            dur_rows = conn.execute(
                "SELECT (strftime('%s', closed_at) - strftime('%s', opened_at)) "
                "FROM positions WHERE status = 'CLOSED' AND closed_at IS NOT NULL"
            ).fetchall()
            if dur_rows:
                valid_durs = [int(r[0]) for r in dur_rows if r[0] is not None and r[0] > 0]
                if valid_durs:
                    avg_dur_sec = int(np.mean(valid_durs))
                    duration_val = f"{avg_dur_sec // 60}m {avg_dur_sec % 60:02d}s"

            # 5. Slippage Rata-rata = setengah bid-ask spread relatif terhadap mid price.
            # Pakai mid price dari orderbook itu sendiri agar pembilang & penyebut se-sumber.
            ob = market_store.get_order_book(symbol)
            ofi_val, rel_spread = calculate_order_flow_imbalance(ob)
            if rel_spread > 0:
                # Biaya slippage per sisi ≈ setengah spread relatif (Cartea & Jaimungal 2015)
                half_spread_pct = (rel_spread / 2.0) * 100.0
                slippage_val = f"{half_spread_pct:.3f}%"
            else:
                # Belum ada snapshot L2: laporkan ketiadaan data, bukan angka nol yang menyesatkan
                slippage_val = "N/A"

            conn.close()

        except Exception as e:
            logger.debug(f"Error analytics sparklines: {e}")

        fig_pnl = create_mini_sparkline_fig(pnl_series, GREEN_VINTAGE)
        fig_vol = create_mini_sparkline_fig(vol_series, BLUE_VINTAGE)
        pf_val = f"{pf:.2f}"
        mdd_val = f"{mdd:.2f}%" if mdd else "N/A"

        return fig_pnl, fig_vol, sharpe_val, pf_val, mdd_val, duration_val, slippage_val, fees_val

    # =====================================================================
    # 12. ROW 2: STRATEGY DECISION TREE — STATUS TAHAP REAL
    #
    # Callback ini HANYA menukar `className` kotak, `className` panah, dan
    # lebar rel kemajuan. Children kotak tahap tidak pernah disentuh, sehingga
    # animasi CSS idle tidak restart setiap siklus 500 ms.
    #
    # Setiap status diturunkan dari bukti nyata (harga live, snapshot L2,
    # lilin WS, funding/OI, tabel signals/positions/trades), bukan timer.
    # =====================================================================
    @app.callback(
        [Output("hud-symbol-pnl-rail", "children"),
         Output("hud-pnl-stage-tag", "children")],
        [Input("dashboard-interval", "n_intervals")],
    )
    def update_symbol_pnl(n):
        """
        PnL dan win rate per simbol, diurutkan dari yang paling untung.

        Ini menggantikan "Strategy Decision Tree" yang isinya cuma 6 kotak
        statis dengan badge "6/6 COMPLETE" - jawaban yang selalu sama dan
        tidak pernah mengubah keputusan apa pun.

        Yang ditunjukkan di sini: simbol mana yang membawa PnL dan mana
        yang memakannya. Dari win rate global angka itu tidak terlihat -
        win rate 50% bisa menyembunyikan satu simbol yang rugi 14 USDT
        dan satu lain yang untung 2 USDT.
        """
        try:
            conn = _get_sync_db()
            try:
                rows = conn.execute(
                    """
                    SELECT symbol,
                           COUNT(*) AS n,
                           SUM(CASE WHEN COALESCE(realized_pnl, 0) > 0
                                    THEN 1 ELSE 0 END) AS wins,
                           COALESCE(SUM(realized_pnl), 0) AS pnl
                    FROM positions
                    WHERE status IN ('CLOSED', 'LIQUIDATED')
                    GROUP BY symbol
                    HAVING COUNT(*) > 0
                    ORDER BY pnl DESC
                    """
                ).fetchall()
                open_now = {
                    r["symbol"]
                    for r in conn.execute(
                        "SELECT DISTINCT symbol FROM positions "
                        "WHERE status = 'OPEN'"
                    ).fetchall()
                }
            finally:
                conn.close()

            if not rows:
                return (
                    html.Div("NO CLOSED TRADES YET", className="sympnl-empty"),
                    "IDLE",
                )

            peak = max(max(abs(float(r["pnl"])) for r in rows), 0.01)
            best = max(float(r["pnl"]) for r in rows)
            worst = min(float(r["pnl"]) for r in rows)

            rails = []
            for r in rows:
                sym_full = str(r["symbol"])
                sym = sym_full.split("/")[0]
                pnl = float(r["pnl"])
                n_tr = int(r["n"])
                wr = int(r["wins"]) / n_tr if n_tr else 0.0
                width = min(abs(pnl) / peak * 100.0, 100.0)
                tone = "pos" if pnl > 0 else ("neg" if pnl < 0 else "flat")
                live = sym_full in open_now
                rails.append(html.Div([
                    html.Span(sym, className="sympnl-sym" + (" live" if live else "")),
                    html.Div([
                        html.Div(className="sympnl-bar " + tone,
                                 style={"width": f"{width:.1f}%"}),
                    ], className="sympnl-track"),
                    html.Span(f"{pnl:+.2f}", className="sympnl-val " + tone),
                    html.Span(f"{wr:.0%}", className="sympnl-wr"),
                    html.Span(f"n{n_tr}", className="sympnl-n"),
                ], className="sympnl-row"))

            if best > 0 and worst < 0:
                tag = f"BEST {best:+.2f} / WORST {worst:+.2f}"
            elif best > 0:
                tag = f"ALL POSITIVE {best:+.2f}"
            else:
                tag = f"ALL NEGATIVE {worst:+.2f}"

            return html.Div(rails, className="sympnl-list"), tag

        except Exception as e:
            logger.debug(f"Error symbol pnl: {e}")
            raise PreventUpdate
