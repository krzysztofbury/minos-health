# Data And Licensing

Operational source details (endpoints, cadence, watchers) live in
[data-sources.md](data-sources.md). This document covers provenance
requirements and the legal boundary. Planning observations are dated
2026-08-06; none of this is legal advice, and the two formal opinions listed
under gates are release requirements for the paid product.

## License Position By Dataset

| Dataset | Stated license | Position |
| --- | --- | --- |
| Zarządzenia Prezesa NFZ and załączniki (katalog 1a, charakterystyka 9, coefficients) | none stated | Normative acts of a public payer issued under statutory authority. Strong reading: copyright-exempt official materials under Art. 4 pkt 1-2 ustawy o prawie autorskim. 15-plus years of commercial grouper precedent (KAMSOFT, CGM, Asseco, SGA). Formal opinion pending. |
| NFZ komunikaty JGP (algorithm description, rank tables) | none stated | Same Art. 4 argument (urzędowe dokumenty i materiały). Published expressly so providers could buy groupers on the open market. |
| ICD-9 PL (NFZ files) | none stated on the NFZ page | Same Art. 4 argument. A stale dane.gov.pl dataset (v5.39, 2018) declares `CC0 1.0`; extrapolating that to current versions is inference (NIEPEWNE). |
| CZS `MODTAR` (JGP tariff modifiers) | none stated on CZS | Machine-readable NFZ dictionary derived from cited provisions of the governing order. Treat under the same pending Art. 4 and attribution review; the legal text remains controlling. |
| ICD-10 PL (RSK `00CD10`) | `CC BY-NC-SA 3.0 IGO` quoted in RSK metadata | The license sentence grammatically attaches to the WHO 2019 English edition; whether the Polish translation itself is under it is ambiguous. Treated as NC-constrained until resolved. |
| NFZ statistics API | portal terms | Open data: attribution to api.nfz.gov.pl required, 10 requests/s per IP, no data modification, no warranty. |

## The Strategic Consequence

Załącznik 9 (charakterystyka JGP) carries ICD-10 codes **with full Polish
titles**, verified by sampling the 1/2022 edition on 2026-08-06 (8,759
code-plus-title strings in that file, a count that also includes JGP group
names; confirm against the current 46/2026 consolidated edition during
fixturing). The
billing-relevant ICD-10 subset therefore lives inside the Art. 4-arguable
normative acts, alongside the complete ICD-9 PL and all JGP rules.

Consequences:

- The commercial core does not depend on the RSK `CC BY-NC-SA` resource.
- The full RSK ICD-10 dictionary (complete hierarchy, all descriptions) is
  free-tier enrichment. Until the NC scope is legally resolved, no paid
  feature may require it.
- Every concept row records which source release it came from, so the
  boundary is enforceable in data, not just in policy.

## RSK ICD-10 Source Record

Planning metadata observed 2026-08-06 and revalidated 2026-08-21:

| Field | Value |
| --- | --- |
| Resource | ICD-10, HCD `00CD10` |
| Selected version | `013` |
| Publisher | `Biuro Światowej Organizacji Zdrowia w Polsce` (`WHO PL`) |
| Valid from | `2025-10-16T22:00:00Z` (`2025-10-17` in Poland) |
| XML label | `ICD10_2008_PL_v2026_07_20` |
| XML UUID | `b8dd1741-c048-489e-84d6-47a915425b2d` |
| XML size | `12738027` bytes |
| XML created | `2026-08-05T10:43:22.502Z` |
| Stated license | `CC BY-NC-SA 3.0 IGO` (scope ambiguity noted above) |

RSK states that the Polish translation was not prepared by WHO, that WHO is
not responsible for its content or accuracy, and that the 2019 English
edition is the binding and authentic edition. This warning and the publisher
identity are mandatory provenance in artifacts and user-facing views.

RSK provides no checksums or useful ETag/Last-Modified or Range behavior.
Resource versions are not immutable file-set revisions: files can be added or
recreated while the resource version remains unchanged. The current XML was
created 2026-08-05 while version `013` remained selected, but no predecessor
fixture exists to prove mutation behind the same UUID. Checksums are computed
at archive time and pinned in the manifest.

The stale `dane.gov.pl` RSK dataset metadata must not be generalized to current
resources. Its dataset-level `CC BY 4.0` label refers to an obsolete two-file
publication and conflicts with other metadata; current rights remain a
per-resource review.

## Dataset Manifest

Every data refresh is a reviewed manifest change. The manifest pins every
upstream file and the ruleset pairing:

