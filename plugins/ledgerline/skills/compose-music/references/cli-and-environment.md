# CLI and environment

Invoke all commands through `<plugin-root>/scripts/ledgerline.ps1`.

```powershell
& "<plugin-root>\scripts\ledgerline.ps1" doctor --json
& "<plugin-root>\scripts\ledgerline.ps1" validate <project> --json
& "<plugin-root>\scripts\ledgerline.ps1" compile <project> --json
& "<plugin-root>\scripts\ledgerline.ps1" inspect <project> --json
& "<plugin-root>\scripts\ledgerline.ps1" render <project> --fluidsynth <exe> --soundfont <sf3> --json
& "<plugin-root>\scripts\ledgerline.ps1" mix <project> --ffmpeg <exe> --json
& "<plugin-root>\scripts\ledgerline.ps1" meter <wav> --ffmpeg <exe> --json
```

## Runtime bootstrap

`bootstrap.ps1 -Plan` is read-only. It reports the managed runtime destination, bundled wheel,
network dependency source, and system changes. Present it and wait for explicit approval.
`bootstrap.ps1 -Apply` creates a private venv under `%LOCALAPPDATA%\LedgerLine\runtime` and
installs the bundled LedgerLine wheel plus its Python dependencies. It does not modify PATH or the
registry.

## Starter pack

```powershell
& "<plugin-root>\scripts\ledgerline.ps1" setup plan --packs starter --output <plan.json> --json
& "<plugin-root>\scripts\ledgerline.ps1" setup apply --plan <plan.json> --consent <approved-token> --json
```

The plan is valid for 30 minutes and single-use. The signed catalog fixes version, URL, compressed
size/hash, expanded limits, license, attribution, destination, and catalog key. Never apply before
the user approves those exact values.

FluidSynth is not in the Starter pack. Use an existing executable only through an explicit
`--fluidsynth` path approved by the user. The same applies to unmanaged MuseScore, FFmpeg, and
third-party SoundFonts.
