from __future__ import annotations

import shutil
import tempfile
from fractions import Fraction
from pathlib import Path
from threading import RLock
from typing import Any

import yaml

from ledgerline.compiler import compile_project
from ledgerline.mix_config import load_mix_config
from ledgerline.model import parse_duration, parse_pitch
from ledgerline.project import load_piece
from ledgerline.studio_model import project_revision


class StudioSession:
    """Transactional in-place edits with process-local undo and redo."""

    def __init__(self, project: str | Path):
        self.root = Path(project).resolve()
        load_piece(self.root)
        self._undo: list[dict[str, bytes]] = []
        self._redo: list[dict[str, bytes]] = []
        self._lock = RLock()

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def apply(self, commands: list[dict[str, Any]], *, revision: str | None = None) -> dict:
        if not commands:
            raise ValueError("at least one edit command is required")
        with self._lock:
            if revision and revision != project_revision(self.root):
                raise ValueError("project changed after this editor state was loaded")
            before = self._capture()
            try:
                applied = [self._apply_command(command) for command in commands]
                load_piece(self.root)
                if (self.root / "mix.yaml").is_file():
                    load_mix_config(self.root)
                compile_project(self.root)
            except Exception:
                self._restore(before)
                raise
            self._undo.append(before)
            self._redo.clear()
            return {
                "schema_version": "1",
                "status": "ok",
                "applied": applied,
                "revision": project_revision(self.root),
                "can_undo": self.can_undo,
                "can_redo": self.can_redo,
            }

    def undo(self) -> dict:
        with self._lock:
            if not self._undo:
                raise ValueError("nothing to undo")
            current = self._capture()
            previous = self._undo.pop()
            self._restore(previous)
            compile_project(self.root)
            self._redo.append(current)
            return self._history_report("undo")

    def redo(self) -> dict:
        with self._lock:
            if not self._redo:
                raise ValueError("nothing to redo")
            current = self._capture()
            following = self._redo.pop()
            self._restore(following)
            compile_project(self.root)
            self._undo.append(current)
            return self._history_report("redo")

    def _apply_command(self, command: dict[str, Any]) -> dict:
        if not isinstance(command, dict):
            raise ValueError("edit command must be an object")
        kind = command.get("type")
        if kind == "update_note":
            return self._update_note(command)
        if kind in {"move_event", "resize_event"}:
            return self._rebuild_voice(command)
        if kind == "update_mix":
            return self._update_mix(command)
        if kind == "update_tempo":
            return self._update_tempo(command)
        if kind in {"transpose_range", "scale_velocity_range", "set_articulation_range"}:
            return self._range_edit(command)
        raise ValueError(f"unsupported Studio command: {kind!r}")

    def _update_note(self, command: dict[str, Any]) -> dict:
        path, data, event = self._event(command)
        changes = command.get("changes")
        if not isinstance(changes, dict) or not changes:
            raise ValueError("update_note.changes must be a non-empty object")
        unknown = set(changes) - {"pitch", "velocity", "articulation"}
        if unknown:
            raise ValueError(f"unsupported note fields: {', '.join(sorted(unknown))}")
        if "pitch" in changes:
            pitch = str(changes["pitch"])
            parse_pitch(pitch)
            raw = event["p"]
            pitch_index = _integer(command.get("pitch_index", 0), "pitch_index")
            if isinstance(raw, str):
                if pitch_index != 0:
                    raise ValueError("single note pitch_index must be 0")
                event["p"] = pitch
            else:
                if not 0 <= pitch_index < len(raw):
                    raise ValueError("pitch_index is outside the chord")
                raw[pitch_index] = pitch
        if "velocity" in changes:
            velocity = _integer(changes["velocity"], "velocity")
            if not 1 <= velocity <= 127:
                raise ValueError("velocity must be between 1 and 127")
            event["vel"] = velocity
        if "articulation" in changes:
            articulation = changes["articulation"]
            if articulation in {None, ""}:
                event.pop("art", None)
            elif articulation not in {"staccato", "tenuto", "accent", "marcato"}:
                raise ValueError("unsupported articulation")
            else:
                event["art"] = articulation
        _write_yaml(path, data)
        return {"type": "update_note", "part": command["part"], "changes": changes}

    def _rebuild_voice(self, command: dict[str, Any]) -> dict:
        part = str(command["part"])
        measure = _positive_integer(command["measure"], "measure")
        voice = str(command["voice"])
        event_index = _integer(command["event_index"], "event_index")
        path, data = self._part_document(part)
        measure_data = data.get("measures", {}).get(str(measure))
        if measure_data is None:
            measure_data = data.get("measures", {}).get(measure)
        if not isinstance(measure_data, dict) or not isinstance(measure_data.get(voice), list):
            raise ValueError("voice does not exist")
        events = measure_data[voice]
        if not 0 <= event_index < len(events) or events[event_index].get("r", False):
            raise ValueError("selected event is not an editable note")
        placements: list[tuple[Fraction, dict[str, Any]]] = []
        cursor = Fraction(0)
        selected: tuple[Fraction, dict[str, Any]] | None = None
        for index, event in enumerate(events):
            duration = parse_duration(str(event["d"]))
            if not event.get("r", False):
                item = (cursor, dict(event))
                if index == event_index:
                    selected = item
                else:
                    placements.append(item)
            cursor += duration
        if selected is None:
            raise ValueError("selected event was not found")
        start, selected_event = selected
        if command["type"] == "move_event":
            start = Fraction(str(command["target_offset_whole"]))
        else:
            duration_text = str(command["duration"])
            parse_duration(duration_text)
            selected_event["d"] = duration_text
        placements.append((start, selected_event))
        piece = load_piece(self.root)
        measure_length = piece.time_at(measure).length
        measure_data[voice] = _rebuild_measure_voice(placements, measure_length)
        _write_yaml(path, data)
        return {
            "type": command["type"],
            "part": part,
            "measure": measure,
            "voice": voice,
        }

    def _update_mix(self, command: dict[str, Any]) -> dict:
        part = str(command["part"])
        path = self.root / "mix.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        tracks = data.get("tracks", {})
        if part not in tracks:
            raise ValueError(f"mix track does not exist: {part}")
        changes = command.get("changes")
        if not isinstance(changes, dict) or set(changes) - {"gain_db", "pan", "send"}:
            raise ValueError("update_mix supports gain_db, pan, and send")
        if "gain_db" in changes:
            gain = float(changes["gain_db"])
            if not -120 <= gain <= 24:
                raise ValueError("gain_db must be between -120 and 24")
            tracks[part]["gain_db"] = gain
        if "pan" in changes:
            pan = float(changes["pan"])
            if not -1 <= pan <= 1:
                raise ValueError("pan must be between -1 and 1")
            tracks[part]["pan"] = pan
        if "send" in changes:
            send = changes["send"]
            if not isinstance(send, dict) or set(send) != {"bus", "gain_db"}:
                raise ValueError("send requires bus and gain_db")
            tracks[part].setdefault("sends", {})[str(send["bus"])] = float(send["gain_db"])
        _write_yaml(path, data)
        return {"type": "update_mix", "part": part, "changes": changes}

    def _update_tempo(self, command: dict[str, Any]) -> dict:
        path = self.root / "piece.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        index = _integer(command.get("index", 0), "index")
        bpm = float(command["bpm"])
        if not 1 <= bpm <= 999 or not 0 <= index < len(data["tempo"]):
            raise ValueError("tempo index or bpm is invalid")
        data["tempo"][index]["bpm"] = bpm
        _write_yaml(path, data)
        return {"type": "update_tempo", "index": index, "bpm": bpm}

    def _range_edit(self, command: dict[str, Any]) -> dict:
        part = str(command["part"])
        start = _positive_integer(command.get("measure_start", 1), "measure_start")
        end = _positive_integer(command.get("measure_end", start), "measure_end")
        if end < start:
            raise ValueError("measure range is reversed")
        path, data = self._part_document(part)
        affected = 0
        for raw_number, measure in data.get("measures", {}).items():
            if not start <= int(raw_number) <= end:
                continue
            for events in measure.values():
                for event in events:
                    if event.get("r", False):
                        continue
                    if command["type"] == "transpose_range":
                        semitones = _integer(command["semitones"], "semitones")
                        raw_pitch = event["p"]
                        pitches = [raw_pitch] if isinstance(raw_pitch, str) else raw_pitch
                        changed = [
                            _midi_pitch(parse_pitch(item).midi + semitones)
                            for item in pitches
                        ]
                        event["p"] = changed[0] if isinstance(raw_pitch, str) else changed
                    elif command["type"] == "scale_velocity_range":
                        factor = float(command["factor"])
                        source = int(event.get("vel", 76))
                        event["vel"] = max(1, min(127, round(source * factor)))
                    else:
                        articulation = command.get("articulation")
                        if articulation in {None, ""}:
                            event.pop("art", None)
                        else:
                            event["art"] = str(articulation)
                    affected += 1
        _write_yaml(path, data)
        return {"type": command["type"], "part": part, "affected": affected}

    def _event(self, command: dict[str, Any]) -> tuple[Path, dict, dict]:
        path, data = self._part_document(str(command["part"]))
        measure = _positive_integer(command["measure"], "measure")
        voice = str(command["voice"])
        index = _integer(command["event_index"], "event_index")
        measures = data.get("measures", {})
        measure_data = measures.get(str(measure), measures.get(measure))
        if not isinstance(measure_data, dict) or voice not in measure_data:
            raise ValueError("event voice does not exist")
        events = measure_data[voice]
        if not 0 <= index < len(events) or events[index].get("r", False):
            raise ValueError("event is not an editable note")
        return path, data, events[index]

    def _part_document(self, part: str) -> tuple[Path, dict]:
        piece = yaml.safe_load((self.root / "piece.yaml").read_text(encoding="utf-8"))
        reference = next((item for item in piece["parts"] if str(item["id"]) == part), None)
        if reference is None:
            raise ValueError(f"unknown part: {part}")
        path = (self.root / str(reference["file"])).resolve()
        return path, yaml.safe_load(path.read_text(encoding="utf-8"))

    def _capture(self) -> dict[str, bytes]:
        paths = [self.root / "piece.yaml", *sorted((self.root / "parts").glob("*.yaml"))]
        paths.extend(
            path
            for name in ("mix.yaml", "automation.yaml", "performance.yaml", "render.yaml")
            if (path := self.root / name).is_file()
        )
        return {path.relative_to(self.root).as_posix(): path.read_bytes() for path in paths}

    def _restore(self, state: dict[str, bytes]) -> None:
        with tempfile.TemporaryDirectory(prefix="ledgerline-studio-restore-") as temporary:
            staging = Path(temporary)
            for relative, content in state.items():
                target = staging / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
            for relative in state:
                source = staging / relative
                target = self.root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
        load_piece(self.root)

    def _history_report(self, action: str) -> dict:
        return {
            "schema_version": "1",
            "status": "ok",
            "action": action,
            "revision": project_revision(self.root),
            "can_undo": self.can_undo,
            "can_redo": self.can_redo,
        }


