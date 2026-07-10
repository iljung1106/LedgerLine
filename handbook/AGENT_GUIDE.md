# LedgerLine Agent Guide

## First use

1. Run `ledgerline doctor --json`.
2. If audio is unavailable, run `ledgerline setup plan --packs starter,core --json`.
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

