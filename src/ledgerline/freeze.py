from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from ledgerline.diagnostics import CapabilityError, Diagnostic
from ledgerline.project import load_piece


def freeze_part(project: str | Path, part_id: str, source: str | Path | None = None) -> dict:
    root = Path(project).resolve()
    piece = load_piece(root)
    if part_id not in {part.id for part in piece.parts}:
        raise ValueError(f"unknown part: {part_id}")
    source_path = Path(source).resolve() if source else root / "build" / "stems" / f"{part_id}.wav"
    if not source_path.is_file():
        raise CapabilityError(
            "stem to freeze is missing",
            [Diagnostic("error", "freeze.source_missing", str(source_path), "Render first.")],
        )
    output = root / "frozen" / f"{part_id}.wav"
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise CapabilityError(
            "a frozen stem already exists",
            [Diagnostic("error", "freeze.exists", str(output), "Preserve or rename it first.")],
        )
    shutil.copyfile(source_path, output)
    report = {
        "schema_version": "1",
        "status": "ok",
        "part": part_id,
        "source": str(source_path),
        "frozen": str(output),
        "bytes": output.stat().st_size,
        "sha256": _sha256(output),
        "render_node": {
            "id": f"{part_id}-frozen",
            "part": part_id,
            "engine": "frozen",
            "instrument": f"frozen/{part_id}.wav",
        },
    }
    receipt = root / "frozen" / f"{part_id}.json"
    receipt.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {**report, "receipt": str(receipt)}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
