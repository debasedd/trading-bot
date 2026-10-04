# STATE — sumber status lintas sesi

**File ini adalah satu-satunya sumber status program.** Sesi berikutnya harus
membacanya sebelum bekerja, dan memperbaruinya di akhir setiap sesi kerja serta
sebelum menulis laporan fase.

Terakhir diperbarui: 2026-10-04 · Branch: `fase-1` · HEAD: `caa57b1`

---

## 1. Status fase dan gerbang

### Fase 0 — baseline dan verifikasi klaim

**LULUS.** Laporan: `docs/reports/fase-0.md`

Tugasnya membuktikan defect-nya ada, bukan memperbaikinya. Lima defect
direproduksi dengan test xfail strict. Tanpa stub pada komponen yang diuji.

### Fase 1 — perbaikan jalur live

**BELUM LULUS.** Laporan sementara: `docs/reports/fase-1-partial.md`
(`fase-1.md` final hanya boleh ditulis kalau gerbang benar-benar lulus).

Commit dasar Fase 1: `b0190e7` · HEAD saat ini: `caa57b1`

| Item | Isi | Status | Commit |
|---|---|---|---|
| (a) | normalisasi kunci simbol | selesai | `7844fc9` |
| (b) | fill bursa ke DB + daily-loss breaker | selesai | `67862f0` |
| (c) | partial fill | selesai | `df3a2ee` |
| (d) | idempotensi retry | selesai | `9e2974a` |
| (e) | breakeven live | selesai | `caa57b1` |
| (f) | funding dari bursa | **belum** | — |
| (g) | filter `mode='live'` + `close_position` atomik | **sebagian** — lihat §5 | — |
| (h) | kill switch | **belum** | — |
| (i) | shutdown order resting | **belum** | — |
| (j) | arming CLI yang bisa diaudit | **belum** | — |

Dua item tambahan dari keputusan 2026-10-04:

| Tambahan | Status | Commit |
|---|---|---|
| Pemisahan signing key / account address | selesai | `052950a` |
| Bukti offline untuk semua jenis order | **sebagian** | `052950a` |

Pemisahan wallet (`052950a`) baru mencakup limit order. Trigger SL/TP, cancel,
dan modify belum diuji offline.

#### Gerbang 1 — semua harus terpenuhi

| Gerbang | Status |
|---|---|
| Semua test reproduksi hijau, xfail dilepas | **GAGAL** — 4 xfailed tersisa |
| Tidak ada stub bursa pada jalur yang diuji | terpenuhi |
| Chaos test (`kill -9` saat posisi terbuka di testnet) | **belum dijalankan** — butuh testnet key |
| Smoke test testnet (buka → SL/TP kena → DB cocok) | **belum dijalankan** — butuh testnet key |
| `docs/reports/fase-1.md` selesai | belum ada |

**Dua gerbang terakhir tidak bisa dijalankan tanpa testnet key dari operator.**
Tanpa itu, gerbang Fase 1 secara faktual belum bisa dinyatakan lulus.

#### Condition suite saat ini

```
$ python -m pytest tests -q
2 failed, 689 passed, 4 skipped, 4 xfailed, 37 subtests passed

$ python -m unittest discover tests
Ran 683 tests — OK (skipped=4, expected failures=4)
```

Dua `failed` = `test_live_tui::TestUnpatchedSmoke`, **pre-existing** dan
tidak terkait Fase 1: `console.ask_mode` memanggil `input()` sementara pytest
menangkap stdin. Terbukti di Fase 0 dengan `git stash`.

Empat `xfailed` tersisa semuanya milik DEFECT-1 (`int()` pada field string di
`client.py:219`).

---

## 2. Keputusan yang sudah disetujui operator

