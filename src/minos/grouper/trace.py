"""Bounded per-call trace ownership; never logs encounter decisions."""

from minos.domain.encounter import (
    MAX_PROCEDURES,
    MAX_SECONDARY_DIAGNOSES,
    MAX_SETTLEMENT_PRODUCTS,
    MAX_STAYS,
)
from minos.domain.grouper import Decision, GrouperError

# Per procedure: qualification, product effect and rank; per product: its class.
MAX_TRACE_DECISIONS = (
    2 * MAX_STAYS
    + MAX_SECONDARY_DIAGNOSES
    + 3 * MAX_PROCEDURES
    + MAX_SETTLEMENT_PRODUCTS
    + 10
)


class TraceAccumulator:
    def __init__(self) -> None:
        self._decisions: list[Decision] = []

    def append(self, decision: Decision) -> None:
        if len(self._decisions) >= MAX_TRACE_DECISIONS:
            raise GrouperError("algorithm trace exceeds its bound")
        self._decisions.append(decision)

    def freeze(self) -> tuple[Decision, ...]:
        return tuple(self._decisions)
