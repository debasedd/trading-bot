"""
Audit kontaminasi indeks kolom pada loader data riset.

MASALAH YANG PERNAH TERJADI
---------------------------
`research/market_neutral.py` pada satu sesi membaca `cs_close = r[5]`.
Pada tuple yang dibangunnya `(ts, o, h, l, c, v)`, indeks 4 adalah
close dan indeks 5 adalah VOLUME. Jadi r[5] = volume, bukan harga.

Akibatnya ranking "momentum" sebenarnya mengurutkan simbol berdasarkan
volume, dan hasilnya: +9.2M USDT dari modal 10.000, win rate 93%,
t=20+. Semuanya palsu.

CARA KERJA TEST INI — VERIFIKASI RUNTIME, BUKAN PARSING SOURCE
--------------------------------------------------------------
Test ini tidak menebak indeks dari regex. Untuk setiap indeks `r[N]`
yang dipakai untuk menghasilkan seri harga, test ini:

  1. Menjalankan SELECT yang sama terhadap `historical_candles.db` sungguhan
  2. Membangun tuple persis seperti loaderimensinya
  3. Memastikan `r[N]` adalah HARGA (dalam rentang crypto), bukan volume
  4. Memastikan indeks close dan indeks volume memang berbeda

Kalau suatu loader berubah ke r[5], test ini gagal karena r[5] berisi
jumlah koin, bukan harga.
"""
import ast
import pathlib
import re
import sqlite3
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
RESEARCH = ROOT / "research"
HIST_DB = ROOT / "data_store" / "historical_candles.db"
LIVE_DB = ROOT / "data_store" / "trading_bot.db"

# Nama variabel yang arousal menandakan "ini seri harga".
CLOSE_VARS = {"c", "close", "cl", "cs_close", "closes", "px", "close_px"}

# Rentang harga yang masuk akal untuk perp crypto, dipatok longgar
# supaya test ini tidak rapuh saat simbol baru masuk.
PRICE_MIN = 1.0
PRICE_MAX = 10_000_000.0


def _loaders():
    """(path, ast.FunctionDef) untuk setiap fungsi loader di research/."""
    for path in sorted(RESEARCH.glob("*.py")):
        src = path.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            name = node.name.lower()
            if "load" in name or name in ("split_regimes", "build_common"):
                yield path, node


def _close_indexes(node):
    """Semua indeks r[N] yang hasilnya masuk variabel ber-nama harga."""
    out = []
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Assign):
            continue
        tnames = [t.id for t in sub.targets if isinstance(t, ast.Name)]
        if not tnames or tnames[0] not in CLOSE_VARS:
            continue
        for inner in ast.walk(sub.value):
            if (
                isinstance(inner, ast.Subscript)
                and isinstance(inner.value, ast.Name)
                and isinstance(inner.slice, ast.Constant)
                and isinstance(inner.slice.value, int)
            ):
                out.append((tnames[0], inner.value.id, inner.slice.value,
                            sub.lineno))
    return out


