from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate an offline LedgerLine catalog key")
    parser.add_argument("--private-key", type=Path, required=True)
    parser.add_argument("--trust-store", type=Path, required=True)
    parser.add_argument("--key-id", default="ledgerline-release-2026-01")
    args = parser.parse_args()
    if args.private_key.exists() or args.trust_store.exists():
        raise SystemExit("refusing to overwrite an existing key or trust store")
    key = Ed25519PrivateKey.generate()
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_raw = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    args.private_key.parent.mkdir(parents=True, exist_ok=True)
    args.private_key.write_bytes(private_pem)
    args.trust_store.parent.mkdir(parents=True, exist_ok=True)
    args.trust_store.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "keys": [
                    {
                        "id": args.key_id,
                        "algorithm": "ed25519",
                        "public_key_base64": base64.b64encode(public_raw).decode("ascii"),
                        "status": "active",
                    }
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"private key: {args.private_key}")
    print(f"trust store: {args.trust_store}")


if __name__ == "__main__":
    main()
