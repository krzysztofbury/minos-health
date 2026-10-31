"""Recovery, truthful progress and shutdown at the real SQLite/HTTP boundary."""

import hashlib
import json
import logging
import sqlite3
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from threading import Event
from time import monotonic, sleep
from typing import cast

import pytest
from fastapi.testclient import TestClient

from minos.domain.grouper_candidates import Catalog
from minos.domain.reference import ArtifactError, QueryTimeoutError
from minos.grouper import pipeline
from minos.grouper.warmup import SHEET_SECONDS
from minos.http import app as application
from minos.http.app import MAX_IN_FLIGHT
from minos.http.grouping import warm_grouping
from minos.http.reference import MAX_WAITERS, CapacityError, ReferencePool
from minos.store import grouper_bundle
from minos.store.grouper_bundle import CharacteristicsGraph, load_graph, sheet_releases
from minos.store.product_catalogs import product_catalog_releases
from minos.store.query_budget import query_budget
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
        db: sqlite3.Connection, release: str, catalog: Catalog, *, seconds: float
    ) -> CharacteristicsGraph:
        nonlocal attempts
        graph = load_graph(db, release, catalog, seconds=seconds)
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


def test_warm_up_gives_a_sheet_more_time_than_a_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = grouping_bundle(tmp_path)
    budgets: list[float] = []

    def recorded(
        connection: sqlite3.Connection, *, seconds: float = 1.0
    ) -> AbstractContextManager[object]:
        budgets.append(seconds)
        return query_budget(connection, seconds=seconds)

    monkeypatch.setattr(grouper_bundle, "query_budget", recorded)

    def skipped(*_args: object) -> None:
        return None

    monkeypatch.setattr(application, "warm_grouping", skipped)
    with TestClient(application.create_app(bundle)) as client:
        budgets.clear()
        response = client.post("/api/v1/group", json=grouping_body())
    assert response.status_code == 200, response.text
    assert budgets and set(budgets) == {1.0}
    budgets.clear()
    with _pool(bundle) as pool:
        warm_grouping(pool, pipeline.PreparedSources(), Event())
    assert budgets and SHEET_SECONDS in budgets
    assert SHEET_SECONDS > 1.0


def test_a_request_never_waits_past_its_budget_behind_a_slow_sheet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = grouping_bundle(tmp_path)
    entered, release = Event(), Event()
    failing = [True]

    def slow(
        db: sqlite3.Connection, identity: str, catalog: Catalog, *, seconds: float
    ) -> CharacteristicsGraph:
        entered.set()
        assert release.wait(10)
        if failing and failing.pop():
            raise QueryTimeoutError("first reader failed")
        return load_graph(db, identity, catalog, seconds=seconds)

    monkeypatch.setattr(pipeline, "load_graph", slow)
    prepared = pipeline.PreparedSources()
    with _pool(bundle) as pool, ThreadPoolExecutor(1) as threads:
        (release_id, catalog) = next(iter(sheet_list(bundle)))

        def warm() -> object:
            with pool.borrow() as lease:
                return prepared.sheet(lease.connection, release_id, catalog)

        first = threads.submit(warm)
        try:
            assert entered.wait(10)
            with pool.borrow() as lease:
                began = monotonic()
                # Other entries stay free while the sheet is being read.
                _ = prepared.algorithm(lease.connection)
                with pytest.raises(QueryTimeoutError):
                    _ = prepared.sheet(lease.connection, release_id, catalog, wait=0.05)
                assert monotonic() - began < 0.5
        finally:
            release.set()
        with pytest.raises(QueryTimeoutError):
            _ = first.result(10)
        # A failed reader leaves the entry to the next caller.
        with pool.borrow() as lease:
            graph, _index = prepared.sheet(lease.connection, release_id, catalog)
            again, _index = prepared.sheet(lease.connection, release_id, catalog)
        assert graph is again


