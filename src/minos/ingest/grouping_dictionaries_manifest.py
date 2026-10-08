"""Reviewed legal dictionaries for the grouping builder (#151).

Both sources are Polish legal acts pinned as the authentic Dziennik Ustaw PDF
from the Sejm ELI API. Each manifest carries a reviewed table of its annex:
the ELI HTML rendering merges rows at page breaks (1220, 4450 and four other
annex 2 codes disappear into neighbouring cells), so it is not a source.
"""

from datetime import date
from typing import Annotated, ClassVar, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from minos.ingest.manifest_types import Digest, Identifier, SourceText


class LegalActFile(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", frozen=True, strict=True
    )

    source_url: Annotated[
        str,
        Field(
            pattern=r"^https://api\.sejm\.gov\.pl/eli/acts/DU/20[0-9]{2}/[1-9][0-9]{0,4}/text\.(?:html|pdf)$",
            max_length=256,
        ),
    ]
    sha256: Digest
    size_bytes: int = Field(ge=1, le=16 * 1024 * 1024)
    fetched_at: AwareDatetime


class SpecialtyEntry(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", frozen=True, strict=True
    )

    code: Annotated[str, Field(pattern=r"^[0-9]{4}$")]
    name: Annotated[str, Field(min_length=1, max_length=512)]
    section: Annotated[str, Field(min_length=1, max_length=512)]


class SpecialtyDictionary(BaseModel):
    """Annex 2: codes of the specialty of an organizational unit (part VIII)."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", frozen=True, strict=True
    )

    release_id: Identifier
    act: SourceText
    annex: Literal["Załącznik nr 2"]
    publisher: SourceText
    license: SourceText
    notices: Annotated[tuple[SourceText, ...], Field(min_length=1, max_length=16)]
    source: LegalActFile
    reviewed_on: date
    # § 9 ust. 4: children's units take the odd code one above the adult code.
    children_rule: SourceText
    entries: Annotated[tuple[SpecialtyEntry, ...], Field(min_length=1, max_length=2000)]

    @model_validator(mode="after")
    def validate_entries(self) -> Self:
        if not self.source.source_url.endswith(".pdf"):
            raise ValueError("specialty codes are reviewed from the published PDF")
        codes = [entry.code for entry in self.entries]
        if len(set(codes)) != len(codes) or codes != sorted(codes):
            raise ValueError("specialty codes must be unique and in annex order")
        return self


class DischargeMode(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", frozen=True, strict=True
    )

    point: int = Field(ge=1, le=99)
    code: int = Field(ge=1, le=99)
    name: Annotated[str, Field(min_length=1, max_length=1024)]


class DischargeModeDictionary(BaseModel):
    """Annex 4: discharge-mode codes, reviewed from the pinned PDF."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", frozen=True, strict=True
    )

    release_id: Identifier
    act: SourceText
    annex: Literal["Załącznik nr 4"]
    publisher: SourceText
    license: SourceText
    notices: Annotated[tuple[SourceText, ...], Field(min_length=1, max_length=16)]
    source: LegalActFile
    reviewed_on: date
    modes: Annotated[tuple[DischargeMode, ...], Field(min_length=1, max_length=99)]

    @model_validator(mode="after")
    def validate_modes(self) -> Self:
        if not self.source.source_url.endswith(".pdf"):
            raise ValueError("the discharge-mode act is pinned as published PDF")
        codes = [mode.code for mode in self.modes]
        points = [mode.point for mode in self.modes]
        if len(set(codes)) != len(codes) or points != list(range(1, len(points) + 1)):
            raise ValueError("discharge modes must keep the act's point order")
        return self


class GroupingDictionaries(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", frozen=True, strict=True
    )

    specialties: SpecialtyDictionary
    discharge_modes: DischargeModeDictionary

    @model_validator(mode="after")
    def validate_identities(self) -> Self:
        if self.specialties.release_id == self.discharge_modes.release_id:
            raise ValueError("dictionary release identities conflict")
        if self.specialties.source.sha256 == self.discharge_modes.source.sha256:
            raise ValueError("dictionary sources conflict")
        return self
