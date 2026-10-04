"""
tests/test_live_startup_gate.py — Boot live gagal, proses berhenti.

MASALAH YANG INI TUTUP
---------------------
Sejak `c224b58` `Info` dibangun LAZY. `LiveExchange(...)` tidak lagi
menyentuh jaringan, jadi konstruksinya SELALU berhasil — bahkan di mesin
tanpa koneksi, dan bahkan dengan private key yang tidak pernah men-sign
apa pun ke bursa sungguhan.

Bot yang lolos ke loop berarti order attempt tanpa knowing-the-state.
Jadi pemeriksaan harus terjadi DI START, sebelum `LiveEngine` ada.

MENGAPA FILE INI ADA TERPISAH DARI `test_live_preflight.py`
------------------------------------------------------------
`test_live_preflight.py` menguji `preflight()` sebagai unit. File ini
menguji hal yang berbeda: bahwa `_build_live_executor()` benar-benar
MENJALANKAN pemeriksa itu, dan benar-benar berhenti.

Dua test lama (`test_source_calls_preflight_before_engine` dan
`test_preflight_failure_propagates`) membaca TEKS `run.py` dengan
`inspect.getsource`. Keduanya hijau tanpa menjalankan apa pun, jadi:

  * Keduanya tetap hijau kalau `preflight()` dipanggil di jalur kode mati.
  * Keduanya tetap hijau kalau pemanggilnya dibungkus `try/except` yang
    menelan exception — persis perilaku salah yang harus dicegah.
  * Keduanya tidak bisa membedakan "memanggil preflight" dari "memanggil
    preflight lalu meneruskan apa pun yang terjadi".

Keduanya dihapus. Digantikan test yang menjalankan kodenya.

TIRUAN DI BATAS SDK, BUKAN STUB PREFLIGHT
-----------------------------------------
Yang dipalsukan adalah `hyperliquid.info.Info` — batas SDK — supaya tidak
ada jaringan. Yang diuji, yaitu `preflight()`, `_build_live_executor()`,
dan `_cli()`, adalah kode produksi yang asli dan tidak pernah disentuh.

Kalau `preflight()` ikut dipalsukan, file ini hanya membuktikan bahwa
stub-nya memanggil stub lain.
"""
import asyncio
import io
import os
import pathlib
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from core.config import LiveConfig
from trading.live.client import PreflightError
from trading.live.safety import SafetyGate

# Dummy 32 byte. Tidak pernah men-sign apa pun: test ini berhenti di
# preflight, sebelum ada order yang mungkin dikirim.
DUMMY_KEY = "0x" + "ab" * 32
KEY_ENV = "HYPERLIQUID_PRIVATE_KEY"


class _DeadInfoFactory:
    """
    Pengganti `hyperliquid.info.Info` yang selalu gagal saat `meta()`.

    Dipasang di batas SDK, bukan di `preflight`. Kalau yang dipalsukan
    `preflight`-nya sendiri, file ini tidak membuktikan apa pun.
    """

    def __init__(self, *args, **kwargs):
        pass

    def meta(self):
        raise ConnectionError("koneksi ditolak (tiruan test)")

    def user_state(self, user, dex=""):
        raise AssertionError("preflight tidak boleh query posisi")


def _isolated_gate_patch():
    """
    Arahkan `SafetyGate` ke file state di TEMP.

    `_build_live_executor()` membangun `SafetyGate(live_cfg)` dengan
    `state_path` default, yaitu `data_store/live_counters.json`. Tanpa ini,
    test ini menulis ke file kill switch produksi — dan saat mutan
    "preflight dihapus" diuji, engine sungguhan sempat jalan sehingga
    `engaged: true` tersimpan ke sana.

    Kill switch yang ditulis test adalah kill switch palsu: operator
    membuka bot lalu menemukannya aktif tanpa sebab.

    `trading.live.safety.SafetyGate` yang dipatch, bukan yang diimpor di
    sini — supaya yang diganti adalah yang benar-benar dipanggil `run.py`.
    """
    state = pathlib.Path(tempfile.mkdtemp()) / "counters.json"

    def _make(*args, **kwargs):
        kwargs.setdefault("env", {})
        kwargs["state_path"] = state
        return SafetyGate(*args, **kwargs)

    return patch("trading.live.safety.SafetyGate", side_effect=_make)


def _app(mode="testnet"):
    """
    `TradingBotApp` tanpa `__init__` berat.

    `__init__` membangun price feed, sentiment analyzer, dan lima agen —
    semuanya tidak relevan untuk pertanyaan "apakah boot berhenti", dan
    semuanya menambah dependensi yang bisa pecah karena alasan lain.
    """
    from run import TradingBotApp

    app = TradingBotApp.__new__(TradingBotApp)
    app.mode = mode
    app.event_bus = None
    app._live_task = None
    app._live_engine_obj = None
    return app


