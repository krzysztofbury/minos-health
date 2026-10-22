"""Pinned WHO ICD-10/ICD-11 map tables from the RSK ICD-11 PL resource (#53).

The three workbooks are reproduced row for row: no match type is added and no
gap is filled. Their ICD-10 side is WHO ICD-10 2019, so a row names a code,
not a Polish ICD-10 release.
"""

from typing import Annotated, ClassVar, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from minos.ingest.manifest_types import Digest, Identifier, SourceText

type MapKind = Literal["10to11_one", "10to11_many", "11to10_one"]
MAP_FILE_NAMES: dict[MapKind, str] = {
    "10to11_one": "10To11MapdoJednejKategorii",
    "10to11_many": "10To11MapdowieluKategorii",
    "11to10_one": "11To10MapdoJednejKategorii",
}
MAX_MAP_ROWS = 40_000


class Icd11MapFile(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", frozen=True, strict=True
    )

    kind: MapKind
    file_name: Annotated[
        str,
        Field(
            pattern=r"^(10To11MapdoJednejKategorii|10To11MapdowieluKategorii|11To10MapdoJednejKategorii)_20[0-9]{2}-[0-9]{2}\.xlsx$"
        ),
    ]
    source_url: Annotated[
        str,
        Field(
            pattern=r"^https://rsk3\.ezdrowie\.gov\.pl/api/rsk/file/get/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
        ),
    ]
    sha256: Digest
    size_bytes: int = Field(ge=1, le=8 * 1024 * 1024)
    fetched_at: AwareDatetime
    expected_rows: int = Field(ge=1, le=MAX_MAP_ROWS)

    @model_validator(mode="after")
    def validate_name(self) -> Self:
        if not self.file_name.startswith(MAP_FILE_NAMES[self.kind] + "_"):
            raise ValueError("map file name does not match its kind")
        return self


class Icd11Maps(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", frozen=True, strict=True
    )

    release_id: Identifier
    # The ICD-11 PL release of the same WHO version; rows link to its codes.
    icd11_release_id: Identifier
    notices: Annotated[tuple[SourceText, ...], Field(min_length=1, max_length=16)]
    files: Annotated[tuple[Icd11MapFile, ...], Field(min_length=3, max_length=3)]

    @model_validator(mode="after")
    def validate_files(self) -> Self:
        if {item.kind for item in self.files} != set(MAP_FILE_NAMES):
            raise ValueError("ICD-11 maps need each WHO table exactly once")
        versions = {item.file_name.rsplit("_", 1)[1] for item in self.files}
        if len(versions) != 1:
            raise ValueError("ICD-11 map tables are of different WHO versions")
        return self
