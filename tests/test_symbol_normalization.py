"""
Item (a) Fase 1: normalisasi simbol/coin sebagai satu fungsi.

MASALAH
-------
Kode produksi memakai DUA format kunci untuk hal yang sama:

  * engine.py:162  ->  "BTC / USDC:USDC"   (dibaca dari respons bursa)
  * executor.py:472 ->  "BTC/USDT:USDT"    (format internal repo)

`reconcile()` membandingkan kedua himpunan itu langsung, jadi koin yang
sama selalu muncul sebagai `only_local` DAN `only_remote`. Dengan
`auto_reconcile=False` (default), itu menyalakan kill switch.

Perbaikan ini memakai ATURAN KERAS dari Fase 1: "jangan men-stub
komponen yang sedang diuji". Test di bawah memanggil
`_fetch_remote_positions()` dan `reconcile()` sungguhan, dengan data
dari fixture rekaman API testnet -- bukan stub yang saya karakan.
"""
import asyncio
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from hl_live_fixtures import (
    CLEARINGHOUSE_STATE_EMPTY,
    CLEARINGHOUSE_STATE_WITH_POSITION,
    META_UNIVERSE,
    make_info_double,
)
from repro_helpers import clean_gate

from core.config import LiveConfig
from trading.live import engine as engine_mod
from trading.live.engine import LiveEngine, LivePosition


def make_exchange_with_state(state):
    """LiveExchange yang Info-nya disuntik fixture; transport di-fake."""
    from trading.live.client import LiveExchange

    ex = LiveExchange.__new__(LiveExchange)
    ex.testnet = True
    ex._account_address = None
    ex._rules = None
    ex.base_url = "https://api.hyperliquid-testnet.xyz/info"
    ex.info = make_info_double(state)
    ex.exchange = None
    ex.wallet = None
    ex.address = "0x5972698398d8c5bbe67c0db74906236691020417"
    ex.query_address = ex.address
    return ex


class TestSymbolNormalizationFunction(unittest.IsolatedAsyncioTestCase):
    """
    Fungsi normalisasi harus ada, dan harus mengembalikan format yang
    KONSISTEN untuk semua sumber kunci simbol.
    """

    def test_normalize_coin_returns_plain_ticker(self):
        """
        Nama koin dari bursa ("BTC") harus dinormalisasi ke format
        internal repo ("BTC/USDT:USDT").

        Bursa TIDAK PERNAH mengirim "BTC / USDC:USDC" -- itu format
        yang dikarang oleh produksi sendiri di engine.py:162.
        """
        from trading.live.engine import normalize_symbol

        self.assertEqual(normalize_symbol("BTC"), "BTC/USDT:USDT")
        self.assertEqual(normalize_symbol("btc"), "BTC/USDT:USDT")
        self.assertEqual(normalize_symbol("ETH"), "ETH/USDT:USDT")

    def test_normalize_is_idempotent(self):
        """Memanggil dua kali tidak boleh mengubah hasil."""
        from trading.live.engine import normalize_symbol

        once = normalize_symbol("SOL")
        twice = normalize_symbol(once)
        self.assertEqual(once, twice, "normalisasi harus idempoten")

    def test_normalize_handles_already_normalized_forms(self):
        """
        Bentuk yang sudah ada di produksi hari ini harus tetap cocok,
        supaya tidak ada caller yang tiba-tiba rusak.
        """
        from trading.live.engine import normalize_symbol

        for raw in ("BTC/USDT:USDT", "BTC / USDC:USDC", "BTC/USDC:USDC"):
            self.assertEqual(
                normalize_symbol(raw), "BTC/USDT:USDT",
                "bentuk %r harus dinormalisasi ke format internal" % raw,
            )

    def test_normalize_preserves_coin_with_underscore(self):
        """Nama koin seperti ini harus utuh, tidak dipotong spasi."""
        from trading.live.engine import normalize_symbol

        self.assertEqual(
            normalize_symbol("1000PEPE"), "1000PEPE/USDT:USDT",
        )