class TestStartupStopsWhenExchangeUnreachable(unittest.TestCase):
    """Bursa mati = boot gagal, dan tidak ada engine yang dibangun."""

    def _run_with_dead_exchange(self, app=None):
        import run

        app = app or _app()
        env = {KEY_ENV: DUMMY_KEY}
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("HYPERLIQUID_ACCOUNT_ADDRESS", None)
            with patch("hyperliquid.info.Info", _DeadInfoFactory), \
                 _isolated_gate_patch():
                asyncio.run(
                    run.TradingBotApp._build_live_executor(app)
                )
        return app

    def test_raises_preflight_error(self):
        with self.assertRaises(PreflightError) as ctx:
            self._run_with_dead_exchange()
        self.assertEqual(ctx.exception.code,
                         PreflightError.EXCHANGE_UNREACHABLE)

    def test_engine_is_never_constructed(self):
        """
        `LiveEngine` tidak boleh dibuat kalau preflight gagal.

        `LiveEngine.__init__` menyalakan timer internal dan membaca config.
        Kalau ia tetap dibuat, ada timer hidup tanpa supervising, dan kill
        switch bisa aktif tanpa yang mengawasi.
        """
        import run
        import trading.live.engine as engine_mod

        built = []

        class _Tripwire:
            def __init__(self, *a, **kw):
                built.append(kw)

        with patch.dict(os.environ, {KEY_ENV: DUMMY_KEY}, clear=False):
            os.environ.pop("HYPERLIQUID_ACCOUNT_ADDRESS", None)
            with patch("hyperliquid.info.Info", _DeadInfoFactory), \
                 patch.object(engine_mod, "LiveEngine", _Tripwire), \
                 _isolated_gate_patch():
                with self.assertRaises(PreflightError):
                    asyncio.run(
                        run.TradingBotApp._build_live_executor(_app()))

        self.assertEqual(
            built, [],
            "LiveEngine dibangun padahal preflight gagal: objek hidup "
            "tanpa supervising sebelum operator tahu posisi sebenarnya",
        )

    def test_no_live_task_is_created(self):
        """
        Loop tidak boleh pernah dimulai.

        `_live_task` adalah satu-satunya pegangan ke loop poll. Kalau
        `run_loop` sempat jalan sebelum preflight, order bisa bocor.

        Instance yang diperiksa adalah instance yang benar-benar gagal
        boot — bukan objek baru, yang selalu `_live_task is None` dan
        tidak akan pernah gagal apa pun.
        """
        app = _app()
        with self.assertRaises(PreflightError):
            self._run_with_dead_exchange(app)

        self.assertIsNone(
            app._live_task,
            "loop live tercipta pada instance yang gagal boot",
        )


class TestCliExitCode(unittest.TestCase):
    """
    Titik masuk proses harus keluar dengan KODE, bukan traceback.

    Operator menjalankan bot lewat satu perintah. Kalau kegagalan preflight
    muncul sebagai traceback atau, lebih buruk, sebagai exit 0, boot yang
    gagal terlihat seperti boot yang berhasil.
    """

    def _cli_with_failing_main(self):
        import run

        async def _boom():
            raise PreflightError(
                PreflightError.EXCHANGE_UNREACHABLE,
                "Bursa Hyperliquid tidak bisa dibaca (ConnectionError). "
                "Bot tidak tahu posisi sebenarnya, jadi live tidak "
                "dijalankan. Periksa koneksi, base_url, dan proxy.")

        buf = io.StringIO()
        with patch.object(run, "main", _boom), \
             patch.dict(os.environ, {}, clear=False):
            with redirect_stdout(buf):
                code = run._cli(["run.py"])
        return code, buf.getvalue()

    def test_failure_exits_with_code_2(self):
        code, _ = self._cli_with_failing_main()
        self.assertEqual(code, 2,
                         "kegagalan konfigurasi live harus keluar dengan "
                         "kode 2, bukan 0")

    def test_failure_message_reaches_operator(self):
        """
        Pesan yang sampai ke operator harus menyebut penyebabnya.

        Tanpa ini, blok `except RuntimeError` bisa dihapus dan
        `test_failure_exits_with_code_2` tetap hijau.
        """
        _, text = self._cli_with_failing_main()
        self.assertIn("BOT TIDAK DIJALANKAN", text)
        self.assertIn("tidak bisa dibaca", text)

    def test_success_exits_with_code_0(self):
        """
        Jalur sehat harus keluar 0.

        Tanpa test ini, `return 2` di mana pun membuat CI selalu hijau
        sambil bot tidak pernah jalan.
        """
        import run

        async def _ok():
            return None

        with patch.object(run, "main", _ok):
            code = run._cli(["run.py"])
        self.assertEqual(code, 0)

    def test_cli_accepts_explicit_argv(self):
        """`_cli()` harus menerima argv eksplisit supaya bisa diuji."""
        import inspect

        import run

        params = inspect.signature(run._cli).parameters
        self.assertIn("argv", params,
                      "_cli harus menerima argv supaya exit code bisa "
                      "diuji tanpa menjalankan main() sungguhan")


if __name__ == "__main__":
    unittest.main()