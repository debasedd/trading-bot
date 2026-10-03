# Fase 0 — Baseline dan Verifikasi Klaim

**Status gerbang: LULUS**

Tanggal: 2026-10-04 · Commit dasar: `7da7885` · Commit hasil fase: `6107eec`
Platform: Windows 11 · Python 3.14.6 · pytest 9.1.1 · SDK Hyperliquid terpasang

Tidak ada perubahan perilaku produksi di fase ini. Satu-satunya perubahan
adalah penambahan file di `tests/`.

---

## 1. Ringkasan

Memin commit saat ini, menjalankan seluruh test suite dengan dua runner,
mendokumentasikan test yang tidak bisa dipercaya, mengaudit 44 skrip riset
untuk kontaminasi indeks kolom (`r[5]` = volume, bukan close), dan mereproduksi
lima defect jalur live dengan test yang benar-benar gagal — menggunakan
respons **nyata** dari API publik Hyperliquid testnet, bukan stub. Kelima
defect terbukti ada. Empat dari sepuluh item "yang tidak diverifikasi" di
`ARCHITECTURE.md` §18 sekarang sudah terverifikasi, dan satu naik
kelas: paritas kernel C++ tervalidasi di 2.000 book acak, bukan lagi satu
skenario. Yang paling penting secara strategis: **`factor_sweep.py`
berhasil direproduksi persis**, jadi angka edge yang tercatat bukan
warisan skrip rusak — tapi ia juga mengonfirmasi bahwa edge itu hanya ada
di maker, dan sistem produksi tidak punya jalur maker sama sekali.

---

## 2. Daftar commit

| Commit | Isi |
|---|---|
| `7da7885` | Commit dasar fase ini (dokumentasi, ditulis sebelum konteks fase diberikan) |
| `6107eec` | Fase 0: 14 test reproduksi defect + 5 test audit loader riset + fixture respons nyata |

Tidak ada commit lain. File yang ditambahkan:

```
tests/hyperliquid_fixtures.py            172 baris   fixture respons API testnet
tests/known_broken.py                     58 baris   dekorator xfail untuk 2 runner
tests/repro_helpers.py                    65 baris   isolasi gerbang safety
tests/test_repro_live_defects.py         617 baris   14 reproduksi + 7 fakta
tests/test_research_loader_integrity.py  232 baris   audit indeks kolom loader
```

---

## 3. Bukti

### 3.1 Commit dasar dan baseline

```
$ git rev-parse HEAD
7da7885a11733954759cabaf13e1d0f53e411aec

$ git status --short
(kosong — working tree bersih)
```

### 3.2 Baseline test SEBELUM test reproduksi ditambahkan

`unittest`:

```
$ python -m unittest discover tests
Ran 605 tests in 12.155s

OK (skipped=4)
```

`pytest`:

```
$ python -m pytest tests -q -rs --tb=no
SKIPPED [1] tests\test_live_tui.py:389: jalur POSIX tidak berlaku di Windows
SKIPPED [1] tests\test_live_tui.py:378: jalur POSIX tidak berlaku di Windows
SKIPPED [1] tests\test_live_tui.py:372: jalur POSIX tidak berlaku di Windows
SKIPPED [1] tests\test_live_tui.py:385: jalur POSIX tidak berlaku di Windows
2 failed, 599 passed, 4 skipped, 1 warning, 37 subtests passed in 15.23s
```

Dua kegagalan pytest itu **pre-existing**. Dibuktikan dengan menyingkirkan
seluruh file reproduksi:

```
$ git stash -u
$ python -m pytest tests/test_live_tui.py -q --tb=no
FAILED tests/test_live_tui.py::TestUnpatchedSmoke::test_ask_mode_escape_via_real_path
FAILED tests/test_live_tui.py::TestUnpatchedSmoke::test_ask_mode_paper_via_real_path
2 failed, 59 passed, 4 skipped, 4 subtests passed in 0.35s
```

Penyebabnya: `console.ask_mode` memanggil `input()` sementara pytest
menangkap stdin → `OSError: pytest: reading from stdin while output is
captured`. `unittest` tidak punya capture itu, jadi hijau. Bukan
regresi.

### 3.3 Baseline SESUDAH test reproduksi ditambahkan

