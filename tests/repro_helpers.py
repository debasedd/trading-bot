"""
Helper bersama untuk test reproduksi defect live.

MASALAH ISOLASI YANG INI SELESAIKAN
----------------------------------
`SafetyGate.__init__` (trading/live/safety.py:195) memuat
`data_store/live_counters.json` KAPAN SAJA `TRADEBOT_LIVE` bernilai
benar -- bahkan kalau `state_path` bernilai None. Itu disengaja di produksi.

Akibatnya test yang menyuntik `TRADEBOT_LIVE=1` akan MEWARISI kill
switch dari test lain atau dari run sebelumnya, dan order ditolak
karena `KILL_SWITCH` -- bukan karena defect yang sedang diuji.
Test seperti itu lulus salah, dan tidak membuktikan apa pun.

Helper di file ini menjamin tiap test mulai dari gerbang yang bersih:
path state unik di tempfile, dan counters di-reset eksplisit. Tidak
ada file state produksi yang dibaca atau ditulis.
"""
import pathlib
import tempfile
from collections import deque
from contextlib import contextmanager

from core.market_store import PRICE_HISTORY_MAX

from core.config import LiveConfig
from trading.live.safety import SafetyGate

# Env yang MEMANGGI kan semua syarat live. Private key-nya dummy
# (32 byte hex) -- tidak pernah dipakai untuk menandatangani apa pun,
# dan test ini tidak pernah mengirim order.
LIVE_ENV = {
    "TRADEBOT_LIVE": "1",
    "TRADEBOT_LIVE_CONFIRMED": "1",
    "HYPERLIQUID_PRIVATE_KEY": "0x" + "ab" * 32,
}


def clean_gate(cfg=None, **overrides):
    """
    SafetyGate dengan state terisolasi total.

    `state_path` diarahkan ke tempfile.mkdtemp() sehingga tidak ada
    `data_store/live_counters.json` yang terbaca. Kill switch
    dipaksa mati, streak error dan counter harian dikosongkan --
    semua di memori, tidak menyentuh disk produksi.
    """
    cfg = cfg or LiveConfig()
    for key, value in overrides.items():
        setattr(cfg, key, value)
    state_path = pathlib.Path(tempfile.mkdtemp()) / "counters.json"
    gate = SafetyGate(LiveConfig(**{
        k: v for k, v in vars(cfg).items()
    }), env=dict(LIVE_ENV), state_path=state_path)
    # Ensure ulang apa pun yang mungkin terbaca (seharusnya tidak ada,
    # tapi ini membuat jaminan eksplisit dan tidak bergantung pada
    # detail implementasi load()).
    #
    # `day_utc` DIISI dengan tanggal UTC hari ini, bukan string kosong.
    # Alasannya: `master_blockers(now)` memanggil `rollover_if_needed(now)`
    # lebih dulu, dan itu mereset counter harian bila `day_utc != today`.
    # Dengan `day_utc = ""`, setiap pemanggilan pertama akan menganggap
    # ini hari baru dan meng-nolkan `realized_pnl` -- jadi test daily-loss
    # breaker akan lulus karena alasan yang salah.
    #
    # Di produksi `day_utc` selalu terisi (di-set saat load file state),
    # jadi ini memirror kondisi nyata, bukan mengubahnya.
    from datetime import datetime, timezone

    gate.counters.engaged = False
    gate.counters.orders_sent = 0
    gate.counters.realized_pnl = 0.0
    gate.counters.consecutive_errors = 0
    gate.counters.day_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    gate.counters._unreadable = False
    return gate


def make_gate_for_test(**cfg_overrides):
    """Alias yang lebih pendek; sama dengan clean_gate()."""
    return clean_gate(**cfg_overrides)


def inside_trading_window(cfg=None):
    """
    LiveEngine dengan jam yang DIKENDALIKAN, untuk test yang memakai
    jalur order.

    `SafetyGate.master_blockers` menolak order di luar
    `cfg.live_window_utc`, yang di produksi adalah (13, 23) UTC. Test
    yang mengirim order lalu mengasumsikan order itu terkirim akan
    LULUS karena GERBANG menolak -- bukan karena perilaku yang diuji.

    Ini bukan jebakan hipotetis: `tests/test_partial_fill.py` sudah
    Ini bukan jebakan hipotetis: `tests/test_partial_fill.py` gagal
    pada pukul 06:18 UTC dengan pesan `di luar jendela waktu
    Test yang hanya hijau sebagian hari bukan bukti apa pun.

    Jendela dibuat (0, 24) supaya tidak pernah menutup. Sisa gerbang
    -- notional, collateral, leverage, kill switch, daily loss -- tetap
    yang sebenarnya diuji.
    """
    from datetime import datetime, timezone

    from trading.live.engine import LiveEngine

    cfg = cfg or LiveConfig()
    cfg.live_window_utc = (0, 24)
    eng = LiveEngine(
        gate=clean_gate(cfg),
        exchange=None,
        cfg=cfg,
    )
    eng.now_fn = lambda: datetime(2026, 10, 4, 15, 0, tzinfo=timezone.utc)
    return eng


@contextmanager
def market_price(symbol, price):
    """
    Set harga sementara lalu pulihkan state global apa adanya.

    `market_store` adalah SINGKTON tanpa API reset. `set_price(0.0)`
    tidak mengosongkan apa pun -- `set_price` menolak nilai yang bukan
    positif, jadi harga lama dan SEJARUHNYA tetap ada.

    Itu bukan detail kecil. `MarketEngine` menghitung harga eksekusi dari
    median riwayat, jadi satu harga yang tertinggal di test sebelumnya
    membuat test berikutnya gagal dengan:

        Harga tidak layak eksekusi: harga 60000 menyimpang 29.763%
        dari median 85425

    Test breakeven punya harga ini, dan `unittest` (yang tidak mengisolasi
    test per file) ikut gagal karena itu. Pulihkan dari dalam, bukan
    mengandalkan urutan test.
    """
    from core.market_store import market_store

    store = market_store.__dict__
    had_history = symbol in store["_price_history"]
    saved_hist = (
        list(store["_price_history"][symbol]) if had_history else None)
    saved_last = store["_last_prices"].get(symbol)
    saved_ts = store["_price_ts"].get(symbol)

    market_store.set_price(symbol, price)
    try:
        yield market_store
    finally:
        if had_history:
            store["_price_history"][symbol] = deque(
                saved_hist, maxlen=PRICE_HISTORY_MAX)
        else:
            store["_price_history"].pop(symbol, None)
        if saved_last is None:
            store["_last_prices"].pop(symbol, None)
        else:
            store["_last_prices"][symbol] = saved_last
        if saved_ts is None:
            store["_price_ts"].pop(symbol, None)
        else:
            store["_price_ts"][symbol] = saved_ts
