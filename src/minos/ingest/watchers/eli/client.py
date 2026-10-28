"""Read-only bounded Sejm ELI act metadata for the pinned grouping dictionaries."""

import re
from contextlib import closing
from dataclasses import dataclass
from typing import ClassVar, cast, final, override

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

_HOST = "api.sejm.gov.pl"
_BASE = f"https://{_HOST}/eli/acts/"
_MAX_BYTES = 256 * 1024
_ELI = re.compile(r"DU/(19|20)[0-9]{2}/[1-9][0-9]{0,4}")
# Referenced acts may also be Monitor Polski entries.
_REFERENCE = re.compile(r"(DU|MP)/(19|20)[0-9]{2}/[1-9][0-9]{0,4}")
# Reference sections of the base act compared with the reviewed sets.
AMENDMENTS = "Akty zmieniające"
CONSOLIDATED = "Inf. o tekście jednolitym"
# Sections that are empty for a reviewed pin: any entry, on either act, is a
# finding. A repeal is listed here before its effective date flips inForce.
UNEXPECTED = {
    "Uchylenia wynikające z": "repeal",
    "Sprostowanie": "correction",
    "Orzeczenie TK": "tribunal",
}
MAX_FINDINGS = 32


class EliSourceError(ValueError):
    """The ELI answer is not the expected act metadata."""


class EliRetryableError(RuntimeError):
    """The ELI API is temporarily unavailable."""


@dataclass(frozen=True, slots=True)
class WatchedDictionary:
    """A reviewed pin: a consolidated text and the base act it consolidates.

    New amendments and newer consolidated texts appear on the base act, so the
    reviewed reference sets are compared there; the pinned act gives its status.
    """

    name: str
    pinned: str
    base: str
    amendments: frozenset[str]
    consolidated: frozenset[str]


# Reviewed against the authentic PDFs in manifests/sources/grouping-dictionaries.json
# (2026-10-08); the 2026 amendments of DU/2019/1207 leave annex 4 unchanged.
WATCHED = (
    WatchedDictionary(
        "specialties",
        "DU/2019/173",
        "DU/2012/594",
        frozenset({"DU/2017/999"}),
        frozenset({"DU/2019/173"}),
    ),
    WatchedDictionary(
        "discharge_modes",
        "DU/2025/1588",
        "DU/2019/1207",
        frozenset(
            {
                "DU/2019/2532",
                "DU/2020/1586",
                "DU/2022/749",
                "DU/2022/1497",
                "DU/2022/2262",
                "DU/2023/1245",
                "DU/2024/1469",
                "DU/2025/205",
                "DU/2025/985",
                "DU/2026/270",
                "DU/2026/822",
                "DU/2026/1113",
            }
        ),
        frozenset({"DU/2022/434", "DU/2024/610", "DU/2025/1588"}),
    ),
)


