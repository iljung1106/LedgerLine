# LedgerLine Agent Guide

## First use

1. Run `ledgerline doctor --json`.
2. If the signed Starter assets are needed, run `ledgerline setup plan --packs starter --json`.
3. Show the user the exact download size, licenses, destinations, and system changes.
4. Do not download or install anything until the user explicitly approves that plan.
5. After setup, run `ledgerline doctor --json` again and require the smoke render to pass.

## Before writing music

Ask the user:

- What is the music for?
- What should the listener feel? Ask for three concrete words.
- What references describe the form, harmony, groove, or sound world?
- What length and delivery formats are required?
- Which instruments are required or forbidden?
- What should remain recognizable after revision?
- At which checkpoints does the user want to listen?

Record the answers and the intended form in `NOTES.md`. Run `ledgerline doctor --json` and inspect
the installed instrument coverage before choosing the ensemble. Never invent an installed patch.

## Authoring loop

1. Write a short motif and an explicit section plan.
2. Author pitches and durations in `parts/*.yaml`.
3. Run `ledgerline validate` after every small edit.
4. Run `ledgerline compile` and inspect MusicXML or the MIDI piano roll.
5. Render stems and the preview mix with a strict instrument policy.
6. Measure clipping and loudness; do not convert those measurements into an aesthetic score.
7. Change one musical or mix decision at a time and record why in `NOTES.md`.
8. Before consequential revision, run `snapshot`; use `apply-edits --output` for scoped changes.
9. Run `compare` at matched loudness and record listening conclusions in `review.yaml`.
10. Run `lock` and `bundle` for reproducible, license-aware delivery.

## Multiple staves

Declare notation staves in a part when one instrument needs more than one staff. Number them
contiguously from 1 and give each one an explicit clef. In a multi-staff part, every authored event
must name its staff; LedgerLine will not guess a hand or silently move a note between staves.

```yaml
format: 1
part: piano
staves:
  - {number: 1, name: right, clef: {sign: G, line: 2}}
  - {number: 2, name: left, clef: {sign: F, line: 4}}
measures:
  "1":
    v1:
      - {p: [C4, E4, G4], d: 1/1, staff: 1}
    v2:
      - {p: C3, d: 1/1, staff: 2}
```

Staff placement affects MusicXML engraving only. MIDI pitch and timing remain identical. Because
`staff` belongs to each event, a voice may cross between declared staves deliberately. Single-staff
parts may omit both `staves` and event-level `staff`; the instrument profile's clef is then used.

## Performance controls

Author channel controls on the part timeline, independently of note voices. Use semantic pedal
events instead of raw CC64, and semantic keyswitch names declared by the instrument profile.

```yaml
controls:
  - {at: "1:1", type: cc, controller: 11, value: 72}
  - {at: "1:1", type: pedal, action: down}
  - {at: "1:3", type: pedal, action: change}
  - {at: "2:1", type: keyswitch, name: legato, velocity: 64, duration: 1/32}
  - {at: "2:4", type: pedal, action: up}
```

CC0 and CC32 remain owned by the profile's bank selection. Pedal state must be consistent and end
up. A missing keyswitch mapping is a hard error. MIDI contains the playback events; MusicXML uses
standard pedal directions and retains CC/keyswitch data as hidden LedgerLine directions.

## Musical checks

- Keep important lines in a deliberate register and within the instrument's comfortable range.
- Treat range, transposition, breath, bowing, hand span, and articulation availability as physical
  constraints rather than MIDI program numbers.
- Check every leap, tendency tone, suspension, doubled leading tone, voice crossing, bass motion,
  and cadence against the chosen idiom.
- Give phrases dynamic shapes. Use silence and release as musical events.
- Render stems so balance and articulation problems can be localized.
- Missing instruments or articulations must stop the render unless the user explicitly approves a
  named substitution or omission.

## Human listening checkpoints

Objective tools cannot decide whether a melody is memorable, harmony is moving, or a groove feels
good. Ask the user to listen at least after the first 8-bar sketch, the structural draft, the first
mix, and before final delivery.

Use `render.yaml` when a piece mixes SoundFont, SFZ, external VST3/CLAP-host, or frozen nodes. Bind
each part exactly once and author latency, tail, state, and resource ceilings. Never substitute a
renderer or preset after a node fails; preserve the quarantine report and ask for direction.
