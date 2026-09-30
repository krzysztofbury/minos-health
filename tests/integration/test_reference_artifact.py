import hashlib
import json
import os
import sqlite3
from collections.abc import Iterator
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from typing import BinaryIO, cast

import pytest
from pydantic import ValidationError

from minos.domain.reference import (
    ArtifactError,
    ConceptDescription,
    QueryLimitError,
    ReferenceNode,
    ReferenceRelation,
    SourceError,
    SourceHeader,
)
from minos.ingest import reference_artifact as artifact_module
from minos.ingest.icd10 import ReferenceEvent, parse_icd10
from minos.ingest.reference_artifact import build_reference_artifact, verified_source
from minos.ingest.reference_manifest import DatasetManifest
from minos.store.reference import (
    ReferenceReader,
    open_reference,
    open_reference_connection,
)
from minos.store.reference_build import (
    finalize_reference,
    import_release,
    initialize_reference,
)

ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "icd10-pl"
INPUT_MANIFEST = ROOT / "manifests" / "sources" / "icd10-synthetic.json"


def stage_source(root: Path, content: bytes | None = None) -> DatasetManifest:
    original = DatasetManifest.model_validate_json(INPUT_MANIFEST.read_bytes())
    data = cast(dict[str, object], json.loads(original.model_dump_json()))
    sources = cast(list[dict[str, object]], data["sources"])
    if content is None:
        content = (FIXTURES / "minimal.xml").read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    sources[0]["sha256"] = digest
    sources[0]["size_bytes"] = len(content)
    blob = root / "blobs" / "sha256" / digest
    blob.parent.mkdir(parents=True, exist_ok=True)
    _ = blob.write_bytes(content)
    return DatasetManifest.model_validate_json(json.dumps(data))


def test_build_reopen_and_recursive_cte_preserve_the_agreed_model(
    tmp_path: Path,
) -> None:
    manifest = stage_source(tmp_path)
    bundle = tmp_path / "bundle"
    result = build_reference_artifact(manifest, tmp_path, bundle)
    assert result.row_counts == {
        "source_release": 1,
        "reference_node": 10,
        "reference_node_attribute": 11,
        "reference_relation": 7,
        "concept": 6,
        "concept_description": 12,
    }
    assert (bundle / "minos.db").stat().st_mode & 0o777 == 0o600
    with open_reference(bundle) as reader:
        concept = reader.concept("icd10-synthetic-v1", "E11.2")
        assert concept is not None
        assert concept.source_code == ".2†"
        assert concept.system == "icd10-pl"
        assert [
            (item.language, item.description_type, item.source_text)
            for item in concept.descriptions
        ] == [
            ("en", "translation", "Marked synthetic variant"),
            ("pl", "preferred", "Wariant z oznaczeniem"),
        ]
        assert reader.concept("icd10-synthetic-v1", "M000") is None
        assert reader.concept("unknown-release", "E11.2") is None
        parents = reader.hierarchy("icd10-synthetic-v1", 7, direction="ancestors")
        assert [(node.ordinal, node.depth, node.kind) for node in parents] == [
            (1, 3, "range"),
            (2, 2, "container"),
            (3, 1, "classification"),
        ]
        descendants = reader.hierarchy("icd10-synthetic-v1", 3)
        assert [(node.ordinal, node.depth) for node in descendants] == [
            (4, 1),
            (5, 2),
            (6, 2),
            (7, 1),
        ]
        assert reader.hierarchy("icd10-synthetic-v1", 3, relation_type="is_a") == ()
    with open_reference_connection(bundle) as connection:
        assert connection.execute(
            "SELECT source_text, normalized_text FROM concept_description WHERE node_ordinal=3 AND language='pl'"
        ).fetchone() == (
            "  Próbna etykieta Łódź  ",
            "próbna etykieta łódź",
        )
        assert connection.execute(
            "SELECT count(*) FROM concept_description WHERE source_text='Notatka próbna'"
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT source_text FROM reference_node_attribute WHERE node_ordinal=7 AND name='annotationPL'"
        ).fetchone() == ("Notatka próbna",)
        assert connection.execute(
            "SELECT publisher, license FROM source_release"
        ).fetchone() == (
            "Synthetic fixture, not RSK content",
            "Apache-2.0 OR MIT (synthetic fixture only)",
        )
        # Indexed token results are explicitly ordered by source identity, not FTS rank.
        assert connection.execute(
            "SELECT node_ordinal FROM concept_description_fts JOIN concept_description ON concept_description.id=concept_description_fts.rowid WHERE concept_description_fts MATCH 'synthetic' ORDER BY node_ordinal"
        ).fetchall() == [(1,), (3,), (4,), (7,), (8,), (10,)]
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            _ = connection.execute("DELETE FROM concept")


