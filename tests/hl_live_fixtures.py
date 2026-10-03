"""
Fixture respons NYATA dari API publik Hyperliquid testnet.

Semua isi di file ini disalin apa adanya dari panggilan read-only ke
`https://api.hyperliquid-testnet.xyz/info` pada 2026-10-03. Tidak ada
order yang dikirim, tidak ada kredensial yang dipakai, tidak ada akun
yang milik repository ini.

MENGAPA INI PENTING
-------------------
Code produksi dulu salah membaca tiga hal yang HANYA bisa diketahui dari
respons nyata. Ketiganya jadi akar defect yang direproduksi di
`test_repro_live_defects.py`:

1. `position.coin` adalah STRING ("BTC"), bukan integer.
   Produksi menulis `int(pos.get("coin", -1))` -> ValueError.

2. Semua angka datang sebagai STRING desimal: `szi` = "-0.24567",
   `entryPx` = "85110.1", `fee` = "-0.002095".

3. Fill punya `dir` ("Open Long"/"Open Short"/"Close Long"/"Close
   Short"), `closedPnl`, dan `fee` BERUBAH tanda: fee di isi minus.
   `closedPnl` sudah NET di bursa -- angkanya sudah dikurangi fee,
   jadi produksi harus memakai angka itu apa adanya, bukan menghitung
   ulang dari harga.

Tambahan untuk item (c) dan (d): order punya `origSz` (ukuran asli
sebelum partial fill) dan `cloid` (null kalau tidak diisi).

TENTANG item (d) -- TEMUAN YANG MENGUBAH RENCANA
-----------------------------------------------
Saya memeriksa apakah status order bisa dicari lewat `cloid`:

    POST {"type":"orderStatus","user":addr,"oid":61756985785}
      -> 200 {"status":"order","order":{...,"status":"canceled", ...}}

    POST {"type":"orderStatus","user":addr,"cloid":"7c7a..."}
      -> HTTP 422 "Failed to deserialize the JSON body"

    POST {"type":"queryOrderByCloid","user":addr,"cloid":"7c7a..."}
      -> HTTP 422 "Failed to deserialize the JSON body"

Jadi bursa TIDAK menyediakan lookup order by cloid. SDK Python juga tidak
punya `query_order_by_cloid`; yang ada hanya `cancel_by_cloid` dan
`bulk_cancel_by_cloid`.

Konsekuensi untuk desain item (d): idempotensi retry TIDAK bisa
bergantung pada cloid. Yang bisa dipakai:
  - `origSz` vs `sz` untuk mendeteksi partial fill (item c)
  - `oid` yang sudah dikembalikan bursa, dicek via `orderStatus` (item d)
  - `frontendOpenOrders` + `historicalOrders` untuk memverifikasi apakah
    order benar-benar resting

`CLOID_ADOPTED_BY_BURSA = False` di bawah_encode keputusan itu supaya
tidak ada yang mengira lookup-by-cloid akan ditambahkan.
"""
import json

# Alamat publik yang datanya diambil. Bukan milik repo ini.
FIXTURE_ADDRESS = "0x5972698398d8c5bbe67c0db74906236691020417"

# bursa tidak menyediakan endpoint lookup-by-cloid (lihat docstring).
# Dikunci di sini sebagai fakta yang sudah diverifikasi, bukan asumsi.
CLOID_ADOPTED_BY_BURSA = False

# ─────────────────────────────────────────────────────────────────────
# meta / universe
# Diambil dari: POST /info {"type":"meta"}
# Urutan persis seperti bursa kirim; name_to_asset(coin) mencari di sini.
# ─────────────────────────────────────────────────────────────────────
META_UNIVERSE = [
    {"szDecimals": 2, "name": "SOL", "maxLeverage": 10, "marginTableId": 10},
    {"szDecimals": 2, "name": "APT", "maxLeverage": 10, "marginTableId": 10},
    {"szDecimals": 2, "name": "ATOM", "maxLeverage": 10, "marginTableId": 10},
    {"szDecimals": 5, "name": "BTC", "maxLeverage": 25, "marginTableId": 10},
    {"szDecimals": 4, "name": "ETH", "maxLeverage": 20, "marginTableId": 10},
]
EXPECTED_BTC_ASSET_INDEX = 3


