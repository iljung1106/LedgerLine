# Development status — 2026-07-10

## Implemented

- New Git repository and Python 3.11 package.
- Strict authored YAML parsing; unknown fields fail.
- Meter, pitch, duration, range, articulation, and tie validation.
- Deterministic MusicXML 4.0, full MIDI, and per-part MIDI compilation.
- Profile and artifact hashes in the build manifest.
- Chord/inversion, sounding-pitch, range, and density inspection without a quality score.
- FluidSynth rendering after SoundFont preset-table validation.
- Stem mix with authored gain/pan, shared reverb bus, two-pass loudness normalization, and limiter.
- FFprobe/FFmpeg measurement for 16-bit and 24-bit WAV.
- Fail-closed setup planning and managed/unmanaged asset distinction.
- Explicit multi-staff parts with numbered clefs, per-event staff placement, strict validation,
  MusicXML grand-staff output, and notation-neutral MIDI playback.
- Anchored CC automation, validated sustain-pedal state, and profile-declared semantic keyswitches
  with MIDI playback and lossless MusicXML annotations.
- Reproducible MuseScore General 0.2 Starter `.llpack` with pinned source hashes and full notices.
- Detached Ed25519 catalog signatures with a pinned public key, expiry, and downgrade checks.
- Expiring random single-use setup plans, safe streamed ZIP extraction, versioned atomic activation,
  receipts, quarantine, active-version pointers, and SoundFont smoke validation.

## Local integration evidence

- FluidSynth: 2.5.6, explicitly supplied unmanaged binary.
- SoundFont: MS Basic.sf3, 51,278,610 bytes,
  SHA-256 `5ea2375e8bd7d8e71def1036978c1621e85b66934169b6a2744b27b9b3c2d99c`.
- MuseScore discovered: 4.7.4.
- FFmpeg discovered: 7.0.2.
- Rendered preview, piano stem, and cello stem at 48 kHz.
- Final example mix: target -16 LUFS; measured -16.11 LUFS and -4.35 dBTP.

## Not implemented yet

- Publishing the Starter `.llpack` as an HTTPS release asset; the development catalog uses `dist/`.
- Signed downloadable Core `.llpack` artifacts.
- Tuplets, lyrics, and MusicXML escape hatch.
- Engraved-page, piano-roll, waveform, and spectrogram images.
- Detailed voice-leading diagnostics and transposing-instrument fixtures.
- Native SFZ renderer and Extended pack.
- Codex plugin/MCP wrapper.
