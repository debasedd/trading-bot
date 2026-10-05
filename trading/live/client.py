"""
trading/live/client.py — Pembungkus SDK Hyperliquid untuk kebutuhan bot.

Mengapa tidak memanggil SDK langsung dari mana-mana:

1. **Testnet vs mainnet ditentukan di satu tempat.** Salah pilih URL bukan
   kesalahan kecil: di mainnet yang salah maksud adalah order sungguhan.
2. **Private key tidak pernah melewati batas modul ini.** Modul lain
   bekerja dengan `LiveExchange` yang sudah siap pakai.
3. **Bentuk order dipatok di sini** — tick size, lot size, `reduce_only`,
   dan `cloid` — supaya tidak ada pemanggil yang bisa mengirim order dengan
   bentuk yang tidak valid karena tidak tahu aturan mainannya.
4. **Semua error diberi bentuk yang seragam** sehingga pemanggil tidak
   perlu tahu detail HTTP.

SDK resmi dipakai, bukan signing buatan sendiri. Dokumentasi Hyperliquid
secara eksplisit merekomendasikan begitu, dan alasannya nyata: L1 action
dipaket dengan msgpack di mana urutan field menentukan hash, dan salah
urutan menghasilkan tanda tangan yang valid tapi untuk aksi yang salah.
"""
from __future__ import annotations

import math
import time
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from core.logger import get_logger

try:
    from hyperliquid.utils.types import Cloid
except ImportError:  # pragma: no cover
    # Modul ini harus bisa di-import di mesin tanpa SDK terpasang; SDK
    # sendiri tetap wajib ada sebelum order apa pun benar-benar dikirim.
    Cloid = None

logger = get_logger("live_client")


class PreflightError(RuntimeError):
    """
    Kegagalan preflight. Bot TIDAK boleh start kalau ini muncul.

    Subkelas `RuntimeError` bukan gaya: `run.py` sudah menangkap
    `RuntimeError` di titik masuk dan keluar dengan kode 2 plus pesan yang
    bisa dibaca operator. Preflight yang melempar exception lain akan
    lolos sebagai traceback mentah, dan pesan yang hilang di titik yang
    paling butuh pesan adalah kegagalan boot.

    `code` ada supaya test bisa mengunci CABANG yang salah tanpa
    bergantung pada teks pesan. Assert `code`, bukan `assertIn` pada
    string: dua cabang yang kebetulan berbagi kata yang sama akan saling
    menyelamatkan, dan test jadi hijau karena alasan salah -- persis yang
    terjadi di `fase-1-partial.md` §5.2.
    """

    # Kode stabil. Perlakukan sebagai kontrak: renamed = test gagal.
    EXCHANGE_UNREACHABLE = "exchange_unreachable"
    UNIVERSE_EMPTY = "universe_empty"
    BAD_ADDRESS = "bad_address"
    SIGNER_MISMATCH = "signer_mismatch"
    AGENT_NOT_REGISTERED = "agent_not_registered"
    AGENT_CHECK_FAILED = "agent_check_failed"
    ACCOUNT_APPEARS_EMPTY = "account_appears_empty"
    AGENT_EXPIRED = "agent_expired"

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def mask_address(value: Optional[str]) -> str:
    """
    Bentuk aman untuk dicetak: `0x5972…0417`.

    Alamat penuh tidak dibutuhkan untuk diagnosis, tapi log sering dibaca
    orang yang tidak berhak atas akun tersebut, dan log biasa ditempel di
    issue. Private key tidak pernah masuk ke sini karena bentuknya 32 byte
    dan tidak pernah dialirkan ke logging mana pun di jalur live.
    """
    if not isinstance(value, str):
        return "<bukan string>"
    if len(value) < 10:
        return "<terlalu pendek>"
    return value[:6] + "…" + value[-4:]


@dataclass
class OrderOutcome:
    """
    Hasil satu pengiriman order, dalam bentuk yang seragam.

    `ok` TIDAK berarti order dieksekusi. Bursa mengembalikan respons per
    order, dan hasilnya bisa berupa dict sukses ATAU string error.
    `filled_size` sering 0 untuk order IOC yang tidak menyentuh book — itu
    sukses dalam arti "diterima", bukan "tereksekusi".
    """

    ok: bool
    filled_size: float = 0.0
    avg_price: float = 0.0
    order_id: Optional[int] = None
    error: Optional[str] = None
    raw: Any = None

    def describe(self) -> str:
        if not self.ok:
            return "GAGAL: " + str(self.error)
        if self.filled_size <= 0:
            return "DITERIMA tapi tidak terisi"
        return f"TERISI {self.filled_size} @ {self.avg_price}"


def to_cloid(raw: Any) -> Optional[Any]:
    """
    Ubah client order id menjadi objek yang dipahami SDK.

    SDK Hyperliquid MENYIGN cloid bersama order
    (`order_request_to_order_wire` memanggil `cloid.to_raw()`), jadi
    cloid bukan string bebas: harus `0x` diikuti 32 karakter hex, dan
    harus jadi objek `Cloid`, bukan `str`.

    String biasa ditolak SDK di lapisan signing -- sebelum ada yang
    dikirim ke bursa -- dengan error `AttributeError` yang tidak
    menyinggung cloid sama sekali. Konversi dilakukan di SATU tempat
    ini supaya tidak ada pemanggil yang perlu tahu bentuknya.

    `None` diteruskan apa adanya: order closing `reduce_only` dan
    `emergency_flat` memang mengirim tanpa cloid, dan itu sah.

    Bentuk yang salah DITOLAK dengan pesan yang menyebut cloid, bukan
    diteruskan. Meneruskannya berarti order hilang tanpa jejak.

    Pemeriksaan hex dilakukan DI SINI, bukan diserahkan ke
    `Cloid._validate()`. Validasi SDK hanya memeriksa awalan `0x` dan
    panjang 32 karakter -- `Cloid("0x" + "zz"*16)` diterima SDK, lalu
    ditolak bursa dengan pesan yang tidak menyebut cloid. Toda yang
    salah harus berhenti di sisi ini, sebelum ada yang dikirim.
    """
    if raw is None or isinstance(raw, Cloid):
        return raw

    text = str(raw)
    body = text[2:] if text[:2].lower() == "0x" else text
    if len(body) != 32 or any(ch not in "0123456789abcdefABCDEF"
                             for ch in body):
        raise ValueError(
            f"cloid tidak valid ({raw!r}): harus '0x' + 32 hex. "
            f"Bentuk lain ditolak bursa, dan ditolak SDK saat signing "
            f"-- order tidak akan pernah terkirim."
        )
    try:
        return Cloid.from_str(text)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"cloid tidak valid ({raw!r}): {exc}") from exc


