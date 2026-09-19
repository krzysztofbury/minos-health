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
- [x] Build immutable `linux/amd64` API, edge, and watcher images and run the
      complete stack through Compose locally and on a dedicated Mikr.us `3.0`.
- [x] Use Cloudflare Tunnel as the only public ingress; keep GitHub Actions as
      build and test infrastructure rather than a production runtime.
- [x] Use mutable SQLite for watcher control state and private versioned S3 for
      immutable raw bytes and revisioned control-database recovery images.
- [x] Deploy tested image digests from signed version tags without rebuilding.
- [x] Use Python 3.14 as the sole application language through 2027-08-21;
      keep synchronous domain rules independent of FastAPI and persistence.
- [x] Prioritize security and correctness, then measured performance, then
      developer convenience; require unit, targeted mutation or fault injection,
      integration, and pair-programmer review gates for implementation chunks.

## Domains

- [x] Register `minoshealth.com` and delegate it to Cloudflare.
- [x] Register canonical `minoshealth.pl` and delegate it to Cloudflare.
- [ ] Decide on premium `minos.health` (checked free 2026-08-06, ~450 PLN/yr):
      verify the renewal price is not first-year promo pricing and buy only
      after trademark counsel clears the name.
- [ ] Configure registrar lock, 2FA, auto-renewal, recovery codes off-host,
      and DNSSEC when the delegation chain supports it.
- [x] Route canonical `.pl` through Cloudflare Tunnel and set up `.com` as a
      proxied permanent redirect to `.pl` for allowlisted public paths without
      forwarding query values; serve an honest maintenance page until the
      application exists.
- [ ] Enable Cloudflare Email Routing for `minoshealth.pl`, verify
      `krzysztof@datacraze.io` as the destination, and route
      `kontakt@minoshealth.pl` to it. Test delivery from an unrelated mailbox.
- [ ] Decide whether inbound-only forwarding is sufficient. If replies must use
      `kontakt@minoshealth.pl` as the sender, configure a separate SMTP mailbox
      or verified send-as provider; Cloudflare Email Routing does not send mail.

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

### Immediate Delivery Sequence

- [ ] Complete issue #49 before continuing source-specific issue #6 work. Keep
      the accepted order `#49 -> #6 -> #8 -> #7`.
- [ ] Deliver #49 control state first: numbered SQLite migrations, complete
      migration checksums, one writer, fenced claims, legal run transitions,
      durable due cursors, observations, blob references, incidents, audit
      events, and monotonic state revisions.
- [ ] Deliver #49 archive storage next: equivalent local and S3 content-addressed
      blob stores, bounded single-part conditional writes, native checksum and
      byte-count verification, and disposable-AWS acceptance evidence. Present
      the exact phase-0 Terraform plan for approval before applying it.
- [ ] Deliver #49 recovery and scheduling next: verified SQLite recovery images,
      bounded paginated restore selection, operator-trusted images and signed
      inventories, APScheduler reconstruction, one bounded catch-up, stale-run
      expiry, and heartbeat reconciliation.
- [ ] Finish #49 production integration: add the pinned watcher service to the
      Mikr.us Compose topology, service-restricted secrets and volumes,
      Healthchecks.io, scheduler-paused preflight, ordinary-loss restore, and
      suspected-compromise restore evidence.
- [ ] Resume issue #6 by capturing dated raw live RSK fixtures before changing
      parsers, then finish bounded HTTPX retrieval and Pydantic validation for
      the catalog and every returned `basic-info` resource.
- [ ] Store exact catalog and metadata response bytes and complete observations
      for every HCD. Compare full structures; use `uuid + creationDate +
      sizeBytes` only as a cheap change trigger.
- [ ] Archive only the allowlisted `00CD10` XML initially. Stream from byte zero,
      compute SHA-256, verify declared and received lengths, reject HTML or wrong
      magic, re-fetch metadata after the batch, and quarantine rotated or
      ambiguous transfers.
- [ ] Add issue #6 daily, weekly forced re-download/re-hash, and manual modes;
      prove interruption, retry, metadata rotation, same-metadata changed bytes,
      stale-worker, transaction failure, and duplicate-notification behavior.
- [ ] Complete issue #6 with the atomic #49 observation transaction, exact
      revisioned recovery image, external heartbeat, documented local and
      production Compose commands, and the first forced Mikr.us observation of
      all HCD metadata plus the current `00CD10` XML.
- [ ] Planning estimates remain 30-50 hours for #49 and 8-14 hours for #6 after
      #49, excluding the RSK transport hardening already implemented.

- [ ] Scheduled watcher: archive the RSK catalog and metadata for every returned
      HCD; alert on set/schema drift; download only allowlisted product files
      (initially `00CD10` XML), compare complete metadata, and compute SHA-256.
- [ ] Scheduled watcher: NFZ DSOZ listing, general Centrala provider news,
      Komunikaty JGP, CZS ICD-9 and MODTAR metadata/pinned XML, projects, and
      AOTMiT tariff signals. Use official machine interfaces where available,
      not HTML only.
