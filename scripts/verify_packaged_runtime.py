"""Install a built LedgerLine wheel into a clean venv and exercise its packaged runtime."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
import venv
from pathlib import Path


def _wheel(value: Path) -> Path:
    candidate = value.resolve()
    if candidate.is_dir():
        wheels = sorted(candidate.glob("ledgerline-*.whl"))
        if len(wheels) != 1:
            raise ValueError(f"expected one LedgerLine wheel in {candidate}, found {len(wheels)}")
        return wheels[0]
    if not candidate.is_file() or candidate.suffix != ".whl":
        raise ValueError(f"wheel does not exist: {candidate}")
    return candidate


def _run(command: list[str], *, cwd: Path | None = None) -> str:
    completed = subprocess.run(
        command,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
        shell=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"packaged runtime command failed ({completed.returncode}): {' '.join(command)}\n"
            f"{completed.stdout}\n{completed.stderr}"
        )
    return completed.stdout.strip()


def verify(wheel: Path) -> dict[str, object]:
    repository = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="ledgerline-wheel-smoke-") as temporary:
        root = Path(temporary)
        environment = root / "venv"
        venv.EnvBuilder(with_pip=True, clear=True).create(environment)
        python = environment / (
            "Scripts/python.exe" if os.name == "nt" else "bin/python"
        )
        _run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-input",
                str(wheel),
            ]
        )
        project = root / "nocturne"
        shutil.copytree(
            repository / "examples" / "nocturne",
            project,
            ignore=shutil.ignore_patterns("build", ".ledgerline", "__pycache__"),
        )
        validation = json.loads(
            _run([str(python), "-m", "ledgerline", "validate", str(project), "--json"])
        )
        compilation = json.loads(
            _run([str(python), "-m", "ledgerline", "compile", str(project), "--json"])
        )
        model = json.loads(
            _run([str(python), "-m", "ledgerline", "studio-model", str(project), "--json"])
        )
        resource_probe = (
            "import importlib.resources as r, json; "
            "root=r.files('ledgerline'); "
            "items={'brief_schema': root/'data/schemas/brief.schema.json', "
            "'studio_schema': root/'data/schemas/studio-state.schema.json', "
            "'studio_ui': root/'data/studio/index.html'}; "
            "print(json.dumps({key: value.is_file() for key, value in items.items()}))"
        )
        resources = json.loads(
            _run(
                [
                    str(python),
                    "-c",
                    resource_probe,
                ]
            )
        )
        if validation.get("status") != "ok":
            raise RuntimeError("installed wheel did not validate the fixture")
        score = project / "build" / "score.musicxml"
        if compilation.get("status") != "ok" or not score.is_file():
            raise RuntimeError("installed wheel did not compile MusicXML")
        if model.get("schema_version") != "2" or not model.get("notes"):
            raise RuntimeError("installed wheel did not produce the Studio v2 model")
        if not all(resources.values()):
            raise RuntimeError(f"installed wheel is missing packaged resources: {resources}")
        return {
            "schema_version": "1",
            "status": "ok",
            "wheel": str(wheel),
            "project": validation.get("title"),
            "studio_schema_version": model.get("schema_version"),
            "notes": len(model.get("notes", [])),
            "resources": resources,
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("wheel", type=Path, help="Built wheel file or directory containing it")
    args = parser.parse_args()
    print(json.dumps(verify(_wheel(args.wheel)), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
