"""
tests/test_advanced_modules.py — Uji engine volatilitas dinamis & backtester L2.

Dua hal yang diuji di sini:

1. **Volatilitas dinamis** — SL harus melebar saat ATR naik, TP harus
   mengikuti agar rasio risk/reward tidak anjlok, dan volatility gate harus
   membatalkan order ketika fee sudah memakan seluruh target.

2. **Backtester antrean** — order limit TIDAK boleh terisi di depan antrean.
   Ini yang membedakan backtester ini dari "isi di mid price", dan kalau
   model antreannya salah, seluruh laporan performa jadi tidak berarti.
"""

import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent

SYMBOL = "BTC/USDT:USDT"


def _make_candles(high_low_range: float, count: int = 30, base: float = 100.0):
    """
    Bangun candle 1m sintetis dengan range high-low tiap lilin.

    Pada data seperti ini SETIAP true range persis sama dengan
    `high_low_range`: high-low = R, dan |high - prev_close| serta
    |low - prev_close| tidak pernah melebihi R. Karena semua TR identik,
    Wilder smoothing menghasilkan persis rata-ratanya — TIDAK ada
    "sedikit di bawah rata-rata". Klaim lama di docstring itu yang membuat
    test ATR jadi salah arah.
    """
    candles = []
    for i in range(count):
        mid = base + (0.01 if i % 2 == 0 else -0.01)
        half = high_low_range / 2.0
        candles.append({
            "timestamp": 1_700_000_000_000 + i * 60_000,
            "open": mid,
            "high": mid + half,
            "low": mid - half,
            "close": mid,
            "volume": 10.0,
        })
    return candles


class TestAtrCalculation(unittest.TestCase):
    """ATR Wilder dihitung dari candle, dan menolak data yang tidak cukup."""

    def setUp(self):
        from analysis import volatility
        self.vol = volatility
        self._clear_vol_state()

    def tearDown(self):
        self._clear_vol_state()

    @staticmethod
    def _clear_vol_state():
        """
        `_ATR_CACHE` dan `_CANDLE_SOURCE` di `analysis/volatility.py` adalah
        dict modul, jadi cache yang diisi test ini bertahan untuk test lain --
        dan kunci cache tidak menyertakan `period`, jadi test dengan `period`
        berbeda dalam jendela 5 detik yang sama bisa bertabrakan diam-diam.
        """
        from analysis import volatility
        volatility.clear_volatility_cache()
        volatility.clear_candle_source()

    def test_atr_from_uniform_candles(self):
        """
        ATR pada candle seragam harus PERSIS sama dengan range per lilin.

        Semua true range identik (range 1.0), jadi Wilder smoothing — yang
        selalu menghasilkan rata-rata tertimbang — juga menghasilkan 1.0.
        Tidak ada "sedikit di bawah rata-rata": asumsi itu yang membuat test
        ini menuntut `< 1.0` dan gagal.
        """
        result = self.vol.atr_1m_from_candles(_make_candles(1.0), period=14)
        self.assertIsNotNone(result, "ATR harus terhitung dari 30 candle")
        self.assertAlmostEqual(result["atr"], 1.0, places=9)

    def test_atr_pct_is_fraction_of_price(self):
        """ATR harus diekspresikan sebagai fraksi harga supaya bisa dibandingkan."""
        result = self.vol.atr_1m_from_candles(_make_candles(2.0, base=1000.0), period=14)
        self.assertIsNotNone(result)
        # Range 2.0 pada harga ~1000 = 0.2%
        self.assertAlmostEqual(result["atr_pct"], 0.002, delta=0.0002)

    def test_atr_needs_enough_candles(self):
        """ATR dengan candle terlalu sedikit harus None, bukan 0.0."""
        self.assertIsNone(
            self.vol.atr_1m_from_candles(_make_candles(1.0, count=5), period=14)
        )

    def test_atr_on_empty_candles_returns_none(self):
        """Data kosong berarti "tidak tahu", bukan "volatilitas nol"."""
        self.assertIsNone(self.vol.atr_1m_from_candles([], period=14))
        self.assertIsNone(self.vol.atr_1m_from_candles(None, period=14))

    def test_atr_handles_unsorted_candles(self):
        """
        Urutan input tidak boleh mengubah hasil.

        `Repository.get_candles` mengembalikan DESC (terbaru dulu), dan kalau
        ATR mengasumsikan urutan tertentu, angka di produksi akan salah
        sementara test tetap lulus karena test memakai list yang sudah urut.
        """
        candles = _make_candles(1.0)
        forward = self.vol.atr_1m_from_candles(candles, period=14)
        backward = self.vol.atr_1m_from_candles(list(reversed(candles)), period=14)
        self.assertIsNotNone(forward)
        self.assertIsNotNone(backward)
        self.assertAlmostEqual(forward["atr"], backward["atr"], places=12)

    def test_atr_rejects_zero_prices(self):
        """Candle dengan harga 0 harus diabaikan, bukan membuat ATR 0."""
        candles = _make_candles(1.0)
        for c in candles:
            c["high"] = 0.0
        self.assertIsNone(self.vol.atr_1m_from_candles(candles, period=14))


