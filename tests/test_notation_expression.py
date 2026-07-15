from __future__ import annotations

import json
import math
import xml.etree.ElementTree as ET
from pathlib import Path

import mido
import pytest
import yaml

from ledgerline.compiler import compile_project
from ledgerline.diagnostics import ValidationError
from ledgerline.project import load_piece
from ledgerline.studio_model import _notes
from ledgerline.timeline import Timeline


def _read_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _write_yaml(path: Path, data: dict) -> None:
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _absolute(track: mido.MidiTrack) -> list[tuple[int, mido.Message | mido.MetaMessage]]:
    tick = 0
    result = []
    for message in track:
        tick += message.time
        result.append((tick, message))
    return result


def test_tuplet_grace_and_slur_share_notation_and_midi_timing(
    example_project: Path,
) -> None:
    piano_path = example_project / "parts" / "piano.yaml"
    piano = _read_yaml(piano_path)
    triplet = {"actual": 3, "normal": 2}
    piano["measures"]["3"]["v1"] = [
        {"p": "C5", "d": "1/8", "tuplet": {**triplet, "type": "start"}, "slur": "start"},
        {"p": "D5", "d": "1/8", "tuplet": {**triplet, "type": "continue"}},
        {"p": "Eb5", "d": "1/8", "tuplet": {**triplet, "type": "stop"}, "slur": "stop"},
        {"p": "G5", "d": "1/4"},
        {"p": "Ab5", "d": "1/4"},
        {
            "p": "B5",
            "d": "1/16",
            "grace": {"kind": "acciaccatura", "steal": 0.125},
        },
        {"p": "C6", "d": "1/4"},
    ]
    _write_yaml(piano_path, piano)

    result = compile_project(example_project)
    manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
    contract = manifest["notation_contract"]
    assert contract["features"]["tuplet_events"] == 3
    assert contract["features"]["grace_notes"] == 1
    assert contract["representations"]["slurs"]["midi_metadata_only"] is True
    tree = ET.parse(result["musicxml"])
    time_modifications = tree.findall(".//time-modification")
    assert len(time_modifications) == 3
    assert [node.findtext("actual-notes") for node in time_modifications] == ["3"] * 3
    assert [node.attrib["type"] for node in tree.findall(".//tuplet")] == ["start", "stop"]
    assert [node.attrib["type"] for node in tree.findall(".//slur")] == ["start", "stop"]
    grace = tree.find(".//grace")
    assert grace is not None
    assert grace.attrib == {"slash": "yes", "steal-time-following": "12.5"}

    midi = mido.MidiFile(example_project / "build" / "parts" / "piano.mid")
    messages = _absolute(midi.tracks[1])
    starts = {}
    for tick, message in messages:
        if (
            3840 <= tick < 5760
            and message.type == "note_on"
            and message.velocity > 0
            and message.note >= 60
        ):
            starts.setdefault(message.note, tick)
    # Measure 3 begins at 3840 ticks. Triplet eighths are exactly 160 ticks each.
    assert [starts[note] for note in (72, 74, 75)] == [3840, 4000, 4160]
    # The grace note takes 1/8 of the following quarter (60 ticks); the slot still ends on beat 4.
    assert starts[83] == 5280
    assert starts[84] == 5340
    markers = [
        message.text
        for _, message in messages
        if message.type == "marker" and message.text.startswith("ledgerline:slur:")
    ]
    assert markers == ["ledgerline:slur:start", "ledgerline:slur:stop"]

    piece = load_piece(example_project)
    studio_notes = _notes(piece, Timeline(piece))
    tuplet_note = next(
        note for note in studio_notes if note["measure"] == 3 and note["pitch"] == 72
    )
    grace_note = next(
        note for note in studio_notes if note["measure"] == 3 and note["pitch"] == 83
    )
    assert tuplet_note["duration"] == "1/8"
    assert tuplet_note["tuplet"] == {"actual": 3, "normal": 2, "type": "start"}
    assert grace_note["start_tick"] == 5280
    assert grace_note["end_tick"] == 5340