def test_same_inputs_produce_identical_database_bytes(tmp_path: Path) -> None:
    manifest = stage_source(tmp_path)
    first = build_reference_artifact(manifest, tmp_path, tmp_path / "first")
    second = build_reference_artifact(manifest, tmp_path, tmp_path / "second")
    assert first.database_sha256 == second.database_sha256
    assert (tmp_path / "first" / "minos.db").read_bytes() == (
        tmp_path / "second" / "minos.db"
    ).read_bytes()


@pytest.mark.parametrize("damage", ["checksum", "size", "symlink", "fifo", "missing"])
def test_unverified_bytes_are_rejected_before_parsing(
    tmp_path: Path, damage: str
) -> None:
    manifest = stage_source(tmp_path)
    source = manifest.sources[0]
    blob = tmp_path / "blobs" / "sha256" / source.sha256
    if damage == "checksum":
        _ = blob.write_bytes(b"X" * source.size_bytes)
    elif damage == "size":
        _ = blob.write_bytes(b"X")
    elif damage == "symlink":
        original = tmp_path / "original.xml"
        _ = blob.rename(original)
        blob.symlink_to(original)
    elif damage == "fifo":
        blob.unlink()
        os.mkfifo(blob)
    else:
        blob.unlink()
    with pytest.raises(SourceError, match="pinned"):
        _ = build_reference_artifact(manifest, tmp_path, tmp_path / "failed")
    assert not (tmp_path / "failed").exists()
    assert not list(tmp_path.glob(".minos-reference-*"))


@pytest.mark.parametrize("filename", ["dtd.xml", "dup-code.xml", "unsupported.xml"])
def test_parse_or_import_failure_never_publishes_an_artifact(
    tmp_path: Path, filename: str
) -> None:
    manifest = stage_source(tmp_path, (FIXTURES / filename).read_bytes())
    with pytest.raises((SourceError, ArtifactError)):
        _ = build_reference_artifact(manifest, tmp_path, tmp_path / "failed")
    assert not (tmp_path / "failed").exists()
    assert not list(tmp_path.glob(".minos-reference-*"))


def test_failure_cannot_modify_an_existing_valid_bundle(tmp_path: Path) -> None:
    manifest = stage_source(tmp_path)
    bundle = tmp_path / "existing"
    _ = build_reference_artifact(manifest, tmp_path, bundle)
    original = {path.name: path.read_bytes() for path in bundle.iterdir()}
    broken = stage_source(tmp_path, (FIXTURES / "dtd.xml").read_bytes())
    with pytest.raises(ArtifactError, match="already exists"):
        _ = build_reference_artifact(broken, tmp_path, bundle)
    assert {path.name: path.read_bytes() for path in bundle.iterdir()} == original


