from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ledgerline.diagnostics import Diagnostic, ValidationError


@dataclass(frozen=True, slots=True)
class Processor:
    kind: str
    settings: dict[str, Any]


@dataclass(frozen=True, slots=True)
class MixNode:
    gain_db: float = 0.0
    pan: float = 0.0
    output: str = "master"
    sends: dict[str, float] = field(default_factory=dict)
    inserts: tuple[Processor, ...] = ()


@dataclass(frozen=True, slots=True)
class MixConfig:
    format: int
    tracks: dict[str, MixNode]
    buses: dict[str, MixNode]
    master: dict[str, Any]
    legacy_reverb: dict[str, Any]


def load_mix_document(root: str | Path) -> dict[str, Any]:
    """Return the authored mix document after validating it against the runtime parser."""

    path = Path(root).resolve() / "mix.yaml"
    load_mix_config(root)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:  # pragma: no cover - load_mix_config reports first
        raise ValidationError(
            "mix.yaml is invalid",
            [Diagnostic("error", "mix.invalid", str(path), str(exc))],
        ) from exc
    if not isinstance(data, dict):  # pragma: no cover - load_mix_config reports first
        raise AssertionError("validated mix document is not a mapping")
    return copy.deepcopy(data)


def mix_config_to_dict(config: MixConfig) -> dict[str, Any]:
    """Serialize the effective routing graph, including parser defaults, as JSON data."""

    return {
        "format": config.format,
        "tracks": {node_id: _node_to_dict(node) for node_id, node in config.tracks.items()},
        "buses": {node_id: _node_to_dict(node) for node_id, node in config.buses.items()},
        "master": {
            **{key: _jsonable(value) for key, value in config.master.items() if key != "inserts"},
            "inserts": [_processor_to_dict(item) for item in config.master.get("inserts", ())],
        },
        "legacy_reverb": _jsonable(config.legacy_reverb),
    }


def load_mix_config(root: str | Path) -> MixConfig:
    path = Path(root).resolve() / "mix.yaml"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("root must be a mapping")
        _unknown(data, {"format", "master", "reverb", "tracks", "buses"}, "mix.yaml")
        version = _integer(data.get("format"), "mix.yaml.format")
        if version not in {1, 2}:
            raise ValueError("mix format must be 1 or 2")
        raw_tracks = data.get("tracks")
        if not isinstance(raw_tracks, dict) or not raw_tracks:
            raise ValueError("mix tracks must be a non-empty mapping")
        raw_master = data.get("master", {})
        raw_reverb = data.get("reverb", {})
        raw_buses = data.get("buses", {})
        if not isinstance(raw_master, dict) or not isinstance(raw_reverb, dict):
            raise ValueError("master and reverb must be mappings")
        if not isinstance(raw_buses, dict):
            raise ValueError("buses must be a mapping")
        master = _master(raw_master)
        legacy_reverb = _legacy_reverb(raw_reverb)
        if version == 1:
            if raw_buses:
                raise ValueError("format 1 does not support buses")
            tracks = {
                str(node_id): _legacy_track(raw, f"mix.yaml.tracks.{node_id}")
                for node_id, raw in raw_tracks.items()
            }
            return MixConfig(version, tracks, {}, master, legacy_reverb)
        buses = {
            str(node_id): _node(raw, f"mix.yaml.buses.{node_id}", is_bus=True)
            for node_id, raw in raw_buses.items()
        }
        tracks = {
            str(node_id): _node(raw, f"mix.yaml.tracks.{node_id}", is_bus=False)
            for node_id, raw in raw_tracks.items()
        }
        _validate_routing(tracks, buses)
        return MixConfig(version, tracks, buses, master, legacy_reverb)
    except (OSError, yaml.YAMLError, TypeError, ValueError) as exc:
        code = "mix.unknown_field" if "unknown fields:" in str(exc) else "mix.invalid"
        raise ValidationError(
            "mix.yaml is invalid",
            [Diagnostic("error", code, str(path), str(exc))],
        ) from exc


def processor_filters(processor: Processor) -> list[str]:
    data = processor.settings
    if processor.kind == "eq":
        filters: list[str] = []
        if data.get("highpass_hz") is not None:
            filters.append(f"highpass=f={data['highpass_hz']:.6f}")
        if data.get("lowpass_hz") is not None:
            filters.append(f"lowpass=f={data['lowpass_hz']:.6f}")
        for band in data.get("bands", ()):  # pragma: no branch - tiny authored list
            filters.append(
                "equalizer="
                f"f={band['frequency_hz']:.6f}:width_type=q:"
                f"width={band['q']:.6f}:g={band['gain_db']:.6f}"
            )
        return filters
    if processor.kind == "compressor":
        threshold = 10.0 ** (data["threshold_db"] / 20.0)
        makeup = 10.0 ** (data["makeup_db"] / 20.0)
        return [
            "acompressor="
            f"threshold={threshold:.9f}:ratio={data['ratio']:.6f}:"
            f"attack={data['attack_ms']:.6f}:release={data['release_ms']:.6f}:"
            f"makeup={makeup:.9f}:knee={data['knee_db']:.6f}:detection=rms"
        ]
    if processor.kind == "reverb":
        return [
            f"aecho={data['in_gain']:.6f}:{data['out_gain']:.6f}:"
            f"{data['delays_ms']}:{data['decays']}"
        ]
    raise AssertionError(f"unhandled processor: {processor.kind}")


