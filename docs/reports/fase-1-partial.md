# Fase 1 (PARSIAL) — Perbaikan Jalur Live

**Status gerbang: BELUM LULUS** — 3 dari 10 item selesai.

Tanggal: 2026-10-04 · Commit dasar: `5a97d11` · Commit hasil: `df3a2ee`

> **INI BUKAN LAPORAN FASE 1 FINAL.** Item (d)–(j) belum dikerjakan. Saya
> berhenti di sini karena konteks sesi hampir habis, dan setiap item Butuh
> TDD penuh. Lanjut di sesi baru lebih aman daripada menulis kode dengan
> konteks yang sudah terpotong.
>
> Yang TIDAK boleh terbaca dari parsial ini: gerbang Fase 1 belum
> terpenuhi, chaos test belum dijalankan, dan smoke test testnet belum
> dilakukan — karena butuh testnet key darimu.

---

## 1. Ringkasan

Tiga item pertama dari Fase 1 selesai dengan TDD penuh: (a) normalisasi
kunci simbol, (b) deteksi + pencatatan fill bursa yang menghidupkan
daily-loss breaker, dan (c) penanganan partial fill. Yang paling
berp Impacts adalah temuan bahwa **bursa tidak menyediakan lookup order
by cloid** — `orderStatus` dengan `cloid` mengembalikan HTTP 422 dan
`queryOrderByCloid` juga tidak ada. Item (d) karena itu harus
dirancang ulang: idempotensi harus lewat `oid` yang sudah diketahui,
bukan cloid. Test suite tetap hijau di kedua runner.

---

## 2. Daftar commit

| Commit | Isi |
|---|---|
| `7844fc9` | Item (a): satu fungsi normalisasi untuk semua kunci simbol |
| `67862f0` | Item (b): fill SL/TP bursa terdeteksi, dicatat, menghidupkan daily-loss breaker |
| `df3a2ee` | Item (c): partial fill dibatalkan, proteksi mengikuti ukuran riil |

Tidak ada commit lain. Perubahan produksi hanya di `trading/live/*.py`
dan `run.py`; sisanya test dan fixture.

---

## 3. Bukti

### 3.1 Item (a) — normalisasi kunci simbol

Sebelum (reproduksi Fase 0):

```
POSISI HANTUAN: 1 tercatat lokal tapi TIDAK ada di bursa: ['BTC/USDT:USDT']
POSISI ASING: 1 ada di bursa tapi tidak tercatat: ['BTC / USDC:USDC']
KILL SWITCH diaktifkan oleh sistem: rekonsiliasi gagal
```

Setelah:

```
$ python -m pytest tests/test_symbol_normalization.py
10 passed

$ python (reconcile langsung)
REMOTE KEY : 'BTC/USDT:USDT'
REPORT     : {"only_local": [], "only_remote": [], "size_mismatch": [], "matched": 1}
KILLSWITCH : False
```

`normalize_symbol()` menerima "BTC", "BTC/USDT:USDT", dan
"BTC / USDC:USDC"; selalu mengembalikan format internal; idempoten.
Dipakai di `_fetch_remote_positions` dan `check_pending_fills`.

**Test lama yang harus diubah:** `tests/test_live_engine.py` punya 15
kemunculan hardcode `"BTC / USDC:USDC"` — ia lulus karena kebetulan
**menguji defect**, bukan karena benar.

### 3.2 Item (b) — fill bursa

Sumber kebenaran `userFills`, direkam apa adanya dari testnet:

```json
{"coin":"BTC","px":"85171.0","sz":"0.00069","side":"B",
 "dir":"Close Short","closedPnl":"-0.0414","fee":"-0.001763",
 "oid":61756806860,"tid":789291717218145,"feeToken":"USDC"}
```

Tiga hal yang hanya terlihat dari data nyata dan menentukan semuanya:
`dir` membedakan Open/Close, `closedPnl` sudah net di bursa, `fee`
negatif.

Bukti end-to-end lewat `health_check` (jalur yang sama dengan `run_loop`):

```
=== END-TO-END lewat health_check (jalur run_loop) ===
health ok          : True
kill switch        : False
closed_by_exchange : [{'coin':'BTC','size':0.00069,
                       'closed_pnl':-0.0414,'fee':0.001763,
                       'reason':'EXCHANGE_FILL'}]
posisi di DB       : [{'status':'CLOSED','realized_pnl':-0.0414,
                       'close_reason':'SL_HIT'}]
trades CLOSE       : [{'quantity':0.00069,'fee':0.001763,'mode':'live'}]
daily-loss counter : -0.0414
```

`daily-loss counter` yang tadinya selalu 0.0 — itu bukti `DAILY_LOSS_LIMIT`
sekarang punya sumber angka.

