from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from fractions import Fraction
from itertools import combinations
from pathlib import Path
from typing import Any

from ledgerline.analysis import inspect_project
from ledgerline.brief import CreativeBrief, load_brief
from ledgerline.build_state import authored_revision, record_refinement
from ledgerline.diagnostics import Diagnostic, ValidationError
from ledgerline.model import DYNAMIC_VELOCITY, Piece
from ledgerline.project import load_piece, validate_piece


@dataclass(frozen=True, slots=True)
class _NoteRecord:
    part: str
    voice: str
    event_id: str | None
    measure: int
    start: Fraction
    end: Fraction
    pitches: tuple[int, ...]
    velocity: int
    articulation: str | None


class _Findings:
    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []
        self._counts: Counter[str] = Counter()

    def add(
        self,
        code: str,
        domain: str,
        severity: str,
        scope: dict[str, Any],
        evidence: dict[str, Any],
        message: str,
        suggestions: list[str] | None = None,
        *,
        gate: str | None = None,
    ) -> dict[str, Any]:
        self._counts[code] += 1
        finding = {
            "id": f"{code}.{self._counts[code]}",
            "domain": domain,
            "severity": severity,
            "gate": gate or ("hard" if severity == "error" else "review"),
            "scope": scope,
            "evidence": evidence,
            "message": message,
            "suggestions": suggestions or [],
        }
        self.items.append(finding)
        return finding


def build_refinement_report(
    root: str | Path,
    output: str | Path | None = None,
) -> dict[str, Any]:
    """Build evidence for agent-led musical refinement without rewriting authored music."""

    root_path = Path(root).resolve()
    revision = authored_revision(root_path)
    findings = _Findings()
    try:
        piece = load_piece(root_path)
    except ValidationError as exc:
        for diagnostic in exc.diagnostics:
            _diagnostic_finding(findings, diagnostic)
        report = _report_envelope(root_path, revision, None, findings, _empty_domains(), {})
        return _finish_report(root_path, revision, report, output)

    brief: CreativeBrief | None = None
    try:
        brief = load_brief(root_path, piece)
    except ValidationError as exc:
        for diagnostic in exc.diagnostics:
            _diagnostic_finding(findings, diagnostic)

    for diagnostic in validate_piece(piece):
        _diagnostic_finding(findings, diagnostic)

    notes = _note_records(piece)
    inspection = inspect_project(root_path)
    domains = {
        "structure": _structure_report(piece, notes, brief, findings),
        "harmony": _harmony_report(piece, notes, inspection, brief, findings),
        "orchestration": _orchestration_report(piece, notes, inspection, brief, findings),
        "expression": _expression_report(piece, notes, findings),
    }
    brief_summary = (
        {"present": True, "path": "brief.yaml", **brief.to_dict()}
        if brief is not None
        else {"present": False, "path": "brief.yaml"}
    )
    if brief is None and not (root_path / "brief.yaml").is_file():
        findings.add(
            "structure.brief_missing",
            "structure",
            "warning",
            {"from": "1:1", "to": f"{piece.measures}:end", "parts": []},
            {"brief_path": "brief.yaml"},
            "No creative brief records the trajectory, protected material, or review checkpoints.",
            ["Write brief.yaml before making a broad refinement pass."],
        )
    report = _report_envelope(root_path, revision, piece, findings, domains, brief_summary)
    return _finish_report(root_path, revision, report, output)


def _report_envelope(
    root: Path,
    revision: str,
    piece: Piece | None,
    findings: _Findings,
    domains: dict[str, Any],
    brief: dict[str, Any],
) -> dict[str, Any]:
    hard = [item for item in findings.items if item["gate"] == "hard"]
    review = [item for item in findings.items if item["gate"] == "review"]
    status = "blocked" if hard else "review" if review else "ok"
    return {
        "schema_version": "1",
        "status": status,
        "project": str(root),
        "authored_revision": revision,
        "title": piece.title if piece is not None else None,
        "brief": brief,
        "gates": {
            "hard": {
                "status": "failed" if hard else "passed",
                "count": len(hard),
                "finding_ids": [item["id"] for item in hard],
            },
            "review": {
                "status": "attention" if review else "clear",
                "count": len(review),
                "finding_ids": [item["id"] for item in review],
            },
        },
        "domains": domains,
        "findings": findings.items,
        "claims": [
            "Findings expose evidence and possible listening checks; they do not rewrite music.",
            "Review findings are style-dependent and may be waived with a recorded reason.",
            "No aggregate aesthetic or quality score is calculated.",
        ],
    }


