"""
REPRODUKSI defect jalur live. Test-file ini SENGAJA GAGAL.

Fase 0 tidak memperbaiki apa pun. Tugasnya membuktikan bahwa defect-nya
ada, dengan cara yang tidak bisa berbohong -- pakai data nyata, bukan
stub, dan bukan asumsi tentang bentuk respons.

Aturan yang dipakai di sini:
  * Fixture berasal dari API publik Hyperliquid testnet yang benar-benar
    dipanggil pada 2026-10-03 (lihat tests/hyperliquid_fixtures.py).
  * Tidak ada stub pada komponen yang sedang diuji. Untuk DEFECT-1, yang
    diuji `LiveExchange.symbol_notional` membaca respons nyata; yang
    di-fake hanya transport-nya, karena tesnet tidak mengizinkan order.
  * Test yang membuktikan FAKTA (bukan defect) tidak diberi xfail --
    ia harus lulus, kalau tidak reprokusinya tidak bermakna.
  * Test yang membuktikan DEFECT diberi `@pytest.mark.xfail(strict=True)`.
    Kalau defect diperbaiki dan test mulai lulus, pytest GAGAL dengan
    "XPASS(strict)" -- jadi perbaikan tidak bisa lolos diam-diam.
  * Tidak ada env var live produksi yang di-set di luar helper.
    Tidak ada order. Tidak ada private key sungguhan.
"""
import asyncio
import pathlib
import sys
import unittest

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from hyperliquid_fixtures import (
    CLEARINGHOUSE_STATE_EMPTY,
    CLEARINGHOUSE_STATE_WITH_POSITION,
    EXPECTED_BTC_ASSET_INDEX,
    EXPECTED_BTC_NOTIONAL,
    make_exchange_with_state,
)
from known_broken import known_broken
from repro_helpers import clean_gate

from core.config import LiveConfig
from trading.live.engine import LiveEngine, LivePosition


# ═══════════════════════════════════════════════════════════════════
# FAKTA DASAR — harus LULUS, bukan xfail.
# Tanpa ini, test di bawahnya tidak punya arti.
# ═══════════════════════════════════════════════════════════════════


class TestFixtureFacts(unittest.TestCase):
    """Fakta tentang respons nyata yang jadi dasar reproduksi."""

    def test_coin_field_from_exchange_is_a_string(self):
        pos = CLEARINGHOUSE_STATE_WITH_POSITION["assetPositions"][0]["position"]
        self.assertIsInstance(pos["coin"], str)
        self.assertEqual(pos["coin"], "BTC")

    def test_numeric_fields_from_exchange_are_strings(self):
        pos = CLEARINGHOUSE_STATE_WITH_POSITION["assetPositions"][0]["position"]
        # Semua angka datang sebagai string desimal dari bursa.
        self.assertIsInstance(pos["szi"], str)
        self.assertIsInstance(pos["entryPx"], str)
        self.assertNotIsInstance(pos["szi"], float)

    def test_position_fixture_matches_expected_notional(self):
        pos = CLEARINGHOUSE_STATE_WITH_POSITION["assetPositions"][0]["position"]
        expected = abs(float(pos["szi"])) * float(pos["entryPx"])
        self.assertAlmostEqual(expected, EXPECTED_BTC_NOTIONAL, places=2)

    def test_leverage_is_an_object_not_an_integer(self):
        """Ada di respons nyata; tidak ada di docstring SDK."""
        pos = CLEARINGHOUSE_STATE_WITH_POSITION["assetPositions"][0]["position"]
        self.assertIsInstance(pos["leverage"], dict)
        self.assertEqual(pos["leverage"]["type"], "cross")


# ═══════════════════════════════════════════════════════════════════
# DEFECT 1 — client.py:219, int() pada field string
#
# `position.coin` dari bursa adalah STRING ("BTC"). Kode produksi
# menulis `int(pos.get("coin", -1))`. Selama ada satu pun posisi
# terbuka, setiap panggilan symbol_notional() melempar ValueError.
#
# Dampaknya bukan kosmetik: _remote_context() memanggilnya SEBELUM
# gerbang safety dikonsultasi, jadi tidak ada order yang bisa lolos --
# yang error-nya muncul duluan dan tertelan except yang lebar.
# ═══════════════════════════════════════════════════════════════════



    # ---- Jalur yang memang tidak rusak -----------------------------
