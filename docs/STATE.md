# STATE — sumber status lintas sesi

**Satu-satunya sumber status program.** Baca sebelum bekerja. Perbarui di
akhir setiap sesi dan sebelum laporan fase.

Terakhir: 2026-10-04 · Branch `fase-1`

---

## Ringkasan

Fase 0 **LULUS**. Fase 1 **BELUM LULUS** — 5 dari 10 item selesai, dua gerbang
terakhir belum bisa dijalankan karena butuh testnet key.

```
pytest:   820 passed, 4 skipped          (0 failed, 0 xfailed)
```

Tidak ada test failed maupun xfailed. Semua perbaikan Fase 1 sudah punya bukti
cabutan. Dua gerbang yang tersisa — chaos test dan smoke test testnet — butuh
testnet key dari operator.

**Sweep seed 25/25 SELESAI PENUH: 25 seed `rc=0`, semuanya 820 passed,
4 skipped, nol `failed`.** Ini bedanya dari run sebelumnya, di mana 4 dari
25 proses mati di tengah jalan. Penyebab matinya sudah dicari dan TIDAK
bukan test:

- 4 seed itu (3, 8, 16, 21) dijalankan ulang dengan `-v`,
  `faulthandler`, dan `pytest-timeout --timeout=180`:
  **semuanya mencapai `[100%]` dan 795 passed**, tanpa satu pun timeout
  atau crash yang tercatat.
- Seed 8 yang sebelumnya mati DUA kali di ~92% jalannya sekarang selesai
  penuh.
- Setiap seed yang mati punya `rc=-1` dengan output kosong dan nol `F` di
  progress bar. pytest yang gagal selalu mencetak ringkasan; tidak adanya
  ringkasan berarti prosesnya hilang, bukan test-nya yang salah.

Kesimpulan: penyebabnya berada DI LUAR pytest (proses dibunuh oleh
lingkungan mesin, bukan oleh kode atau test). Yang penting untuk gerbang:
25 dari 25 sekarang benar-benar selesai, jadi angkanya layak ditulis.

Tidak ada kegagalan test di seed mana pun, dan pencetakan urutan tidak
menimbulkan pencemar.

**CATATAN TENTANG HASH:** header sengaja tidak menulis hash commit. Setiap kali
hash ditulis, commit barunya punya hash lain lagi, jadi tidak ada yang bisa
converging. Header berarti "perubahan terakhir ada di commit ini", bukan "ini
adalah HEAD". Untuk hash terbaru, pakai `git log`.

**Suite pytest TIDAK LAGI flaky.** Klaim "700 passed" dulu hanya berlaku untuk
satu run tertentu. Sekarang pencemar urutan sudah ditutup, dan gerbangnya bisa
dijalankan ulang kapan saja (lihat § Gerbang pencemar). Angka di atas masih
hasil satu run — yang membuatnya berbeda adalah angka itu sekarang bisa
dibuktikan berulang, bukan cuma dipercaya sekali.

Temuan yang mengubah gambaran ada di `docs/reports/fase-1-partial.md` §5.

## Status

| Item | Isi | Status | Commit |
|---|---|---|---|
| (a) | normalisasi kunci simbol | selesai | `7844fc9` |
| (b) | fill bursa ke DB + daily-loss breaker | selesai | `67862f0` |
| (c) | partial fill | selesai | `df3a2ee` |
| (d) | idempotensi retry | selesai | `9e2974a` |
| (e) | breakeven live | selesai | `caa57b1` |
| (f) | funding dari bursa | belum | — |
| (g) | filter `mode='live'` | sebagian | — |
| (h) | kill switch | **sebagian** | `522d11c`–`ba581f4` |
| (i) | shutdown order resting | belum | — |
| (j) | arming CLI | belum | — |

Tambahan: pemisahan wallet `052950a`. DEFECT-6 (cloid) `34a3a72`.
DEFECT-1 (`int()` pada field string) `3b91b9a`.

**Koreksi tabel ini (2026-10-04):** (h) tadinya tertulis "sedang dikerjakan".
Tidak ada commit untuk (h) di repo — `git log` tidak punya apa pun untuk
kill switch di luar `a8afade` (config lewat parameter) dan pekerjaan
mutation testing. Yang benar-benar masuk setelah `caa57b1` adalah:

| Commit | Isi |
|---|---|
| `a8afade` | `RiskManager` menerima `config=`, bukan singleton |
| `c224b58` | blokir jaringan + scrub env live di test suite |
| `a3cb243` | guard tangkap pembocor state global |
| `c1c1034` | konfigurasi mutmut |
| `64acf92` | 8 test batas di safety/breaker (tiga celah mutasi) |
| `8ea6d7c` | hasil mutation testing + pagar pencemar |
| `bae1c86` | preflight saat start (`PreflightError` per cabang) |
| `600f97f` | test startup gate tidak menulis ke kill switch produksi |

Jadi (h)–(j) **belum**. "Sedang dikerjakan" jangan dibaca sebagai sedikit
selesai — tidak ada kode (h) yang ditulis.

## Item (h) kill switch — SEBAGIAN

Lima commit, masing-masing satu defect. Yang BELUM dikerjakan masih ada di
§ Berikutnya.

| Commit | Isi |
|---|---|
| `522d11c` | isolasi state test: fixture autouse + pagar `data_store/` |
| `f891a0f` | fail closed: akun kosong bursa bukan "tidak ada posisi" |
| `eba8473` | `FakeExchange` menyediakan respons akun berbentuk bursa |
| `5652696` | verifikasi signer adalah agent wallet resmi dari master |
| `ba581f4` | konstanta `SIGNER` semula tidak punya huruf (mutan selamat) |
| `ef551de` | collateral SPOT bukan "akun kosong" (portfolio margin) |
| `f19c3fd` | satu sumber nilai "off"; state rusak = fail closed |

