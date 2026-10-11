"""Recovery, truthful progress and shutdown at the real SQLite/HTTP boundary."""

import hashlib
import json
import logging
import sqlite3
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from threading import Event
from typing import cast

import pytest
from fastapi.testclient import TestClient

from minos.domain.grouper_candidates import Catalog
from minos.domain.reference import ArtifactError, QueryTimeoutError
from minos.grouper import pipeline
from minos.http import app as application
from minos.http.grouping import warm_grouping
from minos.http.reference import ReferencePool
from minos.store.grouper_bundle import CharacteristicsGraph, load_graph, sheet_releases
from minos.store.product_catalogs import product_catalog_releases
from tests.fixtures.grouping_interface import grouping_body, grouping_bundle


@pytest.mark.parametrize("failure", ["transient", "persistent", "invalid"])
def test_failed_sheet_keeps_truthful_progress_and_bounded_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    failure: str,
) -> None:
    bundle = grouping_bundle(tmp_path, products=True)
    with sqlite3.connect(bundle / "minos.db") as db:
        sheets = sheet_releases(db)
        products = product_catalog_releases(db)
    target = sheets[0]
    attempts = 0
    secret = "PRIVATE_EXCEPTION_WITH_PATH_AND_ENCOUNTER"

    def interrupted(
        db: sqlite3.Connection, release: str, catalog: Catalog
    ) -> CharacteristicsGraph:
        nonlocal attempts
        graph = load_graph(db, release, catalog)
        if (release, catalog) == target:
            attempts += 1
            if failure == "invalid":
                raise ArtifactError(secret)
            if failure == "persistent" or attempts == 1:
                raise QueryTimeoutError(secret)
        return graph

    monkeypatch.setattr(pipeline, "load_graph", interrupted)
    app = application.create_app(bundle)
    prepared = cast(pipeline.PreparedSources, app.state.grouping_prepared)
    with (
        caplog.at_level(logging.INFO, logger="minos.http"),
        # Use the same checksum-verified pool and adapter as application startup.
        _pool(bundle) as pool,
    ):
        warm_grouping(pool, prepared, Event())
    events = [
        cast(dict[str, object], json.loads(record.message))
        for record in caplog.records
        if record.name == "minos.http.warmup"
    ]
    summary = events[-1]
    assert summary["event"] == "grouping_warmup"
    assert secret not in caplog.text and str(tmp_path) not in caplog.text
    steps = [event for event in events if event["event"] == "grouping_warmup_step"]
    failed = [event for event in steps if event["status"] != "completed"]
    assert failed
    assert all(event["stage"] == "sheet" for event in failed)
    assert all(
        event["release_sha256"] == hashlib.sha256(target[0].encode()).hexdigest()
        and event["catalog"] == target[1]
        for event in failed
    )
    if failure == "invalid":
        assert attempts == summary["attempts"] == 1
        assert summary["status"] == "failed:ArtifactError"
        assert summary["sheets"] == summary["product_catalogs"] == 0
        assert not prepared.warmed.is_set()
        return
    assert attempts == summary["attempts"] == 2
    # Independent entries complete before retry, despite the first sheet failure.
    assert sum(
        event["stage"] == "products"
        and event["attempt"] == 1
        and event["status"] == "completed"
        for event in steps
    ) == len(products)
    assert summary["product_catalogs"] == len(products)
    if failure == "persistent":
        assert summary["status"] == "failed:QueryTimeoutError"
        assert summary["sheets"] == len(sheets) - 1
        assert not prepared.warmed.is_set()
    else:
        assert summary["status"] == "completed"
        assert summary["sheets"] == len(sheets)
        assert prepared.warmed.is_set()

        # Recovered entries must supply the first real HTTP answer without rebuild.
        def unexpected(*_args: object) -> CharacteristicsGraph:
            raise AssertionError("completed warm-up rebuilt a sheet")

        monkeypatch.setattr(pipeline, "load_graph", unexpected)
        with TestClient(app) as client:
            response = client.post("/api/v1/group", json=grouping_body())
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "evaluated"


@contextmanager
def _pool(bundle: Path) -> Generator[ReferencePool]:
    pool = ReferencePool(bundle)
    try:
        yield pool
    finally:
        pool.close()


def test_shutdown_waits_for_active_reader_before_closing_sqlite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered, release, stopped, finished = Event(), Event(), Event(), Event()
    original_close = ReferencePool.close

    def blocked(
        db: sqlite3.Connection, identity: str, catalog: Catalog
    ) -> CharacteristicsGraph:
        entered.set()
        assert release.wait(10)
        return load_graph(db, identity, catalog)

    def warm(
        pool: ReferencePool, prepared: pipeline.PreparedSources, stop: Event
    ) -> None:
        # Expose the real cancellation signal without replacing its behavior.
        nonlocal stopped
        stopped = stop
        try:
            warm_grouping(pool, prepared, stop)
        finally:
            finished.set()

    def close(pool: ReferencePool) -> None:
        assert finished.is_set(), "SQLite closed while its warm-up reader was active"
        original_close(pool)

    monkeypatch.setattr(pipeline, "load_graph", blocked)
    monkeypatch.setattr(application, "warm_grouping", warm)
    monkeypatch.setattr(ReferencePool, "close", close)
    app = application.create_app(grouping_bundle(tmp_path))

    def serve() -> None:
        with TestClient(app) as client:
            assert entered.wait(10)
            assert client.get("/readyz").status_code == 200

    with ThreadPoolExecutor(max_workers=1) as executor:
        task = executor.submit(serve)
        try:
            assert entered.wait(10)
            assert stopped.wait(10)
            assert not task.done()
        finally:
            release.set()
        task.result(timeout=10)
    assert not cast(
        pipeline.PreparedSources, app.state.grouping_prepared
    ).warmed.is_set()
