"""Small synthetic Office sources and source-backed condition phrase fixtures."""

import hashlib
import json
from collections.abc import Callable
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, cast

from minos.domain import jgp_conditions as c
from minos.ingest.jgp_conditions_manifest import ConditionsManifest

if TYPE_CHECKING:
    from openpyxl.worksheet.worksheet import Worksheet

ROWS_1A = (
    ("A", "A01", "A01 Synthetic group"),
    (
        "A",
        "A01",
        "wymagane wskazanie procedury z listy procedur A01 oraz rozpoznania zasadniczego z listy rozpoznań A01",
    ),
    ("A", "A01", "ICD-9"),
    ("A", "A01", "100.01 Synthetic procedure"),
    ("A", "A01", "99.97910 Synthetic long code"),
    ("A", "A01", "ICD-10"),
    ("A", "A01", "G60.4 Synthetic unmatched diagnosis"),
    ("A", "A01", "G60.4 Duplicate source occurrence"),
    (
        "A",
        "A01",
        "E11.0 Synthetic title containing wiek < 18 r.ż. lub czas pobytu > 2 dni",
    ),
    ("N", "N09", "N09 Synthetic KOC characteristics"),
    ("N", "N09", "(dotyczy wyłącznie KOC)"),
    ("N", "N11", "N11 Synthetic KOC characteristics"),
    ("N", "N11", "(dotyczy wyłącznie KOC)"),
    ("", "Lista Ogólna", "Lista Ogólna"),
    ("", "Lista Ogólna", "ICD-10"),
    ("", "Lista Ogólna", "E11.0 Synthetic general list member"),
)
ROWS_1AE = (
    ("A", "AU01", "AU01 Synthetic AE group"),
    ("A", "AU01", "wymagane wskazanie rozpoznania zasadniczego z Listy Ogólnej"),
    ("", "Lista Ogólna", "Lista Ogólna"),
    ("", "Lista Ogólna", "ICD-10"),
    ("", "Lista Ogólna", "G60.4 AE-only general list member"),
)


def workbook(
    *, original: bool = False, edit: Callable[[Worksheet], None] | None = None
) -> bytes:
    from openpyxl import Workbook

    book = Workbook()
    active = book.active
    assert active is not None
    book.remove(active)
    specifications = (
        (("Charakterystyka", ROWS_1A),)
        if original
        else (("Charakterystyka 1a", ROWS_1A), ("Charakterystyka 1ae", ROWS_1AE))
    )
    for name, rows in specifications:
        sheet = cast("Worksheet", book.create_sheet(name))
        if not original:
            sheet.append((None, None, None))
        sheet.append(
            (
                "* w przypadku filtrowania należy uwzględnić wszystkie elementy zawarte w etykiecie grupy JGP",
                None,
                None,
            )
        )
        sheet.append(
            (
                "Sekcja",
                "Kod JGP*/\nlisty dodatkowej/\nlisty ogólnej",
                "Charakterystyka JGP"
                if original
                else "Charakterystyka JGP " + name.rsplit(" ", 1)[1],
            )
        )
        for row in rows:
            sheet.append(row)
        if edit is not None:
            edit(sheet)
    content = BytesIO()
    book.save(content)
    book.close()
    return content.getvalue()


def manifest(content: bytes, *, original: bool = False) -> ConditionsManifest:
    counts_1a = {
        "groups": 3,
        "evidence_cells": 16,
        "unbound_member_occurrences": 0,
        "lists": 3,
        "member_occurrences": 6,
        "unique_members": 5,
        "conditions": 5,
        "kind_counts": {
            "all": 1,
            "diagnosis_presence": 1,
            "procedure_presence": 1,
            "program_membership": 2,
        },
        "unsupported_reasons": {},
        "unmatched_codes": 3,
    }
    counts_1ae = {
        "groups": 1,
        "evidence_cells": 5,
        "unbound_member_occurrences": 0,
        "lists": 1,
        "member_occurrences": 1,
        "unique_members": 1,
        "conditions": 1,
        "kind_counts": {"diagnosis_presence": 1},
        "unsupported_reasons": {},
        "unmatched_codes": 1,
    }
    return ConditionsManifest.model_validate_json(
        json.dumps(
            {
                "release_id": "conditions-original"
                if original
                else "conditions-current",
                "baw_document_id": 43868 if original else 44042,
                "lineage_document_id": 43868,
                "act_number": "46/2026/DSOZ" if original else "102/2026/DSOZ",
                "layout": "nfz-9-46-2026" if original else "nfz-9-102-2026",
                "valid_from": "2026-05-01" if original else "2026-07-01",
                "valid_to": "2026-06-30" if original else None,
                "published_on": "2026-04-30" if original else "2026-09-23",
                "applicability_evidence": "Synthetic independently reviewed attachment applicability",
                "publisher": "Synthetic NFZ fixture",
                "license": "Synthetic fixture, not publisher source bytes",
                "notices": [
                    "Reference evidence, not executable grouping or settlement."
                ],
                "icd9_release_id": "icd9-czs-synthetic-v1",
                "icd10_release_id": "icd10-synthetic-v1",
                "correspondence": "source_local_exact_reference",
                "workbook": {
                    "source_url": "https://baw.nfz.gov.pl/api/file/4/33027",
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "size_bytes": len(content),
                    "fetched_at": "2026-10-03T10:00:00Z",
                },
                "sheets": {"1a": counts_1a}
                if original
                else {"1a": counts_1a, "1ae": counts_1ae},
            }
        )
    )


def stage(root: Path, *, original: bool = False) -> ConditionsManifest:
    content = workbook(original=original)
    selected = manifest(content, original=original)
    path = root / "blobs" / "sha256" / selected.workbook.sha256
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_bytes(content)
    return selected


def phrase_sheet(
    group: str,
    text: str,
    *,
    title: str | None = None,
    lists: tuple[tuple[str, c.System], ...] = (),
) -> c.Characteristics:
    evidence = [
        c.Evidence(4, "X", group, title or group + " Synthetic group", "title"),
        c.Evidence(5, "X", group, text, "label"),
    ]
    definitions: list[c.DirectionalList] = []
    for label, system in lists or (("Lista Ogólna", "icd10-pl"),):
        row = 6 + len(definitions) * 2
        code = "00.36" if system == "icd9-pl" else "E11.0"
        evidence.extend(
            (
                c.Evidence(
                    row,
                    "X",
                    label,
                    "ICD-9" if system == "icd9-pl" else "ICD-10",
                    "marker",
                ),
                c.Evidence(row + 1, "X", label, code + " Synthetic member", "member"),
            )
        )
        definitions.append(
            c.DirectionalList(
                len(definitions) + 1,
                label,
                system,
                label,
                row,
                (),
                (c.Member(row + 1, code, ()),),
            )
        )
    return c.Characteristics(
        "1a",
        "Charakterystyka 1a",
        tuple(evidence),
        (c.Group(group, 4, ()),),
        tuple(definitions),
        (),
        (),
    )
