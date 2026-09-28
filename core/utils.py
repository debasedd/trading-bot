"""
core/utils.py — Fungsi utilitas umum (parsing timestamp database UTC, dll).
"""

from datetime import datetime, timezone
from typing import Any, Union


def parse_db_timestamp(ts_val: Any) -> float:
    """
    Ubah nilai timestamp dari database SQLite (format UTC string seperti 'YYYY-MM-DD HH:MM:SS'
    atau float) menjadi unix timestamp (detik UTC) yang kompatibel dengan time.time().

    Menghindari bug timezone di mana OS lokal (seperti WIB / UTC+7) salah mengasumsikan
    string waktu UTC sebagai waktu lokal dan menghasilkan offset 7 jam.
    """
    if not ts_val:
        return 0.0
    if isinstance(ts_val, (int, float)):
        return float(ts_val)
    try:
        raw_str = str(ts_val).strip()
        # Jika ada Z ganti dengan +00:00
        cleaned = raw_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned)
        # SQLite datetime('now') tidak menyertakan timezone, padahal nilainya UTC
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return 0.0
