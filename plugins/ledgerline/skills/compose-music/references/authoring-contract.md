# Authoring contract

## Project layout

```text
piece.yaml
parts/<part-id>.yaml
mix.yaml
NOTES.md
```

All authored YAML uses `format: 1`. Unknown fields are hard errors. A present voice must fill its
measure exactly. An omitted part measure means an intentional whole-measure rest.

## Piece

```yaml
format: 1
title: Example
measures: 8
time:
  - {measure: 1, beats: 4, beat_type: 4}
tempo:
  - {at: "1:1", bpm: 84}
key:
  - {measure: 1, fifths: 0, mode: major}
parts:
  - id: piano
    name: Piano
    profile: starter.acoustic-grand-piano
    file: parts/piano.yaml
```

## Events

Measures contain voices named `v1`, `v2`, and so on. Durations are whole-note fractions from
`1/1` through `1/32`, optionally dotted once or twice.

```yaml
format: 1
part: piano
measures:
  "1":
    v1:
      - {p: [C4, E4, G4], d: 1/2, dyn: mp, art: tenuto}
      - {p: D5, d: 1/4, tie: start}
      - {r: true, d: 1/4}
```

Supported dynamics are `ppp pp p mp mf f ff fff`. Supported note articulations are
`staccato tenuto accent marcato`. Ties are `start stop continue`. `vel` may explicitly set
1–127.

## Multiple staves

Declare contiguous staff numbers and clefs. Every event in a multi-staff part must name a staff.

```yaml
staves:
  - {number: 1, name: right, clef: {sign: G, line: 2}}
  - {number: 2, name: left, clef: {sign: F, line: 4}}
measures:
  "1":
    v1:
      - {p: [C4, E4, G4], d: 1/1, staff: 1}
    v2:
      - {p: C3, d: 1/1, staff: 2}
```

Staff placement affects notation, not MIDI pitch or timing. A voice may cross staves intentionally.

## Performance controls

Controls use measure:beat anchors on the part timeline.

```yaml
controls:
  - {at: "1:1", type: cc, controller: 11, value: 64}
  - {at: "1:1", type: pedal, action: down}
  - {at: "1:3", type: pedal, action: change}
  - {at: "2:1", type: keyswitch, name: legato, velocity: 64, duration: 1/32}
  - {at: "2:4", type: pedal, action: up}
```

CC0/32 belong to bank selection. Use semantic pedal instead of CC64. Keyswitch names must be
declared by the selected instrument profile. End pedal state explicitly.

## Instrument profiles

A custom `profiles/<profile-id>.yaml` may declare range, transposition, bank/program,
articulations, clef, and semantic keyswitch pitches. Never describe a GM patch as if it supported
sampled legato, round robins, or other unavailable techniques.
