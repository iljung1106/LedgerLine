from __future__ import annotations

import yaml

from ledgerline.project import load_piece
from ledgerline.project_init import initialize_project, list_project_templates


def test_initialize_project_creates_valid_empty_score_and_direction_gate(tmp_path) -> None:
    root = tmp_path / "new-piece"
    report = initialize_project(
        root,
        title="새 작품",
        template="piano-cello",
        measures=12,
        bpm=72,
        mode="minor",
        fifths=-3,
        duration_target="2:30",
    )
    assert report["direction_gate"] == "unresolved"
    piece = load_piece(root)
    assert piece.title == "새 작품"
    assert [part.id for part in piece.parts] == ["piano", "cello"]
    piano = yaml.safe_load((root / "parts" / "piano.yaml").read_text(encoding="utf-8"))
    assert len(piano["staves"]) == 2
    assert "Do not author score events" in (root / "NOTES.md").read_text(encoding="utf-8")


def test_project_templates_are_machine_readable() -> None:
    report = list_project_templates()
    ids = {item["id"] for item in report["templates"]}
    assert {"piano-solo", "piano-cello", "string-duo", "chamber-trio"} <= ids
