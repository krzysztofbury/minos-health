# Private RSK Capture

This issue #6 bootstrap command captures compatibility evidence without
registering a collector or changing watcher control state:

```sh
uv run python -m scripts.capture_rsk_fixtures
```

The command uses fixed anonymous HTTPS endpoints on
`rsk3.ezdrowie.gov.pl`. Redirects, environment proxies, environment
credentials, and netrc authentication are disabled. It retrieves the role
catalog, then each returned HCD's `basic-info` sequentially. It downloads only
the single active, non-archived, non-education `00CD10` XML selected by metadata
semantics, never by a pinned UUID or filename. The XML transfer starts at byte
zero and is checked for declared size, received size, SHA-256, XML structure,
and HTML substitution. Product XML is bounded at 64 MiB, about five times the
12,738,027-byte reviewed capture. A complete metadata refetch must match before
the capture is accepted.

Output is anchored to the repository's ignored directory
`data/private-fixtures/rsk/<UTC timestamp>/` regardless of the current working
directory. The command has no output-root override. Destinations outside that
root and any symbolic-link path component are rejected. Directories are mode
`0700`, files are mode `0600`, and existing output is rejected. Exact
response and XML bodies use `blobs/sha256/<digest>`. `private-manifest.json`
stays private. `public-checksums.json` contains only safe identities, byte
counts, digests, media types, status codes, and aggregate counts. It excludes
response bodies, source text, contacts, source filenames and descriptions,
request headers, credentials, and local or cloud storage details.

Private response headers are limited to six provenance fields with printable
ASCII and per-field length bounds. The canonical private manifest is checked
against its 2 MiB byte ceiling before creation and read back before success is
reported.

Metadata rotation or any incomplete capture is renamed with a `.quarantine`
suffix and must not be used as compatibility evidence. Do not add any file
under `data/` to Git.

Validate one private capture explicitly with:

```sh
MINOS_RSK_PRIVATE_CAPTURE_ROOT=data/private-fixtures/rsk/<UTC timestamp> \
  uv run pytest -m rsk_live tests/unit/test_rsk_capture.py
```

The test is skipped unless the private root variable is set. Commit-safe
fixture tests use only the wholly synthetic data in `tests/fixtures/rsk/`.
The reviewed safe projection from the current live capture is recorded in
`docs/validation/rsk-live-capture-checksums.json`; no private body is copied
there. That checked file is exact canonical JSON evidence and must not be
reformatted. Validation rejects any byte-level reformatting and the reviewed
attestation pins its file SHA-256 as
`16129d526f282f3f41909dc8f6227f55e07c8cb7664391960cbfbc5e31d891c6`.
