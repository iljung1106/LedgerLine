from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import mido

Runner = Callable[..., Any]


@dataclass(frozen=True, slots=True)
class SmokeConfig:
    fluidsynth: Path | None
    soundfont: Path | None
    ffmpeg: Path | None
    sfizz: Path | None = None
    sfz: Path | None = None
    bank_msb: int = 0
    bank_lsb: int = 0
    program: int = 0
    sample_rate: int = 48_000
    timeout: int = 30
    require_config: bool = False
    keep_output: Path | None = None


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        print(
            json.dumps(
                {
                    "schema_version": "1",
                    "status": "failed",
                    "reason": "invalid_arguments",
                    "detail": message,
                },
                indent=2,
            )
        )
        raise SystemExit(2)


def build_parser() -> argparse.ArgumentParser:
    parser = JsonArgumentParser(
        description=(
            "Smoke-test explicitly configured FluidSynth, SoundFont, FFmpeg, and optional "
            "sfizz/SFZ paths without downloading or substituting assets."
        )
    )
    parser.add_argument("--fluidsynth", type=Path)
    parser.add_argument("--soundfont", type=Path)
    parser.add_argument("--ffmpeg", type=Path)
    parser.add_argument("--sfizz", type=Path)
    parser.add_argument("--sfz", type=Path)
    parser.add_argument("--bank-msb", type=int, default=0)
    parser.add_argument("--bank-lsb", type=int, default=0)
    parser.add_argument("--program", type=int, default=0)
    parser.add_argument("--sample-rate", type=int, default=48_000)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--require-config", action="store_true")
    parser.add_argument("--keep-output", type=Path)
    return parser


def config_from_args(args: argparse.Namespace) -> SmokeConfig:
    return SmokeConfig(
        fluidsynth=_argument_or_env(args.fluidsynth, "LEDGERLINE_FLUIDSYNTH"),
        soundfont=_argument_or_env(args.soundfont, "LEDGERLINE_SOUNDFONT"),
        ffmpeg=_argument_or_env(args.ffmpeg, "LEDGERLINE_FFMPEG"),
        sfizz=_argument_or_env(args.sfizz, "LEDGERLINE_SFIZZ"),
        sfz=_argument_or_env(args.sfz, "LEDGERLINE_SFZ"),
        bank_msb=args.bank_msb,
        bank_lsb=args.bank_lsb,
        program=args.program,
        sample_rate=args.sample_rate,
        timeout=args.timeout,
        require_config=args.require_config,
        keep_output=args.keep_output.expanduser().resolve() if args.keep_output else None,
    )


