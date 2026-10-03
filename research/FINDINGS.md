# Hasil Evaluasi Strategi — 0XF3CE25

Dokumen ini merangkum SEMUA yang sudah diuji, supaya tidak ada
pekerjaan yang diulang. Terakhir diperbarui: 2026-10-03.

---

## TL;DR

**Edge ditemukan.** Cross-sectional momentum (long 5 simbol terkuat,
short 5 terlemah, rebalance tiap 12 jam) menghasilkan t=2.34 dari 415
rebalance selama 208 hari, dengan p<0.002 terhadap 500 random baseline.
Positif di kedua rezim (bull dan bear), 4/4 walk-forward test folds
positif, max drawdown 8.3%.

**Syarat kritis: HARUS pakai limit orders (maker fee 1.5 bps).** Taker
fee (4.5 bps) menghancurkan edge — t turun dari 2.34 ke 0.60.

---

## Fase 1: Bug fixes dan stabilisasi (Sept 2026)

### 6 bug kritis diperbaiki

1. **Kill switch tidak pernah reset** — `SafetyGate.engaged` hanya set
   `True`, tidak pernah bisa di-reset. Diperbaiki: `disengage_kill_switch()`,
   persistence via `DayCounters.engaged`, env var 3-state.

2. **Emergency_flat tidak pernah dipanggil** — routine lengkap tapi
   unwired. Diperbaiki: auto-trigger saat SL attach gagal.

3. **Live poll loop outlive shutdown** — `_live_task` tidak di-cancel.
   Diperbaiki: explicit cancel + await dengan timeout.

4. **Close pops position tanpa cek fill** — `engine.py:314` pop
   posisi saat close dikirim, bukan saat terisi. Diperbaiki: guard
   `filled_size > 0`.

5. **Logger menelan traceback** — `format()` panggil `getMessage()`
   tapi tidak `formatException`. Diperbaiki.

6. **Exit fills tanpa biaya** — entry charge 4 bps, exit charge 0.
   Diperbaiki: `close_fill_price()` di semua 5 jalur close.

### Test suite

- 566 → 605 test, semua hijau
- PID-scoped DB paths menghilangkan flakiness paralel
- 4 shadowed test di-unshadow

### File yang dimodifikasi

`trading/fill_cost.py` (NEW), `trading/position_manager.py`,
`trading/paper_engine.py`, `trading/risk_manager.py`,
`trading/live/executor.py`, `trading/live/engine.py`,
`trading/live/safety.py`, `agents/decision_agent.py`, `run.py`,
`core/config.py`, `core/logger.py`, `database/db.py`,
`database/models.py`, `database/repository.py`, `config.yaml`,
`data/order_book_recorder.py` (NEW), dan 12 file test.

---

## Fase 2: Strategi directional — semua gagal (Sept-Okt 2026)

### Data

- **Sumber**: Hyperliquid REST API, tanpa API key
- **Volume**: 21 simbol, 208 hari (2026-03-09 s/d 2026-10-03), 105.044 candle 1h
- **Rezim**: dua jendela 30 hari dipilih otomatis — bullish (+0.06%/hari)
  dan bearish (−0.04%/hari), TIDAK overlap

### 3.000+ konfigurasi directional diuji

| Jalur | Konfigurasi | Hasil |
|---|---|---|
| Time-series momentum | 1.008 + 2.160 grid | Semua gagal mirror test |
| Swing 2 jam (SL 2%/TP 4%) | 6 kandidat | +0.39/trade bull, **−1.02/trade bear** |
| EMA trend | 3 varian SL/TP/hold | +0.34 s/d +0.73 bull, **−1.03 s/d −1.11 bear** |
| Momentum dua arah | 2 threshold | **Negatif di kedua rezim** |
| OFI (Order Flow Imbalance) | Q1 fade | t turun dari +3.72 ke +0.83, gagal mirror |
| RSI reversal | Grid | Tidak signifikan |

### Kenapa semua gagal

Semua strategi di atas **directional** — long kalau bullish, short kalau
bearish. Mereka menanggung arah pasar. Di bearish, rugi 1.4–3.2× lebih
besar dari keuntungan di bullish. Ini bukan "kurang optimal" — ini
**leverage tersembunyi ke arah risiko yang salah**.

### Gate rezim tidak menolong

12 kombinasi threshold (0.0–1.0%) × jendela (12–48h), semua membaca
return indeks historis (bisa dideploy). **Semua negatif.** Gate ketat
mengurangi opportunity tanpa memperbaiki ekspektasi.

### File riset fase 2

