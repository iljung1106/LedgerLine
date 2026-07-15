from __future__ import annotations

import re
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

import yaml

from ledgerline.diagnostics import Diagnostic, ValidationError
from ledgerline.model import Piece
from ledgerline.project import load_piece

BRIEF_ANCHOR_RE = re.compile(r"^[1-9][0-9]*:(?:[1-9][0-9]*(?:\.[0-9]+)?|end)$")
STYLE_CHECK_MODES = {"off", "review"}
PROTECTED_ASPECTS = {
    "pitch",
    "rhythm",
    "dynamics",
    "articulation",
    "expression",
    "instrument",
    "mix",
}


@dataclass(frozen=True, slots=True)
class BriefSection:
    id: str
    from_anchor: str
    to_anchor: str
    function: str


@dataclass(frozen=True, slots=True)
class RoleAssignment:
    part: str
    from_anchor: str
    to_anchor: str
    role: str


@dataclass(frozen=True, slots=True)
class ProtectedRange:
    from_anchor: str
    to_anchor: str
    parts: tuple[str, ...]
    aspects: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CreativeBrief:
    root: Path
    trajectory: tuple[str, ...]
    sections: tuple[BriefSection, ...]
    roles: tuple[RoleAssignment, ...]
    protected: tuple[ProtectedRange, ...]
    style_checks: dict[str, str]
    invariants: tuple[str, ...]
    checkpoints: tuple[str, ...]
    purpose: str | None = None
    duration_seconds: float | None = None
    references: tuple[str, ...] = ()
    required_instruments: tuple[str, ...] = ()
    forbidden_sounds: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": 1,
            "purpose": self.purpose,
            "duration_seconds": self.duration_seconds,
            "trajectory": list(self.trajectory),
            "references": list(self.references),
            "required_instruments": list(self.required_instruments),
            "forbidden_sounds": list(self.forbidden_sounds),
            "sections": [
                {
                    "id": item.id,
                    "from": item.from_anchor,
                    "to": item.to_anchor,
                    "function": item.function,
                }
                for item in self.sections
            ],
            "roles": [
                {
                    "part": item.part,
                    "from": item.from_anchor,
                    "to": item.to_anchor,
                    "role": item.role,
                }
                for item in self.roles
            ],
            "protected": [
                {
                    "from": item.from_anchor,
                    "to": item.to_anchor,
                    "parts": list(item.parts),
                    "aspects": list(item.aspects),
                }
                for item in self.protected
            ],
            "style_checks": dict(self.style_checks),
            "invariants": list(self.invariants),
            "checkpoints": list(self.checkpoints),
        }


def load_brief(root: str | Path, piece: Piece | None = None) -> CreativeBrief | None:
    root_path = Path(root).resolve()
    path = root_path / "brief.yaml"
    if not path.is_file():
        return None
    piece = piece or load_piece(root_path)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("brief root must be a mapping")
        _reject_unknown(
            data,
            {
                "format",
                "purpose",
                "duration_seconds",
                "trajectory",
                "references",
                "required_instruments",
                "forbidden_sounds",
                "sections",
                "roles",
                "protected",
                "style_checks",
                "invariants",
                "checkpoints",
            },
            "brief.yaml",
        )
        if data.get("format") != 1:
            raise ValueError("brief format must be 1")
        required = {
            "trajectory",
            "sections",
            "roles",
            "protected",
            "style_checks",
            "invariants",
            "checkpoints",
        }
        missing = sorted(required - set(data))
        if missing:
            raise ValueError(f"brief is missing required fields: {', '.join(missing)}")

        trajectory = _string_list(data["trajectory"], "trajectory", allow_empty=False)
        references = _string_list(data.get("references", []), "references")
        required_instruments = _string_list(
            data.get("required_instruments", []), "required_instruments"
        )
        forbidden_sounds = _string_list(data.get("forbidden_sounds", []), "forbidden_sounds")
        invariants = _string_list(data["invariants"], "invariants")
        checkpoints = _string_list(data["checkpoints"], "checkpoints", allow_empty=False)
        sections = _sections(data["sections"], piece)
        roles = _roles(data["roles"], piece)
        protected = _protected(data["protected"], piece)
        style_checks = _style_checks(data["style_checks"])
        purpose = _optional_string(data.get("purpose"), "purpose")
        duration_seconds = data.get("duration_seconds")
        if duration_seconds is not None:
            if isinstance(duration_seconds, bool) or not isinstance(duration_seconds, (int, float)):
                raise ValueError("duration_seconds must be numeric")
            duration_seconds = float(duration_seconds)
            if duration_seconds <= 0:
                raise ValueError("duration_seconds must be positive")
        known_parts = {part.id for part in piece.parts}
        unknown_required = sorted(set(required_instruments) - known_parts)
        if unknown_required:
            raise ValueError(
                "required_instruments must name project part IDs; unknown: "
                + ", ".join(unknown_required)
            )
        return CreativeBrief(
            root_path,
            trajectory,
            sections,
            roles,
            protected,
            style_checks,
            invariants,
            checkpoints,
            purpose,
            duration_seconds,
            references,
            required_instruments,
            forbidden_sounds,
        )
    except (OSError, yaml.YAMLError, KeyError, TypeError, ValueError) as exc:
        raise ValidationError(
            "brief.yaml is invalid",
            [Diagnostic("error", "brief.invalid", str(path), str(exc))],
        ) from exc


