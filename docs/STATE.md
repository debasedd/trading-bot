# STATE — sumber status lintas sesi

**Satu-satunya sumber status program.** Baca sebelum bekerja. Perbarui di
akhir setiap sesi dan sebelum laporan fase.

Terakhir: 2026-10-04 · Branch `fase-1` · HEAD `838587c` (lihat catatan di bawah)

---

## Ringkasan

Fase 0 **LULUS**. Fase 1 **BELUM LULUS** — 6 dari 10 item selesai, dua gerbang
terakhir belum bisa dijalankan karena butuh testnet key.

```
pytest:   700 passed, 4 skipped          (0 failed, 0 xfailed)
unittest: Ran 704 tests — OK
```

Tidak ada test failed maupun xfailed. Semua perbaikan Fase 1 sudah punya bukti
cabutan. Dua gerbang yang tersisa — chaos test dan smoke test testnet — butuh
testnet key dari operator.

**CATATAN TENTANG HASH DI HEADER:** header menulis hash commit yang memuat
file ini, jadi setiap kali hash diperbarui, commit barunya punya hash lain
lagi. Ini tidak bisa converging. Header itu berarti "perubahan terakhir ada
di commit ini", bukan "ini adalah HEAD". Untuk hash terbaru, pakai `git log`.

**PERINGATAN: suite pytest FLAKY.** Angka di atas adalah hasil run yang
bersih, bukan jaminan. Enam run berturut-turut menghasilkan 700 passed, lalu
10 failed, lalu 700 passed lagi — tanpa ada perubahan kode di antaranya.
Test yang gagal berbeda-beda antar run. Jadi "700 passed" tidak boleh
dibaca sebagai bukti suite benar-benar hijau; itu bukti bahwa *pada run
tertentu* tidak ada yang gagal. Lihat bagian pencemar di bawah.

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

## Penjemar state global — belum tuntas

**Status butir 1-5 dari operator 2026-10-04: hanya butir 1 selesai.**

| Butir | Isi | Status |
|---|---|---|
| 1 | Pangkas STATE.md, commit sebelum (h) | **selesai** |
| 2 | Mutation testing otomatis (mutmut) | belum — tertahan butir 4 |
| 3 | Test batas breaker / kill switch / partial fill | belum |
| 4 | Urutan acak + tiap file sendiri-sendiri | **sebagian** |
| 5 | Jam palsu di beberapa zona waktu | belum |

`mutmut` dan `pytest-randomly` sudah terpasang (`mutmut-3.8.0`,
`pytest-randomly-5.0.0`). Mutation testing **sengaja belum dijalankan**:
memutasi suite yang masih punya pencemar state global menghasilkan
hitungan yang tidak bisa dipercaya — mutan yang "selamat" bisa saja
hilang karena test lain ikut memalsukan hasil, bukan karena mutannya
tidak terdeteksi.

### Yang sudah diketahui

Setiap file test dijalankan sendiri-sendiri: 40+ file, 0 gagal.

Urutan acak **menemukan pencemar**. Seed 1, empat test gagal — kesemuia
hijau pada urutan default:

    FAILED tests/test_position_manager.py::TestPositionManager::test_liquidation_trigger
    FAILED tests/test_lifecycle_paths.py::TestOpeningFeeNotDoubleCharged::test_flat_trade_loses_exactly_two_fees_plus_spread
    FAILED tests/test_lifecycle_paths.py::TestFeeAccounting::test_realized_pnl_is_net_of_both_fees
    FAILED tests/test_lifecycle_paths.py::TestRaceAndDoubleClaim::test_concurrent_close_returns_margin_once

Seed 2 dan 3 hijau. Seed 1 reproduktif.

### Suite pytest flaky — ditemukan saat serah-terima

Enam run berturut-turut dengan kode yang tidak berubah:

    run 1: 1 failed, 699 passed
    run 2: 700 passed
    run 3: 700 passed
    run 4: 700 passed

Lalu run kelima: 10 failed. Run berikutnya: 700 passed.

Test yang gagal BERBEDA-BEDA setiap run. Yang tertangkap dalam satu rangkaian
enam run:

    test_paper_engine.py::test_execute_open_long
    test_paper_engine.py::test_execute_open_and_close
    test_live_executor.py::TestOpenOrder::test_open_without_price_refused
    test_fill_price_sl.py::test_sl_and_tp_derived_from_fill_price
    test_fill_price_sl.py::test_short_precomputed_stops_are_ignored
    test_fill_price_sl.py::test_risk_distance_matches_announced_pct
    test_fill_price_sl.py::test_long_precomputed_stops_are_ignored

