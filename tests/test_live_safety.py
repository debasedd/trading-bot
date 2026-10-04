"""
tests/test_live_safety.py — Bukti bahwa gerbang live benar-benar MENAHAN.

Fokus test ini bukan "kode jalan", tapi "kode menolak". Untuk uang
sungguhan, kegagalan yang paling merusak adalah gerbang yang TIDAK
menolak — itulah yang membuat order tidak seharusnya keluar. Jadi setiap
test di sini mencoba MELEWATI gerbang dan membuktikan bahwa usaha itu
gagal.

Tidak ada test di file ini yang menyentuh jaringan. Semua dependensi bursa
disuntikkan sebagai stub, jadi tidak mungkin ada order sungguhan yang
terkirim karena salah jalan test.
"""
import pathlib
import unittest
from datetime import datetime, timezone

from core.config import LiveConfig
from trading.live.safety import Blocker, OrderRequest, SafetyGate


def _cfg(**overrides) -> LiveConfig:
    cfg = LiveConfig()
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def _state_path():
    """Path unik per test supaya penghitung harian tidak bocor antar test."""
    import tempfile
    return pathlib.Path(tempfile.mkdtemp()) / "counters.json"


def _env(**overrides) -> dict:
    """Environment yang MEMBOLEHKAN trading, supaya test bisa fokus pada
    satu kondisi penolakan saja."""
    env = {
        "TRADEBOT_LIVE": "1",
        "TRADEBOT_LIVE_CONFIRMED": "1",
        "HYPERLIQUID_PRIVATE_KEY": "0x" + "ab" * 32,
    }
    env.update(overrides)
    return env


def _midday() -> datetime:
    """Waktu yang jelas ADA di dalam jendela default (13, 23)."""
    return datetime(2026, 1, 15, 15, 0, tzinfo=timezone.utc)


class TestGateDefaultsRefuseEverything(unittest.TestCase):
    """Dengan konfigurasi apa adanya, TIDAK ADA yang boleh keluar."""

    def test_empty_env_blocks_everything(self):
        gate = SafetyGate(LiveConfig(), env={})
        blockers = gate.master_blockers(_midday())
        self.assertIn(Blocker.LIVE_DISABLED, blockers)
        self.assertIn(Blocker.NOT_CONFIRMED, blockers)
        self.assertIn(Blocker.MISSING_KEY, blockers)

    def test_config_enabled_does_not_bypass_env(self):
        """
        `LiveConfig.enabled = True` TIDAK cukup.

        Ini pengaman terhadap file config yang ikut ter-commit: nilai di
        YAML harus diabaikan, hanya environment yang Counts.
        """
        gate = SafetyGate(_cfg(enabled=True), env={})
        self.assertIn(Blocker.LIVE_DISABLED, gate.master_blockers(_midday()))

    def test_private_key_alone_is_not_enough(self):
        """Key tanpa konfirmasi eksplisit tetap ditolak."""
        gate = SafetyGate(
            _cfg(), env={"HYPERLIQUID_PRIVATE_KEY": "0x" + "cd" * 32}
        )
        blockers = gate.master_blockers(_midday())
        self.assertIn(Blocker.NOT_CONFIRMED, blockers)
        self.assertIn(Blocker.LIVE_DISABLED, blockers)

    def test_confirmation_alone_is_not_enough(self):
        """Konfirmasi tanpa key tetap ditolak."""
        gate = SafetyGate(_cfg(), env={"TRADEBOT_LIVE": "1",
                                      "TRADEBOT_LIVE_CONFIRMED": "1"})
        self.assertIn(Blocker.MISSING_KEY, gate.master_blockers(_midday()))

    def test_wrong_truthy_value_does_not_enable(self):
        """Hanya '1', 'true', 'yes' yang dihitung — bukan 'on' atau 'y'."""
        for value in ("0", "false", "no", "off", "enabled", "yes please", "2"):
            gate = SafetyGate(_cfg(), env=_env(TRADEBOT_LIVE=value))
            self.assertIn(
                Blocker.LIVE_DISABLED, gate.master_blockers(_midday()),
                "nilai '{}' tidak boleh menyalakan live".format(value),
            )


