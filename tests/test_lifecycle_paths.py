"""
tests/test_lifecycle_paths.py — Cakupan jalur yang TIDAK tersentuh unit test.

Test di file lain memverifikasi komponen satu per satu. File ini memverifikasi
INVARIANT AKHIR yang muncul dari interaksinya — dan terutama jalur yang
hampir tidak pernah terjadi di pasar nyata, sehingga bug di sana bisa
bertahun baru ketemu:

  * race: dua pemanggil mengklaim posisi yang sama
  * batch close banyak posisi
  * likuidasi
  * fee: apakah `realized_pnl` benar-benar NET dari KEDUA sisi

Bagian fee adalah yang paling penting. `realized_pnl` pernah hanya
mengurangi fee PENUTUPAN, padahal fee PEMBUKAAN juga dipotong dari saldo.
Akibatnya win rate menghitung posisi rugi-bersih sebagai "menang", dan
circuit breaker daily-loss UNDER-estimasi kerugian. Saldo sendiri tetap
benar — yang salah adalah lapis atribusi.
"""

import asyncio
import os
import unittest

import database.db as db_module
from core.event_bus import EventBus
from database.db import Database, close_db
from database.repository import Repository
from trading.position_manager import PositionManager
from trading.risk_manager import RiskManager


class LifecycleBase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Nama file memuat PID, jadi dua proses yang menjalankan suite
        # bersamaan tidak saling menimpa atau membaca baris milik satu
        # sama. Path yang sama untuk semua proses adalah sumber kegagalan
        # paling sering: `close_position` mengembalikan None (klaim gagal),
        # `realized_pnl` tertinggal NULL, dan baris lama bertahan melewati
        # batch-close dan prune — semuanya karena satu proses menulis
        # DB yang sedang dibaca proses lain.
        self.path = "data_store/test_lifecycle_%d.db" % os.getpid()
        for suffix in ("", "-wal", "-shm"):
            p = self.path + suffix
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass

        self.db = Database(db_path=self.path)
        await self.db.connect()
        db_module._db = self.db

        self.repo = Repository(self.db)
        await self.repo.init_account(10000.0)

        self.bus = EventBus()
        self.rm = RiskManager()
        self.pm = PositionManager(self.bus, self.rm)

    async def asyncTearDown(self):
        await close_db()
        for suffix in ("", "-wal", "-shm"):
            p = self.path + suffix
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass

    async def _open(self, side="LONG", price=50000.0, qty=0.2, lev=10,
                    sl=None, tp=None, symbol="BTC/USDT:USDT"):
        return await self.pm.open_position(
            symbol=symbol, side=side, entry_price=price, quantity=qty,
            leverage=lev,
            stop_loss=price * 0.9 if sl is None else sl,
            take_profit=price * 1.1 if tp is None else tp,
        )


class TestFeeAccounting(LifecycleBase):
    """`realized_pnl` harus NET dari fee buka DAN fee tutup."""

    async def test_realized_pnl_is_net_of_both_fees(self):
        pos_id = await self._open(price=50000.0, qty=0.2)
        self.assertIsNotNone(pos_id)

        # Tutup di harga PASAR yang sama. PnL gross bukan nol: `close_position`
        # membebankan spread di sisi exit, jadi harga jualnya di bawah mid.
        await self.pm.close_position(pos_id, 50000.0, "MANUAL")

        pos = (await self.repo.get_all_positions())[0]
        trades = await self.repo.get_trades_by_position(pos_id)
        open_fee = sum(t["fee"] for t in trades if t["trade_type"] == "OPEN")
        close_fee = sum(t["fee"] for t in trades if t["trade_type"] == "CLOSE")
        self.assertGreater(open_fee, 0, "fee pembukaan harus tercatat")
        self.assertGreater(close_fee, 0, "fee penutupan harus tercatat")

        # Biaya outbreak exit. Versi test ini mengettakkan
        # `-(open_fee + close_fee)` persis, yang meng-asumsikan posisi datar
        # tidak membayar spread. Sekarang expectation dihitung dari model
        # biaya yang sama dengan yang dipakai production, jadi assertion ini
        # meng-contract "tidak ada biaya yang hilang" alih-alih membekukan
        # sebuah nominal.
        from trading.fill_cost import close_fill_price

        _, close_meta = close_fill_price(
            "BTC/USDT:USDT", "LONG", 50000.0, reason="TEST",
        )
        exit_cost = close_meta["total_cost_pct"] * 50000.0 * 0.2

        self.assertAlmostEqual(
            pos["realized_pnl"],
            -(open_fee + close_fee) - exit_cost,
            places=4,
            msg="realized_pnl harus mengurangi KEDUA fee dan biaya outbreak",
        )

    async def test_win_rate_not_inflated_by_round_trip_fee(self):
        """
        Posisi yang tipis profitnya harus tetap TERHITUNG RUGI kalau fee
        roundtrip lebih besar dari geraknya.

        Fee taker 0,05% per sisi = 0,10% roundtrip. Gerak 0,08% < 0,10%,
        jadi hasil bersihnya harus negatif walau PnL brutto positif.
        """
        pos_id = await self._open(price=50000.0, qty=0.2)
        await self.pm.close_position(pos_id, 50040.0, "MANUAL")

        stats = await self.repo.get_trade_stats()
        pos = (await self.repo.get_all_positions())[0]
        trades = await self.repo.get_trades_by_position(pos_id)
        total_fee = sum(t["fee"] for t in trades)

        self.assertLess(
            pos["realized_pnl"], 0,
            "0.08% gerak harus kalah dari 0.10% fee roundtrip",
        )
        self.assertGreater(total_fee, abs(pos["realized_pnl"]) + 0.03,
                           "fee harus jadi komponen dominan di sini")
        self.assertEqual(
            stats["winning_trades"], 0,
            "posisi rugi setelah fee tidak boleh dihitung sebagai menang",
        )
        self.assertEqual(stats["losing_trades"], 1)

    async def test_daily_pnl_reflects_opening_fee(self):
        """
        `get_daily_realized_pnl` adalah sumber angka circuit breaker, jadi
        harus sama dengan PnL bersih — bukan PnL yang belum dipotong fee buka.
        """
        pos_id = await self._open(price=50000.0, qty=0.2)
        await self.pm.close_position(pos_id, 50040.0, "MANUAL")

        daily = await self.repo.get_daily_realized_pnl()
        pos = (await self.repo.get_all_positions())[0]
        self.assertAlmostEqual(
            daily, pos["realized_pnl"], places=6,
            msg="PnL harian harus sama dengan realized_pnl posisi itu",
        )
        self.assertLess(daily, 0, "setelah fee, hasil hari ini harus negatif")


