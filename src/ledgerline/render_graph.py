from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ledgerline.audio import resolve_ffmpeg
from ledgerline.diagnostics import CapabilityError, Diagnostic, ValidationError
from ledgerline.model import Piece
from ledgerline.soundfont import read_presets
from ledgerline.timeline import Timeline

ENGINES = {"fluidsynth", "sfizz", "plugin", "frozen"}
PLUGIN_FORMATS = {"vst3", "clap"}


@dataclass(frozen=True, slots=True)
class RenderNode:
    id: str
    part: str
    engine: str
    executable: Path | None
    arguments: tuple[str, ...]
    instrument: Path
    plugin_format: str | None
    state: Path | None
    latency_samples: int
    tail_seconds: float


@dataclass(frozen=True, slots=True)
class RenderGraph:
    root: Path
    sample_rate: int
    block_size: int
    tail_seconds: float
    nodes: tuple[RenderNode, ...]
    max_render_seconds: float
    max_stem_mb: float
    max_cache_mb: float


def load_render_graph(root: str | Path, piece: Piece) -> RenderGraph:
    project = Path(root).resolve()
    path = project / "render.yaml"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("render root must be a mapping")
        _unknown(
            data,
            {"format", "sample_rate", "block_size", "tail_seconds", "nodes", "resources"},
            "render.yaml",
        )
        if data.get("format") != 1:
            raise ValueError("render format must be 1")
        sample_rate = _bounded_int(data.get("sample_rate", 48_000), 8_000, 384_000, "sample_rate")
        block_size = _bounded_int(data.get("block_size", 512), 16, 16_384, "block_size")
        tail_seconds = _bounded_float(data.get("tail_seconds", 2.0), 0.0, 600.0, "tail_seconds")
        raw_nodes = data.get("nodes")
        if not isinstance(raw_nodes, list) or not raw_nodes:
            raise ValueError("render nodes must be a non-empty list")
        nodes = tuple(_node_from_dict(project, raw, index) for index, raw in enumerate(raw_nodes))
        _validate_graph_nodes(nodes, piece)
        resources = data.get("resources", {})
        if not isinstance(resources, dict):
            raise ValueError("resources must be a mapping")
        _unknown(
            resources,
            {"max_render_seconds", "max_stem_mb", "max_cache_mb"},
            "render.yaml.resources",
        )
        max_render_seconds = _bounded_float(
            resources.get("max_render_seconds", 7_200.0),
            1.0,
            86_400.0,
            "max_render_seconds",
        )
        max_stem_mb = _bounded_float(
            resources.get("max_stem_mb", 4_096.0), 1.0, 1_000_000.0, "max_stem_mb"
        )
        max_cache_mb = _bounded_float(
            resources.get("max_cache_mb", 16_384.0), 1.0, 1_000_000.0, "max_cache_mb"
        )
        return RenderGraph(
            project,
            sample_rate,
            block_size,
            tail_seconds,
            nodes,
            max_render_seconds,
            max_stem_mb,
            max_cache_mb,
        )
    except (OSError, yaml.YAMLError, TypeError, ValueError, KeyError) as exc:
        raise ValidationError(
            "render.yaml is invalid",
            [Diagnostic("error", "render.graph_invalid", str(path), str(exc))],
        ) from exc


