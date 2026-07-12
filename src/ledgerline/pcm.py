from __future__ import annotations

import wave
from pathlib import Path

import numpy as np


def read_pcm_wav(path: str | Path) -> tuple[np.ndarray, int, int]:
    """Read uncompressed 16/24/32-bit PCM WAV as float64 frames."""
    audio_path = Path(path).resolve(strict=True)
    with wave.open(str(audio_path), "rb") as stream:
        channels = stream.getnchannels()
        sample_rate = stream.getframerate()
        width = stream.getsampwidth()
        compression = stream.getcomptype()
        frames = stream.readframes(stream.getnframes())
    if compression != "NONE" or width not in {2, 3, 4}:
        raise ValueError("analysis requires uncompressed 16/24/32-bit PCM WAV")
    if width == 2:
        integers = np.frombuffer(frames, dtype="<i2").astype(np.int32)
        scale = 2**15
    elif width == 4:
        integers = np.frombuffer(frames, dtype="<i4")
        scale = 2**31
    else:
        raw = np.frombuffer(frames, dtype=np.uint8).reshape(-1, 3)
        integers = (
            raw[:, 0].astype(np.int32)
            | (raw[:, 1].astype(np.int32) << 8)
            | (raw[:, 2].astype(np.int32) << 16)
        )
        integers = np.where(integers & 0x800000, integers - 0x1000000, integers)
        scale = 2**23
    return integers.astype(np.float64).reshape(-1, channels) / scale, sample_rate, width