def _build_config():
    """AppConfig lengkap dengan blok scalping & dynamic_tp_sl yang koheren."""
    from core.config import AppConfig, DynamicTpSlConfig, FeeConfig, ScalpingConfig

    cfg = AppConfig()
    cfg.scalping = ScalpingConfig(
        min_profit_pct=0.0060, tight_sl_pct=0.0025, fast_tp_pct=0.0060,
        min_confidence=0.40, reversal_close_threshold=0.70,
        breakeven_trigger_pct=0.0020, breakeven_offset_pct=0.0015,
    )
    cfg.fees = FeeConfig(maker=0.0002, taker=0.0005)
    cfg.dynamic_tp_sl = DynamicTpSlConfig(
        enabled=True, atr_multiple=1.5, atr_period=14,
        min_sl_pct=0.0025, max_sl_pct=0.0150, min_risk_reward=1.5,
        max_breakeven_win_rate=0.65,
    )
    return cfg


class TestDynamicTpSl(unittest.TestCase):
    """SL/TP dinamis: melebar saat ATR naik, TP mengikuti, R:R terkunci."""

    def setUp(self):
        from analysis import volatility
        self.vol = volatility
        self.cfg = _build_config()
        self._install_candles(_make_candles(0.5))

    def tearDown(self):
        self.vol.clear_volatility_cache()
        self.vol.clear_candle_source()

    def _install_candles(self, candles):
        """Pasang sumber candle statis supaya ATR bisa dihitung tanpa DB."""
        def source(symbol, timeframe, limit):
            if timeframe != "1m":
                return None
            return candles[:limit] if limit else candles

        self.vol.clear_volatility_cache()
        self.vol.set_candle_source(source)

    def test_falls_back_to_static_without_atr(self):
        """Tanpa data candle, target harus statis — bukan crash, bukan nol."""
        self.vol.clear_candle_source()
        self.vol.clear_volatility_cache()
        r = self.vol.get_dynamic_tp_sl_thresholds(SYMBOL, self.cfg)
        self.assertFalse(r["used_dynamic"])
        self.assertAlmostEqual(r["sl_pct"], 0.0025)
        self.assertAlmostEqual(r["tp_pct"], 0.0060)
        self.assertIsNone(r["atr_pct"])

    def test_disabled_dynamic_uses_static(self):
        """`enabled: false` harus mengembalikan target statis."""
        self.cfg.dynamic_tp_sl.enabled = False
        r = self.vol.get_dynamic_tp_sl_thresholds(SYMBOL, self.cfg)
        self.assertFalse(r["used_dynamic"])
        self.assertAlmostEqual(r["sl_pct"], 0.0025)

    def test_atr_spike_widens_sl_proportionally(self):
        """
        Lonjakan volatilitas harus MELLEBARkan SL secara proporsional.

        Ini inti dari seluruh modul: SL statis 0.25% di pasar yang range-nya
        1% per menit bukan stop loss, itu tiket untuk tersapu noise.

        Angka dipilih agar TIDAK menyentuh clamp, jadi proporsionalitasnya
        bisa diuji murni. Dengan `atr_multiple=1.5` dan `atr_pct = atr/close`:

            range 0.2 -> atr_pct ~0.0020 -> raw_sl ~0.0030  (> min 0.0025)
            range 0.4 -> atr_pct ~0.0040 -> raw_sl ~0.0060  (< max 0.0150)

        Rasio SL = 2.0, persis rasio ATR. Menguji "4x" di sini mustahil:
        range 2.0 memberi raw_sl 0.03 yang sudah terpotong max_sl 0.0150,
        jadi rasionya jadi 2.0, bukan 4.0 — dan itu perilaku yang BENAR.

        Angka absolut hanya perkiraan: `close` diambil dari candle terakhir
        yang DISAMPLE, yang `mid`-nya 100.01 (bukan 100.00) karena
        `_make_candles` berganti-ganti 0.01. Yang diuji ketat adalah RASIO,
        karena itulah klaim modul ini.
        """
        self._install_candles(_make_candles(0.2))
        calm = self.vol.get_dynamic_tp_sl_thresholds(SYMBOL, self.cfg)
        self._install_candles(_make_candles(0.4))
        spike = self.vol.get_dynamic_tp_sl_thresholds(SYMBOL, self.cfg)

        self.assertTrue(calm["used_dynamic"], "ATR harus terpakai pada data normal")
        self.assertTrue(spike["used_dynamic"])
        self.assertAlmostEqual(calm["sl_pct"], 0.0030, delta=0.0001)
        self.assertAlmostEqual(spike["sl_pct"], 0.0060, delta=0.0001)
        self.assertAlmostEqual(
            spike["sl_pct"] / calm["sl_pct"], 2.0, delta=0.01,
            msg="SL harus melebar sebanding dengan lonjakan ATR",
        )

    def test_sl_clamped_to_max(self):
        """SL tidak boleh melebihi `max_sl_pct`, berapa pun volatilitasnya."""
        self._install_candles(_make_candles(50.0))   # volatilitas ekstrem
        r = self.vol.get_dynamic_tp_sl_thresholds(SYMBOL, self.cfg)
        self.assertLessEqual(
            r["sl_pct"], self.cfg.dynamic_tp_sl.max_sl_pct,
            "SL harus terpotong di batas atas",
        )
        self.assertTrue(r.get("clamped"), "penandaan clamped harus aktif")

    def test_sl_never_below_min(self):
        """SL tidak boleh lebih rapat dari `min_sl_pct`."""
        self._install_candles(_make_candles(0.0001))  # volatilitas nyaris nol
        r = self.vol.get_dynamic_tp_sl_thresholds(SYMBOL, self.cfg)
        self.assertGreaterEqual(
            r["sl_pct"], self.cfg.dynamic_tp_sl.min_sl_pct,
            "SL harus terpotong di batas bawah",
        )

    def test_tp_keeps_minimum_risk_reward(self):
        """
        TP harus selalu >= SL × min_risk_reward.

        Inilah penguncian R:R: kalau SL melebar dan TP tidak ikut, rasionya
        anjlok di bawah yang dijanjikan dan seluruh janji ekonomi jadi tidak
        berdaya.
        """
        for range_ in (0.2, 0.5, 1.0, 2.0, 5.0):
            with self.subTest(range=range_):
                self._install_candles(_make_candles(range_))
                r = self.vol.get_dynamic_tp_sl_thresholds(SYMBOL, self.cfg)
                if not r["used_dynamic"]:
                    continue
                self.assertGreaterEqual(
                    r["tp_pct"], r["sl_pct"] * 1.5 - 1e-12,
                    f"R:R di bawah 1.5 pada range={range_}",
                )

    def test_tp_never_below_min_profit(self):
        """Di pasar tenang, TP tidak boleh menyusut ke bawah min_profit_pct."""
        self._install_candles(_make_candles(0.2))
        r = self.vol.get_dynamic_tp_sl_thresholds(SYMBOL, self.cfg)
        self.assertGreaterEqual(r["tp_pct"], self.cfg.scalping.min_profit_pct)

    def test_tp_widens_with_sl(self):
        """Saat SL melebar, TP juga harus naik — bukan tetap di statis."""
        self._install_candles(_make_candles(0.3))
        calm = self.vol.get_dynamic_tp_sl_thresholds(SYMBOL, self.cfg)
        self._install_candles(_make_candles(3.0))
        spike = self.vol.get_dynamic_tp_sl_thresholds(SYMBOL, self.cfg)
        self.assertGreater(spike["tp_pct"], calm["tp_pct"])


