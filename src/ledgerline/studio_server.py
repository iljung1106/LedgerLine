from __future__ import annotations

import json
import mimetypes
import secrets
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from ledgerline.build_state import build_state, ensure_media_sidecar
from ledgerline.compiler import compile_project
from ledgerline.delegation import (
    accept_delegation,
    answer_delegation,
    apply_delegation,
    create_delegation,
    finalize_delegation_job,
    list_delegations,
    reject_delegation,
    revise_delegation,
)
from ledgerline.jobs import LocalBuildCoordinator
from ledgerline.studio_edits import StudioSession
from ledgerline.studio_model import build_review_impact, build_studio_model, project_revision


class LedgerLineStudioServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        project: Path,
        *,
        ffmpeg: str | Path | None = None,
        job_payload: dict[str, Any] | None = None,
    ):
        self.project = project
        self.session = StudioSession(project)
        self.jobs = LocalBuildCoordinator(
            project,
            ffmpeg=ffmpeg,
            default_payload=job_payload,
            explicit_audio_tools=True,
            on_terminal=lambda job: finalize_delegation_job(project, job),
        )
        self.csrf_token = secrets.token_urlsafe(32)
        self.static_root = Path(__file__).parent / "data" / "studio"
        super().__init__(address, _handler(self))

    def server_close(self) -> None:
        self.jobs.close()
        super().server_close()


def create_studio_server(
    project: str | Path,
    *,
    host: str = "127.0.0.1",
    port: int = 0,
    ffmpeg: str | Path | None = None,
    fluidsynth: str | Path | None = None,
    soundfont: str | Path | None = None,
) -> LedgerLineStudioServer:
    root = Path(project).resolve()
    job_payload = _studio_job_payload(
        root,
        ffmpeg=ffmpeg,
        fluidsynth=fluidsynth,
        soundfont=soundfont,
        require_audio=False,
    )
    compile_project(root)
    server = LedgerLineStudioServer(
        (host, port),
        root,
        ffmpeg=job_payload.get("ffmpeg"),
        job_payload=job_payload,
    )
    return server


def _studio_model(server: LedgerLineStudioServer) -> dict[str, Any]:
    model = build_studio_model(server.project)
    model["history"] = {
        "can_undo": server.session.can_undo,
        "can_redo": server.session.can_redo,
    }
    model["csrf_token"] = server.csrf_token
    model["contracts"] = {
        "model": "/api/schemas/studio-state",
        "command": "/api/schemas/studio-command",
    }
    return model


