from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import yaml

from ledgerline.audio import db_to_amplitude, measure_audio, pan_coefficients, resolve_ffmpeg
from ledgerline.diagnostics import CapabilityError, Diagnostic, ValidationError


def mix_project(
    project: str | Path,
    *,
    ffmpeg: str | Path | None = None,
    timeout: int = 180,
) -> dict:
    root = Path(project).resolve()
    mix_path = root / "mix.yaml"
    try:
        data = yaml.safe_load(mix_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, yaml.YAMLError) as exc:
        raise ValidationError(
            "mix.yaml is unavailable or invalid",
            [Diagnostic("error", "mix.invalid_yaml", str(mix_path), str(exc))],
        ) from exc
    if not isinstance(data, dict):
        raise ValidationError(
            "mix.yaml root must be a mapping",
            [Diagnostic("error", "mix.root_type", str(mix_path), "Expected a mapping.")],
        )
    _unknown(data, {"format", "master", "reverb", "tracks"}, "mix.yaml")
    if int(data.get("format", 0)) != 1:
        raise ValidationError(
            "mix format must be 1",
            [Diagnostic("error", "mix.format", str(mix_path), "Expected format: 1.")],
        )
    tracks = data.get("tracks")
    if not isinstance(tracks, dict) or not tracks:
        raise ValidationError(
            "mix tracks must be non-empty",
            [Diagnostic("error", "mix.tracks", str(mix_path), "Expected a track mapping.")],
        )
    stems_dir = root / "build" / "stems"
    inputs: list[Path] = []
    settings: list[dict] = []
    for track_id, raw in tracks.items():
        if not isinstance(raw, dict):
            raise ValidationError(
                "invalid track mix settings",
                [
                    Diagnostic(
                        "error",
                        "mix.track_type",
                        f"mix.yaml:tracks.{track_id}",
                        "Expected a mapping.",
                    )
                ],
            )
        _unknown(raw, {"gain_db", "pan", "reverb_send_db"}, f"mix.yaml:tracks.{track_id}")
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
        settings.append(
            {
                "gain_db": float(raw.get("gain_db", 0.0)),
                "pan": float(raw.get("pan", 0.0)),
                "reverb_send_db": float(raw.get("reverb_send_db", -120.0)),
            }
        )
    master = data.get("master", {})
    reverb = data.get("reverb", {})
    if not isinstance(master, dict) or not isinstance(reverb, dict):
        raise ValidationError("master and reverb must be mappings")
    _unknown(
        master,
        {
            "gain_db",
            "target_lufs",
            "true_peak_ceiling_db",
            "loudness_range_lu",
            "loudness_tolerance_lu",
        },
        "mix.yaml:master",
    )
    _unknown(reverb, {"delays_ms", "decays"}, "mix.yaml:reverb")

    command = [str(resolve_ffmpeg(ffmpeg)), "-hide_banner", "-y"]
    for input_path in inputs:
        command.extend(["-i", str(input_path)])
    graph, output_label = _filter_graph(settings, master, reverb)
    premaster = root / "build" / "premaster.wav"
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
            str(premaster),
        ]
    )
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
        shell=False,
        cwd=str(root),
    )
    if completed.returncode != 0 or not premaster.is_file() or premaster.stat().st_size <= 44:
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
    ffmpeg_path = Path(command[0])
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
        str(output),
    ]
    mastered = subprocess.run(
        master_command,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
        shell=False,
        cwd=str(root),
    )
    if mastered.returncode != 0 or not output.is_file() or output.stat().st_size <= 44:
        raise CapabilityError(
            "FFmpeg mastering failed",
            [Diagnostic("error", "mix.master_failed", str(output), mastered.stderr[-3000:])],
        )
    final_measurement = measure_audio(
        output,
        ffmpeg=ffmpeg_path,
        timeout=timeout,
        target_lufs=target_lufs,
        true_peak_dbtp=ceiling_db,
        loudness_range_lu=loudness_range,
    )
    tolerance = float(master.get("loudness_tolerance_lu", 0.5))
    actual_lufs = final_measurement["integrated_lufs"]
    actual_peak = final_measurement["true_peak_dbtp"]
    if actual_lufs is None or abs(actual_lufs - target_lufs) > tolerance:
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
    report = {
        "schema_version": "1",
        "status": "ok",
        "output": str(output),
        "premaster": str(premaster),
        "premaster_measurement": measurement,
        "final_measurement": final_measurement,
        "bytes": output.stat().st_size,
        "tracks": [path.stem for path in inputs],
        "ffmpeg": command[0],
    }
    (root / "build" / "mix-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def _filter_graph(settings: list[dict], master: dict, reverb: dict) -> tuple[str, str]:
    filters: list[str] = []
    dry_labels: list[str] = []
    send_labels: list[str] = []
    for index, setting in enumerate(settings):
        left, right = pan_coefficients(setting["pan"])
        filters.append(
            f"[{index}:a]volume={setting['gain_db']:.4f}dB,"
            f"pan=stereo|c0={left:.8f}*c0|c1={right:.8f}*c1,asplit=2"
            f"[dry{index}][sendraw{index}]"
        )
        filters.append(f"[sendraw{index}]volume={setting['reverb_send_db']:.4f}dB[send{index}]")
        dry_labels.append(f"[dry{index}]")
        send_labels.append(f"[send{index}]")
    filters.append(f"{''.join(dry_labels)}amix=inputs={len(dry_labels)}:normalize=0[drybus]")
    filters.append(f"{''.join(send_labels)}amix=inputs={len(send_labels)}:normalize=0[sendbus]")
    delays = str(reverb.get("delays_ms", "40|55"))
    decays = str(reverb.get("decays", "0.30|0.20"))
    filters.append(f"[sendbus]aecho=0.8:0.7:{delays}:{decays}[wetbus]")
    filters.append("[drybus][wetbus]amix=inputs=2:normalize=0[premaster]")
    master_gain = float(master.get("gain_db", 0.0))
    filters.append(f"[premaster]volume={master_gain:.4f}dB[mixout]")
    return ";".join(filters), "[mixout]"


def _unknown(data: dict[str, Any], allowed: set[str], path: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ValidationError(
            f"{path} has unknown fields",
            [Diagnostic("error", "mix.unknown_field", path, ", ".join(unknown))],
        )
