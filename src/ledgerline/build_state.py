from __future__ import annotations

import hashlib
import json
import math
import os
import secrets
import shutil
import struct
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any

import yaml

from ledgerline.render_inputs import part_performance_inputs

STATE_SCHEMA_VERSION = "1"
_RECEIPTS_NAME = "stage-receipts.json"
_JSON_WRITE_LOCK = RLock()


def file_sha256(path: str | Path) -> str:
    """Return a streaming SHA-256 for an artifact or executable."""

    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_identity(path: str | Path) -> dict[str, Any]:
    artifact = Path(path).resolve()
    return {
        "path": str(artifact),
        "bytes": artifact.stat().st_size,
        "sha256": file_sha256(artifact),
    }


def authored_revision(root: str | Path) -> str:
    """Hash every authored input that can affect score, performance, render, or mix."""

    project = Path(root).resolve()
    return _records_revision(_file_records(project, _authored_paths(project)))


def build_state(root: str | Path, *, write: bool = True) -> dict[str, Any]:
    """Project current source and artifact receipts into one Studio-facing state document."""

    project = Path(root).resolve()
    build = project / "build"
    receipts = _read_json(build / _RECEIPTS_NAME) or {}
    revision = authored_revision(project)
    compile_stage = _compile_stage(project)
    render_stage = _render_stage(project, compile_stage, receipts.get("render", {}))
    mix_stage = _mix_stage(project, render_stage, receipts.get("mix"))
    refinement_stage = _refinement_stage(project, revision, receipts.get("refinement"))
    state = {
        "schema_version": STATE_SCHEMA_VERSION,
        "status": "ok",
        "project": str(project),
        "authored_revision": revision,
        "compiled_revision": compile_stage.get("compiled_revision"),
        "rendered_revision": render_stage.get("rendered_revision"),
        "mix_revision": mix_stage.get("output", {}).get("sha256"),
        "refinement_revision": refinement_stage.get("output", {}).get("sha256"),
        "stages": {
            "authored": {"status": "ready", "revision": revision},
            "compile": compile_stage,
            "render": render_stage,
            "mix": mix_stage,
            "refinement": refinement_stage,
        },
        "engines": {
            part_id: part["provenance"]
            for part_id, part in render_stage.get("parts", {}).items()
            if part.get("provenance")
        },
    }
    if write:
        build.mkdir(parents=True, exist_ok=True)
        _write_json_atomic(build / "state.json", state)
    return state


def record_compile(root: str | Path, report: dict[str, Any] | None = None) -> dict[str, Any]:
    """Refresh state after compilation; the compiler manifest remains the source of truth."""

    del report
    return build_state(root)


def record_refinement(root: str | Path, report_path: str | Path) -> dict[str, Any]:
    """Bind a refinement report to the exact authored source it inspected."""

    project = Path(root).resolve()
    path = Path(report_path).resolve(strict=True)
    report = _read_json(path)
    if not isinstance(report, dict):
        raise ValueError("refinement report is not a readable JSON object")
    revision = authored_revision(project)
    if report.get("authored_revision") != revision:
        raise ValueError("refinement report is not bound to the current authored revision")
    try:
        report_project = Path(str(report.get("project", ""))).resolve(strict=True)
    except OSError as exc:
        raise ValueError("refinement report project is invalid") from exc
    if report_project != project:
        raise ValueError("refinement report belongs to a different project")
    receipt = {
        "authored_revision": revision,
        "output": file_identity(path),
        "report_schema_version": report.get("schema_version"),
    }
    receipts = _load_stage_receipts(project)
    receipts["refinement"] = receipt
    _write_stage_receipts(project, receipts)
    return build_state(project)


