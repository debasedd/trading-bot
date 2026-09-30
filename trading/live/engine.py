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
                "symbol": "{} / USDC:USDC".format(name),
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
            self.gate.record_error()
            return {
                "success": False,
                "message": self.gate.redact(f"exception saat kirim: {exc}"),
            }

        if not outcome.ok:
            self.gate.record_error()
            return {
                "success": False,
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

        position = await self._attach_protection(
            coin, symbol, "LONG" if is_buy else "SHORT",
            outcome.filled_size, outcome.avg_price or price,
            stop_loss, take_profit, leverage,
        )
        return {
            "success": True,
            "message": "posisi dibuka: " + outcome.describe(),
            "outcome": outcome,
            "protected": position.sl_order_id is not None,
            "position": position,
        }

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
                    "{} / USDC:USDC".format(coin),
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

    # ── Health check ───────────────────────────────────────────────────

    async def health_check(self) -> Dict[str, Any]:
        """
        Periksa apakah bursa masih bisa dibaca dan state masih sinkron.

        Ini dipanggil berkala oleh loop. Hasilnya menentukan apakah bot
        boleh mengirim order lagi:

        * bursa tidak bisa dibaca -> berhenti. Buta terhadap bursa
          berarti-butikarak tidak tahu posisi sebenarnya.
        * posisi lokal dan bursa berbeda -> berhenti. Bot yang mengirim
          order dengan keyakinan salah tentang posisinya akan
          menggandakan atau membatalkan eksposur yang salah.
        * kill switch aktif -> berhenti.
        """
        health: Dict[str, Any] = {
            "ok": False,
            "reachable": False,
            "reconciled": False,
            "kill_switch": self.gate.engaged,
            "problems": [],
        }

        if self.gate.engaged:
            health["problems"].append("kill switch aktif")
            return health

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

        for coin, local in local_by_coin.items():
            rpos = remote_by_coin.get(coin)
            if rpos is None:
                health["problems"].append(
                    "posisi tercatat lokal tapi hilang di bursa: "
                    "{}".format(coin))
            elif not self._sizes_match(local, rpos):
                health["problems"].append(
                    "ukuran posisi tidak cocok: {}".format(coin))

        # Order resting tanpa posisi di bursa: mungkin sudah terisi
        # dan proteksinya belum terpasang.
        for order in orders or []:
            coin = order.get("coin")
            if coin and coin not in local_by_coin:
                health["problems"].append(
                    "order resting tanpa posisi tercatat: {}".format(coin))

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

