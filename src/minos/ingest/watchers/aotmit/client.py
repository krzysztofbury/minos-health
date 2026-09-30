"""Read-only bounded page-one AOTMiT tariff announcements."""

import re
import time
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import cast, final, override

import httpx

_BASE = "https://bip.aotm.gov.pl"
_LISTING = _BASE + "/taryfikacja-swiadczen/860-taryfikacja-obwieszczenia"
_FILE_PATH = "/assets/files/taryfikacja/taryfy/obwieszczenia/"


class AotmitSourceError(ValueError):
    """The publication cannot be safely attributed to an official tariff notice."""


class AotmitRetryableError(RuntimeError):
    """The AOTMiT page or file is temporarily unavailable."""


@dataclass(frozen=True, slots=True)
class AotmitNotice:
    url: str
    title: str
    published: str


@dataclass(frozen=True, slots=True)
class AotmitResponse:
    body: bytes
    headers: dict[str, str]
    retry_after_seconds: int


@final
class _AnonymousTransport(httpx.BaseTransport):
    def __init__(self, wrapped: httpx.BaseTransport) -> None:
        self._wrapped = wrapped

    @override
    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.scheme != "https" or request.url.host != "bip.aotm.gov.pl":
            raise AotmitSourceError("AOTMiT host is not allowlisted")
        if "cookie" in request.headers or "authorization" in request.headers:
            raise AotmitSourceError("AOTMiT request must be anonymous")
        response = self._wrapped.handle_request(request)
        if "set-cookie" in response.headers:
            del response.headers["set-cookie"]
        return response

    @override
    def close(self) -> None:
        self._wrapped.close()


def create_aotmit_client(
    *, transport: httpx.BaseTransport | None = None
) -> httpx.Client:
    return httpx.Client(
        trust_env=False,
        follow_redirects=False,
        timeout=httpx.Timeout(30.0, connect=5.0),
        headers={
            "Accept-Encoding": "identity",
            "User-Agent": "minos-health/0.0.1 aotmit-watcher",
        },
        transport=_AnonymousTransport(
            transport if transport is not None else httpx.HTTPTransport(trust_env=False)
        ),
    )


def fetch_listing(
    client: httpx.Client,
) -> tuple[AotmitResponse, tuple[AotmitNotice, ...]]:
    response = _fetch(client, _LISTING, 512 * 1024)
    if response.headers.get("content-type", "").split(";", 1)[0].lower() != "text/html":
        raise AotmitSourceError("AOTMiT listing is not HTML")
    return response, parse_listing(response.body)


def fetch_pdf(client: httpx.Client, notice: AotmitNotice) -> AotmitResponse:
    if not notice.url.startswith(_BASE + _FILE_PATH):
        raise AotmitSourceError("AOTMiT PDF path is invalid")
    suffix = notice.url[len(_BASE + _FILE_PATH) :]
    if (
        ".." in suffix
        or re.search(r"%2f|%5c", suffix, re.I)
        or re.fullmatch(r"20[0-9]{2}/[\w .%-]{3,180}\.pdf", suffix) is None
    ):
        raise AotmitSourceError("AOTMiT PDF path is invalid")
    response = _fetch(client, notice.url, 32 * 1024 * 1024)
    if not response.body.startswith(b"%PDF-") or b"%%EOF" not in response.body[-1024:]:
        raise AotmitSourceError("AOTMiT announcement is not a complete PDF")
    return response


def _fetch(client: httpx.Client, url: str, maximum: int) -> AotmitResponse:
    request = client.build_request("GET", url)
    if "cookie" in request.headers or "authorization" in request.headers:
        raise AotmitSourceError("AOTMiT request contains credentials")
    with closing(client.send(request, stream=True)) as response:
        if response.status_code in (408, 429, 500, 502, 503, 504):
            raise AotmitRetryableError("AOTMiT request temporarily failed")
        if response.status_code != 200:
            raise AotmitSourceError("AOTMiT request was rejected")
        retry = cast(str | None, response.headers.get("retry-after"))
        delay = 0
        if retry is not None:
            if retry.isascii() and retry.isdecimal():
                delay = int(retry)
            else:
                try:
                    requested = parsedate_to_datetime(retry)
                except TypeError, ValueError, IndexError:
                    raise AotmitSourceError("AOTMiT Retry-After is invalid") from None
                if requested.tzinfo is None:
                    raise AotmitSourceError("AOTMiT Retry-After has no timezone")
                delay = max(0, int((requested - datetime.now(UTC)).total_seconds()) + 1)
            if delay > 300:
                raise AotmitSourceError("AOTMiT Retry-After exceeds its bound")
        length = cast(str | None, response.headers.get("content-length"))
        if length is not None and (
            not length.isascii() or not length.isdecimal() or int(length) > maximum
        ):
            raise AotmitSourceError("AOTMiT response length is invalid")
        if (
            cast(str, response.headers.get("content-encoding", "identity")).lower()
            != "identity"
        ):
            raise AotmitSourceError("AOTMiT response encoding is unsupported")
        parts: list[bytes] = []
        size = 0
        for part in response.iter_bytes(65_536):
            size += len(part)
            if size > maximum:
                raise AotmitSourceError("AOTMiT response exceeds its byte limit")
            parts.append(part)
        if size == 0 or (length is not None and size != int(length)):
            raise AotmitSourceError("AOTMiT response is empty or truncated")
        headers: dict[str, str] = {}
        for key in (
            "content-type",
            "content-length",
            "last-modified",
            "etag",
            "retry-after",
        ):
            value = cast(str | None, response.headers.get(key))
            if value is not None:
                if len(value) > 256 or any(
                    ord(char) < 32 or ord(char) == 127 for char in value
                ):
                    raise AotmitSourceError("AOTMiT selected header is invalid")
                headers[key] = value
        return AotmitResponse(b"".join(parts), headers, delay)


