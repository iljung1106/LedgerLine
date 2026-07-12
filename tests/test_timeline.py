from __future__ import annotations

from fractions import Fraction

import pytest

from ledgerline.project import load_piece
from ledgerline.timeline import Timeline


def test_timeline_maps_measure_beats_to_ticks_seconds_and_samples(example_project) -> None:
    timeline = Timeline(load_piece(example_project), sample_rate=48_000)
    position = timeline.anchor(2, Fraction(1))
    assert position.tick == 1920
    assert position.seconds == pytest.approx(10 / 3)
    assert position.sample == 160_000
    assert timeline.total_seconds() == pytest.approx(40 / 3)
    assert timeline.total_seconds(tail_seconds=2.5) == pytest.approx(95 / 6)


def test_timeline_integrates_tempo_changes(example_project) -> None:
    piece_path = example_project / "piece.yaml"
    text = piece_path.read_text(encoding="utf-8")
    text = text.replace(
        '- {at: "1:1", bpm: 72}',
        '- {at: "1:1", bpm: 60}\n  - {at: "3:1", bpm: 120}',
    )
    piece_path.write_text(text, encoding="utf-8")
    timeline = Timeline(load_piece(example_project))
    assert len(timeline.tempo_segments) == 2
    assert timeline.anchor(3, 1).seconds == pytest.approx(8.0)
    assert timeline.total_seconds() == pytest.approx(12.0)
