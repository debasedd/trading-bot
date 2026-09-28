"""
dashboard/layouts/positions.py — Tab posisi aktif & riwayat perdagangan.
"""

from dash import html, dash_table, dcc


def create_positions_layout():
    """Layout tab posisi dan riwayat."""
    return html.Div([
        # Posisi aktif
        html.Div([
            html.H3("Posisi Aktif", style={"color": "#58a6ff", "marginBottom": "12px"}),
            html.Div(id="active-positions-table"),
        ], className="info-card"),

        html.Br(),

        # Riwayat trading
        html.Div([
            html.H3("Riwayat Perdagangan", style={"color": "#58a6ff", "marginBottom": "12px"}),
            html.Div(id="trade-history-table"),
        ], className="info-card"),
    ], className="tab-content")


def create_positions_table(positions):
    """Buat tabel posisi aktif."""
    if not positions:
        return html.P("Tidak ada posisi terbuka", style={"color": "#8b949e"})

    headers = ["ID", "Simbol", "Sisi", "Entry", "Qty", "Leverage",
               "Margin", "SL", "TP", "Liq.", "PnL", "ROE%"]

    rows = []
    for p in positions:
        pnl = p.get("unrealized_pnl", 0)
        entry = p.get("entry_price", 0)
        qty = p.get("quantity", 0)
        margin = p.get("margin", 0)
        roe = (pnl / margin * 100) if margin > 0 else 0

        pnl_color = "#3fb950" if pnl >= 0 else "#f85149"
        side_color = "#3fb950" if p["side"] == "LONG" else "#f85149"

        rows.append(html.Tr([
            html.Td(f"#{p['id']}"),
            html.Td(p["symbol"]),
            html.Td(
                html.Span(p["side"], className=f"side-badge {'long' if p['side'] == 'LONG' else 'short'}")
            ),
            html.Td(f"${entry:,.2f}"),
            html.Td(f"{qty:.5f}"),
            html.Td(f"{p.get('leverage', 1)}x"),
            html.Td(f"${margin:,.2f}"),
            html.Td(f"${p.get('stop_loss', 0):,.2f}" if p.get("stop_loss") else "—"),
            html.Td(f"${p.get('take_profit', 0):,.2f}" if p.get("take_profit") else "—"),
            html.Td(f"${p.get('liquidation_price', 0):,.2f}"),
            html.Td(f"${pnl:+,.2f}", style={"color": pnl_color, "fontWeight": "700"}),
            html.Td(f"{roe:+.2f}%", style={"color": pnl_color, "fontWeight": "700"}),
        ]))

    return html.Table([
        html.Thead(html.Tr([html.Th(h) for h in headers])),
        html.Tbody(rows),
    ], className="dash-table", style={"width": "100%", "borderCollapse": "collapse"})


def create_trade_history_table(trades):
    """Buat tabel riwayat trading."""
    if not trades:
        return html.P("Belum ada riwayat perdagangan", style={"color": "#8b949e"})

    headers = ["ID", "Posisi", "Simbol", "Sisi", "Harga", "Qty", "Fee", "Tipe", "Waktu"]

    rows = []
    for t in trades[:50]:  # Tampilkan 50 terbaru
        side_color = "#3fb950" if t["side"] == "BUY" else "#f85149"

        rows.append(html.Tr([
            html.Td(f"#{t['id']}"),
            html.Td(f"#{t.get('position_id', '—')}"),
            html.Td(t["symbol"]),
            html.Td(t["side"], style={"color": side_color, "fontWeight": "600"}),
            html.Td(f"${t['price']:,.2f}"),
            html.Td(f"{t['quantity']:.5f}"),
            html.Td(f"${t.get('fee', 0):.2f}"),
            html.Td(t.get("trade_type", "")),
            html.Td(t.get("executed_at", "")[:19]),
        ]))

    return html.Table([
        html.Thead(html.Tr([html.Th(h) for h in headers])),
        html.Tbody(rows),
    ], className="dash-table", style={"width": "100%", "borderCollapse": "collapse"})
