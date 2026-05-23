# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.0] - 2026-05-22

### Added

- **GitHub Actions CI/CD** (`.github/workflows/test.yml`, `.github/workflows/publish.yml`):
  `test.yml` triggers on every push to `main` and all pull requests; runs
  `uv sync --all-packages` then `uv run pytest` on `ubuntu-latest` with Python 3.12
  via `astral-sh/setup-uv`.  `publish.yml` triggers on `v*` tags; a dedicated `test`
  job mirrors the full CI suite inline and the `publish` job declares `needs: [test]`,
  guaranteeing the pipeline fails closed and blocks PyPI deployment when a tag is
  pushed against a broken or untested commit.  Uses `pypa/gh-action-pypi-publish`
  with OIDC Trusted Publishing (`id-token: write` permission).

- **`sovereign-verify` CLI — JSON object type guard**
  (`packages/sovereign-core/src/sovereign_core/cli.py`): After `json.loads` parses
  the receipt file, an explicit `isinstance(receipt, dict)` check is now enforced.
  If the top-level JSON value is not an object (e.g. an array, string, or bare
  number), the CLI writes a descriptive error to `stderr` and exits `1` before any
  field access is attempted, preventing raw `AttributeError` tracebacks when
  auditors supply malformed or non-object documents.

- **`README.md` — Security provenance repositioning**: Reframed the project as a
  local-first AI WAF and compliance gate.  New introductory section leads with the
  non-repudiation and chain-of-custody value proposition.  Added a "The ForensicReceipt:
  Sealed Transformation Accounting" technical subsection documenting how the Ed25519
  signature binds both `payload_hash` (SHA-256 of the sieved output) and the
  `prose_tax_summary` token-delta metrics inside the same canonical manifest — giving
  auditors mathematical proof that neither the boundary output nor the transformation
  accounting can be altered after issuance.  Prose Tax optimization demoted to an
  optional feature subsection inside the audit envelope narrative.  All standalone
  code snippets wrapped in `asyncio.run()` harnesses for direct executability.

- **PEP 561 `py.typed` marker** (`packages/sovereign-fastapi/src/sovereign_fastapi/py.typed`):
  Empty marker file declaring `sovereign-fastapi` as a typed package.  Included in
  the built distribution via `[tool.setuptools.package-data]` in the package manifest,
  enabling downstream type checkers to resolve `sovereign_fastapi` stubs without
  additional configuration.

### Changed

- **Version promoted to `1.0.0`** across all package manifests
  (`packages/sovereign-core/pyproject.toml`, `packages/sovereign-fastapi/pyproject.toml`,
  root `pyproject.toml`, `sovereign_core.__version__`): Reflects stable, production-ready
  API surface with no further breaking-change windows before a major bump.

- **PyPI metadata injected** (`packages/sovereign-core/pyproject.toml`,
  `packages/sovereign-fastapi/pyproject.toml`): Both package manifests now carry
  `license = { text = "MIT" }`, `[project.classifiers]` (MIT, Python 3.12,
  Intended Audience :: Developers, Topic :: Security, Topic :: Software Development :: Libraries),
  and `[project.urls]` (Homepage, Repository, Changelog) targeting
  `https://github.com/kenwalger/sovereign-sdk`.

- **`sovereign-fastapi` inter-package dependency version-pinned**
  (`packages/sovereign-fastapi/pyproject.toml`): `"sovereign-core"` dependency
  tightened to `"sovereign-core>=1.0.0,<2.0.0"` to prevent accidental major-version
  drift across the workspace.

- **`sovereign-verify` CLI — narrowed exception handling**
  (`packages/sovereign-core/src/sovereign_core/cli.py`): `_verify()` now catches
  `except Exception` (fail-closed fallback comment added) instead of the
  previously redundant `except (InvalidSignature, KeyError, ValueError, Exception)`.
  The now-unused `from cryptography.exceptions import InvalidSignature` import removed.

- **`README.md` — async example wrapped in `asyncio.run(main())` harness**:
  The "Verifying a ForensicReceipt" code snippet is now a complete, runnable
  script with `async def main()` and `asyncio.run(main())` so readers can
  execute it directly without a surrounding async framework.

## [0.8.4] - 2026-05-22

### Added

- **`sovereign-verify` — Stateless ForensicReceipt Verification CLI**
  (`packages/sovereign-core/src/sovereign_core/cli.py`,
  `packages/sovereign-core/pyproject.toml`): New `argparse`-based CLI entry
  point registered as `sovereign-verify`.  Accepts `--receipt FILE` (path to a
  JSON ForensicReceipt) and `--public-key BASE64_KEY` (base64 raw Ed25519 public
  key).  Performs key-pin assertion (receipt `"public_key"` field must match the
  provided key) followed by Ed25519 signature verification over the canonical
  manifest `{"metadata": …, "payload_hash": …, "timestamp": …}`.  Prints
  `Verified  ✓  payload_hash: …` to stdout and exits `0` on success; prints a
  descriptive tamper warning to stderr and exits `1` on any failure.  Requires
  no private key or `SOVEREIGN_NODE_SECRET`, enabling fully stateless out-of-band
  auditing.

- **`examples/fastapi_gateway/app.py`** — Runnable FastAPI application wired with
  `SovereignMiddleware` pointing to `.keys/example_identity.pem`.  Exposes a
  single `POST /api/v1/sanitize` route that echoes the sieved `"text"` field and
  the receipt `payload_hash` back to the caller.  Start with
  `SOVEREIGN_NODE_SECRET=<secret> uv run uvicorn examples.fastapi_gateway.app:app --reload`.

- **`examples/fastapi_gateway/client.py`** — Companion `httpx` client that
  transmits a high-density Prose Tax payload to the example server, prints the
  optimized low-entropy output, and pretty-prints the `X-Sovereign-Receipt-Signature`
  and `X-Sovereign-Tokens-Saved` response headers.

- **`CONTRIBUTING.md`** — Open-source community guidelines specifying the four
  project invariants (local-first, tamper-evidence, zero regression, dependency
  minimalism), pull request requirements, code style rules, and instructions for
  running tests locally via `uv run pytest`.

- **`SECURITY.md`** — Security policy documenting the coordinated private
  disclosure process, in-scope vulnerability categories (key forgery, receipt
  bypass, boundary injection, CLI acceptance of tampered receipts), response
  timeline commitments, and contact address.

- **`README.md` — "Running the Workspace Examples" section**: Documents the exact
  `uv run` commands to start the example FastAPI server, execute the example
  client, and invoke the `sovereign-verify` CLI with sample output.

### Changed

- **`SovereignMiddleware` — PEP 8 Import Grouping** (`middleware.py`): Stdlib
  imports (`json`, `logging`, `typing`) are now grouped first, followed by a
  blank-line-separated Starlette framework block, then a local block for
  `sovereign_core.gateway`.  The module-level `logger` assignment is placed
  after all import blocks rather than inline between stdlib and framework imports.

