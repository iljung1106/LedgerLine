from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from ledgerline.diagnostics import CapabilityError, Diagnostic
from ledgerline.environment import doctor
from ledgerline.project import load_piece
from ledgerline.soundfont import read_presets


def render_project(
    project: str | Path,
    *,
    fluidsynth: str | Path | None = None,
    soundfont: str | Path | None = None,
    sample_rate: int = 48000,
    timeout: int = 180,
) -> dict:
    root = Path(project).resolve()
    build = root / "build"
    score_midi = build / "score.mid"
    if not score_midi.is_file():
        raise CapabilityError(
            "compiled MIDI is missing",
            [
                Diagnostic(
                    "error",
                    "render.midi_missing",
                    str(score_midi),
                    "Run ledgerline compile first.",
                )
            ],
        )
    environment = doctor()
    renderer_path = Path(fluidsynth).resolve() if fluidsynth else _renderer_from(environment)
    soundfont_path = Path(soundfont).resolve() if soundfont else _soundfont_from(environment)
    if renderer_path is None or not renderer_path.is_file():
        raise CapabilityError(
            "FluidSynth is unavailable",
            [
                Diagnostic(
                    "error",
                    "render.fluidsynth_missing",
                    "environment",
                    "Pass --fluidsynth or run setup after user consent.",
                )
            ],
        )
    if soundfont_path is None or not soundfont_path.is_file():
        raise CapabilityError(
            "SoundFont is unavailable",
            [
                Diagnostic(
                    "error",
                    "render.soundfont_missing",
                    "environment",
                    "Pass --soundfont or install an audited pack.",
                )
            ],
        )

    piece = load_piece(root)
    presets = {
        (preset.bank, preset.program): preset.name for preset in read_presets(soundfont_path)
    }
    missing_presets: list[dict] = []
    for part in piece.parts:
        profile = piece.profiles[part.profile_id]
        bank = profile.bank_msb * 128 + profile.bank_lsb
        if (bank, profile.program) not in presets:
            missing_presets.append(
                {
                    "part": part.id,
                    "profile": profile.id,
                    "bank": bank,
                    "program": profile.program,
                }
            )
    if missing_presets:
        raise CapabilityError(
            "SoundFont does not cover every requested instrument",
            [
                Diagnostic(
                    "error",
                    "render.preset_missing",
                    str(soundfont_path),
                    json.dumps(missing_presets, ensure_ascii=False),
                )
            ],
        )

    stems_dir = build / "stems"
    stems_dir.mkdir(parents=True, exist_ok=True)
    rendered: list[dict] = []
    for midi_path in [score_midi, *sorted((build / "parts").glob("*.mid"))]:
        name = "preview" if midi_path == score_midi else midi_path.stem
        wav_path = build / "preview.wav" if name == "preview" else stems_dir / f"{name}.wav"
        command = [
            str(renderer_path),
            "-ni",
            "-q",
            "-r",
            str(sample_rate),
            "-T",
            "wav",
            "-F",
            str(wav_path),
            str(soundfont_path),
            str(midi_path),
        ]
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
            cwd=str(root),
        )
        if completed.returncode != 0 or not wav_path.is_file() or wav_path.stat().st_size <= 44:
            raise CapabilityError(
                f"FluidSynth failed while rendering {midi_path.name}",
                [
                    Diagnostic(
                        "error",
                        "render.external_failed",
                        str(midi_path),
                        json.dumps(
                            {
                                "returncode": completed.returncode,
                                "stdout": completed.stdout[-2000:],
                                "stderr": completed.stderr[-2000:],
                            },
                            ensure_ascii=False,
                        ),
                    )
                ],
            )
        rendered.append(
            {
                "midi": str(midi_path),
                "wav": str(wav_path),
                "bytes": wav_path.stat().st_size,
                "sha256": hashlib.sha256(wav_path.read_bytes()).hexdigest(),
            }
        )
    report = {
        "schema_version": "1",
        "status": "ok",
        "renderer": str(renderer_path),
        "soundfont": {
            "path": str(soundfont_path),
            "bytes": soundfont_path.stat().st_size,
            "sha256": hashlib.sha256(soundfont_path.read_bytes()).hexdigest(),
        },
        "sample_rate": sample_rate,
        "artifacts": rendered,
    }
    (build / "render-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def _renderer_from(environment: dict) -> Path | None:
    for renderer in environment["renderers"]:
        if renderer["id"] == "fluidsynth" and renderer["origin"] == "ledgerline-managed":
            return Path(renderer["path"])
    return None


def _soundfont_from(environment: dict) -> Path | None:
    preferred = [item for item in environment["soundfonts"] if item["origin"] == "ledgerline-pack"]
    if len(preferred) == 1:
        return Path(preferred[0]["path"])
    return None
