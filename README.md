# Sistem AI Crypto Futures Trading Agent (Paper Trading)

Sistem agen kecerdasan buatan otonom untuk perdagangan berjangka kripto (*crypto futures*) berskala simulasi (*paper trading*) dengan **biaya $0 ($0-cost)** dan **tanpa memerlukan API key berbayar ataupun kredensial rahasia bursa**.

Sistem dirancang dengan arsitektur multi-agen asinkron (*event-driven*), terhubung ke data publik bursa, agregator berita global, kalender makroekonomi, model *Machine Learning* (RandomForest) lokal, serta *dashboard* pemantauan *real-time* berbasis web.

> **Dokumentasi lengkap ada di [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)** —
> 18 bagian dari pembacaan baris-per-baris source, dengan `file:line` untuk
> tiap klaim dan daftar 53 defect terverifikasi. README ini ringkas dan
> beberapa angkanya sudah usang; dokumen itu yang benar.
>
> **Status nyata sistem ini, singkat:**
> - Jalur **live belum pernah mengirim order** — `SafetyGate` menolak semua
>   karena `TRADEBOT_LIVE` tidak pernah di-set oleh kode mana pun (§8.2).
> - Jalur **paper berjalan tapi strateginya rugi**: 250 trade, profit factor
>   0.213, win rate 30% terhadap 66.8% yang dibutuhkan untuk impas (§6).
> - Riset menemukan **satu edge yang valid** (cross-sectional momentum,
>   t = 2.34, 4/4 walk-forward) dan mengimplementasikannya di
>   `trading/cross_sectional.py` — **tapi modul itu tidak terhubung ke
>   apa pun** (§17.1).

---

## Gambaran Arsitektur (Kondisi Riil)

```
Hyperliquid WebSocket (L2 20 level, candle, allMids, funding, trades)
        │
        ▼  snapshot penuh per update
┌───────────────────────────────────────┐
│ core/microstructure.py                │  ← HOT PATH KERNEL
│  · parsing L2 · OFI · market depth    │    (siap diganti C++/Rust)
│  · antarmuka: MicrostructureKernel    │
└───────────────────────────────────────┘
        │ .kernel.order_flow_imbalance()
        ▼
┌───────────────────────────────────────┐
│ 4 agen spesialis arah                 │
│  orderflow(0.30) momentum(0.25)       │
│  technical(0.25) microstructure(0.20) │
└───────────────────────────────────────┘
        │  log-odds pooling + shrinkage 0.85 + agreement bonus
        ▼
   P(LONG) / P(SHORT)  ──►  tabel `direction_snapshots` (tiap 5 dtk)
        │                                    │              ▲
        │ DecisionAgent baca snapshot        │              │ sumber
        ▼ (fresh < 20 dtk, conf ≥ 0.40)      └── HUD 12 kolom┘
   Order → PaperTradingEngine → PositionManager → SQLite WAL (11 tabel)
        │  guard tick · SL/TP ROUND_DOWN · circuit breaker rugi harian
        ▼
   Dashboard Dash/Plotly @ http://127.0.0.1:8050 (interval 500 ms)
```

**Titik paling penting:** tabel `direction_snapshots` adalah *satu sumber
kebenaran*. Angka yang memicu entry sama persis dengan angka yang tampil di
layar, karena `DecisionAgent` dan callback HUD membaca tabel yang sama.

---

## Fitur Utama

1. **Arsitektur 4 Agen Otonom (*Sense -> Think -> Act*)**:
   - **`NewsAgent`**: Memindai berita secara otomatis dari CoinDesk, CoinTelegraph (RSS), dan CryptoPanic, lalu menghitung skor sentimen *real-time* (VADER & batch FinBERT).
   - **`AnalysisAgent`**: Mensintesis indikator teknikal (`pandas-ta`: RSI, MACD, Bollinger Bands, EMA 9/21 cross, ATR, Volume SMA), bias fundamental/makroekonomi, dan sinyal *Machine Learning*.
   - **`DecisionAgent`**: Mengambil keputusan perdagangan (`OPEN_LONG`, `OPEN_SHORT`, `CLOSE`, `HOLD`) dengan penalaran transparan yang dicatat secara detail ke basis data.
   - **`ExecutionAgent`**: Mengeksekusi order simulasi, memantau *Stop-Loss*, *Take-Profit*, biaya transaksi maker/taker, serta *liquidation price* posisi aktif setiap 1 detik.