# Tiga test di bawah ini MENJADI LULUS, dan itu memang benar:
# keduanya adalah jalur yang tidak melewati kode yang rusak.
# Mereka tetap di sini supaya perbaikan nanti tidak Reviews salah
# tempat -- dan supaya tidak ada test yang hilang diam-diam.


    def test_empty_state_path_does_not_raise(self):
        """Jalur tanpa posisi harus tetap jalan (tidak ada yang di-int)."""
        ex = make_exchange_with_state(CLEARINGHOUSE_STATE_EMPTY, "BTC")
        self.assertEqual(ex.symbol_notional("BTC"), 0.0)


    def test_total_notional_unaffected(self):
        """
        total_notional() memakai float(pos["szi"]) yang menerima string.

        Jadi hanya symbol_notional() yang rusak. Test ini memisahkan
        dua masalah itu -- bukan berarti symbol_notional tidak rusak.
        """
        ex = make_exchange_with_state(CLEARINGHOUSE_STATE_WITH_POSITION, "BTC")
        self.assertAlmostEqual(
            ex.total_notional(), EXPECTED_BTC_NOTIONAL, places=2
        )


# Bentuk kunci yang dipakai sisi bursa, direkam apa adanya dari respons
# nyata. Ini fakta -- bukan defect -- jadi harus lulus. Test ini yang
# membuat test DEFECT-2 bermakna: kalau format ini berubah, kedua sisi
# harus dibandingkan ulang.


class TestRemoteKeyFormatFact(unittest.IsolatedAsyncioTestCase):

    async def test_remote_key_format_is_verbatim(self):
        from trading.live.engine import LiveEngine
        from core.config import LiveConfig

        ex = make_exchange_with_state(CLEARINGHOUSE_STATE_WITH_POSITION, "BTC")
        engine = LiveEngine(gate=clean_gate(), exchange=ex, cfg=LiveConfig())
        remote = await asyncio.to_thread(engine._fetch_remote_positions)
        self.assertEqual(len(remote), 1)
        self.assertEqual(remote[0]["symbol"], "BTC / USDC:USDC")
        self.assertEqual(remote[0]["coin"], "BTC")


@known_broken("DEFECT-1 client.py:219 int() pada field string")
class TestSymbolNotionalIntOnStringField(unittest.IsolatedAsyncioTestCase):
    """Reproduksi: symbol_notional harus bisa membaca coin berupa string."""

    def test_symbol_notional_does_not_raise_on_real_position(self):
        """HARUS TIDAK MELEMPAR ValueError. Sekarang: int('BTC')."""
        ex = make_exchange_with_state(CLEARINGHOUSE_STATE_WITH_POSITION, "BTC")
        value = ex.symbol_notional("BTC")   # <-- sekarang ValueError
        self.assertIsInstance(value, float)

    def test_symbol_notional_returns_correct_number(self):
        """Bukan cuma "tidak melempar" -- angkanya harus benar."""
        ex = make_exchange_with_state(CLEARINGHOUSE_STATE_WITH_POSITION, "BTC")
        self.assertAlmostEqual(
            ex.symbol_notional("BTC"), EXPECTED_BTC_NOTIONAL, places=2
        )

    def test_symbol_notional_finds_eth_in_empty_state(self):
        """
        Nama koin yang tidak ada di posisi harus mengembalikan 0.0,
        bukan melempar.
        """
        ex = make_exchange_with_state(CLEARINGHOUSE_STATE_WITH_POSITION, "BTC")
        self.assertEqual(ex.symbol_notional("ETH"), 0.0)



    async def test_submit_order_never_reaches_the_gate(self):
        """
        DAMPAK PENUH: order tidak pernah sampai ke gerbang safety.

        _remote_context() memanggil symbol_notional() sebelum can_send(),
        jadi ValueError terjadi sebelum gerbang sempat mengecek -- termasuk
        sebelum ia sempat menolak order karena alasan yang benar.
        """
        ex = make_exchange_with_state(CLEARINGHOUSE_STATE_WITH_POSITION, "BTC")
        engine = LiveEngine(gate=clean_gate(), exchange=ex, cfg=LiveConfig())

        result = await engine.submit_order(
            "BTC", "BTC/USDT:USDT", True, 0.001, 85000.0,
            stop_loss=84000.0, take_profit=87000.0, leverage=5,
            cloid="tb-repro-0001",
        )
        # Kalau defect ada: submit_order melempar ValueError dari
        # _remote_context() SEBELUM gate.can_send() dipanggil.
        # Kalau diperbaiki: hasilnya dict.
        self.assertIsInstance(
            result, dict,
            "submit_order mengembalikan dict; ValueError berarti "
            "symbol_notional() masih melempar sebelum gate dicek",
        )

