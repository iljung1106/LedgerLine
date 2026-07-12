from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import tempfile
import urllib.request
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from ledgerline.catalog import load_verified_catalog
from ledgerline.environment import ledgerline_home
from ledgerline.pack import PackError, extract_llpack, inspect_llpack
from ledgerline.setup_plan import load_setup_plan
from ledgerline.soundfont import read_presets

DOWNLOAD_TIMEOUT_SECONDS = 60


def apply_setup_plan(
    plan_path: Path,
    consent_token: str,
    *,
    catalog_path: Path | None = None,
    now: datetime | None = None,
) -> dict:
    plan = load_setup_plan(plan_path)
    current = (now or datetime.now(UTC)).astimezone(UTC)
    if plan["status"] != "ready" or plan["blocked"]:
        raise ValueError("blocked setup plans cannot be applied")
    if current >= _parse_timestamp(plan["expires_at"]):
        raise ValueError("setup plan has expired; create a new plan")
    if not secrets.compare_digest(str(plan["consent_token"]), consent_token):
        raise ValueError("consent token does not match this setup plan")
    catalog_record = plan["catalog"]
    selected_catalog_path = (catalog_path or Path(catalog_record["path"])).resolve(strict=True)
    catalog = load_verified_catalog(selected_catalog_path, now=current)
    if catalog.sha256 != catalog_record["sha256"]:
        raise ValueError("catalog differs from the catalog bound to the setup plan")
    if catalog.key_id != catalog_record["key_id"]:
        raise ValueError("catalog key differs from the setup plan")
    if catalog.document["catalog_version"] != catalog_record["version"]:
        raise ValueError("catalog version differs from the setup plan")
    _enforce_catalog_monotonicity(catalog.key_id, catalog.document["catalog_version"])
    _verify_steps_against_catalog(plan, catalog.document)

    home = ledgerline_home()
    home.mkdir(parents=True, exist_ok=True)
    _check_free_space(home, plan)
    with _installer_lock(home, plan["plan_id"]):
        _consume_consent(plan["plan_id"], plan["plan_digest"], home)
        cache = home / "cache" / "downloads"
        cache.mkdir(parents=True, exist_ok=True)
        installed: list[dict] = []
        for step in plan["steps"]:
            artifact = cache / f"{step['sha256']}.llpack"
            _obtain_artifact(step["source"], artifact, step["size_bytes"], step["sha256"])
            inspect_llpack(
                artifact,
                expected_id=step["pack"],
                expected_version=step["version"],
                expected_manifest_sha256=step["manifest_sha256"],
                unpacked_size_limit=step["unpacked_size_limit"],
                entry_limit=step["entry_limit"],
            )
            installed.append(_install_artifact(artifact, step, home))
        _record_catalog_version(catalog.key_id, catalog.document["catalog_version"], home)
    return {
        "schema_version": "1",
        "status": "ok",
        "plan_id": plan["plan_id"],
        "catalog_version": catalog.document["catalog_version"],
        "installed": installed,
        "system_changes": [],
    }


def _verify_steps_against_catalog(plan: dict, catalog: dict) -> None:
    packs = {item["id"]: item for item in catalog["packs"]}
    expected: list[tuple] = []
    for pack_id in plan["packs"]:
        pack = packs.get(pack_id)
        if not pack or pack["status"] != "installable":
            raise ValueError(f"planned pack is not installable in the signed catalog: {pack_id}")
        for artifact in pack["artifacts"]:
            expected.append(
                (
                    pack_id,
                    pack["version"],
                    artifact["size_bytes"],
                    artifact["sha256"],
                    artifact["manifest_sha256"],
                    artifact["unpacked_size_limit"],
                    artifact["entry_limit"],
                )
            )
    actual = [
        (
            step.get("pack"),
            step.get("version"),
            step.get("size_bytes"),
            step.get("sha256"),
            step.get("manifest_sha256"),
            step.get("unpacked_size_limit"),
            step.get("entry_limit"),
        )
        for step in plan["steps"]
    ]
    if actual != expected:
        raise ValueError("setup plan steps differ from the signed catalog")
    if plan["total_download_bytes"] != sum(item[2] for item in actual):
        raise ValueError("setup plan download total is inconsistent")
    if plan["total_unpacked_limit"] != sum(item[5] for item in actual):
        raise ValueError("setup plan unpacked total is inconsistent")


def _obtain_artifact(source: str, target: Path, size_bytes: int, expected_sha256: str) -> None:
    if target.is_file():
        if target.stat().st_size == size_bytes and _sha256_file(target) == expected_sha256:
            return
        target.unlink()
    partial = target.with_suffix(".partial")
    partial.unlink(missing_ok=True)
    digest = hashlib.sha256()
    written = 0
    source_path = Path(source)
    parsed = urlparse(source)
    if source_path.is_absolute():
        stream = source_path.resolve(strict=True).open("rb")
    elif parsed.scheme == "https":
        request = urllib.request.Request(source, headers={"User-Agent": "LedgerLine/0.2"})
        response = urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_SECONDS)
        if urlparse(response.geturl()).scheme != "https":
            response.close()
            raise ValueError("artifact redirect downgraded from HTTPS")
        stream = response
    elif not parsed.scheme:
        stream = Path(source).resolve(strict=True).open("rb")
    else:
        raise ValueError(f"unsupported artifact source: {source!r}")
    try:
        with stream, partial.open("xb") as output:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                written += len(chunk)
                if written > size_bytes:
                    raise ValueError("download exceeds the signed artifact size")
                digest.update(chunk)
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        if written != size_bytes or digest.hexdigest() != expected_sha256:
            raise ValueError("download size or SHA-256 does not match the signed catalog")
        os.replace(partial, target)
    finally:
        partial.unlink(missing_ok=True)


