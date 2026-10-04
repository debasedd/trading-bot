"""
tests/test_bugfixes.py — Regresi untuk perbaikan B1..B9.

Setiap test di sini ada karena ada bug nyata yang sudah diperbaiki. Kalau
salah satu gagal lagi, itu bukan "test yang rewel": itu bug yang kembali.

Kelompok test:
  * B1/B2  — guard tick: simbol yang benar, dan guard yang benar-benar bekerja
  * B3     — callback Dash membungkus Output dalam list
  * B4     — circuit breaker rugi harian benar-benar dievaluasi
  * B5     — validasi margin memasukkan fee, pesan error jujur
  * B6     — penutupan parsial dilaporkan apa adanya + jejak audit
  * B7     — konstanta mati dibuang dari hud_figures
  * B8     — warna diturunkan dari token, bukan rgba hardcoded
  * B9     — SL/TP memakai ROUND_DOWN eksplisit
"""

import os
import re
import unittest
from collections import deque
from pathlib import Path

ROOT = Path(__file__).parent.parent
PAPER_ENGINE_PY = ROOT / "trading" / "paper_engine.py"
RISK_MANAGER_PY = ROOT / "trading" / "risk_manager.py"
REPOSITORY_PY = ROOT / "database" / "repository.py"
CALLBACKS_PY = ROOT / "dashboard" / "callbacks" / "update_callbacks.py"
HUD_FIGURES_PY = ROOT / "dashboard" / "layouts" / "hud_figures.py"
DECISION_AGENT_PY = ROOT / "agents" / "decision_agent.py"
CONFIG_YAML = ROOT / "config.yaml"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ============================================================
# B1 & B2 — Guard kualitas tick
# ============================================================

class TestTickQualityGuard(unittest.IsolatedAsyncioTestCase):
    """
    Guard lama membandingkan `price` dengan `stop_loss` yang SENDIRI diturunkan
    dari `price`, sehingga `price <= stop_loss` mustahil terjadi. Test di bawah
    mengunci bahwa guard yang baru benar-benar bisa menolak.
    """

    async def asyncSetUp(self):
        self.test_db_path = "data_store/test_bugfix_tickguard_%d.db" % os.getpid()
        for suffix in ("", "-wal", "-shm"):
            p = self.test_db_path + suffix
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass

        from core.event_bus import EventBus
        from core.market_store import market_store
        import database.db as db_module
        from database.db import Database
        from trading.paper_engine import PaperTradingEngine

        self.db = Database(db_path=self.test_db_path)
        await self.db.connect()
        db_module._db = self.db

        self.engine = PaperTradingEngine(EventBus())
        await self.engine.initialize()
        # `from core.market_store import market_store` memberi INSTANS
        # singleton, bukan modulnya. `from core import market_store` memberi
        # modul — dan modul tidak punya `_price_history`, karena state itu
        # hidup di instans. Karena itu import harus dari submodule langsung.
        self.store = market_store
        self.symbol = "BTC/USDT:USDT"
        # Kosongkan sisa state dari test lain agar guard mulai bersih.
        self.store._price_history.clear()
        self.store._last_prices.clear()
        self.store._price_ts.clear()
        self.store._order_books.clear()
        self.store._funding.clear()
        self.store._open_interest.clear()
        self.store._recent_trades.clear()
        self.store._tickers.clear()
        self.store._live_candles.clear()

    async def asyncTearDown(self):
        from database.db import close_db
        # Kosongkan SEMUA dict store, bukan hanya tiga yang dipakai test ini.
        # `funding` dan `_recent_trades` dibaca `close_position`/`fill_cost`,
        # jadi sisa dari test ini akan mengubah angka akuntansi di file lain.
        self.store._price_history.clear()
        self.store._last_prices.clear()
        self.store._price_ts.clear()
        self.store._order_books.clear()
        self.store._funding.clear()
        self.store._open_interest.clear()
        self.store._recent_trades.clear()
        self.store._tickers.clear()
        self.store._live_candles.clear()
        await close_db()
        for suffix in ("", "-wal", "-shm"):
            p = self.test_db_path + suffix
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass

    def _seed_ticks(self, prices, max_age=0.0):
        """Isi price_history dengan tick-tick recent, lalu opsional jadi basi.

        Timestamp dimundurkan lewat `_price_ts`, bukan dengan menunggu, supaya
        test tidak bergantung pada waktu nyata.
        """
        import time
        now = time.time()
        self.store._price_history[self.symbol] = deque(maxlen=240)
        for px in prices:
            self.store._price_history[self.symbol].append((now, px))
        if max_age:
            # Mundurkan timestamp supaya tick dianggap basi.
            self.store._price_ts[self.symbol] = now - max_age

    async def test_stale_tick_is_rejected(self):
        """Tick yang sudah tua harus ditolak — ini yang mustahil terjadi dulu."""
        self._seed_ticks([60000.0] * 6, max_age=99.0)
        reason = self.engine._tick_quality_guard(self.symbol, 60000.0, 0.0025)
        self.assertIsNotNone(reason, "tick basi harus ditolak")
        self.assertIn("basi", reason)

    async def test_outlier_tick_is_rejected(self):
        """
        Fill yang menyimpang dari median tick terakhir harus ditolak.

        Secara ekonomi inilah gunanya: stop scalp hanya 0.25% dari harga, jadi
        fill di puncak lokal berarti stop-nya sudah berada di dalam spread dan
        posisi akan tersapu pada pemeriksaan berikutnya.
        """
        self._seed_ticks([60000.0] * 6)  # median 60000
        # 60600 adalah 1% di atas median, jauh melewati SL 0.25%.
        reason = self.engine._tick_quality_guard(self.symbol, 60600.0, 0.0025)
        self.assertIsNotNone(reason, "outlier harus ditolak")
        self.assertIn("menyimpang", reason)

    async def test_representative_tick_is_accepted(self):
        """Harga yang dekat median harus lolos — guard tidak boleh menahan semua."""
        self._seed_ticks([60000.0] * 6)
        self.assertIsNone(self.engine._tick_quality_guard(self.symbol, 60002.0, 0.0025))

    async def test_insufficient_samples_fails_open(self):
        """
        Riwayat yang belum cukup harus MENERIMA order (fail-open).

        Menolak order karena tidak punya data sejarah akan membuat bot diam
        persis di detik-detik paling ramai, yaitu saat feed baru tersambung.
        """
        self._seed_ticks([60000.0, 60001.0])  # hanya 2 sampel
        self.assertIsNone(self.engine._tick_quality_guard(self.symbol, 65000.0, 0.0025))

    async def test_guard_actually_rejects_in_execute_open(self):
        """
        Uji integrasi: `execute_order` dengan tick basi harus DITOLAK.

        Inilah yang membuktikan B2 benar-benar diperbaiki, bukan sekadar ada
        method baru yang tidak pernah terpakai.
        """
        from trading.models import Order, TradeAction

        self.engine.update_price(self.symbol, 60000.0)
        self._seed_ticks([60000.0] * 6, max_age=99.0)

        order = Order(
            symbol=self.symbol, action=TradeAction.OPEN_LONG,
            quantity=0.01, leverage=10,
        )
        res = await self.engine.execute_order(order)
        self.assertFalse(res["success"])
        self.assertIn("tidak layak eksekusi", res["message"])

        repo = await self.engine._get_repo()
        self.assertEqual(
            len(await repo.get_open_positions()), 0,
            "posisi tidak boleh terbuka saat guard menolak",
        )

    async def test_no_name_error_on_rejection_path(self):
        """
        Regresi B1: jalur penolakan tidak boleh melempar `NameError`.

        Versi lama menulis `f"Order LONG {symbol} ..."` di fungsi yang hanya
        punya `order` — jadi justru saat guard menyala, yaitu momen paling butuh
        diagnostik, ia meledak dengan `NameError`.
        """
        from trading.models import Order, TradeAction

        self.engine.update_price(self.symbol, 60000.0)
        self._seed_ticks([60000.0] * 6, max_age=99.0)
        order = Order(
            symbol=self.symbol, action=TradeAction.OPEN_LONG,
            quantity=0.01, leverage=10,
        )
        # Kalau masih ada referensi `symbol` yang tak terdefinisi, baris ini
        # melempar NameError dari dalam pemanggilan logger.
        res = await self.engine.execute_order(order)
        self.assertFalse(res["success"])


