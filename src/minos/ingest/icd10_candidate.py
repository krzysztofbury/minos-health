"""Derive a shadow ICD-10 dataset candidate from a newly archived RSK release.

The candidate is the live dataset plus one new RSK ICD-10 release (#141 phase
B). The previous release stays, closed at the new validity start, so answers
for earlier service dates keep using it. Nothing here builds or activates the
candidate; every refusal names the activation criterion that failed.
"""

import hashlib
import json
import tempfile
from collections.abc import Callable, Iterable
from datetime import date
from pathlib import Path
from typing import Annotated, BinaryIO, ClassVar, Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)

from minos.domain.reference import ReferenceNode, ReferenceRelation, SourceError
from minos.ingest.archive_source import ArchivedBlob, verified_source
from minos.ingest.icd10 import ReferenceEvent, parse_icd10
from minos.ingest.manifest_types import Digest, SourceText
from minos.ingest.reference_manifest import (
    DatasetManifest,
    ExpectedCounts,
    SourceManifest,
)

type Criterion = Literal["bytes", "parse", "build", "regression", "history", "licence"]

_MAX_POLICY_BYTES = 64 * 1024
_MAX_BASIC_INFO_BYTES = 256 * 1024
_RSK_FILE_URL = "https://rsk3.ezdrowie.gov.pl/api/rsk/file/get/"
_UUID = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"


class CandidateRejected(Exception):
    """A candidate failed one activation criterion; the live release stays."""

    def __init__(self, criterion: Criterion, code: str) -> None:
        super().__init__(f"{criterion}: {code}")
        self.criterion: Criterion = criterion
        self.code: str = code


class RskIcd10Policy(BaseModel):
    """The owner's class-level approval for automatic RSK ICD-10 releases."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", frozen=True, strict=True
    )

    schema_version: Literal[1]
    source_class: Literal["rsk-icd10"]
    hcd: Literal["00CD10"]
    approved_on: date
    approval: SourceText
    # SHA-256 of the RSK basic-info description, which carries the licence
    # statement. Any rewording sends the release to the owner.
    description_sha256: Digest
    publisher: SourceText
    license: SourceText
    notices: Annotated[tuple[SourceText, ...], Field(min_length=1, max_length=16)]
    max_removed_code_ratio: float = Field(ge=0, le=0.2)
    max_concept_count_change_ratio: float = Field(ge=0, le=0.2)

    @classmethod
    def load(cls, path: Path) -> Self:
        with path.open("rb") as source:
            content = source.read(_MAX_POLICY_BYTES + 1)
        if len(content) > _MAX_POLICY_BYTES:
            raise ValueError("automatic activation policy exceeds its byte bound")
        return cls.model_validate_json(content)


class _RskPublisher(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", frozen=True)

    name: SourceText


class _RskFile(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", frozen=True)

    uuid: Annotated[str, Field(pattern=_UUID)]
    size_bytes: int = Field(alias="sizeBytes", ge=1)
    creation_date: AwareDatetime = Field(alias="creationDate")
    extension: str
    archived: bool


class RskBasicInfo(BaseModel):
    """The archived RSK basic-info fields a release entry is derived from."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", frozen=True)

    hcd: str
    version: Annotated[str, Field(pattern=r"^[0-9A-Za-z.]{1,16}$")]
    description: Annotated[str, Field(min_length=1, max_length=8192)]
    valid_from: AwareDatetime = Field(alias="validDateFrom")
    valid_to: AwareDatetime | None = Field(alias="validDateTo")
    publisher: _RskPublisher
    files: Annotated[tuple[_RskFile, ...], Field(max_length=64)]

    @model_validator(mode="after")
    def validate_period(self) -> Self:
        if self.valid_to is not None and self.valid_to <= self.valid_from:
            raise ValueError("invalid RSK validity period")
        return self


