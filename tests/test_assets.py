from __future__ import annotations

import json
import zipfile

import yaml

from ledgerline.assets import audit_assets, bundle_project


def test_asset_lineage_and_license_aware_bundle(example_project) -> None:
    assets_dir = example_project / "assets"
    assets_dir.mkdir()
    redistributable = assets_dir / "open.sfz"
    restricted = assets_dir / "restricted.wav"
    redistributable.write_text("<region> sample=restricted.wav", encoding="utf-8")
    restricted.write_bytes(b"sample-data")
    data = {
        "format": 1,
        "assets": [
            {
                "id": "sample",
                "path": "assets/restricted.wav",
                "source": "https://example.invalid/sample",
                "license": "Proprietary test license",
                "redistributable": False,
            },
            {
                "id": "patch",
                "path": "assets/open.sfz",
                "source": "locally authored",
                "license": "CC0-1.0",
                "redistributable": True,
                "derived_from": ["sample"],
                "conversion": "mapped to SFZ",
            },
        ],
    }
    (example_project / "assets.yaml").write_text(
        yaml.safe_dump(data, sort_keys=False), encoding="utf-8"
    )
    report = audit_assets(example_project)
    assert len(report["assets"]) == 2
    bundle = bundle_project(example_project, include_build=False)
    with zipfile.ZipFile(bundle["bundle"]) as archive:
        names = archive.namelist()
        assert "assets/open.sfz" in names
        assert "assets/restricted.wav" not in names
        placeholder = json.loads(archive.read("assets-unavailable/sample.json"))
        assert placeholder["reason"].startswith("license")
