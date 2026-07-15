# LedgerLine 0.6.0 release-candidate verification

- Date: 2026-07-14
- Platform: Windows, Python 3.11.5
- Working-tree base: `5b84164bb7b8c15398fba7112ebab34113ad5ded`

This record describes the implemented, uncommitted 0.6.0 release-candidate working tree. It is not
a claim that a public tag or hosted CI run exists. Commands below were run after the final Studio
bundle, server disconnect handling, packaged README/skill contract, wheel, and plugin asset were
frozen; this verification record was then written from those results.

## Automated verification

| Surface | Result |
|---|---|
| Python unit/integration | `176 passed in 83.92s` |
| Ruff | `All checks passed!` with `--no-cache` |
| Studio Vitest | 10 files, `37 passed` in 15.02 s |
| TypeScript and production build | passed; Vite transformed 6,709 modules |
| Chromium Playwright | `1 passed (22.9s)` against the production Studio bundle |
| Packaged runtime | clean temporary venv passed validate, compile, Studio v2 model, and resource probes |
| Plugin manifest | `validate_plugin.py` passed |
| Compose skill | `quick_validate.py` passed |

Playwright exercises the real production bundle and Studio server for note editing, stale state,
undo/redo, score synchronization, mixer, engine receipts, and the delegation review UI. Its later
delegation state transitions are route-mocked deliberately; Python integration tests exercise the
real proposal preview, revision-bound apply/build, listening, accept, and revise state machine.

The in-app Browser was also asked to open the running localhost Studio, but its enterprise network
policy rejected all `127.0.0.1` access. No workaround was attempted. This is recorded as a manual
browser inspection not run, not as a LedgerLine failure; the independent Chromium E2E passed.

## Wheel and plugin identity

- Wheel: `ledgerline-0.6.0-py3-none-any.whl`
- Size: 735,880 bytes
- Wheel SHA-256: `480360c68d49e2572a49891120d5bf851326c0dfb048cb1bfc1ff212253aab4b`
- Plugin version: `0.6.0+codex.20260714194255`
- Compared ZIP entries: 84
- Content-manifest SHA-256: `0bcb981a4b2a87a31732eb5d54f529e60b17732fc898202ca039f7f25ab175ba`

`scripts/verify_plugin_asset.py` compared every uncompressed ZIP entry, not merely the two archive
files. The release wheel, repository plugin asset, and installed Codex cache copy all matched.

The clean-wheel smoke installed only the built wheel and its declared dependencies into a new
temporary venv. It validated and compiled `Ledgerline Nocturne Sketch`, produced a Studio schema v2
model with 25 notes, and found the packaged brief schema, Studio schema, and Studio UI.

## Actual external-engine verification

The no-download external smoke used the user's installed tools and did not substitute an asset.

| Item | Verified value |
|---|---|
| FluidSynth | 2.5.6; SHA-256 `23bfbfa8d2e8fe88cb2698969e06fce8ed56217de834133904884784ff69ed1e` |
| SoundFont | MuseScore `MS Basic.sf3`; SHA-256 `5ea2375e8bd7d8e71def1036978c1621e85b66934169b6a2744b27b9b3c2d99c` |
| FFmpeg | 7.0.2; SHA-256 `d269a313b8893bd72ec3ba4f6faea08cd9f26ccda8385cf40918f08e029a1692` |
| Smoke WAV | 755,756 bytes and FFmpeg-decodable |

The full nocturne compile/render/mix completed at authored revision
`5cb3a640b543b761bb369d0fa91b7eb57d133fb650dfcd86a6fcb03b092ec21d`.
The mastered result measured -16.11 integrated LUFS and -1.62 dBTP. A second render reported cache
hits for both piano and cello, demonstrating changed-node cache reuse.

sfizz was not configured for this run. Its command path remains covered by automated fixtures and
the opt-in actual-engine CI lane, but no actual sfizz render is claimed here.

## Managed runtime and Studio lifecycle

`bootstrap.ps1 -Plan` reported an explicit 0.5.0 → 0.6.0 upgrade. `-Apply` installed the bundled
wheel, ran `pip check`, and ran `doctor --json`. Doctor reported `degraded`, by design, because the
verified FluidSynth executable is local-unmanaged and LedgerLine refuses to select it implicitly;
the explicit FluidSynth path remains usable.

On an isolated project copy, the plugin lifecycle wrapper:

1. compiled, rendered, mixed, and started Studio with explicit FluidSynth, SoundFont, and FFmpeg;
2. returned the recorded `http://127.0.0.1:8891/` URL even when Status requested port 8899;
3. verified PID, process start time, runtime executable, and project identity;
4. rejected an external Rebuild before starting compile while the healthy Studio was running; and
5. stopped only the identity-verified process and then removed the isolated fixture.

`codex plugin add ledgerline@ledgerline` installed the cache-busted plugin at
`C:\Users\1wndr\.codex\plugins\cache\ledgerline\ledgerline\0.6.0+codex.20260714194255`.
The current Codex task continues to report its startup snapshot (0.5.0); a new Codex task is required
to activate and enumerate the new plugin version.

## Explicit product boundaries

- The bundled reference host is deterministic conformance infrastructure, not a commercial virtual
  instrument. Arbitrary native VST3/CLAP binaries need an SDK-backed adapter implementing the
  documented host protocol.
- `midi2` is a lossless transport plan, not a binary UMP stream.
- Instrument probes and refinement findings provide evidence; they do not score aesthetic quality.
- The Studio spectrogram is time-aligned rendered analysis, not a live spectrum analyzer.
- A public 0.6.0 release still requires an intentional commit, push, hosted CI result, and tag.
