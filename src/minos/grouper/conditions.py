"""Evaluate the approved attachment 9 subset over prepared facts (#35).

Kleene logic: `undetermined` whenever a fact, rank role, contract scope or
interpretation is unknown. Source rules applied (algorithm 1.1.2026):
ranks 1-4 (paragraphs 75-79), two required facts never share one code
(134, 143), conservative-group routing (105-111).
"""

import itertools
from dataclasses import dataclass
from datetime import date

from minos.domain import jgp_conditions as c
from minos.domain.grouper import GrouperError, Outcome, PreparationResult
from minos.domain.grouper_rules import (
    GroupEvaluation,
    GroupRules,
    NodeVerdict,
    RoutingTrace,
    SelectorCodes,
    Status,
    Verdict,
)
from minos.grouper.budget import checkpoint

# Paragraph 134: E11 and N24 may count one code twice on different dates.
DISTINCT_DATE_GROUPS = frozenset({"E11", "N24"})
# Paragraph 111: the conservative-group marker currently used only for N24.
CONSERVATIVE_MARKER_GROUPS = frozenset({"N24"})
MAX_ASSIGNMENTS = 4_096


@dataclass(frozen=True, repr=False)
class Facts:
    principal: tuple[str, Outcome]
    secondary: tuple[tuple[str, Outcome], ...]
    procedures: tuple[tuple[str, Outcome, int | None], ...]
    years: int | None
    # Algorithm duration, known only when no settlement product can change it.
    days: int | None
    # Aligned with `procedures`; a missing date counts as one shared day
    # (paragraph 136). Empty means every date is unknown.
    procedure_dates: tuple[date | None, ...] = ()
    discharge_mode: int | None = None


def facts_from(
    preparation: PreparationResult, discharge_mode: int | None = None
) -> Facts:
    # Bound preparation already subtracted type W stays or left the duration
    # undetermined; unbound products could still change it.
    product_free = preparation.product_reporting == "complete" and (
        not preparation.settlement_products or preparation.product_binding == "bound"
    )
    principal = preparation.principal_diagnosis
    return Facts(
        (principal.fact.code, principal.outcome),
        tuple((d.fact.code, d.outcome) for d in preparation.secondary_diagnoses),
        tuple((p.fact.code, p.outcome, p.rank) for p in preparation.procedures),
        preparation.completed_years,
        preparation.ward_adjusted_days if product_free else None,
        tuple(p.fact.performed_on for p in preparation.procedures),
        discharge_mode,
    )


def _in_family(code: str, families: tuple[str, ...]) -> bool:
    return any(code == f or code.startswith(f + ".") for f in families)


type _Leaf = tuple[Verdict, str, frozenset[str]]


def _diagnosis(role: c.Role | None, selector: SelectorCodes, facts: Facts) -> _Leaf:
    pool: tuple[tuple[str, Outcome], ...]
    if role == "principal":
        pool = (facts.principal,)
    elif role == "secondary":
        pool = facts.secondary
    else:
        pool = (facts.principal, *facts.secondary)
    matched = frozenset(
        code
        for code, outcome in pool
        if outcome == "retained" and code in selector.codes
    )
    if matched:
        return "met", "list_member", matched
    possible = any(
        (outcome == "unresolved" and code in selector.codes)
        or (outcome != "excluded" and _in_family(code, selector.families))
        for code, outcome in pool
    )
    if possible:
        return "undetermined", "unresolved_fact", frozenset()
    return "not_met", "no_list_member", frozenset()


