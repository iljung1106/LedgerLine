from __future__ import annotations

import json
import math
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from ledgerline.audio import resolve_ffmpeg
from ledgerline.diagnostics import CapabilityError, Diagnostic


def analyze_project_timeline(
    project: str | Path,
    *,
    audio: str | Path | None = None,
    ffmpeg: str | Path | None = None,
    window_seconds: float = 0.5,
    silence_threshold_db: float = -60.0,
    timeout: int = 180,
) -> dict:
    root = Path(project).resolve()
    source = Path(audio).resolve() if audio else _default_audio(root)
    if not 0.05 <= window_seconds <= 10.0:
        raise ValueError("window_seconds must be between 0.05 and 10")
    ffmpeg_path = resolve_ffmpeg(ffmpeg)
    stems_dir = root / "build" / "stems"
    stem_paths = sorted(stems_dir.glob("*.wav")) if stems_dir.is_dir() else []
    with tempfile.TemporaryDirectory(prefix="ledgerline-analysis-") as temporary:
        temp = Path(temporary)
        samples, sample_rate = _decode_float_mono(source, ffmpeg_path, temp / "master.f32", timeout)
        stem_samples = {
            path.stem: _decode_float_mono(path, ffmpeg_path, temp / f"stem-{index}.f32", timeout)[0]
            for index, path in enumerate(stem_paths)
        }
        report = _analyze_samples(
            samples,
            sample_rate,
            stem_samples,
            window_seconds=window_seconds,
            silence_threshold_db=silence_threshold_db,
        )
    result = {
        "schema_version": "1",
        "status": "ok",
        "project": str(root),
        "audio": str(source),
        **report,
    }
    output = root / "build" / "timeline-analysis.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def _analyze_samples(
    samples: np.ndarray,
    sample_rate: int,
    stems: dict[str, np.ndarray],
    *,
    window_seconds: float,
    silence_threshold_db: float,
) -> dict:
    window_size = max(1, round(window_seconds * sample_rate))
    frequencies = np.fft.rfftfreq(window_size, d=1.0 / sample_rate)
    windows: list[dict] = []
    for index, start in enumerate(range(0, len(samples), window_size)):
        frame = samples[start : start + window_size]
        if len(frame) < window_size:
            frame = np.pad(frame, (0, window_size - len(frame)))
        rms = float(np.sqrt(np.mean(frame * frame)))
        peak = float(np.max(np.abs(frame)))
        spectrum = np.abs(np.fft.rfft(frame * np.hanning(window_size)))
        magnitude = float(np.sum(spectrum))
        centroid = float(np.sum(frequencies * spectrum) / magnitude) if magnitude else 0.0
        rms_db = _amplitude_db(rms)
        peak_db = _amplitude_db(peak)
        active_parts = []
        part_levels = {}
        for part, values in stems.items():
            stem_frame = values[start : start + window_size]
            level = _amplitude_db(
                float(np.sqrt(np.mean(stem_frame * stem_frame))) if len(stem_frame) else 0.0
            )
            part_levels[part] = level
            if level > silence_threshold_db:
                active_parts.append(part)
        windows.append(
            {
                "index": index,
                "start_seconds": start / sample_rate,
                "end_seconds": min(len(samples), start + window_size) / sample_rate,
                "rms_dbfs": rms_db,
                "peak_dbfs": peak_db,
                "crest_db": peak_db - rms_db if rms > 0 else 0.0,
                "spectral_centroid_hz": centroid,
                "active_parts": active_parts,
                "part_rms_dbfs": part_levels,
            }
        )
    issues = _detect_issues(windows, window_seconds, silence_threshold_db)
    return {
        "sample_rate": sample_rate,
        "duration_seconds": len(samples) / sample_rate,
        "window_seconds": window_seconds,
        "windows": windows,
        "issues": issues,
    }


def _detect_issues(windows: list[dict], window_seconds: float, silence_db: float) -> list[dict]:
    issues: list[dict] = []
    _append_runs(
        issues,
        windows,
        lambda item: item["rms_dbfs"] <= silence_db,
        minimum_seconds=2.0,
        window_seconds=window_seconds,
        code="long_silence",
    )
    audible = [item for item in windows if item["rms_dbfs"] > silence_db]
    if audible:
        median_level = float(np.median([item["rms_dbfs"] for item in audible]))
        median_centroid = float(np.median([item["spectral_centroid_hz"] for item in audible]))
        for item in audible:
            if item["rms_dbfs"] > median_level + 9.0:
                issues.append(
                    _window_issue("loudness_spike", item, item["rms_dbfs"] - median_level)
                )
            if median_centroid > 0 and item["spectral_centroid_hz"] > median_centroid * 1.8:
                issues.append(
                    _window_issue(
                        "brightness_spike",
                        item,
                        item["spectral_centroid_hz"] / median_centroid,
                    )
                )
            if item["crest_db"] > 20.0:
                issues.append(_window_issue("transient_spike", item, item["crest_db"]))
    return issues


def _append_runs(
    issues: list[dict],
    windows: list[dict],
    predicate,
    *,
    minimum_seconds: float,
    window_seconds: float,
    code: str,
) -> None:
    start = None
    for index in range(len(windows) + 1):
        matched = index < len(windows) and predicate(windows[index])
        if matched and start is None:
            start = index
        if not matched and start is not None:
            duration = windows[index - 1]["end_seconds"] - windows[start]["start_seconds"]
            if duration >= minimum_seconds:
                issues.append(
                    {
                        "code": code,
                        "start_seconds": windows[start]["start_seconds"],
                        "end_seconds": windows[index - 1]["end_seconds"],
                        "duration_seconds": duration,
                    }
                )
            start = None


def _window_issue(code: str, window: dict, amount: float) -> dict:
    return {
        "code": code,
        "start_seconds": window["start_seconds"],
        "end_seconds": window["end_seconds"],
        "amount": amount,
        "active_parts": window["active_parts"],
    }


def _decode_float_mono(
    source: Path, ffmpeg: Path, output: Path, timeout: int
) -> tuple[np.ndarray, int]:
    sample_rate = 48_000
    completed = subprocess.run(
        [
            str(ffmpeg),
            "-hide_banner",
            "-y",
            "-i",
            str(source),
            "-ac",
            "1",
            "-ar",
            str(sample_rate),
            "-f",
            "f32le",
            str(output),
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
        shell=False,
    )
    if completed.returncode != 0 or not output.is_file():
        raise CapabilityError(
            "audio decode for timeline analysis failed",
            [Diagnostic("error", "analysis.decode_failed", str(source), completed.stderr[-2000:])],
        )
    return np.fromfile(output, dtype="<f4"), sample_rate


def _default_audio(root: Path) -> Path:
    candidates = [root / "build" / "mix.wav", root / "build" / "preview.wav"]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise CapabilityError(
        "no rendered audio is available for timeline analysis",
        [Diagnostic("error", "analysis.audio_missing", str(root / "build"), "Render first.")],
    )


def _amplitude_db(value: float) -> float:
    return 20.0 * math.log10(max(value, 1e-12))
