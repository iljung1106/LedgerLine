from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

from ledgerline.model import Piece

TPQ = 480
WHOLE_TICKS = TPQ * 4


@dataclass(frozen=True, slots=True)
class TimelinePosition:
    measure: int
    beat: Fraction
    whole: Fraction
    tick: int
    seconds: float
    sample: int


@dataclass(frozen=True, slots=True)
class TempoSegment:
    start_whole: Fraction
    end_whole: Fraction
    start_seconds: float
    bpm: float

    @property
    def duration_seconds(self) -> float:
        return float((self.end_whole - self.start_whole) * 4) * 60.0 / self.bpm


class Timeline:
    """Convert authored measure:beat anchors to ticks, seconds, and samples."""

    def __init__(self, piece: Piece, sample_rate: int = 48_000):
        if sample_rate < 8_000 or sample_rate > 384_000:
            raise ValueError("sample_rate must be between 8000 and 384000")
        self.piece = piece
        self.sample_rate = sample_rate
        self.measure_starts = self._measure_starts()
        self.end_whole = self.measure_starts[piece.measures + 1]
        self.tempo_segments = self._tempo_segments()

    def anchor(self, measure: int, beat: Fraction | int | float) -> TimelinePosition:
        beat_fraction = beat if isinstance(beat, Fraction) else Fraction(str(beat))
        if not 1 <= measure <= self.piece.measures:
            raise ValueError(f"measure outside piece: {measure}")
        time = self.piece.time_at(measure)
        if beat_fraction < 1 or beat_fraction > time.beats:
            raise ValueError(f"beat outside measure {measure}: {beat_fraction}")
        whole = self.measure_starts[measure] + (beat_fraction - 1) * Fraction(1, time.beat_type)
        seconds = self.seconds_at_whole(whole)
        return TimelinePosition(
            measure=measure,
            beat=beat_fraction,
            whole=whole,
            tick=_whole_to_ticks(whole),
            seconds=seconds,
            sample=round(seconds * self.sample_rate),
        )

    def seconds_at_whole(self, whole: Fraction) -> float:
        if whole < 0 or whole > self.end_whole:
            raise ValueError(f"timeline position outside piece: {whole}")
        for segment in self.tempo_segments:
            if whole <= segment.end_whole:
                offset = float((whole - segment.start_whole) * 4) * 60.0 / segment.bpm
                return segment.start_seconds + offset
        return sum(segment.duration_seconds for segment in self.tempo_segments)

    def total_seconds(self, *, tail_seconds: float = 0.0) -> float:
        if tail_seconds < 0 or tail_seconds > 600:
            raise ValueError("tail_seconds must be between 0 and 600")
        return self.seconds_at_whole(self.end_whole) + tail_seconds

    def total_samples(self, *, tail_seconds: float = 0.0) -> int:
        return round(self.total_seconds(tail_seconds=tail_seconds) * self.sample_rate)

    def report(self, *, tail_seconds: float = 0.0) -> dict:
        return {
            "schema_version": "1",
            "status": "ok",
            "sample_rate": self.sample_rate,
            "musical_duration_seconds": self.total_seconds(),
            "tail_seconds": tail_seconds,
            "estimated_duration_seconds": self.total_seconds(tail_seconds=tail_seconds),
            "estimated_samples": self.total_samples(tail_seconds=tail_seconds),
            "tempo_segments": [
                {
                    "start_tick": _whole_to_ticks(segment.start_whole),
                    "end_tick": _whole_to_ticks(segment.end_whole),
                    "start_seconds": segment.start_seconds,
                    "duration_seconds": segment.duration_seconds,
                    "bpm": segment.bpm,
                }
                for segment in self.tempo_segments
            ],
        }

    def _measure_starts(self) -> dict[int, Fraction]:
        starts: dict[int, Fraction] = {}
        cursor = Fraction(0)
        for measure in range(1, self.piece.measures + 1):
            starts[measure] = cursor
            cursor += self.piece.time_at(measure).length
        starts[self.piece.measures + 1] = cursor
        return starts

    def _tempo_segments(self) -> tuple[TempoSegment, ...]:
        anchors: list[tuple[Fraction, float]] = []
        for change in self.piece.tempo_changes:
            time = self.piece.time_at(change.measure)
            whole = self.measure_starts[change.measure] + (change.beat - 1) * Fraction(
                1, time.beat_type
            )
            anchors.append((whole, change.bpm))
        segments: list[TempoSegment] = []
        seconds = 0.0
        for index, (start, bpm) in enumerate(anchors):
            end = anchors[index + 1][0] if index + 1 < len(anchors) else self.end_whole
            segment = TempoSegment(start, end, seconds, bpm)
            segments.append(segment)
            seconds += segment.duration_seconds
        return tuple(segments)


def _whole_to_ticks(whole: Fraction) -> int:
    value = whole * WHOLE_TICKS
    if value.denominator != 1:
        raise ValueError(f"timeline position cannot be represented at {TPQ} TPQ: {whole}")
    return value.numerator