def record_render(root: str | Path, report: dict[str, Any]) -> dict[str, Any]:
    """Bind actual render provenance and output hashes to the current compiled inputs."""

    project = Path(root).resolve()
    source_revision = report.get("source_revision")
    if not isinstance(source_revision, str) or source_revision != authored_revision(project):
        raise ValueError("render report is not bound to the current authored revision")
    compile_stage = _compile_stage(project)
    if compile_stage["status"] != "ready":
        raise ValueError("cannot receipt render output while compiled artifacts are stale")
    part_profiles = _part_profile_records(project)
    entries: dict[str, Any] = {}
    if isinstance(report.get("nodes"), list):
        for node in report["nodes"]:
            if not isinstance(node, dict) or not isinstance(node.get("part"), str):
                continue
            part_id = node["part"]
            output = _identity_from_report(node.get("output"))
            if output is None:
                continue
            provenance = {
                "engine": node.get("engine"),
                "host_kind": node.get("host_kind"),
                "plugin_format": node.get("plugin_format"),
                "renderer": _identity_from_report(node.get("renderer")),
                "instrument": _identity_from_report(node.get("instrument")),
                "preset_state": _identity_from_report(node.get("state")),
                "profile": part_profiles.get(part_id),
                "latency_samples": node.get("latency_samples"),
                "tail_seconds": node.get("tail_seconds"),
                "cache": node.get("cache"),
                "sample_rate": report.get("sample_rate"),
                "block_size": report.get("block_size"),
            }
            entries[part_id] = _render_receipt(
                project,
                part_id,
                compile_stage["compiled_revision"],
                output,
                provenance,
                cache_key=node.get("cache_key"),
            )
        preview = _identity_from_report(report.get("preview"))
    else:
        renderer = _path_identity(report.get("renderer"))
        instrument = _identity_from_report(report.get("soundfont"))
        preview = None
        for artifact in report.get("artifacts", []):
            if not isinstance(artifact, dict):
                continue
            wav = artifact.get("wav")
            if not isinstance(wav, str):
                continue
            if Path(wav).stem == "preview":
                preview = _identity_from_report(
                    {
                        "path": wav,
                        "bytes": artifact.get("bytes"),
                        "sha256": artifact.get("sha256"),
                    }
                )
                continue
            part_id = Path(wav).stem
            if part_id not in part_profiles:
                continue
            output = _identity_from_report(
                {"path": wav, "bytes": artifact.get("bytes"), "sha256": artifact.get("sha256")}
            )
            if output is None:
                continue
            profile = part_profiles[part_id]
            entries[part_id] = _render_receipt(
                project,
                part_id,
                compile_stage["compiled_revision"],
                output,
                {
                    "engine": "fluidsynth",
                    "host_kind": "external",
                    "renderer": renderer,
                    "instrument": instrument,
                    "preset_state": None,
                    "profile": profile,
                    "preset": profile.get("midi") if profile else None,
                    "sample_rate": report.get("sample_rate"),
                },
                cache_key=artifact.get("cache_key"),
            )
    if not entries:
        raise ValueError("render report contains no receiptable part outputs")
    if preview is not None:
        entries["__preview__"] = _render_receipt(
            project,
            "__preview__",
            compile_stage["compiled_revision"],
            preview,
            {
                "kind": "render-preview",
                "parts": sorted(entries),
                "renderer": _path_identity(report.get("ffmpeg")),
            },
        )
    receipts = _load_stage_receipts(project)
    receipts["render"] = entries
    _write_stage_receipts(project, receipts)
    for item in entries.values():
        ensure_media_sidecar(project, item["output"]["path"])
    return build_state(project)


def record_mix(root: str | Path, report: dict[str, Any]) -> dict[str, Any]:
    """Bind the mastered file to the exact stem and mix input hash used to create it."""

    project = Path(root).resolve()
    source_revision = report.get("source_revision")
    if not isinstance(source_revision, str) or source_revision != authored_revision(project):
        raise ValueError("mix report is not bound to the current authored revision")
    state = build_state(project, write=False)
    render_stage = state["stages"]["render"]
    if render_stage["status"] != "ready":
        raise ValueError("cannot receipt a mix while rendered stems are stale")
    raw_output = report.get("output")
    output = _identity_from_report(raw_output)
    if output is None and isinstance(raw_output, str) and Path(raw_output).is_file():
        output = file_identity(raw_output)
    if output is None:
        candidate = project / "build" / "mix.wav"
        if candidate.is_file():
            output = file_identity(candidate)
    if output is None:
        raise ValueError("mix report does not identify a readable output")
    receipt = {
        "input_hash": _mix_input_hash(project, render_stage),
        "output": output,
        "provenance": {
            "engine": "ffmpeg",
            "executable": _path_identity(report.get("ffmpeg")),
            "premaster_measurement": report.get("premaster_measurement"),
            "final_measurement": report.get("final_measurement"),
        },
    }
    receipts = _load_stage_receipts(project)
    receipts["mix"] = receipt
    _write_stage_receipts(project, receipts)
    ensure_media_sidecar(project, output["path"])
    return build_state(project)


