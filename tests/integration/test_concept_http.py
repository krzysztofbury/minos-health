import json
import logging
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import TypedDict, cast

import pytest
from fastapi.testclient import TestClient

from minos.domain.reference import QueryLimitError
from minos.http.app import create_app
from minos.http.reference import ReferencePool
from minos.ingest.reference_artifact import build_reference_artifact
from minos.store.concept_detail import MAX_NEIGHBOURS, concept_detail
from minos.store.reference import open_reference_bundle
from scripts.smoke_reference_web import fixture
from tests.integration.test_jgp_condition_consumers import consumer_bundle
from tests.integration.test_reference_http import bundle as legacy_bundle

ICD9 = "/api/v1/concepts/icd9-pl/icd9-czs-synthetic-v1/"
ICD10 = "/api/v1/concepts/icd10-pl/icd10-synthetic-v1/"


class Concept(TypedDict):
    descriptions: list[dict[str, str]]
    parents: list[dict[str, object]]
    children: list[dict[str, object]]
    source_attributes: dict[str, str]


class Supporting(TypedDict):
    release: dict[str, object]
    concept: Concept


class Rank(TypedDict):
    applicable_to_service_date: None
    evidence: dict[str, object]


class Membership(TypedDict):
    total: int
    page: int
    page_size: int
    source: dict[str, object]
    groups: list[dict[str, object]]
    terminology_release_id: str


class Jgp(TypedDict):
    service_date: str
    knowledge_as_of: str
    catalogs: list[Membership]


class Answer(TypedDict):
    api: int
    database_sha256: str
    release: dict[str, object]
    concept: Concept
    supporting_contexts: list[Supporting]
    rank_evidence: Rank
    jgp: Jgp