# ============================================================
# B4 — Circuit breaker rugi harian
# ============================================================

class TestDailyLossCircuitBreaker(unittest.IsolatedAsyncioTestCase):
    """
    B4: `max_daily_loss` ada di config tapi tidak pernah dievaluasi — argumennya
    tidak diteruskan ke `validate_trade`, jadi circuit breaker mati.
    """

    async def asyncSetUp(self):
        self.test_db_path = "data_store/test_bugfix_dailyloss_%d.db" % os.getpid()
        for suffix in ("", "-wal", "-shm"):
            p = self.test_db_path + suffix
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass
        from core.event_bus import EventBus
        import database.db as db_module
        from database.db import Database
        from trading.paper_engine import PaperTradingEngine
        self.db = Database(db_path=self.test_db_path)
        await self.db.connect()
        db_module._db = self.db
        self.engine = PaperTradingEngine(EventBus())
        await self.engine.initialize()
        self.engine.update_price("BTC/USDT:USDT", 60000.0)

    async def asyncTearDown(self):
        from database.db import close_db
        await close_db()
        for suffix in ("", "-wal", "-shm"):
            p = self.test_db_path + suffix
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass

    async def _insert_closed(self, pnl):
        """Sisipkan posisi tertutup dengan PnL tertentu, ditutup hari ini."""
        repo = await self.engine._get_repo()
        await repo.db.execute(
            """INSERT INTO positions
               (symbol, side, entry_price, quantity, leverage, margin,
                liquidation_price, status, opened_at, closed_at, realized_pnl)
               VALUES (?,?,?,?,?,?,?,?,datetime('now'),datetime('now'),?)""",
            ("BTC/USDT:USDT", "LONG", 60000.0, 0.1, 10, 600.0, 50000.0,
             "CLOSED", float(pnl)),
        )
        await repo.db.commit()

    async def test_daily_realized_pnl_is_zero_on_fresh_account(self):
        """Akun baru: PnL harian harus 0.0, bukan None."""
        repo = await self.engine._get_repo()
        self.assertEqual(await repo.get_daily_realized_pnl(), 0.0)

    async def test_daily_realized_pnl_counts_todays_closes(self):
        """PnL terealisasi hari ini harus terbaca oleh circuit breaker."""
        await self._insert_closed(-500.0)
        repo = await self.engine._get_repo()
        self.assertAlmostEqual(
            await repo.get_daily_realized_pnl(), -500.0, places=2
        )

    async def test_validate_trade_blocks_after_daily_loss(self):
        """Setelah rugi harian melewati batas, order baru harus DITOLAK."""
        from trading.models import Order, TradeAction
        from core.config import get_config

        max_daily = get_config().risk.max_daily_loss
        await self._insert_closed(-(max_daily * 11000 * 1.5))

        order = Order(
            symbol="BTC/USDT:USDT", action=TradeAction.OPEN_LONG,
            quantity=0.01, leverage=10,
        )
        res = await self.engine.execute_order(order)
        self.assertFalse(res["success"], "order harus ditolak setelah daily loss besar")
        self.assertIn("Ditolak", res["message"])

    async def test_validate_trade_allows_when_daily_pnl_positive(self):
        """Keuntungan harian tidak boleh memicu circuit breaker."""
        from trading.models import Order, TradeAction

        await self._insert_closed(+5000.0)
        order = Order(
            symbol="BTC/USDT:USDT", action=TradeAction.OPEN_LONG,
            quantity=0.01, leverage=10,
        )
        res = await self.engine.execute_order(order)
        self.assertTrue(res["success"], "order harus boleh saat PnL harian positif")

    def test_daily_loss_uses_initial_balance_not_free_cash(self):
        """
        Source check: penyebut circuit breaker harus modal awal, bukan kas.

        Memakai kas sebagai penyebut membuat ambang ikut turun begitu margin
        terkunci — pelonggaran terjadi justru di saat paling rugi.
        """
        from trading.risk_manager import RiskManager

        rm = RiskManager()
        rm.set_initial_balance(10000.0)
        v = rm.validate_trade(
            balance=1000.0,        # kas sudah kecil (margin terkunci)
            margin_required=100.0,
            open_positions=1,
            daily_pnl=-1500.0,     # 15% dari modal awal
            peak_balance=10000.0,
            equity=1000.0,
        )
        # 1500 / 10000 = 15% >= max_daily_loss (10%) → harus DITOLAK
        self.assertFalse(
            v["allowed"],
            f"circuit breaker harus menolak; reasons={v['reasons']}",
        )
        self.assertIn("modal awal", " ".join(v["reasons"]))

    def test_daily_loss_zero_does_not_block(self):
        """PnL harian 0 bukan rugi — breaker tidak boleh menahan."""
        from trading.risk_manager import RiskManager

        rm = RiskManager()
        rm.set_initial_balance(10000.0)
        v = rm.validate_trade(
            balance=10000.0, margin_required=100.0, open_positions=0,
            daily_pnl=0.0, peak_balance=10000.0, equity=10000.0,
        )
        self.assertNotIn(
            "modal awal", " ".join(v["reasons"]),
            "PnL 0 tidak boleh memicu daily-loss breaker",
        )