- **`SovereignMiddleware.dispatch` — Strict-Mode Critical Log** (`middleware.py`):
  When `strict_mode=True`, the exception handler now emits
  `logger.critical("Sovereign boundary processing failed under strict enforcement. Aborting request.", exc_info=True)`
  immediately before returning the HTTP 422 response.  Previously, strict-mode
  failures were invisible to structured log consumers: only the 422 response
  status was observable, with no log record emitted.

## [0.8.3] - 2026-05-22

### Added

- **`SovereignKeyManager.has_identity` — Public State Property** (`crypto.py`):
  New read-only `@property` that returns ``True`` when both ``_private_key`` and
  ``_public_key`` are non-``None`` (fully initialised in memory), ``False``
  otherwise.  Provides a clean, public alternative to private-attribute inspection
  from outside the class, eliminating the need for callers to pierce internal
  slots to determine whether the keypair is loaded.

- **`TestSovereignGateway.test_gateway_export_public_key_uses_state_not_string_matching`**
  (`test_gateway.py`): Three-step architectural contract test for the
  ``has_identity``-based refactor of ``export_public_key()``.  Asserts that
  ``has_identity`` is ``False`` on a fresh gateway, transitions to ``True`` after
  the first call triggers lazy initialization, and that a subsequent call with
  ``load_or_generate_keypair`` mocked confirms the method does not attempt to
  reload — proving the implementation relies entirely on explicit state gating
  rather than string-in-exception matching.

- **`TestSovereignMiddleware.test_middleware_emits_error_log_on_interception_failure`**
  (`test_middleware.py`): Observability contract test for the structured error
  log added to the non-strict bypass path.  Sends a JSON payload missing the
  configured ``payload_field`` key to trigger a ``KeyError`` inside the
  middleware try block.  Uses ``pytest.LogCaptureFixture`` (``caplog``) to
  assert that (a) the request completes with HTTP 200 (defensive bypass intact)
  and (b) at least one ``logging.ERROR`` record is emitted to the
  ``sovereign_fastapi`` logger containing the phrase
  ``"Sovereign boundary processing failed"``.

### Changed

- **`SovereignGateway.export_public_key()` — State-Based Key Loading**
  (`gateway.py`): Replaced the ``try/except RuntimeError`` block (which
  matched the sentinel string ``"Keypair not loaded"`` inside the exception
  message) with a direct ``if not self._key_manager.has_identity:`` state
  check.  The refactor removes all coupling to internal exception message
  strings and makes the gating logic explicit and unconditionally correct.

- **`SovereignMiddleware.dispatch` — Structured Error Logging** (`middleware.py`):
  The non-strict exception bypass path now emits a ``logging.ERROR`` record via
  ``logger.error("Sovereign boundary processing failed. Bypassing defensively …",
  exc_info=True)`` before falling through.  Previously the bypass was entirely
  silent, making interception failures invisible to log-based observability
  systems.  Full exception context is captured via ``exc_info=True``.

- **`sovereign-fastapi` Starlette Dependency Range** (`pyproject.toml`):
  Narrowed from ``starlette>=0.27.0`` to ``starlette>=0.35.0,<1.0.0`` to
  anchor the package against the tested private-attribute contract
  (``request._body`` / ``_CachedRequest.wrapped_receive``) and prevent
  unguarded drift into a potential Starlette 1.x major-version break.

## [0.8.2] - 2026-05-22

### Added

- **`TestSovereignMiddleware.test_middleware_corrects_content_length_header`**
  (`packages/sovereign-fastapi/tests/test_middleware.py`): Functional regression
  test for the v0.8.1 `Content-Length` realignment fix.  A custom route handler
  echoes back both the `content-length` header it observes on the inbound request
  and the actual byte count of the body it reads via `await request.body()`.  A
  filler-dense payload (`"hi please just certainly of course help me now"` → sieved
  to `"help me now"`) ensures the body shrinks measurably, making a stale header
  produce a clearly visible mismatch.  Asserts that both values are identical,
  confirming that `request.scope["headers"]` was patched in-place by the middleware
  so downstream ASGI consumers never hang waiting for bytes that no longer exist.

- **`TestSovereignGateway.test_gateway_export_public_key_bubbles_unrelated_runtime_errors`**
  (`packages/sovereign-core/tests/test_gateway.py`): Defensive regression test for
  the v0.8.1 `export_public_key()` exception-narrowing fix.  Uses
  `unittest.mock.patch.object` with `PropertyMock` to make the underlying
  `SovereignKeyManager.public_key` property raise
  `RuntimeError("Unexpected database disk full error")`.  Asserts that (a) the
  exception propagates to the caller unchanged via `pytest.raises`, and (b)
  `load_or_generate_keypair()` — mocked and tracked independently — is never
  invoked.  This guarantees that system faults fail loudly rather than triggering
  silent key regeneration that would replace the node's cryptographic identity.

## [0.8.1] - 2026-05-22

### Fixed

- **`SovereignMiddleware` — `Content-Length` Header Realignment** (`middleware.py`):
  After the Prose Tax sieve shrinks or rewrites the JSON body, the inbound
  `Content-Length` header retained the original byte count.  Downstream ASGI
  handlers, routers, and reverse proxies that validate header/body length parity
  could therefore encounter a stale-length payload mismatch or a hanging read
  timeout waiting for bytes that no longer exist.  Fixed by computing
  ``len(new_body)`` immediately after ``request._body`` is overwritten and
  patching ``request.scope["headers"]`` in place: existing ``content-length``
  entries are replaced in a single list comprehension pass, and the header is
  appended if it was absent.

- **`SovereignGateway.export_public_key()` — RuntimeError Path Narrowing**
  (`gateway.py`): The bare ``except RuntimeError:`` guard intercepted every
  ``RuntimeError`` raised inside ``self._key_manager.public_key``, including
  system-level failures such as disk faults and file permission errors.
  Swallowing those errors silently triggered ``load_or_generate_keypair()``,
  risking the generation of a rogue cryptographic identity on a node that
  could not safely read its existing key material.  The handler is narrowed to
  ``except RuntimeError as e:`` with an explicit
  ``"Keypair not loaded" in str(e)`` guard; any ``RuntimeError`` whose message
  does not match the documented sentinel is re-raised immediately so the SDK
  fails loudly and defensively.

## [0.8.0] - 2026-05-22

### Added

