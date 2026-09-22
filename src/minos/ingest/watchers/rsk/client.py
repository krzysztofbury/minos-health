import hashlib
import re
from collections.abc import Buffer, Callable, Generator, Iterator
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from math import isfinite
from time import monotonic as _monotonic
from time import sleep as _sleep
from typing import Annotated, Protocol, cast, final, override
from urllib.parse import quote
from uuid import UUID

import backoff
import httpx
from loguru import logger
from pydantic import Field, TypeAdapter

from minos.ingest.watchers.protocols import SourceDriftWatcherError

from ..config import RskSettings
from ..models import DownloadResult, HttpObservation
from .models import (
    RSK_ARCHIVED_HEADER_VALUE_LIMITS,
    RSK_XML_EXTENSION,
    RskCatalogMetadata,
    RskRoleUriModel,
    is_valid_rsk_archived_header,
)

_ARCHIVED_HEADER_NAMES = tuple(RSK_ARCHIVED_HEADER_VALUE_LIMITS)
_RETRYABLE_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})
_ROLE_CATALOG_ADAPTER: TypeAdapter[list[RskRoleUriModel]] = TypeAdapter(
    Annotated[list[RskRoleUriModel], Field(max_length=128)]
)
_HCD_PATTERN = re.compile(r"[0-9A-Z]{6}")
_XML_ROOT_PATTERN = re.compile(rb"<([a-z_][a-z0-9_.:-]*)")


class RskProtocolError(SourceDriftWatcherError, ValueError):
    pass


class RskSizeMismatchError(RskProtocolError):
    pass


class RskSourceDriftError(RskProtocolError):
    pass


class RskDeadlineExceededError(TimeoutError):
    pass


class RskDownloadDestination(Protocol):
    def tell(self) -> int: ...

    def seek(self, offset: int, whence: int = 0, /) -> int: ...

    def truncate(self, size: int | None = None, /) -> int: ...

    def write(self, buffer: Buffer, /) -> int: ...


@final
class _AnonymousRskTransport(httpx.BaseTransport):
    def __init__(self, transport: httpx.BaseTransport) -> None:
        self._transport = transport

    @override
    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if "cookie" in request.headers or "authorization" in request.headers:
            raise RskProtocolError("RSK anonymous request contains forbidden state")
        response = self._transport.handle_request(request)
        if "set-cookie" in response.headers:
            response.close()
            raise RskProtocolError("RSK anonymous response attempted to set a cookie")
        return response

    @override
    def close(self) -> None:
        self._transport.close()


def create_rsk_client(
    settings: RskSettings,
    *,
    transport: httpx.BaseTransport | None = None,
) -> httpx.Client:
    anonymous_transport = _AnonymousRskTransport(
        transport
        if transport is not None
        else httpx.HTTPTransport(retries=0, trust_env=False)
    )
    return httpx.Client(
        auth=None,
        cookies={},
        headers={"Accept-Encoding": "identity", "User-Agent": settings.user_agent},
        timeout=httpx.Timeout(
            settings.timeout_seconds,
            connect=settings.connect_timeout_seconds,
        ),
        follow_redirects=False,
        trust_env=False,
        transport=anonymous_transport,
    )


def get_default_rsk_role_catalog(
    client: httpx.Client, settings: RskSettings
) -> HttpObservation:
    source_url = str(settings.role_catalog_url)
    body, headers = _get_json_body(
        client, settings, source_url, endpoint="role_catalog"
    )
    logger.bind(
        event="rsk_metadata_received",
        endpoint="role_catalog",
        size_bytes=len(body),
    ).info("RSK metadata received")
    return HttpObservation(
        source_url=source_url,
        body=body,
        headers=headers,
    )


