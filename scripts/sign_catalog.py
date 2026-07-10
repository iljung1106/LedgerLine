from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path

from cryptography.hazmat.primitives import serialization


def main() -> None:
    parser = argparse.ArgumentParser(description="Sign exact LedgerLine catalog bytes")
    parser.add_argument("catalog", type=Path)
    parser.add_argument("--private-key", type=Path, required=True)
    parser.add_argument("--key-id", default="ledgerline-release-2026-01")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    raw = args.catalog.read_bytes()
    key = serialization.load_pem_private_key(args.private_key.read_bytes(), password=None)
    signature = key.sign(raw)
    output = args.output or args.catalog.with_name(args.catalog.name + ".sig")
    output.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "algorithm": "ed25519",
                "key_id": args.key_id,
                "signed_sha256": hashlib.sha256(raw).hexdigest(),
                "signature": base64.b64encode(signature).decode("ascii"),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(output)


if __name__ == "__main__":
    main()
