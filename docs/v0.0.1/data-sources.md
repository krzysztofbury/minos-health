# Data Sources

Status: primary sources revalidated 2026-08-21; CZS and `dane.gov.pl` currency
rechecked 2026-09-14. Earlier research was performed 2026-08-06. Facts below are
point-in-time observations; items marked NIEPEWNE are inferences or third-party
statements that must not control production data.

This document is operational: which publisher controls each fact, where the
bytes live, how changes are discovered, and which watcher owns them. The legal
analysis lives in [data-and-licensing.md](data-and-licensing.md).

## Authority And Discovery

Minos does not select one globally canonical website. Authority is per data
domain and per reporting period.

| Data | Controlling source | Discovery and cross-checks |
| --- | --- | --- |
| JGP catalogs, conditions, and points | Issued NFZ act and attachments in BAW | NFZ order listing and announcement page |
| Effective legal lineage | BAW source, amendment, and consolidated document graph | Act text and NFZ announcement metadata |
| Grouper algorithm and procedure ranks | NFZ Komunikaty JGP | General NFZ provider news may add operational guidance |
| ICD-9 PL | NFZ Centralne Zasoby Słownikowe (CZS) | NFZ ICD-9 page and general announcements; RSK is secondary and may lag |
| ICD-10 PL | RSK HCD `00CD10` | JGP attachment 9 contains the billing-relevant subset |
| JGP tariff modifier mapping | NFZ CZS `MODTAR`, discovered by year/type and pinned by generation | Governing NFZ act text controls legal applicability |
| Tariff signals | AOTMiT obwieszczenia | Issued NFZ catalog remains controlling |
| Competitor output | Never authoritative | SGA, CGM/UHC, Asseco, Comarch, and KAMSOFT are review oracles only |

The NFZ and AOTMiT publication pages do not expose a useful RSS or Atom change
feed. That does not mean every source requires HTML scraping: BAW exposes
anonymous JSON and binary endpoints, and CZS explicitly documents anonymous
SOAP and direct-download automation.

## NFZ Hospital Orders

The current base act is `46/2026/DSOZ`, signed and published 2026-04-30 and
effective 2026-05-01:

- announcement: <https://www.nfz.gov.pl/zarzadzenia-prezesa/zarzadzenia-prezesa-nfz/zarzadzenie-nr-462026dsoz,7938.html>;
- BAW source document `43868`:
  <https://baw.nfz.gov.pl/NFZ/document/43868/Zarzadzenie-46_2026_DSOZ>.

It repealed `101/2025/DSOZ` at the end of 2026-04-30. The two observed lineages
must not be merged:

```text
101/2025/DSOZ -> 1/2026 -> 14/2026 -> 32/2026 -> repealed
46/2026/DSOZ  -> 74/2026 -> current consolidated document 43943
```

Amendment `74/2026/DSOZ` is BAW document `43942`. It was signed and published
2026-07-23 and entered into force 2026-07-24, while its section 4 applies the
settlement rules to services delivered from 2026-07-01. Store at least:

- act date;
- publication timestamp and raw BAW value;
- entry-into-force date;
- service applicability date and its evidence;
- valid-to date;
- observed legal status.

Attachment `1a` is the JGP catalog workbook. After `74/2026`, that workbook has
separate worksheets `1a - katalog grup` and `1ae - katalog grup`. `1ae` is a
catalog inside attachment `1a`, not a separately issued attachment. Catalog,
settlement mode, duration, and statistical-unit code must remain separate
facts; `1ae` must not be reduced to `duration <= 12h`.

Attachment `9` contains group characteristics, directional ICD-10 and ICD-9
lists, age and length-of-stay conditions, and source-preserved code titles.
Correction coefficients and combination rules can also live in legal prose and
therefore require curated rows with `manual` provenance.

Watcher discovery starts with the NFZ server-rendered listing:
<https://www.nfz.gov.pl/zarzadzenia-prezesa/zarzadzenia-prezesa-nfz/1,strona.html?szukaj=szpitaln>.
Match the normalized DSOZ hospital subject, then verify that subject and status
against BAW. The search also finds unrelated DGL, DSM, and PSZ streams; those
are notification-only unless product scope changes. Projects are monitored at
<https://www.nfz.gov.pl/zarzadzenia-prezesa/projekty-zarzadzen/> and never
treated as issued rules.

