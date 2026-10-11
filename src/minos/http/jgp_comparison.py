"""Bounded public catalog comparisons pinned to one verified dataset identity."""

import re
from datetime import date
from typing import Annotated, ClassVar, cast

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from minos.domain.jgp_ruleset import (
    AmbiguousRuleset,
    KnowledgeBeyondBundle,
    NoRulesetForDate,
)
from minos.domain.reference import ArtifactError, QueryLimitError, QueryTimeoutError
from minos.domain.release_diff import DiffInputError
from minos.http.jgp_comparison_pages import ALL, comparison_page
from minos.http.jgp_pages import _jgp_page  # pyright: ignore[reportPrivateUsage]
from minos.http.reference import CapacityError, ReferencePool
from minos.store.artifact import dataset_identity
from minos.store.jgp_comparison import catalog_versions, compare_catalog_dates
from minos.store.query_budget import query_budget

COMPARISON_PATH = (
    r"/jgp/porownaj/[0-9a-f]{64}/20[0-9]{2}-[0-9]{2}-[0-9]{2}"
    r"/[a-z0-9-]{1,80}/20[0-9]{2}-[0-9]{2}-[0-9]{2}/[a-z0-9-]{1,80}"
    r"/(?:wszystkie|[0-9a-f]{64})"
    r"/(?:wszystkie|[A-Z]{1,4}[0-9]{2,3}[A-Z]{0,3})"
)
COMPARISON_ROUTE = re.compile(COMPARISON_PATH)


class Selection(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)
    dataset: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    before: date
    after: date
    before_release: Annotated[str, Field(pattern=r"^[a-z0-9-]{1,80}$")]
    after_release: Annotated[str, Field(pattern=r"^[a-z0-9-]{1,80}$")]
    scope: Annotated[str, Field(pattern=r"^(wszystkie|[0-9a-f]{64})$")]
    group: Annotated[
        str, Field(pattern=r"^(wszystkie|[A-Z]{1,4}[0-9]{2,3}[A-Z]{0,3})$")
    ]


def register_comparison_pages(app: FastAPI, *, cache_control: str) -> None:
    def error(status: int, message: str) -> HTMLResponse:
        page = _jgp_page(
            title="Porównanie niedostępne - Minos",
            description="Nie można odtworzyć wybranego porównania.",
            path="/jgp",
            content=f'<div class="shell page-head"><h1>Porównanie niedostępne</h1><p>{message}</p><a href="/jgp">Wróć do katalogu JGP</a></div>',
        )
        return HTMLResponse(
            page, status_code=status, headers={"Cache-Control": "no-store"}
        )

    def compare(
        dataset: str,
        before: str,
        before_release: str,
        after: str,
        after_release: str,
        scope: str,
        group: str,
    ) -> HTMLResponse:
        if not COMPARISON_ROUTE.fullmatch(
            f"/jgp/porownaj/{dataset}/{before}/{before_release}/{after}/{after_release}/{scope}/{group}"
        ):
            return error(404, "Nieprawidłowy adres porównania.")
        try:
            selected = Selection.model_validate(
                {
                    "dataset": dataset,
                    "before": before,
                    "after": after,
                    "before_release": before_release,
                    "after_release": after_release,
                    "scope": scope,
                    "group": group,
                }
            )
        except ValidationError:
            return error(404, "Nieprawidłowy wybór dat lub filtrów.")
        pool = cast(ReferencePool, app.state.pool)
        if dataset != dataset_identity(pool.manifest.dataset):
            return error(
                409,
                "Zbiór zapisany w tym linku nie jest już dostępny. Nie zastępujemy go nowszym wydaniem.",
            )
        if pool.manifest.schema_version < 4:
            return error(404, "Zbiór nie zawiera katalogów JGP do porównania.")
        try:
            with pool.borrow() as lease, query_budget(lease.connection):
                comparison = compare_catalog_dates(
                    lease.connection,
                    selected.before,
                    selected.after,
                    selected.before_release,
                    selected.after_release,
                    pool.comparisons,
                )
                if scope != ALL and scope not in comparison.scopes:
                    return error(
                        404, "Zakres nie występuje po żadnej stronie porównania."
                    )
                if group != ALL and not any(
                    key[1] == group
                    for key in comparison.left.keys() | comparison.right.keys()
                ):
                    return error(
                        404, "Grupa nie występuje po żadnej stronie porównania."
                    )
                body = comparison_page(
                    comparison,
                    dataset,
                    scope,
                    group,
                    catalog_versions(lease.connection),
                )
                if len(body.encode()) > 2 * 1024 * 1024:
                    raise QueryLimitError("comparison page exceeds output bound")
            return HTMLResponse(body, headers={"Cache-Control": cache_control})
        except NoRulesetForDate:
            return error(
                404, "Brak zweryfikowanego katalogu dla jednej z wybranych dat."
            )
        except AmbiguousRuleset, DiffInputError:
            return error(409, "Źródła nie pozwalają jednoznacznie porównać tych dat.")
        except CapacityError:
            return error(429, "Serwis jest zajęty. Spróbuj ponownie za chwilę.")
        except QueryTimeoutError:
            return error(504, "Przekroczono limit czasu porównania.")
        except QueryLimitError:
            return error(422, "Porównanie przekracza bezpieczny limit danych.")
        except ArtifactError, KnowledgeBeyondBundle, OSError:
            return error(503, "Nie można odczytać zweryfikowanych danych porównania.")

    app.add_api_route(
        "/jgp/porownaj/{dataset}/{before}/{before_release}/{after}/{after_release}/{scope}/{group}",
        compare,
        methods=["GET"],
    )
