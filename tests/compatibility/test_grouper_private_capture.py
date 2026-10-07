"""Opt-in real pinned grouper sources; no eligibility or settlement claim."""

import json
import os
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from typing import cast

import pytest

from minos.domain.encounter import (
    Diagnosis,
    Encounter,
    Procedure,
    SettlementProduct,
    WardStay,
)
from minos.domain.grouper_rules import ModifierBindingUnbound
from minos.domain.jgp_ruleset import NoRulesetForDate
from minos.grouper.pipeline import derive_candidates, evaluate_candidates, final_points
from minos.grouper.registry import EngineBinding, EngineRegistry
from minos.ingest.grouper_algorithm import load_algorithm
from minos.ingest.komunikat_manifest import KomunikatManifest
from minos.store.artifact import dataset_identity
from minos.store.reference import open_reference_bundle
from tests.fixtures.grouper import encounter, key

ROOT = Path(__file__).parents[2]


@pytest.mark.jgp_live
def test_pinned_szp_algorithm_qualifies_excludes_and_ranks_without_cross_scope_fallback(
    tmp_path: Path,
) -> None:
    archive = os.environ.get("MINOS_GROUPER_PRIVATE_ARCHIVE")
    if not archive:
        pytest.skip("private pinned grouper-source archive opt-in is not configured")
    selection = cast(
        dict[str, list[dict[str, object]]],
        json.loads(
            (
                ROOT / "manifests/sources/reference-ranks-public-validation.json"
            ).read_bytes()
        ),
    )
    manifest = KomunikatManifest.model_validate_json(
        json.dumps(selection["komunikaty"][0])
    )
    data = load_algorithm(manifest, Path(archive), staging_directory=tmp_path)
    binding_key = replace(
        key(data.identity), ruleset_id="komunikat-54-szp-preparation-validation"
    )
    engine = EngineRegistry((EngineBinding(binding_key, data),))
    original = encounter()
    icu = replace(
        original.stays[0],
        identifier="icu",
        department="4260",
        admitted_on=date(2026, 10, 3),
        discharged_on=date(2026, 10, 5),
    )
    facts = replace(
        original,
        stays=(*original.stays, icu),
        procedures=(
            Procedure("00.121", date(2026, 10, 2), "main"),
            Procedure("00.121", date(2026, 10, 4), "icu"),
            Procedure("L91", date(2026, 10, 2), "main"),
        ),
        settlement_products=(
            SettlementProduct("5.52.01.0001363", date(2026, 10, 3), date(2026, 10, 5)),
            SettlementProduct("5.52.01.0001363", date(2026, 10, 3), date(2026, 10, 5)),
        ),
        product_reporting="complete",
    )
    result = engine.execute(binding_key, facts)
    assert result.scope == "algorithm_preparation_only"
    assert result.sources == binding_key.sources == data.identity
    assert result.settlement_products == facts.settlement_products
    assert result.product_reporting == "complete"
    assert result.product_binding == "unbound"
    assert result.principal_diagnosis.outcome == "retained"
    assert [(p.outcome, p.rank) for p in result.procedures] == [
        ("retained", 3),
        ("excluded", None),
        ("unresolved", None),
    ]
    assert result.ward_adjusted_days == 7
    assert result.completed_years == 17
    assert (
        next(d for d in result.trace if d.reason == "rank_once_only")
        .evidence[0]
        .position
        == 4047
    )
    assert (
        next(d for d in result.trace if d.reason == "all_codes_excluded")
        .evidence[-1]
        .position
        == 352
    )
    assert result.trace[-1].outcome == "unresolved"
    assert (
        engine.execute(
            binding_key,
            replace(
                facts,
                procedures=tuple(reversed(facts.procedures)),
                stays=tuple(reversed(facts.stays)),
            ),
        )
        == result
    )


def _real_bundle() -> Path:
    bundle = os.environ.get("MINOS_GROUPER_BUNDLE")
    if not bundle:
        pytest.skip("real pinned conditions bundle opt-in is not configured")
    return Path(bundle)


