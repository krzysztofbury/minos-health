import json
from typing import cast, final, override

import httpx

from minos.ingest.watchers.baw.models import BawResponse, BawSourceError

BASE_URL = "https://baw.nfz.gov.pl"
MAX_METADATA_BYTES = 2 * 1024 * 1024
MAX_FILE_BYTES = 512 * 1024 * 1024
_CHUNK_BYTES = 64 * 1024
_RETRYABLE_STATUSES = frozenset({408, 429, 500, 502, 503, 504})


class BawRetryableError(RuntimeError):
    """A transient BAW request failed before the watcher committed state."""


@final
class _AnonymousBawTransport(httpx.BaseTransport):
    def __init__(self, wrapped: httpx.BaseTransport) -> None:
        self._wrapped = wrapped

    @override
    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.scheme != "https" or request.url.host != "baw.nfz.gov.pl":
            raise BawSourceError("BAW request host is not allowlisted")
        if "cookie" in request.headers or "authorization" in request.headers:
            raise BawSourceError("BAW anonymous request contains credentials")
        response = self._wrapped.handle_request(request)
        if "set-cookie" in response.headers:
            del response.headers["set-cookie"]
        return response

    @override
    def close(self) -> None:
        self._wrapped.close()


def create_baw_client(*, transport: httpx.BaseTransport | None = None) -> httpx.Client:
    return httpx.Client(
        base_url=BASE_URL,
        auth=None,
        cookies={},
        follow_redirects=False,
        trust_env=False,
        timeout=httpx.Timeout(25.0, connect=5.0),
        headers={
            "Accept-Encoding": "identity",
            "User-Agent": "minos-health/0.0.1 baw-watcher",
        },
        transport=_AnonymousBawTransport(
            transport if transport is not None else httpx.HTTPTransport(trust_env=False)
        ),
    )


def get_json(client: httpx.Client, path: str, *, optional: bool = False) -> BawResponse:
    with client.stream("GET", path) as response:
        _check_status(response, optional=optional)
        if response.status_code == 204:
            return BawResponse(b"", "application/json")
        media = (
            cast(str, response.headers.get("content-type", "")).split(";", 1)[0].lower()
        )
        if media != "application/json":
            raise BawSourceError("BAW metadata is not JSON")
        body = _read_bounded(response, MAX_METADATA_BYTES)
        if not body or body.lstrip().startswith(b"<"):
            raise BawSourceError("BAW returned an empty response or HTML challenge")
        try:
            _ = cast(object, json.loads(body))
        except UnicodeDecodeError, ValueError, RecursionError:
            raise BawSourceError("BAW metadata JSON is invalid") from None
        return BawResponse(body, media)


def _check_status(response: httpx.Response, *, optional: bool = False) -> None:
    if response.status_code == 204 and optional:
        return
    if response.status_code == 200:
        return
    if response.status_code in _RETRYABLE_STATUSES:
        raise BawRetryableError("BAW temporarily rejected the request")
    raise BawSourceError("BAW request was rejected")


def _read_bounded(response: httpx.Response, limit: int) -> bytes:
    length = cast(str | None, response.headers.get("content-length"))
    if length is not None:
        if not length.isascii() or not length.isdigit() or int(length) > limit:
            raise BawSourceError("BAW content length is invalid")
    pieces: list[bytes] = []
    received = 0
    for chunk in response.iter_bytes(_CHUNK_BYTES):
        received += len(chunk)
        if received > limit:
            raise BawSourceError("BAW response exceeds its byte ceiling")
        pieces.append(chunk)
    if length is not None and received != int(length):
        raise BawSourceError("BAW response is truncated")
    return b"".join(pieces)