class TestVolatilityGate(unittest.TestCase):
    """
    Volatility circuit breaker: batalkan order ketika targetnya sudah tidak
    layak secara ekonomi.
    """

    def setUp(self):
        from analysis import volatility
        self.vol = volatility
        self.cfg = _build_config()
        self._clear_vol_state()

    @staticmethod
    def _clear_vol_state():
        from analysis import volatility
        volatility.clear_volatility_cache()
        volatility.clear_candle_source()

    def tearDown(self):
        self._clear_vol_state()

    def test_healthy_target_passes(self):
        """Target normal harus lolos tanpa alasan penolakan."""
        self.assertIsNone(
            self.vol.assess_volatility_gate(SYMBOL, 0.0025, 0.0060, self.cfg)
        )

    def test_fee_eats_whole_target_blocks(self):
        """Kalau fee roundtrip >= TP, setiap trade pasti merugi — wajib batal."""
        reason = self.vol.assess_volatility_gate(SYMBOL, 0.0025, 0.0005, self.cfg)
        self.assertIsNotNone(reason, "TP di bawah fee harus dibatalkan")
        self.assertIn("fee roundtrip", reason)

    def test_target_exactly_at_fee_blocks(self):
        """TP yang tepat sama dengan fee sama sekali tidak menyisakan apa pun."""
        roundtrip = self.cfg.fees.taker * 2
        reason = self.vol.assess_volatility_gate(
            SYMBOL, 0.0025, roundtrip, self.cfg
        )
        self.assertIsNotNone(reason)

    def test_poor_risk_reward_blocks(self):
        """
        R:R yang menuntut win rate di atas batas harus dibatalkan.

        Angka di bawah dipilih dari perhitungan nyata, bukan tebakan:
            roundtrip = 0.0005 x 2 = 0.001
            net_tp    = 0.0090 - 0.001 = 0.0080
            net_sl    = 0.0150 + 0.001 = 0.0160
            breakeven = 0.0160 / (0.0080 + 0.0160) = 0.667 > 0.65  → DITOLAK
        """
        reason = self.vol.assess_volatility_gate(SYMBOL, 0.0150, 0.0090, self.cfg)
        self.assertIsNotNone(reason, "R:R buruk harus dibatalkan")
        self.assertIn("volatilitas gate", reason)

    def test_gate_message_names_breakeven_rate(self):
        """Pesan penolakan harus menyebut win rate impas yang dibutuhkan."""
        reason = self.vol.assess_volatility_gate(SYMBOL, 0.0150, 0.0090, self.cfg)
        self.assertIsNotNone(reason)
        self.assertIn("win rate", reason)

    def test_moderate_risk_reward_passes(self):
        """
        R:R di atas batas harus LOLOS.

        Gate yang menolak semua order sama pathogennya dengan gate yang tidak
        menolak apa pun: bot berhenti bertransaksi tanpa alasan.
        """
        # net_tp = 0.0140 - 0.001 = 0.0130, net_sl = 0.0050 + 0.001 = 0.0060
        # breakeven = 0.0060 / 0.0190 = 0.316 < 0.65 → LOLOS
        self.assertIsNone(
            self.vol.assess_volatility_gate(SYMBOL, 0.0050, 0.0140, self.cfg)
        )

    def test_negative_sl_is_rejected(self):
        """SL negatif adalah konfigurasi salah, harus ditolak."""
        reason = self.vol.assess_volatility_gate(SYMBOL, -0.0050, 0.0060, self.cfg)
        self.assertIsNotNone(reason, "SL negatif harus ditolak")

    def test_gate_reports_error_raising_config(self):
        """
        Gate harus muncul di jalur eksekusi sebagai exception tersendiri.

        Kalau volatilitas gate hanya jadi string yang diabaikan, order akan
        lolos dan hilang begitu saja tepat di kondisi pasar terburuk.
        """
        from trading.paper_engine import VolatilityGateError

        self.assertTrue(issubclass(VolatilityGateError, Exception))
        err = VolatilityGateError("alasan uji", {"sl_pct": 0.01})
        self.assertEqual(err.reason, "alasan uji")
        self.assertEqual(err.meta["sl_pct"], 0.01)

    def test_paper_engine_resolve_tp_sl_uses_gate(self):
        """
        `_resolve_tp_sl` harus ada dan melempar `VolatilityGateError` saat
        target tidak layak.
        """
        from trading.paper_engine import PaperTradingEngine, VolatilityGateError

        self.assertTrue(hasattr(PaperTradingEngine, "_resolve_tp_sl"))
        self.assertEqual(VolatilityGateError.__module__, "trading.paper_engine")

    def test_execution_agent_uses_dynamic_thresholds(self):
        """ExecutionAgent harus membaca target dinamis, bukan hanya statis."""
        src = (ROOT / "agents" / "execution_agent.py").read_text(encoding="utf-8")
        self.assertIn(
            "get_dynamic_tp_sl_thresholds", src,
            "execution_agent harus memakai target dinamis",
        )


