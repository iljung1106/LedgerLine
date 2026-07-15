from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import json
import math
import os
import re
import tempfile
import uuid
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path
from threading import RLock
from typing import Any

import yaml

from ledgerline.automation import INTERPOLATIONS
from ledgerline.brief import validate_edit_actions
from ledgerline.build_state import archive_media_checkpoint
from ledgerline.compiler import compile_project
from ledgerline.mix_config import load_mix_config
from ledgerline.model import (
    control_event_from_dict,
    event_from_dict,
    parse_anchor,
    parse_duration,
    parse_pitch,
)
from ledgerline.project import load_piece
from ledgerline.render_graph import load_render_graph
from ledgerline.studio_model import project_revision

_PROFILE_ID_RE = re.compile(r"^[a-z][a-z0-9._-]{0,127}$")


class StudioSession:
    """Transactional edits with bounded, restart-safe undo and redo snapshots."""

    HISTORY_LIMIT = 24

    def __init__(self, project: str | Path):
        self.root = Path(project).resolve()
        load_piece(self.root)
        self._undo, self._redo = self._load_history()
        self._lock = RLock()

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def apply(
        self,
        commands: list[dict[str, Any]],
        *,
        revision: str | None = None,
        expected_revision: str | None = None,
        expected_impact: dict[str, Any] | None = None,
    ) -> dict:
        if not commands:
            raise ValueError("at least one edit command is required")
        with self._lock:
            if revision and revision != project_revision(self.root):
                raise ValueError("project changed after this editor state was loaded")
            violations = validate_edit_actions(self.root, commands)
            if violations:
                raise ValueError("protected brief range blocks edit: " + "; ".join(violations))
            archive_media_checkpoint(self.root)
            before = self._capture()
            before_revision = project_revision(self.root)
            try:
                applied = [self._apply_command(command) for command in commands]
                piece = load_piece(self.root)
                if (self.root / "render.yaml").is_file():
                    load_render_graph(self.root, piece)
                if (self.root / "mix.yaml").is_file():
                    load_mix_config(self.root)
                compile_project(self.root)
                after = self._capture()
                after_revision = project_revision(self.root)
                actual_impact = _source_impact(before, after)
                if expected_revision is not None and after_revision != expected_revision:
                    raise ValueError(
                        "Studio edit result does not match the approved preview revision"
                    )
                if expected_impact is not None and actual_impact != expected_impact:
                    raise ValueError(
                        "Studio edit impact does not match the approved proposal preview"
                    )
                transaction = self._record_transaction(
                    "apply",
                    before,
                    after,
                    before_revision=before_revision,
                    after_revision=after_revision,
                    command_types=[str(command.get("type", "")) for command in commands],
                )
            except Exception:
                self._restore(before)
                raise
            self._undo.append(before)
            self._undo = self._undo[-self.HISTORY_LIMIT :]
            self._redo.clear()
            self._persist_history()
            return {
                "schema_version": "1",
                "status": "ok",
                "applied": applied,
                "revision": after_revision,
                "transaction": transaction,
                "can_undo": self.can_undo,
                "can_redo": self.can_redo,
            }

    def undo(self) -> dict:
        with self._lock:
            if not self._undo:
                raise ValueError("nothing to undo")
            current = self._capture()
            before_revision = project_revision(self.root)
            previous = self._undo.pop()
            self._restore(previous)
            compile_project(self.root)
            self._record_transaction(
                "undo",
                current,
                previous,
                before_revision=before_revision,
                after_revision=project_revision(self.root),
                command_types=["undo"],
            )
            self._redo.append(current)
            self._redo = self._redo[-self.HISTORY_LIMIT :]
            self._persist_history()
            return self._history_report("undo")

    def redo(self) -> dict:
        with self._lock:
            if not self._redo:
                raise ValueError("nothing to redo")
            current = self._capture()
            before_revision = project_revision(self.root)
            following = self._redo.pop()
            self._restore(following)
            compile_project(self.root)
            self._record_transaction(
                "redo",
                current,
                following,
                before_revision=before_revision,
                after_revision=project_revision(self.root),
                command_types=["redo"],
            )
            self._undo.append(current)
            self._undo = self._undo[-self.HISTORY_LIMIT :]
            self._persist_history()
            return self._history_report("redo")

    def _apply_command(self, command: dict[str, Any]) -> dict:
        if not isinstance(command, dict):
            raise ValueError("edit command must be an object")
        kind = command.get("type")
        if kind == "update_note":
            return self._update_note(command)
        if kind == "update_event":
            return self._update_event(command)
        if kind == "insert_event":
            return self._insert_event(command)
        if kind == "delete_event":
            return self._delete_event(command)
        if kind == "duplicate_event":
            return self._duplicate_event(command)
        if kind == "replace_measure_voice":
            return self._replace_measure_voice(command)
        if kind in {"move_event", "resize_event"}:
            return self._rebuild_voice(command)
        if kind == "update_instrument":
            return self._update_instrument(command)
        if kind == "update_mix":
            return self._update_mix(command)
        if kind == "update_mix_node":
            return self._update_mix_node(command)
        if kind in {"set_mix_send", "delete_mix_send"}:
            return self._edit_mix_send(command)
        if kind in {
            "add_mix_insert",
            "update_mix_insert",
            "delete_mix_insert",
            "reorder_mix_insert",
        }:
            return self._edit_mix_insert(command)
        if kind == "update_tempo":
            return self._update_tempo(command)
        if kind in {"insert_tempo", "delete_tempo"}:
            return self._edit_tempo(command)
        if kind in {"insert_control", "update_control", "delete_control"}:
            return self._edit_control(command)
        if kind in {"insert_point", "update_point", "move_point", "delete_point", "set_curve"}:
            return self._edit_automation(command)
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
            else:
                event["art"] = str(articulation)
        _write_yaml(path, data)
        return {
            "type": "update_note",
            "part": command["part"],
            "event_id": event.get("id"),
            "changes": changes,
        }

    def _update_event(self, command: dict[str, Any]) -> dict:
        path, data, event = self._event(command)
        changes = command.get("changes")
        if not isinstance(changes, dict) or not changes:
            raise ValueError("update_event.changes must be a non-empty object")
        aliases = {
            "pitch": "p",
            "duration": "d",
            "dynamic": "dyn",
            "articulation": "art",
            "velocity": "vel",
        }
        normalized = {
            aliases.get(str(field), str(field)): value for field, value in changes.items()
        }
        allowed = {
            "p",
            "d",
            "dyn",
            "art",
            "tie",
            "vel",
            "staff",
            "expr",
            "tuplet",
            "grace",
            "slur",
            "pitch_cents",
        }
        unknown = set(normalized) - allowed
        if unknown:
            raise ValueError(f"unsupported event fields: {', '.join(sorted(unknown))}")
        pitch_cents = normalized.pop("pitch_cents", None)
        if pitch_cents is not None:
            if "expr" in normalized:
                raise ValueError("update_event cannot set expr and pitch_cents together")
            if isinstance(pitch_cents, bool) or not isinstance(pitch_cents, (int, float)):
                raise ValueError("pitch_cents must be numeric")
            pitch_cents = float(pitch_cents)
            if not math.isfinite(pitch_cents) or not -200 <= pitch_cents <= 200:
                raise ValueError("pitch_cents must be between -200 and 200")
        for field, value in normalized.items():
            if value is None and field not in {"p", "d"}:
                event.pop(field, None)
            else:
                event[field] = copy.deepcopy(value)
        if pitch_cents is not None:
            expression = copy.deepcopy(event.get("expr") or {})
            if not isinstance(expression, dict):
                raise ValueError("event expr must be a mapping")
            expression["pitch_cents"] = pitch_cents
            event["expr"] = expression
        event_from_dict(dict(event))
        _write_yaml(path, data)
        return {
            "type": "update_event",
            "part": command["part"],
            "event_id": event.get("id"),
            "changes": {
                **normalized,
                **({"pitch_cents": pitch_cents} if pitch_cents is not None else {}),
            },
        }

    def _insert_event(self, command: dict[str, Any]) -> dict:
        part = str(command["part"])
        measure = _positive_integer(command["measure"], "measure")
        voice = str(command["voice"])
        path, data, events = self._voice_document(part, measure, voice)
        raw_event = command.get("event")
        if not isinstance(raw_event, dict):
            raise ValueError("insert_event.event must be an object")
        event = copy.deepcopy(raw_event)
        if event.get("r", False):
            raise ValueError("insert_event authors notes or chords; silence is created by deleting")
        event.setdefault("id", _new_id("evt"))
        event_from_dict(dict(event))
        offset = _optional_offset(command)
        if offset is None:
            index = _integer(command.get("event_index", len(events)), "event_index")
            if not 0 <= index <= len(events):
                raise ValueError("event_index is outside the voice")
            events.insert(index, event)
        else:
            placements = _note_placements(events)
            placements.append((offset, event))
            events[:] = _rebuild_measure_voice(placements, self._measure_length(measure))
        _write_yaml(path, data)
        return {
            "type": "insert_event",
            "part": part,
            "measure": measure,
            "voice": voice,
            "event_id": event["id"],
        }

    def _delete_event(self, command: dict[str, Any]) -> dict:
        part, measure, voice, index, path, data, events = self._event_location(command)
        deleted_id = events[index].get("id")
        placements = [
            item for source_index, item in _indexed_note_placements(events) if source_index != index
        ]
        events[:] = _rebuild_measure_voice(placements, self._measure_length(measure))
        _write_yaml(path, data)
        return {
            "type": "delete_event",
            "part": part,
            "measure": measure,
            "voice": voice,
            "event_id": deleted_id,
        }

    def _duplicate_event(self, command: dict[str, Any]) -> dict:
        part, _measure, _voice, index, _path, _data, source = self._event_location(command)
        duplicate = copy.deepcopy(source[index])
        duplicate["id"] = str(command.get("new_event_id") or _new_id("evt"))
        event_from_dict(dict(duplicate))
        target_measure = _positive_integer(
            command.get("target_measure", command["measure"]), "target_measure"
        )
        target_voice = str(command.get("target_voice", command["voice"]))
        path, data, events = self._voice_document(part, target_measure, target_voice)
        offset = _optional_offset(command, required=True)
        placements = _note_placements(events)
        placements.append((offset, duplicate))
        events[:] = _rebuild_measure_voice(placements, self._measure_length(target_measure))
        _write_yaml(path, data)
        return {
            "type": "duplicate_event",
            "part": part,
            "measure": target_measure,
            "voice": target_voice,
            "event_id": duplicate["id"],
        }

    def _replace_measure_voice(self, command: dict[str, Any]) -> dict:
        part = str(command["part"])
        measure = _positive_integer(command["measure"], "measure")
        voice = str(command["voice"])
        path, data, events = self._voice_document(part, measure, voice)
        replacement = command.get("events")
        if not isinstance(replacement, list) or not replacement:
            raise ValueError("replace_measure_voice.events must be a non-empty list")
        prepared = []
        for raw_event in replacement:
            if not isinstance(raw_event, dict):
                raise ValueError("replace_measure_voice events must be objects")
            event = copy.deepcopy(raw_event)
            if not event.get("r", False):
                event.setdefault("id", _new_id("evt"))
            event_from_dict(dict(event))
            prepared.append(event)
        duration = sum(
            (parse_duration(str(event["d"])) for event in prepared), start=Fraction(0)
        )
        if duration != self._measure_length(measure):
            raise ValueError("replacement voice duration must exactly fill the measure")
        events[:] = prepared
        _write_yaml(path, data)
        return {
            "type": "replace_measure_voice",
            "part": part,
            "measure": measure,
            "voice": voice,
            "event_ids": [event.get("id") for event in prepared if event.get("id")],
        }

    def _rebuild_voice(self, command: dict[str, Any]) -> dict:
        part, measure, voice, event_index, path, data, events = self._event_location(command)
        indexed = _indexed_note_placements(events)
        selected = next(item for index, item in indexed if index == event_index)
        placements = [item for index, item in indexed if index != event_index]
        start, selected_event = selected
        if command["type"] == "move_event":
            start = _optional_offset(command, required=True)
            target_measure = _positive_integer(
                command.get("target_measure", measure), "target_measure"
            )
            target_voice = str(command.get("target_voice", voice))
            if (target_measure, target_voice) == (measure, voice):
                placements.append((start, selected_event))
                events[:] = _rebuild_measure_voice(placements, self._measure_length(measure))
            else:
                source_length = self._measure_length(measure)
                target_length = self._measure_length(target_measure)
                target_events = self._voice_from_data(data, target_measure, target_voice)
                target_placements = _note_placements(target_events)
                target_placements.append((start, selected_event))
                rebuilt_source = _rebuild_measure_voice(placements, source_length)
                rebuilt_target = _rebuild_measure_voice(target_placements, target_length)
                events[:] = rebuilt_source
                target_events[:] = rebuilt_target
        else:
            duration_text = str(command["duration"])
            parse_duration(duration_text)
            selected_event["d"] = duration_text
            placements.append((start, selected_event))
            events[:] = _rebuild_measure_voice(placements, self._measure_length(measure))
        _write_yaml(path, data)
        return {
            "type": command["type"],
            "part": part,
            "measure": command.get("target_measure", measure),
            "voice": command.get("target_voice", voice),
            "event_id": selected_event.get("id"),
        }

    def _update_mix(self, command: dict[str, Any]) -> dict:
        part = str(command["part"])
        path = self.root / "mix.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        tracks = data.get("tracks", {})
        if not isinstance(tracks, dict) or part not in tracks:
            raise ValueError(f"mix track does not exist: {part}")
        changes = command.get("changes")
        if (
            not isinstance(changes, dict)
            or not changes
            or set(changes) - {"gain_db", "pan", "send"}
        ):
            raise ValueError("update_mix supports gain_db, pan, and send")
        if "gain_db" in changes:
            gain = _mix_gain(changes["gain_db"], "gain_db")
            tracks[part]["gain_db"] = gain
        if "pan" in changes:
            pan = _finite_number(changes["pan"], "pan")
            if not -1 <= pan <= 1:
                raise ValueError("pan must be between -1 and 1")
            tracks[part]["pan"] = pan
        if "send" in changes:
            send = changes["send"]
            if not isinstance(send, dict) or set(send) != {"bus", "gain_db"}:
                raise ValueError("send requires bus and gain_db")
            gain = _mix_gain(send["gain_db"], "send.gain_db")
            if data.get("format") == 1:
                if str(send["bus"]) != "__legacy_reverb":
                    raise ValueError("format 1 update_mix send bus must be __legacy_reverb")
                tracks[part]["reverb_send_db"] = gain
            else:
                tracks[part].setdefault("sends", {})[str(send["bus"])] = gain
        _write_yaml(path, data)
        load_mix_config(self.root)
        return {"type": "update_mix", "part": part, "changes": changes}

    def _update_instrument(self, command: dict[str, Any]) -> dict:
        part = command.get("part")
        if not isinstance(part, str) or not part.strip():
            raise ValueError("update_instrument.part must be non-empty")
        part = part.strip()
        changes = command.get("changes")
        if not isinstance(changes, dict) or not changes:
            raise ValueError("update_instrument.changes must be a non-empty object")
        unknown = sorted(set(changes) - {"profile", "instrument", "state"})
        if unknown:
            raise ValueError(
                "update_instrument cannot change " + ", ".join(unknown)
            )

        piece_path = self.root / "piece.yaml"
        piece_data = yaml.safe_load(piece_path.read_text(encoding="utf-8"))
        if not isinstance(piece_data, dict) or not isinstance(piece_data.get("parts"), list):
            raise ValueError("piece.yaml parts must be a list")
        references = [
            item
            for item in piece_data["parts"]
            if isinstance(item, dict) and item.get("id") == part
        ]
        if len(references) != 1:
            raise ValueError(f"instrument part must resolve exactly once: {part!r}")
        if "profile" in changes:
            profile = changes["profile"]
            if (
                not isinstance(profile, str)
                or not _PROFILE_ID_RE.fullmatch(profile)
                or ".." in profile
            ):
                raise ValueError("profile must be a safe LedgerLine profile id")
            references[0]["profile"] = profile

        render_fields = {"instrument", "state"} & set(changes)
        render_path = self.root / "render.yaml"
        render_data: dict[str, Any] | None = None
        if render_fields:
            if not render_path.is_file():
                raise ValueError(
                    "render.yaml is required to change instrument or preset state"
                )
            loaded = yaml.safe_load(render_path.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict) or not isinstance(loaded.get("nodes"), list):
                raise ValueError("render.yaml nodes must be a list")
            render_data = loaded
            nodes = [
                item
                for item in render_data["nodes"]
                if isinstance(item, dict) and item.get("part") == part
            ]
            if len(nodes) != 1:
                raise ValueError(f"render node must resolve exactly once for part: {part}")
            node = nodes[0]
            if "instrument" in changes:
                instrument = changes["instrument"]
                if not isinstance(instrument, str) or not instrument.strip():
                    raise ValueError("instrument must be a non-empty existing asset path")
                node["instrument"] = instrument.strip()
            if "state" in changes:
                state = changes["state"]
                if state is None:
                    node.pop("state", None)
                elif not isinstance(state, str) or not state.strip():
                    raise ValueError("state must be null or a non-empty existing file path")
                else:
                    node["state"] = state.strip()

        if "profile" in changes:
            _write_yaml(piece_path, piece_data)
        if render_data is not None:
            _write_yaml(render_path, render_data)
        piece = load_piece(self.root)
        if render_path.is_file():
            load_render_graph(self.root, piece)
        return {"type": "update_instrument", "part": part, "changes": changes}

    def _update_mix_node(self, command: dict[str, Any]) -> dict:
        path, data = self._format_two_mix_document()
        node_type, node_id, node = _mix_target(data, command)
        changes = command.get("changes")
        if not isinstance(changes, dict) or not changes:
            raise ValueError("update_mix_node.changes must be a non-empty object")
        allowed = (
            {
                "gain_db",
                "target_lufs",
                "true_peak_ceiling_db",
                "loudness_range_lu",
                "loudness_tolerance_lu",
            }
            if node_type == "master"
            else {"gain_db", "pan", "output"}
        )
        unknown = sorted(set(changes) - allowed)
        if unknown:
            raise ValueError(
                f"update_mix_node does not support {', '.join(unknown)} for {node_type}"
            )
        normalized: dict[str, Any] = {}
        for field, raw_value in changes.items():
            if field == "gain_db":
                value: Any = _mix_gain(raw_value, f"changes.{field}")
            elif field == "pan":
                value = _finite_number(raw_value, f"changes.{field}")
                if not -1 <= value <= 1:
                    raise ValueError("pan must be between -1 and 1")
            elif field == "output":
                if not isinstance(raw_value, str) or not raw_value.strip():
                    raise ValueError("output must be a non-empty bus id")
                value = raw_value.strip()
            elif field == "target_lufs":
                value = _bounded_number(raw_value, field, -70.0, 0.0)
            elif field == "true_peak_ceiling_db":
                value = _bounded_number(raw_value, field, -20.0, 0.0)
            elif field == "loudness_range_lu":
                value = _bounded_number(raw_value, field, 0.01, 70.0)
            else:
                value = _bounded_number(raw_value, field, 0.0, 10.0)
            node[field] = value
            normalized[field] = value
        _write_yaml(path, data)
        load_mix_config(self.root)
        return {
            "type": "update_mix_node",
            "node_type": node_type,
            **({"node": node_id} if node_id else {}),
            "changes": normalized,
        }

    def _edit_mix_send(self, command: dict[str, Any]) -> dict:
        path, data = self._format_two_mix_document()
        node_type, node_id, node = _mix_target(data, command, allow_master=False)
        bus = command.get("bus")
        if not isinstance(bus, str) or not bus.strip():
            raise ValueError("bus must be a non-empty bus id")
        bus = bus.strip()
        sends = node.setdefault("sends", {})
        if not isinstance(sends, dict):
            raise ValueError("mix node sends must be a mapping")
        if command["type"] == "set_mix_send":
            gain = _mix_gain(command.get("gain_db"), "gain_db")
            sends[bus] = gain
            result = {"gain_db": gain}
        else:
            if bus not in sends:
                raise ValueError(f"mix send does not exist: {node_type} {node_id} -> {bus}")
            deleted = sends.pop(bus)
            result = {"deleted_gain_db": deleted}
        _write_yaml(path, data)
        load_mix_config(self.root)
        return {
            "type": command["type"],
            "node_type": node_type,
            "node": node_id,
            "bus": bus,
            **result,
        }

    def _edit_mix_insert(self, command: dict[str, Any]) -> dict:
        path, data = self._format_two_mix_document()
        node_type, node_id, node = _mix_target(data, command)
        inserts = node.setdefault("inserts", [])
        if not isinstance(inserts, list):
            raise ValueError("mix node inserts must be a list")
        kind = str(command["type"])
        if kind == "add_mix_insert":
            processor = command.get("processor")
            if not isinstance(processor, dict):
                raise ValueError("add_mix_insert.processor must be an object")
            processor = copy.deepcopy(processor)
            insert_index = command.get("insert_index", len(inserts))
            insert_index = _integer(insert_index, "insert_index")
            if not 0 <= insert_index <= len(inserts):
                raise ValueError("insert_index is outside the insert chain")
            inserts.insert(insert_index, processor)
            result: dict[str, Any] = {
                "insert_index": insert_index,
                "processor": processor,
            }
        else:
            insert_index = _integer(command.get("insert_index"), "insert_index")
            if not 0 <= insert_index < len(inserts):
                raise ValueError("insert_index is outside the insert chain")
            if kind == "update_mix_insert":
                changes = command.get("changes")
                if not isinstance(changes, dict) or not changes or "type" in changes:
                    raise ValueError(
                        "update_mix_insert.changes must be non-empty and cannot change type"
                    )
                processor = inserts[insert_index]
                if not isinstance(processor, dict):
                    raise ValueError("authored mix processor must be an object")
                processor.update(copy.deepcopy(changes))
                result = {"insert_index": insert_index, "changes": changes}
            elif kind == "delete_mix_insert":
                deleted = inserts.pop(insert_index)
                result = {"insert_index": insert_index, "deleted": deleted}
            else:
                to_index = _integer(command.get("to_index"), "to_index")
                if not 0 <= to_index < len(inserts):
                    raise ValueError("to_index is outside the insert chain")
                processor = inserts.pop(insert_index)
                inserts.insert(to_index, processor)
                result = {"insert_index": insert_index, "to_index": to_index}
        _write_yaml(path, data)
        load_mix_config(self.root)
        return {
            "type": kind,
            "node_type": node_type,
            **({"node": node_id} if node_id else {}),
            **result,
        }

    def _format_two_mix_document(self) -> tuple[Path, dict[str, Any]]:
        path = self.root / "mix.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("format") != 2:
            raise ValueError("structured mixer commands require mix.yaml format 2")
        return path, data

    def _update_tempo(self, command: dict[str, Any]) -> dict:
        path = self.root / "piece.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        index = _integer(command.get("index", 0), "index")
        changes = command.get("changes")
        if changes is None:
            changes = {"bpm": command["bpm"]}
        if not isinstance(changes, dict) or not changes or set(changes) - {"at", "bpm"}:
            raise ValueError("update_tempo supports at and bpm")
        if not 0 <= index < len(data["tempo"]):
            raise ValueError("tempo index is invalid")
        updated = dict(data["tempo"][index])
        updated.update(changes)
        parse_anchor(str(updated["at"]))
        bpm = float(updated["bpm"])
        if not 1 <= bpm <= 999 or not 0 <= index < len(data["tempo"]):
            raise ValueError("tempo index or bpm is invalid")
        updated["bpm"] = bpm
        data["tempo"][index] = updated
        _sort_anchors(data["tempo"])
        _write_yaml(path, data)
        return {"type": "update_tempo", "index": index, "changes": changes}

    def _edit_tempo(self, command: dict[str, Any]) -> dict:
        path = self.root / "piece.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        tempo = data["tempo"]
        if command["type"] == "insert_tempo":
            entry = command.get("tempo", {"at": command.get("at"), "bpm": command.get("bpm")})
            if not isinstance(entry, dict) or set(entry) != {"at", "bpm"}:
                raise ValueError("insert_tempo requires tempo.at and tempo.bpm")
            parse_anchor(str(entry["at"]))
            bpm = float(entry["bpm"])
            if not 1 <= bpm <= 999:
                raise ValueError("bpm must be between 1 and 999")
            tempo.append({"at": str(entry["at"]), "bpm": bpm})
            _sort_anchors(tempo)
            result = {"type": "insert_tempo", "at": str(entry["at"]), "bpm": bpm}
        else:
            index = _integer(command["index"], "index")
            if not 0 <= index < len(tempo):
                raise ValueError("tempo index is invalid")
            deleted = tempo.pop(index)
            if not tempo:
                raise ValueError("a piece requires at least one tempo change")
            result = {"type": "delete_tempo", "index": index, "deleted": deleted}
        _write_yaml(path, data)
        return result

    def _edit_control(self, command: dict[str, Any]) -> dict:
        part = str(command["part"])
        path, data = self._part_document(part)
        controls = data.setdefault("controls", [])
        if not isinstance(controls, list):
            raise ValueError("part controls must be a list")
        kind = command["type"]
        if kind == "insert_control":
            raw = command.get("control")
            if not isinstance(raw, dict):
                raise ValueError("insert_control.control must be an object")
            control = copy.deepcopy(raw)
            control.setdefault("id", _new_id("ctl"))
            parsed = control_event_from_dict(dict(control))
            self._assert_profile_control(part, parsed)
            controls.append(control)
            _sort_anchors(controls)
            result = {"type": kind, "part": part, "control_id": control["id"]}
        else:
            index = _identified_index(
                controls,
                command.get("control_id"),
                command.get("control_index"),
                "control",
            )
            control = controls[index]
            control_id = control.get("id")
            if kind == "delete_control":
                controls.pop(index)
                result = {"type": kind, "part": part, "control_id": control_id}
            else:
                changes = command.get("changes")
                if not isinstance(changes, dict) or not changes or "id" in changes:
                    raise ValueError(
                        "update_control.changes must be non-empty and cannot replace id"
                    )
                updated = {**control, **copy.deepcopy(changes)}
                if updated.get("type") != control.get("type"):
                    raise ValueError(
                        "update_control cannot change control type; delete and insert instead"
                    )
                parsed = control_event_from_dict(dict(updated))
                self._assert_profile_control(part, parsed)
                controls[index] = updated
                _sort_anchors(controls)
                result = {
                    "type": kind,
                    "part": part,
                    "control_id": control_id,
                    "changes": changes,
                }
        _write_yaml(path, data)
        return result

    def _assert_profile_control(self, part_id: str, control: Any) -> None:
        piece = load_piece(self.root)
        part = next((item for item in piece.parts if item.id == part_id), None)
        if part is None:
            raise ValueError(f"unknown part: {part_id}")
        profile = piece.profiles[part.profile_id]
        if control.kind == "keyswitch" and control.keyswitch not in profile.keyswitches:
            raise ValueError(
                f"profile {profile.id} does not declare keyswitch {control.keyswitch!r}"
            )
        if control.kind in {"cc", "dynamic_ramp"} and control.controller in {1, 11}:
            supported = {
                binding.controller
                for binding in profile.performance.values()
                if binding.type == "cc" and binding.controller is not None
            }
            if control.controller not in supported:
                raise ValueError(
                    f"profile {profile.id} does not declare CC{control.controller}"
                )

    def _edit_automation(self, command: dict[str, Any]) -> dict:
        path = self.root / "automation.yaml"
        if not path.is_file():
            raise ValueError("automation.yaml does not exist")
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        lanes = data.get("lanes", [])
        lane_id = str(command["lane"])
        lane = next((item for item in lanes if str(item.get("id")) == lane_id), None)
        if lane is None:
            raise ValueError(f"automation lane does not exist: {lane_id}")
        points = lane.get("points")
        if not isinstance(points, list):
            raise ValueError("automation lane points must be a list")
        kind = command["type"]
        if kind == "insert_point":
            raw = command.get("point")
            if not isinstance(raw, dict) or not {"at", "value"} <= set(raw):
                raise ValueError("insert_point.point requires at and value")
            point = copy.deepcopy(raw)
            point.setdefault("id", _new_id("aut"))
            parse_anchor(str(point["at"]))
            points.append(point)
            _sort_anchors(points)
            result = {"type": kind, "lane": lane_id, "point_id": point["id"]}
        elif kind == "set_curve" and "point_id" not in command and "point_index" not in command:
            interpolation = str(command["curve"])
            if interpolation not in INTERPOLATIONS:
                raise ValueError("unsupported automation curve")
            lane["interpolation"] = interpolation
            result = {"type": kind, "lane": lane_id, "curve": interpolation}
        else:
            index = _identified_index(
                points,
                command.get("point_id"),
                command.get("point_index"),
                "automation point",
            )
            point_id = points[index].get("id")
            if kind == "delete_point":
                points.pop(index)
                if not points:
                    raise ValueError("an automation lane requires at least one point")
                result = {"type": kind, "lane": lane_id, "point_id": point_id}
            else:
                if kind == "move_point":
                    changes = {"at": command["at"]}
                elif kind == "set_curve":
                    changes = {"curve": command["curve"]}
                else:
                    changes = command.get("changes")
                allowed = {"at", "value", "curve", "in_value", "out_value"}
                if not isinstance(changes, dict) or not changes or set(changes) - allowed:
                    raise ValueError("automation point changes are invalid")
                updated = {**points[index], **copy.deepcopy(changes)}
                if updated.get("curve") is not None and updated["curve"] not in INTERPOLATIONS:
                    raise ValueError("unsupported automation curve")
                parse_anchor(str(updated["at"]))
                points[index] = updated
                _sort_anchors(points)
                result = {
                    "type": kind,
                    "lane": lane_id,
                    "point_id": point_id,
                    "changes": changes,
                }
        _write_yaml(path, data)
        return result

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
        _part, _measure, _voice, index, path, data, events = self._event_location(command)
        return path, data, events[index]

    def _event_location(
        self, command: dict[str, Any]
    ) -> tuple[str, int, str, int, Path, dict, list[dict[str, Any]]]:
        part = str(command["part"])
        path, data = self._part_document(part)
        event_id = command.get("event_id")
        if event_id is not None:
            matches = []
            for raw_measure, measure_data in data.get("measures", {}).items():
                for voice, events in measure_data.items():
                    for index, event in enumerate(events):
                        if event.get("id") == event_id:
                            matches.append((int(raw_measure), str(voice), index, events))
            if len(matches) != 1:
                raise ValueError(f"event_id must resolve exactly once: {event_id!r}")
            measure, voice, index, events = matches[0]
        else:
            measure = _positive_integer(command["measure"], "measure")
            voice = str(command["voice"])
            index = _integer(command["event_index"], "event_index")
            measures = data.get("measures", {})
            measure_data = measures.get(str(measure), measures.get(measure))
            if not isinstance(measure_data, dict) or not isinstance(
                measure_data.get(voice), list
            ):
                raise ValueError("voice does not exist")
            events = measure_data[voice]
        if not 0 <= index < len(events) or events[index].get("r", False):
            raise ValueError("event is not an editable note")
        return part, measure, voice, index, path, data, events

    def _voice_document(
        self, part: str, measure: int, voice: str
    ) -> tuple[Path, dict, list[dict[str, Any]]]:
        path, data = self._part_document(part)
        return path, data, self._voice_from_data(data, measure, voice)

    @staticmethod
    def _voice_from_data(data: dict, measure: int, voice: str) -> list[dict[str, Any]]:
        measures = data.get("measures", {})
        measure_data = measures.get(str(measure), measures.get(measure))
        if not isinstance(measure_data, dict) or not isinstance(measure_data.get(voice), list):
            raise ValueError("voice does not exist")
        return measure_data[voice]

    def _measure_length(self, measure: int) -> Fraction:
        return load_piece(self.root).time_at(measure).length

    def _part_document(self, part: str) -> tuple[Path, dict]:
        piece = yaml.safe_load((self.root / "piece.yaml").read_text(encoding="utf-8"))
        reference = next((item for item in piece["parts"] if str(item["id"]) == part), None)
        if reference is None:
            raise ValueError(f"unknown part: {part}")
        path = (self.root / str(reference["file"])).resolve()
        if path == self.root or self.root not in path.parents:
            raise ValueError(f"part source is outside the project: {reference['file']}")
        return path, yaml.safe_load(path.read_text(encoding="utf-8"))

    def _capture(self) -> dict[str, bytes]:
        piece_path = self.root / "piece.yaml"
        piece = yaml.safe_load(piece_path.read_text(encoding="utf-8"))
        paths = [piece_path]
        for reference in piece.get("parts", []):
            if not isinstance(reference, dict) or not isinstance(reference.get("file"), str):
                raise ValueError("piece part references must identify a source file")
            path = (self.root / reference["file"]).resolve()
            if path == self.root or self.root not in path.parents:
                raise ValueError(f"part source is outside the project: {reference['file']}")
            paths.append(path)
        paths.extend(
            path
            for name in ("mix.yaml", "automation.yaml", "performance.yaml", "render.yaml")
            if (path := self.root / name).is_file()
        )
        return {
            path.relative_to(self.root).as_posix(): path.read_bytes()
            for path in dict.fromkeys(paths)
        }

    def _record_transaction(
        self,
        operation: str,
        before: dict[str, bytes],
        after: dict[str, bytes],
        *,
        before_revision: str,
        after_revision: str,
        command_types: list[str],
    ) -> dict[str, Any]:
        transaction = {
            "schema_version": "1",
            "id": f"studio_{uuid.uuid4().hex}",
            "operation": operation,
            "created_at": datetime.now(UTC).isoformat(),
            "from_revision": before_revision,
            "to_revision": after_revision,
            "command_count": len(command_types),
            "command_types": command_types,
            "impact": _source_impact(before, after),
        }
        _write_bytes_atomic(
            self._transaction_path(),
            (json.dumps(transaction, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        )
        return transaction

    def _restore(self, state: dict[str, bytes]) -> None:
        for relative, content in state.items():
            path = (self.root / relative).resolve()
            if path == self.root or self.root not in path.parents:
                raise ValueError(f"history path is outside the project: {relative}")
            if path.is_file() and path.read_bytes() == content:
                continue
            _write_bytes_atomic(path, content)
        load_piece(self.root)

    def _history_path(self) -> Path:
        return self.root / ".ledgerline" / "history" / "studio-history.json"

    def _transaction_path(self) -> Path:
        return self.root / ".ledgerline" / "history" / "studio-last-transaction.json"

    def _load_history(self) -> tuple[list[dict[str, bytes]], list[dict[str, bytes]]]:
        path = self._history_path()
        if not path.is_file():
            return [], []
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if raw.get("schema_version") != "1":
                return [], []
            if raw.get("head_revision") != project_revision(self.root):
                return [], []
            return _decode_states(raw.get("undo", [])), _decode_states(raw.get("redo", []))
        except (OSError, ValueError, TypeError, binascii.Error):
            return [], []

    def _persist_history(self) -> None:
        path = self._history_path()
        payload = {
            "schema_version": "1",
            "limit": self.HISTORY_LIMIT,
            "head_revision": project_revision(self.root),
            "undo": _encode_states(self._undo[-self.HISTORY_LIMIT :]),
            "redo": _encode_states(self._redo[-self.HISTORY_LIMIT :]),
        }
        _write_bytes_atomic(
            path,
            (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        )

    def _history_report(self, action: str) -> dict:
        return {
            "schema_version": "1",
            "status": "ok",
            "action": action,
            "revision": project_revision(self.root),
            "can_undo": self.can_undo,
            "can_redo": self.can_redo,
        }


def _mix_target(
    data: dict[str, Any],
    command: dict[str, Any],
    *,
    allow_master: bool = True,
) -> tuple[str, str | None, dict[str, Any]]:
    node_type = command.get("node_type")
    allowed = {"track", "bus", "master"} if allow_master else {"track", "bus"}
    if node_type not in allowed:
        choices = ", ".join(sorted(allowed))
        raise ValueError(f"node_type must be one of: {choices}")
    if node_type == "master":
        if command.get("node") not in {None, "", "master"}:
            raise ValueError("master commands do not accept a node id")
        node = data.get("master")
        if not isinstance(node, dict):
            raise ValueError("mix master must be a mapping")
        return "master", None, node
    node_id = command.get("node")
    if not isinstance(node_id, str) or not node_id.strip():
        raise ValueError(f"{node_type} commands require a non-empty node id")
    node_id = node_id.strip()
    collection_name = "tracks" if node_type == "track" else "buses"
    collection = data.get(collection_name)
    if not isinstance(collection, dict) or node_id not in collection:
        raise ValueError(f"mix {node_type} does not exist: {node_id}")
    node = collection[node_id]
    if not isinstance(node, dict):
        raise ValueError(f"mix {node_type} must be a mapping: {node_id}")
    return str(node_type), node_id, node


def _source_impact(before: dict[str, bytes], after: dict[str, bytes]) -> dict[str, Any]:
    changed_names = sorted(
        name for name in set(before) | set(after) if before.get(name) != after.get(name)
    )
    files = [
        {
            "path": name,
            "before_sha256": _bytes_sha256(before.get(name)),
            "after_sha256": _bytes_sha256(after.get(name)),
        }
        for name in changed_names
    ]
    parts: set[str] = set()
    measures: set[tuple[str, int]] = set()
    aspects: set[str] = set()
    targets: set[str] = set()
    fields: set[str] = set()

    before_piece = _state_yaml(before, "piece.yaml")
    after_piece = _state_yaml(after, "piece.yaml")
    references = _part_references(before_piece) | _part_references(after_piece)
    for name in changed_names:
        old = _state_yaml(before, name)
        new = _state_yaml(after, name)
        fields.update(_diff_paths(old, new, name))
        if name == "mix.yaml":
            _mix_impact(old, new, parts, aspects, targets)
        elif name == "piece.yaml":
            _piece_impact(old, new, parts, measures, aspects, targets)
        elif name in references:
            part = references[name]
            parts.add(part)
            _part_impact(part, old, new, measures, aspects, targets)
        elif name == "automation.yaml":
            aspects.add("automation")
            targets.add("automation")
            _automation_impact(old, new, parts, measures, targets)
        elif name == "performance.yaml":
            aspects.add("expression")
            targets.add("performance")
        elif name == "render.yaml":
            _render_impact(old, new, parts, aspects, targets)
        else:
            aspects.add("source")
            targets.add(name)
    return {
        "changed": bool(changed_names),
        "files": files,
        "parts": sorted(parts),
        "measures": [
            {"part": part, "measure": measure} for part, measure in sorted(measures)
        ],
        "aspects": sorted(aspects),
        "targets": sorted(targets),
        "fields": sorted(fields),
    }


def _mix_impact(
    old: Any,
    new: Any,
    parts: set[str],
    aspects: set[str],
    targets: set[str],
) -> None:
    aspects.add("mix")
    old = old if isinstance(old, dict) else {}
    new = new if isinstance(new, dict) else {}
    for collection, label in (("tracks", "track"), ("buses", "bus")):
        old_nodes = old.get(collection, {})
        new_nodes = new.get(collection, {})
        old_nodes = old_nodes if isinstance(old_nodes, dict) else {}
        new_nodes = new_nodes if isinstance(new_nodes, dict) else {}
        for node_id in set(old_nodes) | set(new_nodes):
            if old_nodes.get(node_id) != new_nodes.get(node_id):
                targets.add(f"{label}:{node_id}")
                if label == "track":
                    parts.add(str(node_id))
    if old.get("master") != new.get("master"):
        targets.add("master")
    if old.get("reverb") != new.get("reverb"):
        targets.add("legacy-reverb")


def _piece_impact(
    old: Any,
    new: Any,
    parts: set[str],
    measures: set[tuple[str, int]],
    aspects: set[str],
    targets: set[str],
) -> None:
    old = old if isinstance(old, dict) else {}
    new = new if isinstance(new, dict) else {}
    if old.get("tempo") != new.get("tempo"):
        aspects.add("tempo")
        targets.add("tempo")
        for entry in [*old.get("tempo", []), *new.get("tempo", [])]:
            if isinstance(entry, dict) and isinstance(entry.get("at"), str):
                raw_measure = entry["at"].split(":", 1)[0]
                if raw_measure.isdigit():
                    measures.add(("*", int(raw_measure)))
    if old.get("time") != new.get("time"):
        aspects.add("meter")
        targets.add("time-signature")
    if old.get("key") != new.get("key"):
        aspects.add("key")
        targets.add("key-signature")
    old_parts = {
        str(item.get("id")): item
        for item in old.get("parts", [])
        if isinstance(item, dict) and item.get("id") is not None
    }
    new_parts = {
        str(item.get("id")): item
        for item in new.get("parts", [])
        if isinstance(item, dict) and item.get("id") is not None
    }
    for part in set(old_parts) | set(new_parts):
        before = old_parts.get(part, {})
        after = new_parts.get(part, {})
        if before.get("profile") != after.get("profile"):
            parts.add(part)
            aspects.add("instrument")
            targets.add(f"part:{part}:configuration")
    structural_change = any(
        old.get(key) != new.get(key) for key in ("format", "title", "measures")
    )
    old_order = [
        str(item.get("id"))
        for item in old.get("parts", [])
        if isinstance(item, dict) and item.get("id") is not None
    ]
    new_order = [
        str(item.get("id"))
        for item in new.get("parts", [])
        if isinstance(item, dict) and item.get("id") is not None
    ]
    structural_change = structural_change or old_order != new_order
    for part in set(old_parts) | set(new_parts):
        before = old_parts.get(part)
        after = new_parts.get(part)
        if before is None or after is None:
            structural_change = True
            continue
        if any(before.get(field) != after.get(field) for field in ("id", "name", "file")):
            structural_change = True
    if structural_change:
        aspects.add("project")
        targets.add("piece")


def _render_impact(
    old: Any,
    new: Any,
    parts: set[str],
    aspects: set[str],
    targets: set[str],
) -> None:
    aspects.add("render")
    targets.add("render")
    old = old if isinstance(old, dict) else {}
    new = new if isinstance(new, dict) else {}
    old_nodes = {
        str(item.get("part")): item
        for item in old.get("nodes", [])
        if isinstance(item, dict) and item.get("part") is not None
    }
    new_nodes = {
        str(item.get("part")): item
        for item in new.get("nodes", [])
        if isinstance(item, dict) and item.get("part") is not None
    }
    for part in set(old_nodes) | set(new_nodes):
        before = old_nodes.get(part, {})
        after = new_nodes.get(part, {})
        if any(before.get(field) != after.get(field) for field in ("instrument", "state")):
            parts.add(part)
            aspects.add("instrument")
            targets.add(f"part:{part}:instrument")


def _part_impact(
    part: str,
    old: Any,
    new: Any,
    measures: set[tuple[str, int]],
    aspects: set[str],
    targets: set[str],
) -> None:
    old = old if isinstance(old, dict) else {}
    new = new if isinstance(new, dict) else {}
    old_measures = _measure_mapping(old.get("measures"))
    new_measures = _measure_mapping(new.get("measures"))
    for number in set(old_measures) | set(new_measures):
        old_measure = old_measures.get(number)
        new_measure = new_measures.get(number)
        if old_measure != new_measure:
            measures.add((part, number))
            targets.add(f"part:{part}:measure:{number}")
            aspects.update(_measure_aspects(old_measure, new_measure))
    if old.get("controls") != new.get("controls"):
        aspects.add("expression")
        targets.add(f"part:{part}:controls")
    ignored = {"measures", "controls", "format", "part"}
    if any(old.get(key) != new.get(key) for key in set(old) | set(new) if key not in ignored):
        aspects.add("instrument")
        targets.add(f"part:{part}:configuration")


def _measure_aspects(old: Any, new: Any) -> set[str]:
    result: set[str] = set()
    old_events = _voice_events(old)
    new_events = _voice_events(new)
    projections = {
        "pitch": ("p",),
        "rhythm": ("d", "r", "tie", "tuplet", "grace", "slur"),
        "dynamics": ("dyn", "vel"),
        "articulation": ("art",),
        "expression": ("expr", "pitch_cents", "gestures"),
        "instrument": ("staff",),
    }
    for aspect, keys in projections.items():
        if _event_projection(old_events, keys) != _event_projection(new_events, keys):
            result.add(aspect)
    if old_events != new_events and not result:
        result.add("identity")
    return result


def _automation_impact(
    old: Any,
    new: Any,
    parts: set[str],
    measures: set[tuple[str, int]],
    targets: set[str],
) -> None:
    for document in (old, new):
        if not isinstance(document, dict):
            continue
        for lane in document.get("lanes", []):
            if not isinstance(lane, dict):
                continue
            target = str(lane.get("target", ""))
            targets.add(f"automation:{lane.get('id', target)}")
            part = target.split(".", 2)[1] if target.startswith("parts.") else "*"
            if part != "*":
                parts.add(part)
            for point in lane.get("points", []):
                if isinstance(point, dict) and isinstance(point.get("at"), str):
                    raw_measure = point["at"].split(":", 1)[0]
                    if raw_measure.isdigit():
                        measures.add((part, int(raw_measure)))


def _state_yaml(state: dict[str, bytes], name: str) -> Any:
    raw = state.get(name)
    if raw is None:
        return None
    try:
        return yaml.safe_load(raw.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError):
        return None


def _part_references(piece: Any) -> dict[str, str]:
    if not isinstance(piece, dict):
        return {}
    result = {}
    for item in piece.get("parts", []):
        if isinstance(item, dict) and item.get("file") and item.get("id"):
            result[str(item["file"]).replace("\\", "/")] = str(item["id"])
    return result


def _measure_mapping(raw: Any) -> dict[int, Any]:
    if not isinstance(raw, dict):
        return {}
    result = {}
    for key, value in raw.items():
        try:
            result[int(key)] = value
        except (TypeError, ValueError):
            continue
    return result


def _voice_events(measure: Any) -> list[tuple[str, Any]]:
    if not isinstance(measure, dict):
        return []
    return [(str(voice), event) for voice, events in sorted(measure.items()) for event in events]


def _event_projection(events: list[tuple[str, Any]], keys: tuple[str, ...]) -> list[Any]:
    return [
        (voice, {key: event.get(key) for key in keys if key in event})
        if isinstance(event, dict)
        else (voice, event)
        for voice, event in events
    ]


def _diff_paths(old: Any, new: Any, prefix: str) -> set[str]:
    if old == new:
        return set()
    if isinstance(old, dict) and isinstance(new, dict):
        result: set[str] = set()
        for key in set(old) | set(new):
            result.update(_diff_paths(old.get(key), new.get(key), f"{prefix}.{key}"))
        return result
    if isinstance(old, list) and isinstance(new, list):
        result = set()
        for index in range(max(len(old), len(new))):
            before = old[index] if index < len(old) else None
            after = new[index] if index < len(new) else None
            result.update(_diff_paths(before, after, f"{prefix}[{index}]"))
        return result
    return {prefix}


def _bytes_sha256(content: bytes | None) -> str | None:
    return hashlib.sha256(content).hexdigest() if content is not None else None


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field} must be finite")
    return result


def _bounded_number(value: Any, field: str, minimum: float, maximum: float) -> float:
    result = _finite_number(value, field)
    if not minimum <= result <= maximum:
        raise ValueError(f"{field} must be between {minimum:g} and {maximum:g}")
    return result


def _mix_gain(value: Any, field: str) -> float:
    return _bounded_number(value, field, -120.0, 24.0)


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


def _indexed_note_placements(
    events: list[dict[str, Any]],
) -> list[tuple[int, tuple[Fraction, dict[str, Any]]]]:
    result = []
    cursor = Fraction(0)
    for index, event in enumerate(events):
        duration = parse_duration(str(event["d"]))
        if not event.get("r", False):
            result.append((index, (cursor, copy.deepcopy(event))))
        cursor += duration
    return result


def _note_placements(events: list[dict[str, Any]]) -> list[tuple[Fraction, dict[str, Any]]]:
    return [item for _index, item in _indexed_note_placements(events)]


def _optional_offset(command: dict[str, Any], *, required: bool = False) -> Fraction | None:
    raw = command.get("target_offset_whole", command.get("at_offset_whole"))
    if raw is None:
        if required:
            raise ValueError("target_offset_whole is required")
        return None
    try:
        offset = Fraction(str(raw))
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError("target_offset_whole must be a finite rational number") from exc
    if offset < 0:
        raise ValueError("target_offset_whole cannot be negative")
    return offset


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _identified_index(
    items: list[dict[str, Any]],
    authored_id: object,
    raw_index: object,
    label: str,
) -> int:
    if authored_id is not None:
        matches = [index for index, item in enumerate(items) if item.get("id") == authored_id]
        if len(matches) != 1:
            raise ValueError(f"{label} id must resolve exactly once: {authored_id!r}")
        return matches[0]
    index = _integer(raw_index, f"{label.replace(' ', '_')}_index")
    if not 0 <= index < len(items):
        raise ValueError(f"{label} index is outside the list")
    return index


def _sort_anchors(items: list[dict[str, Any]]) -> None:
    items.sort(key=lambda item: parse_anchor(str(item["at"])))


def _encode_states(states: list[dict[str, bytes]]) -> list[dict[str, str]]:
    return [
        {name: base64.b64encode(content).decode("ascii") for name, content in state.items()}
        for state in states
    ]


def _decode_states(raw: object) -> list[dict[str, bytes]]:
    if not isinstance(raw, list):
        return []
    states = []
    for raw_state in raw:
        if not isinstance(raw_state, dict):
            return []
        states.append(
            {
                str(name): base64.b64decode(str(content), validate=True)
                for name, content in raw_state.items()
            }
        )
    return states


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
    content = yaml.safe_dump(data, sort_keys=False, allow_unicode=True).encode("utf-8")
    _write_bytes_atomic(path, content)


def _write_bytes_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
    ) as handle:
        temporary = Path(handle.name)
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    return value


def _positive_integer(value: Any, field: str) -> int:
    result = _integer(value, field)
    if result < 1:
        raise ValueError(f"{field} must be positive")
    return result
