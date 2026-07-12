from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np

from ledgerline.pcm import read_pcm_wav


def record_audio_baseline(
    audio: str | Path,
    baseline: str | Path,
    *,
    exact: bool = False,
) -> dict:
    audio_path = Path(audio).resolve(strict=True)
    baseline_path = Path(baseline).resolve()
    fingerprint = audio_fingerprint(audio_path)
    fingerprint["exact_sha256"] = _hash(audio_path) if exact else None
    payload = {
        "schema_version": "1",
        "status": "baseline",
        "source": str(audio_path),
        "fingerprint": fingerprint,
        "default_tolerances": {
            "duration_seconds": 0.02,
            "rms_db": 0.35,
            "peak_db": 0.35,
            "spectral_centroid_hz": 80.0,
            "band_energy_db": 1.0,
            "onset_envelope": 0.08,
            "active_tail_seconds": 0.04,
        },
    }
    baseline_path.parent.mkdir(parents=True, exist_ok=True)
    baseline_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return {"schema_version": "1", "status": "ok", "baseline": str(baseline_path), **payload}


def check_audio_baseline(
    audio: str | Path,
    baseline: str | Path,
    *,
    exact: bool = False,
) -> dict:
    audio_path = Path(audio).resolve(strict=True)
    baseline_path = Path(baseline).resolve(strict=True)
    stored = json.loads(baseline_path.read_text(encoding="utf-8"))
    expected = stored["fingerprint"]
    actual = audio_fingerprint(audio_path)
    tolerances = stored["default_tolerances"]
    checks: list[dict[str, Any]] = []
    for field in (
        "duration_seconds",
        "rms_db",
        "peak_db",
        "spectral_centroid_hz",
        "active_tail_seconds",
    ):
        delta = abs(float(actual[field]) - float(expected[field]))
        checks.append(
            {
                "metric": field,
                "delta": delta,
                "tolerance": tolerances[field],
                "pass": delta <= tolerances[field],
            }
        )
    for field in ("band_energy_db", "onset_envelope"):
        left = np.array(expected[field], dtype=np.float64)
        right = np.array(actual[field], dtype=np.float64)
        if len(left) != len(right):
            delta = float("inf")
        else:
            delta = float(np.max(np.abs(left - right))) if len(left) else 0.0
        checks.append(
            {
                "metric": field,
                "delta": delta,
                "tolerance": tolerances[field],
                "pass": delta <= tolerances[field],
            }
        )
    if exact:
        digest = expected.get("exact_sha256")
        checks.append(
            {
                "metric": "exact_sha256",
                "expected": digest,
                "actual": _hash(audio_path),
                "pass": bool(digest) and digest == _hash(audio_path),
            }
        )
    passed = all(item["pass"] for item in checks)
    return {
        "schema_version": "1",
        "status": "ok" if passed else "failed",
        "pass": passed,
        "audio": str(audio_path),
        "baseline": str(baseline_path),
        "checks": checks,
        "fingerprint": actual,
    }


def audio_fingerprint(path: str | Path) -> dict[str, Any]:
    audio_path = Path(path).resolve(strict=True)
    frames, sample_rate, width = read_pcm_wav(audio_path)
    channels = frames.shape[1]
    data = frames.mean(axis=1)
    if len(data) == 0:
        raise ValueError("audio file is empty")
    rms = math.sqrt(float(np.mean(data * data)))
    peak = float(np.max(np.abs(data)))
    window = np.hanning(len(data))
    spectrum = np.abs(np.fft.rfft(data * window)) + 1e-12
    frequencies = np.fft.rfftfreq(len(data), 1 / sample_rate)
    centroid = float(np.sum(frequencies * spectrum) / np.sum(spectrum))
    bands = ((20, 200), (200, 1_000), (1_000, 5_000), (5_000, min(20_000, sample_rate / 2)))
    band_energy = []
    power = spectrum * spectrum
    for low, high in bands:
        mask = (frequencies >= low) & (frequencies < high)
        value = float(np.mean(power[mask])) if np.any(mask) else 1e-12
        band_energy.append(round(10 * math.log10(max(value, 1e-12)), 5))
    frame = max(1, round(sample_rate * 0.05))
    frame_rms = [
        math.sqrt(float(np.mean(data[index : index + frame] ** 2)))
        for index in range(0, len(data), frame)
    ]
    onset = np.maximum(0.0, np.diff([0.0, *frame_rms]))
    if float(np.max(onset)) > 0:
        onset /= float(np.max(onset))
    # Fixed 32-value summary avoids duration-sensitive golden files.
    source_x = np.linspace(0, 1, len(onset))
    summary = np.interp(np.linspace(0, 1, 32), source_x, onset)
    active = np.flatnonzero(np.abs(data) > 10 ** (-60 / 20))
    active_tail = (
        (len(data) - 1 - int(active[-1])) / sample_rate if len(active) else len(data) / sample_rate
    )
    return {
        "sample_rate": sample_rate,
        "channels": channels,
        "sample_width_bytes": width,
        "duration_seconds": round(len(data) / sample_rate, 8),
        "rms_db": round(20 * math.log10(max(rms, 1e-12)), 5),
        "peak_db": round(20 * math.log10(max(peak, 1e-12)), 5),
        "spectral_centroid_hz": round(centroid, 5),
        "band_energy_db": band_energy,
        "onset_envelope": [round(float(value), 5) for value in summary],
        "active_tail_seconds": round(active_tail, 8),
    }


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
