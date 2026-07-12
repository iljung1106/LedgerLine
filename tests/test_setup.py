from __future__ import annotations

import base64
import hashlib
import json
import struct
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import ledgerline.catalog as catalog_module
from ledgerline.catalog import CatalogError, load_verified_catalog
from ledgerline.pack import PackError, inspect_llpack
from ledgerline.setup_apply import apply_setup_plan
from ledgerline.setup_plan import create_setup_plan, persist_setup_plan


def test_signed_catalog_plan_apply_and_single_use(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("LEDGERLINE_HOME", str(home))
    soundfont = tmp_path / "starter.sf3"
    _write_fake_gm_soundfont(soundfont)
    llpack, manifest_sha = _write_llpack(tmp_path, soundfont)
    catalog, trust = _write_signed_catalog(tmp_path, llpack, manifest_sha)
    monkeypatch.setattr(catalog_module, "default_trust_store_path", lambda: trust)
    now = datetime(2026, 7, 10, tzinfo=UTC)
    plan = create_setup_plan(["starter"], catalog, now=now)
    plan_path = persist_setup_plan(plan, tmp_path / "plan.json")

    report = apply_setup_plan(plan_path, plan["consent_token"], now=now)

    assert report["status"] == "ok"
    installed = home / "packs" / "starter" / "test-1"
    assert (installed / "payload" / "starter.sf3").is_file()
    assert (home / "packs" / "starter" / "ACTIVE").read_text().strip() == "test-1"
    with pytest.raises(ValueError, match="already been consumed"):
        apply_setup_plan(plan_path, plan["consent_token"], now=now)


def test_catalog_tampering_is_rejected(tmp_path: Path, monkeypatch) -> None:
    soundfont = tmp_path / "starter.sf3"
    _write_fake_gm_soundfont(soundfont)
    llpack, manifest_sha = _write_llpack(tmp_path, soundfont)
    catalog, trust = _write_signed_catalog(tmp_path, llpack, manifest_sha)
    monkeypatch.setattr(catalog_module, "default_trust_store_path", lambda: trust)
    catalog.write_text(catalog.read_text().replace("Starter test", "Starter evil"))
    with pytest.raises(CatalogError, match="signed SHA-256"):
        load_verified_catalog(catalog, now=datetime(2026, 7, 10, tzinfo=UTC))


def test_setup_rejects_wrong_token_expiry_and_plan_tampering(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("LEDGERLINE_HOME", str(home))
    soundfont = tmp_path / "starter.sf3"
    _write_fake_gm_soundfont(soundfont)
    llpack, manifest_sha = _write_llpack(tmp_path, soundfont)
    catalog, trust = _write_signed_catalog(tmp_path, llpack, manifest_sha)
    monkeypatch.setattr(catalog_module, "default_trust_store_path", lambda: trust)
    now = datetime(2026, 7, 10, tzinfo=UTC)
    plan = create_setup_plan(["starter"], catalog, now=now)
    plan_path = persist_setup_plan(plan, tmp_path / "plan.json")

    with pytest.raises(ValueError, match="consent token"):
        apply_setup_plan(plan_path, "wrong", now=now)
    with pytest.raises(ValueError, match="expired"):
        apply_setup_plan(plan_path, plan["consent_token"], now=now + timedelta(minutes=31))

    changed = json.loads(plan_path.read_text())
    changed["total_download_bytes"] += 1
    plan_path.write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="digest mismatch"):
        apply_setup_plan(plan_path, plan["consent_token"], now=now)


def test_setup_plan_rejects_duplicate_pack_ids() -> None:
    with pytest.raises(ValueError, match="duplicate pack ids"):
        create_setup_plan(["starter", "starter"])


@pytest.mark.parametrize("unsafe_name", ["../escape.txt", "CON.txt", "a\\b.txt", "x:ads"])
def test_llpack_rejects_unsafe_member_names(tmp_path: Path, unsafe_name: str) -> None:
    path = tmp_path / "unsafe.llpack"
    manifest = {
        "format": 1,
        "id": "starter",
        "version": "test-1",
        "display_name": "unsafe",
        "license": "MIT",
        "attribution": "test",
        "source_page": "https://example.test",
        "soundfonts": [unsafe_name],
        "files": [
            {
                "path": unsafe_name,
                "size_bytes": 1,
                "sha256": hashlib.sha256(b"x").hexdigest(),
            }
        ],
    }
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr(unsafe_name, b"x")
    with pytest.raises(PackError, match="unsafe|reserved"):
        inspect_llpack(path, unpacked_size_limit=10_000, entry_limit=10)


def test_pack_hash_mismatch_is_rejected(tmp_path: Path) -> None:
    soundfont = tmp_path / "starter.sf3"
    _write_fake_gm_soundfont(soundfont)
    llpack, manifest_sha = _write_llpack(tmp_path, soundfont)
    with pytest.raises(PackError, match="manifest SHA-256"):
        inspect_llpack(
            llpack,
            expected_manifest_sha256="0" * 64,
            unpacked_size_limit=2_000_000,
            entry_limit=10,
        )
    assert len(manifest_sha) == 64


def _write_signed_catalog(root: Path, llpack: Path, manifest_sha: str) -> tuple[Path, Path]:
    private_key = Ed25519PrivateKey.generate()
    public_raw = private_key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    trust = root / "trust.json"
    trust.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "keys": [
                    {
                        "id": "test-key",
                        "algorithm": "ed25519",
                        "public_key_base64": base64.b64encode(public_raw).decode(),
                        "status": "active",
                    }
                ],
            }
        )
    )
    document = {
        "schema_version": "1",
        "catalog_version": 1,
        "generated_at": "2026-07-01T00:00:00Z",
        "expires_at": "2027-07-01T00:00:00Z",
        "packs": [
            {
                "id": "starter",
                "version": "test-1",
                "display_name": "Starter test",
                "status": "installable",
                "license": "MIT",
                "attribution": "test",
                "source_page": "https://example.test/source",
                "artifacts": [
                    {
                        "id": "test-artifact",
                        "url": llpack.name,
                        "size_bytes": llpack.stat().st_size,
                        "sha256": _sha256_file(llpack),
                        "media_type": "application/vnd.ledgerline.llpack+zip",
                        "unpacked_size_limit": 2_000_000,
                        "entry_limit": 3,
                        "manifest_sha256": manifest_sha,
                    }
                ],
            }
        ],
    }
    catalog = root / "catalog.json"
    raw = (json.dumps(document, indent=2) + "\n").encode()
    catalog.write_bytes(raw)
    signature = private_key.sign(raw)
    (root / "catalog.json.sig").write_text(
        json.dumps(
            {
                "schema_version": "1",
                "algorithm": "ed25519",
                "key_id": "test-key",
                "signed_sha256": hashlib.sha256(raw).hexdigest(),
                "signature": base64.b64encode(signature).decode(),
            }
        )
    )
    return catalog, trust


