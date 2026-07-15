from __future__ import annotations

import json
import threading
from pathlib import Path
from urllib.request import Request, urlopen

import pytest
import yaml

from ledgerline.diagnostics import ValidationError
from ledgerline.mix_config import load_mix_config
from ledgerline.studio_edits import StudioSession
from ledgerline.studio_model import _ab_contract, _master_report, build_studio_model
from ledgerline.studio_server import create_studio_server


def test_structured_mix_commands_edit_full_format_two_graph(example_project: Path) -> None:
    session = StudioSession(example_project)
    report = session.apply(
        [
            {
                "type": "update_mix_node",
                "node_type": "track",
                "node": "piano",
                "changes": {"gain_db": -5.5, "pan": -0.15, "output": "master"},
            },
            {
                "type": "update_mix_node",
                "node_type": "bus",
                "node": "strings",
                "changes": {"gain_db": -1.5, "pan": 0.1},
            },
            {
                "type": "update_mix_node",
                "node_type": "master",
                "node": "master",
                "changes": {"gain_db": -2.0, "target_lufs": -17.0},
            },
            {
                "type": "set_mix_send",
                "node_type": "track",
                "node": "piano",
                "bus": "room",
                "gain_db": -9.0,
            },
            {
                "type": "add_mix_insert",
                "node_type": "track",
                "node": "piano",
                "processor": {
                    "type": "eq",
                    "highpass_hz": 40.0,
                    "bands": [{"frequency_hz": 320.0, "gain_db": -1.0, "q": 1.1}],
                },
            },
            {
                "type": "add_mix_insert",
                "node_type": "track",
                "node": "piano",
                "processor": {
                    "type": "compressor",
                    "threshold_db": -20.0,
                    "ratio": 2.5,
                },
            },
            {
                "type": "reorder_mix_insert",
                "node_type": "track",
                "node": "piano",
                "insert_index": 1,
                "to_index": 0,
            },
            {
                "type": "update_mix_insert",
                "node_type": "track",
                "node": "piano",
                "insert_index": 0,
                "changes": {"threshold_db": -22.0, "attack_ms": 15.0},
            },
            {
                "type": "delete_mix_insert",
                "node_type": "track",
                "node": "piano",
                "insert_index": 1,
            },
        ]
    )

    config = load_mix_config(example_project)
    assert config.tracks["piano"].gain_db == -5.5
    assert config.tracks["piano"].pan == -0.15
    assert config.tracks["piano"].sends["room"] == -9.0
    assert config.buses["strings"].gain_db == -1.5
    assert config.master["gain_db"] == -2.0
    assert config.master["target_lufs"] == -17.0
    assert [item.kind for item in config.tracks["piano"].inserts] == ["compressor"]
    assert config.tracks["piano"].inserts[0].settings["threshold_db"] == -22.0
    assert report["transaction"]["impact"]["aspects"] == ["mix"]
    assert report["transaction"]["impact"]["parts"] == ["piano"]
    assert set(report["transaction"]["impact"]["targets"]) >= {
        "track:piano",
        "bus:strings",
        "master",
    }

    model = build_studio_model(example_project)
    assert model["review"]["status"] == "current"
    assert model["review"]["impact"] == report["transaction"]["impact"]
    assert model["mix"]["authored"] == yaml.safe_load(
        (example_project / "mix.yaml").read_text(encoding="utf-8")
    )
    assert model["mix"]["source"]["authored_revision"] == model["project"][
        "authored_revision"
    ]


def test_invalid_mix_graph_rolls_back_every_command_and_keeps_source_bytes(
    example_project: Path,
) -> None:
    before = (example_project / "mix.yaml").read_bytes()
    session = StudioSession(example_project)
    with pytest.raises(ValidationError, match="mix.yaml is invalid"):
        session.apply(
            [
                {
                    "type": "update_mix_node",
                    "node_type": "master",
                    "node": "master",
                    "changes": {"gain_db": -3.0},
                },
                {
                    "type": "update_mix_node",
                    "node_type": "track",
                    "node": "piano",
                    "changes": {"output": "missing-bus"},
                },
            ]
        )
    assert (example_project / "mix.yaml").read_bytes() == before
    assert not (
        example_project / ".ledgerline" / "history" / "studio-last-transaction.json"
    ).exists()


