"""Transactional komunikat evidence import and source-binding verification."""

import sqlite3
from pathlib import Path
from typing import cast

from minos.domain.komunikat import ProcedureRank, first_reporting_month
from minos.domain.reference import ArtifactError
from minos.ingest.archive_source import verified_source
from minos.ingest.komunikat import parse_algorithm, parse_companion
from minos.ingest.komunikat_manifest import KomunikatManifest
from minos.store.artifact import canonical_json


def import_komunikat(
    connection: sqlite3.Connection,
    manifest: KomunikatManifest,
    archive: Path,
    staging: Path,
) -> None:
    if not connection.in_transaction:
        raise ArtifactError("komunikat import requires a build transaction")
    with verified_source(
        archive, manifest.algorithm, staging_directory=staging
    ) as source:
        metadata = parse_algorithm(source, manifest)
    _ = connection.execute(
        "INSERT INTO algorithm_release VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            manifest.release_id,
            manifest.icd9_release_id,
            metadata.version,
            metadata.reporting_from.isoformat(),
            metadata.version_text,
            metadata.paragraph,
            manifest.pairing,
            canonical_json(manifest).decode(),
        ),
    )
    for file, ranks in ((manifest.ranks, True), (manifest.specialties, False)):
        with verified_source(archive, file, staging_directory=staging) as source:
            for event in parse_companion(source, manifest, ranks=ranks):
                if isinstance(event, ProcedureRank):
                    _ = connection.execute(
                        "INSERT INTO procedure_rank VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            manifest.release_id,
                            event.grouper_type,
                            event.code,
                            event.source_title,
                            event.rank,
                            event.worksheet,
                            event.row,
                        ),
                    )
                else:
                    _ = connection.execute(
                        "INSERT INTO department_specialty VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            manifest.release_id,
                            event.grouper_type,
                            event.code,
                            event.service_codes,
                            event.icd_codes,
                            event.worksheet,
                            event.row,
                        ),
                    )


def validate_komunikat_bindings(
    connection: sqlite3.Connection,
    manifests: tuple[KomunikatManifest, ...],
) -> None:
    rows = cast(
        list[tuple[str, str, str, str, str, str]],
        connection.execute(
            "SELECT id, icd9_release_id, algorithm_version, reporting_from, pairing, manifest_json FROM algorithm_release ORDER BY id"
        ).fetchall(),
    )
    expected = {
        manifest.release_id: (
            manifest.icd9_release_id,
            manifest.algorithm_version,
            first_reporting_month(manifest.algorithm_version).isoformat(),
            manifest.pairing,
            canonical_json(manifest).decode(),
        )
        for manifest in manifests
    }
    if {row[0]: row[1:] for row in rows} != expected:
        raise ArtifactError("komunikat source policy does not match manifest")
