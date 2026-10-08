"""Versioned, bounded request envelope. Never retain input-bearing errors."""

import json
from datetime import date
from typing import Annotated, Literal, cast

from pydantic import Field, ValidationError

from minos.domain.encounter import EncounterInputError
from minos.domain.grouping_request import GroupingRequest
from minos.ingest.encounter import (
    MAX_ENCOUNTER_JSON_BYTES,
    EncounterInput,
    RedactedInput,
    unique_object_keys,
)

MAX_GROUPING_REQUEST_BYTES = MAX_ENCOUNTER_JSON_BYTES


class _Request(RedactedInput):
    schema_version: Literal[1]
    encounter: EncounterInput
    service_date: date
    reporting_month: date
    declared_modifiers: Annotated[
        tuple[Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")], ...],
        Field(max_length=64),
    ] = ()


def parse_grouping_request(payload: bytes) -> GroupingRequest:
    if (
        type(payload) is not bytes
        or not 1 <= len(payload) <= MAX_GROUPING_REQUEST_BYTES
    ):
        raise EncounterInputError("invalid bounded grouping request")
    try:
        raw = cast(
            dict[str, object], json.loads(payload, object_pairs_hook=unique_object_keys)
        )
        if type(raw) is not dict or type(raw.get("schema_version")) is not int:
            raise ValueError
        request = _Request.model_validate_json(payload)
        encounter = request.encounter.to_domain()
        if request.reporting_month.day != 1:
            raise ValueError
        if not encounter.admitted_on <= request.service_date <= encounter.discharged_on:
            raise ValueError
        if len(set(request.declared_modifiers)) != len(request.declared_modifiers):
            raise ValueError
        return GroupingRequest(
            encounter,
            request.service_date,
            request.reporting_month,
            frozenset(request.declared_modifiers),
        )
    except ValidationError, EncounterInputError, ValueError, RecursionError:
        raise EncounterInputError("invalid grouping request") from None