def propose_icd10_release(
    base: DatasetManifest,
    archive_root: Path,
    *,
    product_sha256: str,
    basic_info_sha256: str,
    fetched_at: AwareDatetime,
    policy: RskIcd10Policy,
) -> DatasetManifest:
    """Return the base dataset plus the archived release, or refuse it."""
    info = _read_basic_info(archive_root, basic_info_sha256)
    if info.hcd != policy.hcd:
        raise CandidateRejected("bytes", "basic_info_not_icd10")
    description = hashlib.sha256(info.description.encode()).hexdigest()
    if description != policy.description_sha256:
        raise CandidateRejected("licence", "licence_statement_changed")
    if info.publisher.name != policy.publisher:
        raise CandidateRejected("licence", "publisher_changed")
    try:
        product = ArchivedBlob.at(archive_root, product_sha256)
    except SourceError:
        raise CandidateRejected("bytes", "product_not_archived") from None
    listed = [
        item for item in info.files if item.extension == "xml" and not item.archived
    ]
    if len(listed) != 1 or listed[0].size_bytes != product.size_bytes:
        raise CandidateRejected("bytes", "product_not_listed_by_rsk")
    previous = _latest_rsk(base)
    if any(source.sha256 == product.sha256 for source in base.sources):
        raise CandidateRejected("history", "release_already_present")
    if info.valid_to is not None:
        raise CandidateRejected("history", "release_already_closed")
    if previous.valid_from is None:
        raise CandidateRejected("history", "live_release_has_no_validity_start")
    if info.valid_from <= previous.valid_from:
        # A correction or backdated release changes answers for past service
        # dates, so it always goes to the owner.
        raise CandidateRejected("history", "not_newer_than_live_release")
    counts = _count_release(archive_root, product)
    release_id = f"icd10-rsk-{info.version.lower()}-{product.sha256[:12]}"
    try:
        release = SourceManifest(
            release_id=release_id,
            system="icd10-pl",
            origin="rsk",
            source_version=info.version,
            source_file_id=listed[0].uuid,
            source_url=_RSK_FILE_URL + listed[0].uuid,
            publisher=policy.publisher,
            fetched_at=fetched_at,
            source_created_at=listed[0].creation_date,
            valid_from=info.valid_from,
            valid_to=None,
            sha256=product.sha256,
            size_bytes=product.size_bytes,
            license=policy.license,
            notices=policy.notices,
            expected=counts,
        )
        return _with_release(base, previous, release)
    except ValidationError:
        raise CandidateRejected("parse", "candidate_manifest_invalid") from None


def _latest_rsk(base: DatasetManifest) -> SourceManifest:
    open_releases = [
        source
        for source in base.sources
        if (source.system, source.origin) == ("icd10-pl", "rsk")
        and source.valid_to is None
    ]
    if len(open_releases) != 1:
        raise CandidateRejected("history", "live_icd10_release_is_ambiguous")
    return open_releases[0]


def _read_basic_info(archive_root: Path, sha256: str) -> RskBasicInfo:
    try:
        blob = ArchivedBlob.at(archive_root, sha256)
        if blob.size_bytes > _MAX_BASIC_INFO_BYTES:
            raise CandidateRejected("bytes", "basic_info_too_large")
        with tempfile.TemporaryDirectory(prefix=".minos-candidate-") as staging:
            with verified_source(
                archive_root, blob, staging_directory=Path(staging)
            ) as snapshot:
                content = snapshot.read(_MAX_BASIC_INFO_BYTES + 1)
        return RskBasicInfo.model_validate(json.loads(content))
    except SourceError:
        raise CandidateRejected("bytes", "basic_info_not_archived") from None
    except UnicodeDecodeError, ValueError, RecursionError:
        # ValidationError is a ValueError: RSK changed its metadata shape.
        raise CandidateRejected("parse", "basic_info_schema_changed") from None


def _count_release(archive_root: Path, product: ArchivedBlob) -> ExpectedCounts:
    return count_release(archive_root, product, parse_icd10)


def count_release(
    archive_root: Path,
    product: ArchivedBlob,
    parse: Callable[[BinaryIO], Iterable[ReferenceEvent]],
    *,
    code: str = "product_rejected_by_parser",
) -> ExpectedCounts:
    """Count one archived release through its parser, or refuse it as unparseable."""
    nodes = relations = concepts = descriptions = 0
    try:
        with tempfile.TemporaryDirectory(prefix=".minos-candidate-") as staging:
            with verified_source(
                archive_root, product, staging_directory=Path(staging)
            ) as snapshot:
                for event in parse(snapshot):
                    if isinstance(event, ReferenceRelation):
                        relations += 1
                    elif isinstance(event, ReferenceNode):
                        nodes += 1
                        if event.lookup_code is not None:
                            concepts += 1
                            descriptions += len(event.descriptions)
        return ExpectedCounts(
            nodes=nodes,
            relations=relations,
            concepts=concepts,
            descriptions=descriptions,
        )
    except SourceError:
        raise CandidateRejected("parse", code) from None
    except ValidationError:
        raise CandidateRejected("parse", "product_counts_out_of_bounds") from None


def _with_release(
    base: DatasetManifest, previous: SourceManifest, release: SourceManifest
) -> DatasetManifest:
    closed = previous.model_copy(update={"valid_to": release.valid_from})
    sources = tuple(
        closed if source.release_id == previous.release_id else source
        for source in base.sources
    )
    # A synonym pinned to the previous release becomes unscoped, which the
    # build accepts only if its wording exists in every ICD-10 release.
    synonyms = tuple(
        item.model_copy(update={"release_id": None})
        if item.system == "icd10-pl" and item.release_id == previous.release_id
        else item
        for item in base.search_synonyms
    )
    value = base.model_dump(mode="json")
    value["sources"] = [
        source.model_dump(mode="json") for source in (*sources, release)
    ]
    value["search_synonyms"] = [item.model_dump(mode="json") for item in synonyms]
    return DatasetManifest.model_validate_json(json.dumps(value))