def ensure_media_sidecar(
    root: str | Path,
    audio: str | Path,
    *,
    bins: int = 720,
    ffmpeg: str | Path | None = None,
    spectrogram: bool = False,
    timeout: int = 180,
) -> dict[str, Any]:
    """Create or reuse SHA-keyed waveform metadata and an optional spectrogram."""

    if bins < 1 or bins > 100_000:
        raise ValueError("peak bins must be between 1 and 100000")
    project = Path(root).resolve()
    path = Path(audio).resolve(strict=True)
    identity = file_identity(path)
    media_root = project / "build" / "studio" / "media"
    media_root.mkdir(parents=True, exist_ok=True)
    peaks_path = media_root / f"{identity['sha256']}.peaks.{bins}.json"
    cached = _read_json(peaks_path)
    if not _valid_peak_sidecar(cached, identity["sha256"], bins):
        frames, rate, width = _read_pcm_frames(path)
        mono = frames.mean(axis=1)
        size = max(1, math.ceil(len(mono) / bins))
        peaks = []
        for index in range(0, len(mono), size):
            chunk = mono[index : index + size]
            peaks.append([round(float(chunk.min()), 5), round(float(chunk.max()), 5)])
        cached = {
            "schema_version": "1",
            "source_sha256": identity["sha256"],
            "bins": bins,
            "duration_seconds": len(frames) / rate,
            "sample_rate": rate,
            "channels": int(frames.shape[1]),
            "sample_width": width,
            "peaks": peaks,
        }
        _write_json_atomic(peaks_path, cached)
    spectrogram_path = media_root / f"{identity['sha256']}.spectrogram.png"
    warning = None
    if spectrogram and not spectrogram_path.is_file():
        executable = _resolve_ffmpeg(ffmpeg)
        if executable is not None:
            temporary = media_root / f"{identity['sha256']}.spectrogram.tmp.png"
            command = [
                str(executable),
                "-hide_banner",
                "-y",
                "-i",
                str(path),
                "-lavfi",
                "showspectrumpic=s=2400x320:legend=disabled:color=fiery:scale=log",
                "-frames:v",
                "1",
                str(temporary),
            ]
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                shell=False,
            )
            if completed.returncode == 0 and temporary.is_file():
                os.replace(temporary, spectrogram_path)
            else:
                temporary.unlink(missing_ok=True)
                warning = completed.stderr[-1000:]
    return {
        **identity,
        **{key: cached[key] for key in ("duration_seconds", "sample_rate", "channels")},
        "sample_width": cached["sample_width"],
        "peaks": cached["peaks"],
        "peaks_sidecar": str(peaks_path),
        "spectrogram": str(spectrogram_path) if spectrogram_path.is_file() else None,
        "warning": warning,
    }


def archive_media_checkpoint(
    root: str | Path,
    *,
    label: str = "before-edit",
) -> dict[str, Any] | None:
    """Preserve the current revision-matched master for later Studio A/B review."""

    project = Path(root).resolve()
    state = build_state(project, write=False)
    mix = state["stages"]["mix"]
    render_preview = state["stages"]["render"].get("preview", {})
    if mix.get("status") == "ready":
        source = Path(mix["output"]["path"])
        stage = "mix"
    elif render_preview.get("status") == "ready":
        source = Path(render_preview["output"]["path"])
        stage = "render-preview"
    else:
        return None
    identity = file_identity(source)
    checkpoint_root = project / "build" / "studio" / "checkpoints"
    checkpoint_root.mkdir(parents=True, exist_ok=True)
    destination = checkpoint_root / f"{identity['sha256']}{source.suffix.lower()}"
    if not destination.is_file() or file_sha256(destination) != identity["sha256"]:
        shutil.copyfile(source, destination)
    archived = file_identity(destination)
    record = {
        "schema_version": "1",
        "status": "ready",
        "label": str(label),
        "stage": stage,
        "source_revision": state["authored_revision"],
        "created_at": datetime.now(UTC).isoformat(),
        "audio": archived,
        "measurement": (
            mix.get("provenance", {}).get("final_measurement")
            if stage == "mix" and isinstance(mix.get("provenance"), dict)
            else None
        ),
    }
    _write_json_atomic(checkpoint_root / "latest.json", record)
    ensure_media_sidecar(project, destination)
    return record