2. **Pemindaian Dinamis Top 10 Volume Futures**:
   - Secara otomatis memindai dan memeringkat 10 pasangan kripto futures teratas berdasarkan volume transaksi 24 jam global (misal BTC, ETH, SOL, XRP, UNI, BNB, NEAR, DOGE, SUI, LINK).
   - Mendukung pembaruan dinamis berkala dan konfigurasi statis/dinamis via `config.yaml`.
   - Menjamin seluruh 10 aset memiliki likuiditas tinggi dan data live terverifikasi.

3. **Penyesuaian Frekuensi Pembukaan Pasar AS (US Market Open)**:
   - Menggunakan `AgentScheduler` (APScheduler) untuk mendeteksi jam bursa AS (09:30 – 16:00 Eastern Time, Senin–Jumat).
   - Meningkatkan intensitas analisis sentimen dan pemindaian harga secara otomatis saat volatilitas pasar AS meningkat.

4. **Multi-Tier Network Fallback (Bebas Sensor ISP)**:
   - Tingkat 1: Binance Futures REST/WebSocket publik via `ccxt`.
   - **Tier 0: Hyperliquid** perp DEX via REST + WebSocket publik (tanpa API key). Sumber utama — orderbook L2 asli 20 level per sisi dan lilin real-time.
   - **Tier 1: Binance Futures** REST/WS publik via `ccxt.async_support` (timeout 4 detik agar fallback cepat aktif).
   - **Tier 2: yfinance** (`BTC-USD`, `ETH-USD`, dll.) — hanya untuk *tampilan*, tidak dipersistensikan ke tabel `candles` karena instrumennya berbeda satuan volume.
   - **Tier 3: CoinGecko Public API** sebagai fallback darurat terakhir.

5. **Arsitektur Hot-Path Hybrid (Python dengan C++/Rust Ready)**:
   - Seluruh parsing L2, kalkulasi *Order Flow Imbalance*, dan pelacakan *market depth* terisolasi di **`core/microstructure.py`** — satu-satunya tempat kalkulasi mikrostruktur terjadi.
   - Antarmuka abstrak `MicrostructureKernel` mendefinisikan kontrak (`ingest_l2`, `order_flow_imbalance`, `depth_imbalance`, `reset`). Implementasi default `PythonKernel` juga berfungsi sebagai *reference* untuk uji paritas.
   - **Mengganti ke C++/Rust adalah perubahan di satu tempat**, tanpa menyentuh satu pun baris di agent, dashboard, atau test:
     ```python
     from core.microstructure import register_kernel
     register_kernel(NativeKernelAdapter(my_native_ext))   # pybind11 / CFFI
     ```
   - `register_kernel()` memvalidasi kelengkapan antarmuka lebih dulu — kernel yang tidak lengkap ditolak saat boot, bukan diam-diam gagal saat feed pertama datang.
   - Parsing frame WebSocket dijalankan via `asyncio.to_thread`, sehingga event loop eksekusi 0.3 detik dan pembaruan harga tidak pernah terblokir oleh ratusan frame JSON per detik.

