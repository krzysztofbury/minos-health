"""Stateless grouping request and outcome shared by CLI and HTTP adapters."""

from collections.abc import Mapping
from dataclasses import dataclass, field
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
    """Product reporting is not complete: nothing was evaluated (#145)."""


@dataclass(frozen=True, repr=False)
class ProductCatalogMissing:
    """Products were reported, but the bundle cannot classify them under the
    act governing the service date: nothing was evaluated (#145)."""


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
    # Reported product code -> (catalog sheet, official name) under the
    # classifying act, for explanations only; classes come from preparation.
    product_names: Mapping[str, tuple[str, str]] = field(
        default_factory=dict[str, tuple[str, str]]
    )
