"""
tests/test_state_isolation.py — Test tidak boleh menyentuh `data_store/`.

MASALAH YANG INI TUTUP
----------------------
`_build_live_executor()` membangun `SafetyGate(live_cfg)` tanpa
`state_path`, jadi `DayCounters` memakai default
`data_store/live_counters.json`. Test yang memanggil fungsi itu sungguhan
menulis ke file kill switch PRODUKSI.

Baru ketahuan karena bukti cabutan: dengan mutan "preflight dihapus",
engine sungguhan sempat jalan dan file itu muncul dengan isi

    {"consecutive_errors": 1, "engaged": true, ...}

Artinya operator yang membuka bot berikutnya menemukan kill switch AKTIF
tanpa sebab. Test yang menempelkan gangguan ke mekanisme pengaman adalah
pencemar, bukan bukti.

KENAPA FIXTURE `isolate_state_paths` SAJA TIDAK CUKUP
----------------------------------------------------
Fixture di `conftest.py` mengarahkan `os.getcwd()` ke tmp_path, jadi path
relatif jatuh ke sana. Dua celah tetap terbuka:

1. Path ABSOLUT yang dibentuk dari lokasi repo (mis. lewat
   `Path(__file__).parent`), yang mengabaikan cwd sepenuhnya.
2. Kode yang menulis langsung ke `data_store/` tanpa lewat config.

File ini menutup keduanya dengan memeriksa FAKTA, bukan konfigurasi:
snapshot `data_store/` sebelum dan sesudah, lalu membandingkan.

KENAPA BUKAN CUKUP MEMPERIKSA UKURAN MODUL SAJA
-----------------------------------------------
Modul di dalam `data_store/` adalah produk, bukan state. Yang berbahaya
adalah BERKAS: DB, JSON state, log. Menghapus modul yang tidak dipakai
bukan pencemar yang menyakitkan; menulis `engaged: true` ke file state
adalah.
"""

import hashlib
import pathlib
import tempfile
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA_STORE = REPO_ROOT / "data_store"

# Snapshot yang diambil `pytest_configure`, SEBELUM test pertama jalan.
# Module-level supaya bisa ditulis dari conftest tanpa mengimpor fixture.
_BASELINE = {}

# Pola yang TIDAK boleh ditulis test. `*.db` dan `logs/` sudah diabaikan
# git, tapi "sudah diabaikan" bukan alasan yang cukup untuk membiarkan test
# menimpanya: file log operator hilang hanya karena satu test.
_INTERESTING_SUFFIXES = (".json", ".db", ".log", ".csv")


