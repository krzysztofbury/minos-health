"""Fixed-size verified connection pool for concurrent, read-only HTTP lookup."""

import sqlite3
from collections.abc import Generator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from threading import Lock
from typing import NamedTuple, final

from minos.domain.morphology import PolishMorphology
from minos.domain.reference import ArtifactError
from minos.store.artifact import DATABASE_NAME, ArtifactManifest
from minos.store.reference import open_reference_bundle


class CapacityError(Exception):
    """The fixed HTTP worker budget is already in use."""


class Lease(NamedTuple):
    connection: sqlite3.Connection
    analyzer: PolishMorphology


@final
class ReferencePool:
    def __init__(self, bundle: Path, *, capacity: int = 4) -> None:
        if not 1 <= capacity <= 4:
            raise ValueError("invalid reference worker capacity")
        self._stack = ExitStack()
        self._lock = Lock()
        self._database = bundle / DATABASE_NAME
        self._leases: list[Lease] = []
        self.manifest: ArtifactManifest
        try:
            for _ in range(capacity):
                connection, manifest = self._stack.enter_context(
                    open_reference_bundle(bundle, check_same_thread=False)
                )
                # Load each verified native dictionary before readiness. Cold
                # loading exceeded the host's request budget; one analyzer per
                # exclusive lease keeps concurrent requests independent.
                self._leases.append(Lease(connection, PolishMorphology()))
                self.manifest = manifest
            self._identity = self._file_identity()
        except BaseException:
            self._stack.close()
            raise

    def _file_identity(self) -> tuple[int, int, int, int]:
        value = self._database.stat(follow_symlinks=False)
        return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns

    @contextmanager
    def borrow(self) -> Generator[Lease]:
        # Checksums are verified at startup. Runtime artifacts must be mounted
        # read-only; identity changes still fail closed before each lookup.
        if self._file_identity() != self._identity:
            raise ArtifactError("reference artifact changed after startup")
        with self._lock:
            if not self._leases:
                raise CapacityError
            lease = self._leases.pop()
        try:
            yield lease
        finally:
            with self._lock:
                self._leases.append(lease)

    def close(self) -> None:
        self._stack.close()