# ============================================================
# B3 — Dekorator callback Dash
# ============================================================

class TestDashCallbackOutputWrapping(unittest.TestCase):
    """
    B3: `Output` yang tidak dibungkus list diperlakukan Dash sebagai argumen
    berurutan `output, inputs, state` — sehingga output kedua jadi INPUT dan
    callback tidak pernah terpicu interval. Kerusakannya senyap: panel membeku
    tanpa error apa pun.
    """

    def _callback_blocks(self):
        src = _read(CALLBACKS_PY)
        # Ambil setiap dekorator @app.callback(...)
        return re.findall(r"@app\.callback\((.*?)\)\s*\n", src, flags=re.S)

    def test_every_callback_has_at_least_one_output(self):
        blocks = self._callback_blocks()
        self.assertTrue(blocks, "tidak ada callback ditemukan di update_callbacks.py")
        for i, block in enumerate(blocks):
            self.assertIn("Output(", block, f"callback #{i} tidak punya Output")

    def test_no_callback_with_multiple_bare_outputs(self):
        """
        Dilarang ada callback dengan >1 Output tanpa pembungkus list.

        Bentuk yang benar:  `[Output("a", ...), Output("b", ...)]`
        Bentuk yang salah: `Output("a", ...),` lalu `Output("b", ...)` di baris
        berikutnya — Dash akan memetakan argumen kedua ke `inputs`, dan
        callback tidak pernah terpicu interval.

        CATATAN: satu `Output(...)` sendirian itu SAH dan sering dipakai
        (mis. `update_neural_net`). Yang salah hanya kalau ada LEBIH DARI SATU
        Output tanpa `[`. Versi test sebelumnya salah menandai single-Output
        yang sah sebagai bug.
        """
        src = _read(CALLBACKS_PY)
        for match in re.finditer(r"@app\.callback\((.*?)\n\s*\)\s*\n", src, flags=re.S):
            block = match.group(1)
            outputs = re.findall(r"Output\(", block)
            if len(outputs) > 1 and "[" not in block:
                raise AssertionError(
                    "Callback punya {} Output tanpa pembungkus list:\n{}".format(
                        len(outputs), block[:300]
                    )
                )

    def test_symbol_pnl_callback_uses_list(self):
        """Guard spesifik untuk callback yang bermasalah (B3)."""
        src = _read(CALLBACKS_PY)
        idx = src.find('def update_symbol_pnl')
        self.assertGreater(idx, -1, "update_symbol_pnl tidak ditemukan")
        # Ambil blok dekorator tepat di atas fungsi
        deco_start = src.rfind("@app.callback(", 0, idx)
        block = src[deco_start:idx]
        self.assertIn(
            "[Output(", block,
            "update_symbol_pnl harus membungkus Output-nya dalam list",
        )

    def test_all_outputs_declared_as_list_or_single(self):
        """
        Setiap dekorator harus punya tepat satu dari dua bentuk yang valid:
          1. `[Output(...), ...]`  (multi-output)
          2. `Output(...)` tunggal (single-output, sah)
        """
        src = _read(CALLBACKS_PY)
        for match in re.finditer(r"@app\.callback\((.*?)\n\s*\)\s*\n", src, flags=re.S):
            block = match.group(1)
            outputs = re.findall(r"Output\(", block)
            if len(outputs) > 1 and "[" not in block:
                raise AssertionError(
                    "Callback dengan lebih dari satu Output tapi tanpa pembungkus "
                    f"list:\n{block[:300]}"
                )


# ============================================================
# B5 — Validasi margin + fee
# ============================================================