# ═══════════════════════════════════════════════════════════════════
# DEFECT 2 — format kunci simbol berbeda
#
# engine.py:162 membuat kunci "BTC / USDC:USDC" dari respons bursa.
# executor.py:472 menyimpan posisi dengan kunci "BTC/USDT:USDT"
# (format simbol internal repo).
#
# reconcile() membandingkan kedua himpunan itu langsung. Koin yang sama
# punya dua kunci berbeda, jadi SELALU muncul di only_local DAN
# only_remote -- bahkan saat posisinya benar-benar cocok.
#
# Dengan auto_reconcile=False (default), itu menyalakan kill switch.
# ═══════════════════════════════════════════════════════════════════


@known_broken("DEFECT-2 format kunci simbol tidak sinkron")
class TestReconcileSymbolKeyMismatch(unittest.IsolatedAsyncioTestCase):
    """Reproduksi: reconcile harus bisa mencocokkan posisi lokal vs bursa."""

    def _engine(self, local_positions):
        ex = make_exchange_with_state(CLEARINGHOUSE_STATE_WITH_POSITION, "BTC")
        engine = LiveEngine(gate=clean_gate(), exchange=ex, cfg=LiveConfig())
        for entry in local_positions:
            engine.positions[entry["symbol"]] = LivePosition(
                symbol=entry["symbol"], coin="BTC", side="SHORT",
                size=0.24567, entry_price=85110.1,
                stop_loss=None, take_profit=None, leverage=5,
                sl_order_id=1, tp_order_id=2,
            )
        return engine


    async def test_matching_position_reports_no_divergence(self):
        """
        Posisi lokal dan bursa yang sama harus dilaporkan COCOK.

        Sekarang: only_local dan only_remote sama-sama berisi BTC karena
        format kuncinya beda -- padahal itu posisi yang sama persis.
        """
        engine = self._engine([{"symbol": "BTC/USDT:USDT"}])
        report = await engine.reconcile()
        self.assertEqual(report["only_local"], [])
        self.assertEqual(report["only_remote"], [])
        self.assertEqual(report["size_mismatch"], [])

    async def test_matching_position_does_not_engage_kill_switch(self):
        """
        Posisi yang benar-benar cocok tidak boleh menyalakan kill switch.

        Ini akibat berbahaya: reconcile() juga dipanggil dari
        emergency_flat(), jadi emergency_flat tidak akan pernah melaporkan
        "flattened" -- walau semua posisi benar-benar tertutup.
        """
        engine = self._engine([{"symbol": "BTC/USDT:USDT"}])
        gate = engine.gate
        await engine.reconcile()
        self.assertFalse(
            gate.engaged,
            "kill switch menyala setelah reconcile pada posisi yang cocok",
        )

    async def test_report_would_be_clean_for_matching_position(self):
        """
        Bentuk laporan yang seharusnya dihasilkan emergency_flat.

        Sekarang `flattened` tidak pernah True karena divergensi palsu.
        """
        engine = self._engine([{"symbol": "BTC/USDT:USDT"}])
        report = await engine.reconcile()
        flattened = not (
            report["only_local"] or report["only_remote"] or report["size_mismatch"]
        )
        self.assertTrue(
            flattened,
            "posisi yang cocok harus menghasilkan flattened=True; "
            "yang sebenarnya: only_local=%r only_remote=%r"
            % (report["only_local"], report["only_remote"]),
        )