def _structure_report(
    piece: Piece,
    notes: list[_NoteRecord],
    brief: CreativeBrief | None,
    findings: _Findings,
) -> dict[str, Any]:
    sections = []
    if brief is not None:
        for section in brief.sections:
            start = _brief_anchor_position(piece, section.from_anchor)
            end = _brief_anchor_position(piece, section.to_anchor)
            selected = [note for note in notes if start <= note.start < end]
            pitch_values = [pitch for note in selected for pitch in note.pitches]
            measure_span = max(
                1,
                _anchor_measure(section.to_anchor)
                - _anchor_measure(section.from_anchor)
                + 1,
            )
            metric = {
                "id": section.id,
                "function": section.function,
                "from": section.from_anchor,
                "to": section.to_anchor,
                "note_events": len(selected),
                "events_per_measure": round(len(selected) / measure_span, 3),
                "active_parts": sorted({note.part for note in selected}),
                "lowest_midi": min(pitch_values) if pitch_values else None,
                "highest_midi": max(pitch_values) if pitch_values else None,
                "median_midi": statistics.median(pitch_values) if pitch_values else None,
            }
            sections.append(metric)
            if not selected:
                findings.add(
                    "structure.empty_section",
                    "structure",
                    "warning",
                    {"from": section.from_anchor, "to": section.to_anchor, "parts": []},
                    {"section": section.id, "function": section.function},
                    f"Section {section.id!r} contains no note events.",
                    [
                        "Confirm that the empty section is an intentional silence or add "
                        "authored material."
                    ],
                )
    return {
        "measure_count": piece.measures,
        "tempo_changes": [
            {"at": f"{item.measure}:{float(item.beat):g}", "bpm": item.bpm}
            for item in piece.tempo_changes
        ],
        "sections": sections,
        "motif_placements": len(piece.motif_expansions),
        "checkpoint_sequence": list(brief.checkpoints) if brief is not None else [],
    }


def _harmony_report(
    piece: Piece,
    notes: list[_NoteRecord],
    inspection: dict[str, Any],
    brief: CreativeBrief | None,
    findings: _Findings,
) -> dict[str, Any]:
    sonorities = inspection["harmony"]
    unlabeled = [
        item
        for item in sonorities
        if item["chord"] is None and len(item["pitch_classes"]) >= 3
    ]
    for item in unlabeled[:12]:
        findings.add(
            "harmony.unlabelled_sonority",
            "harmony",
            "warning",
            {"from": item["at"], "to": item["at"], "parts": []},
            {"midi_pitches": item["midi_pitches"], "pitch_classes": item["pitch_classes"]},
            "This vertical sonority does not match a basic triad or seventh-chord template.",
            ["Check whether non-chord tones and their resolutions are intentional."],
        )

    low_spacing = []
    for item in sonorities:
        pitches = sorted(set(item["midi_pitches"]))
        if len(pitches) >= 2 and pitches[1] < 55 and pitches[1] - pitches[0] <= 4:
            low_spacing.append({"at": item["at"], "lower": pitches[0], "upper": pitches[1]})
    if low_spacing and _style_enabled(brief, "low_register_spacing"):
        findings.add(
            "harmony.low_register_spacing",
            "harmony",
            "warning",
            {"from": low_spacing[0]["at"], "to": low_spacing[-1]["at"], "parts": []},
            {"occurrences": len(low_spacing), "examples": low_spacing[:8]},
            "Close intervals occur in the low register and may reduce harmonic clarity.",
            ["Listen in the actual voicing and consider widening only the documented collisions."],
        )

    parallels = _parallel_candidates(notes, piece)
    for kind in ("parallel_fifths", "parallel_octaves"):
        selected = [item for item in parallels if item["kind"] == kind]
        if selected and _style_enabled(brief, kind):
            findings.add(
                f"harmony.{kind}",
                "harmony",
                "warning",
                {
                    "from": selected[0]["from"],
                    "to": selected[-1]["to"],
                    "parts": selected[0]["parts"],
                },
                {"occurrences": len(selected), "examples": selected[:8]},
                f"Detected {len(selected)} aligned {kind.replace('_', ' ')} candidate(s).",
                ["Review against the intended style; parallel motion may be deliberate."],
            )
    return {
        "sonorities": sonorities,
        "unlabelled_sonorities": len(unlabeled),
        "low_register_spacing_candidates": low_spacing,
        "parallel_motion_candidates": parallels,
    }


