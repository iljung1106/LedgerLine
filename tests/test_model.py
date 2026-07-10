from fractions import Fraction

import pytest

from ledgerline.model import parse_duration, parse_pitch


def test_pitch_uses_scientific_pitch_notation() -> None:
    assert parse_pitch("C4").midi == 60
    assert parse_pitch("Eb5").midi == 75


def test_duration_supports_dots() -> None:
    assert parse_duration("1/4") == Fraction(1, 4)
    assert parse_duration("1/8.") == Fraction(3, 16)
    assert parse_duration("1/16..") == Fraction(7, 64)


def test_invalid_pitch_is_rejected() -> None:
    with pytest.raises(ValueError):
        parse_pitch("H4")
