# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 1.0.0   | Yes       |
| < 1.0   | No        |

Only the latest minor release series receives security fixes. Upgrade to the
current version before reporting an issue.

---

## Reporting a Vulnerability

**Do not open a public GitHub Issue for security vulnerabilities.**

If you discover a cryptographic bypass, boundary violation, key-material exposure,
receipt forgery vector, or any other security-sensitive defect, please report it
privately so a fix can be prepared before public disclosure.

### How to Report

Send a plain-text email to:

**ken.alger.778@gmail.com**

Include the following in your report:

1. **Summary** — a one-paragraph description of the vulnerability and its impact.
2. **Affected component** — which module or package (`sovereign-core`, `sovereign-fastapi`, etc.).
3. **Reproduction steps** — a minimal, self-contained code snippet or command sequence
   that demonstrates the issue against a clean `uv sync` environment.
4. **Severity assessment** — your estimate of CVSS score or qualitative severity
   (Critical / High / Medium / Low) and reasoning.
5. **Suggested fix** — optional, but appreciated.

### Response Timeline

| Milestone | Target |
|-----------|--------|
| Acknowledgement | Within 72 hours |
| Triage and severity confirmation | Within 7 days |
| Patch availability | Within 30 days for Critical/High; 90 days for Medium/Low |
| Public disclosure | Coordinated with reporter after patch ships |

If you do not receive an acknowledgement within 72 hours, follow up by opening a
GitHub Issue with the title `[SECURITY] Follow-up required` and no further detail —
this signals that private contact failed without exposing the vulnerability.

---

## Scope

The following are in scope for this policy:

- Ed25519 key generation, loading, and PEM storage (`sovereign_core.crypto`)
- `ForensicReceipt` signature forgery or bypass (`SovereignKeyManager.verify_receipt`)
- Prose Tax sieve producing output that passes receipt verification for tampered payloads
- `SovereignMiddleware` body injection or receipt header spoofing
- `sovereign-verify` CLI accepting a tampered receipt as valid
- Private key material exposure through any code path

The following are out of scope:

- Vulnerabilities in upstream dependencies (report to the upstream maintainer directly)
- Denial-of-service via crafted payloads (the SDK is local-first; external DoS is not a threat model)
- Issues requiring physical access to the host machine

---

## Disclosure Policy

This project follows a **coordinated disclosure** model. We ask reporters to give us
a reasonable window to ship a fix before publishing details. In return, we commit to
crediting reporters by name (or anonymously at their preference) in the release notes
and CHANGELOG.
