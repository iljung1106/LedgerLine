from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import yaml

from ledgerline.automation import load_automation
from ledgerline.build_state import (
    authored_revision,
    build_state,
    ensure_media_sidecar,
    file_sha256,
)
from ledgerline.compiler import compile_project
from ledgerline.diagnostics import ValidationError
from ledgerline.mix_config import load_mix_config, load_mix_document, mix_config_to_dict
from ledgerline.model import DYNAMIC_VELOCITY, Event, Piece, parse_anchor
from ledgerline.project import load_piece, load_profile
from ledgerline.timeline import Timeline


def build_studio_model(project: str | Path, *, peak_bins: int = 720) -> dict[str, Any]:
    root = Path(project).resolve()
    piece = load_piece(root)
    build = root / "build"
    if not (build / "score.musicxml").is_file():
        compile_project(root)
    engine_state = build_state(root)
    timeline = Timeline(piece)
    duration = timeline.total_seconds()
    notes = _notes(piece, timeline)
    controls = _controls(piece, timeline)
    automation = _automation(root, piece, timeline)
    mix = _mix_model(root, engine_state)
    media = _media_model(root, piece, duration, peak_bins, engine_state)
    refinement = _refinement_model(root, engine_state)
    review = build_review_impact(root)
    profile_catalog = _profile_catalog(root)
    # Keep the standalone `studio-model` CLI and the HTTP model on one contract.
    # The local import avoids a module cycle because delegation edits use project_revision.
    from ledgerline.delegation import list_delegations

    delegations = list_delegations(root)["tasks"]
    prepared_ids = all(
        event.id
        for part in piece.parts
        for measure in part.measures.values()
        for events in measure.voices.values()
        for event in events
        if not event.is_rest
    )
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
        "schema_version": "2",
        "status": "ok",
        "project": {
            "root": str(root),
            "title": piece.title,
            "revision": project_revision(root),
            "compiled_revision": engine_state["compiled_revision"],
            "rendered_revision": engine_state["rendered_revision"],
            "mix_revision": engine_state["mix_revision"],
            "refinement_revision": engine_state["refinement_revision"],
            "measures": piece.measures,
            "duration_seconds": duration,
            "sample_rate": engine_state["stages"]["render"].get("sample_rate") or 48_000,
            "authored_revision": engine_state["authored_revision"],
            "prepared_ids": prepared_ids,
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
                "articulations": sorted(piece.profiles[part.profile_id].articulations),
                "supported_controls": sorted(piece.profiles[part.profile_id].performance),
                "profile_capabilities": {
                    **_profile_capabilities(piece.profiles[part.profile_id]),
                },
                "staff_count": len(part.staves),
                "note_count": sum(1 for note in notes if note["part"] == part.id),
                "color": _part_color(index),
                "engine": engine_state["engines"].get(part.id),
                "render_status": engine_state["stages"]["render"]["parts"].get(
                    part.id, {"status": "missing"}
                )["status"],
            }
            for index, part in enumerate(piece.parts)
        ],
        "profile_catalog": profile_catalog,
        "notes": notes,
        "controls": controls,
        "tempo": _authored_tempo_points(root, timeline),
        "automation": automation,
        "mix": mix,
        "media": media,
        "review": {**review, "ab": media["ab"]},
        "refinement": refinement,
        "delegations": delegations,
        "score": {
            "url": f"/api/score?v={engine_state['compiled_revision'] or 'stale'}",
            "format": "musicxml",
            "status": engine_state["stages"]["compile"]["status"],
        },
        "build": engine_state,
        "capabilities": {
            "edit_pitch": True,
            "edit_velocity": True,
            "edit_instrument": True,
            "structural_editing": prepared_ids,
            "move_within_measure": True,
            "move_across_measures": True,
            "resize_with_validation": True,
            "edit_controls": True,
            "edit_tempo": True,
            "edit_automation": True,
            "edit_mix_graph": True,
            "edit_mix_inserts": True,
            "source_impact": True,
            "ab_master_review": media["ab"]["available"],
            "undo_redo": True,
            "delegation": True,
            "refinement_report": True,
            "realtime_stem_mix": bool(media["stems"]),
        },
    }


