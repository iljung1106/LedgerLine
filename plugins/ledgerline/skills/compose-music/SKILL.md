---
name: compose-music
description: Compose, arrange, orchestrate, validate, render, and refine original music with the LedgerLine agent workbench. Use when Codex should ask the user for musical direction, author pitches/chords/rhythm/form in LedgerLine YAML, analyze harmony and ranges, control dynamics/CC/pedal/keyswitches, produce MusicXML or MIDI, render virtual instruments, mix audio, or revise a piece from listening feedback.
---

# Compose with LedgerLine

Treat LedgerLine as a workbench, not a composer. Author every musical decision yourself. Never let
a validator, renderer, or library choice silently generate, repair, omit, or substitute music.

Resolve the plugin root as two directories above this file. Invoke LedgerLine only through
`<plugin-root>/scripts/ledgerline.ps1`. If it reports that the runtime is missing, follow the
bootstrap gate below.

## 1. Establish direction before writing

Read the conversation first. If the user has not already supplied a sufficiently concrete brief,
ask a compact set of questions covering:

- intended use and target duration;
- emotional trajectory and stylistic references;
- ensemble or sonic palette;
- form, tempo/groove, and harmonic preferences;
- notation, rendering, delivery, and difficulty constraints.

Do not author score events until the user answers or explicitly delegates those choices. When the
brief is already detailed, restate only consequential assumptions and proceed. Record the agreed
brief and listening notes in `NOTES.md`.

Treat the brief as sufficient only when purpose, approximate duration, emotional/style direction,
ensemble, performance difficulty, and desired deliverables are either specified or explicitly
delegated. Interpret a requested duration as a target unless the user requires frame-accurate timing.

## 2. Gate environment changes

Run:

```powershell
& "<plugin-root>\scripts\bootstrap.ps1" -Plan
```

If setup is required, present its exact destination, network sources, and system changes. Run
`-Apply` only after explicit user approval. Then run:

```powershell
& "<plugin-root>\scripts\ledgerline.ps1" doctor --json
```

Unmanaged FluidSynth, MuseScore, FFmpeg, or SoundFonts are informative only. Pass their paths
explicitly after the user approves them. Never modify PATH, the registry, or system-wide software.

For the signed Starter instrument pack, create a setup plan, show its bytes/license/destination,
wait for approval, and apply that exact plan/token. Never synthesize consent or reuse a token.

## 3. Author the piece

Read [references/authoring-contract.md](references/authoring-contract.md) before creating or changing
project YAML. Read [references/musical-quality.md](references/musical-quality.md) when composing,
arranging, or critiquing. Read [references/cli-and-environment.md](references/cli-and-environment.md)
when setup, rendering, or command details are needed.

Create or edit only authored files:

- `piece.yaml`: global meter, tempo, key, and part/profile bindings;
- `parts/*.yaml`: exact measure-local voices, notes, rests, staves, and performance controls;
- `mix.yaml`: explicit gain, pan, reverb, and master targets;
- `NOTES.md`: brief, form, decisions, listening feedback, and unresolved questions.

Keep all generated files under `build/`. Never hand-edit generated MusicXML, MIDI, manifests, or
audio as if they were authored sources.

## 4. Work in audible checkpoints

Use this loop deliberately:

1. Author a short structural draft.
2. Validate and fix only reported contract errors.
3. Compile and inspect harmony, density, register, ranges, and instrument coverage.
4. Render with the exact selected instrument assets.
5. Measure clipping/loudness without treating metrics as aesthetic quality.
6. Ask the user to listen at meaningful checkpoints.
7. Change traceable musical or mix decisions and record why.

Always request listening feedback after the first representative section, the full structural
draft, the first production render, and before final delivery. A successful command is not proof
that a melody, arrangement, performance, or mix is good.

## 5. Fail closed

- Never silently replace an unavailable instrument, articulation, keyswitch, or sound library.
- Never invent a keyswitch mapping; it must exist in the instrument profile.
- Never use raw CC64; author semantic pedal events.
- Never guess staff placement in a multi-staff part.
- Never overwrite the user's musical material merely because an analyzer reports a warning.
- Preserve user changes and explain any requested compromise.