@pytest.fixture(scope="module")
def bundle(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return fixture(tmp_path_factory.mktemp("concept-http"))


def test_concept_wire_preserves_source_descriptions_hierarchy_and_rank_scope(
    bundle: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with (
        TestClient(create_app(bundle)) as client,
        caplog.at_level(logging.INFO, logger="minos.http"),
    ):
        response = client.get(ICD9 + "A01")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        answer = cast(Answer, response.json())
        assert answer["api"] == 1 and len(answer["database_sha256"]) == 64
        assert answer["release"]["release_id"] == "icd9-czs-synthetic-v1"
        assert answer["release"]["source_sha256"] and answer["release"]["license"]
        assert (
            answer["concept"]["descriptions"][0]["source_text"] == "  Badanie moczu  "
        )
        assert answer["concept"]["source_attributes"]["status"] == "N"
        assert answer["concept"]["source_attributes"]["data_do"] == "2026-09-24"
        assert answer["concept"]["parents"] == answer["concept"]["children"] == []
        supporting = answer["supporting_contexts"][0]
        assert supporting["release"]["release_id"] == "icd9-nfz-workbook-synthetic-v1"
        assert supporting["concept"]["parents"][0]["relation_type"] == "contains"
        assert answer["rank_evidence"]["evidence"]["rank"] == 4
        assert answer["rank_evidence"]["applicable_to_service_date"] is None
        assert answer["jgp"] is None
        assert client.get(ICD9 + "A01").content == response.content
        assert (
            client.get(ICD9 + "00.00001").json()["rank_evidence"]["evidence"]["rank"]
            == 0
        )
        assert (
            client.get(ICD9 + "A").json()["rank_evidence"]["evidence"]["rank"] is None
        )
        diagnosis = cast(Answer, client.get(ICD10 + "E11").json())
        assert {d["language"] for d in diagnosis["concept"]["descriptions"]} == {
            "pl",
            "en",
        }
        assert [
            (c["code"], c["source_code"], c["relation_type"])
            for c in diagnosis["concept"]["children"]
        ] == [("E11.0", ".0", "contains"), ("E11.2", ".2†", "contains")]
        assert diagnosis["concept"]["parents"][0]["kind"] == "container"
        assert diagnosis["rank_evidence"] is None
    messages = [r.message for r in caplog.records if r.name == "minos.http"]
    assert messages and all('"route": "/api/v1/concepts"' in m for m in messages)
    assert all(
        "A01" not in m and "E11" not in m and "synthetic-v1" not in m for m in messages
    )


def test_dated_context_keeps_dictionary_and_rank_unchanged_and_rejects_unknown_dates(
    bundle: Path,
) -> None:
    with TestClient(create_app(bundle)) as client:
        plain = cast(dict[str, object], client.get(ICD9 + "A01").json())
        dated = client.get(ICD9 + "A01/jgp/2026-07-01")
        assert dated.status_code == 200
        for field in ("concept", "release", "rank_evidence", "supporting_contexts"):
            assert dated.json()[field] == plain[field]
        memberships = cast(Answer, client.get(ICD10 + "E11.0/jgp/2026-07-01").json())[
            "jgp"
        ]
        assert memberships["service_date"] == "2026-07-01"
        assert [g["code"] for g in memberships["catalogs"][0]["groups"]] == ["A01"]
        assert (
            memberships["catalogs"][0]["terminology_release_id"] == "icd10-synthetic-v1"
        )
        assert memberships["knowledge_as_of"] == "2026-10-02"
        future = client.get(ICD10 + "E11.0/jgp/2026-10-03")
        assert future.status_code == 200
        assert future.json()["jgp"]["beyond_knowledge_horizon"] is True
        failure = client.get(ICD10 + "E11.0/jgp/2026-06-30")
        assert (
            failure.status_code == 404
            and failure.json()["error"]["code"] == "no_ruleset_for_date"
        )
        for suffix in (
            "E11.0/jgp/2026-02-30",
            "e11.0",
            "E11.0/jgp/2026-7-01",
            "E11.0/jgp/2026-07-01/1a/pages/21",
        ):
            assert client.get(ICD10 + suffix).status_code == 404
        for route in (
            ICD10.replace("icd10-pl", "icd9-pl") + "E11.0",
            ICD9 + "05.01",
            ICD9.replace("czs", "nfz-workbook") + "A01",
            ICD10.replace("icd10-pl", "unsupported") + "E11",
        ):
            assert client.get(route).status_code == 404
        rejected = client.get(ICD9 + "A01?date=2026-07-01")
        assert (
            rejected.status_code == 400
            and rejected.json()["error"]["code"] == "queries_must_not_be_in_urls"
        )
        assert client.post(ICD9 + "A01").status_code == 405


def test_legacy_dictionary_has_explicitly_absent_jgp_context(tmp_path: Path) -> None:
    with TestClient(create_app(legacy_bundle(tmp_path))) as client:
        response = client.get(ICD9 + "A01")
        assert response.status_code == 200
        assert response.json()["rank_evidence"]["evidence"]["status"] == "not_selected"
        assert (
            client.get(ICD9 + "A01/jgp/2026-07-01").json()["error"]["code"]
            == "no_ruleset_for_date"
        )


def test_membership_pagination_is_explicit_and_complete(tmp_path: Path) -> None:
    with TestClient(create_app(consumer_bundle(tmp_path, many_groups=True))) as client:
        route = ICD10 + "E11.0/jgp/2026-07-01/1a/pages/"
        first = cast(Answer, client.get(route + "1").json())["jgp"]["catalogs"][0]
        second = cast(Answer, client.get(route + "2").json())["jgp"]["catalogs"][0]
        assert first["total"] == second["total"] == 102
        assert len(first["groups"]) == first["page_size"] == 100 and first["page"] == 1
        assert len(second["groups"]) == 2 and second["page"] == 2
        assert client.get(route + "3").status_code == 404


def test_real_pool_exhaustion_and_integrity_failure_have_safe_errors(
    bundle: Path, tmp_path: Path
) -> None:
    import shutil
    from contextlib import ExitStack

    private = tmp_path / "bundle"
    _ = shutil.copytree(bundle, private)
    app = create_app(private)
    with TestClient(app) as client:
        pool = cast(ReferencePool, app.state.pool)
        with ExitStack() as leases:
            for _ in range(4):
                _ = leases.enter_context(pool.borrow())
            busy = client.get(ICD9 + "A01")
            assert busy.status_code == 429 and busy.headers["retry-after"] == "1"
        assert client.get(ICD9 + "A01").status_code == 200
        (private / "minos.db").chmod(0o600)
        with (private / "minos.db").open("ab") as output:
            _ = output.write(b"altered")
        response = client.get(ICD9 + "A01")
        assert response.status_code == 503
        assert str(private) not in response.text


def test_dated_membership_never_joins_another_dictionary_release(
    tmp_path: Path,
) -> None:
    # Build a valid second canonical dictionary, while the characteristics retain
    # their original explicit binding. Equal codes do not make releases equal.
    from minos.ingest.reference_manifest import DatasetManifest
    from tests.integration.test_jgp_conditions_reference import conditions_manifest

    base = conditions_manifest(tmp_path)
    extra = base.sources[0].model_copy(update={"release_id": "icd10-other"})
    data = base.model_dump(mode="json")
    data["sources"] = [
        *cast(list[object], data["sources"]),
        extra.model_dump(mode="json"),
    ]
    selected = DatasetManifest.model_validate_json(json.dumps(data))
    path = tmp_path / "bundle"
    _ = build_reference_artifact(selected, tmp_path, path)
    with TestClient(create_app(path)) as client:
        route = "/api/v1/concepts/icd10-pl/icd10-other/E11.0"
        assert client.get(route).status_code == 200
        response = client.get(route + "/jgp/2026-07-01")
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "context_release_mismatch"


@pytest.mark.parametrize(
    "boundary", ["descriptions", "attributes", "children", "bytes"]
)
def test_store_bounds_refuse_incomplete_answers(bundle: Path, boundary: str) -> None:
    with (
        open_reference_bundle(bundle) as (source, manifest),
        closing(sqlite3.connect(":memory:")) as connection,
    ):
        source.backup(connection)
        release = "icd9-czs-synthetic-v1"
        ordinal = 3
        if boundary == "descriptions":
            _ = connection.executemany(
                "INSERT INTO concept_description(source_release_id,node_ordinal,language,description_type,source_text,normalized_text) VALUES (?,?,?,'alternate','extra','extra')",
                [(release, ordinal, f"x-{i}") for i in range(129)],
            )
        elif boundary == "attributes":
            _ = connection.executemany(
                "INSERT INTO reference_node_attribute VALUES (?,?,?,?,?)",
                [(release, ordinal, i + 100, f"attr-{i}", "value") for i in range(65)],
            )
        elif boundary == "children":
            _ = connection.executemany(
                "INSERT INTO reference_node VALUES (?,?,?,'child','text')",
                [(release, i + 100, "") for i in range(MAX_NEIGHBOURS + 1)],
            )
            _ = connection.executemany(
                "INSERT INTO reference_relation VALUES (?,3,?,'contains')",
                [(release, i + 100) for i in range(MAX_NEIGHBOURS + 1)],
            )
        elif boundary == "bytes":
            _ = connection.execute(
                "UPDATE concept_description SET source_text=? WHERE source_release_id=? AND node_ordinal=?",
                ("x" * 1048576, release, ordinal),
            )
        with pytest.raises(QueryLimitError):
            _ = concept_detail(connection, manifest, "icd9-pl", release, "A01")
