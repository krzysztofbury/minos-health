"""Validation-only HTTP adapter. Queries are bodies, never URLs or telemetry."""

import asyncio
import json
import logging
import re
import time
import uuid
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Annotated, ClassVar, Literal, cast

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool

from minos.cli.reference_output import search_envelope
from minos.domain import jgp_conditions as c
from minos.domain.jgp_ruleset import (
    NoRulesetForDate,
    TimeZoneUnavailable,
    today_in_poland,
)
from minos.domain.reference import ArtifactError, QueryLimitError
from minos.domain.search import (
    SearchInputError,
    SearchRequest,
    prepare_query,
)
from minos.http.jgp import (
    JGP_PAGE_ROUTE,
    JGP_ROUTE,
    register_jgp_pages,
    register_jgp_routes,
)
from minos.http.jgp_condition_pages import membership_section
from minos.http.jgp_pages import date_bar, error_page
from minos.http.pages import (
    CODE_PATTERN,
    DICTIONARIES,
    code_page,
    missing_page,
    sitemap,
)
from minos.http.reference import CapacityError, ReferencePool
from minos.store.artifact import dataset_identity
from minos.store.jgp_condition_reference import (
    characteristics_calendar,
    code_memberships,
)
from minos.store.komunikat import procedure_rank_evidence
from minos.store.search import search_reference
from minos.store.tree import (
    Hierarchy,
    all_codes,
    find_code,
    resolve_hierarchy,
    tree_node,
    tree_roots,
)

LOGGER = logging.getLogger("minos.http")
TREE_ROUTE = re.compile(r"/api/v1/tree/([a-z0-9-]{1,80})/(0|[1-9][0-9]{0,6})")
PAGE_ROUTE = re.compile(
    r"/slowniki/icd-(?:10|9)/[A-Za-z0-9][A-Za-z0-9.\-]{0,15}(?:/jgp/20[0-9]{2}-[0-9]{2}-[0-9]{2}(?:/1ae?/[1-9][0-9]{0,1})?)?"
)
SITEMAP_ROUTE = re.compile(r"/sitemap-icd-(?:10|9)\.xml")
RANK_ROUTE = re.compile(
    r"/api/v1/procedure-ranks/[a-z0-9-]{1,80}/[A-Za-z0-9][A-Za-z0-9.\-]{0,15}"
)
PAGE_CACHE = "public, max-age=3600"