Test: `tests/test_fill_reconciliation.py` (11) + `tests/test_fill_ledger.py`
(9), yang memakai SQLite sungguhan di tempfile.

### 3.3 Item (c) — partial fill

`frontendOpenOrders` testnet:

```json
{"coin":"BTC","sz":"0.12","origSz":"0.3","oid":61757018228,
 "isTrigger":false,"orderType":"Limit","tif":"Alo"}
```

`origSz` adalah satu-satunya cara bot tahu masih ada order aktif.
Test: `tests/test_partial_fill.py` (9 test).

### 3.4 Suite penuh

```
$ python -m pytest tests -q
2 failed, 653 passed, 4 skipped, 7 xfailed, 37 subtests passed in 13.27s
$ python -m unittest discover tests
657 test, OK (skipped=4, expected failures=7)
```

Dua `failed` = `test_live_tui::TestUnpatchedSmoke`, **pre-existing** —
`console.ask_mode` memanggil `input()` sementara pytest menangkap stdin.
Terbukti di Fase 0 dengan `git stash`: muncul tanpa file reproduksi.

---

## 4. Yang gagal, tidak selesai, dan tidak yakin

### Tidak selesai — 7 dari 10 item

| Item | Status |
|---|---|
| (d) Idempotensi | **BELUM** — dan rancangannya berubah (lihat §5.1) |
| (e) Breakeven live | **BELUM** |
| (f) Fee & funding dari fill | **SEBAGIAN** — fee sudah dari bursa (item b); funding per fill belum |
| (g) Filter `mode='live'` + `close_position` atomik | **BELUM** |
| (h) Kill switch: kontradiksi "off", `disengage`, persist | **BELUM** |
| (i) Shutdown: kebijakan order resting | **BELUM** |
| (j) Mekanisme arming yang bisa diaudit | **BELUM** |

### Gerbang Fase 1 yang belum diuji

- **Chaos test** (`kill -9` saat posisi terbuka, restart, rekonsiliasi
  bersih) — butuh testnet key.
- **Smoke test testnet** (buka → SL/TP kena → DB cocok dengan fill bursa) —
  butuh testnet key.
- Says: "Minta saya key testnet bila belum ada." — **saya belum punya
  key itu.** Tanpa key, dua gerbang di atas tidak bisa dijalankan, jadi
  gerbang Fase 1 secara faktual belum bisa dinyatakan lulus.

### Tidak yakin

1. **Item (d) perlu keputusan desain Anda.** Temuan di §5.1 mengubah
   pendekatannya. Saya tidak ingin menebak.

2. **Item (f) perlu define-equil.** `userFills` tidak punya funding per
   fill. Funding datang dari `userNonFundingBookUpdates` atau dari
   `cumFunding` di `clearinghouseState`. Saya belum cek mana yang
   lengkap dan bisa diandalkan.

3. **Apakah ada fill yang saya lewatkan.** `poll_exchange_fills` dedup
   lewat `tid` dan menyimpan semua `tid` yang pernah dilihat di
   `_seen_fill_ids` (tanpa batas). Untuk sesi panjang, set itu tumbuh
   tanpa batas. Tidak berbahaya sekarang (2.000 fill ≈ beberapa ratus
   KB), tapi belum saya ukur.

4. **`_last_fill_time` di-reset ke "sekarang" setiap poll**, artinya
   fill yang terjadi tepat di detik yang sama bisa terlewat kalau
   `userFills` memfilter dengan `startTime` yang eksklusif. Saya belum
   menguji batas itu.

5. **Duplikasi isi fill.** `health_check` memanggil `poll_exchange_fills`,
   dan `run_loop` juga memanggilnya via `check_pending_fills`? Tidak —
   `check_pending_fills` tidak mem-poll fill. Tapi kalau nanti ada
   pemanggil kedua, dedup berbasis `tid` akan mencegah dobel catat.

---

## 5. Temuan di luar lingkup (penting)

### 5.1 BURSA TIDAK PUNYA LOOKUP ORDER BY CLOID — item (d) berubah bentuk

Saya memeriksa langsung ke testnet:

```
orderStatus + oid    -> 200 {"status":"order","order":{...,"status":"canceled"}}
orderStatus + cloid  -> HTTP 422 "Failed to deserialize the JSON body"
queryOrderByCloid    -> HTTP 422 "Failed to deserialize the JSON body"
```

Dan SDK Python hanya punya `cancel_by_cloid` / `bulk_cancel_by_cloid`,
tanpa `query_order_by_cloid`.

