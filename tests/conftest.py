"""
conftest.py — jaring pengaman pencemar state global.

MASALAH YANG INI SELESAIKAN
--------------------------
`market_store` adalah SINGKTON modul yang TIDAK punya API reset. Test yang
menulis ke sana lalu selesai meninggalkan state-nya untuk test berikutnya.
Dampaknya nyata dan sudah terukur:

    tests/test_direction_agents.py::TestMicrostructureAgent::
        test_positive_funding_is_contrarian_short
        -> market_store.set_funding("BTC/USDT:USDT", 0.0002)

lalu test akuntansi di file lain menghitung `funding_cost()` dari rate yang
TERTINGGAL itu dan gagal dengan selisih 1.9998 — persis
`notional x 0.0002 x 1 periode`. Delta debugging (ddmin atas urutan seed 1)
menunjuk satu test itu sebagai penyebab tunggal; keempat test yang gagal
hijau lagi kalau test itu dihapus dari urutannya.

Test yang gagal begini MENYERTAKAN kesimpulan salah: ia tidak menguji
logikanya sendiri, ia menguji state yang dicuri test lain.

APA YANG FILE INI LAKUKAN
-------------------------
Snapshot state global SEBELUM tiap test, bandingkan SESUDAH, dan gagal
dengan menyebut test pembocornya secara langsung. Deteksi ada di fixture,
bukan di hipsotesis analis — jadi pencemar berikutnya tidak perlu
diperkirakan lagi, ia akan menunjuk dirinya sendiri.

Snapshot sengaja memuat:
  * `market_store` — SEMUA dict-nya, disalin dalam
  * `os.environ`
  * `core.config._config` — identitas DAN isi risk/fees/scalping
  * `database.db._db`
  * dict/list modul yang bisa ditemukan lewat `sys.modules`

Yang DILEWATI dan alasannya:
  * counter AkShare/requests dan cache internalledger — bukan milik repo ini
  * dict yang ditulis sekali saat import lalu tidak pernah berubah lagi
  * `re.compile` cache — immutable secara fungsional

Mematikan guard: `TRADEBOT_SKIP_POLLUTION_GUARD=1`.
"""
import os
import sys

import pytest


# ---------------------------------------------------------------------------
# Normalisasi state
# ---------------------------------------------------------------------------

# pytest sendiri menulis dua variabel ini selama run. Bukan pencemar test,
# dan membandingkan nilainya hanya menghasilkan noise.
_PYTEST_OWNED_ENV = frozenset({"PYTEST_CURRENT_TEST", "PYTEST_ADDOPTS"})


def _norm_env():
    """Lingkungan sebagai dict, diurutkan supaya perbandingan deterministik."""
    return {k: v for k, v in sorted(os.environ.items()) if k not in _PYTEST_OWNED_ENV}


def _norm_market_store():
    """
    market_store disalin dalam.

    `deque` disalin ke list supaya perbandingan tidak melihat perbedaan
    identitas objek, dan `dict` di dalam book disalin satu tingkat supaya
    mutasi in-place (bukan reassign) juga tertangkap.
    """
    from core.market_store import market_store

    snap = {}
    for name, value in market_store.__dict__.items():
        snap[name] = _copy_state(value)
    return snap