class TestRaceAndDoubleClaim(LifecycleBase):
    """Dua pemanggil berebut satu posisi."""

    async def test_second_close_returns_none(self):
        """Penutupan kedua atas posisi yang sama harus gagal, bukan dobel bayar."""
        pos_id = await self._open(price=50000.0, qty=0.2)

        first = await self.pm.close_position(pos_id, 50000.0, "MANUAL")
        self.assertIsNotNone(first, "penutupan pertama harus berhasil")

        second = await self.pm.close_position(pos_id, 50000.0, "MANUAL")
        self.assertIsNone(
            second, "penutupan kedua harus ditolak (posisi sudah tertutup)",
        )

    async def test_concurrent_close_returns_margin_once(self):
        """
        Dua penutupan paralel: margin hanya boleh dikembalikan SEKALI.

        Kalau rusak, saldo naik dua kali dan equity meledak. Ini persis yang
        diklaim komentar "klaim-tunggal" di `close_position`.
        """
        pos_id = await self._open(price=50000.0, qty=0.2)
        balance_before = (await self.repo.get_account())["balance"]

        results = await asyncio.gather(
            self.pm.close_position(pos_id, 50000.0, "MANUAL"),
            self.pm.close_position(pos_id, 50000.0, "MANUAL"),
            return_exceptions=True,
        )
        ok = [r for r in results if isinstance(r, dict)]
        self.assertEqual(
            len(ok), 1,
            "tepat satu dari dua penutupan paralel boleh berhasil",
        )

        balance_after = (await self.repo.get_account())["balance"]
        pos = (await self.repo.get_all_positions())[0]
        margin = float(pos["margin"])
        realized = float(pos["realized_pnl"])

        # `balance_before` sudah dipotong margin DAN fee opening.
        #
        # `realized_pnl` sengaja net DUA fee karena itu metrik KUALITAS
        # trade: win rate dan circuit breaker daily-loss butuh angka
        # profit bersih sebenarnya. Tapi fee opening sudah keluar dari
        # saldo saat opening, jadi saat menutup yang kembali hanya margin
        # dan PnL. Karena itu `open_fee` harus ditambah balik di sini --
        # kalau tidak, test akan salah menyalahkan kode, dan yang lebih
        # berbahaya: membiarkan bug double-hitung masuk lagi.
        open_fee = 0.0
        for t in await self.repo.get_trades_by_position(pos_id):
            if (t.get("trade_type") or "").upper() == "OPEN":
                open_fee = float(t.get("fee") or 0.0)
                break

        self.assertAlmostEqual(
            balance_after, balance_before + margin + realized + open_fee,
            places=4,
            msg="margin + PnL dikembalikan tepat satu kali, "
                "tanpa memotong fee opening lagi",
        )

        # Posisi datar harus RUGI, dan ruginya harus setotal biaya yang
        # benar-benar dibayar: dua fee plus biaya outbreak di sisi exit.
        #
        # Versi lama menulis `realized == -2 * open_fee`, yang asumsikan fee
        # tutup identik dengan fee buka dan spread gratis. Keduanya salah
        # setelah model biaya masuk: `close_fill_price` mengembalikan harga
        # di bawah mid, jadi fee tutup dihitung dari notional yang lebih
        # kecil, dan ada satu biaya outbreak yang belum pernah dihitung.
        # Yang dikunci di sini adalah arah dan besarnya kedua komponen
        # yang di-assert: tidak ada biaya yang hilang, dan tidak ada yang
        # terhitung dua kali.
        from trading.fill_cost import close_fill_price

        close_fee = 0.0
        for t in await self.repo.get_trades_by_position(pos_id):
            if (t.get("trade_type") or "").upper() == "CLOSE":
                close_fee = float(t.get("fee") or 0.0)
                break
        self.assertGreater(close_fee, 0.0, "fee penutupan harus tercatat")

        _, close_meta = close_fill_price(
            "BTC/USDT:USDT", "LONG", 50000.0, reason="TEST",
        )
        exit_cost = close_meta["total_cost_pct"] * 50000.0 * 0.2

        self.assertLess(realized, 0.0, "posisi datar harus rugi")
        self.assertAlmostEqual(
            realized, -(open_fee + close_fee) - exit_cost, places=4,
            msg="posisi datar harus rugi sebesar dua fee + biaya outbreak exit",
        )


