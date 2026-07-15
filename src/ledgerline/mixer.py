from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from ledgerline.audio import db_to_amplitude, measure_audio, pan_coefficients, resolve_ffmpeg
from ledgerline.automation import compile_automation, load_automation
from ledgerline.diagnostics import CapabilityError, Diagnostic
from ledgerline.external_process import run_external
from ledgerline.mix_config import MixConfig, MixNode, load_mix_config, processor_filters
from ledgerline.project import load_piece


def mix_project(
    project: str | Path,
    *,
    ffmpeg: str | Path | None = None,
    timeout: int = 180,
    cancel_event: threading.Event | None = None,
) -> dict:
    from ledgerline.build_state import authored_revision, file_identity, record_mix

    root = Path(project).resolve()
    config = load_mix_config(root)
    master = config.master
    automation = _load_mix_automation(root)
    inputs: list[Path] = []
    stems_dir = root / "build" / "stems"
    for track_id in config.tracks:
        stem = stems_dir / f"{track_id}.wav"
        if not stem.is_file():
            raise CapabilityError(
                "required stem is missing",
                [
                    Diagnostic(
                        "error", "mix.stem_missing", str(stem), "Run ledgerline render first."
                    )
                ],
            )
        inputs.append(stem)

    ffmpeg_path = resolve_ffmpeg(ffmpeg)
    command = [str(ffmpeg_path), "-hide_banner", "-y"]
    for input_path in inputs:
        command.extend(["-i", str(input_path)])
    graph, output_label = _filter_graph(config, automation)
    premaster = root / "build" / "premaster.wav"
    premaster_temporary = premaster.with_name(f".{premaster.stem}.rendering{premaster.suffix}")
    premaster_temporary.unlink(missing_ok=True)
    command.extend(
        [
            "-filter_complex",
            graph,
            "-map",
            output_label,
            "-c:a",
            "pcm_s24le",
            "-ar",
            "48000",
            str(premaster_temporary),
        ]
    )
    try:
        completed = run_external(
            command,
            timeout=timeout,
            cancel_event=cancel_event,
            cwd=root,
        )
    except BaseException:
        premaster_temporary.unlink(missing_ok=True)
        raise
    if (
        completed.returncode != 0
        or not premaster_temporary.is_file()
        or premaster_temporary.stat().st_size <= 44
    ):
        premaster_temporary.unlink(missing_ok=True)
        raise CapabilityError(
            "FFmpeg premaster mix failed",
            [
                Diagnostic(
                    "error",
                    "mix.external_failed",
                    str(premaster),
                    completed.stderr[-3000:],
                )
            ],
        )
    os.replace(premaster_temporary, premaster)
    target_lufs = float(master.get("target_lufs", -16.0))
    ceiling_db = float(master.get("true_peak_ceiling_db", -1.0))
    loudness_range = float(master.get("loudness_range_lu", 11.0))
    measurement = measure_audio(
        premaster,
        ffmpeg=ffmpeg_path,
        timeout=timeout,
        target_lufs=target_lufs,
        true_peak_dbtp=ceiling_db,
        loudness_range_lu=loudness_range,
        cancel_event=cancel_event,
    )
    required = {
        "integrated_lufs": measurement["integrated_lufs"],
        "true_peak_dbtp": measurement["true_peak_dbtp"],
        "loudness_range_lu": measurement["loudness_range_lu"],
        "threshold_lufs": measurement["threshold_lufs"],
        "target_offset_lu": measurement["target_offset_lu"],
    }
    if any(value is None for value in required.values()):
        raise CapabilityError(
            "premaster loudness values are incomplete",
            [
                Diagnostic(
                    "error", "mix.measurement_incomplete", str(premaster), json.dumps(required)
                )
            ],
        )
    output = root / "build" / "mix.wav"
    output_temporary = output.with_name(f".{output.stem}.rendering{output.suffix}")
    output_temporary.unlink(missing_ok=True)
    limit = db_to_amplitude(ceiling_db)
    master_filter = (
        f"loudnorm=I={target_lufs}:TP={ceiling_db}:LRA={loudness_range}:"
        f"measured_I={required['integrated_lufs']}:measured_TP={required['true_peak_dbtp']}:"
        f"measured_LRA={required['loudness_range_lu']}:"
        f"measured_thresh={required['threshold_lufs']}:offset={required['target_offset_lu']}:"
        f"linear=true:print_format=json,aresample=48000,"
        f"alimiter=limit={limit:.8f}:attack=5:release=50:level=false"
    )
    master_command = [
        str(ffmpeg_path),
        "-hide_banner",
        "-y",
        "-i",
        str(premaster),
        "-af",
        master_filter,
        "-c:a",
        "pcm_s24le",
        "-ar",
        "48000",
        str(output_temporary),
    ]
    try:
        mastered = run_external(
            master_command,
            timeout=timeout,
            cancel_event=cancel_event,
            cwd=root,
        )
    except BaseException:
        output_temporary.unlink(missing_ok=True)
        raise
    if (
        mastered.returncode != 0
        or not output_temporary.is_file()
        or output_temporary.stat().st_size <= 44
    ):
        output_temporary.unlink(missing_ok=True)
        raise CapabilityError(
            "FFmpeg mastering failed",
            [Diagnostic("error", "mix.master_failed", str(output), mastered.stderr[-3000:])],
        )
    try:
        final_measurement = measure_audio(
            output_temporary,
            ffmpeg=ffmpeg_path,
            timeout=timeout,
            target_lufs=target_lufs,
            true_peak_dbtp=ceiling_db,
            loudness_range_lu=loudness_range,
            cancel_event=cancel_event,
        )
    except BaseException:
        output_temporary.unlink(missing_ok=True)
        raise
    tolerance = float(master.get("loudness_tolerance_lu", 0.5))
    actual_lufs = final_measurement["integrated_lufs"]
    actual_peak = final_measurement["true_peak_dbtp"]
    if actual_lufs is None or abs(actual_lufs - target_lufs) > tolerance:
        output_temporary.unlink(missing_ok=True)
        raise CapabilityError(
            "master loudness is outside the authored tolerance",
            [
                Diagnostic(
                    "error",
                    "mix.loudness_out_of_tolerance",
                    str(output),
                    json.dumps(
                        {
                            "target_lufs": target_lufs,
                            "actual_lufs": actual_lufs,
                            "tolerance": tolerance,
                        }
                    ),
                )
            ],
        )
    if actual_peak is None or actual_peak > ceiling_db + 0.1:
        output_temporary.unlink(missing_ok=True)
        raise CapabilityError(
            "master true peak exceeds the authored ceiling",
            [
                Diagnostic(
                    "error",
                    "mix.true_peak_exceeded",
                    str(output),
                    json.dumps({"ceiling_dbtp": ceiling_db, "actual_dbtp": actual_peak}),
                )
            ],
        )
    os.replace(output_temporary, output)
    report = {
        "schema_version": "2",
        "status": "ok",
        "source_revision": authored_revision(root),
        "output": str(output),
        "output_identity": file_identity(output),
        "premaster": str(premaster),
        "premaster_measurement": measurement,
        "final_measurement": final_measurement,
        "bytes": output.stat().st_size,
        "tracks": [path.stem for path in inputs],
        "inputs": [file_identity(path) for path in inputs],
        "buses": list(config.buses),
        "automation_lanes": len(automation),
        "ffmpeg": command[0],
    }
    (root / "build" / "mix-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    record_mix(root, report)
    return report


def _filter_graph(config: MixConfig, automation: tuple[dict, ...]) -> tuple[str, str]:
    if config.format == 1:
        return _legacy_filter_graph(config)
    filters: list[str] = []
    destinations: dict[str, list[str]] = {"master": []}
    destinations.update({bus_id: [] for bus_id in config.buses})
    for index, (track_id, node) in enumerate(config.tracks.items()):
        label = _process_node(
            filters,
            f"[{index}:a]",
            f"track_{index}",
            node,
            _gain_lane(automation, f"parts.{track_id}.gain_db"),
        )
        _route(filters, label, f"track_{index}", node, destinations)
    for bus_id in _bus_order(config.buses):
        incoming = destinations[bus_id]
        if not incoming:
            continue
        label = _sum(filters, incoming, f"bus_{bus_id}_input")
        node = config.buses[bus_id]
        label = _process_node(
            filters,
            label,
            f"bus_{bus_id}",
            node,
            _gain_lane(automation, f"buses.{bus_id}.gain_db"),
        )
        _route(filters, label, f"bus_{bus_id}", node, destinations)
    if not destinations["master"]:
        raise CapabilityError("mix graph has no signal routed to master")
    label = _sum(filters, destinations["master"], "master_input")
    for index, processor in enumerate(config.master["inserts"]):
        next_label = f"master_insert_{index}"
        chain = ",".join(processor_filters(processor)) or "anull"
        filters.append(f"{label}{chain}[{next_label}]")
        label = f"[{next_label}]"
    gain_filter = _gain_filter(
        float(config.master["gain_db"]),
        _gain_lane(automation, "master.gain_db"),
    )
    filters.append(f"{label}{gain_filter}[mixout]")
    return ";".join(filters), "[mixout]"


def _legacy_filter_graph(config: MixConfig) -> tuple[str, str]:
    filters: list[str] = []
    dry_labels: list[str] = []
    send_labels: list[str] = []
    for index, setting in enumerate(config.tracks.values()):
        left, right = pan_coefficients(setting.pan)
        filters.append(
            f"[{index}:a]volume={setting.gain_db:.4f}dB,"
            f"pan=stereo|c0={left:.8f}*c0|c1={right:.8f}*c1,asplit=2"
            f"[dry{index}][sendraw{index}]"
        )
        send_db = setting.sends["__legacy_reverb"]
        filters.append(f"[sendraw{index}]volume={send_db:.4f}dB[send{index}]")
        dry_labels.append(f"[dry{index}]")
        send_labels.append(f"[send{index}]")
    filters.append(f"{''.join(dry_labels)}amix=inputs={len(dry_labels)}:normalize=0[drybus]")
    filters.append(f"{''.join(send_labels)}amix=inputs={len(send_labels)}:normalize=0[sendbus]")
    delays = config.legacy_reverb["delays_ms"]
    decays = config.legacy_reverb["decays"]
    filters.append(f"[sendbus]aecho=0.8:0.7:{delays}:{decays}[wetbus]")
    filters.append("[drybus][wetbus]amix=inputs=2:normalize=0[premaster]")
    master_gain = float(config.master["gain_db"])
    filters.append(f"[premaster]volume={master_gain:.4f}dB[mixout]")
    return ";".join(filters), "[mixout]"


def _process_node(
    filters: list[str],
    input_label: str,
    prefix: str,
    node: MixNode,
    lane: dict | None,
) -> str:
    left, right = pan_coefficients(node.pan)
    chain = [_gain_filter(node.gain_db, lane)]
    chain.extend(
        processor_filter for item in node.inserts for processor_filter in processor_filters(item)
    )
    chain.append(f"pan=stereo|c0={left:.8f}*c0|c1={right:.8f}*c1")
    output = f"{prefix}_processed"
    filters.append(f"{input_label}{','.join(chain)}[{output}]")
    return f"[{output}]"


def _route(
    filters: list[str],
    label: str,
    prefix: str,
    node: MixNode,
    destinations: dict[str, list[str]],
) -> None:
    routes = [(node.output, 0.0), *node.sends.items()]
    if len(routes) == 1:
        destinations[routes[0][0]].append(label)
        return
    labels = [f"{prefix}_route_{index}" for index in range(len(routes))]
    filters.append(f"{label}asplit={len(routes)}{''.join(f'[{item}]' for item in labels)}")
    for index, ((target, gain_db), split_label) in enumerate(zip(routes, labels, strict=True)):
        if gain_db == 0.0:
            destinations[target].append(f"[{split_label}]")
        else:
            send_label = f"{prefix}_send_{index}"
            filters.append(f"[{split_label}]volume={gain_db:.6f}dB[{send_label}]")
            destinations[target].append(f"[{send_label}]")


def _sum(filters: list[str], labels: list[str], output: str) -> str:
    if len(labels) == 1:
        filters.append(f"{labels[0]}anull[{output}]")
    else:
        filters.append(f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0[{output}]")
    return f"[{output}]"


def _bus_order(buses: dict[str, MixNode]) -> list[str]:
    indegree = {bus_id: 0 for bus_id in buses}
    edges: dict[str, set[str]] = {bus_id: set() for bus_id in buses}
    for bus_id, node in buses.items():
        for target in {node.output, *node.sends}:
            if target in buses and target not in edges[bus_id]:
                edges[bus_id].add(target)
                indegree[target] += 1
    ready = sorted(bus_id for bus_id, degree in indegree.items() if degree == 0)
    result: list[str] = []
    while ready:
        bus_id = ready.pop(0)
        result.append(bus_id)
        for target in sorted(edges[bus_id]):
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
                ready.sort()
    return result


def _load_mix_automation(root: Path) -> tuple[dict, ...]:
    if not (root / "automation.yaml").is_file():
        return ()
    piece = load_piece(root)
    report = compile_automation(
        piece,
        load_automation(root, piece),
        root / "build" / "automation.json",
    )
    allowed_prefixes = ("parts.", "buses.", "master.")
    return tuple(
        lane for lane in report["lanes"] if str(lane["target"]).startswith(allowed_prefixes)
    )


def _gain_lane(lanes: tuple[dict, ...], target: str) -> dict | None:
    return next((lane for lane in lanes if lane["target"] == target), None)


def _gain_filter(base_db: float, lane: dict | None) -> str:
    if lane is None:
        return f"volume={base_db:.6f}dB"
    if lane["unit"] != "db":
        raise CapabilityError(
            "gain automation must use dB",
            [Diagnostic("error", "mix.automation_unit", lane["target"], lane["unit"])],
        )
    expression = _automation_expression(lane)
    return f"volume='pow(10,({base_db:.9f}+({expression}))/20)':eval=frame"


def _automation_expression(lane: dict) -> str:
    points = lane["points"]
    result = f"{float(points[-1]['value']):.9f}"
    for start, end in reversed(list(zip(points, points[1:], strict=False))):
        start_time = float(start["seconds"])
        end_time = float(end["seconds"])
        start_value = float(start["value"])
        end_value = float(end["value"])
        curve = start.get("curve") or lane["interpolation"]
        position = f"clip((t-{start_time:.9f})/{end_time - start_time:.9f},0,1)"
        if curve == "step":
            segment = f"{start_value:.9f}"
        elif curve == "smooth":
            smooth = f"(({position})*({position})*(3-2*({position})))"
            segment = f"lerp({start_value:.9f},{end_value:.9f},{smooth})"
        elif curve == "exponential":
            segment = f"{start_value:.9f}*pow({end_value / start_value:.9f},{position})"
        elif curve == "bezier":
            first = start.get("out_value")
            second = end.get("in_value")
            first = start_value if first is None else float(first)
            second = end_value if second is None else float(second)
            inverse = f"(1-({position}))"
            segment = (
                f"pow({inverse},3)*{start_value:.9f}"
                f"+3*pow({inverse},2)*({position})*{first:.9f}"
                f"+3*({inverse})*pow(({position}),2)*{second:.9f}"
                f"+pow(({position}),3)*{end_value:.9f}"
            )
        else:
            segment = f"lerp({start_value:.9f},{end_value:.9f},{position})"
        result = f"if(lt(t,{end_time:.9f}),{segment},{result})"
    first = points[0]
    return f"if(lt(t,{float(first['seconds']):.9f}),{float(first['value']):.9f},{result})"
