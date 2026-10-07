"""Owner-approved executable subset of attachment 9 (decision 2026-10-07).

Executable: diagnosis and procedure list presence, `all`/`any`, age in
completed years and stay-length bounds. Everything else, and every group
with several reference roots, evaluates to `undetermined` with a reason.
Three-valued (Kleene) logic: an unknown fact never becomes met or not met.
"""

from dataclasses import dataclass
from typing import Literal

from minos.domain import jgp_conditions as c
from minos.domain.encounter import HospitalizationType
from minos.domain.grouper import GrouperError
from minos.domain.grouper_candidates import CandidateResult, Catalog

MAX_GROUP_NODES = 512
type Verdict = Literal["met", "not_met", "undetermined"]
type Status = Literal["valid", "invalid", "undetermined"]


@dataclass(frozen=True)
class SelectorCodes:
    identity: int
    codes: frozenset[str]
    # Never expanded: a fact inside a family keeps the verdict undetermined.
    families: tuple[str, ...]


@dataclass(frozen=True)
class GroupRules:
    catalog: Catalog
    code: str
    # Rows of the governing catalog release in force on the service date. Zero
    # means the group cannot be settled then; several are ambiguous.
    catalog_rows: int
    # The catalog's trailing `*` marker; only meaningful for exactly one row.
    procedural: bool
    roots: tuple[int, ...]
    nodes: tuple[c.Condition, ...]
    selectors: tuple[SelectorCodes, ...]
    # The single governing row and its catalog measures (points in hundredths,
    # financed stays in days); empty unless exactly one row governs the date.
    source_row: int | None = None
    base_values: tuple[tuple[str, int], ...] = ()

    def __post_init__(self) -> None:
        if len(self.nodes) > MAX_GROUP_NODES:
            raise GrouperError("group condition nodes exceed bound")
        if not 0 <= self.catalog_rows <= 8:
            raise GrouperError("invalid governing catalog row count")
        if len({node.identity for node in self.nodes}) != len(self.nodes):
            raise GrouperError("duplicate group condition node")


@dataclass(frozen=True, repr=False)
class NodeVerdict:
    node: int
    verdict: Verdict
    reason: str


@dataclass(frozen=True, repr=False)
class GroupEvaluation:
    catalog: Catalog
    group: str
    status: Status
    conditions: Verdict
    # Paragraphs 105-111: whether a conservative group may be settled given
    # the reported procedures. Procedural groups route through their lists.
    routing: Verdict
    reasons: tuple[str, ...]
    nodes: tuple[NodeVerdict, ...]


@dataclass(frozen=True)
class StayFacts:
    hospitalization_type: HospitalizationType
    same_day: bool
    # Algorithm duration, known only for a complete, product-free report.
    days: int | None


@dataclass(frozen=True, repr=False)
class BasePoints:
    """Catalog base points for the settlement mode; never final points."""

    determined: bool
    reason: str
    measure: str | None = None
    base_hundredths: int | None = None
    extra_days: int = 0
    extra_hundredths: int = 0
    kind: Literal["catalog_base_points_without_modifiers"] = (
        "catalog_base_points_without_modifiers"
    )

    @property
    def total_hundredths(self) -> int | None:
        if self.base_hundredths is None:
            return None
        return self.base_hundredths + self.extra_hundredths


@dataclass(frozen=True, repr=False)
class GroupingResult:
    candidates: CandidateResult
    # One evaluation and one base-points answer per candidate, in order.
    evaluations: tuple[GroupEvaluation, ...]
    points: tuple[BasePoints, ...]
    # Valid groups by base points, highest first (paragraph 113: the
    # provider chooses; the first is the highest value, not "the" answer).
    ranking: tuple[tuple[Catalog, str], ...]
    scope: Literal["base_points_without_modifiers"] = "base_points_without_modifiers"

    def __post_init__(self) -> None:
        order = [(c.catalog, c.group) for c in self.candidates.candidates]
        if [(e.catalog, e.group) for e in self.evaluations] != order:
            raise GrouperError("evaluations must follow the candidates")
        if len(self.points) != len(order):
            raise GrouperError("base points must follow the candidates")
        valid = {(e.catalog, e.group) for e in self.evaluations if e.status == "valid"}
        if set(self.ranking) != valid or len(self.ranking) != len(valid):
            raise GrouperError("ranking must list exactly the valid groups")


class ModifierBindingUnbound(GrouperError):
    """No reviewed MODTAR binding governs the date; final points are refused."""


type ModifierOutcome = Literal["applied", "possible", "rejected", "undetermined"]


@dataclass(frozen=True, repr=False)
class ModifierDecision:
    symbol: str
    provenance: Literal["MODTAR", "manual_legal_overlay"]
    source_citation: str
    coefficient_millionths: int
    outcome: ModifierOutcome
    reason: str
    # For a possible modifier: the hundredths it would add on its own.
    would_add_hundredths: int | None = None


@dataclass(frozen=True, repr=False)
class FinalPoints:
    catalog: Catalog
    group: str
    base: BasePoints
    decisions: tuple[ModifierDecision, ...]
    # Base x applied coefficients, rounded half-up once to 0.01 point.
    total_hundredths: int | None
    notes: tuple[str, ...]
