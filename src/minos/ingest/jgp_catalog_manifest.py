"""Reviewed attachment 1a workbooks pinned to their BAW document identity."""

from datetime import date
from typing import Annotated, ClassVar, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from minos.ingest.jgp_catalog_layout import LAYOUTS
from minos.ingest.manifest_types import Digest, Identifier, SourceText

type BawId = Annotated[int, Field(ge=1, le=10_000_000)]


class CatalogFile(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", frozen=True, strict=True
    )

    source_url: Annotated[
        str,
        Field(pattern=r"^https://baw\.nfz\.gov\.pl/api/file/4/[1-9][0-9]{0,8}$"),
    ]
    name: Literal["Zalacznik1a.xlsx"]
    sha256: Digest
    size_bytes: int = Field(ge=1, le=32 * 1024 * 1024)
    fetched_at: AwareDatetime


class CatalogSheetExpectation(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", frozen=True, strict=True
    )

    layout: str
    expected_groups: int = Field(ge=1, le=5_000)
    expected_chapters: int = Field(ge=1, le=200)
    # Codes listed on two rows, such as 102/2026 rows deferred to 2027-01-01.
    expected_repeated_codes: int = Field(ge=0, le=1_000)


class CatalogManifest(BaseModel):
    """Publication evidence only; service-date applicability belongs to #18."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", frozen=True, strict=True
    )

    release_id: Identifier
    act_number: Annotated[str, Field(pattern=r"^[1-9][0-9]{0,3}/20[0-9]{2}/DSOZ$")]
    baw_document_id: BawId
    # BAW AutomaticType: 1 original act, 3 consolidated text.
    automatic_type: Literal[1, 3]
    source_document_id: BawId | None
    introductory_document_id: BawId | None
    act_date: date
    entry_into_force: date | None
    document_url: Annotated[
        str,
        Field(
            pattern=r"^https://baw\.nfz\.gov\.pl/NFZ/document/[1-9][0-9]{0,8}/[A-Za-z0-9_-]{1,128}$"
        ),
    ]
    publisher: SourceText
    license: SourceText
    notices: Annotated[tuple[SourceText, ...], Field(min_length=1, max_length=16)]
    workbook: CatalogFile
    sheets: dict[Literal["1a", "1ae"], CatalogSheetExpectation]

    @model_validator(mode="after")
    def validate_release(self) -> Self:
        if not self.document_url.startswith(
            f"https://baw.nfz.gov.pl/NFZ/document/{self.baw_document_id}/"
        ):
            raise ValueError("document URL does not match the BAW document")
        if "1a" not in self.sheets:
            raise ValueError("attachment 1a must contain catalog 1a")
        for catalog, sheet in self.sheets.items():
            layout = LAYOUTS.get(sheet.layout)
            if layout is None or layout.catalog != catalog:
                raise ValueError("catalog sheet layout is not reviewed for it")
        if self.automatic_type == 1:
            if self.source_document_id is not None:
                raise ValueError("an original act has no source document")
            if self.introductory_document_id is not None:
                raise ValueError("an original act has no introductory amendment")
        elif self.source_document_id is None or self.introductory_document_id is None:
            raise ValueError("a consolidated text requires its lineage")
        if self.entry_into_force is not None and self.entry_into_force < self.act_date:
            raise ValueError("entry into force precedes the act date")
        return self
