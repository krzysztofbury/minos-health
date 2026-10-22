import json
from pathlib import Path
from typing import cast

import httpx
import pytest

from minos.ingest.watchers.protocols import FailureCategory
from minos.ingest.watchers.uptime_kuma import (
    UptimeKumaClient,
    UptimeKumaError,
    UptimeKumaTargets,
)

_TOKEN = "AbCdEfGhIjKlMnOpQrStUvWxYz012345"


def _write_targets(path: Path, sources: dict[str, str]) -> UptimeKumaTargets:
    _ = path.write_text(json.dumps({"version": 1, "sources": sources}))
    return UptimeKumaTargets.load(path)


def test_uptime_kuma_sends_exact_up_and_down_queries_and_start_is_noop(
    tmp_path: Path,
) -> None:
    targets = _write_targets(tmp_path / "targets.json", {"rsk": _TOKEN})
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True})

    with UptimeKumaClient(
        targets,
        transport=httpx.MockTransport(handle),
    ) as client:
        client.start("rsk")
        client.success("rsk")
        client.failure("rsk", "source_drift")

    assert [request.method for request in requests] == ["GET", "GET"]
    assert [request.url.scheme for request in requests] == ["http", "http"]
    assert [request.url.host for request in requests] == [
        "uptime-kuma",
        "uptime-kuma",
    ]
    assert [request.url.port for request in requests] == [3001, 3001]
    assert [request.url.path for request in requests] == [
        f"/api/push/{_TOKEN}",
        f"/api/push/{_TOKEN}",
    ]
    assert [request.url.query for request in requests] == [
        b"status=up&msg=completed&ping=",
        b"status=down&msg=failed%3A+source_drift&ping=",
    ]


@pytest.mark.parametrize(
    "unknown",
    ["schema at https://source.invalid/x?q=1", "SOURCE_DRIFT", "source_drift ", ""],
)
def test_uptime_kuma_refuses_unknown_failure_category_before_any_request(
    tmp_path: Path,
    unknown: str,
) -> None:
    targets = _write_targets(tmp_path / "targets.json", {"rsk": _TOKEN})
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True})

    with UptimeKumaClient(
        targets,
        transport=httpx.MockTransport(handle),
    ) as client:
        with pytest.raises(UptimeKumaError, match="category is unsupported"):
            client.failure("rsk", cast(FailureCategory, unknown))

    assert requests == []


def test_optional_partial_uptime_kuma_targets_are_noop_for_missing_source(
    tmp_path: Path,
) -> None:
    targets = _write_targets(tmp_path / "targets.json", {"other": _TOKEN})
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True})

    with UptimeKumaClient(
        targets,
        transport=httpx.MockTransport(handle),
    ) as client:
        client.start("rsk")
        client.success("rsk")
        client.failure("rsk", "collection_failure")

    assert requests == []
    with pytest.raises(UptimeKumaError, match="required"):
        targets.require_sources(("rsk",))


@pytest.mark.parametrize(
    ("status_code", "content", "expected_message"),
    [
        (404, b'{"ok":false}', "rejected"),
        (200, b"not-json", "response is invalid"),
        (200, b'{"ok":1}', "response is invalid"),
        (200, b'{"ok":true,"msg":"extra"}', "response is invalid"),
    ],
)
def test_uptime_kuma_rejects_bad_responses_without_revealing_token_or_url(
    tmp_path: Path,
    status_code: int,
    content: bytes,
    expected_message: str,
) -> None:
    targets = _write_targets(tmp_path / "targets.json", {"rsk": _TOKEN})

    def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, content=content)

    with UptimeKumaClient(
        targets,
        transport=httpx.MockTransport(handle),
    ) as client:
        with pytest.raises(UptimeKumaError, match=expected_message) as captured:
            client.success("rsk")

    error_text = repr(captured.value)
    assert _TOKEN not in error_text
    assert "uptime-kuma:3001" not in error_text
    assert "api/push" not in error_text


def test_uptime_kuma_timeout_is_privacy_safe(tmp_path: Path) -> None:
    targets = _write_targets(tmp_path / "targets.json", {"rsk": _TOKEN})

    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout(f"secret request {request.url}", request=request)

    with UptimeKumaClient(
        targets,
        transport=httpx.MockTransport(timeout),
    ) as client:
        with pytest.raises(UptimeKumaError, match="request failed") as captured:
            client.failure("rsk", "collection_failure")

    assert _TOKEN not in repr(captured.value)
    assert "uptime-kuma" not in repr(captured.value)


