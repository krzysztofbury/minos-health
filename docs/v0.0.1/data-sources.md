# Data Sources

Status: researched 2026-08-06. Facts below are point-in-time observations;
items marked NIEPEWNE are inferences or third-party statements that must be
verified against primary sources before being relied on.

This document is operational: where each dataset lives, how it changes, and
how the watchers detect and archive those changes. The legal analysis lives in
[data-and-licensing.md](data-and-licensing.md).

## Overview

| Dataset | Authoritative source | Format | Observed cadence |
| --- | --- | --- | --- |
| JGP katalog (zał. 1a) and charakterystyka (zał. 9) | Zarządzenia Prezesa NFZ, DSOZ szpitalne family, via BAW | XLSX (merged-cell headers) | 5-8 amendments/yr; base act reissue ~2 yrs |
| Grouper algorithm and procedure ranks | NFZ komunikaty JGP | DOCX + XLSX | algorithm version valid per reporting month |
| ICD-9 PL | NFZ dictionary pages; XML via slowniki.nfz.gov.pl | XLSX + XML | 3-6 versions/yr |
| ICD-10 PL | RSK, HCD `00CD10` | XML | ~3-4 versions/yr (NIEPEWNE, inferred from version counter) |
| JGP utilization statistics | api.nfz.gov.pl | JSON:API | annual datasets |
| Tariff signals | AOTMiT obwieszczenia (BIP) | PDF | leading indicator, irregular |

No source offers RSS, webhooks, or documented change feeds. All change
detection is polling.

## NFZ Zarządzenia (JGP Rules And Points)

- Current base act: zarządzenie `101/2025/DSOZ` (issued 2025-12-23)
  "w sprawie określenia warunków zawierania i realizacji umów w
  rodzaju leczenie szpitalne oraz leczenie szpitalne - świadczenia
  wysokospecjalistyczne".
  Announcement: <https://www.nfz.gov.pl/zarzadzenia-prezesa/zarzadzenia-prezesa-nfz/zarzadzenie-nr-1012025dsoz,7902.html>.
  Full content: <https://baw.nfz.gov.pl/NFZ/document/43718/Zarzadzenie-101_2025_DSOZ>.
- Base-act lineage: `1/2022/DSOZ` (2022-01-03), `37/2024/DSOZ` (2024-03-29),
  `101/2025/DSOZ` (2025-12-23). Amendments replace individual załączniki; a
  new base act renumbers and restructures (always human review).
- Key załączniki: `1a` katalog grup JGP (point values per settlement mode:
  hospitalizacja, leczenie jednego dnia, hospitalizacja poniżej 3 dni,
  osobodzień ponad ryczałt, plus specialty-scope matrix); `1b/1c/1d/1ts/1w/1ws`
  auxiliary product catalogs; `1ae` short-stay catalog added by `74/2026/DSOZ`
  (NIEPEWNE, via mbrk.pl); `9` charakterystyka JGP (per-group directional
  conditions with complete ICD-10 and ICD-9 lists, age and length-of-stay
  conditions). Verified: załącznik 9 carries ICD-10 codes with full Polish
  titles.
- Correction coefficients (współczynniki korygujące) and their combination
  formula live in the legal prose of the base act, not in spreadsheets. They
  become curated overlay data with `manual` provenance.
- Effective dates decouple from publication dates, sometimes retroactively:
  `48/2025/DSOZ` published 2025-06-30 applies from 2025-07-01; `74/2026/DSOZ`
  published 2026-07-23, reportedly effective 2026-07-01 (NIEPEWNE, mbrk.pl).
  Store both dates on every release.
- 2026 amendments observed in this family: `14/2026` (2026-02-09), `32/2026`
  (2026-03-18), `74/2026` (2026-07-23); additional numbers possible
  (NIEPEWNE).

Watcher: poll the server-rendered listing
<https://www.nfz.gov.pl/zarzadzenia-prezesa/zarzadzenia-prezesa-nfz/1,strona.html?szukaj=szpitaln>
and match the exact DSOZ family title; the filter also catches DGL/DSM/PSZ
streams, which are explicitly out of ingestion scope. Advance notice appears
at <https://www.nfz.gov.pl/zarzadzenia-prezesa/projekty-zarzadzen/>.

File retrieval: announcement pages since 2022-01-28 link only to BAW
(`baw.nfz.gov.pl`), an AngularJS SPA behind Incapsula whose internal REST
endpoints return the SPA shell to plain HTTP clients. Downloads of załączniki
and tekst ujednolicony therefore require a headless-browser step keyed by BAW
document ID. Direct ZIP URLs exist only for pre-2022 acts.

## NFZ Komunikaty JGP (Grouper Algorithm)