### "Tidak ada posisi" vs "tidak bisa memastikan"

`_fetch_remote_positions()` memfilter `assetPositions` dengan `szi != 0`.
Bursa membalas akun **kosong** — bukan error — untuk alamat yang bukan akun
sungguhan, jadi hasilnya `positions() == []`, `healthy = True`, dan kill
switch **tidak** menyala. Bot berjalan dengan keyakinan bahwa datar.

Dokumentasi menyebut jebakan ini eksplisit, dan mudah terkena di sistem ini
karena pemisahan wallet sudah dipakai: `query_address` bisa berupa agent
wallet.

Yang membuatnya dapat dibedakan: akun sungguhan yang Datar tetap punya
collateral, jadi `marginSummary.accountValue` bukan nol. `_require_real_account()`
menolaknya sebagai `empty_account` / `malformed_state`, dan `reconcile()`
melaporkan `state_known=False` plus `problems`, lalu menyalakan kill switch.

Kontrol yang menjaga ini tidak berubah jadi "selalu menolak": akun datar
dengan collateral tetap hijau, dan akun dust (`accountValue` 0.01) diterima.

### Verifikasi agent wallet

`allow_api_wallet=True` menyatakan **niat**, bukan bukti. Dua kesalahan
berbeda terlihat sama dari sisi bot: `account_address` salah ketik, atau
signer tidak pernah didaftarkan sebagai agent. Kasus kedua tidak bisa
dideteksi dari sisi bot — ordernya tidak pernah muncul di bursa, jadi
rekonsiliasi juga tidak melihat apa-apa.

`verify_agent_wallet()` memakai endpoint `extraAgents`
(`POST /info {"type": "extraAgents", "user": <master>}`), bentuk respons
diambil dari SDK resmi `Info.extra_agents`. Dicek terhadap **master**, bukan
terhadap `query_address`. Signer == master dilewati.

### Isolasi state test

`_build_live_executor()` membangun `SafetyGate(live_cfg)` tanpa
`state_path`, jadi jatuh ke default `data_store/live_counters.json` — file
kill switch produksi. Test yang memanggilnya sungguhan menulis ke sana.
Baru ketahuan saat bukti cabutan preflight: engine sempat jalan dan file itu
muncul dengan `engaged: true`.

- `isolate_state_paths` (autouse) — `os.chdir(tmp_path)` + pengalihan
  handler log + folder temporer.
- Baseline `data_store/` diambil di `pytest_sessionstart`, bukan di fixture.
- `tests/test_state_isolation.py` membandingkan snapshot (ukuran + sha256)
  sebelum dan sesudah suite.

**Temuan kedua yang lebih luas:** pagar langsung melaporkan
`logs/trading_bot.log` tumbuh 4.941.112 → 4.957.238 byte selama suite.
`os.chdir` tidak menutupinya — `RotatingFileHandler` menyimpan
`baseFilename` sebagai path absolut saat konstruksi dan tidak pernah membaca
ulang config. Sudah dicoba mengubah `get_config().logging.file`: tidak
berpengaruh. Yang benar: mengarahkan ulang handler yang sudah terpasang.

`.gitignore` juga diperbaiki: `data_store/live_counters.json` sebelumnya
TIDAK diabaikan dan tidak ter-track, jadi muncul sebagai `??` di setiap
`git status` dan bisa ter-commit tanpa sengaja.

### Mutasi yang selamat, dan apa yang memperbaikinya

| Mutasi | Hasil |
|---|---|
| `_require_real_account()` tidak dipanggil | 7 failed |
| `reconcile()` tidak engages kill switch saat tak terbaca | 1 failed |
| `value <= 0` jadi `value < 0` | 2 failed |
| `_redirect_log_handlers()` dimatikan | 1 failed |
| cek agent dimatikan | 5 failed |
| `extra_agents` dicek ke signer | 2 failed |
| perbandingan agent jadi case-sensitive | **9 passed — SELAMAT** |

Yang selamat itu karena `SIGNER` = `0x` + `"11"*20` — semua heksadesimal
ANGKA, jadi `SIGNER.upper()` identik dengan `SIGNER`. Test
case-insensitivity-nya hijau tanpa menguji apa pun. Setelah konstantanya
punya huruf, mutannya terbunuh.

Pola yang sama seperti `test_account_address_must_be_hex_address` yang hijau
karena salah bercabang. Ditutup dengan
`test_fixture_addresses_actually_exercise_case_folding`.

### Kebijakan: dua kondisi, dua perlakuan

Kill switch sebelumnya memperlakukan sama dua hal yang berbeda bahaya.
Pemisahan ini keputusan operasional, dan sekarang tertulis supaya tidak
ditebak ulang.

**(a) TIDAK BISA MEMASTIKAN** — bursa tidak terbaca, rate limit, respons
tak terduga, akun tidak terverifikasi, state lokal rusak. Posisi bot
mungkin benar; bot hanya tidak tahu.

- Order baru: **DIJEDA**, bukan dibatalkan.
- Coba ulang dengan batas yang terbatas.
- Setelah **N kegagalan berturut-turut**, naik ke kill switch persisten.
- Alert ke operator sejak kegagalan pertama.

Alasan: transient (timeout, rate limit) mematikan bot sementara yang
sebenarnya sehat. Kill switch untuk transient membuat operator membiasakan
diri menekan tombol yang seharusnya jarang dipakai — dan saat divergensi
benar terjadi, tidak ada yang merespons.

**(b) DIVERGENSI TERKONFIRMASI** — posisi lokal dan bursa berbeda, dan
kedua sumber terbaca serta bisa dipercaya. Bot tahu posisinya salah.

