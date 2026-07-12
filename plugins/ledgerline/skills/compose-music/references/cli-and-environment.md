# CLI and environment

Invoke commands through `<plugin-root>/scripts/ledgerline.ps1` and request JSON for machine reads.

```powershell
& "<plugin-root>\scripts\ledgerline.ps1" doctor --json
& "<plugin-root>\scripts\ledgerline.ps1" init <project> --title <title> --template piano-cello --json
& "<plugin-root>\scripts\ledgerline.ps1" validate <project> --json
& "<plugin-root>\scripts\ledgerline.ps1" compile <project> --json
& "<plugin-root>\scripts\ledgerline.ps1" inspect <project> --json
& "<plugin-root>\scripts\ledgerline.ps1" duration <project> --tail-seconds 3 --json
& "<plugin-root>\scripts\ledgerline.ps1" automation <project> --json
& "<plugin-root>\scripts\ledgerline.ps1" render <project> --ffmpeg <exe> --json
& "<plugin-root>\scripts\ledgerline.ps1" mix <project> --ffmpeg <exe> --json
& "<plugin-root>\scripts\ledgerline.ps1" meter <wav> --ffmpeg <exe> --json
& "<plugin-root>\scripts\ledgerline.ps1" analyze-timeline <project> --ffmpeg <exe> --json
& "<plugin-root>\scripts\ledgerline.ps1" compare <before.wav> <after.wav> --ffmpeg <exe> --json
```

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