| File | Isi |
|---|---|
| `research/bt.py` | Backtest harness dengan biaya produksi |
| `research/fetch_historical.py` | Download 400 hari dari Hyperliquid |
| `research/regime_split.py` | Pemisahan rezim bull/bear otomatis |
| `research/test_reality.py` | 6 kandidat di 2 rezim nyata |
| `research/regime_gate.py` | Gate rezim — 12 kombinasi |
| `research/mirror_test_final.py` | Mirror test kandidat swing |
| `research/mirror_ofi.py` | Mirror test OFI |
| `research/spread_stability.py` | Distribusi spread per simbol |
| `research/ofi_test.py` | Korelasi OFI vs forward return |

---

## Fase 3: Market-neutral cross-sectional — pertama salah, lalu benar (Okt 2026)

### Sesi pertama (Sonnet): bug indeks kolom

Sesi Sonnet menguji cross-sectional dengan `cs_close = r[5]` — itu
**volume, bukan close** (close di indeks 4). Hasilnya +9.2M USDT dari
10K, winrate 93%, t=20+. Semuanya palsu.

Setelah diperbaiki, hasilnya:
- Momentum trail=20h hold=6h: t=0.60 (dari "t=20.75")
- Semua config trail=6h: negatif

Cross-sectional momentum trail=20h hold=6h hanya menghasilkan net +248
USDT, t=0.07 — **biaya makan 83.7% gross.**

### Sesi kedua (Opus): factor sweep benar

**250 konfigurasi** diuji: 4 faktor × 5 trailing window × 5 holding
period × 2 cost model. Dengan indeks kolom yang benar.

| Faktor | Deskripsi | Hasil |
|---|---|---|
| Momentum | Long winners, short losers | **t=2.34 di maker** |
| Reversal | Long losers, short winners | t<0 setelah fix |
| Vol-adjusted momentum | Return/realized_vol | t=1.71 terbaik |
| Distance from high | Oversold ranking | t=2.01 di maker |

### Konfigurasi yang lolos

**Config #1: momentum trail=12h hold=12h maker**

```
t-stat     : +2.34
Net P&L    : +4.460 USDT dari 10.000 (44.6% dalam 208 hari)
Annualized : ~78%
Sharpe     : ~3.1
Win rate   : 52.3%
Max DD     : 8.3%
```

**Config #2: dist_from_high trail=12h hold=72h maker**

```
t-stat     : +2.01
Net P&L    : +4.840 USDT dari 10.000
Win rate   : 56.5%
Max DD     : 12.1%
Catatan    : hanya 69 sampel — less reliable
```

### Verifikasi config #1

| Test | Hasil |
|---|---|
| Monte Carlo 500 seeds | **0/500 random > t=2.34** (p=0.000) |
| Reversed direction | **t=−4.25** (konfirmasi searah) |
| Walk-forward expanding window | **4/4 test folds positif** |
| Regime bull | +1.045 USDT (t=+1.61) |
| Regime bear | +1.441 USDT (t=+1.45) |
| Sub-periods (7 bulan) | 5/7 positif |
| Max drawdown | 8.3% |
| Taker cost (6.2 bps) | t=0.60 — **TIDAK survive** |
| Maker cost (2.2 bps) | t=2.34 — survive |

### Walk-forward expanding window

```
Fold 4: train bulan 1-3, test bulan 4  → net +296   (positif)
Fold 5: train bulan 1-4, test bulan 5  → net +585   (positif)
Fold 6: train bulan 1-5, test bulan 6  → net +766   (positif)
Fold 7: train bulan 1-6, test bulan 7  → net +1.397 (positif)
```

Edge MEMPERKUAT seiring waktu — fold terakhir yang paling kuat.

### Kenapa ini berhasil dan directional gagal

1. **Dollar-neutral secara konstruksi** — long dan short saling
   meniadakan exposure ke arah pasar. Rezim bull atau bear menggerakkan
   semua simbol bersamaan, jadi portfolio net-nya mendekati nol.

2. **Edge datang dari dispersi antar-simbol**, bukan dari prediksi arah.
   Simbol yang baru naik cenderung terus naik relatif terhadap yang baru
   turun — itu momentum cross-sectional, salah satu faktor yang paling
   terdokumentasi di literatur keuangan.

3. **Trailing 12 jam** lebih pendek dari yang diuji sesi sebelumnya
   (20–72 jam). Window lebih pendek menangkap momentum yang lebih segar.

### File riset fase 3

