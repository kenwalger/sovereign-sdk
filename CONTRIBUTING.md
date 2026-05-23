# Contributing to Sovereign Systems SDK

Thank you for your interest in contributing. This document explains the project's
non-negotiable invariants, the pull request process, and how to run the test suite
locally before submitting.

---

## Project Invariants

Every contribution must preserve these four properties. A pull request that
violates any one of them will not be merged regardless of its other merits.

1. **Local-first** — No network call, external service, or cloud API may be
   invoked during sieve, sign, or verify operations. All cryptographic work runs
   on local silicon with zero external dependencies at runtime.

2. **Tamper-evidence** — Any post-issuance mutation of a `ForensicReceipt` field
   (including nested `metadata` values) must cause `SovereignKeyManager.verify_receipt`
   to return `False`. Tests that assert tamper-detection must never be weakened.

3. **Zero regression** — Every phase ships with a complete, passing test suite.
   No existing test may be deleted, weakened, or marked `xfail` to make new code
   land. The `uv run pytest` command must exit 0 with zero warnings.

4. **Dependency minimalism** — `sovereign-core` must never take on a high-compute
   dependency (PyTorch, transformers, sentence-transformers, etc.). Framework
   adapters (`sovereign-fastapi`, etc.) carry their own optional dependency trees.
   Propose new dependencies in the PR description with explicit justification.

---

## Running Tests Locally

This workspace uses [`uv`](https://github.com/astral-sh/uv) for dependency management.

```bash
# 1. Bootstrap the workspace (one-time setup)
uv sync

# 2. Run the full test suite across all workspace packages
uv run pytest

# 3. Run tests for a single package
uv run pytest packages/sovereign-core/tests/
uv run pytest packages/sovereign-fastapi/tests/
```

A passing run exits 0 with the summary `N passed, 1 skipped` (the skipped case is
the `os.fchmod` permission test on Windows — expected).

---

## Pull Request Guidelines

- **Scope**: One logical change per PR. Refactors, bug fixes, and features belong
  in separate PRs.
- **Tests**: New functionality requires new tests. The test count must increase or
  stay the same — it must never decrease.
- **CHANGELOG**: Add an entry under `[Unreleased]` in `CHANGELOG.md` following the
  [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format.
- **Docstrings**: Public methods and classes require Google-style docstrings with
  `Args:`, `Returns:`, and `Raises:` sections.
- **Type annotations**: All function signatures must carry complete PEP 484 type
  annotations. `Any` is acceptable only where genuinely necessary.
- **No attribution lines**: Do not add `Co-Authored-By` or similar lines to commit
  messages.

---

## Code Style

- Python 3.12+, enforced via `requires-python = ">=3.12"` in every `pyproject.toml`.
- PEP 8 import grouping: stdlib → third-party → local, each group separated by a
  blank line.
- Comments explain *why*, not *what*. Avoid restating what the code already says.
- No inline `# type: ignore` except where Starlette's internal private-attribute
  access requires it (`request._body`).

---

## Reporting Bugs

Open a GitHub Issue with a minimal reproducible example. For security-sensitive
issues, follow the process described in [SECURITY.md](SECURITY.md) instead of
opening a public issue.
