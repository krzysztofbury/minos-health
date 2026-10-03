"""Source mappings and explicit legal-review gates, not tariff calculation."""

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from minos.domain.reference import SourceError


@dataclass(frozen=True)
class ModifierPeriod:
    ordinal: int
    month_from: int
    month_to: int
    coefficient_text: str
    coefficient_millionths: int
    groups: tuple[str, ...]


@dataclass(frozen=True)
class Modifier:
    ordinal: int
    symbol: str
    description: str
    status: Literal["A", "N"]
    periods: tuple[ModifierPeriod, ...]


@dataclass(frozen=True)
class ModifierRelease:
    year: int
    generation: int
    grouper_type: Literal["SZP"]
    xml_version: Literal["1.1"]
    published_at_text: str
    generated_at_text: str
    modifiers: tuple[Modifier, ...]


type Mapping = tuple[str, str, str, int | None, int | None, str | None, str | None]


def mappings(release: ModifierRelease) -> tuple[Mapping, ...]:
    result: list[Mapping] = []
    for modifier in release.modifiers:
        if not modifier.periods:
            result.append(
                (
                    modifier.symbol,
                    modifier.description,
                    modifier.status,
                    None,
                    None,
                    None,
                    None,
                )
            )
        for period in modifier.periods:
            result.extend(
                (
                    modifier.symbol,
                    modifier.description,
                    modifier.status,
                    period.month_from,
                    period.month_to,
                    period.coefficient_text,
                    group,
                )
                for group in period.groups
            )
    return tuple(result)


def validate_modifier_release(release: ModifierRelease) -> None:
    if not 2000 <= release.year <= 2099 or not 1 <= release.generation <= 999999:
        raise SourceError("MODTAR release identity exceeds bound")
    if str(release.grouper_type) != "SZP" or str(release.xml_version) != "1.1":
        raise SourceError("unsupported MODTAR grouper or XML version")
    for timestamp in (release.published_at_text, release.generated_at_text):
        if not re.fullmatch(
            r"20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}", timestamp
        ):
            raise SourceError("invalid MODTAR source timestamp")
        try:
            _ = datetime.fromisoformat(timestamp)
        except ValueError as error:
            raise SourceError("invalid MODTAR source timestamp") from error
    if not 1 <= len(release.modifiers) <= 10000:
        raise SourceError("MODTAR modifier count exceeds bound")
    occupied: dict[tuple[str, str], set[int]] = {}
    total_groups = 0
    for ordinal, modifier in enumerate(release.modifiers, 1):
        if modifier.ordinal != ordinal or modifier.status not in ("A", "N"):
            raise SourceError("invalid MODTAR modifier order or status")
        if not modifier.symbol or len(modifier.symbol) > 80:
            raise SourceError("invalid MODTAR symbol")
        if not modifier.description.strip() or len(modifier.description) > 8192:
            raise SourceError("invalid MODTAR legal citation")
        if not modifier.periods and modifier.status != "N":
            raise SourceError("active MODTAR modifier has no period")
        if len(modifier.periods) > 24:
            raise SourceError("MODTAR period count exceeds bound")
        for position, period in enumerate(modifier.periods, 1):
            if (
                period.ordinal != position
                or not 1 <= period.month_from <= period.month_to <= 12
            ):
                raise SourceError("invalid MODTAR period order or month range")
            if not 1 <= len(period.groups) <= 2000 or len(set(period.groups)) != len(
                period.groups
            ):
                raise SourceError("invalid MODTAR group references")
            if (
                exact_millionths(period.coefficient_text)
                != period.coefficient_millionths
            ):
                raise SourceError("MODTAR exact coefficient conflicts")
            months = set(range(period.month_from, period.month_to + 1))
            for group in period.groups:
                if not re.fullmatch(r"[A-Z]{1,4}[0-9]{2,3}[A-Z._]{0,3}", group):
                    raise SourceError("invalid MODTAR JGP group code")
                key = (modifier.symbol, group)
                used = occupied.setdefault(key, set())
                if used & months:
                    raise SourceError("duplicate or overlapping MODTAR mapping")
                used.update(months)
            total_groups += len(period.groups)
            if total_groups > 50000:
                raise SourceError("MODTAR mapping count exceeds bound")


def exact_millionths(text: str) -> int:
    if not re.fullmatch(r"[0-9]{1,3}(?:\.[0-9]{1,6})?", text):
        raise SourceError("unsupported MODTAR exact decimal")
    whole, _, fraction = text.partition(".")
    return int(whole) * 1_000_000 + int(fraction.ljust(6, "0"))


@dataclass(frozen=True)
class LegalMapping:
    mapping: Mapping
    service_from: date
    service_to: date
    applicability_evidence: str


@dataclass(frozen=True)
class LegalOverlay:
    year: int
    generation: int
    grouper_type: Literal["SZP"]
    catalog_release_id: str
    reviewed_on: date
    legal_source_evidence: tuple[str, ...]
    rows: tuple[LegalMapping, ...]


def reconcile_legal_overlay(
    release: ModifierRelease,
    overlay: LegalOverlay | None,
    *,
    catalog_release_id: str,
    catalog_groups: frozenset[str],
) -> LegalOverlay:
    """A missing review is unbound, never an empty set of applicable modifiers."""
    validate_modifier_release(release)
    if overlay is None:
        raise SourceError("MODTAR binding is unbound: legal overlay not reviewed")
    if (
        overlay.year,
        overlay.generation,
        overlay.grouper_type,
        overlay.catalog_release_id,
    ) != (release.year, release.generation, release.grouper_type, catalog_release_id):
        raise SourceError("MODTAR legal overlay binding is incompatible")
    if not overlay.legal_source_evidence or not 1 <= len(overlay.rows) <= 50000:
        raise SourceError("MODTAR legal overlay lacks reviewed source evidence")
    actual = mappings(release)
    expected = tuple(row.mapping for row in overlay.rows)
    if len(set(expected)) != len(expected) or set(actual) != set(expected):
        raise SourceError(
            "MODTAR versus legal overlay missing, extra or conflicting mapping"
        )
    for row in overlay.rows:
        if row.service_from > row.service_to or not row.applicability_evidence.strip():
            raise SourceError("MODTAR legal applicability lacks a reviewed window")
        if row.service_from.year != release.year or row.service_to.year != release.year:
            raise SourceError("MODTAR legal applicability has another year")
        if row.mapping[6] is not None and row.mapping[6] not in catalog_groups:
            raise SourceError(
                "MODTAR group is absent from the selected ruleset catalog"
            )
    return overlay


def select_reviewed_mappings(
    release: ModifierRelease,
    overlay: LegalOverlay | None,
    service_date: date,
    *,
    catalog_release_id: str,
    catalog_groups: frozenset[str],
) -> tuple[Mapping, ...]:
    overlay = reconcile_legal_overlay(
        release,
        overlay,
        catalog_release_id=catalog_release_id,
        catalog_groups=catalog_groups,
    )
    if service_date.year != overlay.year:
        raise SourceError("MODTAR service date is not covered by this binding")
    return tuple(
        row.mapping
        for row in overlay.rows
        if row.mapping[2] == "A"
        and row.service_from <= service_date <= row.service_to
        and row.mapping[3] is not None
        and row.mapping[4] is not None
        and row.mapping[3] <= service_date.month <= row.mapping[4]
    )
