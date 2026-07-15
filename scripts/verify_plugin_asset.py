"""Verify that the Codex plugin embeds the exact wheel built from this checkout."""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path


def _single_wheel(value: Path, label: str) -> Path:
    candidate = value.resolve()
    if candidate.is_file() and candidate.suffix == ".whl":
        return candidate
    if candidate.is_dir():
        wheels = sorted(candidate.glob("ledgerline-*.whl"))
        if len(wheels) == 1:
            return wheels[0]
        raise ValueError(f"expected exactly one {label} wheel in {candidate}, found {len(wheels)}")
    raise ValueError(f"{label} wheel path does not exist: {candidate}")


def _entries(path: Path) -> dict[str, str]:
    with zipfile.ZipFile(path) as archive:
        return {
            name: hashlib.sha256(archive.read(name)).hexdigest()
            for name in sorted(archive.namelist())
            if not name.endswith("/")
        }


def verify(built: Path, embedded: Path) -> dict[str, object]:
    built_entries = _entries(built)
    embedded_entries = _entries(embedded)
    built_names = set(built_entries)
    embedded_names = set(embedded_entries)
    missing = sorted(built_names - embedded_names)
    extra = sorted(embedded_names - built_names)
    changed = sorted(
        name
        for name in built_names & embedded_names
        if built_entries[name] != embedded_entries[name]
    )
    if missing or extra or changed:
        raise ValueError(
            "plugin wheel differs from the wheel built from this checkout: "
            f"missing={missing[:20]}, extra={extra[:20]}, changed={changed[:20]}"
        )
    return {
        "schema_version": "1",
        "status": "ok",
        "built_wheel": str(built),
        "plugin_wheel": str(embedded),
        "entries": len(built_entries),
        "content_manifest_sha256": hashlib.sha256(
            json.dumps(built_entries, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("built", type=Path, help="Built wheel or directory containing one wheel")
    parser.add_argument(
        "plugin_asset",
        type=Path,
        help="Embedded plugin wheel or directory containing one wheel",
    )
    args = parser.parse_args()
    report = verify(
        _single_wheel(args.built, "built"),
        _single_wheel(args.plugin_asset, "plugin asset"),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
