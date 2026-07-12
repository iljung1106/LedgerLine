from __future__ import annotations

import json
from pathlib import Path

import pytest

from ledgerline.instrument_profile import (
    analyze_instrument_probe,
    approve_instrument_profile,
    create_instrument_probe,
    draft_instrument_profile,
    probe_reference_instrument,
    seal_instrument_profile,
)
from ledgerline.plugin_host import scan_reference_plugin
from ledgerline.project import load_profile
from ledgerline.reference_host import reference_manifest, render_reference_plugin


def test_plugin_scan_draft_requires_matching_explicit_approval(tmp_path: Path) -> None:
    scan_path = tmp_path / "scan.json"
    scan_reference_plugin(reference_manifest(), "clap", output=scan_path)
    draft_path = tmp_path / "draft.json"
    draft = draft_instrument_profile(
        scan_path,
        draft_path,
        profile_id="local.reference",
        name="Reference",
        family="synth",
    )
    raw = json.loads(draft_path.read_text(encoding="utf-8"))
    semantics = {item["semantic"] for item in raw["binding_candidates"]}
    assert {"attack", "brightness", "expression"} <= semantics
    with pytest.raises(ValueError, match="approval token"):
        approve_instrument_profile(draft_path, tmp_path / "profile.yaml", token="wrong")
    raw["profile"]["midi"]["program"] = 7
    draft_path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="approval token"):
        approve_instrument_profile(
            draft_path, tmp_path / "profile.yaml", token=draft["approval_token"]
        )
    draft = seal_instrument_profile(draft_path)
    approved = tmp_path / "profiles" / "local.reference.yaml"
    approve_instrument_profile(draft_path, approved, token=draft["approval_token"])
    assert load_profile(tmp_path, "local.reference").id == "local.reference"


def test_reference_audio_probe_reports_range_and_velocity_response(tmp_path: Path) -> None:
    report = probe_reference_instrument(
        reference_manifest(), tmp_path / "probe", low=60, high=66, step=6
    )
    assert report["status"] == "ok"
    assert report["audible_range"] == ["C4", "F#4"]
    assert report["silent_pitches"] == []


def test_renderer_neutral_probe_plan_can_analyze_external_render(tmp_path: Path) -> None:
    plan = create_instrument_probe(
        tmp_path / "generic", low=60, high=60, step=1, sample_rate=24_000
    )
    schedule = [{**note, "expression": []} for note in plan["schedule"]]
    wav = tmp_path / "generic" / "render.wav"
    render_reference_plugin(
        {
            "plugin_format": "clap",
            "plugin": str(reference_manifest()),
            "wav": str(wav),
            "midi": plan["midi"],
            "sample_rate": 24_000,
            "tail_seconds": 0.1,
            "automation": [],
            "note_expression": schedule,
        }
    )
    report = analyze_instrument_probe(wav, plan["plan"], tmp_path / "generic" / "analysis.json")
    assert report["audible_range"] == ["C4", "C4"]
    assert report["silent_pitches"] == []
