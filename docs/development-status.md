# Development status — 2026-07-13

## Implemented in 0.4.0

- All 0.3 score, timeline, render graph, mix, analysis, review, setup, and bundle capabilities.
- `ledgerline init` project templates with a musical-direction gate in `NOTES.md`.
- Fail-closed per-note expression plans with overlap detection and stable note IDs.
- Real lower-zone MPE channel allocation and MIDI output, CLAP note-expression schedules, and a
  transport-neutral lossless MIDI 2.0 event plan.
- Bundled deterministic reference host/manifests with scan, state, sample automation, per-note
  pitch/pressure/timbre, stereo offline render, and latency/tail metadata.
- Evidence-bearing SFZ/plugin profile drafts, ranked semantic candidates, hash-token promotion, and
  deterministic range/silence/velocity audio probes.
- Cross-platform tolerant audio fingerprints, optional exact hashes, and golden regression checks.
- Local visual review page with audio, waveform, spectrogram, optional MuseScore pages, and seekable
  listening annotations.
- Five performance templates for MPE strings, CLAP expression, sampled VST3 legato, SoundFont
  keyboard, and Korean bowed-string gestures.

## Verification target

- Windows/Python 3.11 unit and integration suite plus Ruff.
- Deterministic reference scan/render/probe and exact/tolerant golden comparison.
- Plugin wheel content, manifest, skill metadata, cache-busted install, and managed bootstrap plan.
- Existing FluidSynth/FFmpeg production example remains an external-tool integration test.

## Explicit boundaries

- The bundled reference host is a conformance renderer for LedgerLine manifests. Arbitrary native
  VST3/CLAP binaries still require an SDK-backed adapter; commercial instruments are not bundled.
- `midi2` emits a stable lossless event plan, not a binary UMP stream. A native transport must
  advertise UMP capability before use.
- Audio probes measure response evidence; they cannot infer musical quality or safely name unknown
  keyswitches. Profile promotion therefore remains explicitly reviewed.
- Objective reports and regression checks detect changes; they do not decide whether music is good.
