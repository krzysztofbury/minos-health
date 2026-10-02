import re
from typing import cast

import pytest

from minos.http.jgp import JGP_ROUTE
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