def _refinement_model(root: Path, state: dict[str, Any]) -> dict[str, Any]:
    stage = state["stages"]["refinement"]
    output = stage.get("output") if isinstance(stage.get("output"), dict) else {}
    url = None
    if isinstance(output.get("path"), str):
        try:
            relative = Path(output["path"]).resolve().relative_to((root / "build").resolve())
            url = f"/media/{relative.as_posix()}?v={output.get('sha256', 'stale')}"
        except ValueError:
            pass
    return {
        "status": stage.get("status", "missing"),
        "reason": stage.get("reason"),
        "authored_revision": stage.get("authored_revision"),
        "report_status": stage.get("report_status"),
        "gates": stage.get("gates"),
        "output_sha256": output.get("sha256"),
        "url": url,
    }


def _authored_tempo_points(root: Path, timeline: Timeline) -> list[dict[str, Any]]:
    """Project authored tempo entries without expanding ramps into render segments."""

    document = yaml.safe_load((root / "piece.yaml").read_text(encoding="utf-8"))
    raw_tempo = document.get("tempo", []) if isinstance(document, dict) else []
    result: list[dict[str, Any]] = []
    for source_index, raw in enumerate(raw_tempo):
        if not isinstance(raw, dict):  # load_piece already reports malformed source
            continue
        at = str(raw["at"])
        measure, beat = parse_anchor(at)
        point = {
            "source_index": source_index,
            "at": at,
            "seconds": timeline.anchor(measure, beat).seconds,
            "bpm": float(raw["bpm"]),
        }
        if isinstance(raw.get("ramp"), dict):
            point["ramp"] = {
                "to": str(raw["ramp"]["to"]),
                "bpm": float(raw["ramp"]["bpm"]),
                "curve": str(raw["ramp"].get("curve", "linear")),
            }
        result.append(point)
    return result


def project_revision(root: str | Path) -> str:
    return authored_revision(root)


def build_review_impact(project: str | Path) -> dict[str, Any]:
    """Describe the latest Studio source transaction without judging musical quality."""

    root = Path(project).resolve()
    current_revision = authored_revision(root)
    path = root / ".ledgerline" / "history" / "studio-last-transaction.json"
    try:
        transaction = json.loads(path.read_text(encoding="utf-8"))
        required = {"id", "operation", "from_revision", "to_revision", "impact"}
        if not isinstance(transaction, dict) or not required <= set(transaction):
            raise ValueError("transaction record is incomplete")
        impact = transaction["impact"]
        if not isinstance(impact, dict):
            raise ValueError("transaction impact is invalid")
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {
            "schema_version": "1",
            "status": "none",
            "current_revision": current_revision,
            "latest_transaction": None,
            "impact": {
                "changed": False,
                "files": [],
                "parts": [],
                "measures": [],
                "aspects": [],
                "targets": [],
                "fields": [],
            },
        }
    matches = transaction["to_revision"] == current_revision
    return {
        "schema_version": "1",
        "status": "current" if matches else "superseded",
        "current_revision": current_revision,
        "transaction_matches_current_revision": matches,
        "latest_transaction": {
            key: transaction.get(key)
            for key in (
                "id",
                "operation",
                "created_at",
                "from_revision",
                "to_revision",
                "command_count",
                "command_types",
            )
        },
        "impact": impact,
    }


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
                velocity = dynamics.get(voice, DYNAMIC_VELOCITY["mf"])
                event_indices = {id(event): index for index, event in enumerate(events)}
                for scheduled in timeline.schedule_voice(measure_number, events):
                    event = scheduled.event
                    event_index = event_indices[id(event)]
                    if event.dynamic:
                        velocity = DYNAMIC_VELOCITY[event.dynamic]
                        dynamics[voice] = velocity
                    if not event.is_rest:
                        start_seconds = timeline.seconds_at_whole(scheduled.start_whole)
                        end_whole = scheduled.start_whole + scheduled.duration
                        end_seconds = timeline.seconds_at_whole(end_whole)
                        display_event_id = event.id or (
                            f"{part.id}:{measure_number}:{voice}:{event_index}"
                        )
                        for pitch_index, pitch in enumerate(event.pitches):
                            result.append(
                                {
                                    "id": f"{display_event_id}:{pitch_index}",
                                    **({"event_id": event.id} if event.id else {}),
                                    "part": part.id,
                                    "measure": measure_number,
                                    "voice": voice,
                                    "event_index": event_index,
                                    "pitch_index": pitch_index,
                                    "pitch": pitch.midi + profile.transposition,
                                    "written_pitch": str(pitch),
                                    "start_tick": round(scheduled.start_whole * 1920),
                                    "end_tick": round(end_whole * 1920),
                                    "start_seconds": start_seconds,
                                    "end_seconds": end_seconds,
                                    "duration": _duration_text(event),
                                    "velocity": event.velocity or velocity,
                                    "dynamic": event.dynamic,
                                    "articulation": event.articulation,
                                    "slur": event.slur,
                                    "tuplet": (
                                        {
                                            "actual": event.tuplet.actual,
                                            "normal": event.tuplet.normal,
                                            "type": event.tuplet.type,
                                        }
                                        if event.tuplet
                                        else None
                                    ),
                                    "grace": (
                                        {"kind": event.grace.kind, "steal": event.grace.steal}
                                        if event.grace
                                        else None
                                    ),
                                    "staff": event.staff or 1,
                                    "pitch_cents": event.pitch_cents,
                                    "expression": _expression_payload(event),
                                }
                            )
    return sorted(result, key=lambda item: (item["start_tick"], item["pitch"], item["id"]))