class _Reference(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", strict=True)
    # Opaque here: only the sections the watch reads are checked, in ids().
    id: str = Field(min_length=1, max_length=64)


class EliAct(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", strict=True)
    ELI: str = Field(pattern="^" + _ELI.pattern + "$")
    status: str = Field(min_length=1, max_length=128)
    inForce: str = Field(pattern=r"^[A-Z_]{1,32}$")  # noqa: N815 - ELI field name
    changeDate: str | None = Field(default=None, max_length=32)  # noqa: N815
    references: dict[str, list[_Reference]] = Field(default_factory=dict, max_length=64)

    def ids(self, section: str) -> frozenset[str]:
        items = self.references.get(section, [])
        if len(items) > 256:
            raise EliSourceError("ELI reference section exceeds its bound")
        if any(_REFERENCE.fullmatch(item.id) is None for item in items):
            raise EliSourceError("ELI reference identifier is invalid")
        return frozenset(item.id for item in items)


@dataclass(frozen=True, slots=True)
class EliResponse:
    url: str
    body: bytes
    headers: dict[str, str]
    act: EliAct


@final
class _AnonymousTransport(httpx.BaseTransport):
    def __init__(self, wrapped: httpx.BaseTransport) -> None:
        self._wrapped = wrapped

    @override
    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.scheme != "https" or request.url.host != _HOST:
            raise EliSourceError("ELI host is not allowlisted")
        if "cookie" in request.headers or "authorization" in request.headers:
            raise EliSourceError("ELI request must be anonymous")
        response = self._wrapped.handle_request(request)
        if "set-cookie" in response.headers:
            del response.headers["set-cookie"]
        return response

    @override
    def close(self) -> None:
        self._wrapped.close()


def create_eli_client(*, transport: httpx.BaseTransport | None = None) -> httpx.Client:
    return httpx.Client(
        trust_env=False,
        follow_redirects=False,
        timeout=httpx.Timeout(30.0, connect=5.0),
        headers={
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            "User-Agent": "minos-health/0.0.1 eli-watcher",
        },
        transport=_AnonymousTransport(
            transport if transport is not None else httpx.HTTPTransport(trust_env=False)
        ),
    )


def fetch_act(client: httpx.Client, eli: str) -> EliResponse:
    if _ELI.fullmatch(eli) is None:
        raise EliSourceError("ELI identifier is invalid")
    url = _BASE + eli
    with closing(
        client.send(client.build_request("GET", url), stream=True)
    ) as response:
        if response.status_code in (408, 429, 500, 502, 503, 504):
            raise EliRetryableError("ELI request temporarily failed")
        if response.status_code != 200:
            raise EliSourceError("ELI request was rejected")
        # A firewall in front of the API answers HTTP 200 with an HTML page.
        media = cast(str, response.headers.get("content-type", ""))
        if media.split(";", 1)[0].strip().lower() != "application/json":
            raise EliSourceError("ELI answer is not JSON")
        if (
            cast(str, response.headers.get("content-encoding", "identity")).lower()
            != "identity"
        ):
            raise EliSourceError("ELI response encoding is unsupported")
        parts: list[bytes] = []
        size = 0
        for part in response.iter_bytes(65_536):
            size += len(part)
            if size > _MAX_BYTES:
                raise EliSourceError("ELI response exceeds its byte limit")
            parts.append(part)
        headers: dict[str, str] = {}
        for key in ("content-type", "content-length", "last-modified", "etag"):
            value = cast(str | None, response.headers.get(key))
            if value is not None:
                if len(value) > 256 or any(
                    ord(char) < 32 or ord(char) == 127 for char in value
                ):
                    raise EliSourceError("ELI selected header is invalid")
                headers[key] = value
    body = b"".join(parts)
    try:
        act = EliAct.model_validate_json(body)
    except ValidationError:
        raise EliSourceError("ELI act metadata is invalid") from None
    if act.ELI != eli:
        raise EliSourceError("ELI answer names another act")
    return EliResponse(url, body, headers, act)


def findings(watched: WatchedDictionary, pinned: EliAct, base: EliAct) -> list[str]:
    """Differences from the reviewed pin; an empty list is a quiet observation."""
    if pinned.ELI != watched.pinned or base.ELI != watched.base:
        raise EliSourceError("ELI answers do not match the watched acts")
    consolidated = base.ids(CONSOLIDATED)
    if watched.pinned not in consolidated:
        # The base act must still list the pinned text; otherwise the section moved.
        raise EliSourceError("ELI base act no longer lists the pinned text")
    amendments = base.ids(AMENDMENTS)
    result = [f"amendment:{item}" for item in sorted(amendments - watched.amendments)]
    result += [
        f"consolidated_text:{item}"
        for item in sorted(consolidated - watched.consolidated)
    ]
    result += [
        f"reference_removed:{item}"
        for item in sorted(
            (watched.amendments - amendments) | (watched.consolidated - consolidated)
        )
    ]
    for role, act in (("pinned", pinned), ("base", base)):
        for section, label in UNEXPECTED.items():
            result += [f"{label}:{role}:{item}" for item in sorted(act.ids(section))]
        if act.inForce != "IN_FORCE":
            result.append(f"not_in_force:{role}:{act.inForce}")
    if len(result) > MAX_FINDINGS:
        # So many differences at once means the reference sections moved.
        raise EliSourceError("ELI differences exceed their bound")
    return result
