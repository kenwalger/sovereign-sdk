# sovereign-sensor — Phase 9

A lightweight, MicroPython-compatible Hardware Abstraction Layer (HAL) that enforces
data custody at the **Point of Genesis** by sealing each sensor observation into a
versioned, tamper-evident, replay-protected JSON transmission envelope before any
network transit or cloud ingestion occurs.

Zero external dependencies.  Internal library code is restricted to standard MicroPython
built-ins (`json`, `sys`, `hashlib`, `hmac`, `binascii`).  Targets ESP32 and Raspberry Pi
Pico via MicroPython; fully exercisable on CPython 3.12 for desktop CI.

---

## Architecture

### Hardware Abstraction Layer

`SovereignCryptoDriver` (`interface.py`) defines a three-method contract:

| Method | Contract |
|---|---|
| `initialize_hardware() -> None` | Load key material; configure accelerator subsystem. |
| `sign(payload: bytes) -> bytes` | Return raw binary signature bytes (no encoding). |
| `algorithm() -> str` | Return a canonical algorithm identifier string. |

Two concrete drivers are provided:

- **`SoftwareFallbackDriver`** — HMAC-SHA256 over a VFS-resident binary key file.
  Used on all non-ESP32 targets (desktop CI, Raspberry Pi Pico, etc.).
  The class-level `MOCK_KEY_SENTINEL = "/mock/test_gateway.key"` opts into a
  deterministic stub key for desktop testing; every other path that cannot be opened
  raises `RuntimeError` immediately, eliminating silent key substitution.  A zero-byte
  key file raises `ValueError` to prevent HMAC keyed with `b""`.

- **`ESP32HardwareDriver`** — Skeleton placeholder for the on-chip ECC accelerator.
  `initialize_hardware()` raises `NotImplementedError` until register-level engineering
  is complete; `bootstrap_sensor_node()` catches this and falls back to
  `SoftwareFallbackDriver` automatically so the sealing and VFS layers remain
  exercisable on the workbench.

### Seven-Step Sealing Pipeline (`SovereignEnvelope.seal()`)

1. **Monotonic sequence counter** — computed transiently as `_sequence + 1` and bound
   into the preimage.  The in-memory counter and VFS file (default `.sovereign_sequence`)
   are advanced only after signing succeeds, so a `sign()` failure never consumes a
   sequence position or introduces a gap in the on-disk custody timeline.  A truncated
   (0-byte) file resets to 0; a negative stored value is clamped to 0.  VFS write
   failures degrade gracefully to RAM-only tracking.

2. **Algorithm identifier** — queried from the active driver via `algorithm()` and
   embedded in the authenticated preimage, providing protocol agility without
   a schema change.

3. **Canonical payload serialization** — `json.dumps(payload, separators=(",", ":"), sort_keys=True, ensure_ascii=False)`
   guarantees a byte-identical preimage for semantically equivalent payloads
   regardless of key insertion order.  `ensure_ascii=False` forces raw UTF-8 output
   for all characters, eliminating the `\uXXXX`-vs-raw-UTF-8 split-brain divergence
   that would cause cross-platform HMAC verification to fail silently on any payload
   containing characters outside U+007F.

4. **UTF-8 byte-count-prefixed preimage assembly** — `node_id`, `timestamp`, and the
   `algorithm` identifier are each encoded to UTF-8 independently; the prefix for each
   field is the UTF-8 byte count (not the Unicode character count).  The preimage is
   assembled from raw byte slices:

   ```
   1|{len(node_bytes)}:{node_id}|{len(time_bytes)}:{timestamp}|{seq}|{len(algo_bytes)}:{algo}|{canonical}
   ```

   Byte-count prefixes close all variable-length field injection surfaces: delimiter
   injection (any two inputs that differ only in where a `|` character falls produce
   identical naive pipe-joined preimage bytes without prefixes) and multi-byte encoding
   ambiguity (a receiver using character-count semantics parses field boundaries at the
   wrong byte offset for any non-ASCII field value).

5. **Driver signing** — raw preimage bytes traverse `driver.sign()`, returning raw
   binary output from the underlying cryptographic primitive.

6. **Hex encoding** — `binascii.hexlify()` maps all byte values `0x00–0xFF` to
   the lowercase alphanumeric characters `0–9`, `a–f`, preventing
   `UnicodeDecodeError` on constrained MicroPython silicon.

7. **Wire frame serialization** — all seven envelope fields (`v`, `n`, `t`, `q`,
   `alg`, `d`, `s`) are packed into a dict and serialized with
   `json.dumps(..., separators=(",", ":"), sort_keys=True, ensure_ascii=False)`,
   freezing the alphabetical key sequence and enforcing raw UTF-8 wire encoding
   independently of MicroPython allocator-driven insertion order.

---

## Quick Start

```python
from sovereign_sensor import bootstrap_sensor_node

# Auto-selects ESP32HardwareDriver or SoftwareFallbackDriver at runtime.
# Falls back to SoftwareFallbackDriver with a warning if hardware crypto
# is not yet implemented on the target.
envelope = bootstrap_sensor_node(
    node_id="node-temperature-01",
    private_key_path="/flash/keys/node.key",
    sequence_file="/flash/.sovereign_sequence",
)

observation = {"sensor": "temperature", "value": 21.4, "unit": "C"}
wire_bytes = envelope.seal("2026-06-16T12:00:00Z", observation)
# → b'{"alg":"hmac-sha256","d":{"sensor":"temperature","unit":"C","value":21.4},'
#     '"n":"node-temperature-01","q":1,"s":"<64-char hex>","t":"2026-06-16T12:00:00Z","v":1}'
```

### Desktop / CI (mock key sentinel)

```python
from sovereign_sensor import bootstrap_sensor_node
from sovereign_sensor.drivers.software_fallback import SoftwareFallbackDriver

envelope = bootstrap_sensor_node(
    node_id="ci-node-001",
    private_key_path=SoftwareFallbackDriver.MOCK_KEY_SENTINEL,
)
wire = envelope.seal("2026-06-16T00:00:00Z", {"ping": True})
```

---

## Invariants

| Property | Guarantee |
|---|---|
| **Replay protection** | Monotonic `q` counter persisted to VFS; resumes across reboots. |
| **Sequence atomicity** | Counter advanced only after `sign()` succeeds; a signing failure leaves the on-disk counter unchanged with no gap. |
| **Key material safety** | Missing or empty key file raises immediately; no silent substitution. |
| **Preimage determinism** | `sort_keys=True` and `ensure_ascii=False` on payload; byte-count length prefixes on all three variable-length fields. |
| **Wire frame determinism** | `sort_keys=True` and `ensure_ascii=False` on the outer frame; byte-identical UTF-8 output across CPython and MicroPython builds. |
| **Encoding safety** | `binascii.hexlify` prevents `UnicodeDecodeError` on raw binary digest bytes. |
| **Zero dependencies** | No network calls, no PyTorch, no external packages at runtime. |
