"""Settlement products excluded from grouping, type W (#145, algorithm 1.1.2026).

A product of the governing act's 1w catalog excludes every ICD-9 procedure
dated within its start and end dates, inclusive. An unknown product, or a W
product without both dates, could exclude any procedure within its known
bounds (the episode on a missing side), so those facts stay unresolved.
Decisions are owner-recorded (2026-10-09).
"""

from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from minos.domain.encounter import SettlementProduct
from minos.domain.grouper import ProductClass, ProductClassification

type Interval = tuple[date, date]
_DAY = timedelta(days=1)


def touching(first: Interval, second: Interval) -> bool:
    """Intervals sharing a day or lying on consecutive days."""
    return first[0] <= second[1] + _DAY and second[0] <= first[1] + _DAY


def merge(intervals: list[Interval]) -> tuple[Interval, ...]:
    merged: list[Interval] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1] + _DAY:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return tuple(merged)


@dataclass(frozen=True)
class ProductContext:
    classes: tuple[ProductClass, ...]
    # Complete W intervals in reported order; their union excludes procedures.
    w_intervals: tuple[Interval, ...]
    covered: tuple[Interval, ...]
    # Days an unknown product or a W product without both dates could cover:
    # procedures there stay unresolved. A missing endpoint extends to the
    # episode boundary on that side.
    uncertain: tuple[Interval, ...]
    # Some W or unknown product lacks a date, so its length is unknown.
    incomplete: bool

    @property
    def unknown(self) -> bool:
        return "unknown" in self.classes or self.incomplete


def analyse(
    products: tuple[SettlementProduct, ...],
    classification: ProductClassification,
    episode: Interval,
) -> ProductContext:
    classes: tuple[ProductClass, ...] = tuple(
        classification.classify(product.code) for product in products
    )
    w_intervals: list[Interval] = []
    uncertain: list[Interval] = []
    incomplete = False
    for product, kind in zip(products, classes, strict=True):
        if kind == "neutral":
            continue
        start, end = product.started_on, product.ended_on
        if kind == "W" and start is not None and end is not None:
            w_intervals.append((start, end))
            continue
        incomplete = incomplete or start is None or end is None
        uncertain.append((start or episode[0], end or episode[1]))
    return ProductContext(
        classes, tuple(w_intervals), merge(w_intervals), merge(uncertain), incomplete
    )


def unclassified(episode: Interval) -> ProductContext:
    """Reported products without a binding could cover any day of the episode."""
    return ProductContext((), (), (), (episode,), True)


def _hit(intervals: tuple[Interval, ...], possible: Interval) -> Interval | None:
    """The sorted, disjoint interval overlapping `possible` that starts last
    on or before its end, found by bisection (up to 1,000 products)."""
    index = bisect_right(intervals, (possible[1], date.max)) - 1
    if index >= 0 and intervals[index][1] >= possible[0]:
        return intervals[index]
    return None


def procedure_effect(
    context: ProductContext, possible: Interval
) -> Literal["excluded", "unresolved"] | None:
    """Effect on a procedure that happened on some day within `possible`."""
    covering = _hit(context.covered, possible)
    if (
        covering is not None
        and covering[0] <= possible[0]
        and possible[1] <= covering[1]
    ):
        return "excluded"
    if covering is not None or _hit(context.uncertain, possible) is not None:
        return "unresolved"
    return None
