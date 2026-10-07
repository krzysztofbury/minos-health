"""Directional candidate derivation over prepared facts; no condition evaluation.

Only facts the preparation retained can make a group a matched candidate.
Unresolved facts (unknown attribution, missing rank) and unexpanded families
can only make it an unresolved candidate. Excluded facts never count.
"""

from minos.domain.grouper import (
    GrouperError,
    Outcome,
    PreparationResult,
    QualifiedDiagnosis,
    RankedProcedure,
)
from minos.domain.grouper_candidates import (
    Basis,
    Candidate,
    CandidateResult,
    Catalog,
    DirectionalIndex,
    DirectionalRequirement,
    GroupingSources,
    Underivable,
)

type _Pools = dict[tuple[str, Outcome], frozenset[str]]


def _diagnoses(
    items: tuple[QualifiedDiagnosis, ...], outcome: Outcome
) -> frozenset[str]:
    return frozenset(item.fact.code for item in items if item.outcome == outcome)


def _procedures(items: tuple[RankedProcedure, ...], outcome: Outcome) -> frozenset[str]:
    return frozenset(item.fact.code for item in items if item.outcome == outcome)


def _pools(preparation: PreparationResult) -> _Pools:
    principal = (preparation.principal_diagnosis,)
    diagnoses = (*principal, *preparation.secondary_diagnoses)
    pools: _Pools = {}
    for outcome in ("retained", "unresolved"):
        pools[("principal", outcome)] = _diagnoses(principal, outcome)
        pools[("any", outcome)] = _diagnoses(diagnoses, outcome)
        pools[("procedure", outcome)] = _procedures(preparation.procedures, outcome)
    return pools


def _match(requirement: DirectionalRequirement, pools: _Pools) -> Basis | None:
    pool = requirement.role if requirement.kind == "diagnosis" else "procedure"
    retained = pools[(pool, "retained")]
    unresolved = pools[(pool, "unresolved")]
    if requirement.codes & retained:
        return "retained_fact"
    in_family = any(
        code == family or code.startswith(family + ".")
        for family in requirement.families
        for code in retained | unresolved
    )
    if requirement.codes & unresolved or in_family:
        return "unresolved_fact"
    return None


def derive(
    preparation: PreparationResult,
    indexes: tuple[DirectionalIndex, ...],
    sources: GroupingSources,
) -> CandidateResult:
    if type(preparation) is not PreparationResult or not 1 <= len(indexes) <= 2:
        raise GrouperError("invalid candidate derivation input")
    if len({index.catalog for index in indexes}) != len(indexes) or any(
        index.characteristics_release_id != sources.characteristics_release_id
        for index in indexes
    ):
        raise GrouperError("directional indexes conflict with grouping sources")
    pools = _pools(preparation)
    candidates: list[Candidate] = []
    underivable: list[tuple[Catalog, str, Underivable]] = []
    for index in sorted(indexes, key=lambda item: item.catalog):
        for group in sorted(index.groups, key=lambda item: item.code):
            if not group.requirements:
                underivable.append(
                    (index.catalog, group.code, "no_directional_requirement")
                )
                continue
            matched = [
                (requirement, basis)
                for requirement in group.requirements
                if (basis := _match(requirement, pools)) is not None
            ]
            if not matched:
                continue
            retained = [r for r, basis in matched if basis == "retained_fact"]
            chosen = retained or [r for r, _ in matched]
            candidates.append(
                Candidate(
                    index.catalog,
                    group.code,
                    "retained_fact" if retained else "unresolved_fact",
                    tuple(sorted(r.node for r in chosen)),
                    tuple(sorted({i for r in chosen for i in r.list_ids})),
                    tuple(sorted({row for r in chosen for row in r.source_rows})),
                    group.fully_interpreted,
                )
            )
    return CandidateResult(sources, preparation, tuple(candidates), tuple(underivable))
