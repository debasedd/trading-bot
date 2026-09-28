"""
dashboard/layouts/news_feed.py — Tab feed berita & skor sentimen.
"""

from dash import html, dcc


def create_news_feed_layout():
    """Layout tab berita & sentimen."""
    return html.Div([
        # Ringkasan sentimen
        html.Div(id="sentiment-summary", className="info-card"),

        html.Br(),

        # Feed berita
        html.Div([
            html.H3("Berita Terbaru", style={"color": "#58a6ff", "marginBottom": "12px"}),
            html.Div(id="news-feed-container", style={
                "maxHeight": "60vh",
                "overflowY": "auto",
            }),
        ], className="info-card"),
    ], className="tab-content")


def create_sentiment_summary(aggregate):
    """Buat ringkasan sentimen pasar."""
    if not aggregate:
        return html.P("Data sentimen belum tersedia", style={"color": "#8b949e"})

    score = aggregate.get("avg_score", 0)
    label = aggregate.get("label", "NEUTRAL")
    count = aggregate.get("count", 0)
    pos = aggregate.get("positive", 0)
    neg = aggregate.get("negative", 0)

    if label == "POSITIVE":
        badge_class = "positive"
        icon = "+"
    elif label == "NEGATIVE":
        badge_class = "negative"
        icon = "-"
    else:
        badge_class = "neutral"
        icon = "~"

    return html.Div([
        html.H3("Sentimen Pasar", style={"color": "#58a6ff"}),
        html.Div([
            html.Div([
                html.Div("Skor Sentimen", className="label"),
                html.Div(f"{score:+.3f}", className=f"value {badge_class}"),
            ], className="stat-card"),

            html.Div([
                html.Div("Label", className="label"),
                html.Span(label, className=f"sentiment-badge {badge_class}",
                          style={"fontSize": "1rem", "padding": "4px 16px"}),
            ], className="stat-card"),

            html.Div([
                html.Div("Total Berita", className="label"),
                html.Div(str(count), className="value"),
            ], className="stat-card"),

            html.Div([
                html.Div("Positif / Negatif", className="label"),
                html.Div([
                    html.Span(f"{pos}", style={"color": "#3fb950"}),
                    html.Span(" / ", style={"color": "#8b949e"}),
                    html.Span(f"{neg}", style={"color": "#f85149"}),
                ], className="value"),
            ], className="stat-card"),
        ], style={"display": "flex", "gap": "16px", "flexWrap": "wrap"}),
    ])


def create_news_items(news_list):
    """Buat daftar berita."""
    if not news_list:
        return html.P("Belum ada berita", style={"color": "#8b949e"})

    items = []
    for n in news_list:
        sentiment = n.get("sentiment_vader") or 0
        label = n.get("sentiment_label", "NEUTRAL")

        if label == "POSITIVE":
            badge_class = "positive"
        elif label == "NEGATIVE":
            badge_class = "negative"
        else:
            badge_class = "neutral"

        impact = n.get("impact_level", "LOW")
        impact_color = {"HIGH": "#f85149", "MEDIUM": "#d29922", "LOW": "#8b949e"}.get(impact, "#8b949e")

        items.append(
            html.Div([
                html.Div([
                    html.Span(n.get("title", ""), className="title"),
                    html.Span(
                        f" {label} ({sentiment:+.2f})",
                        className=f"sentiment-badge {badge_class}",
                        style={"marginLeft": "8px"},
                    ),
                ]),
                html.Div([
                    html.Span(n.get("source", ""), style={"marginRight": "12px"}),
                    html.Span(f"Dampak: {impact}", style={"color": impact_color, "marginRight": "12px"}),
                    html.Span(str(n.get("fetched_at", ""))[:19]),
                ], className="meta"),
            ], className="news-item")
        )

    return html.Div(items)