def test_update_mix_remains_backward_compatible(example_project: Path) -> None:
    StudioSession(example_project).apply(
        [
            {
                "type": "update_mix",
                "part": "piano",
                "changes": {
                    "gain_db": -6.0,
                    "pan": 0.25,
                    "send": {"bus": "room", "gain_db": -14.0},
                },
            }
        ]
    )
    config = load_mix_config(example_project)
    assert config.tracks["piano"].gain_db == -6.0
    assert config.tracks["piano"].pan == 0.25
    assert config.tracks["piano"].sends["room"] == -14.0


def test_master_report_and_ab_contract_are_explicit_and_revision_bound() -> None:
    state = {
        "authored_revision": "a" * 64,
        "stages": {
            "mix": {
                "status": "ready",
                "output": {"sha256": "b" * 64},
                "provenance": {
                    "premaster_measurement": {"integrated_lufs": -18.2},
                    "final_measurement": {
                        "integrated_lufs": -16.1,
                        "true_peak_dbtp": -1.2,
                        "loudness_range_lu": 7.5,
                    },
                },
            }
        },
    }
    report = _master_report(
        {
            "target_lufs": -16.0,
            "true_peak_ceiling_db": -1.0,
            "loudness_range_lu": 11.0,
            "loudness_tolerance_lu": 0.5,
        },
        state,
    )
    assert report["source_revision"] == "a" * 64
    assert report["integrated_lufs"] == -16.1
    assert report["true_peak_dbtp"] == -1.2
    assert report["final_measurement"]["loudness_range_lu"] == 7.5

    current = {
        "status": "ready",
        "source_revision": "a" * 64,
        "sha256": "b" * 64,
        "duration_seconds": 32.0,
    }
    previous = {
        "status": "ready",
        "source_revision": "0" * 64,
        "sha256": "c" * 64,
        "duration_seconds": 31.5,
    }
    contract = _ab_contract(current, previous, "a" * 64)
    assert contract["available"] is True
    assert contract["selection_mode"] == "exclusive"
    assert contract["playback_policy"]["simultaneous_playback"] is False
    assert contract["playback_policy"]["level_matching"] == "none"
    assert contract["alignment"]["common_duration_seconds"] == 31.5

    current["measurement"] = {"integrated_lufs": -16.0, "true_peak_dbtp": -1.1}
    previous["measurement"] = {"integrated_lufs": -20.0, "true_peak_dbtp": -2.0}
    matched = _ab_contract(current, previous, "a" * 64)
    assert matched["playback_policy"]["level_matching"] == "integrated-lufs"
    assert matched["playback_policy"]["gain_adjustment_db"]["requested_previous"] == 4.0
    assert matched["playback_policy"]["gain_adjustment_db"]["previous"] == pytest.approx(1.9)
    assert matched["playback_policy"]["gain_adjustment_db"]["peak_limited"] is True


def test_studio_server_exposes_latest_source_impact(example_project: Path) -> None:
    server = create_studio_server(example_project, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        with urlopen(f"{base}/api/model", timeout=5) as response:
            model = json.loads(response.read().decode("utf-8"))
        request = Request(
            f"{base}/api/commands",
            data=json.dumps(
                {
                    "revision": model["project"]["revision"],
                    "commands": [
                        {
                            "type": "update_mix_node",
                            "node_type": "track",
                            "node": "piano",
                            "changes": {"gain_db": -7.0},
                        }
                    ],
                }
            ).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-LedgerLine-Token": model["csrf_token"],
            },
            method="POST",
        )
        with urlopen(request, timeout=10) as response:
            edited = json.loads(response.read().decode("utf-8"))
        with urlopen(f"{base}/api/review/impact", timeout=5) as response:
            impact = json.loads(response.read().decode("utf-8"))
        assert impact["status"] == "current"
        assert impact["current_revision"] == edited["revision"]
        assert impact["impact"]["parts"] == ["piano"]
        assert impact["impact"]["measures"] == []
        assert impact["impact"]["aspects"] == ["mix"]
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
