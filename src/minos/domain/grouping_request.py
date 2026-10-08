"""Stateless grouping request shared by CLI and HTTP adapters."""

from dataclasses import dataclass
from datetime import date

from minos.domain.encounter import Encounter


@dataclass(frozen=True, repr=False)
class GroupingRequest:
    encounter: Encounter
    service_date: date
    reporting_month: date
    declared_modifiers: frozenset[str]
