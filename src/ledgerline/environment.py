from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path


def ledgerline_home() -> Path:
    override = os.environ.get("LEDGERLINE_HOME")
    if override:
        return Path(override).expanduser().resolve()
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "LedgerLine"
    return Path.home() / ".ledgerline"


def doctor() -> dict:
    fluidsynth = _find_fluidsynth()
    musescore = _find_executable(
        "LEDGERLINE_MUSESCORE",
        [
            Path(os.environ.get("PROGRAMFILES", "C:/Program Files"))
            / "MuseScore 4"
            / "bin"
            / "MuseScore4.exe",
            Path(os.environ.get("PROGRAMFILES", "C:/Program Files"))
            / "MuseScore 4"
            / "MuseScore4.exe",
        ],
        ["MuseScore4", "MuseScore4.exe", "mscore"],
    )
    ffmpeg = _find_executable("LEDGERLINE_FFMPEG", [], ["ffmpeg", "ffmpeg.exe"])
    soundfonts = _find_soundfonts()
    problems: list[dict[str, str]] = []
    if not fluidsynth:
        problems.append(
            {
                "code": "FLUIDSYNTH_NOT_FOUND",
                "severity": "warning",
                "detail": "Static compilation works, but SF2/SF3 audio rendering is unavailable.",
                "fix": (
                    "Run ledgerline setup plan --packs starter --json or set LEDGERLINE_FLUIDSYNTH."
                ),
            }
        )
    if not soundfonts:
        problems.append(
            {
                "code": "SOUNDFONT_NOT_FOUND",
                "severity": "warning",
                "detail": "No SF2/SF3 was discovered. LedgerLine will not guess a substitute.",
                "fix": "Install an audited pack or pass --soundfont explicitly.",
            }
        )
    render_available = bool(fluidsynth and soundfonts)
    managed_renderer = bool(fluidsynth and _is_under(fluidsynth, ledgerline_home() / "engines"))
    managed_soundfonts = [item for item in soundfonts if item["origin"] == "ledgerline-pack"]
    managed_render_ready = bool(managed_renderer and managed_soundfonts)
    if render_available and not managed_render_ready:
        problems.append(
            {
                "code": "UNMANAGED_AUDIO_ASSETS",
                "severity": "warning",
                "detail": (
                    "Local audio tools were discovered, but LedgerLine has not installed or "
                    "verified them. Pass their paths explicitly to use them."
                ),
                "fix": "Install an audited pack or explicitly pass --fluidsynth and --soundfont.",
            }
        )
    report = {
        "schema_version": "1",
        "status": "ok" if managed_render_ready else "degraded",
        "home": str(ledgerline_home()),
        "capabilities": {
            "compile_musicxml": True,
            "compile_midi": True,
            "render_sf2_sf3_available": render_available,
            "managed_render_ready": managed_render_ready,
        },
        "renderers": [
            item
            for item in (
                _tool_record("fluidsynth", fluidsynth, ["--version"]),
                _tool_record("musescore", musescore, ["--version"]),
                _tool_record("ffmpeg", ffmpeg, ["-version"]),
            )
            if item is not None
        ],
        "soundfonts": soundfonts,
        "problems": problems,
    }
    return report


def _find_fluidsynth() -> Path | None:
    program_files = Path(os.environ.get("PROGRAMFILES", "C:/Program Files"))
    candidates = [
        ledgerline_home() / "engines" / "fluidsynth" / "bin" / "fluidsynth.exe",
        program_files / "FluidSynth" / "bin" / "fluidsynth.exe",
        Path("C:/tools/fluidsynth/bin/fluidsynth.exe"),
    ]
    found = _find_executable("LEDGERLINE_FLUIDSYNTH", candidates, ["fluidsynth", "fluidsynth.exe"])
    if found:
        return found
    downloads = Path.home() / "Downloads"
    if downloads.is_dir():
        for root in sorted(downloads.glob("fluidsynth*")):
            if root.is_dir():
                matches = sorted(root.rglob("fluidsynth.exe"))
                if matches:
                    return matches[0].resolve()
    return None


def _find_executable(env_name: str, candidates: list[Path], names: list[str]) -> Path | None:
    override = os.environ.get(env_name)
    if override:
        path = Path(override).expanduser()
        if path.is_file():
            return path.resolve()
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    for name in names:
        resolved = shutil.which(name)
        if resolved:
            return Path(resolved).resolve()
    return None


def _find_soundfonts() -> list[dict[str, str | int]]:
    paths: list[tuple[Path, str]] = []
    override = os.environ.get("LEDGERLINE_SOUNDFONT")
    if override:
        paths.append((Path(override).expanduser(), "environment"))
    pack_root = ledgerline_home() / "packs"
    if pack_root.is_dir():
        for suffix in ("*.sf2", "*.sf3"):
            paths.extend((path, "ledgerline-pack") for path in pack_root.rglob(suffix))
    muse_sound = Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "MuseScore 4" / "sound"
    if muse_sound.is_dir():
        for suffix in ("*.sf2", "*.sf3"):
            paths.extend((path, "musescore-install") for path in muse_sound.glob(suffix))
    seen: set[Path] = set()
    records: list[dict[str, str | int]] = []
    for path, origin in paths:
        try:
            resolved = path.resolve(strict=True)
        except FileNotFoundError:
            continue
        if resolved in seen or resolved.suffix.lower() not in {".sf2", ".sf3"}:
            continue
        seen.add(resolved)
        records.append(
            {
                "path": str(resolved),
                "origin": origin,
                "bytes": resolved.stat().st_size,
                "sha256": _sha256_file(resolved),
            }
        )
    return records


def _tool_record(tool_id: str, path: Path | None, version_args: list[str]) -> dict | None:
    if path is None:
        return None
    version = "unknown"
    try:
        completed = subprocess.run(
            [str(path), *version_args],
            check=False,
            capture_output=True,
            text=True,
            timeout=8,
            shell=False,
        )
        lines = (completed.stdout + "\n" + completed.stderr).strip().splitlines()
        if lines:
            version = lines[0].strip()
    except (OSError, subprocess.TimeoutExpired):
        version = "probe-failed"
    return {
        "id": tool_id,
        "path": str(path),
        "version": version,
        "origin": "ledgerline-managed"
        if _is_under(path, ledgerline_home() / "engines")
        else "local-unmanaged",
        "sha256": _sha256_file(path),
    }


def write_doctor_report(path: Path) -> dict:
    report = doctor()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True
