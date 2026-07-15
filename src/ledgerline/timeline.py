from __future__ import annotations

import math
from dataclasses import dataclass
from fractions import Fraction

from ledgerline.model import Event, Piece, TempoChange

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
    end_bpm: float | None = None
    curve: str = "step"

    @property
    def duration_seconds(self) -> float:
        return self.seconds_until(self.end_whole)

    def seconds_until(self, whole: Fraction) -> float:
        quarters = float((whole - self.start_whole) * 4)
        total_quarters = float((self.end_whole - self.start_whole) * 4)
        target = self.end_bpm if self.end_bpm is not None else self.bpm
        if self.curve != "linear" or target == self.bpm or total_quarters == 0:
            return quarters * 60.0 / self.bpm
        slope = (target - self.bpm) / total_quarters
        current = self.bpm + slope * quarters
        return 60.0 / slope * math.log(current / self.bpm)

    def bpm_at(self, whole: Fraction) -> float:
        target = self.end_bpm if self.end_bpm is not None else self.bpm
        if self.curve != "linear" or target == self.bpm:
            return self.bpm
        span = self.end_whole - self.start_whole
        position = float((whole - self.start_whole) / span) if span else 0.0
        return self.bpm + (target - self.bpm) * position


@dataclass(frozen=True, slots=True)
class ScheduledEvent:
    event: Event
    start_whole: Fraction
    duration: Fraction


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
                return segment.start_seconds + segment.seconds_until(whole)
        return sum(segment.duration_seconds for segment in self.tempo_segments)

    def bpm_at_whole(self, whole: Fraction) -> float:
        if whole < 0 or whole > self.end_whole:
            raise ValueError(f"timeline position outside piece: {whole}")
        for segment in self.tempo_segments:
            if whole <= segment.end_whole:
                return segment.bpm_at(whole)
        return self.tempo_segments[-1].bpm_at(self.end_whole)

    def schedule_voice(
        self,
        measure: int,
        events: tuple[Event, ...],
    ) -> tuple[ScheduledEvent, ...]:
        """Schedule measured and grace events on the shared musical timeline."""

        cursor = self.measure_starts[measure]
        pending: list[Event] = []
        scheduled: list[ScheduledEvent] = []
        for event in events:
            if event.grace is not None:
                pending.append(event)
                continue
            allocations = [
                event.duration * Fraction(str(grace.grace.steal)) for grace in pending
            ]
            stolen = sum(allocations, start=Fraction(0))
            if stolen >= event.duration:
                raise ValueError("grace-note group leaves no duration for its following note")
            grace_cursor = cursor
            for grace, allocation in zip(pending, allocations, strict=True):
                _whole_to_ticks(allocation)
                scheduled.append(ScheduledEvent(grace, grace_cursor, allocation))
                grace_cursor += allocation
            performed_duration = event.duration - stolen
            _whole_to_ticks(performed_duration)
            scheduled.append(ScheduledEvent(event, cursor + stolen, performed_duration))
            cursor += event.duration
            pending = []
        if pending:
            raise ValueError("grace-note group has no following measured note")
        return tuple(scheduled)

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
                    "end_bpm": segment.end_bpm,
                    "curve": segment.curve,
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
        anchors: list[tuple[Fraction, TempoChange]] = []
        for change in self.piece.tempo_changes:
            time = self.piece.time_at(change.measure)
            whole = self.measure_starts[change.measure] + (change.beat - 1) * Fraction(
                1, time.beat_type
            )
            anchors.append((whole, change))
        segments: list[TempoSegment] = []
        seconds = 0.0
        for index, (start, raw_change) in enumerate(anchors):
            change = raw_change
            end = anchors[index + 1][0] if index + 1 < len(anchors) else self.end_whole
            if change.ramp_end_measure is not None:
                ramp_time = self.piece.time_at(change.ramp_end_measure)
                ramp_end = self.measure_starts[change.ramp_end_measure] + (
                    change.ramp_end_beat - 1
                ) * Fraction(1, ramp_time.beat_type)
                ramp = TempoSegment(
                    start,
                    ramp_end,
                    seconds,
                    change.bpm,
                    change.ramp_bpm,
                    "linear",
                )
                segments.append(ramp)
                seconds += ramp.duration_seconds
                if ramp_end < end:
                    hold = TempoSegment(ramp_end, end, seconds, change.ramp_bpm)
                    segments.append(hold)
                    seconds += hold.duration_seconds
            else:
                segment = TempoSegment(start, end, seconds, change.bpm)
                segments.append(segment)
                seconds += segment.duration_seconds
        return tuple(segments)


def _whole_to_ticks(whole: Fraction) -> int:
    value = whole * WHOLE_TICKS
    if value.denominator != 1:
        raise ValueError(f"timeline position cannot be represented at {TPQ} TPQ: {whole}")
    return value.numerator