```
$ python -m unittest discover tests
Ran 631 tests in 13.089s

OK (skipped=4, expected failures=14)

$ python -m pytest tests -q --tb=no
2 failed, 611 passed, 4 skipped, 14 xfailed, 1 warning, 37 subtests passed in 15.84s
```

631 test (+26), 0 kegagalan baru.

### 3.4 Audit test yang tidak bisa dipercaya

Scanner AST (dengan verifikasi manual tiap temuan):

```
$ python -c "<AST scan>"     # hasil lengkap di bawah
=== BENAR-BENAR TANPA ASSERTION ===
total: 9
  test_advanced_modules.py:801  test_production_config_passes_new_validator
  test_advanced_modules.py:855  test_validator_skips_when_disabled
  test_bugfixes.py:355  test_no_callback_with_multiple_bare_outputs
  test_bugfixes.py:393  test_all_outputs_declared_as_list_or_single
  test_bugfixes.py:1007  test_default_config_passes_validation
  test_config.py:26  test_current_config_is_valid
  test_config.py:81  test_disabled_scalping_skips_economics
  test_live_executor.py:161  test_close_short_is_sell
  test_order_book_recorder.py:49  test_schema_is_idempotent
```

**Koreksi terhadap ARCHITECTURE.md §16.2.** Dokumen itu mengklaim 9 test
"benar-benar tanpa assertion". Setelah verifikasi manual satu per satu,
**hanya 1 yang benar** — `test_close_short_is_sell`. Delapan lainnya punya
bukti:

| Test | Kenapa sebenarnya punya bukti |
|---|---|
| `test_config.py:26`, `:81` | pola "expect no raise" — `_validate_scalping_economics` melempar `ValueError` kalau config salah, jadi "tidak melempar" memang pembuktian |
| `test_advanced_modules.py:801`, `:855` | sama — validator melempar `ValueError` || `test_bugfixes.py:1007` | sama |
| `test_bugfixes.py:355`, `:393` | guard manual `raise AssertionError(...)` di dalam loop — pola yang sah, hanya bukan `assert` |
| `test_order_book_recorder.py:49` | `executescript(SCHEMA)` dua kali; kalau tidak idempoten, exception |

Scanner awal saya salah hitung 7 test `test_cpp_kernel.py` sebagai
tanpa-assertion — ternyata mereka delegate ke `self._assert_parity()`.
Scanner diperbaiki agar mengenali pemanggilan method.

**Test yang membaca source text** — `git grep`:

```
$ grep -rn 'read_text(\|\.open(\|Path(.*\.py' tests/*.py
  tests/test_advanced_modules.py:353   agents/execution_agent.py
  tests/test_advanced_modules.py:793   config.yaml
  tests/test_bugfixes.py:1107          run.py
  tests/test_bugfixes.py:1113          run.py
  tests/test_bugfixes.py:1124          data/hyperliquid_feed.py
  tests/test_bugfixes.py:1155          .gitignore
  tests/test_cpp_kernel.py:271         cpp/microstructure_kernel.cpp
  tests/test_cpp_kernel.py:305         cpp/include/microstructure_kernel.h
  tests/test_cpp_kernel.py:318         cpp/bindings.cpp
  tests/test_cpp_kernel.py:337         CMakeLists.txt
  tests/test_cpp_kernel.py:345         data/hyperliquid_feed.py
  tests/test_dashboard_palette.py:49   3 file figure
  tests/test_dashboard_palette.py:58   3 file figure
  tests/test_dashboard_palette.py:74   style.css
  tests/test_dashboard_palette.py:82   3 file figure
  tests/test_layout_budget.py:22-24    style.css, hud.py, hud_figures.py
  tests/test_layout_budget.py:159      hud.py
  tests/test_layout_contract.py:31    (helper _read)
  tests/test_neural_net_layout.py:236  neural_flow.css
```

**Koreksi kedua terhadap ARCHITECTURE.md §16.3.** Dokumen itu mengklaim
12 test "baca source text". Setelah memeriksa mana yang **mengimpor modul
nyata** lalu membaca CSS terpisah (bukan grep string), jumlahnya **9**,
dan hanya 7 yang benar-benar grep string murni:

| Test | Status |
|---|---|
| `test_bugfixes.py:1107,1113,1124,1155` | **grep string murni** — bisa lulus tanpa eksekusi |
| `test_cpp_kernel.py:271,305,318,337,345` | **grep string murni** — mengunci konstanta, flag `-Ofast`, pelepas GIL, dll lewat teks |
| `test_layout_budget.py:159` | grep string murni (markup decision tree hilang) |
| `test_advanced_modules.py:353` | grep string murni |
| `test_advanced_modules.py:793` | `yaml.safe_load` — **bukan** grep, membaca config terstruktur |
| `test_dashboard_palette.py:49,58,74,82` | **bukan** grep — mengimpor modul nyata (`hud_figures`), lalu memeriksa warnanya |
| `test_layout_contract.py` (via `_read`) | **bukan** grep — sebagian besar mengimpor layout laluidayata `create_hud_layout()` |

### 3.5 Dua test yang secara matematis tidak bisa gagal — DIBUKTIKAN

**① `test_layout_contract.py:277 test_layout_files_have_no_hex_literals`**

Replikasi logikanya persis:

```
$ python -c "<replikasi>"

A. File ASLI (hud.py sekarang):
   jumlah '#' sebelum strip : 18
   hexes ditemukan         : []

B. File dengan hex PALSU disuntikkan:
   suntik C = "#0d1117"                -> hexes: []
   suntik COLOR="#ff0000"              -> hexes: []
   suntik style={"color": "#ABCDEF"}   -> hexes: []

C. Untuk bandingan, pola asli TANPA langkah penghapusan '#':
   hexes di hud.py tanpa strip-# : []
```

Penyebabnya baris `:284` `re.sub(r"#.*", "", stripped)` menghapus
segalanya sejak `#` pertama, **lalu** `:285` mencari `#[0-9a-fA-F]{3,6}`.
Setelah strip, `#` tidak pernah ada. Test ini tidak bisa gagal.

**② `test_live_executor.py:226 test_execution_agent_takes_generic_engine`**

```
$ python -c "<inspeksi>"

signature : (self, event_bus: core.event_bus.EventBus, engine)

params['engine']        : engine
str(params['engine'])  : 'engine'

assertNotIn('PaperTradingEngine', str(...)) -> True

annotation-nya: <class 'inspect._empty'>
default-nya   : <class 'inspect._empty'>
```

`agents/execution_agent.py:37` mendeklarasikan `def __init__(self, event_bus, engine)`
**tanpa anotasi**, jadi `str(params["engine"])` adalah literal `'engine'`.
Reduksinya: `"PaperTradingEngine" not in "engine"` → selalu `True`.

### 3.6 Stub yang menutupi defect fatal — DIBUKTIKAN

```
$ grep -n "def symbol_notional" tests/test_live_engine.py
63:    def symbol_notional(self, coin):
64:        return 0.0
```

Stub ini tepat menutupi `client.py:219`. Delapan test di
`tests/test_live_engine.py` memakainya, sehingga 190 test live hijau
dengan defect fatal masih ada.

### 3.7 Fixture respons nyata

Diambil dari API publik testnet, read-only, tanpa key:

```
$ python -c "<POST https://api.hyperliquid-testnet.xyz/info>"

universe: 212 aset
contoh: ['SOL', 'APT', 'ATOM', 'BTC', 'ETH']
name_to_asset('BTC') analog = 3

# userState -> HTTP 422 "Failed to deserialize"
# clearinghouseState -> OK
  assetPositions: []
  marginSummary: {"accountValue": "61.702826", ...}

# posisi nyata dari alamat publik
address: 0x5972698398d8c5bbe67c0db74906236691020417
{
  "type": "oneWay",
  "position": {
    "coin": "BTC",              <-- STRING
    "szi": "-0.24567",          <-- string desimal
    "leverage": {"type": "cross", "value": 5},
    "entryPx": "85110.1",        <-- string desimal
    "positionValue": "20902.83195",
    ...
    "maxLeverage": 25,           <-- tidak ada di docstring SDK
    "cumFunding": {...}          <-- tidak ada di docstring SDK
  }
}
```

Tiga fakta yang hanya bisa diketahui dari respons nyata:

1. `position.coin` adalah **string** — inti bug `client.py:219`
2. Semua angka datang sebagai **string desimal**, bukan float JSON
3. Payload yang benar adalah `clearinghouseState`, bukan `userState`
   seperti docstring SDK menyebutnya — `userState` mengembalikan HTTP 422

### 3.8 Lima defect terbukti ada

```
$ python -m pytest tests/test_repro_live_defects.py -rxX
7 passed, 14 xfailed, 0 XPASS
```