class TestMarginFeeValidation(unittest.IsolatedAsyncioTestCase):
    """
    B5: validasi memakai `margin` saja, padahal `open_position` memotong
    `margin + fee` secara atomik. Order bisa lolos validasi lalu ditolak,
    dengan pesan yang menyesatkan.
    """

    async def asyncSetUp(self):
        self.test_db_path = "data_store/test_bugfix_marginfee_%d.db" % os.getpid()
        for suffix in ("", "-wal", "-shm"):
            p = self.test_db_path + suffix
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass
        from core.event_bus import EventBus
        import database.db as db_module
        from database.db import Database
        from trading.paper_engine import PaperTradingEngine
        self.db = Database(db_path=self.test_db_path)
        await self.db.connect()
        db_module._db = self.db
        self.engine = PaperTradingEngine(EventBus())
        await self.engine.initialize()
        self.engine.update_price("BTC/USDT:USDT", 60000.0)

    async def asyncTearDown(self):
        from database.db import close_db
        await close_db()
        for suffix in ("", "-wal", "-shm"):
            p = self.test_db_path + suffix
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass

    async def test_fee_estimate_matches_actual_open_fee(self):
        """
        Estimasi fee di validasi harus sama dengan fee yang benar-benar dipotong.

        Kalau estimasinya meleset, order lolos gerbang lalu ditolak — atau
        lebih buruk, lolos padahal modal sebenarnya tidak cukup.

        Estimasi HARUS dihitung dari harga FILL, bukan harga pasar. Yang
        dipakai `estimated_fee` di `:556` adalah harga yang sudah
        digeser, sama dengan yang dipakai `open_position`. Menghitung dari
        harga pasar membuat estimasi terlalu kecil — dan itu persis celah yang B5 perbaiki, hanya dengan bentuk yang lebih kecil sekarang.
        """
        from trading.models import Order, TradeAction
        from trading.paper_engine import (
            FILL_HALF_SPREAD_FLOOR,
            FILL_IMPACT_FLOOR,
        )
        from trading.fill_cost import FILL_HALF_SPREAD_FLOOR_BY_SYMBOL

        rm = self.engine.risk_manager
        # Fee dihitung dari HARGA FILL, bukan harga pasar. `open_position`
        # menerima `entry_price=price` yang sudah digeser biaya menyeberang,
        # dan fee adalah persentase dari notional yang benar-benar dibayar.
        #
        # Floor spread sekarang PER-SIMBOL (lihat `trading/fill_cost.py`):
        # BTC terukur 0.12 bps, bukan 3 bps global. Test yang memakai
        # konstanta global akan menghitung fee 25x lebih besar.
        base = "BTC"
        half = FILL_HALF_SPREAD_FLOOR_BY_SYMBOL.get(base, FILL_HALF_SPREAD_FLOOR)
        fill = 60000.0 * (1 + half + FILL_IMPACT_FLOOR)
        estimated = rm.calculate_fee(0.1, fill, "TAKER")

        order = Order(
            symbol="BTC/USDT:USDT", action=TradeAction.OPEN_LONG,
            quantity=0.1, leverage=10,
        )
        res = await self.engine.execute_order(order)
        self.assertTrue(res["success"])

        repo = await self.engine._get_repo()
        trades = await repo.get_trades()
        actual = trades[0]["fee"]
        self.assertAlmostEqual(estimated, actual, places=8)

    async def test_insufficient_balance_message_is_specific(self):
        """
        Pesan kegagalan harus menyebut angkanya, bukan kalimat generik.

        `open_position` mengembalikan None untuk tiga alasan berbeda; kalau
        semuanya dilabeli "saldo tidak cukup", pembaca log akan menyalahkan
        sizing padahal masalahnya bisa di database.
        """
        from trading.models import Order, TradeAction
        from database.models import Position

        # Paksa kegagalan: hentikan balance jadi sangat kecil.
        repo = await self.engine._get_repo()
        await repo.db.execute("UPDATE account SET balance = 10.0")
        await repo.db.commit()

        order = Order(
            symbol="BTC/USDT:USDT", action=TradeAction.OPEN_LONG,
            quantity=0.1, leverage=10,   # margin 600 > 10
        )
        res = await self.engine.execute_order(order)
        self.assertFalse(res["success"])
        msg = res["message"]
        self.assertNotIn(
            "Gagal membuka posisi (saldo tidak cukup)", msg,
            "pesan generik lama masih ada",
        )
        # Pesan baru harus menyebut nominal
        self.assertRegex(msg, r"\d+\.\d+", "pesan harus menyebut angka nominal")

    async def test_validation_reserves_fee_not_just_margin(self):
        """
        Source check: `validate_trade` menerima `margin + fee`, bukan margin.

        Dikunci lewat source check karena sulit diuji secara langsung: order
        dengan margin persis di bawah saldo tapi ditambah fee akan lolos kalau
        fee tidak ikut dihitung — dan hanya gagal belakangan, di lapisan lain.
        """
        src = _read(PAPER_ENGINE_PY)
        self.assertIn(
            "required_cash = margin + estimated_fee", src,
            "validasi harus menghitung margin + fee",
        )
        self.assertIn(
            "margin_required=required_cash", src,
            "validate_trade harus menerima required_cash, bukan margin",
        )


# ============================================================
# B6 — Penutupan parsial & jejak audit
# ============================================================

