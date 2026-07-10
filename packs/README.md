# Pack release workflow

`catalog.json` is the signed release catalog. Its Starter artifact points at the immutable GitHub
v0.2.0 release asset. The Python wheel and Codex plugin contain the catalog and public key but do
not embed the 35.6 MB pack.

For a public release:

1. download only the pinned upstream files listed in `scripts/build_starter_pack.py`;
2. build twice and require identical artifact SHA-256 values;
3. run the full offline setup test and a FluidSynth smoke render;
4. upload the `.llpack` as an immutable HTTPS release asset matching the signed URL;
5. for later releases, replace the URL and increment `catalog_version`;
6. sign the exact final catalog bytes with `scripts/sign_catalog.py` and the offline private key;
7. copy the catalog and detached signature to `src/ledgerline/data/packs/`, build the wheel, and
   verify both files plus `data/trust/catalog_keys.json` are present.

The private key lives below ignored `packs/private/` only for this local development checkout. It
must be moved to protected offline storage before publishing and must never be committed or bundled.
