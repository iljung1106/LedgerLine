# Native VST3 and CLAP adapter boundary

LedgerLine 0.4 ships a complete out-of-process request protocol and a deterministic reference host.
The reference host is sufficient for composition-pipeline tests, expression tests, profile probes,
and audio golden files. It does not load an arbitrary commercial binary.

Production plugin compatibility belongs in a small native adapter process:

1. Build against the official [VST 3 SDK](https://github.com/steinbergmedia/vst3sdk) or the official
   [CLAP headers](https://github.com/free-audio/clap), following each license and plugin vendor's
   terms.
2. Accept `--ledgerline-scan-request <json>` and return the strict identity/parameter/state/latency/
   tail/audio-port/note-port response accepted by `ledgerline plugin-scan`.
3. Accept `--ledgerline-request <json>`, instantiate exactly the authored plugin, restore the exact
   state, deliver MIDI and sample-positioned automation, and write the requested WAV.
4. For CLAP note expression, preserve every `note_id`; for MIDI 2.0, reject the request unless the
   adapter actually supports UMP. Never collapse either silently to channel-wide MIDI.
5. Report plugin latency and tail exactly. Do not normalize, substitute a preset, change sample
   rate, or add an undisclosed effect.
6. Keep one adapter process per render node. A crash or malformed response must remain quarantined
   at the LedgerLine boundary.

The VST3 SDK includes validator and hosting examples; CLAP publishes a stable C ABI and a simple
host example. Those upstream projects are the source of truth for ABI and lifecycle behavior.
LedgerLine deliberately avoids vendoring changing SDK code or distributing a vendor's plugin.

## Automatic agent setup

The composition skill must run `bootstrap.ps1 -Plan`, `doctor --json`, and the relevant plugin scan
before choosing instruments. Download/build/install actions require explicit user approval. The
agent records the adapter executable, plugin binary, state, scan report, hashes, version, license,
latency, tail, and note-expression dialect in project assets and the lockfile. A missing capability
stops the render; it never triggers a fallback sound.