6. **Konkurensi Aman pada Frekuensi Tinggi**:
   - Saldo hanya boleh berubah lewat mutasi SQL atomik `balance = balance + ?`. Dua pembukaan yang jalan bersamaan tidak dapat saling menimpa saldo.
   - Penutupan posisi memakai klaim-tunggal `WHERE id = ? AND status = 'OPEN'`, jadi dua pemanggil yang berebut posisi yang sama tidak dapat dua-duanya mengembalikan margin.
   - `peak_balance` mengikuti **equity** (kas + margin + unrealized), bukan kas bebas — kalau mengikuti kas, batas drawdown bergeser tanpa mencerminkan nilai portofolio sebenarnya.
   - Operasi NumPy / pandas / pandas-ta dan query database sinkron dialokasikan ke `asyncio.to_thread` agar event loop tidak pernah terblokir (*zero-blocking hot path*).

7. **Model Matematika Manajemen Risiko Presisi**:
   - **Ukuran Posisi (*Fixed Fractional Sizing*)**:
     $$\text{Kuantitas} = \frac{\text{Saldo Akun} \times \text{Risk \%}}{|\text{Harga Entry} - \text{Harga Stop Loss}|}$$
   - **Harga Likuidasi Terisolasi (*Isolated Liquidation Price*)**:
     $$\text{Long Liq} = \text{Entry} \times \left(1 - \frac{1}{\text{Leverage}} + \text{MMR}\right)$$
     $$\text{Short Liq} = \text{Entry} \times \left(1 + \frac{1}{\text{Leverage}} - \text{MMR}\right)$$
   - **Perhitungan PnL & Fee Realistis**: Maker 0.015%, Taker 0.045% (roundtrip 0.09%). **Semua fill di produksi kena taker** — tidak ada jalur maker.
   - **Seluruh kalkulasi keuangan memakai `Decimal` dengan `ROUND_DOWN` eksplisit** — termasuk SL, TP, likuidasi, dan sizing. Pada scalp berjarak 0.40%, satu tick pembulatan ke arah yang salah bukan netral: ia memakan ruang gerak yang justru alasan kenapa strategi ini dipilih.
   - **Circuit breaker rugi harian** dibandingkan terhadap **modal awal**, bukan saldo kas. Memakai kas sebagai penyebut membuat ambang ikut turun begitu margin terkunci — pelonggaran terjadi justru di saat paling rugi.
   - ⚠️ **Kenyataannya: R:R riil 1:2, bukan 1:1.** Winner rata-rata +0.50, loser rata-rata −1.01 → butuh win rate 66.8%, aktual 30.0%. Lihat `docs/ARCHITECTURE.md` §6.

8. **Guard Kualitas Tick (Anti Spike-Fill)**:
   - Stop scalp hanya 0.40% dari harga, jadi fill di puncak lokal berarti stop-nya sudah berada di dalam spread. `PaperTradingEngine._tick_quality_guard` menolak fill yang:
     1. berumur lebih tua dari `scalping.max_tick_age_seconds`, atau
     2. menyimpang lebih dari `tight_sl_pct` dari median `stale_tick_min_samples` tick terakhir.
   - **Fail-open yang disengaja:** data historis yang belum cukup *mengizinkan* eksekusi. Menolak order karena tidak punya data sejarah akan membuat bot diam persis di detik-detik paling ramai.
   - Setiap penolakan dicatat ke `agent_logs` dengan alasannya, agar keputusan dapat direkonstruksi.

9. **Penyimpanan Lokal Berkecepatan Tinggi (SQLite WAL Mode)**:
   - Menggunakan `aiosqlite` dan mode `PRAGMA journal_mode=WAL` untuk mendukung penulisan asinkron tanpa mengunci pembacaan dashboard web.
   - Menyimpan **11 tabel**: `candles`, `positions`, `trades`, `signals`, `news`, `agent_logs`, `account`, `balance_history`, `direction_snapshots`, dan `macro_data`.
   - **Pemangkasan snapshot otomatis.** Tabel `direction_snapshots` ditulis tiap 5 detik × 10 simbol = 172.800 baris/hari. Job `direction_snapshot_prune` berjalan tiap `database.snapshot_prune_interval` detik, menyisakan `snapshot_keep_per_symbol` terbaru per simbol.
   - **Validasi invarian OHLC berlapis ganda** (di `PriceFeed` dan `Repository`): bar dengan harga/volume ≤ 0, `h<l`, `h<o`, `h<cl`, `l>o`, atau `l>cl` ditolak. Sekali bar rusak tersimpan, chart langsung rusak karena skala sumbu-y bergeser.

