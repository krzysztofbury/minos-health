# Minos Notices

## Repository Materials

Copyright 2026 Minos contributors.

Software, infrastructure, tests, and project documentation are licensed under
either the Apache License, Version 2.0 or the MIT License, at your option.
These licenses do not grant rights to bundled or generated medical datasets.

## NFZ Normative-Act Data

JGP group catalogs, group characteristics, correction coefficients, the
grouper algorithm description, procedure rank tables, and the ICD-9 PL
procedure classification are derived from materials published by Narodowy
Fundusz Zdrowia:

- zarządzenia Prezesa NFZ in the leczenie szpitalne (DSOZ) family, published
  through <https://baw.nfz.gov.pl> (current base act: `46/2026/DSOZ`);
- komunikaty JGP:
  <https://www.nfz.gov.pl/dla-swiadczeniodawcy/jednorodne-grupy-pacjentow/komunikaty-jgp/>;
- ICD-9 PL through Centralne Zasoby Słownikowe:
  <https://slowniki.nfz.gov.pl/Help/WersjaSlownikow#pobieranieAutomatyczne>,
  cross-checked against the NFZ ICD-9 download page and announcements.

These are treated as official documents and materials within the meaning of
Art. 4 of the Polish Act on Copyright and Related Rights, pending a formal
legal opinion recorded in
[data and licensing](docs/v0.0.1/data-and-licensing.md). Every generated
dataset records the exact act numbers, versions, file identities, retrieval
dates, and checksums it was built from. NFZ has not endorsed Minos.

## Polish ICD-10 Data

The full ICD-10 PL dictionary is derived from the Polish Register of Coding
Systems (Rejestr Systemów Kodowania, RSK):

- resource: ICD-10, HCD `00CD10`;
- selected source version: `013`;
- publisher recorded by RSK: Biuro Światowej Organizacji Zdrowia w Polsce
  (`WHO PL`);
- source validity timestamp: `2025-10-16T22:00:00Z` (which falls on
  `2025-10-17` in Poland);
- source XML label observed during planning: `ICD10_2008_PL_v2026_07_20`;
- source XML UUID: `b8dd1741-c048-489e-84d6-47a915425b2d`;
- source XML size: `12738027` bytes;
- source XML creation timestamp recorded by RSK: `2026-08-05T10:43:22.502Z`;
- source metadata:
  <https://rsk3.ezdrowie.gov.pl/api/rsk/resource/basic-info/00CD10>;
- license quoted in the source metadata: `CC BY-NC-SA 3.0 IGO` (the quoted
  sentence attaches to the WHO 2019 English edition; the scope for the Polish
  translation is under review).

RSK states that the Polish translation was not prepared by WHO and that WHO is
not responsible for its content or accuracy. It identifies the 2019 English
edition, *International Statistical Classification of Diseases and Related
Health Problems, 10th Revision*, as the binding and authentic edition. This
warning must accompany every generated dataset and user-facing provenance
view.

Every distributed dataset and container that includes this resource must carry
the exact source release, retrieval date, archive-time source checksum,
generated artifact checksum, transformation version, attribution, modification
notice, license reference, and a statement that the publisher has not endorsed
Minos.

## Statistics

JGP utilization statistics, when integrated, come from the public NFZ API at
<https://api.nfz.gov.pl> and require attribution to that source under its
terms.

## ICD-11 PL And WHO Mapping Tables

The pre-released Polish ICD-11 translation (MMS 2026-01) comes from the RSK
resource HCD `99ICD1` and is licensed under CC BY-ND 4.0
(<https://creativecommons.org/licenses/by-nd/4.0/>). The translation was not
made by WHO, WHO is not responsible for it, and the English version is binding.
Minos reproduces titles, inclusions and index terms word for word, with each
item's code, title and WHO URI, and adds no ICD-11 text. It is not in force and
not used for NFZ settlement.

The ICD-10/ICD-11 mapping tables are WHO's own tables, published in the same
RSK resource and licensed under CC BY-ND 3.0 IGO
(<https://creativecommons.org/licenses/by-nd/3.0/igo/>). Minos republishes them
verbatim, row for row, attributed to WHO and RSK, and produces no mappings of
its own. Neither WHO nor RSK endorses Minos. The licensing analysis is in
[decisions](docs/v0.0.1/decisions.md).

## Reviewed Polish WordNet Phrases

Two terminology expansions use same-synset phrases from Open Multilingual
Wordnet `omw-pl:2.0`, the Princeton-aligned subset of plWordNet 2.3, copyright
2015 Wrocław University of Technology. They do not represent the current full
Słowosieć database. The pinned archive identity and individual synsets are
recorded in the reviewed synonym manifest. The complete upstream licence is
preserved in `src/minos/ingest/wordnet-license.txt`, included in generated
bundle review notices and exposed in release metadata. Medical sense selection
and whole-query restrictions are Minos modifications, without endorsement by
the source authors. The optional build-time `wn==1.1.1` library is MIT-licensed;
it is not an API runtime dependency.

## Polish Morphology

Search uses Morfeusz2 (Copyright 2014-2021 Institute of Computer Science,
Polish Academy of Sciences) with the embedded SGJP dictionary (Copyright
2007-2026 Marcin Woliński, Zbigniew Bronk, Włodzimierz Gruszczyński, Witold
Kieraś, Zygmunt Saloni, Danuta Skowrońska, Robert Wołosz), both under the
BSD-2-Clause license. Their unmodified notices are in `src/minos/licenses/` and
travel with the wheel and standalone builds. Upstream terms:
<https://morfeusz.sgjp.pl/doc/license/en>.

## Vendored JavaScript

The website vendors MiniSearch 7.2.0 (MIT), unmodified, with its license in
`apps/web/vendor/minisearch-LICENSE.txt`.

## Web Fonts

The website self-hosts subsets of Atkinson Hyperlegible Next and Atkinson
Hyperlegible Mono (Copyright 2020-2024 The Atkinson Hyperlegible Next and
Atkinson Hyperlegible Mono Project Authors), licensed under the SIL Open Font
License 1.1. The licence text and subsetting details are in `apps/web/fonts/`
and the licence is published with the fonts at `/fonts/OFL.txt`.