| Defect | Test reproduksi | Status |
|---|---|---|
| 1. `client.py:219` int() pada string | 4 test | XFAIL |
| 2. kunci simbol tidak sinkron | 3 test | XFAIL |
| 3. `health_check` latch saat fill normal | 3 test | XFAIL |
| 4. partial fill tidak ditangani | 1 test | XFAIL |
| 5. tidak ada lookup by cloid | 3 test | XFAIL |

Reproduksi langsung DEFECT-1:

```
$ python -c "..."
name_to_asset(BTC) = 3
RAISED: ValueError invalid literal for int() with base 10: 'BTC'
```

Reproduksi langsung DEFECT-2:

```
ERROR trading_bot.live_engine:109  POSISI HANTUAN: 1 tercatat lokal tapi
      TIDAK ada di bursa: ['BTC/USDT:USDT']
ERROR trading_bot.live_engine:115  POSISI ASING: 1 ada di bursa tapi tidak
      tercatat: ['BTC / USDC:USDC']
ERROR trading_bot.live_safety:455   KILL SWITCH diaktifkan oleh sistem:
      rekonsiliasi gagal: posisi lokal dan bursa tidak cocok
```

Dua kunci berbeda untuk koin yang **sama persis**. Homicide.

### 3.9 `xfail(strict=True)` terbukti menangkap perbaikan

Memperbaiki DEFECT-1 sementara (`int(...)` → `str(...)`):

```
$ python -m pytest tests/test_repro_live_defects.py -rxX
[XPASS(strict)] DEFECT-1 client.py:219 int() pada field string   (x3)
3 failed, 7 passed, 11 xfailed

# dipulihkan:
$ python -m pytest tests/test_repro_live_defects.py
7 passed, 14 xfailed
```

Perbaikan tidak bisa lolos diam-diam. Second runner (unittest) juga
menangkapnya — `unittest.expectedFailure` melaporkan "unexpected success"
sebagai kegagalan.

### 3.10 Loader riset — kontaminasi indeks kolom

```
$ python -m pytest tests/test_research_loader_integrity.py -v
5 passed in 0.15s
```

Test ini membuktikan dirinya bisa gagal (RED → GREEN):

```
$ # bug disuntik kembali ke market_neutral.py
$ python -m pytest tests/test_research_loader_integrity.py
E  AssertionError: 'r[0]: r[4] for r in v' not found ...
FAILED test_market_neutral_uses_correct_close_index

$ # dipulihkan
$ python -m pytest tests/test_research_loader_integrity.py
5 passed
```

**Daftar skrip dan status kontaminasi:**

| Skrip | Loader | Close di indeks | Status |
|---|---|---|---|
| `market_neutral.py` | `for sym,ts,o,h,l,c in` → 5-elemen | `r[4]` | **Pernah kontaminasi** (`r[5]`), sudah dikoreksi sebelum commit `7da7885`. Test mengunci agar tidak regress |
| `factor_sweep.py:74` | `raw[sym]` 6-elemen `(ts,o,h,l,c,v)` | baris itu `r[5]` = **volume** | **Kontaminasi**, tapi baris mati — `cs` tidak pernah dipakai. `load_1h()` mengembalikan 4 tuple; `cs_close = r[4]` di baris 80 **benar** |
| `bt.py` | `raw[sym]` 6-elemen | `r[4]` | Bersih |
| `regime_gate.py`, `regime_split.py` | 6-elemen | `r[4]` | Bersih |
| `ofi_test.py`, `reversal_test.py` | 6-elemen | `r[4]` | Bersih |
| `test_reality.py`, `walkforward_validation.py` | 6-elemen | `r[4]` | Bersih |

**Hasil riset yang harus dianggap tidak valid:** semua angka yang
menghasilkan dari loader yang memakai `r[5]` sebagai close.INCLUDE — yaitu
hasil `market_neutral.py` versi lama (`+9.2M USDT`, win rate 93%, t=20+)
dan semua turunannya (`audit_neutral.py`, `robust_neutral.py`,
`leverage_audit.py`, `lookahead_check.py`, `deep_neutral.py`,
`horizon_scan.py`, `final_audit.py`, `lower_turnover.py`,
`regime_conditioning.py`).

Hasil `factor_sweep.py` **tidak** terkontaminasi — loadernya memakai
`r[4]` di jalur yang benar-benar dieksekusi. Konfirmasi di §3.11.

