from __future__ import annotations

import hashlib
import math
import wave
from pathlib import Path
from typing import Any

from ledgerline.compiler import compile_project
from ledgerline.mix_config import MixConfig, MixNode, Processor, load_mix_config
from ledgerline.model import DYNAMIC_VELOCITY, Event, Piece
from ledgerline.project import load_piece
from ledgerline.timeline import Timeline


def build_studio_model(project: str | Path, *, peak_bins: int = 720) -> dict[str, Any]:
    root = Path(project).resolve()
    piece = load_piece(root)
    build = root / "build"
    if not (build / "score.musicxml").is_file():
        compile_project(root)
    timeline = Timeline(piece)
    duration = timeline.total_seconds()
    notes = _notes(piece, timeline)
    mix = _mix_model(root, piece)
    media = _media_model(root, piece, duration, peak_bins)
    measures = []
    for number in range(1, piece.measures + 1):
        start = timeline.measure_starts[number]
        end = timeline.measure_starts[number + 1]
        time = piece.time_at(number)
        measures.append(
            {
                "number": number,
                "start_tick": round(start * 1920),
                "end_tick": round(end * 1920),
                "start_seconds": timeline.seconds_at_whole(start),
                "end_seconds": timeline.seconds_at_whole(end),
                "beats": time.beats,
                "beat_type": time.beat_type,
            }
        )
    return {
        "schema_version": "1",
        "status": "ok",
        "project": {
            "root": str(root),
            "title": piece.title,
            "revision": project_revision(root),
            "measures": piece.measures,
            "duration_seconds": duration,
            "sample_rate": 48_000,
        },
        "transport": {
            "duration_seconds": duration,
            "tempo_segments": Timeline(piece).report()["tempo_segments"],
            "measures": measures,
        },
        "parts": [
            {
                "id": part.id,
                "name": part.name,
                "profile": part.profile_id,
                "family": piece.profiles[part.profile_id].family,
                "staff_count": len(part.staves),
                "note_count": sum(1 for note in notes if note["part"] == part.id),
                "color": _part_color(index),
            }
            for index, part in enumerate(piece.parts)
        ],
        "notes": notes,
        "mix": mix,
        "media": media,
        "score": {"url": "/api/score", "format": "musicxml"},
        "capabilities": {
            "edit_pitch": True,
            "edit_velocity": True,
            "move_within_measure": True,
            "resize_with_validation": True,
            "undo_redo": True,
            "delegation": True,
            "realtime_stem_mix": bool(media["stems"]),
        },
    }


def project_revision(root: str | Path) -> str:
    project = Path(root).resolve()
    digest = hashlib.sha256()
    authored = [project / "piece.yaml", *sorted((project / "parts").glob("*.yaml"))]
    authored.extend(
        path
        for name in ("mix.yaml", "automation.yaml", "performance.yaml", "render.yaml")
        if (path := project / name).is_file()
    )
    for path in authored:
        digest.update(path.relative_to(project).as_posix().encode())
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def _notes(piece: Piece, timeline: Timeline) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for part in piece.parts:
        profile = piece.profiles[part.profile_id]
        dynamics: dict[str, int] = {}
        for measure_number in range(1, piece.measures + 1):
            measure = part.measures.get(measure_number)
            if measure is None:
                continue
            for voice, events in sorted(measure.voices.items(), key=lambda item: int(item[0][1:])):
                cursor = timeline.measure_starts[measure_number]
                velocity = dynamics.get(voice, DYNAMIC_VELOCITY["mf"])
                for event_index, event in enumerate(events):
                    if event.dynamic:
                        velocity = DYNAMIC_VELOCITY[event.dynamic]
                        dynamics[voice] = velocity
                    if not event.is_rest:
                        start_seconds = timeline.seconds_at_whole(cursor)
                        end_seconds = timeline.seconds_at_whole(cursor + event.duration)
                        for pitch_index, pitch in enumerate(event.pitches):
                            result.append(
                                {
                                    "id": (
                                        f"{part.id}:{measure_number}:{voice}:"
                                        f"{event_index}:{pitch_index}"
                                    ),
                                    "part": part.id,
                                    "measure": measure_number,
                                    "voice": voice,
                                    "event_index": event_index,
                                    "pitch_index": pitch_index,
                                    "pitch": pitch.midi + profile.transposition,
                                    "written_pitch": str(pitch),
                                    "start_tick": round(cursor * 1920),
                                    "end_tick": round((cursor + event.duration) * 1920),
                                    "start_seconds": start_seconds,
                                    "end_seconds": end_seconds,
                                    "duration": _duration_text(event),
                                    "velocity": event.velocity or velocity,
                                    "dynamic": event.dynamic,
                                    "articulation": event.articulation,
                                    "staff": event.staff or 1,
                                    "expression": bool(
                                        event.pitch_cents or event.expression or event.gestures
                                    ),
                                }
                            )
                    cursor += event.duration
    return sorted(result, key=lambda item: (item["start_tick"], item["pitch"], item["id"]))


