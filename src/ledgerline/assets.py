from __future__ import annotations

import hashlib
import json
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ledgerline.diagnostics import Diagnostic, ValidationError

ASSET_ID = re.compile(r"^[a-z][a-z0-9_.-]*$")


@dataclass(frozen=True, slots=True)
class Asset:
    id: str
    path: Path
    source: str
    license: str
    redistributable: bool
    derived_from: tuple[str, ...]
    conversion: str | None


def audit_assets(project: str | Path) -> dict:
    root = Path(project).resolve()
    assets = load_assets(root)
    records = []
    for asset in assets:
        if not asset.path.is_file():
            raise ValidationError(
                "an authored asset is missing",
                [Diagnostic("error", "asset.missing", str(asset.path), asset.id)],
            )
        records.append(
            {
                "id": asset.id,
                "path": str(asset.path),
                "bytes": asset.path.stat().st_size,
                "sha256": _sha256(asset.path),
                "source": asset.source,
                "license": asset.license,
                "redistributable": asset.redistributable,
                "derived_from": list(asset.derived_from),
                "conversion": asset.conversion,
            }
        )
    report = {
        "schema_version": "1",
        "status": "ok",
        "project": str(root),
        "assets": records,
    }
    output = root / "build" / "asset-lineage.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {**report, "report": str(output)}


def load_assets(project: str | Path) -> tuple[Asset, ...]:
    root = Path(project).resolve()
    path = root / "assets.yaml"
    if not path.is_file():
        return ()
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("assets root must be a mapping")
        _unknown(data, {"format", "assets"}, "assets.yaml")
        if data.get("format") != 1:
            raise ValueError("assets format must be 1")
        raw_assets = data.get("assets")
        if not isinstance(raw_assets, list):
            raise ValueError("assets must be a list")
        assets = tuple(_asset(item, index, root) for index, item in enumerate(raw_assets))
        ids = [asset.id for asset in assets]
        if len(ids) != len(set(ids)):
            raise ValueError("asset ids must be unique")
        known = set(ids)
        for asset in assets:
            missing = set(asset.derived_from) - known
            if missing:
                raise ValueError(f"asset {asset.id!r} derives from unknown ids: {sorted(missing)}")
        _reject_lineage_cycles(assets)
        return assets
    except (OSError, yaml.YAMLError, TypeError, ValueError, KeyError) as exc:
        raise ValidationError(
            "assets.yaml is invalid",
            [Diagnostic("error", "asset.invalid", str(path), str(exc))],
        ) from exc


def bundle_project(
    project: str | Path,
    output: str | Path | None = None,
    *,
    include_build: bool = True,
) -> dict:
    root = Path(project).resolve()
    asset_report = audit_assets(root)
    output_path = Path(output).resolve() if output else root / "build" / f"{root.name}.llproject"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    authored = sorted(
        path
        for path in root.rglob("*")
        if path.is_file()
        and ".git" not in path.parts
        and ".venv" not in path.parts
        and "__pycache__" not in path.parts
        and (include_build or "build" not in path.relative_to(root).parts)
        and path.resolve() != output_path
    )
    asset_by_path = {asset.path.resolve(): asset for asset in load_assets(root)}
    included: list[str] = []
    placeholders: list[str] = []
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in authored:
            asset = asset_by_path.get(path.resolve())
            if asset is not None and not asset.redistributable:
                placeholder = f"assets-unavailable/{asset.id}.json"
                _writestr(
                    archive,
                    placeholder,
                    json.dumps(
                        {
                            "id": asset.id,
                            "source": asset.source,
                            "license": asset.license,
                            "sha256": _sha256(asset.path),
                            "reason": "license marked this asset non-redistributable",
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                    + "\n",
                )
                placeholders.append(placeholder)
                continue
            relative = path.relative_to(root).as_posix()
            _writefile(archive, relative, path)
            included.append(relative)
        external_assets = [
            asset
            for asset in load_assets(root)
            if root not in asset.path.resolve().parents and asset.path.resolve() != root
        ]
        for asset in external_assets:
            if asset.redistributable:
                relative = f"external-assets/{asset.id}/{asset.path.name}"
                _writefile(archive, relative, asset.path)
                included.append(relative)
            else:
                placeholder = f"assets-unavailable/{asset.id}.json"
                if placeholder not in placeholders:
                    _writestr(
                        archive,
                        placeholder,
                        json.dumps(
                            {
                                "id": asset.id,
                                "source": asset.source,
                                "license": asset.license,
                                "sha256": _sha256(asset.path),
                                "reason": "license marked this asset non-redistributable",
                            },
                            ensure_ascii=False,
                            indent=2,
                        )
                        + "\n",
                    )
                    placeholders.append(placeholder)
        _writestr(
            archive,
            "ledgerline-bundle.json",
            json.dumps(
                {
                    "schema_version": "1",
                    "asset_lineage": asset_report["assets"],
                    "included": included,
                    "placeholders": placeholders,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
        )
    return {
        "schema_version": "1",
        "status": "ok",
        "bundle": str(output_path),
        "bytes": output_path.stat().st_size,
        "sha256": _sha256(output_path),
        "included_files": len(included),
        "license_placeholders": placeholders,
    }


def _asset(raw: Any, index: int, root: Path) -> Asset:
    path = f"assets.yaml.assets[{index}]"
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must be a mapping")
    allowed = {"id", "path", "source", "license", "redistributable", "derived_from", "conversion"}
    _unknown(raw, allowed, path)
    asset_id = raw.get("id")
    if not isinstance(asset_id, str) or not ASSET_ID.fullmatch(asset_id):
        raise ValueError(f"{path}.id is invalid")
    asset_path = Path(str(raw["path"]))
    if not asset_path.is_absolute():
        asset_path = root / asset_path
    source = raw.get("source")
    license_name = raw.get("license")
    if not isinstance(source, str) or not source.strip():
        raise ValueError(f"{path}.source is required")
    if not isinstance(license_name, str) or not license_name.strip():
        raise ValueError(f"{path}.license is required")
    redistributable = raw.get("redistributable")
    if not isinstance(redistributable, bool):
        raise ValueError(f"{path}.redistributable must be explicit true or false")
    derived = raw.get("derived_from", [])
    if not isinstance(derived, list) or any(not isinstance(item, str) for item in derived):
        raise ValueError(f"{path}.derived_from must be a string list")
    conversion = raw.get("conversion")
    if conversion is not None and not isinstance(conversion, str):
        raise ValueError(f"{path}.conversion must be a string")
    return Asset(
        asset_id,
        asset_path.resolve(),
        source.strip(),
        license_name.strip(),
        redistributable,
        tuple(derived),
        conversion,
    )


def _reject_lineage_cycles(assets: tuple[Asset, ...]) -> None:
    graph = {asset.id: set(asset.derived_from) for asset in assets}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(asset_id: str) -> None:
        if asset_id in visiting:
            raise ValueError(f"asset lineage cycle includes {asset_id!r}")
        if asset_id in visited:
            return
        visiting.add(asset_id)
        for parent in graph[asset_id]:
            visit(parent)
        visiting.remove(asset_id)
        visited.add(asset_id)

    for asset_id in graph:
        visit(asset_id)


def _writefile(archive: zipfile.ZipFile, name: str, path: Path) -> None:
    _writestr(archive, name, path.read_bytes())


def _writestr(archive: zipfile.ZipFile, name: str, data: str | bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    archive.writestr(info, data)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _unknown(data: dict[str, Any], allowed: set[str], path: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ValueError(f"{path} has unknown fields: {', '.join(unknown)}")
