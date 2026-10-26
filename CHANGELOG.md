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

- Bounded API load measurement (#42): `scripts/measure_api_load.py` drives a
  browser-shaped mix plus MCP calls through a disposable local runtime at the
  production limits, or a capped public run that stops at the first server
  error. `docs/validation/api-load.md` proposes launch objectives for owner
  review and records the local run: no 5xx or OOM kill up to concurrency 8,
  overload answered as closed `busy`/`BUSY`, API process memory 278 MiB after
  startup and 328 MiB after load. The capped public run meets them for one
  user and for MCP; at four users one Gruper case got an edge 504 (#222).
  `local --api-cpus 0.25` approximates the slower host CPU, and each Gruper
  request logs its phase durations (`upload_ms` to `respond_ms`, never
  content) so time can be placed under load.
- Public MCP server for agents (#216) at `https://minoshealth.pl/mcp`:
  stateless Streamable HTTP, no key, twelve read-only tools that mirror the
  site (`search_codes`, `get_code`, `browse_codes`, `map_icd10_icd11`,
  `get_jgp_group`, `get_jgp_list`, `browse_jgp_catalog`, `compare_jgp_group`,
  `get_case_dictionaries`, `group_case`, `get_source_status`,
  `get_service_info`), two resources and two prompts, closed error codes, a
  256 KiB result bound and a 60-per-minute per-client edge limit. The
  `/dla-agentow` page documents it from the live catalog; the privacy policy
  describes the Gruper and MCP processing. `mcp==2.2.0` (MIT) joins the API
  extra; its `cryptography` chain adds the permissive MIT-0 (`cffi`) and
  Apache-2.0 OR BSD-3-Clause licences to the dependency allowlist.
- ICD-11 PL (#53): pre-released Polish ICD-11 MMS 2026-01 from RSK `99ICD1`
  as a searchable dictionary (titles, inclusions and index terms, verbatim),
  with `/slowniki/icd-11` tree and code pages that carry the WHO URI,
  definition, exclusions, the WHO disclaimer and citation and a
  not-in-force, not-for-NFZ notice. The search filter starts unselected and
  "Przywróć domyślne filtry" restores ICD-10, ICD-9 and JGP. The three WHO
  ICD-10/ICD-11 tables are imported row for row (reference schema 11) and
  shown on ICD-10 and ICD-11 code pages and in the concept API, verbatim and
  attributed to WHO, with no crosswalk of Minos's own. The database grows from
  425 to 676 MiB; API memory is unchanged. The processor reports
  `manual icd11 <version>` while current `99ICD1` files are not pinned live.
- The host processor reports JGP catalogs waiting for a manual release: a BAW
  original act or consolidated text in force whose 1a or 9 workbook no live
  release serves pushes `data-update` down with `manual jgp <document id>`,
  and the next run after a reviewed release serves it clears the alert
  (#141).
- `minos data candidate-icd10` (operational CLI) judges one archived RSK
  ICD-10 release against the live bundle without activating it: the release is
  added beside the previous one, built twice, bounded against removed codes
  and checked for unchanged history, with the owner's class-level licence
  approval in `manifests/policy/rsk-icd10-automatic.json` (#141 phase B1).
  Unscoped terminology synonyms now need their wording in every release.
- `minos data candidate-icd9` judges one archived CZS ICD-9 generation with its
  same-version NFZ workbook against the live bundle, using the B1 evaluation.
  The generation governs from its earliest changed procedure date, and
  backdating beyond the owner's 92-day bound is refused
  (`manifests/policy/czs-icd9-automatic.json`, #141 phase B2).
- Data-only activation (#141 phase C). A `would_activate` candidate must also
  keep the reviewed search gold and golden grouping answers (criterion 4,
  cases shipped as `/app/acceptance`), then the host runner activates it
  through `validation_host`'s new `stage-data` and the existing transition,
  with one retry, a public readiness check, rollback, an R2
  `deployment-receipts/` chain entry and a `data-activation` Kuma report.
  Only source classes in `/etc/minos/processor/activation.json` activate;
  the list starts empty. `deploy_validation plan` refuses a bundle that drops
  a release the live bundle serves, and the processor container's limit is
  512 MiB.
- `minos processor run` and `minos-processor.timer` (#141 phase B3): every six
  hours a hardened container from the live reference API image reads the
  watcher's public RSK and NFZ projections, fetches new inputs and the live
  ones from R2 with its own read-only key, judges each new input once with
  the B1/B2 candidates and reports `idle`, `would_activate <release>`,
  `rejected <system> <criterion>/<code>` or `failed: <step>` to a separate
  `data-update` Kuma monitor. It activates nothing and holds the deployment
  lock while it runs. `infra/ansible/processor.yml` installs it; the API image
  now carries the activation policies in `/app/policy`.
- `/api/v1/releases` marks each release's `valid_from`, `valid_to` and
  `in_force` and lists the release in force today first; a search may carry a
  `service_date` instead of a release and is answered from the release in force
  that day. Dated dictionary pages and grouper code suggestions follow the same
  rule (#141).
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
  rollback-safe activation. The watcher AWS Terraform and the disposable AWS
  acceptance harness added then were retired with the move to R2 (#200).
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

- Request-path query plans reviewed against planner statistics (#228): a
  copy of the production bundle with `ANALYZE` ran 948 request statements of
  84 shapes with no full scan of a large table on a per-request lookup, and
  request medians matched the bundle without statistics. The `INDEXED BY`
  pins on ICD-11 map lookups stay: with statistics they cost nothing, and
  without them, unpinned lookups take 2 to 3.5 ms instead of 0.01 ms, while
  application and bundle releases deploy independently. Details in
  `docs/validation/api-load.md`.
- Design re-check fixes (#255). The JGP date controls (catalog, group and
  comparison pages) are DD.MM.RRRR fields with the grouper's Polish calendar,
  now shared in `datepicker.js`, instead of a native date input that showed
  `10/26/2026` in an en-US browser; the catalog heading names the day. The
  grouper lists annex 4 discharge modes 4, 7, 8, 10 and 11 by short labels
  and shows the full official text under the select once chosen (a short
  label applies only while the official name starts as reviewed). The
  service day and reporting month are pills like the hospitalization mode and
  stay unselected until chosen, as `grouping-interface.md` records. The step
  bar has five columns for five steps, the `?` buttons are 24 px, Źródła
  shows Wyczyść and Resetuj filtry only when they would change something and
  its glossary is a plain definition list, Słowniki marks ICD-11 with a "Nie
  do rozliczeń NFZ" badge and links the JGP catalog, Gruper and sources, and
  the legal pages' body shares the heading's left edge.
- Tailnet Lock preparation (#258). The tailnet join stage accepts an auth key
  pre-signed with `tailscale lock sign` (up to 1024 bytes; a plain key was
  capped at 256), so the server can rejoin once the lock is on. The Ansible
  README documents enabling the lock with two owner signing nodes, storing
  the disablement secrets, signing existing devices, and the pre-signed
  rejoin, whose key wraps a signing key and is handled like one.
- Tailnet hardening after the "Tailworm" report (#253). The report spreads
  through policy-granted Tailscale SSH, which the server never ran
  (`--ssh=false`, OpenSSH only, no grant from the server). The policy now drops
  its unused `ssh` section, so Tailscale SSH is denied tailnet-wide, and `sshd`
  refuses Unix-socket forwarding (`AllowStreamLocalForwarding no`, asserted in
  the effective configuration) beside the TCP forwarding it already refused.
  The runbook names deleting the node in the admin console as the compromise
  response. On the running host the drop-in lands through the
  `ssh-tailnet.yml` private stage, not `site.yml`, which never runs over an
  active validation deployment.
- Repository security baseline: Dependabot watches the uv locks (root and
  standalone CLI), GitHub Actions and the digest-pinned base images instead of
  `pip`; `virtualenv` and the standalone `setuptools` move past their advisories;
  the JGP comparison link encodes each path segment (CodeQL `js/xss-through-dom`).
  SECURITY.md lists the enabled protections.
- Dependency updates: FastAPI 0.160.0, Pydantic 2.14.1, MCP 2.3.0, HTTPX2
  2.16.0, boto3 1.43.117 and the dev tools (Ruff 0.17.1, mypy 2.4.0,
  BasedPyright 1.40.2). FastAPI 0.160 stops validating omitted `Query`,
  `Header`, `Cookie` and `Form` defaults; Minos uses none of them (routes take
  path parameters or the raw request, and bodies go through Minos' own
  Pydantic models), so no validation moved. Ruff 0.17 also formats Python
  blocks in Markdown.
- Patch updates (#257): FastAPI 0.161.1, boto3 1.43.118, Ruff 0.17.2 and
  Hypothesis 6.170.8.
- Base images are pinned by tag and digest. An untagged digest made Dependabot
  propose `latest`: Debian trixie for the standalone CLI builder and
  Debian-based NGINX for the validation edge, where `apk` cannot run. The
  builder and its smoke OS move together to the current `bookworm-slim`, the
  edge to the current `1.31.7-alpine` rebuild. It still ships brotli-libs
  1.2.0-r1 and curl 8.22.0-r0 (CVE-2026-92392, high), so its layer pins
  brotli-libs 1.2.1-r0 and curl and libcurl 8.23.0-r0. Dependabot no longer proposes Python 3.15
  (`requires-python` is `<3.15`) or image-only uv bumps, which would drift from
  the `setup-uv` pins.
- The maintenance edge (the stock NGINX that `site.yml` runs before a
  validation deployment replaces it) moves from its 2026-09-18 Alpine 3.24.1
  digest, with eight fixable high findings and not scanned in CI, to the
  current `1.31.7-alpine` rebuild. That leaves brotli-libs and curl, which the
  static configuration loads no module for. It takes effect at the next
  maintenance `site.yml` run, which never runs over an active validation
  deployment; a later first cutover renders against the same digest.
- The validation API image moves to Python 3.14.9 (`3.14.9-slim-bookworm`), a
  patch release on the 3.14 line Dependabot may now propose (#256).
- The validation API image pins Debian's fixed OpenSSL 3.0.22-1~deb12u2
  (CVE-2026-84782, high) beside the PCRE2 and Perl fixes, because the pinned
  Python 3.14.7 bookworm base still ships 3.0.20 and the container gate refused
  it.
- Prepared the repository for public visibility (#244): the README states it
  holds no datasets or deployment credentials and how to run tests and a local
  site, a retired cloud account id is gone from two records, and the clean-path
  public load run (four-user search p95 214 ms, all objectives met) is recorded.
  NOTICE.md now credits ICD-11 PL (CC BY-ND 4.0) and the WHO ICD-10/ICD-11
  tables (CC BY-ND 3.0 IGO), Morfeusz2 with SGJP (BSD-2-Clause) and the vendored
  MiniSearch (MIT); it still said no WHO dataset was included.
- Cloudflare provider-retention audit re-read on 2026-10-25 (#28), read-only:
  no Logpush, retention control unavailable on Free, NEL, RUM and obfuscation
  off, Zaraz without tools and nothing injected into pages; the rollout record
  no longer says the zone was never audited.
- Reference bundles carry SQLite planner statistics (#228): the builder runs
  `ANALYZE` on the final rows before hashing, and two builds of the same inputs
  stay byte-identical. It applies from the next bundle build; the live bundle
  and the `INDEXED BY` pin on ICD-11 map lookups are unchanged until then, when
  request-path query plans get reviewed.
- Status documents agree on what is open (#238): the README and Ansible
  runbook no longer call the delivered watcher, status route, push delivery and
  browser verification pending, and name the two open gates (the empty-host
  recovery drill, #49, and launch-capacity load, #42); the decisions log no
  longer says empty-host recovery was tested.
- AWS and Terraform leftovers after the R2 move are gone (#239): the unused
  `mise.toml` pins for `aws-cli` and `terraform`, a CI-security check for a
  Terraform step no workflow has, and present-tense AWS, IAM and Terraform
  statements in this changelog and the archive, CI-budget and R2 documents;
  the R2 cutover reads as the completed 2026-10-19 record.
- SSH left the public internet on 2026-10-23 (#227). `infra/ansible/ssh-tailnet.yml`
  joins the host to a Tailscale tailnet in userspace mode with a one-time
  tagged key, proves a login over it, and only then makes `sshd` listen on
  loopback and removes the firewall's SSH rule, rolling back on any failure
  before that. The materializer derives `ssh_exposure` from the address and
  pins port 22 under the bare host name, as OpenSSH looks it up; `site.yml`
  keeps either state and never reopens SSH on a tailnet host. The tailnet
  policy is reviewed in `infra/tailscale/policy.hujson`. The site stays on the
  Cloudflare Tunnel. Both owner-approved stages ran: the provider IPv4 port
  now times out, `sshd` listens on loopback only and UFW allows no inbound
  traffic. The join stage's tmpfs staging now applies only to the remote auth
  key copy (the first run stopped on the workstation-side check), and recovery
  goes through Mikr.us M.A.R.I.A.N, not the panel's WebSSH.
- Load objectives adopted (#42, `docs/validation/api-load.md`): one user is
  the experience to protect (public p95: search 500 ms, pages and Gruper 1 s,
  MCP call 1.5 s), four users may slow down but stay correct (search, pages
  and Gruper 1.5 s, closed deadline answers at most 5% per route), eight
  users must shed cleanly. At any load: no edge-generated 5xx, transport
  failure or API 500. Public load runs now stop only on those, not on the
  API's closed `query_timeout`/`evaluation_timeout` answers.
- The terms of service (`/regulamin`, effective 23.10.2026) cover ICD-11 PL,
  JGP groups, the Gruper and the MCP server: their scope, anonymised input
  only, query limits, and the ICD-11 PL, WHO map and NFZ material rights.
- ICD-11 search ranks extension codes (chapter X) after stem codes, so
  "cholera" lists 1A00 before the vaccine and agent codes; they remain
  findable and exact code lookups are unchanged. ICD-11 blocks and windows
  are tree levels in the dictionary and on code pages, so chapter X shows its
  14 blocks instead of the first 400 of 2,767 codes (#53).
- `TODO.md` is retired: its open items moved to GitHub issues (#2, #3, #27,
  #28, #38, #41, #42, #49, #126, #141, #158 and the new #212, #213, #214),
  and `AGENTS.md` now keeps open work in issues.
- A dictionary listing marks every entry that has codes, however long the
  listing: one capped probe of the children's children left ICD-11 chapters
  25, 26, V and X without an expand arrow. The processor fetches live inputs
  up to the source bound (the 117 MB ICD-11 PL export failed every run with
  `failed: fetch` after the 7ddb4b0 release), and map URIs on code pages
  lost a stray separator line (#53).
- An RSK file with the live ICD-10 release's version and start whose nodes and
  relations parse identically is `release_content_unchanged`, and the
  processor reports that verdict as `idle`. The first production run held the
  `data-update` monitor red over RSK's content-identical `013` re-export
  (#184), which would hide the next real alert (#141).
- The data-update processor's host runner parses on the host's Python 3.12:
  Ruff had rewritten one `except` clause into the unparenthesized 3.14 form,
  so the first production run stopped with a `SyntaxError`. The runner now has
  the `py312` Ruff target and the host-grammar test of the other host scripts.
- A CZS generation whose effective day is not after the live generation's
  start is refused as `effective_day_not_after_live_release`, apart from an
  older generation number (`not_newer_than_live_release`), so the alert names
  the check (#141).
- certifi 2026.10.19 in `uv.lock` (was 2026.7.22) for PYSEC-2026-4320, which
  `pip-audit` reported on 2026-10-21.
- virtualenv 21.14.12 in `uv.lock` (was 21.7.13, a pre-commit development
  dependency) for CVE-2026-113722, -113725, -113726, -113732 and -113776,
  which `pip-audit` reported on 2026-10-22; packaging and python-discovery
  moved with it.
- The reference database bound is 1 GiB (was 512 MiB), and the search
  attribution budget is per release, so terminology history can accumulate.
  A 915.9 MiB 10-source bundle kept the API at 264 MiB anonymous memory under
  its 1 CPU/512 MiB limits (#141).

- The Kuma login and admin paths on `status.minoshealth.pl` now require
  Cloudflare Access for the owner. The tunnel route covered the whole host, so
  `/dashboard` and `/socket.io` had been reachable from the internet behind the
  Kuma password and 2FA; the public status page is unchanged.
- Watcher down pushes to Uptime Kuma now read `failed: <category>`, one of the
  fixed run failure classes such as `source_drift` or `archive_failure`, so the
  Telegram alert tells upstream drift from storage or network failure. The
  client refuses any other text before sending (#141 phase A).
- The owner resumed unattended data activation (#141, 2026-10-20): host-side,
  data-only, ICD-10/ICD-9 first and JGP after shadow evidence. It is not
  enabled yet; see [the decision](docs/v0.0.1/decisions.md#unattended-data-activation-resumed-2026-10-20).
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
  and their tests are removed; CI no longer installs Terraform. R2 checks bucket
  lock rules before `If-None-Match`, so every create-once writer now treats
  `409 ObjectLockedByBucketPolicy` for an existing key like `412` and verifies
  the stored SHA-256 and size; before this, a watcher run re-archiving unchanged
  metadata would have failed. The watcher moved to R2 at `36402de` on 2026-10-19
  and Minos's AWS resources were retired on 2026-10-20.
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
  tiff 4.7.2-r0, so the extra tiff layer is gone. FastAPI 0.143.2 is included.
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
  AWS then provided only versioned source storage, narrow IAM, budgets, and
  Terraform state, all retired for Cloudflare R2 in #200.
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

### Fixed

- MCP `get_code` and `browse_codes` answer with the requested ICD-9 code
  (#242). They reused the CZS concept's node number in the tree, which for
  ICD-9 browses the NFZ workbook's hierarchy with other numbers, so
  `get_code(53.03)` returned the title, path and children of 53.8 beside 53.03's
  rank and JGP lists. They now resolve the code in the browsed hierarchy, as the
  code pages do. On the production bundle all 58,122 ICD-9, ICD-10 and ICD-11
  codes resolve to themselves. Present since the MCP server shipped (#216).
- The local and CI Compose watcher health check allows 30 s like production
  instead of 5 s; the check takes about 3 s on a workstation and intermittently
  timed out on slower CI runners.
- The validation edge pins Alpine `brotli-libs` 1.2.1-r0 for CVE-2026-111497
  (HIGH): the newest upstream NGINX 1.31.7 digest still ships 1.2.0-r1, so a
  package layer bridges until upstream rebuilds. The development dependency
  `filelock` moves to 4.2.4 for CVE-2026-112907, -112913 and -112940; it ships
  in no image.
- Small source and test cleanups (#237, #240): the XML streaming test now
  checks the validator's element cap (accepted at the cap, refused one above)
  instead of asserting nothing; two stale comments match the code (activation
  outcomes are always pushed, and schema 7 answers carry the reviewed MODTAR
  binding); `SearchResult.search_version` defaults to the current version; an
  unreachable page label and an unused receipt pattern are gone.
- The filter, conditions, JGP search and search-recovery browser harnesses read
  the Chromium DevTools port through the shared `waitForDevToolsPort` (#236),
  which refuses an empty or partial port line; they still read it themselves,
  so the race behind hosted run 37361709208 stayed open there.
- The source catalog badges BAW orders `BAW NFZ` instead of `NFZ` (#234), so
  they no longer look like the NFZ / CZS / AOTMiT publication cards; the
  no-JavaScript pages already named the three apart.
- Host reference measurement cleanup no longer depends on a `docker ps` that can
  fail (#233): it removes the owned container by name and then its scratch
  directory, never raising, so neither stays behind and a finished
  measurement is not discarded.
- A failed or OOM-killed API start keeps its evidence when reading it outlasts
  the startup deadline (#232); the handler replaced the whole record with a
  generic timeout, dropping the #198 diagnostics exactly when they mattered.
- `scripts/expire_kuma_ssh_setup.py` reloads SSH even when the temporary
  forwarding block is already gone (#231). A failed reload after rewriting the
  file left `sshd` forwarding, and the retry reported "Already absent" without
  reloading. The Kuma tunnel snippet now uses the tailnet address (#227).
- The data processor's cap of two activation attempts holds per database
  (#230): it rewrote the attempt file with only the database it was trying, and
  cleared the whole file on success, so a second database gave the first one
  fresh attempts. Latent until unattended activation is enabled (#141).
- Releases of one system that start on the same Polish day resolve to the
  latest (owner decision 2026-10-25, #235): later validity instant, then source
  creation, fetch time and release identity. Search refused that day while the
  dictionary pages, MCP tools, search page and Gruper each guessed differently;
  all now use the one rule, MCP calls it directly and the Gruper drops its CZS
  override. Latent: current manifests have no shared start day.
- API logs no longer carry MCP tool arguments (#220). The MCP SDK called
  `logging.basicConfig` on startup, so the in-process client's request lines,
  whose URLs hold codes from `get_code`, `browse_codes` and the JGP tools,
  reached stdout, and every Minos log line appeared twice. The server now
  restores the root logger, `httpx`/`httpcore` log at WARNING only, and `/mcp`
  requests log under their own route name. Query text and encounter data were
  never logged; they travel in POST bodies.
- Intermittent API activation failures (#198): each release in the JGP
  comparison preload ran under the one-second request budget, nested inside
  a 5 s total, and the one-CPU host crossed it while the previous API colour
  was still running (`QueryTimeoutError`, exit code 3). Raising only the total
  to 30 s (#223) did not help, and the next activation failed the same way;
  now each release shares the 30 s preload deadline, still failing closed
  before readiness. Memory was ruled out: limits down to 288 MiB start
  normally and 256 MiB ends in an OOM kill, not exit code 3.
- Search no longer answers with deadline timeouts under concurrent load
  (#225). The one CPU was full of work that depends only on the immutable
  bundle, redone per request: serialising the dataset manifest for every log
  line, rebuilding every JGP group's selector reachability for each code page,
  re-reading search membership counts, and an ICD-11 map lookup that scanned
  half its table because bundles carry no planner statistics (#228). The API's
  verified pool now caches them, and a test compares the cached JGP lists with
  the full query for every listed code. A browser-and-MCP cycle takes 75 ms of
  CPU instead of 129 ms; at a host-like 0.25 CPU, four users see search p95
  203 ms instead of 1,687 ms and no deadline answers, eight users none instead
  of 7 of 110.
- Gruper cases no longer pile up at the 3 s deadline under concurrent load
  (#222). Every request revalidated and reconciled the whole MODTAR modifier
  dictionary for each valid group, 85% of its CPU, although startup already
  validates the immutable bundle. The grouper now keeps one validated
  reference per MODTAR and catalog release, guarded against any other
  binding: a warm case takes 8.8 ms instead of 37.5 ms, and at a host-like
  0.25 CPU four users see Gruper p95 598 ms instead of 2,317 ms and MCP
  `group_case` 501 ms instead of 3,000 ms. Search deadline answers under
  contention remain (#225).
- `opentelemetry-api` 1.45.1 fixes CVE-2026-114603 in a FastAPI and MCP
  dependency; the API image runs with the OpenTelemetry SDK disabled.
