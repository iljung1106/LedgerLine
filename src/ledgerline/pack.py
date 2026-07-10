from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import unicodedata
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from ledgerline.catalog import HEX_SHA256_RE

MANIFEST_NAME = "manifest.json"
WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}
PACK_ID_RE = re.compile(r"^[a-z][a-z0-9_-]*$")


class PackError(ValueError):
    """Raised when a .llpack cannot be safely verified or installed."""


def inspect_llpack(
    path: Path,
    *,
    expected_id: str | None = None,
    expected_version: str | None = None,
    expected_manifest_sha256: str | None = None,
    unpacked_size_limit: int,
    entry_limit: int,
) -> dict[str, Any]:
    with zipfile.ZipFile(path, "r") as archive:
        manifest, entries = _preflight(
            archive,
            expected_id=expected_id,
            expected_version=expected_version,
            expected_manifest_sha256=expected_manifest_sha256,
            unpacked_size_limit=unpacked_size_limit,
            entry_limit=entry_limit,
        )
        _stream_verify(archive, manifest, entries, unpacked_size_limit)
    return manifest


def extract_llpack(
    path: Path,
    destination: Path,
    *,
    expected_id: str,
    expected_version: str,
    expected_manifest_sha256: str,
    unpacked_size_limit: int,
    entry_limit: int,
) -> dict[str, Any]:
    if destination.exists():
        raise PackError(f"staging destination already exists: {destination}")
    destination.mkdir(parents=True, exist_ok=False)
    try:
        with zipfile.ZipFile(path, "r") as archive:
            manifest, entries = _preflight(
                archive,
                expected_id=expected_id,
                expected_version=expected_version,
                expected_manifest_sha256=expected_manifest_sha256,
                unpacked_size_limit=unpacked_size_limit,
                entry_limit=entry_limit,
            )
            _stream_extract(archive, manifest, entries, destination, unpacked_size_limit)
        return manifest
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise


def _preflight(
    archive: zipfile.ZipFile,
    *,
    expected_id: str | None,
    expected_version: str | None,
    expected_manifest_sha256: str | None,
    unpacked_size_limit: int,
    entry_limit: int,
) -> tuple[dict[str, Any], dict[str, zipfile.ZipInfo]]:
    infos = archive.infolist()
    if not infos or len(infos) > entry_limit:
        raise PackError("pack entry count is empty or exceeds its signed limit")
    entries: dict[str, zipfile.ZipInfo] = {}
    normalized_names: dict[str, str] = {}
    file_names: set[str] = set()
    declared_total = 0
    for info in infos:
        name = _validate_member_name(info.filename)
        normalized = unicodedata.normalize("NFC", name).casefold()
        previous = normalized_names.get(normalized)
        if previous is not None:
            raise PackError(f"pack contains colliding paths: {previous!r}, {name!r}")
        normalized_names[normalized] = name
        mode = info.external_attr >> 16
        if info.flag_bits & 0x1:
            raise PackError(f"encrypted pack entries are not allowed: {name!r}")
        windows_attributes = info.external_attr & 0xFFFF
        if windows_attributes & 0x400:
            raise PackError(f"pack contains a Windows reparse-point entry: {name!r}")
        if stat.S_ISLNK(mode) or stat.S_ISCHR(mode) or stat.S_ISBLK(mode) or stat.S_ISFIFO(mode):
            raise PackError(f"pack contains a non-regular entry: {name!r}")
        if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
            raise PackError(f"unsupported compression method for {name!r}")
        if info.file_size < 0 or info.compress_size < 0:
            raise PackError(f"negative ZIP size for {name!r}")
        declared_total += info.file_size
        if declared_total > unpacked_size_limit:
            raise PackError("pack exceeds its signed unpacked byte limit")
        if info.is_dir():
            continue
        if name in entries:
            raise PackError(f"duplicate pack entry: {name!r}")
        entries[name] = info
        file_names.add(name)
    for file_name in file_names:
        parts = PurePosixPath(file_name).parts
        prefixes = {"/".join(parts[:index]) for index in range(1, len(parts))}
        if prefixes & file_names:
            raise PackError(f"file/directory prefix collision at {file_name!r}")
    if MANIFEST_NAME not in entries:
        raise PackError("pack does not contain manifest.json")
    manifest_info = entries[MANIFEST_NAME]
    if manifest_info.file_size > 1024 * 1024:
        raise PackError("pack manifest exceeds 1 MiB")
    raw_manifest = archive.read(manifest_info)
    manifest_sha = hashlib.sha256(raw_manifest).hexdigest()
    if expected_manifest_sha256 and manifest_sha != expected_manifest_sha256:
        raise PackError("pack manifest SHA-256 does not match the signed catalog")
    manifest = _parse_manifest(raw_manifest)
    if expected_id and manifest["id"] != expected_id:
        raise PackError("pack id does not match the signed catalog")
    if expected_version and manifest["version"] != expected_version:
        raise PackError("pack version does not match the signed catalog")
    declared_files = {item["path"] for item in manifest["files"]}
    actual_files = set(entries) - {MANIFEST_NAME}
    if declared_files != actual_files:
        raise PackError(
            "pack file allowlist mismatch: "
            f"missing={sorted(declared_files - actual_files)}, "
            f"unexpected={sorted(actual_files - declared_files)}"
        )
    return manifest, entries


