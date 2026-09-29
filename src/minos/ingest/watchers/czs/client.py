"""Bounded anonymous CZS SOAP discovery and pinned XML retrieval."""

import hashlib
import re
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from email.parser import BytesParser
from email.policy import default
from typing import Literal, cast, final, override

# ElementTree only constructs our request; defusedxml parses source responses.
from xml.etree import ElementTree as ET  # nosec B405

import httpx
from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import fromstring as safe_fromstring

_SOAP_ENDPOINT = (
    "https://slowniki-ws.nfz.gov.pl/ws-broker-czs/services/ServiceBrokerAnonymous"
)
_XML_ENDPOINT = "https://slowniki.nfz.gov.pl/WersjeSlownikow/PobierzXml"
_REQUEST_NAMESPACE = (
    "https://czs.nfz.gov.pl/ws/broker/nfz/xml/czs/get-list-slowniki-request/v1.0"
)
_RESPONSE_NAMESPACE = (
    "https://czs.nfz.gov.pl/ws/broker/nfz/xml/czs/get-list-slowniki-response/v1.0"
)
_SOAP_NAMESPACE = "http://schemas.xmlsoap.org/soap/envelope/"
_BROKER_NAMESPACE = "http://xml.kamsoft.pl/ws/broker"
_LOCATION_NAMESPACE = "http://xml.kamsoft.pl/ws/common"
_MAX_SOAP_BYTES = 128 * 1024
_MAX_XML_BYTES = 16 * 1024 * 1024


class CzsSourceError(ValueError):
    """A CZS reply is incomplete or outside the accepted publication contract."""


class CzsRetryableError(RuntimeError):
    """The official CZS endpoint is temporarily unavailable."""


@dataclass(frozen=True, slots=True)
class CzsVersion:
    code: Literal["ICD9", "MODTAR"]
    generation: int
    generated_at: str
    year: int | None
    grouper_type: str | None


@dataclass(frozen=True, slots=True)
class CzsEvidence:
    body: bytes
    media_type: str
    headers: dict[str, str]
    sha256: str


@final
class _AnonymousCzsTransport(httpx.BaseTransport):
    def __init__(self, wrapped: httpx.BaseTransport) -> None:
        self._wrapped = wrapped

    @override
    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.scheme != "https" or request.url.host not in (
            "slowniki.nfz.gov.pl",
            "slowniki-ws.nfz.gov.pl",
        ):
            raise CzsSourceError("CZS request host is not allowlisted")
        if "cookie" in request.headers or "authorization" in request.headers:
            raise CzsSourceError("CZS request must be anonymous")
        response = self._wrapped.handle_request(request)
        if "set-cookie" in response.headers:
            del response.headers["set-cookie"]
        return response

    @override
    def close(self) -> None:
        self._wrapped.close()


def create_czs_client(*, transport: httpx.BaseTransport | None = None) -> httpx.Client:
    return httpx.Client(
        auth=None,
        cookies={},
        trust_env=False,
        follow_redirects=False,
        timeout=httpx.Timeout(30.0, connect=5.0),
        headers={
            "Accept-Encoding": "identity",
            "User-Agent": "minos-health/0.0.1 czs-watcher",
        },
        transport=_AnonymousCzsTransport(
            transport if transport is not None else httpx.HTTPTransport(trust_env=False)
        ),
    )


def discover_version(
    client: httpx.Client,
    code: Literal["ICD9", "MODTAR"],
    *,
    year: int | None = None,
    grouper_type: str | None = None,
) -> tuple[CzsVersion, CzsEvidence]:
    if code == "ICD9" and (year is not None or grouper_type is not None):
        raise CzsSourceError("ICD9 discovery must not include a grouper or year")
    if code == "MODTAR" and (
        type(year) is not int or not 2020 <= year <= 2100 or grouper_type != "SZP"
    ):
        raise CzsSourceError("MODTAR discovery requires a supported year and SZP")
    criteria: dict[str, str] = {"kod-slownika": code}
    if year is not None:
        criteria["rok"] = str(year)
    if grouper_type is not None:
        criteria["typ-grupera"] = grouper_type
    document = ET.Element(f"{{{_SOAP_NAMESPACE}}}Envelope")
    body = ET.SubElement(document, f"{{{_SOAP_NAMESPACE}}}Body")
    service = ET.SubElement(body, f"{{{_BROKER_NAMESPACE}}}executeService")
    location = ET.SubElement(service, f"{{{_LOCATION_NAMESPACE}}}location")
    for name, value in (
        ("namespace", "https://nfz.gov.pl/ws/broker/nfz/cen/czs"),
        ("localname", "getListSlowniki"),
        ("version", "1.0"),
    ):
        ET.SubElement(location, f"{{{_LOCATION_NAMESPACE}}}{name}").text = value
    ET.SubElement(service, f"{{{_BROKER_NAMESPACE}}}date").text = datetime.now(
        UTC
    ).isoformat()
    payload = ET.SubElement(service, f"{{{_BROKER_NAMESPACE}}}payload")
    textload = ET.SubElement(payload, f"{{{_BROKER_NAMESPACE}}}textload")
    message = ET.SubElement(
        textload,
        f"{{{_REQUEST_NAMESPACE}}}komunikat",
        {"nazwa-sys": "Minos", "wersja-sys": "0.0.1"},
    )
    _ = ET.SubElement(message, f"{{{_REQUEST_NAMESPACE}}}kryteria", criteria)
    request = cast(bytes, ET.tostring(document, encoding="utf-8", xml_declaration=True))
    evidence = _fetch(
        client,
        "POST",
        _SOAP_ENDPOINT,
        _MAX_SOAP_BYTES,
        content=request,
        headers={
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": "executeService",
        },
    )
    version = _parse_soap_version(evidence, code, year, grouper_type)
    return version, evidence


