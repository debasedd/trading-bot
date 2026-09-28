"""
dashboard/layouts/agent_logs.py — Tab log penalaran agen.
"""

from dash import html, dcc


def create_agent_logs_layout():
    """Layout tab log agen."""
    return html.Div([
        # Filter
        html.Div([
            html.Label("Filter Agen:", style={"color": "#8b949e", "marginRight": "8px"}),
            dcc.Dropdown(
                id="agent-log-filter",
                options=[
                    {"label": "Semua Agen", "value": "ALL"},
                    {"label": "Berita (News Agent)", "value": "news_agent"},
                    {"label": "Analisis", "value": "analysis_agent"},
                    {"label": "Keputusan", "value": "decision_agent"},
                    {"label": "Eksekusi", "value": "execution_agent"},
                    {"label": "Paper Engine", "value": "paper_engine"},
                ],
                value="ALL",
                style={"width": "220px", "backgroundColor": "#161b22"},
                clearable=False,
            ),
        ], style={"marginBottom": "16px", "display": "flex", "alignItems": "center"}),

        # Log container
        html.Div(id="agent-logs-container", style={
            "maxHeight": "70vh",
            "overflowY": "auto",
            "padding": "8px",
        }),
    ], className="tab-content")


def create_log_entries(logs):
    """Buat tampilan log penalaran agen."""
    if not logs:
        return html.P("Belum ada log agen", style={"color": "#8b949e"})

    entries = []
    for log in logs:
        agent = log.get("agent_name", "")
        action = log.get("action", "")
        reasoning = log.get("reasoning", "")
        timestamp = log.get("timestamp", "")[:19]

        # Tentukan warna berdasarkan konten
        css_class = "info"
        if "BULLISH" in reasoning.upper() or "LONG" in action.upper():
            css_class = "bullish"
        elif "BEARISH" in reasoning.upper() or "SHORT" in action.upper():
            css_class = "bearish"
        elif "HOLD" in action.upper() or "NEUTRAL" in reasoning.upper():
            css_class = "neutral"

        entries.append(
            html.Div([
                html.Span(timestamp, className="log-time"),
                html.Span(f"[{agent}]", className="log-agent"),
                html.Span(f" {action}: ", style={"color": "#d29922", "fontWeight": "600"}),
                html.Span(reasoning, style={"color": "#c9d1d9"}),
            ], className=f"log-entry {css_class}")
        )

    return html.Div(entries)
