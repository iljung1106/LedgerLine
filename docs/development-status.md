# Development status — 2026-07-14

## Implemented in the 0.6.0 release-candidate working tree

- All 0.5 score, timeline, render graph, mix, analysis, review, setup, bundle, expression, and
  interactive Studio capabilities.
- Stable optional event/control IDs with dry-run migration, source backup, and compatibility with
  existing format 1 projects.
- A validated creative brief with section functions, time-bounded roles, protected aspects,
  invariants, and human listening checkpoints.
- Evidence-only structure, harmony, orchestration, and expression refinement reports linked to
  parts, measures, sections, and events.
- One hash-bound build state for compile, per-part render, mix, media freshness, engine identity,
  instrument identity, and plugin preset/state identity.
- A persistent local build queue with progress, coalescing, cancellation, persistence, restart
  detection that marks interrupted jobs failed, and a Studio API.
- Expanded transactional Studio commands for note/control/automation/tempo CRUD, range edits,
  stable selection, atomic writes, and disk-backed history.
- Studio editor controls for note creation/move/resize/copy/paste/delete, performance lanes, real
  preview meters, build freshness/jobs, engine provenance, and needs-direction delegation replies.
- Source-backed track/bus/master editing for routing, sends, EQ, compression, reverb, master
  objectives, revision-bound source impact, and loudness-matched current/previous A/B review.
- Bounded safe-auto delegation, stale-base rejection, questions/answers, and explicit refinement
  proposal metadata.
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
- A three-state sketch/refined/production fixture with protected material and pass-by-pass listening
  rationale, plus a no-download external FluidSynth/FFmpeg and optional sfizz smoke harness.
- Production-bundle Playwright coverage for edit/stale/undo/redo/score/mixer/engine UI flows and
  mocked delegation UI transitions; Python integration tests cover the real proposal, apply, build,
  listening, accept, and revise state machine.

## Release verification

- Windows/Python 3.11: 176 Python tests passed and Ruff passed.
- Studio: 37 Vitest tests, TypeScript/production build, and one Chromium production-bundle E2E
  passed.
- Clean-wheel runtime, 84-entry release/plugin/installed-cache identity, manifest, skill metadata,
  cache-busted install, and managed 0.5 → 0.6 bootstrap passed.
- Actual FluidSynth 2.5.6 + MuseScore `MS Basic.sf3` + FFmpeg 7.0.2 rendering passed; the nocturne
  master measured -16.11 LUFS / -1.62 dBTP and its second render reused both part caches.
- Hosted Windows CI is configured to run Python, Ruff, Studio unit/build and Chromium E2E tests.
  An opt-in
  `ledgerline-audio` self-hosted lane renders configured local engines with exact asset hashes.

Exact commands, hashes, limitations, and the localhost browser-policy exception are recorded in
[`release-verification-0.6.0.md`](release-verification-0.6.0.md).

## Explicit boundaries

- The bundled reference host is a conformance renderer for LedgerLine manifests. Arbitrary native
  VST3/CLAP binaries still require an SDK-backed adapter; commercial instruments are not bundled.
- `midi2` emits a stable lossless event plan, not a binary UMP stream. A native transport must
  advertise UMP capability before use.
- Audio probes measure response evidence; they cannot infer musical quality or safely name unknown
  keyswitches. Profile promotion therefore remains explicitly reviewed.
- Objective reports and regression checks detect changes; they do not decide whether music is good.
