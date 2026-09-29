"""Bounded extraction of issued hospital orders from the official NFZ listing."""

import re
import unicodedata
from datetime import datetime
from html.parser import HTMLParser
from typing import ClassVar, cast, final, override

import httpx
from pydantic import BaseModel, ConfigDict, Field

from minos.ingest.watchers.baw.models import (
    BawDocument,
    BawResponse,
    BawSourceError,
    parse_document,
)

_MAX_LISTING_BYTES = 256 * 1024
_MAX_DETAIL_BYTES = 256 * 1024
_MAX_LISTING_ITEMS = 128
_MAX_FIELD_LENGTH = 512
_LISTING_ENDPOINT = (
    "https://www.nfz.gov.pl/zarzadzenia-prezesa/zarzadzenia-prezesa-nfz/"
    "1,strona.html?szukaj=szpitaln"
)
_ACT_NUMBER = re.compile(
    r"Zarządzenie nr ([1-9][0-9]{0,3})/(20[0-9]{2})/([A-Z]{2,8})\Z"
)
_DATE = re.compile(r"[0-9]{2}\.[0-9]{2}\.[0-9]{4}\Z")
_LISTING_URL = "/zarzadzenia-prezesa/zarzadzenia-prezesa-nfz/"
_HOSPITAL_SUBJECT = (
    "okreslenia warunkow zawierania i realizacji umow w rodzaju leczenie "
    "szpitalne oraz leczenie szpitalne swiadczenia wysokospecjalistyczne"
)


class NfzListingError(RuntimeError):
    """The NFZ listing cannot safely identify issued hospital orders."""


class HospitalOrderCandidate(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", strict=True, frozen=True
    )

    act_number: str = Field(min_length=1, max_length=24)
    listing_id: int = Field(gt=0)
    published_date: str = Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
    subject: str = Field(min_length=1, max_length=_MAX_FIELD_LENGTH)
    url: str = Field(min_length=1, max_length=256)


@final
class _AnonymousNfzTransport(httpx.BaseTransport):
    def __init__(self, wrapped: httpx.BaseTransport) -> None:
        self._wrapped = wrapped

    @override
    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.scheme != "https" or request.url.host != "www.nfz.gov.pl":
            raise NfzListingError("NFZ request host is not allowlisted")
        if "cookie" in request.headers or "authorization" in request.headers:
            raise NfzListingError("NFZ anonymous request contains credentials")
        response = self._wrapped.handle_request(request)
        if "set-cookie" in response.headers:
            del response.headers["set-cookie"]
        return response

    @override
    def close(self) -> None:
        self._wrapped.close()


def create_nfz_client(*, transport: httpx.BaseTransport | None = None) -> httpx.Client:
    return httpx.Client(
        auth=None,
        cookies={},
        follow_redirects=False,
        trust_env=False,
        timeout=httpx.Timeout(25.0, connect=5.0),
        headers={
            "Accept-Encoding": "identity",
            "User-Agent": "minos-health/0.0.1 nfz-watcher",
        },
        transport=_AnonymousNfzTransport(
            transport if transport is not None else httpx.HTTPTransport(trust_env=False)
        ),
    )


def fetch_hospital_listing(
    client: httpx.Client,
) -> tuple[bytes, tuple[HospitalOrderCandidate, ...]]:
    body = _fetch_html(client, _LISTING_ENDPOINT, _MAX_LISTING_BYTES)
    return body, parse_hospital_listing(body)


def fetch_order_detail(
    client: httpx.Client, candidate: HospitalOrderCandidate
) -> tuple[bytes, int]:
    if re.fullmatch(r"[1-9][0-9]{0,3}/20[0-9]{2}/DSOZ", candidate.act_number) is None:
        raise NfzListingError("NFZ order number is invalid")
    expected_url = (
        "https://www.nfz.gov.pl"
        + _LISTING_URL
        + "zarzadzenie-nr-"
        + candidate.act_number.replace("/", "").lower()
        + f",{candidate.listing_id}.html"
    )
    if candidate.url != expected_url:
        raise NfzListingError("NFZ order URL is not allowlisted")
    body = _fetch_html(client, candidate.url, _MAX_DETAIL_BYTES)
    return body, resolve_baw_document_id(body, candidate)


