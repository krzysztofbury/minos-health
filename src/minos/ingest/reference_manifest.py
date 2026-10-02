"""Strict reviewed input manifests, separate from watcher control metadata."""

from datetime import UTC, datetime
from typing import Annotated, ClassVar, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from minos.ingest.komunikat_manifest import KomunikatManifest
from minos.ingest.manifest_types import Digest as Digest
from minos.ingest.manifest_types import Identifier as Identifier
from minos.ingest.manifest_types import SourceText as SourceText

MAX_SOURCE_BYTES = 64 * 1024 * 1024


def absent_context(value: object) -> bool:
    return value is None


def empty_evidence(value: object) -> bool:
    return value == ()


class ExpectedCounts(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", frozen=True, strict=True
    )

    nodes: int = Field(ge=1, le=100_000)
    relations: int = Field(ge=0, le=100_000)
    concepts: int = Field(ge=1, le=100_000)
    descriptions: int = Field(ge=1, le=200_000)


class SourceManifest(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", frozen=True, strict=True
    )

    release_id: Identifier
    system: Identifier
    origin: Identifier
    source_version: SourceText
    source_file_id: SourceText
    source_url: Annotated[str, Field(pattern=r"^https://[^\s]+$", max_length=2048)]
    publisher: SourceText
    fetched_at: AwareDatetime
    source_created_at: AwareDatetime | None
    valid_from: AwareDatetime | None
    valid_to: AwareDatetime | None
    sha256: Digest
    size_bytes: int = Field(ge=1, le=MAX_SOURCE_BYTES)
    license: SourceText
    notices: Annotated[tuple[SourceText, ...], Field(min_length=1, max_length=16)]
    expected: ExpectedCounts
    context_for: Identifier | None = Field(default=None, exclude_if=absent_context)

    @model_validator(mode="after")
    def validate_period(self) -> Self:
        if self.valid_from and self.valid_to and self.valid_to <= self.valid_from:
            raise ValueError("invalid source validity period")
        return self


class DatasetManifest(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", frozen=True, strict=True
    )

    schema_version: Literal[1]
    transform_version: SourceText
    lockfile_sha256: Digest
    sources: Annotated[tuple[SourceManifest, ...], Field(min_length=1, max_length=16)]
    komunikaty: Annotated[tuple[KomunikatManifest, ...], Field(max_length=8)] = Field(
        default=(), exclude_if=empty_evidence
    )

    @model_validator(mode="after")
    def validate_releases(self) -> Self:
        identities = [source.release_id for source in self.sources]
        if len(set(identities)) != len(identities):
            raise ValueError("duplicate source release identity")
        sources = {source.release_id: source for source in self.sources}
        evidence_ids = [item.release_id for item in self.komunikaty]
        if len(set(identities + evidence_ids)) != len(identities + evidence_ids):
            raise ValueError("duplicate komunikat release identity")
        for item in self.komunikaty:
            target = sources.get(item.icd9_release_id)
            if target is None or (target.system, target.origin) != ("icd9-pl", "czs"):
                raise ValueError("komunikat reference requires canonical CZS")
        if len({item.icd9_release_id for item in self.komunikaty}) != len(
            self.komunikaty
        ):
            raise ValueError("ambiguous komunikat reference selection")
        for source in self.sources:
            if source.context_for is None:
                continue
            target = sources.get(source.context_for)
            if (
                target is None
                or target.context_for is not None
                or (source.system, source.origin) != ("icd9-pl", "nfz-workbook")
                or (target.system, target.origin) != ("icd9-pl", "czs")
                or source.source_version != target.source_version
            ):
                raise ValueError("invalid supporting source binding")
        return self


def utc_text(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat()