def _procedure(selector: SelectorCodes, facts: Facts) -> _Leaf:
    matched: set[str] = set()
    reason = ""
    for code, outcome, rank in facts.procedures:
        checkpoint()
        listed = code in selector.codes
        if not listed and not _in_family(code, selector.families):
            continue
        if outcome == "excluded":
            continue
        if outcome == "unresolved" or not listed:
            reason = reason or "unresolved_fact"
        elif rank in (3, 4):
            matched.add(code)
        elif rank == 2:
            # Paragraph 77: a minor procedure only for stays under 2 days.
            if facts.days is None:
                reason = reason or "rank_2_duration_unknown"
            elif facts.days <= 1:
                matched.add(code)
        elif rank == 1:
            # Paragraph 76: rank 1 never directs; whether this list is an
            # additional condition is not part of the approved subset.
            reason = reason or "rank_1_role_unreviewed"
    if matched:
        return "met", "list_member", frozenset(matched)
    if reason:
        return "undetermined", reason, frozenset()
    return "not_met", "no_list_member", frozenset()


def _compare(value: int, comparison: c.Comparison, threshold: int) -> bool:
    if comparison == "gt":
        return value > threshold
    if comparison == "ge":
        return value >= threshold
    if comparison == "lt":
        return value < threshold
    return value <= threshold


def _leaf(
    node: c.Condition, selectors: dict[int, SelectorCodes], facts: Facts
) -> _Leaf:
    payload = node.payload
    if node.kind == "diagnosis_presence" and isinstance(payload, c.Presence):
        return _diagnosis(payload.role, selectors[payload.selector_id], facts)
    if node.kind == "procedure_presence" and isinstance(payload, c.Presence):
        return _procedure(selectors[payload.selector_id], facts)
    if node.kind == "patient_age" and isinstance(payload, c.Age):
        if payload.unit != "completed_years":
            return "undetermined", "age_unit_not_executable", frozenset()
        if facts.years is None:
            return "undetermined", "age_unknown", frozenset()
        met = _compare(facts.years, payload.comparison, payload.threshold)
        return ("met" if met else "not_met"), "age_compared", frozenset()
    if node.kind == "episode_duration" and isinstance(payload, c.EpisodeDuration):
        days = facts.days
        if days is None:
            return "undetermined", "duration_unresolved", frozenset()
        met = all(_compare(days, b.comparison, b.days) for b in payload.bounds)
        return ("met" if met else "not_met"), "duration_compared", frozenset()
    if node.kind == "procedure_count" and isinstance(payload, c.Count):
        return _count(payload, selectors[payload.selector_id], facts)
    if node.kind == "discharge_mode" and isinstance(payload, c.DischargeMode):
        if facts.discharge_mode is None:
            return "undetermined", "discharge_mode_unknown", frozenset()
        met = facts.discharge_mode in payload.allowed_reported_codes
        return ("met" if met else "not_met"), "discharge_mode_compared", frozenset()
    return "undetermined", f"not_executable:{node.kind}", frozenset()


type _Occurrence = tuple[str, date | None]


def _matching(occurrences: list[_Occurrence], repeat: bool) -> int:
    """Largest set of occurrences on pairwise distinct days and, unless the
    label allows repeats, pairwise distinct codes (paragraphs 103, 134, 136)."""
    if repeat:
        return len({day for _, day in occurrences})
    edges: dict[str, set[date | None]] = {}
    for code, day in occurrences:
        edges.setdefault(code, set()).add(day)
    owner: dict[date | None, str] = {}
    for code in sorted(edges):
        checkpoint()
        # Iterative augmenting path (Kuhn); bounded by codes x days.
        parent: dict[str, tuple[str, date | None] | None] = {code: None}
        stack = [code]
        found: tuple[str, date | None] | None = None
        while stack and found is None:
            checkpoint()
            current = stack.pop()
            for day in sorted(edges[current], key=lambda d: (d is None, d or date.min)):
                holder = owner.get(day)
                if holder is None:
                    found = (current, day)
                    break
                if holder not in parent:
                    parent[holder] = (current, day)
                    stack.append(holder)
        while found is not None:
            current, day = found
            owner[day] = current
            found = parent[current]
    return len(owner)


