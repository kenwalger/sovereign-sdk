# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- **Graceful Legacy Keypair Migration** (`crypto.py` —
  `load_or_generate_keypair`): Hardened the PEM identity loading sequence to
  automatically detect, warn, and seamlessly upgrade legacy unencrypted
  keypairs to password-encrypted storage without breaking the operator
  migration path.  On first boot after upgrading, the node attempts to open
  the existing `sovereign_identity.pem` with the active
  `SOVEREIGN_NODE_SECRET` passphrase.  If that raises `TypeError` or
  `ValueError` (the signature of an unencrypted legacy file), a second attempt
  is made with `password=None`.  Success triggers an advisory warning to
  `stderr` and an immediate in-place rewrite of the PEM under
  `BestAvailableEncryption`; subsequent boots load the now-encrypted file
  silently.  If both attempts fail, a `RuntimeError` is raised with explicit
  rotation guidance rather than exposing the raw library exception.

### Security

- **Key Self-Attestation Forgery Elimination** (`crypto.py` —
  `verify_receipt`; `router.py` — `dispatch`; `__main__.py` —
  `_audit_receipt`): `verify_receipt` previously accepted any receipt whose
  Ed25519 signature was internally valid, including receipts minted by a rogue
  keypair that embedded its own public key.  The method now accepts an optional
  `expected_public_key` parameter; when supplied, the receipt's embedded
  `public_key` field is compared against it before any cryptographic operation
  is attempted.  The router passes `self.key_manager.public_key` at every
  post-mint defense check and `_audit_receipt` performs an additional explicit
  string comparison, emitting a distinct fraud-alert message to `stderr` to
  distinguish identity-forgery from general seal tampering.

- **Atomic Legacy Key Migration** (`crypto.py` — `load_or_generate_keypair`):
  The previous migration path opened `sovereign_identity.pem` directly with
  `open(..., "wb")`, truncating the only copy of the unencrypted key before the
  encrypted replacement was ready.  A crash between truncation and the final
  write left the node with an empty PEM and no recovery path.  The rewrite now
  stages the encrypted PEM to a temporary file in the same directory (guaranteeing
  same-filesystem atomicity), applies `os.chmod(0o600)` before writing any bytes,
  then promotes the temp file over the original via `os.replace()`.  A `finally`
  block removes the temp file on any failure path so no partial material is left
  on disk.

## [0.5.1] - 2026-05-21

### Fixed

- **Pipeline Happy-Path Audit Gap** (`__main__.py` — `execute_runtime_node`):
  `_audit_receipt` was only reachable through the retry branch, meaning the
  first successful `"download"` dispatch advanced to Step 2 without any
  cryptographic seal or `execution_success` check.  The call has been moved
  outside the retry `if` block so it executes unconditionally on whichever
  receipt — original or recovery — is carried forward.  The documentation
  invariant ("every receipt is audited before the pipeline advances") now
  holds on every code path.

### Security

- **Phantom Field Closure — `payload_hash` Explicit Equality Assertion**
  (`crypto.py` — `verify_receipt`): `verify_receipt` reconstructed the
  canonical signing manifest using a locally re-derived `payload_hash` rather
  than the value stored in `receipt["payload_hash"]`.  This allowed an
  adversary to replace the stored hash with any arbitrary string; the
  signature check would still pass because the manifest was built from the
  correct re-derived value.  The method now derives `expected_payload_hash`
  and immediately returns `False` when it does not match
  `receipt["payload_hash"]` byte-for-byte, before any Ed25519 operation is
  attempted.  The canonical manifest in Step 2 is then built from
  `receipt["payload_hash"]`, which has been confirmed to be consistent with
  the original payload.

## [0.5.0] - 2026-05-21

### Added

- **`_audit_receipt` Helper** (`__main__.py`): Extracted cryptographic audit
  logic into a dedicated `_audit_receipt(receipt, payload, label)` function.
  Centralises `SovereignKeyManager.verify_receipt` and `execution_success`
  checks, eliminating duplicated abort logic across pipeline and single-tool
  call paths.
- **`SovereignKeyManager._resolve_node_secret`** (`crypto.py`): New private
  helper that reads and validates the `SOVEREIGN_NODE_SECRET` environment
  variable, raising a descriptive `RuntimeError` immediately when the secret is
  absent or blank rather than silently falling back to plaintext storage.
- **Google-Style Docstrings**: Added complete `Args:`, `Returns:`, and
  `Raises:` documentation blocks to every public class, method, and async tool
  function across `crypto.py`, `gateway.py`, `router.py`, and `__main__.py`.
- **Explicit PEP 484 Type Annotations**: Applied complete return-type and
  parameter annotations to all previously untyped functions, including
  `Dict[str, Any]` signatures on async tool callbacks and `-> None` on
  side-effect-only methods.