def get_rsk_catalog_basic_info(
    client: httpx.Client, settings: RskSettings, resource: str
) -> HttpObservation:
    _validate_hcd(resource)
    source_url = f"{settings.basic_info_base_url}{quote(resource, safe='')}"
    body, headers = _get_json_body(client, settings, source_url, endpoint="basic_info")
    logger.bind(
        event="rsk_metadata_received",
        endpoint="basic_info",
        size_bytes=len(body),
    ).info("RSK metadata received")
    return HttpObservation(
        source_url=source_url,
        body=body,
        headers=headers,
    )


def parse_rsk_role_catalog(observation: HttpObservation) -> list[RskRoleUriModel]:
    _validate_json_observation(observation)
    catalog = _ROLE_CATALOG_ADAPTER.validate_json(observation.body)
    if not catalog:
        raise RskSourceDriftError("RSK role catalog is empty")
    hcds = [resource.hcd for resource in catalog]
    if len(set(hcds)) != len(hcds):
        raise RskSourceDriftError("RSK role catalog contains duplicate HCD values")
    return catalog


def parse_rsk_catalog_basic_info(
    observation: HttpObservation, expected_hcd: str
) -> RskCatalogMetadata:
    _validate_hcd(expected_hcd)
    _validate_json_observation(observation)
    metadata = RskCatalogMetadata.model_validate_json(observation.body)
    if metadata.hcd != expected_hcd:
        raise RskSourceDriftError("RSK basic-info HCD does not match the request")
    file_uuids = [file.uuid for file in metadata.files]
    if len(set(file_uuids)) != len(file_uuids):
        raise RskSourceDriftError("RSK basic-info contains duplicate file UUIDs")
    return metadata


def download_rsk_file(
    client: httpx.Client,
    settings: RskSettings,
    file_uuid: UUID,
    expected_size_bytes: int,
    expected_extension: str,
    destination: RskDownloadDestination,
) -> DownloadResult:
    if expected_size_bytes < 0 or expected_size_bytes > settings.file_max_bytes:
        raise RskSizeMismatchError(
            f"RSK expected file size is outside the configured bound: {expected_size_bytes} bytes"
        )
    if expected_extension != RSK_XML_EXTENSION:
        raise RskProtocolError("RSK file extension is not allowlisted")

    _require_empty_seekable_destination(destination)
    source_url = f"{settings.file_download_base_url}{file_uuid}"
    try:
        result = _with_retries(
            lambda deadline: _download_once(
                client,
                settings,
                source_url,
                expected_size_bytes,
                expected_extension,
                destination,
                deadline,
            ),
            settings,
            endpoint="file",
            before_retry=lambda: _reset_destination(destination),
        )
    except BaseException as transfer_error:
        try:
            _reset_destination(destination)
        except Exception as cleanup_error:
            transfer_error.add_note(
                f"RSK destination rollback failed: {type(cleanup_error).__name__}"
            )
        raise

    logger.bind(
        event="rsk_file_received",
        endpoint="file",
        size_bytes=result.size_bytes,
        sha256=result.sha256,
    ).info("RSK file received")
    return result


def _download_once(
    client: httpx.Client,
    settings: RskSettings,
    source_url: str,
    expected_size_bytes: int,
    expected_extension: str,
    destination: RskDownloadDestination,
    deadline: float,
) -> DownloadResult:
    sha256 = hashlib.sha256()
    received_bytes = 0

    with client.stream(
        "GET",
        source_url,
        headers={"Accept-Encoding": "identity"},
        timeout=_remaining_timeout(settings, deadline),
    ) as response:
        _ = response.raise_for_status()
        _require_ok_status(response)
        if response.headers.get("content-range") is not None:
            raise RskProtocolError("RSK returned an unexpected partial response")
        _validate_identity_encoding(response)
        _reject_html_response(response)
        declared_size_bytes = _content_length(response.headers)
        if (
            declared_size_bytes is not None
            and declared_size_bytes != expected_size_bytes
        ):
            raise RskSizeMismatchError(
                f"RSK Content-Length does not match metadata size: {declared_size_bytes} != {expected_size_bytes}"
            )

        for chunk in _validated_file_chunks(
            _deadline_chunks(response.iter_raw(), deadline), expected_extension
        ):
            _check_deadline(deadline)
            next_size_bytes = received_bytes + len(chunk)
            if next_size_bytes > expected_size_bytes:
                raise RskSizeMismatchError("RSK download exceeded metadata size")
            written_bytes = destination.write(chunk)
            if written_bytes != len(chunk):
                raise OSError("RSK destination accepted a partial write")
            sha256.update(chunk)
            received_bytes = next_size_bytes

        headers = _selected_headers(response.headers)

    if received_bytes != expected_size_bytes:
        raise RskSizeMismatchError(
            f"RSK download size does not match metadata: {received_bytes} != {expected_size_bytes}"
        )

    return DownloadResult(
        source_url=source_url,
        sha256=sha256.hexdigest(),
        size_bytes=received_bytes,
        headers=headers,
    )


