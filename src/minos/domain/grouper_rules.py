"""Owner-approved executable subset of attachment 9 (decision 2026-10-07).

Executable: diagnosis and procedure list presence, `all`/`any`, age in
completed years and stay-length bounds. Everything else, and every group
with several reference roots, evaluates to `undetermined` with a reason.
Three-valued (Kleene) logic: an unknown fact never becomes met or not met.
"""

from dataclasses import dataclass
from typing import Literal

from minos.domain import jgp_conditions as c
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


@dataclass(frozen=True, repr=False)
class GroupingResult:
    candidates: CandidateResult
    # One evaluation per candidate, in candidate order.
    evaluations: tuple[GroupEvaluation, ...]
    scope: Literal["conditions_without_points"] = "conditions_without_points"

    def __post_init__(self) -> None:
        if [(e.catalog, e.group) for e in self.evaluations] != [
            (c.catalog, c.group) for c in self.candidates.candidates
        ]:
            raise GrouperError("evaluations must follow the candidates")
