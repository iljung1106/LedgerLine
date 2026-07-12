from __future__ import annotations

import json
import math
import tempfile
from pathlib import Path

import numpy as np

from ledgerline.audio import resolve_ffmpeg
from ledgerline.time_analysis import _decode_float_mono


def compare_audio(
    before: str | Path,
    after: str | Path,
    *,
    output: str | Path | None = None,
    ffmpeg: str | Path | None = None,
    window_seconds: float = 0.5,
    loudness_match: bool = True,
    timeout: int = 180,
) -> dict:
    before_path = Path(before).resolve()
    after_path = Path(after).resolve()
    if not 0.05 <= window_seconds <= 10.0:
        raise ValueError("window_seconds must be between 0.05 and 10")
    with tempfile.TemporaryDirectory(prefix="ledgerline-compare-") as temporary:
        temp = Path(temporary)
        before_samples, sample_rate = _decode_float_mono(
            before_path, resolve_ffmpeg(ffmpeg), temp / "before.f32", timeout
        )
        after_samples, after_rate = _decode_float_mono(
            after_path, resolve_ffmpeg(ffmpeg), temp / "after.f32", timeout
        )
    if after_rate != sample_rate:
        raise ValueError("decoded sample rates do not match")
    metrics = _compare_samples(
        before_samples,
        after_samples,
        sample_rate,
        window_seconds=window_seconds,
        loudness_match=loudness_match,
    )
    report = {
        "schema_version": "1",
        "status": "ok",
        "before": str(before_path),
        "after": str(after_path),
        **metrics,
    }
    output_path = Path(output).resolve() if output else after_path.parent / "comparison-report.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    report["report"] = str(output_path)
    return report


def _compare_samples(
    before: np.ndarray,
    after: np.ndarray,
    sample_rate: int,
    *,
    window_seconds: float,
    loudness_match: bool,
) -> dict:
    before = np.asarray(before, dtype=np.float64)
    after = np.asarray(after, dtype=np.float64)
    before_length = len(before)
    after_length = len(after)
    before_rms = _rms(before)
    after_rms = _rms(after)
    gain_db = 0.0
    if loudness_match and before_rms > 0 and after_rms > 0:
        gain_db = 20.0 * math.log10(before_rms / after_rms)
        after = after * (before_rms / after_rms)
    length = max(len(before), len(after))
    before = np.pad(before, (0, length - len(before)))
    after = np.pad(after, (0, length - len(after)))
    window_size = max(1, round(window_seconds * sample_rate))
    windows = []
    for start in range(0, length, window_size):
        end = min(length, start + window_size)
        first = before[start:end]
        second = after[start:end]
        difference = second - first
        first_rms = _rms(first)
        second_rms = _rms(second)
        windows.append(
            {
                "start_seconds": start / sample_rate,
                "end_seconds": end / sample_rate,
                "before_rms_dbfs": _db(first_rms),
                "after_rms_dbfs": _db(second_rms),
                "level_delta_db": _db(second_rms) - _db(first_rms),
                "difference_rms_dbfs": _db(_rms(difference)),
                "before_centroid_hz": _centroid(first, sample_rate),
                "after_centroid_hz": _centroid(second, sample_rate),
            }
        )
    ranked = sorted(windows, key=lambda item: item["difference_rms_dbfs"], reverse=True)
    correlation = 0.0
    if np.any(before) and np.any(after):
        correlation = float(np.corrcoef(before, after)[0, 1])
        if not math.isfinite(correlation):
            correlation = 0.0
    return {
        "sample_rate": sample_rate,
        "before_duration_seconds": before_length / sample_rate,
        "after_duration_seconds": after_length / sample_rate,
        "loudness_matched": loudness_match,
        "after_match_gain_db": gain_db,
        "waveform_correlation": correlation,
        "difference_rms_dbfs": _db(_rms(after - before)),
        "windows": windows,
        "largest_changes": ranked[:10],
    }


def _rms(values: np.ndarray) -> float:
    return float(np.sqrt(np.mean(values * values))) if len(values) else 0.0


def _db(value: float) -> float:
    return 20.0 * math.log10(max(value, 1e-12))


def _centroid(values: np.ndarray, sample_rate: int) -> float:
    if not len(values) or not np.any(values):
        return 0.0
    spectrum = np.abs(np.fft.rfft(values * np.hanning(len(values))))
    total = float(np.sum(spectrum))
    if total == 0:
        return 0.0
    frequencies = np.fft.rfftfreq(len(values), d=1.0 / sample_rate)
    return float(np.sum(frequencies * spectrum) / total)
