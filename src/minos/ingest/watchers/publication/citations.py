"""Immutable public metadata records which routine generation GC never removes.

The watcher process lock supplies the single writer. A citation is installed
and fsynced before the current public projection can advertise it. Exhaustion
refuses publication; it never evicts a previously issued citation.
"""

import hashlib
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import final
from uuid import uuid4

from minos.ingest.watchers.publication.safety import (
    PublicPublicationError,
    prepare_public_root,
)

MAX_CITATION_REVISIONS = 1024
MAX_CITATION_BYTES = 256 * 1024 * 1024
MAX_RECORD_BYTES = 8 * 1024 * 1024
_RECORD = re.compile(r"([1-9][0-9]{0,18})-([0-9a-f]{64})\.json")
_TEMPORARY = re.compile(r"\.building-[0-9a-f]{32}")


@dataclass(frozen=True)
class CitationRecord:
    revision: int
    sha256: str
    size_bytes: int

    @property
    def filename(self) -> str:
        return f"{self.revision}-{self.sha256}.json"


@final
class CitationArchive:
    def __init__(self, root: Path) -> None:
        self._root = root

    def records(self) -> tuple[CitationRecord, ...]:
        """Verify the retained evidence during recovery, including content digests."""
        descriptor = self._open()
        try:
            records = self._inventory(descriptor)
            for record in records:
                self._verify(descriptor, record)
            return records
        except OSError:
            raise PublicPublicationError("citation verification failed") from None
        finally:
            os.close(descriptor)

    def publish(self, revision: int, digest: str, content: bytes) -> None:
        record = CitationRecord(revision, digest, len(content))
        if (
            type(revision) is not int
            or not _RECORD.fullmatch(record.filename)
            or not 1 <= len(content) <= MAX_RECORD_BYTES
            or hashlib.sha256(content).hexdigest() != digest
        ):
            raise PublicPublicationError("citation content identity is invalid")
        descriptor = self._open()
        temporary = f".building-{uuid4().hex}"
        try:
            records = self._inventory(descriptor)
            prior = next((item for item in records if item.revision == revision), None)
            if prior is not None:
                if prior != record:
                    raise PublicPublicationError("citation revision already differs")
                self._verify(descriptor, prior)
                # A prior attempt may have stopped between rename and directory
                # fsync. Reestablish durability before publishing a current link.
                os.fsync(descriptor)
                return
            if (
                len(records) >= MAX_CITATION_REVISIONS
                or sum(item.size_bytes for item in records) + len(content)
                > MAX_CITATION_BYTES
            ):
                raise PublicPublicationError("citation retention capacity is exhausted")
            file_descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
                dir_fd=descriptor,
            )
            try:
                offset = 0
                while offset < len(content):
                    written = os.write(file_descriptor, content[offset:])
                    if written <= 0:
                        raise PublicPublicationError("citation write was incomplete")
                    offset += written
                os.fchmod(file_descriptor, 0o444)
                os.fsync(file_descriptor)
            finally:
                os.close(file_descriptor)
            # The exclusive process lock is also required by the existing
            # generation publisher; no second writer can replace this identity.
            os.rename(
                temporary, record.filename, src_dir_fd=descriptor, dst_dir_fd=descriptor
            )
            os.fsync(descriptor)
            self._verify(descriptor, record)
        except OSError:
            raise PublicPublicationError("citation publication failed") from None
        finally:
            try:
                os.unlink(temporary, dir_fd=descriptor)
            except FileNotFoundError:
                pass
            except OSError:
                raise PublicPublicationError(
                    "citation staging cleanup failed"
                ) from None
            finally:
                os.close(descriptor)

    def _open(self) -> int:
        prepare_public_root(self._root)
        descriptor = -1
        try:
            descriptor = os.open(
                self._root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
            )
            info = os.fstat(descriptor)
            if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o755:
                raise PublicPublicationError("citation directory identity differs")
            # A record fsync cannot make a newly created citations/ entry
            # durable in its parent. Repeat this on retries after interruption.
            parent = os.open(
                self._root.parent,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
            )
            try:
                os.fsync(parent)
            finally:
                os.close(parent)
            return descriptor
        except PublicPublicationError:
            if descriptor >= 0:
                os.close(descriptor)
            raise
        except OSError:
            if descriptor >= 0:
                os.close(descriptor)
            raise PublicPublicationError("citation archive is unreadable") from None

    def _inventory(self, descriptor: int) -> tuple[CitationRecord, ...]:
        records: list[CitationRecord] = []
        temporary: list[str] = []
        revisions: set[int] = set()
        size = 0
        try:
            with os.scandir(descriptor) as entries:
                for entry in entries:
                    match = _RECORD.fullmatch(entry.name)
                    if match is None and not _TEMPORARY.fullmatch(entry.name):
                        raise PublicPublicationError("citation filename is invalid")
                    info = entry.stat(follow_symlinks=False)
                    if (
                        not stat.S_ISREG(info.st_mode)
                        or info.st_uid != os.geteuid()
                        or info.st_nlink != 1
                        or info.st_size > MAX_RECORD_BYTES
                        or stat.S_IMODE(info.st_mode)
                        not in ((0o444,) if match else (0o444, 0o600))
                    ):
                        raise PublicPublicationError("citation file is unsafe")
                    if match is None:
                        temporary.append(entry.name)
                        if len(temporary) > 1:
                            raise PublicPublicationError(
                                "citation staging count exceeds its bound"
                            )
                        continue
                    revision = int(match[1])
                    if revision in revisions or info.st_size == 0:
                        raise PublicPublicationError("citation revision is ambiguous")
                    revisions.add(revision)
                    records.append(CitationRecord(revision, match[2], info.st_size))
                    size += info.st_size
                    if (
                        len(records) > MAX_CITATION_REVISIONS
                        or size > MAX_CITATION_BYTES
                    ):
                        raise PublicPublicationError(
                            "citation archive exceeds its bound"
                        )
            # Only this publisher's interrupted staging files are removable.
            # Installed citations are never cleanup candidates.
            for name in temporary:
                os.unlink(name, dir_fd=descriptor)
            if temporary:
                os.fsync(descriptor)
            return tuple(sorted(records, key=lambda item: item.revision))
        except OSError:
            raise PublicPublicationError("citation inventory failed") from None

    def _verify(self, descriptor: int, record: CitationRecord) -> None:
        file_descriptor = os.open(
            record.filename,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=descriptor,
        )
        try:
            info = os.fstat(file_descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or info.st_nlink != 1
                or info.st_size != record.size_bytes
                or stat.S_IMODE(info.st_mode) != 0o444
            ):
                raise PublicPublicationError("citation file identity differs")
            digest = hashlib.sha256()
            remaining = record.size_bytes
            while remaining:
                block = os.read(file_descriptor, min(65536, remaining))
                if not block:
                    raise PublicPublicationError("citation file is truncated")
                digest.update(block)
                remaining -= len(block)
            if os.read(file_descriptor, 1) or digest.hexdigest() != record.sha256:
                raise PublicPublicationError("citation content digest differs")
        finally:
            os.close(file_descriptor)
