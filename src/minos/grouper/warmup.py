"""Bounded background preparation over the same validated, budgeted readers.

Timeouts may reflect transient startup contention. Finish independent entries
before retrying; never retry validation, storage or operation-limit failures.
"""

import hashlib
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from threading import Event
from typing import Literal, final

from minos.domain.grouper_candidates import Catalog
from minos.domain.reference import QueryTimeoutError
from minos.grouper.pipeline import PreparedSources
from minos.store.grouper_bundle import sheet_releases
from minos.store.product_catalogs import product_catalog_releases

# The first read of a sheet can outlast a request's one second on the host while
# activation shares its CPU, and a failed warm-up leaves that read to visitors
# (#310). Requests never wait for it past their own budget.
SHEET_SECONDS = 5.0

type Stage = Literal[
    "sheet_inventory", "product_inventory", "algorithm", "sheet", "products"
]


class WarmupCancelled(Exception):
    """Shutdown was requested between bounded preparation steps."""


@dataclass(frozen=True)
class WarmupStep:
    stage: Stage
    release_sha256: str | None
    catalog: Catalog | None
    attempt: int
    status: Literal["completed", "timeout", "failed"]
    duration_ms: int


@final
class Warmup:
    def __init__(self, stop: Event, report: Callable[[WarmupStep], None]) -> None:
        self.stop = stop
        self.report = report
        self.attempt = 0
        self._completed: set[tuple[Stage, str, Catalog | None]] = set()

    @property
    def sheets(self) -> int:
        return sum(stage == "sheet" for stage, _, _ in self._completed)

    @property
    def products(self) -> int:
        return sum(stage == "products" for stage, _, _ in self._completed)

    def _step[T](
        self,
        stage: Stage,
        release: str,
        catalog: Catalog | None,
        action: Callable[[], T],
    ) -> T:
        if self.stop.is_set():
            raise WarmupCancelled
        started = time.monotonic()
        status: Literal["completed", "timeout", "failed"] = "failed"
        try:
            result = action()
            self._completed.add((stage, release, catalog))
            status = "completed"
            return result
        except QueryTimeoutError:
            status = "timeout"
            raise
        finally:
            self.report(
                WarmupStep(
                    stage,
                    hashlib.sha256(release.encode()).hexdigest() if release else None,
                    catalog,
                    self.attempt,
                    status,
                    round((time.monotonic() - started) * 1000),
                )
            )

    def _pass(self, connection: sqlite3.Connection, prepared: PreparedSources) -> None:
        sheets = self._step(
            "sheet_inventory", "", None, partial(sheet_releases, connection)
        )
        products = self._step(
            "product_inventory", "", None, partial(product_catalog_releases, connection)
        )
        tasks: list[tuple[Stage, str, Catalog | None, Callable[[], object]]] = [
            ("algorithm", "", None, partial(prepared.algorithm, connection))
        ]
        tasks.extend(
            (
                "sheet",
                release,
                catalog,
                partial(
                    prepared.sheet, connection, release, catalog, seconds=SHEET_SECONDS
                ),
            )
            for release, catalog in sheets
        )
        tasks.extend(
            ("products", release, None, partial(prepared.products, connection, release))
            for release in products
        )
        timeout: QueryTimeoutError | None = None
        for stage, release, catalog, action in tasks:
            # Completed caches remain immutable; avoid even reacquiring their
            # lock on retry. Failed entries were never published by the readers.
            if (stage, release, catalog) in self._completed:
                continue
            try:
                _ = self._step(stage, release, catalog, action)
            except QueryTimeoutError as error:
                timeout = error
        if timeout is not None:
            raise timeout

    def run(self, connection: sqlite3.Connection, prepared: PreparedSources) -> None:
        # At most two passes over 64 sheets, 64 product releases and one
        # algorithm. The ordinary per-reader query/row budgets remain unchanged.
        for attempt in (1, 2):
            self.attempt = attempt
            try:
                self._pass(connection, prepared)
            except QueryTimeoutError:
                if attempt == 2:
                    raise
                if self.stop.wait(1):
                    raise WarmupCancelled from None
            else:
                if self.stop.is_set():
                    raise WarmupCancelled
                prepared.warmed.set()
                return
