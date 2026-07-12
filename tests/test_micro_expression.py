from __future__ import annotations

import xml.etree.ElementTree as ET

import mido
import yaml

from ledgerline.compiler import compile_project


def test_microtone_curves_and_korean_gestures_compile_to_midi_and_musicxml(
    example_project,
) -> None:
    path = example_project / "parts" / "cello.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["measures"]["1"]["v1"][0]["expr"] = {
        "pitch_cents": 25,
        "curves": {
            "pitch": [{"at": 0, "value": 0}, {"at": 1, "value": -40}],
            "pressure": [{"at": 0, "value": 0.4}, {"at": 1, "value": 0.8}],
            "timbre": [{"at": 0, "value": 0.3}],
        },
        "gestures": [{"type": "nonghyeon", "depth_cents": 20, "rate_hz": 5}],
    }
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    report = compile_project(example_project)
    midi = mido.MidiFile(report["part_midis"][1], charset="utf-8")
    messages = [message for track in midi.tracks for message in track]
    assert any(message.type == "pitchwheel" and message.pitch != 0 for message in messages)
    assert any(message.type == "aftertouch" for message in messages)
    assert any(message.type == "control_change" and message.control == 74 for message in messages)
    tree = ET.parse(report["musicxml"])
    alters = [float(node.text) for node in tree.findall(".//alter")]
    assert any(abs(value - 0.25) < 0.001 for value in alters)
    annotations = tree.findall(".//other-notation")
    assert any(node.get("type") == "ledgerline:gesture:nonghyeon" for node in annotations)