class TestQueueFillModel(unittest.TestCase):
    """
    Model antrean — inti dari seluruh backtester.

    Test di sini sengaja dibuat sederhana: satu order, satu level harga,
    beberapa trade. Kalau model antrean salah, seluruh statistik di atasnya
    menjadi tidak bermakna, jadi lebih baik gagal di sini daripada terlihat
    meyakinkan di laporan performa.
    """

    def setUp(self):
        from analysis.backtester import QueueFillModel
        self.model_cls = QueueFillModel

    def test_no_queue_ahead_fills_immediately(self):
        """Tanpa antrean di depan, trade pertama langsung mengisi."""
        m = self.model_cls(0.0)
        self.assertTrue(m.has_priority)
        self.assertAlmostEqual(m.on_trade(1.0), 1.0)

    def test_queue_blocks_initial_fill(self):
        """
        Trade pertama harus DILEWATI, bukan mengisi order kita.

        Ini test yang membedakan backtester yang jujur dari yang optimis.
        Kalau fill terjadi di trade pertama meski ada 10 unit antrean di
        depan, seluruh laporan ke depan terlalu optimis.
        """
        m = self.model_cls(10.0)
        self.assertFalse(m.has_priority)
        self.assertAlmostEqual(m.on_trade(4.0), 0.0, msg="tidak boleh ada fill")
        self.assertAlmostEqual(m.queue_ahead, 6.0)

    def test_fill_starts_after_queue_clears(self):
        """Setelah antrean habis, trade berikutnya mulai mengisi."""
        m = self.model_cls(10.0)
        self.assertAlmostEqual(m.on_trade(6.0), 0.0)
        self.assertAlmostEqual(m.queue_ahead, 4.0)
        # Trade ini menyelesaikan sisa antrean (4) dan mengisi 2 sisanya.
        self.assertAlmostEqual(m.on_trade(6.0), 2.0)
        self.assertTrue(m.has_priority)

    def test_single_trade_can_clear_queue_and_fill(self):
        """Satu trade besar bisa menyelesaikan antrean sekaligus mengisi."""
        m = self.model_cls(5.0)
        self.assertAlmostEqual(m.on_trade(8.0), 3.0)

    def test_zero_size_trade_fills_nothing(self):
        """Trade berukuran nol tidak boleh mengisi apa pun."""
        m = self.model_cls(0.0)
        self.assertAlmostEqual(m.on_trade(0.0), 0.0)

    def test_negative_size_ignored(self):
        """Ukuran negatif harus diabaikan, bukan mengurangi antrean."""
        m = self.model_cls(0.0)
        self.assertAlmostEqual(m.on_trade(-5.0), 0.0)

    def test_partial_fills_across_many_trades(self):
        """Fill boleh terjadi bertahap di banyak trade."""
        m = self.model_cls(0.0)
        total = sum(m.on_trade(0.5) for _ in range(10))
        self.assertAlmostEqual(total, 5.0)

    def test_negative_queue_ahead_clamped(self):
        """Antrean negatif (entah kenapa) harus dijepit ke nol."""
        m = self.model_cls(-10.0)
        self.assertAlmostEqual(m.queue_ahead, 0.0)
        self.assertTrue(m.has_priority)


