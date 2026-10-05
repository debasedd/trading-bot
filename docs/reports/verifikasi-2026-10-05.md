# VERIFIKASI (h) — 2026-10-05 (revisi 2)

Output MENTAH. Tidak diringkas, tidak diedit.

Revisi ini menggantikan butir verifikasi sebelumnya dan menambah item
1-8 yang diminta setelahnya. Bagian 1-7 dari revisi pertama tetap di
bawah sebagai riwayat.

---

## SELISIH DENGAN LAPORAN SEBELUMNYA

**1. Angka suite: 888 → 914 → 959.** Akumulasi pekerjaan sesi ini.

**2. Nama berkas laporan.** `docs/reports/verifikasi-2026-10-05.md`.
Diperiksa byte-per-byte terhadap spesifikasi `verifikasi-<tanggal>.md`:

```
'verifikasi-2026-10-05.md' ['0x76','0x65','0x72','0x69','0x66','0x69',
 '0x6b','0x61','0x73','0x69']
```

v-e-r-i-f-i-k-a-s-i. **Tidak ada salah eja.** Tidak ada perbaikan yang
diperlukan, dan sengaja tidak ada: mengganti ejaan yang benar hanya
membuat berkas lama yatim.

**3. Seed tidak lagi diketik manual.** `_seed_pick.py` menulis seed ke
`docs/reports/seed-bukti-cabutan.txt`; `_seed_use.py` membacanya dari
berkas.

**4. `disengage_kill_switch()` sekarang DIHAPUS**, bukan hanya terbukti
tidak terpanggil. Sebelumnya masih ada sebagai metode yang tidak
dipanggil — yaitu jalur pelepasan kedua yang masih terbuka.

**5. Pagar hash `.db`/`.json` sekarang hash ISI**, bukan ukuran+mtime.

**6. `health_check()` saat engaged: sudah ada sebelumnya, tidak diubah.**

---

## a. REPO

```
$ git status --porcelain
(kosong)
```

```
$ git log --oneline -15
d859882 Skrip seed (buat+baca) dan pagar hash isi data_store
e639c27 Single-instance lock: satu proses bot per akun
3f8a6ff Kill switch selalu dibaca dari disk; disengage_kill_switch dihapus
f42ffa0 Bursa tiruan stateful + test perilaku jalur uang (open/persist/close/fills)
0c8974e Seed sweep 25/25 rc=0 (914 passed) + laporan verifikasi final
637657d STATE.md: status (h) per 2026-10-05 dan angka suite 914
3713aaa Laporan verifikasi (h) 2026-10-05: output mentah, selisih klaim lama
afdd46f gitignore: sandbox mutmut dan skrip bantu
3a610d5 Perbaiki karakter rusak di produksi dan test
fedacd9 run.py --release-kill-switch: perintah operator jadi nyata
0d5f605 validUntil: pagar kewajaran + TESTNET_CHECKLIST
b2dc0a8 UnverifiedTracker disambungkan ke run_loop
9e329a2 STATE.md: angka suite dan gerbang akhir di commit (h)
d4ede06 STATE.md: posisi saat engaged, pelepasan kill switch, dan catatan seed mati
02e8860 Bukti cabutan: tutup mutan yang selamat setelah "env tidak bisa melepas"
```

```
$ Get-ChildItem docs\reports
Name                     Length
----                     ------
fase-0.md                 25212
fase-1-partial.md         11495
seed-bukti-cabutan.txt      250
verifikasi-2026-10-05.md  26142
```

---

## b. MUTAN DAN KODE MATI DI PRODUKSI

```
$ git grep -n -E "MUTAN|if False|if True|if 0:|if 1:" -- trading run.py
grep_rc=1 (1 = kosong)
```

Tidak ada output. **Kosong.**

---

## c. SAFETY YANG TERPASANG

### `UnverifiedTracker`

```
$ git grep -n "UnverifiedTracker" -- trading run.py
run.py:1139:    from trading.live.safety import SafetyGate, UnverifiedTracker
run.py:1154:    engine.unverified = UnverifiedTracker(cfg=engine.cfg)
trading/live/engine.py:37:    UnverifiedTracker,
trading/live/engine.py:369:    unverified: UnverifiedTracker = field(default_factory=UnverifiedTracker)
trading/live/safety.py:52:class UnverifiedTracker:
```

**Dipanggil dari jalur produksi**: `LiveEngine.run_loop` (engine.py:1584,
1610) -> `_note_unverified`/`_note_verified` -> `self.unverified`.
Plus `run.py:1154` di perintah pelepasan operator.

### `operator_release`

```
$ git grep -n "operator_release" -- trading run.py
run.py:1169:def _operator_release() -> int:
run.py:1271:        released = gate.operator_release(
run.py:1370:        return _operator_release()
trading/live/safety.py:412:    def operator_release(self, reason: str, typed_confirmation: str,
```

**Dipanggil dari jalur produksi**: `_cli(["--release-kill-switch"])`
(run.py:1370) -> `_operator_release()` (run.py:1169) ->
`gate.operator_release()` (run.py:1271).

### `release-kill-switch`

```
$ git grep -n "release-kill-switch" -- run.py
run.py:1171:    `python run.py --release-kill-switch` — jalan melepas switch yang nyata.
run.py:1318:    `--release-kill-switch` membaca dan menulis file state yang sama
run.py:1360:    # Perintah sekali: `python run.py --release-kill-switch`. Jalur ini
run.py:1363:    if "--release-kill-switch" in argv:
```

**Dipanggil dari jalur produksi**: `_cli()` run.py:1363.

### `disengage_kill_switch` — TIDAK ADA PEMANGGIL

```
$ git grep -n "disengage_kill_switch" -- trading run.py
trading/live/safety.py:438:        `disengage_kill_switch()` pernah ada dan sudah DIHAPUS. Ia tidak
grep_rc=0
```

Satu-satunya hasil adalah sebutan di docstring yang menjelaskan
methodenya sudah dihapus. **Tidak ada pemanggil.** Metodenya sendiri juga
sudah tidak ada (`test_method_no_longer_exists`).

### `_redirect_log_handlers` — TIDAK ADA DI PRODUKSI

```
$ git grep -n "_redirect_log_handlers" -- trading run.py
grep_rc=1
```

**Kosong.** Hanya ada di `tests/conftest.py` sebagai fixture.

### health_check() saat kill switch engaged

```
$ git grep -n "already_engaged" -- trading run.py
trading/live/engine.py:1372:        already_engaged = self.gate.engaged
trading/live/engine.py:1373:        if already_engaged:
trading/live/engine.py:1481:        if not health["ok"] and not already_engaged:
```

