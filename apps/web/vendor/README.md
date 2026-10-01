# MiniSearch

Vendored upstream UMD build of MiniSearch 7.2.0 (MIT), with its original license.
No network/CDN or npm runtime dependency is required.

Source: <https://registry.npmjs.org/minisearch/-/minisearch-7.2.0.tgz>

The npm registry SHA-512 integrity was verified before extracting exactly
`package/dist/umd/index.js` and `package/LICENSE.txt`. The build script pins
their SHA-256 identities and rejects local modifications. Update the version,
review the upstream license, refresh both hashes and run the catalog contract
and browser tests when upgrading. Do not modify the vendored search algorithm.

`scripts/build_catalog_assets.py` concatenates the library and
`apps/web/sources.js` into the existing deployed `/sources.js` asset. This keeps
the current CSP, exact routes and atomic edge activation/rollback inventory
unchanged. Generated assets are checked for drift in CI.
