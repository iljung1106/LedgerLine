from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

import mido

from ledgerline.model import (
    DYNAMIC_VELOCITY,
    ArticulationDefinition,
    Event,
    Part,
    Piece,
)
from ledgerline.timeline import Timeline

TPQ = 480
WHOLE_TICKS = TPQ * 4


@dataclass(frozen=True, slots=True)
class TimedMessage:
    tick: int
    priority: int
    message: mido.Message | mido.MetaMessage


def compile_midi(piece: Piece, output: Path, selected_parts: Iterable[str] | None = None) -> None:
    selected = set(selected_parts) if selected_parts is not None else None
    midi = mido.MidiFile(type=1, ticks_per_beat=TPQ, charset="utf-8")
    midi.tracks.append(_meta_track(piece))
    melodic_channel = 0
    for part in piece.parts:
        if selected is not None and part.id not in selected:
            continue
        profile = piece.profiles[part.profile_id]
        if profile.family == "percussion":
            channel = 9
        else:
            while melodic_channel == 9:
                melodic_channel += 1
            if melodic_channel > 15:
                raise ValueError("MIDI supports at most 15 simultaneous melodic channels")
            channel = melodic_channel
            melodic_channel += 1
        midi.tracks.append(_part_track(piece, part, channel))
    output.parent.mkdir(parents=True, exist_ok=True)
    midi.save(output)


def _measure_starts(piece: Piece) -> dict[int, int]:
    starts: dict[int, int] = {}
    cursor = 0
    for number in range(1, piece.measures + 1):
        starts[number] = cursor
        cursor += _ticks(piece.time_at(number).length)
    starts[piece.measures + 1] = cursor
    return starts


