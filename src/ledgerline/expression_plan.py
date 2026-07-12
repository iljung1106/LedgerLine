from __future__ import annotations

import json
import math
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

import yaml

from ledgerline.compiler_midi import WHOLE_TICKS, _articulation_gate
from ledgerline.diagnostics import Diagnostic, ValidationError
from ledgerline.model import DYNAMIC_VELOCITY, Event, Part, Piece
from ledgerline.timeline import Timeline

BACKENDS = {"legacy", "mpe", "clap-note-expression", "midi2"}
MPE_CHANNELS = tuple(channel for channel in range(1, 16) if channel != 9)


@dataclass(frozen=True, slots=True)
class PerformancePolicy:
    backend: str = "legacy"
    overlap: str = "error"
    pitch_bend_range: int = 2


def load_performance_policies(piece: Piece) -> dict[str, PerformancePolicy]:
    path = piece.root / "performance.yaml"
    if not path.is_file():
        return {part.id: PerformancePolicy() for part in piece.parts}
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or set(raw) - {"format", "parts"}:
        raise ValueError("performance.yaml requires only format and parts")
    if raw.get("format") != 1 or not isinstance(raw.get("parts"), dict):
        raise ValueError("performance.yaml format must be 1 and parts must be a mapping")
    unknown = set(raw["parts"]) - {part.id for part in piece.parts}
    if unknown:
        raise ValueError(f"performance.yaml has unknown parts: {', '.join(sorted(unknown))}")
    policies: dict[str, PerformancePolicy] = {}
    for part in piece.parts:
        value = raw["parts"].get(part.id, {})
        if not isinstance(value, dict) or set(value) - {
            "backend",
            "overlap",
            "pitch_bend_range",
        }:
            raise ValueError(f"performance.yaml part {part.id!r} is invalid")
        backend = str(value.get("backend", "legacy"))
        overlap = str(value.get("overlap", "error"))
        bend = value.get("pitch_bend_range", 2)
        if backend not in BACKENDS:
            raise ValueError(f"unsupported expression backend: {backend}")
        if overlap not in {"error", "allow"}:
            raise ValueError("overlap must be error or allow")
        if isinstance(bend, bool) or not isinstance(bend, int) or not 1 <= bend <= 96:
            raise ValueError("pitch_bend_range must be an integer from 1 to 96")
        policies[part.id] = PerformancePolicy(backend, overlap, bend)
    return policies


def build_expression_plan(piece: Piece, *, sample_rate: int = 48_000) -> dict[str, Any]:
    timeline = Timeline(piece, sample_rate)
    policies = load_performance_policies(piece)
    parts: dict[str, Any] = {}
    diagnostics: list[Diagnostic] = []
    for part in piece.parts:
        policy = policies[part.id]
        notes = _part_notes(piece, part, timeline)
        expressive = [note for note in notes if note["expression"]]
        overlaps = _expression_overlaps(expressive)
        if overlaps and policy.backend == "legacy":
            diagnostics.append(
                Diagnostic(
                    "error",
                    "expression.channel_conflict",
                    f"performance.yaml:parts.{part.id}",
                    "Per-note expression overlaps on a channel-wide MIDI backend: "
                    + ", ".join(f"{a}/{b}" for a, b in overlaps[:8]),
                )
            )
        if overlaps and policy.overlap == "error" and policy.backend != "legacy":
            diagnostics.append(
                Diagnostic(
                    "error",
                    "expression.overlap_forbidden",
                    f"performance.yaml:parts.{part.id}",
                    "Expression overlap policy is error: "
                    + ", ".join(f"{a}/{b}" for a, b in overlaps[:8]),
                )
            )
        if policy.backend == "mpe":
            _assign_mpe_channels(notes, diagnostics, part.id)
        parts[part.id] = {
            "backend": policy.backend,
            "pitch_bend_range": policy.pitch_bend_range,
            "note_count": len(notes),
            "expressive_note_count": len(expressive),
            "overlap_pairs": [{"left": a, "right": b} for a, b in overlaps],
            "notes": notes,
        }
    if diagnostics:
        raise ValidationError("per-note expression cannot be represented safely", diagnostics)
    return {
        "schema_version": "1",
        "status": "ok",
        "sample_rate": sample_rate,
        "parts": parts,
        "capability_contract": {
            "legacy": "channel-wide MIDI 1.0; overlapping expression is rejected",
            "mpe": "one MIDI channel per simultaneous note, zone channels 2-16 excluding 10",
            "clap-note-expression": "stable note IDs and normalized pressure/timbre events",
            "midi2": "lossless MIDI 2.0 event plan; transport adapter must declare UMP support",
        },
    }