- Page: <https://www.nfz.gov.pl/dla-swiadczeniodawcy/jednorodne-grupy-pacjentow/komunikaty-jgp/>.
- Current: algorithm version `1.1.2026`, komunikat of 2026-04-22, published
  under § 10 of the base act. Files: `opis_algorytmu_grupera_2026.docx`
  (grouping steps, exclusion rules, procedure rank system, condition
  evaluation, tariff determination), `tabela_1` specialty codes XLSX,
  `tabela_2` procedure ranks XLSX.
- Versioning scheme `X.Y.ZZZZ`: one version per reporting month; mid-year
  bumps possible. The reporting month binds the algorithm version, ICD-9
  version, and załącznik versions together; this pairing defines a
  `ruleset_release`.
- The published description is implementable but descriptive: the binding
  settlement result is produced by NFZ's own claim-verification systems.
  Divergence risk is handled by the golden-case suite (see SPEC).

Watcher: poll the komunikaty page for new entries and file links.

## ICD-9 PL

- Authoritative and freshest: <https://www.nfz.gov.pl/dla-swiadczeniodawcy/slowniki/pliki-icd-9-pl/>,
  per-version XLSX (current `5.88`, 2026-07-23; ~60 historical files back to
  `5.20`). XML: <https://slowniki.nfz.gov.pl> (Centralne Zasoby Słownikowe).
- RSK mirrors ICD-9 PL as HCD `00CD09` but lags: RSK held `5.82` (valid from
  2026-01-07) while NFZ published `5.88`. RSK is a secondary source for this
  dataset.
- Which ICD-9 version binds for a reporting period is announced by the Prezes
  NFZ per § 10 of the base act; the watcher pairs versions rather than
  assuming latest.

Watcher: poll the NFZ page (predictable XLSX link pattern) and slowniki XML.

## ICD-10 PL (RSK)

- Catalog: `GET https://rsk3.ezdrowie.gov.pl/api/rsk/resource/role/UZYNLG`
  returns all 8 anonymously visible resources with current versions.
- Per-resource: `GET /api/rsk/resource/basic-info/{hcd}` returns current
  version, validity dates, publisher, and the `files[]` array (UUID, name,
  size, creation date, extension). No license field, no checksums, no version
  history (the archive endpoint is login-gated).
- Download: `GET /api/rsk/file/get/{uuid}`, anonymous. No ETag, no
  Last-Modified, Range ignored; full re-download is the only option (ICD-10
  XML: 12,738,027 bytes).
- Current ICD-10: HCD `00CD10`, version `013`, publisher Biuro WHO w Polsce,
  XML UUID `b8dd1741-c048-489e-84d6-47a915425b2d`.
- Critical behaviour, observed 2026-08-05: **files are replaced in place
  without a version bump** (the ICD-10 XML was recreated while `version`
  stayed `013`). Change detection must diff
  `files[].uuid + creationDate + sizeBytes`, never the `version` field alone.
  Because history is anonymously unreachable once a file rotates, the archive
  step is urgent and unconditional.

Watcher: daily, 1 catalog call plus 8 basic-info calls (a few KB each);
download and archive only on detected change; compute SHA-256 at archive time.

## NFZ Statistics API

- <https://api.nfz.gov.pl/app-stat-api-jgp/>: JSON:API, hospitalization
  statistics per JGP group (by gender, age, admission/discharge, length-of-stay
  histograms, ICD-9 and ICD-10 breakdowns). Terms: 10 requests/s per IP,
  source attribution required, no data modification, no warranty.
- This API serves statistics about JGP usage, not grouper definitions or
  point values. Planned use: group-level utilization context in the UI
  (Later).

## AOTMiT Obwieszczenia (Tariff Signals)

- <https://bip.aotm.gov.pl/taryfikacja-swiadczen/860-taryfikacja-obwieszczenia>,
  PDF only. AOTMiT tariff announcements lead NFZ katalog changes (katalog 1a
  carries an explicit "Taryfa ustalona przez AOTMiT" column). Watch as a
  leading indicator; no parsing commitment in `v0.0.1`.

## Watcher And Archive Rules

1. Poll daily. Every fetched raw file is archived immediately to private
   versioned S3 keyed by source, version, and SHA-256 computed at archive
   time, with an independent encrypted QNAP copy on separate credentials.
2. On any detected change, open a data PR updating the manifest with a
   human-readable diff. Never auto-merge; never let CI follow `latest`.
3. Alert on watcher failure, on new załącznik names or format changes, and on
   any silent file replacement.
4. An empty poll result obtained through a changed page structure is suspect:
   verify the selector still matches before concluding "no changes".
5. Store publication date and effective date separately on every release;
   effective dates may precede publication.
