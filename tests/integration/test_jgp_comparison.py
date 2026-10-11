"""Dated, source-backed UI comparisons through the verified HTTP boundary."""

import logging
import os
import re
import sqlite3
from collections.abc import Generator, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import cast

import pytest
from fastapi.testclient import TestClient

from minos.domain.reference import QueryLimitError, QueryTimeoutError
from minos.http import reference as reference_http
from minos.http.app import create_app
from minos.http.jgp_comparison_pages import comparison_path
from minos.ingest.reference_artifact import build_reference_artifact
from minos.store import comparison_snapshot
from minos.store.artifact import ArtifactManifest, dataset_identity
from minos.store.jgp_comparison import scope_identity
from minos.store.reference import open_reference_bundle, read_artifact_manifest
from tests.fixtures.jgp_catalog import (
    Row,
    catalog_workbook,
    default_sheets,
    stage_catalog,
)
from tests.integration.test_jgp_api import (
    ORIGINAL_ROWS,
    lineage_bundle,
    original_release,
)
from tests.integration.test_jgp_api import dataset as fixture_dataset


@pytest.fixture(scope="module")
def comparison_client(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[tuple[TestClient, str]]:
    bundle = lineage_bundle(tmp_path_factory.mktemp("comparison"))
    identity = dataset_identity(read_artifact_manifest(bundle).dataset)
    with TestClient(create_app(bundle)) as client:
        yield client, identity


def test_shared_comparison_preserves_exact_values_and_reverses_additions(
    comparison_client: tuple[TestClient, str],
) -> None:
    client, dataset = comparison_client
    path = comparison_path(
        dataset,
        "2026-06-15",
        "2026-07-10",
        "jgp-catalog-original",
        "jgp-catalog-synthetic",
    )
    response = client.get(path)
    assert response.status_code == 200, response.text
    assert response.text == client.get(path).text
    assert 'data-group="H21" data-action="Dodana"' in response.text
    assert 'data-group="PZD02" data-action="Zmieniona"' in response.text
    assert "<td>8 040,00</td><td>12 871,72</td>" in response.text
    assert "brak wartości w katalogu" in response.text
    assert "grupa nieobecna" in response.text
    assert "jgp-catalog-original" in response.text
    assert "jgp-catalog-synthetic" in response.text
    assert "To nie są punkty do rozliczenia" in response.text
    assert "nie jest pełnym audytem wydania" in response.text
    reverse = client.get(
        comparison_path(
            dataset,
            "2026-07-10",
            "2026-06-15",
            "jgp-catalog-synthetic",
            "jgp-catalog-original",
        )
    )
    assert reverse.status_code == 200
    assert 'data-group="H21" data-action="Usunięta"' in reverse.text
    assert "<td>12 871,72</td><td>8 040,00</td>" in reverse.text


def test_same_release_selects_dated_twins_and_scope_from_either_side(
    comparison_client: tuple[TestClient, str],
) -> None:
    client, dataset = comparison_client
    scope = scope_identity("chirurgia ogólna")
    path = comparison_path(
        dataset,
        "2026-12-31",
        "2027-01-01",
        "jgp-catalog-synthetic",
        "jgp-catalog-synthetic",
        scope,
        "H21",
    )
    response = client.get(path)
    assert response.status_code == 200, response.text
    assert re.findall(r'data-group="([A-Z0-9]+)"', response.text) == ["H21"]
    assert "<td>3 974,00</td><td>3 974,00</td>" in response.text
    assert "Synthetic § 4 ust. 5." in response.text
    assert "Stan wiedzy Minos" in response.text
    unchanged = client.get(
        comparison_path(
            dataset,
            "2026-07-10",
            "2026-07-11",
            "jgp-catalog-synthetic",
            "jgp-catalog-synthetic",
        )
    )
    assert unchanged.status_code == 200
    assert "Brak zmian w porównywanym zakresie" in unchanged.text
    assert 'class="compare-change"' not in unchanged.text


@pytest.mark.parametrize(
    ("before", "after", "scope", "group", "foreign", "status"),
    [
        ("2026-04-30", "2026-07-10", "wszystkie", "wszystkie", False, 404),
        ("2026-06-15", "2026-06-30", "wszystkie", "wszystkie", False, 409),
        ("2026-02-30", "2026-07-10", "wszystkie", "wszystkie", False, 404),
        ("2026-06-15", "2026-07-10", "0" * 64, "wszystkie", False, 404),
        ("2026-06-15", "2026-07-10", "wszystkie", "ZZ99", False, 404),
        ("2026-06-15", "2026-07-10", "wszystkie", "wszystkie", True, 409),
    ],
)
def test_unavailable_selection_never_falls_back(
    comparison_client: tuple[TestClient, str],
    before: str,
    after: str,
    scope: str,
    group: str,
    foreign: bool,
    status: int,
) -> None:
    client, dataset = comparison_client
    response = client.get(
        comparison_path(
            "0" * 64 if foreign else dataset,
            before,
            after,
            "jgp-catalog-original",
            "jgp-catalog-synthetic",
            scope,
            group,
        )
    )
    assert response.status_code == status
    assert response.headers["cache-control"] == "no-store"
    assert 'class="compare-change"' not in response.text


def test_entry_link_and_privacy_safe_read_only_contract(
    comparison_client: tuple[TestClient, str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    client, dataset = comparison_client
    page = client.get("/jgp/2026-07-10")
    path = comparison_path(
        dataset,
        "2026-07-10",
        "2026-07-10",
        "jgp-catalog-original",
        "jgp-catalog-synthetic",
    )
    assert f'href="{path}"' in page.text
    with caplog.at_level(logging.INFO, logger="minos.http"):
        assert client.get(path).status_code == 200
        assert client.post(path).status_code == 405
        assert client.get(path + "?PRIVATE_QUERY=secret").status_code == 400
    assert '"route": "/jgp"' in caplog.text
    assert "PRIVATE_QUERY" not in caplog.text and "secret" not in caplog.text


def test_explicit_releases_compare_retroactive_changes_on_the_same_day(
    comparison_client: tuple[TestClient, str],
) -> None:
    client, dataset = comparison_client
    path = comparison_path(
        dataset,
        "2026-07-10",
        "2026-07-10",
        "jgp-catalog-original",
        "jgp-catalog-synthetic",
        group="PZD02",
    )
    response = client.get(path)
    assert response.status_code == 200
    assert "<td>8 040,00</td><td>12 871,72</td>" in response.text
    assert 'href="https://baw.nfz.gov.pl/NFZ/document/43868/Synthetic"' in response.text
    assert 'href="/jgp/2026-07-10/PZD02"' not in response.text
    missing = client.get(path.replace("jgp-catalog-original", "missing-pinned-release"))
    assert missing.status_code == 409 and 'class="compare-change"' not in missing.text


def test_same_service_start_keeps_both_amendments_selectable(tmp_path: Path) -> None:
    sheets: dict[str, tuple[Row, ...]] = {"nfz-1a-74-2026": ORIGINAL_ROWS}
    old = stage_catalog(
        tmp_path,
        catalog_workbook(sheets),
        sheets=sheets,
        release={
            "release_id": "reviewed-before",
            "row_applicability": (),
            "service_evidence": "Synthetic 74/2026/DSOZ: services from July.",
        },
    )
    new = stage_catalog(
        tmp_path,
        catalog_workbook(default_sheets()),
        release={
            "release_id": "reviewed-after",
            "supersedes": "reviewed-before",
            "baw_document_id": 44042,
            "introductory_document_id": 44041,
            "document_url": "https://baw.nfz.gov.pl/NFZ/document/44042/Synthetic",
            "published_on": date(2026, 9, 23),
            "service_evidence": "Synthetic 102/2026/DSOZ: services from July.",
        },
    )
    manifest = fixture_dataset(
        tmp_path, [old.model_dump(mode="json"), new.model_dump(mode="json")]
    )
    bundle = tmp_path / "bundle"
    _ = build_reference_artifact(manifest, tmp_path, bundle)
    identity = dataset_identity(manifest)
    path = comparison_path(
        identity, "2026-07-10", "2026-07-10", "reviewed-before", "reviewed-after"
    )
    with TestClient(create_app(bundle)) as client:
        response = client.get(path)
        assert response.status_code == 200, response.text
        assert "<td>8 040,00</td><td>12 871,72</td>" in response.text
        assert "Synthetic 74/2026/DSOZ (2026-07-23)" in response.text
        assert "Synthetic 102/2026/DSOZ (2026-09-23)" in response.text
        # A date outside the selected release is refused, never substituted.
        assert client.get(path.replace("2026-07-10", "2026-06-30")).status_code == 404


@pytest.mark.parametrize(
    "limit",
    [
        "MAX_SOURCE_ROWS",
        "MAX_SOURCE_BYTES",
        "MAX_GROUPS",
        "MAX_SCHEDULE_ROWS",
        "MAX_RETAINED_BYTES",
        "MAX_PRELOAD_SECONDS",
    ],
)
def test_preload_limits_fail_startup_and_close_verified_readers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    limit: str,
) -> None:
    bundle = lineage_bundle(tmp_path)
    opened: list[sqlite3.Connection] = []

    @contextmanager
    def tracked_open(
        path: Path, *, check_same_thread: bool
    ) -> Generator[tuple[sqlite3.Connection, ArtifactManifest]]:
        with open_reference_bundle(path, check_same_thread=check_same_thread) as result:
            opened.append(result[0])
            yield result

    monkeypatch.setattr(reference_http, "open_reference_bundle", tracked_open)
    monkeypatch.setattr(comparison_snapshot, limit, 0)
    with pytest.raises(
        QueryTimeoutError if limit == "MAX_PRELOAD_SECONDS" else QueryLimitError
    ):
        with TestClient(create_app(bundle)):
            pytest.fail("failed preload must not reach readiness")
    assert len(opened) == 4
    for connection in opened:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            _ = connection.execute("SELECT 1")


def test_cached_comparisons_are_bundle_local_and_preserve_runtime_identity_checks(
    tmp_path: Path,
) -> None:
    first = lineage_bundle(tmp_path / "first")
    second_root = tmp_path / "second"
    second_root.mkdir()
    sheets = default_sheets()
    for sheet, rows in sheets.items():
        sheets[sheet] = tuple(
            replace(row, values={**row.values, "F": 8888})
            if row.code == "PZD02"
            else row
            for row in rows
        )
    changed = stage_catalog(
        second_root,
        catalog_workbook(sheets),
        sheets=sheets,
        release={"supersedes": "jgp-catalog-original"},
    )
    manifest = fixture_dataset(
        second_root, [original_release(second_root), changed.model_dump(mode="json")]
    )
    second = second_root / "bundle"
    _ = build_reference_artifact(manifest, second_root, second)

    def deny_source_reads(
        action: int,
        table: str | None,
        column: str | None,
        database: str | None,
        trigger: str | None,
    ) -> int:
        del column, database, trigger
        return (
            sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_READ
            and table
            in {
                "group_definition",
                "group_scope",
                "group_base_value",
            }
            else sqlite3.SQLITE_OK
        )

    applications = [create_app(bundle) for bundle in (first, second)]
    with ExitStack() as stack:
        clients = [stack.enter_context(TestClient(app)) for app in applications]
        for app in applications:
            pool = cast(reference_http.ReferencePool, app.state.pool)
            with ExitStack() as leases:
                for _ in range(4):
                    lease = leases.enter_context(pool.borrow())
                    lease.connection.set_authorizer(deny_source_reads)
                    with pytest.raises(sqlite3.DatabaseError):
                        _ = lease.connection.execute(
                            "SELECT name FROM group_definition"
                        )
        paths = [
            comparison_path(
                dataset_identity(read_artifact_manifest(bundle).dataset),
                "2026-07-10",
                "2026-07-10",
                "jgp-catalog-original",
                "jgp-catalog-synthetic",
                group="PZD02",
            )
            for bundle in (first, second)
        ]

        def fetch(index: int) -> str:
            response = clients[index].get(paths[index])
            assert response.status_code == 200, response.text
            return response.text

        with ThreadPoolExecutor(max_workers=4) as workers:
            answers = list(workers.map(fetch, [0, 1, 0, 1]))
        assert answers[0] == answers[2] and answers[1] == answers[3]
        assert "<td>8 040,00</td><td>12 871,72</td>" in answers[0]
        assert "<td>8 040,00</td><td>8 888,00</td>" in answers[1]
        assert clients[1].get(paths[0]).status_code == 409
        database = first / "minos.db"
        metadata = database.stat()
        os.utime(database, ns=(metadata.st_atime_ns, metadata.st_mtime_ns + 1))
        assert clients[0].get(paths[0]).status_code == 503
