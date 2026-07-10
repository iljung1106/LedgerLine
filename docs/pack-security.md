# Pack and catalog security contract

This document defines the fail-closed contract for `ledgerline setup`. Terms such as MUST and MUST
NOT are normative. A pack is data handled by an untrusted archive parser even when its catalog
entry is signed.

## Trust roots and signed catalog

LedgerLine distributions pin one or more Ed25519 public keys by key ID. The bundled keyring is the
only initial trust root; a downloaded catalog or key is never trusted on first use.

The catalog is UTF-8 JSON accompanied by a detached `<catalog>.sig` document. The signature covers
the exact catalog bytes, so verification does not depend on a home-grown JSON canonicalizer:

```json
{"schema_version":"1","catalog_version":1,"generated_at":"...","expires_at":"...","packs":[]}
```

```json
{"schema_version":"1","algorithm":"ed25519","key_id":"...","signed_sha256":"...","signature":"BASE64"}
```

- A signature covers every exact byte of the catalog file. Reformatting it invalidates the
  signature. Release tooling signs only after the final bytes are written.
- The verifier MUST reject unknown schema versions, duplicate JSON keys, non-finite numbers,
  invalid UTF-8, unknown key IDs, malformed signatures, and catalogs with no valid signature.
- `expires` is mandatory and checked against UTC. An expired catalog is unusable, including when it
  is cached. Clock failure produces an actionable error rather than bypassing expiry.
- `catalog_version` is a monotonically increasing integer. The highest successfully verified
  version is stored in managed state; lower versions are rejected unless the user explicitly runs
  a documented offline recovery command.
- Key rotation requires a new key declaration signed by a currently trusted key. Key revocation and
  threshold changes are shipped in an application update or in a catalog valid under the previous
  threshold. A catalog cannot declare its own signing key and immediately trust it.
- Each artifact record MUST include pack ID, pack version, format version, exact compressed byte
  size, exact SHA-256, HTTPS URL, license identifier, attribution location, unpacked byte limit,
  entry-count limit, and installed manifest SHA-256.
- Redirects are limited, revalidated as HTTPS, and never allowed to change the expected digest or
  size. Authentication headers are not forwarded to another origin.

The release pipeline signs catalogs with an offline key. Runtime code contains verification public
keys only. SHA-256 alone proves integrity against a signed catalog; it does not replace the catalog
signature.

## `.llpack` format

Version 1 `.llpack` is a ZIP archive with only regular files and directories. Its root contains
`manifest.json`; current packs use `licenses/`, `notices/`, and `payload/` below it. The manifest
records every regular file's normalized POSIX path, byte size, and SHA-256. No file may exist in the
archive without a manifest entry, apart from `manifest.json` itself, and no manifest entry may be
absent from the archive.

Extraction MUST reject the complete archive before writing payload files if any of these conditions
is found:

- absolute, drive-qualified, UNC, device, or empty paths;
- `.` or `..` segments, backslashes, NULs, control characters, colons, or alternate data streams;
- Windows reserved names, trailing spaces/dots, or names that collide after Unicode NFC and
  case-folding;
- duplicate entries, file/directory prefix collisions, or a second `manifest.json`;
- symbolic links, hard links, junctions, reparse points, sockets, devices, FIFOs, or unknown entry
  types;
- encrypted entries or compression methods outside the explicit allow-list (`stored`, `deflate`);
- any entry larger than its declared size, any entry exceeding its per-file limit, total expanded
  bytes exceeding the signed limit, too many entries, excessive path depth, or excessive path
  length;
- a compressed archive size different from the signed catalog size, or any archive/file/manifest
  digest mismatch.

Limits are evaluated from both headers and bytes actually streamed. The extractor MUST stream to
disk while counting bytes and hashing; it MUST NOT call `extractall`, load a whole sample library in
memory, or trust ZIP CRC/declared sizes as a security boundary. The destination of every file is
resolved and checked to remain below the staging root immediately before creation. Existing staging
entries are never followed.

