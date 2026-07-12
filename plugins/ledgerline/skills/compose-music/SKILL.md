---
name: compose-music
description: Compose, arrange, orchestrate, validate, render, mix, compare, run LedgerLine Studio, process AI delegation requests, and reversibly refine original music with LedgerLine. Use when Codex should ask the user for musical direction, then author explicit notes, chords, rhythm, form, motifs, dynamics, CC/pedal/keyswitches, microtonal or Korean gestures, MusicXML/MIDI, SoundFont/SFZ/VST3/CLAP renders, buses and automation, or revisions based on listening feedback.
---

# Compose with LedgerLine

Treat LedgerLine as a deterministic workbench. Author musical decisions yourself; do not ask a
validator, renderer, sample library, or generator to invent or silently repair the composition.

Resolve the plugin root as two directories above this file. Invoke LedgerLine only through
`<plugin-root>/scripts/ledgerline.ps1`.

## 1. Establish direction before writing

Read the conversation. If the user has not supplied a concrete brief, ask compact questions for:

- purpose, duration, and delivery format;
- emotional trajectory and stylistic references;
- ensemble, required/forbidden sounds, and performance difficulty;
- form, tempo/groove, harmonic language, and desired ending;
- listening checkpoints and what existing material must remain unchanged.

Do not author score events until the user answers or explicitly delegates these choices. Record the
brief, assumptions, form map, and listening decisions in `NOTES.md`.

For non-expert users, prefer plain musical choices over technical questions. Ask about mood,
trajectory, reference feelings, length, and what should remain unchanged. Choose the orchestration,
registers, voicings, CC lanes, articulations, mix routing, and render details yourself, then surface
only consequential assumptions and listening checkpoints.

## 2. Gate environment and assets

Run `bootstrap.ps1 -Plan`. If setup is needed, show its destination, network sources, dependencies,
and system changes; run `-Apply` only after explicit approval. Then run `doctor --json`.

Use unmanaged FluidSynth, sfizz, FFmpeg, MuseScore, plugins, and libraries only through explicit
authored paths approved by the user. For signed packs, show the generated setup plan and apply only
its unexpired single-use token. Never change PATH or the registry.

Before choosing sounds, inspect their presets/zones and license. Never infer an articulation,
keyswitch, microphone position, or redistribution right from a filename.

## 3. Author explicit source documents

Read [references/authoring-contract.md](references/authoring-contract.md) before editing YAML. Read
[references/musical-quality.md](references/musical-quality.md) for composition/orchestration work.
Read [references/cli-and-environment.md](references/cli-and-environment.md) for command details.

Use only the documents needed by the project:

- `piece.yaml` and `parts/*.yaml`: exact score and performance data;
- `motifs.yaml`: declarative motives whose compiled expansion remains explicit;
- `automation.yaml`: sample-timed part, bus, master, or plugin-parameter lanes;
- `performance.yaml`: explicit legacy, MPE, CLAP-note-expression, or MIDI 2.0 policy per part;
- `render.yaml`: one fail-closed render node per part;
- `mix.yaml`: track/bus routing, inserts, sends, and master targets;
- `assets.yaml`: source, license, hashes, and conversion lineage;
- `review.yaml`: measure-anchored listening notes;
- `NOTES.md`: user intent and decisions.

Keep generated MusicXML, MIDI, audio, manifests, and reports under `build/`. Do not hand-edit them as
source.

## 4. Work in reversible audible checkpoints

1. Author a short representative section; validate and compile it.
2. Inspect harmony, density, register, range, instrument coverage, expression-plan conflicts, and
   predicted duration.
3. Render exact selected instruments; mix stems through authored buses and automation.
4. Meter LUFS/true peak and run time-local analysis. Treat metrics as evidence, not taste.
5. Build `visual-review` and ask the user to listen on concrete axes: theme, pacing, harmony,
   color, realism, and depth.
6. Snapshot before consequential changes. Apply requested revisions to a named part/measure scope.
7. Render A/B versions and compare at matched loudness. Record approval or unresolved notes.
8. Lock the environment and create a license-aware bundle for delivery.
9. Record an audio golden baseline only after the user approves the deterministic reference.

Ask for feedback after the representative section, full structural draft, first production render,
and before final delivery. Do not claim musical quality from command success.

## 5. Use Studio for inspection and delegation

LedgerLine Studio is the preferred review surface when the user wants to see, hear, or delegate
work. It exposes a shared timeline with piano roll, velocity lane, score cursor, waveform,
spectrogram, track mixer, and note inspector.

```powershell
& "<plugin-root>\scripts\ledgerline.ps1" studio <project> --no-open
```

When a Studio delegation exists, process it as an agent work order:

1. Read the next request with `delegate next <project> --json`.
2. Inspect the project with `studio-model`, `inspect`, `duration`, and any relevant score files.
3. If the goal lacks essential musical direction, return questions in the proposal instead of
   guessing the musical intent.
4. Otherwise create a proposal JSON with `summary`, optional `reasoning`, `actions`, and
   `listening_check`.
5. Submit it with `delegate propose <project> <id> <proposal.json> --json`.

Review-mode delegations wait for the user in Studio. Safe-auto delegations apply immediately only
when every action is in the supported safe edit set. Never download assets, substitute instruments,
or claim aesthetic success while processing a delegation unless the user approved that scope.

## 6. Fail closed

- Never substitute an unavailable instrument, preset, articulation, keyswitch, sample, or plugin.
- Never use a semantic performance parameter absent from the selected profile.
- Never hide lossy conversion of EXS24, Ableton, Kontakt, per-note expression, or microtonality.
- Never approve an inferred instrument profile without reviewing its evidence and exact token.
- Never call the bundled reference manifest a native third-party VST3/CLAP implementation.
- Never guess staff placement, loop points, latency, plugin state, or redistribution permission.
- Never overwrite the original project for scoped edits; write a new project and A/B it.
- Quarantine failed external render nodes and preserve their request/error report.
- Surface resource-budget, range, routing, license, and loudness failures to the user.
