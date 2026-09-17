import hashlib
import json
from collections.abc import Iterator
from io import BytesIO
from typing import override
from uuid import UUID

import httpx
import pytest
from pydantic import ValidationError

from minos.ingest.watchers.config import RskSettings
from minos.ingest.watchers.models import HttpObservation
from minos.ingest.watchers.rsk.client import (
    RskDeadlineExceededError,
    RskProtocolError,
    RskSizeMismatchError,
    RskSourceDriftError,
    download_rsk_file,
    get_default_rsk_role_catalog,
    get_rsk_catalog_basic_info,
    parse_rsk_catalog_basic_info,
    parse_rsk_role_catalog,
)

_FILE_UUID = UUID("bf7b563c-2823-41db-a41d-94f7f5ac8418")
_XML_PREFIX_START = b'<?xml version="1.0"?><root>'
_XML_PREFIX = _XML_PREFIX_START + (b" " * (512 - len(_XML_PREFIX_START)))


class _InterruptedStream(httpx.SyncByteStream):
    @override
    def __iter__(self) -> Iterator[bytes]:
        yield _XML_PREFIX
        raise httpx.ReadError("simulated interrupted response")


class _CancelledStream(httpx.SyncByteStream):
    @override
    def __iter__(self) -> Iterator[bytes]:
        yield _XML_PREFIX
        raise KeyboardInterrupt


class _ChunkedStream(httpx.SyncByteStream):
    def __init__(self, *chunks: bytes) -> None:
        self._chunks: tuple[bytes, ...] = chunks

    @override
    def __iter__(self) -> Iterator[bytes]:
        yield from self._chunks


class _SlowStream(httpx.SyncByteStream):
    def __init__(self, clock: list[float]) -> None:
        self._clock: list[float] = clock

    @override
    def __iter__(self) -> Iterator[bytes]:
        yield b"<?xml "
        self._clock[0] = 2.0
        yield b'version="1.0"?><root/>'


def _settings(**overrides: object) -> RskSettings:
    values: dict[str, object] = {
        "metadata_max_bytes": 1024,
        "file_max_bytes": 1024,
    }
    values.update(overrides)
    return RskSettings.model_validate(values)


def _client(handler: httpx.MockTransport) -> httpx.Client:
    return httpx.Client(transport=handler, follow_redirects=False)


def _streaming_response(
    body: bytes, *, content_type: str, etag: str | None = None
) -> httpx.Response:
    headers = {
        "Content-Type": content_type,
        "Content-Length": str(len(body)),
    }
    if etag is not None:
        headers["ETag"] = etag
    return httpx.Response(200, stream=httpx.ByteStream(body), headers=headers)


def _observation(body: bytes) -> HttpObservation:
    return HttpObservation(
        source_url="https://rsk.example",
        body=body,
        headers=(("content-type", "application/json"),),
    )


def _role_catalog_body(resource_count: int = 1) -> bytes:
    resource: dict[str, object] = {
        "ordinalNumber": 1,
        "hcd": "00CD10",
        "name": "ICD-10 PL",
        "description": None,
        "version": "013",
        "organizationName": "CeZ",
        "creationDate": "2026-08-05T08:00:00",
        "resourceType": "SLOWNIK",
    }
    return json.dumps(
        [resource] * resource_count,
        separators=(",", ":"),
    ).encode()


def _basic_info_body(
    hcd: str = "00CD10", files: list[dict[str, object]] | None = None
) -> bytes:
    return json.dumps(
        {
            "hcd": hcd,
            "name": "ICD-10 PL",
            "shortName": "ICD-10",
            "description": None,
            "validDateFrom": "2026-01-01T00:00:00",
            "validDateTo": None,
            "version": "013",
            "publisher": None,
            "files": files or [],
            "specifyDownload": False,
            "getAsRest": True,
            "resourceType": "SLOWNIK",
            "language": "pl",
            "fhirOrRestApiVersion": None,
            "educationLinks": [],
        },
        separators=(",", ":"),
    ).encode()


def test_role_catalog_preserves_body_headers_and_parses_models() -> None:
    body = _role_catalog_body()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["accept-encoding"] == "identity"
        return _streaming_response(
            body, content_type="application/json", etag='"catalog"'
        )

    with _client(httpx.MockTransport(handler)) as client:
        observation = get_default_rsk_role_catalog(client, _settings())

    assert observation.body == body
    assert parse_rsk_role_catalog(observation)[0].hcd == "00CD10"
    assert observation.header_map()["etag"] == '"catalog"'