@final
class _Notices(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.strong = False
        self.published = False
        self.intro_depth = 0
        self.current_title = ""
        self.current_summary = ""
        self.current_date = ""
        self.current_urls: list[str] = []
        self.notices: list[AotmitNotice] = []

    @override
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if len(attributes) != len(attrs) and self.depth:
            raise AotmitSourceError("AOTMiT notice tag has duplicate attributes")
        classes = set((attributes.get("class") or "").split())
        if tag == "div":
            if self.depth:
                self.depth += 1
            elif "blog-item" in classes:
                if len(self.notices) >= 32:
                    raise AotmitSourceError("AOTMiT listing has too many notices")
                self.depth = 1
                self.current_title = ""
                self.current_summary = ""
                self.current_date = ""
                self.current_urls = []
            if self.depth and "introtext" in classes:
                self.intro_depth = self.depth
        if not self.depth:
            return
        if tag == "strong":
            self.strong = True
        if tag == "dd" and "published" in classes:
            self.published = True
        if tag == "time" and self.published:
            value = attributes.get("datetime") or ""
            if self.current_date and self.current_date != value:
                raise AotmitSourceError("AOTMiT notice has conflicting dates")
            self.current_date = value
        if tag == "a" and (attributes.get("href") or "").startswith(_FILE_PATH):
            if len(self.current_urls) >= 8:
                raise AotmitSourceError("AOTMiT notice has too many PDFs")
            self.current_urls.append(attributes["href"] or "")

    @override
    def handle_data(self, data: str) -> None:
        if self.depth and self.strong:
            self.current_title += data
            if len(self.current_title) > 1024:
                raise AotmitSourceError("AOTMiT notice title exceeds its bound")
        if self.intro_depth and len(self.current_summary) < 1024:
            self.current_summary += data[: 1024 - len(self.current_summary)]

    @override
    def handle_endtag(self, tag: str) -> None:
        if tag == "strong":
            self.strong = False
        if tag == "dd":
            self.published = False
        if tag == "div" and self.depth:
            if self.depth == self.intro_depth:
                self.intro_depth = 0
            self.depth -= 1
            if not self.depth:
                title = " ".join((self.current_title or self.current_summary).split())
                if not title or not self.current_date or not self.current_urls:
                    raise AotmitSourceError("AOTMiT announcement is incomplete")
                if "leczenia szpitalnego" not in title.casefold():
                    return
                if (
                    re.fullmatch(
                        r"20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9:+-]{8,14}",
                        self.current_date,
                    )
                    is None
                ):
                    raise AotmitSourceError("AOTMiT publication date is invalid")
                for path in self.current_urls:
                    self.notices.append(
                        AotmitNotice(_BASE + path, title, self.current_date[:10])
                    )


def parse_listing(body: bytes) -> tuple[AotmitNotice, ...]:
    if not 1 <= len(body) <= 512 * 1024:
        raise AotmitSourceError("AOTMiT listing byte count is invalid")
    try:
        text = body.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise AotmitSourceError("AOTMiT listing encoding is invalid") from None
    parser = _Notices()
    parser.feed(text)
    parser.close()
    if parser.depth or not parser.notices or len(parser.notices) > 16:
        raise AotmitSourceError("AOTMiT listing has no bounded hospital notices")
    unique: dict[str, AotmitNotice] = {}
    for notice in parser.notices:
        previous = unique.get(notice.url)
        if previous is not None and previous != notice:
            raise AotmitSourceError(
                "AOTMiT PDF has conflicting duplicate announcements"
            )
        unique[notice.url] = notice
    return tuple(unique.values())


def honor_retry_after(response: AotmitResponse, *, remaining_seconds: float) -> None:
    if response.retry_after_seconds > remaining_seconds:
        raise AotmitRetryableError("AOTMiT Retry-After exceeds the collection deadline")
    if response.retry_after_seconds:
        time.sleep(response.retry_after_seconds)
