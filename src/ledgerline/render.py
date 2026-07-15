from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path
from typing import Any

from ledgerline.audio import resolve_ffmpeg
from ledgerline.diagnostics import CapabilityError, Diagnostic
from ledgerline.environment import doctor
from ledgerline.external_process import run_external
from ledgerline.project import load_piece
from ledgerline.render_inputs import canonical_hash, part_performance_inputs
from ledgerline.soundfont import read_presets


def render_project(
    project: str | Path,
    *,
    fluidsynth: str | Path | None = None,
    soundfont: str | Path | None = None,
    sample_rate: int = 48000,
    ffmpeg: str | Path | None = None,
    timeout: int = 180,
    cancel_event: threading.Event | None = None,
) -> dict:
    from ledgerline.build_state import authored_revision, record_render

    root = Path(project).resolve()
    if (root / "render.yaml").is_file():
        from ledgerline.render_graph import render_graph_project

        return render_graph_project(
            root,
            ffmpeg=ffmpeg,
            timeout=timeout,
            cancel_event=cancel_event,
        )
    build = root / "build"
    parts_dir = build / "parts"
    if not (build / "score.mid").is_file() or not parts_dir.is_dir():
        raise CapabilityError(
            "compiled MIDI is missing",
            [
                Diagnostic(
                    "error",
                    "render.midi_missing",
                    str(parts_dir),
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

    ffmpeg_path = resolve_ffmpeg(ffmpeg)
    renderer_identity = _file_identity(renderer_path)
    soundfont_identity = _file_identity(soundfont_path)
    stems_dir = build / "stems"
    cache_dir = build / "render-cache"
    stems_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    rendered: list[dict[str, Any]] = []
    for part in piece.parts:
        midi_path = parts_dir / f"{part.id}.mid"
        if not midi_path.is_file():
            raise CapabilityError(
                "compiled part MIDI is missing",
                [Diagnostic("error", "render.midi_missing", str(midi_path), "Run compile.")],
            )
        profile = piece.profiles[part.profile_id]
        wav_path = stems_dir / f"{part.id}.wav"
        receipt_path = cache_dir / f"legacy-{part.id}.json"
        cache_key = _legacy_cache_key(
            root,
            part.id,
            profile,
            renderer_identity,
            soundfont_identity,
            sample_rate,
        )
        cached = _valid_legacy_cache(receipt_path, wav_path, cache_key)
        if not cached:
            temporary = stems_dir / f".{part.id}.rendering.wav"
            temporary.unlink(missing_ok=True)
            command = [
                str(renderer_path),
                "-ni",
                "-q",
                "-r",
                str(sample_rate),
                "-T",
                "wav",
                "-F",
                str(temporary),
                str(soundfont_path),
                str(midi_path),
            ]
            try:
                completed = run_external(
                    command,
                    timeout=timeout,
                    cancel_event=cancel_event,
                    cwd=root,
                )
                if (
                    completed.returncode != 0
                    or not temporary.is_file()
                    or temporary.stat().st_size <= 44
                ):
                    raise _render_failure(midi_path, completed)
                os.replace(temporary, wav_path)
                _write_legacy_receipt(receipt_path, part.id, wav_path, cache_key)
            except BaseException:
                temporary.unlink(missing_ok=True)
                raise
        rendered.append(
            {
                "midi": str(midi_path),
                "wav": str(wav_path),
                "bytes": wav_path.stat().st_size,
                "sha256": _hash_file(wav_path),
                "cache": "hit" if cached else "miss",
                "cache_key": cache_key,
            }
        )

    preview = build / "preview.wav"
    _mix_preview(
        [Path(item["wav"]) for item in rendered],
        preview,
        ffmpeg_path,
        sample_rate=sample_rate,
        timeout=timeout,
        cancel_event=cancel_event,
    )
    rendered.append(
        {
            "wav": str(preview),
            "bytes": preview.stat().st_size,
            "sha256": _hash_file(preview),
            "cache": "miss",
        }
    )
    report = {
        "schema_version": "2",
        "status": "ok",
        "source_revision": authored_revision(root),
        "renderer": str(renderer_path),
        "soundfont": soundfont_identity,
        "ffmpeg": str(ffmpeg_path),
        "sample_rate": sample_rate,
        "artifacts": rendered,
    }
    (build / "render-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    record_render(root, report)
    return report


def _legacy_cache_key(
    root: Path,
    part_id: str,
    profile: Any,
    renderer: dict[str, Any],
    soundfont: dict[str, Any],
    sample_rate: int,
) -> str:
    payload = {
        "schema_version": "2",
        "engine": "fluidsynth",
        "part": part_id,
        "performance": part_performance_inputs(root, part_id),
        "profile": {
            "id": profile.id,
            "bank_msb": profile.bank_msb,
            "bank_lsb": profile.bank_lsb,
            "program": profile.program,
        },
        "renderer": renderer,
        "soundfont": soundfont,
        "sample_rate": sample_rate,
    }
    return canonical_hash(payload)


def _valid_legacy_cache(receipt: Path, output: Path, cache_key: str) -> bool:
    if not receipt.is_file() or not output.is_file():
        return False
    try:
        raw = json.loads(receipt.read_text(encoding="utf-8"))
        return (
            isinstance(raw, dict)
            and raw.get("cache_key") == cache_key
            and raw.get("output_sha256") == _hash_file(output)
        )
    except (OSError, ValueError, json.JSONDecodeError):
        return False


def _write_legacy_receipt(path: Path, part_id: str, output: Path, cache_key: str) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(
            {
                "schema_version": "2",
                "part": part_id,
                "cache_key": cache_key,
                "output_sha256": _hash_file(output),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _mix_preview(
    inputs: list[Path],
    output: Path,
    ffmpeg: Path,
    *,
    sample_rate: int,
    timeout: int,
    cancel_event: threading.Event | None,
) -> None:
    temporary = output.with_name(f".{output.stem}.rendering{output.suffix}")
    temporary.unlink(missing_ok=True)
    command = [str(ffmpeg), "-hide_banner", "-y"]
    for path in inputs:
        command.extend(["-i", str(path)])
    command.extend(
        [
            "-filter_complex",
            f"amix=inputs={len(inputs)}:normalize=0",
            "-c:a",
            "pcm_s24le",
            "-ar",
            str(sample_rate),
            str(temporary),
        ]
    )
    try:
        completed = run_external(
            command,
            timeout=timeout,
            cancel_event=cancel_event,
            cwd=output.parent.parent,
        )
        if (
            completed.returncode != 0
            or not temporary.is_file()
            or temporary.stat().st_size <= 44
        ):
            raise CapabilityError(
                "preview mix failed",
                [
                    Diagnostic(
                        "error",
                        "render.preview_failed",
                        str(output),
                        completed.stderr[-2000:],
                    )
                ],
            )
        os.replace(temporary, output)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _render_failure(midi_path: Path, completed: Any) -> CapabilityError:
    return CapabilityError(
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


def _file_identity(path: Path) -> dict[str, Any]:
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": _hash_file(path)}


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
