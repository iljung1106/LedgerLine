# Development status — 2026-07-12

## Implemented in 0.3.0

- Shared tempo-aware tick/second/sample timeline and tail-aware duration prediction.
- Strict sample-accurate automation lanes with five interpolation modes.
- Unified FluidSynth, sfizz-render, external VST3/CLAP-host, and frozen-stem render graph.
- Per-node subprocess isolation, request/quarantine records, content cache, latency/tail alignment,
  resource budgets, and reproducible renderer/instrument/state identities.
- Semantic attack/brightness/distance/expression-style profile bindings; unsupported controls fail.
- Microtonal note offsets, pitch/pressure/timbre curves, and nonghyeon/chuseong/toeseong/breath/
  pluck-position gestures in MusicXML plus MIDI 1 fallback messages.
- Multi-level bus routing, EQ/compressor/reverb inserts, sends, smooth gain automation, two-pass LUFS
  mastering, and true-peak verification.
- Time-local loudness/brightness/transient/active-part analysis and loudness-matched A/B reports.
- SFZ inheritance, zone, velocity, loop, round-robin, and missing-sample audit; conservative EXS24
  and Ableton sample-path recovery; provenance-only Kontakt handling.
- Declarative motif expansion with transpose, invert, retrograde, augmentation, diminution, and
  rhythm replacement, plus explicit expansion reports.
- UTF-8 Korean and multilingual title/part metadata across MusicXML, MIDI, and manifests.
- Snapshots, scoped edits into a new project, project diffs, listening annotations, and frozen stems.
- Asset hashes, source/license/conversion lineage, environment lockfiles, deterministic license-aware
  `.llproject` bundles, signed pack setup, and a refreshed Codex plugin skill.

## Local integration evidence

- 63 Python tests passing on Windows with Ruff clean.
- FluidSynth 2.5.6 rendered MuseScore General 0.2 preview plus piano/cello stems at 48 kHz.
- FFmpeg 7.0.2 executed the format-2 track/bus graph with EQ, compression, reverb, two smooth gain
  lanes, and mastering.
- Production example result: 16.065667 seconds, 24-bit/48 kHz stereo, -16.14 LUFS and -1.60 dBTP.
- Timeline analysis found the authored/reverb tail from 14.0 to 16.065667 seconds; A/B comparison
  produced window-level level, centroid, correlation, and difference metrics.

## Explicit limitations

- LedgerLine defines an external VST3/CLAP host protocol but does not bundle a native plugin host,
  third-party SDK, commercial instrument, or plugin license.
- MIDI 1 fallback expression is channel-wide. Overlapping independently expressive notes require a
  capable external host or future MIDI 2/MPE backend to remain truly per-note.
- EXS24 and Ableton conversion preserves recoverable sample paths and simple zones; vendor-specific
  modulation must be verified in the source application. Kontakt containers are not decrypted.
- Objective reports identify measurable changes and risks; they do not decide whether music is good.
