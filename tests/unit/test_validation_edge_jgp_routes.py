import re
from typing import cast

import pytest

from minos.http.concepts import CONCEPT_ROUTE
from minos.http.jgp import JGP_PAGE_ROUTE, JGP_ROUTE
from minos.http.jgp_comparison import COMPARISON_ROUTE
from scripts.build_validation_edge import _routes  # pyright: ignore[reportPrivateUsage]

CORPUS = (
    "/api/v1/jgp/2026-07-10/groups/PZD02",
    "/api/v1/jgp/2026-07-10/groups/AU35C",
    "/api/v1/jgp/2026-07-10/groups/DU05III",
    "/api/v1/jgp/2026-07-10/sections",
    "/api/v1/jgp/2026-07-10/sections/1a/1",
    "/api/v1/jgp/2026-07-10/sections/1ae/16",
    "/api/v1/jgp/2026-07-10/sections/1a/999",
    "/api/v1/jgp/2026-07-10/groups/pzd02",
    "/api/v1/jgp/2026-07-10/groups/PZD02/",
    "/api/v1/jgp/2026-07-10/groups/1A01",
    "/api/v1/jgp/2026-07-10/sections/1a/0",
    "/api/v1/jgp/2026-07-10/sections/1a/01",
    "/api/v1/jgp/2026-07-10/sections/1a/1000",
    "/api/v1/jgp/2026-07-10/sections/1b/1",
    "/api/v1/jgp/1999-07-10/groups/PZD02",
    "/api/v1/jgp/2026-7-10/groups/PZD02",
    "/api/v1/jgp/2026-07-10/groups/../sections",
    "/api/v1/jgp/2026-07-10",
)


def edge_pattern() -> re.Pattern[str]:
    locations = cast(
        list[str], re.findall(r'location ~ "(\^/api/v1/jgp/[^"]+)"', _routes())
    )
    assert len(locations) == 1
    return re.compile(locations[0])


@pytest.mark.parametrize("path", CORPUS)
def test_edge_and_api_accept_exactly_the_same_jgp_paths(path: str) -> None:
    # The edge builder must stay stdlib-only for the publish job, so it keeps
    # its own copy of the route; this corpus keeps the two from drifting.
    assert bool(edge_pattern().search(path)) == bool(JGP_ROUTE.fullmatch(path))


def test_corpus_covers_both_accepted_and_refused_paths() -> None:
    accepted = [path for path in CORPUS if JGP_ROUTE.fullmatch(path)]
    assert 0 < len(accepted) < len(CORPUS)


PAGE_CORPUS = (
    "/jgp",
    "/jgp/2026-10-02",
    "/jgp/2026-10-02/PZD02",
    "/jgp/2026-10-02/pzd02",
    "/jgp/2026-10-02/DU05III",
    "/jgp/2026-10-02/zakres/15",
    "/jgp/H21",
    "/jgp/h21",
    "/jgp/",
    "/jgp/2026-10-02/",
    "/jgp/2026-10-02/zakres/0",
    "/jgp/2026-10-02/zakres/1000",
    "/jgp/2026-10-02/zakres",
    "/jgp/2026-10-02/1A01",
    "/jgp/1999-10-02",
    "/jgp/2026-10-02/PZD02/extra",
    "/jgp.js",
    "/jgpx",
)


def edge_page_pattern() -> re.Pattern[str]:
    locations = cast(list[str], re.findall(r'location ~ "(\^/jgp\(/[^"]+)"', _routes()))
    assert len(locations) == 1
    return re.compile(locations[0])


@pytest.mark.parametrize("path", PAGE_CORPUS)
def test_edge_and_app_accept_exactly_the_same_jgp_pages(path: str) -> None:
    assert bool(edge_page_pattern().search(path)) == bool(
        JGP_PAGE_ROUTE.fullmatch(path)
    )


def test_page_corpus_covers_both_accepted_and_refused_paths() -> None:
    accepted = [path for path in PAGE_CORPUS if JGP_PAGE_ROUTE.fullmatch(path)]
    assert 0 < len(accepted) < len(PAGE_CORPUS)


@pytest.mark.parametrize(
    "suffix,accepted",
    [
        ("wszystkie/wszystkie", True),
        ("a" * 64 + "/H21", True),
        ("wszystkie/PZD02", True),
        ("wszystkie/H21/", False),
        ("kardiologia/H21", False),
        ("wszystkie/h21", False),
        ("wszystkie/H21?private=secret", False),
    ],
)
def test_comparison_edge_and_log_allowlists(suffix: str, accepted: bool) -> None:
    patterns = cast(
        list[str], re.findall(r'location ~ "(\^/jgp/porownaj/[^\"]+)"', _routes())
    )
    assert len(patterns) == 1
    path = f"/jgp/porownaj/{'b' * 64}/2026-09-30/catalog-before/2026-10-01/catalog-after/{suffix}"
    assert bool(re.fullmatch(patterns[0], path)) is accepted
    assert bool(COMPARISON_ROUTE.fullmatch(path)) is accepted


@pytest.mark.parametrize(
    "suffix,accepted",
    [
        ("A01", True),
        ("00.00001/jgp/2026-07-01", True),
        ("A01/jgp/2026-07-01/1ae/pages/20", True),
        ("A01/jgp/2026-07-01/1a/pages/21", False),
        ("A01/jgp/2026-07-01/1a/pages/01", False),
        ("A01/jgp/2026-7-01", False),
        ("a01", False),
        ("A01?date=2026-07-01", False),
        ("A01/", False),
        ("private text", False),
    ],
)
def test_concept_edge_and_log_allowlists(suffix: str, accepted: bool) -> None:
    patterns = cast(
        list[str], re.findall(r'location ~ "(\^/api/v1/concepts/[^\"]+)"', _routes())
    )
    assert len(patterns) == 1
    path = "/api/v1/concepts/icd9-pl/pinned-release/" + suffix
    assert bool(re.fullmatch(patterns[0], path)) is accepted
    assert bool(CONCEPT_ROUTE.fullmatch(path)) is accepted
