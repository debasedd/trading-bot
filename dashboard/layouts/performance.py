"""
dashboard/layouts/performance.py — Tab performa & statistik (Equity Curve, Drawdown).
"""

from dash import html, dcc
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from dashboard.layouts import palette


def create_performance_layout():
    """Layout tab performa."""
    return html.Div([
        # Stat cards
        html.Div(id="performance-stats", className="row", style={"marginBottom": "20px"}),

        # Equity Curve
        html.Div([
            html.H3("Equity Curve", style={"color": palette.BLUE}),
            dcc.Graph(id="equity-chart", style={"height": "40vh"}),
        ], className="info-card"),

        html.Br(),

        # Drawdown chart
        html.Div([
            html.H3("Drawdown", style={"color": palette.BLUE}),
            dcc.Graph(id="drawdown-chart", style={"height": "25vh"}),
        ], className="info-card"),
    ], className="tab-content")


def create_performance_stats(account_summary):
    """Buat stat cards performa."""
    if not account_summary:
        return html.P("Data belum tersedia", style={"color": palette.INK_MUTED})

    balance = account_summary.get("balance", 0)
    initial = account_summary.get("initial_balance", 10000)
    total_pnl = account_summary.get("total_pnl", 0)
    total_trades = account_summary.get("total_trades", 0)
    win_rate = account_summary.get("win_rate", 0)
    profit_factor = account_summary.get("profit_factor", 0)
    max_dd = account_summary.get("max_drawdown", 0)
    equity = account_summary.get("equity", balance)

    pnl_class = "positive" if total_pnl >= 0 else "negative"
    pnl_pct = ((balance - initial) / initial * 100) if initial > 0 else 0

    cards = [
        ("Saldo", f"${balance:,.2f}", ""),
        ("Equity", f"${equity:,.2f}", ""),
        ("Total PnL", f"${total_pnl:+,.2f}", pnl_class),
        ("Return", f"{pnl_pct:+.2f}%", pnl_class),
        ("Total Trade", str(total_trades), ""),
        ("Win Rate", f"{win_rate:.1f}%", "positive" if win_rate > 50 else "negative" if win_rate < 40 else "neutral"),
        ("Profit Factor", f"{profit_factor:.2f}" if profit_factor else "—", ""),
        ("Max Drawdown", f"{max_dd * 100:.2f}%", "negative" if max_dd > 0.1 else ""),
    ]

    return html.Div([
        html.Div([
            html.Div(label, className="label"),
            html.Div(value, className=f"value {css}"),
        ], className="stat-card")
        for label, value, css in cards
    ], style={"display": "flex", "gap": "12px", "flexWrap": "wrap"})


def create_equity_chart(balance_history):
    """Buat grafik equity curve."""
    fig = go.Figure()

    if not balance_history:
        fig.update_layout(
            template="none",
            paper_bgcolor=palette.PAPER_BG,
            plot_bgcolor=palette.PAPER_BG,
            annotations=[dict(text="Belum ada data", showarrow=False, font=dict(color=palette.INK_MUTED))],
        )
        return fig

    timestamps = [h.get("timestamp", "") for h in reversed(balance_history)]
    equities = [h.get("equity", 0) for h in reversed(balance_history)]
    balances = [h.get("balance", 0) for h in reversed(balance_history)]

    fig.add_trace(go.Scatter(
        x=timestamps, y=equities,
        mode="lines",
        name="Equity",
        line=dict(color=palette.BLUE, width=2),
        fill="tozeroy",
        fillcolor=palette.alpha(palette.BLUE, 0.10),
    ))

    fig.add_trace(go.Scatter(
        x=timestamps, y=balances,
        mode="lines",
        name="Balance",
        line=dict(color=palette.GREEN, width=1, dash="dot"),
    ))

    fig.update_layout(
        template="none",
        paper_bgcolor=palette.PAPER_BG,
        plot_bgcolor=palette.PAPER_BG,
        margin=dict(l=50, r=20, t=10, b=30),
        font=dict(color=palette.INK_MUTED, size=11),
        legend=dict(bgcolor=palette.alpha(palette.CARD_BG, 0.80), bordercolor=palette.RULE),
        yaxis=dict(gridcolor=palette.CARD_BG, title="USDT"),
        xaxis=dict(gridcolor=palette.CARD_BG),
    )

    return fig


def create_drawdown_chart(balance_history):
    """Buat grafik drawdown."""
    fig = go.Figure()

    if not balance_history:
        fig.update_layout(
            template="none",
            paper_bgcolor=palette.PAPER_BG,
            plot_bgcolor=palette.PAPER_BG,
        )
        return fig

    equities = [h.get("equity", 0) for h in reversed(balance_history)]
    timestamps = [h.get("timestamp", "") for h in reversed(balance_history)]

    # Hitung drawdown
    peak = equities[0] if equities else 0
    drawdowns = []
    for eq in equities:
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak * 100 if peak > 0 else 0
        drawdowns.append(-dd)

    fig.add_trace(go.Scatter(
        x=timestamps, y=drawdowns,
        mode="lines",
        name="Drawdown",
        line=dict(color=palette.RED, width=1.5),
        fill="tozeroy",
        fillcolor=palette.alpha(palette.RED, 0.15),
    ))

    fig.update_layout(
        template="none",
        paper_bgcolor=palette.PAPER_BG,
        plot_bgcolor=palette.PAPER_BG,
        margin=dict(l=50, r=20, t=10, b=30),
        font=dict(color=palette.INK_MUTED, size=11),
        yaxis=dict(gridcolor=palette.CARD_BG, title="Drawdown %"),
        xaxis=dict(gridcolor=palette.CARD_BG),
    )

    return fig