10. **Dashboard Visual Real-Time (Retro Bloomberg / CRT Terminal Command Center HUD)**:
   - Dapat diakses langsung melalui peramban web di `http://127.0.0.1:8050`.
   - Mengusung tema visual *Vintage Parchment Bloomberg Terminal* (`#dfd5b8`, border 2px hitam pekat, tipografi monospace retro, badge piksel neon).
   - **Dua interval terpisah**: 500 ms untuk panel yang berubah cepat (harga, orderbook, posisi, scanner), 60 detik untuk query berat (trade log stream, marquee, footer). Memisahkannya mencegah JOIN berat dijalankan 2× per detik.
   - Susunan 12 kolom, diurutkan mengikuti urutan pertanyaan trader:
     1. **Z1 · Account rail**: giant PnL, saldo, equity, win rate, skala risiko drawdown.
     2. **Z2 · Price & risk**: mini candlestick TradingView-style (drag untuk pan, tanpa reset zoom) + ladder orderbook L2 5 level per sisi + tabel posisi aktif + *Probability Scanner* (P(LONG)/P(SHORT) dari snapshot ensemble).
     3. **Z3 · Pipeline**: rail PnL & win rate per simbol, diurutkan dari yang paling untung.
     4. **Z4 · Analytics**: kurva equity (area `tonexty`), live trade log stream, dual sparkline (PnL velocity & volume pulse) + tabel KPI, dan konstelasi *Snipe Neural Net*.
     5. **Footer ticker**: status mesin inti `0XF3CE25 CORE ENGINE v2.4`.
   - **Doktrin visual yang dijaga ketat**: data yang absen selalu ditampilkan sebagai *empty state* eksplisit, tidak pernah sebagai angka hasil karangan. Orderbook belum ada → `"AWAITING L2 ORDERBOOK SNAPSHOT // HYPERLIQUID WS"`; slippage tanpa L2 → `"N/A"`, bukan `0.000%`; MDD/Sharpe yang tidak pernah ditulis → `"N/A"`, bukan `"0.00"`.

---

## Struktur Direktori Proyek

