from __future__ import annotations

import shutil
import tempfile
import uuid
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path
from typing import Any

import yaml

from ledgerline.diagnostics import Diagnostic, ValidationError
from ledgerline.model import (
    MUSICXML_ARTICULATIONS,
    ArticulationDefinition,
    ControlEvent,
    InstrumentProfile,
    KeyChange,
    Measure,
    Part,
    PerformanceBinding,
    Piece,
    StaffDefinition,
    TempoChange,
    TimeChange,
    control_event_from_dict,
    event_from_dict,
    parse_anchor,
    parse_pitch,
)


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValidationError(
            f"missing project file: {path}",
            [Diagnostic("error", "file.missing", str(path), "File does not exist.")],
        ) from exc
    except yaml.YAMLError as exc:
        raise ValidationError(
            f"invalid YAML: {path}",
            [Diagnostic("error", "yaml.invalid", str(path), str(exc))],
        ) from exc
    if not isinstance(value, dict):
        raise ValidationError(
            f"expected a YAML mapping: {path}",
            [Diagnostic("error", "yaml.root_type", str(path), "Root must be a mapping.")],
        )
    return value


def _reject_unknown(data: dict[str, Any], allowed: set[str], path: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ValueError(f"{path} has unknown fields: {', '.join(unknown)}")


def _profile_search_paths(root: Path, profile_id: str) -> list[Path]:
    package_profiles = Path(__file__).parent / "data" / "profiles"
    return [
        root / "profiles" / f"{profile_id}.yaml",
        package_profiles / f"{profile_id}.yaml",
    ]


def load_profile(root: Path, profile_id: str) -> InstrumentProfile:
    for path in _profile_search_paths(root, profile_id):
        if path.is_file():
            data = _read_yaml(path)
            break
    else:
        raise ValidationError(
            f"unknown instrument profile: {profile_id}",
            [
                Diagnostic(
                    "error",
                    "profile.missing",
                    f"piece.yaml:parts[{profile_id}]",
                    f"No project or built-in profile named {profile_id!r}.",
                )
            ],
        )
    try:
        _reject_unknown(
            data,
            {
                "format",
                "id",
                "name",
                "family",
                "range",
                "transposition",
                "midi",
                "clef",
                "articulations",
                "keyswitches",
                "performance",
            },
            str(path),
        )
        absolute = data["range"]["absolute"]
        comfortable = data["range"].get("comfortable", absolute)
        midi = data["midi"]
        clef = data.get("clef", {"sign": "G", "line": 2})
        _reject_unknown(data["range"], {"absolute", "comfortable"}, f"{path}:range")
        _reject_unknown(midi, {"bank_msb", "bank_lsb", "program"}, f"{path}:midi")
        _reject_unknown(clef, {"sign", "line"}, f"{path}:clef")
        raw_keyswitches = data.get("keyswitches", {})
        if not isinstance(raw_keyswitches, dict):
            raise ValueError("keyswitches must be a mapping of semantic names to pitches")
        keyswitches = {}
        for name, pitch in raw_keyswitches.items():
            if not isinstance(name, str) or not name.strip():
                raise ValueError("keyswitch names must be non-empty strings")
            if not isinstance(pitch, str):
                raise ValueError(f"keyswitch {name!r} pitch must be a string")
            normalized_name = name.strip()
            if normalized_name in keyswitches:
                raise ValueError(f"duplicate keyswitch name: {normalized_name!r}")
            keyswitches[normalized_name] = parse_pitch(pitch)
        performance = _load_performance_bindings(data.get("performance", {}), path)
        articulation_definitions = _load_articulation_definitions(
            data.get("articulations", []), path
        )
        return InstrumentProfile(
            id=str(data["id"]),
            name=str(data["name"]),
            family=str(data["family"]),
            absolute_low=parse_pitch(str(absolute[0])),
            absolute_high=parse_pitch(str(absolute[1])),
            comfortable_low=parse_pitch(str(comfortable[0])),
            comfortable_high=parse_pitch(str(comfortable[1])),
            transposition=int(data.get("transposition", 0)),
            bank_msb=int(midi.get("bank_msb", 0)),
            bank_lsb=int(midi.get("bank_lsb", 0)),
            program=int(midi["program"]),
            clef_sign=str(clef["sign"]),
            clef_line=int(clef["line"]),
            articulations=frozenset(articulation_definitions),
            articulation_definitions=articulation_definitions,
            keyswitches=keyswitches,
            performance=performance,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValidationError(
            f"invalid profile: {path}",
            [Diagnostic("error", "profile.invalid", str(path), str(exc))],
        ) from exc


def _load_articulation_definitions(
    raw: object, path: Path
) -> dict[str, ArticulationDefinition]:
    if not isinstance(raw, list):
        raise ValueError("articulations must be a list")
    defaults = {
        "staccato": ("staccato", 0.5, 0),
        "tenuto": ("tenuto", 0.98, 0),
        "accent": ("accent", 0.9, 10),
        "marcato": ("strong-accent", 0.72, 16),
    }
    definitions: dict[str, ArticulationDefinition] = {}
    for index, item in enumerate(raw):
        item_path = f"{path}:articulations[{index}]"
        if isinstance(item, str):
            if item not in defaults:
                raise ValueError(
                    f"{item_path} custom articulation requires an explicit mapping"
                )
            musicxml, gate, velocity_delta = defaults[item]
            definition = ArticulationDefinition(item, musicxml, None, gate, velocity_delta)
        elif isinstance(item, dict):
            _reject_unknown(
                item,
                {"id", "musicxml", "label", "gate", "velocity_delta"},
                item_path,
            )
            articulation_id = item.get("id")
            musicxml = item.get("musicxml")
            label = item.get("label")
            if not isinstance(articulation_id, str) or not articulation_id:
                raise ValueError(f"{item_path}.id must be a non-empty string")
            from ledgerline.model import ARTICULATION_ID_RE

            if not ARTICULATION_ID_RE.fullmatch(articulation_id):
                raise ValueError(f"{item_path}.id is invalid")
            if musicxml not in MUSICXML_ARTICULATIONS:
                raise ValueError(f"{item_path}.musicxml is unsupported: {musicxml!r}")
            if musicxml == "other-articulation" and (
                not isinstance(label, str) or not label.strip()
            ):
                raise ValueError(f"{item_path}.label is required for other-articulation")
            if label is not None and (not isinstance(label, str) or not label.strip()):
                raise ValueError(f"{item_path}.label must be a non-empty string")
            gate = float(item.get("gate", 0.9))
            velocity_delta = item.get("velocity_delta", 0)
            if not 0.05 <= gate <= 1.0:
                raise ValueError(f"{item_path}.gate must be between 0.05 and 1")
            if (
                isinstance(velocity_delta, bool)
                or not isinstance(velocity_delta, int)
                or not -64 <= velocity_delta <= 64
            ):
                raise ValueError(f"{item_path}.velocity_delta must be an integer from -64 to 64")
            definition = ArticulationDefinition(
                articulation_id,
                str(musicxml),
                label.strip() if isinstance(label, str) else None,
                gate,
                velocity_delta,
            )
        else:
            raise ValueError(f"{item_path} must be a string or mapping")
        if definition.id in definitions:
            raise ValueError(f"duplicate articulation id: {definition.id!r}")
        definitions[definition.id] = definition
    return definitions


def _load_performance_bindings(raw: object, path: Path) -> dict[str, PerformanceBinding]:
    if not isinstance(raw, dict):
        raise ValueError("performance must be a mapping")
    bindings: dict[str, PerformanceBinding] = {}
    for name, value in raw.items():
        binding_path = f"{path}:performance.{name}"
        if not isinstance(name, str) or not name.strip() or not isinstance(value, dict):
            raise ValueError(f"{binding_path} must be a named mapping")
        _reject_unknown(
            value,
            {"type", "controller", "parameter", "min", "max", "default"},
            binding_path,
        )
        binding_type = value.get("type")
        if binding_type not in {"cc", "plugin_parameter", "mix"}:
            raise ValueError(f"{binding_path}.type is unsupported: {binding_type!r}")
        controller = value.get("controller")
        parameter = value.get("parameter")
        if binding_type == "cc":
            if isinstance(controller, bool) or not isinstance(controller, int):
                raise ValueError(f"{binding_path}.controller must be an integer")
            if not 1 <= controller <= 127 or controller in {32, 64}:
                raise ValueError(f"{binding_path}.controller is reserved or outside MIDI range")
        elif not isinstance(parameter, str) or not parameter.strip():
            raise ValueError(f"{binding_path}.parameter must be a non-empty string")
        minimum = float(value.get("min", 0.0))
        maximum = float(value.get("max", 127.0 if binding_type == "cc" else 1.0))
        default = float(value.get("default", 0.5))
        if maximum <= minimum or not 0.0 <= default <= 1.0:
            raise ValueError(f"{binding_path} has invalid range or default")
        bindings[name.strip()] = PerformanceBinding(
            type=str(binding_type),
            controller=controller,
            parameter=parameter.strip() if isinstance(parameter, str) else None,
            minimum=minimum,
            maximum=maximum,
            default=default,
        )
    return bindings


def load_piece(root: str | Path) -> Piece:
    root_path = Path(root).resolve()
    piece_path = root_path / "piece.yaml"
    data = _read_yaml(piece_path)
    diagnostics: list[Diagnostic] = []
    try:
        _reject_unknown(
            data,
            {"format", "title", "measures", "time", "tempo", "key", "parts"},
            "piece.yaml",
        )
        if int(data.get("format", 0)) != 1:
            raise ValueError("piece format must be 1")
        title = str(data["title"])
        measure_count = int(data["measures"])
        if measure_count < 1:
            raise ValueError("measures must be positive")
        time_changes = tuple(_time_change(item, index) for index, item in enumerate(data["time"]))
        tempo_changes = []
        for index, item in enumerate(data["tempo"]):
            _reject_unknown(dict(item), {"at", "bpm", "ramp"}, f"piece.yaml:tempo[{index}]")
            measure, beat = parse_anchor(str(item["at"]))
            ramp = item.get("ramp")
            if ramp is None:
                tempo_changes.append(TempoChange(measure, beat, float(item["bpm"])))
            else:
                if not isinstance(ramp, dict):
                    raise ValueError(f"piece.yaml:tempo[{index}].ramp must be a mapping")
                _reject_unknown(
                    ramp,
                    {"to", "bpm", "curve"},
                    f"piece.yaml:tempo[{index}].ramp",
                )
                if set(ramp) not in ({"to", "bpm"}, {"to", "bpm", "curve"}):
                    raise ValueError(
                        f"piece.yaml:tempo[{index}].ramp requires to and bpm"
                    )
                end_measure, end_beat = parse_anchor(str(ramp["to"]))
                curve = ramp.get("curve", "linear")
                if curve != "linear":
                    raise ValueError("tempo ramp curve must be linear")
                tempo_changes.append(
                    TempoChange(
                        measure,
                        beat,
                        float(item["bpm"]),
                        end_measure,
                        end_beat,
                        float(ramp["bpm"]),
                        str(curve),
                    )
                )
        key_changes = tuple(_key_change(item, index) for index, item in enumerate(data["key"]))
        if not time_changes or time_changes[0].measure != 1:
            raise ValueError("time changes must start at measure 1")
        if not tempo_changes or tempo_changes[0].measure != 1:
            raise ValueError("tempo changes must start at measure 1")
        if not key_changes or key_changes[0].measure != 1:
            raise ValueError("key changes must start at measure 1")
    except (KeyError, TypeError, ValueError) as exc:
        raise ValidationError(
            "invalid piece.yaml",
            [Diagnostic("error", "piece.invalid", str(piece_path), str(exc))],
        ) from exc

    parts: list[Part] = []
    profiles: dict[str, InstrumentProfile] = {}
    seen_ids: set[str] = set()
    raw_parts = data.get("parts")
    if not isinstance(raw_parts, list) or not raw_parts:
        raise ValidationError(
            "piece requires at least one part",
            [Diagnostic("error", "piece.parts_empty", str(piece_path), "parts must be non-empty.")],
        )
    for index, item in enumerate(raw_parts):
        path_prefix = f"piece.yaml:parts[{index}]"
        try:
            _reject_unknown(dict(item), {"id", "name", "profile", "file"}, path_prefix)
            part_id = str(item["id"])
            if part_id in seen_ids:
                raise ValueError(f"duplicate part id: {part_id}")
            seen_ids.add(part_id)
            name = str(item.get("name", part_id))
            profile_id = str(item["profile"])
            source_path = (root_path / str(item["file"])).resolve()
            if root_path not in source_path.parents:
                raise ValueError("part file must remain inside the project directory")
            profile = load_profile(root_path, profile_id)
            profiles[profile_id] = profile
            part = _load_part(part_id, name, profile_id, profile, source_path, diagnostics)
            parts.append(part)
        except (KeyError, TypeError, ValueError) as exc:
            diagnostics.append(Diagnostic("error", "part.reference_invalid", path_prefix, str(exc)))

    piece = Piece(
        root=root_path,
        title=title,
        measures=measure_count,
        time_changes=tuple(sorted(time_changes, key=lambda item: item.measure)),
        tempo_changes=tuple(sorted(tempo_changes, key=lambda item: (item.measure, item.beat))),
        key_changes=tuple(sorted(key_changes, key=lambda item: item.measure)),
        parts=tuple(parts),
        profiles=profiles,
    )
    from ledgerline.motifs import apply_project_motifs

    piece = apply_project_motifs(piece)
    _validate_authored_ids(piece, diagnostics)
    diagnostics.extend(validate_piece(piece))
    if any(item.severity == "error" for item in diagnostics):
        raise ValidationError("project validation failed", diagnostics)
    return piece


def prepare_ids(root: str | Path, *, dry_run: bool = False) -> dict[str, Any]:
    """Add persistent IDs to authored notes, controls, and automation points.

    Existing IDs are preserved. A dry run returns the exact deterministic IDs that a subsequent
    apply would write. Applying creates a source snapshot before atomically replacing YAML files.
    """

    root_path = Path(root).resolve()
    piece = load_piece(root_path)
    from ledgerline.automation import load_automation

    load_automation(root_path, piece)
    piece_data = _read_yaml(root_path / "piece.yaml")
    documents: dict[Path, dict[str, Any]] = {}
    used: set[str] = set()
    changes: list[dict[str, str]] = []

    for reference in piece_data["parts"]:
        part_id = str(reference["id"])
        path = (root_path / str(reference["file"])).resolve()
        data = _read_yaml(path)
        documents[path] = data
        for raw_measure, measure in data.get("measures", {}).items():
            for voice, events in measure.items():
                for index, event in enumerate(events):
                    existing = event.get("id")
                    if existing:
                        used.add(str(existing))
                        continue
                    if event.get("r", False):
                        continue
                    location = f"parts/{part_id}/measures/{raw_measure}/{voice}/{index}"
                    event_id = _prepared_id("evt", location, used)
                    event["id"] = event_id
                    changes.append(
                        {
                            "kind": "event",
                            "path": _relative(path, root_path),
                            "location": location,
                            "id": event_id,
                        }
                    )
        for index, control in enumerate(data.get("controls", [])):
            existing = control.get("id")
            if existing:
                used.add(str(existing))
                continue
            location = f"parts/{part_id}/controls/{index}"
            control_id = _prepared_id("ctl", location, used)
            control["id"] = control_id
            changes.append(
                {
                    "kind": "control",
                    "path": _relative(path, root_path),
                    "location": location,
                    "id": control_id,
                }
            )

    automation_path = root_path / "automation.yaml"
    if automation_path.is_file():
        automation = _read_yaml(automation_path)
        documents[automation_path] = automation
        for lane_index, lane in enumerate(automation.get("lanes", [])):
            lane_id = str(lane.get("id", f"lane-{lane_index}"))
            for point_index, point in enumerate(lane.get("points", [])):
                existing = point.get("id")
                if existing:
                    used.add(str(existing))
                    continue
                location = f"automation/{lane_id}/points/{point_index}"
                point_id = _prepared_id("aut", location, used)
                point["id"] = point_id
                changes.append(
                    {
                        "kind": "automation_point",
                        "path": _relative(automation_path, root_path),
                        "location": location,
                        "id": point_id,
                    }
                )

    changed_paths = sorted(
        {root_path / item["path"] for item in changes}, key=lambda item: item.as_posix()
    )
    snapshot: str | None = None
    if changes and not dry_run:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
        snapshot_path = root_path / ".ledgerline" / "history" / f"prepare-ids-{stamp}"
        before = {path: path.read_bytes() for path in changed_paths}
        try:
            for path in changed_paths:
                backup = snapshot_path / path.relative_to(root_path)
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, backup)
            for path in changed_paths:
                _write_yaml_atomic(path, documents[path])
            piece = load_piece(root_path)
            load_automation(root_path, piece)
        except Exception:
            for path, content in before.items():
                _write_bytes_atomic(path, content)
            raise
        snapshot = _relative(snapshot_path, root_path)
    return {
        "schema_version": "1",
        "status": "dry-run" if dry_run else "ok",
        "project": str(root_path),
        "changed": len(changes),
        "files": [_relative(path, root_path) for path in changed_paths],
        "changes": changes,
        "snapshot": snapshot,
    }


def _validate_authored_ids(piece: Piece, diagnostics: list[Diagnostic]) -> None:
    seen: dict[str, str] = {}
    for part in piece.parts:
        for number, measure in part.measures.items():
            for voice, events in measure.voices.items():
                for index, event in enumerate(events):
                    if event.id:
                        _record_authored_id(
                            event.id,
                            f"{part.source_path}:measures[{number}].{voice}[{index}]",
                            seen,
                            diagnostics,
                        )
        for index, control in enumerate(part.controls):
            if control.id:
                _record_authored_id(
                    control.id,
                    f"{part.source_path}:controls[{index}]",
                    seen,
                    diagnostics,
                )


def _record_authored_id(
    authored_id: str,
    path: str,
    seen: dict[str, str],
    diagnostics: list[Diagnostic],
) -> None:
    previous = seen.get(authored_id)
    if previous is None:
        seen[authored_id] = path
        return
    diagnostics.append(
        Diagnostic(
            "error",
            "authored_id.duplicate",
            path,
            f"Authored ID {authored_id!r} is already used at {previous}.",
        )
    )


def _prepared_id(prefix: str, location: str, used: set[str]) -> str:
    counter = 0
    while True:
        seed = location if counter == 0 else f"{location}:{counter}"
        candidate = f"{prefix}_{uuid.uuid5(uuid.NAMESPACE_URL, f'ledgerline:{seed}').hex}"
        if candidate not in used:
            used.add(candidate)
            return candidate
        counter += 1


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _write_yaml_atomic(path: Path, data: dict[str, Any]) -> None:
    content = yaml.safe_dump(data, sort_keys=False, allow_unicode=True).encode("utf-8")
    _write_bytes_atomic(path, content)


def _write_bytes_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as handle:
        temporary = Path(handle.name)
        handle.write(content)
        handle.flush()
    temporary.replace(path)


def _load_part(
    part_id: str,
    name: str,
    profile_id: str,
    profile: InstrumentProfile,
    source_path: Path,
    diagnostics: list[Diagnostic],
) -> Part:
    data = _read_yaml(source_path)
    try:
        _reject_unknown(
            data,
            {"format", "part", "staves", "controls", "measures"},
            str(source_path),
        )
    except ValueError as exc:
        diagnostics.append(Diagnostic("error", "part.unknown_field", str(source_path), str(exc)))
    if int(data.get("format", 0)) != 1:
        diagnostics.append(
            Diagnostic("error", "part.format", str(source_path), "part format must be 1")
        )
    if str(data.get("part", "")) != part_id:
        diagnostics.append(
            Diagnostic(
                "error",
                "part.id_mismatch",
                str(source_path),
                f"Expected part id {part_id!r}.",
            )
        )
    try:
        staves = _load_staves(data.get("staves"), profile, source_path)
    except (TypeError, ValueError, KeyError) as exc:
        diagnostics.append(
            Diagnostic("error", "part.staves_invalid", f"{source_path}:staves", str(exc))
        )
        staves = (StaffDefinition(1, "staff-1", profile.clef_sign, profile.clef_line),)

    raw_measures = data.get("measures")
    if not isinstance(raw_measures, dict):
        diagnostics.append(
            Diagnostic(
                "error", "part.measures_type", str(source_path), "measures must be a mapping"
            )
        )
        raw_measures = {}
    measures: dict[int, Measure] = {}
    for raw_number, raw_measure in raw_measures.items():
        try:
            number = int(raw_number)
            if not isinstance(raw_measure, dict) or not raw_measure:
                raise ValueError("measure must contain at least one voice")
            voices: dict[str, tuple] = {}
            for voice_name, raw_events in raw_measure.items():
                if not re_voice_name(voice_name):
                    raise ValueError(f"invalid voice name: {voice_name!r}")
                if not isinstance(raw_events, list) or not raw_events:
                    raise ValueError(f"{voice_name} must be a non-empty event list")
                voices[str(voice_name)] = tuple(
                    event_from_dict(dict(event)) for event in raw_events
                )
            measures[number] = Measure(number, voices)
        except (TypeError, ValueError, KeyError) as exc:
            diagnostics.append(
                Diagnostic(
                    "error",
                    "measure.invalid",
                    f"{source_path}:measures[{raw_number}]",
                    str(exc),
                )
            )
    controls: list[ControlEvent] = []
    raw_controls = data.get("controls", [])
    if not isinstance(raw_controls, list):
        diagnostics.append(
            Diagnostic("error", "part.controls_type", str(source_path), "controls must be a list")
        )
    else:
        for index, raw_control in enumerate(raw_controls):
            try:
                if not isinstance(raw_control, dict):
                    raise ValueError("control must be a mapping")
                controls.append(control_event_from_dict(dict(raw_control)))
            except (TypeError, ValueError, KeyError) as exc:
                diagnostics.append(
                    Diagnostic(
                        "error",
                        "control.invalid",
                        f"{source_path}:controls[{index}]",
                        str(exc),
                    )
                )
    return Part(
        id=part_id,
        name=name,
        profile_id=profile_id,
        source_path=source_path,
        measures=measures,
        controls=tuple(controls),
        staves=staves,
    )


def _load_staves(
    raw_staves: object,
    profile: InstrumentProfile,
    source_path: Path,
) -> tuple[StaffDefinition, ...]:
    if raw_staves is None:
        return (StaffDefinition(1, "staff-1", profile.clef_sign, profile.clef_line),)
    if not isinstance(raw_staves, list) or not raw_staves:
        raise ValueError("staves must be a non-empty list")
    if len(raw_staves) > 32:
        raise ValueError("a part supports at most 32 staves")

    staves: list[StaffDefinition] = []
    for index, raw_staff in enumerate(raw_staves):
        path = f"{source_path}:staves[{index}]"
        if not isinstance(raw_staff, dict):
            raise ValueError(f"{path} must be a mapping")
        _reject_unknown(raw_staff, {"number", "name", "clef"}, path)
        number = raw_staff.get("number")
        if isinstance(number, bool) or not isinstance(number, int):
            raise ValueError(f"{path}.number must be an integer")
        if not 1 <= number <= 32:
            raise ValueError(f"{path}.number must be between 1 and 32")
        name = str(raw_staff.get("name", f"staff-{number}")).strip()
        if not name:
            raise ValueError(f"{path}.name must be non-empty")
        clef = raw_staff.get("clef")
        if not isinstance(clef, dict):
            raise ValueError(f"{path}.clef must be a mapping")
        _reject_unknown(clef, {"sign", "line"}, f"{path}.clef")
        sign = str(clef.get("sign", ""))
        if sign not in {"G", "F", "C", "percussion", "TAB", "none"}:
            raise ValueError(f"{path}.clef.sign is unsupported: {sign!r}")
        line = clef.get("line")
        if isinstance(line, bool) or not isinstance(line, int) or not 1 <= line <= 5:
            raise ValueError(f"{path}.clef.line must be an integer between 1 and 5")
        staves.append(StaffDefinition(number, name, sign, line))

    actual_numbers = [staff.number for staff in staves]
    expected_numbers = list(range(1, len(staves) + 1))
    if actual_numbers != expected_numbers:
        raise ValueError(
            f"staff numbers must be contiguous and ordered {expected_numbers}; got {actual_numbers}"
        )
    return tuple(staves)


def re_voice_name(value: object) -> bool:
    return isinstance(value, str) and len(value) >= 2 and value[0] == "v" and value[1:].isdigit()


def _time_change(item: dict[str, Any], index: int) -> TimeChange:
    _reject_unknown(dict(item), {"measure", "beats", "beat_type"}, f"piece.yaml:time[{index}]")
    return TimeChange(int(item["measure"]), int(item["beats"]), int(item["beat_type"]))


def _key_change(item: dict[str, Any], index: int) -> KeyChange:
    _reject_unknown(dict(item), {"measure", "fifths", "mode"}, f"piece.yaml:key[{index}]")
    return KeyChange(int(item["measure"]), int(item["fifths"]), str(item["mode"]))


def validate_piece(piece: Piece) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    for change in piece.time_changes:
        if not 1 <= change.measure <= piece.measures or change.beats < 1:
            diagnostics.append(
                Diagnostic(
                    "error",
                    "piece.time_invalid",
                    "piece.yaml:time",
                    "Time changes must target a piece measure and have positive beats.",
                )
            )
    for change in piece.key_changes:
        if not 1 <= change.measure <= piece.measures or not -7 <= change.fifths <= 7:
            diagnostics.append(
                Diagnostic(
                    "error",
                    "piece.key_invalid",
                    "piece.yaml:key",
                    "Key changes must target a piece measure and use -7..7 fifths.",
                )
            )
        if change.mode not in {"major", "minor"}:
            diagnostics.append(
                Diagnostic(
                    "error", "piece.key_mode", "piece.yaml:key", "Mode must be major or minor."
                )
            )
    for change_index, change in enumerate(piece.tempo_changes):
        if not 1 <= change.measure <= piece.measures or change.bpm <= 0:
            diagnostics.append(
                Diagnostic(
                    "error",
                    "piece.tempo_invalid",
                    "piece.yaml:tempo",
                    "Tempo changes must target a piece measure and use a positive BPM.",
                )
            )
        elif change.beat > piece.time_at(change.measure).beats:
            diagnostics.append(
                Diagnostic(
                    "error",
                    "piece.tempo_beat",
                    "piece.yaml:tempo",
                    "Tempo anchor is outside the target measure.",
                )
            )
        elif change.ramp_end_measure is not None:
            if (
                change.ramp_end_beat is None
                or change.ramp_bpm is None
                or change.ramp_curve != "linear"
                or change.ramp_bpm <= 0
                or not 1 <= change.ramp_end_measure <= piece.measures
                or change.ramp_end_beat > piece.time_at(change.ramp_end_measure).beats
                or _anchor_whole(piece, change.ramp_end_measure, change.ramp_end_beat)
                <= _anchor_whole(piece, change.measure, change.beat)
            ):
                diagnostics.append(
                    Diagnostic(
                        "error",
                        "piece.tempo_ramp_invalid",
                        "piece.yaml:tempo",
                        (
                            "Tempo ramps require a later in-piece endpoint, positive BPM, "
                            "and linear curve."
                        ),
                    )
                )
            elif change_index + 1 < len(piece.tempo_changes):
                following = piece.tempo_changes[change_index + 1]
                ramp_end = _anchor_whole(
                    piece, change.ramp_end_measure, change.ramp_end_beat
                )
                following_start = _anchor_whole(piece, following.measure, following.beat)
                if ramp_end > following_start:
                    diagnostics.append(
                        Diagnostic(
                            "error",
                            "piece.tempo_ramp_overlap",
                            "piece.yaml:tempo",
                            "Tempo ramp overlaps the next authored tempo change.",
                        )
                    )
                elif ramp_end == following_start and change.ramp_bpm != following.bpm:
                    diagnostics.append(
                        Diagnostic(
                            "error",
                            "piece.tempo_ramp_endpoint_conflict",
                            "piece.yaml:tempo",
                            "Tempo at a ramp endpoint must match a change at the same anchor.",
                        )
                    )
    for part in piece.parts:
        profile = piece.profiles[part.profile_id]
        _validate_controls(piece, part, profile, diagnostics)
        declared_staves = {staff.number for staff in part.staves}
        active_ties: dict[str, set[int]] = {}
        active_slurs: dict[str, bool] = {}
        active_tuplets: dict[str, tuple[int, int] | None] = {}
        for number in part.measures:
            if not 1 <= number <= piece.measures:
                diagnostics.append(
                    Diagnostic(
                        "error",
                        "measure.out_of_piece",
                        f"{part.source_path}:measures[{number}]",
                        f"Measure must be between 1 and {piece.measures}.",
                    )
                )
        for number in range(1, piece.measures + 1):
            measure = part.measures.get(number)
            if measure is None:
                continue
            expected = piece.time_at(number).length
            for voice_name, events in measure.voices.items():
                tie_pitches = active_ties.setdefault(voice_name, set())
                slur_active = active_slurs.setdefault(voice_name, False)
                tuplet_active = active_tuplets.setdefault(voice_name, None)
                actual = sum((event.duration for event in events), start=expected * 0)
                if actual != expected:
                    diagnostics.append(
                        Diagnostic(
                            "error",
                            "measure.duration_mismatch",
                            f"{part.source_path}:measures[{number}].{voice_name}",
                            f"Voice totals {actual}; meter requires {expected}.",
                        )
                    )
                grace_steal = 0.0
                for event_index, event in enumerate(events):
                    event_path = (
                        f"{part.source_path}:measures[{number}].{voice_name}[{event_index}]"
                    )
                    if len(part.staves) > 1 and event.staff is None:
                        diagnostics.append(
                            Diagnostic(
                                "error",
                                "staff.required_multistaff",
                                event_path,
                                "Every event in a multi-staff part must declare staff.",
                            )
                        )
                    elif event.staff is not None and event.staff not in declared_staves:
                        diagnostics.append(
                            Diagnostic(
                                "error",
                                "staff.undefined",
                                event_path,
                                f"Staff {event.staff} is not declared by this part.",
                            )
                        )
                    event_midis = {pitch.midi for pitch in event.pitches}
                    if event.grace is None and (event.duration * 1920).denominator != 1:
                        diagnostics.append(
                            Diagnostic(
                                "error",
                                "notation.duration_unrepresentable",
                                event_path,
                                "Event duration cannot be represented exactly at 480 MIDI TPQ.",
                            )
                        )
                    if event.grace is not None:
                        grace_steal += event.grace.steal
                        following = next(
                            (
                                candidate
                                for candidate in events[event_index + 1 :]
                                if not candidate.grace
                            ),
                            None,
                        )
                        if following is None:
                            diagnostics.append(
                                Diagnostic(
                                    "error",
                                    "grace.no_following_note",
                                    event_path,
                                    (
                                        "A grace-note group must precede a measured note in "
                                        "the same voice and measure."
                                    ),
                                )
                            )
                        elif (
                            following.duration
                            * Fraction(str(event.grace.steal))
                            * 1920
                        ).denominator != 1:
                            diagnostics.append(
                                Diagnostic(
                                    "error",
                                    "grace.duration_unrepresentable",
                                    event_path,
                                    (
                                        "Grace steal duration cannot be represented exactly "
                                        "at 480 MIDI TPQ."
                                    ),
                                )
                            )
                        elif grace_steal > 0.5:
                            diagnostics.append(
                                Diagnostic(
                                    "error",
                                    "grace.steal_excessive",
                                    event_path,
                                    (
                                        "A grace-note group may steal at most half of its "
                                        "following note."
                                    ),
                                )
                            )
                    else:
                        grace_steal = 0.0
                    if event.slur == "start":
                        if slur_active:
                            diagnostics.append(
                                Diagnostic(
                                    "error",
                                    "slur.duplicate_start",
                                    event_path,
                                    "This minimal contract supports one active slur per voice.",
                                )
                            )
                        slur_active = True
                    elif event.slur in {"continue", "stop"} and not slur_active:
                        diagnostics.append(
                            Diagnostic(
                                "error",
                                "slur.without_start",
                                event_path,
                                "Slur continuation or stop has no active start in this voice.",
                            )
                        )
                    if event.slur == "stop":
                        slur_active = False
                    if event.tuplet is not None:
                        ratio = (event.tuplet.actual, event.tuplet.normal)
                        if event.tuplet.type == "start":
                            if tuplet_active is not None:
                                diagnostics.append(
                                    Diagnostic(
                                        "error",
                                        "tuplet.duplicate_start",
                                        event_path,
                                        "Nested tuplets are outside the supported contract.",
                                    )
                                )
                            tuplet_active = ratio
                        elif tuplet_active is None or tuplet_active != ratio:
                            diagnostics.append(
                                Diagnostic(
                                    "error",
                                    "tuplet.without_matching_start",
                                    event_path,
                                    "Tuplet continuation or stop has no matching active ratio.",
                                )
                            )
                        if event.tuplet.type == "stop":
                            tuplet_active = None
                    if event.tie in {"stop", "continue"} and not event_midis <= tie_pitches:
                        diagnostics.append(
                            Diagnostic(
                                "error",
                                "tie.stop_without_start",
                                event_path,
                                "A tie stop or continuation has no matching active tie.",
                            )
                        )
                    if event.tie == "start" and event_midis & tie_pitches:
                        diagnostics.append(
                            Diagnostic(
                                "error",
                                "tie.duplicate_start",
                                event_path,
                                "A tie starts while the same pitch is already tied.",
                            )
                        )
                    if event.tie == "start":
                        tie_pitches.update(event_midis)
                    elif event.tie == "stop":
                        tie_pitches.difference_update(event_midis)
                    if event.articulation and event.articulation not in profile.articulations:
                        diagnostics.append(
                            Diagnostic(
                                "error",
                                "instrument.articulation_unsupported",
                                event_path,
                                f"{profile.id} does not declare {event.articulation!r}.",
                            )
                        )
                    for pitch in event.pitches:
                        sounding = pitch.midi + profile.transposition
                        if not profile.absolute_low.midi <= sounding <= profile.absolute_high.midi:
                            diagnostics.append(
                                Diagnostic(
                                    "error",
                                    "instrument.range_absolute",
                                    event_path,
                                    f"{pitch} sounds outside {profile.name}'s absolute range.",
                                )
                            )
                        elif not (
                            profile.comfortable_low.midi
                            <= sounding
                            <= profile.comfortable_high.midi
                        ):
                            diagnostics.append(
                                Diagnostic(
                                    "warning",
                                    "instrument.range_comfortable",
                                    event_path,
                                    f"{pitch} is outside {profile.name}'s comfortable range.",
                                )
                            )
                active_slurs[voice_name] = slur_active
                active_tuplets[voice_name] = tuplet_active
        for voice_name, pitches in active_ties.items():
            if pitches:
                diagnostics.append(
                    Diagnostic(
                        "error",
                        "tie.unclosed",
                        str(part.source_path),
                        f"Voice {voice_name} ends with unclosed tied pitches: {sorted(pitches)}.",
                    )
                )
        for voice_name, active in active_slurs.items():
            if active:
                diagnostics.append(
                    Diagnostic(
                        "error",
                        "slur.unclosed",
                        str(part.source_path),
                        f"Voice {voice_name} ends with an unclosed slur.",
                    )
                )
        for voice_name, active in active_tuplets.items():
            if active is not None:
                diagnostics.append(
                    Diagnostic(
                        "error",
                        "tuplet.unclosed",
                        str(part.source_path),
                        f"Voice {voice_name} ends with an unclosed tuplet.",
                    )
                )
    return diagnostics


def _validate_controls(
    piece: Piece,
    part: Part,
    profile: InstrumentProfile,
    diagnostics: list[Diagnostic],
) -> None:
    pedal_down = False
    cc_at_anchor: dict[tuple[int, Fraction, int], int] = {}
    cc_points: list[tuple[Fraction, int, str]] = []
    dynamic_ranges: list[tuple[Fraction, Fraction, int, str]] = []
    indexed = sorted(
        enumerate(part.controls),
        key=lambda item: (item[1].measure, item[1].beat, item[0]),
    )
    for source_index, control in indexed:
        path = f"{part.source_path}:controls[{source_index}]"
        if not 1 <= control.measure <= piece.measures:
            diagnostics.append(
                Diagnostic("error", "control.measure", path, "Control targets no piece measure.")
            )
            continue
        time = piece.time_at(control.measure)
        if control.beat > time.beats:
            diagnostics.append(
                Diagnostic("error", "control.beat", path, "Control anchor is outside the measure.")
            )
            continue
        if control.kind == "cc":
            key = (control.measure, control.beat, int(control.controller))
            previous = cc_at_anchor.get(key)
            if previous is not None and previous != control.value:
                diagnostics.append(
                    Diagnostic(
                        "error",
                        "control.cc_conflict",
                        path,
                        "The same controller has conflicting values at one anchor.",
                    )
                )
            cc_at_anchor[key] = int(control.value)
            cc_points.append(
                (
                    _anchor_whole(piece, control.measure, control.beat),
                    int(control.controller),
                    path,
                )
            )
        elif control.kind == "pedal":
            if control.pedal_action == "down":
                if pedal_down:
                    diagnostics.append(
                        Diagnostic("error", "pedal.already_down", path, "Pedal is already down.")
                    )
                pedal_down = True
            elif control.pedal_action == "up":
                if not pedal_down:
                    diagnostics.append(
                        Diagnostic("error", "pedal.already_up", path, "Pedal is already up.")
                    )
                pedal_down = False
            elif not pedal_down:
                diagnostics.append(
                    Diagnostic(
                        "error",
                        "pedal.change_while_up",
                        path,
                        "Pedal change requires pedal down.",
                    )
                )
        elif control.kind == "keyswitch" and control.keyswitch not in profile.keyswitches:
            diagnostics.append(
                Diagnostic(
                    "error",
                    "instrument.keyswitch_unsupported",
                    path,
                    f"{profile.id} does not declare keyswitch {control.keyswitch!r}.",
                )
            )
        elif (
            control.kind == "performance"
            and control.performance_parameter not in profile.performance
        ):
            diagnostics.append(
                Diagnostic(
                    "error",
                    "instrument.performance_unsupported",
                    path,
                    (
                        f"{profile.id} does not declare performance parameter "
                        f"{control.performance_parameter!r}."
                    ),
                )
            )
        elif control.kind == "dynamic_ramp":
            if (
                control.end_measure is None
                or control.end_beat is None
                or not 1 <= control.end_measure <= piece.measures
                or control.end_beat > piece.time_at(control.end_measure).beats
                or _anchor_whole(piece, control.end_measure, control.end_beat)
                <= _anchor_whole(piece, control.measure, control.beat)
            ):
                diagnostics.append(
                    Diagnostic(
                        "error",
                        "dynamic_ramp.invalid_range",
                        path,
                        "A dynamic ramp must end after it starts at a valid piece anchor.",
                    )
                )
            else:
                dynamic_ranges.append(
                    (
                        _anchor_whole(piece, control.measure, control.beat),
                        _anchor_whole(piece, control.end_measure, control.end_beat),
                        int(control.controller),
                        path,
                    )
                )
    for index, (start, end, controller, path) in enumerate(dynamic_ranges):
        for other_start, other_end, other_controller, _ in dynamic_ranges[:index]:
            if controller == other_controller and start < other_end and other_start < end:
                diagnostics.append(
                    Diagnostic(
                        "error",
                        "dynamic_ramp.overlap",
                        path,
                        f"Dynamic ramps overlap on controller {controller}.",
                    )
                )
        if any(
            point_controller == controller and start <= point <= end
            for point, point_controller, _ in cc_points
        ):
            diagnostics.append(
                Diagnostic(
                    "error",
                    "dynamic_ramp.cc_conflict",
                    path,
                    f"A discrete CC conflicts with the ramp on controller {controller}.",
                )
            )
    if pedal_down:
        diagnostics.append(
            Diagnostic(
                "error",
                "pedal.unclosed",
                str(part.source_path),
                "Sustain pedal remains down at the end of the piece; author an explicit up event.",
            )
        )


def _anchor_whole(piece: Piece, measure: int, beat: Fraction) -> Fraction:
    cursor = Fraction(0)
    for number in range(1, measure):
        cursor += piece.time_at(number).length
    return cursor + (beat - 1) * Fraction(1, piece.time_at(measure).beat_type)