| Tanggal | Keputusan |
|---|---|
| 2026-10-04 | **Testnet key** sedang disiapkan (butuh deposit mainnet untuk faucet). Bot WAJIB mendukung pemisahan `account_address` vs signing key. Diberikan sebagai **API/agent wallet key testnet saja**, dengan wallet utama terpisah. |
| 2026-10-04 | **Item (e) breakeven**: hapus fitur atau nonaktifkan eksplisit di mode live, plus test yang membuktikan jalur live tidak bisa memanggilnya. → DONE, `caa57b1`. |
| 2026-10-04 | **Item (f) funding**: verifikasi endpoint di dokumentasi resmi Hyperliquid. Catatan operator: `userFunding` untuk funding payments, `userNonFundingLedgerUpdates` untuk non-funding. Nama yang saya sebut sebelumnya kemungkinan salah. |
| 2026-10-04 | **Urutan (g)(h)(i)(j)**: urut dampak, tiap butir dimulai dengan test gagal, satu commit per defect. |
| 2026-10-04 | **Tambahan wajib**: (a) test signing SDK offline untuk limit, trigger SL/TP, cancel, modify; (b) jalankan suite dengan jam dibekukan di beberapa jam UTC **dan zona waktu**; (c) untuk setiap test baru, bukti bahwa test itu gagal ketika perbaikannya dicabut. |
| 2026-10-04 | **Batas live** (`max_order_notional=100` USDC) tidak boleh disentuh. |
| 2026-10-04 | `docs/reports/fase-1.md` hanya ditulis kalau gerbang benar-benar lulus. Sampai itu, perbarui `fase-1-partial.md`. |
| 2026-10-04 | Branch kerja: `fase-1`. `research/` adalah wilayah sesi lain — jangan diedit. |

**Catatan belumdieksekusi:** tambahan (b) menguji beberapa jam UTC. Sapi
**zona waktu** belum diuji. Lihat §5 butir 5.

---

## 3. Temuan yang mengubah gambaran

### 3.1 Tidak ada order opening live yang pernah bisa terkirim — `34a3a72`

`make_cloid()` menghasilkan `'tb-<16 hex>'`. SDK Hyperliquid menandatangani
cloid bersama order (`signing.py::order_request_to_order_wire` memanggil
`cloid.to_raw()`), sehingga format yang diterima bursa adalah `0x` + 32 hex dan
harus jadi objek `Cloid`.

String biasa gagal di lapisan signing dengan `AttributeError` — **sebelum
signing selesai, sebelum ada yang dikirim ke bursa**:

```
cloid dari make_cloid()  -> GAGAL: AttributeError: 'str' object has no attribute 'to_raw'
sesudah perbaikan       -> WIRE OK: c=0x2efa58126c5f48dbaca54f5c418beadd
```

`place_limit_order` menangkap exception itu dan mengubahnya jadi
`OrderOutcome(ok=False)`, yang terlihat seperti penolakan bursa biasa.
Karena `SafetyGate` mewajibkan cloid untuk order opening
(`Blocker.MISSING_CLOID`) dan cloid selalu diisi, **setiap order opening dan
setiap order closing ditolak**.

Artinya: semua temuan Fase 0 tentang jalur live diuji pada sistem yang secara
faktual tidak bisa membuka posisi. Ini mengubah cara membaca hasil Fase 0 —
bukan berarti temuan-temuannya salah, tapi karena itu jalur itu
belum pernah benar-benar jalan.

### 3.2 Bursa tidak punya lookup order by cloid — diverifikasi dua kali

```
orderStatus + oid numerik  -> 200 {"status":"order","order":{...}}
orderStatus + oid = hex    -> 422 Failed to deserialize the JSON body
orderStatus + cloid        -> 422
queryOrderByCloid          -> 422 (tidak ada di bursa)
```

SDK punya `cancel_by_cloid` — itu **action L1 yang membatalkan**, bukan query.
Artinya order yang masih resting bisa dibatalkan; order yang sudah terisi tidak
bisa ditanya siapa-siapa.

Konsekuensi desain (dipakai di `9e2974a`): karena bursa tidak bisa menjawab
"apakah order ini sudah masuk?", satu-satunya opsi yang tidak menebak adalah
**cloid sama = tidak mengirim ulang**. Xfail lama yang menuntut
`order_status_by_cloid()` dihapus di `334b048` karena menuntut sesuatu yang
mustahil.

