"""Pinned algorithm preparation contracts, not a complete settlement ruleset."""

import re
from dataclasses import dataclass
from datetime import date
from typing import Literal

from minos.domain.encounter import (
    Diagnosis,
    Procedure,
    ProductReporting,
    SettlementProduct,
)
from minos.domain.komunikat import (
    AlgorithmMetadata,
    DepartmentSpecialty,
    ProcedureRank,
    first_reporting_month,
)
from minos.domain.reference import ReferenceError

MAX_ALGORITHM_ROWS = 20_000
type Outcome = Literal["retained", "excluded", "unresolved", "recorded"]
type CountingPolicy = Literal["ignored", "once_per_code", "label_dependent", "unknown"]
type Stage = Literal[
    "dispatch",
    "ward_exclusion",
    "principal_diagnosis",
    "secondary_diagnosis",
    "procedure",
    "procedure_rank",
    "age",
    "ward_duration",
    "duration_interval",
    "stay_coverage",
    "product_binding",
    "scope",
]
type Reason = Literal[
    "exact_binding",
    "no_specialty_exclusion",
    "all_codes_excluded",
    "service_not_excluded",
    "service_code_missing",
    "service_code_uninterpreted",
    "specialty_predicate_unsupported",
    "stay_retained",
    "stay_excluded",
    "stay_unresolved",
    "attribution_unresolved",
    "no_excluded_stays",
    "hospitalization_level_diagnosis",
    "rank_zero",
    "rank_once_only",
    "rank_label_dependent",
    "rank_missing",
    "not_ranked",
    "reported_years",
    "birth_date_years",
    "birth_date_years_leap_day",
    "ward_duration_calculated",
    "ward_duration_unresolved",
    "one_day_dates_inconsistent",
    "excluded_interval_added",
    "excluded_interval_merged",
    "stay_coverage_complete",
    "stay_coverage_incomplete",
    "product_catalog_unbound",
    "preparation_only",
]


class GrouperError(ReferenceError):
    """Grouping boundary error; never interpolate encounter values."""


class UnknownEngineBinding(GrouperError):
    """No exact, explicitly registered combination exists."""


class UnsupportedAlgorithm(GrouperError):
    """The engine cannot interpret this source algorithm."""


def _identity(value: str) -> None:
    if (
        type(value) is not str
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", value) is None
    ):
        raise GrouperError("invalid execution identity")


@dataclass(frozen=True)
class AlgorithmIdentity:
    release_id: str
    algorithm_sha256: str
    ranks_sha256: str
    specialties_sha256: str

    def __post_init__(self) -> None:
        _identity(self.release_id)
        digests = (self.algorithm_sha256, self.ranks_sha256, self.specialties_sha256)
        if any(
            type(d) is not str or re.fullmatch(r"[0-9a-f]{64}", d) is None
            for d in digests
        ):
            raise GrouperError("invalid algorithm source digest")
        if len(set(digests)) != 3:
            raise GrouperError("algorithm source digests conflict")