class TestOpeningFeeNotDoubleCharged(LifecycleBase):
    """
    Regression: fee opening pernah dipotong DUA KALI.

    `open_position` memotong fee dari saldo, lalu `net_pnl` juga
    memotong `open_fee` dan nilai itulah yang dikreditkan. Akibatnya
    setiap posisi kehilangan fee opening sekali lagi -- pada 330
    posisi paper itu sekitar 891 USDT hilang tanpa jejak.

    NAMA KELAS INI DULUNYA `TestFeeAccounting`, sama persis dengan kelas di
    atas. Python membolehkan itu, dan `unittest discover` tidak pernah
    memperingatkan: kelas kedua menimpa yang pertama, jadi seluruh test di
    kelas pertama - termasuk `test_realized_pnl_is_net_of_both_fees` - TIDAK
    PERNAH DIJALANKAN. Angka 570 `def test_` vs 566 kasus yang terkumpul
    berasal dari sini. Nama itu sudah dipakai untuk regression fee opening
    yang berbeda, jadi kelas ini dipanggil apa adanya sekarang.
    """

    async def test_open_fee_is_not_charged_twice(self):
        pos_id = await self._open(price=50000.0, qty=0.2)

        fee_open = 0.0
        for t in await self.repo.get_trades_by_position(pos_id):
            if (t.get("trade_type") or "").upper() == "OPEN":
                fee_open = float(t.get("fee") or 0.0)
                break
        self.assertGreater(fee_open, 0.0, "test ini butuh fee opening nyata")

        balance_at_open = (await self.repo.get_account())["balance"]

        # Tutup di harga yang sama -> PnL kotor nol, jadi seluruh
        # pergerakan saldo harus persis kebalikan dari saat opening.
        await self.pm.close_position(pos_id, 50000.0, "MANUAL")

        balance_final = (await self.repo.get_account())["balance"]
        pos = (await self.repo.get_all_positions())[0]
        margin = float(pos["margin"])
        realized = float(pos["realized_pnl"])

        # Gerak saldo sejak posisi dibuka = margin kembali + PnL.
        #
        # `open_fee` ikut ditambah karena `realized_pnl` dihitung net DUA
        # fee, sementara fee opening sudah keluar dari saldo saat opening.
        # Tanpa tambahan ini, test akan salah menyalahkan kode -- dan yang
        # lebih berbahaya, membiarkan bug double-hitung kembali masuk.
        open_fee = 0.0
        for t in await self.repo.get_trades_by_position(pos_id):
            if (t.get("trade_type") or "").upper() == "OPEN":
                open_fee = float(t.get("fee") or 0.0)
                break

        self.assertAlmostEqual(
            balance_final - balance_at_open,
            margin + realized + open_fee, places=4,
            msg="fee opening harus sudah terpakai saat opening, "
                "tidak boleh dipotong lagi saat menutup")

    async def test_flat_trade_loses_exactly_two_fees_plus_spread(self):
        """
        Buka lalu tutup di harga pasar yang SAMA: hasil bersih harus persis
        -fee opening - fee tutup - biaya outbreak di sisi exit.

        Versi lama mengettakkan angka absolut `-9.0`, yang meng-asumsikan
        posisi datar hanya membayar dua fee dan spread-nya gratis. Assertion
        itu melanggar konvensi suite yang berlaku di file lain - "test yang
        mengarang threshold-nya sendiri tetap hijau saat config berubah" -
        karena `-9.0` membekukan qty=0.2, price=50000 dan tarif taker ke
        dalam assertion.

        Sekarang expectation dihitung dari komponennya, jadi test ini
        bertahan saat konfigurasi biaya berubah. Yang di-assert CONTRACT-nya
        bukan NOMINAL-nya: tidak ada biaya yang hilang, dan tidak ada yang
        terhitung dua kali.
        """
        from trading.fill_cost import close_fill_price

        pos_id = await self._open(price=50000.0, qty=0.2)
        await self.pm.close_position(pos_id, 50000.0, "MANUAL")

        pos = (await self.repo.get_all_positions())[0]
        realized = float(pos["realized_pnl"])

        # Fees, dibaca dari tabel `trades` - bukan dihitung ulang, karena
        # tarif taker bisa berubah di tengah jalan.
        open_fee = 0.0
        close_fee = 0.0
        for t in await self.repo.get_trades_by_position(pos_id):
            ttype = (t.get("trade_type") or "").upper()
            if ttype == "OPEN":
                open_fee = float(t.get("fee") or 0.0)
            elif ttype == "CLOSE":
                close_fee = float(t.get("fee") or 0.0)

        self.assertGreater(open_fee, 0.0, "fee opening harus tercatat")
        self.assertGreater(close_fee, 0.0, "fee penutupan harus tercatat")

        # Biaya outbreak exit, dari model yang sama dengan yang dipakai
        # `close_position`. Sisi LONG menutup dengan SELL, jadi harga jual
        # lebih RENDAH dari pasar dan biaya menambah kerugian.
        _, close_meta = close_fill_price(
            "BTC/USDT:USDT", "LONG", 50000.0, reason="TEST",
        )
        exit_cost = close_meta["total_cost_pct"] * 50000.0 * 0.2
        self.assertGreater(exit_cost, 0.0)

        self.assertAlmostEqual(realized, -open_fee - close_fee - exit_cost, places=2)

    async def test_realized_pnl_is_net_of_both_fees(self):
        """`realized_pnl` yang tersimpan harus benar-benar bersih."""
        pos_id = await self._open(price=50000.0, qty=0.2)
        await self.pm.update_positions({"BTC/USDT:USDT": 51000.0})
        await self.pm.close_position(pos_id, 51000.0, "TP_HIT")

        pos = (await self.repo.get_all_positions())[0]
        gross = (51000.0 - 50000.0) * 0.2
        self.assertLess(float(pos["realized_pnl"]), gross,
                        "PnL tersimpan harus lebih kecil dari PnL kotor")