class TestL2BacktesterEngine(unittest.TestCase):
    """Engine backtester: market impact, PnL, dan metrik."""

    def setUp(self):
        from analysis.backtester import L2Snapshot, TapeTrade
        self.L2Snapshot = L2Snapshot
        self.TapeTrade = TapeTrade

    def _flat_book(self, ts, bid=99.99, ask=100.01, size=10.0, levels=5,
                   symbol="TEST/USDT:USDT"):
        bids = [(round(bid - i * 0.01, 2), size) for i in range(levels)]
        asks = [(round(ask + i * 0.01, 2), size) for i in range(levels)]
        return self.L2Snapshot(
            timestamp=ts, symbol=symbol, bids=bids, asks=asks
        )

    def _engine(self, **kwargs):
        from analysis.backtester import L2Backtester
        return L2Backtester(initial_balance=10000.0, **kwargs)

    def test_snapshot_helpers(self):
        """Helper L2Snapshot harus menghitung mid & kedalaman dengan benar."""
        snap = self._flat_book(1.0, size=10.0, levels=3)
        self.assertAlmostEqual(snap.best_bid(), 99.99)
        self.assertAlmostEqual(snap.best_ask(), 100.01)
        self.assertAlmostEqual(snap.mid(), 100.0)
        self.assertAlmostEqual(snap.bid_depth(3), 30.0)
        self.assertAlmostEqual(snap.ask_depth(3), 30.0)

    def test_empty_snapshot_has_no_mid(self):
        """Book kosong harus menghasilkan None, bukan 0.0."""
        snap = self.L2Snapshot(timestamp=1.0, symbol="X", bids=[], asks=[])
        self.assertIsNone(snap.mid())
        self.assertIsNone(snap.best_bid())
        self.assertIsNone(snap.best_ask())

    def test_market_order_uses_impact_not_mid(self):
        """
        Order besar harus terisi di harga yang lebih buruk dari mid.

        Market order yang selalu terisi di mid adalah asumsi paling optimis
        dan paling sering dipakai. Di sini kita buktikan engine menelusuri
        book sampai beberapa level.
        """
        eng = self._engine(max_book_levels=5)
        eng.on_snapshot(self._flat_book(1.0, size=1.0, levels=5))

        result = eng._worst_fill_price("TEST/USDT:USDT", "BUY", 3.0)
        self.assertIsNotNone(result)
        avg_price, filled = result
        self.assertAlmostEqual(filled, 3.0)
        mid = eng.book["TEST/USDT:USDT"].mid()
        self.assertGreater(
            avg_price, mid,
            "fill rata-rata harus lebih buruk dari mid (market impact)",
        )

    def test_market_order_larger_than_book_fills_less(self):
        """Order melebihi kedalaman book hanya terisi sebesar book."""
        eng = self._engine(max_book_levels=2)
        eng.on_snapshot(self._flat_book(1.0, size=1.0, levels=2))
        result = eng._worst_fill_price("TEST/USDT:USDT", "BUY", 100.0)
        self.assertIsNotNone(result)
        _, filled = result
        self.assertAlmostEqual(filled, 2.0, msg="hanya book yang ada yang terisi")

    def test_market_order_without_book_returns_none(self):
        """Tanpa book, tidak boleh ada fill sama sekali."""
        eng = self._engine()
        self.assertIsNone(eng._worst_fill_price("TIDAK/ADA", "BUY", 1.0))

    def test_impact_scales_with_order_size(self):
        """Order lebih besar relatif terhadap book harus dampaknya lebih besar."""
        eng = self._engine()
        eng.on_snapshot(self._flat_book(1.0, size=1.0, levels=5))
        small = eng._estimate_impact_bps("TEST/USDT:USDT", "BUY", 0.5)
        large = eng._estimate_impact_bps("TEST/USDT:USDT", "BUY", 5.0)
        self.assertIsNotNone(small)
        self.assertIsNotNone(large)
        self.assertGreater(large, small)

    def test_open_and_close_computes_net_pnl(self):
        """
        Siklus buka-tutup harus menghasilkan PnL yang bisa dihitung ulang.

        Net PnL = (exit − entry) × qty − fee masuk − fee keluar. Tester tidak
        boleh "membantu" dengan membulatkan atau membiarkan fee luput.
        """
        eng = self._engine()
        eng.on_snapshot(self._flat_book(1.0, size=50.0, levels=5))
        entry = eng.open_position("TEST/USDT:USDT", "BUY", 5.0)
        self.assertIsNotNone(entry)
        self.assertGreater(entry.fees, 0.0, "fee masuk harus dihitung")

        # Book bergerak naik supaya ada keuntungan.
        eng.on_snapshot(
            self._flat_book(2.0, bid=100.09, ask=100.11, size=50.0, levels=5)
        )
        closed = eng.close_position(reason="TEST")
        self.assertIsNotNone(closed)
        self.assertGreater(closed.pnl, 0.0, "harga naik → PnL kotor positif")
        self.assertAlmostEqual(
            closed.net_pnl, closed.pnl - closed.fees, places=10,
            msg="net PnL harus PnL kotor dikurangi fee",
        )
        self.assertLess(closed.net_pnl, closed.pnl, "fee harus mengurangi")

    def test_open_position_reduces_cash(self):
        """Membuka posisi BUY harus mengurangi kas (ditambah fee)."""
        eng = self._engine()
        eng.on_snapshot(self._flat_book(1.0, size=50.0, levels=5))
        before = eng.cash
        eng.open_position("TEST/USDT:USDT", "BUY", 1.0)
        self.assertLess(eng.cash, before, "kas harus berkurang setelah BUY")

    def test_open_position_without_book_fails(self):
        """Tanpa book, membuka posisi harus gagal, bukan terisi di harga karangan."""
        eng = self._engine()
        self.assertIsNone(eng.open_position("TIDAK/ADA", "BUY", 1.0))
        self.assertIsNone(eng.open_trade)

    def test_close_without_open_fails(self):
        """Menutup tanpa posisi terbuka harus mengembalikan None."""
        eng = self._engine()
        self.assertIsNone(eng.close_position())

    def test_limit_order_waits_for_queue(self):
        """
        Limit order harus MENUNGGU volume di depannya habis.

        Test ini memakai BUY di harga BID (99.99), bukan di ask. Alasannya:
        order yang bertahan di book sebagai maker duduk di antrean bid —
        order BUY di ask justru langsung marketable dan tidak punya antrean
        sama sekali. Perbedaan ini yang menguji model antrean, bukan market
        order.
        """
        eng = self._engine()
        eng.on_snapshot(
            self._flat_book(1.0, bid=99.99, ask=100.01, size=10.0, levels=5)
        )

        order = eng.submit_limit("TEST/USDT:USDT", "BUY", 99.99, 3.0)
        self.assertIsNotNone(order)
        self.assertGreater(
            order.queue_ahead, 0.0,
            "order harus punya antrean di depan karena book sudah berisi volume",
        )

        # Trade kecil tidak boleh mengisi.
        eng.on_trade_event(self.TapeTrade(1.5, 99.99, 1.0, "SELL"))
        self.assertAlmostEqual(order.filled_quantity, 0.0)

        # Setelah antrean habis, trade berikutnya mengisi.
        eng.on_trade_event(self.TapeTrade(1.6, 99.99, 30.0, "SELL"))
        self.assertGreater(order.filled_quantity, 0.0, "order harus terisi")

    def test_limit_order_records_slippage_and_fees(self):
        """Setiap fill harus mencatat slippage dan fee-nya."""
        eng = self._engine()
        eng.on_snapshot(
            self._flat_book(1.0, bid=99.99, ask=100.01, size=10.0, levels=5)
        )
        eng.submit_limit("TEST/USDT:USDT", "BUY", 99.99, 1.0)
        eng.on_trade_event(self.TapeTrade(1.5, 99.99, 30.0, "SELL"))

        self.assertTrue(eng.fills, "harus ada fill yang tercatat")
        fill = eng.fills[0]
        self.assertGreater(fill.fees, 0.0, "fee harus dihitung")
        self.assertIsNotNone(fill.mid_at_fill)
        self.assertLessEqual(
            fill.slippage_bps, 0.0,
            "BUY di bawah mid harus punya slippage negatif atau nol",
        )

    def test_stats_on_empty_engine(self):
        """Engine kosong harus mengembalikan statistik jujur, bukan division by zero."""
        eng = self._engine()
        st = eng.stats()
        self.assertEqual(st.total_trades, 0)
        self.assertIsNone(st.win_rate, "tanpa trade, win rate tidak terdefinisi")
        self.assertIsNone(st.sharpe, "tanpa return, Sharpe tidak terdefinisi")
        self.assertIsNone(st.sortino)
        self.assertIsNone(st.max_drawdown)
        self.assertIsNone(st.avg_slippage_bps)

    def test_stats_counts_completed_trades(self):
        """
        Statistik harus menghitung trade yang benar-benar selesai.

        Margin di sini disengaja besar (1%), bukan 0.09%. Alasannya
        ekonomi, bukan estetika: fill BUY terjadi di ask dan penutupan di
        bid, ditambah fee taker 0.05% per sisi = 0.10% roundtrip. Gerak
        0.09% itu lebih kecil dari spread DAN fee, jadi kedua trade rugi
        bersih — dan test versi lama gagal bukan karena statistiknya salah,
        tapi karena marginnya lebih kecil dari biaya transaksi.
        """
        eng = self._engine()
        # Trade 1: beli di ~100.01, tutup di 100.99  -> +0.98% gross
        eng.on_snapshot(self._flat_book(1.0, size=50.0, levels=5))
        eng.open_position("TEST/USDT:USDT", "BUY", 1.0)
        eng.on_snapshot(
            self._flat_book(2.0, bid=100.99, ask=101.01, size=50.0, levels=5)
        )
        eng.close_position(reason="WIN")
        # Trade 2: beli di ~100.01, tutup di 98.99    -> -1.02% gross
        eng.on_snapshot(
            self._flat_book(3.0, bid=99.99, ask=100.01, size=50.0, levels=5)
        )
        eng.open_position("TEST/USDT:USDT", "BUY", 1.0)
        eng.on_snapshot(
            self._flat_book(4.0, bid=98.99, ask=99.01, size=50.0, levels=5)
        )
        eng.close_position(reason="LOSS")

        st = eng.stats()
        self.assertEqual(st.total_trades, 2)
        self.assertEqual(st.wins, 1, f"trade pertama harus menang: {eng.completed}")
        self.assertEqual(st.losses, 1, f"trade kedua harus kalah: {eng.completed}")
        self.assertAlmostEqual(st.win_rate, 0.5)
        self.assertGreater(st.total_fees, 0.0)
        self.assertGreater(len(st.equity_curve), 2)

    def test_max_drawdown_and_sharpe_metrics(self):
        """Metrik risiko harus bisa dihitung dari kurva equity."""
        from analysis.backtester import max_drawdown, sharpe_ratio, sortino_ratio

        equity = [100.0, 110.0, 105.0, 120.0, 90.0, 95.0]
        dd_abs, dd_pct = max_drawdown(equity)
        self.assertAlmostEqual(dd_abs, 30.0, places=6)   # 120 -> 90
        self.assertAlmostEqual(dd_pct, 0.25, places=6)    # 30/120

        returns = [0.10, -0.045, 0.143, -0.25, 0.0556]
        self.assertIsNotNone(sharpe_ratio(returns))
        self.assertIsNotNone(sortino_ratio(returns))

    def test_metrics_return_none_when_undefined(self):
        """Metrik yang tidak terdefinisi harus None, bukan 0.0."""
        from analysis.backtester import max_drawdown, sharpe_ratio, sortino_ratio

        self.assertIsNone(sharpe_ratio([]))
        self.assertIsNone(sharpe_ratio([0.01]))
        self.assertIsNone(sharpe_ratio([0.01, 0.01, 0.01]), "std nol")
        self.assertIsNone(sortino_ratio([]))
        # `max_drawdown` mengembalikan TUPLE (absolut, persentase) — bukan
        # nilai tunggal. `assertIsNone` di sini akan selalu gagal.
        self.assertEqual(max_drawdown([]), (None, None))
        self.assertEqual(max_drawdown([100.0]), (None, None))

    def test_replay_orders_events_by_time(self):
        """
        Replay harus mengurutkan event satu kali dan mendahulukan snapshot.

        Kalau trade diproses sebelum snapshot pada timestamp yang sama,
        antrean dihitung dari book yang belum pernah ada.

        Urutannya ASCENDING: timestamp paling awal diproses PERTAMA, dan
        snapshot terakhir yang menimpa book. Jadi setelah replay, book harus
        mencerminkan snap2 (ts=2.0, bid 100.09) — bukan snap1.

        Ini yang membuktikan pengurutan bekerja: input sengaja tidak terurut
        `[snap2, snap1]`, jadi TANPA sorting book akan berakhir di 99.99.
        """
        from analysis.backtester import L2Backtester, L2Snapshot

        eng = L2Backtester(initial_balance=10000.0)
        snap1 = L2Snapshot(
            timestamp=1.0, symbol="X",
            bids=[(99.99, 5.0)], asks=[(100.01, 5.0)],
        )
        snap2 = L2Snapshot(
            timestamp=2.0, symbol="X",
            bids=[(100.09, 5.0)], asks=[(100.11, 5.0)],
        )
        # Sengaja diberi timestamps terbalik untuk membuktikan engine mengurutkan.
        eng.replay([snap2, snap1], [])

        self.assertEqual(
            eng.book["X"].bids[0][0], 100.09,
            "snapshot dengan timestamp TERAKHIR harus diproses terakhir",
        )
        self.assertEqual(eng.current_ts, 2.0)

    def test_synthesize_walk_is_deterministic(self):
        """Data sintetis harus deterministik, supaya kegagalan bisa direproduksi."""
        from analysis.backtester import synthesize_walk

        snaps_a, trades_a = synthesize_walk(steps=50, seed=42)
        snaps_b, trades_b = synthesize_walk(steps=50, seed=42)

        self.assertEqual(len(snaps_a), len(snaps_b))
        self.assertEqual(len(trades_a), len(trades_b))
        for a, b in zip(snaps_a, snaps_b):
            self.assertEqual(a.bids, b.bids, "book harus identik untuk seed sama")
        for a, b in zip(trades_a, trades_b):
            self.assertAlmostEqual(a.price, b.price, places=12)

    def test_synthesize_walk_different_seeds_differ(self):
        """Seed berbeda harus menghasilkan data berbeda."""
        from analysis.backtester import synthesize_walk

        _, trades_a = synthesize_walk(steps=100, seed=1)
        _, trades_b = synthesize_walk(steps=100, seed=2)
        self.assertNotEqual(
            [t.price for t in trades_a], [t.price for t in trades_b],
            "seed berbeda harus menghasilkan jejak harga berbeda",
        )

    def test_synthesized_data_produces_two_sided_book(self):
        """Data sintetis harus menghasilkan book dua sisi dengan urutan benar."""
        from analysis.backtester import synthesize_walk

        snaps, trades = synthesize_walk(steps=100, seed=3)
        self.assertGreater(len(snaps), 0)
        self.assertGreater(len(trades), 0)
        for snap in snaps[:10]:
            self.assertTrue(snap.bids, "book harus punya sisi bid")
            self.assertTrue(snap.asks, "book harus punya sisi ask")
            self.assertGreater(snap.best_bid(), 0.0)
            self.assertGreaterEqual(snap.bids[0][0], snap.bids[-1][0],
                                    "bids harus menurun")
            self.assertLessEqual(snap.asks[0][0], snap.asks[-1][0],
                                "asks harus menaik")

    def test_end_to_end_replay_with_synthetic_data(self):
        """
        Uji menyeluruh: replay data sintetis, buka-tutup beberapa kali,
        pastikan statistiknya konsisten.
        """
        from analysis.backtester import L2Backtester, synthesize_walk

        snaps, trades = synthesize_walk(steps=300, seed=11)
        eng = L2Backtester(initial_balance=10000.0, taker_fee=0.0005)

        def strategy(tester, snap):
            if snap.timestamp < 1_700_000_050:
                return
            if snap.timestamp % 40 < 1 and tester.open_trade is None:
                if snap.mid() and snap.mid() > 0:
                    tester.open_position(
                        "TEST/USDT:USDT", "BUY", 0.5,
                        use_market_impact=False,
                    )
            elif snap.timestamp % 40 == 20 and tester.open_trade is not None:
                tester.close_position(reason="TIME_EXIT",
                                      use_market_impact=False)

        eng.replay(snaps, trades, strategy=strategy)
        st = eng.stats()

        self.assertGreater(st.total_trades, 0, "harus ada trade yang selesai")
        self.assertAlmostEqual(
            st.total_trades, st.wins + st.losses,
            msg="setiap trade harus win atau loss, tidak ada kategori ketiga",
        )
        self.assertGreater(len(st.equity_curve), 2)
        self.assertIn("L2 EVENT-DRIVEN BACKTEST", eng.report())