- [ ] BAW anonymous-API fetcher: archive source, amendment, and consolidated
      document graphs, root ZIPX packages, selected attachments, all relevant
      dates, and checksums. Browser automation requires a separate task and
      captured evidence that it succeeds from the Mikr.us environment.
- [x] Bootstrap the dedicated versioned Terraform state bucket locally and back
      up bootstrap state independently.
- [ ] Initialize the `phase0` root directly against its own remote-state key.
- [ ] Provision the phase-0 private versioned S3 snapshot bucket, narrowly
      scoped Mikr.us watcher identity, GitHub OIDC read role, and budget in
      remote Terraform state before the first production run. Enforce
      conditional creates and deny watcher overwrite and deletion.
- [x] Define the raw-SQL watcher control schema for sources, schedules, runs,
      observations, blobs, source cursors, monotonic state revisions, changes,
      incidents, and audit events. Keep the canonical `schema.sql` synchronized
      with numbered checksum-verified migrations and tested down scripts.
- [x] Implement the single-writer control transactions for configuration and
      schedule changes, fenced claims, legal run transitions, atomic observation
      commits, state revision increments, incidents, and audit events.
- [ ] Run APScheduler `3.11.x` as one UTC `BlockingScheduler` worker with
      bounded misfire handling. Reconcile committed and heartbeat-pending runs
      before dispatch and use durable due cursors plus token-and-epoch fenced
      claims for catch-up, manual, expiry, and stale-worker rejection.
- [ ] Run issue #6 through one Docker/Compose watcher command locally and on
      Mikr.us; use local filesystem blobs in development and S3 in production,
      with shared contract tests and a disposable AWS test.
- [ ] Stage at most one download in a disk-backed `noexec,nosuid,nodev`
      directory with a `600 MiB` application quota, `512 MiB` per-file ceiling,
      startup cleanup, and pre-run free-space assertion.
- [ ] Archive every selected raw file immediately under a digest-only
      `blobs/sha256/<digest>` key. Use bounded single-part `PutObject`; send and
      verify S3 native SHA-256 plus byte count after conditional create or reuse.
      Source identity stays in SQLite.
- [ ] Create revisioned control SQLite recovery images through
      `sqlite3.Connection.backup()`, enforce class-specific lifecycle rules,
      copy one verified image weekly to operator-only trusted storage, sign the
      non-expiring chained inventory off-host, and complete ordinary-loss and
      suspected-compromise empty-host restore drills.
- [ ] Issues #6-#8 emit immutable observations and bounded change or incident
      records. An operator-dispatched protected issue #21 workflow reads one
      selected verified recovery image through OIDC and turns only the selected
      change into a data PR; never give the watcher a GitHub token or auto-merge.
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
      packaging, and deterministic `linux/amd64` container operation.
- [ ] Add Ruff, BasedPyright, mypy, pytest, dependency policy, and package
      metadata; select an automated mutation runner only after proving Python
      3.14 compatibility and bounded runtime.
- [ ] Add small committed fixtures (XML, XLSX, DOCX) outside the ignored
      `/data` directory.
- [ ] Add a checked-in dataset manifest containing source identities and
      checksums.
- [ ] Add architecture decision records if implementation changes accepted
      decisions in `docs/v0.0.1/decisions.md`.

## Data Ingestion

- [ ] Download and fixture the RSK ICD-10 XML (HCD `00CD10`), the NFZ ICD-9 PL
      XLSX/XML, załącznik 1a (katalog JGP), załącznik 9 (charakterystyka JGP),
      CZS MODTAR XML, and the komunikat algorithm DOCX plus rank tables.
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
      and mandatory review; reconcile it against CZS MODTAR mappings.
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
      group browser, and version diff views; embed its immutable output in the
      edge image and route API paths to `minos.http` under the same origin.
- [ ] Display code hierarchy, grouping conditions, point values, source
      release, and the safety/compliance disclaimer.
- [ ] Exclude raw query text and encounter payloads from logs and analytics.
- [ ] Add input, result-count, concurrency, and timeout limits.

## Container And Local Runtime

- [ ] Add a multi-stage non-root `linux/amd64` Dockerfile.
- [ ] Add a separate immutable watcher image or Dockerfile target used by both
      local and Mikr.us Compose; keep watchers out of the API runtime image.
- [ ] Embed provenance and the read-only generated database in the API image;
      build static assets as a separate checksummed artifact.
- [ ] Run with a read-only filesystem and no unnecessary Linux capabilities.
- [ ] Add `compose.yaml` for Cloudflare Tunnel, edge, API, and watcher services;
      the edge serves static assets and proxies API paths under the same origin.
- [ ] Exercise one real search in the container smoke test.

## CI And Release

- [ ] Add PR checks for Ruff formatting/linting, BasedPyright, mypy, pytest,
      fixture ingestion, documented mutation/fault-injection evidence,
      dependency policy, and a Compose browser-to-API search; automate mutation
      checks after tool selection.
- [ ] Build the pinned full dataset and private GHCR image on `main`.
- [ ] Tag images by commit and address releases by digest; never deploy
      `latest`.
- [ ] Sign release digests and publish build provenance using GitHub OIDC.
- [ ] Bind image digest, source commit, and build workflow in a verifiable
      attestation; reject a moved `sha-<commit>` tag.