def validate_edit_actions(root: str | Path, actions: list[dict[str, Any]]) -> list[str]:
    """Return machine-checkable protected-range violations for explicit edit actions."""

    root_path = Path(root).resolve()
    piece = load_piece(root_path)
    brief = load_brief(root_path, piece)
    if brief is None or not brief.protected:
        return []
    locations = _authored_locations(root_path)
    violations = []
    for index, action in enumerate(actions):
        if not isinstance(action, dict):
            violations.append(f"action[{index}] is not an edit object")
            continue
        for part, measure_start, measure_end, aspects in _action_scopes(
            action, piece, locations
        ):
            for protected in brief.protected:
                protected_start = _anchor_measure(protected.from_anchor)
                protected_end = _anchor_measure(protected.to_anchor)
                if part not in protected.parts:
                    continue
                if measure_end < protected_start or protected_end < measure_start:
                    continue
                blocked = sorted(set(aspects) & set(protected.aspects))
                if blocked:
                    violations.append(
                        f"action[{index}] {action.get('type')!r} changes protected "
                        f"{', '.join(blocked)} for {part} in measures "
                        f"{max(measure_start, protected_start)}-"
                        f"{min(measure_end, protected_end)}"
                    )
    return violations


def _authored_locations(root: Path) -> dict[str, tuple[str, int]]:
    piece_data = yaml.safe_load((root / "piece.yaml").read_text(encoding="utf-8"))
    locations = {}
    for reference in piece_data.get("parts", []):
        part = str(reference["id"])
        path = root / str(reference["file"])
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        for raw_measure, measure in data.get("measures", {}).items():
            for events in measure.values():
                for event in events:
                    if event.get("id"):
                        locations[str(event["id"])] = (part, int(raw_measure))
        for control in data.get("controls", []):
            if control.get("id"):
                locations[str(control["id"])] = (
                    part,
                    int(str(control["at"]).split(":", 1)[0]),
                )
    automation_path = root / "automation.yaml"
    if automation_path.is_file():
        automation = yaml.safe_load(automation_path.read_text(encoding="utf-8"))
        for lane in automation.get("lanes", []):
            target = str(lane.get("target", ""))
            lane_part = target.split(".", 2)[1] if target.startswith("parts.") else "*"
            first_measure = 1
            if lane.get("points"):
                first_measure = int(str(lane["points"][0]["at"]).split(":", 1)[0])
            locations[f"lane:{lane.get('id')}"] = (lane_part, first_measure)
            for point in lane.get("points", []):
                if point.get("id"):
                    locations[str(point["id"])] = (
                        lane_part,
                        int(str(point["at"]).split(":", 1)[0]),
                    )
    return locations


