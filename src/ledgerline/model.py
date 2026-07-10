from __future__ import annotations

import re
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any

PITCH_RE = re.compile(r"^([A-Ga-g])([#b]{0,2})(-?\d+)$")
DURATION_RE = re.compile(r"^1/(1|2|4|8|16|32)(\.{0,2})$")
ANCHOR_RE = re.compile(r"^(\d+):(\d+(?:\.\d+)?)$")

STEP_TO_SEMITONE = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
ALTER = {"": 0, "#": 1, "##": 2, "b": -1, "bb": -2}
DYNAMIC_VELOCITY = {
    "ppp": 28,
    "pp": 38,
    "p": 50,
    "mp": 62,
    "mf": 76,
    "f": 92,
    "ff": 108,
    "fff": 120,
}
SUPPORTED_ARTICULATIONS = {"staccato", "tenuto", "accent", "marcato"}
SUPPORTED_TIES = {"start", "stop", "continue"}


@dataclass(frozen=True, slots=True)
class Pitch:
    step: str
    alter: int
    octave: int

    @property
    def midi(self) -> int:
        return (self.octave + 1) * 12 + STEP_TO_SEMITONE[self.step] + self.alter

    @property
    def accidental(self) -> str:
        return {0: "", 1: "#", 2: "##", -1: "b", -2: "bb"}[self.alter]

    def __str__(self) -> str:
        return f"{self.step}{self.accidental}{self.octave}"


def parse_pitch(value: str) -> Pitch:
    match = PITCH_RE.fullmatch(value.strip())
    if not match:
        raise ValueError(f"invalid scientific pitch: {value!r}")
    step, accidental, octave = match.groups()
    pitch = Pitch(step.upper(), ALTER[accidental], int(octave))
    if not 0 <= pitch.midi <= 127:
        raise ValueError(f"pitch outside MIDI range: {value!r}")
    return pitch


def parse_duration(value: str) -> Fraction:
    match = DURATION_RE.fullmatch(str(value).strip())
    if not match:
        raise ValueError(f"invalid duration: {value!r}")
    denominator = int(match.group(1))
    dots = len(match.group(2))
    base = Fraction(1, denominator)
    if dots == 1:
        return base * Fraction(3, 2)
    if dots == 2:
        return base * Fraction(7, 4)
    return base


def duration_token(value: Fraction) -> tuple[str, int]:
    for denominator in (1, 2, 4, 8, 16, 32):
        base = Fraction(1, denominator)
        if value == base:
            return _note_type(denominator), 0
        if value == base * Fraction(3, 2):
            return _note_type(denominator), 1
        if value == base * Fraction(7, 4):
            return _note_type(denominator), 2
    raise ValueError(f"duration has no v1 notation token: {value}")


def _note_type(denominator: int) -> str:
    return {1: "whole", 2: "half", 4: "quarter", 8: "eighth", 16: "16th", 32: "32nd"}[denominator]


@dataclass(frozen=True, slots=True)
class Event:
    duration: Fraction
    pitches: tuple[Pitch, ...] = ()
    dynamic: str | None = None
    articulation: str | None = None
    tie: str | None = None
    velocity: int | None = None
    staff: int | None = None

    @property
    def is_rest(self) -> bool:
        return not self.pitches


@dataclass(frozen=True, slots=True)
class Measure:
    number: int
    voices: dict[str, tuple[Event, ...]]


@dataclass(frozen=True, slots=True)
class ControlEvent:
    measure: int
    beat: Fraction
    kind: str
    controller: int | None = None
    value: int | None = None
    pedal_action: str | None = None
    keyswitch: str | None = None
    velocity: int = 64
    duration: Fraction = Fraction(1, 32)


@dataclass(frozen=True, slots=True)
class StaffDefinition:
    number: int
    name: str
    clef_sign: str
    clef_line: int


@dataclass(frozen=True, slots=True)
class Part:
    id: str
    name: str
    profile_id: str
    source_path: Path
    measures: dict[int, Measure]
    controls: tuple[ControlEvent, ...] = ()
    staves: tuple[StaffDefinition, ...] = ()


@dataclass(frozen=True, slots=True)
class TimeChange:
    measure: int
    beats: int
    beat_type: int

    @property
    def length(self) -> Fraction:
        return Fraction(self.beats, self.beat_type)


@dataclass(frozen=True, slots=True)
class TempoChange:
    measure: int
    beat: Fraction
    bpm: float


@dataclass(frozen=True, slots=True)
class KeyChange:
    measure: int
    fifths: int
    mode: str


@dataclass(frozen=True, slots=True)
class InstrumentProfile:
    id: str
    name: str
    family: str
    absolute_low: Pitch
    absolute_high: Pitch
    comfortable_low: Pitch
    comfortable_high: Pitch
    transposition: int
    bank_msb: int
    bank_lsb: int
    program: int
    clef_sign: str = "G"
    clef_line: int = 2
    articulations: frozenset[str] = frozenset(SUPPORTED_ARTICULATIONS)
    keyswitches: dict[str, Pitch] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Piece:
    root: Path
    title: str
    measures: int
    time_changes: tuple[TimeChange, ...]
    tempo_changes: tuple[TempoChange, ...]
    key_changes: tuple[KeyChange, ...]
    parts: tuple[Part, ...]
    profiles: dict[str, InstrumentProfile] = field(repr=False)

    def time_at(self, measure: int) -> TimeChange:
        candidates = [change for change in self.time_changes if change.measure <= measure]
        return candidates[-1]

    def key_at(self, measure: int) -> KeyChange:
        candidates = [change for change in self.key_changes if change.measure <= measure]
        return candidates[-1]


