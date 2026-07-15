from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ledgerline.build_state import authored_revision
from ledgerline.delegation import (
    apply_delegation,
    create_delegation,
    list_delegations,
    propose_delegation,
    show_delegation,
)
from ledgerline.project import load_piece, prepare_ids
from ledgerline.studio_edits import StudioSession
from ledgerline.studio_model import build_studio_model


def _yaml_sources(project: Path) -> dict[str, bytes]:
    return {
        path.relative_to(project).as_posix(): path.read_bytes()
        for path in project.rglob("*.yaml")
        if "build" not in path.parts and ".ledgerline" not in path.parts
    }


def _history(project: Path) -> dict[str, bytes]:
    root = project / ".ledgerline" / "history"
    if not root.is_dir():
        return {}
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_proposal_preview_is_exact_isolated_and_bound_to_apply(example_project: Path) -> None:
    prepare_ids(example_project)
    piece = load_piece(example_project)
    first = piece.parts[0].measures[3].voices["v1"][0]
    second = piece.parts[0].measures[3].voices["v1"][1]
    assert first.id and second.id
    created = create_delegation(example_project, "Rewrite the opening of measure three")
    source_before = _yaml_sources(example_project)
    history_before = _history(example_project)
    revision_before = authored_revision(example_project)

    proposed = propose_delegation(
        example_project,
        created["id"],
        {
            "summary": "Replace the first melody note and shape the next note",
            "actions": [
                {"type": "delete_event", "part": "piano", "event_id": first.id},
                {
                    "type": "insert_event",
                    "part": "piano",
                    "measure": 3,
                    "voice": "v1",
                    "at_offset_whole": "0",
                    "event": {"p": "B4", "d": "1/4", "dyn": "mp"},
                },
                {
                    "type": "update_note",
                    "part": "piano",
                    "event_id": second.id,
                    "changes": {"velocity": 91, "articulation": "tenuto"},
                },
            ],
        },
    )

    assert _yaml_sources(example_project) == source_before
    assert _history(example_project) == history_before
    assert authored_revision(example_project) == revision_before
    preview = proposed["proposal_preview"]
    assert preview["status"] == "ready"
    assert preview["base_revision"] == revision_before
    assert preview["result_revision"] != revision_before
    impact = preview["impact"]
    assert [item["path"] for item in impact["files"]] == ["parts/piano.yaml"]
    assert impact["parts"] == ["piano"]
    assert impact["measures"] == [{"part": "piano", "measure": 3}]
    assert {"pitch", "dynamics", "articulation"} <= set(impact["aspects"])
    assert impact["targets"] == ["part:piano:measure:3"]
    assert impact["counts"] == {
        name: len(impact[name])
        for name in ("files", "parts", "measures", "aspects", "targets", "fields")
    }
    yaml_diff = preview["yaml_diff"]
    assert yaml_diff["files"] == ["parts/piano.yaml"]
    assert "--- a/parts/piano.yaml" in yaml_diff["text"]
    assert "+++ b/parts/piano.yaml" in yaml_diff["text"]
    assert yaml_diff["byte_count"] <= yaml_diff["limits"]["max_bytes"]
    assert yaml_diff["line_count"] <= yaml_diff["limits"]["max_lines"]

    score = preview["score_diff"]
    assert score["identity"]["complete"] is True
    assert score["counts"] == {"added": 1, "removed": 1, "changed": 1, "total": 3}
    inserted_id = proposed["proposal"]["actions"][1]["event"]["id"]
    assert score["added"][0]["event_id"] == inserted_id
    assert score["removed"][0]["event_id"] == first.id
    assert score["changed"][0]["event_id"] == second.id
    assert score["changed"][0]["changed_fields"] == ["velocity", "articulation"]

    assert show_delegation(example_project, created["id"])["proposal_preview"] == preview
    listed = list_delegations(example_project)["tasks"]
    assert listed[0]["proposal_preview"] == preview
    studio = build_studio_model(example_project)
    assert studio["delegations"][0]["proposal_preview"] == preview

    observed_contract = {}

    class BoundSession:
        def apply(self, actions, **contract):
            observed_contract.update(contract)
            return StudioSession(example_project).apply(actions, **contract)

    applied = apply_delegation(
        example_project,
        created["id"],
        token=proposed["approval_token"],
        session=BoundSession(),
    )
    assert observed_contract["revision"] == preview["base_revision"]
    assert observed_contract["expected_revision"] == preview["result_revision"]
    assert applied["result"]["source_revision"] == preview["result_revision"]
    assert applied["result"]["source"]["transaction"]["impact"] == {
        key: value for key, value in impact.items() if key != "counts"
    }


