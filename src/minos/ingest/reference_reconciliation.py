"""Count-only reconciliation of separate ICD-9 evidence, without source merging."""

import sqlite3
from pathlib import Path
from typing import cast

from minos.domain.reference import ArtifactError
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


def conditions_reconciliation_lines(
    database: Path, manifest: DatasetManifest
) -> list[str]:
    if not manifest.characteristics:
        return []
    connection = sqlite3.connect(
        database.resolve().as_uri() + "?mode=ro&immutable=1", uri=True
    )
    try:
        codes = cast(
            list[tuple[str, str, str, str, int, int]],
            connection.execute(
                """SELECT local.release_id, local.catalog, local.system, local.correspondence,
                count(*), sum((SELECT count(*) FROM characteristics_code_evidence AS evidence
                    WHERE evidence.release_id=local.release_id AND evidence.catalog=local.catalog
                      AND evidence.system=local.system AND evidence.code=local.code))
            FROM characteristics_code AS local
            GROUP BY local.release_id, local.catalog, local.system, local.correspondence
            ORDER BY local.release_id, local.catalog, local.system, local.correspondence LIMIT 65"""
            ).fetchall(),
        )
        catalogs = cast(
            list[tuple[str, str, str, int]],
            connection.execute(
                """SELECT release_id, catalog, correspondence, count(*)
            FROM characteristics_group_catalog GROUP BY release_id, catalog, correspondence
            ORDER BY release_id, catalog, correspondence LIMIT 49"""
            ).fetchall(),
        )
        if len(codes) > 64 or len(catalogs) > 48:
            raise ArtifactError("characteristics review summary exceeds release bounds")
        lines = [
            "",
            "Attachment 9 correspondence: exact source identities, not clinical equivalence.",
        ]
        lines.extend(
            f"{release} / {catalog} / {system} / {status}: {count} distinct codes; {occurrences} code/cell evidence edges"
            for release, catalog, system, status, count, occurrences in codes
        )
        lines.extend(
            f"{release} / {catalog} / {status}: {count} characteristics groups"
            for release, catalog, status, count in catalogs
        )
        return lines
    finally:
        connection.close()
