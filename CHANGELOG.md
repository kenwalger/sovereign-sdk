# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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