def test_preview_rejects_protected_and_invalid_actions_without_source_or_history_changes(
    example_project: Path,
) -> None:
    prepare_ids(example_project)
    brief_path = example_project / "brief.yaml"
    brief = yaml.safe_load(brief_path.read_text(encoding="utf-8"))
    brief["protected"] = [
        {
            "from": "1:1",
            "to": "1:end",
            "parts": ["cello"],
            "aspects": ["pitch"],
        }
    ]
    brief_path.write_text(yaml.safe_dump(brief, sort_keys=False), encoding="utf-8")
    piece = load_piece(example_project)
    event_id = piece.parts[1].measures[1].voices["v1"][0].id
    assert event_id
    source_before = _yaml_sources(example_project)
    history_before = _history(example_project)

    protected = create_delegation(example_project, "Change the protected cello motive")
    with pytest.raises(ValueError, match="protected material"):
        propose_delegation(
            example_project,
            protected["id"],
            {
                "summary": "Change protected pitch",
                "actions": [
                    {
                        "type": "update_note",
                        "part": "cello",
                        "event_id": event_id,
                        "changes": {"pitch": "D3"},
                    }
                ],
            },
        )
    assert show_delegation(example_project, protected["id"])["proposal_preview"] is None

    invalid = create_delegation(example_project, "Make one note impossibly loud")
    with pytest.raises(ValueError, match="proposal preview failed"):
        propose_delegation(
            example_project,
            invalid["id"],
            {
                "summary": "Invalid velocity",
                "actions": [
                    {
                        "type": "update_note",
                        "part": "piano",
                        "measure": 3,
                        "voice": "v1",
                        "event_index": 0,
                        "changes": {"velocity": 999},
                    }
                ],
            },
        )
    invalid_task = show_delegation(example_project, invalid["id"])
    assert invalid_task["status"] == "pending"
    assert invalid_task["proposal"] is None
    assert invalid_task["proposal_preview"] is None
    assert _yaml_sources(example_project) == source_before
    assert _history(example_project) == history_before


def test_preview_supports_custom_nested_part_source_path(example_project: Path) -> None:
    source = example_project / "parts" / "piano.yaml"
    custom = example_project / "score" / "sections" / "keys-source.yaml"
    custom.parent.mkdir(parents=True)
    source.replace(custom)
    piece_path = example_project / "piece.yaml"
    piece = yaml.safe_load(piece_path.read_text(encoding="utf-8"))
    piece["parts"][0]["file"] = "score/sections/keys-source.yaml"
    piece_path.write_text(yaml.safe_dump(piece, sort_keys=False), encoding="utf-8")
    prepare_ids(example_project)
    loaded = load_piece(example_project)
    event_id = loaded.parts[0].measures[3].voices["v1"][0].id
    assert event_id
    created = create_delegation(example_project, "Accent the custom-source melody")
    source_before = _yaml_sources(example_project)

    proposed = propose_delegation(
        example_project,
        created["id"],
        {
            "summary": "Accent one note",
            "actions": [
                {
                    "type": "update_note",
                    "part": "piano",
                    "event_id": event_id,
                    "changes": {"velocity": 96, "articulation": "accent"},
                }
            ],
        },
    )

    preview = proposed["proposal_preview"]
    assert [item["path"] for item in preview["impact"]["files"]] == [
        "score/sections/keys-source.yaml"
    ]
    assert preview["yaml_diff"]["files"] == ["score/sections/keys-source.yaml"]
    assert _yaml_sources(example_project) == source_before