**Dipanggil dari jalur produksi**: `run_loop` (engine.py:1578) ->
`health_check()` (engine.py:1324). `already_engaged` mencegah kill switch
dinyalakan ulang, TETAPI reconcil dan laporan divergensi tetap berjalan —
itulah gunanya baris 1372-1373. Tidak ada short-circuit.

### Verifikasi ulang agent wallet berkala

```
$ git grep -n "reverify_agent_if_due" -- trading run.py
trading/live/client.py:621:    def reverify_agent_if_due(self) -> bool:
trading/live/engine.py:1608:                    self.exchange.reverify_agent_if_due()
```

**Dipanggil dari jalur produksi**: `run_loop` (engine.py:1608), setiap
putaran. Kegagalan lewat `_note_unverified` (engine.py:1610).

### Single-instance lock

```
$ git grep -n "SingleInstanceLock\|_acquire_instance_lock" -- trading run.py
run.py:1093:        lock = _acquire_instance_lock()
run.py:1288:def _acquire_instance_lock():
run.py:1305:    lock = SingleInstanceLock(account)
run.py:1329:    path = SingleInstanceLock(account).path
trading/live/single_instance.py:134:class SingleInstanceLock:
```

**Dipanggil dari jalur produksi**: `main()` (run.py:1093) untuk start bot,
dan `_release_command_refuses_while_bot_running()` (run.py:1329) untuk
menolak `--release-kill-switch` saat bot hidup.

---

## d. POLUSI STATE

Pagar hash SHA-256 **isi** semua berkas `.db` dan `.json` di `data_store`
(`-shm` dan `-wal` diabaikan: artefak SQLite yang berubah karena proses
lain membuka database, bukan karena test).

```
$ python _state_fence.py before fence_before.json
fence SEBELAH ditulis: 270 berkas

$ # suite penuh, dua bagian terpisah
567 passed, 1 warning, 62 subtests passed in 26.62s
392 passed, 4 skipped, 3 warnings, 28 subtests passed in 20.47s

$ python _state_fence.py after fence_before.json
fence SESUDAH: 270 berkas
BARU    : 0 []
HILANG : 0 []
BERUBAH: 0 []
FENCE_RC=0
```

**Identik.** 270 berkas sebelum, 270 setelah, nol perubahan isi.

### Alasan test yang di-skip

```
SKIPPED [1] tests\test_live_tui.py:372: jalur POSIX tidak berlaku di Windows
SKIPPED [1] tests\test_live_tui.py:389: jalur POSIX tidak berlaku di Windows
SKIPPED [1] tests\test_live_tui.py:378: jalur POSIX tidak berlaku di Windows
SKIPPED [1] tests\test_live_tui.py:385: jalur POSIX tidak berlaku di Windows
```

---

## e. ENVIRONMENT

```
TRADEBOT_LIVE = tidak diset
TRADEBOT_LIVE_CONFIRMED = tidak diset
HYPERLIQUID_* lain: tidak ada
```

Tidak ada variabel live yang diset.

---

## f. KARAKTER RUSAK

Pindai 254 file ber-git (py, md, cfg, txt, yaml, yml, toml) untuk
`\u3400-\u9fff`, `\uac00-\ud7af`, `\ufffd`, plus surrogate dan private use.

```
file diperiksa: 254
temuan: 22
```

Semua 22 ada di dua tempat yang SENGAJA tidak disentuh:

| Lokasi | Jumlah | Alasan |
|---|---|---|
| `research/` | 10 | `research/` di luar batas tugas |
| `data_store/_snap*`, `constellation_*.py`, `count_crossings.py`, `critiques.txt` | 12 | snapshot dan skrip investigasi lama, bukan jalur produksi |

Tidak ada temuan di `trading/`, `core/`, `run.py`, atau `tests/`.
Temuan produksi yang sebelumnya ada sudah diperbaiki di `3a610d5`.

---

## g. BUKTI CABUTAN ACAK — seed dari berkas

Seed **tidak diketik**. `_seed_pick.py` menulisnya ke
`docs/reports/seed-bukti-cabutan.txt`, `_pick_claim.py` membacanya dari
berkas dan memilih butir secara deterministik terhadap seed itu.

```
$ cat docs/reports/seed-bukti-cabutan.txt
# seed acak — dibuat oleh _seed_pick.py, BUKAN diketik manual
# dibuat: 2026-10-05T14:59:39.235535+00:00
# jumlah: 3

1	D59F88E9
2	EAB59582
3	D5F8DCB3
```

```
$ python _pick_claim.py
seed dari docs/reports/seed-bukti-cabutan.txt:
  D59F88E9 -> 8. single-instance lock: lock kedua ditolak; 1. validUntil: pagar kewajaran; 12. fill CLOSE menutup baris lokal
  EAB59582 -> 3. collateral spot total ATAU hold; 7. disengage_kill_switch tidak punya pemanggil; 1. validUntil: pagar kewajaran
  D5F8DCB3 -> 13. env tidak bisa melepas kill switch; 11. closing resting dilaporkan sukses; 7. disengage_kill_switch tidak punya pemanggil
```

Tiga item diambil dari seed `D59F88E9`.

### g.1 — single-instance lock, lock kedua ditolak

```
$ git commit -q -m "..."
$ # mutan: if pid is not None and _pid_alive(pid):  ->  if False:
$ python -m pytest tests/test_single_instance.py -q
5 failed, 10 passed, 1 warning in 1.97s

$ # cabut mutan
$ python -m pytest tests/test_single_instance.py -q
15 passed, 1 warning in 1.65s

$ git status --porcelain
(kosong)
```

**MATI.**

### g.2 — pagar kewajaran `validUntil`

```
$ git commit -q -m "..."
$ # mutan: if seconds > max_future:  ->  if False:
$ python -m pytest tests/test_agent_wallet_verification.py -q
2 failed, 25 passed in 0.95s

$ # cabut mutan
$ python -m pytest tests/test_agent_wallet_verification.py -q
27 passed in 0.22s

$ git status --porcelain
(kosong)
```

**MATI.**

### g.3 — `fill CLOSE` menutup baris lokal

```
$ git commit -q -m "..."
$ # mutan: if fill.get("kind") != "CLOSE": continue  ->  if == "__MUTAN_NEVER__": continue
$ python -m pytest tests/test_money_path_behaviour.py -q
FAILED tests/test_money_path_behaviour.py::TestRecordExchangeFills::test_open_fill_is_not_treated_as_a_close
1 failed, 24 passed in 0.34s

$ # cabut mutan
$ python -m pytest tests/test_money_path_behaviour.py -q
25 passed in 0.25s

$ git status --porcelain
(kosong)
```