# ═══════════════════════════════════════════════════════════════════
# DEFECT 3 — health_check mengunci kill switch pada fill normal
#
# Tidak ada langganan fill di trading/live/. Jadi ketika SL atau TP
# benar-benar fires di bursa, Python tidak diberi tahu.
#
# Siklus berikutnya: health_check melihat koin hilang dari bursa tapi
# masih ada di self.positions -> problem -> engage_kill_switch.
#
# Efeknya: strike pertama pada setiap posisi -- termasuk take profit
# yang wajar -- menghentikan trading secara permanen.
# ═══════════════════════════════════════════════════════════════════


@known_broken("DEFECT-3 health_check latch saat fill normal")
class TestHealthCheckLatchesOnNormalFill(unittest.IsolatedAsyncioTestCase):
    """Reproduksi: fill SL/TP normal tidak boleh menghentikan bot."""

    def _engine_with_stale_local_position(self, exchange_factory):
        ex = exchange_factory()
        engine = LiveEngine(gate=clean_gate(), exchange=ex, cfg=LiveConfig())
        engine.positions["BTC/USDT:USDT"] = LivePosition(
            symbol="BTC/USDT:USDT", coin="BTC", side="SHORT", size=0.24567,
            entry_price=85110.1, stop_loss=86000.0, take_profit=83000.0,
            leverage=5, sl_order_id=1, tp_order_id=2,
        )
        return engine

    async def test_take_profit_fill_does_not_trip_kill_switch(self):
        """
        Posisi hilang dari bursa karena TP fires = BERHASIL, bukan
        kegagalan.
        """
        engine = self._engine_with_stale_local_position(
            lambda: make_exchange_with_state(CLEARINGHOUSE_STATE_EMPTY, "BTC")
        )
        health = await engine.health_check()
        self.assertFalse(
            engine.gate.engaged,
            "kill switch menyala setelah take-profit normal: %s"
            % health["problems"],
        )
        self.assertTrue(health["ok"], "health check gagal: %s" % health["problems"])

    async def test_stop_loss_fill_does_not_trip_kill_switch(self):
        """
        Sama untuk SL -- hasilnya juga penutupan yang berhasil.
        """
        engine = self._engine_with_stale_local_position(
            lambda: make_exchange_with_state(CLEARINGHOUSE_STATE_EMPTY, "BTC")
        )
        health = await engine.health_check()
        self.assertFalse(
            engine.gate.engaged,
            "kill switch menyala setelah stop-loss normal: %s" % health["problems"],
        )

    async def test_resting_order_without_position_does_not_trip_kill_switch(self):
        """
        Order GTC yang belum terisi tidak punya LivePosition -- itu definisi
        dari order resting, bukan kondisi error.

        health_check:624-628 menandainya sebagai problem, dan problem
        apa pun memanggil engage_kill_switch.
        """
        from hyperliquid_fixtures import make_info_double

        class _ExchangeWithRestingOrder:
            """
            Bursa dengan satu order GTC yang belum terisi.

            Bentuk order disalin dari apa yang frontendOpenOrders bursa
            kirimkan: field numerik berupa string desimal, `orderType`
            "Limit", `tif` "Gtc". Eksposisi di bursa kosong -- order
            resting tanpa posisi adalah kondisi yang harus diuji.
            """

            def __init__(self):
                self.info = make_info_double(CLEARINGHOUSE_STATE_EMPTY)

            def open_orders(self):
                return [{
                    "coin": "ETH",
                    "limitPx": "3000.0",
                    "sz": "1.0",
                    "side": "B",
                    "oid": 123456789,
                    "cloid": None,
                    "orderType": "Limit",
                    "tif": "Gtc",
                    "reduceOnly": False,
                    "triggerPx": None,
                }]

            def positions(self):
                return []

            def frontend_open_orders(self, address):
                return self.open_orders()

            def user_state(self, address, dex=""):
                return CLEARINGHOUSE_STATE_EMPTY

        engine = LiveEngine(
            gate=clean_gate(), exchange=_ExchangeWithRestingOrder(),
            cfg=LiveConfig(),
        )
        health = await engine.health_check()
        self.assertFalse(
            engine.gate.engaged,
            "kill switch menyala karena ada order GTC yang belum terisi: %s"
            % health["problems"],
        )


