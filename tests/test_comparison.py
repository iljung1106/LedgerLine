from __future__ import annotations

import numpy as np

from ledgerline.comparison import _compare_samples


def test_audio_comparison_reports_local_and_global_change() -> None:
    sample_rate = 8_000
    time = np.arange(sample_rate * 2) / sample_rate
    before = 0.1 * np.sin(2 * np.pi * 220 * time)
    after = before.copy()
    after[sample_rate:] += 0.05 * np.sin(2 * np.pi * 1200 * time[:sample_rate])
    report = _compare_samples(
        before,
        after,
        sample_rate,
        window_seconds=0.5,
        loudness_match=True,
    )
    assert report["waveform_correlation"] < 1.0
    assert report["largest_changes"][0]["start_seconds"] >= 1.0
    assert report["largest_changes"][0]["after_centroid_hz"] > 220