def _controls(piece: Piece, timeline: Timeline) -> list[dict[str, Any]]:
    result = []
    for part in piece.parts:
        for index, control in enumerate(part.controls):
            position = timeline.anchor(control.measure, control.beat)
            value: int | float | str
            semantic = None
            if control.kind == "cc":
                value = int(control.value or 0)
            elif control.kind == "pedal":
                value = str(control.pedal_action)
                semantic = control.pedal_action
            elif control.kind == "keyswitch":
                value = str(control.keyswitch)
                semantic = control.keyswitch
            elif control.kind == "dynamic_ramp":
                value = str(control.end_dynamic)
                semantic = f"{control.start_dynamic}->{control.end_dynamic}"
            else:
                value = float(control.performance_value or 0.0)
                semantic = control.performance_parameter
            display_id = control.id or f"{part.id}:control:{index}"
            result.append(
                {
                    "id": display_id,
                    **({"control_id": control.id} if control.id else {}),
                    "control_index": index,
                    "part": part.id,
                    "measure": control.measure,
                    "kind": control.kind,
                    "controller": control.controller,
                    "semantic": semantic,
                    "value": value,
                    "start_tick": position.tick,
                    "start_seconds": position.seconds,
                    "curve": "step",
                    **(
                        {
                            "start_dynamic": control.start_dynamic,
                            "end_dynamic": control.end_dynamic,
                            "end_measure": control.end_measure,
                            "end_tick": timeline.anchor(
                                int(control.end_measure), control.end_beat
                            ).tick,
                            "end_seconds": timeline.anchor(
                                int(control.end_measure), control.end_beat
                            ).seconds,
                            "curve": "linear",
                        }
                        if control.kind == "dynamic_ramp"
                        else {}
                    ),
                    "editable": True,
                }
            )
    return sorted(result, key=lambda item: (item["start_tick"], item["part"], item["id"]))


def _automation(root: Path, piece: Piece, timeline: Timeline) -> list[dict[str, Any]]:
    result = []
    for lane in load_automation(root, piece):
        part = lane.target.split(".", 2)[1] if lane.target.startswith("parts.") else None
        for index, point in enumerate(lane.points):
            position = timeline.anchor(point.measure, point.beat)
            display_id = point.id or f"{lane.id}:point:{index}"
            result.append(
                {
                    "id": display_id,
                    **({"point_id": point.id} if point.id else {}),
                    "point_index": index,
                    "lane": lane.id,
                    "part": part,
                    "measure": point.measure,
                    "kind": "automation",
                    "semantic": lane.target,
                    "lane_target": lane.target,
                    "unit": lane.unit,
                    "lane_interpolation": lane.interpolation,
                    "value": point.value,
                    "start_tick": position.tick,
                    "start_seconds": position.seconds,
                    "curve": point.curve or lane.interpolation,
                    "editable": True,
                }
            )
    return sorted(result, key=lambda item: (item["start_tick"], item["lane"], item["id"]))