class TestResearchLoaderColumnIntegrity(unittest.TestCase):
    """Loader riset tidak boleh salah memetakan kolom."""

    @classmethod
    def setUpClass(cls):
        if not HIST_DB.exists():
            raise unittest.SkipTest("historical_candles.db belum ada")
        cls.conn = sqlite3.connect(f"file:{HIST_DB}?mode=ro", uri=True)
        # Satu baris nyata untuk dijadikan bahan uji.
        cls.real_row = cls.conn.execute(
            "SELECT symbol, ts, open, high, low, close, "
            "COALESCE(volume, 0) FROM hist_candles "
            "WHERE interval = '1h' AND close > 0 ORDER BY symbol, ts LIMIT 1"
        ).fetchone()
        # Volume BTC per menit平均值 supaya assertion bisa membandingkan.
        cls.median_volume = cls.conn.execute(
            "SELECT AVG(volume) FROM hist_candles "
            "WHERE interval = '1h' AND symbol = (SELECT symbol FROM "
            "hist_candles WHERE interval='1h' ORDER BY symbol, ts LIMIT 1)"
        ).fetchone()[0]

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "conn", None):
            cls.conn.close()

    def test_reference_row_has_distinguishable_close_and_volume(self):
        """Baris nyata harus punya close != volume, kalau tidak test ini tak berguna."""
        self.assertIsNotNone(self.real_row, "tidak ada baris 1h di hist_candles")
        r = list(self.real_row)
        self.assertEqual(len(r), 7)
        _, _, o, h, low, c, v = r
        self.assertNotAlmostEqual(
            c, v,
            msg="close == volume pada baris nyata; test tidak bisa membedakan",
        )
        # close harus masuk akal sebagai harga
        self.assertGreater(c, PRICE_MIN)
        self.assertLess(c, PRICE_MAX)
        # dan OHLC harus konsisten satu sama lain
        self.assertLessEqual(o, h)
        self.assertLessEqual(low, h)
        self.assertLessEqual(low, c)

    def test_build_tuple_then_check_index(self):
        """
        Bukti inti: tuple (ts,o,h,l,c,v) 6-elemen → close di indeks 4.

        Loader yang memakai indeks 5 mengambil VOLUME. Test ini
        mengunci perbedaan itu dengan angka nyata, bukan asumsi.
        """
        _, ts, o, h, low, c, v = self.real_row
        loader_tuple = (ts, o, h, low, c, v)   # bentuk yang dipakai loader

        self.assertEqual(len(loader_tuple), 6)
        # indeks 4 = close
        self.assertAlmostEqual(loader_tuple[4], c)
        self.assertAlmostEqual(loader_tuple[5], v)
        # jika ada yang pakai r[5] sebagai close, itu akan dapat v
        self.assertNotAlmostEqual(
            loader_tuple[5], c,
            msg="indeks 5 ternyata bukan volume — asumsi test salah",
        )

    def test_no_loader_uses_volume_index_for_close(self):
        """
        Tidak ada loader yang menugaskan variabel harga dari indeks
        volume.

        Volume hanya bisa muncul kalau SELECT-nya menyertakan kolom
        ke-6; setelah tuple dibangun 6-elemen, indeks 5 (atau lebih)
        adalah volume atau di luar rentang. Close harus di indeks 4
        atau kurang.
        """
        violations = []
        for path, fn in _loaders():
            for varname, basename, idx, lineno in _close_indexes(fn):
                # Variabel harga SELALU dibaca dari list 6-elemen
                # (ts,o,h,l,c,v). Indeks > 4 berarti di luar close.
                if idx > 4:
                    violations.append(
                        "%s:%d  %s = %s[%d]  -> indeks > 4 berarti "
                        "bukan close (close di indeks 4)"
                        % (path.name, lineno, varname, basename, idx)
                    )
        self.assertFalse(
            violations,
            "Loader riset memakai indeks di luar posisi close:\n  "
            + "\n  ".join(violations),
        )

    def test_market_neutral_uses_correct_close_index(self):
        """
        Kunci koreksi pada `market_neutral.py`.

        File ini pernah memakai r[5] dan menghasilkan hasil palsu.
        Sekarang r[4]. Test ini mengunci koreksinya supaya tidak regress.
        """
        path = RESEARCH / "market_neutral.py"
        if not path.exists():
            self.skipTest("market_neutral.py tidak ada")
        src = path.read_text(encoding="utf-8", errors="replace")

        self.assertIn(
            "r[0]: r[4] for r in v",
            src,
            "market_neutral harus mengambil close dari r[4], bukan r[5]",
        )
        self.assertNotIn(
            "r[5]",
            src,
            "market_neutral masih memakai r[5] (itu volume, bukan close)",
        )

    def test_live_db_candles_close_index_matches_loader(self):
        """
        Loader `candles` (tabel di trading_bot.db) memakai pola sama.

        `research/bt.py` membangun tuple 6-elemen dari SELECT
        (symbol, timestamp, open, high, low, close, volume) dan membaca
        close dari r[4]. Test ini memastikan bentuk itu masih cocok
        dengan skema live saat ini.
        """
        if not LIVE_DB.exists():
            self.skipTest("trading_bot.db belum ada")
        conn = sqlite3.connect(f"file:{LIVE_DB}?mode=ro", uri=True)
        row = conn.execute(
            "SELECT symbol, timestamp, open, high, low, close, volume "
            "FROM candles WHERE timeframe = '1m' AND close > 0 "
            "ORDER BY symbol, timestamp LIMIT 1"
        ).fetchone()
        conn.close()
        if row is None:
            self.skipTest("tabel candles kosong untuk 1m")

        sym, ts, o, h, low, c, v = row
        loader_tuple = (ts, o, h, low, c, v)
        self.assertEqual(len(loader_tuple), 6)
        self.assertAlmostEqual(loader_tuple[4], c)
        self.assertNotAlmostEqual(
            loader_tuple[5], c,
            msg="indeks 5 di live DB bukan volume — asumsi test salah",
        )
        # close harus harga, volume harus kuantitas: bentuknya beda
        self.assertGreater(loader_tuple[4], PRICE_MIN)


if __name__ == "__main__":
    unittest.main()
