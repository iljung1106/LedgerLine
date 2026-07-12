from __future__ import annotations

import gzip
import re
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from ledgerline.diagnostics import CapabilityError, Diagnostic, ValidationError

HEADER_RE = re.compile(r"<(control|global|master|group|region)>", re.IGNORECASE)
OPCODE_RE = re.compile(r"([a-zA-Z][a-zA-Z0-9_]*)\s*=\s*([^\s]+)")
PATH_RE = re.compile(
    rb"(?:[A-Za-z]:[\\/]|\.\.?[\\/])[^\x00\r\n]{3,260}\.(?:wav|aif|aiff|flac)", re.I
)


def inspect_sample_library(source: str | Path) -> dict:
    path = Path(source).resolve()
    if not path.is_file():
        raise ValidationError(
            "sample library does not exist",
            [Diagnostic("error", "samples.missing", str(path), "File does not exist.")],
        )
    suffix = path.suffix.lower()
    if suffix == ".sfz":
        report = _inspect_sfz(path)
    elif suffix == ".exs":
        report = _inspect_exs(path)
    elif suffix in {".adv", ".als"}:
        report = _inspect_ableton(path)
    elif suffix in {".nki", ".nkm"}:
        report = _inspect_kontakt(path)
    else:
        raise ValidationError(
            "unsupported sample library format",
            [Diagnostic("error", "samples.format", str(path), suffix or "no extension")],
        )
    return {"schema_version": "1", "status": "ok", "source": str(path), **report}