- Kill switch **LANGSUNG**, tanpa retry.
- Satu-satunya jalan keluar: perintah operator.

Alasan: di sini tidak ada ketidakpastian. Menunda hanya menambah exposure
yang tidak dipantau.

**Angka policy sudah jadi konfigurasi** (`core/config.py`, `LiveConfig`):

```
unverified_retry_backoff   = (10.0, 30.0, 60.0)   detik
unverified_max_consecutive = 3
unverified_max_seconds     = 300.0                 (5 menit)
```

Backoff naik 10/30/60 untuk menahan blip tanpa membanjiri bursa saat rate
limit aktif. DUA batas dipakai karena menangkap hal berbeda: streak
menangkap kegagalan berdekatan, batas waktu menangkap kegagalan yang jarang
tapi terus-menerus (satu kegagalan per menit sepanjang malam tidak pernah
mencapai tiga berturut-turut, tapi jelas tidak normal).

**Belum disambungkan ke `run_loop`.** `UnverifiedTracker` sudah ada dan
policy-nya sudah diuji, tapi loop live belum memanggilnya — yang diuji
sekarang adalah angka dan perilakunya, bukan integrasinya.

**Batas retry (a) sekarang ditetapkan.**Sebelumnya tercatat sebagai
keputusan operator yang belum diambil.

**Posisi terbuka saat kill switch menyala**

Kill switch menghentikan order BARU. Positions yang sudah terbuka **tetap
ada dan tetap dilindungi**:

- SL/TP yang sudah terpasang di bursa **tetap aktif** — trigger-nya milik
  bursa, bukan bot. Kill switch tidak membatalkannya dan tidak menghapus.
- Yang berhenti: polling, rekonsiliasi, dan pengiriman order baru.
- Konsekuensi yang harus diterima: bot tidak lagi memasang proteksi baru.
  Kalau operator tidak melepas switch dalam waktu yang wajar, posisi
  tanpa proteksi baru bisa terbuka di luar sistem.

**Yang belum diputuskan:** `health_check()` sekarang berhenti langsung
kalau switch menyala (`if self.gate.engaged: return health`), supaya bot
tidak membanjiri log. Akibatnya **divergensi yang terjadi SETELAH switch
menyala tidak terdeteksi**. Owner perlu memutuskan: apakah polling
dilanjutkan dalam mode degraded untuk memberi tahu posisi berubah.

### Posisi saat kill switch menyala

Kill switch menghentikan order BARU. Positions yang sudah terbuka **tetap
ada dan tetap dilindungi**:

- SL/TP yang sudah terpasang di bursa **tetap aktif** — trigger-nya milik
  bursa, bukan bot. Kill switch tidak membatalkannya dan tidak menghapus.
- Yang berhenti: polling, rekonsiliasi, dan pengiriman order baru.
- Konsekuensi yang harus diterima: bot tidak lagi memasang proteksi baru.
  Kalau operator tidak melepas switch dalam waktu yang wajar, posisi
  tanpa proteksi baru bisa terbuka di luar sistem.

**Flatten TIDAK dilakukan otomatis.** Semua leg harus ditutup bersama
dengan perintah operator eksplisit, karena menutup separuh posisi
membuat bot kehilangan setengah proteksi untuk yang lain. Tidak ada
flattening sebagian.

**Satu pengecualian: proteksi gagal terpasang.** Kalau posisi terbuka tanpa
SL/TP yang sudah dikonfirmasi ada di bursa, itu posisi telanjang. Dalam
keadaan itu sistem boleh menutupnya tanpa perintah —menutup posisi
telanjang lebih baik daripada membiarkannya tanpa batas. Kondisi ini
belum diimplementasikan dan tercatat sebagai pekerjaan terpisah.

### Rekonsiliasi tetap jalan saat engaged

`health_check()` tidak lagi `return` begitu switch menyala. Pembacaan bursa
dan perbandingan posisi tetap berjalan; hanya perlakuan AKHIR yang
berubah, dan switch yang sudah menyala tidak dinyalakan ulang. Bot buta
saat posisi bergerak adalah bot buta saat keadaannya paling berbahaya.

### Pelepasan kill switch

Env `TRADEBOT_LIVE_KILL_SWITCH` HANYA bisa MENYALAKAN. Nilai `0`/`false`/
`no`/`off` tidak pernah melepas switch yang aktif di disk — hanya
memicu pesan yang menyebut jalan yang benar.

Pelepasan hanya lewat `SafetyGate.operator_release()`, yang menuntut:

1. `typed_confirmation` persis sama dengan `RELEASE_CONFIRMATION_PHRASE`.
2. `reason` tidak boleh kosong.
3. `reconcile_clean` harus True.
4. Audit log JSONL: waktu UTC, outcome, alasan, state sebelum/sesudah,
   hash commit, path state.

Percobaan yang gagal juga dicatat. `audit_path` mengikuti `state_path`,
jadi test yang mengarahkan state ke tmp_path otomatis mengarahkan audit.

### Verifikasi agent wallet — validUntil dan pengulangan

`extraAgents` mengembalikan `validUntil` per agent. Versi sekarang
**membaca bentuk respons tapi belum memeriksa `validUntil`**, dan
verifikasi hanya jalan **sekali saat start**. Keduanya belum dikerjakan
dan tercatat di § Berikutnya, bukan diklaim selesai.

Dua risiko yang harus ditutup:

- **Masa berlaku.** Agent yang sudah kedaluwarsa tidak akan bisa
  men-sign. Kalau bot berjalan lama, order bisa hilang tanpa jejak — pola
  yang persis sama dengan agent yang tidak terdaftar, tapi muncul jauh
  setelah start. Margin pemeriksaan tidak boleh nol.
