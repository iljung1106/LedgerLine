from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

import pytest

import ledgerline.delegation as delegation_module
from ledgerline.build_state import authored_revision, build_state
from ledgerline.cli import main
from ledgerline.delegation import (
    accept_delegation,
    apply_delegation,
    create_delegation,
    next_delegation,
    propose_delegation,
    reconcile_delegation,
    revise_delegation,
    show_delegation,
)
from ledgerline.studio_server import create_studio_server


class _DeferredBuildCoordinator:
    def __init__(self) -> None:
        self.job: dict[str, Any] | None = None

    def submit(
        self,
        kind: str,
        payload: dict[str, Any] | None = None,
        *,
        coalesce: bool = True,
    ) -> dict[str, Any]:
        assert kind == "build"
        assert coalesce is False
        self.job = {
            "id": "listening-build-1",
            "kind": kind,
            "status": "queued",
            "payload": payload or {},
        }
        return self.job

    def get(self, job_id: str) -> dict[str, Any]:
        assert self.job is not None and self.job["id"] == job_id
        return self.job


def _prepare_ready_task(
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[dict[str, Any], _DeferredBuildCoordinator, dict[str, Any]]:
    real_build_state = build_state
    production_ready = {"value": False, "mix_revision": "m" * 64}

    def controlled_build_state(root: str | Path) -> dict[str, Any]:
        if not production_ready["value"]:
            return real_build_state(root)
        revision = authored_revision(root)
        return {
            "schema_version": "2",
            "status": "ok",
            "project": str(Path(root).resolve()),
            "authored_revision": revision,
            "compiled_revision": "c" * 64,
            "rendered_revision": "r" * 64,
            "mix_revision": production_ready["mix_revision"],
            "stages": {
                "compile": {"status": "ready"},
                "render": {"status": "ready"},
                "mix": {"status": "ready"},
            },
            "engines": {},
        }

    monkeypatch.setattr(delegation_module, "build_state", controlled_build_state)
    created = create_delegation(project, "Soften the opening and preserve the melody")
    proposed = propose_delegation(
        project,
        created["id"],
        {
            "summary": "Softer opening",
            "actions": [
                {
                    "type": "scale_velocity_range",
                    "part": "piano",
                    "measure_start": 1,
                    "measure_end": 2,
                    "factor": 0.9,
                }
            ],
            "listening_check": [
                "Confirm the opening remains clear.",
                "Confirm the cello melody is unchanged.",
            ],
        },
    )
    coordinator = _DeferredBuildCoordinator()
    building = apply_delegation(
        project,
        created["id"],
        token=proposed["approval_token"],
        coordinator=coordinator,
    )
    assert building["status"] == "building"
    assert coordinator.job is not None
    production_ready["value"] = True
    coordinator.job["status"] = "ready"
    ready = reconcile_delegation(project, created["id"], job=coordinator.job)
    assert ready["status"] == "ready-for-listening"
    return ready, coordinator, production_ready


def test_build_ready_requires_listening_acceptance_and_persists_record(
    example_project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ready, _, _ = _prepare_ready_task(example_project, monkeypatch)
    production = ready["result"]["production"]
    assert ready["result"]["status"] == "ready-for-listening"
    assert production["status"] == "ready-for-listening"
    assert production["revisions"] == {
        "authored_revision": ready["result"]["source_revision"],
        "compiled_revision": "c" * 64,
        "rendered_revision": "r" * 64,
        "mix_revision": "m" * 64,
    }
    assert production["listening_checks"] == [
        "Confirm the opening remains clear.",
        "Confirm the cello melody is unchanged.",
    ]
    assert isinstance(production["ab"]["available"], bool)
    assert production["ab"]["source_revision"] == ready["result"]["source_revision"]

    exit_code = main(
        [
            "delegate",
            "accept",
            str(example_project),
            ready["id"],
            "--note",
            "The balance and phrase direction are approved.",
            "--json",
        ]
    )
    assert exit_code == 0
    accepted = show_delegation(example_project, ready["id"])
    assert accepted["status"] == "accepted"
    assert accepted["accepted_revision"] == ready["result"]["source_revision"]
    assert accepted["acceptance"]["note"] == "The balance and phrase direction are approved."
    assert accepted["accepted_at"]
    assert accepted["result"]["production"]["listening"]["status"] == "accepted"

    persisted = show_delegation(example_project, ready["id"])
    assert persisted["acceptance"] == accepted["acceptance"]
    assert persisted["listening_history"][-1]["action"] == "accept"


def test_listening_revision_returns_to_pending_without_undoing_source(
    example_project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ready, _, _ = _prepare_ready_task(example_project, monkeypatch)
    applied_revision = authored_revision(example_project)
    exit_code = main(
        [
            "delegate",
            "revise",
            str(example_project),
            ready["id"],
            "Keep the same notes, but make the cadence less abrupt.",
            "--json",
        ]
    )
    assert exit_code == 0
    revised = show_delegation(example_project, ready["id"])
    assert revised["status"] == "pending"
    assert revised["base_revision"] == applied_revision
    assert revised["proposal"] is None
    assert revised["proposal_preview"] is None
    assert revised["listening_history"][-1] == {
        "action": "revise",
        "feedback": "Keep the same notes, but make the cadence less abrupt.",
        "requested_at": revised["listening_history"][-1]["requested_at"],
        "revision": applied_revision,
    }
    assert authored_revision(example_project) == applied_revision
    assert next_delegation(example_project)["task"]["id"] == ready["id"]

    reproposed = propose_delegation(
        example_project,
        ready["id"],
        {
            "summary": "Gentler cadence",
            "actions": [
                {
                    "type": "scale_velocity_range",
                    "part": "piano",
                    "measure_start": 7,
                    "measure_end": 8,
                    "factor": 0.95,
                }
            ],
            "listening_check": "Confirm the cadence still resolves clearly.",
        },
    )
    assert reproposed["status"] == "proposed"
    assert reproposed["base_revision"] == applied_revision
    assert reproposed["listening_history"][-1]["action"] == "revise"


def test_accept_rejects_not_ready_and_stale_production(
    example_project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pending = create_delegation(example_project, "Make it warmer")
    with pytest.raises(ValueError, match="not ready for listening acceptance"):
        accept_delegation(example_project, pending["id"], "Not yet")

    ready, _, _ = _prepare_ready_task(example_project, monkeypatch)
    mix = example_project / "mix.yaml"
    mix.write_text(mix.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="not ready for listening acceptance"):
        accept_delegation(example_project, ready["id"], "Approve stale audio")
    assert show_delegation(example_project, ready["id"])["status"] == "rebuild-required"


def test_accept_requires_a_fresh_listening_revision(
    example_project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ready, _, production = _prepare_ready_task(example_project, monkeypatch)
    production["mix_revision"] = "n" * 64
    with pytest.raises(ValueError, match="listen to the current revision first"):
        accept_delegation(example_project, ready["id"], "Approve the older master")

    refreshed = show_delegation(example_project, ready["id"])
    assert refreshed["status"] == "ready-for-listening"
    assert refreshed["result"]["production"]["revisions"]["mix_revision"] == "n" * 64
    accepted = accept_delegation(example_project, ready["id"], "Approved after re-listening")
    assert accepted["status"] == "accepted"


def test_revise_requires_feedback_and_ready_listening_state(example_project: Path) -> None:
    pending = create_delegation(example_project, "Clarify the cadence")
    with pytest.raises(ValueError, match="feedback must be non-empty"):
        revise_delegation(example_project, pending["id"], "  ")
    with pytest.raises(ValueError, match="not ready for listening revision"):
        revise_delegation(example_project, pending["id"], "Use a lighter bass attack.")


@pytest.mark.parametrize(
    ("action", "payload", "expected_status"),
    [
        ("accept", {"note": "Approved through Studio."}, "accepted"),
        ("revise", {"feedback": "Let the cadence breathe longer."}, "pending"),
    ],
)
def test_studio_listening_decision_routes(
    example_project: Path,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
    payload: dict[str, str],
    expected_status: str,
) -> None:
    ready, _, _ = _prepare_ready_task(example_project, monkeypatch)
    server = create_studio_server(example_project, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        request = Request(
            f"{base}/api/delegations/{ready['id']}/{action}",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-LedgerLine-Token": server.csrf_token,
            },
            method="POST",
        )
        with urlopen(request, timeout=5) as response:
            report = json.loads(response.read().decode("utf-8"))
        assert report["status"] == expected_status
        assert show_delegation(example_project, ready["id"])["status"] == expected_status
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