def _compile_stage(project: Path) -> dict[str, Any]:
    manifest_path = project / "build" / "manifest.json"
    manifest = _read_json(manifest_path)
    current_inputs = _file_records(project, _compile_input_paths(project))
    input_revision = _records_revision(current_inputs)
    if not isinstance(manifest, dict):
        return {"status": "missing", "input_revision": input_revision, "reason": "manifest missing"}
    if not isinstance(manifest.get("source_revision"), str):
        return {
            "status": "stale",
            "input_revision": input_revision,
            "reason": "compile manifest has no source revision",
        }
    recorded_inputs = {
        str(item.get("path")): item.get("sha256")
        for item in manifest.get("inputs", [])
        if str(item.get("path")) in current_inputs
    }
    expected_inputs = {path: item["sha256"] for path, item in current_inputs.items()}
    outputs = _verified_manifest_outputs(project / "build", manifest.get("outputs"))
    if recorded_inputs != expected_inputs:
        return {
            "status": "stale",
            "input_revision": input_revision,
            "reason": "authored compile inputs changed",
        }
    if outputs is None:
        return {
            "status": "stale",
            "input_revision": input_revision,
            "reason": "compiled output is missing or changed",
        }
    if not _profiles_match_manifest(project, manifest.get("profiles")):
        return {
            "status": "stale",
            "input_revision": input_revision,
            "reason": "instrument profile changed",
        }
    compiled_revision = _records_revision(outputs)
    return {
        "status": "ready",
        "input_revision": input_revision,
        "compiled_revision": compiled_revision,
        "manifest": str(manifest_path),
        "outputs": outputs,
    }


def _render_stage(
    project: Path, compile_stage: dict[str, Any], runtime_receipts: Any
) -> dict[str, Any]:
    part_profiles = _part_profile_records(project)
    parts: dict[str, Any] = {}
    graph_parts = _graph_render_parts(project, compile_stage)
    runtime = runtime_receipts if isinstance(runtime_receipts, dict) else {}
    for part_id in part_profiles:
        graph_receipt = graph_parts.get(part_id)
        if graph_receipt is not None:
            parts[part_id] = graph_receipt
            continue
        receipt = runtime.get(part_id)
        output_path = project / "build" / "stems" / f"{part_id}.wav"
        if compile_stage["status"] != "ready":
            parts[part_id] = _stale_or_missing(output_path, "compiled artifacts are stale")
        elif not isinstance(receipt, dict):
            parts[part_id] = _stale_or_missing(output_path, "render receipt missing")
        else:
            parts[part_id] = _validate_runtime_render_receipt(
                project, part_id, compile_stage["compiled_revision"], receipt
            )
    statuses = [item["status"] for item in parts.values()]
    if statuses and all(status == "ready" for status in statuses):
        status = "ready"
    elif statuses and all(status == "missing" for status in statuses):
        status = "missing"
    else:
        status = "stale"
    outputs = {
        part_id: item["output"]
        for part_id, item in parts.items()
        if item.get("status") == "ready"
    }
    rendered_revision = _records_revision(outputs) if status == "ready" else None
    sample_rates = {
        item.get("provenance", {}).get("sample_rate")
        for item in parts.values()
        if item.get("status") == "ready" and item.get("provenance", {}).get("sample_rate")
    }
    preview_path = project / "build" / "preview.wav"
    preview_receipt = runtime.get("__preview__")
    if compile_stage["status"] != "ready":
        preview = _stale_or_missing(preview_path, "compiled artifacts are stale")
    elif isinstance(preview_receipt, dict):
        preview = _validate_runtime_render_receipt(
            project, "__preview__", compile_stage["compiled_revision"], preview_receipt
        )
    else:
        preview = _stale_or_missing(preview_path, "preview render receipt missing")
    return {
        "status": status,
        "parts": parts,
        "preview": preview,
        "rendered_revision": rendered_revision,
        "sample_rate": next(iter(sample_rates)) if len(sample_rates) == 1 else None,
    }