def _profile_capabilities(profile: Any) -> dict[str, Any]:
    return {
        "range": {
            "absolute_low": str(profile.absolute_low),
            "absolute_high": str(profile.absolute_high),
            "comfortable_low": str(profile.comfortable_low),
            "comfortable_high": str(profile.comfortable_high),
            "transposition": profile.transposition,
        },
        "midi": {
            "bank_msb": profile.bank_msb,
            "bank_lsb": profile.bank_lsb,
            "program": profile.program,
        },
        "articulations": sorted(profile.articulations),
        "keyswitches": sorted(profile.keyswitches),
        "keyswitch_map": {
            name: str(pitch) for name, pitch in sorted(profile.keyswitches.items())
        },
        "performance_parameters": sorted(profile.performance),
        "performance": {
            name: {
                "type": binding.type,
                "controller": binding.controller,
                "parameter": binding.parameter,
                "minimum": binding.minimum,
                "maximum": binding.maximum,
                "default": binding.default,
            }
            for name, binding in sorted(profile.performance.items())
        },
    }


def _profile_catalog(root: Path) -> list[dict[str, Any]]:
    built_in_root = Path(__file__).parent / "data" / "profiles"
    built_in = {path.stem for path in built_in_root.glob("*.yaml")}
    project = {path.stem for path in (root / "profiles").glob("*.yaml")}
    result = []
    for profile_id in sorted(built_in | project):
        source = "project" if profile_id in project else "built-in"
        try:
            profile = load_profile(root, profile_id)
        except ValidationError as exc:
            result.append(
                {
                    "id": profile_id,
                    "source": source,
                    "status": "error",
                    "reason": str(exc),
                    "diagnostics": [item.to_dict() for item in exc.diagnostics],
                }
            )
            continue
        capabilities = _profile_capabilities(profile)
        result.append(
            {
                "id": profile_id,
                "name": profile.name,
                "family": profile.family,
                "source": source,
                "status": "ready",
                **capabilities,
                "midi_preset": dict(capabilities["midi"]),
            }
        )
    return result


def _expression_payload(event: Event) -> dict[str, Any] | None:
    if not (event.pitch_cents or event.expression or event.gestures):
        return None
    curves: dict[str, list[dict[str, float]]] = {}
    for point in event.expression:
        curves.setdefault(point.parameter, []).append(
            {"at": point.position, "value": point.value}
        )
    gestures = []
    for gesture in event.gestures:
        item: dict[str, Any] = {"type": gesture.type}
        if gesture.type == "nonghyeon":
            item.update(depth_cents=gesture.depth_cents, rate_hz=gesture.rate_hz)
        elif gesture.type in {"chuseong", "toeseong"}:
            item.update(depth_cents=gesture.depth_cents, position=gesture.position)
        else:
            item["amount"] = gesture.amount
        gestures.append(item)
    return {
        "pitch_cents": event.pitch_cents,
        "curves": curves,
        "gestures": gestures,
    }


def _duration_text(event: Event) -> str:
    from ledgerline.model import duration_token

    kind, dots = duration_token(event.notation_duration)
    denominator = {
        "whole": 1,
        "half": 2,
        "quarter": 4,
        "eighth": 8,
        "16th": 16,
        "32nd": 32,
    }[kind]
    return f"1/{denominator}{'.' * dots}"


def _mix_model(root: Path, state: dict[str, Any]) -> dict[str, Any]:
    config = load_mix_config(root)
    authored = load_mix_document(root)
    effective = mix_config_to_dict(config)
    if config.format != 1:
        effective.pop("legacy_reverb", None)
    return {
        **effective,
        "authored": authored,
        "source": {
            "path": "mix.yaml",
            "sha256": file_sha256(root / "mix.yaml"),
            "authored_revision": state["authored_revision"],
        },
        "master_report": _master_report(config.master, state),
    }


