"""Per-row service windows for one catalog release, from reviewed evidence."""

import re
from collections import defaultdict
from datetime import date

from minos.domain.jgp_catalog import CatalogGroup, CatalogName, CatalogSheet
from minos.domain.jgp_ruleset import RowWindow
from minos.domain.reference import SourceError
from minos.ingest.jgp_catalog_manifest import CatalogManifest

type RowKey = tuple[CatalogName, int]
# A source note limiting a row to a date, such as "od 1.01.2027" or
# "do 31.12.2026"; citations such as "ustawą z dnia 7 stycznia 1993 r" differ.
DATE_LIMIT_PATTERN = re.compile(r"\b(?:od|do) \d{1,2}\.\d{1,2}\.20\d{2}\b")
MAX_ROWS_PER_CODE = 2


def row_windows(
    sheets: tuple[CatalogSheet, ...], manifest: CatalogManifest
) -> dict[RowKey, tuple[RowWindow, str]]:
    """Window and evidence for every group row, keyed by (catalog, source row).

    Rows default to the release window. A reviewed override (102/2026 § 4
    ust. 5) narrows listed rows only after their own source note confirms it,
    and no code may apply on two rows for the same service date.
    """
    release = RowWindow(manifest.service_from, None)
    windows: dict[RowKey, tuple[RowWindow, str]] = {
        (sheet.catalog, group.row): (release, manifest.service_evidence)
        for sheet in sheets
        for group in sheet.groups
    }
    by_ordinal: dict[tuple[CatalogName, int], list[CatalogGroup]] = defaultdict(list)
    for sheet in sheets:
        for group in sheet.groups:
            by_ordinal[(sheet.catalog, group.ordinal)].append(group)
    for item in manifest.row_applicability:
        for ordinal in item.ordinals:
            matches = by_ordinal.get((item.catalog, ordinal), [])
            if len(matches) != 1:
                raise SourceError("row applicability ordinal is missing or ambiguous")
            group = matches[0]
            if item.note_marker not in " ".join((group.notes or "").split()):
                raise SourceError("catalog row note does not confirm its applicability")
            window = RowWindow(
                item.service_from or manifest.service_from, item.service_to
            )
            windows[(group.catalog, group.row)] = (window, item.evidence)
    overridden = {
        (item.catalog, ordinal)
        for item in manifest.row_applicability
        for ordinal in item.ordinals
    }
    for sheet in sheets:
        for group in sheet.groups:
            dated = DATE_LIMIT_PATTERN.search(" ".join((group.notes or "").split()))
            if dated and (sheet.catalog, group.ordinal) not in overridden:
                raise SourceError("a dated catalog note has no reviewed applicability")
    _check_single_row_per_date(sheets, windows)
    return windows


def _check_single_row_per_date(
    sheets: tuple[CatalogSheet, ...], windows: dict[RowKey, tuple[RowWindow, str]]
) -> None:
    rows: dict[str, list[RowWindow]] = defaultdict(list)
    for sheet in sheets:
        for group in sheet.groups:
            rows[group.code].append(windows[(group.catalog, group.row)][0])
    for code_windows in rows.values():
        if len(code_windows) > MAX_ROWS_PER_CODE:
            raise SourceError("catalog code repeats more than twice")
        for index, first in enumerate(code_windows):
            for second in code_windows[index + 1 :]:
                if _overlap(first, second):
                    raise SourceError("a catalog code applies on two rows at once")


def _overlap(first: RowWindow, second: RowWindow) -> bool:
    end = date.max
    return first.service_from <= (second.service_to or end) and second.service_from <= (
        first.service_to or end
    )
