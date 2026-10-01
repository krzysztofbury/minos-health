"""Fresh immutable reference database construction; no watcher state access."""

import hashlib
import json
import platform
import sqlite3
from collections.abc import Iterable
from importlib.resources import files
from typing import Literal, cast

from minos.domain.normalization import NORMALIZATION_VERSION, normalize_description
from minos.domain.reference import (
    ArtifactError,
    ConceptDescription,
    ReferenceNode,
    ReferenceRelation,
    SourceHeader,
)
from minos.ingest.reference_manifest import DatasetManifest, SourceManifest, utc_text
from minos.store.search_build import build_search_index

REFERENCE_SCHEMA_VERSION: Literal[2] = 2
MIGRATIONS = ("001_reference.sql", "002_search.sql")
REFERENCE_APPLICATION_ID = 0x4D494E52
TABLES = (
    "source_release",
    "reference_node",
    "reference_node_attribute",
    "reference_relation",
    "concept",
    "concept_description",
    "search_document",
    "search_owner",
    "search_word",
    "search_token",
    "search_word_lemma",
)


def reference_sql(relative_path: str) -> str:
    packaged = files("minos").joinpath("database/reference/sqlite", relative_path)
    if packaged.is_file():
        return packaged.read_text(encoding="utf-8")
    from pathlib import Path

    repository = Path(__file__).resolve().parents[3]
    return (repository / "database/reference/sqlite" / relative_path).read_text(
        encoding="utf-8"
    )


def schema_checksum() -> str:
    return hashlib.sha256(
        "".join(reference_sql("migrations/" + name) for name in MIGRATIONS).encode()
    ).hexdigest()


def migration_identities() -> list[tuple[int, str]]:
    return [
        (
            index,
            hashlib.sha256(reference_sql("migrations/" + name).encode()).hexdigest(),
        )
        for index, name in enumerate(MIGRATIONS, 1)
    ]


def initialize_reference(
    connection: sqlite3.Connection, manifest: DatasetManifest
) -> None:
    """Apply reviewed migrations only inside a fresh-build transaction."""
    if not connection.in_transaction:
        raise ArtifactError("reference schema requires a build transaction")
    for name, identity in zip(MIGRATIONS, migration_identities(), strict=True):
        for statement in reference_sql("migrations/" + name).split(";"):
            if statement.strip():
                _ = connection.execute(statement)
        _ = connection.execute("INSERT INTO schema_migration VALUES (?, ?)", identity)
    metadata = {
        "schema_version": str(REFERENCE_SCHEMA_VERSION),
        "normalization_version": NORMALIZATION_VERSION,
        "code_conversion_version": "rsk-icd10-1",
        "transform_version": manifest.transform_version,
        "lockfile_sha256": manifest.lockfile_sha256,
        "python_version": platform.python_version(),
        "sqlite_version": sqlite3.sqlite_version,
    }
    if any(source.system == "icd9-pl" for source in manifest.sources):
        versions = {"czs": "czs-icd9-1", "nfz-workbook": "nfz-icd9-workbook-1"}
        metadata["icd9_conversion_version"] = ",".join(
            sorted(
                {
                    versions.get(source.origin, "source-defined")
                    for source in manifest.sources
                    if source.system == "icd9-pl"
                }
            )
        )
    _ = connection.executemany(
        "INSERT INTO build_metadata VALUES (?, ?)", sorted(metadata.items())
    )
    _ = connection.execute(f"PRAGMA application_id = {REFERENCE_APPLICATION_ID}")
    _ = connection.execute(f"PRAGMA user_version = {REFERENCE_SCHEMA_VERSION}")


def import_release(
    connection: sqlite3.Connection,
    source: SourceManifest,
    events: Iterable[SourceHeader | ReferenceNode | ReferenceRelation],
) -> None:
    if not connection.in_transaction:
        raise ArtifactError("source import requires a build transaction")
    header_seen = False
    for event_count, event in enumerate(events, start=1):
        if event_count > 300_001:
            raise ArtifactError("source event count exceeds limit")
        if isinstance(event, SourceHeader):
            if header_seen or event_count != 1:
                raise ArtifactError("duplicate or misplaced source header")
            _insert_release(connection, source, event)
            header_seen = True
        elif not header_seen:
            raise ArtifactError("missing source header")
        elif isinstance(event, ReferenceNode):
            _insert_node(connection, source.release_id, event)
        else:
            _ = connection.execute(
                "INSERT INTO reference_relation VALUES (?, ?, ?, ?)",
                (
                    source.release_id,
                    event.parent_ordinal,
                    event.child_ordinal,
                    event.relation_type,
                ),
            )
    if not header_seen:
        raise ArtifactError("empty source event stream")
    _validate_release(connection, source)


def _insert_release(
    connection: sqlite3.Connection, source: SourceManifest, header: SourceHeader
) -> None:
    _ = connection.execute(
        "INSERT INTO source_release VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            source.release_id,
            source.system,
            source.origin,
            source.source_version,
            source.source_file_id,
            source.source_url,
            source.publisher,
            utc_text(source.fetched_at),
            utc_text(source.source_created_at),
            utc_text(source.valid_from),
            utc_text(source.valid_to),
            source.sha256,
            source.size_bytes,
            source.license,
            json.dumps(source.notices, ensure_ascii=False, separators=(",", ":")),
            json.dumps(
                {"attributes": dict(header.attributes), "fields": dict(header.fields)},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        ),
    )