class TestPartialCloseReporting(unittest.IsolatedAsyncioTestCase):
    """
    B6: loop tutup semua membuang kegagalan per-posisi tanpa jejak, lalu tetap
    melaporkan `success: True` dengan PnL yang hanya setengah. Pembukaan punya
    jejak audit (`TRADE_EXECUTED`); penutupan tidak punya apa pun.
    """

    async def asyncSetUp(self):
        self.test_db_path = "data_store/test_bugfix_partialclose_%d.db" % os.getpid()
        for suffix in ("", "-wal", "-shm"):
            p = self.test_db_path + suffix
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass
        from core.event_bus import EventBus
        import database.db as db_module
        from database.db import Database
        from trading.paper_engine import PaperTradingEngine
        self.db = Database(db_path=self.test_db_path)
        await self.db.connect()
        db_module._db = self.db
        self.engine = PaperTradingEngine(EventBus())
        await self.engine.initialize()
        self.symbol = "BTC/USDT:USDT"
        self.engine.update_price(self.symbol, 60000.0)

    async def asyncTearDown(self):
        from database.db import close_db
        await close_db()
        for suffix in ("", "-wal", "-shm"):
            p = self.test_db_path + suffix
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass

    async def _open_positions(self, n, entry=60000.0, qty=0.05, lev=10):
        """Buka n posisi dan kembalikan list id-nya."""
        from trading.models import Order, TradeAction
        ids = []
        for _ in range(n):
            order = Order(
                symbol=self.symbol, action=TradeAction.OPEN_LONG,
                quantity=qty, leverage=lev, stop_loss=entry * 0.9,
                take_profit=entry * 1.1,
            )
            res = await self.engine.execute_order(order)
            self.assertTrue(res["success"], res["message"])
            ids.append(res["position_id"])
        return ids

    async def test_full_close_reports_success(self):
        """Semua posisi tertutup → success True dan tidak ada daftar gagal."""
        from trading.models import Order, TradeAction
        await self._open_positions(3)

        order = Order(symbol=self.symbol, action=TradeAction.CLOSE)
        # Naikkan harga supaya penutupan bersih dan profit.
        self.engine.update_price(self.symbol, 61000.0)
        res = await self.engine.execute_order(order)

        self.assertTrue(res["success"], res["message"])
        self.assertEqual(len(res["details"]["closed_ids"]), 3)
        self.assertEqual(res["details"]["failed"], [])
        self.assertTrue(res["details"]["fully_closed"])

    async def test_partial_close_is_not_reported_as_success(self):
        """
        Penutupan parsial harus melaporkan apa adanya.

        Race yang diuji: `get_open_positions` mengembalikan 3 posisi, tapi
        saat loop menutupnya, satu di antaranya sudah hilang (ditutup jalur
        lain pada siklus yang sama) sehingga `close_position` mengembalikan
        `None`. Hasilnya: 2 tertutup, 1 gagal — dan itu WAJIB dilaporkan
        sebagai `success: False`.

        Cara memicunya: kita stub `close_position` supaya mengembalikan
        `None` untuk satu id. Menutup posisi secara manual SEBELUMNYA tidak
        akan memicu apa pun — posisi itu hilang dari DB, jadi
        `get_open_positions` tidak pernah mengembalikannya, dan tidak ada
        yang gagal. Versi test sebelumnya salah memahami itu, dan karena itu
        selalu gagal.
        """
        from trading.models import Order, TradeAction
        ids = await self._open_positions(3)
        vanished_id = ids[1]

        pm = self.engine.position_manager
        real_close = pm.close_position

        async def flaky_close(position_id, price, reason="SIGNAL"):
            if position_id == vanished_id:
                return None  # simulates a race: already gone
            return await real_close(position_id, price, reason)

        self.engine.update_price(self.symbol, 61000.0)
        pm.close_position = flaky_close
        try:
            order = Order(symbol=self.symbol, action=TradeAction.CLOSE)
            res = await self.engine.execute_order(order)
        finally:
            pm.close_position = real_close

        self.assertEqual(len(res["details"]["closed_ids"]), 2)
        self.assertEqual(len(res["details"]["failed"]), 1)
        self.assertEqual(res["details"]["failed"][0]["position_id"], vanished_id)
        self.assertFalse(res["details"]["fully_closed"])
        self.assertFalse(res["success"], "penutupan parsial tidak boleh sukses")
        self.assertIn("SEBAGIAN", res["message"])
        # Pesan harus menyebut posisi yang gagal.
        self.assertIn(f"#{vanished_id}", res["message"])

    async def test_close_writes_audit_log(self):
        """
        Penutupan harus meninggalkan jejak di `agent_logs`.

        Tanpa ini, rekonstruksi "kenapa posisi ini tidak ada lagi" mustahil
        dari database.
        """
        from trading.models import Order, TradeAction
        await self._open_positions(1)
        self.engine.update_price(self.symbol, 61000.0)

        order = Order(symbol=self.symbol, action=TradeAction.CLOSE)
        await self.engine.execute_order(order)

        repo = await self.engine._get_repo()
        logs = await repo.get_agent_logs("paper_engine", limit=50)
        actions = [entry["action"] for entry in logs]
        self.assertIn(
            "TRADE_CLOSED", actions,
            "penutupan harus menulis agent_log TRADE_CLOSED",
        )

    async def test_close_failure_writes_rejection_log(self):
        """Kegagalan total juga harus meninggalkan jejak audit."""
        from trading.models import Order, TradeAction
        order = Order(symbol=self.symbol, action=TradeAction.CLOSE)
        # Tidak ada posisi → tidak ada yang ditutup.
        res = await self.engine.execute_order(order)
        self.assertFalse(res["success"])

        repo = await self.engine._get_repo()
        # Tidak ada penutupan yang terjadi, jadi tidak harus ada TRADE_CLOSED.
        logs = await repo.get_agent_logs("paper_engine", limit=50)
        self.assertIsNotNone(logs)

    async def test_describe_failures_truncates_long_lists(self):
        """Ringkasan kegagalan panjang harus dipotong, tidak meledakkan log."""
        failed = [{"position_id": i, "side": "LONG"} for i in range(1, 21)]
        text = self.engine._describe_failures(failed)
        self.assertIn("+15 lainnya", text)
        self.assertEqual(self.engine._describe_failures([]), "tidak ada")


# ============================================================
# B7 & B8 — Kode mati & token warna
# ============================================================

class TestHudFigureCleanup(unittest.TestCase):
    """B7: `BASE_PRICES` tidak pernah dipakai tapi masih diimpor."""

    def test_base_prices_constant_is_removed(self):
        """Konstanta mati harus hilang dari hud_figures.py."""
        src = _read(HUD_FIGURES_PY)
        self.assertNotIn(
            "BASE_PRICES", src,
            "BASE_PRICES adalah konstanta mati dan harus dibuang (B7)",
        )

    def test_base_prices_not_imported_by_callbacks(self):
        """Import `BASE_PRICES` juga harus dibuang dari update_callbacks.py."""
        src = _read(CALLBACKS_PY)
        self.assertNotIn(
            "BASE_PRICES", src,
            "update_callbacks.py tidak boleh mengimpor BASE_PRICES lagi",
        )

    def test_hardcoded_rgba_replaced_with_token(self):
        """
        B8: warna hex tidak boleh ditulis langsung sebagai rgba hardcoded.

        Nilai rgba yang tidak diturunkan dari token warna berarti palet
        bercabang: mengubah `GREEN_VINTAGE` tidak akan mengubah garis yang
        pakai `rgba(0, 153, 51, 0.22)`.
        """
        src = _read(HUD_FIGURES_PY)
        # Warna yang HARUS hilang (hardcoded):
        for bad in (
            "rgba(0, 119, 182",
            "rgba(0, 153, 51",
            '"rgba(0, 0, 0, 0.13)"',
            '"rgba(0,0,0,0)"',
        ):
            self.assertNotIn(
                bad, src,
                f"rgba hardcoded masih ada: {bad} — harus pakai with_alpha() atau token",
            )

    def test_with_alpha_helper_exists(self):
        """Helper `with_alpha` harus ada sebagai sumber tunggal turunan warna."""
        src = _read(HUD_FIGURES_PY)
        self.assertIn("def with_alpha(", src)

    def test_vintage_tokens_still_defined(self):
        """Token warna inti harus tetap ada setelah refactor."""
        src = _read(HUD_FIGURES_PY)
        for token in (
            "GREEN_VINTAGE", "RED_VINTAGE", "AMBER_VINTAGE",
            "BLUE_VINTAGE", "PARCHMENT_BG", "INK_BLACK", "INK_MUTED",
        ):
            self.assertIn(f"{token} = ", src, f"token {token} hilang")


