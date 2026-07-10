# Environment setup protocol for agents

## Session start

Run `ledgerline doctor --json`. Read `status`, `capabilities`, `problems`, and asset origins. A local
tool marked `local-unmanaged` is information, not permission to use it automatically.

If `managed_render_ready` is false:

1. Run `ledgerline setup plan --packs starter --output setup-plan.json --json`.
2. Present exact bytes, licenses, destinations, and system changes to the user.
3. Wait for explicit approval.
4. Run `ledgerline setup apply --plan setup-plan.json --consent TOKEN --json` with the random token
   from that exact, approved plan. The plan expires after 30 minutes and is single-use.
5. Run `doctor` and the smoke render again.

`setup apply` verifies the pinned Ed25519 catalog signature, archive and manifest hashes, every
payload file, safe paths, limits, and SoundFont preset coverage before atomically activating a
versioned installation. It installs data under `LEDGERLINE_HOME`; it does not install FluidSynth or
change the system. An informed user may continue to supply explicit `--fluidsynth` and
`--soundfont` paths. The render report records the SoundFont hash, and rendering fails if the file
does not contain every bank/program requested by the piece profiles.

Never modify PATH or the registry, install system-wide software, accept an unverified checksum,
silently substitute an instrument, or treat a file found in Downloads as trusted.
