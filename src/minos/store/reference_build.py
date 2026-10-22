"""Fresh immutable reference database construction; no watcher state access."""

import hashlib
import json
import platform
import sqlite3
from collections.abc import Iterable
from functools import cache
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
KOMUNIKAT_MIGRATION = "003_komunikat.sql"
KOMUNIKAT_TABLES = ("algorithm_release", "procedure_rank", "department_specialty")
CATALOG_MIGRATION = "004_jgp_catalog.sql"
CATALOG_TABLES = (
    "catalog_knowledge",
    "catalog_release",
    "catalog_sheet",
    "catalog_scope",
    "group_definition",
    "group_base_value",
    "group_scope",
)
CONDITIONS_MIGRATION = "005_jgp_conditions.sql"
MODIFIERS_MIGRATION = "006_jgp_modifiers.sql"
BINDING_MIGRATION = "007_jgp_modifier_binding.sql"
FOLDED_MIGRATION = "008_folded_search.sql"
DICTIONARIES_MIGRATION = "009_grouping_dictionaries.sql"
PRODUCTS_MIGRATION = "010_product_catalogs.sql"
ICD11_MAPS_MIGRATION = "011_icd11_maps.sql"
MODIFIERS_TABLES = (
    "modifier_release",
    "tariff_modifier",
    "modifier_period",
    "modifier_group",
    "modifier_candidate",
)
CONDITIONS_TABLES = (
    "characteristics_release",
    "characteristics_knowledge",
    "characteristics_sheet",
    "characteristics_evidence",
    "characteristics_group",
    "characteristics_group_catalog",
    "characteristics_catalog_row",
    "directional_list",
    "characteristics_code",
    "characteristics_code_evidence",
    "directional_member",
    "condition_selector",
    "group_condition",
)
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


@cache
def reference_sql(relative_path: str) -> str:
    # Packaged SQL is immutable; rereading it per query cost more than the SQL.
    packaged = files("minos").joinpath("database/reference/sqlite", relative_path)
    if packaged.is_file():
        return packaged.read_text(encoding="utf-8")
    from pathlib import Path

    repository = Path(__file__).resolve().parents[3]
    return (repository / "database/reference/sqlite" / relative_path).read_text(
        encoding="utf-8"
    )


def schema_version_for(
    manifest: DatasetManifest,
) -> Literal[2, 3, 4, 5, 6, 7, 8, 9, 10, 11]:
    # Each schema is a superset of the previous one, so older bundles stay valid.
    # Conditions build as 6 even without MODTAR: the dated condition consumers
    # rely on schema 6's lookup indexes. Schema 5 bundles stay readable.
    if manifest.icd11_maps is not None:
        return 11
    if manifest.product_catalogs:
        return 10
    if manifest.grouping_dictionaries is not None:
        return 9
    if manifest.search_profile is not None:
        return 8
    if manifest.modifier_bindings:
        return 7
    if manifest.modifiers or manifest.characteristics:
        return 6
    if manifest.catalogs:
        return 4
    return 3 if manifest.komunikaty else 2


def migrations_for(schema_version: int) -> tuple[str, ...]:
    if schema_version not in (2, 3, 4, 5, 6, 7, 8, 9, 10, 11):
        raise ArtifactError("incompatible reference schema version")
    return (
        MIGRATIONS
        + ((KOMUNIKAT_MIGRATION,) if schema_version >= 3 else ())
        + ((CATALOG_MIGRATION,) if schema_version >= 4 else ())
        + ((CONDITIONS_MIGRATION,) if schema_version >= 5 else ())
        + ((MODIFIERS_MIGRATION,) if schema_version >= 6 else ())
        + ((BINDING_MIGRATION,) if schema_version >= 7 else ())
        + ((FOLDED_MIGRATION,) if schema_version >= 8 else ())
        + ((DICTIONARIES_MIGRATION,) if schema_version >= 9 else ())
        + ((PRODUCTS_MIGRATION,) if schema_version >= 10 else ())
        + ((ICD11_MAPS_MIGRATION,) if schema_version >= 11 else ())
    )


def tables_for(schema_version: int) -> tuple[str, ...]:
    _ = migrations_for(schema_version)
    return (
        TABLES
        + (KOMUNIKAT_TABLES if schema_version >= 3 else ())
        + (CATALOG_TABLES if schema_version >= 4 else ())
        + (CONDITIONS_TABLES if schema_version >= 5 else ())
        + (MODIFIERS_TABLES if schema_version >= 6 else ())
        + (
            ("modifier_binding", "modifier_binding_window")
            if schema_version >= 7
            else ()
        )
        + (
            (
                "search_word_folded",
                "search_jgp_member",
                "search_jgp_count",
                "search_synonym",
            )
            if schema_version >= 8
            else ()
        )
        + (
            ("grouping_dictionary_release", "specialty_code", "discharge_mode")
            if schema_version >= 9
            else ()
        )
        + (
            ("product_catalog_release", "product_catalog_member")
            if schema_version >= 10
            else ()
        )
        + (("icd11_map_release", "icd11_map_row") if schema_version >= 11 else ())
    )


def schema_checksum(schema_version: int = REFERENCE_SCHEMA_VERSION) -> str:
    return hashlib.sha256(
        "".join(
            reference_sql("migrations/" + name)
            for name in migrations_for(schema_version)
        ).encode()
    ).hexdigest()


