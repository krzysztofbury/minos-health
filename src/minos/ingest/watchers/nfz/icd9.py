"""NFZ's human-readable ICD-9 companion listing and selected XLSX bytes."""

import re
from contextlib import closing
from dataclasses import dataclass
from html.parser import HTMLParser
from io import BytesIO
from typing import cast, final, override
from zipfile import BadZipFile, ZipFile

import httpx

from minos.ingest.watchers.nfz.discovery import (
    NfzHtmlResponse,
    NfzListingError,
    NfzRetryableError,
    fetch_nfz_html,
)

_PAGE = "https://www.nfz.gov.pl/dla-swiadczeniodawcy/slowniki/pliki-icd-9-pl/"
_PREFIX = "/download/gfx/nfz/pl/defaultstronaopisowa/782/1/2/"
_MAX_FILE_BYTES = 32 * 1024 * 1024


@final
class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.paths: list[str] = []

    @override
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        attributes = dict(attrs)
        if len(attributes) != len(attrs):
            raise NfzListingError("NFZ ICD-9 file link has duplicate attributes")
        if "files-link" in (attributes.get("class") or "").split():
            if len(self.paths) >= 128:
                raise NfzListingError("NFZ ICD-9 page exceeds its file bound")
            self.paths.append(attributes.get("href") or "")


def fetch_companion_page(
    client: httpx.Client,
) -> tuple[NfzHtmlResponse, dict[str, str]]:
    response = fetch_nfz_html(client, _PAGE, 256 * 1024)
    return response, parse_companion_page(response.body)


def parse_companion_page(body: bytes) -> dict[str, str]:
    if not 1 <= len(body) <= 256 * 1024:
        raise NfzListingError("NFZ ICD-9 page byte count is invalid")
    try:
        text = body.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise NfzListingError("NFZ ICD-9 page encoding is invalid") from None
    parser = _Links()
    parser.feed(text)
    parser.close()
    if not parser.paths:
        raise NfzListingError("NFZ ICD-9 page has no files")
    versions: dict[str, str] = {}
    for path in parser.paths:
        if not path.startswith(_PREFIX):
            raise NfzListingError("NFZ ICD-9 file has an unsupported path")
        filename = path[len(_PREFIX) :]
        if re.fullmatch(r"[a-zA-Z0-9_.-]{3,140}\.(?:xls|xlsx)", filename) is None:
            raise NfzListingError("NFZ ICD-9 file name is invalid")
        if filename.endswith(".xls"):
            continue
        match = re.search(r"(?:_|-)5\.([1-9][0-9]?)_?\.xlsx\Z", filename)
        if match is None:
            continue
        version = "5." + match[1]
        url = "https://www.nfz.gov.pl" + path
        previous = versions.get(version)
        if previous is not None:
            raise NfzListingError("NFZ ICD-9 page has a duplicate version")
        versions[version] = url
    if not versions:
        raise NfzListingError("NFZ ICD-9 page has no versioned XLSX")
    return versions


@dataclass(frozen=True, slots=True)
class Icd9Companion:
    body: bytes
    headers: dict[str, str]


def fetch_companion(client: httpx.Client, url: str) -> Icd9Companion:
    if (
        not url.startswith("https://www.nfz.gov.pl" + _PREFIX)
        or re.fullmatch(
            r"[a-zA-Z0-9_.-]{3,140}\.xlsx",
            url[len("https://www.nfz.gov.pl" + _PREFIX) :],
        )
        is None
    ):
        raise NfzListingError("NFZ ICD-9 companion URL is invalid")
    request = client.build_request("GET", url)
    if "cookie" in request.headers or "authorization" in request.headers:
        raise NfzListingError("NFZ ICD-9 request contains credentials")
    with closing(client.send(request, stream=True)) as response:
        if response.status_code in (408, 429, 500, 502, 503, 504):
            raise NfzRetryableError("NFZ ICD-9 companion request temporarily failed")
        if response.status_code != 200:
            raise NfzListingError("NFZ ICD-9 companion request was rejected")
        length = cast(str | None, response.headers.get("content-length"))
        if length is not None and (
            not length.isascii()
            or not length.isdecimal()
            or int(length) > _MAX_FILE_BYTES
        ):
            raise NfzListingError("NFZ ICD-9 companion length is invalid")
        if (
            cast(str, response.headers.get("content-encoding", "identity")).lower()
            != "identity"
        ):
            raise NfzListingError("NFZ ICD-9 companion encoding is unsupported")
        parts: list[bytes] = []
        total = 0
        for chunk in response.iter_bytes(65_536):
            total += len(chunk)
            if total > _MAX_FILE_BYTES:
                raise NfzListingError("NFZ ICD-9 companion exceeds its byte limit")
            parts.append(chunk)
        if total < 4 or (length is not None and total != int(length)):
            raise NfzListingError("NFZ ICD-9 companion is empty or truncated")
        body = b"".join(parts)
        if not body.startswith(b"PK\x03\x04"):
            raise NfzListingError("NFZ ICD-9 companion is not an XLSX ZIP")
        try:
            with ZipFile(BytesIO(body)) as archive:
                if (
                    "xl/workbook.xml" not in archive.namelist()
                    or "[Content_Types].xml" not in archive.namelist()
                ):
                    raise NfzListingError("NFZ ICD-9 XLSX structure is incomplete")
        except BadZipFile:
            raise NfzListingError(
                "NFZ ICD-9 XLSX central directory is invalid"
            ) from None
        headers: dict[str, str] = {}
        for key in ("content-type", "content-length", "last-modified", "etag"):
            value = cast(str | None, response.headers.get(key))
            if value is not None:
                if len(value) > 256 or any(
                    ord(char) < 32 or ord(char) == 127 for char in value
                ):
                    raise NfzListingError("NFZ ICD-9 response header is invalid")
                headers[key] = value
        return Icd9Companion(body, headers)