Recommended v1 hard ceilings are 20,000 entries, 32 path segments, 240 UTF-16 code units per
relative Windows path, and the artifact-specific expanded-byte limit in the signed catalog. A
general compression-ratio limit should not reject legitimate audio packs; exact expanded bytes and
entry limits are the primary zip-bomb controls.

## Planning and consent

`setup plan` writes a create-only plan document (the default is managed state; `--output` chooses an
explicit location) and returns a random 256-bit single-use consent nonce plus the plan digest. The
persisted plan binds:

- the verified catalog digest/version/expiry;
- every pack ID/version, URL, compressed size, archive digest, expanded limit, and license;
- the exact managed destination and required free-space estimate;
- all system changes (normally none) and the LedgerLine installer version.

`setup apply` accepts the nonce, opens the persisted plan, and recomputes its digest. It MUST NOT
re-plan from the current catalog. The nonce is consumed atomically when apply starts and cannot be
replayed. A plain deterministic hash of public plan data is a plan identifier, not evidence of user
consent. Plans expire after a short documented period, are local-user-only, and are invalidated by
any material change. Changing packs, destination, URL, digest, installer version, or catalog means
creating and approving a new plan.

For non-interactive automation, the agent still presents exact bytes, licenses, destinations, and
system changes before requesting user approval. CLI flags cannot silently convert a plan into
consent.

## Download and atomic installation

1. Check that the plan is unexpired and single-use, the catalog signature remains valid, and free
   space covers compressed file, expanded files, existing installation retained for rollback, and
   safety margin.
2. Download to a `.partial` file in a managed content-addressed downloads directory. The v1
   installer intentionally restarts rather than resumes. Enforce the exact compressed byte count
   while streaming and then SHA-256 the complete file.
3. Preflight the entire archive. Extract to a newly created, private staging directory on the same
   volume as the final installation. Stream and verify every file against `manifest.json`.
4. Run pack validation and an offline SoundFont preset-table smoke test from staging. Nothing in a
   pack is executed.
5. Write an installation receipt containing catalog digest/version, artifact and manifest digests,
   file inventory, license/notice paths, installer version, and install time.
6. Keep versions side-by-side, atomically rename staging to the final versioned directory, then
   atomically replace the small active-version pointer file. Never merge files into an existing
   pack directory. Previous versions remain available for rollback.
7. Run `doctor` and an explicit render through the active pointer after setup.

An interrupted install is recovered by inspecting receipts and names, never by guessing from
partially extracted files. Before activation, deleting staging is sufficient. If activation or its
post-check fails, restore the previous active pointer and keep the failed version quarantined for
diagnostics. Cleanup never traverses symlinks/reparse points and deletes only paths whose generated
operation ID and resolved parent match managed state.

Pack versions install side-by-side at `packs/<pack-id>/<version>/`; the active pointer contains a
version string rather than being a filesystem symlink. Concurrent applies are serialized with a
per-user installer lock. v1 fails closed on a stale lock and requires manual inspection instead of
guessing that ownership can be reclaimed safely.

## Required adversarial tests

At minimum, CI must cover:

1. valid catalog/pack install, idempotent detection, update, and rollback after each activation
   boundary;
2. bad, missing, unknown-key, expired, and non-canonical catalog signatures; catalog downgrade and
   key-rotation/revocation cases;
3. archive hash/size mismatch, manifest hash mismatch, undeclared/missing files, duplicate JSON and
   ZIP entries;
4. `../`, absolute, drive, UNC, ADS, backslash, reserved-name, Unicode/case collision, trailing-dot,
   file-prefix, and overlong path cases;
5. symlink/junction/device entries and a pre-existing reparse point inside staging;
6. forged header sizes, streamed overrun, too many entries, deep paths, and expanded-byte-limit zip
   bombs;
7. network truncation, redirect downgrade/cross-origin redirect, resume with changed ETag, disk
   full, permission failure, process interruption, and concurrent apply;
8. altered plan, expired plan, wrong nonce, nonce replay, catalog replacement after planning, and
   installer-version mismatch;
9. smoke-render failure and malicious pack content that is inert because setup never executes pack
   files.

Tests should use tiny synthetic archives and temporary managed roots. They must not need network
access or install to the real user data directory.
