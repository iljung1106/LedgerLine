from __future__ import annotations

from pathlib import Path

from ledgerline.visual_review import _html


def test_visual_review_html_has_seekable_markers_and_score_pages(tmp_path: Path) -> None:
    score = tmp_path / "score-1.png"
    page = _html(
        "Test",
        [score],
        [{"at": "2:3", "start_seconds": 4.25, "message": "thin out cello"}],
    )
    assert "data-seconds='4.25'" in page
    assert "thin out cello" in page
    assert "score-1.png" in page