def event_from_dict(data: dict[str, Any]) -> Event:
    unknown = sorted(set(data) - {"p", "r", "d", "dyn", "art", "tie", "vel", "staff"})
    if unknown:
        raise ValueError(f"unknown event fields: {', '.join(unknown)}")
    duration = parse_duration(str(data["d"]))
    rest = data.get("r", False)
    raw_pitch = data.get("p")
    if rest and raw_pitch is not None:
        raise ValueError("an event cannot contain both 'r' and 'p'")
    if not rest and raw_pitch is None:
        raise ValueError("an event requires 'p' or r: true")
    if isinstance(raw_pitch, str):
        pitches = (parse_pitch(raw_pitch),)
    elif isinstance(raw_pitch, list) and raw_pitch:
        pitches = tuple(parse_pitch(str(item)) for item in raw_pitch)
    elif rest:
        pitches = ()
    else:
        raise ValueError("'p' must be a pitch string or a non-empty pitch list")
    dynamic = data.get("dyn")
    if dynamic is not None and dynamic not in DYNAMIC_VELOCITY:
        raise ValueError(f"unsupported dynamic: {dynamic!r}")
    articulation = data.get("art")
    if articulation is not None and articulation not in SUPPORTED_ARTICULATIONS:
        raise ValueError(f"unsupported articulation: {articulation!r}")
    tie = data.get("tie")
    if tie is not None and tie not in SUPPORTED_TIES:
        raise ValueError(f"unsupported tie value: {tie!r}")
    velocity = data.get("vel")
    if velocity is not None:
        velocity = int(velocity)
        if not 1 <= velocity <= 127:
            raise ValueError("velocity must be between 1 and 127")
    staff = data.get("staff")
    if staff is not None and (
        isinstance(staff, bool) or not isinstance(staff, int) or staff < 1
    ):
        raise ValueError("staff must be a positive integer")
    return Event(
        duration=duration,
        pitches=pitches,
        dynamic=dynamic,
        articulation=articulation,
        tie=tie,
        velocity=velocity,
        staff=staff,
    )


def control_event_from_dict(data: dict[str, Any]) -> ControlEvent:
    try:
        measure, beat = parse_anchor(str(data["at"]))
        kind = str(data["type"])
    except KeyError as exc:
        raise ValueError(f"control requires {exc.args[0]!r}") from exc

    if kind == "cc":
        _reject_control_fields(data, {"at", "type", "controller", "value"})
        controller = _control_int(data, "controller")
        value = _control_int(data, "value")
        if controller in {0, 32}:
            raise ValueError("CC 0 and 32 are reserved for the instrument profile's bank select")
        if controller == 64:
            raise ValueError("use type: pedal instead of raw CC 64")
        return ControlEvent(measure, beat, kind, controller=controller, value=value)

    if kind == "pedal":
        _reject_control_fields(data, {"at", "type", "action"})
        action = str(data.get("action", ""))
        if action not in {"down", "up", "change"}:
            raise ValueError("pedal action must be down, up, or change")
        return ControlEvent(measure, beat, kind, pedal_action=action)

    if kind == "keyswitch":
        _reject_control_fields(data, {"at", "type", "name", "velocity", "duration"})
        raw_name = data.get("name")
        if not isinstance(raw_name, str) or not raw_name.strip():
            raise ValueError("keyswitch name must be a non-empty string")
        name = raw_name.strip()
        velocity = _control_int(data, "velocity", default=64, minimum=1)
        duration = parse_duration(str(data.get("duration", "1/32")))
        return ControlEvent(
            measure,
            beat,
            kind,
            keyswitch=name,
            velocity=velocity,
            duration=duration,
        )

    raise ValueError(f"unsupported control type: {kind!r}")


def _reject_control_fields(data: dict[str, Any], allowed: set[str]) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ValueError(f"unknown {data.get('type', 'control')} fields: {', '.join(unknown)}")


def _control_int(
    data: dict[str, Any],
    field_name: str,
    *,
    default: int | None = None,
    minimum: int = 0,
) -> int:
    raw = data.get(field_name, default)
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ValueError(f"{field_name} must be an integer")
    if not minimum <= raw <= 127:
        raise ValueError(f"{field_name} must be between {minimum} and 127")
    return raw


def parse_anchor(value: str) -> tuple[int, Fraction]:
    match = ANCHOR_RE.fullmatch(value)
    if not match:
        raise ValueError(f"invalid measure:beat anchor: {value!r}")
    measure = int(match.group(1))
    beat = Fraction(match.group(2))
    if measure < 1 or beat < 1:
        raise ValueError(f"anchor values must be positive: {value!r}")
    return measure, beat
