from __future__ import annotations

import json
from pathlib import Path

import mido
import pytest
import yaml

from ledgerline.compiler import compile_project
from ledgerline.diagnostics import ValidationError
from ledgerline.expression_plan import build_expression_plan
from ledgerline.performance_templates import apply_performance_template
from ledgerline.project import load_piece


def _expressive_polyphony(project: Path) -> None:
    path = project / "parts" / "piano.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["measures"]["1"]["v1"][0]["expr"] = {
        "curves": {"pitch": [{"at": 0.0, "value": 0.0}, {"at": 1.0, "value": 35.0}]}
    }
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")


def test_legacy_expressive_chord_fails_closed(example_project: Path) -> None:
    _expressive_polyphony(example_project)
    with pytest.raises(ValidationError) as caught:
        build_expression_plan(load_piece(example_project))
    assert caught.value.diagnostics[0].code == "expression.channel_conflict"


def test_mpe_assigns_distinct_channels_and_compiles_member_messages(example_project: Path) -> None:
    _expressive_polyphony(example_project)
    apply_performance_template(example_project, "piano", "mpe-expressive-string")
    report = compile_project(example_project)
    plan = json.loads(Path(report["expression_plan"]).read_text(encoding="utf-8"))
    chord = plan["parts"]["piano"]["notes"][:3]
    assert len({note["mpe_channel"] for note in chord}) == 3
    midi = mido.MidiFile(example_project / "build" / "parts" / "piano.mid")
    channels = {
        message.channel
        for message in midi.tracks[1]
        if not message.is_meta and message.type == "note_on" and message.velocity > 0
    }
    assert len(channels) >= 3
    assert any(message.type == "pitchwheel" for message in midi.tracks[1])


def test_clap_plan_preserves_stable_note_ids_without_midi_channel_collapse(
    example_project: Path,
) -> None:
    _expressive_polyphony(example_project)
    apply_performance_template(example_project, "piano", "clap-note-expression")
    plan = build_expression_plan(load_piece(example_project))
    notes = plan["parts"]["piano"]["notes"]
    assert len({note["note_id"] for note in notes}) == len(notes)
    assert all("mpe_channel" not in note for note in notes)