@dataclass(frozen=True, repr=False)
class AlgorithmData:
    identity: AlgorithmIdentity
    metadata: AlgorithmMetadata
    ranks: tuple[ProcedureRank, ...]
    specialties: tuple[DepartmentSpecialty, ...]

    def __post_init__(self) -> None:
        if (
            type(self.identity) is not AlgorithmIdentity
            or type(self.metadata) is not AlgorithmMetadata
        ):
            raise GrouperError("invalid algorithm metadata types")
        if (
            type(self.metadata.version) is not str
            or re.fullmatch(
                r"[1-9][0-9]?\.(?:[1-9]|1[0-2])\.20[0-9]{2}", self.metadata.version
            )
            is None
        ):
            raise GrouperError("invalid algorithm version")
        if type(
            self.metadata.reporting_from
        ) is not date or self.metadata.reporting_from != first_reporting_month(
            self.metadata.version
        ):
            raise GrouperError("algorithm reporting month conflicts")
        if (
            type(self.ranks) is not tuple
            or not 1 <= len(self.ranks) <= MAX_ALGORITHM_ROWS
        ):
            raise GrouperError("invalid bounded procedure rank table")
        if (
            type(self.specialties) is not tuple
            or not 1 <= len(self.specialties) <= MAX_ALGORITHM_ROWS
        ):
            raise GrouperError("invalid bounded specialty table")
        worksheet = "SZP " + self.metadata.version.split(".")[2]
        self._validate_ranks(worksheet)
        self._validate_specialties(worksheet)
        if len({r.code for r in self.ranks}) != len(self.ranks) or len(
            {s.code for s in self.specialties}
        ) != len(self.specialties):
            raise GrouperError("duplicate algorithm table code")
        if len({r.row for r in self.ranks}) != len(self.ranks) or len(
            {s.row for s in self.specialties}
        ) != len(self.specialties):
            raise GrouperError("duplicate algorithm evidence row")

    def _validate_ranks(self, worksheet: str) -> None:
        for item in self.ranks:
            if (
                type(item) is not ProcedureRank
                or item.grouper_type != "SZP"
                or item.worksheet != worksheet
            ):
                raise GrouperError("rank belongs to another algorithm scope")
            if type(item.rank) is not int or not 0 <= item.rank <= 4:
                raise GrouperError("invalid procedure rank")
            if (
                type(item.code) is not str
                or re.fullmatch(r"[A-Z0-9][A-Z0-9.\-]{0,15}", item.code) is None
                or type(item.row) is not int
                or not 4 <= item.row <= MAX_ALGORITHM_ROWS + 3
            ):
                raise GrouperError("invalid rank evidence identity")

    def _validate_specialties(self, worksheet: str) -> None:
        for item in self.specialties:
            if (
                type(item) is not DepartmentSpecialty
                or item.grouper_type != "SZP"
                or item.worksheet != worksheet
            ):
                raise GrouperError("specialty belongs to another algorithm scope")
            if (
                type(item.code) is not str
                or re.fullmatch(r"[0-9]{4}", item.code) is None
                or type(item.row) is not int
                or not 4 <= item.row <= MAX_ALGORITHM_ROWS + 3
            ):
                raise GrouperError("invalid specialty evidence identity")
            if (
                type(item.service_codes) is not str
                or type(item.icd_codes) is not str
                or not 1 <= len(item.service_codes) <= 128
                or not 1 <= len(item.icd_codes) <= 128
            ):
                raise GrouperError("unbounded specialty predicate")


@dataclass(frozen=True)
class ExecutionKey:
    # Registry identity is operator-selected, not proof of legal source pairing.
    ruleset_id: str
    algorithm_version: str
    engine_version: str
    reporting_month: date
    sources: AlgorithmIdentity

    def __post_init__(self) -> None:
        for value in (self.ruleset_id, self.algorithm_version, self.engine_version):
            _identity(value)
        if type(self.reporting_month) is not date or self.reporting_month.day != 1:
            raise GrouperError("expected an explicit reporting month")
        if type(self.sources) is not AlgorithmIdentity:
            raise GrouperError("expected exact algorithm source identities")


@dataclass(frozen=True)
class EvidencePointer:
    source: Literal["algorithm", "ranks", "specialties"]
    position: int


@dataclass(frozen=True, repr=False)
class Decision:
    stage: Stage
    ordinal: int
    outcome: Outcome
    reason: Reason
    evidence: tuple[EvidencePointer, ...] = ()
    value: int | None = None


@dataclass(frozen=True, repr=False)
class QualifiedDiagnosis:
    fact: Diagnosis
    outcome: Outcome


@dataclass(frozen=True, repr=False)
class RankedProcedure:
    fact: Procedure
    outcome: Outcome
    rank: int | None
    counting_policy: CountingPolicy


@dataclass(frozen=True, repr=False)
class PreparationResult:
    key: ExecutionKey
    sources: AlgorithmIdentity
    principal_diagnosis: QualifiedDiagnosis
    secondary_diagnoses: tuple[QualifiedDiagnosis, ...]
    procedures: tuple[RankedProcedure, ...]
    completed_years: int | None
    ward_adjusted_days: int | None
    trace: tuple[Decision, ...]
    settlement_products: tuple[SettlementProduct, ...]
    product_reporting: ProductReporting
    product_binding: Literal["unbound"] = "unbound"
    # Always explicit: omitted product/contract/condition bindings cannot turn
    # this into final grouping, even when every preprocessing fact is resolved.
    scope: Literal["algorithm_preparation_only"] = "algorithm_preparation_only"