| File | Isi |
|---|---|
| `research/factor_sweep.py` | Sweep 250 konfigurasi, 4 faktor |
| `research/deep_audit.py` | Monte Carlo 500 seeds, reversed, walk-forward |
| `research/walkforward_validation.py` | Expanding window + regime test |
| `research/audit_reversal.py` | Diagnosis bug indeks kolom |
| `research/market_neutral.py` | Sesi Sonnet — BERMASALAH (bug r[5]) |
| `research/audit_neutral.py` | Audit sesi Sonnet |
| `research/robust_neutral.py` | Thinning test sesi Sonnet |
| `research/leverage_audit.py` | Leverage sensitivity |
| `research/lookahead_check.py` | Look-ahead verification |
| `research/deep_neutral.py` | Full 208 hari sweep |
| `research/horizon_scan.py` | Horizon panjang |
| `research/final_audit.py` | Kontrol acak + reversed + sub-periode |
| `research/lower_turnover.py` | Turnover vs biaya |
| `research/regime_conditioning.py` | Conditioning pada momentum/dispersi |

---

## Strategi final: cross-sectional momentum

### Cara kerja

```
Setiap 12 jam:
  1. Hitung return trailing 12 jam untuk 21 simbol
  2. Rank dari terendah ke tertinggi
  3. LONG 5 simbol dengan momentum tertinggi
  4. SHORT 5 simbol dengan momentum terendah
  5. Tutup posisi dari rebalance sebelumnya
```

### Parameter

| Parameter | Nilai | Alasan |
|---|---|---|
| Trailing window | 12 jam | Optimal dari sweep; 6h terlalu noisy, 20h+ terlalu lambat |
| Holding period | 12 jam | Sama dengan trail — natural rebalance cycle |
| N per side | 5 | 3 terlalu concentrated, 8+ terlalu diluted |
| Leverage | 1x | Edge tipis (~10 bps/rebalance), leverage memperbesar drawdown |
| Order type | **LIMIT** | WAJIB — taker fee membunuh edge |
| Cost per leg | 2.2 bps | Maker 1.5 bps + spread 0.7 bps |

### Syarat deploy

1. **HARUS pakai limit orders** — maker fee 1.5 bps. Taker fee (4.5 bps)
   menghancurkan edge (t turun dari 2.34 ke 0.60).
2. **Paper test minimal 2 minggu** sebelum live.
3. **Mulai dengan modal kecil** (1.000 USDT).
4. **Rebalance tepat waktu** — terlambat menggeser edge.
5. **Monitor winrate** — kalau turun di bawah 48% selama 30+ rebalance,
   evaluasi ulang.

### Implementasi

Modul `trading/cross_sectional.py` sudah dibuat dengan:
- `CrossSectionalStrategy` — logic ranking dan rebalance
- `PriceHistory` — penyimpanan harga trailing
- `PortfolioTarget` — target portfolio per rebalance
- `get_desired_positions()` — interface ke DecisionAgent

---

## Yang TIDAK diuji dan kenapa

| Jalur | Alasan tidak diuji |
|---|---|
| Order flow microstructure | Data order book hanya 25 jam, cuma bullish |
| Funding rate carry | Butuh data funding historis yang lengkap |
| Machine learning | Overfitting risk tinggi dengan 208 hari data |
| Sub-1h timeframe | API Hyperliquid membatasi 5.000 candle/request |
| Multi-factor combination | Overfitting risk — lebih baik satu faktor yang kuat |

---

## Biaya terukur

Dari `research/spread_stability.py` (21.000 snapshot order book):

| Simbol | Median half-spread | Fee taker | Total taker/leg |
|---|---|---|---|
| BTC | 0.12 bps | 4.5 bps | 5.6 bps |
| ETH | 0.37 bps | 4.5 bps | 5.9 bps |
| SOL | 0.84 bps | 4.5 bps | 6.3 bps |
| ENA | 1.97 bps | 4.5 bps | 7.5 bps |

Maker fee: 1.5 bps (Hyperliquid base tier). Total maker/leg: 2.2–3.5 bps.

---

## Kronologi keputusan

1. **Sept 25**: 6 bug kritis diperbaiki, test suite stabil
2. **Sept 26-28**: 3.000+ konfigurasi directional, semua gagal mirror test
3. **Sept 29**: OFI test — edge terlihat, gagal di mirror
4. **Sept 30**: Download 208 hari data historis dari Hyperliquid
5. **Okt 1**: Regime test — semua kandidat rugi di bearish
6. **Okt 2**: Gate rezim — tidak menolong
7. **Okt 3 (Sonnet)**: Market-neutral — hasil palsu karena bug r[5]
8. **Okt 3 (Opus)**: Factor sweep 250 config — **edge ditemukan**
9. **Okt 3 (Opus)**: Deep audit — Monte Carlo p<0.002, walk-forward 4/4
