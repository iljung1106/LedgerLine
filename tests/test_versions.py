from __future__ import annotations

import zipfile

import yaml

from ledgerline.project import load_piece
from ledgerline.versions import apply_edit_plan, diff_projects, snapshot_project


def test_snapshot_and_scoped_edit_preserve_original(example_project, tmp_path) -> None:
    snapshot = snapshot_project(example_project, "before-soften")
    with zipfile.ZipFile(snapshot["snapshot"]) as archive:
        assert "piece.yaml" in archive.namelist()
        assert not any(name.startswith("build/") for name in archive.namelist())
    original = (example_project / "parts" / "cello.yaml").read_text(encoding="utf-8")
    plan = tmp_path / "edits.yaml"
    plan.write_text(
        yaml.safe_dump(
            {
                "format": 1,
                "edits": [
                    {
                        "scope": {"part": "cello", "measure_start": 1, "measure_end": 2},
                        "operation": {"type": "scale_velocity", "factor": 0.8},
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    output = tmp_path / "edited"
    report = apply_edit_plan(example_project, plan, output)
    assert report["edits"][0]["affected"] > 0
    assert (example_project / "parts" / "cello.yaml").read_text(encoding="utf-8") == original
    load_piece(output)
    changes = diff_projects(example_project, output)
    assert any(item["path"] == "parts/cello.yaml" for item in changes["changed_files"])
