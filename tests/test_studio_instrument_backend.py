from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest
import yaml

import ledgerline.delegation_preview as delegation_preview_module
import ledgerline.studio_edits as studio_edits_module
import ledgerline.studio_model as studio_model_module
from ledgerline.build_state import authored_revision
from ledgerline.delegation import (
    apply_delegation,
    create_delegation,
    propose_delegation,
)
from ledgerline.diagnostics import ValidationError
from ledgerline.project import load_piece
from ledgerline.render_graph import load_render_graph
from ledgerline.studio_edits import StudioSession
from ledgerline.studio_model import build_studio_model

_PROFILE_ROOT = Path(studio_model_module.__file__).parent / "data" / "profiles"


def _write_profile(
    project: Path,
    profile_id: str,
    *,
    source: str = "starter.acoustic-grand-piano",
    name: str | None = None,
    absolute: list[str] | None = None,
    articulations: list[str] | None = None,
) -> Path:
    document = yaml.safe_load((_PROFILE_ROOT / f"{source}.yaml").read_text(encoding="utf-8"))
    document["id"] = profile_id
    if name is not None:
        document["name"] = name
    if absolute is not None:
        document["range"] = {"absolute": absolute, "comfortable": absolute}
    if articulations is not None:
        document["articulations"] = articulations
    path = project / "profiles" / f"{profile_id}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return path


def _write_render_graph(project: Path) -> dict[str, str]:
    paths = {
        "piano_old": "assets/piano-old.sfz",
        "piano_new": "assets/piano-new.sfz",
        "cello_old": "assets/cello-old.sfz",
        "piano_state_old": "presets/piano-old.state",
        "piano_state_new": "presets/piano-new.state",
        "cello_state_old": "presets/cello-old.state",
    }
    for relative in paths.values():
        path = project / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"fixture:{relative}".encode())
    document = {
        "format": 1,
        "sample_rate": 48_000,
        "block_size": 512,
        "nodes": [
            {
                "id": "piano-node",
                "part": "piano",
                "engine": "frozen",
                "instrument": paths["piano_old"],
                "state": paths["piano_state_old"],
            },
            {
                "id": "cello-node",
                "part": "cello",
                "engine": "frozen",
                "instrument": paths["cello_old"],
                "state": paths["cello_state_old"],
            },
        ],
    }
    (project / "render.yaml").write_text(
        yaml.safe_dump(document, sort_keys=False), encoding="utf-8"
    )
    return paths


def _write_plugin_render_graph(project: Path, external: Path) -> dict[str, Path]:
    relative_host = project / "tools" / "plugin-host.exe"
    relative_bundle = project / "plugins" / "Piano.vst3"
    relative_state = project / "presets" / "piano.plugin-state"
    external_host = external / "plugin-host.exe"
    external_bundle = external / "Cello.vst3"
    external_state = external / "cello.plugin-state"
    files = {
        "relative_host": relative_host,
        "relative_binary": relative_bundle / "Contents" / "x86_64-win" / "Piano.vst3",
        "relative_resource": relative_bundle / "Contents" / "Resources" / "preset.json",
        "relative_state": relative_state,
        "external_host": external_host,
        "external_binary": external_bundle / "Contents" / "x86_64-win" / "Cello.vst3",
        "external_state": external_state,
    }
    for name, path in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"fixture:{name}".encode())
    document = {
        "format": 1,
        "nodes": [
            {
                "id": "piano-plugin",
                "part": "piano",
                "engine": "plugin",
                "executable": relative_host.relative_to(project).as_posix(),
                "instrument": relative_bundle.relative_to(project).as_posix(),
                "plugin_format": "vst3",
                "state": relative_state.relative_to(project).as_posix(),
            },
            {
                "id": "cello-plugin",
                "part": "cello",
                "engine": "plugin",
                "executable": str(external_host.resolve()),
                "instrument": str(external_bundle.resolve()),
                "plugin_format": "vst3",
                "state": str(external_state.resolve()),
            },
        ],
    }
    (project / "render.yaml").write_text(
        yaml.safe_dump(document, sort_keys=False), encoding="utf-8"
    )
    return {
        **files,
        "relative_bundle": relative_bundle,
        "external_bundle": external_bundle,
    }


