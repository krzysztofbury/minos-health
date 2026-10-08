"""Stateless grouping request and outcome shared by CLI and HTTP adapters."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

from minos.domain.encounter import Encounter
from minos.domain.grouper_rules import FinalPoints, GroupingResult


@dataclass(frozen=True, repr=False)
class GroupingRequest:
    encounter: Encounter
    service_date: date
    reporting_month: date
    declared_modifiers: frozenset[str]


@dataclass(frozen=True, repr=False)
class UnsupportedProductReport:
    """Unknown or nonempty product report: nothing was evaluated (#145)."""


@dataclass(frozen=True, repr=False)
class GroupingOutcome:
    result: GroupingResult
    # Empty when no reviewed MODTAR binding governs the date; base points are
    # never relabeled final.
    finals: tuple[FinalPoints, ...]
    tariff_bound: bool
    # (catalog, group) -> (name, product code) for every group governing the
    # service date, including groups outside the derived candidates.
    titles: Mapping[tuple[str, str], tuple[str, str]]