### Changed

- **`LocalRuntimeRouter.dispatch` Return Type** (`router.py`): Changed from
  `ForensicReceipt` to `tuple[ForensicReceipt, Dict[str, Any]]`.  The
  execution payload is now returned alongside the receipt so call sites can
  independently reconstruct the signed manifest for re-verification without
  duplicating internal router logic.
- **`__main__.py` Dispatch Call Sites**: All three `await router.dispatch(...)`
  calls now unpack the 2-tuple `(receipt, payload)`.  Receipt auditing is
  delegated to `_audit_receipt` at every pipeline step.

### Fixed

- **Unverified Envelope Propagation** (`router.py`): `dispatch` now calls
  `SovereignKeyManager.verify_receipt(receipt, execution_payload)` immediately
  after minting and raises `RuntimeError` if the seal check fails, preventing
  a corrupted receipt from reaching the caller.

### Security

- **Full Envelope Signing** (`crypto.py` — `generate_receipt` /
  `verify_receipt`): The Ed25519 signature previously covered only the raw
  serialised payload, leaving `metadata` (including `execution_success`),
  `timestamp`, and `payload_hash` unsigned and mutable after issuance.  The
  signature now covers a canonical JSON manifest binding all three fields;
  `verify_receipt` reconstructs the identical manifest before checking the
  signature, so any post-issuance mutation of the envelope returns `False`.
- **Encrypted Private Key Storage** (`crypto.py` — `load_or_generate_keypair`):
  Replaced `serialization.NoEncryption()` with
  `serialization.BestAvailableEncryption(passphrase)` when writing the PKCS8
  PEM to disk.  The passphrase is derived from the mandatory
  `SOVEREIGN_NODE_SECRET` environment variable; the node refuses to initialise
  when the variable is absent.  Existing unencrypted PEM files are rejected at
  load time and must be rotated.

## [0.4.1] - 2026-05-20

### Fixed
- **Retry Sequence Ledger Collision**: Corrected an api-level architectural bug where failed dispatches left the tracking index frozen. `execution_depth` now increments unconditionally for every single dispatch transaction attempt, completely preventing cryptographic index collisions or duplicate ledger positions during external loop recovery cycles.

## [0.4.0] - 2026-05-20

### Added
- **Symmetric CLI Failure Signaling**: Integrated strict termination controls across all CLI code paths inside `__main__.py`. Single-tool mode invocations now cleanly mirror the pipeline execution path by forcing an explicit non-zero exit status (`click.Abort()`) upon runtime faults, completely eliminating silent error masking.
- **Isolated Standalone Diagnostic Hydration**: Enhanced `analyze_stored_data` to gracefully intercept standalone debugging operations. If an empty execution boundary is detected, the runtime fallback-hydrates a baseline diagnostic telemetry stream, keeping standalone CLI options functional for out-of-band testing.

### Fixed
- **Ledger Sequence Collision**: Corrected an off-by-one ledger depth assignment flaw within `LocalRuntimeRouter.dispatch`. Stamping indices are now securely locked down *prior* to successful tool mutations, ensuring chronological integrity (`0`-indexed tracking) and preventing signature index duplication across success-to-failure state handoffs.

## [0.3.8] - 2026-05-20

### Fixed
- **Forensic Receipt Depth Collision**: Fixed an off-by-one ledger assignment where `execution_depth` was captured post-mutation. Stamped index allocations are now secured prior to state updates, preserving unique timeline signatures across success/failure transitions.
- **Standalone CLI Invocation Context**: Upgraded `analyze_stored_data` to support standalone context exploration options, fallback-hydrating diagnostic baselines smoothly to prevent standalone invocation crashes.

## [0.3.7] - 2026-05-20

### Fixed
- **Asymmetric CLI Failure Reporting**: Resolved a telemetry reporting gap by introducing explicit validation checks within the single-tool CLI execution path inside `__main__.py`. Single tool failures now print the forensic receipt and cleanly terminate with a non-zero exit status (`Aborted!`), ensuring visibility across containerized and shell orchestrators.
- **Tracking Metrics Integrity**: Reinforced `execution_depth` handling inside `router.py` to prevent post-increment capture pollution, keeping transactional session indexes stable across variable failure scenarios.

## [0.3.6] - 2026-05-20

### Fixed
- **Pipeline Failure Obscurity**: Implemented fail-fast circuit evaluation inside `__main__.py`. Upstream receipts are now immediately verified post-dispatch, gracefully aborting execution chains on failures and preventing misleading downstream errors.
- **Dead Fallback Elimination**: Streamlined `_calculate_stable_arguments_hash` inside `router.py` by removing redundant `try/except` error routing since string serialization coercion is covered natively via `default=str`.