def parse_order_response(raw: Any) -> OrderOutcome:
    """
    Terjemahkan respons bursa menjadi `OrderOutcome`.

    Respons batch Hyperliquid berupa LIST dengan panjang sama dengan
    jumlah order, dan setiap elemen bisa berupa dict sukses atau string
    error. Elemen error TIDAK BOLEH dianggap order sukses.
    """
    if isinstance(raw, list) and raw:
        raw = raw[0]
    if isinstance(raw, str):
        return OrderOutcome(ok=False, error=raw, raw=raw)
    if not isinstance(raw, dict):
        return OrderOutcome(ok=False, error="respons tidak dikenal", raw=raw)

    status = str(raw.get("status", "")).lower()
    if status and status != "ok":
        resp = raw.get("response") or raw.get("error") or raw
        return OrderOutcome(ok=False, error=str(resp), raw=raw)

    filled = raw.get("filled") or {}
    return OrderOutcome(
        ok=True,
        filled_size=float(filled.get("totalSz") or 0.0),
        avg_price=float(filled.get("avgPx") or 0.0),
        order_id=raw.get("oid"),
        raw=raw,
    )


class LiveExchange:
    """
    Klien order Hyperliquid untuk satu akun.

    Dibuat SATU KALI dan dipakai bersama. Immutable kecuali leverage yang
    memang harus diubah per simbol.
    """

    #: Margin sebelum kedaluwarsa. Agent yang masih valid beberapa menit
    #: lagi TIDAK boleh dipakai: order yang sedang dikirim bisa melewati
    #: batas itu, dan order yang hilang tanpa jejak tidak pernah ketahuan.
    AGENT_EXPIRY_MARGIN_SECONDS = 3600.0

    #: Kapan verifikasi agent diulang saat bot berjalan. Verifikasi sekali
    #: saat start hanya membuktikan keadaan saat itu; agent bisa dicabut
    #: kapan saja.
    AGENT_REVERIFY_SECONDS = 900.0

    def __init__(
        self,
        private_key: str,
        testnet: bool = True,
        account_address: Optional[str] = None,
        timeout: float = 15.0,
    ):
        if not private_key:
            raise ValueError("private_key wajib diisi")

        self.testnet = testnet
        self._account_address = account_address
        # Cache aturan presisi aset. Diisi sekali pada pemakaian pertama.
        self._rules: Optional[Dict[str, Dict[str, Any]]] = None

        # Import di dalam `__init__` supaya modul ini bisa di-import di
        # mesin tanpa SDK terpasang (mis. CI), dan supaya kegagalan
        # dependency muncul sebagai pesan jelas.
        try:
            from eth_account import Account
            from hyperliquid.exchange import Exchange
            from hyperliquid.utils import constants as hl_constants
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "SDK Hyperliquid belum terpasang. Jalankan: "
                "pip install hyperliquid-python-sdk"
            ) from exc

        self.wallet = Account.from_key(private_key)
        self.address = self.wallet.address
        # Alamat yang SEBENARNYA holding posisi. Kalau bot memakai API
        # wallet, posisi ADA di alamat itu -- bukan di alamat signer.
        # Query memakai `self.address` (signer) akan membaca akun yang
        # salah, dan rekonsiliasi akan menyimpulkan tidak ada posisi
        # padahal ada.
        self.query_address = (account_address or self.address).lower()

        base_url = (
            hl_constants.TESTNET_API_URL
            if testnet
            else hl_constants.MAINNET_API_URL
        )
        self.base_url = base_url

        self._info = None
        self._base_url = base_url
        # `Exchange` SDK juga membangun `Info` di __init__-nya, jadi dia
        # ikut ditunda. Lihat properti `exchange`.
        self._exchange = None
        self._account_address = account_address
        self._timeout = timeout

        logger.info(
            f"LiveExchange siap: {self.address} di "
            f"{'TESTNET' if testnet else 'MAINNET'} ({base_url})"
        )
        if account_address and account_address.lower() != self.address.lower():
            logger.warning(
                f"Signing dengan {self.address} tetapi account_address="
                f"{account_address} (API wallet / subaccount)"
            )

    # ── Pembacaan state ────────────────────────────────────────────────

    @property
    def info(self):
        """
        Klien `Info` Hyperliquid, dibangun saat pertama kali dibutuhkan.

        SENGAJA TIDAK dibangun di `__init__`. `Info(base_url)` langsung
        melakukan POST ke `api.hyperliquid-testnet.xyz` untuk mengambil
        `spotMeta` saat konstruksi, jadi membangunnya di `__init__`
        berarti SETIAP `LiveExchange(...)` — termasuk di test offline —
        menembus jaringan.

        Yang hilang dari penulisan ini bukan kebetulan, tapi bukti:
        110 kegagalan berturut-turut di `tests/test_api_wallet_separation.py`
        dengan `socket.getaddrinfo(('api.hyperliquid-testnet.xyz', 443))`.
        Test itu didokumentasikan sebagai "offline, tidak pernah menyentuh
        jaringan" dan secara rutin memang memanggil bursa sungguhan,
        memakai jaringan operator.

        Properti ini menjaga backward compatibility: semua pemanggil lama
        `exchange.info.…` tetap bekerja tanpa perubahan.
        """
        if self._info is None:
            from hyperliquid.info import Info
            self._info = Info(self._base_url, skip_ws=True)
        return self._info

    @info.setter
    def info(self, value):
        """
        Suntik klien `Info` — HANYA untuk test.

        Kode produksi tidak pernah menulis `exchange.info`. Setter ini
        ada supaya test yang menyuntik `Info_double` tidak harus tahu
        bahwa state internalnya bernama `_info`, dan supaya refactor
        nanti tidak memaksa mengubah delapan file test sekaligus.
        """
        self._info = value

    @property
    def exchange(self):
        """
        Klien `Exchange` Hyperliquid, dibangun saat pertama dipakai.

        `Exchange.__init__` SDK membuat `Info(base_url)` di dalamnya,
        yang langsung POST ke bursa untuk `spotMeta`. Jadi membangunnya
        di `__init__` LiveExchange berarti setiap instance — termasuk di
        test offline — menembus jaringan.
        """
        if self._exchange is None:
            from hyperliquid.exchange import Exchange
            self._exchange = Exchange(
                self.wallet, self._base_url,
                account_address=self._account_address,
                timeout=self._timeout,
            )
        return self._exchange

    @exchange.setter
    def exchange(self, value):
        """Suntik klien `Exchange` — HANYA untuk test, simetris dengan `info`."""
        self._exchange = value

    def get_account_state(self) -> Dict[str, Any]:
        """Margin, collateral, dan posisi sesuai pandangan BURSA."""
        return self.info.user_state(self.query_address)

    def free_collateral(self) -> float:
        """
        Collateral yang boleh dipakai untuk order baru.

        Nilai ini dibaca dari BURSA, bukan dari database lokal. Book lokal
        bisa menyimpang — dan kalau angka lokal yang dipakai untuk
        mengambil keputusan pengaman, limitnya tidak melindungi apa pun.
        """
        state = self.get_account_state()
        margin_summary = state.get("marginSummary") or {}
        # "accountValue" BUKAN collateral bebas: itu nilai total akun
        # termasuk margin yang sudah terkunci di posisi terbuka. Memakainya
        # membuat gerbang selalu melihat dana tersedia, jadi batas collateral
        # praktisnya tidak pernah menyala tepat di saat paling dibutuhkan.
        free = margin_summary.get("withdrawable")
        if free is not None:
            return float(free)
        # Fallback kalau bursa tidak mengirim "withdrawable": accountValue
        # dikurangi eksposur terkunci supaya tidak terlalu optimistis.
        value = float(margin_summary.get("accountValue") or 0.0)
        return max(0.0, value - abs(self.total_notional()))

    def positions(self) -> List[Dict[str, Any]]:
        """Posisi terbuka yang dilaporkan bursa (bukan book lokal)."""
        state = self.get_account_state()
        return [
            p for p in (state.get("assetPositions") or [])
            if float((p.get("position") or {}).get("szi") or 0.0) != 0.0
        ]

    def total_notional(self) -> float:
        """
        Total nilai nominal posisi terbuka, dihitung dari `szi * entryPx`.

        Sengaja memakai nominal, bukan `accountValue` — yang kedua adalah
        nilai EKUITAS seluruh akun, termasuk collateral yang menganggur, dan
        memakainya untuk batas eksposur akan membuat limit menyala tanpa
        sebab.
        """
        total = 0.0
        for asset in self.positions():
            pos = asset.get("position") or {}
            size = abs(float(pos.get("szi") or 0.0))
            entry = float(pos.get("entryPx") or 0.0)
            total += size * entry
        return total

    def symbol_notional(self, coin: str) -> float:
        """
        Nilai nominal posisi terbuka untuk satu koin saja.

        `position.coin` di respons bursa adalah STRING ticker ("BTC"),
        bukan indeks aset. Versi sebelumnya memanggil `int()` pada
        field itu, jadi setiap pemanggilan melempar `ValueError`.

        Dampaknya bukan cuma fungsi ini: `_remote_context()` memanggilnya
        SEBELUM `gate.can_send()`, sehingga tidak ada order yang pernah
        sampai ke gerbang safety -- termasuk order yang seharusnya
        ditolak karena alasan lain. Gerbang yang tidak pernah dipanggil
        tidak melindungi apa pun.
        """
        state = self.get_account_state()
        index = self.info.name_to_asset(coin)
        if index is None:
            return 0.0
        target = str(coin).strip().upper()
        for asset in state.get("assetPositions") or []:
            pos = asset.get("position") or {}
            name = pos.get("coin")
            # Satu perbandingan ticker, bukan satu perbandingan indeks:
            # bursa mengirim nama, dan nama itu yang sebanding dengan
            # argumen. Indeks hanya dipakai `name_to_asset` untuk
            # menolak koin yang memang tidak dikenal.
            if isinstance(name, int):
                if name != int(index):
                    continue
            elif str(name or "").strip().upper() != target:
                continue
            return abs(float(pos.get("szi") or 0.0)) * float(
                pos.get("entryPx") or 0.0
            )
        return 0.0

    def rate_limit(self) -> Dict[str, Any]:
        """
        Sisa kuota request berbasis alamat.

        Hyperliquid membatasi 1 request per 1 USDC yang diperdagangkan, dan
        setelah kena limit hanya satu order per 10 detik yang boleh. Bot
        scalping yang menembak tiap 0,3 detik bisa menghabiskan kuota itu
        jauh sebelum PnL-nya terealisasi.
        """
        return self.info.user_rate_limit(self.query_address)

    def open_orders(self) -> List[Dict[str, Any]]:
        return self.info.frontend_open_orders(self.query_address)

    # ------------------------------------------------------------------ tick

    def verify_agent_wallet(self) -> None:
        """
        Pastikan signer benar-benar agent wallet yang DAFTAR di master.

        `allow_api_wallet=True` menyatakan NIAT operator, bukan bukti.
        Dua kesalahan yang berbeda bisa membuat intent itu benar tapi
        hasilnya tetap salah, dan keduanya terlihat sama dari sisi bot:
        bot membaca akun yang tidak punya posisi apa pun.

        1. `account_address` salah ketik (`...ab` vs `...ba`). Bursa
           membalas akun kosong — bukan error — untuk alamat yang tidak
           dikenal.
        2. Signer adalah wallet yang tidak pernah didaftarkan sebagai agent
           di master tersebut. Order yang ditandatangani tidak akan
           pernah sampai ke akun itu, dan itu baru ketahuan saat order
           hilang.

        Endpoint: `extraAgents`, yaitu `POST /info {"type": "extraAgents",
        "user": <master>}`. Bentuk respons mengikuti SDK resmi
        (`Info.extra_agents`):

            [{"name": str, "address": str, "validUntil": int}, ...]

        Yang diperiksa terhadap MASTER, bukan terhadap `query_address`:
        agent terdaftar di bawah akun yang men-query dia, bukan di bawah
        alamat signer-nya sendiri.

        Kalau signer == master, tidak ada agent yang perlu dicek —
        konfigurasi itu sah dan memaksa operator mendaftarkan agent demi
        apa yang sudah benar tidak menambah keamanan.

        HANYA membaca. Tidak mengirim order dan tidak mengubah apa pun.
        """
        master = (self._account_address or self.address).lower()
        signer = self.address.lower()

        if master == signer:
            # Tidak ada pemisahan wallet; tidak ada yang perlu diverifikasi.
            return

        try:
            agents = self.info.extra_agents(self._account_address or self.address)
        except Exception as exc:  # noqa: BLE001
            raise PreflightError(
                PreflightError.AGENT_CHECK_FAILED,
                "Daftar agent wallet tidak bisa dibaca dari bursa ({}). "
                "Tidak bisa memastikan signer punya wewenang atas akun "
                "ini, jadi live tidak dijalankan."
                .format(type(exc).__name__)
            ) from exc

        registered = {}
        for agent in (agents or []):
            if not isinstance(agent, dict):
                continue
            address = str(agent.get("address") or "").strip().lower()
            if address:
                registered[address] = agent

        if signer not in registered:
            raise PreflightError(
                PreflightError.AGENT_NOT_REGISTERED,
                "Signer ({}) tidak terdaftar sebagai agent wallet dari "
                "master ({}). Dua kemungkinan: private key yang dipakai "
                "bukan agent milik akun ini, atau `account_address` diisi "
                "dengan alamat yang salah. Kalau memang memakai pemisahan "
                "wallet, daftarkan dulu signer lewat ApproveAgent di UI "
                "bursa.".format(mask_address(self.address),
                                 mask_address(master))
            )

        # MASA BERLAKU. `extraAgents` mengembalikan `validUntil` per agent.
        #
        # Unit-nya TIDAK didokumentasikan — SDK hanya menulis `"validUntil":
        # int`. Jadi satuan dideteksi dari besarannya, bukan diasumsikan:
        # nilai di atas 1e11 pasti milidetik (sekarang ~1.7e12), di bawah
        # itu detik. Salah membaca satuan membuat pemeriksaan kedaluwarsa
        # selalu benar atau selalu salah, dan tidak ada yang mengetahuinya.
        valid_until = self._agent_valid_until_seconds(
            registered[signer].get("validUntil"))
        if valid_until is not None:
            now = datetime.now(timezone.utc).timestamp()
            margin = self.AGENT_EXPIRY_MARGIN_SECONDS
            if valid_until <= now:
                raise PreflightError(
                    PreflightError.AGENT_EXPIRED,
                    "Agent wallet ({}) sudah KEDALUWARSA. Order yang "
                    "ditandatangani tidak akan sampai ke bursa — hilang "
                    "tanpa jejak, dan itu baru ketahuan saat order hilang. "
                    "Daftarkan ulang signer lewat ApproveAgent."
                    .format(mask_address(self.address)))
            if valid_until <= now + margin:
                raise PreflightError(
                    PreflightError.AGENT_EXPIRED,
                    "Agent wallet ({}) kedaluwarsa dalam {:.0f} detik "
                    "(margin {:.0f} detik). Order yang sedang dikirim bisa "
                    "melewati batas itu dan hilang tanpa jejak. Perpanjang "
                    "daftarnya sebelum melanjutkan.".format(
                        mask_address(self.address),
                        valid_until - now, margin))

        logger.info(
            "Agent wallet terverifikasi: signer %s terdaftar di master %s",
            mask_address(self.address), mask_address(master),
        )

    @staticmethod
    def _agent_valid_until_seconds(raw) -> Optional[float]:
        """
        Ubah `validUntil` menjadi detik-since-epoch, atau None.

        None berarti "tidak bisa dinilai" — `validUntil` yang tidak ada,
        nol, atau tidak bisa diparse. Itu TIDAK sama dengan "tidak
        kedaluwarsa":obot tidak tahu, jadi pemeriksaan kedaluwarsa dilewati
        demi mencegah penolakan palsu pada agent yang sebenarnya sah.

        Satu-satunya alasan None dipakai:menolak agent yang jelas masih
        beres hanya karena bursa tidak mengirim field-nya.
        """
        if raw is None or raw == "":
            return None
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return None
        if value <= 0:
            return None
        # > 1e11 detik = 3170 tahun. Pasti milidetik.
        return value / 1000.0 if value > 1e11 else value

    def agent_verification_due(self, now: Optional[float] = None) -> bool:
        """
        True kalau verifikasi agent sudah harus diulang.

        Percakapan bukan bagian dari rekonsiliasi, jadi ini dipanggil dari
        loop supaya agent yang dicabut di tengah jalan ikut terdeteksi.
        """
        now = time.time() if now is None else now
        last = getattr(self, "_agent_verified_at", None)
        if last is None:
            return True
        return (now - last) >= self.AGENT_REVERIFY_SECONDS

    def mark_agent_verified(self, now: Optional[float] = None) -> None:
        """Catat kapan verifikasi terakhir berhasil."""
        self._agent_verified_at = time.time() if now is None else now

    def reverify_agent_if_due(self) -> bool:
        """
        Verifikasi ulang agent kalau sudah waktunya. True kalau dicek.

        Melempar `PreflightError` kalau agent hilang, kedaluwarsa, atau
        tidak bisa diverifikasi — pemanggil yang memutuskanbagaimana
        perlakuannya.
        """
        if not self.agent_verification_due():
            return False
        self.verify_agent_wallet()
        self.mark_agent_verified()
        return True

    def spot_equity_is_nonzero(self) -> Optional[bool]:
        """
        True/False kalau saldo spot diketahui, None kalau TIDAK bisa dibaca.

        Kenapa perlu: `marginSummary.accountValue` di `clearinghouseState`
        hanya mencakup margin PERPS. Dokumentasi Portfolio margin
        menyatakan spot dan perps "are collectively margined together",
        jadi sebuah akun bisa punya dana yang seluruhnya di spot dan
        `accountValue` tetap nol.

        Mengembalikan `None` untuk "tidak diketahui", bukan `False`.
        Itu perbedaan yang menentukan: `False` berarti "akun ini memang
        kosong", `None` berarti "bot tidak tahu". Menyamakan keduanya
        akan membuat preflight menolak akun yang sebenarnya sehat —
        dan penolakan palsu lebih buruk daripada penolakan yang benar.

        Endpoint: `spotUserState` / `Info.spot_user_state`. Bentuk balasan
        diambil dari SDK resmi, bukan dari asumsi:

            {"balances": [{"coin": str, "total": str, "hold": str}, ...],
             ...}

        APA SAJA YANG DIANGGAP "ADA UANG"
        -------------------------------
        Setiap token dengan `total` atau `hold` yang tidak nol.

        Versi sebelumnya hanya menghitung USDC, dengan alasan yang memang
        benar: `total` ada dalam satuan token, jadi BTC tidak bisa
        dijumlahkan dengan USDC tanpa harga.

        Tapi untuk pertanyaan "apakah akun ini punya collateral", nilai
        TIDAK perlu dijumlahkan. Yang ditanyakan adalah nol atau bukan, dan
        itu berlaku sama untuk HYPE, BTC, atau USDC. Akun dengan 0,0001 HYPE
        adalah akun bersaldo, dan collateral-nya nyata menurut aturan
        portfolio margin (HYPE LTV 0.65, BTC LTV 0.5). Mengabaikannya
        berarti menandai akun yang hidup sebagai kosong.

        `hold` ikut dihitung: saldo yang sedang di-hold order yang belum
        terisi adalah milik akun itu juga.
        """
        try:
            spot = self.info.spot_user_state(self.query_address)
        except Exception:  # noqa: BLE001
            # Sengaja tidak logging di sini: pemanggil yang memutuskan
            # apakah ini penting. Melempar juga salah, karena "tidak bisa
            # baca" bukan "tidak ada".
            return None

        if not isinstance(spot, dict):
            return None

        balances = spot.get("balances")
        if balances is None:
            return None

        for balance in (balances or []):
            if not isinstance(balance, dict):
                return None
            for field in ("total", "hold"):
                raw = balance.get(field)
                if raw is None or raw == "":
                    continue
                try:
                    if float(raw) != 0.0:
                        return True
                except (TypeError, ValueError):
                    # Ada entri yang tidak bisa dibaca. Satu nilai rusak
                    # membuat seluruh saldo "tidak diketahui", lebih baik
                    # daripada menyimpulkan nol saat mungkin tidak.
                    return None

        # Semua entri terbaca dan semuanya nol.
        return False

    def preflight(self, allow_api_wallet: bool = False) -> None:
        """
        Pastikan bursa hidup dan kredensial benar SEBELUM order pertama.

        Kenapa ini wajib: `Info` dibangun lazy (lihat properti `info`),
        jadi `LiveExchange(...)` sendiri tidak lagi menyentuh jaringan.
        Bot bisa start di mesin yang tidak punya koneksi sama sekali dan
        baru gagal — atau lebih buruk, baru tahu posisinya salah — saat
        order pertama dikirim.

        Yang diperiksa, semuanya HANYA membaca:

        1. **Konektivitas.** `info.meta()` adalah satu call paling murah
           yang benar-benar ke bursa. Kalau gagal, bot tidak tahu posisi
           sendiri, dan ketidaktahuan itu harus menghentikan boot.
        2. **`account_address` bentuknya alamat.** Alamat salah bentuk
           tidak menghasilkan error dari bursa — `user_state()` untuk
           string bukan-alamat mengembalikan akun kosong. Bot akan
           menyimpulkan "tidak ada posisi" padahal posisinya ada.
        3. **Signer vs query address.** Kalau berbeda, ini HANYA sah
           kalau operator menyatakannya lewat `allow_api_wallet=True`.
           Tanpa itu, key typo (`...ab` vs `...ba`) menghasilkan wallet
           yang tidak pernah menandatangani ke bursa sungguhan, dan itu
           baru ketahuan saat order hilang.

        Melempar `PreflightError` dengan `code` yang menunjuk CABANG mana
        yang gagal, bukan `NetworkAccessDenied` dari socket guard test —
        di produksi tidak ada guard, jadi pesannya harus berdiri sendiri.

        TIDAK mengirim order. `meta()` dan validasi bentuk alamat adalah
        satu-satunya operasi yang dipanggil.
        """
        # 1. Konektivitas + 2. Bentuk universe.
        try:
            meta = self.info.meta() or {}
        except Exception as exc:  # noqa: BLE001
            raise PreflightError(
                PreflightError.EXCHANGE_UNREACHABLE,
                "Bursa Hyperliquid tidak bisa dibaca ({}). Bot tidak "
                "tahu posisi sebenarnya, jadi live tidak dijalankan. "
                "Periksa koneksi, base_url, dan proxy bila ada."
                .format(type(exc).__name__)
            ) from exc

        universe = meta.get("universe") or []
        if not universe:
            raise PreflightError(
                PreflightError.UNIVERSE_EMPTY,
                "Bursa menjawab tapi `universe` kosong. Meta yang tidak "
                "lengkap berarti aturan presisi aset tidak diketahui, dan "
                "size yang tidak sesuai aturan bursa ditolak tanpa pesan "
                "yang menyebut angkanya."
            )

        # 3. Bentuk alamat yang benar-benar dipakai untuk query.
        #
        # `account_address=None` itu KASUS VALID: `__init__` sudah
        # fallback ke alamat penanda tangan, dan `query_address` berisi
        # alamat itu. Yang di sini bukan "harus selalu diisi", tapi
        # "kalau diisi, harus berbentuk alamat" — kalau tidak, bursa
        # membalas akun kosong tanpa error.
        for label, value in (
            ("account_address", self._account_address),
            ("query_address", self.query_address),
        ):
            if value is None:
                continue
            if not self._is_address(value):
                raise PreflightError(
                    PreflightError.BAD_ADDRESS,
                    "{}={} bukan alamat Ethereum (0x + 40 hex). Bursa "
                    "tidak akan menolak ini dengan error — dia akan "
                    "membalas akun kosong, jadi bot menyimpulkan tidak ada "
                    "posisi padahal ada.".format(label, mask_address(value))
                )

        # 4. Signer vs query.
        if self._account_address and \
                self._account_address.lower() != self.address.lower() \
                and not allow_api_wallet:
            raise PreflightError(
                PreflightError.SIGNER_MISMATCH,
                "Signer ({}) berbeda dari account_address ({}). Pola ini "
                "hanya sah untuk API wallet, dan kalau salah ketik dia "
                "membaca akun yang tidak memegang posisi. Kalau memang "
                "memakai API wallet, teruskan allow_api_wallet=True."
                .format(mask_address(self.address),
                        mask_address(self._account_address))
            )

        # 5. Bukti, bukan niat: signer harus terdaftar sebagai agent.
        #
        # Hanya dicek ketika pemisahan wallet dipakai DAN operator sudah
        # opt-in. Kalau `allow_api_wallet=False`, cabang 4 di atas sudah
        # menolak lebih dulu, jadi cek di sini hanya relevan setelah opt-in
        # itu diterima.
        if allow_api_wallet:
            self.verify_agent_wallet()

        # 6. Akun yang tampak kosong dari perps TIDAK otomatis kosong.
        #
        # `accountValue` hanya mencakup margin perps. Kalau collateral
        # ada di SPOT, angka itu nol sementara akunnya jelas nyata.
        # Dokumentasi Portfolio margin: spot dan perps "are collectively
        # margined together within one account".
        #
        # TABEL KEBENARAN "APA ITU AKUN NYATA?"
        #
        # Test untuk setiap sel ada di `tests/test_account_truth_table.py`.
        #
        #   accountValue | posisi  | spot     | aksi
        #   -------------+---------+----------+--------------------------
        #   > 0          | ya/tidak| apa pun  | LOLOS
        #   0            | YA      | apa pun  | FAIL CLOSED
        #   0            | tidak   | > 0      | LOLOS (collateral di SPOT)
        #   0            | tidak   | 0        | DITOLAK (alamat salah)
        #   0            | tidak   | TIDAK    | TIDAK BISA MEMASTIKAN
        #               |         | TERBACA  |
        #
        # Baris kedua dan keempat adalah akun yang tidak memegang apa pun:
        # itu jawaban bursa untuk alamat yang bukan akun sungguhan. Kalau
        # lolos, bot melihat "datar" untuk akun yang salah dan mengira
        # tidak ada yang perlu dilindungi.
        #
        # Baris terakhir TIDAK boleh diperlakukan sebagai "tidak ada dana":
        # spot yang gagal dibaca karena rate limit bukan bukti apa pun.
        #
        # `accountValue` hanya mencakup margin PERPS. Dokumentasi Portfolio
        # margin menyatakan spot dan perps "are collectively margined
        # together within one account", jadi dana di SPOT tidak muncul di
        # angka ini.
        state = self.get_account_state()
        is_dict = isinstance(state, dict)
        margin = (state.get("marginSummary") or {}) if is_dict else {}
        try:
            perps_value = float(margin.get("accountValue") or 0.0)
        except (TypeError, ValueError):
            perps_value = 0.0

        positions = (state.get("assetPositions") or []) if is_dict else []
        has_position = any(
            float((p.get("position") or {}).get("szi") or 0.0) != 0.0
            for p in positions
            if isinstance(p, dict)
        )

        if perps_value <= 0.0 and has_position:
            # Baris 2. Posisi sebesar itu berarti ada margin; margin itu
            # harus terlihat di `accountValue`. Kalau tidak, bentuk
            # responsnya bukan akun nyata, dan spot yang dibaca bukan spot
            # akun itu — jadi collateral spot TIDAK boleh menyelamatkan sel
            # ini.
            raise PreflightError(
                PreflightError.ACCOUNT_APPEARS_EMPTY,
                "accountValue=%.6f padahal ada posisi perps terbuka. "
                "Posisi yang punya ukuran berarti ada margin, jadi kombinasi "
                "ini tidak mungkin terjadi pada akun sungguhan. Kemungkinan "
                "besar: `account_address` bukan akun yang memegang posisi."
                % perps_value)

        if perps_value <= 0.0 and not has_position:
            # Baris 3, 4, atau 5. Hanya spot yang bisa memutuskan.
            spot = self.spot_equity_is_nonzero()
            if spot is True:
                # Baris 3: collateral ada di SPOT. Akun nyata.
                pass
            elif spot is False:
                # Baris 4: memang tidak ada apa-apa.
                raise PreflightError(
                    PreflightError.ACCOUNT_APPEARS_EMPTY,
                    "Akun tidak punya margin perps (accountValue=%.6f), "
                    "tidak ada posisi, dan tidak ada saldo spot. Kombinasi "
                    "ini tidak cocok dengan akun sungguhan: kemungkinan "
                    "besar `account_address` bukan akun yang memegang "
                    "posisi." % perps_value)
            else:
                # Baris 5: tidak bisa memastikan.
                raise PreflightError(
                    PreflightError.ACCOUNT_APPEARS_EMPTY,
                    "Akun tidak punya margin perps (accountValue=%.6f) dan "
                    "tidak ada posisi, tapi saldo spot TIDAK bisa dibaca. "
                    "Ini bukan bukti akun kosong — ini TIDAK BISA "
                    "MEMASTIKAN, jadi live tidak dijalankan sampai "
                    "pembacaan spot berhasil." % perps_value)

        logger.info(
            "Preflight OK: %d aset, query %s", len(universe),
            mask_address(self.query_address),
        )

    @staticmethod
    def _is_address(value: Optional[str]) -> bool:
        """True kalau `value` adalah alamat Ethereum: 0x + 40 hex."""
        if not isinstance(value, str):
            return False
        v = value.strip()
        if len(v) != 42 or not v.startswith("0x"):
            return False
        return all(ch in "0123456789abcdefABCDEF" for ch in v[2:])

    def asset_rules(self) -> Dict[str, Dict[str, Any]]:
        """
        Aturan presisi per koin, di-cache.

        Hyperliquid menentukan `szDecimals` (lot size) dan tick size dari
        `szMax`, `szMin`, dan `maxLeverage` di dalam `info.meta()`. Tanpa
        ini, size yang tidak sesuai aturan bursa ditolak dengan pesan yang
        sering tidak menyebut angkanya -- dan bot akan gagal berulang kali
        dengan alasan yang sama.
        """
        if self._rules is not None:
            return self._rules

        rules: Dict[str, Dict[str, Any]] = {}
        try:
            universe = (self.info.meta() or {}).get("universe") or []
        except Exception as exc:  # noqa: BLE001
            logger.warning("Gagal membaca aturan aset: %s", exc)
            universe = []

        for asset in universe:
            name = asset.get("name")
            if not name:
                continue
            sz_max = float(asset.get("szMax") or 0.0)
            sz_min = float(asset.get("szMin") or 0.0)

            # Lot size diturunkan dari `szMin`, bukan `szMax`.
            # `szMax` cuma batas atas, tidak encode presisi sama sekali:
            # BTC punya szMax ~1e6 dan szMin 1e-5, jadi szMax-nya tidak
            # bisa dipakai untuk menyimpulkan desimal. Yang dipakai
            # szMin, karena di situlah presisi terkecil dinyatakan.
            sz_dec = 0
            if sz_min > 0:
                sz_dec = int(round(-1.0 * math.log10(sz_min)))
            # Bulletproof: kalau hasil hitung tidak menghasilkan step
            # yang membagi szMin dengan utuh, turunkan satu desimal sampai
            # cocok. Lebih baik satu desimal terlalu banyak daripada
            # membulatkan semua order BTC menjadi lot 1.
            for _ in range(9):
                step = 10.0 ** (-sz_dec)
                if step <= 0:
                    break
                if abs(round(sz_min / step) - sz_min / step) < 1e-6:
                    break
                sz_dec += 1

            rules[name] = {
                "sz_decimals": max(0, sz_dec),
                "sz_min": sz_min,
                "sz_max": sz_max,
                "max_leverage": int(asset.get("maxLeverage") or 0) or None,
            }

        self._rules = rules
        return rules

    def quantize_size(self, coin: str, size: float) -> float:
        """
        Bulatkan size ke lot size bursa, dan clamp ke rentang yang sah.

        `ROUND_DOWN` dipakai, bukan `ROUND_HALF_UP`: ukuran posisi yang
        sedikit lebih besar dari yang dimaksud berarti eksposur lebih
        besar dari yang disetujui batas gerbang.
        """
        rules = self.asset_rules().get(coin)
        if not rules:
            return size

        dec = int(rules["sz_decimals"])
        step = 10.0 ** (-dec)
        lo = float(rules.get("sz_min") or 0.0)
        hi = float(rules.get("sz_max") or 0.0)
        target = abs(size)

        # Nol tetap nol. Menaikkan nol ke szMin menghasilkan order
        # sekecil mungkin untuk koin yang tidak pernah dimaksudkan.
        if target == 0.0:
            return 0.0

        # Di bawah szMin: naikkan ke szMin. Membulatkan ke bawah
        # menghasilkan nol, dan nol ditolak tanpa alasan berguna.
        if lo > 0 and target < lo:
            return lo if size >= 0 else -lo

        # Di atas szMax: turunkan ke step terbesar yang masih muat.
        if hi > 0 and target > hi:
            target = math.floor(hi / step) * step

        # Pakai Decimal, bukan float. `0.5 / 1e-5` dalam float bisa
        # jadi 49999.99999999999, dan `floor` lalu memangkas satu step
        # penuh: order 0.5 menjadi 0.49999 tanpa alasan.
        step_d = Decimal(str(step))
        steps = int((Decimal(str(target)) / step_d).to_integral_value(
            rounding=ROUND_DOWN))
        quantized = round(float(Decimal(steps) * step_d), dec)

        return quantized if size >= 0 else -quantized

    def quantize_price(self, coin: str, price: float) -> float:
        """
        Bulatkan harga ke tick size yang wajar untuk koin.

        Perpetual crypto bergerak sangat halus, jadi tick 1 dolar membuat
        setiap order praktis tersimpan di harga yang sama. Presisi 5
        desimal cukup untuk koin termurah tanpa sisa digit yang tidak
        pernah dipakai.
        """
        if price <= 0:
            return price
        decimals = 5 if price < 1.0 else 4 if price < 100.0 else 2
        return round(price, decimals)

    # ── Pengiriman order ───────────────────────────────────────────────
    #
    # Semua method di bawah SINKRON dan memblokir. Ini disengaja: SDK-nya
    # blocking, dan membungkusnya dalam `await` hanya memberi ilusi
    # asinkroni tanpa mengubah apa pun. Pemanggil yang async harus
    # membungkusnya dengan `asyncio.to_thread`.

    def set_leverage(self, coin: str, leverage: int, is_cross: bool = True) -> Any:
        """
        Set leverage per simbol.

        Melewati ini berarti order pertama memakai leverage DEFAULT bursa,
        yang bisa jauh lebih tinggi daripada yang bot kira. Itu perbedaan
        antara loss 1% dan likuidasi.
        """
        return self.exchange.update_leverage(leverage, coin, is_cross)

    def place_limit_order(
        self,
        coin: str,
        is_buy: bool,
        size: float,
        price: float,
        reduce_only: bool = False,
        cloid: Optional[Any] = None,
    ) -> OrderOutcome:
        """
        Kirim satu limit order GTC.

        `cloid` (client order id) SEWAJIB diisi di jalur live: tanpa itu,
        ketika respons hilang karena timeout kita tidak punya cara untuk
        menanyakan "apakah order saya sudah masuk?". Idempotensi itu yang
        membedakan retry yang aman dari retry yang menggandakan posisi.

        Nilai `cloid` harus berbentuk hex `0x` + 32 karakter -- LIHAT
        `to_cloid`. Bentuk string bebas tidak akan sampai ke bursa: SDK
        menolaknya saat signing.
        """
        if size <= 0 or price <= 0:
            return OrderOutcome(
                ok=False, error=f"size/price tidak valid: {size} @ {price}"
            )

        # Konversi cloid dilakukan SEBELUMisto order apa pun, supaya
        # bentuk yang salah muncul sebagai penolakan yang jelas dan
        # tercatat, bukan sebagai exception yang ditangkap `except`
        # di bawah dan berubah jadi error yang tidak menyebut cloid.
        try:
            sdk_cloid = to_cloid(cloid)
        except ValueError as exc:
            return OrderOutcome(ok=False, error=str(exc))

        # Bulatkan ke lot/tick bursa sebelum mengirim. Size yang tidak
        # sesuai aturan bursa ditolak dengan pesan yang sering tidak
        # menyebut angkanya, jadi bot akan gagal berulang dengan alasan
        # yang sama tanpa pernah tahu penyebabnya.
        size = self.quantize_size(coin, size)
        price = self.quantize_price(coin, price)
        if size <= 0:
            return OrderOutcome(
                ok=False,
                error="size membulat jadi nol setelah mengikuti lot size "
                      f"bursa untuk {coin}: {size}",
            )

        order_type: Dict[str, Any] = {"limit": {"tif": "Gtc"}}
        try:
            raw = self.exchange.order(
                coin, is_buy, size, price, order_type,
                reduce_only=reduce_only, cloid=sdk_cloid,
            )
        except Exception as exc:  # noqa: BLE001
            return OrderOutcome(ok=False, error=str(exc), raw=exc)

        return parse_order_response(raw)

    def place_trigger_order(
        self,
        coin: str,
        is_buy: bool,
        size: float,
        trigger_price: float,
        tpsl: str,
        reduce_only: bool = True,
    ) -> OrderOutcome:
        """
        Pasang TP atau SL DI SISI BURSA.

        Ini bukan fitur tambahan — inilah yang membuat posisi tetap
        terlindungi ketika proses Python mati, kehabisan memori, atau
        jaringan terputus. Stop loss yang hanya dipantau di loop 0,3 detik
        TIDAK ADA selama prosesnya tidak berjalan.

        `tpsl` harus "tp" atau "sl". `is_buy` adalah ARAH order trigger:
        SL untuk posisi LONG adalah SELL, jadi `is_buy=False`.
        """
        if tpsl not in ("tp", "sl"):
            raise ValueError("tpsl harus 'tp' atau 'sl'")
        if trigger_price <= 0:
            return OrderOutcome(
                ok=False, error=f"trigger price tidak valid: {trigger_price}"
            )

        order_type: Dict[str, Any] = {
            "trigger": {
                "triggerPx": trigger_price,
                "isMarket": True,
                "tpsl": tpsl,
            }
        }
        try:
            raw = self.exchange.order(
                coin, is_buy, size, trigger_price, order_type,
                reduce_only=reduce_only,
            )
        except Exception as exc:  # noqa: BLE001
            return OrderOutcome(ok=False, error=str(exc), raw=exc)

        return parse_order_response(raw)

    def cancel(self, coin: str, oid: int) -> Any:
        return self.exchange.cancel(coin, oid)

    def cancel_by_cloid(self, coin: str, cloid: Any) -> Any:
        """
        Batalkan satu order lewat client order id-nya.

        `cancel(coin, oid)` tidak bisa dipakai di jalur timeout: `oid`
        justru informasi yang hilang saat respons hilang. cloid adalah
        satu-satunya identitas order yang kita tetapkan SEBELUM
        mengirim, jadi ini satu-satunya pembatalan yang mungkin dilakukan
        tanpa mengetahui apa pun soal bursa.

        Bursa tidak menyediakan lookup by cloid -- `cancel_by_cloid` ini
        action L1 yang membatalkan, bukan query. Kalau order ternyata
        sudah terisi, pembatalan tidak akan mengembalikannya.
        """
        return self.exchange.cancel_by_cloid(coin, to_cloid(cloid))

    def order_status(self, coin: str, oid: int) -> Dict[str, Any]:
        """
        Status satu order lewat `oid`.

        Hanya menerima `oid` numerik. Bentuk lain -- termasuk cloid --
        ditolak bursa dengan HTTP 422, jadi jangan dicoba.
        """
        return self.info.query_order_by_oid(self.query_address, int(oid))

    def mid_price(self, coin: str) -> float:
        """
        Harga mid terkini untuk satu koin.

        Dipakai `emergency_flat` untuk menutup posisi pada harga yang
        dijamin bisa terisi. Order closing tanpa harga pasar hanyalah
        berharap, dan saat darurat berharap tidak cukup.
        """
        try:
            all_mids = self.exchange.all_mids() or {}
        except Exception:  # noqa: BLE001
            return 0.0
        px = all_mids.get(coin)
        try:
            return float(px) if px is not None else 0.0
        except (TypeError, ValueError):
            return 0.0

    def cancel_all(self, coin: str) -> Any:
        """
        Batalkan semua order resting untuk satu koin.

        Dipakai saat proteksi posisi dipasang ulang, dan saat bot dinonaktifkan.
        Order yang menggantung di bursa tetap bisa mengeksekusi jauh setelah
        bot berhenti — dan itu sumber kerugian yang tidak terlihat.
        """
        return self.exchange.cancel(coin, None)


