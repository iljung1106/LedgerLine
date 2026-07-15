from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ledgerline import __version__
from ledgerline.automation import compile_automation, load_automation
from ledgerline.compiler_midi import compile_midi
from ledgerline.compiler_mpe import compile_mpe_part
from ledgerline.compiler_musicxml import compile_musicxml
from ledgerline.expression_plan import write_expression_plan
from ledgerline.project import load_piece


def compile_project(root: str | Path, output: str | Path | None = None) -> dict:
    from ledgerline.build_state import authored_revision, record_compile

    piece = load_piece(root)
    build = Path(output).resolve() if output else piece.root / "build"
    build.mkdir(parents=True, exist_ok=True)
    parts_dir = build / "parts"
    parts_dir.mkdir(exist_ok=True)

    musicxml_path = build / "score.musicxml"
    midi_path = build / "score.mid"
    compile_musicxml(piece, musicxml_path)
    compile_midi(piece, midi_path)
    expression_path = build / "expression-plan.json"
    expression_plan = write_expression_plan(piece, expression_path)
    part_paths: list[Path] = []
    for part in piece.parts:
        path = parts_dir / f"{part.id}.mid"
        part_plan = expression_plan["parts"][part.id]
        if part_plan["backend"] == "mpe":
            compile_mpe_part(piece, part, part_plan, path)
        else:
            compile_midi(piece, path, [part.id])
        part_paths.append(path)

    inputs = [piece.root / "piece.yaml", *(part.source_path for part in piece.parts)]
    mix_path = piece.root / "mix.yaml"
    if mix_path.is_file():
        inputs.append(mix_path)
    render_path = piece.root / "render.yaml"
    if render_path.is_file():
        inputs.append(render_path)
    performance_path = piece.root / "performance.yaml"
    if performance_path.is_file():
        inputs.append(performance_path)
    outputs = [musicxml_path, midi_path, expression_path, *part_paths]
    motifs_path = piece.root / "motifs.yaml"
    if motifs_path.is_file():
        inputs.append(motifs_path)
        expansion_path = build / "motif-expansion.json"
        expansion_path.write_text(
            json.dumps(
                {
                    "schema_version": "1",
                    "status": "ok",
                    "placements": piece.motif_expansions,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        outputs.append(expansion_path)
    automation_path = piece.root / "automation.yaml"
    if automation_path.is_file():
        inputs.append(automation_path)
        compiled_automation_path = build / "automation.json"
        compile_automation(piece, load_automation(piece.root, piece), compiled_automation_path)
        outputs.append(compiled_automation_path)
    manifest = {
        "schema_version": "1",
        "tool": {"name": "ledgerline", "version": __version__},
        "project": str(piece.root),
        "source_revision": authored_revision(piece.root),
        "title": piece.title,
        "parts": [{"id": part.id, "name": part.name} for part in piece.parts],
        "inputs": [_file_record(path, piece.root) for path in inputs],
        "profiles": [_profile_record(piece.profiles[part.profile_id]) for part in piece.parts],
        "notation_contract": _notation_contract(piece),
        "outputs": [_file_record(path, build) for path in outputs],
    }
    manifest_path = build / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    report = {
        "status": "ok",
        "project": str(piece.root),
        "source_revision": manifest["source_revision"],
        "build": str(build),
        "musicxml": str(musicxml_path),
        "midi": str(midi_path),
        "part_midis": [str(path) for path in part_paths],
        "expression_plan": str(expression_path),
        "manifest": str(manifest_path),
        "notation_contract": manifest["notation_contract"],
    }
    if output is None:
        record_compile(piece.root, report)
    return report


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
        "articulation_definitions": {
            name: {
                "musicxml": definition.musicxml,
                "label": definition.label,
                "gate": definition.gate,
                "velocity_delta": definition.velocity_delta,
            }
            for name, definition in sorted(profile.articulation_definitions.items())
        },
        "keyswitches": {name: str(pitch) for name, pitch in sorted(profile.keyswitches.items())},
        "performance": {
            name: {
                "type": binding.type,
                "controller": binding.controller,
                "parameter": binding.parameter,
                "min": binding.minimum,
                "max": binding.maximum,
                "default": binding.default,
            }
            for name, binding in sorted(profile.performance.items())
        },
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return {**payload, "sha256": hashlib.sha256(canonical).hexdigest()}


def _notation_contract(piece) -> dict:
    events = [
        event
        for part in piece.parts
        for measure in part.measures.values()
        for voice in measure.voices.values()
        for event in voice
    ]
    controls = [control for part in piece.parts for control in part.controls]
    counts = {
        "tuplet_events": sum(event.tuplet is not None for event in events),
        "grace_notes": sum(event.grace is not None for event in events),
        "slur_events": sum(event.slur is not None for event in events),
        "dynamic_ramps": sum(control.kind == "dynamic_ramp" for control in controls),
        "tempo_ramps": sum(change.ramp_bpm is not None for change in piece.tempo_changes),
    }
    return {
        "schema_version": "1",
        "features": counts,
        "representations": {
            "tuplets": {
                "musicxml": "exact time-modification and tuplet notation",
                "midi": "exact tick duration at 480 TPQ or compile failure",
            },
            "grace_notes": {
                "musicxml": "exact grace type and authored steal-time-following",
                "midi": "deterministic stolen-time scheduling within the following note slot",
            },
            "slurs": {
                "musicxml": "exact slur notation",
                "midi": "metadata marker only; no implicit controller approximation",
                "midi_metadata_only": True,
            },
            "dynamic_ramps": {
                "musicxml": "wedge plus explicit endpoint dynamics",
                "midi": "sampled CC ramp plus exact ledgerline metadata marker",
                "midi_sample_ticks": 30,
            },
            "tempo_ramps": {
                "musicxml": (
                    "visible tempo words/endpoints plus exact ledgerline:tempo-ramp metadata"
                ),
                "midi": "sampled tempo map plus exact ledgerline metadata marker",
                "midi_sample_ticks": 60,
            },
        },
    }