def _meta_track(piece: Piece) -> mido.MidiTrack:
    events: list[TimedMessage] = [
        TimedMessage(0, 0, mido.MetaMessage("track_name", name=piece.title, time=0))
    ]
    starts = _measure_starts(piece)
    major_keys = [
        "Cb",
        "Gb",
        "Db",
        "Ab",
        "Eb",
        "Bb",
        "F",
        "C",
        "G",
        "D",
        "A",
        "E",
        "B",
        "F#",
        "C#",
    ]
    minor_keys = [
        "Abm",
        "Ebm",
        "Bbm",
        "Fm",
        "Cm",
        "Gm",
        "Dm",
        "Am",
        "Em",
        "Bm",
        "F#m",
        "C#m",
        "G#m",
        "D#m",
        "A#m",
    ]
    for change in piece.time_changes:
        events.append(
            TimedMessage(
                starts[change.measure],
                1,
                mido.MetaMessage(
                    "time_signature",
                    numerator=change.beats,
                    denominator=change.beat_type,
                    time=0,
                ),
            )
        )
    for change in piece.key_changes:
        index = max(0, min(14, change.fifths + 7))
        key = minor_keys[index] if change.mode == "minor" else major_keys[index]
        events.append(
            TimedMessage(
                starts[change.measure],
                2,
                mido.MetaMessage("key_signature", key=key, time=0),
            )
        )
    timeline = Timeline(piece)
    tempo_by_tick: dict[int, float] = {}
    for segment in timeline.tempo_segments:
        start_tick = _ticks(segment.start_whole)
        end_tick = _ticks(segment.end_whole)
        tempo_by_tick[start_tick] = segment.bpm
        if segment.curve == "linear":
            events.append(
                TimedMessage(
                    start_tick,
                    2,
                    mido.MetaMessage(
                        "marker",
                        text=(
                            "ledgerline:tempo-ramp:"
                            f"from={segment.bpm:g};to={segment.end_bpm:g};curve=linear;"
                            f"end_tick={end_tick}"
                        ),
                    ),
                )
            )
            sample_ticks = list(range(start_tick, end_tick, max(1, TPQ // 8)))
            sample_ticks.append(end_tick)
            for tick in sample_ticks:
                tempo_by_tick[tick] = segment.bpm_at(Fraction(tick, WHOLE_TICKS))
    for tick, bpm in sorted(tempo_by_tick.items()):
        events.append(
            TimedMessage(
                tick,
                3,
                mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(bpm), time=0),
            )
        )
    return _delta_track(events)


def _part_track(piece: Piece, part: Part, channel: int) -> mido.MidiTrack:
    profile = piece.profiles[part.profile_id]
    events: list[TimedMessage] = [
        TimedMessage(0, 0, mido.MetaMessage("track_name", name=part.name, time=0)),
        TimedMessage(
            0,
            1,
            mido.Message(
                "control_change", channel=channel, control=0, value=profile.bank_msb, time=0
            ),
        ),
        TimedMessage(
            0,
            2,
            mido.Message(
                "control_change", channel=channel, control=32, value=profile.bank_lsb, time=0
            ),
        ),
        TimedMessage(
            0, 3, mido.Message("program_change", channel=channel, program=profile.program, time=0)
        ),
    ]
    if any(
        event.pitch_cents or event.expression or event.gestures
        for measure in part.measures.values()
        for voice_events in measure.voices.values()
        for event in voice_events
    ):
        events.extend(_pitch_bend_range_messages(channel))
    starts = _measure_starts(piece)
    timeline = Timeline(piece)
    for control in part.controls:
        tick = _anchor_tick(piece, starts, control.measure, control.beat)
        if control.kind == "cc":
            events.append(
                TimedMessage(
                    tick,
                    4,
                    mido.Message(
                        "control_change",
                        channel=channel,
                        control=int(control.controller),
                        value=int(control.value),
                        time=0,
                    ),
                )
            )
        elif control.kind == "pedal":
            if control.pedal_action == "change":
                events.append(
                    TimedMessage(
                        tick,
                        4,
                        mido.Message(
                            "control_change", channel=channel, control=64, value=0, time=0
                        ),
                    )
                )
                events.append(
                    TimedMessage(
                        tick,
                        5,
                        mido.Message(
                            "control_change", channel=channel, control=64, value=127, time=0
                        ),
                    )
                )
            else:
                value = 127 if control.pedal_action == "down" else 0
                events.append(
                    TimedMessage(
                        tick,
                        4,
                        mido.Message(
                            "control_change", channel=channel, control=64, value=value, time=0
                        ),
                    )
                )
        elif control.kind == "keyswitch":
            note = profile.keyswitches[str(control.keyswitch)].midi
            events.append(
                TimedMessage(
                    tick,
                    6,
                    mido.Message(
                        "note_on",
                        channel=channel,
                        note=note,
                        velocity=control.velocity,
                        time=0,
                    ),
                )
            )
            events.append(
                TimedMessage(
                    tick + _ticks(control.duration),
                    0,
                    mido.Message("note_off", channel=channel, note=note, velocity=0, time=0),
                )
            )
        elif control.kind == "performance":
            binding = profile.performance[str(control.performance_parameter)]
            if binding.type == "cc":
                value = round(
                    binding.minimum
                    + float(control.performance_value) * (binding.maximum - binding.minimum)
                )
                events.append(
                    TimedMessage(
                        tick,
                        4,
                        mido.Message(
                            "control_change",
                            channel=channel,
                            control=int(binding.controller),
                            value=max(0, min(127, value)),
                            time=0,
                        ),
                    )
                )
        elif control.kind == "dynamic_ramp":
            end_tick = _anchor_tick(
                piece,
                starts,
                int(control.end_measure),
                Fraction(control.end_beat),
            )
            start_value = DYNAMIC_VELOCITY[str(control.start_dynamic)]
            end_value = DYNAMIC_VELOCITY[str(control.end_dynamic)]
            events.append(
                TimedMessage(
                    tick,
                    3,
                    mido.MetaMessage(
                        "marker",
                        text=(
                            "ledgerline:dynamic-ramp:"
                            f"from={control.start_dynamic};to={control.end_dynamic};"
                            f"controller={control.controller};end_tick={end_tick}"
                        ),
                    ),
                )
            )
            sample_ticks = list(range(tick, end_tick, max(1, TPQ // 16)))
            sample_ticks.append(end_tick)
            for sample_tick in sample_ticks:
                position = (sample_tick - tick) / (end_tick - tick)
                value = round(start_value + (end_value - start_value) * position)
                events.append(
                    TimedMessage(
                        sample_tick,
                        4,
                        mido.Message(
                            "control_change",
                            channel=channel,
                            control=int(control.controller),
                            value=max(0, min(127, value)),
                            time=0,
                        ),
                    )
                )
    active_ties: dict[tuple[str, int], bool] = {}
    dynamic_by_voice: dict[str, int] = {}
    for number in range(1, piece.measures + 1):
        measure = part.measures.get(number)
        if measure is None:
            continue
        for voice_name, voice_events in sorted(
            measure.voices.items(), key=lambda item: int(item[0][1:])
        ):
            velocity = dynamic_by_voice.get(voice_name, DYNAMIC_VELOCITY["mf"])
            for scheduled in timeline.schedule_voice(number, voice_events):
                event = scheduled.event
                start_tick = _ticks(scheduled.start_whole)
                duration = _ticks(scheduled.duration)
                if event.dynamic:
                    velocity = DYNAMIC_VELOCITY[event.dynamic]
                    dynamic_by_voice[voice_name] = velocity
                event_velocity = event.velocity or velocity
                definition = profile.articulation_definitions.get(event.articulation or "")
                event_velocity = _articulation_velocity(
                    event,
                    event_velocity,
                    definition,
                )
                gate = _articulation_gate(event, definition)
                end_tick = start_tick + max(1, round(duration * gate))
                start_whole = Fraction(start_tick, WHOLE_TICKS)
                expression_seconds = timeline.seconds_at_whole(
                    start_whole + Fraction(duration, WHOLE_TICKS)
                ) - timeline.seconds_at_whole(start_whole)
                _append_note_expression(
                    events,
                    event,
                    start_tick,
                    duration,
                    end_tick,
                    channel,
                    expression_seconds,
                )
                if event.slur:
                    events.append(
                        TimedMessage(
                            start_tick,
                            8,
                            mido.MetaMessage(
                                "marker", text=f"ledgerline:slur:{event.slur}"
                            ),
                        )
                    )
                for pitch in event.pitches:
                    sounding = pitch.midi + profile.transposition
                    tie_key = (voice_name, sounding)
                    if event.tie in {"stop", "continue"} and active_ties.get(tie_key):
                        if event.tie == "stop":
                            events.append(
                                TimedMessage(
                                    start_tick + duration,
                                    0,
                                    mido.Message(
                                        "note_off",
                                        channel=channel,
                                        note=sounding,
                                        velocity=0,
                                        time=0,
                                    ),
                                )
                            )
                            active_ties.pop(tie_key, None)
                        cursor_end_only = True
                    else:
                        cursor_end_only = False
                    if not cursor_end_only:
                        events.append(
                            TimedMessage(
                                start_tick,
                                10,
                                mido.Message(
                                    "note_on",
                                    channel=channel,
                                    note=sounding,
                                    velocity=event_velocity,
                                    time=0,
                                ),
                            )
                        )
                        if event.tie in {"start", "continue"}:
                            active_ties[tie_key] = True
                        else:
                            events.append(
                                TimedMessage(
                                    end_tick,
                                    0,
                                    mido.Message(
                                        "note_off",
                                        channel=channel,
                                        note=sounding,
                                        velocity=0,
                                        time=0,
                                    ),
                                )
                            )
    final_tick = starts[piece.measures + 1]
    for _, note in active_ties:
        events.append(
            TimedMessage(
                final_tick,
                0,
                mido.Message("note_off", channel=channel, note=note, velocity=0, time=0),
            )
        )
    return _delta_track(events)


def _articulation_velocity(
    event: Event,
    velocity: int,
    definition: ArticulationDefinition | None = None,
) -> int:
    if event.articulation and definition is None:
        raise ValueError(f"articulation lacks a profile definition: {event.articulation}")
    if definition is not None:
        velocity += definition.velocity_delta
    return max(1, min(127, velocity))


def _pitch_bend_range_messages(channel: int) -> list[TimedMessage]:
    return [
        TimedMessage(0, 4, mido.Message("control_change", channel=channel, control=101, value=0)),
        TimedMessage(0, 5, mido.Message("control_change", channel=channel, control=100, value=0)),
        TimedMessage(0, 6, mido.Message("control_change", channel=channel, control=6, value=2)),
        TimedMessage(0, 7, mido.Message("control_change", channel=channel, control=38, value=0)),
        TimedMessage(0, 8, mido.Message("control_change", channel=channel, control=101, value=127)),
        TimedMessage(0, 9, mido.Message("control_change", channel=channel, control=100, value=127)),
    ]


def _append_note_expression(
    messages: list[TimedMessage],
    event: Event,
    start_tick: int,
    duration: int,
    end_tick: int,
    channel: int,
    duration_seconds: float,
) -> None:
    points = list(event.expression)
    for gesture in event.gestures:
        if gesture.type == "nonghyeon":
            cycles = max(0.25, gesture.rate_hz * duration_seconds)
            samples = min(64, max(8, round(cycles * 8)))
            for index in range(samples + 1):
                position = index / samples
                value = gesture.depth_cents * math.sin(2 * math.pi * cycles * position)
                points.append(_point("pitch", position, value))
        elif gesture.type in {"chuseong", "toeseong"}:
            sign = 1.0 if gesture.type == "chuseong" else -1.0
            points.extend(
                [
                    _point("pitch", 0.0, 0.0),
                    _point("pitch", gesture.position, 0.0),
                    _point("pitch", 1.0, sign * gesture.depth_cents),
                ]
            )
        elif gesture.type == "breath":
            points.append(_point("pressure", 0.0, 1.0 - gesture.amount))
        elif gesture.type == "pluck_position":
            points.append(_point("timbre", 0.0, gesture.amount))
    if event.pitch_cents and not any(point.parameter == "pitch" for point in points):
        points.append(_point("pitch", 0.0, 0.0))
    for point in sorted(points, key=lambda item: (item.position, item.parameter)):
        tick = start_tick + round(duration * point.position)
        if point.parameter == "pitch":
            cents = event.pitch_cents + point.value
            messages.append(
                TimedMessage(
                    tick,
                    9,
                    mido.Message("pitchwheel", channel=channel, pitch=_pitchwheel(cents)),
                )
            )
        elif point.parameter == "pressure":
            messages.append(
                TimedMessage(
                    tick,
                    9,
                    mido.Message("aftertouch", channel=channel, value=round(point.value * 127)),
                )
            )
        elif point.parameter == "timbre":
            messages.append(
                TimedMessage(
                    tick,
                    9,
                    mido.Message(
                        "control_change",
                        channel=channel,
                        control=74,
                        value=round(point.value * 127),
                    ),
                )
            )
    if points or event.pitch_cents:
        messages.append(
            TimedMessage(end_tick, 1, mido.Message("pitchwheel", channel=channel, pitch=0))
        )


def _point(parameter: str, position: float, value: float):
    from ledgerline.model import ExpressionPoint

    return ExpressionPoint(parameter, position, value)


def _pitchwheel(cents: float) -> int:
    return max(-8192, min(8191, round(cents / 200.0 * 8192)))


def _articulation_gate(
    event: Event,
    definition: ArticulationDefinition | None = None,
) -> float:
    if event.articulation and definition is None:
        raise ValueError(f"articulation lacks a profile definition: {event.articulation}")
    return definition.gate if definition is not None else 0.9


def _ticks(duration: Fraction) -> int:
    value = duration * WHOLE_TICKS
    if value.denominator != 1:
        raise ValueError(f"duration cannot be represented at {TPQ} TPQ: {duration}")
    return value.numerator


def _anchor_tick(
    piece: Piece,
    starts: dict[int, int],
    measure: int,
    beat: Fraction,
) -> int:
    beat_offset = (beat - 1) * Fraction(1, piece.time_at(measure).beat_type)
    return starts[measure] + _ticks(beat_offset)


def _delta_track(events: list[TimedMessage]) -> mido.MidiTrack:
    track = mido.MidiTrack()
    previous = 0
    for event in sorted(events, key=lambda item: (item.tick, item.priority)):
        message = event.message.copy(time=event.tick - previous)
        track.append(message)
        previous = event.tick
    track.append(mido.MetaMessage("end_of_track", time=0))
    return track
