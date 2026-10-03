# Hasil Evaluasi Strategi — 2026-10-03

Dokumen ini merangkum apa yang sudah diuji dan apa hasilnya. Tujuannya
agar keputusan berikutnya tidak mengulang pekerjaan yang sudah selesai.

## Ringkas

**Tidak ada edge yang bertahan di dua rezim.** Bot ini belum layak
dijalankan dengan uang sungguhan.

Tiga ratus lebih konfigurasi, lima jalur microstructure, dan enam
kandidat edge sudah diuji dengan walk-forward dan mirror test. Semuanya
positif di data bullish dan negatif di data bearish.

## Data

21 simbol, 208 hari (2026-03-09 s/d 2026-10-03), 105.044 candle 1m
dari Hyperliquid, tanpa API key.

Dua rezim dipilih dari data itu sendiri, bukan dari asumsi — jendela
30 hari dengan return paling ekstrem:

| rezim | return | periode |
|---|---|---|
| bullish | +0.06%/hari | 20673 – 20702 |
| bearish | −0.04%/hari | 20585 – 20614 |

## Hasil kandidat terbaik

Diuji di kedua rezim, dengan spread terukur (0.12–1.97 bps per simbol,
bukan asumsi 3 bps) dan fee taker 4.5 bps per sisi:

| kandidat | bullish/trade | bearish/trade |
|---|---|---|
| momentum long | **+0.3899** | **−1.0183** |
| momentum long t.15 | +0.4476 | −0.9488 |
| EMA trend 2%/4%, 2h | +0.3402 | −1.0891 |
| EMA trend 2%/3%, 4h | +0.7252 | −1.1076 |
| EMA trend 1%/2%, 8h | +0.7283 | −1.0271 |
| momentum dua arah | −0.1765 | −0.3550 |

Profit factor di bearish: **0.69 sampai 0.76** untuk semua kandidat
long-biased.

### Yang paling penting

Rugi di bearish bukan hanya "kebetulan tidak menang". Itu **1.4 sampai
3.2 kali lebih besar** dari keuntungan di bullish, dan konsisten
di semua kandidat.

Efeknya praktis: kalau bot masuk long-biased dan kebetulan aktif
saat pasar turun, kerugiannya bukan sebanding dengan keuntungan saat
naik. Long-bias bukan netral — itu leverage tersembunyi ke arah
risiko yang salah.

## Gate rezim tidak menolong

Pertanyaan terakhir sebelum menyimpulkan: kalau bot berhenti
bertransaksi saat pasar turun, apakah advantage bull cukup?

Dua belas kombinasi threshold (0.0% – 1.0%) dan jendela (12h – 48h),
semua membaca return indeks dari data historis — jadi simulasi yang
bisa dideploy, bukan yang butuh informasi masa depan.

**Semua negatif.**Terbaik: threshold 1% / jendela 48h menyaring
66.481 sinyal dan menyisakan 7.215 trade, dan per trade tetap −0.5148.

Gate yang ketat tidak mengubah kesimpulan; ia hanya mengurangi
jumlah opportunity tanpa memperbaiki ekspektasi.

## Yang sudah dibuktikan sebelumnya

Enam kandidat menolak di mirror test (membalik harga secara sintetis):

- Empat kandidat time-series, 1.008 dan 2.160 konfigurasi
- Satu kandidat swing 2 jam: positif 3/3 fold asli, negatif 3/3 mirror
- Satu Order Flow Imbalance Q1: t turun dari +3.72 ke +0.83 begitu
  data ditambah, dan negatif di mirror

Mirror test membalikkan harga tanpa mengubah volume atau volatilitas,
jadi hasilnya lebih dramatis dari data nyata. Data bearish asli
mengonfirmasi arah kesimpulan yang sama dengan besaran yang lebih
terukur.

## Yang tidak diuji, dan kenapa

**Order flow pada data historis.** Order book hanya terkumpul 25 jam,
dan itu hanya periode bullish. Butuh berbulan-bulan, dan itu satu-
satunya jalur yang masih mungkin punya edge — tapi belum ada data
yang cukup untuk mengujinya.

**Untuk horizon pendek (15m dan 5m),** API Hyperliquid membatasi 5.000 candle per
request; 1m hanya menjangkau 4 hari dan 15m 52 hari. Untuk horizon
pendek, data historis praktis tidak tersedia dari bursa ini.

## Implikasi ke configuration

Config yang sekarang (SL 0.40%, TP 0.60%, hold 900s) **sudah
benar dan jujur**. Perbaikannya dari config lama terukur:

- `tight_sl_pct` 0.25% → 0.40%: nilai lama tidak pernah dipakai,
  118 dari 123 SL_HIT nyata di 0.35–0.45%
- `max_hold_seconds` 300 → 900: median hold 46s tapi p90 jauh di
  atas 5 menit
- spread floor per simbol: asumsi 3 bps ternyata 1.6 sampai 25 kali
  lebih besar dari median yang terukur

PF paper membaik dari 0.412 ke 0.603 (biaya terukur: 0.905). Itu
perbaikan nyata, tapi tidak mengubah kesimpulan: tidak ada edge.

## Rekomendasi

Jangan jalankan live. Bukan karena kode rusak — kodenya sudah
diperbaiki dan diuji (605 test, semua hijau). Tapi karena:

1. Tidak ada edge yang bertahan di kedua rezim
2. Long-bias mengubah kerugian di rezim bearish jadi lebih besar,
   bukan lebih kecil
3. Tidak ada mekanisme regime detection di kode yang menghentikan
   bot saat kondisi yang salah

Yang perlu benar-benar terpisah:jika tujuan akhirnya bot yang menang,,
perlu data order book berbulan-bulan dan model microstructure yang
belum ada. Yang sudah selesai adalah membuat broker ini jujur —
ia sekarang melaporkan apa yang sebenarnya terjadi, bukan apa yang
terlihat rapi.

## Berkas riset

| Berkas | Isi |
|---|---|
| `research/bt.py` | harness backtest dengan biaya produksi |
| `research/fetch_historical.py` | unduh 400 hari dari Hyperliquid |
| `research/regime_split.py` | pisahkan bullish/bearish dari data |
| `research/test_reality.py` | kandidat di dua rezim nyata |
| `research/regime_gate.py` | apakah gate rezim menolong |
| `research/mirror_test_final.py` | mirror test untuk kandidat swing |
| `research/mirror_ofi.py` | mirror test untuk OFI |
| `research/spread_stability.py` | sebaran spread terukur |
| `research/ofi_test.py` | korelasi OFI vs return ke depan |
