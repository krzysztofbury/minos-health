"""Reported-fact boundary checks, not clinical grouper acceptance fixtures."""

import json
import traceback
from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime
from typing import cast

import pytest

from minos.domain.encounter import (
    MAX_PROCEDURES,
    MAX_SECONDARY_DIAGNOSES,
    MAX_SETTLEMENT_PRODUCTS,
    MAX_STAYS,
    Diagnosis,
    Encounter,
    EncounterInputError,
    Procedure,
    ProductReporting,
    SettlementProduct,
    WardStay,
)
from minos.ingest.encounter import MAX_ENCOUNTER_JSON_BYTES, parse_encounter


def reported_json() -> dict[str, object]:
    return {
        "admitted_on": "2026-01-01",
        "discharged_on": "2026-01-04",
        "hospitalization_type": "hospitalization",
        "principal_diagnosis": {"code": "A00", "stay_id": "ward-a"},
        "secondary_diagnoses": [{"code": "B00"}],
        "procedures": [
            {"code": "00.121", "performed_on": "2026-01-02", "stay_id": "ward-a"},
            {"code": "00.121", "performed_on": "2026-01-03", "stay_id": "ward-a"},
            {"code": "01.01"},
        ],
        "birth_date": "1990-02-03",
        "stays": [
            {
                "identifier": "ward-a",
                "department": "4000",
                "service_code": "01.0000.001.02",
                "admitted_on": "2026-01-01",
                "discharged_on": "2026-01-04",
            }
        ],
    }


def encounter() -> Encounter:
    return parse_encounter(json.dumps(reported_json()).encode())


def test_json_boundary_preserves_reported_facts_without_deduplication() -> None:
    result = encounter()
    assert result == Encounter(
        date(2026, 1, 1),
        date(2026, 1, 4),
        "hospitalization",
        Diagnosis("A00", "ward-a"),
        (
            WardStay(
                "ward-a", "4000", date(2026, 1, 1), date(2026, 1, 4), "01.0000.001.02"
            ),
        ),
        (Diagnosis("B00"),),
        (
            Procedure("00.121", date(2026, 1, 2), "ward-a"),
            Procedure("00.121", date(2026, 1, 3), "ward-a"),
            Procedure("01.01"),
        ),
        birth_date=date(1990, 2, 3),
    )
    assert result.age_completed_years is None
    assert result.discharge_mode is None
    attribute = "code"
    with pytest.raises(FrozenInstanceError):
        setattr(result.procedures[0], attribute, "99.99")


@pytest.mark.parametrize("years", [0, 18, 200])
def test_completed_years_are_an_alternative_not_an_invented_birth_date(
    years: int,
) -> None:
    data = reported_json()
    del data["birth_date"]
    data["age_completed_years"] = years
    result = parse_encounter(json.dumps(data).encode())
    assert result.birth_date is None
    assert result.age_completed_years == years


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("admitted_on", "2026-01-05"),
        ("admitted_on", "2026-01-01T00:00:00"),
        ("discharged_on", "2026-01-00"),
        ("birth_date", "2026-01-02"),
        ("birth_date", None),
        ("age_completed_years", 35),
        ("hospitalization_type", "outpatient"),
        ("discharge_mode", True),
        ("discharge_mode", "9"),
        ("discharge_mode", 0),
        ("stays", []),
        ("procedures", [{"code": "00.121", "performed_on": "2025-12-31"}]),
        ("procedures", [{"code": "00.121", "stay_id": "unknown"}]),
        ("principal_diagnosis", {"code": "A00", "stay_id": "unknown"}),
        ("principal_diagnosis", {"code": "a00"}),
        ("principal_diagnosis", {"code": "A00\n"}),
        ("principal_diagnosis", {"code": "A" * 17}),
        ("principal_diagnosis", {"code": "A00", "narrative": "private fact"}),
        ("patient_name", "private fact"),
        ("product_reporting", True),
        ("product_reporting", "absent"),
        ("settlement_products", None),
        ("settlement_products", [{"code": "5.52.01.0001363", "exclusion_type": "W"}]),
        (
            "settlement_products",
            [{"code": "5.52.01.0001363", "started_on": "2025-12-31"}],
        ),
        (
            "settlement_products",
            [{"code": "5.52.01.0001363", "ended_on": "2026-01-05"}],
        ),
        (
            "settlement_products",
            [{"code": "5.52.01.0001363", "started_on": "2026-01-02T00:00:00"}],
        ),
        (
            "settlement_products",
            [
                {
                    "code": "5.52.01.0001363",
                    "started_on": "2026-01-03",
                    "ended_on": "2026-01-02",
                }
            ],
        ),
    ],
)
def test_invalid_or_coerced_json_facts_fail(field: str, value: object) -> None:
    data = reported_json()
    data[field] = value
    with pytest.raises(EncounterInputError, match="^invalid encounter facts$"):
        _ = parse_encounter(json.dumps(data).encode())


@pytest.mark.parametrize("years", [-1, 201, True, "18", 18.0])
def test_invalid_completed_years_fail_without_coercion(years: object) -> None:
    data = reported_json()
    del data["birth_date"]
    data["age_completed_years"] = years
    with pytest.raises(EncounterInputError):
        _ = parse_encounter(json.dumps(data).encode())