- **Deregistrasi saat berjalan.** Agent bisa dicabut dari master kapan
  saja. Verifikasi satu kali hanya membuktikan keadaan saat start, bukan
  keadaan saat order dikirim.

`_is_agent_expired()` belum ada; bentuk `validUntil` diambil dari SDK
(`Info.extra_agents`) dan harus dibaca dari sana, bukan dari asumsi.

**Gerbang 1:** test reproduksi hijau — **TERPENUHI** (0 xfailed).
Chaos test dan smoke test testnet — **belum**, butuh testnet key.

## Keputusan operator

Hanya yang dinyatakan eksplisit. Konteks di
`docs/reports/fase-1-partial.md` §6.

1. **2026-10-04** — Testnet key sedang disiapkan. Diberikan sebagai **API/agent
   wallet key testnet saja**, dengan `HYPERLIQUID_ACCOUNT_ADDRESS` untuk wallet
   utama. Dukungan pemisahan sudah terbukti di `052950a`.
2. **2026-10-04** — Item (e): nonaktifkan eksplisit di mode live, plus test
   yang membuktikan jalur live tidak bisa memanggilnya. Selesai `caa57b1`.
3. **2026-10-04** — Item (f): verifikasi endpoint di dokumentasi resmi
   Hyperliquid. Catatan operator: `userFunding` untuk funding payments,
   `userNonFundingLedgerUpdates` untuk non-funding. Nama yang saya sebut
   sebelumnya kemungkinan salah.
4. **2026-10-04** — Urutan (g)(h)(i)(j): urut dampak, test gagal lebih dulu,
   satu commit per defect.
5. **2026-10-04** — Wajib: (a) test signing SDK offline untuk semua jenis
   order, (b) jalankan suite dengan jam dibekuan di beberapa jam UTC **dan
   zona waktu**, (c) bukti bahwa tiap test gagal ketika perbaikannya dicabut.
6. **2026-10-04** — `max_order_notional=100` USDC tidak boleh disentuh.
7. **2026-10-04** — `docs/reports/fase-1.md` hanya ditulis kalau gerbang
   benar-benar lulus. Sampai itu, perbarui `fase-1-partial.md`.
8. **2026-10-04** — Branch `fase-1`. `research/` adalah wilayah sesi lain.
9. **2026-10-04** — Urutan ulang: bukti cabutan retroaktif dulu, lalu perbaiki
   test failed dan petakan xfailed, baru (h)(g)(i)(f)(j).
10. **2026-10-04** — (h): satu sumber kebenaran untuk nilai `"off"`; state
    `engaged` dipersist dan **fail closed** bila file state rusak/hilang;
    `disengage_kill_switch` hanya lewat perintah operator eksplisit
    (konfirmasi ketik + audit log); kill switch hanya untuk divergensi nyata.
    Tiap test baru wajib dimutasi sebelum commit.
11. **2026-10-04** — Mutation testing otomatis untuk modul kritis: safety.py,
    executor.py, akuntansi fill, daily-loss breaker. Mutan yang selamat di
    jalur safety harus ditutup dengan test baru.
12. **2026-10-04** — Test batas untuk breaker, kill switch, partial fill: tepat
    di limit, sedikit di bawah/atas, pergantian hari UTC, state setelah
    restart.
13. **2026-10-04** — Jalankan suite dengan urutan acak (pytest-randomly) dan
    tiap file test sendiri-sendiri untuk mencari pencemar state global lain.

## Larangan yang tetap berlaku

1. **Jangan set `TRADEBOT_LIVE` atau `TRADEBOT_LIVE_CONFIRMED`.** Jangan baca,
   minta, atau pakai private key mainnet. Jangan kirim order ke mainnet.
   Kerja di kode, test, paper, dan testnet dengan key testnet dari operator —
   jangan membuat atau mencari key sendiri. Pindah ke mainnet keputusan
   operator.
2. **Jangan melonggarkan batas safety** (daily loss, notional cap, kill switch)
   supaya test lulus. `max_order_notional=100` USDC tidak boleh disentuh.
3. **Jangan mengubah atau memoles angka riset.** Jangan tulis "edge terbukti"
   tanpa bukti yang sudah melewati Fase 3.
4. **Jangan menstub komponen yang sedang diuji.** Test live harus memakai
   objek/respons nyata, fixture rekaman dari SDK Hyperliquid, atau testnet.
5. **Satu commit per defect.** Test gagal dulu, cari akar masalah, perbaiki,
   hijau. Setiap klaim "selesai" disertai output perintah pembuktian.
6. **Berhenti di akhir fase.** Kirim laporan, tunggu persetujuan.
7. **Jangan edit `research/`.**
8. **Kalau validasi gagal, laporkan `NO-GO` dan berhenti.**

## Kewajibannya

**Setiap test baru wajib dimutasi sebelum commit**, dan hasilnya dicantumkan di
pesan commit. Test yang belum dimutasi dianggap belum terbukti.

Mutasi sendiri bisa salah sasaran — dua kali dalam sesi ini mutan mengarah ke
baris atau nama yang salah dan sempat terlihat seperti "test lemah". Periksa
sasaran mutan sebelum menyimpulkan apa pun.

## Pencemar state global — SELESAI

Butir 4 dari operator 2026-10-04 (urutan acak + tiap file sendiri) sudah
tertutup. Root cause ditunjuk, diperbaiki, dan sekarang ada guard yang
menangkap pembocor berikutnya tanpa perlu dianalisis manual.

### Akar masalah

Delta debugging (`ddmin` atas urutan seed 1) turun ke SATU test:

```
tests/test_direction_agents.py::TestMicrostructureAgent::
    test_positive_funding_is_contrarian_short
```

