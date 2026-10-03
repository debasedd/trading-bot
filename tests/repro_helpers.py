"""
Helper bersama untuk test reproduksi defect live.

MASALAH ISOLASI YANG INI SELESAIKAN
----------------------------------
`SafetyGate.__init__` (trading/live/safety.py:195) memuat
`data_store/live_counters.json` KAPAN SAJA `TRADEBOT_LIVE` bernilai
benar -- bahkan kalau `state_path`zhSNULL. Itu disengaja di produksi.

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
    # Blekir ulang apa pun yang mungkin terbaca (harusnya tidak ada,
    # tapi ini membuat jaminan eksplisit dan tidak bergantung pada
    # detail implementasi load()).
    gate.counters.engaged = False
    gate.counters.orders_sent = 0
    gate.counters.realized_pnl = 0.0
    gate.counters.consecutive_errors = 0
    gate.counters.day_utc = ""
    gate.counters._unreadable = False
    return gate


def make_gate_for_test(**cfg_overrides):
    """Alias yang lebih pendek; sama dengan clean_gate()."""
    return clean_gate(**cfg_overrides)
