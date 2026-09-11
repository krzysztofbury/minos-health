# TODO

Execution backlog lives in [GitHub issues](https://github.com/krzysztofbury/minos-health/issues)
(milestones M0-M7, numbered tasks with step-by-step descriptions and
estimates). This file remains the spec-level checklist; issues carry the
day-to-day order.

## Accepted Decisions

- [x] Pivot from an ICD-10 reference (Apollo Codebook) to an explainable JGP
      settlement platform (2026-08-06).
- [x] Use **Minos** as the provisional public brand pending trademark counsel.
- [x] Keep `minos` as the Python package and CLI command; repository is
      `minos-health`.
- [x] `v0.0.1` datasets: `icd10-pl`, `icd9-pl`, and the `jgp-szp` knowledge
      base; grouper engine in `v0.0.2`; optimizer in `v0.0.3`.
- [x] Compliance framing: coding completeness and rejection prevention, never
      coding beyond documentation; outputs in points, not PLN.
- [x] Bitemporal data model (valid time plus knowledge time); retroactive
      amendments are the norm.
- [x] Watchers-first (phase 0): archive upstream files before product code.
- [x] Cut semantic/vector search from scope; boring SQLite plus FTS5 is the
      default engine candidate.
- [x] Provide both an offline CLI and an anonymous read-only web application.
- [x] License repository software, infrastructure, tests, and documentation as
      `MIT OR Apache-2.0` and license data separately.
- [x] Build one immutable `linux/amd64` HTTP container.
- [x] Use AWS Lambda as the primary target and Mikr.us as a measured fallback.
- [x] Deploy tested image digests from signed version tags without rebuilding.
- [x] Use Python 3.14 as the sole application language through 2027-08-21;
      keep synchronous domain rules independent of FastAPI and persistence.

## Domains

- [ ] Register `minoshealth.com` (checked free 2026-08-06, ~70 PLN/yr).
- [ ] Register `minoshealth.pl` (checked free 2026-08-06).
- [ ] Decide on premium `minos.health` (checked free 2026-08-06, ~450 PLN/yr):
      verify the renewal price is not first-year promo pricing and buy only
      after trademark counsel clears the name.
- [ ] Configure registrar lock, 2FA, auto-renewal, recovery codes off-host,
      and DNSSEC when the delegation chain supports it.
- [ ] Set up `.pl` as a proxied Cloudflare Redirect Rule to `.com` (DNS alone
      cannot redirect).

## Trademark And Legal

- [ ] Engage a trademark attorney on **MINOS** (pre-screen 2026-08-06 via
      TMview API):
      - EUTM `009772146` (MINOS-EMI, GR music label) covers class 9
        "interactive computer software" without field limitation; assess
        opposition exposure and non-use revocation potential (registered
        2011).
      - EUTM `010138105` (CEA, FR) class 42 is nuclear-research-limited; low
        overlap.
      - PL: Grupa INCO holds MINOS in class 3 (cleaning); funeral-services
        mark in class 45; both dissimilar, not expected blockers.
      - National layer for EUTM opposition mapping: CZ `593741` (software,
        2024), FR MINOS 400 (Inetum ERP), IT MINOSSE, GR `D43553`.
- [ ] Decide filing strategy: PL national mark first with a specification
      narrowed to medical-billing/DRG software (classes 9, 42), then EU.
- [ ] Manual registry work the API screen could not cover: UPRP ewyszukiwarka
      direct search (R. numbers, renewal status), EUIPO eSearch opposition and
      cancellation histories for the marks above, phonetic-similarity search,
      KRS and common-law use clearance.
- [ ] Fallback name if counsel advises against Minos: **Telesphoros/Telesfor**
      (registry-clean in EM/PL/WO on 2026-08-06; `telesphoros.health`,
      `telesphoros.pl`, `telesforhealth.pl` free; `telesfor.pl` taken
      09.2025). **Obol** is rejected: EUTM `018756046` (Obol Association)
      covers classes 9/36/42.
- [ ] Formal legal opinion: Art. 4 pr. aut. status of zarządzenia Prezesa NFZ
      and their appendices (katalog, charakterystyka, algorithm) for use in a
      commercial product.
- [ ] Formal legal opinion: scope of `CC BY-NC-SA 3.0 IGO` on the RSK ICD-10
      translation (the license sentence grammatically attaches to the WHO 2019
      English edition; the commercial core must not depend on the resolution).
- [ ] Resolve WHO licensing before adding international ICD-10 or ICD-9 data.

## Phase 0: Watchers And Archive

- [ ] Scheduled watcher: archive the RSK catalog and metadata for every returned
      HCD; alert on set/schema drift; download only allowlisted product files
      (initially `00CD10` XML), compare complete metadata, and compute SHA-256.
- [ ] Scheduled watcher: NFZ DSOZ listing, general Centrala provider news,
      Komunikaty JGP, CZS ICD-9 metadata/pinned XML, projects, and AOTMiT tariff
      signals. Use official machine interfaces where available, not HTML only.
- [ ] BAW anonymous-API fetcher: archive source, amendment, and consolidated
      document graphs, root ZIPX packages, selected attachments, all relevant
      dates, and checksums. Use local/QNAP execution if a runner is blocked;
      browser automation requires a separate task and captured evidence that it
      succeeds from the affected environment.
- [ ] Provision the phase-0 private versioned S3 snapshot bucket and watcher
      OIDC role before the first production watcher run. Archive objects under
      content-addressed prefixes with conditional creates; deny overwrite and
      delete there. Allow conditional compare-and-swap updates only under a
      separate state prefix.
- [ ] Archive every selected raw file immediately, keyed by source identity and
      SHA-256 computed at archive time (upstream publishes no checksums).
- [ ] Independent encrypted QNAP copy with credentials outside the AWS
      deployment account; restore drill.
- [ ] Issues #6-#8 emit immutable observations and bounded change or incident
      records. Issue #21 turns reviewed change records into data PRs with
      manifest updates and human-readable diffs; never auto-merge.
- [ ] Alert on watcher failure and on upstream schema drift (new załącznik
      names, changed file formats).

## Repository Baseline

- [x] Initialize `pyproject.toml`, `uv.lock`, and the `src/minos` package with
      domain, store, ingest, CLI, and HTTP modules; add grouper in `v0.0.2`.
- [x] Establish `apps/web`, `database/reference/sqlite/{migrations,queries}`,
      `tests/{unit,integration,compatibility,fixtures,golden}`, `manifests`,
      `infra`, and `scripts` boundaries; do not commit empty future components.
- [ ] Implement raw numbered SQLite migrations with transactional application,
      an applied-migration checksum table, and fresh-database build tests. The
      runtime must only validate and open the reference artifact read-only.
- [ ] Reduced engine spike: pin Python and SQLite versions with FTS5; verify
      read-only operation, deterministic Polish search, standalone CLI
      packaging, and Lambda compatibility.
- [ ] Add Ruff, mypy, pytest, dependency policy, and package metadata.
- [ ] Add small committed fixtures (XML, XLSX, DOCX) outside the ignored
      `/data` directory.
- [ ] Add a checked-in dataset manifest containing source identities and
      checksums.
- [ ] Add architecture decision records if implementation changes accepted
      decisions in `docs/v0.0.1/decisions.md`.

## Data Ingestion

- [ ] Download and fixture the RSK ICD-10 XML (HCD `00CD10`), the NFZ ICD-9 PL
      XLSX/XML, załącznik 1a (katalog JGP), załącznik 9 (charakterystyka JGP),
      and the komunikat algorithm DOCX plus rank tables.
- [ ] Record source identity for every file: URL or UUID, byte size, computed
      SHA-256, upstream timestamps, publisher, and disclaimers, without
      inventing publication dates.
- [ ] Define terminology, concept, description, relation, group, condition,
      tariff, source-release, and ruleset-release tables with bitemporal
      fields.
- [ ] Implement streaming XML parsing with explicit validation errors; reject
      DTDs and external entities; bound depth, value size, record count,
      source bytes, and parser resource use.
- [ ] Implement hardened XLSX parsing (merged-cell headers are the norm in NFZ
      catalogs) with zip entry-count, expansion-ratio, and size limits.
- [ ] Implement komunikat DOCX extraction for algorithm metadata; the
      algorithm steps themselves are implemented as versioned engine code with
      fixture-backed tests.
- [ ] Curated overlay dataset for rules living in legal prose (correction
      coefficients, coefficient-combination formula) with `manual` provenance
      and mandatory review.
- [ ] Reject duplicate codes, broken parents, unresolved list references,
      missing descriptions, and unknown structural elements that affect
      meaning.
- [ ] Build the database transactionally and reproducibly; emit a
      machine-readable artifact manifest, database checksum, and a
      human-readable diff against the previous release.
- [ ] Validate complete-dataset counts, hierarchy integrity, and list
      reference integrity.

## Domain And Search

- [ ] Define namespaced terminology, grouping-scheme, and release identifiers.
- [ ] Implement Unicode-aware Polish normalization without changing source
      text.
- [ ] Implement exact code and exact normalized-description matches.
- [ ] Implement indexed prefix/token search and bounded substring fallback.
- [ ] Define deterministic rank and tie-break rules in tests.
- [ ] Service-date resolution: given a date, select the governing ruleset
      release; error explicitly when no release covers the date.

## Grouper (v0.0.2)

- [ ] Implement `minos.grouper`: encounter facts against a pinned ruleset
      release; rules as data, engine as versioned code.
- [ ] Full rule trace: every consulted condition with outcome; near-miss
      report for conditions that almost changed the group.
- [ ] Golden-case regression suite; cross-check against the CGM grouper and
      WikiJGP case builder; review divergences.
- [ ] Validate with real anonymized ward cases before public release.
- [ ] `POST /api/v1/group`: stateless, bounded, never logged, never stored.

## CLI

- [ ] Add `minos data build` and dataset inspection commands.
- [ ] Add `minos search` with explicit system and dataset selection.
- [ ] Add `minos jgp show <group>` with service-date selection.
- [ ] Add human-readable output, stable exit codes, and versioned JSON output.
- [ ] Include dataset provenance and the reference-only disclaimer in output.
- [ ] Verify lookup requires no network access after a dataset is built.
- [ ] Define checksummed standalone CLI and data-bundle artifacts and promote
      them from `main` to a release without rebuilding.

## HTTP And Web

- [ ] Add `/healthz`, `/readyz`, bounded search, concept detail, and JGP group
      detail endpoints with explicit service-date parameters.
- [ ] Add versioned response schemas and consistent error mapping.
- [ ] Build the responsive interface under `apps/web`: terminology search, JGP
      group browser, and version diff views; serve its output from `minos.http`.
- [ ] Display code hierarchy, grouping conditions, point values, source
      release, and the safety/compliance disclaimer.
- [ ] Exclude raw query text and encounter payloads from logs and analytics.
- [ ] Add input, result-count, concurrency, and timeout limits.

## Container And Local Runtime

- [ ] Add a multi-stage non-root `linux/amd64` Dockerfile.
- [ ] Embed static assets, provenance, and the read-only generated database.
- [ ] Run with a read-only filesystem and no unnecessary Linux capabilities.
- [ ] Add `compose.yaml` for local and QNAP-compatible smoke testing.
- [ ] Exercise one real search in the container smoke test.

## CI And Release

- [ ] Add PR checks for Ruff formatting/linting, mypy, pytest, fixture ingestion,
      dependency policy, and container smoke testing.
- [ ] Build the pinned full dataset and private GHCR image on `main`.
- [ ] Tag images by commit and address releases by digest; never deploy
      `latest`.
- [ ] Sign release digests and publish build provenance using GitHub OIDC.
- [ ] Bind image digest, source commit, and build workflow in a verifiable
      attestation; reject a moved `sha-<commit>` tag.
- [ ] Require a signed `v*` tag workflow that promotes the attested digest.
- [ ] Reject OCI indexes and attestations as the Lambda runtime image; copy
      one verified `linux/amd64` image manifest to ECR.
- [ ] Retain enough GHCR releases for rollback within GitHub Free storage
      limits.

## AWS And Cloudflare

- [ ] Bootstrap a private versioned S3 Terraform state bucket with native
      locking.
- [ ] Import and extend the phase-0 S3 snapshot bucket; provision ECR, IAM,
      Lambda, Function URL, CloudFront OAC, ACM, logs, alarms, and budgets with
      Terraform.
- [ ] Restrict the deploy role to the repository and release-tag OIDC subject;
      watcher jobs get write access to the snapshot bucket only.
- [ ] Authenticate to GHCR with a GitHub token scoped to `read:packages`; use
      the AWS OIDC role only for AWS actions.
- [ ] Copy the verified GHCR image manifest by digest into same-region ECR.
- [ ] Publish a Lambda version and update a `live` alias atomically.
- [ ] Attach the Function URL to the `live` alias qualifier and grant
      CloudFront both required Lambda invocation actions, scoped to the
      distribution ARN.
- [ ] Disable caching for API/health behaviours, forward only the required
      query parameters, and keep CloudFront access logging off pending privacy
      review.
- [ ] Plan the `v0.0.2` POST path: signed POST to a Lambda Function URL
      through CloudFront requires correct `x-amz-content-sha256` handling;
      design before implementation, not during.
- [ ] Manage Cloudflare DNS with Terraform and keep the CloudFront record
      DNS-only.
- [ ] Verify rollback by restoring the previous Lambda alias version.

## Verification And Operations

- [ ] Define launch latency, availability, concurrency, and monthly cost
      budgets.
- [ ] Measure container size, startup, RSS, and complete-dataset search
      latency.
- [ ] Load-test warm and cold Lambda paths at representative launch
      concurrency.
- [ ] Verify production through the public domain with a real search request.
- [ ] Add privacy-safe structured logging and CloudWatch alarms, including
      per-source dataset age and watcher failure alerts.
- [ ] Test recovery from Git, versioned S3 artifacts, GHCR digest, and
      Terraform state without relying on a runtime disk backup.
- [ ] Maintain and restore-test the encrypted QNAP archive of raw snapshots
      and critical state metadata using credentials outside the AWS
      deployment account.
- [ ] Document benchmark conditions that trigger a move to Mikr.us `3.0`.

## Later

- [ ] Revisit optional editor conveniences only after observed friction: debug
      adapter integration, test-navigation plugins, and project task shortcuts.
      Do not add repository-owned Neovim dotfiles.
- [ ] `v0.0.3` optimizer: what-if simulation framed as completeness and
      rejection prevention, validated with settlement practitioners.
- [ ] AOS and REH grouping schemes (`jgp-aos`, `jgp-reh`) after `jgp-szp`.
- [ ] NFZ statistics integration (api.nfz.gov.pl, attribution required) for
      group-level utilization context.
- [ ] Add separately namespaced international ICD variants after licensing
      review.
- [ ] Revisit Polish medicinal products and substitution as a separate module.
- [ ] Evaluate a public Python SDK only after the internal domain API is stable.
- [ ] Evolve `apps/web` into an installable PWA with SQLite WASM, supported
      browser storage, versioned client-data manifests, verified full-snapshot
      updates, atomic activation, rollback, and visible dataset freshness.
- [ ] Design central authentication, subscriptions, entitlements, payment
      webhooks, and an idempotent usage ledger in a separate application
      database; store no card details or encounter payloads.
- [ ] Define online free and paid calculation allowances with server-side
      enforcement; offline UI state is feedback, never authorization.
- [ ] Define premium organization/hospital offline licensing through signed,
      expiring entitlements and periodic renewal or manual import; do not use
      browser-side per-run billing as a security boundary.
- [ ] Before offline calculation, select and validate a browser-executable
      grouper against the canonical golden cases and complete rule traces; do
      not duplicate grouping rules before this requirement is measured.
- [ ] Design any mutable user features with authentication, privacy analysis,
      and a separate database backup and restore strategy.
