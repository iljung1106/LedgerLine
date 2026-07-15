from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any

PITCH_RE = re.compile(r"^([A-Ga-g])([#b]{0,2})(-?\d+)$")
DURATION_RE = re.compile(r"^1/(1|2|4|8|16|32)(\.{0,2})$")
ANCHOR_RE = re.compile(r"^(\d+):(\d+(?:\.\d+)?)$")
AUTHORED_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{2,127}$")

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
SUPPORTED_SLURS = {"start", "stop", "continue"}
ARTICULATION_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
MUSICXML_ARTICULATIONS = {
    "accent",
    "breath-mark",
    "caesura",
    "detached-legato",
    "doit",
    "falloff",
    "other-articulation",
    "plop",
    "scoop",
    "soft-accent",
    "spiccato",
    "staccatissimo",
    "staccato",
    "stress",
    "strong-accent",
    "tenuto",
    "unstress",
}


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
class Tuplet:
    actual: int
    normal: int
    type: str


@dataclass(frozen=True, slots=True)
class Grace:
    kind: str
    steal: float


@dataclass(frozen=True, slots=True)
class Event:
    duration: Fraction
    written_duration: Fraction | None = None
    pitches: tuple[Pitch, ...] = ()
    dynamic: str | None = None
    articulation: str | None = None
    tie: str | None = None
    velocity: int | None = None
    staff: int | None = None
    pitch_cents: float = 0.0
    expression: tuple[ExpressionPoint, ...] = ()
    gestures: tuple[PerformanceGesture, ...] = ()
    id: str | None = None
    tuplet: Tuplet | None = None
    grace: Grace | None = None
    slur: str | None = None

    @property
    def is_rest(self) -> bool:
        return not self.pitches

    @property
    def notation_duration(self) -> Fraction:
        return self.written_duration or self.duration


@dataclass(frozen=True, slots=True)
class Measure:
    number: int
    voices: dict[str, tuple[Event, ...]]


@dataclass(frozen=True, slots=True)
class ExpressionPoint:
    parameter: str
    position: float
    value: float


@dataclass(frozen=True, slots=True)
class PerformanceGesture:
    type: str
    depth_cents: float = 0.0
    rate_hz: float = 0.0
    position: float = 0.5
    amount: float = 0.5


@dataclass(frozen=True, slots=True)
class ControlEvent:
    measure: int
    beat: Fraction
    kind: str
    controller: int | None = None
    value: int | None = None
    pedal_action: str | None = None
    keyswitch: str | None = None
    performance_parameter: str | None = None
    performance_value: float | None = None
    velocity: int = 64
    duration: Fraction = Fraction(1, 32)
    id: str | None = None
    end_measure: int | None = None
    end_beat: Fraction | None = None
    start_dynamic: str | None = None
    end_dynamic: str | None = None


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
    ramp_end_measure: int | None = None
    ramp_end_beat: Fraction | None = None
    ramp_bpm: float | None = None
    ramp_curve: str | None = None


@dataclass(frozen=True, slots=True)
class ArticulationDefinition:
    id: str
    musicxml: str
    label: str | None = None
    gate: float = 0.9
    velocity_delta: int = 0


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
    articulation_definitions: dict[str, ArticulationDefinition] = field(default_factory=dict)
    keyswitches: dict[str, Pitch] = field(default_factory=dict)
    performance: dict[str, PerformanceBinding] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PerformanceBinding:
    type: str
    controller: int | None = None
    parameter: str | None = None
    minimum: float = 0.0
    maximum: float = 127.0
    default: float = 0.5


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
    motif_expansions: tuple[dict[str, Any], ...] = field(default=(), repr=False)

    def time_at(self, measure: int) -> TimeChange:
        candidates = [change for change in self.time_changes if change.measure <= measure]
        return candidates[-1]

    def key_at(self, measure: int) -> KeyChange:
        candidates = [change for change in self.key_changes if change.measure <= measure]
        return candidates[-1]


