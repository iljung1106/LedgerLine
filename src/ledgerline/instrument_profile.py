from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import mido
import numpy as np
import yaml

from ledgerline.pcm import read_pcm_wav
from ledgerline.reference_host import render_reference_plugin
from ledgerline.sample_import import inspect_sample_library

SEMANTIC_PARAMETERS = {
    "expression": ("expression", "dynamics", "volume", "gain"),
    "attack": ("attack", "onset"),
    "brightness": ("brightness", "bright", "timbre", "tone", "cutoff"),
    "distance": ("distance", "room", "reverb", "mic", "microphone"),
    "vibrato": ("vibrato", "vib depth", "vib"),
    "bow_pressure": ("bow pressure", "pressure"),
}


def draft_instrument_profile(
    source: str | Path,
    output: str | Path,
    *,
    profile_id: str,
    name: str,
    family: str = "other",
) -> dict:
    source_path = Path(source).resolve(strict=True)
    output_path = Path(output).resolve()
    if output_path.exists():
        raise ValueError(f"profile draft already exists: {output_path}")
    evidence: dict[str, Any]
    candidates: list[dict[str, Any]] = []
    keyswitches: list[int] = []
    low, high = 0, 127
    if source_path.suffix.lower() == ".sfz":
        evidence = inspect_sample_library(source_path)
        coverage = evidence["coverage"]
        low = coverage["lowest_key"] if coverage["lowest_key"] is not None else 0
        high = coverage["highest_key"] if coverage["highest_key"] is not None else 127
        for region in evidence["regions"]:
            opcodes = region.get("opcodes", {})
            for field in ("sw_lokey", "sw_hikey", "sw_last"):
                value = opcodes.get(field)
                if value is not None:
                    parsed = _midi_number(value)
                    if parsed not in keyswitches:
                        keyswitches.append(parsed)
    else:
        evidence = json.loads(source_path.read_text(encoding="utf-8"))
        if not isinstance(evidence, dict) or not isinstance(evidence.get("parameters"), list):
            raise ValueError("non-SFZ sources must be LedgerLine plugin scan JSON")
        for parameter in evidence["parameters"]:
            normalized = str(parameter["name"]).casefold()
            ranked = []
            for semantic, aliases in SEMANTIC_PARAMETERS.items():
                score = max((_match_score(normalized, alias) for alias in aliases), default=0.0)
                if score:
                    ranked.append((score, semantic))
            if ranked:
                score, semantic = max(ranked)
                candidates.append(
                    {
                        "semantic": semantic,
                        "binding": {
                            "type": "plugin_parameter",
                            "parameter": str(parameter["id"]),
                            "min": float(parameter.get("minimum", 0.0)),
                            "max": float(parameter.get("maximum", 1.0)),
                            "default": _normalized_default(parameter),
                        },
                        "confidence": round(score, 3),
                        "evidence": parameter["name"],
                    }
                )
    draft = {
        "schema_version": "1",
        "status": "draft",
        "approved": False,
        "source": _identity(source_path),
        "profile": {
            "format": 1,
            "id": profile_id,
            "name": name,
            "family": family,
            "range": {
                "absolute": [_pitch_name(low), _pitch_name(high)],
                "comfortable": [_pitch_name(low), _pitch_name(high)],
            },
            "transposition": 0,
            "midi": {"bank_msb": 0, "bank_lsb": 0, "program": 0},
            "clef": {"sign": "G", "line": 2},
            "articulations": ["staccato", "tenuto", "accent", "marcato"],
            "keyswitches": {
                f"unresolved-{index + 1}": _pitch_name(key) for index, key in enumerate(keyswitches)
            },
            "performance": {},
        },
        "binding_candidates": sorted(candidates, key=lambda item: -item["confidence"]),
        "evidence_summary": {
            "format": evidence.get("format", evidence.get("plugin_format", "plugin-scan")),
            "regions": len(evidence.get("regions", [])),
            "velocity_layers": evidence.get("coverage", {}).get("velocity_layers"),
            "round_robin_groups": evidence.get("round_robin_groups", []),
            "missing_samples": evidence.get("missing_samples"),
        },
        "review_required": [
            "confirm musical and comfortable ranges",
            "name every keyswitch semantically",
            "approve or reject each parameter binding candidate",
            "verify bank/program and transposition",
        ],
    }
    draft["approval_token"] = _draft_token(draft)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(draft, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"status": "ok", "draft": str(output_path), "approval_token": draft["approval_token"]}


