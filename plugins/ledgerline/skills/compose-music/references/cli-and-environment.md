# CLI and environment

Invoke commands through `<plugin-root>/scripts/ledgerline.ps1` and request JSON for machine reads.

```powershell
& "<plugin-root>\scripts\ledgerline.ps1" doctor --json
& "<plugin-root>\scripts\ledgerline.ps1" init <project> --title <title> --template piano-cello --json
& "<plugin-root>\scripts\ledgerline.ps1" validate <project> --json
& "<plugin-root>\scripts\ledgerline.ps1" compile <project> --json
& "<plugin-root>\scripts\ledgerline.ps1" inspect <project> --json
& "<plugin-root>\scripts\ledgerline.ps1" prepare-ids <project> --dry-run --json
& "<plugin-root>\scripts\ledgerline.ps1" prepare-ids <project> --json
& "<plugin-root>\scripts\ledgerline.ps1" refine inspect <project> --json
& "<plugin-root>\scripts\ledgerline.ps1" duration <project> --tail-seconds 3 --json
& "<plugin-root>\scripts\ledgerline.ps1" automation <project> --json
& "<plugin-root>\scripts\ledgerline.ps1" render <project> --ffmpeg <exe> --json
& "<plugin-root>\scripts\ledgerline.ps1" mix <project> --ffmpeg <exe> --json
& "<plugin-root>\scripts\ledgerline.ps1" meter <wav> --ffmpeg <exe> --json
& "<plugin-root>\scripts\ledgerline.ps1" analyze-timeline <project> --ffmpeg <exe> --json
& "<plugin-root>\scripts\ledgerline.ps1" compare <before.wav> <after.wav> --ffmpeg <exe> --json
```

Studio and delegation commands:

```powershell
& "<plugin-root>\scripts\studio.ps1" -Project <render-graph-project> -Action Start -Prepare `
  -FFmpeg <ffmpeg.exe>
& "<plugin-root>\scripts\studio.ps1" -Project <legacy-project> -Action Start -Prepare `
  -FluidSynth <fluidsynth.exe> -SoundFont <instrument.sf2-or-sf3> -FFmpeg <ffmpeg.exe>
& "<plugin-root>\scripts\studio.ps1" -Project <project> -Action Start
& "<plugin-root>\scripts\studio.ps1" -Project <project> -Action Status
& "<plugin-root>\scripts\studio.ps1" -Project <legacy-project> -Action Rebuild `
  -FluidSynth <fluidsynth.exe> -SoundFont <instrument.sf2-or-sf3> -FFmpeg <ffmpeg.exe>
& "<plugin-root>\scripts\studio.ps1" -Project <project> -Action Stop
& "<plugin-root>\scripts\ledgerline.ps1" studio <render-graph-project> --prepare `
  --ffmpeg <ffmpeg.exe> --host 127.0.0.1 --port 8765 --no-open
& "<plugin-root>\scripts\ledgerline.ps1" studio-model <project> --json
& "<plugin-root>\scripts\ledgerline.ps1" delegate create <project> "make the ending warmer" --autonomy review --json
& "<plugin-root>\scripts\ledgerline.ps1" delegate list <project> --json
& "<plugin-root>\scripts\ledgerline.ps1" delegate next <project> --json
& "<plugin-root>\scripts\ledgerline.ps1" delegate show <project> <id> --json
& "<plugin-root>\scripts\ledgerline.ps1" delegate propose <project> <id> <proposal.json> --json
& "<plugin-root>\scripts\ledgerline.ps1" delegate answer <project> <id> "the direction answer" --json
& "<plugin-root>\scripts\ledgerline.ps1" delegate apply <project> <id> --token <approval-token> --json
& "<plugin-root>\scripts\ledgerline.ps1" delegate reject <project> <id> --reason "needs a different direction" --json
& "<plugin-root>\scripts\ledgerline.ps1" delegate accept <project> <id> --note "approved after listening" --json
& "<plugin-root>\scripts\ledgerline.ps1" delegate revise <project> <id> "make the cadence breathe more" --json
```

The lifecycle wrapper intentionally distinguishes inspection from audio preparation. `Start`
without `-Prepare` may serve score and MIDI state without a renderer. If `render.yaml` is absent,
`-Prepare` and `-Action Rebuild` require explicit `-FluidSynth`, `-SoundFont`, and `-FFmpeg` files;
the wrapper does not infer them from PATH or environment discovery and does not substitute an
instrument. With `render.yaml`, instrument and renderer paths come from its authored nodes, and an
explicit `-FFmpeg` remains mandatory and is forwarded to both render and mix. Status and Stop
verify the recorded process start time and runtime executable before trusting a PID; `stale` means
no process was killed. The approved explicit paths are also passed into the local Studio server so
later Rebuild, Mix, and accepted delegation jobs reuse the same engine identities; they are kept in
process memory and are never rediscovered or substituted. A score-only Studio started without
these paths rejects later audio jobs instead of consulting PATH or environment discovery, and HTTP
job options cannot replace the paths approved when that Studio process started.
If a healthy Studio is already running, `Start -Prepare` or `Start` with new explicit paths fails
closed before rebuilding; use `-Action Stop` and start it again so the new process receives those
paths. The launcher never kills or reconfigures the existing process implicitly.

