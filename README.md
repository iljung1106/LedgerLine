# LedgerLine

LedgerLine is an agent-native composition and production workbench. The agent writes explicit,
reviewable YAML for notes, form, performance, instrument routing, automation, and mixing. LedgerLine
validates those decisions, compiles MusicXML/MIDI, renders local virtual instruments, and produces
objective reports without silently composing or repairing music.

## What the 0.6 release candidate provides

- strict meter, range, staff, tie, articulation, CC, pedal, keyswitch, and semantic capability checks;
- LedgerLine Studio: shared transport, editable piano roll, velocity/CC/pedal/tempo lanes, score
  cursor, revision-bound waveform/spectrogram, mixer, engine provenance, disk-backed undo/redo, build
  jobs, and plain-language AI delegation;
- optional stable score/control IDs, a dry-run `prepare-ids` migration with backup, and atomic
  source transactions for reliable agent and Studio edits;
- `brief.yaml` for trajectory, form, orchestral roles, protected material, invariants, and listening
  checkpoints, plus evidence-only structure/harmony/orchestration/expression refinement reports;
- hash-bound `build/state.json`, per-stage freshness, SHA-keyed media sidecars, changed-node render
  caching, and actual renderer/instrument/preset-state receipts;
- UTF-8 MusicXML 4.0 and MIDI, per-part MIDI, microtones, expression curves, and Korean gestures;
- tuplets, grace-note steal time, slurs, dynamic hairpins, linear tempo ramps, and profile-defined
  articulation notation/gate/velocity mappings with an explicit cross-format representation report;
- declarative motif transpose/invert/retrograde/augment/diminish/rhythm expansion;
- one timeline for measure:beat, ticks, seconds, and samples, including tempo maps and tails;
- FluidSynth, sfizz-render, an isolated external VST3/CLAP host-adapter protocol, a bundled
  deterministic reference host, and frozen-stem render nodes;
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
- a Codex plugin whose skill asks for musical direction before authoring and can process Studio
  delegation requests.

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
  brief.yaml                 # optional direction, roles, protected ranges, invariants
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
.\.venv\Scripts\ledgerline.exe prepare-ids examples\nocturne --dry-run --json
.\.venv\Scripts\ledgerline.exe refine inspect examples\nocturne --json
.\.venv\Scripts\ledgerline.exe duration examples\nocturne --tail-seconds 3 --json
```

Open the interactive Studio workbench:

```powershell
.\.venv\Scripts\ledgerline.exe studio examples\nocturne --prepare `
  --fluidsynth "C:\path\to\fluidsynth.exe" `
  --soundfont "C:\path\to\library.sf3" `
  --ffmpeg "C:\path\to\ffmpeg.exe" --host 127.0.0.1 --port 8765
```

Studio delegates work through files under `.ledgerline/delegations`. A human can create the request
in the UI, or an agent can use the same contract from CLI:

```powershell
ledgerline delegate create examples\nocturne "make measures 5-8 warmer but keep the cello line" --autonomy review --json
ledgerline delegate next examples\nocturne --json
ledgerline studio-model examples\nocturne --json
ledgerline delegate propose examples\nocturne <id> proposal.json --json

# A questions-only proposal pauses here; the user answers, then the agent prepares a fresh proposal.
ledgerline delegate answer examples\nocturne <id> "keep it restrained and preserve the cello" --json

# For a review proposal, read the bound preview/token, approve the exact proposal, then rebuild.
ledgerline delegate show examples\nocturne <id> --json
ledgerline delegate apply examples\nocturne <id> --token <approval-token> --json
ledgerline delegate show examples\nocturne <id> --json

# These are human listening decisions, only after the task reports ready-for-listening.
ledgerline delegate accept examples\nocturne <id> --note "approved after listening" --json
ledgerline delegate revise examples\nocturne <id> "keep the notes, soften the cadence" --json
```

`safe-auto` delegations apply immediately only when the proposal uses the validated safe edit set.
Structural, wide-range, protected, or explicitly review-required proposals wait for approval in
Studio. A questions-only proposal enters `needs-direction`; the user's answer returns it to the
agent with a fresh base revision. Applying and building a proposal ends at `ready-for-listening`,
with exact production revisions, A/B availability, and listening checks recorded on the task. Only
explicit user acceptance moves it to `accepted`; a listening revision records feedback and returns
the same, already-applied source to `pending` for another proposal.

The agent owns inspection, proposal authoring, and the revision-bound apply/build loop. The user
owns the approval token decision and the final listening acceptance. `accept` records that human
decision; it never applies a proposal and never treats a successful build as musical approval.

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

Verify an installed external engine without downloading or substituting any asset:

```powershell
.\scripts\external-engine-smoke.ps1 `
  -FluidSynth "C:\path\to\fluidsynth.exe" `
  -SoundFont "C:\path\to\library.sf3" `
  -FFmpeg "C:\path\to\ffmpeg.exe" `
  -RequireConfig
```

`-Sfizz` and `-Sfz` add the optional sfizz lane. The smoke report records executable results,
asset hashes, rendered WAV hashes, and decode status. CI can run the same command on an opt-in
`ledgerline-audio` Windows runner; it never fetches a library or guesses a replacement.

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

For an agent-managed Studio session, use the plugin lifecycle wrapper instead of starting duplicate
foreground servers:

```powershell
plugins\ledgerline\scripts\studio.ps1 -Project <project> -Action Start -Prepare -FFmpeg <ffmpeg.exe>
plugins\ledgerline\scripts\studio.ps1 -Project <project> -Action Status
plugins\ledgerline\scripts\studio.ps1 -Project <project> -Action Rebuild -FFmpeg <ffmpeg.exe>
plugins\ledgerline\scripts\studio.ps1 -Project <project> -Action Stop
```

An authored `render.yaml` supplies its explicit engines and instruments. A legacy project without
one must also pass `-FluidSynth <fluidsynth.exe> -SoundFont <library.sf2-or-sf3>` to `Start
-Prepare` and `Rebuild`. The wrapper records and verifies the PID, process start time, project,
runtime, URL, and source revision before status, rebuild, or stop operations.

## Upgrading a 0.5 project

- Existing format 1 projects remain readable. Run `prepare-ids <project> --dry-run --json` before
  structural Studio editing, review its backup plan, and then apply it explicitly.
- Regenerate `build/`; 0.6 does not infer freshness from an old render receipt or a same-length WAV.
- Reinstall the 0.6 plugin/runtime together so the Studio bundle, schemas, and Python wheel match.
- Existing delegation files remain readable. New preview, impact, build, and listening fields are
  added as the task advances.
- Install from `main` while developing. A public stable release should pin the marketplace to its
  matching tag instead of silently following future changes.

## Signed Starter pack

Large instrument libraries do not live in Git. `setup plan` verifies the signed catalog and shows
the exact URL, bytes, hashes, license, attribution, and destination. `setup apply` accepts only that
plan's unexpired single-use token. The published Starter pack contains MuseScore General 0.2; users
may author other licensed SoundFont/SFZ/plugin assets explicitly.