def run_studio(
    project: str | Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
    ffmpeg: str | Path | None = None,
    fluidsynth: str | Path | None = None,
    soundfont: str | Path | None = None,
    prepare: bool = False,
) -> dict[str, Any]:
    root = Path(project).resolve()
    job_payload = _studio_job_payload(
        root,
        ffmpeg=ffmpeg,
        fluidsynth=fluidsynth,
        soundfont=soundfont,
        require_audio=prepare,
    )
    prepared_job = None
    if prepare:
        coordinator = LocalBuildCoordinator(
            root,
            ffmpeg=job_payload.get("ffmpeg"),
            default_payload=job_payload,
            explicit_audio_tools=True,
        )
        try:
            prepared_job = coordinator.wait(
                coordinator.submit("build")["id"],
                timeout=1_200,
            )
        finally:
            coordinator.close()
        if prepared_job["status"] != "ready":
            message = (prepared_job.get("error") or {}).get("message", "Studio build failed")
            raise RuntimeError(message)
    prepare_studio_assets(root, ffmpeg=job_payload.get("ffmpeg"))
    server = create_studio_server(
        root,
        host=host,
        port=port,
        ffmpeg=job_payload.get("ffmpeg"),
        fluidsynth=job_payload.get("fluidsynth"),
        soundfont=job_payload.get("soundfont"),
    )
    actual_port = server.server_address[1]
    url = f"http://{host}:{actual_port}/"
    report = {
        "schema_version": "1",
        "status": "serving",
        "project": str(root),
        "url": url,
        "host": host,
        "port": actual_port,
        "prepared_job": prepared_job,
    }
    print(json.dumps(report, ensure_ascii=False), flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return {**report, "status": "stopped"}


def _studio_job_payload(
    project: Path,
    *,
    ffmpeg: str | Path | None,
    fluidsynth: str | Path | None,
    soundfont: str | Path | None,
    require_audio: bool,
) -> dict[str, str]:
    """Keep only explicitly supplied, existing engine assets for later Studio jobs."""

    supplied = {
        "ffmpeg": ffmpeg,
        "fluidsynth": fluidsynth,
        "soundfont": soundfont,
    }
    resolved: dict[str, str] = {}
    for name, value in supplied.items():
        if value is None:
            continue
        path = Path(value).resolve()
        if not path.is_file():
            raise ValueError(f"explicit Studio {name} path is not a file: {path}")
        resolved[name] = str(path)

    if require_audio:
        required = {"ffmpeg"}
        if not (project / "render.yaml").is_file():
            required.update({"fluidsynth", "soundfont"})
        missing = sorted(required - resolved.keys())
        if missing:
            flags = ", ".join(f"--{name}" for name in missing)
            mode = "render.yaml" if (project / "render.yaml").is_file() else "legacy"
            raise ValueError(
                f"Studio --prepare for a {mode} project requires explicit paths: {flags}; "
                "no renderer, instrument, or media tool is inferred"
            )
    return resolved


def prepare_studio_assets(
    project: str | Path, *, ffmpeg: str | Path | None = None, timeout: int = 180
) -> dict[str, Any]:
    root = Path(project).resolve()
    compile_project(root)
    build = root / "build"
    audio = build / "mix.wav" if (build / "mix.wav").is_file() else build / "preview.wav"
    if not audio.is_file():
        return {"status": "midi-only", "spectrogram": None}
    try:
        sidecar = ensure_media_sidecar(
            root,
            audio,
            ffmpeg=ffmpeg,
            spectrogram=True,
            timeout=timeout,
        )
    except Exception as exc:
        return {"status": "audio-ready", "spectrogram": None, "warning": str(exc)}
    status = "ok" if sidecar["spectrogram"] else "audio-ready"
    return {
        "status": status,
        "spectrogram": sidecar["spectrogram"],
        "source_sha256": sidecar["sha256"],
        "warning": sidecar["warning"],
    }


def _handler(server: LedgerLineStudioServer):
    class StudioHandler(BaseHTTPRequestHandler):
        server_version = "LedgerLineStudio/1"

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            try:
                if path == "/api/health":
                    self._json(
                        {
                            "schema_version": "1",
                            "status": "ok",
                            "project": str(server.project),
                            "revision": project_revision(server.project),
                        }
                    )
                elif path == "/api/model":
                    self._json(_studio_model(server))
                elif path == "/api/status":
                    self._json(
                        {
                            "schema_version": "1",
                            "status": "ok",
                            "build": build_state(server.project),
                            "jobs": server.jobs.list()["jobs"],
                        }
                    )
                elif path == "/api/review/impact":
                    self._json(build_review_impact(server.project))
                elif path == "/api/jobs":
                    self._json(server.jobs.list())
                elif path.startswith("/api/jobs/"):
                    segments = path.strip("/").split("/")
                    if len(segments) != 3:
                        raise ValueError("job route is invalid")
                    self._json(server.jobs.get(segments[2]))
                elif path == "/api/delegations":
                    self._json(list_delegations(server.project))
                elif path == "/api/schemas/studio-command":
                    self._file(
                        _schema_path("studio-command.schema.json"),
                        "application/schema+json",
                    )
                elif path == "/api/schemas/studio-state":
                    self._file(
                        _schema_path("studio-state.schema.json"),
                        "application/schema+json",
                    )
                elif path == "/api/score":
                    self._file(server.project / "build" / "score.musicxml", "application/xml")
                elif path.startswith("/media/"):
                    relative = unquote(path.removeprefix("/media/"))
                    self._safe_file(server.project / "build", relative)
                else:
                    relative = "index.html" if path in {"", "/"} else unquote(path.lstrip("/"))
                    self._safe_file(server.static_root, relative)
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                # Browsers may cancel an obsolete polling response after a newer model arrives.
                # The project is untouched and there is no client left to receive an error body.
                return
            except Exception as exc:
                self._error(exc)

        def do_POST(self) -> None:  # noqa: N802
            try:
                if self.headers.get("X-LedgerLine-Token") != server.csrf_token:
                    self._json({"status": "error", "message": "invalid Studio token"}, 403)
                    return
                path = urlparse(self.path).path
                payload = self._body()
                if path == "/api/commands":
                    commands = payload.get("commands")
                    if not isinstance(commands, list):
                        commands = [payload.get("command")]
                    report = server.session.apply(commands, revision=payload.get("revision"))
                    self._json({**report, "model": _studio_model(server)})
                elif path == "/api/undo":
                    report = server.session.undo()
                    self._json({**report, "model": _studio_model(server)})
                elif path == "/api/redo":
                    report = server.session.redo()
                    self._json({**report, "model": _studio_model(server)})
                elif path == "/api/jobs":
                    kind = payload.get("kind")
                    if not isinstance(kind, str) or not kind:
                        raise ValueError("job kind is required")
                    options = payload.get("payload", {})
                    if not isinstance(options, dict):
                        raise ValueError("job payload must be an object")
                    self._json(
                        server.jobs.submit(
                            kind,
                            options,
                            coalesce=bool(payload.get("coalesce", True)),
                        ),
                        202,
                    )
                elif path.startswith("/api/jobs/"):
                    segments = path.strip("/").split("/")
                    if len(segments) != 4 or segments[3] != "cancel":
                        raise ValueError("job action route is invalid")
                    self._json(server.jobs.cancel(segments[2]))
                elif path == "/api/delegations":
                    self._json(
                        create_delegation(
                            server.project,
                            str(payload.get("goal", "")),
                            autonomy=str(payload.get("autonomy", "review")),
                            context=str(payload.get("context", "")),
                            constraints=list(payload.get("constraints", [])),
                        ),
                        201,
                    )
                elif path.startswith("/api/delegations/"):
                    self._delegation_action(path, payload)
                else:
                    self._json({"status": "error", "message": "route not found"}, 404)
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                return
            except Exception as exc:
                self._error(exc)

        def _delegation_action(self, path: str, payload: dict[str, Any]) -> None:
            segments = path.strip("/").split("/")
            if len(segments) != 4:
                raise ValueError("delegation action route is invalid")
            task_id, action = segments[2], segments[3]
            if action == "apply":
                report = apply_delegation(
                    server.project,
                    task_id,
                    token=payload.get("token"),
                    session=server.session,
                    coordinator=server.jobs,
                )
            elif action == "reject":
                report = reject_delegation(server.project, task_id, str(payload.get("reason", "")))
            elif action == "answer":
                report = answer_delegation(
                    server.project,
                    task_id,
                    str(payload.get("answer", "")),
                )
            elif action == "accept":
                report = accept_delegation(
                    server.project,
                    task_id,
                    str(payload.get("note", "")),
                )
            elif action == "revise":
                report = revise_delegation(
                    server.project,
                    task_id,
                    str(payload.get("feedback", "")),
                )
            else:
                raise ValueError("delegation action is unsupported")
            self._json(report, 202 if report.get("status") == "building" else 200)

        def _body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 1_000_000:
                raise ValueError("request body is too large")
            raw = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(raw, dict):
                raise ValueError("request body must be a JSON object")
            return raw

        def _safe_file(self, root: Path, relative: str) -> None:
            target = (root / relative).resolve()
            resolved_root = root.resolve()
            if target != resolved_root and resolved_root not in target.parents:
                self._json({"status": "error", "message": "path is outside Studio root"}, 403)
                return
            self._file(target)

        def _file(self, path: Path, content_type: str | None = None) -> None:
            if not path.is_file():
                self._json({"status": "error", "message": "file not found"}, 404)
                return
            data = path.read_bytes()
            kind = content_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(data)))
            cache_control = (
                "no-store"
                if path.name == "index.html"
                else "public, max-age=31536000, immutable"
                if urlparse(self.path).query.startswith("v=")
                else "public, max-age=60"
            )
            self.send_header("Cache-Control", cache_control)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; "
                "script-src 'self'; "
                "style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data:; "
                "media-src 'self'; "
                "connect-src 'self'",
            )
            self.end_headers()
            self.wfile.write(data)

        def _json(self, payload: dict[str, Any], status: int = 200) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        def _error(self, exc: Exception) -> None:
            self._json({"status": "error", "message": str(exc)}, 400)

        def log_message(self, format: str, *args: object) -> None:
            return

    return StudioHandler


def _schema_path(name: str) -> Path:
    bundled = Path(__file__).parent / "data" / "schemas" / name
    if bundled.is_file():
        return bundled
    return Path(__file__).parents[2] / "schemas" / name
