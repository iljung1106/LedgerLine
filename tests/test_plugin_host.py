from __future__ import annotations

import sys
from pathlib import Path

from ledgerline.plugin_host import scan_plugin


def test_external_plugin_host_scan_protocol(tmp_path: Path) -> None:
    plugin = tmp_path / "instrument.clap"
    plugin.write_bytes(b"plugin")
    host_script = tmp_path / "host.py"
    host_script.write_text(
        """
import json
print(json.dumps({
    "schema_version": "1",
    "name": "Test Instrument",
    "vendor": "LedgerLine Tests",
    "version": "1.0",
    "parameters": [
        {"id": "attack", "name": "Attack", "minimum": 0, "maximum": 1,
         "default": 0.5, "automatable": True}
    ],
    "supports_state": True,
    "latency_samples": 128,
    "tail_samples": 48000,
    "audio_ports": [{"direction": "output", "channels": 2}],
    "note_ports": [{"direction": "input"}]
}))
""".strip(),
        encoding="utf-8",
    )
    report = scan_plugin(
        sys.executable,
        plugin,
        "clap",
        arguments=(str(host_script),),
    )
    assert report["name"] == "Test Instrument"
    assert report["parameters"][0]["id"] == "attack"
    assert report["latency_samples"] == 128
