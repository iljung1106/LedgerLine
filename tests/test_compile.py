from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import mido
import yaml

from ledgerline.compiler import compile_project


def test_compile_writes_musicxml_midi_and_manifest(example_project: Path) -> None:
    result = compile_project(example_project)
    musicxml = Path(result["musicxml"])
    midi = Path(result["midi"])
    manifest = Path(result["manifest"])
    assert musicxml.is_file()
    assert '<score-partwise version="4.0">' in musicxml.read_text(encoding="utf-8")
    parsed = mido.MidiFile(midi)
    assert len(parsed.tracks) == 3
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert data["tool"]["name"] == "ledgerline"
    assert len(data["outputs"]) == 4


def test_compile_is_deterministic(example_project: Path) -> None:
    first = compile_project(example_project)
    midi_bytes = Path(first["midi"]).read_bytes()
    xml_text = Path(first["musicxml"]).read_text(encoding="utf-8")
    second = compile_project(example_project)
    assert Path(second["midi"]).read_bytes() == midi_bytes
    assert Path(second["musicxml"]).read_text(encoding="utf-8") == xml_text


def test_tie_is_consistent_in_musicxml_and_midi(example_project: Path) -> None:
    cello = example_project / "parts" / "cello.yaml"
    data = yaml.safe_load(cello.read_text(encoding="utf-8"))
    data["measures"]["3"]["v1"][0]["tie"] = "start"
    data["measures"]["4"]["v1"][0]["p"] = "Ab2"
    data["measures"]["4"]["v1"][0]["tie"] = "stop"
    cello.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    result = compile_project(example_project)

    tree = ET.parse(result["musicxml"])
    tie_types = [node.attrib["type"] for node in tree.findall(".//tie")]
    assert "start" in tie_types and "stop" in tie_types

    midi = mido.MidiFile(example_project / "build" / "parts" / "cello.mid")
    absolute = 0
    starts: list[int] = []
    stops: list[int] = []
    for message in midi.tracks[1]:
        absolute += message.time
        if message.type == "note_on" and message.note == 44 and message.velocity > 0:
            starts.append(absolute)
        if message.type in {"note_off", "note_on"} and message.note == 44 and message.velocity == 0:
            stops.append(absolute)
    assert len(starts) == 1
    assert len(stops) == 1
    assert stops[0] - starts[0] == 3840
