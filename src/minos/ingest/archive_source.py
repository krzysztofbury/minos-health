"""Verified snapshots shared by terminology and komunikat artifact builders."""

import hashlib
import os
import re
import stat
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Self

from minos.domain.reference import SourceError
from minos.ingest.grouping_dictionaries_manifest import LegalActFile
from minos.ingest.icd11_maps_manifest import Icd11MapFile
from minos.ingest.jgp_catalog_manifest import CatalogFile
from minos.ingest.jgp_conditions_manifest import ConditionsFile
from minos.ingest.jgp_modifier_binding_manifest import LegalOverlayFile
from minos.ingest.jgp_modifiers_manifest import DisplayFile, ModifierFile
from minos.ingest.jgp_statistics_manifest import MonthlyReport
from minos.ingest.komunikat_manifest import KomunikatFile
from minos.ingest.product_catalogs_manifest import ProductCatalogFile
from minos.ingest.reference_manifest import SourceManifest

_DIGEST = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class ArchivedBlob:
    """A content-addressed archive object that no manifest pins yet."""

    sha256: str
    size_bytes: int

    @classmethod
    def at(cls, archive_root: Path, sha256: str) -> Self:
        # The size only bounds the snapshot; verified_source still proves the
        # digest before any parser sees the bytes.
        if _DIGEST.fullmatch(sha256) is None:
            raise SourceError("archive object digest is invalid")
        try:
            details = (archive_root / "blobs" / "sha256" / sha256).lstat()
        except OSError as error:
            raise SourceError("archive object is missing") from error
        if not stat.S_ISREG(details.st_mode) or details.st_size < 1:
            raise SourceError("archive object is not a nonempty regular file")
        return cls(sha256, details.st_size)


@contextmanager
def verified_source(
    archive_root: Path,
    source: SourceManifest
    | KomunikatFile
    | CatalogFile
    | ConditionsFile
    | ModifierFile
    | DisplayFile
    | LegalOverlayFile
    | LegalActFile
    | ProductCatalogFile
    | Icd11MapFile
    | MonthlyReport
    | ArchivedBlob,
    *,
    staging_directory: Path,
) -> Generator[BinaryIO]:
    """Snapshot and verify before parsing, preventing hash/parse file rotation."""
    blob_path = archive_root / "blobs" / "sha256" / source.sha256
    try:
        descriptor = os.open(
            blob_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
        )
        with (
            os.fdopen(descriptor, "rb") as archived,
            tempfile.TemporaryFile("w+b", dir=staging_directory) as snapshot,
        ):
            details = os.fstat(archived.fileno())
            if (
                not stat.S_ISREG(details.st_mode)
                or details.st_size != source.size_bytes
            ):
                raise SourceError(
                    "pinned source byte count or file type does not match"
                )
            digest = hashlib.sha256()
            consumed = 0
            while chunk := archived.read(64 * 1024):
                consumed += len(chunk)
                if consumed > source.size_bytes:
                    raise SourceError("pinned source grew during verification")
                digest.update(chunk)
                if snapshot.write(chunk) != len(chunk):
                    raise SourceError("source snapshot write was incomplete")
            if consumed != source.size_bytes or digest.hexdigest() != source.sha256:
                raise SourceError("pinned source checksum or byte count does not match")
            _ = snapshot.seek(0)
            yield snapshot
    except OSError as error:
        raise SourceError("pinned archive source cannot be read or staged") from error
