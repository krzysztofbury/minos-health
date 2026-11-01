"""Read-only bounded dane.gov.pl metadata of NFZ's monthly JGP reports (#283).

Only the listing is read, never a report: a new month is a review item, and a
reviewed release pins it.
"""

from contextlib import closing
from dataclasses import dataclass
from typing import ClassVar, cast, final, override

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from minos.ingest.jgp_statistics_manifest import report_month
from minos.ingest.watchers.nfz.public_models import Day, Text

_HOST = "api.dane.gov.pl"
# NFZ has republished twelve old reports on one day, and each then sorts first.
_PAGE = 20
LISTING_URL = (
    f"https://{_HOST}/1.4/datasets/2581/resources?per_page={_PAGE}&sort=-data_date"
)
_MAX_BYTES = 256 * 1024


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


def create_dane_gov_client(
    *, transport: httpx.BaseTransport | None = None
) -> httpx.Client:
    return httpx.Client(
        trust_env=False,
        follow_redirects=False,
        timeout=httpx.Timeout(30.0, connect=5.0),
        headers={
            "Accept": "application/vnd.api+json",
            "Accept-Encoding": "identity",
            "User-Agent": "minos-health/0.0.1 dane-gov-watcher",
        },
        transport=_AnonymousTransport(
            transport if transport is not None else httpx.HTTPTransport(trust_env=False)
        ),
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
    body = b"".join(parts)
    report, unrecognized = _newest(body)
    return ReportsResponse(body, headers, report, unrecognized)
