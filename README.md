# LedgerLine

LedgerLine is an agent-native composition and production workbench. The agent writes explicit,
reviewable YAML for notes, form, performance, instrument routing, automation, and mixing. LedgerLine
validates those decisions, compiles MusicXML/MIDI, renders local virtual instruments, and produces
objective reports without silently composing or repairing music.

## What 0.4 provides

- strict meter, range, staff, tie, articulation, CC, pedal, keyswitch, and semantic capability checks;
- UTF-8 MusicXML 4.0 and MIDI, per-part MIDI, microtones, expression curves, and Korean gestures;
- declarative motif transpose/invert/retrograde/augment/diminish/rhythm expansion;
- one timeline for measure:beat, ticks, seconds, and samples, including tempo maps and tails;
- FluidSynth, sfizz-render, isolated external VST3/CLAP adapters, bundled deterministic reference
  host, and frozen-stem render nodes;
- fail-closed expression planning with stable note IDs, overlap diagnostics, real MPE channel
  allocation, CLAP note-expression events, and a lossless MIDI 2.0 transport plan;
- evidence-based instrument profile drafts and approval, range/velocity/silence audio probes,
  performance templates, audio golden tests, and waveform/spectrogram/score review pages;
- `ledgerline init` project generation with an unresolved musical-direction gate;
- hash caching, latency/tail alignment, quarantine, duration/stem/cache resource budgets;
- track and multi-level bus routing with EQ, compression, reverb, sends, gain/pan, and automation;
- LUFS/true-peak mastering, time-local level/brightness/transient/activity analysis, and A/B reports;
- SFZ zone/loop/round-robin/missing-sample audit plus conservative EXS24/Ableton path recovery;
- snapshots, scoped non-destructive edit plans, review annotations, asset lineage, lockfiles, and
  license-aware `.llproject` bundles;
- signed `.llpack` catalogs and consent-gated setup;
- a Codex plugin whose skill asks for musical direction before authoring.

LedgerLine intentionally does not contain a baseline composer, automatic orchestrator, aesthetic
quality score, or silent instrument fallback. The agent remains responsible for the music.

## Project documents

```text
my-piece/
  piece.yaml                 # meter, tempo, key, parts
  parts/*.yaml               # exact notes, staves, controls, expression
  motifs.yaml                # optional declarative expansions
  automation.yaml            # optional sample-timed lanes
  performance.yaml           # optional per-part expression transport
  render.yaml                # optional multi-engine render graph
  mix.yaml                   # tracks, buses, inserts, sends, master
  assets.yaml                # source/license/conversion lineage
  review.yaml                # listening annotations
  NOTES.md                   # user brief and decisions
  build/                     # generated artifacts only
```

See [architecture](docs/architecture.md), the [agent guide](handbook/AGENT_GUIDE.md), and the
plugin's [authoring contract](plugins/ledgerline/skills/compose-music/references/authoring-contract.md).
Native plugin implementers should also read the [host adapter boundary](docs/native-host-adapters.md).

## Quick start

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\ledgerline.exe doctor --json
.\.venv\Scripts\ledgerline.exe init my-piece --title "My Piece" --template piano-cello --json
.\.venv\Scripts\ledgerline.exe validate examples\nocturne --json
.\.venv\Scripts\ledgerline.exe compile examples\nocturne --json
.\.venv\Scripts\ledgerline.exe duration examples\nocturne --tail-seconds 3 --json
```

With an authored `render.yaml`, `render` dispatches every part to its exact engine. Without one,
the legacy explicit FluidSynth route remains available:

```powershell
ledgerline render examples\nocturne `
  --fluidsynth "C:\path\to\fluidsynth.exe" `
  --soundfont "C:\path\to\library.sf3" `
  --ffmpeg "C:\path\to\ffmpeg.exe" --json
ledgerline mix examples\nocturne --ffmpeg "C:\path\to\ffmpeg.exe" --json
ledgerline analyze-timeline examples\nocturne --ffmpeg "C:\path\to\ffmpeg.exe" --json
```

`render.yaml` plugin nodes normally invoke an external VST3/CLAP host with
`--ledgerline-request <request.json>`. The protocol carries offline settings, state, MIDI, output,
sample-positioned automation, and per-note expression. LedgerLine isolates and audits the process.

If a plugin node omits `executable`, its instrument must be LedgerLine's `.llplugin.json` reference
manifest. This bundled path deterministically exercises scan, state, automation, note expression,
latency/tail, and WAV output. It is not a native commercial plugin: real VST3/CLAP binaries still
require an SDK-backed adapter implementing the same protocol.

Use `plugin-scan <host> <plugin> --format vst3|clap` before authoring mappings. The host must return
strict identity, parameter, state, latency, tail, audio-port, and note-port metadata; unknown or
ambiguous fields fail.

```powershell
ledgerline reference-plugin-scan --format clap --json
ledgerline performance-templates apply my-piece cello mpe-expressive-string --json
ledgerline expression-plan my-piece --json
ledgerline instrument-profile draft scan.json draft.json --id local.cello --name Cello --json
ledgerline instrument-profile seal draft.json --json
ledgerline regression record build/preview.wav tests/golden/preview.json --json
ledgerline visual-review my-piece --audio build/preview.wav --ffmpeg C:/tools/ffmpeg.exe --json
```

## Codex plugin

```powershell
codex plugin marketplace add iljung1106/LedgerLine --ref main
codex plugin add ledgerline@ledgerline
```

Start a new Codex task and invoke `$compose-music`. Runtime and pack setup are plan-first and require
explicit consent; the plugin never changes PATH or the registry.

## Signed Starter pack

Large instrument libraries do not live in Git. `setup plan` verifies the signed catalog and shows
the exact URL, bytes, hashes, license, attribution, and destination. `setup apply` accepts only that
plan's unexpired single-use token. The published Starter pack contains MuseScore General 0.2; users
may author other licensed SoundFont/SFZ/plugin assets explicitly.
