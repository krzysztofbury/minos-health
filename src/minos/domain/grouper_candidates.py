"""Directional candidate groups (algorithm paragraph 103), not eligibility.

A candidate is a group whose attachment 9 directional lists contain a prepared
fact. Its conditions are not evaluated here (#35), and the provider chooses
among valid groups (paragraph 113). Absence from the candidates is not proof
that a group's conditions fail; it means no directional list was hit.
"""

import re
from dataclasses import dataclass
from datetime import date
from typing import Literal

from minos.domain.grouper import GrouperError, PreparationResult

MAX_INDEX_GROUPS = 2_000
MAX_GROUP_REQUIREMENTS = 512
MAX_REQUIREMENT_CODES = 20_000
type Catalog = Literal["1a", "1ae"]
type FactKind = Literal["diagnosis", "procedure"]
# Principal-role lists match only the principal diagnosis; every other role
# (secondary, unspecified, absent) matches any prepared diagnosis, so the
# candidate set errs towards inclusion rather than silently dropping a group.
type MatchRole = Literal["principal", "any"]
type Basis = Literal["retained_fact", "unresolved_fact"]
type Underivable = Literal["no_directional_requirement"]


def _code(value: str) -> None:
    if (
        type(value) is not str
        or re.fullmatch(r"[A-Z0-9][A-Z0-9.\-]{0,15}", value) is None
    ):
        raise GrouperError("invalid directional code")


@dataclass(frozen=True)
class DirectionalRequirement:
    node: int
    kind: FactKind
    role: MatchRole
    codes: frozenset[str]
    # Classification families are never expanded; a fact inside one can only
    # make the group an unresolved candidate.
    families: tuple[str, ...]
    list_ids: tuple[int, ...]
    source_rows: tuple[int, ...]

    def __post_init__(self) -> None:
        if type(self.codes) is not frozenset or len(self.codes) > MAX_REQUIREMENT_CODES:
            raise GrouperError("invalid bounded directional codes")
        for code in (*self.codes, *self.families):
            _code(code)
        if not self.codes and not self.families:
            raise GrouperError("directional requirement has no codes")


@dataclass(frozen=True)
class GroupDirections:
    code: str
    title_row: int
    requirements: tuple[DirectionalRequirement, ...]
    fully_interpreted: bool

    def __post_init__(self) -> None:
        if len(self.requirements) > MAX_GROUP_REQUIREMENTS:
            raise GrouperError("group directional requirements exceed bound")


@dataclass(frozen=True)
class DirectionalIndex:
    characteristics_release_id: str
    catalog: Catalog
    groups: tuple[GroupDirections, ...]

    def __post_init__(self) -> None:
        if not 1 <= len(self.groups) <= MAX_INDEX_GROUPS:
            raise GrouperError("directional index groups exceed bound")
        if len({group.code for group in self.groups}) != len(self.groups):
            raise GrouperError("duplicate directional index group")


@dataclass(frozen=True)
class GroupingSources:
    """One dated composite from a single reviewed dataset bundle."""

    dataset_id: str
    service_date: date
    catalog_release_id: str
    characteristics_release_id: str
    icd9_release_id: str
    icd10_release_id: str
    algorithm_release_id: str


@dataclass(frozen=True, repr=False)
class Candidate:
    catalog: Catalog
    group: str
    basis: Basis
    matched_nodes: tuple[int, ...]
    matched_lists: tuple[int, ...]
    evidence_rows: tuple[int, ...]
    fully_interpreted: bool
    conditions: Literal["not_evaluated"] = "not_evaluated"


@dataclass(frozen=True, repr=False)
class CandidateResult:
    sources: GroupingSources
    preparation: PreparationResult
    candidates: tuple[Candidate, ...]
    # Groups no directional list can ever reach; reported, never dropped.
    underivable: tuple[tuple[Catalog, str, Underivable], ...]
    scope: Literal["directional_candidates_only"] = "directional_candidates_only"