def event_from_dict(data: dict[str, Any]) -> Event:
    unknown = sorted(
        set(data)
        - {
            "id",
            "p",
            "r",
            "d",
            "dyn",
            "art",
            "tie",
            "vel",
            "staff",
            "expr",
            "tuplet",
            "grace",
            "slur",
        }
    )
    if unknown:
        raise ValueError(f"unknown event fields: {', '.join(unknown)}")
    written_duration = parse_duration(str(data["d"]))
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
    if articulation is not None and (
        not isinstance(articulation, str) or not ARTICULATION_ID_RE.fullmatch(articulation)
    ):
        raise ValueError(f"invalid articulation id: {articulation!r}")
    tie = data.get("tie")
    if tie is not None and tie not in SUPPORTED_TIES:
        raise ValueError(f"unsupported tie value: {tie!r}")
    velocity = data.get("vel")
    if velocity is not None:
        velocity = int(velocity)
        if not 1 <= velocity <= 127:
            raise ValueError("velocity must be between 1 and 127")
    staff = data.get("staff")
    if staff is not None and (isinstance(staff, bool) or not isinstance(staff, int) or staff < 1):
        raise ValueError("staff must be a positive integer")
    pitch_cents, expression, gestures = _expression_from_dict(data.get("expr"))
    event_id = _optional_authored_id(data.get("id"), "event id")
    tuplet = _tuplet_from_dict(data.get("tuplet"))
    grace = _grace_from_dict(data.get("grace"))
    slur = data.get("slur")
    if slur is not None and slur not in SUPPORTED_SLURS:
        raise ValueError(f"unsupported slur value: {slur!r}")
    if grace is not None and (rest or tie is not None or tuplet is not None):
        raise ValueError("grace notes must be pitched and cannot also be tied or tuplets")
    if slur is not None and rest:
        raise ValueError("rests cannot carry slurs")
    duration = Fraction(0) if grace is not None else written_duration
    if tuplet is not None:
        duration *= Fraction(tuplet.normal, tuplet.actual)
    return Event(
        duration=duration,
        written_duration=written_duration,
        pitches=pitches,
        dynamic=dynamic,
        articulation=articulation,
        tie=tie,
        velocity=velocity,
        staff=staff,
        pitch_cents=pitch_cents,
        expression=expression,
        gestures=gestures,
        id=event_id,
        tuplet=tuplet,
        grace=grace,
        slur=slur,
    )


def _tuplet_from_dict(raw: object) -> Tuplet | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("tuplet must be a mapping")
    unknown = sorted(set(raw) - {"actual", "normal", "type"})
    if unknown or set(raw) != {"actual", "normal", "type"}:
        raise ValueError("tuplet requires only actual, normal, and type")
    actual = raw["actual"]
    normal = raw["normal"]
    kind = raw["type"]
    if (
        isinstance(actual, bool)
        or isinstance(normal, bool)
        or not isinstance(actual, int)
        or not isinstance(normal, int)
        or not 2 <= actual <= 16
        or not 1 <= normal <= 16
        or actual == normal
    ):
        raise ValueError("tuplet actual/normal must be unequal integers in supported range")
    if kind not in {"start", "continue", "stop"}:
        raise ValueError("tuplet type must be start, continue, or stop")
    return Tuplet(actual, normal, str(kind))


def _grace_from_dict(raw: object) -> Grace | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("grace must be a mapping")
    unknown = sorted(set(raw) - {"kind", "steal"})
    if unknown or set(raw) != {"kind", "steal"}:
        raise ValueError("grace requires only kind and steal")
    kind = raw["kind"]
    steal = _finite_float(raw["steal"], "grace.steal")
    if kind not in {"acciaccatura", "appoggiatura"}:
        raise ValueError("grace.kind must be acciaccatura or appoggiatura")
    if not 0 < steal <= 0.5:
        raise ValueError("grace.steal must be greater than 0 and at most 0.5")
    return Grace(str(kind), steal)


def _expression_from_dict(
    raw: object,
) -> tuple[float, tuple[ExpressionPoint, ...], tuple[PerformanceGesture, ...]]:
    if raw is None:
        return 0.0, (), ()
    if not isinstance(raw, dict):
        raise ValueError("expr must be a mapping")
    unknown = sorted(set(raw) - {"pitch_cents", "curves", "gestures"})
    if unknown:
        raise ValueError(f"expr has unknown fields: {', '.join(unknown)}")
    pitch_cents = _finite_float(raw.get("pitch_cents", 0.0), "expr.pitch_cents")
    if not -200.0 <= pitch_cents <= 200.0:
        raise ValueError("expr.pitch_cents must be between -200 and 200")
    curves = raw.get("curves", {})
    if not isinstance(curves, dict):
        raise ValueError("expr.curves must be a mapping")
    points: list[ExpressionPoint] = []
    for parameter, raw_points in curves.items():
        if parameter not in {"pitch", "pressure", "timbre"}:
            raise ValueError(f"unsupported expression curve: {parameter!r}")
        if not isinstance(raw_points, list) or not raw_points:
            raise ValueError(f"expr.curves.{parameter} must be a non-empty list")
        positions = []
        for index, raw_point in enumerate(raw_points):
            if not isinstance(raw_point, dict) or set(raw_point) != {"at", "value"}:
                raise ValueError(f"expr.curves.{parameter}[{index}] requires at and value")
            position = _finite_float(raw_point["at"], "expression position")
            value = _finite_float(raw_point["value"], "expression value")
            if not 0.0 <= position <= 1.0:
                raise ValueError("expression positions must be between 0 and 1")
            if parameter in {"pressure", "timbre"} and not 0.0 <= value <= 1.0:
                raise ValueError(f"{parameter} expression values must be between 0 and 1")
            if parameter == "pitch" and not -200.0 <= value <= 200.0:
                raise ValueError("pitch expression values must be between -200 and 200 cents")
            positions.append(position)
            points.append(ExpressionPoint(str(parameter), position, value))
        if positions != sorted(positions) or len(set(positions)) != len(positions):
            raise ValueError(f"expr.curves.{parameter} positions must be strictly increasing")
    raw_gestures = raw.get("gestures", [])
    if not isinstance(raw_gestures, list):
        raise ValueError("expr.gestures must be a list")
    gestures = tuple(_gesture_from_dict(item, index) for index, item in enumerate(raw_gestures))
    return pitch_cents, tuple(points), gestures


