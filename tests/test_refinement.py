from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from ledgerline.brief import load_brief, validate_edit_actions
from ledgerline.build_state import authored_revision, build_state
from ledgerline.cli import main
from ledgerline.diagnostics import ValidationError
from ledgerline.project import load_piece, prepare_ids
from ledgerline.refinement import build_refinement_report


def _write_brief(project: Path) -> None:
    data = {
        "format": 1,
        "purpose": "representative chamber sketch",
        "trajectory": ["intimate", "uncertain", "resolved"],
        "sections": [
            {"id": "A", "from": "1:1", "to": "2:end", "function": "exposition"},
            {"id": "B", "from": "3:1", "to": "4:end", "function": "response"},
        ],
        "roles": [
            {"part": "piano", "from": "1:1", "to": "4:end", "role": "harmony"},
            {"part": "cello", "from": "1:1", "to": "4:end", "role": "foreground"},
        ],
        "protected": [
            {
                "from": "1:1",
                "to": "1:end",
                "parts": ["cello"],
                "aspects": ["pitch", "rhythm"],
            }
        ],
        "style_checks": {"parallel_fifths": "review", "low_register_spacing": "review"},
        "invariants": ["Keep the ensemble acoustic and small."],
        "checkpoints": ["representative-sketch", "structural-draft", "final"],
    }
    (project / "brief.yaml").write_text(
        yaml.safe_dump(data, sort_keys=False), encoding="utf-8"
    )


def test_prepare_ids_is_deterministic_idempotent_and_snapshot_backed(
    example_project: Path,
) -> None:
    preview = prepare_ids(example_project, dry_run=True)
    assert preview["changed"] > 0
    applied = prepare_ids(example_project)
    assert applied["changes"] == preview["changes"]
    assert (example_project / applied["snapshot"]).is_dir()
    assert prepare_ids(example_project)["changed"] == 0

    piece = load_piece(example_project)
    assert all(
        event.id
        for part in piece.parts
        for measure in part.measures.values()
        for events in measure.voices.values()
        for event in events
        if not event.is_rest
    )
    automation = yaml.safe_load((example_project / "automation.yaml").read_text(encoding="utf-8"))
    assert all(point["id"] for lane in automation["lanes"] for point in lane["points"])


def test_duplicate_authored_event_id_is_rejected(example_project: Path) -> None:
    prepare_ids(example_project)
    path = example_project / "parts" / "piano.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    duplicate = data["measures"]["1"]["v1"][0]["id"]
    data["measures"]["1"]["v1"][1]["id"] = duplicate
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValidationError) as caught:
        load_piece(example_project)
    assert any(item.code == "authored_id.duplicate" for item in caught.value.diagnostics)


def test_brief_and_refinement_report_expose_evidence_not_aesthetic_score(
    example_project: Path,
) -> None:
    _write_brief(example_project)
    brief = load_brief(example_project)
    assert brief is not None
    assert brief.sections[0].function == "exposition"

    report = build_refinement_report(example_project)
    assert report["gates"]["hard"]["status"] == "passed"
    assert set(report["domains"]) == {"structure", "harmony", "orchestration", "expression"}
    assert report["domains"]["structure"]["sections"][0]["note_events"] > 0
    assert "quality_score" not in report


def test_protected_brief_range_blocks_machine_edit_scope(example_project: Path) -> None:
    prepare_ids(example_project)
    _write_brief(example_project)
    piece = load_piece(example_project)
    event_id = piece.parts[1].measures[1].voices["v1"][0].id
    violations = validate_edit_actions(
        example_project,
        [{"type": "update_event", "part": "cello", "event_id": event_id, "changes": {"p": "D3"}}],
    )
    assert violations and "protected pitch" in violations[0]


def test_invalid_project_becomes_failed_hard_gate(example_project: Path) -> None:
    path = example_project / "parts" / "cello.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["measures"]["1"]["v1"][0]["p"] = "C7"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    report = build_refinement_report(example_project)
    assert report["status"] == "blocked"
    assert report["gates"]["hard"]["status"] == "failed"


def test_cli_refine_records_the_same_freshness_contract(
    example_project: Path,
) -> None:
    assert main(["refine", "inspect", str(example_project), "--json"]) == 0
    output = example_project / "build" / "refinement" / "report.json"
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["authored_revision"] == authored_revision(example_project)
    assert build_state(example_project)["stages"]["refinement"]["status"] == "ready"
