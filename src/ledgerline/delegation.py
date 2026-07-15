from __future__ import annotations

import copy
import hashlib
import json
import os
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from ledgerline.brief import validate_edit_actions
from ledgerline.build_state import authored_revision, build_state, file_sha256
from ledgerline.delegation_preview import (
    build_proposal_preview,
    expected_preview_impact,
    prepare_preview_actions,
)
from ledgerline.studio_edits import StudioSession

AUTONOMY = {"review", "safe-auto"}
SAFE_ACTIONS = {
    "insert_event",
    "delete_event",
    "update_event",
    "duplicate_event",
    "replace_measure_voice",
    "update_instrument",
    "update_note",
    "move_event",
    "resize_event",
    "insert_control",
    "update_control",
    "delete_control",
    "insert_tempo",
    "delete_tempo",
    "insert_point",
    "update_point",
    "move_point",
    "delete_point",
    "set_curve",
    "update_mix",
    "update_mix_node",
    "set_mix_send",
    "delete_mix_send",
    "add_mix_insert",
    "update_mix_insert",
    "delete_mix_insert",
    "reorder_mix_insert",
    "update_tempo",
    "transpose_range",
    "scale_velocity_range",
    "set_articulation_range",
}
SAFE_AUTO_ACTIONS = {"update_note", "update_mix", "scale_velocity_range", "set_articulation_range"}


class BuildCoordinator(Protocol):
    def submit(
        self,
        kind: str,
        payload: dict[str, Any] | None = None,
        *,
        coalesce: bool = True,
    ) -> dict[str, Any]: ...

    def get(self, job_id: str) -> dict[str, Any]: ...


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
        "base_revision": authored_revision(root),
        "answers": [],
        "proposal": None,
        "proposal_preview": None,
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
            task = reconcile_delegation(root, path.stem)
            if status is None or task.get("status") == status:
                tasks.append(task)
    return {"schema_version": "1", "status": "ok", "project": str(root), "tasks": tasks}


def next_delegation(project: str | Path) -> dict[str, Any]:
    tasks = list_delegations(project, status="pending")["tasks"]
    if not tasks:
        return {"schema_version": "1", "status": "empty", "task": None}
    return {"schema_version": "1", "status": "ok", "task": tasks[-1]}


def show_delegation(project: str | Path, task_id: str) -> dict[str, Any]:
    return reconcile_delegation(project, task_id)


def propose_delegation(
    project: str | Path, task_id: str, proposal: str | Path | dict[str, Any]
) -> dict[str, Any]:
    root = Path(project).resolve()
    path = _task_path(root, task_id)
    task = _read(path)
    if task["status"] not in {"pending", "needs-direction"}:
        raise ValueError("delegation is not waiting for an agent proposal")
    if isinstance(proposal, dict):
        raw = copy.deepcopy(proposal)
    else:
        raw = json.loads(Path(proposal).resolve(strict=True).read_text(encoding="utf-8"))
    raw.setdefault("base_revision", task["base_revision"])
    _validate_proposal(raw)
    current_revision = authored_revision(root)
    if raw["base_revision"] != task["base_revision"] or current_revision != task["base_revision"]:
        raise ValueError(
            "delegation project changed; inspect the current revision and propose again"
        )
    invariant_violations = validate_edit_actions(root, raw["actions"])
    if invariant_violations:
        raise ValueError("proposal violates protected material: " + "; ".join(invariant_violations))
    raw["actions"] = prepare_preview_actions(root, raw["actions"])
    try:
        proposal_preview = build_proposal_preview(
            root,
            raw["actions"],
            base_revision=current_revision,
        )
    except Exception as exc:
        if authored_revision(root) != current_revision:
            raise ValueError(
                "delegation project changed while the proposal preview was generated"
            ) from exc
        raise ValueError(f"delegation proposal preview failed: {exc}") from exc
    task["proposal"] = raw
    task["proposal_preview"] = proposal_preview
    questions = [item.strip() for item in raw.get("questions", []) if item.strip()]
    task["status"] = "needs-direction" if questions else "proposed"
    task["updated_at"] = datetime.now(UTC).isoformat()
    task["approval_token"] = (
        None if questions else _proposal_token(raw, proposal_preview)
    )
    if questions:
        _write(path, task)
        return task
    safe, reasons = _safe_auto_budget(raw["actions"])
    if raw.get("requires_review") is True:
        safe = False
        reasons.append("proposal explicitly requires human review")
    task["safe_auto"] = {"allowed": safe, "reasons": reasons}
    _write(path, task)
    if task["autonomy"] == "safe-auto" and safe:
        return apply_delegation(root, task_id)
    if task["autonomy"] == "safe-auto" and not safe:
        task["effective_autonomy"] = "review"
        _write(path, task)
    return task