```text
trading-bot/
├── core/                       # Infrastruktur & hot-path
│   ├── config.py               # 12 dataclass config + 2 validator ekonomi
│   ├── microstructure.py       # HOT-PATH KERNEL: L2 · OFI · depth (C++/Rust ready)
│   ├── logger.py               # Formatter ANSI, progress bar, quiet mode
│   ├── event_bus.py            # Publish/subscribe antar agen + Channels
│   ├── market_store.py         # Store in-memory harga/tape/orderbook
│   ├── scheduler.py            # APScheduler + deteksi jam pasar AS
│   └── utils.py                # Parse timestamp UTC dari SQLite
├── database/                   # Persistensi
│   ├── db.py                   # Skema 11 tabel + WAL mode
│   ├── models.py               # Dataclass entity
│   └── repository.py           # Semua query (atomik & klaim-tunggal)
├── data/                       # Akuisisi data
│   ├── hyperliquid_feed.py     # Tier 0: WebSocket L2 20 level + REST
│   ├── price_feed.py           # Multi-tier fallback OHLCV/ticker
│   ├── news_fetcher.py         # RSS CoinDesk/CoinTelegraph + CryptoPanic
│   ├── macro_fetcher.py        # FRED + Forex Factory
│   └── sentiment.py            # VADER (real-time) + FinBERT (batch)
├── trading/                    # Mesin uang
│   ├── models.py               # Enum + dataclass order/posisi
│   ├── risk_manager.py         # Semua rumus Decimal + validasi risiko
│   ├── position_manager.py     # Lifecycle posisi, SL/TP/liq
│   └── paper_engine.py         # Orkestrasi order + guard tick
├── analysis/                   # Matematika sinyal (murni, tanpa I/O)
│   ├── direction_ensemble.py   # Log-odds pooling + shrinkage + agreement
│   ├── probability_engine.py   # Phi(x), OFI facade, kurva difusi, edge
│   ├── technical.py            # Indikator teknikal (pandas-ta)
│   ├── fundamental.py          # Makro + sentimen → bias
│   └── ml_signals.py           # RandomForest + fallback rule-based
├── ml/                         # Pipeline Machine Learning
│   ├── trainer.py              # Pelatihan model offline
│   ├── predictor.py            # Inferensi model
│   └── models/                 # signal_model.pkl
├── agents/                     # 4 agen otonom + ensemble arah
│   ├── base_agent.py           # ABC: siklus sense → think → act
│   ├── news_agent.py           # Pemindai berita & sentimen
│   ├── analysis_agent.py       # Teknikal + fundamental + ML
│   ├── decision_agent.py       # Gate entry/exit dari snapshot ensemble
│   ├── execution_agent.py      # Order + TP/SL/breakeven/expiry
│   └── direction_agents.py     # 4 spesialis + DirectionEnsembleAgent
├── dashboard/                  # Antarmuka web Dash/Plotly
│   ├── app.py                  # Server Dash (2 interval: 500 ms + 60 s)
│   ├── layouts/hud.py          # Grid HUD 12 kolom
│   ├── layouts/hud_figures.py  # 5 pembuat figure Plotly
│   ├── callbacks/              # 19 callback real-time (18 di 2 Hz)
│   └── assets/style.css        # 59 token CSS (satu sumber tinggi panel)
├── tests/                      # 29 file test (605 test)
│   ├── test_bugfixes.py        # Regresi B1..B9 + kontrak arsitektur baru
│   ├── test_risk_manager.py    # Matematika likuidasi & ukuran posisi
│   ├── test_paper_engine.py    # Alur buka/tutup posisi
│   ├── test_position_manager.py# Pemicu otomatis SL/TP/Likuidasi
│   ├── test_direction_ensemble.py # Invarian agregator arah
│   ├── test_layout_contract.py # Kontrak ID DOM & token CSS
│   └── …                       # 23 file lainnya
├── research/                   # 44 skrip riset + FINDINGS.md (tidak di-import produksi)
├── data_store/                 # DB, logs, dan tooling dev (bukan produksi)
├── config.yaml                 # Konfigurasi parameter trading
├── run.py                      # Entry point (1113 baris)
├── requirements.txt            # Dependensi Python
├── .gitignore                  # Pengecualian artefak runtime
└── docs/ARCHITECTURE.md        # Dokumentasi lengkap (18 bagian, 53 defect)
```

---

## Panduan Instalasi & Penggunaan

### 1. Prasyarat Lingkungan
- Python 3.10 atau lebih baru (kompatibel penuh dengan Python 3.10 - 3.14).
- Akses internet untuk mengunduh data pasar publik.

### 2. Pemasangan Dependensi
Pasang seluruh paket yang dibutuhkan melalui terminal:
```bash
pip install -r requirements.txt
```

### 3. Melatih Model Machine Learning (Opsional / Terpadu)
Model RandomForest awal telah dilatih menggunakan 8.000+ data bar lilin historis dan disimpan di `ml/models/signal_model.pkl`. Jika ingin melatih ulang dengan data terbaru:
```bash
python -m ml.trainer
```

### 4. Menjalankan Seluruh Unit Test
Untuk memverifikasi formula matematika, logika trading, kontrak layout, dan regresi bug:
```bash
python -m unittest discover tests
```
*29 file test, 605 test. Termasuk `test_bugfixes.py` yang mengunci setiap perbaikan B1..B9 — kalau salah satu gagal, itu berarti bug-nya kembali.*

