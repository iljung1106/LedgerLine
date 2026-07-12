from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

import yaml

from ledgerline.diagnostics import Diagnostic, ValidationError
from ledgerline.model import Piece, parse_anchor
from ledgerline.timeline import Timeline

TARGET_RE = re.compile(r"^(parts|buses|master)\.[a-zA-Z0-9_.-]+$")
INTERPOLATIONS = {"step", "linear", "smooth", "exponential", "bezier"}
UNITS = {"normalized", "db", "percent", "pan", "hz", "semitones", "cents", "native"}


@dataclass(frozen=True, slots=True)
class AutomationPoint:
    measure: int
    beat: Fraction
    value: float
    curve: str | None = None
    in_value: float | None = None
    out_value: float | None = None


@dataclass(frozen=True, slots=True)
class AutomationLane:
    id: str
    target: str
    unit: str
    interpolation: str
    points: tuple[AutomationPoint, ...]


def load_automation(root: str | Path, piece: Piece) -> tuple[AutomationLane, ...]:
    path = Path(root).resolve() / "automation.yaml"
    if not path.is_file():
        return ()
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("automation root must be a mapping")
        _reject_unknown(data, {"format", "lanes"}, "automation.yaml")
        if data.get("format") != 1:
            raise ValueError("automation format must be 1")
        raw_lanes = data.get("lanes")
        if not isinstance(raw_lanes, list) or not raw_lanes:
            raise ValueError("automation lanes must be a non-empty list")
        lanes = tuple(_lane_from_dict(item, index, piece) for index, item in enumerate(raw_lanes))
        _validate_lane_conflicts(lanes)
        return lanes
    except (OSError, yaml.YAMLError, TypeError, ValueError, KeyError) as exc:
        raise ValidationError(
            "automation.yaml is invalid",
            [Diagnostic("error", "automation.invalid", str(path), str(exc))],
        ) from exc


def compile_automation(
    piece: Piece,
    lanes: tuple[AutomationLane, ...],
    output: Path,
    *,
    sample_rate: int = 48_000,
) -> dict:
    timeline = Timeline(piece, sample_rate)
    compiled_lanes = []
    for lane in lanes:
        points = []
        for point in lane.points:
            position = timeline.anchor(point.measure, point.beat)
            points.append(
                {
                    "measure": point.measure,
                    "beat": str(point.beat),
                    "tick": position.tick,
                    "seconds": position.seconds,
                    "sample": position.sample,
                    "value": point.value,
                    "curve": point.curve or lane.interpolation,
                    "in_value": point.in_value,
                    "out_value": point.out_value,
                }
            )
        compiled_lanes.append(
            {
                "id": lane.id,
                "target": lane.target,
                "unit": lane.unit,
                "interpolation": lane.interpolation,
                "points": points,
            }
        )
    report = {
        "schema_version": "1",
        "status": "ok",
        "sample_rate": sample_rate,
        "lanes": compiled_lanes,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def value_at_sample(lane: dict[str, Any], sample: int) -> float:
    points = lane["points"]
    if sample <= points[0]["sample"]:
        return float(points[0]["value"])
    if sample >= points[-1]["sample"]:
        return float(points[-1]["value"])
    for start, end in zip(points, points[1:], strict=False):
        if start["sample"] <= sample <= end["sample"]:
            span = end["sample"] - start["sample"]
            position = 0.0 if span == 0 else (sample - start["sample"]) / span
            curve = start.get("curve") or lane["interpolation"]
            return _interpolate(start, end, position, curve)
    return float(points[-1]["value"])


def _lane_from_dict(raw: Any, index: int, piece: Piece) -> AutomationLane:
    path = f"automation.yaml:lanes[{index}]"
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must be a mapping")
    _reject_unknown(raw, {"id", "target", "unit", "interpolation", "points"}, path)
    lane_id = raw.get("id")
    target = raw.get("target")
    unit = raw.get("unit", "normalized")
    interpolation = raw.get("interpolation", "linear")
    if not isinstance(lane_id, str) or not re.fullmatch(r"[a-z][a-z0-9_-]*", lane_id):
        raise ValueError(f"{path}.id is invalid")
    if not isinstance(target, str) or not TARGET_RE.fullmatch(target):
        raise ValueError(f"{path}.target is invalid")
    if unit not in UNITS:
        raise ValueError(f"{path}.unit is unsupported: {unit!r}")
    if interpolation not in INTERPOLATIONS:
        raise ValueError(f"{path}.interpolation is unsupported: {interpolation!r}")
    raw_points = raw.get("points")
    if not isinstance(raw_points, list) or not raw_points:
        raise ValueError(f"{path}.points must be a non-empty list")
    points = tuple(
        _point_from_dict(item, path, point_index, piece)
        for point_index, item in enumerate(raw_points)
    )
    anchors = [(point.measure, point.beat) for point in points]
    if anchors != sorted(anchors) or len(set(anchors)) != len(anchors):
        raise ValueError(f"{path}.points must be strictly increasing")
    if interpolation == "exponential" and any(point.value <= 0 for point in points):
        raise ValueError(f"{path} exponential automation requires positive values")
    return AutomationLane(lane_id, target, str(unit), str(interpolation), points)


def _point_from_dict(raw: Any, lane_path: str, index: int, piece: Piece) -> AutomationPoint:
    path = f"{lane_path}.points[{index}]"
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must be a mapping")
    _reject_unknown(raw, {"at", "value", "curve", "in_value", "out_value"}, path)
    measure, beat = parse_anchor(str(raw["at"]))
    if not 1 <= measure <= piece.measures or beat > piece.time_at(measure).beats:
        raise ValueError(f"{path}.at is outside the piece")
    value = _finite_number(raw["value"], f"{path}.value")
    curve = raw.get("curve")
    if curve is not None and curve not in INTERPOLATIONS:
        raise ValueError(f"{path}.curve is unsupported: {curve!r}")
    in_value = _optional_number(raw.get("in_value"), f"{path}.in_value")
    out_value = _optional_number(raw.get("out_value"), f"{path}.out_value")
    return AutomationPoint(measure, beat, value, curve, in_value, out_value)


def _validate_lane_conflicts(lanes: tuple[AutomationLane, ...]) -> None:
    ids: set[str] = set()
    targets: set[str] = set()
    for lane in lanes:
        if lane.id in ids:
            raise ValueError(f"duplicate automation lane id: {lane.id}")
        if lane.target in targets:
            raise ValueError(f"multiple automation lanes target {lane.target!r}")
        ids.add(lane.id)
        targets.add(lane.target)


def _interpolate(start: dict, end: dict, position: float, curve: str) -> float:
    start_value = float(start["value"])
    end_value = float(end["value"])
    if curve == "step":
        return start_value
    if curve == "smooth":
        position = position * position * (3.0 - 2.0 * position)
    elif curve == "exponential":
        return start_value * ((end_value / start_value) ** position)
    elif curve == "bezier":
        first = float(start.get("out_value") or start_value)
        second = float(end.get("in_value") or end_value)
        inverse = 1.0 - position
        return (
            inverse**3 * start_value
            + 3 * inverse**2 * position * first
            + 3 * inverse * position**2 * second
            + position**3 * end_value
        )
    return start_value + (end_value - start_value) * position


def _finite_number(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{path} must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{path} must be finite")
    return number


def _optional_number(value: Any, path: str) -> float | None:
    return None if value is None else _finite_number(value, path)


def _reject_unknown(data: dict, allowed: set[str], path: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ValueError(f"{path} has unknown fields: {', '.join(unknown)}")
