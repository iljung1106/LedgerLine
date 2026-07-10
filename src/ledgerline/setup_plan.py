from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ledgerline.environment import ledgerline_home


def default_catalog_path() -> Path:
    return Path(__file__).parent / "data" / "packs" / "catalog.json"


def create_setup_plan(pack_ids: list[str], catalog_path: Path | None = None) -> dict:
    path = catalog_path or default_catalog_path()
    catalog = json.loads(path.read_text(encoding="utf-8"))
    available = {item["id"]: item for item in catalog["packs"]}
    unknown = sorted(set(pack_ids) - set(available))
    if unknown:
        raise ValueError(f"unknown packs: {', '.join(unknown)}")
    selected = [available[item] for item in pack_ids]
    steps: list[dict] = []
    blocked: list[dict] = []
    for pack in selected:
        if pack["status"] != "installable":
            blocked.append(
                {
                    "pack": pack["id"],
                    "reasons": list(pack.get("blocked_reasons", [])),
                }
            )
        for artifact in pack.get("artifacts", []):
            steps.append(
                {
                    "id": f"download-{pack['id']}-{artifact['id']}",
                    "pack": pack["id"],
                    "action": "download",
                    "url": artifact["url"],
                    "size_bytes": artifact["size_bytes"],
                    "sha256": artifact["sha256"],
                    "license": artifact["license"],
                    "target": str(ledgerline_home() / "packs" / pack["id"]),
                    "requires_consent": True,
                }
            )
    core = {
        "schema_version": "1",
        "status": "blocked" if blocked else "ready",
        "packs": [item["id"] for item in selected],
        "total_download_bytes": sum(item["approx_download_bytes"] for item in selected),
        "system_changes": [],
        "steps": steps,
        "blocked": blocked,
    }
    token = hashlib.sha256(
        json.dumps(core, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {**core, "consent_token": token}