**MATI — setelah diperbaiki.** Lihat di bawah.

### MUTAN YANG SELAMA INI SELAMAT, DAN APA YANG TERBUKA

Percobaan pertama pada g.3 **tidak membunuh mutan**: `19 passed` dengan
dan tanpa mutasi.

Akar masalahnya bukan test yang lemah, tapi **test yang hilang**.
Perbaikan `unittest.main()` yang yatim di awal sesi ini memotong ekor
berkas, dan enam test `TestRecordExchangeFills` ikut terhapus —
termasuk `test_open_fill_is_not_treated_as_a_close`, yang justru
sendiri yang harus menangkap mutan itu.

Yang berbahaya: `19 passed` terlihat hijau dan tidak ada yang
mempermasyukannya. Testsuite yang kehilangan test **tidak lebih
loud** daripada testsuite yang semuanya benar — keduanya hijau.

Perbaikan: enam test dipulihkan (`25 passed`), lalu mutan yang sama
dijalankan ulang dan MATI.

Pelajaran yang dicatat: mutasi terarah bukan sekadar alat bukti, tapi
juga **alat deteksi test yang hilang**. Kalau sebuah mutan selamat
padahal jelas harus dibunuh, pertanyaannya "kenapa tidak terbunuh?",
bukan "apakah memang setara".

---

## h. SEED SWEEP 25 SEED

Dua kali jalan pada sesi ini. Yang pertama **tidak sah** dan tidak
dilaporkan sebagai hijau.

### Jalankan pertama — TIDAK SAH

```
$ python _seed_sweep.py 25
...
seed 19    rc=1  3 failed, 956 passed, 4 skipped, ...
seed 20    rc=1  ...
seed 23    rc=1  1 failed, 964 passed, 4 skipped, ...
=== 22/25 hijau ===
GAGAL: seed 19, seed 20, seed 23
```

Tiga kegagalan itu **saya sendiri yang menyebabkannya**: selama sweep
berjalan, saya sedang menerapkan dan mencabut mutan di
`trading/live/single_instance.py` dan `trading/live/executor.py`.
Kegagalannya:

```
FAILED tests/test_no_disabled_code.py::TestNoMutationMarkersInProduction::test_no_mutation_markers
FAILED tests/test_symbol_normalization.py::TestExecutorAndEngineAgreeOnKey::test_no_runtime_code_string_concatenates_a_key
```

Jadi `test_no_mutation_markers` justru menangkap mutan yang sedang
diterapkan — pagar itu bekerja. Sweep ini tidak membuktikan apa pun
tentang determinisme test, jadi **tidak dipakai sebagai bukti**.

### Jalankan kedua — SAH

Dijalankan dengan `git status` bersih dan tanpa perubahan berkas
selama berjalan.

```
$ git status --porcelain
(kosong)

$ python _state_fence.py before fence2_before.json
fence SEBELAH ditulis: 270 berkas

$ python _seed_sweep.py 25
```

_(hasil mentah di bawah)_

---

## i. TABEL STATUS SEMUA BUTIR

### Sesi sebelumnya

| # | Butir | Status | Commit | Test |
|---|---|---|---|---|
| 1 | `UnverifiedTracker` disambungkan ke `run_loop` | SELESAI | `b2dc0a8` | 12 test |
| 2 | Perintah operator lepas kill switch | SELESAI | `fedacd9` | 9 test |
| 3 | Pagar kewajaran `validUntil` | SELESAI | `0d5f605` | 5 test |
| 4 | `docs/TESTNET_CHECKLIST.md` | SELESAI (7 asumsi tercatat) | `0d5f605` | — |
| 5 | Pemindaian karakter rusak | SELESAI | `3a610d5` | 69 test |
| 6 | `health_check()` tidak short-circuit | SELESAI (sebelum sesi ini) | `d4ede06` | — |
| 7 | Verifikasi ulang agent berkala | SELESAI (diperkuat) | `b2dc0a8` | 12 test |
| 8 | Seed sweep 25 seed | SELESAI | `0c8974e` | 25/25 |

### Sesi ini

| # | Butir | Status | Commit | Test |
|---|---|---|---|---|
| 1 | Bursa tiruan stateful + test jalur uang | SELESAI | `f42ffa0` | 25 test |
| 2 | Seed dibuat skrip, tidak diketik | SELESAI | `d859882` | 5 test |
| 3 | Pagar hash isi `.db`/`.json` | SELESAI | `d859882` | — |
| 4 | Single-instance lock (file+PID+basi) | SELESAI | `e639c27` | 15 test |
| 5 | `--release-kill-switch` menolak saat bot hidup | SELESAI | `e639c27` | (bagian 4) |
| 6 | State kill switch selalu dibaca dari disk | SELESAI | `3f8a6ff` | 5 test |
| 7 | `disengage_kill_switch()` dihapus + pagar | SELESAI | `3f8a6ff` | 2 test |
| 8 | Nama berkas laporan diperiksa | SELESAI (tidak ada salah eja) | — | byte-check |

### BELUM

| # | Butir | Status | Kenapa belum |
|---|---|---|---|
| A | **Skor mutasi jalur uang** | **BELUM ADA** | `mutmut` hanya jalan di WSL; setelah semua hambatan teratasi ia melaporkan `killed = 0` dari 2076 mutan, bertentangan dengan harness yang sudah dibuktikan bisa membunuh mutan. Verdict tidak dipercaya, jadi tidak dipublikasikan. Tercatat sebagai risiko terbuka di `docs/STATE.md`. |
| B | **Mutan kanari** sebelum tiap run mutmut | **BELUM** | Butuh harness yang verdict-nya bisa dipercaya dulu (butir A). |
| C | `cosmic-ray` sebagai pengganti | **BELUM** | Dicoba hanya kalau mutmut tetap gagal setelah kanari dipasang. |
| D | `docs/TESTNET_CHECKLIST.md` dijalankan | **BELUM** | Butuh testnet key dan kredensial operator. Tujuh asumsi masih hanya terbukti offline. |
| E | Pemindaian karakter rusak di `research/` | **BELUM** | 10 temuan. `research/` di luar batas tugas; dilaporkan di bagian f, tidak diperbaiki. |
| F | 12 temuan karakter rusak di `data_store/_snap*` dan skrip investigasi | **BELUM** | Snapshot lama, bukan jalur produksi. |

### Risiko terbuka yang tersisa

