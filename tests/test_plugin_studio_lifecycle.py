from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
STUDIO_SCRIPT = ROOT / "plugins" / "ledgerline" / "scripts" / "studio.ps1"
PWSH = shutil.which("pwsh")

pytestmark = pytest.mark.skipif(
    os.name != "nt" or PWSH is None,
    reason="the plugin lifecycle wrapper is Windows PowerShell specific",
)


def _environment(tmp_path: Path, runtime: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment["LEDGERLINE_RUNTIME_PYTHON"] = str(runtime)
    environment["LEDGERLINE_HOME"] = str(tmp_path / "ledgerline-home")
    return environment


def _run_studio(
    project: Path,
    tmp_path: Path,
    *arguments: str,
    runtime: Path | None = None,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    runtime = runtime or Path(sys.executable)
    return subprocess.run(
        [
            str(PWSH),
            "-NoProfile",
            "-File",
            str(STUDIO_SCRIPT),
            "-Project",
            str(project),
            *arguments,
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        shell=False,
        env=environment or _environment(tmp_path, runtime),
    )


def _fake_runtime(tmp_path: Path) -> tuple[Path, Path]:
    runtime = tmp_path / "fake runtime.ps1"
    call_log = tmp_path / "runtime-calls.jsonl"
    runtime.write_text(
        """$serialized = ConvertTo-Json -Compress -InputObject @($args)
[System.IO.File]::AppendAllText(
    $env:LEDGERLINE_FAKE_CALL_LOG,
    $serialized + [Environment]::NewLine
)
Write-Output '{"status":"ok"}'
exit 0
""",
        encoding="utf-8",
    )
    return runtime, call_log


def _tool_files(tmp_path: Path) -> tuple[Path, Path, Path]:
    fluidsynth = tmp_path / "FluidSynth tools" / "fluidsynth.exe"
    soundfont = tmp_path / "SoundFonts" / "fixture.sf2"
    ffmpeg = tmp_path / "FFmpeg tools" / "ffmpeg.exe"
    for path in (fluidsynth, soundfont, ffmpeg):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture")
    return fluidsynth, soundfont, ffmpeg


def _calls(path: Path) -> list[list[str]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _record_live_process(project: Path, url: str) -> None:
    completed = subprocess.run(
        [
            str(PWSH),
            "-NoProfile",
            "-Command",
            (
                f"Get-Process -Id {os.getpid()} | Select-Object Path, "
                "@{Name='Start'; Expression={$_.StartTime.ToUniversalTime().ToString("
                "'o', [System.Globalization.CultureInfo]::InvariantCulture)}} | ConvertTo-Json"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    process = json.loads(completed.stdout)
    executable = str(Path(process["Path"]).resolve())
    control = project / ".ledgerline"
    control.mkdir(parents=True, exist_ok=True)
    (control / "studio-process.json").write_text(
        json.dumps(
            {
                "schema_version": "1",
                "pid": os.getpid(),
                "project": str(project.resolve()),
                "url": url,
                "process_start_time_utc": process["Start"],
                "runtime_path": executable,
                "process_executable_path": executable,
            }
        ),
        encoding="utf-8",
    )


@contextmanager
def _health_endpoint(project: Path) -> Iterator[int]:
    class HealthHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path != "/api/health":
                self.send_error(404)
                return
            body = json.dumps({"status": "ok", "project": str(project.resolve())}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *args: object) -> None:
            del args

    server = ThreadingHTTPServer(("127.0.0.1", 0), HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield int(server.server_address[1])
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_legacy_rebuild_fails_before_compile_without_explicit_audio_paths(
    tmp_path: Path,
) -> None:
    project = tmp_path / "legacy"
    project.mkdir()
    environment = _environment(tmp_path, Path(sys.executable))
    environment["LEDGERLINE_FLUIDSYNTH"] = str(tmp_path / "must-not-be-inferred.exe")
    completed = _run_studio(
        project,
        tmp_path,
        "-Action",
        "Rebuild",
        environment=environment,
    )
    combined = completed.stdout + completed.stderr
    assert completed.returncode != 0
    assert "requires explicit -FluidSynth, -SoundFont, and -FFmpeg" in combined
    assert "No renderer, instrument, or media tool was inferred or substituted" in combined
    assert not (project / "build").exists()


def test_legacy_rebuild_forwards_exact_paths_to_render_and_mix(tmp_path: Path) -> None:
    project = tmp_path / "legacy project"
    project.mkdir()
    (project / "mix.yaml").write_text("format: 2\n", encoding="utf-8")
    runtime, call_log = _fake_runtime(tmp_path)
    fluidsynth, soundfont, ffmpeg = _tool_files(tmp_path)
    environment = _environment(tmp_path, runtime)
    environment["LEDGERLINE_FAKE_CALL_LOG"] = str(call_log)

    completed = _run_studio(
        project,
        tmp_path,
        "-Action",
        "Rebuild",
        "-FluidSynth",
        str(fluidsynth),
        "-SoundFont",
        str(soundfont),
        "-FFmpeg",
        str(ffmpeg),
        runtime=runtime,
        environment=environment,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["status"] == "ready"
    calls = _calls(call_log)
    assert calls[0][2:] == ["compile", str(project.resolve()), "--json"]
    assert calls[1][2:] == [
        "render",
        str(project.resolve()),
        "--fluidsynth",
        str(fluidsynth.resolve()),
        "--soundfont",
        str(soundfont.resolve()),
        "--ffmpeg",
        str(ffmpeg.resolve()),
        "--json",
    ]
    assert calls[2][2:] == [
        "mix",
        str(project.resolve()),
        "--ffmpeg",
        str(ffmpeg.resolve()),
        "--json",
    ]


def test_render_graph_rebuild_forwards_explicit_ffmpeg_only(tmp_path: Path) -> None:
    project = tmp_path / "graph"
    project.mkdir()
    (project / "render.yaml").write_text("format: 1\n", encoding="utf-8")
    (project / "mix.yaml").write_text("format: 2\n", encoding="utf-8")
    runtime, call_log = _fake_runtime(tmp_path)
    _, _, ffmpeg = _tool_files(tmp_path)
    environment = _environment(tmp_path, runtime)
    environment["LEDGERLINE_FAKE_CALL_LOG"] = str(call_log)

    completed = _run_studio(
        project,
        tmp_path,
        "-Action",
        "Rebuild",
        "-FFmpeg",
        str(ffmpeg),
        runtime=runtime,
        environment=environment,
    )
    assert completed.returncode == 0, completed.stderr
    calls = _calls(call_log)
    assert calls[1][2:] == [
        "render",
        str(project.resolve()),
        "--ffmpeg",
        str(ffmpeg.resolve()),
        "--json",
    ]
    assert "--fluidsynth" not in calls[1]
    assert calls[2][2:] == [
        "mix",
        str(project.resolve()),
        "--ffmpeg",
        str(ffmpeg.resolve()),
        "--json",
    ]


def test_render_graph_rebuild_never_infers_ffmpeg(tmp_path: Path) -> None:
    project = tmp_path / "graph-with-mix"
    project.mkdir()
    (project / "render.yaml").write_text("format: 1\n", encoding="utf-8")
    (project / "mix.yaml").write_text("format: 2\n", encoding="utf-8")
    environment = _environment(tmp_path, Path(sys.executable))
    environment["LEDGERLINE_FFMPEG"] = str(tmp_path / "must-not-be-inferred.exe")

    completed = _run_studio(
        project,
        tmp_path,
        "-Action",
        "Rebuild",
        environment=environment,
    )
    combined = completed.stdout + completed.stderr
    assert completed.returncode != 0
    assert "requires explicit -FFmpeg" in combined
    assert "unmanaged FFmpeg" in combined
    assert not (project / "build").exists()


def test_rebuild_running_project_fails_before_any_runtime_command(tmp_path: Path) -> None:
    project = tmp_path / "running project"
    project.mkdir()
    runtime, call_log = _fake_runtime(tmp_path)
    fluidsynth, soundfont, ffmpeg = _tool_files(tmp_path)
    environment = _environment(tmp_path, runtime)
    environment["LEDGERLINE_FAKE_CALL_LOG"] = str(call_log)

    with _health_endpoint(project) as port:
        _record_live_process(project, f"http://127.0.0.1:{port}/")
        completed = _run_studio(
            project,
            tmp_path,
            "-Action",
            "Rebuild",
            "-Port",
            str(port + 1),
            "-FluidSynth",
            str(fluidsynth),
            "-SoundFont",
            str(soundfont),
            "-FFmpeg",
            str(ffmpeg),
            runtime=runtime,
            environment=environment,
        )

    combined = completed.stdout + completed.stderr
    assert completed.returncode != 0
    assert "A healthy Studio is already running for this project" in combined
    assert "No compile, render, or mix command was started" in combined
    assert not call_log.exists()


def test_rebuild_ignores_health_endpoint_for_a_different_project(tmp_path: Path) -> None:
    project = tmp_path / "requested project"
    other_project = tmp_path / "different project"
    project.mkdir()
    other_project.mkdir()
    runtime, call_log = _fake_runtime(tmp_path)
    fluidsynth, soundfont, ffmpeg = _tool_files(tmp_path)
    environment = _environment(tmp_path, runtime)
    environment["LEDGERLINE_FAKE_CALL_LOG"] = str(call_log)

    with _health_endpoint(other_project) as port:
        completed = _run_studio(
            project,
            tmp_path,
            "-Action",
            "Rebuild",
            "-Port",
            str(port),
            "-FluidSynth",
            str(fluidsynth),
            "-SoundFont",
            str(soundfont),
            "-FFmpeg",
            str(ffmpeg),
            runtime=runtime,
            environment=environment,
        )

    assert completed.returncode == 0, completed.stderr
    assert _calls(call_log)[0][2:] == ["compile", str(project.resolve()), "--json"]


def test_start_reuses_recorded_project_studio_on_a_different_requested_port(
    tmp_path: Path,
) -> None:
    project = tmp_path / "single studio project"
    project.mkdir()
    runtime, call_log = _fake_runtime(tmp_path)
    environment = _environment(tmp_path, runtime)
    environment["LEDGERLINE_FAKE_CALL_LOG"] = str(call_log)

    with _health_endpoint(project) as running_port:
        running_url = f"http://127.0.0.1:{running_port}/"
        _record_live_process(project, running_url)
        status = _run_studio(
            project,
            tmp_path,
            "-Action",
            "Status",
            "-Port",
            str(running_port + 1),
            runtime=runtime,
            environment=environment,
        )
        assert status.returncode == 0, status.stderr
        status_report = json.loads(status.stdout)
        assert status_report["status"] == "running", json.dumps(status_report, indent=2)
        assert status_report["url"] == running_url
        completed = _run_studio(
            project,
            tmp_path,
            "-Action",
            "Start",
            "-Port",
            str(running_port + 1),
            runtime=runtime,
            environment=environment,
        )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert report["status"] == "running"
    assert report["reused"] is True
    assert report["url"] == running_url
    assert not call_log.exists()


def test_stop_reports_stale_identity_and_does_not_kill_reused_pid(tmp_path: Path) -> None:
    project = tmp_path / "stale"
    control = project / ".ledgerline"
    control.mkdir(parents=True)
    unrelated = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        state = {
            "schema_version": "1",
            "pid": unrelated.pid,
            "project": str(project),
            "url": "http://127.0.0.1:8765/",
            "process_start_time_utc": "2000-01-01T00:00:00.0000000Z",
            "runtime_path": str(Path(sys.executable).resolve()),
            "process_executable_path": str(Path(sys.executable).resolve()),
        }
        (control / "studio-process.json").write_text(json.dumps(state), encoding="utf-8")
        completed = _run_studio(project, tmp_path, "-Action", "Stop")
        assert completed.returncode == 0, completed.stderr
        report = json.loads(completed.stdout)
        assert report["status"] == "stale"
        assert report["process_identity"]["status"] == "stale"
        assert "Stop was not attempted" in report["process_identity"]["reason"]
        assert unrelated.poll() is None
    finally:
        unrelated.terminate()
        unrelated.wait(timeout=10)
