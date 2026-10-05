# Changelog

All notable changes to Minos will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project intends to follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html) after its first
release.

## [Unreleased]

### Added

- Explicit pinned-release `minos data diff` for verified bundle directories,
  covering terminology, catalog base values/applicability and conditions graph,
  list, correspondence and evidence changes with bounded Markdown/JSON output.
- Private-source ICD-9 5.89/5.90 and conditions 74/102 logical-diff gold checks,
  provenance-safe predecessor pins and an actionable new-base-act review checklist.
- Owner-confirmed 2026-10-05 policy for automatic activation of validated routine
  data-only updates, retaining/rolling back the last good compatible release and
  requiring durable detailed Telegram failure alerts. The #21 pipeline and
  notification integration remain unimplemented and are not enabled here.
- Reviewed service-applicability evidence for BAW amendment `74/2026/DSOZ`,
  separate from publication and entry into force, without inferring dates for
  other documents.
- Publisher-shaped BAW document links derived from a validated legal-act type
  and act number, with legacy or unsupported links withheld rather than sending
  visitors to the BAW application shell.
- Source-specific, bounded BAW provenance as an independent immutable public
  generation with deny-all artifact redistribution; a compact RSK and BAW
  sources directory with shared local search and source tabs.
- A prominent homepage link to the live sources directory and clearer copy
  about what visitors can use before the terminology search ships.
- A compact navy, paper, and cobalt Minos favicon shared by the homepage and
  sources directory, with a canonical fallback for favicon.ico requests.
- A bounded tunnel ingress for `www.minoshealth.com` that uses the existing
  allowlisted, query-safe Minos `.com` redirects instead of returning 404.
- Python 3.14 and uv development environment with Ruff, BasedPyright, mypy,
  pytest, a runnable `minos` command, and terminal/Neovim setup guidance.
- Initial project documentation and development configuration.
- Accepted `v0.0.1` architecture, data, delivery, and operations
  documentation.
- Data-sources reference covering NFZ zarządzenia, komunikaty JGP, ICD-9 PL,
  RSK ICD-10, the NFZ statistics API, and AOTMiT tariff signals, with watcher
  and archive rules.
- Revalidated the watcher source hierarchy against live RSK, NFZ, BAW, CZS,
  AOTMiT, SGA, CGM/UHC, Asseco, Comarch, and KAMSOFT evidence; corrected the
  current hospital-act lineage and replaced browser-first BAW retrieval with
  anonymous JSON and binary APIs.
- Apache-2.0 or MIT dual licensing for repository materials, excluding medical
  datasets and data-bearing artifacts.
- Explicit provenance, privacy, safety, compliance, and data-license
  requirements.
- Reproducible Mikr.us Ansible baseline with pinned host identity, dedicated
  1Password-backed SSH access, an exact IPv4/IPv6 firewall, unattended upgrades,
  bounded persistent host logs, and exact Docker Engine and Compose packages
  with a constrained daemon boundary.
- Exact-digest maintenance edge and Cloudflare Tunnel for canonical
  `minoshealth.pl`, with an allowlisted-path `.com` redirect, truthful
  readiness, restricted tunnel credentials, and no published host application
  port.
- Direct cookie-free Plausible pageviews and aggregate engagement measurements,
  plus Polish privacy-policy and terms pages for the maintenance-stage site.
- Raw-SQL watcher control schema with a canonical current-state DDL, numbered
  transactional up/down migrations, exact-byte checksums, forward-reader
  compatibility, packaged SQL resources, and migration fault-injection tests.
- Single-writer raw-SQL control transactions for canonical source configuration,
  validated UTC schedules, fenced claims, legal run transitions, and atomic
  revisioned observation, cursor, change, incident, and audit commits, including
  recovery-revision fencing before durable local finalization.
- Shared bounded raw-blob staging with atomic local content-addressed storage and
  direct single-part S3 conditional writes, checksum-enabled verification,
  bounded conflict handling, and verified reuse receipts.
- Revisioned SQLite recovery images with integrity and migration validation,
  version-pinned S3 upload, bounded restore selection, trusted operator copies,
  signed inventory chains, and safe restore installation.
- Source-neutral watcher orchestration with one UTC APScheduler worker, durable
  catch-up, stale-claim expiry, pending-run reconciliation, capacity gates, and
  privacy-safe failure handling.
- Hardened watcher Docker and Compose runtime, Mikr.us candidate preflight and
  rollback-safe activation, watcher AWS Terraform, and an opt-in disposable AWS
  acceptance harness.
- Private, bounded RSK compatibility capture with schema-bounded response
  models, semantic `00CD10` XML selection, full metadata rotation detection,
  digest-addressed evidence, a privacy-safe checksum projection, and wholly
  synthetic committed fixtures.
- Source-neutral RSK collection with exact all-HCD metadata observations,
  allowlisted streamed `00CD10` XML acquisition, canonical semantic cursors,
  daily and weekly verification semantics, deep rotation checks, and atomic
  observation, cursor, change, recovery, and local finalization integration.