def _action_scopes(
    action: dict[str, Any],
    piece: Piece,
    locations: dict[str, tuple[str, int]],
) -> list[tuple[str, int, int, set[str]]]:
    kind = str(action.get("type", ""))
    all_parts = [part.id for part in piece.parts]
    part = str(action["part"]) if action.get("part") is not None else None
    measure = action.get("measure")
    authored_id = action.get(
        "event_id",
        action.get("control_id", action.get("point_id", f"lane:{action.get('lane')}")),
    )
    if authored_id in locations:
        part, measure = locations[str(authored_id)]
    nested_anchor = None
    if isinstance(action.get("point"), dict):
        nested_anchor = action["point"].get("at")
    elif isinstance(action.get("control"), dict):
        nested_anchor = action["control"].get("at")
    if measure is None and nested_anchor is not None:
        measure = int(str(nested_anchor).split(":", 1)[0])
    start = _safe_measure(action.get("measure_start", measure), 1)
    end = _safe_measure(action.get("measure_end", measure), start)

    aspects = _action_aspects(kind, action)
    if not aspects:
        return []
    if kind in {"update_tempo", "insert_tempo", "delete_tempo"}:
        parts = all_parts
        start, end = 1, piece.measures
    elif kind == "update_instrument":
        start, end = 1, piece.measures
        parts = [part] if part is not None else []
    elif part == "*":
        parts = all_parts
    elif part is None:
        return []
    else:
        parts = [part]
    scopes = [(item, start, end, aspects) for item in parts]
    target_measure = action.get("target_measure")
    target_anchor = action.get("at")
    if target_anchor is None:
        target_anchor = nested_anchor
    if target_anchor is None and isinstance(action.get("changes"), dict):
        target_anchor = action["changes"].get("at")
    if target_anchor is not None:
        target_measure = int(str(target_anchor).split(":", 1)[0])
    if target_measure is not None and part is not None:
        target = _safe_measure(target_measure, start)
        scopes.append((part, target, target, aspects))
    return scopes


def _action_aspects(kind: str, action: dict[str, Any]) -> set[str]:
    fixed = {
        "delete_event": {"pitch", "rhythm"},
        "update_instrument": {"instrument"},
        "duplicate_event": {"pitch", "rhythm"},
        "replace_measure_voice": {"pitch", "rhythm", "dynamics", "articulation", "expression"},
        "move_event": {"rhythm"},
        "resize_event": {"rhythm"},
        "transpose_range": {"pitch"},
        "scale_velocity_range": {"dynamics"},
        "set_articulation_range": {"articulation"},
        "insert_control": {"expression"},
        "update_control": {"expression"},
        "delete_control": {"expression"},
        "insert_tempo": {"rhythm"},
        "update_tempo": {"rhythm"},
        "delete_tempo": {"rhythm"},
        "update_mix": {"mix"},
        "update_mix_node": {"mix"},
        "set_mix_send": {"mix"},
        "delete_mix_send": {"mix"},
        "add_mix_insert": {"mix"},
        "update_mix_insert": {"mix"},
        "delete_mix_insert": {"mix"},
        "reorder_mix_insert": {"mix"},
        "insert_point": {"mix"},
        "update_point": {"mix"},
        "move_point": {"mix"},
        "delete_point": {"mix"},
        "set_curve": {"mix"},
    }
    if kind in fixed:
        return fixed[kind]
    if kind == "insert_event":
        event = action.get("event", {})
        result = {"pitch", "rhythm"}
        if isinstance(event, dict):
            if set(event) & {"vel", "dyn"}:
                result.add("dynamics")
            if "art" in event:
                result.add("articulation")
            if "expr" in event:
                result.add("expression")
            if "staff" in event:
                result.add("instrument")
        return result
    if kind in {"update_note", "update_event"}:
        changes = action.get("changes", {})
        if not isinstance(changes, dict):
            return set()
        result = set()
        for field in changes:
            if field in {"p", "pitch"}:
                result.add("pitch")
            elif field in {"d", "duration", "tie", "slur", "tuplet", "grace"}:
                result.add("rhythm")
            elif field in {"vel", "velocity", "dyn", "dynamic"}:
                result.add("dynamics")
            elif field in {"art", "articulation"}:
                result.add("articulation")
            elif field == "expr":
                result.add("expression")
            elif field == "staff":
                result.add("instrument")
        return result
    return set()


def _safe_measure(value: object, default: int) -> int:
    valid = isinstance(value, int) and not isinstance(value, bool) and value > 0
    return value if valid else default


def _anchor_measure(anchor: str) -> int:
    return int(anchor.split(":", 1)[0])


def _sections(raw: object, piece: Piece) -> tuple[BriefSection, ...]:
    values = _mapping_list(raw, "sections")
    result: list[BriefSection] = []
    seen: set[str] = set()
    for index, item in enumerate(values):
        path = f"sections[{index}]"
        _reject_unknown(item, {"id", "from", "to", "function"}, path)
        section_id = _required_string(item.get("id"), f"{path}.id")
        if section_id in seen:
            raise ValueError(f"duplicate section id: {section_id}")
        seen.add(section_id)
        start, end = _range(item, path, piece)
        function = _required_string(item.get("function"), f"{path}.function")
        result.append(BriefSection(section_id, start, end, function))
    return tuple(result)