def run_smoke(
    config: SmokeConfig,
    *,
    runner: Runner = subprocess.run,
) -> dict[str, Any]:
    configured = {
        "fluidsynth": _display(config.fluidsynth),
        "soundfont": _display(config.soundfont),
        "ffmpeg": _display(config.ffmpeg),
        "sfizz": _display(config.sfizz),
        "sfz": _display(config.sfz),
        "bank_msb": config.bank_msb,
        "bank_lsb": config.bank_lsb,
        "program": config.program,
        "sample_rate": config.sample_rate,
    }
    report: dict[str, Any] = {
        "schema_version": "1",
        "status": "failed",
        "configuration": configured,
        "checks": [],
        "artifacts": {},
        "policy": {
            "downloads_attempted": False,
            "substitutions_attempted": False,
            "paths_must_be_explicit_args_or_environment": True,
        },
    }
    checks: list[dict[str, Any]] = report["checks"]
    required = (config.fluidsynth, config.soundfont, config.ffmpeg)
    if not any((*required, config.sfizz, config.sfz)):
        status = "failed" if config.require_config else "skipped"
        checks.append(
            _check(
                "configuration",
                status,
                "FluidSynth, SoundFont, and FFmpeg were not configured.",
            )
        )
        checks.append(
            _check("sfizz-configuration", "skipped", "Optional sfizz was not configured.")
        )
        report["status"] = status
        report["reason"] = "required_configuration_missing"
        return report

    errors = _configuration_errors(config)
    if errors:
        checks.append(_check("configuration", "failed", "; ".join(errors)))
        report["reason"] = "invalid_configuration"
        return report
    checks.append(_check("configuration", "passed", "All required paths are explicit and valid."))
    report["assets"] = {
        "soundfont": _identity(config.soundfont),
        "sfz": _identity(config.sfz) if config.sfz else None,
    }

    output_context: Any
    if config.keep_output is not None:
        config.keep_output.mkdir(parents=True, exist_ok=True)
        output_context = nullcontext(str(config.keep_output))
    else:
        output_context = tempfile.TemporaryDirectory(prefix="ledgerline-engine-smoke-")

    with output_context as raw_output:
        output = Path(raw_output).resolve()
        midi = output / "smoke.mid"
        _write_midi(midi, config)

        fluid_version = _run_command(
            "fluidsynth-version",
            [str(config.fluidsynth), "--version"],
            checks,
            runner,
            config,
            output,
        )
        ffmpeg_version = _run_command(
            "ffmpeg-version",
            [str(config.ffmpeg), "-version"],
            checks,
            runner,
            config,
            output,
        )
        fluid_wav = output / "fluidsynth-smoke.wav"
        fluid_rendered = False
        if fluid_version and ffmpeg_version:
            fluid_rendered = _run_command(
                "fluidsynth-render",
                [
                    str(config.fluidsynth),
                    "-ni",
                    "-q",
                    "-r",
                    str(config.sample_rate),
                    "-T",
                    "wav",
                    "-F",
                    str(fluid_wav),
                    str(config.soundfont),
                    str(midi),
                ],
                checks,
                runner,
                config,
                output,
                expected_output=fluid_wav,
            )
        else:
            checks.append(_check("fluidsynth-render", "skipped", "A version probe failed."))
        if fluid_rendered:
            _decode_check(
                "fluidsynth-decode", config.ffmpeg, fluid_wav, checks, runner, config, output
            )
            report["artifacts"]["fluidsynth_wav"] = _audio_identity(fluid_wav)
        else:
            checks.append(_check("fluidsynth-decode", "skipped", "No FluidSynth WAV was produced."))

        if config.sfizz is None:
            checks.append(
                _check("sfizz-configuration", "skipped", "Optional sfizz was not configured.")
            )
        else:
            checks.append(_check("sfizz-configuration", "passed", "sfizz and SFZ paths are valid."))
            sfizz_version = _run_command(
                "sfizz-version",
                [str(config.sfizz), "--version"],
                checks,
                runner,
                config,
                output,
            )
            sfizz_wav = output / "sfizz-smoke.wav"
            sfizz_rendered = False
            if sfizz_version and ffmpeg_version:
                sfizz_rendered = _run_command(
                    "sfizz-render",
                    [
                        str(config.sfizz),
                        "--wav",
                        str(sfizz_wav),
                        "--sfz",
                        str(config.sfz),
                        "--midi",
                        str(midi),
                        "--samplerate",
                        str(config.sample_rate),
                        "--blocksize",
                        "512",
                    ],
                    checks,
                    runner,
                    config,
                    output,
                    expected_output=sfizz_wav,
                )
            else:
                checks.append(_check("sfizz-render", "skipped", "A version probe failed."))
            if sfizz_rendered:
                _decode_check(
                    "sfizz-decode", config.ffmpeg, sfizz_wav, checks, runner, config, output
                )
                report["artifacts"]["sfizz_wav"] = _audio_identity(sfizz_wav)
            else:
                checks.append(_check("sfizz-decode", "skipped", "No sfizz WAV was produced."))

        report["output_retained"] = config.keep_output is not None
        if config.keep_output is not None:
            report["output_directory"] = str(output)

    failed = [item for item in checks if item["status"] == "failed"]
    report["status"] = "failed" if failed else "passed"
    if failed:
        report["reason"] = "one_or_more_checks_failed"
    return report


