from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ledgerline import __version__
from ledgerline.compiler_midi import compile_midi
from ledgerline.compiler_musicxml import compile_musicxml
from ledgerline.project import load_piece


def compile_project(root: str | Path, output: str | Path | None = None) -> dict:
    piece = load_piece(root)
    build = Path(output).resolve() if output else piece.root / "build"
    build.mkdir(parents=True, exist_ok=True)
    parts_dir = build / "parts"
    parts_dir.mkdir(exist_ok=True)

    musicxml_path = build / "score.musicxml"
    midi_path = build / "score.mid"
    compile_musicxml(piece, musicxml_path)
    compile_midi(piece, midi_path)
    part_paths: list[Path] = []
    for part in piece.parts:
        path = parts_dir / f"{part.id}.mid"
        compile_midi(piece, path, [part.id])
        part_paths.append(path)

    inputs = [piece.root / "piece.yaml", *(part.source_path for part in piece.parts)]
    mix_path = piece.root / "mix.yaml"
    if mix_path.is_file():
        inputs.append(mix_path)
    outputs = [musicxml_path, midi_path, *part_paths]
    manifest = {
        "schema_version": "1",
        "tool": {"name": "ledgerline", "version": __version__},
        "project": str(piece.root),
        "inputs": [_file_record(path, piece.root) for path in inputs],
        "profiles": [_profile_record(piece.profiles[part.profile_id]) for part in piece.parts],
        "outputs": [_file_record(path, build) for path in outputs],
    }
    manifest_path = build / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {
        "status": "ok",
        "project": str(piece.root),
        "build": str(build),
        "musicxml": str(musicxml_path),
        "midi": str(midi_path),
        "part_midis": [str(path) for path in part_paths],
        "manifest": str(manifest_path),
    }


def _file_record(path: Path, relative_to: Path) -> dict[str, str | int]:
    data = path.read_bytes()
    return {
        "path": str(path.relative_to(relative_to)).replace("\\", "/"),
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def _profile_record(profile) -> dict:
    payload = {
        "id": profile.id,
        "name": profile.name,
        "family": profile.family,
        "absolute_range": [str(profile.absolute_low), str(profile.absolute_high)],
        "comfortable_range": [str(profile.comfortable_low), str(profile.comfortable_high)],
        "transposition": profile.transposition,
        "midi": {
            "bank_msb": profile.bank_msb,
            "bank_lsb": profile.bank_lsb,
            "program": profile.program,
        },
        "articulations": sorted(profile.articulations),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return {**payload, "sha256": hashlib.sha256(canonical).hexdigest()}
