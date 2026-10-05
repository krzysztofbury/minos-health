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
from minos.ingest.search_synonyms import SearchSynonym
from minos.store.reference import open_reference_connection
from minos.store.reference_build import (
    finalize_reference,
    import_release,
    initialize_reference,
)
from minos.store.search import search_reference
from scripts.evaluate_search import EvaluationError, GoldSet, evaluate

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


@pytest.fixture
def folded_search_bundle(tmp_path: Path, search_source: DatasetManifest) -> Path:
    bundle = tmp_path / "folded-bundle"
    manifest = search_source.model_copy(update={"search_profile": "polish-folded-1"})
    _ = build_reference_artifact(manifest, tmp_path, bundle)
    return bundle


@pytest.mark.parametrize("index", ["auto", "portable"])
@pytest.mark.parametrize(
    "query,codes",
    [
        ("niewydolnosc", ["E10.0"]),
        # A typed diacritic is never folded away: "tęstowy" is not "testowy".
        ("tęstowy", []),
        ("powiklan bez", ["E11.1"]),
        ("wariantu z powiklaniami", []),
        ("cukrzyca typu 9", []),
        ("E119", []),
    ],
)
def test_folded_fallback_is_labelled_portable_and_keeps_qualifiers_literal(
    folded_search_bundle: Path, query: str, codes: list[str], index: str
) -> None:
    with open_reference_connection(folded_search_bundle) as connection:
        result = search_reference(
            connection,
            SearchRequest(
                "icd10-pl", prepare_query(query), index=cast(IndexMode, index)
            ),
        )
    assert [hit.code for hit in result.hits] == codes
    if codes:
        assert result.hits[0].match_type == "folded_tokens"
        assert any(
            character in result.hits[0].matched_text for character in "ąćęłńóśźż"
        )


def test_folded_profile_keeps_exact_precedence_and_legacy_bundle_compatibility(
    folded_search_bundle: Path, search_bundle: Path
) -> None:
    for bundle in (search_bundle, folded_search_bundle):
        with open_reference_connection(bundle) as connection:
            code = search_reference(
                connection, SearchRequest("icd10-pl", prepare_query("E11"))
            )
            assert code.hits[0].code == "E11"
            assert code.hits[0].match_type == "exact_code"
            description = search_reference(
                connection, SearchRequest("icd10-pl", prepare_query("Testowy wariant"))
            )
            assert description.hits[0].match_type == "exact_description"
    with open_reference_connection(search_bundle) as connection:
        legacy = search_reference(
            connection, SearchRequest("icd10-pl", prepare_query("niewydolnosc"))
        )
        assert not legacy.hits


@pytest.mark.parametrize(
    "elapsed_seconds,refused", [(0.3, False), (0.6, False), (2.001, True)]
)
def test_search_allows_slow_results_with_one_bounded_deadline(
    search_bundle: Path,
    monkeypatch: pytest.MonkeyPatch,
    elapsed_seconds: float,
    refused: bool,
) -> None:
    import time

    from minos.domain.reference import QueryTimeoutError

    clock = iter((0.0,))
    monkeypatch.setattr(time, "monotonic", lambda: next(clock, elapsed_seconds))
    with open_reference_connection(search_bundle) as connection:
        if refused:
            with pytest.raises(QueryTimeoutError, match="elapsed deadline"):
                _ = search_reference(
                    connection, SearchRequest("icd10-pl", prepare_query("E11"))
                )
        else:
            assert (
                search_reference(
                    connection, SearchRequest("icd10-pl", prepare_query("E11"))
                )
                .hits[0]
                .code
                == "E11"
            )