# ═══════════════════════════════════════════════════════════════════
# DEFECT 4 — partial fill tidak ditangani
#
# place_limit_order mengembalikan OrderOutcome dengan filled_size yang
# bisa lebih kecil dari ukuran yang diminta. Tidak ada kode yang:
#   - membandingkan filled_size dengan ukuran yang diminta
#   - membatalkan sisa order yang masih resting
#
# Akibatnya proteksi dipasang untuk ukuran PARSIAL sementara sisa
# order GTC tetap hidup tanpa proteksi.
# ═══════════════════════════════════════════════════════════════════


@known_broken("DEFECT-4 partial fill tidak ditangani")
class TestPartialFillUnhandled(unittest.IsolatedAsyncioTestCase):
    """Reproduksi: partial fill harus ditangani, bukan diabaikan diam-diam."""

    async def test_partial_fill_leaves_unprotected_remainder(self):
        """
        Order 0.01 terisi 0.004 -> sisa 0.006 tetap resting tanpa SL.

        Yang diuji: LiveEngine.submit_order sungguhan. Yang di-fake hanya
        bursa, dan angka filled-nya dikembalikan lewat OrderOutcome yang
        sama seperti parser produksi memakai.
        """
        from trading.live.client import OrderOutcome

        requested = 0.01
        filled = 0.004

        class _ExchangePartialFill:
            def __init__(self):
                self.cancelled = []
                self.protected = []

            def free_collateral(self):
                return 10000.0

            def total_notional(self):
                return 0.0

            def symbol_notional(self, coin):
                return 0.0

            def set_leverage(self, coin, leverage, is_cross=True):
                return {"ok": True}

            def place_limit_order(self, coin, is_buy, size, price,
                                  reduce_only=False, cloid=None):
                return OrderOutcome(ok=True, filled_size=filled,
                                    avg_price=price, order_id=42)

            def place_trigger_order(self, coin, is_buy, size, trigger_price,
                                    tpsl, reduce_only=True):
                self.protected.append((tpsl, size))
                return OrderOutcome(ok=True, filled_size=size, oid=1)

            def cancel(self, coin, oid):
                self.cancelled.append(oid)
                return "cancelled"

            def cancel_all(self, coin):
                return "cancelled"

            def open_orders(self):
                return [{"coin": "BTC", "oid": 42,
                         "sz": str(requested - filled), "limitPx": "85000.0"}]

            def positions(self):
                return []

            def mid_price(self, coin):
                return 85000.0

        ex = _ExchangePartialFill()
        engine = LiveEngine(gate=clean_gate(), exchange=ex, cfg=LiveConfig())

        result = await engine.submit_order(
            "BTC", "BTC/USDT:USDT", True, requested, 85000.0,
            stop_loss=84000.0, take_profit=87000.0, leverage=5,
            cloid="tb-repro-partial",
        )

        # Sisa order partial HARUS dibatalkan, atau setidaknya dilaporkan.
        self.assertTrue(
            ex.cancelled,
            "sisa order partial (%.4f) tidak pernah dibatalkan; proteksi "
            "hanya dipasang untuk %.4f sementara %.4f tetap resting"
            % (requested - filled, filled, requested - filled),
        )
        self.assertIn(
            "partial", str(result.get("message", "")).lower(),
            "partial fill tidak dilaporkan eksplisit: %r" % result.get("message"),
        )


# ═══════════════════════════════════════════════════════════════════
# DEFECT 5 — tidak ada lookup order by cloid, cancel() tidak terpakai
#
# cloid dibuat untuk setiap order opening dengan alasan yang tertulis
# eksplisit di client.py:384-387 dan engine.py:225-228: kalau respons
# hilang karena timeout, hanya cloid yang bisa menjawab "apakah order
# saya sudah masuk?".
#
# Tapi tidak ada kode yang melakukan lookup itu. LiveExchange.cancel(
# coin, oid) juga tidak pernah dipanggil dari produksi mana pun.
#
# Akibatnya setelah timeout, siklus berikutnya bisa mengirim order
# kedua untuk posisi yang sama: posisi tergandakan.
# ═══════════════════════════════════════════════════════════════════