def _hernia(day: date) -> Encounter:
    return Encounter(
        day - timedelta(days=2),
        day,
        "hospitalization",
        Diagnosis("K40.9", hospitalization_level=True),
        (WardStay("surgery", "4500", day - timedelta(days=2), day),),
        procedures=(Procedure("53.03", day - timedelta(days=1), "surgery"),),
        birth_date=date(1975, 3, 3),
    )


@pytest.mark.jgp_live
def test_real_pinned_ruleset_prepares_and_derives_dated_candidates() -> None:
    """#34 acceptance on the reviewed conditions selector, hand-checked cases.

    Attachment 9 (46/2026 after 102): A01 needs a procedure from list A01 and a
    principal diagnosis from list A01 (S06.x); F72 needs a procedure from list
    F72 (row 7991). Candidates are directional only; #35 evaluates conditions.
    """
    with open_reference_bundle(_real_bundle()) as (connection, manifest):
        dataset = dataset_identity(manifest.dataset)
        trauma = Encounter(
            date(2026, 9, 20),
            date(2026, 10, 2),
            "hospitalization",
            Diagnosis("S06.0", hospitalization_level=True),
            (
                WardStay("neuro", "4570", date(2026, 9, 20), date(2026, 9, 22)),
                WardStay("icu", "4260", date(2026, 9, 22), date(2026, 9, 27)),
                WardStay("neuro-2", "4570", date(2026, 9, 27), date(2026, 10, 2)),
            ),
            procedures=(Procedure("01.01", date(2026, 9, 23), "icu"),),
            birth_date=date(1975, 3, 3),
        )
        result = derive_candidates(
            connection,
            dataset,
            trauma,
            service_date=date(2026, 10, 2),
            reporting_month=date(2026, 10, 1),
        )
        assert result.sources.catalog_release_id.startswith(
            "jgp-catalog-46-2026-after-102"
        )
        assert result.sources.characteristics_release_id.startswith(
            "jgp-conditions-46-2026-after-102"
        )
        assert result.sources.algorithm_release_id.startswith("jgp-algorithm-1-1-2026")
        # The ICU procedure is excluded (specialty 4260), so A01 is reached only
        # through its principal-diagnosis list; 12 days minus 5 ICU days is 7.
        assert [(p.fact.code, p.outcome) for p in result.preparation.procedures] == [
            ("01.01", "excluded")
        ]
        assert result.preparation.ward_adjusted_days == 7
        a01 = next(c for c in result.candidates if c.group == "A01")
        assert (a01.basis, a01.matched_nodes, a01.fully_interpreted) == (
            "retained_fact",
            (2,),
            True,
        )
        reordered = replace(trauma, stays=tuple(reversed(trauma.stays)))
        again = derive_candidates(
            connection,
            dataset,
            reordered,
            service_date=date(2026, 10, 2),
            reporting_month=date(2026, 10, 1),
        )
        assert again.candidates == result.candidates
        assert again.preparation.trace == result.preparation.trace

        october = derive_candidates(
            connection,
            dataset,
            _hernia(date(2026, 10, 2)),
            service_date=date(2026, 10, 2),
            reporting_month=date(2026, 10, 1),
        )
        f72 = next(c for c in october.candidates if c.group == "F72")
        assert (f72.basis, f72.fully_interpreted) == ("retained_fact", True)
        assert 7991 in f72.evidence_rows
        assert october.preparation.procedures[0].rank == 3
        # The same encounter in June resolves the original 46/2026 composite.
        june = derive_candidates(
            connection,
            dataset,
            _hernia(date(2026, 6, 12)),
            service_date=date(2026, 6, 12),
            reporting_month=date(2026, 6, 1),
        )
        assert june.sources.catalog_release_id.startswith(
            "jgp-catalog-46-2026-original"
        )
        assert june.sources.characteristics_release_id.startswith(
            "jgp-conditions-46-2026-original"
        )
        assert "F72" in {c.group for c in june.candidates}
        # Attachment 9 coverage starts on 2026-05-01; earlier dates have none.
        with pytest.raises(NoRulesetForDate):
            _ = derive_candidates(
                connection,
                dataset,
                _hernia(date(2026, 4, 20)),
                service_date=date(2026, 4, 20),
                reporting_month=date(2026, 4, 1),
            )