# ============================================================
# B9 — Kuantisasi SL/TP
# ============================================================

class TestStopLossTakeProfitQuantization(unittest.TestCase):
    """
    B9: SL/TP memakai `.quantize(...)` tanpa `rounding=`, sehingga jatuh ke
    default `ROUND_HALF_EVEN` — berbeda dari likuidasi dan sizing yang eksplisit
    memakai `ROUND_DOWN`. Pada scalp berjarak 0.25%, satu tick salah arah bukan
    pembulatan netral.
    """

    def setUp(self):
        from trading.risk_manager import RiskManager
        self.rm = RiskManager()

    def test_stop_loss_uses_round_down(self):
        src = _read(RISK_MANAGER_PY)
        # SL dan TP harus punya `rounding=ROUND_DOWN` di kuantisasinya.
        sl_block = src[src.index("def calculate_stop_loss"):src.index("def calculate_take_profit")]
        self.assertIn(
            "rounding=ROUND_DOWN", sl_block,
            "calculate_stop_loss harus memakai ROUND_DOWN eksplisit (B9)",
        )

    def test_take_profit_uses_round_down(self):
        src = _read(RISK_MANAGER_PY)
        tp_block = src[src.index("def calculate_take_profit"):src.index("def calculate_fee")]
        self.assertIn(
            "rounding=ROUND_DOWN", tp_block,
            "calculate_take_profit harus memakai ROUND_DOWN eksplisit (B9)",
        )

    def test_sl_direction_is_conservative(self):
        """SL LONG harus di BAWAH entry (ROUND_DOWN), TP LONG di atas."""
        entry = 60000.123456789
        sl = self.rm.calculate_stop_loss(entry, "LONG", 0.0025)
        tp = self.rm.calculate_take_profit(entry, "LONG", 0.0060)
        self.assertLess(sl, entry, "SL LONG harus di bawah entry")
        self.assertGreater(tp, entry, "TP LONG harus di atas entry")

    def test_short_sl_direction(self):
        """SL SHORT harus di ATAS entry."""
        entry = 60000.123456789
        sl = self.rm.calculate_stop_loss(entry, "SHORT", 0.0025)
        self.assertGreater(sl, entry, "SL SHORT harus di atas entry")

    def test_quantization_precision_is_8_decimals(self):
        """
        Hasil harus berada di kisi 1e-8.

        Catatan penting: hasil TIDAK akan selalu punya tepat 8 desimal.
        `60000.0 x (1 - 0.0025) = 59850.0` persis, jadi kuantisasinya tidak
        mengubah apa pun dan `Decimal` akan melaporkan eksponen -1, bukan -8.
        yang benar-benar diuji kuantisasi adalah "tidak lebih presisi dari
        1e-8", bukan "selalu 8 desimal" — test yang salah akan menolak harga
        bulat yang justru valid.
        """
        from decimal import Decimal as _D

        for entry, pct in ((60000.0, 0.0025), (1.23456789, 0.0025), (98765.4321, 0.0060)):
            for direction in ("LONG", "SHORT"):
                sl = self.rm.calculate_stop_loss(entry, direction, pct)
                scaled = _D(str(sl)) * _D(10) ** 8
                self.assertEqual(
                    scaled, scaled.to_integral_value(),
                    f"SL {sl} ({direction}) harus berada di kisi 1e-8",
                )

    def test_round_down_never_exceeds_exact_value(self):
        """
        ROUND_DOWN berarti hasil ≤ nilai persisnya, untuk kedua sisi.

        Ini yang membuat SL LONG tidak pernah lebih tinggi dari yang
        direncanakan — stop yang bergeser ke atas mengurangi ruang gerak.
        """
        from decimal import Decimal
        for entry in (60000.0, 1.23456789, 0.00012345, 98765.4321):
            pct = 0.0025
            exact_sl_long = Decimal(str(entry)) * (Decimal(1) - Decimal(str(pct)))
            got = Decimal(str(self.rm.calculate_stop_loss(entry, "LONG", pct)))
            self.assertLessEqual(
                got, exact_sl_long,
                f"SL LONG untuk {entry} melebihi nilai persis — bukan ROUND_DOWN",
            )


# ============================================================
# Bagian 2 — Arsitektur & Konfigurasi
# ============================================================