def _configuration_errors(config: SmokeConfig) -> list[str]:
    errors = []
    required = {
        "fluidsynth": config.fluidsynth,
        "soundfont": config.soundfont,
        "ffmpeg": config.ffmpeg,
    }
    for name, path in required.items():
        if path is None:
            errors.append(f"{name} is missing")
        elif not path.is_file():
            errors.append(f"{name} is not a file: {path}")
    if config.soundfont is not None and config.soundfont.suffix.lower() not in {".sf2", ".sf3"}:
        errors.append("soundfont must end in .sf2 or .sf3")
    if (config.sfizz is None) != (config.sfz is None):
        errors.append("sfizz and sfz must be configured together")
    if config.sfizz is not None and not config.sfizz.is_file():
        errors.append(f"sfizz is not a file: {config.sfizz}")
    if config.sfz is not None:
        if not config.sfz.is_file():
            errors.append(f"sfz is not a file: {config.sfz}")
        elif config.sfz.suffix.lower() != ".sfz":
            errors.append("sfz instrument must end in .sfz")
    for name, value, minimum, maximum in (
        ("bank_msb", config.bank_msb, 0, 127),
        ("bank_lsb", config.bank_lsb, 0, 127),
        ("program", config.program, 0, 127),
        ("sample_rate", config.sample_rate, 8_000, 384_000),
        ("timeout", config.timeout, 1, 600),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
            errors.append(f"{name} must be an integer between {minimum} and {maximum}")
    return errors


def _write_midi(path: Path, config: SmokeConfig) -> None:
    midi = mido.MidiFile(type=0, ticks_per_beat=480)
    track = mido.MidiTrack()
    midi.tracks.append(track)
    track.append(mido.MetaMessage("track_name", name="LedgerLine external engine smoke", time=0))
    track.append(mido.Message("control_change", control=0, value=config.bank_msb, time=0))
    track.append(mido.Message("control_change", control=32, value=config.bank_lsb, time=0))
    track.append(mido.Message("program_change", program=config.program, time=0))
    for pitch, velocity in ((60, 72), (64, 68), (67, 70)):
        track.append(mido.Message("note_on", note=pitch, velocity=velocity, time=0))
        track.append(mido.Message("note_off", note=pitch, velocity=0, time=360))
    track.append(mido.MetaMessage("end_of_track", time=120))
    midi.save(path)


def _run_command(
    check_id: str,
    command: list[str],
    checks: list[dict[str, Any]],
    runner: Runner,
    config: SmokeConfig,
    cwd: Path,
    *,
    expected_output: Path | None = None,
) -> bool:
    try:
        completed = runner(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=config.timeout,
            shell=False,
            cwd=str(cwd),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        checks.append(_check(check_id, "failed", f"process error: {exc}"))
        return False
    output = _bounded_output(completed)
    if completed.returncode != 0:
        checks.append(
            _check(check_id, "failed", f"process exited with {completed.returncode}", output=output)
        )
        return False
    if expected_output is not None and not _is_wave_file(expected_output):
        checks.append(
            _check(
                check_id,
                "failed",
                "process did not create a non-empty RIFF/WAVE file",
                output=output,
            )
        )
        return False
    checks.append(_check(check_id, "passed", "process completed successfully", output=output))
    return True


def _decode_check(
    check_id: str,
    ffmpeg: Path | None,
    wav: Path,
    checks: list[dict[str, Any]],
    runner: Runner,
    config: SmokeConfig,
    cwd: Path,
) -> None:
    _run_command(
        check_id,
        [str(ffmpeg), "-v", "error", "-i", str(wav), "-f", "null", "-"],
        checks,
        runner,
        config,
        cwd,
    )


def _argument_or_env(value: Path | None, env_name: str) -> Path | None:
    raw: str | Path | None = value
    if raw is None:
        raw = os.environ.get(env_name)
    if raw is None or not str(raw).strip():
        return None
    return Path(raw).expanduser().resolve()


def _identity(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": _sha256(path)}


def _audio_identity(path: Path) -> dict[str, Any]:
    return {"bytes": path.stat().st_size, "sha256": _sha256(path), "container": "RIFF/WAVE"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_wave_file(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size <= 44:
        return False
    with path.open("rb") as handle:
        header = handle.read(12)
    return header[:4] == b"RIFF" and header[8:] == b"WAVE"


def _bounded_output(completed: Any) -> str:
    combined = f"{getattr(completed, 'stdout', '')}\n{getattr(completed, 'stderr', '')}".strip()
    return combined[-1000:]


def _check(check_id: str, status: str, detail: str, **extra: Any) -> dict[str, Any]:
    return {"id": check_id, "status": status, "detail": detail, **extra}


def _display(path: Path | None) -> str | None:
    return str(path) if path is not None else None


def main() -> int:
    args = build_parser().parse_args()
    report = run_smoke(config_from_args(args))
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 1 if report["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
