"""Stateless bounded grouping transport. No request model enters telemetry."""

import asyncio
import json
import threading
import time
from collections.abc import Callable
from typing import cast, final

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from minos.cli.grouping import evaluate_json
from minos.domain.encounter import EncounterInputError
from minos.domain.grouper import GrouperError
from minos.domain.jgp_ruleset import NoRulesetForDate
from minos.domain.reference import QueryLimitError, QueryTimeoutError, ReferenceError
from minos.http.reference import CapacityError, ReferencePool
from minos.ingest.grouping_request import (
    MAX_GROUPING_REQUEST_BYTES,
    parse_grouping_request,
)

MAX_GROUPING_RESPONSE_BYTES = 4 * 1024 * 1024


@final
class GroupingAdmission:
    """Two workers and a process-wide token bucket; no IP or user state.

    A token is spent before the upload, bounding concurrent body readers to
    about twelve (burst plus two seconds of refill). A worker is taken only
    after the complete body arrives, so slow uploads cannot hold evaluation.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tokens = 8.0
        self._at = time.monotonic()
        self._active = 0

    def admit(self) -> bool:
        with self._lock:
            now = time.monotonic()
            self._tokens = min(8.0, self._tokens + (now - self._at) * 2)
            self._at = now
            if self._tokens < 1:
                return False
            self._tokens -= 1
            return True

    def acquire(self) -> bool:
        with self._lock:
            if self._active >= 2:
                return False
            self._active += 1
            return True

    def release(self) -> None:
        with self._lock:
            self._active -= 1


def evaluate(pool: ReferencePool, body: bytes) -> bytes:
    request = parse_grouping_request(body)
    with pool.borrow() as lease:
        answer = evaluate_json(lease.connection, pool.manifest, request)
    encoded = json.dumps(
        answer, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    if len(encoded) > MAX_GROUPING_RESPONSE_BYTES:
        raise QueryLimitError("grouping response exceeds bound")
    return encoded


def register_grouping_routes(
    app: FastAPI, *, failure: Callable[[int, str], JSONResponse]
) -> None:
    admission = GroupingAdmission()

    async def group(request: Request) -> Response:
        if (
            request.headers.get("content-type", "").split(";", 1)[0].strip()
            != "application/json"
        ):
            return failure(415, "json_required")
        if request.headers.get("content-encoding", "identity") != "identity":
            return failure(415, "content_encoding_unsupported")
        if not admission.admit():
            return failure(429, "busy")
        body = bytearray()
        try:
            async with asyncio.timeout(2):
                async for chunk in request.stream():
                    if len(body) + len(chunk) > MAX_GROUPING_REQUEST_BYTES:
                        return failure(413, "body_too_large")
                    body.extend(chunk)
        except TimeoutError:
            return failure(408, "request_timeout")
        if not admission.acquire():
            return failure(429, "busy")
        try:
            # AnyIO's default threadpool wait retains ownership until the worker
            # stops; a client abort must not start another unbounded CPU worker.
            encoded = await run_in_threadpool(
                evaluate, cast(ReferencePool, app.state.pool), bytes(body)
            )
            return Response(
                encoded,
                media_type="application/json",
                headers={"Cache-Control": "no-store"},
            )
        except EncounterInputError:
            return failure(400, "invalid_request")
        except NoRulesetForDate:
            return failure(422, "no_ruleset_for_date")
        except CapacityError:
            return failure(429, "busy")
        except QueryTimeoutError:
            return failure(504, "evaluation_timeout")
        except QueryLimitError:
            return failure(422, "evaluation_limit")
        except GrouperError:
            return failure(422, "grouping_unavailable")
        except ReferenceError, OSError, ValueError:
            return failure(503, "unavailable")
        finally:
            admission.release()

    app.add_api_route("/api/v1/group", group, methods=["POST"])
