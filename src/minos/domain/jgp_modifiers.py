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
class CatalogWindow:
    """One stretch of service dates governed by one reviewed catalog release,
    with the group codes that have a catalog row in that stretch."""

    release_id: str
    service_from: date
    service_to: date | None  # inclusive; None is open-ended
    groups: frozenset[str]


type Resolution = Literal["exact", "trailing_punctuation_removed", "owner_assumption"]

# Literal references with no public definition, read by explicit owner decision.
# Z01_S (2026-10-03): no meaning of "_S" found in the MODTAR XML, the CZS
# dictionary-service documentation or the CZS MODTAR browser; it occurs only
# under section 17 (17.1, 17.18) and KON_1 (comprehensive oncology) modifiers.
# Read as Z01 until NFZ evidence says otherwise. Exactly this literal; no
# general "_S" rule.
OWNER_ASSUMPTIONS: dict[str, str] = {"Z01_S": "Z01"}


def resolve_group_reference(
    literal: str, groups: frozenset[str]
) -> tuple[str, Resolution] | None:
    """Exact group, or one trailing `.` / `_` removed when that group exists.

    Owner rule (2026-10-03): MODTAR's `C14.`, `P42.` and `P52_` are read as
    C14, P42 and P52 only when the governing catalog has that group. `Z01_S`
    is read as Z01 only through the explicit OWNER_ASSUMPTIONS entry. No other
    suffix is stripped and no prefix family is derived.
    """
    if literal in groups:
        return literal, "exact"
    if literal[-1:] in (".", "_") and literal[:-1] in groups:
        return literal[:-1], "trailing_punctuation_removed"
    assumed = OWNER_ASSUMPTIONS.get(literal)
    if assumed is not None and assumed in groups:
        return assumed, "owner_assumption"
    return None


def governing_windows(
    windows: tuple[CatalogWindow, ...], start: date, end: date
) -> tuple[CatalogWindow, ...]:
    """Catalog windows overlapping [start, end]; a day without a reviewed
    catalog refuses rather than borrowing another release."""
    selected = tuple(
        window
        for window in windows
        if window.service_from <= end
        and (window.service_to is None or window.service_to >= start)
    )
    day = start
    for window in sorted(selected, key=lambda item: item.service_from):
        if window.service_from > day:
            break
        if window.service_to is None or window.service_to >= end:
            return selected
        day = date.fromordinal(window.service_to.toordinal() + 1)
    raise SourceError("MODTAR service window is not covered by a reviewed catalog")


@dataclass(frozen=True)
class LegalProvision:
    """A reviewed governing provision absent from the operational dictionary."""

    symbol: str
    clause: str
    coefficient_text: str
    groups: tuple[str, ...]
    service_from: date
    service_to: date
    eligibility_text: str
    source_evidence: str


@dataclass(frozen=True)
class LegalOverlay:
    year: int
    generation: int
    grouper_type: Literal["SZP"]
    # Every catalog release governing some reviewed service window: MODTAR is
    # a calendar-year dictionary spanning catalog releases.
    catalog_release_ids: tuple[str, ...]
    reviewed_on: date
    legal_source_evidence: tuple[str, ...]
    rows: tuple[LegalMapping, ...]
    supplements: tuple[LegalProvision, ...] = ()


def reconcile_legal_overlay(
    release: ModifierRelease,
    overlay: LegalOverlay | None,
    *,
    catalog_windows: tuple[CatalogWindow, ...],
) -> LegalOverlay:
    """A missing review is unbound, never an empty set of applicable modifiers.

    Each mapping's group must resolve in every catalog release governing its
    own reviewed service window, not in one selected catalog.
    """
    validate_modifier_release(release)
    if overlay is None:
        raise SourceError("MODTAR binding is unbound: legal overlay not reviewed")
    if (overlay.year, overlay.generation, overlay.grouper_type) != (
        release.year,
        release.generation,
        release.grouper_type,
    ):
        raise SourceError("MODTAR legal overlay binding is incompatible")
    if not overlay.legal_source_evidence or not 1 <= len(overlay.rows) <= 50000:
        raise SourceError("MODTAR legal overlay lacks reviewed source evidence")
    actual = mappings(release)
    expected = tuple(row.mapping for row in overlay.rows)
    if len(set(expected)) != len(expected) or set(actual) != set(expected):
        raise SourceError(
            "MODTAR versus legal overlay missing, extra or conflicting mapping"
        )
    governing: set[str] = set()
    for row in overlay.rows:
        if row.service_from > row.service_to or not row.applicability_evidence.strip():
            raise SourceError("MODTAR legal applicability lacks a reviewed window")
        if row.service_from.year != release.year or row.service_to.year != release.year:
            raise SourceError("MODTAR legal applicability has another year")
        if row.mapping[6] is None:
            continue
        for window in governing_windows(
            catalog_windows, row.service_from, row.service_to
        ):
            governing.add(window.release_id)
            if resolve_group_reference(row.mapping[6], window.groups) is None:
                raise SourceError(
                    "MODTAR group is absent from the catalog governing its window"
                )
    if set(overlay.catalog_release_ids) != governing or len(
        overlay.catalog_release_ids
    ) != len(set(overlay.catalog_release_ids)):
        raise SourceError("MODTAR legal overlay catalog releases conflict")
    _validate_supplements(overlay, catalog_windows)
    return overlay


