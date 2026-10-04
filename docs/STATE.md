# STATE — sumber status lintas sesi

**Satu-satunya sumber status program.** Baca sebelum bekerja. Perbarui di
akhir setiap sesi dan sebelum laporan fase.

Terakhir: 2026-10-04 · Branch `fase-1`

---

## Ringkasan

Fase 0 **LULUS**. Fase 1 **BELUM LULUS** — 6 dari 10 item selesai, dua gerbang
terakhir belum bisa dijalankan karena butuh testnet key.

```
pytest:   713 passed, 4 skipped          (0 failed, 0 xfailed)
unittest: Ran 717 tests — OK
```

Tidak ada test failed maupun xfailed. Semua perbaikan Fase 1 sudah punya bukti
cabutan. Dua gerbang yang tersisa — chaos test dan smoke test testnet — butuh
testnet key dari operator.

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
| (h) | kill switch | **sedang dikerjakan** | — |
| (i) | shutdown order resting | belum | — |
| (j) | arming CLI | belum | — |

Tambahan: pemisahan wallet `052950a`. DEFECT-6 (cloid) `34a3a72`.
DEFECT-1 (`int()` pada field string) `3b91b9a`.

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

Delta debugging (`ddmin` atas urutan seed 1)Cit down ke SATU test:

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

## Berikutnya

Pencemar urutan acak sudah beres. Urutan berikutnya mengikuti urutan yang
operator tetapkan di 2026-10-04.

1. **Mutation testing otomatis (butir 11)** — `mutmut` untuk `safety.py`,
   `executor.py`, akuntansi fill, dan breaker. Sudah tidak tertahan lagi:
   suite-nya sekarang bisa dipercaya, jadi mutan yang "selamat" memang
   berarti tidak terdeteksi. Mutan yang selamat di jalur safety ditutup
   dengan test baru.
2. **Test batas (butir 12)** — tepat di limit, sedikit di bawah/atas,
   pergantian hari UTC, state setelah restart. Untuk breaker, kill switch,
   dan partial fill.
3. **Jam palsu di beberapa zona waktu (butir 5)** — jalankan suite dengan
   jam dibekukan di beberapa jam UTC dan zona waktu.
4. **(h) kill switch** sesuai lingkup operator butir 10.
5. **(g) sisa:** parameter `mode` di `get_daily_realized_pnl` dan semua
   pemanggil `get_open_positions`. Catatan: `close_position` ternyata
   **sudah** atomik (`WHERE id = ? AND status = 'OPEN'` plus `commit()` dan
   `rowcount`), jadi yang belum ada hanya pengembalian nilai.
6. **(i) shutdown**, **(f) funding** — cek dokumentasi resmi lebih dulu,
   **(j) arming CLI**.
7. Perbarui `docs/reports/fase-1-partial.md` setelah tiap item.

### Sisa pekerjaan config di jalur risiko

`RiskManager` sudah menerima `config=` (commit `a8afade`). Dua pemanggil
masih membaca `get_config()` langsung dan perlu pekerjaan terpisah dengan
test sendiri:

- `trading/position_manager.py:57` — `self.config = get_config()`
- `trading/paper_engine.py:93` — `self.config = get_config()`

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
