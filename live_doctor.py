#!/usr/bin/env python
"""
live_doctor.py — Periksa kesiapan live TANPA mengirim order.

Alat ini hanya MEMBACA. Tidak ada satu pun jalur yang bisa mengubah posisi
di bursa, membatalkan order, atau memasang trigger. Itu disengaja: tujuan
pertama sebelum uang sungguhan bergerak adalah membuktikan bahwa kredensial
dan parsing bursa benar, dan satu-satunya cara aman untuk membuktikan itu
adalah tidak melakukan apa-apa.

Pemeriksaan:

  1. Kredensial terbaca dan alamatnya cocok dengan yang diharapkan
  2. Jaringan ke bursa hidup
  3. Saldo dan collateral bebas terbaca
  4. Posisi dan order resting terbaca
  5. Gerbang safety menolak seperti seharusnya
  6. Aturan aset (lot size, tick, leverage) bisa dibaca

Keluar dengan kode selain nol kalau ada yang gagal, supaya bisa dipakai
sebagai gerbang di script.

    python live_doctor.py --testnet
    python live_doctor.py --testnet --expect 0xabc...
"""
from __future__ import annotations

import argparse
import os
import sys


def _out(text: str = "") -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    print(text)


class Checker:
    def __init__(self):
        self.failed = []
        self.passed = 0

    def ok(self, name, detail=""):
        self.passed += 1
        _out("  [OK]    {}{}".format(name, "  " + detail if detail else ""))

    def fail(self, name, detail=""):
        self.failed.append(name)
        _out("  [GAGAL] {}{}".format(name, "  " + detail if detail else ""))

    def info(self, name, detail=""):
        _out("  [info]  {}{}".format(name, "  " + detail if detail else ""))

    def warn(self, name, detail=""):
        _out("  [WARN]  {}{}".format(name, "  " + detail if detail else ""))


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Periksa kesiapan live tanpa mengirim order")
    ap.add_argument("--testnet", action="store_true",
                    help="Periksa testnet (default)")
    ap.add_argument("--mainnet", action="store_true",
                    help="Periksa mainnet")
    ap.add_argument("--expect", default="",
                    help="Alamat wallet yang diharapkan")
    args = ap.parse_args()

    c = Checker()
    testnet = not args.mainnet

    _out()
    _out("=" * 66)
    _out("  LIVE DOCTOR")
    _out("  Mode baca-saja. Tidak ada order yang bisa dikirim dari sini.")
    _out("  Jaringan: " + ("TESTNET" if testnet else "MAINNET"))
    _out("=" * 66)
    _out()

    _out("[1] KREDENSIAL")
    key = os.environ.get("HYPERLIQUID_PRIVATE_KEY", "").strip()
    if not key:
        c.fail("private key ada", "HYPERLIQUID_PRIVATE_KEY belum diisi")
        return 1
    c.ok("private key ada", "terbaca dari environment")

    try:
        from eth_account import Account

        address = Account.from_key(key).address
        c.ok("private key valid", address)
    except Exception as exc:  # noqa: BLE001
        c.fail("private key valid", str(exc))
        return 1

    if args.expect:
        if address.lower() == args.expect.lower():
            c.ok("alamat cocok", address)
        else:
            c.fail("alamat cocok",
                   "dapat {} tapi diharapkan {}".format(address, args.expect))
            _out()
            _out("  HENTI. Alamat yang terbaca bukan yang Anda maksudkan.")
            return 1

    api_wallet = os.environ.get("HYPERLIQUID_ACCOUNT_ADDRESS", "").strip()
    if api_wallet:
        c.info("API wallet", api_wallet)
    _out()

    _out("[2] KONEKSI BURSA")
    try:
        from trading.live.client import LiveExchange

        exchange = LiveExchange(key, testnet=testnet,
                                account_address=api_wallet or None)
    except Exception as exc:  # noqa: BLE001
        c.fail("klien bursa dibuat", str(exc))
        return 1
    c.ok("klien bursa dibuat", exchange.base_url)
    c.info("alamat query", exchange.query_address)

    try:
        meta = exchange.info.meta()
        n = len(meta.get("universe") or [])
        if n:
            c.ok("meta() terbaca", "{} aset".format(n))
        else:
            c.fail("meta() terbaca", "universe kosong")
            return 1
    except Exception as exc:  # noqa: BLE001
        c.fail("jaringan ke bursa", str(exc))
        return 1
    _out()
    return _continue_checks(c, exchange)


