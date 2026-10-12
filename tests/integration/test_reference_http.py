import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from typing import TypedDict, cast

import pytest
from fastapi.testclient import TestClient

from minos.domain.morphology import PolishMorphology
from minos.domain.reference import ArtifactError, QueryLimitError, QueryTimeoutError
from minos.domain.search import SearchRequest, SearchResult
from minos.http.app import create_app
from minos.http.reference import CapacityError
from minos.ingest.reference_artifact import build_reference_artifact
from minos.store.search import search_reference
from tests.integration.test_icd9_reference import stage_companion


class Match(TypedDict):
    description_type: str
    source_attributes: dict[str, str]


class Context(TypedDict):
    edges: list[dict[str, object]]


class Hit(TypedDict):
    code: str
    source_attributes: dict[str, str]
    match: Match
    context: Context


class Answer(TypedDict):
    api: int
    results: list[Hit]
    releases: list[dict[str, object]]


def bundle(root: Path) -> Path:
    manifest = stage_companion(root)
    path = root / "bundle"
    _ = build_reference_artifact(manifest, root, path)
    return path


def request_body(
    query: str = "A01", release: str = "icd9-czs-synthetic-v1"
) -> dict[str, object]:
    return {"system": "icd9-pl", "release_id": release, "query": query}


def test_http_search_preserves_cli_evidence_and_source_selection(
    tmp_path: Path,
) -> None:
    with TestClient(create_app(bundle(tmp_path))) as client:
        assert client.get("/healthz").json()["mode"] == "validation"
        assert client.get("/readyz").status_code == 200
        metadata = cast(Answer, client.get("/api/v1/releases").json())
        assert len(metadata["releases"]) == 3
        result = cast(Answer, client.post("/api/v1/search", json=request_body()).json())
        assert result["api"] == 1
        assert result["results"][0]["source_attributes"]["status"] == "N"
        assert result["results"][0]["source_attributes"]["data_do"] == "2026-09-24"
        assert result["results"][0]["context"]["edges"] == []
        companion = cast(
            Answer,
            client.post(
                "/api/v1/search",
                json=request_body(
                    "Different synthetic title", "icd9-nfz-workbook-synthetic-v1"
                ),
            ).json(),
        )
        assert companion["results"][0]["code"] == "A01"
        assert companion["results"][0]["match"]["description_type"] == "context"
        assert (
            companion["results"][0]["match"]["source_attributes"]["evidence_kind"]
            == "conflicting_title_or_code_form"
        )
        assert len(companion["results"][0]["context"]["edges"]) == 3


@pytest.mark.parametrize(
    ("change", "status"),
    [
        ({"query": ""}, 400),
        ({"query": "x" * 257}, 400),
        ({"query": "one two three four five six seven eight nine"}, 400),
        ({"system": "unknown"}, 400),
        ({"release_id": "absent"}, 400),
        ({"limit": True}, 400),
        ({"limit": 21}, 400),
        ({"extra": "unsupported"}, 400),
        ({"query": "x" * 5000}, 413),
    ],
)
def test_invalid_http_inputs_are_bounded_and_not_echoed(
    tmp_path: Path, change: dict[str, object], status: int
) -> None:
    with TestClient(create_app(bundle(tmp_path))) as client:
        response = client.post("/api/v1/search", json={**request_body(), **change})
        assert response.status_code == status
        assert set(cast(dict[str, dict[str, str]], response.json())["error"]) == {
            "code"
        }
        assert response.headers["cache-control"] == "no-store"


