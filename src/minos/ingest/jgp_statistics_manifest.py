"""Reviewed NFZ monthly JGP hospitalization reports (dane.gov.pl dataset 2581, #283).

Each report covers one reporting month and repeats two earlier months: the
previous month and the same month a year earlier. NFZ revises recent months as
late settlements arrive, so a figure is only as final as its age at the report.
"""

import re
from datetime import date
from typing import Annotated, ClassVar, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from minos.ingest.manifest_types import Digest, Identifier, SourceText

type Month = Annotated[str, Field(pattern=r"^20[0-9]{2}-(0[1-9]|1[0-2])$")]
_URL = re.compile(
    r"^https://api\.dane\.gov\.pl/media/resources/20[0-9]{6}/"
    + r"raport_SZP_i_kardiologia_(20[0-9]{2})_([1-9]|1[0-2])\.xml$"
)


class ReportExtract(BaseModel):
    """The canonical parsed figures of one report; what builds read."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", frozen=True, strict=True
    )

    sha256: Digest
    size_bytes: int = Field(ge=1, le=16 * 1024 * 1024)
    group_figures: int = Field(ge=1, le=60_000)
    section_figures: int = Field(ge=1, le=3_000)


class ReportPin(BaseModel):
    """The raw report as published: provenance and the parser's input."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", frozen=True, strict=True
    )

    month: Month
    resource_id: int = Field(ge=1, le=100_000_000)
    source_url: Annotated[str, Field(max_length=256)]
    # dane.gov.pl data date: a report published months later is more complete.
    published_on: date
    sha256: Digest
    size_bytes: int = Field(ge=1, le=128 * 1024 * 1024)
    fetched_at: AwareDatetime
    # Reviewed data-row counts; any drift fails the parse.
    group_rows: int = Field(ge=1, le=20_000)
    section_rows: int = Field(ge=1, le=1_000)

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        match = _URL.fullmatch(self.source_url)
        if match is None:
            raise ValueError("monthly report URL is not a dane.gov.pl report")
        if f"{match.group(1)}-{int(match.group(2)):02d}" != self.month:
            raise ValueError("monthly report URL names another month")
        if self.published_on.isoformat()[:7] <= self.month:
            raise ValueError("monthly report must be published after its month")
        return self


class MonthlyReport(ReportPin):
    # The raw report is provenance; builds read this extract (#283).
    extract: ReportExtract


class JgpStatistics(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", frozen=True, strict=True
    )

    release_id: Identifier
    dataset: Literal["https://dane.gov.pl/pl/dataset/2581"]
    publisher: Literal["Narodowy Fundusz Zdrowia"]
    license: Literal["CC0 1.0"]
    notices: Annotated[tuple[SourceText, ...], Field(min_length=1, max_length=16)]
    reports: Annotated[tuple[MonthlyReport, ...], Field(min_length=1, max_length=36)]

    @model_validator(mode="after")
    def validate_reports(self) -> Self:
        months = [report.month for report in self.reports]
        if months != sorted(set(months)):
            raise ValueError("monthly reports must be unique and in month order")
        published = [report.published_on for report in self.reports]
        if published != sorted(published):
            raise ValueError("monthly reports must be published in month order")
        return self
