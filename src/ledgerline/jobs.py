from __future__ import annotations

import json
import os
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ledgerline.build_state import build_state
from ledgerline.external_process import ExternalProcessCancelled

TERMINAL_STATUSES = {"ready", "failed", "cancelled"}
JOB_STATUSES = {"queued", "running", *TERMINAL_STATUSES}
_AUDIO_TOOL_KEYS = frozenset({"ffmpeg", "fluidsynth", "soundfont"})


class JobCancelled(Exception):
    pass


@dataclass(slots=True)
class JobContext:
    project: Path
    payload: dict[str, Any]
    cancelled: threading.Event
    _progress: Callable[[float, str], None]

    def progress(self, value: float, message: str) -> None:
        if self.cancelled.is_set():
            raise JobCancelled("job was cancelled")
        self._progress(max(0.0, min(1.0, float(value))), str(message))

    def check_cancelled(self) -> None:
        if self.cancelled.is_set():
            raise JobCancelled("job was cancelled")


Runner = Callable[[JobContext], dict[str, Any]]
TerminalListener = Callable[[dict[str, Any]], None]


class LocalBuildCoordinator:
    """One-project FIFO worker for long Studio build operations."""

    def __init__(
        self,
        project: str | Path,
        *,
        ffmpeg: str | Path | None = None,
        default_payload: dict[str, Any] | None = None,
        explicit_audio_tools: bool = False,
        runners: dict[str, Runner] | None = None,
        on_terminal: TerminalListener | None = None,
    ) -> None:
        self.project = Path(project).resolve()
        self.ffmpeg = str(Path(ffmpeg).resolve()) if ffmpeg else None
        if default_payload is not None and not isinstance(default_payload, dict):
            raise ValueError("default job payload must be an object")
        self._default_payload = dict(default_payload or {})
        self._explicit_audio_tools = bool(explicit_audio_tools)
        self._jobs: dict[str, dict[str, Any]] = {}
        self._queue: deque[str] = deque()
        self._cancellation: dict[str, threading.Event] = {}
        self._condition = threading.Condition(threading.RLock())
        self._closed = False
        self._on_terminal = on_terminal
        self._runners = {
            "compile": self._compile,
            "render": self._render,
            "mix": self._mix,
            "refine": self._refine,
            "build": self._build,
            "refresh": self._refresh,
            **(runners or {}),
        }
        recovered = self._recover()
        if recovered:
            self._persist_locked()
            for job in recovered:
                self._notify_terminal(job)
        self._worker = threading.Thread(
            target=self._work,
            name=f"ledgerline-build-{self.project.name}",
            daemon=True,
        )
        self._worker.start()

    def submit(
        self,
        kind: str,
        payload: dict[str, Any] | None = None,
        *,
        coalesce: bool = True,
    ) -> dict[str, Any]:
        if kind not in self._runners:
            raise ValueError(f"unsupported build job: {kind}")
        if payload is not None and not isinstance(payload, dict):
            raise ValueError("job payload must be an object")
        requested = dict(payload or {})
        if self._explicit_audio_tools:
            for key in sorted(_AUDIO_TOOL_KEYS & requested.keys()):
                if requested[key] != self._default_payload.get(key):
                    raise ValueError(
                        f"job payload cannot override the Studio-approved {key} path"
                    )
        superseded: list[dict[str, Any]] = []
        with self._condition:
            if self._closed:
                raise RuntimeError("build coordinator is closed")
            if coalesce:
                for queued_id in tuple(self._queue):
                    queued = self._jobs[queued_id]
                    if queued["kind"] == kind and queued["status"] == "queued":
                        queued["status"] = "cancelled"
                        queued["message"] = "superseded by a newer request"
                        queued["finished_at"] = _now()
                        self._queue.remove(queued_id)
                        self._append_event(queued, queued["progress"], queued["message"])
                        superseded.append(_public_job(queued))
            job_id = uuid.uuid4().hex
            job = {
                "schema_version": "1",
                "id": job_id,
                "kind": kind,
                "status": "queued",
                "progress": 0.0,
                "message": "queued",
                # Runtime engine defaults are intentionally not persisted into
                # build/jobs.json; receipts record the engine identities after work.
                "payload": requested,
                "created_at": _now(),
                "started_at": None,
                "finished_at": None,
                "result": None,
                "error": None,
                "events": [{"at": _now(), "progress": 0.0, "message": "queued"}],
            }
            self._jobs[job_id] = job
            self._queue.append(job_id)
            self._cancellation[job_id] = threading.Event()
            self._persist_locked()
            self._condition.notify()
            public = _public_job(job)
        for terminal in superseded:
            self._notify_terminal(terminal)
        return public

    def get(self, job_id: str) -> dict[str, Any]:
        with self._condition:
            if job_id not in self._jobs:
                raise KeyError(f"unknown build job: {job_id}")
            return _public_job(self._jobs[job_id])

    def list(self) -> dict[str, Any]:
        with self._condition:
            jobs = sorted(self._jobs.values(), key=lambda item: item["created_at"], reverse=True)
            return {
                "schema_version": "1",
                "status": "ok",
                "jobs": [_public_job(job) for job in jobs],
            }

    def cancel(self, job_id: str) -> dict[str, Any]:
        terminal = False
        with self._condition:
            if job_id not in self._jobs:
                raise KeyError(f"unknown build job: {job_id}")
            job = self._jobs[job_id]
            if job["status"] in TERMINAL_STATUSES:
                return _public_job(job)
            self._cancellation.setdefault(job_id, threading.Event()).set()
            if job["status"] == "queued":
                if job_id in self._queue:
                    self._queue.remove(job_id)
                job["status"] = "cancelled"
                job["message"] = "cancelled before start"
                job["finished_at"] = _now()
                self._append_event(job, job["progress"], job["message"])
                terminal = True
            else:
                job["message"] = "cancellation requested"
                self._append_event(job, job["progress"], job["message"])
            self._persist_locked()
            self._condition.notify_all()
            public = _public_job(job)
        if terminal:
            self._notify_terminal(public)
        return public

    def wait(self, job_id: str, timeout: float = 30.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        with self._condition:
            while True:
                job = self._jobs.get(job_id)
                if job is None:
                    raise KeyError(f"unknown build job: {job_id}")
                if job["status"] in TERMINAL_STATUSES:
                    return _public_job(job)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"build job did not finish: {job_id}")
                self._condition.wait(min(remaining, 0.25))

    def close(self, timeout: float = 5.0) -> None:
        with self._condition:
            self._closed = True
            for job_id, job in self._jobs.items():
                if job["status"] in {"queued", "running"}:
                    self._cancellation.setdefault(job_id, threading.Event()).set()
            self._condition.notify_all()
        if threading.current_thread() is not self._worker:
            self._worker.join(timeout=timeout)

    def _work(self) -> None:
        while True:
            with self._condition:
                while not self._queue and not self._closed:
                    self._condition.wait()
                if self._closed:
                    return
                job_id = self._queue.popleft()
                job = self._jobs[job_id]
                if job["status"] != "queued":
                    continue
                job["status"] = "running"
                job["started_at"] = _now()
                job["message"] = "running"
                self._append_event(job, 0.01, "running")
                self._persist_locked()
                cancellation = self._cancellation[job_id]
            context = JobContext(
                self.project,
                {**self._default_payload, **dict(job["payload"])},
                cancellation,
                lambda value, message, current=job_id: self._update(current, value, message),
            )
            try:
                result = self._runners[job["kind"]](context)
                context.check_cancelled()
            except (JobCancelled, ExternalProcessCancelled) as exc:
                self._finish(job_id, "cancelled", message=str(exc))
            except Exception as exc:
                self._finish(
                    job_id,
                    "failed",
                    message="build failed",
                    error={"type": type(exc).__name__, "message": str(exc)},
                )
            else:
                self._finish(job_id, "ready", message="ready", result=result)

    def _compile(self, context: JobContext) -> dict[str, Any]:
        from ledgerline.compiler import compile_project

        context.progress(0.1, "compiling score")
        report = compile_project(context.project)
        context.progress(0.9, "recording compiled artifacts")
        return {"compile": report, "state": build_state(context.project)}

    def _render(self, context: JobContext) -> dict[str, Any]:
        from ledgerline.render import render_project

        self._require_explicit_audio_tools(context, "render")
        state = build_state(context.project)
        if state["stages"]["compile"]["status"] != "ready":
            self._compile(context)
        context.progress(0.2, "rendering instrument stems")
        payload = context.payload
        report = render_project(
            context.project,
            fluidsynth=payload.get("fluidsynth"),
            soundfont=payload.get("soundfont"),
            sample_rate=int(payload.get("sample_rate", 48_000)),
            ffmpeg=payload.get("ffmpeg", self.ffmpeg),
            timeout=int(payload.get("timeout", 300)),
            cancel_event=context.cancelled,
        )
        context.progress(0.9, "recording render provenance")
        return {"render": report, "state": build_state(context.project)}

    def _mix(self, context: JobContext) -> dict[str, Any]:
        from ledgerline.mixer import mix_project

        self._require_explicit_audio_tools(context, "mix")
        context.progress(0.15, "mixing rendered stems")
        report = mix_project(
            context.project,
            ffmpeg=context.payload.get("ffmpeg", self.ffmpeg),
            timeout=int(context.payload.get("timeout", 300)),
            cancel_event=context.cancelled,
        )
        context.progress(0.9, "recording master provenance")
        return {"mix": report, "state": build_state(context.project)}

    def _refine(self, context: JobContext) -> dict[str, Any]:
        from ledgerline.refinement import build_refinement_report

        context.progress(0.1, "analyzing authored music")
        output = context.project / "build" / "refinement" / "report.json"
        report = build_refinement_report(context.project, output)
        context.progress(0.9, "recording refinement report freshness")
        return {"refinement": report, "state": build_state(context.project)}

    def _build(self, context: JobContext) -> dict[str, Any]:
        self._require_explicit_audio_tools(context, "build")
        context.progress(0.02, "starting build")
        compile_result = self._compile(_scaled_context(context, 0.02, 0.25))
        context.check_cancelled()
        render_result = self._render(_scaled_context(context, 0.25, 0.75))
        context.check_cancelled()
        mix_result = self._mix(_scaled_context(context, 0.75, 1.0))
        return {"compile": compile_result, "render": render_result, "mix": mix_result}

    def _require_explicit_audio_tools(self, context: JobContext, kind: str) -> None:
        if not self._explicit_audio_tools:
            return
        required = {"ffmpeg"}
        if kind in {"render", "build"} and not (context.project / "render.yaml").is_file():
            required.update({"fluidsynth", "soundfont"})
        missing = [
            key
            for key in sorted(required)
            if not isinstance(context.payload.get(key), str)
            or not Path(context.payload[key]).is_file()
        ]
        if missing:
            flags = ", ".join(f"--{key}" for key in missing)
            raise ValueError(
                f"Studio {kind} requires approved explicit paths: {flags}; "
                "implicit environment discovery is disabled"
            )

    def _refresh(self, context: JobContext) -> dict[str, Any]:
        context.progress(0.5, "checking artifact receipts")
        return {"state": build_state(context.project)}

    def _update(self, job_id: str, progress: float, message: str) -> None:
        with self._condition:
            job = self._jobs[job_id]
            if job["status"] != "running":
                return
            job["progress"] = progress
            job["message"] = message
            self._append_event(job, progress, message)
            self._persist_locked()
            self._condition.notify_all()

    def _finish(
        self,
        job_id: str,
        status: str,
        *,
        message: str,
        result: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
    ) -> None:
        with self._condition:
            job = self._jobs[job_id]
            job["status"] = status
            job["progress"] = 1.0 if status == "ready" else job["progress"]
            job["message"] = message
            job["result"] = result
            job["error"] = error
            job["finished_at"] = _now()
            self._append_event(job, job["progress"], message)
            self._persist_locked()
            self._condition.notify_all()
            public = _public_job(job)
        self._notify_terminal(public)

    def _notify_terminal(self, job: dict[str, Any]) -> None:
        if self._on_terminal is None:
            return
        try:
            self._on_terminal(job)
        except Exception:
            # Delegation reconciliation also runs on reads, so a notification
            # failure must not turn a successful production build into a failed job.
            return

    @staticmethod
    def _append_event(job: dict[str, Any], progress: float, message: str) -> None:
        job["events"].append({"at": _now(), "progress": progress, "message": message})
        del job["events"][:-100]

    def _recover(self) -> list[dict[str, Any]]:
        recovered: list[dict[str, Any]] = []
        raw = _read_json(self.project / "build" / "jobs.json")
        for job in raw.get("jobs", []) if raw else []:
            if not isinstance(job, dict) or not isinstance(job.get("id"), str):
                continue
            if job.get("status") not in JOB_STATUSES:
                continue
            if job["status"] in {"queued", "running"}:
                job["status"] = "failed"
                job["message"] = "Studio stopped before the job completed"
                job["finished_at"] = _now()
                job["error"] = {"type": "Interrupted", "message": job["message"]}
                recovered.append(_public_job(job))
            self._jobs[job["id"]] = job
        return recovered

    def _persist_locked(self) -> None:
        path = self.project / "build" / "jobs.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps(
                {"schema_version": "1", "jobs": list(self._jobs.values())},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)


def _scaled_context(context: JobContext, start: float, end: float) -> JobContext:
    return JobContext(
        context.project,
        context.payload,
        context.cancelled,
        lambda value, message: context.progress(start + ((end - start) * value), message),
    )


def _public_job(job: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(job, ensure_ascii=False))


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else None
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def _now() -> str:
    return datetime.now(UTC).isoformat()
