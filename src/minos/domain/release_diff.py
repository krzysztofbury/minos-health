"""Logical reference changes, independent of storage and output adapters."""

from dataclasses import dataclass

from minos.domain.reference import ReferenceError


class DiffInputError(ReferenceError):
    """The selected releases cannot be compared without guessing correspondence."""


@dataclass(frozen=True)
class Fact:
    category: str
    identity: tuple[str, ...]
    value: str
    context: str | None = None


@dataclass(frozen=True)
class Change:
    pair: str
    category: str
    identity: tuple[str, ...]
    before: str | None
    after: str | None
    before_context: str | None = None
    after_context: str | None = None

    @property
    def action(self) -> str:
        return (
            "added"
            if self.before is None
            else "removed"
            if self.after is None
            else "changed"
        )


def compare_facts(
    pair: str, before: tuple[Fact, ...], after: tuple[Fact, ...]
) -> tuple[Change, ...]:
    left = {(fact.category, fact.identity): fact for fact in before}
    right = {(fact.category, fact.identity): fact for fact in after}
    if len(left) != len(before) or len(right) != len(after):
        raise DiffInputError("ambiguous logical fact identity")
    changes: list[Change] = []
    for category, identity in sorted(left.keys() | right.keys()):
        old, new = left.get((category, identity)), right.get((category, identity))
        old_value, new_value = old.value if old else None, new.value if new else None
        if old_value != new_value:
            changes.append(
                Change(
                    pair,
                    category,
                    identity,
                    old_value,
                    new_value,
                    old.context if old else None,
                    new.context if new else None,
                )
            )
    return tuple(changes)
