from __future__ import annotations

import struct
import sys
from pathlib import Path

import pytest

from ledgerline.compiler import compile_project
from ledgerline.diagnostics import CapabilityError
from ledgerline.render import render_project
from ledgerline.soundfont import read_presets


def _chunk(chunk_id: bytes, data: bytes) -> bytes:
    padding = b"\0" if len(data) % 2 else b""
    return chunk_id + struct.pack("<I", len(data)) + data + padding


def _phdr(name: str, program: int, bank: int) -> bytes:
    encoded = name.encode("ascii")[:19].ljust(20, b"\0")
    return struct.pack("<20sHHHIII", encoded, program, bank, 0, 0, 0, 0)


def test_soundfont_preset_table_is_read_before_render(tmp_path: Path) -> None:
    phdr = _phdr("Piano", 0, 0) + _phdr("Cello", 42, 0) + _phdr("EOP", 0, 0)
    pdta = b"pdta" + _chunk(b"phdr", phdr)
    body = b"sfbk" + _chunk(b"LIST", pdta)
    soundfont = tmp_path / "fixture.sf2"
    soundfont.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    presets = read_presets(soundfont)
    assert [(item.bank, item.program, item.name) for item in presets] == [
        (0, 0, "Piano"),
        (0, 42, "Cello"),
    ]


def test_render_fails_before_synthesis_when_a_preset_is_missing(
    example_project: Path, tmp_path: Path
) -> None:
    compile_project(example_project)
    phdr = _phdr("Piano", 0, 0) + _phdr("EOP", 0, 0)
    pdta = b"pdta" + _chunk(b"phdr", phdr)
    body = b"sfbk" + _chunk(b"LIST", pdta)
    soundfont = tmp_path / "piano-only.sf2"
    soundfont.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    with pytest.raises(CapabilityError) as caught:
        render_project(example_project, fluidsynth=sys.executable, soundfont=soundfont)
    assert caught.value.diagnostics[0].code == "render.preset_missing"
