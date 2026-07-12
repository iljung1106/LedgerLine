from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from ledgerline.diagnostics import CapabilityError, Diagnostic, ValidationError


def scan_plugin(
    host: str | Path,
    plugin: str | Path,
    plugin_format: str,
    *,
    arguments: tuple[str, ...] = (),
    output: str | Path | None = None,
    timeout: int = 60,
) -> dict:
    host_path = Path(host).resolve(strict=True)
    plugin_path = Path(plugin).resolve(strict=True)
    if not host_path.is_file():
        raise ValueError("plugin host must be a file")
    if plugin_format not in {"vst3", "clap"}:
        raise ValueError("plugin_format must be vst3 or clap")
    with tempfile.TemporaryDirectory(prefix="ledgerline-plugin-scan-") as temporary:
        request_path = Path(temporary) / "request.json"
        request_path.write_text(
            json.dumps(
                {
                    "schema_version": "1",
                    "plugin_format": plugin_format,
                    "plugin": str(plugin_path),
                    "offline": True,
                    "requested": [
                        "identity",
                        "parameters",
                        "state",
                        "latency",
                        "tail",
                        "audio_ports",
                        "note_ports",
                    ],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        completed = subprocess.run(
            [str(host_path), *arguments, "--ledgerline-scan-request", str(request_path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
        )
    if completed.returncode != 0:
        raise CapabilityError(
            "external plugin host scan failed",
            [
                Diagnostic(
                    "error",
                    "plugin.scan_failed",
                    str(plugin_path),
                    completed.stderr[-3000:],
                )
            ],
        )
    try:
        response = json.loads(completed.stdout)
        normalized = _validate_scan_response(response)
    except (json.JSONDecodeError, TypeError, ValueError, KeyError) as exc:
        raise ValidationError(
            "plugin host returned an invalid scan response",
            [Diagnostic("error", "plugin.scan_response", str(plugin_path), str(exc))],
        ) from exc
    report = {
        "schema_version": "1",
        "status": "ok",
        "host": _identity(host_path),
        "plugin": _identity(plugin_path),
        "plugin_format": plugin_format,
        **normalized,
    }
    if output is not None:
        output_path = Path(output).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        report["report"] = str(output_path)
    return report


def _validate_scan_response(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("response must be a mapping")
    allowed = {
        "schema_version",
        "name",
        "vendor",
        "version",
        "parameters",
        "supports_state",
        "latency_samples",
        "tail_samples",
        "audio_ports",
        "note_ports",
    }
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ValueError(f"response has unknown fields: {', '.join(unknown)}")
    if raw.get("schema_version") != "1":
        raise ValueError("response schema_version must be '1'")
    for field_name in ("name", "vendor", "version"):
        if not isinstance(raw.get(field_name), str) or not raw[field_name].strip():
            raise ValueError(f"response {field_name} is required")
    parameters = raw.get("parameters")
    if not isinstance(parameters, list):
        raise ValueError("response parameters must be a list")
    normalized_parameters = [_parameter(item, index) for index, item in enumerate(parameters)]
    ids = [item["id"] for item in normalized_parameters]
    if len(ids) != len(set(ids)):
        raise ValueError("plugin parameter ids must be unique")
    supports_state = raw.get("supports_state")
    if not isinstance(supports_state, bool):
        raise ValueError("supports_state must be boolean")
    latency = _nonnegative_integer(raw.get("latency_samples", 0), "latency_samples")
    tail = _nonnegative_integer(raw.get("tail_samples", 0), "tail_samples")
    audio_ports = raw.get("audio_ports", [])
    note_ports = raw.get("note_ports", [])
    if not isinstance(audio_ports, list) or not isinstance(note_ports, list):
        raise ValueError("audio_ports and note_ports must be lists")
    return {
        "name": raw["name"],
        "vendor": raw["vendor"],
        "version": raw["version"],
        "parameters": normalized_parameters,
        "supports_state": supports_state,
        "latency_samples": latency,
        "tail_samples": tail,
        "audio_ports": audio_ports,
        "note_ports": note_ports,
    }


def _parameter(raw: Any, index: int) -> dict[str, Any]:
    path = f"parameters[{index}]"
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must be a mapping")
    allowed = {"id", "name", "minimum", "maximum", "default", "automatable"}
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ValueError(f"{path} has unknown fields: {', '.join(unknown)}")
    parameter_id = raw.get("id")
    name = raw.get("name")
    if not isinstance(parameter_id, (str, int)) or not isinstance(name, str) or not name:
        raise ValueError(f"{path} requires id and name")
    minimum = _number(raw.get("minimum", 0.0), f"{path}.minimum")
    maximum = _number(raw.get("maximum", 1.0), f"{path}.maximum")
    default = _number(raw.get("default", minimum), f"{path}.default")
    if maximum <= minimum or not minimum <= default <= maximum:
        raise ValueError(f"{path} has an invalid range/default")
    automatable = raw.get("automatable", True)
    if not isinstance(automatable, bool):
        raise ValueError(f"{path}.automatable must be boolean")
    return {
        "id": str(parameter_id),
        "name": name,
        "minimum": minimum,
        "maximum": maximum,
        "default": default,
        "automatable": automatable,
    }


def _identity(path: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    if path.is_file():
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        size = path.stat().st_size
    else:
        size = 0
        for child in sorted(item for item in path.rglob("*") if item.is_file()):
            digest.update(child.relative_to(path).as_posix().encode("utf-8"))
            child_bytes = child.read_bytes()
            digest.update(hashlib.sha256(child_bytes).digest())
            size += len(child_bytes)
    return {"path": str(path), "bytes": size, "sha256": digest.hexdigest()}


def _nonnegative_integer(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{path} must be a non-negative integer")
    return value


def _number(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{path} must be numeric")
    return float(value)