def _snapshot():
    """
    Peta {nama relatif: (ukuran, sha256)} untuk semua berkas state.

    Dipakai hash, bukan hanya ukuran, karena pencemar yang terdeteksi
    adalah "file state ditulis ulang dengan isi berbeda" — ukurannya
    sering sama persis.
    """
    out = {}
    if not DATA_STORE.is_dir():
        return out
    for path in sorted(DATA_STORE.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in _INTERESTING_SUFFIXES:
            continue
        rel = path.relative_to(DATA_STORE).as_posix()
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            out[rel] = (path.stat().st_size, digest)
        except OSError:
            # Berkas hilang di tengah pembacaan = sudah berubah.
            out[rel] = ("vanished", None)
    return out


class TestSnapshotIsTrustworthy(unittest.TestCase):
    """
    Kontrol untuk pagar yang memakai snapshot.

    Kalau snapshot-nya tidak stabil atau tidak melihat berkas yang paling
    berbahaya, pagar yang memakainya akan salah dan diam.
    """

    def test_snapshot_is_deterministic(self):
        self.assertEqual(
            _snapshot(), _snapshot(),
            "snapshot data_store/ tidak stabil — pagar yang memakainya "
            "akan melaporkan perubahan palsu",
        )

    def test_data_store_directory_exists(self):
        """
        Pagar yang memeriksa folder yang tidak ada akan selalu hijau.
        """
        self.assertTrue(
            DATA_STORE.is_dir(),
            "data_store/ tidak ditemukan di %s — pagar ini tidak akan "
            "menangkap apa pun" % REPO_ROOT,
        )

    def test_snapshot_can_see_the_kill_switch_file(self):
        """
        Kontrol negatif: pagar harus bisa MEMBEDAKAN file kill switch dari
        file lain.

        `live_counters.json` sengaja TIDAK ada di `data_store/` — tidak
        pernah ada sejak test mulai mengarahkan path-nya. Itu kondisi yang
        benar: kill switch yang sudah aktif harus muncul sebagai file.

        Yang diuji di sini bukan "file-nya ada", tapi bahwa snapshot akan
        melihatnya kalau muncul. Dicek dengan membuat file sementara di
        folder temporer dan memastikan `_snapshot` bisa memetakannya — kalau
        tidak, pagar buta tepat untuk file yang paling penting.
        """
        with tempfile.TemporaryDirectory() as tmp:
            fake = pathlib.Path(tmp) / "data_store"
            fake.mkdir()
            target = fake / "live_counters.json"
            target.write_text('{"engaged": true}', encoding="utf-8")

            # `_snapshot` membaca DATA_STORE, jadi dipanggil langsung
            # dengan monkeypatch-lite: simpan path asli lalu arahkan.
            original = globals()["DATA_STORE"]
            try:
                globals()["DATA_STORE"] = fake
                names = set(_snapshot())
            finally:
                globals()["DATA_STORE"] = original

        self.assertIn(
            "live_counters.json", names,
            "snapshot tidak memetakan file kill switch. Kalau file ini "
            "tidak terlihat, pagar tidak berguna untuk kill switch — dan "
            "kill switch justru target item (h). Terpetakan: %r"
            % sorted(names),
        )

    def test_snapshot_detects_a_content_change(self):
        """
        Kontrol negatif kedua: snapshot harus membedakan "file sama" dari
        "file ditulis ulang".

        Kalau ini hanya membandingkan ukuran, pencemar yang treacherous
        — menulis ulang `engaged` dengan panjang string yang sama — akan
        lolos.
        """
        with tempfile.TemporaryDirectory() as tmp:
            fake = pathlib.Path(tmp) / "data_store"
            fake.mkdir()
            target = fake / "counters.json"
            target.write_text('{"engaged": false}', encoding="utf-8")

            original = globals()["DATA_STORE"]
            try:
                globals()["DATA_STORE"] = fake
                before = _snapshot()
                # Panjang identik: "false" (5) -> "true!" (5).
                target.write_text('{"engaged": true!}', encoding="utf-8")
                after = _snapshot()
            finally:
                globals()["DATA_STORE"] = original

        self.assertNotEqual(
            before, after,
            "snapshot tidak membedakan isi yang berbeda dengan panjang "
            "sama — pencemar yang menulis ulang state tanpa mengubah "
            "ukuran akan lolos",
        )


class TestKillSwitchStateIsNotTracked(unittest.TestCase):
    """State kill switch tidak boleh ikut ter-commit."""

    def test_live_counters_is_gitignored(self):
        """
        `data_store/live_counters.json` harus diabaikan git.

        File ini berisi `engaged`, yaitu kill switch. Kalau ter-commit,
        operator berikutnya yang `git clone` akan mewarisi kill switch
        milik mesin ini. Kalau tidak diabaikan tapi juga tidak ter-commit,
        file itu muncul sebagai `??` di setiap `git status` — dan
        akhirnya ada yang commit tanpa sengaja.
        """
        gitignore = REPO_ROOT / ".gitignore"
        self.assertTrue(gitignore.is_file(), ".gitignore tidak ditemukan")

        patterns = [
            line.strip() for line in
            gitignore.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        ]
        self.assertTrue(
            [p for p in patterns
             if "data_store" in p or "live_counters" in p or p == "*"],
            ".gitignore tidak punya aturan untuk "
            "data_store/live_counters.json",
        )

    def test_live_counters_not_tracked(self):
        """
        Dan file itu tidak boleh sudah tercatat di git.

        `cwd` harus REPO_ROOT secara eksplisit: fixture `isolate_state_paths`
        menjalankan `os.chdir(tmp_path)`, jadi path relatif akan mencari git
        di folder temporer dan naik ke atas sana — hasilnya `returncode != 0`
        yang SALAH, yaitu test ini akan selalu hijau tanpa mengecek apa pun.
        """
        import subprocess

        tracked = subprocess.run(
            ["git", "ls-files", "--", "data_store/live_counters.json"],
            cwd=str(REPO_ROOT), capture_output=True, text=True,
        )
        self.assertEqual(
            tracked.returncode, 0,
            "git gagal dijalankan dari REPO_ROOT (rc=%d): %s — test ini "
            "tidak membuktikan apa pun"
            % (tracked.returncode, tracked.stderr.strip()),
        )
        self.assertNotIn(
            "live_counters.json", tracked.stdout,
            "data_store/live_counters.json TER-TRACK di git. Isinya berisi "
            "kill switch; mesin lain akan mewarisi state ini.",
        )

    def test_gitignore_rule_is_actually_effective(self):
        """
        Kontrol negatif: aturan .gitignore harus benar-benar berlaku.

        `test_live_counters_is_gitignored` hanya membaca teks. Kalau
        polanya salah ketik (`data_store/*.jsn`), file tetap tidak
        ter-track dan test pertama tetap hijau — padahal tidak ada
        perlindungan sama sekali.
        """
        import subprocess

        result = subprocess.run(
            ["git", "check-ignore", "-v",
             "data_store/live_counters.json"],
            cwd=str(REPO_ROOT), capture_output=True, text=True,
        )
        self.assertEqual(
            result.returncode, 0,
            "gitignore tidak berlaku untuk data_store/live_counters.json. "
            "File itu akan muncul sebagai `??` di setiap `git status` dan "
            "akhirnya ter-commit tanpa sengaja. (stderr: %s)"
            % result.stderr.strip(),
        )


class TestSuiteDidNotWriteToDataStore(unittest.TestCase):
    """
    Pagar terakhir: `data_store/` harus SAMA persis sebelum dan sesudah suite.

    Fixture `isolate_state_paths` mencegah test yang membaca path
    relatif lewat cwd. Tapi ada dua kelas pencemar yang tidak bisa
    dicegah fixture:

    1. Penulis via path absolut yang dibentuk dari lokasi repo.
    2. Penulis yang terjadi di luar siklus test — misalnya di
       `pytest_configure`, `pytest_sessionfinish`, atau teardown modul.

    Pagar ini menangkap keduanya, karena yang dia periksa adalah FAKTA:
    isi folder sama atau tidak.

    SNAPSHOT DIAMBIL DARI `pytest_configure`
    ---------------------------------------
    Snapshot harus diambil SEBELUM test pertama jalan, kalau tidak test
    pertama yang menulis sudah terpotong dari pembanding dan tidak akan
    pernah dilaporkan.
    """

    def test_data_store_unchanged_during_session(self):
        baseline = _BASELINE.get("data_store")
        if baseline is None:
            # `pytest_configure` tidak jalan (mis. file ini dijalankan
            # sendiri lewat `python tests/test_state_isolation.py`).
            # Lewati, bukan gagal: fail di sini akan membuat file tidak
            # bisa dipakai di luar pytest sama sekali.
            self.skipTest("baseline diambil oleh pytest_configure")

        now = _snapshot()
        added = sorted(set(now) - set(baseline))
        removed = sorted(set(baseline) - set(now))
        changed = sorted(
            name for name in set(now) & set(baseline)
            if now[name] != baseline[name]
        )

        problems = []
        for name in added:
            problems.append("DIBUAT   %s" % name)
        for name in removed:
            problems.append("DIHAPUS  %s" % name)
        for name in changed:
            problems.append("DIUBAH   %s  (%s -> %s)"
                            % (name, baseline[name][0], now[name][0]))

        self.assertEqual(
            problems, [],
            "SUITE MENYENTUH data_store/:\n  "
            + "\n  ".join(problems)
            + "\n\n  Test tidak boleh menulis ke folder state produksi. "
              "Kalau ini memang disengaja, arahkan path-nya ke tmp_path "
              "atau setel TRADEBOT_SKIP_POLLUTION_GUARD=1 untuk "
              "menginvestigasi — jangan commit dengan folder state "
              "berubah.",
        )


if __name__ == "__main__":
    unittest.main()