"""Reviewed attachment 9 inputs and source-local correspondence expectations."""

from datetime import date
from typing import Annotated, ClassVar, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from minos.domain.jgp_ruleset import (
    AmbiguousRuleset,
    NoRulesetForDate,
    ReleaseWindow,
    resolve_release,
)
from minos.ingest.manifest_types import Digest, Identifier, SourceText


def absent(value: object) -> bool:
    return value is None


class ConditionsFile(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", frozen=True, strict=True
    )
    source_url: Annotated[
        str, Field(pattern=r"^https://baw\.nfz\.gov\.pl/api/file/4/[1-9][0-9]{0,8}$")
    ]
    sha256: Digest
    size_bytes: int = Field(ge=1, le=32 * 1024 * 1024)
    fetched_at: AwareDatetime


class ConditionsExpectation(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", frozen=True, strict=True
    )
    groups: int = Field(ge=1, le=2000)
    evidence_cells: int = Field(ge=1, le=50_000)
    unbound_member_occurrences: int = Field(ge=0, le=50_000)
    lists: int = Field(ge=1, le=3000)
    member_occurrences: int = Field(ge=1, le=50_000)
    unique_members: int = Field(ge=1, le=50_000)
    conditions: int = Field(ge=1, le=20_000)
    kind_counts: dict[str, Annotated[int, Field(ge=0, le=20_000)]]
    unsupported_reasons: dict[str, Annotated[int, Field(ge=0, le=20_000)]]
    unmatched_codes: int = Field(ge=0, le=50_000)


class ConditionsManifest(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", frozen=True, strict=True
    )
    release_id: Identifier
    baw_document_id: int = Field(ge=1, le=10_000_000)
    lineage_document_id: int = Field(ge=1, le=10_000_000)
    supersedes: Identifier | None = Field(default=None, exclude_if=absent)
    catalog_release_id: Identifier | None = Field(default=None, exclude_if=absent)
    act_number: SourceText
    layout: Literal["nfz-9-102-2026", "nfz-9-46-2026"]
    valid_from: date
    valid_to: date | None
    published_on: date
    applicability_evidence: SourceText
    publisher: SourceText
    license: SourceText
    notices: Annotated[tuple[SourceText, ...], Field(min_length=1, max_length=16)]
    icd9_release_id: Identifier
    icd10_release_id: Identifier
    correspondence: Literal["source_local_exact_reference"]
    workbook: ConditionsFile
    sheets: dict[Literal["1a", "1ae"], ConditionsExpectation]

    @model_validator(mode="after")
    def validate_layout(self) -> Self:
        expected = {"1a", "1ae"} if self.layout == "nfz-9-102-2026" else {"1a"}
        if set(self.sheets) != expected:
            raise ValueError("characteristics layout requires exact catalog contexts")
        if self.valid_to is not None and self.valid_to < self.valid_from:
            raise ValueError("characteristics validity window is reversed")
        return self


def validate_conditions_windows(releases: tuple[ConditionsManifest, ...]) -> None:
    """Require an explicit, unambiguous publication/service chronology.

    Only release-start boundaries can introduce new overlaps. Closed endpoints
    remain checked by dated readers; no missing supersession is inferred from
    an endpoint, filename or publication order.
    """
    if len(releases) > 8:
        raise ValueError("characteristics release inventory exceeds bound")
    windows = tuple(
        ReleaseWindow(r.release_id, r.valid_from, r.published_on, r.supersedes)
        for r in releases
    )
    for known_on in sorted({r.published_on for r in releases}):
        for service_date in sorted({r.valid_from for r in releases}):
            try:
                _ = resolve_release(windows, service_date, known_on)
            except NoRulesetForDate:
                continue
            except AmbiguousRuleset as error:
                raise ValueError(
                    "characteristics chronology requires reviewed supersession"
                ) from error