def _mix_stage(
    project: Path, render_stage: dict[str, Any], receipt: Any
) -> dict[str, Any]:
    output = project / "build" / "mix.wav"
    if render_stage["status"] != "ready":
        return {
            "status": "blocked" if output.is_file() else "missing",
            "reason": "one or more rendered stems are stale",
            **({"output": file_identity(output)} if output.is_file() else {}),
        }
    current_input = _mix_input_hash(project, render_stage)
    if not isinstance(receipt, dict):
        return {
            "status": "stale" if output.is_file() else "missing",
            "input_hash": current_input,
            "reason": "mix receipt missing",
            **({"output": file_identity(output)} if output.is_file() else {}),
        }
    recorded_output = receipt.get("output")
    if receipt.get("input_hash") != current_input:
        return {
            "status": "stale",
            "input_hash": current_input,
            "reason": "mix inputs changed",
            **({"output": file_identity(output)} if output.is_file() else {}),
        }
    if not _identity_matches(recorded_output):
        return {
            "status": "stale" if output.is_file() else "missing",
            "input_hash": current_input,
            "reason": "master output is missing or changed",
            **({"output": file_identity(output)} if output.is_file() else {}),
        }
    return {
        "status": "ready",
        "input_hash": current_input,
        "output": recorded_output,
        "provenance": receipt.get("provenance"),
    }


def _refinement_stage(project: Path, revision: str, receipt: Any) -> dict[str, Any]:
    default_path = project / "build" / "refinement" / "report.json"
    if not isinstance(receipt, dict):
        return {
            "status": "stale" if default_path.is_file() else "missing",
            "authored_revision": revision,
            "reason": (
                "refinement report receipt missing"
                if default_path.is_file()
                else "refinement report missing"
            ),
            **({"output": file_identity(default_path)} if default_path.is_file() else {}),
        }
    output = receipt.get("output")
    output_path = (
        Path(output["path"])
        if isinstance(output, dict) and isinstance(output.get("path"), str)
        else default_path
    )
    if not output_path.is_file():
        return {
            "status": "missing",
            "authored_revision": revision,
            "reason": "receipted refinement report is missing",
        }
    actual = file_identity(output_path)
    if not _identity_matches(output):
        return {
            "status": "stale",
            "authored_revision": revision,
            "reason": "refinement report identity changed",
            "output": actual,
        }
    report = _read_json(output_path)
    if not isinstance(report, dict):
        return {
            "status": "stale",
            "authored_revision": revision,
            "reason": "refinement report is damaged or is not a JSON object",
            "output": actual,
        }
    report_revision = report.get("authored_revision")
    if not isinstance(report_revision, str):
        return {
            "status": "stale",
            "authored_revision": revision,
            "reason": "legacy refinement report has no authored revision",
            "output": actual,
        }
    if (
        receipt.get("authored_revision") != revision
        or report_revision != revision
    ):
        return {
            "status": "stale",
            "authored_revision": revision,
            "report_authored_revision": report_revision,
            "reason": "authored source changed after refinement analysis",
            "output": actual,
        }
    if report.get("schema_version") != receipt.get("report_schema_version"):
        return {
            "status": "stale",
            "authored_revision": revision,
            "reason": "refinement report schema does not match its receipt",
            "output": actual,
        }
    try:
        report_project = Path(str(report.get("project", ""))).resolve(strict=True)
    except OSError:
        report_project = None
    if report_project != project:
        return {
            "status": "stale",
            "authored_revision": revision,
            "reason": "refinement report belongs to a different project",
            "output": actual,
        }
    return {
        "status": "ready",
        "authored_revision": revision,
        "output": output,
        "report_status": report.get("status"),
        "gates": report.get("gates"),
    }


