"""Validation-only HTTP adapter. Queries are bodies, never URLs or telemetry."""

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, ClassVar, Literal, cast

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool

from minos.cli.reference_output import search_envelope
from minos.domain.reference import ArtifactError, QueryLimitError
from minos.domain.search import (
    SEARCH_VERSION,
    SearchInputError,
    SearchRequest,
    prepare_query,
)
from minos.http.reference import CapacityError, ReferencePool
from minos.store.artifact import dataset_identity
from minos.store.search import search_reference

LOGGER = logging.getLogger("minos.http")
DISCLAIMER = "Validation only. Reference data, not medical advice or a binding settlement decision. Code documented care only; never infer billability."


class Lookup(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", strict=True)
    system: Annotated[str, Field(min_length=1, max_length=80)]
    release_id: Annotated[str, Field(min_length=1, max_length=80)]
    query: Annotated[str, Field(min_length=1, max_length=256)]
    language: Literal["pl", "en"] = "pl"
    limit: Annotated[int, Field(ge=1, le=20)] = 10


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
            ),
            analyzer=lease.analyzer,
        )
        answer = search_envelope(
            result, pool.manifest, disclaimer=DISCLAIMER, search_version=SEARCH_VERSION
        )
        answer["api"] = 1
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
            yield
        finally:
            pool.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    async def guard(request: Request, call_next: object) -> JSONResponse:
        from collections.abc import Awaitable, Callable

        from starlette.responses import Response

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
    app.add_api_route("/healthz", health, methods=["GET"])
    app.add_api_route("/readyz", ready, methods=["GET"])
    app.add_api_route("/api/v1/releases", releases, methods=["GET"])
    app.add_api_route("/api/v1/search", search, methods=["POST"])
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