1. **Tidak ada mutation score untuk jalur uang.** Test perilaku
   menyempit celah (bursa tiruan berkeadian tiang, partial fill sungguhan,
   lock basi) tapi tidak menggantikannya. Yang tidak diketahui: berapa
   banyak cabang jalur uang yang salah tapi masih hijau.
2. **Batas single-instance lock hanya satu mesin.** `os.kill`/`OpenProcess`
   hanya tahu proses hidup atau tidak, TIDAK untuk siapa. Dua mesin
   berbeda atau proses lain yang memakai akun yang sama **tidak**
   dicegah.
3. **Tujuh asumsi bursa belum diverifikasi.** Termasuk satuan
   `validUntil` yang masih TEBAKAN.



Laporan sebelumnya menyatakan (h) selesai. Yang berikut tidak sesuai klaim itu.

**1. `UnverifiedTracker` TIDAK TERPASANG di produksi.** Hanya ada di
`tests/test_unverified_policy.py`. Angka 10/30/60 detik, 3 kegagalan, dan
300 detik hanya berlaku untuk test yang memanggil tracker secara langsung.
Test hijau bersamaan dengan policy yang tidak dijalankan. → Diperbaiki di
`b2dc0a8`.

**2. `operator_release()` tidak punya perintah.** API-nya ada dan keempat
syaratnya teruji, tapi tidak ada CLI yang memanggilnya. → Diperbaiki di
`fedacd9`.

**3. `disengage_kill_switch()` TIDAK TERPASANG** — dan memang tidak pernah
dipanggil. Tidak dilaporkan eksplisit sebelumnya. Masih begitu sekarang,
secara sengaja. Lihat bagian c.

**4. `_redirect_log_handlers()` TIDAK PERNAH ADA di produksi.** Hanya
fixture di `tests/conftest.py`. Instruksi verifikasi memintanya dihitung
sebagai "safety terpasang"; itu keliru. Lihat bagian c.

**5. Pagar kewajaran `validUntil` yang saya tulis pertama kali salah arah.**
Versi pertama juga menolak nilai di MASA LALU sebagai "tidak bisa menilai".
 Itu membuat agent yang benar-benar kedaluwarsa LOLOS. Test yang sedang saya
tulis menangkapnya sebelum commit. → Diperbaiki di `0d5f605`.

**6. Angka suite: 888 → 914.** Selisihnya accreted work (12 test wiring
+ 9 test CLI + 5 test validUntil), bukan test yang hilang.

**7. Seed sweep 25 seed SUDAH diulang** pada 2026-10-05 (bagian h):
25/25 `rc=0`, semuanya 914 passed. Angka lama `d4ede06` (888 passed)
dipakai hanya sebagai pembanding, bukan bukti kondisi sekarang.
Seed 3, 8, 16, dan 21 yang dulu mati sekarang semuanya hijau.

**8. Item 5 (mutmut) TIDAK SELESAI.** Bukan karena tidak dicoba — lihat
bagian 5. Angka mutmut yang bisa dikumpulkan bertentangan dengan harness
yang sudah saya buktikan bekerja, jadi tidak saya publikasikan sebagai
mutation score.

---

## 1. `UnverifiedTracker` — SUDAH, dan sekarang terpasang

**Status: SELESAI.** Commit `b2dc0a8`.

Kelas dipindahkan dari test ke produksi (`trading/live/safety.py:52`) dan
dihubungkan ke `run_loop`. Rantai produksi ada di bagian c.

### Test perilaku (`tests/test_unverified_wiring.py`, 12 test)

Bursa tiruan dipasang di batas SDK. Tracker yang diuji adalah kelas
produksi yang sama persis dengan yang diimpor `run_loop` — bukan stub.

| Test | Yang dijamin |
|---|---|
| `test_single_failure_pauses` | 1 gagal → jeda, kill switch BELUM |
| `test_two_failures_still_no_kill_switch` | 2 gagal → masih belum |
| `test_three_consecutive_engages` | 3 beruntun → engaged |
| `test_five_minutes_engages_without_three` | jarang tapi lama → engaged |
| `test_success_between_failures_resets_streak` | gagal,pulih,gagal = streak 1 |
| `test_recovery_clears_pause` | pemulihan → jeda hilang |
| `test_recovery_while_engaged_does_not_clear_kill_switch` | pulih TIDAK lepas switch |
| `test_backoff_follows_config` | 10/30/60 sesuai config |
| `test_backoff_never_returns_zero` | delay tidak pernah 0 |

### Bukti cabutan

```
$ python -m pytest tests/test_unverified_wiring.py -q
MUTAN: _note_unverified jadi no-op
FAILED tests/test_unverified_wiring.py::TestThreeFailuresEngageKillSwitch::test_five_minutes_engages_without_three
FAILED tests/test_unverified_wiring.py::TestThreeFailuresEngageKillSwitch::test_three_consecutive_engages
FAILED tests/test_unverified_wiring.py::TestThreeFailuresEngageKillSwitch::test_success_between_failures_resets_streak
FAILED tests/test_unverified_wiring.py::TestOneFailurePausesButDoesNotStop::test_single_failure_pauses
FAILED tests/test_unverified_wiring.py::TestOneFailurePausesButDoesNotStop::test_two_failures_still_no_kill_switch
FAILED tests/test_unverified_wiring.py::TestOneFailurePausesButDoesNotStop::test_backoff_follows_config
FAILED tests/test_unverified_wiring.py::TestRecoveryClearsPause::test_recovery_while_engaged_does_not_clear_kill_switch
FAILED tests/test_unverified_wiring.py::TestRecoveryClearsPause::test_recovery_clears_pause
8 failed, 4 passed

MUTAN 2: run_loop tidak memanggil tracker saat bursa tak terbaca
FAILED tests/test_unverified_wiring.py::TestTrackerIsActuallyUsedByProduction::test_run_loop_uses_backoff
1 failed, 11 passed

$ # setelah cabut
12 passed in 0.18s
```

Mutan kedua penting: helper-nya masih ada dan masih dipanggil test lain,
jadi tanpa test sambungan, mutan itu lolos.

---

## 2. STATUS EKPLISIT TIGA BUTIR YANG DITANYAKAN

### 2a. Perintah operator melepas kill switch

**Status: BELUM → SUDAH.** Commit `fedacd9`.

Sebelum: `SafetyGate.operator_release()` ada, keempat syaratnya teruji, tapi
tidak ada yang memanggilnya. Sekarang ada CLI.