### 3.11 Item §18 yang bisa diverifikasi

**Item 1 — tidak ada order live. TERKONFIRMASI.**

```
$ sqlite3 data_store/trading_bot.db "SELECT mode, COUNT(*) FROM positions GROUP BY mode"
positions|paper|253
$ ... trades ...
trades|paper|503

$ grep -rn "TRADEBOT_LIVE" --include=*.py .   # di luar tests/
  trading/live/safety.py:334, 345     (membaca, tidak pernah menulis)
  live_doctor.py:189-190               (os.environ.pop — MENGHAPUS)
```

**Tidak ada kode produksi yang meng-set env itu.** YerAda satu jalan
menuju live order: tidak ada.

**Item 3 — FRED mati. TERKONFIRMASI.**

```
$ sqlite3 data_store/trading_bot.db "SELECT COUNT(*) FROM macro_data"
0

$ grep -rn "fred_api_key" --include=*.py --include=*.yaml .   # di luar tests/
  data/macro_fetcher.py:38,39,44,78    (hanya definisi parameter)

$ grep -n "MacroFetcher(" run.py
263:    self.macro_fetcher = MacroFetcher()     # tanpa argumen
```

Nol config key, nol env var. Tidak ada jalan supplying key.

**Item 5 — paritas C++. NAIK KELAS.**

```
$ python -m pytest tests/test_cpp_kernel.py -q
22 passed

$ python -c "<fuzz 2000 book acak>"
=== [5] FUZZ PARITAS KERNEL C++ ===
  2000 book acak: sisi 1..32 level, depth 1/3/5/10/20/32
  toleransi 1e-9
  HASIL: tidak ada divergensi > 1e-9 di 2000 kasus
```

Klaim ARCHITECTURE.md §13.4 yang sebelumnya hanya "satu pembandingan" sekarangberpijak pada 2.000 kasus acak. Klaim paritas **diperkuat**, bukan
ditemper.

**Item 7 — riset direproduksi. TERKONFIRMASI.**

```
$ python research/factor_sweep.py
symbols: 21, hours: 5000 (208 days)

--- COST: maker (2.2 bps/leg) ---
  factor           trail  hold     n        net       t     wr  q_pos
  momentum            12    12   415   +4459.75   +2.34  52.3%     3/4  *** SIGNIFICANT ***
  dist_from_high     12    72    69   +4839.52   +2.01  56.5%     4/4

--- COST: taker (6.2 bps/leg) ---
  momentum            12    12   415   +1139.75   +0.60  47.7%     3/4

RANDOM BASELINE (best config parameters)
  seed=0  net=+1566.26  t=+1.05
  seed=1  net=+2689.13  t=+1.80
  seed=2  net=-2363.22  t=-1.47

  momentum trail=12h hold=12h cost=maker
    net=+4459.75  t=+2.34  wr=52.3%
    quarters: t=-0.52 / t=+1.72 / t=+1.28 / t=+1.79
    final equity: 14459.75  max drawdown: 8.3%
```

**Angka cocok persis** dengan `research/FINDINGS.md`. Edge-nya nyata
dalam harness itu. Dua catatan penting:

- Config yang sama di **taker hanya t=+0.60** — edge hilang
- **Random baseline mencapai t=+1.80**, hanya 0.54 di bawah edge asli.
  Pada 5 seed yang ditampilkan, distribusi acakrespondence lebar.
  Temuan `FINDINGS.md` (p<0.002 dari 500 baseline) berasal dari
  `deep_audit.py`, yang belum saya jalankan di fase ini.

**Item 8 — pandas_ta. TERKONFIRMASI.**

```
$ python -c "import importlib.metadata as md; ..."
  pandas-ta      0.4.71b0
  pandas         3.0.5

$ python -c "import pandas_ta as ta; ..."
  macd  columns: ['MACD_12_26_9', 'MACDh_12_26_9', 'MACDs_12_26_9']
  bbands columns: ['BBL_20_2.0_2.0', 'BBM_20_2.0_2.0', 'BBU_20_2.0_2.0', ...]

$ grep -rn "import pandas_ta" --include=*.py .
  analysis/technical.py:9   import pandas_ta as ta      <- level modul, tanpa fallback
  ml/trainer.py:44          import pandas_ta as ta      <- di dalam fungsi
```