## BAW Document And Attachment Retrieval

The BAW page is an AngularJS shell, but browser automation is not required for
normal retrieval. These anonymous endpoints worked with ordinary HTTP clients
on 2026-08-21:

```text
GET /api/documents/4/details/{document_id}/null
GET /api/documents/GetAmendments/{document_id}
GET /api/documents/4/GetDocumentAttachments/{document_id}/{true|false}
GET /api/file/4/{file_id}
GET /api/file/GetZipxAttachment/4/{file_id}
GET /api/file/GetActHtmlString/4/{root_file_id}
```

The complete file inventory is the recursive `Files[].Children[]` tree in the
details response. `GetDocumentAttachments` is relationship data, not the leaf
attachment inventory. Select the binary endpoint from `IsZipxAttachment` and
validate status, content type, `Content-Disposition`, byte count, file magic,
ZIP safety, and SHA-256. BAW provides no ETag, Last-Modified, Range support, or
source checksum for these files.

Document role cannot be inferred from act number. Original and consolidated
documents can share `46/2026/DSOZ`; classify them through document IDs,
`AutomaticType`, `SourceDocumentId`, and `IntroductoryDocumentId`. Traverse
those document relationships, amendments, consolidations, and comparison
documents recursively until no unseen document ID remains. Current anchors are:

| Role | Document ID | Important file IDs |
| --- | ---: | --- |
| Repealed base `101/2025` | `43718` | historical lineage |
| Original base `46/2026` | `43868` | `1a=29620`, `9=29652` |
| Amendment `74/2026` | `43942` | replacement `1=30968`, `9=30976` |
| Consolidated `46/2026` | `43943` | root ZIPX `30979`, `1a=30983`, `9=31015` |

The amendment replacements are byte-identical to the corresponding current
consolidated attachments. Archive content-addressed blobs and preserve both
provenance edges rather than storing by filename alone. Playwright is an
optional subject of a separate task only after ordinary HTTP fails and captured
evidence shows browser automation succeeds from the affected environment. It is
not a default dependency or an assumed solution to runner-IP blocking.

## NFZ Grouper Algorithm And Operational Guidance

The dedicated Komunikaty JGP page is:
<https://www.nfz.gov.pl/dla-swiadczeniodawcy/jednorodne-grupy-pacjentow/komunikaty-jgp/>.
The current official description is version `1.1.2026`, published 2026-04-22,
with a DOCX algorithm description and XLSX specialty/rank tables. The published
description is implementable but descriptive; NFZ claim verification remains
binding.

The watcher must also monitor general Centrala NFZ provider news. A critical
2026-07-29 notice was published there, not under Komunikaty JGP:
<https://www.nfz.gov.pl/aktualnosci/aktualnosci-centrali/komunikat-dla-swiadczeniodawcow,8987.html>.
It links code `26`, catalog `1ae`, and `74/2026`, and explicitly states that the
JGP grouping algorithm remains unchanged.

Poll known current detail pages as well as listing pages and re-hash their file
links. A stable URL is not a content identity. New general-news items are
classified by references such as `JGP`, `DSOZ`, `ICD-9`, `1a`, and `1ae`;
classification should assist review, not automatically promote rules.

## ICD-9 PL

NFZ CZS is authoritative. Its public help explicitly documents automated
retrieval:
<https://slowniki.nfz.gov.pl/Help/WersjaSlownikow#pobieranieAutomatyczne>.

```text
WSDL: https://slowniki-ws.nfz.gov.pl/ws-broker-czs/services/ServiceBrokerAnonymous?wsdl
latest XML: https://slowniki.nfz.gov.pl/WersjeSlownikow/PobierzXml?kodSlownika=ICD9
pinned XML: https://slowniki.nfz.gov.pl/WersjeSlownikow/PobierzXml?kodSlownika=ICD9&nrGen=92
```

`getListSlowniki` returns public version metadata; `getSlownik` returns a ZIP
with XML. A syntactically valid direct request for a missing version can return
HTTP 200 with header `XML_STATUS: NO_CONTENT`, which is an error, not an empty
dictionary.

