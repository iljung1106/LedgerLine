"""Run the production LedgerLine Studio against an isolated example project."""

from __future__ import annotations

import argparse
import json
import shutil
import signal
import threading
from pathlib import Path

from ledgerline.build_state import authored_revision, file_identity, record_render
from ledgerline.project import load_piece, prepare_ids
from ledgerline.reference_host import reference_manifest, render_reference_plugin
from ledgerline.studio_server import create_studio_server, prepare_studio_assets

REPOSITORY = Path(__file__).resolve().parents[1]
SOURCE_PROJECT = REPOSITORY / "examples" / "nocturne"
E2E_ROOT = REPOSITORY / ".cache" / "studio-e2e"
DEFAULT_PROJECT = E2E_ROOT / "nocturne"


def reset_project(destination: Path = DEFAULT_PROJECT) -> Path:
    """Create a writable fixture without ever mutating the authored example."""

    destination = destination.resolve()
    e2e_root = E2E_ROOT.resolve()
    if destination == e2e_root or e2e_root not in destination.parents:
        raise ValueError(f"E2E project must stay inside {e2e_root}")
    if destination.exists():
        shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        SOURCE_PROJECT,
        destination,
        ignore=shutil.ignore_patterns("build", ".ledgerline", "__pycache__"),
    )
    prepare_ids(destination)
    return destination


def seed_reference_render(project: Path) -> None:
    """Create honest engine receipts with LedgerLine's deterministic reference synth."""

    plugin = reference_manifest("clap")
    renderer = Path(__file__).resolve().parents[1] / "src" / "ledgerline" / "reference_host.py"
    nodes = []
    for part in load_piece(project).parts:
        midi = project / "build" / "parts" / f"{part.id}.mid"
        output = project / "build" / "stems" / f"{part.id}.wav"
        render_reference_plugin(
            {
                "plugin": str(plugin),
                "plugin_format": "clap",
                "midi": str(midi),
                "wav": str(output),
                "sample_rate": 48_000,
                "tail_seconds": 0.25,
            }
        )
        nodes.append(
            {
                "part": part.id,
                "output": file_identity(output),
                "engine": "ledgerline-reference-synth",
                "plugin_format": "clap",
                "host_kind": "bundled-reference",
                "instrument": file_identity(plugin),
                "renderer": file_identity(renderer),
                "latency_samples": 0,
                "tail_seconds": 0.25,
                "cache": "e2e-fixture",
            }
        )
    record_render(
        project,
        {
            "schema_version": "1",
            "source_revision": authored_revision(project),
            "sample_rate": 48_000,
            "block_size": 512,
            "nodes": nodes,
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8876, type=int)
    parser.add_argument("--project", type=Path, default=DEFAULT_PROJECT)
    args = parser.parse_args()

    project = reset_project(args.project)
    prepare_studio_assets(project)
    seed_reference_render(project)
    server = create_studio_server(project, host=args.host, port=args.port)
    actual_port = int(server.server_address[1])

    stopping = threading.Event()

    def stop(_signum: int, _frame: object) -> None:
        if stopping.is_set():
            return
        stopping.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, stop)

    print(
        json.dumps(
            {
                "status": "serving",
                "project": str(project),
                "url": f"http://{args.host}:{actual_port}/",
            }
        ),
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.1)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
