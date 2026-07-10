from __future__ import annotations

from fractions import Fraction
from pathlib import Path
from typing import Any

import yaml

from ledgerline.diagnostics import Diagnostic, ValidationError
from ledgerline.model import (
    ControlEvent,
    InstrumentProfile,
    KeyChange,
    Measure,
    Part,
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
            articulations=frozenset(data.get("articulations", [])),
            keyswitches=keyswitches,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValidationError(
            f"invalid profile: {path}",
            [Diagnostic("error", "profile.invalid", str(path), str(exc))],
        ) from exc


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
            _reject_unknown(dict(item), {"at", "bpm"}, f"piece.yaml:tempo[{index}]")
            measure, beat = parse_anchor(str(item["at"]))
            tempo_changes.append(TempoChange(measure, beat, float(item["bpm"])))
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
    diagnostics.extend(validate_piece(piece))
    if any(item.severity == "error" for item in diagnostics):
        raise ValidationError("project validation failed", diagnostics)
    return piece


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
        staves = (
            StaffDefinition(1, "staff-1", profile.clef_sign, profile.clef_line),
        )

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
    for change in piece.tempo_changes:
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
    for part in piece.parts:
        profile = piece.profiles[part.profile_id]
        _validate_controls(piece, part, profile, diagnostics)
        declared_staves = {staff.number for staff in part.staves}
        active_ties: dict[str, set[int]] = {}
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
                    if event.tie in {"stop", "continue"} and not event_midis <= tie_pitches:
                        diagnostics.append(
                            Diagnostic(
                                "error",
                                "tie.stop_without_start",
                                f"{part.source_path}:measures[{number}].{voice_name}[{event_index}]",
                                "A tie stop or continuation has no matching active tie.",
                            )
                        )
                    if event.tie == "start" and event_midis & tie_pitches:
                        diagnostics.append(
                            Diagnostic(
                                "error",
                                "tie.duplicate_start",
                                f"{part.source_path}:measures[{number}].{voice_name}[{event_index}]",
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
                                f"{part.source_path}:measures[{number}].{voice_name}[{event_index}]",
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
                                    f"{part.source_path}:measures[{number}].{voice_name}[{event_index}]",
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
                                    f"{part.source_path}:measures[{number}].{voice_name}[{event_index}]",
                                    f"{pitch} is outside {profile.name}'s comfortable range.",
                                )
                            )
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
    return diagnostics


def _validate_controls(
    piece: Piece,
    part: Part,
    profile: InstrumentProfile,
    diagnostics: list[Diagnostic],
) -> None:
    pedal_down = False
    cc_at_anchor: dict[tuple[int, Fraction, int], int] = {}
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
    if pedal_down:
        diagnostics.append(
            Diagnostic(
                "error",
                "pedal.unclosed",
                str(part.source_path),
                "Sustain pedal remains down at the end of the piece; author an explicit up event.",
            )
        )
