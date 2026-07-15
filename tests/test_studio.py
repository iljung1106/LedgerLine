from __future__ import annotations

import json
import threading
import time
from urllib.request import Request, urlopen

import pytest
import yaml

import ledgerline.delegation as delegation_module
import ledgerline.studio_server as studio_server_module
from ledgerline.build_state import authored_revision, build_state
from ledgerline.delegation import (
    apply_delegation,
    create_delegation,
    finalize_delegation_job,
    next_delegation,
    propose_delegation,
    show_delegation,
)
from ledgerline.jobs import JobContext, LocalBuildCoordinator
from ledgerline.studio_edits import StudioSession
from ledgerline.studio_model import build_studio_model, project_revision
from ledgerline.studio_server import _studio_job_payload, create_studio_server, run_studio


def test_studio_prepare_requires_explicit_legacy_engine_paths(example_project) -> None:
    with pytest.raises(ValueError, match="--ffmpeg.*--fluidsynth.*--soundfont"):
        _studio_job_payload(
            example_project,
            ffmpeg=None,
            fluidsynth=None,
            soundfont=None,
            require_audio=True,
        )


def test_run_studio_prepare_rejects_missing_legacy_paths_before_build(
    example_project,
    monkeypatch,
) -> None:
    def fail_coordinator(*_args, **_kwargs):
        raise AssertionError("build coordinator must not start before explicit path validation")

    monkeypatch.setattr(studio_server_module, "LocalBuildCoordinator", fail_coordinator)
    with pytest.raises(ValueError, match="requires explicit paths"):
        run_studio(example_project, prepare=True, open_browser=False)


def test_studio_prepare_render_graph_requires_explicit_ffmpeg(
    example_project,
    tmp_path,
) -> None:
    (example_project / "render.yaml").write_text("format: 1\nnodes: []\n", encoding="utf-8")
    with pytest.raises(ValueError, match="--ffmpeg"):
        _studio_job_payload(
            example_project,
            ffmpeg=None,
            fluidsynth=None,
            soundfont=None,
            require_audio=True,
        )
    ffmpeg = tmp_path / "ffmpeg.exe"
    ffmpeg.write_bytes(b"fixture")
    assert _studio_job_payload(
        example_project,
        ffmpeg=ffmpeg,
        fluidsynth=None,
        soundfont=None,
        require_audio=True,
    ) == {"ffmpeg": str(ffmpeg.resolve())}

    score_only = create_studio_server(example_project, port=0)
    try:
        submitted = score_only.jobs.submit("render")
        failed = score_only.jobs.wait(submitted["id"], timeout=5)
        assert failed["status"] == "failed"
        assert "--ffmpeg" in failed["error"]["message"]
        assert "--fluidsynth" not in failed["error"]["message"]
        assert "--soundfont" not in failed["error"]["message"]
    finally:
        score_only.server_close()


def test_studio_preserves_only_explicit_existing_engine_paths(
    example_project, tmp_path
) -> None:
    ffmpeg = tmp_path / "ffmpeg.exe"
    fluidsynth = tmp_path / "fluidsynth.exe"
    soundfont = tmp_path / "instrument.sf3"
    for path in (ffmpeg, fluidsynth, soundfont):
        path.write_bytes(b"fixture")
    payload = _studio_job_payload(
        example_project,
        ffmpeg=ffmpeg,
        fluidsynth=fluidsynth,
        soundfont=soundfont,
        require_audio=True,
    )
    assert payload == {
        "ffmpeg": str(ffmpeg.resolve()),
        "fluidsynth": str(fluidsynth.resolve()),
        "soundfont": str(soundfont.resolve()),
    }


def test_score_only_studio_audio_job_does_not_use_implicit_environment(
    example_project,
    monkeypatch,
) -> None:
    def fail_doctor():
        raise AssertionError("Studio audio jobs must not discover implicit render tools")

    monkeypatch.setattr("ledgerline.render.doctor", fail_doctor)
    server = create_studio_server(example_project, port=0)
    try:
        submitted = server.jobs.submit("render")
        failed = server.jobs.wait(submitted["id"], timeout=5)
        assert failed["status"] == "failed"
        assert failed["error"]["type"] == "ValueError"
        assert "implicit environment discovery is disabled" in failed["error"]["message"]
    finally:
        server.server_close()


