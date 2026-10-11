# Minos v0.0.1

Status reconciled 2026-10-11: reference search, dated JGP pages and the supported
grouper are deployed under the public-validation exception. Full product-release
and practitioner acceptance remain open; see [TODO](../../TODO.md) and the
[#156 delivery record](https://github.com/krzysztofbury/minos-health/pull/156#issuecomment-6077733204).

This directory records the decisions and constraints for the first Minos
release. Root-level [SPEC.md](../../SPEC.md) defines observable product
behaviour; these documents explain the underlying tradeoffs and delivery
model.

## Documents

- [decisions.md](decisions.md): accepted, rejected, and still-open decisions,
  including the 2026-08-06 pivot record and the naming screen.
- [architecture.md](architecture.md): Python packages, bitemporal data model,
  search, grouping, API, and runtime.
- [data-sources.md](data-sources.md): every upstream source, its endpoints,
  cadence, and the watcher/archive rules.
- [jgp-conditions.md](jgp-conditions.md): source-backed attachment 9 taxonomy,
  proposed typed conditions, frequency census and #30 review boundaries.
- [jgp-code-correspondence.md](jgp-code-correspondence.md): source-local NFZ
  member identities, separately attributed dictionary links and watcher-to-app
  population/review gates.
- [jgp-conditions-ingest.md](jgp-conditions-ingest.md): #30 reference importer,
  schema, conservative source gaps, dated correspondence and reproducible checks.
- [data-and-licensing.md](data-and-licensing.md): provenance requirements and
  the code/data licensing boundary, including the Art. 4 position.
- [deployment.md](deployment.md): image lifecycle, CI/CD, the Mikr.us Compose
  runtime, S3 archive, and Cloudflare Tunnel.
- [operations.md](operations.md): logging, monitoring, backups, rollback,
  recovery, and verification.
- [release-diff.md](release-diff.md): selected-scope logical release comparisons,
  explicit pinning, Markdown/JSON output and limits for #20/#31.
- [grouping-interface.md](grouping-interface.md): deployed CLI/HTTP/builder,
  trace, privacy and remaining practitioner acceptance (#158).
- [golden-cases.md](golden-cases.md): reviewed synthetic and pinned-source
  derived encounters, rule coverage and divergence dispositions (#37).

## Release Definition

`v0.0.1` is complete only when:

1. Phase 0 watchers run on schedule and archive every exact metadata observation
   and selected in-scope source file. Reviewed updates preserve verified source
   identities and last-good rollback. The earlier #21 unattended-activation work
   is parked in #141 under the owner decision of 2026-10-06; it is not a current
   prerequisite for validation. Re-entry requires explicit owner prioritization.
   Its original automatic-update safety contract remains in the decisions log.
2. The pinned multi-source ruleset (ICD-10 PL, ICD-9 PL, JGP katalog and
   charakterystyka, algorithm metadata) transforms reproducibly into a
   validated bitemporal database with provenance and checksums.
3. CLI and HTTP searches and JGP group lookups share deterministic domain
   behaviour, selected by explicit service date.
4. The anonymous webpage works on desktop and mobile and presents terminology
   search, the JGP knowledge base, version diffs, and source and safety
   information clearly.
5. The same tested API, edge, and watcher images run through Docker Compose
   locally and on the dedicated Mikr.us `3.0` production host.
6. A signed version tag promotes a commit-bound, attested image digest,
   checksummed static web bundle, and prebuilt CLI/data artifacts without
   rebuilding, exercises desktop and mobile searches through Cloudflare Tunnel,
   and can roll back the complete compatible digest set.
7. Representative load, restart, memory, disk, image-size, recovery, and cost
   measurements on Mikr.us meet explicitly recorded launch thresholds.
8. Code and data notices are present in source, generated artifacts, and the
   public application.
9. Minos naming has passed trademark counsel review and the intended domains
   are registered and locked.

The grouper engine and stateless grouping endpoint belong to the `v0.0.2` scope
and their supported subset is already available for public validation. That
delivery does not establish full `v0.0.2` acceptance. The what-if optimizer
remains future `v0.0.3` work.

## Change Policy

An implementation detail may not silently override an accepted decision. If a
decision changes, update [decisions.md](decisions.md), the affected design
document, [SPEC.md](../../SPEC.md), and [CHANGELOG.md](../../CHANGELOG.md) in
the same change.
