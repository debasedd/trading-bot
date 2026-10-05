# TESTNET_CHECKLIST — asumsi yang hanya terbukti offline

**Status: BELUM DIJALANKAN. Tidak ada satu pun butir di bawah ini yang
sudah diverifikasi terhadap bursa nyata.**

Semua test suite berjalan tanpa jaringan (`tests/conftest.py` memblokir
socket). Itu pelindung yang benar untuk CI, tapi artinya setiap asumsi
tentang bentuk respons bursa di repo ini **belum pernah diuji terhadap
bursa**. Test memberi jaminan bahwa kode konsisten dengan asumsi — bukan
bahwa asumsinya benar.

Dokumen ini adalah daftar asumsi yang ada, cara memverifikasinya, dan
hasil yang diharapkan. Jalankan per butir, catat hasil NYATA (termasuk
yang bertentangan dengan harapan), dan perbarui butir yang berubah.

## CARA MENJALANKAN

T1–T3 dan T6–T7 read-only atau butuh testnet key. T4 dan T5 butuh
testnet key. Key hanya lewat environment variable, tidak pernah ditulis
ke log atau dokumen ini.

```bash
python _probe_testnet.py <nomor>
```

`mainnet` DILARANG. Kalau sebuah probe tidak bisa dijalankan di testnet,
catat "belum terverifikasi" — jangan menebaknya.

---

## T1 — Bentuk respons `extraAgents`

**Asumsi offline:** `extra_agents(user)` mengembalikan
`[{"name": str, "address": str, "validUntil": int}, ...]`.

Asal: docstring SDK `hyperliquid.info.Info.extra_agents` (dibaca dari
paket yang terpasang, bukan dari ingatan).

**Kenapa berbahaya:** `verify_agent_wallet()` memakai `address` untuk
mencocokkan signer. Kalau field itu bernama lain, daftar agent kosong,
atau bentuknya berbeda, pencocokan gagal dan bot menolak start — atau
mencocokkan tidak sengaja ke entri yang bukan signer.

**Cara verifikasi:** POST `{"type": "extraAgents", "user": <master>}` ke
testnet, print respons mentah.

**Diharapkan:** array of dict; tiap dict punya `name`, `address`
(42 hex), `validUntil` (int).

**Kalau berbeda:** daftar agent kosong -> semua bot dengan pemisahan
wallet ditolak saat start. Perbaiki pemetaan field dan tambahkan test
dari respons rekaman.

---

## T2 — Satuan `validUntil`

**Asumsi offline:** satuan dideteksi dari besarannya — nilai `> 1e11`
dianggap milidetik, selain itu detik.

Asal: **tidak ada.** Dokumentasi hanya menulis `"validUntil": int`
tanpa menyatakan satuan. Ini tebakan, dan sekarang DIJAGA pagar
kewajaran (`_agent_valid_until_seconds`): hasil di luar jendela
(sekarang .. +1 tahun) jadi `None` = "tidak bisa menilai" plus log
error — bukan dianggap beres.

**Kenapa berbahaya:** salah membaca satuan membuat pemeriksaan
kedaluwarsa selalu lulus (tidak pernah bersuara) atau selalu gagal.
Keduanya tidak terlihat dari test offline.

**Cara verifikasi:** baca `validUntil` nyata, bandingkan dengan
`time.time()`. Kalau angkanya 13 digit dan sedikit di masa depan,
sqliteannya milidetik. Catat juga berapa lama daftarnya berlaku secara
nyata — itu menentukan apakah `AGENT_EXPIRY_MARGIN_SECONDS = 3600`
cukup.

**Kalau berbeda:** ganti heuristik satuan dan SESUAIKAN test
(`test_milliseconds_are_normalised`, `test_plausible_window_boundaries`)
dari respons rekaman, bukan dari tebakan.

---

## T3 — Bentuk respons `spotClearinghouseState`

**Asumsi offline:** `spot_user_state(user)` mengembalikan dict dengan key
`balances`, tiap entri punya `coin` (str), `total` (str), `hold` (str).

Asal: docstring SDK `Info.spot_user_state` + respons testnet lama yang
direkam di `tests/hyperliquid_fixtures.py`.

**Kenapa berbahaya:** `spot_equity_is_nonzero()` memakai `total` dan
`hold` untuk memutuskan "akun punya collateral atau tidak". Salah baca
bentuk = akun nyata terbaca sebagai kosong (bot berhenti).

**Cara verifikasi:** POST `{"type": "spotClearinghouseState", "user":
<master>}` untuk akun bersaldo spot dan akun kosong.

**Diharapkan:** dua kasus terbedakan — akun bersaldo punya
`coin: "USDC"` dengan `total` > 0; akun kosong punya `balances` kosong
atau `total` "0".

**Kalau berbeda:** `spot_equity_is_nonzero()` yang paling terdampak.
Perhatikan: `Info.spot_user_state` mengirim `{"type":
"spotClearinghouseState"}` — nama tipenya berbeda dari nama metodenya,
dan itu bukan tebakan.

---

## T4 — Perilaku akun kosong untuk alamat salah

