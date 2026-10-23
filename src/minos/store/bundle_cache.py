"""Release-static lookups of one verified bundle, built on first use (#225)."""

import threading
from collections.abc import Callable, Hashable
from typing import cast, final

from minos.domain.reference import ArtifactError

MAX_ENTRIES = 64


@final
class BundleCache:
    """Values derived only from an immutable, checksum-verified bundle.

    Own exactly one per opened bundle and never share it across bundles:
    entries are keyed by release, not by database bytes. Each owning module
    builds its own entries, so the cache holds no knowledge of their meaning.
    A failed build stores nothing and the next request retries it.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[Hashable, object] = {}

    def get[T](self, key: Hashable, build: Callable[[], T]) -> T:
        with self._lock:
            if key not in self._entries:
                if len(self._entries) >= MAX_ENTRIES:
                    raise ArtifactError("bundle cache exceeds its bound")
                self._entries[key] = build()
            return cast(T, self._entries[key])
