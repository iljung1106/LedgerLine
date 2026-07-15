from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest
import yaml

from ledgerline.compiler import compile_project
from ledgerline.project import load_piece, validate_piece
from ledgerline.refinement import build_refinement_report
from ledgerline.render_graph import _run_node, load_render_graph

ROOT = Path(__file__).parents[1]
FIXTURE_ROOT = ROOT / "examples" / "refinement-demo"
STATES = ("sketch", "refined", "production")


def _copy_state(tmp_path: Path, state: str) -> Path:
    destination = tmp_path / state
    shutil.copytree(FIXTURE_ROOT / state, destination, ignore=shutil.ignore_patterns("build"))
    return destination


def _event_signature(project: Path) -> list[tuple]:
    piece = load_piece(project)
    return [
        (
            part.id,
            measure_number,
            voice,
            event.id,
            tuple(str(pitch) for pitch in event.pitches),
            str(event.duration),
            event.staff,
        )
        for part in piece.parts
        for measure_number, measure in sorted(part.measures.items())
        for voice, events in sorted(measure.voices.items())
        for event in events
    ]


@pytest.mark.parametrize("state", STATES)
def test_refinement_demo_states_validate_compile_and_report(
    tmp_path: Path,
    state: str,
) -> None:
    project = _copy_state(tmp_path, state)
    piece = load_piece(project)
    assert not [item for item in validate_piece(piece) if item.severity == "error"]
    report = compile_project(project)
    assert report["status"] == "ok"
    assert (project / "build" / "score.musicxml").is_file()
    assert (project / "build" / "score.mid").is_file()

    refinement = build_refinement_report(project)
    assert refinement["gates"]["hard"]["status"] == "passed"
    assert refinement["brief"]["protected"][0]["aspects"] == ["pitch", "rhythm"]
    assert "quality_score" not in refinement
    assert all(signature[3] for signature in _event_signature(project))


def test_refinement_demo_preserves_protected_motive_and_refined_notes() -> None:
    protected = []
    for state in STATES:
        data = yaml.safe_load((FIXTURE_ROOT / state / "parts" / "cello.yaml").read_text())
        protected.append(
            [
                (event["id"], event["p"], event["d"])
                for measure in ("1", "2")
                for event in data["measures"][measure]["v1"]
            ]
        )
    assert protected[0] == protected[1] == protected[2]
    assert _event_signature(FIXTURE_ROOT / "refined") == _event_signature(
        FIXTURE_ROOT / "production"
    )

    rationale = json.loads((FIXTURE_ROOT / "refinement-rationale.json").read_text())
    assert all(item["rationale"] and item["listening_checks"] for item in rationale["passes"])
    assert any("not an aesthetic score" in claim for claim in rationale["claims"])


def test_production_fixture_uses_deterministic_reference_host_without_external_assets(
    tmp_path: Path,
) -> None:
    project = _copy_state(tmp_path, "production")
    compile_project(project)
    piece = load_piece(project)
    graph = load_render_graph(project, piece)
    assert all(node.engine == "plugin" and node.executable is None for node in graph.nodes)
    assert all(node.instrument.is_relative_to(project) for node in graph.nodes)

    node = graph.nodes[0]
    first = project / "build" / "reference-first.wav"
    second = project / "build" / "reference-second.wav"
    midi = project / "build" / "parts" / f"{node.part}.mid"
    _run_node(node, midi, first, graph, project / "build", 30)
    _run_node(node, midi, second, graph, project / "build", 30)
    assert first.stat().st_size > 44
    first_hash = hashlib.sha256(first.read_bytes()).digest()
    second_hash = hashlib.sha256(second.read_bytes()).digest()
    assert first_hash == second_hash
