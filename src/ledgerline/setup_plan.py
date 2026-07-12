from __future__ import annotations

import hashlib
import json
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ledgerline import __version__
from ledgerline.catalog import load_verified_catalog, resolve_artifact_location
from ledgerline.environment import ledgerline_home

PLAN_SCHEMA_VERSION = "2"
PLAN_TTL = timedelta(minutes=30)


def default_catalog_path() -> Path:
    return Path(__file__).parent / "data" / "packs" / "catalog.json"


def create_setup_plan(
    pack_ids: list[str],
    catalog_path: Path | None = None,
    *,
    now: datetime | None = None,
) -> dict:
    if not pack_ids:
        raise ValueError("at least one pack id is required")
    if len(set(pack_ids)) != len(pack_ids):
        raise ValueError("duplicate pack ids are not allowed")
    current = (now or datetime.now(UTC)).astimezone(UTC)
    catalog = load_verified_catalog(catalog_path or default_catalog_path(), now=current)
    available = {item["id"]: item for item in catalog.document["packs"]}
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
            continue
        for artifact in pack["artifacts"]:
            location = resolve_artifact_location(catalog, artifact["url"])
            steps.append(
                {
                    "id": f"install-{pack['id']}-{artifact['id']}",
                    "pack": pack["id"],
                    "version": pack["version"],
                    "action": "install_llpack",
                    "source": str(location),
                    "size_bytes": artifact["size_bytes"],
                    "sha256": artifact["sha256"],
                    "manifest_sha256": artifact["manifest_sha256"],
                    "unpacked_size_limit": artifact["unpacked_size_limit"],
                    "entry_limit": artifact["entry_limit"],
                    "license": pack["license"],
                    "attribution": pack["attribution"],
                    "target": str(ledgerline_home() / "packs" / pack["id"] / pack["version"]),
                    "requires_consent": True,
                }
            )
    plan_id = secrets.token_hex(16)
    consent_token = secrets.token_urlsafe(32)
    core = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "installer_version": __version__,
        "plan_id": plan_id,
        "created_at": current.isoformat().replace("+00:00", "Z"),
        "expires_at": (current + PLAN_TTL).isoformat().replace("+00:00", "Z"),
        "status": "blocked" if blocked else "ready",
        "catalog": {
            "path": str(catalog.path),
            "signature_path": str(catalog.signature_path),
            "sha256": catalog.sha256,
            "key_id": catalog.key_id,
            "version": catalog.document["catalog_version"],
        },
        "packs": pack_ids,
        "total_download_bytes": sum(step["size_bytes"] for step in steps),
        "total_unpacked_limit": sum(step["unpacked_size_limit"] for step in steps),
        "system_changes": [],
        "steps": steps,
        "blocked": blocked,
        "consent_token": consent_token,
    }
    return {**core, "plan_digest": _plan_digest(core)}


def persist_setup_plan(plan: dict, output: Path | None = None) -> Path:
    path = output or ledgerline_home() / "plans" / f"{plan['plan_id']}.json"
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(plan, ensure_ascii=False, indent=2) + "\n"
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(payload)
    return path


def load_setup_plan(path: Path) -> dict:
    raw = path.expanduser().resolve(strict=True).read_text(encoding="utf-8")
    plan = json.loads(
        raw,
        object_pairs_hook=_pairs_without_duplicates,
        parse_constant=lambda value: _reject_json_constant(value),
    )
    if not isinstance(plan, dict):
        raise ValueError("setup plan must be a JSON object")
    expected = {
        "schema_version",
        "installer_version",
        "plan_id",
        "created_at",
        "expires_at",
        "status",
        "catalog",
        "packs",
        "total_download_bytes",
        "total_unpacked_limit",
        "system_changes",
        "steps",
        "blocked",
        "consent_token",
        "plan_digest",
    }
    if set(plan) != expected:
        raise ValueError("setup plan fields are invalid")
    if plan["schema_version"] != PLAN_SCHEMA_VERSION:
        raise ValueError("unsupported setup plan schema")
    if plan["installer_version"] != __version__:
        raise ValueError(
            f"setup plan was created by LedgerLine {plan['installer_version']}; "
            f"current version is {__version__}"
        )
    digest = plan.pop("plan_digest")
    actual = _plan_digest(plan)
    plan["plan_digest"] = digest
    if not secrets.compare_digest(str(digest), actual):
        raise ValueError("setup plan digest mismatch")
    return plan


def _plan_digest(core: dict) -> str:
    return hashlib.sha256(
        json.dumps(core, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _pairs_without_duplicates(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key in setup plan: {key!r}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number in setup plan: {value}")