def answer_delegation(
    project: str | Path,
    task_id: str,
    answer: str,
) -> dict[str, Any]:
    root = Path(project).resolve()
    path = _task_path(root, task_id)
    task = _read(path)
    if task.get("status") != "needs-direction":
        raise ValueError("delegation is not waiting for direction")
    if not answer.strip():
        raise ValueError("delegation answer must be non-empty")
    task.setdefault("answers", []).append(
        {"at": datetime.now(UTC).isoformat(), "text": answer.strip()}
    )
    task["proposal"] = None
    task["proposal_preview"] = None
    task["approval_token"] = None
    task.pop("safe_auto", None)
    task.pop("effective_autonomy", None)
    task["status"] = "pending"
    task["base_revision"] = authored_revision(root)
    task["updated_at"] = datetime.now(UTC).isoformat()
    _write(path, task)
    return task


def apply_delegation(
    project: str | Path,
    task_id: str,
    *,
    token: str | None = None,
    session: StudioSession | None = None,
    coordinator: BuildCoordinator | None = None,
) -> dict[str, Any]:
    root = Path(project).resolve()
    path = _task_path(root, task_id)
    task = _read(path)
    if task["status"] != "proposed" or not task.get("proposal"):
        raise ValueError("delegation has no applicable proposal")
    if task.get("base_revision") != authored_revision(root):
        raise ValueError("delegation project changed after the proposal was created")
    expected = task.get("approval_token")
    if not isinstance(expected, str) or not expected:
        raise ValueError("delegation proposal has no valid approval token")
    preview = task.get("proposal_preview")
    if (
        not isinstance(preview, dict)
        or preview.get("status") != "ready"
        or preview.get("base_revision") != task.get("base_revision")
        or not isinstance(preview.get("result_revision"), str)
    ):
        raise ValueError("delegation proposal has no valid isolated preview")
    if not secrets.compare_digest(expected, _proposal_token(task["proposal"], preview)):
        raise ValueError("delegation proposal or preview changed after approval was requested")
    actions = task["proposal"]["actions"]
    safe, _ = _safe_auto_budget(actions)
    requires_review = bool(task["proposal"].get("requires_review"))
    requires_token = task["autonomy"] == "review" or requires_review or not safe
    if requires_token and token is None:
        raise ValueError("delegation approval token is required")
    if token is not None and not secrets.compare_digest(token, expected):
        raise ValueError("delegation approval token does not match")
    active_session = session or StudioSession(root)
    report = active_session.apply(
        actions,
        revision=task["base_revision"],
        expected_revision=preview["result_revision"],
        expected_impact=expected_preview_impact(preview),
    )
    source_revision = report["revision"]
    state = build_state(root)
    ready = _production_ready(state, source_revision)
    task["status"] = "ready-for-listening" if ready else "rebuild-required"
    task["updated_at"] = datetime.now(UTC).isoformat()
    task["approval_token"] = None
    listening_checks = _listening_checks(task.get("proposal"))
    task["result"] = {
        "status": task["status"],
        "source": report,
        "source_revision": source_revision,
        "production": {
            "status": task["status"],
            "job_id": None,
            "job": None,
            "build": _build_summary(state),
            "revisions": _production_revisions(state),
            "ab": _unavailable_ab("production-not-ready"),
            "listening_checks": listening_checks,
            "listening": {
                "status": "pending" if ready else "waiting-for-build",
                "checks": listening_checks,
            },
            "error": None,
        },
    }
    if ready:
        _mark_ready_for_listening(root, task, state, source_revision)
    _write(path, task)
    if task["status"] == "ready-for-listening" or coordinator is None:
        return task
    try:
        job = coordinator.submit(
            "build",
            {"delegation_id": task_id, "source_revision": source_revision},
            coalesce=False,
        )
    except Exception as exc:
        task["result"]["production"]["error"] = {
            "type": type(exc).__name__,
            "message": str(exc),
        }
        task["updated_at"] = datetime.now(UTC).isoformat()
        _write(path, task)
        return task
    task["status"] = "building"
    task["result"]["status"] = "building"
    task["result"]["production"].update(
        {"status": job["status"], "job_id": job["id"], "job": job, "error": None}
    )
    task["updated_at"] = datetime.now(UTC).isoformat()
    _write(path, task)
    # A tiny or cached build can finish before the task records its job id.
    return reconcile_delegation(root, task_id, job=coordinator.get(job["id"]))


