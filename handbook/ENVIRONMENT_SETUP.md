# Environment setup protocol for agents

## Session start

Run `ledgerline doctor --json`. Read `status`, `capabilities`, `problems`, and asset origins. A local
tool marked `local-unmanaged` is information, not permission to use it automatically.

If `managed_render_ready` is false:

1. Run `ledgerline setup plan --packs starter,core --json`.
2. Present exact bytes, licenses, destinations, and system changes to the user.
3. Wait for explicit approval.
4. When `setup apply` becomes available, pass only the consent token produced by that exact plan.
5. Run `doctor` and the smoke render again.

Until signed packs ship, an informed user may supply explicit `--fluidsynth` and `--soundfont`
paths. The render report records the SoundFont hash, and rendering fails if the file does not contain
every bank/program requested by the piece profiles.

Never modify PATH or the registry, install system-wide software, accept an unverified checksum,
silently substitute an instrument, or treat a file found in Downloads as trusted.