def test_dynamic_and_tempo_ramps_are_explicit_and_deterministic(
    example_project: Path,
) -> None:
    piece_path = example_project / "piece.yaml"
    piece_data = _read_yaml(piece_path)
    piece_data["tempo"] = [
        {
            "at": "1:1",
            "bpm": 72,
            "ramp": {"to": "2:1", "bpm": 96, "curve": "linear"},
        }
    ]
    _write_yaml(piece_path, piece_data)
    piano_path = example_project / "parts" / "piano.yaml"
    piano = _read_yaml(piano_path)
    piano["controls"] = [
        {
            "at": "1:1",
            "type": "dynamic_ramp",
            "end": "2:1",
            "from": "p",
            "to": "f",
        }
    ]
    _write_yaml(piano_path, piano)

    piece = load_piece(example_project)
    timeline = Timeline(piece)
    expected_first_measure = 10 * math.log(96 / 72)
    assert timeline.anchor(2, 1).seconds == pytest.approx(expected_first_measure)
    assert timeline.total_seconds() == pytest.approx(expected_first_measure + 7.5)

    result = compile_project(example_project)
    tree = ET.parse(result["musicxml"])
    tempo_metadata = tree.find(".//other-direction[@type='ledgerline:tempo-ramp']")
    assert tempo_metadata is not None
    assert tempo_metadata.text == "from=72;to=96;end=2:1;curve=linear"
    assert [node.attrib["type"] for node in tree.findall(".//wedge")] == [
        "crescendo",
        "stop",
    ]

    score = mido.MidiFile(result["midi"])
    meta = _absolute(score.tracks[0])
    assert any(
        message.type == "marker" and message.text.startswith("ledgerline:tempo-ramp:")
        for _, message in meta
    )
    piano_midi = mido.MidiFile(example_project / "build" / "parts" / "piano.mid")
    messages = _absolute(piano_midi.tracks[1])
    expression = [
        (tick, message.value)
        for tick, message in messages
        if message.type == "control_change" and message.control == 11
    ]
    assert expression[0] == (0, 50)
    assert expression[-1] == (1920, 92)
    assert expression == sorted(expression)


def test_profile_defined_articulation_controls_musicxml_and_midi(
    example_project: Path,
) -> None:
    source_profile = (
        Path(__file__).parents[1]
        / "src/ledgerline/data/profiles/starter.acoustic-grand-piano.yaml"
    )
    profile = _read_yaml(source_profile)
    profile["articulations"] = [
        "staccato",
        "tenuto",
        "accent",
        "marcato",
        {
            "id": "portato",
            "musicxml": "detached-legato",
            "gate": 0.8,
            "velocity_delta": 3,
        },
    ]
    profiles = example_project / "profiles"
    profiles.mkdir()
    _write_yaml(profiles / "starter.acoustic-grand-piano.yaml", profile)
    piano_path = example_project / "parts" / "piano.yaml"
    piano = _read_yaml(piano_path)
    piano["measures"]["1"]["v1"][0]["art"] = "portato"
    piano["measures"]["1"]["v1"][0]["vel"] = 70
    _write_yaml(piano_path, piano)

    result = compile_project(example_project)
    tree = ET.parse(result["musicxml"])
    assert tree.find(".//detached-legato") is not None
    midi = mido.MidiFile(example_project / "build" / "parts" / "piano.mid")
    messages = _absolute(midi.tracks[1])
    note_on = next(message for tick, message in messages if tick == 0 and message.type == "note_on")
    assert note_on.velocity == 73
    first_note_off = min(
        tick for tick, message in messages if message.type == "note_off" and message.note == 60
    )
    assert first_note_off == 768


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (
            lambda events: events.__setitem__(
                0,
                {"p": "C5", "d": "1/4", "tuplet": {"actual": 3, "normal": 2, "type": "start"}},
            ),
            "tuplet.unclosed",
        ),
        (
            lambda events: events.append(
                {"p": "D5", "d": "1/16", "grace": {"kind": "acciaccatura", "steal": 0.25}}
            ),
            "grace.no_following_note",
        ),
    ],
)
def test_malformed_notation_fails_closed(
    example_project: Path,
    mutate,
    code: str,
) -> None:
    path = example_project / "parts" / "piano.yaml"
    data = _read_yaml(path)
    mutate(data["measures"]["4"]["v1"])
    _write_yaml(path, data)
    with pytest.raises(ValidationError) as caught:
        load_piece(example_project)
    assert any(item.code == code for item in caught.value.diagnostics)


def test_custom_articulation_string_without_profile_mapping_fails_closed(
    example_project: Path,
) -> None:
    source_profile = (
        Path(__file__).parents[1]
        / "src/ledgerline/data/profiles/starter.acoustic-grand-piano.yaml"
    )
    profile = _read_yaml(source_profile)
    profile["articulations"].append("portato")
    profiles = example_project / "profiles"
    profiles.mkdir()
    _write_yaml(profiles / "starter.acoustic-grand-piano.yaml", profile)
    with pytest.raises(ValidationError, match="invalid profile"):
        load_piece(example_project)
