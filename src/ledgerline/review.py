from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

from ledgerline.diagnostics import Diagnostic, ValidationError
from ledgerline.model import parse_anchor
from ledgerline.project import load_piece
from ledgerline.timeline import Timeline

REVIEW_ID = re.compile(r"^[a-z][a-z0-9_-]*$")


def compile_review_annotations(project: str | Path, *, sample_rate: int = 48_000) -> dict:
    root = Path(project).resolve()
    path = root / "review.yaml"
    piece = load_piece(root)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("review root must be a mapping")
        _unknown(data, {"format", "annotations"}, "review.yaml")
        if data.get("format") != 1:
            raise ValueError("review format must be 1")
        raw_annotations = data.get("annotations")
        if not isinstance(raw_annotations, list):
            raise ValueError("annotations must be a list")
        timeline = Timeline(piece, sample_rate)
        part_ids = {part.id for part in piece.parts}
        annotations = [
            _annotation(item, index, piece.measures, part_ids, timeline)
            for index, item in enumerate(raw_annotations)
        ]
        ids = [item["id"] for item in annotations]
        if len(ids) != len(set(ids)):
            raise ValueError("annotation ids must be unique")
    except (OSError, yaml.YAMLError, TypeError, ValueError, KeyError) as exc:
        raise ValidationError(
            "review.yaml is invalid",
            [Diagnostic("error", "review.invalid", str(path), str(exc))],
        ) from exc
    report = {
        "schema_version": "1",
        "status": "ok",
        "project": str(root),
        "sample_rate": sample_rate,
        "annotations": annotations,
        "open": sum(item["status"] == "open" for item in annotations),
    }
    output = root / "build" / "review-annotations.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {**report, "report": str(output)}


def _annotation(
    raw: Any,
    index: int,
    measures: int,
    part_ids: set[str],
    timeline: Timeline,
) -> dict[str, Any]:
    path = f"review.yaml.annotations[{index}]"
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must be a mapping")
    allowed = {"id", "at", "end", "category", "severity", "message", "parts", "status"}
    _unknown(raw, allowed, path)
    annotation_id = raw.get("id")
    if not isinstance(annotation_id, str) or not REVIEW_ID.fullmatch(annotation_id):
        raise ValueError(f"{path}.id is invalid")
    start_measure, start_beat = parse_anchor(str(raw["at"]))
    end_measure, end_beat = parse_anchor(str(raw.get("end", raw["at"])))
    if not 1 <= start_measure <= measures or not 1 <= end_measure <= measures:
        raise ValueError(f"{path} is outside the piece")
    start = timeline.anchor(start_measure, start_beat)
    end = timeline.anchor(end_measure, end_beat)
    if end.sample < start.sample:
        raise ValueError(f"{path}.end precedes at")
    category = str(raw.get("category", "other"))
    if category not in {"composition", "orchestration", "performance", "mix", "render", "other"}:
        raise ValueError(f"{path}.category is unsupported")
    severity = str(raw.get("severity", "note"))
    if severity not in {"note", "warning", "blocking"}:
        raise ValueError(f"{path}.severity is unsupported")
    status = str(raw.get("status", "open"))
    if status not in {"open", "resolved", "wontfix"}:
        raise ValueError(f"{path}.status is unsupported")
    parts = raw.get("parts", [])
    if not isinstance(parts, list) or any(item not in part_ids for item in parts):
        raise ValueError(f"{path}.parts contains an unknown part")
    message = raw.get("message")
    if not isinstance(message, str) or not message.strip():
        raise ValueError(f"{path}.message is required")
    return {
        "id": annotation_id,
        "at": str(raw["at"]),
        "end": str(raw.get("end", raw["at"])),
        "start_seconds": start.seconds,
        "end_seconds": end.seconds,
        "start_sample": start.sample,
        "end_sample": end.sample,
        "category": category,
        "severity": severity,
        "message": message.strip(),
        "parts": parts,
        "status": status,
    }


def _unknown(data: dict[str, Any], allowed: set[str], path: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ValueError(f"{path} has unknown fields: {', '.join(unknown)}")