class TestKillSwitch(unittest.TestCase):
    def test_env_kill_switch_blocks(self):
        gate = SafetyGate(_cfg(), env=_env(TRADEBOT_LIVE_KILL_SWITCH="1"))
        self.assertIn(Blocker.KILL_SWITCH, gate.master_blockers(_midday()))

    def test_internal_kill_switch_blocks(self):
        gate = SafetyGate(_cfg(), env=_env(), state_path=_state_path())
        gate.engage_kill_switch("uji")
        self.assertIn(Blocker.KILL_SWITCH, gate.master_blockers(_midday()))

    def test_consecutive_errors_trip_kill_switch(self):
        """Error beruntun harus mematikan trading sendiri."""
        gate = SafetyGate(_cfg(max_consecutive_errors=3), env=_env(), state_path=_state_path())
        for _ in range(3):
            gate.record_error()
        self.assertTrue(gate.engaged, "kill switch harus aktif")
        self.assertIn(Blocker.KILL_SWITCH, gate.master_blockers(_midday()))

    def test_success_resets_error_streak(self):
        gate = SafetyGate(_cfg(max_consecutive_errors=3), env=_env(), state_path=_state_path())
        gate.record_error()
        gate.record_error()
        gate.record_success()
        gate.record_error()
        self.assertFalse(gate.engaged, "streak harus terputus oleh sukses")


class TestLiveWindow(unittest.TestCase):
    def test_inside_window_allowed(self):
        gate = SafetyGate(_cfg(live_window_utc=(13, 23)), env=_env(), state_path=_state_path())
        self.assertTrue(gate.in_live_window(_midday()))

    def test_before_window_blocked(self):
        gate = SafetyGate(_cfg(live_window_utc=(13, 23)), env=_env(), state_path=_state_path())
        early = datetime(2026, 1, 15, 3, 0, tzinfo=timezone.utc)
        self.assertFalse(gate.in_live_window(early))
        self.assertIn(Blocker.OUTSIDE_WINDOW, gate.master_blockers(early))

    def test_after_window_blocked(self):
        gate = SafetyGate(_cfg(live_window_utc=(13, 23)), env=_env(), state_path=_state_path())
        late = datetime(2026, 1, 15, 23, 30, tzinfo=timezone.utc)
        self.assertFalse(gate.in_live_window(late))

    def test_window_end_is_exclusive(self):
        """
        Batas jendela bersifat [start, end).

        Jam PERSIS di batas akhir harus DI LUAR — kalau sebuah order lolos
        pada 23:00:00 padahal jendela berakhir jam 23, maka aktivitas
        sudah di luar rencana.
        """
        gate = SafetyGate(_cfg(live_window_utc=(13, 23)), env=_env(), state_path=_state_path())
        self.assertFalse(gate.in_live_window(
            datetime(2026, 1, 15, 23, 0, tzinfo=timezone.utc)),
            "tepat jam 23:00 sudah di luar jendela (13, 23)")
        self.assertTrue(gate.in_live_window(
            datetime(2026, 1, 15, 22, 59, tzinfo=timezone.utc)))
        self.assertFalse(gate.in_live_window(
            datetime(2026, 1, 15, 12, 59, tzinfo=timezone.utc)),
            "tepat sebelum 13:00 juga di luar")

    def test_overnight_window(self):
        """Jendela yang melewati tengah malam harus bekerja."""
        gate = SafetyGate(_cfg(live_window_utc=(22, 4)), env=_env(), state_path=_state_path())
        self.assertTrue(gate.in_live_window(
            datetime(2026, 1, 15, 23, 0, tzinfo=timezone.utc)))
        self.assertTrue(gate.in_live_window(
            datetime(2026, 1, 15, 2, 0, tzinfo=timezone.utc)))
        self.assertFalse(gate.in_live_window(
            datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)))


