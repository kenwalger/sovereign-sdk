import os
from pathlib import Path

import pytest
import yaml

_MINIMAL_POLICY: dict = {
    "version": "1.0",
    "global": {
        "max_token_ceiling": 40000,
        "prose_tax_warning_threshold": 0.0,
    },
    "rules": [
        {
            "name": "block_private_keys",
            "scope": "raw",
            "pattern": "-----BEGIN PRIVATE KEY-----",
            "action": "deny",
        },
        {
            "name": "guard_internal_namespaces",
            "scope": "fields",
            "fields": ["messages.content", "input", "prompt", "tools.description"],
            "pattern": r"internal\.sovereign\.local",
            "action": "warn",
        },
        {
            "name": "excessive_context",
            "scope": "telemetry",
            "metric": "raw_tokens",
            "threshold": 30000,
            "action": "warn",
        },
    ],
}


@pytest.fixture
def policy_path(tmp_path: Path) -> Path:
    """Write the standard test policy YAML and return its path."""
    path = tmp_path / "policy.yaml"
    path.write_text(yaml.dump(_MINIMAL_POLICY), encoding="utf-8")
    return path


@pytest.fixture
def allow_all_policy_path(tmp_path: Path) -> Path:
    """Write a permissive policy with no rules."""
    config = {"version": "1.0", "global": {}, "rules": []}
    path = tmp_path / "allow_all.yaml"
    path.write_text(yaml.dump(config), encoding="utf-8")
    return path


@pytest.fixture
def sovereign_secret(monkeypatch) -> str:
    """Inject a deterministic test passphrase for SovereignKeyManager."""
    secret = "airlock-test-node-secret-v1"
    monkeypatch.setenv("SOVEREIGN_NODE_SECRET", secret)
    return secret