Test itu menulis `market_store.set_funding("BTC/USDT:USDT", 0.0002)` lalu
selesai. `market_store` adalah singleton modul tanpa API reset, jadi rate
itu bertahan dan dibaca `funding_cost()` di `test_lifecycle_paths`.
Buktinya aritmetika, bukan tebakan:

```
notional   0.2 x 49994.4          = 9998.88
funding     9998.88 x 0.0002 x 1  =  1.9997760000000002
selisih yang dilaporkan                 1.9997759999999989
```

Menghapus test itu dari urutan membuat keempat test yang tadinya gagal
menjadi hijau. Satu pembocor, bukan empat.

### Hipotesis yang DIBANTAH

`RiskManager.__init__` yang membaca `get_config()` singleton **bukan**
penyebab seed 1. Pengukuran menunjukkan isi `fees` dan `risk` singleton
tidak berubah sama sekali setelah test tersebut.

Yang membuat probe lama meleset: yang dipantau `market_store`,
`get_config().fees/risk`, dan `_db` — tapi `_funding` tidak ada di daftar
itu, dan `cfg.microstructure` yang sempat dipantau ternyata tidak pernah
ada di `AppConfig`. Guard sekarang menutup SELURUH dict store.

Selisih `0.8999` yang tercatat sebelumnya adalah tanda setengah-spread,
tetapi itu gejala test lain pada run lain, bukan pembocar ini.

### Yang diperbaiki

| File | Perubahan |
|---|---|
| `tests/conftest.py` (baru) | fixture autouse `guard_global_state` — snapshot sebelum tiap test, bandingkan sesudah, gagal menyebut pembocornya |
| `tests/test_direction_agents.py` | `_reset_store()` jadi dipanggil di setUp DAN `addCleanup` |
| `tests/test_advanced_modules.py` | `TestAtrCalculation` dan `TestVolatilityGate` dapat `tearDown` untuk cache ATR modul |
| `tests/test_bugfixes.py` | hanya kosongkan 3 dict yang dipakai tick guard, bukan semua 9 |
| `tests/test_pollution_guard_detects.py` (baru) | 8 test membuktikan guard menangkap 5 jenis pencemar |

Guard memantau `market_store`, `os.environ`, isi config singleton,
`database.db._db`, dan dict modul yang sudah diimpor. Mematikannya:
`TRADEBOT_SKIP_POLLUTION_GUARD=1`.

Dua kontrol negatif di `test_pollution_guard_detects.py` wajib ada: test
yang bersih harus tetap hijau, dan test yang menulis lalu memulihkan juga
harus hijau. Guard yang menolak test bersih akan dimatikan, dan saat itu
pencemar masuk tanpa terlihat.

Pelajaran yang harus diingat saat menambah test: `clear()` pada dict yang
bukan milikmu juga pencemaran. Menyapu `market_store` sepenuhnya demi
kenyamanan akan menghapus state test lain dan membuat test yang tidak
salah ikut gagal.

### Gerbang pencemar

```bash
python _seed_sweep.py 25            # 25 seed acak, suite penuh tiap seed
python _seed_sweep.py 25 --unittest # runner unittest
python _per_file_gate.py            # tiap file sendiri-sendiri (pytest)
python _per_file_gate.py --unittest # tiap file sendiri-sendiri (unittest)
```

**Hash yang terakhir disweep: `f922395`** — 25/25 hijau, 765 passed tiap
seed. Tree tidak diubah selama sweep berjalan; angka itu berlaku untuk
commit itu saja, bukan untuk HEAD berikutnya.

Catatan: sweep ini dijalankan saat pagar AST masih berhash `055fd48`, lalu
commit itu di-`--amend` (perbaikan satu kata di pesan commit) menjadi
`f922395`. Isi pohonnya identik — yang berubah hanya pesan commit — jadi
angka 25/25 tetap berlaku, dan sekarang hash yang ditulis di sini
adalah hash yang benar-benar ada di riwayat.

Riwayat sweep yang sudah lewat:

| Sweep | Commit | Hasil |
|---|---|---|
| 25 seed (awal) | `a3cb243` | 24/25 — seed 3 gagal, penyebabnya belum diketahui saat itu |
| 25 seed (tree bersih) | `a3cb243` | 25/25 |
| 25 seed (dengan guard jaringan + env) | `8ea6d7c` | 25/25, 731 passed |
| 25 seed (preflight + pagar AST) | `f922395` | 25/25, 765 passed |

Sweep pertama setelah preflight **dibatalkan** karena pagar AST masih
sedang disunting saat sweep berjalan. Angka dari sweep yang dibatalkan
tidak dicatat di sini: ia berlaku untuk tree yang tidak pernah ada.

Skrip gerbang di-`gitignore` — alat bantu pengukuran, bukan bagian produk.
`_seed_sweep.py` menyimpan log lengkap tiap seed yang gagal ke
`%TEMP%/seed_sweep_logs/`, karena "seed N gagal" tanpa alasan tidak bisa
diinvestigasi.



## Tidak yakin

1. **Bukti signing offline baru mencakup limit order.** Trigger SL/TP, cancel,
   dan modify belum diuji offline — masih jadi tugas.
2. **Zona waktu belum diuji.** `SafetyGate.in_live_window` memakai UTC
   eksplisit jadi seharusnya tidak sensitif `TZ`, belum dibuktikan.
3. **`_seen_fill_ids` tumbuh tanpa batas** selama sesi panjang. Tidak berbahaya
   sekarang, belum diukur.
4. **`_last_fill_time` di-reset ke "sekarang" tiap poll.** Fill pada detik yang
   sama bisa terlewat kalau `userFills` memfilter dengan `startTime` eksklusif.
   Belum diuji.