### 3.3 Test yang hijau karena alasan yang salah — dua kejadian

Pola ini berulang dan harus diwaspadai di setiap test baru.

**Gerbang menolak order.** Order test yang collapsible lewat batas notional
ditolak `SafetyGate`, jadi test lulus karena GERBANG menolak, bukan karena
logika yang diuji. Batas produksi bisa jauh lebih tinggi dari
yang dikira. Terjadi di item (c) dan (b).

**Jam dinding menentukan hasil test.** `SafetyGate` menolak order di luar
`cfg.live_window_utc = (13, 23)` UTC. Test partial fill mengirim order tanpa
meng-inject jam, jadi **hanya lulus antara pukul 13:00 dan 23:00 UTC**.
Commit `df3a2ee` saya laporkan hijau karena suite kebetulan dijalankan pukul
13:15 UTC — bukan karena testnya benar. Terbukti saat suite dijalankan pukul
06:18 UTC:

```
FAILED test_partial_fill_is_reported_in_message
FAILED test_protection_uses_filled_size_not_requested
FAILED test_remainder_is_canceled
ORDER DITOLAK BTC/USDT:USDT BUY 0.001 @ 85134.0: di luar jendela waktu trading (UTC)
```

Diperbaiki di `ba1f9fa` lewat `repro_helpers.inside_trading_window()`. Bukti:
sapu jam 0..23 → **hijau di 24 dari 24 jam**; sebelum perbaikan 14 dari 24
gagal.

Pelajaran: test yang hanya hijau sebagian hari bukan bukti apa pun, dan tidak
terlihat di output mana pun.

### 3.4 Breakeven hanya bergerak di database — `caa57b1`

`ExecutionAgent._protect_breakeven` menulis SL baru ke SQLite tanpa menyentuh
trigger di bursa. `LivePosition.sl_order_id` — order yang benar-benar melindungi
posisi — tidak pernah berubah.

Di mode live: DB menampilkan SL sudah di breakeven, sementara bursa masih
memakai level lama. Kalau proses mati, posisi tetap memakai SL lama.

Dinonaktifkan di live; tetap jalan di paper.

### 3.5 Kewajiban bukti cabutan — sudah melanggar sekali

Untuk item (e), bukti cabutan pertama **tidak tertangkap**: mengembalikan filter
mode tidak mengubah hasil test. Penyebabnya test saya sendiri — kalau hanya ada
posisi live, jalur produksi keluar di `if not paper_rows: return` dan loop tidak
pernah jalan, jadi test lulus karena guard tak sengaja.

Setelah test men-seed posisi live **dan** paper sekaligus, mutasi tertangkap:

```
FAILED test_live_position_sl_is_not_rewritten
AssertionError: 85084.99999999999 != 83000.0 within 6 places
1 failed, 4 passed
```

**Kewajiban ini mengikat ke depan:** setiap test baru harus menjalankan mutasi
sebelum commit, dan hasil mutasi dicantumkan di pesan commit. Test yang belum
dimutasi dianggap belum terbukti.

### 3.6 Test lama bisa menguji perilaku yang tidak pernah bekerja

Tiga kasus ditemukan, semuanya "hijau karena salah":

- `test_live_engine.py` punya 15 kemunculan hardcode `"BTC / USDC:USDC"` — lulus
  karena menguji defect normalisasi simbol.
- `test_cloid_has_prefix` menuntut `startswith("close-")` — prefix tidak muat
  dalam 16 byte, jadi order dengan prefix tidak pernah sampai ke bursa.
- `test_order_applies_quantization` mengirim `cloid="c"`.

Kasus ketiga menunjukkan risiko nyata: perbaikan yang benar bisa membuat test
lama gagal karena test itu mengirim input yang tidak pernah valid.

---

## 4. Larangan yang tetap berlaku

Berlaku di semua sesi, tanpa kecuali.

