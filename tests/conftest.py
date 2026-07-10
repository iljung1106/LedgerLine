from __future__ import annotations

import shutil
from pathlib import Path

import pytest


@pytest.fixture
def example_project(tmp_path: Path) -> Path:
    source = Path(__file__).parents[1] / "examples" / "nocturne"
    target = tmp_path / "nocturne"
    shutil.copytree(source, target)
    return target
