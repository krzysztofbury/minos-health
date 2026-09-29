"""Validate CZS MODTAR's published label against its machine generation."""

import hashlib
import re
from contextlib import closing
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import cast, final, override

import httpx

from minos.ingest.watchers.czs.client import (
    CzsEvidence,
    CzsSourceError,
    CzsVersion,
    fetch_czs_page,
)

_BASE = "https://slowniki.nfz.gov.pl"
_ENTRY = _BASE + "/WersjeSlownikow/MainDictionary?kodSlownika=MODTAR"


@dataclass(frozen=True, slots=True)
class ModtarDisplay:
    page_id: int
    label: str
    technical_generation: int
    published_date: str
    effective_from: str


@final
class _VersionFields(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.field_depth = 0
        self.value_depth = 0
        self.label = ""
        self.value = ""
        self.fields: dict[str, str] = {}
        self.pending = ""

    @override
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if len(attributes) != len(attrs) and (self.field_depth or self.value_depth):
            raise CzsSourceError("CZS version field has conflicting attributes")
        if tag != "div":
            return
        classes = set((attributes.get("class") or "").split())
        if self.field_depth:
            self.field_depth += 1
        elif self.value_depth:
            self.value_depth += 1
        elif "filter-name" in classes and "hidden-sm" in classes:
            self.field_depth = 1
            self.label = ""
        elif "filter-val" in classes and self.pending:
            self.value_depth = 1
            self.value = ""

    @override
    def handle_data(self, data: str) -> None:
        if self.field_depth:
            self.label += data
            if len(self.label) > 96:
                raise CzsSourceError("CZS version label exceeds its bound")
        elif self.value_depth:
            self.value += data
            if len(self.value) > 128:
                raise CzsSourceError("CZS version field value exceeds its bound")

    @override
    def handle_endtag(self, tag: str) -> None:
        if tag != "div":
            return
        if self.field_depth:
            self.field_depth -= 1
            if not self.field_depth:
                self.pending = " ".join(self.label.split()).rstrip(":")
        elif self.value_depth:
            self.value_depth -= 1
            if not self.value_depth:
                if self.pending in self.fields:
                    raise CzsSourceError("CZS version field is duplicated")
                self.fields[self.pending] = " ".join(self.value.split())
                self.pending = ""


def parse_display_page(body: bytes, page_id: int, version: CzsVersion) -> ModtarDisplay:
    if not 1 <= len(body) <= 256 * 1024 or not 1 <= page_id <= 2_147_483_647:
        raise CzsSourceError("CZS display version page is outside its bound")
    try:
        text = body.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise CzsSourceError("CZS display version encoding is invalid") from None
    if f"/WersjeSlownikow/Index/{page_id}?kod=MODTAR" not in text:
        # The version info page links back to the exact dictionary page.
        if f"/ModyfikatoryTaryfyJgp/SlownikModyfikatorowTaryfJgp/{page_id}" not in text:
            raise CzsSourceError("CZS version-info page identity is missing")
    parser = _VersionFields()
    parser.feed(text)
    parser.close()
    fields = parser.fields
    if fields.get("Kod słownika") != "MODTAR":
        raise CzsSourceError("CZS display page dictionary code conflicts")
    raw_label = fields.get("Nr wersji", "")
    raw_generation = fields.get("Nr wersji technicznej", "")
    published = fields.get("Data publikacji", "")
    effective = fields.get("Obowiązuje od", "")
    if re.fullmatch(r"20[0-9]{2}[0-9]{1,4}", raw_label) is None or (
        version.year is None or not raw_label.startswith(str(version.year))
    ):
        raise CzsSourceError("CZS MODTAR display label is invalid")
    if (
        not raw_generation.isascii()
        or not raw_generation.isdecimal()
        or int(raw_generation) != version.generation
    ):
        raise CzsSourceError("CZS display label conflicts with SOAP generation")
    for value in (published, effective):
        if re.fullmatch(r"20[0-9]{2}-[0-9]{2}-[0-9]{2}", value) is None:
            raise CzsSourceError("CZS display date is invalid")
    if published != version.generated_at[:10]:
        raise CzsSourceError("CZS display publication date conflicts with generation")
    expected_link = f"/WersjeSlownikow/PobierzXml?nrGen={version.generation}&amp;kodSlownika=MODTAR&amp;idWersji={page_id}"
    if expected_link not in text:
        raise CzsSourceError("CZS display page does not link the pinned XML")
    return ModtarDisplay(page_id, raw_label, version.generation, published, effective)


def fetch_display_version(
    client: httpx.Client, version: CzsVersion
) -> tuple[ModtarDisplay, CzsEvidence, CzsEvidence]:
    if (
        version.code != "MODTAR"
        or version.year is None
        or version.grouper_type != "SZP"
    ):
        raise CzsSourceError("CZS MODTAR display requires a year and SZP selection")
    request = client.build_request("GET", _ENTRY)
    if "cookie" in request.headers or "authorization" in request.headers:
        raise CzsSourceError("CZS display request contains credentials")
    with closing(client.send(request, stream=True)) as response:
        if response.status_code != 302:
            raise CzsSourceError("CZS MODTAR current version redirect changed")
        location = cast(str, response.headers.get("location", ""))
        match = re.fullmatch(
            r"/ModyfikatoryTaryfyJgp/SlownikModyfikatorowTaryfJgp/([1-9][0-9]{0,9})",
            location,
        )
        if match is None:
            raise CzsSourceError("CZS MODTAR redirect target is not allowlisted")
        parts: list[bytes] = []
        size = 0
        for chunk in response.iter_bytes(4096):
            size += len(chunk)
            if size > 4096:
                raise CzsSourceError("CZS MODTAR redirect exceeds its bound")
            parts.append(chunk)
        body = b"".join(parts)
        if not body:
            raise CzsSourceError("CZS MODTAR redirect has no raw evidence")
        redirect = CzsEvidence(
            body, "text/html", {"location": location}, hashlib.sha256(body).hexdigest()
        )
    page_id = int(match[1])
    page = fetch_czs_page(client, f"{_BASE}/WersjeSlownikow/Index/{page_id}?kod=MODTAR")
    if page.media_type != "text/html":
        raise CzsSourceError("CZS version-info response is not HTML")
    display = parse_display_page(page.body, page_id, version)
    return display, redirect, page
