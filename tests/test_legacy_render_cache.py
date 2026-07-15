from __future__ import annotations

import hashlib
import struct
import subprocess
import wave
from pathlib import Path

import yaml

from ledgerline.build_state import build_state
from ledgerline.compiler import compile_project
from ledgerline.render import render_project


def test_legacy_render_reuses_only_unchanged_parts(
    example_project: Path, tmp_path: Path, monkeypatch
) -> None:
    compile_project(example_project)
    renderer = tmp_path / "fluidsynth.exe"
    ffmpeg = tmp_path / "ffmpeg.exe"
    soundfont = tmp_path / "orchestra.sf2"
    renderer.write_bytes(b"fake-fluidsynth-v1")
    ffmpeg.write_bytes(b"fake-ffmpeg-v1")
    _write_soundfont(soundfont)

    calls: list[tuple[str, str]] = []

    def fake_external(command, **_kwargs):
        if "-F" in command:
            output = Path(command[command.index("-F") + 1])
            midi = Path(command[-1])
            calls.append(("fluidsynth", midi.stem))
            seed = hashlib.sha256(midi.read_bytes()).digest()[0]
        else:
            output = Path(command[-1])
            inputs = [
                Path(command[index + 1])
                for index, item in enumerate(command)
                if item == "-i"
            ]
            calls.append(("ffmpeg", "preview"))
            seed = hashlib.sha256(b"".join(path.read_bytes() for path in inputs)).digest()[0]
        _write_wav(output, sample=seed)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("ledgerline.render.run_external", fake_external)
    options = {
        "fluidsynth": renderer,
        "soundfont": soundfont,
        "ffmpeg": ffmpeg,
    }

    first = render_project(example_project, **options)
    assert calls == [
        ("fluidsynth", "piano"),
        ("fluidsynth", "cello"),
        ("ffmpeg", "preview"),
    ]
    assert _part_cache(first) == {"piano": "miss", "cello": "miss"}

    calls.clear()
    second = render_project(example_project, **options)
    assert calls == [("ffmpeg", "preview")]
    assert _part_cache(second) == {"piano": "hit", "cello": "hit"}

    cello = example_project / "build" / "stems" / "cello.wav"
    cello_before = (cello.stat().st_mtime_ns, hashlib.sha256(cello.read_bytes()).hexdigest())
    preview = example_project / "build" / "preview.wav"
    preview_before = hashlib.sha256(preview.read_bytes()).hexdigest()
    piano_source = example_project / "parts" / "piano.yaml"
    piano = yaml.safe_load(piano_source.read_text(encoding="utf-8"))
    piano["measures"]["3"]["v1"][0]["p"] = "Db5"
    piano_source.write_text(yaml.safe_dump(piano, sort_keys=False), encoding="utf-8")
    compile_project(example_project)

    stale = build_state(example_project)
    assert stale["stages"]["render"]["parts"]["piano"]["status"] == "stale"
    assert stale["stages"]["render"]["parts"]["cello"]["status"] == "ready"
    assert stale["stages"]["render"]["preview"]["status"] == "stale"

    calls.clear()
    third = render_project(example_project, **options)
    assert calls == [("fluidsynth", "piano"), ("ffmpeg", "preview")]
    assert _part_cache(third) == {"piano": "miss", "cello": "hit"}
    cello_after = (cello.stat().st_mtime_ns, hashlib.sha256(cello.read_bytes()).hexdigest())
    assert cello_after == cello_before
    assert hashlib.sha256(preview.read_bytes()).hexdigest() != preview_before

    automation_path = example_project / "automation.yaml"
    automation = yaml.safe_load(automation_path.read_text(encoding="utf-8"))
    automation["lanes"][0]["points"][1]["value"] = 1.25
    automation_path.write_text(yaml.safe_dump(automation, sort_keys=False), encoding="utf-8")
    compile_project(example_project)

    calls.clear()
    fourth = render_project(example_project, **options)
    assert calls == [("fluidsynth", "cello"), ("ffmpeg", "preview")]
    assert _part_cache(fourth) == {"piano": "hit", "cello": "miss"}


def _part_cache(report: dict) -> dict[str, str]:
    return {
        Path(item["wav"]).stem: item["cache"]
        for item in report["artifacts"]
        if Path(item["wav"]).stem != "preview"
    }


def _write_wav(path: Path, *, sample: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    value = int(sample - 128).to_bytes(2, "little", signed=True)
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(2)
        stream.setsampwidth(2)
        stream.setframerate(48_000)
        stream.writeframes((value * 2) * 960)


def _write_soundfont(path: Path) -> None:
    phdr = _phdr("Piano", 0, 0) + _phdr("Cello", 42, 0) + _phdr("EOP", 0, 0)
    pdta = b"pdta" + _chunk(b"phdr", phdr)
    body = b"sfbk" + _chunk(b"LIST", pdta)
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)


def _chunk(chunk_id: bytes, data: bytes) -> bytes:
    padding = b"\0" if len(data) % 2 else b""
    return chunk_id + struct.pack("<I", len(data)) + data + padding


def _phdr(name: str, program: int, bank: int) -> bytes:
    encoded = name.encode("ascii")[:19].ljust(20, b"\0")
    return struct.pack("<20sHHHIII", encoded, program, bank, 0, 0, 0, 0)