@pytest.mark.parametrize("reviewed", [False, True])
@pytest.mark.parametrize("whole_query_only", [False, True])
def test_synonym_rewrite_requires_review_keeps_qualifiers_and_preserves_source_evidence(
    tmp_path: Path,
    search_source: DatasetManifest,
    reviewed: bool,
    whole_query_only: bool,
) -> None:
    entry = SearchSynonym.model_validate_json(
        json.dumps(
            {
                "id": "synthetic-alias",
                "version": 1,
                "system": "icd10-pl",
                "term": "syntetyczny odpowiednik",
                "source_wording": "bez powikłań",
                "source_url": "https://example.org/synthetic-dictionary",
                "evidence": "Synthetic terminology equivalence only.",
                "status": "reviewed" if reviewed else "proposed",
                "reviewer": "Synthetic reviewer" if reviewed else None,
                "reviewed_on": "2026-10-04" if reviewed else None,
                "release_id": search_source.sources[0].release_id,
                "whole_query_only": whole_query_only,
            }
        )
    )
    manifest = search_source.model_copy(
        update={"search_profile": "polish-folded-1", "search_synonyms": (entry,)}
    )
    bundle = tmp_path / "synonym-bundle"
    _ = build_reference_artifact(manifest, tmp_path, bundle)
    with open_reference_connection(bundle) as connection:
        request = SearchRequest("icd10-pl", prepare_query("syntetyczny odpowiednik"))
        result = search_reference(connection, request)
        if reviewed:
            assert result.hits[0].code == "E11.1"
            assert result.hits[0].match_type == "synonym_tokens"
            assert result.hits[0].synonym_source_wording == "bez powikłań"
            assert result.hits[0].matched_text == "Wariant testowy bez powikłań"
            exact = search_reference(
                connection,
                SearchRequest(
                    "icd10-pl", prepare_query("Wariant testowy bez powikłań")
                ),
            )
            assert exact.hits[0].match_type == "exact_description"
        else:
            assert not result.hits
        for query in (
            "syntetyczny odpowiednik typu 9",
            "syntetyczny odpowiednik z powikłaniami",
        ):
            qualified = search_reference(
                connection, SearchRequest("icd10-pl", prepare_query(query))
            )
            assert not qualified.hits
        qualified = search_reference(
            connection,
            SearchRequest("icd10-pl", prepare_query("wariant syntetyczny odpowiednik")),
        )
        assert bool(qualified.hits) == (reviewed and not whole_query_only)


def test_synonym_hits_leave_room_for_literal_matches(
    tmp_path: Path, search_source: DatasetManifest
) -> None:
    entry = SearchSynonym.model_validate_json(
        json.dumps(
            {
                "id": "synthetic-overlap",
                "version": 1,
                "system": "icd10-pl",
                "term": "cukrzyca",
                "source_wording": "wariant",
                "source_url": "https://example.org/synthetic-dictionary",
                "evidence": "Synthetic terminology equivalence only.",
                "status": "reviewed",
                "reviewer": "Synthetic reviewer",
                "reviewed_on": "2026-10-04",
                "release_id": search_source.sources[0].release_id,
            }
        )
    )
    manifest = search_source.model_copy(
        update={"search_profile": "polish-folded-1", "search_synonyms": (entry,)}
    )
    bundle = tmp_path / "overlap-bundle"
    _ = build_reference_artifact(manifest, tmp_path, bundle)
    with open_reference_connection(bundle) as connection:
        result = search_reference(
            connection, SearchRequest("icd10-pl", prepare_query("cukrzyca"), limit=2)
        )
    kinds = [hit.match_type for hit in result.hits]
    # Three rewrite hits alone would fill the page; what the user typed still shows.
    assert "synonym_tokens" in kinds
    assert {"prefix_tokens", "morphology_tokens"} & set(kinds)
    assert result.truncated


@pytest.mark.parametrize(
    "release_id,wording",
    [
        ("absent-release", "bez powikłań"),
        ("icd10-synthetic-v1", "absent terminology"),
    ],
)
def test_reviewed_synonym_refuses_wording_outside_its_pinned_dictionary(
    tmp_path: Path, search_source: DatasetManifest, release_id: str, wording: str
) -> None:
    from minos.domain.reference import ArtifactError

    entry = SearchSynonym.model_validate_json(
        json.dumps(
            {
                "id": "synthetic-alias",
                "version": 1,
                "system": "icd10-pl",
                "term": "syntetyczny odpowiednik",
                "source_wording": wording,
                "source_url": "https://example.org/synthetic-dictionary",
                "evidence": "Synthetic source wording contract.",
                "status": "reviewed",
                "reviewer": "Synthetic reviewer",
                "reviewed_on": "2026-10-04",
                "release_id": release_id,
            }
        )
    )
    manifest = search_source.model_copy(
        update={
            "search_profile": "polish-folded-1",
            "search_synonyms": (entry,),
        }
    )
    with pytest.raises(ArtifactError, match="absent from its pinned dictionary"):
        _ = build_reference_artifact(manifest, tmp_path, tmp_path / "invalid-bundle")
    assert not (tmp_path / "invalid-bundle").exists()