## [0.3.5] - 2026-05-20

### Fixed
- **Public Key Serialization Encoding**: Resolved a `TypeError` crash in `SovereignKeyManager.get_base64_public_key` by ensuring public key raw byte extractions consume a valid `PublicFormat.Raw` enumeration parameter rather than a private key formatter target.

## [0.3.4] - 2026-05-20

### Fixed
- **Core Receipt Instability**: Eradicated the final remaining instance of Python's process-unstable `hash()` call inside `SovereignKeyManager.generate_receipt` (`crypto.py`). Upgraded `payload_hash` generation to use a deterministic SHA-256 hex digest to ensure forensic receipts are cross-process stable.


## [0.3.3] - 2026-05-20

### Fixed
- **Session State Leakage**: Fixed a side-effect bug where `execution_depth` was incremented before tool completion. State mutations now deferred until post-execution confirmation, preventing counter inflation on tool exceptions.
- **CLI Configuration Regression**: Restored dynamic Click terminal configuration bindings (`--tool`, `--resource-id`, `--session-id`) across the async runtime architecture, removing hardcoded script blocks.
- **Dead Import Pruning**: Stripped out an unused `SovereignKeyManager` dependency declaration from `__main__.py`.

## [0.3.2] - 2026-05-20

### Fixed
- **Process Hash Instability**: Eliminated Python's native `hash()` function inside `LocalRuntimeRouter.dispatch` due to runtime seed volatility. Replaced it with a canonical, key-sorted SHA-256 hex-digest loop.
- **Complex Argument Type Crash**: Resolved an unhashable `TypeError` regression when passing complex data arrays (`lists`, `dicts`) to runtime tools by using JSON serialization schemas prior to hashing.

## [0.3.1] - 2026-05-20

### Security
- **Exposed Secret Remediation**: Excluded the `.keys/` cryptographic directory from version control tracking inside `.gitignore` and untracked compromised development certificates.

### Fixed
- **Wildcard Import Runtime Crash**: Fixed a `TypeError` in `sovereign_core.__init__` by casting the `SessionContext` object inside `__all__` to a compliant string literal.
- **Forensic Hash Regression**: Restored the `arguments_hash` generation step inside the asynchronous `LocalRuntimeRouter` pipeline to prevent duplicate receipt structural hashes across varying parameter executions.

## [0.3.0] - 2026-05-20

### Added
- **Asynchronous Execution Architecture**: Upgraded the execution pipeline within `LocalRuntimeRouter` to handle non-blocking, cooperative concurrent tool patterns via `asyncio`.
- **Stateful Session Primitives**: Introduced the `SessionContext` model to track ephemeral state variables, variable scoping, and sequential transaction counts (`execution_depth`) across tool chains.
- **Inter-Tool Data Dependency Support**: Validated state mutation flows where secondary tools seamlessly digest and process outputs cached in volatile memory by preceding tools within the same execution session.

### Changed
- **Pipeline Signatures**: Modified the router verification mechanism to embed `session_id` and transactional metrics cleanly inside the finalized cryptographic payload receipts.

## [0.2.0] - 2026-05-20

### Added
- **Cryptography Implementation**: Added `sovereign_core.crypto` featuring native `cryptography` library integration for asymmetric Ed25519 key derivation.
- **Identity Persistence**: Added secure on-disk keypair caching inside a restricted `.keys/` directory (with `0o600` file permissions) to persist node identities across runtime restarts.
- **Forensic Verification Ledger**: Implemented `ForensicReceipt` (`TypedDict`) payload minting and a strict `ReceiptSchema` validation layer via Pydantic to ensure provenance integrity.
- **Local Dispatch Router**: Created `LocalRuntimeRouter` in `sovereign_runtime.router` to securely register local tool routines, wrap execution outputs, and map immutable cryptographic signatures over results.
- **Interactive CLI Context**: Updated the main executable entry-point (`sovereign-node`) to demonstrate a complete mock payload hash dispatch and automated receipt logging directly to the console.

### Fixed
- **Build Backend Alignment**: Shifted child workspaces from `hatchling` to `setuptools` to completely mitigate editable-link mapping failures (`build_editable` exceptions) within virtual workspaces.
- **Package Layout Discovery**: Added explicit `src-layout` path tracking configuration under `[tool.setuptools.packages.find]` to resolve `ModuleNotFoundError` anomalies on Windows runtime environments.
- **Namespace Cleanups**: Streamlined internal eager imports inside package `__init__.py` files, eliminating stale placeholder structures (`IngestionBoundary`, `PreFlightRouter`) that stalled initialization boundaries.
- **Python Toolchain Binding**: Pinned the root workspace execution target cleanly to Python `3.12` using `uv python pin` to eliminate host configuration skew against global `3.11` fallbacks.