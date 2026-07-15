from __future__ import annotations

import json
import struct
import wave
from pathlib import Path

import pytest

from ledgerline.build_state import (
    archive_media_checkpoint,
    authored_revision,
    build_state,
    ensure_media_sidecar,
    file_identity,
    record_mix,
    record_refinement,
    record_render,
)
from ledgerline.compiler import compile_project
from ledgerline.refinement import build_refinement_report
from ledgerline.studio_model import build_studio_model


def test_same_length_unreceipted_audio_is_stale_not_aligned(example_project: Path) -> None:
    compile_project(example_project)
    build = example_project / "build"
    for name in ("piano", "cello"):
        _write_wav(build / "stems" / f"{name}.wav")
    _write_wav(build / "preview.wav")

    model = build_studio_model(example_project)

    assert model["build"]["stages"]["compile"]["status"] == "ready"
    assert model["build"]["stages"]["render"]["status"] == "stale"
    assert model["media"]["binding"] == "stale"
    assert all(stem["status"] == "stale" for stem in model["media"]["stems"])


def test_render_and_mix_receipts_track_engine_and_invalidation(example_project: Path) -> None:
    compile_project(example_project)
    build = example_project / "build"
    renderer = example_project / "fake-fluidsynth.exe"
    soundfont = example_project / "fake.sf2"
    renderer.write_bytes(b"renderer-v1")
    soundfont.write_bytes(b"instrument-v1")
    artifacts = []
    for name, relative in (
        ("preview", build / "preview.wav"),
        ("piano", build / "stems" / "piano.wav"),
        ("cello", build / "stems" / "cello.wav"),
    ):
        _write_wav(relative)
        artifacts.append(
            {
                "midi": str(build / ("score.mid" if name == "preview" else f"parts/{name}.mid")),
                "wav": str(relative),
                "bytes": relative.stat().st_size,
                "sha256": file_identity(relative)["sha256"],
            }
        )
    render_report = {
        "schema_version": "1",
        "status": "ok",
        "source_revision": authored_revision(example_project),
        "renderer": str(renderer),
        "soundfont": file_identity(soundfont),
        "sample_rate": 48_000,
        "artifacts": artifacts,
    }
    rendered = record_render(example_project, render_report)
    assert rendered["stages"]["render"]["status"] == "ready"
    assert rendered["stages"]["render"]["preview"]["status"] == "ready"
    assert rendered["engines"]["piano"]["engine"] == "fluidsynth"
    assert rendered["engines"]["piano"]["renderer"]["sha256"] == file_identity(renderer)[
        "sha256"
    ]

    mix = build / "mix.wav"
    _write_wav(mix)
    mixed = record_mix(
        example_project,
        {
            "schema_version": "2",
            "status": "ok",
            "source_revision": authored_revision(example_project),
            "output": str(mix),
            "ffmpeg": None,
        },
    )
    assert mixed["stages"]["mix"]["status"] == "ready"

    mix_config = example_project / "mix.yaml"
    mix_config.write_text(mix_config.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    invalidated = build_state(example_project)
    assert invalidated["stages"]["compile"]["status"] == "ready"
    assert invalidated["stages"]["render"]["status"] == "ready"
    assert invalidated["stages"]["mix"]["status"] == "stale"


def test_revision_matched_master_can_be_archived_for_studio_ab(example_project: Path) -> None:
    compile_project(example_project)
    build = example_project / "build"
    renderer = example_project / "renderer.exe"
    soundfont = example_project / "instrument.sf2"
    renderer.write_bytes(b"renderer")
    soundfont.write_bytes(b"instrument")
    artifacts = []
    for name in ("preview", "piano", "cello"):
        path = build / ("preview.wav" if name == "preview" else f"stems/{name}.wav")
        _write_wav(path)
        artifacts.append(
            {
                "wav": str(path),
                "bytes": path.stat().st_size,
                "sha256": file_identity(path)["sha256"],
            }
        )
    record_render(
        example_project,
        {
            "status": "ok",
            "source_revision": authored_revision(example_project),
            "renderer": str(renderer),
            "soundfont": file_identity(soundfont),
            "artifacts": artifacts,
        },
    )
    master = build / "mix.wav"
    _write_wav(master)
    record_mix(
        example_project,
        {
            "status": "ok",
            "source_revision": authored_revision(example_project),
            "output": str(master),
            "final_measurement": {
                "integrated_lufs": -16.2,
                "true_peak_dbtp": -1.4,
            },
        },
    )

    checkpoint = archive_media_checkpoint(example_project, label="before-refinement")
    assert checkpoint is not None
    assert Path(checkpoint["audio"]["path"]).is_file()
    assert checkpoint["measurement"]["integrated_lufs"] == -16.2
    model = build_studio_model(example_project)
    assert model["media"]["previous_master"]["label"] == "before-refinement"
    assert model["media"]["previous_master"]["measurement"]["true_peak_dbtp"] == -1.4
    assert "?v=" in model["media"]["previous_master"]["url"]


def test_legacy_report_without_source_revision_is_not_adopted(example_project: Path) -> None:
    compile_project(example_project)
    with pytest.raises(ValueError, match="authored revision"):
        record_render(example_project, {"status": "ok", "artifacts": []})


def test_refinement_report_is_ready_then_stale_after_source_edit(
    example_project: Path,
) -> None:
    assert build_state(example_project)["stages"]["refinement"]["status"] == "missing"
    output = example_project / "build" / "refinement" / "report.json"
    report = build_refinement_report(example_project, output)

    ready = build_state(example_project)
    stage = ready["stages"]["refinement"]
    assert report["authored_revision"] == authored_revision(example_project)
    assert stage["status"] == "ready"
    assert stage["authored_revision"] == report["authored_revision"]
    assert stage["output"] == file_identity(output)
    model = build_studio_model(example_project)
    assert model["refinement"]["status"] == "ready"
    assert model["refinement"]["url"].startswith("/media/refinement/report.json?v=")

    part = example_project / "parts" / "piano.yaml"
    part.write_text(part.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    stale = build_state(example_project)["stages"]["refinement"]
    assert stale["status"] == "stale"
    assert stale["reason"] == "authored source changed after refinement analysis"


def test_damaged_and_legacy_refinement_reports_fail_closed(example_project: Path) -> None:
    output = example_project / "build" / "refinement" / "report.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text('{"schema_version":"1","status":"ok"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="authored revision"):
        record_refinement(example_project, output)
    legacy = build_state(example_project)["stages"]["refinement"]
    assert legacy["status"] == "stale"
    assert legacy["reason"] == "refinement report receipt missing"

    build_refinement_report(example_project, output)
    output.write_text("{damaged", encoding="utf-8")
    damaged = build_state(example_project)["stages"]["refinement"]
    assert damaged["status"] == "stale"
    assert damaged["reason"] == "refinement report identity changed"


def test_peak_sidecar_is_reused_by_output_sha(example_project: Path, monkeypatch) -> None:
    audio = example_project / "build" / "preview.wav"
    _write_wav(audio)
    first = ensure_media_sidecar(example_project, audio, bins=32)
    sidecar = Path(first["peaks_sidecar"])
    first_contents = json.loads(sidecar.read_text(encoding="utf-8"))

    def fail_if_decoded(_path):
        raise AssertionError("cached WAV should not be decoded again")

    monkeypatch.setattr("ledgerline.pcm.read_pcm_wav", fail_if_decoded)
    second = ensure_media_sidecar(example_project, audio, bins=32)

    assert second["sha256"] == first["sha256"]
    assert second["peaks"] == first_contents["peaks"]
    assert sidecar.name.startswith(first["sha256"])


def test_peak_sidecar_reads_ffmpeg_style_extensible_pcm(example_project: Path) -> None:
    audio = example_project / "build" / "extensible.wav"
    _write_extensible_wav(audio)
    sidecar = ensure_media_sidecar(example_project, audio, bins=16)
    assert sidecar["sample_rate"] == 48_000
    assert sidecar["channels"] == 2
    assert sidecar["sample_width"] == 2
    assert sidecar["peaks"]


def _write_wav(path: Path, *, frames: int = 960, sample_rate: int = 48_000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(2)
        stream.setsampwidth(2)
        stream.setframerate(sample_rate)
        stream.writeframes((b"\x00\x00\x00\x00") * frames)


def _write_extensible_wav(path: Path, *, frames: int = 960) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm_guid = b"\x01\x00\x00\x00\x00\x00\x10\x00\x80\x00\x00\xaa\x00\x38\x9b\x71"
    fmt = struct.pack("<HHIIHHH", 0xFFFE, 2, 48_000, 192_000, 4, 16, 22)
    fmt += struct.pack("<HI", 16, 3) + pcm_guid
    data = (b"\x00\x00\x00\x00") * frames
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt
    body += b"data" + struct.pack("<I", len(data)) + data
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