- [ ] Require a signed `v*` tag workflow that promotes the attested digest.
- [ ] Verify deployable GHCR references resolve to the intended runnable
      `linux/amd64` manifests rather than indexes or attestations.
- [ ] Enable and verify GitHub immutable releases. Publish OIDC provenance, then
      independently sign and conditionally copy each release manifest to its
      operator-only S3 key. Write signed, sequenced, append-only deployment
      receipts under sequence-only conditional keys and verify a unique chain
      head after every write.
- [ ] Create a dedicated Ed25519 operator signing key, commit only its public
      verification key, keep the private key off-host, and use domain-separated
      OpenSSH namespaces for release, deployment, and recovery records.
- [ ] Retain enough GHCR releases for rollback within GitHub Free storage
      limits.

## Mikr.us, AWS, And Cloudflare

- [ ] Reuse and recovery-test the phase-0 private versioned S3 Terraform state
      bucket with native locking.
- [x] Purchase the dedicated Mikr.us `3.0` host; pin its SSH host key, rotate the
      console password, install a dedicated 1Password-backed operator key, and
      apply an idempotent Ansible baseline for key-only SSH, exact UFW rules,
      current packages, unattended upgrades, and bounded persistent logs.
- [x] Extend the reviewed Ansible baseline with exact Docker Engine and Compose
      packages, bounded logs, no unprivileged operators, and no TCP daemon socket.
- [ ] Add service UIDs and GIDs, persistent and staging volumes, and service-
      restricted secret files with the first deployable Compose stack.
- [ ] Create the `phase0` Terraform root for the snapshot bucket, conditional
      write policy, watcher IAM identity, GitHub OIDC read role, and budget.
- [ ] Create the watcher access key manually outside Terraform; store the
      recovery copy in the password manager and install it as a service-
      restricted host file for the pinned watcher UID/GID.
- [ ] Authenticate to GHCR with a GitHub token scoped to `read:packages`; use
      AWS OIDC only for GitHub archive reads.
- [x] Create the Cloudflare Tunnel, back up its scoped credential in 1Password,
      install it as a service-restricted host secret for the pinned
      `cloudflared` GID, and publish no application port directly from Mikr.us.
- [x] Deploy the exact-digest maintenance edge and tunnel stack with non-root,
      read-only containers, candidate validation before promotion, runtime
      boundary assertions, legal pages, public browser checks, and an idempotent
      Ansible apply.
- [ ] Manage Cloudflare DNS and tunnel routing with Terraform where supported;
      keep Cloudflare access logs and medical-query analytics disabled.
- [x] Add direct Plausible pageview and aggregate engagement analytics without
      cookies, custom properties, medical query values, Tag Manager, or a
      consent banner.
- [x] Publish maintenance-stage privacy policy and terms with the operator and
      privacy boundaries; obtain legal review before product or paid launch.
- [ ] Deploy exact API, edge, watcher, and `cloudflared` digests from one
      compatible release manifest; do not deploy mutable tags or use Watchtower.
- [ ] Quiesce watcher dispatch, verify a pre-deploy recovery image, test
      migrations on a disposable copy, and run scheduler-paused volume, secret,
      S3, recovery-image, and Healthchecks.io preflights before activation.
- [ ] Stop the prior watcher, start the exact candidate digest paused on the live
      volume, apply the tested migration, verify live revision/WAL/secrets and a
      post-migration recovery image, then activate one bounded canary.
- [ ] Verify rollback by restoring the prior complete digest set and rerunning
      public readiness, static-integrity, browser, and known-search checks. Keep
      migrations backward-readable and never restore a database older than an
      acknowledged candidate run.

## Verification And Operations

- [ ] Define launch latency, availability, concurrency, and monthly cost
      budgets.
- [ ] Measure container size, startup, RSS, and complete-dataset search
      latency on the actual Mikr.us `3.0` host.
- [ ] Load-test warm API paths, process restart, concurrent watcher work, and a
      documented launch burst through Cloudflare Tunnel.
- [ ] Verify production through the public domain with a real search request.
- [ ] Add bounded Docker logs, external readiness and known-search checks, and
      privacy-safe Healthchecks.io pings for watcher, recovery-image, and trusted-
      inventory schedules.
- [ ] Test recovery from Git, signed release and deployment records, versioned
      S3 snapshots and control recovery images, GHCR digests, and Terraform
      state onto an empty host.

## Later

- [ ] Revisit optional editor conveniences only after observed friction: debug
      adapter integration, test-navigation plugins, and project task shortcuts.
      Do not add repository-owned Neovim dotfiles.
- [ ] `v0.0.3` optimizer: what-if simulation framed as completeness and
      rejection prevention, validated with settlement practitioners.
- [ ] AOS and REH grouping schemes (`jgp-aos`, `jgp-reh`) after `jgp-szp`.
- [ ] NFZ statistics integration (api.nfz.gov.pl, attribution required) plus
      `dane.gov.pl` datasets `2581` and `3009` for group-level utilization and
      plausibility context; evaluate `4896` only against a concrete feature.
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
