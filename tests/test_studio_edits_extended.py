from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ledgerline.project import load_piece, prepare_ids
from ledgerline.studio_edits import StudioSession
from ledgerline.studio_model import build_studio_model


def _event_id(project: Path, part: str, measure: int, index: int = 0) -> str:
    piece = load_piece(project)
    selected = next(item for item in piece.parts if item.id == part)
    event_id = selected.measures[measure].voices["v1"][index].id
    assert event_id is not None
    return event_id


def test_event_commands_use_stable_ids_and_move_across_measures(example_project: Path) -> None:
    prepare_ids(example_project)
    moving = _event_id(example_project, "piano", 3)
    target = _event_id(example_project, "piano", 4)
    session = StudioSession(example_project)
    report = session.apply(
        [
            {"type": "delete_event", "part": "piano", "event_id": target},
            {
                "type": "move_event",
                "part": "piano",
                "event_id": moving,
                "target_measure": 4,
                "target_voice": "v1",
                "target_offset_whole": "0",
            },
        ]
    )
    assert report["status"] == "ok"
    moved_piece = load_piece(example_project)
    measure_four = next(part for part in moved_piece.parts if part.id == "piano").measures[4]
    assert any(event.id == moving for event in measure_four.voices["v1"])


def test_insert_update_delete_and_replace_voice_are_transactional(example_project: Path) -> None:
    prepare_ids(example_project)
    deleted = _event_id(example_project, "piano", 3)
    session = StudioSession(example_project)
    result = session.apply(
        [
            {"type": "delete_event", "part": "piano", "event_id": deleted},
            {
                "type": "insert_event",
                "part": "piano",
                "measure": 3,
                "voice": "v1",
                "at_offset_whole": "0",
                "event": {"p": "C#5", "d": "1/4", "vel": 88},
            },
        ]
    )
    inserted = result["applied"][1]["event_id"]
    session.apply(
        [
            {
                "type": "update_event",
                "part": "piano",
                "event_id": inserted,
                "changes": {"articulation": "accent"},
            }
        ]
    )
    event = next(
        event
        for event in load_piece(example_project).parts[0].measures[3].voices["v1"]
        if event.id == inserted
    )
    assert event.articulation == "accent"

    session.apply(
        [
            {
                "type": "replace_measure_voice",
                "part": "piano",
                "measure": 3,
                "voice": "v1",
                "events": [
                    {"p": "C5", "d": "1/2", "dyn": "mp"},
                    {"p": "G5", "d": "1/2", "dyn": "f"},
                ],
            }
        ]
    )
    assert len(load_piece(example_project).parts[0].measures[3].voices["v1"]) == 2


def test_control_tempo_automation_and_restart_safe_undo(example_project: Path) -> None:
    prepare_ids(example_project)
    session = StudioSession(example_project)
    inserted = session.apply(
        [
            {
                "type": "insert_control",
                "part": "piano",
                "control": {"at": "1:1", "type": "cc", "controller": 11, "value": 32},
            },
            {"type": "insert_tempo", "tempo": {"at": "2:1", "bpm": 76}},
            {
                "type": "insert_point",
                "lane": "cello-entrance",
                "point": {"at": "3:1", "value": -0.5},
            },
        ]
    )
    control_id = inserted["applied"][0]["control_id"]
    point_id = inserted["applied"][2]["point_id"]
    session.apply(
        [
            {
                "type": "update_control",
                "part": "piano",
                "control_id": control_id,
                "changes": {"at": "2:2", "value": 48},
            },
            {
                "type": "update_point",
                "lane": "cello-entrance",
                "point_id": point_id,
                "changes": {"value": -0.25, "curve": "smooth"},
            },
            {
                "type": "move_point",
                "lane": "cello-entrance",
                "point_id": point_id,
                "at": "3:2",
            },
            {
                "type": "set_curve",
                "lane": "cello-entrance",
                "curve": "linear",
            },
        ]
    )

    moved_piano = yaml.safe_load(
        (example_project / "parts" / "piano.yaml").read_text(encoding="utf-8")
    )
    assert next(item for item in moved_piano["controls"] if item["id"] == control_id)["at"] == "2:2"
    automation = yaml.safe_load((example_project / "automation.yaml").read_text(encoding="utf-8"))
    lane = next(item for item in automation["lanes"] if item["id"] == "cello-entrance")
    assert lane["interpolation"] == "linear"
    assert next(item for item in lane["points"] if item["id"] == point_id)["at"] == "3:2"

    restarted = StudioSession(example_project)
    assert restarted.can_undo is True
    restarted.undo()
    piano = yaml.safe_load((example_project / "parts" / "piano.yaml").read_text(encoding="utf-8"))
    control = next(item for item in piano["controls"] if item["id"] == control_id)
    assert control["value"] == 32
    assert control["at"] == "1:1"


