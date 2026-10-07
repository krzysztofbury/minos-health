"""Bounded reported episode facts, independent of grouping and serialization.

Missing attribution and dates remain unknown. Codes are exact reported tokens,
not dictionary-validated diagnoses or evidence of eligibility. No patient
identity, narrative, tariff or inferred clinical facts belong in this model.
"""

import re
from dataclasses import dataclass
from datetime import date
from typing import Literal

from minos.domain.reference import ReferenceError

MAX_SECONDARY_DIAGNOSES = 100
MAX_PROCEDURES = 1_000
MAX_STAYS = 100
MAX_SETTLEMENT_PRODUCTS = 1_000
type HospitalizationType = Literal["hospitalization", "planned", "one_day"]
type ProductReporting = Literal["unknown", "complete"]


class EncounterInputError(ReferenceError):
    """Invalid reported facts; messages never interpolate encounter values."""


def _code(value: str) -> None:
    # A token boundary, not a grammar or validity claim for an ICD release.
    if (
        type(value) is not str
        or re.fullmatch(r"[A-Z0-9][A-Z0-9.\-]{0,15}", value) is None
    ):
        raise EncounterInputError("invalid reported code token")


def _stay_id(value: str) -> None:
    if type(value) is not str or re.fullmatch(r"[a-zA-Z0-9_-]{1,32}", value) is None:
        raise EncounterInputError("invalid episode-local stay identifier")


def _date(value: date) -> None:
    if type(value) is not date:
        raise EncounterInputError("expected a calendar date")


@dataclass(frozen=True, repr=False)
class Diagnosis:
    """A stay attribution, an explicit hospitalization-level report, or neither.

    Hospitalization-level SWIAD causes (algorithm paragraph 143) are episode
    facts, not codes of a ward stay. Neither marker means attribution unknown.
    """

    code: str
    stay_id: str | None = None
    hospitalization_level: bool = False

    def __post_init__(self) -> None:
        _code(self.code)
        if self.stay_id is not None:
            _stay_id(self.stay_id)
        if type(self.hospitalization_level) is not bool:
            raise EncounterInputError("invalid diagnosis reporting level")
        if self.hospitalization_level and self.stay_id is not None:
            raise EncounterInputError("diagnosis has two reporting levels")


@dataclass(frozen=True, repr=False)
class Procedure:
    code: str
    performed_on: date | None = None
    stay_id: str | None = None

    def __post_init__(self) -> None:
        _code(self.code)
        if self.performed_on is not None:
            _date(self.performed_on)
        if self.stay_id is not None:
            _stay_id(self.stay_id)


@dataclass(frozen=True, repr=False)
class SettlementProduct:
    """Reported product occurrence, not a caller-selected W/V exclusion rule."""

    code: str
    started_on: date | None = None
    ended_on: date | None = None

    def __post_init__(self) -> None:
        _code(self.code)
        if self.started_on is not None:
            _date(self.started_on)
        if self.ended_on is not None:
            _date(self.ended_on)
        if (
            self.started_on is not None
            and self.ended_on is not None
            and self.ended_on < self.started_on
        ):
            raise EncounterInputError("product ends before start")


@dataclass(frozen=True, repr=False)
class WardStay:
    identifier: str
    department: str
    admitted_on: date
    discharged_on: date
    service_code: str | None = None

    def __post_init__(self) -> None:
        _stay_id(self.identifier)
        if (
            type(self.department) is not str
            or re.fullmatch(r"[0-9]{4}", self.department) is None
        ):
            raise EncounterInputError("expected a department specialty code")
        _date(self.admitted_on)
        _date(self.discharged_on)
        if self.discharged_on < self.admitted_on:
            raise EncounterInputError("stay ends before admission")
        if self.service_code is not None:
            _code(self.service_code)


