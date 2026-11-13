"""Read-only bounded dane.gov.pl metadata of NFZ's monthly JGP reports (#283).

Only the listing is read, never a report: a new month is a review item, and a
reviewed release pins it.

Since 2026-11-10 dane.gov.pl answers only Polish source addresses (#344). In
production the listing is read through the `minos-dane-gov` Cloudflare Worker
(#349), which answers exactly this one request from a Polish location.
"""

import os
import re
import stat
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, cast, final, override

import httpx
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, ValidationError

from minos.ingest.jgp_statistics_manifest import report_month
from minos.ingest.watchers.nfz.public_models import Day, Text

_HOST = "api.dane.gov.pl"
# NFZ has republished twelve old reports on one day, and each then sorts first.
_PAGE = 20
LISTING_URL = (
    f"https://{_HOST}/1.4/datasets/2581/resources?per_page={_PAGE}&sort=-data_date"
)
_MAX_BYTES = 256 * 1024
PROXY_KEY_HEADER = "x-minos-proxy-key"
PROXY_COLO_HEADER = "x-minos-colo"
# The Worker aborts its upstream call after 20 s and answers 504; the client
# waits longer so that answer, not a client timeout, ends a slow call.
_READ_TIMEOUT_S = 30.0
_KEY = re.compile(r"[A-Za-z0-9_-]{32,256}")
_KEY_FILE_MAX_BYTES = 512
_COLO = re.compile(r"[A-Z]{3}")
_COLO_EXTENSION = "minos_dane_gov_colo"


class DaneGovSourceError(ValueError):
    """The dane.gov.pl answer is not the expected report listing."""


class DaneGovRetryableError(RuntimeError):
    """The dane.gov.pl API is temporarily unavailable."""