def _continue_checks(c, exchange) -> int:
    _out("[3] SALDO")
    try:
        state = exchange.get_account_state()
        summary = state.get("marginSummary") or {}
        c.ok("account state terbaca")
        c.info("accountValue", "{:,.2f} USDC".format(
            float(summary.get("accountValue") or 0)))
        free = exchange.free_collateral()
        c.info("collateral bebas", "{:,.2f} USDC".format(free))
        if free <= 0:
            c.warn("collateral nol", "tidak ada dana untuk bertransaksi")
    except Exception as exc:  # noqa: BLE001
        c.fail("account state terbaca", str(exc))
    _out()

    _out("[4] POSISI DAN ORDER")
    try:
        positions = exchange.positions()
        c.ok("posisi terbaca", "{} posisi terbuka".format(len(positions)))
        for p in positions[:5]:
            c.info("posisi", "{} {} @ {}".format(
                p.get("coin"), p.get("size"), p.get("entry_price")))
    except Exception as exc:  # noqa: BLE001
        c.fail("posisi terbaca", str(exc))

    try:
        orders = exchange.open_orders()
        c.ok("order resting terbaca", "{} order".format(len(orders)))
        for o in orders[:5]:
            c.info("order", "{} {} @ {}".format(
                o.get("coin"), o.get("sz"), o.get("limitPx")))
    except Exception as exc:  # noqa: BLE001
        c.fail("order resting terbaca", str(exc))
    _out()
    return _final_checks(c, exchange)


def _final_checks(c, exchange) -> int:
    _out("[5] GERBANG SAFETY (harus MENOLAK)")
    try:
        import tempfile
        from pathlib import Path

        from core.config import LiveConfig
        from trading.live.safety import Blocker, OrderRequest, SafetyGate

        env = dict(os.environ)
        env.pop("TRADEBOT_LIVE", None)
        env.pop("TRADEBOT_LIVE_CONFIRMED", None)
        tmp = Path(tempfile.mkdtemp()) / "counters.json"
        gate = SafetyGate(LiveConfig(), env=env, state_path=tmp)
        req = OrderRequest(symbol="BTC / USDC:USDC", is_buy=True,
                           size=0.01, price=100.0)
        ok, blockers = gate.can_send(req, free_collateral=1000.0,
                                     current_exposure=0.0,
                                     symbol_exposure=0.0)
        if not ok and Blocker.LIVE_DISABLED in blockers:
            c.ok("gerbang menolak tanpa env live")
        else:
            c.fail("gerbang menolak tanpa env live",
                   "allowed={} blockers={}".format(ok, blockers))
    except Exception as exc:  # noqa: BLE001
        c.fail("gerbang safety diuji", str(exc))
    _out()

    _out("[6] ATURAN ASET")
    try:
        rules = exchange.asset_rules()
        if not rules:
            c.fail("aturan aset terbaca", "kosong")
        else:
            c.ok("aturan aset terbaca", "{} koin".format(len(rules)))
            for coin in list(rules)[:3]:
                r = rules[coin]
                c.info("aturan", "{}: lot {} desimal, min {}, max {}, lev {}"
                       .format(coin, r["sz_decimals"], r["sz_min"],
                               r["sz_max"], r.get("max_leverage")))
            first = next(iter(rules))
            c.info("contoh quantization",
                   "0.0123456789 -> {}".format(
                       exchange.quantize_size(first, 0.0123456789)))
    except Exception as exc:  # noqa: BLE001
        c.fail("aturan aset terbaca", str(exc))
    _out()

    _out("[7] RATE LIMIT")
    try:
        rl = exchange.rate_limit()
        c.ok("rate limit terbaca", str(rl)[:90])
    except Exception as exc:  # noqa: BLE001
        c.warn("rate limit tidak terbaca", str(exc))
    _out()

    _out("=" * 66)
    if c.failed:
        _out("  GAGAL: {} pemeriksaan".format(len(c.failed)))
        for name in c.failed:
            _out("    - " + name)
        _out()
        _out("  JANGAN pakai uang sungguhan sebelum ini beres.")
        _out("=" * 66)
        return 1

    _out("  Semua {} pemeriksaan lolos.".format(c.passed))
    _out()
    _out("  Cukup untuk LANJUT ke testnet read-only.")
    _out("  BELUM cukup untuk mengirim order sungguhan.")
    _out("=" * 66)
    return 0


if __name__ == "__main__":
    sys.exit(main())