def test_uptime_kuma_target_file_allows_empty_bootstrap_and_hides_tokens(
    tmp_path: Path,
) -> None:
    empty = _write_targets(tmp_path / "empty.json", {})
    targets = _write_targets(tmp_path / "targets.json", {"rsk": _TOKEN})

    assert empty.configured_sources() == ()
    assert targets.configured_sources() == ("rsk",)
    assert _TOKEN not in repr(targets)
    client = UptimeKumaClient(
        targets,
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json={"ok": True})
        ),
    )
    try:
        assert _TOKEN not in repr(client)
    finally:
        client.close()


@pytest.mark.parametrize(
    "payload",
    [
        {"version": 1, "sources": {"RSK": _TOKEN}},
        {"version": 1, "sources": {"rsk": "a" * 31}},
        {"version": 1, "sources": {"rsk": "a" * 31 + "/"}},
        {"version": 1, "sources": {}, "extra": True},
        {"version": True, "sources": {}},
        {"version": 1, "sources": {f"source-{index}": _TOKEN for index in range(129)}},
    ],
)
def test_uptime_kuma_target_file_rejects_noncanonical_schema(
    tmp_path: Path,
    payload: object,
) -> None:
    path = tmp_path / "targets.json"
    _ = path.write_text(json.dumps(payload))

    with pytest.raises(UptimeKumaError, match="target file is invalid") as captured:
        _ = UptimeKumaTargets.load(path)

    assert _TOKEN not in repr(captured.value)


def test_uptime_kuma_target_file_rejects_symlink(tmp_path: Path) -> None:
    target = tmp_path / "real.json"
    _ = target.write_text(json.dumps({"version": 1, "sources": {}}))
    link = tmp_path / "link.json"
    link.symlink_to(target)

    with pytest.raises(UptimeKumaError, match="could not be read"):
        _ = UptimeKumaTargets.load(link)


def test_uptime_kuma_target_file_rejects_duplicate_keys(tmp_path: Path) -> None:
    path = tmp_path / "targets.json"
    _ = path.write_text(
        f'{{"version":1,"sources":{{"rsk":"{_TOKEN}","rsk":"{_TOKEN}"}}}}'
    )

    with pytest.raises(UptimeKumaError, match="target file is invalid"):
        _ = UptimeKumaTargets.load(path)


@pytest.mark.parametrize(
    "message,sent",
    [
        ("idle", True),
        ("failed: fetch", True),
        ("would_activate icd9-czs-5-90-gen-94-0a1b2c3d4e5f", True),
        (
            "rejected icd9-pl history/retroactive_amendment_exceeds_bound; "
            + "would_activate icd10-rsk-014-0a1b2c3d4e5f",
            True,
        ),
        ("rejected icd9-pl history/x at /var/lib/minos/work", False),
        ("would_activate https://source.invalid/x?q=1", False),
        ("failed: fetch\nToken: secret", False),
        ("manual jgp 44042", True),
        ("rejected icd9-pl history/x; manual jgp 44042; manual jgp 43868", True),
        ("manual jgp 044042", False),
        ("manual icd11 002; manual jgp 44042", True),
        ("manual icd11 https://rsk3.ezdrowie.gov.pl/x", False),
        ("manual jgp Zalacznik1a.xlsx", False),
        ("rejected jgp history/x", False),
        ("", False),
    ],
)
def test_data_update_report_sends_only_fixed_verdict_shapes(
    tmp_path: Path, message: str, sent: bool
) -> None:
    targets = _write_targets(tmp_path / "targets.json", {"data-update": _TOKEN})
    queries: list[bytes] = []

    def handle(request: httpx.Request) -> httpx.Response:
        queries.append(request.url.query)
        return httpx.Response(200, json={"ok": True})

    with UptimeKumaClient(targets, transport=httpx.MockTransport(handle)) as client:
        if sent:
            client.report("data-update", up=False, message=message)
        else:
            with pytest.raises(UptimeKumaError, match="report message is unsupported"):
                client.report("data-update", up=True, message=message)

    expected = httpx.QueryParams({"status": "down", "msg": message, "ping": ""})
    assert queries == ([str(expected).encode()] if sent else [])
