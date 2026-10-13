"""Verified read-only reference access with typed, bounded hierarchy queries."""

import hashlib
import json
import os
import sqlite3
import stat
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Literal, cast, final

from pydantic import ValidationError

from minos.domain.normalization import NORMALIZATION_VERSION
from minos.domain.reference import (
    ArtifactError,
    Concept,
    ConceptDescription,
    HierarchyNode,
    NodeKind,
    QueryLimitError,
    RelationType,
)
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
    REFERENCE_APPLICATION_ID,
    migration_identities,
    reference_sql,
    schema_checksum,
    schema_version_for,
    tables_for,
)

# (database SHA-256, dataset SHA-256) pairs whose conditions this process has
# already reconstructed and verified; bounded by the bundles a process opens.
_VALIDATED_CONDITIONS: set[tuple[str, str]] = set()
_VALIDATED_MODIFIERS: set[tuple[str, str]] = set()
_VALIDATED_MODIFIER_BINDINGS: set[tuple[str, str]] = set()
_VALIDATED_SEARCH_SYNONYMS: set[tuple[str, str]] = set()
# Database files whose SHA-256 this process verified, by stat identity. A pool
# opens several connections; re-hashing ~445 MB for each dominated host startup
# under the 512 MiB container limit (#189). A replaced or rewritten file has a
# different identity and is hashed again.
_VERIFIED_DATABASES: dict[tuple[int, int, int, int, int], tuple[str, int]] = {}


