"""Strict reviewed input manifests, separate from watcher control metadata."""

from datetime import UTC, date, datetime
from typing import Annotated, ClassVar, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from minos.ingest.jgp_catalog_manifest import CatalogManifest
from minos.ingest.jgp_conditions_manifest import (
    ConditionsManifest,
    validate_conditions_windows,
)
from minos.ingest.jgp_modifiers_manifest import ModifierCandidate, ModifierManifest
from minos.ingest.komunikat_manifest import KomunikatManifest
from minos.ingest.manifest_types import Digest as Digest
from minos.ingest.manifest_types import Identifier as Identifier
from minos.ingest.manifest_types import SourceText as SourceText

MAX_SOURCE_BYTES = 64 * 1024 * 1024


def _lineage_root(item: CatalogManifest) -> int:
    """The original act a catalog release belongs to (BAW document ID)."""
    return item.source_document_id or item.baw_document_id


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
    catalogs: Annotated[tuple[CatalogManifest, ...], Field(max_length=8)] = Field(
        default=(), exclude_if=empty_evidence
    )
    characteristics: Annotated[tuple[ConditionsManifest, ...], Field(max_length=8)] = (
        Field(default=(), exclude_if=empty_evidence)
    )
    modifiers: Annotated[tuple[ModifierManifest, ...], Field(max_length=8)] = Field(
        default=(), exclude_if=empty_evidence
    )
    modifier_candidates: Annotated[
        tuple[ModifierCandidate, ...], Field(max_length=8)
    ] = Field(default=(), exclude_if=empty_evidence)
    characteristics_knowledge_as_of: date | None = Field(
        default=None, exclude_if=absent_context
    )
    characteristics_knowledge_evidence: SourceText | None = Field(
        default=None, exclude_if=absent_context
    )
    # Reviewed date through which the catalog lineage is known complete. No
    # answer may claim knowledge beyond it.
    catalog_knowledge_as_of: date | None = Field(
        default=None, exclude_if=absent_context
    )
    catalog_knowledge_evidence: SourceText | None = Field(
        default=None, exclude_if=absent_context
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
        catalog_ids = [item.release_id for item in self.catalogs]
        all_ids = identities + evidence_ids + catalog_ids
        all_ids += [item.release_id for item in self.characteristics]
        all_ids += [item.release_id for item in self.modifiers]
        if len(set(all_ids)) != len(all_ids):
            raise ValueError("duplicate catalog release identity")
        for characteristic in self.characteristics:
            for release_id, system, origin in (
                (characteristic.icd9_release_id, "icd9-pl", "czs"),
                (characteristic.icd10_release_id, "icd10-pl", "rsk"),
            ):
                target = sources.get(release_id)
                if target is None or (target.system, target.origin) != (system, origin):
                    raise ValueError(
                        "characteristics require explicit canonical dictionary pins"
                    )
        self.validate_characteristics()
        documents = [item.baw_document_id for item in self.catalogs]
        if len(set(documents)) != len(documents):
            raise ValueError("a BAW document is pinned twice")
        if bool(self.catalogs) != (self.catalog_knowledge_as_of is not None):
            raise ValueError("catalog releases require a reviewed knowledge horizon")
        if (self.catalog_knowledge_as_of is None) != (
            self.catalog_knowledge_evidence is None
        ):
            raise ValueError("a catalog knowledge horizon requires its evidence")
        if self.catalog_knowledge_as_of is not None and any(
            catalog.published_on > self.catalog_knowledge_as_of
            for catalog in self.catalogs
        ):
            raise ValueError("a catalog release is published after the horizon")
        catalogs = {catalog.release_id: catalog for catalog in self.catalogs}
        modifiers = {item.release_id: item for item in self.modifiers}
        if len({item.catalog_release_id for item in self.modifier_candidates}) != len(
            self.modifier_candidates
        ):
            raise ValueError("catalog has multiple MODTAR review candidates")
        for candidate in self.modifier_candidates:
            catalog = catalogs.get(candidate.catalog_release_id)
            modifier = modifiers.get(candidate.modifier_release_id)
            if (
                catalog is None
                or modifier is None
                or catalog.service_from.year != modifier.year
            ):
                raise ValueError(
                    "MODTAR review candidate has absent or incompatible source pins"
                )
        for catalog in self.catalogs:
            if catalog.supersedes is None:
                continue
            replaced = catalogs.get(catalog.supersedes)
            if replaced is None:
                raise ValueError("superseded catalog release is not pinned")
            if replaced.published_on >= catalog.published_on:
                raise ValueError("a catalog release supersedes a later publication")
            if _lineage_root(replaced) != _lineage_root(catalog):
                raise ValueError("superseded catalog release has another lineage")
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

    def validate_characteristics(self) -> None:
        horizon = self.characteristics_knowledge_as_of
        if bool(self.characteristics) != (horizon is not None) or (horizon is None) != (
            self.characteristics_knowledge_evidence is None
        ):
            raise ValueError(
                "characteristics require their own reviewed knowledge horizon"
            )
        catalogs = {item.release_id: item for item in self.catalogs}
        releases = {item.release_id: item for item in self.characteristics}
        for item in self.characteristics:
            if horizon is None or item.published_on > horizon:
                raise ValueError("characteristics publication exceeds reviewed horizon")
            if item.catalog_release_id is not None:
                target = catalogs.get(item.catalog_release_id)
                if target is None or target.baw_document_id != item.baw_document_id:
                    raise ValueError(
                        "characteristics catalog correspondence requires the same reviewed document"
                    )
            if item.supersedes is not None:
                previous = releases.get(item.supersedes)
                if (
                    previous is None
                    or previous.published_on >= item.published_on
                    or previous.lineage_document_id != item.lineage_document_id
                ):
                    raise ValueError(
                        "characteristics supersession is missing, later or another lineage"
                    )
        validate_conditions_windows(self.characteristics)


def utc_text(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat()
