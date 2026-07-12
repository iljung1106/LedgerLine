from __future__ import annotations

import hashlib
import json
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ledgerline.studio_edits import StudioSession

AUTONOMY = {"review", "safe-auto"}
SAFE_ACTIONS = {
    "update_note",
    "move_event",
    "resize_event",
    "update_mix",
    "update_tempo",
    "transpose_range",
    "scale_velocity_range",
    "set_articulation_range",
}


def create_delegation(
    project: str | Path,
    goal: str,
    *,
    autonomy: str = "review",
    context: str = "",
    constraints: list[str] | None = None,
) -> dict[str, Any]:
    root = Path(project).resolve()
    if autonomy not in AUTONOMY:
        raise ValueError("autonomy must be review or safe-auto")
    if not goal.strip():
        raise ValueError("delegation goal must be non-empty")
    now = datetime.now(UTC)
    task_id = f"{now.strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(3)}"
    task = {
        "schema_version": "1",
        "id": task_id,
        "status": "pending",
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "goal": goal.strip(),
        "context": context.strip(),
        "constraints": constraints or [],
        "autonomy": autonomy,
        "proposal": None,
        "approval_token": None,
        "result": None,
        "agent_contract": {
            "source_of_truth": "LedgerLine authored YAML",
            "must_validate": True,
            "must_explain_changes": True,
            "must_not": [
                "download assets without a setup plan and user consent",
                "substitute instruments silently",
                "overwrite delivery files",
                "claim aesthetic success without a listening checkpoint",
            ],
        },
    }
    path = _task_path(root, task_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write(path, task)
    return {
        **task,
        "path": str(path),
        "agent_command": f"ledgerline delegate next \"{root}\" --json",
    }


def list_delegations(project: str | Path, *, status: str | None = None) -> dict[str, Any]:
    root = Path(project).resolve()
    directory = root / ".ledgerline" / "delegations"
    tasks = []
    if directory.is_dir():
        for path in sorted(directory.glob("*.json"), reverse=True):
            task = _read(path)
            if status is None or task.get("status") == status:
                tasks.append(task)
    return {"schema_version": "1", "status": "ok", "project": str(root), "tasks": tasks}


def next_delegation(project: str | Path) -> dict[str, Any]:
    tasks = list_delegations(project, status="pending")["tasks"]
    if not tasks:
        return {"schema_version": "1", "status": "empty", "task": None}
    return {"schema_version": "1", "status": "ok", "task": tasks[-1]}


def show_delegation(project: str | Path, task_id: str) -> dict[str, Any]:
    return _read(_task_path(Path(project).resolve(), task_id))


def propose_delegation(
    project: str | Path, task_id: str, proposal: str | Path | dict[str, Any]
) -> dict[str, Any]:
    root = Path(project).resolve()
    path = _task_path(root, task_id)
    task = _read(path)
    if task["status"] not in {"pending", "needs-direction"}:
        raise ValueError("delegation is not waiting for an agent proposal")
    if isinstance(proposal, dict):
        raw = proposal
    else:
        raw = json.loads(Path(proposal).resolve(strict=True).read_text(encoding="utf-8"))
    _validate_proposal(raw)
    task["proposal"] = raw
    task["status"] = "proposed"
    task["updated_at"] = datetime.now(UTC).isoformat()
    task["approval_token"] = _proposal_token(raw)
    _write(path, task)
    if task["autonomy"] == "safe-auto":
        return apply_delegation(root, task_id)
    return task


def apply_delegation(
    project: str | Path,
    task_id: str,
    *,
    token: str | None = None,
    session: StudioSession | None = None,
) -> dict[str, Any]:
    root = Path(project).resolve()
    path = _task_path(root, task_id)
    task = _read(path)
    if task["status"] != "proposed" or not task.get("proposal"):
        raise ValueError("delegation has no applicable proposal")
    expected = task["approval_token"]
    if task["autonomy"] == "review" and token != expected:
        raise ValueError("delegation approval token is required")
    if token is not None and token != expected:
        raise ValueError("delegation approval token does not match")
    actions = task["proposal"]["actions"]
    if task["autonomy"] == "safe-auto" and any(
        item.get("type") not in SAFE_ACTIONS for item in actions
    ):
        raise ValueError("safe-auto proposal contains an unsafe action")
    active_session = session or StudioSession(root)
    report = active_session.apply(actions)
    task["status"] = "applied"
    task["updated_at"] = datetime.now(UTC).isoformat()
    task["result"] = report
    _write(path, task)
    return task


def reject_delegation(project: str | Path, task_id: str, reason: str) -> dict[str, Any]:
    root = Path(project).resolve()
    path = _task_path(root, task_id)
    task = _read(path)
    task["status"] = "rejected"
    task["updated_at"] = datetime.now(UTC).isoformat()
    task["result"] = {"reason": reason.strip() or "rejected by user"}
    _write(path, task)
    return task


def _validate_proposal(raw: Any) -> None:
    if not isinstance(raw, dict):
        raise ValueError("proposal must be an object")
    unknown = set(raw) - {"summary", "reasoning", "actions", "listening_check", "questions"}
    if unknown:
        raise ValueError(f"proposal has unknown fields: {', '.join(sorted(unknown))}")
    if not isinstance(raw.get("summary"), str) or not raw["summary"].strip():
        raise ValueError("proposal summary is required")
    actions = raw.get("actions")
    if not isinstance(actions, list) or not actions:
        raise ValueError("proposal actions must be a non-empty list")
    if any(not isinstance(item, dict) or item.get("type") not in SAFE_ACTIONS for item in actions):
        raise ValueError("proposal contains an unsupported action")
    questions = raw.get("questions", [])
    if not isinstance(questions, list) or any(not isinstance(item, str) for item in questions):
        raise ValueError("proposal questions must be a string list")


def _proposal_token(proposal: dict[str, Any]) -> str:
    canonical = json.dumps(proposal, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()


def _task_path(root: Path, task_id: str) -> Path:
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
    if not task_id or any(character not in allowed for character in task_id):
        raise ValueError("delegation id is invalid")
    return root / ".ledgerline" / "delegations" / f"{task_id}.json"


def _read(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("delegation document must be an object")
    return raw


def _write(path: Path, task: dict[str, Any]) -> None:
    path.write_text(json.dumps(task, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