def _write_llpack(root: Path, soundfont: Path) -> tuple[Path, str]:
    data = soundfont.read_bytes()
    file_record = {
        "path": "payload/starter.sf3",
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    manifest = {
        "format": 1,
        "id": "starter",
        "version": "test-1",
        "display_name": "Starter test",
        "license": "MIT",
        "attribution": "test",
        "source_page": "https://example.test/source",
        "soundfonts": ["payload/starter.sf3"],
        "files": [file_record],
    }
    raw = (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode()
    path = root / "starter.llpack"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", raw)
        archive.writestr("payload/starter.sf3", data)
    return path, hashlib.sha256(raw).hexdigest()


def _write_fake_gm_soundfont(path: Path) -> None:
    records = []
    for program in range(128):
        name = f"Program {program}".encode("ascii")
        records.append(struct.pack("<20sHHHIII", name, program, 0, 0, 0, 0, 0))
    records.append(struct.pack("<20sHHHIII", b"EOP", 0, 0, 0, 0, 0, 0))
    phdr = b"".join(records)
    phdr_chunk = b"phdr" + struct.pack("<I", len(phdr)) + phdr
    pdta = b"pdta" + phdr_chunk
    list_chunk = b"LIST" + struct.pack("<I", len(pdta)) + pdta
    body = b"sfbk" + list_chunk
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
