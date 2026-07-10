from __future__ import annotations

import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path

from ledgerline.model import Event, Piece, duration_token

DIVISIONS = 960
WHOLE_TICKS = DIVISIONS * 4


def _ticks(duration: Fraction) -> int:
    value = duration * WHOLE_TICKS
    if value.denominator != 1:
        raise ValueError(f"duration cannot be represented at {DIVISIONS} divisions: {duration}")
    return value.numerator


def compile_musicxml(piece: Piece, output: Path) -> None:
    score = ET.Element("score-partwise", version="4.0")
    work = ET.SubElement(score, "work")
    ET.SubElement(work, "work-title").text = piece.title
    identification = ET.SubElement(score, "identification")
    encoding = ET.SubElement(identification, "encoding")
    ET.SubElement(encoding, "software").text = "LedgerLine 0.1.0"

    part_list = ET.SubElement(score, "part-list")
    for index, part in enumerate(piece.parts, start=1):
        profile = piece.profiles[part.profile_id]
        score_part = ET.SubElement(part_list, "score-part", id=f"P{index}")
        ET.SubElement(score_part, "part-name").text = part.name
        score_instrument = ET.SubElement(score_part, "score-instrument", id=f"P{index}-I1")
        ET.SubElement(score_instrument, "instrument-name").text = profile.name
        midi_instrument = ET.SubElement(score_part, "midi-instrument", id=f"P{index}-I1")
        ET.SubElement(midi_instrument, "midi-channel").text = str(_musicxml_channel(index - 1))
        ET.SubElement(midi_instrument, "midi-bank").text = str(
            profile.bank_msb * 128 + profile.bank_lsb + 1
        )
        ET.SubElement(midi_instrument, "midi-program").text = str(profile.program + 1)

    for index, part in enumerate(piece.parts, start=1):
        profile = piece.profiles[part.profile_id]
        xml_part = ET.SubElement(score, "part", id=f"P{index}")
        current_dynamic: str | None = None
        for measure_number in range(1, piece.measures + 1):
            xml_measure = ET.SubElement(xml_part, "measure", number=str(measure_number))
            if measure_number == 1 or any(
                item.measure == measure_number for item in piece.time_changes + piece.key_changes
            ):
                attributes = ET.SubElement(xml_measure, "attributes")
                if measure_number == 1:
                    ET.SubElement(attributes, "divisions").text = str(DIVISIONS)
                    if profile.transposition:
                        transpose = ET.SubElement(attributes, "transpose")
                        ET.SubElement(transpose, "chromatic").text = str(profile.transposition)
                key = piece.key_at(measure_number)
                key_node = ET.SubElement(attributes, "key")
                ET.SubElement(key_node, "fifths").text = str(key.fifths)
                ET.SubElement(key_node, "mode").text = key.mode
                time = piece.time_at(measure_number)
                time_node = ET.SubElement(attributes, "time")
                ET.SubElement(time_node, "beats").text = str(time.beats)
                ET.SubElement(time_node, "beat-type").text = str(time.beat_type)
                if measure_number == 1:
                    clef = ET.SubElement(attributes, "clef")
                    ET.SubElement(clef, "sign").text = profile.clef_sign
                    ET.SubElement(clef, "line").text = str(profile.clef_line)

            for tempo in [item for item in piece.tempo_changes if item.measure == measure_number]:
                direction = ET.SubElement(xml_measure, "direction", placement="above")
                direction_type = ET.SubElement(direction, "direction-type")
                metronome = ET.SubElement(direction_type, "metronome")
                ET.SubElement(metronome, "beat-unit").text = "quarter"
                ET.SubElement(metronome, "per-minute").text = f"{tempo.bpm:g}"
                ET.SubElement(direction, "sound", tempo=f"{tempo.bpm:g}")

            source_measure = part.measures.get(measure_number)
            if source_measure is None:
                _append_rest(xml_measure, piece.time_at(measure_number).length, "1")
                continue

            measure_ticks = _ticks(piece.time_at(measure_number).length)
            for voice_index, (voice_name, events) in enumerate(
                sorted(source_measure.voices.items(), key=lambda item: int(item[0][1:]))
            ):
                if voice_index:
                    backup = ET.SubElement(xml_measure, "backup")
                    ET.SubElement(backup, "duration").text = str(measure_ticks)
                for event in events:
                    if event.dynamic and event.dynamic != current_dynamic:
                        _append_dynamic(xml_measure, event.dynamic)
                        current_dynamic = event.dynamic
                    _append_event(xml_measure, event, voice_name[1:])

    ET.indent(score, space="  ")
    body = ET.tostring(score, encoding="unicode")
    header = (
        '<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n'
        '<!DOCTYPE score-partwise PUBLIC "-//Recordare//DTD MusicXML 4.0 Partwise//EN" '
        '"http://www.musicxml.org/dtds/partwise.dtd">\n'
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(header + body + "\n", encoding="utf-8")


def _musicxml_channel(part_index: int) -> int:
    channel = part_index + 1
    if channel == 10:
        channel += 1
    return min(channel, 16)


def _append_dynamic(parent: ET.Element, dynamic: str) -> None:
    direction = ET.SubElement(parent, "direction", placement="below")
    direction_type = ET.SubElement(direction, "direction-type")
    dynamics = ET.SubElement(direction_type, "dynamics")
    ET.SubElement(dynamics, dynamic)


def _append_rest(parent: ET.Element, duration: Fraction, voice: str) -> None:
    event = Event(duration=duration)
    _append_event(parent, event, voice, measure_rest=True)


def _append_event(
    parent: ET.Element,
    event: Event,
    voice: str,
    *,
    measure_rest: bool = False,
) -> None:
    note_type, dots = duration_token(event.duration)
    pitches = event.pitches or (None,)
    for pitch_index, pitch in enumerate(pitches):
        note = ET.SubElement(parent, "note")
        if pitch_index:
            ET.SubElement(note, "chord")
        if pitch is None:
            rest = ET.SubElement(note, "rest")
            if measure_rest:
                rest.set("measure", "yes")
        else:
            pitch_node = ET.SubElement(note, "pitch")
            ET.SubElement(pitch_node, "step").text = pitch.step
            if pitch.alter:
                ET.SubElement(pitch_node, "alter").text = str(pitch.alter)
            ET.SubElement(pitch_node, "octave").text = str(pitch.octave)
        ET.SubElement(note, "duration").text = str(_ticks(event.duration))
        if event.tie in {"start", "continue"}:
            ET.SubElement(note, "tie", type="start")
        if event.tie in {"stop", "continue"}:
            ET.SubElement(note, "tie", type="stop")
        ET.SubElement(note, "voice").text = voice
        ET.SubElement(note, "type").text = note_type
        for _ in range(dots):
            ET.SubElement(note, "dot")
        if event.articulation or event.tie:
            notations = ET.SubElement(note, "notations")
            if event.tie in {"start", "continue"}:
                ET.SubElement(notations, "tied", type="start")
            if event.tie in {"stop", "continue"}:
                ET.SubElement(notations, "tied", type="stop")
            if event.articulation:
                articulations = ET.SubElement(notations, "articulations")
                ET.SubElement(articulations, event.articulation)
