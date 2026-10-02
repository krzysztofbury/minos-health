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


class RowApplicability(BaseModel):
    """Reviewed per-row service window that differs from the release window.

    Each listed row must exist once and its source note must contain
    `note_marker`, pairing the legal provision with the catalog row.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", frozen=True, strict=True
    )

    catalog: Literal["1a", "1ae"]
    ordinals: Annotated[
        tuple[Annotated[int, Field(ge=1, le=5_000)], ...],
        Field(min_length=1, max_length=500),
    ]
    service_from: date | None
    service_to: date | None
    note_marker: SourceText
    evidence: SourceText

    @model_validator(mode="after")
    def validate_window(self) -> Self:
        if self.service_from is None and self.service_to is None:
            raise ValueError("row applicability must narrow the release window")
        if (
            self.service_from is not None
            and self.service_to is not None
            and self.service_to < self.service_from
        ):
            raise ValueError("row applicability ends before it starts")
        if len(set(self.ordinals)) != len(self.ordinals):
            raise ValueError("row applicability lists an ordinal twice")
        return self


class CatalogManifest(BaseModel):
    """Publication and service-applicability evidence for one catalog release.

    Knowledge time is the publication evidence (`published_on`); the workbook
    `fetched_at` records when Minos ingested the bytes.
    """

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
    published_on: date
    service_from: date
    service_evidence: SourceText
    # An earlier release this one replaces where both cover a service date.
    supersedes: Identifier | None
    row_applicability: Annotated[
        tuple[RowApplicability, ...], Field(max_length=32)
    ] = ()
    # #46 supplies a reviewed MODTAR binding; until then modifiers are unbound,
    # which is not the same as an empty set of applicable modifiers.
    modifier_binding: Literal["unbound"]
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
        if self.published_on < self.act_date:
            raise ValueError("publication precedes the act date")
        if self.supersedes == self.release_id:
            raise ValueError("a release cannot supersede itself")
        listed = [
            (item.catalog, ordinal)
            for item in self.row_applicability
            for ordinal in item.ordinals
        ]
        if len(set(listed)) != len(listed):
            raise ValueError("a catalog row has two applicability overrides")
        for item in self.row_applicability:
            if item.catalog not in self.sheets:
                raise ValueError("row applicability names an absent catalog")
            if item.service_to is not None and item.service_to < self.service_from:
                raise ValueError("row applicability ends before the release applies")
            if item.service_from is not None and item.service_from <= self.service_from:
                raise ValueError("row applicability must start after the release")
        return self
