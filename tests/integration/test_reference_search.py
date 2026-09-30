import hashlib
import json
import sqlite3
import subprocess
import sys
from io import BytesIO
from pathlib import Path
from typing import cast

import pytest

from minos.domain.reference import (
    ConceptDescription,
    ReferenceNode,
    ReferenceRelation,
    SourceHeader,
)
from minos.domain.search import (
    IndexMode,
    SearchInputError,
    SearchRequest,
    prepare_query,
)
from minos.ingest.icd10 import parse_icd10
from minos.ingest.reference_artifact import build_reference_artifact
from minos.ingest.reference_manifest import DatasetManifest
from minos.store.reference import open_reference_connection
from minos.store.reference_build import (
    finalize_reference,
    import_release,
    initialize_reference,
)
from minos.store.search import search_reference

ROOT = Path(__file__).parents[2]


@pytest.fixture
def search_source(tmp_path: Path) -> DatasetManifest:
    original = DatasetManifest.model_validate_json(
        (ROOT / "manifests/sources/icd10-synthetic.json").read_bytes()
    )
    content = (ROOT / "tests/fixtures/icd10-pl/search.xml").read_bytes()
    source = original.sources[0].model_copy(
        update={
            "sha256": hashlib.sha256(content).hexdigest(),
            "size_bytes": len(content),
            "expected": original.sources[0].expected.model_copy(
                update={"nodes": 10, "relations": 7, "concepts": 7, "descriptions": 8}
            ),
        }
    )
    manifest = original.model_copy(update={"sources": (source,)})
    blob = tmp_path / "blobs/sha256" / source.sha256
    blob.parent.mkdir(parents=True)
    _ = blob.write_bytes(content)
    return manifest


@pytest.fixture
def search_bundle(tmp_path: Path, search_source: DatasetManifest) -> Path:
    bundle = tmp_path / "bundle"
    _ = build_reference_artifact(search_source, tmp_path, bundle)
    return bundle


@pytest.mark.parametrize("index", ["auto", "portable"])
@pytest.mark.parametrize(
    ("query", "codes"),
    [
        ("e11", ["E11", "E11.0", "E11.1"]),
        ("Testowy wariant", ["E11.0", "E10.0", "E11.1"]),
        ("pokaż mi możliwe kody dla cukrzycy", ["E10.0", "E10", "E11"]),
        ("cukrzycy typu 2", ["E11"]),
        ("cukrzycą typu 2", ["E11"]),
        ("niewydolność", ["E10.0"]),
        ("wariantu bez powikłania", ["E11.1"]),
        ("wariantu z powikłaniami", []),
        ("Testowy wariant dotyczący cukrzycy", ["E10.0"]),
        ("bursztyn", ["E10.0"]),
        ("riant", ["E10.0", "E11.0", "E11.1"]),
        ("powikłań bez", ["E11.1"]),
        ("powikłań z", []),
        ("Nieindeksowana adnotacja", []),
        ("Nieindeksowany modyfikator", []),
        ("E110", []),
        ("E11.9", []),
    ],
)
def test_lexical_precedence_and_portable_index_have_the_same_source_results(
    search_bundle: Path,
    query: str,
    codes: list[str],
    index: str,
) -> None:
    request = SearchRequest(
        "icd10-pl", prepare_query(query), index=cast(IndexMode, index)
    )
    with open_reference_connection(search_bundle) as connection:
        result = search_reference(connection, request)
    assert [hit.code for hit in result.hits] == codes
    if query == "Testowy wariant dotyczący cukrzycy":
        assert result.hits[0].match_type == "exact_description"
    if query == "niewydolność":
        assert result.hits[0].match_type == "morphology_tokens"
    if query == "pokaż mi możliwe kody dla cukrzycy":
        assert dict(result.morphology)["cukrzycy"] == (
            "cukrzyca",
            "cukrzycy",
            "cukrzyk",
        )
    if query == "cukrzycą typu 2":
        assert dict(result.morphology)["2"] == ("2",)