def _count(payload: c.Count, selector: SelectorCodes, facts: Facts) -> _Leaf:
    if payload.included_node_ids:
        return "undetermined", "count_scope_unreviewed", frozenset()
    dates = facts.procedure_dates or (None,) * len(facts.procedures)
    firm: list[_Occurrence] = []
    maybe: list[_Occurrence] = []
    for (code, outcome, rank), day in zip(facts.procedures, dates, strict=True):
        listed = code in selector.codes
        if outcome == "excluded" or (
            not listed and not _in_family(code, selector.families)
        ):
            continue
        short = facts.days is not None and facts.days <= 1
        if (
            listed
            and outcome == "retained"
            and (rank in (3, 4) or (rank == 2 and short))
        ):
            firm.append((code, day))
        elif not (rank == 2 and facts.days is not None and not short):
            # Unresolved facts, families, rank 1 and unknown-duration rank 2
            # could still count; they can only make the count undetermined.
            maybe.append((code, day))
    repeat = payload.counting_policy == "repeat_occurrences"
    if _matching(firm, repeat) >= payload.required_count:
        return "met", "count_reached", frozenset(code for code, _ in firm)
    if _matching(firm + maybe, repeat) >= payload.required_count:
        return "undetermined", "count_needs_unresolved", frozenset()
    return "not_met", "count_below_required", frozenset()


def _distinct(sets: list[frozenset[str]]) -> bool | None:
    """Whether each requirement can use its own code; None if unbounded."""
    size = 1
    for item in sets:
        size *= len(item)
    if size > MAX_ASSIGNMENTS:
        return None
    return any(len(set(choice)) == len(choice) for choice in itertools.product(*sets))


def _all(
    group: str,
    children: list[tuple[c.Condition, Verdict, frozenset[str]]],
    nested: set[str],
) -> tuple[Verdict, str]:
    verdicts = [verdict for _, verdict, _ in children]
    if "not_met" in verdicts:
        return "not_met", "child_not_met"
    if "undetermined" in verdicts:
        return "undetermined", "child_undetermined"
    counted = [
        matched for node, _, matched in children if node.kind == "procedure_count"
    ]
    procedures = [
        matched
        for node, _, matched in children
        if node.kind in ("procedure_presence", "procedure_count")
    ]
    if counted and (
        "procedure_presence" in nested
        or "procedure_count" in nested
        or any(a & b for a, b in itertools.combinations(procedures, 2))
    ):
        # A counted procedure must not also satisfy a sibling requirement;
        # overlapping candidates need an unreviewed allocation.
        return "undetermined", "shared_fact_unreviewed"
    for kind in ("diagnosis_presence", "procedure_presence"):
        sets = [matched for node, _, matched in children if node.kind == kind]
        if sets and kind in nested:
            # A nested expression may reuse this fact; pairing is unreviewed.
            return "undetermined", "shared_fact_unreviewed"
        if len(sets) < 2:
            continue
        if kind == "procedure_presence" and group in DISTINCT_DATE_GROUPS:
            return "undetermined", "distinct_date_rule_unreviewed"
        distinct = _distinct(sets)
        if distinct is None:
            return "undetermined", "assignment_unbounded"
        if not distinct:
            return "not_met", "same_fact_counted_twice"
    return "met", "all_children_met"


def _expression(
    rules: GroupRules,
    node: c.Condition,
    payload: c.Expression,
    done: dict[int, tuple[Verdict, str, frozenset[str]]],
    nodes: dict[int, c.Condition],
) -> tuple[Verdict, str]:
    children = [(nodes[i], *done[i]) for i in payload.children]
    if node.kind == "any":
        verdicts = [verdict for _, verdict, _, _ in children]
        if "met" in verdicts:
            return "met", "child_met"
        if all(verdict == "not_met" for verdict in verdicts):
            return "not_met", "no_child_met"
        return "undetermined", "child_undetermined"
    return _all(
        rules.code,
        [(child, verdict, matched) for child, verdict, _, matched in children],
        _nested_kinds(payload, nodes),
    )