def _graph_render_parts(project: Path, compile_stage: dict[str, Any]) -> dict[str, Any]:
    if not (project / "render.yaml").is_file() or compile_stage.get("status") != "ready":
        return {}
    try:
        from ledgerline.project import load_piece
        from ledgerline.render_graph import _node_cache_key, load_render_graph

        graph = load_render_graph(project, load_piece(project))
        report = _read_json(project / "build" / "render-report.json") or {}
        if not isinstance(report.get("source_revision"), str):
            return {}
        report_nodes = {
            item.get("part"): item
            for item in report.get("nodes", [])
            if isinstance(item, dict) and isinstance(item.get("part"), str)
        }
        result = {}
        for node in graph.nodes:
            midi = project / "build" / "parts" / f"{node.part}.mid"
            output = project / "build" / "stems" / f"{node.part}.wav"
            cache_key = _node_cache_key(node, midi, graph, project / "build" / "automation.json")
            raw_receipt = _read_json(project / "build" / "render-cache" / f"{node.id}.json")
            actual = report_nodes.get(node.part)
            if (
                isinstance(raw_receipt, dict)
                and raw_receipt.get("cache_key") == cache_key
                and output.is_file()
                and raw_receipt.get("output_sha256") == file_sha256(output)
                and isinstance(actual, dict)
                and actual.get("cache_key") == cache_key
                and _identity_matches(actual.get("output"))
            ):
                result[node.part] = {
                    "status": "ready",
                    "cache_key": cache_key,
                    "output": actual["output"],
                    "provenance": {
                        **{
                            key: actual.get(key)
                            for key in (
                                "engine",
                                "plugin_format",
                                "instrument",
                                "renderer",
                                "host_kind",
                                "latency_samples",
                                "tail_seconds",
                                "cache",
                            )
                        },
                        "preset_state": actual.get("state"),
                        "sample_rate": report.get("sample_rate"),
                        "block_size": report.get("block_size"),
                    },
                }
            else:
                result[node.part] = _stale_or_missing(output, "render graph cache key changed")
        return result
    except Exception:
        return {}


def _render_receipt(
    project: Path,
    part_id: str,
    compiled_revision: str,
    output: dict[str, Any],
    provenance: dict[str, Any],
    *,
    cache_key: Any = None,
) -> dict[str, Any]:
    return {
        "compiled_revision": compiled_revision,
        "input_hash": _render_input_hash(project, part_id, provenance),
        "cache_key": cache_key,
        "output": output,
        "provenance": provenance,
    }


def _validate_runtime_render_receipt(
    project: Path, part_id: str, compiled_revision: str, receipt: dict[str, Any]
) -> dict[str, Any]:
    output = receipt.get("output")
    provenance = receipt.get("provenance")
    del compiled_revision
    expected = _render_input_hash(project, part_id, provenance)
    if receipt.get("input_hash") != expected:
        path = Path(str(output.get("path", ""))) if isinstance(output, dict) else Path()
        return _stale_or_missing(path, "render inputs changed")
    if not _identity_matches(output) or not _provenance_matches(provenance):
        path = Path(str(output.get("path", ""))) if isinstance(output, dict) else Path()
        return _stale_or_missing(path, "render output or engine asset changed")
    return {
        "status": "ready",
        "input_hash": expected,
        "cache_key": receipt.get("cache_key"),
        "output": output,
        "provenance": provenance,
    }


def _render_input_hash(project: Path, part_id: str, provenance: Any) -> str:
    if part_id == "__preview__":
        raw_parts = provenance.get("parts", []) if isinstance(provenance, dict) else []
        part_ids = sorted(str(item) for item in raw_parts if isinstance(item, str))
        performance = {
            current: {
                **part_performance_inputs(project, current),
                "stem": _optional_sha(project / "build" / "stems" / f"{current}.wav"),
            }
            for current in part_ids
        }
    else:
        performance = part_performance_inputs(project, part_id)
    inputs = {
        "part": part_id,
        "performance": performance,
        "render_config": _optional_sha(project / "render.yaml"),
        "provenance": provenance,
    }
    return _json_hash(inputs)


def _mix_input_hash(project: Path, render_stage: dict[str, Any]) -> str:
    payload = {
        "mix": _optional_sha(project / "mix.yaml"),
        "automation": _optional_sha(project / "build" / "automation.json"),
        "stems": {
            part_id: part["output"]["sha256"]
            for part_id, part in sorted(render_stage.get("parts", {}).items())
            if part.get("status") == "ready"
        },
    }
    return _json_hash(payload)


