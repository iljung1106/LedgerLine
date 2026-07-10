from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class SoundFontPreset:
    bank: int
    program: int
    name: str


def read_presets(path: str | Path) -> tuple[SoundFontPreset, ...]:
    soundfont = Path(path)
    with soundfont.open("rb") as handle:
        header = handle.read(12)
        if len(header) != 12 or header[:4] != b"RIFF" or header[8:] != b"sfbk":
            raise ValueError(f"not a SoundFont RIFF file: {soundfont}")
        riff_size = struct.unpack_from("<I", header, 4)[0]
        riff_end = min(soundfont.stat().st_size, 8 + riff_size)
        phdr = _find_phdr(handle, 12, riff_end)
    if phdr is None or len(phdr) < 38 or len(phdr) % 38:
        raise ValueError(f"SoundFont has no valid phdr preset table: {soundfont}")
    presets: list[SoundFontPreset] = []
    for offset in range(0, len(phdr), 38):
        name_bytes, program, bank, _, _, _, _ = struct.unpack_from("<20sHHHIII", phdr, offset)
        name = name_bytes.split(b"\0", 1)[0].decode("latin-1", errors="replace").strip()
        if name == "EOP" or offset == len(phdr) - 38:
            break
        presets.append(SoundFontPreset(bank=bank, program=program, name=name))
    return tuple(presets)


def _find_phdr(handle, start: int, end: int) -> bytes | None:
    for chunk_id, data_start, size in _chunks(handle, start, end):
        if chunk_id == b"LIST":
            handle.seek(data_start)
            list_type = handle.read(4)
            if list_type == b"pdta":
                for nested_id, nested_start, nested_size in _chunks(
                    handle, data_start + 4, data_start + size
                ):
                    if nested_id == b"phdr":
                        handle.seek(nested_start)
                        return handle.read(nested_size)
    return None


def _chunks(handle, start: int, end: int):
    position = start
    while position + 8 <= end:
        handle.seek(position)
        header = handle.read(8)
        if len(header) != 8:
            return
        chunk_id, size = struct.unpack("<4sI", header)
        data_start = position + 8
        if data_start + size > end:
            return
        yield chunk_id, data_start, size
        position = data_start + size + (size & 1)
