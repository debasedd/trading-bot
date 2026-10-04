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


# Bentuk kunci yang dibaca dari bursa. Ini fakta, bukan defect, jadi harus
# lulus.
#
# PERUBAHAN:(assert ini sebelumnya mengharapkan "BTC / USDC:USDC" --
# format yang dikarang engine.py sebelum item (a) diperbaiki. Sekarang
# semua kunci lewat `normalize_symbol()` dan hasilnya format internal
# repo: "BTC/USDT:USDT". Test ini sengaja diperbarui bersama perbaikannya;
# kalau format berubah lagi, test ini yang akan menangkap lebih dulu.


class TestRemoteKeyFormatFact(unittest.IsolatedAsyncioTestCase):

    async def test_remote_key_uses_internal_format(self):
        from trading.live.engine import LiveEngine
        from core.config import LiveConfig

        ex = make_exchange_with_state(CLEARINGHOUSE_STATE_WITH_POSITION, "BTC")
        engine = LiveEngine(gate=clean_gate(), exchange=ex, cfg=LiveConfig())
        remote = await asyncio.to_thread(engine._fetch_remote_positions)
        self.assertEqual(len(remote), 1)
        self.assertEqual(
            remote[0]["symbol"], "BTC/USDT:USDT",
            "kunci dari bursa harus format internal repo; kalau ini "
            "berubah, reconcile() dan test DEFECT-2 harus dievaluasi ulang",
        )
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


# ═══════════════════════════════════════════════════════════════════
# DEFECT 3 — SUDAH DIPERBAIKAN di item (b) Fase 1.
#
# Ketiga reproduksinya (SL fill, TP fill, order resting) sekarang hijau.
# Versinya yang lebih lengkap ada di:
#
#   tests/test_fill_reconciliation.py
#     TestHealthCheckAcceptsExchangeTriggeredClose
#       test_stop_loss_fill_does_not_engage_kill_switch
#       test_take_profit_fill_does_not_engage_kill_switch
#       test_real_mismatch_still_engages_kill_switch
#
#   tests/test_live_engine.py
#     test_resting_order_without_position_is_not_an_anomaly
#
# Test di sana juga membuktikan sisi yang harus TETAP: divergensi nyata
# tanpa fill penjelasan masih menyalakan kill switch. Test yang hanya
# memeriksa "tidak menyala" bisa lulus karena alasan yang salah --
# persis jebakan yang hampir menimpa saya di Fase 0.
# ═══════════════════════════════════════════════════════════════════

# DEFECT 4 — SUDAH DIPERBAIKAN di item (c) Fase 1.
# Testnya pindah ke tests/test_partial_fill.py sebagai test biasa.
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



# DEFECT 4 — SUDAH DIPERBAIKAN di item (c) Fase 1.
#
# Reproduksinya pindah ke tests/test_partial_fill.py sebagai test
# biasa (9 test). Test lama di sini dihapus karena isinya duplikat:
# keduanya menguji bahwa sisa order partial dibatalkan dan proteksi
# mengikuti filled_size.



# ═══════════════════════════════════════════════════════════════════
# DEFECT 5 -- SUDAH DIPERBAIKAN di item (d) Fase 1.
#
# Reproduksi aslinya menuntut `LiveExchange.order_status_by_cloid()` --
# dan itu tidak akan pernah ada: bursa tidak menyediakan lookup order
# by cloid. `orderStatus` hanya menerima `oid` numerik; cloid dalam
# bentuk hex maupun string menghasilkan HTTP 422. Jadi test lama itu
# menuntut sesuatu yang mustahil, dan tidak bisa jadi bukti apa pun.
#
# Yang SEBENARNYA bisa dijamin tanpa endpoint tersebut: cloid yang
# sama tidak boleh mengirim order kedua. Bursa tidak bisa menjawab
# "apakah order saya sudah masuk?", jadi satu-satunya opsi yang tidak
# menebak adalah tidak mengirim apa pun untuk identitas yang sama.
#
# Reproduksi lengkapnya pindah ke tests/test_order_idempotency.py
# (8 test) dan tests/test_cloid_wire_encoding.py (12 test).
# ═══════════════════════════════════════════════════════════════════
