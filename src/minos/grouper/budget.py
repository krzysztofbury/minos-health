"""Cooperative whole-evaluation budget, separate from deterministic verdicts."""

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from time import monotonic

from minos.domain.reference import QueryLimitError, QueryTimeoutError


@dataclass
class _Budget:
    deadline: float
    remaining: int


_ACTIVE: ContextVar[_Budget | None] = ContextVar("grouping_budget", default=None)


def checkpoint() -> None:
    budget = _ACTIVE.get()
    if budget is None:
        return
    budget.remaining -= 1
    if budget.remaining < 0:
        raise QueryLimitError("grouping work limit exceeded")
    if monotonic() > budget.deadline:
        raise QueryTimeoutError("grouping deadline exceeded")


def seconds_left() -> float | None:
    """Time left on this evaluation, or None outside one."""
    budget = _ACTIVE.get()
    return None if budget is None else budget.deadline - monotonic()


@contextmanager
def evaluation_budget(*, seconds: float = 3.0, steps: int = 200_000) -> Generator[None]:
    token = _ACTIVE.set(_Budget(monotonic() + seconds, steps))
    try:
        checkpoint()
        yield
        checkpoint()
    finally:
        _ACTIVE.reset(token)
