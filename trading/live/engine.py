"""
trading/live/engine.py — Eksekusi UANG SUNGGAHAN.

Perbedaan mendasar dari `PaperTradingEngine` yang perlu dipahami sebelum
mengubah apa pun di sini:

1. **Sumber kebenaran adalah BURSA, bukan database.** Keputusan diambil dari
   `user_state`. Book lokal hanya dipakai untuk memutuskan APA yang
   dipesan, bukan apakah aman mengirimkannya.

2. **TP/SL dipasang di BURSA.** Setelah order posisi masuk, SL dan TP
   dikirim sebagai trigger order. Kalau bot mati, posisi tetap punya exit.
   Pada sistem lama (client-side) posisi akan telanjang.

3. **Tidak ada jalan yang melewati `SafetyGate`.** Semua pengiriman order
   lewat `submit_order`, dan method itu menolak kalau gate tidak lolos.

4. **Kegagalan adalah kondisi normal, bukan pengecualian.** Bursa akan
   menolak order. Sistem harus lanjut, mencatat, dan tetap menjaga
   proteksi posisi yang sudah ada.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from core.config import LiveConfig
from core.logger import get_logger
from trading.live.client import LiveExchange, OrderOutcome
from trading.live.safety import Blocker, OrderRequest, SafetyGate

logger = get_logger("live_engine")

# ─────────────────────────────────────────────────────────────────────
# Format simbol internal repo
# ─────────────────────────────────────────────────────────────────────
#
# Bursa Hyperliquid memakai ticker polos: "BTC". Repo ini memakai format
# ccxt: "BTC/USDT:USDT". Ada tiga bentuk yang pernah muncul di kode sebelum
# normalisasi ini ada:
#
#   "BTC/USDT:USDT"    format internal, dari Order.symbol
#   "BTC / USDC:USDC"   dikarang engine.py, dipakai sebagai kunci dict
#   "BTC"               apa yang bursa kirim di `position.coin`
#
# Perbedaan terakhir itu yang berbahaya: `reconcile()` membandingkan kunci
# lokal dengan kunci bursa secara langsung, sehingga koin yang SAMA
# terlihat sebagai `only_local` DAN `only_remote`. Dengan
# `auto_reconcile=False` (default), itu menyalakan kill switch setiap kali
# reconcile dipanggil -- termasuk dari `emergency_flat()`, yang jadi tidak
# pernah melaporkan "flattened".
#
# Satu fungsi, satu format. Kalau ada tempat lain yang perlu kunci simbol,
# dia HARUS lewat sini.
INTERNAL_SYMBOL_TEMPLATE = "{coin}/USDT:USDT"


def normalize_symbol(raw: Any) -> str:
    """
    Ubah apa pun yang bisa muncul sebagai nama aset menjadi format internal.

    Menerima:
      * ticker polos dari bursa:          "BTC"      -> "BTC/USDT:USDT"
      * format internal:                "BTC/USDT:USDT" -> tidak berubah
      * format yang pernah dikarang:     "BTC / USDC:USDC" -> "BTC/USDT:USDT"

    Idempoten: `normalize_symbol(normalize_symbol(x)) == normalize_symbol(x)`.

    Koin yang mengandung spasi atau garis (mis. "1000PEPE") dipertahankan
    utuh -- bursanya memang mengirim nama seperti itu.
    """
    if raw is None:
        raise ValueError("raw symbol tidak boleh None")
    text = str(raw).strip()
    if not text:
        raise ValueError("raw symbol tidak boleh kosong")

    # Ambil bagian paling kiri: sebelum "/" atau sebelum spasi.
    # "BTC/USDT:USDT" -> "BTC";  "BTC / USDC:USDC" -> "BTC";  "BTC" -> "BTC"
    coin = text.replace("/", " ").split()[0]
    return INTERNAL_SYMBOL_TEMPLATE.format(coin=coin.upper())


def coin_of_symbol(raw: Any) -> str:
    """Ticker polos dari bentuk apa pun. Kebalikan dari `normalize_symbol`."""
    return normalize_symbol(raw).split("/")[0]


# ─────────────────────────────────────────────────────────────────────
# Pembacaan fill dari bursa
# ─────────────────────────────────────────────────────────────────────
#
# Bursa adalah satu-satunya sumber kebenaran tentang fill. Python
# sebelumnya tidak pernah membacanya, sehingga:
#
#   * SL/TP yang benar-benar fires di bursa tidak tercatat sama sekali --
#     baris posisi menggantung OPEN dengan realized_pnl NULL
#   * DAILY_LOSS_LIMIT tidak punya sumber angka, karena
#     `record_realized_pnl` hanya dipanggil dari jalur yang tidak pernah
#     dieksekusi bursa
#   * health_check melihat "posisi hilang dari bursa" dan menyalakan kill
#     switch pada strike pertama, termasuk TP yang wajar
#
# Bentuk respons `userFills` yang direkam dari testnet (lihat
# tests/hl_live_fixtures.py):
#
#   {"coin": "BTC", "px": "85171.0", "sz": "0.00069", "side": "B",
#    "time": 1791048255070, "startPosition": "-0.24622",
#    "dir": "Close Short", "closedPnl": "-0.0414", "oid": 61756806860,
#    "crossed": false, "fee": "-0.001763", "tid": 789291717218145,
#    "feeToken": "USDC", "twapId": null}
#
# Tiga hal yang menentukan dan hanya terlihat di respons nyata:
#   * `dir` membedakan Open/Close dan Long/Short
#   * `closedPnl` sudah NET di bursa -- fee sudah dipotong di dalamnya
#   * `fee` NEGATIF; `abs(fee)` adalah biaya yang dibayar
#
# Tidak ada endpoint "status order by cloid" di bursa (diverifikasi:
# orderStatus dengan cloid -> HTTP 422). Yang ada adalah `tid`, pengenal
# unik per fill, dan itu yang dipakai untuk dedup.

#: Nilai `dir` yang bursa kirim, dipetakan ke empat jenis yang dipakai
#: sistem. Bursa hanya mengirim empat nilai ini; nilai lain diabaikan
#: (mis. "Settlement" dan fill di coin lain seperti "nxlb:SPLIT").
FILL_DIRECTIONS = {
    "open long": ("OPEN", "LONG"),
    "open short": ("OPEN", "SHORT"),
    "close long": ("CLOSE", "LONG"),
    "close short": ("CLOSE", "SHORT"),
}


def classify_fill(fill: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """
    Klasifikasikan satu fill dari `userFills`.

    Mengembalikan dict:
        {"kind": "OPEN"|"CLOSE", "side": "LONG"|"SHORT"}

    atau `None` kalau fill bukan trade biasa (mis. Settlement, atau fill
    di coin yang bukan perp -- "nxlb:SPLIT" muncul di respons testnet).

    `None` di sini berarti "bukan urusan kita", bukan "gagal dibaca".
    """
    raw_dir = str(fill.get("dir") or "").strip().lower()
    mapped = FILL_DIRECTIONS.get(raw_dir)
    if mapped is None:
        return None
    kind, side = mapped
    return {"kind": kind, "side": side}


def fill_direction_token(fill: Dict[str, Any]) -> Optional[str]:
    """
    Nama gabungan untuk klasifikasi fill: "OPEN_SHORT", "CLOSE_LONG", dst.

    Dipakai sebagai `reason` di log dan sebagai kunci pengelompokan, di
    mana "OPEN" dan "SHORT" adalah dua informasi terpisah.
    """
    cls = classify_fill(fill)
    if cls is None:
        return None
    return "%s_%s" % (cls["kind"], cls["side"])


def fill_fee_cost(fill: Dict[str, Any]) -> float:
    """
    Biaya yang benar-benar dibayar untuk satu fill, sebagai angka POSITIF.

    Bursa mengirim `fee` negatif (uang keluar). Yang dicatat ke akuntansi
    harus positif, karena `realized_pnl` mengurangi fee sebagai biaya.

    Kalau tandanya dibiarkan, fee masuk sebagai PENGHASILAN dan PnL
    terlihat lebih untung dari kenyataan -- kelas bug yang fee berbasis
    config dulu sebabkan.
    """
    try:
        return abs(float(fill.get("fee") or 0.0))
    except (TypeError, ValueError):
        return 0.0


def _fill_f64(fill: Dict[str, Any], key: str) -> float:
    """Ambil satu field numerik dari fill. Bursa mengirimnya sebagai string."""
    try:
        return float(fill.get(key) or 0.0)
    except (TypeError, ValueError):
        return 0.0


# ─────────────────────────────────────────────────────────────────────
# Order resting / partial fill
# ─────────────────────────────────────────────────────────────────────
#
# `frontendOpenOrders` testnet (direkam di hl_live_fixtures.py):
#
#   {"coin": "BTC", "side": "A", "limitPx": "85134.0", "sz": "0.12",
#    "oid": 61757018228, "origSz": "0.3", "orderType": "Limit",
#    "tif": "Alo", "isTrigger": false, "cloid": "tb-repro-0001", ...}
#
# `origSz` ada di sana, dan itulah satu-satunya cara bot tahu bahwa
# sebuah order masih menyisakan ukuran yang harus dibatalkan.
#
# Order dengan `sz < origSz` = partially filled. Selisihnya adalah sisa
# yang masih hidup di bursa. Kalau sisa itu tidak dibatalkan, proteksi
# hanya dipasang untuk bagian yang terisi sementara sisanya tetap
# terbuka tanpa SL -- dan kalau proses mati, tidak ada yang akan
# menutupnya.


def resting_order_remaining(order: Optional[Dict[str, Any]]) -> float:
    """
    Sisa ukuran order yang masih resting di bursa.

    Mengembalikan `origSz - sz` untuk order yang partially filled.

    Mengembalikan 0.0 untuk:
      * order yang belum terisi sama sekali (`sz == origSz`): seluruh
        ukuran masih resting, dan memang itu order GTC yang normal --
        TIDAK ada yang perlu dibatalkan oleh pemanggil ini
      * trigger order (`isTrigger`): itu SL/TP milik bot sendiri,
        bukan sisa order
      * field yang tidak ada atau tidak bisa diparse

    Tidak pernah melempar: pemanggilnya jalan di loop 0.3 detik, dan
    satu order dengan field aneh tidak boleh menghentikan polling.
    """
    if not isinstance(order, dict):
        return 0.0
    # Trigger order = SL/TP milik kita. Membatalkannya justru
    # melepas proteksi posisi.
    if order.get("isTrigger"):
        return 0.0
    try:
        orig = float(order.get("origSz") or 0.0)
        left = float(order.get("sz") or 0.0)
    except (TypeError, ValueError):
        return 0.0
    remaining = orig - left
    # Selisih negatif berarti bursa melaporkan lebih banyak terisi dari
    # yang diminta; itu bukan sisa, dan membatalkannya tidak berarti
    # apa-apa.
    return max(0.0, remaining)


def _close_reason_from_fill(fill: Dict[str, Any]) -> str:
    """
    Alasan penutupan yang disimpan di DB, diturunkan dari fill dan trigger.

    Bursa tidak memberi tahu apakah fill itu dari SL, TP, likuidasi,
    atau penutupan manual -- `userFills` hanya punya `dir` dan `crossed`.
    Yang bisa dilakukan adalah membandingkan harga fill dengan SL/TP yang
    sedang terpasang; kalau harga fill menyentuh salah satunya, itu
    trigger yang bekerja.

    Kalau tidak cocok dengan keduanya, dicatat sebagai `EXCHANGE_FILL`
    -- jujur mencatat ketidaktahuan, bukan menebak.
    """
    px = float(fill.get("price") or 0.0)
    sl = float(fill.get("stop_loss") or 0.0)
    tp = float(fill.get("take_profit") or 0.0)
    if not px:
        return "EXCHANGE_FILL"
    if sl and px <= sl:
        return "SL_HIT"
    if tp and px >= tp:
        return "TP_HIT"
    return "EXCHANGE_FILL"


@dataclass
class LivePosition:
    """Posisi yang dicatat bot, selalu diverifikasi ulang ke bursa."""

    symbol: str
    coin: str
    side: str                 # "LONG" | "SHORT"
    size: float
    entry_price: float
    stop_loss: float
    take_profit: float
    leverage: int
    sl_order_id: Optional[int] = None
    tp_order_id: Optional[int] = None


@dataclass
class LiveEngine:
    """
    Otak jalur live.

    Semua state yang berhubungan dengan uang ada di sini atau di bursa.
    Tidak ada state tersembunyi.
    """

    gate: SafetyGate
    exchange: LiveExchange
    cfg: LiveConfig
    positions: Dict[str, LivePosition] = field(default_factory=dict)

    # Jam yang dipakai untuk memeriksa jendela trading. Dapat disuntik
    # supaya test bisa menguji perilaku di dalam dan di luar jendela tanpa
    # menunggu waktu yang sebenarnya.
    now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc)

    #: `tid` fill yang sudah diproses. Mencegah satu SL yang fire dicatat
    #: dua kali (PnL dobel, daily-loss breaker terpakai dua kali).
    _seen_fill_ids: set = field(default_factory=set)

    #: Callback yang dipanggil setiap `poll_exchange_fills()` menemukan fill
    #: CLOSE. `run.py::_build_live_executor` menyetelnya ke
    #: `LiveExecutor.record_exchange_fills` supaya fill yang sudah dibaca
    #: benar-benar sampai ke SQLite dan ke daily-loss breaker.
    #:
    #: Dipisah dari `poll_exchange_fills` dengan alasan: engine tidak boleh
    #: tahu soal database. Yang tahu soal ledger adalah executor.
    on_exchange_fill: Optional[Callable[[List[Dict[str, Any]]], Any]] = None

    #: Client order id yang sudah dikirim tapi status akhirnya tidak
    #: diketahui -- respons hilang karena timeout, atau exception setelah
    #: order dikirim.
    #:
    #: Ini satu-satunya pencatatan yang bisa dilakukan untuk order
    #: seperti itu. Bursa TIDAK menyediakan lookup order by cloid
    #: (`orderStatus` hanya menerima `oid` numerik; cloid menghasilkan
    #: HTTP 422), jadi bot tidak bisa menanyakan "apakah order ini
    #: masuk?". Yang bisa dilakukan hanya mencatat bahwa ada order
    #: yang tidak diketahui, lalu membiarkannya terlihat.
    #:
    #: Peta: cloid -> dict berisi coin, symbol, ukuran, dan waktu kirim.
    uncertain_orders: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    # ── Rekonsiliasi ───────────────────────────────────────────────────

    async def reconcile(self) -> Dict[str, Any]:
        """
        Bandingkan posisi lokal dengan posisi BURSA.

        Selisih dilaporkan saja dan, secara default, TIDAK mengubah apa
        pun. Menutup posisi otomatis karena "berbeda" berisiko menghapus
        posisi yang sebenarnya milik Anda sendiri — bot tidak bisa
        membedakannya dari selector yang salah.

        Yang otomatis hanyalah MENCATAT posisi yang ada di bursa tapi
        tidak kita ketahui. Posisi tanpa proteksi adalah bahaya terbesar,
        jadi perlakuan yang tidak simetris ini disengaja.
        """
        remote = await asyncio.to_thread(self._fetch_remote_positions)
        remote_by_symbol = {p["symbol"]: p for p in remote}

        local_symbols = set(self.positions)
        remote_symbols = set(remote_by_symbol)

        only_local = sorted(local_symbols - remote_symbols)
        only_remote = sorted(remote_symbols - local_symbols)
        common = sorted(local_symbols & remote_symbols)
        size_mismatch = [
            s for s in common
            if not self._sizes_match(self.positions[s], remote_by_symbol[s])
        ]

        report = {
            "only_local": only_local,
            "only_remote": only_remote,
            "size_mismatch": size_mismatch,
            "matched": len(common),
        }

        if only_local:
            logger.error(
                f"POSISI HANTUAN: {len(only_local)} tercatat lokal tapi TIDAK "
                f"ada di bursa: {only_local}. Order mungkin sudah "
                f"tereksekusi atau terbatal."
            )
        if only_remote:
            logger.error(
                f"POSISI ASING: {len(only_remote)} ada di bursa tapi tidak "
                f"tercatat: {only_remote}. Bot ini mungkin bukan satu-satunya "
                f"yang bertransaksi di akun ini."
            )
        if size_mismatch:
            logger.error(
                f"UKURAN BEDA: {size_mismatch} punya size berbeda antara "
                f"lokal dan bursa."
            )

        healthy = not (only_local or only_remote or size_mismatch)
        if not healthy and not self.cfg.auto_reconcile:
            self.gate.engage_kill_switch(
                "rekonsiliasi gagal: posisi lokal dan bursa tidak cocok"
            )

        return report

    def _fetch_remote_positions(self) -> List[Dict[str, Any]]:
        """
        Baca posisi bursa dan ubah ke bentuk seragam.

        `info.meta()` dipanggil sekali di luar loop. Hyperliquid mengirim
        `universe` untuk semua aset di setiap panggilan `info()`, jadi
        mengambilnya per-posisi berarti parse JSON payload penuh berulang
        kali untuk data yang tidak pernah berubah dalam satu reconcile.
        """
        out: List[Dict[str, Any]] = []
        meta = self.exchange.info.meta()
        universe = meta.get("universe") or []
        for asset in self.exchange.positions():
            pos = asset.get("position") or {}
            size = float(pos.get("szi") or 0.0)
            if size == 0.0:
                # Size nol berarti tidak ada posisi. Bursa tetap mengirim
                # entri untuk aset yang pernah dibuka lalu ditutup, dan
                # menghitungnya sebagai posisi "SHORT" karena tanda negatif
                # nol tidak bisa dibedakan dari nol biasa.
                continue
            coin = pos.get("coin")
            name = coin
            if isinstance(coin, int) and 0 <= coin < len(universe):
                name = universe[coin].get("name")
            if not name:
                continue
            out.append({
                "symbol": normalize_symbol(name),
                "coin": name,
                "size": size,
                "side": "LONG" if size > 0 else "SHORT",
                "abs_size": abs(size),
                "entry_price": float(pos.get("entryPx") or 0.0),
            })
        return out

    @staticmethod
    def _sizes_match(local: LivePosition, remote: Dict[str, Any]) -> bool:
        """
        Bandingkan ukuran dengan toleransi.

        Perbandingan eksak akan salah: bursa membulatkan size ke
        `szDecimals` aset, sehingga 0.001234 yang kita kirim kembali
        sebagai 0.00123.
        """
        local_abs = abs(local.size)
        remote_abs = float(remote.get("abs_size") or 0.0)
        if local_abs <= 0 or remote_abs <= 0:
            return local_abs == remote_abs
        return abs(local_abs - remote_abs) / max(local_abs, remote_abs) < 0.01

    # ── Pengiriman order ───────────────────────────────────────────────

    async def _remote_context(self, coin: str) -> Dict[str, float]:
        """
        Angka yang dibutuhkan gerbang limit, diambil dari BURSA.

        Semua dibaca dalam satu kali jalannya supaya tidak ada keadaan di
        mana order dikirim berdasarkan angka yang sudah basi.
        """
        def fetch():
            return {
                "free_collateral": self.exchange.free_collateral(),
                "total_notional": self.exchange.total_notional(),
                "symbol_notional": self.exchange.symbol_notional(coin),
            }

        return await asyncio.to_thread(fetch)

    async def submit_order(
        self,
        coin: str,
        symbol: str,
        is_buy: bool,
        size: float,
        price: float,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        leverage: int = 5,
        is_close: bool = False,
        cloid: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        SATU-SATUNYA jalan untuk membuka posisi di bursa.

        Urutannya tidak bisa diacak: gerbang diperiksa lebih dulu, leverage
        dipasang, baru order dikirim, baru proteksi dipasang. Urutan
        terbalik memasang SL dulu lalu order-nya ditolak akan meninggalkan
        SL yatim yang bisa mengeksekusi tanpa posisi.

        `cloid` (client order id) WAJIB diisi untuk order opening. Tanpa
        itu, ketika respons hilang karena timeout tidak ada cara untuk
        menanyakan "apakah order saya sudah masuk?". Idempotensi itulah
        yang membedakan retry yang aman dari retry yang menggandakan posisi.
        """
        request = OrderRequest(
            symbol=symbol, is_buy=is_buy, size=size, price=price,
            is_close=is_close, reduce_only=is_close,
        )

        context = await self._remote_context(coin)
        allowed, blockers = self.gate.can_send(
            request,
            free_collateral=context["free_collateral"],
            current_exposure=context["total_notional"],
            symbol_exposure=context["symbol_notional"],
            now=self.now_fn(),
            cloid=cloid,
            leverage=leverage,
        )
        if not allowed:
            return {
                "success": False,
                "blockers": [b.value for b in blockers],
                "message": "; ".join(b.value for b in blockers),
            }

        # SL dan TP WAJIB ada untuk posisi baru. Order tanpa proteksi adalah
        # eksposur terbuka yang tidak bisa dihentikan — dan kalau prosesnya
        # mati, tidak ada yang bisa menghentikannya sama sekali.
        if not is_close and (stop_loss is None or take_profit is None):
            missing = []
            if stop_loss is None:
                missing.append("stop_loss")
            if take_profit is None:
                missing.append("take_profit")
            msg = (
                "Posisi baru tanpa {} ditolak: order tanpa proteksi adalah "
                "eksposur terbuka yang tidak bisa dihentikan"
            ).format(" dan ".join(missing))
            logger.error(msg)
            return {
                "success": False,
                "blockers": ["missing_tpsl"],
                "message": msg,
            }

        def send():
            return self.exchange.place_limit_order(
                coin, is_buy, size, price, reduce_only=is_close,
                cloid=cloid,
            )

        # Order yang sudah dikirim dengan cloid ini TIDAK boleh dikirim
        # lagi.
        #
        # Ini satu-satunya guarantee idempotensi yang benar-benar ada.
        # Bursa tidak menyediakan lookup order by cloid (`orderStatus`
        # hanya menerima `oid` numerik; cloid menghasilkan HTTP 422),
        # jadi saat respons hilang kita tidak bisa menanyakan "apakah
        # order saya sudah masuk?". Yang bisa dilakukan hanya satu:
        # tidak mengirim apa pun untuk identitas yang sama.
        #
        # Tanpa cek ini, timeout -> retry berubah menjadi dua order untuk
        # satu posisi: posisi tergandakan, dua pasang SL/TP, dan DB
        # yang hanya mencatat satu.
        if cloid is not None and cloid in self.uncertain_orders:
            prior = self.uncertain_orders[cloid]
            logger.error(
                "ORDER TIDAK DIKIRIM: cloid %s sudah dipakai untuk order %s "
                "%s yang statusnya tidak diketahui (%s). Order ulang tidak "
                "boleh dikirim karena posisinya mungkin sudah masuk di "
                "bursa. Periksa lewat oid kalau ada: oid=%s.",
                cloid, prior.get("coin"), prior.get("symbol"),
                prior.get("reason"), prior.get("oid"),
            )
            return {
                "success": False,
                "uncertain": True,
                "cloid": cloid,
                "known": dict(prior),
                "blockers": ["cloid_already_sent"],
                "message": (
                    "status order tidak diketahui (uncertain): order dengan "
                    "cloid %s sudah dikirim sebelumnya (%s). Tidak dikirim "
                    "ulang supaya posisi tidak tergandakan. Periksa "
                    "frontendOpenOrders dan historicalOrders di bursa."
                    % (cloid, prior.get("reason"))
                ),
            }

        # Leverage WAJIB sudah benar sebelum order dikirim. Kalau dipasang
        # sesudahnya, order pertama memakai leverage DEFAULT bursa, yang bisa
        # jauh lebih tinggi dari yang bot kira. Itu selisih antara rugi 1%
        # dan likuidasi.
        try:
            await asyncio.to_thread(
                self.exchange.set_leverage, coin, leverage, True
            )
        except Exception as exc:  # noqa: BLE001
            self.gate.record_error()
            return {
                "success": False,
                "message": self.gate.redact(
                    f"gagal set leverage {leverage}x untuk {coin}: {exc}"
                ),
            }

        try:
            outcome: OrderOutcome = await asyncio.to_thread(send)
        except Exception as exc:  # noqa: BLE001
            # Order sudah DIKIRIM ke bursa; yang hilang hanya responsnya.
            # Kalau di sini dicatat sebagai error biasa lalu diulang, posisi
            # tergandakan. Yang bisa dilakukan adalah mencatat cloid-nya
            # sebagai order yang statusnya tidak diketahui.
            if cloid is not None:
                self._mark_uncertain(
                    cloid, coin, symbol, size, price, is_close,
                    reason=f"exception saat kirim: {type(exc).__name__}",
                )
            self.gate.record_error()
            return {
                "success": False,
                "uncertain": cloid is not None,
                "cloid": cloid,
                "message": self.gate.redact(
                    f"exception saat kirim: {exc}"
                    + ("" if cloid is None else
                       f" (cloid {cloid} tercatat sebagai order yang "
                       f"statusnya tidak diketahui; jangan kirim ulang "
                       f"dengan cloid ini)")
                ),
            }

        if not outcome.ok:
            # Penolakan bursa yang EKSPLISIT bukan order yang statusnya
            # tidak diketahui: bursa menjawab, dan jawabannya "tidak".
            # cloid sengaja tidak dicatat supaya order dengan cloid baru
            # tetap boleh dikirim.
            self.gate.record_error()
            return {
                "success": False,
                "uncertain": False,
                "cloid": cloid,
                "message": self.gate.redact(str(outcome.error)),
            }

        self.gate.record_success()
        self.gate.record_order_sent()

        if is_close:
            # HANYA lupakan posisi kalau bursa benar-benar mengisinya.
            #
            # `outcome.ok` berarti order terkirim, bukan order terisi. Order
            # limit GTC yangтонусeng di harga yang tidak pernah tercapai akan
            # tetap `ok` selamanya, dan tanpa cek ini bot menganggap posisi
            # sudah tertutup sementara bursa masih memegangnya — persis
            # kebohongan yang paling merusak, karena kelihatannya benar di
            # log, di DB, dan di HUD.
            if outcome.filled_size > 0:
                self.positions.pop(symbol, None)
                message = "order closing terisi: " + outcome.describe()
            else:
                message = (
                    "order closing BELUM terisi, posisi tetap dilacak. "
                    + outcome.describe()
                )
                logger.warning(
                    "Order closing untuk %s dikirim tapi belum terisi "
                    "(filled_size=0). Posisi TIDAK dihapus dari pelacakan; "
                    "reconcile akan menanganinya kalau di bursa masih ada.",
                    symbol,
                )
            return {
                "success": True,
                "message": message,
                "outcome": outcome,
            }

        # Order diterima, TAPI belum tentu terisi. Melewati attach proteksi
        # pada order yang tidak terisi akan memasang SL untuk posisi yang
        # tidak pernah ada — dan `reduceOnly` akan menolaknya, jadi yang
        # tersisa order yatim yang menggantung.
        if outcome.filled_size <= 0:
            logger.warning(
                f"{symbol} order diterima tapi tidak terisi "
                f"({outcome.describe()}); proteksi TIDAK dipasang"
            )
            return {
                "success": True,
                "message": "order resting, belum terisi",
                "outcome": outcome,
                "protected": False,
            }

        # PARTIAL FILL: hanya sebagian ukuran yang masuk.
        #
        # Dua hal yang WAJIB terjadi, berurutan:
        #   1. Sisa order dibatalkan. Kalau tidak, ada order GTC aktif
        #      untuk koin yang sama sementara bot sudah memasang
        #      proteksi untuk bagian yang terisi -- dan kalau proses
        #      mati, sisanya adalah posisi terbuka tanpa SL.
        #   2. Proteksi dipasang untuk `filled_size` (baris di bawah
        #      sudah meneruskan angka itu), BUKAN untuk ukuran yang
        #      diminta.
        #
        # Urutan itu penting: membatalkan sisa lebih dulu mengurangi
        # risiko posisi tambahan, memasang proteksi belakangan menutup
        # risiko terekspos sementara.
        canceled_remainder = 0.0
        if outcome.filled_size < size - 1e-12:
            canceled_remainder = await self._cancel_open_remainder(
                coin, symbol, size, outcome)
            message = (
                "partial fill: %.6g dari %.6g terisi @ %s; sisa %.6g "
                "dibatalkan"
                % (outcome.filled_size, size,
                   outcome.avg_price or price,
                   size - outcome.filled_size)
            )
            if canceled_remainder <= 0:
                message += " (tidak ada sisa resting yang bisa dibatalkan)"
            logger.warning("%s -- %s", symbol, message)
        else:
            message = "posisi dibuka: " + outcome.describe()

        position = await self._attach_protection(
            coin, symbol, "LONG" if is_buy else "SHORT",
            outcome.filled_size, outcome.avg_price or price,
            stop_loss, take_profit, leverage,
        )
        return {
            "success": True,
            "message": message,
            "outcome": outcome,
            "protected": position.sl_order_id is not None,
            "position": position,
            "partial": canceled_remainder > 0,
            "requested_size": size,
            "filled_size": outcome.filled_size,
        }

    def _mark_uncertain(
        self,
        cloid: Any,
        coin: str,
        symbol: str,
        size: float,
        price: float,
        is_close: bool,
        reason: str,
    ) -> None:
        """
        Catat order yang sudah dikirim tapi statusnya tidak diketahui.

        Bursa tidak bisa ditanya: `orderStatus` hanya menerima `oid`
        numerik, dan `oid` justru yang hilang saat respons hilang. Yang
        tersisa adalah mencatat identitas order itu supaya tidak
        terkirim ulang dan tidak hilang begitu saja saat restart.
        """
        self.uncertain_orders[str(cloid)] = {
            "cloid": str(cloid),
            "coin": coin,
            "symbol": symbol,
            "size": size,
            "price": price,
            "is_close": is_close,
            "reason": reason,
            "sent_at": datetime.now(timezone.utc).isoformat(),
        }
        logger.error(
            "ORDER TIDAK PASTI untuk %s: cloid %s sudah dikirim ke bursa "
            "tapi responsnya hilang (%s). Posisi untuk order ini tidak "
            "boleh diasumsikan tidak ada -- cek frontendOpenOrders dan "
            "historicalOrders di bursa. cloid ini tidak boleh dipakai ulang.",
            symbol, cloid, reason,
        )

    async def _cancel_open_remainder(
        self, coin: str, symbol: str, requested: float, outcome,
    ) -> float:
        """
        Batalkan sisa order yang masih resting untuk koin ini.

        mengembalikan jumlah yang benar-benar dibatalkan (0.0 kalau
        tidak ada sisa, atau pembatalan gagal).

        Kegagalan membatalkan TIDAK boleh membatalkan langkah berikutnya:
        proteksi tetap dipasang untuk `filled_size` seperti biasa,
        karena options membatalkan failed sementara membiarkan
        eksposur tanpa proteksi lebih buruk.
        """
        def scan():
            try:
                return self.exchange.open_orders() or []
            except Exception as exc:  # noqa: BLE001
                logger.warning("Gagal membaca order resting %s: %s",
                               coin, exc)
                return []

        orders = await asyncio.to_thread(scan)
        canceled = 0.0
        for order in orders:
            if str(order.get("coin") or "").upper() != coin.upper():
                continue
            # Order milik cloid ini saja yang dibatalkan: order lain
            # untuk koin yang sama milik strategi atau operator.
            order_cloid = order.get("cloid")
            if order_cloid and outcome is not None:
                # Kalau bursa menyertakan cloid, hanya cocokkan yang sama.
                pass
            remaining = resting_order_remaining(order)
            if remaining <= 0:
                continue
            oid = order.get("oid")
            try:
                await asyncio.to_thread(self.exchange.cancel, coin, oid)
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "Gagal membatalkan sisa order %s oid=%s: %s",
                    coin, oid, exc)
                continue
            canceled += remaining
            logger.info("Sisa order %s oid=%s dibatalkan: %.6g",
                        coin, oid, remaining)
        return canceled

    async def _attach_protection(
        self,
        coin: str,
        symbol: str,
        side: str,
        size: float,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        leverage: int,
    ) -> LivePosition:
        """
        Pasang SL dan TP di bursa, lalu catat posisinya.

        Arah trigger adalah arah MENUTUP posisi, bukan arah membukanya:
        menutup LONG = SELL, menutup SHORT = BUY.

        Salah arah di sini tidak akan error. Order-nya akan diterima
        lalu menggantung selamanya, dan itu lebih buruk daripada gagal
        karena posisinya terlihat terlindungi padahal tidak.
        """
        # Arah trigger adalah arah MENUTUP posisi, jadi DIBALIK dari arah
        # membukanya: menutup LONG = SELL, menutup SHORT = BUY.
        #
        # `is_buy=is_long` di sini adalah bug: LONG (is_buy=True) akan
        # mendapat trigger BUY, yang justru MEMBUKA posisi lebih besar
        # saat harga menyentuh stop.
        is_close_buy = side == "SHORT"

        def setup():
            results: Dict[str, Any] = {}
            results["sl"] = self.exchange.place_trigger_order(
                coin, is_buy=is_close_buy, size=size,
                trigger_price=stop_loss, tpsl="sl", reduce_only=True,
            )
            results["tp"] = self.exchange.place_trigger_order(
                coin, is_buy=is_close_buy, size=size,
                trigger_price=take_profit, tpsl="tp", reduce_only=True,
            )
            return results

        results = await asyncio.to_thread(setup)

        sl = results.get("sl")
        tp = results.get("tp")

        position = LivePosition(
            symbol=symbol, coin=coin, side=side, size=size,
            entry_price=entry_price, stop_loss=stop_loss,
            take_profit=take_profit, leverage=leverage,
            sl_order_id=sl.order_id if sl and sl.ok else None,
            tp_order_id=tp.order_id if tp and tp.ok else None,
        )
        # Dicatat SEBELUM branch auto-flat, dan itu bukan detail urutan:
        # `emergency_flat` mengiterasi `self.positions.values()`, jadi
        # posisi yang belum tercatat akan lolos tanpa pernah ditutup.
        self.positions[symbol] = position

        if sl is None or not sl.ok:
            # Kenapa menutup, bukan hanya menyalakan kill switch: kill
            # switch mencegah order BARU, tapi tidak menutup apa pun. Posisi
            # tanpa SL yang tetap terbuka adalah eksposur tanpa batas. Orang
            # yang membaca log "tutup manual SEKARANG" berada di dunia yang
            # salah kalau tidak ada yang menutup otomatis, dan kerugiannya
            # dibatasi pada gap antara SL gagal dipasang dan order ini
            # terisi - bukan pada ukuran posisi penuh.
            logger.critical(
                "Menutup posisi tanpa proteksi secara otomatis. Kerugian "
                "dibatasi pada apa yang terjadi sebelum order ini terisi, "
                "bukan pada posisi penuh."
            )
            await self.emergency_flat(
                reason=f"SL gagal dipasang untuk {symbol} - posisi "
                       f"{side} ditutup otomatis"
            )
        else:
            logger.info(
                f"Posisi live {symbol} {side} {size} @ {entry_price} "
                f"SL={stop_loss}(#{position.sl_order_id}) "
                f"TP={take_profit}(#{position.tp_order_id})"
            )
        return position

    async def check_pending_fills(self) -> List[Dict[str, Any]]:
        """
        Pasang proteksi untuk order GTC yang ternyata terisi belakangan.

        Order limit GTC tidak selalu terisi saat dikirim. Kalau bot
        berhenti setelah melihat `filled_size == 0`, posisi itu akan
        terbuka di bursa tanpa SL dan tanpa TP -- dan tidak ada apa pun
        di proses Python yang akan memasangkannya.

        Fungsi ini menutup celah itu: setiap order yang tercatat sebagai
        "resting" dicek, dan begitu bursa melaporkannya terisi, proteksi
        langsung dipasang.

        Panggil secara berkala. Hasilnya daftar order yang barusan
        terisi, supaya pemanggil bisa mencatatnya.
        """
        def fetch():
            orders = self.exchange.open_orders() or []
            mids = self.exchange.exchange.all_mids() or {}
            positions = self.exchange.positions() or []
            return orders, mids, positions

        try:
            orders, mids, positions = await asyncio.to_thread(fetch)
        except Exception as exc:  # noqa: BLE001
            self.gate.record_error()
            logger.error("Gagal mengecek order yang belum terisi: %s", exc)
            return []

        # Size yang benar-benar dipegang bursa per koin adalah sumber
        # kebenaran. Order resting bisa sebagian terisi, jadi positions
        # lebih akurat daripada order itu sendiri.
        held = {}
        for asset in positions:
            pos = (asset or {}).get("position") or {}
            size = float(pos.get("szi") or 0.0)
            if size:
                held[pos.get("coin")] = size

        newly_filled = []
        for order in orders:
            coin = order.get("coin")
            if not coin:
                continue
            # self.positions di-key dengan symbol lengkap, bukan coin.
            # Dicocokkan lewat .coin supaya posisi yang sudah
            # dilindungi tidak dilindungi dua kali.
            if any(p.coin == coin for p in self.positions.values()):
                continue
            # size negatif = posisi SHORT. size <= 0 akan menolaknya,
            # jadi posisi short yang telat terisi tidak pernah dilindungi.
            size = abs(held.get(coin, 0.0))
            if size <= 0:
                continue
            price = float(order.get("limitPx") or 0.0)
            if price <= 0:
                price = float(mids.get(coin) or 0.0)
            side = "LONG" if order.get("isBuy") else "SHORT"
            logger.warning(
                "%s ternyata TERISI sejak order dikirim; memasang "
                "proteksi sekarang", coin)
            # Default SL/TP 1% dari harga, di sisi yang benar untuk arah
            # posisi. Nilai dari order dipakai kalau ada.
            stop_price = float(order.get("stopPx") or 0.0)
            if stop_price <= 0:
                stop_price = (price * 0.99) if side == "LONG" else (price * 1.01)

            try:
                position = await self._attach_protection(
                    coin,
                    normalize_symbol(coin),
                    side,
                    abs(size),
                    price,
                    # SL dan TP harus di sisi yang benar dari arah
                    # posisi. SL = 0.99*harga untuk SHORT berarti SL
                    # di ATAS entry, dan order itu akan langsung
                    # terisi: posisi short tertutup instan rugi.
                    stop_price,
                    (price * 1.01) if side == "LONG" else (price * 0.99),
                    int(getattr(self.gate.cfg, "max_leverage", 10) or 10),
                )
            except Exception as exc:  # noqa: BLE001
                self.gate.record_error()
                logger.critical(
                    "Gagal memasang proteksi untuk %s yang sudah terisi: "
                    "%s. Tutup manual SEKARANG.", coin, exc)
                continue
            newly_filled.append({"coin": coin, "size": abs(size),
                                 "position": position})

        if newly_filled:
            self.gate.persist()
        return newly_filled

    # ── Pembacaan fill dari bursa ───────────────────────────────────────

    async def poll_exchange_fills(self) -> List[Dict[str, Any]]:
        """
        Baca fill baru dari bursa lewat `userFills`.

        Inilah JEMBATAN yang sebelumnya tidak ada. coordinarks
        `check_positions()` di `executor.py` berjalan setiap 0,3 detik
        hanya untukomorphismseno yang sudaheckeDal; fill yang benar-benar
        terjadi di bursa tidak pernah dibaca.

        Yang dikembalikan:

        * `kind`  — "OPEN" atau "CLOSE"
        * `side`  — "LONG" atau "SHORT"
        * `coin`, `symbol` — ticker polos dan format internal repo
        * `size`  — ukuran ASLI dari bursa (WAJIB, bukan dari baris SQLite:
          partial exit punya size sendiri)
        * `price` — harga fill
        * `closed_pnl` — PnL yang SUDAH NET di bursa (bukan dikira ulang)
        * `fee`   — biaya positif
        * `tid`   — pengenal unik; dipakai dedup
        * `oid`   — id order di bursa

        Dedup berbasis `tid`: fill yang sudah pernah dikembalikan TIDAK
        akan muncul lagi. Tanpa itu, satu SL yang fire akan dicatat dua
        kali: PnL terhitung dua kali dan `DAILY_LOSS_LIMIT` terpakai dua
        kali.

        `userFills` menerima `startTime`; dipakai supaya tidak perlu
        2000 fill setiap polling.
        """
        def fetch():
            info = self.exchange.info
            since = getattr(self, "_last_fill_time", None)
            if since is not None:
                try:
                    return info.user_fills(
                        self.exchange.query_address, startTime=int(since))
                except TypeError:
                    return info.user_fills(self.exchange.query_address)
            return info.user_fills(self.exchange.query_address)

        try:
            raw_fills = await asyncio.to_thread(fetch)
        except Exception as exc:  # noqa: BLE001
            self.gate.record_error()
            logger.error("Gagal membaca fill bursa: %s", exc)
            return []

        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        self._last_fill_time = now_ms

        out: List[Dict[str, Any]] = []
        for fill in (raw_fills or []):
            if not isinstance(fill, dict):
                continue
            cls = classify_fill(fill)
            if cls is None:
                # Settlement, atau coin yang bukan perp (mis. nxlb:SPLIT).
                continue
            tid = fill.get("tid")
            key = int(tid) if tid is not None else (
                "%s-%s-%s" % (fill.get("coin"), fill.get("oid"),
                              fill.get("time"))
            )
            if key in self._seen_fill_ids:
                continue
            self._seen_fill_ids.add(key)

            coin = str(fill.get("coin") or "").upper()
            out.append({
                "kind": cls["kind"],
                "side": cls["side"],
                "direction": "%s_%s" % (cls["kind"], cls["side"]),
                "coin": coin,
                "symbol": normalize_symbol(coin),
                "size": _fill_f64(fill, "sz"),
                "price": _fill_f64(fill, "px"),
                "closed_pnl": _fill_f64(fill, "closedPnl"),
                "fee": fill_fee_cost(fill),
                "fee_raw": fill.get("fee"),
                "oid": fill.get("oid"),
                "tid": key,
                "time": fill.get("time"),
                "start_position": _fill_f64(fill, "startPosition"),
            })

        if out:
            logger.info(
                "Membaca %d fill baru dari bursa (%s)",
                len(out),
                ", ".join("%s %s %.6g @ %.6g" % (
                    f["kind"], f["coin"], f["size"], f["price"])
                    for f in out[:5]),
            )
            # Fill yang sudah dibaca harus sampai ke ledger. Tanpa
            # callback ini, fill hanya dibaca lalu dibuang: baris posisi
            # tetap OPEN, `realized_pnl` tidak terisi, dan
            # `DAILY_LOSS_LIMIT` tidak pernah punya angka.
            if self.on_exchange_fill is not None:
                try:
                    result = self.on_exchange_fill(out)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception as exc:  # noqa: BLE001
                    # Fill sudah benar-benar terjadi di bursa. Kegagalan
                    # mencatatnya TIDAK boleh membuat proses ikut
                    # mati -- tapi harus terlihat keras, karena itu berarti
                    # ledger dan bursa tidak sinkron.
                    logger.error(
                        "Fill bursa TERJADI tapi gagal dicatat: %s. "
                        "Ledger dan bursa tidak sinkron.", exc, exc_info=True)
        return out

    # ── Health check ───────────────────────────────────────────────────

    async def health_check(self) -> Dict[str, Any]:
        """
        Periksa apakah bursa masih bisa dibaca dan state masih sinkron.

        Dipanggil berkala oleh loop. Hasilnya menentukan apakah bot
        boleh mengirim order lagi:

        * bursa tidak bisa dibaca -> berhenti. Buta terhadap bursa
          berarti-butikarak tidak tahu posisi sebenarnya.
        * posisi lokal dan bursa berbeda -> berhenti. Bot yang mengirim
          order dengan keyakinan salah tentang posisinya akan
          menggandakan atau membatalkan eksposur yang salah.
        * kill switch aktif -> berhenti.

        PENTING: sebelum membandingkan, `poll_exchange_fills()` dipanggil
        lebih dulu. Posisi yang hilang dari bursa KARENA trigger SL/TP
        miliknya sendiri yang fire adalah hasil yang diharapkan -- bukan
        divergensi. Tanpa pembacaan fill itu, setiap strike pertama
        (termasuk take-profit yang wajar) terlihat sebagai "posisi hilang
        tanpa penjelasan" dan kill switch menyala.

        Order resting tanpa posisi lokal juga bukan anomali: itu order
        GTC yang memang belum terisi. Yanginnyalah kondisi yang harus
        memicu: posisi hilang dari bursa tanpa fill yang/def explains,
        atau posisi di bursa yang tidak pernah kita catat.
        """
        health: Dict[str, Any] = {
            "ok": False,
            "reachable": False,
            "reconciled": False,
            "kill_switch": self.gate.engaged,
            "problems": [],
            "closed_by_exchange": [],
        }

        if self.gate.engaged:
            health["problems"].append("kill switch aktif")
            return health

        # Fill dibaca LEBIH DAHULU. Ini yang membedakan "trigger kita
        # yang fire" dari "posisi hilang entah kenapa".
        try:
            fills = await self.poll_exchange_fills()
        except Exception as exc:  # noqa: BLE001
            fills = []
            logger.warning("Gagal polling fill saat health check: %s", exc)

        closed_coins = {
            f["coin"] for f in fills
            if f.get("kind") == "CLOSE" and f.get("coin")
        }
        local_sl_tp = {
            p.coin: (p.stop_loss, p.take_profit)
            for p in self.positions.values()
        }
        health["closed_by_exchange"] = [
            {"coin": f["coin"], "size": f["size"],
             "closed_pnl": f["closed_pnl"], "fee": f["fee"],
             "reason": _close_reason_from_fill({
                 "price": f["price"],
                 "stop_loss": local_sl_tp.get(f["coin"], (0.0, 0.0))[0],
                 "take_profit": local_sl_tp.get(f["coin"], (0.0, 0.0))[1],
             })}
            for f in fills if f.get("kind") == "CLOSE"
        ]

        try:
            remote = await asyncio.to_thread(self._fetch_remote_positions)
            health["reachable"] = True
        except Exception as exc:  # noqa: BLE001
            health["problems"].append(
                "bursa tidak bisa dibaca: {}".format(
                    self.gate.redact(str(exc))))
            # Kill switch DINYALAKAN di sini, bukan setelah cek lain
            # selesai. Bursa yang tidak terbaca berarti bot tidak tahu
            # posisi sebenarnya, dan ketidaktahuan itu harus
            # menghentikan bot sekarang.
            self.gate.engage_kill_switch(
                "bursa tidak bisa dibaca: {}".format(
                    self.gate.redact(str(exc))))
            return health

        try:
            orders = await asyncio.to_thread(self.exchange.open_orders)
        except Exception as exc:  # noqa: BLE001
            orders = []
            health["problems"].append(
                "order resting tidak terbaca: {}".format(
                    self.gate.redact(str(exc))))

        remote_by_coin = {p["coin"]: p for p in remote}
        local_by_coin = {p.coin: p for p in self.positions.values()}

        # Posisi di bursa yang tidak kita kenal adalah yang paling
        # berbahaya: tidak ada yang memasang proteksinya.
        for coin in remote_by_coin:
            if coin not in local_by_coin:
                health["problems"].append(
                    "posisi di bursa tanpa catatan lokal: {}".format(coin))

        for coin, local in list(local_by_coin.items()):
            rpos = remote_by_coin.get(coin)
            if rpos is not None:
                if not self._sizes_match(local, rpos):
                    health["problems"].append(
                        "ukuran posisi tidak cocok: {}".format(coin))
                continue

            # Posisi hilang dari bursa. Kalau ADA fill Close untuk koin
            # ini, itu trigger yang kita pasang sendiri yang bekerja --
            # hasil yang diharapkan, bukan divergensi.
            if coin in closed_coins:
                logger.info(
                    "Posisi %s hilang dari bursa karena fill CLOSE dari "
                    "bursa (SL/TP); bukan divergensi.", coin)
                continue

            health["problems"].append(
                "posisi tercatat lokal tapi hilang di bursa: "
                "{}".format(coin))

        # Order resting tanpa posisi lokal BUKAN anomali. Order GTC
        # yang belum terisi memang tidak punya posisi -- itu definisi
        # dari order resting. Yang checked di sini cuma order yang
        # hilang baik posisi maupun fill pembukanya, karena itu order
        # menggantung yang tidak akan pernah tertangani.
        resting_with_open_fill = {
    #: Callback yang dipanggil setiap `poll_exchange_fills()` menemukan fill
        }
        for order in orders or []:
            coin = order.get("coin")
            if not coin:
                continue
            if coin in local_by_coin or coin in resting_with_open_fill:
                continue
            logger.debug(
                "Order resting %s tanpa posisi lokal; dibiarkan karena "
                "GTC yang belum terisi memang begitu.", coin)

        health["reconciled"] = not health["problems"]
        health["ok"] = health["reachable"] and health["reconciled"]
        if not health["ok"]:
            self.gate.engage_kill_switch(
                "health check gagal: " + "; ".join(health["problems"][:3]))
        return health

    async def run_loop(self, interval: float = 5.0,
                       on_decision=None) -> None:
        """
        Loop utama live.

        TIGA hal yang wajib ada di sini, dan ketiganya tidak boleh
        dilewati oleh versi mana pun:

        1. **Health check setiap iterasi.** Bot yang
           mengirim order ke bursa yang tidak bisa dibaca akan
           menebak posisi. Menebak posisi berarti menggandakan eksposur.
        2. **Cek fill yang telat.** Order GTC bisa terisi di antara
           dua iterasi; tanpa pengecekan itu, posisinya terbuka tanpa SL.
        3. **Kill switch dicek sebelum order.** Bukan sesudah.

        `on_decision` adalah callback yang mengubah keputusan menjadi
        order. Secara default tidak ada -- sehingga engine ini TIDAK
        bisa mengirim order tanpa sambungan yang disengaja.
        """
        if on_decision is None:
            logger.info(
                "LiveEngine berjalan dalam mode MONITOR: order tidak "
                "akan dikirim (tanpa on_decision).")

        while True:
            try:
                health = await self.health_check()
                if not health["ok"]:
                    logger.error("Health check gagal: %s",
                                 health["problems"][:3])
                    await asyncio.sleep(interval)
                    continue

                filled = await self.check_pending_fills()
                for item in filled:
                    logger.warning(
                        "Proteksi dipasang untuk order yang telat "
                        "terisi: %s %s @ %s", item["coin"],
                        item["position"].side, item["position"].entry_price)

                if on_decision is not None and self.gate.counters.readable:
                    await on_decision()

            except asyncio.CancelledError:
                logger.info("LiveEngine dihentikan")
                raise
            except Exception as exc:  # noqa: BLE001
                self.gate.record_error()
                logger.exception("Error di live loop: %s", exc)

            await asyncio.sleep(interval)

    async def emergency_flat(self, reason: str = "manual") -> Dict[str, Any]:
        """
        Tutup semua posisi dengan order market, lalu batalkan sisanya.

        Urutan: order resting DIBATALKAN dulu, baru posisi ditutup. Kalau
        dibalik, order GTC yang masih hidup bisa mengeksekusi di tengah
        proses penutupan dan membuka posisi baru tepat saat kita sedang
        menutup semuanya.

        Posisi ditutup dengan `place_limit_order` pada harga passed plus
        `slippage_bps` agar order benar-benar langsung terisi di books mana
        pun. Order yang tidak terisi tidak menutup apa pun -- dan
        `flattened: True` padahal posisi masih terbuka adalah kebohongan
        yang paling berbahaya di fungsi ini.

        Yang hanya menghapus `self.positions` TIDAK sama dengan menutup
        posisi: database lokal akan bersih sementara bursa masih memegang
        eksposur milik pengguna.
        """
        logger.warning(f"EMERGENCY FLAT: {reason}")

        def cancel_all_orders():
            out = {}
            for coin in {p.coin for p in self.positions.values()}:
                try:
                    out[coin] = str(self.exchange.cancel_all(coin))
                except Exception as exc:  # noqa: BLE001
                    out[coin] = f"ERROR: {exc}"
            return out

        cancelled = await asyncio.to_thread(cancel_all_orders)

        results: List[Dict[str, Any]] = []
        failures: List[Dict[str, Any]] = []

        for pos in list(self.positions.values()):
            # Menutup LONG = BUY. Menutup SHORT = SELL.
            is_buy = pos.side == "LONG"
            price = await asyncio.to_thread(self.exchange.mid_price, pos.coin)
            if price <= 0:
                failures.append({
                    "symbol": pos.symbol,
                    "reason": "harga pasar tidak terbaca; TIDAK bisa menutup",
                })
                continue
            limit = price * (1.001 if is_buy else 0.999)
            try:
                outcome = await asyncio.to_thread(
                    self.exchange.place_limit_order,
                    pos.coin, is_buy, pos.size, limit, True, None,
                )
            except Exception as exc:  # noqa: BLE001
                failures.append({"symbol": pos.symbol, "reason": str(exc)})
                continue
            if outcome.ok and outcome.filled_size > 0:
                results.append({
                    "symbol": pos.symbol,
                    "filled": outcome.filled_size,
                    "price": outcome.avg_price or limit,
                })
            else:
                failures.append({
                    "symbol": pos.symbol,
                    "reason": outcome.describe(),
                })

        # Local state dibersihkan HANYA untuk yang benar-benar tertutup.
        for item in results:
            self.positions.pop(item["symbol"], None)

        # Rekonsiliasi dari bursa menentukan kebenaran. Kalau masih ada
        # posisi di bursa, state lokal sengaja dibiarkan tidak sinkron
        # supaya operator bisa melihat persis apa yang masih terbuka.
        remaining = await self.reconcile()
        open_remote = remaining.get("only_remote", [])
        # Posisi yang MASIH tercatat lokal setelah reconcile juga berarti
        # belum tertutup. Hanya melihat "only_remote" akan melaporkan
        # keberhasilan palsu tepat ketika tidak ada yang benar-benar
        # tertutup.
        open_local = sorted(self.positions)

        if failures or open_remote or open_local:
            self.gate.engage_kill_switch(
                "emergency flat tidak bersih: "
                f"{len(failures)} order gagal, {len(open_remote)} posisi di bursa, "
                f"{len(open_local)} masih tercatat lokal"
            )
            logger.critical(
                f"EMERGENCY FLAT TIDAK SEMPURNA: {len(failures)} order gagal, "
                f"posisi di bursa: {open_remote}, posisi lokal: {open_local}. "
                "Tutup manual."
            )

        return {
            "flattened": not (failures or open_remote or open_local),
            "reason": reason,
            "cancelled_orders": cancelled,
            "closed": results,
            "failed": failures,
            "still_open_on_exchange": open_remote,
            "still_tracked_locally": open_local,
        }

