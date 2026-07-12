from __future__ import annotations

from dataclasses import replace
from fractions import Fraction
from typing import Any

import yaml

from ledgerline.diagnostics import Diagnostic, ValidationError
from ledgerline.model import (
    Event,
    Measure,
    Piece,
    Pitch,
    event_from_dict,
    parse_duration,
    parse_pitch,
)


def apply_project_motifs(piece: Piece) -> Piece:
    path = piece.root / "motifs.yaml"
    if not path.is_file():
        return piece
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("motifs root must be a mapping")
        _unknown(data, {"format", "motifs", "placements"}, "motifs.yaml")
        if data.get("format") != 1:
            raise ValueError("motifs format must be 1")
        raw_motifs = data.get("motifs")
        raw_placements = data.get("placements")
        if not isinstance(raw_motifs, dict) or not raw_motifs:
            raise ValueError("motifs must be a non-empty mapping")
        if not isinstance(raw_placements, list) or not raw_placements:
            raise ValueError("placements must be a non-empty list")
        motifs = {
            str(name): _motif(value, f"motifs.yaml.motifs.{name}")
            for name, value in raw_motifs.items()
        }
        return _apply_placements(piece, motifs, raw_placements)
    except (OSError, yaml.YAMLError, TypeError, ValueError, KeyError) as exc:
        raise ValidationError(
            "motifs.yaml is invalid",
            [Diagnostic("error", "motifs.invalid", str(path), str(exc))],
        ) from exc


def _motif(raw: Any, path: str) -> tuple[Event, ...]:
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must be a mapping")
    _unknown(raw, {"events"}, path)
    raw_events = raw.get("events")
    if not isinstance(raw_events, list) or not raw_events:
        raise ValueError(f"{path}.events must be a non-empty list")
    return tuple(event_from_dict(dict(item)) for item in raw_events)


def _apply_placements(
    piece: Piece,
    motifs: dict[str, tuple[Event, ...]],
    raw_placements: list[Any],
) -> Piece:
    parts = {part.id: part for part in piece.parts}
    reports: list[dict[str, Any]] = []
    touched: set[tuple[str, int, str]] = set()
    for index, raw in enumerate(raw_placements):
        path = f"motifs.yaml.placements[{index}]"
        if not isinstance(raw, dict):
            raise ValueError(f"{path} must be a mapping")
        _unknown(raw, {"motif", "part", "measure", "voice", "mode", "transform"}, path)
        motif_id = str(raw["motif"])
        part_id = str(raw["part"])
        measure_number = _positive_integer(raw["measure"], f"{path}.measure")
        voice = str(raw.get("voice", "v1"))
        mode = str(raw.get("mode", "replace"))
        if motif_id not in motifs:
            raise ValueError(f"{path} references unknown motif {motif_id!r}")
        if part_id not in parts:
            raise ValueError(f"{path} references unknown part {part_id!r}")
        if not 1 <= measure_number <= piece.measures:
            raise ValueError(f"{path}.measure is outside the piece")
        if mode not in {"replace", "append"}:
            raise ValueError(f"{path}.mode must be replace or append")
        key = (part_id, measure_number, voice)
        if key in touched:
            raise ValueError(f"{path} duplicates target {part_id}:{measure_number}:{voice}")
        touched.add(key)
        transforms = raw.get("transform", [])
        if not isinstance(transforms, list):
            raise ValueError(f"{path}.transform must be a list")
        events = motifs[motif_id]
        applied = []
        for transform_index, transform in enumerate(transforms):
            transform_path = f"{path}.transform[{transform_index}]"
            events, description = _transform(events, transform, transform_path)
            applied.append(description)
        part = parts[part_id]
        measures = dict(part.measures)
        old_measure = measures.get(measure_number, Measure(measure_number, {}))
        voices = dict(old_measure.voices)
        previous = voices.get(voice, ())
        voices[voice] = (*previous, *events) if mode == "append" else events
        measures[measure_number] = Measure(measure_number, voices)
        parts[part_id] = replace(part, measures=measures)
        reports.append(
            {
                "motif": motif_id,
                "part": part_id,
                "measure": measure_number,
                "voice": voice,
                "mode": mode,
                "transformations": applied,
                "expanded_events": [_event_dict(event) for event in events],
            }
        )
    return replace(
        piece,
        parts=tuple(parts[part.id] for part in piece.parts),
        motif_expansions=tuple(reports),
    )