def _parse_manifest(raw: bytes) -> dict[str, Any]:
    try:
        manifest = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs_without_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PackError(f"invalid pack manifest JSON: {exc}") from exc
    required = {
        "format",
        "id",
        "version",
        "display_name",
        "license",
        "attribution",
        "source_page",
        "soundfonts",
        "files",
    }
    if not isinstance(manifest, dict) or set(manifest) != required:
        raise PackError("pack manifest fields are invalid")
    if manifest["format"] != 1:
        raise PackError("unsupported pack manifest format")
    if not isinstance(manifest["id"], str) or not PACK_ID_RE.fullmatch(manifest["id"]):
        raise PackError("invalid pack manifest id")
    for field in ("version", "display_name", "license", "attribution", "source_page"):
        if not isinstance(manifest[field], str) or not manifest[field].strip():
            raise PackError(f"invalid pack manifest {field}")
    if not isinstance(manifest["soundfonts"], list) or not manifest["soundfonts"]:
        raise PackError("pack manifest soundfonts must be non-empty")
    files = manifest["files"]
    if not isinstance(files, list) or not files:
        raise PackError("pack manifest files must be non-empty")
    seen: set[str] = set()
    for item in files:
        if not isinstance(item, dict) or set(item) != {"path", "size_bytes", "sha256"}:
            raise PackError("pack manifest file record is invalid")
        path = _validate_member_name(item["path"])
        if path == MANIFEST_NAME or path in seen:
            raise PackError(f"duplicate or reserved manifest path: {path!r}")
        seen.add(path)
        if not isinstance(item["size_bytes"], int) or item["size_bytes"] < 0:
            raise PackError(f"invalid declared size for {path!r}")
        if not isinstance(item["sha256"], str) or not HEX_SHA256_RE.fullmatch(item["sha256"]):
            raise PackError(f"invalid declared SHA-256 for {path!r}")
    for path in manifest["soundfonts"]:
        if not isinstance(path, str) or path not in seen or Path(path).suffix.lower() not in {
            ".sf2",
            ".sf3",
        }:
            raise PackError(f"invalid soundfont entrypoint: {path!r}")
    return manifest


def _stream_verify(
    archive: zipfile.ZipFile,
    manifest: dict[str, Any],
    entries: dict[str, zipfile.ZipInfo],
    unpacked_size_limit: int,
) -> None:
    total = 0
    records = {item["path"]: item for item in manifest["files"]}
    for name, record in records.items():
        digest = hashlib.sha256()
        size = 0
        with archive.open(entries[name], "r") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                size += len(chunk)
                total += len(chunk)
                if size > record["size_bytes"] or total > unpacked_size_limit:
                    raise PackError(f"expanded bytes exceed signed limits at {name!r}")
                digest.update(chunk)
        if size != record["size_bytes"] or digest.hexdigest() != record["sha256"]:
            raise PackError(f"file hash or size mismatch for {name!r}")


def _stream_extract(
    archive: zipfile.ZipFile,
    manifest: dict[str, Any],
    entries: dict[str, zipfile.ZipInfo],
    destination: Path,
    unpacked_size_limit: int,
) -> None:
    total = 0
    records = {item["path"]: item for item in manifest["files"]}
    for name, record in records.items():
        target = destination.joinpath(*PurePosixPath(name).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        size = 0
        with archive.open(entries[name], "r") as source, target.open("xb") as output:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                size += len(chunk)
                total += len(chunk)
                if size > record["size_bytes"] or total > unpacked_size_limit:
                    raise PackError(f"expanded bytes exceed signed limits at {name!r}")
                digest.update(chunk)
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        if size != record["size_bytes"] or digest.hexdigest() != record["sha256"]:
            raise PackError(f"file hash or size mismatch for {name!r}")
    manifest_target = destination / MANIFEST_NAME
    raw = archive.read(entries[MANIFEST_NAME])
    manifest_target.write_bytes(raw)


def _validate_member_name(value: Any) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise PackError("pack contains an invalid empty or NUL path")
    if "\\" in value or value.startswith(("/", "//")) or ":" in value or "//" in value:
        raise PackError(f"unsafe pack path: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise PackError(f"unsafe pack path: {value!r}")
    if len(path.parts) > 32:
        raise PackError(f"pack path is deeper than 32 segments: {value!r}")
    if len(value.encode("utf-16-le")) // 2 > 240:
        raise PackError(f"pack path exceeds 240 UTF-16 code units: {value!r}")
    if any(ord(character) < 32 for character in value):
        raise PackError(f"pack path contains a control character: {value!r}")
    for part in path.parts:
        if part != unicodedata.normalize("NFC", part):
            raise PackError(f"pack path is not Unicode NFC: {value!r}")
        if part.endswith((" ", ".")):
            raise PackError(f"pack path has a trailing dot or space: {value!r}")
        stem = part.split(".", 1)[0].upper()
        if stem in WINDOWS_RESERVED:
            raise PackError(f"pack path uses a Windows reserved name: {value!r}")
    return value


def _pairs_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PackError(f"duplicate JSON key in pack manifest: {key!r}")
        result[key] = value
    return result
