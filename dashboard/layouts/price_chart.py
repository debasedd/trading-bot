"""
dashboard/layouts/price_chart.py — Tab grafik harga candlestick + overlay indikator.
"""

from dash import html, dcc
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from core.config import get_config
from dashboard.layouts import palette


def create_price_chart_layout():
    """Layout tab grafik harga."""
    cfg = get_config()
    symbol_options = []
    for s in cfg.symbols:
        short = s.split("/")[0] + "/USDT"
        symbol_options.append({"label": short, "value": s})
    if not symbol_options:
        symbol_options = [{"label": "BTC/USDT", "value": "BTC/USDT:USDT"}]
    default_symbol = symbol_options[0]["value"]

    return html.Div([
        html.Div([
            html.Div([
                html.Label("Simbol (Top Volume):", style={"color": palette.INK_MUTED, "marginRight": "8px"}),
                dcc.Dropdown(
                    id="chart-symbol",
                    options=symbol_options,
                    value=default_symbol,
                    style={"width": "180px", "backgroundColor": palette.CARD_BG, "color": palette.INK_MUTED},
                    clearable=False,
                ),
            ], style={"display": "flex", "alignItems": "center", "gap": "8px"}),

            html.Div([
                html.Label("Timeframe:", style={"color": palette.INK_MUTED, "marginRight": "8px"}),
                dcc.Dropdown(
                    id="chart-timeframe",
                    options=[
                        {"label": "1m", "value": "1m"},
                        {"label": "5m", "value": "5m"},
                        {"label": "15m", "value": "15m"},
                        {"label": "1h", "value": "1h"},
                        {"label": "4h", "value": "4h"},
                    ],
                    value="5m",
                    style={"width": "100px", "backgroundColor": palette.CARD_BG, "color": palette.INK_MUTED},
                    clearable=False,
                ),
            ], style={"display": "flex", "alignItems": "center", "gap": "8px"}),
        ], style={"display": "flex", "gap": "24px", "marginBottom": "16px"}),

        # Grafik utama
        dcc.Graph(
            id="candlestick-chart",
            config={"displayModeBar": True, "scrollZoom": True},
            style={"height": "65vh"},
        ),

        # Indikator panel
        html.Div([
            html.Div(id="indicator-panel", className="info-card", children=[
                html.H3("Indikator Terkini"),
                html.Div(id="indicator-values"),
            ]),
        ]),
    ], className="tab-content")


def create_candlestick_figure(df, symbol="BTC/USDT", trades=None):
    """
    Buat grafik candlestick dengan volume dan indikator overlay.

    Args:
        df: DataFrame OHLCV dengan kolom indikator
        symbol: Nama simbol
        trades: List posisi untuk ditandai di chart
    """
    fig = make_subplots(
        rows=3, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.03,
        row_heights=[0.6, 0.2, 0.2],
        subplot_titles=[symbol, "Volume", "RSI"],
    )

    # Candlestick
    fig.add_trace(
        go.Candlestick(
            x=df["timestamp"],
            open=df["open"],
            high=df["high"],
            low=df["low"],
            close=df["close"],
            name="OHLC",
            increasing_line_color=palette.GREEN,
            decreasing_line_color=palette.RED,
        ),
        row=1, col=1,
    )

    # EMA overlay
    if "ema_short" in df.columns:
        fig.add_trace(
            go.Scatter(
                x=df["timestamp"], y=df["ema_short"],
                line=dict(color=palette.BLUE, width=1),
                name=f"EMA {9}",
            ),
            row=1, col=1,
        )

    if "ema_long" in df.columns:
        fig.add_trace(
            go.Scatter(
                x=df["timestamp"], y=df["ema_long"],
                line=dict(color=palette.AMBER, width=1),
                name=f"EMA {21}",
            ),
            row=1, col=1,
        )

    # Bollinger Bands
    if "bb_upper" in df.columns:
        fig.add_trace(
            go.Scatter(
                x=df["timestamp"], y=df["bb_upper"],
                line=dict(color=palette.alpha(palette.INK_MUTED, 0.30), width=1, dash="dot"),
                name="BB Upper",
                showlegend=False,
            ),
            row=1, col=1,
        )
        fig.add_trace(
            go.Scatter(
                x=df["timestamp"], y=df["bb_lower"],
                line=dict(color=palette.alpha(palette.INK_MUTED, 0.30), width=1, dash="dot"),
                name="BB Lower",
                fill="tonexty",
                fillcolor=palette.alpha(palette.INK_MUTED, 0.05),
                showlegend=False,
            ),
            row=1, col=1,
        )

    # Trade markers
    if trades:
        for t in trades:
            color = palette.GREEN if t.get("side") == "LONG" else palette.RED
            marker = "triangle-up" if t.get("side") == "LONG" else "triangle-down"
            fig.add_trace(
                go.Scatter(
                    x=[t.get("time")],
                    y=[t.get("price")],
                    mode="markers",
                    marker=dict(symbol=marker, size=14, color=color),
                    name=f"{t.get('side')} @ {t.get('price')}",
                ),
                row=1, col=1,
            )

    # Volume
    colors = [palette.GREEN if c >= o else palette.RED for c, o in zip(df["close"], df["open"])]
    fig.add_trace(
        go.Bar(
            x=df["timestamp"], y=df["volume"],
            marker_color=colors,
            name="Volume",
            showlegend=False,
        ),
        row=2, col=1,
    )

    # RSI
    if "rsi" in df.columns:
        fig.add_trace(
            go.Scatter(
                x=df["timestamp"], y=df["rsi"],
                line=dict(color=palette.AMBER, width=1.5),
                name="RSI",
            ),
            row=3, col=1,
        )
        # Garis RSI 30/70
        fig.add_hline(y=70, line_dash="dot", line_color=palette.RED, opacity=0.5, row=3, col=1)
        fig.add_hline(y=30, line_dash="dot", line_color=palette.GREEN, opacity=0.5, row=3, col=1)

    # Layout tema gelap
    fig.update_layout(
        template="none",
        paper_bgcolor=palette.PAPER_BG,
        plot_bgcolor=palette.PAPER_BG,
        font=dict(color=palette.INK_MUTED, size=11),
        margin=dict(l=50, r=20, t=40, b=20),
        legend=dict(
            bgcolor=palette.alpha(palette.CARD_BG, 0.80),
            bordercolor=palette.RULE,
            font=dict(size=10),
        ),
        xaxis_rangeslider_visible=False,
        xaxis3=dict(gridcolor=palette.CARD_BG),
        yaxis=dict(gridcolor=palette.CARD_BG),
        yaxis2=dict(gridcolor=palette.CARD_BG),
        yaxis3=dict(gridcolor=palette.CARD_BG, range=[0, 100]),
    )

    return fig