def until_polish_midnight() -> str:
    """Undated ICD pages show JGP data for today, which changes at midnight in
    Poland; cache them until then (at most an hour), never across the change."""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        now = datetime.now(ZoneInfo("Europe/Warsaw"))
    except ZoneInfoNotFoundError:
        return "no-store"
    midnight = (now + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    seconds = int((midnight - now).total_seconds())
    return f"public, max-age={min(3600, seconds)}" if seconds > 0 else "no-store"


DISCLAIMER = "Validation only. Reference data, not medical advice or a binding settlement decision. Code documented care only; never infer billability."


class Lookup(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", strict=True)
    system: Annotated[str, Field(min_length=1, max_length=80)]
    release_id: Annotated[str, Field(min_length=1, max_length=80)]
    query: Annotated[str, Field(min_length=1, max_length=256)]
    language: Literal["pl", "en"] = "pl"
    limit: Annotated[int, Field(ge=1, le=20)] = 10
    service_date: date | None = None


def failure(status: int, code: str) -> JSONResponse:
    return JSONResponse(
        {"api": 1, "status": "error", "error": {"code": code}}, status_code=status
    )


def lookup(pool: ReferencePool, payload: Lookup) -> dict[str, object]:
    with pool.borrow() as lease:
        result = search_reference(
            lease.connection,
            SearchRequest(
                payload.system,
                prepare_query(payload.query, language=payload.language),
                release_id=payload.release_id,
                language=payload.language,
                limit=payload.limit,
                service_date=payload.service_date,
            ),
            analyzer=lease.analyzer,
        )
        answer = search_envelope(result, pool.manifest, disclaimer=DISCLAIMER)
        answer["api"] = 1
        return answer


@dataclass(frozen=True)
class Tree:
    hierarchy: Hierarchy
    roots: dict[str, object]
    system: str
    version: str
    sitemap: bytes


def load_trees(pool: ReferencePool) -> dict[str, Tree]:
    """Precompute each catalog's root listing; the release scan is too slow per request."""
    trees: dict[str, Tree] = {}
    with pool.borrow() as lease:
        for source in pool.manifest.dataset.sources:
            if source.context_for is not None:
                continue
            hierarchy = resolve_hierarchy(lease.connection, source.release_id)
            roots = tree_roots(lease.connection, hierarchy)
            codes = all_codes(lease.connection, hierarchy)
            unplaced = cast(list[dict[str, object]], roots.get("unplaced", []))
            codes += [cast(str, item["code"]) for item in unplaced]
            dictionary = next(
                (
                    item
                    for item in DICTIONARIES.values()
                    if item.system == source.system
                ),
                None,
            )
            trees[source.release_id] = Tree(
                hierarchy,
                roots,
                source.system,
                source.source_version,
                sitemap(dictionary, codes) if dictionary else b"",
            )
    return trees


def render_code(
    pool: ReferencePool,
    trees: dict[str, Tree],
    slug: str,
    code: str,
    service_date: date | None = None,
    membership_catalog: str | None = None,
    membership_page: int = 1,
) -> tuple[int, str]:
    dictionary = DICTIONARIES[slug]
    tree = next(
        (item for item in trees.values() if item.system == dictionary.system), None
    )
    if tree is None:
        raise ArtifactError("dictionary is not in the dataset")
    canonical_icd9 = any(
        source.release_id == tree.hierarchy.release_id
        and (source.system, source.origin) == ("icd9-pl", "czs")
        for source in pool.manifest.dataset.sources
    )
    # One lease per page: the pool has no wait queue, so a second borrow could
    # turn an already-resolved page into a capacity failure.
    with pool.borrow() as lease:
        ordinal = find_code(lease.connection, tree.hierarchy, code)
        if ordinal is None:
            unplaced = cast(list[dict[str, object]], tree.roots.get("unplaced", []))
            item = next((entry for entry in unplaced if entry["code"] == code), None)
            if item is None:
                return 404, missing_page(dictionary, code)
            node: dict[str, object] = {**item, "path": [], "notes": []}
            children: list[dict[str, object]] = []
        else:
            answer = tree_node(lease.connection, tree.hierarchy, ordinal)
            node = cast(dict[str, object], answer["node"])
            children = cast(list[dict[str, object]], answer["children"])
        # Supporting-only workbook nodes have no canonical concept to rank.
        ranks = (
            procedure_rank_evidence(
                lease.connection,
                pool.manifest.dataset,
                tree.hierarchy.release_id,
                code,
            )
            if canonical_icd9 and node.get("canonical") is not False
            else None
        )
        jgp_section = ""
        day: date | None = service_date
        if day is None and pool.manifest.dataset.characteristics:
            try:
                day = today_in_poland()
            except TimeZoneUnavailable:
                # The ICD page itself never depended on the clock; only its
                # dated JGP section is omitted when "today" is unknown.
                day = None
        if day is not None and pool.manifest.dataset.characteristics:
            memberships: list[dict[str, object]] = []
            for catalog in ("1a", "1ae"):
                try:
                    answer = code_memberships(
                        lease.connection,
                        cast(c.System, dictionary.system),
                        code,
                        catalog,
                        day,
                        membership_page if membership_catalog == catalog else 1,
                    )
                    memberships.append({"status": "covered", **answer})
                except NoRulesetForDate:
                    memberships.append(
                        {"status": "context_not_covered", "catalog": catalog}
                    )
            if service_date is not None and all(
                a["status"] != "covered" for a in memberships
            ):
                raise NoRulesetForDate("no characteristics cover the selected day")
            jgp_section = date_bar(
                day.isoformat(),
                "",
                characteristics_calendar(lease.connection),
                prefix=f"/slowniki/{slug}/{code}/jgp/",
            )
            jgp_section += membership_section(
                memberships, slug=slug, code=code, day=day.isoformat()
            )
            if dictionary.system == "icd10-pl" and pool.manifest.schema_version >= 8:
                from minos.http.jgp_condition_pages import shared_procedure_section
                from minos.store.search_jgp import shared_procedures

                jgp_section += shared_procedure_section(
                    shared_procedures(
                        lease.connection, code, day, tree.hierarchy.release_id
                    )
                )
        elif service_date is not None:
            raise NoRulesetForDate("bundle has no characteristics")
    return 200, code_page(
        dictionary,
        node,
        children,
        tree.version,
        tree.hierarchy.supporting,
        ranks,
        jgp_section,
        f"/slowniki/{slug}/{code}/jgp/{service_date}" if service_date else None,
    )


def browse(
    pool: ReferencePool, trees: dict[str, Tree], release_id: str, ordinal: int
) -> dict[str, object]:
    tree = trees.get(release_id)
    if tree is None:
        raise SearchInputError("release is not a selectable catalog")
    if ordinal == 0:
        answer = dict(tree.roots)
    else:
        with pool.borrow() as lease:
            answer = tree_node(lease.connection, tree.hierarchy, ordinal)
    answer.update(
        {
            "api": 1,
            "release_id": release_id,
            "hierarchy_release_id": tree.hierarchy.hierarchy_release_id,
            "supporting_hierarchy": tree.hierarchy.supporting,
        }
    )
    return answer


def create_app(bundle: Path, *, expected_database_sha256: str | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
        pool = await run_in_threadpool(ReferencePool, bundle)
        if (
            expected_database_sha256 is not None
            and pool.manifest.database_sha256 != expected_database_sha256
        ):
            pool.close()
            raise ArtifactError(
                "reference dataset is not the selected deployment artifact"
            )
        _app.state.pool = pool
        try:
            _app.state.trees = await run_in_threadpool(load_trees, pool)
        except BaseException:
            pool.close()
            raise
        try:
            yield
        finally:
            pool.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    async def guard(request: Request, call_next: object) -> JSONResponse:
        from collections.abc import Awaitable

        request_id = uuid.uuid4().hex
        began = time.monotonic()
        try:
            if request.url.query:
                response: Response = failure(400, "queries_must_not_be_in_urls")
            else:
                handler = cast(Callable[[Request], Awaitable[Response]], call_next)
                response = await handler(request)
        except Exception:
            # Fixed failures deliberately omit exception/input/path representations.
            response = failure(503, "unavailable")
        if "Cache-Control" not in response.headers:
            response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Request-ID"] = request_id
        LOGGER.info(
            json.dumps(
                {
                    "request_id": request_id,
                    "route": request.url.path
                    if request.url.path
                    in ("/healthz", "/readyz", "/api/v1/releases", "/api/v1/search")
                    else "/api/v1/tree"
                    if TREE_ROUTE.fullmatch(request.url.path)
                    else "/slowniki/code"
                    if PAGE_ROUTE.fullmatch(request.url.path)
                    else "/sitemap"
                    if SITEMAP_ROUTE.fullmatch(request.url.path)
                    else "/api/v1/procedure-ranks"
                    if RANK_ROUTE.fullmatch(request.url.path)
                    else "/api/v1/jgp"
                    if JGP_ROUTE.fullmatch(request.url.path)
                    else "/jgp"
                    if JGP_PAGE_ROUTE.fullmatch(request.url.path)
                    else "unmatched",
                    "method": request.method
                    if request.method in ("GET", "POST", "HEAD", "OPTIONS")
                    else "OTHER",
                    "status_class": response.status_code // 100,
                    "duration_ms": min(
                        round((time.monotonic() - began) * 1000), 10_000
                    ),
                    "result_count": getattr(request.state, "result_count", None),
                    "dataset_id": dataset_identity(
                        cast(ReferencePool, app.state.pool).manifest.dataset
                    ),
                }
            )
        )
        return cast(JSONResponse, response)

    def health() -> dict[str, object]:
        return {"api": 1, "status": "ok", "mode": "validation"}

    def ready() -> JSONResponse:
        pool = cast(ReferencePool, app.state.pool)
        try:
            with pool.borrow() as lease:
                if lease.connection.execute(
                    "SELECT count(*) FROM source_release"
                ).fetchone() != (len(pool.manifest.dataset.sources),):
                    return failure(503, "unavailable")
            return JSONResponse(
                {
                    "api": 1,
                    "status": "ready",
                    "dataset_id": dataset_identity(pool.manifest.dataset),
                }
            )
        except CapacityError:
            return failure(503, "busy")
        except ArtifactError, OSError:
            return failure(503, "unavailable")

    def releases() -> dict[str, object]:
        pool = cast(ReferencePool, app.state.pool)
        return {
            "api": 1,
            "mode": "validation",
            "disclaimer": DISCLAIMER,
            "dataset_id": dataset_identity(pool.manifest.dataset),
            "releases": [
                {
                    "release_id": source.release_id,
                    "system": source.system,
                    "origin": source.origin,
                    "source_version": source.source_version,
                    "publisher": source.publisher,
                    "source_url": source.source_url,
                    "license": source.license,
                    "notices": source.notices,
                    "supporting_sources": [
                        {
                            "release_id": evidence.release_id,
                            "source_sha256": evidence.sha256,
                            "source_url": evidence.source_url,
                            "publisher": evidence.publisher,
                            "source_version": evidence.source_version,
                            "license": evidence.license,
                            "notices": evidence.notices,
                        }
                        for evidence in pool.manifest.dataset.sources
                        if evidence.context_for == source.release_id
                    ],
                }
                for source in pool.manifest.dataset.sources
                if source.context_for is None
            ],
        }

    async def search(request: Request) -> JSONResponse:
        if (
            request.headers.get("content-type", "").split(";", 1)[0]
            != "application/json"
        ):
            return failure(415, "json_required")
        try:
            body = bytearray()
            async with asyncio.timeout(2):
                async for chunk in request.stream():
                    if len(body) + len(chunk) > 4096:
                        return failure(413, "body_too_large")
                    body.extend(chunk)
            payload = Lookup.model_validate_json(bytes(body))
            answer = await run_in_threadpool(
                lookup, cast(ReferencePool, app.state.pool), payload
            )
            request.state.result_count = len(cast(list[object], answer["results"]))
            return JSONResponse(answer)
        except ValidationError, SearchInputError, ValueError:
            return failure(400, "invalid_request")
        except CapacityError:
            return failure(429, "busy")
        except QueryLimitError, TimeoutError:
            return failure(429, "limit_exceeded")
        except ArtifactError, OSError:
            return failure(503, "unavailable")

    _ = app.middleware("http")(guard)

    async def tree(release_id: str, node: str) -> JSONResponse:
        # Paths carry only a release identity and a source node number, never text.
        if TREE_ROUTE.fullmatch(f"/api/v1/tree/{release_id}/{node}") is None:
            return failure(404, "not_found")
        try:
            answer = await run_in_threadpool(
                browse,
                cast(ReferencePool, app.state.pool),
                cast(dict[str, Tree], app.state.trees),
                release_id,
                int(node),
            )
            return JSONResponse(answer)
        except SearchInputError:
            return failure(404, "not_found")
        except CapacityError:
            return failure(429, "busy")
        except QueryLimitError, TimeoutError:
            return failure(429, "limit_exceeded")
        except ArtifactError, OSError:
            return failure(503, "unavailable")

    async def dictionary_page(
        dictionary: str,
        code: str,
        service_date: str | None = None,
        membership_catalog: str | None = None,
        membership_page: str | None = None,
    ) -> Response:
        # Codes are public identifiers, never typed free text; no query strings.
        if dictionary not in DICTIONARIES or not CODE_PATTERN.fullmatch(code):
            return failure(404, "not_found")
        if membership_catalog is not None and (
            membership_catalog not in ("1a", "1ae")
            or membership_page is None
            or not re.fullmatch(r"[1-9][0-9]?", membership_page)
        ):
            return failure(404, "not_found")
        selected: date | None = None
        if service_date is not None:
            if not re.fullmatch(r"20[0-9]{2}-[0-9]{2}-[0-9]{2}", service_date):
                return failure(404, "not_found")
            try:
                selected = date.fromisoformat(service_date)
            except ValueError:
                return failure(404, "not_found")
        if code != code.upper():
            return RedirectResponse(
                f"/slowniki/{dictionary}/{code.upper()}"
                + (f"/jgp/{service_date}" if service_date else "")
                + (
                    f"/{membership_catalog}/{membership_page}"
                    if membership_catalog
                    else ""
                ),
                status_code=301,
            )
        try:
            status, page = await run_in_threadpool(
                render_code,
                cast(ReferencePool, app.state.pool),
                cast(dict[str, Tree], app.state.trees),
                dictionary,
                code,
                selected,
                membership_catalog,
                int(membership_page) if membership_page else 1,
            )
        except NoRulesetForDate:
            # A page route: a branded HTML error, never a JSON envelope.
            return HTMLResponse(
                error_page(404, "no_ruleset_for_date", None, None),
                status_code=404,
                headers={"Cache-Control": "no-store"},
            )
        except SearchInputError:
            return failure(404, "not_found")
        except CapacityError:
            return failure(429, "busy")
        except QueryLimitError, TimeoutError:
            return failure(429, "limit_exceeded")
        except ArtifactError, OSError:
            return failure(503, "unavailable")
        return HTMLResponse(
            page,
            status_code=status,
            headers={
                "Cache-Control": "no-store"
                if status != 200
                else PAGE_CACHE
                if service_date is not None
                or not cast(
                    ReferencePool, app.state.pool
                ).manifest.dataset.characteristics
                else until_polish_midnight()
            },
        )

    def dictionary_sitemap(dictionary: str) -> Response:
        slug = dictionary.removesuffix(".xml")
        tree = next(
            (
                item
                for item in cast(dict[str, Tree], app.state.trees).values()
                if slug in DICTIONARIES and item.system == DICTIONARIES[slug].system
            ),
            None,
        )
        if tree is None or not tree.sitemap:
            return failure(404, "not_found")
        return Response(
            tree.sitemap,
            media_type="application/xml",
            headers={"Cache-Control": PAGE_CACHE},
        )

    def procedure_rank(release_id: str, code: str) -> JSONResponse:
        if not CODE_PATTERN.fullmatch(code) or len(release_id) > 80:
            return failure(404, "not_found")
        pool = cast(ReferencePool, app.state.pool)
        try:
            with pool.borrow() as lease:
                evidence = procedure_rank_evidence(
                    lease.connection, pool.manifest.dataset, release_id, code
                )
            return JSONResponse({"api": 1, "evidence": evidence})
        except SearchInputError:
            return failure(404, "not_found")
        except CapacityError:
            return failure(429, "busy")
        except QueryLimitError, TimeoutError:
            return failure(429, "limit_exceeded")
        except ArtifactError, OSError:
            return failure(503, "unavailable")

    app.add_api_route("/healthz", health, methods=["GET"])
    app.add_api_route("/readyz", ready, methods=["GET"])
    app.add_api_route("/api/v1/releases", releases, methods=["GET"])
    app.add_api_route("/api/v1/search", search, methods=["POST"])
    app.add_api_route("/api/v1/tree/{release_id}/{node}", tree, methods=["GET"])
    app.add_api_route("/slowniki/{dictionary}/{code}", dictionary_page, methods=["GET"])
    app.add_api_route(
        "/slowniki/{dictionary}/{code}/jgp/{service_date}",
        dictionary_page,
        methods=["GET"],
    )
    app.add_api_route(
        "/slowniki/{dictionary}/{code}/jgp/{service_date}/{membership_catalog}/{membership_page}",
        dictionary_page,
        methods=["GET"],
    )
    app.add_api_route("/sitemap-{dictionary}", dictionary_sitemap, methods=["GET"])
    app.add_api_route(
        "/api/v1/procedure-ranks/{release_id}/{code}", procedure_rank, methods=["GET"]
    )
    register_jgp_routes(
        app, failure=failure, disclaimer=DISCLAIMER, cache_control=PAGE_CACHE
    )
    register_jgp_pages(app, cache_control=PAGE_CACHE)
    return app


def application() -> FastAPI:
    import os
    import sys

    if not LOGGER.handlers:
        LOGGER.addHandler(logging.StreamHandler(sys.stdout))
        LOGGER.setLevel(logging.INFO)
    return create_app(
        Path(os.environ.get("MINOS_REFERENCE_BUNDLE", "/app/data")),
        expected_database_sha256=os.environ.get("MINOS_REFERENCE_DATABASE_SHA256", ""),
    )