def render_graph_project(
    project: str | Path,
    *,
    ffmpeg: str | Path | None = None,
    timeout: int = 300,
) -> dict:
    from ledgerline.project import load_piece

    root = Path(project).resolve()
    piece = load_piece(root)
    graph = load_render_graph(root, piece)
    build = root / "build"
    parts_dir = build / "parts"
    if not parts_dir.is_dir():
        raise CapabilityError(
            "compiled part MIDI files are missing",
            [
                Diagnostic(
                    "error",
                    "render.midi_missing",
                    str(parts_dir),
                    "Run ledgerline compile first.",
                )
            ],
        )
    ffmpeg_path = resolve_ffmpeg(ffmpeg)
    stems = build / "stems"
    raw_stems = build / "render-raw"
    receipts = build / "render-cache"
    quarantine = build / "render-quarantine"
    for directory in (stems, raw_stems, receipts, quarantine):
        directory.mkdir(parents=True, exist_ok=True)
    timeline = Timeline(piece, graph.sample_rate)
    total_samples = timeline.total_samples(
        tail_seconds=max([graph.tail_seconds, *(node.tail_seconds for node in graph.nodes)])
    )
    duration_seconds = total_samples / graph.sample_rate
    if duration_seconds > graph.max_render_seconds:
        raise CapabilityError(
            "render exceeds the authored resource budget",
            [
                Diagnostic(
                    "error",
                    "render.duration_budget",
                    "render.yaml.resources.max_render_seconds",
                    f"estimated {duration_seconds:.3f}s > {graph.max_render_seconds:.3f}s",
                )
            ],
        )
    cache_mb = sum(_asset_size(path) for path in (receipts, raw_stems, stems)) / (1024 * 1024)
    if cache_mb > graph.max_cache_mb:
        raise CapabilityError(
            "render cache exceeds the authored resource budget",
            [
                Diagnostic(
                    "error",
                    "render.cache_budget",
                    str(receipts),
                    f"{cache_mb:.3f} MiB > {graph.max_cache_mb:.3f} MiB",
                )
            ],
        )
    rendered: list[dict] = []
    for node in graph.nodes:
        midi = parts_dir / f"{node.part}.mid"
        output = stems / f"{node.part}.wav"
        raw_output = raw_stems / f"{node.part}.wav"
        receipt_path = receipts / f"{node.id}.json"
        cache_key = _node_cache_key(node, midi, graph, build / "automation.json")
        cached = _valid_cache(receipt_path, output, cache_key)
        if not cached:
            raw_output.unlink(missing_ok=True)
            try:
                _run_node(node, midi, raw_output, graph, build, timeout)
                _align_stem(
                    raw_output,
                    output,
                    ffmpeg_path,
                    latency_samples=node.latency_samples,
                    total_samples=total_samples,
                    sample_rate=graph.sample_rate,
                    timeout=timeout,
                )
                _write_receipt(receipt_path, node, output, cache_key)
            except Exception as exc:
                failure = quarantine / f"{node.id}.json"
                failure.write_text(
                    json.dumps(
                        {"node": node.id, "engine": node.engine, "error": str(exc)},
                        ensure_ascii=False,
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                raise
        stem_mb = output.stat().st_size / (1024 * 1024)
        if stem_mb > graph.max_stem_mb:
            raise CapabilityError(
                "rendered stem exceeds the authored resource budget",
                [
                    Diagnostic(
                        "error",
                        "render.stem_budget",
                        str(output),
                        f"{stem_mb:.3f} MiB > {graph.max_stem_mb:.3f} MiB",
                    )
                ],
            )
        rendered.append(
            {
                "node": node.id,
                "part": node.part,
                "engine": node.engine,
                "plugin_format": node.plugin_format,
                "instrument": _file_identity(node.instrument),
                "renderer": _file_identity(node.executable) if node.executable else None,
                "host_kind": "bundled-reference"
                if node.engine == "plugin" and node.executable is None
                else "external",
                "latency_samples": node.latency_samples,
                "tail_seconds": node.tail_seconds,
                "cache": "hit" if cached else "miss",
                "cache_key": cache_key,
                "output": _file_identity(output),
            }
        )
    preview = build / "preview.wav"
    _mix_preview([Path(item["output"]["path"]) for item in rendered], preview, ffmpeg_path, timeout)
    report = {
        "schema_version": "2",
        "status": "ok",
        "project": str(root),
        "sample_rate": graph.sample_rate,
        "block_size": graph.block_size,
        "estimated_duration_seconds": total_samples / graph.sample_rate,
        "estimated_samples": total_samples,
        "resource_budget": {
            "max_render_seconds": graph.max_render_seconds,
            "max_stem_mb": graph.max_stem_mb,
            "max_cache_mb": graph.max_cache_mb,
        },
        "nodes": rendered,
        "preview": _file_identity(preview),
    }
    (build / "render-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def _node_from_dict(root: Path, raw: Any, index: int) -> RenderNode:
    path = f"render.yaml:nodes[{index}]"
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must be a mapping")
    allowed = {
        "id",
        "part",
        "engine",
        "executable",
        "arguments",
        "instrument",
        "plugin_format",
        "state",
        "latency_samples",
        "tail_seconds",
    }
    _unknown(raw, allowed, path)
    node_id = raw.get("id")
    part = raw.get("part")
    engine = raw.get("engine")
    if not isinstance(node_id, str) or not node_id:
        raise ValueError(f"{path}.id must be non-empty")
    if not isinstance(part, str) or not part:
        raise ValueError(f"{path}.part must be non-empty")
    if engine not in ENGINES:
        raise ValueError(f"{path}.engine is unsupported: {engine!r}")
    executable = None
    if engine not in {"frozen", "plugin"} or (engine == "plugin" and raw.get("executable")):
        executable = _resolve_file(root, raw.get("executable"), f"{path}.executable")
    elif raw.get("executable") is not None:
        raise ValueError(f"{path}.executable does not apply to a frozen node")
    instrument = _resolve_asset(root, raw.get("instrument"), f"{path}.instrument")
    if engine == "plugin" and executable is None and not instrument.name.endswith(".llplugin.json"):
        raise ValueError(
            f"{path}.instrument must be a .llplugin.json manifest when executable is omitted"
        )
    raw_arguments = raw.get("arguments", [])
    if not isinstance(raw_arguments, list) or not all(
        isinstance(item, str) for item in raw_arguments
    ):
        raise ValueError(f"{path}.arguments must be a string list")
    plugin_format = raw.get("plugin_format")
    if engine == "plugin" and plugin_format not in PLUGIN_FORMATS:
        raise ValueError(f"{path}.plugin_format must be vst3 or clap")
    if engine != "plugin" and plugin_format is not None:
        raise ValueError(f"{path}.plugin_format only applies to plugin nodes")
    state = None
    if raw.get("state") is not None:
        state = _resolve_file(root, raw["state"], f"{path}.state")
    latency = _bounded_int(raw.get("latency_samples", 0), 0, 10_000_000, "latency_samples")
    tail = _bounded_float(raw.get("tail_seconds", 0.0), 0.0, 600.0, "tail_seconds")
    return RenderNode(
        node_id,
        part,
        str(engine),
        executable,
        tuple(raw_arguments),
        instrument,
        plugin_format,
        state,
        latency,
        tail,
    )


def _validate_graph_nodes(nodes: tuple[RenderNode, ...], piece: Piece) -> None:
    ids = [node.id for node in nodes]
    parts = [node.part for node in nodes]
    expected = [part.id for part in piece.parts]
    if len(ids) != len(set(ids)):
        raise ValueError("render node ids must be unique")
    if len(parts) != len(set(parts)):
        raise ValueError("each part must have exactly one render node")
    if set(parts) != set(expected):
        missing = sorted(set(expected) - set(parts))
        unknown = sorted(set(parts) - set(expected))
        raise ValueError(f"render parts differ; missing={missing}, unknown={unknown}")


def _run_node(
    node: RenderNode,
    midi: Path,
    output: Path,
    graph: RenderGraph,
    build: Path,
    timeout: int,
) -> None:
    if node.engine == "frozen":
        shutil.copyfile(node.instrument, output)
        return
    if node.executable is None and node.engine != "plugin":
        raise AssertionError("non-frozen render nodes require an executable")
    if node.engine == "fluidsynth":
        _check_soundfont_coverage(node.instrument, midi, node.part, graph.root)
        command = [
            str(node.executable),
            *node.arguments,
            "-ni",
            "-q",
            "-r",
            str(graph.sample_rate),
            "-T",
            "wav",
            "-F",
            str(output),
            str(node.instrument),
            str(midi),
        ]
    elif node.engine == "sfizz":
        command = [
            str(node.executable),
            *node.arguments,
            "--wav",
            str(output),
            "--sfz",
            str(node.instrument),
            "--midi",
            str(midi),
            "--samplerate",
            str(graph.sample_rate),
            "--blocksize",
            str(graph.block_size),
        ]
    else:
        request_path = build / "render-requests" / f"{node.id}.json"
        request_path.parent.mkdir(parents=True, exist_ok=True)
        request = {
            "schema_version": "1",
            "plugin_format": node.plugin_format,
            "plugin": str(node.instrument),
            "state": str(node.state) if node.state else None,
            "midi": str(midi),
            "wav": str(output),
            "sample_rate": graph.sample_rate,
            "block_size": graph.block_size,
            "offline": True,
            "latency_samples": node.latency_samples,
            "tail_seconds": node.tail_seconds,
            "automation": _plugin_parameter_automation(graph, node, build),
            "note_expression": _plugin_note_expression(node, build),
        }
        request_path.write_text(json.dumps(request, indent=2) + "\n", encoding="utf-8")
        if node.executable is None:
            command = [
                sys.executable,
                "-m",
                "ledgerline.reference_host",
                "--ledgerline-request",
                str(request_path),
            ]
        else:
            command = [
                str(node.executable),
                *node.arguments,
                "--ledgerline-request",
                str(request_path),
            ]
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
        shell=False,
        cwd=str(graph.root),
    )
    if completed.returncode != 0 or not output.is_file() or output.stat().st_size <= 44:
        raise CapabilityError(
            f"render node failed: {node.id}",
            [
                Diagnostic(
                    "error",
                    "render.node_failed",
                    node.id,
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


def _align_stem(
    source: Path,
    output: Path,
    ffmpeg: Path,
    *,
    latency_samples: int,
    total_samples: int,
    sample_rate: int,
    timeout: int,
) -> None:
    temporary = output.with_suffix(".aligned.wav")
    filter_graph = f"atrim=start_sample={latency_samples},apad,atrim=end_sample={total_samples}"
    command = [
        str(ffmpeg),
        "-hide_banner",
        "-y",
        "-i",
        str(source),
        "-af",
        filter_graph,
        "-c:a",
        "pcm_s24le",
        "-ar",
        str(sample_rate),
        str(temporary),
    ]
    completed = subprocess.run(
        command, capture_output=True, text=True, timeout=timeout, shell=False
    )
    if completed.returncode != 0 or not temporary.is_file():
        raise CapabilityError(
            "stem latency/tail alignment failed",
            [Diagnostic("error", "render.align_failed", str(source), completed.stderr[-2000:])],
        )
    os.replace(temporary, output)


def _mix_preview(inputs: list[Path], output: Path, ffmpeg: Path, timeout: int) -> None:
    command = [str(ffmpeg), "-hide_banner", "-y"]
    for path in inputs:
        command.extend(["-i", str(path)])
    command.extend(
        [
            "-filter_complex",
            f"amix=inputs={len(inputs)}:normalize=0",
            "-c:a",
            "pcm_s24le",
            str(output),
        ]
    )
    completed = subprocess.run(
        command, capture_output=True, text=True, timeout=timeout, shell=False
    )
    if completed.returncode != 0 or not output.is_file():
        raise CapabilityError(
            "preview mix failed",
            [Diagnostic("error", "render.preview_failed", str(output), completed.stderr[-2000:])],
        )


def _check_soundfont_coverage(soundfont: Path, midi: Path, part_id: str, root: Path) -> None:
    from ledgerline.project import load_piece

    piece = load_piece(root)
    part = next(item for item in piece.parts if item.id == part_id)
    profile = piece.profiles[part.profile_id]
    presets = {(item.bank, item.program) for item in read_presets(soundfont)}
    bank = profile.bank_msb * 128 + profile.bank_lsb
    if (bank, profile.program) not in presets:
        raise CapabilityError(
            "SoundFont does not cover the render node instrument",
            [Diagnostic("error", "render.preset_missing", str(midi), part_id)],
        )


def _node_cache_key(node: RenderNode, midi: Path, graph: RenderGraph, automation: Path) -> str:
    payload = {
        "node": node.id,
        "engine": node.engine,
        "plugin_format": node.plugin_format,
        "midi": _hash_file(midi),
        "instrument": _hash_asset(node.instrument),
        "renderer": _hash_asset(node.executable) if node.executable else None,
        "state": _hash_asset(node.state) if node.state else None,
        "automation": _hash_file(automation) if automation.is_file() else None,
        "note_expression": _hash_file(graph.root / "build" / "expression-plan.json")
        if (graph.root / "build" / "expression-plan.json").is_file()
        else None,
        "sample_rate": graph.sample_rate,
        "block_size": graph.block_size,
        "latency_samples": node.latency_samples,
        "tail_seconds": node.tail_seconds,
        "arguments": node.arguments,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _valid_cache(receipt: Path, output: Path, cache_key: str) -> bool:
    if not receipt.is_file() or not output.is_file():
        return False
    try:
        data = json.loads(receipt.read_text(encoding="utf-8"))
        return data["cache_key"] == cache_key and data["output_sha256"] == _hash_file(output)
    except (OSError, KeyError, ValueError, json.JSONDecodeError):
        return False


def _write_receipt(path: Path, node: RenderNode, output: Path, cache_key: str) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "node": node.id,
                "cache_key": cache_key,
                "output_sha256": _hash_file(output),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _resolve_file(root: Path, value: Any, path: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{path} must be a file path")
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve(strict=True)
    if not resolved.is_file():
        raise ValueError(f"{path} must resolve to a file")
    return resolved


def _resolve_asset(root: Path, value: Any, path: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{path} must be an asset path")
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    return candidate.resolve(strict=True)


def _file_identity(path: Path) -> dict[str, Any]:
    return {"path": str(path), "bytes": _asset_size(path), "sha256": _hash_asset(path)}


def _hash_asset(path: Path | None) -> str:
    if path is None:
        return ""
    if path.is_file():
        return _hash_file(path)
    digest = hashlib.sha256()
    for child in sorted(item for item in path.rglob("*") if item.is_file()):
        digest.update(child.relative_to(path).as_posix().encode("utf-8"))
        digest.update(bytes.fromhex(_hash_file(child)))
    return digest.hexdigest()


def _asset_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _bounded_int(value: Any, minimum: int, maximum: int, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{path} must be an integer between {minimum} and {maximum}")
    return value


def _bounded_float(value: Any, minimum: float, maximum: float, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{path} must be numeric")
    number = float(value)
    if not minimum <= number <= maximum:
        raise ValueError(f"{path} must be between {minimum} and {maximum}")
    return number


def _unknown(data: dict, allowed: set[str], path: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ValueError(f"{path} has unknown fields: {', '.join(unknown)}")


def _plugin_parameter_automation(
    graph: RenderGraph,
    node: RenderNode,
    build: Path,
) -> list[dict[str, Any]]:
    from ledgerline.project import load_piece

    piece = load_piece(graph.root)
    part = next(item for item in piece.parts if item.id == node.part)
    profile = piece.profiles[part.profile_id]
    timeline = Timeline(piece, graph.sample_rate)
    events = []
    for control in part.controls:
        if control.kind != "performance":
            continue
        binding = profile.performance[str(control.performance_parameter)]
        if binding.type != "plugin_parameter":
            continue
        position = timeline.anchor(control.measure, control.beat)
        value = binding.minimum + float(control.performance_value) * (
            binding.maximum - binding.minimum
        )
        events.append(
            {
                "parameter": binding.parameter,
                "sample": position.sample,
                "value": value,
                "source": f"performance:{control.performance_parameter}",
            }
        )
    automation_path = build / "automation.json"
    if automation_path.is_file():
        data = json.loads(automation_path.read_text(encoding="utf-8"))
        prefix = f"parts.{node.part}.plugin."
        for lane in data.get("lanes", []):
            target = str(lane.get("target", ""))
            if target.startswith(prefix):
                parameter = target[len(prefix) :]
                for point in lane["points"]:
                    events.append(
                        {
                            "parameter": parameter,
                            "sample": point["sample"],
                            "value": point["value"],
                            "curve": point["curve"],
                            "source": f"automation:{lane['id']}",
                        }
                    )
    return sorted(events, key=lambda item: (item["sample"], item["parameter"]))


def _plugin_note_expression(node: RenderNode, build: Path) -> list[dict[str, Any]]:
    path = build / "expression-plan.json"
    if not path.is_file():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    part = raw.get("parts", {}).get(node.part, {})
    if part.get("backend") not in {"clap-note-expression", "midi2", "mpe"}:
        return []
    return list(part.get("notes", []))