**Asumsi offline:** `user_state(<alamat yang bukan akun>)` membalas objek
berbentuk akun dengan isinya kosong (`assetPositions: []`,
`accountValue: "0"`), bukan error.

Asal: **dokumentasi**, bukan pengamatan langsung — Info endpoint bagian
"User address": *"A common pitfall is to use the agent wallet's address
which leads to an empty result."*

**Kenapa berbahaya:** ini asal dari seluruh tabel kebenaran
`tests/test_account_truth_table.py`. Kalau bursa ternyata membalas ERROR
untuk alamat tak dikenal, guard `accountValue == 0` tidak pernah menyala
— dan bug asli (alamat salah ketik terbaca sebagai "datar") kembali.

**Cara verifikasi:** (butuh testnet key) jalankan dengan
`HYPERLIQUID_ACCOUNT_ADDRESS` berisi satu karakter yang diganti dari
master yang valid, sehingga alamat itu pasti bukan akun. Lihat apa yang
dikembalikan `user_state`.

**Diharapkan:** objek berbentuk akun, isinya kosong, tanpa error.

**Kalau berbeda:** kalau ternyata error, baris 4 tabel kebenaran harus
berubah dan `_require_real_account()` perlu ditambah cabang error.

---

## T5 — Penerimaan format `cloid`

**Asumsi offline:** SDK menerima `cloid` berupa objek `Cloid`, dan hex
bukan-hex ditolak.

Asal: pengamatan langsung pada kode SDK
(`order_request_to_order_wire` memanggil `cloid.to_raw()`), plus
regresi DEFECT-6 yang tercatat di `docs/STATE.md`.

**Kenapa berbahaya:** kalau bursa menerima format lain, `Cloid` yang kita
buat ditolak saat signing — order hilang tanpa jejak, dan itu baru
ketahuan saat order hilang.

**Cara verifikasi:** (butuh testnet key) kirim order dengan `cloid`
valid dan dengan hex bukan-hex; bandingkan respons bursa.

**Diharapkan:** hex valid diterima; hex tidak valid ditolak bursa atau
SDK sebelum dikirim, dan pesannya menyebut cloid.

**Kalau berbeda:** `to_cloid()` perlu disesuaikan;
`tests/test_cloid_wire_encoding.py` diperbarui dari rekaman.

---

## T6 — Partial fill

**Asumsi offline:** `parse_order_response` membaca `filled.totalSz`, dan
respons order bisa berupa batch list dengan satu elemen per order.

Asal: dokumentasi Info endpoint + fixture rekaman
`tests/hyperliquid_fixtures.py`. Item (c) Fase 1 sudah
menerapkaninya, tetapi tidak pernah diuji terhadap bursa.

**Kenapa berbahaya:** kalau field partial-fill bernama lain, proteksi
dipasang untuk ukuran yang salah — posisi dengan SL/TP di harga yang
tidak sesuai.

**Cara verifikasi:** (butuh testnet key) kirim order IOC/IEX yang hanya
terisi sebagian di book tipis, baca `filled.totalSz`.

**Dampak:** `parse_order_response` dan logika partial fill di
`executor.py`.

---

## T7 — Event fill saat SL/TP terpicu

**Asumsi offline:** saat trigger SL/TP milik bot terisi, `user_fills`
mengembalikan baris dengan `dir` bernilai `"Close Long"`/`"Close Short"`
dan `closedPnl` sudah net.

Asal: **respons testnet rekaman** (2026-10-03) di
`tests/hyperliquid_fixtures.py` — satu baris BTC:
`{"coin":"BTC","dir":"Close Short","closedPnl":"-0.0414","fee":-0.001763,...}`.

**Kenapa berbahaya:** `poll_exchange_fills()` membedakan "trigger kita
yang fire" dari "posisi hilang entah kenapa" berdasarkan `dir` itu. Salah
baca berarti setiap SL/TP yang bekerja terlihat sebagai divergensi dan
kill switch menyala pada strike pertama. Ini memengaruhi item (h).

**Cara verifikasi:** (butuh testnet key) pasang SL/TP pada posisi testnet
kecil, biarkan SL terpicu, baca `user_fills`.

**Diharapkan:** baris fill dengan `dir` menunjuk arah penutupan yang
benar, `closedPnl` STRING desimal (bukan float), `fee` negatif.

---

## Yang BELUM ada di daftar ini

Butir berikut tidak bisa diverifikasi dengan satu panggilan read-only:

- **Free collateral.** `free_collateral()` memakai
  `marginSummary.withdrawable` dengan fallback ke `accountValue -
  notional`. Belum pernah dibandingkan dengan angka yang sama di UI.
- **Rate limit.** `user_rate_limit()` dipakai untuk keputusan. Bentuk
  respons belum direkam.
- **Funding.** Item (f) belum dikerjakan sama sekali; endpoint dan
  skemanya masih harus dikonfirmasi lebih dulu
  (lihat `docs/STATE.md` § Syarat item (f)).
- **Shutdown order resting.** Item (i) belum dikerjakan.

Setiap butir yang selesai diverifikasi harus dipindahkan ke STATE.md
dengan commit dan output mentahnya.