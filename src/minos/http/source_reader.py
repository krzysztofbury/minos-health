"""Read only bounded public metadata; never open watcher control or raw archives."""

import hashlib
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from pydantic import ValidationError

from minos.http.source_models import BawProjection
from minos.ingest.watchers.nfz.public_models import NfzProjection
from minos.ingest.watchers.rsk.public_models import RskProvenanceProjection

type Source = Literal["rsk", "baw", "nfz"]
IDENTITY = r"[1-9][0-9]{0,18}-[0-9a-f]{64}"
_GENERATION = re.compile(IDENTITY)
_DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_MODELS: dict[
    Source, type[RskProvenanceProjection] | type[BawProjection] | type[NfzProjection]
] = {"rsk": RskProvenanceProjection, "baw": BawProjection, "nfz": NfzProjection}


class SourceUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class PublicSnapshot:
    source: Source
    identity: str
    data: dict[str, object]
    retained: bool


def _file(
    root: Path, parts: list[str], maximum: int, *, missing_record: bool = False
) -> bytes:
    descriptors: list[int] = []
    try:
        descriptor = os.open(root, _DIRECTORY)
        descriptors.append(descriptor)
        owner = os.fstat(descriptor).st_uid
        if os.fstat(descriptor).st_mode & 0o022:
            raise SourceUnavailable("public root is unsafe")
        for part in parts[:-1]:
            descriptor = os.open(part, _DIRECTORY, dir_fd=descriptor)
            descriptors.append(descriptor)
            info = os.fstat(descriptor)
            if info.st_uid != owner or info.st_mode & 0o022:
                raise SourceUnavailable("public directory is unsafe")
        try:
            descriptor = os.open(
                parts[-1],
                os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                dir_fd=descriptor,
            )
        except FileNotFoundError:
            if missing_record:
                raise LookupError("citation record absent") from None
            raise
        descriptors.append(descriptor)
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != owner
            or info.st_mode & 0o022
            or not 1 <= info.st_size <= maximum
        ):
            raise SourceUnavailable("public file is unsafe")
        blocks: list[bytes] = []
        remaining = info.st_size
        while remaining:
            block = os.read(descriptor, min(65536, remaining))
            if not block:
                raise SourceUnavailable("public file is incomplete")
            remaining -= len(block)
            blocks.append(block)
        if os.read(descriptor, 1):
            raise SourceUnavailable("public file grew")
        return b"".join(blocks)
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def load_snapshot(
    root: Path, source: Source, identity: str | None = None
) -> PublicSnapshot:
    directory = root if source == "rsk" else root / source
    historical = identity is not None
    try:
        if identity is None:
            descriptor = os.open(directory, _DIRECTORY)
            try:
                target = os.readlink("current", dir_fd=descriptor)
            finally:
                os.close(descriptor)
            if not target.startswith("generations/"):
                raise SourceUnavailable("public pointer is invalid")
            identity = target.removeprefix("generations/")
            parts = [
                "generations",
                identity,
                "api",
                "v1",
                "provenance",
                source + ".json",
            ]
        else:
            if source == "nfz":
                raise SourceUnavailable("NFZ observation retention is unavailable")
            parts = ["citations", identity + ".json"]
        if _GENERATION.fullmatch(identity) is None:
            raise SourceUnavailable("public observation identity is invalid")
        revision, digest = identity.split("-", 1)
        limit = {"rsk": 8 * 1024 * 1024, "baw": 256 * 1024, "nfz": 1024 * 1024}[source]
        content = _file(directory, parts, limit, missing_record=historical)
        if hashlib.sha256(content).hexdigest() != digest:
            raise SourceUnavailable("public observation digest differs")
        model = _MODELS[source].model_validate_json(content)
        if model.through_state_revision != int(revision):
            raise SourceUnavailable("public observation revision differs")
        retained = historical
        if not historical and source != "nfz":
            try:
                retained = (
                    _file(directory, ["citations", identity + ".json"], limit)
                    == content
                )
            except OSError, SourceUnavailable:
                retained = False
        return PublicSnapshot(
            source,
            identity,
            cast(dict[str, object], model.model_dump(mode="json")),
            retained,
        )
    except OSError, ValueError, ValidationError:
        raise SourceUnavailable("public source is unavailable") from None
