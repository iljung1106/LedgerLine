from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

import mido

from ledgerline.model import DYNAMIC_VELOCITY, Event, Part, Piece

TPQ = 480
WHOLE_TICKS = TPQ * 4


@dataclass(frozen=True, slots=True)
class TimedMessage:
    tick: int
    priority: int
    message: mido.Message | mido.MetaMessage


def compile_midi(piece: Piece, output: Path, selected_parts: Iterable[str] | None = None) -> None:
    selected = set(selected_parts) if selected_parts is not None else None
    midi = mido.MidiFile(type=1, ticks_per_beat=TPQ)
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
    for change in piece.tempo_changes:
        tick = _anchor_tick(piece, starts, change.measure, change.beat)
        events.append(
            TimedMessage(
                tick,
                3,
                mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(change.bpm), time=0),
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
    starts = _measure_starts(piece)
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
    active_ties: dict[tuple[str, int], bool] = {}
    dynamic_by_voice: dict[str, int] = {}
    for number in range(1, piece.measures + 1):
        measure = part.measures.get(number)
        if measure is None:
            continue
        for voice_name, voice_events in sorted(
            measure.voices.items(), key=lambda item: int(item[0][1:])
        ):
            cursor = starts[number]
            velocity = dynamic_by_voice.get(voice_name, DYNAMIC_VELOCITY["mf"])
            for event in voice_events:
                if event.dynamic:
                    velocity = DYNAMIC_VELOCITY[event.dynamic]
                    dynamic_by_voice[voice_name] = velocity
                event_velocity = event.velocity or velocity
                event_velocity = _articulation_velocity(event, event_velocity)
                duration = _ticks(event.duration)
                gate = _articulation_gate(event)
                end_tick = cursor + max(1, round(duration * gate))
                for pitch in event.pitches:
                    sounding = pitch.midi + profile.transposition
                    tie_key = (voice_name, sounding)
                    if event.tie in {"stop", "continue"} and active_ties.get(tie_key):
                        if event.tie == "stop":
                            events.append(
                                TimedMessage(
                                    cursor + duration,
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
                                cursor,
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
                cursor += duration
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


def _articulation_velocity(event: Event, velocity: int) -> int:
    if event.articulation == "accent":
        velocity += 10
    elif event.articulation == "marcato":
        velocity += 16
    return max(1, min(127, velocity))


def _articulation_gate(event: Event) -> float:
    return {"staccato": 0.5, "marcato": 0.72, "tenuto": 0.98}.get(event.articulation, 0.9)


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
