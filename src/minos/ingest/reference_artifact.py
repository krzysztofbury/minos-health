"""Build a complete local bundle from reviewed content-addressed archive bytes."""

import hashlib
import os
import platform
import sqlite3
import tempfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO, cast

from minos.domain.reference import ArtifactError, SourceError
from minos.ingest.archive_source import verified_source as verified_source
from minos.ingest.icd9 import parse_icd9_xml
from minos.ingest.icd10 import ReferenceEvent, parse_icd10
from minos.ingest.reference_manifest import DatasetManifest, SourceManifest
from minos.store.artifact import (
    ARTIFACT_MANIFEST_NAME,
    DATABASE_NAME,
    MAX_MANIFEST_BYTES,
    REVIEW_SUMMARY_NAME,
    ArtifactManifest,
    canonical_json,
    file_identity,
)
from minos.store.reference_build import (
    finalize_reference,
    import_release,
    initialize_reference,
    schema_checksum,
    schema_version_for,
)


def build_reference_artifact(
    manifest: DatasetManifest,
    archive_root: Path,
    output: Path,
    *,
    previous_bundle: Path | None = None,
) -> ArtifactManifest:
    """Publish a fresh directory containing a validated DB and checksum manifest.

    No output is overwritten. A complete nonempty bundle is renamed only after
    all work succeeds. Concurrent publication to the same output is unsupported;
    an existing valid bundle also prevents directory replacement at rename time.
    """
    if output.exists() or output.is_symlink():
        raise ArtifactError("reference output already exists")
    for source in manifest.sources:
        if (source.system, source.origin) not in (
            ("icd10-pl", "rsk"),
            ("icd9-pl", "czs"),
            ("icd9-pl", "nfz-workbook"),
        ):
            raise SourceError("unsupported source adapter")
    try:
        with tempfile.TemporaryDirectory(
            prefix=".minos-reference-", dir=output.parent
        ) as temporary:
            candidate = Path(temporary)
            database_path = candidate / DATABASE_NAME
            counts, options = _build_database(database_path, manifest, archive_root)
            database_path.chmod(0o600)
            checksum, size_bytes = file_identity(database_path)
            summary = _review_summary(manifest, counts, previous_bundle, database_path)
            _write_synced(candidate / REVIEW_SUMMARY_NAME, summary)
            result = ArtifactManifest(
                schema_version=schema_version_for(manifest),
                database_sha256=checksum,
                database_size_bytes=size_bytes,
                schema_sha256=schema_checksum(schema_version_for(manifest)),
                review_summary_sha256=hashlib.sha256(summary).hexdigest(),
                dataset=manifest,
                row_counts=counts,
                python_version=platform.python_version(),
                sqlite_version=sqlite3.sqlite_version,
                sqlite_compile_options=options,
                built_at=datetime.now(UTC),
            )
            _write_synced(candidate / ARTIFACT_MANIFEST_NAME, canonical_json(result))
            with database_path.open("rb") as database:
                os.fsync(database.fileno())
            # Existing nonempty directories cannot be replaced by os.rename.
            if output.exists() or output.is_symlink():
                raise ArtifactError("reference output appeared during construction")
            os.rename(candidate, output)
            return result
    except sqlite3.Error as error:
        raise ArtifactError("reference database construction failed") from error
    except OSError as error:
        raise ArtifactError(
            "reference bundle construction or publication failed"
        ) from error


