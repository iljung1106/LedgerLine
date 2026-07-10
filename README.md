# LedgerLine

LedgerLine is an agent-native music workbench. A coding agent writes the music itself in small,
reviewable YAML documents. LedgerLine validates the documents, compiles them to MusicXML and MIDI,
renders installed virtual instruments, and reports objective problems without making aesthetic
decisions on the agent's behalf.

This repository is a greenfield replacement for the discarded CodexSongWriter project. It does not
contain a baseline composer, auto-orchestrator, quality score, or automatic musical repair loop.

## Current milestone

The current milestone provides:

- environment discovery through `ledgerline doctor`;
- Ed25519-signed pack catalogs, single-use setup plans, and atomic `setup apply`;
- a reproducible Starter `.llpack` based on MuseScore General 0.2;
- measure-local score documents;
- validation of meter, pitches, ranges, dynamics, articulations, performance controls, and staves;
- deterministic MusicXML and MIDI compilation;
- authored CC, sustain-pedal, semantic keyswitch, and grand-staff compilation;
- FluidSynth rendering with an explicitly selected SF2/SF3;
- an example project and an agent operating guide.

## Project documents

```text
my-piece/
  piece.yaml
  parts/
    piano.yaml
    violin.yaml
  mix.yaml
  NOTES.md
```

Generated files are written below `my-piece/build/` and must never be hand-edited.

## Quick start

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\ledgerline.exe doctor --json
.\.venv\Scripts\ledgerline.exe validate examples\nocturne
.\.venv\Scripts\ledgerline.exe compile examples\nocturne
.\.venv\Scripts\ledgerline.exe inspect examples\nocturne --json
.\.venv\Scripts\ledgerline.exe compile examples\performance-demo
```

## Codex plugin

The repository is also a Codex marketplace. After it is published:

```powershell
codex plugin marketplace add iljung1106/LedgerLine --ref main
codex plugin add ledgerline@ledgerline
```

Start a new Codex task and invoke `$compose-music`. The skill first establishes the user's
musical direction, then gates runtime/sample downloads behind explicit plans and consent. It uses a
managed Python environment and never changes PATH or the registry.

Rendering requires FluidSynth and an SF2/SF3. Both may be passed explicitly; LedgerLine never
silently selects an unrelated instrument library.

```powershell
.\.venv\Scripts\ledgerline.exe render examples\nocturne `
  --fluidsynth "C:\path\to\fluidsynth.exe" `
  --soundfont "C:\path\to\soundfont.sf3"

.\.venv\Scripts\ledgerline.exe mix examples\nocturne --json
.\.venv\Scripts\ledgerline.exe meter examples\nocturne\build\mix.wav --json
```

## Audio packs

Large sample libraries do not live in Git. Build the audited Starter artifact, inspect the exact
plan, and pass its random token only after the user approves it:

```powershell
.\.venv\Scripts\python.exe scripts\build_starter_pack.py `
  --source-dir .cache\starter-source `
  --output dist\ledgerline-starter-0.2.0-ll.1.llpack

.\.venv\Scripts\ledgerline.exe setup plan --packs starter `
  --output starter-plan.json --json
.\.venv\Scripts\ledgerline.exe setup apply --plan starter-plan.json `
  --consent "TOKEN_FROM_THE_APPROVED_PLAN" --json
```

The signed catalog points to the immutable GitHub v0.2.0 release asset. Release creation must upload
the locally reproduced artifact with the exact catalog hash. The private key is ignored by Git and
must never be published.
