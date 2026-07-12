from __future__ import annotations

import hashlib
import json
import platform
import sys
from pathlib import Path

from ledgerline import __version__
from ledgerline.assets import load_assets
from ledgerline.environment import doctor
from ledgerline.project import load_piece
from ledgerline.render_graph import load_render_graph


def lock_project_environment(project: str | Path) -> dict:
    root = Path(project).resolve()
    piece = load_piece(root)
    capabilities = doctor()
    render = None
    if (root / "render.yaml").is_file():
        graph = load_render_graph(root, piece)
        render = {
            "sample_rate": graph.sample_rate,
            "block_size": graph.block_size,
            "nodes": [
                {
                    "id": node.id,
                    "engine": node.engine,
                    "executable": _identity(node.executable) if node.executable else None,
                    "host_kind": (
                        "bundled-reference"
                        if node.engine == "plugin" and node.executable is None
                        else "external"
                    ),
                    "instrument": _identity(node.instrument),
                    "state": _identity(node.state) if node.state else None,
                    "arguments": list(node.arguments),
                    "latency_samples": node.latency_samples,
                    "tail_seconds": node.tail_seconds,
                }
                for node in graph.nodes
            ],
        }
    assets = [
        {
            "id": asset.id,
            "path": str(asset.path),
            "sha256": _hash_asset(asset.path) if asset.path.exists() else None,
            "license": asset.license,
            "source": asset.source,
        }
        for asset in load_assets(root)
    ]
    report = {
        "schema_version": "1",
        "status": "ok",
        "ledgerline": __version__,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "capabilities": capabilities,
        "render": render,
        "assets": assets,
    }
    output = root / "ledgerline.lock.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {**report, "lockfile": str(output)}


def _identity(path: Path) -> dict:
    return {
        "path": str(path),
        "bytes": path.stat().st_size if path.is_file() else _directory_size(path),
        "sha256": _hash_asset(path),
    }


def _hash_asset(path: Path) -> str:
    digest = hashlib.sha256()
    if path.is_file():
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    for child in sorted(item for item in path.rglob("*") if item.is_file()):
        digest.update(child.relative_to(path).as_posix().encode("utf-8"))
        digest.update(bytes.fromhex(_hash_asset(child)))
    return digest.hexdigest()


def _directory_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())