def _get_json_body(
    client: httpx.Client,
    settings: RskSettings,
    source_url: str,
    *,
    endpoint: str,
) -> tuple[bytes, tuple[tuple[str, str], ...]]:
    return _with_retries(
        lambda deadline: _get_json_body_once(
            client,
            settings,
            source_url,
            settings.metadata_max_bytes,
            deadline,
        ),
        settings,
        endpoint=endpoint,
    )


def _get_json_body_once(
    client: httpx.Client,
    settings: RskSettings,
    source_url: str,
    max_bytes: int,
    deadline: float,
) -> tuple[bytes, tuple[tuple[str, str], ...]]:
    body = bytearray()
    with client.stream(
        "GET",
        source_url,
        headers={"Accept-Encoding": "identity"},
        timeout=_remaining_timeout(settings, deadline),
    ) as response:
        _ = response.raise_for_status()
        _require_ok_status(response)
        declared_size_bytes = _content_length(response.headers)
        if declared_size_bytes is not None and declared_size_bytes > max_bytes:
            raise RskSizeMismatchError("RSK metadata exceeds configured size limit")

        for chunk in _deadline_chunks(response.iter_raw(), deadline):
            if len(body) + len(chunk) > max_bytes:
                raise RskSizeMismatchError("RSK metadata exceeds configured size limit")
            body.extend(chunk)

        headers = _selected_headers(response.headers)

    if declared_size_bytes is not None and len(body) != declared_size_bytes:
        raise RskSizeMismatchError("RSK metadata Content-Length does not match body")
    return bytes(body), headers


def _with_retries[ResultT](
    operation: Callable[[float], ResultT],
    settings: RskSettings,
    *,
    endpoint: str,
    before_retry: Callable[[], None] | None = None,
) -> ResultT:
    deadline = _monotonic() + settings.operation_deadline_seconds
    waits = cast(
        Generator[float | None],
        backoff.expo(
            factor=settings.retry_base_seconds,
            base=2,
            max_value=settings.retry_max_seconds,
        ),
    )
    _ = next(waits)
    jitter = cast(Callable[[float], float], backoff.full_jitter)

    for attempt in range(1, settings.retry_max_attempts + 1):
        try:
            _check_deadline(deadline)
            return operation(deadline)
        except (httpx.TransportError, httpx.HTTPStatusError) as error:
            if _monotonic() >= deadline:
                raise RskDeadlineExceededError(
                    "RSK operation exceeded its deadline"
                ) from error
            retryable = _is_retryable(error)
            if not retryable or attempt == settings.retry_max_attempts:
                raise
            if before_retry is not None:
                before_retry()
            backoff_seconds = next(waits)
            if backoff_seconds is None:
                raise AssertionError(
                    "backoff schedule yielded an unexpected sentinel"
                ) from error
            delay_seconds = _retry_delay_seconds(
                error,
                jitter(backoff_seconds),
                settings.retry_max_seconds,
            )
            remaining_seconds = deadline - _monotonic()
            if delay_seconds >= remaining_seconds:
                raise RskDeadlineExceededError(
                    "RSK operation deadline would be exceeded before retry"
                ) from error
            logger.bind(
                event="rsk_request_retry",
                endpoint=endpoint,
                attempt=attempt,
                max_attempts=settings.retry_max_attempts,
                delay_seconds=round(delay_seconds, 3),
                error_category=type(error).__name__,
                status_code=_status_code(error),
            ).warning("Retrying transient RSK request")
            _sleep(delay_seconds)

    raise AssertionError("retry loop exhausted without returning or raising")


