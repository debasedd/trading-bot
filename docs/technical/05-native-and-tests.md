# Native Extension, Build and Tests

Two independent halves that share one discipline: **never trade an unverified number.** The
native kernel exists so the OFI hot path can be 13x faster without changing one agent's
source, and 32 executed tests exist so "faster" can never quietly mean "different". The ML
model lifecycle and the cost model are built the same way — a formula that is wrong in an
unloud direction is worse than one that raises.

All counts, line numbers and constants in this document were re-derived from the working
tree on 2026-09-29. Where the stale `docs/context/CONTEXT.md` disagrees, the disagreement
is named.

---

## 1. The native extension

### 1.1 What it is, and where it sits

`cpp_microstructure` is a CPython extension that computes Order Flow Imbalance, relative
spread and depth imbalance from an L2 order book. It is a bit-for-bit reimplementation of
`core/microstructure.py::PythonKernel`, not a different algorithm with the same name. The
swap happens through a module-level registry variable, so no agent, no dashboard and no test
changes when the native kernel is active.

```
                        production Python
  ────────────────────────────────────────────────────────────────────────────
  data/hyperliquid_feed.py:411  _handle_message(raw)      run via to_thread :534
      channel == "l2Book"                                [SOLE production write path]
        book = _parse_book(...)   :313   -> list[list[float]], sz<=0 dropped
        ├─► market_store.set_order_book(symbol, book)          :453
        └─► microstructure.ingest_l2(symbol, bids, asks)      :458
              core/microstructure.py:218-220  _KERNEL.ingest_l2(...)
                CppMicrostructureKernel.ingest_l2   :281-282
                  ── pybind11, GIL HELD ─────────────────────────────
                  bindings.cpp:80-94   ingest_l2
                    book_from_python(bids, asks, book)   bindings.cpp:32-44  ← noexcept
                    reserve_symbol()  ── false ⇒ RuntimeError      bindings.cpp:87-90
                    ingest_book → BookStore::set_book
                                            microstructure_kernel.cpp:147-156

  agents/direction_agents.py:125  calculate_order_flow_imbalance(book, symbol=symbol)
        microstructure.order_flow_imbalance(symbol, depth=5)   probability_engine.py:90
          CppMicrostructureKernel.order_flow_imbalance   core/microstructure.py:284-288
            float(ofi), float(spread)
            ── pybind11, GIL RELEASED ────────────────────────────
            microstructure_kernel.cpp:287-301
              store_.find(symbol)        locked, builds std::string, (void)len   :137-145
              book_ofi(*book, depth)     NO LOCK HELD                             :207-254
                weighted_volume(sizes, n) == python  size * (1.0 - 0.1*i)         :192-198
                ofi = (bv-av)/(bv+av), clamped;  rel_spread = max(ask-bid,0)/mid

  ── bypassed, never reaches C++ ──────────────────────────────────────────────
  update_callbacks.py:1311  calculate_order_flow_imbalance(ob)   [symbol omitted]
      probability_engine.py:85  kernel = PythonKernel()   throwaway
      HUD "slippage" KPI computed from that Python kernel's rel_spread (:1314)

  ── native surface with ZERO production callers ─────────────────────────────
      depth_imbalance  core/microstructure.py:290      reset      :294
      ingest_json      core/microstructure.py:303      symbol_count :306
      level_weight     bindings.cpp:160                MAX_LEVELS / __version__ :162-163
```

### 1.2 Exported surface, verbatim

Everything Python-visible is defined in `cpp/bindings.cpp` (164 lines). Nothing else in
`cpp/` is exported.

| Symbol | Kind | Signature | GIL | Production callers |
|---|---|---|---|---|
| `MicrostructureKernel` | class | — | — | constructor only (`core/microstructure.py:279`) |
| `.__init__` | ctor | `max_symbols: int = 64` | held | 1 |
| `.order_flow_imbalance` | method | `(symbol: str, depth: int = 5) -> (float, float)` | **released** `bindings.cpp:63` | 1 (`probability_engine.py:90`) |
| `.depth_imbalance` | method | `(symbol: str, depth: int = 5) -> float` | **released** `:74` | 0 |
| `.ingest_l2` | method | `(symbol: str, bids, asks) -> None` | **held** `:84` | 1 (`hyperliquid_feed.py:458`) |
| `.ingest_json` | method | `(payload: bytes) -> str \| None` | **released** `:113` | **0** |
| `.reset` | method | `(symbol: str \| None = None) -> None` | **released** `:146` | 0 |
| `.symbol_count` | method | `() -> int` | **released** `:155` | 0 |
| `level_weight` | function | `(index: int) -> float` | held | 0 |
| `MAX_LEVELS` | attr | `int` | — | 0 (boot log only) |
| `__version__` | attr | `str` = `"1.0.0"` | — | 0 (boot log only) |

Runtime-verified against the shipped `.pyd`:

```
version 1.0.0  MAX_LEVELS 32
module attrs : ['MAX_LEVELS', 'MicrostructureKernel', 'level_weight']
kernel attrs : ['depth_imbalance', 'ingest_json', 'ingest_l2',
                'order_flow_imbalance', 'reset', 'symbol_count']
level_weight(0..11) = [1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1, 0.0, -0.1]
```

Note what is **absent** from `dir(kernel)`: `n_bid`, `n_ask`, `bid_price`, `ask_price`,
`overflow_bid`, `overflow_ask`. They exist in C++ (`microstructure_kernel.h:43-52`) and no
binding exports them, so the 32-level truncation is unobservable from Python (see §1.7).

### 1.3 The Python↔C++ data contract

**Direction 1 — Python to C++.** `ingest_l2(symbol, bids, asks)`.

`book_from_python` (`bindings.cpp:32-44`) does the only Python-object iteration in the hot
path, and it must hold the GIL (`bindings.cpp:12-13`, and the comment at `:84` records that
the ordering is deliberate):

```cpp
void book_from_python(const py::object& bids, const py::object& asks,
                      tradebot::Book& book) noexcept {
    book.clear();
    for (const auto& item : bids) {
        const auto pair = item.cast<std::pair<double, double>>();
        book.ingest_bid(pair.first, pair.second);
    }
    for (const auto& item : asks) { ... book.ingest_ask(...); }
}
```

| Accepted by the binding | Rejected |
|---|---|
| `list[list[float]]` — what `_parse_book` produces (`hyperliquid_feed.py:327-329`) | `list[dict]`, bare `float`, `None`, `range()`, 1-D numpy, numpy scalar |
| `list[tuple[float,float]]`, `tuple[tuple]`, `int`/`bool` tuples | 3-tuples, `("1.0", 2.0)` — any element that is not a 2-sequence of numbers |
| numpy 2-D `float64`/`float32`/`object`, including non-contiguous `b[::2]` | |

The rejection path is **not** a Python exception. `book_from_python` is declared `noexcept`
while `py::object::cast<>` throws; an escaping C++ exception across a `noexcept` boundary
is `std::terminate`, so a malformed level element kills the process with exit code
`3221226505` (`0xC0000409`), no traceback, uncatchable by any `try`/`except`.
`PythonKernel` raises a clean `TypeError`/`ValueError` for the same inputs
(`core/microstructure.py:117-120`). Production always sends `list[list[float]]`, so this is
latent — but it is one refactor away from a process kill in the L2 hot path.

**Direction 2 — C++ to Python.** `order_flow_imbalance` returns
`std::pair<double,double>`, coerced at `core/microstructure.py:288` to `float(ofi),
float(spread)`. `depth_imbalance` returns a bare `double`.

