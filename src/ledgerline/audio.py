from __future__ import annotations

import json
import math
import re
import subprocess
from pathlib import Path

from ledgerline.diagnostics import CapabilityError, Diagnostic
from ledgerline.environment import doctor

LOUDNORM_JSON_RE = re.compile(r"\{\s*\"input_i\".*?\}", re.DOTALL)


def resolve_ffmpeg(override: str | Path | None = None) -> Path:
    if override:
        path = Path(override).expanduser().resolve()
        if path.is_file():
            return path
    for renderer in doctor()["renderers"]:
        if renderer["id"] == "ffmpeg":
            return Path(renderer["path"])
    raise CapabilityError(
        "FFmpeg is unavailable",
        [
            Diagnostic(
                "error",
                "audio.ffmpeg_missing",
                "environment",
                "Pass --ffmpeg or install a portable audited FFmpeg build.",
            )
        ],
    )


def measure_audio(
    path: str | Path,
    *,
    ffmpeg: str | Path | None = None,
    timeout: int = 180,
    target_lufs: float = -14.0,
    true_peak_dbtp: float = -1.0,
    loudness_range_lu: float = 11.0,
) -> dict:
    audio_path = Path(path).resolve()
    if not audio_path.is_file():
        raise CapabilityError(
            "audio file is missing",
            [Diagnostic("error", "audio.file_missing", str(audio_path), "File does not exist.")],
        )
    ffmpeg_path = resolve_ffmpeg(ffmpeg)
    stream = _probe_audio(audio_path, ffmpeg_path, timeout)
    command = [
        str(ffmpeg_path),
        "-hide_banner",
        "-nostats",
        "-i",
        str(audio_path),
        "-af",
        (f"loudnorm=I={target_lufs}:TP={true_peak_dbtp}:LRA={loudness_range_lu}:print_format=json"),
        "-f",
        "null",
        "-",
    ]
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
        shell=False,
    )
    matches = LOUDNORM_JSON_RE.findall(completed.stderr)
    if completed.returncode != 0 or not matches:
        raise CapabilityError(
            "FFmpeg loudness analysis failed",
            [
                Diagnostic(
                    "error",
                    "audio.measure_failed",
                    str(audio_path),
                    completed.stderr[-2000:],
                )
            ],
        )
    loudness = json.loads(matches[-1])
    return {
        "schema_version": "1",
        "status": "ok",
        "path": str(audio_path),
        "duration_seconds": stream["duration_seconds"],
        "sample_rate": stream["sample_rate"],
        "channels": stream["channels"],
        "sample_width_bytes": stream["sample_width_bytes"],
        "integrated_lufs": _float_or_none(loudness.get("input_i")),
        "true_peak_dbtp": _float_or_none(loudness.get("input_tp")),
        "loudness_range_lu": _float_or_none(loudness.get("input_lra")),
        "threshold_lufs": _float_or_none(loudness.get("input_thresh")),
        "target_offset_lu": _float_or_none(loudness.get("target_offset")),
    }


def db_to_amplitude(value: float) -> float:
    return math.pow(10.0, value / 20.0)


def pan_coefficients(pan: float) -> tuple[float, float]:
    if not -1.0 <= pan <= 1.0:
        raise ValueError("pan must be between -1 and 1")
    angle = (pan + 1.0) * math.pi / 4.0
    return math.cos(angle), math.sin(angle)


def _float_or_none(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _probe_audio(audio_path: Path, ffmpeg_path: Path, timeout: int) -> dict:
    executable = "ffprobe.exe" if ffmpeg_path.suffix.lower() == ".exe" else "ffprobe"
    ffprobe = ffmpeg_path.with_name(executable)
    if not ffprobe.is_file():
        raise CapabilityError(
            "FFprobe is unavailable next to FFmpeg",
            [
                Diagnostic(
                    "error",
                    "audio.ffprobe_missing",
                    str(ffprobe),
                    "Install a complete portable FFmpeg distribution.",
                )
            ],
        )
    command = [
        str(ffprobe),
        "-v",
        "error",
        "-show_streams",
        "-show_format",
        "-of",
        "json",
        str(audio_path),
    ]
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
        shell=False,
    )
    if completed.returncode != 0:
        raise CapabilityError(
            "FFprobe failed",
            [Diagnostic("error", "audio.probe_failed", str(audio_path), completed.stderr[-2000:])],
        )
    payload = json.loads(completed.stdout)
    audio_stream = next(
        (item for item in payload.get("streams", []) if item.get("codec_type") == "audio"), None
    )
    if audio_stream is None:
        raise CapabilityError(
            "No audio stream was found",
            [Diagnostic("error", "audio.stream_missing", str(audio_path), "No audio stream.")],
        )
    bits = int(audio_stream.get("bits_per_raw_sample") or audio_stream.get("bits_per_sample") or 0)
    duration = audio_stream.get("duration") or payload.get("format", {}).get("duration")
    return {
        "duration_seconds": float(duration),
        "sample_rate": int(audio_stream["sample_rate"]),
        "channels": int(audio_stream["channels"]),
        "sample_width_bytes": math.ceil(bits / 8) if bits else None,
    }
