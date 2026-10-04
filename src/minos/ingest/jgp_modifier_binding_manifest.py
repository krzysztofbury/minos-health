"""Explicit reviewed MODTAR bindings, with separately pinned manual overlays."""

from datetime import date
from typing import Annotated, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from minos.domain.jgp_modifiers import (
    LegalMapping,
    LegalOverlay,
    LegalProvision,
    Mapping,
)
from minos.ingest.manifest_types import Digest, Identifier, SourceText


class LegalMappingInput(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", strict=True, frozen=True
    )
    mapping: Mapping
    service_from: date
    service_to: date
    applicability_evidence: SourceText


class LegalProvisionInput(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", strict=True, frozen=True
    )
    symbol: Identifier
    clause: SourceText
    coefficient_text: Annotated[str, Field(pattern=r"^[0-9]{1,3}(?:\.[0-9]{1,6})?$")]
    groups: Annotated[tuple[SourceText, ...], Field(min_length=1, max_length=2000)]
    service_from: date
    service_to: date
    eligibility_text: SourceText
    source_evidence: SourceText

    def domain(self) -> LegalProvision:
        return LegalProvision(
            self.symbol,
            self.clause,
            self.coefficient_text,
            self.groups,
            self.service_from,
            self.service_to,
            self.eligibility_text,
            self.source_evidence,
        )


class LegalOverlayInput(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", strict=True, frozen=True
    )
    year: int = Field(ge=2000, le=2099)
    generation: int = Field(ge=1, le=999999)
    grouper_type: Literal["SZP"]
    catalog_release_ids: Annotated[
        tuple[Identifier, ...], Field(min_length=1, max_length=8)
    ]
    reviewed_on: date
    legal_source_evidence: Annotated[
        tuple[SourceText, ...], Field(min_length=1, max_length=64)
    ]
    rows: Annotated[
        tuple[LegalMappingInput, ...], Field(min_length=1, max_length=50000)
    ]
    supplements: Annotated[tuple[LegalProvisionInput, ...], Field(max_length=500)] = ()

    def domain(self) -> LegalOverlay:
        return LegalOverlay(
            self.year,
            self.generation,
            self.grouper_type,
            self.catalog_release_ids,
            self.reviewed_on,
            self.legal_source_evidence,
            tuple(
                LegalMapping(
                    row.mapping,
                    row.service_from,
                    row.service_to,
                    row.applicability_evidence,
                )
                for row in self.rows
            ),
            tuple(rule.domain() for rule in self.supplements),
        )


class LegalOverlayFile(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", strict=True, frozen=True
    )
    sha256: Digest
    size_bytes: int = Field(ge=1, le=32 * 1024 * 1024)


class ModifierBinding(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", strict=True, frozen=True
    )
    catalog_release_id: Identifier
    modifier_release_id: Identifier
    overlay: LegalOverlayFile
    reviewed_on: date
    knowledge_from: date
    reviewer: SourceText
    evidence: SourceText