def _transform(
    events: tuple[Event, ...], raw: Any, path: str
) -> tuple[tuple[Event, ...], dict[str, Any]]:
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must be a mapping")
    kind = raw.get("type")
    if kind == "transpose":
        _unknown(raw, {"type", "semitones"}, path)
        semitones = _integer(raw.get("semitones"), f"{path}.semitones")
        return tuple(
            _map_pitches(event, lambda pitch: _transpose(pitch, semitones)) for event in events
        ), dict(raw)
    if kind == "invert":
        _unknown(raw, {"type", "axis"}, path)
        axis = parse_pitch(str(raw["axis"]))
        return tuple(
            _map_pitches(event, lambda pitch: _invert(pitch, axis)) for event in events
        ), dict(raw)
    if kind == "retrograde":
        _unknown(raw, {"type"}, path)
        return tuple(reversed(events)), dict(raw)
    if kind in {"augment", "diminish"}:
        _unknown(raw, {"type", "factor"}, path)
        factor = Fraction(str(raw.get("factor", "2")))
        if factor <= 0:
            raise ValueError(f"{path}.factor must be positive")
        if kind == "diminish":
            factor = 1 / factor
        transformed = tuple(replace(event, duration=event.duration * factor) for event in events)
        for event in transformed:
            _duration_is_notatable(event.duration, path)
        return transformed, dict(raw)
    if kind == "rhythm":
        _unknown(raw, {"type", "durations"}, path)
        durations = raw.get("durations")
        if not isinstance(durations, list) or len(durations) != len(events):
            raise ValueError(f"{path}.durations must match the motif event count")
        return (
            tuple(
                replace(event, duration=parse_duration(str(duration)))
                for event, duration in zip(events, durations, strict=True)
            ),
            dict(raw),
        )
    raise ValueError(f"{path}.type is unsupported: {kind!r}")


def _map_pitches(event: Event, operation) -> Event:
    return replace(event, pitches=tuple(operation(pitch) for pitch in event.pitches))


def _transpose(pitch: Pitch, semitones: int) -> Pitch:
    return _pitch_from_midi(pitch.midi + semitones)


def _invert(pitch: Pitch, axis: Pitch) -> Pitch:
    return _pitch_from_midi(2 * axis.midi - pitch.midi)


def _pitch_from_midi(midi: int) -> Pitch:
    if not 0 <= midi <= 127:
        raise ValueError(f"transformed pitch is outside MIDI range: {midi}")
    names = (
        ("C", 0),
        ("C", 1),
        ("D", 0),
        ("D", 1),
        ("E", 0),
        ("F", 0),
        ("F", 1),
        ("G", 0),
        ("G", 1),
        ("A", 0),
        ("A", 1),
        ("B", 0),
    )
    step, alter = names[midi % 12]
    return Pitch(step, alter, midi // 12 - 1)


def _duration_is_notatable(value: Fraction, path: str) -> None:
    allowed = {
        Fraction(1, denominator) * multiplier
        for denominator in (1, 2, 4, 8, 16, 32)
        for multiplier in (Fraction(1), Fraction(3, 2), Fraction(7, 4))
    }
    if value not in allowed:
        raise ValueError(f"{path} creates unnotatable duration {value}")


def _event_dict(event: Event) -> dict[str, Any]:
    result: dict[str, Any] = {"d": str(event.duration)}
    if event.is_rest:
        result["r"] = True
    else:
        result["p"] = [str(pitch) for pitch in event.pitches]
    for key, value in (
        ("dyn", event.dynamic),
        ("art", event.articulation),
        ("tie", event.tie),
        ("vel", event.velocity),
        ("staff", event.staff),
    ):
        if value is not None:
            result[key] = value
    return result


def _integer(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{path} must be an integer")
    return value


def _positive_integer(value: Any, path: str) -> int:
    result = _integer(value, path)
    if result < 1:
        raise ValueError(f"{path} must be positive")
    return result


def _unknown(data: dict[str, Any], allowed: set[str], path: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ValueError(f"{path} has unknown fields: {', '.join(unknown)}")
