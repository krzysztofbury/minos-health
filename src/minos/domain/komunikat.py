"""Source-preserved algorithm evidence, separate from executable grouping rules."""

from dataclasses import dataclass
from datetime import date
from typing import Literal

type GrouperType = Literal["SZP", "AOS", "REH"]


@dataclass(frozen=True)
class AlgorithmMetadata:
    version: str
    reporting_from: date
    version_text: str
    paragraph: int


@dataclass(frozen=True)
class ProcedureRank:
    grouper_type: GrouperType
    code: str
    source_title: str
    rank: int
    worksheet: str
    row: int


@dataclass(frozen=True)
class DepartmentSpecialty:
    grouper_type: GrouperType
    code: str
    service_codes: str
    icd_codes: str
    worksheet: str
    row: int
