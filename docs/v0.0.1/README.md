# Minos v0.0.1

Status: accepted planning baseline, implementation in progress

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
- [data-and-licensing.md](data-and-licensing.md): provenance requirements and
  the code/data licensing boundary, including the Art. 4 position.
- [deployment.md](deployment.md): image lifecycle, CI/CD, the Mikr.us Compose
  runtime, S3 archive, and Cloudflare Tunnel.
- [operations.md](operations.md): logging, monitoring, backups, rollback,
  recovery, and verification.

## Release Definition

`v0.0.1` is complete only when:

1. Phase 0 watchers run on schedule, archive every exact metadata observation
   and selected in-scope source file, and issue #21 opens reviewed data PRs
   with diffs from their change records.
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

The grouper engine and stateless grouping endpoint are `v0.0.2`; the what-if
optimizer is `v0.0.3`.

## Change Policy

An implementation detail may not silently override an accepted decision. If a
decision changes, update [decisions.md](decisions.md), the affected design
document, [SPEC.md](../../SPEC.md), and [CHANGELOG.md](../../CHANGELOG.md) in
the same change.
