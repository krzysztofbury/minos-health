"""Reviewed per-report extracts of the NFZ monthly JGP reports (#283).

The raw reports (about 60 MB each) are provenance. Builds read one small
canonical extract per report instead: the processor downloads every live input
on each run, and twelve raw reports would add 743 MB to it. The parser stays
the only raw-to-figure path; an extract is its serialized output, re-validated
here before import.
"""

import json
from typing import Annotated, BinaryIO, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from minos.domain.reference import SourceError
from minos.ingest.jgp_statistics import (
    AGES,
    LEVELS,
    GroupFigure,
    ReportFigures,
    SectionFigure,
    month_before,
)
from minos.ingest.jgp_statistics_manifest import Month, MonthlyReport, ReportPin

SCHEMA = "minos.jgp-statistics-extract.v1"


class _Model(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", frozen=True, strict=True
    )


class _Group(_Model):
    code: Annotated[str, Field(pattern=r"^[A-Z0-9]{2,8}$")]
    hospital_level: Annotated[str, Field(min_length=1, max_length=64)]
    month: Month
    age_months: Literal[0, 1, 12]
    section: Annotated[str, Field(min_length=1, max_length=160)]
    hospitalizations: Annotated[int, Field(ge=0)] | Literal["<5"] | None
    points_ten_thousandths: Annotated[int, Field(ge=0)] | None


class _Section(_Model):
    section: Annotated[str, Field(min_length=1, max_length=160)]
    admission: Literal["planned", "other"]
    character: Literal["procedural", "conservative"]
    month: Month
    age_months: Literal[0, 1, 12]
    hospitalizations: Annotated[int, Field(ge=0)] | Literal["<5"] | None
    mean_stay_hundredths: Annotated[int, Field(ge=0)] | None
    mean_age_tenths: Annotated[int, Field(ge=0)] | None


class _Document(_Model):
    schema_: Literal["minos.jgp-statistics-extract.v1"] = Field(alias="schema")
    report_month: Month
    report_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    groups: Annotated[tuple[_Group, ...], Field(max_length=60_000)]
    sections: Annotated[tuple[_Section, ...], Field(max_length=3_000)]


def _group_key(item: GroupFigure | _Group) -> tuple[str, str, str]:
    return (item.code, item.month, item.hospital_level)


def _section_key(item: SectionFigure | _Section) -> tuple[str, str, str, str]:
    return (item.section, item.month, item.admission, item.character)


def write_extract(figures: ReportFigures, pin: ReportPin) -> bytes:
    """Canonical bytes: sorted rows, integers only, so one report has one extract."""
    document = {
        "schema": SCHEMA,
        "report_month": pin.month,
        "report_sha256": pin.sha256,
        "groups": [
            {
                "code": item.code,
                "hospital_level": item.hospital_level,
                "month": item.month,
                "age_months": item.age_months,
                "section": item.section,
                "hospitalizations": item.hospitalizations,
                "points_ten_thousandths": item.points_ten_thousandths,
            }
            for item in sorted(figures.groups, key=_group_key)
        ],
        "sections": [
            {
                "section": item.section,
                "admission": item.admission,
                "character": item.character,
                "month": item.month,
                "age_months": item.age_months,
                "hospitalizations": item.hospitalizations,
                "mean_stay_hundredths": item.mean_stay_hundredths,
                "mean_age_tenths": item.mean_age_tenths,
            }
            for item in sorted(figures.sections, key=_section_key)
        ],
    }
    return (
        json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode()


def read_extract(source: BinaryIO, pin: MonthlyReport) -> ReportFigures:
    """The figures of a verified extract that names exactly this pinned report."""
    content = source.read(pin.extract.size_bytes + 1)
    if len(content) != pin.extract.size_bytes:
        raise SourceError("JGP statistics extract byte count differs from the pin")
    try:
        document = _Document.model_validate_json(content)
    except ValidationError:
        raise SourceError("JGP statistics extract is invalid") from None
    if (document.report_month, document.report_sha256) != (pin.month, pin.sha256):
        raise SourceError("JGP statistics extract names another report")
    if (len(document.groups), len(document.sections)) != (
        pin.extract.group_figures,
        pin.extract.section_figures,
    ):
        raise SourceError("JGP statistics extract does not match its reviewed counts")
    groups = [_group_key(item) for item in document.groups]
    sections = [_section_key(item) for item in document.sections]
    if groups != sorted(set(groups)) or sections != sorted(set(sections)):
        raise SourceError("JGP statistics extract rows are not unique and sorted")
    months = {month_before(pin.month, age): age for age in AGES}
    ages = [(item.month, item.age_months) for item in document.groups]
    ages += [(item.month, item.age_months) for item in document.sections]
    if any(months.get(month) != age for month, age in ages):
        raise SourceError("JGP statistics extract month and age disagree")
    if any(item.hospital_level not in LEVELS for item in document.groups):
        raise SourceError("JGP statistics extract names an unknown hospital level")
    return ReportFigures(
        tuple(
            GroupFigure(
                item.section,
                item.code,
                item.hospital_level,
                item.month,
                item.age_months,
                item.hospitalizations,
                item.points_ten_thousandths,
            )
            for item in document.groups
        ),
        tuple(
            SectionFigure(
                item.section,
                item.admission,
                item.character,
                item.month,
                item.age_months,
                item.hospitalizations,
                item.mean_stay_hundredths,
                item.mean_age_tenths,
            )
            for item in document.sections
        ),
    )