def _nested_kinds(payload: c.Expression, nodes: dict[int, c.Condition]) -> set[str]:
    """Presence kinds reachable below this expression's expression children."""
    pending = [
        child
        for child in payload.children
        if isinstance(nodes[child].payload, c.Expression)
    ]
    kinds: set[str] = set()
    seen: set[int] = set()
    for _ in range(4 * len(nodes) + 4):
        checkpoint()
        if not pending:
            return kinds
        identity = pending.pop()
        if identity in seen:
            continue
        seen.add(identity)
        node = nodes[identity]
        if isinstance(node.payload, c.Expression):
            pending.extend(node.payload.children)
        elif node.kind is not None:
            kinds.add(node.kind)
    raise GrouperError("nested condition graph exceeds bound")


def _evaluate(
    rules: GroupRules, facts: Facts, root: int
) -> tuple[Verdict, tuple[NodeVerdict, ...]]:
    nodes = {node.identity: node for node in rules.nodes}
    selectors = {item.identity: item for item in rules.selectors}
    done: dict[int, tuple[Verdict, str, frozenset[str]]] = {}
    stack = [root]
    for _ in range(4 * len(nodes) + 4):
        checkpoint()
        if not stack:
            break
        identity = stack[-1]
        node = nodes.get(identity)
        if node is None:
            raise GrouperError("group condition references a missing node")
        payload = node.payload
        executable = node.status == "proposed" and node.kind in ("all", "any")
        if executable and isinstance(payload, c.Expression):
            pending = [i for i in payload.children if i not in done]
            if pending:
                stack.extend(pending)
                continue
            verdict, reason = _expression(rules, node, payload, done, nodes)
            done[identity] = (verdict, reason, frozenset())
        elif node.status != "proposed":
            done[identity] = ("undetermined", "unsupported_extraction", frozenset())
        else:
            done[identity] = _leaf(node, selectors, facts)
        _ = stack.pop()
    else:
        raise GrouperError("group condition graph exceeds evaluation bound")
    trace = tuple(
        NodeVerdict(
            identity,
            verdict,
            reason,
            nodes[identity],
            tuple(sorted(matched)),
            _observed(nodes[identity], selectors, facts),
            selectors.get(p.selector_id)
            if isinstance(p := nodes[identity].payload, (c.Presence, c.Count))
            else None,
            _consulted(nodes[identity], facts),
        )
        for identity, (verdict, reason, matched) in sorted(done.items())
    )
    return done[root][0], trace


def _observed(
    node: c.Condition, selectors: dict[int, SelectorCodes], facts: Facts
) -> tuple[tuple[str, int | None], ...]:
    if node.status != "proposed":
        return ()
    if node.kind == "patient_age":
        return (("completed_years", facts.years),)
    if node.kind == "episode_duration":
        return (("duration_days", facts.days),)
    if node.kind == "discharge_mode":
        return (("discharge_mode", facts.discharge_mode),)
    if node.kind == "procedure_count" and isinstance(node.payload, c.Count):
        if node.payload.included_node_ids:
            return ()
        selector = selectors[node.payload.selector_id]
        dates = facts.procedure_dates or (None,) * len(facts.procedures)
        firm = [
            (code, day)
            for (code, outcome, rank), day in zip(facts.procedures, dates, strict=True)
            if code in selector.codes
            and outcome == "retained"
            and (
                rank in (3, 4)
                or (rank == 2 and facts.days is not None and facts.days <= 1)
            )
        ]
        return (
            (
                "count",
                _matching(firm, node.payload.counting_policy == "repeat_occurrences"),
            ),
        )
    return ()


