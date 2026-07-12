from __future__ import annotations

import hashlib
import json
import re
import shutil
import zipfile
from pathlib import Path
from typing import Any

import yaml

from ledgerline.diagnostics import Diagnostic, ValidationError
from ledgerline.model import DYNAMIC_VELOCITY, parse_pitch
from ledgerline.project import load_piece


def snapshot_project(project: str | Path, name: str) -> dict:
    root = Path(project).resolve()
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}", name):
        raise ValueError("snapshot name is invalid")
    output = root / "build" / "snapshots" / f"{name}.llsnapshot"
    if output.exists():
        raise ValidationError(
            "snapshot already exists",
            [Diagnostic("error", "snapshot.exists", str(output), "Choose a new name.")],
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    records = []
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in _authored_files(root):
            relative = path.relative_to(root).as_posix()
            data = path.read_bytes()
            info = zipfile.ZipInfo(relative, (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
            records.append({"path": relative, "bytes": len(data), "sha256": _hash(data)})
        manifest = {
            "schema_version": "1",
            "name": name,
            "project": root.name,
            "files": records,
        }
        info = zipfile.ZipInfo("ledgerline-snapshot.json", (1980, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(info, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return {
        "schema_version": "1",
        "status": "ok",
        "snapshot": str(output),
        "files": len(records),
        "sha256": _hash(output.read_bytes()),
    }


def apply_edit_plan(project: str | Path, plan: str | Path, output: str | Path) -> dict:
    root = Path(project).resolve()
    plan_path = Path(plan).resolve()
    output_root = Path(output).resolve()
    if output_root == root or root in output_root.parents:
        raise ValidationError(
            "edit output must be outside the source project",
            [
                Diagnostic(
                    "error",
                    "edit.output_nested",
                    str(output_root),
                    "Choose a sibling or unrelated directory.",
                )
            ],
        )
    if output_root.exists():
        raise ValidationError(
            "edit output already exists",
            [Diagnostic("error", "edit.output_exists", str(output_root), "Choose a new path.")],
        )
    data = yaml.safe_load(plan_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValidationError("edit plan root must be a mapping")
    try:
        _unknown(data, {"format", "edits"}, "edit-plan")
        if data.get("format") != 1 or not isinstance(data.get("edits"), list):
            raise ValueError("edit plan requires format 1 and an edits list")
        shutil.copytree(root, output_root, ignore=shutil.ignore_patterns("build", ".git", ".venv"))
        piece_data = yaml.safe_load((output_root / "piece.yaml").read_text(encoding="utf-8"))
        part_files = {
            str(item["id"]): output_root / str(item["file"]) for item in piece_data["parts"]
        }
        applied = []
        for index, raw in enumerate(data["edits"]):
            applied.append(_apply_edit(raw, index, part_files))
        load_piece(output_root)
    except (OSError, yaml.YAMLError, TypeError, ValueError, KeyError) as exc:
        raise ValidationError(
            "edit plan is invalid or produced invalid music",
            [Diagnostic("error", "edit.invalid", str(plan_path), str(exc))],
        ) from exc
    report = {
        "schema_version": "1",
        "status": "ok",
        "source": str(root),
        "output": str(output_root),
        "edits": applied,
    }
    report_path = output_root / "edit-report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {**report, "report": str(report_path)}


def diff_projects(before: str | Path, after: str | Path) -> dict:
    before_root = Path(before).resolve()
    after_root = Path(after).resolve()
    first = {
        path.relative_to(before_root).as_posix(): _hash(path.read_bytes())
        for path in _authored_files(before_root)
    }
    second = {
        path.relative_to(after_root).as_posix(): _hash(path.read_bytes())
        for path in _authored_files(after_root)
    }
    paths = sorted(set(first) | set(second))
    changes = [
        {
            "path": path,
            "status": "added"
            if path not in first
            else "removed"
            if path not in second
            else "modified",
        }
        for path in paths
        if first.get(path) != second.get(path)
    ]
    return {
        "schema_version": "1",
        "status": "ok",
        "before": str(before_root),
        "after": str(after_root),
        "changed_files": changes,
        "unchanged_files": len(paths) - len(changes),
    }


def _apply_edit(raw: Any, index: int, part_files: dict[str, Path]) -> dict:
    path = f"edits[{index}]"
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must be a mapping")
    _unknown(raw, {"scope", "operation"}, path)
    scope = raw.get("scope")
    operation = raw.get("operation")
    if not isinstance(scope, dict) or not isinstance(operation, dict):
        raise ValueError(f"{path}.scope and operation must be mappings")
    _unknown(scope, {"part", "measure_start", "measure_end"}, f"{path}.scope")
    part_id = str(scope["part"])
    if part_id not in part_files:
        raise ValueError(f"{path} references unknown part {part_id!r}")
    start = _positive_integer(scope.get("measure_start", 1), f"{path}.scope.measure_start")
    end = _positive_integer(scope.get("measure_end", start), f"{path}.scope.measure_end")
    if end < start:
        raise ValueError(f"{path} measure range is reversed")
    part_path = part_files[part_id]
    data = yaml.safe_load(part_path.read_text(encoding="utf-8"))
    kind = operation.get("type")
    if kind == "transpose":
        _unknown(operation, {"type", "semitones"}, f"{path}.operation")
        semitones = _integer(operation.get("semitones"), f"{path}.operation.semitones")
        count = _edit_events(data, start, end, lambda event: _transpose_event(event, semitones))
    elif kind == "scale_velocity":
        _unknown(operation, {"type", "factor", "minimum", "maximum"}, f"{path}.operation")
        factor = _number(operation.get("factor"), f"{path}.operation.factor")
        minimum = _integer(operation.get("minimum", 1), f"{path}.operation.minimum")
        maximum = _integer(operation.get("maximum", 127), f"{path}.operation.maximum")
        count = _edit_events(
            data, start, end, lambda event: _scale_velocity(event, factor, minimum, maximum)
        )
    elif kind == "set_articulation":
        _unknown(operation, {"type", "articulation"}, f"{path}.operation")
        articulation = str(operation["articulation"])
        count = _edit_events(data, start, end, lambda event: _set_articulation(event, articulation))
    elif kind == "scale_performance":
        _unknown(operation, {"type", "parameter", "factor"}, f"{path}.operation")
        parameter = str(operation["parameter"])
        factor = _number(operation["factor"], f"{path}.operation.factor")
        count = 0
        for control in data.get("controls", []):
            measure = int(str(control.get("at", "0:0")).split(":", 1)[0])
            if (
                start <= measure <= end
                and control.get("type") == "performance"
                and control.get("parameter") == parameter
            ):
                control["value"] = min(1.0, max(0.0, float(control["value"]) * factor))
                count += 1
    else:
        raise ValueError(f"{path}.operation.type is unsupported: {kind!r}")
    part_path.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    return {
        "part": part_id,
        "measure_start": start,
        "measure_end": end,
        "operation": kind,
        "affected": count,
    }


def _edit_events(data: dict, start: int, end: int, operation) -> int:
    count = 0
    for raw_number, measure in data.get("measures", {}).items():
        if start <= int(raw_number) <= end:
            for events in measure.values():
                for event in events:
                    if not event.get("r", False):
                        operation(event)
                        count += 1
    return count


def _transpose_event(event: dict, semitones: int) -> None:
    raw = event["p"]
    pitches = [raw] if isinstance(raw, str) else raw
    result = [_midi_pitch(parse_pitch(str(pitch)).midi + semitones) for pitch in pitches]
    event["p"] = result[0] if isinstance(raw, str) else result


def _scale_velocity(event: dict, factor: float, minimum: int, maximum: int) -> None:
    source = int(event.get("vel", DYNAMIC_VELOCITY.get(event.get("dyn", "mf"), 76)))
    event["vel"] = max(minimum, min(maximum, round(source * factor)))


def _set_articulation(event: dict, articulation: str) -> None:
    event["art"] = articulation


def _midi_pitch(midi: int) -> str:
    if not 0 <= midi <= 127:
        raise ValueError("transposition leaves MIDI range")
    names = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
    return f"{names[midi % 12]}{midi // 12 - 1}"


def _authored_files(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file()
        and not {"build", ".git", ".venv", "__pycache__"} & set(path.relative_to(root).parts)
    )


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _integer(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{path} must be an integer")
    return value


def _positive_integer(value: Any, path: str) -> int:
    result = _integer(value, path)
    if result < 1:
        raise ValueError(f"{path} must be positive")
    return result


def _number(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{path} must be numeric")
    return float(value)


def _unknown(data: dict[str, Any], allowed: set[str], path: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ValueError(f"{path} has unknown fields: {', '.join(unknown)}")
