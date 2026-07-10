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

## Local integration evidence

- FluidSynth: 2.5.6, explicitly supplied unmanaged binary.
- SoundFont: MS Basic.sf3, 51,278,610 bytes,
  SHA-256 `5ea2375e8bd7d8e71def1036978c1621e85b66934169b6a2744b27b9b3c2d99c`.
- MuseScore discovered: 4.7.4.
- FFmpeg discovered: 7.0.2.
- Rendered preview, piano stem, and cello stem at 48 kHz.
- Final example mix: target -16 LUFS; measured -16.11 LUFS and -4.35 dBTP.

## Not implemented yet

- Signed downloadable Starter/Core `.llpack` artifacts and `setup apply`.
- CC curves, pedal, keyswitch profiles, tuplets, multiple staves, lyrics, and MusicXML escape hatch.
- Engraved-page, piano-roll, waveform, and spectrogram images.
- Detailed voice-leading diagnostics and transposing-instrument fixtures.
- Native SFZ renderer and Extended pack.
- Codex plugin/MCP wrapper.

