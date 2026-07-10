from __future__ import annotations

import math

import pytest
import yaml

from ledgerline.audio import db_to_amplitude, pan_coefficients
from ledgerline.diagnostics import ValidationError
from ledgerline.mixer import mix_project


def test_db_to_amplitude() -> None:
    assert db_to_amplitude(-6.0206) == pytest.approx(0.5, abs=0.0001)


def test_equal_power_pan() -> None:
    left, right = pan_coefficients(0.0)
    assert left == pytest.approx(math.sqrt(0.5))
    assert right == pytest.approx(math.sqrt(0.5))


def test_invalid_pan_is_rejected() -> None:
    with pytest.raises(ValueError):
        pan_coefficients(1.1)


def test_unknown_mix_field_is_rejected(example_project) -> None:
    path = example_project / "mix.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["master"]["target_lufs_typo"] = -16
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValidationError) as caught:
        mix_project(example_project)
    assert caught.value.diagnostics[0].code == "mix.unknown_field"