Current ICD-9 PL is `5.89`, generation `92`, published and announced
2026-09-01:
<https://www.nfz.gov.pl/aktualnosci/aktualnosci-centrali/opublikowano-nowa-wersje-slownika-icd-9-pl-cm-w-wersji-5-89,9006.html>.
The NFZ XLSX page is a useful human-readable cross-check but is not a complete
version archive. CZS exposes more historical semantic versions.

RSK HCD `00CD09` currently exposes `5.87`, with validity ending 2026-07-20.
It is secondary and stale for this domain. Alert on disagreement, but never
select RSK ICD-9 over CZS merely because both are government systems.

The NFZ-published `dane.gov.pl` dataset `696` is a historical mirror, not a
current discovery channel. Its 16 XLS resources cover selected versions from
`5.22` through `5.39`; the newest content is `5.39` from 2018-08-02. The portal
labels the dataset `CC0 1.0` and `Yearly`, but it has received no newer content
despite the live NFZ series reaching `5.89`. The license applies to those listed
historical resources and must not be extrapolated to current CZS files.

## JGP Tariff Modifiers

CZS publishes the machine-readable `MODTAR` dictionary separately from the JGP
catalog and algorithm files. On 2026-09-14, the current 2026 SZP page displayed
version label `202662`; the separately fetched latest 2026 SZP XML reported
technical generation `100`, `data_pub="2026-08-20T08:00:30"`, and
`data_gen="2026-08-20T08:37:01"`:

```text
latest 2026 SZP XML: https://slowniki.nfz.gov.pl/WersjeSlownikow/PobierzXml?kodSlownika=MODTAR&rok=2026&typGrupera=SZP
version page: https://slowniki.nfz.gov.pl/ModyfikatoryTaryfyJgp/SlownikModyfikatorowTaryfJgp/7297
```

The XML timestamps contain no timezone offset and are not effective dates.

The XML maps a modifier symbol and cited legal clause to status, month range,
coefficient, and one or more JGP group codes. This is required for complete
tariff determination and must be pinned in each applicable `ruleset_release`.
The inspected outputs do not directly link display version `202662` to
generation `100` or state an effective date. Store both observations, but bind
a generation to a reporting-month ruleset only after reviewing legal and
applicability evidence; the presence of `mies_od="7"` and `mies_do="12"` rows
alone does not establish a 2026-07 effective date.
The governing NFZ act remains controlling for eligibility, combination rules,
and legal applicability; disagreement between the act and `MODTAR` blocks the
release for review rather than selecting either silently.

Issue #7 monitors `MODTAR` metadata and pinned bytes through the same documented
CZS automation interface used for ICD-9. No equivalent current dataset was
found on `dane.gov.pl`.

## ICD-10 PL And RSK

The anonymous catalog is:
<https://rsk3.ezdrowie.gov.pl/api/rsk/resource/role/UZYNLG>. On 2026-08-21 it
returned eight HCDs: `99ICD1`, `99CD11`, `00CD10`, `00CD09`, `99CD1A`,
`99ICF1`, `99GPS1`, and `99PNS1`. Eight is an observed baseline, not a schema
invariant.

Per-resource metadata is available at
`/api/rsk/resource/basic-info/{hcd}`. It includes resource, publisher, validity,
and file metadata but no structured license, publication date, checksum, ETag,
or anonymous history listing. Download bytes from `/api/rsk/file/get/{uuid}`.
Range and conditional requests were ignored in live checks.

The resource `version` is not an immutable file-set revision. Files were added
to unchanged ICD-11 resource versions between 2026-08-18 and 2026-08-21. The
current `00CD10` XML was created 2026-08-05 while resource version remained
`013`; without a captured predecessor fixture, do not overstate this as proof
that bytes changed behind the same UUID.

Issue #6 monitors and archives exact catalog/basic-info responses for every
returned HCD, but downloads only an explicit product artifact allowlist. The
initial byte scope is the `00CD10` XML. Copying all 24 currently exposed files
(about 316 MiB, mostly out-of-scope ICD-11) would broaden licensing and storage
without product value.