def finalize_delegation_job(project: str | Path, job: dict[str, Any]) -> dict[str, Any] | None:
    """Bind a terminal production job to the delegation that requested it."""

    payload = job.get("payload")
    task_id = payload.get("delegation_id") if isinstance(payload, dict) else None
    if not isinstance(task_id, str) or not task_id:
        return None
    path = _task_path(Path(project).resolve(), task_id)
    if not path.is_file():
        return None
    return reconcile_delegation(project, task_id, job=job)


def reconcile_delegation(
    project: str | Path,
    task_id: str,
    *,
    job: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Refresh production truth without reapplying the authored source edit."""

    root = Path(project).resolve()
    path = _task_path(root, task_id)
    task = _read(path)
    if task.get("status") not in {
        "building",
        "rebuild-required",
        "build-failed",
        "build-cancelled",
        "ready-for-listening",
    }:
        return task
    was_ready_for_listening = task.get("status") == "ready-for-listening"
    result = task.get("result")
    production = result.get("production") if isinstance(result, dict) else None
    if not isinstance(production, dict):
        return task
    source_revision = result.get("source_revision")
    if not isinstance(source_revision, str):
        return task
    if job is None and isinstance(production.get("job_id"), str):
        job = _persisted_job(root, production["job_id"])
    terminal_failure = False
    if job is not None and job.get("id") == production.get("job_id"):
        production["job"] = job
        production["status"] = job.get("status", production.get("status"))
        if job.get("status") in {"failed", "cancelled"}:
            terminal_failure = True
            task["status"] = "build-failed" if job["status"] == "failed" else "build-cancelled"
            result["status"] = "error"
            production["error"] = job.get("error") or {
                "type": "Cancelled",
                "message": job.get("message", "production build was cancelled"),
            }
    state = build_state(root)
    production["build"] = _build_summary(state)
    production["revisions"] = _production_revisions(state)
    if _production_ready(state, source_revision):
        _mark_ready_for_listening(root, task, state, source_revision)
        task["updated_at"] = datetime.now(UTC).isoformat()
        _write(path, task)
    elif was_ready_for_listening:
        task["status"] = "rebuild-required"
        result["status"] = "rebuild-required"
        production["status"] = "rebuild-required"
        production["listening"] = {
            "status": "blocked",
            "checks": production.get("listening_checks", []),
            "reason": "production artifacts are no longer current",
        }
        production["error"] = {
            "type": "StaleProductionArtifacts",
            "message": "production artifacts changed after they became ready for listening",
        }
        task["updated_at"] = datetime.now(UTC).isoformat()
        _write(path, task)
    elif job is not None and job.get("status") == "ready":
        task["status"] = "build-failed"
        result["status"] = "error"
        production["status"] = "failed"
        production["error"] = {
            "type": "StaleProductionArtifacts",
            "message": "build job finished but render or mix artifacts are not current",
        }
        task["updated_at"] = datetime.now(UTC).isoformat()
        _write(path, task)
    elif terminal_failure or job is not None:
        task["updated_at"] = datetime.now(UTC).isoformat()
        _write(path, task)
    return task


def accept_delegation(
    project: str | Path,
    task_id: str,
    note: str = "",
) -> dict[str, Any]:
    """Record human musical acceptance of current, revision-bound production artifacts."""

    root = Path(project).resolve()
    path = _task_path(root, task_id)
    persisted = _read(path)
    if persisted.get("status") != "ready-for-listening":
        raise ValueError("delegation is not ready for listening acceptance")
    _, persisted_production, _ = _production_result(persisted)
    persisted_revisions = copy.deepcopy(persisted_production.get("revisions"))
    task = reconcile_delegation(root, task_id)
    if task.get("status") != "ready-for-listening":
        raise ValueError("delegation is not ready for listening acceptance")
    result, production, source_revision = _production_result(task)
    if persisted_revisions != production.get("revisions"):
        raise ValueError("delegation production changed; listen to the current revision first")
    state = build_state(root)
    _require_current_production(state, source_revision, production)
    _refresh_listening_evidence(root, task, state, source_revision)
    accepted_at = datetime.now(UTC).isoformat()
    acceptance = {
        "action": "accept",
        "note": note.strip(),
        "accepted_at": accepted_at,
        "revision": source_revision,
    }
    task.setdefault("listening_history", []).append(acceptance)
    task["status"] = "accepted"
    task["accepted_at"] = accepted_at
    task["accepted_revision"] = source_revision
    task["acceptance"] = {
        "note": acceptance["note"],
        "accepted_at": accepted_at,
        "revision": source_revision,
    }
    task["updated_at"] = accepted_at
    result["status"] = "accepted"
    production["status"] = "accepted"
    production["listening"] = {
        "status": "accepted",
        "checks": production.get("listening_checks", []),
        **task["acceptance"],
    }
    production["error"] = None
    _write(path, task)
    return task


def revise_delegation(
    project: str | Path,
    task_id: str,
    feedback: str,
) -> dict[str, Any]:
    """Request another proposal without reverting the source that was already applied."""

    if not feedback.strip():
        raise ValueError("listening feedback must be non-empty")
    root = Path(project).resolve()
    path = _task_path(root, task_id)
    persisted = _read(path)
    if persisted.get("status") != "ready-for-listening":
        raise ValueError("delegation is not ready for listening revision")
    _, persisted_production, _ = _production_result(persisted)
    persisted_revisions = copy.deepcopy(persisted_production.get("revisions"))
    task = reconcile_delegation(root, task_id)
    if task.get("status") != "ready-for-listening":
        raise ValueError("delegation is not ready for listening revision")
    result, production, source_revision = _production_result(task)
    if persisted_revisions != production.get("revisions"):
        raise ValueError("delegation production changed; listen to the current revision first")
    state = build_state(root)
    _require_current_production(state, source_revision, production)
    _refresh_listening_evidence(root, task, state, source_revision)
    requested_at = datetime.now(UTC).isoformat()
    record = {
        "action": "revise",
        "feedback": feedback.strip(),
        "requested_at": requested_at,
        "revision": source_revision,
    }
    task.setdefault("listening_history", []).append(record)
    task["status"] = "pending"
    task["base_revision"] = source_revision
    task["proposal"] = None
    task["proposal_preview"] = None
    task["approval_token"] = None
    task.pop("safe_auto", None)
    task.pop("effective_autonomy", None)
    task["updated_at"] = requested_at
    result["status"] = "revision-requested"
    production["status"] = "revision-requested"
    production["listening"] = {
        "status": "revision-requested",
        "checks": production.get("listening_checks", []),
        "feedback": record["feedback"],
        "requested_at": requested_at,
        "revision": source_revision,
    }
    production["error"] = None
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
    unknown = set(raw) - {
        "summary",
        "reasoning",
        "actions",
        "listening_check",
        "questions",
        "base_revision",
        "pass",
        "scope",
        "preserve",
        "evidence_ids",
        "expected_effect",
        "requires_review",
    }
    if unknown:
        raise ValueError(f"proposal has unknown fields: {', '.join(sorted(unknown))}")
    if not isinstance(raw.get("summary"), str) or not raw["summary"].strip():
        raise ValueError("proposal summary is required")
    actions = raw.get("actions")
    if not isinstance(actions, list):
        raise ValueError("proposal actions must be a list")
    if any(not isinstance(item, dict) or item.get("type") not in SAFE_ACTIONS for item in actions):
        raise ValueError("proposal contains an unsupported action")
    questions = raw.get("questions", [])
    if not isinstance(questions, list) or any(not isinstance(item, str) for item in questions):
        raise ValueError("proposal questions must be a string list")
    if not actions and not questions:
        raise ValueError("proposal requires actions or direction questions")
    if not isinstance(raw.get("base_revision"), str) or len(raw["base_revision"]) != 64:
        raise ValueError("proposal base_revision must be a SHA-256 string")
    if "requires_review" in raw and not isinstance(raw["requires_review"], bool):
        raise ValueError("proposal requires_review must be a boolean")
    listening_check = raw.get("listening_check")
    if listening_check is not None and not (
        isinstance(listening_check, str)
        or (
            isinstance(listening_check, list)
            and all(isinstance(item, str) for item in listening_check)
        )
    ):
        raise ValueError("proposal listening_check must be a string or string list")


def _safe_auto_budget(actions: list[dict[str, Any]]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if len(actions) > 32:
        reasons.append("more than 32 edit actions")
    for index, action in enumerate(actions):
        kind = action.get("type")
        if kind not in SAFE_AUTO_ACTIONS:
            reasons.append(f"action {index} ({kind}) can change musical structure")
            continue
        if kind == "update_note":
            changes = action.get("changes", {})
            if not isinstance(changes, dict) or set(changes) - {"velocity", "articulation"}:
                reasons.append(f"action {index} changes pitch or an unsupported note field")
        if kind in {"scale_velocity_range", "set_articulation_range"}:
            start = int(action.get("measure_start", 1))
            end = int(action.get("measure_end", start))
            if end - start + 1 > 16:
                reasons.append(f"action {index} spans more than 16 measures")
        if kind == "scale_velocity_range":
            factor = float(action.get("factor", 1.0))
            if not 0.75 <= factor <= 1.25:
                reasons.append(f"action {index} velocity factor exceeds 25 percent")
        if kind == "update_mix":
            changes = action.get("changes", {})
            if not isinstance(changes, dict) or set(changes) - {"gain_db", "pan", "send"}:
                reasons.append(f"action {index} has unsupported mix changes")
            if "gain_db" in changes and not -18 <= float(changes["gain_db"]) <= 6:
                reasons.append(f"action {index} gain exceeds the safe preview range")
    return not reasons, reasons


def _proposal_token(proposal: dict[str, Any], preview: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"proposal": proposal, "proposal_preview": preview},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(canonical).hexdigest()


def _production_result(
    task: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], str]:
    result = task.get("result")
    production = result.get("production") if isinstance(result, dict) else None
    source_revision = result.get("source_revision") if isinstance(result, dict) else None
    if not isinstance(result, dict) or not isinstance(production, dict):
        raise ValueError("delegation has no production result")
    if not isinstance(source_revision, str) or not source_revision:
        raise ValueError("delegation production has no source revision")
    return result, production, source_revision


def _mark_ready_for_listening(
    root: Path,
    task: dict[str, Any],
    state: dict[str, Any],
    source_revision: str,
) -> None:
    result, production, recorded_revision = _production_result(task)
    if recorded_revision != source_revision:
        raise ValueError("delegation production source revision changed")
    checks = production.get("listening_checks")
    if not isinstance(checks, list):
        checks = _listening_checks(task.get("proposal"))
    task["status"] = "ready-for-listening"
    result["status"] = "ready-for-listening"
    production.update(
        {
            "status": "ready-for-listening",
            "build": _build_summary(state),
            "revisions": _production_revisions(state),
            "ab": _ab_evidence(root, state, source_revision),
            "listening_checks": checks,
            "listening": {"status": "pending", "checks": checks},
            "error": None,
        }
    )


def _refresh_listening_evidence(
    root: Path,
    task: dict[str, Any],
    state: dict[str, Any],
    source_revision: str,
) -> None:
    _, production, _ = _production_result(task)
    latest = build_state(root)
    _require_current_production(latest, source_revision, production)
    ab = _ab_evidence(root, latest, source_revision)
    production["build"] = _build_summary(latest)
    production["revisions"] = _production_revisions(latest)
    production["ab"] = ab


def _require_current_production(
    state: dict[str, Any],
    source_revision: str,
    production: dict[str, Any],
) -> None:
    if not _production_ready(state, source_revision):
        raise ValueError("delegation production is stale or not ready")
    current = _production_revisions(state)
    recorded = production.get("revisions")
    if not isinstance(recorded, dict) or recorded != current:
        raise ValueError("delegation production revisions changed after listening became ready")


def _listening_checks(proposal: Any) -> list[str]:
    raw = proposal.get("listening_check") if isinstance(proposal, dict) else None
    if isinstance(raw, str):
        return [raw.strip()] if raw.strip() else []
    if isinstance(raw, list):
        return [item.strip() for item in raw if isinstance(item, str) and item.strip()]
    return []


def _production_revisions(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "authored_revision": state.get("authored_revision", state.get("source_revision")),
        "compiled_revision": state.get("compiled_revision"),
        "rendered_revision": state.get("rendered_revision"),
        "mix_revision": state.get("mix_revision"),
    }


def _ab_evidence(
    root: Path,
    state: dict[str, Any],
    source_revision: str,
) -> dict[str, Any]:
    checked_at = datetime.now(UTC).isoformat()
    try:
        stages = state.get("stages") if isinstance(state.get("stages"), dict) else {}
        mix = stages.get("mix") if isinstance(stages.get("mix"), dict) else {}
        output = mix.get("output") if isinstance(mix.get("output"), dict) else None
        if (
            state.get("authored_revision") != source_revision
            or mix.get("status") != "ready"
            or output is None
            or not isinstance(output.get("sha256"), str)
        ):
            return _unavailable_ab(
                "current-master-not-bound-to-authored-revision",
                checked_at=checked_at,
                source_revision=source_revision,
            )
        current = {
            "source_revision": source_revision,
            "sha256": output["sha256"],
            "integrated_lufs": _measurement_value(mix.get("provenance"), "integrated_lufs"),
        }
        previous = _previous_checkpoint_identity(root)
        if previous is None:
            reason = "previous-master-unavailable"
        elif previous.get("sha256") == current["sha256"]:
            reason = "masters-are-identical"
        else:
            reason = None
        level_matching = (
            "integrated-lufs"
            if current["integrated_lufs"] is not None
            and previous is not None
            and previous.get("integrated_lufs") is not None
            else "none"
        )
        return {
            "available": reason is None,
            "unavailable_reason": reason,
            "checked_at": checked_at,
            "source_revision": source_revision,
            "level_matching": level_matching,
            "current": current,
            "previous": previous,
        }
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        return {
            **_unavailable_ab(
                "listening-evidence-unavailable",
                checked_at=checked_at,
                source_revision=source_revision,
            ),
            "detail": f"{type(exc).__name__}: {exc}",
        }


def _previous_checkpoint_identity(root: Path) -> dict[str, Any] | None:
    checkpoint_root = (root / "build" / "studio" / "checkpoints").resolve()
    try:
        record = json.loads((checkpoint_root / "latest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    audio = record.get("audio") if isinstance(record, dict) else None
    if not isinstance(audio, dict) or not isinstance(audio.get("path"), str):
        return None
    path = Path(audio["path"]).resolve(strict=True)
    if checkpoint_root not in path.parents:
        return None
    expected_sha = audio.get("sha256")
    if not isinstance(expected_sha, str) or file_sha256(path) != expected_sha:
        return None
    measurement = record.get("measurement")
    return {
        "source_revision": record.get("source_revision"),
        "sha256": expected_sha,
        "integrated_lufs": (
            measurement.get("integrated_lufs") if isinstance(measurement, dict) else None
        ),
    }


def _measurement_value(provenance: Any, field: str) -> float | None:
    if not isinstance(provenance, dict):
        return None
    measurement = provenance.get("final_measurement")
    value = measurement.get(field) if isinstance(measurement, dict) else None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _unavailable_ab(
    reason: str,
    *,
    checked_at: str | None = None,
    source_revision: str | None = None,
) -> dict[str, Any]:
    return {
        "available": False,
        "unavailable_reason": reason,
        "checked_at": checked_at,
        "source_revision": source_revision,
        "level_matching": None,
        "current": None,
        "previous": None,
    }


def _production_ready(state: dict[str, Any], source_revision: str) -> bool:
    revision = state.get("authored_revision", state.get("source_revision"))
    if revision != source_revision:
        return False
    stages = state.get("stages")
    if not isinstance(stages, dict):
        return False
    stages_ready = all(
        isinstance(stages.get(name), dict) and stages[name].get("status") == "ready"
        for name in ("compile", "render", "mix")
    )
    revisions = _production_revisions(state)
    return stages_ready and all(
        isinstance(revisions.get(name), str) and bool(revisions[name])
        for name in (
            "authored_revision",
            "compiled_revision",
            "rendered_revision",
            "mix_revision",
        )
    )


def _build_summary(state: dict[str, Any]) -> dict[str, Any]:
    stages = state.get("stages") if isinstance(state.get("stages"), dict) else {}
    return {
        "source_revision": state.get("authored_revision", state.get("source_revision")),
        "compiled_revision": state.get("compiled_revision"),
        "rendered_revision": state.get("rendered_revision"),
        "mix_revision": state.get("mix_revision"),
        "stages": {
            name: {
                key: stage.get(key)
                for key in ("status", "reason")
                if stage.get(key) is not None
            }
            for name in ("compile", "render", "mix")
            if isinstance((stage := stages.get(name)), dict)
        },
    }


def _persisted_job(root: Path, job_id: str) -> dict[str, Any] | None:
    jobs_path = root / "build" / "jobs.json"
    try:
        raw = json.loads(jobs_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    jobs = raw.get("jobs") if isinstance(raw, dict) else None
    if not isinstance(jobs, list):
        return None
    return next(
        (item for item in jobs if isinstance(item, dict) and item.get("id") == job_id),
        None,
    )


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
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
    )
    try:
        temporary.write_text(
            json.dumps(task, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