def _roles(raw: object, piece: Piece) -> tuple[RoleAssignment, ...]:
    values = _mapping_list(raw, "roles")
    known_parts = {part.id for part in piece.parts}
    result = []
    for index, item in enumerate(values):
        path = f"roles[{index}]"
        _reject_unknown(item, {"part", "from", "to", "role"}, path)
        part = _required_string(item.get("part"), f"{path}.part")
        if part not in known_parts:
            raise ValueError(f"{path}.part is unknown: {part}")
        start, end = _range(item, path, piece)
        role = _required_string(item.get("role"), f"{path}.role")
        result.append(RoleAssignment(part, start, end, role))
    return tuple(result)


def _protected(raw: object, piece: Piece) -> tuple[ProtectedRange, ...]:
    values = _mapping_list(raw, "protected")
    known_parts = {part.id for part in piece.parts}
    result = []
    for index, item in enumerate(values):
        path = f"protected[{index}]"
        _reject_unknown(item, {"from", "to", "parts", "aspects"}, path)
        start, end = _range(item, path, piece)
        parts = _string_list(item.get("parts"), f"{path}.parts", allow_empty=False)
        unknown = sorted(set(parts) - known_parts)
        if unknown:
            raise ValueError(f"{path}.parts contains unknown IDs: {', '.join(unknown)}")
        aspects = _string_list(item.get("aspects"), f"{path}.aspects", allow_empty=False)
        unsupported = sorted(set(aspects) - PROTECTED_ASPECTS)
        if unsupported:
            raise ValueError(f"{path}.aspects is unsupported: {', '.join(unsupported)}")
        result.append(ProtectedRange(start, end, parts, aspects))
    return tuple(result)


def _style_checks(raw: object) -> dict[str, str]:
    if not isinstance(raw, dict):
        raise ValueError("style_checks must be a mapping")
    result = {}
    for name, mode in raw.items():
        if not isinstance(name, str) or not name.strip() or mode not in STYLE_CHECK_MODES:
            raise ValueError("style_checks entries require a name and mode: off or review")
        result[name.strip()] = str(mode)
    return result


def _range(item: dict[str, Any], path: str, piece: Piece) -> tuple[str, str]:
    start = _required_string(item.get("from"), f"{path}.from")
    end = _required_string(item.get("to"), f"{path}.to")
    start_position = _anchor_position(start, piece, allow_end=False)
    end_position = _anchor_position(end, piece, allow_end=True)
    if end_position <= start_position:
        raise ValueError(f"{path} range must end after it starts")
    return start, end


def _anchor_position(value: str, piece: Piece, *, allow_end: bool) -> Fraction:
    if not BRIEF_ANCHOR_RE.fullmatch(value):
        raise ValueError(f"invalid brief anchor: {value!r}")
    raw_measure, raw_beat = value.split(":", 1)
    measure = int(raw_measure)
    if not 1 <= measure <= piece.measures:
        raise ValueError(f"brief anchor is outside the piece: {value!r}")
    position = sum(
        (piece.time_at(number).length for number in range(1, measure)), start=Fraction(0)
    )
    time = piece.time_at(measure)
    if raw_beat == "end":
        if not allow_end:
            raise ValueError(f"end is not valid for a range start: {value!r}")
        return position + time.length
    beat = Fraction(raw_beat)
    if beat > time.beats:
        raise ValueError(f"brief anchor beat is outside the measure: {value!r}")
    return position + (beat - 1) * Fraction(1, time.beat_type)


def _mapping_list(raw: object, path: str) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise ValueError(f"{path} must be a list")
    if not all(isinstance(item, dict) for item in raw):
        raise ValueError(f"{path} entries must be mappings")
    return raw


def _string_list(raw: object, path: str, *, allow_empty: bool = True) -> tuple[str, ...]:
    if not isinstance(raw, list) or not all(isinstance(item, str) and item.strip() for item in raw):
        raise ValueError(f"{path} must be a list of non-empty strings")
    if not allow_empty and not raw:
        raise ValueError(f"{path} must not be empty")
    return tuple(item.strip() for item in raw)


def _required_string(raw: object, path: str) -> str:
    value = _optional_string(raw, path)
    if value is None:
        raise ValueError(f"{path} must be a non-empty string")
    return value


def _optional_string(raw: object, path: str) -> str | None:
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"{path} must be a non-empty string")
    return raw.strip()


def _reject_unknown(data: dict[str, Any], allowed: set[str], path: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ValueError(f"{path} has unknown fields: {', '.join(unknown)}")