def convert_sample_library(source: str | Path, output: str | Path) -> dict:
    path = Path(source).resolve()
    output_path = Path(output).resolve()
    if output_path.exists():
        raise ValidationError(
            "sample conversion output already exists",
            [Diagnostic("error", "samples.output_exists", str(output_path), "Choose a new path.")],
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report = inspect_sample_library(path)
    if report["format"] == "sfz":
        shutil.copyfile(path, output_path)
        regions = report["regions"]
    elif report["format"] in {"exs24", "ableton-sampler"}:
        regions = report.get("regions", [])
        if not regions:
            raise CapabilityError(
                "the source contains no recoverable sample zones",
                [
                    Diagnostic(
                        "error",
                        "samples.no_zones",
                        str(path),
                        "Preserve the source and use its vendor application to export samples.",
                    )
                ],
            )
        lines = ["// Converted by LedgerLine; verify loop and modulation semantics."]
        for region in regions:
            if not region.get("sample"):
                continue
            opcodes = ["<region>", f"sample={region['sample']}"]
            for key in ("lokey", "hikey", "pitch_keycenter", "lovel", "hivel"):
                if region.get(key) is not None:
                    opcodes.append(f"{key}={region[key]}")
            lines.append(" ".join(opcodes))
        output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    else:
        raise CapabilityError(
            "Kontakt conversion is intentionally unsupported",
            [
                Diagnostic(
                    "error",
                    "samples.kontakt_proprietary",
                    str(path),
                    "LedgerLine records provenance but does not decrypt proprietary containers.",
                )
            ],
        )
    return {
        "schema_version": "1",
        "status": "ok",
        "source": str(path),
        "output": str(output_path),
        "regions": len(regions),
        "warnings": report.get("warnings", []),
    }


def _inspect_sfz(path: Path) -> dict:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    text = re.sub(r"//.*?$|/\*.*?\*/", "", text, flags=re.MULTILINE | re.DOTALL)
    matches = list(HEADER_RE.finditer(text))
    inherited: dict[str, dict[str, str]] = {"global": {}, "master": {}, "group": {}}
    regions: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    default_path = ""
    for index, match in enumerate(matches):
        kind = match.group(1).lower()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        opcodes = dict(OPCODE_RE.findall(text[match.end() : end]))
        if kind == "control":
            default_path = opcodes.get("default_path", default_path)
        elif kind in inherited:
            inherited[kind] = opcodes
            if kind in {"master", "global"}:
                inherited["group"] = {}
        elif kind == "region":
            merged = {**inherited["global"], **inherited["master"], **inherited["group"], **opcodes}
            sample = merged.get("sample")
            resolved = None
            missing = False
            if sample:
                normalized = sample.replace("\\", "/")
                resolved_path = (path.parent / default_path / normalized).resolve()
                resolved = str(resolved_path)
                missing = not resolved_path.is_file()
            region = {
                "sample": sample,
                "resolved_sample": resolved,
                "missing": missing,
                "lokey": _midi_value(merged.get("lokey", merged.get("key"))),
                "hikey": _midi_value(merged.get("hikey", merged.get("key"))),
                "pitch_keycenter": _midi_value(merged.get("pitch_keycenter", merged.get("key"))),
                "lovel": _integer(merged.get("lovel", "0")),
                "hivel": _integer(merged.get("hivel", "127")),
                "loop_mode": merged.get("loop_mode"),
                "loop_start": _optional_integer(merged.get("loop_start")),
                "loop_end": _optional_integer(merged.get("loop_end")),
                "seq_length": _optional_integer(merged.get("seq_length")),
                "seq_position": _optional_integer(merged.get("seq_position")),
                "trigger": merged.get("trigger", "attack"),
                "opcodes": merged,
            }
            if missing:
                warnings.append({"code": "missing_sample", "sample": sample})
            if region["lovel"] > region["hivel"] or region["lokey"] > region["hikey"]:
                warnings.append({"code": "reversed_zone", "region": len(regions)})
            regions.append(region)
    coverage = _coverage(regions)
    return {
        "format": "sfz",
        "regions": regions,
        "coverage": coverage,
        "missing_samples": sum(bool(region["missing"]) for region in regions),
        "round_robin_groups": sorted(
            {region["seq_length"] for region in regions if region["seq_length"]}
        ),
        "warnings": warnings,
    }


def _inspect_exs(path: Path) -> dict:
    data = path.read_bytes()
    strings = _sample_paths(data)
    regions = [{"sample": item} for item in strings]
    return {
        "format": "exs24",
        "container": "binary",
        "regions": regions,
        "recoverable_sample_paths": strings,
        "warnings": [
            {
                "code": "exs_mapping_partial",
                "message": (
                    "Binary EXS24 sample paths were recovered; verify zones in Logic/Sampler."
                ),
            }
        ],
    }


def _inspect_ableton(path: Path) -> dict:
    raw = path.read_bytes()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ValidationError(
            "Ableton document XML is invalid",
            [Diagnostic("error", "samples.ableton_xml", str(path), str(exc))],
        ) from exc
    candidates: list[str] = []
    for element in root.iter():
        for key, value in element.attrib.items():
            if key.lower() in {"value", "path"} and re.search(r"\.(wav|aiff?|flac)$", value, re.I):
                candidates.append(value)
    unique = list(dict.fromkeys(candidates))
    return {
        "format": "ableton-sampler",
        "regions": [{"sample": item} for item in unique],
        "recoverable_sample_paths": unique,
        "warnings": [
            {
                "code": "ableton_modulation_not_converted",
                "message": "Sample paths are portable; device modulation remains vendor-specific.",
            }
        ],
    }


def _inspect_kontakt(path: Path) -> dict:
    return {
        "format": "kontakt",
        "container": path.suffix.lower().lstrip("."),
        "bytes": path.stat().st_size,
        "regions": [],
        "warnings": [
            {
                "code": "kontakt_proprietary",
                "message": "Provenance only; no proprietary samples or mappings are decrypted.",
            }
        ],
    }


def _sample_paths(data: bytes) -> list[str]:
    paths = [match.group(0).decode("latin-1", errors="replace") for match in PATH_RE.finditer(data)]
    for encoding in ("utf-16-le", "utf-16-be"):
        decoded = data.decode(encoding, errors="ignore")
        paths.extend(
            re.findall(
                r"(?:[A-Za-z]:[\\/]|\.\.?[\\/])[^\r\n]{3,260}\.(?:wav|aiff?|flac)", decoded, re.I
            )
        )
    return list(dict.fromkeys(paths))


def _coverage(regions: list[dict[str, Any]]) -> dict[str, Any]:
    covered = set()
    velocity_layers = set()
    for region in regions:
        covered.update(range(region["lokey"], region["hikey"] + 1))
        velocity_layers.add((region["lovel"], region["hivel"]))
    return {
        "lowest_key": min(covered) if covered else None,
        "highest_key": max(covered) if covered else None,
        "key_count": len(covered),
        "velocity_layers": len(velocity_layers),
    }


def _midi_value(value: str | None) -> int:
    if value is None:
        return 0
    try:
        result = int(value)
    except ValueError:
        match = re.fullmatch(r"([A-Ga-g])([#b]?)(-?\d+)", value)
        if not match:
            raise ValueError(f"invalid SFZ key value: {value!r}") from None
        step, accidental, octave = match.groups()
        base = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}[step.upper()]
        result = (int(octave) + 1) * 12 + base + {"": 0, "#": 1, "b": -1}[accidental]
    if not 0 <= result <= 127:
        raise ValueError(f"SFZ key value is outside MIDI range: {value!r}")
    return result


def _integer(value: str) -> int:
    return int(value)


def _optional_integer(value: str | None) -> int | None:
    return None if value is None else int(value)