def fetch_pinned_xml(client: httpx.Client, version: CzsVersion) -> CzsEvidence:
    if version.code not in ("ICD9", "MODTAR") or not 1 <= version.generation <= 999_999:
        raise CzsSourceError("CZS pinned version is invalid")
    url = f"{_XML_ENDPOINT}?kodSlownika={version.code}&nrGen={version.generation}"
    evidence = _fetch(client, "GET", url, _MAX_XML_BYTES)
    if evidence.media_type not in ("text/xml", "application/xml"):
        raise CzsSourceError("CZS pinned dictionary is not XML")
    if b"<!DOCTYPE" in evidence.body.upper() or b"<!ENTITY" in evidence.body.upper():
        raise CzsSourceError("CZS dictionary contains an XML entity declaration")
    try:
        root = safe_fromstring(evidence.body)
    except ET.ParseError, DefusedXmlException:
        raise CzsSourceError("CZS pinned dictionary XML is invalid") from None
    if root.tag != "komunikat":
        raise CzsSourceError("CZS pinned dictionary XML has an unexpected root")
    if version.code == "ICD9":
        if (
            root.attrib.get("typ") != "ICD9"
            or root.attrib.get("nr_gen") != str(version.generation)
            or re.fullmatch(
                r"[1-9][0-9]{0,2}\.[0-9]{1,3}", root.attrib.get("symbol", "")
            )
            is None
            or len(root) < 1
        ):
            raise CzsSourceError("CZS ICD9 version or content does not match discovery")
    elif (
        root.attrib.get("typ") != "CZS-PUB"
        or root.attrib.get("kod_slow") != "MODTAR"
        or root.attrib.get("nr-gen") != str(version.generation)
        or root.attrib.get("rok") != str(version.year)
        or len(root) != 1
        or root[0].tag != "gruper"
        or root[0].attrib.get("typ_grupera") != version.grouper_type
        or len(root[0]) < 1
    ):
        raise CzsSourceError(
            "CZS MODTAR year, grouper or content does not match discovery"
        )
    if root.attrib.get("czas_gen", root.attrib.get("data_gen")) != version.generated_at:
        raise CzsSourceError("CZS pinned dictionary generation timestamp conflicts")
    return evidence


def fetch_czs_page(client: httpx.Client, url: str) -> CzsEvidence:
    if (
        re.fullmatch(
            r"https://slowniki\.nfz\.gov\.pl/WersjeSlownikow/Index/[1-9][0-9]{0,9}\?kod=MODTAR",
            url,
        )
        is None
    ):
        raise CzsSourceError("CZS version page URL is invalid")
    return _fetch(client, "GET", url, 256 * 1024)


