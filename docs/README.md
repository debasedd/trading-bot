# Dokumentasi 0XF3CE25

Satu dokumen: **[`ARCHITECTURE.md`](ARCHITECTURE.md)** — 18 bagian,
dibaca baris-per-baris dari source pada commit `e49011a`.

| # | Bagian | Isi |
|---|---|---|
| 1 | Apa ini dan keadaannya sekarang | Angka repo, performa terukur, ringkasan jujur |
| 2 | Cara menjalankan | CLI, env var, exit code |
| 3 | Arsitektur | Lapisan, thread/task, scheduler, kanal mati |
| 4 | Alur data end-to-end | WS → kernel → ensemble → keputusan → fill → UI |
| 5 | Konfigurasi | Lapisan config, pemetaan YAML, 3 validator, key mati |
| 6 | Ekonomi | Model biaya, biaya terukur per simbol, R:R, breaker, akuntansi |
| 7 | Paper path | Rejection ladder, guard tick, lifecycle, 7 jalur tutup |
| 8 | Live path | Client, safety gate, kill switch, 7 jalur, defect blocking |
| 9 | Data layer | Hyperliquid, multi-tier, rekorder, skema DB, repository |
| 10 | Analysis layer | Indikator, ensemble (yang hidup), volatilitas, mati |
| 11 | Agen | 5 agen + matriks interval |
| 12 | Dashboard | Grid, 19 callback, token CSS, figure, empty state |
| 13 | Ekstensi native C++ | Ekspor, paritas, parser, binary, 4 cacat |
| 14 | Machine learning | Artefak, 5 train/serve skew, fallback, backtester |
| 15 | Riset | Apa yang gagal, edge yang terverifikasi, kesalahan yang lolos |
| 16 | Test suite | Angka, test tanpa assertion, test tak bisa gagal |
| 17 | Daftar defect | 53 temuan, urut dampak |
| 18 | Yang tidak diverifikasi | 10 hal yang tidak diklaim |

Lampiran A: peta file. Lampiran B: perintah verifikasi — semua klaim di
dokumen bisa dicek ulang.

## Ringkas kalau hanya punya 5 menit

1. **Live tidak pernah berjalan.** Tidak ada kode yang meng-set
   `TRADEBOT_LIVE`, jadi `SafetyGate` menolak semua order. Tiga defect
   blocking lain ada di jalur itu (§8.2, §8.8).
2. **Paper menghapus uang.** 250 trade, PF 0.213, win rate 30% terhadap
   66.8% yang dibutuhkan. R:R riil 1:2, bukan 1:1 yang diklaim config (§6).
3. **Edge yang benar tidak terhubung.** `trading/cross_sectional.py`
   (t=2.34, tervalidasi 4/4 walk-forward) tidak di-import siapa pun (§17.1).
4. **Kernel C++ bisa membunuh proses.** `ingest_l2` dengan input salah
   →
   `exit 127`, tidak bisa di-catch (§13.8).
5. **12 test baca source text; 2 test tidak bisa gagal; 1 test tidak punya
   assertion** — dan yang terakhir itu tepat di jalur tutup short live (§16).