class TestTriggerPaths(LifecycleBase):
    """SL / TP / likuidasi."""

    async def test_sl_hit_closes_with_loss(self):
        await self._open(price=50000.0, qty=0.1, sl=48000.0, tp=54000.0)
        await self.pm.update_positions({"BTC/USDT:USDT": 47500.0})
        pos = (await self.repo.get_all_positions())[0]
        self.assertEqual(pos["status"], "CLOSED")
        self.assertIn("SL_HIT", pos["close_reason"])
        self.assertLess(pos["realized_pnl"], 0)

    async def test_tp_hit_closes_with_profit(self):
        await self._open(price=50000.0, qty=0.1, sl=48000.0, tp=54000.0)
        await self.pm.update_positions({"BTC/USDT:USDT": 54500.0})
        pos = (await self.repo.get_all_positions())[0]
        self.assertEqual(pos["status"], "CLOSED")
        self.assertIn("TP_HIT", pos["close_reason"])
        self.assertGreater(pos["realized_pnl"], 0)

    async def test_price_between_sl_and_tp_keeps_position_open(self):
        """Harga di antara SL dan TP tidak boleh menutup posisi."""
        await self._open(price=50000.0, qty=0.1, sl=48000.0, tp=54000.0)
        await self.pm.update_positions({"BTC/USDT:USDT": 51000.0})
        self.assertEqual(
            len(await self.repo.get_open_positions()), 1,
            "harga di tengah range tidak boleh menutup posisi",
        )

    async def test_short_sl_is_above_entry_and_triggers_upward(self):
        """SL SHORT harus di ATAS entry, dan terpicu saat harga naik."""
        await self._open(side="SHORT", price=50000.0, qty=0.1,
                         sl=52000.0, tp=46000.0)
        await self.pm.update_positions({"BTC/USDT:USDT": 52500.0})
        pos = (await self.repo.get_all_positions())[0]
        self.assertEqual(pos["status"], "CLOSED")
        self.assertIn("SL_HIT", pos["close_reason"])
        self.assertLess(pos["realized_pnl"], 0, "SL untuk SHORT harus rugi")

    async def test_liquidation_closes_position(self):
        await self._open(price=50000.0, qty=0.1, lev=20,
                         sl=40000.0, tp=60000.0)
        pos = (await self.repo.get_all_positions())[0]
        liq = float(pos["liquidation_price"])
        # Harga jatuh ke likuidasi, dan DALAM area stop loss.
        await self.pm.update_positions({"BTC/USDT:USDT": liq * 0.999})
        pos = (await self.repo.get_all_positions())[0]
        self.assertEqual(pos["status"], "LIQUIDATED", pos["close_reason"])
        self.assertLess(pos["realized_pnl"], 0)


class TestBatchPaths(LifecycleBase):
    """
    Penutupan banyak posisi sekaligus.

    API-nya `batch_close_positions(position_ids, prices, reason)` yang
    mengembalikan JUMLAH yang berhasil — bukan daftar id. Test ini memakai
    signature itu apa adanya; mengarang `batch_close_all` hanya akan
    menguji imajinasi.
    """

    async def test_batch_close_counts_only_real_closes(self):
        ids = []
        for i in range(4):
            pid = await self._open(price=50000.0 + i, qty=0.1,
                                   symbol="SYM{}/USDT:USDT".format(i))
            ids.append(pid)

        prices = {"SYM{}/USDT:USDT".format(i): 50010.0 for i in range(4)}
        closed = await self.pm.batch_close_positions(ids, prices, "MANUAL")

        self.assertEqual(closed, 4, "keempat posisi harus tertutup")
        self.assertEqual(len(await self.repo.get_open_positions()), 0,
                         "tidak boleh ada yang tertinggal")

    async def test_batch_close_skips_unknown_ids(self):
        """
        Id yang tidak dikenal harus DILEWATI, bukan dihitung sebagai berhasil.

        Kalau `closed` menambah 1 untuk id yang tidak ada, pemanggil percaya
        eksposur sudah bersih padahal tidak — bug "penutupan parsial
        dilaporkan sukses" dalam bentuk lain.
        """
        real = await self._open(price=50000.0, qty=0.1)
        closed = await self.pm.batch_close_positions(
            [real, 999999], {"BTC/USDT:USDT": 50010.0}, "MANUAL")

        self.assertEqual(closed, 1, "hanya posisi nyata yang boleh dihitung")
        self.assertEqual(len(await self.repo.get_open_positions()), 0)

    async def test_batch_close_skips_symbol_without_price(self):
        """Posisi yang simbolnya tidak ada di `prices` harus dilewati."""
        pid = await self._open(price=50000.0, qty=0.1)
        closed = await self.pm.batch_close_positions(pid and [pid], {}, "MANUAL")
        self.assertEqual(closed, 0, "tanpa harga tidak boleh ditutup")
        self.assertEqual(len(await self.repo.get_open_positions()), 1)

    async def test_batch_close_empty_list_is_zero(self):
        """Daftar kosong harus mengembalikan 0, bukan melempar."""
        closed = await self.pm.batch_close_positions([], {}, "MANUAL")
        self.assertEqual(closed, 0)


