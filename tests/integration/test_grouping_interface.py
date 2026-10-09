"""Actual HTTP and stdin CLI boundaries, including input privacy and recovery."""

import asyncio
import json
import logging
import subprocess
import sys
import time
from collections.abc import AsyncIterator, Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from threading import Event
from typing import cast

import httpx
import pytest
from fastapi.testclient import TestClient

from minos.http.app import create_app
from tests.fixtures.grouping_interface import grouping_body, grouping_bundle


def test_http_and_installed_cli_share_versioned_trace(tmp_path: Path) -> None:
    bundle = grouping_bundle(tmp_path)
    body = grouping_body()
    with TestClient(create_app(bundle)) as client:
        response = client.post("/api/v1/group", json=body)
        assert response.status_code == 200, response.text
        answer = cast(dict[str, object], response.json())
        assert answer["schema"] == "minos.grouping.v1"
        assert answer["tariff_status"] == "unbound"
        group = next(
            g
            for g in cast(list[dict[str, object]], answer["groups"])
            if g["group"] == "A01"
        )
        assert group["status"] == "valid"
        assert group["final_points"] is None
        trace = cast(list[dict[str, object]], group["trace"])
        assert trace[0]["matched_codes"] == ["100.01"]
        assert trace[1]["matched_codes"] == ["G60.4"]
        assert all(event["source_rows"] for event in trace)
        skipped = [
            (catalog, code)
            for entry in cast(list[dict[str, object]], answer["not_evaluated"])
            for catalog, codes in cast(dict[str, list[str]], entry["groups"]).items()
            for code in codes
        ]
        evaluated = [
            (g["catalog"], g["group"])
            for g in cast(list[dict[str, object]], answer["groups"])
        ]
        assert skipped and len(set(skipped)) == len(skipped)
        assert not set(skipped) & set(evaluated)
        assert response.headers["cache-control"] == "no-store"
        assert client.post("/api/v1/group", json=body).json() == answer
    process = subprocess.run(
        [
            str(Path(sys.executable).with_name("minos")),
            "group",
            "--data",
            str(bundle),
            "--json",
        ],
        input=json.dumps(body),
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert process.returncode == 0, process.stderr
    assert json.loads(process.stdout) == answer
    assert not process.stderr


def test_http_reuses_release_static_inputs_without_changing_answers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sqlite3

    from minos.domain.grouper_candidates import Catalog
    from minos.grouper import pipeline
    from minos.store.grouper_bundle import CharacteristicsGraph, load_graph

    loads: list[str] = []

    def counted(
        connection: sqlite3.Connection, release_id: str, catalog: Catalog
    ) -> CharacteristicsGraph:
        loads.append(release_id)
        return load_graph(connection, release_id, catalog)

    monkeypatch.setattr(pipeline, "load_graph", counted)
    app = create_app(grouping_bundle(tmp_path))
    with TestClient(app) as client:
        # The background warm-up loads every sheet once; requests add none.
        assert cast(pipeline.PreparedSources, app.state.grouping_prepared).warmed.wait(
            10
        )
        loaded = len(loads)
        first = client.post("/api/v1/group", json=grouping_body())
        second = client.post("/api/v1/group", json=grouping_body())
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    # #149: decoding the characteristics graph dominated every request.
    assert loaded >= 1 and len(loads) == loaded


def test_first_request_after_start_uses_warmed_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from minos.grouper import pipeline

    def unexpected(*_args: object) -> object:
        raise AssertionError("a request rebuilt release-static inputs")

    app = create_app(grouping_bundle(tmp_path))
    with TestClient(app) as client:
        prepared = cast(pipeline.PreparedSources, app.state.grouping_prepared)
        # #149: the first request after a restart must not pay the build.
        assert prepared.warmed.wait(10)
        monkeypatch.setattr(pipeline, "load_graph", unexpected)
        monkeypatch.setattr(pipeline, "load_bundle_algorithm", unexpected)
        response = client.post("/api/v1/group", json=grouping_body())
        assert client.get("/readyz").status_code == 200
    assert response.status_code == 200, response.text


def test_dictionaries_offer_named_codes_with_derived_children(tmp_path: Path) -> None:
    from tests.integration.test_jgp_condition_consumers import consumer_bundle

    with TestClient(create_app(grouping_bundle(tmp_path / "with"))) as client:
        response = client.get("/api/v1/grouping/dictionaries")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "public, max-age=300"
        answer = cast(dict[str, dict[str, object]], response.json())
        assert client.get("/api/v1/grouping/dictionaries").json() == answer
    entries = cast(list[dict[str, object]], answer["specialties"]["entries"])
    named = {str(entry["code"]): entry for entry in entries}
    assert named["4000"]["derived"] is False
    # § 9 ust. 4: the children's unit of 4000 is 4001; 4401 is itself odd.
    assert named["4001"] == {**named["4000"], "code": "4001", "derived": True}
    assert "4402" not in named
    modes = cast(list[dict[str, object]], answer["discharge_modes"]["entries"])
    assert modes == [
        {"code": 1, "name": "zakończenie procesu terapeutycznego lub diagnostycznego"},
        {"code": 9, "name": "zgon pacjenta"},
    ]
    with TestClient(create_app(consumer_bundle(tmp_path / "without"))) as client:
        missing = client.get("/api/v1/grouping/dictionaries")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "dictionaries_unavailable"


def test_warmup_status_reaches_the_configured_api_logger(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from minos.grouper import pipeline

    app = create_app(grouping_bundle(tmp_path))
    with caplog.at_level(logging.INFO, logger="minos.http"), TestClient(app):
        prepared = cast(pipeline.PreparedSources, app.state.grouping_prepared)
        assert prepared.warmed.wait(10)
        for _ in range(100):
            if any(r.name == "minos.http.warmup" for r in caplog.records):
                break
            time.sleep(0.02)
    events = [
        cast(dict[str, object], json.loads(record.getMessage()))
        for record in caplog.records
        if record.name == "minos.http.warmup"
    ]
    # #155: only the `minos.http` hierarchy has a handler in production.
    assert len(events) == 1
    assert set(events[0]) == {
        "event",
        "status",
        "sheets",
        "product_catalogs",
        "duration_ms",
    }
    assert events[0]["status"] == "completed"


def _case_variants() -> dict[str, dict[str, object]]:
    def changed(edit: Callable[[dict[str, object]], None]) -> dict[str, object]:
        body = grouping_body()
        edit(cast(dict[str, object], body["encounter"]))
        return body

    return {
        "valid": grouping_body(),
        # Specialty 0010 is excluded only for service code 4, which is unknown here.
        "unresolved_stay": changed(
            lambda e: cast(list[dict[str, object]], e["stays"])[0].__setitem__(
                "department", "0010"
            )
        ),
        "no_procedure": changed(lambda e: e.__setitem__("procedures", [])),
        "unattributed": changed(
            lambda e: e.__setitem__(
                "procedures", [{"code": "100.01", "performed_on": "2026-10-02"}]
            )
        ),
        "unrelated": changed(
            lambda e: e.__setitem__(
                "principal_diagnosis", {"code": "Z00.0", "hospitalization_level": True}
            )
        ),
    }


def test_every_group_outcome_is_explained_by_the_trace_alone(tmp_path: Path) -> None:
    """#36: 'why this group and not X' needs no engine code, for every case."""
    import sqlite3

    from minos.store.grouper_rules import group_titles

    bundle = grouping_bundle(tmp_path)
    unknown_report = grouping_body()
    cast(dict[str, object], unknown_report["encounter"])["product_reporting"] = (
        "unknown"
    )
    with TestClient(create_app(bundle)) as client:
        answers = {
            name: cast(
                dict[str, object], client.post("/api/v1/group", json=body).json()
            )
            for name, body in _case_variants().items()
        }
        unsupported = cast(
            dict[str, object], client.post("/api/v1/group", json=unknown_report).json()
        )
    # Without an evaluable product report there is no trace; the answer says why.
    assert unsupported["status"] == "unsupported_product_report"
    assert any(
        "produkt" in text for text in cast(list[str], unsupported["limitations"])
    )
    for name, answer in answers.items():
        sources = cast(dict[str, object], answer["sources"])
        with sqlite3.connect(f"file:{bundle / 'minos.db'}?mode=ro", uri=True) as db:
            titled = set(
                group_titles(
                    db,
                    str(sources["catalog_release_id"]),
                    date.fromisoformat(str(answer["service_date"])),
                )
            )
            rows = cast(
                list[tuple[str, str]],
                db.execute(
                    "SELECT catalog, code FROM characteristics_group WHERE release_id=?",
                    (str(sources["characteristics_release_id"]),),
                ).fetchall(),
            )
            characterised = set(rows)
        groups = cast(list[dict[str, object]], answer["groups"])
        evaluated = [(str(g["catalog"]), str(g["group"])) for g in groups]
        placed: dict[tuple[str, str], str] = {}
        for entry in cast(list[dict[str, object]], answer["not_evaluated"]):
            for catalog, codes in cast(dict[str, list[str]], entry["groups"]).items():
                for code in codes:
                    assert (catalog, code) not in placed, (name, code)
                    placed[(catalog, code)] = str(entry["reason"])
        underivable = {
            (str(item["catalog"]), str(item["group"]))
            for item in cast(list[dict[str, object]], answer["underivable"])
        }
        answered = set(evaluated) | set(placed) | underivable
        # Every titled or characterised group is answered, none twice.
        assert answered >= titled | characterised, name
        assert len(set(evaluated)) == len(evaluated) and not set(evaluated) & set(
            placed
        )
        # A placement reason is checked independently of the serializer.
        for key, reason in placed.items():
            assert (reason == "no_characteristics_group") == (key not in characterised)
        for group in groups:
            trace = cast(list[dict[str, object]], group["trace"])
            nodes = {cast(int, event["node"]): event for event in trace}
            consulted: set[int] = set()
            pending = list(cast(list[int], group["roots"]))
            while pending:
                node = pending.pop()
                if node in consulted:
                    continue
                consulted.add(node)
                event = nodes[node]
                # Only executable expressions consult their children.
                if event["kind"] in ("all", "any"):
                    parameters = cast(dict[str, object], event["parameters"])
                    pending.extend(cast(list[int], parameters.get("children", [])))
            # The trace is exactly the consulted conditions, nothing dangling.
            assert set(nodes) == consulted, (name, group["group"])
            verdicts = {str(event["verdict"]) for event in trace}
            causes = set(cast(list[str], group["reasons"]))
            if group["status"] == "invalid":
                assert (
                    "not_met" in verdicts
                    or group["routing"] == "not_met"
                    or ("not_in_governing_catalog" in causes)
                ), (name, group["group"])
            elif group["status"] == "undetermined":
                assert (
                    "undetermined" in verdicts or group["routing"] == "undetermined"
                ), (
                    name,
                    group["group"],
                )
            assert len(cast(list[str], group["explanations"])) == len(causes)


def test_trace_preserves_attribution_and_canonical_stay_ordinals(
    tmp_path: Path,
) -> None:
    body = grouping_body()
    encounter = cast(dict[str, object], body["encounter"])
    stays = cast(list[dict[str, object]], encounter["stays"])
    stays.append(
        {
            "identifier": "later",
            "department": "4500",
            "admitted_on": "2026-10-03",
            "discharged_on": "2026-10-05",
        }
    )
    encounter["secondary_diagnoses"] = [{"code": "E11.0", "stay_id": "ward"}]
    with TestClient(create_app(grouping_bundle(tmp_path))) as client:
        first = cast(dict[str, object], client.post("/api/v1/group", json=body).json())
        stays.reverse()
        second = cast(dict[str, object], client.post("/api/v1/group", json=body).json())
        assert first == second
        prepared = cast(dict[str, object], first["preparation"])
        assert [
            s["identifier"] for s in cast(list[dict[str, object]], prepared["stays"])
        ] == ["ward", "later"]
        assert (
            cast(list[dict[str, object]], prepared["secondary_diagnoses"])[0]["stay_id"]
            == "ward"
        )
        assert (
            cast(dict[str, object], prepared["principal_diagnosis"])[
                "hospitalization_level"
            ]
            is True
        )


def test_post_privacy_for_valid_invalid_exception_and_url_paths(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    from minos.http import grouping

    secret = "PRIVATE_ENCOUNTER_SENTINEL"
    with (
        TestClient(create_app(grouping_bundle(tmp_path))) as client,
        caplog.at_level(logging.INFO, logger="minos.http"),
    ):
        assert client.post("/api/v1/group", json=grouping_body()).status_code == 200
        invalid = {**grouping_body(), "patient_name": secret}
        assert client.post("/api/v1/group", json=invalid).status_code == 400
        assert client.post("/api/v1/group", content=secret).status_code == 415
        assert (
            client.post(
                "/api/v1/group?private=" + secret, json=grouping_body()
            ).status_code
            == 400
        )

        def fail(*_args: object) -> bytes:
            raise RuntimeError(secret)

        monkeypatch.setattr(grouping, "evaluate", fail)
        response = client.post("/api/v1/group", json=grouping_body())
        assert response.status_code == 503 and secret not in response.text
    logs = [record.message for record in caplog.records if record.name == "minos.http"]
    assert len(logs) == 5
    for message in logs:
        assert all(
            value not in message
            for value in (secret, "G60.4", "100.01", "2026-10-05", str(tmp_path))
        )
        assert set(cast(dict[str, object], json.loads(message))) == {
            "request_id",
            "route",
            "method",
            "status_class",
            "status_code",
            "failure_code",
            "duration_ms",
            "result_count",
            "dataset_id",
        }


@pytest.mark.parametrize(
    "change",
    [
        {"schema_version": True},
        {"schema_version": 2},
        {"reporting_month": "2026-10-02"},
        {"service_date": "2026-09-30"},
        {"declared_modifiers": ["17.1", "17.1"]},
        {"declared_modifiers": ["private input\n"]},
    ],
)
def test_invalid_envelopes_do_not_echo_values(
    tmp_path: Path, change: dict[str, object]
) -> None:
    with TestClient(create_app(grouping_bundle(tmp_path))) as client:
        response = client.post("/api/v1/group", json={**grouping_body(), **change})
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_request"


def test_payload_caps_duplicate_keys_and_product_unknowns(tmp_path: Path) -> None:
    with TestClient(create_app(grouping_bundle(tmp_path))) as client:
        assert (
            client.post(
                "/api/v1/group",
                content=b" " * (256 * 1024 + 1),
                headers={"Content-Type": "application/json"},
            ).status_code
            == 413
        )
        payload = json.dumps(grouping_body()).replace(
            '"schema_version": 1', '"schema_version": 1, "schema_version": 1'
        )
        assert (
            client.post(
                "/api/v1/group",
                content=payload,
                headers={"Content-Type": "application/json"},
            ).status_code
            == 400
        )
        body = grouping_body()
        cast(dict[str, object], body["encounter"])["product_reporting"] = "unknown"
        answer = cast(dict[str, object], client.post("/api/v1/group", json=body).json())
        assert (
            answer["status"] == "unsupported_product_report" and answer["groups"] == []
        )
        assert client.get("/healthz").status_code == 200


def test_grouping_admission_preserves_search_capacity_and_recovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from minos.grouper.pipeline import PreparedSources
    from minos.http import grouping
    from minos.http.reference import ReferencePool

    started = [Event(), Event()]
    release = Event()
    original = grouping.evaluate

    def held(pool: ReferencePool, body: bytes, prepared: PreparedSources) -> bytes:
        # A real leased worker is busy, rather than a fabricated busy response.
        with pool.borrow():
            position = 0 if not started[0].is_set() else 1
            started[position].set()
            assert release.wait(5)
        return original(pool, body, prepared)

    with TestClient(create_app(grouping_bundle(tmp_path))) as client:
        monkeypatch.setattr(grouping, "evaluate", held)
        with ThreadPoolExecutor(max_workers=2) as executor:
            try:
                first = executor.submit(
                    client.post, "/api/v1/group", json=grouping_body()
                )
                assert started[0].wait(3)
                second = executor.submit(
                    client.post, "/api/v1/group", json=grouping_body()
                )
                assert started[1].wait(3)
                assert (
                    client.post("/api/v1/group", json=grouping_body()).status_code
                    == 429
                )
                assert client.get("/readyz").status_code == 200
                search = client.post(
                    "/api/v1/search",
                    json={
                        "system": "icd10-pl",
                        "release_id": "icd10-synthetic-v1",
                        "query": "E11.0",
                    },
                )
                assert search.status_code == 200
            finally:
                release.set()
            assert first.result().status_code == second.result().status_code == 200
        monkeypatch.setattr(grouping, "evaluate", original)
        assert client.post("/api/v1/group", json=grouping_body()).status_code == 200


def test_slow_uploads_do_not_hold_evaluation_workers(tmp_path: Path) -> None:
    app = create_app(grouping_bundle(tmp_path))
    body = json.dumps(grouping_body()).encode()
    headers = {"Content-Type": "application/json"}

    async def scenario() -> list[int]:
        resume = asyncio.Event()

        async def trickle() -> AsyncIterator[bytes]:
            yield body[:1]
            _ = await resume.wait()
            yield body[1:]

        transport = httpx.ASGITransport(app=app)
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=transport, base_url="http://test") as client,
        ):
            slow = [
                asyncio.create_task(
                    client.post("/api/v1/group", content=trickle(), headers=headers)
                )
                for _ in range(2)
            ]
            await asyncio.sleep(0.2)
            fast = await client.post("/api/v1/group", content=body, headers=headers)
            resume.set()
            return [fast.status_code, *[(await task).status_code for task in slow]]

    assert asyncio.run(scenario()) == [200, 200, 200]


def test_evaluation_and_response_limits_return_no_partial_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from functools import partial

    from minos.grouper import pipeline as service
    from minos.grouper.budget import evaluation_budget
    from minos.http import grouping as transport

    with TestClient(create_app(grouping_bundle(tmp_path))) as client:
        with monkeypatch.context() as patch:
            patch.setattr(
                service, "evaluation_budget", partial(evaluation_budget, steps=0)
            )
            response = client.post("/api/v1/group", json=grouping_body())
            assert response.status_code == 422
            assert response.json()["error"]["code"] == "evaluation_limit"
            assert "groups" not in response.json()
        with monkeypatch.context() as patch:
            patch.setattr(
                service, "evaluation_budget", partial(evaluation_budget, seconds=0)
            )
            assert client.post("/api/v1/group", json=grouping_body()).status_code == 504
        with monkeypatch.context() as patch:
            patch.setattr(transport, "MAX_GROUPING_RESPONSE_BYTES", 1)
            assert client.post("/api/v1/group", json=grouping_body()).status_code == 422
        assert client.post("/api/v1/group", json=grouping_body()).status_code == 200
