# LedgerLine

LedgerLine is an agent-native music workbench. A coding agent writes the music itself in small,
reviewable YAML documents. LedgerLine validates the documents, compiles them to MusicXML and MIDI,
renders installed virtual instruments, and reports objective problems without making aesthetic
decisions on the agent's behalf.

This repository is a greenfield replacement for the discarded CodexSongWriter project. It does not
contain a baseline composer, auto-orchestrator, quality score, or automatic musical repair loop.

## Current milestone

The first milestone provides:

- environment discovery through `ledgerline doctor`;
- a machine-readable setup plan contract;
- measure-local score documents;
- validation of meter, pitches, ranges, dynamics, and articulations;
- deterministic MusicXML and MIDI compilation;
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
```

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

Large sample libraries do not live in Git. Signed `.llpack` release assets and the consent-based
installer are planned as separate deliverables. Catalog entries remain non-installable until their
download URL, checksum, license, and attribution have been audited.
