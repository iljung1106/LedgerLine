# Architecture

LedgerLine separates authorship from mechanics.

```text
agent-authored YAML
  -> validate (read only)
  -> inspect (read only)
  -> compile (build/MusicXML + MIDI)
  -> render (build/WAV stems)
  -> mix (build/premaster + master)
  -> meter (read only)
```

This diagram describes explicit commands, not an automatic pipeline. No command edits `piece.yaml`,
`parts/*.yaml`, `mix.yaml`, or `NOTES.md`. The agent chooses when to invoke each command and makes
every musical change itself.

## Authored contracts

- `piece.yaml`: global meter, tempo, key, part/profile bindings.
- `parts/*.yaml`: measure-local voices and exact events.
- `mix.yaml`: gain, equal-power pan, shared reverb sends, and master targets.
- `NOTES.md`: user direction, form plan, critique, and human listening decisions.

Unknown fields fail. A missing part measure is an intentional whole-measure rest. Any present voice
must exactly fill its measure. Absolute instrument ranges block validation; comfortable ranges are
reported as warnings.

## Trust boundary

`doctor` distinguishes discovered local assets from LedgerLine-managed assets and records SHA-256.
Unmanaged tools and SoundFonts are never automatically selected. They may be used only through
explicit command arguments. Managed assets will live below `%LOCALAPPDATA%\LedgerLine` and must
match a signed pack manifest.

Before synthesis, the SoundFont `phdr` table is parsed and every requested bank/program is checked.
There is no nearest-program fallback.

