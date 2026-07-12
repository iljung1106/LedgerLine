from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import yaml

from ledgerline.compiler import compile_project
from ledgerline.diagnostics import ValidationError
from ledgerline.project import load_piece


def _make_grand_staff(project: Path) -> Path:
    path = project / "parts" / "piano.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["staves"] = [
        {"number": 1, "name": "right", "clef": {"sign": "G", "line": 2}},
        {"number": 2, "name": "left", "clef": {"sign": "F", "line": 4}},
    ]
    bass_notes = {"1": "C3", "2": "F2", "3": "Ab2", "4": "C3"}
    for number, measure in data["measures"].items():
        for event in measure["v1"]:
            event["staff"] = 1
        measure["v2"] = [{"p": bass_notes[number], "d": "1/1", "staff": 2}]
    # Event-level placement permits an intentional cross-staff note without changing its voice.
    data["measures"]["3"]["v1"][1]["staff"] = 2
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def test_grand_staff_compiles_musicxml_and_preserves_midi_semantics(
    example_project: Path,
) -> None:
    piano_path = _make_grand_staff(example_project)
    piece = load_piece(example_project)
    piano = piece.parts[0]
    assert [(staff.number, staff.name) for staff in piano.staves] == [
        (1, "right"),
        (2, "left"),
    ]

    result = compile_project(example_project)
    tree = ET.parse(result["musicxml"])
    piano_xml = tree.find("./part[@id='P1']")
    assert piano_xml is not None
    assert piano_xml.findtext("./measure[@number='1']/attributes/staves") == "2"
    clefs = piano_xml.findall("./measure[@number='1']/attributes/clef")
    clef_values = [
        (item.attrib.get("number"), item.findtext("sign"), item.findtext("line")) for item in clefs
    ]
    assert clef_values == [
        ("1", "G", "2"),
        ("2", "F", "4"),
    ]
    assert {node.text for node in piano_xml.findall(".//note/staff")} == {"1", "2"}
    voice_one_staves = {
        note.findtext("staff")
        for note in piano_xml.findall(".//note")
        if note.findtext("voice") == "1"
    }
    assert voice_one_staves == {"1", "2"}
    assert {node.text for node in piano_xml.findall(".//direction/staff")} == {"1"}
    multistaff_midi = Path(example_project / "build" / "parts" / "piano.mid").read_bytes()

    # Staff placement is notation metadata. It must not alter timing, pitches, or playback.
    data = yaml.safe_load(piano_path.read_text(encoding="utf-8"))
    data.pop("staves")
    for measure in data["measures"].values():
        for events in measure.values():
            for event in events:
                event.pop("staff")
    piano_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    compile_project(example_project)
    assert Path(example_project / "build" / "parts" / "piano.mid").read_bytes() == multistaff_midi


def test_multistaff_requires_explicit_event_staff(example_project: Path) -> None:
    path = _make_grand_staff(example_project)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["measures"]["1"]["v1"][0].pop("staff")
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    try:
        load_piece(example_project)
    except ValidationError as exc:
        assert any(item.code == "staff.required_multistaff" for item in exc.diagnostics)
    else:
        raise AssertionError("multi-staff event without staff was accepted")


def test_undefined_staff_is_rejected(example_project: Path) -> None:
    path = _make_grand_staff(example_project)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["measures"]["1"]["v1"][0]["staff"] = 3
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    try:
        load_piece(example_project)
    except ValidationError as exc:
        assert any(item.code == "staff.undefined" for item in exc.diagnostics)
    else:
        raise AssertionError("undefined staff number was accepted")


def test_staff_definitions_are_strict_and_contiguous(example_project: Path) -> None:
    path = example_project / "parts" / "piano.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["staves"] = [
        {"number": 1, "clef": {"sign": "G", "line": 2}},
        {"number": 3, "clef": {"sign": "F", "line": 4}, "typo": True},
    ]
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    try:
        load_piece(example_project)
    except ValidationError as exc:
        assert any(item.code == "part.staves_invalid" for item in exc.diagnostics)
    else:
        raise AssertionError("invalid staff definitions were accepted")