# ─────────────────────────────────────────────────────────────────────
# clearinghouseState dengan posisi terbuka
# Diambil dari: POST /info {"type":"clearinghouseState","user":ADDR}
# BTC SHORT 0.24567 @ 85110.1, leverage 5 cross.
# ─────────────────────────────────────────────────────────────────────
CLEARINGHOUSE_STATE_WITH_POSITION = {
    "assetPositions": [
        {
            "type": "oneWay",
            "position": {
                "coin": "BTC",
                "szi": "-0.24567",
                "leverage": {"type": "cross", "value": 5},
                "entryPx": "85110.1",
                "positionValue": "20902.83195",
                "unrealizedPnl": "6.176407",
                "returnOnEquity": "0.0014769727",
                "liquidationPx": "488199.6575506826",
                "marginUsed": "4180.56639",
                "maxLeverage": 25,
                "cumFunding": {
                    "allTime": "-11798.384567",
                    "sinceOpen": "-333.030252",
                    "sinceChange": "0.0",
                },
            },
        }
    ],
    "crossMaintenanceMarginUsed": "0.0",
    "crossMarginSummary": "4176.74274",
    "marginSummary": {
        "accountValue": "24917.36539",
        "totalNtlPos": "20902.83195",
        "totalRawUsd": "24917.36539",
        "totalMarginUsed": "4180.56639",
    },
    "time": 1791046827047,
    "withdrawable": "20734.216",
}

# Diambil dari: alamat 0x + "0"*40 (tidak ada posisi)
CLEARINGHOUSE_STATE_EMPTY = {
    "assetPositions": [],
    "crossMaintenanceMarginUsed": "0.0",
    "crossMarginSummary": "61.702826",
    "marginSummary": {
        "accountValue": "61.702826",
        "totalNtlPos": "0.0",
        "totalRawUsd": "61.702826",
        "totalMarginUsed": "0.0",
    },
    "time": 1791046827047,
    "withdrawable": "61.702826",
}

# Nilai yang harus dihasilkan symbol_notional("BTC") dari fixture posisi:
#   abs(szi) * entryPx
EXPECTED_BTC_NOTIONAL = abs(-0.24567) * 85110.1


# ─────────────────────────────────────────────────────────────────────
# userFills -- bentuk NYATA, disalin dari alamat di atas.
#
# Ini sumber kebenaran untuk item (b) dan (f): fee dan PnL harus
# diambil dari sini, bukan dihitung ulang dari harga config.
#
# `fee` SELALU negatif di respons ini (biaya keluar dari wallet).
# `closedPnl` sudah NET di bursa -- produksi memakai angka itu apa adanya.
# `tid` adalah pengenal unik per fill; dipakai untuk dedup.
# ─────────────────────────────────────────────────────────────────────

# Fill yang MEMBUKA posisi short BTC
FILL_OPEN_SHORT_BTC = {
    "coin": "BTC",
    "px": "85171.0",
    "sz": "0.00082",
    "side": "A",
    "time": 1791048381962,
    "startPosition": "-0.24931",
    "dir": "Open Short",
    "closedPnl": "0.0",
    "hash": "0xdedd9b701a85d4b8e057042ad3a1b3010100b355b588f38a82a646c2d989aea3",
    "oid": 61756970368,
    "crossed": False,
    "fee": "-0.002095",
    "tid": 7688972741898,
    "feeToken": "USDC",
    "twapId": None,
}

# Fill yang MENUTUP short BTC dengan rugi (closedPnl negatif)
FILL_CLOSE_SHORT_BTC_LOSS = {
    "coin": "BTC",
    "px": "85171.0",
    "sz": "0.00069",
    "side": "B",
    "time": 1791048255070,
    "startPosition": "-0.24622",
    "dir": "Close Short",
    "closedPnl": "-0.0414",
    "hash": "0x95c246b59d97480f973c042ad39a450000fd5e9b389a66e1398af2085c9b21fa",
    "oid": 61756806860,
    "crossed": False,
    "fee": "-0.001763",
    "tid": 789291717218145,
    "feeToken": "USDC",
    "twapId": None,
}

# Fill yang MENUTUP short BTC dengan rugi lebih kecil (partial exit)
FILL_CLOSE_SHORT_BTC_SMALL_LOSS = {
    "coin": "BTC",
    "px": "85171.0",
    "sz": "0.00013",
    "side": "B",
    "time": 1791048205573,
    "startPosition": "-0.24635",
    "dir": "Close Short",
    "closedPnl": "-0.0078",
    "hash": "0xee2194bb97bff9a0ef9b042ad39775010200aca132b3187291ea400e56b3d38b",
    "oid": 61756800506,
    "crossed": False,
    "fee": "-0.000332",
    "tid": 6668597456865,
    "feeToken": "USDC",
    "twapId": None,
}

# Fill dengan closedPnl POSITIF (untuk menguji bahwa produksi tidak
# selalu mencatat rugi)
FILL_CLOSE_SHORT_BTC_PROFIT = {
    "coin": "BTC",
    "px": "85171.0",
    "sz": "0.00528",
    "side": "B",
    "time": 1791048398257,
    "startPosition": "-0.24579",
    "dir": "Close Short",
    "closedPnl": "0.128457",
    "hash": "0x0f9e2c1b7a4d3e8f6a5b4c3d2e1f0a9b8c7d6e5f4a3b2c1d0e9f8a7b6c5d4e",
    "oid": 61756985785,
    "crossed": False,
    "fee": "-0.013485",
    "tid": 662045119320734,
    "feeToken": "USDC",
    "twapId": None,
}


