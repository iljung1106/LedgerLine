# Architecture

LedgerLine separates human/agent authorship from deterministic mechanics.

```text
authored score + motifs + expression + performance policy
        │
        ├─ validate / inspect / duration
        ▼
shared tempo-aware timeline ──► MusicXML + MIDI/MPE + note-expression plan
        │
        ▼
render graph: FluidSynth | sfizz | bundled reference | native host adapter | frozen stem
        │             isolated process, hash cache, latency/tail alignment
        ▼
WAV stems ──► track/bus graph + automation + mastering ──► mix.wav
        │                                              │
        ├─ time-local / golden analysis                ├─ LUFS / true peak
        └─ visual + matched A/B review                 └─ review annotations

assets/source/license/conversion ──► lockfile + manifest + .llproject bundle
```

Commands never change authored music unless the user explicitly invokes `apply-edits`, which writes
to a new directory and validates the result. `snapshot` preserves source before consequential
work. Generated files live under `build/`.

## Time and automation

One `Timeline` integrates every meter and tempo segment and converts measure:beat anchors to whole
notes, 480-TPQ MIDI ticks, seconds, and integer samples. Duration prediction, automation, plugin
requests, rendering alignment, analysis, and listening notes use this same mapping.

Automation is authored independently of notes. Lanes are strict, unit-bearing, and support step,
linear, smooth, exponential, and cubic Bezier interpolation. Mixer gain lanes become time-varying
FFmpeg expressions; plugin lanes are delivered as sample positions to the external host protocol.

`performance.yaml` chooses a transport per part. Legacy MIDI rejects independently expressive
overlaps. MPE allocates one member channel per simultaneously active note and fails when its zone is
exhausted. CLAP and MIDI 2.0 plans retain stable note IDs, tuning, pressure, and timbre. MIDI 2.0 is
currently a transport-neutral event plan; a native adapter must advertise UMP support before use.

## Instrument capability boundary

Profiles declare range, transposition, bank/program, articulations, keyswitches, and semantic
performance bindings. A parameter such as `brightness` may map to CC74 for a SoundFont or to a named
plugin parameter for a sampled instrument. Missing mappings fail rather than degrading to an
unrelated controller.

SF2/SF3 preset tables and SFZ zones are inspected before use. External plugins run out of process,
receive an immutable request, and produce hash-identified output. Failed nodes write quarantine
reports. Resource ceilings limit duration, individual stem size, and cache size.

The bundled reference host is a deterministic conformance instrument, not a compatibility claim
for arbitrary commercial binaries. Native VST3/CLAP adapters own SDK loading, ABI/lifecycle,
discovery, and crash isolation while keeping the LedgerLine request boundary stable.

Profile discovery is two-phase: a scan or SFZ audit creates an evidence-bearing draft and ranked
semantic candidates; only a reviewed SHA-256 token promotes the edited draft. Audio probes
independently measure silence, audible range, and velocity response.

## Mix and evidence boundary

Mix format 2 routes tracks and buses as a validated acyclic graph. Ordered EQ, compressor, and
reverb inserts plus post-fader sends compile to FFmpeg. A two-pass loudness stage verifies final
integrated LUFS and true peak against authored tolerance.

Analysis reports loudness, peaks, crest factor, spectral centroid, and active stems per time window.
It flags evidence such as long silence or relative spikes, but it never assigns an aesthetic score
or rewrites the source.

## Reproducibility and licenses

Build manifests hash authored inputs, profiles, and outputs. Render receipts additionally hash MIDI,
renderer, instrument, state, automation, settings, and output. `ledgerline.lock.json` records the
host environment. `assets.yaml` requires explicit source/license/redistribution metadata and a
cycle-free conversion lineage. Bundles include permitted assets and replace restricted assets with
source/license/hash placeholders.
