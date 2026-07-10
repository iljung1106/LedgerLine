# Audio pack policy

Large samples are release artifacts, not Git content. A source is not installable merely because it
is free or redistributable. It must have an audited version, license, provenance, exact byte size,
LedgerLine SHA-256, safe archive layout, normalized mapping, smoke MIDI, and golden audio review.

## Planned tiers

- Starter: MuseScore_General 0.2.0, about 38 MiB, MIT. Coverage and smoke-test fallback.
- Core Orchestra: selected VSCO 2 CE plus selected VCSL gaps, rebuilt as deterministic SF3 for
  FluidSynth v1.0.
- Core Keys: a LedgerLine mapping derived from Salamander Grand Piano with CC BY attribution and
  modification notice.
- Core Rhythm/Band: simplified Virtuosity Drums mapping, Growlybass 1.002, and Emilyguitar 1.001.
- Extended v1.1: native SFZ after an owned, audited offline renderer exists.

VCSL is a selection source rather than a whole default pack because its rolling release and patch
quality vary. GeneralUser GS is not bundled because its license text notes incomplete sample-origin
certainty and discourages direct hotlinks. Muse Sounds is never redistributed.

See `packs/candidates.json` for the audited research queue. Entries there are deliberately not used
by `setup plan` as trusted artifacts.

