from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

from ledgerline.compiler import compile_project
from ledgerline.external_process import run_external
from ledgerline.jobs import LocalBuildCoordinator


def test_running_render_job_cancels_external_process_tree(
    example_project: Path, tmp_path: Path
) -> None:
    marker = tmp_path / "renderer-pids.json"
    renderer_script = tmp_path / "long_renderer.py"
    renderer_script.write_text(
        """from __future__ import annotations
import json
import os
import subprocess
import sys
import time
from pathlib import Path

marker = Path(sys.argv[1])
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
marker.write_text(json.dumps({"parent": os.getpid(), "child": child.pid}), encoding="utf-8")
time.sleep(60)
""",
        encoding="utf-8",
    )
    instrument = example_project / "assets" / "long-test.clap"
    instrument.parent.mkdir(parents=True, exist_ok=True)
    instrument.write_bytes(b"fake plugin")
    (example_project / "render.yaml").write_text(
        yaml.safe_dump(
            {
                "format": 1,
                "nodes": [
                    {
                        "id": f"{part}-long",
                        "part": part,
                        "engine": "plugin",
                        "plugin_format": "clap",
                        "executable": Path(sys.executable).as_posix(),
                        "arguments": [renderer_script.as_posix(), marker.as_posix()],
                        "instrument": instrument.relative_to(example_project).as_posix(),
                    }
                    for part in ("piano", "cello")
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    compile_project(example_project)

    coordinator = LocalBuildCoordinator(example_project)
    pids: dict[str, int] = {}
    try:
        submitted = coordinator.submit(
            "render",
            {"ffmpeg": sys.executable, "timeout": 60},
        )
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if marker.is_file():
                try:
                    pids = json.loads(marker.read_text(encoding="utf-8"))
                    break
                except json.JSONDecodeError:
                    pass
            current = coordinator.get(submitted["id"])
            assert current["status"] in {"queued", "running"}, current
            time.sleep(0.02)
        assert set(pids) == {"parent", "child"}

        started = time.monotonic()
        coordinator.cancel(submitted["id"])
        completed = coordinator.wait(submitted["id"], timeout=10)
        assert completed["status"] == "cancelled"
        assert time.monotonic() - started < 8
    finally:
        coordinator.close()

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and any(_pid_exists(pid) for pid in pids.values()):
        time.sleep(0.05)
    assert not _pid_exists(pids["parent"])
    assert not _pid_exists(pids["child"])
    assert not list((example_project / "build" / "render-raw").glob("*.wav"))


def test_external_timeout_keeps_subprocess_timeout_semantics(tmp_path: Path) -> None:
    marker = tmp_path / "timeout.pid"
    script = tmp_path / "timeout.py"
    script.write_text(
        "import os, sys, time\n"
        "from pathlib import Path\n"
        "Path(sys.argv[1]).write_text(str(os.getpid()), encoding='utf-8')\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )

    # Leave enough startup budget for a busy Windows CI runner to execute the
    # marker write before the deliberately long sleep is timed out.
    timeout = 1.0
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        run_external([sys.executable, script, marker], timeout=timeout)

    assert caught.value.timeout == timeout
    pid = int(marker.read_text(encoding="utf-8"))
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and _pid_exists(pid):
        time.sleep(0.02)
    assert not _pid_exists(pid)


def _pid_exists(pid: int) -> bool:
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    import ctypes
    from ctypes import wintypes

    process = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
    if not process:
        return False
    try:
        exit_code = wintypes.DWORD()
        if not ctypes.windll.kernel32.GetExitCodeProcess(process, ctypes.byref(exit_code)):
            return False
        return exit_code.value == 259
    finally:
        ctypes.windll.kernel32.CloseHandle(process)