def _is_retryable(error: httpx.TransportError | httpx.HTTPStatusError) -> bool:
    return isinstance(error, httpx.TransportError) or (
        error.response.status_code in _RETRYABLE_STATUS_CODES
    )


def _status_code(error: httpx.TransportError | httpx.HTTPStatusError) -> int | None:
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code
    return None


def _retry_delay_seconds(
    error: httpx.TransportError | httpx.HTTPStatusError,
    jittered_backoff_seconds: float,
    max_seconds: float,
) -> float:
    if not isinstance(error, httpx.HTTPStatusError):
        return jittered_backoff_seconds
    retry_after = cast(str | None, error.response.headers.get("retry-after"))
    if retry_after is None:
        return jittered_backoff_seconds
    retry_after = retry_after.strip()
    if retry_after.isdigit():
        requested_seconds = float(retry_after)
    else:
        try:
            retry_at = parsedate_to_datetime(retry_after)
        except TypeError, ValueError, OverflowError:
            return jittered_backoff_seconds
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
        requested_seconds = (retry_at - datetime.now(UTC)).total_seconds()
    if not isfinite(requested_seconds):
        return jittered_backoff_seconds
    return min(max(requested_seconds, jittered_backoff_seconds, 0.0), max_seconds)


def _require_empty_seekable_destination(destination: RskDownloadDestination) -> None:
    try:
        start_position = destination.tell()
        tracked_size = getattr(destination, "size_bytes", None)
        if isinstance(tracked_size, int) and not isinstance(tracked_size, bool):
            end_position = tracked_size
        else:
            _ = destination.seek(0, 2)
            end_position = destination.tell()
            _ = destination.seek(start_position)
    except (OSError, ValueError) as error:
        raise ValueError("RSK destination must be seekable") from error
    if start_position != 0 or end_position != 0:
        raise ValueError("RSK destination must be empty and positioned at zero")


def _reset_destination(destination: RskDownloadDestination) -> None:
    _ = destination.seek(0)
    _ = destination.truncate(0)


def _check_deadline(deadline: float) -> None:
    if _monotonic() >= deadline:
        raise RskDeadlineExceededError("RSK operation exceeded its deadline")


def _remaining_timeout(settings: RskSettings, deadline: float) -> httpx.Timeout:
    remaining_seconds = deadline - _monotonic()
    if remaining_seconds <= 0:
        raise RskDeadlineExceededError("RSK operation exceeded its deadline")
    read_seconds = min(settings.timeout_seconds, remaining_seconds)
    connect_seconds = min(settings.connect_timeout_seconds, remaining_seconds)
    return httpx.Timeout(
        connect=connect_seconds,
        read=read_seconds,
        write=read_seconds,
        pool=read_seconds,
    )


def _validate_json_observation(observation: HttpObservation) -> None:
    headers = observation.header_map()
    content_encoding = headers.get("content-encoding", "identity").lower()
    if content_encoding not in {"", "identity"}:
        raise RskProtocolError(
            f"RSK ignored identity encoding request: {content_encoding!r}"
        )
    content_type = headers.get("content-type", "")
    media_type = content_type.partition(";")[0].lower()
    if media_type != "application/json" and not media_type.endswith("+json"):
        raise RskProtocolError(f"RSK returned unexpected content type: {media_type!r}")
    if _looks_like_html(observation.body):
        raise RskProtocolError("RSK returned HTML instead of JSON")