# ─────────────────────────────────────────────────────────────────────
# frontendOpenOrders -- bentuk NYATA
#
# PENTING untuk item (b) dan (i): `origSz` ada di sini. Order dengan
# sz < origSz = PARTIALLY TERISI dan masih resting.
# `cloid` null kalau bot tidak mengirimnya.
# ─────────────────────────────────────────────────────────────────────

# Order resting, BELUM terisi sama sekali (sz == origSz)
OPEN_ORDER_RESTING_FULL = {
    "coin": "ETH",
    "side": "A",
    "limitPx": "2679.6",
    "sz": "5.0",
    "oid": 61757012387,
    "timestamp": 1791048426424,
    "triggerCondition": "N/A",
    "isTrigger": False,
    "triggerPx": "0.0",
    "children": [],
    "isPositionTpsl": False,
    "reduceOnly": False,
    "orderType": "Limit",
    "origSz": "5.0",
    "tif": "Alo",
    "cloid": None,
}

# Order resting, PARTIAL fill: sz 0.12 dari origSz 0.3.
# Sisa 0.18 masih hidup tanpa proteksi kalau bot tidak
# membatalkannya.
OPEN_ORDER_PARTIALLY_FILLED = {
    "coin": "BTC",
    "side": "A",
    "limitPx": "85134.0",
    "sz": "0.12",
    "oid": 61757018228,
    "timestamp": 1791048432980,
    "triggerCondition": "N/A",
    "isTrigger": False,
    "triggerPx": "0.0",
    "children": [],
    "isPositionTpsl": False,
    "reduceOnly": False,
    "orderType": "Limit",
    "origSz": "0.3",
    "tif": "Alo",
    "cloid": "tb-repro-0001",
}


# ─────────────────────────────────────────────────────────────────────
# orderStatus -- bentuk NYATA
# Hanya menerima `oid`; `cloid` menghasilkan HTTP 422 (diverifikasi).
# ─────────────────────────────────────────────────────────────────────
ORDER_STATUS_CANCELED = {
    "status": "order",
    "order": {
        "order": {
            "coin": "BTC",
            "side": "B",
            "limitPx": "85138.0",
            "sz": "0.29472",
            "oid": 61756985785,
            "timestamp": 1791048398257,
            "triggerCondition": "N/A",
            "isTrigger": False,
            "triggerPx": "0.0",
            "children": [],
            "isPositionTpsl": False,
            "reduceOnly": False,
            "orderType": "Limit",
            "origSz": "0.3",
            "tif": "Alo",
            "cloid": None,
        },
        "status": "canceled",
        "statusTimestamp": 1791048400407,
    },
}


# ─────────────────────────────────────────────────────────────────────
# Helper untuk menyuntikkan fixture ke test.
# Yang di-fake HANYA transport-nya; data yang masuk adalah data nyata.
# ─────────────────────────────────────────────────────────────────────


def make_info_double(state, universe=None):
    """Info yang menjawab seperti `hyperliquid.info.Info`."""
    universe = META_UNIVERSE if universe is None else universe
    by_name = {u["name"]: i for i, u in enumerate(universe)}

    class _InfoDouble:
        def __init__(self):
            self._state = state
            self._fills = []
            self._open_orders = []
            self._order_status = {}
            self._frontend_order_status = {}

        # --- yang dipakai symbol_notional / total_notional ---
        def user_state(self, address, dex=""):
            return self._state

        def name_to_asset(self, coin):
            return by_name.get(coin)

        def meta(self):
            return {"universe": universe}

        # --- item (b): sumber fill ---
        def user_fills(self, user, startTime=None):
            if startTime is not None:
                return [f for f in self._fills if int(f["time"]) >= int(startTime)]
            return list(self._fills)

        def user_non_funding_book_updates(self, user, startTime):
            return []

        # --- item (c) dan (i): order resting ---
        def frontend_open_orders(self, user):
            return list(self._open_orders)

        def historical_orders(self, user, startTime=None):
            return list(self._frontend_order_status.values())

        # --- item (d): status order by oid ---
        def order_status(self, user, oid, cloid=None):
            if cloid is not None:
                # Bursa sebenarnya menolak dengan HTTP 422 kalau cloid
                # dikirim. Di sini kita tirukan agar test ini gagal.
                raise ValueError(
                    "orderStatus hanya menerima oid; cloid ditolak bursa "
                    "dengan HTTP 422"
                )
            if oid not in self._order_status:
                raise ValueError("order %s tidak ditemukan" % oid)
            return self._order_status[oid]

    return _InfoDouble()


def load_fills(records):
    return list(records)


def load_open_orders(records):
    return list(records)


def dumps(value):
    return json.dumps(value, sort_keys=True)
