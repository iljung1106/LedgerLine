from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

from ledgerline.compiler import compile_project
from ledgerline.diagnostics import ValidationError
from ledgerline.project import load_piece
from ledgerline.reference_host import reference_manifest
from ledgerline.render_graph import _run_node, load_render_graph


def test_render_graph_binds_each_part_to_an_engine(example_project: Path) -> None:
    assets = example_project / "assets"
    assets.mkdir()
    (assets / "piano.sf3").write_bytes(b"test")
    (assets / "cello.sfz").write_text("<region> sample=cello.wav\n", encoding="utf-8")
    executable = Path(sys.executable).as_posix()
    (example_project / "render.yaml").write_text(
        f"""format: 1
sample_rate: 48000
block_size: 512
tail_seconds: 3.0
nodes:
  - id: piano-render
    part: piano
    engine: fluidsynth
    executable: "{executable}"
    instrument: assets/piano.sf3
    latency_samples: 128
  - id: cello-render
    part: cello
    engine: sfizz
    executable: "{executable}"
    instrument: assets/cello.sfz
    tail_seconds: 1.5
""",
        encoding="utf-8",
    )
    graph = load_render_graph(example_project, load_piece(example_project))
    assert graph.sample_rate == 48_000
    assert [node.engine for node in graph.nodes] == ["fluidsynth", "sfizz"]
    assert graph.nodes[0].latency_samples == 128


def test_render_graph_rejects_missing_or_duplicate_part_bindings(example_project: Path) -> None:
    executable = Path(sys.executable).as_posix()
    (example_project / "render.yaml").write_text(
        f"""format: 1
nodes:
  - id: duplicate-a
    part: piano
    engine: plugin
    plugin_format: clap
    executable: "{executable}"
    instrument: "{executable}"
  - id: duplicate-b
    part: piano
    engine: plugin
    plugin_format: vst3
    executable: "{executable}"
    instrument: "{executable}"
""",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError, match="render.yaml is invalid"):
        load_render_graph(example_project, load_piece(example_project))


def test_frozen_render_node_needs_no_executable(example_project: Path) -> None:
    frozen = example_project / "frozen"
    frozen.mkdir()
    (frozen / "piano.wav").write_bytes(b"RIFF" + b"\0" * 100)
    (frozen / "cello.wav").write_bytes(b"RIFF" + b"\0" * 100)
    data = {
        "format": 1,
        "resources": {"max_render_seconds": 60, "max_stem_mb": 10, "max_cache_mb": 10},
        "nodes": [
            {
                "id": "piano-frozen",
                "part": "piano",
                "engine": "frozen",
                "instrument": "frozen/piano.wav",
            },
            {
                "id": "cello-frozen",
                "part": "cello",
                "engine": "frozen",
                "instrument": "frozen/cello.wav",
            },
        ],
    }
    (example_project / "render.yaml").write_text(
        yaml.safe_dump(data, sort_keys=False), encoding="utf-8"
    )
    graph = load_render_graph(example_project, load_piece(example_project))
    assert graph.nodes[0].executable is None
    assert graph.max_render_seconds == 60


def test_bundled_reference_plugin_node_needs_no_executable_and_renders(
    example_project: Path,
) -> None:
    manifest = reference_manifest().as_posix()
    data = {
        "format": 1,
        "nodes": [
            {
                "id": "piano-reference",
                "part": "piano",
                "engine": "plugin",
                "plugin_format": "clap",
                "instrument": manifest,
            },
            {
                "id": "cello-reference",
                "part": "cello",
                "engine": "plugin",
                "plugin_format": "clap",
                "instrument": manifest,
            },
        ],
    }
    (example_project / "render.yaml").write_text(
        yaml.safe_dump(data, sort_keys=False), encoding="utf-8"
    )
    compile_project(example_project)
    graph = load_render_graph(example_project, load_piece(example_project))
    assert graph.nodes[0].executable is None
    output = example_project / "build" / "reference-piano.wav"
    _run_node(
        graph.nodes[0],
        example_project / "build" / "parts" / "piano.mid",
        output,
        graph,
        example_project / "build",
        30,
    )
    assert output.stat().st_size > 44


def test_plugin_without_executable_rejects_native_binary(example_project: Path) -> None:
    plugin = example_project / "instrument.clap"
    plugin.write_bytes(b"not-a-reference-manifest")
    data = {
        "format": 1,
        "nodes": [
            {
                "id": f"{part}-native",
                "part": part,
                "engine": "plugin",
                "plugin_format": "clap",
                "instrument": "instrument.clap",
            }
            for part in ("piano", "cello")
        ],
    }
    (example_project / "render.yaml").write_text(
        yaml.safe_dump(data, sort_keys=False), encoding="utf-8"
    )
    with pytest.raises(ValidationError, match="render.yaml is invalid"):
        load_render_graph(example_project, load_piece(example_project))
