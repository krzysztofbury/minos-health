"""Strict, bounded in-memory JSON boundary for reported encounter facts.

This adapter has no HTTP route, storage or logging. Validation failures are
replaced with fixed errors rather than exposing Pydantic's input-bearing
diagnostics to a caller. Dates use JSON ISO dates; Python coercion is not used.
"""

import json
from datetime import date
from typing import Annotated, ClassVar, cast, override

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from minos.domain.encounter import (
    MAX_PROCEDURES,
    MAX_SECONDARY_DIAGNOSES,
    MAX_SETTLEMENT_PRODUCTS,
    MAX_STAYS,
    Diagnosis,
    Encounter,
    EncounterInputError,
    HospitalizationType,
    Procedure,
    ProductReporting,
    SettlementProduct,
    WardStay,
)

MAX_ENCOUNTER_JSON_BYTES = 256 * 1_024


class RedactedInput(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        strict=True, frozen=True, extra="forbid", hide_input_in_errors=True
    )

    @override
    def __repr__(self) -> str:
        return f"<{type(self).__name__}: redacted>"

    @override
    def __str__(self) -> str:
        return repr(self)


class _CodeFact(RedactedInput):
    code: Annotated[str, Field(min_length=1, max_length=16)]
    stay_id: Annotated[str, Field(min_length=1, max_length=32)] | None = None


class _Diagnosis(_CodeFact):
    hospitalization_level: bool = False

    def to_domain(self) -> Diagnosis:
        return Diagnosis(self.code, self.stay_id, self.hospitalization_level)


class _Procedure(_CodeFact):
    performed_on: date | None = None

    def to_domain(self) -> Procedure:
        return Procedure(self.code, self.performed_on, self.stay_id)


class _SettlementProduct(RedactedInput):
    code: Annotated[str, Field(min_length=1, max_length=16)]
    started_on: date | None = None
    ended_on: date | None = None

    def to_domain(self) -> SettlementProduct:
        return SettlementProduct(self.code, self.started_on, self.ended_on)


class _WardStay(RedactedInput):
    identifier: Annotated[str, Field(min_length=1, max_length=32)]
    department: Annotated[str, Field(min_length=4, max_length=4)]
    admitted_on: date
    discharged_on: date
    service_code: Annotated[str, Field(min_length=1, max_length=16)] | None = None

    def to_domain(self) -> WardStay:
        return WardStay(
            self.identifier,
            self.department,
            self.admitted_on,
            self.discharged_on,
            self.service_code,
        )


class EncounterInput(RedactedInput):
    admitted_on: date
    discharged_on: date
    hospitalization_type: HospitalizationType
    principal_diagnosis: _Diagnosis
    stays: Annotated[tuple[_WardStay, ...], Field(min_length=1, max_length=MAX_STAYS)]
    secondary_diagnoses: Annotated[
        tuple[_Diagnosis, ...], Field(max_length=MAX_SECONDARY_DIAGNOSES)
    ] = ()
    procedures: Annotated[tuple[_Procedure, ...], Field(max_length=MAX_PROCEDURES)] = ()
    birth_date: date | None = None
    age_completed_years: Annotated[int, Field(ge=0, le=200)] | None = None
    discharge_mode: Annotated[int, Field(ge=1, le=99)] | None = None
    settlement_products: Annotated[
        tuple[_SettlementProduct, ...], Field(max_length=MAX_SETTLEMENT_PRODUCTS)
    ] = ()
    product_reporting: ProductReporting = "unknown"

    def to_domain(self) -> Encounter:
        return Encounter(
            admitted_on=self.admitted_on,
            discharged_on=self.discharged_on,
            hospitalization_type=self.hospitalization_type,
            principal_diagnosis=self.principal_diagnosis.to_domain(),
            stays=tuple(stay.to_domain() for stay in self.stays),
            secondary_diagnoses=tuple(
                item.to_domain() for item in self.secondary_diagnoses
            ),
            procedures=tuple(item.to_domain() for item in self.procedures),
            birth_date=self.birth_date,
            age_completed_years=self.age_completed_years,
            discharge_mode=self.discharge_mode,
            settlement_products=tuple(
                item.to_domain() for item in self.settlement_products
            ),
            product_reporting=self.product_reporting,
        )


def unique_object_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise EncounterInputError("duplicate encounter JSON keys")
        result[key] = value
    return result


def parse_encounter(payload: bytes) -> Encounter:
    """Validate once at the boundary; return immutable domain facts only."""
    if type(payload) is not bytes or not 1 <= len(payload) <= MAX_ENCOUNTER_JSON_BYTES:
        raise EncounterInputError("invalid bounded encounter JSON")
    try:
        # Pydantic otherwise silently takes the last duplicate key, allowing
        # conflicting medical facts to pass as an unambiguous report.
        _ = cast(object, json.loads(payload, object_pairs_hook=unique_object_keys))
        return EncounterInput.model_validate_json(payload).to_domain()
    except (
        ValidationError,
        EncounterInputError,
        ValueError,
        RecursionError,
    ):
        raise EncounterInputError("invalid encounter facts") from None