@pytest.mark.parametrize(("owner_fails", "loads"), [(False, 1), (True, 2)])
def test_a_waiter_gets_the_sheet_or_takes_over_from_a_failed_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, owner_fails: bool, loads: int
) -> None:
    bundle = grouping_bundle(tmp_path)
    entered, release = Event(), Event()
    calls: list[str] = []

    def slow(
        db: sqlite3.Connection, identity: str, catalog: Catalog, *, seconds: float
    ) -> CharacteristicsGraph:
        calls.append(identity)
        entered.set()
        assert release.wait(10)
        if owner_fails and len(calls) == 1:
            raise QueryTimeoutError("first reader failed")
        return load_graph(db, identity, catalog, seconds=seconds)

    monkeypatch.setattr(pipeline, "load_graph", slow)
    prepared = pipeline.PreparedSources()
    (release_id, catalog) = sheet_list(bundle)[0]
    with _pool(bundle) as pool, ThreadPoolExecutor(2) as threads:

        def read() -> object:
            with pool.borrow() as lease:
                return prepared.sheet(lease.connection, release_id, catalog, wait=5)

        first = threads.submit(read)
        try:
            assert entered.wait(10)
            second = threads.submit(read)
            sleep(0.1)  # The waiter reaches the owner's event.
        finally:
            release.set()
        answer = second.result(4)
        if owner_fails:
            with pytest.raises(QueryTimeoutError):
                _ = first.result(10)
        else:
            assert first.result(10) is answer
    assert len(calls) == loads


def test_a_waiter_out_of_time_does_not_take_over(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = grouping_bundle(tmp_path)
    calls: list[str] = []

    def counted(
        db: sqlite3.Connection, identity: str, catalog: Catalog, *, seconds: float
    ) -> CharacteristicsGraph:
        calls.append(identity)
        return load_graph(db, identity, catalog, seconds=seconds)

    monkeypatch.setattr(pipeline, "load_graph", counted)
    (release_id, catalog) = sheet_list(bundle)[0]
    with _pool(bundle) as pool, pool.borrow() as lease:
        with pytest.raises(QueryTimeoutError):
            _ = pipeline.PreparedSources().sheet(
                lease.connection, release_id, catalog, wait=0
            )
    assert calls == []


def sheet_list(bundle: Path) -> list[tuple[str, Catalog]]:
    with sqlite3.connect(bundle / "minos.db") as db:
        return list(sheet_releases(db))


@contextmanager
def _pool(bundle: Path) -> Generator[ReferencePool]:
    pool = ReferencePool(bundle)
    try:
        yield pool
    finally:
        pool.close()


def test_the_waiting_room_holds_every_request_the_api_admits(tmp_path: Path) -> None:
    pool = ReferencePool(grouping_bundle(tmp_path))
    try:
        leases = len(pool._leases)  # pyright: ignore[reportPrivateUsage]
    finally:
        pool.close()
    assert leases + MAX_WAITERS == MAX_IN_FLIGHT


def test_every_admitted_request_may_wait_and_the_next_is_refused_at_once(
    tmp_path: Path,
) -> None:
    pool = ReferencePool(grouping_bundle(tmp_path), capacity=1)
    release, waiting = Event(), Event()

    def borrow() -> None:
        with pool.borrow():
            pass

    def hold() -> None:
        with pool.borrow():
            waiting.set()
            assert release.wait(10)

    try:
        with ThreadPoolExecutor(MAX_WAITERS + 1) as threads:
            holder = threads.submit(hold)
            assert waiting.wait(10)
            waiters = [threads.submit(borrow) for _ in range(MAX_WAITERS)]
            for _ in range(200):
                if pool._waiting == MAX_WAITERS:  # pyright: ignore[reportPrivateUsage]
                    break
                sleep(0.001)
            assert pool._waiting == MAX_WAITERS  # pyright: ignore[reportPrivateUsage]
            began = monotonic()
            with pytest.raises(CapacityError):
                borrow()
            assert monotonic() - began < 0.25
            release.set()
            holder.result(10)
            for waiter in waiters:
                waiter.result(10)
    finally:
        release.set()
        pool.close()


def test_shutdown_waits_for_active_reader_before_closing_sqlite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered, release, stopped, finished = Event(), Event(), Event(), Event()
    original_close = ReferencePool.close

    def blocked(
        db: sqlite3.Connection, identity: str, catalog: Catalog, *, seconds: float
    ) -> CharacteristicsGraph:
        entered.set()
        assert release.wait(10)
        return load_graph(db, identity, catalog, seconds=seconds)

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
