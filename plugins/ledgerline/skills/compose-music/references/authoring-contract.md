# Authoring contract

All authored YAML uses `format: 1`. Unknown fields, missing instrument capabilities, invalid
routing, and ambiguous assets are hard errors. A present voice fills its measure exactly; an
omitted measure is an intentional whole-measure rest.

## Creative brief and protected material

`brief.yaml` is optional for legacy projects and required for a directed refinement workflow. It
declares `trajectory`, section anchor ranges and functions, part roles, protected ranges/aspects,
style checks (`off` or `review`), plain-language invariants, and listening checkpoints. Optional
purpose, duration, references, required instruments, and forbidden sounds carry user intent without
turning it into an aesthetic score. Protected aspects are `pitch`, `rhythm`, `dynamics`,
`articulation`, `expression`, `instrument`, and `mix`.

Run `prepare-ids --dry-run` before the first structural Studio session, then inspect and apply it.
Optional authored `id` values on note/rest events and controls must be unique within their part and
must not be regenerated during move, resize, copy, undo, or redo. Existing format 1 projects without
IDs remain readable; index-based commands are a compatibility path, not a stable agent reference.

## Score and performance

`piece.yaml` declares title, measure count, meter/tempo/key maps, and part/profile bindings.
`parts/<id>.yaml` declares exact voices and controls.

```yaml
format: 1
part: cello
controls:
  - {id: ctl-cello-expression-1, at: "1:1", type: performance, parameter: expression, value: 0.55}
  - {id: ctl-cello-legato-1, at: "1:1", type: keyswitch, name: legato, velocity: 64, duration: 1/32}
measures:
  "1":
    v1:
      - id: ev-cello-1
        p: C4
        d: 1/2
        dyn: mp
        art: tenuto
        expr:
          pitch_cents: 12
          curves:
            pitch: [{at: 0, value: 0}, {at: 1, value: -35}]
            pressure: [{at: 0, value: 0.4}, {at: 1, value: 0.75}]
          gestures:
            - {type: nonghyeon, depth_cents: 20, rate_hz: 5}
      - {r: true, d: 1/2}
```

Durations are whole-note fractions `1/1` through `1/32`, optionally dotted. Dynamics are
`ppp pp p mp mf f ff fff`; ties and slurs are `start stop continue`. Profile articulations may be
the built-ins or explicit mappings with `id`, MusicXML articulation, optional label, gate ratio, and
velocity delta. A custom articulation string without that mapping is invalid. Semantic performance
controls must be declared by the profile. MIDI 1
retains microtonal/curve data as channel pitch bend, pressure, and CC74; MusicXML retains decimal
alterations and LedgerLine annotations. Do not overlap incompatible channel-wide expression in a
MIDI 1 render. Declare `performance.yaml`: MPE produces member-channel MIDI, while CLAP/MIDI 2.0
preserve stable note IDs in the expression plan. Legacy overlap is a hard error.

For multiple staves, declare contiguous numbered clefs and set `staff` on every event. CC0/32 are
owned by bank selection; use semantic pedal events instead of raw CC64.

## Professional notation and ramps

Tuplets retain their written duration and declare the exact playback ratio and group boundary:

```yaml
- {p: C5, d: 1/8, tuplet: {actual: 3, normal: 2, type: start}, slur: start}
- {p: D5, d: 1/8, tuplet: {actual: 3, normal: 2, type: continue}}
- {p: Eb5, d: 1/8, tuplet: {actual: 3, normal: 2, type: stop}, slur: stop}
```

Grace notes use `grace: {kind: acciaccatura|appoggiatura, steal: 0..0.5}`. They consume no notated
measure duration and explicitly steal that proportion of the following measured note for MIDI.
They cannot be rests, ties, or tuplets and a grace group without a following note is invalid.

Dynamic hairpins are part controls. Their endpoints compile to MusicXML wedges and deterministic
sampled MIDI controller values:

```yaml
- {at: "3:1", type: dynamic_ramp, end: "4:1", from: mp, to: f, controller: 11}
```

Tempo entries may contain `ramp: {to: "measure:beat", bpm: 96, curve: linear}`. The shared
timeline integrates that continuous linear-BPM ramp for score, MIDI, automation, analysis, and
Studio positions. MusicXML includes visible endpoint tempi plus exact LedgerLine metadata; MIDI
contains deterministic tempo samples and metadata. Unsupported curves fail rather than degrading.

## Motifs

`motifs.yaml` places explicit cells into a target part/measure/voice and supports `transpose`,
`invert`, `retrograde`, `augment`, `diminish`, and `rhythm` transformations. Compilation writes
`build/motif-expansion.json`; review this explicit expansion rather than treating a motif name as
musical output.

## Render graph

`render.yaml` binds every part exactly once to `fluidsynth`, `sfizz`, `plugin`, or `frozen`.

```yaml
format: 1
sample_rate: 48000
block_size: 512
tail_seconds: 3
resources: {max_render_seconds: 900, max_stem_mb: 1024, max_cache_mb: 4096}
nodes:
  - id: cello-sfz
    part: cello
    engine: sfizz
    executable: C:/tools/sfizz-render.exe
    instrument: assets/cello/cello.sfz
  - id: piano-clap
    part: piano
    engine: plugin
    plugin_format: clap
    executable: C:/tools/ledgerline-plugin-host.exe
    instrument: C:/plugins/Piano.clap
    state: states/piano.state
    latency_samples: 256
    tail_seconds: 4
```

Plugin hosts receive `--ledgerline-request <json>` with plugin/state/MIDI/output, offline process
settings, sample-positioned parameter automation, and note-expression events. Each node runs in a
separate process, is hash-cached, latency/tail aligned, and quarantined on failure. LedgerLine
includes a deterministic reference host for protocol/golden tests. It does not include a commercial
instrument license or claim native compatibility with arbitrary binaries; those require an
SDK-backed external adapter.

## Automation and mix

`automation.yaml` lanes target `parts.<id>.*`, `buses.<id>.*`, `master.*`, or
`parts.<id>.plugin.<parameter>`. Points use measure:beat anchors and `step`, `linear`, `smooth`,
`exponential`, or `bezier` interpolation.

`mix.yaml` format 2 supports tracks and buses with `gain_db`, equal-power `pan`, `output`, `sends`,
and ordered `eq`, `compressor`, or `reverb` inserts. The master adds inserts, target LUFS, true-peak
ceiling, loudness range, and tolerance. Format 1 remains readable for older projects.

## Assets and review

Every `assets.yaml` entry must explicitly state source, license, redistribution permission, path,
and optional conversion parents. `bundle` includes redistributable assets and replaces restricted
ones with source/license/hash placeholders.

Use `review.yaml` for part-aware measure ranges, categories, severity, status, and human listening
notes. `review` resolves them through the same tempo map to exact seconds and samples.