def _req(size=0.001, price=100_000.0, is_buy=True, is_close=False):
    return OrderRequest(
        symbol="BTC/USDC:USDC", is_buy=is_buy, size=size, price=price,
        is_close=is_close, reduce_only=is_close,
    )


class TestNotionalLimits(unittest.TestCase):
    """
    Batas nominal.

    Angka diambil dari config, bukan ditulis ulang, supaya test tetap benar
    kalau seseorang mengubah batasnya.
    """

    def setUp(self):
        self.cfg = _cfg()
        self.gate = SafetyGate(self.cfg, env=_env(), state_path=_state_path())

    def test_under_all_limits_is_allowed(self):
        ok, blockers = self.gate.can_send(
            _req(), free_collateral=10_000.0, current_exposure=0.0,
            symbol_exposure=0.0, now=_midday(), cloid="abc-123",
        )
        self.assertTrue(ok, "order normal harus lolos: " + str(blockers))
        self.assertEqual(blockers, [])


    def test_order_over_max_notional_blocked(self):
        big = self.cfg.max_order_notional * 2
        ok, blockers = self.gate.can_send(
            _req(size=big / 100_000.0), free_collateral=1_000_000.0,
            current_exposure=0.0, symbol_exposure=0.0, now=_midday(),
        )
        self.assertFalse(ok)
        self.assertIn(Blocker.ORDER_TOO_LARGE, blockers)

    def test_position_limit_accumulates(self):
        """Eksposur per simbol harus MENJUMAH dengan yang sudah ada."""
        existing = self.cfg.max_position_notional * 0.9
        ok, blockers = self.gate.can_send(
            _req(size=self.cfg.max_order_notional / 100_000.0),
            free_collateral=1_000_000.0, current_exposure=0.0,
            symbol_exposure=existing, now=_midday(),
        )
        self.assertFalse(ok)
        self.assertIn(Blocker.POSITION_TOO_LARGE, blockers)

    def test_total_exposure_limit_blocked(self):
        ok, blockers = self.gate.can_send(
            _req(), free_collateral=1_000_000.0,
            current_exposure=self.cfg.max_total_notional,
            symbol_exposure=0.0, now=_midday(),
        )
        self.assertFalse(ok)
        self.assertIn(Blocker.EXPOSURE_TOO_LARGE, blockers)

    def test_minimum_collateral_respected(self):
        """Collateral yang dibiarkan harus selalu >= minimum."""
        free = self.cfg.min_free_collateral + 1.0
        ok, blockers = self.gate.can_send(
            _req(size=5.0, price=100_000.0), free_collateral=free,
            current_exposure=0.0, symbol_exposure=0.0, now=_midday(),
        )
        self.assertFalse(ok)
        self.assertIn(Blocker.COLLATERAL_TOO_LOW, blockers)


class TestInvalidInputNeverSends(unittest.TestCase):
    """Order dengan bentuk salah harus berhenti sebelum menyentuh bursa."""

    def setUp(self):
        self.gate = SafetyGate(_cfg(), env=_env(), state_path=_state_path())

    def test_zero_size_blocked(self):
        ok, blockers = self.gate.can_send(
            _req(size=0.0), free_collateral=1e6, now=_midday())
        self.assertFalse(ok)
        self.assertIn(Blocker.INVALID_INPUT, blockers)

    def test_negative_size_blocked(self):
        """Quantity negatif membuat margin negatif — dan itu membuat
        validasi risiko menganggapnya "cukup"."""
        ok, blockers = self.gate.can_send(
            _req(size=-1.0), free_collateral=1e6, now=_midday())
        self.assertFalse(ok)
        self.assertIn(Blocker.INVALID_INPUT, blockers)

    def test_zero_price_blocked(self):
        ok, blockers = self.gate.can_send(
            _req(price=0.0), free_collateral=1e6, now=_midday())
        self.assertFalse(ok)
        self.assertIn(Blocker.INVALID_INPUT, blockers)

    def test_negative_price_blocked(self):
        ok, blockers = self.gate.can_send(
            _req(price=-100_000.0), free_collateral=1e6, now=_midday())
        self.assertFalse(ok)
        self.assertIn(Blocker.INVALID_INPUT, blockers)