Pola nama testnya menunjuk satu arah: `test_paper_engine`,
`test_fill_price_sl`, `test_live_executor`. Semuanya menghitung harga
eksekusi dari `market_store`. Kegagalan datang dari data global yang
berganti-ganti isinya, bukan dari logika test yang salah.

`unittest` tidak menunjukkan gejala yang sama pada run yang sama, jadi ini
tertangkap `pytest` saja.

**Konsekuensi:** tidak boleh ada klaim "suite hijau" tanpa menyebutkan
run-nya. Angka 700 passed adalah hasil satu run, bukan properti suite.

### Akar masalah — belum ditunjuk

Selisih angkanya persis **0.8999**, yang merupakan tanda setengah-spread:

    AssertionError: -502.9246724 != -502.0247732 within 7 places
                   (0.899899199999993 difference)

Hipotesis kerja: `RiskManager.__init__` (trading/risk_manager.py:56-57)
mengambil `get_config().risk` dan `get_config().fees`, sementara
`get_config()` (core/config.py:1029) adalah **singleton** yang meng-cache
`_config` di level modul. Test mana pun yang mengubah config global akan
mengotori setiap `RiskManager` yang dibuat sesudahnya — termasuk yang
dipakai test akuntansi fee dan likuidasi.

### Hipotesis yang sudah dibantah

Dua kandidat dicurigai karena posisinya di urutan seed 1. Keduanya **salah**:

    tests/test_neural_net_layout.py + tests/test_position_manager.py -> 32 passed
    tests/test_fill_cost_funding.py + tests/test_position_manager.py -> 23 passed

Diverifikasi juga bahwa tidak ada test yang memanggil `reload_config()`
atau menulis `_config` secara langsung, dan `cfg.microstructure` tidak
pernah ada di `AppConfig` -- probe yang memantau state sempat salah
memantau atribut yang tidak pernah ada.

Plugin probe yang menulis repr state global sebelum/sesudah tiap test
tidak mendeteksi perubahan apa pun. Jadi pencemarnya berada di state yang
belum terpantau, bukan di `market_store`, `get_config().fees`,
`get_config().risk`, atau `get_config().scalping`.

Belum ditunjuk test yang mencuri. Test yang gagal itu sendiri TIDAK gagal
saat dijalankan sendiri-sendiri atau berpasangan dengan kandidat yang
dicurigai — jadi ini murni masalah urutan, bukan test yang salah secara
individual.

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

Urutan diubah: pencemar urutan acak harus beres dulu.

1. **Tunjuk test yang mencuri state global** pada seed 1. Plugin probe
   yang memantau `market_store`, `get_config().fees/risk/scalping`, dan
   `_db` tidak menemukan apa pun -- state yang mencuri belum terpantau.
   Dugaan sementara: `RiskManager` membaca `get_config()` yang
   singleton, tapi sumber pencemar belum terbukti.
2. **Baru setelah itu** mutation testing otomatis (butir 11). Mutan yang
   "selamat" di suite yang masih tercemar tidak bisa dipercaya.
3. Test batas breaker, kill switch, partial fill (butir 12).
4. Jam palsu di beberapa zona waktu (butir 5).
5. (h) kill switch sesuai lingkup operator butir 10.
6. (g) sisa: parameter `mode` di `get_daily_realized_pnl` dan semua pemanggil
   `get_open_positions`. Catatan: `close_position` ternyata **sudah** atomik
   (`WHERE id = ? AND status = 'OPEN'` plus `commit()` dan `rowcount`), jadi
   yang belum ada hanya pengembalian nilai.
7. (i) shutdown, (f) funding — cek dokumentasi resmi lebih dulu, (j) arming
   CLI.
8. Perbarui `docs/reports/fase-1-partial.md` setelah tiap item.
5. (h) kill switch sesuai lingkup operator butir 10.
6. (g) sisa: parameter `mode` di `get_daily_realized_pnl` dan semua pemanggil
   `get_open_positions`. Catatan: `close_position` ternyata **sudah** atomik
   (`WHERE id = ? AND status = 'OPEN'` plus `commit()` dan `rowcount`), jadi
   yang belum ada hanya pengembalian nilai.
7. (i) shutdown, (f) funding — cek dokumentasi resmi lebih dulu, (j) arming
   CLI.
8. Perbarui `docs/reports/fase-1-partial.md` setelah tiap item.

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

# tiap file sendiri-sendiri (menangkap pencemar state global)
for f in tests/test_*.py; do python -m pytest "$f" -q 2>&1 | tail -1; done

# urutan acak
python -m pytest tests -q -p randomly -p no:cacheprovider

# batas breaker dan kill switch
python -m pytest tests/test_bugfixes.py -q -k "DailyLoss or KillSwitch"
```
