"""Source-preserved JGP catalog rows from attachment 1a.

Values are catalog base points without modifiers, never final settlement
points. Release applicability for a service date is resolved elsewhere (#18).
"""

from dataclasses import dataclass
from typing import Literal

type CatalogName = Literal["1a", "1ae"]
type ValueUnit = Literal["points_hundredths", "days"]
type ScopeMarker = Literal[1, 2, 3]


@dataclass(frozen=True)
class CatalogScope:
    """One "Zakresy świadczeń" column, in source order."""

    position: int
    column: str
    label: str


@dataclass(frozen=True)
class CatalogValue:
    measure: str
    unit: ValueUnit
    amount: int
    column: str


@dataclass(frozen=True)
class ScopeMark:
    # Source legend: 1 = first, 2 = second, 3 = every scope named in the column.
    position: int
    marker: ScopeMarker


@dataclass(frozen=True)
class CatalogGroup:
    """One catalog row; the same code may legitimately appear on two rows."""

    catalog: CatalogName
    row: int
    ordinal: int
    code: str
    product_code: str
    name: str
    procedural: bool
    chapter: str
    notes: str | None
    values: tuple[CatalogValue, ...]
    scopes: tuple[ScopeMark, ...]


@dataclass(frozen=True)
class CatalogSheet:
    catalog: CatalogName
    layout: str
    worksheet: str
    title: str
    chapters: tuple[str, ...]
    scopes: tuple[CatalogScope, ...]
    groups: tuple[CatalogGroup, ...]
