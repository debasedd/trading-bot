"""
tests/stateful_exchange.py — bursa tiruan yang BERAKEH TIAN.

Stub yang selalu mengembalikan dict tetap tidak bisa membuktikan apa pun
tentang jalur uang: semua order terisi, semua harga sama, tidak pernah ada
slippage. Test yang memakai stub seperti itu hanya menguji bahwa kodenya
tidak crash.

Bursa tiruan di sini SAGA:

* order BENAR-BENAR mengisi posisi, dan `filled_size` mencerminkan yang
  benar-benar terisi — termasuk saat hanya sebagian;
* partial fill mungkin, dan sisanya jadi order resting;
* harga bergerak antar-panggilan, jadi slippage bisa diuji;
* `user_fills` hanya mengembalikan fill yang benar-benar terjadi, dan
  `dir`ocosong kalau bot tidak melakukan apa-apa.

Kalau bursa tiruan bisa membuat test hijau yang tidak akan hijau di bursa
sungguhan, itu kegagalan test, bukan keberhasilan test.
"""

import itertools
from types import SimpleNamespace
from typing import Any, Dict, List, Optional


class Outcome(SimpleNamespace):
    """
    Hasil fill bursa.

    OBJEK, bukan dict: `executor._persist_open` dan `_close` membaca
    dengan `getattr(outcome, "filled_size")`. Mengembalikan dict di sini
    membuat `getattr` mengembalikan 0 diam-diam — fill yang benar-benar
    terjadi akan terbaca sebagai nol.
    """


class StatefulExchange:
    """Bursa tiruan dengan keadaan internal."""

    def __init__(self, mid: float = 100.0, tick: float = 0.0):
        self.mid = mid
        self.tick = tick
        self.positions: Dict[str, float] = {}
        self.open_orders: List[Dict[str, Any]] = []
        self.fills: List[Dict[str, Any]] = []
        self.submitted: List[Dict[str, Any]] = []
        self._ids = itertools.count(1)
        self._fills = itertools.count(1)

    # ── harga ─────────────────────────────────────────────────────────
    def mid_price(self, coin: str) -> float:
        """Harga bergerak kalau `tick` diset — supaya slippage bisa diuji."""
        self.mid = self.mid + self.tick
        return self.mid

    # ── order ─────────────────────────────────────────────────────────
    def submit(self, coin: str, is_buy: bool, size: float, price: float,
               is_close: bool = False,
               fill_ratio: float = 1.0) -> Dict[str, Any]:
        """
        Catat order dan isi sebesar `fill_ratio`.

        `fill_ratio < 1.0` menghasilkan PARTIAL FILL plus order resting
        untuk sisanya — keadaan yang harus ditangani `_persist_open` dan
        `_close`, bukan kondisi yang di concoct.
        """
        oid = next(self._ids)
        rec = {
            "id": oid, "coin": coin, "is_buy": is_buy, "size": size,
            "price": price, "is_close": is_close, "cloid": None,
        }
        self.submitted.append(rec)

        filled = size * fill_ratio
        if filled <= 0:
            self.open_orders.append(dict(rec, filled_size=0.0))
            return {"success": True, "protected": False,
                    "message": "order resting", "outcome": None, "order": rec}

        self.open_orders.append(dict(rec, filled_size=filled))
        avg = price if is_buy else price
        self.fills.append({
            "coin": coin, "dir": "Open Long" if is_buy else "Open Short",
            "px": avg, "sz": filled, "fee": -0.001 * filled * avg,
            "closedPnl": "0.0", "cloid": rec["cloid"],
        })
        if is_close:
            prev = self.positions.get(coin, 0.0)
            self.positions[coin] = max(0.0, prev - filled) if is_buy else prev - filled
        else:
            self.positions[coin] = self.positions.get(coin, 0.0) + (
                filled if is_buy else -filled)
        return {
            "success": True, "protected": True,
            "message": "filled", "order": rec,
            "outcome": Outcome(filled_size=filled, avg_price=avg),
            "position": Outcome(coin=coin, side="LONG" if is_buy else "SHORT",
                                size=filled, leverage=5,
                                stop_loss=None, take_profit=None),
        }

    def cancel(self, oid: int) -> None:
        self.open_orders = [o for o in self.open_orders if o["id"] != oid]

    def cancel_all(self, coin: Optional[str] = None) -> Dict[str, Any]:
        if coin is None:
            self.open_orders = []
        else:
            self.open_orders = [o for o in self.open_orders if o["coin"] != coin]
        return {"status": "ok"}

    def open_orders_snapshot(self) -> List[Dict[str, Any]]:
        return [dict(o) for o in self.open_orders]

    # ── ledger ────────────────────────────────────────────────────────
    def fills_snapshot(self) -> List[Dict[str, Any]]:
        return [dict(f) for f in self.fills]

    def position_of(self, coin: str) -> float:
        return self.positions.get(coin, 0.0)