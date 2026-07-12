from __future__ import annotations

import json
import mimetypes
import secrets
import subprocess
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from ledgerline.compiler import compile_project
from ledgerline.delegation import (
    apply_delegation,
    create_delegation,
    list_delegations,
    reject_delegation,
)
from ledgerline.studio_edits import StudioSession
from ledgerline.studio_model import build_studio_model


class LedgerLineStudioServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], project: Path):
        self.project = project
        self.session = StudioSession(project)
        self.csrf_token = secrets.token_urlsafe(32)
        self.static_root = Path(__file__).parent / "data" / "studio"
        super().__init__(address, _handler(self))


def create_studio_server(
    project: str | Path, *, host: str = "127.0.0.1", port: int = 0
) -> LedgerLineStudioServer:
    root = Path(project).resolve()
    compile_project(root)
    server = LedgerLineStudioServer((host, port), root)
    return server


def _studio_model(server: LedgerLineStudioServer) -> dict[str, Any]:
    model = build_studio_model(server.project)
    model["history"] = {
        "can_undo": server.session.can_undo,
        "can_redo": server.session.can_redo,
    }
    model["csrf_token"] = server.csrf_token
    return model


def run_studio(
    project: str | Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
    ffmpeg: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(project).resolve()
    prepare_studio_assets(root, ffmpeg=ffmpeg)
    server = create_studio_server(root, host=host, port=port)
    actual_port = server.server_address[1]
    url = f"http://{host}:{actual_port}/"
    report = {
        "schema_version": "1",
        "status": "serving",
        "project": str(root),
        "url": url,
        "host": host,
        "port": actual_port,
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


def prepare_studio_assets(
    project: str | Path, *, ffmpeg: str | Path | None = None, timeout: int = 180
) -> dict[str, Any]:
    root = Path(project).resolve()
    compile_project(root)
    build = root / "build"
    audio = build / "mix.wav" if (build / "mix.wav").is_file() else build / "preview.wav"
    output = build / "studio" / "spectrogram.png"
    if not audio.is_file():
        return {"status": "midi-only", "spectrogram": None}
    executable = None
    if ffmpeg:
        candidate = Path(ffmpeg).resolve()
        executable = candidate if candidate.is_file() else None
    if executable is None:
        try:
            from ledgerline.audio import resolve_ffmpeg

            executable = resolve_ffmpeg()
        except Exception:
            return {"status": "audio-ready", "spectrogram": None}
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        str(executable),
        "-hide_banner",
        "-y",
        "-i",
        str(audio),
        "-lavfi",
        "showspectrumpic=s=2400x320:legend=disabled:color=fiery:scale=log",
        "-frames:v",
        "1",
        str(output),
    ]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        shell=False,
    )
    if completed.returncode != 0 or not output.is_file():
        return {"status": "audio-ready", "spectrogram": None, "warning": completed.stderr[-1000:]}
    return {"status": "ok", "spectrogram": str(output)}


def _handler(server: LedgerLineStudioServer):
    class StudioHandler(BaseHTTPRequestHandler):
        server_version = "LedgerLineStudio/1"

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            try:
                if path == "/api/health":
                    self._json({"status": "ok"})
                elif path == "/api/model":
                    self._json(_studio_model(server))
                elif path == "/api/delegations":
                    self._json(list_delegations(server.project))
                elif path == "/api/score":
                    self._file(server.project / "build" / "score.musicxml", "application/xml")
                elif path.startswith("/media/"):
                    relative = unquote(path.removeprefix("/media/"))
                    self._safe_file(server.project / "build", relative)
                else:
                    relative = "index.html" if path in {"", "/"} else unquote(path.lstrip("/"))
                    self._safe_file(server.static_root, relative)
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
                )
            elif action == "reject":
                report = reject_delegation(server.project, task_id, str(payload.get("reason", "")))
            else:
                raise ValueError("delegation action is unsupported")
            self._json(report)

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
                "no-store" if path.name == "index.html" else "public, max-age=60"
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
