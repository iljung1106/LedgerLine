from __future__ import annotations

import json
import zipfile
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]
PLUGIN = ROOT / "plugins" / "ledgerline"


def test_codex_plugin_manifest_and_marketplace_are_consistent() -> None:
    manifest = json.loads((PLUGIN / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
    marketplace = json.loads(
        (ROOT / ".agents" / "plugins" / "marketplace.json").read_text(encoding="utf-8")
    )
    assert manifest["name"] == "ledgerline"
    assert manifest["version"].split("+", 1)[0] == "0.4.0"
    assert manifest["skills"] == "./skills/"
    assert marketplace["name"] == "ledgerline"
    assert marketplace["plugins"] == [
        {
            "name": "ledgerline",
            "source": {"source": "local", "path": "./plugins/ledgerline"},
            "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
            "category": "Creativity",
        }
    ]


def test_compose_skill_has_valid_metadata_and_no_placeholders() -> None:
    skill_path = PLUGIN / "skills" / "compose-music" / "SKILL.md"
    text = skill_path.read_text(encoding="utf-8")
    _, frontmatter, body = text.split("---", 2)
    metadata = yaml.safe_load(frontmatter)
    assert metadata["name"] == "compose-music"
    assert "ask the user for musical direction" in metadata["description"]
    assert "[TODO" not in text
    assert "Establish direction before writing" in body
    for reference in (
        "authoring-contract.md",
        "musical-quality.md",
        "cli-and-environment.md",
    ):
        assert (PLUGIN / "skills" / "compose-music" / "references" / reference).is_file()


def test_plugin_bundles_verified_runtime_wheel() -> None:
    wheel = PLUGIN / "assets" / "ledgerline-0.4.0-py3-none-any.whl"
    assert wheel.is_file()
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        assert "ledgerline/data/packs/catalog.json" in names
        assert "ledgerline/data/packs/catalog.json.sig" in names
        assert "ledgerline/data/trust/catalog_keys.json" in names
        assert "ledgerline/data/schemas/render.schema.json" in names
        metadata = archive.read("ledgerline-0.4.0.dist-info/METADATA").decode("utf-8")
        assert "ledgerline/data/reference_plugins/ledgerline-sine.clap.llplugin.json" in names
    assert "Version: 0.4.0" in metadata
    assert "Requires-Dist: cryptography" in metadata
    assert "Requires-Dist: numpy" in metadata
