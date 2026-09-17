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

### Changed

- Split the initial web delivery into a private S3/CloudFront static frontend
  and a Lambda-hosted API, with the same artifacts exercised through local
  Docker Compose.
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
