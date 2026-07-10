from __future__ import annotations

from pathlib import Path

from ledgerline.analysis import inspect_project


def test_inspect_reports_harmony_without_a_quality_score(example_project: Path) -> None:
    report = inspect_project(example_project)
    assert report["harmony"][0]["chord"] == "Cm"
    assert report["harmony"][1]["chord"] == "Ab/C"
    assert "quality_score" not in report
    assert {part["id"] for part in report["parts"]} == {"piano", "cello"}
