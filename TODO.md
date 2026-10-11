# TODO

Execution backlog lives in [GitHub issues](https://github.com/krzysztofbury/minos-health/issues)
(milestones M0-M7, numbered tasks with step-by-step descriptions and
estimates). This file remains the spec-level checklist; issues carry the
day-to-day order.

## Reconciled Delivery Status (2026-10-11)

Latest verified validation deployment: #164, revision `bc4e9ef`, activated
2026-10-11T18:06:56Z with the schema-10 bundle. [Release evidence](docs/validation/jgp-comparison.md#successful-164-activation)
records exact identities, public API/browser/privacy checks and startup results.
This is a dated deployment record, not a new production inspection.

- [x] Deliver the supported grouper subset, reviewed MODTAR binding, trace,
      CLI/HTTP grouping and encounter builder (#34-#37, #39, #46, #129).
- [x] Deliver settlement-product classification/exclusions and official builder
      dictionaries (#145, #151), plus warm-up logging and dictionary caching (#155).
- [x] Fix the observed startup warm-up timeout (#157). Bounded local recovery
      and actual-host retry passed; #164 is now successfully activated, with
      complete warm-up and first current/older-act golden requests passing. See
      [warm-up evidence](docs/validation/grouping-warmup.md).
- [x] Deliver pinned JGP catalog comparisons (#104), verified through the public
      UI with source-backed values, shared URLs and desktop/mobile acceptance.
- [ ] Obtain practitioner entry-time and explanation acceptance (#158, carried
      from closed #39). Real ward outcome agreement remains separate in #38.
- [ ] Complete search relevance/workflow and capacity evidence (#126, #25, #42).
- [ ] Deliver the remaining reference features: JSON concept detail (#23),
      NFZ publication metadata (#92), and source-page
      discovery/citation improvements (#80). Historical downloads remain
      rights-gated (#84).
- [ ] Preserve deployment/rollback diagnostics and reliable edge restoration
      (#162); the first #164 activation also lost its failure diagnostics.
- [ ] Complete hosted archive-reader/build evidence (#19), watcher recovery/AWS
      acceptance (#49), Cloudflare privacy audit (#28), signed releases (#41),
      full deployment acceptance (#27), and external brand/commercial gates (#2/#3).

Deferred work remains #141 (unattended activation), #95 (assisted case entry),
#53 (ICD-11) and #54 (private watcher portal). Completed prerequisites do not
automatically authorize those features. Unsupported grouping rules remain
undetermined; the deployed subset and golden cases do not establish ward accuracy.

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
- [x] Self-host Uptime Kuma 2.5.5 on the same Mikr.us host for coarse public
      status and watcher completion notifications, without paid external
      monitoring SaaS. Accept for now that this cannot detect or notify for a
      total Mikr.us host, network, or power outage.
- [x] Deploy tested image digests from signed version tags without rebuilding.
- [x] Use Python 3.14 as the sole application language through 2027-08-21;
      keep synchronous domain rules independent of FastAPI and persistence.
- [x] Make security, correctness, and measured performance co-equal release
      requirements; require evidence-based tradeoffs and representative
      benchmarks, with developer convenience following; require unit, targeted
      mutation or fault injection, integration, and pair-programmer review gates
      for implementation chunks.

## Owner-Ordered Validation Work (2026-10-06, reconciled 2026-10-11)

#20/#31 merged in #139 and deployed at `1d1e8b4` on 2026-10-05. The owner
accepted the real ICD-9 Markdown diff. On 2026-10-06 the owner reprioritized
client validation over unattended-release infrastructure and requested merging
only the useful completed subset of #140.

Completed in #142 at `902496d` without a separate deployment: retain #19's
exact-version S3 build adapter, focused real-S3 evidence and scoped acceptance
repairs, plus Chromium startup reliability.
The merged smaller branch was `feat/validation-archive-build`. The existing artifact
manifest is unchanged; transport pins stay separate. #19 remains open for
hosted complete real-source determinism and workflow-role acceptance, not more
parser work. See [archive processing](docs/v0.0.1/archive-processing.md).

Delivery and remaining acceptance of the owner-ordered phases:

1. [ ] #126 and #25/#42: strengthen relevance safety and measure actual
       user-visible performance before further search expansion. Preserve the
       unchanged gold sets, obtain practitioner and independent held-out review,
       record hosted evaluation, and measure complete user workflows and
       representative capacity rather than inferring them from quiet probes.
       PR #143 records the unchanged hosted 37/44 top-1, 42/44 top-5 and 12/12
       JGP results, separate 14-case source-based safety observations, and local
       packaged browser workflows. All 40 scripted discoveries complete in both
       220/120/150 ms single-user runs. The rejected 150 ms experiment improves
       suggestion medians by about 70 ms but still increases slower-typing
       suggestion requests 18.4x. The repeated 150 ms capacity run fails:
       9/160 searches have failed sources and 4/160
       target discoveries fail, versus complete 220 ms final results. The owner
       then authorized restoring 220 ms; #143 merged and deployed at `aedb781`
       on 2026-10-06 after final runtime/CI/preflight checks. Do not deploy 150 ms
       or treat passing CI as capacity acceptance. Clinical,
       independently held-out, incumbent and multi-user/actual-host capacity
       acceptance remain open. See
       [search workflow evidence](docs/validation/search-workflow.md).
2. [x] #34-#36: deliver the supported explainable grouper with deterministic
       encounter validation and source-backed traces. #37 supplies reviewed
       synthetic and pinned-source golden cases, not practitioner outcome proof.
       #46/#129 were resolved under recorded owner assumptions in #146; #156
       completes trace/product/golden-case delivery. Unbound modifiers still
       cannot be interpreted as no modifiers or base points as final tariffs.

Each numbered phase gets its own branch, coherent commits and PR. Complete and
review/merge its PR before starting the next phase; run its relevant runtime,
integration, fault and representative-performance gates before deployment.
Update this checklist and its evidence before closing delivered issues. Do not
combine the phases into one branch or weaken blocked acceptance to clear an issue.

Deferred: #21 is closed as superseded, not delivered. Unattended
processing/activation and its authority, OCI, signing,
journal and detailed-notification work are parked in
[#141](https://github.com/krzysztofbury/minos-health/issues/141). Preserve #140
and `feat/archive-application-freshness`; do not merge unused foundations.
Continue reviewed validation updates. Automatic activation is not a prerequisite
for the phases above. #41's signed-release obligation and all source, privacy,
applicability and last-good rollback requirements remain intact.

The owner-applied GitHub OIDC subject customization passed the non-AWS hosted
[attempt-2 rehearsal](https://github.com/krzysztofbury/minos-health/actions/runs/37372958134/attempts/2).
This is observed claim shape, not AWS trust or activation acceptance. The old
archive-reader IAM subject was not repaired; future live changes require an
exact reviewed plan. No new IAM/KMS/host changes or activation are approved.
Dictionary and embedding experiments remain separate and use unchanged gold sets.

## Reference Delivery Sequence (2026-10-02, reconciled 2026-10-11)

Free ICD-10 PL, ICD-9 PL and JGP search, dated reference pages and the supported
explainable grouper are live under the public-validation exception. The sequence
below records their delivery; remaining practitioner and release gates stay explicit.

1. [x] #33: procedure ranks per ruleset release, shown on ICD-9 code pages
       (#106, deployed to the validation host 2026-10-02).
2. [x] #17, a minimal #18, then #24 and #26: JGP group pages with points per
       settlement mode, selectable by service date. These are catalog base
       points without modifiers, labelled as such in API and UI. #18 retains
       bitemporal provenance, explicit uncovered-date errors and per-provision
       base-point applicability, including 102/2026 section 4(5)'s deferred
       2027-01-01 catalog rows. Its initial modifier state was explicitly unbound;
       #46 subsequently delivered reviewed MODTAR binding and the supported
       section 17 applicability under the 2026-10-07 owner assumptions.
       #26 first delivers shareable date-specific group pages with the governing
       act/catalog identity, not diffs or condition-based ICD cross-links.
       Delivered as /jgp (#118, deployed to the validation host 2026-10-03).
3. [x] #29 in parallel with step 2, since it is the schedule risk; then #30
       and #32 for the "występuje w grupach" reverse lookup from any ICD code.
       #29 done: [condition taxonomy](docs/v0.0.1/jgp-conditions.md), reviewed
       2026-10-03 (#121). #30 done: [source-traced condition import](docs/v0.0.1/jgp-conditions-ingest.md)
       (#122, deployed to the validation host 2026-10-03; the public bundle
        stayed schema 4 until #32). #32/#131 delivered dated conditions and reverse
        links; the latest verified public bundle uses schema 10. Unsupported
        fragments remain explicit reference evidence, not executable defaults.
4. [x] #34-#36 grouper engine and trace with #37 golden cases, #46 reviewed
       MODTAR binding and #145 product exclusions. #35 rejects unbound modifiers.
       The [binding record](docs/v0.0.1/jgp-modifiers-ingest.md) identifies the
       accepted assumptions; #129 closure does not represent an NFZ answer.
5. [ ] External: obtain practitioner builder feedback (#158) and independently
       reviewed de-identified outcome cases when available (#38). Closed #4 did
       not supply verified encounters; #37's derived cases are a separate result.

ICD-11 (#53) stays deferred: it is not an NFZ settlement input, adds a
licensing review, and does not advance JGP validation.

See [JGP delivery scope](docs/v0.0.1/jgp-delivery-scope.md) for the acceptance
boundaries, deferred diff ownership and UI preservation gate. Implementation
PRs require owner review before merge; the #102 shared layout and search-first
UI remain the baseline.

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

- [x] Deliver the accepted order `#49 -> #6 -> #8 -> #7`. #6, #7, and #8 are
      closed; #49 stays open only for its external acceptance gates below.
- [x] Deliver #49 control state first: numbered SQLite migrations, complete
      migration checksums, one writer, fenced claims, legal run transitions,
      durable due cursors, observations, blob references, incidents, audit
      events, and monotonic state revisions.
- [x] Deliver #49 archive storage implementation: equivalent local and S3
      content-addressed blob stores, bounded single-part conditional writes, and
      native checksum and byte-count verification.
- [x] Deliver #49 recovery and scheduling next: verified SQLite recovery images,
      bounded paginated restore selection, operator-trusted images and signed
      inventories, APScheduler reconstruction, one bounded catch-up, stale-run
      expiry, and pending-run reconciliation.
- [x] Finish #49 repository production integration: add the pinned watcher service to the
      Mikr.us Compose topology, service-restricted secrets and volumes,
      scheduler-paused preflight, read-only watcher status, bounded local logs,
      ordinary-loss restore, and suspected-compromise restore tooling.
- [ ] Execute the remaining #49 external acceptance: broad disposable real-AWS
      watcher/refusal tests, both empty-host restore drills, confirmed budget
      alert delivery and representative host overlap/load evidence. Production
      provisioning, secrets, preflight and the RSK/BAW/NFZ watchers are delivered;
      #142's focused S3 build cases do not complete the broader acceptance.
- [x] Add the issue #6 private capture gate: fixed anonymous endpoints, bounded
      exact-byte staging, semantic `00CD10` XML selection, rotation quarantine,
      schema-shaped synthetic fixtures, and an opt-in live compatibility check.
- [x] Run and review the dated live RSK compatibility capture under ignored
      `data/`, including all returned metadata and the allowlisted XML.
- [x] Finish watcher integration for the catalog and every returned `basic-info`
      resource after this capture chunk.
- [x] Store exact catalog and metadata response bytes and complete observations
      for every HCD. Compare full structures; use `uuid + creationDate +
      sizeBytes` only as a cheap change trigger.
- [x] Archive only the allowlisted `00CD10` XML initially. Stream from byte zero,
      compute SHA-256, verify declared and received lengths, reject HTML or wrong
      magic, re-fetch metadata after the batch, and quarantine rotated or
      ambiguous transfers.
- [x] Add issue #6 daily, weekly forced re-download/re-hash, and manual modes;
      prove interruption, retry, metadata rotation, same-metadata changed bytes,
      stale-worker, transaction failure, and duplicate-notification behavior.
- [x] Register the RSK collector in filesystem and S3 runtime composition and
      add explicit atomic RSK bootstrap with independent durable daily and
      weekly verification cursors, conflict preservation, and bounded catch-up.
- [x] Publish bounded, canonical, PII-sanitized RSK provenance through immutable
      local generations and an atomic revision fence; mount only that root into
      the edge, expose the exact provenance endpoint, and keep redistribution
      deny-all while the legal digest allowlist is empty.
- [x] Build the accepted Sources HTML, CSS, and JavaScript UI over the public
      provenance endpoint. This remains separate from the publication boundary.
- [x] Complete issue #6 with the atomic #49 observation transaction, exact
      revisioned recovery image, durable local finalization, documented local
      and production Compose status and log commands, and the first forced
      Mikr.us observation of all HCD metadata plus the current `00CD10` XML.
- [ ] Observe one real weekly forced RSK re-download and re-hash of an unchanged
      `00CD10` file in production (carried from #6 into #49).

- [x] Scheduled watcher: archive the RSK catalog and metadata for every returned
      HCD; alert on set/schema drift; download only allowlisted product files
      (initially `00CD10` XML), compare complete metadata, and compute SHA-256.
- [x] Scheduled watcher: NFZ DSOZ listing, general Centrala provider news,
      Komunikaty JGP, CZS ICD-9 and MODTAR metadata/pinned XML, projects, and
      AOTMiT tariff signals. Use official machine interfaces where available,
      not HTML only.
- [x] BAW anonymous-API fetcher: archive source, amendment, and consolidated
      document graphs, root ZIPX packages, selected attachments, all relevant
      dates, and checksums. Browser automation requires a separate task and
      captured evidence that it succeeds from the Mikr.us environment.
- [x] Bootstrap the dedicated versioned Terraform state bucket locally and back
      up bootstrap state independently.
- [x] Initialize the `watcher` root directly against its own remote-state key.
- [x] Provision the watcher private versioned S3 snapshot bucket, narrowly
      scoped Mikr.us watcher identity, GitHub OIDC read role, and budget in
      remote Terraform state before the first production run. Enforce
      conditional creates and deny watcher overwrite and deletion.
- [ ] Resolve the SNS budget subscription inconsistency (list returned
      `Deleted`, direct read returned confirmed). Do not claim budget email
      alerts work until a delivered alert is observed (#49).
- [x] Define the raw-SQL watcher control schema for sources, schedules, runs,
      observations, blobs, source cursors, monotonic state revisions, changes,
      incidents, and audit events. Keep the canonical `schema.sql` synchronized
      with numbered checksum-verified migrations and tested down scripts.
- [x] Implement the single-writer control transactions for configuration and
      schedule changes, fenced claims, legal run transitions, atomic observation
      commits, state revision increments, incidents, and audit events.
- [x] Implement bounded mode-0600 blob staging plus equivalent local and S3
      content-addressed adapters with conditional-create, verified-reuse, native
      SHA-256, byte-count, conflict, timeout, and corruption unit coverage.
- [ ] Prove S3 versioning, native checksum, conditional-create, IAM denial,
      lifecycle, and bounded listing semantics against a disposable AWS stack.
- [x] At the watcher orchestration boundary, turn archive verification failures
      into hard incidents and failed claims, and prove observations and source
      cursors remain unchanged.
- [x] Run APScheduler `3.11.x` as one UTC `BlockingScheduler` worker with
      bounded misfire handling. Reconcile committed and `heartbeat_pending` runs
      before dispatch and use durable due cursors plus token-and-epoch fenced
      claims for catch-up, manual, expiry, and stale-worker rejection.
- [x] Run issue #6 through one Docker/Compose watcher command locally and on
      Mikr.us; use local filesystem blobs in development and S3 in production,
      with shared contract tests. The disposable AWS test is tracked above.
- [x] Stage at most one download in a disk-backed `noexec,nosuid,nodev`
      directory with a `600 MiB` application quota, `512 MiB` per-file ceiling,
      startup cleanup, and pre-run free-space assertion.
- [x] Archive every selected raw file immediately under a digest-only
      `blobs/sha256/<digest>` key. Use bounded single-part `PutObject`; send and
      verify S3 native SHA-256 plus byte count after conditional create or reuse.
      Source identity stays in SQLite.
- [x] Create revisioned control SQLite recovery images through
      `sqlite3.Connection.backup()`, enforce class-specific lifecycle rules,
      copy one verified image weekly to operator-only trusted storage, sign the
      non-expiring chained inventory off-host, with ordinary-loss and
      suspected-compromise restore commands.
- [ ] Execute ordinary-loss and suspected-compromise empty-host restore drills.
- [ ] Deferred in #141 (#21 closed as superseded): automatic verified change
      selection through scoped OIDC,
      pinned proposals/logical diffs and policy-qualified routine data activation.
      Failed or significant candidates retain/restore the last good release and
      send detailed privacy-safe Telegram alerts. Never give the watcher a GitHub
      token; only the trusted promotion boundary may merge eligible data changes.
- [ ] Alert on watcher failure and on upstream schema drift (new załącznik
      names, changed file formats).
- [x] Bootstrap Uptime Kuma privately on Mikr.us from the pinned rootless image,
      with no host port or public route. Create the admin and 2FA, HTTP monitor,
      paused RSK push monitor, and status page domain mapping. Remove the
      temporary restricted SSH forwarding exception after setup.
- [x] Stop Uptime Kuma and save its complete `/app/data` tree as an encrypted
      off-host 1Password Document. Read it back, check the image pin, and run a
      SQLite integrity check before restarting the container.
- [x] Prepare and read back the watcher-only RSK Push target document in
      1Password from the rotated Kuma token; keep it off the host until the
      watcher deployment.
- [x] Restore the stopped 1Password Kuma backup to a disposable isolated Docker
      volume and start the same pinned rootless image as UID `10003`. Confirm
      health and the status page, then remove the test volume and container.
- [ ] Include restored Kuma state in a complete empty-host Mikr.us recovery drill
      before an image upgrade; never copy its live SQLite database.
- [x] Configure a dedicated Telegram bot and test delivery through Kuma. Attach
      the notification to the public-edge HTTP monitor and save a new stopped
      1Password backup containing the verified notification configuration.
- [ ] Install the mode-0600 watcher target document, activate required monitoring
      with the watcher release, configure public DNS and tunnel routing, then
      visually verify `status.minoshealth.pl` on mobile and desktop.
- [ ] Keep issue #54 as the future private detailed admin portal. The public
      Uptime Kuma page exposes coarse service state only.

## Repository Baseline

- [x] Initialize `pyproject.toml`, `uv.lock`, and the `src/minos` package with
      domain, store, ingest, CLI, and HTTP modules; add grouper in `v0.0.2`.
- [x] Establish `apps/web`, `database/reference/sqlite/{migrations,queries}`,
      `tests/{unit,integration,compatibility,fixtures,golden}`, `manifests`,
      `infra`, and `scripts` boundaries; do not commit empty future components.
- [x] Implement raw numbered reference SQLite migrations
      (`database/reference/sqlite/migrations`) with fresh-database build tests.
      The runtime only validates and opens the reference artifact read-only.
- [x] Real #11-#13 artifact gates replacing #10's disposable spike: pin runtime
      identity and verify FTS5, read-only operation, determinism and measured
      build/query behavior (#94, #96).
- [x] Add Ruff, BasedPyright, mypy, pytest, dependency policy, and package
      metadata (#15).
- [ ] Select an automated mutation runner only after proving Python 3.14
      compatibility and bounded runtime.
- [x] Add small committed XML fixtures and code-generated XLSX fixtures
      (`tests/fixtures/icd9_workbook.py`) outside the ignored `/data` directory.
- [x] Add code-generated DOCX and XLSX komunikat fixtures
      (`tests/fixtures/komunikat.py`, #33).
- [x] Add checked-in dataset manifests containing source identities and
      checksums (`manifests/sources`).
- [ ] Add architecture decision records if implementation changes accepted
      decisions in `docs/v0.0.1/decisions.md`.

## Data Ingestion

### ICD-10 Reference Foundation (#11-#13)

- [x] Inspect checksum-verified archived RSK XML and record its actual node,
      relative-code, modifier and description semantics.
- [x] Add synthetic schema-faithful fixtures, bounded streaming and typed
      source-preserving records without redistributing licensed source bytes.
- [x] Build a generic release-scoped hierarchy linked to concepts/descriptions,
      with transactional integrity, FTS5, deterministic bytes, provenance,
      checksums, read-only opening and bounded recursive CTEs.
- [x] Exercise the full archived source and installed wheel; record local
      artifact and query measurements plus targeted failure evidence.
- [x] Complete #14 product search precedence and standalone packaging, #15
      private full-source CI and container search/runtime acceptance (#96). The
      #10 disposable spike is superseded, not completed or waived.
- [x] Ingest canonical NFZ CZS ICD-9 PL with the separately attributed NFZ
      workbook as supporting context (#97, #101).
- [x] Publish the free, noncommercial ICD-10 PL and ICD-9 PL validation search,
      API, dictionary trees, and code pages under the owner's validation
      exception (#98-#102, `docs/validation/reference-web-rollout.md`).
- [x] Replace manual validation updates with one guarded command and artifact
      retention (#109, #111, `docs/validation/validation-deploy.md`; first tool
      update 900e464 on 2026-10-02).

### Remaining Dataset And Product Gates

- [x] Download and fixture the RSK ICD-10 XML (HCD `00CD10`) and the NFZ ICD-9
      PL XML/XLSX.
- [x] Fixture CZS MODTAR XML (#46). Załącznik 9 fixtures landed with #30
      (synthetic in-repo, pinned private captures opt-in). The watchers already archive the source bytes. Komunikat algorithm
      and rank fixtures landed with #33; załącznik 1a catalog fixtures with #17.
- [x] Record source identity for every archived file: URL or UUID, byte size,
      computed SHA-256, upstream timestamps, publisher, and disclaimers, without
      inventing publication dates.
- [x] Define a generic release-scoped source-node/relation graph linked to
      concepts and multilingual descriptions, preserving uncoded containers,
      annotations and modifiers without inventing diagnosis codes.
- [x] Define terminology, concept, description, and source-release tables.
- [x] Define group, base-tariff and ruleset-release tables with bitemporal
      fields and per-provision applicability (#18, including the 102/2026
      deferred catalog rows); conditions landed in #30 and modifiers/binding in
      #46. Schema 10 additionally stores product catalogs. A bundle without a
      compatible reviewed modifier binding explicitly remains unbound.
- [x] Implement streaming XML parsing with explicit validation errors; reject
      DTDs and external entities; bound depth, value size, record count,
      source bytes, and parser resource use.
- [x] Implement hardened XLSX parsing with zip entry-count, expansion-ratio,
      and size limits for the ICD-9 workbook.
- [x] Extend it to the merged-cell multi-row headers of the JGP catalog with
      explicit layout-version markers (#17).
- [x] Implement komunikat DOCX extraction for algorithm metadata (#33); the
      algorithm steps themselves are implemented as versioned engine code with
      fixture-backed tests.
- [x] Curated overlay dataset for the reviewed rules living in legal prose (correction
      coefficients, coefficient-combination formula) with `manual` provenance
      and mandatory review; reconcile it against CZS MODTAR mappings (#46/#129).
      The owner-approved scope excludes the food-standard ratio and discloses
      dictionary disagreements; this is not comprehensive legal clearance.
- [x] Reject duplicate codes, broken parents, missing descriptions, and unknown
      structural elements that affect meaning in terminology sources.
- [x] Reject unresolved JGP list references (#30): persisted selectors always
      resolve; an unresolvable reference is unsupported evidence, never a
      dangling selector.
- [x] Build the database transactionally and reproducibly; emit a
      machine-readable artifact manifest and database checksum.
- [x] Implement human-readable logical release diffs (#20) for explicit verified-
      bundle terminology, catalog and conditions pairs. Real 5.89 -> 5.90 golden
      test, paired review and full checks pass; owner authorized merge/deployment
      on 2026-10-05. Close the delivering issue only after remote merge and guarded
      activation receipts. Two-minute human usability is not a measured result.
- [x] Validate complete-dataset counts and hierarchy integrity for terminology.
- [x] Validate JGP logical comparison across versions (#31); within one release
      integrity is enforced by #30. Cross-version graphs/list populations and
      evidence bindings have synthetic regression and private 74 -> 102 gold
      tests, plus the actionable new-base-act checklist. Review passed; owner
      authorized merge/deployment on 2026-10-05. Require merge/activation receipts
      before closing the issue; this is not legal or practitioner qualification.

## Domain And Search

- [x] Define namespaced terminology and release identifiers.
- [x] Define grouping-scheme identifiers (`jgp-szp`) with the first JGP data.
- [x] Implement Unicode-aware Polish normalization and morphology without
      changing source text.
- [x] Implement exact code and exact normalized-description matches.
- [x] Implement indexed prefix/token search and bounded substring fallback.
- [x] Define deterministic rank and tie-break rules in tests.
- [x] Service-date resolution: given a date, select the governing ruleset
      release; error explicitly when no release covers the date.

## Grouper (v0.0.2)

- [x] Implement the supported `minos.grouper` subset: encounter facts against a
      pinned ruleset; rules as data, engine as versioned code (#34/#35).
- [x] Trace every consulted condition and outcome; report bounded near misses
      only when whole-group reevaluation changes the result (#36).
- [x] Deliver 14 reviewed golden cases: 9 synthetic and 5 derived from pinned
      sources, with evidence levels and divergence dispositions (#37).
- [ ] Cross-check complete, source-matched cases against independently available
      grouper outputs where access permits; record tool/version, missing evidence
      and divergences. The initial suite does not establish this comparison.
- [x] Bind governing-act product catalogs and apply reviewed W exclusions and
      duration rules (#145). No pinned act has type V; unknowns remain unresolved.
- [ ] Obtain independent real ward outcome validation (#38). Public/synthetic
      examples and third-party comparisons do not establish NFZ agreement.
- [x] `POST /api/v1/group` and `minos group`: stateless bounded evaluation;
      encounter contents excluded from logs and persistent storage (#39).
- [ ] Verify practitioner entry-time and explanation acceptance (#158).
- [x] Resolve startup warm-up timeout under representative limits (#157).

## CLI

- [x] Add `minos data build`.
- [ ] Add dataset inspection commands.
- [x] Add `minos search` with explicit system and dataset selection.
- [x] Add dated JGP lookup as `minos jgp group --code <group>` and
      `minos jgp modifiers`, both with `--service-date` and `--known-on`.
- [x] Add human-readable output, stable exit codes, and versioned JSON output.
- [x] Include dataset provenance and the reference-only disclaimer in output.
- [x] Verify lookup requires no network access after a dataset is built.
- [x] Define checksummed standalone CLI and data-bundle artifacts
      (`scripts/build_standalone_cli.sh`).
- [ ] Promote them from `main` to a signed release without rebuilding (#41).

## HTTP And Web

- [x] Add `/healthz`, `/readyz`, and bounded `POST /api/v1/search` (#22).
- [ ] Add the remaining JSON concept-detail contract (#23); explicitly decide
      terminology release/date semantics and keep query values out of URLs.
- [x] Add dated JGP group endpoints (#24), extended with conditions in #32.
- [x] Add versioned response schemas and consistent error mapping.
- [x] Build the responsive terminology search, dictionary trees, and code pages
      under `apps/web`; embed its immutable output in the edge image and route
      API paths to `minos.http` under the same origin (#102).
- [x] Add the shareable service-date JGP group browser with labelled catalog
      base points and governing act/catalog provenance (#26).
- [x] Add JGP version diff views in #104 after #20 and the first #26 delivery.
- [x] Display code hierarchy, source release, and the safety/compliance
      disclaimer.
- [x] Display grouping conditions and point values (#26, #32).
- [x] Exclude raw query text from logs and analytics.
- [x] Extend the log-privacy test to encounter payloads (#39); public Tunnel
      and running-container log checks are recorded in #156's release evidence.
- [x] Add input, result-count, concurrency, and timeout limits.

## Container And Local Runtime

- [x] Add a multi-stage non-root `linux/amd64` Dockerfile.
- [x] Add a separate immutable watcher image or Dockerfile target used by both
      local and Mikr.us Compose; keep watchers out of the API runtime image.
- [ ] Embed provenance and the read-only generated database in the API image;
      build static assets as a separate checksummed artifact.
- [x] Run with a read-only filesystem and no unnecessary Linux capabilities.
- [x] Compose Cloudflare Tunnel, edge, API, and watcher services; the edge
      serves static assets and proxies API paths under the same origin
      (`scripts/render_validation_compose.py` for the validation stack).
- [x] Exercise one real search in the container smoke test.

## CI And Release

- [x] Add PR checks for Ruff formatting/linting, BasedPyright, mypy, pytest,
      fixture ingestion, dependency policy, and Compose browser-to-API checks.
      Targeted manual mutation/fault-injection evidence accompanies relevant changes.
- [ ] Automate mutation checks after Python 3.14 runner selection and validation.
- [ ] Build the pinned full dataset and private GHCR image on `main`.
- [x] Bind validation images to source commits and publish run-specific tags;
      deploy only exact digests with verified revision labels. Signed product
      promotion remains #41; never deploy `latest`.
- [ ] Sign release digests and publish build provenance using GitHub OIDC.
- [ ] Bind image digest, source commit, and build workflow in a verifiable
      attestation; reject a moved `sha-<commit>` tag.
- [ ] Require a signed `v*` tag workflow that promotes the attested digest.
- [x] Verify validation GHCR references resolve to intended runnable
      `linux/amd64` images; retain this verification in #41's signed product path.
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

- [ ] Reuse and recovery-test the watcher private versioned S3 Terraform state
      bucket with native locking.
- [x] Purchase the dedicated Mikr.us `3.0` host; pin its SSH host key, rotate the
      console password, install a dedicated 1Password-backed operator key, and
      apply an idempotent Ansible baseline for key-only SSH, exact UFW rules,
      current packages, unattended upgrades, and bounded persistent logs.
- [x] Extend the reviewed Ansible baseline with exact Docker Engine and Compose
      packages, bounded logs, no unprivileged operators, and no TCP daemon socket.
- [x] Add service UIDs and GIDs, persistent and staging volumes, and service-
      restricted secret files with the first deployable Compose stack.
- [x] Create the `watcher` Terraform root for the snapshot bucket, conditional
      write policy, watcher IAM identity, GitHub OIDC read role, and budget.
- [x] Create the watcher access key manually outside Terraform; store the
      recovery copy in the password manager and install it as a service-
      restricted host file for the pinned watcher UID/GID.
- [x] Complete #55: review scoped AWS production and disposable-test operators,
      provision the fixed acceptance boundary, and regenerate the production
      plan under the approved role before any AWS apply. Disposable denial
      tests carry over to #49.
- [x] Install host GHCR authentication for private exact-digest pulls, using
      the required package-read-only credential boundary.
- [ ] Verify actual hosted archive-reader OIDC/IAM acceptance (#19); the custom
      subject rehearsal does not prove the current AWS trust accepts it.
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
      S3, and recovery-image preflights before activation.
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
- [x] Verify validation releases through the public domain with real source
      searches and artificial grouping cases; retain these checks in #27's
      full signed deployment path.
- [ ] Verify bounded Docker logs, read-only watcher status, and operator-run
      readiness and known-search checks. Accept that these local signals do not
      detect a complete Mikr.us host, network, or power outage from outside its
      fault domain.
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