def _part_profile_records(project: Path) -> dict[str, dict[str, Any]]:
    manifest = _read_json(project / "build" / "manifest.json") or {}
    profiles = manifest.get("profiles", [])
    parts = manifest.get("parts", [])
    result = {}
    for index, part in enumerate(parts):
        if isinstance(part, dict) and index < len(profiles) and isinstance(profiles[index], dict):
            result[str(part.get("id"))] = profiles[index]
    if result:
        return result
    try:
        from ledgerline.project import load_piece

        piece = load_piece(project)
        return {
            part.id: {
                "id": profile.id,
                "name": profile.name,
                "midi": {
                    "bank_msb": profile.bank_msb,
                    "bank_lsb": profile.bank_lsb,
                    "program": profile.program,
                },
            }
            for part in piece.parts
            for profile in (piece.profiles[part.profile_id],)
        }
    except Exception:
        return {}


def _compile_input_paths(project: Path) -> list[Path]:
    piece_path = project / "piece.yaml"
    paths = [piece_path, *sorted((project / "parts").glob("*.yaml"))]
    try:
        piece_data = yaml.safe_load(piece_path.read_text(encoding="utf-8"))
        for part in piece_data.get("parts", []):
            reference = (project / str(part["file"])).resolve()
            if project == reference or project in reference.parents:
                paths.append(reference)
    except (OSError, AttributeError, KeyError, TypeError, yaml.YAMLError):
        pass
    paths.extend(
        path
        for name in ("automation.yaml", "performance.yaml", "motifs.yaml")
        if (path := project / name).is_file()
    )
    return paths


def _authored_paths(project: Path) -> list[Path]:
    paths = _compile_input_paths(project)
    paths.extend(sorted((project / "profiles").glob("*.yaml")))
    paths.extend(
        path
        for name in ("mix.yaml", "render.yaml", "brief.yaml")
        if (path := project / name).is_file()
    )
    return sorted(set(paths))


def _profiles_match_manifest(project: Path, recorded: Any) -> bool:
    if not isinstance(recorded, list):
        return False
    try:
        from ledgerline.compiler import _profile_record
        from ledgerline.project import load_piece

        piece = load_piece(project)
        current = [_profile_record(piece.profiles[part.profile_id]) for part in piece.parts]
        return [item.get("sha256") for item in current] == [
            item.get("sha256") for item in recorded if isinstance(item, dict)
        ]
    except Exception:
        return False


def _file_records(project: Path, paths: list[Path]) -> dict[str, dict[str, Any]]:
    return {
        path.relative_to(project).as_posix(): {
            "bytes": path.stat().st_size,
            "sha256": file_sha256(path),
        }
        for path in paths
        if path.is_file()
    }


def _verified_manifest_outputs(build: Path, raw: Any) -> dict[str, dict[str, Any]] | None:
    if not isinstance(raw, list) or not raw:
        return None
    result = {}
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            return None
        path = (build / item["path"]).resolve()
        if build.resolve() not in path.parents or not path.is_file():
            return None
        identity = {"bytes": path.stat().st_size, "sha256": file_sha256(path)}
        if item.get("bytes") != identity["bytes"] or item.get("sha256") != identity["sha256"]:
            return None
        result[item["path"]] = identity
    return result


def _identity_from_report(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, str):
        return file_identity(raw) if Path(raw).is_file() else None
    if not isinstance(raw, dict) or not isinstance(raw.get("path"), str):
        return None
    path = Path(raw["path"])
    if not path.is_file():
        return None
    actual = file_identity(path)
    if raw.get("sha256") not in {None, actual["sha256"]}:
        return None
    return actual


def _path_identity(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, str) or not Path(raw).is_file():
        return None
    return file_identity(raw)


def _identity_matches(raw: Any) -> bool:
    if not isinstance(raw, dict) or not isinstance(raw.get("path"), str):
        return False
    path = Path(raw["path"])
    return (
        path.is_file()
        and raw.get("bytes") == path.stat().st_size
        and raw.get("sha256") == file_sha256(path)
    )


