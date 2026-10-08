"""Shared in-memory grouping adapter and stdin-only CLI command."""

import json
import sqlite3
import sys
from collections.abc import Container
from pathlib import Path
from typing import cast

from minos.cli.grouping_output import (
    DISCLAIMER,
    NEAR_MISS_NOTICE,
    SCHEMA,
    final_output,
    human_output,
    node_output,
    points_output,
    reason_text,
)
from minos.domain.encounter import EncounterInputError
from minos.domain.grouper import GrouperError
from minos.domain.grouper_rules import ModifierBindingUnbound
from minos.domain.grouping_request import GroupingRequest
from minos.domain.jgp_ruleset import NoRulesetForDate
from minos.domain.reference import QueryLimitError, QueryTimeoutError, ReferenceError
from minos.grouper.budget import evaluation_budget
from minos.grouper.pipeline import evaluate_candidates, final_points
from minos.ingest.grouping_request import (
    MAX_GROUPING_REQUEST_BYTES,
    parse_grouping_request,
)
from minos.store.artifact import ArtifactManifest, dataset_identity
from minos.store.query_budget import query_budget
from minos.store.reference import open_reference_bundle

LIMITATIONS = [
    "Ocena obejmuje zatwierdzony podzbiór reguł. Warunki nieobsługiwane pozostają nierozstrzygnięte.",
    "Grupy bez dopasowania listy kierunkowej nie są oceniane; brak kandydata nie dowodzi wykluczenia.",
    "Wycena stosuje założenia właściciela z 2026-10-07: MODTAR, mnożenie współczynników i zaokrąglenie raz do 0,01 punktu.",
]


NOT_EVALUATED = {
    "no_directional_requirement": "Brak interpretowalnego wymagania kierunkowego.",
    "no_directional_match": "Brak dopasowania zachowanego lub nierozstrzygniętego faktu do list kierunkowych. Nie oceniano kwalifikacji.",
    "no_characteristics_group": "Brak grupy w wybranych charakterystykach. Nie oceniano kwalifikacji.",
}


def _not_evaluated(
    keys: list[tuple[str, str]],
    underivable: Container[tuple[str, str]],
    not_matched: Container[tuple[str, str]],
) -> list[dict[str, object]]:
    """One entry per reason: the catalog has hundreds of groups outside the
    candidates, and per-group explanations dominated every response."""
    by_reason: dict[str, dict[str, list[str]]] = {}
    for key in keys:
        reason = (
            "no_directional_requirement"
            if key in underivable
            else "no_directional_match"
            if key in not_matched
            else "no_characteristics_group"
        )
        by_reason.setdefault(reason, {}).setdefault(key[0], []).append(key[1])
    return [
        {"reason": reason, "explanation": text, "groups": by_reason[reason]}
        for reason, text in NOT_EVALUATED.items()
        if reason in by_reason
    ]