class TestNativeKernelParity(unittest.TestCase):
    """
    Paritas C++ vs Python pada data yang TIDAK SERU.

    Test yang sudah ada memakai book normal. Yang berbahaya justru book
    aneh — karena di sinilah dua implementasi biasanya berbeda: jumlah level
    tidak sama, ada sisi kosong, atau ada level bernilai 0.
    """

    @classmethod
    def setUpClass(cls):
        # Import disimpan sebagai atribut kelas, bukan cuma variabel lokal:
        # `import` di dalam fungsi hanya mengikat nama DI FUNGSI ITU, sehingga
        # method lain akan mendapat `NameError`. Ini kesalahan yang mudah
        # terulang dan tidak akan ketahuan kalau modulnya tidak ter-build.
        try:
            import cpp_microstructure
        except Exception as exc:  # noqa: BLE001
            raise unittest.SkipTest("modul native belum dibangun: " + str(exc))
        cls.cpp = cpp_microstructure

    def _pair(self, bids, asks):
        from core.microstructure import PythonKernel

        native = self.cpp.MicrostructureKernel(4)
        native.ingest_l2("SYM", bids, asks)
        py = PythonKernel()
        py.ingest_l2("SYM", bids, asks)
        return native, py

    def _assert_same(self, bids, asks, depth=5, label=""):
        native, py = self._pair(bids, asks)
        n_ofi, n_spread = native.order_flow_imbalance("SYM", depth)
        p_ofi, p_spread = py.order_flow_imbalance("SYM", depth)
        self.assertAlmostEqual(n_ofi, p_ofi, delta=1e-7, msg="OFI " + label)
        self.assertAlmostEqual(n_spread, p_spread, delta=1e-7,
                               msg="spread " + label)
        self.assertAlmostEqual(
            native.depth_imbalance("SYM", depth),
            py.depth_imbalance("SYM", depth),
            delta=1e-7, msg="depth " + label)

    def test_unequal_level_counts(self):
        """5 bid tapi 2 ask — jumlah level tidak sama."""
        self._assert_same(
            [(100.0, 1.0), (99.9, 2.0), (99.8, 3.0), (99.7, 4.0), (99.6, 5.0)],
            [(100.2, 1.0), (100.3, 2.0)],
            label="unequal")

    def test_bid_side_empty(self):
        self._assert_same([], [(100.2, 1.0)], label="bid kosong")

    def test_ask_side_empty(self):
        self._assert_same([(100.0, 1.0)], [], label="ask kosong")

    def test_zero_sized_levels(self):
        """Size 0 boleh ada di book dan harus ditangani sama di kedua sisi."""
        self._assert_same([(100.0, 0.0), (99.9, 5.0)],
                          [(100.2, 0.0), (100.3, 5.0)],
                          label="size nol")

    def test_all_zero_volume(self):
        self._assert_same([(100.0, 0.0)], [(100.2, 0.0)], label="semua nol")

    def test_deeper_than_max_levels(self):
        """Lebih dari 32 level harus di-truncate TANPA lewat komputasi."""
        bids = [(100.0 - i * 0.01, 1.0 + i) for i in range(50)]
        asks = [(100.5 + i * 0.01, 1.0 + i) for i in range(50)]
        self._assert_same(bids, asks, depth=32, label=">dari max level")

    def test_depth_zero_is_safe(self):
        """`depth=0` tidak boleh crash di salah satu sisi."""
        self._assert_same([(100.0, 1.0)], [(100.2, 1.0)], depth=0, label="depth 0")

    def test_depth_larger_than_book(self):
        self._assert_same([(100.0, 1.0)], [(100.2, 1.0)], depth=99,
                          label="depth > book")

    def test_crossed_book(self):
        """Best bid > best ask (book rusak) — tidak boleh crash."""
        self._assert_same([(101.0, 1.0)], [(100.0, 1.0)], label="crossed")

    def test_tiny_and_huge_prices(self):
        self._assert_same([(1e-8, 1.0)], [(2e-8, 1.0)], label="sangat kecil")
        self._assert_same([(1e9, 1.0)], [(1.1e9, 1.0)], label="sangat besar")