Artinya: **idempotensi retry tidak bisa bergantung pada cloid.** Dan
`cloid` yang selama ini diklaim sebagai guarantee idempotensi di tiga
docstring (`client.py:384`, `engine.py:225`, `executor.py:64`) —
garansi yang tidak punya endpoint di bursa.

Yang bisa dipakai:
- `origSz` vs `sz` untuk partial fill (sudah dipakai di item (c))
- `oid` yang dikembalikan bursa, dicek via `orderStatus`
- `frontendOpenOrders` + `historicalOrders` untuk verifikasi

**Ini keputusan yang perlu Anda ambil** (lihat §6).

### 5.2 Tiga jebakan test yang muncul di Fase 1

Semuanya satu jenis yang sama: **test lulus karena alasan yang salah.**

1. **Item (a)–(b): gerbang mewarisi kill switch antar-test.** `SafetyGate`
   memuat `data_store/live_counters.json` kalau `TRADEBOT_LIVE=1`.
   Beberapa test XPASS karena order ditolak kill switch, bukan karena
   defect teratasi. Diperbaiki dengan `repro_helpers.clean_gate()`
   (path tempfile + counters direset).

2. **Item (b): wiring salah tidak tertangkap.** `poll_exchange_fills()`
   memanggil `on_exchange_fill` dengan DAFTAR FILL, tapi
   `record_exchange_fills()` awalnya tanpa parameter → fill terbaca,
   pencatatan gagal, log hanya "gagal dicatat" tanpa jejak DB.
   Semua test yang memanggil fungsi itu langsung tetap hijau.
   Ditambahkan `test_wiring_via_on_exchange_fill_callback`.

3. **Item (c): gate menolak order test.** Order 0.004 BTC = 340 USDC >
   `max_order_notional` 100 USDC → test lulus karena GERBANG menolak.
   Ukuran dikoreksi ke 0.001 (85.13 USDC).

Pola yang sama sudah menimpa saya di Fase 0 (test `test_close_short_is_sell`
tanpa assertion). **Rekomendasi: setiap test live harusvenue assert
bahwa order benar-benar melewati gate sebelum_domhong behavior yang
diuji.**

### 5.3 Dua test lama menguji perilaku yang salah

- `test_live_engine.py:15` kemunculan hardcode `"BTC / USDC:USDC"` —
  lulus karena menguji defect.
- `test_resting_order_without_position_flags` —yang sehat
  `health_check` menandai order resting sebagai problem (yang berarti
  kill switch menyala). Test sekarang: `..._is_not_an_anomaly`.

---

## 6. Pertanyaan / keputusan yang butuh Anda

1. **Item (d) — bagaimana cara idempotensi?** Usulan saya: simpan
   `oid` bursanya saat order dikirim; saat timeout, `orderStatus(oid)`
   untuk memastikan status sebelum retry. Alternatif: coba
   `cancel_by_cloid` dulu (SDK punya), lalu `frontendOpenOrders` untuk
   memastikan tidak ada order tersisa untuk koin itu, baru retry.
   Mana yang Anda mau? Saya tidak ingin menebak di jalur uang.

2. **Testnet key.** Gerbang Fase 1 butuh chaos test dan smoke test yang
   keduanya butuh key. Anda belum memberikan. Apakah Anda ingin:
   (a) saya lanjut item (d)–(j) dulu tanpa chaos/smoke, lalu testnet
   di akhir; atau (b) Anda berikan key sekarang supaya chaos/smoke bisa
   masuk per-item?

3. **Item (e) — breakeven live.** Dua opsi: (a) cancel trigger lama +
   pasang yang baru di bursa; (b) hapus fitur breakeven live sepenuhnya
   dan andalkan SL/TP statis. (a) lebih benar secara ekonomi tapi
   menambah dua order setiap kali breakeven menyala, dan itu menambah
   rate-limit pressure. Mana?

4. **Item (f) — funding.** `userFills` tidak punya funding. Saya perlu
   cek `userNonFundingBookUpdates` dulu. Boleh saya lakukan?

5. **Batas live.** `max_order_notional=100`, `max_total_notional=600`
   USDC sangat kecil. Untuk smoke test testnet ini cukup, tapi untuk
   "diuji dengan uang sungguhan dalam jumlah kecil" mungkin perlu
   diturunkan dulu — atau justru dinaikkan setelah semua gerbang lulus?
   Saya tidak akan menyentuhnya tanpa persetujuan eksplisit (aturan
   keras nomor 2).

---

## Lampiran — verifikasi ulang

```bash
git log --oneline -4
python -m pytest tests -q                      # 653 passed, 7 xfailed
python -m pytest tests/test_symbol_normalization.py -v
python -m pytest tests/test_fill_reconciliation.py tests/test_fill_ledger.py -v
python -m pytest tests/test_partial_fill.py -v
```