@dataclass(frozen=True, repr=False)
class Encounter:
    admitted_on: date
    discharged_on: date
    hospitalization_type: HospitalizationType
    principal_diagnosis: Diagnosis
    stays: tuple[WardStay, ...]
    secondary_diagnoses: tuple[Diagnosis, ...] = ()
    procedures: tuple[Procedure, ...] = ()
    birth_date: date | None = None
    age_completed_years: int | None = None
    discharge_mode: int | None = None
    settlement_products: tuple[SettlementProduct, ...] = ()
    # An omitted/partial product report is not evidence that no products exist.
    product_reporting: ProductReporting = "unknown"

    def __post_init__(self) -> None:
        _date(self.admitted_on)
        _date(self.discharged_on)
        if self.discharged_on < self.admitted_on:
            raise EncounterInputError("episode ends before admission")
        if type(
            self.hospitalization_type
        ) is not str or self.hospitalization_type not in (
            "hospitalization",
            "planned",
            "one_day",
        ):
            raise EncounterInputError("unsupported hospitalization type")
        if (self.birth_date is None) == (self.age_completed_years is None):
            raise EncounterInputError(
                "provide birth date or completed years at admission"
            )
        if self.birth_date is not None:
            _date(self.birth_date)
            if self.birth_date > self.admitted_on:
                raise EncounterInputError("birth date is after admission")
        if self.age_completed_years is not None and (
            type(self.age_completed_years) is not int
            or not 0 <= self.age_completed_years <= 200
        ):
            raise EncounterInputError("invalid completed years at admission")
        if self.discharge_mode is not None and (
            type(self.discharge_mode) is not int or not 1 <= self.discharge_mode <= 99
        ):
            raise EncounterInputError("invalid reported discharge mode")
        if type(self.principal_diagnosis) is not Diagnosis:
            raise EncounterInputError("expected a principal diagnosis")
        if (
            type(self.stays) is not tuple
            or not 1 <= len(self.stays) <= MAX_STAYS
            or any(type(stay) is not WardStay for stay in self.stays)
        ):
            raise EncounterInputError("invalid bounded ward stays")
        if (
            type(self.secondary_diagnoses) is not tuple
            or len(self.secondary_diagnoses) > MAX_SECONDARY_DIAGNOSES
            or any(type(item) is not Diagnosis for item in self.secondary_diagnoses)
        ):
            raise EncounterInputError("invalid bounded secondary diagnoses")
        if (
            type(self.procedures) is not tuple
            or len(self.procedures) > MAX_PROCEDURES
            or any(type(item) is not Procedure for item in self.procedures)
        ):
            raise EncounterInputError("invalid bounded procedures")
        self._validate_attribution()
        self._validate_products()

    def _validate_products(self) -> None:
        if type(self.product_reporting) is not str or self.product_reporting not in (
            "unknown",
            "complete",
        ):
            raise EncounterInputError("invalid product reporting completeness")
        if (
            type(self.settlement_products) is not tuple
            or len(self.settlement_products) > MAX_SETTLEMENT_PRODUCTS
            or any(
                type(item) is not SettlementProduct for item in self.settlement_products
            )
        ):
            raise EncounterInputError("invalid bounded settlement products")
        for product in self.settlement_products:
            for endpoint in (product.started_on, product.ended_on):
                if endpoint is not None and not (
                    self.admitted_on <= endpoint <= self.discharged_on
                ):
                    raise EncounterInputError("product is outside the episode")

    def _validate_attribution(self) -> None:
        stays = {stay.identifier: stay for stay in self.stays}
        if len(stays) != len(self.stays):
            raise EncounterInputError("duplicate stay identifiers")
        for stay in self.stays:
            if (
                not self.admitted_on
                <= stay.admitted_on
                <= stay.discharged_on
                <= self.discharged_on
            ):
                raise EncounterInputError("stay is outside the episode")
        # Overlapping and adjacent stays are reported facts, not a timeline to
        # repair here. Their exclusion/duration semantics belong to the engine.
        facts: tuple[Diagnosis | Procedure, ...] = (
            self.principal_diagnosis,
            *self.secondary_diagnoses,
            *self.procedures,
        )
        for fact in facts:
            if fact.stay_id is not None and fact.stay_id not in stays:
                raise EncounterInputError("unknown stay attribution")
            if isinstance(fact, Procedure) and fact.performed_on is not None:
                if not self.admitted_on <= fact.performed_on <= self.discharged_on:
                    raise EncounterInputError("procedure is outside the episode")
                if fact.stay_id is not None:
                    stay = stays[fact.stay_id]
                    if not stay.admitted_on <= fact.performed_on <= stay.discharged_on:
                        raise EncounterInputError(
                            "procedure is outside its attributed stay"
                        )