def _fetch_html(client: httpx.Client, url: str, limit_bytes: int) -> bytes:
    request = client.build_request("GET", url)
    if "cookie" in request.headers or "authorization" in request.headers:
        raise NfzListingError("NFZ listing request contains credentials")
    response = client.send(request, stream=True, follow_redirects=False)
    try:
        if response.status_code != 200:
            raise NfzListingError("NFZ listing request was rejected")
        media = cast(str, response.headers.get("content-type", ""))
        if media.split(";", 1)[0].lower() != "text/html":
            raise NfzListingError("NFZ listing is not HTML")
        encoding = cast(str, response.headers.get("content-encoding", ""))
        if encoding.lower() not in ("", "identity"):
            raise NfzListingError("NFZ HTML response encoding is not identity")
        length = cast(str | None, response.headers.get("content-length"))
        if length is not None and (
            not length.isascii() or not length.isdigit() or int(length) > limit_bytes
        ):
            raise NfzListingError("NFZ listing content length is invalid")
        parts: list[bytes] = []
        size_bytes = 0
        for part in response.iter_bytes(64 * 1024):
            size_bytes += len(part)
            if size_bytes > limit_bytes:
                raise NfzListingError("NFZ listing exceeds its byte ceiling")
            parts.append(part)
        if length is not None and size_bytes != int(length):
            raise NfzListingError("NFZ listing body is truncated")
    finally:
        response.close()
    return b"".join(parts)


@final
class _NewsParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[dict[str, str]] = []
        self.depth = 0
        self.field: str | None = None
        self.field_depth = 0
        self.in_title = False
        self.in_link = False
        self.item: dict[str, str] | None = None

    @override
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())
        if len(attributes) != len(attrs) and (self.depth > 0 or "news" in classes):
            raise NfzListingError("NFZ listing tag has conflicting attributes")
        if self.depth == 0:
            if tag == "div" and {"news", "row", "collapse"} <= classes:
                if len(self.items) >= _MAX_LISTING_ITEMS:
                    raise NfzListingError("NFZ listing exceeds its item bound")
                self.depth = 1
                self.item = {}
            return
        if tag == "div":
            self.depth += 1
            if "date" in classes or "desc" in classes:
                self.field = "date" if "date" in classes else "subject"
                self.field_depth = self.depth
        elif tag == "h3" and "title" in classes:
            self.in_title = True
        elif tag == "a" and self.in_title:
            if self.item is None or "href" in self.item:
                raise NfzListingError("NFZ listing title has duplicate links")
            self.item["href"] = attributes.get("href") or ""
            self.field = "title"
            self.in_link = True

    @override
    def handle_data(self, data: str) -> None:
        if self.item is None or self.field is None:
            return
        text = self.item.get(self.field, "") + data
        if len(text) > _MAX_FIELD_LENGTH:
            raise NfzListingError("NFZ listing text exceeds its bound")
        self.item[self.field] = text

    @override
    def handle_endtag(self, tag: str) -> None:
        if self.depth == 0:
            return
        if tag == "a" and self.in_link:
            self.in_link = False
            self.field = None
        elif tag == "h3":
            self.in_title = False
        elif tag == "div":
            if self.field_depth == self.depth:
                self.field = None
                self.field_depth = 0
            self.depth -= 1
            if self.depth == 0:
                if self.item is None:
                    raise NfzListingError("NFZ listing item is missing")
                self.items.append(self.item)
                self.item = None


