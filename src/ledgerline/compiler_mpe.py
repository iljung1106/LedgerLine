from __future__ import annotations

from fractions import Fraction
from pathlib import Path

import mido

from ledgerline.compiler_midi import (
    TimedMessage,
    _anchor_tick,
    _delta_track,
    _measure_starts,
    _meta_track,
    _pitch_bend_range_messages,
)
from ledgerline.expression_plan import MPE_CHANNELS
from ledgerline.model import DYNAMIC_VELOCITY, Part, Piece


def compile_mpe_part(
    piece: Piece,
    part: Part,
    part_plan: dict,
    output: Path,
) -> None:
    """Write a lower-zone MPE file with one member channel per active note."""
    if part_plan["backend"] != "mpe":
        raise ValueError("compile_mpe_part requires an MPE performance policy")
    profile = piece.profiles[part.profile_id]
    events: list[TimedMessage] = [
        TimedMessage(0, 0, mido.MetaMessage("track_name", name=part.name)),
        # Lower-zone configuration: master channel 1, 14 member channels.
        TimedMessage(0, 1, mido.Message("control_change", channel=0, control=101, value=0)),
        TimedMessage(0, 2, mido.Message("control_change", channel=0, control=100, value=6)),
        TimedMessage(0, 3, mido.Message("control_change", channel=0, control=6, value=14)),
    ]
    bend_range = int(part_plan["pitch_bend_range"])
    for channel in MPE_CHANNELS:
        events.extend(
            [
                TimedMessage(
                    0,
                    4,
                    mido.Message(
                        "control_change", channel=channel, control=0, value=profile.bank_msb
                    ),
                ),
                TimedMessage(
                    0,
                    5,
                    mido.Message(
                        "control_change", channel=channel, control=32, value=profile.bank_lsb
                    ),
                ),
                TimedMessage(
                    0,
                    6,
                    mido.Message("program_change", channel=channel, program=profile.program),
                ),
            ]
        )
        range_messages = _pitch_bend_range_messages(channel)
        # The standard helper uses two semitones; replace the data-entry MSB.
        for item in range_messages:
            message = item.message
            if message.type == "control_change" and message.control == 6:
                message = message.copy(value=bend_range)
            events.append(TimedMessage(item.tick, item.priority + 4, message))
    starts = _measure_starts(piece)
    for control in part.controls:
        tick = _anchor_tick(piece, starts, control.measure, control.beat)
        if control.kind == "cc":
            events.append(
                TimedMessage(
                    tick,
                    12,
                    mido.Message(
                        "control_change",
                        channel=0,
                        control=int(control.controller),
                        value=int(control.value),
                    ),
                )
            )
        elif control.kind == "pedal":
            values = (
                (0, 127)
                if control.pedal_action == "change"
                else ((127,) if control.pedal_action == "down" else (0,))
            )
            for priority, value in enumerate(values, start=12):
                events.append(
                    TimedMessage(
                        tick,
                        priority,
                        mido.Message("control_change", channel=0, control=64, value=value),
                    )
                )
        elif control.kind == "keyswitch":
            note = profile.keyswitches[str(control.keyswitch)].midi
            duration = round(float(control.duration) * 4 * 480)
            events.extend(
                [
                    TimedMessage(
                        tick,
                        12,
                        mido.Message(
                            "note_on",
                            channel=0,
                            note=note,
                            velocity=control.velocity,
                        ),
                    ),
                    TimedMessage(
                        tick + duration,
                        0,
                        mido.Message("note_off", channel=0, note=note, velocity=0),
                    ),
                ]
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
                        12,
                        mido.Message(
                            "control_change",
                            channel=0,
                            control=int(binding.controller),
                            value=max(0, min(127, value)),
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
                    11,
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
            sample_ticks = list(range(tick, end_tick, 30))
            sample_ticks.append(end_tick)
            for sample_tick in sample_ticks:
                position = (sample_tick - tick) / (end_tick - tick)
                value = round(start_value + (end_value - start_value) * position)
                events.append(
                    TimedMessage(
                        sample_tick,
                        12,
                        mido.Message(
                            "control_change",
                            channel=0,
                            control=int(control.controller),
                            value=max(0, min(127, value)),
                        ),
                    )
                )
    for note in part_plan["notes"]:
        channel = int(note["mpe_channel"])
        start = int(note["start_tick"])
        end = int(note["end_tick"])
        events.append(
            TimedMessage(
                start,
                20,
                mido.Message(
                    "note_on",
                    channel=channel,
                    note=int(note["pitch"]),
                    velocity=int(note["velocity"]),
                ),
            )
        )
        for point in note["expression"]:
            tick = start + round((end - start) * float(point["position"]))
            parameter = point["parameter"]
            value = float(point["value"])
            if parameter == "pitch":
                pitch = max(-8192, min(8191, round(value / (bend_range * 100) * 8192)))
                message = mido.Message("pitchwheel", channel=channel, pitch=pitch)
            elif parameter == "pressure":
                message = mido.Message(
                    "aftertouch", channel=channel, value=max(0, min(127, round(value * 127)))
                )
            else:
                message = mido.Message(
                    "control_change",
                    channel=channel,
                    control=74,
                    value=max(0, min(127, round(value * 127))),
                )
            events.append(TimedMessage(tick, 15, message))
        events.extend(
            [
                TimedMessage(
                    end,
                    0,
                    mido.Message("note_off", channel=channel, note=int(note["pitch"]), velocity=0),
                ),
                TimedMessage(end, 1, mido.Message("pitchwheel", channel=channel, pitch=0)),
            ]
        )
    midi = mido.MidiFile(type=1, ticks_per_beat=480, charset="utf-8")
    midi.tracks.extend([_meta_track(piece), _delta_track(events)])
    output.parent.mkdir(parents=True, exist_ok=True)
    midi.save(output)