def test_basic_info_preserves_body_and_validates_identity() -> None:
    resource = "00CD10"
    body = _basic_info_body(resource)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.raw_path.endswith(b"00CD10")
        return _streaming_response(body, content_type="application/json")

    with _client(httpx.MockTransport(handler)) as client:
        observation = get_rsk_catalog_basic_info(client, _settings(), resource)

    assert observation.body == body
    assert parse_rsk_catalog_basic_info(observation, resource).hcd == resource


def test_role_catalog_rejects_empty_and_duplicate_hcds() -> None:
    with pytest.raises(RskSourceDriftError, match="empty"):
        _ = parse_rsk_role_catalog(_observation(b"[]"))

    with pytest.raises(RskSourceDriftError, match="duplicate HCD"):
        _ = parse_rsk_role_catalog(_observation(_role_catalog_body(resource_count=2)))


def test_basic_info_rejects_identity_mismatch_and_duplicate_files() -> None:
    with pytest.raises(RskSourceDriftError, match="does not match"):
        _ = parse_rsk_catalog_basic_info(
            _observation(_basic_info_body("00CD11")), "00CD10"
        )

    duplicate_file: dict[str, object] = {"uuid": str(_FILE_UUID)}
    body = _basic_info_body(files=[duplicate_file, duplicate_file])
    with pytest.raises(RskSourceDriftError, match="duplicate file UUID"):
        _ = parse_rsk_catalog_basic_info(_observation(body), "00CD10")


def test_basic_info_rejects_invalid_hcd_before_request() -> None:
    requested = False

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal requested
        requested = True
        raise AssertionError("request should not be sent")

    with _client(httpx.MockTransport(handler)) as client:
        with pytest.raises(RskProtocolError, match="six uppercase"):
            _ = get_rsk_catalog_basic_info(client, _settings(), "../CD10")

    assert requested is False


def test_settings_reject_source_redirects() -> None:
    with pytest.raises(ValidationError, match="official RSK host"):
        _ = RskSettings.model_validate(
            {
                "role_catalog_url": "https://attacker.example/api/rsk/resource/role/UZYNLG"
            }
        )


def test_metadata_rejects_html_content_type() -> None:
    body = b"<html></html>"

    def handler(_: httpx.Request) -> httpx.Response:
        return _streaming_response(body, content_type="text/html")

    with _client(httpx.MockTransport(handler)) as client:
        observation = get_default_rsk_role_catalog(client, _settings())

    assert observation.body == body
    with pytest.raises(RskProtocolError, match="content type"):
        _ = parse_rsk_role_catalog(observation)


def test_metadata_preserves_body_before_rejecting_content_encoding() -> None:
    body = b"compressed source bytes"

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            stream=httpx.ByteStream(body),
            headers={
                "Content-Type": "application/json",
                "Content-Encoding": "gzip",
                "Content-Length": str(len(body)),
            },
        )

    with _client(httpx.MockTransport(handler)) as client:
        observation = get_default_rsk_role_catalog(client, _settings())

    assert observation.body == body
    with pytest.raises(RskProtocolError, match="identity encoding"):
        _ = parse_rsk_role_catalog(observation)


def test_metadata_rejects_body_over_configured_bound() -> None:
    body = _role_catalog_body()

    def handler(_: httpx.Request) -> httpx.Response:
        return _streaming_response(body, content_type="application/json")

    settings = _settings(metadata_max_bytes=len(body) - 1)
    with _client(httpx.MockTransport(handler)) as client:
        with pytest.raises(RskSizeMismatchError, match="size limit"):
            _ = get_default_rsk_role_catalog(client, settings)


def test_metadata_schema_failure_is_not_retried() -> None:
    request_count = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return _streaming_response(b"{}", content_type="application/json")

    with _client(httpx.MockTransport(handler)) as client:
        observation = get_default_rsk_role_catalog(client, _settings())

    with pytest.raises(ValidationError):
        _ = parse_rsk_role_catalog(observation)
    assert observation.body == b"{}"
    assert request_count == 1


def test_metadata_retries_transient_status_and_honors_retry_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request_count = 0
    delays: list[float] = []

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        if request_count == 1:
            return httpx.Response(503, headers={"Retry-After": "2"})
        return _streaming_response(
            _role_catalog_body(), content_type="application/json"
        )

    monkeypatch.setattr(
        "minos.ingest.watchers.rsk.client._sleep",
        delays.append,
    )
    with _client(httpx.MockTransport(handler)) as client:
        observation = get_default_rsk_role_catalog(client, _settings())

    assert parse_rsk_role_catalog(observation)[0].hcd == "00CD10"
    assert request_count == 2
    assert delays == [2.0]


