"""
Item (e) Fase 1: breakeven live.

KEADAAN SEBENARNYA
------------------
`ExecutionAgent._protect_breakeven` (agents/execution_agent.py:194)
menghitung level breakeven lalu menulisnya ke SQLite:

    await repo.update_position_sl_tp(pos["id"], stop_loss=be_sl)

Yang TIDAK terjadi: trigger di BURSA tidak disentuh. `LivePosition`
menyimpan `sl_order_id` -- order trigger yang benar-benar melindungi
posisi -- dan angka itu tidak pernah berubah.

Akibatnya di mode live:

    posisi profit 0.4%
    DB menyimpan SL = breakeven
    bursa masih menyimpan SL lama di level yang lebih jauh

Yang tampil di dashboard dan DB terlihat sudah aman. Yang benar-benar
melindungi posisi di bursa tidak bergerak. Kalau proses mati, posisi
itu tetap memakai SL lama -- persis risiko yang-breakeven seharusnya
cegah.

INI BUKAN "UI SEMU" YANG SEDANG BISA DIPERBAIKI MURAH
Move trigger di bursa berarti cancel + replace, jadi dua order tambahan
setiap kali ambang terlampaui. Ambang ini dilewati berulang selama
posisi terbuka (dicek tiap 0,3 detik), jadi tanpa sifat idempoten
menjadi hujan order. Itu alasan fitur ini dinonaktifkan, bukan
diperbaiki sekarang.

KEPUTUSAN
Fitur breakeven DINONAKTIFKAN untuk mode live. Di paper tetap jalan,
karena di paper tidak ada dana nyata dan tidak ada order di bursa yang
perlu dicabut.

Yang diuji file ini: jalur live tidak bisa memanggilnya sama sekali.
Bukan "dipanggil tapi tidak berpengaruh" -- tidak boleh dipanggil.
"""
import asyncio
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from database.models import Position
from repro_helpers import market_price


class _Scalp:
    """Nilai ambang dari konfigurasi; tidak ada logika di sini."""

    breakeven_trigger_pct = 0.004
    breakeven_offset_pct = 0.001
    min_profit_pct = 0.006
    min_hold_seconds = 0
    max_hold_seconds = 10 ** 9
    fast_tp_pct = 0.006


class _Repo:
    """Repository sungguhan di SQLite sementara."""

    def __init__(self, db):
        self.db = db
        self.sl_updates = []

    async def get_open_positions(self, symbol=None, mode=None):
        """
        Repository sungguhan dipanggil dengan filter mode.

        `mode` DIJAGA di sini, bukan diabaikan: kalau test helper
        mengabaikan filter yang justru sedang diuji, test-nya lulus
        karena tidak terjadi apa-apa -- bukan karena produksi benar.
        """
        sql = "SELECT * FROM positions WHERE status = 'OPEN'"
        params = ()
        if mode:
            sql += " AND mode = ?"
            params = (mode,)
        sql += " ORDER BY id"
        rows = await self.db.fetchall(sql, params)
        return [dict(r) for r in rows]

    async def update_position_sl_tp(self, pos_id, stop_loss=None,
                                    take_profit=None):
        # Mode dicatat bersama perubahannya: test harus bisa membedakan
        # "live disentuh" dari "paper disentuh", bukan cuma melihat
        # bahwa ada update yang terjadi.
        row = await self.db.fetchall(
            "SELECT mode FROM positions WHERE id = ?", (pos_id,))
        mode = row[0]["mode"] if row else "?"
        self.sl_updates.append((pos_id, stop_loss, mode))
        await self.db.execute(
            "UPDATE positions SET stop_loss = ? WHERE id = ?",
            (stop_loss, pos_id))

    async def get_trade_stats(self, mode=None):
        return {}

    async def get_daily_realized_pnl(self):
        return 0.0


class _EngineDouble:
    """Engine yang mencatat apa pun yang dicoba bot terhadap bursa."""

    def __init__(self):
        self.submitted = []
        self._last_prices = {}

    async def position_manager_close(self, pos_id, price, reason):
        return None