def test_http_never_logs_queries_bodies_or_complete_urls(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with (
        TestClient(create_app(bundle(tmp_path))) as client,
        caplog.at_level(logging.INFO, logger="minos.http"),
    ):
        secret = "UNIQUE_PRIVATE_QUERY_SENTINEL"
        assert (
            client.post("/api/v1/search", json=request_body(secret)).status_code == 200
        )
        assert client.get("/api/v1/search?q=" + secret).status_code == 400
        assert client.post("/api/v1/search", content=secret).status_code == 415
    messages = [
        record.message for record in caplog.records if record.name == "minos.http"
    ]
    assert len(messages) == 3
    assert all(
        secret not in message and "?q=" not in message and str(tmp_path) not in message
        for message in messages
    )
    assert all(
        '"request_id"' in message and '"status_class"' in message
        for message in messages
    )


def test_http_missing_or_altered_data_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(ArtifactError):
        with TestClient(create_app(tmp_path / "missing")):
            pytest.fail("missing data started the HTTP service")
    path = bundle(tmp_path)
    with pytest.raises(ArtifactError, match="selected deployment"):
        with TestClient(create_app(path, expected_database_sha256="0" * 64)):
            pytest.fail("wrong dataset identity started the HTTP service")
    with TestClient(create_app(path)) as client:
        database = path / "minos.db"
        with database.open("ab") as stream:
            _ = stream.write(b"altered")
        assert client.post("/api/v1/search", json=request_body()).status_code == 503
        assert client.get("/readyz").status_code == 503
        assert client.get("/healthz").status_code == 200


def test_http_cannot_be_ready_without_pinned_morphology(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from minos.domain import morphology

    path = bundle(tmp_path)

    def unavailable(_name: str) -> object:
        raise ImportError("unavailable analyzer")

    monkeypatch.setattr(morphology, "import_module", unavailable)
    with pytest.raises(ArtifactError, match="pinned reference extra"):
        with TestClient(create_app(path)) as client:
            assert client.get("/readyz").status_code == 200


def test_production_profile_requirement_rejects_a_legacy_bundle(tmp_path: Path) -> None:
    with pytest.raises(ArtifactError, match="required search profile"):
        with TestClient(
            create_app(bundle(tmp_path), required_search_profile="polish-folded-1")
        ):
            pytest.fail(
                "legacy database cannot satisfy the selected deployment profile"
            )


@pytest.mark.parametrize(
    ("error_type", "status", "code"),
    [
        (CapacityError, 429, "busy"),
        (QueryTimeoutError, 504, "query_timeout"),
        (QueryLimitError, 422, "limit_exceeded"),
    ],
)
def test_search_and_detail_failures_are_distinct_and_recoverable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[Exception],
    status: int,
    code: str,
) -> None:
    from minos.http import app as adapter
    from minos.http import concepts

    def fail(*_args: object, **_kwargs: object) -> object:
        raise error_type("private diagnostic sentinel")

    with TestClient(create_app(bundle(tmp_path))) as client:
        monkeypatch.setattr(adapter, "lookup", fail)
        monkeypatch.setattr(adapter, "render_code", fail)
        monkeypatch.setattr(concepts, "concept_detail", fail)
        detail = client.get("/api/v1/concepts/icd9-pl/icd9-czs-synthetic-v1/A01")
        assert detail.status_code == status
        assert detail.json() == {"api": 1, "status": "error", "error": {"code": code}}
        assert detail.headers["cache-control"] == "no-store"
        response = client.post("/api/v1/search", json=request_body())
        assert response.status_code == status
        assert response.json() == {"api": 1, "status": "error", "error": {"code": code}}
        page = client.get("/slowniki/icd-9/A01")
        assert page.status_code == status
        assert page.headers["content-type"].startswith("text/html")
        assert page.headers["cache-control"] == "no-store"
        assert "Spróbuj ponownie" in page.text
        assert 'href="/slowniki/icd-9/A01"' in page.text
        assert "private diagnostic sentinel" not in page.text
        if status == 429:
            assert response.headers["retry-after"] == page.headers["retry-after"] == "1"


def test_busy_sql_workers_do_not_block_liveness_and_overload_is_explicit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sqlite3

    from minos.http import app as adapter

    real_search = search_reference
    entered = Event()
    release = Event()
    occupied = 0
    from threading import Lock

    lock = Lock()

    def slow(
        connection: sqlite3.Connection,
        request: SearchRequest,
        *,
        analyzer: PolishMorphology | None = None,
    ) -> SearchResult:
        nonlocal occupied
        with lock:
            occupied += 1
            if occupied == 4:
                entered.set()
        assert release.wait(5)
        return real_search(connection, request, analyzer=analyzer)

    monkeypatch.setattr(adapter, "search_reference", slow)
    with (
        TestClient(create_app(bundle(tmp_path))) as client,
        ThreadPoolExecutor(max_workers=4) as executor,
    ):
        futures = [
            executor.submit(client.post, "/api/v1/search", json=request_body())
            for _ in range(4)
        ]
        try:
            assert entered.wait(3)
            assert client.get("/healthz").status_code == 200
            assert client.post("/api/v1/search", json=request_body()).status_code == 429
        finally:
            release.set()
        assert [future.result(timeout=3).status_code for future in futures] == [200] * 4


def test_lookups_reuse_analyzers_loaded_before_readiness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Cold dictionary loads exceeded the production request budget, so every
    # analyzer must exist before readiness and no lookup may construct one.
    constructed: list[object] = []
    original = PolishMorphology.__init__

    def counting(self: PolishMorphology) -> None:
        constructed.append(self)
        original(self)

    monkeypatch.setattr(PolishMorphology, "__init__", counting)
    with TestClient(create_app(bundle(tmp_path))) as client:
        assert client.get("/readyz").status_code == 200
        loaded = len(constructed)
        assert loaded > 0
        for query in ("Different synthetic title", "synthetic procedures", "A01"):
            response = client.post("/api/v1/search", json=request_body(query))
            assert response.status_code == 200
        assert len(constructed) == loaded