def test_server_ui_and_delegation_jobs_keep_approved_tool_paths(
    example_project,
    tmp_path,
) -> None:
    ffmpeg = tmp_path / "ffmpeg.exe"
    fluidsynth = tmp_path / "fluidsynth.exe"
    soundfont = tmp_path / "instrument.sf3"
    unapproved = tmp_path / "other.exe"
    for path in (ffmpeg, fluidsynth, soundfont, unapproved):
        path.write_bytes(b"fixture")
    expected = {
        "ffmpeg": str(ffmpeg.resolve()),
        "fluidsynth": str(fluidsynth.resolve()),
        "soundfont": str(soundfont.resolve()),
    }
    observed: list[tuple[str, dict]] = []

    def capture_render(context: JobContext) -> dict:
        observed.append(("render", dict(context.payload)))
        return {"status": "ok"}

    def capture_build(context: JobContext) -> dict:
        observed.append(("build", dict(context.payload)))
        return {"status": "ok"}

    server = create_studio_server(
        example_project,
        port=0,
        ffmpeg=ffmpeg,
        fluidsynth=fluidsynth,
        soundfont=soundfont,
    )
    server.jobs._runners["render"] = capture_render
    server.jobs._runners["build"] = capture_build
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        with urlopen(f"{base}/api/model", timeout=5) as response:
            model = json.loads(response.read().decode("utf-8"))
        for kind in ("render", "build"):
            request = Request(
                f"{base}/api/jobs",
                data=json.dumps({"kind": kind, "payload": {"timeout": 17}}).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "X-LedgerLine-Token": model["csrf_token"],
                },
                method="POST",
            )
            with urlopen(request, timeout=5) as response:
                submitted = json.loads(response.read().decode("utf-8"))
            assert submitted["payload"] == {"timeout": 17}
            assert server.jobs.wait(submitted["id"], timeout=5)["status"] == "ready"

        created = create_delegation(example_project, "Soften the opening")
        proposed = propose_delegation(
            example_project,
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
            },
        )
        delegated = apply_delegation(
            example_project,
            created["id"],
            token=proposed["approval_token"],
            session=server.session,
            coordinator=server.jobs,
        )
        delegation_job = delegated["result"]["production"]["job_id"]
        assert delegation_job
        server.jobs.wait(delegation_job, timeout=5)

        assert observed[0] == ("render", {**expected, "timeout": 17})
        assert observed[1] == ("build", {**expected, "timeout": 17})
        assert observed[2][0] == "build"
        assert {key: observed[2][1][key] for key in expected} == expected
        assert observed[2][1]["delegation_id"] == created["id"]
        assert observed[2][1]["source_revision"]
        with pytest.raises(ValueError, match="cannot override"):
            server.jobs.submit("render", {"ffmpeg": str(unapproved.resolve())})
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_studio_model_exposes_score_mix_notes_and_static_capabilities(example_project) -> None:
    model = build_studio_model(example_project)
    assert model["status"] == "ok"
    assert model["schema_version"] == "2"
    assert model["parts"]
    assert model["notes"]
    assert "controls" in model
    assert "automation" in model
    assert model["score"]["url"].startswith("/api/score?v=")
    assert model["score"]["status"] == "ready"
    assert model["capabilities"]["edit_pitch"] is True
    assert set(model["mix"]["tracks"]) >= {part["id"] for part in model["parts"]}
    piano = next(part for part in model["parts"] if part["id"] == "piano")
    assert piano["profile_capabilities"]["range"]["comfortable_low"]
    assert piano["profile_capabilities"]["performance"]["expression"]["controller"] == 11
    assert "keyswitch_map" in piano["profile_capabilities"]


def test_studio_tempo_points_keep_authored_indexes_when_ramp_splits_render_segments(
    example_project,
) -> None:
    piece_path = example_project / "piece.yaml"
    document = yaml.safe_load(piece_path.read_text(encoding="utf-8"))
    document["tempo"] = [
        {
            "at": "1:1",
            "bpm": 72,
            "ramp": {"to": "3:1", "bpm": 84, "curve": "linear"},
        }
    ]
    piece_path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")

    model = build_studio_model(example_project)
    assert len(model["transport"]["tempo_segments"]) == 2
    assert model["tempo"] == [
        {
            "source_index": 0,
            "at": "1:1",
            "seconds": 0.0,
            "bpm": 72.0,
            "ramp": {"to": "3:1", "bpm": 84.0, "curve": "linear"},
        }
    ]

    StudioSession(example_project).apply(
        [
            {
                "type": "update_tempo",
                "index": model["tempo"][0]["source_index"],
                "changes": {"bpm": 75.0},
            }
        ]
    )
    authored = yaml.safe_load(piece_path.read_text(encoding="utf-8"))
    assert authored["tempo"][0]["bpm"] == 75.0
    assert authored["tempo"][0]["ramp"]["bpm"] == 84.0


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