For allowlisted bytes, compare complete file metadata and compute SHA-256.
`uuid + creationDate + sizeBytes` is a useful trigger, not a sufficient content
identity. Re-fetch metadata after download to detect rotation during transfer.
Re-download and re-hash unchanged allowlisted files at least weekly, and on a
manual forced run, because the source provides no validators.

## NFZ Statistics API

<https://api.nfz.gov.pl/app-stat-api-jgp/> provides JSON:API hospitalization
statistics by JGP group. Its terms state 10 requests/s per IP, attribution,
no data modification, and no warranty. It does not provide grouper definitions
or point values and remains a later contextual source.

`dane.gov.pl` contains useful NFZ-published empirical data, but no operative
JGP catalog, characteristics, grouper algorithm, procedure-rank table, NFZ act
collection, or AOTMiT tariff table was found in the 2026-09-14 audit:

| Dataset | Freshness and license | Minos use |
| --- | --- | --- |
| `1519`, API Statystyki NFZ - Świadczenia | Live API pointer, `CC BY 4.0` | Existing later utilization source; portal metadata is not the data API |
| `2581`, Miesięczny raport dot. hospitalizacji JGP | Monthly, verified 2026-08-12, `CC0 1.0` | Best fresh aggregate source for utilization trends and product-level context |
| `3009`, hospitalizacje rozliczone JGP 2019-2025 | Annual, published 2026-07-09, `CC0 1.0` | Historical JGP, provider, and ICD-9 procedure distributions for plausibility checks and analytics |
| `4896`, ICD-10 diagnosis plus ICD-9 procedure by provider | Quarterly, updated 2026-06-15, `CC0 1.0` | Optional diagnosis-procedure plausibility context; not a grouper golden-case source |

Dataset `1734` (NFZ contracts), CeZ dataset `728` (RPWDL), and broader
diagnosis-count datasets are potentially useful for future organization or
market features. They do not support current point-only grouping requirements
and are not ingestion dependencies. Dataset `4864` is a narrower, one-off JGP
provider reimbursement extract already covered better by the sources above.

The CeZ `dane.gov.pl` dataset `27`, Rejestr Systemów Kodowania, is also a stale
two-file publication. Its `CC BY 4.0` dataset label and portal verification
timestamp do not establish the currency or licensing of current RSK resources.

## AOTMiT Tariff Signals

<https://bip.aotm.gov.pl/taryfikacja-swiadczen/860-taryfikacja-obwieszczenia>
publishes PDF tariff announcements. They can precede NFZ catalog changes, but
the issued NFZ catalog controls Minos. Monitor and notify; do not parse into
production rules in `v0.0.1`.

No working RSS/Atom feed was found. Poll page 1 politely, honor `Retry-After`
even if supplied with HTTP 200, and avoid crawling the full archive daily.
Deduplicate known identical repeated PDF announcements by stable page identity
and content hash; conflicting duplicates fail for review.

## Competitor And Vendor Cross-Checks

Competitors help find omissions and create golden cases. They never establish
the authoritative rule or legal effective date.

- SGA WikiJGP serves static versioned JSON. On 2026-08-21, `SZP2689` covered
  `202607` through `202612` and was loaded from
  <https://www.sga.waw.pl/WikiJGP/data/version/SZP2689.json>, with version
  metadata at <https://www.sga.waw.pl/WikiJGP/data/data-map.json>. The payload
  contains groups and ICD list membership but no NFZ act, source checksum,
  ICD edition, or transformation provenance.
- CGM/UHC Optymalizator JGP is a legacy JSF application. Its captured public
  selector contains historical SZP versions only through `SZP.15.103`, defaults
  to choosing by discharge date, and exposes no official source lineage. It is
  useful only for historical black-box comparisons.
- Asseco mMedica explicitly linked transitional grouper file `SZP.19.98`
  (`nr-gen=268`) to `74/2026`, the 2026-07-29 NFZ guidance, and retroactive
  applicability from 2026-07-01. Its documented ambiguity is strong evidence
  for manual review, but NFZ's statement that the official algorithm remains
  unchanged controls over vendor interpretation.
