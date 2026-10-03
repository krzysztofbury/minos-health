import json
import sqlite3
from datetime import date
from pathlib import Path
from typing import cast

import pytest

from minos.domain.jgp_ruleset import (
    KnowledgeBeyondBundle,
    NoRulesetForDate,
)
from minos.domain.reference import ArtifactError, SourceError
from minos.domain.search import SearchRequest, prepare_query
from minos.ingest.reference_artifact import build_reference_artifact
from minos.ingest.reference_manifest import DatasetManifest
from minos.store.artifact import canonical_json, file_identity
from minos.store.jgp_conditions import (
    group_catalog_correspondence,
    resolve_characteristics,
)
from minos.store.jgp_conditions_build import load_characteristics
from minos.store.reference import open_reference_bundle, read_artifact_manifest
from minos.store.search import search_reference
from tests.fixtures.jgp_catalog import catalog_workbook, default_sheets, stage_catalog
from tests.fixtures.jgp_conditions import stage
from tests.integration.test_canonical_reference import canonical_manifest


def conditions_manifest(root: Path, *, original: bool = False) -> DatasetManifest:
    base = canonical_manifest(root)
    evidence = stage(root, original=original)
    data = base.model_dump(mode="json")
    data["characteristics"] = [evidence.model_dump(mode="json")]
    data["characteristics_knowledge_as_of"] = "2026-10-02"
    data["characteristics_knowledge_evidence"] = (
        "Synthetic characteristics lineage review, separate from catalog review"
    )
    return DatasetManifest.model_validate_json(json.dumps(data))


@pytest.mark.parametrize("original", [False, True])
def test_reference_bundle_roundtrip_keeps_source_local_codes_out_of_canonical_search(
    tmp_path: Path, original: bool
) -> None:
    selected = conditions_manifest(tmp_path, original=original)
    bundle = tmp_path / "bundle"
    built = build_reference_artifact(selected, tmp_path, bundle)
    assert built.schema_version == 5
    assert built.row_counts["group_condition"] == (5 if original else 6)
    identity = selected.characteristics[0].release_id
    with open_reference_bundle(bundle) as (connection, _):
        source = load_characteristics(connection, identity, "1a")
        assert {group.code for group in source.groups} == {"A01", "N09", "N11"}
        assert connection.execute(
            "SELECT reference_release_id, reference_node, correspondence FROM characteristics_code WHERE catalog='1a' AND code='G60.4'"
        ).fetchall() == [("icd10-synthetic-v1", None, "not_in_reference")]
        assert connection.execute(
            "SELECT code, correspondence FROM characteristics_code WHERE catalog='1a' AND system='icd10-pl' ORDER BY code"
        ).fetchall() == [("E11.0", "matched"), ("G60.4", "not_in_reference")]
        assert connection.execute(
            "SELECT row FROM characteristics_code_evidence WHERE catalog='1a' AND code='G60.4' ORDER BY row"
        ).fetchall() == ([(9,), (10,)] if original else [(10,), (11,)])
        assert not search_reference(
            connection, SearchRequest("icd10-pl", prepare_query("G60.4"))
        ).hits
        assert connection.execute(
            "SELECT count(*) FROM group_definition WHERE code IN ('N09','N11')"
        ).fetchone() == (0,)
        dates = cast(
            tuple[str, str | None, str, str],
            connection.execute(
                "SELECT valid_from, valid_to, published_on, recorded_at FROM characteristics_release"
            ).fetchone(),
        )
        assert dates == (
            ("2026-05-01", "2026-06-30", "2026-04-30", "2026-10-03T10:00:00+00:00")
            if original
            else ("2026-07-01", None, "2026-09-23", "2026-10-03T10:00:00+00:00")
        )
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            _ = connection.execute("DELETE FROM group_condition")
    second = build_reference_artifact(selected, tmp_path, tmp_path / "second")
    assert second.database_sha256 == built.database_sha256
    assert "Not an executable ruleset" in (bundle / "review.txt").read_text()
    assert (
        "icd10-pl / not_in_reference: 1 distinct codes"
        in (bundle / "review.txt").read_text()
    )


@pytest.mark.parametrize("failure", ["checksum", "unmatched_count", "node_count"])
def test_failed_characteristics_import_never_publishes_a_partial_bundle(
    tmp_path: Path, failure: str
) -> None:
    selected = conditions_manifest(tmp_path)
    characteristic = selected.characteristics[0]
    if failure == "checksum":
        blob = tmp_path / "blobs" / "sha256" / characteristic.workbook.sha256
        content = bytearray(blob.read_bytes())
        content[-1] ^= 1
        _ = blob.write_bytes(content)
    else:
        expected = characteristic.sheets["1a"]
        field = "unmatched_codes" if failure == "unmatched_count" else "conditions"
        changed = expected.model_copy(
            update={
                field: 0 if failure == "unmatched_count" else expected.conditions + 1
            }
        )
        characteristic = characteristic.model_copy(
            update={"sheets": {**characteristic.sheets, "1a": changed}}
        )
        selected = selected.model_copy(update={"characteristics": (characteristic,)})
    output = tmp_path / "bundle"
    with pytest.raises((SourceError, ArtifactError)):
        _ = build_reference_artifact(selected, tmp_path, output)
    assert not output.exists() and not tuple(tmp_path.glob(".minos-reference-*"))


