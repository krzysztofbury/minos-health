"""Actual HTTP and stdin CLI boundaries, including input privacy and recovery."""

import json
import logging
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from typing import cast

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
        cast(dict[str, object], body["encounter"])["product_reporting"] = "complete"
        cast(dict[str, object], body["encounter"])["settlement_products"] = [
            {"code": "5.52.01.0001363"}
        ]
        assert (
            client.post("/api/v1/group", json=body).json()["status"]
            == "unsupported_product_report"
        )
        assert client.get("/healthz").status_code == 200


def test_grouping_admission_preserves_search_capacity_and_recovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from minos.http import grouping
    from minos.http.reference import ReferencePool

    started = [Event(), Event()]
    release = Event()
    original = grouping.evaluate

    def held(pool: ReferencePool, body: bytes) -> bytes:
        # A real leased worker is busy, rather than a fabricated busy response.
        with pool.borrow():
            position = 0 if not started[0].is_set() else 1
            started[position].set()
            assert release.wait(5)
        return original(pool, body)

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


def test_evaluation_and_response_limits_return_no_partial_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from functools import partial

    from minos.cli import grouping as adapter
    from minos.grouper.budget import evaluation_budget
    from minos.http import grouping as transport

    with TestClient(create_app(grouping_bundle(tmp_path))) as client:
        with monkeypatch.context() as patch:
            patch.setattr(
                adapter, "evaluation_budget", partial(evaluation_budget, steps=0)
            )
            response = client.post("/api/v1/group", json=grouping_body())
            assert response.status_code == 422
            assert response.json()["error"]["code"] == "evaluation_limit"
            assert "groups" not in response.json()
        with monkeypatch.context() as patch:
            patch.setattr(
                adapter, "evaluation_budget", partial(evaluation_budget, seconds=0)
            )
            assert client.post("/api/v1/group", json=grouping_body()).status_code == 504
        with monkeypatch.context() as patch:
            patch.setattr(transport, "MAX_GROUPING_RESPONSE_BYTES", 1)
            assert client.post("/api/v1/group", json=grouping_body()).status_code == 422
        assert client.post("/api/v1/group", json=grouping_body()).status_code == 200