def _copy_state(value):
    """Salin struktur sedalam mungkin tanpa memanggil __eq__ pada objek aneh."""
    if isinstance(value, dict):
        return {k: _copy_state(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [_copy_state(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_copy_state(v) for v in value), key=repr)
    try:
        from collections import deque
        if isinstance(value, deque):
            return [_copy_state(v) for v in value]
    except ImportError:  # pragma: no cover
        pass
    try:
        import numpy as np
        if isinstance(value, np.ndarray):
            return value.tolist()
        if isinstance(value, np.generic):
            return value.item()
    except ImportError:  # pragma: no cover
        pass
    return value


def _norm_config():
    """
    Identitas DAN isi config singleton.

    Yang penting adalah ISI, bukan identitas: test yang menulis
    `get_config().fees.taker = 0` mengubah objek yang sama tanpa pernah
    memanggil `reload_config()`, jadi membandingkan `id()` saja lolos.
    """
    from core import config as config_mod

    cfg = config_mod._config
    if cfg is None:
        return None
    return {
        "id": id(cfg),
        "risk": _copy_state(vars(cfg.risk)) if hasattr(cfg, "risk") else None,
        "fees": _copy_state(vars(cfg.fees)) if hasattr(cfg, "fees") else None,
        "scalping": _copy_state(vars(cfg.scalping)) if hasattr(cfg, "scalping") else None,
        "account": _copy_state(vars(cfg.account)) if hasattr(cfg, "account") else None,
    }


def _norm_db():
    """Koneksi DB modul — path-nya menentukan DB mana yang dibaca test."""
    try:
        from database import db as db_mod
    except Exception:  # pragma: no cover - modul mungkin belum ada
        return None
    conn = getattr(db_mod, "_db", None)
    if conn is None:
        return None
    return {"id": id(conn), "path": getattr(conn, "db_path", None) or getattr(conn, "path", None)}


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------

# Hanya modul milik repo ini. Modul pihak ketiga punya cache internalnya
# sendiri yang bukan bleed dari test kita, dan memantaunya hanya menambah
# noise — persis noise yang membuat probe STATE.md sebelumnya meleset.
_REPO_ROOTS = ("core.", "trading.", "agents.", "analysis.", "database.", "ml.", "dashboard.")

# Nama yang diabaikan: diisi sekali saat import, atau sengajayang bisa berubah dan
# hanya dibaca sebagai cache yang tidak memengaruhi hasil test.
_IGNORE_NAMES = frozenset({
    "_KNOWN_FIELDS",   # dict field->type, diisi load_config, dibaca validasi
})


def _module_dicts(known_modules):
    """
    dict/list/set mutable di level modul, HANYA untuk modul yang sudah diimpor
    sebelum test berjalan.

    Modul yang baru diimpor di tengah test DILEWATI: mengimpor modul bukan
    pencemar, itu cuma memuat state awal. Tanpa pengecualian ini, test
    pertama yang menyentuh modul tertentu akan dilaporkan bocor padahal
    tidak ada yang menulis apa pun.
    """
    found = {}
    for name, mod in list(sys.modules.items()):
        if not name.startswith(_REPO_ROOTS) or mod is None or name not in known_modules:
            continue
        try:
            items = vars(mod)
        except Exception:  # pragma: no cover
            continue
        for attr, value in items.items():
            if attr.startswith("__") or attr in _IGNORE_NAMES:
                continue
            if isinstance(value, (dict, list, set, frozenset)):
                try:
                    found[f"{name}.{attr}"] = _copy_state(value)
                except Exception:  # pragma: no cover
                    continue
    return found


def _take_snapshot(known_modules):
    return {
        "env": _norm_env(),
        "market_store": _norm_market_store(),
        "config": _norm_config(),
        "db": _norm_db(),
        "modules": _module_dicts(known_modules),
    }


# ---------------------------------------------------------------------------
# Diff yang bisa dibaca manusia
# ---------------------------------------------------------------------------

def _diff(before, after, path=""):
    """Beda dua snapshot, dibatasi supaya pesan error tetap terbaca."""
    out = []
    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(set(before) | set(after), key=str):
            child = f"{path}.{key}" if path else str(key)
            if key not in before:
                out.append(f"{child}: DITAMBAHKAN (tidak ada sebelumnya)")
            elif key not in after:
                out.append(f"{child}: DIHAPUS")
            else:
                out.extend(_diff(before[key], after[key], child))
    elif before != after:
        b, a = repr(before), repr(after)
        if len(b) > 120:
            b = b[:117] + "..."
        if len(a) > 120:
            a = a[:117] + "..."
        out.append(f"{path}: {b}  ->  {a}")
    return out


def _test_id(item):
    try:
        return item.nodeid
    except AttributeError:  # pragma: no cover
        return str(item)


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

def pytest_ignore_collect(collection_path, config):
    """
    Jangan kumpulkan file di `tests/_probe_tmp/`.

    `test_pollution_guard_detects.py` menulis probe sintetis ke sana untuk
    membuktikan guard benar-benar menangkap pencemar. Kalau test itu selesai
    normal, filenya terhapus sendiri — tapi kalau proses mati di tengah
    jalan, sisa file ikut ter-collection dan ikut jadi "test" yang gagal
    dengan pesan guard. Itu persis kebisingan yang harus dihindari.
    """
    parts = str(collection_path).replace("\\", "/").split("/")
    if "_probe_tmp" in parts:
        return True
    return None


def pytest_sessionstart(session):
    """
    Muat config SEBELUM test pertama, bukan saat test pertama memintanya.

    `get_config()` bersifat lazy, jadi tanpa ini `_config` berubah dari
    `None` menjadi objek utuh di tengah satu test. Guard akan melaporkannya
    sebagai pencemar padahal itu inisialisasi sekali — persis noise yang
    membuat probe sebelumnya gagal menemukan pembocor sungguhan.

    Memuatnya di sini juga membuat guard lebih tajam: setelah sesi dimulai,
    `_config` selalu terisi, jadi perubahan ISI config (batas breaker, tarif
    fee) yang ditulis test mana pun langsung terlihat sebagai perubahan nilai,
    bukan sebagai kemunculan objek.
    """
    try:
        from core.config import get_config
        get_config()
    except Exception as exc:  # pragma: no cover
        print("conftest: gagal memuat config awal (%r); guard akan noisy" % (exc,))


@pytest.fixture(autouse=True)
def guard_global_state(request):
    """
    Tiap test WAJIB mengembalikan state global persis seperti sebelum masuknya.

    Kegagalan di sini naming test-nya sendiri sebagai pembocor, jadi tidak
    ada lagi perlu menebak-nebak test mana yang mencuri apa.
    """
    if os.environ.get("TRADEBOT_SKIP_POLLUTION_GUARD") == "1":
        yield
        return

    # Modul yang sudah ada saat test DIMULAI. Yang diimpor di tengah test
    # tidak masuk pembanding — memuat modul bukan pencemar state.
    known = frozenset(sys.modules)
    before = _take_snapshot(known)
    yield
    after = _take_snapshot(known)

    leaks = _diff(before, after)
    if not leaks:
        return

    # Dedupe baris identik supaya satu kebocoran dict dengan banyak kunci
    # tidak menutupi kebocoran lain di report yang sama.
    seen = set()
    unique = []
    for line in leaks:
        head = line.split(":")[0]
        if head in seen:
            continue
        seen.add(head)
        unique.append(line)

    shown = unique[:25]
    pytest.fail(
        "PENCEMAR STATE GLOBAL oleh test ini.\n"
        f"  test  : {_test_id(request.node)}\n"
        f"  state : {len(unique)} perbedaan, ditampilkan {len(shown)}\n"
        "\n"
        + "\n".join("    " + line for line in shown)
        + "\n"
        + ("\n  ... dan %d lagi\n" % (len(unique) - len(shown)) if len(unique) > len(shown) else "\n")
        + "  Test yang ditulis untuk mendeteksi ini tidak bisa dipercaya sampai\n"
        "  state pulih. Pulihkan state di tearDown/addCleanup, atau jangan tulis\n"
        "  state global sama sekali.",
        pytrace=False,
    )