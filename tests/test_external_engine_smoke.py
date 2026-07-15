from __future__ import annotations

import importlib.util
import subprocess
import sys
import wave
from pathlib import Path


def _load_smoke_module():
    path = Path(__file__).parents[1] / "scripts" / "external_engine_smoke.py"
    spec = importlib.util.spec_from_file_location("external_engine_smoke", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


smoke = _load_smoke_module()


def _write_wave(path: Path, sample_rate: int = 48_000) -> None:
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(2)
        stream.setsampwidth(2)
        stream.setframerate(sample_rate)
        stream.writeframes(b"\0\0\0\0" * 512)


class FakeRunner:
    def __init__(self, *, fail_token: str | None = None) -> None:
        self.calls: list[tuple[list[str], dict]] = []
        self.fail_token = fail_token

    def __call__(self, command: list[str], **kwargs):
        self.calls.append((command, kwargs))
        if self.fail_token and self.fail_token in command:
            return subprocess.CompletedProcess(command, 7, "", "deliberate fake failure")
        if "-F" in command:
            _write_wave(Path(command[command.index("-F") + 1]))
        if "--wav" in command:
            _write_wave(Path(command[command.index("--wav") + 1]))
        return subprocess.CompletedProcess(command, 0, "fake tool 1.0", "")


def _files(tmp_path: Path) -> dict[str, Path]:
    paths = {
        "fluidsynth": tmp_path / "fluidsynth.exe",
        "soundfont": tmp_path / "fixture.sf2",
        "ffmpeg": tmp_path / "ffmpeg.exe",
        "sfizz": tmp_path / "sfizz_render.exe",
        "sfz": tmp_path / "fixture.sfz",
    }
    for key, path in paths.items():
        path.write_bytes(b"fake" if key != "sfz" else b"<region> sample=fake.wav\n")
    return paths


def test_external_engine_smoke_skips_clearly_when_unconfigured() -> None:
    def unexpected_runner(*_args, **_kwargs):
        raise AssertionError("no process should run without explicit configuration")

    report = smoke.run_smoke(
        smoke.SmokeConfig(None, None, None),
        runner=unexpected_runner,
    )
    assert report["status"] == "skipped"
    assert report["reason"] == "required_configuration_missing"
    assert report["policy"]["downloads_attempted"] is False
    assert report["policy"]["substitutions_attempted"] is False


def test_external_engine_smoke_fails_closed_on_partial_configuration(tmp_path: Path) -> None:
    files = _files(tmp_path)
    runner = FakeRunner()
    report = smoke.run_smoke(
        smoke.SmokeConfig(files["fluidsynth"], None, files["ffmpeg"]),
        runner=runner,
    )
    assert report["status"] == "failed"
    assert report["reason"] == "invalid_configuration"
    assert "soundfont is missing" in report["checks"][0]["detail"]
    assert runner.calls == []


def test_external_engine_smoke_renders_and_decodes_with_fake_tools(tmp_path: Path) -> None:
    files = _files(tmp_path)
    output = tmp_path / "retained"
    runner = FakeRunner()
    report = smoke.run_smoke(
        smoke.SmokeConfig(
            files["fluidsynth"],
            files["soundfont"],
            files["ffmpeg"],
            sfizz=files["sfizz"],
            sfz=files["sfz"],
            keep_output=output,
        ),
        runner=runner,
    )
    assert report["status"] == "passed"
    statuses = {item["id"]: item["status"] for item in report["checks"]}
    assert statuses["fluidsynth-render"] == "passed"
    assert statuses["fluidsynth-decode"] == "passed"
    assert statuses["sfizz-render"] == "passed"
    assert statuses["sfizz-decode"] == "passed"
    assert set(report["artifacts"]) == {"fluidsynth_wav", "sfizz_wav"}
    assert (output / "smoke.mid").is_file()
    assert all(call[1]["shell"] is False for call in runner.calls)


def test_external_engine_smoke_surfaces_process_failure(tmp_path: Path) -> None:
    files = _files(tmp_path)
    report = smoke.run_smoke(
        smoke.SmokeConfig(files["fluidsynth"], files["soundfont"], files["ffmpeg"]),
        runner=FakeRunner(fail_token="--version"),
    )
    assert report["status"] == "failed"
    checks = {item["id"]: item for item in report["checks"]}
    assert checks["fluidsynth-version"]["status"] == "failed"
    assert checks["fluidsynth-render"]["status"] == "skipped"