def _provenance_matches(raw: Any) -> bool:
    if not isinstance(raw, dict):
        return False
    for key in ("renderer", "instrument", "preset_state", "executable"):
        identity = raw.get(key)
        if identity is not None and not _identity_matches(identity):
            return False
    return True


def _stale_or_missing(path: Path, reason: str) -> dict[str, Any]:
    if path.is_file():
        return {"status": "stale", "reason": reason, "output": file_identity(path)}
    return {"status": "missing", "reason": reason}


def _records_revision(records: dict[str, Any]) -> str:
    return _json_hash(records)


def _json_hash(value: Any) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _optional_sha(path: Path) -> str | None:
    return file_sha256(path) if path.is_file() else None


def _load_stage_receipts(project: Path) -> dict[str, Any]:
    return _read_json(project / "build" / _RECEIPTS_NAME) or {"schema_version": "1"}


def _write_stage_receipts(project: Path, receipts: dict[str, Any]) -> None:
    receipts["schema_version"] = "1"
    _write_json_atomic(project / "build" / _RECEIPTS_NAME, receipts)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else None
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    with _JSON_WRITE_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(
            f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
        )
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def _valid_peak_sidecar(raw: Any, sha256: str, bins: int) -> bool:
    return (
        isinstance(raw, dict)
        and raw.get("source_sha256") == sha256
        and raw.get("bins") == bins
        and isinstance(raw.get("peaks"), list)
    )


def _resolve_ffmpeg(value: str | Path | None) -> Path | None:
    if value is not None:
        candidate = Path(value).resolve()
        return candidate if candidate.is_file() else None
    try:
        from ledgerline.audio import resolve_ffmpeg

        return resolve_ffmpeg()
    except Exception:
        return None


def _read_pcm_frames(path: Path):
    try:
        from ledgerline.pcm import read_pcm_wav

        return read_pcm_wav(path)
    except Exception as original:
        try:
            return _read_extensible_pcm(path)
        except Exception:
            raise original from None


def _read_extensible_pcm(path: Path):
    import numpy as np

    raw = path.read_bytes()
    if len(raw) < 12 or raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
        raise ValueError("audio is not a RIFF/WAVE file")
    fmt = None
    audio = None
    offset = 12
    while offset + 8 <= len(raw):
        chunk_id = raw[offset : offset + 4]
        size = struct.unpack_from("<I", raw, offset + 4)[0]
        start = offset + 8
        end = start + size
        if end > len(raw):
            raise ValueError("WAV chunk exceeds file size")
        if chunk_id == b"fmt ":
            fmt = raw[start:end]
        elif chunk_id == b"data":
            audio = raw[start:end]
        offset = end + (size & 1)
    if fmt is None or audio is None or len(fmt) < 16:
        raise ValueError("WAV fmt or data chunk is missing")
    tag, channels, sample_rate, _average, block_align, bits = struct.unpack_from(
        "<HHIIHH", fmt
    )
    if tag == 0xFFFE:
        if len(fmt) < 40 or struct.unpack_from("<I", fmt, 24)[0] != 1:
            raise ValueError("WAVE_FORMAT_EXTENSIBLE is not PCM")
    elif tag != 1:
        raise ValueError("WAV is not PCM")
    width = bits // 8
    if channels < 1 or width not in {2, 3, 4} or block_align != channels * width:
        raise ValueError("unsupported PCM sample layout")
    if len(audio) % block_align:
        raise ValueError("PCM data is not frame aligned")
    if width == 2:
        integers = np.frombuffer(audio, dtype="<i2").astype(np.int32)
        scale = 2**15
    elif width == 4:
        integers = np.frombuffer(audio, dtype="<i4")
        scale = 2**31
    else:
        packed = np.frombuffer(audio, dtype=np.uint8).reshape(-1, 3)
        integers = (
            packed[:, 0].astype(np.int32)
            | (packed[:, 1].astype(np.int32) << 8)
            | (packed[:, 2].astype(np.int32) << 16)
        )
        integers = np.where(integers & 0x800000, integers - 0x1000000, integers)
        scale = 2**23
    frames = integers.astype(np.float64).reshape(-1, channels) / scale
    return frames, sample_rate, width
