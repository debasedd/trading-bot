"""
tests/test_order_book_recorder.py — Rekorder order book historis.

Test-test di sini mengunci dua hal yang keduanya sudah pernah rusak:

  1. Bobot depth `1.0 - 0.1*i` jadi NEGATIF mulai level ke-11. Memakai
     20 level membuat ask_depth negatif dan depth_imbalance melonjak di
     luar rentang [-1, 1] pada book yang benar-benar diam. Produksi aman
     karena default depth-nya 5, jadi yang rusak hanya rekonder.
  2. Schema SQL harus bisa dieksekusi. Emoji atau karakter aneh di dalam
     string SQL tidak akan terlihat di preview — hanya saat bot start.
"""
import os
import sqlite3
import unittest
from pathlib import Path

from data.order_book_recorder import SCHEMA, OrderBookRecorder


def make_book(bids, asks, ts=1_700_000_000_000):
    bb = bids[0][0]
    ba = asks[0][0]
    return {
        "symbol": "BTC/USDT:USDT",
        "coin": "BTC",
        "bids": [[float(p), float(s)] for p, s in bids],
        "asks": [[float(p), float(s)] for p, s in asks],
        "spread": ba - bb,
        "mid_price": (bb + ba) / 2.0,
        "timestamp": ts,
    }


class TestSchema(unittest.TestCase):
    def test_schema_executes(self):
        """SCHEMA harus bisa dieksekusi sqlite tanpa error."""
        conn = sqlite3.connect(":memory:")
        conn.executescript(SCHEMA)
        tables = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        self.assertIn("order_book_features", tables)
        self.assertIn("order_book_raw", tables)
        conn.close()

    def test_schema_is_idempotent(self):
        conn = sqlite3.connect(":memory:")
        conn.executescript(SCHEMA)
        conn.executescript(SCHEMA)   # harus tidak error
        conn.close()


class TestFeatureMath(unittest.TestCase):
    def _rec(self):
        r = OrderBookRecorder(":memory:", [])
        r.open()
        return r

    def test_depth_imbalance_stays_in_unit_range(self):
        """
        Book dengan 20 level per sisi tidak boleh menghasilkan
        depth_imbalance di luar [-1, 1].

        Ini regresi nyata: bobot `1.0 - 0.1*i` negatif mulai i=11, jadi
        `bids[:20]` membuat satu sisi depth negatif dan imbalance-nya
        meledak. Ditemukan saat menjalankan bot sungguhan.
        """
        r = self._rec()
        # 20 level, size seragam, harga turun dari best bid.
        bids = [(100.0 - i * 0.1, 5.0) for i in range(20)]
        asks = [(100.2 + i * 0.1, 5.0) for i in range(20)]
        feats = r._features("BTC/USDT:USDT", make_book(bids, asks), 1)
        self.assertIsNotNone(feats, "book 20 level harus diterima")
        # index 12 = depth_imbalance di tuple return
        depth_imb = feats[12]
        self.assertGreaterEqual(depth_imb, -1.0,
                                "depth_imbalance di bawah -1")
        self.assertLessEqual(depth_imb, 1.0,
                             "depth_imbalance di atas 1")

    def test_ask_depth_never_negative(self):
        r = self._rec()
        bids = [(100.0 - i * 0.1, 3.0) for i in range(20)]
        asks = [(100.2 + i * 0.1, 3.0) for i in range(20)]
        feats = r._features("BTC/USDT:USDT", make_book(bids, asks), 1)
        ask_depth = feats[11]
        self.assertGreater(ask_depth, 0.0,
                           "ask_depth negatif berarti bobotnya salah")

    def test_equal_books_give_zero_imbalance(self):
        r = self._rec()
        bids = [(100.0 - i * 0.1, 4.0) for i in range(10)]
        asks = [(100.2 + i * 0.1, 4.0) for i in range(10)]
        feats = r._features("BTC/USDT:USDT", make_book(bids, asks), 1)
        self.assertAlmostEqual(feats[12], 0.0, places=9)
        self.assertAlmostEqual(feats[9], 0.0, places=9)   # top_imbalance

    def test_heavy_bid_side_gives_positive_imbalance(self):
        r = self._rec()
        bids = [(100.0 - i * 0.1, 10.0) for i in range(10)]
        asks = [(100.2 + i * 0.1, 2.0) for i in range(10)]
        feats = r._features("BTC/USDT:USDT", make_book(bids, asks), 1)
        self.assertGreater(feats[12], 0.0, "bids lebih besar harus positif")
        self.assertLess(feats[12], 1.0)


