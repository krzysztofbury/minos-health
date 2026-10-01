"""Synthetic CZS source and manifest, usable without pytest in wheel smoke."""

import hashlib
import json
from pathlib import Path
from typing import cast

from minos.ingest.reference_manifest import DatasetManifest, SourceManifest

XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<komunikat typ="ICD9" wersja="1.1" symbol="5.90" edycja="5" instytucja="NFZ"
 czas_pub="2026-09-24T08:35:45" nr_gen="93" czas_gen="2026-09-24T08:58:10">
 <pozycja_slownika kod="A" nazwa="Synthetic chapter" status="A"/>
 <pozycja_slownika kod="AA" nazwa="Synthetic parent" status="A"/>
 <pozycja_slownika kod="A01" nazwa="  Badanie moczu  " status="N"
  opis="Synthetic note" wlaczenia="Includes evidence" wylaczenia="Excludes evidence">
  <daty_proc data_od="2008-07-01" data_do="2026-09-24"/>
 </pozycja_slownika>
 <pozycja_slownika kod="00.00001" nazwa="Synthetic numeric procedure" status="A">
  <daty_proc data_od="2026-09-24"/>
 </pozycja_slownika>
</komunikat>"""


def source_manifest(content: bytes = XML) -> SourceManifest:
    dataset = DatasetManifest.model_validate_json(
        (
            Path(__file__).parents[2] / "manifests/sources/icd10-synthetic.json"
        ).read_bytes()
    )
    original = cast(dict[str, object], dataset.sources[0].model_dump(mode="json"))
    original.update(
        release_id="icd9-czs-synthetic-v1",
        system="icd9-pl",
        origin="czs",
        source_version="5.90",
        source_file_id="ICD9:93",
        source_url="https://slowniki.nfz.gov.pl/WersjeSlownikow/PobierzXml?kodSlownika=ICD9&nrGen=93",
        sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
        expected={"nodes": 4, "relations": 0, "concepts": 4, "descriptions": 4},
    )
    return SourceManifest.model_validate_json(json.dumps(original))
