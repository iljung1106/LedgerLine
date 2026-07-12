from __future__ import annotations

import yaml

from ledgerline.compiler import compile_project
from ledgerline.project import load_piece


def test_motif_transpose_and_retrograde_expand_into_explicit_events(example_project) -> None:
    motifs = {
        "format": 1,
        "motifs": {
            "cell": {
                "events": [
                    {"p": "C4", "d": "1/2"},
                    {"p": "D4", "d": "1/2"},
                ]
            }
        },
        "placements": [
            {
                "motif": "cell",
                "part": "cello",
                "measure": 1,
                "voice": "v1",
                "transform": [
                    {"type": "transpose", "semitones": 12},
                    {"type": "retrograde"},
                ],
            }
        ],
    }
    (example_project / "motifs.yaml").write_text(
        yaml.safe_dump(motifs, sort_keys=False), encoding="utf-8"
    )
    piece = load_piece(example_project)
    events = piece.parts[1].measures[1].voices["v1"]
    assert [str(event.pitches[0]) for event in events] == ["D5", "C5"]
    report = compile_project(example_project)
    assert (example_project / "build" / "motif-expansion.json").is_file()
    assert report["status"] == "ok"