1. **Jangan set `TRADEBOT_LIVE` atau `TRADEBOT_LIVE_CONFIRMED`.** Jangan baca,
   minta, atau pakai private key mainnet. Jangan kirim order ke mainnet. Kerja
   hanya di kode, test, paper, dan testnet dengan key testnet dari operator —
   jangan membuat atau mencari key sendiri. Pindah ke mainnet adalah keputusan
   operator setelah semua gerbang lulus.
2. **Jangan melonggarkan batas safety** (daily loss, notional cap, kill switch)
   supaya test lulus. `max_order_notional=100` USDC tidak boleh disentuh.
3. **Jangan mengubah atau memoles angka riset.** Laporkan apa adanya. Jangan
   tulis "edge terbukti" tanpa bukti yang sudah melewati Fase 3.
4. **Jangan menstub komponen yang sedang diuji.** Stub di
   `tests/test_live_engine.py:63` menutupi defect fatal. Test live harus memakai
   objek/respons nyata, fixture rekaman dari SDK Hyperliquid, atau testnet.
5. **Satu commit per defect.** Setiap perbaikan dimulai dari test yang GAGAL, cari
   akar masalah, perbaiki, test jadi hijau. Setiap klaim "selesai" disertai
   output perintah yang membuktikannya.
6. **Berhenti di akhir fase.** Kirim laporan, tunggu persetujuan operator.
7. **Jangan edit `research/`.** Wilayah sesi lain.
8. **Kesimpulan program:** kalau validasi gagal, laporkan `NO-GO` dan berhenti.

---

## 5. Yang belum dikerjakan dan tidak yakin

### Tidak yakin

1. **Bukti cabutan belum dilakukan untuk (a), (b), (c), (d), dan pemisahan
   wallet.** Semuanya Green, tapi hanya yang (e) yang sudah dimutasi. Test
   yang belum dimutasi belum terbukti menangkap apa yang diklaim.

2. **Zona waktu belum diuji.** Tambahan (b) dari operator meminta jam UTC
   **dan zona waktu**. `SafetyGate.in_live_window` memakai UTC secara eksplisit
   dan otomatis memakai `datetime.now(timezone.utc)`, jadi menggeser `TZ`
   seharusnya tidak berpengaruh — tapi itu belum dibuktikan, dan
   `day_utc` yang di-rollover ikut arises dari jam lokal di beberapa tempat.

3. **`_seen_fill_ids` tumbuh tanpa batas.** Dedup fill berbasis `tid`
   menyimpan semua tid yang pernah dilihat. Untuk sesi panjang set itu membesar
   tanpa batas. Tidak berbahaya sekarang (2.000 fill ≈ beberapa ratus KB), tapi
   belum diukur.

4. **`_last_fill_time` di-reset ke "sekarang" setiap poll.** Kalau `userFills`
   memfilter dengan `startTime` yang eksklusif, fill pada detik yang sama bisa
   terlewat. Belum diuji batas itu.

5. **`uncertain_orders` hanya di memori.** Item (d) mencatat order yang statusnya
   tidak diketahui supaya tidak terkirim ulang dan tidak hilang diam-diam. Tapi
   saat restart, peta itu kosong — order yang timeout sebelum restart hilang
   dari catatan. Item (i) kemungkinan besar harus Tama covers ini.

6. **Bukti offline signing baru mencakup limit order.** Trigger SL/TP, cancel,
   dan modify belum diuji offline.

### Belum dikerjakan

Item (f), (g), (h), (i), (j), dan tambahan wajib (b) zona waktu.

---

## 6. Langkah berikutnya

Urut dampak, tiap butir dengan TDD penuh dan bukti cabutan:

1. **(h) Kill switch.** `disengage_kill_switch` (`safety.py`) tidak punya
   pemanggil produksi — nol. Ada kontradiksi nilai: `__init__:229` melepas
   switch kalau `TRADEBOT_LIVE_KILL_SWITCH=0`, tapi `master_blockers:345`
   tidak memasukkan `"off"` ke himpunan yang dianggap "tidak aktif", sehingga
   operator yang menyetel `off` masih melihat switch aktif. Perlu juga
   `persist()` yang selalu jalan.
