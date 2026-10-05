"""JGP catalog base-point routes for the validation API (#24).

Dates are path segments because every route rejects query strings. Answers use
the bundle's reviewed knowledge horizon, so they are fixed per bundle and
cacheable; a service date after the horizon is flagged in the ruleset.
"""

import re
import sqlite3
from collections.abc import Callable
from datetime import date, timedelta
from typing import Literal, cast

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from minos.domain.jgp_ruleset import (
    AmbiguousRuleset,
    GroupNotOnDate,
    KnowledgeBeyondBundle,
    NoRulesetForDate,
    SectionNotOnDate,
    TimeZoneUnavailable,
    today_in_poland,
)
from minos.domain.reference import ArtifactError, QueryLimitError, QueryTimeoutError
from minos.domain.search import SearchInputError
from minos.http.jgp_pages import error_page, group_page, index_page
from minos.http.reference import CapacityError, ReferencePool
from minos.ingest.jgp_catalog import GROUP_CODE_REGEX
from minos.store.jgp_condition_reference import list_members, reference_group
from minos.store.jgp_ruleset import (
    catalog_calendar,
    catalog_group,
    catalog_index,
    catalog_scope_labels,
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
    + rf"/(?:groups/{GROUP_CODE_REGEX}|sections|sections/1ae?/{POSITION_REGEX}"
    + r"|lists/1ae?/[1-9][0-9]{0,3}/pages/[1-9][0-9]{0,2})"
)
# Page paths, mirrored by the validation edge; lowercase codes are redirected.
ANY_CASE_CODE_REGEX = r"[A-Za-z]{1,4}[0-9]{2,3}[A-Za-z]{0,3}"
JGP_PAGE_ROUTE = re.compile(
    rf"/jgp(?:/(?:{SERVICE_DATE_REGEX}(?:/(?:{ANY_CASE_CODE_REGEX}|zakres/{POSITION_REGEX}"
    + r"|listy/1ae?/[1-9][0-9]{0,3}/[1-9][0-9]{0,2}))?"
    + rf"|{ANY_CASE_CODE_REGEX}))?"
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
        except QueryTimeoutError, TimeoutError:
            return failure(504, "query_timeout")
        except QueryLimitError:
            return failure(422, "limit_exceeded")
        except ArtifactError, OSError:
            return failure(503, "unavailable")

    def jgp_group(service_date: str, code: str) -> JSONResponse:
        if not GROUP_CODE.fullmatch(code):
            return failure(404, "not_found")
        return jgp_answer(
            service_date,
            lambda connection, ruleset: with_display(
                reference_group(
                    connection,
                    ruleset,
                    code,
                    available=bool(
                        cast(
                            ReferencePool, app.state.pool
                        ).manifest.dataset.characteristics
                    ),
                )
            ),
        )

    def jgp_list(
        service_date: str, catalog: str, identity: str, page: str
    ) -> JSONResponse:
        if (
            catalog not in ("1a", "1ae")
            or not re.fullmatch(r"[1-9][0-9]{0,3}", identity)
            or not POSITION.fullmatch(page)
            or cast(ReferencePool, app.state.pool).manifest.schema_version < 5
        ):
            return failure(404, "not_found")
        selected_catalog: Literal["1a", "1ae"] = "1a" if catalog == "1a" else "1ae"
        return jgp_answer(
            service_date,
            lambda connection, ruleset: list_members(
                connection,
                selected_catalog,
                int(identity),
                date.fromisoformat(cast(str, ruleset["service_date"])),
                int(page),
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
        "/api/v1/jgp/{service_date}/lists/{catalog}/{identity}/pages/{page}",
        jgp_list,
        methods=["GET"],
    )
    app.add_api_route(
        "/api/v1/jgp/{service_date}/sections", jgp_sections, methods=["GET"]
    )
    app.add_api_route(
        "/api/v1/jgp/{service_date}/sections/{catalog}/{position}",
        jgp_section,
        methods=["GET"],
    )


def verified_neighbours(
    connection: sqlite3.Connection, code: str, answer: dict[str, object]
) -> list[tuple[str, str]]:
    """Links to the days just outside this row's window, only where the group
    has a row then (the day before may belong to a release without it)."""
    group = cast(dict[str, object], answer["group"])
    if group["applicability"] is None:
        return []
    window = cast(dict[str, object], group["applicability"])
    end = cast(str | None, window["service_to"])
    start = date.fromisoformat(cast(str, window["service_from"]))
    candidates = [(f"Zobacz {start - timedelta(days=1)}", start - timedelta(days=1))]
    if end is not None:
        after = date.fromisoformat(end) + timedelta(days=1)
        candidates.insert(0, (f"Zobacz od {after}", after))
    links: list[tuple[str, str]] = []
    for label, day in candidates:
        if not SERVICE_DATE.fullmatch(day.isoformat()):
            continue
        try:
            _ = catalog_group(connection, resolve_ruleset(connection, day, None), code)
        except NoRulesetForDate, GroupNotOnDate, AmbiguousRuleset:
            continue
        links.append((label, day.isoformat()))
    return links


def register_jgp_pages(app: FastAPI, *, cache_control: str) -> None:
    """Server-rendered /jgp pages: one resolved release per page, no queries."""

    def html(status: int, body: str, cache: str) -> HTMLResponse:
        return HTMLResponse(body, status_code=status, headers={"Cache-Control": cache})

    def error(
        status: int,
        code: str,
        service_date: str | None = None,
        group: str | None = None,
        calendar: list[str] | None = None,
    ) -> HTMLResponse:
        page = error_page(status, code, service_date, group, calendar)
        response = html(status, page, "no-store")
        if status in (429, 503):
            response.headers["Retry-After"] = "1"
        return response

    def today_redirect(suffix: str) -> Response:
        try:
            today = today_in_poland().isoformat()
        except TimeZoneUnavailable:
            return error(503, "timezone_unavailable")
        # "Today" changes at midnight, so the redirect itself is never cached.
        return RedirectResponse(
            f"/jgp/{today}{suffix}",
            status_code=302,
            headers={"Cache-Control": "no-store"},
        )

    def render(
        service_date: str,
        group: str | None,
        page: Callable[[sqlite3.Connection, dict[str, object], list[str]], str],
    ) -> HTMLResponse:
        if not SERVICE_DATE.fullmatch(service_date):
            return error(404, "not_found")
        try:
            selected = date.fromisoformat(service_date)
        except ValueError:
            return error(404, "not_found")
        pool = cast(ReferencePool, app.state.pool)
        if pool.manifest.schema_version < 4:
            return error(404, "not_found")
        # Read first, so date-specific errors can still offer the date bar.
        calendar: list[str] | None = None
        try:
            with pool.borrow() as lease:
                calendar = catalog_calendar(lease.connection)
                ruleset = resolve_ruleset(lease.connection, selected, None)
                body = page(lease.connection, ruleset, calendar)
            return html(200, body, cache_control)
        except NoRulesetForDate:
            return error(404, "no_ruleset_for_date", service_date, group, calendar)
        except GroupNotOnDate:
            return error(404, "group_not_on_date", service_date, group, calendar)
        except SectionNotOnDate:
            return error(404, "scope_not_found", service_date, group, calendar)
        except AmbiguousRuleset:
            return error(409, "ambiguous_ruleset", service_date, group, calendar)
        except CapacityError:
            return error(429, "busy")
        except QueryTimeoutError, TimeoutError:
            return error(504, "query_timeout")
        except QueryLimitError:
            return error(422, "limit_exceeded")
        except SearchInputError:
            return error(404, "not_found")
        # The default knowledge date is the horizon, so KnowledgeBeyondBundle
        # here means an inconsistent bundle, like any other artifact failure.
        except ArtifactError, KnowledgeBeyondBundle, OSError:
            return error(503, "unavailable")

    def catalog(service_date: str, scope_position: int | None) -> HTMLResponse:
        def page(
            connection: sqlite3.Connection,
            ruleset: dict[str, object],
            calendar: list[str],
        ) -> str:
            scopes = catalog_scope_labels(connection, ruleset)
            scope = next(
                (item for item in scopes if item["position"] == scope_position), None
            )
            if scope_position is not None and scope is None:
                raise SectionNotOnDate("no such scope")
            return index_page(
                ruleset,
                catalog_index(connection, ruleset, scope_position),
                scopes,
                scope,
                calendar,
            )

        return render(service_date, None, page)

    def jgp_root() -> Response:
        return today_redirect("")

    def jgp_segment(segment: str) -> Response:
        if SERVICE_DATE.fullmatch(segment):
            return catalog(segment, None)
        if GROUP_CODE.fullmatch(segment):
            return today_redirect(f"/{segment}")
        if GROUP_CODE.fullmatch(segment.upper()):
            return RedirectResponse(f"/jgp/{segment.upper()}", status_code=301)
        return error(404, "not_found")

    def jgp_group_page(service_date: str, code: str) -> Response:
        if not GROUP_CODE.fullmatch(code):
            if GROUP_CODE.fullmatch(code.upper()) and SERVICE_DATE.fullmatch(
                service_date
            ):
                return RedirectResponse(
                    f"/jgp/{service_date}/{code.upper()}", status_code=301
                )
            return error(404, "not_found")

        def page(
            connection: sqlite3.Connection,
            ruleset: dict[str, object],
            calendar: list[str],
        ) -> str:
            answer = reference_group(
                connection,
                ruleset,
                code,
                available=bool(
                    cast(ReferencePool, app.state.pool).manifest.dataset.characteristics
                ),
            )
            neighbours = verified_neighbours(connection, code, answer)
            return group_page(answer, calendar, neighbours)

        return render(service_date, code, page)

    def jgp_scope_page(service_date: str, position: str) -> Response:
        if not POSITION.fullmatch(position):
            return error(404, "not_found")
        return catalog(service_date, int(position))

    def jgp_list_page(
        service_date: str, catalog: str, identity: str, page_number: str
    ) -> Response:
        from minos.http.jgp_pages import directional_list_page

        if (
            catalog not in ("1a", "1ae")
            or not re.fullmatch(r"[1-9][0-9]{0,3}", identity)
            or not POSITION.fullmatch(page_number)
            or cast(ReferencePool, app.state.pool).manifest.schema_version < 5
        ):
            return error(404, "not_found")
        selected_catalog: Literal["1a", "1ae"] = "1a" if catalog == "1a" else "1ae"

        def page(
            connection: sqlite3.Connection,
            ruleset: dict[str, object],
            _calendar: list[str],
        ) -> str:
            answer = list_members(
                connection,
                selected_catalog,
                int(identity),
                date.fromisoformat(cast(str, ruleset["service_date"])),
                int(page_number),
            )
            return directional_list_page(answer)

        return render(service_date, None, page)

    app.add_api_route("/jgp", jgp_root, methods=["GET"])
    app.add_api_route("/jgp/{segment}", jgp_segment, methods=["GET"])
    app.add_api_route("/jgp/{service_date}/{code}", jgp_group_page, methods=["GET"])
    app.add_api_route(
        "/jgp/{service_date}/zakres/{position}", jgp_scope_page, methods=["GET"]
    )
    app.add_api_route(
        "/jgp/{service_date}/listy/{catalog}/{identity}/{page_number}",
        jgp_list_page,
        methods=["GET"],
    )