`requirements.txt:24` hanya mensyaratkan `pandas-ta>=0.3.14b1` **tanpa
batas atas**. Versi 0.3.x punya layout kolom berbeda. `analysis/technical.py`
mengakses via `iloc[:, 0/1/2]` — benar untuk 0.4.71b0, salah untuk 0.3.x.
Tidak ada `try/except ImportError`.

---

## 4. Yang gagal, tidak selesai, dan tidak yakin

### Gagal

**Tidak ada.** Kelima defect terbukti ada dan sekarang punya test.

### Tidak selesai (di luar lingkup Fase 0)

- **Item 2** (tidak ada rekaman jalannya bot) — tidak bisa diverifikasi tanpa
  menjalankan bot. Log produksi tercemar test: 11.049 dari 70.988 baris
  (15.6%) menyebut `test_*.db`, dan 8.028 baris "rollover" adalah artefak
  `_midday()` = 2026-01-15, bukan operasi.
- **Item 4** (dashboard belum dibuka di browser) — butuh browser + bot
  berjalan.
- **Item 6** (`analysis/backtester.py` tidak pernah dijalankan) — butuh
  data order book berurutan; hanya ada snapshot 1 detik.
- **Item 9** (performa throughput) — butuh 10 simbol top-volume + WS
  während beberapa jam.
- **Item 10** (Windows-only) — butuh mesin POSIX.

### Tidak yakin

1. **Apakah angka `deep_audit.py` (p<0.002 dari 500 baseline) withstand
   eksekusi ulang.** Saya menjalankan `factor_sweep.py` dan hasilnya cocok,
   tapi tidak menjalankan `deep_audit.py` maupun `walkforward_validation.py`.
   Mengingat random baseline di `factor_sweep` saja mencapai t=+1.80, saya
   ingin melihat distribusi penuh 500 seed sebelum menganggap p<0.002
sebagai hal yang kokoh.
2. **Apakah environment issue (`OSError: pytest: reading from stdin`)
   bisa diperbaiki tanpa mengubah perilaku produksi.** Dua test TUI gagal
   di pytest tapi hijau di unittest. Saya tidak menyentuhnya karena di luar
   lingkup Fase 0 dan perbaikannya bisa berarti mengubah kode produksi.

3. **Mutabilitas global di `SafetyGate`.** `DayCounters` dibaca dari
   `data_store/live_counters.json` setiap kali `TRADEBOT_LIVE` bernilai
   benar. Test pertama yang saya tulis **mewarisi kill switch** dari test
   lain karena itu, dan 5 test sempat XPASS karena alasan yang salah. Saya
   perbaiki dengan `repro_helpers.clean_gate()` yang memakai path tempfile.
   Tapi ada architectural smell yang belum diperbaiki: test lain di repo
   (`test_live_safety.py`) sengaja memakai PID-scoped path — apakah mereka
   juga bisa saling memengaruhi, atau hanya kebetulan tidak pada urutan
   yang sama.

4. **Apakah `factor_sweep.py:74` (`cs[s] = {r[0]: (r[5], ...)}`) pernah
   menyumbang apa pun.** `cs` tidak pernah dibaca di file itu. Tapi saya
   belum menjalankan `git log -p` untuk melihat apakah pernah dipakai
   sebelum dihapus.

5. **Apakah 14 test `xfail` akan tetap valid setelah perbaikan pertama.**
   Beberapa defect saling terkait — memperbaiki DEFECT-2 mungkin
   otomatis memperbaiki bagian DEFECT-3. `xfail(strict=True)` akan
   melaporkan XPASS di sana, dan itu **bagus**: artinya ada defect yang
   bisa dianggap selesai lebih cepat dari dugaan.

---

## 5. Risiko baru atau temuan baru di luar lingkup

### Di luar lingkup

1. **Test suite punya dua runner dengankriteria keberhasilan berbeda.**
   `unittest` = 631 OK. `pytest` = 2 failed. README menyebut keduanya. Mulai
   sekarang keduanya harus hijau.

2. **`data_store/live_counters.json` adalah state global yang dibaca test.**
   Saya menghapus file itu beberapa kali selama bekerja. Kalau file itu
   ada di mesin lain dengan `engaged: true`, test live akan gagal atau —
   lebih buruk — lulus untuk alasan salah. **Ini harus immutable per test.**

