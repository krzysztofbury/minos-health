"""Bounded discovery of files linked from a current NFZ JGP notice."""

import re
from contextlib import closing
from dataclasses import dataclass
from html.parser import HTMLParser
from io import BytesIO
from typing import cast, final, override
from zipfile import BadZipFile, ZipFile

import httpx

from minos.ingest.watchers.nfz.discovery import NfzListingError, NfzRetryableError

_FILE_PREFIX = "/download/gfx/nfz/pl/defaultaktualnosci/354/"
_MAX_FILE_BYTES = 32 * 1024 * 1024


@final
class _JgpFiles(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.urls: list[str] = []

    @override
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if len(attributes) != len(attrs):
            raise NfzListingError("NFZ JGP file link has conflicting attributes")
        classes = set((attributes.get("class") or "").split())
        if tag in ("div", "ul", "li"):
            if self.depth:
                self.depth += 1
            elif tag == "ul" and "files-list" in classes:
                self.depth = 1
        if tag == "a" and self.depth and "files-link" in classes:
            if len(self.urls) >= 8:
                raise NfzListingError("NFZ JGP notice exceeds its file bound")
            self.urls.append(attributes.get("href") or "")

    @override
    def handle_endtag(self, tag: str) -> None:
        if tag in ("div", "ul", "li") and self.depth:
            self.depth -= 1


def parse_jgp_files(detail: bytes, item_id: int) -> tuple[str, ...]:
    if not 1 <= len(detail) <= 256 * 1024 or not 1 <= item_id <= 2_147_483_647:
        raise NfzListingError("NFZ JGP detail or notice ID is invalid")
    try:
        html = detail.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise NfzListingError("NFZ JGP detail encoding is invalid") from None
    parser = _JgpFiles()
    parser.feed(html)
    parser.close()
    if parser.depth or not parser.urls:
        raise NfzListingError("NFZ JGP notice has no complete file list")
    urls: list[str] = []
    for path in parser.urls:
        if (
            re.fullmatch(
                re.escape(_FILE_PREFIX)
                + str(item_id)
                + r"/[1-9][0-9]{0,2}/[a-zA-Z0-9_.-]{3,160}\.(?:docx|xlsx)",
                path,
            )
            is None
        ):
            raise NfzListingError("NFZ JGP file path is outside the notice")
        url = "https://www.nfz.gov.pl" + path
        if url in urls:
            raise NfzListingError("NFZ JGP notice has a duplicate file")
        urls.append(url)
    return tuple(urls)


@dataclass(frozen=True, slots=True)
class JgpFile:
    body: bytes
    headers: dict[str, str]


def fetch_jgp_file(client: httpx.Client, url: str, item_id: int) -> JgpFile:
    prefix = "https://www.nfz.gov.pl" + _FILE_PREFIX + str(item_id) + "/"
    if (
        not url.startswith(prefix)
        or re.fullmatch(
            r"[1-9][0-9]{0,2}/[a-zA-Z0-9_.-]{3,160}\.(?:docx|xlsx)",
            url[len(prefix) :],
        )
        is None
    ):
        raise NfzListingError("NFZ JGP file request is outside the notice")
    request = client.build_request("GET", url)
    if "cookie" in request.headers or "authorization" in request.headers:
        raise NfzListingError("NFZ JGP request contains credentials")
    with closing(client.send(request, stream=True)) as response:
        if response.status_code in (408, 429, 500, 502, 503, 504):
            raise NfzRetryableError("NFZ JGP file request temporarily failed")
        if response.status_code != 200:
            raise NfzListingError("NFZ JGP file request was rejected")
        if (
            cast(str, response.headers.get("content-encoding", "identity")).lower()
            != "identity"
        ):
            raise NfzListingError("NFZ JGP file encoding is unsupported")
        length = cast(str | None, response.headers.get("content-length"))
        if length is not None and (
            not length.isascii()
            or not length.isdecimal()
            or int(length) > _MAX_FILE_BYTES
        ):
            raise NfzListingError("NFZ JGP file content length is invalid")
        parts: list[bytes] = []
        total = 0
        for chunk in response.iter_bytes(65_536):
            total += len(chunk)
            if total > _MAX_FILE_BYTES:
                raise NfzListingError("NFZ JGP file exceeds its byte limit")
            parts.append(chunk)
        if total < 4 or (length is not None and total != int(length)):
            raise NfzListingError("NFZ JGP file is empty or truncated")
        body = b"".join(parts)
        if not body.startswith(b"PK\x03\x04"):
            raise NfzListingError("NFZ JGP file is not an Office ZIP document")
        try:
            with ZipFile(BytesIO(body)) as archive:
                names = archive.namelist()
                required = (
                    "word/document.xml" if url.endswith(".docx") else "xl/workbook.xml"
                )
                if (
                    not 1 <= len(names) <= 1024
                    or "[Content_Types].xml" not in names
                    or required not in names
                ):
                    raise NfzListingError("NFZ JGP Office ZIP structure is invalid")
        except BadZipFile:
            raise NfzListingError("NFZ JGP Office ZIP is truncated") from None
        headers: dict[str, str] = {}
        for key in ("content-type", "content-length", "last-modified", "etag"):
            value = cast(str | None, response.headers.get(key))
            if value is not None:
                if len(value) > 256 or any(
                    ord(char) < 32 or ord(char) == 127 for char in value
                ):
                    raise NfzListingError("NFZ JGP response header is invalid")
                headers[key] = value
        return JgpFile(body, headers)
