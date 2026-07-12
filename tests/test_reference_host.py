from __future__ import annotations

import hashlib
import json
import wave
from pathlib import Path

from ledgerline.audio_regression import check_audio_baseline, record_audio_baseline
from ledgerline.plugin_host import scan_reference_plugin
from ledgerline.reference_host import reference_manifest, render_reference_plugin


def _request(tmp_path: Path) -> dict:
    return {
        "plugin_format": "clap",
        "plugin": str(reference_manifest()),
        "wav": str(tmp_path / "render.wav"),
        "midi": str(tmp_path / "unused.mid"),
        "sample_rate": 24_000,
        "tail_seconds": 0.1,
        "automation": [
            {"parameter": "brightness", "sample": 0, "value": 0.2},
            {"parameter": "brightness", "sample": 8_000, "value": 0.9},
        ],
        "note_expression": [
            {
                "note_id": "n1",
                "pitch": 60,
                "velocity": 90,
                "start_sample": 0,
                "end_sample": 12_000,
                "expression": [
                    {"parameter": "pitch", "position": 0.0, "value": 0.0},
                    {"parameter": "pitch", "position": 1.0, "value": 50.0},
                    {"parameter": "pressure", "position": 0.0, "value": 0.4},
                    {"parameter": "pressure", "position": 1.0, "value": 1.0},
                ],
            }
        ],
    }


def test_reference_host_scans_and_renders_deterministically(tmp_path: Path) -> None:
    scan = scan_reference_plugin(reference_manifest(), "clap")
    assert scan["name"] == "LedgerLine Reference Instrument"
    assert "clap-note-expression" in scan["note_ports"][0]["dialects"]
    request = _request(tmp_path)
    render_reference_plugin(request)
    first = hashlib.sha256((tmp_path / "render.wav").read_bytes()).hexdigest()
    render_reference_plugin(request)
    second = hashlib.sha256((tmp_path / "render.wav").read_bytes()).hexdigest()
    assert first == second
    with wave.open(str(tmp_path / "render.wav"), "rb") as stream:
        assert stream.getnchannels() == 2
        assert stream.getframerate() == 24_000


def test_audio_regression_exact_and_tolerant_check(tmp_path: Path) -> None:
    request = _request(tmp_path)
    render_reference_plugin(request)
    baseline = tmp_path / "golden.json"
    record_audio_baseline(tmp_path / "render.wav", baseline, exact=True)
    report = check_audio_baseline(tmp_path / "render.wav", baseline, exact=True)
    assert report["pass"] is True
    stored = json.loads(baseline.read_text(encoding="utf-8"))
    stored["fingerprint"]["rms_db"] += 3
    baseline.write_text(json.dumps(stored), encoding="utf-8")
    assert check_audio_baseline(tmp_path / "render.wav", baseline)["pass"] is False