def test_pitch_cents_edit_preserves_the_rest_of_expression_payload(
    example_project: Path,
) -> None:
    piano_path = example_project / "parts" / "cello.yaml"
    document = yaml.safe_load(piano_path.read_text(encoding="utf-8"))
    event = document["measures"]["1"]["v1"][0]
    event["expr"] = {
        "pitch_cents": 12,
        "curves": {"timbre": [{"at": 0.0, "value": 0.2}, {"at": 1.0, "value": 0.8}]},
        "gestures": [{"type": "breath", "amount": 0.35}],
    }
    piano_path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    prepare_ids(example_project)
    event_id = _event_id(example_project, "cello", 1)

    StudioSession(example_project).apply(
        [{
            "type": "update_event",
            "part": "cello",
            "event_id": event_id,
            "changes": {"pitch_cents": -37.5},
        }]
    )

    cello = next(part for part in load_piece(example_project).parts if part.id == "cello")
    edited = next(item for item in cello.measures[1].voices["v1"] if item.id == event_id)
    assert edited.pitch_cents == -37.5
    assert [(point.parameter, point.position, point.value) for point in edited.expression] == [
        ("timbre", 0.0, 0.2),
        ("timbre", 1.0, 0.8),
    ]
    assert edited.gestures[0].type == "breath"
    assert edited.gestures[0].amount == 0.35
    modeled = next(
        note
        for note in build_studio_model(example_project)["notes"]
        if note.get("event_id") == event_id
    )
    assert modeled["pitch_cents"] == -37.5
    assert modeled["expression"]["curves"]["timbre"][-1] == {"at": 1.0, "value": 0.8}
    assert modeled["expression"]["gestures"] == [{"type": "breath", "amount": 0.35}]


def test_cc_and_keyswitch_writes_fail_closed_against_active_profile(
    example_project: Path,
) -> None:
    prepare_ids(example_project)
    session = StudioSession(example_project)
    with pytest.raises(ValueError, match="does not declare CC1"):
        session.apply([
            {
                "type": "insert_control",
                "part": "piano",
                "control": {"at": "1:1", "type": "cc", "controller": 1, "value": 64},
            }
        ])
    with pytest.raises(ValueError, match="does not declare keyswitch"):
        session.apply([
            {
                "type": "insert_control",
                "part": "cello",
                "control": {"at": "1:1", "type": "keyswitch", "name": "spiccato"},
            }
        ])


def test_transaction_history_follows_authored_part_references(example_project: Path) -> None:
    custom_dir = example_project / "score-sources"
    custom_dir.mkdir()
    source = example_project / "parts" / "piano.yaml"
    custom = custom_dir / "keyboard.yaml"
    source.replace(custom)
    piece = yaml.safe_load((example_project / "piece.yaml").read_text(encoding="utf-8"))
    next(item for item in piece["parts"] if item["id"] == "piano")["file"] = (
        "score-sources/keyboard.yaml"
    )
    (example_project / "piece.yaml").write_text(
        yaml.safe_dump(piece, sort_keys=False), encoding="utf-8"
    )
    prepare_ids(example_project)
    event_id = _event_id(example_project, "piano", 1)
    before = custom.read_bytes()

    session = StudioSession(example_project)
    session.apply(
        [
            {
                "type": "update_note",
                "part": "piano",
                "event_id": event_id,
                "changes": {"velocity": 101},
            }
        ]
    )
    assert custom.read_bytes() != before
    session.undo()
    assert custom.read_bytes() == before
    assert not source.exists()
