# Hasil Evaluasi Strategi

Ringkasan riset Sept–Okt 2026. Dokumentasi arsitektur ada di
[`../docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md) §15. File ini hanya
catatan hasil.

**Status: satu edge ditemukan dan belum dideploy.** Modulnya ada di
`trading/cross_sectional.py` tapi **tidak terhubung ke sistem mana pun**
(§17.1 di dokumen utama).

---

## Data

Sumber: Hyperliquid REST `candleSnapshot`, tanpa API key.
**208 hari, 21 simbol, 105.044 candle 1h** — 2026-03-09 s/d 2026-10-03.

Tersimpan di `data_store/historical_candles.db`, tabel `hist_candles`.
Hanya interval `1h` yang punya cakupan 21 simbol; 15m/5m/1m cuma 4 simbol.

> **Jebakan API.** `candleSnapshot` **memotong hasil di ~5.000 candle tanpa
> memberi tahu**. Request 5 hari untuk 1m meminta 7.200 dan hanya ~5.000
> yang kembali — tanpa error, tanpa penanda truncation. Gejalanya: DB terisi,
> nol error, tapi cuma 4 hari history untuk 1m padahal minta 400.
> Chunk harus dihitung dari interval: `chunk_ms = 4200 × INTERVAL_MS[interval]`
> (1m = 2.9 hari/request, 1h = 175 hari).

---

## Fase 1 — Semua strategi directional GAGAL

| Jalur | Skala | Hasil |
|---|---|---|
| Time-series momentum | 1.008 + 2.160 kandidat | semua gagal |
| Swing horizon panjang | 450 kandidat | menang di train, mati di validasi |
| **Swing 2 j, SL 2% / TP 4%** | 6 kandidat | **+0.39/trade bull, −1.02/trade bear** |
| **EMA trend, 3 varian** | — | **+0.34…+0.73 bull, −1.03…−1.11 bear** |
| Momentum dua arah | 2 ambang | negatif di kedua rezim |
| **OFI Q1 fade** | 24 jam data | t +3.72 → **gagal mirror test** |
| RSI reversal | grid | tidak signifikan |
| Regime gate | 12 kombinasi | semua negatif |
| Regime trend filter | 4 mode × 3 fold | drift tidak bisa dieksploitasi |

**Penyebabnya konsisten:** rugi di bearish **1.4–3.2× lebih besar** dari
keuntungan di bullish. `FINDINGS.md` versi lama menyebutnya *"leverage
tersembunyi ke arah risiko yang salah"*. Gate rezim ketat mengurangi
opportunity tanpa memperbaiki ekspektasi.

Empat aturan anti-look-ahead yang dipakai seluruh harness:
1. Isi di bar berikutnya, bukan bar yang menghasilkan sinyal
2. Rank dari trailing, tahan ke depan
3. Gate rezim hanya baca return indeks historis
4. **SL diperiksa sebelum TP** — urutan high/low intrabar tak diketahui

---

## Fase 2 — Cross-sectional momentum: EDGE TERVERIFIKASI

Dari `factor_sweep.py` — 250 konfigurasi, 4 faktor × 5 trailing × 5 holding
× 2 cost model, indeks kolom sudah dikoreksi.

```
faktor    : cross-sectional momentum, trailing 12j, hold 12j
posisi   : long 5 terkuat + short 5 terlemah, simultan, dollar-neutral
n         : 415 rebalances selama 208 hari
t-stat    : +2.34          p < 0.002 (0 dari 500 baseline acak)
Net P&L   : +4.460 USDT dari 10.000  (44.6% dalam 208 hari)
Sharpe    : ~3.1          Win rate : 52.3%      Max DD : 8.3%
Biaya     : maker 2.2 bps/leg      Leverage : 1x
Reversed  : t = −4.25 (konfirmasi arah)      OOS 2nd half : t = 2.11
```

Walk-forward expanding window — **4/4 fold positif, dan edge menguat**:
```
Fold 4 → +296    Fold 5 → +585    Fold 6 → +766    Fold 7 → +1.397
```

Rezim: bull +1.045 (t 1.61) · bear +1.441 (t 1.45). Sub-periode 5/7 bulan positif.

Config #2 (dist-from-high, trail 12j / hold 72j): t 2.01, net +4.840, WR 56.5%,
max DD 12.1% — tapi hanya 69 sampel, "less reliable".

### ⛔ Syarat menentukan: HARUS maker

| Biaya/leg | t-stat |
|---|---|
| maker 2.2 bps | **2.34 — bertahan** |
| taker 6.2 bps | **0.60 — mati** |

Contohnya di `trading/cross_sectional.py`, tapi sistem produksi **tidak punya
jalur maker sama sekali** — semua fill kena taker, dan `SPREAD_FLOOR`
(0.7 bps, satu-satunya konstanta yang memodelkan fill maker) **nol
pemanggil**. Hooking modul itu apa adanya ke `PaperTradingEngine` akan
menagih 9 bps fee + ≥2.4 bps slippage terhadap edge ~0.18/trade.

---

## Kesalahan yang hampir lolos

Sesi pertama memakai `cs_close = r[5]` — itu **volume, bukan close**
(close di indeks 4). Ranking berdasarkan volume memberi:

```
+9.2M USDT dari modal 10.000    win rate 93%    t = 20+
```

Semuanya palsu. Setelah dikoreksi, config yang sama memberi **t = 0.60**,
dan semua `trail=6h` negatif. `lower_turnover.py` mengukur kerabatnya:
gross +1519 dari 205 rebalances sebelum biaya, +248 sesudah — **biaya
memakan 83.7% dari gross**.

---

## Biaya terukur

Dari 105.040 snapshot order book nyata (`data_store/order_book.db`):

| Simbol | Median half-spread | Fee taker | Total taker/leg |
|---|---|---|---|
| BTC / HYPE | 0.12 bps | 4.5 bps | 5.6 bps |
| ETH | 0.37 | 4.5 | 5.9 |
| XRP | 0.66 | 4.5 | 6.2 |
| ZEC | 0.70 | 4.5 | 6.4 |
| SOL | 0.84 | 4.5 | 6.3 |
| NEAR | 1.13 | 4.5 | 6.3 |
| LIT | 1.28 | 4.5 | 6.6 |
| PUMP | 1.75 | 4.5 | 7.2 |
| ENA | 1.97 | 4.5 | 7.5 |

Rentang **16×** antar simbol. Spread juga berkorelasi dengan volatilitas
(1.72 bps median untuk simbol bergejolak vs 0.37 untuk yang tenang, 4.64×) —
jadi asumsi 3 bps konstan terlalu optimis **pada saat spread paling mahal**,
yaitu saat kerugian paling besar.

Angka-angka ini sudah jadi `FILL_HALF_SPREAD_FLOOR_BY_SYMBOL` di
`trading/fill_cost.py:78-89`.

---

## Yang tidak diuji

| Jalur | Alasan |
|---|---|
| Mikrostruktur order flow | Data order book cuma ~25 jam, dan hanya bullish |
| Funding rate carry | Butuh data funding historis lengkap |
| Machine learning | Risiko overfitting tinggi dengan 208 hari |
| Sub-1 jam | Plafon 5.000 candle/request |
| Multi-faktor | Overfitting; lebih baik satu faktor kuat |

---

## Syarat deploy (kalau nanti diimplementasikan)

1. **Limit orders WAJIB** — taker membunuh edge
2. Minimal 12 simbol dengan 12 jam history (kode butuh `N_SIDE*2+2 = 12`;
   `config.symbols` sekarang hanya 10)
3. Paper test ≥ 2 minggu sebelum live
4. Mulai kecil (1.000 USDT)
5. Rebalance tepat waktu — telat menggeser edge
6. Pantau win rate; di bawah 48% selama 30+ rebalance, evaluasi ulang

---

## Kronologi

1. **25 Sep** — 6 bug kritis live-diperbaiki, 566 → 605 test
2. **26–28 Sep** — 3.000+ konfigurasi directional, semua gagal mirror test
3. **29 Sep** — OFI: edge terlihat, gagal mirror (drift, bukan alpha)
4. **30 Sep** — Download 208 hari data historis
5. **1 Okt** — Regime test: semua kandidat rugi di bearish
6. **2 Okt** — Gate rezim: tidak menolong
7. **3 Okt** — Market-neutral: hasil palsu (`r[5]` = volume)
8. **3 Okt** — Factor sweep 250 config: **edge ditemukan**
9. **3 Okt** — Deep audit: Monte Carlo p<0.002, walk-forward 4/4

Catatan: 11 dari 44 skrip riset **tidak tracked di git**, dan punya
kontaminasi loader `r[5]` di atas. Angka di sini berasal dari
`factor_sweep.py` yang sudah dikoreksi — bukan dari skrip contaminated.