```
$ python run.py --release-kill-switch        (tanpa state engaged)
  Kill switch TIDAK aktif. Tidak ada yang perlu dilepas.
  EXIT=0

$ python run.py --release-kill-switch        (state engaged:true, tanpa key)
  KILL SWITCH AKTIF. Melepasnya mengizinkan trading lagi.

  Melepas switch TIDAK memperbaiki apa pun. Kalau penyebabnya
  belum ditangani, switch menyala lagi — dan setiap kali bot
  restart, posisi mungkin sudah berbeda.

  Menjalankan rekonsiliasi SEGAR terhadap bursa...

  Gagal menghubungi bursa: private_key wajib diisi
  Kill switch TIDAK dilepas. Tidak bisa memastikan apa pun
  berarti tidak boleh melepas switch.
  EXIT=3
```

Empat syarat, semuanya diuji di `tests/test_operator_release_cli.py`:

| Syarat | Test | Keluar |
|---|---|---|
| konfirmasi ketik persis | `test_wrong_confirmation_refuses` | 3 |
| alasan wajib | `test_empty_reason_refuses` | 3 |
| rekonsiliasi segar bersih | `test_dirty_reconcile_refuses` | 3 |
| bursa bisa dibaca | `test_unreachable_exchange_refuses` | 3 |
| semuanya terpenuhi | `test_releases_and_writes_audit` | 0 |

Audit log diverifikasi isinya:

```python
for field in ("at_utc", "reason", "commit"):
    self.assertIn(field, rec)
self.assertEqual(rec["outcome"], "released")
self.assertEqual(rec["reason"], "sudah periksa divergensi")
self.assertTrue(rec["engaged_before"])
self.assertFalse(rec["engaged_after"])
```

**Celah yang ditemukan saat menulis perintah ini:** versi pertama memakai
`SafetyGate(LiveConfig(), env=os.environ)` tanpa `state_path`. Konstruktor
itu hanya membaca file state kalau `TRADEBOT_LIVE=1` — benar untuk paper
trading, tapi salah untuk perintah operator: operator tanpa env itu diberi
tahu "tidak ada yang perlu dilepas" padahal switch-nya nyala di disk.
Diperbaiki dengan meneruskan `state_path` eksplisit.

### 2b. `health_check()` tidak short-circuit saat engaged

**Status: SELESAI, tidak diubah sesi ini.** Sudah ada sebelum `d4ede06` dan
masih terpasang.

Dipanggil dari `run_loop` (engine.py:1580) setiap putaran. Divergensi tetap
dilaporkan ke `health["problems"]` meski `engaged` sudah True — itu yang
membuat operator bisa melihat posisi terbaru untuk memutuskan melepas atau
tidak.

### 2c. Verifikasi ulang agent wallet berkala saat bot berjalan

**Status: SUDAH, diperkuat sesi ini.**

`run_loop` memanggil `self.exchange.reverify_agent_if_due()` setiap putaran
(engine.py:1607). Kegagalan DI SINI lewat `_note_unverified` — jeda order
baru sejak kegagalan pertama, backoff, dan naik ke kill switch setelah
policy terlampaui. Sebelumnya lewat `gate.record_error()`, yang sekarang
sudah diganti.

---

## 3. `validUntil` — pagar kewajaran, dan koreksi yang saya sendiri buat

**Status: SELESAI.** Commit `0d5f605`.

Satuan `validUntil` TIDAK didokumentasikan; satuan ditebak dari besarannya
(`> 1e11` = milidetik). Hasil konversi sekarang harus di jendela masuk akal.
Di luar itu hasilnya `None` = "tidak bisa menilai", plus log error — bukan
lolos.

**Koreksi yang saya buat sendiri, belum commit:** versi pertama juga
menolak nilai di MASA LALU. Itu keliru:

```
$ python -c "print(LiveExchange._agent_valid_until_seconds(time.time()-86400))"
kemarin (detik): None
kemarin (ms)   : None

MASALAH: nilai masa lalu yang MASUK AKAL jadi None juga,
jadi agent kedaluwarsa sungguhan lolos.
```

`now - 86400` adalah kedaluwarsa SAHIH. Menolaknya sebagai "tidak bisa
menilai" adalah kebalikan dari tujuan pagar. Test `test_milliseconds_in_past_are_expired`
 awalnya menguji harapan yang salah dan ikut dikoreksi.

Jadi hanya batas ATAS yang dipakai. Batas bawah bukan "tidak masuk akal",
itu "sudah lewat" — dan penanganannya milik `verify_agent_wallet` yang
melempar `agent_expired`.

```
Bukti cabutan (pagar dimatikan):
FAILED tests/test_agent_wallet_verification.py::TestAgentExpiry::test_plausible_window_boundaries
FAILED tests/test_agent_wallet_verification.py::TestAgentExpiry::test_far_future_conversion_returns_none
2 failed, 25 passed
$ # setelah cabut
27 passed in 0.23s
```

**Satuan masih belum dikonfirmasi.** Tercatat sebagai T2 di
`docs/TESTNET_CHECKLIST.md`.

---

## 4. `docs/TESTNET_CHECKLIST.md`

**Status: SUDAH.** Commit `0d5f605`.

Tujuh asumsi yang hanya terbukti offline. Tiap butir punya: asumsi, asal
(dokumentasi / rekaman / TEBAKAN), kenapa berbahaya, cara verifikasi,
hasil yang diharapkan, dan apa yang diubah kalau ternyata berbeda.

| Butir | Asumsi | Asal |
|---|---|---|
| T1 | bentuk respons `extraAgents` | docstring SDK |
| T2 | satuan `validUntil` | **TEBAKAN, tanpa dokumentasi** |
| T3 | bentuk respons `spotClearinghouseState` | docstring SDK + rekaman |
| T4 | akun kosong untuk alamat salah | dokumentasi |
| T5 | penerimaan format `cloid` | kode SDK + regresi DEFECT-6 |
| T6 | partial fill | dokumentasi + fixture rekaman |
| T7 | event fill saat SL/TP terpicu | rekaman testnet 2026-10-03 |

Yang paling penting dicatat jujur: T2 berasal dari tebakan. Test offline
hanya membuktikan kode konsisten dengan tebakan itu.

Tidak ada satu pun butir yang sudah dijalankan — dokumen itu daftar kerja,
bukan laporan.

---

## 5. MUTMUT — BELUM SELESAI, dan alasannya

**Status: BELUM SELESAI.** Saya tidak punya mutation score yang layak
dipublikasikan. Berikut semua yang dicoba, dengan outputnya.

### Yang berhasil disiapkan

```
$ python -m mutmut --version
To run mutmut on Windows, please use the WSL. Native windows support is
tracked in issue https://github.com/boxed/mutmut/issues/397
```

mutmut hanya jalan di WSL. WSL di mesin ini perlu effort:

