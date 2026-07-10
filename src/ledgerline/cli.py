from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ledgerline.analysis import inspect_project
from ledgerline.audio import measure_audio
from ledgerline.compiler import compile_project
from ledgerline.diagnostics import LedgerLineError, ValidationError
from ledgerline.environment import doctor
from ledgerline.mixer import mix_project
from ledgerline.project import load_piece
from ledgerline.render import render_project
from ledgerline.setup_apply import apply_setup_plan
from ledgerline.setup_plan import create_setup_plan, persist_setup_plan


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
