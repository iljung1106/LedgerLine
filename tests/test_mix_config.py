from __future__ import annotations

import pytest
import yaml

from ledgerline.diagnostics import ValidationError
from ledgerline.mix_config import load_mix_config
from ledgerline.mixer import _filter_graph


def test_format_two_bus_graph_has_eq_compression_and_sends(example_project) -> None:
    data = {
        "format": 2,
        "master": {
            "target_lufs": -16,
            "true_peak_ceiling_db": -1,
            "inserts": [{"type": "compressor", "threshold_db": -18, "ratio": 2}],
        },
        "buses": {
            "room": {
                "output": "master",
                "gain_db": -8,
                "inserts": [{"type": "reverb", "delays_ms": "30|45"}],
            },
            "strings": {
                "output": "master",
                "inserts": [
                    {
                        "type": "eq",
                        "highpass_hz": 60,
                        "bands": [{"frequency_hz": 2500, "gain_db": -2, "q": 1.2}],
                    }
                ],
            },
        },
        "tracks": {
            "piano": {"output": "master", "sends": {"room": -12}},
            "cello": {"output": "strings", "sends": {"room": -10}},
        },
    }
    (example_project / "mix.yaml").write_text(
        yaml.safe_dump(data, sort_keys=False), encoding="utf-8"
    )
    config = load_mix_config(example_project)
    graph, output = _filter_graph(config, ())
    assert output == "[mixout]"
    assert "equalizer=" in graph
    assert "acompressor=" in graph
    assert "aecho=" in graph
    assert "bus_strings" in graph


def test_format_two_rejects_bus_cycles(example_project) -> None:
    data = {
        "format": 2,
        "master": {},
        "buses": {"a": {"output": "b"}, "b": {"output": "a"}},
        "tracks": {"piano": {"output": "a"}},
    }
    (example_project / "mix.yaml").write_text(
        yaml.safe_dump(data, sort_keys=False), encoding="utf-8"
    )
    with pytest.raises(ValidationError) as caught:
        load_mix_config(example_project)
    assert "cycle" in str(caught.value.__cause__)