def _fetch(
    client: httpx.Client,
    method: Literal["GET", "POST"],
    url: str,
    maximum_bytes: int,
    *,
    content: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> CzsEvidence:
    request = client.build_request(method, url, content=content, headers=headers)
    if "cookie" in request.headers or "authorization" in request.headers:
        raise CzsSourceError("CZS request contains credentials")
    with closing(client.send(request, stream=True)) as response:
        if response.status_code in (408, 429, 500, 502, 503, 504):
            raise CzsRetryableError("CZS request temporarily failed")
        if response.status_code != 200:
            raise CzsSourceError("CZS request was rejected")
        if cast(str, response.headers.get("xml_status", "")).upper() == "NO_CONTENT":
            raise CzsSourceError("CZS returned NO_CONTENT for a requested version")
        media_type = (
            cast(str, response.headers.get("content-type", "")).split(";", 1)[0].lower()
        )
        length = cast(str | None, response.headers.get("content-length"))
        if length is not None and (
            not length.isascii()
            or not length.isdecimal()
            or int(length) > maximum_bytes
        ):
            raise CzsSourceError("CZS response length is invalid")
        if (
            cast(str, response.headers.get("content-encoding", "identity")).lower()
            != "identity"
        ):
            raise CzsSourceError("CZS response encoding is unsupported")
        parts: list[bytes] = []
        total = 0
        for chunk in response.iter_bytes(65_536):
            total += len(chunk)
            if total > maximum_bytes:
                raise CzsSourceError("CZS response exceeds its byte limit")
            parts.append(chunk)
        if total == 0 or (length is not None and total != int(length)):
            raise CzsSourceError("CZS response is empty or truncated")
        selected: dict[str, str] = {}
        for key in (
            "content-type",
            "content-length",
            "last-modified",
            "etag",
            "xml_status",
        ):
            value = cast(str | None, response.headers.get(key))
            if value is not None:
                if len(value) > 256 or any(
                    ord(char) < 32 or ord(char) == 127 for char in value
                ):
                    raise CzsSourceError("CZS response header is invalid")
                selected[key] = value
        body = b"".join(parts)
        return CzsEvidence(body, media_type, selected, hashlib.sha256(body).hexdigest())


def _parse_soap_version(
    evidence: CzsEvidence,
    code: Literal["ICD9", "MODTAR"],
    year: int | None,
    grouper_type: str | None,
) -> CzsVersion:
    if evidence.media_type != "multipart/related":
        raise CzsSourceError("CZS version reply is not SOAP multipart")
    media = evidence.headers.get("content-type", "")
    if not media.isascii():
        raise CzsSourceError("CZS SOAP media type is invalid")
    multipart = BytesParser(policy=default).parsebytes(
        b"MIME-Version: 1.0\r\nContent-Type: "
        + media.encode("ascii")
        + b"\r\n\r\n"
        + evidence.body
    )
    parts = list(multipart.iter_parts())
    if len(parts) != 1 or parts[0].get_content_type() != "application/xop+xml":
        raise CzsSourceError("CZS SOAP response has an unsupported attachment")
    xml = parts[0].get_payload(decode=True)
    if (
        not isinstance(xml, bytes)
        or b"<!DOCTYPE" in xml.upper()
        or b"<!ENTITY" in xml.upper()
    ):
        raise CzsSourceError("CZS SOAP XML is invalid")
    try:
        root = safe_fromstring(xml)
    except ET.ParseError, DefusedXmlException:
        raise CzsSourceError("CZS SOAP XML is invalid") from None
    body = root.find(f"{{{_SOAP_NAMESPACE}}}Body")
    if (
        body is None
        or len(body) != 1
        or body[0].tag != f"{{{_BROKER_NAMESPACE}}}executeServiceReturn"
    ):
        raise CzsSourceError("CZS SOAP request did not return a version response")
    returned = body[0]
    location = returned.find(f"{{{_LOCATION_NAMESPACE}}}location")
    if location is None or (
        location.findtext(f"{{{_LOCATION_NAMESPACE}}}namespace")
        != "https://nfz.gov.pl/ws/broker/nfz/cen/czs"
        or location.findtext(f"{{{_LOCATION_NAMESPACE}}}localname") != "getListSlowniki"
        or location.findtext(f"{{{_LOCATION_NAMESPACE}}}version") != "1.0"
    ):
        raise CzsSourceError("CZS SOAP service identity conflicts")
    textload = returned.find(
        f"{{{_BROKER_NAMESPACE}}}payload/{{{_BROKER_NAMESPACE}}}textload"
    )
    if textload is None or len(textload) != 1:
        raise CzsSourceError("CZS SOAP version list is missing")
    document = textload[0]
    if document.tag != f"{{{_RESPONSE_NAMESPACE}}}komunikat":
        raise CzsSourceError("CZS version response namespace changed")
    results = document.find(f"{{{_RESPONSE_NAMESPACE}}}wyniki")
    if results is None or len(results) != 1:
        raise CzsSourceError("CZS version selector is empty or conflicting")
    item = results[0]
    attrs = item.attrib
    if item.tag != f"{{{_RESPONSE_NAMESPACE}}}wersja-slownika" or (
        attrs.get("fl-obow") != "T"
        or attrs.get("kod-slownika") != code
        or attrs.get("rok") != (str(year) if year is not None else None)
        or attrs.get("typ-grupera") != grouper_type
    ):
        raise CzsSourceError("CZS version metadata does not match its criteria")
    raw_generation = attrs.get("nr-gen", "")
    if not raw_generation.isascii() or not raw_generation.isdecimal():
        raise CzsSourceError("CZS generation number is invalid")
    generation = int(raw_generation)
    generated_at = attrs.get("data-gen", "")
    if (
        not 1 <= generation <= 999_999
        or re.fullmatch(
            r"20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}",
            generated_at,
        )
        is None
    ):
        raise CzsSourceError("CZS generation timestamp or number is invalid")
    return CzsVersion(code, generation, generated_at, year, grouper_type)