def _statuses(encounter: Encounter, day: date) -> dict[str, str]:
    with open_reference_bundle(_real_bundle()) as (connection, manifest):
        result = evaluate_candidates(
            connection,
            dataset_identity(manifest.dataset),
            encounter,
            service_date=day,
            reporting_month=day.replace(day=1),
        )
    return {item.group: item.status for item in result.evaluations}


@pytest.mark.jgp_live
def test_real_pinned_ruleset_evaluates_the_approved_condition_subset() -> None:
    """#35 paths hand-checked against attachment 9 46/2026 after 102 labels.

    F72: procedure from list F72. PZF09: age below 18 and a list procedure.
    A01: procedure and principal diagnosis from lists A01. T07 ("Leczenie
    zachowawcze urazów"): principal diagnosis from list T07. A49 ("Udar mózgu
    - leczenie > 3 dni"): principal from list A50, three procedures from list
    A49 (distinct codes on distinct days) and a stay over 3 days.
    """
    day = date(2026, 10, 2)
    adult = replace(_hernia(day), product_reporting="complete")
    child = replace(adult, birth_date=date(2021, 3, 3))
    assert _statuses(adult, day)["F72"] == "valid"
    assert _statuses(adult, day)["PZF09"] == "invalid"
    assert _statuses(child, day)["PZF09"] == "valid"
    trauma = Encounter(
        date(2026, 9, 20),
        day,
        "hospitalization",
        Diagnosis("S06.0", hospitalization_level=True),
        (
            WardStay("neuro", "4570", date(2026, 9, 20), date(2026, 9, 22)),
            WardStay("icu", "4260", date(2026, 9, 22), date(2026, 9, 27)),
            WardStay("neuro-2", "4570", date(2026, 9, 27), day),
        ),
        procedures=(Procedure("01.01", date(2026, 9, 23), "icu"),),
        birth_date=date(1975, 3, 3),
    )
    statuses = _statuses(trauma, day)
    assert (statuses["A01"], statuses["T07"]) == ("invalid", "valid")
    imaging = ("88.900", "88.911", "87.033")  # list A49, SZP rank 3
    for days, procedures, status in (
        (2, imaging, "invalid"),  # stay not over 3 days
        (10, (), "invalid"),  # fewer than three procedures
        (10, imaging, "valid"),
        (10, ("88.900", "88.900", "88.900"), "invalid"),  # one code, counted once
    ):
        admitted = day - timedelta(days=days)
        stroke = Encounter(
            admitted,
            day,
            "hospitalization",
            Diagnosis("I63.9", hospitalization_level=True),
            (WardStay("neuro", "4220", admitted, day),),
            procedures=tuple(
                Procedure(code, admitted + timedelta(days=offset), "neuro")
                for offset, code in enumerate(procedures)
            ),
            birth_date=date(1950, 1, 1),
            product_reporting="complete",
        )
        assert _statuses(stroke, day)["A49"] == status


@pytest.mark.jgp_live
def test_real_pinned_ruleset_ranks_valid_groups_by_catalog_base_points() -> None:
    """Catalog 1a, 46/2026 after 102: F72 K (2-day) 3959.00, PZF09 E 3598.00
    with no 2-day column. Paragraph 113: both valid, highest value first."""
    day = date(2026, 10, 2)
    child = replace(
        _hernia(day), product_reporting="complete", birth_date=date(2021, 3, 3)
    )
    with open_reference_bundle(_real_bundle()) as (connection, manifest):
        result = evaluate_candidates(
            connection,
            dataset_identity(manifest.dataset),
            child,
            service_date=day,
            reporting_month=day.replace(day=1),
        )
        points = {
            c.group: p
            for c, p in zip(result.candidates.candidates, result.points, strict=True)
        }
        assert result.ranking == (("1a", "F72"), ("1a", "PZF09"))
        assert (points["F72"].measure, points["F72"].total_hundredths) == (
            "two_day_stay",
            395900,
        )
        assert (
            points["PZF09"].reason,
            points["PZF09"].total_hundredths,
        ) == ("short_stay_column_blank", 359800)
        with pytest.raises(ModifierBindingUnbound):
            final_points(connection, result)