def write_expression_plan(piece: Piece, output: Path, *, sample_rate: int = 48_000) -> dict:
    report = build_expression_plan(piece, sample_rate=sample_rate)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def _part_notes(piece: Piece, part: Part, timeline: Timeline) -> list[dict[str, Any]]:
    profile = piece.profiles[part.profile_id]
    starts = timeline.measure_starts
    result: list[dict[str, Any]] = []
    dynamics: dict[str, int] = {}
    active_ties: dict[tuple[str, int], dict[str, Any]] = {}
    ordinal = 0
    for measure_number in range(1, piece.measures + 1):
        measure = part.measures.get(measure_number)
        if measure is None:
            continue
        for voice, events in sorted(measure.voices.items(), key=lambda item: int(item[0][1:])):
            cursor = starts[measure_number]
            velocity = dynamics.get(voice, DYNAMIC_VELOCITY["mf"])
            for event_index, event in enumerate(events):
                if event.dynamic:
                    velocity = DYNAMIC_VELOCITY[event.dynamic]
                    dynamics[voice] = velocity
                if event.is_rest:
                    cursor += event.duration
                    continue
                duration = event.duration
                gate = _articulation_gate(event)
                end = cursor + duration * Fraction(str(gate))
                expression = _expand_expression(event, timeline, cursor, duration)
                for chord_index, pitch in enumerate(event.pitches):
                    sounding = pitch.midi + profile.transposition
                    tie_key = (voice, sounding)
                    if event.tie in {"stop", "continue"} and tie_key in active_ties:
                        tied = active_ties[tie_key]
                        tied_end = cursor + duration
                        tied["end_tick"] = round(tied_end * WHOLE_TICKS)
                        tied["end_sample"] = round(
                            timeline.seconds_at_whole(tied_end) * timeline.sample_rate
                        )
                        if event.tie == "stop":
                            active_ties.pop(tie_key)
                        continue
                    ordinal += 1
                    note_id = (
                        f"{part.id}:{measure_number}:{voice}:{event_index}:{chord_index}:{ordinal}"
                    )
                    start_seconds = timeline.seconds_at_whole(cursor)
                    end_seconds = timeline.seconds_at_whole(min(end, timeline.end_whole))
                    result.append(
                        {
                            "note_id": note_id,
                            "measure": measure_number,
                            "voice": voice,
                            "pitch": sounding,
                            "velocity": event.velocity or velocity,
                            "start_tick": round(cursor * WHOLE_TICKS),
                            "end_tick": round(end * WHOLE_TICKS),
                            "start_sample": round(start_seconds * timeline.sample_rate),
                            "end_sample": round(end_seconds * timeline.sample_rate),
                            "expression": expression,
                        }
                    )
                    if event.tie in {"start", "continue"}:
                        active_ties[tie_key] = result[-1]
                cursor += duration
    return sorted(result, key=lambda note: (note["start_tick"], note["pitch"], note["note_id"]))


def _expand_expression(
    event: Event, timeline: Timeline, start: Fraction, duration: Fraction
) -> list[dict[str, Any]]:
    points = [(point.parameter, point.position, point.value) for point in event.expression]
    seconds = timeline.seconds_at_whole(start + duration) - timeline.seconds_at_whole(start)
    for gesture in event.gestures:
        if gesture.type == "nonghyeon":
            cycles = max(0.25, gesture.rate_hz * seconds)
            count = min(64, max(8, round(cycles * 8)))
            points.extend(
                (
                    "pitch",
                    index / count,
                    gesture.depth_cents * math.sin(2 * math.pi * cycles * index / count),
                )
                for index in range(count + 1)
            )
        elif gesture.type in {"chuseong", "toeseong"}:
            sign = 1.0 if gesture.type == "chuseong" else -1.0
            points.extend(
                [
                    ("pitch", 0.0, 0.0),
                    ("pitch", gesture.position, 0.0),
                    ("pitch", 1.0, sign * gesture.depth_cents),
                ]
            )
        elif gesture.type == "breath":
            points.append(("pressure", 0.0, 1.0 - gesture.amount))
        elif gesture.type == "pluck_position":
            points.append(("timbre", 0.0, gesture.amount))
    if event.pitch_cents and not any(parameter == "pitch" for parameter, _, _ in points):
        points.append(("pitch", 0.0, 0.0))
    return [
        {
            "parameter": parameter,
            "position": position,
            "value": value + event.pitch_cents if parameter == "pitch" else value,
        }
        for parameter, position, value in sorted(points, key=lambda item: (item[1], item[0]))
    ]


def _expression_overlaps(notes: list[dict[str, Any]]) -> list[tuple[str, str]]:
    overlaps = []
    active: list[dict[str, Any]] = []
    for note in sorted(notes, key=lambda item: (item["start_tick"], item["end_tick"])):
        active = [item for item in active if item["end_tick"] > note["start_tick"]]
        overlaps.extend((item["note_id"], note["note_id"]) for item in active)
        active.append(note)
    return overlaps


def _assign_mpe_channels(
    notes: list[dict[str, Any]], diagnostics: list[Diagnostic], part_id: str
) -> None:
    active: list[tuple[int, int]] = []
    for note in notes:
        active = [(end, channel) for end, channel in active if end > note["start_tick"]]
        used = {channel for _, channel in active}
        available = next((channel for channel in MPE_CHANNELS if channel not in used), None)
        if available is None:
            diagnostics.append(
                Diagnostic(
                    "error",
                    "expression.mpe_voice_exhausted",
                    f"parts.{part_id}",
                    (
                        f"More than {len(MPE_CHANNELS)} simultaneous notes "
                        f"at tick {note['start_tick']}."
                    ),
                )
            )
            return
        note["mpe_channel"] = available
        active.append((note["end_tick"], available))