class TestAgentLogPruning(LifecycleBase):
    """
    `agent_logs` pernah tumbuh tanpa batas — tidak ada `DELETE` yang
    menyentuhnya, padahal loop decision menulis ~214 baris/menit.
    """

    async def test_prune_keeps_newest_and_bounds_size(self):
        from database.models import AgentLog

        for i in range(60):
            await self.repo.insert_agent_log(
                AgentLog(agent_name="t", action="CYCLE",
                         reasoning="baris {}".format(i)))
        total = await self.db.fetchone("SELECT COUNT(*) c FROM agent_logs")
        self.assertEqual(total["c"], 60)

        await self.repo.prune_agent_logs(keep=10)
        left = await self.db.fetchone("SELECT COUNT(*) c FROM agent_logs")
        self.assertEqual(left["c"], 10, "harus tersisa tepat `keep` baris")

    async def test_prune_keeps_the_newest_rows(self):
        """Yang dipangkas harus yang LAMA; jejak terbaru tidak boleh hilang."""
        from database.models import AgentLog

        for i in range(30):
            await self.repo.insert_agent_log(
                AgentLog(agent_name="t", action="CYCLE",
                         reasoning="urutan-{}".format(i)))

        await self.repo.prune_agent_logs(keep=5)
        rows = await self.db.fetchall(
            "SELECT reasoning FROM agent_logs ORDER BY id DESC")
        reasons = [r["reasoning"] for r in rows]
        self.assertEqual(len(reasons), 5)
        for want in ("urutan-29", "urutan-28", "urutan-27"):
            self.assertIn(want, reasons,
                          "baris terbaru harus dipertahankan")
        self.assertNotIn("urutan-0", reasons,
                         "baris terlama harus dipangkas")

    async def test_prune_is_idempotent(self):
        """Menjalankan prune dua kali tidak boleh merusak atau menambah."""
        from database.models import AgentLog

        for i in range(20):
            await self.repo.insert_agent_log(
                AgentLog(agent_name="t", action="CYCLE", reasoning=str(i)))
        await self.repo.prune_agent_logs(keep=7)
        await self.repo.prune_agent_logs(keep=7)
        left = await self.db.fetchone("SELECT COUNT(*) c FROM agent_logs")
        self.assertEqual(left["c"], 7)

    async def test_prune_keeps_all_when_under_limit(self):
        from database.models import AgentLog

        for i in range(5):
            await self.repo.insert_agent_log(
                AgentLog(agent_name="t", action="CYCLE", reasoning=str(i)))
        await self.repo.prune_agent_logs(keep=100)
        left = await self.db.fetchone("SELECT COUNT(*) c FROM agent_logs")
        self.assertEqual(left["c"], 5, "di bawah batas tidak ada yang dipangkas")


class TestEngineInputValidation(LifecycleBase):
    """
    Validasi input di `_execute_open`.

    Jalur `else` di sana memakai `quantity = order.quantity` dan
    `margin = (quantity * price) / order.leverage` tanpa memeriksa apa pun
    soal nilainya. Order yang keliru karena bug di agen mana pun — atau
    karena data yang salah baca — akan lolos sampai ke `open_position`.
    """

    async def _engine(self):
        from trading.paper_engine import PaperTradingEngine

        self.pm._repo = self.repo
        eng = PaperTradingEngine(self.bus)
        await eng.initialize()
        eng.position_manager = self.pm
        return eng

    def _order(self, quantity, leverage=10):
        from trading.models import Order, TradeAction

        return Order(
            symbol="BTC/USDT:USDT", action=TradeAction.OPEN_LONG,
            quantity=quantity, leverage=leverage,
            stop_loss=45000.0, take_profit=70000.0,
        )

    async def test_zero_quantity_is_rejected(self):
        eng = await self._engine()
        eng.update_price("BTC/USDT:USDT", 50000.0)
        before = len(await self.repo.get_open_positions())

        res = await eng.execute_order(self._order(0.0))

        self.assertFalse(res["success"], "quantity 0 harus ditolak: " + res["message"])
        self.assertEqual(len(await self.repo.get_open_positions()), before,
                         "tidak boleh ada posisi yang tercipta")

    async def test_negative_quantity_is_rejected(self):
        """
        Quantity negatif adalah yang paling berbahaya: `margin` jadi negatif,
        sehingga `required_cash` negatif dan uang mengalir ke arah terbalik —
        `validate_trade` tidak akan pernah menangkapnya karena margin-nya
        terlihat "cukup".
        """
        eng = await self._engine()
        eng.update_price("BTC/USDT:USDT", 50000.0)
        res = await eng.execute_order(self._order(-1.0))

        self.assertFalse(res["success"],
                         "quantity negatif harus ditolak: " + res["message"])
        self.assertEqual(len(await self.repo.get_open_positions()), 0)

    async def test_negative_quantity_does_not_increase_balance(self):
        """Efek samping paling merusak: saldo bertambah dari qty negatif."""
        eng = await self._engine()
        eng.update_price("BTC/USDT:USDT", 50000.0)
        before = (await self.repo.get_account())["balance"]

        await eng.execute_order(self._order(-1.0))

        after = (await self.repo.get_account())["balance"]
        self.assertLessEqual(
            after, before,
            "saldo tidak boleh bertambah karena order dengan quantity negatif",
        )

    async def test_zero_leverage_is_rejected(self):
        """Leverage 0 membuat pembagian dengan nol saat menghitung margin."""
        eng = await self._engine()
        eng.update_price("BTC/USDT:USDT", 50000.0)
        res = await eng.execute_order(self._order(0.1, leverage=0))
        self.assertFalse(res["success"],
                         "leverage 0 harus ditolak: " + res["message"])

    async def test_negative_price_is_rejected(self):
        """Harga 0/negatif harus ditolak, bukan sampai ke sizing."""
        eng = await self._engine()
        eng.update_price("BTC/USDT:USDT", 0.0)
        res = await eng.execute_order(self._order(0.1))
        self.assertFalse(res["success"],
                         "harga 0 harus ditolak: " + res["message"])