class _Attributes(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", strict=True)
    title: Text
    data_date: Day
    modified: str = Field(min_length=1, max_length=64)
    link: str = Field(min_length=1, max_length=1024)
    file_size: int | None = Field(default=None, ge=1, le=1_073_741_824)


class _Resource(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", strict=True)
    id: str = Field(pattern=r"^[1-9][0-9]{0,8}$")
    attributes: _Attributes


class _Listing(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", strict=True)
    data: list[_Resource] = Field(min_length=1, max_length=_PAGE)


@dataclass(frozen=True, slots=True)
class NewestReport:
    month: str
    resource_id: int
    source_url: str
    published_date: str
    title: str
    size_bytes: int
    modified: str


@dataclass(frozen=True, slots=True)
class ReportsResponse:
    body: bytes
    headers: dict[str, str]
    report: NewestReport
    # Resources at least as new as the report that are not named like one: a
    # rename that would otherwise look like a quiet listing.
    unrecognized: tuple[int, ...]
    # The Cloudflare location that ran the Worker, or None for a direct read.
    proxy_colo: str | None = None


class DaneGovProxyError(ValueError):
    """The dane.gov.pl proxy configuration is unusable.

    The message never names the key, its file or the Worker hostname.
    """


@dataclass(frozen=True, slots=True)
class DaneGovProxy:
    origin: str
    key: str

    # Neither the Worker hostname nor the key may reach a log or traceback.
    @override
    def __repr__(self) -> str:
        return "DaneGovProxy(<redacted>)"


def _proxy_origin(value: str) -> str:
    """Accept only a bare HTTPS origin, written in its canonical form."""
    try:
        url = AnyHttpUrl(value)
    except ValidationError:
        raise DaneGovProxyError("dane.gov.pl proxy origin is invalid") from None
    origin = f"https://{url.host}" + ("" if url.port == 443 else f":{url.port}")
    if url.scheme != "https" or value not in (origin, origin + "/"):
        # Also refuses user information, a path, a query and a fragment.
        raise DaneGovProxyError("dane.gov.pl proxy origin is invalid")
    return origin


def load_dane_gov_proxy(origin: str, key_file: Path) -> DaneGovProxy:
    """Validate the Worker origin and read its key from a host secret file."""
    checked = _proxy_origin(origin)
    try:
        descriptor = os.open(key_file, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError:
        raise DaneGovProxyError("dane.gov.pl proxy key is unreadable") from None
    try:
        status = os.fstat(descriptor)
        if (
            not stat.S_ISREG(status.st_mode)
            or status.st_size > _KEY_FILE_MAX_BYTES
            or status.st_mode & 0o007
        ):
            raise DaneGovProxyError("dane.gov.pl proxy key file is unsafe")
        content = os.read(descriptor, _KEY_FILE_MAX_BYTES + 1)
    except OSError:
        raise DaneGovProxyError("dane.gov.pl proxy key is unreadable") from None
    finally:
        os.close(descriptor)
    key = content.removesuffix(b"\n")
    if _KEY.fullmatch(key.decode("ascii", errors="replace")) is None:
        raise DaneGovProxyError("dane.gov.pl proxy key is invalid")
    return DaneGovProxy(checked, key.decode("ascii"))


@final
class _AnonymousTransport(httpx.BaseTransport):
    def __init__(self, wrapped: httpx.BaseTransport) -> None:
        self._wrapped = wrapped

    @override
    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.scheme != "https" or request.url.host != _HOST:
            raise DaneGovSourceError("dane.gov.pl host is not allowlisted")
        if "cookie" in request.headers or "authorization" in request.headers:
            raise DaneGovSourceError("dane.gov.pl request must be anonymous")
        response = self._wrapped.handle_request(request)
        if "set-cookie" in response.headers:
            del response.headers["set-cookie"]
        return response

    @override
    def close(self) -> None:
        self._wrapped.close()


@final
class _ProxiedTransport(httpx.BaseTransport):
    """Send the one canonical listing request to the Worker, never elsewhere.

    The caller still names the canonical dane.gov.pl URL; only this transport
    knows the Worker origin and key, so neither can reach another host, a
    redirect or an error message.
    """

    def __init__(self, wrapped: httpx.BaseTransport, proxy: DaneGovProxy) -> None:
        self._wrapped = wrapped
        self._proxy = proxy

    @override
    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if request.method != "GET" or str(request.url) != LISTING_URL:
            raise DaneGovSourceError("dane.gov.pl request is not allowlisted")
        if any(
            name in request.headers
            for name in ("cookie", "authorization", PROXY_KEY_HEADER)
        ):
            raise DaneGovSourceError("dane.gov.pl request must be anonymous")
        headers = httpx.Headers(request.headers)
        del headers["host"]
        headers[PROXY_KEY_HEADER] = self._proxy.key
        forwarded = httpx.Request(
            "GET",
            self._proxy.origin + request.url.raw_path.decode("ascii"),
            headers=headers,
            extensions=request.extensions,
        )
        if (
            forwarded.url.scheme != "https"
            or f"https://{forwarded.url.netloc.decode('ascii')}" != self._proxy.origin
        ):
            raise DaneGovSourceError("dane.gov.pl proxy is not allowlisted")
        response = self._wrapped.handle_request(forwarded)
        if "set-cookie" in response.headers:
            del response.headers["set-cookie"]
        colo = cast(str | None, response.headers.get(PROXY_COLO_HEADER))
        if response.status_code == 200:
            if colo is None or _COLO.fullmatch(colo) is None:
                response.close()
                raise DaneGovSourceError("dane.gov.pl proxy location is missing")
            response.extensions[_COLO_EXTENSION] = colo
        return response

    @override
    def close(self) -> None:
        self._wrapped.close()


def create_dane_gov_client(
    *,
    transport: httpx.BaseTransport | None = None,
    proxy: DaneGovProxy | None = None,
) -> httpx.Client:
    wrapped = (
        transport if transport is not None else httpx.HTTPTransport(trust_env=False)
    )
    return httpx.Client(
        trust_env=False,
        follow_redirects=False,
        timeout=httpx.Timeout(_READ_TIMEOUT_S, connect=5.0),
        headers={
            "Accept": "application/vnd.api+json",
            "Accept-Encoding": "identity",
            "User-Agent": "minos-health/0.0.1 dane-gov-watcher",
        },
        transport=_AnonymousTransport(wrapped)
        if proxy is None
        else _ProxiedTransport(wrapped, proxy),
    )


def _newest(body: bytes) -> tuple[NewestReport, tuple[int, ...]]:
    try:
        listing = _Listing.model_validate_json(body)
    except ValidationError:
        raise DaneGovSourceError("dane.gov.pl listing is invalid") from None
    reports: list[NewestReport] = []
    for resource in listing.data:
        attributes = resource.attributes
        month = report_month(attributes.link)
        if month is not None and attributes.file_size is not None:
            reports.append(
                NewestReport(
                    month,
                    int(resource.id),
                    attributes.link,
                    attributes.data_date,
                    attributes.title,
                    attributes.file_size,
                    attributes.modified,
                )
            )
    if not reports:
        raise DaneGovSourceError("dane.gov.pl listing has no monthly report")
    newest = max(reports, key=lambda item: item.month)
    known = {item.resource_id for item in reports}
    return newest, tuple(
        int(resource.id)
        for resource in listing.data
        if int(resource.id) not in known
        and resource.attributes.data_date >= newest.published_date
    )


def fetch_newest_report(client: httpx.Client) -> ReportsResponse:
    with closing(
        client.send(client.build_request("GET", LISTING_URL), stream=True)
    ) as response:
        if response.status_code in (408, 429, 500, 502, 503, 504):
            raise DaneGovRetryableError("dane.gov.pl request temporarily failed")
        if response.status_code != 200:
            raise DaneGovSourceError("dane.gov.pl request was rejected")
        media = cast(str, response.headers.get("content-type", ""))
        if media.split(";", 1)[0].strip().lower() not in (
            "application/json",
            "application/vnd.api+json",
        ):
            raise DaneGovSourceError("dane.gov.pl answer is not JSON")
        if (
            cast(str, response.headers.get("content-encoding", "identity")).lower()
            != "identity"
        ):
            raise DaneGovSourceError("dane.gov.pl response encoding is unsupported")
        parts: list[bytes] = []
        size = 0
        for part in response.iter_bytes(65_536):
            size += len(part)
            if size > _MAX_BYTES:
                raise DaneGovSourceError("dane.gov.pl response exceeds its byte limit")
            parts.append(part)
        headers: dict[str, str] = {}
        for key in ("content-type", "content-length", "last-modified", "etag"):
            value = cast(str | None, response.headers.get(key))
            if value is not None:
                if len(value) > 256 or any(
                    ord(char) < 32 or ord(char) == 127 for char in value
                ):
                    raise DaneGovSourceError("dane.gov.pl selected header is invalid")
                headers[key] = value
        colo = cast(str | None, response.extensions.get(_COLO_EXTENSION))
    body = b"".join(parts)
    report, unrecognized = _newest(body)
    return ReportsResponse(body, headers, report, unrecognized, colo)
