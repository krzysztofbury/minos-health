"""Pinned modifier bytes and independently evidenced display observations."""

from typing import Annotated, ClassVar, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from minos.ingest.manifest_types import Digest, Identifier, SourceText


class ModifierFile(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", strict=True, frozen=True
    )
    source_url: Annotated[
        str,
        Field(
            pattern=r"^https://slowniki\.nfz\.gov\.pl/WersjeSlownikow/PobierzXml\?kodSlownika=MODTAR&nrGen=[1-9][0-9]{0,5}$"
        ),
    ]
    sha256: Digest
    size_bytes: int = Field(ge=1, le=4 * 1024 * 1024)
    fetched_at: AwareDatetime


class DisplayFile(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", strict=True, frozen=True
    )
    source_url: Annotated[
        str,
        Field(
            pattern=r"^https://slowniki\.nfz\.gov\.pl/WersjeSlownikow/Index/[1-9][0-9]{0,9}\?kod=MODTAR$"
        ),
    ]
    sha256: Digest
    size_bytes: int = Field(ge=1, le=256 * 1024)
    fetched_at: AwareDatetime


class DisplayObservation(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", strict=True, frozen=True
    )
    page_id: int = Field(ge=1, le=2_147_483_647)
    label: Annotated[str, Field(pattern=r"^20[0-9]{2}[0-9]{1,4}$")]
    published_on: Annotated[str, Field(pattern=r"^20[0-9]{2}-[0-9]{2}-[0-9]{2}$")]
    dictionary_valid_from: Annotated[
        str, Field(pattern=r"^20[0-9]{2}-[0-9]{2}-[0-9]{2}$")
    ]
    file: DisplayFile


class ModifierManifest(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", strict=True, frozen=True
    )
    release_id: Identifier
    year: int = Field(ge=2000, le=2099)
    generation: int = Field(ge=1, le=999999)
    grouper_type: Literal["SZP"]
    xml: ModifierFile
    display: DisplayObservation | None = None
    expected_modifiers: int = Field(ge=1, le=10000)
    expected_periods: int = Field(ge=1, le=50000)
    expected_group_mappings: int = Field(ge=1, le=50000)
    expected_inactive_empty: int = Field(ge=0, le=10000)
    publisher: SourceText
    license: SourceText
    notices: Annotated[tuple[SourceText, ...], Field(min_length=1, max_length=16)]

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        if (
            self.xml.source_url
            != f"https://slowniki.nfz.gov.pl/WersjeSlownikow/PobierzXml?kodSlownika=MODTAR&nrGen={self.generation}"
        ):
            raise ValueError("MODTAR XML URL does not match technical generation")
        if self.display is not None and (
            not self.display.label.startswith(str(self.year))
            or self.display.file.source_url
            != f"https://slowniki.nfz.gov.pl/WersjeSlownikow/Index/{self.display.page_id}?kod=MODTAR"
        ):
            raise ValueError(
                "MODTAR display observation conflicts with source identity"
            )
        return self


class ModifierCandidate(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", strict=True, frozen=True
    )
    catalog_release_id: Identifier
    modifier_release_id: Identifier
    status: Literal["pending_legal_review"]
    evidence: SourceText