def test_late_parse_failure_rolls_back_real_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = stage_source(tmp_path)

    def late_failure(source: BinaryIO) -> Iterator[ReferenceEvent]:
        yield from parse_icd10(source)
        raise SourceError("injected after complete parsed rows")

    monkeypatch.setattr(artifact_module, "parse_icd10", late_failure)
    with pytest.raises(SourceError, match="injected"):
        _ = build_reference_artifact(manifest, tmp_path, tmp_path / "failed")
    assert not (tmp_path / "failed").exists()


@pytest.mark.parametrize(
    "damage", ["missing-parent", "cycle", "duplicate-lookup", "counts"]
)
def test_store_integrity_failure_can_be_rolled_back(
    tmp_path: Path, damage: str
) -> None:
    manifest = stage_source(tmp_path)
    events = list(parse_icd10(BytesIO((FIXTURES / "minimal.xml").read_bytes())))
    if damage in ("missing-parent", "cycle"):
        for index, event in enumerate(events):
            if isinstance(event, ReferenceRelation) and event.child_ordinal == (
                5 if damage == "missing-parent" else 4
            ):
                events[index] = replace(
                    event, parent_ordinal=0 if damage == "missing-parent" else 5
                )
                break
    elif damage == "duplicate-lookup":
        events = [
            replace(event, lookup_code="E11")
            if isinstance(event, ReferenceNode) and event.ordinal == 4
            else event
            for event in events
        ]
    else:
        events = [
            event
            for event in events
            if not isinstance(event, ReferenceNode) or event.ordinal != 10
        ]
    connection = sqlite3.connect(":memory:", autocommit=True)
    try:
        _ = connection.execute("PRAGMA foreign_keys = ON")
        _ = connection.execute("BEGIN")
        initialize_reference(connection, manifest)
        with pytest.raises((ArtifactError, sqlite3.IntegrityError)):
            import_release(connection, manifest.sources[0], events)
        _ = connection.execute("ROLLBACK")
        assert (
            connection.execute(
                "SELECT name FROM sqlite_schema WHERE type='table'"
            ).fetchall()
            == []
        )
    finally:
        connection.close()


def test_generic_graph_allows_multiple_parents_and_deduplicates_walk_results(
    tmp_path: Path,
) -> None:
    manifest = stage_source(tmp_path)
    source = manifest.sources[0].model_copy(
        update={
            "expected": manifest.sources[0].expected.model_copy(update={"relations": 8})
        }
    )
    events = list(parse_icd10(BytesIO((FIXTURES / "minimal.xml").read_bytes())))
    events.append(ReferenceRelation(3, 6, "contains"))
    connection = sqlite3.connect(":memory:", autocommit=True)
    try:
        _ = connection.execute("PRAGMA foreign_keys = ON")
        _ = connection.execute("BEGIN")
        initialize_reference(connection, manifest)
        import_release(connection, source, events)
        _ = finalize_reference(connection)
        _ = connection.execute("COMMIT")
        reader = ReferenceReader(connection)
        assert [
            (node.ordinal, node.depth)
            for node in reader.hierarchy(source.release_id, 3)
        ] == [(4, 1), (5, 2), (6, 1), (7, 1)]
    finally:
        connection.close()


@pytest.mark.parametrize(("max_depth", "max_nodes"), [(1, 100), (32, 2)])
def test_recursive_queries_fail_explicitly_instead_of_silent_truncation(
    tmp_path: Path, max_depth: int, max_nodes: int
) -> None:
    manifest = stage_source(tmp_path)
    bundle = tmp_path / "bundle"
    _ = build_reference_artifact(manifest, tmp_path, bundle)
    with open_reference(bundle) as reader:
        with pytest.raises(QueryLimitError):
            _ = reader.hierarchy(
                "icd10-synthetic-v1", 1, max_depth=max_depth, max_nodes=max_nodes
            )


def test_read_rejects_database_tampering(tmp_path: Path) -> None:
    manifest = stage_source(tmp_path)
    bundle = tmp_path / "bundle"
    _ = build_reference_artifact(manifest, tmp_path, bundle)
    with (bundle / "minos.db").open("r+b") as database:
        _ = database.seek(100)
        _ = database.write(b"corrupt")
    with pytest.raises(ArtifactError, match="checksum"):
        with open_reference(bundle):
            pytest.fail("tampered database opened")


