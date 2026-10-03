"""
Fixture respons NYATA dari API publik Hyperliquid testnet.

Semua isi di file ini disalin apa adanya dari panggilan read-only ke
`https://api.hyperliquid-testnet.xyz/info` pada 2026-10-03. Tidak ada
order yang dikirim, tidak ada kredensial yang dipakai, tidak ada akun
yang milik repository ini.

APA YANG TERSIMPAN DI SINI
--------------------------
`CLEARINGHOUSE_STATE_WITH_POSITION` — `clearinghouseState` milik alamat
publik yang kebetulan punya posisi BTC SHORT terbuka. Ini yang dipakai
test live supaya bentuk datanya **benar**, bukan tebakan.

Dua fakta yang HANYA bisa diketahui dari respons nyata, bukan dari
docstring SDK:

1. `position.coin` adalah STRING, bukan integer.
   Docstring SDK memang menuliskan `coin: str`, tapi implementasi
   produksi di `trading/live/client.py:219` memperlakukannya sebagai
   integer: `int(pos.get("coin", -1))`. Itu yang membuat
   `symbol_notional()` melempar `ValueError`.

2. SEMUA angka datang sebagai STRING desimal, bukan float JSON.
   `szi` = "-0.24567", `entryPx` = "85110.1". Kode produksi memakai
   `float(...)` di sekitarannya, jadi ini konsisten -- tapi test yang
   memakai angka float langsung akan salah-merasa aman.

3. Skema `assetPositions[].position.leverage` berupa OBJEK
   (`{"type": "cross", "value": 5}`), bukan integer. Ada juga
   `maxLeverage` dan `cumFunding` yang tidak ada di docstring SDK.

CATATAN TENTANG PAYLOAD
----------------------
Payload yang benar adalah `{"type": "clearinghouseState", "user": addr}`.
`{"type": "userState", ...}` mengembalikan HTTP 422 -- nama tipenya salah
walaupun docstring SDK menyebutnya `user_state`. Ini terverifikasi
terhadap API sungguhan, bukan disimpulkan.
"""

# Alamat publik yang posisinya diambil. Bukan milik repo ini.
FIXTURE_ADDRESS = "0x5972698398d8c5bbe67c0db74906236691020417"

# Diambil dari: POST /info {"type":"clearinghouseState","user": FIXTURE_ADDRESS}
# -> 2026-10-03. BTC SHORT 0.24567 @ 85110.1, leverage 5 cross.
CLEARINGHOUSE_STATE_WITH_POSITION = {
    "assetPositions": [
        {
            "type": "oneWay",
            "position": {
                "coin": "BTC",              # <-- STRING. Ini inti bug client.py:219
                "szi": "-0.24567",          # <-- string desimal, bukan float
                "leverage": {"type": "cross", "value": 5},
                "entryPx": "85110.1",        # <-- string desimal
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

# Diambil dari: POST /info {"type":"clearinghouseState","user":"0x"+"0"*40}
# Alamat tanpa posisi. Dipakai untuk menguji jalur "tidak ada posisi".
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

# Diambil dari: POST /info {"type":"meta"}
# Dipakai supaya test memakai indeks koin yang BENAR dan tidak hardcode.
# Urutan persis seperti yang bursa kirim -- name_to_asset(coin) mencari
# posisi di list ini.
META_UNIVERSE = [
    {"name": "SOL", "szDecimals": 2, "maxLeverage": 20, "onlyIsolated": False},
    {"name": "APT", "szDecimals": 2, "maxLeverage": 20, "onlyIsolated": False},
    {"name": "ATOM", "szDecimals": 2, "maxLeverage": 20, "onlyIsolated": False},
    {"name": "BTC", "szDecimals": 5, "maxLeverage": 25, "onlyIsolated": False},
    {"name": "ETH", "szDecimals": 4, "maxLeverage": 20, "onlyIsolated": False},
]

# Turunan yang harus benar kalau fixture dipakai sebagai jawaban API.
# Dicari dari META_UNIVERSE di atas: BTC ada di indeks 3.
EXPECTED_BTC_ASSET_INDEX = 3

# Nilai yang HARUS dihasilkan symbol_notional("BTC") dari fixture di atas:
#   abs(szi) * float(entryPx) = 0.24567 * 85110.1
EXPECTED_BTC_NOTIONAL = abs(-0.24567) * 85110.1


def make_info_double(position_state, meta_universe=None):
    """
    Objek yang Answers persis seperti `hyperliquid.info.Info` untuk dua
    method yang dipakai `LiveExchange.symbol_notional`:
      - .user_state(address)  -> clearinghouseState
      - .name_to_asset(coin)  -> indeks di universe

    Ini BUKAN stub untuk_logika yang sedang diuji. Yang diuji adalah
    `LiveExchange.symbol_notional` -- yaitu bagaimana ia membaca field
    dari respons. `get_account_state()` hanya meneruskan, jadi menyediakannya
    lewat objek ini等同于 memanggil API sungguhan minus jaringan.

    Data yang dikembalikan adalah fixture rekaman, bukan karangan.
    """
    universe = META_UNIVERSE if meta_universe is None else meta_universe
    name_to_index = {u["name"]: i for i, u in enumerate(universe)}

    class _InfoDouble:
        def __init__(self):
            self._state = position_state

        def user_state(self, address, dex=""):
            return self._state

        def name_to_asset(self, coin):
            return name_to_index.get(coin)

        def meta(self):
            return {"universe": universe}

    return _InfoDouble()


def make_exchange_with_state(position_state, coin, meta_universe=None):
    """
    LiveExchange yang memakai Info_double di atas.

    sengaja TIDAK menyalakan SDK Exchange/wallet -- supaya test ini
    tidak pernah menyentuh kredensial apa pun. Yang diuji cuma jalur
    baca `symbol_notional`.
    """
    from trading.live.client import LiveExchange

    ex = LiveExchange.__new__(LiveExchange)   # bypass __init__: tanpa key
    ex.testnet = True
    ex._account_address = None
    ex._rules = None
    ex.base_url = "https://api.hyperliquid-testnet.xyz/info"
    ex.info = make_info_double(position_state, meta_universe)
    # .exchange dipakai place_limit_order dll; di sini tidak akan terpakai
    ex.exchange = None
    ex.wallet = None
    ex.address = FIXTURE_ADDRESS
    ex.query_address = FIXTURE_ADDRESS
    return ex
