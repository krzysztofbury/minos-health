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
| ICD-10 PL | RSK HCD `00CD10` | JGP attachment 9 retains separately maintained NFZ membership; not every NFZ member has an exact RSK counterpart |
| JGP tariff modifier mapping | NFZ CZS `MODTAR`, discovered by year/type and pinned by generation | Governing NFZ act text controls legal applicability |
| Tariff signals | AOTMiT obwieszczenia | Issued NFZ catalog remains controlling |
| Ward specialty codes (part VIII) | Annex 2 of the resortowe kody identyfikacyjne regulation, consolidated Dz. U. z 2019 r. poz. 173, authentic PDF from the Sejm ELI API | ELI act status and amendments; the ELI HTML merges rows at page breaks and is not a source |
| Discharge-mode codes | Annex 4 of the regulation on niezbędne informacje, consolidated Dz. U. z 2025 r. poz. 1588, authentic PDF | ELI act status; 2026 amendments checked; CeZ HL7 value set 2015 wording is outdated |
| Competitor output | Never authoritative | SGA, CGM/UHC, Asseco, Comarch, and KAMSOFT are review oracles only |

For JGP list membership, NFZ attachment 9 code/title identities remain
source-local and authoritative even where RSK lacks an exact code. Dictionary
correspondence is separate, not a reason to drop a member or manufacture a
replacement. See [the #30 decision and population contract](jgp-code-correspondence.md).

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
46/2026/DSOZ  -> 74/2026 -> 102/2026 -> current consolidated document 44042
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

The subsequent issued amendment `102/2026/DSOZ` is BAW document `44041`;
its consolidated document is `44042`. Its reporting-period exceptions are
described under JGP Tariff Modifiers below. Keep both amendment observations
and their distinct consolidated snapshots; do not replace a historical
service-date binding simply because the latest consolidated document changed.

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

## Settlement Product Catalogs (#145)

Every pinned act publishes product catalogs 1b, 1c, 1d, 1ows, 1ts and 1w beside
1a/1ae, and no 1v. `manifests/sources/product-catalogs.json` pins each file by
BAW file ID, SHA-256 and byte count, and binds the set to the act's JGP catalog
release. Only the reviewed product-code column is read (header `Kod produktu` or
`Kod produktu rozliczeniowego`, name in the next column): other columns hold
cross references to codes of other catalogs, which a whole-sheet scan misread as
memberships. Row and unique-code counts are part of the review; any drift fails
the build. Legacy BIFF `.xls` files (1ts of act 101/2025) are pinned but not
parsed, so their codes stay unknown. 1w is the excluded catalog of type W.

| Act | BAW document | File IDs 1b, 1c, 1d, 1ows, 1ts, 1w |
| --- | ---: | --- |
| `101/2025` original | `43718` | `27126`-`27131` |
| `101/2025` after `14/2026` | `43769` | `27994`-`27999` |
| `46/2026` original | `43868` | `29621`-`29626` |
| `46/2026` after `74/2026` | `43943` | `30984`-`30989` |
| `46/2026` after `102/2026` | `44042` | `32995`-`33000` |

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
| Consolidated after `74/2026` | `43943` | root ZIPX `30979`, `1a=30983`, `9=31015` |
| Amendment `102/2026` | `44041` | root ZIPX `32981` |
| Consolidated after `102/2026` | `44042` | root ZIPX `32990` |

The amendment replacements are byte-identical to the corresponding current
consolidated attachments. Archive content-addressed blobs and preserve both
provenance edges rather than storing by filename alone. Playwright is an
optional subject of a separate task only after ordinary HTTP fails and captured
evidence shows browser automation succeeds from the affected environment. It is
not a default dependency or an assumed solution to runner-IP blocking.

On 2026-09-27, the live `GetAmendments/43868` response also listed amendment
`102/2026/DSOZ` (`44041`) and its consolidated document (`44042`). The IDs
above are historical anchors, not a complete current graph. The watcher must
follow the returned relations rather than hard-code a fixed amendment list.

The 2026-09-30 source-catalog review expanded private capture to comparison
variants as well. Supported `AutomaticType` values are original act (`1`),
consolidated text (`3`), full comparison (`4`), shortened comparison (`5`), and
amending act (`6`). Every supported document requires its root ZIPX package;
selected individually usable catalogs remain `Zalacznik1a.xlsx` and
`Zalacznik9.xlsx`, plus `Zalacznik1.xlsx` for an amending act. Package capture
preserves the remaining nested attachments without independently downloading
every leaf. The existing limits remain 32 documents, four selected files per
document, 512 MiB per file, and the fenced run deadline. Missing packages,
unsupported roles, conflicting lineage, or source drift cannot advance state.

Public BAW projection schema `2` adds the verified document role, original-act
edge, and introductory-amendment edge. It includes file evidence only for the
current selected inventory, not removed files retained in history. It exposes
neither private bytes nor archive paths. The source catalog groups consolidated
and comparison variants under their original act, with historical states keyed
by the introductory amendment. Amending acts remain distinct, linked records.
Document dates, entry into force, reviewed service applicability, and Minos
observation dates remain separate. A latest consolidated text is a reading
shortcut, not automatic selection of a ruleset for an earlier service date.

Immutable schema `1` generations still restore byte-for-byte. Their missing
lineage is not inferred from act numbers; the UI labels this legacy limitation
until a fresh successful BAW run publishes schema `2`. Deployment must run BAW
once before claiming that production grouping or expanded capture is active.

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

On 2026-09-14 ICD-9 PL was `5.89`, generation `92`, published and announced
2026-09-01:
<https://www.nfz.gov.pl/aktualnosci/aktualnosci-centrali/opublikowano-nowa-wersje-slownika-icd-9-pl-cm-w-wersji-5-89,9006.html>.
This is a historical baseline, not a current-version pin. On 2026-09-29,
anonymous `getListSlowniki` selected generation `93`, and its pinned XML
identified ICD-9 PL `5.90` with SHA-256
`e3c744634e68f23d5ecbfecb50ece831140eb8533ab72b8a1b3ee54594024b1b`.
The watcher records the discovered generation and exact pinned bytes rather than
hard-coding either historical version.
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
On 2026-09-29, anonymous `getListSlowniki` selected 2026 SZP generation `102`;
its pinned XML had SHA-256
`22595df09b3aac1af31a2db5450267fc9c352e7f30672079ab767e78f78611c1`.
The official version-info page
<https://slowniki.nfz.gov.pl/WersjeSlownikow/Index/7364?kod=MODTAR>
explicitly links display version `202664` to technical generation `102`,
publication date `2026-09-24`, and dictionary validity start `2026-09-24`.
The earlier version-info page
<https://slowniki.nfz.gov.pl/WersjeSlownikow/Index/7297?kod=MODTAR>
explicitly links display version `202662` to technical generation `100` and
validity start `2026-08-20`. These are dictionary validity dates, not by
themselves reviewed reporting-month or NFZ catalog applicability dates.

The newer issued amendment `102/2026/DSOZ` (BAW document `44041`, signed
2026-09-23, valid from 2026-09-24) and its consolidated act (BAW document
`44042`) supersede the `74/2026`-only snapshot for current legal review.
Section 4(1) of `102/2026` applies the amendment to settlement of services
provided from 2026-07-01, with explicit exceptions: section 4(3) retains the
prior coefficients from section 17(3), (4), (8), (19), and (20) during the
quality-indicator transition; section 4(4) applies the new section 17(6a)
from 2026-10-01; and section 4(5) defers specified catalog rows until
2027-01-01. Its section 5 makes the act effective the day after signing.
The matching CZS dictionary validity start of 2026-09-24 therefore cannot be
used as the applicability date of every row. In particular, the XML still
cites some section 17 provisions that the amendment repeals but temporarily
preserves for settlement. The watcher archives this evidence and never binds
MODTAR generation `102` to a reporting-month ruleset automatically. That
binding belongs to reviewed rule ingestion, with each exception assessed
against the governing act and its relevant catalog row.

The XML maps a modifier symbol and cited legal clause to status, month range,
coefficient, and one or more JGP group codes. This is required for complete
tariff determination and must be pinned in each applicable `ruleset_release`.
The version-info pages provide the display-label and generation link and
dictionary validity dates. The amendment supplies several distinct service
applicability dates. The presence of `mies_od="7"` and `mies_do="12"` rows
alone does not establish that every modifier applied from 2026-07-01.
The governing NFZ act remains controlling for eligibility, combination rules,
and legal applicability; disagreement between the act and `MODTAR` blocks the
release for review rather than selecting either silently.

Issue #7 monitors `MODTAR` metadata and pinned bytes through the same documented
CZS automation interface used for ICD-9. No equivalent current dataset was
found on `dane.gov.pl`.

## ICD-10 PL And RSK

### Archived XML Structure

The checksum-verified #6 capture inspected for #11 contains a UTF-8 `hcd`
document in namespace `http://rsk.rejestrymedyczne.csioz.gov.pl`, not an NFZ
`komunikat`. Since the rsk3 platform RSK exports the same resource under
`https://rsk3.ezdrowie.gov.pl/`; the reader accepts exactly these two, one per
document (#184). Root attributes are `hcd`, `visible`, `closed`, `version`,
`applicationdate`, `registrationdate` and `maxchars`. XML `version="7"` is
distinct from catalog resource version `013`. Root scalar fields are
`expirationreason`, `name`, `common_name`, `codesformat`, `charset`,
`minreusetime`, `languages` and `link`, followed by `nodes`.

Each `node` has one `code` attribute and a `name`, then optional `attributes`
and optional nested `nodes`. Attributes are named text values: `EN`,
`annotationPL` and `annotationEN`. Hierarchy is physical containment, not an
explicit parent-code reference. Preserve it using release-local node identity
and preorder, including empty structural containers and uncoded descriptions.

The 12,738,027-byte pinned XML contains 24,040 nodes: 12,936 coded and 11,104
uncoded. There are 593 unnamed uncoded containers. Maximum node depth is 9;
maximum XML element depth is 21. Of the coded nodes, 1,054 have dotted relative
labels under category nodes, 565 have single-digit modifier labels, and 296
have range labels. Do not concatenate digit modifiers into diagnosis codes or
infer billability from hierarchy position. Repeated raw suffixes are valid
source labels, not globally unique codes. Annotations and uncoded descriptive
text are not automatically alternate concept descriptions.

See [pipeline evidence](../validation/icd10-reference-pipeline.md) for the
source checksum, fixture policy and parser/store acceptance boundaries.

### Catalog And Capture

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

Issue #6 does not omit ICD-9 or ICD-11. It monitors and archives the exact
catalog and `basic-info` responses for every returned HCD, including the RSK
ICD-9 and ICD-11 resources. The public Sources timeline includes file-level
metadata from each observed HCD that exposes files; an HCD with an empty file
list still remains preserved in the private exact metadata observation.

Product-byte acquisition is a separate allowlist. It initially contains only
the `00CD10` XML. Current ICD-9 product-byte acquisition is owned by issue #7
and will use the newer CZS source because the RSK ICD-9 publication is stale.
Full ICD-11 acquisition, ingestion, and search is owned by issue #53. Copying
all 24 currently exposed RSK files (about 316 MiB, mostly ICD-11) before that
selection would broaden licensing and storage without identifying the required
product artifacts. Expanding the byte allowlist requires naming the exact
artifacts and reviewing their product and redistribution boundaries; it does
not require a change to the existing all-HCD metadata monitoring.

ICD-11 PL (#53, 2026-10-22) did not expand the watcher's byte allowlist.
`99ICD1` resource version 002 exposes the MMS export `icd11_2026-01_pl_in.xml`
(117,000,048 bytes), three WHO map workbooks
(`10To11MapdoJednejKategorii`, `10To11MapdowieluKategorii`,
`11To10MapdoJednejKategorii`, all `_2026-01.xlsx`) and three PDFs (print
edition, English MMS, WHO guide). The owner approved one download of the XML
and the workbooks on 2026-10-22; the PDFs are not used. The export is the WHO
API linearization as XML: 28 chapters (`01` to `26`, `V`, `X`), 1,360 uncoded
blocks, 159 windows and 35,664 categories, every text value tagged `pl`.
Against ICD-10 PL `013` the 10-to-11 tables cover 12,288 of 12,371 concepts;
the 83 others are chapter ranges written differently and 2008 codes WHO later
removed. The watcher still observes `99ICD1` metadata; the processor compares
its file UUIDs and sizes with the live pins (`manual icd11 <version>`).

For allowlisted bytes, compare complete file metadata and compute SHA-256.
`uuid + creationDate + sizeBytes` is a useful trigger, not a sufficient content
identity. Re-fetch metadata after download to detect rotation during transfer.
Daily scheduled and catch-up collections download the allowlisted file on the
first observation or when its selected identity or complete file set changes.
Weekly verification and explicit manual collection always re-download and
re-hash it because the source provides no validators. Persisted cursors are
validated against their full-state digest, catalog membership, normalized
resource metadata, and selected product identity before any request; missing
or inconsistent state fails closed rather than being replaced from the current
source. The post-download `00CD10` metadata response is also archived exactly
and must match the pre-download structure before the observation set can
commit.

Production composition registers `RskSourceCollector` for watcher kind `rsk`
with the runtime's bounded blob staging area for both filesystem and R2 archive
backends. The explicit `watcher bootstrap-rsk` command creates the canonical
source only when absent and installs independent durable daily and weekly
verification obligations. Runtime startup validates every enabled source kind
against both the configuration-codec and collector registries and checks the
schedule modes declared by that collector. Empty, disabled-only, unknown, and
partial source compositions fail readiness rather than becoming inert.

Every committed RSK metadata observation also carries a strict public snapshot.
It allowlists only HCD, bounded resource and publisher labels, official file
identity and dates, file type and byte count, the exact metadata-response
SHA-256, and the verified product SHA-256 only for privately archived bytes.
Descriptions, publisher addresses and contacts, tax identifiers, education
links, arbitrary publisher and file URLs, response headers, cursors, object keys,
and signed URLs are excluded. Official origin links are reconstructed only from
the fixed validated RSK file endpoint and a validated UUID.

After recovery reconciliation, the watcher projects at most 512 complete
committed revisions and 8,192 timeline entries through the run's committed
state-revision fence. Canonical JSON is limited to 8 MiB. If a limit requires
truncation, only complete oldest revisions are omitted. The projection is
installed as a read-only filesystem generation and an atomic `current` symlink
before the run is finalized locally. The persisted `heartbeat_pending` state is
a compatibility name for this finalization phase, not a monitoring request.
Only after durable terminal completion does the watcher make a best-effort
self-hosted Uptime Kuma up or down push. A failed push never blocks or changes
the terminal watcher result. The edge mounts only this public root read-only; it
never mounts control SQLite, the private archive, staging, or credentials.

`manifests/redistribution.json` is schema version 1 and currently contains an
empty artifact allowlist. Therefore every provenance entry has
`downloadable=false` and `download_url=null`, and all artifact routes return
404. Positive publication is intentionally absent. Per-request guarded
streaming remains deferred until an approved digest allowlist and application
endpoint exist.

The BAW watcher separately publishes a bounded document and selected-file
summary under `/api/v1/provenance/baw`. It uses its own immutable generation
under the same watcher-writable, edge-read-only public directory. The summary
contains only document IDs, act labels and dates, publisher legal status,
official BAW document links, selected file names and sizes, and verified
checksums. Generated source UUIDs, raw legal responses, private object-store addresses,
control records, and downloadable Minos artifact URLs are not published.
Service applicability stays unknown until reviewed from the governing act.
The `/zrodla` directory can search across BAW and RSK without mixing their
publisher roles or treating observation time as source validity.

## Grouping Builder Dictionaries

Issue #151. The builder searches ward specialties and lists discharge modes
by official name instead of expecting coders to know codes. Both come from
Polish legal acts, excluded from copyright by art. 4 pkt 1 of the copyright
act, pinned as the authentic Dziennik Ustaw PDF and archived by SHA-256:

| Dictionary | Act and annex | Rows |
| --- | --- | ---: |
| Specialty of an organizational unit | Dz. U. z 2019 r. poz. 173, annex 2 | 359 listed, 345 derived |
| Discharge mode | Dz. U. z 2025 r. poz. 1588, annex 4 | 10 |

`manifests/sources/grouping-dictionaries.json` holds reviewed transcriptions;
`tests/fixtures/legal/` holds the `pdftotext -layout` extracts that a unit test
compares row by row, including exact name boundaries. The ELI HTML rendering
of the specialty act merges rows at page breaks (1220, 1554, 2202, 4450, 4754,
7250 disappear into neighbouring cells), so it is not used. Children's codes
follow paragraph 9 section 4 (the odd code one above an adult code) and are
marked as derived. NFZ exclusion tables also use codes outside annex 2 (4920,
9154, 9240, 9999); the builder accepts exact codes. Status was checked in the
Sejm ELI API on 2026-10-08; a later amendment requires a new reviewed pin.

The NFZ watcher reads both pins from the Sejm ELI API on every run (#282). A
new amendment or consolidated text appears on the base act that each pin
consolidates (`DU/2012/594` for specialties, `DU/2019/1207` for discharge
modes), so the watcher compares the base act's "Akty zmieniające" and "Inf. o
tekście jednolitym" with the reviewed sets in
`src/minos/ingest/watchers/eli/client.py`, and the in-force status of both
acts. A repeal ("Uchylenia wynikające z", listed before its effective date
changes the status), a correction ("Sprostowanie") or a Constitutional Tribunal
ruling ("Orzeczenie TK") on either act is a finding too. Only these sections
are validated; others may name Monitor Polski acts and are not read. Any
difference is one `eli_legal_dictionary` change for review; the pin
and the manifest change only through a reviewed re-pin. A base act that no
longer lists the pinned text, or an HTTP 200 that is not ELI JSON (a firewall
in front of the API answers with an HTML page), stops the run without
advancing state. The NFZ projection publishes one `eli` entry per dictionary
(pinned act, base act evidence, in-force status, open findings) for `/zrodla`,
and the processor pushes `data-update` down with `manual eli <dictionary>`
while an entry has findings, so the Telegram alert names the dictionary.

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

## NFZ Monthly JGP Reports (#283)

`dane.gov.pl` dataset `2581` publishes one SpreadsheetML report per reporting
month (`raport_SZP_i_kardiologia_<year>_<month>.xml`, about 57 MB, `CC0 1.0`).
`src/minos/ingest/jgp_statistics.py` reads two of its nine tables; the others
name providers and are not read:

- Table 2: hospitalizations and settled points (the table title says "liczba
  punktów"; points, not złoty) per JGP group code and hospital level (PSZ
  level).
- Table 4: count, mean length of stay and mean patient age per JGP section,
  admission mode (planned or not) and stay character (procedural or
  conservative).

Each table reports three months: the reporting month, the previous month and
the same month a year earlier. A figure keeps its age at the report (0, 1 or
12 months), because NFZ revises recent months as late settlements arrive: July
2026 per group and hospital level matched in 731 cells between the July and
August reports and differed in 3,288 (D02 at national hospitals 112 against
251). Counts below five are suppressed as `<5`, kept as a bound of one to four
and never merged with an empty cell, which means no figure. Points are stored
as integer ten-thousandths (the precision NFZ reports) and mean values as
fixed decimals, so a rebuild gives the same bytes. Every data row must end at
its header's last column and cell indexes must move forward, so a missing or
extra cell cannot move a value into another month. The parser refuses bytes
that differ from the pin, a header or month label that differs from the
reviewed layout, a title that no longer names points, an unknown hospital
level, a repeated row, formatted cell text, numbers outside plain ASCII
notation, any DTD, and more rows than reviewed.

`manifests/sources/jgp-statistics.json` pins the twelve reports for September
2025 to August 2026 (743,499,116 bytes, each with its `dane.gov.pl` resource
ID and publication date). The raw reports are provenance: builds read one
canonical extract per report (about 2.2 MB, 26 MB for all twelve), written by
`scripts/extract_jgp_statistics.py` through the same parser and pinned beside
the raw report. The Data Update processor downloads every live input on each
run under one 600-second deadline, so raw reports in the build would have
raised that download from 147 MB to 890 MB; with extracts it is 173 MB. An
extract must name its report's month and SHA-256, match its reviewed counts,
keep sorted unique rows and agree on month and age, or the build refuses it.
Reference schema 12 stores every figure of every
report (`jgp_group_statistic`, `jgp_section_statistic`) with its report month,
so revisions stay visible; a reader takes the newest report for a month. The
reports from 2025 were published in March 2026, so their figures are more
complete than the month alone suggests, which is why each pin keeps its
publication date.

The annual dataset `3009` is not used for counts: at its granularity 94.5% of
2025 cells are suppressed, so a group's national count spans a range about 3.7
times wide. In August 2026, 734 of 740 reported group codes were groups of the
catalog in force and the other six were groups of earlier Minos catalog
releases.

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
| `#7` | NFZ HTML discovery, general provider news, Komunikaty JGP, CZS ICD-9 and `MODTAR`, projects, AOTMiT signals, and Sejm ELI status of the legal grouping dictionaries (#282) |
| `#8` | BAW JSON document graph, original/amending/consolidated packages, attachment bytes, dates, and hashes |

Issue #7 dispatches discovered BAW IDs to #8. It does not duplicate BAW file
retrieval. Issue #8 does not discover unrelated NFZ news or own CZS.

Implementation order is #49, then completion of #6, then #8, then #7. Issue #49
establishes the SQLite control plane, local and R2 blob contracts, recovery
images, and Mikr.us scheduler. Issue #8 supplies the working BAW dispatch target
that issue #7 subsequently calls.

Issues #6-#8 stop at exact raw observations, selected source bytes, provenance,
change records, and incidents. They do not parse terminology or JGP facts into
the generated SQLite reference database. Issue #21 owns verified change selection,
pinned manifest proposals, logical diff validation and automatic activation of
policy-qualified routine data-only updates. Failed or significant candidates
retain the last good release and produce an actionable Telegram alert.

### Freshness Review 2026-10-13 (#184)

- ICD-10 PL 013: RSK now serves a re-export (UUID `d38bfc6b...`, SHA-256
  `4a82a176...`, 12,738,069 bytes) beside the pinned one (`b8dd1741...`,
  `f3dc9811...`), which still downloads byte for byte. Parsed, all 48,058
  classification events are identical; only the namespace, the root `version`
  attribute (7 to 13) and the export `link` differ. The deployed pin stays;
  re-pinning would rename the release without a content change.
- MODTAR: generation 103 is pinned and bound (see `jgp-modifiers-ingest.md`).
- Monitoring coverage: the public BAW projection lists parent documents and
  selected files, not the 30 pinned product-catalog attachments. Their
  freshness follows from the parent document and attachment metadata, which
  showed no act after 102/2026; their bytes are not re-checked by the watcher.
  The two ELI legal dictionary PDFs (specialties, discharge modes) were not
  watched; since #282 the NFZ watcher reads their status and amendments from
  the Sejm ELI API (see Grouping Builder Dictionaries).

## Archive And Failure Rules

1. Poll with bounded timeouts, low concurrency, descriptive user agent, and
   bounded exponential backoff with jitter. Honor `Retry-After` when present.
2. Archive exact successful metadata observations before deriving normalized
   state. Empty or sharply reduced results are schema-drift alerts.
3. Stream allowlisted bytes while hashing. Validate declared and received byte
   counts, magic, extension, archive safety, and parser bounds.
4. Address exact response bodies and downloaded files by SHA-256. Use
   single-part conditional `PutObject` with exact `ContentLength` and the
   Base64-encoded whole-object SHA-256 in `ChecksumSHA256`; R2 bucket lock rules
   refuse overwrite and deletion under blob prefixes. Derive the requested key from the
   local digest; after create, `412` or `409 ObjectLockedByBucketPolicy` (an
   existing key under a lock rule), verify checksum and byte count with
   checksum-enabled `HeadObject`. Retry other `409` conflicts within the bounded
   operation. Content addressing, create-once writes and the bucket lock enforce
   the application-level write-once rule. Filenames and mutable URLs are metadata,
   not identity.
5. Fetch metadata before and after a download batch. A changed source view
   invalidates that attempt rather than publishing a mixed observation.
6. Advance SQLite state only after every referenced R2 blob passes that
   verification. Commit the observation, artifact references, change or incident
   record, source cursor, incremented state revision, claim fencing token, and
   `committed` run state in one local transaction. A post-upload crash may leave
   a harmless unreferenced blob; retry reuses it only after re-verification.
7. Preserve publication, entry-into-force, service-applicability, first-seen,
   fetch, and ingestion times separately. Never invent one from another.
8. Source collectors emit a bounded change record or incident. Issue #21 may
   automatically activate a routine data-only candidate after all reviewed
   policy gates pass, using only exact verified archive pins. Capture alone
   never promotes production rules. Failed or significant candidates stop for
   investigation, retain the last good release and notify the owner.
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
