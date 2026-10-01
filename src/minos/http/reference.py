"""Fixed-size verified connection pool for concurrent, read-only HTTP lookup."""

import sqlite3
from collections.abc import Generator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from threading import Lock
from typing import final

from minos.domain.morphology import PolishMorphology
from minos.domain.reference import ArtifactError
from minos.store.artifact import DATABASE_NAME, ArtifactManifest
from minos.store.reference import open_reference_bundle


class CapacityError(Exception):
    """The fixed HTTP worker budget is already in use."""


@final
class ReferencePool:
    def __init__(self, bundle: Path, *, capacity: int = 4) -> None:
        if not 1 <= capacity <= 4:
            raise ValueError("invalid reference worker capacity")
        self._stack = ExitStack()
        self._lock = Lock()
        self._database = bundle / DATABASE_NAME
        self._connections: list[sqlite3.Connection] = []
        self.manifest: ArtifactManifest
        try:
            for _ in range(capacity):
                connection, manifest = self._stack.enter_context(
                    open_reference_bundle(bundle, check_same_thread=False)
                )
                self._connections.append(connection)
                self.manifest = manifest
            self._identity = self._file_identity()
            # Retain the verified native dictionary before readiness. Cold loading
            # exceeded the host's request budget; request analyzers stay independent.
            self._morphology = PolishMorphology()
        except BaseException:
            self._stack.close()
            raise

    def _file_identity(self) -> tuple[int, int, int, int]:
        value = self._database.stat(follow_symlinks=False)
        return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns

    @contextmanager
    def borrow(self) -> Generator[sqlite3.Connection]:
        # Checksums are verified at startup. Runtime artifacts must be mounted
        # read-only; identity changes still fail closed before each lookup.
        if self._file_identity() != self._identity:
            raise ArtifactError("reference artifact changed after startup")
        with self._lock:
            if not self._connections:
                raise CapacityError
            connection = self._connections.pop()
        try:
            yield connection
        finally:
            with self._lock:
                self._connections.append(connection)

    def close(self) -> None:
        self._stack.close()