def test_stay_attribution_and_timeline_validation_without_repair() -> None:
    original = encounter()
    stay = original.stays[0]
    with pytest.raises(EncounterInputError, match="duplicate stay"):
        _ = replace(original, stays=(stay, stay))
    with pytest.raises(EncounterInputError, match="outside the episode"):
        _ = replace(original, stays=(replace(stay, admitted_on=date(2025, 12, 31)),))
    with pytest.raises(EncounterInputError, match="outside its attributed stay"):
        _ = replace(original, stays=(replace(stay, admitted_on=date(2026, 1, 3)),))
    with pytest.raises(EncounterInputError, match="stay ends before"):
        _ = replace(stay, discharged_on=date(2025, 12, 31))
    # The source algorithm explicitly discusses overlapping/adjacent stays.
    overlapping = replace(stay, identifier="ward-b", admitted_on=date(2026, 1, 2))
    assert replace(original, stays=(stay, overlapping)).stays == (stay, overlapping)
    same_day = WardStay("ward-a", "4000", date(2026, 1, 1), date(2026, 1, 1))
    assert replace(
        original, discharged_on=date(2026, 1, 1), stays=(same_day,), procedures=()
    ).stays == (same_day,)


def test_domain_cannot_bypass_immutability_or_strict_fact_types() -> None:
    original = encounter()
    with pytest.raises(EncounterInputError, match="bounded procedures"):
        _ = replace(
            original,
            procedures=cast(
                tuple[Procedure, ...], cast(object, list(original.procedures))
            ),
        )
    with pytest.raises(EncounterInputError, match="bounded procedures"):
        _ = replace(
            original, procedures=cast(tuple[Procedure, ...], (Diagnosis("A00"),))
        )
    with pytest.raises(EncounterInputError, match="calendar date"):
        _ = replace(original, admitted_on=datetime(2026, 1, 1))
    with pytest.raises(EncounterInputError, match="completed years"):
        _ = replace(original, birth_date=None, age_completed_years=True)
    with pytest.raises(EncounterInputError, match="discharge mode"):
        _ = replace(original, discharge_mode=True)
    with pytest.raises(EncounterInputError, match="department specialty"):
        _ = replace(original.stays[0], department="4000\n")


@pytest.mark.parametrize(
    ("field", "limit"),
    [
        ("procedures", MAX_PROCEDURES),
        ("secondary_diagnoses", MAX_SECONDARY_DIAGNOSES),
        ("stays", MAX_STAYS),
        ("settlement_products", MAX_SETTLEMENT_PRODUCTS),
    ],
)
def test_collection_limits_at_json_and_domain_boundaries(
    field: str, limit: int
) -> None:
    data = reported_json()
    if field == "stays":
        data[field] = [
            {
                "identifier": f"ward-{i}",
                "department": "4000",
                "admitted_on": "2026-01-01",
                "discharged_on": "2026-01-04",
            }
            for i in range(limit)
        ]
        data["principal_diagnosis"] = {"code": "A00"}
        data["procedures"] = []
    else:
        data[field] = [{"code": "00.121" if field == "procedures" else "A00"}] * limit
    result = parse_encounter(json.dumps(data).encode())
    items = cast(tuple[object, ...], getattr(result, field))
    assert len(items) == limit
    if field == "stays":
        extra: object = {
            "identifier": "ward-extra",
            "department": "4000",
            "admitted_on": "2026-01-01",
            "discharged_on": "2026-01-04",
        }
    else:
        extra = {"code": "00.121" if field == "procedures" else "A00"}
    data[field] = [*cast(list[object], data[field]), extra]
    with pytest.raises(EncounterInputError):
        _ = parse_encounter(json.dumps(data).encode())
    with pytest.raises(EncounterInputError):
        if field == "procedures":
            _ = replace(result, procedures=(*result.procedures, result.procedures[0]))
        elif field == "secondary_diagnoses":
            _ = replace(
                result,
                secondary_diagnoses=(
                    *result.secondary_diagnoses,
                    result.secondary_diagnoses[0],
                ),
            )
        elif field == "settlement_products":
            _ = replace(
                result,
                settlement_products=(
                    *result.settlement_products,
                    result.settlement_products[0],
                ),
            )
        else:
            _ = replace(
                result,
                stays=(
                    *result.stays,
                    replace(result.stays[0], identifier="ward-extra"),
                ),
            )


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"{",
        b"null",
        b"[]",
        b"\xff",
        b"[" * 2_000 + b"]" * 2_000,
    ],
)
def test_malformed_oversized_or_ambiguous_json_fails(payload: bytes) -> None:
    with pytest.raises(EncounterInputError):
        _ = parse_encounter(payload)


def test_byte_cap_accepts_exact_limit_and_rejects_otherwise_valid_overflow() -> None:
    payload = json.dumps(reported_json()).encode()
    at_limit = payload + b" " * (MAX_ENCOUNTER_JSON_BYTES - len(payload))
    assert parse_encounter(at_limit) == encounter()
    with pytest.raises(EncounterInputError, match="^invalid bounded encounter JSON$"):
        _ = parse_encounter(at_limit + b" ")


