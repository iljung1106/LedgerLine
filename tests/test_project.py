from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ledgerline.diagnostics import ValidationError
from ledgerline.project import load_piece


def test_example_is_valid(example_project: Path) -> None:
    piece = load_piece(example_project)
    assert piece.title == "Ledgerline Nocturne Sketch"
    assert [part.id for part in piece.parts] == ["piano", "cello"]


def test_overfull_measure_is_rejected(example_project: Path) -> None:
    path = example_project / "parts" / "cello.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["measures"]["1"]["v1"].append({"p": "D3", "d": "1/4"})
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValidationError) as caught:
        load_piece(example_project)
    assert any(item.code == "measure.duration_mismatch" for item in caught.value.diagnostics)


def test_absolute_range_is_enforced(example_project: Path) -> None:
    path = example_project / "parts" / "cello.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["measures"]["1"]["v1"][0]["p"] = "C7"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValidationError) as caught:
        load_piece(example_project)
    assert any(item.code == "instrument.range_absolute" for item in caught.value.diagnostics)


def test_unknown_event_field_is_rejected(example_project: Path) -> None:
    path = example_project / "parts" / "piano.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["measures"]["1"]["v1"][0]["typo"] = 1
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValidationError) as caught:
        load_piece(example_project)
    assert any(item.code == "measure.invalid" for item in caught.value.diagnostics)


def test_unknown_piece_field_is_rejected(example_project: Path) -> None:
    path = example_project / "piece.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["duration_seconds"] = 90
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValidationError) as caught:
        load_piece(example_project)
    assert any(item.code == "piece.invalid" for item in caught.value.diagnostics)