def approve_instrument_profile(draft: str | Path, output: str | Path, *, token: str) -> dict:
    draft_path = Path(draft).resolve(strict=True)
    output_path = Path(output).resolve()
    raw = json.loads(draft_path.read_text(encoding="utf-8"))
    if raw.get("status") != "draft" or raw.get("approved") is not False:
        raise ValueError("input is not an unapproved instrument profile draft")
    stored_token = raw.pop("approval_token", None)
    if not token or token != stored_token or token != _draft_token(raw):
        raise ValueError("approval token does not match the reviewed draft")
    profile = raw["profile"]
    if any(name.startswith("unresolved-") for name in profile.get("keyswitches", {})):
        raise ValueError("rename or remove every unresolved keyswitch before approval")
    if output_path.exists():
        raise ValueError(f"approved profile already exists: {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(yaml.safe_dump(profile, sort_keys=False), encoding="utf-8")
    return {
        "schema_version": "1",
        "status": "ok",
        "profile": str(output_path),
        "draft_sha256": _identity(draft_path)["sha256"],
    }


def seal_instrument_profile(draft: str | Path) -> dict:
    """Seal an edited draft so approval refers to its exact current content."""
    draft_path = Path(draft).resolve(strict=True)
    raw = json.loads(draft_path.read_text(encoding="utf-8"))
    if raw.get("status") != "draft" or raw.get("approved") is not False:
        raise ValueError("input is not an unapproved instrument profile draft")
    raw.pop("approval_token", None)
    token = _draft_token(raw)
    raw["approval_token"] = token
    draft_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "schema_version": "1",
        "status": "ok",
        "draft": str(draft_path),
        "approval_token": token,
    }


def probe_reference_instrument(
    plugin: str | Path,
    output: str | Path,
    *,
    plugin_format: str = "clap",
    low: int = 24,
    high: int = 96,
    step: int = 6,
    sample_rate: int = 24_000,
) -> dict:
    plugin_path = Path(plugin).resolve(strict=True)
    output_dir = Path(output).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    notes = []
    cursor = 0
    note_seconds = 0.20
    # Longer than the reference release so one velocity does not contaminate the next probe.
    gap_seconds = 0.25
    for pitch in range(low, high + 1, step):
        for velocity in (32, 80, 120):
            start = cursor
            end = start + round(note_seconds * sample_rate)
            notes.append(
                {
                    "note_id": f"probe:{pitch}:{velocity}",
                    "pitch": pitch,
                    "velocity": velocity,
                    "start_sample": start,
                    "end_sample": end,
                    "expression": [],
                }
            )
            cursor = end + round(gap_seconds * sample_rate)
    wav_path = output_dir / "probe.wav"
    request = {
        "plugin_format": plugin_format,
        "plugin": str(plugin_path),
        "wav": str(wav_path),
        "midi": str(output_dir / "unused.mid"),
        "sample_rate": sample_rate,
        "tail_seconds": 0.25,
        "automation": [],
        "note_expression": notes,
    }
    render_reference_plugin(request)
    amplitudes = _probe_amplitudes(wav_path, notes)
    report = {
        **_probe_report(notes, amplitudes, wav_path),
        "plugin": _identity(plugin_path),
        "wav": str(wav_path),
    }
    report_path = output_dir / "probe-report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    report["report"] = str(report_path)
    return report


def create_instrument_probe(
    output: str | Path,
    *,
    low: int = 24,
    high: int = 96,
    step: int = 6,
    sample_rate: int = 48_000,
) -> dict:
    """Create a renderer-neutral MIDI and exact analysis schedule."""
    if not 0 <= low <= high <= 127 or step < 1:
        raise ValueError("probe range must satisfy 0 <= low <= high <= 127 and step >= 1")
    root = Path(output).resolve()
    root.mkdir(parents=True, exist_ok=True)
    midi = mido.MidiFile(type=0, ticks_per_beat=480)
    track = mido.MidiTrack()
    midi.tracks.append(track)
    track.append(mido.MetaMessage("set_tempo", tempo=500_000, time=0))
    note_ticks = 192  # 200 ms at 120 BPM
    gap_ticks = 384  # 400 ms to isolate ordinary release tails
    samples_per_tick = sample_rate * 0.5 / 480
    schedule = []
    tick = 0
    for pitch in range(low, high + 1, step):
        for velocity in (32, 80, 120):
            track.append(
                mido.Message(
                    "note_on",
                    channel=0,
                    note=pitch,
                    velocity=velocity,
                    time=gap_ticks if tick else 0,
                )
            )
            tick += gap_ticks if tick else 0
            start_tick = tick
            track.append(
                mido.Message("note_off", channel=0, note=pitch, velocity=0, time=note_ticks)
            )
            tick += note_ticks
            schedule.append(
                {
                    "note_id": f"probe:{pitch}:{velocity}",
                    "pitch": pitch,
                    "velocity": velocity,
                    "start_sample": round(start_tick * samples_per_tick),
                    "end_sample": round(tick * samples_per_tick),
                }
            )
    midi_path = root / "probe.mid"
    midi.save(midi_path)
    plan = {
        "schema_version": "1",
        "status": "probe-plan",
        "sample_rate": sample_rate,
        "midi": str(midi_path),
        "schedule": schedule,
        "instructions": (
            "Render without normalization at the declared sample rate, then analyze-probe."
        ),
    }
    plan_path = root / "probe-plan.json"
    plan_path.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    return {**plan, "plan": str(plan_path)}