def test_metadata_ignores_malformed_retry_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request_count = 0
    delays: list[float] = []

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        if request_count == 1:
            return httpx.Response(503, headers={"Retry-After": "NaN"})
        return _streaming_response(
            _role_catalog_body(), content_type="application/json"
        )

    def fixed_jitter(_: float) -> float:
        return 0.25

    monkeypatch.setattr(
        "minos.ingest.watchers.rsk.client.backoff.full_jitter", fixed_jitter
    )
    monkeypatch.setattr("minos.ingest.watchers.rsk.client._sleep", delays.append)
    with _client(httpx.MockTransport(handler)) as client:
        _ = get_default_rsk_role_catalog(client, _settings())

    assert delays == [0.25]


def test_metadata_caps_retry_after_at_configured_maximum(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request_count = 0
    delays: list[float] = []

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        if request_count == 1:
            return httpx.Response(429, headers={"Retry-After": "999"})
        return _streaming_response(
            _role_catalog_body(), content_type="application/json"
        )

    monkeypatch.setattr("minos.ingest.watchers.rsk.client._sleep", delays.append)
    with _client(httpx.MockTransport(handler)) as client:
        _ = get_default_rsk_role_catalog(client, _settings(retry_max_seconds=1.0))

    assert delays == [1.0]


def test_metadata_does_not_retry_non_transient_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request_count = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(404)

    def fail_sleep(_: float) -> None:
        pytest.fail("non-transient response was retried")

    monkeypatch.setattr("minos.ingest.watchers.rsk.client._sleep", fail_sleep)
    with _client(httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.HTTPStatusError):
            _ = get_default_rsk_role_catalog(client, _settings())

    assert request_count == 1


def test_download_streams_bytes_and_returns_digest() -> None:
    body = b'<?xml version="1.0"?><root/>'
    destination = BytesIO()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith(str(_FILE_UUID))
        return _streaming_response(body, content_type="application/xml", etag='"file"')

    with _client(httpx.MockTransport(handler)) as client:
        result = download_rsk_file(
            client,
            _settings(),
            _FILE_UUID,
            expected_size_bytes=len(body),
            expected_extension="xml",
            destination=destination,
        )

    assert destination.getvalue() == body
    assert result.size_bytes == len(body)
    assert result.sha256 == hashlib.sha256(body).hexdigest()
    assert dict(result.headers)["etag"] == '"file"'


def test_download_rejects_content_length_that_differs_from_metadata() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"abc",
            headers={"Content-Type": "application/octet-stream"},
        )

    with _client(httpx.MockTransport(handler)) as client:
        with pytest.raises(RskSizeMismatchError, match="Content-Length"):
            _ = download_rsk_file(
                client,
                _settings(),
                _FILE_UUID,
                expected_size_bytes=4,
                expected_extension="xml",
                destination=BytesIO(),
            )


def test_download_rejects_html_before_writing() -> None:
    destination = BytesIO()
    body = b"<!doctype html><title>error</title>"

    def handler(_: httpx.Request) -> httpx.Response:
        return _streaming_response(body, content_type="application/octet-stream")

    with _client(httpx.MockTransport(handler)) as client:
        with pytest.raises(RskProtocolError, match="HTML"):
            _ = download_rsk_file(
                client,
                _settings(),
                _FILE_UUID,
                expected_size_bytes=len(body),
                expected_extension="xml",
                destination=destination,
            )

    assert destination.getvalue() == b""


def test_download_rejects_split_html_prefix_before_writing() -> None:
    destination = BytesIO()
    body = b" \n<!doctype html><title>error</title>"

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            stream=_ChunkedStream(b" \n", b"<!doctype html><title>error</title>"),
            headers={
                "Content-Type": "application/octet-stream",
                "Content-Length": str(len(body)),
            },
        )

    with _client(httpx.MockTransport(handler)) as client:
        with pytest.raises(RskProtocolError, match="HTML"):
            _ = download_rsk_file(
                client,
                _settings(),
                _FILE_UUID,
                expected_size_bytes=len(body),
                expected_extension="xml",
                destination=destination,
            )

    assert destination.getvalue() == b""