- Fail-closed RSK cursor integrity validation, fresh-snapshot retries for
  metadata rotation, and remaining-claim HTTP connect/read deadline enforcement.
- Filesystem and S3 watcher runtime registration for the RSK collector, plus an
  explicit conflict-safe `watcher bootstrap-rsk` command with canonical source
  configuration and daily and weekly schedules.
- Generic multi-obligation durable source scheduling with independent cursors,
  deterministic weekly-first co-due claims, bounded restart catch-up, and
  fenced concurrent claim protection.
- Persisted retry-not-before state for failed durable obligations, local
  filesystem recovery and local finalization composition, and fail-closed source
  and schedule readiness validation.
- Strict RSK public provenance observations and bounded committed-history
  projection with canonical JSON, complete-revision truncation, immutable
  filesystem generations, atomic activation, startup restoration, and a
  watcher-RW/edge-RO shared volume.
- Exact Nginx `/api/v1/provenance/rsk` delivery and a checked schema-version-1
  empty redistribution manifest, with universal artifact-route denial.
- Responsive Polish `/zrodla` provenance page with strict browser-side schema
  validation, deterministic RSK history, source and observation dates, separate
  metadata and privately archived product checksums, accessible local filters,
  and exact static-asset and analytics routes.
- Read-only bounded watcher status backed by control SQLite, bounded local
  Docker logs, explicit supported live control-schema migration, keyed
  deployment file checksums, a bounded pre-start manual RSK canary, and exact
  post-start provenance byte validation.
- Crash-idempotent filesystem recovery publication that verifies and completes a
  valid destination hard link left before temporary-name cleanup.
- Self-hosted Uptime Kuma 2.5.5 using the pinned official MIT `slim-rootless`
  `linux/amd64` image, a rootless `10003:10003` identity, private monitoring
  network, outbound monitor and notification network, persistent data, a
  conditional default-off public route, and privacy-safe watcher up/down pushes.

### Changed

- Replaced the planned Lambda, ECR, CloudFront, frontend-S3, and QNAP topology
  with one dedicated Mikr.us `3.0` Docker Compose host behind Cloudflare Tunnel;
  AWS now provides only versioned source storage, narrow IAM, budgets, and
  Terraform state.
- Replaced S3 sidecar and S3-hosted compare-and-swap watcher state with a mutable
  SQLite control plane on Mikr.us, revisioned S3 database recovery images, and
  APScheduler;
  GitHub Actions remains build and test infrastructure only.
- Removed the paid external watcher-monitoring SaaS dependency. Detailed state
  remains in SQLite incidents, the read-only CLI status, and bounded host-local
  logs, while self-hosted Uptime Kuma provides coarse status and notifications.
  Because Kuma runs on the same Mikr.us host, a total host, network, or power
  outage cannot be detected or notified by this stack; that limitation is
  accepted for now.
- Superseded an earlier plan that split web delivery between a private
  S3/CloudFront frontend and a Lambda-hosted API.
- Made security, correctness, and measured performance co-equal release
  requirements, with evidence-based tradeoffs, representative benchmarks, and
  developer convenience following them; adopted unit, targeted mutation,
  integration, and pair-programmer review gates for incremental work.
- Recorded the phased client and commercial direction: one responsive PWA,
  immutable offline reference-data synchronization, centrally enforced online
  calculation allowances, and separately licensed organization/hospital
  offline calculation.
- Expanded the target repository layout with explicit web, raw reference SQL,
  compatibility-test, manifest, infrastructure, and future application-data
  boundaries.
- Replaced the planned Rust workspace with one Python 3.14 application using
  strict domain, store, ingestion, CLI, HTTP, and grouper package boundaries.
  Rust is frozen until 2027-08-21 unless measured product requirements prove
  Python cannot satisfy them.
- Replaced Cargo/Clippy CI and binary terminology with Ruff, mypy, pytest, and
  standalone Python application bundles.
- Reframed the project (2026-08-06) from a Polish ICD-10 terminology reference
  (Apollo Codebook) into Minos, an explainable JGP settlement intelligence
  platform: terminology plus JGP knowledge base in `v0.0.1`, grouper engine
  with rule trace in `v0.0.2`, completeness-framed optimizer in `v0.0.3`.
- Selected Minos as the provisional public brand pending trademark counsel
  review; repository renamed to `minos-health`.
- Adopted a bitemporal data model and a composite ruleset-release concept to
  handle retroactive NFZ amendments and per-reporting-month version pairing.
- Moved source ingestion to a watchers-first model with immutable raw
  archiving and reviewed data pull requests.
- Cut semantic/vector search from scope; plain SQLite with FTS5 became the
  default engine candidate.
- Earlier reframe retained for history: from Polish drug substitution to a
  medical terminology platform, with AWS Lambda as the primary production
  target.
