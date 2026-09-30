"""
trading/risk_manager.py — Kalkulasi ukuran posisi, validasi leverage, batas kerugian.
"""

from decimal import Decimal, ROUND_DOWN
from typing import Optional, Dict, List
from core.config import get_config
from core.logger import get_logger
from trading.models import Order, TradeDecision, Side

logger = get_logger("risk_manager")

# Satu sumber kebenaran untuk presisi harga. 8 desimal cukup untuk altcoin
# termurah tanpa sisa digit yang tidak pernah dipakai, dan pemanggil tidak
# perlu lagi menebak berapa angka yang membuat pembulatan.
PRICE_QUANT = Decimal("0.00000001")
MONEY_QUANT = Decimal("0.01")
QTY_QUANT = Decimal("0.00001")

# ── SL/TP fallback untuk mode NON-scalping ─────────────────────────
#
# Angka ini adalah SL/TP yang dipasang `PaperTradingEngine._execute_open`
# dan dihitung `DecisionAgent` untuk sizing ketika `scalping.enabled` false.
# Keduanya harus identik: kalau sizing memakai SL 2% sementara engine
# memasang SL 0.25%, notional meledak 8x dan risiko sebenarnya jauh
# lebih besar dari yang disetujui risk manager.
#
# Dulu nilai ini DIHARDCODE di kedua file dengan komentar yang saling
# menunjuk - pola yang pasti gagal, karena mengubah satu sisi tidak
# pernah mengubah yang lain dan tidak ada yang mengeluh. Sekarang modul
# ini yang memiliki keduanya, jadi mengubahnya mengubah kedua jalur
# sekaligus. `agents/decision_agent.py` mengimpornya dari sini.
NON_SCALP_SL_PCT = 0.02
NON_SCALP_TP_PCT = 0.04


