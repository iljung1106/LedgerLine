from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import mido
import pytest
import yaml

from ledgerline.compiler import compile_project
from ledgerline.diagnostics import ValidationError
from ledgerline.project import load_piece


def _add_piano_keyswitch_profile(project: Path) -> None:
    profiles = project / "profiles"
    profiles.mkdir()
    profile = {
        "format": 1,
        "id": "starter.acoustic-grand-piano",
        "name": "Test Piano",
        "family": "keyboards",
        "range": {"absolute": ["A0", "C8"], "comfortable": ["A0", "C8"]},
        "transposition": 0,
        "midi": {"bank_msb": 0, "bank_lsb": 0, "program": 0},
        "clef": {"sign": "G", "line": 2},
        "articulations": ["staccato", "tenuto", "accent", "marcato"],
        "keyswitches": {"sustain": "C0"},
    }
    (profiles / "starter.acoustic-grand-piano.yaml").write_text(
        yaml.safe_dump(profile, sort_keys=False), encoding="utf-8"
    )


def _write_controls(project: Path, controls: list[dict]) -> None:
    path = project / "parts" / "piano.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["controls"] = controls
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _absolute_messages(track: mido.MidiTrack) -> list[tuple[int, mido.Message]]:
    tick = 0
    result = []
    for message in track:
        tick += message.time
        if not message.is_meta:
            result.append((tick, message))
    return result


def test_cc_pedal_and_keyswitch_compile_to_midi_and_musicxml(example_project: Path) -> None:
    _add_piano_keyswitch_profile(example_project)
    _write_controls(
        example_project,
        [
            {"at": "1:1", "type": "cc", "controller": 11, "value": 60},
            {"at": "1:1", "type": "pedal", "action": "down"},
            {
                "at": "1:1",
                "type": "keyswitch",
                "name": "sustain",
                "velocity": 72,
                "duration": "1/32",
            },
            {"at": "1:2.5", "type": "cc", "controller": 1, "value": 70},
            {"at": "1:3", "type": "pedal", "action": "change"},
            {"at": "2:1", "type": "pedal", "action": "up"},
        ],
    )

    result = compile_project(example_project)
    midi = mido.MidiFile(example_project / "build" / "parts" / "piano.mid")
    messages = _absolute_messages(midi.tracks[1])

    assert any(
        tick == 0 and msg.type == "control_change" and msg.control == 11 and msg.value == 60
        for tick, msg in messages
    )
    assert any(
        tick == 720 and msg.type == "control_change" and msg.control == 1 and msg.value == 70
        for tick, msg in messages
    )
    assert [
        (tick, msg.value)
        for tick, msg in messages
        if msg.type == "control_change" and msg.control == 64
    ] == [(0, 127), (960, 0), (960, 127), (1920, 0)]
    assert any(
        tick == 0 and msg.type == "note_on" and msg.note == 12 and msg.velocity == 72
        for tick, msg in messages
    )
    assert any(
        tick == 60 and msg.type == "note_off" and msg.note == 12
        for tick, msg in messages
    )

    tree = ET.parse(result["musicxml"])
    assert [node.attrib["type"] for node in tree.findall(".//pedal")] == [
        "start",
        "change",
        "stop",
    ]
    annotations = {
        node.attrib["type"]: node.text for node in tree.findall(".//other-direction")
    }
    assert annotations["ledgerline:cc"] in {
        "controller=11;value=60",
        "controller=1;value=70",
    }
    assert annotations["ledgerline:keyswitch"].startswith("name=sustain;")


@pytest.mark.parametrize(
    ("controls", "code"),
    [
        (
            [{"at": "1:1", "type": "cc", "controller": 11, "value": 64, "typo": 1}],
            "control.invalid",
        ),
        (
            [{"at": "1:1", "type": "keyswitch", "name": "legato"}],
            "instrument.keyswitch_unsupported",
        ),
        ([{"at": "1:1", "type": "pedal", "action": "down"}], "pedal.unclosed"),
        ([{"at": "1:1", "type": "cc", "controller": 64, "value": 127}], "control.invalid"),
    ],
)
def test_invalid_expression_data_fails_closed(
    example_project: Path,
    controls: list[dict],
    code: str,
) -> None:
    _write_controls(example_project, controls)
    with pytest.raises(ValidationError) as caught:
        load_piece(example_project)
    assert any(item.code == code for item in caught.value.diagnostics)
