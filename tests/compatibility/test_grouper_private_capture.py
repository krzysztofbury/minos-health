"""Opt-in real pinned algorithm preparation; no legal pairing/eligibility claim."""

import json
import os
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import cast

import pytest

from minos.domain.encounter import Procedure, SettlementProduct
from minos.grouper.registry import EngineBinding, EngineRegistry
from minos.ingest.grouper_algorithm import load_algorithm
from minos.ingest.komunikat_manifest import KomunikatManifest
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