@pytest.mark.parametrize(
    ("original", "ambiguous"),
    [
        (
            '"birth_date": "1990-02-03"',
            '"birth_date": "1990-02-03", "birth_date": "2000-02-03"',
        ),
        ('"code": "A00"', '"code": "A00", "code": "B00"'),
    ],
)
def test_duplicate_keys_fail_in_otherwise_valid_encounters(
    original: str,
    ambiguous: str,
) -> None:
    payload = json.dumps(reported_json())
    # Each value is independently valid. An overwrite would silently accept a
    # different principal diagnosis or age, not merely fail another validator.
    assert original in payload
    with pytest.raises(EncounterInputError):
        _ = parse_encounter(payload.replace(original, ambiguous, 1).encode())


def test_error_and_default_repr_do_not_disclose_facts(
    capsys: pytest.CaptureFixture[str],
) -> None:
    data = reported_json()
    sentinel = "private-patient-sentinel"
    data[sentinel] = sentinel
    try:
        _ = parse_encounter(json.dumps(data).encode())
    except EncounterInputError as error:
        assert sentinel not in "".join(traceback.format_exception(error))
        assert str(error) == "invalid encounter facts"
    else:
        pytest.fail("unexpected acceptance of patient identity")
    result = encounter()
    for fact in (result, result.principal_diagnosis, *result.procedures, *result.stays):
        assert "A00" not in repr(fact)
        assert "00.121" not in str(fact)
        assert "ward-a" not in repr(fact)
        assert "1990" not in repr(fact)
    assert capsys.readouterr() == ("", "")


def test_product_occurrences_dates_and_completeness_are_preserved() -> None:
    data = reported_json()
    assert encounter().product_reporting == "unknown"
    assert encounter().settlement_products == ()
    data["product_reporting"] = "complete"
    assert parse_encounter(json.dumps(data).encode()).product_reporting == "complete"
    data["settlement_products"] = [
        {
            "code": "5.52.01.0001363",
            "started_on": "2026-01-01",
            "ended_on": "2026-01-04",
        },
        {
            "code": "5.52.01.0001363",
            "started_on": "2026-01-01",
            "ended_on": "2026-01-04",
        },
        {"code": "5.52.01.0001363", "ended_on": "2026-01-03"},
        {"code": "5.52.01.0001363", "started_on": "2026-01-03"},
        {"code": "5.52.01.0001363"},
        {
            "code": "5.52.01.0001363",
            "started_on": "2026-01-02",
            "ended_on": "2026-01-02",
        },
    ]
    result = parse_encounter(json.dumps(data).encode())
    assert result.settlement_products == (
        SettlementProduct("5.52.01.0001363", date(2026, 1, 1), date(2026, 1, 4)),
        SettlementProduct("5.52.01.0001363", date(2026, 1, 1), date(2026, 1, 4)),
        SettlementProduct("5.52.01.0001363", None, date(2026, 1, 3)),
        SettlementProduct("5.52.01.0001363", date(2026, 1, 3)),
        SettlementProduct("5.52.01.0001363"),
        SettlementProduct("5.52.01.0001363", date(2026, 1, 2), date(2026, 1, 2)),
    )
    # Complete reporting can still contain unknown dates. It does not classify
    # products, establish W/V semantics or imply a repaired timeline.
    assert result.product_reporting == "complete"
    partial = replace(result, product_reporting="unknown")
    assert partial.settlement_products == result.settlement_products
    for fact in (result, *result.settlement_products):
        assert "5.52.01.0001363" not in repr(fact)
        assert "2026" not in str(fact)
    attribute = "code"
    with pytest.raises(FrozenInstanceError):
        setattr(result.settlement_products[0], attribute, "other")


def test_domain_rejects_invalid_product_types_dates_and_reporting() -> None:
    original = encounter()
    with pytest.raises(EncounterInputError, match="bounded settlement products"):
        _ = replace(
            original,
            settlement_products=cast(tuple[SettlementProduct, ...], cast(object, [])),
        )
    with pytest.raises(EncounterInputError, match="bounded settlement products"):
        _ = replace(
            original,
            settlement_products=cast(
                tuple[SettlementProduct, ...], (Procedure("00.1"),)
            ),
        )
    with pytest.raises(EncounterInputError, match="reporting completeness"):
        _ = replace(
            original, product_reporting=cast(ProductReporting, cast(object, True))
        )
    with pytest.raises(EncounterInputError, match="calendar date"):
        _ = SettlementProduct("5.52.01.0001363", datetime(2026, 1, 1))
    with pytest.raises(EncounterInputError, match="ends before start"):
        _ = SettlementProduct("5.52.01.0001363", date(2026, 1, 3), date(2026, 1, 2))
    with pytest.raises(EncounterInputError, match="outside the episode"):
        _ = replace(
            original,
            settlement_products=(
                SettlementProduct("5.52.01.0001363", ended_on=date(2026, 1, 5)),
            ),
        )
