"""Explicit grouping schema v1 and compact human rendering, shared with HTTP.

Do not use dataclass reflection here: new internal fields are not automatically
part of the public protocol. Returned facts belong only in the response.
"""

from collections.abc import Container
from typing import cast

from minos.domain import jgp_conditions as c
from minos.domain.grouper_rules import BasePoints, FinalPoints, NodeVerdict
from minos.domain.grouping_request import (
    GroupingOutcome,
    GroupingRequest,
    UnsupportedProductReport,
)
from minos.store.artifact import ArtifactManifest, dataset_identity

SCHEMA = "minos.grouping.v1"
DISCLAIMER = (
    "Wynik referencyjny według obsługiwanego zakresu reguł, nie decyzja NFZ ani porada medyczna. "
    "Wybór grupy należy do świadczeniodawcy. Sprawdzaj wyłącznie udokumentowaną opiekę; "
    "nie dopisuj rozpoznań ani procedur dla wyższej wyceny."
)
NEAR_MISS_NOTICE = (
    "Sprawdź zgodność podanego faktu z dokumentacją. Zmiana tego faktu mogłaby zmienić "
    "kwalifikację według obsługiwanych reguł; to nie zalecenie zmiany opieki lub kodowania."
)

REASONS = {
    "exact_binding": "Wybrano dokładne powiązanie wersji silnika i źródeł.",
    "no_specialty_exclusion": "Specjalność oddziału nie wymaga wyłączenia.",
    "all_codes_excluded": "Wyłączono wszystkie kody przypisane do tego pobytu.",
    "service_not_excluded": "Zgłoszone świadczenie nie podlega wyłączeniu.",
    "service_code_missing": "Brak kodu świadczenia wymaganego do oceny wyłączenia.",
    "service_code_uninterpreted": "Kod świadczenia nie ma obsługiwanej interpretacji.",
    "specialty_predicate_unsupported": "Warunek specjalności nie jest obsługiwany.",
    "stay_retained": "Zachowano fakt z niewyłączonego pobytu.",
    "stay_excluded": "Wyłączono fakt przypisany do wyłączonego pobytu.",
    "stay_unresolved": "Kwalifikacja pobytu pozostaje nierozstrzygnięta.",
    "attribution_unresolved": "Brak jednoznacznego przypisania faktu do pobytu.",
    "no_excluded_stays": "Nie stwierdzono wyłączonych pobytów.",
    "hospitalization_level_diagnosis": "Rozpoznanie zgłoszono na poziomie hospitalizacji.",
    "rank_zero": "Ranga 0: procedura nie jest brana pod uwagę.",
    "rank_once_only": "Ranga 1-3: kod zliczany jednokrotnie według reguły.",
    "rank_label_dependent": "Ranga 4: zliczanie zależy od etykiety grupy.",
    "rank_missing": "Brak rangi w wybranym źródle SZP.",
    "not_ranked": "Nie nadano rangi wyłączonej procedurze.",
    "reported_years": "Użyto zgłoszonego wieku w ukończonych latach.",
    "birth_date_years": "Obliczono ukończone lata w dniu przyjęcia.",
    "birth_date_years_leap_day": "Obliczono wiek z uwzględnieniem daty 29 lutego.",
    "ward_duration_calculated": "Obliczono długość pobytu po wyłączeniach oddziałów.",
    "ward_duration_unresolved": "Nie można jednoznacznie obliczyć długości pobytu po wyłączeniach.",
    "excluded_interval_added": "Uwzględniono wyłączony przedział pobytu.",
    "excluded_interval_merged": "Połączono pokrywające się wyłączone przedziały.",
    "stay_coverage_complete": "Zgłoszone pobyty pokrywają hospitalizację.",
    "stay_coverage_incomplete": "Zgłoszone pobyty nie pokrywają całej hospitalizacji.",
    "product_catalog_unbound": "Brak obsługi wyłączeń produktów rozliczeniowych.",
    "preparation_only": "Zakończono etap przygotowania faktów, poprzedzający ocenę grup.",
    "list_member": "Zgłoszony kod spełnia wymaganie listy.",
    "no_list_member": "Brak zachowanego kodu spełniającego wymaganie listy.",
    "unresolved_fact": "Niepewny fakt lub nierozwinięta rodzina kodów.",
    "age_compared": "Porównano ukończone lata w dniu przyjęcia z progiem reguły.",
    "duration_compared": "Porównano czas pobytu po wyłączeniach z progami reguły.",
    "discharge_mode_compared": "Porównano zgłoszony tryb wypisu z regułą.",
    "count_reached": "Osiągnięto wymaganą liczbę procedur według polityki zliczania.",
    "count_below_required": "Liczba kwalifikowanych procedur jest poniżej wymaganej.",
    "count_needs_unresolved": "Wymagana liczba zależy od nierozstrzygniętych procedur.",
    "all_children_met": "Wszystkie wymagane części są spełnione.",
    "child_met": "Co najmniej jedna alternatywa jest spełniona.",
    "child_not_met": "Co najmniej jedna wymagana część nie jest spełniona.",
    "no_child_met": "Żadna alternatywa nie jest spełniona.",
    "child_undetermined": "Wynik zależy od nierozstrzygniętej części.",
    "same_fact_counted_twice": "Ten sam kod nie może spełnić obu wymagań.",
    "unsupported_extraction": "Treść warunku nie ma obsługiwanej interpretacji.",
    "shared_fact_unreviewed": "Współdzielenie faktów między wymaganiami wymaga interpretacji.",
    "contract_scope_unknown": "Brak zakresu umowy potrzebnego do oceny ścieżki zachowawczej.",
    "minor_procedure_short_stay": "Mały zabieg i krótki pobyt wykluczają tę ścieżkę zachowawczą.",
    "conservative_allowed": "Ścieżka zachowawcza jest dopuszczona.",
    "routing_not_restricted": "Brak ograniczenia ścieżki dla tej grupy.",
    "not_in_governing_catalog": "Brak grupy w katalogu obowiązującym w dniu świadczenia.",
    "catalog_rows_ambiguous": "Więcej niż jeden wiersz katalogu obowiązuje w tym dniu.",
    "roots_as_alternatives": "Korzenie warunków oceniono jako alternatywy.",
    "no_reference_root": "Brak korzenia warunków do oceny.",
    "duration_unresolved": "Czas dla grupowania jest nieznany; sprawdź pobyty i raport produktów.",
    "rank_2_duration_unknown": "Ranga 2 wymaga znanego czasu pobytu.",
    "rank_1_role_unreviewed": "Rola procedury rangi 1 w tym wymaganiu jest nierozstrzygnięta.",
    "unresolved_procedure": "Nierozstrzygnięta procedura uniemożliwia ocenę ścieżki.",
    "age_unknown": "Brak wieku o wymaganej dokładności.",
    "age_unit_not_executable": "Jednostka wieku nie jest obsługiwana.",
    "discharge_mode_unknown": "Nie podano trybu wypisu.",
    "distinct_date_rule_unreviewed": "Wyjątek od zliczania kodów na różnych datach nie jest obsługiwany.",
    "assignment_unbounded": "Przekroczono limit przyporządkowania faktów do wymagań.",
    "count_scope_unreviewed": "Zakres łącznego zliczania nie jest obsługiwany.",
    "standard_value": "Standardowa wartość katalogowa dla trybu hospitalizacji.",
    "short_stay_value": "Wartość katalogowa dla krótkiego pobytu.",
    "short_stay_column_blank": "Pusta kolumna krótkiego pobytu: użyto wartości standardowej.",
    "governing_catalog_row_not_unique": "Brak jednoznacznego wiersza katalogu.",
    "mode_value_absent": "Katalog nie podaje wartości dla tego trybu.",
    "one_day_dates_inconsistent": "Leczenie jednego dnia wymaga tej samej daty przyjęcia i wypisu.",
    "one_day_treatment": "Wartość katalogowa leczenia jednego dnia.",
    "ward_duration_not_positive": "Czas po wyłączeniach nie jest dodatni.",
    "day_stay_hours_unknown": "Katalog 1ae wymaga czasu w godzinach, którego nie zgłoszono.",
    "declared": "Modyfikator zadeklarowany przez użytkownika i zgodny z warunkami wieku.",
    "not_declared": "Możliwy modyfikator: nie zadeklarowano wymaganych faktów.",
    "patient_age_unknown": "Nieznany wiek dla modyfikatora.",
    "patient_age_outside_clause": "Wiek nie spełnia warunku modyfikatora.",
}