def _build_database(
    database: Path,
    manifest: DatasetManifest,
    archive_root: Path,
) -> tuple[dict[str, int], tuple[str, ...]]:
    connection = sqlite3.connect(database, autocommit=True)
    try:
        _ = connection.execute("PRAGMA foreign_keys = ON")
        _ = connection.execute("PRAGMA journal_mode = DELETE")
        _ = connection.execute("PRAGMA synchronous = FULL")
        _ = connection.execute("PRAGMA page_size = 4096")
        _ = connection.execute("PRAGMA max_page_count = 131072")
        _ = connection.execute("PRAGMA cache_size = -8192")
        _ = connection.execute("PRAGMA mmap_size = 0")
        _ = connection.execute("PRAGMA temp_store = FILE")
        options = tuple(
            row[0]
            for row in cast(
                list[tuple[str]],
                connection.execute("PRAGMA compile_options").fetchall(),
            )
        )
        _ = connection.execute("BEGIN IMMEDIATE")
        initialize_reference(connection, manifest)
        for source in sorted(manifest.sources, key=lambda item: item.release_id):
            with verified_source(
                archive_root, source, staging_directory=database.parent
            ) as snapshot:
                import_release(connection, source, _source_events(snapshot, source))
        from minos.store.komunikat_build import import_komunikat

        for komunikat in sorted(manifest.komunikaty, key=lambda item: item.release_id):
            import_komunikat(connection, komunikat, archive_root, database.parent)
        counts = finalize_reference(connection)
        _ = connection.execute("COMMIT")
        return counts, options
    finally:
        try:
            if connection.in_transaction:
                _ = connection.execute("ROLLBACK")
        finally:
            connection.close()


def _source_events(
    snapshot: BinaryIO, source: SourceManifest
) -> Iterator[ReferenceEvent]:
    if (source.system, source.origin) == ("icd10-pl", "rsk"):
        return parse_icd10(snapshot)
    if (source.system, source.origin) == ("icd9-pl", "czs"):
        return parse_icd9_xml(snapshot, source)
    if (source.system, source.origin) == ("icd9-pl", "nfz-workbook"):
        from minos.ingest.icd9_workbook import parse_icd9_workbook

        return parse_icd9_workbook(snapshot, source)
    raise SourceError("unsupported source adapter")


def _review_summary(
    manifest: DatasetManifest,
    counts: dict[str, int],
    previous_bundle: Path | None,
    database_path: Path,
) -> bytes:
    from minos.store.reference import open_reference_connection, read_artifact_manifest

    previous: ArtifactManifest | None = None
    if previous_bundle is not None:
        with open_reference_connection(previous_bundle):
            previous = read_artifact_manifest(previous_bundle)
    lines = [
        "Minos reference artifact review",
        "",
        "Count/provenance summary only, not a semantic or legal applicability diff.",
        "Source containment is not is_a or billability.",
        "Dotted suffixes use explicit parent context; markers remain in source fields.",
        "Numeric modifiers and uncoded text are preserved, not invented diagnosis codes.",
        "",
    ]
    if previous is None:
        lines.append("Initial build; no previous artifact selected.")
    else:
        lines.append(f"Previous database SHA-256: {previous.database_sha256}")
    for table, count in sorted(counts.items()):
        old_count = previous.row_counts.get(table, 0) if previous else 0
        lines.append(f"{table}: {old_count} -> {count} ({count - old_count:+d})")
    lines.append("")
    for source in sorted(manifest.sources, key=lambda item: item.release_id):
        lines.append(
            f"Source: {source.system} / {source.release_id} / {source.source_version}"
        )
        lines.append(f"Pinned bytes: {source.sha256} / {source.size_bytes}")
        lines.append(f"License: {source.license}")
        lines.extend(f"Notice: {notice}" for notice in source.notices)
    from minos.ingest.reference_reconciliation import reconciliation_lines

    lines.extend(reconciliation_lines(database_path, manifest))
    for item in manifest.komunikaty:
        lines.append(
            f"Algorithm evidence: {item.release_id} / {item.algorithm_version}"
        )
        lines.append(
            "Reference-only code correspondence; not service-date ruleset pairing."
        )
        lines.append(f"Canonical ICD-9 reference: {item.icd9_release_id}")
        for label, file in (
            ("algorithm", item.algorithm),
            ("ranks", item.ranks),
            ("specialties", item.specialties),
        ):
            lines.append(f"Pinned {label}: {file.sha256} / {file.size_bytes}")
    return ("\n".join(lines) + "\n").encode()


def _write_synced(path: Path, content: bytes) -> None:
    if len(content) > MAX_MANIFEST_BYTES:
        raise ArtifactError("reference manifest exceeds size limit")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as target:
        if target.write(content) != len(content):
            raise ArtifactError("reference manifest write was incomplete")
        target.flush()
        os.fsync(target.fileno())