def parse_hospital_listing(body: bytes) -> tuple[HospitalOrderCandidate, ...]:
    if not 1 <= len(body) <= _MAX_LISTING_BYTES:
        raise NfzListingError("NFZ listing byte count is invalid")
    try:
        text = body.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise NfzListingError("NFZ listing encoding is invalid") from None
    parser = _NewsParser()
    parser.feed(text)
    parser.close()
    if parser.depth != 0 or not parser.items:
        raise NfzListingError("NFZ listing has no complete issued orders")
    candidates: list[HospitalOrderCandidate] = []
    seen_ids: set[int] = set()
    seen_numbers: set[str] = set()
    for item in parser.items:
        title = " ".join(item.get("title", "").split())
        match = _ACT_NUMBER.fullmatch(title)
        if match is None:
            raise NfzListingError("NFZ listing order title is invalid")
        number, year, department = match.groups()
        act_number = f"{number}/{year}/{department}"
        raw_date = " ".join(item.get("date", "").split())
        if _DATE.fullmatch(raw_date) is None:
            raise NfzListingError("NFZ listing date is invalid")
        try:
            published_date = datetime.strptime(raw_date, "%d.%m.%Y").date().isoformat()
        except ValueError:
            raise NfzListingError("NFZ listing calendar date is invalid") from None
        path = item.get("href", "")
        expected_prefix = (
            _LISTING_URL + "zarzadzenie-nr-" + act_number.replace("/", "").lower() + ","
        )
        if (
            not path.startswith(expected_prefix)
            or re.fullmatch(r"[1-9][0-9]{0,9}\.html", path[len(expected_prefix) :])
            is None
        ):
            raise NfzListingError("NFZ listing order URL conflicts with its title")
        listing_id = int(path[len(expected_prefix) : -5])
        if listing_id in seen_ids:
            raise NfzListingError("NFZ listing contains a duplicate order ID")
        seen_ids.add(listing_id)
        if act_number in seen_numbers:
            raise NfzListingError("NFZ listing contains a duplicate act number")
        seen_numbers.add(act_number)
        subject = " ".join(item.get("subject", "").split())
        if not subject or len(subject) > _MAX_FIELD_LENGTH:
            raise NfzListingError("NFZ listing subject is invalid")
        if department != "DSOZ" or not _normalize(subject).endswith(_HOSPITAL_SUBJECT):
            continue
        candidates.append(
            HospitalOrderCandidate(
                act_number=act_number,
                listing_id=listing_id,
                published_date=published_date,
                subject=subject,
                url="https://www.nfz.gov.pl" + path,
            )
        )
    if not candidates:
        raise NfzListingError("NFZ listing has no hospital orders")
    return tuple(candidates)


@final
class _DocumentLinks(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self.href: str | None = None
        self.text = ""

    @override
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        if len(attrs) != len(dict(attrs)):
            raise NfzListingError("NFZ detail link has conflicting attributes")
        href = dict(attrs).get("href")
        if href is not None and href.startswith("https://baw.nfz.gov.pl/NFZ/document/"):
            if self.href is not None:
                raise NfzListingError("NFZ detail has nested document links")
            self.href = href
            self.text = ""

    @override
    def handle_data(self, data: str) -> None:
        if self.href is not None:
            self.text += data
            if len(self.text) > _MAX_FIELD_LENGTH:
                raise NfzListingError("NFZ detail link text exceeds its bound")

    @override
    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self.href is not None:
            self.links.append((self.href, " ".join(self.text.split())))
            if len(self.links) > 16:
                raise NfzListingError("NFZ detail has too many document links")
            self.href = None


def resolve_baw_document_id(body: bytes, candidate: HospitalOrderCandidate) -> int:
    if not 1 <= len(body) <= _MAX_DETAIL_BYTES:
        raise NfzListingError("NFZ detail byte count is invalid")
    try:
        text = body.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise NfzListingError("NFZ detail encoding is invalid") from None
    parser = _DocumentLinks()
    parser.feed(text)
    parser.close()
    if parser.href is not None or len(parser.links) != 1:
        raise NfzListingError("NFZ detail has no unique BAW document link")
    href, label = parser.links[0]
    pattern = (
        r"https://baw\.nfz\.gov\.pl/NFZ/document/([1-9][0-9]{0,9})/"
        + "Zarzadzenie-"
        + re.escape(candidate.act_number.replace("/", "_"))
    )
    match = re.fullmatch(pattern, href)
    if match is None or candidate.act_number not in label:
        raise NfzListingError("NFZ detail and BAW document identities conflict")
    return int(match.group(1))


def verify_baw_candidate(
    response: BawResponse, candidate: HospitalOrderCandidate, document_id: int
) -> BawDocument:
    try:
        document = parse_document(response, document_id)
    except BawSourceError:
        raise NfzListingError("BAW has no matching public issued document") from None
    if (
        document.ActNumber != candidate.act_number
        or document.LegalActTypeDescription != "Zarządzenie"
        or document.PublishedDate is None
        or re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}",
            document.PublishedDate,
        )
        is None
        or document.PublishedDate[:10] != candidate.published_date
    ):
        raise NfzListingError("NFZ listing and BAW metadata disagree")
    return document


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.casefold()).replace("ł", "l")
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    return " ".join(re.findall(r"[a-z0-9]+", normalized))
