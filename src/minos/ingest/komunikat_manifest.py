"""Reviewed komunikat files and reference-only terminology correspondence."""

from typing import Annotated, ClassVar, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from minos.ingest.manifest_types import Digest, Identifier, SourceText


class KomunikatFile(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", frozen=True, strict=True
    )

    source_url: Annotated[
        str,
        Field(
            pattern=r"^https://www\.nfz\.gov\.pl/download/gfx/nfz/pl/defaultaktualnosci/354/[1-9][0-9]*/[1-9][0-9]*/[a-zA-Z0-9_.-]+\.(?:xlsx|docx)$",
            max_length=2048,
        ),
    ]
    sha256: Digest
    size_bytes: int = Field(ge=1, le=32 * 1024 * 1024)
    fetched_at: AwareDatetime


class KomunikatManifest(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", frozen=True, strict=True
    )

    release_id: Identifier
    algorithm_version: Annotated[
        str, Field(pattern=r"^[1-9][0-9]?\.(?:[1-9]|1[0-2])\.20[0-9]{2}$")
    ]
    icd9_release_id: Identifier
    pairing: Literal["reference-only"]
    publisher: SourceText
    license: SourceText
    notices: Annotated[tuple[SourceText, ...], Field(min_length=1, max_length=16)]
    algorithm: KomunikatFile
    ranks: KomunikatFile
    specialties: KomunikatFile
    expected_ranks: dict[Literal["SZP", "AOS"], Annotated[int, Field(ge=1, le=20_000)]]
    expected_specialties: dict[
        Literal["SZP", "AOS", "REH"], Annotated[int, Field(ge=1, le=20_000)]
    ]

    @model_validator(mode="after")
    def validate_files(self) -> KomunikatManifest:
        prefix = self.algorithm.source_url.rsplit("/", 1)[0]
        if not self.algorithm.source_url.endswith(".docx"):
            raise ValueError("algorithm file must be DOCX")
        for source in (self.ranks, self.specialties):
            if not source.source_url.endswith(".xlsx"):
                raise ValueError("companion file must be XLSX")
            if source.source_url.rsplit("/", 1)[0] != prefix:
                raise ValueError("komunikat files belong to different notices")
        if (
            len(
                {
                    source.sha256
                    for source in (self.algorithm, self.ranks, self.specialties)
                }
            )
            != 3
        ):
            raise ValueError("komunikat file identities conflict")
        if set(self.expected_ranks) != {"SZP", "AOS"}:
            raise ValueError("rank worksheet counts are incomplete")
        if set(self.expected_specialties) != {"SZP", "AOS", "REH"}:
            raise ValueError("specialty worksheet counts are incomplete")
        return self