class TestDailyCounters(unittest.TestCase):
    def setUp(self):
        self.cfg = _cfg()
        self.gate = SafetyGate(self.cfg, env=_env(), state_path=_state_path())
        # `master_blockers` selalu menjalankan rollover lebih dulu, dan
        # penghitung yang masih kosong akan mereset dirinya ke nol. Jadi
        # penanda tanggalnya harus diisi dulu, kalau tidak test ini diam-diam
        # menguji rollover dan bukan batas yang dimaksud.
        self.gate.counters.day_utc = "2026-01-15"

    def test_daily_order_limit(self):
        self.gate.counters.orders_sent = self.cfg.max_daily_orders
        self.assertIn(
            Blocker.DAILY_ORDER_LIMIT, self.gate.master_blockers(_midday())
        )

    def test_daily_loss_limit(self):
        self.gate.counters.realized_pnl = -self.cfg.max_daily_loss - 1
        self.assertIn(
            Blocker.DAILY_LOSS_LIMIT, self.gate.master_blockers(_midday())
        )

    def test_loss_limit_uses_actual_sign(self):
        """Batas ini untuk RUGI. PnL positif tidak boleh memicu."""
        self.gate.counters.realized_pnl = +1000.0
        self.assertNotIn(
            Blocker.DAILY_LOSS_LIMIT, self.gate.master_blockers(_midday())
        )

    def test_just_below_limit_still_allowed(self):
        """Tepat di bawah batas harus LOLOS — limit bukan pemblokir permanen."""
        self.gate.counters.orders_sent = self.cfg.max_daily_orders - 1
        self.gate.counters.realized_pnl = -self.cfg.max_daily_loss + 1.0
        blockers = self.gate.master_blockers(_midday())
        self.assertNotIn(Blocker.DAILY_ORDER_LIMIT, blockers)
        self.assertNotIn(Blocker.DAILY_LOSS_LIMIT, blockers)

    def test_daily_loss_exactly_at_limit_blocks(self):
        """
        TEPAT di limit harus memblokir.

        Test lama hanya mencoba `max - 1` dan `max + 1`, jadi BUKAN
        batasnya yang diuji tapi "di dekat" batasnya. Mengubah
        `<=` menjadi `<` di `master_blockers` membuat test lama tetap
        hijau sementara limit melebar satu sen — dan itu yang ditemukan
        mutation testing (`master_blockers__mutmut_57`).

        Breaker harus menyala tepat saat kerugian menyentuh batas. Melewat
        satu titik berarti satu order lagi terkirim setelah batas tercapai.
        """
        self.gate.counters.realized_pnl = -abs(self.cfg.max_daily_loss)
        self.assertIn(
            Blocker.DAILY_LOSS_LIMIT, self.gate.master_blockers(_midday()),
            "tepat di batas, daily-loss breaker harus menyala",
        )

    def test_daily_loss_one_cent_above_limit_blocks(self):
        """Sedikit di atas batas juga memblokir — arahnya benar."""
        self.gate.counters.realized_pnl = -abs(self.cfg.max_daily_loss) - 0.01
        self.assertIn(
            Blocker.DAILY_LOSS_LIMIT, self.gate.master_blockers(_midday()),
        )

    def test_daily_loss_one_cent_below_limit_allows(self):
        """Satu sen di bawah batas harus LOLOS."""
        self.gate.counters.realized_pnl = -abs(self.cfg.max_daily_loss) + 0.01
        self.assertNotIn(
            Blocker.DAILY_LOSS_LIMIT, self.gate.master_blockers(_midday()),
            "satu sen di bawah batas tidak boleh memblokir",
        )

    def test_consecutive_errors_exactly_at_limit_blocks(self):
        """
        TEPAT `max_consecutive_errors` harus memblokir.

        `master_blockers` memakai `>=`. Mengubahnya jadi `>` membuat
        error streak ke-3 lolos — dan order ketiga yang gagal tidak lagi
        menghentikan trading. Itu mutan
        `master_blockers__mutmut_61`, dan test lama tidak pernah menyetel
        counter ke angka persis batasnya.
        """
        self.gate.counters.consecutive_errors = self.cfg.max_consecutive_errors
        self.assertIn(
            Blocker.TOO_MANY_ERRORS, self.gate.master_blockers(_midday()),
            "tepat di batas error, gerbang harus menutup",
        )

    def test_consecutive_errors_one_below_limit_allows(self):
        """Satu error di bawah batas masih boleh order."""
        self.gate.counters.consecutive_errors = self.cfg.max_consecutive_errors - 1
        self.assertNotIn(
            Blocker.TOO_MANY_ERRORS, self.gate.master_blockers(_midday()),
        )

    def test_consecutive_errors_one_above_limit_blocks(self):
        """Di atas batas juga memblokir."""
        self.gate.counters.consecutive_errors = self.cfg.max_consecutive_errors + 1
        self.assertIn(
            Blocker.TOO_MANY_ERRORS, self.gate.master_blockers(_midday()),
        )

    def test_daily_order_count_exactly_at_limit_blocks(self):
        """Pola yang sama untuk batas jumlah order harian."""
        self.gate.counters.orders_sent = self.cfg.max_daily_orders
        self.assertIn(
            Blocker.DAILY_ORDER_LIMIT, self.gate.master_blockers(_midday()),
        )

    def test_rollover_resets_counters_but_keeps_error_streak(self):
        """
        Angka harian direset tengah malam, tapi error beruntun TIDAK.

        Membiarkan error beruntun melewati tengah malam menahan bot sampai
        operator intervient — dan itu jauh lebih murah daripada bot yang
        mengulang kesalahan yang sama terus-menerus.
        """
        self.gate.counters.day_utc = "2020-01-01"
        self.gate.counters.orders_sent = 999
        self.gate.counters.realized_pnl = -500.0
        self.gate.counters.consecutive_errors = 2

        self.gate.counters.rollover_if_needed(_midday())

        self.assertEqual(self.gate.counters.orders_sent, 0)
        self.assertEqual(self.gate.counters.realized_pnl, 0.0)
        self.assertEqual(
            self.gate.counters.consecutive_errors, 2,
            "error beruntun tidak boleh direset oleh rollover",
        )


