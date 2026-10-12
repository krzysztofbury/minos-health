"""Thin concept JSON adapter; path identifiers only, no implicit release/date."""

import re
from collections.abc import Callable
from typing import ClassVar, Literal, cast

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from minos.domain.jgp_ruleset import AmbiguousRuleset, NoRulesetForDate
from minos.domain.reference import ArtifactError, QueryLimitError, QueryTimeoutError
from minos.domain.search import SearchInputError
from minos.http.reference import CapacityError, ReferencePool
from minos.store.concept_detail import ContextReleaseMismatch, concept_detail

CONCEPT_ROUTE = re.compile(
    r"/api/v1/concepts/icd(?:10|9)-pl/[a-z0-9-]{1,80}/[A-Z0-9][A-Z0-9.\-]{0,15}(?:/jgp/20[0-9]{2}-[0-9]{2}-[0-9]{2}(?:/1ae?/pages/(?:[1-9]|1[0-9]|20))?)?"
)


class ConceptLookup(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", strict=True)
    system: Literal["icd10-pl", "icd9-pl"]
    release_id: str = Field(pattern=r"^[a-z0-9-]{1,80}$", max_length=80)
    code: str = Field(pattern=r"^[A-Z0-9][A-Z0-9.\-]{0,15}$", max_length=16)
    service_date: str | None = Field(
        default=None, pattern=r"^20[0-9]{2}-[0-9]{2}-[0-9]{2}$", max_length=10
    )
    catalog: Literal["1a", "1ae"] | None = None
    page: str = Field(default="1", pattern=r"^(?:[1-9]|1[0-9]|20)$", max_length=2)


def register_concept_routes(
    app: FastAPI, *, failure: Callable[[int, str], JSONResponse]
) -> None:
    def concept(
        request: Request,
        system: str,
        release_id: str,
        code: str,
        service_date: str | None = None,
        catalog: str | None = None,
        page: str = "1",
    ) -> Response:
        from datetime import date

        try:
            selection = ConceptLookup.model_validate(
                dict(
                    system=system,
                    release_id=release_id,
                    code=code,
                    service_date=service_date,
                    catalog=catalog,
                    page=page,
                )
            )
            day = (
                date.fromisoformat(selection.service_date)
                if selection.service_date
                else None
            )
            pool = cast(ReferencePool, app.state.pool)
            with pool.borrow() as lease:
                payload = concept_detail(
                    lease.connection,
                    pool.manifest,
                    selection.system,
                    selection.release_id,
                    selection.code,
                    service_date=day,
                    catalog=selection.catalog,
                    page=int(selection.page),
                )
            return Response(payload, media_type="application/json")
        except ValidationError, ValueError, SearchInputError:
            return failure(404, "not_found")
        except NoRulesetForDate:
            return failure(404, "no_ruleset_for_date")
        except ContextReleaseMismatch:
            return failure(409, "context_release_mismatch")
        except AmbiguousRuleset:
            return failure(409, "ambiguous_ruleset")
        except CapacityError:
            return failure(429, "busy")
        except QueryTimeoutError, TimeoutError:
            request.state.failure_code = "query_timeout"
            return failure(504, "query_timeout")
        except QueryLimitError:
            request.state.failure_code = "limit_exceeded"
            return failure(422, "limit_exceeded")
        except ArtifactError, OSError:
            return failure(503, "unavailable")

    for suffix in (
        "",
        "/jgp/{service_date}",
        "/jgp/{service_date}/{catalog}/pages/{page}",
    ):
        app.add_api_route(
            "/api/v1/concepts/{system}/{release_id}/{code}" + suffix,
            concept,
            methods=["GET"],
        )
