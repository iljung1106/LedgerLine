from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ledgerline.analysis import inspect_project
from ledgerline.assets import audit_assets, bundle_project
from ledgerline.audio import measure_audio
from ledgerline.audio_regression import check_audio_baseline, record_audio_baseline
from ledgerline.automation import compile_automation, load_automation
from ledgerline.comparison import compare_audio
from ledgerline.compiler import compile_project
from ledgerline.delegation import (
    apply_delegation,
    create_delegation,
    list_delegations,
    next_delegation,
    propose_delegation,
    reject_delegation,
    show_delegation,
)
from ledgerline.diagnostics import LedgerLineError, ValidationError
from ledgerline.environment import doctor
from ledgerline.expression_plan import write_expression_plan
from ledgerline.freeze import freeze_part
from ledgerline.instrument_profile import (
    analyze_instrument_probe,
    approve_instrument_profile,
    create_instrument_probe,
    draft_instrument_profile,
    probe_reference_instrument,
    seal_instrument_profile,
)
from ledgerline.mixer import mix_project
from ledgerline.performance_templates import (
    apply_performance_template,
    list_performance_templates,
    show_performance_template,
)
from ledgerline.plugin_host import scan_plugin, scan_reference_plugin
from ledgerline.project import load_piece
from ledgerline.project_init import initialize_project, list_project_templates
from ledgerline.provenance import lock_project_environment
from ledgerline.reference_host import reference_manifest
from ledgerline.render import render_project
from ledgerline.review import compile_review_annotations
from ledgerline.sample_import import convert_sample_library, inspect_sample_library
from ledgerline.setup_apply import apply_setup_plan
from ledgerline.setup_plan import create_setup_plan, persist_setup_plan
from ledgerline.studio_model import build_studio_model
from ledgerline.studio_server import run_studio
from ledgerline.time_analysis import analyze_project_timeline
from ledgerline.timeline import Timeline
from ledgerline.versions import apply_edit_plan, diff_projects, snapshot_project
from ledgerline.visual_review import create_visual_review


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ledgerline")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Create a strict LedgerLine project")
    init_parser.add_argument("destination", type=Path)
    init_parser.add_argument("--title", required=True)
    init_parser.add_argument(
        "--template",
        choices=("piano-solo", "piano-cello", "string-duo", "chamber-trio"),
        default="piano-solo",
    )
    init_parser.add_argument("--measures", type=int, default=16)
    init_parser.add_argument("--beats", type=int, default=4)
    init_parser.add_argument("--beat-type", type=int, default=4)
    init_parser.add_argument("--bpm", type=float, default=84.0)
    init_parser.add_argument("--fifths", type=int, default=0)
    init_parser.add_argument("--mode", choices=("major", "minor"), default="major")
    init_parser.add_argument("--duration-target")
    init_parser.add_argument("--json", action="store_true", dest="as_json")

    init_templates = subparsers.add_parser("init-templates", help="List built-in project templates")
    init_templates.add_argument("--json", action="store_true", dest="as_json")

    expression_parser = subparsers.add_parser(
        "expression-plan", help="Validate and emit per-note expression transport events"
    )
    expression_parser.add_argument("project", type=Path)
    expression_parser.add_argument("--output", type=Path)
    expression_parser.add_argument("--sample-rate", type=int, default=48_000)
    expression_parser.add_argument("--json", action="store_true", dest="as_json")

    performance_parser = subparsers.add_parser(
        "performance-templates", help="Inspect or apply performance backend templates"
    )
    performance_sub = performance_parser.add_subparsers(dest="performance_command", required=True)
    performance_list = performance_sub.add_parser("list")
    performance_list.add_argument("--json", action="store_true", dest="as_json")
    performance_show = performance_sub.add_parser("show")
    performance_show.add_argument("template")
    performance_show.add_argument("--json", action="store_true", dest="as_json")
    performance_apply = performance_sub.add_parser("apply")
    performance_apply.add_argument("project", type=Path)
    performance_apply.add_argument("part")
    performance_apply.add_argument("template")
    performance_apply.add_argument("--json", action="store_true", dest="as_json")

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

    render_parser = subparsers.add_parser(
        "render", help="Render through authored nodes or an explicit FluidSynth route"
    )
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

    reference_scan = subparsers.add_parser(
        "reference-plugin-scan", help="Scan the bundled deterministic reference instrument"
    )
    reference_scan.add_argument("--format", choices=("vst3", "clap"), default="clap")
    reference_scan.add_argument("--plugin", type=Path)
    reference_scan.add_argument("--output", type=Path)
    reference_scan.add_argument("--timeout", type=int, default=60)
    reference_scan.add_argument("--json", action="store_true", dest="as_json")

    profile_parser = subparsers.add_parser(
        "instrument-profile", help="Draft, approve, and audio-probe instrument profiles"
    )
    profile_sub = profile_parser.add_subparsers(dest="profile_command", required=True)
    profile_draft = profile_sub.add_parser("draft")
    profile_draft.add_argument("source", type=Path)
    profile_draft.add_argument("output", type=Path)
    profile_draft.add_argument("--id", required=True, dest="profile_id")
    profile_draft.add_argument("--name", required=True)
    profile_draft.add_argument("--family", default="other")
    profile_draft.add_argument("--json", action="store_true", dest="as_json")
    profile_approve = profile_sub.add_parser("approve")
    profile_approve.add_argument("draft", type=Path)
    profile_approve.add_argument("output", type=Path)
    profile_approve.add_argument("--token", required=True)
    profile_approve.add_argument("--json", action="store_true", dest="as_json")
    profile_seal = profile_sub.add_parser("seal")
    profile_seal.add_argument("draft", type=Path)
    profile_seal.add_argument("--json", action="store_true", dest="as_json")
    profile_probe = profile_sub.add_parser("probe")
    profile_probe.add_argument("plugin", type=Path)
    profile_probe.add_argument("output", type=Path)
    profile_probe.add_argument("--format", choices=("vst3", "clap"), default="clap")
    profile_probe.add_argument("--low", type=int, default=24)
    profile_probe.add_argument("--high", type=int, default=96)
    profile_probe.add_argument("--step", type=int, default=6)
    profile_probe.add_argument("--sample-rate", type=int, default=24_000)
    profile_probe.add_argument("--json", action="store_true", dest="as_json")
    profile_probe_plan = profile_sub.add_parser("probe-plan")
    profile_probe_plan.add_argument("output", type=Path)
    profile_probe_plan.add_argument("--low", type=int, default=24)
    profile_probe_plan.add_argument("--high", type=int, default=96)
    profile_probe_plan.add_argument("--step", type=int, default=6)
    profile_probe_plan.add_argument("--sample-rate", type=int, default=48_000)
    profile_probe_plan.add_argument("--json", action="store_true", dest="as_json")
    profile_analyze = profile_sub.add_parser("analyze-probe")
    profile_analyze.add_argument("audio", type=Path)
    profile_analyze.add_argument("plan", type=Path)
    profile_analyze.add_argument("output", type=Path)
    profile_analyze.add_argument("--json", action="store_true", dest="as_json")

    regression_parser = subparsers.add_parser(
        "regression", help="Record or check tolerant deterministic audio golden files"
    )
    regression_sub = regression_parser.add_subparsers(dest="regression_command", required=True)
    regression_record = regression_sub.add_parser("record")
    regression_record.add_argument("audio", type=Path)
    regression_record.add_argument("baseline", type=Path)
    regression_record.add_argument("--exact", action="store_true")
    regression_record.add_argument("--json", action="store_true", dest="as_json")
    regression_check = regression_sub.add_parser("check")
    regression_check.add_argument("audio", type=Path)
    regression_check.add_argument("baseline", type=Path)
    regression_check.add_argument("--exact", action="store_true")
    regression_check.add_argument("--json", action="store_true", dest="as_json")

    visual_parser = subparsers.add_parser(
        "visual-review", help="Build a local waveform, spectrogram, score, and marker review page"
    )
    visual_parser.add_argument("project", type=Path)
    visual_parser.add_argument("--audio", type=Path)
    visual_parser.add_argument("--ffmpeg", type=Path)
    visual_parser.add_argument("--musescore", type=Path)
    visual_parser.add_argument("--timeout", type=int, default=180)
    visual_parser.add_argument("--json", action="store_true", dest="as_json")

    studio_parser = subparsers.add_parser(
        "studio", help="Serve the interactive LedgerLine Studio workbench"
    )
    studio_parser.add_argument("project", type=Path)
    studio_parser.add_argument("--host", default="127.0.0.1")
    studio_parser.add_argument("--port", type=int, default=8765)
    studio_parser.add_argument("--no-open", action="store_true")
    studio_parser.add_argument("--ffmpeg", type=Path)

    studio_model_parser = subparsers.add_parser(
        "studio-model", help="Emit the Studio timeline, mix, score, and media model"
    )
    studio_model_parser.add_argument("project", type=Path)
    studio_model_parser.add_argument("--json", action="store_true", dest="as_json")

    delegate_parser = subparsers.add_parser(
        "delegate", help="Create and process Studio AI delegation requests"
    )
    delegate_sub = delegate_parser.add_subparsers(dest="delegate_command", required=True)
    delegate_create = delegate_sub.add_parser("create")
    delegate_create.add_argument("project", type=Path)
    delegate_create.add_argument("goal")
    delegate_create.add_argument("--autonomy", choices=("review", "safe-auto"), default="review")
    delegate_create.add_argument("--context", default="")
    delegate_create.add_argument("--constraint", action="append", default=[])
    delegate_create.add_argument("--json", action="store_true", dest="as_json")
    delegate_list = delegate_sub.add_parser("list")
    delegate_list.add_argument("project", type=Path)
    delegate_list.add_argument("--status")
    delegate_list.add_argument("--json", action="store_true", dest="as_json")
    delegate_next = delegate_sub.add_parser("next")
    delegate_next.add_argument("project", type=Path)
    delegate_next.add_argument("--json", action="store_true", dest="as_json")
    delegate_show = delegate_sub.add_parser("show")
    delegate_show.add_argument("project", type=Path)
    delegate_show.add_argument("id")
    delegate_show.add_argument("--json", action="store_true", dest="as_json")
    delegate_propose = delegate_sub.add_parser("propose")
    delegate_propose.add_argument("project", type=Path)
    delegate_propose.add_argument("id")
    delegate_propose.add_argument("proposal", type=Path)
    delegate_propose.add_argument("--json", action="store_true", dest="as_json")
    delegate_apply = delegate_sub.add_parser("apply")
    delegate_apply.add_argument("project", type=Path)
    delegate_apply.add_argument("id")
    delegate_apply.add_argument("--token")
    delegate_apply.add_argument("--json", action="store_true", dest="as_json")
    delegate_reject = delegate_sub.add_parser("reject")
    delegate_reject.add_argument("project", type=Path)
    delegate_reject.add_argument("id")
    delegate_reject.add_argument("--reason", default="")
    delegate_reject.add_argument("--json", action="store_true", dest="as_json")

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
        if args.command == "init":
            return _emit(
                initialize_project(
                    args.destination,
                    title=args.title,
                    template=args.template,
                    measures=args.measures,
                    beats=args.beats,
                    beat_type=args.beat_type,
                    bpm=args.bpm,
                    fifths=args.fifths,
                    mode=args.mode,
                    duration_target=args.duration_target,
                ),
                args.as_json,
            )
        if args.command == "init-templates":
            return _emit(list_project_templates(), args.as_json)
        if args.command == "expression-plan":
            piece = load_piece(args.project)
            output = args.output or piece.root / "build" / "expression-plan.json"
            return _emit(
                write_expression_plan(piece, output, sample_rate=args.sample_rate), args.as_json
            )
        if args.command == "performance-templates":
            if args.performance_command == "list":
                return _emit(list_performance_templates(), args.as_json)
            if args.performance_command == "show":
                return _emit(show_performance_template(args.template), args.as_json)
            return _emit(
                apply_performance_template(args.project, args.part, args.template), args.as_json
            )
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
        if args.command == "reference-plugin-scan":
            return _emit(
                scan_reference_plugin(
                    args.plugin or reference_manifest(args.format),
                    args.format,
                    output=args.output,
                    timeout=args.timeout,
                ),
                args.as_json,
            )
        if args.command == "instrument-profile":
            if args.profile_command == "draft":
                return _emit(
                    draft_instrument_profile(
                        args.source,
                        args.output,
                        profile_id=args.profile_id,
                        name=args.name,
                        family=args.family,
                    ),
                    args.as_json,
                )
            if args.profile_command == "approve":
                return _emit(
                    approve_instrument_profile(args.draft, args.output, token=args.token),
                    args.as_json,
                )
            if args.profile_command == "seal":
                return _emit(seal_instrument_profile(args.draft), args.as_json)
            if args.profile_command == "probe-plan":
                return _emit(
                    create_instrument_probe(
                        args.output,
                        low=args.low,
                        high=args.high,
                        step=args.step,
                        sample_rate=args.sample_rate,
                    ),
                    args.as_json,
                )
            if args.profile_command == "analyze-probe":
                return _emit(
                    analyze_instrument_probe(args.audio, args.plan, args.output), args.as_json
                )
            return _emit(
                probe_reference_instrument(
                    args.plugin,
                    args.output,
                    plugin_format=args.format,
                    low=args.low,
                    high=args.high,
                    step=args.step,
                    sample_rate=args.sample_rate,
                ),
                args.as_json,
            )
        if args.command == "regression":
            if args.regression_command == "record":
                return _emit(
                    record_audio_baseline(args.audio, args.baseline, exact=args.exact),
                    args.as_json,
                )
            report = check_audio_baseline(args.audio, args.baseline, exact=args.exact)
            _emit(report, args.as_json)
            return 0 if report["pass"] else 5
        if args.command == "visual-review":
            return _emit(
                create_visual_review(
                    args.project,
                    audio=args.audio,
                    ffmpeg=args.ffmpeg,
                    musescore=args.musescore,
                    timeout=args.timeout,
                ),
                args.as_json,
            )
        if args.command == "studio":
            run_studio(
                args.project,
                host=args.host,
                port=args.port,
                open_browser=not args.no_open,
                ffmpeg=args.ffmpeg,
            )
            return 0
        if args.command == "studio-model":
            return _emit(build_studio_model(args.project), args.as_json)
        if args.command == "delegate":
            if args.delegate_command == "create":
                return _emit(
                    create_delegation(
                        args.project,
                        args.goal,
                        autonomy=args.autonomy,
                        context=args.context,
                        constraints=args.constraint,
                    ),
                    args.as_json,
                )
            if args.delegate_command == "list":
                return _emit(list_delegations(args.project, status=args.status), args.as_json)
            if args.delegate_command == "next":
                return _emit(next_delegation(args.project), args.as_json)
            if args.delegate_command == "show":
                return _emit(show_delegation(args.project, args.id), args.as_json)
            if args.delegate_command == "propose":
                return _emit(propose_delegation(args.project, args.id, args.proposal), args.as_json)
            if args.delegate_command == "apply":
                return _emit(
                    apply_delegation(args.project, args.id, token=args.token), args.as_json
                )
            return _emit(
                reject_delegation(args.project, args.id, args.reason), args.as_json
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