⚠️ **Catatan jujur tentang cakupan test.** 605 test hijau **tidak berarti
jalur live aman**. Tidak ada kode produksi yang meng-set `TRADEBOT_LIVE`,
jadi gerbang live menolak semua order dan belum pernah ada order nyata
yang terkirim. Selain itu: 12 test membaca *teks source* alih-alih
menguji perilaku, 2 test secara matematis tidak bisa gagal, dan
`test_close_short_is_sell` **tidak punya assertion sama sekali** — tepat
di jalur yang menentukan arah penutupan posisi short. Detail di
`docs/ARCHITECTURE.md` §16.

### 5. Mode Maintenance (Opsional)
Perbaiki data lilin yang rusak tanpa menjalankan bot:
```bash
python run.py --repair-candles
```
Menghapus bar `candles` yang melanggar invarian OHLC, lalu menarik ulang 600 bar 1m dan 5m untuk **seluruh** simbol yang punya bar di tabel — bukan hanya Top-10 saat ini, karena simbol yang tergeser keluar peringkat volume akan meninggalkan bar rusak yang muncul lagi begitu dipilih di chart.

⚠️ Perintah ini menutup koneksi database dan price feed. **Jangan** dijalankan pada instance yang sedang aktif.

### 6. Reset Database Paper Trading
```bash
python reset_paper_db.py
```
Menghapus riwayat trade dan mengembalikan saldo ke 10.000 USDT.

### 7. Menjalankan Bot Trading & Dashboard
Jalankan aplikasi utama:
```bash
python run.py
```