class TestRiskGatePaths(unittest.TestCase):
    """
    Setiap cabang `validate_trade` — gerbang sebelum uang benar-benar keluar.

    Fokusnya TITIK BATAS (`>=` vs `>`) dan kasus yang sering terlewat:
    `initial_balance` nol, `peak_balance` nol, dan `equity` yang diberikan.
    """

    def setUp(self):
        self.rm = RiskManager()

    def test_all_clear_allows(self):
        r = self.rm.validate_trade(10000.0, 100.0, 0, daily_pnl=0.0)
        self.assertTrue(r["allowed"], r["reasons"])

    def test_margin_boundary_is_strictly_greater_than(self):
        """
        Aturan margin: `margin_required > balance * 0.9` yang DITOLAK.

        Tepat 90% tidak *melebihi* 90%, jadi lolos. Itu pilihan yang disengaja
        (batas 90% berarti "jangan sampai 100%"), dan test ini mengunci
        batasnya supaya perubahan `>` menjadi `>=` ketahuan.
        """
        at_limit = self.rm.validate_trade(1000.0, 900.0, 0)
        self.assertTrue(
            at_limit["allowed"],
            "tepat 90% tidak melebihi batas, jadi harus lolos: "
            + str(at_limit["reasons"]),
        )
        over = self.rm.validate_trade(1000.0, 900.01, 0)
        self.assertFalse(over["allowed"], "di atas 90% harus ditolak")
        self.assertTrue(any("Margin" in x for x in over["reasons"]))

    def test_margin_just_below_90_percent_allows(self):
        r = self.rm.validate_trade(1000.0, 899.0, 0)
        self.assertTrue(r["allowed"], r["reasons"])

    def test_position_count_at_limit_is_rejected(self):
        limit = self.rm.config.max_open_positions
        r = self.rm.validate_trade(10000.0, 10.0, limit)
        self.assertFalse(r["allowed"])
        self.assertTrue(any("Posisi terbuka" in x for x in r["reasons"]))

    def test_position_count_below_limit_allows(self):
        limit = self.rm.config.max_open_positions
        r = self.rm.validate_trade(10000.0, 10.0, limit - 1)
        self.assertTrue(r["allowed"], r["reasons"])

    def test_daily_loss_breaker_uses_initial_balance_not_cash(self):
        """
        Penyebut HARUS `initial_balance`, dan ambangnya dibaca dari config.

        Kalau penyebutnya saldo kas, ambang ikut turun begitu margin
        terkunci — sehingga pelonggaran terjadi justru saat paling rugi.

        Ambang diambil dari `rm.config.max_daily_loss`, bukan ditulis di sini:
        test yang mengarang angka batasnya akan tetap hijau saat config
        berubah, dan itu justru kelemahan yang paling mahal untuk pengaman
        uang.
        """
        rm = RiskManager()
        initial = 10000.0
        rm._initial_balance = initial
        limit = rm.config.max_daily_loss

        # Sedikit di bawah batas -> belum tripped, meski kas kecil.
        just_under = initial * limit * 0.9
        r = rm.validate_trade(200.0, 10.0, 0, daily_pnl=-just_under)
        self.assertTrue(
            r["allowed"],
            "{:.1%} dari modal awal belum melewati {:0.0%}".format(
                just_under / initial, limit),
        )

        # Jauh di atas batas -> harus tripped.
        over = initial * limit * 1.2
        r = rm.validate_trade(200.0, 10.0, 0, daily_pnl=-over)
        self.assertFalse(
            r["allowed"],
            "{:.1%} dari modal awal harus melewati {:0.0%}".format(
                over / initial, limit),
        )
        self.assertTrue(any("Kerugian harian" in x for x in r["reasons"]))

    def test_daily_loss_exactly_at_limit_trips_breaker(self):
        """
        TEPAT di batas harus memicu breaker.

        Test di atas memakai 0.9x dan 1.2x dari batas — jadi keduanya
        menguji "jauh dari batas", bukan batasnya. Mengubah
        `loss_fraction >= max_daily_loss` menjadi `>` di
        `validate_trade` (mutan `validate_trade__mutmut_22`) membuat
        kedua test itu tetap hijau sementara breaker melebar satu titik.

        Angka batas diambil dari `rm.config.max_daily_loss`, bukan ditulis
        di sini — test yang mengarang angkanya akan hijau saat config
        berubah, dan itu kelemahan yang paling mahal untuk pengaman uang.
        """
        rm = RiskManager()
        initial = 10000.0
        rm._initial_balance = initial
        limit = rm.config.max_daily_loss

        exactly = initial * limit
        r = rm.validate_trade(200.0, 10.0, 0, daily_pnl=-exactly)
        self.assertFalse(
            r["allowed"],
            "tepat di {:0.0%} dari modal awal, breaker harus tripped".format(limit),
        )
        self.assertTrue(any("Kerugian harian" in x for x in r["reasons"]))

        # Dan satu sen di bawahnya harus LOLOS, supaya test ini tidak
        # lulus karena batasnya melebar ke arah lain.
        just_under = exactly - initial * 0.0001
        r = rm.validate_trade(200.0, 10.0, 0, daily_pnl=-just_under)
        self.assertTrue(
            r["allowed"],
            "satu sen di bawah batas tidak boleh tripped",
        )

    def test_profit_daily_pnl_never_trips_breaker(self):
        r = self.rm.validate_trade(10000.0, 10.0, 0, daily_pnl=5000.0)
        self.assertTrue(r["allowed"], "PnL positif tidak boleh memicu breaker")

    def test_drawdown_uses_equity_when_given(self):
        """
        Drawdown harus diukur dari EQUITY, bukan kas.

        Kas bisa turun hanya karena margin terkunci — itu bukan kerugian.
        """
        r = self.rm.validate_trade(1000.0, 10.0, 0,
                                   peak_balance=10000.0, equity=9500.0)
        self.assertTrue(r["allowed"], r["reasons"])
        r = self.rm.validate_trade(1000.0, 10.0, 0,
                                   peak_balance=10000.0, equity=7000.0)
        self.assertFalse(r["allowed"], "drawdown 30% harus melewati batas 20%")
        self.assertTrue(any("Drawdown" in x for x in r["reasons"]))

    def test_zero_peak_balance_does_not_divide_by_zero(self):
        """`peak_balance = 0` harus dilewati, bukan crash."""
        r = self.rm.validate_trade(10000.0, 10.0, 0, peak_balance=0)
        self.assertTrue(r["allowed"], r["reasons"])

    def test_zero_balance_does_not_crash(self):
        r = self.rm.validate_trade(0.0, 100.0, 0, daily_pnl=0.0)
        self.assertFalse(r["allowed"])

    def test_all_reasons_collected_not_just_first(self):
        """Semua alasan harus terkumpul supaya pesan ke user lengkap."""
        limit = self.rm.config.max_open_positions
        r = self.rm.validate_trade(100.0, 500.0, limit, daily_pnl=-99999.0,
                                   peak_balance=10000.0, equity=100.0)
        self.assertFalse(r["allowed"])
        self.assertGreaterEqual(
            len(r["reasons"]), 3,
            "margin + jumlah posisi + drawdown + daily loss harus semua muncul",
        )


