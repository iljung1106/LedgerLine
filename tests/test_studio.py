from __future__ import annotations

import json
import threading
from urllib.request import urlopen

from ledgerline.delegation import create_delegation, next_delegation, propose_delegation
from ledgerline.studio_edits import StudioSession
from ledgerline.studio_model import build_studio_model
from ledgerline.studio_server import create_studio_server


def test_studio_model_exposes_score_mix_notes_and_static_capabilities(example_project) -> None:
    model = build_studio_model(example_project)
    assert model["status"] == "ok"
    assert model["parts"]
    assert model["notes"]
    assert model["score"]["url"] == "/api/score"
    assert model["capabilities"]["edit_pitch"] is True
    assert set(model["mix"]["tracks"]) >= {part["id"] for part in model["parts"]}


def test_studio_session_applies_reversible_note_edits(example_project) -> None:
    model = build_studio_model(example_project)
    note = model["notes"][0]
    session = StudioSession(example_project)
    report = session.apply(
        [
            {
                "type": "update_note",
                "part": note["part"],
                "measure": note["measure"],
                "voice": note["voice"],
                "event_index": note["event_index"],
                "pitch_index": note["pitch_index"],
                "changes": {"velocity": 99},
            }
        ],
        revision=model["project"]["revision"],
    )
    assert report["status"] == "ok"
    edited = build_studio_model(example_project)
    changed = next(item for item in edited["notes"] if item["id"] == note["id"])
    assert changed["velocity"] == 99
    session.undo()
    reverted = build_studio_model(example_project)
    original = next(item for item in reverted["notes"] if item["id"] == note["id"])
    assert original["velocity"] == note["velocity"]


def test_safe_auto_delegation_applies_agent_proposal(example_project) -> None:
    created = create_delegation(
        example_project,
        "Make the opening a touch softer",
        autonomy="safe-auto",
    )
    pending = next_delegation(example_project)
    assert pending["task"]["id"] == created["id"]
    applied = propose_delegation(
        example_project,
        created["id"],
        {
            "summary": "Softer opening dynamics",
            "reasoning": "Lowering velocity is reversible and stays inside authored notes.",
            "actions": [
                {
                    "type": "scale_velocity_range",
                    "part": "piano",
                    "measure_start": 1,
                    "measure_end": 2,
                    "factor": 0.9,
                }
            ],
            "listening_check": "Confirm the opening still speaks clearly.",
        },
    )
    assert applied["status"] == "applied"
    assert applied["result"]["status"] == "ok"


def test_studio_server_serves_model_with_csrf_and_static_index(example_project) -> None:
    server = create_studio_server(example_project, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        with urlopen(f"{base}/api/model", timeout=5) as response:
            model = json.loads(response.read().decode("utf-8"))
        assert model["csrf_token"]
        assert model["history"] == {"can_undo": False, "can_redo": False}
        with urlopen(f"{base}/", timeout=5) as response:
            index = response.read().decode("utf-8")
        assert "LedgerLine Studio" in index
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
