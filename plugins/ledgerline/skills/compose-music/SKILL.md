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
machine-readable direction in `brief.yaml` and the conversation, assumptions, and listening
decisions in `NOTES.md`. Preserve every `brief.yaml` invariant and locked range during refinement.

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

- `brief.yaml`: purpose, trajectory, sections, roles, invariants, and locked ranges;
- `piece.yaml` and `parts/*.yaml`: exact score and performance data;
- `motifs.yaml`: declarative motives whose compiled expansion remains explicit;
- `automation.yaml`: sample-timed part, bus, master, or plugin-parameter lanes;
- `performance.yaml`: explicit legacy, MPE, CLAP-note-expression, or MIDI 2.0 policy per part;
- `render.yaml`: one fail-closed render node per part;
- `mix.yaml`: track/bus routing, inserts, sends, and master targets;
- `assets.yaml`: source, license, hashes, and conversion lineage;
- `review.yaml`: measure-anchored listening notes;
- `NOTES.md`: user intent and decisions.

Keep generated MusicXML, MIDI, audio, manifests, state, and reports under `build/`. Do not hand-edit
them as source. Before structural Studio edits, run `prepare-ids --dry-run`, inspect the report, then
run `prepare-ids`; explicit IDs must remain stable through later edits.

## 4. Draft first, then refine deliberately

1. Write a compact but complete sketch with form, motive, harmony, bass, and essential roles.
2. Validate, compile, and run `refine inspect`. Treat every finding as evidence to investigate, not
   a rule or aesthetic score.
3. Refine in named passes: structure, harmony/voice-leading, orchestration, expression/performance,
   production, then final listening. State the scope, preserved material, evidence IDs, intended
   audible effect, and change budget before each pass.
4. Snapshot before consequential changes. Apply the smallest coherent group of edits, validate,
   and inspect the source diff before continuing.
5. Render exact selected instruments. `build/state.json` is authoritative: never present stale
   score, stem, mix, waveform, engine, preset, or state data as current.
6. Mix stems through authored buses and automation. Meter LUFS/true peak and run time-local analysis;
   metrics are evidence, not taste.
7. Ask the user to compare A/B at matched loudness on concrete axes: theme, pacing, harmony, color,
   realism, depth, and the pass's stated listening check.
8. Lock the approved environment, create a license-aware bundle, and only then record an audio
   golden baseline for a deterministic reference.

Ask for feedback after the representative section, full structural draft, first production render,
and before final delivery. Do not claim musical quality from command success.

## 5. Use Studio for inspection and delegation

LedgerLine Studio is the preferred review surface when the user wants to see, hear, edit, or
delegate work. It exposes a shared timeline with editable piano roll, velocity/CC/pedal/tempo
lanes, score cursor, revision-bound waveforms, spectrogram, real preview meters, mixer, engine
provenance, build jobs, and note inspector.

```powershell
# Project with authored render.yaml; pass the exact media tool when preparing audio.
& "<plugin-root>\scripts\studio.ps1" -Project <project> -Action Start -Prepare `
  -FFmpeg <ffmpeg.exe>

# Legacy project without render.yaml; all three paths are mandatory for audio preparation.
& "<plugin-root>\scripts\studio.ps1" -Project <project> -Action Start -Prepare `
  -FluidSynth <fluidsynth.exe> -SoundFont <instrument.sf2-or-sf3> -FFmpeg <ffmpeg.exe>
```

Use `-Action Status`, `Rebuild`, or `Stop` for the same project, repeating the same explicit engine
arguments for `Rebuild`. `Start` without `-Prepare` remains available for score and MIDI inspection
when audio tools are not configured. `Prepare` and `Rebuild` always require explicit `-FFmpeg`.
For a project without `render.yaml`, they additionally require `-FluidSynth` and `-SoundFont`;
never read an implicit renderer, media tool, or SoundFont from PATH, environment discovery, or a
guessed installation. Return the reported local URL to the user; do not invent a URL or start a
duplicate process. Treat a `stale` Status/Stop result as an identity mismatch: the launcher
deliberately did not signal that PID.

When a Studio delegation exists, process it as an agent work order:

1. Read the next request with `delegate next <project> --json`.
2. Inspect the project with `studio-model`, `inspect`, `duration`, and any relevant score files.
3. If the goal lacks essential musical direction, submit a questions-only proposal. After the user
   answers in Studio, read the pending task again; do not reuse the old proposal.
4. Otherwise create a proposal JSON with `base_revision`, `summary`, `pass`, `scope`, `preserve`,
   `evidence_ids`, `reasoning`, `actions`, `expected_effect`, `listening_check`, and
   `requires_review`. Use stable IDs for events and controls when available.
5. Submit it with `delegate propose <project> <id> <proposal.json> --json`.
6. After source application, poll `delegate show <project> <id> --json`. Treat `building`,
   `rebuild-required`, `build-failed`, and `build-cancelled` as production states, not musical
   completion. Only `ready-for-listening` means the current compile, render, and mix receipts are
   bound to the applied revision.
7. At `ready-for-listening`, present the recorded A/B availability and proposal listening checks.
   Do not turn technical readiness into approval. Run `delegate accept ... --note ...` only after
   the user explicitly accepts what they heard.
8. When the user requests changes after listening, record the exact feedback with
   `delegate revise <project> <id> <feedback> --json`. Read the returned pending task and make a new
   proposal against its current `base_revision`; never undo the already-applied source implicitly.

Review-mode delegations wait for the user in Studio. Safe-auto applies only bounded dynamics,
articulation, and preview-mix edits. Structural edits, stale base revisions, large ranges, invariant
changes, and unsupported fields must require review. Never download assets, substitute instruments,
claim aesthetic success, or mark a `ready-for-listening` task accepted unless the user approved it.

## 6. Fail closed

- Never substitute an unavailable instrument, preset, articulation, keyswitch, sample, or plugin.
- Never use a semantic performance parameter absent from the selected profile.
- Never hide lossy conversion of EXS24, Ableton, Kontakt, per-note expression, or microtonality.
- Never approve an inferred instrument profile without reviewing its evidence and exact token.
- Never call the bundled reference manifest a native third-party VST3/CLAP implementation.
- Never guess staff placement, loop points, latency, plugin state, or redistribution permission.
- Before consequential edits, create a snapshot or isolated proposal preview. Change authored
  source only through a revision-checked Studio transaction with disk undo, then render and A/B
  the exact applied revision; never overwrite source files outside that transaction.
- Quarantine failed external render nodes and preserve their request/error report.
- Surface resource-budget, range, routing, license, and loudness failures to the user.