def test_download_rejects_html_root_after_xml_declaration() -> None:
    destination = BytesIO()
    body = b'<?xml version="1.0"?><html><title>error</title></html>'

    def handler(_: httpx.Request) -> httpx.Response:
        return _streaming_response(body, content_type="application/xml")

    with _client(httpx.MockTransport(handler)) as client:
        with pytest.raises(RskProtocolError, match="HTML"):
            _ = download_rsk_file(
                client,
                _settings(),
                _FILE_UUID,
                expected_size_bytes=len(body),
                expected_extension="xml",
                destination=destination,
            )

    assert destination.getvalue() == b""


def test_download_rejects_non_xml_bytes_and_extension() -> None:
    body = b"same-sized arbitrary bytes"
    request_count = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return _streaming_response(body, content_type="application/octet-stream")

    with _client(httpx.MockTransport(handler)) as client:
        with pytest.raises(RskProtocolError, match="XML signature"):
            _ = download_rsk_file(
                client,
                _settings(),
                _FILE_UUID,
                expected_size_bytes=len(body),
                expected_extension="xml",
                destination=BytesIO(),
            )
        with pytest.raises(RskProtocolError, match="not allowlisted"):
            _ = download_rsk_file(
                client,
                _settings(),
                _FILE_UUID,
                expected_size_bytes=len(body),
                expected_extension="zip",
                destination=BytesIO(),
            )

    assert request_count == 1


def test_download_rejects_expected_size_outside_bound_without_request() -> None:
    requested = False

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal requested
        requested = True
        raise AssertionError("request should not be sent")

    with _client(httpx.MockTransport(handler)) as client:
        with pytest.raises(RskSizeMismatchError, match="configured bound"):
            _ = download_rsk_file(
                client,
                _settings(file_max_bytes=3),
                _FILE_UUID,
                expected_size_bytes=4,
                expected_extension="xml",
                destination=BytesIO(),
            )

    assert requested is False


def test_download_rolls_back_partial_bytes_before_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = _XML_PREFIX + b"<complete-response/>"
    request_count = 0
    destination = BytesIO()

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        if request_count == 1:
            return httpx.Response(
                200,
                stream=_InterruptedStream(),
                headers={"Content-Type": "application/octet-stream"},
            )
        return _streaming_response(body, content_type="application/octet-stream")

    def no_sleep(_: float) -> None:
        pass

    monkeypatch.setattr("minos.ingest.watchers.rsk.client._sleep", no_sleep)
    with _client(httpx.MockTransport(handler)) as client:
        result = download_rsk_file(
            client,
            _settings(),
            _FILE_UUID,
            expected_size_bytes=len(body),
            expected_extension="xml",
            destination=destination,
        )

    assert request_count == 2
    assert result.size_bytes == len(body)
    assert destination.getvalue() == body


def test_download_clears_partial_bytes_after_final_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = BytesIO()

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            stream=_InterruptedStream(),
            headers={"Content-Type": "application/octet-stream"},
        )

    def no_sleep(_: float) -> None:
        pass

    monkeypatch.setattr("minos.ingest.watchers.rsk.client._sleep", no_sleep)
    with _client(httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.ReadError):
            _ = download_rsk_file(
                client,
                _settings(retry_max_attempts=2),
                _FILE_UUID,
                expected_size_bytes=600,
                expected_extension="xml",
                destination=destination,
            )

    assert destination.getvalue() == b""


def test_download_clears_partial_bytes_after_cancellation() -> None:
    destination = BytesIO()

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            stream=_CancelledStream(),
            headers={"Content-Type": "application/octet-stream"},
        )

    with _client(httpx.MockTransport(handler)) as client:
        with pytest.raises(KeyboardInterrupt):
            _ = download_rsk_file(
                client,
                _settings(),
                _FILE_UUID,
                expected_size_bytes=600,
                expected_extension="xml",
                destination=destination,
            )

    assert destination.getvalue() == b""


def test_download_enforces_overall_operation_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = BytesIO()
    clock = [0.0]

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            stream=_SlowStream(clock),
            headers={"Content-Type": "application/octet-stream"},
        )

    def monotonic() -> float:
        return clock[0]

    monkeypatch.setattr("minos.ingest.watchers.rsk.client._monotonic", monotonic)
    with _client(httpx.MockTransport(handler)) as client:
        with pytest.raises(RskDeadlineExceededError):
            _ = download_rsk_file(
                client,
                _settings(operation_deadline_seconds=1.0),
                _FILE_UUID,
                expected_size_bytes=600,
                expected_extension="xml",
                destination=destination,
            )

    assert destination.getvalue() == b""