def _consulted(node: c.Condition, facts: Facts) -> tuple[tuple[str, int], ...]:
    if node.status != "proposed":
        return ()
    if node.kind == "diagnosis_presence" and isinstance(node.payload, c.Presence):
        principal = (
            (("principal_diagnosis", 0),) if node.payload.role != "secondary" else ()
        )
        secondary = (
            tuple(("secondary_diagnoses", i) for i in range(len(facts.secondary)))
            if node.payload.role != "principal"
            else ()
        )
        return (*principal, *secondary)
    if node.kind in ("procedure_presence", "procedure_count"):
        if isinstance(node.payload, c.Count) and node.payload.included_node_ids:
            return ()
        return tuple(("procedures", i) for i in range(len(facts.procedures)))
    return ()


def _routing(rules: GroupRules, facts: Facts) -> tuple[Verdict, str]:
    """Paragraphs 105-111 for conservative (non-procedural) groups."""
    if rules.procedural or rules.code in CONSERVATIVE_MARKER_GROUPS:
        return "met", "routing_not_restricted"
    retained = [(c, o, r) for c, o, r in facts.procedures if o != "excluded"]
    if any(outcome == "unresolved" for _, outcome, _ in retained):
        return "undetermined", "unresolved_procedure"
    # Paragraph 105: a directing procedure from this group's own lists directs
    # here, so only directing procedures outside them raise paragraph 110.
    own = {code for selector in rules.selectors for code in selector.codes}
    if any(
        rank is not None and rank > 2 and code not in own for code, _, rank in retained
    ):
        # Paragraph 110: allowed only if the surgical JGP is outside the ward's
        # contract scope, which is not an encounter fact.
        return "undetermined", "contract_scope_unknown"
    if any(rank == 2 for _, _, rank in retained):
        if facts.days is None:
            return "undetermined", "rank_2_duration_unknown"
        if facts.days <= 1:
            return "not_met", "minor_procedure_short_stay"
    return "met", "conservative_allowed"


def _roots(rules: GroupRules, facts: Facts) -> tuple[Verdict, tuple[NodeVerdict, ...]]:
    """Several reference roots are alternatives (owner decision 2026-10-07)."""
    if not rules.roots:
        return "undetermined", ()
    verdicts: list[Verdict] = []
    traced: dict[int, NodeVerdict] = {}
    for root in rules.roots:
        verdict, trace = _evaluate(rules, facts, root)
        verdicts.append(verdict)
        traced.update((item.node, item) for item in trace)
    combined: Verdict = (
        "met"
        if "met" in verdicts
        else "not_met"
        if all(v == "not_met" for v in verdicts)
        else "undetermined"
    )
    return combined, tuple(traced[key] for key in sorted(traced))


def evaluate_group(rules: GroupRules, facts: Facts) -> GroupEvaluation:
    if rules.catalog_rows == 0:
        return GroupEvaluation(
            rules.catalog, rules.code, "invalid", "undetermined", "undetermined",
            ("not_in_governing_catalog",), (),
        )  # fmt: skip
    reasons: list[str] = []
    if rules.catalog_rows > 1:
        reasons.append("catalog_rows_ambiguous")
    conditions, trace = _roots(rules, facts)
    if len(rules.roots) > 1:
        reasons.append("roots_as_alternatives")
    elif not rules.roots:
        reasons.append("no_reference_root")
    routing, why = (
        _routing(rules, facts)
        if rules.catalog_rows == 1
        else (
            "undetermined",
            "catalog_rows_ambiguous",
        )
    )
    reasons.append(why)
    status: Status
    if "not_met" in (conditions, routing):
        status = "invalid"
    elif conditions == routing == "met" and rules.catalog_rows == 1:
        status = "valid"
    else:
        status = "undetermined"
    return GroupEvaluation(
        rules.catalog,
        rules.code,
        status,
        conditions,
        routing,
        tuple(reasons),
        trace,
        rules.roots,
        rules.source_row,
        RoutingTrace(
            routing,
            why,
            rules.procedural if rules.catalog_rows == 1 else None,
            tuple(range(len(facts.procedures)))
            if why not in ("routing_not_restricted", "catalog_rows_ambiguous")
            else (),
            facts.days,
        ),
    )
