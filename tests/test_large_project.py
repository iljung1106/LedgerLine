from __future__ import annotations

import gc
import json
import tracemalloc

import yaml

from ledgerline.studio_model import build_studio_model


def test_long_project_studio_model_stays_bounded_and_repeatable(example_project) -> None:
    """Exercise the Studio projection on a score much longer than the demo fixture.

    A generous allocation ceiling catches accidental quadratic materialisation without
    imposing a machine-dependent wall-clock assertion.
    """

    measure_count = 256
    piece_path = example_project / "piece.yaml"
    piece = yaml.safe_load(piece_path.read_text(encoding="utf-8"))
    piece["measures"] = measure_count
    piece_path.write_text(
        yaml.safe_dump(piece, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    for part_id, pitch in (("piano", "C4"), ("cello", "C3")):
        part_path = example_project / "parts" / f"{part_id}.yaml"
        part = yaml.safe_load(part_path.read_text(encoding="utf-8"))
        part["measures"] = {
            str(measure): {
                "v1": [
                    {
                        "id": f"{part_id}-m{measure}",
                        "p": pitch,
                        "d": "1/1",
                    }
                ]
            }
            for measure in range(1, measure_count + 1)
        }
        part_path.write_text(
            yaml.safe_dump(part, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )

    gc.collect()
    tracemalloc.start()
    try:
        first = build_studio_model(example_project, peak_bins=128)
        _, peak_bytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert first["project"]["measures"] == measure_count
    assert first["project"]["prepared_ids"] is True
    assert len(first["notes"]) == measure_count * 2
    assert len(first["transport"]["measures"]) == measure_count
    assert peak_bytes < 128 * 1024 * 1024
    assert len(json.dumps(first)) < 8 * 1024 * 1024

    second = build_studio_model(example_project, peak_bins=128)
    assert second["project"]["revision"] == first["project"]["revision"]
    assert [note["id"] for note in second["notes"]] == [
        note["id"] for note in first["notes"]
    ]