```
$ wsl -d Ubuntu -- python3 --version
Python 3.14.6
$ wsl -d Ubuntu -- python3 -m pip --version
/usr/bin/python3: No module named pip
$ wsl -d Ubuntu -- sudo -n apt-get install -y python3-venv
sudo: interactive authentication is required
```

Harus bootstrap pip sendiri, dan PEP 668 menolak:

```
$ python3 get-pip.py --user -q
error: externally-managed-environment
```

Akhirnya berhasil dengan `--break-system-packages`:

```
$ python -m mutmut --version
python -m mutmut, version 3.8.0
```

Baseline hijau di WSL dengan 18 file test target:

```
$ python3 -m pytest -q ... (18 file dari setup.cfg)
435 passed, 10 skipped, 46 subtests passed in 3.38s
```

### Hambatan nyata yang ditemukan

1. **Dua file test tidak bisa jalan di WSL.**
   `test_operator_release_cli.py` dan `test_live_startup_gate.py` meng-import
   `run.py`, yang menarik seluruh aplikasi termasuk `pandas_ta`. `pandas_ta`
   butuh `numba`, dan `numba` tidak bisa di-build di Python 3.14:
   ```
   RuntimeError: Cannot install on Python version 3.14.4; only versions >=3.10,<3.14 are supported.
   ERROR: Failed to build 'numba' when getting requirements to build wheel
   ```
   Keduanya dikeluarkan dari selection mutmut, dicatat di `setup.cfg`.
   Keduanya tetap hijau di Windows dan tetap menutup jalur CLI-nya.

2. **Proses background dibunuh saat sesi WSL berakhir.** Harus `setsid
   nohup ... < /dev/null & disown`.

3. **`/tmp` tidak persisten antar-stemparan WSL.** Log hilang. Diganti `~/mm.log`.

4. **Drive `/mnt/c` terlalu lambat.** 18 mutan dalam ~15 menit. Setelah
   repo disalin ke filesystem native WSL (`~/repo`): 17 mutan dalam 25 detik.

5. **`also_copy` kehilangan `executor.py`.** Begitu `source_paths` dipersempit
   ke `safety.py` saja, `executor.py` tidak ikut tersalin ke sandbox dan
   langkah stats gagal:
   ```
   ../tests/test_live_executor.py:10: in <module>
       from trading.live.executor import LiveExecutor, coin_of, make_cloid
   E   ModuleNotFoundError: No module named 'trading.live.executor'
   =========================== short test summary info ============================
   ERROR tests/test_live_executor.py
   failed to collect stats. runner returned 1
   ```
   Diperbaiki dengan mengembalikan `executor.py` ke `source_paths`.

### Kenapa hasilnya TIDAK saya publikasikan

Setelah semua hambatan teratasi, mutmut melaporkan:

```
$ grep -c killed ~/repo/_mm_results2.txt
0
```

**Nol mutan mati dari 2076 yang sudah dijalankan.** Itu tidak masuk akal
untuk kode safety — dan saya bisa membuktikannya langsung bahwa harness-nya
memang bisa membunuh mutan:

```
$ cd ~/repo/mutants && python3 -m pytest -q tests/test_unverified_wiring.py
............                            [100%]
12 passed in 0.17s

$ sed -i "s/self.consecutive += 1/self.consecutive += 0/" trading/live/safety.py
$ grep -n "consecutive +=" trading/live/safety.py
152:        self.consecutive += 0
160:        self.consecutive += 0

$ python3 -m pytest -q tests/test_unverified_wiring.py
FAILED tests/test_unverified_wiring.py::TestRecoveryClearsPause::test_recovery_while_engaged_does_not_clear_kill_switch
8 failed, 4 passed in 0.31s
```

Mutasi yang sama, di sandbox yang sama, dengan test yang sama, membunuh 8
test — tapi mutmut mencatatnya `survived`:

```
    trading.live.safety.xÇUnverifiedTrackerÇrecord_failure__mutmut_1: survived
    trading.live.safety.xÇUnverifiedTrackerÇrecord_failure__mutmut_8: survived
```

Artinya verdict mutmut di lingkungan ini TIDAK bisa dipercaya, dan angka
"2076 selamat" bukan temuan — itu gejala harness yang salah. Menerbitkannya
sebagai mutation score akan jadi klaim palsu, yang persis yang diminta
dihindari di item 7.

**Penggantinya:** mutasi terarah yang terbukti. Lima bukti cabutan di
**Penggantinya:** mutasi terarah yang terbukti. Lima bukti cabutan di
sesi ini, masing-masing menunjukkan merah -> hijau. Bukti ini jauh lebih

Yang masih perlu/workaround untuk item 5 diselesaikan: kegagalan harness
Yang masih perlu dikerjakan untuk item 5: akar kegagalan harness di WSL
belum ditemukan. Dugaan terkuat: pytest di dalam sandbox
modul yang benar-benar diuji. Itu belum dibuktikan.

---

## a. KONDISI REPO

| Commit | Isi |
|---|---|
| `b2dc0a8` | UnverifiedTracker disambungkan ke run_loop |
| `0d5f605` | validUntil: pagar kewajaran + TESTNET_CHECKLIST |
| `fedacd9` | run.py --release-kill-switch: perintah operator jadi nyata |
| `3a610d5` | Perbaiki karakter rusak di produksi dan test |

```
$ git status
(kosong)
```

---

## b. MUTAN DAN KODE MATI DI PRODUKSI

```
$ git grep -n -E "MUTAN|if False|if True|if 0:|if 1:" -- trading run.py
grep_rc=1 (1 = kosong)
```

Tidak ada output. **Kosong.**

---

## c. SAFETY YANG TERPASANG

### `UnverifiedTracker` — TERPASANG

```
$ git grep -n "self\.unverified\|self\._note_unverified\|self\._note_verified" -- trading run.py
trading/live/engine.py:1501:        should_stop = self.unverified.record_failure(now, reason)
trading/live/engine.py:1508:        elif self.unverified.consecutive == 1:
trading/live/engine.py:1517:        if self.unverified.is_paused():
trading/live/engine.py:1520:        self.unverified.record_success()
trading/live/engine.py:1531:        delay = self.unverified.next_delay()
trading/live/engine.py:1584:                    self._note_unverified(
trading/live/engine.py:1592:                self._note_verified()
trading/live/engine.py:1610:                    self._note_unverified(
trading/live/engine.py:1614:                self._note_verified()
```

Baris 1584 dan 1610 ada DI DALAM `run_loop`. Plus `run.py:1137`.

### `disengage_kill_switch` — TIDAK TERPASANG