def _file_fingerprint(paths: list[Path]) -> dict[str, tuple[int, int, str]]:
    result = {}
    for path in paths:
        stat = path.stat()
        result[str(path.resolve())] = (
            stat.st_size,
            stat.st_mtime_ns,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
    return result


def _part_profile(project: Path, part: str) -> str:
    piece = yaml.safe_load((project / "piece.yaml").read_text(encoding="utf-8"))
    return next(item["profile"] for item in piece["parts"] if item["id"] == part)


def _render_node(project: Path, part: str) -> dict:
    render = yaml.safe_load((project / "render.yaml").read_text(encoding="utf-8"))
    return next(item for item in render["nodes"] if item["part"] == part)


def test_update_instrument_transaction_reports_exact_impact_and_undo_redo(
    example_project: Path,
) -> None:
    _write_profile(example_project, "project.piano", name="Project Piano")
    paths = _write_render_graph(example_project)
    session = StudioSession(example_project)
    before_revision = authored_revision(example_project)

    report = session.apply(
        [
            {
                "type": "update_instrument",
                "part": "piano",
                "changes": {
                    "profile": "project.piano",
                    "instrument": paths["piano_new"],
                    "state": paths["piano_state_new"],
                },
            }
        ],
        revision=before_revision,
    )

    assert _part_profile(example_project, "piano") == "project.piano"
    assert _render_node(example_project, "piano") == {
        "id": "piano-node",
        "part": "piano",
        "engine": "frozen",
        "instrument": paths["piano_new"],
        "state": paths["piano_state_new"],
    }
    graph = load_render_graph(example_project, load_piece(example_project))
    assert next(node for node in graph.nodes if node.part == "piano").instrument == (
        example_project / paths["piano_new"]
    ).resolve()
    impact = report["transaction"]["impact"]
    assert [item["path"] for item in impact["files"]] == ["piece.yaml", "render.yaml"]
    assert impact["parts"] == ["piano"]
    assert impact["measures"] == []
    assert impact["aspects"] == ["instrument", "render"]
    assert impact["targets"] == [
        "part:piano:configuration",
        "part:piano:instrument",
        "render",
    ]
    assert impact["fields"] == [
        "piece.yaml.parts[0].profile",
        "render.yaml.nodes[0].instrument",
        "render.yaml.nodes[0].state",
    ]
    assert session.can_undo is True
    assert session.can_redo is False

    session.undo()
    assert _part_profile(example_project, "piano") == "starter.acoustic-grand-piano"
    assert _render_node(example_project, "piano")["instrument"] == paths["piano_old"]
    assert session.can_redo is True
    session.redo()
    assert _part_profile(example_project, "piano") == "project.piano"
    assert _render_node(example_project, "piano")["state"] == paths["piano_state_new"]


def test_update_instrument_null_state_removes_only_existing_preset(
    example_project: Path,
) -> None:
    _write_render_graph(example_project)
    session = StudioSession(example_project)
    report = session.apply(
        [
            {
                "type": "update_instrument",
                "part": "piano",
                "changes": {"state": None},
            }
        ]
    )

    assert "state" not in _render_node(example_project, "piano")
    assert report["transaction"]["impact"]["fields"] == [
        "render.yaml.nodes[0].state"
    ]
    session.undo()
    assert _render_node(example_project, "piano")["state"] == "presets/piano-old.state"


def test_profile_only_change_revalidates_render_and_has_configuration_impact(
    example_project: Path,
) -> None:
    _write_profile(example_project, "project.piano")
    _write_render_graph(example_project)

    report = StudioSession(example_project).apply(
        [
            {
                "type": "update_instrument",
                "part": "piano",
                "changes": {"profile": "project.piano"},
            }
        ]
    )

    assert report["transaction"]["impact"] == {
        "changed": True,
        "files": report["transaction"]["impact"]["files"],
        "parts": ["piano"],
        "measures": [],
        "aspects": ["instrument"],
        "targets": ["part:piano:configuration"],
        "fields": ["piece.yaml.parts[0].profile"],
    }
    assert [
        item["path"] for item in report["transaction"]["impact"]["files"]
    ] == ["piece.yaml"]


@pytest.mark.parametrize(
    "failure", ["missing", "range", "articulation", "capability", "path"]
)
def test_update_instrument_validation_is_fail_closed_and_rolls_back(
    example_project: Path,
    failure: str,
) -> None:
    command: dict = {
        "type": "update_instrument",
        "part": "piano",
        "changes": {},
    }
    if failure == "missing":
        command["changes"] = {"profile": "project.missing"}
    elif failure == "range":
        _write_profile(example_project, "project.narrow", absolute=["C4", "C4"])
        command["changes"] = {"profile": "project.narrow"}
    elif failure == "articulation":
        _write_profile(
            example_project,
            "project.no-tenuto",
            articulations=["staccato", "accent", "marcato"],
        )
        command["changes"] = {"profile": "project.no-tenuto"}
    elif failure == "capability":
        profile_path = _write_profile(example_project, "project.no-brightness")
        profile = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
        profile["performance"].pop("brightness")
        profile_path.write_text(yaml.safe_dump(profile, sort_keys=False), encoding="utf-8")
        part_path = example_project / "parts" / "piano.yaml"
        part = yaml.safe_load(part_path.read_text(encoding="utf-8"))
        part["controls"] = [
            {
                "at": "1:1",
                "type": "performance",
                "parameter": "brightness",
                "value": 0.5,
            }
        ]
        part_path.write_text(yaml.safe_dump(part, sort_keys=False), encoding="utf-8")
        command["changes"] = {"profile": "project.no-brightness"}
    else:
        _write_render_graph(example_project)
        command["changes"] = {"instrument": "assets/does-not-exist.sfz"}
    before = {
        name: (example_project / name).read_bytes()
        for name in ("piece.yaml", "render.yaml")
        if (example_project / name).is_file()
    }
    session = StudioSession(example_project)

    with pytest.raises(ValidationError):
        session.apply([command])

    assert session.can_undo is False
    assert {
        name: (example_project / name).read_bytes() for name in before
    } == before
    assert _part_profile(example_project, "piano") == "starter.acoustic-grand-piano"


def test_profile_only_change_revalidates_existing_render_graph(
    example_project: Path,
) -> None:
    _write_profile(example_project, "project.piano")
    paths = _write_render_graph(example_project)
    (example_project / paths["cello_old"]).unlink()
    before = (example_project / "piece.yaml").read_bytes()
    session = StudioSession(example_project)

    with pytest.raises(ValidationError, match="render.yaml is invalid"):
        session.apply(
            [
                {
                    "type": "update_instrument",
                    "part": "piano",
                    "changes": {"profile": "project.piano"},
                }
            ]
        )

    assert (example_project / "piece.yaml").read_bytes() == before
    assert session.can_undo is False


def test_update_instrument_rejects_engine_fields_and_requires_existing_graph(
    example_project: Path,
) -> None:
    session = StudioSession(example_project)
    before = (example_project / "piece.yaml").read_bytes()
    for changes, message in (
        ({"engine": "plugin"}, "cannot change engine"),
        ({"executable": "host.exe"}, "cannot change executable"),
        ({"arguments": ["--unsafe"]}, "cannot change arguments"),
        ({"instrument": "assets/piano.sfz"}, "render.yaml is required"),
        ({"state": None}, "render.yaml is required"),
    ):
        with pytest.raises(ValueError, match=message):
            session.apply(
                [
                    {
                        "type": "update_instrument",
                        "part": "piano",
                        "changes": changes,
                    }
                ]
            )
    assert (example_project / "piece.yaml").read_bytes() == before
    assert session.can_undo is False


def test_instrument_protection_is_whole_part_even_for_a_partial_brief_range(
    example_project: Path,
) -> None:
    brief_path = example_project / "brief.yaml"
    brief = yaml.safe_load(brief_path.read_text(encoding="utf-8"))
    brief["protected"] = [
        {
            "from": "2:1",
            "to": "2:end",
            "parts": ["piano"],
            "aspects": ["instrument"],
        }
    ]
    brief_path.write_text(yaml.safe_dump(brief, sort_keys=False), encoding="utf-8")
    _write_profile(example_project, "project.piano")

    with pytest.raises(ValueError, match="protected brief range.*instrument.*measures 2-2"):
        StudioSession(example_project).apply(
            [
                {
                    "type": "update_instrument",
                    "part": "piano",
                    "changes": {"profile": "project.piano"},
                }
            ]
        )


def test_delegation_previews_instrument_assets_requires_review_and_rejects_stale_apply(
    example_project: Path,
) -> None:
    _write_profile(example_project, "project.piano")
    paths = _write_render_graph(example_project)
    created = create_delegation(
        example_project,
        "Change the piano profile and render preset",
        autonomy="safe-auto",
    )
    piece_before = (example_project / "piece.yaml").read_bytes()
    render_before = (example_project / "render.yaml").read_bytes()
    proposed = propose_delegation(
        example_project,
        created["id"],
        {
            "summary": "Use the approved project piano and its brighter preset",
            "actions": [
                {
                    "type": "update_instrument",
                    "part": "piano",
                    "changes": {
                        "profile": "project.piano",
                        "instrument": paths["piano_new"],
                        "state": paths["piano_state_new"],
                    },
                }
            ],
        },
    )

    assert (example_project / "piece.yaml").read_bytes() == piece_before
    assert (example_project / "render.yaml").read_bytes() == render_before
    assert proposed["status"] == "proposed"
    assert proposed["effective_autonomy"] == "review"
    assert proposed["safe_auto"]["allowed"] is False
    assert "update_instrument" in proposed["safe_auto"]["reasons"][0]
    preview = proposed["proposal_preview"]
    assert [item["path"] for item in preview["impact"]["files"]] == [
        "piece.yaml",
        "render.yaml",
    ]
    assert preview["impact"]["parts"] == ["piano"]
    assert preview["impact"]["aspects"] == ["instrument", "render"]
    assert preview["impact"]["targets"] == [
        "part:piano:configuration",
        "part:piano:instrument",
        "render",
    ]

    mix_path = example_project / "mix.yaml"
    mix_path.write_text(mix_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="project changed after the proposal"):
        apply_delegation(
            example_project,
            created["id"],
            token=proposed["approval_token"],
        )


@pytest.mark.parametrize("ordinary_action", ["note", "mix"])
def test_ordinary_preview_validates_relative_and_absolute_render_dependencies_read_only(
    example_project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ordinary_action: str,
) -> None:
    dependencies = _write_plugin_render_graph(example_project, tmp_path / "external-plugins")
    dependency_files = [
        dependencies["relative_host"],
        dependencies["relative_binary"],
        dependencies["relative_resource"],
        dependencies["relative_state"],
        dependencies["external_host"],
        dependencies["external_binary"],
        dependencies["external_state"],
    ]
    dependency_before = _file_fingerprint(dependency_files)
    authored_paths = [
        example_project / "piece.yaml",
        example_project / "parts" / "piano.yaml",
        example_project / "mix.yaml",
        example_project / "render.yaml",
    ]
    authored_before = {str(path): path.read_bytes() for path in authored_paths}
    real_load_render_graph = studio_edits_module.load_render_graph
    observed = {"preview": False}

    def inspect_isolated_dependencies(root: str | Path, piece):
        graph = real_load_render_graph(root, piece)
        preview_root = Path(root).resolve()
        if preview_root != example_project.resolve():
            observed["preview"] = True
            piano = next(node for node in graph.nodes if node.part == "piano")
            assert piano.executable == preview_root / "tools" / "plugin-host.exe"
            assert piano.instrument == preview_root / "plugins" / "Piano.vst3"
            assert piano.state == preview_root / "presets" / "piano.plugin-state"
            assert os.path.samefile(piano.executable, dependencies["relative_host"])
            assert os.path.samefile(
                piano.instrument / "Contents" / "x86_64-win" / "Piano.vst3",
                dependencies["relative_binary"],
            )
            assert os.path.samefile(
                piano.instrument / "Contents" / "Resources" / "preset.json",
                dependencies["relative_resource"],
            )
            assert os.path.samefile(piano.state, dependencies["relative_state"])
            cello = next(node for node in graph.nodes if node.part == "cello")
            assert cello.executable == dependencies["external_host"].resolve()
            assert cello.instrument == dependencies["external_bundle"].resolve()
            assert cello.state == dependencies["external_state"].resolve()
            for authored in authored_paths:
                preview_source = preview_root / authored.relative_to(example_project)
                assert not os.path.samefile(preview_source, authored)
        return graph

    monkeypatch.setattr(
        studio_edits_module, "load_render_graph", inspect_isolated_dependencies
    )
    action = (
        {
            "type": "update_note",
            "part": "piano",
            "measure": 3,
            "voice": "v1",
            "event_index": 0,
            "changes": {"velocity": 91},
        }
        if ordinary_action == "note"
        else {
            "type": "update_mix",
            "part": "piano",
            "changes": {"gain_db": -1.5},
        }
    )
    created = create_delegation(example_project, f"Preview a normal {ordinary_action} edit")

    proposed = propose_delegation(
        example_project,
        created["id"],
        {"summary": "Ordinary edit with render validation", "actions": [action]},
    )

    assert proposed["proposal_preview"]["status"] == "ready"
    assert observed["preview"] is True
    assert _file_fingerprint(dependency_files) == dependency_before
    assert {str(path): path.read_bytes() for path in authored_paths} == authored_before


def test_preview_render_directory_file_limit_fails_closed(
    example_project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_plugin_render_graph(example_project, tmp_path / "external-plugins")
    monkeypatch.setattr(delegation_preview_module, "_RENDER_DEPENDENCY_MAX_FILES", 1)
    mix_before = (example_project / "mix.yaml").read_bytes()
    created = create_delegation(example_project, "Preview with an oversized plugin bundle")

    with pytest.raises(ValueError, match="render dependency file count.*preview limit"):
        propose_delegation(
            example_project,
            created["id"],
            {
                "summary": "Normal mix proposal",
                "actions": [
                    {
                        "type": "update_mix",
                        "part": "piano",
                        "changes": {"gain_db": -1.0},
                    }
                ],
            },
        )

    assert (example_project / "mix.yaml").read_bytes() == mix_before


def test_profile_catalog_prefers_project_override_and_survives_unused_invalid_profile(
    example_project: Path,
) -> None:
    _write_profile(
        example_project,
        "starter.cello",
        source="starter.cello",
        name="Project Chamber Cello",
    )
    invalid = example_project / "profiles" / "unused.invalid.yaml"
    invalid.write_text("format: 1\nid: unused.invalid\n", encoding="utf-8")

    model = build_studio_model(example_project)

    assert model["capabilities"]["edit_instrument"] is True
    cello = [item for item in model["profile_catalog"] if item["id"] == "starter.cello"]
    assert len(cello) == 1
    assert cello[0]["status"] == "ready"
    assert cello[0]["source"] == "project"
    assert cello[0]["name"] == "Project Chamber Cello"
    assert set(cello[0]) >= {
        "family",
        "range",
        "midi",
        "midi_preset",
        "articulations",
        "keyswitches",
        "keyswitch_map",
        "performance_parameters",
        "performance",
    }
    broken = next(
        item for item in model["profile_catalog"] if item["id"] == "unused.invalid"
    )
    assert broken["status"] == "error"
    assert broken["source"] == "project"
    assert broken["reason"]
    assert broken["diagnostics"]
    assert set(broken) == {
        "id",
        "source",
        "status",
        "reason",
        "diagnostics",
    }
    assert set(broken["diagnostics"][0]) == {"severity", "code", "path", "message"}

    schema = json.loads(
        (Path(__file__).parents[1] / "schemas" / "studio-state.schema.json").read_text(
            encoding="utf-8"
        )
    )
    assert "profile_catalog" in schema["required"]
    catalog_item = schema["properties"]["profile_catalog"]["items"]
    assert catalog_item["required"] == ["id", "source", "status"]
    status_contract = catalog_item["allOf"][0]
    assert set(status_contract["then"]["required"]) == {
        "name",
        "family",
        "range",
        "midi",
        "midi_preset",
        "articulations",
        "keyswitches",
        "keyswitch_map",
        "performance_parameters",
        "performance",
    }
    assert status_contract["else"]["required"] == ["reason", "diagnostics"]