def test_safe_auto_delegation_reports_rebuild_required_without_a_production_job(
    example_project,
) -> None:
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
    assert applied["status"] == "rebuild-required"
    assert applied["result"]["status"] == "rebuild-required"
    assert applied["result"]["source"]["status"] == "ok"
    assert applied["result"]["production"]["job_id"] is None
    assert applied["result"]["production"]["build"]["source_revision"] == applied["result"][
        "source_revision"
    ]
    assert applied["result"]["production"]["build"]["stages"]["compile"]["status"] == "ready"


def test_requires_review_proposal_cannot_bypass_token_in_safe_auto(example_project) -> None:
    created = create_delegation(
        example_project,
        "Make the opening a touch softer, but ask me first",
        autonomy="safe-auto",
    )
    proposed = propose_delegation(
        example_project,
        created["id"],
        {
            "summary": "Softer opening dynamics",
            "actions": [
                {
                    "type": "scale_velocity_range",
                    "part": "piano",
                    "measure_start": 1,
                    "measure_end": 2,
                    "factor": 0.9,
                }
            ],
            "requires_review": True,
        },
    )
    assert proposed["status"] == "proposed"
    assert proposed["effective_autonomy"] == "review"
    with pytest.raises(ValueError, match="approval token is required"):
        apply_delegation(example_project, created["id"])
    with pytest.raises(ValueError, match="does not match"):
        apply_delegation(example_project, created["id"], token="wrong")
    accepted = apply_delegation(
        example_project,
        created["id"],
        token=proposed["approval_token"],
    )
    assert accepted["status"] == "rebuild-required"


def test_structural_safe_auto_proposal_is_downgraded_to_review(example_project) -> None:
    created = create_delegation(example_project, "Change a melody note", autonomy="safe-auto")
    proposed = propose_delegation(
        example_project,
        created["id"],
        {
            "summary": "Change one melody note",
            "actions": [
                {
                    "type": "update_event",
                    "part": "piano",
                    "measure": 3,
                    "voice": "v1",
                    "event_index": 0,
                    "changes": {"pitch": "B4"},
                }
            ],
        },
    )
    assert proposed["status"] == "proposed"
    assert proposed["effective_autonomy"] == "review"
    assert proposed["safe_auto"]["allowed"] is False
    assert proposed["approval_token"]


def test_delegation_rejects_proposal_after_authored_revision_changes(example_project) -> None:
    created = create_delegation(example_project, "Soften the piano")
    mix = example_project / "mix.yaml"
    mix.write_text(mix.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="project changed"):
        propose_delegation(
            example_project,
            created["id"],
            {
                "summary": "Softer piano",
                "actions": [
                    {
                        "type": "scale_velocity_range",
                        "part": "piano",
                        "measure_start": 1,
                        "measure_end": 2,
                        "factor": 0.9,
                    }
                ],
            },
        )


def test_studio_approved_delegation_queues_build_without_blocking_http(
    example_project,
    monkeypatch,
) -> None:
    started = threading.Event()
    release = threading.Event()

    def blocked_build(_self: LocalBuildCoordinator, context: JobContext) -> dict:
        started.set()
        if not release.wait(5):
            raise TimeoutError("test build was not released")
        context.check_cancelled()
        return {"status": "ok"}

    real_build_state = build_state

    def controlled_build_state(project) -> dict:
        state = real_build_state(project)
        if release.is_set():
            state["authored_revision"] = authored_revision(project)
            state["compiled_revision"] = "c" * 64
            state["rendered_revision"] = "r" * 64
            state["mix_revision"] = "m" * 64
            for name in ("compile", "render", "mix"):
                state["stages"][name] = {"status": "ready"}
        return state

    monkeypatch.setattr(LocalBuildCoordinator, "_build", blocked_build)
    monkeypatch.setattr(delegation_module, "build_state", controlled_build_state)
    created = create_delegation(example_project, "Soften the opening")
    proposed = propose_delegation(
        example_project,
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
        },
    )
    server = create_studio_server(example_project, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        with urlopen(f"{base}/api/model", timeout=5) as response:
            model = json.loads(response.read().decode("utf-8"))
        request = Request(
            f"{base}/api/delegations/{created['id']}/apply",
            data=json.dumps({"token": proposed["approval_token"]}).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-LedgerLine-Token": model["csrf_token"],
            },
            method="POST",
        )
        with urlopen(request, timeout=5) as response:
            queued = json.loads(response.read().decode("utf-8"))
            assert response.status == 202
        assert queued["status"] == "building"
        assert queued["result"]["source"]["status"] == "ok"
        job_id = queued["result"]["production"]["job_id"]
        assert job_id
        assert started.wait(5)

        release.set()
        deadline = time.monotonic() + 5
        task = show_delegation(example_project, created["id"])
        while task["status"] != "ready-for-listening":
            assert time.monotonic() < deadline
            time.sleep(0.01)
            task = show_delegation(example_project, created["id"])
        assert task["result"]["production"]["status"] == "ready-for-listening"
        assert task["result"]["production"]["job"]["id"] == job_id
    finally:
        release.set()
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_failed_delegation_build_is_recorded_on_the_task(example_project) -> None:
    def failed_build(_context: JobContext) -> dict:
        raise RuntimeError("renderer refused the authored engine")

    coordinator = LocalBuildCoordinator(
        example_project,
        runners={"build": failed_build},
        on_terminal=lambda job: finalize_delegation_job(example_project, job),
    )
    try:
        created = create_delegation(example_project, "Soften the opening")
        proposed = propose_delegation(
            example_project,
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
            },
        )
        accepted = apply_delegation(
            example_project,
            created["id"],
            token=proposed["approval_token"],
            coordinator=coordinator,
        )
        job_id = accepted["result"]["production"]["job_id"]
        assert job_id
        assert coordinator.wait(job_id, timeout=5)["status"] == "failed"
        failed = show_delegation(example_project, created["id"])
        assert failed["status"] == "build-failed"
        assert failed["result"]["production"]["error"] == {
            "type": "RuntimeError",
            "message": "renderer refused the authored engine",
        }
    finally:
        coordinator.close()