class TestCounterPersistence(unittest.IsolatedAsyncioTestCase):
    """
    Penghitung harian harus bertahan melewati restart.

    Dulu murni in-memory, dan komentarnya mengklaim itu "lebih
    konservatif". Justru sebaliknya: bot yang crash lalu start ulang akan
    mulai dari nol, dan batas harian jadi tidak ada selama hari itu.
    """

    def setUp(self):
        import tempfile

        self.dir = pathlib.Path(tempfile.mkdtemp())
        self.path = self.dir / "counters.json"

    def _gate(self, **overrides):
        # Counter di-set dengan jam SEKARANG, jadi test harus memakai
        # jam yang sama. Kalau test memakai tanggal tetap (2026-01-15),
        # rollover menganggapnya hari baru dan me-reset counter --
        # persis bug yang bagian ini sedang menutup.
        return SafetyGate(_cfg(**overrides), env=_env(),
                          state_path=self.path)

    def _now(self):
        from datetime import datetime, timezone
        return datetime.now(timezone.utc)

    def test_counters_survive_restart(self):
        gate = self._gate(max_daily_orders=10)
        gate.record_order_sent()
        gate.record_order_sent()
        gate.record_realized_pnl(-5.0)

        restarted = self._gate(max_daily_orders=10)
        self.assertEqual(restarted.counters.orders_sent, 2,
                         "order hari ini harus bertahan setelah restart")
        self.assertAlmostEqual(restarted.counters.realized_pnl, -5.0)

    def test_daily_limit_survives_restart(self):
        """
        Batas harian harus tetap berlaku setelah restart.

        Ini inti dari bug aslinya: satu restart cukup untuk melewati
        plafon berulang kali kalau counter tidak bertahan.
        """
        gate = self._gate(max_daily_orders=2)
        gate.record_order_sent()
        gate.record_order_sent()

        restarted = self._gate(max_daily_orders=2)
        req = _req()
        ok, blockers = restarted.can_send(
            req, free_collateral=10_000.0, current_exposure=0.0,
            symbol_exposure=0.0, now=self._now(), cloid="x",
        )
        self.assertFalse(ok, "batas harian harus tetap berlaku")
        self.assertIn(Blocker.DAILY_ORDER_LIMIT, blockers)

    def test_daily_loss_survives_restart(self):
        gate = self._gate(max_daily_loss=50.0)
        gate.record_realized_pnl(-60.0)

        restarted = self._gate(max_daily_loss=50.0)
        ok, blockers = restarted.can_send(
            _req(), free_collateral=10_000.0, current_exposure=0.0,
            symbol_exposure=0.0, now=self._now(), cloid="x",
        )
        self.assertFalse(ok, "batas rugi harian harus tetap berlaku")
        self.assertIn(Blocker.DAILY_LOSS_LIMIT, blockers)

    def test_corrupt_file_blocks_orders(self):
        """
        File counter rusak = order ditolak.

        Angka batas hari ini tidak diketahui, dan batas yang tidak diketahui
        berarti tidak ada batas.
        """
        self.path.write_text("{ ini bukan JSON valid", encoding="utf-8")
        gate = self._gate()

        self.assertFalse(gate.counters.readable)
        ok, blockers = gate.can_send(
            _req(), free_collateral=10_000.0, current_exposure=0.0,
            symbol_exposure=0.0, now=self._now(), cloid="x",
        )
        self.assertFalse(ok, "file rusak harus menolak order")
        self.assertIn(Blocker.COUNTER_STATE_UNREADABLE, blockers)

    def test_missing_file_is_new_day(self):
        """File tidak ada = hari pertama, bukan kondisi error."""
        gate = self._gate()
        self.assertTrue(gate.counters.readable)
        self.assertEqual(gate.counters.orders_sent, 0)

    def test_error_streak_survives_restart(self):
        """Error beruntun tidak boleh hilang karena restart."""
        gate = self._gate(max_consecutive_errors=3)
        gate.record_error()
        gate.record_error()

        restarted = self._gate(max_consecutive_errors=3)
        self.assertEqual(restarted.counters.consecutive_errors, 2)


