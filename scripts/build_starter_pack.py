from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path

PACK_VERSION = "0.2.0-ll.1"
SOURCE_FILES = {
    "MuseScore_General.sf3": "payload/soundfonts/MuseScore_General.sf3",
    "MuseScore_General_License.md": "licenses/MuseScore_General_License.md",
    "MuseScore_General_Readme.md": "notices/MuseScore_General_Readme.md",
    "MuseScore_General_Sample_Sources.csv": "notices/MuseScore_General_Sample_Sources.csv",
    "VERSION": "notices/UPSTREAM_VERSION",
}
AUDITED_SHA256 = {
    "MuseScore_General.sf3": "5b85b6c2c61d10b2b91cddd41efcce7b25cd31c8271d511c73afafbef20b6fa3",
    "MuseScore_General_License.md": (
        "5ad8d737e13c7f01f5b9674872a82a92b4ba253603e8ed14b9db12293550b4b9"
    ),
    "MuseScore_General_Readme.md": (
        "e4ee85d097cda49a7926ea73e59ea8b7bb90d6ad715f82d20c6bfddf5dcfeb4c"
    ),
    "MuseScore_General_Sample_Sources.csv": (
        "cbec757614fa47d2ba71a2f1276bf010918261d94987cfb678352e9755e9bdd4"
    ),
    "VERSION": "1f930dd1f133c1f97a94fe3acb8db34372cf4c01ffdb2b3ff4ca72f9494121e9",
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the reproducible LedgerLine Starter llpack")
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    missing = [name for name in SOURCE_FILES if not (args.source_dir / name).is_file()]
    if missing:
        raise SystemExit(f"missing audited source files: {', '.join(missing)}")
    if (args.source_dir / "MuseScore_General.sf3").stat().st_size != 39_900_972:
        raise SystemExit("MuseScore_General.sf3 size differs from the audited 0.2 artifact")
    for name, expected in AUDITED_SHA256.items():
        actual = sha256_file(args.source_dir / name)
        if actual != expected:
            raise SystemExit(f"{name} SHA-256 differs from the audited source: {actual}")
    files = []
    for source_name, archive_name in sorted(SOURCE_FILES.items(), key=lambda item: item[1]):
        source = args.source_dir / source_name
        files.append(
            {
                "path": archive_name,
                "size_bytes": source.stat().st_size,
                "sha256": sha256_file(source),
            }
        )
    manifest = {
        "format": 1,
        "id": "starter",
        "version": PACK_VERSION,
        "display_name": "LedgerLine Starter — MuseScore General 0.2",
        "license": "MIT",
        "attribution": "MuseScore General contributors; full notices included in the pack",
        "source_page": "https://ftp.osuosl.org/pub/musescore/soundfont/MuseScore_General/",
        "soundfonts": ["payload/soundfonts/MuseScore_General.sf3"],
        "files": files,
    }
    raw_manifest = (
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, "w", allowZip64=True) as archive:
        write_bytes(archive, "manifest.json", raw_manifest)
        for source_name, archive_name in sorted(SOURCE_FILES.items(), key=lambda item: item[1]):
            write_bytes(archive, archive_name, (args.source_dir / source_name).read_bytes())
    report = {
        "artifact": str(args.output.resolve()),
        "version": PACK_VERSION,
        "size_bytes": args.output.stat().st_size,
        "sha256": sha256_file(args.output),
        "manifest_sha256": hashlib.sha256(raw_manifest).hexdigest(),
        "unpacked_size_limit": sum(item["size_bytes"] for item in files)
        + len(raw_manifest)
        + 1024 * 1024,
        "entry_limit": len(files) + 1,
    }
    print(json.dumps(report, indent=2))


def write_bytes(archive: zipfile.ZipFile, name: str, data: bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=(2020, 7, 10, 8, 27, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    info.create_system = 3
    archive.writestr(info, data, compresslevel=9)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    main()