def test_studio_server_serves_model_with_csrf_and_static_index(example_project) -> None:
    server = create_studio_server(example_project, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        with urlopen(f"{base}/api/health", timeout=5) as response:
            health = json.loads(response.read().decode("utf-8"))
        assert health == {
            "schema_version": "1",
            "status": "ok",
            "project": str(example_project.resolve()),
            "revision": project_revision(example_project),
        }
        with urlopen(f"{base}/api/model", timeout=5) as response:
            model = json.loads(response.read().decode("utf-8"))
        assert model["csrf_token"]
        assert model["history"] == {"can_undo": False, "can_redo": False}
        assert model["contracts"]["command"] == "/api/schemas/studio-command"
        with urlopen(f"{base}/api/schemas/studio-command", timeout=5) as response:
            command_schema = json.loads(response.read().decode("utf-8"))
        assert "insert_event" in command_schema["properties"]["type"]["enum"]
        with urlopen(f"{base}/api/schemas/studio-state", timeout=5) as response:
            state_schema = json.loads(response.read().decode("utf-8"))
        assert state_schema["properties"]["schema_version"]["const"] == "2"
        with urlopen(f"{base}/", timeout=5) as response:
            index = response.read().decode("utf-8")
        assert "LedgerLine Studio" in index

        request = Request(
            f"{base}/api/jobs",
            data=json.dumps({"kind": "refresh"}).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-LedgerLine-Token": model["csrf_token"],
            },
            method="POST",
        )
        with urlopen(request, timeout=5) as response:
            job = json.loads(response.read().decode("utf-8"))
        deadline = time.monotonic() + 5
        while job["status"] not in {"ready", "failed", "cancelled"}:
            assert time.monotonic() < deadline
            with urlopen(f"{base}/api/jobs/{job['id']}", timeout=5) as response:
                job = json.loads(response.read().decode("utf-8"))
            time.sleep(0.01)
        assert job["status"] == "ready"

        with urlopen(f"{base}/api/status", timeout=5) as response:
            status = json.loads(response.read().decode("utf-8"))
        assert status["build"]["stages"]["compile"]["status"] == "ready"

        task = create_delegation(example_project, "Clarify the intended ending")
        waiting = propose_delegation(
            example_project,
            task["id"],
            {
                "summary": "Direction is needed",
                "reasoning": "Two endings fit the current draft.",
                "actions": [],
                "questions": ["Should the ending resolve or remain open?"],
                "listening_check": "Compare both cadence directions.",
            },
        )
        assert waiting["status"] == "needs-direction"
        answer_request = Request(
            f"{base}/api/delegations/{task['id']}/answer",
            data=json.dumps({"answer": "Resolve quietly to the tonic."}).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-LedgerLine-Token": model["csrf_token"],
            },
            method="POST",
        )
        with urlopen(answer_request, timeout=5) as response:
            answered = json.loads(response.read().decode("utf-8"))
        assert answered["status"] == "pending"
        assert answered["answers"][-1]["text"] == "Resolve quietly to the tonic."
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