def test_unknown_manifest_fields_are_rejected() -> None:
    content = cast(dict[str, object], json.loads(INPUT_MANIFEST.read_bytes()))
    content["follow_latest"] = True
    with pytest.raises(ValidationError, match="follow_latest"):
        _ = DatasetManifest.model_validate_json(json.dumps(content))


@pytest.mark.parametrize("failure", ["finalization", "publication"])
def test_late_storage_failures_do_not_publish_or_leave_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    manifest = stage_source(tmp_path)

    def fail_finalization(_connection: sqlite3.Connection) -> dict[str, int]:
        raise sqlite3.OperationalError("injected finalization failure")

    def fail_publication(_source: Path, _target: Path) -> None:
        raise OSError("injected publication failure")

    if failure == "finalization":
        monkeypatch.setattr(artifact_module, "finalize_reference", fail_finalization)
    else:
        monkeypatch.setattr(os, "rename", fail_publication)
    with pytest.raises(ArtifactError, match="construction|publication"):
        _ = build_reference_artifact(manifest, tmp_path, tmp_path / "failed")
    assert not (tmp_path / "failed").exists()
    assert not list(tmp_path.glob(".minos-reference-*"))


def test_previous_artifact_summary_is_checked_and_count_deltas_are_explicit(
    tmp_path: Path,
) -> None:
    manifest = stage_source(tmp_path)
    _ = build_reference_artifact(manifest, tmp_path, tmp_path / "previous")
    _ = build_reference_artifact(
        manifest, tmp_path, tmp_path / "current", previous_bundle=tmp_path / "previous"
    )
    summary = (tmp_path / "current" / "review.txt").read_text()
    assert "reference_node: 10 -> 10 (+0)" in summary
    assert "not a semantic or legal applicability diff" in summary
    with (tmp_path / "previous" / "minos.db").open("r+b") as database:
        _ = database.write(b"invalid")
    with pytest.raises(ArtifactError, match="checksum"):
        _ = build_reference_artifact(
            manifest,
            tmp_path,
            tmp_path / "failed",
            previous_bundle=tmp_path / "previous",
        )
    assert not (tmp_path / "failed").exists()


def test_generic_store_is_not_tied_to_icd_codes_or_polish_descriptions(
    tmp_path: Path,
) -> None:
    manifest = stage_source(tmp_path)
    source = manifest.sources[0].model_copy(
        update={
            "system": "other-classification",
            "origin": "synthetic",
            "expected": manifest.sources[0].expected.model_copy(
                update={"nodes": 1, "relations": 0, "concepts": 1, "descriptions": 2}
            ),
        }
    )
    events: list[ReferenceEvent] = [
        SourceHeader((), ()),
        ReferenceNode(
            1,
            "parent/source-42",
            "Exemple",
            (),
            "classification",
            "source-42",
            (
                ConceptDescription("fr", "preferred", "Exemple", "exemple"),
                ConceptDescription("es", "alternate", "Ejemplo", "ejemplo"),
            ),
        ),
    ]
    connection = sqlite3.connect(":memory:", autocommit=True)
    try:
        _ = connection.execute("PRAGMA foreign_keys = ON")
        _ = connection.execute("BEGIN")
        initialize_reference(connection, manifest)
        import_release(connection, source, events)
        _ = finalize_reference(connection)
        _ = connection.execute("COMMIT")
        concept = ReferenceReader(connection).concept(source.release_id, "source-42")
        assert concept is not None
        assert concept.system == "other-classification"
        assert {item.language for item in concept.descriptions} == {"fr", "es"}
    finally:
        connection.close()


