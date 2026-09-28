"""
apply_candidate.py — tulis kandidat "layered flow" ke salinan modul.

Pemisahan ini disengaja: geometri bisa disapu (pitch, rise, posisi hub)
tanpa mengedit file produksi berulang kali, dan file produksi tidak pernah
disentuh oleh skrip ini.

Setiap patch diverifikasi. Patch yang gagal menempel harus MELUAP, bukan
diam-diam lolos — itulah bagaimana "fix" sebelumnya bisaDll shipped tanpa
mengubah satu baris pun.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "dashboard" / "layouts" / "hud_figures.py"
DST = Path(__file__).parent / "_snap" / "hf_cand.py"

NODES_BLOCK = '''NEURAL_SYSTEM_NODES = {
    # Baris 1 - SUMBER. Empat sumber harga (satu cascade bertingkat) dan
    # dua sumber fitur. Keduanya tidak dicampur.
    "CORE_ENGINE":     (1.60,  10.00, "SIGNAL", "#000000", "Hyperliquid L2 Book"),
    "BINANCE_FEED":    (5.00,  10.00, "FLOW",   "#c27800", "All Mids Stream (WS)"),
    "YF_FALLBACK":     (8.40,  10.00, "YFI",    "#555042", "Price Tier 1 - yfinance"),
    "COINGECKO":       (11.80, 10.00, "COIN",   "#555042", "Price Tier 2 - CoinGecko"),
    "MACRO_FRED":      (15.20, 10.00, "MACRO",  "#555042", "CPI / Yield / FFR"),
    "SENTIMENT":       (18.60, 10.00, "SENT",   "#c27800", "VADER + FinBERT"),
    # Baris 2 - KONSUMEN. Masing-masing benar-benar ada di kodenya.
    "TECH_TA":         (5.00,   7.20, "TECH",   "#0077b6", "pandas-ta (RSI/MACD/BB)"),
    "RF_MODEL":        (10.00,  7.20, "RF ML",  "#7209b7", "RandomForest - 7 fitur"),
    "DECISION_GATE":   (13.60,  7.20, "RISK",   "#0077b6", "Risk Sizing Gate"),
    # Baris 3 - SATU-SATUNYA hub.
    "EXECUTION_AGENT": (13.60,  4.90, "EXEC",   "#009933", "Paper Engine Exec"),
}
'''

EDGES_BLOCK = '''NEURAL_SYSTEM_EDGES = [
    # Empat sumber harga bertemu di TECH. Bukan turunan yang dikarang:
    # price_feed menghasilkan satu baris OHLCV dan pandas-ta mengolahnya.
    # Efektif satu sumber data, empat prioritas.
    ("CORE_ENGINE",   "TECH_TA"),
    ("BINANCE_FEED",  "TECH_TA"),
    ("YF_FALLBACK",   "TECH_TA"),
    ("COINGECKO",     "TECH_TA"),
    # Dua sumber fitur bertemu di model, bersama hasil TA. SENT bukan
    # tahap SESUDAH RF: sentiment_score adalah salah satu dari 7 fitur
    # (analysis/ml_signals.py:39), jadi ia masuk, bukan lanjut.
    ("MACRO_FRED",    "RF_MODEL"),
    ("SENTIMENT",     "RF_MODEL"),
    ("TECH_TA",       "RF_MODEL"),
    # Dua sisi terakhir ini publish/subscribe nyata di core/event_bus.py.
    ("RF_MODEL",      "DECISION_GATE"),    # publish MARKET_ANALYSIS
    ("DECISION_GATE", "EXECUTION_AGENT"),   # publish TRADE_DECISION
]

# YF_FALLBACK dan COINGECKO sengaja TIDAK punya sisi ke hilir. Keduanya
# adalah TINGKAT fallback dari SATU sumber harga (data/price_feed.py:378-383,
# cascade `if not ticker`), bukan tahap pipeline. Sisi dari mereka ke
# simpul lain berarti mengarang relasi.
'''

HUB_BLOCK = '''    # SATU hub, bukan lima.
    #
    # Versi lama memakai lima simpul baris bawah sebagai kandidat hub dan
    # memilih yang terdekat. Akibatnya satu token bisa nyambung ke lima
    # simpul berbeda tergantung token mana yang aktif, jadi graf berganti
    # bentuk tiap refresh dan tidak ada satu pun bentuk yang bisa dibaca.
    # Hub tunggal juga benar secara semantik: semua token dieksekusi di
    # Paper Engine, dan hanya di situ.
    token_hubs = ("EXECUTION_AGENT",)
'''

CURVE_BLOCK = '''    # Sisi token semuanya berporos di satu titik, jadi dua sisi berporos
    # sama tidak bisa berpotongan di tengah - mereka cuma bertemu di
    # poros. Yang bisa membuat mereka berpotongan adalah busur yang
    # membuat satu melintasi drop milik yang lain, dan itu yang dicegah.
    #
    # Rise SEBANDING dengan |dx|, bukan tetapan. Untuk dua token di sisi
    # yang sama dari poros, yang lebih jauh punya|y| bus lebih tinggi dan
    # span horizontal lebih panjang, sehingga busnya melintas DI ATAS
    # drop milik yang lebih dekat. Sisi yang persis vertikal rise-nya nol.
    def _edge_curve(src, dst, samples=24):
        """Titik-titik Bezier kubik dari src ke dst."""
        ax, ay = layout[src][0], layout[src][1]
        bx, by = layout[dst][0], layout[dst][1]
        dx, dy = bx - ax, by - ay
        length = math.hypot(dx, dy)
        if length < 1e-9:
            return [ax, bx], [ay, by]

        if src in token_hubs or dst in token_hubs:
            # Titik kontrol di x poros dan x target, bukan di tengah,
            # supaya leave dan arrive sama-sama vertikal: dua drop
            # vertikal di kolom berbeda tidak mungkin saling potong
            # walau setinggi apa pun bus masing-masing.
            rise = 0.0 if abs(dx) < 1e-9 else min(abs(dx) * 0.30, 2.6)
            c1 = (ax, ay - rise)
            c2 = (bx, by + rise)
        else:
            bow = 0.0 if abs(dx) < 1e-6 else min(length * 0.05, 0.7)
            c1 = (ax + dx * 0.5 - dy / length * bow,
                  ay + dy * 0.5 + dx / length * bow)
            c2 = c1

        xs, ys = [], []
        for i in range(samples):
            t = i / (samples - 1)
            inv = 1.0 - t
            a, b, c, d = inv ** 3, 3 * inv * inv * t, 3 * inv * t * t, t ** 3
            xs.append(a * ax + b * c1[0] + c * c2[0] + d * bx)
            ys.append(a * ay + b * c1[1] + c * c2[1] + d * by)
        return xs, ys
'''

TOKENS_12 = ["BTC", "ETH", "SOL", "XRP", "BNB", "DOGE",
             "NEAR", "HYPE", "ZEC", "ONDO", "ENA", "XPL"]


def build_module(token_slots=None, rise=None, dst=DST, src=SRC):
    src_text = src.read_text(encoding="utf-8")

    def sub(pattern, repl, flags=re.S, label=""):
        new, n = re.subn(pattern, repl, src_text, count=1, flags=flags)
        if n != 1:
            raise AssertionError(f"patch tidak menempel: {label or pattern[:40]}")
        return new

    src_text = sub(r"NEURAL_SYSTEM_NODES = \{.*?\n\}\n", NODES_BLOCK,
                   label="nodes")
    src_text = sub(r"NEURAL_SYSTEM_EDGES = \[.*?\n\]\n", EDGES_BLOCK,
                   label="edges")

    if token_slots is None:
        token_slots = [(1.65 + 1.70 * k, 1.40) for k in range(12)]
    body = "".join(f"    ({x:.2f}, {y:.2f}),\n" for x, y in token_slots)
    slots_block = (
        "TOKEN_SLOTS = [\n"
        "    # SATU baris. Dua baris hanya perlu kalau ada baris kedua di\n"
        "    # bawahnya yang harus dilewati; di kandidat ini tidak ada, karena\n"
        "    # semua sisi ke token berporos di EXEC dan tidak pernah memotong\n"
        "    # pita token.\n"
        + body + "]\n"
    )
    src_text = sub(r"TOKEN_SLOTS = \[.*?\n\]\n", slots_block, label="slots")

    registry = "_TOKEN_SLOT_REGISTRY: dict = {\n" + "".join(
        f"    {s!r}: {i},\n" for i, s in enumerate(TOKENS_12)) + "}"
    src_text = sub(r"_TOKEN_SLOT_REGISTRY: dict = _build_token_slot_registry\(\)",
                   registry, flags=0, label="registry")

    src_text = sub(r"    token_hubs = \(.*?\n    \)\n", HUB_BLOCK, label="hub")
    src_text = sub(
        r"    def _edge_curve\(src, dst, samples=\d+\):.*?\n        return xs, ys\n",
        CURVE_BLOCK, label="curve")
    src_text = sub(
        r"        tx, ty = layout\[sym\]\[0\], layout\[sym\]\[1\]\n.*?"
        r"        all_edges\.append\(\(nearest, sym\)\)\n",
        "        tx, ty = layout[sym][0], layout[sym][1]\n"
        "        nearest = token_hubs[0]\n"
        "        all_edges.append((nearest, sym))\n",
        label="hubpick")

    if rise is not None:
        src_text = src_text.replace("* 0.30, 2.6", f"* {rise}, 2.6")

    src_text = sub(r"range=\[0\.0, 22\.0\]", "range=_X_RANGE", flags=0,
                   label="xrange")
    src_text = sub(r"range=\[0\.0, 11\.5\]", "range=_Y_RANGE", flags=0,
                   label="yrange")
    src_text = "_X_RANGE = [0.0, 22.0]\n_Y_RANGE = [0.0, 11.5]\n\n" + src_text

    dst.write_text(src_text, encoding="utf-8")
    return dst


if __name__ == "__main__":
    out = build_module()
    s = out.read_text(encoding="utf-8")
    assert 'token_hubs = ("EXECUTION_AGENT",)' in s, "hub"
    assert "Bezier kubik" in s, "curve"
    assert "_X_RANGE" in s, "range"
    print("patched ->", out)
