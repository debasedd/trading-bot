"""Pembantu menjalankan test dan menulis hasilnya ke file."""
import io
import logging
import sys
import unittest

logging.disable(logging.CRITICAL)

name = sys.argv[1] if len(sys.argv) > 1 else None
try:
    suite = (unittest.TestLoader().loadTestsFromName(name) if name
             else unittest.TestLoader().discover("tests"))
except BaseException as exc:  # noqa: BLE001
    import traceback

    with open("_t.txt", "w", encoding="utf-8") as fh:
        fh.write("LOAD GAGAL: {}\n{}".format(
            exc, traceback.format_exc()))
    raise SystemExit(2)

buf = io.StringIO()
try:
    result = unittest.TextTestRunner(stream=buf, verbosity=2).run(suite)
except BaseException:  # noqa: BLE001
    import traceback

    with open("_t.txt", "w", encoding="utf-8") as fh:
        fh.write("RUN GAGAL\n" + traceback.format_exc())
    raise SystemExit(2)

with open("_t.txt", "w", encoding="utf-8") as fh:
    fh.write(buf.getvalue())
    fh.write("\nrun={} errors={} failures={} skipped={}\n".format(
        result.testsRun, len(result.errors), len(result.failures),
        len(result.skipped)))
    for test, tb in result.errors + result.failures:
        fh.write("\n=== {}\n{}\n".format(test, tb))