def reason_text(reason: str) -> str:
    if reason.startswith("not_executable:"):
        return "Ten rodzaj warunku nie jest obsługiwany: " + reason.split(":", 1)[1]
    return REASONS.get(reason, reason.replace("_", " "))


def rule_parameters(rule: c.Condition | None) -> dict[str, object]:
    if rule is None:
        return {}
    p = rule.payload
    if isinstance(p, c.Presence):
        return {"selector_id": p.selector_id, "role": p.role}
    if isinstance(p, c.Count):
        return {
            "selector_id": p.selector_id,
            "required_count": p.required_count,
            "comparison": p.comparison,
            "counting_policy": p.counting_policy,
            "included_node_ids": list(p.included_node_ids),
        }
    if isinstance(p, c.Age):
        return {
            "comparison": p.comparison,
            "threshold": p.threshold,
            "unit": p.unit,
            "anchor": p.anchor,
        }
    if isinstance(p, c.EpisodeDuration):
        return {
            "bounds": [{"comparison": b.comparison, "days": b.days} for b in p.bounds]
        }
    if isinstance(p, c.DischargeMode):
        return {"allowed_reported_codes": list(p.allowed_reported_codes)}
    if isinstance(p, c.Expression):
        return {"children": list(p.children), "member_rows": list(p.member_rows)}
    return {"interpretation": "unsupported"}


