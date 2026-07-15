from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def part_performance_inputs(project: str | Path, part_id: str) -> dict[str, Any]:
    """Return only compiled performance inputs that can affect one part render."""

    root = Path(project).resolve()
    build = root / "build"
    return {
        "midi": optional_file_sha256(build / "parts" / f"{part_id}.mid"),
        "expression": part_expression(build / "expression-plan.json", part_id),
        "automation": part_automation(build / "automation.json", part_id),
    }


def part_expression(path: str | Path, part_id: str) -> Any:
    raw = read_json(path)
    parts = raw.get("parts") if isinstance(raw, dict) else None
    return parts.get(part_id) if isinstance(parts, dict) else None


def part_automation(path: str | Path, part_id: str) -> list[Any]:
    raw = read_json(path)
    lanes = raw.get("lanes") if isinstance(raw, dict) else None
    if not isinstance(lanes, list):
        return []
    prefix = f"parts.{part_id}."
    exact = f"parts.{part_id}"
    return [
        lane
        for lane in lanes
        if isinstance(lane, dict)
        and isinstance(lane.get("target"), str)
        and (lane["target"] == exact or lane["target"].startswith(prefix))
    ]


def optional_file_sha256(path: str | Path) -> str | None:
    candidate = Path(path)
    if not candidate.is_file():
        return None
    digest = hashlib.sha256()
    with candidate.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_hash(value: Any) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def read_json(path: str | Path) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
