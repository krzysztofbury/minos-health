"""Reviewed BAW product catalogs that classify reported settlement products (#145).

The algorithm excludes from grouping the procedures dated within a product of
the excluded catalog of type W (currently 1w). Every other product catalog of
the governing act proves a code is not type W. A catalog of type V (1v) is not
supported: an act listing one fails review instead of being read as neutral.
"""

import re
from typing import Annotated, ClassVar, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from minos.ingest.manifest_types import Digest, Identifier, SourceText

type ProductSheet = Literal["1b", "1c", "1d", "1ows", "1ts", "1w"]
_ATTACHMENT = re.compile(r"^Zalacznik(1[a-z]+)\.xlsx?$")


class ProductCatalogFile(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", frozen=True, strict=True
    )

    sheet: ProductSheet
    file_name: Annotated[str, Field(pattern=r"^Zalacznik1(?:b|c|d|ows|ts|w)\.xlsx?$")]
    source_url: Annotated[
        str,
        Field(pattern=r"^https://baw\.nfz\.gov\.pl/api/file/4/[1-9][0-9]{0,9}$"),
    ]
    sha256: Digest
    size_bytes: int = Field(ge=1, le=32 * 1024 * 1024)
    fetched_at: AwareDatetime
    # Legacy BIFF .xls is pinned but not parsed; its codes stay unknown.
    parsed: bool
    worksheet: SourceText | None = None
    code_header: SourceText | None = None
    expected_rows: int = Field(ge=0, le=5000)
    expected_codes: int = Field(ge=0, le=5000)

    @model_validator(mode="after")
    def validate_parse_policy(self) -> Self:
        if self.parsed != self.file_name.endswith(".xlsx"):
            raise ValueError("only .xlsx product catalogs are parsed")
        if self.parsed != (self.worksheet is not None and self.code_header is not None):
            raise ValueError("a parsed product catalog needs its worksheet and header")
        if self.parsed != (self.expected_codes > 0):
            raise ValueError("product code counts follow the parse policy")
        if self.expected_codes > self.expected_rows:
            raise ValueError("unique product codes exceed product rows")
        if not self.file_name.startswith(f"Zalacznik{self.sheet}."):
            raise ValueError("product catalog file name conflicts with its sheet")
        return self


class ProductCatalogs(BaseModel):
    """Every product catalog of one pinned act, bound to its JGP catalog release."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", frozen=True, strict=True
    )

    release_id: Identifier
    catalog_release_id: Identifier
    baw_document_id: int = Field(ge=1, le=10_000_000)
    # The reviewed set of catalog attachments of the BAW document, 1a included.
    attachments: Annotated[tuple[str, ...], Field(min_length=1, max_length=32)]
    notices: Annotated[tuple[SourceText, ...], Field(min_length=1, max_length=16)]
    files: Annotated[tuple[ProductCatalogFile, ...], Field(min_length=1, max_length=16)]

    @model_validator(mode="after")
    def validate_catalog_set(self) -> Self:
        sheets: list[str] = []
        for name in self.attachments:
            match = _ATTACHMENT.fullmatch(name)
            if match is None:
                raise ValueError("unexpected catalog attachment name")
            sheets.append(match.group(1))
        if "1v" in sheets:
            raise ValueError("excluded catalog type V is not supported")
        if len(set(sheets)) != len(sheets) or "1a" not in sheets:
            raise ValueError("catalog attachments are ambiguous")
        files = {item.sheet: item for item in self.files}
        if len(files) != len(self.files):
            raise ValueError("a product catalog is pinned twice")
        if set(files) != set(sheets) - {"1a", "1ae"}:
            raise ValueError("product catalogs do not cover the act's attachments")
        if {item.file_name for item in self.files} != {
            name for name in self.attachments if not name.startswith("Zalacznik1a")
        }:
            raise ValueError("product catalog files conflict with attachments")
        if "1w" not in files or not files["1w"].parsed:
            raise ValueError("the type W catalog must be parsed")
        if len({item.sha256 for item in self.files}) != len(self.files):
            raise ValueError("product catalog file identities conflict")
        return self