def _validate_supplements(
    overlay: LegalOverlay, windows: tuple[CatalogWindow, ...]
) -> None:
    if len(overlay.supplements) > 500:
        raise SourceError("legal supplement count exceeds bound")
    symbols: set[str] = set()
    dictionary_symbols = {row.mapping[0] for row in overlay.rows}
    for rule in overlay.supplements:
        if (
            not rule.symbol
            or len(rule.symbol) > 80
            or rule.symbol in symbols
            or rule.symbol in dictionary_symbols
        ):
            raise SourceError("legal supplement must have a distinct manual identity")
        symbols.add(rule.symbol)
        for text in (rule.clause, rule.eligibility_text, rule.source_evidence):
            if not text.strip() or len(text) > 8192:
                raise SourceError(
                    "legal supplement lacks bounded eligibility or source evidence"
                )
        _ = exact_millionths(rule.coefficient_text)
        if (
            rule.service_from.year != overlay.year
            or rule.service_to.year != overlay.year
            or rule.service_from > rule.service_to
        ):
            raise SourceError("legal supplement has an incompatible service window")
        if not 1 <= len(rule.groups) <= 2000 or len(set(rule.groups)) != len(
            rule.groups
        ):
            raise SourceError("legal supplement has invalid group references")
        for window in governing_windows(windows, rule.service_from, rule.service_to):
            if window.release_id not in overlay.catalog_release_ids:
                raise SourceError("legal supplement has another governing catalog")
            if any(
                resolve_group_reference(code, window.groups) is None
                for code in rule.groups
            ):
                raise SourceError(
                    "legal supplement group is absent from the governing catalog"
                )


def select_legal_provisions(
    overlay: LegalOverlay, service_date: date
) -> tuple[LegalProvision, ...]:
    """Reference applicability only; qualifiers still require encounter evaluation."""
    if service_date.year != overlay.year:
        raise SourceError("service date is outside the reviewed legal year")
    return tuple(
        rule
        for rule in overlay.supplements
        if rule.service_from <= service_date <= rule.service_to
    )


def select_reviewed_mappings(
    release: ModifierRelease,
    overlay: LegalOverlay | None,
    service_date: date,
    *,
    catalog_windows: tuple[CatalogWindow, ...],
) -> tuple[tuple[Mapping, str | None], ...]:
    """Mappings applying on the date, each with the group code it resolves to
    in the catalog governing that date (None for a group-less record)."""
    overlay = reconcile_legal_overlay(release, overlay, catalog_windows=catalog_windows)
    if service_date.year != overlay.year:
        raise SourceError("MODTAR service date is not covered by this binding")
    windows = governing_windows(catalog_windows, service_date, service_date)
    if len(windows) != 1:
        raise SourceError("MODTAR service date has no single governing catalog")
    window = windows[0]

    def resolved(row: LegalMapping) -> str | None:
        if row.mapping[6] is None:
            return None
        match = resolve_group_reference(row.mapping[6], window.groups)
        if match is None:
            raise SourceError("MODTAR group is absent from the governing catalog")
        return match[0]

    return tuple(
        (row.mapping, resolved(row))
        for row in overlay.rows
        if row.mapping[2] == "A"
        and row.service_from <= service_date <= row.service_to
        and row.mapping[3] is not None
        and row.mapping[4] is not None
        and row.mapping[3] <= service_date.month <= row.mapping[4]
    )