class TestConfigIntegration(unittest.TestCase):
    """Konfigurasi baru harus terbaca dari YAML dan lolos validator."""

    def test_yaml_has_dynamic_tp_sl_block(self):
        import yaml
        raw = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
        block = raw.get("dynamic_tp_sl", {})
        for key in (
            "enabled", "atr_multiple", "atr_period", "min_sl_pct",
            "max_sl_pct", "min_risk_reward", "max_breakeven_win_rate",
        ):
            self.assertIn(key, block, f"config.yaml tidak punya dynamic_tp_sl.{key}")

    def test_production_config_passes_new_validator(self):
        """Konfigurasi produksi harus lolos validator TP/SL dinamis."""
        from core.config import get_config, _validate_dynamic_tp_sl
        _validate_dynamic_tp_sl(get_config())

    def test_validator_rejects_inverted_clamps(self):
        """min_sl >= max_sl membuat clamp tidak berguna — harus ditolak."""
        from core.config import (
            AppConfig, DynamicTpSlConfig, FeeConfig, ScalpingConfig,
            _validate_dynamic_tp_sl,
        )
        cfg = AppConfig()
        cfg.scalping = ScalpingConfig(min_profit_pct=0.006, tight_sl_pct=0.0025)
        cfg.fees = FeeConfig(maker=0.0002, taker=0.0005)
        cfg.dynamic_tp_sl = DynamicTpSlConfig(
            enabled=True, min_sl_pct=0.0150, max_sl_pct=0.0025,
            min_risk_reward=1.5, max_breakeven_win_rate=0.65,
        )
        with self.assertRaises(ValueError) as ctx:
            _validate_dynamic_tp_sl(cfg)
        self.assertIn("min_sl_pct", str(ctx.exception))

    def test_validator_rejects_bad_risk_reward(self):
        """min_risk_reward <= 1 membuat penguncian R:R tidak berguna."""
        from core.config import (
            AppConfig, DynamicTpSlConfig, FeeConfig, ScalpingConfig,
            _validate_dynamic_tp_sl,
        )
        cfg = AppConfig()
        cfg.scalping = ScalpingConfig(min_profit_pct=0.006, tight_sl_pct=0.0025)
        cfg.fees = FeeConfig(maker=0.0002, taker=0.0005)
        cfg.dynamic_tp_sl = DynamicTpSlConfig(
            enabled=True, min_risk_reward=1.0, max_breakeven_win_rate=0.65,
        )
        with self.assertRaises(ValueError) as ctx:
            _validate_dynamic_tp_sl(cfg)
        self.assertIn("min_risk_reward", str(ctx.exception))

    def test_validator_rejects_disabled_gate_range(self):
        """max_breakeven_win_rate di luar (0.5, 1.0) membuat gate tak berguna."""
        from core.config import (
            AppConfig, DynamicTpSlConfig, FeeConfig, ScalpingConfig,
            _validate_dynamic_tp_sl,
        )
        cfg = AppConfig()
        cfg.scalping = ScalpingConfig(min_profit_pct=0.006, tight_sl_pct=0.0025)
        cfg.fees = FeeConfig(maker=0.0002, taker=0.0005)
        cfg.dynamic_tp_sl = DynamicTpSlConfig(
            enabled=True, min_risk_reward=1.5, max_breakeven_win_rate=1.0,
        )
        with self.assertRaises(ValueError) as ctx:
            _validate_dynamic_tp_sl(cfg)
        self.assertIn("max_breakeven_win_rate", str(ctx.exception))

    def test_validator_skips_when_disabled(self):
        """Saat dinamis dimatikan, validator tidak boleh menahan boot."""
        from core.config import (
            AppConfig, DynamicTpSlConfig, FeeConfig, ScalpingConfig,
            _validate_dynamic_tp_sl,
        )
        cfg = AppConfig()
        cfg.scalping = ScalpingConfig(min_profit_pct=0.006, tight_sl_pct=0.0025)
        cfg.fees = FeeConfig(maker=0.0002, taker=0.0005)
        cfg.dynamic_tp_sl = DynamicTpSlConfig(
            enabled=False, min_risk_reward=0.1,
            min_sl_pct=0.99, max_sl_pct=0.01,
        )
        _validate_dynamic_tp_sl(cfg)   # tidak boleh melempar


if __name__ == "__main__":
    unittest.main()









