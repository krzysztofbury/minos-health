"""Fixed-size verified connection pool for concurrent, read-only HTTP lookup."""

import sqlite3
from collections.abc import Generator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from threading import Condition
from typing import NamedTuple, final

from minos.domain.morphology import PolishMorphology
from minos.domain.reference import ArtifactError
from minos.store.artifact import DATABASE_NAME, ArtifactManifest
from minos.store.jgp_search import GroupIndex, build_group_index
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
        self._lock = Condition()
        self._waiting = 0
        self._closed = False
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
            self.group_index: GroupIndex = {}
            if self.manifest.dataset.catalogs:
                self.group_index = build_group_index(
                    self._leases[0].connection, self._leases[0].analyzer
                )
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
                if self._waiting >= 4:
                    raise CapacityError
                self._waiting += 1
                try:
                    if not self._lock.wait_for(
                        lambda: bool(self._leases) or self._closed, timeout=0.5
                    ):
                        raise CapacityError
                finally:
                    self._waiting -= 1
            if self._closed:
                raise CapacityError
            lease = self._leases.pop()
        try:
            yield lease
        finally:
            with self._lock:
                self._leases.append(lease)
                self._lock.notify()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._lock.notify_all()
        self._stack.close()
