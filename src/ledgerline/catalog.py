from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

HEX_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_-]*$")


class CatalogError(ValueError):
    """Raised when catalog authenticity or structure cannot be established."""


@dataclass(frozen=True, slots=True)
class VerifiedCatalog:
    path: Path
    signature_path: Path
    document: dict[str, Any]
    key_id: str
    sha256: str


def default_trust_store_path() -> Path:
    return Path(__file__).parent / "data" / "trust" / "catalog_keys.json"


def signature_path_for(catalog_path: Path) -> Path:
    return catalog_path.with_name(catalog_path.name + ".sig")


def load_verified_catalog(
    catalog_path: Path,
    *,
    signature_path: Path | None = None,
    trust_store_path: Path | None = None,
    now: datetime | None = None,
) -> VerifiedCatalog:
    path = catalog_path.expanduser().resolve(strict=True)
    signature_path = (signature_path or signature_path_for(path)).expanduser().resolve(strict=True)
    raw = path.read_bytes()
    if len(raw) > 4 * 1024 * 1024:
        raise CatalogError("catalog exceeds the 4 MiB safety limit")
    document = _load_strict_json(raw, "catalog")
    signature = _load_strict_json(signature_path.read_bytes(), "catalog signature")
    _require_exact_keys(
        signature,
        {"schema_version", "algorithm", "key_id", "signed_sha256", "signature"},
        "catalog signature",
    )
    if signature["schema_version"] != "1" or signature["algorithm"] != "ed25519":
        raise CatalogError("unsupported catalog signature format")
    digest = hashlib.sha256(raw).hexdigest()
    if signature["signed_sha256"] != digest:
        raise CatalogError("catalog bytes do not match the signed SHA-256")

    trust = _load_strict_json(
        (trust_store_path or default_trust_store_path()).read_bytes(), "catalog trust store"
    )
    _require_exact_keys(trust, {"schema_version", "keys"}, "catalog trust store")
    if trust["schema_version"] != "1" or not isinstance(trust["keys"], list):
        raise CatalogError("unsupported catalog trust store")
    keys = {item.get("id"): item for item in trust["keys"] if isinstance(item, dict)}
    key_id = signature["key_id"]
    key = keys.get(key_id)
    if not key or set(key) != {"id", "algorithm", "public_key_base64", "status"}:
        raise CatalogError(f"catalog signing key is not trusted: {key_id!r}")
    if key["algorithm"] != "ed25519" or key["status"] != "active":
        raise CatalogError(f"catalog signing key is not active: {key_id!r}")
    try:
        public_key = Ed25519PublicKey.from_public_bytes(
            base64.b64decode(key["public_key_base64"], validate=True)
        )
        public_key.verify(base64.b64decode(signature["signature"], validate=True), raw)
    except (InvalidSignature, ValueError) as exc:
        raise CatalogError("catalog Ed25519 signature verification failed") from exc

    _validate_catalog(document, now=now or datetime.now(UTC))
    return VerifiedCatalog(path, signature_path, document, key_id, digest)


def resolve_artifact_location(catalog: VerifiedCatalog, value: str) -> str | Path:
    parsed = urlparse(value)
    if parsed.scheme in {"https", "http"}:
        if parsed.scheme != "https":
            raise CatalogError("remote pack artifacts must use HTTPS")
        return value
    if parsed.scheme == "file":
        raise CatalogError("file: URLs are not accepted; use a relative catalog path")
    if parsed.scheme or parsed.netloc:
        raise CatalogError(f"unsupported artifact URL: {value!r}")
    candidate = (catalog.path.parent / value).resolve()
    return candidate


def _validate_catalog(document: dict[str, Any], *, now: datetime) -> None:
    _require_exact_keys(
        document,
        {"schema_version", "catalog_version", "generated_at", "expires_at", "packs"},
        "catalog",
    )
    if document["schema_version"] != "1":
        raise CatalogError("unsupported catalog schema_version")
    if not isinstance(document["catalog_version"], int) or document["catalog_version"] < 1:
        raise CatalogError("catalog_version must be a positive integer")
    generated = _parse_timestamp(document["generated_at"], "generated_at")
    expires = _parse_timestamp(document["expires_at"], "expires_at")
    if generated >= expires:
        raise CatalogError("catalog expiry must be after generation")
    if now >= expires:
        raise CatalogError(f"catalog expired at {expires.isoformat()}")
    packs = document["packs"]
    if not isinstance(packs, list) or not packs:
        raise CatalogError("catalog packs must be a non-empty list")
    ids: set[str] = set()
    for pack in packs:
        _validate_pack(pack)
        if pack["id"] in ids:
            raise CatalogError(f"duplicate pack id: {pack['id']}")
        ids.add(pack["id"])