2. **(g) Filter `mode='live'`.** Parameter `mode` sudah ada di signature
   `get_open_positions` (repository.py:185) dan `get_trade_stats` (:599), tapi
   pemanggil produksi tidak mengisinya:
   - `agents/decision_agent.py:140` — `get_open_positions()` tanpa mode
   - `agents/execution_agent.py:278, :314` — `get_open_positions()` tanpa mode

   `get_daily_realized_pnl` (:549) tidak punya parameter `mode` sama sekali.
   **`close_position` (:149) ternyata SUDAH atomik** — `WHERE id = ? AND
   status = 'OPEN'` dengan `commit()` sendiri, dan `cursor.rowcount > 0`
   memastikan hanya satu pemanggil yang mendapat klaim. Yang belum ada adalah
   pengembalian nilai (margin) ke pemanggil; perlu dipastikan apakah itu
   memang bagian dari item (g) atau tidak.
3. **(i) Shutdown order resting.** Kebijakan eksplisit: batalkan saat keluar,
   atau rekonsiliasi saat start. Related: `uncertain_orders` harus bertahan
   melewati restart (lihat §5 butir 5).
4. **(f) Funding.** Baca dokumentasi resmi Hyperliquid dulu. Verifikasi nama
   endpoint yang benar — `userFunding` untuk funding payments,
   `userNonFundingLedgerUpdates` untuk non-funding. Cek dulu sebelum
   menulis kode.
   kode.
5. **Tambahan wajib (a):** test signing offline untuk trigger SL/TP, cancel,
   modify.
6. **Tambahan wajib (b):** jalankan suite dengan jam dibekukan di beberapa zona
   waktu.
7. **Jalankan bukti cabutan untuk item (a), (b), (c), (d) dan pemisahan wallet.**
8. **Perbarui `docs/reports/fase-1-partial.md`** setelah tiap item.

Setelah semua item selesai: minta testnet key, jalankan chaos test dan smoke
test. Baru kalau keduanya lulus, tulis `docs/reports/fase-1.md`.

---

## 7. Pertanyaan yang menunggu operator

1. **Testnet key** — masih menunggu. Diberikan sebagai API/agent wallet key
   testnet dengan `HYPERLIQUID_ACCOUNT_ADDRESS` untuk wallet utama. Dukungan
   pemisahan sudah terbukti di `052950a`.

2. **Item (e)** — sudah diputuskan dan dikerjakan. Tidak ada pertanyaan terbuka.

3. **Item (f)** — perlu konfirmasi setelah saya cek dokumentasi resmi: endpoint
   mana yang jadi sumber funding per-fill, dan bagaimana funding dicatat di
   ledger (`trades` atau tabel terpisah).

4. **`uncertain_orders` dan restart** — item (d) mencatat order yang statusnya
   tidak diketahui, tapi hanya di memori. Apakah ini harus dipersist ke
   `live_counters.json` atau file state lain? Persistensi ke file state berarti
   operator bisa retomada setelah restart.

5. **Urutan (g)(h)(i)(j)** — saya proposes (h) lebih dulu karena
   `disengage_kill_switch` yang tidak terpakai adalah lubang safety, bukan
   sekadar fitur yang belum ada. Setuju?

---

## Lampiran — perintah verifikasi

```bash
git log --oneline -9
python -m pytest tests -q                        # 2 failed pre-existing, 689 passed
python -m pytest tests -q --tb=no 2>&1 | tail -2
python -m unittest discover tests 2>&1 | grep -E "^(OK|FAILED|Ran )"

# test per item
python -m pytest tests/test_symbol_normalization.py tests/test_fill_reconciliation.py \
                 tests/test_fill_ledger.py tests/test_partial_fill.py \
                 tests/test_order_idempotency.py tests/test_cloid_wire_encoding.py \
                 tests/test_api_wallet_separation.py tests/test_breakeven_live_disabled.py -q

# bukti clock fragility
python -m pytest tests/test_partial_fill.py tests/test_order_idempotency.py -q
```