def test_synonym_release_pin_isolated_and_old_entry_serialization_preserved(
    tmp_path: Path, search_source: DatasetManifest
) -> None:
    from minos.store.artifact import canonical_json

    original = {
        "id": "synthetic-alias",
        "version": 1,
        "system": "icd10-pl",
        "term": "syntetyczny odpowiednik",
        "source_wording": "bez powikłań",
        "source_url": "https://example.org/synthetic-dictionary",
        "evidence": "Synthetic source wording contract.",
        "status": "reviewed",
        "reviewer": "Synthetic reviewer",
        "reviewed_on": "2026-10-04",
    }
    old_entry = SearchSynonym.model_validate_json(json.dumps(original))
    assert (
        canonical_json(old_entry)
        == (
            json.dumps(
                original, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            )
            + "\n"
        ).encode()
    )
    pinned = old_entry.model_copy(
        update={"release_id": search_source.sources[0].release_id}
    )
    other = search_source.sources[0].model_copy(update={"release_id": "other-release"})
    manifest = search_source.model_copy(
        update={
            "sources": (*search_source.sources, other),
            "search_profile": "polish-folded-1",
            "search_synonyms": (pinned,),
        }
    )
    bundle = tmp_path / "release-scoped"
    _ = build_reference_artifact(manifest, tmp_path, bundle)
    with open_reference_connection(bundle) as connection:
        for release_id, expected in (
            (pinned.release_id, ["E11.1"]),
            (other.release_id, []),
        ):
            result = search_reference(
                connection,
                SearchRequest(
                    "icd10-pl",
                    prepare_query("syntetyczny odpowiednik"),
                    release_id=release_id,
                ),
            )
            assert [hit.code for hit in result.hits] == expected


def test_evaluation_reports_all_cases_without_query_text_and_checks_exact_release_codes(
    search_bundle: Path,
) -> None:
    gold = GoldSet.model_validate_json(
        json.dumps(
            {
                "version": 1,
                "source": "https://github.com/krzysztofbury/minos-health/issues/126",
                "notice": "Synthetic evaluation, not real gold data.",
                "releases": {"icd10-pl": "icd10-synthetic-v1"},
                "cases": [
                    {
                        "id": "G01",
                        "system": "icd10-pl",
                        "query": "E11",
                        "codes": ["E11"],
                        "category": "synthetic",
                        "source": "practitioner",
                        "baseline_rank": 1,
                    },
                    {
                        "id": "G02",
                        "system": "icd10-pl",
                        "query": "niewydolnosc",
                        "codes": ["E10.0"],
                        "category": "synthetic",
                        "source": "proposed",
                        "baseline_rank": 1,
                    },
                ],
            }
        )
    )
    report = evaluate(search_bundle, gold)
    assert report["total"] == 2
    assert report["top1"] == 1
    assert report["top5"] == 1
    assert report["baseline_regressions"] == ["G02"]
    assert report["reviewed_baseline_regressions"] == []
    assert "niewydolnosc" not in json.dumps(report)
    absent = gold.cases[0].model_copy(update={"codes": ("ZZ99",)})
    with pytest.raises(EvaluationError, match="exact pinned release"):
        _ = evaluate(search_bundle, gold.model_copy(update={"cases": (absent,)}))


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


def test_titles_rank_before_inclusion_terms_then_source_order(
    tmp_path: Path,
) -> None:
    original = DatasetManifest.model_validate_json(
        (ROOT / "manifests/sources/icd10-synthetic.json").read_bytes()
    )
    content = (ROOT / "tests/fixtures/icd10-pl/ranking.xml").read_bytes()
    source = original.sources[0].model_copy(
        update={
            "sha256": hashlib.sha256(content).hexdigest(),
            "size_bytes": len(content),
            "expected": original.sources[0].expected.model_copy(
                update={"nodes": 6, "relations": 5, "concepts": 5, "descriptions": 5}
            ),
        }
    )
    blob = tmp_path / "blobs/sha256" / source.sha256
    blob.parent.mkdir(parents=True)
    _ = blob.write_bytes(content)
    bundle = tmp_path / "bundle"
    _ = build_reference_artifact(
        original.model_copy(update={"sources": (source,)}), tmp_path, bundle
    )
    indexes: tuple[IndexMode, ...] = ("auto", "portable")
    for index in indexes:
        request = SearchRequest(
            "icd10-pl", prepare_query("zapalenie rankingowe"), index=index
        )
        with open_reference_connection(bundle) as connection:
            result = search_reference(connection, request)
        # Source order alone would return A01 (inclusion term only) first.
        assert [hit.code for hit in result.hits] == ["A03", "A02.1", "A02", "A01"]
        assert result.hits[0].matched_text == "Zapalenie rankingowe nieokreślone"
        assert result.hits[3].matched_text == "Zapalenie rankingowe płatowe"
