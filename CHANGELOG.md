# Changelog

All notable changes to Minos will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project intends to follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html) after its first
release.

## [Unreleased]

These entries include public validation deployments, not a versioned product
release. No version tags or GitHub Releases have been published; the complete
signed-release and deployment-acceptance gates remain #41/#27.

### Added

- A read-only site audit (`tests/browser/site-audit-browser.mjs`) of every
  public page family: no-JS at three widths, 200% text, clipping, console errors
  and script origins (#80). Release verification now refuses a homepage the
  edge proxy rewrote (`/cdn-cgi/`). See [the audit](docs/validation/site-audit.md).
- Source catalog publisher and file-format filters (including historical
  attachments), name and observation-time sorting, 50-card pages and a shareable
  view link that never carries typed text (#80, #185; deployed at `736ba9f` on
  2026-10-13). See [discovery evidence](docs/validation/source-discovery.md).
- Useful initial no-JS source catalogs, bounded provider/dataset pagination,
  and stable RSK/BAW source, dataset and exact-observation citation URLs (#80,
  #171; deployed with #172 at `088f190` on 2026-10-12). Retained metadata survives
  generation cleanup, with owner-approved 1,024-record / 256 MiB provider
  limits, explicit exhaustion, exact-byte recovery and immutable cited revisions.
  Raw files remain private. Browser refresh preserves an independently failed
  provider's initial HTML; maintenance hides unavailable API-backed detail links.
  Production copied-state recovery and public/browser acceptance passed; see
  [activation evidence](docs/validation/source-citations.md#production-activation).
- Release-pinned concept JSON with source-local hierarchy, separately attributed
  supporting evidence, bounded dated JGP membership and explicit release-scoped
  rank applicability (#23). Deployed through #169 at `da8b675` on 2026-10-12;
  [public acceptance](https://github.com/krzysztofbury/minos-health/issues/23#issuecomment-6121441538)
  passed and the issue is closed.
- Source-backed JGP catalog comparisons with explicit service-date/release
  selectors, scope/group filters, old/new base values and dataset-pinned shared
  URLs, including retroactive 74/2026 versus 102/2026 amendments (#104).
  Delivered through #160/#161/#164 and verified publicly on 2026-10-11 at
  `bc4e9ef`, after the first two candidates were rolled back for comparison
  timeouts. Preloaded comparisons, shared URLs and browser acceptance pass.
- Supported deterministic grouping with reviewed MODTAR binding, explicit
  modifier declarations, source-linked trace and bounded near misses; stdin CLI,
  stateless HTTP endpoint and stepped desktop/mobile encounter builder
  (#146/#147/#150/#154).
- Governing-act product classification and W exclusions in schema 10, product
  explanations in the builder, and 14 reviewed synthetic/pinned-source golden
  cases (#156, deployed for validation 2026-10-09).
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

- The archive moved from AWS S3 to one EU-jurisdiction Cloudflare R2 bucket
  (#200). R2 has no object versions, so integrity rests on SHA-256 keys,
  create-once writes with native checksums, bucket lock rules against overwrite
  and delete, and ETag-pinned recovery downloads. `MINOS_WATCHER_S3_ENDPOINT`
  (an R2 account endpoint only) replaces `AWS_REGION` and the bucket-owner
  setting, which R2 answers with `501`. The archive selection is schema 2
  (endpoint, no version IDs) and the signed recovery inventory drops its version
  field. The trusted recovery copy is a verified download plus a create-once
  upload, because R2 `CopyObject` ignores `If-None-Match`. `deploy_watcher`
  gains a rollback-safe one-time storage migration and accepts dispatch-run
  main CI (#174). The AWS Terraform stacks, operator scripts, real-AWS harness
  and their tests are removed; CI no longer installs Terraform.
- Design critique (#196): the Gruper shows its rule-scope limit as a callout
  above the form and again with the result; the case card lists only the facts
  entered, leaving progress to the stepper. Visible dates read DD.MM.RRRR (or
  MM.RRRR) on JGP, dictionary, search and source pages, while URLs and
  attributes keep ISO. The source catalog keeps its tabs and text filter and
  moves publisher, format and order behind "Więcej filtrów". On the home page,
  "Przywróć wszystkie filtry" appears only while a source is switched off, the
  placeholder fits a phone, and shortcuts sit 4 across or 2×2.
- A failed API start keeps its evidence before rollback (#198): the API logs the
  startup exception class only, and the host records the candidate's running,
  OOM and restart state, that class and its cgroup memory counters.
- The validation edge image moved to the upstream NGINX 1.31.7 / Alpine 3.24.2
  rebuild, which fixes `CVE-2026-77214` (libexpat 2.9.0-r0) and
  `CVE-2026-88806` (libx11 1.8.14-r0) found by the image scan, and ships
  tiff 4.7.2-r0, so the extra tiff layer is gone. FastAPI 0.143.2 and the
  Terraform AWS provider 6.69.1 are included.
- Validation releases start the new API colour (`reference-api-blue` or
  `reference-api-green`) beside the live one and retire the old colour only
  after the new API and edge are healthy (#192). The API startup (87 to 270
  seconds on the host) no longer returns 502; only the edge swap remains, about
  0.5 seconds in the disposable container smoke and about 24 seconds in
  production (2026-10-19, accepted as a known limit). Rollback reuses a still-running
  previous colour. The public-edge Kuma monitor now retries 3 times at 20-second
  intervals before alerting, so one failed check no longer pages.
- Cloudflare zones (#28, #80, owner-approved 2026-10-14): Email Address
  Obfuscation, Web Analytics auto-install and Network Error Logging are off,
  and `www` hosts redirect in one hop to `https://minoshealth.pl` with the path
  and without the query string.
- `/gruper` without JavaScript shows only the explanation instead of step
  controls that cannot work, and long attachment names wrap inside source cards
  (#80).
- Hash the reference database once per API process instead of once per pool
  connection, re-hashing any file whose stat identity changes, and index
  characteristics evidence in one pass. Validation is unchanged; local readiness
  under production limits fell from about 12 to 8.5 seconds. Startup phase
  timings are logged with fixed fields (#189).
- Bind MODTAR generation 103 (published 2026-10-09) to every 2026 catalog under
  the owner rule "MODTAR as published wins": PZA04 and PZF13 gain 17.10 (1.2)
  and 17.11 (1.1) for July-December, settling that #129 difference for those
  months. Catalog and characteristics knowledge horizons move to 2026-10-13 on
  BAW evidence that no act after 102/2026 exists. The ICD-10 reader accepts the
  rsk3 export namespace; the RSK 013 re-export is content-identical, so the
  deployed ICD-10 pin stays (#184).
- Stop re-running full CI on pushes to `main`; releases dispatch full CI on
  their commit instead (#174, #186).
- Bound activation and rollback startup readiness: API first, then edge, under
  one 300-second deadline, tolerating initial unhealthy status only while the
  process stays alive (#181, #182). First production activation completed in
  212.7 seconds at `736ba9f` on 2026-10-13.
- Transfer the tested API/edge images to the private validation publisher instead
  of rebuilding them, with same-run artifact identity, bounded one-day image
  retention and post-push identity verification (#174, #180). Hosted acceptance
  passed; level-1 transport compression (#183) cut the publish job to 45 seconds.
- Scope documentation-only CI to document/security checks while retaining full
  checks for runtime, workflow and unknown changes. Cancel obsolete PR validation
  without cancelling serialized manual publication (#174, #178).
- Remove the blanket knowledge-cutoff warning and repeated cutoff copy from
  JGP and attachment-9 pages, keeping concrete source/version and applicability
  details. Bundle/API knowledge fences remain enforced; this does not advance
  data freshness or enable automatic data activation (#80, #172; deployed at
  `088f190` on 2026-10-12).
- Add a bounded NFZ/CZS/AOTMiT publication metadata projection and a separate
  source-catalog filter, preserving observation, publication and declared-validity
  dates without inferring settlement applicability (#92). Deployed through
  #167/#168 at `0bf9b31` on 2026-10-12; watcher and public/browser acceptance
  passed and the issue is closed. See [metadata evidence](docs/validation/nfz-publication-metadata.md).
- Preserve structured host refusals and bounded, privacy-safe Compose failure
  diagnostics. Rollback verifies both validation services, exact running images
  and operational isolation; explicit retries retain their original baseline,
  and complete rollback requires public readiness and homepage checks (#162).
  Deployed through #166, with actual-host recovery acceptance completed and the
  issue closed. The original #161/#164 failure causes remain unconfirmed.
- Preload bounded immutable JGP comparison schedules before API readiness.
  Cache ownership follows the verified bundle; incomplete or over-budget
  construction fails startup. Date/release validation and request budgets remain
  in place, without per-request source projection or JSON decoding (#104).
- Avoid serializing unused offline audit categories in dated JGP comparisons;
  keep the same source reads, schedule values and request budgets after #160's
  comparison timeout caused a guarded validation rollback.
- Continue independent grouping warm-up entries after a timeout and retry
  unfinished entries once, with privacy-safe stage diagnostics and accurate
  partial counts. Cancel between steps and finish active readers before closing
  the SQLite pool; request and source-validation budgets are unchanged (#157).
- Reconciled README, TODO, specification status and delivery documents with
  the verified #156 release and GitHub backlog (2026-10-11). Practitioner builder
  acceptance from closed #39 is explicitly tracked in #158. A subsequent
  2026-10-12 reconciliation records #157/#162/#92/#23 as delivered and closed,
  #169 as the latest verified validation deployment, and #171 as pending review
  and activation. Full release, capacity and ward-evidence gates remain open.
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
