from __future__ import annotations

import time
from pathlib import Path

from ledgerline.jobs import JobContext, LocalBuildCoordinator


def test_compile_job_runs_in_background_and_publishes_state(example_project: Path) -> None:
    coordinator = LocalBuildCoordinator(example_project)
    try:
        submitted = coordinator.submit("compile")
        completed = coordinator.wait(submitted["id"], timeout=10)
        assert completed["status"] == "ready"
        assert completed["progress"] == 1.0
        assert completed["result"]["state"]["stages"]["compile"]["status"] == "ready"
    finally:
        coordinator.close()


def test_refine_job_writes_a_revision_bound_report(example_project: Path) -> None:
    coordinator = LocalBuildCoordinator(example_project)
    try:
        submitted = coordinator.submit("refine")
        completed = coordinator.wait(submitted["id"], timeout=10)
        assert completed["status"] == "ready"
        assert completed["result"]["state"]["stages"]["refinement"]["status"] == "ready"
        report = example_project / "build" / "refinement" / "report.json"
        assert report.is_file()
        assert completed["result"]["refinement"]["authored_revision"]
    finally:
        coordinator.close()


def test_running_reference_job_can_be_cancelled_without_external_tools(
    example_project: Path,
) -> None:
    def slow_reference(context: JobContext) -> dict:
        for index in range(200):
            context.progress(index / 200, "reference render")
            time.sleep(0.005)
        return {"status": "ok"}

    coordinator = LocalBuildCoordinator(example_project, runners={"reference": slow_reference})
    try:
        submitted = coordinator.submit("reference")
        deadline = time.monotonic() + 5
        while coordinator.get(submitted["id"])["status"] != "running":
            assert time.monotonic() < deadline
            time.sleep(0.005)
        coordinator.cancel(submitted["id"])
        completed = coordinator.wait(submitted["id"], timeout=5)
        assert completed["status"] == "cancelled"
        assert any("cancellation" in event["message"] for event in completed["events"])
    finally:
        coordinator.close()


def test_coordinator_merges_trusted_default_payload_with_per_job_options(
    example_project: Path,
) -> None:
    observed: list[dict] = []

    def capture(context: JobContext) -> dict:
        observed.append(dict(context.payload))
        return {"status": "ok"}

    coordinator = LocalBuildCoordinator(
        example_project,
        default_payload={"ffmpeg": "C:/approved/ffmpeg.exe", "timeout": 300},
        runners={"capture": capture},
    )
    try:
        submitted = coordinator.submit("capture", {"timeout": 45})
        assert submitted["payload"] == {"timeout": 45}
        assert coordinator.wait(submitted["id"], timeout=5)["status"] == "ready"
        assert observed == [{"ffmpeg": "C:/approved/ffmpeg.exe", "timeout": 45}]
    finally:
        coordinator.close()


def test_queued_jobs_of_the_same_kind_are_coalesced(example_project: Path) -> None:
    def blocker(context: JobContext) -> dict:
        while not context.cancelled.wait(0.01):
            context.progress(0.2, "blocked")
        context.check_cancelled()
        return {}

    terminal: list[dict] = []
    coordinator = LocalBuildCoordinator(
        example_project,
        runners={"reference": blocker},
        on_terminal=terminal.append,
    )
    try:
        active = coordinator.submit("reference")
        deadline = time.monotonic() + 5
        while coordinator.get(active["id"])["status"] != "running":
            assert time.monotonic() < deadline
            time.sleep(0.005)
        old = coordinator.submit("compile")
        new = coordinator.submit("compile")
        assert coordinator.get(old["id"])["status"] == "cancelled"
        assert any(job["id"] == old["id"] and job["status"] == "cancelled" for job in terminal)
        assert coordinator.get(new["id"])["status"] == "queued"
        coordinator.cancel(active["id"])
        coordinator.wait(active["id"], timeout=5)
        assert coordinator.wait(new["id"], timeout=10)["status"] == "ready"
    finally:
        coordinator.close()
