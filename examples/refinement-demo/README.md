# Refinement demo

This fixture shows one authored eight-measure piano-and-cello phrase at three reversible
checkpoints. It is small enough to review as text, but it exercises stable event IDs, a creative
brief, protected musical ranges, notation, expression, mix automation, and a deterministic render
route.

The fixture is **not** evidence that a successful build is aesthetically good. The accompanying
notes and `review.yaml` files record why each change was made and what a listener still needs to
judge.

## Checkpoints

| Project | Deliberate scope | Listening question |
| --- | --- | --- |
| `sketch` | Motive, harmonic landmarks, and large phrase | Is the opening cello cell memorable enough to protect? |
| `refined` | Bass motion, register, counterline, articulation, and phrase contour | Does the middle gain direction without obscuring the protected cell? |
| `production` | Performance controls, tempo breath, automation, routing, and reference render | Do attacks, sustain, balance, and cadence timing support the phrase? |

The cello pitches, rhythms, and IDs in measures 1–2 are identical in all three states and are
declared protected in each `brief.yaml`. Other retained events also keep their IDs. See
`refinement-rationale.json` for explicit change scopes and listening checks.

## Validate and inspect

From the LedgerLine repository root:

```powershell
foreach ($state in 'sketch', 'refined', 'production') {
  python -m ledgerline validate "examples/refinement-demo/$state" --json
  python -m ledgerline compile "examples/refinement-demo/$state" --json
  python -m ledgerline refine inspect "examples/refinement-demo/$state" --json
}
```

All three checkpoints compile without downloaded samples or other external assets. The production
project also contains a copy of LedgerLine's MIT-licensed deterministic reference-instrument
manifest. Its `render.yaml` therefore needs no third-party instrument, but the full render graph
still needs an explicitly supplied FFmpeg executable for alignment and preview assembly:

```powershell
python -m ledgerline render examples/refinement-demo/production --ffmpeg C:\path\to\ffmpeg.exe --json
```

That sine-based reference sound is for routing, timing, cache, and regression checks. It is not a
substitute for a user-approved production piano or cello library.
