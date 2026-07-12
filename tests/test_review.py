from __future__ import annotations

import yaml

from ledgerline.review import compile_review_annotations


def test_review_annotations_resolve_to_shared_timeline(example_project) -> None:
    data = {
        "format": 1,
        "annotations": [
            {
                "id": "cello-too-forward",
                "at": "2:1",
                "end": "2:3",
                "category": "mix",
                "severity": "warning",
                "message": "첼로가 너무 앞에 들림",
                "parts": ["cello"],
            }
        ],
    }
    (example_project / "review.yaml").write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    report = compile_review_annotations(example_project, sample_rate=48_000)
    annotation = report["annotations"][0]
    assert annotation["start_seconds"] < annotation["end_seconds"]
    assert annotation["start_sample"] == round(annotation["start_seconds"] * 48_000)