Setelah perintah dijalankan, sistem akan otomatis:
1. Menghubungkan basis data lokal SQLite dengan mode WAL (11 tabel).
2. Membuka saldo virtual awal (10.000 USDT) dan menyuntikkannya ke `RiskManager` sebagai penyebut circuit breaker rugi harian.
3. Memindai top-10 futures berdasarkan volume 24 jam dan **menimpa `config.symbols`**. Perhatikan: simbol yang benar-benar dipantau sering berbeda dari yang tertulis di `config.yaml` — `ZEC`, `HYPE`, atau `ENA` bisa masuk, sementara `ADA`/`AVAX`/`LINK` keluar.
4. Menyambung WebSocket Hyperliquid untuk seluruh simbol aktif.
5. Menjalankan 4 agen otonom + `DirectionEnsembleAgent` secara asinkron.
6. Menjalankan 5 background task: price feed, execution loop (0.3 dtk), candle refresh (30 dtk), telemetry, dan loop pemangkasan database.
7. Meluncurkan server dashboard visual di:
   **[http://127.0.0.1:8050](http://127.0.0.1:8050)**

Buka URL tersebut di peramban web untuk memantau grafik harga, orderbook L2, posisi aktif, probabilitas arah, dan riwayat performa secara langsung.

---

## Konfigurasi Kustom (`config.yaml`)

Parameter perdagangan dapat disesuaikan tanpa mengubah kode program melalui file `config.yaml`:

**Akun & Simbol**
- `account.initial_balance`: Saldo awal akun simulasi (default: `10000.0` USDT).
- `symbols`: Daftar pasangan futures default. **Diperingati:** kalau `scanning.dynamic_top_volume: true`, daftar ini akan **ditimpa** saat boot oleh hasil pemindaian volume. Untuk melihat simbol yang benar-benar dipantau, baca baris `Active Top Symbols` di log startup.
- `scanning.dynamic_top_volume` / `top_n` / `refresh_interval`: pemindaian dinamis top volume.

**Risiko**
- `risk.max_risk_per_trade`: Maksimum risiko saldo per perdagangan (default `0.005` atau 0.5%).
- `risk.max_leverage` / `default_leverage`: Batas dan leverage bawaan (default `50` / `10`).
- `risk.max_daily_loss` / `max_drawdown` / `max_open_positions`: circuit breaker rugi harian, batas drawdown, dan jumlah posisi simultan.

**Scalping** (lihat `config.yaml` untuk komentar lengkap per parameter)
- `min_profit_pct` (0.60%) dan `tight_sl_pct` (0.40%) — target dan stop. Setelah dipotong fee roundtrip 0.09%: `net_tp = 0.51%` vs `net_sl = 0.49%` → rasio 1:1.04, **win rate impas 49.0%**.
  ⚠️ Hanya **2.0 bps** marginnya, dan hasil riil jauh lebih buruk: winner
  rata-rata +0.50, loser rata-rata −1.01 → R:R 1:2, butuh **66.8%** win
  rate, aktual 30.0% (`docs/ARCHITECTURE.md` §6).
- `min_confidence` (0.40) — confidence minimum untuk **membuka** posisi.
- `reversal_close_threshold` (0.70) — confidence minimum untuk **membalikkan** posisi aktif. ⚠️ Karena plafon confidence ensemble cuma 0.7866, ambang ini sebenarnya berarti **satu agen dengan confidence ≥ 0.82 sudah cukup untuk membalikkan posisi** (§10.2).
- `max_tick_age_seconds` / `stale_tick_window_seconds` / `stale_tick_min_samples` — parameter guard kualitas tick.
- `min_hold_seconds` (15) / `max_hold_seconds` (900) — batas umur posisi.
- `cooldown_after_close_seconds` / `cooldown_after_loss_seconds` — cooldown adaptif per simbol (dikali streak loss).

**Ensemble arah**
- `ensemble.interval_seconds` (5) / `shrinkage_delta` (0.85) / `agreement_bonus` (0.5) / `min_prob` (0.02).
- `ensemble.max_snapshot_age_seconds` (20) — snapshot lebih tua dari ini **ditolak** DecisionAgent. **Divalidasi ≥ 2× `interval_seconds`** saat boot.
- `ensemble.agents.*.weight` — bobot tiap agen spesialis (orderflow 0.30, momentum 0.25, technical 0.25, microstructure 0.20).

**Database**
- `database.path`: Lokasi file SQLite (default `data_store/trading_bot.db`).
- `database.snapshot_prune_interval` (3600) / `snapshot_keep_per_symbol` (120): parameter pemangkasan `direction_snapshots`.

**Agen**
- `agent_intervals`: Frekuensi siklus tiap agen, untuk jam normal vs. jam pembukaan pasar AS.

### Validator Ekonomi Saat Boot
`core/config.py` menolak konfigurasi yang **secara struktural mustahil untung** — bukan menunggu ruginya terlihat. Contohnya, konfigurasi yang ditolak:
- `breakeven_trigger_pct ≥ min_profit_pct` → proteksi breakeven tidak akan pernah aktif (TP menutup duluan).
- `net_tp < net_sl` setelah fee → butuh win rate yang tidak bisa dicapai sinyal mana pun.
- `reversal_close_threshold < min_confidence` atau `≥ 1.0` → gate reversal mati atau membalikkan posisi lemah.
- `stale_tick_window_seconds ≤ max_tick_age_seconds` → median dihitung dari sampel yang sudah ditolak basi.
- `max_snapshot_age_seconds < 2 × interval_seconds` → ada celah di mana tidak ada snapshot cukup segar.

---

## Keamanan & Independensi
- **100% Simulasi Lokal**: Tidak ada transmisi kredensial rahasia bursa atau transfer dana riil.
- **Tahan Gangguan Jaringan**: Sistem secara mandiri beralih antar-sumber data publik (Hyperliquid → Binance → yfinance → CoinGecko) jika salah satu endpoint mengalami gangguan.
- **Kredensial opsional**: Token CryptoPanic dan API key FRED bersifat opsional; tanpa keduanya sistem tetap berjalan penuh, hanya kehilangan dua sumber data tambahan.