def node_output(event: NodeVerdict) -> dict[str, object]:
    return {
        "node": event.node,
        "verdict": event.verdict,
        "reason": event.reason,
        "explanation": reason_text(event.reason),
        "kind": event.rule.kind if event.rule else None,
        "source_rows": list(event.rule.source_rows) if event.rule else [],
        "parameters": rule_parameters(event.rule),
        "matched_codes": list(event.matched_codes),
        "observed": dict(event.observed),
        "phase": "condition",
        "consulted": [
            {"facts": kind, "index": index} for kind, index in event.consulted
        ],
        "selector": {
            "identity": event.selector.identity,
            "list_ids": list(event.selector.list_ids),
            "list_labels": list(event.selector.list_labels),
            "source_rows": list(event.selector.source_rows),
            "families": list(event.selector.families),
            "explicit_codes": sorted(event.selector.codes)
            if not event.selector.list_ids
            else [],
        }
        if event.selector
        else None,
    }


def points_output(points: BasePoints) -> dict[str, object]:
    return {
        "kind": points.kind,
        "determined": points.determined,
        "reason": points.reason,
        "explanation": reason_text(points.reason),
        "measure": points.measure,
        "base_hundredths": points.base_hundredths,
        "extra_days": points.extra_days,
        "extra_hundredths": points.extra_hundredths,
        "total_hundredths": points.total_hundredths,
    }


def final_output(points: FinalPoints) -> dict[str, object]:
    return {
        "total_hundredths": points.total_hundredths,
        "notes": list(points.notes),
        "modifiers": [
            {
                "symbol": d.symbol,
                "provenance": d.provenance,
                "source_citation": d.source_citation,
                "coefficient_millionths": d.coefficient_millionths,
                "outcome": d.outcome,
                "reason": d.reason,
                "explanation": reason_text(d.reason),
                "would_add_hundredths": d.would_add_hundredths,
            }
            for d in points.decisions
        ],
    }


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


def grouping_answer(
    manifest: ArtifactManifest,
    request: GroupingRequest,
    outcome: GroupingOutcome | UnsupportedProductReport,
) -> dict[str, object]:
    """Wire schema v1 of one outcome. The answer echoes reported facts and is
    returned only to the caller; it is never stored or logged."""
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
    if isinstance(outcome, UnsupportedProductReport):
        answer.update(
            status="unsupported_product_report", tariff_status="not_evaluated"
        )
        answer["limitations"] = [
            *LIMITATIONS,
            "Wyłączenia produktów nie są obsługiwane (#145). Ocena wymaga potwierdzonego kompletnego raportu bez produktów rozliczeniowych.",
        ]
        return answer
    result = outcome.result
    finals = outcome.finals
    tariff_status = "bound" if outcome.tariff_bound else "unbound"
    final_by_group = {(p.catalog, p.group): p for p in finals}
    source = result.candidates.sources
    names = outcome.titles
    facts = request.encounter
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
                "explanations": [reason_text(reason) for reason in evaluated.reasons],
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
        (catalog, group) for catalog, group, _reason in result.candidates.underivable
    }
    not_matched_keys = set(result.candidates.not_matched)
    answer.update(
        groups=groups,
        tariff_status=tariff_status,
        ranking=[{"catalog": p.catalog, "group": p.group} for p in finals]
        if tariff_status == "bound"
        else [{"catalog": catalog, "group": code} for catalog, code in result.ranking],
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
            "catalog_knowledge_as_of": str(manifest.dataset.catalog_knowledge_as_of),
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
                        {"source": p.source, "position": p.position} for p in d.evidence
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


def human_output(answer: dict[str, object]) -> str:
    lines = [DISCLAIMER, f"Stan: {answer['status']}", f"Zestaw: {answer['dataset_id']}"]
    lines.extend(cast(list[str], answer.get("limitations", [])))
    preparation = cast(dict[str, object], answer.get("preparation", {}))
    for event in cast(list[dict[str, object]], preparation.get("trace", [])):
        lines.append(
            f"{event['phase']} #{event['ordinal']}: {event['explanation']}; {event['value']}; źródło {event['evidence']}"
        )
    for group in cast(list[dict[str, object]], answer.get("groups", [])):
        base = cast(dict[str, object], group["base_points"])
        lines.append(
            f"{group['catalog']}/{group['group']} {group['title']}: {group['status']}; punkty bazowe (0.01): {base['total_hundredths']}"
        )
        lines.extend(cast(list[str], group["explanations"]))
        for node in cast(list[dict[str, object]], group["trace"]):
            lines.append(
                f"  #{node['node']} {node['verdict']}: {node['explanation']} {node['parameters']} / {node['observed']}; wiersze {node['source_rows']}"
            )
        final = cast(dict[str, object] | None, group["final_points"])
        if final is not None:
            lines.append(
                f"  Punkty z zadeklarowanymi modyfikatorami (0.01): {final['total_hundredths']}"
            )
            lines.extend(cast(list[str], final["notes"]))
            for modifier in cast(list[dict[str, object]], final["modifiers"]):
                lines.append(
                    f"  {modifier['symbol']}: {modifier['outcome']} - {modifier['explanation']}"
                )
    for miss in cast(list[dict[str, object]], answer.get("near_misses", [])):
        lines.append(f"{miss['group']} #{miss['node']}: {NEAR_MISS_NOTICE}")
    return "\n".join(lines)