def _node(raw: Any, path: str, *, is_bus: bool) -> MixNode:
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must be a mapping")
    allowed = {"gain_db", "pan", "output", "sends", "inserts"}
    _unknown(raw, allowed, path)
    output = str(raw.get("output", "master"))
    if not output or output == path.rsplit(".", 1)[-1]:
        raise ValueError(f"{path}.output is invalid")
    sends_raw = raw.get("sends", {})
    if not isinstance(sends_raw, dict):
        raise ValueError(f"{path}.sends must be a mapping")
    sends = {str(key): _number(value, f"{path}.sends.{key}") for key, value in sends_raw.items()}
    inserts_raw = raw.get("inserts", [])
    if not isinstance(inserts_raw, list):
        raise ValueError(f"{path}.inserts must be a list")
    inserts = tuple(
        _processor(item, f"{path}.inserts[{index}]") for index, item in enumerate(inserts_raw)
    )
    pan = _number(raw.get("pan", 0.0), f"{path}.pan")
    if not -1.0 <= pan <= 1.0:
        raise ValueError(f"{path}.pan must be between -1 and 1")
    return MixNode(
        gain_db=_number(raw.get("gain_db", 0.0), f"{path}.gain_db"),
        pan=pan,
        output=output,
        sends=sends,
        inserts=inserts,
    )


def _legacy_track(raw: Any, path: str) -> MixNode:
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must be a mapping")
    _unknown(raw, {"gain_db", "pan", "reverb_send_db"}, path)
    pan = _number(raw.get("pan", 0.0), f"{path}.pan")
    if not -1.0 <= pan <= 1.0:
        raise ValueError(f"{path}.pan must be between -1 and 1")
    return MixNode(
        gain_db=_number(raw.get("gain_db", 0.0), f"{path}.gain_db"),
        pan=pan,
        sends={"__legacy_reverb": _number(raw.get("reverb_send_db", -120.0), path)},
    )


def _processor(raw: Any, path: str) -> Processor:
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must be a mapping")
    kind = raw.get("type")
    if kind == "eq":
        _unknown(raw, {"type", "highpass_hz", "lowpass_hz", "bands"}, path)
        result: dict[str, Any] = {}
        for field_name in ("highpass_hz", "lowpass_hz"):
            if field_name in raw:
                value = _number(raw[field_name], f"{path}.{field_name}")
                if not 10.0 <= value <= 24_000.0:
                    raise ValueError(f"{path}.{field_name} is outside 10..24000 Hz")
                result[field_name] = value
        bands_raw = raw.get("bands", [])
        if not isinstance(bands_raw, list):
            raise ValueError(f"{path}.bands must be a list")
        bands = []
        for index, band in enumerate(bands_raw):
            band_path = f"{path}.bands[{index}]"
            if not isinstance(band, dict):
                raise ValueError(f"{band_path} must be a mapping")
            _unknown(band, {"frequency_hz", "gain_db", "q"}, band_path)
            frequency = _number(band.get("frequency_hz"), f"{band_path}.frequency_hz")
            gain = _number(band.get("gain_db"), f"{band_path}.gain_db")
            q = _number(band.get("q", 1.0), f"{band_path}.q")
            if not 10 <= frequency <= 24_000 or not -24 <= gain <= 24 or not 0.05 <= q <= 30:
                raise ValueError(f"{band_path} has an out-of-range EQ value")
            bands.append({"frequency_hz": frequency, "gain_db": gain, "q": q})
        result["bands"] = tuple(bands)
        return Processor("eq", result)
    if kind == "compressor":
        allowed = {
            "type",
            "threshold_db",
            "ratio",
            "attack_ms",
            "release_ms",
            "makeup_db",
            "knee_db",
        }
        _unknown(raw, allowed, path)
        values = {
            "threshold_db": _number(raw.get("threshold_db", -18.0), f"{path}.threshold_db"),
            "ratio": _number(raw.get("ratio", 3.0), f"{path}.ratio"),
            "attack_ms": _number(raw.get("attack_ms", 20.0), f"{path}.attack_ms"),
            "release_ms": _number(raw.get("release_ms", 200.0), f"{path}.release_ms"),
            "makeup_db": _number(raw.get("makeup_db", 0.0), f"{path}.makeup_db"),
            "knee_db": _number(raw.get("knee_db", 2.0), f"{path}.knee_db"),
        }
        if not -100 <= values["threshold_db"] <= 0 or not 1 <= values["ratio"] <= 20:
            raise ValueError(f"{path} has an invalid threshold or ratio")
        if not 0.01 <= values["attack_ms"] <= 2000 or not 0.01 <= values["release_ms"] <= 9000:
            raise ValueError(f"{path} has an invalid attack or release")
        return Processor("compressor", values)
    if kind == "reverb":
        _unknown(raw, {"type", "in_gain", "out_gain", "delays_ms", "decays"}, path)
        return Processor(
            "reverb",
            {
                "in_gain": _number(raw.get("in_gain", 0.8), f"{path}.in_gain"),
                "out_gain": _number(raw.get("out_gain", 0.7), f"{path}.out_gain"),
                "delays_ms": _delay_list(raw.get("delays_ms", "40|55"), f"{path}.delays_ms"),
                "decays": _delay_list(raw.get("decays", "0.3|0.2"), f"{path}.decays"),
            },
        )
    raise ValueError(f"{path}.type must be eq, compressor, or reverb")