`studio-model` returns JSON-only notes, controls, parts, measures, authored mix, score, revisioned
media, engine provenance, build freshness, history state, and delegations for agents.
`delegate propose`, `delegate show`, and `delegate list` expose `proposal_preview`: exact source
impact/counts, a bounded unified YAML diff, and an event-ID note diff. Proposal actions first run
in a source-only isolated project through the same transactional edit and compile contract as
Studio. Preview failure rejects the proposal; the authored source and Studio history remain
untouched until approval, whose token binds both the actions and displayed preview. A
questions-only proposal
enters `needs-direction`; after `delegate answer`, inspect the pending task and create a new proposal
against its new `base_revision`. Safe-auto applies only within the bounded non-structural budget;
larger or structural work is changed to review mode. A successful build enters
`ready-for-listening`, whose production record contains exact authored/compiled/rendered/mix
revisions, A/B availability, and listening checks. It is not accepted until the user explicitly
uses `delegate accept`. `delegate revise` preserves the applied source, records listening feedback,
and returns the task to pending against that current revision.

The Studio HTTP API exposes `/api/status`, `/api/jobs`, `/api/jobs/<id>`, job cancellation, and
delegation answer/apply/reject/accept/revise actions. Use the CSRF token returned by `/api/model`
for writes. Job and artifact status is authoritative; a file's presence or duration does not make
it current.

Workflow and asset commands:

```powershell
ledgerline samples inspect <library.sfz|exs|adv|als|nki> --json
ledgerline samples convert <library> <output.sfz> --json
ledgerline plugin-scan <host.exe> <instrument.vst3|clap> --format <vst3|clap> --json
ledgerline reference-plugin-scan --format clap --json
ledgerline performance-templates list --json
ledgerline performance-templates apply <project> <part> <template> --json
ledgerline expression-plan <project> --json
ledgerline instrument-profile draft <scan-or-sfz> <draft.json> --id <id> --name <name> --json
ledgerline instrument-profile seal <draft.json> --json
ledgerline instrument-profile approve <draft.json> <profile.yaml> --token <token> --json
ledgerline instrument-profile probe <reference.llplugin.json> <output-dir> --json
ledgerline instrument-profile probe-plan <output-dir> --sample-rate 48000 --json
ledgerline instrument-profile analyze-probe <rendered.wav> <probe-plan.json> <report.json> --json
ledgerline regression record <wav> <baseline.json> --json
ledgerline regression check <wav> <baseline.json> --json
ledgerline visual-review <project> --audio <wav> --ffmpeg <exe> --musescore <exe> --json
ledgerline assets <project> --json
ledgerline snapshot <project> <name> --json
ledgerline apply-edits <project> <plan.yaml> --output <new-project> --json
ledgerline diff <before-project> <after-project> --json
ledgerline review <project> --json
ledgerline freeze <project> <part> --json
ledgerline lock <project> --json
ledgerline bundle <project> --output <piece.llproject> --json
```

If `render.yaml` is absent, `render` uses the legacy explicit FluidSynth path:

```powershell
ledgerline render <project> --fluidsynth <exe> --soundfont <sf2-or-sf3> --ffmpeg <exe> --json
```

## Runtime and packs

`bootstrap.ps1 -Plan` is read-only. After approval, `-Apply` creates a private Python 3.11 venv
under `%LOCALAPPDATA%\LedgerLine\runtime`, installs the bundled wheel and pinned dependency ranges,
and verifies the installed version. It never modifies PATH or the registry.

```powershell
ledgerline setup plan --packs starter --output <plan.json> --json
ledgerline setup apply --plan <plan.json> --consent <approved-token> --json
```

The signed catalog pins URL, compressed and expanded limits, hashes, license, attribution,
destination, and key. Plans expire and are single-use. FluidSynth, sfizz, FFmpeg, VST3/CLAP hosts,
plugins, and commercial samples are not implied by the Starter pack; use only explicit paths. The
deterministic reference host ships in the wheel without a download, but it is a conformance sound,
not a production orchestral library.

Repository developers can verify explicitly installed FluidSynth/FFmpeg/SoundFont paths, plus an
optional sfizz/SFZ pair, without changing the machine or downloading an asset:

```powershell
& <repository>\scripts\external-engine-smoke.ps1 `
  -FluidSynth <fluidsynth.exe> -SoundFont <library.sf2-or-sf3> `
  -FFmpeg <ffmpeg.exe> -RequireConfig
```

The same values may be supplied as `LEDGERLINE_FLUIDSYNTH`, `LEDGERLINE_SOUNDFONT`,
`LEDGERLINE_FFMPEG`, `LEDGERLINE_SFIZZ`, and `LEDGERLINE_SFZ`. Missing or partial required
configuration fails closed when `-RequireConfig` is set.