def evaluate_json(
    connection: sqlite3.Connection, manifest: ArtifactManifest, request: GroupingRequest
) -> dict[str, object]:
    """One caller-owned verified lease; no input/output storage or logging."""
    answer: dict[str, object] = {
        "schema": SCHEMA,
        "schema_version": 1,
        "status": "evaluated",
        "dataset_id": dataset_identity(manifest.dataset),
        "database_sha256": manifest.database_sha256,
        "service_date": request.service_date.isoformat(),
        "reporting_month": request.reporting_month.isoformat(),
        "disclaimer": DISCLAIMER,
        "limitations": LIMITATIONS.copy(),
        "choice": "provider",
        "groups": [],
        "ranking": [],
        "near_misses": [],
        "near_miss_coverage": "single_reported_age_duration_or_discharge_fact_in_derived_candidates",
    }
    facts = request.encounter
    if facts.product_reporting != "complete" or facts.settlement_products:
        answer.update(
            status="unsupported_product_report", tariff_status="not_evaluated"
        )
        answer["limitations"] = [
            *LIMITATIONS,
            "Wyłączenia produktów nie są obsługiwane (#145). Ocena wymaga potwierdzonego kompletnego raportu bez produktów rozliczeniowych.",
        ]
        return answer
    if not manifest.dataset.characteristics or not manifest.dataset.komunikaty:
        raise GrouperError("grouping sources unavailable")
    with (
        evaluation_budget(),
        query_budget(connection, seconds=3, operations=20_000_000),
    ):
        result = evaluate_candidates(
            connection,
            cast(str, answer["dataset_id"]),
            facts,
            service_date=request.service_date,
            reporting_month=request.reporting_month,
        )
        try:
            finals = final_points(
                connection, manifest.dataset, result, request.declared_modifiers
            )
            tariff_status = "bound"
        except ModifierBindingUnbound:
            finals, tariff_status = (), "unbound"
        final_by_group = {(p.catalog, p.group): p for p in finals}
        source = result.candidates.sources
        metadata = cast(
            list[tuple[str, str, str, str]],
            connection.execute(
                """SELECT catalog, code, name, product_code FROM group_definition
            WHERE release_id=? AND service_from<=? AND (service_to IS NULL OR service_to>=?)
            ORDER BY catalog, code, source_row LIMIT 4001""",
                (
                    source.catalog_release_id,
                    request.service_date.isoformat(),
                    request.service_date.isoformat(),
                ),
            ).fetchall(),
        )
        if len(metadata) > 4000:
            raise QueryLimitError("grouping catalog exceeds limit")
        names = {
            (catalog, code): (name, product)
            for catalog, code, name, product in metadata
        }
        groups: list[dict[str, object]] = []
        for candidate, evaluated, points in zip(
            result.candidates.candidates, result.evaluations, result.points, strict=True
        ):
            key = evaluated.catalog, evaluated.group
            final = final_by_group.get(key)
            title, product = names.get(key, (evaluated.group, ""))
            groups.append(
                {
                    "catalog": evaluated.catalog,
                    "group": evaluated.group,
                    "title": title,
                    "product_code": product or None,
                    "status": evaluated.status,
                    "conditions": evaluated.conditions,
                    "routing": evaluated.routing,
                    "routing_trace": {
                        "phase": "routing",
                        "outcome": evaluated.routing_trace.outcome,
                        "reason": evaluated.routing_trace.reason,
                        "procedural": evaluated.routing_trace.procedural,
                        "procedure_indexes": list(
                            evaluated.routing_trace.procedure_indexes
                        ),
                        "duration_days": evaluated.routing_trace.duration_days,
                        "algorithm_paragraphs": list(
                            evaluated.routing_trace.algorithm_paragraphs
                        ),
                    }
                    if evaluated.routing_trace
                    else None,
                    "reasons": list(evaluated.reasons),
                    "explanations": [
                        reason_text(reason) for reason in evaluated.reasons
                    ],
                    "candidate_basis": candidate.basis,
                    "matched_nodes": list(candidate.matched_nodes),
                    "matched_lists": list(candidate.matched_lists),
                    "candidate_rows": list(candidate.evidence_rows),
                    "roots": list(evaluated.roots),
                    "catalog_row": evaluated.catalog_row,
                    "trace": [node_output(event) for event in evaluated.nodes],
                    "base_points": points_output(points),
                    "final_points": final_output(final) if final is not None else None,
                    "unapplied_declared_modifiers": sorted(
                        request.declared_modifiers
                        - {d.symbol for d in final.decisions if d.outcome == "applied"}
                    )
                    if final is not None
                    else sorted(request.declared_modifiers),
                }
            )
        preparation = result.candidates.preparation
        evaluated_keys = {(g.catalog, g.group) for g in result.evaluations}
        underivable_keys = {
            (catalog, group)
            for catalog, group, _reason in result.candidates.underivable
        }
        not_matched_keys = set(result.candidates.not_matched)
        answer.update(
            groups=groups,
            tariff_status=tariff_status,
            ranking=[{"catalog": p.catalog, "group": p.group} for p in finals]
            if tariff_status == "bound"
            else [
                {"catalog": catalog, "group": code} for catalog, code in result.ranking
            ],
            ranking_basis="final_points" if tariff_status == "bound" else "base_points",
            not_evaluated=_not_evaluated(
                sorted(set(names) - evaluated_keys), underivable_keys, not_matched_keys
            ),
            sources={
                "catalog_release_id": source.catalog_release_id,
                "characteristics_release_id": source.characteristics_release_id,
                "icd9_release_id": source.icd9_release_id,
                "icd10_release_id": source.icd10_release_id,
                "algorithm_release_id": source.algorithm_release_id,
                "algorithm_version": preparation.key.algorithm_version,
                "engine_version": preparation.key.engine_version,
                "algorithm_sha256": preparation.sources.algorithm_sha256,
                "ranks_sha256": preparation.sources.ranks_sha256,
                "specialties_sha256": preparation.sources.specialties_sha256,
                "catalog_knowledge_as_of": str(
                    manifest.dataset.catalog_knowledge_as_of
                ),
                "characteristics_knowledge_as_of": str(
                    manifest.dataset.characteristics_knowledge_as_of
                ),
                "tariff_bindings": [
                    {
                        "catalog_release_id": b.catalog_release_id,
                        "modifier_release_id": b.modifier_release_id,
                        "overlay_sha256": b.overlay.sha256,
                        "knowledge_from": b.knowledge_from.isoformat(),
                    }
                    for b in manifest.dataset.modifier_bindings
                    if b.catalog_release_id == source.catalog_release_id
                ],
            },
            declared_modifiers=sorted(request.declared_modifiers),
            preparation={
                "completed_years": preparation.completed_years,
                "admitted_on": facts.admitted_on.isoformat(),
                "discharged_on": facts.discharged_on.isoformat(),
                "hospitalization_type": facts.hospitalization_type,
                "discharge_mode": facts.discharge_mode,
                "stays": [
                    {
                        "identifier": s.identifier,
                        "department": s.department,
                        "admitted_on": s.admitted_on.isoformat(),
                        "discharged_on": s.discharged_on.isoformat(),
                        "service_code": s.service_code,
                    }
                    for s in sorted(
                        facts.stays,
                        key=lambda s: (s.admitted_on, s.discharged_on, s.identifier),
                    )
                ],
                "ward_adjusted_days": preparation.ward_adjusted_days,
                "principal_diagnosis": {
                    "code": preparation.principal_diagnosis.fact.code,
                    "outcome": preparation.principal_diagnosis.outcome,
                    "stay_id": preparation.principal_diagnosis.fact.stay_id,
                    "hospitalization_level": preparation.principal_diagnosis.fact.hospitalization_level,
                },
                "secondary_diagnoses": [
                    {
                        "code": d.fact.code,
                        "outcome": d.outcome,
                        "stay_id": d.fact.stay_id,
                        "hospitalization_level": d.fact.hospitalization_level,
                    }
                    for d in preparation.secondary_diagnoses
                ],
                "procedures": [
                    {
                        "code": p.fact.code,
                        "performed_on": p.fact.performed_on.isoformat()
                        if p.fact.performed_on
                        else None,
                        "stay_id": p.fact.stay_id,
                        "outcome": p.outcome,
                        "rank": p.rank,
                        "counting_policy": p.counting_policy,
                    }
                    for p in preparation.procedures
                ],
                "trace": [
                    {
                        "phase": d.stage,
                        "ordinal": d.ordinal,
                        "outcome": d.outcome,
                        "reason": d.reason,
                        "explanation": reason_text(d.reason),
                        "value": d.value,
                        "evidence": [
                            {"source": p.source, "position": p.position}
                            for p in d.evidence
                        ],
                    }
                    for d in preparation.trace
                ],
            },
            underivable=[
                {"catalog": catalog, "group": code, "reason": reason}
                for catalog, code, reason in result.candidates.underivable
            ],
            near_misses=[
                {
                    "catalog": m.catalog,
                    "group": m.group,
                    "node": m.node,
                    "fact": m.fact,
                    "explanation": NEAR_MISS_NOTICE,
                }
                for m in result.near_misses
            ],
        )
        return answer


def execute_grouping(data: Path, *, output_json: bool) -> int:
    try:
        request = parse_grouping_request(
            sys.stdin.buffer.read(MAX_GROUPING_REQUEST_BYTES + 1)
        )
        with open_reference_bundle(data) as (connection, manifest):
            answer = evaluate_json(connection, manifest, request)
        print(
            json.dumps(answer, ensure_ascii=False, sort_keys=True)
            if output_json
            else human_output(answer)
        )
        return 0
    except EncounterInputError:
        code, status = "invalid_request", 2
    except NoRulesetForDate:
        code, status = "no_ruleset_for_date", 3
    except QueryTimeoutError, QueryLimitError:
        code, status = "evaluation_limit", 4
    except ReferenceError, OSError, ValueError:
        code, status = "unavailable", 3
    print(
        json.dumps({"schema": SCHEMA, "status": "error", "error": {"code": code}})
        if output_json
        else f"minos: {code}",
        file=sys.stderr,
    )
    return status