def _master(raw: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "gain_db",
        "target_lufs",
        "true_peak_ceiling_db",
        "loudness_range_lu",
        "loudness_tolerance_lu",
        "inserts",
    }
    _unknown(raw, allowed, "mix.yaml.master")
    inserts_raw = raw.get("inserts", [])
    if not isinstance(inserts_raw, list):
        raise ValueError("mix.yaml.master.inserts must be a list")
    result = {
        "gain_db": _number(raw.get("gain_db", 0.0), "mix.yaml.master.gain_db"),
        "target_lufs": _number(raw.get("target_lufs", -16.0), "mix.yaml.master.target_lufs"),
        "true_peak_ceiling_db": _number(
            raw.get("true_peak_ceiling_db", -1.0), "mix.yaml.master.true_peak_ceiling_db"
        ),
        "loudness_range_lu": _number(
            raw.get("loudness_range_lu", 11.0), "mix.yaml.master.loudness_range_lu"
        ),
        "loudness_tolerance_lu": _number(
            raw.get("loudness_tolerance_lu", 0.5), "mix.yaml.master.loudness_tolerance_lu"
        ),
        "inserts": tuple(
            _processor(item, f"mix.yaml.master.inserts[{index}]")
            for index, item in enumerate(inserts_raw)
        ),
    }
    if result["true_peak_ceiling_db"] > 0 or result["loudness_range_lu"] <= 0:
        raise ValueError("master ceiling must be <= 0 and loudness range must be positive")
    if result["loudness_tolerance_lu"] < 0:
        raise ValueError("master loudness tolerance must be non-negative")
    return result


def _legacy_reverb(raw: dict[str, Any]) -> dict[str, Any]:
    _unknown(raw, {"delays_ms", "decays"}, "mix.yaml.reverb")
    return {
        "delays_ms": _delay_list(raw.get("delays_ms", "40|55"), "mix.yaml.reverb.delays_ms"),
        "decays": _delay_list(raw.get("decays", "0.30|0.20"), "mix.yaml.reverb.decays"),
    }


def _validate_routing(tracks: dict[str, MixNode], buses: dict[str, MixNode]) -> None:
    if "master" in buses:
        raise ValueError("master is reserved and cannot be a bus id")
    known = set(buses) | {"master"}
    for group, nodes in (("tracks", tracks), ("buses", buses)):
        for node_id, node in nodes.items():
            for target in {node.output, *node.sends}:
                if target not in known:
                    raise ValueError(f"mix.yaml.{group}.{node_id} targets unknown bus {target!r}")
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in visiting:
            raise ValueError(f"bus routing cycle includes {node_id!r}")
        if node_id in visited:
            return
        visiting.add(node_id)
        node = buses[node_id]
        for target in {node.output, *node.sends}:
            if target != "master":
                visit(target)
        visiting.remove(node_id)
        visited.add(node_id)

    for bus_id in buses:
        visit(bus_id)


def _delay_list(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{path} must be a non-empty pipe-separated string")
    try:
        items = [float(item) for item in value.split("|")]
    except ValueError as exc:
        raise ValueError(f"{path} contains a non-number") from exc
    if not items or any(not math.isfinite(item) or item < 0 for item in items):
        raise ValueError(f"{path} contains an invalid value")
    return "|".join(f"{item:g}" for item in items)


def _integer(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{path} must be an integer")
    return value


def _number(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{path} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{path} must be finite")
    return result


def _unknown(data: dict[str, Any], allowed: set[str], path: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ValueError(f"{path} has unknown fields: {', '.join(unknown)}")


def _node_to_dict(node: MixNode) -> dict[str, Any]:
    return {
        "gain_db": node.gain_db,
        "pan": node.pan,
        "output": node.output,
        "sends": dict(node.sends),
        "inserts": [_processor_to_dict(item) for item in node.inserts],
    }


def _processor_to_dict(processor: Processor) -> dict[str, Any]:
    return {"type": processor.kind, **_jsonable(processor.settings)}


def _jsonable(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value