def test_same_code_in_distinct_releases_is_not_a_duplicate(tmp_path: Path) -> None:
    original = stage_source(tmp_path)
    second = original.sources[0].model_copy(update={"release_id": "icd10-synthetic-v2"})
    manifest = original.model_copy(update={"sources": (original.sources[0], second)})
    result = build_reference_artifact(manifest, tmp_path, tmp_path / "bundle")
    assert result.row_counts["concept"] == 12
    with open_reference(tmp_path / "bundle") as reader:
        assert reader.concept(original.sources[0].release_id, "E11") is not None
        assert reader.concept(second.release_id, "E11") is not None
        assert len(reader.hierarchy(second.release_id, 3)) == 4


def test_recursive_cte_handles_representative_fanout_within_execution_budget(
    tmp_path: Path,
) -> None:
    manifest = stage_source(tmp_path)
    source = manifest.sources[0].model_copy(
        update={
            "expected": manifest.sources[0].expected.model_copy(
                update={
                    "nodes": 3001,
                    "relations": 3000,
                    "concepts": 1,
                    "descriptions": 1,
                }
            ),
        }
    )

    def events() -> Iterator[ReferenceEvent]:
        yield SourceHeader((), ())
        yield ReferenceNode(
            1,
            "ROOT",
            "Synthetic root",
            (),
            "classification",
            "ROOT",
            (
                ConceptDescription(
                    "en", "preferred", "Synthetic root", "synthetic root"
                ),
            ),
        )
        for ordinal in range(2, 3002):
            yield ReferenceNode(ordinal, "", "Synthetic leaf", (), "text", None)
            yield ReferenceRelation(1, ordinal, "contains")

    connection = sqlite3.connect(":memory:", autocommit=True)
    try:
        _ = connection.execute("PRAGMA foreign_keys = ON")
        _ = connection.execute("BEGIN")
        initialize_reference(connection, manifest)
        import_release(connection, source, events())
        _ = finalize_reference(connection)
        _ = connection.execute("COMMIT")
        rows = ReferenceReader(connection).hierarchy(
            source.release_id, 1, max_nodes=3000
        )
        assert len(rows) == 3000
        assert rows[0].ordinal == 2
        assert rows[-1].ordinal == 3001
        assert {row.depth for row in rows} == {1}
    finally:
        connection.close()


def test_search_keys_cannot_silently_diverge_from_exact_source_text(
    tmp_path: Path,
) -> None:
    manifest = stage_source(tmp_path)
    events = list(parse_icd10(BytesIO((FIXTURES / "minimal.xml").read_bytes())))
    events = [
        replace(
            event,
            descriptions=(replace(event.descriptions[0], normalized_text="unrelated"),),
        )
        if isinstance(event, ReferenceNode) and event.ordinal == 3
        else event
        for event in events
    ]
    connection = sqlite3.connect(":memory:", autocommit=True)
    try:
        _ = connection.execute("BEGIN")
        initialize_reference(connection, manifest)
        with pytest.raises(ArtifactError, match="search key"):
            import_release(connection, manifest.sources[0], events)
        _ = connection.execute("ROLLBACK")
    finally:
        connection.close()


def test_verified_source_snapshot_is_not_changed_by_archive_rotation(
    tmp_path: Path,
) -> None:
    manifest = stage_source(tmp_path)
    source = manifest.sources[0]
    with verified_source(tmp_path, source, staging_directory=tmp_path) as snapshot:
        _ = (tmp_path / "blobs" / "sha256" / source.sha256).write_bytes(b"rotated")
        assert (
            sum(isinstance(event, ReferenceNode) for event in parse_icd10(snapshot))
            == 10
        )


def test_manifest_fifo_is_rejected_without_waiting_for_a_writer(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    os.mkfifo(bundle / "manifest.json")
    with pytest.raises(ArtifactError, match="not a regular file"):
        with open_reference(bundle):
            pytest.fail("FIFO manifest opened")