def _orchestration_report(
    piece: Piece,
    notes: list[_NoteRecord],
    inspection: dict[str, Any],
    brief: CreativeBrief | None,
    findings: _Findings,
) -> dict[str, Any]:
    declared_roles = defaultdict(list)
    if brief is not None:
        for role in brief.roles:
            declared_roles[role.part].append(
                {"from": role.from_anchor, "to": role.to_anchor, "role": role.role}
            )
        active = {note.part for note in notes}
        for part in sorted(active - set(declared_roles)):
            findings.add(
                "orchestration.role_missing",
                "orchestration",
                "warning",
                {"from": "1:1", "to": f"{piece.measures}:end", "parts": [part]},
                {"part": part},
                f"Active part {part!r} has no foreground, support, bass, or texture role "
                "in brief.yaml.",
                ["Declare its time-bounded role before changing orchestration density."],
            )

    collisions = _register_collisions(notes, piece)
    for collision in collisions[:12]:
        findings.add(
            "orchestration.register_collision",
            "orchestration",
            "warning",
            {
                "from": collision["from"],
                "to": collision["to"],
                "parts": collision["parts"],
            },
            {
                "shared_onsets": collision["shared_onsets"],
                "overlap_midi": collision["overlap_midi"],
            },
            "Two parts repeatedly occupy an overlapping register at aligned onsets.",
            [
                "Confirm their roles, then separate register, rhythm, articulation, or "
                "level if masked."
            ],
        )
    return {
        "parts": [
            {**item, "roles": declared_roles.get(item["id"], [])} for item in inspection["parts"]
        ],
        "register_collision_candidates": collisions,
    }


def _expression_report(
    piece: Piece,
    notes: list[_NoteRecord],
    findings: _Findings,
) -> dict[str, Any]:
    reports = []
    for part in piece.parts:
        selected = [note for note in notes if note.part == part.id]
        velocities = [note.velocity for note in selected]
        controls = Counter(control.kind for control in part.controls)
        articulations = Counter(
            note.articulation for note in selected if note.articulation is not None
        )
        report = {
            "part": part.id,
            "note_events": len(selected),
            "velocity_min": min(velocities) if velocities else None,
            "velocity_max": max(velocities) if velocities else None,
            "velocity_distinct": len(set(velocities)),
            "articulations": dict(sorted(articulations.items())),
            "controls": dict(sorted(controls.items())),
        }
        reports.append(report)
        expressive_controls = any(
            control.kind in {"cc", "performance"} for control in part.controls
        )
        if len(selected) >= 4 and len(set(velocities)) <= 1 and not expressive_controls:
            findings.add(
                "expression.flat_profile",
                "expression",
                "warning",
                {"from": "1:1", "to": f"{piece.measures}:end", "parts": [part.id]},
                {
                    "note_events": len(selected),
                    "distinct_velocities": len(set(velocities)),
                    "expressive_controls": False,
                },
                f"Part {part.id!r} has a uniform attack profile and no expression controls.",
                ["Author a phrase arc only if the uniform delivery is not intentional."],
            )
    return {"parts": reports}


def _note_records(piece: Piece) -> list[_NoteRecord]:
    from ledgerline.timeline import Timeline

    timeline = Timeline(piece)
    result = []
    for part in piece.parts:
        profile = piece.profiles[part.profile_id]
        for measure_number, measure in part.measures.items():
            for voice, events in measure.voices.items():
                for scheduled in timeline.schedule_voice(measure_number, events):
                    event = scheduled.event
                    if event.pitches:
                        velocity = event.velocity or DYNAMIC_VELOCITY.get(event.dynamic or "mf", 76)
                        result.append(
                            _NoteRecord(
                                part.id,
                                voice,
                                event.id,
                                measure_number,
                                scheduled.start_whole,
                                scheduled.start_whole + scheduled.duration,
                                tuple(
                                    pitch.midi + profile.transposition for pitch in event.pitches
                                ),
                                velocity,
                                event.articulation,
                            )
                        )
    return result