def _insert_node(
    connection: sqlite3.Connection, release_id: str, node: ReferenceNode
) -> None:
    if node.ordinal < 1 or node.ordinal > 100_000:
        raise ArtifactError("source node ordinal exceeds limit")
    if len(node.attributes) > 16:
        raise ArtifactError("source node attributes exceed limit")
    _ = connection.execute(
        "INSERT INTO reference_node VALUES (?, ?, ?, ?, ?)",
        (
            release_id,
            node.ordinal,
            node.source_code,
            node.source_text,
            node.kind,
        ),
    )
    _ = connection.executemany(
        "INSERT INTO reference_node_attribute VALUES (?, ?, ?, ?, ?)",
        (
            (release_id, node.ordinal, order, name, text)
            for order, (name, text) in enumerate(node.attributes, start=1)
        ),
    )
    if node.lookup_code is None:
        if node.kind in ("classification", "range"):
            raise ArtifactError("classification node lacks lookup identity")
        if node.descriptions:
            raise ArtifactError("non-concept node has concept descriptions")
        return
    if node.kind not in ("classification", "range") or not node.source_code:
        raise ArtifactError("non-concept node has lookup identity")
    _ = connection.execute(
        "INSERT INTO concept VALUES (?, ?, ?)",
        (release_id, node.ordinal, node.lookup_code),
    )
    if not 1 <= len(node.descriptions) <= 16:
        raise ArtifactError("concept descriptions exceed limit or are absent")
    if not any(
        description.description_type == "preferred" for description in node.descriptions
    ):
        raise ArtifactError("concept lacks a preferred description")
    for description in node.descriptions:
        _insert_description(connection, release_id, node, description)


def _insert_description(
    connection: sqlite3.Connection,
    release_id: str,
    node: ReferenceNode,
    description: ConceptDescription,
) -> None:
    if description.normalized_text != normalize_description(description.source_text):
        raise ArtifactError("description search key does not match source text")
    _ = connection.execute(
        """INSERT INTO concept_description (source_release_id, node_ordinal, language,
        description_type, source_text, normalized_text) VALUES (?, ?, ?, ?, ?, ?)""",
        (
            release_id,
            node.ordinal,
            description.language,
            description.description_type,
            description.source_text,
            description.normalized_text,
        ),
    )


def _count(connection: sqlite3.Connection, table: str, release_id: str) -> int:
    statements = {
        "reference_node": "SELECT count(*) FROM reference_node WHERE source_release_id = ?",
        "reference_relation": "SELECT count(*) FROM reference_relation WHERE source_release_id = ?",
        "concept": "SELECT count(*) FROM concept WHERE source_release_id = ?",
        "concept_description": "SELECT count(*) FROM concept_description WHERE source_release_id = ?",
    }
    if table not in statements:
        raise ArtifactError("unknown reference table")
    row = cast(
        tuple[int],
        connection.execute(statements[table], (release_id,)).fetchone(),
    )
    return row[0]


def _validate_release(connection: sqlite3.Connection, source: SourceManifest) -> None:
    counts = (
        ("nodes", "reference_node", source.expected.nodes),
        ("relations", "reference_relation", source.expected.relations),
        ("concepts", "concept", source.expected.concepts),
        ("descriptions", "concept_description", source.expected.descriptions),
    )
    for key, table, expected in counts:
        if _count(connection, table, source.release_id) != expected:
            raise ArtifactError(f"reviewed {key} count does not match source")
    bounds = cast(
        tuple[int, int],
        connection.execute(
            "SELECT min(ordinal), max(ordinal) FROM reference_node WHERE source_release_id = ?",
            (source.release_id,),
        ).fetchone(),
    )
    if bounds != (1, source.expected.nodes):
        raise ArtifactError("source ordering is not contiguous")
    # Source containment is preorder. Strict forward edges prove acyclicity
    # without retaining the hierarchy in Python or recursively visiting it twice.
    invalid = cast(
        tuple[int] | None,
        connection.execute(
            """SELECT 1 FROM reference_relation WHERE source_release_id = ? AND
        (relation_type <> 'contains' OR parent_ordinal >= child_ordinal) LIMIT 1""",
            (source.release_id,),
        ).fetchone(),
    )
    if invalid is not None:
        raise ArtifactError("source containment violates preorder or has a cycle")
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise ArtifactError("unresolved reference hierarchy or concept link")


def finalize_reference(connection: sqlite3.Connection) -> dict[str, int]:
    if not connection.in_transaction:
        raise ArtifactError("reference finalization requires a build transaction")
    build_search_index(
        connection,
        reference_sql("queries/search_sources.sql"),
        reference_sql("queries/search_owners.sql"),
    )
    _ = connection.execute(
        "INSERT INTO concept_description_fts(concept_description_fts) VALUES ('rebuild')"
    )
    _ = connection.execute(
        "INSERT INTO concept_description_fts(concept_description_fts, rank) VALUES ('integrity-check', 1)"
    )
    if connection.execute("PRAGMA integrity_check").fetchone() != ("ok",):
        raise ArtifactError("reference database integrity check failed")
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise ArtifactError("reference foreign key check failed")
    counts = cast(
        list[tuple[str, int]],
        connection.execute("""
        SELECT 'source_release', count(*) FROM source_release
        UNION ALL SELECT 'reference_node', count(*) FROM reference_node
        UNION ALL SELECT 'reference_node_attribute', count(*) FROM reference_node_attribute
        UNION ALL SELECT 'reference_relation', count(*) FROM reference_relation
        UNION ALL SELECT 'concept', count(*) FROM concept
        UNION ALL SELECT 'concept_description', count(*) FROM concept_description
        UNION ALL SELECT 'search_document', count(*) FROM search_document
        UNION ALL SELECT 'search_owner', count(*) FROM search_owner
        UNION ALL SELECT 'search_word', count(*) FROM search_word
        UNION ALL SELECT 'search_token', count(*) FROM search_token
        UNION ALL SELECT 'search_word_lemma', count(*) FROM search_word_lemma
    """).fetchall(),
    )
    return dict(counts)