**The store contract.** `max_symbols` defaults to 64 (`bindings.cpp:54`,
`microstructure_kernel.h:121`). `BookStore::set_book` **never creates a symbol**
(`microstructure_kernel.cpp:147-156` — `return;` on a miss, "jangan diam-diam membuat book
baru"), so `ingest_l2` must call `reserve_symbol` first (`bindings.cpp:87-91`). A full store
becomes `RuntimeError("store mikrostruktur penuh: jumlah simbol melebihi batas")`.
`max_symbols = 0` is silently coerced to 1 (`microstructure_kernel.cpp:117`).

**`find()` ignores its length argument.** `order_flow_imbalance` and `depth_imbalance` both
`(void)len;` and call `store_.find(symbol)` (`microstructure_kernel.cpp:295`, `:305`), and
`find` builds `std::string key(symbol)` (`:139`) — a NUL-terminated read. Consequence: a
symbol containing an embedded NUL is ingested successfully and then unreadable, and every
read allocates a `std::string` (SSO-capped, so ~0 for short symbols).

**Mutex scope.** `std::mutex` guards only registration and pointer lookup
(`microstructure_kernel.h:68-72`, `:107`). `find` returns a raw pointer released from the
lock and the caller then reads book fields unlocked. The stated justification is that the
Python layer guarantees one thread touches a given book at a time via the GIL.

### 1.4 Build system

`CMakeLists.txt` (152 lines) at the repo root.

| Setting | Value | Source |
|---|---|---|
| minimum cmake | `3.18` | `:21` |
| C++ standard | `CMAKE_CXX_STANDARD 20`, `REQUIRED ON`, `EXTENSIONS OFF` | `:24-26` |
| default build type | `Release` when unset | `:29-31` |
| pybind11 discovery | `python -m pybind11 --cmakedir` first, `find_package(pybind11 CONFIG REQUIRED)` fallback | `:36-56` |
| sources | `cpp/microstructure_kernel.cpp`, `cpp/bindings.cpp` | `:67-70` |
| include paths | `cpp/` **and** `cpp/include/` (both used) | `:74-77` |
| optimisation (non-MSVC) | `-O2 -Wall -Wextra -fvisibility=hidden` | `:109-114` |
| optimisation (MSVC) | `/O2 /W4` | `:107` |
| link (non-MSVC) | `-static-libgcc -static-libstdc++ -Wl,-Bstatic -lwinpthread -Wl,-Bdynamic` | `:138-144` |
| output directory | `${CMAKE_CURRENT_SOURCE_DIR}` — the **project root**, not `build/` | `:149-152` |

Build command is documented verbatim at `CMakeLists.txt:5-6` and
`core/microstructure.py:38-39`:

```
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release
```

Because output lands in the root, a local build drops an **untracked** `*.pyd` next to
`run.py`. `.gitignore` covers `*.pyd`, `*.dylib`, `build/`, `dist/`, `libwinpthread-1.dll`
and `api-ms-win-crt-private-l1-1-0.dll`. A fresh clone therefore has no native kernel and
runs `PythonKernel` until someone builds.

**`HL_JSON_USE_SIMDJSON` is a no-op.** `CMakeLists.txt:86-99` defines the option and, when
`ON`, adds an include directory plus `-DHL_JSON_USE_SIMDJSON=1`. The macro is read by no
source file — repo-wide grep for `HL_JSON_USE_SIMDJSON` across `cpp/*.cpp` and
`cpp/include/*.h` returns exactly one hit, the comment at `cpp/include/simdjson.h:16`. The
header cannot become the forwarding shim its own comment claims (`:16-17`).

**Actual compile line** from `build/build.ninja`:

```
FLAGS = -O3 -DNDEBUG -std=c++20 -fvisibility=hidden -O2 -Wall -Wextra -fvisibility=hidden
LINK_FLAGS = -shared -static-libgcc -static-libstdc++ -Wl,-Bstatic -lwinpthread -Wl,-Bdynamic
```

`-O3` comes from CMake's `Release` default and is overridden by the later `-O2`; last flag
wins. There is no fast-math.

### 1.5 There is no SIMD, and no runtime CPU feature detection

The brief asks about "SIMD and runtime feature detection with its fallback". The honest
answer:

| Searched | Result |
|---|---|
| `immintrin`, `__m128`, `__m256`, `_mm_`, `cpuid`, `__builtin_cpu`, `-mavx`, `-msse`, `-march=`, `__AVX`, `__SSE`, `/arch:`, `vtune` across `cpp/`, `cpp/include/`, `CMakeLists.txt`, `build/build.ninja` | **zero hits** |
| optimisation flags in the real link line | `-O3` and `-O2` only |

Storage is four `std::array<double, 32>` (`microstructure_kernel.h:55-58`), the accumulation
loop is scalar (`microstructure_kernel.cpp:192-198`). The file is named `simdjson.h` and
the header explicitly disclaims being upstream simdjson (`:4-33`): it is a hand-written
zero-copy scanner purpose-built for one payload shape.

**What actually exists as runtime detection and fallback** is the *kernel* choice, not CPU
features:

| Mechanism | Location | Behaviour |
|---|---|---|
| `TRADEBOT_KERNEL=python` | `core/microstructure.py:331-334` | returns `False`, keeps `PythonKernel`. `force=True` overrides, for tests only. |
| `TRADEBOT_KERNEL=cpp` | `:336-345`, `:357-363` | import failure raises `RuntimeError` with the build instructions — **not** a silent degrade. Re-raised at `hyperliquid_feed.py:120`. |
| unset / anything else | `:347-369` | import succeeds ⇒ register; fails ⇒ one `logger.info` and `PythonKernel`. Nothing surfaces this at WARNING. |
| `register_kernel` validation | `:201-209` | requires callable `ingest_l2`, `order_flow_imbalance`, `depth_imbalance`, `reset`; raises `TypeError` naming what is missing and leaves the old kernel installed. |
| idempotence | `data/hyperliquid_feed.py:112-121` | `self._native_kernel` flag; called once at `:513`, immediately before the socket opens. |

The kernel is chosen **once, before the first WebSocket frame**, and never swapped mid-stream.
The comment at `hyperliquid_feed.py:104-106` states why: two implementations' OFI entering
the same pipeline within seconds is the failure being prevented.

Boot log line, the only reader of `__version__` and `MAX_LEVELS`
(`core/microstructure.py:351-355`):

```python
logger.info(f"Kernel mikrostruktur C++20 aktif (versi {native.__version__}, MAX_LEVELS={native.MAX_LEVELS})")
```

Measured throughput (benchmarked in-process, 20-level books, 300,000 reads):
`read_cpp ≈ 0.34 µs` vs `read_py ≈ 4.55 µs` (**≈13x**). Ingest gain is small
(`ingest_cpp ≈ 2.05 µs` vs `ingest_py ≈ 3.35 µs`, **≈1.6x**) because the write is dominated by
the per-element `py::object` cast loop, not by the copy into the fixed-size arrays.

### 1.6 SIMD-adjacent constants and the parity contract

```cpp
// C++ ONLY — file-local, inside an anonymous namespace (microstructure_kernel.cpp:55-60)
constexpr double kLevelWeight0   = 1.0;   // microstructure_kernel.cpp:57
constexpr double kLevelWeightStep = 0.1;   // microstructure_kernel.cpp:58

// their ONLY consumer — exported at bindings.cpp:160, zero production callers (see table at :79)
double level_weight(uint32_t i) { return kLevelWeight0 - kLevelWeightStep * (double)i; }  // :62-64
```

**The C++ hot path does not use them.** `weighted_volume` re-hardcodes the literals —
`total += sizes[i] * (1.0 - 0.1 * static_cast<double>(i))` (`microstructure_kernel.cpp:195`) — and
`book_ofi` calls `weighted_volume` (`:231-232`), never `level_weight`. So the named constants are
dead on the OFI path: set `kLevelWeight0` to 2.0 and the exported helper reports 2.0 while the
actual OFI math still weighs level 0 at 1.0.

**Python has no named constant at all.** `PythonKernel._weighted_volume` — the default production
kernel and the reference implementation this document names — writes the same numbers inline:
`total += size * (1.0 - 0.1 * i)` (`core/microstructure.py:127`, loop body `:126-128`).

So `1.0` and `0.1` exist twice per language, four times total, with no shared declaration:
C++ `:195` and Python `:127`. The two pairs must be edited together by hand or bit-exact OFI
parity breaks in the last digit — and nothing fails, because the only test naming
`kLevelWeight0`/`kLevelWeightStep` is a source-text grep of the C++ file
(`tests/test_cpp_kernel.py:264-281`) that never opens the Python side. The comment at
`microstructure_kernel.cpp:45-52` states the intent ("WAJIB identik dengan
`PythonKernel._weighted_volume`, yang memakai `size * (1.0 - 0.1 * i)`") — the intent is real,
the C++ constant is not the thing that enforces it.

Two consequences worth knowing:

- **`level_weight` goes negative past index 10.** `level_weight(10) == 0.0`,
  `level_weight(11) == -0.1`. A `depth > 10` on a deep book makes both sides sum negative
  and cancel, returning `(0.0, 0.0)` from both kernels alike. Production uses `depth=5`
  (`probability_engine.py:87`, `:90`), so it never bites; the tests exercise `depth` up to 99
  and treat the resulting zeros as correct parity.
- **Parity is order-dependent, and the code says so.** `microstructure_kernel.cpp:22-25`:
  *"Urutan adalah bagian dari kontrak, bukan detail implementasi: floating point tidak
  asosiatif."* The accumulation order in `weighted_volume` and the early-return order in
  `book_ofi` are both mirrored line-for-line from the Python. Suite tolerance is `1e-7`
  (`tests/test_cpp_kernel.py:37`) — far tighter than float64 needs, deliberately.

`book_ofi` (`microstructure_kernel.cpp:207-254`) reproduces Python's guard sequence exactly:
empty side → `(0.0, 0.0)`; depth clamped per side to the actual level count (`:217-218`);
`denom <= 0.0` → `(0.0, 0.0)`; `mid <= 0.0` → `(ofi, 0.0)`. `book_depth`
(`:256-285`) reproduces the fact that Python's `depth_imbalance` does **not** check for an
empty side, only `denom <= 0` (`core/microstructure.py:160-170`) — the comment at
`:258-260` says deviating would break parity.

### 1.7 Does the prebuilt binary match current source?

**Yes.** Verified four ways on 2026-09-29.

| Check | Result |
|---|---|
| `ninja -C build -n` | `ninja: no work to do.` |
| source mtimes | `simdjson.h` 20:12:02, `bindings.cpp` 22:22:19, `CMakeLists.txt` 22:25:01, `microstructure_kernel.cpp` 22:31:57, `microstructure_kernel.h` 22:33:06 |
| binary mtime | `cpp_microstructure.cp314-win_amd64.pyd`, 408,576 B, **2026-09-27 23:01:23** — later than every source |
| `build/.ninja_log` | 3 edges: `microstructure_kernel.cpp.obj → 283e529d2022a254`, `bindings.cpp.obj → 2b2a91e443440218`, `.pyd → 5303c21a3c9a7ef3` |
| runtime strings | `__version__ == "1.0.0"`, `MAX_LEVELS == 32`, `ingest_json` round-trips a real payload, non-l2Book returns `None` |

`.pyd` PE facts (parsed directly):

- PE32+ (`magic 0x20b`), import table = **12 entries**: `KERNEL32.dll`, nine
  `api-ms-win-crt-*-l1-1-0.dll` (convert, environment, heap, locale, **private**, runtime,
  stdio, string, utility), `libwinpthread-1.dll`, `python314.dll`.
- `libwinpthread-1.dll` and `api-ms-win-crt-private-l1-1-0.dll` survive the static-link
  flags because `std::mutex` (`microstructure_kernel.h:107`) drags `libwinpthread` back in
  through `libstdc++.a`. `CMakeLists.txt:133-136` claims only Windows/ucrt + `python3xx.dll`
  + `KERNEL32` should remain; that expectation is not met. `microstructure_kernel.h:74-82`
  documents the dependency and records that a header-only spinlock was already tried and did
  not help. Both DLLs are present in the repo root (64,702 B and 75,184 B) and both are
  gitignored.
- Exactly **one** export string: `PyInit_cpp_microstructure`. The module is unusable via
  ctypes by design.

### 1.8 The hand-written JSON scanner, and why it is dead at runtime

`cpp/include/simdjson.h` (347 lines) implements `Status`, `Span` and `Scanner`
(`scan_string_span` `:148`, `scan_number_double` `:217`, `skip_value` `:274`, plus private
`skip_object`/`skip_array`/`skip_literal`). Its stated design rule (`simdjson.h:29-32`) is
**rejection, not tolerance**: input outside the supported subset produces an error, because
"tolerating unexpected input in the hot path means producing microstructure numbers that
look right but are wrong."

Documented wire shape (`microstructure_kernel.cpp:335-341`) — note the numbers are **inside
JSON strings**:

```
{"channel":"l2Book","data":{"coin":"BTC","levels":[
   [{"px":"64000.5","sz":"1.2"}, ...],      <- index 0 = BID side
   [{"px":"64001.0","sz":"0.8"}, ...]       <- index 1 = ASK side
],"time":1700000000000}}
```

`scan_number_double` accepts a leading `"`, skips sign/digits/fraction, **requires** the
closing `"` (`:253`, else `NumberMalformed`), copies into a 64-byte **stack** buffer and
NUL-terminates for `strtod` (`:261-269`) because the socket buffer is not NUL-terminated.
Exponent forms are rejected: `1e2` → `NumberMalformed`. Escaped keys are rejected:
`scan_string_span` returns `UnexpectedChar` on any `\` (`:156`).

Error taxonomy as exercised by `tests/test_cpp_kernel.py:229-233` and the implementation:

| Status | Triggered by |
|---|---|
| `UnexpectedEnd` | truncated payload, `{` alone |
| `UnexpectedChar` | escaped key, 3-element level, UTF-8 BOM, top-level array, missing `:` |
| `NumberMalformed` | exponent `1e2`, 70-digit number, `null` value, number ≥64 chars |
| `MissingField` | level with only `px`, empty `coin`, coin length ≥ 64 (`:501-503`), `"coin":{...}` without `"levels"` |
| `TooManyLevels` | **declared at `simdjson.h:55`, named in `status_text` `:65`, never constructed anywhere** |
| `Ok` | — |

Two structural properties hold by construction:

- **"not an l2Book channel" is distinguishable from "malformed l2Book"**.
  `ingest_json` returns `false` with `json_error` **intentionally left empty**
  (`microstructure_kernel.cpp:604-611`); the binding turns empty-error-false into
  `py::none()` (`bindings.cpp:119-123`) and non-empty-error-false into a thrown
  `RuntimeError` (`:124`). Runtime-verified: `ingest_json(trades_payload)` → `None`.
- **A failed parse never leaves stale OFI behind.** The local `Book` is handed to
  `store_.set_book` only on the fully-successful path (`:636`), after both `coin` and
  `levels` are confirmed (`:617-624`). No partial write exists.

**But the whole scanner is unreachable at runtime.** `ingest_json` has **zero production
callers** repo-wide: `core/microstructure.py:296-303` (the adapter) and
`tests/test_cpp_kernel.py:207,226,233` are the only references outside `cpp/`. The live feed
parses with Python `json.loads` at `hyperliquid_feed.py:412-418` and enters the kernel
through `ingest_l2`. Every OFI number the trading pipeline consumes therefore comes from the
Python parse path; the C++ parser never sees a byte of production traffic.

Three further pieces of C++ are declared but never constructed or called:
`Status::TooManyLevels` (§above), `Scanner::find_key` (`:169-209` — `parse_data_block` uses
its own inline key loop at `:485-524`), and `BookStore::reset_all()` (`microstructure_kernel.h:101`,
`:170-173` — `reset(nullptr)` routes to `BookStore::reset`, which inlines
`books_.clear()` itself at `:160-162`).

### 1.9 Known divergences between the two kernels

Parity is exact for all well-formed inputs, including 300 feed-shaped random books and 400
random books at depths 1..40. It is **not** exact in three places:

| Case | C++ | Python | Cause |
|---|---|---|---|
| `sz = NaN` | `ofi = nan` | `ofi = 1.0` | Python `max(-1.0, min(1.0, nan))` → `1.0` because `min(1.0, nan) == 1.0` (`core/microstructure.py:150`); C++ `if (ofi < -1.0)… if (ofi > 1.0)…` leaves NaN (`microstructure_kernel.cpp:241-242`) |
| `sz = +inf` | `nan` | `1.0` | same |
| book deeper than 32 levels, read at depth > 32 | levels past 32 silently dropped (`microstructure_kernel.cpp:88, 96-98`) | all levels kept | the cap is structural; `PythonKernel` has none |
| `reset(symbol)` | `BookStore::reset` clears the Book in place (`:158-168`) — the **capacity slot is not freed** | `self._books.pop(symbol, None)` frees the slot (`core/microstructure.py:176`) | with `max_symbols=2` and A+B loaded, C++ `reset('A')` then `ingest_l2('C')` raises `RuntimeError`; Python accepts C |

The 32-level truncation is doubly invisible: the `overflow_bid_`/`overflow_ask_` flags exist
(`microstructure_kernel.h:51-52`, set at `:97`, `:108`) but **no binding exports them**, so
the truncation the comment at `microstructure_kernel.cpp:93-95` insists must be "terlihat
lewat flag overflow" is in fact only visible as missing OFI. `MAX_LEVELS` is never compared
against an actual book depth, so a >32-level feed produces no warning.

---

## 2. The ML model lifecycle

`ml/` is **offline tooling**, not part of the live path. It has two modules and one artifact.

### 2.1 Module roles

| Module | Lines | Role | Importers |
|---|---|---|---|
| `ml/trainer.py` | 233 | the **only** writer of `ml/models/signal_model.pkl` | none; run as `python -m ml.trainer` (`:232-233`) |
| `ml/predictor.py` | 44 | thin wrapper over `MLSignalGenerator` | **zero importers anywhere in the repo**, tests included |
| `ml/__init__.py` | 1 | docstring only | — |
| `analysis/ml_signals.py` | 257 | the **actual runtime consumer** of the artifact | `agents/analysis_agent.py:19,50,61,116` and the dead `ml/predictor.py:11` |

`ml/predictor.py` is the only upward `ml/ → analysis/` edge in the module graph, and it is
dead. Its own docstring says "File ini untuk penggunaan standalone jika perlu."

### 2.2 Train

`ModelTrainer.__init__(lookahead: int = 10, threshold_pct: float = 0.005)` (`trainer.py:33`).

Seven features, **positionally** (`trainer.py:112-115`):

```python
feature_cols = ["rsi", "macd_hist_norm", "bb_position",
                "ema_trend", "volume_ratio", "sentiment_score", "atr_pct"]
```

Labels (`create_labels`, `:80-94`): `future_returns = close.shift(-lookahead)/close - 1`;
`> threshold_pct` → LONG, `< -threshold_pct` → SHORT, else HOLD.

Hard floors and split policy:

| Rule | Location |
|---|---|
| `len(X) < 100` → return `{"accuracy": 0, "error": "Data terlalu sedikit"}`, **write nothing** | `:125-127` |
| `train_test_split(..., test_size=0.2, random_state=42, shuffle=False)` — chronological, `random_state` is inert with `shuffle=False` | `:130-132` |
| `RandomForestClassifier(n_estimators=100, max_depth=10, min_samples_split=10, min_samples_leaf=5, random_state=42, n_jobs=-1)` | `:135-142` |
| `MODEL_DIR.mkdir(parents=True, exist_ok=True)` then `joblib.dump(model, str(model_path))` — unconditional overwrite, no temp-then-rename, no backup, no versioning | `:154-156` |

**`train()` requires an integer index.** `valid_mask & (labels.index < len(df) - self.lookahead)`
(`:120`) compares the index against an int; with a `DatetimeIndex` this raises
`TypeError: Invalid comparison between dtype=datetime64[us] and int` (reproduced).
`train_from_exchange` happens to build a `RangeIndex` (`:222-223`, no `set_index` call), so
the shipped path works.

**The trainer's docstring is wrong.** `:5` advertises
`python -m ml.trainer --symbol BTCUSDT --timeframe 1h --days 90`; `:232-233` has no
`argparse` whatsoever and always uses the defaults
`symbol="BTC/USDT:USDT", timeframe="1h", limit=1000` (`:172-174`).

### 2.3 Serve, and the train/serve skew

`analysis/ml_signals.py`:

```python
MODEL_DIR = Path("ml/models")            # ml_signals.py:20  == ml/trainer.py:18
self._feature_names = ["rsi", "macd_hist", "bb_position", "ema_trend",
                       "volume_ratio", "sentiment_score", "atr_pct"]   # :37-40
```

| # | Trainer name | Serve name | Scale |
|---|---|---|---|
| 0 | `rsi` | `rsi` | trainer `rsi/100` (`trainer.py:47`); serve `value/100.0`, default 50 → 0.5 (`ml_signals.py:79`) |
| 1 | `macd_hist_norm` | `macd_hist` | **trainer**: `clip(-0.01,0.01)*100` ⇒ `[-1,1]` (`trainer.py:52-53`). **serve**: `np.clip(macd_hist/100, -1, 1)` (`ml_signals.py:86`) — a different computation from the same raw field |
| 2 | `bb_position` | `bb_position` | both fill 0.5 on failure (`trainer.py:61`, `ml_signals.py:99`) |
| 3 | `ema_trend` | `ema_trend` | trainer continuous `clip(-0.01,0.01)*100`; serve a **3-state discretisation** `{1.0, 0.5, 0.0, -0.5, -1.0}` (`ml_signals.py:104-109`) |
| 4 | `volume_ratio` | `volume_ratio` | trainer `clip(0,3)/3` ⇒ `[0,1]`, fill 0.5 (`trainer.py:68-69`); serve `min(v/3.0, 1.0)`, default **1.0** (`ml_signals.py:118`) |
| 5 | `sentiment_score` | `sentiment_score` | trainer hardcodes `0.0` when absent (`trainer.py:72-73`) |
| 6 | `atr_pct` | `atr_pct` | trainer `(atr/close).clip(0,0.1)*10`; serve `min(atr/close,0.1)*10`, hardcoded `0.5` when no df (`ml_signals.py:124-128`) |

**Position is the contract, not the names.** Slot 1 is `macd_hist_norm` in the trainer and
`macd_hist` at serve; slot 3 is continuous in training and a 5-level step at serve. A rename
or a reorder in either file is a silent behaviour change.

**Feature 5 is structurally dead.** The trainer hardcodes `sentiment_score = 0.0`, so the
column is constant and no tree can split on it. Verified against the shipped artifact:

```
RandomForestClassifier  n_estimators=100  max_depth=10
classes ['HOLD', 'LONG', 'SHORT']
feature_importances_ = [0.153834, 0.179758, 0.144227, 0.172063, 0.090074, 0.0, 0.260044]
                              ^^^ slot 5 (sentiment_score) is exactly 0.0
```

The model is effectively **six-feature**.

`MODEL_DIR` is declared twice, independently, and is **CWD-relative both times**
(`ml/trainer.py:18` and `analysis/ml_signals.py:20`). Train from the wrong directory and you
write a fresh model elsewhere; run from the wrong directory and the bot silently loads
nothing, logging one INFO line (`ml_signals.py:48-50`) and falling back to rule-based.

Missing model raises `FileNotFoundError("Model belum dilatih")` at `:56`, but `initialize()`
catches **everything** at `:48-50` and sets `_model_loaded = False` at INFO level — a missing
model degrades silently, not loudly.

### 2.4 The shipped artifact

| Property | Value |
|---|---|
| Path | `ml/models/signal_model.pkl` |
| Size | **3,903,681 bytes** |
| SHA-256 | `ABB0F9A8C2973E73C7FE1E7A0829250F16CE696C795D65E13098826026CC6607` |
| Type | `RandomForestClassifier`, 100 trees, `max_depth=10`, 7 features |
| `classes_` | `['HOLD', 'LONG', 'SHORT']` |
| `feature_importances_[5]` | `0.0` exactly |

Loaded once at boot, off the event loop: `await loop.run_in_executor(None, self._load_model)`
(`ml_signals.py:47`) — the default `ThreadPoolExecutor`. `predict()` dispatches to
`_predict_ml` when loaded, else `_predict_rule_based` (`ml_signals.py:164-167`); `_predict_ml`
itself falls back to rule-based on any exception (`:185-187`). The rule-based scorer
(threshold `0.45`, `:237-246`) is untested.

**Zero test coverage.** No test file imports `ml.trainer`, `ml.predictor`, or
`analysis.ml_signals`. The only `ml_prediction` fixtures are hand-built dicts with **no
`probabilities` key**, so they exercise only the string-token branch — the RandomForest path
and the rule-based scorer never execute in the suite.

---

## 3. The test suite

### 3.1 Inventory and collected count

Re-derived by execution, not carried over from any prior document.

```
python -m pytest --collect-only -q     →  569 tests collected in 3.41s
python -m unittest discover tests      →  Ran 569 tests in 13.132s
                                             OK (skipped=4)
```

An independent `unittest.TestLoader` walk over the 24 test modules also sums to **569**.

| Test module | Lines | Cases | Contract it defends |
|---|---|---|---|
| `test_live_tui.py` | 822 | 65 | Menu/TextField key stepping, screen redraw, the whole `ask_mode` arrow→confirm→address flow. Hosts all 4 skips. |
| `test_bugfixes.py` | 1167 | 62 | Regressions B1–B9: tick-quality guard, Dash Output list-wrapping, daily-loss breaker, margin+fee validation, partial-close reporting, dead constants, palette sourcing, explicit `ROUND_DOWN`. |
| `test_advanced_modules.py` | 881 | 59 | ATR/Wilder, dynamic TP/SL widening and clamping, volatility-gate economics, the L2 queue-fill backtester, `_validate_dynamic_tp_sl`. |
| `test_lifecycle_paths.py` | 893 | 54 | End-state invariants: claim-once races, batch close, SL/TP/liquidation triggers, the two-sided fee invariant, log pruning, **and 10 native-parity tests on adversarial books**. |
| `test_live_safety.py` | 546 | 45 | The gate **refuses**: defaults, kill switch, live window, notional limits, invalid input, daily counters, counter persistence across restart, corrupt-counter-blocks-orders, secret redaction, cloid requirement, leverage bounds. |
| `test_live_engine.py` | 645 | 36 | Call **order** against the exchange — leverage before order, cloid pass-through, no trigger on an unfilled order, gate blocks before any exchange call, trigger direction inversion, quantization, pending-fill monitoring, `health_check`. |
| `test_live_console.py` | 234 | 27 | `decide_mode` refuses: confirmation phrase, address echo, NaN/inf rejection, paper-is-default for every garbage input. |
| `test_neural_net_layout.py` | 560 | 26 | Token-slot stability under membership changes, label collision, edge quality for the constellation graph. |
| `test_cpp_kernel.py` | 395 | 22 | C++↔Python OFI parity at 1e-7, **plus 9 source-integrity tests that need no build** (13 parity + 9 integrity = 22). |
| `test_live_executor.py` | 226 | 21 | The `LiveExecutor` adapter never becomes a bypass of the gate and never sends an unprotected order. |
| `test_direction_agents.py` | 267 | 20 | The four specialist agents **abstain** when blind, not emit a fake neutral. |
| `test_numerics.py` | 170 | 19 | Fee never negative, LONG/SHORT PnL mirror, daily-breaker denominator, ATR data sufficiency, ensemble probability clamping. |
| `test_layout_contract.py` | 414 | 18 | Mounts the real Dash layout at runtime and walks it: every callback Output/Input/State id exists, no duplicate ids, CSS has no hex outside `:root`, no `will-change`, ≤4 keyframes, no cyclic `var()`. |
| `test_probability_engine.py` | 248 | 16 | `norm_cdf`, OFI, technical z-scores, the Bayesian composite, the directional-curve regression. |
| `test_direction_ensemble.py` | 236 | 14 | Pure-math invariants of `aggregate()`: complementarity, mirror symmetry, monotone shrinkage, agreement weighting, abstain ≠ neutral. |
| `test_dashboard_palette.py` | 180 | 11 | No `var()` reaching Plotly, no dead GitHub-dark hex, readable luminance, alpha helper. |
| `test_risk_manager.py` | 171 | 11 | Position sizing, liquidation both sides, PnL, fee rates, `validate_trade`, `calculate_max_drawdown`, `calculate_sharpe_ratio`. |
| `test_decision_agent_ensemble.py` | 223 | 10 | Snapshot-freshness gate and confidence gate for `DecisionAgent._generate_scalp_signals`. |
| `test_layout_budget.py` | 222 | 10 | Pixel arithmetic per dashboard zone (header 22, zone-3 cells, KPI table, wallet rail, scanner stack). |
| `test_config.py` | 106 | 6 | Only dedicated test of `_validate_scalping_economics`. |
| `test_position_manager.py` | 237 | 6 | Position lifecycle plus two concurrency regressions (10 concurrent opens, 6 concurrent closes). |
| `test_fill_price_sl.py` | 198 | 4 | Entry-side slippage: `entry_price` must be `FILL*(1+cost)` for LONG and `FILL*(1-cost)` for SHORT; SL/TP derive from the real fill. |
| `test_paper_engine.py` | 191 | 4 | Entry-side contract end to end: entry price, margin and both fees derived from the fill. |
| `test_indicators.py` | 85 | 3 | OHLCV→DataFrame, indicator columns non-NaN, signal dict shape. Seeded `np.random(42)`. |
| **Total (24 test modules)** | **9,317** | **569** | |

`tests/` holds 26 `.py` files, **9,342 lines total**: 24 test modules (9,317) +
`__init__.py` (1 line) + `t3.py` (24 lines, a manual WebSocket probe that discovery ignores
— 0 collected cases).

**All 4 skips are `TestKeyParsing` in `tests/test_live_tui.py:359`**, skipped by
`setUp` at `:369-370` (`if os.name == "nt": self.skipTest(...)`) — the POSIX escape-sequence
decoder is untested on Windows, the platform this runs on.

### 3.2 The 4 skips and the 2 pytest-only failures

| Runner | Result |
|---|---|
| `python -m unittest discover tests` | `Ran 569 tests` / `OK (skipped=4)` |
| `python -m pytest -q` | `2 failed, 563 passed, 4 skipped` |

Both failures are `TestUnpatchedSmoke` in `tests/test_live_tui.py` and are **runner
artifacts, not product bugs**:

```
tests\test_live_tui.py:724:  decision = console.ask_mode(LiveConfig())
trading\live\console.py:419:  in ask_mode
trading\live\console.py:275:  in show_banner
trading\live\console.py:167:  in _read
E  OSError: pytest: reading from stdin while output is captured!  Consider using `-s`.
```

`TestUnpatchedSmoke` deliberately leaves `console._read` unpatched (its whole point is to
exercise the un-mocked path, `:593-605`) and never calls `console.show_banner`
(`test_ask_mode_paper_via_real_path`, `:716-726`). Under pytest's default capture, `input()`
raises. The sibling `test_ask_mode_non_tty_uses_plain_path` (`:734-755`) patches both
`show_banner` and `_read` and passes under both runners.

**Recommendation:** the suite's authoritative runner is `python -m unittest discover tests`.
`tests/t3.py` should be renamed so it does not sit inside the test package.

### 3.3 Tests that assert on source text, not behaviour

**51 of 569 (9.0%) read files as text.** They are architecture guards: they would pass if
the code they guard were empty, and they are invisible to import-graph tooling. Grepping the
literal string `'asyncio.to_thread(self._handle_message'` in a source file proves the author
typed a substring, not that the call happens.

| File | Tests | Line | Asserts the *source text* of |
|---|---|---|---|
| `test_advanced_modules.py` | 2 | 351, 791 | `agents/execution_agent.py` contains `"get_dynamic_tp_sl_thresholds"`; `config.yaml` has 7 `dynamic_tp_sl` keys (this one parses YAML, not text) |
| `test_bugfixes.py` | 20 | 349, 355, 380, 393, 517, 705, 713, 721, 742, 747, 773, 782, 1076, 1089, 1096, 1102, 1108, 1121, 1130, 1142 | `update_callbacks.py` x5, `hud_figures.py` x4, `risk_manager.py` x2, `config.yaml` x2, `run.py` x2, `decision_agent.py` x2, `paper_engine.py`, `data/hyperliquid_feed.py`, `.gitignore` — e.g. `ROUND_DOWN` present in both SL and TP bodies, `sig["strength"] >= 0.70` absent |
| `test_cpp_kernel.py` | 5 | 264, 293, 316, 324, 343 | `constexpr double kLevelWeight0 = 1.0`, `kLevelWeightStep = 0.1`; `std::array` present / `std::vector` absent in the header; `py::gil_scoped_release` in bindings; `-O2` present / `-Ofast`+`ffast-math` absent in CMakeLists; `initialize_native_kernel`+`_ensure_native_kernel` in the feed |
| `test_dashboard_palette.py` | 4 | 47, 55, 73, 80 | `var(--` absent, `plotly_dark`/`plotly_white` absent, `palette.PAPER_BG` present in `style.css`, 12 GitHub-dark hexes absent |
| `test_layout_budget.py` | 1 | 156 | `hud.py` — `tree-step-node`/`hud-tree-node-` absent. (The module-scope reads at `:22-24` run at import and are not a test.) |
| `test_layout_contract.py` | 18 | 81, 93, 107, 145, 153, 161, 165, 235, 244, 255, 277, 292, 308, 331, 342, 349, 392, 404 | `style.css` x13, `app.py` x4, `hud.py` x4, `update_callbacks.py` x2, plus the Dash bundle opened with `open()` at `:362`. (The x4 for `app.py` and `hud.py` come from `test_layout_files_have_no_hex_literals` at `:277`, which loops over both paths — a per-constant-name scan misses it and undercounts by 1 each.) |
| `test_neural_net_layout.py` | 1 | 204 | `neural_flow.css` contains `stroke-dashoffset: -16px` |
| **Total** | **51** | | of 569 collected |

Two of them are genuinely careful and worth copying:
`test_cpp_uses_fixed_size_arrays` (`:293-314`) strips `//.*` comments before asserting
`std::array` present and `std::vector` absent, because the header's own docstring says
"we do NOT use std::vector" and a raw grep would flag the explanation as the violation.
`test_cmake_avoids_fast_math` (`:324-341`) does the same with `#.*` for the same reason.

Two of them can be strengthened immediately:

- `test_prune_job_is_scheduled` / `test_maintenance_loop_is_started` would pass if the
  maintenance loop body were empty. A behavioural test would `await app._maintenance_loop()`
  against a seeded DB and assert rows were deleted.
- `test_every_dash_pattern_matches_css_keyframe` (`test_neural_net_layout.py:204-238`) checks
  that one literal CSS string exists. It does not actually parse `dash_patterns` out of
  `create_neural_net_fig` and sum them to 16 px.

### 3.4 Tests that exercise a copy of production logic

Two, both small, both unremarked:

| Location | Copy | Drift risk |
|---|---|---|
| `tests/test_cpp_kernel.py:289` | `expected = sum(1.0 - 0.1 * k for k in range(i + 1))` — the level-weight formula, re-derived in the test rather than imported | This test re-derives the formula independently, so it is the oracle and nothing else can be. But note the constants it guards are **not the ones the hot path uses**: `kLevelWeight0`/`kLevelWeightStep` are declared at `cpp/microstructure_kernel.cpp:57-58`, while the C++ OFI computation re-hardcodes `sizes[i] * (1.0 - 0.1*i)` at `cpp/microstructure_kernel.cpp:195`, and `PythonKernel` hardcodes the same expression inline at `core/microstructure.py:127`. Changing the named constants therefore breaks `test_cpp_weight_constants_match_python` while changing **no** OFI output. The genuinely unguarded pair is `cpp/microstructure_kernel.cpp:195` against `core/microstructure.py:127` — two unnamed literals that must be kept in step for bit-exact parity. |
| `tests/test_live_tui.py:340-356` | `_posix_key(text)` — a full reimplementation of `tui._read_posix` key decoding, byte for byte | The suite's own docstring at `:361-366` says wrong key mapping moves the cursor to the wrong entry, and for the live menu a wrong cursor followed by Enter is the wrong *mode*. The copy is exercised by `TestKeyParsing`, which **is skipped on Windows**. On this platform nothing verifies either copy. |

A third, softer case: `tests/test_lifecycle_paths.py:99-111` imports `close_fill_price` from
`trading.fill_cost` and computes the expected exit cost from it, so the test and the
production path share one implementation. This is the *correct* trade (a frozen literal
would re-lock the very assumption the change removed — see the comment at `:93-98`), but it
means the cost-model formulas themselves have no independent test. `tests/test_fill_price_sl.py`
and `tests/test_paper_engine.py` import `FILL_HALF_SPREAD_FLOOR` and `FILL_IMPACT_FLOOR` from
`trading.paper_engine` for the same reason.

### 3.5 Remaining shadowed definitions

**One shadow survives, and exactly one accounts for the 570-vs-569 gap.**

```
tests/test_neural_net_layout.py
  240  class TestEdgeRendering: def test_axes_have_no_scaleanchor   ← SHADOWED, never collected
  259  class TestEdgeRendering: def test_axes_have_no_scaleanchor   ← the one that runs
```

27 `def test_*` in that file, 26 collected. Python binds the second definition to the name;
the first is dead. Its docstring is the older, weaker one ("px per unit turun ~7.4x") — the
second is the current text. Both assert the same two `assertIsNone` calls, so nothing is
currently lost, but the file reads as if it contains two different tests.

Every other module has exactly one `def test_` per collected case. **No duplicate class
names** remain: `TestFeeAccounting` now appears once, at `test_lifecycle_paths.py:75`, and
the former second definition is `TestOpeningFeeNotDoubleCharged` at `:253`. AST scan across
all 24 modules reports zero same-name class collisions and zero other duplicate methods.

Nothing enforces either rule. `unittest discover` silently binds the later definition and
drops the earlier one.

### 3.6 Coverage gaps

Modules with **no test importer at all**, by AST scan of all 24 test modules:

| Slice | Modules | Lines |
|---|---|---|
| `data/` — **entire market-data layer** | `price_feed` 705, `hyperliquid_feed` 573, `macro_fetcher` 218, `sentiment` 213, `news_fetcher` 154 | **1,863** |
| `dashboard/` except `hud`, `hud_figures`, `palette` | `update_callbacks` 1429, `price_chart` 209, `performance` 160, `news_feed` 117, `positions` 95, `app` 148, `agent_logs` 69 | **2,227** |
| `agents/` | `analysis_agent` 318, `base_agent` 151, `news_agent` 145 | **614** |
| `analysis/` | `ml_signals` 257, `fundamental` 153 | **410** |
| `core/` | `logger` 403, `scheduler` 139, `utils` 31 | **573** |
| `ml/` | `trainer` 233, `predictor` 44 | **277** |

The three largest gaps:

1. **`data/` (1,863 lines, zero tests).** Every network call, timeout, fallback tier and
   cache TTL is untested. No file in `tests/` imports `data.price_feed`,
   `data.hyperliquid_feed`, `data.macro_fetcher`, `data.news_fetcher` or `data.sentiment`.
2. **`dashboard/callbacks/update_callbacks.py` (1,429 lines, 20 callbacks).** Zero tests
   import it; its only importer is `dashboard/app.py:20`. Seven of the source-text guards
   above read it as a string instead.
3. **The whole ML path** (410 lines of code plus a 3.9 MB artifact).

Other notable gaps:

- **No test imports `run.py`.** The only run.py assertions are the two string greps at
  `test_bugfixes.py:1098` and `:1104`. The composition root — startup order, the escalation
  ladder in `_execution_loop`, the `CancelledError` handler, `shutdown()` — is entirely
  untested.
- `analysis/vol_target.py` (132 lines) has zero importers **and** zero tests.
- `LiveExecutor` persistence and EventBus publishing (`_persist_open` SQLite path,
  `_record_close`, `_publish`) have **no test**. `test_live_executor.py` uses stub engines
  whose `_Recorder` captures calls; it never exercises SQLite or the bus.
- `test_live_executor.py:208-217` checks only **2 of the 6** members `ExecutionAgent` touches
  (`execute_order`, `update_price`). The production class implements all six — `update_price`
  (executor.py:210), `get_price` (:237), `check_positions` (:259), `execute_order` (:397),
  `_last_prices` (:110), `position_manager` (:115). **The test is weaker than the code**, which
  is how the old document's "implements 2 of 6" claim persisted.
- Native parity covers only `ingest_l2` inputs. The `ingest_json` scanner has 3 tests
  (`test_cpp_kernel.py:177-233`) against 347 lines of parser, and no test for
  `TooManyLevels`, `find_key`, `reset_all`, coin-length ≥ 64, or the `noexcept` terminate.
- `TradingBotApp._build_live_executor`, `emergency_flat` (5 tests but **zero production
  callers**), and `SafetyGate`'s interaction with a live exchange are untested end to end.

### 3.7 Test discipline the suite holds, in its own words

Recorded because these are invariants a future contributor will break silently:

| Rule | Where it is written down |
|---|---|
| Fee expectations are computed from the `trades` table or from the same `fill_cost` model production uses — never from a frozen literal | `test_lifecycle_paths.py:93-98` (a `-9.0` had to be removed) |
| Risk thresholds are read from config, not hardcoded | `test_lifecycle_paths.py` (the failure mode is a test that stays green while the limit changes) |
| A test may not loosen its own assertion floor | `test_lifecycle_paths.py:821` requires ≥3 reasons so a future "only the first reason" optimisation fails |
| Every test that mutates a module-level singleton restores it in `tearDown` | `test_live_tui.py:454-467`, `:608-626`; `test_direction_agents.py:254-263` |
| All assertions in `test_fill_price_sl.py` are relative to the actual `p["entry_price"]`, never to the market price | module docstring `:9-15` — otherwise the test re-locks the assumption the fix removed |

---

## 4. Numerical conventions

Every convention below is load-bearing in at least one place where the alternative is a
silent money bug.

### 4.1 Units

| Quantity | Unit | Where |
|---|---|---|
| Price | quote currency (USDC/USDT), absolute | `Position.entry_price`, `Trade.price` |
| Quantity | base asset | `Quantity` field; `trades.side` records the **fill** side, `positions.side` the **position** side |
| Money / margin / PnL | quote currency | `account.balance` is a **paper-only** ledger |
| PnL %, return on position notional % | **percent, already ×100** | `calculate_pnl` divides by `position_value = entry * qty` — position notional, **not** account equity — and multiplies by 100 (`risk_manager.py:264-265`). Consumers treat it as percent: `trading/live/executor.py` renders `ROE {:+.2f}%` and `trading/paper_engine.py` does the same; `dashboard/layouts/positions.py` has an `ROE%` column. The field name `roe_pct` is a misnomer inherited from that denominator. |
| `max_drawdown`, `calculate_max_drawdown` | **fraction** | `risk_manager.py:331` `(peak - mark) / peak`; `calculate_max_drawdown` at `:397-412` returns `round(max_dd, 4)` un-scaled. Two other sites do multiply by 100 for display: `dashboard/layouts/performance.py:48` and the warning string at `risk_manager.py:333`. |
| `sl_pct`, `tp_pct`, `risk_pct`, `max_daily_loss`, `max_drawdown`, `*_spread_pct` | **fraction of price / of balance** | `config.yaml`: `tight_sl_pct: 0.0025` = 0.25%, `max_daily_loss: 0.10` = 10% |
| `relative_spread`, `half_spread_pct`, `impact_pct`, `total_cost_pct` | fraction | `fill_cost.py:165-174`; `describe_cost` multiplies by 10000 to render bps |
| `atr_pct`, `realized_vol_pct` | fraction | `analysis/volatility.py` |
| Time | ISO TEXT UTC from SQLite `datetime('now')`, **except** `candles.timestamp` = INTEGER Unix **milliseconds** | `database/db.py` |
| `max_book_age_seconds`, `max_tick_age_seconds` | seconds, float | `config.yaml: max_tick_age_seconds: 1.5` |

### 4.2 float vs `Decimal`

The codebase is **float everywhere except inside the risk math**, and the exception is
deliberate.

| Zone | Type | Evidence |
|---|---|---|
| Indicators, ML, scores, z-scores, probabilities | `float64` / numpy | `analysis/technical.py`, `probability_engine.py`, `direction_ensemble.py` |
| Prices, sizes, PnL, fees as *stored* | `float` | every `REAL` column; `dict` in `market_store` |
| `RiskManager` sizing / SL / TP / liquidation / fee / PnL | **`Decimal`, rounded, then cast back to float** | `trading/risk_manager.py` |
| Exchange size quantization | **`Decimal`** for the step division, float for the result | `trading/live/client.py:334-337` |
| Margin in `PositionManager.open_position` | **`Decimal`** | `position_manager.py:85` |

The canonical quantizers (`risk_manager.py:16-18`):

```python
PRICE_QUANT = Decimal("0.00000001")   # 8 dp  — prices
MONEY_QUANT = Decimal("0.01")         # 2 dp  — money
QTY_QUANT   = Decimal("0.00001")      # 5 dp  — quantity
```

Every input is stringified before it enters `Decimal` — `Decimal(str(x))`, never
`Decimal(x)`. `Decimal(0.1)` is not `Decimal("0.1")`, and the difference is exactly the kind
of digit that a 1e-8 price quantizer cannot absorb.

### 4.3 Rounding points

**`ROUND_DOWN` everywhere, explicitly.** `Decimal.quantize` defaults to `ROUND_HALF_EVEN`,
and every money path overrides it.

| Call | Location | Granularity |
|---|---|---|
| `calculate_position_size` → `quantity` / `margin` / `position_value` / `risk_amount` | `risk_manager.py:126-129` | `QTY_QUANT` / `MONEY_QUANT` |
| `calculate_scalp_position_size` → all four | `:373-376` | same |
| `calculate_liquidation_price` | `:155` | `PRICE_QUANT` |
| `calculate_stop_loss` | `:179` | `PRICE_QUANT` |
| `calculate_take_profit` | `:200` | `PRICE_QUANT` |
| `calculate_fee` | `:238-239` | `Decimal("0.0000000001")` — **10 dp, not cents** |
| `quantize_size` (exchange lot) | `client.py:335-336` | `to_integral_value(rounding=ROUND_DOWN)` on `Decimal` steps |
| `PositionManager.open_position` margin | `position_manager.py:85` | via `Decimal` division |

The direction rationale is written out at `risk_manager.py:163-169`: for LONG,
`ROUND_DOWN` puts the stop slightly lower (more room, same direction as the round-down TP
rationale at `:187-190`) — both are described as the conservative side. **For a SHORT
`ROUND_DOWN` puts the stop slightly *higher***, i.e. marginally tighter than intended. At a
0.25% scalp, one 1e-8 tick is negligible; the point of the lock is that it is *deterministic*
rather than *optimal*.

The fee rounding comment (`risk_manager.py:220-237`) records the bug this replaced:
quantizing to cents made a $0.10 notional pay `$0.00` (fee `$0.000045`), so a small-size
scalping bot looked fee-free **and looked profitable**. Ten decimal places avoids float noise
without deleting small fees.

**Where rounding is NOT applied** — the display and analysis layers round for storage into
`agent_logs` / `direction_snapshots` only, never for a number that reaches the engine:

| Value | Rounding | Location |
|---|---|---|
| `direction_ensemble` per-verdict `z`, aggregate `confidence` | `round(..., 4)` | `direction_ensemble.py:113-114` |
| `probability_engine` composite outputs | `round(..., 4)` / `round(..., 3)` | `probability_engine.py:253-267` |
| Diffusion curve probabilities | `round(p, 4)` | `probability_engine.py:317-318` |
| Direction-agent factor details | `round(ofi, 4)`, `round(change, 6)`, `round(z, 4)` | `direction_agents.py:144, 180, 240, 252` |
| `calculate_max_drawdown` / `calculate_sharpe_ratio` return | `round(..., 4)` | `risk_manager.py:412, 429` |
| `kelly_criterion` return | `round(..., 4)` | `risk_manager.py:395` |
| `quantize_price` (exchange tick) | `round(price, 5 / 4 / 2)` by magnitude | `client.py:352-353` |
| Backtester synthetic book prices | `round(..., 8)` | `backtester.py:397-421` |
| `calculate_pnl` outputs | `Decimal("0.01")` — **default rounding**, i.e. `ROUND_HALF_EVEN` | `risk_manager.py:268-270` |

That last row is the one inconsistency in the file: `calculate_pnl` does **not** pass
`rounding=`, so PnL rounds half-to-even while every other money figure rounds down. The
difference is at most half a cent and it is not covered by a test.

### 4.4 NaN and inf

**The rule is that unknown is never zero, and non-finite is never admitted.**

Where the codebase actively rejects non-finite input:

| Site | Test | Consequence |
|---|---|---|
| `DecisionAgent` order construction | `math.isfinite(price) and price > 0` (`:349`); same for `quantity` (`:408`), `stop_loss` (`:410`), `take_profit` (`:412`) | order refused before publish |
| `LiveExecutor._finite_positive` | `math.isfinite(f) and f > 0.0` (`executor.py:95`) | live open refused, **no safe default size** |
| `LiveExecutor.update_price` | `math.isfinite(p) and p > 0` (`:234`) | bad tick dropped, never cached |
| `console.validate_rule` | `not math.isfinite(parsed)` (`console.py:523`) | NaN/inf limits rejected explicitly |
| `PythonKernel.order_flow_imbalance` | `denom <= 0.0 → (0.0, 0.0)`; `mid <= 0.0 → (ofi, 0.0)` | price-0 sentinel gives 0, never `ZeroDivisionError` |

Why explicit rather than inherited: **every comparison against NaN is `False`.** A limit
containing NaN therefore looks like protection and never fires. The comment at
`test_numerics.py:154-156` states this as the reason the rule exists.

Where NaN *is* tolerated, with a documented result:

| Site | Behaviour |
|---|---|
| `PythonKernel` OFI clamp | `max(-1.0, min(1.0, nan))` → **`1.0`**. A NaN size becomes a maximally bullish OFI. The C++ kernel leaves NaN. See §1.9. |
| `analysis/probability_engine.py:105-129` | each sub-z guarded by `math.isnan` before use; a missing input contributes a 0 rather than a NaN |
| `agents/direction_agents.py:328` | `return None if math.isnan(out) else out` |
| `ml/trainer.py:60, 68` | `.replace(0, np.nan)` then `.fillna(0.5)` — the *training* path deliberately normalises NaN to a neutral 0.5 |
| `fill_cost._observed_half_spread` | `mid <= 0 or best_bid <= 0 or best_ask <= 0` → `(None, age)`, i.e. falls to the **floor**, never to zero cost (`:88-93`) |

**inf.** Only one producer: `Repository.get_trade_stats` returns
`profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")`
(`database/repository.py:603`). Both callers convert it before persisting —
`position_manager.py:338-342` and `executor.py:836-838` both map `inf → 0`. The two dashboards
then render that same `0.0` differently: `performance.py:57` shows `"—"`, and
`update_callbacks.py:1284-1327` falls through to `pf = 0.0` and shows `"0.00"`.

### 4.5 Epsilons

Small-magnitude thresholds that decide "is this value real evidence or noise":

| Epsilon | Location | Meaning |
|---|---|---|
| `1e-12` | `analysis/backtester.py:111, 115, 303, 529, 538` | fill/queue residual is zero; below this the order counts as filled |
| `1e-9` | `backtester.py:597, 659` | price equality for queue lookup and trade-price match |
| `1e-6` | `agents/direction_agents.py:129, 165, 236, 307` | an OFI / z-score below this is **NEUTRAL with confidence 0.0**, not a tiny directional vote |
| `1e-6` | `agents/direction_agents.py:174` | floor on `momentum_threshold` so a config of 0 cannot divide by zero |
| `1e-7` | `tests/test_cpp_kernel.py:37`, `test_lifecycle_paths.py:508-514` | C++↔Python parity tolerance |
| `1e-4` | `probability_engine.py:114` | ATR fallback denominator `max(price*0.002, 1e-4)` |
| `1e-6` | `trainer.py:...` / `client.py:285` | lot-step divisibility check `abs(round(sz_min/step) - sz_min/step) < 1e-6` (`client.py:285`) |

### 4.6 Precision-sensitive spots

Ordered by cost of being wrong.

| # | Spot | Why it is sensitive |
|---|---|---|
| 1 | **`trading/fill_cost.py:159` `total = min(half + impact, FILL_MAX_TOTAL_COST_PCT)`** | The cap applies to the **sum**, so `meta["half_spread_pct"]` can read 196 bps while `total_cost_pct` reads 50 bps. A caller that reads the half-spread for display sees a number the model did not charge. |
| 2 | **`analysis/volatility.py:383-384` `roundtrip = taker * 2`** | The volatility gate prices a round trip as **fees only** — 9.0 bps at `taker 0.00045` — and never imports `fill_cost`. True round trip at the configured scalp is **17.0 bps** (4.0 entry crossing + 4.0 exit crossing + 9.0 fees), so the gate understates the required break-even win rate and is completely blind to the book. |
| 3 | **`analysis/volatility.py:397` `net_tp = tp_pct - roundtrip`** | Same 9.0 bps model. With a wide observed half-spread the model still returns `None` (no rejection) on a target the book makes unprofitable. |
| 4 | **`core/config.py:959-974`** | The boot validator uses the same fees-only `roundtrip`, so it accepts a `breakeven_offset_pct` that still loses money net of exit cost. Shipped `breakeven_offset_pct: 0.0015` (15 bps) clears the true 13 bps break-even by 2 bps. |
| 5 | **Live anchors SL/TP to the pre-fill price** | `agents/execution_agent.py:139-140` sets `order.stop_loss/take_profit` from `engine.get_price(...)`; `executor.py:475-476` and `:558-559` pass and store them unchanged. Paper re-anchors to the fill (`paper_engine.py:566-567`). The live stop therefore sits ~29 bps from the real fill, not the configured 25. |
| 6 | **`price_is_final=True`** (`position_manager.py:172`, `:213`) | A documented opt-out from the exit cost model with **zero callers repo-wide**. Live would be the natural caller; it does not use it, so live applies no crossing model at all (correct — it books the exchange's real `avg_price`). The hook is dead surface. |
| 7 | **`trading/paper_engine.py:51-54` re-declares all four `FILL_*` constants** | STORE-only local copies shadowing the identical imports at `:21-26`. Values agree today; the two copies can drift silently. |
| 8 | **`fill_cost.describe_cost` (`fill_cost.py:207`)** | Zero callers — the `reasoning` column it was written for is populated by other means. |
| 9 | **ML feature alignment** | §2.3. Slot 3 is continuous at train time and a 5-level step at serve time; slot 1 has different formulas. |
| 10 | **`risk_manager.calculate_pnl` rounding** | The only money path without explicit `rounding=`. |
| 11 | **`_liquidate` (`position_manager.py:480`) `realized_pnl = -pos["margin"] - fee`** | Does not subtract the opening fee that `close_position:257` does, so `SUM(realized_pnl)` no longer reconciles with `balance - initial_balance` on the liquidation path, and `get_daily_realized_pnl` under-reads the daily-loss breaker by exactly that fee. |
| 12 | **`sharpe_ratio` has no variance floor** (`risk_manager.py:424-427`) | Guards only `std == 0`. A flat equity book where snapshots move by ~1e-8 produces std ~1e-7 and a ratio in the thousands — measured 16264.11 on three identical round trips. Rendered verbatim by the HUD. |
| 13 | **MDD/Sharpe from an empty `balance_history`** | Both functions return `0.0` on a short series (`risk_manager.py:399-400`, `:418-419`), and `0.0 is not None`, so the `is not None` guard at `database/repository.py:385` passes and both columns are **overwritten with zero**, destroying previously correct values. Also: `_get_equity_series(limit=500)` (`position_manager.py:513`) makes MDD a rolling ~8.3-hour window at 60 s snapshots, not all-time, so a stored value can shrink. |
| 14 | **`trading/paper_engine.py:927, 965`** | `fill_meta = None` is set before the loop and only assigned on success; `:965` dereferences `fill_meta["ref_price"]` unconditionally. When every close loses its claim race the batch audit log raises `TypeError` instead of writing the `GAGAL:` report it exists to produce. |

### 4.7 Cost model, in one table

The four constants are **floors, not targets** (`fill_cost.py:44-48`, and re-declared
identically at `paper_engine.py:51-54`):

```python
FILL_HALF_SPREAD_FLOOR        = 0.0003   # 3 bps
FILL_IMPACT_FLOOR             = 0.0001   # 1 bps
FILL_BOOK_MALFORMED_SPREAD_PCT= 0.05     # 5% — above this the book is broken, not the market
FILL_MAX_TOTAL_COST_PCT       = 0.0050   # 50 bps, a safety net only
```

The formula (`fill_cost.py:141-163`), verbatim:

```
half   = FILL_HALF_SPREAD_FLOOR
if observed is not None:  half = max(observed, FILL_HALF_SPREAD_FLOOR)   # max, never min
impact = FILL_IMPACT_FLOOR                                                 # constant, never reads the book
total  = min(half + impact, FILL_MAX_TOTAL_COST_PCT)
signed = +total if fill_side == "BUY" else -total
fill   = ref_price * (1.0 + signed)
```

`observed = (best_ask - best_bid) / (2.0 * mid)` (`:95`), admitted only when the book is
provably fresh — `age is None` (no timestamp) or `age > max_book_age_seconds` both reject
(`:76`). The age budget is read from `config.scalping.max_tick_age_seconds` (`:133-139`) so
the cost model's staleness budget and the tick guard's cannot drift apart.

Position side → fill side (`close_fill_price`, `:196`):
`"SELL" if position_side == "LONG" else "BUY"`. Closing a LONG receives less; closing a
SHORT pays more. The conversion exists so no caller has to remember it — guessing there
exactly inverts the sign and makes a SHORT close look profitable.

Arithmetic at the configured scalp (`tight_sl_pct 0.0025`, `fast_tp_pct 0.0060`,
`taker 0.00045`), ref 100, no book so the floor applies:

```
entry crossing   +4.0 bps        exit crossing   +4.0 bps        fees 2 x 4.5 = 9.0 bps
ROUND TRIP = 17.0 bps = 68.0% of the 25 bps stop, 28.3% of the 60 bps target

paper re-anchors SL/TP to the FILL (paper_engine.py:566-567), so the entry leg buys no
extra stop room:
   cost charged on a stopped trade = exit 4.0 + fees 9.0 = 13.0 bps = 52% of the stop
   realized SL = -37.98 bps (152% of the announced 25 bps)
   realized TP = +46.95 bps ( 78% of the announced 60 bps)
   realized R:R 1:1.236   (nominal 1:2.40)   break-even WR 44.72% LONG / 44.70% SHORT

gate model (analysis/volatility.py:384, roundtrip = taker*2 = 9.0 bps):
   gate break-even WR 40.00%   true 44.72%   gap +4.72 points at the configured target
   and the gate is blind to the book: at a 49 bps observed half-spread, total caps at
   50 bps, round trip becomes 109 bps, yet assess_volatility_gate(0.0025, 0.0060) = None
```

---

## Not verified

- **The 13x / 1.6x speedups** in §1.5 are in-process benchmarks on synthetic 20-level books,
  measured once, not repeated and not run under the real WS load. Treat them as an order of
  magnitude, not a specification.
- **The PE import-directory dump** (§1.7) was produced by a hand-written PE parser in this
  session rather than by `dumpbin`/`objdump`. The 12 entries were read correctly and the one
  export string was confirmed by raw byte scan, but a tool-based cross-check was not run.
- **Byte-for-byte matching of every `bindings.cpp` docstring inside the `.pyd`** was not
  re-verified in this session. The `.pyd` freshness is established by ninja's "no work to do",
  the mtime ordering, the matching `.ninja_log` output hashes, and `__version__`/`MAX_LEVELS`
  at runtime.
- **The parity results in §1.9** (300 + 400 random books) and the `book_from_python`
  process-termination matrix in §1.3 come from prior analysis of this working tree. The
  current session confirmed parity indirectly: the suite runs `OK (skipped=4)`, and both
  `test_cpp_kernel.py::TestCppPythonParity` (11 cases) and
  `test_lifecycle_paths.py::TestNativeKernelParity` (10 cases) pass with the shipped `.pyd`
  present. The NaN, >32-level and `reset` divergences were not re-executed here.
- **`max_symbols = 0` coercion to 1** (`microstructure_kernel.cpp:117`) and the
  non-contiguous-numpy acceptance in §1.3 are read from source and from pybind11's
  `stl.h` caster semantics; they were not re-executed in this session.
- **`_doc04_econ.py`, `_lines.py` and `data_store/_snap_cp/` are untracked scratch files** in
  the working tree, not part of the repository. They are excluded from every count in this
  document.
- The exact `docs/context/CONTEXT.md` figure it carried for the test count (566) was located
  at lines 65, 448, 2524, 2530, 3112, 3386 and 5751 during this session; only the first three
  were read directly.


---

*Dokumen ini mengoreksi klaim dari `docs/context/ARCHIVED-2026-09-28.md`
(selama ini `docs/context/CONTEXT.md`). Berkas itu sudah diarsipkan dan
ditandai usang; nama lamanya tidak lagi dipakai supaya tidak ada dua
sumber kebenaran untuk hal yang sama.*