def _duration_text(event: Event) -> str:
    from ledgerline.model import duration_token

    kind, dots = duration_token(event.duration)
    denominator = {
        "whole": 1,
        "half": 2,
        "quarter": 4,
        "eighth": 8,
        "16th": 16,
        "32nd": 32,
    }[kind]
    return f"1/{denominator}{'.' * dots}"


def _mix_model(root: Path, piece: Piece) -> dict[str, Any]:
    try:
        config = load_mix_config(root)
    except Exception:
        config = MixConfig(2, {}, {}, {}, {})
    tracks = {}
    for part in piece.parts:
        node = config.tracks.get(part.id)
        tracks[part.id] = {
            "gain_db": node.gain_db if node else 0.0,
            "pan": node.pan if node else 0.0,
            "output": node.output if node else "master",
            "sends": node.sends if node else {},
            "inserts": [_processor(item) for item in (node.inserts if node else ())],
        }
    return {
        "format": config.format,
        "tracks": tracks,
        "buses": {key: _mix_node(node) for key, node in config.buses.items()},
        "master": {
            **{key: value for key, value in config.master.items() if key != "inserts"},
            "inserts": [_processor(item) for item in config.master.get("inserts", ())],
        },
    }


def _mix_node(node: MixNode) -> dict[str, Any]:
    return {
        "gain_db": node.gain_db,
        "pan": node.pan,
        "output": node.output,
        "sends": node.sends,
        "inserts": [_processor(item) for item in node.inserts],
    }


def _processor(item: Processor) -> dict[str, Any]:
    return {"type": item.kind, **_jsonable(item.settings)}


def _jsonable(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value


def _media_model(root: Path, piece: Piece, musical_duration: float, bins: int) -> dict[str, Any]:
    build = root / "build"
    stems = []
    for part in piece.parts:
        path = build / "stems" / f"{part.id}.wav"
        if path.is_file():
            identity = _safe_wav_identity(path, musical_duration, bins)
            if identity:
                stems.append({"part": part.id, "url": f"/media/stems/{part.id}.wav", **identity})
    mix_path = build / "mix.wav"
    preview_path = build / "preview.wav"
    master_path = (
        mix_path
        if mix_path.is_file()
        else preview_path
        if preview_path.is_file()
        else None
    )
    master = None
    if master_path:
        identity = _safe_wav_identity(master_path, musical_duration, bins)
        if identity:
            master = {
                "url": f"/media/{master_path.name}",
                **identity,
            }
    spectrogram = build / "studio" / "spectrogram.png"
    return {
        "master": master,
        "stems": stems,
        "spectrogram_url": "/media/studio/spectrogram.png" if spectrogram.is_file() else None,
        "binding": "aligned"
        if master and master["duration_seconds"] + 0.05 >= musical_duration
        else "midi-only"
        if master is None
        else "stale",
    }


def _safe_wav_identity(path: Path, musical_duration: float, bins: int) -> dict[str, Any] | None:
    try:
        return _wav_identity(path, musical_duration, bins)
    except Exception:
        return None


def _wav_identity(path: Path, musical_duration: float, bins: int) -> dict[str, Any]:
    with wave.open(str(path), "rb") as stream:
        frames = stream.getnframes()
        rate = stream.getframerate()
        channels = stream.getnchannels()
        width = stream.getsampwidth()
    duration = frames / rate
    return {
        "path": str(path),
        "duration_seconds": duration,
        "sample_rate": rate,
        "channels": channels,
        "sample_width": width,
        "musical_duration_seconds": musical_duration,
        "peaks": _peaks(path, bins),
    }


def _peaks(path: Path, bins: int) -> list[list[float]]:
    from ledgerline.pcm import read_pcm_wav

    frames, _, _ = read_pcm_wav(path)
    mono = frames.mean(axis=1)
    size = max(1, math.ceil(len(mono) / bins))
    result = []
    for index in range(0, len(mono), size):
        chunk = mono[index : index + size]
        result.append([round(float(chunk.min()), 5), round(float(chunk.max()), 5)])
    return result


def _part_color(index: int) -> str:
    colors = ("#4fc4b2", "#e7a95a", "#7ea2d8", "#cf7f8f", "#9caf74", "#b38bd4")
    return colors[index % len(colors)]