- Public Comarch Optimed material confirms operational consumption of NFZ
  communications and named NFZ orders, but does not disclose an exact JGP
  source pipeline. KAMSOFT documentation confirms effective-date-aware grouper
  version selection, not provenance.

Never parse competitor identifiers (`SZP2689`, Asseco `nr-gen=268`, CZS
`nr_gen=92`, or official algorithm `1.1.2026`) as interchangeable version
systems.

## Watcher Ownership

| Issue | Responsibility |
| --- | --- |
| `#6` | RSK catalog and all returned resource metadata; allowlisted RSK bytes and content drift |
| `#7` | NFZ HTML discovery, general provider news, Komunikaty JGP, CZS ICD-9 and `MODTAR`, projects, and AOTMiT signals |
| `#8` | BAW JSON document graph, original/amending/consolidated packages, attachment bytes, dates, and hashes |

Issue #7 dispatches discovered BAW IDs to #8. It does not duplicate BAW file
retrieval. Issue #8 does not discover unrelated NFZ news or own CZS.

Implementation order is #49, then completion of #6, then #8, then #7. Issue #49
establishes the SQLite control plane, local and S3 blob contracts, recovery
images, and Mikr.us scheduler. Issue #8 supplies the working BAW dispatch target
that issue #7 subsequently calls.

Issues #6-#8 stop at exact raw observations, selected source bytes, provenance,
change records, and incidents. They do not parse terminology or JGP facts into
the generated SQLite reference database. Issue #21 owns conversion of reviewed
change records into manifest PRs.

## Archive And Failure Rules

1. Poll with bounded timeouts, low concurrency, descriptive user agent, and
   bounded exponential backoff with jitter. Honor `Retry-After` when present.
2. Archive exact successful metadata observations before deriving normalized
   state. Empty or sharply reduced results are schema-drift alerts.
3. Stream allowlisted bytes while hashing. Validate declared and received byte
   counts, magic, extension, archive safety, and parser bounds.
4. Address exact response bodies and downloaded files by SHA-256. Use
   single-part conditional `PutObject` with exact `ContentLength` and the
   Base64-encoded whole-object SHA-256 in `ChecksumSHA256`; deny watcher
   overwrite and deletion under blob prefixes. Derive the requested key from the
   local digest; after create or `412`, verify checksum and byte count with
   checksum-enabled `HeadObject`. Retry `409` conflicts within the bounded
   operation. S3 versioning, content addressing, and IAM enforce the
   application-level write-once rule. Filenames and mutable URLs are metadata,
   not identity.
5. Fetch metadata before and after a download batch. A changed source view
   invalidates that attempt rather than publishing a mixed observation.
6. Advance SQLite state only after every referenced S3 blob passes that
   verification. Commit the observation, artifact references, change or incident
   record, source cursor, incremented state revision, claim fencing token, and
   `committed` run state in one local transaction. A post-upload crash may leave
   a harmless unreferenced blob; retry reuses it only after re-verification.
7. Preserve publication, entry-into-force, service-applicability, first-seen,
   fetch, and ingestion times separately. Never invent one from another.
8. Source collectors emit a bounded change record or incident. Issue #21 may
   turn a reviewed change into a manifest PR; no automation auto-promotes an
   upstream change into production rules.
9. A source disagreement opens a review item with bounded evidence. Competitor
   agreement cannot override a primary source.
10. A quiet run is valid only after selectors, expected source IDs, and
    freshness have been checked against actual responses.

## Remaining Research Gates

- Capture dated raw fixtures for every endpoint before implementing parsers.
- Ask CeZ whether RSK file UUIDs and bytes are contractually immutable and for
  automation/rate-limit guidance.
- Verify BAW plain-HTTP behavior from the Mikr.us host. Consider browser
  automation only in a separate task after captured evidence shows it succeeds
  from that environment.
- Confirm per-resource redistribution rights separately; stale `dane.gov.pl`
  dataset-level license metadata does not license current RSK resources.
- Revisit `dane.gov.pl` datasets `2581`, `3009`, and `4896` only when the
  later utilization or plausibility feature has an explicit product contract.
- Formal legal review remains required before commercial redistribution.
