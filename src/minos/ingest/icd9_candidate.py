"""Derive a shadow ICD-9 dataset candidate from a newly archived CZS generation.

A CZS generation republishes the whole dictionary with per-code procedure
dates, and NFZ backdates some of them. The new generation is added beside the
previous one (#141 phase B2) and governs from its earliest changed code date,
capped at the publication day, so answers before that day keep using the
previous generation. Backdating beyond the owner's bound, an edition change or
a missing same-version workbook refuses the candidate.
"""

import json
import string
import tempfile
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Annotated, ClassVar, Literal, Self
from xml.parsers import expat

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from minos.domain.jgp_ruleset import TimeZoneUnavailable
from minos.domain.reference import ReferenceNode, SourceError
from minos.ingest.archive_source import ArchivedBlob, verified_source
from minos.ingest.icd9 import parse_icd9_xml
from minos.ingest.icd9_workbook import parse_icd9_workbook
from minos.ingest.icd10_candidate import CandidateRejected, count_release
from minos.ingest.manifest_types import SourceText
from minos.ingest.reference_manifest import (
    DatasetManifest,
    ExpectedCounts,
    SourceManifest,
)

_MAX_POLICY_BYTES = 64 * 1024
_HEADER_BYTES = 64 * 1024
_CZS_URL = (
    "https://slowniki.nfz.gov.pl/WersjeSlownikow/PobierzXml?kodSlownika=ICD9&nrGen="
)
_WORKBOOK_PREFIX = (
    "https://www.nfz.gov.pl/download/gfx/nfz/pl/defaultstronaopisowa/782/1/2/"
)
_NOTICE_FIELDS = frozenset({"version", "generation", "czs_sha256"})
# Only for parsing: the parser checks the header against the manifest, and the
# real counts come from that same pass.
_PROVISIONAL = ExpectedCounts(nodes=1, relations=0, concepts=1, descriptions=1)

type _Notices = Annotated[tuple[SourceText, ...], Field(min_length=1, max_length=16)]