def test_uncoded_match_keeps_its_exact_evidence_and_full_ancestor_graph(
    search_bundle: Path,
) -> None:
    with open_reference_connection(search_bundle) as connection:
        result = search_reference(
            connection, SearchRequest("icd10-pl", prepare_query("bursztynowy"))
        )
    hit = result.hits[0]
    assert hit.code == "E10.0"
    assert hit.matched_node_ordinal == 4
    assert hit.description_type == "context"
    assert hit.matched_text == "Bursztynowy przykład dotyczący cukrzycy i niewydolności"
    assert [(node.ordinal, node.code) for node in hit.context_nodes] == [
        (1, "E00-E99"),
        (2, "E10"),
        (3, "E10.0"),
        (4, None),
    ]
    assert hit.context_edges == ((1, 2), (2, 3), (3, 4))


def test_english_is_explicit_and_unowned_root_text_never_invents_a_code(
    search_bundle: Path,
) -> None:
    with open_reference_connection(search_bundle) as connection:
        english = search_reference(
            connection,
            SearchRequest(
                "icd10-pl", prepare_query("amber", language="en"), language="en"
            ),
        )
        polish = search_reference(
            connection, SearchRequest("icd10-pl", prepare_query("amber"))
        )
        root = search_reference(
            connection, SearchRequest("icd10-pl", prepare_query("korzeniowy"))
        )
    assert english.hits[0].code == "E10.0"
    assert english.hits[0].matched_text == "Amber contextual example"
    assert polish.hits == ()
    assert root.hits[0].code is None
    assert root.hits[0].node_ordinal == 10


def test_spelling_is_an_explicit_source_vocabulary_suggestion_not_a_hidden_search(
    search_bundle: Path,
) -> None:
    with open_reference_connection(search_bundle) as connection:
        result = search_reference(
            connection, SearchRequest("icd10-pl", prepare_query("cukrzycca"))
        )
        code = search_reference(
            connection, SearchRequest("icd10-pl", prepare_query("E11.9"))
        )
    assert result.hits == ()
    assert result.suggestions[0].replacement == "cukrzyca"
    assert result.suggestions[0].distance == 1
    assert code.suggestions == ()


def test_search_limits_deduplicate_concepts_and_report_more_matches(
    search_bundle: Path,
) -> None:
    with open_reference_connection(search_bundle) as connection:
        result = search_reference(
            connection, SearchRequest("icd10-pl", prepare_query("E11"), limit=1)
        )
    assert [hit.code for hit in result.hits] == ["E11"]
    assert result.truncated


def test_ambiguous_release_selection_never_follows_latest(
    tmp_path: Path, search_source: DatasetManifest
) -> None:
    second = search_source.sources[0].model_copy(update={"release_id": "synthetic-v2"})
    manifest = search_source.model_copy(
        update={"sources": (*search_source.sources, second)}
    )
    bundle = tmp_path / "two-releases"
    _ = build_reference_artifact(manifest, tmp_path, bundle)
    with open_reference_connection(bundle) as connection:
        with pytest.raises(SearchInputError, match="explicit"):
            _ = search_reference(
                connection, SearchRequest("icd10-pl", prepare_query("E11"))
            )
        result = search_reference(
            connection,
            SearchRequest("icd10-pl", prepare_query("E11"), release_id="synthetic-v2"),
        )
    assert result.release_id == "synthetic-v2"


def test_multiple_parent_context_and_attribution_do_not_collapse_to_one_tree(
    search_source: DatasetManifest,
) -> None:
    source = search_source.sources[0].model_copy(
        update={
            "expected": search_source.sources[0].expected.model_copy(
                update={"relations": 8}
            )
        }
    )
    events = list(
        parse_icd10(BytesIO((ROOT / "tests/fixtures/icd10-pl/search.xml").read_bytes()))
    )
    events.append(ReferenceRelation(2, 4, "contains"))
    connection = sqlite3.connect(":memory:", autocommit=True)
    try:
        _ = connection.execute("PRAGMA foreign_keys=ON")
        _ = connection.execute("BEGIN")
        initialize_reference(connection, search_source)
        import_release(connection, source, events)
        _ = finalize_reference(connection)
        _ = connection.execute("COMMIT")
        result = search_reference(
            connection, SearchRequest("icd10-pl", prepare_query("bursztynowy"))
        )
        assert [hit.code for hit in result.hits] == ["E10", "E10.0"]
        assert result.hits[0].context_edges == ((1, 2), (2, 3), (2, 4), (3, 4))
    finally:
        connection.close()