- **`sovereign-fastapi` — Phase 5 ASGI Middleware Package** (new workspace member
  `packages/sovereign-fastapi/`): Implements Phase 5 of the Sovereign Systems
  Specification by introducing a drop-in `SovereignMiddleware` class for
  FastAPI and Starlette applications.  The class inherits from Starlette's
  `BaseHTTPMiddleware` and intercepts every inbound JSON request in its
  `dispatch` lifecycle:
  (1) Extracts the target text from the configurable `payload_field` key (or
  falls back to the serialised root body when no field is specified).
  (2) Invokes `await self.gateway.sieve_and_sign(target_text)` to strip Prose
  Tax filler, normalise whitespace, and mint a fully signed Ed25519
  `ForensicReceipt` in coroutine-local scope — concurrent requests sharing the
  same middleware instance never bleed per-request metrics into each other's
  receipts.
  (3) Overwrites `request._body` on the `_CachedRequest` instance so that
  `_CachedRequest.wrapped_receive` serves the cleaned payload to every downstream
  route handler transparently, without requiring any code changes in the application.
  (4) Caches the sealed receipt at `request.state.sovereign_receipt` for
  in-route auditability.
  (5) Injects `X-Sovereign-Receipt-Signature` (Ed25519 base64 signature) and
  `X-Sovereign-Tokens-Saved` (cumulative FinOps savings counter) headers on
  the outbound response.
  The constructor accepts `signing_key: str`, `strict_mode: bool = False`
  (returns HTTP 422 on any interception error rather than falling through), and
  `payload_field: Optional[str] = None`.  Importable as
  `from sovereign_fastapi.middleware import SovereignMiddleware`.

- **`sovereign-fastapi` Test Suite** (`packages/sovereign-fastapi/tests/test_middleware.py`):
  Four functional test cases covering the middleware contract:
  (1) `test_middleware_happy_path` — asserts that a filler-laden inbound
  payload is sieved to `"help me now"` before the route handler sees it, and
  that both `X-Sovereign-*` response headers are present and well-formed.
  (2) `test_middleware_passthrough_for_non_json` — verifies that requests with
  non-JSON content types bypass sieve processing entirely and produce no receipt
  header.
  (3) `test_middleware_strict_mode_missing_field` — confirms that `strict_mode=True`
  returns HTTP 422 when the `payload_field` key is absent from the JSON body.
  (4) `test_middleware_concurrency_isolation` — fires 20 simultaneous requests
  (10 filler + 10 clean payloads) via `asyncio.gather` and asserts that every
  response body reflects the correct minimized form of its own payload, proving
  that the coroutine-local `OptimizationReceipt` capture in `sieve_and_sign`
  prevents cross-request metric contamination under concurrent load.

- **Workspace `--import-mode=importlib`** (`pyproject.toml`): Added
  `addopts = "--import-mode=importlib"` to `[tool.pytest.ini_options]` so that
  pytest resolves test module paths via the `importlib` import system.  This
  prevents the `ModuleNotFoundError` collision that arises when multiple
  workspace packages each contain a `tests/` directory with an `__init__.py`,
  which the default `prepend` import mode treats as a single shared `tests`
  package.

- **Workspace dev-dependencies** (`pyproject.toml`): Added `fastapi>=0.100.0`,
  `httpx>=0.24.0`, and `sovereign-fastapi` to the root workspace dev-dependencies
  so that `uv run pytest` discovers and executes the new middleware test suite
  without requiring per-package invocations.

## [0.7.2] - 2026-05-22

### Added

- **`SovereignGateway` — Unified High-Level Developer Interface** (`gateway.py`):
  Introduced the `SovereignGateway` class as the primary entry point for application
  code interacting with the Sovereign Systems SDK.  The class accepts a `signing_key`
  file path (defaulting to `.keys/sovereign_identity.pem`), defensively creates the
  key directory via `os.makedirs(..., exist_ok=True)` before initializing the
  `SovereignKeyManager` (preventing `FileNotFoundError` in stateless containerized
  environments), and exposes four public methods:
  (1) `async sieve(text_payload: str) -> str` — delegates to `process_prose_tax` to
  strip conversational boilerplate, remove filler tokens, and normalize whitespace,
  returning the purified string and accumulating optimization metrics in the gateway's
  internal `SessionContext`; (2) `sign(clean_context: str) -> ForensicReceipt` — wraps
  `SovereignKeyManager.generate_receipt` to produce a fully minted, Ed25519-signed
  `ForensicReceipt` envelope that seals both data provenance and FinOps
  cost-optimization telemetry in a single cryptographic manifest (see below);
  (3) `async sieve_and_sign(text_payload: str) -> SovereignBoundaryResponse` — one-shot
  macro that internally calls `sieve()` then `sign()` and returns both the purified
  string and the sealed receipt together in a `SovereignBoundaryResponse` Pydantic model
  (see below); (4) `export_public_key() -> str` — returns the base64-encoded Ed25519
  public verification key, triggering keypair loading if not yet initialised, enabling
  out-of-band receipt verification by third parties who hold only the public key.
  The class is importable from `sovereign_core.gateway`.

- **`SovereignBoundaryResponse` — Structured Pydantic Return Model** (`gateway.py`):
  Added `SovereignBoundaryResponse(BaseModel)` as the return type of `sieve_and_sign()`,
  replacing the previous `SieveAndSignResult` TypedDict.  Exposes two attributes:
  `content: str` (the minimized payload) and `receipt: Dict[str, Any]` (the
  `ForensicReceipt` envelope).  Provides attribute-style access (`result.content`,
  `result.receipt`) rather than error-prone dict key lookups, and is validated by
  Pydantic on construction.  Exportable from `sovereign_core.gateway`.

- **`SovereignGateway.__init__` — Defensive Key Directory Creation** (`gateway.py`):
  Added `os.makedirs(key_path.parent, exist_ok=True)` as the first operation in
  `__init__`, executed before `SovereignKeyManager` is constructed.  Prevents
  unhandled `FileNotFoundError` crashes in stateless containerized environments
  (Docker, Kubernetes, CI runners) where the `.keys/` directory is not guaranteed
  to pre-exist.

- **`SovereignGateway.export_public_key()` — Public Verification Key Export**
  (`gateway.py`): New method that returns the base64-encoded raw 32-byte Ed25519
  public key for the gateway's signing identity.  Triggers lazy keypair loading if
  the `SovereignKeyManager` has not yet been initialised.  The returned value is
  identical to `receipt["public_key"]` in every `ForensicReceipt` minted by this
  gateway instance, enabling third-party receipt verification against a distributed
  public key without access to the node secret.

- **`SieveAndSignResult` — TypedDict Legacy Export** (`gateway.py`):
  The original `SieveAndSignResult` TypedDict remains exported from
  `sovereign_core.gateway` for backwards compatibility.  New code should use
  `SovereignBoundaryResponse` instead.