def _gesture_from_dict(raw: object, index: int) -> PerformanceGesture:
    path = f"expr.gestures[{index}]"
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must be a mapping")
    kind = raw.get("type")
    allowed = {
        "nonghyeon": {"type", "depth_cents", "rate_hz"},
        "chuseong": {"type", "depth_cents", "position"},
        "toeseong": {"type", "depth_cents", "position"},
        "breath": {"type", "amount"},
        "pluck_position": {"type", "amount"},
    }
    if kind not in allowed:
        raise ValueError(f"{path}.type is unsupported: {kind!r}")
    unknown = sorted(set(raw) - allowed[str(kind)])
    if unknown:
        raise ValueError(f"{path} has unknown fields: {', '.join(unknown)}")
    depth = _finite_float(raw.get("depth_cents", 30.0), f"{path}.depth_cents")
    rate = _finite_float(raw.get("rate_hz", 5.0), f"{path}.rate_hz")
    position = _finite_float(raw.get("position", 0.5), f"{path}.position")
    amount = _finite_float(raw.get("amount", 0.5), f"{path}.amount")
    if not 0 <= depth <= 200 or not 0.1 <= rate <= 20:
        raise ValueError(f"{path} has invalid pitch gesture depth or rate")
    if not 0 <= position <= 1 or not 0 <= amount <= 1:
        raise ValueError(f"{path} position and amount must be normalized")
    return PerformanceGesture(str(kind), depth, rate, position, amount)


def _finite_float(value: object, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{path} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{path} must be finite")
    return result


def control_event_from_dict(data: dict[str, Any]) -> ControlEvent:
    try:
        measure, beat = parse_anchor(str(data["at"]))
        kind = str(data["type"])
    except KeyError as exc:
        raise ValueError(f"control requires {exc.args[0]!r}") from exc
    control_id = _optional_authored_id(data.get("id"), "control id")

    if kind == "cc":
        _reject_control_fields(data, {"id", "at", "type", "controller", "value"})
        controller = _control_int(data, "controller")
        value = _control_int(data, "value")
        if controller in {0, 32}:
            raise ValueError("CC 0 and 32 are reserved for the instrument profile's bank select")
        if controller == 64:
            raise ValueError("use type: pedal instead of raw CC 64")
        return ControlEvent(
            measure, beat, kind, controller=controller, value=value, id=control_id
        )

    if kind == "pedal":
        _reject_control_fields(data, {"id", "at", "type", "action"})
        action = str(data.get("action", ""))
        if action not in {"down", "up", "change"}:
            raise ValueError("pedal action must be down, up, or change")
        return ControlEvent(measure, beat, kind, pedal_action=action, id=control_id)

    if kind == "keyswitch":
        _reject_control_fields(data, {"id", "at", "type", "name", "velocity", "duration"})
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
            id=control_id,
        )

    if kind == "performance":
        _reject_control_fields(data, {"id", "at", "type", "parameter", "value"})
        parameter = data.get("parameter")
        if not isinstance(parameter, str) or not parameter.strip():
            raise ValueError("performance parameter must be a non-empty string")
        raw_value = data.get("value")
        if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
            raise ValueError("performance value must be numeric")
        value = float(raw_value)
        if not 0.0 <= value <= 1.0:
            raise ValueError("performance value must be normalized between 0 and 1")
        return ControlEvent(
            measure,
            beat,
            kind,
            performance_parameter=parameter.strip(),
            performance_value=value,
            id=control_id,
        )

    if kind == "dynamic_ramp":
        _reject_control_fields(
            data,
            {"id", "at", "type", "end", "from", "to", "controller"},
        )
        end_measure, end_beat = parse_anchor(str(data.get("end", "")))
        start_dynamic = data.get("from")
        end_dynamic = data.get("to")
        if start_dynamic not in DYNAMIC_VELOCITY or end_dynamic not in DYNAMIC_VELOCITY:
            raise ValueError("dynamic ramp from/to must be supported dynamic marks")
        if start_dynamic == end_dynamic:
            raise ValueError("dynamic ramp endpoints must differ")
        controller = _control_int(data, "controller", default=11, minimum=1)
        if controller in {32, 64}:
            raise ValueError("dynamic ramp controller is reserved")
        return ControlEvent(
            measure,
            beat,
            kind,
            controller=controller,
            id=control_id,
            end_measure=end_measure,
            end_beat=end_beat,
            start_dynamic=str(start_dynamic),
            end_dynamic=str(end_dynamic),
        )

    raise ValueError(f"unsupported control type: {kind!r}")


def _reject_control_fields(data: dict[str, Any], allowed: set[str]) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ValueError(f"unknown {data.get('type', 'control')} fields: {', '.join(unknown)}")


def _optional_authored_id(value: object, path: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not AUTHORED_ID_RE.fullmatch(value):
        raise ValueError(
            f"{path} must start with a lowercase letter and contain 3-128 "
            "lowercase letters, digits, underscores, or hyphens"
        )
    return value


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