def _install_artifact(artifact: Path, step: dict, home: Path) -> dict:
    pack_root = home / "packs" / step["pack"]
    target = pack_root / step["version"]
    active_file = pack_root / "ACTIVE"
    if target.is_dir():
        _verify_installed(target, step)
        _write_active_pointer(active_file, step["version"])
        return {"pack": step["pack"], "version": step["version"], "state": "already_installed"}
    staging_root = pack_root / ".staging"
    staging_root.mkdir(parents=True, exist_ok=True)
    staging = staging_root / secrets.token_hex(16)
    try:
        manifest = extract_llpack(
            artifact,
            staging,
            expected_id=step["pack"],
            expected_version=step["version"],
            expected_manifest_sha256=step["manifest_sha256"],
            unpacked_size_limit=step["unpacked_size_limit"],
            entry_limit=step["entry_limit"],
        )
        _smoke_test_soundfonts(staging, manifest)
        receipt = {
            "schema_version": "1",
            "pack": step["pack"],
            "version": step["version"],
            "artifact_sha256": step["sha256"],
            "manifest_sha256": step["manifest_sha256"],
        }
        (staging / "RECEIPT.json").write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(staging, target)
        _write_active_pointer(active_file, step["version"])
        return {"pack": step["pack"], "version": step["version"], "state": "installed"}
    except Exception:
        quarantine = pack_root / ".quarantine" / staging.name
        quarantine.parent.mkdir(parents=True, exist_ok=True)
        if staging.exists():
            os.replace(staging, quarantine)
        raise


def _verify_installed(target: Path, step: dict) -> None:
    receipt_path = target / "RECEIPT.json"
    if not receipt_path.is_file():
        raise PackError(f"existing pack has no receipt: {target}")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if receipt.get("artifact_sha256") != step["sha256"]:
        raise PackError(f"existing pack receipt does not match the catalog: {target}")


def _smoke_test_soundfonts(root: Path, manifest: dict) -> None:
    for relative in manifest["soundfonts"]:
        path = root.joinpath(*relative.split("/"))
        presets = read_presets(path)
        if not presets:
            raise PackError(f"SoundFont smoke test found no presets: {relative}")
        if manifest["id"] == "starter":
            programs = {preset.program for preset in presets if preset.bank == 0}
            missing = sorted(set(range(128)) - programs)
            if missing:
                raise PackError(f"Starter SoundFont is missing GM bank 0 programs: {missing}")


def _write_active_pointer(path: Path, version: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix="ACTIVE.", dir=path.parent, text=True)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as output:
            output.write(version + "\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp_name, path)
    finally:
        Path(temp_name).unlink(missing_ok=True)


def _consume_consent(plan_id: str, plan_digest: str, home: Path) -> None:
    consumed = home / "state" / "consumed-plans" / f"{plan_id}.json"
    consumed.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"plan_id": plan_id, "plan_digest": plan_digest}) + "\n"
    try:
        with consumed.open("x", encoding="utf-8") as handle:
            handle.write(payload)
    except FileExistsError as exc:
        raise ValueError("setup plan consent has already been consumed") from exc


@contextmanager
def _installer_lock(home: Path, plan_id: str):
    lock_path = home / "state" / "setup.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"pid": os.getpid(), "plan_id": plan_id}) + "\n"
    try:
        with lock_path.open("x", encoding="utf-8") as handle:
            handle.write(payload)
    except FileExistsError as exc:
        raise ValueError(f"another setup operation owns {lock_path}") from exc
    try:
        yield
    finally:
        lock_path.unlink(missing_ok=True)


def _check_free_space(home: Path, plan: dict) -> None:
    required = (
        int(plan["total_download_bytes"]) + int(plan["total_unpacked_limit"]) + 256 * 1024 * 1024
    )
    free = shutil.disk_usage(home).free
    if free < required:
        raise ValueError(f"insufficient free space: need {required} bytes, have {free} bytes")


def _enforce_catalog_monotonicity(key_id: str, version: int) -> None:
    state_path = ledgerline_home() / "state" / "catalog-versions.json"
    if not state_path.is_file():
        return
    state = json.loads(state_path.read_text(encoding="utf-8"))
    previous = int(state.get(key_id, 0))
    if version < previous:
        raise ValueError(f"catalog downgrade rejected: {version} < {previous}")


def _record_catalog_version(key_id: str, version: int, home: Path) -> None:
    state_path = home / "state" / "catalog-versions.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state = {}
    if state_path.is_file():
        state = json.loads(state_path.read_text(encoding="utf-8"))
    state[key_id] = max(int(state.get(key_id, 0)), version)
    temp = state_path.with_suffix(".tmp")
    temp.write_text(json.dumps(state, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, state_path)


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("setup plan timestamp is missing a timezone")
    return parsed.astimezone(UTC)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