class TestReconcileUsesOneKeyFormat(unittest.IsolatedAsyncioTestCase):
    """
    `reconcile()` harus mencocokkan posisi lokal dan bursa dengan
    kunci yang sama.
    """

    def _engine(self, local_symbols):
        ex = make_exchange_with_state(CLEARINGHOUSE_STATE_WITH_POSITION)
        eng = LiveEngine(gate=clean_gate(), exchange=ex, cfg=LiveConfig())
        for sym in local_symbols:
            eng.positions[sym] = LivePosition(
                symbol=sym, coin="BTC", side="SHORT", size=0.24567,
                entry_price=85110.1, stop_loss=86000.0, take_profit=83000.0,
                leverage=5, sl_order_id=1, tp_order_id=2,
            )
        return eng

    async def test_remote_key_uses_internal_format(self):
        """
        Kunci yang dibaca dari bursa harus langsung format internal.

        Inilah inti perbaikannya: `_fetch_remote_positions`
        tidak boleh lagi mengarang format sendiri.
        """
        eng = self._engine([])
        remote = await asyncio.to_thread(eng._fetch_remote_positions)
        self.assertEqual(len(remote), 1)
        self.assertEqual(
            remote[0]["symbol"], "BTC/USDT:USDT",
            "kunci dari bursa harus format internal, bukan 'BTC / USDC:USDC'",
        )
        self.assertEqual(remote[0]["coin"], "BTC")

    async def test_matching_position_reports_no_divergence(self):
        """
        Posisi lokal dan bursa yang sama harus dilaporkan COCOK.

        Ini test yang gagal sebelum item (a) diperbaiki.
        """
        eng = self._engine(["BTC/USDT:USDT"])
        report = await eng.reconcile()
        self.assertEqual(
            report["only_local"], [],
            "posisi lokal tidak boleh dilaporkan hilang dari bursa",
        )
        self.assertEqual(
            report["only_remote"], [],
            "posisi bursa tidak boleh dilaporkan asing",
        )
        self.assertEqual(report["size_mismatch"], [])

    async def test_matching_position_does_not_engage_kill_switch(self):
        """
        Posisi yang benar-benar cocok tidak boleh menyalakan kill switch.
        """
        eng = self._engine(["BTC/USDT:USDT"])
        await eng.reconcile()
        self.assertFalse(
            eng.gate.engaged,
            "kill switch menyala setelah reconcile pada posisi yang cocok",
        )

    async def test_report_would_be_clean_for_matching_position(self):
        """
        Bentuk laporan yang seharusnya dihasilkan emergency_flat().
        """
        eng = self._engine(["BTC/USDT:USDT"])
        report = await eng.reconcile()
        flattened = not (
            report["only_local"] or report["only_remote"] or report["size_mismatch"]
        )
        self.assertTrue(
            flattened,
            "posisi cocok harus menghasilkan flattened=True; sebenarnya: "
            "only_local=%r only_remote=%r"
            % (report["only_local"], report["only_remote"]),
        )

    async def test_reconcile_still_detects_genuine_mismatch(self):
        """
        Normalisasi tidak boleh membuat reconcile BUTA.

        Posisi lokal yang benar-benar tidak ada di bursa harus tetap
        dilaporkan sebagai `only_local`.
        """
        ex = make_exchange_with_state(CLEARINGHOUSE_STATE_EMPTY)
        eng = LiveEngine(gate=clean_gate(), exchange=ex, cfg=LiveConfig())
        eng.positions["BTC/USDT:USDT"] = LivePosition(
            symbol="BTC/USDT:USDT", coin="BTC", side="SHORT", size=0.1,
            entry_price=85110.1, stop_loss=86000.0, take_profit=83000.0,
            leverage=5, sl_order_id=1, tp_order_id=2,
        )
        report = await eng.reconcile()
        self.assertEqual(
            report["only_local"], ["BTC/USDT:USDT"],
            "posisi yang benar-benar hilang dari bursa harus terdeteksi",
        )


class TestExecutorAndEngineAgreeOnKey(unittest.IsolatedAsyncioTestCase):
    """
    Executor dan Engine harus memakai kunci yang sama.

    `executor.py:472` menyimpan posisi dengan `order.symbol` (format
    internal), dan `engine.py` membuat kunci sendiri dari coin. Setelah
    item (a), keduanya harus menghasilkan string yang sama.
    """

    def test_no_runtime_code_string_concatenates_a_key(self):
        """
        Tidak boleh ada KODE (bukan dokumentasi) yang merangkai kunci
        simbol dengan template literal.

        Sebelumnya kedua titik di `engine.py` melakukan
        `"{} / USDC:USDC".format(coin)`. Sekarang keduanya lewat
        `normalize_symbol()`.

        Test memakai AST dan MEMBEDAKAN docstring dari kode: string yang
        jadi docstring sebuah fungsi/kelas, atau statement Expr di level
        modul, adalah dokumentasi -- dan dokumentasi memang perlu menyebut
        format lama supaya sejarahnya tercatat. Yang dilarang adalah
        string yang benar-benar dieksekusi.
        """
        import ast

        def _is_docstring(node):
            """String literal yang menempel pada def/class/module."""
            parent = getattr(node, "parent", None)
            return parent is None

        offenders = []
        for mod_name in ("trading/live/engine.py", "trading/live/executor.py"):
            path = pathlib.Path(__file__).resolve().parent.parent / mod_name
            tree = ast.parse(path.read_text(encoding="utf-8"))
            # Kumpulkan node yang HANYA berfungsi sebagai docstring.
            docstrings = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef,
                                      ast.AsyncFunctionDef, ast.ClassDef,
                                      ast.Module)):
                    body = getattr(node, "body", [])
                    if (body and isinstance(body[0], ast.Expr)
                            and isinstance(body[0].value, ast.Constant)
                            and isinstance(body[0].value.value, str)):
                        docstrings.add(id(body[0].value))

            for node in ast.walk(tree):
                if not isinstance(node, ast.Constant):
                    continue
                if not isinstance(node.value, str):
                    continue
                if "USDC:USDC" not in node.value:
                    continue
                if id(node) in docstrings:
                    continue          # dokumentasi, bukan kode
                offenders.append(
                    "%s:%d  %r" % (mod_name, node.lineno, node.value[:60])
                )
        self.assertFalse(
            offenders,
            " masih ada literal 'USDC:USDC' yang dieksekusi; semua kunci "
            "harus lewat normalize_symbol():\n  " + "\n  ".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()