def _make_agent(repo, live_mode):
    """
    ExecutionAgent sungguhan, hanya `_get_repo` dan scalp yang diganti.

    `_protect_breakeven` produksi yang diuji. Tidak ada logic yang
    dilewati; yang diganti hanya sumber data (SQLite di tempfile
   instead of DB global) dan konfigurasi ambang.
    """
    from agents.execution_agent import ExecutionAgent

    agent = ExecutionAgent.__new__(ExecutionAgent)
    agent._repo = repo
    agent.scalp = _Scalp()
    agent.engine = _EngineDouble()
    agent.logger = _NullLogger()
    agent._live_mode = live_mode
    return agent


class _NullLogger:
    def debug(self, *a, **k):
        pass

    def info(self, *a, **k):
        pass

    def warning(self, *a, **k):
        pass

    def error(self, *a, **k):
        pass


class BreakevenLiveTestBase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import tempfile

        from database.db import Database

        self._dir = tempfile.mkdtemp()
        self.db = Database(str(pathlib.Path(self._dir) / "be.db"))
        await self.db.connect()
        self.repo = _Repo(self.db)
        await self.repo.db.execute(
            "INSERT INTO positions (symbol, side, entry_price, quantity, "
            "leverage, margin, liquidation_price, stop_loss, take_profit, "
            "status, mode) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("BTC/USDT:USDT", "LONG", 85000.0, 0.001, 5, 17.0, 0.0,
             83000.0, 87000.0, "OPEN", "live"))
        await self.db.commit()

        # Harga 0.5% di atas entry: melewati breakeven_trigger_pct (0.4%)
        # sehingga versi lama AKAN menulis SL baru ke DB.
        #
        # DIBUNGKUS `market_price` karena `market_store` itu sington tanpa
        # API reset. Versi lama memanggil `set_price()` langsung dan
        # meninggalkan median 85425 di state global -- itu membuat
        # `tests/test_bugfixes.py` gagal di runner `unittest` dengan
        # "harga 60000 menyimpang 29.763% dari median 85425".
        self._price_ctx = market_price(
            "BTC/USDT:USDT", 85000.0 * 1.005)
        self._price_ctx.__enter__()

    async def _seed_paper(self):
        await self.db.execute(
            "INSERT INTO positions (symbol, side, entry_price, quantity, "
            "leverage, margin, liquidation_price, stop_loss, take_profit, "
            "status, mode) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("ETH/USDT:USDT", "LONG", 2000.0, 0.05, 5, 20.0, 0.0,
             1900.0, 2100.0, "OPEN", "paper"))
        await self.db.commit()

        self._eth_price_ctx = market_price(
            "ETH/USDT:USDT", 2000.0 * 1.005)
        self._eth_price_ctx.__enter__()

    async def asyncTearDown(self):
        for ctx in (getattr(self, "_eth_price_ctx", None),
                    getattr(self, "_price_ctx", None)):
            if ctx is not None:
                ctx.__exit__(None, None, None)
        await self.db.close()


