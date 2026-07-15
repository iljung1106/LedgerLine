from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from collections.abc import Sequence
from contextlib import suppress
from pathlib import Path

_POLL_SECONDS = 0.05
_TERMINATE_GRACE_SECONDS = 0.75


class ExternalProcessCancelled(Exception):
    """Raised after a cancellable external process has been stopped and reaped."""


def run_external(
    command: Sequence[str | os.PathLike[str]],
    *,
    timeout: float,
    cancel_event: threading.Event | None = None,
    cwd: str | Path | None = None,
    encoding: str | None = None,
    errors: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run an external command without a shell, with cooperative tree cancellation.

    ``subprocess.run`` cannot observe a cancellation event while it is blocked. This
    wrapper polls ``communicate`` so Studio jobs can stop FluidSynth, plugin hosts,
    FFmpeg, and FFprobe promptly. The process is always reaped before cancellation or
    timeout is reported.
    """

    args = [os.fspath(item) for item in command]
    if not args:
        raise ValueError("external command must not be empty")
    if timeout <= 0:
        raise ValueError("external command timeout must be positive")
    if cancel_event is not None and cancel_event.is_set():
        raise ExternalProcessCancelled("external process cancelled before launch")

    options: dict[str, object] = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "shell": False,
        "cwd": os.fspath(cwd) if cwd is not None else None,
    }
    if encoding is not None:
        options["encoding"] = encoding
    if errors is not None:
        options["errors"] = errors
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True

    process = subprocess.Popen(args, **options)  # type: ignore[arg-type]
    windows_job = _create_windows_job(process) if os.name == "nt" else None
    deadline = time.monotonic() + timeout
    try:
        while True:
            if cancel_event is not None and cancel_event.is_set():
                _stop_process_tree(process, windows_job)
                raise ExternalProcessCancelled(f"external process cancelled: {args[0]}")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                stdout, stderr = _stop_process_tree(process, windows_job)
                raise subprocess.TimeoutExpired(
                    args,
                    timeout,
                    output=stdout,
                    stderr=stderr,
                )
            try:
                stdout, stderr = process.communicate(timeout=min(_POLL_SECONDS, remaining))
            except subprocess.TimeoutExpired:
                continue
            return subprocess.CompletedProcess(args, process.returncode, stdout, stderr)
    except (ExternalProcessCancelled, subprocess.TimeoutExpired):
        raise
    except BaseException:
        if process.poll() is None:
            _stop_process_tree(process, windows_job)
        raise
    finally:
        if windows_job is not None:
            windows_job.close()


def _stop_process_tree(
    process: subprocess.Popen[str], windows_job: _WindowsJob | None = None
) -> tuple[str, str]:
    """Stop the owned process group/tree and return all captured output."""

    if process.poll() is not None:
        return process.communicate()

    if os.name == "nt":
        _stop_windows_tree(process, windows_job)
    else:
        _stop_posix_group(process)
    try:
        return process.communicate(timeout=_TERMINATE_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        process.kill()
        return process.communicate()


def _stop_posix_group(process: subprocess.Popen[str]) -> None:
    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
    deadline = time.monotonic() + _TERMINATE_GRACE_SECONDS
    while time.monotonic() < deadline:
        process.poll()
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            return
        time.sleep(_POLL_SECONDS)
    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)


def _stop_windows_tree(
    process: subprocess.Popen[str], windows_job: _WindowsJob | None = None
) -> None:
    if windows_job is not None and windows_job.terminate():
        return
    # taskkill /T is the standard Windows tree operation and prevents plugin-host
    # grandchildren from surviving their cancelled Studio job.
    try:
        subprocess.run(
            ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2.0,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        if process.poll() is None:
            process.kill()


class _WindowsJob:
    def __init__(self, kernel32: object, handle: object) -> None:
        self._kernel32 = kernel32
        self._handle = handle

    def terminate(self) -> bool:
        if not self._handle:
            return False
        return bool(self._kernel32.TerminateJobObject(self._handle, 1))

    def close(self) -> None:
        if self._handle:
            self._kernel32.CloseHandle(self._handle)
            self._handle = None


def _create_windows_job(process: subprocess.Popen[str]) -> _WindowsJob | None:
    """Contain a renderer and its descendants in a kill-on-close Windows job."""

    import ctypes
    from ctypes import wintypes

    class BasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class ExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BasicLimitInformation),
            ("IoInfo", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateJobObject.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.CreateJobObjectW(None, None)
    if not handle:
        return None
    information = ExtendedLimitInformation()
    information.BasicLimitInformation.LimitFlags = 0x00002000
    configured = kernel32.SetInformationJobObject(
        handle,
        9,
        ctypes.byref(information),
        ctypes.sizeof(information),
    )
    assigned = configured and kernel32.AssignProcessToJobObject(handle, process._handle)
    if not assigned:
        kernel32.CloseHandle(handle)
        return None
    return _WindowsJob(kernel32, handle)
