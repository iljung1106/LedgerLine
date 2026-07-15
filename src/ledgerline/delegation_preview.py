from __future__ import annotations

import copy
import difflib
import os
import secrets
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from ledgerline.build_state import authored_revision
from ledgerline.project import load_piece
from ledgerline.studio_edits import StudioSession
from ledgerline.studio_model import _notes
from ledgerline.timeline import Timeline

DIFF_MAX_FILES = 12
DIFF_MAX_LINES = 400
DIFF_MAX_BYTES = 64 * 1024
_EDITABLE_OPTIONAL_FILES = (
    "mix.yaml",
    "automation.yaml",
    "performance.yaml",
    "render.yaml",
)
_PREVIEW_DEPENDENCY_FILES = ("motifs.yaml", "brief.yaml")
_RENDER_DEPENDENCY_MAX_FILES = 20_000
_RENDER_DEPENDENCY_MAX_DEPTH = 32


def prepare_preview_actions(
    project: str | Path, actions: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Freeze IDs that Studio would otherwise generate separately in preview and apply."""

    root = Path(project).resolve()
    prepared = copy.deepcopy(actions)
    used = _authored_ids(root)
    for action in prepared:
        if not isinstance(action, dict):
            continue
        kind = action.get("type")
        if kind == "insert_event":
            event = action.get("event")
            if isinstance(event, dict) and not event.get("r", False):
                event.setdefault("id", _fresh_id("evt", used))
        elif kind == "replace_measure_voice":
            events = action.get("events")
            if isinstance(events, list):
                for event in events:
                    if isinstance(event, dict) and not event.get("r", False):
                        event.setdefault("id", _fresh_id("evt", used))
        elif kind == "duplicate_event":
            action.setdefault("new_event_id", _fresh_id("evt", used))
        elif kind == "insert_control":
            control = action.get("control")
            if isinstance(control, dict):
                control.setdefault("id", _fresh_id("ctl", used))
        elif kind == "insert_point":
            point = action.get("point")
            if isinstance(point, dict):
                point.setdefault("id", _fresh_id("aut", used))
    return prepared


def build_proposal_preview(
    project: str | Path,
    actions: list[dict[str, Any]],
    *,
    base_revision: str,
) -> dict[str, Any]:
    """Apply Studio commands to a source-only temporary project and describe the result."""

    root = Path(project).resolve()
    if authored_revision(root) != base_revision:
        raise ValueError("project changed before delegation preview")
    if not actions:
        return _empty_preview(base_revision)

    before_sources = _capture_studio_sources(root)
    before_notes = _score_notes(root)
    with tempfile.TemporaryDirectory(
        prefix=f".{root.name}.ledgerline-preview-", dir=root.parent
    ) as directory:
        preview_root = Path(directory) / "project"
        preview_root.mkdir()
        _copy_preview_project(root, preview_root, before_sources, actions)
        preview_base = authored_revision(preview_root)
        if preview_base != base_revision:
            raise ValueError("isolated preview does not contain the exact authored input set")
        report = StudioSession(preview_root).apply(actions, revision=preview_base)
        after_sources = _capture_studio_sources(preview_root)
        after_notes = _score_notes(preview_root)

    if authored_revision(root) != base_revision:
        raise ValueError("project changed while delegation preview was generated")
    impact = copy.deepcopy(report["transaction"]["impact"])
    impact["counts"] = {
        name: len(impact[name])
        for name in ("files", "parts", "measures", "aspects", "targets", "fields")
    }
    score_diff = _score_diff(before_notes, after_notes)
    return {
        "schema_version": "1",
        "status": "ready",
        "base_revision": base_revision,
        "result_revision": report["revision"],
        "command_count": len(actions),
        "command_types": [str(action.get("type", "")) for action in actions],
        "validation": {
            "status": "ok",
            "contract": "StudioSession.apply",
            "compiled": True,
        },
        "impact": impact,
        "yaml_diff": _bounded_unified_yaml_diff(before_sources, after_sources),
        "score_diff": score_diff,
    }


def expected_preview_impact(preview: dict[str, Any]) -> dict[str, Any]:
    """Return the exact Studio transaction impact, excluding display-only counts."""

    impact = preview.get("impact")
    if not isinstance(impact, dict):
        raise ValueError("delegation proposal preview impact is invalid")
    required = {"changed", "files", "parts", "measures", "aspects", "targets", "fields"}
    if not required <= set(impact):
        raise ValueError("delegation proposal preview impact is incomplete")
    return {name: copy.deepcopy(impact[name]) for name in required}


def _empty_preview(base_revision: str) -> dict[str, Any]:
    impact: dict[str, Any] = {
        "changed": False,
        "files": [],
        "parts": [],
        "measures": [],
        "aspects": [],
        "targets": [],
        "fields": [],
    }
    impact["counts"] = {
        name: 0 for name in ("files", "parts", "measures", "aspects", "targets", "fields")
    }
    return {
        "schema_version": "1",
        "status": "no-actions",
        "base_revision": base_revision,
        "result_revision": base_revision,
        "command_count": 0,
        "command_types": [],
        "validation": {
            "status": "not-run",
            "contract": "StudioSession.apply",
            "compiled": False,
        },
        "impact": impact,
        "yaml_diff": {
            "format": "unified-yaml",
            "text": "",
            "files": [],
            "included_files": [],
            "omitted_files": [],
            "truncated": False,
            "line_count": 0,
            "byte_count": 0,
            "limits": _diff_limits(),
        },
        "score_diff": {
            "identity": {
                "scheme": "authored-event-id+pitch-index",
                "complete": True,
                "fallback_count": 0,
            },
            "added": [],
            "removed": [],
            "changed": [],
            "counts": {"added": 0, "removed": 0, "changed": 0, "total": 0},
        },
    }


def _capture_studio_sources(root: Path) -> dict[str, bytes]:
    piece_path = root / "piece.yaml"
    document = yaml.safe_load(piece_path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError("piece.yaml must be a mapping")
    paths = [piece_path]
    for reference in document.get("parts", []):
        if not isinstance(reference, dict) or not isinstance(reference.get("file"), str):
            raise ValueError("piece part references must identify a source file")
        paths.append(_source_path(root, reference["file"], "part source"))
    paths.extend(
        path
        for name in _EDITABLE_OPTIONAL_FILES
        if (path := root / name).is_file()
    )
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in dict.fromkeys(paths)
    }


def _copy_preview_project(
    source_root: Path,
    preview_root: Path,
    editable_sources: dict[str, bytes],
    actions: list[dict[str, Any]],
) -> None:
    # Authored YAML is copied by value because Studio commands rewrite these files.
    for relative, content in editable_sources.items():
        _write_preview_file(preview_root, relative, content)
    for directory in ("parts", "profiles"):
        for path in sorted((source_root / directory).glob("*.yaml")):
            resolved = _contained_source(source_root, path, f"{directory} source")
            relative = resolved.relative_to(source_root).as_posix()
            if relative not in editable_sources:
                _write_preview_file(preview_root, relative, resolved.read_bytes())
    for name in _PREVIEW_DEPENDENCY_FILES:
        path = source_root / name
        if path.is_file():
            resolved = _contained_source(source_root, path, "preview dependency")
            _write_preview_file(
                preview_root,
                resolved.relative_to(source_root).as_posix(),
                resolved.read_bytes(),
            )
    _link_render_dependencies(source_root, preview_root, actions)


def _link_render_dependencies(
    source_root: Path,
    preview_root: Path,
    actions: list[dict[str, Any]],
) -> None:
    """Expose existing render assets to validation without cloning sample libraries.

    Only paths already named by the authored render graph or an explicit instrument edit are
    linked. Studio never writes these dependency paths; editable YAML above remains an
    independent byte-for-byte copy.
    """

    render_path = source_root / "render.yaml"
    if not render_path.is_file():
        return
    document = yaml.safe_load(render_path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or not isinstance(document.get("nodes"), list):
        return
    dependencies: list[str] = []
    for node in document["nodes"]:
        if not isinstance(node, dict):
            continue
        for field in ("executable", "instrument", "state"):
            raw = node.get(field)
            if isinstance(raw, str) and raw:
                dependencies.append(raw)
    for action in actions:
        if not isinstance(action, dict) or action.get("type") != "update_instrument":
            continue
        changes = action.get("changes")
        if not isinstance(changes, dict):
            continue
        for field in ("instrument", "state"):
            raw = changes.get(field)
            if isinstance(raw, str) and raw:
                dependencies.append(raw)
    budget = {"files": 0}
    for raw in dict.fromkeys(dependencies):
        if Path(raw).expanduser().is_absolute():
            continue
        source = (source_root / raw).resolve(strict=True)
        target = (preview_root / raw).resolve()
        sandbox = preview_root.parent.resolve()
        if target == sandbox or sandbox not in target.parents:
            raise ValueError(
                f"relative render dependency escapes the preview sandbox: {raw}"
            )
        _link_dependency(source, target, budget)


def _link_dependency(source: Path, target: Path, budget: dict[str, int]) -> None:
    if source.is_file():
        if target.exists():
            return
        _consume_dependency_file_budget(budget, source)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(source, target)
        except OSError as exc:
            raise ValueError(
                f"cannot hardlink render dependency for isolated preview: {source}"
            ) from exc
        return
    if not source.is_dir():
        raise ValueError(f"render dependency is neither a file nor directory: {source}")
    if target.exists() and not target.is_dir():
        raise ValueError(f"render dependency target collides with a file: {target}")
    target.mkdir(parents=True, exist_ok=True)
    for current, directories, files in os.walk(source, followlinks=False):
        current_path = Path(current)
        relative_root = current_path.relative_to(source)
        if len(relative_root.parts) > _RENDER_DEPENDENCY_MAX_DEPTH:
            raise ValueError(
                "render dependency directory exceeds the isolated preview depth limit: "
                f"{source}"
            )
        directories.sort()
        files.sort()
        for name in directories:
            child = current_path / name
            if child.is_symlink():
                raise ValueError(
                    f"render dependency directory symlinks are unsupported in preview: {child}"
                )
            (target / relative_root / name).mkdir(parents=True, exist_ok=True)
        for name in files:
            child = current_path / name
            if child.is_symlink() or not child.is_file():
                raise ValueError(
                    f"render dependency contains an unsupported entry: {child}"
                )
            destination = target / relative_root / name
            if destination.exists():
                continue
            _consume_dependency_file_budget(budget, child)
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(child, destination)
            except OSError as exc:
                raise ValueError(
                    f"cannot hardlink render dependency for isolated preview: {child}"
                ) from exc


def _consume_dependency_file_budget(budget: dict[str, int], source: Path) -> None:
    budget["files"] += 1
    if budget["files"] > _RENDER_DEPENDENCY_MAX_FILES:
        raise ValueError(
            "render dependency file count exceeds the isolated preview limit "
            f"({_RENDER_DEPENDENCY_MAX_FILES}): {source}"
        )


def _write_preview_file(root: Path, relative: str, content: bytes) -> None:
    pure = PurePosixPath(relative)
    if pure.is_absolute() or not pure.parts or any(part in {"", ".", ".."} for part in pure.parts):
        raise ValueError(f"preview source path is unsafe: {relative}")
    target = root.joinpath(*pure.parts).resolve()
    if root != target and root not in target.parents:
        raise ValueError(f"preview source path escapes the temporary project: {relative}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)


def _source_path(root: Path, relative: str, label: str) -> Path:
    return _contained_source(root, root / relative, label)


def _contained_source(root: Path, path: Path, label: str) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"{label} does not exist: {path}") from exc
    if resolved == root or root not in resolved.parents or not resolved.is_file():
        raise ValueError(f"{label} is outside the project: {path}")
    return resolved


def _authored_ids(root: Path) -> set[str]:
    piece = load_piece(root)
    result = {
        item.id
        for part in piece.parts
        for item in (
            *(
                event
                for measure in part.measures.values()
                for events in measure.voices.values()
                for event in events
            ),
            *part.controls,
        )
        if item.id
    }
    automation = root / "automation.yaml"
    if automation.is_file():
        document = yaml.safe_load(automation.read_text(encoding="utf-8"))
        if isinstance(document, dict):
            for lane in document.get("lanes", []):
                if not isinstance(lane, dict):
                    continue
                for point in lane.get("points", []):
                    if isinstance(point, dict) and isinstance(point.get("id"), str):
                        result.add(point["id"])
    return result


def _fresh_id(prefix: str, used: set[str]) -> str:
    while True:
        candidate = f"{prefix}_{secrets.token_hex(16)}"
        if candidate not in used:
            used.add(candidate)
            return candidate


def _score_notes(root: Path) -> dict[str, dict[str, Any]]:
    piece = load_piece(root)
    notes = _notes(piece, Timeline(piece))
    return {str(note["id"]): _note_projection(note) for note in notes}


def _note_projection(note: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(note["id"]),
        "event_id": note.get("event_id"),
        "pitch_index": int(note["pitch_index"]),
        "identity_source": "authored-event-id" if note.get("event_id") else "legacy-location",
        "part": str(note["part"]),
        "pitch": {
            "written": str(note["written_pitch"]),
            "midi": int(note["pitch"]),
        },
        "timing": {
            "measure": int(note["measure"]),
            "voice": str(note["voice"]),
            "start_tick": int(note["start_tick"]),
            "end_tick": int(note["end_tick"]),
            "start_seconds": float(note["start_seconds"]),
            "end_seconds": float(note["end_seconds"]),
            "duration": str(note["duration"]),
        },
        "velocity": int(note["velocity"]),
        "articulation": note.get("articulation"),
    }


def _score_diff(
    before: dict[str, dict[str, Any]], after: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    before_ids = set(before)
    after_ids = set(after)
    added = [after[note_id] for note_id in sorted(after_ids - before_ids)]
    removed = [before[note_id] for note_id in sorted(before_ids - after_ids)]
    changed = []
    for note_id in sorted(before_ids & after_ids):
        old = before[note_id]
        new = after[note_id]
        changed_fields = [
            name
            for name in ("pitch", "timing", "velocity", "articulation")
            if old[name] != new[name]
        ]
        if changed_fields:
            changed.append(
                {
                    "id": note_id,
                    "event_id": new.get("event_id") or old.get("event_id"),
                    "pitch_index": new["pitch_index"],
                    "part": new["part"],
                    "changed_fields": changed_fields,
                    "before": {name: old[name] for name in changed_fields},
                    "after": {name: new[name] for name in changed_fields},
                }
            )
    affected = [*added, *removed, *changed]
    fallback_count = sum(not item.get("event_id") for item in affected)
    counts = {
        "added": len(added),
        "removed": len(removed),
        "changed": len(changed),
    }
    counts["total"] = sum(counts.values())
    return {
        "identity": {
            "scheme": "authored-event-id+pitch-index",
            "complete": fallback_count == 0,
            "fallback_count": fallback_count,
            "fallback_scheme": "part+measure+voice+event-index+pitch-index",
        },
        "added": added,
        "removed": removed,
        "changed": changed,
        "counts": counts,
    }


def _bounded_unified_yaml_diff(
    before: dict[str, bytes], after: dict[str, bytes]
) -> dict[str, Any]:
    changed_files = sorted(
        name for name in set(before) | set(after) if before.get(name) != after.get(name)
    )
    lines: list[str] = []
    included: list[str] = []
    byte_count = 0
    truncated = False
    truncated_at: str | None = None
    for file_index, name in enumerate(changed_files):
        if file_index >= DIFF_MAX_FILES:
            truncated = True
            truncated_at = name
            break
        included.append(name)
        old_lines = _decode_yaml(before.get(name)).splitlines()
        new_lines = _decode_yaml(after.get(name)).splitlines()
        for line in difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=f"a/{name}",
            tofile=f"b/{name}",
            n=3,
            lineterm="",
        ):
            encoded = (line + "\n").encode("utf-8")
            if len(lines) >= DIFF_MAX_LINES or byte_count + len(encoded) > DIFF_MAX_BYTES:
                truncated = True
                truncated_at = name
                break
            lines.append(line)
            byte_count += len(encoded)
        if truncated:
            break
    if truncated:
        marker = "... [LedgerLine unified YAML diff truncated; impact metadata remains exact]"
        marker_bytes = (marker + "\n").encode("utf-8")
        while lines and (
            len(lines) >= DIFF_MAX_LINES or byte_count + len(marker_bytes) > DIFF_MAX_BYTES
        ):
            removed = lines.pop()
            byte_count -= len((removed + "\n").encode("utf-8"))
        if len(marker_bytes) <= DIFF_MAX_BYTES:
            lines.append(marker)
            byte_count += len(marker_bytes)
    text = "".join(f"{line}\n" for line in lines)
    return {
        "format": "unified-yaml",
        "text": text,
        "files": changed_files,
        "included_files": included,
        "omitted_files": [name for name in changed_files if name not in included],
        "truncated": truncated,
        "truncated_at_file": truncated_at,
        "line_count": len(lines),
        "byte_count": len(text.encode("utf-8")),
        "limits": _diff_limits(),
    }


def _decode_yaml(content: bytes | None) -> str:
    return "" if content is None else content.decode("utf-8")


def _diff_limits() -> dict[str, int]:
    return {
        "max_files": DIFF_MAX_FILES,
        "max_lines": DIFF_MAX_LINES,
        "max_bytes": DIFF_MAX_BYTES,
        "context_lines": 3,
    }