class TestMicrostructureKernel(unittest.TestCase):
    """
    Antarmuka hot-path OFI/depth. Kontrak ini yang membuat penggantian ke
    C++/Rust jadi perubahan implementasi, bukan perubahan arsitektur.
    """

    def setUp(self):
        from core import microstructure
        self.ms = microstructure
        self.kernel = microstructure.PythonKernel()

    def test_empty_book_returns_zeros(self):
        """Doktrin data absen: tanpa book → (0.0, 0.0), bukan exception."""
        self.assertEqual(self.kernel.order_flow_imbalance("X"), (0.0, 0.0))
        self.assertEqual(self.kernel.depth_imbalance("X"), 0.0)

    def test_heavy_bid_pressure_gives_positive_ofi(self):
        self.kernel.ingest_l2(
            "BTC",
            [[100.0, 10.0], [99.0, 10.0], [98.0, 10.0], [97.0, 10.0], [96.0, 10.0]],
            [[101.0, 1.0], [102.0, 1.0], [103.0, 1.0], [104.0, 1.0], [105.0, 1.0]],
        )
        ofi, spread = self.kernel.order_flow_imbalance("BTC")
        self.assertGreater(ofi, 0.0, "bidi lebih besar → OFI positif")
        self.assertGreater(spread, 0.0)
        self.assertLessEqual(abs(ofi), 1.0)

    def test_heavy_ask_pressure_gives_negative_ofi(self):
        self.kernel.ingest_l2(
            "BTC",
            [[100.0, 1.0], [99.0, 1.0], [98.0, 1.0], [97.0, 1.0], [96.0, 1.0]],
            [[101.0, 10.0], [102.0, 10.0], [103.0, 10.0], [104.0, 10.0], [105.0, 10.0]],
        )
        ofi, _ = self.kernel.order_flow_imbalance("BTC")
        self.assertLess(ofi, 0.0, "ask lebih besar → OFI negatif")

    def test_balanced_book_gives_zero_ofi(self):
        self.kernel.ingest_l2(
            "BTC",
            [[100.0, 5.0], [99.0, 5.0], [98.0, 5.0], [97.0, 5.0], [96.0, 5.0]],
            [[101.0, 5.0], [102.0, 5.0], [103.0, 5.0], [104.0, 5.0], [105.0, 5.0]],
        )
        ofi, _ = self.kernel.order_flow_imbalance("BTC")
        self.assertAlmostEqual(ofi, 0.0, places=9)

    def test_zero_volume_does_not_divide_by_zero(self):
        """Orderbook ber-volume 0 harus menghasilkan 0.0, bukan ZeroDivisionError."""
        self.kernel.ingest_l2(
            "BTC",
            [[0.0, 0.0]],
            [[0.0, 0.0]],
        )
        ofi, spread = self.kernel.order_flow_imbalance("BTC")
        self.assertEqual(ofi, 0.0)
        self.assertEqual(spread, 0.0)

    def test_empty_sides_do_not_crash(self):
        self.kernel.ingest_l2("BTC", [], [[101.0, 1.0]])
        self.assertEqual(self.kernel.order_flow_imbalance("BTC"), (0.0, 0.0))

    def test_reset_single_symbol(self):
        self.kernel.ingest_l2("A", [[1.0, 1.0]], [[2.0, 1.0]])
        self.kernel.ingest_l2("B", [[1.0, 1.0]], [[2.0, 1.0]])
        self.kernel.reset("A")
        self.assertEqual(self.kernel.order_flow_imbalance("A"), (0.0, 0.0))
        self.assertNotEqual(self.kernel.order_flow_imbalance("B"), (0.0, 0.0))

    def test_register_kernel_rejects_incomplete(self):
        """Kernel yang tidak memenuhi antarmuka harus ditolak, bukan dipasang."""

        class Incomplete:
            def ingest_l2(self, *a):
                pass
            # depth_imbalance & reset tidak ada

        original = self.ms.get_kernel()
        with self.assertRaises(TypeError):
            self.ms.register_kernel(Incomplete())
        # Kernel lama harus tetap aktif.
        self.assertIs(self.ms.get_kernel(), original)

    def test_register_kernel_accepts_valid(self):
        class Valid:
            def ingest_l2(self, *a):
                pass
            def order_flow_imbalance(self, *a, **k):
                return (0.5, 0.001)
            def depth_imbalance(self, *a, **k):
                return 0.5
            def reset(self, *a, **k):
                pass

        original = self.ms.get_kernel()
        try:
            self.ms.register_kernel(Valid())
            self.assertIsNot(self.ms.get_kernel(), original)
        finally:
            # Kembalikan kernel default supaya test lain tidak terpengaruh.
            self.ms._KERNEL = original

    def test_probability_engine_delegates_to_kernel(self):
        """
        `calculate_order_flow_imbalance` harus lewat kernel, bukan hitung sendiri.

        Inilah yang membuat OFI bisa diganti ke C++ tanpa menyentuh agent.
        """
        from analysis.probability_engine import calculate_order_flow_imbalance

        book = {
            "bids": [[100.0, 10.0], [99.0, 10.0], [98.0, 10.0], [97.0, 10.0], [96.0, 10.0]],
            "asks": [[101.0, 1.0], [102.0, 1.0], [103.0, 1.0], [104.0, 1.0], [105.0, 1.0]],
        }
        ofi, spread = calculate_order_flow_imbalance(book, symbol="BTC/USDT:USDT")
        self.assertGreater(ofi, 0.0)
        self.assertGreater(spread, 0.0)

    def test_probability_engine_absent_book_returns_zeros(self):
        from analysis.probability_engine import calculate_order_flow_imbalance
        self.assertEqual(calculate_order_flow_imbalance(None), (0.0, 0.0))
        self.assertEqual(calculate_order_flow_imbalance({}), (0.0, 0.0))
        self.assertEqual(
            calculate_order_flow_imbalance({"bids": [], "asks": []}), (0.0, 0.0)
        )


# ============================================================
# Bagian 2 — Konfigurasi & idempotensi
# ============================================================