```toml
schema_version = 2

[[source]]
system = "icd10-pl"
origin = "rsk"
hcd = "00CD10"
source_version = "013"
source_file_id = "b8dd1741-c048-489e-84d6-47a915425b2d"
source_url = "<resolved-download-url>"
fetched_at = "<utc-timestamp>"
archived_sha256 = "<sha256-computed-at-archive-time>"
source_bytes = 12738027
source_license = "CC-BY-NC-SA-3.0-IGO (scope under review)"

[[source]]
system = "icd9-pl"
origin = "nfz-slowniki"
source_version = "5.88"
source_generation = 91
# historical source required by the 2026-07 ruleset below

[[source]]
system = "icd9-pl"
origin = "nfz-slowniki"
source_version = "5.89"
source_generation = 92
# url, fetch, checksum fields as above

[[source]]
scheme = "jgp-szp"
origin = "nfz-baw"
act = "46/2026/DSOZ"
baw_source_document_id = "43868"
baw_consolidated_document_id = "43943"
attachments = ["1a", "9"]
# per-file url, fetch, checksum fields as above

[[source]]
scheme = "jgp-szp"
origin = "nfz-komunikat"
algorithm_version = "1.1.2026"
# per-file fields as above

[[source]]
scheme = "jgp-szp"
origin = "nfz-slowniki"
dictionary = "MODTAR"
source_generation = 100
year = 2026
grouper_type = "SZP"
display_version_observed = "202662"
# The UI label and XML generation were observed separately. Preserve both
# source identities; do not infer reporting-month applicability from them.
# url, fetch, checksum fields as above

[ruleset."2026-07"]
act = "46/2026/DSOZ+74/2026/DSOZ"
algorithm_version = "1.1.2026"
icd9_version = "5.88"
icd10_version = "013"
modtar_generation = "<reviewed-applicable-generation>"
published_at = "2026-07-23"
entry_into_force_on = "2026-07-24"
applies_from = "2026-07-01"      # retroactive; preserve the legal evidence

transform_version = "<git-commit-or-tool-version>"
```

The generated artifact manifest additionally records database SHA-256, byte
size, schema version, row counts per table, build timestamp, Python version,
SQLite version, and lockfile identity.

CI builds from the exact manifest. Watchers propose changes; CI must never
follow a `latest` link or replace a pinned source automatically.

## Import Pipeline

1. Resolve every pinned source from the manifest.
2. Fetch from the archive (S3), not live upstream; live fetch happens only in
   watchers, which archive immediately.
3. Verify byte count and checksum against the manifest before parsing.
4. Parse with hardened parsers: XML (no DTDs or external entities; bounded
   depth, value size, record count, bytes), XLSX/DOCX (zip entry-count,
   expansion-ratio, size limits; merged-cell-aware headers), with line or
   element context on failure.
5. Validate required values, encoding, uniqueness, hierarchy references, and
   condition list references.
6. Convert records into source-preserving domain values plus separate
   normalized search keys; apply curated overlay rows (coefficients from
   legal prose) with `manual` provenance.
7. Write a fresh database inside one transaction with bitemporal fields.
8. Run integrity queries and deterministic count checks.
9. Finalize the database and emit the artifact manifest, checksum, and a
   human-readable diff against the previous release.

A failed stage leaves no publishable database. Existing valid artifacts are
not modified in place.

## Validation Requirements

At minimum, a complete build verifies:

- exactly one ruleset release per reporting period in scope;
- unique canonical concept lookup identity within each source release and
  terminology system, distinct from repeated raw suffix labels;
- non-empty source code and preferred description;
- all hierarchy references resolve or match a documented source root rule;
- all condition list references resolve to known concepts;
- no relation creates an unexpected hierarchy cycle;
- normalized search keys retain a link to exact source text;
- point values carry validity periods and settlement modes;
- source ordering is stable when present;
- repeated builds from identical inputs produce the same logical rows and
  artifact checksum, excluding deliberately isolated non-deterministic
  metadata;
- row counts match reviewed expectations.

Unsupported structures that could alter meaning are errors, not warnings. A
source exception is allowed only with a fixture, an explicit conversion rule,
and a recorded modification notice.

## Normalization

Normalization supports lookup without rewriting the source:

- Unicode text is normalized consistently.
- Search may use case folding and documented punctuation/whitespace handling.
- Polish letters are not discarded from displayed or authoritative text.
- Any optional accent-insensitive key is additional and tested for collisions.
- Codes use a source-aware canonical comparison rather than unrestricted text
  cleanup.

