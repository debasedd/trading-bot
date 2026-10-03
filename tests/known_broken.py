"""
Dekorator yang menandai sebuah test sebagai "belum bisa lulus".

MASALAH YANG INI SELESAIKAN
----------------------------
Test reproduksi defect di `test_repro_live_defects.py` HARUS gagal
selama defect-nya masih ada. Kalau tidak:

  * pytest perlu tahu itu}xfail, supaya CI tidak merah dan test tidak
    bisa "diam-diam lolos" kalau nanti diperbaiki.
  * unittest perlu hal yang sama, karena repo ini dijalankan dengan
    KEDUA runner (lihat README bagian "Menjalankan Seluruh Unit Test").
    unittest tidak mengenal `pytest.mark.xfail` sama sekali, jadi 14
    reproduksi akan muncul sebagai kegagalan palsu di sana.

`pytest.mark.xfail(strict=True)` hanya Rondong ke pytest.
`unittest.expectedFailure` hanya Rondong ke unittest. Keduanya tidak
bisa dipakai bersamaan di satu dekorator.

Solusinya: dekorator di bawah memasang KEDUA penanda pada saat yang
sama. Hasilnya:

  * pytest: xfail(strict=True). Kalau defect diperbaiki dan test mulai
    lulus, pytest melaporkan XPASS(strict) dan suite BERGAGAL.
  * unittest: expectedFailure. Kalau test mulai lulus, unittest
    melaporkan "unexpected success" dan suite BERGAGAL.

Jadi di kedua runner, memperbaiki defect TANPA mengubah test ini akan
men-visible sebagai kegagalan -- persis yang diminta.
"""
import functools
import unittest

import pytest


def known_broken(reason):
    """
    Tandai test sebagai belum bisa lulus, untuk pytest DAN unittest.

    Args:
        reason: string yang menjelaskan defect apa yang masih ada.

    Returns:
        decorator yang bisa dipasang di atas method async maupun sync.
    """
    xfail = pytest.mark.xfail(strict=True, reason=reason)
    expected = unittest.expectedFailure

    def decorate(func):
        # unittest lebih dulu: ia membungkus method jadi TestCase yang
        # deductible, jadi functools.wraps harus diupiter aplikasi
        # ke bagian dalam, bukan ke bagian luar.
        wrapped = expected(func)
        wrapped = xfail(wrapped)
        return wrapped

    return decorate
