from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ledgerline.analysis import inspect_project
from ledgerline.assets import audit_assets, bundle_project
from ledgerline.audio import measure_audio
from ledgerline.automation import compile_automation, load_automation
from ledgerline.comparison import compare_audio
from ledgerline.compiler import compile_project
from ledgerline.diagnostics import LedgerLineError, ValidationError
from ledgerline.environment import doctor
from ledgerline.freeze import freeze_part
from ledgerline.mixer import mix_project
from ledgerline.plugin_host import scan_plugin
from ledgerline.project import load_piece
from ledgerline.provenance import lock_project_environment
from ledgerline.render import render_project
from ledgerline.review import compile_review_annotations
from ledgerline.sample_import import convert_sample_library, inspect_sample_library
from ledgerline.setup_apply import apply_setup_plan
from ledgerline.setup_plan import create_setup_plan, persist_setup_plan
from ledgerline.time_analysis import analyze_project_timeline
from ledgerline.timeline import Timeline
from ledgerline.versions import apply_edit_plan, diff_projects, snapshot_project


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ledgerline")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor_parser = subparsers.add_parser("doctor", help="Inspect local music capabilities")
    doctor_parser.add_argument("--json", action="store_true", dest="as_json")

    validate_parser = subparsers.add_parser("validate", help="Validate authored project files")
    validate_parser.add_argument("project", type=Path)
    validate_parser.add_argument("--json", action="store_true", dest="as_json")

    compile_parser = subparsers.add_parser("compile", help="Compile to MusicXML and MIDI")
    compile_parser.add_argument("project", type=Path)
    compile_parser.add_argument("--output", type=Path)
    compile_parser.add_argument("--json", action="store_true", dest="as_json")

    inspect_parser = subparsers.add_parser(
        "inspect", help="Report harmony, registers, and density without rewriting music"
    )
    inspect_parser.add_argument("project", type=Path)
    inspect_parser.add_argument("--json", action="store_true", dest="as_json")

    render_parser = subparsers.add_parser("render", help="Render compiled MIDI through FluidSynth")
    render_parser.add_argument("project", type=Path)
    render_parser.add_argument("--fluidsynth", type=Path)
    render_parser.add_argument("--soundfont", type=Path)
    render_parser.add_argument("--sample-rate", type=int, default=48000)
    render_parser.add_argument("--ffmpeg", type=Path)
    render_parser.add_argument("--timeout", type=int, default=180)
    render_parser.add_argument("--json", action="store_true", dest="as_json")

    mix_parser = subparsers.add_parser("mix", help="Apply authored mix.yaml to rendered stems")
    mix_parser.add_argument("project", type=Path)
    mix_parser.add_argument("--ffmpeg", type=Path)
    mix_parser.add_argument("--timeout", type=int, default=180)
    mix_parser.add_argument("--json", action="store_true", dest="as_json")

    meter_parser = subparsers.add_parser("meter", help="Measure objective WAV properties")
    meter_parser.add_argument("audio", type=Path)
    meter_parser.add_argument("--ffmpeg", type=Path)
    meter_parser.add_argument("--timeout", type=int, default=180)
    meter_parser.add_argument("--json", action="store_true", dest="as_json")

    duration_parser = subparsers.add_parser(
        "duration", help="Predict playback duration from meter and tempo map"
    )
    duration_parser.add_argument("project", type=Path)
    duration_parser.add_argument("--sample-rate", type=int, default=48000)
    duration_parser.add_argument("--tail-seconds", type=float, default=0.0)
    duration_parser.add_argument("--json", action="store_true", dest="as_json")

    automation_parser = subparsers.add_parser(
        "automation", help="Compile authored automation to sample-accurate positions"
    )
    automation_parser.add_argument("project", type=Path)
    automation_parser.add_argument("--sample-rate", type=int, default=48000)
    automation_parser.add_argument("--output", type=Path)
    automation_parser.add_argument("--json", action="store_true", dest="as_json")

    timeline_parser = subparsers.add_parser(
        "analyze-timeline",
        help="Find time-local loudness, brightness, transient, and activity issues",
    )
    timeline_parser.add_argument("project", type=Path)
    timeline_parser.add_argument("--audio", type=Path)
    timeline_parser.add_argument("--ffmpeg", type=Path)
    timeline_parser.add_argument("--window-seconds", type=float, default=0.5)
    timeline_parser.add_argument("--silence-threshold-db", type=float, default=-60.0)
    timeline_parser.add_argument("--timeout", type=int, default=180)
    timeline_parser.add_argument("--json", action="store_true", dest="as_json")

    compare_parser = subparsers.add_parser(
        "compare", help="Create a loudness-matched, time-local A/B audio report"
    )
    compare_parser.add_argument("before", type=Path)
    compare_parser.add_argument("after", type=Path)
    compare_parser.add_argument("--output", type=Path)
    compare_parser.add_argument("--ffmpeg", type=Path)
    compare_parser.add_argument("--window-seconds", type=float, default=0.5)
    compare_parser.add_argument("--no-loudness-match", action="store_true")
    compare_parser.add_argument("--timeout", type=int, default=180)
    compare_parser.add_argument("--json", action="store_true", dest="as_json")

    assets_parser = subparsers.add_parser("assets", help="Audit asset hashes and license lineage")
    assets_parser.add_argument("project", type=Path)
    assets_parser.add_argument("--json", action="store_true", dest="as_json")

    bundle_parser = subparsers.add_parser(
        "bundle", help="Create a deterministic, license-aware .llproject bundle"
    )
    bundle_parser.add_argument("project", type=Path)
    bundle_parser.add_argument("--output", type=Path)
    bundle_parser.add_argument("--no-build", action="store_true")
    bundle_parser.add_argument("--json", action="store_true", dest="as_json")

    samples_parser = subparsers.add_parser(
        "samples", help="Inspect or convert SFZ, EXS24, Ableton, and Kontakt library metadata"
    )
    samples_subparsers = samples_parser.add_subparsers(dest="samples_command", required=True)
    samples_inspect = samples_subparsers.add_parser("inspect", help="Audit zones and samples")
    samples_inspect.add_argument("source", type=Path)
    samples_inspect.add_argument("--json", action="store_true", dest="as_json")
    samples_convert = samples_subparsers.add_parser(
        "convert", help="Convert recoverable zones to SFZ"
    )
    samples_convert.add_argument("source", type=Path)
    samples_convert.add_argument("output", type=Path)
    samples_convert.add_argument("--json", action="store_true", dest="as_json")

    snapshot_parser = subparsers.add_parser("snapshot", help="Preserve authored project state")
    snapshot_parser.add_argument("project", type=Path)
    snapshot_parser.add_argument("name")
    snapshot_parser.add_argument("--json", action="store_true", dest="as_json")

    edit_parser = subparsers.add_parser(
        "apply-edits", help="Apply a scoped edit plan into a new project directory"
    )
    edit_parser.add_argument("project", type=Path)
    edit_parser.add_argument("plan", type=Path)
    edit_parser.add_argument("--output", type=Path, required=True)
    edit_parser.add_argument("--json", action="store_true", dest="as_json")

    diff_parser = subparsers.add_parser("diff", help="Compare authored project files")
    diff_parser.add_argument("before", type=Path)
    diff_parser.add_argument("after", type=Path)
    diff_parser.add_argument("--json", action="store_true", dest="as_json")

    review_parser = subparsers.add_parser(
        "review", help="Compile measure-anchored listening notes to exact time and samples"
    )
    review_parser.add_argument("project", type=Path)
    review_parser.add_argument("--sample-rate", type=int, default=48_000)
    review_parser.add_argument("--json", action="store_true", dest="as_json")

    lock_parser = subparsers.add_parser(
        "lock", help="Record exact render engines, instruments, assets, and environment"
    )
    lock_parser.add_argument("project", type=Path)
    lock_parser.add_argument("--json", action="store_true", dest="as_json")

    freeze_parser = subparsers.add_parser(
        "freeze", help="Preserve a rendered part as a hash-pinned frozen render node"
    )
    freeze_parser.add_argument("project", type=Path)
    freeze_parser.add_argument("part")
    freeze_parser.add_argument("--source", type=Path)
    freeze_parser.add_argument("--json", action="store_true", dest="as_json")

    plugin_parser = subparsers.add_parser(
        "plugin-scan", help="Scan an external VST3/CLAP through the LedgerLine host protocol"
    )
    plugin_parser.add_argument("host", type=Path)
    plugin_parser.add_argument("plugin", type=Path)
    plugin_parser.add_argument("--format", choices=("vst3", "clap"), required=True)
    plugin_parser.add_argument("--host-argument", action="append", default=[])
    plugin_parser.add_argument("--output", type=Path)
    plugin_parser.add_argument("--timeout", type=int, default=60)
    plugin_parser.add_argument("--json", action="store_true", dest="as_json")

    setup_parser = subparsers.add_parser("setup", help="Consent-based environment setup")
    setup_subparsers = setup_parser.add_subparsers(dest="setup_command", required=True)
    setup_plan = setup_subparsers.add_parser(
        "plan", help="Describe downloads without applying them"
    )
    setup_plan.add_argument("--packs", default="starter")
    setup_plan.add_argument("--catalog", type=Path)
    setup_plan.add_argument("--output", type=Path)
    setup_plan.add_argument("--json", action="store_true", dest="as_json")
    setup_apply = setup_subparsers.add_parser(
        "apply", help="Apply an unexpired setup plan after explicit consent"
    )
    setup_apply.add_argument("--plan", type=Path, required=True)
    setup_apply.add_argument("--consent", required=True)
    setup_apply.add_argument("--catalog", type=Path)
    setup_apply.add_argument("--json", action="store_true", dest="as_json")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "doctor":
            return _emit(doctor(), args.as_json)
        if args.command == "validate":
            piece = load_piece(args.project)
            return _emit(
                {
                    "status": "ok",
                    "project": str(piece.root),
                    "title": piece.title,
                    "measures": piece.measures,
                    "parts": [part.id for part in piece.parts],
                },
                args.as_json,
            )
        if args.command == "compile":
            return _emit(compile_project(args.project, args.output), args.as_json)
        if args.command == "inspect":
            return _emit(inspect_project(args.project), args.as_json)
        if args.command == "render":
            return _emit(
                render_project(
                    args.project,
                    fluidsynth=args.fluidsynth,
                    soundfont=args.soundfont,
                    sample_rate=args.sample_rate,
                    ffmpeg=args.ffmpeg,
                    timeout=args.timeout,
                ),
                args.as_json,
            )
        if args.command == "mix":
            return _emit(
                mix_project(args.project, ffmpeg=args.ffmpeg, timeout=args.timeout), args.as_json
            )
        if args.command == "meter":
            return _emit(
                measure_audio(args.audio, ffmpeg=args.ffmpeg, timeout=args.timeout), args.as_json
            )
        if args.command == "setup" and args.setup_command == "plan":
            pack_ids = [item.strip() for item in args.packs.split(",") if item.strip()]
            report = create_setup_plan(pack_ids, args.catalog)
            plan_path = persist_setup_plan(report, args.output)
            return _emit({**report, "plan_path": str(plan_path)}, args.as_json)
        if args.command == "setup" and args.setup_command == "apply":
            return _emit(
                apply_setup_plan(
                    args.plan,
                    args.consent,
                    catalog_path=args.catalog,
                ),
                args.as_json,
            )
        if args.command == "duration":
            piece = load_piece(args.project)
            return _emit(
                Timeline(piece, args.sample_rate).report(tail_seconds=args.tail_seconds),
                args.as_json,
            )
        if args.command == "automation":
            piece = load_piece(args.project)
            output = args.output or piece.root / "build" / "automation.json"
            return _emit(
                compile_automation(
                    piece,
                    load_automation(piece.root, piece),
                    output,
                    sample_rate=args.sample_rate,
                ),
                args.as_json,
            )
        if args.command == "analyze-timeline":
            return _emit(
                analyze_project_timeline(
                    args.project,
                    audio=args.audio,
                    ffmpeg=args.ffmpeg,
                    window_seconds=args.window_seconds,
                    silence_threshold_db=args.silence_threshold_db,
                    timeout=args.timeout,
                ),
                args.as_json,
            )
        if args.command == "compare":
            return _emit(
                compare_audio(
                    args.before,
                    args.after,
                    output=args.output,
                    ffmpeg=args.ffmpeg,
                    window_seconds=args.window_seconds,
                    loudness_match=not args.no_loudness_match,
                    timeout=args.timeout,
                ),
                args.as_json,
            )
        if args.command == "assets":
            return _emit(audit_assets(args.project), args.as_json)
        if args.command == "bundle":
            return _emit(
                bundle_project(
                    args.project,
                    args.output,
                    include_build=not args.no_build,
                ),
                args.as_json,
            )
        if args.command == "samples" and args.samples_command == "inspect":
            return _emit(inspect_sample_library(args.source), args.as_json)
        if args.command == "samples" and args.samples_command == "convert":
            return _emit(convert_sample_library(args.source, args.output), args.as_json)
        if args.command == "snapshot":
            return _emit(snapshot_project(args.project, args.name), args.as_json)
        if args.command == "apply-edits":
            return _emit(
                apply_edit_plan(args.project, args.plan, args.output),
                args.as_json,
            )
        if args.command == "diff":
            return _emit(diff_projects(args.before, args.after), args.as_json)
        if args.command == "review":
            return _emit(
                compile_review_annotations(args.project, sample_rate=args.sample_rate),
                args.as_json,
            )
        if args.command == "lock":
            return _emit(lock_project_environment(args.project), args.as_json)
        if args.command == "freeze":
            return _emit(freeze_part(args.project, args.part, args.source), args.as_json)
        if args.command == "plugin-scan":
            return _emit(
                scan_plugin(
                    args.host,
                    args.plugin,
                    args.format,
                    arguments=tuple(args.host_argument),
                    output=args.output,
                    timeout=args.timeout,
                ),
                args.as_json,
            )
    except LedgerLineError as exc:
        payload = {
            "status": "error",
            "message": str(exc),
            "diagnostics": [item.to_dict() for item in exc.diagnostics],
        }
        _emit(payload, True, stream=sys.stderr)
        return 2 if isinstance(exc, ValidationError) else 3
    except (OSError, ValueError) as exc:
        _emit({"status": "error", "message": str(exc)}, True, stream=sys.stderr)
        return 4
    return 1


def _emit(payload: dict, as_json: bool, stream=sys.stdout) -> int:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=stream)
    elif payload.get("status") == "ok":
        for key, value in payload.items():
            print(f"{key}: {value}", file=stream)
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=stream)
    return 0
