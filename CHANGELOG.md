# Changelog

All notable changes to Minos will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project intends to follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html) after its first
release.

## [Unreleased]

### Added

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
  recovery-revision fencing before heartbeat reconciliation.
- Shared bounded raw-blob staging with atomic local content-addressed storage and
  direct single-part S3 conditional writes, checksum-enabled verification,
  bounded conflict handling, and verified reuse receipts.

### Changed

- Replaced the planned Lambda, ECR, CloudFront, frontend-S3, and QNAP topology
  with one dedicated Mikr.us `3.0` Docker Compose host behind Cloudflare Tunnel;
  AWS now provides only versioned source storage, narrow IAM, budgets, and
  Terraform state.
- Replaced S3 sidecar and S3-hosted compare-and-swap watcher state with a mutable
  SQLite control plane on Mikr.us, revisioned S3 database recovery images, and
  APScheduler;
  GitHub Actions remains build and test infrastructure only.
- Superseded an earlier plan that split web delivery between a private
  S3/CloudFront frontend and a Lambda-hosted API.
- Made security and correctness the first engineering priority, followed by
  measured performance, and adopted unit, targeted mutation, integration, and
  pair-programmer review gates for incremental work.
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