class TestSecretRedaction(unittest.TestCase):
    """Private key tidak boleh muncul di log mana pun."""

    def test_key_is_redacted_from_text(self):
        key = "0xSECRETSECRETSECRET1234"
        gate = SafetyGate(_cfg(), env=_env(HYPERLIQUID_PRIVATE_KEY=key))
        msg = "gagal kirim dengan key " + key + " di payload"
        self.assertNotIn(key, gate.redact(msg))
        self.assertIn("<REDACTED>", gate.redact(msg))

    def test_redaction_survives_empty_input(self):
        gate = SafetyGate(_cfg(), env=_env(), state_path=_state_path())
        self.assertEqual(gate.redact(""), "")
        self.assertIsNone(gate.redact(None))

    def test_very_short_key_left_alone(self):
        """
        Key yang sangat pendek tidak di-redact.

        Mengganti teks pendek dengan placeholder hanya menghasilkan
        pesan yang lebih membingungkan, bukan lebih aman.
        """
        gate = SafetyGate(_cfg(), env=_env(HYPERLIQUID_PRIVATE_KEY="abc"))
        self.assertEqual(
            gate.redact("abc adalah teks biasa"), "abc adalah teks biasa"
        )


class TestCloidRequired(unittest.TestCase):
    """
    Order yang membuka posisi wajib punya client order id.

    Tanpa cloid, ketika respons hilang karena timeout tidak ada cara untuk
    menanyakan "apakah order saya sudah masuk?". Retry lalu menggandakan
    posisi -- dan di live itu berarti dua kali risiko yang tidak terlihat.
    """

    def setUp(self):
        self.gate = SafetyGate(_cfg(), env=_env(), state_path=_state_path())

    def test_opening_without_cloid_blocked(self):
        ok, blockers = self.gate.can_send(
            _req(), free_collateral=10_000.0, current_exposure=0.0,
            symbol_exposure=0.0, now=_midday(), cloid=None,
        )
        self.assertFalse(ok, "order opening tanpa cloid harus ditolak")
        self.assertIn(Blocker.MISSING_CLOID, blockers)

    def test_opening_with_cloid_allowed(self):
        ok, blockers = self.gate.can_send(
            _req(), free_collateral=10_000.0, current_exposure=0.0,
            symbol_exposure=0.0, now=_midday(), cloid="abc-123",
        )
        self.assertTrue(ok, "order dengan cloid harus lolos: " + str(blockers))

    def test_closing_without_cloid_allowed(self):
        """
        Order MENUTUP tidak butuh cloid.

        Ia mengurangi posisi yang sudah tercatat, jadi retry-nya tidak
        menambah risiko baru. Menolaknya hanya menambah langkah tanpa
        perlindungan.
        """
        req = _req(is_close=True)
        ok, blockers = self.gate.can_send(
            req, free_collateral=10_000.0, current_exposure=0.0,
            symbol_exposure=0.0, now=_midday(), cloid=None,
        )
        self.assertTrue(ok, "order closing tidak perlu cloid: " + str(blockers))

    def test_empty_cloid_string_blocked(self):
        """String kosong bukan cloid yang valid."""
        ok, blockers = self.gate.can_send(
            _req(), free_collateral=10_000.0, current_exposure=0.0,
            symbol_exposure=0.0, now=_midday(), cloid="",
        )
        self.assertFalse(ok)
        self.assertIn(Blocker.MISSING_CLOID, blockers)


