# 0XF3CE25 — Dokumentasi Teknis

Dokumen tunggal untuk seluruh sistem. Dibaca baris-per-baris dari source
pada commit `e49011a`. Setiap angka di sini diverifikasi terhadap file yang
disebutnya — bukan dari dokumen sebelumnya, bukan dari ingatan.

Dokumen ini menggantikan `docs/technical/01..06` dan `docs/context/*` yang
sudah dihapus.

---

## Daftar isi

| # | Bagian |
|---|---|
| 1 | [Apa ini dan keadaannya sekarang](#1-apa-ini-dan-keadaannya-sekarang) |
| 2 | [Cara menjalankan](#2-cara-menjalankan) |
| 3 | [Arsitektur](#3-arsitektur) |
| 4 | [Alur data](#4-alur-data-end-to-end) |
| 5 | [Konfigurasi](#5-konfigurasi) |
| 6 | [Ekonomi: risiko, biaya, P&L](#6-ekonomi-risiko-biaya-dan-pnl) |
| 7 | [Paper path](#7-paper-path) |
| 8 | [Live path](#8-live-path) |
| 9 | [Data layer](#9-data-layer) |
| 10 | [Analysis layer](#10-analysis-layer) |
| 11 | [Agen](#11-agen) |
| 12 | [Dashboard](#12-dashboard) |
| 13 | [Ekstensi native C++](#13-ekstensi-native-c) |
| 14 | [Machine learning](#14-machine-learning) |
| 15 | [Riset](#15-riset) |
| 16 | [Test suite](#16-test-suite) |
| 17 | [Daftar defect](#17-daftar-defect) |
| 18 | [Yang tidak diverifikasi](#18-yang-tidak-diverifikasi) |

---

## 1. Apa ini dan keadaannya sekarang

Sistem trading bot kripto perpetual dengan dua jalur eksekusi (paper
simulasi dan live ke bursa Hyperliquid), 5 agen otonom, ensemble arah
multi-agen, dan dashboard web real-time.

### Angka repo saat ini

`data_store/trading_bot.db` — 30.5 MB, semua `mode='paper'`:

| Tabel | Baris |
|---|---|
| `candles` | 20.989 (1m 12.152 · 5m 7.078 · 1h 1.759) |
| `agent_logs` | 10.782 |
| `signals` | 8.686 |
| `trades` | 503 |
| `positions` | 253 (250 CLOSED, 3 OPEN) |
| `direction_snapshots` | 1.499 |
| `balance_history` | 197 |
| `news` | 118 |
| `account` | 1 |
| `macro_data` | **0** |
| `order_book_features` | **8.180 — sisa schema lama, tidak di-prune** |

`data_store/order_book.db` — 18.9 MB, terpisah:
105.040 baris `order_book_features` (24.98 jam, 10 simbol, 20 level/sisi
selalu penuh). `order_book_raw` = 0 karena `keep_raw=False`.

`data_store/historical_candles.db` — 19 MB, 160.989 baris `hist_candles`:
1h 105.044 (21 simbol, 2026-03-09 → 2026-10-03), 15m 15.313 / 5m 20.104 /
1m 20.528 (4 simbol saja).

### Performa terukur

Dari 250 posisi tertutup (`realized_pnl` sudah net dari semua biaya):

```
win rate          30.0%  (75 dari 250)
profit factor     0.213
net P&L           -139.29 USDT  (dari 10.000, -1.39%)
rata-rata winner  +0.5026 USDT
rata-rata loser   -1.0113 USDT
rasio win:loss    0.497 : 1
win rate impas    66.8%  ← butuh ini, aktual 30.0%
expectancy/trade  -0.557 USDT
fee terbayar      54.98 USDT total (buka 27.64 + tutup 27.34)
gross sebelum fee -84.31 USDT  → biaya memakan 65% dari gross
```

Per `close_reason`:

| Alasan | n | win | total | rata-rata | terburuk |
|---|---|---|---|---|---|
| `SL_HIT` | 188 | 46 (24.5%) | -127.78 | -0.6797 | -3.3677 |
| `SCALP_EXPIRED` | 34 | 1 (2.9%) | -47.89 | -1.4085 | -15.8755 |
| `SCALP_TP` | 28 | 28 (100%) | +36.38 | +1.2993 | +1.6572 |

Semua posisi Stochastic: leverage 5 (bukan 10). Margin 47.43–49.99,
median 48.56. Tidak ada posisi LIQUIDATED.

**3 posisi masih OPEN** dengan umur 180.248–180.854 detik (~50 jam),
padahal `max_hold_seconds: 900`. Bot tidak berjalan sejak 2026-10-01, jadi
`_auto_close_expired` tidak pernah dipanggil lagi untuknya.

### Ringkasan jujur

- **Paper:** sistem berjalan, tidak crash, dan akuntansi internal konsisten
  (tidak ada uang tercipta dari nol). Tapi strategi yang dijalankan
  **menghapus uang**: PF 0.213 selama 250 trade.
- **Live:** belum pernah mengirim satu pun order nyata. Gerbang
  `SafetyGate` menolak semua order karena `TRADEBOT_LIVE` dan
  `TRADEBOT_LIVE_CONFIRMED` tidak pernah di-set oleh kode mana pun
  (§8.2). Tiga defect blocking ada di jalur itu (§8.7).
- **Efektif:** yang berjalan hari ini adalah sistem pencatat teliti untuk
  strategi yang terbukti rugi. Riset menemukan edge yang benar
  (cross-sectional momentum) dan mengimplementasikannya di
  `trading/cross_sectional.py` — **tapi modul itu tidak terhubung ke
  apa pun** (§17.1).

---

## 2. Cara menjalankan

```bash
pip install -r requirements.txt

# Paper (default, tanpa interaksi)
python run.py --paper

# Menu interaktif: simulasi / testnet / mainnet
python run.py

# Testnet / mainnet tanpa menu
python run.py --testnet
python run.py --live

# Preflight read-only live (tidak bisa mengirim apa pun)
python live_doctor.py --testnet

# Reset DB paper ke 10.000 USDT
python reset_paper_db.py

# Perbaiki candle rusak (DESTRUKTIF, menutup koneksi)
python run.py --repair-candles

# Test
python -m unittest discover tests   # 605 test, OK (skipped=4)
python -m pytest tests -q           # 599 passed, 4 skipped, 2 FAILED

# Latih ulang model ML
python -m ml.trainer

# Build ekstensi C++
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release
```

Exit code: `0` normal · `2` kegagalan konfigurasi live (kunci kosong,
health check gagal) · `1` `live_doctor` menemukan masalah.

### Environment variables

| Variabel | Dibaca di | Arti |
|---|---|---|
| `HYPERLIQUID_PRIVATE_KEY` | `safety.py:277`, `run.py:311` | private key. Kalau kosong, live tidak bisa jalan |
| `HYPERLIQUID_ACCOUNT_ADDRESS` | `run.py:317` | alamat API-wallet/subaccount yang memegang posisi |
| `TRADEBOT_LIVE` | `safety.py:195,334` | `"1"/"true"/"yes"` — syarat pertama live |
| `TRADEBOT_LIVE_CONFIRMED` | `safety.py:339` | syarat kedua live |
| `TRADEBOT_LIVE_KILL_SWITCH` | `safety.py:228,345` | 3-keadaan (lihat §8.4) |
| `TRADEBOT_KERNEL` | `microstructure.py:329` | `python` \| `cpp` \| kosong (auto) |
| `TRADEBOT_QUIET_STARTUP` | `logger.py:128` | `1` = console level WARNING selama boot **dan selamanya** (§17.9) |
| `HF_HUB_DISABLE_PROGRESS_BARS` | `logger.py:140` | di-set otomatis saat quiet |
| `TOKENIZERS_PARALLELISM` | `logger.py:142` | di-set default saat quiet |
| `TRADEBOT_BANNER` | `dashboard/app.py:112` | tampilkan banner Flask |

**Tidak ada env var atau config key untuk FRED API key.** `MacroFetcher`
dibangun tanpa argumen di `run.py:263`, jadi `fetch_fred_data()` selalu
mengembalikan `[]` dan tabel `macro_data` selalu kosong (§17.6).

---

## 3. Arsitektur

### Lapisan

```
run.py                          orkestrasi, boot, mode choice, shutdown
├── core/                       config · event bus · scheduler · logger · market store · kernel
├── data/                       akuisisi: Hyperliquid WS+REST, multi-tier OHLCV, berita, makro, sentimen
├── analysis/                   matematika sinyal (murni, tanpa I/O)
├── trading/                    risk · position · paper engine · cross-sectional
│   └── live/                   safety gate · client · engine · executor · TUI · console
├── agents/                     news · analysis · decision · execution · direction ensemble
├── database/                   skema + repository (semua SQL)
├── dashboard/                  Dash/Plotly HUD
├── ml/                         trainer + predictor
└── cpp/                        kernel C++20 + parser JSON tulis-tangan
```

Aturan impor yang dipegang: `core/` tidak impor apa pun dari layer lain ·
`analysis/` hanya boleh baca `core/` dan `market_store` · `trading/`
tidak tahu apa-apa tentang HTTP · `agents/` hanya bicara lewat event bus
dan antarmuka engine.

### Thread dan task

Tasks asyncio yang dibuat di loop berjalan:

| Task | Interval | Membuat |
|---|---|---|
| `price_feed.price_update_loop` | 1.5 s | `run.py:911` |
| `_execution_loop` | **0.3 s** | `run.py:913` |
| `_candle_refresh_loop` | 30 s | `run.py:915` |
| `_telemetry_loop` | 30 s (setelah 25 s) | `run.py:917` |
| `_maintenance_loop` | 3600 s | `run.py:924` |
| `OrderBookRecorder._loop` | 1.0 s | `run.py:958` |
| `LiveEngine.run_loop` | 5.0 s (live saja) | `run.py:340` |
| `dashboard.run_dashboard` | thread terpisah | `run.py:881` |

Scheduler APScheduler mendaftarkan 11 job (`run.py:552-650`):
`refresh_top_volume` 3600 · `news_agent` 300/120 · `analysis_agent` 15/10 ·
`decision_agent` 3/2 · `direction_ensemble` 5 · `direction_snapshot_prune`
3600 · `agent_log_prune` 1800 · `macro_update` 21600 · `finbert_batch` 900 ·
`balance_snapshot` 60 · `_market_check` 60.

> **Catatan ritme:** `decision_agent` jalan tiap 3 detik, tapi pemantauan
> posisi (`ExecutionAgent.check_positions`) jalan tiap **0.3** detik — 10×
> lebih sering. `_protect_breakeven`, `_scalp_take_profit`, dan
> `_auto_close_expired` semuanya ada di loop 0.3 dtk itu
> (`execution_agent.py:177-192`).

### Yang DENYATTA tapi tidak ada

`core/event_bus.py:92-103` mendeklarasikan 10 channel; **5 tidak pernah
dipublish atau disubscribe**:

| Channel | Status |
|---|---|
| `PRICE_UPDATE` | aktif |
| `NEWS_SENTIMENT` | aktif |
| `MARKET_ANALYSIS` | aktif |
| `TRADE_DECISION` | aktif |
| `POSITION_UPDATE` | aktif |
| `TRADE_EXECUTED` | dipublish, **tidak ada subscriber** |
| `BALANCE_UPDATE` | **konstanta mati** |
| `AGENT_LOG` | **konstanta mati** |
| `SYSTEM_EVENT` | **konstanta mati** |
| `DIRECTION_ENSEMBLE` | **konstanta mati** — ensemble menulis ke SQLite, bukan bus |

`EventBus.get_channel_stats()` tidak dilindungi lock
(`event_bus.py:86-88`), dan tidak ada kode yang memanggil `unsubscribe`.

---

## 4. Alur data end-to-end

```
Hyperliquid WS  wss://api.hyperliquid.xyz/ws
  allMids    → market_store.set_price()           → run.py:911 price_update_loop
  l2Book     → market_store.set_order_book()
             → microstructure.ingest_l2()        → kernel OFI/depth
  candle 1m  → market_store.set_live_candle()
  activeAssetCtx → set_funding(), set_open_interest()
  trades     → set_recent_trades()
        │  (semua di _handle_message, via asyncio.to_thread per frame)
        │
        ├──► DirectionEnsembleAgent (5 s)
        │      4 spesialis → aggregate() → INSERT direction_snapshots
        │
        ├──► DecisionAgent (3 s)  baca snapshot TERBARU
        │      freshness <20 s · conf ≥0.40 · spread ≤0.06%
        │      → TRADE_DECISION
        │
        ├──► ExecutionAgent (0.3 s)
        │      → engine.execute_order()
        │      → PaperTradingEngine / LiveExecutor
        │      → PositionManager → SQLite
        │
        └──► Dashboard (500 ms, 19 callback)
               baca DB + market_store yang sama
```

**Titik paling penting:** `DecisionAgent` dan callback HUD membaca tabel
`direction_snapshots` yang **sama**. Angka yang memicu entry identik dengan
angka yang tampil di layar. `DirectionEnsembleAgent` adalah satu-satunya
penulis tabel itu (`direction_agents.py:406`).

### Jalur paper vs live

Keduanya memakai antarmuka yang sama sehingga `ExecutionAgent` tidak tahu
mana yang aktif (`run.py:411-420`):

| Aspek | Paper | Live |
|---|---|---|
| Engine | `trading/paper_engine.py` | `trading/live/executor.py` |
| Harga fill | `fill_cost.fill_price_after_cost()` | `place_limit_order` GTC |
| Fee | Dihitung dari `config.fees` | Dihitung dari `config.fees` (**bukan dari bursa**) |
| SL/TP | Dipantau Python tiap 0.3 s | Trigger di sisi bursa + dipantau Python |
| DB | `mode='paper'` | `mode='live'` |
| Balance | `account.balance` berubah | **`account.balance` tidak pernah disentuh** |
| Gating | `RiskManager.validate_trade()` | `SafetyGate.can_send()` |

Divergensi penting: paper **membebankan** spread+impact pada fill;
live **tidak** — dia mengandalkan limit order. Dan live **tidak** pernah
mengubah `account.balance` sama sekali (`executor.py` tidak pernah memanggil
`apply_balance_delta`), jadi HUD menampilkan equity paper yang benar
saja dan equity live yang nol.

### Bentuk state in-memory

`core/market_store.py` — satu dict per jenis data, tanpa lock:

```python
_tickers      Dict[str, dict]
_order_books  Dict[str, dict]      # butuh bids DAN asks non-kosong
_last_prices  Dict[str, float]     # hanya p > 0
_price_ts     Dict[str, float]
_price_history Dict[str, deque]    # maxlen=240
_live_candles Dict[str, dict]      # butuh close truthy
_funding      Dict[str, float]
_open_interest Dict[str, float]
_recent_trades Dict[str, list]     # dipotong 50
```

Docstring `market_store.py:23` mengklaim *"Store thread-safe sederhana"*,
tapi file itu **tidak mengimpor `threading` sama sekali**. Semua writer
adalah `asyncio.to_thread` (`hyperliquid_feed.py:534`, ratusan frame L2 per
detik), semua reader termasuk callback Dash. `set_price` (`:39-53`) menulis
empat struktur dalam 4 langkah non-atomik. Risikonya rendah sekarang
karena GIL membuat tiap dict-assignment atomik, tapi klaimnya tidakearned.

Pencocokan simbol: 8 dari 9 getter memakai kesetaraan **segmen base**
(`symbol.split("/")[0].split(":")[0].upper()`). Pengecualiannya
`get_ticker` (`:70`) masih memakai `base in k.upper()` — substring bebas,
bertentangan dengan docstring `get_price` sendiri.

---

## 5. Konfigurasi

### Lapisan konfigurasi

1. Default dataclass di `core/config.py`
2. `config.yaml` — ditimpa lewat `_apply_dict`
3. Override per-key scalar di `run.py:113` (`config.symbols`)
4. **Tidak ada** env override di `core/config.py` (`import os` di `:7` tidak terpakai)

### Pemetaan `config.yaml`

| Blok | Target | Mekanisme |
|---|---|---|
| `account` | `config.account` | `_apply_dict` |
| `symbols` | `config.symbols` | assign langsung |
| `scanning`, `scalping`, `dynamic_tp_sl`, `exchange`, `risk`, `fees` | sesuai dataclass | `_apply_dict` |
| `ensemble` + `ensemble.agents.*` | dua level | `pop("agents")` lalu per-agen |
| `live` | `config.live` lalu **`enabled` dipaksa `False`** | `config.py:709-711` |
| `agent_intervals`, `us_market`, `indicators`, `dashboard`, `logging` | sesuai dataclass | `_apply_dict` |
| `database.path` | `config.database_path` | langsung |
| `database.snapshot_*` | `AppConfig` | filter `startswith("snapshot_")` (`:746`) |
| `news_sources.*` | `config.news_rss`, `cryptopanic_url`, `cryptopanic_token` | langsung |

**Tidak ada blok `live:` di `config.yaml`** — sudah diverifikasi. Dan
`run.py:310` membangun `LiveConfig()` dari default dataclass secara
terpisah, jadi semua batas live berasal dari `core/config.py:305-369`,
bukan dari YAML.

### Tiga validator boot

Jalan di `load_config` (`:761-763`), gagal → `ValueError` → boot mati.

**`_validate_scalping_economics`** (`:943`) — 6 aturan:
`breakeven_trigger_pct < min_profit_pct` · `net_tp > net_sl` setelah fee ·
`breakeven_offset_pct ≥ roundtrip` · `min_confidence ≤ reversal_close_threshold < 1.0` ·
`stale_tick_window_seconds > max_tick_age_seconds` · `stale_tick_min_samples ≥ 2`.

**`_validate_ensemble_config`** (`:866`) — 8 aturan: minimal satu agen aktif,
total bobot > 0, bobot non-negatif, `shrinkage_delta ∈ (0,1]`,
`min_prob ∈ (0,0.5)`, `agreement_bonus ≥ 0`, `max_snapshot_age_seconds > 0`,
dan **`max_snapshot_age_seconds ≥ 2 × interval_seconds`**,
`diffusion_points ≥ 2`.

**`_validate_dynamic_tp_sl`** (`:767`) — 8 aturan termasuk
`min_risk_reward > 1.0` dan `max_breakeven_win_rate ∈ (0.5,1.0)`.

### Nilai yang ditulis tapi tidak pernah dibaca

| Key | Nasib |
|---|---|
| `database.agent_log_prune_interval`, `agent_log_keep` | filter `:746` hanya meneruskan `snapshot_*` → **dibuang diam-diam** (dokumentasi di `:740-744`). Kebetulan sama dengan default dataclass |
| `exchange.*` (3 key) | `ExchangeConfig` nol consumer |
| `agent_intervals.funding_rate` | nol job mendaftarkannya |
| `dashboard.update_interval` | nol consumer Python |
| `scalping.orderbook_imbalance_threshold` | nol consumer |
| 10 field duplikat di `DynamicTpSlConfig` | bayangan `ScalpingConfig`, dibaca dari yang satu saja |
| `live.use_exchange_side_tpsl` | nol referensi di luar config.py, walau komentar `:356-360` bilang "WAJIB true" |
| `live.reconciliation_tolerance_days` | nol referensi |
| `risk.max_drawdown` | dibaca `RiskManager`, tapi hanya di `validate_trade` |
| `TRADEBOT_VERBOSE_STARTUP` | didokumentasikan di `logger.py:117,233`, dibaca **tidak ada kode** |

### Peringatan loader

`_apply_dict` (`:497-540`) memberi tahu, tidak diam:
- key tak dikenal → "diabaikan — nilai operator tidak pernah dipakai"
- tipe salah → "nilai dipakai apa adanya … Periksa config.yaml"
- `scalping.enabled` dinonaktifkan → `_warn_missing_config_file` cetak
  banner stderr yang merinci selisih dataclass-vs-YAML.

---

## 6. Ekonomi: risiko, biaya, dan P&L

### Model biaya — `trading/fill_cost.py`

Ini **satu-satunya** tempat biaya eksekusi dihitung. `paper_engine`,
`position_manager`, dan `live` semuanya funnel ke sini.

```
_floor   = FILL_HALF_SPREAD_FLOOR_BY_SYMBOL.get(base, 0.0003)
observed = (best_ask - best_bid) / (2 * mid)     # hanya kalau book segar & tidak rusak
half     = max(observed, _floor)                 # max(), BUKAN min()
impact   = 0.0001                                 # konstanta, tidak pernah baca book
total    = min(half + impact, 0.0050)
fill     = ref_price * (1 ± total)                # BUY bayar lebih, SELL terima kurang
```

Tiga aturan yang tidak boleh dilanggar:

1. **Angka di bawah adalah LANTAI, bukan target.** Book yang tidak pernah
   dikedaluwarsi, jadi jalur live hanya boleh MENAMBAH biaya. Book basi,
   book tanpa stempel, sentinel harga 0, book spread sempit — semua jatuh
   ke lantai.
2. **`side` adalah sisi FILL, bukan sisi posisi.** Buka LONG = BUY, tutup
   SHORT = BUY. Salah ingat membalik tanda biaya.
3. **Impact SELALU konstanta.** Suku impact berbasis depth butuh pembagian
   dengan kedalaman book, dan kasus nolnya adalah crash yang pernah terjadi.

### Biaya round-trip terukur

Fee Hyperliquid base tier (`config.yaml:58-60`): maker 0.00015, taker
0.00045. **Semua fill di produksi dikenai TAKER** — tidak ada jalur maker
(`position_manager.py:94,237,500` semuanya hardcode `"TAKER"`;
`Order.order_type` tidak pernah dibaca engine mana pun).

| Simbol | half-spread | slippage RT | fee RT | **total RT** | % dari SL 0.40% |
|---|---|---|---|---|---|
| BTC | 0.12 bps | 2.24 | 9.00 | **11.24 bps** | 28.1% |
| HYPE | 0.12 | 2.24 | 9.00 | 11.24 | 28.1% |
| ETH | 0.37 | 2.74 | 9.00 | 11.74 | 29.4% |
| XRP | 0.66 | 3.32 | 9.00 | 12.32 | 30.8% |
| ZEC | 0.70 | 3.40 | 9.00 | 12.40 | 31.0% |
| SOL | 0.84 | 3.68 | 9.00 | 12.68 | 31.7% |
| NEAR | 1.13 | 4.26 | 9.00 | 13.26 | 33.1% |
| LIT | 1.28 | 4.56 | 9.00 | 13.56 | 33.9% |
| PUMP | 1.75 | 5.50 | 9.00 | 14.50 | 36.2% |
| ENA | 1.97 | 5.94 | 9.00 | 14.94 | 37.4% |
| *(di luar daftar)* | 3.00 | 8.00 | 9.00 | **17.00** | 42.5% |

Angka-angka ini **terukur**, bukan asumsi — dari 105.040 snapshot order
book nyata di `data_store/order_book.db` (rata-rata spread per simbol).
Lantai global 3 bps dipakai untuk simbol yang tidak ada di dict; itu lebih
konservatif, bukan lebih optimis.

> Lima dari 10 simbol default `config.yaml` (BNB, DOGE, ADA, AVAX, LINK)
> tidak punya entri dan jatuh ke lantai global 3 bps.

### Funding

`funding_cost()` (`:280`) — **periode settlement Hyperliquid adalah 1 JAM,
bukan 8** seperti tertulis di `config.yaml:72`. Nilai `3600.0` di
`fill_cost.py:264` sudah benar; yang salah adalah interpretasi
`agent_intervals.funding_rate` (nilai 28800 = seberapa SERING di-refresh,
bukan periode settlement).

```
settled = ceil(held_seconds / 3600)         # pecahan dibayar PENUH
signed  = rate if LONG else -rate
cost    = notional * signed * settled
```

Dihitung saat tutup, bukan akrual per periode — supaya crash di tengah
periode tidak menyebabkan biaya hilang atau dibayar dua kali.

### Risk/reward

Dengan config sekarang (`sl 0.40%`, `tp 0.60%`, roundtrip 0.09%):

```
net_tp = 0.60% - 0.09% = 0.5100%
net_sl = 0.40% + 0.09% = 0.4900%
R:R bersih        = 1 : 1.04
win rate impas     = 49.0%
```

**Margin hanya 2.0 bps.** `volatilities gate` (§10.4) butuh
`sl < 0.105%` untuk menyala; `min_sl_pct` = 0.400% — **3.8× lebih tinggi**.
Gate itu mati secara struktural, bukan karena kondisinya.

Dan angka riil Movement lebih buruk dari yang konfigurasi janjikan:
winner rata-rata **+0.50**, loser rata-rata **−1.01**. R:R.win:loss
ter Communities **1:2**, bukan 1:1.04. Untuk impas butuh **66.8%** win rate;
aktif 30.0%.

### Circuit breaker

Tiga, semua di `RiskManager.validate_trade` (`risk_manager.py:289`):

| Gate | Pemicu | Denominator |
|---|---|---|
| Margin | `margin_required > balance × 0.9` | saldo kas bebas |
| Jumlah posisi | `open_positions >= max_open_positions` (30) | — |
| Rugi harian | `abs(daily_pnl)/initial_balance ≥ 0.10` | **modal awal**, bukan kas |
| Drawdown | `(peak − equity)/peak ≥ 0.20` | **equity**, bukan kas |

Penyebut modal awal bukan kas itu benar dan penting: begitu margin
terkunci, kas mengecil, penyebut ikut turun — pelonggaran terjadi justru
di saat paling rugi.

`peak_balance` mengikuti **equity** (kas + margin + unrealized), bukan kas
bebas, karena kalau mengikuti kas setiap posisi terbuka menurunkan peak-nya
sendiri sehingga batas bergeser.

### Akuntansi

Setiap perubahan saldo lewat **satu** statement atomik:
`UPDATE account SET balance = balance + ? WHERE id = (SELECT MAX(id) FROM account)`
(`repository.py:438-444`). Tidak ada read-modify-write di jalur mana pun.

Penutupan posisi memakai klaim-tunggal:
`UPDATE positions SET status='CLOSED' ... WHERE id = ? AND status = 'OPEN'`,
returns `rowcount > 0` (`repository.py:149-171`). Dua pemanggil yang
berebut tidak bisa dua-duanya mengembalikan margin.

`realized_pnl` yang ditulis ke DB = `gross − fee_buka − fee_tutup − funding`
— **net dari semua biaya**. Formulasi ini sudah benar: fee buka sengaja
dibebankan sekali di `net_pnl` (agar win rate dan daily-loss breaker melihat
kebenaran) lalu **dikreditkan balik** saat saldo dikembalikan
(`position_manager.py:316`), karena fee itu sudah dipotong saat opening.

> **Tapi bukan satu transaksi.** `close_position` melakukan 6 commit
> berurutan (`:282`, `:297`, `:317`, `:331`, `:350`). Crash di antaranya
> meninggalkan posisi CLOSED dengan margin yang belum kembali.

**`get_trade_stats(mode=None)` punya parameter `mode` tapi tidak pernah
dipanggil dengan `mode=`** (`position_manager.py:330`,
`live/executor.py:833`). Jadi win rate dan profit factor di tabel `account`
mencampur paper dan live. `get_daily_realized_pnl()` (`:563-570`) juga
tanpa filter mode — circuit breaker daily menjumlahkan P&L keduanya.

---

## 7. Paper path

### 7.1 Order rejection ladder — jalur OPEN

Urutan persis di `PaperTradingEngine.execute_order` → `_execute_open`.
Setiap penolakan menulis `agent_logs` dengan `action='TRADE_REJECTED'`
supaya bisa direkonstruksi.

| # | Lokasi | Kondisi | Pesan |
|---|---|---|---|
| 0 | `paper_engine.py:222` | `get_price(symbol) is None` | `Harga {symbol} tidak tersedia` |
| 1 | `:448` | `repo.get_account()` falsy | `Akun belum diinisialisasi` |
| 2 | `:468-496` | qty ≤ 0 / leverage None / ≤ 0 / price ≤ 0 | `Order tidak valid: ` + join dari 4 kemungkinan |
| 3 | `:523` | `VolatilityGateError` | verbatim dari `volatility.py:388/400/406` |
| 4 | `:591` | tick guard | `Harga tidak layak eksekusi: ` + alasan |
| 5 | `:678` | `validate_trade` | `Ditolak: ` + join dari 4 breaker |
| 6 | `:713` | `open_position` → None | `Gagal membuka posisi: ` + diagnosis |

Gate #5 bisa memuat **empat** alasan sekaligus, karena `validate_trade`
mengumpulkan semuanya sebelum mengembalikan `allowed=False`.

Gate #6 punya tiga diagnosis berbeda (`_diagnose_open_failure`, `:403`):
akun belum ada · saldo tidak cukup (termasuk fee) · gagal simpan ke DB.
Tiga Cause itu dulu semuanya dilaporkan sebagai "saldo tidak cukup".

### 7.2 Guard kualitas tick

`_tick_quality_guard` (`:280`) — dua pemeriksaan, dari yang termurah:

1. **Usia tick** — `market_store.get_price_age(symbol) > max_tick_age_seconds` (1.5 s) → `"tick basi (X.XXs, batas 1.50s)"`
2. **Outlier vs median** — devsiasi harga dari median `stale_tick_min_samples` (5) tick terakhir dalam `stale_tick_window_seconds` (3.0 s). Ambang penyimpangan = `sl_pct` hasil gate, yaitu **≥ 0.40%**.

Guard memeriksa `fill_meta["ref_price"]` (harga pasar), **bukan** harga yang
sudah digeser slippage. Itu benar: guard slippage adalah validasi, bukan
penyesuaian. Kalau ia melihat harga fill, ia akan menolak order karena
simpangan yang ia sendiri buat.

**Fail-open yang disengaja:** data belum cukup → izinkan eksekusi. Menolak
order karena tidak punya data sejarah akan membuat bot diam persis di
detik-detik paling ramai.

### 7.3 Lifecycle posisi

```
open_position  → margin = qty×entry/leverage (Decimal, cast float)
                 → apply_balance_delta(-(margin+fee))     ← ATOMIK
                 → INSERT positions, INSERT trades
                 → publish POSITION_UPDATE

check_positions (tiap 0.3 s, per posisi, berurutan):
   1. update_position_pnl (unrealized, dari harga pasar bukan fill)
   2. likuidasi?  → _liquidate  (margin hilang + fee + funding)
   3. SL hit?    → close_position(reason='SL_HIT')
   4. TP hit?    → close_position(reason='TP_HIT')

close_position → fill = close_fill_price()   ← biaya di sisi yang benar
                 → fee TAKER dari harga fill
                 → funding dari held_seconds
                 → open_fee DIBACA dari tabel trades (bukan dihitung ulang)
                 → net = pnl − open_fee − fee − funding
                 → repo.close_position() klaim-tunggal → False = sudah diklaim orang
                 → INSERT trades CLOSE
                 → apply_balance_delta(margin + net + open_fee)  ← ATOMIK
                 → bump_peak_balance(equity)
                 → hitung MDD + Sharpe dari 500 baris equity_history
                 → UPDATE account stats
```

Setiap fee dihitung dari **harga fill**, bukan harga pasar: fee adalah
persentase dari notional, dan notional yang benar adalah yang benar-benar
dibayar.

`open_fee` dibaca dari DB, bukan dihitung ulang, karena tarif taker bisa
berubah di tengah posisi.

### 7.4 Tujuh jalur penutupan

| # | Pemicu | `close_reason` | Kapan |
|---|---|---|---|
| 1 | `update_positions` SL | `SL_HIT` | 0.3 s |
| 2 | `update_positions` TP | `TP_HIT` | 0.3 s |
| 3 | `update_positions` likuidasi | `LIQUIDATED` | 0.3 s |
| 4 | `_scalp_take_profit` | `SCALP_TP` | 0.3 s |
| 5 | `_auto_close_expired` | `SCALP_EXPIRED` | 0.3 s |
| 6 | `DecisionAgent` reversal | (dari order) | 3 s |
| 7 | `batch_close_positions` | (dari pemanggil) | on-demand |

Jalur 4 dan 5 **bukan** milik `PositionManager` — keduanya ada di
`ExecutionAgent` dan memanggil `engine.position_manager.close_position`
secara langsung. Jalur 6–7 menang balapan dengan 1–3 hanya kalau klaim-nya
duluan; kalau kalah, `close_position` mengembalikan `None` dan itu **sah**,
bukan kegagalan.

### 7.5 Breakeven, expiry, cooldown

**Breakeven** (`execution_agent.py:194`) — jalan **pertama** di
`check_positions`. Threshold `breakeven_trigger_pct` = 0.20%, offset
`breakeven_offset_pct` = 0.15%.

Dua catatan:
- Tidak ada gate `min_hold_seconds` — bisa menyala di detik 0.
- `be_sl = entry * (1 ± offset)` ditulis dengan **float tanpa
  `Decimal`/`ROUND_DOWN`**, berbeda dari setiap SL lain di
  `risk_manager.py` yang memakai `PRICE_QUANT`. Perbandingan `_sl_hit`
  diputuskan di bit terakhir.

**Expiry** — `hold > max_hold_seconds` (900) → `SCALP_EXPIRED`.
Persilangan ini terjadi pada 23 dari 250 posisi tertutup (9%): 20
`SCALP_EXPIRED` dan 3 `SCALP_TP` punya durasi ~159.000 detik (~44 jam),
yaitu jauh melewati batas 900 detik.

**Cooldown** (`decision_agent.py:97-105`) — dibaca dari event
`POSITION_UPDATE` dengan `action == "CLOSED"`:
- PnL ≤ 0 → `cooldown = cooldown_after_loss_seconds × min(streak, 4)` = 90 s × s/d 4 = 90–360 s
- PnL > 0 → `cooldown_after_close_seconds` = 20 s

### 7.6 Position sizing

`DecisionAgent.act` selalu mengisi `order.quantity`, jadi jalur
`calculate_scalp_position_size` di `paper_engine.py:621-626` **tidak pernah
terpakai**. Yang benar-benar jalan:

```python
# risk_manager.py:356 calculate_scalp_position_size
margin = balance × risk_pct                          # 10000 × 0.005 = 50
max_per_pos = balance × 0.9 / max_open_positions      # 10000 × 0.9/30 = 300
margin = min(margin, max_per_pos)                    # 50
position_value = margin × leverage                   # 50 × 5 = 250
quantity = position_value / entry
```

Jadi **margin selalu ~48–50 USDT** dan `notional = margin × 5 ≈ 250`,
terlihat di semua baris `positions`. `max_risk_per_trade` (0.005) tidak
berpengaruh pada besarnya kerugian — hanya besarnya margin terkunci.

Leverage: `_determine_leverage` mengembalikan 5/7/10 dari
`fundamental.risk_level`. Karena FRED mati (§17.6),
`interpret_macro_context([], [])` mengembalikan `risk_level='HIGH'`
(terverifikasi), jadi leverage **selalu 5**. Terkonfirmasi: distinct
leverage di DB = `[5]`.

### 7.7 Liquidation

```python
# risk_manager.py:149, mmr default 0.004 (0.4%), tidak pernah di-override
LONG  liq = entry × (1 − 1/leverage + mmr)
SHORT liq = entry × (1 + 1/leverage − mmr)
```

`mmr` hardcode 0.4% untuk semua aset. Pada leverage 5, liq LONG ≈ 80.4%
entry — jauh di luar jangkauan SL 0.4%, jadi **likuidasi secara praktis
tidak pernah terjadi** (0 dari 253 posisi). Itu benar secara ekonomi untuk
isolated margin, tapi berarti parameter MMR tidak divalidasi terhadap
syarat bursa sebenarnya.

### 7.8 Yang tidak ada di paper path

- Tidak ada maker path — semua fill TAKER (§6)
- Tidak ada partial fill, fill latency, posisi antre, atau konsumsi depth
- Tidak ada latensi bolak-balik bursa — fill dihitung dari mid saat itu juga
- Tidak ada order state machine — order langsung jadi posisi, tidak ada
  baris PENDING/REJECTED/PARTIAL
- Tidak ada batas notional di mode paper (bound `max_order_notional`
  hanya live)
- Tidak ada batas eksposur per simbol atau per arah di `validate_trade` —
  aturan "1 posisi per simbol" ada di `DecisionAgent.think:239-241`, bukan
  di engine, jadi bisa dilewati pemanggil `execute_order` lain

---

## 8. Live path

Enam file, 3.013 baris. Dicapai hanya dari `run.py::_build_live_executor`
(`:310-345`), dijaga `self.mode in ("testnet","mainnet")` (`:416`).

### 8.1 Apa yang diekspor bursa

`LiveExchange` (`client.py:89`) membungkus SDK resmi
`hyperliquid-python-sdk`, bukan implementasi signing sendiri. Alasannya
tertulis di `requirements.txt:13-19`: urutan field msgpack menentukan hash,
dan salah urutan menghasilkan tanda tangan yang **valid tapi untuk aksi yang
salah** — kelas kesalahan paling mahal di sistem seperti ini.

```python
place_limit_order(coin, is_buy, size, price, reduce_only=False, cloid=None)
    → quantize_size(), quantize_price()
    → {"limit": {"tif": "Gtc"}}      # SELALU GTC, tidak pernah IOC/market
    → OrderOutcome(ok, filled_size, avg_price, order_id, error)

place_trigger_order(coin, is_buy, size, trigger_price, tpsl, reduce_only=True)
    → {"trigger": {"triggerPx": …, "isMarket": True, "tpsl": "tp"|"sl"}}
```

`parse_order_response` (`:59`) membaca `filled.totalSz` dan
`filled.avgPx`. **Tidak ada penanganan partial fill di mana pun** (§8.7).

### 8.2 Gerbang safety — dan kenapa live belum pernah jalan

`SafetyGate` (`safety.py:171`) adalah satu-satunya tempat yang
memutuskan boleh-tidaknya order dikirim. Setiap method mengembalikan
daftar `Blocker` yang **menjelaskan** penolakan, bukan boolean —
"kenapa tidak dikirim" jauh lebih berharga daripada "tidak dikirim".

17 blocker (`safety.py:30-49`): `LIVE_DISABLED`, `NOT_CONFIRMED`,
`MISSING_KEY`, `OUTSIDE_WINDOW`, `KILL_SWITCH`, `ORDER_TOO_LARGE`,
`POSITION_TOO_LARGE`, `EXPOSURE_TOO_LARGE`, `DAILY_ORDER_LIMIT`,
`DAILY_LOSS_LIMIT`, `TOO_MANY_ERRORS`, `COLLATERAL_TOO_LOW`,
`RECONCILIATION_FAILED` (mati), `INVALID_INPUT`, `MISSING_CLOID`,
`LEVERAGE_TOO_HIGH`, `COUNTER_STATE_UNREADABLE`.

`master_blockers` (`:315`) berurutan:

```
0. rollover_if_needed()
1. COUNTER_STATE_UNREADABLE   ← file counter rusak = semua order ditolak
2. LIVE_DISABLED              ← TRADEBOT_LIVE ∉ ("1","true","yes")
3. NOT_CONFIRMED              ← TRADEBOT_LIVE_CONFIRMED ∉ ("1","true","yes")
4. KILL_SWITCH
5. MISSING_KEY
6. OUTSIDE_WINDOW             ← di luar (13,23) UTC
7. DAILY_ORDER_LIMIT          ≥ 200
8. DAILY_LOSS_LIMIT           realized ≤ −50
9. TOO_MANY_ERRORS            ≥ 3 beruntun
```

`order_blockers` (`:368`) ditambahkan kalau master lolos:
`INVALID_INPUT` (size ≤ 0 **dan/atau** price ≤ 0 — bisa muncul dua kali),
`MISSING_CLOID` (order opening tanpa cloid), `LEVERAGE_TOO_HIGH` (∉ [1,10]),
`ORDER_TOO_LARGE` (> 100 USDC), `POSITION_TOO_LARGE` (> 300),
`EXPOSURE_TOO_LARGE` (> 600), `COLLATERAL_TOO_LOW` (< 100 tersisa).

> ### ⛔ Gating tidak pernah terpenuhi di produksi
>
> Grep seluruh repo: **tidak ada kode non-test yang meng-set
> `TRADEBOT_LIVE` atau `TRADEBOT_LIVE_CONFIRMED`.** Yang bisa meng-set-nya
> hanya `tests/test_live_safety.py:39-40` dan `test_live_engine.py:107-108`;
> `live_doctor.py:189-190` justru **menghapus** keduanya.
>
> Konsekuensi pada setiap invocation nyata:
> - `master_blockers` selalu mengembalikan `LIVE_DISABLED` + `NOT_CONFIRMED`
> - `can_send` selalu `False`
> - `_open` selalu `{"success": False, "message": "live trading tidak dinyalakan; …"}`
> - **Dan** `SafetyGate.__init__` (`:195-202`) tidak pernah memuat
>   `data_store/live_counters.json`, karena `state_path is None` **dan**
>   `TRADEBOT_LIVE` kosong → penghitung harian dan kill switch **tidak
>   bertahan melewati restart**
>
> Fail-closed memang disengaja. Tapi artinya 190 test live yang hijau
> **tidak membuktikan apa pun** soal order nyata: mereka menyuntik env dan
> men-stub bursa.

### 8.3 Batas live

Semua dari default dataclass (`core/config.py:305-369`) — `config.yaml`
tidak punya blok `live:`, dan `run.py:310` membangun `LiveConfig()` baru:

| Field | Nilai |
|---|---|
| `enabled` | `False` (dipaksa ulang di `config.py:711` meski di-set YAML) |
| `testnet` | `True` |
| `live_window_utc` | `(13, 23)` — half-open, jam UTC |
| `max_leverage` | 10 |
| `max_order_notional` | 100 USDC |
| `max_position_notional` | 300 USDC |
| `max_total_notional` | 600 USDC |
| `max_daily_orders` | 200 |
| `max_daily_loss` | 50 USDC |
| `max_consecutive_errors` | 3 |
| `min_free_collateral` | 100 USDC |
| `use_exchange_side_tpsl` | `True` — **nol referensi di luar config.py** |
| `auto_reconcile` | `False` |
| `reconciliation_tolerance_days` | 0 — **nol referensi** |

TUI bisa mengedit 7 field (`console.py:491-502`) — tapi **hasil edit
dibuang** (§17.11).

### 8.4 Kill switch

Satu boolean `counters.engaged`, diproksi lewat property
(`safety.py:252-259`) supaya ikut persists.

**Cara menyalakan** (6 pemicu, semuanya persist):
- env `TRADEBOT_LIVE_KILL_SWITCH` truthy saat konstruksi (`:230`)
- `record_error` saat `consecutive_errors ≥ 3` (`:508`)
- `engine.reconcile` saat ada selisih **dan** `auto_reconcile=False` (default!)
- `engine.health_check` saat bursa tak terbaca (`:589`) atau ada problem (`:633`)
- `engine.emergency_flat` saat ada kegagalan atau posisi tersisa (`:771`)
- `executor._persist_open` saat `insert_position` gagal (`:574`, `:588`)

**Cara melepas** — tiga-keadaan env, dievaluasi **sekali** saat konstruksi
(`safety.py:228-250`):

| Nilai | Arti |
|---|---|
| tidak di-set | ikuti disk |
| `"1"/"true"/"yes"/…` (bukan release-set) | paksa **AKTIF** |
| `"0"/"false"/"no"/"off"` | paksa **LEPAS** + reset error streak + persist |

> **Kontradiksi yang belum tertangkap.** `__init__:229` melepas pada
> `"off"`, tapi `master_blockers:346` mengecek ulang env **live** dengan
> set `("", "0", "false", "no")` — **`"off"` tidak ada di sana**. Jadi
> `TRADEBOT_LIVE_KILL_SWITCH=off` melepas switch saat konstruksi, lalu
> `master_blockers` membacanya sebagai **aktif**. Gerbang menolak semua
> order dengan `KILL_SWITCH`, sementara log bilang switch sudah dilepas.
> Persis kelas bug yang paling mahal: UI semu yang terlihat benar di log.

`disengage_kill_switch()` (`:464`) **nol pemanggil produksi** — docstring-nya
sendiri mengatakannya, dan grep mengonfirmasi. Satu-satunya jalan melepas
dalam proses adalah env var.

### 8.5 Urutan order opening

Tidak boleh diacak; urutan terbalik memasang SL dulu lalu order ditolak
akan meninggalkan SL yatim yang bisa mengeksekusi tanpa posisi.

```
_execute_open → LiveEngine.submit_order (engine.py:204)
   │
   ├─ _remote_context(coin)   ← 3 panggilan user_state BERURUTAN
   ├─ gate.can_send(request, …)  → tolak? return, TANPA record_error
   ├─ SL & TP wajib ada? (kalau bukan close)  → tolak "missing_tpsl"
   ├─ set_leverage(coin, lev, cross=True)   → gagal? record_error, return
   ├─ place_limit_order(GTC, cloid)         → gagal? record_error, return
   ├─ record_success() + record_order_sent() ← dihitung walau filled 0
   ├─ filled ≤ 0?  → return, proteksi TIDAK dipasang
   └─ _attach_protection(coin, side, filled_size, avg_price, sl, tp, lev)
        ├─ SL trigger (reduce_only)
        ├─ TP trigger (reduce_only)
        ├─ self.positions[symbol] = position   ← dicatat SEBELUM cek SL
        └─ SL gagal? → emergency_flat() + logger.critical
```

### 8.6 Tujuh jalur penutupan live

| # | Pemicu | DB ditulis? | Keterangan |
|---|---|---|---|
| 1 | Trigger SL di bursa | **TIDAK** | Python tidak diberi tahu; baris tetap `status='OPEN'` selamanya |
| 2 | Trigger TP di bursa | **TIDAK** | sama |
| 3 | `LiveExecutor._close` (reversal) | ya | butuh entri lokal; tanpa fallback bursa |
| 4 | `_LivePositionManager.close_position` (scalp TP / expiry) | ya | ukuran bursa yang otoritatif |
| 5 | `emergency_flat` | **TIDAK** | posisi yang ditutup di bursa tidak masuk DB |
| 6 | Likuidasi bursa | **TIDAK** | tak ada kode live; `liquidate_position` hanya dipanggil paper engine |
| 7 | Tutup manual | — | tidak ada jalur kode; `disengage_kill_switch` dan `emergency_flat` tak tersambung ke UI |

> Jalur 1 dan 2 adalah cacat fatal akuntansi: setiap SL/TP yang benar-benar
> fires di bursa tidak menghasilkan satu baris pun. Baris posisi menggantung
> `OPEN` tanpa `realized_pnl`, tanpa fee, tanpa trade row — sementara HUD dan
> ledger tetap menampilkannya sebagai posisi hidup.

`_record_close` (`executor.py:760`) adalah satu-satunya helper DB live.
Urutan: `gross` → `fee_close` (dihitung) → `fee_open` (**dibaca** dari
`trades`) → `net` → klaim `repo.close_position()` → INSERT trade CLOSE →
`get_trade_stats()` (**tanpa `mode=`** → mencampur paper+live) →
`gate.record_realized_pnl(net)`.

`record_realized_pnl` cuma punya satu pemanggil: `executor.py:850`. Jadi
`DAILY_LOSS_LIMIT` hanya bisa terpicu lewat `_record_close`. Fill SL/TP di
bursa dan `emergency_flat` tidak pernah mengisinya.

### 8.7 Partial fill — tidak ditangani di mana pun

`parse_order_response` hanya membaca `filled.totalSz`. Tidak ada
perbandingan dengan ukuran yang diminta, tidak ada cancel-remainder, tidak
ada inspeksi `originalSz`. Akibatnya:

- **Open 40% terisi**: proteksi dipasang untuk ukuran **parsial**
  (`engine.py:359-363` meneruskan `outcome.filled_size`), sisa GTC tetap
  hidup tanpa proteksi.
- `check_pending_fills` (`:455`) melewati koin itu karena
  `any(p.coin == coin …)` (`:502`) sudah benar → sisa tak pernah dilindungi.
- `LiveExecutor.check_positions` menulis balik SL/TP dari `lp.size` tapi
  tidak pernah mengoreksi `quantity` ke ukuran sebenarnya di bursa.

### 8.8 Defect blocking di jalur live

Tiga di antaranya sudah **terbukti dengan eksekusi**, bukan hasil baca kode.

**① `client.py:219` — crash pada setiap order saat ada posisi terbuka**

```python
def symbol_notional(self, coin: str) -> float:
    index = self.info.name_to_asset(coin)
    for asset in state.get("assetPositions") or []:
        pos = asset.get("position") or {}
        if int(pos.get("coin", -1)) != int(index):   # ← coin itu STRING
```

SDK mendokumentasikan `position.coin` sebagai `str` (`"BTC"`), sedangkan
`name_to_asset()` mengembalikan `int`. `int("BTC")` →
`ValueError: invalid literal for int() with base 10: 'BTC'` — sudah
direproduksi.

Rantainya: `symbol_notional` dipanggil dari `_remote_context`
(`engine.py:199`) **sebelum** gate dikonsultasi (`:235-236`) → ValueError
lolos keluar `_open` → ditangkap broad `except` di
`ExecutionAgent.act`. **Setiap percobaan order live mati di sini selama
ada posisi terbuka.**

File yang sama mengontradiksi dirinya: `engine.py:157` benar
(`isinstance(coin, int)`), `client.py:219` tidak.

Tertutup oleh test: `tests/test_live_engine.py:63-64` men-stub
`symbol_notional` → `0.0`. 190 test live hijau dengan bug ini masih ada.

**② Format kunci simbol tidak cocok → `reconcile` selalu reports divergensi**

```python
# engine.py:162  — kunci dari bursa
"symbol": "{} / USDC:USDC".format(name)          # "BTC / USDC:USDC"

# executor.py:472 — kunci dari order
submit_order(..., symbol=order.symbol, ...)       # "BTC/USDT:USDT"
```

`reconcile` membandingkan kedua set itu langsung (`:93-95`) →
`only_local` dan `only_remote` **selalu** terisi untuk koin yang sama →
`auto_reconcile=False` → `engage_kill_switch` (`:128`).

Efek domino: `emergency_flat` memanggil `reconcile` (`:762`), jadi
`flattened` **tidak pernah** `True` — selalu latch kill switch dan log
`EMERGENCY FLAT TIDAK SEMPURNA`, bahkan saat semua posisi tertutup rapi.

`health_check` benar karena sama-sama men-`key` by coin (`:602-603`).
`LiveExecutor.check_positions` juga benar — ia memakai workaround coin
(`executor.py:302`, dengan komentar yang menjelaskan divergensinya), tapi
workaround itu tidak pernah diterapkan ke `reconcile`.

**③ Fill di sisi bursa tidak terlihat, dan memicu kill switch permanen**

Tidak ada langganan fill di seluruh `trading/live/`. Saat SL/TP fires di
bursa, `health_check` melihat koin hilang dari bursa tapi masih ada di
lokal → problem → `engage_kill_switch` (`:633`).

`run_loop` (`:664-668`) lalu `continue` tanpa pernah memanggil
`on_decision` lagi. **Strike pertama pada setiap posisi — termasuk take
profit normal — menghentikan trading secara permanen.**

Ditambah: `health_check:624-628` menandai *order resting tanpa posisi
tercatat* sebagai problem → kill switch. Tapi order GTC yang tidak terisi
(`engine.py:347`) memang **tidak punya** `LivePosition` secara
construction. Satu order yang tidak terisi menghentikan bot.

### 8.9 Cacat modal di jalur live

**Tidak ada lookup order by cloid, dan `LiveExchange.cancel()` tak pernah
dipanggil.** Dijamin idempotensi diklaim di tiga docstring
(`engine.py:225-228`, `executor.py:64-67`, `client.py:384-387`), tapi
implementasinya nol. Setelah timeout, bot tidak bisa tahu apakah order-nya
masuk; siklus berikutnya bisa mengirim lagi dan menggandakan posisi.
`cancel(coin, oid)` (`client.py:462`) **nol pemanggil** — hanya
`cancel_all` yang dipakai, dari `emergency_flat`.

**Breakeven live ditimpa dalam ≤ 2 detik.** `_protect_breakeven` menulis
`UPDATE positions SET stop_loss = be_sl` ke SQLite saja — **tidak ada
trigger bursa yang digeser**. Lalu 2 detik kemudian
`LiveExecutor.check_positions` (`executor.py:361-365`) membandingkan SL
lokal dengan `lp.stop_loss` (nilai yang originally placed di bursa) dan
**menulis ulang DB ke SL asli**. Breakeven lock terbalik tiap siklus dan
tidak pernah bisa berlaku. `sl_order_id`/`tp_order_id` disimpan tapi
tidak pernah dibaca di luar `engine.py` sendiri — trigger tidak pernah
dipindah, dibatalkan, atau diamandau ulang.

**Fee live dihitung dari config, bukan dari bursa.** `executor.py:549` dan
`:794` memakai `config.fees.taker`. Fee sebenarnya di Hyperliquid tidak
pernah diambil. Angka di DB adalah asumsi.

**`record_order_sent` menghitung order yang tidak terisi.**
`engine.py:311-312` berjalan sebelum cek `filled_size == 0`.
`max_daily_orders` = 200 membatasi **submission**, bukan fill.

**`_remote_context` melakukan 3 round-trip berurutan.** `free_collateral()`
+ `total_notional()` + `symbol_notional(coin)` masing-masing memanggil
`get_account_state()`. Docstring `engine.py:192-193` mengklaim *"Semua dibaca
dalam satu kali jalannya"* — benar hanya dalam arti satu `to_thread`, bukan
satu network read. Nilainya juga bukan snapshot konsisten.

**`check_pending_fills` memakai `max_leverage` sebagai leverage posisi.**
`engine.py:535` mengoper `cfg.max_leverage` (10) ke parameter `leverage`,
yang disimpan di `LivePosition.leverage` dan muncul di log.
`set_leverage` tidak dipanggil untuk order yang telat terisi.

**Konsumen mode bercampur.** `insert_position` memakai `mode="live"` tapi
`get_open_positions()` di `check_positions` (`executor.py:295`) dipanggil
**tanpa `mode=`** → mengembalikan baris paper dan live bersama.

**Tidak ada shutdown hook yang membatalkan order resting.** `run.py:1012`
membatalkan `_live_task`, tapi order GTC yang masih hidup di bursa bertahan
dan bisa terisi setelah proses mati.

---

## 9. Data layer

### 9.1 HyperliquidFeed — Tier 0

Dua jalur, tanpa kredensial:

```
REST  POST https://api.hyperliquid.xyz/info   (body JSON, field "type")
WS    wss://api.hyperliquid.xyz/ws
```

`WS_MAX_SIZE = 2**22` (4 MiB — allMids mengirim ~1000 entri per frame),
`WS_PING_INTERVAL = 30.0`, `WS_RECV_TIMEOUT = 60.0`.

Simbol internal `"BTC/USDT:USDT"` ↔ bursa `"BTC"`, lewat
`coin_to_symbol` / `symbol_to_coin` (`:59-73`). Logika itu diduplikasi
tiga kali lagi di `price_feed.py:_symbol_to_yf` dan `_symbol_to_base`.

Langganan (`:377`): 1 global + 4 per koin terpantau → **41 pesan** untuk
10 simbol.

```python
allMids        → set_price() per coin
l2Book         → _parse_book() → set_order_book()
               → microstructure.ingest_l2()   ← SATU-SATUNYA jalan masuk kernel
candle (1m)    → set_live_candle()   ← interval hardcoded, tidak configurable
activeAssetCtx → set_funding(), set_open_interest()
trades         → set_recent_trades()
```

Reconnect: `websockets.connect(open_timeout=10, ping_interval=None, max_size=…)`,
backoff `1.0 → ×2 → cap 30.0`, reset ke 1.0 setelah connect sukses.

**`await asyncio.to_thread(self._handle_message, raw)` per frame**
(`:534`) — parsing JSON dipindah ke thread executor. Alasannya bukan
tampilan: pada top-10 volume, frame L2 20-level bisa ratusan per detik,
dan `json.loads` untuk masing-masing cukup menunda event loop hingga
terasa pada detak jantung 0.3 dtk.

> **Komentar yang bertentangan dengan kode.** `:107-110` menyatakan
> *"Kegagalan TIDAK menjatuhkan sistem"*, tapi `:119-120` meng-`raise` yang
> membuat `_ensure_native_kernel` propagasi keluar dari `websocket_loop`
> sebelum loop-nya jalan.

`TRADEBOT_KERNEL=cpp` yang gagal memang harus error — itu salah
konfigurasi operator, bukan fallback. Tapi raise-nya keluar dari
`websocket_loop` sepenuhnya, bukan hanya melewati ke `PythonKernel`.

### 9.2 PriceFeed — multi-tier fallback

`fetch_ohlcv` (`:198`) mencoba berurutan:

| Tier | Sumber | Timeout | Disimpan? |
|---|---|---|---|
| 0 | Hyperliquid REST `candleSnapshot` | 8 s | ya |
| 1 | ccxt Binance futures | 4 s | ya |
| 2 | yfinance | — | **tidak** (instrumen beda satuan volume) |

> **`_ccxt_available = False` permanen pada exception apa pun**
> (`:231`). Satu timeout jaringan mematikan Binance untuk **selama
> proses**, bukan hanya panggilan itu. Tidak ada retry.

Fallback bersifat aditif, bukan eksklusif: yfinance selalu dijalankan
bila `ohlcv` masih kosong, termasuk saat `_ccxt_available` masih True
tapi mengembalikan nol baris.

`fetch_funding_rate` (`:417`) punya empat tahap, dan tahap terakhir
mengembalikan **rate hardcode `0.0001` / `"Setiap 8 Jam"`** sebagai data
— tanpa key `source`, jadi tidak bisa dibedakan dari rate asli di call
site. Tahap itu hanya tercapai kalau tiga tahap sebelumnya gagal.

Tiga tier untuk `fetch_ticker`: Hyperliquid REST → ccxt (3 s) →
yfinance → CoinGecko.

> **Ticker HL menyuntik spread sintetis.** `fetch_ticker` membangun dict
> HL yang **tidak punya** `bid`/`ask` (`:349-365`), lalu publish pada
> `:405-406` mensubstitusi `price * 0.9999` / `price * 1.0001` — spread ±1 bp
> palsu yang masuk ke event stream dari tier yang didokumentasikan
> mengembalikan "depth asli".

`price_update_loop` (`:566`): satu request `allMids` untuk semua simbol,
lalu untuk simbol yang harganya `None` atau umur > 10 s, `fetch_ticker` +
`sleep(0.15)` masing-masing. Dengan 10 simbol basi, lantai per siklus
1.5 + 1.5 = 3 s.

### 9.3 Rekorder order book

File **terpisah**: `data_store/order_book.db`, bukan `trading_bot.db`.

Alasannya terukur dan tercatat di `run.py:105-109`: bot menulis ke DB
yang sama dari belasan task, dan SQLite hanya mengizinkan satu writer —
file bersama **menolak 82% tick rekorder** dengan "database is locked".
Data ini juga lebih besar dan tidak pernah dibaca saat runtime.

```
_tick() → untuk tiap simbol: book dari market_store
        → _features() → 16-tuple
        → _write(rows, raw_rows, cutoff)   ← SATU transaksi, retry 3× backoff 8/16/32 ms
```

`_features` (`:355`): menolak book < 2 level per sisi (book 1 level tidak
punya spread), menolak `best_ask < best_bid` (book terbalik —menyelamatkan
dengan menukar sides akan menyembunyikan masalah di feed).

Depth dibatasi **10 level** (`:391`), bukan 20, dengan alasan tertulis:
bobot `1.0 − 0.1·i` jadi **negatif mulai level 11**, jadi 20 level membuat
`ask_depth` negatif dan `depth_imbalance` melonjak di luar [-1,1] pada
harga yang benar-benar diam. Produksi aman karena default kernel
`depth=5`.

OFI rekorder (`:415-423`) adalah **perubahan** volume level terbaik
antara dua snapshot, bukan rasio statis kernel:

```python
d_bid = bid_size − prev.bid_size
d_ask = ask_size − prev.ask_size
ofi   = (d_bid − d_ask) / (bid_size + ask_size + prev.bid + prev.ask)
```

> **Schema usang masih tertinggal.** `trading_bot.db` masih punya
> `order_book_features` **8.180 baris** + 3 index dari sebelum pemisahan
> file. Tidak ada job prune untuknya. Persis failure mode yang
> `run.py:106-109` ditulis untuk mencegah — masih ada di DB terkirim.
>
> Catatan: kolom `order_book_features` di DB utama itu punya 8.180 baris
> dengan skema **lama** (`ofi`, `depth_imbalance`), sementara file
> terpisah punya 105.040 baris. Keduanya hidup berdampingan.

`keep_raw=False` di produksi (`run.py:958-962` tidak meneruskan argumen
itu), jadi `order_book_raw` = 0 baris — konsisten dengan data yang
terukur. Konsekuensi: prune `DELETE FROM order_book_raw` di `:330-333`
hanya jalan saat `raw_rows` non-kosong, jadi **tidak pernah** tereksekusi.

### 9.4 Makro — mati permanen

`MacroFetcher()` dibangun tanpa argumen di `run.py:263`. Tidak ada config
key dan tidak ada env var untuk FRED API key di seluruh repo
(terverifikasi). `fetch_fred_data()` mengembalikan `[]` di `:49` setiap
kali, jadi:

- `macro_data` = **0 baris** (terverifikasi)
- `macro_update` job tetap jalan tiap 6 jam, hasilnya selalu `[]`
- `derive_macro_bias` di dashboard selalu melihat tabel kosong
- **Tapi** `interpret_macro_context([], [])` mengembalikan
  `{'bias': 'NEUTRAL', 'risk_level': 'HIGH', …}` — dan itu yang sampai ke
  `_determine_leverage`, jadi leverage selalu 5 (§7.6)

Tiga dari enam seri FRED (`CPI`, `UNEMPLOYMENT`, `YIELD_CURVE`) di-fetch
dan disimpan tapi **tidak pernah diskor** di `interpret_macro_context`.

Kalender Forex Factory tidak butuh key, tapi `update_calendar` hasilnya
hanya dipakai `fundamental.py:98` dengan filter `impact == "High"` —
dan itu satu-satunya tempat `risk_level` bisa dinaikkan, tidak pernah
diturunkan.

### 9.5 Berita dan sentimen

`NewsFetcher` (`:20`): RSS (CoinDesk, CoinTelegraph) via `feedparser` +
CryptoPanic (token opsional, kosong di config). Dedup by exact title match.
`clear_cache` memotong ke "200 terakhir" dengan `list(set)[-200:]` — **set
tidak punya urutan sisip**, jadi komentar itu tidak menggambarkan apa yang
kodenya lakukan.

`SentimentAnalyzer` (`:16`): VADER selalu; FinBERT (`ProsusAI/finbert`,
CPU-only) opsional. Kegagalan VADER **tidak** exception — `_vader` tetap
`None` dan setiap panggilan diam-diam mengembalikan NEUTRAL.

`analyze_finbert` memotong `text[:512]` — itu **512 karakter**, bukan
512 token seperti komentar di `:109` butuh. Untuk tokenizer BERT,
karakter ≈ 0.75 token, jadi pemotongan jauh lebih agresif dari maksud.

`analyze_finbert_batch` (:138) meneruskan seluruh list ke `transformers`
pipeline sekaligus — batching diurus HF. `analyze_finbert_async` (:133)
**nol pemanggil**.

`AnalysisAgent.run_finbert_batch` (`analysis_agent.py:222`) menulis
langsung via SQL mentah, 20 `UPDATE` terpisah lalu satu commit — melewati
`Repository` sepenuhnya, dan **menimpa `sentiment_label` VADER** dengan
label FinBERT.

### 9.6 Skema database

10 tabel di `SCHEMA_SQL` (`db.py:13-156`) + 2 tabel order book di file
terpisah.

```
candles              UNIQUE(symbol, timeframe, timestamp)   idx_candles_lookup
positions            + mode TEXT NOT NULL DEFAULT 'paper'   idx_positions_status, idx_positions_mode
trades               + mode TEXT NOT NULL DEFAULT 'paper'   idx_trades_time, idx_trades_mode
signals                                                  idx_signals_time(symbol, timestamp)
news                                                    idx_news_time
agent_logs                                              idx_agent_logs_time(agent_name, timestamp)
account                                                  (tanpa index)
balance_history                                           idx_balance_time
direction_snapshots                                       idx_dir_snap_symbol(symbol, id)
macro_data             UNIQUE(indicator, period)            (autoindex)
```

`connect()` (`:168`): `PRAGMA journal_mode=WAL` + **`PRAGMA busy_timeout=15000`**.
Nilai 5000 ms dulu tidak cukup — log merekam 1150 kegagalan lock, dan
akibatnya execution loop ikut crash.

> **Tidak ada `PRAGMA foreign_keys=ON`.** `trades.position_id REFERENCES
> positions(id)` dideklarasikan tapi tidak ditegakkan; SQLite default-nya
> OFF.

Migrasi (`:209`): pola `PRAGMA table_info` + `ALTER TABLE ADD COLUMN`,
idempoten. **Tidak ada tabel versi skema** — penemuan migrasi adalah
"lihat table_info dan tambahkan yang belum ada", jadi kolom yang
ditambahkan ke `SCHEMA_SQL` tanpa entri di list `:221-232` tidak pernah
sampai ke database lama.

`macro_data` punya `UNIQUE(indicator, period)` dengan `period` nullable —
di SQLite NULL dalam unique index tidak pernah bertabrakan, jadi
`upsert_macro` untuk baris `period=NULL` degenerasi menjadi INSERT dan
membuat duplikat.

`news` **tidak punya** UNIQUE pada `(title, source)` padahal
`news_exists()`_exists()` menyiratkannya. `NewsAgent.act:117-119` melakukan
check-then-insert — balapan yang tidak dilindungi skema.

### 9.7 Repository

42 method. Yang menentukan:

| Method | Catatan |
|---|---|
| `insert_candles_batch` | UPSERT, filter `_valid_candle`, 1 `executemany` + 1 commit |
| `insert_candle` | **nol pemanggil produksi** |
| `insert_trade` | **nol pemanggil produksi** |
| `close_position` / `liquidate_position` | klaim-tunggal `WHERE status='OPEN'` |
| `get_open_positions` | `ORDER BY opened_at DESC LIMIT 1000` — **plafon keras tak terdokumentasi** |
| `apply_balance_delta` | `SET balance = balance + ?` lalu SELECT terpisah — mutasi aman, **readback raced** |
| `bump_peak_balance` | pola sama: UPDATE lalu SELECT |
| `get_trade_stats(mode=None)` | punya parameter `mode`, **tidak pernah dipakai** |
| `get_daily_realized_pnl` | **tanpa filter mode** |
| `prune_direction_snapshots` | window function, butuh SQLite ≥ 3.25 |
| `news_exists` | **tanpa index** → full scan per item berita, tiap siklus |
| `get_trades_by_position` | **tanpa LIMIT** |

`_valid_candle` (`:17`) bukan None-safe: `float(c.open)` melempar
`AttributeError` pada objek non-`Candle`, dan hanya `TypeError`/`ValueError`
yang ditangkap.

`update_position_sl_tp` (`:131`) membangun SET dari argumen non-None —
**tidak bisa mengembalikan kolom ke NULL**, dan `stop_loss=0.0` tetap
diterima (0.0 bukan None) sehingga SL nol bisa ditulis.

> **Tidak ada prune untuk `balance_history`, `signals`, `news`, `candles`,
> atau `trades`.** Hanya `agent_logs` (`:238`) dan `direction_snapshots`
> (`:528`) yang dipangkas.

**Koneksi**: satu `aiosqlite.Connection` untuk seluruh proses.
aiosqlite menjalankan tiap statement di satu worker thread dengan antrean
ter-serialisasi, jadi tidak ada balapan tulis lintas-thread di dalam
SQLite — tapi tidak ada `BEGIN`/`BEGIN IMMEDIATE`, jadi tidak ada
pengelompokan transaksi lintas tabel.

---

## 10. Analysis layer

### 10.1 TechnicalAnalyzer

`analysis/technical.py:17`. Import `pandas_ta` **di level modul, tanpa
fallback** (`:9`) — `ImportError` mematikan modul, dan ikut
`agents/direction_agents.py`, `agents/analysis_agent.py`, serta
`dashboard/callbacks/update_callbacks.py`.

Sebelas kolom indikator, diparameterkan sebagian, hardcode sebagian:

| Kolom | Parameter | Sumber |
|---|---|---|
| `rsi` | 14 | config |
| `rsi_fast` | 7 | **hardcode** |
| `macd`, `macd_hist`, `macd_signal` | 12/26/9 | config, via `iloc[:, 0/1/2]` |
| `bb_lower/mid/upper` | 20, 2 | config, via `iloc[:, 0/1/2]` |
| `ema_short`, `ema_long` | 9, 21 | config |
| `ema_3`, `ema_5` | 3, 5 | **hardcode** |
| `roc_5` | 5 | **hardcode** |
| `vol_sma` | 20 | **hardcode** |
| `atr` | 14 | **hardcode** — tidak baca config |

Guard `:51`: `len(df) < macd_slow + macd_signal` (35) → warn + return df
tanpa kolom indikator sama sekali, sehingga `generate_signals` jatuh ke
early return dan `ml_signals` dapat default kosong.

> **Penggunaan `iloc[:, 0/1/2]` rapuh.** `pandas_ta` 0.4.71b0 (terpasang)
> mengembalikan `['MACD_12_26_9','MACDh_12_26_9','MACDs_12_26_9']` — cocok.
> `requirements.txt:24` hanya mensyaratkan `>=0.3.14b1` **tanpa batas atas**,
> dan 0.3.x punya layout kolom berbeda. Tidak ada penangkap error.

`generate_signals` (`:94`) — delapan kelompok, masing-masing menambah 1 ke
`total_signals`:

| Kelompok | Skor bullish | Skor bearish |
|---|---|---|
| RSI(14) < 30 / > 70 | +1 / — | — / +1 |
| RSI(14) < 45 / > 55 | +0.5 / — | — / +0.5 |
| RSI(7) < 25 / > 75 | +1.5 / — | — / +1.5 |
| RSI(7) < 40 / > 60 | +0.3 / — | — / +0.3 |
| MACD cross up/down | +1.5 / — | — / +1.5 |
| MACD > 0 / ≤ 0 | +0.5 / — | — / +0.5 |
| BB luar bawah / atas | +1 / — | — / +1 |
| BB di dalam | 0 | 0 |
| EMA cross | +1.5 | +1.5 |
| EMA lanjut | +0.5 | +0.5 |
| EMA(3/5) cross | +1.0 | +1.0 |
| EMA(3/5) lanjut | +0.3 | +0.3 |
| ROC > 0.5 / < −0.5 | +1.0 / — | — / +1.0 |
| ROC > 0.1 / < −0.1 | +0.3 / — | — / +0.3 |
| **Volume** | **0** | **0** |

> **Volume tidak memberi arah sama sekali.** Ketiga cabangnya (HIGH /
> LOW / NORMAL) bernilai 0, tapi tetap menambah `total_signals` — yang
> memperbesar penyebut confidence dan **menurunkan** confidence setiap
> sinyal tanpa bukti arah.

```python
net       = bullish_count − bearish_count
max_score = max(total_signals * 1.5, 1)
net >  0.5 → BULLISH, conf = min(net/max_score, 1.0)
net < −0.5 → BEARISH, conf = min(|net|/max_score, 1.0)
```

### 10.2 DirectionEnsembleAgent — algoritma yang hidup

Inilah jalur penentuan arah yang **benar-benar dipakai** produksi.

#### Normalisasi

```python
# direction_ensemble.py:47
conf      = clamp(confidence, 0, 1)
magnitude = MAX_AGENT_Z × conf              # MAX_AGENT_Z = 2.5
LONG → +magnitude · SHORT → −magnitude · selain itu → 0.0
```

**Linier, bukan log-odds** meski modul namanya `direction_ensemble`.
Confidence 1.0 → z 2.5; confidence 0.5 → z 1.25. Direction string yang
bukan persis `"LONG"`/`"SHORT"` diam-diam jadi z=0 tanpa peringatan.

#### Agreement bonus

```python
# :143
bonus  = 1.0 + agreement_bonus × agreement_score(v, active)   # agreement_bonus = 0.5
weight = base × bonus                                         # multiplier ∈ [1.0, 1.5]
```

`agreement_score` hanya membandingkan **tanda**, bukan besarannya. Agen
dengan z = 0 dikeluarkan dari pembilang *dan* penyebut.

> **Konsekuensi yang tidak terlihat dari kode:** ketika semua agen LONG
> dengan confidence 1.0, setiap agen dapat `agreement_score = 1.0` dan
> bonus penuh 1.5×. Karena `z` adalah rata-rata berbobot, bonus yang seragam
> **menghilang saat pembagi** — hasil akhirnya tetap 2.5. Agreement bonus
> hanya berpengaruh saat split. Terverifikasi:

```
1 agen LONG conf 1.0        → z 2.5000, p 0.8933, conf 0.7866
4 agen unanimous LONG      → z 2.5000, p 0.8933, conf 0.7866   ← identik
3 LONG + 1 SHORT(0.5)      → z 1.8632, p 0.8297, conf 0.6595
2 LONG + 2 SHORT semua 1.0  → z 0.2500, p 0.5529, conf 0.1059
```

#### Pooling dan confidence

```python
# :162
z         = Σ(weight_i × z_i) / Σ(weight_i)      # rata-rata berbobot
z_shrunk  = z × shrinkage_delta                   # × 0.85
prob_long = sigmoid(z_shrunk)
prob_long = clamp(prob_long, min_prob, 1−min_prob) # [0.02, 0.98]
confidence = |prob_long − 0.5| × 2.0
direction = >0.5 LONG · <0.5 SHORT · ==0.5 NEUTRAL
```

**Plafon confidence = 0.7866**, karena `|z| ≤ 2.5` dan `2.5 × 0.85 = 2.125`
→ `p = 0.8933` → `conf = 0.7866`.

> **Konsekuensi untuk gate.** `min_confidence` = 0.40 perlu z ≥ **0.9968**
> (40% dari MAX_AGENT_Z). `reversal_close_threshold` = 0.70 perlu z ≥
> **2.0407** (82%). Artinya:
>
> - **Satu agen** dengan confidence ≥ 0.40 sudah cukup untuk membuka posisi
> - **Satu agen** dengan confidence ≥ 0.82 sudah cukup untuk **membalikkan**
>   posisi yang sedang berjalan
>
>Efek struktural ini tidak terlihat dari ambang-ambang di `config.yaml`: angka 0.70
> terasa selektif, tapi sebenarnya 82% dari skala yang tersedia.

#### Detail yang perlu diketahui

- `aggregate` **mutasi dict milik pemanggil**: tulis `v["_z"]` (`:107`) dan
  `v["abstained"] = True` (`:141`).
- `n_abstained` dihitung **sebelum** agent berbobot 0 ditandai abstain, dan
  `n_agents = len(breakdown)` dari list pra-filter (`:190`) — jadi agent
> yang dinonaktifkan muncul di `agent_breakdown` dan `n_agents` tapi tidak
> di `n_abstained`, bertentangan dengan komentar `:139-140`.
- `z_shrunk` **hilang** di dua dari tiga jalur return (`:122-131`,
  `:150-159`); hanya `:187` yang menyertakannya.
- `agreement_score` (`:78`) membaca `verdict["_z"]` — butuh `_z` sudah
  diinjeksi. Dipanggil dari `:143` setelah pass normalisasi, jadi aman
  sekarang, tapi fungsi publik yang diuji langsung akan `KeyError`.
- Dua jalur return **tidak punya** `z_shrunk` dan `n_agents` diisi berbeda.

### 10.3 Empat agen spesialis

| Agen | Sumber | Confidence | Threshold |
|---|---|---|---|
| `orderflow` | `probability_engine.calculate_order_flow_imbalance(book, symbol=…)` | `abs(ofi)` | abstain kalau `ofi == 0 dan spread == 0` |
| `momentum` | `market_store.get_price_change(30 s)` | `min(|change| / (threshold × 2), 1)` | `threshold` 0.08% |
| `technical` | 100 lilin 1m dari SQLite sinkron → `calculate_technical_zscore` | `min(|z| / 2, 1)` | butuh ≥ 30 close |
| `microstructure` | tape + funding + open interest | `min(|z| / 2, 1)` | tape ≥ 8 trade |

> **Momentum saturasi terlalu mudah.** `momentum_threshold = 0.0008` (0.08%)
> dan confidence = `|change| / (threshold × 2)` → **perubahan 0.16% dalam 30
> detik sudah menghasilkan confidence 1.0**. Pada altcoin likuid, itu
> kejadian biasa. Gate `min_confidence = 0.40` efektif hanya berarti
> "harga bergerak sama sekali".

> **Microstructure memakai rata-rata biasa tanpa pembobot.**
> `z = np.mean(z_components)` (`direction_agents.py:306`) — komponen
> tunggal (hanya tape) mendapat bobot penuh, sementara tiga komponen
> mendapat ⅓ masing-masing. Level keyakinan tidak sebanding dengan
> banyaknya bukti.

Funding **counter-sentiment**: `z_funding = clip(−funding / 0.0001, −2, 2)`
(`:290`). Funding positif → z negatif → bearish. Open interest **dicatat
tapi bukan vote** (`:296-298`, komentar eksplisit).

`_load_closes` (`:331`) membuka ** koneksi `sqlite3` sinkron baru** per
panggilan, dengan `ORDER BY timestamp DESC LIMIT ?` lalu dibalik.
`TechnicalAgent.evaluate_async` membungkus ke `asyncio.to_thread` karena
kalkulasi pandas/NumPy CPU-bound.

### 10.4 Volatilitas — target dinamis dan gate

`analysis/volatility.py`. Dua perbaikan ATR:

```python
# :123  True Range
TR = max(high − low, |high − prev_close|, |low − prev_close|)
# Wilder: seed = rata-rata 14 TR pertama, lalu rekursif (atr×13 + tr)/14
atr_pct = atr / close
```

Target dinamis (`:330`):

```python
raw_sl = atr_multiple × atr_pct                    # 1.5 × atr
sl_pct  = clamp(raw_sl, min_sl_pct, max_sl_pct)   # [0.40%, 1.50%]
tp_pct  = max(sl_pct × min_risk_reward, min_profit_pct)   # max(1.5×SL, 0.60%)
```

> **Secara numerik, jalur "dinamis" identik dengan statis.** ATR 1m BTC
> di menit tenang ≈ 0.20–0.30%, jadi `raw_sl` = 0.30–0.45% — dan
> `min_sl_pct` = 0.40% **meng-clamp ke atas**.)")
>-plus `paper_engine._resolve_tp_sl` menerapkan `max()` kedua terhadap
> `tight_sl_pct` (0.40%). Hasilnya: `sl_pct = 0.40%`, `tp_pct = 0.60%` —
> persis nilai statis. Komponen ATR tidak berkontribusi apa pun dalam kondisi
> normal.

#### Gate yang mati secara struktural

```python
# :386  reject kalau tp_pct <= roundtrip
# :402  reject kalau net_tp < net_sl DAN breakeven_wr > max_breakeven_win_rate
```

Syarat R:R branch menyala: `(sl + rt)/(2·sl + rt) > 0.65` → `sl < 1.1667 × rt`
= **0.105%**. Tapi `sl_pct ≥ min_sl_pct = 0.400%` — **3.8× lebih tinggi**.
Gerbang yang butuh SL lebih rapat dari yang diizinkan config-nya sendiri
tidak pernah bisa menyala.

Verifikasi swept seluruh rentang `sl ∈ [0.40%, 1.50%]`: **0 konfigurasi
menyala**.

> Efeknya berantai: `VolatilityGateError` mati → `_validate_dynamic_tp_sl`
> §10.5 juga lolos tanpa tegangan → `max_breakeven_win_rate: 0.65` adalah
> konstanta yang tidak pernah dipakai → `test_advanced_modules.py:297-317`
> menguji gate dengan `sl_pct`/`tp_pct` sintetis **di luar rentang yang bisa
> dicapai**.

#### Cache ATR

```python
_ATR_CACHE: Dict[Tuple[str, int], Dict]   # kunci = (symbol, bucket 5-detik)
```

> **Kunci cache tidak menyertakan `period`.** `atr_1m_pct(symbol, candles=None, period=14)`
> menulis dan membaca dengan kunci yang sama apa pun periodenya. Produksi
> homogen di 14, jadi tidak terlihat — tapi test yang menyuntik `candles=`
> dengan period berbeda dalam jendela 5 detik yang sama akan bertabrakan.
>
> Eviksi adalah `clear()` menyeluruh saat 256 entri (`:197`), bukan LRU.

`_load_candles` (`:249`) **hardcode `"1m"`** sementara kontrak
`set_candle_source` (`:227`) menjanjikan `(symbol, timeframe, limit)`.
Permintaan timeframe lain diam-diam dapat 1m.

`getattr(cfg, name, default) or default` dipakai di seluruh modul — nilai
config **`0.0` diam-diam reverted** ke literal, bukan dihormati.

### 10.5 Yang mati di analysis layer

| Item | Lokasi | Status |
|---|---|---|
| `compute_composite_probability` | `probability_engine.py:171` | **mati** — nol pemanggil produksi. Bobot `0.30/0.20/0.25/0.15/0.10`, sigmoid tanpa clamp |
| `compute_diffusion_curve` | `:326` | mati, ditandai `DEPRECATED` sendiri |
| `calculate_realized_volatility` | `:139` | mati — default `0.0015`, floor `0.0002`, **float bukan None** (konvensi berlawanan dengan `volatility.py`) |
| `QuantititativeProbabilityEngine` | `:155` | instance tunggal `:433`, hanya dipakai untuk `compute_directional_curve` |
| **`vol_target.py` (133 baris)** | seluruh modul | **mati** — nol importer termasuk test |
| `fundamental.should_reduce_risk` | `:135` | mati |
| `fundamental.get_suggested_leverage` | `:143` | mati |
| `backtester.py` (981 baris) | seluruh modul | mati di produksi, dipakai test |

> **Dua pipeline arah yang berbeda.** `compute_composite_probability` (mati)
> dan `direction_ensemble.aggregate` (hidup) sama-sama mengklaim
>pekerjaan yang sama dengan matematika, bobot, dan skala yang berbeda.
> Yang hidup hanya `direction_ensemble.aggregate`.

> **`vol_target.py` punya docstring rusak.** `:18-21` berbunyi *"indikator
> Semuanya memiliki bukti yang jauh lebih tipis"* — `Semuanya` menyisip di
> tengah kalimat, sisa find-replace yang gagal.

### 10.6 Probability engine — kurva difusi

`compute_directional_curve` (`:270`) adalah fungsi **hidup** yang
menghasilkan graf konvergensi HUD.

```python
sigma     = max(realized_vol_per_min, 0.0003)
p_clamped = clip(prob_long, 0.001, 0.999)
z_score   = log(p_clamped / (1 − p_clamped))
drift     = z_score × 0.45 × sigma            # bertanda
t_steps   = linspace(0.2, horizon_minutes, 60) # mulai 0.2 menit, bukan 0

per titik:
  d2        = ((drift − 0.5σ²) × t) / (σ√t)
  p_terminal = Φ(d2)
  weight_t   = 1 − exp(−t × 0.08)
  p_long     = (1 − weight_t) × p_clamped + weight_t × p_terminal
```

> **Ini kurva presentasi, bukan probabilitas terminal.** `weight_t` hanya
> mencapai 0.909 pada horizon 30 menit. Terverifikasi dengan σ=0.002,
> p₀=0.9:

| t (menit) | weight | p_terminal | p_blend |
|---|---|---|---|
| 0.2 | 0.016 | 0.6707 | 0.8964 |
| 5 | 0.330 | 0.9864 | 0.9285 |
| 15 | 0.699 | 0.9999 | 0.9698 |
| 30 | 0.909 | 1.0000 | 0.9909 |

HUD fallback-nya memakai `realized_vol_per_min=0.0018` **hardcode**
(`hud_figures.py:238`), bukan sigma terukur.

### 10.7 Technical z-score

```python
# probability_engine.py:93
z_rsi  = (rsi − 50) / 20                              clip ±2
z_macd = (macd_hist / denom) × 1.5                   clip ±2
z_ema  = ((ema_short − ema_long) / ema_long) × 100   clip ±2
z_bb   = ((price − bb_lower)/(bb_upper − bb_lower) − 0.5) × 2.5  clip ±2
return mean(sub_z)          # rata-rata ARITMETIK, tanpa bobot
```

> **Rata-rata tanpa bobot_components.** Kalau hanya RSI yang berhasil
> dihitung, `z` adalah z RSI mentah tanpa redaman; kalau keempatnya
> berhasil, masing-masing 25%. Simbol yang ATR-nya gagal dihitung diam-diam
> dapat z berskala berbeda dari kondisi pasar yang identik.
> `TechnicalAgent` memetakan `confidence = min(|z| / 2, 1)` (`:245`), jadi
> z satu-indikator dan z empat-indikator diperlakukan samaYDUPAT confident.

`denom` untuk z_macd: `atr` kalau tersedia, kalau tidak
`max(current_price × 0.002, 1e-4)`.

> **Docstring modul salah** (`probability_engine.py:15`): menuliskan
> `OFI = (sum(BidSize) − sum(AskSize)) / (sum(BidSize) + sum(AskSize))`.
> Implementasi sebenarnya memakai bobot linier menurun `1 − 0.1·i` pada 5
> level teratas (`core/microstructure.py:120-128, 143-152`). Rumus di
> docstring mendeskripsikan fungsi yang sudah tidak ada.

---

## 11. Agen

### 11.1 BaseAgent — siklus

`agents/base_agent.py:20`. `sense()` → `think()` → `act()` → `_log_cycle()`.
Tiga abstractmethod; tidak ada `initialize()` di base.

`run_cycle` (`:77`):

```python
_cycle_count += 1 ; cycle_id = f"{name}#{n}"
try:  sense → think → act → _log_cycle
except asyncio.CancelledError:  debug ; raise       # BUKAN error
except Exception as e:  error(str(e)) ; debug(traceback) ; _log_error
```

> **Berbeda dari `_execution_loop`.** `run.py:652` melacak kegagalan
> beruntun, print traceback untuk 3 pertama, eskalasi ke CRITICAL di 100,
> denyut nadi tiap 50. `BaseAgent.run_cycle` hanya log error dan lanjut —
> tanpa penghitung, tanpa eskalasi. Dua loop dengan kebijakan kegagalan
> yang berlawanan; hanya yang 0.3 dtk punya yang lebih kuat.

`CancelledError` ditangani eksplisit karena di Python 3.8+ ia turun dari
`BaseException`, bukan `Exception` — tanpa blok itu, setiap Ctrl+C
mencetak traceback penuh.

`_log_cycle` menulis `reasoning = analysis.get("reasoning", analysis.get("summary", …))`
dan `input_data`/`output_data` dipotong 2000 karakter.

### 11.2 NewsAgent

RSS + CryptoPanic → VADER per item (await seriel per item, satu thread
hop tiap item) → agregat → dedup via `news_exists` → INSERT news +
INSERT Signal(symbol=`"MARKET"`) → publish `NEWS_SENTIMENT`.

`impact_level`: `|score| > 0.5` HIGH · `> 0.2` MEDIUM · else LOW.

**Nol config dibaca** — interval dari scheduler saja.

> **Race check-then-insert.** `news_exists()` lalu `insert_news()`
> (`news_agent.py:117-119`) tanpa UNIQUE di skema. Dua siklus yang
> bersamaan bisa inserting berita yang sama.

### 11.3 AnalysisAgent

Per simbol: `fetch_ohlcv("5m", limit=200)` → indikator → sinyal teknik
→ fundamental → ML → combine.

`_combine_signals` (`:262`) — bobot **hardcode**:

```python
{"technical": 0.40, "fundamental": 0.25, "ml": 0.35}
weighted_score = Σ score × weight × confidence
direction = BULLISH if > 0.15 · BEARISH if < −0.15 · else NEUTRAL
confidence = round(min(total_weight, 1.0), 3)
```

> **Confidence adalah normalizer bobot, bukan keyakinan.** Hanya 1.0 bila
> confidence setiap sumber = 1.0. Sumber dengan confidence 0.5 menurunkan
> confidence gabungan ke ~0.5 — bukan karena ketidakpastian, tapi karena
> kontribusinya dipotong. Definisi ini tidak biasa dan tidak
> didokumentasikan sebagaisuch.

Sentiment label `> 0.05` POSITIVE / `< −0.05` NEGATIVE / else NEUTRAL
(`:107-111`) — **hardcode**, dan label itu diteruskan apa adanya ke
`fundamental.analyze()` yang memakai `avg_score` untuk penskoran. Keduanya
bisa berbeda: score +0.30 memberi `sentiment_bias = "NEUTRAL"` tapi
`bullish_score += 2`.

`run_finbert_batch` (`:222`) menulis 20 `UPDATE` lewat SQL mentah lalu
satu commit — melewati `Repository`, dan menimpa `sentiment_label`.

### 11.4 DecisionAgent

Gate yang dijalankan, berurutan (`think`, `:114`):

**CLOSE (reversal)** `:161-206`:
```
min_hold_seconds dilewati?         → yes: continue
LONG  + sinyal BEARISH + strength ≥ 0.70  → close
SHORT + sinyal BULLISH + strength ≥ 0.70  → close
→ pop dari open_symbols di SIKLUS YANG SAMA
```

> Pop di siklus yang sama itu koreksi yang benar dan bercomments panjang
> (`:188-202`): penutupan tidak dij menang balapan dengan SL/TP, jadi
> `_symbol_cooldowns` tetap jadi pagar kedua.

**OPEN** `:211-265`:
```
opened >= batch_limit?                          → break
symbol sudah di chosen_symbols?                 → continue
now < _symbol_cooldowns[symbol]?                → continue
strength < min_confidence (0.40)?               → continue
spread_pct > max_spread_pct (0.06%)?            → continue
sudah punya 1 posisi?                           → continue
→ Order + publish TRADE_DECISION
```

`_generate_scalp_signals` (`:465`) — sumbernya **bukan** analisis
teknikal, tapi tabel `direction_snapshots`:

```
snap = repo.get_latest_direction_snapshot(symbol)
None?                                    → []
umur > max_snapshot_age_seconds (20 s)?   → []  (log level DEBUG)
direction ∉ (LONG, SHORT)?               → []
strength < min_confidence (0.40)?        → []
→ {"direction": BULLISH/BEARISH, "strength": …, "source": "ensemble", …}
```

> Freshness gate di-log di level **debug** (`:487`), bukan warning. Di
> boot dengan `logging.level=INFO`, snapshot basi yang ditolak **tidak
> kelihatan sama sekali** di log — dan gejalanya (bot tidak pernah buka
> posisi) identik dengan "belum ada sinyal".

`act()` (`:280`) — saldo dibaca **sekali per siklus** (bukan per order),
dan menghitung ukuran posisi di sini (bukan di engine) karena live
executor mengirim `size = order.quantity or 0.0`.

`_determine_leverage` (`:550`): HIGH → `max(2, default//2)` = 5 ·
MEDIUM → `max(3, default×3//4)` = 7 · else 10. Karena FRED mati,
HIGH selalu → **leverage selalu 5** (§7.6, terverifikasi di DB).

`is_us_market_open` diimpor (`:16`) dan dihitung (`:111`), lalu
**tidak pernah dibaca** oleh `think()`. `_global_loss_streak` (`:69`)
 incremented tapi tidak pernah dibaca.

### 11.5 ExecutionAgent

`check_positions()` (`:177`) — urutan itu **load-bearing**:

```python
await self._protect_breakeven()      # 1. SL → breakeven
await self._scalp_take_profit()      # 2. profit ≥ min_profit_pct
await self._auto_close_expired()     # 3. hold > max_hold_seconds
await self.engine.check_positions()  # 4. SL/TP/likuidasi standar
```

Komentar `:199-201` menyatakan breakeven harus jalan **sebelum** scalp TP
dan ambangnya **di bawah** `min_profit_pct` — kalau sama, TP menyala
lebih dulu di siklus yang sama dan seluruh logika breakeven tidak pernah
memberi efek. Urutan itu benar di kode.

`think()` (`:83`) menghitung SL/TP dari harga yang dilihatnya, tapi
nilainya **advisory** — `paper_engine._execute_open` menghitung ulang
dari harga fill (`:517-522`). Alasanannya benar dan penting: `think()` dan
`execute_order()` dipisah `await`, harga bisa bergerak di antaranya.

> `_scalp_take_profit` menghitung `now = time.time()` di `:283` lalu
> **tidak pernah memakainya**. Mati.

### 11.6 Matriks interval

| Agen | Normal | US open | `start_immediately` |
|---|---|---|---|
| `news_agent` | 300 s | 120 s | ya |
| `analysis_agent` | 15 s | 10 s | ya |
| `decision_agent` | 3 s | 2 s | **tidak** |
| `direction_ensemble` | 5 s | (tetap) | — |
| `_execution_loop` | 0.3 s | 0.3 s | — |

Deteksi US market (`scheduler.py:19`): `pytz` timezone `US/Eastern`,
akhir pekan tertutup, jendela **half-open** `[09:30, 16:00)`. Tidak ada
field `close_minute`.

`add_agent_job` dengan `start_immediately=True` mendaftarkan job kedua
tanpa trigger (`:86`) — sekali jalan saat scheduler start, tidak pernah
dihapus, dan tidak terlihat oleh `_adjust_intervals`.

---

## 12. Dashboard

### 12.1 Struktur

Tema **"HUD 12-COLUMN TERMINAL"** — parchment hangat, `#dfd5b8`, border
2px hitam pekat, tipografi `Share Tech Mono`.

```python
dcc.Interval(id="dashboard-interval",      interval=500)    # 2 Hz
dcc.Interval(id="dashboard-slow-interval", interval=60000) # 1/60 Hz
```

Grid 12 kolom (`.hud-grid`, `style.css:337`):
`repeat(12, minmax(0,1fr))`, gutter `var(--gutter)` = 6px. Breakpoint
1499px (8/4 → 7/5), 1099px (12 → 6 kolom), 767px (1 kolom).

Zona:

| Zona | className | Isi |
|---|---|---|
| Top bar | `hud-topbar` | spot BTC/ETH/SOL, avg edge, jam UTC, latency badge |
| Ticker | `hud-live-ticker-tape` | marquee |
| Z1 | `hud-panel span-12 wallet-rail` | giant PnL, saldo, equity, win rate, drawdown |
| Z2 kiri | `hud-panel zone2-price` | mini candlestick + orderbook ladder |
| Z2 kanan | `hud-panel zone2-stack` | tabel posisi + probability scanner |
| Z3 | `hud-panel span-12 symbol-pnl-panel` | rail PnL per simbol |
| Z4 ×4 | `hud-panel span-3 zone3-cell` | equity · trade log · analytics+sparkline · neural net |
| Footer | `hud-footer` | status engine |

> **Penomoran zona tidak konsisten.** `hud.py:38` mengomentari "Z1 ·
> ACCOUNT RAIL", `hud.py:24-31` menomori grup terakhir "Z4 · ANALYTICS
> SEKUNDER", tapi `hud.py:55` memberi class `zone3-cell` pada elemen yang
> sama. Penomoran lagih terhadap nama class.

### 12.2 Sembilan belas callback

**18 pada 2 Hz, 1 pada 60 s.** `update_trade_stream_and_marquee` satu-
satunya yang diikat ke interval lambat.

Beban per detik di jalur 500 ms:

| Callback | Query |
|---|---|
| `update_mini_candlestick_and_tape` | 400 bar 1m + pandas ewm + Plotly figure |
| `update_neural_net` | `MAX(id) GROUP BY symbol` di `signals` + agent_logs 60 s + 24 trace Plotly |
| `update_analytics_and_sparklines` | **8 query** + 2 figure |
| `update_top_bar` | full scan posisi tertutup + `compute_mathematical_edge` |
| `update_wallet_overview` | full scan posisi tertutup |
| `update_equity_area` | 300 bar balance_history + figure |
| `update_symbol_pnl` | GROUP BY di seluruh `positions` |
| 8 legacy callback | 4–5 query masing-masing ke elemen `display:none` |

> **Delapan callback legacy (`:120-305`) mengisi elemen `display:none`.**
> `update_legacy_performance` menjalankan 5 query tiap 500 ms untuk div
> yang tak pernah terlihat. Total ~9 query per detik terpakai untuk
> sesuatu yang tidak dirender.
>
> `create_equity_chart` di jalur legacy **tidak bisa benar**: `:181`
> memilih hanya kolom `equity`, tapi `performance.py:83-85` membaca
> `timestamp` dan `balance`. `.get` dengan default menyelamatkan dari
> `KeyError`, hasilnya sumbu-x 300 string kosong dan garis Balance
> rata-rata nol — di dalam `try/except`, jadi tidak pernah muncul.

Predikat yang sama muncul 6× di file yang sama:

```sql
WHERE status IN ('CLOSED','LIQUIDATED') AND realized_pnl IS NOT NULL
```

`update_analytics_and_sparklines` menjalankannya **dua kali dalam satu
tick** (`:1233` dan `:1274`).

### 12.3 Koneksi database dashboard

`_get_sync_db()` (`:64`) membuka **koneksi `sqlite3` blocking baru per
callback** dan **tidak pernah** set `PRAGMA busy_timeout`. App writer-nya
set 15000 ms justru karena ada banyak writer — 18 koneksi dashboard di
2 Hz dengan timeout nol bisa langsung dapat `database is locked`, dan
ditelan `except: raise PreventUpdate` di 14 tempat. Setiap satu =
satu panel membeku tanpa error yang terlihat.

Sembilan callback memanggil `conn.close()` hanya di jalur bahagia tanpa
`finally` (`:130/139`, `:157/184`, `:277/284`, `:331/364`, `:417/439`,
`:631/642`, `:716/719`, `:916/928`, `:1004/1034`, `:1059/1081`,
`:1221/1320`). Exception di tengah = koneksi bocor, 2 Hz × 16 situs.

### 12.4 Token CSS

59 token di satu blok `:root` (`style.css:14-97`). Tinggi panel adalah
kontrak tunggal:

```
--h-topbar 28px   --h-rail 66px      --h-tape 20px      --h-zone2 356px
--h-zone3 260px   --h-pipe 56px      --h-head 22px      --h-scroll 230px
--h-chart 179px   --h-tape-ob 143px  --h-ob-row 13px    --h-conv 92px
--h-neural 230px  --h-spark 34px     --h-equity 230px   --h-log 230px
--h-kpi 118px
```

Tujuh token tinggi dipakai dari **inline style Python** (`hud.py:185`,
`:266`, `:335`, `:360`, `:408`, `:417`), bukan dari CSS — jadi
`test_layout_budget.py:206` yang grep `var(--…)` di `hud.py` benar,
sementara token yang hanya di CSS (`--h-topbar`, `--h-tape`, `--h-scroll`,
`--h-ob-row`, `--h-tape-ob`) **tidak terpakai di mana pun**. Sebelas token
mati terverifikasi.

`--bg-app` terduplikasi sebagai literal `#dfd5b8` di `app.py:92`.

`.giant-pnl` punya `var(--fs-hero)` di `:393` tapi **dioverride** oleh
`.rail-hero .giant-pnl { font-size: 26px }` di `:856` — dan test yang
 supposedly menjaga tipe skala hanya menangkap rule pertama (§16.4).

Sebelas token tinggi/CSS mati: `--h-topbar`, `--h-tape`, `--h-scroll`,
`--h-tape-ob`, `--h-ob-row`, `--h-kpi`, `--pad-body`, `--pad-bar`,
`--neg-wash-2`, `--warn-wash`, `--warn-wash-2`. Plus `--fs-md`,
`--fs-lg` yang didefinisikan tapi tidak dipakai (nilai literal
digunakan langsung).

### 12.5 Figure builders

| Builder | Baris | Plot |
|---|---|---|
| `create_mini_candlestick_fig` | `:83` | 1m OHLC + EMA-9, x kategorikal, `dragmode="pan"` |
| `create_convergence_fig` | `:217` | kurva probabilitas LONG/SHORT per horizon |
| `create_neural_net_fig` | `:532` | konstelasi: 13 simpul sistem + ≤12 token, **satu trace per edge** |
| `create_equity_area_fig` | `:871` | area equity, `fill="tonexty"` (bukan `tozeroy`) |
| `create_mini_sparkline_fig` | `:943` | garis telanjang, sumbu disembunyikan |

`create_equity_area_fig` memakai `tonexty` dengan sengaja — supaya
perubahan kecil tetap terlihat; `tozeroy` akan membuat kurva rata di
baseline.

`neural_flow.css` menganimasikan garis lewat CSS
(`stroke-dashoffset`, `neural-edge-flow` 1.2 s) dan marker lewat
`opacity` saja — **tidak pernah `transform`**, karena Plotly memposisikan
marker dengan atribut SVG `transform="translate(x,y)"` dan CSS
`transform` akan menimpanya, menumpuk semua marker ke origin.
Ada guard `@media (prefers-reduced-motion: reduce)`.

### 12.6 Doktrin data absen

UI menampilkan empty state eksplisit, tidak pernah angka karangan:

| Absen | Tampil |
|---|---|
| Order book | `AWAITING L2 ORDERBOOK SNAPSHOT // HYPERLIQUID WS` |
| Lilin | `AWAITING 1M CANDLE INGEST // NO SYNTHETIC DATA` |
| Posisi | `NO ACTIVE POSITIONS // SCANNING TOP 10 FUTURES...` |
| Trade | `NO EXECUTIONS YET` |
| Snapshot arah | `BELUM ADA SNAPSHOT` |
| Sharpe / MDD / slippage | `N/A` |
| Sparkline | `NO DATA` |
| Fee belum ada | `$0.00` |

`compute_mathematical_edge` (`:373`, `:1181`) dipanggil dengan
`trades_list` tanpa `current_winner_odds` → default 0.55 → edge teoretis
`(0.55×1.5 − 0.45) × 1.25 = 0.4688%` **dengan floor 0.10%**, jadi HUD
menampilkan edge positif bahkan dengan **nol trade tertutup**. Terverifikasi
dengan eksekusi.

### 12.7 Yang mati

- `derive_macro_bias()` (`:72-111`) — 40 baris logika ambang FRED,
  **nol pemanggil** di seluruh repo
- `calculate_realized_volatility`, `calculate_technical_zscore`,
  `TechnicalAnalyzer` — diimpor (`:22-26`), nol dipanggil
- Enam layout builder (`positions`, `agent_logs`, `news_feed`,
  `price_chart`, `performance`, ×2) — diimpor, nol dipanggil
- `math` diimpor (`:8`), nol `math.` dipakai
- `State("hud-chart-symbol-select","options")` (`:389`) di-feed sebagai
  satu-satunya input ke `raise PreventUpdate` (`:399`) — state itu tak
  mungkin berbeda dari yang baru ditulis callback yang sama
- `.text-red` dan `.text-amber` masing-masing dideklarasikan **dua kali**
  (`style.css:766`/`801` dan `:767`/`:800`)
- `.pulse-halt` didokumentasikan di `:776` sebagai salah satu dari dua
  animasi yang tersisa — **sudah tidak ada** di kedua file CSS

> **Badge latency mengukur dirinya sendiri.** `:358-380` mengukur durasi
> callback-nya sendiri, bukan latensi bursa — dan di-floor ke 2 ms. Badge
> `--ms` tidak pernah bisa baca di bawah 2.

---

## 13. Ekstensi native C++

### 13.1 Yang benar-benar diekspor

`cpp_microstructure` — **tiga nama publik** + dua atribut, diverifikasi
dengan `dir()`:

```
MicrostructureKernel  ·  level_weight  ·  MAX_LEVELS (=32)  ·  __version__ (=1.0.0)
```

Method pada instance (enam): `order_flow_imbalance`, `depth_imbalance`,
`ingest_l2`, `ingest_json`, `reset`, `symbol_count`.

Dokumen lama menyebut "empat nama" — menghitung empat method dan
mengabaikan `reset`, `symbol_count`, `level_weight`.

### 13.2 Kontrak data

```python
order_flow_imbalance(symbol: str, depth: int = 5) -> tuple[float, float]
depth_imbalance(symbol: str, depth: int = 5) -> float
ingest_l2(symbol: str, bids, asks) -> None
ingest_json(payload: bytes) -> str | None        # None = bukan channel l2Book
reset(symbol=None) -> None
symbol_count() -> int
level_weight(index: int) -> float
```

`depth` adalah `uint32_t` — negatif ditolak pybind11 dengan `TypeError`;
nilai > 32 diterima lalu di-clamp internal. Tidak ada skala harga/ukuran;
semua `double` mentah seperti diterima.

### 13.3 Algoritma

```cpp
weighted_volume(sizes, n):
    total = 0.0
    for i in 0..n:  total += sizes[i] × (1.0 − 0.1 × i)   // urutan itu kontrak

ofi = (bid_vol − ask_vol) / (bid_vol + ask_vol);  clamp ±1.0
rel_spread = max(best_ask − best_bid, 0.0) / mid
```

> **Urutan akumulasi adalah bagian dari kontrak**, bukan detail
> implementasi: floating point tidak asosiatif, dan satu operasi yang
> dipindah bisa mengubah digit terakhir. Karena itu `-Ofast` dilarang
> (`CMakeLists.txt:14-18`) dan dijaga test.

> **`weighted_volume` tidak memanggil `level_weight`.** Keduanya menghitung
> ekspresi yang sama dari konstanta bernama (`kLevelWeight0 = 1.0`,
> `kLevelWeightStep = 0.1`) vs literal inline — **dua ekspresi independen
> yang kebetulan sama**. `book_ofi` meng-inline literalnya; `level_weight`
> diekspor. Tidak ada yang mengikat keduanya.

### 13.4 Paritas — terverifikasi saat runtime

Toleransi `1e-7`, lebih ketat dari yang float64 perlukan, dan itu
disengaja. Diverifikasi langsung:

```
PY  ofi (0.11229946524064167, 0.009950248756218905) depth 0.11229946524064167
CPP ofi (0.11229946524064167, 0.009950248756218905) depth 0.11229946524064167
```

**Bit-exact.** 22 test + 26 subtest hijau.

Toleransi dimethyl warehousing: `depth_imbalance` sengaja **tidak**
mengecek sisi kosong (hanya `denom <= 0`), mengikuti persis
`PythonKernel`. Akibatnya book dengan hanya bids memberi `+1.0` —
fiduh terhadap referensi, tapi tidak simetris dengan `order_flow_imbalance`
yang memang mengecek.

### 13.5 Tidak ada SIMD

Grep `immintrin`, `emmintrin`, `__m128`, `__m256`, `_mm_`, `xmmintrin` →
**nol match** di seluruh `cpp/`. Semua loop skalar. `-O2` saja; `-Ofast`
dilarang. Satu-satunya artefak yang "terlihat SIMD" adalah **namanya**.

### 13.6 Parser JSON tulis-tangan

`cpp/include/simdjson.h` — **bukan** upstream simdjson, dan file itu
mengatakannya sendiri di `:4-17`. Scanner sekali-p purposefully-built
±350 baris: `Status`, `Span`, `class Scanner` dengan `skip_ws`, `expect`,
`scan_string_span`, `scan_number_double`, `skip_value`. Tidak memiliki
memori — hanya `const char* data_`, `size_t len_`, `size_t pos_`.

**Dikompilasi secara default dan satu-satunya parser.** Tapi sakelar
upstream-nya mati: `HL_JSON_USE_SIMDJSON` hanya muncul di **komentar**
(`:16`). Tidak ada `#if` di seluruh `cpp/`, tidak ada referensi
`simdjson::`. `CMakeLists.txt:89-102` akan memvalidasi `SIMDJSON_ROOT`,
menambah include dir, mendefinisikan macro, mencetak "Menggunakan upstream
simdjson", dan menghasilkan binary dengan **perilaku identik**.

> `scan_number_double` **tidak mendukung ekspon**. `1e-5` diparse sebagai
> `1` lalu berhenti. Untuk JSON bursa hal ini tidak muncul, tapi notasi
> ekspon akan muncul dan diam-diam menggeser angka.
>
> `find_key` (`:169-209`) terimplementasi penuh tapi **nol pemanggil**;
> `Scanner::reset` dan `position` juga. Dispatch kunci di-roll manual di
> setiap level.

### 13.7 Binary terkirim

```
KERNEL32.dll, api-ms-win-crt-* (9), libwinpthread-1.dll, python314.dll
```

> **Tiga klaim CMake yang sudah tidak benar.**
> 1. `":129` — *".pyd mandiri: bisa disalin ke mesin lain tanpa MSYS"*. hostage
>    `libwinpthread-1.dll` masih ada, karena `std::mutex` menariknya masuk
>    dan tidak bisa di-static penuh (sudah dicoba spinlock, didokumentasikan
>    di header `:74-83`).
> 2. `":132-135` — *"Kalau `libwinpthread-1.dll` masih muncul, berarti ada
>    yang lupa ditaut"*. **Selalu muncul**, by design. Aturan verifikasi itu
>    akan selalu melaporkan kegagalan pada build yang benar.
> 3. Dua DLL yang dibutuhkan ada di root tapi **di-gitignore**
>    (`.gitignore:71-73`) dan `*.pyd` juga gitignored (`:62`). **Fresh clone
>    tidak bisa `import cpp_microstructure` meski build-nya sukses** —
>    gejalanya `ImportError: DLL load failed`, yang terlihat seperti modul
>    rusak padahal kodenya baik-baik saja.

`build/CMakeCache.txt` menyimpan path absolut compiler satu mesin
(`C:/msys64/ucrt64/bin/c++.exe`). Cache basi tidak portabel.

### 13.8 Empat cacat kernel

**① `ingest_l2` mematikan proses — tidak bisa di-catch**

```cpp
// bindings.cpp:32
void book_from_python(const py::object& bids, const py::object& asks,
                      tradebot::Book& book) noexcept {   // ← noexcept
    item.cast<std::pair<double,double>>();              // ← melempar cast_error
```

`noexcept` + lempar = `std::terminate`. Terverifikasi dengan eksekusi:

```
BASELINE OK
terminate called after throwing an instance of 'pybind11::cast_error'
  what(): Unable to cast Python instance of type <class 'tuple'> …
EXITCODE=127
```

`try/except BaseException` tidak menangkapnya. Jalur L2 terlindungi
**hanya kebetulan** — `hyperliquid_feed._parse_book:320-327` sudah memfilter
level buruk sebelum memanggil. Pemanggil lain akan membunuh proses.

**② `ingest_json` bergantung pada urutan kunci**

`is_l2book` baru diperiksa **setelah** loop (`:604-616`), sementara
`data` diparse begitu terlihat. Payload dengan `data` sebelum `channel`
dan `data`-nya rusak → `st` error → `!is_l2book` short-circuit → **silently
`None`**, bukan exception. Terverifikasi:

```python
b'{"data":{"coin":"BTC","levels":[[{"px":"x"}]]},"channel":"l2Book"}'  →  None
```

Langsung melanggar kontrak yang diklaim sendiri di `:344-347`: *"Hanya
payload l2Book yang benar-benar rusak yang boleh dianggap error."*

**③ Bobot level negatif di indeks ≥ 11**

`1.0 − 0.1×i` nol di i=10, negatif di i=11. `level_weight(1_000_000)` →
`-99999.0`. Pada book 20 level benar-benar bid-heavy:

```
depth= 5 → ofi 0.616
depth=10 → ofi 0.705   ← puncak
depth=12 → ofi 0.701
depth=20 → ofi 0.026   ← 96% sinyal hilang
```

Default `depth=5` dan `MAX_LEVELS=32`vs 20 level bursa membuat ini
latent. Tapi `depth` menerima `uint32` bebas tanpa pagar, dan
`level_weight` diekspor tanpa batas atas. Paritas tetap terjaga — ini
cacat desain bersama, bukan divergensi.

**④ NaN memberi hasil berbeda di kedua kernel**

```python
PythonKernel:  max(-1.0, min(1.0, nan))  →  1.0     (NaN tersamar jadi keyakinan penuh)
C++:           if (d < -1.0) ... if (d > 1.0) ...   →  nan lolos
```

Verifikasi: `PY depth_imbalance 1.0` vs `CPP depth_imbalance nan`.
Bentuk C++ lebih bisa dipertahankan, tapi bentuk Python melanggar kontrak
paritas yang jadi alasan seluruh file ada.

> **`reset(symbol)` tidak simetris.** C++ `BookStore::reset` menghapus
> **isi** tapi menyimpan **slot** map; Python `pop` membebaskan slot. Dengan
> `max_symbols=1`, setelah reset C++ melempar `RuntimeError: store
> mikrostruktur penuh` untuk simbol berikutnya sementara Python menerima.
> `symbol_count()` tidak berarti hal yang sama di kedua kernel.

### 13.9 Klaim thread-safety yang berlebihan

`microstructure_kernel.h:68-72` dan `.cpp:27-29` menyatakan mutex melindungi
registrasi dan lookup pointer saja. `BookStore::find` mengembalikan
**pointer mentah ke `Book` di dalam map, lalu melepas mutex**
(`:137-145`), sementara `set_book` melakukan `it->second = book` — copy 512
byte — di bawah mutex yang sama. Pembaca tidak memegangnya.

Justifikasinya (`header:69-71`) menyebut "lapisan Python menjamin satu book
hanya disentuh satu thread (GIL)" — tapi `bindings.cpp:63, 74, 113, 146,
155` **semua melepas GIL**, jadi jaminan itu tidak berlaku untuk operasi
yang justru penting.

---

## 14. Machine learning

### 14.1 Artefak

`ml/models/signal_model.pkl` — 3.9 MB, `RandomForestClassifier`,
sklearn 1.9.1. Diverifikasi dengan memuat:

```
n_estimators=100   max_depth=10    min_samples_split=10  min_samples_leaf=5
random_state=42    n_jobs=-1       max_features='sqrt'  criterion='gini'
classes_ = ['HOLD','LONG','SHORT']   n_features_in_ = 7
```

Tanpa `feature_names_in_` — pipeline tidak pernah memakai DataFrame
bernama, jadi sklearn tidak punya pagar nama untuk mendeteksi skew.

Feature importance pada artefak terkirim:

| Fitur | Importance |
|---|---|
| `atr_pct` | **0.260044** |
| `macd_hist_norm` | **0.179758** |
| `ema_trend` | **0.172063** |
| `rsi` | 0.153834 |
| `bb_position` | 0.144227 |
| `volume_ratio` | 0.090074 |
| `sentiment_score` | **0.000000** |

Dua fitur dengan bobot terbesar justru yang paling berversi-skew (§14.3).

### 14.2 Latihan

```python
# ml/trainer.py:135
RandomForestClassifier(n_estimators=100, max_depth=10, min_samples_split=10,
                       min_samples_leaf=5, random_state=42, n_jobs=-1)
```

Label (`:88`): `future_returns = close.shift(-10)/close − 1`;
`> +0.005` LONG · `< −0.005` SHORT · else HOLD.

Split (`:130`): `train_test_split(test_size=0.2, shuffle=False)` —
chronological, **tanpa purging atau embargo** padahal label maju 10 bar.
10 sampel terakhir fold training punya label yang horizonnya tumpang tindih
dengan fold test.

Data: ccxt Binance futures `BTC/USDT:USDT` 1h limit 1000, fallback yfinance
`BTC-USD` 1y. Minimum 100 sampel.

Guard `:125` mengembalikan `{"accuracy": 0, "error": "Data terlalu sedikit"}`
— **tanpa key `model_path`**, jadi pemanggil `result["model_path"]` akan
`KeyError`.

### 14.3 Lima train/serve skew

**① `sentiment_score` — kolom konstan saat dilatih, hidup saat serve**

`trainer.py:72-73` mengisinya `0.0` tanpa syarat. Variance di matriks
training **tepat nol**, dan importance di artefak **tepat 0.000000** —
tidak satu pun pohon bisa membelah pada kolom konstan. Tapi
`ml_signals.py:121` memberi nilai live yang nyata. Model menerima fitur
yang secara terbukti belum pernah dipelajari.

**② `ema_trend` — nama sama, fitur berbeda**

```
train:  ((ema9 − ema21) / close).clip(±0.01) × 100      → kontinu
serve:  1.0 / 0.5 / 0.0 / −0.5 / −1.0 dari STRING sinyal  → 5 titik
```

Importance 0.172 — mismatch ini tidak ringan. Train melihat distribusi
kontinu, serve hanya bisa menghasilkan lima nilai.

**③ `macd_hist_norm` — skala beda ~100×**

```
train:  (hist / close).clip(±0.01) × 100        # ternormalisasi harga
serve:  clip(hist / 100, −1, 1)                  # pembagi tetap
```

`hist` 0.35 → 0.0035 di serve, padahal di train = 0.35. Importance
0.180 — fitur Ranking-2 dengan shift distribusi 100×.

**④ `bb_position` — estimator berbeda**

Train: `(close − lower)/(upper − lower)`, NaN → 0.5. Serve: `float(bb_val)`
dari string 2-desimal, dengan fallback keras `0.0`/`1.0`/`0.5` saat parse
gagal — sehingga `LOWER_BAND`/`UPPER_BAND` menjadi 0/1 yang tak pernah
dihasilkan training.

**⑤ `volume_ratio` — truncasi**

Train: `(vol/sma).clip(0,3)/3` dengan guard NaN. Serve:
`min(ratio/3.0, 1.0)` tanpa clip bawah dan tanpa guard NaN, lewat
round-trip string.

Hanya `rsi` dan `atr_pct` yang konsisten antara train dan serve.

`_feature_names` (`ml_signals.py:37-40`) **tidak pernah dibaca** di mana
pun — ia dekoratif. Vektor dibangun posisional (`:130-133`) dan tidak
pernah divalidasi terhadap `n_features_in_`.

### 14.4 Fallback rule-based

Model hilang / `predict_proba` melempar → `_predict_rule_based` (`:189`),
ditandai `method: "RULE_BASED"`. Tidak ada exception yang keluar dari
`predict()`.

```python
score = 0
rsi < 0.3 → +1.5   · rsi > 0.7 → −1.5
rsi < 0.45 → +0.5  · rsi > 0.55 → −0.5
score += macd × 2.0
bb < 0.2 → +1.0    · bb > 0.8 → −1.0
score += ema × 1.5
score += sent × 1.0
# vol dan atr dibongkar di :195 dan TIDAK PERNAH dipakai

p_long  = sigmoid(score) ;  p_short = sigmoid(−score)
p_hold  = 1 − |p_long − p_short|   ;  normalisasi ulang
threshold 0.45 → LONG/SHORT, confidence = p_long atau p_short
```

Terverifikasi:

| score | p_long | p_short | p_hold | action |
|---|---|---|---|---|
| 0.00 | 0.2500 | 0.2500 | 0.5000 | HOLD |
| 0.80 | 0.4259 | 0.1914 | 0.3827 | HOLD |
| 0.90 | 0.4505 | 0.1832 | 0.3663 | LONG |
| 2.00 | 0.7112 | 0.0963 | 0.1925 | LONG |

> **Sentimen dan EMA tidak pernah bisa melewati ambang sendiri.**
> Keduanya punya bobot 1.0 dan 1.5, dan `sent ∈ [−1,1]`, `ema ∈
> [−1,1]`. Kombinasi maksimumnya 2.5 menghasilkan `p_long = 0.9241` —
> cukup. Tapi sentimental netral (0) + EMA lemah (0.5) = 0.75 → p 0.68 →
> HOLD.
>
> `confidence` tidak pernah dibatasi `p_hold` ketika LONG menyala, jadi
> LONG marginal di 0.4505 melaporkan confidence 0.4505 — **di atas gate
> `min_confidence` 0.40** dengan edge yang praktis nol.

### 14.5 Jalur yang mati

- `ml/predictor.py` — seluruh file. Nol importer. Produksi lewat
  `agents/analysis_agent.py:19` langsung ke `MLSignalGenerator`.
- `MODEL_DIR = Path("ml/models")` **path relatif terhadap CWD**, bukan
  repo root — dipatah saat bot dijalankan dari direktori lain.
- `extract_features` tidak punya cabang `return None`, jadi handler
  `method: "NONE"` di `:156-162` **tidak bisa dieksekusi**.
- `initialize()` memakai `asyncio.get_event_loop()` (deprecated) +
  `run_in_executor`, bukan `asyncio.to_thread` seperti bagian lain.

### 14.6 Backtester L2

`analysis/backtester.py` — 981 baris, **tidak dipakai produksi**. Dipakai
`tests/test_advanced_modules.py`.

`QueueFillModel` (`:239`) — FIFO dalam satu level harga. `on_trade`
mengembalikan seluruh sisa volume sekaligus ketika antrean habis;
bursa nyata memberi potongan proporsional. Jadi model ini **optimis di
sisi yang biasanya paling pesimis**.

Empat cacat yang tidak dipegang test:

| # | Lokasi | Masalah |
|---|---|---|
| 1 | `:645-694` | **Limit fill tidak pernah menyentuh `cash`**. Fee dicatat dan `Fill` ditambahkan, tapi tidak ada debit/kredit dan tidak ada `CompletedTrade`. Run limit-saja melaporkan PnL dan win rate nol sementara `avg_slippage_bps` bergerak |
| 2 | `:776`, `:828` | Partial exit membuang ukuran. `_worst_fill_price` boleh `filled_qty < quantity`; `:813-823` menghitung gross atas `filled_qty` saja lalu `open_trade = None` — sisanya hilang |
| 3 | `:203` | Sortino membagi `len(downside)` (jumlah seluruh return) bukan jumlah return negatif. Karena `min(0.0, r)` menghasilkan `0.0` untuk setiap return positif, penyebutnya selalu N. Rasio Terrydong naik `√(N/n_neg)` — 1.41× pada contoh 6 return |
| 4 | `:113-115` | `is_expired` mengembalikan `remaining > 1e-12` — **invers logis persis** dari `is_filled`. Tidak ada TTL di seluruh modul |
| 5 | `:472` | `taker_fee` default **0.0005** — 11% di atas tarif Hyperliquid produksi (0.00045) |

Docstring `:369-376` menjanjikan parameter `step_bps` yang sudah dihapus;
volatilitas sekarang di-hardcode `rng.gauss(0.0, 0.00015)` di `:391`.

---

## 15. Riset

44 `.py` di `research/` + `research/FINDINGS.md`. **Tidak ada kode produksi
yang meng-importnya** (grep `import research|from research` di luar
`research/` → nol). Arah dependensinya: research → produksi
(`audit_candidate.py`, `mirror_test_final.py`, `real_cost_backtest.py`,
`maker.py` meng-import dari `trading.fill_cost`).

`research/FINDINGS.md` dihapus bersama dokumen lama; isian penting sudah
diserap ke §6 dan §17.

### 15.1 Data

Sumber: `POST https://api.hyperliquid.xyz/info`,
`{"type":"candleSnapshot","req":{coin,interval,startTime,endTime}}`, tanpa
API key. `TIMEOUT=30`, `SLEEP=0.25`.

`CANDLE_LIMIT = 4200` (sengaja di bawah plafon senyap ~5.000), `DAYS = 400`,
21 simbol.

> **Jebakan yang sudah hampir-menimpa.** API **memotong hasil di ~5.000
> candle tanpa memberi tahu**. Request 5 hari untuk interval 1m meminta
> 7.200 dan hanya ~5.000 yang kembali — tanpa error, tanpa penanda
> truncation. Gejalanya: database terisi, tidak ada error, tapi hanya 4 hari
> history untuk 1m padahal meminta 400. Chunk harus dihitung dari interval:
> `chunk_ms = 4200 × INTERVAL_MS[interval]` → 1m 2.9 hari/request,
> 1h 175 hari.

### 15.2 Hasil: semua strategi directional gagal

| Jalur | Skala | Hasil |
|---|---|---|
| Time-series momentum | 1.008 (`search2.py`) + 2.160 (`bottomline.py`) kandidat | semua gagal |
| Swing horizon panjang | 450 (`swing.py`) | 1800 s menang di train, mati di validasi |
| Swing 2 j, SL 2%/TP 4% | 6 kandidat | **+0.3899/trade bull, −1.0183/trade bear** |
| EMA trend, 3 varian | — | +0.34…+0.73 bull, **−1.03…−1.11 bear** |
| Momentum dua arah | 2 ambang | negatif di kedua rezim |
| OFI Q1 fade | 24 jam data | t +3.72 → **gagal mirror** (drift) |
| RSI reversal | grid | tidak signifikan |
| Regime gate | 12 kombinasi | semua negatif |
| Regime trend filter | 4 mode × 3 fold | drift tidak bisa dieksploitasi |

Arah kegagalan konsisten: **rugi di bearish 1.4–3.2× lebih besar dari
keuntungan di bullish**. `FINDINGS.md` menyebutnya *"leverage tersembunyi
ke arah risiko yang salah"*. Gate rezim yang ketat mengurangi opportunity
tanpa memperbaiki ekspektasi.

**Empat aturan anti-look-ahead** yang dipakai di seluruh harness:
1. Isi di bar berikutnya (`s.o[i+1]`, bukan `s.o[i]`)
2. Rank dari trailing, tahan ke depan
3. Gate rezim hanya membaca return indeks historis
4. **SL diperiksa sebelum TP** — urutan high/low intrabar tidak diketahui,
   dan mengasumsikan yang menguntungkan adalah cara termurah membuat
   backtest berbohong

### 15.3 Hasil: cross-sectional momentum — satu-satunya edge

Dari `factor_sweep.py` (250 konfigurasi, indeks kolom sudah dikoreksi):

```
faktor    : cross-sectional momentum, trailing 12j, hold 12j
posisi   : long 5 terkuat + short 5 terlemah, simultan, dollar-neutral
n         : 415 rebalances selama 208 hari
t-stat    : +2.34
p-value   : < 0.002  (0 dari 500 baseline acak melebihi t=2.34)
Net P&L   : +4.460 USDT dari 10.000  (44.6% dalam 208 hari)
Sharpe    : ~3.1  (aproksimasi t·√(365/208))
Win rate  : 52.3%
Max DD    : 8.3%
Biaya     : maker 2.2 bps/leg
Leverage  : 1x
Reversed  : t = −4.25
OOS (2nd) : t = 2.11
```

Walk-forward expanding window — 4/4 fold positif, dan edge **menguat**:
```
Fold 4 → +296     Fold 5 → +585     Fold 6 → +766     Fold 7 → +1.397
```
Rezim: bull +1.045 (t 1.61) · bear +1.441 (t 1.45). Sub-periode 5/7 positif.

> **Syarat yang menentukan: harus maker.** Biaya taker 6.2 bps/leg
> menurunkan t dari 2.34 ke **0.60**. Edge tidak bertahan di taker.
> Contoh config #2 (dist-from-high, trail 12j / hold 72j): t 2.01, tapi
> hanya 69 sampel — "less reliable".

### 15.4 Kesalahan yang hampir lolos

Sesi pertama memakai `cs_close = r[5]` — itu **volume, bukan close**
(close di indeks 4). Ranking berdasarkan volume menghasilkan **+9.2M USDT
dari 10.000**, win rate 93%, t = 20+. Semuanya palsu. Setelah dikoreksi:
config yang sama memberi t = 0.60 dan semua `trail=6h` negatif.

`lower_turnover.py` mengukur kerabatnya: gross +1519 dari 205 rebalances
sebelum biaya, +248 sesudah — **biaya memakan 83.7% dari gross**.

### 15.5 Yang tidak diuji

| Jalur | Alasan |
|---|---|
| Mikrostruktur order flow | Data order book hanya ~25 jam (kini 25 jam juga), dan hanya bullish |
| Funding rate carry | butuh data funding historis lengkap |
| Machine learning | risiko overfitting tinggi dengan 208 hari |
| Sub-1 jam | plafon 5.000 candle/request |
| Multi-faktor | lebih baik satu faktor kuat |

---

## 16. Test suite

### 16.1 Angka

```
605 test fungsi di 29 file (tests/)
unittest discover : 605 test, OK (skipped=4)   ← 9.4 s
pytest            : 599 passed, 4 skipped, 2 FAILED
```

2 kegagalan pytest bukan bug: `test_live_tui.py::TestUnpatchedSmoke` —
`console.ask_mode` memanggil `input()` sementara pytest Detective
stdin (`OSError: pytest: reading from stdin while output is captured`).
`unittest` tidak punya capture itu, jadi hijau.

4 skip: `test_live_tui.py:372,378,385,389` — *"jalur POSIX tidak berlaku
di Windows"*.

Distribusi: `test_bugfixes.py` 62 · `test_live_tui.py` 65 · `test_advanced_modules.py`
59 · `test_lifecycle_paths.py` 54 · `test_live_safety.py` 45 · `test_live_engine.py` 36 ·
`test_neural_net_layout.py` 26 · `test_live_console.py` 27 · `test_cpp_kernel.py` 22 ·
`test_live_executor.py` 21 · `test_direction_agents.py` 20 ·
`test_fill_cost_funding.py` 17 · `test_probability_engine.py` 16 ·
`test_layout_contract.py` 18 · sisanya 3–14.

### 16.2 Test tanpa assertion

`tests/test_live_executor.py:161 test_close_short_is_sell` — **seluruh
badannya tanpa `assert` satu pun**:

```python
async def test_close_short_is_sell(self):
    rec = _Recorder()
    eng = _Engine(rec, {"BTC/USDT:USDT": self._position("SHORT")})
    await LiveExecutor(eng).execute_order(_order(action=TradeAction.CLOSE, side=Side.SHORT))
    await LiveExecutor(eng).execute_order(_order(action=TradeAction.CLOSE, side=None))
```

Lolos tanpa syarat — bahkan kalau `_close` mengirim `is_buy=True` untuk
short, bahkan kalau exception ditelan, bahkan kalau tidak pernah sampai
ke bursa. Produced `is_buy = position.side == "SHORT"` (`executor.py:675`);
menutup short live sebagai buy **menambah** eksposur short, bukan menutup.

YangSaudi: `test_close_long_is_buy:151` **memang** assert (`:159`). Arah
close LONG diperiksa; **arah close SHORT sama sekali tidak terverifikasi.**
Ini cacat terparah di seluruh suite, dan letaknya tepat di jalur uang.

Satu-satunya test tanpa assertion di empat file live/lifecycle.

### 16.3 Test yang membaca source text

Dua belas test membuka file `.py`/`.css`/`.yaml` dan asserting string
di dalamnya — bukan mengimpor modul dan menguji perilaku:

| Test | Membaca | Yang sebenarnya dijaga |
|---|---|---|
| `test_advanced_modules.py:353` | `agents/execution_agent.py` | string `get_dynamic_tp_sl_thresholds` |
| `test_advanced_modules.py:793` | `config.yaml` | ada blok `dynamic_tp_sl:` |
| `test_bugfixes.py:1107` | `run.py` | `add_fixed_job` ada |
| `test_bugfixes.py:1113` | `run.py` | `_maintenance_loop` ada |
| `test_bugfixes.py:1124` | `data/hyperliquid_feed.py` | `to_thread` ada |
| `test_bugfixes.py:1155` | `.gitignore` | pola ada |
| `test_cpp_kernel.py:271` | `cpp/microstructure_kernel.cpp` | konstanta bobot |
| `test_cpp_kernel.py:305` | header | array fixed-size |
| `test_cpp_kernel.py:318` | `bindings.cpp` | ada pelepas GIL |
| `test_cpp_kernel.py:337` | `CMakeLists.txt` | tidak ada `-Ofast` |
| `test_cpp_kernel.py:345` | `hyperliquid_feed.py` | fallback terpasang |
| `test_layout_budget.py:159` | `hud.py` | markup decision tree hilang |
| `test_dashboard_palette.py:49,58,82` | 3 file figure | tidak ada `var(--` |
| `test_dashboard_palette.py:74` | `style.css` | hex ada |
| `test_neural_net_layout.py:236` | `neural_flow.css` | pola dash cocok |

Semuanya lulus. Semuanya bisa lulus tanpa satu baris pun dieksekusi.

### 16.4 Test yang tidak bisa gagal

**`test_layout_contract.py:277 test_layout_files_have_no_hex_literals`** —
`:284` melakukan `re.sub(r"#.*", "", stripped)` yang menghapus segalanya
sejak `#` pertama, **lalu** `:285` mencari `#[0-9a-fA-F]{3,6}`. Setelah
strip, `#` tidak pernah ada, jadi hasilnya selalu `[]`.

**`test_live_executor.py:226`** — `assertNotIn("PaperTradingEngine", str(params["engine"]))`.
`agents/execution_agent.py:37` mendeklarasikan `def __init__(self, event_bus, engine)`
**tanpa anotasi**, jadi `str(params["engine"])` adalah literal `'engine'`.
Reduksinya: `"PaperTradingEngine" not in "engine"` → selalu `True`.

**`test_layout_contract.py:93 test_every_callback_input_id_is_mounted`** —
`_dash_layout_ids` (`:74`) meng-union semua literal `id="…"` dari
`app.py`. Ketiga id yang diperiksa dideklarasikan di sana. Tidak pernah
bisa gagal. Saudaranya di `:81` (Output) punya coverage nyata.

**`test_layout_budget.py:206`** — regex `r'style=\{"height":\s*"([^"]+)"'`
terikat pada spasi dan kutip ganda persis. Reformat dict, atau pakai
`style=dict(height=…)`, membuat `finditer` mengembalikan nol match — loop
tidak pernah jalan, dan test melapor hijau tanpa assertion.

**`test_layout_budget.py:122 test_giant_pnl_uses_the_type_scale`** —
menangkap rule `.giant-pnl` di `:393` yang pakai `var(--fs-hero)`.
Rule `.rail-hero .giant-pnl` di `:856` meng-override dengan `26px` literal
460 baris kemudian. Test ini menjaga rule yang sudah tidak mengendalikan
output.

**`test_layout_contract.py:342` vs `:392`** — dua test di file sama
menuntut hal berlawanan tentang satu selector: `:342` mensyaratkan
literal `.symbol-select .dash-dropdown` ada; `:392` melarang **persis**
bentuk descendant itu dengan pesan *"`tidak akan match: keduanya satu
elemen`"*. Keduanya hijau karena `:342` adalah `assertIn` **prefix** —
terpuaskan oleh `.symbol-select .dash-dropdown-value` di `:994`.

**`test_neural_net_layout.py:322`** — docstring bilang *"jaraknya > 34px"*,
assertion di `:333` adalah `assertGreaterEqual(min(gaps), 24.0)`.
**Angka yang dijaga bukan angka yang ditegakkan.**

**`test_layout_contract.py:165`** — `assertLessEqual(len(anims), 4)` tapi
`style.css` cuma punya **1** keyframe. Keyframe yang menggerakkan setiap
edge animasi ada di `neural_flow.css:49` dan tidak pernah dihitung.

Lainnya: `:308` hanya assert `f".{cls}"` muncul di CSS (komentar cukup
memenuhinya, tidak pernah cek body aturan ada) · `:292` `src.index(...)`
melempar `ValueError` saat rename · `test_layout_budget.py:165` fallback
ke `44` hardcoded saat rule `.scanner-odds-row` dihapus ·
`test_layout_budget.py:78` `row_h` semua hardcoded, tidak pernah baca CSS ·
`test_dashboard_palette.py:73` `assertIn(PAPER_BG, css)` substring biasa ·
`:80` 12 hex `GITHUB_DARK` dicocokkan case-sensitive.

### 16.5 Duplikasi logika produksi di dalam test

| Lokasi | Menyalin |
|---|---|
| `test_live_engine.py:29-34` | `FakeOutcome.describe` — **tiga string Indonesia verbatim**, dan nol test yang assert pada `describe()`, padahal produksi menyisipkannya di 4 pesan user-facing (`engine.py:325,329,350,366`) |
| `test_lifecycle_paths.py:899` | `expected_margin = (qty × 50000) / 10` — `10` hardcoded bukan dari `leverage`; di balik `if s.get("quantity")` jadi **diam-diam no-op** kalau sizing return 0 (yang bisa terjadi dari `ROUND_DOWN` `risk_manager.py:389`) |
| `test_lifecycle_paths.py:77-78` | fixture default SL `price × 0.9`, TP `price × 1.1` — test yang lupa pass dapat trigger 10% dari harga, vs 0.40%/0.60% produksi |
| `test_lifecycle_paths.py:111,251,362` | exit cost dihitung dari `50000.0 × 0.2` (harga pasar mentah), sementara produksi pakai `quantity × fill_price` yang **sudah digeser** — ekspektasi test beberapa bps lebih kecil dari yang ditagih produksi. `close_fill_price` juga dipanggil tanpa `config=`, berbeda dari `position_manager.py:223` |

### 16.6 Log produksi tercemar test

`data_store/logs/trading_bot.log` berisi **11.049 dari 70.988 baris
(15.6%)** yang menyebut `test_*.db` — test menulis ke logger yang sama
dengan produksi. 8.028 baris "Penghitung harian di-rollover" adalah artefak
`test_live_safety.py` yang memakai `_midday()` = `2026-01-15`, bukan
2026-10-03. Itu sebabnya log tampak menunjukkan tanggal **mundur** —
`2026-09-29 -> 2026-01-15` — yang bukan bug produksi.

### 16.7 Sampah di repo

28 file `data_store/test_live_gate_<pid>.json` yatim —
`test_live_engine.py:116` `_gate()` hanya unlink file PID-nya sendiri saat
masuk, tidak pernah membersihkan saat keluar.

`tests/t3.py` memanggil `asyncio.run(main())` di module scope **tanpa
guard `__main__`** — mengimpornya membuka 7 sesi WebSocket live ke API
publik Hyperliquid.

`test_live_session.py:8` meng-import `run.py`, yang di module level `:39`
menjalankan `WSGIRequestHandler.log_request = _silence_request_log` —
**monkeypatch logger request global Werkzeug untuk seluruh proses pytest**,
sambil `:12 get_logger(…)` membuat folder dan file log saat collection.

### 16.8 Kontrak yang dijaga dengan baik

- **Paritas kernel C++** — 22 test, toleransi `1e-7`, 26 subtest. Hijau
  di mesin ini (§13.4).
- **Gerbang live** — 45 test `test_live_safety.py`, hampir semua mencoba
  **menembus** gerbang dan membuktikan kegagalannya. Filosofi benar:
  kegagalan paling merusak di sistem uang adalah gerbang yang tidak
  menolak.
- **Migrasi `mode`** — 7 test, termasuk idempotensi dan pemisahan
  paper/live.
- **Kontrak DOM dashboard** — `test_every_callback_output_id_is_mounted`
  punya coverage nyata (56 `Output(` call dicocokkan).
- **Spesialis ensemble** — 20 test mencakup abstain-tanpa-data, simbol
  tak dikenal, dan funding counter-sentiment.

### 16.9 Cakupan yang tidak ada

| Perilaku | Yang menguji |
|---|---|
| `analysis/fundamental.py` (154 baris) | **tidak ada test sama sekali** |
| `analysis/ml_signals.py` (258 baris) | **tidak ada test sama sekali** |
| `analysis/vol_target.py` (133 baris) | mati dan tidak diuji |
| `data/*` (1.863 baris) | hanya `test_order_book_recorder.py` 12 test |
| `trading/live/tui.py` (572 baris) | 65 test, tapi 4 skip POSIX |
| `trading/live/console.py` (751 baris) | 27 test |
| Boot `run.py` | tidak ada |
| Shutdown | `test_lifecycle_paths.py` sebagian |
| Skema DB / migrasi | `test_mode_column.py` saja |
| Filter OHLC | hanya lewat integrasi |

---

## 17. Daftar defect

Semua diurutkan dampak. Yang ditandai **[TERBUKTI]** sudah direproduksi
dengan eksekusi atau ukur, bukan hasil baca kode saja.

### 17.1 Strategi — blocking

**① `trading/cross_sectional.py` tidak terhubung ke apa pun** **[TERBUKTI]**

Modul 229 baris berisi satu-satunya edge yang terverifikasi (§15.3).
Grep seluruh repo: **nol import**. `CrossSectionalStrategy` tidak pernah
diinstansikan, `get_desired_positions()` nol pemanggil, nol test.

Docstring modul menyatakan *"Modul ini TIDAK menggantikan DecisionAgent.
Ia menyediakan sinyal ke DecisionAgent lewat event bus"*. **Tidak ada kode
yang melakukan itu.** `research/FINDINGS.md` yang lama mencantumkan
`get_desired_positions()` sebagai *"interface ke DecisionAgent"* — itu
klaim yang tidak pernah diimplementasikan.

Selain itu: butuh minimal `N_SIDE*2 + 2 = 12` simbol dengan 12 jam
history (`:160`), sementara `config.symbols` berisi **10**. Rebalance
yang berhasil secara aritmatika mustahil terjadi dengan daftar simbol
sekarang.

Cost model juga tidak kompatibel: modul ini mengasumsikan **maker**
(1.5 bps + 0.7 bps spread = 2.2 bps/leg), tapi `fill_cost.py` membebankan
taker tanpa jalur maker (§6). Hooking modul ini apa adanya ke
`PaperTradingEngine` akan menagih 9 bps fee + ≥2.4 bps slippage terhadap
edge ~0.18/trade — dan `fill_cost.py:185` sendiri mengomentari itu.

### 17.2 Live — blocking

**② `client.py:219` — `int()` pada field string** **[TERBUKTI]**

`ValueError` pada setiap order saat ada posisi terbuka. Rantai dan
konsekuensinya di §8.8①. Tertutup stub di `test_live_engine.py:63`.

**③ `engine.py:162` vs `executor.py:472` — format kunci simbol beda** **[TERBUKTI]**

`reconcile` selalu melaporkan divergensi → kill switch; `emergency_flat`
tidak pernah bisa melaporkan `flattened: True`. §8.8②.

**④ `health_check` latch kill switch pada fill normal** **[TERBUKTI]**

Strike pertama pada setiap posisi menghentikan trading permanen. §8.8③.

**⑤ Gating tidak pernah terpenuhi** **[TERBUKTI]**

Tidak ada kode non-test yang meng-set `TRADEBOT_LIVE` /
`TRADEBOT_LIVE_CONFIRMED`. Live tidak bisa mengirim apa pun. §8.2.

### 17.3 Kernel native

**⑥ `ingest_l2` mematikan proses** **[TERBUKTI]** — exit code 127,
tidak bisa di-catch. §13.8①.

**⑦ `ingest_json` bergantung urutan kunci** **[TERBUKTI]** — payload
rusak jadi `None` alih-alih exception. §13.8②.

**⑧ NaN → 1.0 di Python, `nan` di C++** **[TERBUKTI]** — melanggar
kontrak paritas. §13.8④.

**⑨ `.pyd` tidak mandiri** **[TERBUKTI]** — masih butuh
`libwinpthread-1.dll`; dua DLL gitignored; fresh clone tidak bisa import.
§13.7.

**⑩ Sakelar upstream simdjson mati** — `HL_JSON_USE_SIMDJSON` tidak ada
`#if`; build "berhasil" dan tidak mengubah apa pun. §13.6.

### 17.4 Akuntansi

**⑪ Fill SL/TP di bursa tidak masuk DB** — baris menggantung `OPEN`
selamanya. §8.6.

**⑫ `get_trade_stats()` tanpa `mode=`** — parameter ada, nol pemanggil
dengan filter. Win rate dan PF di `account` mencampur paper + live;
`get_daily_realized_pnl` juga. §6.

**⑬ `close_position` 6 commit tanpa transaksi** — crash di tengah
meninggalkan posisi CLOSED dengan margin belum kembali. §6.

**⑭ Breakeven live ditimpa ≤ 2 detik** — tulis DB, ditimpa balik oleh
`check_positions`. Tidak ada trigger bursa yang digeser. §8.9.

**⑮ Tidak ada lookup by cloid; `cancel()` nol pemanggil** — timeout bisa
menggandakan posisi. Idempotensi diklaim di 3 docstring, nol
implementasi. §8.9.

**⑯ `profit_factor == inf` → `0`** — akun yang belum pernah kalah
dicatat sebagai profit factor **nol**, nilai terburuk yang ada.

**⑰ `take_balance_snapshot` menulis wallet ke kolom `balance`** —
`balance_history.balance` = dompet; `account.balance` = kas. Nama kolom
menyesatkan.

### 17.5 Ekonomi

**⑱ R:R nyata 1:2, bukan 1:1.04** **[TERBUKTI]** — winner +0.5026,
loser −1.0113. Butuh 66.8% win rate, aktual 30.0%.

**⑲ `max_risk_per_trade` tidak membatasi kerugian** — hanya besar margin
terkunci (~50 USDT). `notional = margin × leverage`.

**⑳ Volatility gate mati struktural** **[TERBUKTI]** — butuh SL < 0.105%,
dijamin ≥ 0.400%. Swept 0 konfigurasi menyala.

**㉑ Jalur maker tidak ada** — `MAKER_FEE` dibaca config tapi nol
pemanggil; semua fill TAKER; `Order.order_type` tak dibaca engine mana pun.

**㉒ 20 dari 250 posisi melewati `max_hold_seconds`** — 20 `SCALP_EXPIRED`
+ 3 `SCALP_TP` berdurasi ~44 jam. Presisi `max_hold` longgar atau
`_auto_close_expired` tidak jalan saat bot mati.

**㉓ 3 posisi OPEN selama ~50 jam** — konsekuensi langsung ㉒.

### 17.6 Data

**㉔ FRED mati permanen** **[TERBUKTI]** — nol config key, nol env var;
`macro_data` = 0 baris selamanya.

**㉕ `risk_level` selalu `HIGH`** **[TERBUKTI]** —
`interpret_macro_context([], [])` mengembalikannya, jadi leverage selalu 5.
`_determine_leverage` punya 3 cabang; hanya satu yang bisa tercapai.

**㉖ `_ccxt_available = False` permanen** — satu timeout mematikan Binance
untuk seluruh umur proses.

**㉗ Funding rate hardcode dikembalikan sebagai data** — tahap fallback
`0.0001` / `"Setiap 8 Jam"`, tanpa key `source`.

**㉘ Ticker HL menyuntik spread ±1 bp sintetis** — ke event stream dari
tier yang didokumentasikan mengembalikan depth asli. §9.2.

**㉙ Skema order book usang di DB utama** — 8.180 baris, nol prune. §9.3.

**㉚ `news_exists` tanpa UNIQUE** — check-then-insert tanpa pagar skema.

**㉛ `macro_data` UNIQUE dengan period nullable** — NULL tidak pernah
bertabrakan, `upsert_macro` jadi INSERT untuk baris itu.

### 17.7 Dashboard

**㉜ 18 dari 19 callback di 2 Hz** termasuk 8 legacy yang mengisi
`display:none`. ~30 query/detik. §12.2.

**㉝ Koneksi dashboard tanpa `busy_timeout`** + `conn.close()` tanpa
`finally` di 16 situs → panel membeku tanpa error, koneksi bocor. §12.3.

**㉞ `create_equity_chart` legacy salah** — sumbu-x 300 string kosong,
garis Balance nol, tersembunyi di `try/except`.

**㉟ `compute_mathematical_edge` melaporkan edge positif tanpa trade** **[TERBUKTI]**
— floor 0.10%, default 0.55 → 0.4688% dengan nol trade.

**㊱ Badge latency mengukur dirinya sendiri** — floor 2 ms.

### 17.8 Log dan utilitas

**㊲ `end_quiet_mode()` tidak restore level handler** **[TERBUKTI]**
`TRADEBOT_QUIET_STARTUP=1` → console **selalu** WARNING selama proses.
Trade, closure, PnL tidak pernah tampil di terminal. `run.py:976` memanggil
`end_quiet_mode()` dengan keyakinan itu memperbaiki, padahal tidak.
Diverifikasi: level 30 sebelum dan sesudah.

**㊳ `TRADEBOT_VERBOSE_STARTUP`** — didokumentasikan dua kali, nol kode.

**㊴ `begin_quiet_mode` / `is_quiet()`** — nol pemanggil.

**㊵ 11 token CSS + 2 token font mati**; `--bg-app` duplicat; `.giant-pnl`
di-override test yang menjaga type scale.

### 17.9 Kernel volatilitas dan config

**㊶ Kunci cache ATR tidak menyertakan `period`** — tabrakan dalam
jendela 5 detik.

**㊷ `getattr(...) or default`** swallow nilai config `0.0` — reverted ke
literal.

**㊸ `_load_candles` hardcode `"1m"`** — kontrak menjanjikan timeframe.

**㊹ `agent_log_prune_interval`/`agent_log_keep` di YAML tidak pernah
dibaca** — filter hanya meneruskan `snapshot_*`. Kebetulan sama dengan
default.

**㊺ 10 field duplikat di `DynamicTpSlConfig`** — bayangan
`ScalpingConfig`, nol yang membaca yang kedua.

**㊻ `exchange.*` nol consumer** — `ExchangeConfig` sepenuhnya tak terpakai.

### 17.10 Test

**㊼ `test_close_short_is_sell` tanpa assertion** **[TERBUKTI]** — jalur
uang live. §16.2.

**㊽ 12 test baca source text** — bisa lulus tanpa eksekusi. §16.3.

**㊾ 2 test tidak bisa gagal** (`test_layout_files_have_no_hex_literals`,
`test_execution_agent_takes_generic_engine`) **[TERBUKTI]**, 1 test menjaga
rule yang sudah di-override, 1 test vakum saat format berubah, 2 test
bertentangan soal satu selector. §16.4.

**㊿ `fundamental.py` dan `ml_signals.py` nol test** — 412 baris logika
tanpa coverage.

### 17.11 Komentar yang bertentangan dengan kode

| Klaim | Lokasi | Kenyataan |
|---|---|---|
| "Kegagalan TIDAK menjatuhkan sistem" | `hyperliquid_feed.py:107-110` | `:120` meng-`raise` keluar dari `websocket_loop` |
| "sama dengan formula yang dipakai kernel" | `order_book_recorder.py:116-117` | kernel default `depth=5`, rekorder 10 |
| "FinBERT max 512 token" | `sentiment.py:109` | 512 **karakter** |
| "Semua dibaca dalam satu kali jalannya" | `live/engine.py:192-193` | 3 network read |
| "NOL alokasi string di lapisan ini" | `microstructure.py:268-270`, `.h:116-117` | satu `std::string` per panggilan di boundary pybind11 |
| "Hanya payload l2Book rusak yang jadi error" | `microstructure_kernel.cpp:344-347` | data sebelum channel → `None` |
| "THREAD-SAFE … mutex melindungi lookup" | `microstructure_kernel.cpp:27-29` | GIL dilepas; pointer tanpa lock |
| ".pyd mandiri" | `CMakeLists.txt:129` | masih butuh `libwinpthread-1.dll` |
| "Kalau libwinpthread muncul, ada yang lupa taut" | `CMakeLists.txt:132-135` | **selalu muncul**, by design |
| `OFI = (ΣBid − ΣAsk)/(ΣBid + ΣAsk)` | `probability_engine.py:15` | implementasi pakai bobot `1 − 0.1·i` |
| "indikator Semuanya memiliki bukti" | `vol_target.py:18-21` | kalimat terpotong |
| "reset(symbol)" membebaskan slot | `microstructure.py:172-176` | C++ menyimpan slot |

Ditambah: `position_manager.py:308` punya `\open_fee\,` — backslash
lolos jadi teks literal; `core/logger.py:141` "Tokenizeriebner" dan
`:206` "Texture confuse"; `trading/live/tui.py:333` kalimat terpotong
`dengan.Does`; `run.py:105-106` kalimat menggantung karena nama path
dihapus dari tengah; `analysis/volatility.py:12` "Mqnormal", `:141`
"antar menit"; `config.py` dan `risk_manager.py` masih menyebut
`tight_sl_pct` 0.25% yang sudah 0.40%.

### 17.12 Dead code

| Item | Lokasi |
|---|---|
| `vol_target.py` seluruh modul | 133 baris, 4 fungsi, nol importer |
| `backtester.py` seluruh modul | 981 baris, nol importer produksi |
| `analysis/backtester.py` `is_expired` | invers logis dari `is_filled` |
| `probability_engine.compute_composite_probability` | 98 baris, nol pemanggil |
| `probability_engine.compute_diffusion_curve` | `DEPRECATED` sendiri |
| `probability_engine.calculate_realized_volatility` | nol pemanggil |
| `fundamental.should_reduce_risk` / `get_suggested_leverage` | nol pemanggil |
| `ml/predictor.py` seluruh file | nol importer |
| `risk_manager.kelly_criterion` | nol pemanggil |
| `fill_cost.describe_cost` | nol pemanggil |
| `cross_sectional.SPREAD_FLOOR`, `_initialized` | nol pemanggil |
| `models.PositionInfo`, `OrderType`, `TradeDecision.risk_pct` | nol referensi |
| `live: Blocker.RECONCILIATION_FAILED` | tak pernah di-append |
| `SafetyGate.disengage_kill_switch` | nol pemanggil produksi |
| `LiveExchange.cancel(coin, oid)` | nol pemanggil |
| `safety: cfg.use_exchange_side_tpsl`, `reconciliation_tolerance_days` | nol referensi |
| `tui.NotATerminal`, `fallback_prompt`, `LEFT`/`RIGHT` | nol pemanggil |
| `Repository.insert_candle`, `insert_trade` | nol pemanggil produksi |
| `order_book_recorder.count_rows` | nol pemanggil |
| `HyperliquidFeed._rest_failures`, `.event_bus` | nol pembaca |
| `SentimentAnalyzer.analyze_finbert_async` | nol pemanggil |
| `microstructure.get_kernel`, `reset_microstructure` | test saja |
| `EventBus.get_channel_stats` | nol pemanggil |
| `microstructure_simdjson.Scanner.find_key/reset/position` | nol pemanggil |
| `Book.overflow_bid/ask` | di-set, tidak pernah dibaca/di-bind |
| `ml_signals._feature_names` | nol pembaca |
| `dashboard: derive_macro_bias` | 40 baris, nol pemanggil |
| `dashboard: 6 layout builder + 4 import tak dipakai` | nol pemanggil |
| `Channels`: `BALANCE_UPDATE`, `AGENT_LOG`, `SYSTEM_EVENT`, `DIRECTION_ENSEMBLE` | konstanta mati |
| `DecisionAgent._global_loss_streak`, `us_market_open` | dihitung, tak dibaca |
| `ExecutionAgent` `now` di `:283` | ditugaskan, tak dipakai |
| `scheduler` 4 channel + 11 token CSS | mati |

---

## 18. Yang tidak diverifikasi

Hal-hal berikut **tidak** diklaim sudah benar di dokumen ini:

1. **Tidak ada order live yang pernah dikirim.** Gerbang menolak semua
   (§8.2). Tidak ada testnet, tidak ada dry-run.
2. **Tidak ada rekaman jalannya bot.** 19.772 baris "rollover" di log
   produksi adalah artefak test, bukan operasi. Perilaku runtime hanya
   disimpulkan dari kode dan DB.
3. **Tidak ada validasi FRED.** `interpret_macro_context` pernah
   dieksekusi dengan data nyata? Tidak — nol baris `macro_data`.
4. **Dashboard belum dibuka di browser** selama penulisan dokumen ini.
   Klaim tentang render, geometri, dan animasi berasal dari kode dan dari
   `data_store/*_report.json` yang dihasilkan probe Playwright sebelumnya.
5. **Paritas C++ diverifikasi pada 1 skenario numerik**, bukan fuzz.
   22 test suite + 26 subtest hijau, tapi claim "bit-exact" di §13.4
   berasal dari satu pembandingan yang saya jalankan sendiri dengan book
   3-level.
6. **`analysis/backtester.py` tidak pernah dijalankan** selain test. Four
   cacatnya dit statically; dampaknya pada hasil numerik belum diukur.
7. **Research tidak direproduksi.** Angka di §15 lifted dari
   `research/FINDINGS.md` dan output yang tersimpan, bukan dari
   eksekusi ulang `factor_sweep.py`. 11 dari 44 skrip riset tidak tracked
   di git, dan punya kontaminasi loader `r[5]` yang dijelaskan di §15.4.
8. **Perilaku `pandas_ta` 0.3.x tidak diuji** — hanya 0.4.71b0 terpasang.
9. **Tidak ada klaim soal performa throughput** selain benchmark query
   di §12 (0.03–3.42 ms pada data terkini). Langsung pada 10-symbol top
   volume, V2 belum diukur.
10. **Windows-only.** `display_width`, VT processing, MinGW static link,
    `cp1252` guard — semua itu asumsi platform. Tidak ada verifikasi
    POSIX.

---

## Lampiran A — Peta file

```
run.py                      1113   orkestrasi
config.yaml                  263   konfigurasi
CMakeLists.txt               153   build kernel
requirements.txt              46   dependensi

core/
  config.py                 1041   dataclass + loader + 3 validator
  logger.py                  425   formatter + progress bar + rotasi
  microstructure.py          370   ABC + PythonKernel + adapter + register
  market_store.py            266   state in-memory
  scheduler.py               139   APScheduler + deteksi US market
  event_bus.py               104   pub/sub + Channels
  utils.py                    31   parse timestamp UTC

data/
  price_feed.py              706   multi-tier OHLCV + ticker + funding
  hyperliquid_feed.py        573   WS + REST
  order_book_recorder.py     464   rekorder L2 ke file terpisah
  macro_fetcher.py           219   FRED + Forex Factory
  news_fetcher.py            155   RSS + CryptoPanic
  sentiment.py               214   VADER + FinBERT

analysis/
  backtester.py              981   L2 queue simulator (mati di produksi)
  probability_engine.py      434   Φ, OFI facade, z-score, kurva difusi
  volatility.py              413   ATR + gate volatilitas
  technical.py               315   11 indikator pandas-ta
  ml_signals.py              258   RandomForest + fallback rule-based
  direction_ensemble.py      228   log-odds pooling (JALUR HIDUP)
  fundamental.py             154   makro + sentimen → bias
  vol_target.py              133   (seluruhnya mati)

trading/
  paper_engine.py           1117   ladder + guard tick + fill
  live/executor.py          1064   adapter live → ledger
  position_manager.py        613   lifecycle posisi
  live/engine.py             791   order → proteksi → rekonsiliasi
  live/console.py            751   menu mode + editor limit
  live/tui.py                572   TUI cross-platform
  risk_manager.py            446   sizing + liq + SL/TP + 4 breaker
  live/safety.py             530   SafetyGate + DayCounters
  fill_cost.py               387   SATU-SATUNYA model biaya
  live/client.py             492   SDK Hyperliquid
  cross_sectional.py         229   edge terverifikasi (TIDAK TERHUBUNG)
  models.py                   87   enum + dataclass

agents/
  decision_agent.py          556   gate entry/exit
  direction_agents.py        526   4 spesialis + coordinator
  execution_agent.py         319   eksekusi + TP/expiry/breakeven
  analysis_agent.py          318   teknik + fundamental + ML
  base_agent.py              152   ABC sense/think/act
  news_agent.py              146   berita + sentimen

database/
  repository.py              642   42 method
  db.py                      325   skema + WAL + migrasi
  models.py                  140   dataclass entitas

dashboard/
  callbacks/update_callbacks.py  1429   19 callback
  layouts/hud.py             441   grid 12 kolom
  layouts/hud_figures.py     984   5 builder Plotly
  layouts/palette.py         139   warna
  layouts/performance.py     160
  layouts/price_chart.py     209
  layouts/news_feed.py       117
  layouts/positions.py        95
  layouts/agent_logs.py       69
  assets/style.css          1253   59 token
  assets/neural_flow.css      89   animasi neural
  app.py                     148   server Dash

ml/
  trainer.py                 234   RandomForest
  predictor.py                45   (mati)
  models/signal_model.pkl   3.9 MB

cpp/
  microstructure_kernel.cpp  644
  include/microstructure_kernel.h  173
  bindings.cpp               165
  include/simdjson.h         348   (bukan simdjson)
```

## Lampiran B — Perintah verifikasi

Semua klaim di dokumen ini bisa dicek ulang:

```bash
# Skema + isi DB
sqlite3 data_store/trading_bot.db ".schema"
sqlite3 data_store/trading_bot.db "SELECT close_reason, COUNT(*), SUM(realized_pnl>0) FROM positions WHERE status='CLOSED' GROUP BY 1"

# Test
python -m unittest discover tests
python -m pytest tests -q -rs

# Paritas kernel
python -c "
from core.microstructure import *
initialize_native_kernel(); register_kernel(PythonKernel())
b=[[100.,1.],[99.,2.],[98.,3.]]; a=[[101.,1.5],[102.,2.5],[103.,.5]]
ingest_l2('BTC',b,a); print(order_flow_imbalance('BTC',3), depth_imbalance('BTC',3))"

# Abort proses (jalankan terpisah; ini memang mematikan)
python -c "
from core.microstructure import initialize_native_kernel, ingest_l2
initialize_native_kernel(); ingest_l2('X',[(1.0,'2.0')],[(3.0,1.0)])"
echo \$?    # 127

# Ensemble ceiling
python -c "
from core.config import get_config
from analysis.direction_ensemble import aggregate, make_verdict
r=aggregate([make_verdict('orderflow','X','LONG',1.0)], get_config().ensemble)
print(r['prob_long'], r['confidence'])"

# Gating live tidak pernah terpenuhi
grep -rn 'TRADEBOT_LIVE' --include=*.py . | grep -v tests/ | grep -v live_doctor

# Config tak terbaca
grep -rn 'agent_log_prune_interval' core/config.py

# macro_data kosong
sqlite3 data_store/trading_bot.db "SELECT COUNT(*) FROM macro_data"

# Log tercemar test
grep -c 'test_' data_store/logs/trading_bot.log
```
