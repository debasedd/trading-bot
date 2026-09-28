"""
_audit_refs.py — Memeriksa bahwa SETIUP akses lintas-modul benar-benar ada.

Bug `enable_vt_processing` (yang tidak ada; aslinya `enable_wt_processing`)
tidak lolos ke test karena test mem-patch fungsi pemanggilnya. Pemeriksa
statis ini menangkap kelas bug yang sama tanpa perlu menjalankan apa pun:
nama yang dipanggil tapi tidak didefinisikan.

Dipakai sebagai gerbang: kalau ada referensi rusak, skrip ini keluar
dengan kode selain nol dan build berhenti di situ.
"""
import ast
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).parent

# Alias modul lokal -> nama modul yang diimpor.
ALIASES = {
    "tui": "trading.live.tui",
    "console": "trading.live.console",
    "c": "trading.live.console",
}


def module_defs(path: pathlib.Path) -> set:
    """Nama yang didefinisikan di level modul dan di dalam kelas."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target,
                                                            ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add((alias.asname or alias.name).split(".")[0])
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    names.add(sub.name)
                elif isinstance(sub, ast.AnnAssign) and isinstance(
                        sub.target, ast.Name):
                    names.add(sub.target.id)
    return names


def attribute_refs(path: pathlib.Path):
    """Semua `alias.atribut` yang muncul di file."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id in ALIASES):
            yield node.value.id, node.attr, node.lineno


def scan():
    """Kembalikan (daftar referensi, daftar masalah) di seluruh repo."""
    live = ROOT / "trading" / "live"
    known = {name: module_defs(live / (name + ".py"))
             for name in ("tui", "console")}
    problems = []
    total = 0

    targets = [ROOT / "run.py", live / "console.py", live / "tui.py"]
    targets += sorted((ROOT / "tests").glob("test_live_*.py"))

    for path in targets:
        if not path.exists():
            continue
        for alias, attr, lineno in attribute_refs(path):
            total += 1
            if attr.startswith("__") or attr in known[alias]:
                continue
            problems.append("{}:{}  {}.{}  (tidak didefinisikan)".format(
                path.relative_to(ROOT), lineno, alias, attr))
    return total, problems


def main() -> int:
    """
    Periksa referensi lintas-modul.

    Bug `enable_vt_processing` lolos ke test karena semua test mem-patch
    fungsi pemanggilnya, jadi nama yang salah di bawahnya tidak pernah
    dieksekusi. Pemeriksa statis menangkapnya tanpa perlu menjalankan apa
    pun — dan tidak bisa dilewati dengan mem-patch apa pun.
    """
    total, problems = scan()
    if problems:
        print("REFERENSI RUSAK ({}):".format(len(problems)))
        for line in problems:
            print("  " + line)
        return 1
    print("Semua {} referensi lintas-modul valid.".format(total))
    return 0


if __name__ == "__main__":
    sys.exit(main())

