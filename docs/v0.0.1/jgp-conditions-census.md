# Reproduce The #29 Attachment 9 Census

This is a read-only research recipe for the feature-frequency table in
[the taxonomy](jgp-conditions.md), not the #30 condition parser. It does not
produce conditions, resolve ICD references, bind a ruleset or evaluate a case.
Its explicit lexical signals count overlapping evidence families. They do not
determine boolean scope, cardinality semantics or clinical eligibility.

Use the repository's locked developer environment (`uv sync --all-groups`).
Copy the code block to a disposable scratch file, then run `uv run python
PATH_TO_SCRIPT DOCUMENT_ID PATH_TO_PINNED_WORKBOOK`, once for each document
43868, 43943 and 44042. Use checksum-matching private archived bytes or capture
the exact official file endpoints listed in the taxonomy. A different digest
requires another source review, not an updated expected count.

```python
import collections
import hashlib
import json
import re
import sys
from pathlib import Path

import openpyxl

from minos.ingest.workbook_archive import WorkbookLimits, preflight_workbook

PINS = {
    "43868": (
        "915c13de1b40ced7a45ce61d371b66ea5a1bb0aceb76d3162a7f880dca73636b",
        {"Charakterystyka": 702},
    ),
    "43943": (
        "f5e360996b3c2b9fb2d12469a1fd06576ebb9cb20a4856162a0341771bfb5dcf",
        {"Charakterystyka 1a": 627, "Charakterystyka 1ae": 121},
    ),
    "44042": (
        "618a476ab3d11f9935a2a74d66db1ce77b2fb5376af776d36afa189f712405c4",
        {"Charakterystyka 1a": 629, "Charakterystyka 1ae": 121},
    ),
}
LABEL = re.compile(
    r"^(?:wymaga|Wymaga|wskazanie|lub wymagane|W przypadku|\(\*|"
    r"\* NYHA|\*\* LVEF|\*\*\* EGFR|\*\*\*\* wymagane|"
    r"\(dotyczy wyłącznie KOC)"
)
SIGNALS = {
    "procedure_presence": r"procedur|proceudr",
    "procedure_count": (
        r"\b(?:[2-9]|dwóch|dwie|trzech|trzy|czterech|cztery|pięciu|pięć|"
        r"sześciu|sześć|siedmiu|siedem)\s+(?:różnych\s+)?(?:z\s+)?procedur|"
        r"dwukro|pięciokro|min\.\s*3 krotnie|min 2 razy"
    ),
    "procedure_all_members": r"wszystkich procedur|obu procedur",
    "procedure_list_coverage": (r"procedur pochodzących z (?:różnych|rożnych) list"),
    "procedure_date_relation": r"w innym terminie",
    "procedure_laterality": r"jednokrotnie dla piersi prawej",
    "procedure_duration": r"czas trwania min 24h",
    "diagnosis_presence": r"rozpozna|rozpozan",
    "diagnosis_count": r"(?:2|dwóch) rozpoznań współistniejąc",
    "patient_age": r"\bwiek\b",
    "episode_duration": r"czas (?:pobytu|hospitalizacji)\s*[<>]",
    "ward_duration": r"czas pobytu w OINK",
    "discharge_mode": r"tryb wypisu",
    "clinical_measure": r"NYHA|LVEF|EGFR|ECOG|mMRC",
    "program_membership": r"dotyczy wyłącznie KOC",
    "external_disease_code": r"ORPHA",
}

if len(sys.argv) != 3 or sys.argv[1] not in PINS:
    raise SystemExit("expected DOCUMENT_ID PINNED_WORKBOOK")
document_id = sys.argv[1]
path = Path(sys.argv[2])
digest, expected_groups = PINS[document_id]
if not 1 <= path.stat().st_size <= 32 * 1024 * 1024:
    raise ValueError("source size outside research bound")
if openpyxl.__version__ != "3.1.5":
    raise ValueError("workbook reader version conflict")

with path.open("rb") as stream:
    if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
        raise ValueError("source checksum conflict")
    stream.seek(0)
    layouts = preflight_workbook(stream, limits=WorkbookLimits(max_rows=50_000))
    if set(layouts) != set(expected_groups):
        raise ValueError("worksheet identities conflict")
    stream.seek(0)
    book = openpyxl.load_workbook(
        stream, read_only=True, data_only=False, keep_links=False
    )
    try:
        for sheet in book:
            layout = layouts[sheet.title]
            sheet.reset_dimensions()
            groups = collections.defaultdict(list)
            for number, cells in enumerate(
                sheet.iter_rows(max_row=layout.rows, max_col=layout.columns), 1
            ):
                if any(cell.data_type in ("f", "e", "d") for cell in cells):
                    raise ValueError("unsupported formula, error or date cell")
                code, text = cells[1].value, cells[2].value
                if (
                    isinstance(code, str)
                    and isinstance(text, str)
                    and LABEL.match(text)
                ):
                    groups[code].append((number, text))
            if len(groups) != expected_groups[sheet.title]:
                raise ValueError("reviewed group denominator conflict")
            inventory = {
                code: {
                    kind: [
                        row
                        for row, text in labels
                        if re.search(pattern, text, re.IGNORECASE)
                    ]
                    for kind, pattern in SIGNALS.items()
                }
                for code, labels in sorted(groups.items())
            }
            counts = {
                kind: sum(bool(kinds[kind]) for kinds in inventory.values())
                for kind in SIGNALS
            }
            print(
                json.dumps(
                    {
                        "document": document_id,
                        "sheet": sheet.title,
                        "groups": len(groups),
                        "counts": counts,
                        "rare_feature_cells": {
                            kind: {
                                code: kinds[kind]
                                for code, kinds in inventory.items()
                                if kinds[kind]
                            }
                            for kind in SIGNALS
                            if counts[kind] <= 12
                        },
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
    finally:
        book.close()
```

The `wymaga` prefix deliberately selects the raw P35 `wymagagne` spelling;
`proceudr` selects DD02's source spelling without replacing it. The `dwukro`
prefix also matches J02BO's `dwukrone`. These are capture-specific research
signals, not a production correction dictionary. The whole selected cell is
inspected, and regex matching does not rewrite authoritative source text.

The recipe excludes code/title member rows, section titles and list headings.
Standalone `lub` is not counted as an atomic condition. It deliberately does
not count boolean operators from arbitrary occurrences of `lub` inside titles.
Rare-feature cell lists provide a compact manual cross-check. Completeness of
the taxonomy also requires reviewing the remaining label clauses, interleaved
annotations and shared-list structure; passing these counters alone cannot
establish a complete importer.

For the structural comparison, retain each nonblank column-C string and its
one-based cell under the exact column-B context, in physical order. Compare
that ordered sequence between 43943 and 44042, without case-folding, spelling
correction or sorting members. Ignore a cell only when its value is absent or
whitespace-only. This yields the changed contexts listed in the taxonomy;
compare label strings separately from whole contexts to distinguish ten changed
group labels from twenty changed 1a contexts. Do not infer medical-semantic
equivalence from a textual diff.