def _parallel_candidates(notes: list[_NoteRecord], piece: Piece) -> list[dict[str, Any]]:
    onsets = sorted({note.start for note in notes})
    representatives: dict[Fraction, dict[str, int]] = {}
    for onset in onsets:
        by_part: dict[str, list[int]] = defaultdict(list)
        for note in notes:
            if note.start == onset and len(note.pitches) == 1:
                by_part[note.part].append(note.pitches[0])
        representatives[onset] = {
            part: pitches[0] for part, pitches in by_part.items() if len(pitches) == 1
        }
    candidates = []
    previous: dict[tuple[str, str], tuple[Fraction, int, int]] = {}
    for onset in onsets:
        current = representatives[onset]
        for first, second in combinations(sorted(current), 2):
            pair = (first, second)
            lower, upper = current[first], current[second]
            earlier = previous.get(pair)
            if earlier is not None:
                earlier_onset, earlier_lower, earlier_upper = earlier
                earlier_interval = abs(earlier_upper - earlier_lower) % 12
                interval = abs(upper - lower) % 12
                lower_motion = lower - earlier_lower
                upper_motion = upper - earlier_upper
                same_direction = lower_motion * upper_motion > 0
                if same_direction and interval == earlier_interval and interval in {0, 7}:
                    candidates.append(
                        {
                            "kind": "parallel_octaves" if interval == 0 else "parallel_fifths",
                            "from": _position_anchor(piece, earlier_onset),
                            "to": _position_anchor(piece, onset),
                            "parts": [first, second],
                            "motions": [lower_motion, upper_motion],
                        }
                    )
            previous[pair] = (onset, lower, upper)
    return candidates


def _register_collisions(notes: list[_NoteRecord], piece: Piece) -> list[dict[str, Any]]:
    onsets = sorted({note.start for note in notes})
    grouped: dict[tuple[str, str], list[tuple[Fraction, int, int]]] = defaultdict(list)
    for onset in onsets:
        by_part: dict[str, list[int]] = defaultdict(list)
        for note in notes:
            if note.start <= onset < note.end:
                by_part[note.part].extend(note.pitches)
        for first, second in combinations(sorted(by_part), 2):
            low = max(min(by_part[first]), min(by_part[second]))
            high = min(max(by_part[first]), max(by_part[second]))
            if low <= high:
                grouped[(first, second)].append((onset, low, high))
    result = []
    for parts, items in grouped.items():
        if len(items) < 2:
            continue
        result.append(
            {
                "parts": list(parts),
                "from": _position_anchor(piece, items[0][0]),
                "to": _position_anchor(piece, items[-1][0]),
                "shared_onsets": len(items),
                "overlap_midi": [min(item[1] for item in items), max(item[2] for item in items)],
            }
        )
    return result


def _diagnostic_finding(findings: _Findings, diagnostic: Diagnostic) -> None:
    domain = _diagnostic_domain(diagnostic.code)
    findings.add(
        diagnostic.code.replace("_", "-"),
        domain,
        diagnostic.severity,
        {"path": diagnostic.path, "parts": []},
        {"diagnostic_code": diagnostic.code},
        diagnostic.message,
        [],
        gate="hard" if diagnostic.severity == "error" else "review",
    )


def _diagnostic_domain(code: str) -> str:
    if code.startswith(("instrument", "profile")):
        return "orchestration"
    if code.startswith(("control", "pedal", "expression")):
        return "expression"
    if code.startswith(("piece", "measure", "tie", "part", "brief")):
        return "structure"
    return "project"


def _style_enabled(brief: CreativeBrief | None, name: str) -> bool:
    return brief is None or brief.style_checks.get(name, "review") == "review"


def _measure_starts(piece: Piece) -> dict[int, Fraction]:
    starts = {}
    cursor = Fraction(0)
    for number in range(1, piece.measures + 1):
        starts[number] = cursor
        cursor += piece.time_at(number).length
    return starts


def _brief_anchor_position(piece: Piece, anchor: str) -> Fraction:
    measure = _anchor_measure(anchor)
    starts = _measure_starts(piece)
    raw_beat = anchor.split(":", 1)[1]
    if raw_beat == "end":
        return starts[measure] + piece.time_at(measure).length
    return starts[measure] + (Fraction(raw_beat) - 1) / piece.time_at(measure).beat_type


def _anchor_measure(anchor: str) -> int:
    return int(anchor.split(":", 1)[0])


def _position_anchor(piece: Piece, position: Fraction) -> str:
    starts = _measure_starts(piece)
    for measure in range(1, piece.measures + 1):
        start = starts[measure]
        end = start + piece.time_at(measure).length
        if start <= position < end:
            beat = (position - start) * piece.time_at(measure).beat_type + 1
            return f"{measure}:{float(beat):g}"
    return f"{piece.measures}:end"


def _empty_domains() -> dict[str, Any]:
    return {
        "structure": {},
        "harmony": {},
        "orchestration": {},
        "expression": {},
    }


def _finish_report(
    root: Path,
    revision: str,
    report: dict[str, Any],
    output: str | Path | None,
) -> dict[str, Any]:
    if authored_revision(root) != revision:
        raise ValueError("authored project changed while the refinement report was being built")
    if output is None:
        return report
    path = Path(output).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    record_refinement(root, path)
    return report