def _rebuild_measure_voice(
    placements: list[tuple[Fraction, dict[str, Any]]], measure_length: Fraction
) -> list[dict[str, Any]]:
    cursor = Fraction(0)
    result: list[dict[str, Any]] = []
    for start, event in sorted(placements, key=lambda item: item[0]):
        duration = parse_duration(str(event["d"]))
        if start < cursor:
            raise ValueError("moved or resized event overlaps another event in the voice")
        if start + duration > measure_length:
            raise ValueError("moved or resized event leaves the measure")
        result.extend({"r": True, "d": token} for token in _rest_tokens(start - cursor))
        result.append(event)
        cursor = start + duration
    result.extend({"r": True, "d": token} for token in _rest_tokens(measure_length - cursor))
    return result


def _rest_tokens(duration: Fraction) -> list[str]:
    if duration < 0:
        raise ValueError("negative rest duration")
    candidates = []
    for denominator in (1, 2, 4, 8, 16, 32):
        base = Fraction(1, denominator)
        candidates.extend(
            [
                (base * Fraction(7, 4), f"1/{denominator}.."),
                (base * Fraction(3, 2), f"1/{denominator}."),
                (base, f"1/{denominator}"),
            ]
        )
    candidates.sort(reverse=True, key=lambda item: item[0])
    result = []
    remaining = duration
    for value, token in candidates:
        while value <= remaining:
            result.append(token)
            remaining -= value
    if remaining:
        raise ValueError("timeline edit cannot be represented by LedgerLine duration tokens")
    return result


def _midi_pitch(midi: int) -> str:
    if not 0 <= midi <= 127:
        raise ValueError("pitch leaves MIDI range")
    names = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
    return f"{names[midi % 12]}{midi // 12 - 1}"


def _write_yaml(path: Path, data: dict) -> None:
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


def _integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    return value


def _positive_integer(value: Any, field: str) -> int:
    result = _integer(value, field)
    if result < 1:
        raise ValueError(f"{field} must be positive")
    return result
