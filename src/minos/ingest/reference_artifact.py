"""Build a complete local bundle from reviewed content-addressed archive bytes."""

import hashlib
import os
import platform
import sqlite3
import stat
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO, cast

from minos.domain.reference import ArtifactError, SourceError
from minos.ingest.icd10 import parse_icd10
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
    REFERENCE_SCHEMA_VERSION,
    finalize_reference,
    import_release,
    initialize_reference,
    schema_checksum,
)


@contextmanager
def verified_source(
    archive_root: Path, source: SourceManifest, *, staging_directory: Path
) -> Generator[BinaryIO]:
    """Snapshot and verify before parsing, preventing hash/parse file rotation."""
    blob_path = archive_root / "blobs" / "sha256" / source.sha256
    try:
        descriptor = os.open(
            blob_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
        )
        with (
            os.fdopen(descriptor, "rb") as archived,
            tempfile.TemporaryFile("w+b", dir=staging_directory) as snapshot,
        ):
            details = os.fstat(archived.fileno())
            if (
                not stat.S_ISREG(details.st_mode)
                or details.st_size != source.size_bytes
            ):
                raise SourceError(
                    "pinned source byte count or file type does not match"
                )
            digest = hashlib.sha256()
            consumed = 0
            while chunk := archived.read(64 * 1024):
                consumed += len(chunk)
                if consumed > source.size_bytes:
                    raise SourceError("pinned source grew during verification")
                digest.update(chunk)
                if snapshot.write(chunk) != len(chunk):
                    raise SourceError("source snapshot write was incomplete")
            if consumed != source.size_bytes or digest.hexdigest() != source.sha256:
                raise SourceError("pinned source checksum or byte count does not match")
            _ = snapshot.seek(0)
            yield snapshot
    except OSError as error:
        raise SourceError("pinned archive source cannot be read or staged") from error


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
        if source.system != "icd10-pl" or source.origin != "rsk":
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
            summary = _review_summary(manifest, counts, previous_bundle)
            _write_synced(candidate / REVIEW_SUMMARY_NAME, summary)
            result = ArtifactManifest(
                schema_version=REFERENCE_SCHEMA_VERSION,
                database_sha256=checksum,
                database_size_bytes=size_bytes,
                schema_sha256=schema_checksum(),
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
                import_release(connection, source, parse_icd10(snapshot))
        counts = finalize_reference(connection)
        _ = connection.execute("COMMIT")
        return counts, options
    finally:
        try:
            if connection.in_transaction:
                _ = connection.execute("ROLLBACK")
        finally:
            connection.close()


def _review_summary(
    manifest: DatasetManifest, counts: dict[str, int], previous_bundle: Path | None
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
        old_count = previous.row_counts[table] if previous else 0
        lines.append(f"{table}: {old_count} -> {count} ({count - old_count:+d})")
    lines.append("")
    for source in sorted(manifest.sources, key=lambda item: item.release_id):
        lines.append(
            f"Source: {source.system} / {source.release_id} / {source.source_version}"
        )
        lines.append(f"Pinned bytes: {source.sha256} / {source.size_bytes}")
        lines.append(f"License: {source.license}")
        lines.extend(f"Notice: {notice}" for notice in source.notices)
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