def migration_identities(
    schema_version: int = REFERENCE_SCHEMA_VERSION,
) -> list[tuple[int, str]]:
    return [
        (
            index,
            hashlib.sha256(reference_sql("migrations/" + name).encode()).hexdigest(),
        )
        for index, name in enumerate(migrations_for(schema_version), 1)
    ]


def initialize_reference(
    connection: sqlite3.Connection, manifest: DatasetManifest
) -> None:
    """Apply reviewed migrations only inside a fresh-build transaction."""
    if not connection.in_transaction:
        raise ArtifactError("reference schema requires a build transaction")
    version = schema_version_for(manifest)
    for name, identity in zip(
        migrations_for(version), migration_identities(version), strict=True
    ):
        for statement in reference_sql("migrations/" + name).split(";"):
            if statement.strip():
                _ = connection.execute(statement)
        _ = connection.execute("INSERT INTO schema_migration VALUES (?, ?)", identity)
    metadata = {
        "schema_version": str(version),
        "normalization_version": NORMALIZATION_VERSION,
        "code_conversion_version": "rsk-icd10-1",
        "transform_version": manifest.transform_version,
        "lockfile_sha256": manifest.lockfile_sha256,
        "python_version": platform.python_version(),
        "sqlite_version": sqlite3.sqlite_version,
    }
    supporting_sources = {
        source.release_id: source.context_for
        for source in manifest.sources
        if source.context_for is not None
    }
    if supporting_sources:
        metadata["supporting_sources"] = json.dumps(
            supporting_sources, sort_keys=True, separators=(",", ":")
        )
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
    _ = connection.execute(f"PRAGMA user_version = {version}")


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
    if node.ordinal < 1 or node.ordinal > 200_000:
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
        reference_sql("queries/search_jgp_build.sql"),
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
    result = dict(counts)
    version = cast(tuple[int], connection.execute("PRAGMA user_version").fetchone())[0]
    if version >= 10:
        result.update(
            cast(
                list[tuple[str, int]],
                connection.execute(
                    """SELECT 'product_catalog_release', count(*) FROM product_catalog_release
                    UNION ALL SELECT 'product_catalog_member', count(*) FROM product_catalog_member"""
                ).fetchall(),
            )
        )
    if version >= 11:
        result.update(
            cast(
                list[tuple[str, int]],
                connection.execute(
                    """SELECT 'icd11_map_release', count(*) FROM icd11_map_release
                    UNION ALL SELECT 'icd11_map_row', count(*) FROM icd11_map_row"""
                ).fetchall(),
            )
        )
    if version >= 9:
        result.update(
            cast(
                list[tuple[str, int]],
                connection.execute(
                    """SELECT 'grouping_dictionary_release', count(*) FROM grouping_dictionary_release
                    UNION ALL SELECT 'specialty_code', count(*) FROM specialty_code
                    UNION ALL SELECT 'discharge_mode', count(*) FROM discharge_mode"""
                ).fetchall(),
            )
        )
    if version >= 8:
        result["search_synonym"] = cast(
            tuple[int],
            connection.execute("SELECT count(*) FROM search_synonym").fetchone(),
        )[0]
        result.update(
            cast(
                list[tuple[str, int]],
                connection.execute(
                    "SELECT 'search_jgp_member',count(*) FROM search_jgp_member UNION ALL SELECT 'search_jgp_count',count(*) FROM search_jgp_count"
                ).fetchall(),
            )
        )
        result["search_word_folded"] = cast(
            tuple[int],
            connection.execute("SELECT count(*) FROM search_word_folded").fetchone(),
        )[0]
    if version >= 7:
        result.update(
            cast(
                list[tuple[str, int]],
                connection.execute(
                    """SELECT 'modifier_binding', count(*) FROM modifier_binding
            UNION ALL SELECT 'modifier_binding_window', count(*) FROM modifier_binding_window"""
                ).fetchall(),
            )
        )
    if version >= 6:
        for table in MODIFIERS_TABLES:
            result[table] = cast(
                tuple[int],
                connection.execute(f"SELECT count(*) FROM {table}").fetchone(),  # nosec B608
            )[0]
    if version >= 5:
        for table in CONDITIONS_TABLES:
            # Identifiers come only from the fixed schema tuple, never input.
            result[table] = cast(
                tuple[int],
                connection.execute(f"SELECT count(*) FROM {table}").fetchone(),  # nosec B608
            )[0]
    if version >= 4:
        result.update(
            cast(
                list[tuple[str, int]],
                connection.execute(
                    """SELECT 'catalog_knowledge', count(*) FROM catalog_knowledge
            UNION ALL SELECT 'catalog_release', count(*) FROM catalog_release
            UNION ALL SELECT 'catalog_sheet', count(*) FROM catalog_sheet
            UNION ALL SELECT 'catalog_scope', count(*) FROM catalog_scope
            UNION ALL SELECT 'group_definition', count(*) FROM group_definition
            UNION ALL SELECT 'group_base_value', count(*) FROM group_base_value
            UNION ALL SELECT 'group_scope', count(*) FROM group_scope"""
                ).fetchall(),
            )
        )
    if version >= 3:
        result.update(
            cast(
                list[tuple[str, int]],
                connection.execute(
                    """SELECT 'algorithm_release', count(*) FROM algorithm_release
            UNION ALL SELECT 'procedure_rank', count(*) FROM procedure_rank
            UNION ALL SELECT 'department_specialty', count(*) FROM department_specialty"""
                ).fetchall(),
            )
        )
    return result
