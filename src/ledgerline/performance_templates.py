from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

TEMPLATES: dict[str, dict[str, Any]] = {
    "mpe-expressive-string": {
        "backend": "mpe",
        "overlap": "allow",
        "pitch_bend_range": 2,
        "required_capabilities": ["mpe", "channel-pressure", "cc74"],
        "technique": {
            "legato": "Overlap connected notes by 20–60 ms only when the instrument supports it.",
            "vibrato": "Delay vibrato after the attack; scale depth with phrase intensity.",
            "bowing": "Shape pressure independently from loudness and reset at phrase boundaries.",
        },
    },
    "clap-note-expression": {
        "backend": "clap-note-expression",
        "overlap": "allow",
        "pitch_bend_range": 48,
        "required_capabilities": ["clap-note-expression", "stable-note-ids"],
        "technique": {
            "polyphony": "Use note IDs; never collapse pressure, timbre, or tuning to a channel.",
            "automation": "Prefer plugin parameter automation for room and microphone controls.",
        },
    },
    "vst3-sampled-legato": {
        "backend": "legacy",
        "overlap": "error",
        "pitch_bend_range": 2,
        "required_capabilities": ["vst3", "keyswitches", "parameter-automation"],
        "technique": {
            "articulation": "Resolve every semantic articulation to an approved keyswitch map.",
            "legato": "Use monophonic connected lines; overlapping per-note bends are forbidden.",
        },
    },
    "soundfont-keyboard": {
        "backend": "legacy",
        "overlap": "error",
        "pitch_bend_range": 2,
        "required_capabilities": ["midi1", "cc64", "velocity"],
        "technique": {
            "pedal": "Change pedal after harmony changes; avoid permanently sustained CC64.",
            "voicing": "Use register, velocity, and timing rather than per-note timbre controls.",
        },
    },
    "korean-bowed-string": {
        "backend": "mpe",
        "overlap": "allow",
        "pitch_bend_range": 2,
        "required_capabilities": ["mpe", "pitch", "pressure"],
        "technique": {
            "nonghyeon": (
                "Start after pitch establishment; do not quantize the oscillation to tempo."
            ),
            "chuseong": "Place an upward terminal inflection at a structurally meaningful arrival.",
            "toeseong": "Reserve downward release inflection for cadence or rhetorical withdrawal.",
        },
    },
}


def list_performance_templates() -> dict:
    return {
        "schema_version": "1",
        "status": "ok",
        "templates": [
            {
                "id": template_id,
                "backend": value["backend"],
                "required_capabilities": value["required_capabilities"],
            }
            for template_id, value in TEMPLATES.items()
        ],
    }


def show_performance_template(template_id: str) -> dict:
    if template_id not in TEMPLATES:
        raise ValueError(f"unknown performance template: {template_id}")
    return {"schema_version": "1", "status": "ok", "id": template_id, **TEMPLATES[template_id]}


def apply_performance_template(project: str | Path, part_id: str, template_id: str) -> dict:
    root = Path(project).resolve()
    template = show_performance_template(template_id)
    from ledgerline.project import load_piece

    piece = load_piece(root)
    if part_id not in {part.id for part in piece.parts}:
        raise ValueError(f"unknown project part: {part_id}")
    path = root / "performance.yaml"
    if path.is_file():
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("format") != 1:
            raise ValueError("existing performance.yaml is invalid")
    else:
        raw = {"format": 1, "parts": {}}
    raw.setdefault("parts", {})[part_id] = {
        "backend": template["backend"],
        "overlap": template["overlap"],
        "pitch_bend_range": template["pitch_bend_range"],
    }
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    guide = root / "performance-guides"
    guide.mkdir(exist_ok=True)
    guide_path = guide / f"{part_id}.json"
    guide_path.write_text(
        json.dumps(template, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {
        "schema_version": "1",
        "status": "ok",
        "part": part_id,
        "template": template_id,
        "performance": str(path),
        "guide": str(guide_path),
    }