def _master_report(master: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    stage = state["stages"]["mix"]
    provenance = stage.get("provenance") if stage.get("status") == "ready" else None
    provenance = provenance if isinstance(provenance, dict) else {}
    premaster = provenance.get("premaster_measurement")
    final = provenance.get("final_measurement")
    premaster = premaster if isinstance(premaster, dict) else None
    final = final if isinstance(final, dict) else None
    output = stage.get("output") if isinstance(stage.get("output"), dict) else {}
    return {
        "status": stage.get("status", "missing"),
        "bound_to_current_revision": stage.get("status") == "ready",
        "source_revision": (
            state["authored_revision"] if stage.get("status") == "ready" else None
        ),
        "output_sha256": output.get("sha256"),
        "target_lufs": float(master.get("target_lufs", -16.0)),
        "true_peak_ceiling_dbtp": float(master.get("true_peak_ceiling_db", -1.0)),
        "loudness_range_target_lu": float(master.get("loudness_range_lu", 11.0)),
        "loudness_tolerance_lu": float(master.get("loudness_tolerance_lu", 0.5)),
        "integrated_lufs": final.get("integrated_lufs") if final else None,
        "true_peak_dbtp": final.get("true_peak_dbtp") if final else None,
        "loudness_range_lu": final.get("loudness_range_lu") if final else None,
        "premaster_measurement": premaster,
        "final_measurement": final,
    }


def _media_model(
    root: Path,
    piece: Piece,
    musical_duration: float,
    bins: int,
    state: dict[str, Any],
) -> dict[str, Any]:
    build = root / "build"
    stems = []
    render_parts = state["stages"]["render"]["parts"]
    for part in piece.parts:
        path = build / "stems" / f"{part.id}.wav"
        if path.is_file():
            identity = _safe_wav_identity(root, path, musical_duration, bins)
            if identity:
                freshness = render_parts.get(part.id, {"status": "stale"})
                sha256 = identity["sha256"]
                stems.append(
                    {
                        "part": part.id,
                        "kind": "stem",
                        "label": part.name,
                        "url": f"/media/stems/{part.id}.wav?v={sha256}",
                        "spectrogram_url": _spectrogram_url(identity),
                        "status": freshness["status"],
                        "provenance": freshness.get("provenance"),
                        **identity,
                    }
                )
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
        identity = _safe_wav_identity(root, master_path, musical_duration, bins)
        if identity:
            status = (
                state["stages"]["mix"]["status"]
                if master_path == mix_path
                else state["stages"]["render"]["preview"]["status"]
            )
            master = {
                "kind": "master",
                "label": "Master",
                "url": f"/media/{master_path.name}?v={identity['sha256']}",
                "spectrogram_url": _spectrogram_url(identity),
                "status": status,
                "source_revision": (
                    state["authored_revision"] if status == "ready" else None
                ),
                "artifact_revision": identity["sha256"],
                **identity,
            }
            if master_path == mix_path and status == "ready":
                provenance = state["stages"]["mix"].get("provenance") or {}
                master["measurement"] = provenance.get("final_measurement")
    previous_master = _previous_master(root, musical_duration, bins)
    ab = _ab_contract(master, previous_master, state["authored_revision"])
    return {
        "master": master,
        "previous_master": previous_master,
        "ab": ab,
        "stems": stems,
        "spectrogram_url": (
            master.get("spectrogram_url") if master else None
        ),
        "binding": "aligned"
        if master and master["status"] == "ready"
        else "midi-only"
        if master is None
        else "stale",
    }


def _ab_contract(
    current: dict[str, Any] | None,
    previous: dict[str, Any] | None,
    current_revision: str,
) -> dict[str, Any]:
    current_ready = bool(
        current
        and current.get("status") == "ready"
        and current.get("source_revision") == current_revision
    )
    previous_ready = bool(previous and previous.get("status") == "ready")
    distinct = bool(
        current
        and previous
        and current.get("sha256")
        and current.get("sha256") != previous.get("sha256")
    )
    available = current_ready and previous_ready and distinct
    if not current_ready:
        reason = "current-master-not-bound-to-authored-revision"
    elif not previous_ready:
        reason = "previous-master-unavailable"
    elif not distinct:
        reason = "masters-are-identical"
    else:
        reason = None
    durations = [
        float(item["duration_seconds"])
        for item in (current, previous)
        if item and isinstance(item.get("duration_seconds"), (int, float))
    ]
    current_lufs = _measurement_number(current, "integrated_lufs")
    previous_lufs = _measurement_number(previous, "integrated_lufs")
    requested_adjustment = (
        current_lufs - previous_lufs
        if current_lufs is not None and previous_lufs is not None
        else None
    )
    applied_adjustment = None
    peak_limited = False
    if requested_adjustment is not None:
        applied_adjustment = max(-12.0, min(12.0, requested_adjustment))
        previous_peak = _measurement_number(previous, "true_peak_dbtp")
        if previous_peak is not None and applied_adjustment > -0.1 - previous_peak:
            applied_adjustment = max(-12.0, -0.1 - previous_peak)
            peak_limited = True
    level_matching = "integrated-lufs" if applied_adjustment is not None else "none"
    return {
        "schema_version": "1",
        "available": available,
        "unavailable_reason": reason,
        "selection_mode": "exclusive",
        "default_selection": "current",
        "playback_policy": {
            "simultaneous_playback": False,
            "stop_before_switch": True,
            "crossfade_ms": 20,
            "level_matching": level_matching,
            "gain_adjustment_db": {
                "current": 0.0,
                "previous": applied_adjustment if applied_adjustment is not None else 0.0,
                "requested_previous": requested_adjustment,
                "bounds": [-12.0, 12.0],
                "limited": bool(
                    requested_adjustment is not None
                    and not math.isclose(applied_adjustment or 0.0, requested_adjustment)
                ),
                "peak_limited": peak_limited,
            },
        },
        "alignment": {
            "start_seconds": 0.0,
            "common_duration_seconds": min(durations) if len(durations) == 2 else None,
        },
        "current": current,
        "previous": previous,
    }


def _measurement_number(media: dict[str, Any] | None, field: str) -> float | None:
    if not media or not isinstance(media.get("measurement"), dict):
        return None
    value = media["measurement"].get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _previous_master(
    root: Path,
    musical_duration: float,
    bins: int,
) -> dict[str, Any] | None:
    metadata = root / "build" / "studio" / "checkpoints" / "latest.json"
    try:
        record = json.loads(metadata.read_text(encoding="utf-8"))
        audio = record["audio"]
        path = Path(audio["path"]).resolve(strict=True)
        checkpoint_root = metadata.parent.resolve()
        if checkpoint_root not in path.parents:
            return None
        if file_sha256(path) != audio["sha256"]:
            return None
        identity = _safe_wav_identity(root, path, musical_duration, bins)
        if identity is None:
            return None
        relative = path.relative_to(root / "build").as_posix()
        return {
            "kind": "previous-master",
            "url": f"/media/{relative}?v={identity['sha256']}",
            "status": "ready",
            "label": record.get("label"),
            "source_revision": record.get("source_revision"),
            "created_at": record.get("created_at"),
            "measurement": (
                record.get("measurement")
                if isinstance(record.get("measurement"), dict)
                else None
            ),
            **identity,
        }
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _safe_wav_identity(
    root: Path, path: Path, musical_duration: float, bins: int
) -> dict[str, Any] | None:
    try:
        identity = ensure_media_sidecar(root, path, bins=bins)
        return {**identity, "musical_duration_seconds": musical_duration}
    except Exception:
        return None


def _spectrogram_url(identity: dict[str, Any]) -> str | None:
    """Expose an existing sidecar without implying that every source has one."""

    value = identity.get("spectrogram")
    if not isinstance(value, str) or not value:
        return None
    path = Path(value)
    if not path.is_file():
        return None
    return f"/media/studio/media/{path.name}?v={identity['sha256']}"


def _part_color(index: int) -> str:
    colors = ("#4fc4b2", "#e7a95a", "#7ea2d8", "#cf7f8f", "#9caf74", "#b38bd4")
    return colors[index % len(colors)]