- **`ROADMAP.md` — Sovereign Systems Specification Strategic Plan** (repo root):
  Added `ROADMAP.md` documenting the full long-range architectural trajectory.
  Summarises the four completed phases (cryptographic identity, Prose Tax
  optimization, testing infrastructure, `SovereignGateway` interface).  Specifies
  Phase 5 drop-in framework middleware targets — `sovereign-fastapi`
  (`SovereignMiddleware` ASGI), `sovereign-django` (`SovereignDjangoMiddleware`),
  `sovereign-langchain` (`SovereignCallbackHandler` + `SovereignChain`), and
  `sovereign-llamaindex` (`SovereignNodePostprocessor` + `SovereignQueryTransform`)
  — with copy-paste-accurate usage examples for each.  Specifies Phase 6
  verification key protocol targets — `PublicKeyBundle` export format,
  `sovereign-verify` CLI, `SovereignKeyManager.rotate_keypair()` succession
  receipts, and `FederatedVerifier` multi-node trust registry.  Closes with four
  non-negotiable guiding invariants (local-first, tamper-evidence, zero regression,
  dependency minimalism).

- **Dual-Receipt Cryptographic Manifest — Prose Tax Telemetry Fusion** (`gateway.py`
  — `SovereignGateway.sign`): When `sieve()` has been called on a gateway instance
  prior to `sign()`, the Prose Tax optimization metrics accumulated in the internal
  `SessionContext` are injected into the `ForensicReceipt` metadata envelope under
  the `"prose_tax_summary"` key before the Ed25519 signature is computed.  This
  means the signed manifest atomically covers both the cleansed payload and its
  FinOps cost profile: `raw_token_count` and `optimized_token_count` (estimated
  via UTF-8 byte-density heuristic), `tokens_eliminated` (the raw minus optimized
  delta), `tax_savings_percentage` (percentage reduction rounded to four decimal
  places), and `total_tokens_saved` (accumulated savings across all `sieve()` calls
  on the same gateway instance).  When `sieve()` has not been called, `sign()`
  behaves identically to its prior form and omits the `"prose_tax_summary"` key.
  Any post-issuance mutation of the telemetry values — like all other metadata
  fields — causes `verify_receipt` to return `False`.

- **`TestSovereignGateway` — High-Level Interface Test Suite** (`test_gateway.py`):
  Added 11 functional test cases covering the `SovereignGateway` interface:
  (1) `test_sieve_returns_string` — confirms the return type is always `str`;
  (2) `test_sieve_strips_filler_tokens` — verifies full filler removal pipeline
  (`hi`, `please`, `just`) yields the expected clean output;
  (3) `test_sieve_normalizes_whitespace` — confirms multi-space runs collapse to
  single spaces; (4) `test_sieve_preserves_guarded_phrases` — asserts that
  instructional directives like `make sure to` pass through unmarred;
  (5) `test_sign_returns_forensic_receipt_fields` — checks all five required
  `ForensicReceipt` fields (`timestamp`, `payload_hash`, `public_key`,
  `signature`, `metadata`) are present; (6) `test_sign_receipt_is_cryptographically_valid`
  — performs a full `SovereignKeyManager.verify_receipt` pass against the
  original payload, proving end-to-end signing fidelity;
  (7) `test_sign_receipt_fails_on_tampered_payload` — asserts that `verify_receipt`
  returns `False` when the payload is mutated after signing, confirming
  tamper-evidence; (8) `test_sign_receipt_metadata_contains_source_tag` — verifies
  the `source` annotation is stamped into every receipt;
  (9) `test_gateway_sieve_and_sign_atomic` — calls `sieve_and_sign()` in one
  invocation and asserts the result is a `SovereignBoundaryResponse` with
  `result.content == "help me now"`, all five receipt fields present via
  `result.receipt`, `verify_receipt` passes, and `prose_tax_summary` is fused
  and arithmetically consistent — using attribute access throughout;
  (10) `test_gateway_export_public_key` — asserts the returned string is base64-
  decodable to exactly 32 bytes (Ed25519 key size) and matches `receipt["public_key"]`
  in receipts from the same gateway instance;
  (11) `test_gateway_fuses_prose_tax_metadata` — calls `sieve()` then `sign()` on
  the same gateway instance and asserts that `receipt["metadata"]["prose_tax_summary"]`
  contains all five keys, carries exact arithmetic-verified token counts
  (`raw=6`, `optimized=2`, `eliminated=4` for the 26-byte input `"hi please just
  help me now"`), and that the enriched envelope still passes full Ed25519
  `verify_receipt` verification — confirming dual-receipt manifest integrity.

- **pytest-asyncio Loop Scope Configuration** (`pyproject.toml`):
  Added `asyncio_default_fixture_loop_scope = "function"` to
  `[tool.pytest.ini_options]` to explicitly configure per-function fixture loop
  scoping and silence the pytest-asyncio loop deprecation warning.  The
  `pytest-asyncio` dependency floor is tightened from `>=0.23.0` to `>=1.0.0`
  to explicitly track the installed v1.x major release block.

### Changed

- **Documentation — `PHILOSOPHY.md` "Intended Usage" Sync**: Updated the
  illustrative code block in the "Intended Usage" section to mirror the
  implemented `SovereignGateway` API exactly.  The import is corrected to
  `from sovereign_core.gateway import SovereignGateway`; `signing_key` is
  updated to the canonical `.keys/sovereign_identity.pem` default; `gateway.sieve()`
  is prefixed with `await` to match its `async def` signature; and the
  `receipt.uuid` attribute reference is replaced with `receipt["payload_hash"]`
  to reflect `ForensicReceipt`'s TypedDict access pattern.

- **Documentation — `README.md` Primary Interface Showcase**: Added a new
  "Primary Developer Interface: `SovereignGateway`" section at the top of the
  implementation target block.  Provides a complete, copy-paste-ready usage
  example — including gateway initialization, async `.sieve()` call, `.sign()`
  receipt minting, and an independent `verify_receipt` check — establishing
  `SovereignGateway` as the canonical integration pattern for downstream
  application code.

### Fixed

- **`SovereignGateway.__init__` — Key Filename Contract** (`gateway.py`):
  When a custom `signing_key` path such as `"vault/node-alpha.pem"` was passed,
  only the parent directory was forwarded to `SovereignKeyManager`, which
  silently replaced the caller-specified filename with the hardcoded default
  `sovereign_identity.pem`.  The fix resolves the full absolute path via
  `Path(...).resolve()`, constructs `SovereignKeyManager` with the parent
  directory as before, then immediately overrides `private_key_path` and
  `public_key_path` on the key manager instance to honour the exact filename
  provided.  The companion `.pub` path is derived via `key_path.with_suffix(".pub")`.