def _stat_identity(path: Path) -> tuple[int, int, int, int, int]:
    # ctime is included because utime can restore mtime but never ctime.
    value = os.stat(path, follow_symlinks=False)
    return (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _verify_database(database: Path, expected: tuple[str, int]) -> None:
    before = _stat_identity(database)
    if _VERIFIED_DATABASES.get(before) == expected:
        return
    identity = file_identity(database, max_bytes=expected[1])
    if identity != expected or _stat_identity(database) != before:
        raise ArtifactError("reference database checksum or byte count does not match")
    if len(_VERIFIED_DATABASES) >= 16:
        _VERIFIED_DATABASES.clear()
    _VERIFIED_DATABASES[before] = identity


def read_artifact_manifest(bundle: Path) -> ArtifactManifest:
    try:
        descriptor = os.open(
            bundle / ARTIFACT_MANIFEST_NAME, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        )
        with os.fdopen(descriptor, "rb") as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise ArtifactError("reference manifest is not a regular file")
            content = source.read(MAX_MANIFEST_BYTES + 1)
        if len(content) > MAX_MANIFEST_BYTES:
            raise ArtifactError("reference manifest exceeds size limit")
        manifest = ArtifactManifest.model_validate_json(content)
        if content != canonical_json(manifest):
            raise ArtifactError("reference manifest is not canonical")
        if manifest.schema_version != schema_version_for(manifest.dataset):
            raise ArtifactError("reference manifest schema and source policy conflict")
        if manifest.schema_sha256 != schema_checksum(manifest.schema_version):
            raise ArtifactError("incompatible reference schema checksum")
        if set(manifest.row_counts) != set(tables_for(manifest.schema_version)):
            raise ArtifactError("invalid reference table counts")
        summary_sha256, _ = file_identity(
            bundle / REVIEW_SUMMARY_NAME, max_bytes=MAX_MANIFEST_BYTES
        )
        if summary_sha256 != manifest.review_summary_sha256:
            raise ArtifactError("reference review summary checksum does not match")
        return manifest
    except (OSError, ValidationError) as error:
        raise ArtifactError("reference manifest cannot be read or validated") from error


@contextmanager
def open_reference_connection(bundle: Path) -> Generator[sqlite3.Connection]:
    """Low-level read-only access for reference queries and local diagnostics."""
    with open_reference_bundle(bundle) as (connection, _manifest):
        yield connection


@contextmanager
def open_reference_bundle(
    bundle: Path,
    *,
    check_same_thread: bool = True,
) -> Generator[tuple[sqlite3.Connection, ArtifactManifest]]:
    """Keep output provenance bound to the same manifest used for verification."""
    manifest = read_artifact_manifest(bundle)
    database = bundle / DATABASE_NAME
    try:
        _verify_database(
            database, (manifest.database_sha256, manifest.database_size_bytes)
        )
        connection = sqlite3.connect(
            database.resolve().as_uri() + "?mode=ro&immutable=1",
            uri=True,
            autocommit=True,
            check_same_thread=check_same_thread,
        )
        try:
            _ = connection.execute("PRAGMA query_only = ON")
            _ = connection.execute("PRAGMA trusted_schema = OFF")
            _ = connection.execute("PRAGMA foreign_keys = ON")
            _ = connection.execute("PRAGMA mmap_size = 0")
            _validate_database_header(connection, manifest.schema_version)
            if manifest.schema_version >= 3:
                from minos.store.komunikat_build import validate_komunikat_bindings

                validate_komunikat_bindings(connection, manifest.dataset.komunikaty)
            if manifest.schema_version >= 4:
                from minos.store.jgp_catalog_build import validate_catalog_bindings

                validate_catalog_bindings(connection, manifest.dataset)
            if manifest.schema_version >= 5:
                from minos.store.jgp_conditions_build import (
                    validate_conditions_bindings,
                )

                # Re-interpreting every release takes seconds. The database
                # is immutable and its digest was verified above, so one
                # successful check per process covers every later connection
                # (a pool opens several) for this exact database and dataset.
                key = (
                    manifest.database_sha256,
                    hashlib.sha256(
                        manifest.dataset.model_dump_json().encode()
                    ).hexdigest(),
                )
                if key not in _VALIDATED_CONDITIONS:
                    validate_conditions_bindings(connection, manifest.dataset)
                    _VALIDATED_CONDITIONS.add(key)
            if manifest.schema_version >= 6:
                from minos.store.jgp_modifiers_build import validate_modifiers_bindings

                key = (
                    manifest.database_sha256,
                    hashlib.sha256(
                        manifest.dataset.model_dump_json().encode()
                    ).hexdigest(),
                )
                if key not in _VALIDATED_MODIFIERS:
                    validate_modifiers_bindings(connection, manifest.dataset)
                    if len(_VALIDATED_MODIFIERS) >= 16:
                        _VALIDATED_MODIFIERS.clear()
                    _VALIDATED_MODIFIERS.add(key)
            if manifest.schema_version >= 7:
                from minos.store.jgp_modifier_binding import validate_modifier_bindings

                key = (
                    manifest.database_sha256,
                    hashlib.sha256(
                        manifest.dataset.model_dump_json().encode()
                    ).hexdigest(),
                )
                if key not in _VALIDATED_MODIFIER_BINDINGS:
                    validate_modifier_bindings(connection, manifest.dataset)
                    if len(_VALIDATED_MODIFIER_BINDINGS) >= 16:
                        _VALIDATED_MODIFIER_BINDINGS.clear()
                    _VALIDATED_MODIFIER_BINDINGS.add(key)
            if manifest.schema_version >= 8:
                from minos.store.search_synonyms import validate_search_synonyms

                key = (
                    manifest.database_sha256,
                    hashlib.sha256(
                        manifest.dataset.model_dump_json().encode()
                    ).hexdigest(),
                )
                if key not in _VALIDATED_SEARCH_SYNONYMS:
                    validate_search_synonyms(
                        connection, manifest.dataset.search_synonyms
                    )
                    if len(_VALIDATED_SEARCH_SYNONYMS) >= 16:
                        _VALIDATED_SEARCH_SYNONYMS.clear()
                    _VALIDATED_SEARCH_SYNONYMS.add(key)
            if manifest.schema_version >= 10:
                from minos.store.product_catalogs import validate_product_bindings

                validate_product_bindings(connection, manifest.dataset.product_catalogs)
            expected = {
                source.release_id: source.context_for
                for source in manifest.dataset.sources
                if source.context_for is not None
            }
            policy = cast(
                tuple[str] | None,
                connection.execute(
                    "SELECT value FROM build_metadata WHERE key='supporting_sources'"
                ).fetchone(),
            )
            if (json.loads(policy[0]) if policy else {}) != expected:
                raise ArtifactError("supporting source policy does not match manifest")
            yield connection, manifest
        finally:
            connection.close()
    except (sqlite3.Error, OSError) as error:
        raise ArtifactError("reference database read failed") from error


def _validate_database_header(
    connection: sqlite3.Connection, schema_version: int
) -> None:
    if connection.execute("PRAGMA application_id").fetchone() != (
        REFERENCE_APPLICATION_ID,
    ):
        raise ArtifactError("not a Minos reference database")
    if connection.execute("PRAGMA user_version").fetchone() != (schema_version,):
        raise ArtifactError("incompatible reference schema version")
    if connection.execute(
        "SELECT version, sha256 FROM schema_migration ORDER BY version"
    ).fetchall() != migration_identities(schema_version):
        raise ArtifactError("incompatible reference migration checksum")
    normalization = cast(
        tuple[str] | None,
        connection.execute(
            "SELECT value FROM build_metadata WHERE key = 'normalization_version'"
        ).fetchone(),
    )
    if normalization != (NORMALIZATION_VERSION,):
        raise ArtifactError("incompatible normalization version")
    search = cast(
        tuple[str] | None,
        connection.execute(
            "SELECT value FROM build_metadata WHERE key='search_version'"
        ).fetchone(),
    )
    if search != (("lexical-3",) if schema_version >= 8 else ("lexical-2",)):
        raise ArtifactError("search profile conflicts with reference schema")


@contextmanager
def open_reference(bundle: Path) -> Generator[ReferenceReader]:
    with open_reference_connection(bundle) as connection:
        yield ReferenceReader(connection)


@final
class ReferenceReader:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def concept(self, release_id: str, lookup_code: str) -> Concept | None:
        if (
            not release_id
            or len(release_id) > 80
            or not lookup_code
            or len(lookup_code) > 256
        ):
            raise ValueError("invalid concept lookup identity")
        row = cast(
            tuple[str, int, str, str] | None,
            self._connection.execute(
                """SELECT release.system, concept.node_ordinal, node.source_code, node.kind
            FROM concept JOIN source_release AS release ON release.id = concept.source_release_id
            JOIN reference_node AS node ON node.source_release_id = concept.source_release_id
            AND node.ordinal = concept.node_ordinal
            WHERE concept.source_release_id = ? AND concept.lookup_code = ?""",
                (release_id, lookup_code),
            ).fetchone(),
        )
        if row is None:
            return None
        descriptions = cast(
            list[tuple[str, str, str, str]],
            self._connection.execute(
                """SELECT language, description_type, source_text, normalized_text FROM concept_description
            WHERE source_release_id = ? AND node_ordinal = ? ORDER BY language, description_type, source_text LIMIT 129""",
                (release_id, row[1]),
            ).fetchall(),
        )
        if len(descriptions) > 128:
            raise QueryLimitError("concept descriptions exceed bound")
        return Concept(
            release_id,
            row[0],
            row[1],
            row[2],
            lookup_code,
            _node_kind(row[3]),
            tuple(ConceptDescription(*description) for description in descriptions),
        )

    def hierarchy(
        self,
        release_id: str,
        ordinal: int,
        *,
        direction: Literal["ancestors", "descendants"] = "descendants",
        relation_type: RelationType = "contains",
        max_depth: int = 32,
        max_nodes: int = 1000,
    ) -> tuple[HierarchyNode, ...]:
        if str(direction) not in ("ancestors", "descendants") or str(
            relation_type
        ) not in ("contains", "is_a"):
            raise ValueError("unsupported hierarchy traversal")
        if not release_id or len(release_id) > 80 or not 1 <= ordinal <= 100_000:
            raise ValueError("invalid hierarchy identity")
        if not 1 <= max_depth <= 64 or not 1 <= max_nodes <= 10_000:
            raise ValueError("invalid hierarchy limits")
        sql = reference_sql(f"queries/{direction}.sql")
        deadline = time.monotonic() + 0.25
        steps = 0

        def interrupted() -> int:
            nonlocal steps
            steps += 1000
            return int(steps > 2_000_000 or time.monotonic() > deadline)

        self._connection.set_progress_handler(interrupted, 1000)
        try:
            rows = cast(
                list[tuple[int, int, str, str, str]],
                self._connection.execute(
                    sql,
                    {
                        "release_id": release_id,
                        "ordinal": ordinal,
                        "relation_type": relation_type,
                        "max_depth": max_depth,
                        "row_limit": max_nodes + 1,
                    },
                ).fetchall(),
            )
        except sqlite3.OperationalError as error:
            if error.sqlite_errorcode == sqlite3.SQLITE_INTERRUPT:
                raise QueryLimitError(
                    "hierarchy query exceeded execution limit"
                ) from error
            raise ArtifactError("hierarchy query failed") from error
        finally:
            self._connection.set_progress_handler(None, 0)
        if len(rows) > max_nodes or any(row[1] > max_depth for row in rows):
            raise QueryLimitError("hierarchy exceeds depth or result limit")
        return tuple(
            HierarchyNode(row[0], row[1], row[2], row[3], _node_kind(row[4]))
            for row in rows
        )


def _node_kind(value: str) -> NodeKind:
    kinds: frozenset[str] = frozenset(
        ("classification", "range", "modifier", "text", "container")
    )
    if value not in kinds:
        raise ArtifactError("invalid reference node kind")
    return cast(NodeKind, value)