3. **12 file `test_live_gate_*.json` terlacak di git dan muncul ulang di
   setiap test run** (`test_live_engine.py:116`). Sudah saya untrack di
   `7da7885` dan masukkan `.gitignore`, tapi file lama masih ada di
   working tree beberapa kali setelah test jalan.

### Di dalam lingkup

4. **`known_broken()` dekorator tidak kompatibel dengan pytest 8+.**
   Saya memakai `pytest.mark.xfail(strict=True)` yang sudah deprecated
   (=`xfail` dengan `strict=True`). Deprecation warning mungkin muncul
   setelah upgrade pytest. verified sekarang jalan di 9.1.1.

5. **Dua test QA yang saya buat initially false-positive** — almonds
   sampai saya menemukan sendiri:
   - `test_timeout_then_retry` lulus karena **gate menolak** (order 850
     USDC > batas 100), bukan karena idempotensi
   - 3 test facts XPASS karena ikut kena `xfail` di level kelas

   Keduanya sudah diperbaiki dan diverifikasi ulang. Ini instruksi
   practicality: pada sistem dengan gerbang safety aktif, test lanjut
   bisa lulus karena alasan yang **salah**. Selalu pastikan test benar-benar
   melewati gate yang diuji.

---

## 6. Pertanyaan atau keputusan yang butuh Anda

1. **Item §18 mana yang mau saya kejar di Fase 1?** Sisa: rekaman
   jalannya bot, dashboard di browser, backtester L2, throughput, POSIX.
   Saran saya: dua yang pertama — dashboard dan throughput adalah yang
   paling mungkin memunculkan kejutan (dashboard belum pernah dibuka sejak
   ditulis ulang).

2. **Dua test TUI yang gagal di pytest** — saya tangani sekarang (perbaiki
   capture-nya, tanpa sentuh produksi) atau saya dokumentasikan saja
   sebagai known issue?

3. **Recontoh saya perlu izin menjalankan `deep_audit.py` dan
   `walkforward_validation.py`** untuk memastikan p<0.002 dan 4/4 walk-forward
   withstand eksekusi ulang. Keduanya read-only terhadap data historis,
   tidak menyentuh produksi. Saya belum menjalankannya karena tidak jelas
   apakah Anda menganggap itu sudah tercakup "riset direproduksi".

4. **Prioritas perbaikan live-path.** Lima defect ini saling menumpuk:
   DEFECT-1 membuat order live mustahil; DEFECT-5 membuat retry tidak
   aman; DEFECT-2 membuat kill switch nyala sendiri; DEFECT-3 memperburuk.
   Saran urutan: **1 → 2 → 3 → 4 → 5**. Alasan: DEFECT-1 satu baris
   (`str()` bukan `int()`), DEFECT-2 hanya format kunci, keduanya membuka
   jalan uji testnet. DEFECT-3 butuh keputusan desain (bagaimana tahu fill
   terjadi tanpa langganan fill), DEFECT-4 dan 5 butuh kerja lebih besar.
   Apakah urutan ini sesuai Equals Anda?

5. **Satu hal yang perlu Anda putuskan sekarang, sebelum Fase 1:** apakah
   mainnet punya batas yang bisa saya jadikan target? `LiveConfig` sekarang
   `max_order_notional=100 USDC`, `max_total_notional=600`. Itu kecil
   sekali. Saya tidak akan mengubahnya tanpa persetujuan eksplisit —
   aturan keras nomor 2.

---

## Lampiran — Perintah verifikasi ulang

```bash
# Baseline
git rev-parse HEAD                       # harus 6107eec
python -m unittest discover tests         # 631, OK (expected failures=14)
python -m pytest tests -q                 # 2 failed (pre-existing), 14 xfailed

# Audit test tak dipercaya
python -m pytest tests/test_research_loader_integrity.py -v   # 5 passed

# Reproduksi defect
python -m pytest tests/test_repro_live_defects.py -rxX        # 7 passed, 14 xfailed, 0 XPASS

# Fakta live
sqlite3 data_store/trading_bot.db "SELECT mode, COUNT(*) FROM positions GROUP BY mode"
sqlite3 data_store/trading_bot.db "SELECT COUNT(*) FROM macro_data"
grep -rn "MacroFetcher(" run.py            # harus tanpa argumen

# Item 7
python research/factor_sweep.py            # t=+2.34 di maker, t=+0.60 di taker
```