def _validate_pack(pack: Any) -> None:
    if not isinstance(pack, dict):
        raise CatalogError("each catalog pack must be an object")
    allowed = {
        "id",
        "version",
        "display_name",
        "status",
        "license",
        "attribution",
        "source_page",
        "artifacts",
        "blocked_reasons",
    }
    if set(pack) - allowed:
        raise CatalogError(f"unknown catalog pack fields: {sorted(set(pack) - allowed)}")
    required = allowed - {"blocked_reasons"}
    if not required <= set(pack):
        raise CatalogError(f"missing catalog pack fields: {sorted(required - set(pack))}")
    if not isinstance(pack["id"], str) or not IDENTIFIER_RE.fullmatch(pack["id"]):
        raise CatalogError("invalid pack id")
    for field in ("version", "display_name", "license", "attribution", "source_page"):
        if not isinstance(pack[field], str) or not pack[field].strip():
            raise CatalogError(f"pack {pack['id']} has invalid {field}")
    if pack["status"] not in {"installable", "blocked"}:
        raise CatalogError(f"pack {pack['id']} has invalid status")
    artifacts = pack["artifacts"]
    if not isinstance(artifacts, list):
        raise CatalogError(f"pack {pack['id']} artifacts must be a list")
    if pack["status"] == "installable" and not artifacts:
        raise CatalogError(f"installable pack {pack['id']} has no artifacts")
    if pack["status"] == "installable" and len(artifacts) != 1:
        raise CatalogError(f"v1 installable pack {pack['id']} must select exactly one artifact")
    if pack["status"] == "blocked" and artifacts:
        raise CatalogError(f"blocked pack {pack['id']} must not expose artifacts")
    if pack["status"] == "blocked" and not pack.get("blocked_reasons"):
        raise CatalogError(f"blocked pack {pack['id']} needs blocked_reasons")
    artifact_ids: set[str] = set()
    for artifact in artifacts:
        _validate_artifact(pack["id"], artifact)
        if artifact["id"] in artifact_ids:
            raise CatalogError(f"duplicate artifact id in pack {pack['id']}")
        artifact_ids.add(artifact["id"])


def _validate_artifact(pack_id: str, artifact: Any) -> None:
    required = {
        "id",
        "url",
        "size_bytes",
        "sha256",
        "media_type",
        "unpacked_size_limit",
        "entry_limit",
        "manifest_sha256",
    }
    if not isinstance(artifact, dict) or set(artifact) != required:
        raise CatalogError(f"pack {pack_id} artifact fields are invalid")
    if not isinstance(artifact["id"], str) or not IDENTIFIER_RE.fullmatch(artifact["id"]):
        raise CatalogError(f"pack {pack_id} has invalid artifact id")
    if not isinstance(artifact["url"], str) or not artifact["url"]:
        raise CatalogError(f"pack {pack_id} has invalid artifact URL")
    if artifact["media_type"] != "application/vnd.ledgerline.llpack+zip":
        raise CatalogError(f"pack {pack_id} artifact has unsupported media_type")
    for field, maximum in (
        ("size_bytes", 8 * 1024**3),
        ("unpacked_size_limit", 16 * 1024**3),
        ("entry_limit", 100_000),
    ):
        value = artifact[field]
        if not isinstance(value, int) or not 0 < value <= maximum:
            raise CatalogError(f"pack {pack_id} artifact has invalid {field}")
    for field in ("sha256", "manifest_sha256"):
        if not isinstance(artifact[field], str) or not HEX_SHA256_RE.fullmatch(artifact[field]):
            raise CatalogError(f"pack {pack_id} artifact has invalid {field}")


def _load_strict_json(raw: bytes, label: str) -> Any:
    try:
        text = raw.decode("utf-8")
        return json.loads(
            text,
            object_pairs_hook=lambda pairs: _pairs_without_duplicates(pairs, label),
            parse_constant=lambda value: _raise_json_constant(value, label),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CatalogError(f"invalid {label} JSON: {exc}") from exc


def _pairs_without_duplicates(pairs: list[tuple[str, Any]], label: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CatalogError(f"duplicate JSON key in {label}: {key!r}")
        result[key] = value
    return result


def _raise_json_constant(value: str, label: str) -> None:
    raise CatalogError(f"non-finite JSON number in {label}: {value}")


def _require_exact_keys(value: Any, expected: set[str], label: str) -> None:
    if not isinstance(value, dict) or set(value) != expected:
        actual = set(value) if isinstance(value, dict) else set()
        raise CatalogError(
            f"{label} fields differ; missing={sorted(expected - actual)}, "
            f"unknown={sorted(actual - expected)}"
        )


def _parse_timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise CatalogError(f"catalog {field} must be an RFC 3339 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CatalogError(f"catalog {field} is invalid") from exc
    if parsed.tzinfo is None:
        raise CatalogError(f"catalog {field} must include a timezone")
    return parsed.astimezone(UTC)
