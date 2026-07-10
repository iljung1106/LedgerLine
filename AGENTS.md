# LedgerLine agent instructions

LedgerLine is a music workbench for an agent, not an automatic composer.

## Product boundary

- The agent authors every pitch, duration, chord, orchestration choice, dynamic, articulation,
  controller curve, and mix choice.
- Tools may validate, analyze, compile, render, measure, or visualize. They must not silently
  generate, replace, omit, or repair music.
- Missing instruments and articulations fail closed unless the user provides an explicit
  substitution or omission policy.
- Authored project files are the source of truth. `build/` is disposable.

## Engineering rules

- Support Windows and Python 3.11 first.
- External programs run without a shell, with a timeout and output checks.
- Network or large downloads require a setup plan and explicit user consent.
- Install portable dependencies below the user data directory. Do not change PATH, the registry,
  or system-wide installations.
- Every downloadable asset needs a version, size, source URL, SHA-256, license, and attribution.
- Keep stdout machine-readable when `--json` is selected.
- Update schemas, examples, documentation, and tests when an authored format changes.

## Verification

```powershell
python -m pytest -q
python -m ruff check src tests
python -m ledgerline doctor --json
python -m ledgerline validate examples/nocturne
python -m ledgerline compile examples/nocturne
```