def _validate_hcd(hcd: str) -> None:
    if _HCD_PATTERN.fullmatch(hcd) is None:
        raise RskProtocolError("RSK HCD must be six uppercase alphanumeric characters")


def _validate_identity_encoding(response: httpx.Response) -> None:
    content_encoding = cast(
        str, response.headers.get("content-encoding", "identity")
    ).lower()
    if content_encoding not in {"", "identity"}:
        raise RskProtocolError(
            f"RSK ignored identity encoding request: {content_encoding!r}"
        )


def _require_ok_status(response: httpx.Response) -> None:
    if response.status_code != 200:
        raise RskProtocolError("RSK returned an unexpected successful status")


def _reject_html_response(response: httpx.Response) -> None:
    content_type = cast(str, response.headers.get("content-type", ""))
    media_type = content_type.partition(";")[0].lower()
    if media_type in {"text/html", "application/xhtml+xml"}:
        raise RskProtocolError("RSK returned HTML instead of a file")


def _deadline_chunks(chunks: Iterator[bytes], deadline: float) -> Iterator[bytes]:
    while True:
        _check_deadline(deadline)
        try:
            chunk = next(chunks)
        except StopIteration:
            return
        _check_deadline(deadline)
        yield chunk


def _validated_file_chunks(
    chunks: Iterator[bytes], expected_extension: str
) -> Iterator[bytes]:
    prefix = bytearray()
    for chunk in chunks:
        remaining_prefix_bytes = 512 - len(prefix)
        prefix.extend(chunk[:remaining_prefix_bytes])
        remainder = chunk[remaining_prefix_bytes:]
        if len(prefix) == 512:
            _validate_file_prefix(prefix, expected_extension)
            yield bytes(prefix)
            if remainder:
                yield remainder
            yield from chunks
            return
    _validate_file_prefix(prefix, expected_extension)
    if prefix:
        yield bytes(prefix)


def _validate_file_prefix(content: bytes | bytearray, expected_extension: str) -> None:
    if expected_extension != RSK_XML_EXTENSION:
        raise RskProtocolError("RSK file extension is not allowlisted")
    prefix = bytes(content).removeprefix(b"\xef\xbb\xbf").lstrip().lower()
    if prefix.startswith(b"<!doctype html") or prefix.startswith(b"<html"):
        raise RskProtocolError("RSK returned HTML instead of a file")
    if not prefix.startswith(b"<?xml"):
        raise RskProtocolError("RSK file does not have the expected XML signature")
    declaration_end = prefix.find(b"?>")
    if declaration_end < 0:
        raise RskProtocolError("RSK XML declaration is incomplete")
    root_match = _XML_ROOT_PATTERN.match(prefix[declaration_end + 2 :].lstrip())
    if root_match is None:
        raise RskProtocolError("RSK XML root element is missing from the prefix")
    if root_match.group(1).split(b":")[-1] == b"html":
        raise RskProtocolError("RSK returned HTML instead of an XML artifact")


def _looks_like_html(content: bytes | bytearray) -> bool:
    prefix = bytes(content[:512]).lstrip().lower()
    return prefix.startswith(b"<!doctype html") or prefix.startswith(b"<html")


def _content_length(headers: httpx.Headers) -> int | None:
    value = cast(str | None, headers.get("content-length"))
    if value is None:
        return None
    try:
        length = int(value)
    except ValueError as error:
        raise RskProtocolError("RSK returned an invalid Content-Length") from error
    if length < 0:
        raise RskProtocolError("RSK returned a negative Content-Length")
    return length


def _selected_headers(headers: httpx.Headers) -> tuple[tuple[str, str], ...]:
    selected_headers: list[tuple[str, str]] = []
    for name in _ARCHIVED_HEADER_NAMES:
        value = cast(str | None, headers.get(name))
        if value is not None:
            if not is_valid_rsk_archived_header(name, value):
                raise RskProtocolError("RSK archived header is outside its bound")
            selected_headers.append((name, value))
    return tuple(selected_headers)