- **`SovereignGateway.sieve_and_sign` — Concurrent Session Contention**
  (`gateway.py`): The original one-shot macro called `self.sieve()` (which
  wrote Prose Tax metrics into the shared `self._session`) and then `self.sign()`
  (which read those metrics back from the same shared session).  Under concurrent
  async load on the same gateway instance, a second request's `sieve()` could
  overwrite the session before the first request's `sign()` read it, silently
  fusing the wrong metrics into the receipt.  Fixed by having `sieve_and_sign`
  call `process_prose_tax` directly, capturing the returned `OptimizationReceipt`
  in the coroutine's local scope, and forwarding it to `sign()` via the new
  `prose_tax_receipt=` keyword argument.  The `sign()` method now branches on
  this argument: when present it reads only from the locally scoped receipt
  (concurrent-safe); when absent it falls back to session state (preserving the
  existing two-step `sieve()` + `sign()` workflow).

- **Documentation — Pydantic Attribute Access Alignment** (`PHILOSOPHY.md`,
  `README.md`): All usage examples updated from legacy dict subscripts
  (`result["content"]`, `result["receipt"]`) to `SovereignBoundaryResponse`
  Pydantic v2 attribute access (`result.content`, `result.receipt`).

- **Test Filesystem Isolation — Sieve-Only Key Path Hardening** (`test_gateway.py`):
  Four sieve-only tests updated to accept `tmp_path` and pass an isolated
  temporary key path to `SovereignGateway(signing_key=...)`, preventing a stray
  `.keys/` directory from being created at the workspace root during test sweeps.

- **`total_tokens_saved` — Cumulative FinOps Semantic Alignment** (`gateway.py`,
  `test_gateway.py`): The `total_tokens_saved` field in `prose_tax_summary` carried
  different semantics across the two execution tracks.  The sequential `sieve()` +
  `sign()` path correctly read the cumulative session accumulator (all prior sieve
  calls on the instance), while the concurrent-safe one-shot `sieve_and_sign()` path
  incorrectly emitted only the per-call delta (`raw - opt`), making the field
  meaningless for multi-call FinOps accounting.  Fixed by adding a
  `total_tokens_saved: Optional[int] = None` keyword argument to `sign()`.
  `sieve_and_sign()` now reads `self._session.get("prose_tax_total_savings", 0)` once
  — immediately after `process_prose_tax` has updated the accumulator — and forwards
  it via `total_tokens_saved=`.  The `sign()` method uses this explicit value in the
  one-shot branch and continues reading the session accumulator in the two-step branch,
  guaranteeing identical cumulative semantics across both tracks.  Verified by the new
  `test_gateway_telemetry_semantic_consistency` test case.

- **`SovereignGateway.export_public_key()` — Private Attribute Encapsulation**
  (`gateway.py`): The original implementation guarded keypair loading with
  `if not self._key_manager._private_key:`, directly piercing the private
  `._private_key` slot and treating any falsy value as an unloaded keypair.  The fix
  replaces this with a `try/except` wrapper around `self._key_manager.public_key`:
  the property raises `RuntimeError('Keypair not loaded.')` when the keypair has
  not been initialised, which is caught to trigger `load_or_generate_keypair()`
  before the key is returned.  This eliminates the private attribute dependency and
  uses only the documented public interface.

## [0.7.0] - 2026-05-21

### Added

- **Phase 3 Testing Framework — `pytest` + `pytest-asyncio` Infrastructure** (root `pyproject.toml`):
  Added `pytest>=8.0.0` and `pytest-asyncio>=0.23.0` as workspace-level development dependencies
  and configured `asyncio_mode = "auto"` under `[tool.pytest.ini_options]` so async test coroutines
  are discovered and executed without explicit per-function decorator decoration.

- **`tests/` Topology — `packages/sovereign-core/tests/`**: Established a unified test topology
  by creating the `tests/` directory (with `__init__.py`) inside `packages/sovereign-core/`.
  The `testpaths` ini option is set to `["packages"]` so that `uv run pytest` discovers
  any future package's `tests/` directory automatically without requiring manual path additions.

- **`test_crypto.py` — Cryptographic Identity Test Suite** (`packages/sovereign-core/tests/`):
  Authored 19 fully type-hinted, docstring-backed test cases across four classes covering:
  (1) `TestGreenfieldKeypairGeneration` — Ed25519 keypair generation returns valid 32-byte key
  material, persists both PEM files, and exposes a consistent `public_key` property;
  (2) `TestFchmodPermissionLocking` — private key file is non-empty, 0o600-permissioned on POSIX
  (skipped on Windows with a platform-guard assertion), leaves no staging temp file after
  `os.replace()`, and routes correctly through the `hasattr(os, 'fchmod')` branch;
  (3) `TestWrongSecretKeyLoad` — wrong, missing, and blank `SOVEREIGN_NODE_SECRET` values each
  raise `RuntimeError` before any key bytes are read or written;
  (4) `TestForensicReceiptMinting` — freshly minted receipts contain all five required fields,
  pass full key-pin/payload-hash/Ed25519 verification, carry a 64-byte signature, and return
  `False` for tampered metadata, rogue key pins, altered payloads, and explicit metadata round-trips.

- **`test_gateway.py` — Prose Tax Optimization Layer Regression Suite**
  (`packages/sovereign-core/tests/`): Authored 31 fully type-hinted, async-native regression
  test cases across five classes insulating `process_prose_tax` from future regressions:
  (1) `TestFillerTokenStripping` — `hi`, `hello`, `please`, `certainly`, `of course`,
  `I hope this`, and `just` are cleanly stripped while the remainder of each sentence is preserved;
  (2) `TestHighIntegrityPhrasePassthrough` — `make sure to`, `hello-world`, `hi-fi`,
  `certainly-not`, `be sure to`, and `absolutely-not` pass through the regex library completely
  unmarred, each exercising a distinct lookahead or lookbehind guard;
  (3) `TestCaseSensitiveHeadingPreservation` — identical duplicate headings are collapsed to one
  copy via the `\\1` backreference, while headings that differ in case, level, or position are
  preserved; (4) `TestWhitespaceNormalization` — multiple spaces collapse to one, orphaned spaces
  before commas and periods are absorbed, and multi-filler removal leaves no double-space artifact;
  (5) `TestDictMessageListStructure` — list-of-dicts input returns a list, all non-content keys
  are forwarded unmodified, content fields are minimized, messages without string content pass
  through intact, list length is preserved, and the `SessionContext` receives the
  `prose_tax_receipt` entry after each pass.

- **Automated `.env` Environment Hydration** (`__main__.py`): Added
  `python-dotenv>=1.0.0` as a runtime dependency and wired `load_dotenv()` at
  the absolute top of the node entrypoint.  `SOVEREIGN_NODE_SECRET` (and any
  other environment overrides) can now be specified in an uncommitted `.env`
  file at the repository root, eliminating the need to export variables
  manually before running `uv run sovereign-node`.

