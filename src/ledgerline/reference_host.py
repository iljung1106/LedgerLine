from __future__ import annotations

import argparse
import json
import math
import sys
import wave
from pathlib import Path
from typing import Any

import mido
import numpy as np


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ledgerline-host")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--ledgerline-scan-request", type=Path)
    group.add_argument("--ledgerline-request", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.ledgerline_scan_request:
            request = _read_json(args.ledgerline_scan_request)
            manifest = _manifest(Path(request["plugin"]), request["plugin_format"])
            print(json.dumps(_scan_response(manifest), ensure_ascii=False))
        else:
            request = _read_json(args.ledgerline_request)
            render_reference_plugin(request)
        return 0
    except Exception as exc:  # host boundary must turn every failure into a process failure
        print(json.dumps({"status": "error", "message": str(exc)}), file=sys.stderr)
        return 2


def render_reference_plugin(request: dict[str, Any]) -> dict:
    plugin = Path(request["plugin"]).resolve(strict=True)
    manifest = _manifest(plugin, str(request["plugin_format"]))
    if manifest.get("engine") != "ledgerline-reference-synth":
        raise ValueError(
            "bundled host only renders signed LedgerLine reference manifests; "
            "use an SDK-backed native adapter for third-party VST3/CLAP binaries"
        )
    sample_rate = _bounded_int(request.get("sample_rate", 48_000), 8_000, 384_000)
    state = dict(manifest.get("defaults", {}))
    if request.get("state"):
        state.update(_read_json(Path(request["state"])))
    notes = request.get("note_expression") or _midi_notes(Path(request["midi"]), sample_rate)
    tail = float(request.get("tail_seconds", manifest.get("tail_seconds", 0.5)))
    final_sample = max((int(note["end_sample"]) for note in notes), default=0)
    length = max(1, final_sample + round(tail * sample_rate))
    audio = np.zeros((length, 2), dtype=np.float64)
    global_gain = _automation_curve(
        request.get("automation", []), "gain", length, float(state.get("gain", 0.25))
    )
    brightness = _automation_curve(
        request.get("automation", []),
        "brightness",
        length,
        float(state.get("brightness", 0.45)),
    )
    attack = max(0.001, float(state.get("attack", 0.012)))
    release = max(0.005, float(state.get("release", 0.18)))
    for note in notes:
        _render_note(audio, note, sample_rate, attack, release, brightness, global_gain)
    peak = float(np.max(np.abs(audio)))
    if peak > 0.98:
        audio *= 0.98 / peak
    output = Path(request["wav"]).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    pcm = np.round(np.clip(audio, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(str(output), "wb") as stream:
        stream.setnchannels(2)
        stream.setsampwidth(2)
        stream.setframerate(sample_rate)
        stream.writeframes(pcm.tobytes())
    return {"status": "ok", "wav": str(output), "samples": length, "notes": len(notes)}


def _render_note(
    audio: np.ndarray,
    note: dict[str, Any],
    sample_rate: int,
    attack: float,
    release: float,
    brightness: np.ndarray,
    gain: np.ndarray,
) -> None:
    start = max(0, int(note["start_sample"]))
    end = min(len(audio), max(start + 1, int(note["end_sample"])))
    release_samples = round(release * sample_rate)
    render_end = min(len(audio), end + release_samples)
    count = render_end - start
    if count <= 0:
        return
    local = np.arange(count)
    position = np.clip(local / max(1, end - start), 0.0, 1.0)
    expression = note.get("expression", [])
    pitch_curve = _expression_curve(expression, "pitch", position, 0.0)
    pressure = _expression_curve(expression, "pressure", position, 0.75)
    timbre = _expression_curve(expression, "timbre", position, 0.5)
    midi_pitch = float(note["pitch"])
    frequency = 440.0 * np.power(2.0, (midi_pitch - 69.0 + pitch_curve / 100.0) / 12.0)
    phase = np.cumsum(2.0 * math.pi * frequency / sample_rate)
    bright = np.clip(brightness[start:render_end] * 0.7 + timbre * 0.3, 0.0, 1.0)
    tone = np.sin(phase)
    tone += (0.12 + 0.30 * bright) * np.sin(2 * phase)
    tone += (0.04 + 0.18 * bright) * np.sin(3 * phase)
    envelope = np.minimum(1.0, local / max(1, round(attack * sample_rate)))
    release_mask = local >= end - start
    envelope[release_mask] *= np.maximum(
        0.0, 1.0 - (local[release_mask] - (end - start)) / max(1, release_samples)
    )
    velocity = max(1, min(127, int(note.get("velocity", 80)))) / 127.0
    signal = tone * envelope * pressure * velocity * gain[start:render_end]
    pan = max(-1.0, min(1.0, (midi_pitch - 60.0) / 48.0))
    left = math.cos((pan + 1) * math.pi / 4)
    right = math.sin((pan + 1) * math.pi / 4)
    audio[start:render_end, 0] += signal * left
    audio[start:render_end, 1] += signal * right


def _expression_curve(
    expression: list[dict[str, Any]], parameter: str, positions: np.ndarray, default: float
) -> np.ndarray:
    points = sorted(
        (
            (float(item["position"]), float(item["value"]))
            for item in expression
            if item.get("parameter") == parameter
        ),
        key=lambda item: item[0],
    )
    if not points:
        return np.full_like(positions, default, dtype=np.float64)
    x = np.array([item[0] for item in points], dtype=np.float64)
    y = np.array([item[1] for item in points], dtype=np.float64)
    return np.interp(positions, x, y, left=y[0], right=y[-1])


def _automation_curve(
    events: list[dict[str, Any]], parameter: str, length: int, default: float
) -> np.ndarray:
    points = [(0, default)]
    points.extend(
        (max(0, min(length - 1, int(item["sample"]))), float(item["value"]))
        for item in events
        if item.get("parameter") == parameter
    )
    points.sort()
    x = np.array([item[0] for item in points], dtype=np.float64)
    y = np.array([item[1] for item in points], dtype=np.float64)
    return np.interp(np.arange(length), x, y, left=y[0], right=y[-1])


def _midi_notes(path: Path, sample_rate: int) -> list[dict[str, Any]]:
    midi = mido.MidiFile(path)
    seconds = 0.0
    tempo = 500_000
    active: dict[tuple[int, int], list[tuple[float, int]]] = {}
    notes: list[dict[str, Any]] = []
    for message in mido.merge_tracks(midi.tracks):
        seconds += mido.tick2second(message.time, midi.ticks_per_beat, tempo)
        if message.type == "set_tempo":
            tempo = message.tempo
        elif message.type == "note_on" and message.velocity > 0:
            active.setdefault((message.channel, message.note), []).append(
                (seconds, message.velocity)
            )
        elif message.type in {"note_off", "note_on"}:
            queue = active.get((message.channel, message.note), [])
            if queue:
                start, velocity = queue.pop(0)
                notes.append(
                    {
                        "pitch": message.note,
                        "velocity": velocity,
                        "start_sample": round(start * sample_rate),
                        "end_sample": max(
                            round(seconds * sample_rate), round(start * sample_rate) + 1
                        ),
                        "expression": [],
                    }
                )
    return notes


def _manifest(path: Path, plugin_format: str) -> dict[str, Any]:
    if plugin_format not in {"vst3", "clap"}:
        raise ValueError("plugin_format must be vst3 or clap")
    if path.suffixes[-2:] != [".llplugin", ".json"]:
        raise ValueError("bundled host requires a .llplugin.json reference manifest")
    raw = _read_json(path)
    if raw.get("schema_version") != "1" or raw.get("plugin_format") != plugin_format:
        raise ValueError("reference manifest schema or plugin format mismatch")
    return raw


def _scan_response(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "1",
        "name": manifest["name"],
        "vendor": manifest["vendor"],
        "version": manifest["version"],
        "parameters": manifest.get("parameters", []),
        "supports_state": True,
        "latency_samples": int(manifest.get("latency_samples", 0)),
        "tail_samples": int(manifest.get("tail_samples", 24_000)),
        "audio_ports": [{"direction": "output", "channels": 2}],
        "note_ports": [
            {
                "direction": "input",
                "dialects": ["midi", "mpe", "clap-note-expression", "midi2-plan"],
            }
        ],
    }


def reference_manifest(plugin_format: str = "clap") -> Path:
    return (
        Path(__file__).parent
        / "data"
        / "reference_plugins"
        / f"ledgerline-sine.{plugin_format}.llplugin.json"
    )


def _read_json(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return raw


def _bounded_int(value: Any, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"integer must be between {minimum} and {maximum}")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
