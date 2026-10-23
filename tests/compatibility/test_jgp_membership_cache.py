"""The cached JGP list path equals the full query on a real conditions bundle.

The synthetic fixtures reach lists directly; real bundles nest selectors and
share lists between groups (#225). Opt in with MINOS_GROUPER_BUNDLE.
"""

import os
from datetime import date
from pathlib import Path
from typing import cast

import pytest

from minos.domain import jgp_conditions as c
from minos.domain.jgp_ruleset import NoRulesetForDate
from minos.store.bundle_cache import BundleCache
from minos.store.jgp_condition_reference import (
    MEMBERSHIP_SQL,
    Catalog,
    code_memberships,
    selector_lists,
)
from minos.store.reference import open_reference_bundle

# The first `linked` branch of the full query, for every code at once: the
# selector lists are materialised so members are scanned once, not per list.
ALL_SELECTOR_LINKS = (
    MEMBERSHIP_SQL.split("), linked AS (", 1)[0]
    + """), selected AS MATERIALIZED (
  SELECT DISTINCT r.group_code,lists.value AS list_value FROM reachable r
    JOIN condition_selector s ON s.release_id=:release AND s.catalog=:catalog
      AND s.id=r.selector_id, json_each(s.payload_json,'$.body.list_ids') lists
)
SELECT DISTINCT m.system,m.code,selected.group_code,m.list_id
  FROM directional_member m JOIN selected ON m.list_id=selected.list_value
  WHERE m.release_id=:release AND m.catalog=:catalog"""
)


def _bundle() -> Path:
    bundle = os.environ.get("MINOS_GROUPER_BUNDLE")
    if not bundle:
        pytest.skip("real pinned conditions bundle opt-in is not configured")
    return Path(bundle)


def test_cached_reachability_reproduces_every_selector_link() -> None:
    with open_reference_bundle(_bundle()) as (connection, _):
        sources = cast(
            list[tuple[str, Catalog]],
            connection.execute(
                "SELECT DISTINCT release_id,catalog FROM group_condition ORDER BY 1,2"
            ).fetchall(),
        )
        assert sources
        for release, catalog in sources:
            parameters = {"release": release, "catalog": catalog}
            expected = set(
                cast(
                    list[tuple[str, str, str, int]],
                    connection.execute(ALL_SELECTOR_LINKS, parameters).fetchall(),
                )
            )
            reach = selector_lists(connection, release, catalog)
            members = cast(
                list[tuple[str, str, int]],
                connection.execute(
                    """SELECT DISTINCT system,code,list_id FROM directional_member
                    WHERE release_id=:release AND catalog=:catalog""",
                    parameters,
                ).fetchall(),
            )
            derived = {
                (system, code, group, list_id)
                for system, code, list_id in members
                for group in reach.get(list_id, ())
            }
            assert derived == expected, (release, catalog)


def test_cached_memberships_equal_the_full_query_on_sampled_codes() -> None:
    cache = BundleCache()
    with open_reference_bundle(_bundle()) as (connection, _):
        # Every 50th listed code, across both systems: a fixed, spread sample.
        listed = cast(
            list[tuple[str, str]],
            connection.execute(
                "SELECT DISTINCT system,code FROM directional_member ORDER BY 1,2"
            ).fetchall(),
        )[::50]
        days = [date(2026, 5, 1), date(2026, 10, 1)]
        catalogs: tuple[Catalog, ...] = ("1a", "1ae")
        compared = 0
        for system, code in listed:
            for day in days:
                for catalog in catalogs:
                    arguments = (
                        connection,
                        cast(c.System, system),
                        code,
                        catalog,
                        day,
                        1,
                    )
                    try:
                        expected = code_memberships(*arguments)
                    except NoRulesetForDate:
                        with pytest.raises(NoRulesetForDate):
                            _ = code_memberships(*arguments, cache)
                        continue
                    assert code_memberships(*arguments, cache) == expected
                    compared += 1
        assert compared > 100
