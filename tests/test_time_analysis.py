from __future__ import annotations

import numpy as np

from ledgerline.time_analysis import _analyze_samples


def test_time_analysis_finds_silence_brightness_and_active_parts() -> None:
    sample_rate = 8_000
    time = np.arange(sample_rate * 4) / sample_rate
    samples = np.zeros(sample_rate * 4, dtype=np.float32)
    samples[sample_rate * 2 :] = 0.2 * np.sin(2 * np.pi * 1000 * time[: sample_rate * 2])
    stems = {"lead": samples.copy()}
    report = _analyze_samples(
        samples,
        sample_rate,
        stems,
        window_seconds=0.5,
        silence_threshold_db=-60.0,
    )
    assert any(item["code"] == "long_silence" for item in report["issues"])
    assert report["windows"][-1]["spectral_centroid_hz"] > 900
    assert report["windows"][-1]["active_parts"] == ["lead"]