- **Prose Tax Optimization Layer — Phase 2 Scaffold** (`gateway.py`):
  Initialized the core architectural scaffold for the Prose Tax Optimization
  Layer inside `gateway.py`.  Added `OptimizationReceipt`, a Pydantic model
  tracking `raw_token_count`, `optimized_token_count`, and
  `tax_savings_percentage` for each compression pass.  Added the async gateway
  entry point `process_prose_tax(payload, context)`, which executes three
  sequential phases: Text Minimization (stripping conversational filler,
  greeting tokens, hedging preambles, and redundant markdown decoration via a
  compiled regex library), Local Token Approximation (UTF-8 byte-density
  heuristic; no external tokenizer), and Ledger Accumulation (writing the
  `OptimizationReceipt` and running savings total directly into
  `context.variables` so every downstream `ForensicReceipt` envelope carries a
  complete optimization audit trail).

### Fixed

- **Prose Tax — Duplicate Heading Deduplication Case-Sensitive** (`gateway.py`
  — `_FILLER_PATTERNS`): Removed ``re.IGNORECASE`` from the duplicate-heading
  compilation so that headings are only collapsed when their text and
  capitalisation match exactly.  The previous case-insensitive form silently
  mutated distinct headings that differed only in case (e.g.
  ``## Setup`` followed by ``## setup`` would have been collapsed to one).

- **Prose Tax — Whitespace Normalization Pass** (`gateway.py` —
  ``process_prose_tax``): Added a post-substitution whitespace normalization
  step in both the string and list-message processing branches.  After all
  filler patterns are applied, consecutive spaces are collapsed to a single
  space and orphaned spaces before punctuation characters are removed.  This
  cleans up double-space and space-punctuation artifacts that filler-word
  removal leaves behind.

- **Crypto — Class Docstring Aligned with Descriptor-Level Architecture**
  (`crypto.py` — ``SovereignKeyManager``): Updated the class-level docstring
  to replace the stale reference to path-based ``os.chmod`` with an accurate
  description of the current descriptor-level ``os.fchmod`` approach and its
  ``os.chmod`` fallback for platforms that lack ``fchmod``.

- **Runtime — E402 noqa Guards Applied** (`__main__.py`): Appended
  ``# noqa: E402`` suppression comments to every import line that follows the
  early ``load_dotenv()`` invocation, ensuring PEP 8 module-level import-order
  rules do not flag the intentional environment-hydration-first pattern.

- **Prose Tax — Preamble Pattern Greedy Wildcard Removed** (`gateway.py` —
  `_FILLER_PATTERNS`): Corrected the ``"I hope"`` preamble regex to remove
  the greedy trailing ``.*`` wildcard that consumed the entire remainder of
  the line, causing silent deletion of any sentence content that followed the
  phrase.  The pattern now terminates at the word boundary immediately after
  the subject pronoun (``\bI hope (?:this|that|you)\b``), stripping only the
  literal preamble token itself and leaving all trailing content on the same
  line perfectly intact.  The capturing group ``(this|that|you)`` is converted
  to a non-capturing group ``(?:this|that|you)`` as the capture was unused.

- **Prose Tax — List Payload Structure Preserved on Return** (`gateway.py` —
  ``process_prose_tax``): Stabilized the list-input branch to return the
  original message list structure with each ``"content"`` field individually
  minimized, instead of collapsing the structure into a flat joined string.
  Messages without a string ``"content"`` field are forwarded unmodified.
  All other dict keys within each message are preserved via shallow copy.
  The return type annotation is updated from ``tuple[str, OptimizationReceipt]``
  to ``tuple[Union[str, List[Dict[str, Any]]], OptimizationReceipt]`` to
  reflect list-in/list-out structural parity.  Token approximation metrics
  continue to use the joined minimized string internally.

- **Prose Tax — Affirmation Alternation Unified Under Global Lookahead**
  (`gateway.py` — `_FILLER_PATTERNS`): Unified the ``certainly`` and
  ``absolutely`` affirmation tokens inside a non-capturing group so that the
  ``(?![-\w])`` negative lookahead applies collectively to both options.  The
  previous naked ``\bcertainly\b|\babsolutely\b`` form carried no lookahead
  guard, making it vulnerable to corrupting hyphenated compound words
  (e.g. ``"certainly-not"`` → ``"-not"``).  Pattern replaced with
  ``\b(?:certainly|absolutely)\b(?![-\w])``.  The ``of course`` phrase-anchor
  and the dual-guarded ``sure`` sub-pattern are unchanged.  Inline comment
  restructured into a three-bullet breakdown documenting each sub-pattern's
  distinct guard rationale.

- **Prose Tax — Greeting Alternation Unified Under Global Lookahead**
  (`gateway.py` — `_FILLER_PATTERNS`): Unified the greeting alternation
  string inside a non-capturing group so that the ``(?![-\w])`` negative
  lookahead guard applies collectively to all four tokens.  The previous
  per-token form ``\bhi\b(?![-\w])|\bhello\b|\bhey\b|\bgreetings\b``
  guarded only ``hi``, leaving ``hello``, ``hey``, and ``greetings``
  unguarded and vulnerable to mangling hyphenated compound strings (e.g.
  ``"hello-world"`` → ``"-world"``).  Pattern replaced with
  ``\b(?:hi|hello|hey|greetings)\b(?![-\w])``.  Inline comment updated to
  document the grouping rationale.

- **Prose Tax — `sure` Affirmation Directive Guard** (`gateway.py` —
  `_FILLER_PATTERNS`): Corrected the ``sure`` affirmation pattern to enforce
  absolute mechanical symmetry between comment promises and regex behavior.
  The previous ``\bsure\b(?![-\w])`` lookahead-only guard did not protect
  instructional directives like ``"make sure to"`` because the lookahead
  examines only what follows ``sure`` (a space, which passes the guard), not
  what precedes it.  Added paired negative lookbehinds
  ``(?<!make )(?<!be )`` before ``\bsure\b`` so that ``sure`` is suppressed
  only when it appears as a standalone colloquial modifier, never as the
  anchor of an instructional phrase.  Both lookbehinds are fixed-width (5
  characters) and honor ``re.IGNORECASE`` at runtime.

- **Prose Tax — Hedging Adverb Coverage Expanded** (`gateway.py` —
  `_FILLER_PATTERNS`): Added ``actually``, ``basically``, and ``probably``
  to the hedging-adverb entry, each guarded with an explicit
  ``(?![-\w])`` negative lookahead to insulate technical documentation prose
  and hyphenated compound forms from accidental optimization corruption.

- **Repository — `.mailmap` Attribution Anchor** (`.mailmap`): Confirmed
  a root ``.mailmap`` file is present with a single clean entry mapping the
  ``noreply@anthropic`` committer identity to
  ``Ken W. Alger <kenalger@comcast.net>``, standardizing Git author graph
  attribution across all local workspace environments.

- **Prose Tax — `hi` Greeting Boundary Hardened** (`gateway.py` —
  `_FILLER_PATTERNS`): Hardened the conversational ``hi`` greeting regex
  boundary with a negative lookahead guard to prevent corruption of technical
  terms like ``hi-fi``, ``hi-res``, and ``hi-DPI``.  The previous ``\bhi\b``
  pattern matched the ``hi`` prefix in hyphenated compounds because ``\b``
  fires at the alphanumeric/hyphen boundary.  The pattern is updated to
  ``\bhi\b(?![-\w])``, suppressing the match when ``hi`` is immediately
  followed by a hyphen or word character.

- **Repository Tracking — `CLAUDE.md` Removed from Git Index**
  (`.gitignore` / git index): Removed ``CLAUDE.md`` from the active git
  tracking index to properly honor the repository ``.gitignore`` boundary.
  The file is already listed in ``.gitignore`` but remained tracked due to a
  prior ``git add``; ``git rm --cached CLAUDE.md`` cleanly removes it from
  the index while leaving the physical file intact on disk.

- **Prose Tax — `process_prose_tax` Returns Optimized Text Payload**
  (`gateway.py` — `process_prose_tax`): Corrected `process_prose_tax` to
  explicitly return the optimized text payload string alongside its tracking
  analytics receipt.  The previous return type was ``OptimizationReceipt`` only,
  making the cleansed text inaccessible to downstream consumers.  The function
  now returns ``tuple[str, OptimizationReceipt]`` — ``(minimized_text, receipt)``
  — and the docstring ``Returns:`` block and type annotation are updated to
  match.

- **Prose Tax — `sure` Affirmation Boundary Hardened** (`gateway.py` —
  `_FILLER_PATTERNS`): Hardened the conversational ``sure`` affirmation regex
  boundary with a negative lookahead guard to protect technical compound words
  from corruption.  The previous ``\bsure\b`` pattern matched the ``sure``
  prefix in hyphenated compounds such as ``sure-fire`` and ``sure-footed``
  because ``\b`` fires at the alphanumeric/hyphen boundary.  The pattern is
  updated to ``\bsure\b(?![-\w])``, suppressing the match when ``sure`` is
  immediately followed by a hyphen or word character.

- **Prose Tax — Bold/Italic Markdown Deduplication Bounds** (`gateway.py` —
  `_FILLER_PATTERNS`): Refined bold/italic markdown deduplication patterns to
  preserve valid formatting tags while stripping only degenerate token
  repetitions.  The previous ``(\*{1,3})(\s*\1)+`` pattern was overbroad: it
  matched the closing ``**`` of one bold span and the opening ``**`` of the next
  when only whitespace separated them (e.g. ``**foo** **bar**`` was corrupted
  to ``**foo**bar**``).  The pattern is replaced with ``\*{4,}|_{4,}``, which
  targets only runs of four or more consecutive bare asterisks or underscores.
  All valid Markdown wrappers (``*italic*``, ``**bold**``, ``***bold-italic***``,
  ``__double__``, ``___triple___``) are left completely untouched.

- **`example.env` — Synthetic Placeholder Sanitization** (`example.env`):
  Sanitized ``example.env`` to deploy explicit synthetic placeholders,
  eliminating credential-leak hazards for local node installations.  The
  previous value ``"super-secret-passphrase"`` was realistic enough to be copied
  verbatim into a live ``.env`` file without triggering alarm.  The value is
  replaced with the screaming placeholder
  ``"YOUR_SECRET_PASSPHRASE_HERE_DO_NOT_USE_THIS_LITERAL_VALUE"`` and the
  inline comment now explicitly warns that using this literal string in a live
  environment breaks cryptographic node identity uniqueness bounds.

- **Legacy Migration Path — Descriptor-Level `os.fchmod` Symmetry** (`crypto.py`
  — `load_or_generate_keypair`): Upgraded the legacy migration block to match
  the greenfield path's descriptor-level permission model.  The previous
  `os.chmod(tmp_path, 0o600)` call resolved the temp file through the filesystem
  namespace, leaving a TOCTOU race window.  On POSIX hosts the call is now
  `os.fchmod(tmp.fileno(), 0o600)`, operating directly on the open file
  descriptor.  A `hasattr(os, "fchmod")` guard retains `os.chmod` as a portable
  fallback on Windows.  Both key write paths now enforce permissions through an
  identical descriptor-first pattern.

- **Stale `RuntimeError` Docstring Entry Pruned** (`crypto.py` —
  `load_or_generate_keypair`): Removed the `Raises: RuntimeError` entry that
  documented a zero-byte `os.write` return as a disk-full signal.  This guard
  belonged to the manual raw write loop, which was fully replaced by
  `tmp_file.write(pem_bytes)` in v0.5.7.  The `Raises:` block now reflects only
  conditions that can actually be triggered by the current implementation.

- **Prose Tax — Duplicate Heading Deduplication** (`gateway.py` —
  `_FILLER_PATTERNS`): Corrected the duplicate-heading deduplication regex to
  properly preserve a single instance of consecutive matching markdown headers.
  The previous `pattern.sub("", ...)` call erased both the original and the
  duplicate heading entirely.  `_FILLER_PATTERNS` is now a list of
  ``(pattern, replacement)`` pairs; the heading entry uses ``r"\1"`` as its
  replacement so the captured first heading is restored after the match is
  consumed, leaving exactly one clean copy in the output.

- **Prose Tax — Filler Word Boundary Hardening** (`gateway.py` —
  `_FILLER_PATTERNS`): Hardened conversational filler word-stripping boundaries
  to prevent collateral text corruption or mangling of technical prose.  The
  previous ``\bword\b`` anchors were insufficient: ``\bsimply\b`` matched the
  ``simply`` prefix in ``simply-typed``, and ``\bjust\b`` matched inside
  ``just-in-time``, because ``\b`` only transitions on alphanumeric/non-alphanumeric
  boundaries and the hyphen is non-alphanumeric.  Each hedging adverb pattern now
  appends ``(?![-\w])`` — a negative lookahead that prevents a match when the
  token is immediately followed by a hyphen or word character — so only
  standalone colloquial uses are stripped.

## [0.5.8] - 2026-05-21

### Fixed

- **Greenfield Key Generation — Descriptor-Level `os.fchmod` and Buffered
  Stream Write** (`crypto.py` — `load_or_generate_keypair`): Eliminated
  path-based TOCTOU race windows and resolved the I/O layer buffering mismatch
  introduced in v0.5.6.  `os.chmod(tmp_path, 0o600)` was replaced with
  `os.fchmod(tmp_file.fileno(), 0o600)` on POSIX hosts (portable fallback via
  `hasattr(os, "fchmod")`) so permission enforcement operates on the open
  descriptor rather than the filesystem path.  The manual `while remaining_bytes`
  / `os.write` loop was replaced by `tmp_file.write(pem_bytes)`, aligning the
  write path with the high-level `NamedTemporaryFile` stream object and
  delegating short-write recovery to Python's buffered I/O layer.  The
  `tmp_file.flush()` → `os.fsync` → `os.replace` durability pipeline is
  retained unchanged.

## [0.5.7] - 2026-05-21

### Fixed

- **Greenfield Key Generation — Descriptor-Level `os.fchmod` Permission
  Tracking** (`crypto.py` — `load_or_generate_keypair`): Eliminated path-based
  TOCTOU race windows during greenfield key generation by upgrading file
  permission tracking to descriptor-level `os.fchmod` actions.  The previous
  `os.chmod(tmp_path, 0o600)` call resolved the temp file through the filesystem
  namespace, leaving a window between path resolution and permission application
  where a symlink substitution or concurrent rename could redirect the chmod to
  an unintended target.  On POSIX hosts the block now calls
  `os.fchmod(tmp_file.fileno(), 0o600)`, operating directly on the open file
  descriptor and bypassing the filesystem namespace entirely.  On Windows, where
  `os.fchmod` is unavailable, `os.chmod(tmp_path, 0o600)` is retained as a
  portable fallback via `hasattr(os, "fchmod")`.

- **Greenfield Key Generation — Native Buffered Stream Write** (`crypto.py` —
  `load_or_generate_keypair`): Streamlined filesystem I/O operations by
  substituting low-level raw `os.write` tracking with native
  `NamedTemporaryFile` buffered streaming blocks.  The previous manual
  `while remaining_bytes` loop called `os.write` directly on the file
  descriptor, mixing raw syscall-level I/O with a high-level `NamedTemporaryFile`
  object and creating a layered buffering mismatch where Python's internal stream
  buffer and the raw write path could diverge.  The block now calls
  `tmp_file.write(pem_bytes)` directly on the `NamedTemporaryFile` instance;
  Python's buffered I/O layer handles short-write recovery internally, and the
  subsequent `tmp_file.flush()` drains the stream buffer to the kernel before
  `os.fsync` commits it to physical storage.

## [0.5.6] - 2026-05-21

### Fixed

- **Greenfield Key Path — Atomic `NamedTemporaryFile` Creation Pattern**
  (`crypto.py` — `load_or_generate_keypair`): Eliminated process-wide umask
  leaks by migrating the greenfield key path to an atomic `NamedTemporaryFile`
  creation pattern.  The previous `os.umask(0)` / `os.open` sequence mutated
  the process-wide umask between the capture and restore calls, creating a race
  window where concurrent threads could create files with world-readable
  permissions.  The block now uses `tempfile.NamedTemporaryFile(dir=…,
  delete=False)`, which the Python runtime creates with `0o600` permissions
  internally without touching the process umask.  The fully written temp file
  is promoted over the final path via `os.replace()`, and an `except` block
  removes the temp file on any failure so no partial material is left on disk.

- **Greenfield Key Path — Strict Byte-Tracking `os.write` Loop**
  (`crypto.py` — `load_or_generate_keypair`): Protected key persistence against
  filesystem resource constraints by implementing a strict byte-tracking write
  loop over raw `os.write` boundaries.  A single `os.write` call is not
  guaranteed to consume the full buffer under resource pressure; a short write
  silently truncates the key file with no exception raised.  The block now
  iterates over the remaining bytes, re-submitting the unwritten tail on each
  pass, and raises `RuntimeError` explicitly on a zero-byte return (disk full
  or broken pipe) before that condition can produce a partial or empty identity
  file on disk.

- **Greenfield Key Generation — Explicit `os.chmod` Permission Symmetry**
  (`crypto.py` — `load_or_generate_keypair`): Hardened greenfield key generation
  by injecting an explicit, umask-independent `os.chmod(..., 0o600)` guard onto
  the atomic temp-file path, achieving perfect permission symmetry with the
  legacy migration architecture.  Although `tempfile.NamedTemporaryFile` targets
  `0o600` by default, this behaviour is not guaranteed across all host
  configurations and Python runtime versions.  The explicit `os.chmod` call
  is placed immediately after `tmp_path` is captured and before any bytes are
  written, inside the existing `try/except` wrapper so a permission failure
  triggers the same temp-file cleanup and re-raise path as any other pre-replace
  error.  Both the migration and greenfield paths now enforce file security
  through an unconditional `os.chmod` regardless of the inherited process
  environment.

## [0.5.4] - 2026-05-21

### Fixed

- **Greenfield Key Generation — Process Umask Isolation** (`crypto.py` —
  `load_or_generate_keypair`): Enforced process umask isolation around
  `os.open` boundaries during greenfield key generation to guarantee
  umask-independent `0o600` file permissions.  The `os.open` mode argument is
  ANDed with the bitwise complement of the current process umask at the kernel
  level, meaning a non-zero umask could silently strip permission bits from the
  newly created inode.  The block now captures the active umask with
  `old_umask = os.umask(0)` immediately before the `os.open` call and restores
  it with `os.umask(old_umask)` immediately after the file descriptor is
  obtained, so the `0o600` mode is applied unconditionally regardless of the
  inherited process environment.

- **Greenfield Key Generation — Pre-Close `os.fsync` Durability** (`crypto.py`
  — `load_or_generate_keypair`): Hardened greenfield key initialization
  durability by executing an explicit `os.fsync` on the newly generated file
  descriptor before closure.  Without this call, the kernel's write-back cache
  may not commit private key bytes to physical storage before the file descriptor
  is closed, leaving an empty or partial key file on disk after a power fault.
  `os.fsync(fd)` is now called after `os.write` and before `os.close(fd)` inside
  a `try/finally` block that guarantees the descriptor is always released, making
  the greenfield path's durability guarantee equivalent to the atomic migration
  path.

## [0.5.3] - 2026-05-21

### Fixed

- **Filesystem Durability and Creation-Time Permission Hardening** (`crypto.py`
  — `load_or_generate_keypair`): Hardened filesystem durability by injecting
  pre-rename `os.fsync` controls on atomic migration tasks and establishing
  strict creation-time permission bounds on greenfield key generation.  In the
  atomic migration path, `os.fsync(tmp.fileno())` is now called after
  `tmp.flush()` and before `os.replace()`, guaranteeing that encrypted PEM
  bytes are physically committed to storage before the rename promotes the temp
  file over the original key.  In the new-keypair generation path, the private
  key file is now opened via `os.open(path, O_WRONLY | O_CREAT | O_TRUNC,
  0o600)` wrapped with `os.fdopen`, so the inode is created with `0o600`
  permissions as its first filesystem operation.  The previous pattern of
  `open(..., "wb")` followed by `os.chmod` left a window where the file existed
  with inheritable process umask permissions before the restriction was applied.

## [0.5.2] - 2026-05-21

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