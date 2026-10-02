"""Service-date selection of reviewed JGP catalog releases.

A service date resolves to exactly one reviewed release, as known on a given
date, or fails explicitly. There is no "latest release" fallback: an
uncovered date is `NoRulesetForDate` and an unresolved overlap is
`AmbiguousRuleset`.
"""

from dataclasses import dataclass
from datetime import date

from minos.domain.reference import ReferenceError


class NoRulesetForDate(ReferenceError):
    """No reviewed release covers the service date with the given knowledge."""


class AmbiguousRuleset(ReferenceError):
    """Several reviewed releases govern a date and none supersedes the others."""


class KnowledgeBeyondBundle(ReferenceError):
    """The knowledge date is later than the bundle's reviewed lineage review.

    Answering would claim knowledge of acts the bundle never saw, which is a
    disguised latest-release fallback.
    """


class GroupNotOnDate(ReferenceError):
    """The code has no catalog row applying on the service date."""


class SectionNotOnDate(ReferenceError):
    """The catalog has no chapter at that source position."""


MAX_RELEASES = 64


@dataclass(frozen=True)
class ReleaseWindow:
    release_id: str
    service_from: date
    published_on: date
    supersedes: str | None


@dataclass(frozen=True)
class RowWindow:
    service_from: date
    service_to: date | None

    def covers(self, service_date: date) -> bool:
        return self.service_from <= service_date and (
            self.service_to is None or service_date <= self.service_to
        )


def resolve_release(
    releases: tuple[ReleaseWindow, ...], service_date: date, known_on: date
) -> ReleaseWindow:
    """Pick the release governing `service_date` as published by `known_on`.

    Retroactive acts (74/2026 published 2026-07-23 for services from
    2026-07-01) govern earlier service dates once known; before publication the
    previously known release still answers.
    """
    if len(releases) > MAX_RELEASES:
        raise AmbiguousRuleset("too many catalog releases to resolve")
    candidates = [
        release
        for release in releases
        if release.service_from <= service_date and release.published_on <= known_on
    ]
    if not candidates:
        covered = (service_date.isoformat(), known_on.isoformat())
        raise NoRulesetForDate(
            "no reviewed ruleset covers {} as known on {}".format(*covered)
        )
    replaced = {release.supersedes for release in candidates}
    governing = [
        release for release in candidates if release.release_id not in replaced
    ]
    if len(governing) != 1:
        raise AmbiguousRuleset(
            f"{len(governing)} reviewed rulesets govern {service_date.isoformat()}"
        )
    return governing[0]
