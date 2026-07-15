# Architecture

LedgerLine separates human/agent authorship from deterministic mechanics.

```text
creative brief + protected ranges + authored score + motifs + expression + performance policy
        │
        ├─ validate / inspect / refine evidence / duration
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

authored revision ──► build/state.json ──► compile/render/mix freshness + engine receipts
        │                                      │
        └─ atomic edit + disk history          └─ SHA-keyed Studio media + local jobs
```

Commands never change authored music unless the user explicitly invokes `apply-edits`, which writes
to a new directory, `prepare-ids`, or a validated Studio/delegation transaction. `snapshot` and
Studio's disk history preserve source before consequential work. Generated files live under
`build/`.

## Direction and refinement boundary

`brief.yaml` stores user direction separately from notation. Sections and part roles give analysis
context; protected ranges/aspects are machine-checkable invariants. Refinement reports identify
evidence and possible questions for structure, harmony, orchestration, and expression, but neither
score the music nor edit source. An agent proposes a named pass against an exact source revision,
then applies one validated command transaction after review.

Stable optional event/control IDs make those proposals durable. A compatibility project can still
compile without IDs; `prepare-ids` adds deterministic IDs with a dry-run report and backup before
structural editing.

## Studio and build truth

`build/state.json` projects the current authored revision, compiler manifest, render cache keys,
actual output hashes, mix input hash, and renderer/instrument/preset-state receipts into one API
contract. Score, audio, waveform, and spectrogram URLs include content hashes. A local FIFO job
coordinator keeps compile/render/mix work off HTTP request threads, reports progress, coalesces stale
queued work, and supports cancellation. File presence alone never implies freshness.

Delegation proposals run the same Studio transaction and validation path in a temporary project.
Authored YAML is always copied by value and is never hard-linked. If an existing `render.yaml`
uses project-relative executable, instrument, or preset-state paths, preview exposes only those
explicit dependencies (plus paths explicitly proposed by `update_instrument`) as read-only inputs
through same-volume file hard links. Directory instruments such as VST3 bundles are traversed
without following symlinks and are bounded to 20,000 files and 32 levels. A missing path, escape
from the temporary sandbox, unsupported entry, exceeded bound, or unavailable hard link fails the
proposal closed; LedgerLine does not download, substitute, or copy a large sample library. Absolute
external paths remain absolute and are only validated in place. Preview commands never write an
executable, instrument, state file, or anything under a linked dependency directory.

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
