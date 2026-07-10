from __future__ import annotations

import statistics
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from ledgerline.model import Piece
from ledgerline.project import load_piece

CHORD_TEMPLATES = {
    (0, 4, 7): "",
    (0, 3, 7): "m",
    (0, 3, 6): "dim",
    (0, 4, 8): "aug",
    (0, 4, 7, 10): "7",
    (0, 4, 7, 11): "maj7",
    (0, 3, 7, 10): "m7",
    (0, 3, 6, 10): "m7b5",
    (0, 3, 6, 9): "dim7",
}
SHARP_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
FLAT_NAMES = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]


@dataclass(frozen=True, slots=True)
class NoteSpan:
    part: str
    start: Fraction
    end: Fraction
    midi: int


def inspect_project(root: str | Path) -> dict:
    piece = load_piece(root)
    spans = _note_spans(piece)
    onsets = sorted({span.start for span in spans})
    harmony: list[dict] = []
    use_flats = piece.key_at(1).fifths < 0
    for onset in onsets:
        sounding = sorted(span.midi for span in spans if span.start <= onset < span.end)
        if not sounding:
            continue
        pitch_classes = sorted(set(note % 12 for note in sounding))
        harmony.append(
            {
                "at": _anchor(piece, onset),
                "midi_pitches": sounding,
                "pitch_classes": pitch_classes,
                "bass": _pitch_name(sounding[0] % 12, use_flats),
                "chord": _chord_label(pitch_classes, sounding[0] % 12, use_flats),
            }
        )
    part_reports: list[dict] = []
    for part in piece.parts:
        profile = piece.profiles[part.profile_id]
        notes = [span.midi for span in spans if span.part == part.id]
        outside_comfortable = [
            note
            for note in notes
            if not profile.comfortable_low.midi <= note <= profile.comfortable_high.midi
        ]
        part_reports.append(
            {
                "id": part.id,
                "profile": profile.id,
                "note_count": len(notes),
                "notes_per_measure": len(notes) / piece.measures,
                "lowest_midi": min(notes) if notes else None,
                "highest_midi": max(notes) if notes else None,
                "median_midi": statistics.median(notes) if notes else None,
                "comfortable_range_excursions": len(outside_comfortable),
            }
        )
    return {
        "schema_version": "1",
        "status": "ok",
        "project": str(piece.root),
        "title": piece.title,
        "harmony": harmony,
        "parts": part_reports,
        "claims": [
            "Chord labels are pitch-class descriptions, not aesthetic judgments.",
            "No finding in this report rewrites authored files.",
        ],
    }


def _note_spans(piece: Piece) -> list[NoteSpan]:
    starts: dict[int, Fraction] = {}
    cursor = Fraction(0)
    for number in range(1, piece.measures + 1):
        starts[number] = cursor
        cursor += piece.time_at(number).length
    spans: list[NoteSpan] = []
    for part in piece.parts:
        profile = piece.profiles[part.profile_id]
        for number, measure in part.measures.items():
            for events in measure.voices.values():
                position = starts[number]
                for event in events:
                    for pitch in event.pitches:
                        spans.append(
                            NoteSpan(
                                part=part.id,
                                start=position,
                                end=position + event.duration,
                                midi=pitch.midi + profile.transposition,
                            )
                        )
                    position += event.duration
    return spans


def _chord_label(pitch_classes: list[int], bass: int, use_flats: bool) -> str | None:
    pcs = set(pitch_classes)
    for root in pitch_classes:
        intervals = tuple(sorted((pitch_class - root) % 12 for pitch_class in pcs))
        quality = CHORD_TEMPLATES.get(intervals)
        if quality is not None:
            label = _pitch_name(root, use_flats) + quality
            if bass != root:
                label += "/" + _pitch_name(bass, use_flats)
            return label
    return None


def _pitch_name(pitch_class: int, use_flats: bool) -> str:
    return (FLAT_NAMES if use_flats else SHARP_NAMES)[pitch_class]


def _anchor(piece: Piece, position: Fraction) -> str:
    cursor = Fraction(0)
    for number in range(1, piece.measures + 1):
        length = piece.time_at(number).length
        if cursor <= position < cursor + length:
            time = piece.time_at(number)
            beat = (position - cursor) / Fraction(1, time.beat_type) + 1
            return f"{number}:{float(beat):g}"
        cursor += length
    return f"{piece.measures}:end"