class TestBreakevenDoesNotTouchLivePositions(BreakevenLiveTestBase):
    """
    Posisi live tidak boleh digeser breakeven hanya di database.

    SETIAP test di kelas ini punya posisi LIVE **dan** PAPER sekaligus.

    Itu wajib, bukan seleccionar: kalau hanya ada posisi live, jalur
    produksi keluar lebih dulu di `if not paper_rows: return` -- dan
    loop-nya tidak pernah jalan. Test lalu lulus karena guard tak
    sengaja, bukan karena filter mode bekerja. Bukti cabutan perbaikan
    di commit ini menunjukkan hal itu: membalik filter live
    tetap hijau kalau loop tidak pernah dieksekusi.
    """

    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self._seed_paper()

    async def test_live_position_sl_is_not_rewritten(self):
        """
        SL di DB harus tetap di level yang dipasang di bursa.

        Kalau angka di DB berubah sementara order trigger di bursa tetap
        di level lama, DB dan bursa berbeda -- dan yang ditampilkan ke
        operator bukan yang melindungi posisi.
        """
        agent = _make_agent(self.repo, live_mode=True)
        await agent._protect_breakeven()

        rows = await self.db.fetchall(
            "SELECT stop_loss FROM positions WHERE mode = 'live'")
        self.assertAlmostEqual(
            float(rows[0]["stop_loss"]), 83000.0, places=6,
            msg="SL posisi live diubah jadi %r. Trigger di bursa tidak ikut "
                "bergerak -- DB dan bursa sekarang berbeda, dan yang tampil "
                "bukan yang melindungi posisi." % rows[0]["stop_loss"],
        )
        live_writes = [u for u in self.repo.sl_updates if u[2] == "live"]
        self.assertEqual(
            live_writes, [],
            "breakeven menulis SL untuk posisi live: %r" % live_writes,
        )
        # Posisi paper di kelas ini boleh disentuh -- kalau tidak, test
        # ini lulus karena loop tidak pernah jalan.
        self.assertTrue(
            [u for u in self.repo.sl_updates if u[2] == "paper"],
            "loop breakeven tidak pernah jalan untuk posisi paper; test "
            "lulus karena guard, bukan karena filter mode bekerja",
        )

    async def test_nothing_is_submitted_to_the_exchange(self):
        """
        Tidak boleh ada order apa pun yang dikirim ke bursa.

        Breakeven yang "dikerjakan" berarti cancel + replace. Kalau
        tidak ada implementasinya, jalur ini harus diam total --
        bukan mengirim setengah-setengah.
        """
        agent = _make_agent(self.repo, live_mode=True)
        await agent._protect_breakeven()

        self.assertEqual(
            agent.engine.submitted, [],
            "jalur live mengirim order dari breakeven tanpa memasang "
            "trigger penggantinya: %r" % agent.engine.submitted,
        )

    async def test_operator_is_told_why_it_did_nothing(self):
        """
        Diam-diam tidak bergerak adalah UI semu.

        Kalau fitur dimatikan, operator harus diberi tahu --
        kalau tidak,SL di DB yang tidak bergerak terlihat seperti
        proteksi yang bekerja.
        """
        import logging

        agent = _make_agent(self.repo, live_mode=True)
        records = []

        class _Capture(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())

        handler = _Capture()
        logging.getLogger("trading_bot").addHandler(handler)
        try:
            await agent._protect_breakeven()
        finally:
            logging.getLogger("trading_bot").removeHandler(handler)

        joined = " ".join(records).lower()
        self.assertIn(
            "breakeven", joined,
            "tidak ada pesan apa pun tentang breakeven; operator akan "
            "mengira SL di DB masih bergerak",
        )
        self.assertIn(
            "live", joined,
            "pesan harus menyebut bahwa ini mode live; dapat: %r" % joined,
        )


class TestPaperPositionsStillGetBreakeven(BreakevenLiveTestBase):
    """
    Di paper tidak ada dana nyata dan tidak ada trigger di bursa.

    Fiturnya tidak berbahaya di sana, jadi harus tetap jalan --
    kalau tidak, hasil riset paper jadi tidak sebanding dengan yang
    dijalankan.
    """

    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self._seed_paper()

    async def test_paper_position_sl_is_moved_to_breakeven(self):
        """
        Breakeven harus tetap mengubah SL untuk posisi paper.

        Ini yang membuat paper trading tetap berguna sebagai proksi.
        """
        agent = _make_agent(self.repo, live_mode=False)

        await agent._protect_breakeven()

        rows = await self.db.fetchall(
            "SELECT stop_loss FROM positions WHERE mode = 'paper'")
        self.assertGreater(
            float(rows[0]["stop_loss"]), 1900.0,
            "SL posisi paper tidak digeser ke breakeven; dapat: %r"
            % rows[0]["stop_loss"],
        )

    async def test_paper_breakeven_is_idempotent(self):
        """
        Dipanggil tiap 0,3 detik: ambang yang sama tidak boleh
        menulis ulang SL berkali-kali.
        """
        agent = _make_agent(self.repo, live_mode=False)

        await agent._protect_breakeven()
        paper_writes = [u for u in self.repo.sl_updates if u[2] == "paper"]
        self.assertTrue(paper_writes, "SL paper tidak digeser sama sekali")
        first = paper_writes[-1][1]
        count_after_first = len(paper_writes)
        await agent._protect_breakeven()

        after = [u for u in self.repo.sl_updates if u[2] == "paper"]
        self.assertEqual(
            len(after), count_after_first,
            "breakeven menulis ulang SL yang sama berulang kali dalam "
            "siklus yang sama: %r" % after,
        )
        self.assertAlmostEqual(after[-1][1], first, places=9)


if __name__ == "__main__":
    unittest.main()