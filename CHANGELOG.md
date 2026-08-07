# Changelog

All notable changes to Minos will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project intends to follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html) after its first
release.

## [Unreleased]

### Added

- Initial project documentation and development configuration.
- Accepted `v0.0.1` architecture, data, delivery, and operations
  documentation.
- Data-sources reference covering NFZ zarządzenia, komunikaty JGP, ICD-9 PL,
  RSK ICD-10, the NFZ statistics API, and AOTMiT tariff signals, with watcher
  and archive rules.
- Apache-2.0 or MIT dual licensing for repository materials, excluding medical
  datasets and data-bearing artifacts.
- Explicit provenance, privacy, safety, compliance, and data-license
  requirements.

### Changed

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