class RiskManager:
    """
    Manajemen risiko otomatis:
    - Hitung ukuran posisi (Fixed Fractional / Kelly)
    - Validasi leverage
    - Cek batas kerugian harian
    - Cek drawdown maksimum
    - Cek jumlah posisi terbuka
    """

    def __init__(self, initial_balance: float = 0.0):
        """
        Args:
            initial_balance: modal awal akun, dipakai sebagai penyebut batas
                rugi harian. Disuntikkan sekali saat engine dibuat supaya
                circuit breaker tidak bergantung pada saldo kas yang berubah
                terus sepanjang hari. Nilai 0 berarti "belum diketahui" —
                `validate_trade` akan jatuh ke `balance` sebagai ganti.
        """
        self.config = get_config().risk
        self.fees = get_config().fees
        self._daily_pnl: float = 0.0
        self._daily_reset_date: str = ""
        self._initial_balance: float = float(initial_balance or 0.0)

    @property
    def initial_balance(self) -> float:
        """Modal awal akun, atau 0.0 bila belum diisi."""
        return self._initial_balance

    def set_initial_balance(self, value: float) -> None:
        """
        Set modal awal setelah akun benar-benar ada di database.

        Dipanggil sekali dari `PaperTradingEngine.initialize()` — saat itu
        `init_account()` baru saja membuat baris akun, jadi nilainya datang dari
        database dan bukan dari asumsi config.
        """
        try:
            self._initial_balance = float(value)
        except (TypeError, ValueError):
            self._initial_balance = 0.0

    def calculate_position_size(
        self,
        balance: float,
        entry_price: float,
        stop_loss_price: float,
        risk_pct: float = None,
        leverage: int = None,
    ) -> Dict:
        """
        Hitung ukuran posisi berdasarkan Fixed Fractional.

        Args:
            balance: Saldo akun saat ini
            entry_price: Harga masuk
            stop_loss_price: Harga stop loss
            risk_pct: Persentase risiko (default dari config)
            leverage: Leverage (default dari config)

        Returns:
            {
                "quantity": float,
                "margin": float,
                "position_value": float,
                "risk_amount": float,
                "leverage": int,
            }
        """
        if risk_pct is None:
            risk_pct = self.config.max_risk_per_trade
        if leverage is None:
            leverage = self.config.default_leverage

        # Validasi leverage
        leverage = min(leverage, self.config.max_leverage)

        # Risiko dalam dollar
        risk_amount = Decimal(str(balance)) * Decimal(str(risk_pct))

        # Jarak ke stop loss
        entry = Decimal(str(entry_price))
        sl = Decimal(str(stop_loss_price))
        sl_distance = abs(entry - sl)

        if sl_distance == 0:
            logger.warning("SL distance = 0, pakai 1% dari entry sebagai default")
            sl_distance = entry * Decimal("0.01")

        # Kuantitas = Risiko / Jarak SL
        quantity = risk_amount / sl_distance

        # Nilai posisi dan margin
        position_value = quantity * entry
        margin = position_value / Decimal(str(leverage))

        # Pastikan margin tidak melebihi saldo yang tersedia
        max_margin = Decimal(str(balance)) * Decimal("0.9")  # Max 90% saldo
        if margin > max_margin:
            margin = max_margin
            position_value = margin * Decimal(str(leverage))
            quantity = position_value / entry

        return {
            "quantity": float(quantity.quantize(QTY_QUANT, rounding=ROUND_DOWN)),
            "margin": float(margin.quantize(MONEY_QUANT, rounding=ROUND_DOWN)),
            "position_value": float(position_value.quantize(MONEY_QUANT, rounding=ROUND_DOWN)),
            "risk_amount": float(risk_amount.quantize(MONEY_QUANT, rounding=ROUND_DOWN)),
            "leverage": leverage,
        }

    def calculate_liquidation_price(
        self,
        entry_price: float,
        side: str,
        leverage: int,
        mmr: float = 0.004,  # 0.4% default BTC
    ) -> float:
        """
        Hitung harga likuidasi (isolated margin).

        LONG:  Liq = Entry × (1 - 1/Leverage + MMR)
        SHORT: Liq = Entry × (1 + 1/Leverage - MMR)
        """
        entry = Decimal(str(entry_price))
        lev = Decimal(str(leverage))
        mr = Decimal(str(mmr))

        if side == "LONG":
            liq = entry * (1 - 1 / lev + mr)
        else:  # SHORT
            liq = entry * (1 + 1 / lev - mr)

        return float(liq.quantize(PRICE_QUANT, rounding=ROUND_DOWN))

    def calculate_stop_loss(
        self, entry_price: float, side: str, sl_pct: float = 0.02
    ) -> float:
        """
        Hitung harga stop loss berdasarkan persentase.

        ROUND_DOWN dipakai eksplisit, sama seperti `calculate_liquidation_price`
        dan seluruh sizing. Tanpa itu, `Decimal.quantize` memakai default
        ROUND_HALF_EVEN dan SL bisa bergerak satu tick ke arah yang tidak
        diinginkan — untuk LONG berarti stop sedikit lebih rendah dari rencana,
        untuk SHORT sedikit lebih tinggi. Pada scalp berjarak 0.25%, selisih
        sekecil itu bukan pembulatan yang netral: ia memakan sebagian dari
        ruang gerak yang justru alasan kenapa scalp ini dipilih.
        """
        entry = Decimal(str(entry_price))
        pct = Decimal(str(sl_pct))

        if side == "LONG":
            sl = entry * (1 - pct)
        else:
            sl = entry * (1 + pct)

        return float(sl.quantize(PRICE_QUANT, rounding=ROUND_DOWN))

    def calculate_take_profit(
        self, entry_price: float, side: str, tp_pct: float = 0.04
    ) -> float:
        """
        Hitung harga take profit berdasarkan persentase.

        ROUND_DOWN konsisten dengan SL dan likuidasi. Untuk LONG, pembulatan ke
        bawah membuat TP sedikit lebih rendah — itu arah yang konservatif: target
        lebih mudah dicapai daripada pembulatan ke atas yang justru memperlebar
        risiko tanpa menambah imbalan.
        """
        entry = Decimal(str(entry_price))
        pct = Decimal(str(tp_pct))

        if side == "LONG":
            tp = entry * (1 + pct)
        else:
            tp = entry * (1 - pct)

        return float(tp.quantize(PRICE_QUANT, rounding=ROUND_DOWN))

    def calculate_fee(self, quantity: float, price: float, fee_type: str = "TAKER") -> float:
        """
        Hitung biaya transaksi simulasi.

        Fee SELALU non-negatif. Nilai absolut diambil dari quantity dan
        harga karena multiply dua bilangan negatif menghasilkan positif --
        dan dari satu negatif satu positif menghasilkan fee negatif, yang
        berarti "biaya" yang menambah saldo.

        Menjumlahkan fee negatif ke saldo bukan error yang terlihat: saldo naik
        tanpa ada order yang menghasilkan uang. Itu jenis bug yang tidak
        pernah ketahuan dari laporan PnL.
        """
        rate = self.fees.taker if fee_type == "TAKER" else self.fees.maker
        if rate < 0:
            rate = abs(float(rate))
        notional = abs(Decimal(str(quantity))) * abs(Decimal(str(price)))

        # Bulat ke 10 desimal, BUKAN ke sen.
        #
        # Versi lama memakai quantize ke 0.01 dengan ROUND_DOWN. Itu
        # menimbulkan dua masalah nyata:
        #
        # 1. Fee jadi tidak linear dengan notional. Notional 100 USDT
        #    dibayar 0.09, bukan 0.08 -- proporsionalitas rusak, dan
        #    test yang mengujinya gagal.
        #    0.08 -- proporsionalitas rusak, dan test yang mengujinya
        #    gagal.
        # 2. Pada order kecil fee hilang SEPENUHNYA. Order 0.001 koin
        #    @ $100 = notional $0.10, fee sebenarnya $0.000045, dibayar
        #    $0.00. Bot yang aktif melakukan scalping size kecil akan
        #    terlihat tanpa biaya transaksi sama sekali -- dan terlihat
        #    untung karena itu.
        #
        # Pembulatan ke bawah 10 desimal cukup untuk menghindari galat
        # float tanpa menghapus biaya yang kecil sekali.
        fee = (notional * Decimal(str(rate))).quantize(
            Decimal("0.0000000001"), rounding=ROUND_DOWN)
        return abs(float(fee))

    def calculate_pnl(
        self,
        side: str,
        entry_price: float,
        current_price: float,
        quantity: float,
    ) -> Dict:
        """
        Hitung PnL (unrealized atau realized).

        Returns:
            {"pnl": float, "roe_pct": float, "pnl_pct": float}
        """
        entry = Decimal(str(entry_price))
        current = Decimal(str(current_price))
        qty = Decimal(str(quantity))

        if side == "LONG":
            pnl = (current - entry) * qty
        else:
            pnl = (entry - current) * qty

        position_value = entry * qty
        pnl_pct = (pnl / position_value * 100) if position_value > 0 else Decimal("0")

        return {
            "pnl": float(pnl.quantize(Decimal("0.01"))),
            "roe_pct": float(pnl_pct.quantize(Decimal("0.01"))),
            "pnl_pct": float(pnl_pct.quantize(Decimal("0.01"))),
        }

    def validate_trade(
        self,
        balance: float,
        margin_required: float,
        open_positions: int,
        daily_pnl: float = 0,
        peak_balance: float = None,
        equity: float = None,
    ) -> Dict:
        """
        Validasi apakah trade boleh dieksekusi.

        Args:
            balance: saldo KAS BEBAS — dipakai untuk batas margin.
            equity:  wallet + unrealized. Dipakai untuk drawdown, karena
                     drawdown adalah ukuran penurunan nilai portofolio, bukan
                     sisa kas yang masih menganggur. Kalau `equity` diberikan,
                     `balance` yang turun karena margin terkunci tidak lagi
                     salah dibaca sebagai kerugian.

        Returns:
            {"allowed": bool, "reasons": [str]}
        """
        reasons = []

        # Cek margin cukup
        if margin_required > balance * 0.9:
            reasons.append(f"Margin ({margin_required:.2f}) melebihi 90% saldo ({balance:.2f})")

        # Cek jumlah posisi
        if open_positions >= self.config.max_open_positions:
            reasons.append(f"Posisi terbuka ({open_positions}) sudah mencapai batas ({self.config.max_open_positions})")

        # Circuit breaker kerugian harian.
        #
        # Penyebutnya `initial_balance`, BUKAN saldo kas sekarang. Memakai
        # `balance` terlihat lebih intuitif tapi keliru: begitu margin
        # terkunci, kas mengecil, penyebut ikut mengecil, ambang ikut turun —
        # sehingga pelonggaran terjadi justru di saat paling rugi. Modal awal
        # konstan sepanjang hari, dan itulah yang membuat ini circuit breaker
        # sungguhan.
        #
        # `initial_balance` selalu ada di tabel `account`, tapi fallback ke
        # `balance` menjaga pemeriksaan tetap berjalan kalau metadata itu
        # hilang — lebih baik ambangnya kurang ideal daripada circuit breaker
        # mati diam-diam.
        reference = self._initial_balance if self._initial_balance > 0 else balance
        if daily_pnl < 0 and reference > 0:
            loss_fraction = abs(daily_pnl) / reference
            if loss_fraction >= self.config.max_daily_loss:
                reasons.append(
                    f"Kerugian harian ({daily_pnl:.2f}) = {loss_fraction * 100:.2f}% "
                    f"modal awal, melewati batas {self.config.max_daily_loss * 100:.0f}%"
                )

        # Cek drawdown terhadap nilai portofolio, bukan kas bebas
        mark = equity if equity is not None else balance
        if peak_balance and peak_balance > 0:
            drawdown = (peak_balance - mark) / peak_balance
            if drawdown >= self.config.max_drawdown:
                reasons.append(f"Drawdown ({drawdown * 100:.1f}%) melebihi batas ({self.config.max_drawdown * 100}%)")

        return {
            "allowed": len(reasons) == 0,
            "reasons": reasons,
        }

    def calculate_scalp_position_size(
        self,
        balance: float,
        entry_price: float,
        leverage: int = None,
        risk_pct: float = None,
    ) -> Dict:
        """
        Position sizing khusus scalping — tanpa SL distance, pakai fixed fraction.

        Scalping pakai margin kecil tapi leverage tinggi, sizing berdasarkan
        persentase saldo per trade.
        """
        if risk_pct is None:
            risk_pct = self.config.max_risk_per_trade
        if leverage is None:
            leverage = self.config.default_leverage

        leverage = min(leverage, self.config.max_leverage)

        # Margin = risk_pct * balance
        margin = Decimal(str(balance)) * Decimal(str(risk_pct))

        # Max margin 90% / max_open_positions (spread risk)
        max_per_pos = Decimal(str(balance)) * Decimal("0.9") / Decimal(str(self.config.max_open_positions))
        margin = min(margin, max_per_pos)

        # Position value & quantity
        position_value = margin * Decimal(str(leverage))
        entry = Decimal(str(entry_price))
        quantity = position_value / entry if entry > 0 else Decimal("0")

        return {
            "quantity": float(quantity.quantize(QTY_QUANT, rounding=ROUND_DOWN)),
            "margin": float(margin.quantize(MONEY_QUANT, rounding=ROUND_DOWN)),
            "position_value": float(position_value.quantize(MONEY_QUANT, rounding=ROUND_DOWN)),
            "risk_amount": float(margin.quantize(MONEY_QUANT, rounding=ROUND_DOWN)),
            "leverage": leverage,
        }

    def kelly_criterion(self, win_rate: float, avg_win: float, avg_loss: float) -> float:
        """
        Hitung Kelly Criterion untuk position sizing optimal.

        f* = W - (1-W)/R
        Gunakan Half-Kelly (f*/2) untuk konservatif.
        """
        if avg_loss == 0:
            return 0.0

        r = avg_win / abs(avg_loss)
        f_star = win_rate - (1 - win_rate) / r

        # Half-Kelly, clamp 0-10%
        half_kelly = max(0, min(f_star / 2, 0.10))
        return round(half_kelly, 4)

    def calculate_max_drawdown(self, equity_history: List[float]) -> float:
        """Hitung maximum drawdown dari riwayat equity."""
        if len(equity_history) < 2:
            return 0.0

        peak = equity_history[0]
        max_dd = 0.0

        for equity in equity_history:
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd

        return round(max_dd, 4)

    def calculate_sharpe_ratio(
        self, returns: List[float], risk_free_rate: float = 0.0
    ) -> float:
        """Hitung Sharpe Ratio dari daftar return."""
        if len(returns) < 2:
            return 0.0

        import numpy as np
        arr = np.array(returns)
        mean_return = np.mean(arr)
        std_return = np.std(arr, ddof=1)

        if std_return == 0:
            return 0.0

        return round(float((mean_return - risk_free_rate) / std_return), 4)
