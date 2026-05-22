"""sovereign-verify — stateless ForensicReceipt verification CLI.

Accepts a receipt JSON file and a base64-encoded Ed25519 public key.
Exits 0 on verified, 1 on tampered or invalid.

Usage:
    sovereign-verify --receipt receipt.json --public-key <base64-key>
"""
import argparse
import base64
import json
import sys
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import ed25519


def _verify(receipt: dict, expected_public_key: str) -> bool:
    """Return True if the receipt passes key-pin and Ed25519 signature checks.

    Performs two sequential verification steps matching the logic of
    SovereignKeyManager.verify_receipt:

    1. Key-pin assertion — receipt["public_key"] must equal expected_public_key.
    2. Signature verification — the Ed25519 signature must be valid over the
       canonical manifest {"metadata": …, "payload_hash": …, "timestamp": …}.

    Note: payload-hash revalidation (step 2 of verify_receipt) requires the
    original payload and is not performed here; the CLI operates on the receipt
    alone for stateless out-of-band auditing.

    Args:
        receipt: Parsed ForensicReceipt dictionary.
        expected_public_key: Base64-encoded raw Ed25519 public key string.

    Returns:
        True if the receipt is structurally intact and key-pinned to the
        provided public key, False otherwise.
    """
    try:
        if receipt.get("public_key") != expected_public_key:
            return False

        pub_bytes = base64.b64decode(receipt["public_key"])
        sig_bytes = base64.b64decode(receipt["signature"])
        pub_key = ed25519.Ed25519PublicKey.from_public_bytes(pub_bytes)

        manifest = {
            "metadata": receipt["metadata"],
            "payload_hash": receipt["payload_hash"],
            "timestamp": receipt["timestamp"],
        }
        canonical = json.dumps(manifest, sort_keys=True, default=str)
        pub_key.verify(sig_bytes, canonical.encode("utf-8"))
        return True
    except (InvalidSignature, KeyError, ValueError, Exception):
        return False


def main() -> None:
    """CLI entry point for sovereign-verify."""
    parser = argparse.ArgumentParser(
        prog="sovereign-verify",
        description=(
            "Verify a Sovereign Systems ForensicReceipt against an Ed25519 public key. "
            "Exits 0 on verified, 1 on tampered or invalid input."
        ),
    )
    parser.add_argument(
        "--receipt",
        required=True,
        metavar="FILE",
        help="Path to a JSON file containing the ForensicReceipt to verify.",
    )
    parser.add_argument(
        "--public-key",
        required=True,
        metavar="BASE64_KEY",
        help="Base64-encoded raw Ed25519 public key string to pin the receipt against.",
    )
    args = parser.parse_args()

    receipt_path = Path(args.receipt)
    if not receipt_path.is_file():
        print(f"Error: receipt file not found: {receipt_path}", file=sys.stderr)
        sys.exit(1)

    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"Error: receipt file is not valid JSON: {exc}", file=sys.stderr)
        sys.exit(1)

    if _verify(receipt, args.public_key):
        print(f"Verified  ✓  payload_hash: {receipt.get('payload_hash', 'unknown')}")
        sys.exit(0)
    else:
        print(
            "Tampered  ✗  Receipt failed cryptographic verification.",
            file=sys.stderr,
        )
        print(f"  payload_hash : {receipt.get('payload_hash', 'unknown')}", file=sys.stderr)
        print(f"  timestamp    : {receipt.get('timestamp', 'unknown')}", file=sys.stderr)
        sys.exit(1)