@pytest.mark.parametrize(
    ("tamper", "message"),
    [
        (
            "UPDATE group_condition SET kind='not' WHERE kind='all'",
            "envelope conflicts",
        ),
        (
            "UPDATE directional_list SET label='arbitrary' WHERE id=1 AND catalog='1a'",
            "structure is invalid",
        ),
        (
            "UPDATE characteristics_evidence SET source_text='unreviewed magic' WHERE category='label' AND context='A01'",
            "structure is invalid",
        ),
        (
            "UPDATE characteristics_group SET roots_json='[]' WHERE code='A01'",
            "structure is invalid",
        ),
        (
            "UPDATE characteristics_group SET roots_json='[\"one\"]' WHERE code='A01'",
            "structure is invalid",
        ),
        (
            "UPDATE characteristics_sheet SET worksheet='Another worksheet' WHERE catalog='1a'",
            "worksheet identity conflicts",
        ),
        (
            "UPDATE characteristics_code SET correspondence='matched', reference_node=1 WHERE code='G60.4'",
            "correspondence conflicts",
        ),
        (
            "UPDATE characteristics_knowledge SET as_of='2026-10-03'",
            "knowledge horizon conflicts",
        ),
        (
            "UPDATE characteristics_group_catalog SET correspondence='not_in_catalog', catalog_release_id='missing' WHERE code='N09'",
            "catalog correspondence conflicts",
        ),
    ],
)
def test_bundle_open_rechecks_payload_graph_after_valid_checksums_are_recomputed(
    tmp_path: Path,
    tamper: str,
    message: str,
) -> None:
    selected = conditions_manifest(tmp_path)
    bundle = tmp_path / "bundle"
    _ = build_reference_artifact(selected, tmp_path, bundle)
    with sqlite3.connect(bundle / "minos.db") as connection:
        _ = connection.execute(tamper)
    original = read_artifact_manifest(bundle)
    checksum, size = file_identity(bundle / "minos.db")
    changed = original.model_copy(
        update={"database_sha256": checksum, "database_size_bytes": size}
    )
    _ = (bundle / "manifest.json").write_bytes(canonical_json(changed))
    with pytest.raises(ArtifactError, match=message):
        with open_reference_bundle(bundle):
            pass


def test_dated_conditions_use_publication_and_separate_catalog_row_windows(
    tmp_path: Path,
) -> None:
    base = conditions_manifest(tmp_path)
    original = stage(tmp_path, original=True).model_copy(update={"valid_to": None})
    catalog = stage_catalog(
        tmp_path,
        catalog_workbook(default_sheets()),
        release={
            "baw_document_id": 44042,
            "document_url": "https://baw.nfz.gov.pl/NFZ/document/44042/Synthetic",
        },
    )
    current = base.characteristics[0].model_copy(
        update={
            "supersedes": original.release_id,
            "catalog_release_id": catalog.release_id,
        }
    )
    data = base.model_dump(mode="json")
    data.update(
        {
            "characteristics": [
                original.model_dump(mode="json"),
                current.model_dump(mode="json"),
            ],
            "catalogs": [catalog.model_dump(mode="json")],
            "catalog_knowledge_as_of": "2026-10-02",
            "catalog_knowledge_evidence": "Synthetic independent catalog horizon",
        }
    )
    selected = DatasetManifest.model_validate_json(json.dumps(data))
    bundle = tmp_path / "bundle"
    _ = build_reference_artifact(selected, tmp_path, bundle)
    with open_reference_bundle(bundle) as (connection, _):
        assert (
            resolve_characteristics(
                connection, "1a", date(2026, 7, 10), date(2026, 9, 22)
            )
            == original.release_id
        )
        assert (
            resolve_characteristics(
                connection, "1a", date(2026, 7, 10), date(2026, 9, 23)
            )
            == current.release_id
        )
        assert (
            resolve_characteristics(connection, "1a", date(2027, 1, 1))
            == current.release_id
        )
        assert group_catalog_correspondence(
            connection, current.release_id, "1a", "A01", date(2026, 7, 10)
        ).source_rows == (7,)
        for code in ("N09", "N11"):
            answer = group_catalog_correspondence(
                connection, current.release_id, "1a", code, date(2026, 7, 10)
            )
            assert answer.status == "not_in_catalog" and answer.source_rows == ()
        assert (
            group_catalog_correspondence(
                connection, original.release_id, "1a", "N09", date(2026, 5, 1)
            ).status
            == "catalog_unbound"
        )
        with pytest.raises(NoRulesetForDate):
            _ = resolve_characteristics(connection, "1a", date(2026, 4, 30))
        with pytest.raises(NoRulesetForDate):
            _ = resolve_characteristics(
                connection, "1ae", date(2026, 7, 10), date(2026, 9, 22)
            )
        with pytest.raises(KnowledgeBeyondBundle):
            _ = resolve_characteristics(
                connection, "1a", date(2026, 7, 10), date(2026, 10, 3)
            )
    ambiguous = selected.model_copy(
        update={
            "characteristics": (
                original,
                current.model_copy(update={"supersedes": None}),
            )
        }
    )
    with pytest.raises(SourceError, match="supersession"):
        _ = build_reference_artifact(ambiguous, tmp_path, tmp_path / "ambiguous")
    assert not (tmp_path / "ambiguous").exists()


