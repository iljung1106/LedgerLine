from __future__ import annotations

import json
from pathlib import Path

import pytest

from ledgerline.automation import compile_automation, load_automation, value_at_sample
from ledgerline.diagnostics import ValidationError
from ledgerline.project import load_piece


def test_automation_compiles_to_sample_accurate_schedule(example_project: Path) -> None:
    (example_project / "automation.yaml").write_text(
        """format: 1
lanes:
  - id: piano-expression
    target: parts.piano.performance.expression
    unit: normalized
    interpolation: linear
    points:
      - {at: "1:1", value: 0.25}
      - {at: "2:1", value: 0.75}
""",
        encoding="utf-8",
    )
    piece = load_piece(example_project)
    lanes = load_automation(example_project, piece)
    output = example_project / "build" / "automation.json"
    report = compile_automation(piece, lanes, output)
    assert report["lanes"][0]["points"][1]["sample"] == 160_000
    assert value_at_sample(report["lanes"][0], 80_000) == pytest.approx(0.5)
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "ok"


def test_conflicting_or_unknown_automation_fails_closed(example_project: Path) -> None:
    (example_project / "automation.yaml").write_text(
        """format: 1
lanes:
  - id: x
    target: parts.piano.gain
    typo: true
    points: [{at: "1:1", value: 0.5}]
""",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError, match="automation.yaml is invalid"):
        load_automation(example_project, load_piece(example_project))