class TestLeverageBounds(unittest.TestCase):
    """Leverage di luar batas harus tertangkap sebelum bursa menolak."""

    def setUp(self):
        self.gate = SafetyGate(_cfg(), env=_env(), state_path=_state_path())

    def _check(self, leverage):
        return self.gate.can_send(
            _req(), free_collateral=10_000.0, current_exposure=0.0,
            symbol_exposure=0.0, now=_midday(), cloid="x", leverage=leverage,
        )

    def test_valid_leverage_allowed(self):
        ok, blockers = self._check(5)
        self.assertTrue(ok, "leverage wajar harus lolos: " + str(blockers))

    def test_zero_leverage_blocked(self):
        ok, blockers = self._check(0)
        self.assertFalse(ok)
        self.assertIn(Blocker.LEVERAGE_TOO_HIGH, blockers)

    def test_negative_leverage_blocked(self):
        ok, blockers = self._check(-3)
        self.assertFalse(ok)
        self.assertIn(Blocker.LEVERAGE_TOO_HIGH, blockers)

    def test_leverage_over_max_blocked(self):
        ok, blockers = self._check(SafetyGate(_cfg(), env=_env(), state_path=_state_path()).cfg.max_leverage + 1)
        self.assertFalse(ok)
        self.assertIn(Blocker.LEVERAGE_TOO_HIGH, blockers)