def test_dictionary_only_rebuild_changes_correspondence_not_nfz_identity(
    tmp_path: Path,
) -> None:
    from tests.integration.test_reference_artifact import stage_source

    base = conditions_manifest(tmp_path)
    first = tmp_path / "before"
    _ = build_reference_artifact(base, tmp_path, first)
    dictionary = next(s for s in base.sources if s.system == "icd10-pl")
    content = (
        (tmp_path / "blobs/sha256" / dictionary.sha256)
        .read_bytes()
        .replace(b"U99.9", b"G60.4")
    )
    revised = (
        stage_source(tmp_path, content)
        .sources[0]
        .model_copy(update={"release_id": "icd10-reviewed-second"})
    )
    old_condition = base.characteristics[0]
    condition = old_condition.model_copy(
        update={
            "icd10_release_id": revised.release_id,
            "sheets": {
                context: counts.model_copy(
                    update={"unmatched_codes": counts.unmatched_codes - 1}
                )
                for context, counts in old_condition.sheets.items()
            },
        }
    )
    updated = base.model_copy(
        update={
            "sources": tuple(
                revised if s.release_id == dictionary.release_id else s
                for s in base.sources
            ),
            "characteristics": (condition,),
        }
    )
    _ = build_reference_artifact(updated, tmp_path, tmp_path / "after")
    with (
        open_reference_bundle(first) as (before, _),
        open_reference_bundle(tmp_path / "after") as (after, _),
    ):
        assert load_characteristics(
            before, condition.release_id, "1a"
        ) == load_characteristics(after, condition.release_id, "1a")
        assert before.execute(
            "SELECT correspondence FROM characteristics_code WHERE code='G60.4' AND catalog='1a'"
        ).fetchone() == ("not_in_reference",)
        assert after.execute(
            "SELECT correspondence, reference_release_id FROM characteristics_code WHERE code='G60.4' AND catalog='1a'"
        ).fetchone() == ("matched", revised.release_id)
    missing = updated.model_copy(
        update={
            "sources": tuple(
                s for s in updated.sources if s.release_id != revised.release_id
            )
        }
    )
    with pytest.raises(ArtifactError, match="pin is absent"):
        _ = build_reference_artifact(missing, tmp_path, tmp_path / "missing")
    assert not (tmp_path / "missing").exists()


def test_conditions_are_reconstructed_once_per_verified_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import minos.store.jgp_conditions_build as build
    import minos.store.reference as reference

    calls: list[str] = []
    real = build.validate_conditions_bindings

    def counted(connection: sqlite3.Connection, dataset: DatasetManifest) -> None:
        calls.append("validated")
        real(connection, dataset)

    monkeypatch.setattr(build, "validate_conditions_bindings", counted)
    monkeypatch.setattr(reference, "_VALIDATED_CONDITIONS", set[tuple[str, str]]())
    bundle = tmp_path / "bundle"
    _ = build_reference_artifact(conditions_manifest(tmp_path), tmp_path, bundle)
    for _ in range(3):  # a pool opens several connections to one bundle
        with open_reference_bundle(bundle):
            pass
    assert calls == ["validated"]
    # A changed database has another verified digest and is reconstructed again.
    with sqlite3.connect(bundle / "minos.db") as connection:
        _ = connection.execute(
            "UPDATE characteristics_evidence SET source_text='unreviewed magic'"
            + " WHERE category='label' AND context='A01'"
        )
    checksum, size = file_identity(bundle / "minos.db")
    changed = read_artifact_manifest(bundle).model_copy(
        update={"database_sha256": checksum, "database_size_bytes": size}
    )
    _ = (bundle / "manifest.json").write_bytes(canonical_json(changed))
    with pytest.raises(ArtifactError, match="structure is invalid"):
        with open_reference_bundle(bundle):
            pass
    assert calls == ["validated", "validated"]


@pytest.mark.parametrize(
    "update",
    [
        "SET kind = 'magic'",
        "SET kind = NULL, interpretation_status = 'proposed'",
    ],
)
def test_schema_refuses_unknown_kinds_and_kindless_proposed_rows(
    tmp_path: Path, update: str
) -> None:
    bundle = tmp_path / "bundle"
    _ = build_reference_artifact(conditions_manifest(tmp_path), tmp_path, bundle)
    with sqlite3.connect(bundle / "minos.db") as connection:
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            _ = connection.execute(
                f"UPDATE group_condition {update}"  # nosec B608 - fixed test SQL
                + " WHERE rowid = (SELECT min(rowid) FROM group_condition"
                + " WHERE kind IS NOT NULL)"
            )
