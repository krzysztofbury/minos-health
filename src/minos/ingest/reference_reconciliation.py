"""Count-only reconciliation of separate ICD-9 evidence, without source merging."""

import sqlite3
from pathlib import Path
from typing import cast

from minos.ingest.reference_manifest import DatasetManifest
from minos.store.reference_build import reference_sql


def reconciliation_lines(database: Path, manifest: DatasetManifest) -> list[str]:
    catalogs = [
        source
        for source in manifest.sources
        if source.system == "icd9-pl" and source.origin == "czs"
    ]
    workbooks = [
        source
        for source in manifest.sources
        if source.system == "icd9-pl" and source.origin == "nfz-workbook"
    ]
    if not workbooks:
        return []
    lines = [
        "",
        "ICD-9 source reconciliation: count evidence only; no corrections or cross-source graph joins.",
    ]
    connection = sqlite3.connect(
        database.resolve().as_uri() + "?mode=ro&immutable=1", uri=True
    )
    try:
        for workbook in workbooks:
            matched = False
            for catalog in catalogs:
                if catalog.source_version != workbook.source_version:
                    continue
                matched = True
                counts = cast(
                    tuple[int, int, int, int, int],
                    connection.execute(
                        reference_sql("queries/icd9_reconciliation.sql"),
                        {
                            "catalog": catalog.release_id,
                            "workbook": workbook.release_id,
                        },
                    ).fetchone(),
                )
                lines.extend(
                    [
                        f"Compared: {catalog.release_id} / {workbook.release_id}",
                        f"CZS-only codes: {counts[0]}; workbook-only codes: {counts[1]}",
                        f"Shared codes with different preferred source text: {counts[2]}",
                        f"Workbook conflicting title/code-form evidence nodes: {counts[3]}",
                        f"CZS-only status A entries: {counts[4]}",
                        "No hierarchy/status/title correction applied. Workbook does not supply source status or procedure dates.",
                    ]
                )
            if not matched:
                lines.append(
                    f"Workbook {workbook.release_id}: no same-version CZS release selected; not reconciled."
                )
    finally:
        connection.close()
    return lines
