# ===========================================================================
#  CMakeLists.txt — build modul C++20 `cpp_microstructure`
# ===========================================================================
#  Build:
#     cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
#     cmake --build build --config Release
#
#  Output: `build/cpp_microstructure.{pyd,so}` di root project, supaya
#  `import cpp_microstructure` langsung berhasil tanpa setting PYTHONPATH.
#
#  Alternatif tanpa CMake (setuptools):
#     python setup.py build_ext --inplace
#
#  Dengan sengaja TIDAK memaksa flags agresif. `-Ofast` mengaktifkan
#  `-ffast-math`, yang mengizinkan reordering floating point — dan itu
#  undermines paritas floating point dengan `PythonKernel`. Lihat
#  `tests/test_cpp_kernel.py`. Kita mau cepat, tapi tidak boleh mengorbankan
#  kebenaran angka.
# ===========================================================================

cmake_minimum_required(VERSION 3.18)
project(cpp_microstructure LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Release CACHE STRING "" FORCE)
endif()

# ---------------------------------------------------------------------------
# pybind11
# ---------------------------------------------------------------------------
find_package(Python3 COMPONENTS Interpreter Development.Module REQUIRED)

# pybind11 bisa datang dari pip. Tanya Python-nya lebih dulu — itu jauh
# lebih andal daripada find_package yang butuh CMAKE_PREFIX_PATH yang benar.
execute_process(
    COMMAND "${Python3_EXECUTABLE}" -m pybind11 --cmakedir
    OUTPUT_VARIABLE PYBIND11_CMAKE_DIR
    OUTPUT_STRIP_TRAILING_WHITESPACE
    ERROR_QUIET
    RESULT_VARIABLE PYBIND11_LOOKUP_RESULT
)

if(PYBIND11_LOOKUP_RESULT EQUAL 0 AND EXISTS "${PYBIND11_CMAKE_DIR}")
    list(APPEND CMAKE_PREFIX_PATH "${PYBIND11_CMAKE_DIR}")
    message(STATUS "pybind11 ditemukan lewat pip: ${PYBIND11_CMAKE_DIR}")
else()
    message(STATUS "pybind11 tidak ditemukan lewat pip, mencoba find_package")
endif()

find_package(pybind11 CONFIG REQUIRED)
message(STATUS "pybind11: ${pybind11_VERSION}")

# ---------------------------------------------------------------------------
# Target
#
# PERHATIKAN URUTAN: target HARUS dibuat SEBELUM `target_include_directories`
# dan `target_compile_definitions` dipanggil untuk namanya. CMake memproses
# command tersebut di Configure-time, jadi menunjuk target yang belum ada
# menghasilkan error "No target ...", bukan warning yang bisa diabaikan.
# Karena itu blok upstream simdjson diletakkan DI BAWAH blok target ini.
# ---------------------------------------------------------------------------
pybind11_add_module(cpp_microstructure
    cpp/microstructure_kernel.cpp
    cpp/bindings.cpp
)

# `cpp/` untuk include berbentuk "include/foo.h"; `cpp/include/` untuk
# include bentuk "foo.h". Keduanya dipakai, jadi keduanya wajib ada di path.
target_include_directories(cpp_microstructure PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/cpp
    ${CMAKE_CURRENT_SOURCE_DIR}/cpp/include
)

# ---------------------------------------------------------------------------
# Upstream simdjson (opsional)
# ---------------------------------------------------------------------------
# Kalau HL_JSON_USE_SIMDJSON=ON dan SIMDJSON_ROOT diisi, kita pakai upstream
# simdjson sungguhan. Selain itu, header `cpp/include/simdjson.h` berisi
# parser zero-copy khusus yang ditulis sendiri — lihat komentar di file itu
# untuk alasan kenapa keduanya dibedakan.
option(HL_JSON_USE_SIMDJSON "Pakai upstream simdjson, bukan parser bawaan" OFF)
set(SIMDJSON_ROOT "" CACHE PATH "Path ke source upstream simdjson")

if(HL_JSON_USE_SIMDJSON)
    if(SIMDJSON_ROOT AND EXISTS "${SIMDJSON_ROOT}/singleheader/simdjson.h")
        target_include_directories(cpp_microstructure PRIVATE "${SIMDJSON_ROOT}/singleheader")
        target_compile_definitions(cpp_microstructure PRIVATE HL_JSON_USE_SIMDJSON=1)
        message(STATUS "Menggunakan upstream simdjson dari ${SIMDJSON_ROOT}")
    else()
        message(FATAL_ERROR
            "HL_JSON_USE_SIMDJSON=ON tapi SIMDJSON_ROOT tidak valid. "
            "Isi dengan path yang memuat singleheader/simdjson.h, atau "
            "matikan opsi ini untuk memakai parser bawaan.")
    endif()
else()
    message(STATUS "Menggunakan parser zero-copy bawaan (cpp/include/simdjson.h)")
endif()


# Optimasi ada, tapi JANGAN fast-math. lihat catatan di header file ini.
if(MSVC)
    target_compile_options(cpp_microstructure PRIVATE /O2 /W4)
else()
    target_compile_options(cpp_microstructure PRIVATE
        -O2
        -Wall
        -Wextra
        -fvisibility=hidden
    )

    # ---------------------------------------------------------------------------
    # Link runtime MinGW secara STATIS.
    #
    # Tanpa ini, `.pyd` hasil build MinGW bergantung pada `libgcc_s_seh-1.dll`,
    # `libwinpthread-1.dll`, dan `libstdc++-6.dll` yang hanya ada di
    # `C:\msys64\ucrt64\bin`. Kalau folder itu tidak ada di PATH mesin tujuan,
    # gejalanya `ImportError: DLL load failed while importing cpp_microstructure`
    # — yang TERLIHAT seperti modul rusak, padahal kodenya baik-baik saja dan
    # semua test lain hijau.
    #
    # Menaut statis membuat `.pyd` mandiri: bisa disalin ke mesin lain tanpa
    # MSYS terpasang. Yang tetap harus ada hanya runtime Python itu sendiri.
    #
    # Catatan: kita TIDAK memakai `-static` penuh. Linker MinGW akan menarik UCRT
    # static, yang justru membuat modul rapuh di Windows lama. Yang ditaut
    # statis hanya pustaka MinGW.
    #
    # Setelah build, dependensi yang tersisa hanya boleh: DLL Windows/ucrt
    # (api-ms-win-crt-*), python3xx.dll, dan KERNEL32. Kalau `libwinpthread-1.dll`
    # atau `libstdc++-6.dll` masih muncul, berarti ada yang lupa ditaut.
    # Verifikasi: objdump -p cpp_microstructure*.pyd | findstr "DLL Name"
    # ---------------------------------------------------------------------------
    target_link_options(cpp_microstructure PRIVATE
        -static-libgcc
        -static-libstdc++
        -Wl,-Bstatic
        -lwinpthread
        -Wl,-Bdynamic
    )
endif()

# Place output di root project supaya `import cpp_microstructure` berhasil
# tanpa PYTHONPATH tambahan.
set_target_properties(cpp_microstructure PROPERTIES
    LIBRARY_OUTPUT_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}"
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}"
)
