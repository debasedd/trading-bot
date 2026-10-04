# STATE — sumber status lintas sesi

**Satu-satunya sumber status program.** Baca sebelum bekerja. Perbarui di
akhir setiap sesi dan sebelum laporan fase.

Terakhir: 2026-10-04 · Branch `fase-1` · HEAD `19bae9a`

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

1. Mutation testing otomatis (butir 11); tutup mutan yang selamat di jalur
   safety dengan test baru.
2. Test batas breaker, kill switch, partial fill (butir 12).
3. Suite dengan urutan acak, plus tiap file sendiri-sendiri (butir 13).
4. Jam palsu di beberapa zona waktu (butir 5).
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
