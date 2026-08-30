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

No WHO ICD dataset is covered by these notices unless it is added later with
its own reviewed terms.