def test_cli_json_is_deterministic_with_provenance_and_stable_no_result_exit(
    search_bundle: Path,
) -> None:
    command = [
        sys.executable,
        "-m",
        "minos",
        "search",
        "--system",
        "icd10-pl",
        "--query",
        "E11",
        "--data",
        str(search_bundle),
        "--json",
    ]
    first = subprocess.run(
        command, capture_output=True, text=True, timeout=10, check=True
    )
    second = subprocess.run(
        command, capture_output=True, text=True, timeout=10, check=True
    )
    assert first.stdout == second.stdout
    response = cast(dict[str, object], json.loads(first.stdout))
    assert response["version"] == 1
    assert cast(list[dict[str, object]], response["results"])[0]["code"] == "E11"
    assert (
        cast(dict[str, object], response["provenance"])["publisher"]
        == "Synthetic fixture, not RSK content"
    )
    assert "Reference only" in cast(str, response["disclaimer"])
    command[command.index("E11")] = "E99.9"
    missing = subprocess.run(
        command, capture_output=True, text=True, timeout=10, check=False
    )
    assert missing.returncode == 1
    assert json.loads(missing.stdout)["status"] == "no_results"


@pytest.mark.parametrize(
    ("query", "limit", "code"), [("sensitive\x1bquery", "20", 2), ("E11", "101", 2)]
)
def test_cli_errors_do_not_echo_queries_paths_or_tracebacks(
    search_bundle: Path, query: str, limit: str, code: int
) -> None:
    failed = subprocess.run(
        [
            sys.executable,
            "-m",
            "minos",
            "search",
            "--system",
            "icd10-pl",
            "--query",
            query,
            "--limit",
            limit,
            "--data",
            str(search_bundle),
            "--json",
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert failed.returncode == code
    assert query not in failed.stderr
    assert str(search_bundle) not in failed.stderr
    assert "Traceback" not in failed.stderr
    assert failed.stdout == ""


def test_cli_build_consumes_pinned_json_and_search_rejects_missing_or_changed_data(
    tmp_path: Path,
    search_source: DatasetManifest,
) -> None:
    manifest = tmp_path / "source.json"
    _ = manifest.write_text(search_source.model_dump_json())
    bundle = tmp_path / "cli-built"
    built = subprocess.run(
        [
            sys.executable,
            "-m",
            "minos",
            "data",
            "build",
            "--manifest",
            str(manifest),
            "--archive-root",
            str(tmp_path),
            "--output",
            str(bundle),
            "--json",
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    assert json.loads(built.stdout)["row_counts"]["concept"] == 7
    assert (bundle / "manifest.json").is_file()
    with (bundle / "minos.db").open("ab") as database:
        _ = database.write(b"changed")
    for data in (bundle, tmp_path / "missing"):
        failed = subprocess.run(
            [
                sys.executable,
                "-m",
                "minos",
                "search",
                "--system",
                "icd10-pl",
                "--query",
                "E11",
                "--data",
                str(data),
                "--json",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert failed.returncode == 3
        assert json.loads(failed.stderr)["error"]["code"] == "data_error"
        assert str(data) not in failed.stderr
        assert failed.stdout == ""


def test_query_operation_budget_fails_closed_without_leaving_an_interrupt_handler(
    search_bundle: Path,
) -> None:
    from minos.domain.reference import QueryLimitError
    from minos.store.query_budget import query_budget

    with open_reference_connection(search_bundle) as connection:
        with pytest.raises(QueryLimitError):
            with query_budget(connection, operations=1):
                _ = cast(
                    tuple[int],
                    connection.execute(
                        "WITH RECURSIVE walk(n) AS (VALUES(0) UNION ALL SELECT n+1 FROM walk WHERE n<10000) SELECT sum(n) FROM walk"
                    ).fetchone(),
                )
        assert connection.execute("SELECT 1").fetchone() == (1,)


@pytest.mark.parametrize(
    ("system", "code"), [("icd9-pl", "45.1"), ("icd11-synthetic", "5A11")]
)
def test_search_and_spelling_use_generic_reference_records_not_icd10_code_rules(
    search_source: DatasetManifest,
    system: str,
    code: str,
) -> None:
    source = search_source.sources[0].model_copy(
        update={
            "system": system,
            "expected": search_source.sources[0].expected.model_copy(
                update={"nodes": 1, "relations": 0, "concepts": 1, "descriptions": 1}
            ),
        }
    )
    description = ConceptDescription("pl", "preferred", "Bursztynowy", "bursztynowy")
    node = ReferenceNode(
        1, code, "Bursztynowy", (), "classification", code, (description,)
    )
    connection = sqlite3.connect(":memory:", autocommit=True)
    try:
        _ = connection.execute("PRAGMA foreign_keys=ON")
        _ = connection.execute("BEGIN")
        initialize_reference(connection, search_source)
        import_release(connection, source, (SourceHeader((), ()), node))
        second = source.model_copy(update={"release_id": "generic-second"})
        other_description = ConceptDescription(
            "pl", "preferred", "Przezroczysty", "przezroczysty"
        )
        other_node = ReferenceNode(
            1, code, "Przezroczysty", (), "classification", code, (other_description,)
        )
        import_release(connection, second, (SourceHeader((), ()), other_node))
        _ = finalize_reference(connection)
        _ = connection.execute("COMMIT")
        exact = search_reference(
            connection,
            SearchRequest(system, prepare_query(code), release_id=source.release_id),
        )
        spelling = search_reference(
            connection,
            SearchRequest(
                system, prepare_query("bursztynnowy"), release_id=source.release_id
            ),
        )
        assert exact.hits[0].code == code
        assert exact.hits[0].match_type == "exact_code"
        assert spelling.hits == ()
        assert spelling.suggestions[0].replacement == "bursztynowy"
        inflected = search_reference(
            connection,
            SearchRequest(
                system, prepare_query("bursztynowymi"), release_id=source.release_id
            ),
        )
        assert inflected.hits[0].code == code
        assert inflected.hits[0].match_type == "morphology_tokens"
        for index in ("auto", "portable"):
            first = search_reference(
                connection,
                SearchRequest(
                    system,
                    prepare_query("przezroczy"),
                    release_id=source.release_id,
                    index=index,
                ),
            )
            selected = search_reference(
                connection,
                SearchRequest(
                    system,
                    prepare_query("przezroczy"),
                    release_id=second.release_id,
                    index=index,
                ),
            )
            assert first.hits == () and first.suggestions == ()
            assert selected.hits[0].label == "Przezroczysty"
    finally:
        connection.close()


@pytest.mark.parametrize("index", ["auto", "portable"])
def test_negation_remains_a_literal_word_in_morphology_and_substring_fallback(
    search_source: DatasetManifest, index: IndexMode
) -> None:
    source = search_source.sources[0].model_copy(
        update={
            "expected": search_source.sources[0].expected.model_copy(
                update={"nodes": 5, "relations": 0, "concepts": 5, "descriptions": 5}
            )
        }
    )
    events: list[SourceHeader | ReferenceNode | ReferenceRelation] = [
        SourceHeader((), ())
    ]
    for ordinal, label in enumerate(("bzami", "niego", "bez", "nie", "babez riant"), 1):
        events.append(
            ReferenceNode(
                ordinal,
                f"Q{ordinal}",
                label,
                (),
                "classification",
                f"Q{ordinal}",
                (ConceptDescription("pl", "preferred", label, label),),
            )
        )
    connection = sqlite3.connect(":memory:", autocommit=True)
    try:
        _ = connection.execute("PRAGMA foreign_keys=ON")
        _ = connection.execute("BEGIN")
        initialize_reference(connection, search_source)
        import_release(connection, source, events)
        _ = finalize_reference(connection)
        _ = connection.execute("COMMIT")
        for query, expected in (("bez", ["Q3"]), ("nie", ["Q4"]), ("bez riant", [])):
            result = search_reference(
                connection, SearchRequest("icd10-pl", prepare_query(query), index=index)
            )
            assert [hit.code for hit in result.hits] == expected
    finally:
        connection.close()