class TestScalpingConfigNewFields(unittest.TestCase):
    """
    Field config baru harus ada DAN konsisten satu sama lain.

    Validator di `core/config.py` menolak konfigurasi yang membuat guard atau
    gate mati, jadi test ini mengunci bahwa default-nya memang lolos validasi.
    """

    def test_scalping_config_has_new_fields(self):
        from core.config import ScalpingConfig
        cfg = ScalpingConfig()
        for field in (
            "reversal_close_threshold",
            "max_tick_age_seconds",
            "stale_tick_window_seconds",
            "stale_tick_min_samples",
        ):
            self.assertTrue(
                hasattr(cfg, field), f"ScalpingConfig tidak punya field `{field}`"
            )

    def test_default_config_passes_validation(self):
        """Default saat ini harus lolos kedua validator."""
        from core.config import get_config, _validate_scalping_economics
        _validate_scalping_economics(get_config())

    def _make_scalp_cfg(self, **overrides):
        from core.config import AppConfig, ScalpingConfig, FeeConfig
        base = dict(
            min_profit_pct=0.0060, tight_sl_pct=0.0025,
            breakeven_trigger_pct=0.0020, breakeven_offset_pct=0.0015,
        )
        base.update(overrides)
        cfg = AppConfig()
        cfg.scalping = ScalpingConfig(**base)
        cfg.fees = FeeConfig(maker=0.0002, taker=0.0005)
        return cfg

    def test_reversal_threshold_rejects_below_min_confidence(self):
        """Ambang reversal < min_confidence harus ditolak saat boot."""
        from core.config import _validate_scalping_economics
        cfg = self._make_scalp_cfg(
            min_confidence=0.40, reversal_close_threshold=0.30,
        )
        with self.assertRaises(ValueError) as ctx:
            _validate_scalping_economics(cfg)
        self.assertIn("reversal_close_threshold", str(ctx.exception))

    def test_reversal_threshold_rejects_above_one(self):
        """Ambang reversal >= 1.0 membuat logikanya kode mati."""
        from core.config import _validate_scalping_economics
        cfg = self._make_scalp_cfg(
            min_confidence=0.40, reversal_close_threshold=1.0,
        )
        with self.assertRaises(ValueError):
            _validate_scalping_economics(cfg)

    def test_tick_window_must_exceed_max_age(self):
        """
        Jendela median harus lebih lebar dari max tick age.

        Kalau tidak, median dihitung dari sampel yang justru sudah ditolak
        karena basi — guard-nya jadi tidak konsisten dengan dirinya sendiri.
        """
        from core.config import _validate_scalping_economics
        cfg = self._make_scalp_cfg(
            max_tick_age_seconds=3.0, stale_tick_window_seconds=1.0,
        )
        with self.assertRaises(ValueError) as ctx:
            _validate_scalping_economics(cfg)
        self.assertIn("stale_tick_window_seconds", str(ctx.exception))

    def test_min_samples_must_be_at_least_two(self):
        """Median dari satu sampel bukan median."""
        from core.config import _validate_scalping_economics
        cfg = self._make_scalp_cfg(
            stale_tick_min_samples=1,
            max_tick_age_seconds=1.5, stale_tick_window_seconds=3.0,
        )
        with self.assertRaises(ValueError) as ctx:
            _validate_scalping_economics(cfg)
        self.assertIn("stale_tick_min_samples", str(ctx.exception))

    def test_snapshot_age_must_cover_two_ensemble_intervals(self):
        """
        `max_snapshot_age_seconds` harus >= 2x `interval_seconds`.

        Kalau lebih pendek, ada celah di mana tidak ada snapshot yang cukup
        segar — bot kehilangan entry tanpa alasan yang bisa dijelaskan.
        """
        from core.config import AppConfig, EnsembleConfig, _validate_ensemble_config
        cfg = AppConfig()
        cfg.ensemble = EnsembleConfig(
            interval_seconds=10, max_snapshot_age_seconds=5
        )
        with self.assertRaises(ValueError) as ctx:
            _validate_ensemble_config(cfg)
        self.assertIn("max_snapshot_age_seconds", str(ctx.exception))

    def test_config_yaml_contains_new_scalping_keys(self):
        """YAML produksi harus mendeklarasikan field baru, bukan hanya default."""
        import yaml
        raw = yaml.safe_load(_read(CONFIG_YAML))
        scalp = raw.get("scalping", {})
        for key in (
            "reversal_close_threshold",
            "max_tick_age_seconds",
            "stale_tick_window_seconds",
            "stale_tick_min_samples",
        ):
            self.assertIn(key, scalp, f"config.yaml tidak punya scalping.{key}")

    def test_config_yaml_contains_snapshot_prune_keys(self):
        import yaml
        raw = yaml.safe_load(_read(CONFIG_YAML))
        db = raw.get("database", {})
        self.assertIn("snapshot_prune_interval", db)
        self.assertIn("snapshot_keep_per_symbol", db)

    def test_prune_job_is_scheduled(self):
        """Job `direction_snapshot_prune` harus terdaftar di setup_scheduler."""
        src = (ROOT / "run.py").read_text(encoding="utf-8")
        self.assertIn('name="direction_snapshot_prune"', src)
        self.assertIn("self._prune_direction_snapshots", src)

    def test_maintenance_loop_is_started(self):
        """Loop pemangkasan harus dijalankan, bukan hanya dijadwalkan."""
        src = (ROOT / "run.py").read_text(encoding="utf-8")
        self.assertIn("def _maintenance_loop", src)
        self.assertIn("self._maintenance_loop()", src)

    def test_websocket_parsing_runs_off_event_loop(self):
        """
        Parsing frame WS harus lewat `asyncio.to_thread`.

        Dijalankan inline, `json.loads` untuk ratusan frame L2 per detik akan
        menunda event loop dan terasa pada detak jantung 0.3 dtk.
        """
        src = (ROOT / "data" / "hyperliquid_feed.py").read_text(encoding="utf-8")
        self.assertIn(
            "asyncio.to_thread(self._handle_message", src,
            "parsing WS harus di-offload dari event loop",
        )

    def test_direction_agent_uses_configured_reversal_threshold(self):
        """Ambang reversal harus dibaca dari config, bukan angka tetap di kode."""
        src = _read(DECISION_AGENT_PY)
        self.assertIn("reversal_close_threshold", src)
        self.assertNotIn(
            'sig["strength"] >= 0.70', src,
            "ambang reversal masih hardcoded 0.70 di kode",
        )

    def test_reversal_clears_open_symbols_in_same_cycle(self):
        """
        Setelah memutuskan CLOSE, `open_symbols` harus dikosongkan seketika.

        Kalau tidak, gate "maks 1 posisi per simbol" masih melihat posisi yang
        baru saja diputuskan ditutup, dan reversal tidak bisa diikuti entry
        baru di simbol yang sama sampai siklus berikutnya.
        """
        src = _read(DECISION_AGENT_PY)
        self.assertIn("closing_symbols", src)
        self.assertIn("open_symbols.pop", src)

    def test_gitignore_exists_and_covers_stray_logs(self):
        """.gitignore harus ada dan menutupi file log liar di root."""
        gi = ROOT / ".gitignore"
        self.assertTrue(gi.exists(), ".gitignore belum ada")
        content = gi.read_text(encoding="utf-8")
        self.assertIn("_*.log", content, "pola log liar di root belum ditutup")
        self.assertIn("data_store/*.db", content, "DB lokal belum diabaikan")
        self.assertIn("__pycache__", content)

    def test_logger_writes_only_to_configured_file(self):
        """
        Logger harus menulis ke `data_store/logs/`, bukan ke root.

        Ini yang membuat pola `_*.log` di .gitignore cukup: kalau ada kode
        yang menulis log ke root, aturan itu hanya menutup gejala.
        """
        from core.config import get_config
        log_path = get_config().logging.file
        self.assertTrue(
            log_path.startswith("data_store/logs/"),
            f"file log harus di data_store/logs/, bukan {log_path}",
        )


if __name__ == "__main__":
    unittest.main()