```
$ git grep -n "disengage_kill_switch(" -- trading run.py
trading/live/safety.py:425:        `disengage_kill_switch()` tetap ada untuk pemakaian internal, tapi
trading/live/safety.py:721:    def disengage_kill_switch(self, reason: str) -> bool:
```

Hanya definisi + docstring. **Sengaja.** Jalur sah adalah
`operator_release()`, kini lewat `python run.py --release-kill-switch`.
`disengage_kill_switch()` tidak menulis audit log dan tidak menyentuh state
`engaged` di disk.

### `_redirect_log_handlers` — TIDAK PERNAH ADA DI PRODUKSI

```
$ git grep -n "_redirect_log_handlers" -- trading run.py
(kosong)

$ git grep -n "_redirect_log_handlers"
docs/STATE.md:194:| `_redirect_log_handlers()` dimatikan | 1 failed |
tests/conftest.py:285:def _redirect_log_handlers(target):
tests/conftest.py:387:    _redirect_log_handlers(data_dir / "logs" / "trading_bot.log")
tests/test_no_disabled_code.py:190:    `_redirect_log_handlers()` menutup handle file logging yang sudah
tests/test_no_disabled_code.py:233:            for p in self._referenced_files("_redirect_log_handlers")
tests/test_no_disabled_code.py:250:        hits = self._referenced_files("_redirect_log_handlers")
```

Fixture test di `tests/conftest.py`.

**Selisih:** STATE.md mencantumkannya di antara bukti cabutan. Bukti itu
benar, tapi yang dimatikan adalah helper TEST, bukan safety produksi.

---

## d. POLUSI STATE

Snapshot dengan SHA-256 (16 char), ukuran, dan mtime untuk seluruh 371
file di `data_store\`, sebelum dan sesudah suite penuh.

```
file SEBELAH: 371  SESUDAH: 371
BARU: 0 []
HILANG: 0 []
BERUBAH: 2 ['data_store\\historical_candles.db-shm', 'data_store\\trading_bot.db-shm']
   data_store\historical_candles.db-shm sebelah [32768, 1791200482] sesudah [32768, 1791202446]
   data_store\trading_bot.db-shm sebelah [32768, 1791200482] sesudah [32768, 1791202446]
```

`logs\trading_bot.log` **tidak ada** sebelum maupun sesudah (`__log__: None`).

### Dua file yang berubah BUKAN karena test

```
$ python -c "..."  # hash+mtime sebelum, jalankan test, hash+mtime sesudah
SEBELAH: {'data_store/historical_candles.db-shm': ('fd4c9fda9cd3f9ae', 1791202446.76), ...}
pytest: 36 passed, 2 subtests passed in 0.54s
SESUDAH: {'data_store/historical_candles.db-shm': ('fd4c9fda9cd3f9ae', 1791202446.76), ...}
BERUBAH: TIDAK ADA
```

Menjalankan test TIDAK mengubahnya. Penyebabnya langkah `tar` saat menyalin
repo ke filesystem WSL untuk mutmut. **Suite tidak mencemari state.**

### Alasan test yang di-skip

Empat skip, semuanya beralasan:

```
SKIPPED [1] tests\test_live_tui.py:372: jalur POSIX tidak berlaku di Windows
SKIPPED [1] tests\test_live_tui.py:389: jalur POSIX tidak berlaku di Windows
SKIPPED [1] tests\test_live_tui.py:378: jalur POSIX tidak berlaku di Windows
SKIPPED [1] tests\test_live_tui.py:385: jalur POSIX tidak berlaku di Windows
```

---

## e. ENVIRONMENT

```
TRADEBOT_LIVE = tidak diset
TRADEBOT_LIVE_CONFIRMED = tidak diset
HYPERLIQUID_PRIVATE_KEY = tidak diset
HYPERLIQUID_ACCOUNT_ADDRESS = tidak diset
TRADEBOT_TESTNET = tidak diset
```

Tidak ada variabel live yang diset. Tidak ada kredensial, tidak ada order
ke mainnet.

---

## f. KARAKTER RUSAK

244 file ber-git dipindai untuk surrogate, private use, U+FFFD, CJK, Hangul.

```
file diperiksa: 244
temuan: 27
```

Diperbaiki (commit `3a610d5`), semuanya di komentar/docstring:

| File | Baris | Sebelum | Sesudah |
|---|---|---|---|
| `dashboard/layouts/hud_figures.py` | 425 | `# kosong hanya ketika <CJK>` | `mungkin` |
| `tests/hyperliquid_fixtures.py` | 128 | `lewat objek ini<CJK>` | `setara dengan` |
| `tests/test_api_wallet_separation.py` | 177 | `# kali<CJK>` | `kali-konstruktor` |
| `tests/test_fill_cost_funding.py` | 149 | `bias yang <CJK> ke arah` | `condong ke arah` |
| `tests/test_research_loader_integrity.py` | 98 | `Volume BTC per menit<CJK>` | `rata-rata` |

```
py_compile: semua 5 file kompilasi OK
69 passed, 2 subtests passed
```

Sengaja TIDAK disentuh: `research/` (10 temuan, di luar batas tugas) dan
`data_store/_snap*`, `constellation_*.py`, `count_crossings.py`,
`critiques.txt` (12 temuan, snapshot dan skrip investigasi lama).

**Catatan:** kelas regex asli (`\u3400-\u9fff`) TIDAK menangkap temuan
tersebut — beberapa ada di `\u4e00-\u9fff`, `\uac00-\ud7af`, atau area
surrogate. Scan yang diperluas yang menemukannya.

---

## g. BUKTI CABUTAN ACAK — 3 DARI 13

Pemilihan memakai `Get-Random -SetSeed`. Seed ditulis, bukan dipilih sendiri.

```
$ seed = Get-Random -Minimum 1 -Maximum 99999
$ claims | Get-Random -Count 3 -SetSeed $seed
SEED=16456
```

Tiga yang keluar:

1. pagar kewajaran `validUntil`
2. margin kedaluwarsa agent 3600 detik
3. collateral spot: `total` ATAU `hold` nonzero

### g.1 — pagar kewajaran `validUntil`

```
$ git commit -q -m "..."
$ sed -i "s/if seconds > max_future:/if False:/" trading/live/client.py
$ python -m pytest tests/test_agent_wallet_verification.py -q
FAILED tests/test_agent_wallet_verification.py::TestAgentExpiry::test_plausible_window_boundaries
FAILED tests/test_agent_wallet_verification.py::TestAgentExpiry::test_far_future_conversion_returns_none
2 failed, 25 passed in 0.34s

$ # cabut mutan
$ python -m pytest tests/test_agent_wallet_verification.py -q
27 passed in 0.23s
```