class TestSizingAndPnlPaths(unittest.TestCase):
    """Perhitungan sizing dan PnL di titik ekstrem."""

    def setUp(self):
        self.rm = RiskManager()

    def test_pnl_long_up_and_down(self):
        up = self.rm.calculate_pnl("LONG", 100.0, 110.0, 2.0)
        down = self.rm.calculate_pnl("LONG", 100.0, 90.0, 2.0)
        self.assertAlmostEqual(up["pnl"], 20.0, places=2)
        self.assertAlmostEqual(down["pnl"], -20.0, places=2)

    def test_pnl_short_is_mirror_of_long(self):
        """SHORT harus kebalikan LONG persis."""
        s = self.rm.calculate_pnl("SHORT", 100.0, 110.0, 2.0)
        lg = self.rm.calculate_pnl("LONG", 100.0, 110.0, 2.0)
        self.assertAlmostEqual(s["pnl"], -lg["pnl"], places=2)

    def test_pnl_zero_entry_does_not_divide_by_zero(self):
        """
        Entry 0 harus dihitung TANPA crash dan tanpa persentase tak bermakna.

        PnL linier sendiri tetap 100 — secara matematika itu benar (keluar dari
        harga 0 ke 100 dengan qty 1). Yang diuji di sini adalah bagian yang
        rawan: `pnl_pct` = pnl / position_value, dan `position_value` = 0
        di sini. Harus jadi 0, bukan `ZeroDivisionError`.
        """
        r = self.rm.calculate_pnl("LONG", 0.0, 100.0, 1.0)
        self.assertEqual(r["pnl"], 100.0, "PnL linier tetap benar")
        self.assertEqual(r["pnl_pct"], 0.0,
                         "persentase di atas posisi bernilai 0 harus 0")
        self.assertEqual(r["roe_pct"], 0.0)

    def test_fee_scales_with_notional(self):
        """
        Fee harus proporsional terhadap notional.

        Yang diuji adalah proporsionalitas, bukan akurasi floating point.
        places=8 membuat test ini gagal pada representasi float yang
        secara matematis sudah benar.
        matematis.
        """
        f1 = self.rm.calculate_fee(1.0, 100.0, "TAKER")
        f2 = self.rm.calculate_fee(1.0, 200.0, "TAKER")
        self.assertAlmostEqual(f2, 2 * f1, places=10,
                               msg="fee harus linear dengan notional")

    def test_taker_fee_never_below_maker(self):
        maker = self.rm.calculate_fee(1.0, 100.0, "MAKER")
        taker = self.rm.calculate_fee(1.0, 100.0, "TAKER")
        self.assertLessEqual(maker, taker,
                             "maker tidak boleh lebih mahal dari taker")

    def test_zero_quantity_fee_is_zero(self):
        self.assertEqual(self.rm.calculate_fee(0.0, 100.0, "TAKER"), 0.0)

    def test_sizing_margin_matches_notional_over_leverage(self):
        s = self.rm.calculate_scalp_position_size(10000.0, 50000.0, 10)
        if s.get("quantity"):
            expected_margin = (s["quantity"] * 50000.0) / 10
            self.assertAlmostEqual(s["margin"], expected_margin, places=2)