@known_broken("DEFECT-5 tidak ada lookup order by cloid")
class TestNoCloidLookupOrCancel(unittest.IsolatedAsyncioTestCase):
    """Reproduksi: harus ada cara memastikan order timeout tidak masuk."""

    def test_live_exchange_has_order_status_lookup(self):
        """
        Harus ada cara menanyakan status order lewat cloid.

        SDK Hyperliquid menyediakan `query_order_by_cloid()`. Kalau kode
        produksi memanggilnya setelah timeout, cloid punya arti.
        """
        ex = make_exchange_with_state(CLEARINGHOUSE_STATE_WITH_POSITION, "BTC")
        self.assertTrue(
            hasattr(ex, "order_status_by_cloid"),
            "LiveExchange tidak punya cara mencari order by cloid -- "
            "idempotensi yang diklaim di docstring tidak diimplementasikan",
        )

    def test_cancel_never_called_in_production(self):
        """
        cancel(coin, oid) adalah satu-satunya cara membatalkan order
        tertentu. Kalau tidak pernah dipanggil, tidak ada jalur
        pembatalan per-order di seluruh sistem.
        """
        root = pathlib.Path(__file__).resolve().parent.parent
        callers = []
        for py in root.rglob("*.py"):
            rel = py.relative_to(root)
            if rel.parts[0] in ("tests", "build", "research", "data_store"):
                continue
            if rel.name == "client.py":
                continue  # definisinya sendiri
            text = py.read_text(encoding="utf-8", errors="replace")
            for i, line in enumerate(text.splitlines(), 1):
                s = line.strip()
                if "cancel_all" in s or ".cancel()" in s:
                    continue
                if ".cancel(" in s and "task.cancel" not in s:
                    callers.append("%s:%d  %s" % (rel, i, s[:70]))
        self.assertTrue(
            callers,
            "cancel(coin, oid) tidak dipanggil dari mana pun di produksi; "
            "tidak ada pembatalan per-order",
        )

    async def test_timeout_then_retry_doubles_position(self):
        """
        Urutan yang harus dicegah: timeout -> order mungkin masuk ->
        retry dengan cloid sama -> posisi tergandakan.

        Test ini memakai mesin sungguhan. Yang dikontrol hanya bursa:
        panggilan pertama melempar TimeoutError, panggilan kedua
        menerima. Kalau idempotensi ada, panggilan kedua harus dicegat
        sebagai "sudah masuk" dan tidak mengirim order lagi.
        """
        from trading.live.client import OrderOutcome

        sent = []

        class _TimeoutThenAccept:
            def __init__(self):
                self.n = 0

            def free_collateral(self):
                return 10000.0

            def total_notional(self):
                return 0.0

            def symbol_notional(self, coin):
                return 0.0

            def set_leverage(self, coin, leverage, is_cross=True):
                return {"ok": True}

            def place_limit_order(self, coin, is_buy, size, price,
                                  reduce_only=False, cloid=None):
                self.n += 1
                sent.append(cloid)
                if self.n == 1:
                    raise TimeoutError("respons hilang")
                return OrderOutcome(ok=True, filled_size=size,
                                    avg_price=price, order_id=7)

            def place_trigger_order(self, coin, is_buy, size, trigger_price,
                                    tpsl, reduce_only=True):
                return OrderOutcome(ok=True, filled_size=size, oid=2)

            def open_orders(self):
                return []

            def positions(self):
                return []

        engine = LiveEngine(
            gate=clean_gate(), exchange=_TimeoutThenAccept(), cfg=LiveConfig()
        )

        # Ukuran 0.001 BTC = 85 USDC, di bawah max_order_notional (100).
        # Kalau lebih besar, gerbang menolak karena limit -- dan test-nya
        # lulus karena alasan yang salah, bukan karena idempotensi.
        cloid = "tb-repro-retry-0001"
        await engine.submit_order(
            "BTC", "BTC/USDT:USDT", True, 0.001, 85000.0,
            stop_loss=84000.0, take_profit=87000.0, leverage=5, cloid=cloid,
        )
        await engine.submit_order(
            "BTC", "BTC/USDT:USDT", True, 0.001, 85000.0,
            stop_loss=84000.0, take_profit=87000.0, leverage=5, cloid=cloid,
        )

        self.assertLessEqual(
            len(sent), 1,
            "cloid %s dikirim %d kali; setelah timeout, retry harus "
            "dicek dulu lewat lookup by cloid sebelum mengirim lagi"
            % (cloid, len(sent)),
        )


if __name__ == "__main__":
    unittest.main()