def analyze_instrument_probe(audio: str | Path, plan: str | Path, output: str | Path) -> dict:
    audio_path = Path(audio).resolve(strict=True)
    plan_path = Path(plan).resolve(strict=True)
    schedule = json.loads(plan_path.read_text(encoding="utf-8"))
    frames, sample_rate, _ = read_pcm_wav(audio_path)
    if sample_rate != int(schedule["sample_rate"]):
        raise ValueError("render sample rate differs from the probe plan")
    mono = frames.mean(axis=1)
    amplitudes = []
    for note in schedule["schedule"]:
        start, end = int(note["start_sample"]), int(note["end_sample"])
        if end > len(mono):
            raise ValueError("probe render ends before the authored schedule")
        amplitudes.append(float(np.max(np.abs(mono[start:end]))))
    report = _probe_report(schedule["schedule"], amplitudes, audio_path)
    output_path = Path(output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {**report, "report": str(output_path)}


def _probe_amplitudes(path: Path, notes: list[dict[str, Any]]) -> list[float]:
    samples, _, _ = read_pcm_wav(path)
    return [
        float(np.max(np.abs(samples[int(note["start_sample"]) : int(note["end_sample"])])))
        for note in notes
    ]


def _probe_report(notes: list[dict[str, Any]], amplitudes: list[float], audio_path: Path) -> dict:
    by_pitch: dict[int, list[float]] = {}
    for note, amplitude in zip(notes, amplitudes, strict=True):
        by_pitch.setdefault(int(note["pitch"]), []).append(amplitude)
    audible = [pitch for pitch, values in by_pitch.items() if max(values) > 1e-4]
    silent = [pitch for pitch, values in by_pitch.items() if max(values) <= 1e-4]
    non_monotonic = [
        pitch
        for pitch, values in by_pitch.items()
        if any(b <= a for a, b in zip(values, values[1:], strict=False))
    ]
    return {
        "schema_version": "1",
        "status": "ok" if audible and not silent and not non_monotonic else "review",
        "audio": str(audio_path),
        "probe_notes": len(notes),
        "audible_range": [_pitch_name(min(audible)), _pitch_name(max(audible))]
        if audible
        else None,
        "silent_pitches": [_pitch_name(item) for item in silent],
        "non_monotonic_velocity_pitches": [_pitch_name(item) for item in non_monotonic],
        "measurements": [
            {"note_id": note["note_id"], "peak": round(amplitude, 8)}
            for note, amplitude in zip(notes, amplitudes, strict=True)
        ],
    }


def _match_score(value: str, alias: str) -> float:
    if value == alias:
        return 1.0
    if alias in value:
        return 0.85
    words = set(value.replace("_", " ").split())
    alias_words = set(alias.split())
    overlap = len(words & alias_words)
    return 0.6 * overlap / len(alias_words) if overlap else 0.0


def _normalized_default(parameter: dict[str, Any]) -> float:
    low = float(parameter.get("minimum", 0.0))
    high = float(parameter.get("maximum", 1.0))
    return (float(parameter.get("default", low)) - low) / (high - low)


def _midi_number(value: object) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        text = str(value)
        steps = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
        step, rest = text[0].upper(), text[1:]
        accidental = 1 if rest.startswith("#") else -1 if rest.startswith("b") else 0
        if accidental:
            rest = rest[1:]
        return (int(rest) + 1) * 12 + steps[step] + accidental


def _pitch_name(midi: int) -> str:
    names = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
    return f"{names[midi % 12]}{midi // 12 - 1}"


def _identity(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    return {"path": str(path), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def _draft_token(raw: dict[str, Any]) -> str:
    canonical = json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()
