from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from ledgerline.project import load_profile

PROJECT_ID = re.compile(r"^[a-z][a-z0-9_-]*$")


@dataclass(frozen=True, slots=True)
class EnsemblePart:
    id: str
    name: str
    profile: str
    pan: float


TEMPLATES: dict[str, tuple[EnsemblePart, ...]] = {
    "piano-solo": (EnsemblePart("piano", "Piano", "starter.acoustic-grand-piano", 0.0),),
    "piano-cello": (
        EnsemblePart("piano", "Piano", "starter.acoustic-grand-piano", 0.15),
        EnsemblePart("cello", "Cello", "starter.cello", -0.2),
    ),
    "string-duo": (
        EnsemblePart("violin", "Violin", "starter.violin", 0.25),
        EnsemblePart("cello", "Cello", "starter.cello", -0.25),
    ),
    "chamber-trio": (
        EnsemblePart("flute", "Flute", "starter.flute", 0.3),
        EnsemblePart("violin", "Violin", "starter.violin", -0.2),
        EnsemblePart("cello", "Cello", "starter.cello", -0.35),
    ),
}


def list_project_templates() -> dict:
    return {
        "schema_version": "1",
        "status": "ok",
        "templates": [
            {
                "id": template_id,
                "parts": [
                    {"id": part.id, "name": part.name, "profile": part.profile} for part in parts
                ],
            }
            for template_id, parts in TEMPLATES.items()
        ],
    }


def initialize_project(
    destination: str | Path,
    *,
    title: str,
    template: str = "piano-solo",
    measures: int = 16,
    beats: int = 4,
    beat_type: int = 4,
    bpm: float = 84.0,
    fifths: int = 0,
    mode: str = "major",
    duration_target: str | None = None,
) -> dict:
    root = Path(destination).resolve()
    if root.exists():
        raise ValueError(f"destination already exists: {root}")
    if template not in TEMPLATES:
        raise ValueError(f"unknown project template: {template!r}")
    if not title.strip():
        raise ValueError("title must be non-empty")
    if not 1 <= measures <= 100_000:
        raise ValueError("measures must be between 1 and 100000")
    if not 1 <= beats <= 32 or beat_type not in {1, 2, 4, 8, 16, 32}:
        raise ValueError("invalid initial meter")
    if not 1.0 <= bpm <= 999.0 or not -7 <= fifths <= 7:
        raise ValueError("invalid tempo or key fifths")
    if mode not in {"major", "minor"}:
        raise ValueError("mode must be major or minor")
    parts = TEMPLATES[template]
    root.mkdir(parents=True)
    (root / "parts").mkdir()
    piece = {
        "format": 1,
        "title": title.strip(),
        "measures": measures,
        "time": [{"measure": 1, "beats": beats, "beat_type": beat_type}],
        "tempo": [{"at": "1:1", "bpm": float(bpm)}],
        "key": [{"measure": 1, "fifths": fifths, "mode": mode}],
        "parts": [
            {
                "id": part.id,
                "name": part.name,
                "profile": part.profile,
                "file": f"parts/{part.id}.yaml",
            }
            for part in parts
        ],
    }
    _write_yaml(root / "piece.yaml", piece)
    for part in parts:
        profile = load_profile(root, part.profile)
        part_document: dict = {"format": 1, "part": part.id}
        if profile.family in {"keyboard", "keyboards"}:
            part_document["staves"] = [
                {"number": 1, "name": "right", "clef": {"sign": "G", "line": 2}},
                {"number": 2, "name": "left", "clef": {"sign": "F", "line": 4}},
            ]
        part_document["controls"] = []
        part_document["measures"] = {}
        _write_yaml(root / "parts" / f"{part.id}.yaml", part_document)
    mix = {
        "format": 2,
        "master": {
            "target_lufs": -16.0,
            "true_peak_ceiling_db": -1.0,
            "loudness_range_lu": 11.0,
            "loudness_tolerance_lu": 0.5,
        },
        "buses": {
            "room": {
                "output": "master",
                "gain_db": -8.0,
                "inserts": [{"type": "reverb", "delays_ms": "40|55", "decays": "0.30|0.20"}],
            }
        },
        "tracks": {
            part.id: {
                "gain_db": -6.0,
                "pan": part.pan,
                "output": "master",
                "sends": {"room": -12.0},
            }
            for part in parts
        },
    }
    _write_yaml(root / "mix.yaml", mix)
    notes = [
        f"# {title.strip()}",
        "",
        "## User direction",
        "",
        "- Purpose: unresolved",
        f"- Target duration: {duration_target or 'unresolved'}",
        "- Emotional trajectory: unresolved",
        f"- Initial ensemble template: {template}",
        "- Required/forbidden sounds: unresolved",
        "- Performance difficulty: unresolved",
        "- Delivery and listening checkpoints: unresolved",
        "",
        (
            "Do not author score events until the unresolved direction fields are "
            "answered or delegated."
        ),
        "",
    ]
    (root / "NOTES.md").write_text("\n".join(notes), encoding="utf-8")
    return {
        "schema_version": "1",
        "status": "ok",
        "project": str(root),
        "template": template,
        "title": title.strip(),
        "measures": measures,
        "parts": [part.id for part in parts],
        "direction_gate": "unresolved",
        "next": ["complete NOTES.md", "ledgerline validate", "author parts/*.yaml"],
    }


def _write_yaml(path: Path, data: dict) -> None:
    path.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