The reviewed RSK conversion expands dotted suffix labels only under an explicit
three-character category parent, removes trailing dagger/asterisk markers from
lookup keys only, and gives range lookup keys an ASCII hyphen. Exact source
codes and markers remain on source nodes. Single-digit modifiers and uncoded
descriptive nodes retain hierarchy identity without becoming invented diagnosis
codes. Polish names are preferred descriptions; nonempty `EN` values are English
translations. Annotations and uncoded descriptions are not automatically
synonyms. These rules are versioned, covered by synthetic fixtures and included
in the artifact modification notice.

Normalization versions are part of artifact provenance because changing them
can change search results.

## Storage And Distribution

Raw and generated data are absent from Git.

| Material | Canonical storage | Notes |
| --- | --- | --- |
| Source manifest | Git | No source bytes or credentials |
| Raw upstream snapshots (RSK, NFZ, AOTMiT) | Private versioned S3 | Content-addressed, conditional creates, no watcher overwrite/delete permission; accepted sole raw-snapshot provider |
| Watcher control recovery images | Private versioned S3 | Immutable revisioned SQLite images with bounded deletion-eligibility rules and restore drills |
| Generated database | Private artifact storage and runtime image | Immutable by checksum |
| Runtime image | Private GHCR | Canonical digest used for releases |
| Small fixtures | Git | Synthetic or separately redistributable |

The watcher identity can conditionally create content-addressed objects but
cannot overwrite or delete them. Provenance, observations, schedules, and source
cursors live in the control SQLite database rather than S3 sidecars or mutable
state objects. S3 versioning provides recovery from ordinary operational
mistakes. The accepted design has no independent second storage provider, so
complete AWS account compromise remains a documented residual risk.

Raw snapshot objects do not expire. Immutable control recovery images use
class-specific `run`, `daily`, `weekly`, and `monthly` prefixes. Current versions
become expiration-eligible after 30, 31, 91, and 400 days respectively;
noncurrent versions become deletion-eligible one day after current expiration,
and expired delete markers are removed. Lifecycle processing is asynchronous.
Independently signed operator inventory objects never expire, preserving their
digest chain from sequence one. Their trusted recovery-image copies become
expiration-eligible after 400 days under a separate operator-only prefix. S3
provides no guaranteed physical deletion deadline. A seven-day incomplete-
multipart abort is defense in depth because watcher uploads are single-part. A
current upstream URL is not a backup; upstream may remove its own history.

## Repository License

Software, infrastructure, tests, and project documentation are dual-licensed
under:

```text
Apache-2.0 OR MIT
```

This allows the application, parser framework, search implementation, grouper
engine, CLI, HTTP adapter, frontend, infrastructure code, tests, and
documentation to be reused commercially, subject to normal dependency
licensing.

## Dataset License Boundary

The repository licenses do not grant rights to source data, generated medical
content, or an image containing that content. Likewise, no data license
applies to separable repository materials.

Before charging for the product, confirm through counsel:

- the Art. 4 status of zarządzenia appendices and komunikat files when
  redistributed inside a commercial database and container;
- required attribution wording for NFZ-sourced materials;
- the scope of `CC BY-NC-SA 3.0 IGO` on the RSK ICD-10 translation and the
  treatment of the derived free-tier database (attribution, modification
  notice, share-alike, noncommercial boundary);
- non-endorsement and warranty statements for all publishers.

The same clearance applies before publishing a downloadable offline PWA
artifact. A paid hospital licence must not unlock or redistribute RSK-derived
content whose noncommercial or share-alike boundary has not been resolved.
Minos publishes one canonical SQLite artifact per dataset release rather than
silently diverging free and commercial copies. Any future reduced dataset must
be an explicitly named and independently versioned profile with the same domain
compatibility tests.

## Artifact Notice

Each generated database and image containing it must expose or package:

- attribution per source: NFZ (zarządzenia, komunikaty, ICD-9 PL), RSK/CeZ and
  Biuro WHO w Polsce (ICD-10 PL);
- act numbers, HCDs, source versions, file identities, and exact upstream
  timestamps;
- archive-time checksums and generated checksums;
- retrieval dates and transformation version;
- a clear statement that Minos transformed the sources;
- the WHO translation warning for ICD-10 PL (not prepared by WHO; 2019
  English edition binding and authentic);
- a statement that no publisher endorses Minos;
- the Minos reference-only and completeness-not-upcoding disclaimers.

The web footer, API metadata, CLI output, and artifact manifest should derive
these fields from the database rather than maintaining divergent hard-coded
copies.

## Other Sources

WHO ICD-10-specific commercial redistribution and WHO-derived ICD-9 rights
have not been resolved. No WHO dataset may be bundled based on assumptions
derived from Polish sources.

Each future namespace requires its own source manifest, rights review,
attribution, provenance, and release identity. Similar codes do not imply
interchangeable text or licensing.