class CzsIcd9Policy(BaseModel):
    """The owner's class-level approval for automatic CZS ICD-9 generations."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", frozen=True, strict=True
    )

    schema_version: Literal[1]
    source_class: Literal["czs-icd9"]
    approved_on: date
    approval: SourceText
    publisher: SourceText
    # ICD-9 PL edition (`edycja`); a new edition goes to the owner.
    edition: Annotated[str, Field(pattern=r"^[1-9][0-9]{0,2}$")]
    worksheet: Literal["ICD-9_PL ", "ICD-9_PL"]
    license: SourceText
    # Templates; only {version}, {generation} and {czs_sha256} are filled in.
    czs_notices: _Notices
    workbook_notices: _Notices
    max_backdating_days: int = Field(ge=0, le=366)
    max_removed_code_ratio: float = Field(ge=0, le=0.2)
    max_concept_count_change_ratio: float = Field(ge=0, le=0.2)

    @model_validator(mode="after")
    def validate_templates(self) -> Self:
        for notice in (*self.czs_notices, *self.workbook_notices):
            for _, name, spec, conversion in string.Formatter().parse(notice):
                if name is not None and (
                    name not in _NOTICE_FIELDS or spec or conversion is not None
                ):
                    raise ValueError("notice template uses an unsupported field")
        return self

    @classmethod
    def load(cls, path: Path) -> Self:
        with path.open("rb") as source:
            content = source.read(_MAX_POLICY_BYTES + 1)
        if len(content) > _MAX_POLICY_BYTES:
            raise ValueError("automatic activation policy exceeds its byte bound")
        return cls.model_validate_json(content)


@dataclass(frozen=True, slots=True)
class NewWorkbook:
    """A newly archived same-version NFZ workbook for a changed CZS version."""

    sha256: str
    source_url: str


@dataclass(frozen=True, slots=True)
class _Header:
    version: str
    edition: str
    generation: int
    published_on: date


def propose_icd9_release(
    base: DatasetManifest,
    archive_root: Path,
    *,
    czs_sha256: str,
    fetched_at: datetime,
    policy: CzsIcd9Policy,
    workbook: NewWorkbook | None = None,
) -> DatasetManifest:
    """Return the base dataset plus the archived generation, or refuse it."""
    previous = _latest_czs(base)
    try:
        product = ArchivedBlob.at(archive_root, czs_sha256)
    except SourceError:
        raise CandidateRejected("bytes", "product_not_archived") from None
    if any(source.sha256 == product.sha256 for source in base.sources):
        raise CandidateRejected("history", "release_already_present")
    header = _read_header(archive_root, product)
    if header.edition != policy.edition:
        raise CandidateRejected("licence", "edition_changed")
    if header.generation <= _generation(previous):
        raise CandidateRejected("history", "not_newer_than_live_release")
    release_id = (
        f"icd9-czs-{header.version.replace('.', '-')}-gen-{header.generation}"
        + f"-{product.sha256[:12]}"
    )
    fields = {
        "version": header.version,
        "generation": str(header.generation),
        "czs_sha256": product.sha256,
    }
    try:
        provisional = SourceManifest(
            release_id=release_id,
            system="icd9-pl",
            origin="czs",
            source_version=header.version,
            source_file_id=f"ICD9:{header.generation}",
            source_url=_CZS_URL + str(header.generation),
            publisher=policy.publisher,
            fetched_at=fetched_at,
            source_created_at=None,
            valid_from=None,
            valid_to=None,
            sha256=product.sha256,
            size_bytes=product.size_bytes,
            license=policy.license,
            notices=tuple(notice.format(**fields) for notice in policy.czs_notices),
            expected=_PROVISIONAL,
        )
    except ValidationError:
        raise CandidateRejected("parse", "candidate_manifest_invalid") from None
    counts = count_release(
        archive_root,
        product,
        lambda snapshot: parse_icd9_xml(snapshot, provisional),
    )
    entries = _entries(archive_root, provisional)
    effective = _effective_day(
        _entries(archive_root, previous), entries, header.published_on
    )
    if (header.published_on - effective).days > policy.max_backdating_days:
        raise CandidateRejected("history", "retroactive_amendment_exceeds_bound")
    valid_from = _polish_midnight(effective)
    if previous.valid_from is not None and valid_from <= previous.valid_from:
        # Replacing the live generation over its own period needs the owner.
        raise CandidateRejected("history", "effective_day_not_after_live_release")
    try:
        release = provisional.model_copy(
            update={"valid_from": valid_from, "expected": counts}
        )
        companion = _companion(
            base, archive_root, previous, release, fields, policy, workbook, fetched_at
        )
        return _with_releases(base, previous, release, companion)
    except ValidationError:
        raise CandidateRejected("parse", "candidate_manifest_invalid") from None


def _latest_czs(base: DatasetManifest) -> SourceManifest:
    open_releases = [
        source
        for source in base.sources
        if (source.system, source.origin) == ("icd9-pl", "czs")
        and source.context_for is None
        and source.valid_to is None
    ]
    if len(open_releases) != 1:
        raise CandidateRejected("history", "live_icd9_release_is_ambiguous")
    return open_releases[0]


def _generation(source: SourceManifest) -> int:
    prefix, _, generation = source.source_file_id.partition(":")
    if prefix != "ICD9" or not generation.isdecimal():
        raise CandidateRejected("history", "live_icd9_release_is_ambiguous")
    return int(generation)


class _HeaderFound(Exception):
    pass


def _read_header(archive_root: Path, product: ArchivedBlob) -> _Header:
    """Read only the root attributes; the full parse later checks them again."""
    found: dict[str, str] = {}

    def start(name: str, attributes: dict[str, str]) -> None:
        found.update(attributes)
        found["\x00root"] = name
        raise _HeaderFound

    def reject(*_arguments: object) -> None:
        raise CandidateRejected("parse", "product_rejected_by_parser")

    parser = expat.ParserCreate()
    parser.StartElementHandler = start
    parser.StartDoctypeDeclHandler = reject
    parser.EntityDeclHandler = reject
    try:
        with tempfile.TemporaryDirectory(prefix=".minos-candidate-") as staging:
            with verified_source(
                archive_root, product, staging_directory=Path(staging)
            ) as snapshot:
                _ = parser.Parse(snapshot.read(_HEADER_BYTES), False)
    except _HeaderFound:
        pass
    except SourceError, expat.ExpatError:
        raise CandidateRejected("parse", "product_rejected_by_parser") from None
    try:
        if found.get("\x00root") != "komunikat" or found.get("typ") != "ICD9":
            raise ValueError("not a CZS ICD-9 dictionary")
        version = found["symbol"]
        generation = int(found["nr_gen"])
        # CZS timestamps carry no zone; they are Polish local time.
        published_on = datetime.fromisoformat(found["czas_pub"]).date()
        if not version.replace(".", "", 1).isdecimal() or generation < 1:
            raise ValueError("invalid CZS header")
        return _Header(version, found["edycja"], generation, published_on)
    except KeyError, ValueError:
        raise CandidateRejected("parse", "product_rejected_by_parser") from None


type _Entry = tuple[str, tuple[tuple[str, str], ...]]


def _entries(archive_root: Path, source: SourceManifest) -> dict[str, _Entry]:
    entries: dict[str, _Entry] = {}
    try:
        with tempfile.TemporaryDirectory(prefix=".minos-candidate-") as staging:
            with verified_source(
                archive_root, source, staging_directory=Path(staging)
            ) as snapshot:
                for event in parse_icd9_xml(snapshot, source):
                    if isinstance(event, ReferenceNode):
                        entries[event.source_code] = (
                            event.source_text,
                            event.attributes,
                        )
    except SourceError:
        raise CandidateRejected("parse", "product_rejected_by_parser") from None
    return entries


def _effective_day(
    previous: dict[str, _Entry], current: dict[str, _Entry], published_on: date
) -> date:
    """The earliest day any change applies to, never later than publication.

    An added code applies from its `data_od`; changed procedure dates apply
    from the earlier of the old and new bound. A title, status or annotation
    change carries no date, so it applies from publication.
    """
    if previous == current:
        raise CandidateRejected("history", "release_content_unchanged")
    days = [published_on]
    for code, entry in current.items():
        before = previous.get(code)
        dates = dict(entry[1])
        if before is None:
            days.extend(_day(dates.get("data_od")))
            continue
        old = dict(before[1])
        for bound in ("data_od", "data_do"):
            if old.get(bound) != dates.get(bound):
                days.extend(_day(old.get(bound)) + _day(dates.get(bound)))
    return min(days)


def _day(value: str | None) -> list[date]:
    return [] if value is None else [date.fromisoformat(value)]


def _polish_midnight(day: date) -> datetime:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        zone = ZoneInfo("Europe/Warsaw")
    except ZoneInfoNotFoundError:
        raise TimeZoneUnavailable("Europe/Warsaw time zone data is missing") from None
    return datetime.combine(day, time(0), zone).astimezone(UTC)


def _companion(
    base: DatasetManifest,
    archive_root: Path,
    previous: SourceManifest,
    release: SourceManifest,
    fields: dict[str, str],
    policy: CzsIcd9Policy,
    workbook: NewWorkbook | None,
    fetched_at: datetime,
) -> SourceManifest:
    """The same-version workbook that gives the new generation its hierarchy."""
    live = [
        source for source in base.sources if source.context_for == previous.release_id
    ]
    if workbook is None:
        # An unchanged version reuses the live workbook bytes.
        if release.source_version != previous.source_version or len(live) != 1:
            raise CandidateRejected("bytes", "workbook_companion_missing")
        reused = live[0]
        blob = ArchivedBlob(reused.sha256, reused.size_bytes)
        source_url, file_id, fetched = (
            reused.source_url,
            reused.source_file_id,
            reused.fetched_at,
        )
    else:
        try:
            blob = ArchivedBlob.at(archive_root, workbook.sha256)
        except SourceError:
            raise CandidateRejected("bytes", "workbook_not_archived") from None
        if not workbook.source_url.startswith(_WORKBOOK_PREFIX):
            raise CandidateRejected("bytes", "workbook_url_unsupported")
        source_url = workbook.source_url
        file_id = source_url[len(_WORKBOOK_PREFIX) :] + "#" + policy.worksheet
        fetched = fetched_at
    version = release.source_version.replace(".", "-")
    generation = release.source_file_id.removeprefix("ICD9:")
    companion = SourceManifest(
        release_id=f"icd9-nfz-{version}-gen-{generation}-main-{blob.sha256[:12]}",
        system="icd9-pl",
        origin="nfz-workbook",
        source_version=release.source_version,
        source_file_id=file_id,
        source_url=source_url,
        publisher=policy.publisher,
        fetched_at=fetched,
        source_created_at=None,
        valid_from=None,
        valid_to=None,
        sha256=blob.sha256,
        size_bytes=blob.size_bytes,
        license=policy.license,
        notices=tuple(notice.format(**fields) for notice in policy.workbook_notices),
        expected=_PROVISIONAL,
        context_for=release.release_id,
    )
    counts = count_release(
        archive_root,
        blob,
        lambda snapshot: parse_icd9_workbook(snapshot, companion),
        code="workbook_rejected_by_parser",
    )
    return companion.model_copy(update={"expected": counts})


def _with_releases(
    base: DatasetManifest,
    previous: SourceManifest,
    release: SourceManifest,
    companion: SourceManifest,
) -> DatasetManifest:
    closed = previous.model_copy(update={"valid_to": release.valid_from})
    sources = tuple(
        closed if source.release_id == previous.release_id else source
        for source in base.sources
    )
    # As for ICD-10, synonyms pinned to the previous generation become unscoped
    # and the build requires their wording in every searchable ICD-9 release.
    synonyms = tuple(
        item.model_copy(update={"release_id": None})
        if item.system == "icd9-pl" and item.release_id == previous.release_id
        else item
        for item in base.search_synonyms
    )
    value = base.model_dump(mode="json")
    value["sources"] = [
        source.model_dump(mode="json") for source in (*sources, release, companion)
    ]
    value["search_synonyms"] = [item.model_dump(mode="json") for item in synonyms]
    return DatasetManifest.model_validate_json(json.dumps(value))