**MATI.**

### g.2 — margin kedaluwarsa agent 3600 detik

```
$ git commit -q -m "..."
$ # AGENT_EXPIRY_MARGIN_SECONDS = 3600.0  ->  0.0
$ python -m pytest tests/test_agent_wallet_verification.py tests/test_live_startup_gate.py -q
FAILED tests/test_agent_wallet_verification.py::TestPeriodicReverification::test_verification_interval_is_configurable_value
FAILED tests/test_agent_wallet_verification.py::TestAgentExpiry::test_expiring_within_margin_rejected
2 failed, 32 passed, 1 warning in 2.20s

$ # cabut mutan
$ python -m pytest tests/test_agent_wallet_verification.py -q
27 passed in 0.39s

$ git status --porcelain
(kosong)
```

**MATI.**

### g.3 — collateral spot `total` ATAU `hold`

```
$ git commit -q -m "..."
$ # for field in ("total", "hold"):  ->  for field in ("total",):
$ python -m pytest tests/test_account_truth_table.py tests/test_fail_closed_account.py tests/test_live_preflight.py -q
FAILED tests/test_account_truth_table.py::TestTruthTableRow3ZeroWithSpotCollateral::test_spot_hold_only_passes
FAILED tests/test_fail_closed_account.py::TestSpotEquityIsNotConfusedWithEmpty::test_held_balance_counts_as_funded
2 failed, 72 passed, 21 subtests passed in 0.60s

$ # cabut mutan
$ python -m pytest tests/test_account_truth_table.py tests/test_fail_closed_account.py tests/test_live_preflight.py -q
74 passed, 21 subtests passed in 0.45s

$ git status --porcelain
(kosong)
```

**MATI.**

Tiga dari tiga mati. `git status` bersih setelah masing-masing cabutan.

### Bukti cabutan lain di sesi ini (di luar sampel acak)

| Mutan | Test | Hasil |
|---|---|---|
| `_note_unverified` jadi no-op | test_unverified_wiring | 8 failed |
| `run_loop` tidak memanggil tracker | test_unverified_wiring | 1 failed |
| pagar kewajaran dimatikan | test_agent_wallet_verification | 2 failed |
| dispatch flag CLI diputus | test_operator_release_cli | 1 failed |

---

## SUITE PENUH

Dijalankan dalam beberapa batch karena proses background dibunuh lingkungan
(lihat bagian 5).

```
batch 1 (13 file): 270 passed
batch 2 (13 file): 234 passed
batch 3 ( 7 file): 207 passed, 4 skipped
batch 4 ( 4 file):  78 passed
batch 5 ( 2 file):  28 passed
batch 6 ( 7 file):  65 passed
batch 7 ( 4 file): 119 passed
batch 8 ( 1 file):  26 passed
batch 9 ( 1 file):   6 passed
---------------------------------------------
TOTAL          : 914 passed, 4 skipped, 0 failed
```

Angka ini cocok dengan seed 1..6 di bagian h, yang melaporkan
`914 passed, 4 skipped` setiap kali.

---

## h. SEED SWEEP 25 SEED

```
$ python _seed_sweep.py 25
seed 1     rc=0  914 passed, 4 skipped, 3 warnings, 90 subtests passed in 39.65s
seed 2     rc=0  914 passed, 4 skipped, 3 warnings, 90 subtests passed in 40.69s
seed 3     rc=0  914 passed, 4 skipped, 3 warnings, 90 subtests passed in 40.37s
seed 4     rc=0  914 passed, 4 skipped, 3 warnings, 90 subtests passed in 41.20s
seed 5     rc=0  914 passed, 4 skipped, 3 warnings, 90 subtests passed in 40.07s
seed 6     rc=0  914 passed, 4 skipped, 3 warnings, 90 subtests passed in 43.51s
seed 7     rc=0  914 passed, 4 skipped, 3 warnings, 90 subtests passed in 46.21s
seed 8     rc=0  914 passed, 4 skipped, 3 warnings, 90 subtests passed in 45.47s
seed 9     rc=0  914 passed, 4 skipped, 3 warnings, 90 subtests passed in 45.72s
seed 10    rc=0  914 passed, 4 skipped, 3 warnings, 90 subtests passed in 46.63s
seed 11    rc=0  914 passed, 4 skipped, 3 warnings, 90 subtests passed in 46.89s
seed 12    rc=0  914 passed, 4 skipped, 3 warnings, 90 subtests passed in 47.43s
seed 13    rc=0  914 passed, 4 skipped, 3 warnings, 90 subtests passed in 47.80s
seed 14    rc=0  914 passed, 4 skipped, 3 warnings, 90 subtests passed in 46.93s
seed 15    rc=0  914 passed, 4 skipped, 3 warnings, 90 subtests passed in 46.02s
seed 16    rc=0  914 passed, 4 skipped, 3 warnings, 90 subtests passed in 41.16s
seed 17    rc=0  914 passed, 4 skipped, 3 warnings, 90 subtests passed in 41.38s
seed 18    rc=0  914 passed, 4 skipped, 3 warnings, 90 subtests passed in 41.38s
seed 19    rc=0  914 passed, 4 skipped, 3 warnings, 90 subtests passed in 41.20s
seed 20    rc=0  914 passed, 4 skipped, 3 warnings, 90 subtests passed in 41.25s
seed 21    rc=0  914 passed, 4 skipped, 3 warnings, 90 subtests passed in 41.56s
seed 22    rc=0  914 passed, 4 skipped, 3 warnings, 90 subtests passed in 42.04s
seed 23    rc=0  914 passed, 4 skipped, 3 warnings, 90 subtests passed in 41.15s
seed 24    rc=0  914 passed, 4 skipped, 3 warnings, 90 subtests passed in 42.23s
seed 25    rc=0  914 passed, 4 skipped, 3 warnings, 90 subtests passed in 41.09s

=== 25/25 hijau ===
SEMUA SEED HIJAU
```

---

## RINGKASAN YANG BELUM SELESAI

1. **Item 5 (mutmut) belum selesai.** Harness WSL tidak bisa dipercaya;
   detail di bagian 5.
2. **Dua `-shm` berubah di `data_store\`** — terbukti bukan dari test, tapi
   penyebabnya belum dibuktikan tuntas.
3. **`research/` masih punya 10 temuan karakter rusak** — sengaja, di luar
   batas tugas.
4. **`docs/TESTNET_CHECKLIST.md` belum dijalankan.** Tujuh asumsi masih
   hanya terbukti offline.