class TestInputValidation(unittest.TestCase):
    def _rec(self):
        r = OrderBookRecorder(":memory:", [])
        r.open()
        return r

    def test_thin_book_rejected(self):
        """Book dengan 1 level tidak punya spread. Tidak direkam."""
        r = self._rec()
        book = make_book([(100.0, 1.0)], [(100.2, 1.0)])
        self.assertIsNone(r._features("BTC/USDT:USDT", book, 1))

    def test_inverted_book_rejected(self):
        """Best bid > best ask berarti book rusak, bukan spread besar."""
        r = self._rec()
        book = make_book(
            [(101.0, 1.0), (100.9, 1.0)],
            [(100.0, 1.0), (100.1, 1.0)],
        )
        self.assertIsNone(
            r._features("BTC/USDT:USDT", book, 1),
            "book terbalik harus ditolak, bukan disimpan",
        )

    def test_zero_price_rejected(self):
        r = self._rec()
        book = make_book([(0.0, 1.0), (0.0, 1.0)], [(1.0, 1.0), (1.1, 1.0)])
        self.assertIsNone(r._features("BTC/USDT:USDT", book, 1))


class TestOFI(unittest.TestCase):
    def test_ofi_is_none_on_first_snapshot(self):
        """
        OFI adalah PERUBAHAN, jadi tanpa snapshot sebelumnya tidak ada
        yang bisa dibandingkan. None lebih benar dari 0.0, karena 0.0
        berarti "tidak ada aliran" dan akan dilatih sebagai sinyal.
        """
        r = OrderBookRecorder(":memory:", [])
        r.open()
        book = make_book([(100.0, 5.0), (99.9, 3.0)],
                         [(100.2, 4.0), (100.3, 2.0)])
        feats = r._features("BTC/USDT:USDT", book, 1)
        self.assertIsNone(feats[13])

    def test_ofi_computed_on_second_snapshot(self):
        r = OrderBookRecorder(":memory:", [])
        r.open()
        b1 = make_book([(100.0, 5.0), (99.9, 3.0)],
                       [(100.2, 4.0), (100.3, 2.0)])
        r._features("BTC/USDT:USDT", b1, 1)
        # Snapshot kedua: bid bertambah, ask berkurang -> tekanan naik.
        b2 = make_book([(100.0, 9.0), (99.9, 3.0)],
                       [(100.2, 1.0), (100.3, 2.0)])
        feats = r._features("BTC/USDT:USDT", b2, 2)
        self.assertIsNotNone(feats[13])
        self.assertGreater(feats[13], 0.0,
                           "bid naik + ask turun harus OFI positif")

    def test_ofi_negative_when_selling_pressure(self):
        r = OrderBookRecorder(":memory:", [])
        r.open()
        # Kedua sisi butuh minimal 2 level, kalau tidak snapshot-nya
        # ditolak dan tidak ada yang tersimpan.
        b1 = make_book([(100.0, 5.0), (99.9, 5.0)],
                       [(100.2, 5.0), (100.3, 5.0)])
        r._features("BTC/USDT:USDT", b1, 1)
        b2 = make_book([(100.0, 1.0), (99.9, 1.0)],
                       [(100.2, 9.0), (100.3, 9.0)])
        feats = r._features("BTC/USDT:USDT", b2, 2)
        self.assertIsNotNone(feats)
        self.assertLess(feats[13], 0.0)


if __name__ == "__main__":
    unittest.main()
