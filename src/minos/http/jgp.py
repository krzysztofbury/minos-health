"""JGP catalog base-point routes for the validation API (#24).

Dates are path segments because every route rejects query strings. Answers use
the bundle's reviewed knowledge horizon, so they are fixed per bundle and
cacheable; a service date after the horizon is flagged in the ruleset.
"""

import re
import sqlite3
from collections.abc import Callable
from datetime import date
from typing import cast

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from minos.domain.jgp_ruleset import (
    AmbiguousRuleset,
    GroupNotOnDate,
    KnowledgeBeyondBundle,
    NoRulesetForDate,
    SectionNotOnDate,
)
from minos.domain.reference import ArtifactError, QueryLimitError
from minos.domain.search import SearchInputError
from minos.http.reference import CapacityError, ReferencePool
from minos.ingest.jgp_catalog import GROUP_CODE_REGEX
from minos.store.jgp_ruleset import (
    catalog_group,
    catalog_section,
    catalog_sections,
    points_display,
    resolve_ruleset,
)

SERVICE_DATE_REGEX = r"20[0-9]{2}-[0-9]{2}-[0-9]{2}"
POSITION_REGEX = r"[1-9][0-9]{0,2}"
# scripts/build_validation_edge.py mirrors this for nginx; a unit test keeps
# both accepting exactly the same paths.
JGP_ROUTE = re.compile(
    rf"/api/v1/jgp/{SERVICE_DATE_REGEX}"
    + rf"/(?:groups/{GROUP_CODE_REGEX}|sections|sections/1ae?/{POSITION_REGEX})"
)
SERVICE_DATE = re.compile(SERVICE_DATE_REGEX)
GROUP_CODE = re.compile(GROUP_CODE_REGEX)
POSITION = re.compile(POSITION_REGEX)

type Failure = Callable[[int, str], JSONResponse]
type AnswerFor = Callable[[sqlite3.Connection, dict[str, object]], dict[str, object]]


def with_display(answer: dict[str, object]) -> dict[str, object]:
    """Add exact decimal text beside each integer so clients never divide floats."""
    for value in cast(list[dict[str, object]], answer["base_values"]):
        amount = cast(int, value["amount"])
        value["display"] = (
            points_display(amount)
            if value["unit"] == "points_hundredths"
            else str(amount)
        )
    return answer


def register_jgp_routes(
    app: FastAPI, *, failure: Failure, disclaimer: str, cache_control: str
) -> None:
    def jgp_answer(service_date: str, answer_for: AnswerFor) -> JSONResponse:
        if not SERVICE_DATE.fullmatch(service_date):
            return failure(404, "not_found")
        try:
            selected = date.fromisoformat(service_date)
        except ValueError:
            return failure(404, "not_found")
        pool = cast(ReferencePool, app.state.pool)
        if pool.manifest.schema_version < 4:
            return failure(404, "no_jgp_catalog")
        try:
            with pool.borrow() as lease:
                ruleset = resolve_ruleset(lease.connection, selected, None)
                answer = answer_for(lease.connection, ruleset)
            return JSONResponse(
                {"api": 1, **answer, "disclaimer": disclaimer},
                headers={"Cache-Control": cache_control},
            )
        except NoRulesetForDate:
            return failure(404, "no_ruleset_for_date")
        except GroupNotOnDate:
            return failure(404, "group_not_on_date")
        except SectionNotOnDate:
            return failure(404, "section_not_on_date")
        except AmbiguousRuleset:
            return failure(409, "ambiguous_ruleset")
        except KnowledgeBeyondBundle:
            return failure(409, "known_on_beyond_bundle")
        except SearchInputError:
            return failure(404, "not_found")
        except CapacityError:
            return failure(429, "busy")
        except QueryLimitError, TimeoutError:
            return failure(429, "limit_exceeded")
        except ArtifactError, OSError:
            return failure(503, "unavailable")

    def jgp_group(service_date: str, code: str) -> JSONResponse:
        if not GROUP_CODE.fullmatch(code):
            return failure(404, "not_found")
        return jgp_answer(
            service_date,
            lambda connection, ruleset: with_display(
                catalog_group(connection, ruleset, code)
            ),
        )

    def jgp_sections(service_date: str) -> JSONResponse:
        return jgp_answer(
            service_date,
            lambda connection, ruleset: {
                "ruleset": ruleset,
                "sections": catalog_sections(connection, ruleset),
            },
        )

    def jgp_section(service_date: str, catalog: str, position: str) -> JSONResponse:
        if catalog not in ("1a", "1ae") or not POSITION.fullmatch(position):
            return failure(404, "not_found")
        return jgp_answer(
            service_date,
            lambda connection, ruleset: catalog_section(
                connection, ruleset, catalog, int(position)
            ),
        )

    app.add_api_route(
        "/api/v1/jgp/{service_date}/groups/{code}", jgp_group, methods=["GET"]
    )
    app.add_api_route(
        "/api/v1/jgp/{service_date}/sections", jgp_sections, methods=["GET"]
    )
    app.add_api_route(
        "/api/v1/jgp/{service_date}/sections/{catalog}/{position}",
        jgp_section,
        methods=["GET"],
    )