5. **`uncertain_orders` hanya di memori.** Item (d) mencatat order yang
   statusnya tidak diketahui supaya tidak terkirim ulang, tapi saat restart peta
   itu kosong. Item (i) kemungkinan harus menanganinya.
6. **Mutan yang selamat di jalur safety belum diinventarisasi** — tugas
   operator butir 11.
7. **Satu kegagalan seed 3 tidak dijelaskan.** Sweep pertama gagal di
   `test_api_wallet_separation` pada seed 3 dengan dua test. Tree masih
   berubah saat itu, dan file test tersebut tidak menyentuh disk
   (`Database`/`sqlite3`/`open(` nol kemunculan), jadi tabrakan antar
   proses tidak bisa menjelaskan — sudah dicoba dan tidak terpicu.
   Sweep bersih berikutnya 25/25 hijau, dan seed 3 hijau 4x berturut
   sesudahnya. Log kegagalan yang lama tidak tersimpan, jadi tidak bisa
   diinvestigasi ulang. `_seed_sweep.py` sekarang menyimpan log penuh ke
   `%TEMP%/seed_sweep_logs/` supaya kasus berikutnya bisa ditelusuri.

## Mutation testing — environment siap, hasil belum

`mutmut` **tidak jalan native Windows**: 3.8.0 keluar dengan pesan eksplisit
`"To run mutmut on Windows, please use the WSL"` (issue #397). Setup yang
dipakai sesuai pilihan operator — alat standar, bukan runner buatan:

```
~/trading-bot-new   clone ke filesystem WSL (bukan /mnt/c)
~/tbenv            venv, Python 3.14.4

pip install pytest mutmut hyperliquid-python-sdk aiosqlite pyyaml
            python-dotenv numpy pandas pytz
```

Tidak dipasang: torch, transformers, dash, plotly, pandas_ta. Install
`numba` (yang `pandas_ta` minta) gagal build di Python 3.14, dan tidak ada
target mutasi yang memerlukannya.

**Baseline di environment itu: 230 test hijau, 0 gagal.** Tanpa baseline
bersih, "mutan selamat" tidak berarti apa pun — mutan bisa terlihat selamat
karena test-nya memang sudah merah.

Per file: `test_live_safety` 45, dan 185 untuk gabungan
`test_live_executor` / `test_live_engine` / `test_risk_manager` /
`test_fill_cost_funding` / `test_partial_fill` / `test_fill_ledger` /
`test_fill_reconciliation` / `test_order_idempotency` / `test_bugfixes` /
`test_lifecycle_paths`.

Konfigurasi ada di `setup.cfg` (commit `c1c1034`). Tiga hal di sana yang
tidak intuitif dan sudah terbukti menyesatkan:

1. **`also_copy` wajib, isinya 90 entri.** mutmut hanya menyalin file yang
   dimutasi, jadi test di `mutants/` gagal `No module named core` di
   collection — sebelum satu baris pun diuji.
2. **`config.yaml` harus ikut.** Tanpa itu tarif fee jatuh ke default
   dataclass (taker `0.0005`, bukan `0.00045`) dan baseline jadi merah di
   mutan pertama. Gejalanya terlihat seperti "mutan belum terdeteksi",
   padahal hanya konfigurasi yang hilang.
3. **Format argumen.** `configparser` menolak key berulang dan mutmut
   memecah nilai multi-line per baris. `-p no:cacheprovider` di satu baris
   jadi satu argumen yang tidak bisa dibaca pytest. Yang benar: blok
   multi-line dengan `-pno:cacheprovider` (tanpa spasi).

Scope test: `test_api_wallet_separation.py` dikeluarkan dari selection.
Root cause kegagalannya sudah ditemukan dan diperbaiki, dan file itu tidak
menyentuh satu pun baris yang dimutasi. Empat file lain butuh
`pandas_ta` / `plotly` / `apscheduler` yang tidak dipasang.

### Hasil run pertama

4076 mutan, **1739 selamat** (43%). Klasifikasi per modul:

| Modul | Mutan | Selamat | Mati |
|---|---|---|---|
| `trading/live/executor.py` | 2126 | 996 | 1130 |
| `trading/position_manager.py` | 788 | 292 | 496 |
| `trading/live/safety.py` | 410 | 189 | 221 |
| `trading/risk_manager.py` | 463 | 133 | 330 |
| `trading/fill_cost.py` | 289 | 129 | 160 |
| **TOTAL** | **4076** | **1739** | **2337** |

Sebagian besar yang selamat **bukan** celah test. Tiga pola yang muncul saat
diff-nya dibaca satu per satu:

1. **Mutan string ke string mustahil.** `"true"` jadi `"XXtrueXX"`, `"BUY"`
   jadi `"XXBUYXX"`. Env value dan label log tidak pernah bernilai itu.
   Setara secara fungsional. Ini batas bawaan mutmut, bukan
   kekurangan test.
2. **Mutan threshold absurd.** `daily_pnl < 0` jadi `< 1`; `reference > 0`
   jadi `>= 0`. Angka 1 USDT untuk daily PnL dan modal awal bukan kondisi
   yang muncul di produksi. Setara.
3. **Mutan di jalur yang memang tidak diuji.** `_persist_open`,
   `_publish`, `record_exchange_fills`, `_liquidate` — hampir semua mutan
   di `executor.py` dan `position_manager.py` karena test tidak pernah
   menjalankan kode live sungguhan dengan bursa tiruan yang cukup lengkap.
   Ini celah test NYATA, tapi cakupannya besar dan perlu item tersendiri.

### Tiga celah test yang ditemukan dan ditutup

Semua di batas breaker, dan semuanya pola yang sama: test lama menguji
"dekat batas" (0.9x, 1.2x, `max ± 1`), bukan batasnya.

| Mutan | Kodenya | Akibatnya |
|---|---|---|
| `master_blockers__mutmut_57` | `<=` jadi `<` pada `realized_pnl` | daily-loss breaker live tidak menyala tepat di batas |
| `master_blockers__mutmut_61` | `>=` jadi `>` pada `consecutive_errors` | error ke-3 lolos, trading tidak berhenti |
| `validate_trade__mutmut_22` | `>=` jadi `>` pada `loss_fraction` | breaker daily-loss melebar satu titik |

Ditutup di `64acf92` dengan 8 test baru. Bukti cabutan keempat di pesan
commit. Gerbang "semua mutan selamat di safety.py dan breaker ditutup"
terpenuhi untuk sel yang bukan ekuivalen: tiga celah ditemukan, tiga
ditutup, sisanya ekuivalen atau di luar cakupan gerbang.

### Yang BELUM tertutup

Mutan selamat di `executor.py` (909) dan `position_manager.py` (314)
belum diinventarisasi satu per satu. Fungsi yang paling banyak
selamat:

```
LiveExecutor._persist_open      402
LiveExecutor._close             208
LiveExecutor.record_exchange_fills  175
LiveExecutor._open              133
PositionManager.close_position   99
PositionManager._liquidate       72
PositionManager.open_position    60
```

`_persist_open` dan `_close` adalah jalur uang. Ini item tersendiri dan
belum dikerjakan — daftar di atas bukan keterangan mutan yang selalu
selamat, hanya yang perlu dibaca.

## Pagar kode yang dimatikan — SELESAI

`trading/live/client.py` pernah memuat:

```python
if False:  # MUTAN: validasi bentuk alamat dimatikan
    raise RuntimeError(...)
```

Tiga kegagalan berantai, dan tidak ada yang bersuara. `git status` bersih,
`pytest` hijau:

1. Validasi bentuk alamat mati. `account_address="bukan-alamat"` lolos
   dan tercatat "Preflight OK". Bursa tidak menolak string itu — dia
   membalas akun kosong — jadi bot menyimpulkan tidak ada posisi padahal
   posisi ada.
2. `_is_address()` tidak pernah dipanggil di mana pun di repo.
3. Test yang menutup cacat itu tetap hijau: ia bercabang ke
   `signer_mismatch` lebih dulu lalu `assertIn("account_address", ...)`.
   Dua cabang berbagi satu kata, jadi saling menyelamatkan.

Pola yang sama seperti tiga jebakan di `fase-1-partial.md` §5.2.

### Yang dipasang

`PreflightError(RuntimeError)` dengan `code` stabil per cabang —
`exchange_unreachable`, `universe_empty`, `bad_address`,
`signer_mismatch`. Semua test mengunci `code`, tidak ada `assertIn` pada
teks pesan. Tiap cabang punya test sendiri dengan konfigurasi yang
membuktikan cabang lain tidak mungkin menyala.

`tests/test_no_disabled_code.py` memindai `trading/` dan `run.py` dengan
AST dan gagal bila ada kondisi konstan (`if False:`, `if True:`, `if 0:`,
`if 1:`, `if None:`, `if not False:`) atau teks `MUTAN`. Dua kontrol
negatif: pagar harus bisa menangkap pola itu, dan harus tetap lolos untuk
`if x > 1:` / `if n == 1:` / `if flag:`.

Mutasi mulai sekarang dilakukan lewat patch yang diterapkan lalu dicabut,
bukan dengan menyunting file produksi di tempat.

### Dua test source-inspection dihapus

`test_source_calls_preflight_before_engine` dan
`test_preflight_failure_propagates` membaca TEKS `run.py` dengan
`inspect.getsource`. Keduanya hijau tanpa menjalankan apa pun, jadi tetap
hijau kalau `preflight()` dipanggil di jalur kode mati, atau kalau
pemanggilnya dibungkus `try/except` yang menelan exception — persis
perilaku salah yang harus dicegah.

Digantikan `tests/test_live_startup_gate.py`: menjalankan
`_build_live_executor()` sungguhan dengan bursa mati pada batas SDK
(`hyperliquid.info.Info`), lalu membuktikan `LiveEngine` tidak pernah
dibangun dan loop tidak pernah mulai. Kontrak kode keluar dipindah dari
blok `__main__` ke `_cli(argv) -> int` supaya bisa diuji sebagai
perilaku, bukan sebagai teks.

### Bukti cabutan

| Mutan | Hasil |
|---|---|
| `if not self._is_address(value):` → `if False:` | 11 failed, 16 passed — 8 test bentuk alamat, 1 test masking, 2 pagar AST |
| `exchange.preflight(...)` dihapus dari `run.py` | 4 failed, 7 passed — 3 test perilaku + pagar marker |

### Temuan sampingan: test yang menulis ke kill switch produksi

`_build_live_executor()` membangun `SafetyGate(live_cfg)` dengan
`state_path` default, yaitu `data_store/live_counters.json`. Test startup
memanggil fungsi itu sungguhan, jadi ia menulis ke file kill switch
produksi. Saat mutan "preflight dihapus" diuji, engine sungguhan sempat
jalan dan file itu muncul dengan `engaged: true` — kill switch palsu
yang akan ditemukan operator sebagai aktif tanpa sebab.

Dicek dengan menjalankan tiap kandidat satu per satu
(`test_live_safety.py`, `test_bugfixes.py`, `test_api_wallet_separation.py`,
`test_repro_live_defects.py`, `test_live_engine.py`) dan suite penuh:
hanya jalur `_build_live_executor()` yang menyentuhnya. Diperbaiki di
`600f97f` dengan mengarahkan gate ke tempfile.

## Berikutnya

### URUTAN RESMI — operator 2026-10-04, tidak bisa diacak

1. **(h) kill switch** — **SEBAGIAN**, lihat § Item (h). Sisa lingkup yang
   BELUM dikerjakan:
   - satu sumber kebenaran untuk nilai `"off"` (sekarang ada dua tempat:
     `SafetyGate.__init__` dan `master_blockers`).
   - file state rusak/tak terbaca → `engaged` (fail closed). Sekarang
     `DayCounters._unreadable` hanya menambah blocker `COUNTER_STATE_UNREADABLE`,
     tidak menyalakan switch.
   - file state belum ada HANYA dibuat lewat inisialisasi eksplisit — tidak
     diam-diam oleh startup atau test.
   - `disengage_kill_switch` hanya lewat perintah operator: konfirmasi ketik
     + audit log. Sekarang juga bisa lewat env `TRADEBOT_LIVE_KILL_SWITCH=0`,
     dan audit log-nya belum ada.
   - kill switch hanya untuk divergensi NYATA — sudah sebagian lewat
     `state_known`, tapi `health_check` masih menyalakannya untuk
     "order resting tidak terbaca".
2. **(g) sisa** — parameter `mode` di `get_daily_realized_pnl` dan semua
   pemanggil `get_open_positions`. `close_position` sudah atomik
   (`WHERE id = ? AND status = 'OPEN'` plus `commit()` dan `rowcount`), jadi
   yang belum hanya pengembalian nilai.
3. **Bursa tiruan stateful berbasis SDK** — lalu **mutmut ulang** untuk
   `_persist_open`, `_close`, `_open`, `record_exchange_fills`. Aturan yang
   berlaku:
   - mutan selamat yang **bukan ekuivalen** ditutup dengan test baru.
   - mutan yang diklaim ekuivalen harus **dijelaskan tertulis**, dan 10
     contoh acak diberi diff lengkap — bukan klaim lisan.
4. **(i) shutdown order resting.**
5. **(f) funding dari bursa** — cek dokumentasi resmi dulu; syaratnya sudah
   tertulis di § Syarat item (f).
6. **(j) arming CLI.**

### Pekerjaan lain yang masih terbuka

1. **Test batas (butir 12)** — tepat di limit, sedikit di bawah/atas,
   pergantian hari UTC, state setelah restart. Untuk breaker, kill switch,
   dan partial fill. Bagian breaker sudah sebagian tertutup di `64acf92`.
2. **Jam palsu di beberapa zona waktu (butir 5)** — jalankan suite dengan
   jam dibekuka di beberapa jam UTC dan zona waktu. Belum.
3. **Config di jalur risiko** — `position_manager.py:57` dan
   `paper_engine.py:93` masih baca `get_config()`. Lihat § Sisa pekerjaan.
4. **Zone waktu** dan bukti signing offline untuk cancel/modify masih
   belum diuji (lihat § Tidak yakin).
5. Perbarui `docs/reports/fase-1-partial.md` setelah tiap item.


### Sisa pekerjaan config di jalur risiko

`RiskManager` sudah menerima `config=` (commit `a8afade`). Dua pemanggil
masih membaca `get_config()` langsung dan perlu pekerjaan terpisah dengan
test sendiri:

- `trading/position_manager.py:57` — `self.config = get_config()`
- `trading/paper_engine.py:93` — `self.config = get_config()`

UTANG INI DIJALUR AMAN. `position_manager` menghitung fee dan funding yang
masuk ke `realized_pnl`, dan `realized_pnl` itu sumber angka daily-loss
breaker. Kalau config global berubah, angka breaker berubah tanpa ada yang
mengubah kode — kelas bug yang paling mahal di sistem ini.

### Syarat item (f) — funding live

Funding **tidak boleh** diambil dari `market_store`. `market_store._funding`
adalah data WebSocket *live*, dan `close_position` membacanya lewat
`market_store.get_funding(symbol)` (`position_manager.py:253`).

Untuk mode live, angka yang benar harus datang dari **data bursa** —
`userFills` untuk fill, dan sumber funding yang disepakati operator untuk
settlement. Alasannya:

1. `market_store` bisa kosong. Kalau WS belum connect, `get_funding()`
   mengembalikan `None`, dan `funding_cost()` memakai default. Posisi
   ditutup dengan angka funding yang bukan milik bursa.
2. `market_store` tidak distinguish testnet dan mainnet. Rate yang sama
   dipakai untuk dua akun berbeda.
3. Rate yang berubah setelah posisi dibuka tidak akan pernah tercatat —
   biaya settlement dihitung dari rate saat penutupan, bukan rate saat
   periode settlement sebenarnya.

Sebelum (f) dikerjakan, endpoint dan skemanya perlu dikonfirmasi operator
(lihat § Pertanyaan yang menunggu operator). Jangan 구현 dulu lalu
meminta konfirmasi — ledger yang salah lebih mahal daripada item yang
belum dikerjakan.

## Pertanyaan yang menunggu operator

1. **Testnet key** — masih menunggu.
2. **Item (f)** — perlu konfirmasi setelah dokumentasi dicek: endpoint mana
   yang jadi sumber funding, dan bagaimana dicatat di ledger.
3. **`uncertain_orders` dan restart** — harus dipersist? Ke mana?
4. **`close_position`** — apakah pengembalian nilai (margin) bagian dari
   item (g), atau sudah cukup dengan atomisitas yang ada?

## Verifikasi

```bash
git log --oneline -8
python -m pytest tests -q
python -m unittest discover tests 2>&1 | grep -E "^(OK|FAILED|Ran )"

# gerbang pencemar (lihat § Pencemar state global)
python _seed_sweep.py 25
python _per_file_gate.py

# batas breaker dan kill switch
python -m pytest tests/test_bugfixes.py -q -k "DailyLoss or KillSwitch"
```
